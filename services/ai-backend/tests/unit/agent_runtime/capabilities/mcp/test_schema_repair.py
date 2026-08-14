"""Unit tests for vendor MCP input-schema repair.

The defect these guard against is an *absence*, not an error. Both rejections
in ``McpSchemaValidator`` fired inside a Pydantic field validator, and
``McpLoaderHelpers.parse_tools`` turns any ``ValidationError`` into
``MALFORMED_DESCRIPTOR`` for the **whole server** — so one Jira ``createIssue``
payload with an omitted ``type`` deleted the entire connector from the model's
surface and the agent politely said it could not do the thing. Nothing in the
logs, nothing in the UI.

So there is one test per repair rule, each built from the shape a real tracker
publishes rather than a minimal synthetic, plus the two tests that matter most:
that the schema still *fits its contract* after being degraded past the byte
ceiling, and that every repair leaves a greppable line behind.
"""

from __future__ import annotations

import json
import logging

import pytest

from agent_runtime.capabilities.mcp.cards import McpSchemaValidator
from agent_runtime.capabilities.mcp.constants import Keys, Limits, Values
from agent_runtime.capabilities.mcp.schema_repair import (
    McpSchemaRepair,
    McpSchemaRepairLog,
    McpSchemaRepairRule,
)


class VendorSchemaMixin:
    """Realistic vendor schema shapes, and the assertions they share."""

    class Names:
        CREATE_ISSUE = "createIssue"
        FIELD = "input_schema"
        LINEAR = "linear"

    def repair(
        self, schema: object, *, tool_name: str = "", max_bytes: int | None = None
    ):
        """Run one repair pass and return ``(schema, report)``."""

        return McpSchemaRepair.repair(
            schema,
            field_name=self.Names.FIELD,
            tool_name=tool_name or self.Names.CREATE_ISSUE,
            **({} if max_bytes is None else {"max_bytes": max_bytes}),
        )

    def jira_create_issue(self) -> dict[str, object]:
        """An Atlassian-shaped ``createIssue`` payload carrying four defects.

        Deeply typed on purpose: the nested ``IssueFields`` definition is where
        a per-node walker that only repairs the top level quietly does nothing.
        """

        return {
            Keys.Schema.PROPERTIES: {
                "project": {Keys.Schema.TYPE: Values.SchemaType.STRING},
                "summary": {Keys.Schema.TYPE: Values.SchemaType.STRING},
                "assignee": {
                    "anyOf": [
                        {Keys.Schema.TYPE: Values.SchemaType.STRING},
                        {Keys.Schema.TYPE: Values.SchemaType.NULL},
                    ],
                    "description": "Account id to assign the issue to.",
                },
                "fields": {"$ref": "#/definitions/IssueFields"},
            },
            Keys.Schema.REQUIRED: ["project", "summary", "reporter"],
            "definitions": {
                "IssueFields": {
                    Keys.Schema.PROPERTIES: {
                        "labels": {
                            "items": {Keys.Schema.TYPE: Values.SchemaType.STRING}
                        }
                    },
                    Keys.Schema.REQUIRED: ["labels", "epic"],
                }
            },
        }

    def linear_create_comment(self) -> dict[str, object]:
        """A Linear-shaped payload whose optional fields use the null union."""

        return {
            Keys.Schema.TYPE: Values.SchemaType.OBJECT,
            Keys.Schema.PROPERTIES: {
                "issueId": {Keys.Schema.TYPE: Values.SchemaType.STRING},
                "parentId": {
                    "anyOf": [
                        {Keys.Schema.TYPE: Values.SchemaType.STRING},
                        {Keys.Schema.TYPE: Values.SchemaType.NULL},
                    ],
                    "default": None,
                    "description": "Parent comment to thread under.",
                },
            },
            Keys.Schema.REQUIRED: ["issueId"],
        }

    def oversized_schema(self, *, properties: int = 40) -> dict[str, object]:
        """A schema over the 16 KB ceiling purely from documentation weight.

        ``properties`` selects *which degradation stage* the schema lands on,
        so the callers below pass it explicitly rather than take the default.
        With this prose length the arithmetic is:

        ==========  ========  ============  ===========  =========
        properties  raw       -examples     -defaults    -truncate
        ==========  ========  ============  ===========  =========
        10           26,468        13,528       (fits)      (fits)
        40          105,788        54,028       27,988     12,268
        200         529,028       270,228      140,028     61,428
        ==========  ========  ============  ===========  =========

        So 10 fits after stage 1, 40 needs all three, and 200 cannot fit at
        all — it exercises the refusal, not the truncation. Sizing these by
        eye is what makes a degradation test assert something impossible.
        """

        prose = "Prose that documents the field at exhausting length. " * 12
        return {
            Keys.Schema.TYPE: Values.SchemaType.OBJECT,
            Keys.Schema.PROPERTIES: {
                f"field_{index}": {
                    Keys.Schema.TYPE: Values.SchemaType.STRING,
                    "description": prose,
                    "default": prose,
                    "examples": [prose, prose],
                }
                for index in range(properties)
            },
            Keys.Schema.REQUIRED: [f"field_{index}" for index in range(properties)],
        }

    def encoded_size(self, schema: object) -> int:
        """Return the UTF-8 byte length of ``schema`` encoded as JSON."""

        return len(json.dumps(schema, sort_keys=True).encode("utf-8"))


class TestMcpSchemaRepairRules(VendorSchemaMixin):
    """One test per rule in :class:`McpSchemaRepairRule`."""

    def test_coerces_missing_top_level_type_to_object(self) -> None:
        """``properties`` with no ``type`` is an object, not a malformed schema."""

        repaired, report = self.repair(self.jira_create_issue())

        assert repaired[Keys.Schema.TYPE] == Values.SchemaType.OBJECT
        assert McpSchemaRepairRule.TYPE_COERCED in report.rules

    def test_infers_type_from_implied_keywords_when_nested(self) -> None:
        """A nested node with only ``items`` is an array; only ``enum`` is a string.

        Inference, not the top-level object fallback: these nodes are reached
        by the walker, so a repair that fired only at the root would miss them.
        """

        repaired, _ = self.repair(
            {
                Keys.Schema.PROPERTIES: {
                    "labels": {"items": {Keys.Schema.TYPE: Values.SchemaType.STRING}},
                    "priority": {"enum": ["low", "high"]},
                    "points": {"minimum": 1, "maximum": 8},
                }
            }
        )

        properties = repaired[Keys.Schema.PROPERTIES]
        assert properties["labels"][Keys.Schema.TYPE] == Values.SchemaType.ARRAY
        assert properties["priority"][Keys.Schema.TYPE] == Values.SchemaType.STRING
        assert properties["points"][Keys.Schema.TYPE] == Values.SchemaType.NUMBER

    def test_leaves_composition_and_ref_nodes_untyped(self) -> None:
        """``$ref`` / ``allOf`` nodes legitimately carry no ``type``.

        Inventing ``object`` here would contradict the branch they compose.
        """

        repaired, _ = self.repair(
            {
                Keys.Schema.TYPE: Values.SchemaType.OBJECT,
                Keys.Schema.PROPERTIES: {
                    "ref": {"$ref": "#/$defs/Thing"},
                    "composed": {
                        "allOf": [{Keys.Schema.TYPE: Values.SchemaType.STRING}]
                    },
                },
                "$defs": {"Thing": {Keys.Schema.TYPE: Values.SchemaType.STRING}},
            }
        )

        properties = repaired[Keys.Schema.PROPERTIES]
        assert Keys.Schema.TYPE not in properties["ref"]
        assert Keys.Schema.TYPE not in properties["composed"]

    def test_renames_definitions_and_repoints_refs(self) -> None:
        """draft-07 ``definitions`` becomes ``$defs``, and pointers follow it.

        Renaming without repointing is worse than not renaming: it produces a
        schema whose ``$ref`` resolves to nothing.
        """

        repaired, report = self.repair(self.jira_create_issue())

        assert "$defs" in repaired
        assert "definitions" not in repaired
        assert repaired["$defs"]["IssueFields"][Keys.Schema.TYPE] == (
            Values.SchemaType.OBJECT
        )
        assert (
            repaired[Keys.Schema.PROPERTIES]["fields"]["$ref"] == "#/$defs/IssueFields"
        )
        assert McpSchemaRepairRule.DEFS_RENAMED in report.rules

    def test_preserves_definitions_used_as_a_property_name(self) -> None:
        """A tool argument *named* ``definitions`` must survive verbatim.

        Anthropic and OpenAI both reject ``$`` in a property name, so
        rewriting this one to ``$defs`` would 400 the whole tool array — the
        exact regression Hermes gates ``_rewrite_local_refs`` against.
        """

        repaired, _ = self.repair(
            {
                Keys.Schema.TYPE: Values.SchemaType.OBJECT,
                Keys.Schema.PROPERTIES: {
                    "definitions": {Keys.Schema.TYPE: Values.SchemaType.ARRAY}
                },
            }
        )

        assert "definitions" in repaired[Keys.Schema.PROPERTIES]
        assert "$defs" not in repaired[Keys.Schema.PROPERTIES]

    def test_collapses_the_pydantic_nullable_union(self) -> None:
        """``anyOf: [{...}, {"type": "null"}]`` becomes one nullable type.

        The parent-authored ``description`` and ``default`` must survive the
        collapse — they document the field, and they live on the property node
        rather than on the surviving branch.
        """

        repaired, report = self.repair(self.linear_create_comment())

        parent = repaired[Keys.Schema.PROPERTIES]["parentId"]
        assert parent[Keys.Schema.TYPE] == [
            Values.SchemaType.STRING,
            Values.SchemaType.NULL,
        ]
        assert "anyOf" not in parent
        assert parent["description"] == "Parent comment to thread under."
        assert parent["default"] is None
        assert McpSchemaRepairRule.NULLABLE_UNION_COLLAPSED in report.rules

    def test_leaves_a_genuine_multi_branch_union_alone(self) -> None:
        """A real either/or union is a contract, not the optional-field idiom."""

        schema = {
            Keys.Schema.TYPE: Values.SchemaType.OBJECT,
            Keys.Schema.PROPERTIES: {
                "target": {
                    "anyOf": [
                        {Keys.Schema.TYPE: Values.SchemaType.STRING},
                        {Keys.Schema.TYPE: Values.SchemaType.NUMBER},
                    ]
                }
            },
        }

        repaired, report = self.repair(schema)

        assert len(repaired[Keys.Schema.PROPERTIES]["target"]["anyOf"]) == 2
        assert McpSchemaRepairRule.NULLABLE_UNION_COLLAPSED not in report.rules

    def test_prunes_required_names_no_property_defines(self) -> None:
        """Gemini 400s on a ``required`` name with no matching property.

        Pruned at every depth: ``epic`` sits inside the nested definition, and
        the surviving names must keep their order and their contract.
        """

        repaired, report = self.repair(self.jira_create_issue())

        assert repaired[Keys.Schema.REQUIRED] == ["project", "summary"]
        assert repaired["$defs"]["IssueFields"][Keys.Schema.REQUIRED] == ["labels"]
        assert McpSchemaRepairRule.REQUIRED_PRUNED in report.rules

    def test_keeps_required_when_properties_come_from_a_ref(self) -> None:
        """A node composing its properties elsewhere must not be stripped.

        Pruning against an absent ``properties`` map would delete a real
        contract here rather than repair a broken one.
        """

        repaired, report = self.repair(
            {
                Keys.Schema.TYPE: Values.SchemaType.OBJECT,
                "allOf": [{"$ref": "#/$defs/Base"}],
                Keys.Schema.REQUIRED: ["project"],
                "$defs": {"Base": {Keys.Schema.TYPE: Values.SchemaType.OBJECT}},
            }
        )

        assert repaired[Keys.Schema.REQUIRED] == ["project"]
        assert McpSchemaRepairRule.REQUIRED_PRUNED not in report.rules

    def test_drops_the_json_schema_boolean_form(self) -> None:
        """``true`` in a schema position is legal JSON Schema, rejected by providers.

        ``additionalProperties`` is the documented exception: the boolean form
        is canonical there, and rewriting it would change what the schema means.
        """

        repaired, report = self.repair(
            {
                Keys.Schema.TYPE: Values.SchemaType.OBJECT,
                Keys.Schema.PROPERTIES: {"anything": True},
                "additionalProperties": False,
            }
        )

        assert repaired[Keys.Schema.PROPERTIES]["anything"] == {
            Keys.Schema.TYPE: Values.SchemaType.STRING
        }
        assert repaired["additionalProperties"] is False
        assert McpSchemaRepairRule.BOOLEAN_FORM_DROPPED in report.rules

    def test_leaves_an_already_clean_schema_byte_identical(self) -> None:
        """No rule fires on a well-formed schema, and nothing is rewritten.

        The repair is a rescue path; a connector that ships correct schemas
        must not have them quietly re-spelled underneath it.
        """

        schema = {
            Keys.Schema.TYPE: Values.SchemaType.OBJECT,
            Keys.Schema.PROPERTIES: {
                "query": {Keys.Schema.TYPE: Values.SchemaType.STRING}
            },
            Keys.Schema.REQUIRED: ["query"],
        }

        repaired, report = self.repair(schema)

        assert repaired == schema
        assert report.rules == ()
        assert report.repaired is False


class TestMcpSchemaRepairDegradation(VendorSchemaMixin):
    """The 16 KB ceiling degrades before it refuses."""

    def test_degrades_documentation_to_fit_and_keeps_the_contract(self) -> None:
        """Over the ceiling, prose is shed — every property and name survives.

        This is the whole justification for degrading rather than rejecting:
        the call stays constructible. The untruncated schema also remains
        readable through the descriptor filesystem (``catalog.py``).
        """

        schema = self.oversized_schema(properties=40)
        assert self.encoded_size(schema) > Limits.MCP_SCHEMA_MAX_BYTES

        repaired, report = self.repair(schema)

        assert self.encoded_size(repaired) <= Limits.MCP_SCHEMA_MAX_BYTES
        assert set(repaired[Keys.Schema.PROPERTIES]) == set(
            schema[Keys.Schema.PROPERTIES]
        )
        assert repaired[Keys.Schema.REQUIRED] == schema[Keys.Schema.REQUIRED]
        assert McpSchemaRepairRule.EXAMPLES_STRIPPED in report.rules
        assert report.repaired_bytes < report.original_bytes

    def test_stops_at_the_first_stage_that_fits(self) -> None:
        """A schema barely over the line keeps its descriptions.

        Ordered degradation is only worth having if it actually stops early.
        Ten properties clear the ceiling once ``examples`` are gone, so
        ``default`` and the prose both survive untouched.
        """

        schema = self.oversized_schema(properties=10)
        repaired, report = self.repair(schema)

        assert McpSchemaRepairRule.EXAMPLES_STRIPPED in report.rules
        # The two later stages never ran, which is the whole claim.
        assert McpSchemaRepairRule.DEFAULTS_STRIPPED not in report.rules
        assert McpSchemaRepairRule.DESCRIPTIONS_TRUNCATED not in report.rules
        first_property = next(iter(repaired[Keys.Schema.PROPERTIES].values()))
        assert "examples" not in first_property
        assert "default" in first_property
        assert len(first_property["description"]) > (
            Limits.SCHEMA_DESCRIPTION_MAX_LENGTH
        )

    def test_truncates_descriptions_only_as_the_last_resort(self) -> None:
        """With ``examples`` and ``default`` already shed, prose is clipped.

        Forty properties still exceed the ceiling after both cheap stages, so
        this is the one shape that reaches truncation and still fits.
        """

        repaired, report = self.repair(self.oversized_schema(properties=40))

        assert McpSchemaRepairRule.EXAMPLES_STRIPPED in report.rules
        assert McpSchemaRepairRule.DEFAULTS_STRIPPED in report.rules
        assert McpSchemaRepairRule.DESCRIPTIONS_TRUNCATED in report.rules
        first_property = next(iter(repaired[Keys.Schema.PROPERTIES].values()))
        assert len(first_property["description"]) <= (
            Limits.SCHEMA_DESCRIPTION_MAX_LENGTH + 3
        )

    def test_refuses_only_when_still_oversized_after_degrading(self) -> None:
        """Contract itself over the ceiling is the one size failure left.

        No documentation to shed, so the refusal is honest — and it must be
        greppable, because this is the case a human has to go look at.
        """

        schema = {
            Keys.Schema.TYPE: Values.SchemaType.OBJECT,
            Keys.Schema.PROPERTIES: {
                f"field_{index}_with_a_long_name": {
                    Keys.Schema.TYPE: Values.SchemaType.STRING
                }
                for index in range(600)
            },
        }

        with pytest.raises(ValueError):
            self.repair(schema)


class TestMcpSchemaRepairObservability(VendorSchemaMixin):
    """Every repair leaves one structured, greppable line."""

    def test_applied_repair_logs_the_server_tool_and_rules(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A silent repair is how a vendor bug becomes our mystery.

        The line has to name the connector, or the next reader knows only that
        "some schema somewhere" was rewritten and cannot file the bug.
        """

        with caplog.at_level(logging.WARNING):
            with McpSchemaRepairLog.for_server(self.Names.LINEAR):
                McpSchemaValidator.validate_json_schema(
                    self.jira_create_issue(),
                    self.Names.FIELD,
                    tool_name=self.Names.CREATE_ISSUE,
                )

        (record,) = [r for r in caplog.records if "mcp_schema_repair" in r.getMessage()]
        message = record.getMessage()
        assert f"server={self.Names.LINEAR}" in message
        assert f"tool={self.Names.CREATE_ISSUE}" in message
        assert McpSchemaRepairRule.TYPE_COERCED.value in message
        assert McpSchemaRepairRule.DEFS_RENAMED.value in message
        assert McpSchemaRepairRule.REQUIRED_PRUNED in message

    def test_clean_schema_logs_nothing(self, caplog: pytest.LogCaptureFixture) -> None:
        """The log is evidence of a repair, so it must stay quiet otherwise."""

        with caplog.at_level(logging.WARNING):
            McpSchemaValidator.validate_json_schema(
                {
                    Keys.Schema.TYPE: Values.SchemaType.OBJECT,
                    Keys.Schema.PROPERTIES: {},
                },
                self.Names.FIELD,
            )

        assert not [r for r in caplog.records if "mcp_schema_repair" in r.getMessage()]

    def test_logs_nothing_a_connector_authored(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Descriptions and property names are connector-authored: never logged."""

        with caplog.at_level(logging.WARNING):
            with McpSchemaRepairLog.for_server(self.Names.LINEAR):
                McpSchemaValidator.validate_json_schema(
                    self.linear_create_comment(),
                    self.Names.FIELD,
                    tool_name=self.Names.CREATE_ISSUE,
                )

        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "Parent comment to thread under." not in joined
        assert "issueId" not in joined

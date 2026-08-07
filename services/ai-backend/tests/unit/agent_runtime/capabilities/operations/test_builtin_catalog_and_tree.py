from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.operations.builtin_catalog import (
    BuiltinOperationCatalog,
    BuiltinOperationCatalogEntry,
    BuiltinOperationCatalogError,
    BuiltinOperationExecution,
    BuiltinOperationKind,
    DEFAULT_BUILTIN_OPERATION_CATALOG,
)
from agent_runtime.capabilities.operations.catalog import DEFAULT_OPERATION_DESCRIPTORS
from agent_runtime.persistence.records import (
    UsageAttributionEdge,
    UsageAttributionRelationship,
)
from agent_runtime.surfaces_v2.ledger_ids import (
    ArtifactIdCodec,
    EffectStageIdCodec,
    OperationIdCodec,
)
from agent_runtime.surfaces_v2.ledger_models import EffectClass


class TestBuiltinOperationCatalog:
    def test_checked_in_inventory_has_expected_model_visible_tools(self) -> None:
        entries = DEFAULT_BUILTIN_OPERATION_CATALOG.model_visible_entries()

        assert {entry.tool_name for entry in entries} == {
            "ask_a_question",
            "auth_mcp",
            "delete",
            "edit",
            "execute",
            "glob",
            "grep",
            "ls",
            "list_connected_servers",
            "load_mcp_server",
            "load_prior_tool_result",
            "load_skill",
            "publish_artifact",
            "read",
            "run_code_mode",
            "run_in_sandbox",
            "stage_rowset_write",
            "suggest_mcp_connector",
            "task",
            "web_search",
            "write",
            "write_todos",
        }
        publish = DEFAULT_BUILTIN_OPERATION_CATALOG.resolve_tool_name(
            "publish_artifact"
        )
        assert publish is not None
        assert publish.key == ("artifact", "publish")
        assert DEFAULT_OPERATION_DESCRIPTORS.resolve_entry(*publish.key) is not None
        loader = DEFAULT_BUILTIN_OPERATION_CATALOG.resolve_tool_name("load_tool_spec")
        assert loader is not None
        assert DEFAULT_OPERATION_DESCRIPTORS.resolve_entry(*loader.key) is not None
        assert all(
            DEFAULT_OPERATION_DESCRIPTORS.resolve_entry(*entry.key) is not None
            for entry in entries
        )

    def test_dynamic_or_unknown_tool_uses_existing_safe_default(self) -> None:
        for tool_name in ("dynamic_tool", "unregistered_tool"):
            descriptor = DEFAULT_BUILTIN_OPERATION_CATALOG.descriptor_or_safe_default(
                tool_name=tool_name,
                descriptors=DEFAULT_OPERATION_DESCRIPTORS,
            )

            assert descriptor.descriptor.effect_class is EffectClass.UNKNOWN
            assert descriptor.unknown_arguments_tighten_to_unknown is True

    def test_dynamic_tool_cannot_be_declared_model_visible(self) -> None:
        with pytest.raises(ValidationError, match="loaded descriptors"):
            BuiltinOperationCatalogEntry.model_validate(
                {
                    "tool_name": "unsafe_dynamic_tool",
                    "capability": "dynamic-tool",
                    "op": "invoke",
                    "source": "test",
                    "kind": BuiltinOperationKind.DYNAMIC_TOOL,
                    "execution": BuiltinOperationExecution.GATEWAY,
                    "model_visible": True,
                }
            )

    def test_framework_aliases_resolve_to_the_reviewed_operation(self) -> None:
        for name, key in (
            ("read_file", ("workspace", "read")),
            ("write_file", ("workspace", "write")),
            ("edit_file", ("workspace", "edit")),
        ):
            entry = DEFAULT_BUILTIN_OPERATION_CATALOG.resolve_model_tool_name(name)

            assert entry is not None
            assert entry.key == key

    def test_duplicate_framework_alias_fails_closed(self) -> None:
        first = BuiltinOperationCatalogEntry(
            tool_name="one",
            model_tool_aliases=("framework_tool",),
            capability="builtin",
            op="one",
            source="test",
            kind=BuiltinOperationKind.BUILTIN,
            execution=BuiltinOperationExecution.PURE,
            model_visible=True,
        )
        second = first.model_copy(update={"tool_name": "two", "op": "two"})

        with pytest.raises(BuiltinOperationCatalogError, match="model-visible"):
            BuiltinOperationCatalog((first, second))

    def test_duplicate_catalog_identity_fails_closed(self) -> None:
        entry = BuiltinOperationCatalogEntry(
            tool_name="one",
            capability="builtin",
            op="one",
            source="test",
            kind=BuiltinOperationKind.BUILTIN,
            execution=BuiltinOperationExecution.PURE,
            model_visible=True,
        )

        with pytest.raises(BuiltinOperationCatalogError, match="duplicate builtin"):
            BuiltinOperationCatalog(
                (entry, entry.model_copy(update={"tool_name": "two"}))
            )


class TestUsageAttributionEdges:
    @pytest.mark.parametrize(
        ("relationship", "artifact_id", "stage_id", "error"),
        [
            (UsageAttributionRelationship.PRODUCED, None, None, "requires artifact_id"),
            (UsageAttributionRelationship.REVISED, None, None, "requires artifact_id"),
            (UsageAttributionRelationship.SHAPED, None, None, "requires artifact_id"),
            (UsageAttributionRelationship.PROPOSED, None, None, "requires stage_id"),
        ],
    )
    def test_edges_require_a_real_immutable_target(
        self,
        relationship: UsageAttributionRelationship,
        artifact_id: str | None,
        stage_id: str | None,
        error: str,
    ) -> None:
        with pytest.raises(ValidationError, match=error):
            UsageAttributionEdge(
                usage_record_id="usage-1",
                operation_id=_operation_id(),
                artifact_id=artifact_id,
                stage_id=stage_id,
                relationship=relationship,
            )


def _operation_id() -> str:
    return OperationIdCodec.format(uuid4())


def _artifact_id() -> str:
    return ArtifactIdCodec.format(uuid4())


def _stage_id() -> str:
    return EffectStageIdCodec.format(uuid4())

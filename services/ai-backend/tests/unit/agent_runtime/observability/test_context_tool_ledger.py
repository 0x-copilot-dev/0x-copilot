"""Unit tests for the model-visible tool-block ledger (PRD-03).

Three contracts are under test, and the order matters because they have
different consequences when they break:

1. **The digest did not move.** ``tool_schema_revision`` is bound into
   prompt-cache identity, so a payload-shape change is not an observability
   regression — it silently re-keys every existing deployment's prompt cache.
   :class:`LegacyToolSchemaRevisionMixin` holds the pre-ledger serialization
   verbatim as an independent witness, and the payload document is additionally
   pinned to a literal digest so a change to key names, wrapper keys, or sort
   order fails here by value rather than by accident.
2. **Measurement never raises.** ``measure`` runs on the model-call path where
   occupancy is best-effort (design §6.4). A tool whose schema explodes, a
   counter that throws, a counter that lies, and a tool whose name cannot be
   read all have to degrade to a recorded row.
3. **Labels come from declarations, not from a central table.** A declared tool
   reports its owner-namespaced label; an undeclared one reports ``UNDECLARED``
   and ``declared=False`` so it lands in ``undeclared_tokens`` where the design
   says a broken declaration contract belongs (§4.4).

``revision`` is the one operation here that deliberately does *not* fail open,
and that asymmetry is asserted directly: a digest that quietly degrades is worse
than one that refuses.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from pydantic import BaseModel, Field, ValidationError

from agent_runtime.execution.factory import _model_tool_schema_revision
from agent_runtime.observability.context_origin import (
    UNDECLARED_CONTEXT_LABEL,
    ContextLifecycle,
    ContextOrigin,
    ContextSegmentClass,
    declare_context_origin,
)
from agent_runtime.observability.context_tool_ledger import (
    HeuristicToolSchemaTokenCounter,
    ToolSchemaFootprint,
    ToolSchemaLedger,
)
from agent_runtime.surfaces_v2.canonical_json import (
    canonical_json_bytes,
    canonical_json_sha256,
)


class PublishArtifactArgs(BaseModel):
    """A representative typed tool schema, shaped like a real model tool's."""

    title: str = Field(description="Human-readable artifact title.")
    body: str = Field(default="", description="Artifact body in markdown.")


class SearchArgs(BaseModel):
    """A second schema, so schema-sensitivity is provable across two tools."""

    query: str
    limit: int = 10


class FakeTool:
    """Exactly the three body-free attributes the provider is shown.

    Not a ``StructuredTool``: the ledger reads ``name`` / ``description`` /
    ``args_schema`` by duck typing precisely so it can measure whatever the
    composition path produced, and a test double that only carries those three
    attributes is what proves it does not secretly depend on a framework type.
    """

    def __init__(
        self,
        name: str,
        description: str,
        args_schema: object | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.args_schema = args_schema


class ExplodingSchema:
    """An ``args_schema`` whose JSON-schema generation blows up.

    Stands in for the real failure this guards: a Pydantic model with an
    unresolvable forward reference or an un-serializable default. It is the one
    input where ``revision`` and ``measure`` must behave differently.
    """

    @staticmethod
    def model_json_schema() -> dict[str, object]:
        raise RuntimeError("schema generation exploded")


class UnreadableNameTool:
    """A tool whose ``name`` raises when read.

    ``getattr(tool, "name", default)`` only swallows ``AttributeError``, so a
    property raising anything else reaches the ledger — which is the point: the
    composed surface contains registry-supplied objects this runtime does not
    author.
    """

    description = "a tool whose identity cannot be read"
    args_schema = None

    @property
    def name(self) -> str:
        raise RuntimeError("name is unreadable")


class WrappingTool:
    """Stands in for the budget / policy / citation wrappers a tool passes through.

    The measurement site sees only the outermost object, so a declaration made
    at composition time has to remain readable through ``inner``.
    """

    def __init__(self, inner: FakeTool) -> None:
        self.inner = inner
        self.name = inner.name
        self.description = inner.description
        self.args_schema = inner.args_schema


class RecordingCounter:
    """A token counter whose answer the test dictates, recording every call."""

    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.texts: list[str] = []

    def __call__(self, text: str) -> int:
        self.texts.append(text)
        return self.answer  # type: ignore[return-value]


class ExplodingCounter:
    """A counter that raises, standing in for a broken tokenizer."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, text: str) -> int:
        del text
        self.calls += 1
        raise RuntimeError("tokenizer exploded")


class LegacyToolSchemaRevisionMixin:
    """The pre-ledger serialization, copied verbatim from ``factory.py``.

    Pinned here rather than imported because the production copy no longer
    exists — ``_model_tool_schema_revision`` now delegates to the ledger, so
    asserting against it would be circular. This copy is the independent witness
    that the refactor did not move a byte of the prompt-cache digest. If a future
    change to :meth:`ToolSchemaLedger.revision` is genuinely intended, this
    method has to be updated in the same commit, which is exactly the
    deliberate-migration moment the digest deserves.
    """

    @staticmethod
    def legacy_revision(model_tools: Sequence[object]) -> str:
        schemas: list[dict[str, object]] = []
        for tool in model_tools:
            args_schema = getattr(tool, "args_schema", None)
            schema: object = None
            model_json_schema = getattr(args_schema, "model_json_schema", None)
            if callable(model_json_schema):
                schema = model_json_schema()
            schemas.append(
                {
                    "name": str(getattr(tool, "name", "")),
                    "description": str(getattr(tool, "description", "")),
                    "args_schema": schema,
                }
            )
        return canonical_json_sha256(
            {
                "schema_revision": "model-visible-tools-v1",
                "tools": sorted(schemas, key=lambda item: str(item["name"])),
            }
        )


class ToolSurfaceMixin:
    """Tool surfaces and declaration helpers shared by the ledger tests."""

    BACKENDS_OWNER = "agent_runtime.capabilities.backends"
    MCP_OWNER = "agent_runtime.capabilities.mcp"
    THIRD_PARTY_OWNER = "deepagents.middleware"

    def sample_surface(self) -> tuple[object, ...]:
        """A surface with a typed schema, a schema-less tool, and unicode text.

        Deliberately not in name order: the digest sorts by name and the report
        does not, and only an unsorted input can tell those two apart.
        """

        return (
            FakeTool(
                "publish_artifact",
                "Publish a durable artifact to the workspace.",
                PublishArtifactArgs,
            ),
            FakeTool("ask_a_question", "Ask the user a bounded question."),
            FakeTool("web_search", "Search the web — résumé friendly.", SearchArgs),
        )

    def declared_tool(
        self,
        tool: object,
        *,
        owner: str,
        name: str,
        lifecycle: ContextLifecycle = ContextLifecycle.RESIDENT,
        third_party: bool = False,
    ) -> object:
        """Stamp a tool-block declaration and hand the tool back for composing."""

        return declare_context_origin(
            tool,
            ContextOrigin(
                owner=owner,
                name=name,
                segment_class=ContextSegmentClass.TOOLS,
                lifecycle=lifecycle,
                third_party=third_party,
            ),
        )

    def footprints_by_name(
        self,
        model_tools: Sequence[object],
        **kwargs: object,
    ) -> dict[str, ToolSchemaFootprint]:
        measured = ToolSchemaLedger.measure(model_tools, **kwargs)  # type: ignore[arg-type]
        return {footprint.tool_name: footprint for footprint in measured}

    def entry_bytes(self, name: str, description: str) -> int:
        """The canonical byte size of one schema-less tool, built independently."""

        return len(
            canonical_json_bytes(
                {
                    "args_schema": None,
                    "description": description,
                    "name": name,
                }
            )
        )


class TestToolSchemaRevisionIdentity(LegacyToolSchemaRevisionMixin, ToolSurfaceMixin):
    """The digest is load-bearing for prompt-cache identity; it must not move."""

    def test_matches_the_pre_ledger_serialization(self) -> None:
        surface = self.sample_surface()

        assert ToolSchemaLedger.revision(surface) == self.legacy_revision(surface)

    def test_matches_the_pre_ledger_serialization_for_an_empty_surface(self) -> None:
        assert ToolSchemaLedger.revision(()) == self.legacy_revision(())

    def test_digests_the_hand_built_canonical_document(self) -> None:
        tools = (FakeTool("b_tool", "second"), FakeTool("a_tool", "first"))

        assert ToolSchemaLedger.revision(tools) == canonical_json_sha256(
            {
                "schema_revision": "model-visible-tools-v1",
                "tools": [
                    {"args_schema": None, "description": "first", "name": "a_tool"},
                    {"args_schema": None, "description": "second", "name": "b_tool"},
                ],
            }
        )

    def test_digest_is_pinned_to_a_literal(self) -> None:
        # The value below is not decorative. Reproducing the document above by
        # construction only proves the two expressions agree; a literal is what
        # catches a change that edits both sides at once — a renamed key, a
        # dropped wrapper, a different sort — and forces the author to state
        # that they are re-keying every deployed prompt cache.
        tools = (FakeTool("b_tool", "second"), FakeTool("a_tool", "first"))

        assert ToolSchemaLedger.revision(tools) == (
            "f8bb41101ba0f33ea3cb6536cea9903eda874b7edddfd575d28fa11aa0ef4a0b"
        )

    def test_empty_surface_digest_is_pinned_to_a_literal(self) -> None:
        assert ToolSchemaLedger.revision(()) == (
            "9f69dfbfc6b8a0b8c754d47602bad278a1b206c493b5cc6ca08506bd76d6f866"
        )

    def test_factory_helper_delegates_to_the_ledger(self) -> None:
        surface = self.sample_surface()

        assert _model_tool_schema_revision(surface) == ToolSchemaLedger.revision(
            surface
        )

    def test_composition_order_does_not_change_the_digest(self) -> None:
        # Content identity, not sequence identity: reordering the append sites
        # changes nothing the model is shown, and invalidating the prompt cache
        # for it would be a self-inflicted cost.
        surface = self.sample_surface()

        assert ToolSchemaLedger.revision(tuple(reversed(surface))) == (
            ToolSchemaLedger.revision(surface)
        )

    def test_declaring_a_tool_does_not_change_the_digest(self) -> None:
        # The whole PRD rests on this: declarations stamp an attribute and
        # nothing the provider sees. If declaring moved the digest, adding an
        # origin would re-key the prompt cache.
        tool = FakeTool("publish_artifact", "Publish an artifact.")
        before = ToolSchemaLedger.revision((tool,))

        self.declared_tool(tool, owner=self.BACKENDS_OWNER, name="publish_artifact")

        assert ToolSchemaLedger.revision((tool,)) == before

    def test_a_changed_description_changes_the_digest(self) -> None:
        original = FakeTool("ask_a_question", "Ask the user a bounded question.")
        edited = FakeTool("ask_a_question", "Ask the user anything at all.")

        assert ToolSchemaLedger.revision((original,)) != ToolSchemaLedger.revision(
            (edited,)
        )

    def test_a_changed_args_schema_changes_the_digest(self) -> None:
        original = FakeTool("web_search", "Search the web.", SearchArgs)
        widened = FakeTool("web_search", "Search the web.", PublishArtifactArgs)

        assert ToolSchemaLedger.revision((original,)) != ToolSchemaLedger.revision(
            (widened,)
        )

    def test_a_dropped_tool_changes_the_digest(self) -> None:
        surface = self.sample_surface()

        assert ToolSchemaLedger.revision(surface[:-1]) != ToolSchemaLedger.revision(
            surface
        )

    def test_revision_refuses_a_tool_whose_schema_raises(self) -> None:
        # Fail-closed on purpose, and identical to the pre-ledger behaviour: a
        # digest that silently degrades on one broken tool is a cache-identity
        # bug that would never be noticed.
        broken = FakeTool("broken_tool", "unserializable", ExplodingSchema)

        with pytest.raises(RuntimeError):
            ToolSchemaLedger.revision((broken,))

    def test_revision_matches_the_legacy_failure_mode(self) -> None:
        broken = FakeTool("broken_tool", "unserializable", ExplodingSchema)

        with pytest.raises(RuntimeError):
            self.legacy_revision((broken,))


class TestToolSchemaEntry(ToolSurfaceMixin):
    """The body-free record is exactly what crosses the wire, and nothing else."""

    def test_carries_only_name_description_and_args_schema(self) -> None:
        entry = ToolSchemaLedger.schema_entry(
            FakeTool("web_search", "Search the web.", SearchArgs)
        )

        assert set(entry) == {"name", "description", "args_schema"}

    def test_expands_the_args_schema_the_provider_is_shown(self) -> None:
        entry = ToolSchemaLedger.schema_entry(
            FakeTool("web_search", "Search the web.", SearchArgs)
        )

        assert entry["args_schema"] == SearchArgs.model_json_schema()

    def test_a_schema_less_tool_reports_a_null_schema(self) -> None:
        entry = ToolSchemaLedger.schema_entry(FakeTool("ask_a_question", "Ask."))

        assert entry["args_schema"] is None

    def test_missing_attributes_read_as_empty_strings(self) -> None:
        entry = ToolSchemaLedger.schema_entry(object())

        assert entry == {"name": "", "description": "", "args_schema": None}


class TestToolSchemaFootprintContract:
    """The record is frozen, closed, and cannot carry a negative measurement."""

    def footprint(self, **overrides: object) -> ToolSchemaFootprint:
        fields: dict[str, object] = {
            "tool_name": "publish_artifact",
            "label": "agent_runtime.capabilities.backends:publish_artifact",
            "segment_class": ContextSegmentClass.TOOLS,
            "lifecycle": ContextLifecycle.RESIDENT,
            "byte_count": 2600,
            "estimated_tokens": 650,
            "declared": True,
        }
        fields.update(overrides)
        return ToolSchemaFootprint(**fields)  # type: ignore[arg-type]

    def test_defaults_to_first_party(self) -> None:
        assert self.footprint().third_party is False

    def test_is_frozen(self) -> None:
        with pytest.raises(ValidationError):
            self.footprint().estimated_tokens = 0  # type: ignore[misc]

    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            self.footprint(owner="agent_runtime.capabilities.backends")

    def test_rejects_a_negative_token_count(self) -> None:
        with pytest.raises(ValidationError):
            self.footprint(estimated_tokens=-1)

    def test_rejects_a_negative_byte_count(self) -> None:
        with pytest.raises(ValidationError):
            self.footprint(byte_count=-1)

    def test_rejects_an_empty_label(self) -> None:
        with pytest.raises(ValidationError):
            self.footprint(label="")


class TestHeuristicToolSchemaTokenCounter:
    """char/4, rounded up, over UTF-8 bytes — so it agrees with ``byte_count``."""

    def test_rounds_up_so_a_small_contributor_is_never_free(self) -> None:
        assert HeuristicToolSchemaTokenCounter.count("x") == 1

    def test_counts_whole_tokens_on_an_exact_boundary(self) -> None:
        assert HeuristicToolSchemaTokenCounter.count("x" * 8) == 2

    def test_empty_text_costs_nothing(self) -> None:
        assert HeuristicToolSchemaTokenCounter.count("") == 0

    def test_counts_utf8_bytes_not_characters(self) -> None:
        # "é" is two UTF-8 bytes. Counting characters here would report fewer
        # tokens than the ``byte_count`` sitting beside it in the same record.
        assert HeuristicToolSchemaTokenCounter.count("é" * 4) == 2


class TestToolSchemaMeasurement(LegacyToolSchemaRevisionMixin, ToolSurfaceMixin):
    """One row per tool, in composition order, with honest counts."""

    def test_measures_one_footprint_per_tool_in_composition_order(self) -> None:
        surface = self.sample_surface()

        measured = ToolSchemaLedger.measure(surface)

        assert tuple(footprint.tool_name for footprint in measured) == (
            "publish_artifact",
            "ask_a_question",
            "web_search",
        )

    def test_an_empty_surface_measures_to_no_rows(self) -> None:
        assert ToolSchemaLedger.measure(()) == ()

    def test_byte_count_is_the_canonical_size_of_the_body_free_entry(self) -> None:
        tool = FakeTool("ask_a_question", "Ask the user a bounded question.")

        (footprint,) = ToolSchemaLedger.measure((tool,))

        assert footprint.byte_count == self.entry_bytes(
            "ask_a_question", "Ask the user a bounded question."
        )

    def test_estimated_tokens_is_the_byte_count_over_four_rounded_up(self) -> None:
        surface = self.sample_surface()

        for footprint in ToolSchemaLedger.measure(surface):
            assert footprint.estimated_tokens == -(-footprint.byte_count // 4)

    def test_a_larger_description_measures_larger(self) -> None:
        small = FakeTool("t", "short")
        large = FakeTool("t", "short" * 200)

        (small_footprint,) = ToolSchemaLedger.measure((small,))
        (large_footprint,) = ToolSchemaLedger.measure((large,))

        assert large_footprint.estimated_tokens > small_footprint.estimated_tokens

    def test_a_typed_schema_costs_more_than_no_schema(self) -> None:
        # Audit item G: the ``args_schema`` JSON is real occupancy, so a tool
        # measured without expanding it would under-report every typed tool.
        bare = FakeTool("web_search", "Search the web.")
        typed = FakeTool("web_search", "Search the web.", SearchArgs)

        (bare_footprint,) = ToolSchemaLedger.measure((bare,))
        (typed_footprint,) = ToolSchemaLedger.measure((typed,))

        assert typed_footprint.byte_count > bare_footprint.byte_count

    def test_unicode_text_is_measured_in_bytes(self) -> None:
        tool = FakeTool("web_search", "résumé")

        (footprint,) = ToolSchemaLedger.measure((tool,))

        assert footprint.byte_count == self.entry_bytes("web_search", "résumé")

    def test_measuring_does_not_change_the_digest(self) -> None:
        surface = self.sample_surface()
        before = ToolSchemaLedger.revision(surface)

        ToolSchemaLedger.measure(surface)

        assert ToolSchemaLedger.revision(surface) == before

    def test_a_long_tool_name_is_bounded(self) -> None:
        # ``tool_name`` is persisted and served over HTTP, and a composed tool's
        # name ultimately comes from a registry this runtime does not own.
        (footprint,) = ToolSchemaLedger.measure((FakeTool("n" * 500, "unbounded"),))

        assert len(footprint.tool_name) == 200


class TestDeclaredLabels(ToolSurfaceMixin):
    """Labels come from the contributor's declaration, never from a table here."""

    def test_a_declared_tool_reports_its_owner_namespaced_label(self) -> None:
        tool = self.declared_tool(
            FakeTool("publish_artifact", "Publish an artifact.", PublishArtifactArgs),
            owner=self.BACKENDS_OWNER,
            name="publish_artifact",
        )

        (footprint,) = ToolSchemaLedger.measure((tool,))

        assert footprint.label == (
            "agent_runtime.capabilities.backends:publish_artifact"
        )
        assert footprint.declared is True

    def test_a_declared_tool_carries_its_class_and_lifecycle(self) -> None:
        tool = self.declared_tool(
            FakeTool("load_mcp_server", "Load an MCP server."),
            owner=self.MCP_OWNER,
            name="load_mcp_server",
        )

        (footprint,) = ToolSchemaLedger.measure((tool,))

        assert footprint.segment_class is ContextSegmentClass.TOOLS
        assert footprint.lifecycle is ContextLifecycle.RESIDENT

    def test_a_non_resident_lifecycle_is_reported_as_declared(self) -> None:
        tool = self.declared_tool(
            FakeTool("load_tool_spec", "Expand a capability reference."),
            owner="agent_runtime.capabilities.discovery",
            name="load_tool_spec",
            lifecycle=ContextLifecycle.ON_DEMAND,
        )

        (footprint,) = ToolSchemaLedger.measure((tool,))

        assert footprint.lifecycle is ContextLifecycle.ON_DEMAND

    def test_a_third_party_declaration_is_reported_as_third_party(self) -> None:
        # A third-party segment cannot be fixed by editing our source; it is
        # fixed by a profile exclusion or a dependency change, so the flag has
        # to survive measurement.
        tool = self.declared_tool(
            FakeTool("task", "Delegate to a subagent."),
            owner=self.THIRD_PARTY_OWNER,
            name="task",
            third_party=True,
        )

        (footprint,) = ToolSchemaLedger.measure((tool,))

        assert footprint.third_party is True

    def test_a_declaration_is_read_through_the_wrapper_chain(self) -> None:
        # Composed tools are re-wrapped by budget / policy / citation adapters
        # after the declaration is made, and the measurement site sees only the
        # outermost object.
        inner = self.declared_tool(
            FakeTool("call_mcp_tool", "Call a loaded MCP tool."),
            owner=self.MCP_OWNER,
            name="call_mcp_tool",
        )

        (footprint,) = ToolSchemaLedger.measure((WrappingTool(inner),))  # type: ignore[arg-type]

        assert footprint.label == "agent_runtime.capabilities.mcp:call_mcp_tool"

    def test_an_undeclared_tool_is_labelled_undeclared(self) -> None:
        (footprint,) = ToolSchemaLedger.measure((FakeTool("mystery", "unowned"),))

        assert footprint.label == UNDECLARED_CONTEXT_LABEL
        assert footprint.declared is False

    def test_an_undeclared_tool_keeps_the_truth_of_where_it_sits(self) -> None:
        # A missing declaration is a *labelling* gap. Defaulting the class or the
        # lifecycle to something else would corrupt the breakdown the report is
        # actually read by.
        (footprint,) = ToolSchemaLedger.measure((FakeTool("mystery", "unowned"),))

        assert footprint.segment_class is ContextSegmentClass.TOOLS
        assert footprint.lifecycle is ContextLifecycle.RESIDENT
        assert footprint.third_party is False

    def test_an_undeclared_tool_is_still_measured(self) -> None:
        # An undeclared tool occupies the window exactly as much as a declared
        # one. Reporting it as free is the failure mode ``undeclared_tokens``
        # exists to make visible.
        (footprint,) = ToolSchemaLedger.measure((FakeTool("mystery", "unowned"),))

        assert footprint.byte_count > 0
        assert footprint.estimated_tokens > 0

    def test_a_malformed_declaration_reads_as_undeclared(self) -> None:
        # The binding attribute namespace is shared with whatever else stamps a
        # tool, so a value that is not a ``ContextOrigin`` must not be trusted.
        tool = FakeTool("mystery", "unowned")
        tool.__context_origin__ = "agent_runtime:not_a_contract"  # type: ignore[attr-defined]

        (footprint,) = ToolSchemaLedger.measure((tool,))

        assert footprint.label == UNDECLARED_CONTEXT_LABEL
        assert footprint.declared is False


class TestMeasurementFailsOpen(ToolSurfaceMixin):
    """Occupancy is best-effort observability and must never fail a run (§6.4)."""

    def test_a_tool_whose_schema_raises_still_produces_a_row(self) -> None:
        # A missing row reads as "this tool is free"; a zero row reads as "we
        # failed to measure this tool". Only the second is honest.
        broken = FakeTool("broken_tool", "unserializable", ExplodingSchema)

        (footprint,) = ToolSchemaLedger.measure((broken,))

        assert footprint.tool_name == "broken_tool"
        assert footprint.byte_count == 0
        assert footprint.estimated_tokens == 0

    def test_a_broken_tool_keeps_its_declaration(self) -> None:
        broken = self.declared_tool(
            FakeTool("broken_tool", "unserializable", ExplodingSchema),
            owner=self.BACKENDS_OWNER,
            name="broken_tool",
        )

        (footprint,) = ToolSchemaLedger.measure((broken,))

        assert footprint.declared is True
        assert footprint.label == "agent_runtime.capabilities.backends:broken_tool"

    def test_a_broken_tool_does_not_stop_the_surrounding_tools(self) -> None:
        surface = (
            FakeTool("ask_a_question", "Ask."),
            FakeTool("broken_tool", "unserializable", ExplodingSchema),
            FakeTool("web_search", "Search.", SearchArgs),
        )

        measured = ToolSchemaLedger.measure(surface)

        assert tuple(footprint.tool_name for footprint in measured) == (
            "ask_a_question",
            "broken_tool",
            "web_search",
        )
        assert measured[0].byte_count > 0
        assert measured[2].byte_count > 0

    def test_a_tool_whose_name_cannot_be_read_still_produces_a_row(self) -> None:
        (footprint,) = ToolSchemaLedger.measure((UnreadableNameTool(),))

        assert footprint.tool_name == ""
        assert footprint.byte_count == 0
        assert footprint.declared is False

    def test_measurement_never_raises_on_a_hostile_surface(self) -> None:
        surface = (
            object(),
            UnreadableNameTool(),
            FakeTool("broken_tool", "unserializable", ExplodingSchema),
        )

        assert len(ToolSchemaLedger.measure(surface)) == 3


class TestInjectedCounter(ToolSurfaceMixin):
    """The counter is a seam PRD-04 fills, and it is treated as untrusted."""

    def test_the_injected_counter_is_used(self) -> None:
        counter = RecordingCounter(answer=42)

        (footprint,) = ToolSchemaLedger.measure(
            (FakeTool("ask_a_question", "Ask."),), counter=counter
        )

        assert footprint.estimated_tokens == 42

    def test_the_counter_is_handed_the_canonical_schema_text(self) -> None:
        counter = RecordingCounter(answer=7)
        tool = FakeTool("ask_a_question", "Ask.")

        ToolSchemaLedger.measure((tool,), counter=counter)

        assert counter.texts == [
            '{"args_schema":null,"description":"Ask.","name":"ask_a_question"}'
        ]

    def test_the_counter_is_called_once_per_tool(self) -> None:
        counter = RecordingCounter(answer=3)

        ToolSchemaLedger.measure(self.sample_surface(), counter=counter)

        assert len(counter.texts) == 3

    def test_a_raising_counter_falls_back_to_the_heuristic(self) -> None:
        counter = ExplodingCounter()
        tool = FakeTool("ask_a_question", "Ask.")

        (footprint,) = ToolSchemaLedger.measure((tool,), counter=counter)

        assert counter.calls == 1
        assert footprint.estimated_tokens == -(-footprint.byte_count // 4)

    @pytest.mark.parametrize("answer", [-1, "12", None, 3.5, True])
    def test_an_invalid_count_falls_back_to_the_heuristic(self, answer: object) -> None:
        # ``True`` is in this list on purpose: ``isinstance(True, int)`` is true
        # in Python, so a boolean would otherwise be accepted as one token.
        (footprint,) = ToolSchemaLedger.measure(
            (FakeTool("ask_a_question", "Ask."),),
            counter=RecordingCounter(answer=answer),
        )

        assert footprint.estimated_tokens == -(-footprint.byte_count // 4)

    def test_zero_from_the_counter_is_a_valid_answer(self) -> None:
        (footprint,) = ToolSchemaLedger.measure(
            (FakeTool("ask_a_question", "Ask."),),
            counter=RecordingCounter(answer=0),
        )

        assert footprint.estimated_tokens == 0

    def test_the_default_counter_is_the_heuristic(self) -> None:
        tool = FakeTool("ask_a_question", "Ask.")

        (footprint,) = ToolSchemaLedger.measure((tool,))

        assert footprint.estimated_tokens == HeuristicToolSchemaTokenCounter.count(
            '{"args_schema":null,"description":"Ask.","name":"ask_a_question"}'
        )


class TestNoContentLeakage(ToolSurfaceMixin):
    """Footprints carry counts and identifiers only (§6.5)."""

    def test_a_footprint_never_carries_the_description_text(self) -> None:
        description = "a very distinctive description body"
        tool = self.declared_tool(
            FakeTool("publish_artifact", description, PublishArtifactArgs),
            owner=self.BACKENDS_OWNER,
            name="publish_artifact",
        )

        (footprint,) = ToolSchemaLedger.measure((tool,))
        rendered = footprint.model_dump_json()

        assert description not in rendered

    def test_a_footprint_never_carries_the_args_schema(self) -> None:
        tool = FakeTool("web_search", "Search.", SearchArgs)

        (footprint,) = ToolSchemaLedger.measure((tool,))
        rendered = footprint.model_dump_json()

        assert "properties" not in rendered

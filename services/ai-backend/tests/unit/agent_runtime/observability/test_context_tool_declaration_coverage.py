"""The gate that keeps ``undeclared_tokens`` at zero for the tool block (§4.4).

``ContextOccupancySnapshot.undeclared_tokens`` is contractually **0** — anything
above it is "a first-party contract defect, not drift", and since PR #625/#626
the composer's context meter renders it to users as an "N undeclared" notice. A
live packaged run recorded 8,257 undeclared tokens on every model call, 3,459 of
them from the tool block, and every existing test was green over it.

They were green because the two mechanisms that were supposed to catch it both
looked somewhere else. The PRD-02 AST gate sweeps ``factory._model_visible_tools``,
which is the only place *this repository* composes a tool — but
``create_deep_agent`` installs the ``FilesystemMiddleware``, ``TodoListMiddleware``
and ``SubAgentMiddleware`` tools inside the library, so ten tools reached the
provider having never passed a site the gate can see. And every unit test that
exercised the ledger built its own fixture tools, which are declared by
construction; a fixture cannot notice that the real surface carries objects
nobody stamped.

So these tests are deliberately built on the **real** middleware tools and the
**real** composition chain rather than on stand-ins. That is the whole point:
this file has to fail when a dependency bump adds a tool we cannot attribute,
and a hand-built tool list can never do that. It is the reason
:class:`MiddlewareToolSurfaceMixin` builds the actual middleware tools and
:class:`FirstPartyToolSurfaceMixin` runs the actual factory-plus-display
decoration.

The second half of the file is the counterweight, and it matters as much as the
first. A resolver that declared *everything* would also drive the number to zero,
while destroying the only signal the field carries. So the undeclared path is
asserted to survive intact for a first-party tool that genuinely lost its
declaration — the case that must stay loud.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from pydantic import BaseModel, Field

from agent_runtime.capabilities.middleware.display_metadata import (
    wrap_tools_with_display,
)
from agent_runtime.capabilities.retrying_tool import RetryingTool
from agent_runtime.execution.factory import _model_visible_tools
from agent_runtime.observability.context_occupancy import GraphScope
from agent_runtime.observability.context_occupancy_recorder import (
    ContextOccupancyRecorder,
    ThirdPartyPromptIndex,
)
from agent_runtime.observability.context_origin import (
    ContextLifecycle,
    ContextOrigin,
    ContextSegmentClass,
    context_origin_of,
    declare_context_origin,
)
from agent_runtime.observability.context_third_party import ThirdPartyToolOrigins
from agent_runtime.observability.context_tool_ledger import (
    ToolSchemaFootprint,
    ToolSchemaLedger,
)


class SearchArgs(BaseModel):
    """A representative typed tool schema, shaped like a real model tool's."""

    query: str = Field(description="What to look up.")


class MiddlewareToolSurfaceMixin:
    """Build the tools ``create_deep_agent`` installs without a composition site.

    These are the objects that actually reach ``ModelRequest.tools`` and that no
    first-party append site ever touches. Building them for real — rather than
    restating the ten tool names — is what makes the assertion a gate instead of
    a mirror: a dependency bump that renames ``read_file``, adds a built-in, or
    moves a tool into a package outside ``DECLARABLE_ROOTS`` fails here, which is
    precisely the event §4.3 wants surfaced at review time.

    ``task`` comes from ``build_atlas_task_tool`` rather than from
    ``SubAgentMiddleware`` because that is what a run actually gets: this
    repository monkey-patches its own ``task`` over the library's, keeping the
    name and the slot. Constructing the library's version would have tested a
    tool the product does not ship — and would have needed a fake model and a
    full subagent spec to do it, coupling this gate to a constructor signature
    that has nothing to do with attribution.
    """

    def middleware_installed_tools(self) -> tuple[object, ...]:
        """Return the built-in tools that reach the model with no append site."""

        from deepagents.middleware import FilesystemMiddleware
        from langchain.agents.middleware import TodoListMiddleware

        from agent_runtime.delegation.subagents.atlas_task_tool import (
            build_atlas_task_tool,
        )

        return (
            *FilesystemMiddleware().tools,
            *TodoListMiddleware().tools,
            build_atlas_task_tool([]),
        )


class FirstPartyToolSurfaceMixin:
    """Compose the model tool surface exactly as the graph build does.

    ``_model_visible_tools`` then ``wrap_tools_with_display``, in that order,
    because the order is where the bug was: the factory declared ``web_search``
    correctly and the display decoration then rebuilt it into a fresh
    ``StructuredTool``, dropping the stamp. Asserting on the factory's output
    alone passes while the surface the model is shown is undeclared, so the
    decoration has to be inside the test.
    """

    def first_party_model_tools(self) -> tuple[object, ...]:
        """Return the decorated first-party tool surface for a minimal run."""

        from runtime_worker.dependencies import WebSearchToolRegistry

        composed = _model_visible_tools(
            tools=tuple(WebSearchToolRegistry().list_available_tools(None)),
            mcp_registry=None,
            skill_registry=None,
            prior_tool_result_loader=None,
            mcp_discovery_cache=None,
            code_mode_tool=None,
            sandbox_execute_tool=None,
            stage_rowset_write_tool=None,
            publish_artifact_tool=None,
            revise_artifact_tool=None,
            runtime_context=None,
            mcp_per_tool=None,
            mcp_catalog=None,
        )
        return tuple(wrap_tools_with_display(composed))


class MeasurementMixin:
    """Measure a tool surface the way the recorder does, and report offenders."""

    def footprints(self, tools: Sequence[object]) -> tuple[ToolSchemaFootprint, ...]:
        """Measure ``tools`` through the ledger with the production resolver."""

        return ToolSchemaLedger.measure(
            tools,
            fallback_origin=ThirdPartyToolOrigins().origin_for,
        )

    def undeclared(
        self,
        footprints: Sequence[ToolSchemaFootprint],
    ) -> tuple[ToolSchemaFootprint, ...]:
        """Return only the rows that carry no declaration."""

        return tuple(footprint for footprint in footprints if not footprint.declared)

    def offender_report(
        self,
        footprints: Sequence[ToolSchemaFootprint],
    ) -> str:
        """Name the undeclared tools and what they cost, for the failure message.

        A bare count would tell a future reader that something regressed without
        telling them which tool to go declare, which is the same unhelpfulness
        the ledger exists to fix.
        """

        return ", ".join(
            f"{footprint.tool_name} (~{footprint.estimated_tokens} tokens)"
            for footprint in self.undeclared(footprints)
        )


class StubModelRequest:
    """The four parts of a provider request, as the recorder reads them.

    ``MaterializedProviderRequest`` reaches for ``system_message`` / ``tools`` /
    ``messages`` / ``response_format`` by attribute, so a plain object with those
    four is a faithful stand-in for the library's ``ModelRequest`` without
    importing a type whose constructor is not ours.
    """

    class SystemMessage:
        """A system block, carried the way LangChain carries one."""

        def __init__(self, content: str) -> None:
            self.content = content

    def __init__(
        self,
        *,
        tools: Sequence[object],
        system_text: str = "",
    ) -> None:
        self.system_message = self.SystemMessage(system_text) if system_text else None
        self.tools = tuple(tools)
        self.messages: tuple[object, ...] = ()
        self.response_format = None


class StubCallIdentity:
    """The one field the recorder reads off a run's call identity."""

    def __init__(self, model_call_id: str = "model-call:fixture") -> None:
        self.model_call_id = model_call_id


class RecorderMixin:
    """Build a recorder with third-party *prompt* attribution switched off.

    Only the prompt index is disabled, and only so a tool-block assertion cannot
    fail for reasons about somebody else's system text. Tool attribution is the
    production resolver throughout.
    """

    def recorder(self) -> ContextOccupancyRecorder:
        """Return a recorder whose tool-block resolution is the production one."""

        return ContextOccupancyRecorder(third_party=ThirdPartyPromptIndex.disabled())

    def snapshot_for(self, tools: Sequence[object], *, system_text: str = ""):
        """Capture a snapshot over a request, with no assembly plan.

        ``plan`` is omitted on purpose: that is the desktop's state on every
        model call, and it is the configuration the whole defect showed up in.
        """

        return self.recorder().capture(
            StubModelRequest(tools=tools, system_text=system_text),
            identity=StubCallIdentity(),
            attempt_ordinal=1,
            graph_scope=GraphScope.ROOT,
            provider="anthropic",
            model_family="claude-sonnet-5",
            context_window_tokens=200_000,
        )


class TestBuiltInToolSurfaceIsDeclared(
    MiddlewareToolSurfaceMixin,
    FirstPartyToolSurfaceMixin,
    MeasurementMixin,
    RecorderMixin,
):
    """Every tool the model is shown must be attributable to somebody."""

    def test_middleware_installed_tools_contribute_no_undeclared_bytes(self) -> None:
        """The ten middleware built-ins all resolve to an authoring module."""

        footprints = self.footprints(self.middleware_installed_tools())

        assert footprints, "the pinned library installed no built-in tools"
        assert not self.undeclared(footprints), (
            "middleware-installed tools contributed undeclared context bytes: "
            f"{self.offender_report(footprints)}"
        )

    def test_library_tools_are_attributed_to_the_package_that_wrote_them(
        self,
    ) -> None:
        """A library tool is owned by its own module and marked third-party.

        The owner is what makes the row actionable, and for text we did not
        write the actionable move is a profile exclusion or a dependency change
        — which is exactly what ``third_party`` tells a reader.
        """

        by_name = {
            footprint.tool_name: footprint
            for footprint in self.footprints(self.middleware_installed_tools())
        }

        assert (
            by_name["read_file"].label == "deepagents.middleware.filesystem:read_file"
        )
        assert by_name["read_file"].third_party is True
        assert (
            by_name["write_todos"].label
            == "langchain.agents.middleware.todo:write_todos"
        )
        assert by_name["write_todos"].third_party is True

    def test_the_task_tool_is_reported_as_ours_not_the_librarys(self) -> None:
        """``task`` occupies a library slot but is authored in this repository.

        ``build_atlas_task_tool`` replaces ``deepagents``' own ``task``, keeping
        its name and its middleware slot. Everything visible from the outside
        says third-party, and roughly 390 resident tokens of description would
        have been billed to a dependency that does not contain the string. This
        assertion is the one that stops the resolver guessing by appearance.
        """

        by_name = {
            footprint.tool_name: footprint
            for footprint in self.footprints(self.middleware_installed_tools())
        }

        assert by_name["task"].third_party is False
        assert by_name["task"].label == (
            "agent_runtime.delegation.subagents.atlas_task_tool:task"
        )

    def test_first_party_tool_surface_contributes_no_undeclared_bytes(self) -> None:
        """The composed-and-decorated first-party surface is fully declared."""

        footprints = self.footprints(self.first_party_model_tools())

        assert footprints, "the factory composed no model tools"
        assert not self.undeclared(footprints), (
            "first-party model tools contributed undeclared context bytes: "
            f"{self.offender_report(footprints)}"
        )

    def test_web_search_is_owned_by_the_module_that_writes_its_description(
        self,
    ) -> None:
        """``web_search`` is ours, and the report must not blame the library.

        It reaches the factory inside a ``RetryingTool``, which used to make it
        both undeclared (the display decoration rebuilt it) and, once declared
        by the injected-registry group, attributed to ``deepagents.middleware``
        — a package that does not contain the string being measured.
        """

        by_name = {
            footprint.tool_name: footprint
            for footprint in self.footprints(self.first_party_model_tools())
        }

        assert by_name["web_search"].label == "runtime_worker.dependencies:web_search"
        assert by_name["web_search"].third_party is False

    def test_snapshot_over_the_whole_surface_reports_zero_undeclared_tokens(
        self,
    ) -> None:
        """The wiring, not just the resolver: a captured snapshot reconciles to 0.

        The resolver existing is not the same as the recorder using it — the
        third-party *prompt* adapter had shipped for exactly this long while the
        tool block never consulted anything — so the assertion that matters is
        made on the snapshot the run actually persists.
        """

        tools = (*self.middleware_installed_tools(), *self.first_party_model_tools())

        snapshot = self.snapshot_for(tools)

        assert snapshot is not None
        assert snapshot.estimated_input_tokens > 0, "the surface measured as free"
        assert snapshot.undeclared_tokens == 0, (
            "the model tool surface reported undeclared occupancy: "
            f"{self.offender_report(self.footprints(tools))}"
        )

    def test_a_whole_desktop_shaped_request_reports_zero_undeclared(self) -> None:
        """The end-to-end shape the live run recorded: system block plus tools.

        The packaged run this work started from reported 8,257 undeclared tokens
        on every model call — 4,798 from a system block with no assembly plan
        and 3,459 from tools nobody had stamped. Both halves are measured here in
        one snapshot, with no plan, because that is the configuration the desktop
        runs in and asserting the halves separately would let their sum regress
        unnoticed.
        """

        tools = (*self.middleware_installed_tools(), *self.first_party_model_tools())

        snapshot = self.snapshot_for(
            tools,
            system_text="You are a careful assistant. Follow the workspace rules.",
        )

        assert snapshot is not None
        assert snapshot.undeclared_tokens == 0
        assert {segment.label for segment in snapshot.segments} == {
            segment.label
            for segment in snapshot.segments
            if segment.label != "UNDECLARED"
        }


class TestUndeclaredOccupancyStaysVisible(MeasurementMixin, RecorderMixin):
    """The resolver must not become a blanket amnesty.

    Driving ``undeclared_tokens`` to zero is only worth anything if the field can
    still reach non-zero. Every test here asserts a case that must *stay*
    undeclared, because each one is a real defect a reader needs to see.
    """

    def _first_party_tool(self, name: str = "orphan_tool") -> object:
        """Build an undeclared tool whose dispatch callable is defined here."""

        from langchain_core.tools import StructuredTool

        def _run(query: str) -> str:
            return query

        return StructuredTool.from_function(
            func=_run,
            name=name,
            description="A first-party tool nobody declared.",
            args_schema=SearchArgs,
        )

    def test_undeclared_first_party_tool_still_measures_undeclared(self) -> None:
        """A tool authored in this repository is never declared on its behalf.

        This is the case the whole design turns on. The resolver reads a tool's
        authoring module, and text written in this repository has a composition
        site that could have declared it — so declaring it here would convert an
        actionable defect into a silent one.
        """

        (footprint,) = self.footprints([self._first_party_tool()])

        assert footprint.declared is False
        assert footprint.label == "UNDECLARED"
        assert footprint.estimated_tokens > 0

    def test_snapshot_surfaces_an_undeclared_first_party_tool(self) -> None:
        """The undeclared bytes reach ``undeclared_tokens``, not just the row."""

        snapshot = self.snapshot_for([self._first_party_tool()])

        assert snapshot is not None
        assert snapshot.undeclared_tokens > 0

    def test_an_existing_declaration_wins_over_the_resolver(self) -> None:
        """A stamp made at composition is never overwritten by inference.

        The composing code knows what it built; the resolver is reading a
        dependency's internals. If the two ever disagree, the stamp is the one
        with standing.
        """

        tool = self._first_party_tool(name="declared_tool")
        declare_context_origin(
            tool,
            ContextOrigin(
                owner="agent_runtime.capabilities.tools",
                name="declared_tool",
                segment_class=ContextSegmentClass.TOOLS,
                lifecycle=ContextLifecycle.RESIDENT,
            ),
        )

        (footprint,) = ToolSchemaLedger.measure(
            [tool],
            fallback_origin=lambda _tool: ContextOrigin(
                owner="deepagents.middleware",
                name="usurper",
                segment_class=ContextSegmentClass.TOOLS,
                lifecycle=ContextLifecycle.RESIDENT,
                third_party=True,
            ),
        )

        assert footprint.label == "agent_runtime.capabilities.tools:declared_tool"
        assert footprint.third_party is False

    def test_a_resolver_that_raises_degrades_to_undeclared(self) -> None:
        """Measurement is on the model-call path and may never raise (§6.4)."""

        def _explode(_tool: object) -> ContextOrigin | None:
            raise RuntimeError("resolver failure")

        (footprint,) = ToolSchemaLedger.measure(
            [self._first_party_tool()],
            fallback_origin=_explode,
        )

        assert footprint.declared is False
        assert footprint.label == "UNDECLARED"

    def test_a_resolver_returning_a_non_origin_is_ignored(self) -> None:
        """An untyped return is treated as "nothing to say", never trusted.

        Trusting it would put a non-contract object into ``label`` and
        ``lifecycle`` and fail the row's own validation instead — on the
        model-call path, which is the failure this whole lane is built to avoid.
        """

        (footprint,) = ToolSchemaLedger.measure(
            [self._first_party_tool()],
            fallback_origin=lambda _tool: "deepagents.middleware:not_an_origin",  # type: ignore[return-value]
        )

        assert footprint.declared is False
        assert footprint.label == "UNDECLARED"

    def test_omitting_the_resolver_preserves_the_original_behaviour(self) -> None:
        """``measure`` without a resolver is exactly what it was before."""

        (footprint,) = ToolSchemaLedger.measure([self._first_party_tool()])

        assert footprint.declared is False
        assert footprint.label == "UNDECLARED"


class TestDisplayWrappingPreservesDeclarations:
    """A re-wrap must carry the declaration the composition site made.

    ``wrap_tools_with_display`` has two branches and they used to behave
    differently: the ``StructuredTool`` branch copies the instance (stamp
    included) while the delegation branch rebuilds the tool with
    ``from_function`` and holds the inner in a closure — no stamp, and no
    attribute chain for ``ContextOriginBinding.of`` to walk. Every non-
    ``StructuredTool`` on the surface was silently un-declared on the way to the
    model, which is how a correctly-declared ``web_search`` was reported as a
    first-party contract defect.
    """

    def _declared_structured_tool(self, name: str) -> object:
        from langchain_core.tools import StructuredTool

        def _run(query: str) -> str:
            return query

        tool = StructuredTool.from_function(
            func=_run,
            name=name,
            description="Declared at its composition site.",
            args_schema=SearchArgs,
        )
        return declare_context_origin(
            tool,
            ContextOrigin(
                owner="agent_runtime.capabilities.tools",
                name=name,
                segment_class=ContextSegmentClass.TOOLS,
                lifecycle=ContextLifecycle.RESIDENT,
            ),
        )

    def test_structured_tool_rewrap_keeps_its_declaration(self) -> None:
        """The copying branch keeps the stamp, and stays that way."""

        source = self._declared_structured_tool("structured_tool")

        (wrapped,) = wrap_tools_with_display([source])

        origin = context_origin_of(wrapped)
        assert origin is not None
        assert origin.label == "agent_runtime.capabilities.tools:structured_tool"

    def test_delegating_tool_rewrap_keeps_its_declaration(self) -> None:
        """The rebuilding branch — the one that dropped it — now carries it over.

        ``RetryingTool`` is the real shape this happens to: it is a ``BaseTool``
        and not a ``StructuredTool``, which is exactly the branch predicate.
        """

        inner = self._declared_structured_tool("retried_tool")
        source = RetryingTool.wrapping(
            inner,
            max_attempts=2,
            initial_backoff_seconds=0.1,
            max_backoff_seconds=1.0,
        )
        assert context_origin_of(source) is not None, "fixture is not declared"

        (wrapped,) = wrap_tools_with_display([source])

        origin = context_origin_of(wrapped)
        assert origin is not None, (
            "the display decoration dropped the declaration made at composition"
        )
        assert origin.label == "agent_runtime.capabilities.tools:retried_tool"

    def test_rewrapping_an_undeclared_tool_declares_nothing(self) -> None:
        """Carrying a declaration over never invents one."""

        from langchain_core.tools import StructuredTool

        def _run(query: str) -> str:
            return query

        source = RetryingTool.wrapping(
            StructuredTool.from_function(
                func=_run,
                name="undeclared_tool",
                description="Nobody declared this.",
                args_schema=SearchArgs,
            ),
            max_attempts=2,
            initial_backoff_seconds=0.1,
            max_backoff_seconds=1.0,
        )

        (wrapped,) = wrap_tools_with_display([source])

        assert context_origin_of(wrapped) is None


class TestThirdPartyToolOriginResolution:
    """The resolver's own rules, asserted directly rather than through a surface."""

    @pytest.fixture
    def origins(self) -> ThirdPartyToolOrigins:
        return ThirdPartyToolOrigins()

    def test_a_tool_with_no_readable_callable_is_not_declared(
        self,
        origins: ThirdPartyToolOrigins,
    ) -> None:
        """No authoring module means no owner, and an absent owner is honest."""

        class Opaque:
            name = "opaque"

        assert origins.origin_for(Opaque()) is None

    def test_the_tool_class_module_is_never_used_as_the_owner(
        self,
        origins: ThirdPartyToolOrigins,
    ) -> None:
        """``StructuredTool`` lives in ``langchain_core`` whoever wrote the tool.

        ``langchain_core`` is a declarable root, so a fallback to
        ``type(tool).__module__`` would not merely degrade — it would confidently
        stamp every tool on the surface as third-party ``langchain_core`` text
        and send a reader to edit a package that contains none of it.
        """

        from langchain_core.tools import StructuredTool

        def _run(query: str) -> str:
            return query

        tool = StructuredTool.from_function(
            func=_run,
            name="first_party",
            description="Authored in this test module.",
            args_schema=SearchArgs,
        )
        # ``func`` is defined here, outside every declarable root.
        assert origins.origin_for(tool) is None

    def test_a_module_outside_the_declarable_roots_is_not_declared(
        self,
        origins: ThirdPartyToolOrigins,
    ) -> None:
        """An unrecognised author lands in ``undeclared_tokens``, not in a vendor.

        The allowlist direction: "not one of ours" is never taken as evidence of
        "therefore theirs".
        """

        class Vendored:
            name = "vendored"

            def func(self) -> None: ...

        Vendored.func.__module__ = "some_unpinned_package.tools"
        assert origins.origin_for(Vendored()) is None

    def test_an_unnamed_third_party_tool_is_not_declared(
        self,
        origins: ThirdPartyToolOrigins,
    ) -> None:
        """A label needs both halves; half a label traces back to nothing."""

        class Unnamed:
            name = "   "

            def func(self) -> None: ...

        Unnamed.func.__module__ = "deepagents.middleware.filesystem"
        assert origins.origin_for(Unnamed()) is None

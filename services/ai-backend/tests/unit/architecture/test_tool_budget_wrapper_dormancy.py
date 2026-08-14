"""``ToolBudgetGuardedTool``'s reject branch is on no shipped path — pinned.

``ToolBudgetGuardedTool._run`` / ``._arun`` raise ``guard.rejection_error`` from
*inside* the tool when a hard cap refuses. That exception is a
``ToolBudgetRejected``, so the wrapper directly outside it —
``ToolErrorPolicyTool`` — classifies it ``SURFACE_TO_LLM`` and returns the
sanitized text as an ordinary tool return. LangChain then builds a
``ToolMessage`` with ``status="success"`` and the refusal is published as
``completed``: the client draws a refused call as "Done".

That is strictly worse than the "Failed" defect journey phase CB-7 reported and
:mod:`agent_runtime.execution.tool_refusals` fixed. The middleware seam avoids it
by *returning* a ``ToolMessage`` rather than raising — ``_surface_rejection``
authors the message itself, so it can attach the typed refusal marker that
``tool_result_payload`` reads to publish ``unavailable``. A refusal raised from
inside the tool has no such seam: by the time the error policy sees it, the
authored-message opportunity is gone.

The branch is unreachable today, and this module pins the three independent
reasons so it cannot be switched back on without someone reading this file:

1. Nothing in ``src/`` installs the wrapper. ``guard_model_tools`` and
   ``ToolBudgetGuardedRegistry`` — the only two things that construct it — have
   no production call site. The shipped registry chain is
   ``ToolErrorPolicyRegistry(CitationCapturingRegistry(WebSearchToolRegistry))``.
2. The graph boundary strips it. ``_canonical_graph_tool`` unwraps every
   ``ToolBudgetGuardedTool`` before the tool surface reaches the builder.
3. The middleware covers every call. ``RuntimeControlMiddleware`` is installed
   unconditionally on both the supervisor and child graphs, and binds a
   ``RuntimeCallContext`` around the handler — which is exactly the condition
   the wrapper early-returns on.

Barrier 1 is the load-bearing one; 2 and 3 are defence in depth. If you are here
because one of these tests failed, the fix is not to relax it: route the
wrapper's rejection through ``ToolRefusals`` the way ``_surface_rejection``
does, then update ``TestWhyTheWrapperMustStayUninstalled`` to assert
``unavailable``.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, cast

from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.tools import BaseTool

from agent_runtime.api.constants import Keys, Values
from agent_runtime.capabilities.middleware.runtime_tool_control import (
    _surface_rejection,
)
from agent_runtime.capabilities.tool_budget_guard import (
    ToolBudgetGuard,
    ToolBudgetGuardedTool,
)
from agent_runtime.capabilities.tool_budget_middleware import ToolBudgetMiddleware
from agent_runtime.capabilities.tool_error_policy_tool import ToolErrorPolicyTool
from agent_runtime.execution.call_identity import (
    RuntimeCallContext,
    RuntimeToolCallIdentity,
)
from agent_runtime.execution.factory import _canonical_graph_tool
from agent_runtime.execution.tool_refusals import ToolRefusals
from agent_runtime.persistence.records import (
    ToolBudgetEnforcement,
    ToolBudgetRecord,
)
from runtime_worker.stream_tools import StreamMessageProcessor
from runtime_worker.tool_call_ledger import ToolCallLedger

_SERVICE_ROOT = Path(__file__).resolve().parents[3]
_SOURCE_ROOT = _SERVICE_ROOT / "src"
_FACTORY = _SOURCE_ROOT / "agent_runtime/execution/factory.py"

#: The module that defines the wrapper. It constructs one by definition; every
#: other module doing so is an installation.
_DEFINING_MODULE = _SOURCE_ROOT / "agent_runtime/capabilities/tool_budget_guard.py"

#: The calls in ``_assemble_harness`` that consume the incoming tool surface and
#: turn it into what the graph dispatches.
_SURFACE_CONSUMERS = frozenset(
    {
        "_mcp_per_tool_registration",
        "_model_visible_tools",
    }
)

#: Calling any of these puts a budget gate on a model-visible tool.
_INSTALLERS = frozenset(
    {
        "guard_model_tools",
        "ToolBudgetGuardedRegistry",
        "ToolBudgetGuardedTool",
    }
)


class _Echo(BaseTool):
    """Inner tool that records whether the gate let dispatch through."""

    name: str = "echo"
    description: str = "Echoes the input back for tests."
    dispatched: bool = False

    def _run(self, *args: object, **kwargs: object) -> str:
        self.dispatched = True
        return "echo-ok"

    async def _arun(self, *args: object, **kwargs: object) -> str:
        self.dispatched = True
        return "echo-ok"


class DormantWrapperMixin:
    """Source scanning plus a live drive of the dormant reject branch."""

    @staticmethod
    def _called_names(path: Path) -> set[str]:
        """Return every simple name this module *calls*.

        Calls only. ``factory.py`` legitimately names the wrapper in an
        ``isinstance`` check in order to strip it, and a reference is not an
        installation — only construction is.
        """

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
        return names

    @classmethod
    def _installation_sites(cls) -> list[str]:
        """Return every shipped module that installs the budget wrapper."""

        return sorted(
            str(path.relative_to(_SOURCE_ROOT))
            for path in _SOURCE_ROOT.rglob("*.py")
            if path != _DEFINING_MODULE and cls._called_names(path) & _INSTALLERS
        )

    @staticmethod
    def _surface_arguments() -> dict[str, str]:
        """Return ``{callee: tools= expression}`` for each tool-surface consumer.

        ``RuntimeHarness(tools=tools)`` is deliberately not among them: it
        records the raw argument for bookkeeping and no dispatch path reads it
        back, so it is not a route into the graph.
        """

        tree = ast.parse(_FACTORY.read_text(encoding="utf-8"), filename=str(_FACTORY))
        arguments: dict[str, str] = {}
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in _SURFACE_CONSUMERS
            ):
                continue
            for entry in node.keywords:
                if entry.arg == "tools":
                    arguments[node.func.id] = ast.unparse(entry.value)
        return arguments

    @staticmethod
    def _keyword_elements(keyword: str) -> list[str]:
        """Return the top-level element names of a ``DeepAgentBuildRequest`` tuple.

        Top level is the point: an element nested inside a conditional or a
        starred call would not appear here, so a middleware that became optional
        would drop out of this list rather than silently stay in it.
        """

        tree = ast.parse(_FACTORY.read_text(encoding="utf-8"), filename=str(_FACTORY))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "DeepAgentBuildRequest"
            ):
                continue
            for entry in node.keywords:
                if entry.arg != keyword or not isinstance(entry.value, ast.Tuple):
                    continue
                names: list[str] = []
                for element in entry.value.elts:
                    if isinstance(element, ast.Call) and isinstance(
                        element.func, ast.Name
                    ):
                        names.append(element.func.id)
                    elif isinstance(element, ast.Name):
                        names.append(element.id)
                return names
        return []

    @staticmethod
    def _exhausted_guard(cap: int = 2) -> ToolBudgetGuard:
        """Build a guard whose ``echo`` budget is already fully spent."""

        ledger = ToolCallLedger(run_id="run-dormancy")
        for index in range(cap):
            ledger.started(f"prior-{index}", tool_name="echo", budget_scoped=True)
        return ToolBudgetGuard(
            middleware=ToolBudgetMiddleware(
                [
                    ToolBudgetRecord(
                        org_id=None,
                        tool_name="echo",
                        max_calls_per_run=cap,
                        enforcement=ToolBudgetEnforcement.HARD,
                    )
                ]
            ),
            ledger=ledger,
        )

    @staticmethod
    def _identity() -> RuntimeToolCallIdentity:
        return RuntimeToolCallIdentity(
            run_id="run-dormancy",
            snapshot_id="snapshot-1",
            execution_scope="supervisor",
            model_turn=1,
            model_tool_call_id="toolu_01",
            operation_id="op-1",
            control_call_id="runtime-control:1",
        )

    @staticmethod
    def _production_layering(inner: BaseTool) -> ToolErrorPolicyTool:
        """Wrap ``inner`` the way the registry chain would: ErrorPolicy(Budget(t))."""

        guarded = ToolBudgetGuardedTool(
            name=inner.name,
            description=inner.description,
            inner=inner,
        )
        return ToolErrorPolicyTool(
            name=guarded.name,
            description=guarded.description,
            inner=guarded,
        )

    @classmethod
    async def _drive(cls, *, bind_call_context: bool) -> tuple[_Echo, object]:
        """Refuse one call at the gate and return the tool plus LangChain's message."""

        inner = _Echo()
        wrapped = cls._production_layering(inner)
        call = {
            "type": "tool_call",
            "name": "echo",
            "args": {"input": "hello"},
            "id": "toolu_01",
        }
        token = ToolBudgetGuard.bind_for_run(cls._exhausted_guard())
        try:
            if bind_call_context:
                with RuntimeCallContext.bind(cls._identity()):
                    return inner, await wrapped.ainvoke(call)
            return inner, await wrapped.ainvoke(call)
        finally:
            ToolBudgetGuard.unbind(token)


class TestTheWrapperIsInstalledNowhere(DormantWrapperMixin):
    def test_no_shipped_module_installs_a_budget_wrapper(self) -> None:
        sites = self._installation_sites()

        assert sites == [], (
            "A module now installs ToolBudgetGuardedTool: "
            f"{sites}. Its reject branch raises out of the tool and publishes "
            "as `completed` — a refused call renders as 'Done'. Route the "
            "rejection through ToolRefusals before wiring this in."
        )

    def test_the_installers_still_exist_to_be_scanned_for(self) -> None:
        # Guards the scan itself: renaming the entry points without updating
        # _INSTALLERS would leave a test that passes by finding nothing.
        assert self._called_names(_DEFINING_MODULE) & _INSTALLERS


class TestTheGraphBoundaryStripsIt(DormantWrapperMixin):
    def test_a_guarded_tool_is_unwrapped_before_the_builder(self) -> None:
        inner = _Echo()
        guarded = ToolBudgetGuardedTool(
            name=inner.name, description=inner.description, inner=inner
        )

        assert _canonical_graph_tool(guarded) is inner

    def test_every_consumer_of_the_tool_surface_gets_the_stripped_one(self) -> None:
        arguments = self._surface_arguments()

        # Both consumers, not merely one: the model-visible surface is built
        # from the second, and passing the raw `tools` to either would carry an
        # unstripped wrapper into the graph.
        assert set(arguments) == _SURFACE_CONSUMERS, (
            f"consumers of the tool surface changed: {sorted(arguments)}"
        )
        for callee, expression in sorted(arguments.items()):
            assert expression == "canonical_tools", (
                f"{callee}(tools={expression}) receives the unstripped surface"
            )


class TestTheMiddlewareCoversEveryCall(DormantWrapperMixin):
    def test_runtime_control_is_unconditional_on_the_supervisor_graph(self) -> None:
        assert "RuntimeControlMiddleware" in self._keyword_elements("middleware")

    def test_runtime_control_is_unconditional_on_child_graphs(self) -> None:
        # A subagent does not inherit the supervisor's sequence, so the child
        # factory list is a separate guarantee, not a restatement.
        assert "RuntimeControlMiddleware" in self._keyword_elements(
            "universal_middleware_factories"
        )

    async def test_a_bound_call_context_makes_the_wrapper_delegate(self) -> None:
        # The wrapper's own early-return: with the middleware's context present
        # the exhausted budget is not consulted here at all, and the inner tool
        # runs. Enforcement happened at the middleware seam instead.
        inner, message = await self._drive(bind_call_context=True)

        assert inner.dispatched is True
        assert "echo-ok" in str(getattr(message, "content", ""))


class TestWhyTheWrapperMustStayUninstalled(DormantWrapperMixin):
    """The defect the three barriers above are holding shut.

    These assertions describe broken behaviour on purpose. They are the reason
    the topology is pinned, and they fail the moment the branch is fixed — at
    which point the barriers become optional and this class should assert
    ``unavailable`` instead.
    """

    async def test_the_refusal_reaches_langchain_as_a_successful_return(self) -> None:
        _, message = await self._drive(bind_call_context=False)

        # Raised from inside the tool, caught by the error policy, returned as
        # an ordinary value: nothing downstream can tell this from a result.
        assert getattr(message, "status", None) == "success"

    async def test_the_refusal_carries_no_typed_marker(self) -> None:
        _, message = await self._drive(bind_call_context=False)

        # `_surface_rejection` attaches this; a raise has no seam to attach it.
        assert ToolRefusals.read(message) is None

    async def test_the_client_would_be_told_the_call_completed(self) -> None:
        _, message = await self._drive(bind_call_context=False)
        payload = StreamMessageProcessor.tool_result_payload(message)

        assert payload[Keys.Field.STATUS] == Values.Status.COMPLETED
        assert payload[Keys.Field.STATUS] != Values.Status.UNAVAILABLE
        assert "error_code" not in payload

    async def test_the_middleware_seam_gets_the_same_refusal_right(self) -> None:
        # Same guard, same exhausted budget, same tool — the only difference is
        # which seam refuses. This is the contrast the pin exists to preserve.
        guard = self._exhausted_guard()
        decision, _ = guard.admit_and_charge(tool_name="echo", estimated_input_tokens=1)
        rejection = guard.rejection_error(decision)
        surfaced = _surface_rejection(
            rejection,
            request=ToolCallRequest(
                tool_call={
                    "name": "echo",
                    "args": {"input": "hello"},
                    "id": "toolu_01",
                    "type": "tool_call",
                },
                tool=None,
                state={},
                runtime=cast(Any, object()),
            ),
        )
        payload = StreamMessageProcessor.tool_result_payload(surfaced)

        assert payload[Keys.Field.STATUS] == Values.Status.UNAVAILABLE

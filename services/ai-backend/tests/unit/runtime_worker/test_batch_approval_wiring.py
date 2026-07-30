"""BUG-19 — the run authority BUG-15 named, proven to be *reached*.

BUG-15 proved the rule: a capability whose dispatch can pause for a human is
refused a plan entry however parallel-safe the catalog says it is. It proved it
thoroughly, and against the exact adversarial case. What no test in that lane
could establish is that production ever consults the authority the rule depends
on, because nothing did — ``RunBatchAdmission`` was constructed in exactly one
place in ``src/`` and that place passed no ``approvals=``, so
``GraphApprovalRequirementResolver.resolve(None, ...)`` answered *not declared*
on every call and ``ToolUsePolicySnapshot`` was never asked anything.

Every proof here therefore enters through the composition root
:class:`BatchConcurrencyComposer` — the object both the run handler and the
approval handler call — rather than through the concurrency package's own
fixtures. The catalog is the operator's environment document, parsed by the real
parser; the tool-use policy is a real ``AgentRuntimeContext`` resolved by the
real :class:`ToolUsePolicyResolver`; the approval decision is the real
:class:`ToolUsePolicyEnforcer`'s. Only the journal's *storage* is substituted,
which is the same substitution W4 makes and for the same reason: the durable
adapter has its own tests over the real event stream, and the records this one
hands back are the same contracts.

:class:`TestTheAuthorityDecidesWhetherAnMcpReadOverlaps` is the pair that closes
the bug, and it is a pair on purpose. One catalog, one turn, two tool-use
policies, two outcomes. The gated half alone would pass just as well if the
wiring had simply switched F6 off; the ungated half is what makes it a proof
that the authority was *consulted* rather than that overlap was abolished.

Nothing here sleeps on the wall clock.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from langchain.agents import create_agent
from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import StructuredTool
import pytest

from agent_runtime.capabilities.concurrency import (
    ApprovalRequirement,
    BatchSegmentMode,
)
from agent_runtime.capabilities.concurrency.graph_admission import (
    GraphApprovalRequirementResolver,
    GraphApprovalRequirementSource,
    graph_capability_key,
)
from agent_runtime.capabilities.mcp.constants import Values as McpValues
from agent_runtime.capabilities.middleware.runtime_tool_control import (
    RuntimeToolControlMiddleware,
)
from agent_runtime.capabilities.tools.permissions import ToolUsePolicySnapshot
from agent_runtime.control_plane.context import RunControlContext
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeBatchAdmissionContext,
)
from runtime_worker.batch_approval_authority import ToolUsePolicyApprovalSource
from runtime_worker.batch_concurrency_composition import (
    BatchConcurrencyComposer,
    BatchConcurrencyEnvironment,
)

from tests.unit.agent_runtime.capabilities.middleware.test_runtime_tool_control_batch import (  # noqa: E501
    _FANOUT,
    _ORG,
    _TRACE,
    _InMemoryBatchJournal,
    _binding,
)

_SOURCE_ROOT = Path(__file__).resolve().parents[3] / "src"

#: The umbrella model tool every connector action flows through, and the only
#: graph tool this deployment's tool-use policy is able to gate. Read from the
#: production constant rather than spelled out, so a rename cannot leave these
#: proofs asserting about a tool the policy no longer reaches.
_UMBRELLA = McpValues.ToolName.CALL_MCP_TOOL

_SERVER = "github"
_INNER_TOOL = "search"

#: The key an operator files a declaration under for the MCP capability above.
#: It is deliberately *not* the graph tool name: an MCP call names its server and
#: tool inside ``call_mcp_tool``'s arguments, so the catalog is keyed by the
#: capability while the approval authority is asked about the umbrella. Joining
#: those two keyings correctly is most of what this lane had to get right.
_CAPABILITY_KEY = graph_capability_key(
    tool_name=_UMBRELLA,
    arguments={"server_name": _SERVER, "tool_name": _INNER_TOOL},
)

#: Every positive claim an operator can make for overlap, about a capability
#: whose declaration is entirely honest: a parallel-safe read whose own
#: execution never pauses for a human. The catalog author cannot see this run's
#: tool-use policy, and that is the whole defect.
_CATALOG = json.dumps(
    {
        _CAPABILITY_KEY: {
            "mode": "parallel_safe",
            "side_effect": "read",
            "approval_requirement": "never",
            "max_parallelism": _FANOUT,
        }
    }
)


class _McpFanoutModel(BaseChatModel):
    """Emit three sibling MCP reads in one turn, then a final answer."""

    @property
    def _llm_type(self) -> str:
        return "bug19-mcp-fanout-test"

    @staticmethod
    def _reply(messages: list[BaseMessage]) -> AIMessage:
        if any(isinstance(message, ToolMessage) for message in messages):
            return AIMessage(content="done")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": _UMBRELLA,
                    "args": {
                        "server_name": _SERVER,
                        "tool_name": _INNER_TOOL,
                        "value": value,
                    },
                    "id": f"call-{value}",
                }
                for value in range(_FANOUT)
            ],
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager, kwargs
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    def bind_tools(self, tools: Any, **kwargs: Any) -> Runnable:
        del tools, kwargs
        return self


class _OverlapProbe:
    """Observed simultaneity across a fixed number of scheduler turns.

    The window is a count of ``asyncio`` yields, never an elapsed duration, so
    the observation is reproducible rather than timing-dependent. Widening it
    can never manufacture overlap: under an exclusive permit the second caller
    is not merely late, it is not running.
    """

    YIELDS: int = 32

    def __init__(self) -> None:
        self.active = 0
        self.maximum_active = 0
        self.completed: list[int] = []

    async def observe(self, server_name: str, tool_name: str, value: int) -> str:
        del server_name, tool_name
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        for _ in range(self.YIELDS):
            await asyncio.sleep(0)
            self.maximum_active = max(self.maximum_active, self.active)
        self.active -= 1
        self.completed.append(value)
        return str(value)


class _RecordingComposer(BatchConcurrencyComposer):
    """The real composer, journalling to memory instead of the event stream.

    Only :meth:`_journal`'s storage is substituted. The presence gate, the
    catalog parse, the kill-switch gate, the permit ladder, the coordinator, and
    — the point of this file — the construction of the run-scoped approval
    authority are all the production ones.
    """

    def __init__(self, journal: _InMemoryBatchJournal, *, catalog: str) -> None:
        super().__init__(
            events=object(),  # type: ignore[arg-type]
            snapshots=object(),  # type: ignore[arg-type]
            environ={
                BatchConcurrencyEnvironment.MAX_PARALLELISM: str(_FANOUT),
                BatchConcurrencyEnvironment.CATALOG: catalog,
            },
        )
        self._test_journal = journal

    def _journal(self) -> Any:
        return self._test_journal


class ComposedRunMixin:
    """Drive a real model turn under an admission the composition root built."""

    @staticmethod
    def context(tool_use: dict[str, dict[str, str]] | None) -> AgentRuntimeContext:
        """Return a run context carrying the backend's stored tool-use policy.

        ``None`` means the aggregate wrote nothing, which
        :class:`ToolUsePolicyResolver` fails *open* to the deployment default —
        ``read=auto`` / ``write=ask`` / ``destructive=require``. That default is
        not a neutral case here: it gates the MCP umbrella, so it is the posture
        the overwhelming majority of deployments actually run under.
        """

        return AgentRuntimeContext(
            user_id="user-bug19",
            org_id=_ORG,
            roles=["employee"],
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
            run_id="run-w3",
            trace_id=_TRACE,
            user_policies_json=({} if tool_use is None else {"tool_use": tool_use}),
        )

    @classmethod
    def compose(
        cls,
        journal: _InMemoryBatchJournal,
        *,
        tool_use: dict[str, dict[str, str]] | None,
        catalog: str = _CATALOG,
    ) -> Any:
        """Compose exactly what both worker handlers compose, and nothing else."""

        return _RecordingComposer(journal, catalog=catalog).compose(
            org_id=_ORG,
            trace_id=_TRACE,
            control=_binding(),
            runtime_context=cls.context(tool_use),
        )

    @staticmethod
    def graph(probe: _OverlapProbe) -> Any:
        """Build the same real graph W3's proofs drive, over the MCP umbrella."""

        return create_agent(
            model=_McpFanoutModel(),
            tools=[
                StructuredTool.from_function(
                    name=_UMBRELLA,
                    description="Call one tool on one MCP server.",
                    coroutine=probe.observe,
                )
            ],
            middleware=[RuntimeToolControlMiddleware()],
        )

    @classmethod
    async def run(
        cls,
        *,
        tool_use: dict[str, dict[str, str]] | None,
        catalog: str = _CATALOG,
    ) -> tuple[_OverlapProbe, _InMemoryBatchJournal, Any]:
        """Install the composed admission the way the handlers do, then run."""

        journal = _InMemoryBatchJournal()
        admission = cls.compose(journal, tool_use=tool_use, catalog=catalog)
        assert admission is not None, "the composition root refused to compose"
        probe = _OverlapProbe()
        token = RunControlContext.bind_for_run(_binding())
        serial_admission = RunControlContext.serial_admission()
        assert serial_admission is not None
        serial_admission.install_parallel_admission(admission)
        batch_token = RuntimeBatchAdmissionContext.install(admission)
        try:
            await cls.graph(probe).ainvoke(
                {"messages": [HumanMessage(content="Search three repositories.")]}
            )
        finally:
            RuntimeBatchAdmissionContext.reset(batch_token)
            RunControlContext.unbind(token)
        return probe, journal, admission

    @staticmethod
    def segment_modes(journal: _InMemoryBatchJournal) -> set[BatchSegmentMode]:
        assert journal.plans, "the turn was never planned"
        return {segment.mode for segment in journal.plans[0].record.segments}


class TestTheAuthorityDecidesWhetherAnMcpReadOverlaps(ComposedRunMixin):
    """The pair that closes BUG-19.

    Same catalog, same turn, same composition root. The only thing that differs
    is the run's tool-use policy — the authority BUG-15 named and nothing
    passed — and it decides the outcome in both directions.
    """

    async def test_a_gated_read_is_unplannable_despite_a_parallel_safe_catalog(
        self,
    ) -> None:
        """``write=ask`` gates the umbrella, so the whole turn stays serial.

        This is the exact adversarial case: the catalog declares a read that is
        parallel-safe and never parks, every positive claim an operator can
        make, and it is not a lie — the author simply could not see that this
        deployment asks a human before every connector call.
        """

        probe, journal, admission = await self.run(
            tool_use={"workspace": {"write": "ask"}}
        )

        assert journal.plans == [], "an approval-gated turn was planned"
        assert journal.transitions == []
        assert admission.tracked_children == 0
        assert probe.maximum_active == 1, (
            "approval-gated reads overlapped, which is three simultaneous human "
            f"decisions where the serial path opens one: {probe.maximum_active}"
        )

    async def test_the_same_catalog_still_overlaps_when_the_policy_permits_it(
        self,
    ) -> None:
        """``write=auto`` adds nothing, so the catalog's own claim survives.

        Without this half the test above proves only that something became more
        serial, which switching F6 off would also achieve. With it, the two runs
        differ in exactly one input and disagree about exactly one outcome.
        """

        probe, journal, _ = await self.run(tool_use={"workspace": {"write": "auto"}})

        assert self.segment_modes(journal) == {BatchSegmentMode.PARALLEL}
        assert probe.maximum_active == _FANOUT, (
            "a permitted parallel-safe read did not overlap, so the wiring "
            f"narrowed something it had no authority to narrow: {probe.maximum_active}"
        )

    async def test_the_unconfigured_policy_lane_gates_the_umbrella(self) -> None:
        """The deployment default is ``write=ask``, and it reaches the seam.

        A deployment that has never opened the tool-use settings is not a
        deployment with no policy: the resolver fails open to the shipped
        default, which gates every connector call. So the common production run
        lands on the serial side of the pair above without anyone configuring
        anything, which is the blast radius this lane actually has.
        """

        probe, journal, _ = await self.run(tool_use=None)

        assert journal.plans == []
        assert probe.maximum_active == 1

    async def test_a_user_override_narrows_a_permissive_workspace(self) -> None:
        """Both halves of the snapshot reach the seam, not just the workspace."""

        probe, journal, _ = await self.run(
            tool_use={"workspace": {"write": "auto"}, "user": {"write": "require"}}
        )

        assert journal.plans == []
        assert probe.maximum_active == 1


class TestTheAuthorityIsAlwaysPassed(ComposedRunMixin):
    """The state BUG-19 found must be unreachable, not merely repaired."""

    def test_a_composed_admission_always_carries_an_approval_authority(self) -> None:
        """There is no branch of ``_compose`` that yields ``approvals=None``."""

        admission = self.compose(
            _InMemoryBatchJournal(),
            tool_use={"workspace": {"write": "auto"}},
        )

        assert admission is not None
        source = admission._approvals  # noqa: SLF001 - the wiring is under test.
        assert isinstance(source, ToolUsePolicyApprovalSource)
        assert isinstance(source, GraphApprovalRequirementSource)
        assert source.is_readable

    def test_an_absent_run_context_still_yields_an_authority(self) -> None:
        """A missing context is answered ``UNKNOWN``, never *not declared*.

        Passing no source is strictly weaker than passing a broken one: it
        leaves the catalog's claim untouched. So the conservative state has to
        be carried *inside* an authority that exists.
        """

        admission = _RecordingComposer(
            _InMemoryBatchJournal(), catalog=_CATALOG
        ).compose(
            org_id=_ORG,
            trace_id=_TRACE,
            control=_binding(),
            runtime_context=None,
        )

        assert admission is not None
        source = admission._approvals  # noqa: SLF001 - the wiring is under test.
        assert isinstance(source, ToolUsePolicyApprovalSource)
        assert not source.is_readable
        assert (
            source.approval_requirement_for(tool_name=_UMBRELLA, arguments={})
            is ApprovalRequirement.UNKNOWN
        )

    async def test_an_absent_run_context_plans_nothing(self) -> None:
        """And that ``UNKNOWN`` is load-bearing, not decorative."""

        journal = _InMemoryBatchJournal()
        admission = _RecordingComposer(journal, catalog=_CATALOG).compose(
            org_id=_ORG,
            trace_id=_TRACE,
            control=_binding(),
            runtime_context=None,
        )
        assert admission is not None
        await admission.aplan_model_batch(
            execution_scope="supervisor",
            model_turn=1,
            tool_calls=[
                {
                    "name": _UMBRELLA,
                    "args": {"server_name": _SERVER, "tool_name": _INNER_TOOL},
                    "id": f"call-{index}",
                }
                for index in range(_FANOUT)
            ],
        )

        assert journal.plans == []
        assert admission.tracked_children == 0

    def test_the_only_construction_site_in_the_service_names_the_authority(
        self,
    ) -> None:
        """BUG-19 restated as the invariant that makes it unrepeatable.

        ``approvals`` has a default of ``None`` and must keep it: BUG-15's own
        proofs pass ``None`` deliberately, to establish that an absent source
        leaves the catalog untouched. So the guarantee cannot live on the
        constructor's signature. It lives here instead — there is exactly one
        place in ``src/`` that builds a :class:`RunBatchAdmission`, and that
        place names an authority. A second construction site is the regression
        to catch, because it is the only way the keyword can go missing again.
        """

        sites = sorted(
            path
            for path in _SOURCE_ROOT.rglob("*.py")
            if "RunBatchAdmission(" in path.read_text()
        )

        assert [path.name for path in sites] == ["batch_concurrency_composition.py"]
        assert "approvals=self._approvals(" in sites[0].read_text()


class TestTheAuthorityAnswersConservativelyWhenItCannotRead(ComposedRunMixin):
    """Unknown means the conservative outcome, structurally."""

    @staticmethod
    def _source(mode: str) -> ToolUsePolicyApprovalSource:
        return ToolUsePolicyApprovalSource(
            ToolUsePolicySnapshot.from_response(workspace={"write": mode})
        )

    def test_no_snapshot_is_unknown_for_every_tool(self) -> None:
        source = ToolUsePolicyApprovalSource(None)

        for tool_name in (_UMBRELLA, "observed_tool", "write_file"):
            assert (
                source.approval_requirement_for(tool_name=tool_name, arguments={})
                is ApprovalRequirement.UNKNOWN
            )

    def test_an_unnamed_tool_is_unknown(self) -> None:
        """A blank name cannot be classified, so it cannot be cleared either."""

        for tool_name in ("", "   "):
            assert (
                self._source("auto").approval_requirement_for(
                    tool_name=tool_name, arguments={}
                )
                is ApprovalRequirement.UNKNOWN
            )

    def test_a_snapshot_that_raises_is_unknown(self) -> None:
        """A source that was asked and failed answers the floor, never silence."""

        class _BrokenSnapshot:
            def mode_for_kind(self, kind: object) -> object:
                raise RuntimeError("policy lane unavailable")

        source = ToolUsePolicyApprovalSource(_BrokenSnapshot())  # type: ignore[arg-type]

        assert (
            source.approval_requirement_for(tool_name=_UMBRELLA, arguments={})
            is ApprovalRequirement.UNKNOWN
        )

    def test_the_resolver_reads_this_source_as_a_declaration(self) -> None:
        """The two halves compose: a wired source is never *not declared*."""

        assert (
            GraphApprovalRequirementResolver.resolve(
                ToolUsePolicyApprovalSource(None),
                tool_name=_UMBRELLA,
                arguments={},
            )
            is ApprovalRequirement.UNKNOWN
        )
        assert (
            GraphApprovalRequirementResolver.resolve(
                None,
                tool_name=_UMBRELLA,
                arguments={},
            )
            is None
        )


class TestConsultingTheAuthorityOnlyNarrows:
    """It may make more work unplannable; it may never make any more parallel."""

    #: The whole answer space of this source, over every policy mode and every
    #: unreadable state. Asserted below rather than assumed, so a member added
    #: to the answer set has to be admitted deliberately.
    _ANSWERS = (
        ApprovalRequirement.ALWAYS,
        ApprovalRequirement.UNKNOWN,
        ApprovalRequirement.NEVER,
    )

    def test_the_source_answers_only_from_the_closed_vocabulary(self) -> None:
        answers = {
            ToolUsePolicyApprovalSource(
                ToolUsePolicySnapshot.from_response(workspace={"write": mode})
            ).approval_requirement_for(tool_name=tool_name, arguments={})
            for mode in ("auto", "ask", "require", "block")
            for tool_name in (_UMBRELLA, "observed_tool")
        }
        answers.add(
            ToolUsePolicyApprovalSource(None).approval_requirement_for(
                tool_name=_UMBRELLA, arguments={}
            )
        )

        assert answers <= set(self._ANSWERS)

    @pytest.mark.parametrize("declared", list(ApprovalRequirement))
    @pytest.mark.parametrize("answer", _ANSWERS)
    def test_the_fold_is_never_wider_than_the_catalog_alone(
        self,
        declared: ApprovalRequirement,
        answer: ApprovalRequirement,
    ) -> None:
        """Over the whole vocabulary, not by example."""

        folded = ApprovalRequirement.narrowest(declared, answer)

        assert folded.rank <= declared.rank

    def test_the_silent_answer_is_the_identity_of_the_fold(self) -> None:
        """``NEVER`` means *this authority adds nothing*, not *it cannot park*.

        The distinction is what keeps a single-axis authority from destroying
        the catalog's own claim on every capability it has nothing to say about.
        """

        identity = ToolUsePolicyApprovalSource.CONTRIBUTES_NO_NARROWING

        for declared in ApprovalRequirement:
            assert ApprovalRequirement.narrowest(declared, identity) is declared


def _loaded_authority_modules(entry_point: str) -> str:
    """Return the approval-authority modules a fresh interpreter loads.

    A subprocess is essential: in-process, some earlier test has already
    imported everything, so "what does this path load" can only be asked of an
    interpreter that has loaded nothing.
    """

    import os

    module = "runtime_worker.batch_approval_authority"
    script = (
        "import sys\n"
        f"{entry_point}\n"
        f"print('yes' if {module!r} in sys.modules else 'no')\n"
    )
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={
            **{
                key: value
                for key, value in os.environ.items()
                if not key.startswith("F6_")
            },
            "PYTHONPATH": os.pathsep.join(path for path in sys.path if path),
        },
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


class TestFeatureOffParity(ComposedRunMixin):
    """An unconfigured deployment does the same work and loads the same modules."""

    def test_an_unconfigured_root_never_loads_the_approval_authority(self) -> None:
        loaded = _loaded_authority_modules(
            "from runtime_worker.batch_concurrency_composition import (\n"
            "    build_batch_concurrency_composer,\n"
            ")\n"
            "assert build_batch_concurrency_composer(\n"
            "    events=object(), snapshots=object(), environ={}\n"
            ") is None"
        )

        assert loaded == "no"

    def test_the_configured_root_does_load_it(self) -> None:
        """The negative above is a real constraint, not a tautology."""

        loaded = _loaded_authority_modules(
            "import runtime_worker.batch_approval_authority  # noqa"
        )

        assert loaded == "yes"

    def test_an_empty_catalog_composes_nothing_whatever_the_policy_says(self) -> None:
        """The three-way agreement is unchanged: no catalog is still no F6."""

        for tool_use in ({"workspace": {"write": "auto"}}, None):
            assert (
                self.compose(_InMemoryBatchJournal(), tool_use=tool_use, catalog="{}")
                is None
            )

    async def test_a_permissive_policy_reproduces_the_pre_bug19_plan(self) -> None:
        """Feature-off parity's other half: the wiring adds no new refusal.

        With the run's tool-use policy silent about a capability, the plan the
        composition root records is the one it recorded before BUG-19 — one
        batch, one parallel segment, ``_FANOUT`` operations.
        """

        _, journal, _ = await self.run(tool_use={"workspace": {"write": "auto"}})

        assert len(journal.plans) == 1
        record = journal.plans[0].record
        assert len(record.segments) == 1
        assert record.segments[0].mode is BatchSegmentMode.PARALLEL
        assert len(record.operations) == _FANOUT

"""Resuming a checkpointed graph that holds MORE THAN ONE pending interrupt.

Regression cover for the packaged-desktop failure where resolving an approval
killed the run instead of resuming it:

    RuntimeError: When there are multiple pending interrupts, you must specify
    the interrupt id when resuming.

The resume carried a bare ``Command(resume=<value>)``, so LangGraph could not
tell which interrupt the decision answered and refused the whole resume — the
user approved an action and lost the run. This is the case that matters most:
the agent proposed several actions at once and the approval gate is the
product's core trust claim.

These tests drive a REAL compiled LangGraph (pinned 1.2.9) rather than a fake
resumer, because a fake resumer cannot reproduce the library-side guard — which
is exactly why the bug reached production.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from typing_extensions import Annotated, TypedDict

from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.execution.runtime import (
    ainvoke_runtime_resume,
    astream_runtime_resume,
    is_native_interrupt_id,
    resume_command,
)


def _merge(left: list | None, right: list | None) -> list:
    return (left or []) + (right or [])


class _State(TypedDict):
    resolved: Annotated[list, _merge]


class TwoInterruptGraphMixin:
    """A graph whose two parallel branches each park on their own interrupt."""

    ALPHA = "alpha"
    BETA = "beta"

    @staticmethod
    def _branch(name: str):
        def _run(_state: _State) -> dict[str, object]:
            decision = interrupt({"ask": name})
            return {"resolved": [f"{name}:{decision}"]}

        return _run

    @classmethod
    def _graph(cls):
        graph = StateGraph(_State)
        graph.add_node(cls.ALPHA, cls._branch(cls.ALPHA))
        graph.add_node(cls.BETA, cls._branch(cls.BETA))
        # Both branches leave START in one superstep, so the checkpoint holds
        # two concurrently-pending interrupts — the production shape.
        graph.add_edge(START, cls.ALPHA)
        graph.add_edge(START, cls.BETA)
        graph.add_edge(cls.ALPHA, END)
        graph.add_edge(cls.BETA, END)
        return graph.compile(checkpointer=InMemorySaver())

    @staticmethod
    def _context(run_id: str) -> AgentRuntimeContext:
        return AgentRuntimeContext(
            user_id="user_resume",
            org_id="org_resume",
            roles=["employee"],
            model_profile={
                "provider": "openai",
                "model_name": "model-x",
                "max_input_tokens": 200_000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
            run_id=run_id,
            trace_id=f"trace_{run_id}",
        )

    class _Harness:
        """Minimal stand-in carrying the two attributes the helpers read."""

        def __init__(self, agent: object, context: AgentRuntimeContext) -> None:
            self.agent = agent
            self.context = context

    @classmethod
    async def _parked_harness(cls, run_id: str) -> tuple[_Harness, list[str]]:
        """Run the graph to its first park; return the harness + pending ids."""
        agent = cls._graph()
        harness = cls._Harness(agent, cls._context(run_id))
        result = await agent.ainvoke(
            {"resolved": []},
            config={"configurable": {"thread_id": run_id}},
        )
        pending = [item.id for item in result["__interrupt__"]]
        assert len(pending) == 2, "fixture must park on two interrupts"
        return harness, pending

    @staticmethod
    def _interrupt_ids(chunks: list[object]) -> list[str]:
        """Collect the distinct interrupt ids still pending across a resumed stream.

        The rich stream normalises every chunk to
        ``{"type", "ns", "data", "interrupts"}``; a re-raised interrupt shows up
        both as ``data["__interrupt__"]`` on an ``updates`` chunk and as
        ``interrupts`` on a ``values`` chunk. Both are the signal the worker's
        ``_is_action_interrupt`` re-parks on, so read both and de-duplicate.
        """
        found: list[str] = []
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            data = chunk.get("data")
            raised = list(chunk.get("interrupts") or ())
            if isinstance(data, dict):
                raised.extend(data.get("__interrupt__") or ())
            for item in raised:
                if item.id not in found:
                    found.append(item.id)
        return found


class TestResumeCommandTargeting:
    """``resume_command`` must only use the map form for real interrupt ids."""

    _REAL_ID = "8b7e810fa2929f54e237a582b3a132da"

    def test_real_interrupt_id_produces_a_resume_map(self) -> None:
        command = resume_command({"decisions": [{"type": "approve"}]}, self._REAL_ID)
        assert command.resume == {self._REAL_ID: {"decisions": [{"type": "approve"}]}}

    def test_missing_id_falls_back_to_bare_resume(self) -> None:
        payload = {"decisions": [{"type": "approve"}]}
        assert resume_command(payload, None).resume == payload

    @pytest.mark.parametrize(
        "candidate",
        [
            "interrupt:run_1:0",  # the stream mapper's synthetic fallback id
            "8B7E810FA2929F54E237A582B3A132DA",  # uppercase is not a digest
            "8b7e810fa2929f54e237a582b3a132d",  # 31 chars
            "8b7e810fa2929f54e237a582b3a132dax",  # 33 chars
            "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",  # non-hex
        ],
    )
    def test_synthetic_ids_never_build_a_map(self, candidate: str) -> None:
        # Wrapping a non-digest id would make LangGraph treat the wrapper dict
        # as the literal resume VALUE, silently corrupting the payload handed
        # to the graph instead of targeting an interrupt.
        assert is_native_interrupt_id(candidate) is False
        payload = {"decisions": [{"type": "approve"}]}
        assert resume_command(payload, candidate).resume == payload

    def test_predicate_matches_the_pinned_langgraph_implementation(self) -> None:
        # The map-vs-value decision lives in LangGraph. If the pinned library
        # ever changes that predicate, this fails loudly rather than letting a
        # targeted resume degrade into a corrupted payload.
        from langgraph.pregel._utils import is_xxh3_128_hexdigest

        probes = [
            self._REAL_ID,
            self._REAL_ID.upper(),
            "interrupt:run_1:0",
            "0" * 32,
            "g" * 32,
            "0" * 31,
            "0" * 33,
            "",
        ]
        assert [is_native_interrupt_id(p) for p in probes] == [
            is_xxh3_128_hexdigest(p) for p in probes
        ]


class TestBareResumeReproducesTheProductionFailure(TwoInterruptGraphMixin):
    async def test_untargeted_resume_is_refused_by_langgraph(self) -> None:
        harness, _pending = await self._parked_harness("run_bare")

        # No interrupt_id: exactly what the packaged app sent. LangGraph cannot
        # attribute the decision and refuses, which surfaced as
        # ``runtime.resume_stream.failed`` / ``external_service_error``.
        with pytest.raises(Exception) as excinfo:
            async for _ in astream_runtime_resume(
                harness, {"decisions": [{"type": "approve"}]}
            ):
                pass
        assert "multiple pending interrupts" in str(excinfo.value.__cause__)


class TestTargetedResumeKeepsTheRunAlive(TwoInterruptGraphMixin):
    async def test_resolving_one_resumes_it_and_parks_on_the_other(self) -> None:
        harness, pending = await self._parked_harness("run_stream")
        alpha_id, beta_id = pending

        chunks = [
            chunk
            async for chunk in astream_runtime_resume(
                harness,
                {"decisions": [{"type": "approve"}]},
                interrupt_id=alpha_id,
            )
        ]

        # 1. The resume completed instead of failing the run.
        assert chunks, "targeted resume produced no stream output"

        # 2. It resumed the interrupt the user actually decided.
        state = harness.agent.get_state(
            {"configurable": {"thread_id": "run_stream"}}
        ).values
        assert state["resolved"] == ["alpha:{'decisions': [{'type': 'approve'}]}"]

        # 3. The run is still ALIVE and parked on the untouched sibling — the
        #    signal the worker re-parks on (``_is_action_interrupt``).
        assert self._interrupt_ids(chunks) == [beta_id]

    async def test_resolving_the_second_completes_the_run(self) -> None:
        harness, pending = await self._parked_harness("run_finish")
        alpha_id, beta_id = pending
        config = {"configurable": {"thread_id": "run_finish"}}

        async for _ in astream_runtime_resume(
            harness, {"decisions": [{"type": "approve"}]}, interrupt_id=alpha_id
        ):
            pass
        remaining = [
            chunk
            async for chunk in astream_runtime_resume(
                harness, {"decisions": [{"type": "reject"}]}, interrupt_id=beta_id
            )
        ]

        # Both branches ran, each with its OWN decision — no cross-talk, and the
        # graph is fully drained.
        assert sorted(harness.agent.get_state(config).values["resolved"]) == [
            "alpha:{'decisions': [{'type': 'approve'}]}",
            "beta:{'decisions': [{'type': 'reject'}]}",
        ]
        assert self._interrupt_ids(remaining) == []
        assert harness.agent.get_state(config).next == ()

    async def test_interrupt_id_is_stable_across_a_partial_resume(self) -> None:
        # The worker re-parks the run and the sibling's approval card must keep
        # its identity: approval_id/batch_id are derived from the interrupt id,
        # so a shifting id would orphan the pending card.
        harness, pending = await self._parked_harness("run_stable")
        chunks = [
            chunk
            async for chunk in astream_runtime_resume(
                harness, {"decisions": [{"type": "approve"}]}, interrupt_id=pending[0]
            )
        ]
        assert self._interrupt_ids(chunks) == [pending[1]]


class TestNonStreamingResumeTargetsToo(TwoInterruptGraphMixin):
    async def test_ainvoke_resume_accepts_an_interrupt_id(self) -> None:
        # ``astream_runtime_resume`` falls back to this helper for agents with
        # no ``astream``; it must target the interrupt the same way.
        harness, pending = await self._parked_harness("run_invoke")
        result = await ainvoke_runtime_resume(
            harness, {"decisions": [{"type": "approve"}]}, interrupt_id=pending[0]
        )
        assert result["resolved"] == ["alpha:{'decisions': [{'type': 'approve'}]}"]
        assert [item.id for item in result["__interrupt__"]] == [pending[1]]

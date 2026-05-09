"""PR 04 Phase 6 — end-to-end binding journeys.

Wires the real allocator + resolver + persistence binding store
(in-memory adapter) and exercises the four journeys called out in the
PRD's §3.9 test plan. The bug catalog these regress against:

* multi-call of the same tool name → ordinal collisions across runs
* MCP call going through approval → resume re-derived ordinals
  positionally and lost / reused them
* mixed MCP + web_search in one response → ordinal-position fallback
  on the FE collided across tool kinds
* cross-turn citation in turn T+k → re-counting events at next-turn
  build time produced different ordinals than turn T did

Each journey asserts the **universal invariants** specified in the
PRD §3.9:

1. Every emitted ``citation_made`` event carries a non-empty
   ``source_tool_call_id``.
2. Each ``(conversation_ordinal, tool_call_id)`` pair appears exactly
   once in the binding store.
3. Each distinct ``conversation_ordinal`` maps to exactly one
   ``tool_call_id`` for the conversation's lifetime.
4. Cross-turn: the same ``[[N]]`` resolves to the same
   ``tool_call_id`` in turn T+k that the originating turn used.

These are runtime-side invariants. The FE projection + chip↔row
roundtrip are covered by vitest tests in
``apps/frontend/src/features/chat/components/citations/``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from agent_runtime.capabilities.citation_resolver import CitationResolver
from agent_runtime.capabilities.conversation_ordinals import (
    ConversationOrdinalAllocator,
)
from agent_runtime.execution.contracts import StreamEventSource
from runtime_adapters.in_memory.conversation_tool_ordinal_store import (
    InMemoryConversationToolOrdinalStore,
)
from runtime_api.schemas import RuntimeApiEventType


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


class _Values:
    ORG_ID = "org_pr04"
    CONVERSATION_ID = "conv_pr04"
    RUN_ID_T1 = "run_t1"
    RUN_ID_T2 = "run_t2"
    MESSAGE = "msg_assistant"

    class Tools:
        WEB = "web_search"
        CALL_TOOL = "call_tool"
        DISCOVER = "discover_mcp_servers"


@dataclass(frozen=True)
class _StubRun:
    """Resolver only reads ``run_id`` from the record."""

    run_id: str


class _RecordingProducer:
    """In-memory stand-in for ``RuntimeEventProducer``.

    Captures every event the resolver emits so the journey can assert
    on the wire shape without booting the real persistence ports.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def append_api_event(
        self,
        *,
        run: Any,
        source: StreamEventSource,
        event_type: RuntimeApiEventType,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> None:
        self.events.append(
            {
                "run_id": run.run_id,
                "source": source,
                "event_type": event_type,
                "payload": payload,
            }
        )

    def citation_made_links(self) -> list[dict[str, Any]]:
        return [
            event["payload"]["link"]
            for event in self.events
            if event["event_type"] is RuntimeApiEventType.CITATION_MADE
        ]


async def _build_run(
    *,
    run_id: str,
    store: InMemoryConversationToolOrdinalStore,
) -> tuple[ConversationOrdinalAllocator, CitationResolver, _RecordingProducer]:
    """Construct the per-run allocator + resolver + producer trio.

    Mirrors what ``RuntimeRunHandler._bind_conversation_ordinal_allocator``
    + ``_bind_citation_resolver`` do at run start, but without the
    surrounding LangGraph harness.
    """

    allocator = await ConversationOrdinalAllocator.for_conversation(
        org_id=_Values.ORG_ID,
        conversation_id=_Values.CONVERSATION_ID,
        run_id=run_id,
        store=store,
    )
    producer = _RecordingProducer()
    resolver = CitationResolver(
        run=_StubRun(run_id=run_id),
        allocator=allocator,
        producer=producer,
        source=StreamEventSource.MODEL,
    )
    return allocator, resolver, producer


def _assert_universal_invariants(
    *,
    producer: _RecordingProducer,
    store_rows: Sequence[Any],
) -> None:
    """The three invariants every journey must hold (PRD §3.9)."""

    # 1. Every citation_made event has a non-empty source_tool_call_id.
    links = producer.citation_made_links()
    for link in links:
        assert link["source_tool_call_id"], (
            f"citation_made link missing source_tool_call_id: {link}"
        )

    # 2. Each (ordinal, call_id) pair appears exactly once in the store.
    seen_pairs: set[tuple[int, str]] = set()
    for row in store_rows:
        pair = (row.conversation_ordinal, row.tool_call_id)
        assert pair not in seen_pairs, f"duplicate binding {pair}"
        seen_pairs.add(pair)

    # 3. Each ordinal maps to exactly one tool_call_id for the conversation.
    by_ordinal: dict[int, str] = {}
    for row in store_rows:
        existing = by_ordinal.get(row.conversation_ordinal)
        assert existing is None or existing == row.tool_call_id, (
            f"ordinal {row.conversation_ordinal} bound to two call_ids: "
            f"{existing} and {row.tool_call_id}"
        )
        by_ordinal[row.conversation_ordinal] = row.tool_call_id


# --------------------------------------------------------------------------
# Journey C2 — multi-call same tool, no ordinal collision
# --------------------------------------------------------------------------


class TestMultiCallSameToolNoCollision:
    @pytest.mark.asyncio
    async def test_two_web_searches_get_distinct_ordinals(self) -> None:
        store = InMemoryConversationToolOrdinalStore()
        allocator, resolver, producer = await _build_run(
            run_id=_Values.RUN_ID_T1, store=store
        )

        # Two web_search calls fire in sequence — distinct LangGraph
        # call_ids, same tool name. The bug this regresses against:
        # the FE used to fold both into one Sources row because the
        # ordinal-position fallback collapsed them.
        ord_a = await allocator.allocate_for_tool_call(
            tool_call_id="call_web_a", tool_name=_Values.Tools.WEB
        )
        ord_b = await allocator.allocate_for_tool_call(
            tool_call_id="call_web_b", tool_name=_Values.Tools.WEB
        )
        assert (ord_a, ord_b) == (1, 2)
        assert ord_a != ord_b

        # Model cites both: "fact A [[1]] and fact B [[2]]".
        await resolver.observe_delta(
            message_id=_Values.MESSAGE,
            delta_text="fact A [[1]] and fact B [[2]]",
        )

        rows = await store.load(
            org_id=_Values.ORG_ID, conversation_id=_Values.CONVERSATION_ID
        )
        _assert_universal_invariants(producer=producer, store_rows=rows)

        # Each citation_made event resolves to the right call_id.
        links = producer.citation_made_links()
        assert len(links) == 2
        ord_to_call = {
            link["conversation_ordinal"]: link["source_tool_call_id"] for link in links
        }
        assert ord_to_call == {1: "call_web_a", 2: "call_web_b"}


# --------------------------------------------------------------------------
# Journey C4 — MCP w/ approval preserves binding across resume
# --------------------------------------------------------------------------


class TestMcpWithApprovalPreservesBinding:
    @pytest.mark.asyncio
    async def test_resume_restores_allocator_and_idempotent_redispatch(self) -> None:
        # Two-step approval flow:
        #   t0 — initial run binds discover + load ordinals;
        #   t1 — call_tool (linear) attempts, runtime pauses for approval;
        #   t2 — approval resolves; new allocator instance reconstructed
        #         via for_conversation(); call_tool re-dispatches with
        #         the SAME LangGraph call_id (LangGraph idempotency on
        #         resume), and the allocator returns the existing
        #         ordinal instead of allocating again. After resume the
        #         model finishes its response and cites the approved
        #         tool's ordinal.
        store = InMemoryConversationToolOrdinalStore()

        # Pre-pause leg: two tool calls allocate ordinals 1 and 2.
        pre_alloc, _, _ = await _build_run(run_id=_Values.RUN_ID_T1, store=store)
        ord_discover = await pre_alloc.allocate_for_tool_call(
            tool_call_id="call_discover_1",
            tool_name=_Values.Tools.DISCOVER,
        )
        ord_call_tool = await pre_alloc.allocate_for_tool_call(
            tool_call_id="call_linear_list",
            tool_name="linear.list_issues",
        )
        assert (ord_discover, ord_call_tool) == (1, 2)

        # === pause point: approval raised, allocator unbound ===

        # Resume leg: a brand-new allocator instance is reconstructed
        # via for_conversation (mirrors RuntimeApprovalHandler).
        resume_alloc, resume_resolver, resume_producer = await _build_run(
            run_id=_Values.RUN_ID_T1, store=store
        )

        # Counter restored to 2; map populated from the persisted rows.
        assert resume_alloc.last_allocated == 2
        assert resume_alloc.tool_call_id_for(1) == "call_discover_1"
        assert resume_alloc.tool_call_id_for(2) == "call_linear_list"

        # LangGraph re-dispatches call_tool with the same call_id on
        # resume — the allocator must return the existing ordinal.
        replayed = await resume_alloc.allocate_for_tool_call(
            tool_call_id="call_linear_list",
            tool_name="linear.list_issues",
        )
        assert replayed == 2

        # A new tool call after resume gets ordinal 3 (strictly greater
        # than any pre-pause allocation; no reuse).
        ord_new = await resume_alloc.allocate_for_tool_call(
            tool_call_id="call_linear_get_team",
            tool_name="linear.get_team",
        )
        assert ord_new == 3

        # Model resumes streaming with citations to the approved tool.
        await resume_resolver.observe_delta(
            message_id=_Values.MESSAGE,
            delta_text="PAR-9 [[2]]; PAR-8 [[2]]; team [[3]].",
        )

        rows = await store.load(
            org_id=_Values.ORG_ID, conversation_id=_Values.CONVERSATION_ID
        )
        _assert_universal_invariants(producer=resume_producer, store_rows=rows)

        # Three distinct bindings, no duplicates.
        assert len(rows) == 3
        ords = sorted(row.conversation_ordinal for row in rows)
        assert ords == [1, 2, 3]
        # And the citations resolve to the bound call_ids.
        links = resume_producer.citation_made_links()
        assert {link["source_tool_call_id"] for link in links} == {
            "call_linear_list",
            "call_linear_get_team",
        }


# --------------------------------------------------------------------------
# Journey C5 — mixed MCP + web in one response
# --------------------------------------------------------------------------


class TestMixedMcpAndWebInOneResponse:
    @pytest.mark.asyncio
    async def test_distinct_call_ids_resolve_to_distinct_ordinals(self) -> None:
        # Single turn calls a web_search AND an MCP linear tool. The bug
        # this regresses against: under the FE's ordinal-position
        # fallback, mixed kinds in document order could collide on the
        # synthetic citation_id, sending clicks on the web row to the
        # MCP chip and vice versa. With binding-store sourcing the two
        # always have distinct (ord, call_id) pairs.
        store = InMemoryConversationToolOrdinalStore()
        allocator, resolver, producer = await _build_run(
            run_id=_Values.RUN_ID_T1, store=store
        )

        ord_web = await allocator.allocate_for_tool_call(
            tool_call_id="call_web_recent",
            tool_name=_Values.Tools.WEB,
        )
        ord_mcp = await allocator.allocate_for_tool_call(
            tool_call_id="call_linear_status",
            tool_name="linear.list_issues",
        )

        await resolver.observe_delta(
            message_id=_Values.MESSAGE,
            delta_text=(
                "Per the public status page [[1]] and our internal Linear "
                "queue [[2]], the rollout is on track."
            ),
        )

        rows = await store.load(
            org_id=_Values.ORG_ID, conversation_id=_Values.CONVERSATION_ID
        )
        _assert_universal_invariants(producer=producer, store_rows=rows)

        links = producer.citation_made_links()
        assert len(links) == 2
        ord_to_call = {
            link["conversation_ordinal"]: link["source_tool_call_id"] for link in links
        }
        # Each ordinal lands on exactly the right call_id — no
        # cross-kind collision.
        assert ord_to_call == {
            ord_web: "call_web_recent",
            ord_mcp: "call_linear_status",
        }


# --------------------------------------------------------------------------
# Journey C6 — cross-turn resolves to same tool_call_id
# --------------------------------------------------------------------------


class TestCrossTurnResolvesToSameToolCallId:
    @pytest.mark.asyncio
    async def test_turn_t_plus_1_cite_resolves_to_turn_t_call_id(self) -> None:
        # Turn 1: tool A fires. Turn 2: model in a *new* run cites the
        # same ordinal to refer to the prior turn's tool — this is how
        # cross-turn citation is supposed to work, sourced by the
        # ToolObservationIndexBuilder which emits a "cite as [[N]]"
        # hint in the prior-observations prompt context. The invariant
        # tested here: the resolver in turn 2 stamps the same
        # ``source_tool_call_id`` that the originating turn 1
        # allocation recorded.
        store = InMemoryConversationToolOrdinalStore()

        # Turn 1: bind one tool call, model cites it once.
        t1_alloc, t1_resolver, t1_producer = await _build_run(
            run_id=_Values.RUN_ID_T1, store=store
        )
        await t1_alloc.allocate_for_tool_call(
            tool_call_id="call_linear_t1",
            tool_name="linear.list_issues",
        )
        await t1_resolver.observe_delta(
            message_id="msg_t1", delta_text="Linear shows [[1]]."
        )
        t1_links = t1_producer.citation_made_links()
        assert len(t1_links) == 1
        assert t1_links[0]["source_tool_call_id"] == "call_linear_t1"

        # Turn 2: brand-new run on the same conversation. The new
        # allocator restores from the binding store; the resolver in
        # this run can resolve [[1]] to the same call_id without the
        # tool firing again.
        _t2_alloc, t2_resolver, t2_producer = await _build_run(
            run_id=_Values.RUN_ID_T2, store=store
        )
        await t2_resolver.observe_delta(
            message_id="msg_t2",
            delta_text="As I noted last turn, PAR-9 is high priority [[1]].",
        )

        rows = await store.load(
            org_id=_Values.ORG_ID, conversation_id=_Values.CONVERSATION_ID
        )
        _assert_universal_invariants(producer=t2_producer, store_rows=rows)

        t2_links = t2_producer.citation_made_links()
        assert len(t2_links) == 1
        # *The* invariant — same tool_call_id in turn 2 as turn 1.
        assert t2_links[0]["source_tool_call_id"] == "call_linear_t1"
        assert (
            t2_links[0]["source_tool_call_id"] == (t1_links[0]["source_tool_call_id"])
        )

        # And the binding map shows exactly one row across both turns.
        assert len(rows) == 1
        assert rows[0].conversation_ordinal == 1
        assert rows[0].tool_call_id == "call_linear_t1"

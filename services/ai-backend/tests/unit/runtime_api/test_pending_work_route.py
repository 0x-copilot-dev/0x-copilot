"""HTTP route tests for ``GET /v1/agent/pending-work`` (PRD-E2).

Drives the real FastAPI app through ``TestClient``. Pins:

* flag OFF ⇒ the route is NOT mounted (404) — the byte-identical guarantee;
* flag ON ⇒ two runs' pending work (a parked gate + a held stage) aggregate into
  ONE response, each item carrying its run + conversation for jump-to-surface;
* the agent fleet rows are running-first and their pending counts match the items;
* a foreign user's / org's runs are never folded (tenant isolation);
* one un-foldable run degrades to zero items, the response is still 200;
* the candidate scan is bounded by the caps.

The route reads the flag at ``create_router`` time via ``SurfacesV2Flag.enabled()``
(``os.environ``), so ``SURFACES_V2`` is set with ``monkeypatch.setenv`` BEFORE
``create_app``.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.execution.contracts import AgentRuntimeContext, StreamEventSource
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.schemas import (
    AgentRunStatus,
    ConversationRecord,
    RunRecord,
    RuntimeApiEventType,
)

_ORG = "acme"
_USER = "sarah"


def _headers(org: str = _ORG, user: str = _USER) -> dict[str, str]:
    return {"x-enterprise-org-id": org, "x-enterprise-user-id": user}


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


def _run_record(
    *,
    run_id: str,
    conversation_id: str,
    status: AgentRunStatus,
    org: str = _ORG,
    user: str = _USER,
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        conversation_id=conversation_id,
        org_id=org,
        user_id=user,
        user_message_id=f"msg_{run_id}",
        trace_id=f"trace_{run_id}",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=status,
        runtime_context=AgentRuntimeContext(
            user_id=user,
            org_id=org,
            roles=["employee"],
            run_id=run_id,
            trace_id=f"trace_{run_id}",
            model_profile={
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
        ),
    )


def _seed_conversation(
    store: InMemoryRuntimeApiStore,
    *,
    conversation_id: str,
    title: str,
    org: str = _ORG,
    user: str = _USER,
) -> None:
    store.conversations[conversation_id] = ConversationRecord(
        conversation_id=conversation_id,
        org_id=org,
        user_id=user,
        assistant_id="assistant_1",
        title=title,
    )


def _seed_run(
    store: InMemoryRuntimeApiStore,
    *,
    run_id: str,
    conversation_id: str,
    status: AgentRunStatus,
    org: str = _ORG,
    user: str = _USER,
) -> RunRecord:
    run = _run_record(
        run_id=run_id,
        conversation_id=conversation_id,
        status=status,
        org=org,
        user=user,
    )
    store.runs[run_id] = run
    store.events_by_run.setdefault(run_id, [])
    return run


async def _append_gate(store: InMemoryRuntimeApiStore, run: RunRecord) -> None:
    producer = RuntimeEventProducer(persistence=store, event_store=store)
    await producer.append_api_event(
        run=run,
        source=StreamEventSource.SYSTEM,
        event_type=RuntimeApiEventType.GATE_OPENED,
        payload={
            "v": 1,
            "gate_id": f"mcp_auth:{run.run_id}:linear",
            "connector": "linear",
            "purpose": "to read ENG-142",
            "scopes": ["read:issues"],
            "auth_state": "missing",
        },
    )


async def _append_held_stage(store: InMemoryRuntimeApiStore, run: RunRecord) -> None:
    producer = RuntimeEventProducer(persistence=store, event_store=store)
    await producer.append_api_event(
        run=run,
        source=StreamEventSource.SYSTEM,
        event_type=RuntimeApiEventType.WRITE_STAGED,
        payload={
            "v": 1,
            "stage_id": f"stage_{run.run_id}",
            "surface_id": f"surface_{run.run_id}",
            "target": {"connector": "gmail", "op": "send"},
            "proposal_ref": "draft://abcdef0123456789abcdef0123456789/v1",
        },
    )
    await producer.append_api_event(
        run=run,
        source=StreamEventSource.SYSTEM,
        event_type=RuntimeApiEventType.REVISION_ADDED,
        payload={
            "v": 1,
            "stage_id": f"stage_{run.run_id}",
            "rev": 1,
            "author": "agent",
            "diff_ref": "draft://abcdef0123456789abcdef0123456789/v1..v1",
            "proposal_ref": "draft://abcdef0123456789abcdef0123456789/v1",
            "authorship_spans": [],
        },
    )


class _AppBundle:
    def __init__(self, client: TestClient, store: InMemoryRuntimeApiStore) -> None:
        self.client = client
        self.store = store


def _build(monkeypatch, *, flag_on: bool) -> _AppBundle:
    if flag_on:
        monkeypatch.setenv("SURFACES_V2", "true")
    else:
        monkeypatch.delenv("SURFACES_V2", raising=False)
    store = InMemoryRuntimeApiStore()
    ports = RuntimeAdapterFactory.from_store(store)
    app = RuntimeApiAppFactory.create_app(ports=ports, settings=_settings())
    return _AppBundle(TestClient(app), store)


class TestPendingWorkRoute:
    def test_flag_off_route_absent_404(self, monkeypatch) -> None:
        bundle = _build(monkeypatch, flag_on=False)
        resp = bundle.client.get("/v1/agent/pending-work", headers=_headers())
        assert resp.status_code == 404

    def test_flag_on_empty_returns_200_empty(self, monkeypatch) -> None:
        bundle = _build(monkeypatch, flag_on=True)
        resp = bundle.client.get("/v1/agent/pending-work", headers=_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"v": 1, "items": [], "agents": []}

    def test_two_runs_items_aggregate_into_one_response(self, monkeypatch) -> None:
        bundle = _build(monkeypatch, flag_on=True)
        store = bundle.store
        _seed_conversation(store, conversation_id="conv_a", title="Read issue")
        _seed_conversation(store, conversation_id="conv_b", title="Draft reply")
        run_a = _seed_run(
            store,
            run_id="runa0000000000000000000000000000",
            conversation_id="conv_a",
            status=AgentRunStatus.WAITING_FOR_APPROVAL,
        )
        run_b = _seed_run(
            store,
            run_id="runb0000000000000000000000000000",
            conversation_id="conv_b",
            status=AgentRunStatus.COMPLETED,
        )
        asyncio.run(_append_gate(store, run_a))
        asyncio.run(_append_held_stage(store, run_b))

        resp = bundle.client.get("/v1/agent/pending-work", headers=_headers())
        assert resp.status_code == 200
        body = resp.json()

        items = body["items"]
        assert len(items) == 2
        by_kind = {item["item_kind"]: item for item in items}
        gate = by_kind["gate"]
        stage = by_kind["staged_write"]
        assert gate["run_id"] == run_a.run_id
        assert gate["conversation_id"] == "conv_a"
        assert gate["conversation_title"] == "Read issue"
        assert gate["gate_id"] == f"mcp_auth:{run_a.run_id}:linear"
        assert gate["connector"] == "linear"
        assert gate["title"] == "to read ENG-142"
        # The held stage carries its surface_id (the canvas jump target) + run.
        assert stage["run_id"] == run_b.run_id
        assert stage["conversation_id"] == "conv_b"
        assert stage["surface_id"] == f"surface_{run_b.run_id}"
        assert stage["connector"] == "gmail"
        assert stage["op"] == "send"

    def test_agents_rows_running_first_and_pending_counts_match_items(
        self, monkeypatch
    ) -> None:
        bundle = _build(monkeypatch, flag_on=True)
        store = bundle.store
        _seed_conversation(store, conversation_id="conv_a", title="Read issue")
        _seed_conversation(store, conversation_id="conv_b", title="Draft reply")
        run_a = _seed_run(
            store,
            run_id="runa0000000000000000000000000000",
            conversation_id="conv_a",
            status=AgentRunStatus.WAITING_FOR_APPROVAL,
        )
        run_b = _seed_run(
            store,
            run_id="runb0000000000000000000000000000",
            conversation_id="conv_b",
            status=AgentRunStatus.COMPLETED,
        )
        asyncio.run(_append_gate(store, run_a))
        asyncio.run(_append_held_stage(store, run_b))

        body = bundle.client.get("/v1/agent/pending-work", headers=_headers()).json()
        agents = body["agents"]
        assert len(agents) == 2
        # Running-first: the active (waiting_for_approval) run leads the terminal.
        assert agents[0]["run_id"] == run_a.run_id
        assert agents[0]["run_status"] == "waiting_for_approval"
        # Per-run pending counts equal that run's items in the queue.
        counts = {row["run_id"]: row["pending_count"] for row in agents}
        assert counts[run_a.run_id] == 1
        assert counts[run_b.run_id] == 1

    def test_terminal_run_without_pending_absent_from_fleet(self, monkeypatch) -> None:
        bundle = _build(monkeypatch, flag_on=True)
        store = bundle.store
        _seed_conversation(store, conversation_id="conv_c", title="Done")
        # A completed run with NO pending events → no items, no fleet row.
        _seed_run(
            store,
            run_id="runc0000000000000000000000000000",
            conversation_id="conv_c",
            status=AgentRunStatus.COMPLETED,
        )
        body = bundle.client.get("/v1/agent/pending-work", headers=_headers()).json()
        assert body["items"] == []
        assert body["agents"] == []

    def test_foreign_user_runs_excluded(self, monkeypatch) -> None:
        bundle = _build(monkeypatch, flag_on=True)
        store = bundle.store
        # A run owned by a DIFFERENT user in the same org.
        _seed_conversation(
            store, conversation_id="conv_x", title="Theirs", user="marcus"
        )
        run_x = _seed_run(
            store,
            run_id="runx0000000000000000000000000000",
            conversation_id="conv_x",
            status=AgentRunStatus.WAITING_FOR_APPROVAL,
            user="marcus",
        )
        asyncio.run(_append_gate(store, run_x))

        body = bundle.client.get("/v1/agent/pending-work", headers=_headers()).json()
        assert body["items"] == []
        assert body["agents"] == []

    def test_foreign_org_never_visible(self, monkeypatch) -> None:
        bundle = _build(monkeypatch, flag_on=True)
        store = bundle.store
        _seed_conversation(
            store, conversation_id="conv_o", title="Other org", org="globex"
        )
        run_o = _seed_run(
            store,
            run_id="runo0000000000000000000000000000",
            conversation_id="conv_o",
            status=AgentRunStatus.WAITING_FOR_APPROVAL,
            org="globex",
        )
        asyncio.run(_append_gate(store, run_o))

        # Caller is acme/sarah — the globex run must never appear.
        body = bundle.client.get("/v1/agent/pending-work", headers=_headers()).json()
        assert body["items"] == []
        assert body["agents"] == []

    def test_one_bad_run_fold_skipped_response_still_200(self, monkeypatch) -> None:
        bundle = _build(monkeypatch, flag_on=True)
        store = bundle.store
        _seed_conversation(store, conversation_id="conv_a", title="Read issue")
        _seed_conversation(store, conversation_id="conv_bad", title="Broken")
        run_a = _seed_run(
            store,
            run_id="runa0000000000000000000000000000",
            conversation_id="conv_a",
            status=AgentRunStatus.WAITING_FOR_APPROVAL,
        )
        run_bad = _seed_run(
            store,
            run_id="runbad000000000000000000000000000",
            conversation_id="conv_bad",
            status=AgentRunStatus.WAITING_FOR_APPROVAL,
        )
        asyncio.run(_append_gate(store, run_a))

        # Force the bad run's ledger read to raise — the queue must degrade to the
        # good run's items, never 500.
        original = store.list_events_after

        async def _boom(*, org_id: str, run_id: str, after_sequence: int):
            if run_id == run_bad.run_id:
                raise RuntimeError("ledger read blew up")
            return await original(
                org_id=org_id, run_id=run_id, after_sequence=after_sequence
            )

        store.list_events_after = _boom  # type: ignore[method-assign]

        resp = bundle.client.get("/v1/agent/pending-work", headers=_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["run_id"] == run_a.run_id

    def test_caps_bound_candidate_scan(self, monkeypatch) -> None:
        from agent_runtime.surfaces_v2.pending_work import Values

        bundle = _build(monkeypatch, flag_on=True)
        store = bundle.store
        # Seed MORE conversations than the cap, each with a parked gate; only the
        # cap's worth are scanned (older pending work is outside the v0 window).
        total = Values.CAP_CONVERSATIONS + 5
        for i in range(total):
            conv = f"conv_{i:03d}"
            _seed_conversation(store, conversation_id=conv, title=f"C{i}")
            run = _seed_run(
                store,
                run_id=f"run{i:03d}0000000000000000000000000000"[:32],
                conversation_id=conv,
                status=AgentRunStatus.WAITING_FOR_APPROVAL,
            )
            asyncio.run(_append_gate(store, run))

        body = bundle.client.get("/v1/agent/pending-work", headers=_headers()).json()
        assert len(body["items"]) <= Values.CAP_CONVERSATIONS

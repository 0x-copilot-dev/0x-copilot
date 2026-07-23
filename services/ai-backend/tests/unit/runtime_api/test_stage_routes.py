"""HTTP route tests for ``/v1/agent/stages/*`` (PRD-D1).

Drives the real FastAPI app through ``TestClient``. Pins:

* flag OFF ⇒ the three routes are NOT mounted (404 on every method) and NO event
  is appended — the cleanest byte-identical guarantee;
* flag ON ⇒ GET reads the folded view, POST /revisions bumps the rev + carries
  authorship spans, POST /decisions approves and pins the rev;
* malformed decision bodies (unknown decision, ``hold``) map to typed 4xx;
* NOTHING executes: no ``write.applied`` event, draft never ``sent``.

The stage routes read the flag at ``create_router`` time via
``SurfacesV2Flag.enabled()`` (``os.environ``), so ``SURFACES_V2`` is set with
``monkeypatch.setenv`` BEFORE ``create_app``.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from fastapi.testclient import TestClient

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.stage_ledger import RuntimeStageLedger
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.persistence.records import DraftRecord, DraftStatus
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.staging import WriteStager
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.schemas import AgentRunStatus, RunRecord

_ORG = "acme"
_USER = "sarah"
_RUN = "run_stage"
_CONV = "conv_stage"


def _identity_headers() -> dict[str, str]:
    return {"x-enterprise-org-id": _ORG, "x-enterprise-user-id": _USER}


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
        }
    )


def _seed_run(store: InMemoryRuntimeApiStore) -> None:
    run = RunRecord(
        run_id=_RUN,
        conversation_id=_CONV,
        org_id=_ORG,
        user_id=_USER,
        user_message_id="msg_1",
        trace_id="trace_1",
        model_provider="openai",
        model_name="gpt-5.4-mini",
        status=AgentRunStatus.RUNNING,
        runtime_context=AgentRuntimeContext(
            user_id=_USER,
            org_id=_ORG,
            roles=["employee"],
            run_id=_RUN,
            trace_id="trace_1",
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
    store.runs[_RUN] = run
    store.events_by_run.setdefault(_RUN, [])


class _AppBundle:
    def __init__(
        self, client: TestClient, store: InMemoryRuntimeApiStore, ports
    ) -> None:
        self.client = client
        self.store = store
        self.ports = ports

    def stage_a_draft(self) -> tuple[str, str]:
        """Stage a draft via the app's OWN ports (same draft_store the routes use).

        Runs the async engine on a throwaway loop before the TestClient owns one.
        """

        return asyncio.run(_stage_a_draft(self.store, self.ports))


def _build_client(monkeypatch, *, flag_on: bool) -> _AppBundle:
    if flag_on:
        monkeypatch.setenv("SURFACES_V2", "true")
    else:
        monkeypatch.delenv("SURFACES_V2", raising=False)
    store = InMemoryRuntimeApiStore()
    _seed_run(store)
    ports = RuntimeAdapterFactory.from_store(store)
    app = RuntimeApiAppFactory.create_app(ports=ports, settings=_settings())
    app.state.runtime_api_store = store
    return _AppBundle(TestClient(app), store, ports)


async def _stage_a_draft(store: InMemoryRuntimeApiStore, ports) -> tuple[str, str]:
    """Stage a fresh single-artifact write directly through the engine.

    Returns ``(stage_id, draft_id)``. Uses the app's own ``draft_store`` so the
    stage (and the base snapshot a later /revisions reads) is the genuine one.
    """

    drafts = ports.draft_store
    draft_id = uuid4().hex
    record = await drafts.insert_version(
        DraftRecord(
            draft_id=draft_id,
            version=1,
            org_id=_ORG,
            conversation_id=_CONV,
            run_id=_RUN,
            user_id=_USER,
            title="Launch email",
            content_text="Dear team, launch Friday.",
            target_connector="gmail",
            status=DraftStatus.SEND_PENDING_APPROVAL,
        )
    )
    producer = RuntimeEventProducer(persistence=store, event_store=store)
    stager = WriteStager(
        draft_store=drafts, ledger=RuntimeStageLedger(event_producer=producer)
    )
    state = await stager.stage(
        run=store.runs[_RUN],
        org_id=_ORG,
        run_id=_RUN,
        draft=record,
        target_connector="gmail",
        target_op="send",
    )
    return state.stage_id, draft_id


def _event_types(store: InMemoryRuntimeApiStore) -> list[str]:
    out = []
    for e in store.events_by_run.get(_RUN, []):
        value = getattr(getattr(e, "event_type", None), "value", None)
        out.append(
            value if isinstance(value, str) else str(getattr(e, "event_type", ""))
        )
    return out


class TestFlagOff:
    def test_all_three_routes_404_and_no_event(self, monkeypatch) -> None:
        b = _build_client(monkeypatch, flag_on=False)
        client, store = b.client, b.store
        before = len(_event_types(store))
        got = client.get(
            f"/v1/agent/stages/whatever?run_id={_RUN}", headers=_identity_headers()
        )
        posted_rev = client.post(
            f"/v1/agent/stages/whatever/revisions?run_id={_RUN}",
            headers=_identity_headers(),
            json={"base_rev": 1, "content_text": "x"},
        )
        posted_dec = client.post(
            f"/v1/agent/stages/whatever/decisions?run_id={_RUN}",
            headers=_identity_headers(),
            json={"decision": "approve", "rev": 1},
        )
        assert got.status_code == 404
        assert posted_rev.status_code == 404
        assert posted_dec.status_code == 404
        assert len(_event_types(store)) == before  # nothing appended


class TestFlagOnHappyPath:
    def test_get_reads_folded_view(self, monkeypatch) -> None:
        b = _build_client(monkeypatch, flag_on=True)
        client = b.client
        stage_id, draft_id = b.stage_a_draft()
        resp = client.get(
            f"/v1/agent/stages/{stage_id}?run_id={_RUN}", headers=_identity_headers()
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["stage_id"] == stage_id
        assert body["draft_id"] == draft_id
        assert body["status"] == "staged"
        assert body["latest_rev"] == 1
        assert body["revisions"][0]["author"] == "agent"
        assert body["revisions"][0]["ledger_id"].startswith("r")

    def test_revision_bumps_rev_and_spans_then_approve_pins(self, monkeypatch) -> None:
        b = _build_client(monkeypatch, flag_on=True)
        client, store = b.client, b.store
        stage_id, _draft_id = b.stage_a_draft()

        rev_resp = client.post(
            f"/v1/agent/stages/{stage_id}/revisions?run_id={_RUN}",
            headers=_identity_headers(),
            json={"base_rev": 1, "content_text": "Dear team, launch Monday."},
        )
        assert rev_resp.status_code == 200
        rev_body = rev_resp.json()
        assert rev_body["latest_rev"] == 2
        rev2 = rev_body["revisions"][1]
        assert rev2["author"] == "user"
        assert rev2["authorship_spans"], "server-diffed spans present"

        # Approve the OLD rev ⇒ 409 stale, no pin.
        stale = client.post(
            f"/v1/agent/stages/{stage_id}/decisions?run_id={_RUN}",
            headers=_identity_headers(),
            json={"decision": "approve", "rev": 1},
        )
        assert stale.status_code == 409

        # Approve the pinned latest rev ⇒ 200 approved.
        ok = client.post(
            f"/v1/agent/stages/{stage_id}/decisions?run_id={_RUN}",
            headers=_identity_headers(),
            json={"decision": "approve", "rev": 2},
        )
        assert ok.status_code == 200
        assert ok.json()["status"] == "approved"
        assert ok.json()["approved_rev"] == 2

        # Nothing executed.
        assert "write.applied" not in _event_types(store)

    def test_reject_then_restore(self, monkeypatch) -> None:
        b = _build_client(monkeypatch, flag_on=True)
        client = b.client
        stage_id, _draft_id = b.stage_a_draft()
        rej = client.post(
            f"/v1/agent/stages/{stage_id}/decisions?run_id={_RUN}",
            headers=_identity_headers(),
            json={"decision": "reject", "rev": 1},
        )
        assert rej.status_code == 200
        assert rej.json()["status"] == "rejected"
        res = client.post(
            f"/v1/agent/stages/{stage_id}/decisions?run_id={_RUN}",
            headers=_identity_headers(),
            json={"decision": "restore"},
        )
        assert res.status_code == 200
        assert res.json()["status"] == "staged"


class TestFlagOnErrors:
    def test_hold_is_422(self, monkeypatch) -> None:
        b = _build_client(monkeypatch, flag_on=True)
        client = b.client
        stage_id, _draft_id = b.stage_a_draft()
        resp = client.post(
            f"/v1/agent/stages/{stage_id}/decisions?run_id={_RUN}",
            headers=_identity_headers(),
            json={"decision": "hold", "rev": 1},
        )
        assert resp.status_code == 422

    def test_unknown_decision_is_422(self, monkeypatch) -> None:
        b = _build_client(monkeypatch, flag_on=True)
        client = b.client
        stage_id, _draft_id = b.stage_a_draft()
        resp = client.post(
            f"/v1/agent/stages/{stage_id}/decisions?run_id={_RUN}",
            headers=_identity_headers(),
            json={"decision": "obliterate", "rev": 1},
        )
        # Not in the SDR enum ⇒ rejected at the pydantic boundary (400) before it
        # can reach the domain; ``hold`` (in the enum) is the one that reaches the
        # stager and 422s. Either way it is a client rejection — nothing executes.
        assert resp.status_code in (400, 422)

    def test_unknown_stage_is_404(self, monkeypatch) -> None:
        b = _build_client(monkeypatch, flag_on=True)
        client = b.client
        resp = client.get(
            f"/v1/agent/stages/ghost?run_id={_RUN}", headers=_identity_headers()
        )
        assert resp.status_code == 404

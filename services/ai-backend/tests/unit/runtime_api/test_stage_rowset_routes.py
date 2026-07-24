"""HTTP route tests for the bulk row-set stage endpoints (PRD-D3).

Drives the real FastAPI app through ``TestClient``. Pins: the decision-body
validator (rev XOR row_keys, non-empty row_keys); ``/decisions`` row-scope
toggles stance; a rev-scoped approve on a row-set 422s; ``/apply`` enqueues
exactly the will-apply set and reads back APPLY_PENDING with rows + counts; a
mismatched apply 409s with no enqueue; flag OFF ⇒ ``/apply`` is 404.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.stage_ledger import RuntimeStageLedger
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.rowset import AgentHold, RowFieldChange, StagedRow
from agent_runtime.surfaces_v2.staging import WriteStager
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.schemas import AgentRunStatus, RunRecord

_ORG = "acme"
_USER = "sarah"
_RUN = "run_stage"
_CONV = "conv_stage"


def _headers() -> dict[str, str]:
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
    store.runs[_RUN] = RunRecord(
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
    store.events_by_run.setdefault(_RUN, [])


class _Bundle:
    def __init__(self, client, store, ports) -> None:  # noqa: ANN001
        self.client = client
        self.store = store
        self.ports = ports

    def stage_rowset(self, *, holds=()) -> str:  # noqa: ANN001
        return asyncio.run(_stage_rowset(self.store, self.ports, holds))


def _build_client(monkeypatch, *, flag_on: bool) -> _Bundle:
    if flag_on:
        monkeypatch.setenv("SURFACES_V2", "true")
    else:
        # E3: SURFACES_V2 defaults ON, so flag-off (route-absence) is the explicit
        # kill switch — a bare delenv would now register the routes.
        monkeypatch.setenv("SURFACES_V2", "false")
    store = InMemoryRuntimeApiStore()
    _seed_run(store)
    ports = RuntimeAdapterFactory.from_store(store)
    app = RuntimeApiAppFactory.create_app(ports=ports, settings=_settings())
    app.state.runtime_api_store = store
    return _Bundle(TestClient(app), store, ports)


async def _stage_rowset(store, ports, holds) -> str:  # noqa: ANN001
    producer = RuntimeEventProducer(persistence=store, event_store=store)
    stager = WriteStager(
        draft_store=ports.draft_store,
        ledger=RuntimeStageLedger(event_producer=producer),
    )
    rows = tuple(
        StagedRow(
            row_key=f"row{i}",
            title=f"Issue {i}",
            target_args={"id": f"row{i}", "priority": 2},
            changes=(RowFieldChange(field="priority", old=1, new=2),),
        )
        for i in range(3)
    )
    state = await stager.stage_rowset(
        run=store.runs[_RUN],
        org_id=_ORG,
        run_id=_RUN,
        target_connector="linear",
        target_op="update_issue",
        rows=rows,
        agent_holds=holds,
        title="Reprioritize",
    )
    return state.stage_id


def _url(stage_id: str, suffix: str) -> str:
    return f"/v1/agent/stages/{stage_id}{suffix}?run_id={_RUN}"


class TestFlagOff:
    def test_apply_route_404_when_flag_off(self, monkeypatch) -> None:
        b = _build_client(monkeypatch, flag_on=False)
        res = b.client.post(
            _url("nope", "/apply"),
            headers=_headers(),
            json={"rev": 1, "row_keys": ["row0"]},
        )
        assert res.status_code == 404


class TestDecisionValidator:
    # The app maps request-body validation (pydantic model_validator) to a 4xx
    # before the domain runs — no ledger event, nothing executes.
    def test_both_rev_and_row_keys_rejected(self, monkeypatch) -> None:
        b = _build_client(monkeypatch, flag_on=True)
        stage_id = b.stage_rowset()
        res = b.client.post(
            _url(stage_id, "/decisions"),
            headers=_headers(),
            json={"decision": "approve", "rev": 1, "row_keys": ["row0"]},
        )
        assert res.status_code in (400, 422)

    def test_empty_row_keys_rejected(self, monkeypatch) -> None:
        b = _build_client(monkeypatch, flag_on=True)
        stage_id = b.stage_rowset()
        res = b.client.post(
            _url(stage_id, "/decisions"),
            headers=_headers(),
            json={"decision": "hold", "row_keys": []},
        )
        assert res.status_code in (400, 422)


class TestRowsetRoutes:
    def test_get_view_carries_rows_and_counts(self, monkeypatch) -> None:
        b = _build_client(monkeypatch, flag_on=True)
        stage_id = b.stage_rowset(holds=(AgentHold(row_key="row1", reason="risky"),))
        res = b.client.get(_url(stage_id, ""), headers=_headers())
        assert res.status_code == 200
        body = res.json()
        assert len(body["rows"]) == 3
        assert body["row_counts"] == {
            "total": 3,
            "will_apply": 2,
            "held": 1,
            "applied": 0,
            "failed": 0,
        }
        held = next(r for r in body["rows"] if r["row_key"] == "row1")
        assert held["stance"] == "held"
        assert held["agent_hold_reason"] == "risky"
        # target_args is server-only — never surfaced on the wire.
        assert "target_args" not in held

    def test_row_decision_toggles_stance(self, monkeypatch) -> None:
        b = _build_client(monkeypatch, flag_on=True)
        stage_id = b.stage_rowset()
        res = b.client.post(
            _url(stage_id, "/decisions"),
            headers=_headers(),
            json={"decision": "hold", "row_keys": ["row0"]},
        )
        assert res.status_code == 200
        row0 = next(r for r in res.json()["rows"] if r["row_key"] == "row0")
        assert row0["stance"] == "held"

    def test_rev_scoped_approve_on_rowset_422(self, monkeypatch) -> None:
        b = _build_client(monkeypatch, flag_on=True)
        stage_id = b.stage_rowset()
        res = b.client.post(
            _url(stage_id, "/decisions"),
            headers=_headers(),
            json={"decision": "approve", "rev": 1},
        )
        assert res.status_code == 422

    def test_apply_happy_path_enqueues_and_reads_pending(self, monkeypatch) -> None:
        b = _build_client(monkeypatch, flag_on=True)
        stage_id = b.stage_rowset()
        res = b.client.post(
            _url(stage_id, "/apply"),
            headers=_headers(),
            json={"rev": 1, "row_keys": ["row0", "row1", "row2"]},
        )
        assert res.status_code == 200
        assert res.json()["status"] == "apply_pending"
        # The apply enqueued exactly one commit command with the approved set.
        assert len(b.store.stage_commit_commands) == 1
        cmd = b.store.stage_commit_commands[0]
        assert set(cmd.row_keys) == {"row0", "row1", "row2"}

    def test_apply_mismatch_409_no_enqueue(self, monkeypatch) -> None:
        b = _build_client(monkeypatch, flag_on=True)
        stage_id = b.stage_rowset(holds=(AgentHold(row_key="row2", reason="x"),))
        res = b.client.post(
            _url(stage_id, "/apply"),
            headers=_headers(),
            json={"rev": 1, "row_keys": ["row0", "row1", "row2"]},  # includes held
        )
        assert res.status_code == 409
        assert b.store.stage_commit_commands == []

    def test_apply_unknown_row_404(self, monkeypatch) -> None:
        b = _build_client(monkeypatch, flag_on=True)
        stage_id = b.stage_rowset()
        res = b.client.post(
            _url(stage_id, "/apply"),
            headers=_headers(),
            json={"rev": 1, "row_keys": ["row0", "row1", "ghost"]},
        )
        assert res.status_code in (404, 409)
        assert b.store.stage_commit_commands == []

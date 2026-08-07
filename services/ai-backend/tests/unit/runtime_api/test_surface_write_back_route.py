"""``POST /v1/agent/surfaces/{surface_id}/write-back`` — the route, wired.

Drives the real FastAPI app through ``TestClient``, which is what makes these
tests worth having: the route was registered but its coordinator was never bound
to ``app.state``, so the endpoint existed and could not work. The first test
below is exactly that — the app must COMPOSE the coordinator at boot.

The rest pin the two things a save-shaped endpoint must never get wrong:

* it **stages and stops** — a 200 leaves ``status: staged`` and the run's commit
  queue empty, and ``/stages/{id}/apply`` remains the only door to execution;
* it **fails out loud** — an unconfigured deployment answers 503 with a message
  an operator can act on, never a 500 and never a quiet 200.

``target_args`` must not appear on the wire in either direction: the client
renders diffs and re-sends only ``row_key``.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.stage_ledger import RuntimeStageLedger
from agent_runtime.capabilities.surfaces.generator import SpecCompletionResult
from agent_runtime.capabilities.surfaces.write_back import SurfaceWriteBackCoordinator
from agent_runtime.capabilities.surfaces.write_mapping import WriteOpCandidate
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.constants import Keys, Values
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.schemas import AgentRunStatus, RunRecord

_ORG = "acme"
_USER = "sarah"
_OTHER = "marcus"
_RUN = "run_writeback"
_CONV = "conv_writeback"
_SURFACE = "surface_issues"
_CONNECTOR = "linear"
_WRITE_OP = "update_issue"
_URL = f"/v1/agent/surfaces/{_SURFACE}/write-back"

_HONEST_ANSWER: dict[str, object] = {
    "op": _WRITE_OP,
    "args": [
        {"arg": "id", "source": "row", "key": "id"},
        {"arg": "priority", "source": "edited", "key": "priority"},
    ],
}


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


def _body(*, run_id: str = _RUN) -> dict[str, object]:
    return {
        "run_id": run_id,
        "edits": [
            {
                "row_key": "ISS-1",
                "title": "Ship the thing",
                "row": {"id": "ISS-1", "team": "core", "priority": 1},
                "changes": [{"field": "priority", "old": 1, "new": 3}],
            }
        ],
    }


class _FakeWriteOps:
    async def write_ops(
        self, *, org_id: str, user_id: str, connector: str
    ) -> tuple[WriteOpCandidate, ...]:
        del org_id, user_id, connector
        return (WriteOpCandidate(name=_WRITE_OP, description="Update one issue."),)


class _FakeCompletion:
    def __init__(self, candidate: object = None) -> None:
        self.candidate = candidate if candidate is not None else _HONEST_ANSWER

    async def complete(self, *, system: str, user: str) -> SpecCompletionResult:
        del system, user
        return SpecCompletionResult(candidate=self.candidate, raw_text="")


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


async def _seed_read_surface(store: InMemoryRuntimeApiStore) -> None:
    ledger = RuntimeStageLedger(
        event_producer=RuntimeEventProducer(persistence=store, event_store=store)
    )
    await ledger.emit(
        run=store.runs[_RUN],
        event_type_value=LedgerEventType.SURFACE_CREATED.value,
        payload={
            Keys.Field.V: Values.PAYLOAD_V,
            Keys.Field.SURFACE_ID: _SURFACE,
            Keys.Field.KIND: Values.KIND_TABLE,
            Keys.Field.SOURCE: {
                Keys.Field.CONNECTOR: _CONNECTOR,
                Keys.Field.OP: "list_issues",
            },
            Keys.Field.TITLE: "Issues",
            Keys.Field.PAYLOAD_REF: "call:abc",
        },
        summary=None,
    )


class _Bundle:
    def __init__(self, client: TestClient, store: InMemoryRuntimeApiStore, app) -> None:  # noqa: ANN001
        self.client = client
        self.store = store
        self.app = app


def _build(
    monkeypatch,  # noqa: ANN001
    *,
    flag_on: bool = True,
    wire_write_ops: bool = True,
    wire_completion: bool = True,
) -> _Bundle:
    monkeypatch.setenv("SURFACES_V2", "true" if flag_on else "false")
    store = InMemoryRuntimeApiStore()
    _seed_run(store)
    asyncio.run(_seed_read_surface(store))
    ports = RuntimeAdapterFactory.from_store(store)
    app = RuntimeApiAppFactory.create_app(ports=ports, settings=_settings())
    coordinator = getattr(app.state, "surface_write_back_coordinator", None)
    if coordinator is not None and (wire_write_ops or wire_completion):
        # The catalogue adapter has no production implementation yet, and the
        # completion is the module's declared test seam. Everything else on the
        # coordinator is the app's own composition, untouched.
        app.state.surface_write_back_coordinator = SurfaceWriteBackCoordinator(
            persistence=coordinator.persistence,
            event_store=coordinator.event_store,
            stager=coordinator.stager,
            environ=coordinator.environ,
            write_ops=_FakeWriteOps() if wire_write_ops else None,
            user_policies=coordinator.user_policies,
            completion=_FakeCompletion() if wire_completion else None,
        )
    return _Bundle(TestClient(app), store, app)


class TestTheAppComposesTheCoordinator:
    """The gap this slice existed to close: route mounted, nothing behind it."""

    def test_app_binds_a_coordinator_at_boot(self, monkeypatch) -> None:
        bundle = _build(monkeypatch, wire_write_ops=False, wire_completion=False)

        assert isinstance(
            bundle.app.state.surface_write_back_coordinator,
            SurfaceWriteBackCoordinator,
        )

    def test_bound_coordinator_shares_the_stage_services_stager(
        self, monkeypatch
    ) -> None:
        # One staging authority per run: the lane must write into the same
        # ledger ``/stages/{id}/apply`` reads back.
        bundle = _build(monkeypatch, wire_write_ops=False, wire_completion=False)

        assert (
            bundle.app.state.surface_write_back_coordinator.stager
            is bundle.app.state.stage_service.stager
        )

    def test_bound_coordinator_carries_no_completion_in_production(
        self, monkeypatch
    ) -> None:
        # ``completion is None`` is what forces the shaping ladder to resolve a
        # real model — and to RAISE when it cannot.
        bundle = _build(monkeypatch, wire_write_ops=False, wire_completion=False)

        assert bundle.app.state.surface_write_back_coordinator.completion is None


class TestRouteRegistration:
    def test_route_is_absent_when_the_flag_is_off(self, monkeypatch) -> None:
        bundle = _build(monkeypatch, flag_on=False)

        res = bundle.client.post(_URL, headers=_headers(), json=_body())

        assert res.status_code == 404

    def test_route_exists_when_the_flag_is_on(self, monkeypatch) -> None:
        bundle = _build(monkeypatch)

        res = bundle.client.post(_URL, headers=_headers(), json=_body())

        assert res.status_code == 200


class TestUnconfiguredDeploymentFailsLoud:
    def test_absent_coordinator_is_a_503_with_a_readable_message(
        self, monkeypatch
    ) -> None:
        bundle = _build(monkeypatch)
        bundle.app.state.surface_write_back_coordinator = None

        res = bundle.client.post(_URL, headers=_headers(), json=_body())

        assert res.status_code == 503
        assert res.json()["detail"] == (
            "Surface write-back is not configured for this deployment."
        )

    def test_absent_state_attribute_is_a_503_not_a_500(self, monkeypatch) -> None:
        # ``app.state`` raises AttributeError on a missing name; the route must
        # answer 503 rather than turning a wiring gap into a server error.
        bundle = _build(monkeypatch)
        del bundle.app.state.surface_write_back_coordinator

        res = bundle.client.post(_URL, headers=_headers(), json=_body())

        assert res.status_code == 503

    def test_unwired_write_op_catalogue_is_a_503(self, monkeypatch) -> None:
        # Today's honest production state: the lane is wired, the catalogue
        # adapter is not, and a save says so instead of quietly staging nothing.
        bundle = _build(monkeypatch, wire_write_ops=False)

        res = bundle.client.post(_URL, headers=_headers(), json=_body())

        assert res.status_code == 503
        assert res.json()["detail"] == (
            "The connector's write operations are not available, so this save "
            "cannot be prepared. Nothing was staged."
        )
        assert bundle.store.stage_commit_commands == []


class TestSaveStagesAndStops:
    def test_save_returns_a_staged_write_view(self, monkeypatch) -> None:
        bundle = _build(monkeypatch)

        res = bundle.client.post(_URL, headers=_headers(), json=_body())

        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "staged"
        assert body["run_id"] == _RUN
        assert body["target"] == {"connector": _CONNECTOR, "op": _WRITE_OP}
        assert body["approved_rev"] is None

    def test_save_enqueues_nothing(self, monkeypatch) -> None:
        bundle = _build(monkeypatch)

        bundle.client.post(_URL, headers=_headers(), json=_body())

        assert bundle.store.stage_commit_commands == []

    def test_save_rows_await_a_decision(self, monkeypatch) -> None:
        bundle = _build(monkeypatch)

        body = bundle.client.post(_URL, headers=_headers(), json=_body()).json()

        assert body["row_counts"] == {
            "total": 1,
            "will_apply": 1,
            "held": 0,
            "applied": 0,
            "failed": 0,
        }
        assert body["decisions"] == []

    def test_target_args_never_reach_the_wire(self, monkeypatch) -> None:
        bundle = _build(monkeypatch)

        body = bundle.client.post(_URL, headers=_headers(), json=_body()).json()

        row = body["rows"][0]
        assert "target_args" not in row
        assert row["changes"] == [{"field": "priority", "old": 1, "new": 3}]

    def test_the_staged_write_is_appliable_through_the_apply_route_only(
        self, monkeypatch
    ) -> None:
        # The whole point of staging here: execution needs a SECOND, deliberate
        # gesture against a different endpoint.
        bundle = _build(monkeypatch)
        staged = bundle.client.post(_URL, headers=_headers(), json=_body()).json()
        assert bundle.store.stage_commit_commands == []

        applied = bundle.client.post(
            f"/v1/agent/stages/{staged['stage_id']}/apply?run_id={_RUN}",
            headers=_headers(),
            json={"rev": 1, "row_keys": ["ISS-1"]},
        )

        assert applied.status_code == 200
        assert len(bundle.store.stage_commit_commands) == 1
        assert bundle.store.stage_commit_commands[0].row_keys == ("ISS-1",)


class TestRouteGuards:
    def test_another_users_run_is_404_never_403(self, monkeypatch) -> None:
        bundle = _build(monkeypatch)

        res = bundle.client.post(
            _URL,
            headers={"x-enterprise-org-id": _ORG, "x-enterprise-user-id": _OTHER},
            json=_body(),
        )

        assert res.status_code == 404
        assert res.json()["detail"] == "resource not found"

    def test_unknown_surface_is_404(self, monkeypatch) -> None:
        bundle = _build(monkeypatch)

        res = bundle.client.post(
            "/v1/agent/surfaces/surface_ghost/write-back",
            headers=_headers(),
            json=_body(),
        )

        assert res.status_code == 404
        assert res.json()["detail"] == "resource not found"

    def test_an_invented_value_is_422_and_nothing_is_staged(self, monkeypatch) -> None:
        bundle = _build(monkeypatch, wire_completion=False)
        coordinator = bundle.app.state.surface_write_back_coordinator
        bundle.app.state.surface_write_back_coordinator = SurfaceWriteBackCoordinator(
            persistence=coordinator.persistence,
            event_store=coordinator.event_store,
            stager=coordinator.stager,
            environ=coordinator.environ,
            write_ops=_FakeWriteOps(),
            completion=_FakeCompletion(
                {
                    "op": _WRITE_OP,
                    "args": [
                        *(_HONEST_ANSWER["args"]),
                        {"arg": "note", "source": "literal", "value": "invented"},
                    ],
                }
            ),
        )
        before = len(bundle.store.events_by_run[_RUN])

        res = bundle.client.post(_URL, headers=_headers(), json=_body())

        assert res.status_code == 422
        assert res.json()["detail"] == (
            "The proposed write contains a value that you did not enter and "
            "that was not read from the connector. Nothing was staged."
        )
        assert len(bundle.store.events_by_run[_RUN]) == before

    def test_empty_edit_list_is_rejected_by_the_body_contract(
        self, monkeypatch
    ) -> None:
        bundle = _build(monkeypatch)

        res = bundle.client.post(
            _URL, headers=_headers(), json={"run_id": _RUN, "edits": []}
        )

        assert res.status_code in (400, 422)

    def test_unknown_body_field_is_rejected(self, monkeypatch) -> None:
        bundle = _build(monkeypatch)

        res = bundle.client.post(
            _URL,
            headers=_headers(),
            json={**_body(), "target_connector": "attacker-chosen"},
        )

        assert res.status_code in (400, 422)


@pytest.mark.parametrize("run_id", ["", " "])
def test_blank_run_id_is_rejected(monkeypatch, run_id: str) -> None:
    bundle = _build(monkeypatch)

    res = bundle.client.post(_URL, headers=_headers(), json=_body(run_id=run_id))

    assert res.status_code in (400, 404, 422)

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi.testclient import TestClient

from agent_runtime.api.rowset_effect_review import (
    RowSetEffectReview,
    RowSetReviewAction,
    RowSetReviewCounts,
    RowSetReviewRow,
)
from agent_runtime.settings import RuntimeSettings
from agent_runtime.surfaces_v2.rowset import RowFieldChange
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory

_ORG = "org-rowset-routes"
_USER = "user-rowset-routes"
_RUN = "run-rowset-routes"
_STAGE = "stg_00000000-0000-4000-8000-000000000001"
_PROPOSAL = "a" * 64
_TARGET = "b" * 64


def _review(
    *,
    kind: str = "apply",
    basis_ledger_id: str | None = None,
) -> RowSetEffectReview:
    return RowSetEffectReview(
        stage_id=_STAGE,
        revision=1,
        proposal_digest=_PROPOSAL,
        target_digest=_TARGET,
        title="Reprioritize",
        source_connector="linear",
        source_op="update_issue",
        status="partial" if kind == "retry_failed" else "staged",
        rows=(
            RowSetReviewRow(
                row_key="row-a",
                title="Acme renewal",
                changes=(RowFieldChange(field="priority", old=1, new=2),),
                decision="approve",
                decision_source="default",
                can_decide=kind == "apply",
                apply_outcome="failed" if kind == "retry_failed" else None,
            ),
        ),
        counts=RowSetReviewCounts(
            total=1,
            approved=1,
            held=0,
            applied=0,
            failed=1 if kind == "retry_failed" else 0,
        ),
        action=RowSetReviewAction(
            kind=kind,  # type: ignore[arg-type]
            row_keys=("row-a",),
            basis_sequence_no=4,
            basis_ledger_id=basis_ledger_id,
        ),
        ledger_id=basis_ledger_id or "rtest·004",
        last_sequence_no=4,
    )


@dataclass
class _Service:
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def review(self, **kwargs: object) -> RowSetEffectReview:
        self.calls.append(("review", kwargs))
        return _review()

    async def record_row_decisions(self, **kwargs: object) -> RowSetEffectReview:
        self.calls.append(("record_row_decisions", kwargs))
        return _review()

    async def apply(self, **kwargs: object) -> RowSetEffectReview:
        self.calls.append(("apply", kwargs))
        return _review()

    async def retry(self, **kwargs: object) -> RowSetEffectReview:
        self.calls.append(("retry", kwargs))
        return _review(kind="retry_failed", basis_ledger_id="rtest·004")


def _client(monkeypatch) -> tuple[TestClient, _Service]:  # noqa: ANN001
    monkeypatch.setenv("SURFACES_V2", "true")
    settings = RuntimeSettings.load(
        environ={
            "SURFACES_V2": "true",
            "OPERATION_GATEWAY_MODE": "enforce",
        }
    )
    store = InMemoryRuntimeApiStore()
    ports = RuntimeAdapterFactory.from_store(store, artifact_effects_v2=True)
    app = RuntimeApiAppFactory.create_app(
        ports=ports,
        settings=settings,
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
    )
    service = _Service()
    app.state.rowset_effect_review_service = service
    return TestClient(app), service


def _headers() -> dict[str, str]:
    return {
        "x-enterprise-org-id": _ORG,
        "x-enterprise-user-id": _USER,
    }


def _action_body(*, retry: bool = False) -> dict[str, object]:
    return {
        "revision": 1,
        "proposal_digest": _PROPOSAL,
        "target_digest": _TARGET,
        "row_keys": ["row-a"],
        "basis_sequence_no": 4,
        "basis_ledger_id": "rtest·004" if retry else None,
    }


def test_review_and_row_decisions_are_owner_scoped(monkeypatch) -> None:  # noqa: ANN001
    client, service = _client(monkeypatch)
    review = client.get(
        f"/v1/agent/effect-stages/{_STAGE}/rowset/review?run_id={_RUN}",
        headers=_headers(),
    )
    decision = client.post(
        f"/v1/agent/effect-stages/{_STAGE}/rowset/decisions?run_id={_RUN}",
        headers=_headers(),
        json={
            "revision": 1,
            "proposal_digest": _PROPOSAL,
            "target_digest": _TARGET,
            "decisions": {"row-a": "hold"},
        },
    )

    assert review.status_code == 200
    assert decision.status_code == 200
    assert service.calls == [
        (
            "review",
            {
                "org_id": _ORG,
                "user_id": _USER,
                "run_id": _RUN,
                "stage_id": _STAGE,
            },
        ),
        (
            "record_row_decisions",
            {
                "org_id": _ORG,
                "user_id": _USER,
                "run_id": _RUN,
                "stage_id": _STAGE,
                "revision": 1,
                "proposal_digest": _PROPOSAL,
                "target_digest": _TARGET,
                "decisions": {"row-a": "hold"},
            },
        ),
    ]


def test_apply_and_retry_forward_exact_immutable_scope(monkeypatch) -> None:  # noqa: ANN001
    client, service = _client(monkeypatch)
    apply = client.post(
        f"/v1/agent/effect-stages/{_STAGE}/rowset/apply?run_id={_RUN}",
        headers=_headers(),
        json=_action_body(),
    )
    retry = client.post(
        f"/v1/agent/effect-stages/{_STAGE}/rowset/retry?run_id={_RUN}",
        headers=_headers(),
        json=_action_body(retry=True),
    )

    assert apply.status_code == 200
    assert retry.status_code == 200
    assert service.calls[0] == (
        "apply",
        {
            "org_id": _ORG,
            "user_id": _USER,
            "run_id": _RUN,
            "stage_id": _STAGE,
            "revision": 1,
            "proposal_digest": _PROPOSAL,
            "target_digest": _TARGET,
            "row_keys": ("row-a",),
            "basis_sequence_no": 4,
        },
    )
    assert service.calls[1] == (
        "retry",
        {
            "org_id": _ORG,
            "user_id": _USER,
            "run_id": _RUN,
            "stage_id": _STAGE,
            "revision": 1,
            "proposal_digest": _PROPOSAL,
            "target_digest": _TARGET,
            "row_keys": ("row-a",),
            "basis_sequence_no": 4,
            "basis_ledger_id": "rtest·004",
        },
    )


def test_apply_and_retry_reject_the_wrong_basis_shape(monkeypatch) -> None:  # noqa: ANN001
    client, service = _client(monkeypatch)

    apply = client.post(
        f"/v1/agent/effect-stages/{_STAGE}/rowset/apply?run_id={_RUN}",
        headers=_headers(),
        json=_action_body(retry=True),
    )
    retry = client.post(
        f"/v1/agent/effect-stages/{_STAGE}/rowset/retry?run_id={_RUN}",
        headers=_headers(),
        json=_action_body(),
    )

    assert apply.status_code == 422
    assert retry.status_code == 422
    assert service.calls == []

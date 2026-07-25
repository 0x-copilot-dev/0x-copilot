"""D7/D12 bounded audit-export verification sampler tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from copilot_audit_chain import AuditChainSigner

from agent_runtime.surfaces_v2.audit_export_verification import (
    AuditExportBundleManifest,
    AuditExportVerificationFailureClass,
    AuditExportVerificationOutcome,
)
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from agent_runtime.surfaces_v2.receipt import ReceiptFold
from agent_runtime.surfaces_v2.receipt_export import ReceiptExportBuilder
from agent_runtime.surfaces_v2.receipt_export_v2 import ReceiptExportV2Builder
from runtime_adapters.file.audit_export_verification_store import (
    FileAuditExportVerificationStore,
)
from runtime_adapters.in_memory.audit_export_verification_store import (
    InMemoryAuditExportVerificationStore,
)
from runtime_worker.jobs.audit_export_verification import (
    AuditExportVerificationSampler,
    AuditExportVerificationSamplingRunner,
)


pytestmark = pytest.mark.anyio

ORG = "org_audit_sample"
RUN = "run_audit_sample"
NOW = datetime(2026, 7, 25, 12, tzinfo=UTC)
KEY = b"audit-export-verification-sample-key-material"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class _Event:
    event_type: LedgerEventType | str
    sequence_no: int
    payload: dict[str, object]
    created_at: datetime = NOW


@dataclass
class _Events:
    events: list[_Event]

    async def list_events_after(
        self,
        *,
        org_id: str,
        run_id: str,
        after_sequence: int,
    ) -> tuple[_Event, ...]:
        assert org_id == ORG
        assert run_id == RUN
        return tuple(
            event for event in self.events if event.sequence_no > after_sequence
        )


@dataclass
class _AuditPersistence:
    audit: list[tuple[str, object]] = field(default_factory=list)

    async def write_audit_log(self, *, event_type: str, record: object) -> None:
        self.audit.append((event_type, record))


class _FailingAuditPersistence(_AuditPersistence):
    async def write_audit_log(self, *, event_type: str, record: object) -> None:
        del event_type, record
        raise RuntimeError("durable audit unavailable")


class _FailingOutcomeStore(InMemoryAuditExportVerificationStore):
    async def record_outcome(self, *, record):  # noqa: ANN001
        del record
        raise RuntimeError("durable outcome unavailable")


class _LeaseUnavailableStore(InMemoryAuditExportVerificationStore):
    async def acquire_lease(self, **kwargs):  # noqa: ANN003
        del kwargs
        raise RuntimeError("durable lease unavailable")


class _CursorUnavailableStore(InMemoryAuditExportVerificationStore):
    async def advance_scan_cursor(self, **kwargs):  # noqa: ANN003
        del kwargs
        raise RuntimeError("durable cursor unavailable")


class _Metrics:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record_audit_verification(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


def _events() -> list[_Event]:
    return [
        _Event(
            LedgerEventType.READ_EXECUTED,
            1,
            {
                "v": 1,
                "call_id": "call_audit_sample",
                "connector": "linear",
                "op": "get_issue",
                "latency_ms": 1,
                "payload_ref": "call:call_audit_sample",
            },
        ),
        _Event(
            LedgerEventType.SURFACE_CREATED,
            2,
            {
                "v": 1,
                "surface_id": "record://linear/get_issue/audit-sample",
                "kind": "record",
                "source": {"connector": "linear", "op": "get_issue"},
                "title": "title-with-cookie=session-secret",
                "payload_ref": "/private/path/never-persisted.json",
            },
        ),
    ]


def _signer() -> AuditChainSigner:
    return AuditChainSigner(keys={1: KEY}, active_version=1)


def _v1_manifest(events: list[_Event]) -> AuditExportBundleManifest:
    receipt = ReceiptFold.fold(run_id=RUN, events=events)
    bundle = ReceiptExportBuilder(signer=_signer()).build(
        run_id=RUN,
        events=events,
        receipt=receipt,
    )
    return AuditExportBundleManifest.from_v1_bundle(
        org_id=ORG,
        bundle=bundle.model_dump(mode="json"),
        captured_at=NOW,
    )


def _old_v1_manifest(events: list[_Event]) -> AuditExportBundleManifest:
    """Model the older v1 spelling accepted by the canonical verifier."""

    receipt = ReceiptFold.fold(run_id=RUN, events=events)
    wire = (
        ReceiptExportBuilder(signer=_signer())
        .build(
            run_id=RUN,
            events=events,
            receipt=receipt,
        )
        .model_dump(mode="json")
    )
    wire["bundle_version"] = wire.pop("export_version")
    return AuditExportBundleManifest.from_v1_bundle(
        org_id=ORG,
        bundle=wire,
        captured_at=NOW,
    )


def _v2_manifest(events: list[_Event]) -> AuditExportBundleManifest:
    bundle = ReceiptExportV2Builder(signer=_signer()).build(
        run_id=RUN,
        events=events,
        run_status="completed",
    )
    return AuditExportBundleManifest.from_v2_bundle(
        org_id=ORG,
        bundle=bundle.model_dump(mode="json"),
        captured_at=NOW + timedelta(seconds=1),
    )


def _runner(
    *,
    store: object,
    events: list[_Event],
    event_store: object | None = None,
    persistence: _AuditPersistence | None = None,
    metrics: _Metrics | None = None,
    signer_factory=None,  # noqa: ANN001
    max_samples: int = 25,
) -> tuple[AuditExportVerificationSamplingRunner, _AuditPersistence, _Metrics]:
    sink = persistence or _AuditPersistence()
    lifecycle_metrics = metrics or _Metrics()
    runner = AuditExportVerificationSamplingRunner(
        store=store,  # type: ignore[arg-type]
        sampler=AuditExportVerificationSampler(
            event_store=event_store or _Events(events),  # type: ignore[arg-type]
            signer_factory=signer_factory or _signer,
            metrics=lifecycle_metrics,  # type: ignore[arg-type]
        ),
        persistence=sink,  # type: ignore[arg-type]
        worker_id="audit-export-sample-worker",
        max_samples=max_samples,
        lease_seconds=60,
    )
    return runner, sink, lifecycle_metrics


async def test_samples_existing_v1_and_v2_bundles_without_persisting_raw_body() -> None:
    events = _events()
    store = InMemoryAuditExportVerificationStore()
    v1 = _v1_manifest(events)
    v2 = _v2_manifest(events)
    await store.record_manifest(manifest=v1)
    await store.record_manifest(manifest=v2)
    runner, persistence, metrics = _runner(store=store, events=events)

    result = await runner.run_once(now=NOW + timedelta(minutes=1))

    assert result.sampled == 2
    assert result.verified == 2
    assert result.failed == result.unavailable == 0
    assert len(metrics.calls) == 2
    assert {call["format"] for call in metrics.calls} == {"receipt_v1", "receipt_v2"}
    assert len(persistence.audit) == 2
    rendered = repr(v1.model_dump(mode="json"))
    assert "session-secret" not in rendered
    assert "/private/path" not in rendered
    assert "'payload':" not in rendered


async def test_samples_older_v1_bundle_version_spelling_through_canonical_verifier() -> (
    None
):
    events = _events()
    manifest = _old_v1_manifest(events)
    assert manifest.legacy_version_key == "bundle_version"
    store = InMemoryAuditExportVerificationStore()
    await store.record_manifest(manifest=manifest)
    runner, _persistence, _metrics = _runner(store=store, events=events)

    result = await runner.run_once(now=NOW + timedelta(minutes=1))

    assert result.verified == 1
    outcome = (await store.list_outcomes(org_id=ORG, bundle_ref=manifest.bundle_ref))[0]
    assert outcome.outcome is AuditExportVerificationOutcome.VERIFIED


async def test_tampered_safe_bundle_is_persisted_as_a_closed_failure() -> None:
    events = _events()
    store = InMemoryAuditExportVerificationStore()
    wire = _v2_manifest(events).model_dump(mode="json")
    wire["rows"][0]["signature"] = "0" * 64
    tampered = AuditExportBundleManifest.model_validate(wire)
    await store.record_manifest(manifest=tampered)
    runner, _persistence, _metrics = _runner(store=store, events=events)

    result = await runner.run_once(now=NOW + timedelta(minutes=1))

    assert result.failed == 1
    outcomes = await store.list_outcomes(
        org_id=tampered.org_id,
        bundle_ref=tampered.bundle_ref,
    )
    assert outcomes[0].outcome is AuditExportVerificationOutcome.FAILED
    assert (
        outcomes[0].failure_class is AuditExportVerificationFailureClass.SOURCE_MISMATCH
    )


def test_safe_v2_manifest_rejects_private_or_unbounded_projection() -> None:
    events = _events()
    private = _v2_manifest(events).model_dump(mode="json")
    private["rows"][0]["safe_payload"]["title"] = "private ledger title"
    with pytest.raises(ValueError, match="bounded safe projection"):
        AuditExportBundleManifest.model_validate(private)

    oversized = _v2_manifest(events).model_dump(mode="json")
    oversized["rows"][0]["safe_payload"]["opaque"] = "x" * 20_000
    with pytest.raises(ValueError, match="bounded safe projection"):
        AuditExportBundleManifest.model_validate(oversized)


async def test_missing_production_key_is_honest_unavailable_not_success() -> None:
    events = _events()
    store = InMemoryAuditExportVerificationStore()
    manifest = _v2_manifest(events)
    await store.record_manifest(manifest=manifest)
    metrics = _Metrics()
    runner, _persistence, _ = _runner(
        store=store,
        events=events,
        metrics=metrics,
        signer_factory=lambda: (_ for _ in ()).throw(RuntimeError("missing key")),
    )

    result = await runner.run_once(now=NOW + timedelta(minutes=1))

    assert result.unavailable == 1
    outcomes = await store.list_outcomes(org_id=ORG, bundle_ref=manifest.bundle_ref)
    assert outcomes[0].outcome is AuditExportVerificationOutcome.UNAVAILABLE
    assert (
        outcomes[0].failure_class
        is AuditExportVerificationFailureClass.SIGNING_MATERIAL_UNAVAILABLE
    )
    assert metrics.calls == [{"format": "receipt_v2", "succeeded": False}]


async def test_real_production_missing_key_is_honest_not_dev_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _events()
    store = InMemoryAuditExportVerificationStore()
    manifest = _v1_manifest(events)
    await store.record_manifest(manifest=manifest)
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "production")
    monkeypatch.delenv("AUDIT_HMAC_KEY", raising=False)
    monkeypatch.delenv("AUDIT_HMAC_KEY_VERSION", raising=False)
    metrics = _Metrics()
    runner, _persistence, _ = _runner(
        store=store,
        events=events,
        metrics=metrics,
        signer_factory=AuditExportVerificationSampler._signer_from_env,
    )

    result = await runner.run_once(now=NOW + timedelta(minutes=1))

    assert result.unavailable == 1
    outcome = (await store.list_outcomes(org_id=ORG, bundle_ref=manifest.bundle_ref))[0]
    assert outcome.outcome is AuditExportVerificationOutcome.UNAVAILABLE
    assert (
        outcome.failure_class
        is AuditExportVerificationFailureClass.SIGNING_MATERIAL_UNAVAILABLE
    )


async def test_file_store_restart_resumes_cursor_without_duplicate_first_sample(
    tmp_path: Path,
) -> None:
    events = _events()
    first = _v1_manifest(events)
    second = _v2_manifest(events)
    first_store = FileAuditExportVerificationStore(root=tmp_path)
    await first_store.record_manifest(manifest=first)
    await first_store.record_manifest(manifest=second)
    first_runner, _sink, _metrics = _runner(
        store=first_store,
        events=events,
        max_samples=1,
    )

    first_result = await first_runner.run_once(now=NOW + timedelta(minutes=1))
    assert first_result.sampled == 1

    restarted = FileAuditExportVerificationStore(root=tmp_path)
    second_runner, _sink, _metrics = _runner(
        store=restarted,
        events=events,
        max_samples=1,
    )
    second_result = await second_runner.run_once(now=NOW + timedelta(minutes=2))

    assert second_result.sampled == 1
    first_outcomes = await restarted.list_outcomes(
        org_id=ORG, bundle_ref=first.bundle_ref
    )
    second_outcomes = await restarted.list_outcomes(
        org_id=ORG, bundle_ref=second.bundle_ref
    )
    assert first_outcomes[0].attempts == 1
    assert second_outcomes[0].attempts == 1
    state = (tmp_path / "audit_export_verification" / "state.json").read_text()
    assert "session-secret" not in state
    assert "/private/path" not in state


async def test_active_lease_and_one_bad_sample_do_not_block_other_tenants() -> None:
    events = _events()
    store = InMemoryAuditExportVerificationStore()
    bad_data = _v1_manifest(events).model_dump(mode="json")
    bad_data["rows"][0]["payload_digest"] = "0" * 64
    bad = AuditExportBundleManifest.model_validate(bad_data)
    good = _v2_manifest(events)
    await store.record_manifest(manifest=bad)
    await store.record_manifest(manifest=good)
    assert await store.acquire_lease(
        owner_id="other-worker",
        now=NOW,
        expires_at=NOW + timedelta(seconds=30),
    )
    runner, _sink, _metrics = _runner(store=store, events=events)
    blocked = await runner.run_once(now=NOW)
    assert blocked.lease_not_acquired
    await store.release_lease(owner_id="other-worker")

    result = await runner.run_once(now=NOW + timedelta(minutes=1))
    assert result.sampled == 2
    assert result.failed == 1
    assert result.verified == 1


async def test_source_failure_for_one_tenant_does_not_skip_another_tenant() -> None:
    events = _events()
    store = InMemoryAuditExportVerificationStore()
    unavailable = _v1_manifest(events).model_copy(
        update={"org_id": "org_audit_unavailable"}
    )
    verified = _v2_manifest(events)
    await store.record_manifest(manifest=unavailable)
    await store.record_manifest(manifest=verified)

    class _TenantScopedEvents:
        async def list_events_after(self, *, org_id, run_id, after_sequence):  # noqa: ANN001
            assert run_id == RUN
            if org_id == unavailable.org_id:
                raise RuntimeError("tenant source unavailable")
            assert org_id == ORG
            return tuple(
                event for event in events if event.sequence_no > after_sequence
            )

    runner, _sink, _metrics = _runner(
        store=store,
        events=events,
        event_store=_TenantScopedEvents(),
    )
    result = await runner.run_once(now=NOW + timedelta(minutes=1))

    assert result.sampled == 2
    assert result.verified == 1
    assert result.unavailable == 1
    unavailable_outcome = (
        await store.list_outcomes(
            org_id=unavailable.org_id,
            bundle_ref=unavailable.bundle_ref,
        )
    )[0]
    assert (
        unavailable_outcome.failure_class
        is AuditExportVerificationFailureClass.SOURCE_UNAVAILABLE
    )


async def test_outcome_store_failure_does_not_advance_cursor_or_skip_evidence() -> None:
    events = _events()
    store = _FailingOutcomeStore()
    manifest = _v2_manifest(events)
    await store.record_manifest(manifest=manifest)
    runner, persistence, _metrics = _runner(store=store, events=events)

    result = await runner.run_once(now=NOW + timedelta(minutes=1))

    assert result.outcome_persistence_failures == 1
    assert result.sampled == 0
    assert persistence.audit == []
    assert await store.load_scan_cursor() is None


async def test_lease_or_cursor_store_outage_is_contained_without_skipping_work() -> (
    None
):
    events = _events()
    manifest = _v2_manifest(events)

    lease_store = _LeaseUnavailableStore()
    await lease_store.record_manifest(manifest=manifest)
    lease_runner, _sink, _metrics = _runner(store=lease_store, events=events)
    lease_result = await lease_runner.run_once(now=NOW + timedelta(minutes=1))
    assert lease_result.unavailable == 1
    assert lease_result.sampled == 0

    cursor_store = _CursorUnavailableStore()
    await cursor_store.record_manifest(manifest=manifest)
    cursor_runner, _sink, _metrics = _runner(store=cursor_store, events=events)
    cursor_result = await cursor_runner.run_once(now=NOW + timedelta(minutes=1))
    assert cursor_result.verified == 1
    assert cursor_result.unavailable == 1
    assert await cursor_store.load_scan_cursor() is None


async def test_audit_evidence_write_failure_is_isolated_after_safe_outcome_persists() -> (
    None
):
    events = _events()
    store = InMemoryAuditExportVerificationStore()
    manifest = _v2_manifest(events)
    await store.record_manifest(manifest=manifest)
    runner, _persistence, _metrics = _runner(
        store=store,
        events=events,
        persistence=_FailingAuditPersistence(),
    )

    result = await runner.run_once(now=NOW + timedelta(minutes=1))

    assert result.verified == 1
    assert result.audit_evidence_failures == 1
    outcomes = await store.list_outcomes(org_id=ORG, bundle_ref=manifest.bundle_ref)
    assert outcomes[0].outcome is AuditExportVerificationOutcome.VERIFIED


async def test_manifest_capture_is_idempotent_across_repeat_exports() -> None:
    events = _events()
    manifest = _v2_manifest(events)
    repeated = manifest.model_copy(
        update={"captured_at": manifest.captured_at + timedelta(hours=1)}
    )
    store = InMemoryAuditExportVerificationStore()
    await store.record_manifest(manifest=manifest)
    await store.record_manifest(manifest=repeated)

    rows = await store.list_manifests_after(cursor=None, limit=10)
    assert rows == (manifest,)

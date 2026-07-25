"""Live Postgres parity checks for the D7/D12 safe verification state adapter."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from agent_runtime.surfaces_v2.audit_export_verification import (
    AuditExportBundleManifest,
    AuditExportEvidenceRow,
    AuditExportFormat,
    AuditExportVerificationCursor,
    AuditExportVerificationFailureClass,
    AuditExportVerificationOutcome,
    AuditExportVerificationRecord,
)
from runtime_adapters.postgres.audit_export_verification_store import (
    PostgresAuditExportVerificationStore,
)
from runtime_adapters.postgres.runtime_api_store import PostgresRuntimeApiStore


pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not os.environ.get("AUDIT_EXPORT_VERIFICATION_LIVE_TEST_DATABASE_URL"),
        reason=(
            "Set AUDIT_EXPORT_VERIFICATION_LIVE_TEST_DATABASE_URL to a disposable "
            "Postgres database to exercise the D7/D12 sampling-state adapter."
        ),
    ),
]


@pytest.fixture
def database_url() -> str:
    return os.environ["AUDIT_EXPORT_VERIFICATION_LIVE_TEST_DATABASE_URL"]


@pytest.fixture
async def runtime_store(database_url: str) -> AsyncIterator[PostgresRuntimeApiStore]:
    store = PostgresRuntimeApiStore(
        database_url,
        pool_min_size=1,
        pool_max_size=4,
        pool_acquire_timeout_seconds=10.0,
    )
    await store.open()
    try:
        await store.migrate()
        yield store
    finally:
        await store.close()


def _manifest(
    *,
    org_id: str,
    suffix: str,
    captured_at: datetime,
    format: AuditExportFormat = AuditExportFormat.RECEIPT_V2,
) -> AuditExportBundleManifest:
    digest = (suffix * 64)[:64]
    legacy = format is AuditExportFormat.RECEIPT_V1
    return AuditExportBundleManifest(
        bundle_ref=f"aev_{suffix * 24}",
        org_id=org_id,
        run_id=f"run_audit_sample_{suffix}",
        format=format,
        bundle_digest=digest,
        # Older signed exports can spell UTC with ``Z``. Postgres normalizes
        # timestamptz values, so this proves the adapter also keeps the exact
        # wire spelling used by the bundle digest.
        generated_at="2026-07-25T12:00:00Z",
        captured_at=captured_at,
        key_id=None if legacy else "audit-hmac:v1",
        legacy_version_key="bundle_version" if legacy else None,
        head_hash="b" * 64,
        receipt_digest=None if legacy else "c" * 64,
        rows=(
            AuditExportEvidenceRow(
                ordinal=1,
                sequence_no=1,
                event_type="receipt.v2",
                created_at="2026-07-25T12:00:00+00:00",
                payload_digest="d" * 64,
                prev_hash=None,
                signature="b" * 64,
                key_version=1,
                key_id=None if legacy else "audit-hmac:v1",
                ref_class=None if legacy else "none",
                safe_payload=None if legacy else {"run_id": "redacted"},
            ),
        ),
    )


async def test_postgres_manifest_cursor_lease_and_outcome_survive_restart(
    runtime_store: PostgresRuntimeApiStore,
) -> None:
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)
    org_id = f"org_audit_sample_{uuid4().hex}"
    first = _manifest(
        org_id=org_id,
        suffix="a",
        captured_at=now,
        format=AuditExportFormat.RECEIPT_V1,
    )
    second = _manifest(
        org_id=org_id, suffix="b", captured_at=now + timedelta(seconds=1)
    )
    store = PostgresAuditExportVerificationStore(store=runtime_store)

    await store.record_manifest(manifest=first)
    await store.record_manifest(manifest=first)
    await store.record_manifest(manifest=second)
    page = await store.list_manifests_after(cursor=None, limit=1)
    assert page == (first,)
    cursor = await store.load_scan_cursor()
    assert cursor is None
    expected_cursor = AuditExportVerificationCursor(
        after_captured_at=first.captured_at,
        after_org_id=first.org_id,
        after_bundle_ref=first.bundle_ref,
    )
    assert await store.advance_scan_cursor(expected=None, next_cursor=expected_cursor)
    restarted = PostgresAuditExportVerificationStore(store=runtime_store)
    assert await restarted.load_scan_cursor() == expected_cursor
    assert await restarted.acquire_lease(
        owner_id="worker-one",
        now=now,
        expires_at=now + timedelta(seconds=30),
    )
    assert not await restarted.acquire_lease(
        owner_id="worker-two",
        now=now + timedelta(seconds=1),
        expires_at=now + timedelta(seconds=30),
    )
    await restarted.release_lease(owner_id="worker-one")
    assert await restarted.acquire_lease(
        owner_id="worker-two",
        now=now + timedelta(seconds=1),
        expires_at=now + timedelta(seconds=31),
    )

    record = AuditExportVerificationRecord(
        org_id=org_id,
        bundle_ref=first.bundle_ref,
        bundle_digest=first.bundle_digest,
        format=AuditExportFormat.RECEIPT_V2,
        outcome=AuditExportVerificationOutcome.VERIFIED,
        failure_class=AuditExportVerificationFailureClass.NONE,
        sampled_at=now,
    )
    assert (await restarted.record_outcome(record=record)).attempts == 1
    updated = await restarted.record_outcome(
        record=record.model_copy(update={"sampled_at": now + timedelta(minutes=1)})
    )
    assert updated.attempts == 2
    assert (await restarted.list_outcomes(org_id=org_id, bundle_ref=first.bundle_ref))[
        0
    ].attempts == 2

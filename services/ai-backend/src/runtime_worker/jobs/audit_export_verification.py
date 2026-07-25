"""Bounded, read-only D7/D12 audit-export verification sampling worker.

The job samples only entries in the safe issued-export catalog.  It obtains no
effect executor, queue, approval service, artifact service, filesystem path,
or network client.  A sample either verifies a concrete issued bundle through
the canonical ``ReceiptExportV2Verifier`` (which also supports v1), records a
closed safe result, or honestly records that signing material/source evidence
is unavailable.  It never turns a failure into a repair or retry side effect.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
import os
from time import perf_counter
from uuid import uuid4

from copilot_audit_chain import AuditChainSigner, ChainVerificationResult

from agent_runtime.api.constants import Values
from agent_runtime.api.ports import EventStorePort, PersistencePort
from agent_runtime.observability.lifecycle_metrics import (
    LifecycleOperationalMetrics,
    get_lifecycle_operational_metrics,
)
from agent_runtime.surfaces_v2.audit_export_verification import (
    AuditExportBundleManifest,
    AuditExportFormat,
    AuditExportVerificationCursor,
    AuditExportVerificationFailureClass,
    AuditExportVerificationOutcome,
    AuditExportVerificationRecord,
    AuditExportVerificationStore,
    audit_export_bundle_digest,
)
from agent_runtime.surfaces_v2.receipt import ReceiptFold
from agent_runtime.surfaces_v2.receipt_export import ReceiptExportBuilder
from agent_runtime.surfaces_v2.receipt_export_v2 import ReceiptExportV2Verifier


_LOGGER = logging.getLogger(__name__)


class AuditExportVerificationSamplingEnv:
    """Disabled-by-default, bounded worker configuration."""

    ENABLED = "AUDIT_EXPORT_VERIFICATION_SAMPLING_ENABLED"
    INTERVAL_SECONDS = "AUDIT_EXPORT_VERIFICATION_SAMPLING_INTERVAL_SECONDS"
    MAX_SAMPLES = "AUDIT_EXPORT_VERIFICATION_SAMPLING_MAX_SAMPLES"
    LEASE_SECONDS = "AUDIT_EXPORT_VERIFICATION_SAMPLING_LEASE_SECONDS"

    DEFAULT_INTERVAL_SECONDS = 900.0
    DEFAULT_MAX_SAMPLES = 25
    DEFAULT_LEASE_SECONDS = 120

    @classmethod
    def env_bool(cls, name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def env_float(cls, name: str, default: float) -> float:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            parsed = float(raw)
        except ValueError:
            return default
        return parsed if parsed > 0 else default

    @classmethod
    def env_int(cls, name: str, default: int, *, maximum: int) -> int:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        try:
            parsed = int(raw)
        except ValueError:
            return default
        return parsed if 1 <= parsed <= maximum else default


@dataclass(frozen=True, slots=True)
class AuditExportVerificationSamplingResult:
    """Identifier-free summary of one bounded worker cycle."""

    sampled: int = 0
    verified: int = 0
    failed: int = 0
    unavailable: int = 0
    outcome_persistence_failures: int = 0
    audit_evidence_failures: int = 0
    cursor_conflict: bool = False
    lease_not_acquired: bool = False


class AuditExportVerificationSampler:
    """Rehydrate and verify one manifest without ever persisting a raw body."""

    def __init__(
        self,
        *,
        event_store: EventStorePort,
        signer_factory: Callable[[], AuditChainSigner] | None = None,
        metrics: LifecycleOperationalMetrics | None = None,
    ) -> None:
        self._event_store = event_store
        self._signer_factory = signer_factory or self._signer_from_env
        self._metrics = (
            metrics if metrics is not None else get_lifecycle_operational_metrics()
        )

    async def sample(
        self, *, manifest: AuditExportBundleManifest
    ) -> tuple[
        AuditExportVerificationOutcome, AuditExportVerificationFailureClass, int | None
    ]:
        """Return a closed result for one issued bundle.

        All exceptions stay inside this security boundary.  A failure is a
        truthful non-success and never carries exception text into persistence
        or logs.
        """

        try:
            signer = self._signer_factory()
        except Exception:  # signing/key material unavailable is an honest state
            self._record_metric(manifest=manifest, succeeded=False)
            return (
                AuditExportVerificationOutcome.UNAVAILABLE,
                AuditExportVerificationFailureClass.SIGNING_MATERIAL_UNAVAILABLE,
                None,
            )
        try:
            wire = await self._wire_for(manifest=manifest, signer=signer)
        except _SourceMismatch:
            self._record_metric(manifest=manifest, succeeded=False)
            return (
                AuditExportVerificationOutcome.FAILED,
                AuditExportVerificationFailureClass.SOURCE_MISMATCH,
                None,
            )
        except Exception:
            self._record_metric(manifest=manifest, succeeded=False)
            return (
                AuditExportVerificationOutcome.UNAVAILABLE,
                AuditExportVerificationFailureClass.SOURCE_UNAVAILABLE,
                None,
            )

        result = ReceiptExportV2Verifier(signer=signer, metrics=self._metrics).verify(
            wire
        )
        if result.ok:
            return (
                AuditExportVerificationOutcome.VERIFIED,
                AuditExportVerificationFailureClass.NONE,
                None,
            )
        return (
            AuditExportVerificationOutcome.FAILED,
            self._failure_class(result),
            result.broken_at_seq,
        )

    async def _wire_for(
        self,
        *,
        manifest: AuditExportBundleManifest,
        signer: AuditChainSigner,
    ) -> dict[str, object]:
        if manifest.format is AuditExportFormat.RECEIPT_V2:
            wire = manifest.v2_wire()
            if audit_export_bundle_digest(wire) != manifest.bundle_digest:
                raise _SourceMismatch()
            return wire
        return await self._rehydrate_v1(manifest=manifest, signer=signer)

    async def _rehydrate_v1(
        self,
        *,
        manifest: AuditExportBundleManifest,
        signer: AuditChainSigner,
    ) -> dict[str, object]:
        """Rebuild only in memory, then swap in the issued signatures.

        Calling the builder gives us canonical event filtering, deterministic
        receipt folding, and a current in-memory payload shape.  We never use
        its new signatures as evidence; the manifest's original envelopes are
        restored before passing the wire map to the canonical v1 verifier.
        """

        events = await self._event_store.list_events_after(
            org_id=manifest.org_id,
            run_id=manifest.run_id,
            after_sequence=0,
        )
        receipt = ReceiptFold.fold(run_id=manifest.run_id, events=events)
        rebuilt = (
            ReceiptExportBuilder(signer=signer)
            .build(
                run_id=manifest.run_id,
                events=events,
                receipt=receipt,
            )
            .model_dump(mode="json")
        )
        rows = rebuilt.get("rows")
        if not isinstance(rows, list) or len(rows) != len(manifest.rows):
            raise _SourceMismatch()
        rehydrated_rows: list[dict[str, object]] = []
        for original, evidence in zip(rows, manifest.rows, strict=True):
            if not isinstance(original, Mapping):
                raise _SourceMismatch()
            if (
                original.get("sequence_no") != evidence.sequence_no
                or original.get("event_type") != evidence.event_type
                or original.get("created_at") != evidence.created_at
                or audit_export_bundle_digest(original.get("payload"))
                != evidence.payload_digest
                or original.get("ledger_id") != evidence.ledger_id
            ):
                raise _SourceMismatch()
            rehydrated_rows.append(
                {
                    "seq": evidence.ordinal,
                    "ledger_id": evidence.ledger_id,
                    "event_type": evidence.event_type,
                    "sequence_no": evidence.sequence_no,
                    "created_at": evidence.created_at,
                    "payload": original.get("payload"),
                    "prev_hash": evidence.prev_hash,
                    "signature": evidence.signature,
                    "key_version": evidence.key_version,
                }
            )
        if manifest.legacy_version_key is None:
            raise _SourceMismatch()
        wire: dict[str, object] = {
            manifest.legacy_version_key: 1,
            "run_id": manifest.run_id,
            "generated_at": manifest.generated_at,
            "receipt": rebuilt.get("receipt"),
            "rows": rehydrated_rows,
            "head_hash": manifest.head_hash,
        }
        if audit_export_bundle_digest(wire) != manifest.bundle_digest:
            raise _SourceMismatch()
        return wire

    @staticmethod
    def _failure_class(
        result: ChainVerificationResult,
    ) -> AuditExportVerificationFailureClass:
        reason = (result.reason or "").lower()
        if "unknown key" in reason or "key version" in reason:
            return AuditExportVerificationFailureClass.KEY_VERSION_UNAVAILABLE
        if "malformed" in reason or "unsupported" in reason:
            return AuditExportVerificationFailureClass.BUNDLE_MALFORMED
        return AuditExportVerificationFailureClass.CHAIN_INVALID

    def _record_metric(
        self,
        *,
        manifest: AuditExportBundleManifest,
        succeeded: bool,
    ) -> None:
        try:
            self._metrics.record_audit_verification(
                format=manifest.format.value,
                succeeded=succeeded,
            )
        except Exception:
            return

    @staticmethod
    def _signer_from_env() -> AuditChainSigner:
        return AuditChainSigner.from_env(
            environment_env_var=Values.RUNTIME_ENVIRONMENT_ENV_VAR
        )


class AuditExportVerificationSamplingRunner:
    """Lease-protected, cursor-resumable, strictly bounded sample runner."""

    _AUDIT_EVENT_TYPE = "runtime_audit_export_verification_sampled"

    def __init__(
        self,
        *,
        store: AuditExportVerificationStore,
        sampler: AuditExportVerificationSampler,
        persistence: PersistencePort,
        worker_id: str | None = None,
        max_samples: int = AuditExportVerificationSamplingEnv.DEFAULT_MAX_SAMPLES,
        lease_seconds: int = AuditExportVerificationSamplingEnv.DEFAULT_LEASE_SECONDS,
    ) -> None:
        if not 1 <= max_samples <= 500 or not 1 <= lease_seconds <= 3600:
            raise ValueError("audit export verification bounds are invalid")
        self._store = store
        self._sampler = sampler
        self._persistence = persistence
        self._worker_id = worker_id or f"audit-export-sampler-{uuid4().hex[:16]}"
        self._max_samples = max_samples
        self._lease_seconds = lease_seconds

    async def run_once(
        self, *, now: datetime | None = None
    ) -> AuditExportVerificationSamplingResult:
        """Sample one page.  Individual source failures never stop the page."""

        reference_now = _utc(now or datetime.now(UTC))
        lease_until = reference_now + timedelta(seconds=self._lease_seconds)
        try:
            lease_acquired = await self._store.acquire_lease(
                owner_id=self._worker_id,
                now=reference_now,
                expires_at=lease_until,
            )
        except Exception:
            # The state store is the ownership boundary.  A lease outage must
            # not kill the periodic task or cause an unleased scan.
            _LOGGER.warning("audit_export_verification_lease_unavailable")
            return AuditExportVerificationSamplingResult(unavailable=1)
        if not lease_acquired:
            return AuditExportVerificationSamplingResult(lease_not_acquired=True)
        try:
            started_at = perf_counter()
            try:
                cursor = await self._store.load_scan_cursor()
                page = await self._store.list_manifests_after(
                    cursor=cursor,
                    limit=self._max_samples,
                )
            except Exception:
                _LOGGER.warning("audit_export_verification_source_unavailable")
                return AuditExportVerificationSamplingResult(unavailable=1)
            if not page:
                if cursor is not None:
                    try:
                        advanced = await self._store.advance_scan_cursor(
                            expected=cursor,
                            next_cursor=None,
                        )
                    except Exception:
                        _LOGGER.warning("audit_export_verification_cursor_unavailable")
                        return AuditExportVerificationSamplingResult(unavailable=1)
                    return AuditExportVerificationSamplingResult(
                        cursor_conflict=not advanced
                    )
                return AuditExportVerificationSamplingResult()

            result = AuditExportVerificationSamplingResult()
            outcomes_persisted = True
            for manifest in page:
                (
                    outcome,
                    failure_class,
                    broken_at_seq,
                    persisted,
                ) = await self._sample_one(manifest=manifest, sampled_at=reference_now)
                if not persisted:
                    outcomes_persisted = False
                    result = AuditExportVerificationSamplingResult(
                        sampled=result.sampled,
                        verified=result.verified,
                        failed=result.failed,
                        unavailable=result.unavailable + 1,
                        outcome_persistence_failures=(
                            result.outcome_persistence_failures + 1
                        ),
                        audit_evidence_failures=result.audit_evidence_failures,
                    )
                    break
                audit_evidence_failed = not await self._write_audit_evidence(
                    manifest=manifest,
                    outcome=outcome,
                    failure_class=failure_class,
                )
                result = AuditExportVerificationSamplingResult(
                    sampled=result.sampled + 1,
                    verified=result.verified
                    + int(outcome is AuditExportVerificationOutcome.VERIFIED),
                    failed=result.failed
                    + int(outcome is AuditExportVerificationOutcome.FAILED),
                    unavailable=result.unavailable
                    + int(outcome is AuditExportVerificationOutcome.UNAVAILABLE),
                    outcome_persistence_failures=result.outcome_persistence_failures,
                    audit_evidence_failures=result.audit_evidence_failures
                    + int(audit_evidence_failed),
                )
            if outcomes_persisted:
                next_cursor = (
                    _cursor_for(page[-1]) if len(page) == self._max_samples else None
                )
                try:
                    advanced = await self._store.advance_scan_cursor(
                        expected=cursor,
                        next_cursor=next_cursor,
                    )
                except Exception:
                    _LOGGER.warning("audit_export_verification_cursor_unavailable")
                    return AuditExportVerificationSamplingResult(
                        sampled=result.sampled,
                        verified=result.verified,
                        failed=result.failed,
                        unavailable=result.unavailable + 1,
                        outcome_persistence_failures=(
                            result.outcome_persistence_failures
                        ),
                        audit_evidence_failures=result.audit_evidence_failures,
                    )
                if not advanced:
                    result = AuditExportVerificationSamplingResult(
                        sampled=result.sampled,
                        verified=result.verified,
                        failed=result.failed,
                        unavailable=result.unavailable,
                        outcome_persistence_failures=(
                            result.outcome_persistence_failures
                        ),
                        audit_evidence_failures=result.audit_evidence_failures,
                        cursor_conflict=True,
                    )
            _LOGGER.info(
                "audit_export_verification_cycle_complete sampled=%s duration_ms=%s",
                result.sampled,
                int((perf_counter() - started_at) * 1_000),
            )
            return result
        finally:
            try:
                await self._store.release_lease(owner_id=self._worker_id)
            except Exception:
                _LOGGER.warning("audit_export_verification_lease_release_failed")

    async def _sample_one(
        self,
        *,
        manifest: AuditExportBundleManifest,
        sampled_at: datetime,
    ) -> tuple[
        AuditExportVerificationOutcome,
        AuditExportVerificationFailureClass,
        int | None,
        bool,
    ]:
        try:
            outcome, failure_class, broken_at_seq = await self._sampler.sample(
                manifest=manifest
            )
        except Exception:
            outcome = AuditExportVerificationOutcome.FAILED
            failure_class = AuditExportVerificationFailureClass.INTERNAL_ERROR
            broken_at_seq = None
        record = AuditExportVerificationRecord(
            org_id=manifest.org_id,
            bundle_ref=manifest.bundle_ref,
            bundle_digest=manifest.bundle_digest,
            format=manifest.format,
            outcome=outcome,
            failure_class=failure_class,
            broken_at_seq=broken_at_seq,
            sampled_at=sampled_at,
        )
        try:
            await self._store.record_outcome(record=record)
        except Exception:
            # Outcome durability is the cursor safety boundary.  Do not claim a
            # successful sample if its evidence cannot be retained.
            return (
                AuditExportVerificationOutcome.UNAVAILABLE,
                AuditExportVerificationFailureClass.SOURCE_UNAVAILABLE,
                None,
                False,
            )
        return outcome, failure_class, broken_at_seq, True

    async def _write_audit_evidence(
        self,
        *,
        manifest: AuditExportBundleManifest,
        outcome: AuditExportVerificationOutcome,
        failure_class: AuditExportVerificationFailureClass,
    ) -> bool:
        """Append safe job evidence; a logging failure cannot corrupt a cursor."""

        try:
            await self._persistence.write_audit_log(
                event_type=self._AUDIT_EVENT_TYPE,
                record={
                    "org_id": manifest.org_id,
                    "actor_type": "system",
                    "action": self._AUDIT_EVENT_TYPE,
                    "resource_type": "audit_export",
                    "resource_id": manifest.bundle_ref,
                    "outcome": outcome.value,
                    "metadata": {
                        "bundle_digest": manifest.bundle_digest,
                        "bundle_format": manifest.format.value,
                        "failure_class": failure_class.value,
                    },
                },
            )
            return True
        except Exception:
            _LOGGER.warning("audit_export_verification_evidence_write_failed")
            return False


class AuditExportVerificationSamplingLoop:
    """Periodic wrapper; disabled by worker composition unless explicitly enabled."""

    def __init__(
        self,
        *,
        runner: AuditExportVerificationSamplingRunner,
        interval_seconds: float | None = None,
    ) -> None:
        self._runner = runner
        self._interval = (
            interval_seconds
            if interval_seconds is not None
            else AuditExportVerificationSamplingEnv.env_float(
                AuditExportVerificationSamplingEnv.INTERVAL_SECONDS,
                AuditExportVerificationSamplingEnv.DEFAULT_INTERVAL_SECONDS,
            )
        )
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(
                self._run(), name="audit-export-verification-sampling-loop"
            )

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            return

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                return
            except TimeoutError:
                pass
            try:
                result = await self._runner.run_once()
            except Exception:
                # This is deliberately a final containment boundary.  The
                # runner normally classifies all adapter failures, but a
                # future implementation mistake must not terminate the worker
                # process or turn a later periodic cycle into skipped work.
                _LOGGER.warning("audit_export_verification_cycle_crashed")
                continue
            if (
                result.failed
                or result.unavailable
                or result.outcome_persistence_failures
                or result.audit_evidence_failures
            ):
                _LOGGER.warning(
                    "audit_export_verification_cycle_nonhealthy sampled=%s "
                    "failed=%s unavailable=%s outcome_persistence_failures=%s "
                    "evidence_failures=%s",
                    result.sampled,
                    result.failed,
                    result.unavailable,
                    result.outcome_persistence_failures,
                    result.audit_evidence_failures,
                )


class _SourceMismatch(Exception):
    """Internal control-flow signal; never retained or logged verbatim."""


def _cursor_for(
    manifest: AuditExportBundleManifest,
) -> AuditExportVerificationCursor:
    return AuditExportVerificationCursor(
        after_captured_at=manifest.captured_at,
        after_org_id=manifest.org_id,
        after_bundle_ref=manifest.bundle_ref,
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


__all__ = (
    "AuditExportVerificationSampler",
    "AuditExportVerificationSamplingEnv",
    "AuditExportVerificationSamplingLoop",
    "AuditExportVerificationSamplingResult",
    "AuditExportVerificationSamplingRunner",
)

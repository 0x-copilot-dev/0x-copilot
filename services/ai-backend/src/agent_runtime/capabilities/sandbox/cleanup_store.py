"""Durable retry schedule for sandbox-provider teardown obligations."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
import re
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from agent_runtime.capabilities.sandbox._file_records import (
    SandboxFileRecordError,
    SandboxFileRecords,
    canonical_record_key,
)
from runtime_adapters.file._paths import FileStoreLayout


_SAFE_ERROR_SUMMARY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,:;_()\-]{0,511}$")


class SandboxCleanupScheduleError(SandboxFileRecordError):
    """A sandbox cleanup duty is corrupt, stale, or transitioned unsafely."""


class SandboxCleanupSchedule(BaseModel):
    """A credential-free, versioned provider teardown duty for one operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    record_type: Literal["sandbox_cleanup"] = "sandbox_cleanup"
    operation_id: str = Field(min_length=1, max_length=255)
    run_id: str = Field(min_length=1, max_length=255)
    provider_session_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    owner_marker: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: Literal["provisioning", "cleanup_pending", "cleaned"] = "cleanup_pending"
    transition_no: int = Field(default=0, ge=0)
    attempts: int = Field(default=0, ge=0)
    retry_not_before: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error_summary: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    immutable_identity: str | None = None

    @field_validator("error_summary")
    @classmethod
    def _validate_error_summary(cls, value: str | None) -> str | None:
        if value is not None and not _SAFE_ERROR_SUMMARY.fullmatch(value):
            raise ValueError("sandbox cleanup error summary is not persistence-safe")
        return value

    @model_validator(mode="after")
    def _bind_immutable_identity(self) -> "SandboxCleanupSchedule":
        expected = _identity_digest(
            operation_id=self.operation_id,
            run_id=self.run_id,
            owner_marker=self.owner_marker,
            snapshot_digest=self.snapshot_digest,
        )
        legacy = _legacy_identity_digest(
            operation_id=self.operation_id,
            run_id=self.run_id,
            provider_session_ref=self.provider_session_ref,
            snapshot_digest=self.snapshot_digest,
        )
        if self.immutable_identity is None:
            object.__setattr__(self, "immutable_identity", expected)
        elif self.immutable_identity not in {expected, legacy}:
            raise ValueError("sandbox cleanup immutable identity is mismatched")
        if self.state == "provisioning":
            if self.provider_session_ref is not None or self.owner_marker is None:
                raise ValueError(
                    "provisioning cleanup duties require an owner marker only"
                )
        elif self.state == "cleanup_pending" and self.provider_session_ref is None:
            raise ValueError("cleanup_pending duties require a provider session ref")
        return self


_TRANSITIONS: dict[str, frozenset[str]] = {
    "provisioning": frozenset({"provisioning", "cleanup_pending", "cleaned"}),
    "cleanup_pending": frozenset({"cleanup_pending", "cleaned"}),
    "cleaned": frozenset(),
}


class FileSandboxCleanupStore:
    """D3 teardown duties with an independently durable recovery journal.

    The normal duty lives below ``sandbox/cleanup``.  A failed normal-record
    commit is not permission to lose an already-created provider session: the
    same immutable duty is written below ``sandbox/cleanup-recovery`` before
    the failure is surfaced to the lifecycle service.  Both locations are
    drained by the same reaper and use the normal duty's logical operation id
    as their cross-process lock key.

    The recovery journal is deliberately a second file-record category rather
    than an in-memory retry.  If both durable writes fail, callers must report
    an indeterminate lifecycle state; they must never claim cleanup succeeded.
    """

    def __init__(self, *, layout: FileStoreLayout) -> None:
        self._records = SandboxFileRecords(layout=layout, category="cleanup")
        self._recovery_records = SandboxFileRecords(
            layout=layout, category="cleanup-recovery"
        )

    async def schedule(self, record: SandboxCleanupSchedule) -> SandboxCleanupSchedule:
        return await asyncio.to_thread(self._schedule, record)

    async def get(self, operation_id: str) -> SandboxCleanupSchedule | None:
        return await asyncio.to_thread(self._get, operation_id)

    async def transition(
        self,
        *,
        record: SandboxCleanupSchedule,
        expected_transition_no: int,
    ) -> SandboxCleanupSchedule:
        return await asyncio.to_thread(self._transition, record, expected_transition_no)

    async def list_pending(
        self, *, limit: int = 100
    ) -> tuple[SandboxCleanupSchedule, ...]:
        if limit < 1:
            return ()
        return await asyncio.to_thread(self._list_pending, limit)

    def _schedule(self, record: SandboxCleanupSchedule) -> SandboxCleanupSchedule:
        record = _validate_record(record)
        operation_id = canonical_record_key(record.operation_id, field="operation id")
        if record.transition_no != 0 or record.state not in {
            "provisioning",
            "cleanup_pending",
        }:
            raise SandboxCleanupScheduleError(
                "sandbox cleanup duty must start at transition zero"
            )
        try:
            with self._records.locked(operation_id, field="operation id"):
                previous = self._load_from(self._records, operation_id)
                if previous is not None:
                    if not _same_identity(previous, record):
                        raise SandboxCleanupScheduleError(
                            "sandbox cleanup identity changed"
                        )
                    return previous
                recovery = self._load_from(self._recovery_records, operation_id)
                if recovery is not None:
                    if not _same_identity(recovery, record):
                        raise SandboxCleanupScheduleError(
                            "sandbox cleanup identity changed"
                        )
                    return recovery
                self._write_to(self._records, record)
                return record
        except (SandboxCleanupScheduleError, SandboxFileRecordError) as primary_error:
            self._preserve_in_recovery_journal(
                operation_id=operation_id,
                record=record,
                primary_error=primary_error,
            )

    def _preserve_in_recovery_journal(
        self,
        *,
        operation_id: str,
        record: SandboxCleanupSchedule,
        primary_error: Exception,
    ) -> None:
        """Persist a provider ref after any primary-duty persistence failure.

        This sits outside the primary category's lock so failure to lock, open,
        read, or commit that category still has an independent durable route.
        It never returns: the lifecycle caller must attempt immediate teardown
        and report failure, while a restarted reaper owns recovery.
        """

        try:
            with self._recovery_records.locked(operation_id, field="operation id"):
                previous = self._load_from(self._recovery_records, operation_id)
                if previous is not None:
                    if not _same_identity(previous, record):
                        raise SandboxCleanupScheduleError(
                            "sandbox cleanup recovery identity changed"
                        )
                else:
                    self._write_to(self._recovery_records, record)
        except (SandboxCleanupScheduleError, SandboxFileRecordError) as recovery_error:
            raise SandboxCleanupScheduleError(
                "sandbox cleanup duty and recovery journal could not be committed"
            ) from recovery_error
        raise SandboxCleanupScheduleError(
            "sandbox cleanup duty was preserved in the recovery journal"
        ) from primary_error

    def _transition(
        self, record: SandboxCleanupSchedule, expected_transition_no: int
    ) -> SandboxCleanupSchedule:
        record = _validate_record(record)
        operation_id = canonical_record_key(record.operation_id, field="operation id")
        with self._records.locked(operation_id, field="operation id"):
            primary = self._load_from(self._records, operation_id)
            recovery = self._load_from(self._recovery_records, operation_id)
            if primary is not None and recovery is not None:
                if not _same_identity(primary, recovery):
                    raise SandboxCleanupScheduleError(
                        "sandbox cleanup journal identity is mismatched"
                    )
                raise SandboxCleanupScheduleError(
                    "sandbox cleanup duty has conflicting durable copies"
                )
            previous = primary or recovery
            if previous is None:
                raise SandboxCleanupScheduleError("sandbox cleanup duty is missing")
            if previous.transition_no != expected_transition_no:
                raise SandboxCleanupScheduleError("sandbox cleanup transition is stale")
            if not _same_identity(previous, record):
                raise SandboxCleanupScheduleError("sandbox cleanup identity changed")
            if record.transition_no != previous.transition_no + 1:
                raise SandboxCleanupScheduleError(
                    "sandbox cleanup transition number is invalid"
                )
            if record.state not in _TRANSITIONS[previous.state]:
                raise SandboxCleanupScheduleError(
                    "sandbox cleanup transition is invalid"
                )
            if record.attempts < previous.attempts:
                raise SandboxCleanupScheduleError("sandbox cleanup attempts regressed")
            if record.created_at != previous.created_at:
                raise SandboxCleanupScheduleError(
                    "sandbox cleanup creation time changed"
                )
            if record.updated_at < previous.updated_at:
                raise SandboxCleanupScheduleError(
                    "sandbox cleanup update time regressed"
                )
            if (
                previous.provider_session_ref is not None
                and record.provider_session_ref != previous.provider_session_ref
            ):
                raise SandboxCleanupScheduleError(
                    "sandbox cleanup provider reference changed"
                )
            if previous.owner_marker != record.owner_marker:
                raise SandboxCleanupScheduleError(
                    "sandbox cleanup owner marker changed"
                )
            if (
                previous.state == "provisioning"
                and record.state == "cleanup_pending"
                and record.provider_session_ref is None
            ):
                raise SandboxCleanupScheduleError(
                    "sandbox cleanup binding omitted provider reference"
                )
            self._write_to(
                self._records if primary is not None else self._recovery_records,
                record,
            )
            return record

    def _get(self, operation_id: str) -> SandboxCleanupSchedule | None:
        operation_id = canonical_record_key(operation_id, field="operation id")
        primary = self._load_from(self._records, operation_id)
        recovery = self._load_from(self._recovery_records, operation_id)
        if primary is not None and recovery is not None:
            if not _same_identity(primary, recovery):
                raise SandboxCleanupScheduleError(
                    "sandbox cleanup journal identity is mismatched"
                )
            raise SandboxCleanupScheduleError(
                "sandbox cleanup duty has conflicting durable copies"
            )
        return primary or recovery

    @staticmethod
    def _load_from(
        records: SandboxFileRecords, operation_id: str
    ) -> SandboxCleanupSchedule | None:
        raw = records.read(operation_id, field="operation id")
        if raw is None:
            return None
        try:
            record = SandboxCleanupSchedule.model_validate(raw)
            if record.operation_id != operation_id:
                raise SandboxCleanupScheduleError(
                    "sandbox cleanup record identity is mismatched"
                )
        except ValidationError as exc:
            raise SandboxCleanupScheduleError(
                "sandbox cleanup record is corrupt"
            ) from exc
        return record

    def _list_pending(self, limit: int) -> tuple[SandboxCleanupSchedule, ...]:
        by_operation: dict[str, SandboxCleanupSchedule] = {}
        for store in (self._records, self._recovery_records):
            for path, raw in store.iter_records():
                try:
                    record = SandboxCleanupSchedule.model_validate(raw)
                    operation_id = canonical_record_key(
                        record.operation_id, field="operation id"
                    )
                    if path != store.path_for(operation_id, field="operation id"):
                        raise SandboxCleanupScheduleError(
                            "sandbox cleanup record name is mismatched"
                        )
                except ValidationError as exc:
                    raise SandboxCleanupScheduleError(
                        "sandbox cleanup record is corrupt"
                    ) from exc
                previous = by_operation.get(operation_id)
                if previous is not None:
                    if not _same_identity(previous, record):
                        raise SandboxCleanupScheduleError(
                            "sandbox cleanup journal identity is mismatched"
                        )
                    raise SandboxCleanupScheduleError(
                        "sandbox cleanup duty has conflicting durable copies"
                    )
                if record.state in {"provisioning", "cleanup_pending"}:
                    by_operation[operation_id] = record
        return tuple(
            sorted(
                by_operation.values(),
                key=lambda item: (item.retry_not_before, item.operation_id),
            )[:limit]
        )

    @staticmethod
    def _write_to(records: SandboxFileRecords, record: SandboxCleanupSchedule) -> None:
        records.write(
            record.operation_id,
            field="operation id",
            value=record.model_dump(mode="json"),
        )


def _validate_record(record: SandboxCleanupSchedule) -> SandboxCleanupSchedule:
    try:
        return SandboxCleanupSchedule.model_validate(record.model_dump(mode="json"))
    except ValidationError as exc:
        raise SandboxCleanupScheduleError("sandbox cleanup record is invalid") from exc


def _same_identity(left: SandboxCleanupSchedule, right: SandboxCleanupSchedule) -> bool:
    return left.immutable_identity == right.immutable_identity


def _identity_digest(
    *,
    operation_id: str,
    run_id: str,
    owner_marker: str | None,
    snapshot_digest: str,
) -> str:
    identity = "\0".join((operation_id, run_id, owner_marker or "", snapshot_digest))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _legacy_identity_digest(
    *,
    operation_id: str,
    run_id: str,
    provider_session_ref: str | None,
    snapshot_digest: str,
) -> str:
    identity = "\0".join(
        (operation_id, run_id, provider_session_ref or "", snapshot_digest)
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


__all__ = (
    "FileSandboxCleanupStore",
    "SandboxCleanupSchedule",
    "SandboxCleanupScheduleError",
)

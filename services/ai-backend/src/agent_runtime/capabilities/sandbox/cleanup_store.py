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
    provider_session_ref: str = Field(
        min_length=1, max_length=2048, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
    )
    snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: Literal["cleanup_pending", "cleaned"] = "cleanup_pending"
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
            provider_session_ref=self.provider_session_ref,
            snapshot_digest=self.snapshot_digest,
        )
        if self.immutable_identity is None:
            object.__setattr__(self, "immutable_identity", expected)
            return self
        if self.immutable_identity != expected:
            raise ValueError("sandbox cleanup immutable identity is mismatched")
        return self


_TRANSITIONS: dict[str, frozenset[str]] = {
    "cleanup_pending": frozenset({"cleanup_pending", "cleaned"}),
    "cleaned": frozenset(),
}


class FileSandboxCleanupStore:
    """D3 cleanup schedule at ``sandbox/cleanup/<sha256(operation-id)>.json``."""

    def __init__(self, *, layout: FileStoreLayout) -> None:
        self._records = SandboxFileRecords(layout=layout, category="cleanup")

    async def schedule(self, record: SandboxCleanupSchedule) -> SandboxCleanupSchedule:
        return await asyncio.to_thread(self._schedule, record)

    async def get(self, operation_id: str) -> SandboxCleanupSchedule | None:
        return await asyncio.to_thread(self._load, operation_id)

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
        if record.transition_no != 0 or record.state != "cleanup_pending":
            raise SandboxCleanupScheduleError(
                "sandbox cleanup duty must start at transition zero"
            )
        with self._records.locked(operation_id, field="operation id"):
            previous = self._load(operation_id)
            if previous is None:
                self._write(record)
                return record
            if not _same_identity(previous, record):
                raise SandboxCleanupScheduleError("sandbox cleanup identity changed")
            return previous

    def _transition(
        self, record: SandboxCleanupSchedule, expected_transition_no: int
    ) -> SandboxCleanupSchedule:
        record = _validate_record(record)
        operation_id = canonical_record_key(record.operation_id, field="operation id")
        with self._records.locked(operation_id, field="operation id"):
            previous = self._load(operation_id)
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
            self._write(record)
            return record

    def _load(self, operation_id: str) -> SandboxCleanupSchedule | None:
        operation_id = canonical_record_key(operation_id, field="operation id")
        raw = self._records.read(operation_id, field="operation id")
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
        records: list[SandboxCleanupSchedule] = []
        for path, raw in self._records.iter_records():
            try:
                record = SandboxCleanupSchedule.model_validate(raw)
                operation_id = canonical_record_key(
                    record.operation_id, field="operation id"
                )
                if path != self._records.path_for(operation_id, field="operation id"):
                    raise SandboxCleanupScheduleError(
                        "sandbox cleanup record name is mismatched"
                    )
            except ValidationError as exc:
                raise SandboxCleanupScheduleError(
                    "sandbox cleanup record is corrupt"
                ) from exc
            if record.state == "cleanup_pending":
                records.append(record)
        return tuple(
            sorted(
                records, key=lambda item: (item.retry_not_before, item.operation_id)
            )[:limit]
        )

    def _write(self, record: SandboxCleanupSchedule) -> None:
        self._records.write(
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
    provider_session_ref: str,
    snapshot_digest: str,
) -> str:
    identity = "\0".join((operation_id, run_id, provider_session_ref, snapshot_digest))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


__all__ = (
    "FileSandboxCleanupStore",
    "SandboxCleanupSchedule",
    "SandboxCleanupScheduleError",
)

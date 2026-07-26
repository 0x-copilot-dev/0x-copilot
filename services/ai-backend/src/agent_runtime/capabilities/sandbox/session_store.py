"""Durable filesystem projection for provider sandbox sessions."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agent_runtime.capabilities.sandbox._file_records import (
    SandboxFileRecordError,
    SandboxFileRecords,
    canonical_record_key,
)
from agent_runtime.capabilities.sandbox.contracts import ManagedSandboxSession
from agent_runtime.capabilities.sandbox.ports import SandboxSessionStore
from runtime_adapters.file._paths import FileStoreLayout


class SandboxSessionStoreError(SandboxFileRecordError):
    """A durable sandbox session projection is malformed or inconsistent."""


class _SessionRecord(BaseModel):
    """Versioned current projection of one provider session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    record_type: Literal["sandbox_session"] = "sandbox_session"
    revision: int = Field(ge=0)
    expected_prior_state: str | None = None
    session: ManagedSandboxSession
    immutable_identity: str | None = None

    @model_validator(mode="after")
    def _bind_immutable_identity(self) -> "_SessionRecord":
        expected = _identity_digest(self.session)
        if self.immutable_identity is None:
            object.__setattr__(self, "immutable_identity", expected)
            return self
        if self.immutable_identity != expected:
            raise ValueError("sandbox session immutable identity is mismatched")
        return self


_STATE_TRANSITIONS: dict[str, frozenset[str]] = {
    "active": frozenset({"active", "terminating", "cleanup_pending", "deleted"}),
    "terminating": frozenset({"terminating", "cleanup_pending", "deleted"}),
    "cleanup_pending": frozenset({"cleanup_pending", "terminating", "deleted"}),
    "deleted": frozenset({"deleted"}),
}


class FileSandboxSessionStore(SandboxSessionStore):
    """Session authority stored at ``sandbox/sessions/<sha256(session-id)>.json``."""

    def __init__(self, *, layout: FileStoreLayout) -> None:
        self._records = SandboxFileRecords(layout=layout, category="sessions")

    async def upsert(self, session: ManagedSandboxSession) -> None:
        await asyncio.to_thread(self._upsert, session)

    async def get(self, session_id: str) -> ManagedSandboxSession | None:
        record = await asyncio.to_thread(self._load, session_id)
        return record.session if record is not None else None

    async def list_non_terminal(self) -> tuple[ManagedSandboxSession, ...]:
        return await asyncio.to_thread(self._list_non_terminal)

    async def delete(self, session_id: str) -> None:
        await asyncio.to_thread(self._delete, session_id)

    def _upsert(self, session: ManagedSandboxSession) -> None:
        session_id = canonical_record_key(session.session_id, field="session id")
        with self._records.locked(session_id, field="session id"):
            previous = self._load(session_id)
            if previous is None:
                replacement = _SessionRecord(revision=0, session=session)
            else:
                if not _same_identity(previous.session, session):
                    raise SandboxSessionStoreError("sandbox session identity changed")
                if previous.session == session:
                    return
                if (
                    session.cleanup_state
                    not in _STATE_TRANSITIONS[previous.session.cleanup_state]
                ):
                    raise SandboxSessionStoreError(
                        "sandbox session transition is invalid"
                    )
                replacement = _SessionRecord(
                    revision=previous.revision + 1,
                    expected_prior_state=previous.session.cleanup_state,
                    session=session,
                )
            self._records.write(
                session_id,
                field="session id",
                value=replacement.model_dump(mode="json"),
            )

    def _load(self, session_id: str) -> _SessionRecord | None:
        session_id = canonical_record_key(session_id, field="session id")
        raw = self._records.read(session_id, field="session id")
        if raw is None:
            return None
        try:
            record = _SessionRecord.model_validate(raw)
            if record.session.session_id != session_id:
                raise SandboxSessionStoreError(
                    "sandbox session record identity is mismatched"
                )
            if record.revision == 0 and record.expected_prior_state is not None:
                raise SandboxSessionStoreError(
                    "sandbox session creation has a prior state"
                )
            if record.revision > 0 and record.expected_prior_state is None:
                raise SandboxSessionStoreError(
                    "sandbox session transition lacks a prior state"
                )
        except ValidationError as exc:
            raise SandboxSessionStoreError("sandbox session record is corrupt") from exc
        return record

    def _list_non_terminal(self) -> tuple[ManagedSandboxSession, ...]:
        sessions: list[ManagedSandboxSession] = []
        for path, raw in self._records.iter_records():
            try:
                record = _SessionRecord.model_validate(raw)
                session_id = canonical_record_key(
                    record.session.session_id, field="session id"
                )
                if path != self._records.path_for(session_id, field="session id"):
                    raise SandboxSessionStoreError(
                        "sandbox session record name is mismatched"
                    )
                if record.revision == 0 and record.expected_prior_state is not None:
                    raise SandboxSessionStoreError(
                        "sandbox session creation has a prior state"
                    )
                if record.revision > 0 and record.expected_prior_state is None:
                    raise SandboxSessionStoreError(
                        "sandbox session transition lacks a prior state"
                    )
            except ValidationError as exc:
                raise SandboxSessionStoreError(
                    "sandbox session record is corrupt"
                ) from exc
            if record.session.cleanup_state != "deleted":
                sessions.append(record.session)
        return tuple(
            sorted(sessions, key=lambda item: (item.created_at, item.session_id))
        )

    def _delete(self, session_id: str) -> None:
        session_id = canonical_record_key(session_id, field="session id")
        with self._records.locked(session_id, field="session id"):
            self._records.remove(session_id, field="session id")


def _same_identity(left: ManagedSandboxSession, right: ManagedSandboxSession) -> bool:
    return _identity_digest(left) == _identity_digest(right)


def _identity_digest(session: ManagedSandboxSession) -> str:
    identity = "\0".join(
        (
            session.session_id,
            session.provider.value,
            session.provider_session_ref,
            session.owner_tag,
            session.created_at.isoformat(),
            session.expires_at.isoformat(),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


__all__ = ("FileSandboxSessionStore", "SandboxSessionStoreError")

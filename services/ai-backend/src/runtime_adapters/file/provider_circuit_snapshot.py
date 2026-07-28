"""Optional capped, atomic desktop snapshot for process-local circuit health."""

from __future__ import annotations

from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.execution.model_invocation.circuit_health import (
    ProviderCircuitSnapshot,
)


class CircuitSnapshotProfile(StrEnum):
    SINGLE_USER_DESKTOP = "single_user_desktop"


class CircuitSnapshotCapacityError(ValueError):
    """Snapshot exceeds its deliberately small desktop persistence budget."""


class _SnapshotEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "provider-circuit-file.v1"
    payload: ProviderCircuitSnapshot
    payload_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class DesktopProviderCircuitSnapshotStore:
    """Persist only content-free circuit facts; corruption restores no state."""

    def __init__(
        self,
        path: Path,
        *,
        profile: CircuitSnapshotProfile,
        max_bytes: int = 256 * 1024,
        max_entries: int = 512,
    ) -> None:
        if profile is not CircuitSnapshotProfile.SINGLE_USER_DESKTOP:
            raise ValueError("provider circuit snapshots are desktop-only")
        if max_bytes < 1024 or max_bytes > 1024 * 1024:
            raise ValueError("max_bytes must be between 1 KiB and 1 MiB")
        if max_entries < 1 or max_entries > 4096:
            raise ValueError("max_entries must be between 1 and 4096")
        self._path = path
        self._max_bytes = max_bytes
        self._max_entries = max_entries

    def save(self, snapshot: ProviderCircuitSnapshot) -> None:
        if len(snapshot.entries) > self._max_entries:
            raise CircuitSnapshotCapacityError("snapshot entry capacity exceeded")
        payload = snapshot.model_dump(mode="json")
        envelope = {
            "schema_version": "provider-circuit-file.v1",
            "payload": payload,
            "payload_digest": self._digest(payload),
        }
        encoded = json.dumps(
            envelope,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(encoded) > self._max_bytes:
            raise CircuitSnapshotCapacityError("snapshot byte capacity exceeded")

        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.{uuid4().hex}.tmp")
        try:
            with open(temporary, "xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._path)
            self._fsync_parent()
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def load(self) -> ProviderCircuitSnapshot | None:
        """Return ``None`` for absent, oversized, corrupt, or incompatible state."""

        try:
            if self._path.stat().st_size > self._max_bytes:
                return None
            raw = self._path.read_bytes()
            envelope = _SnapshotEnvelope.model_validate_json(raw)
        except (FileNotFoundError, OSError, ValueError):
            return None
        payload = envelope.payload.model_dump(mode="json")
        if envelope.payload_digest != self._digest(payload):
            return None
        if len(envelope.payload.entries) > self._max_entries:
            return None
        return envelope.payload

    @staticmethod
    def _digest(payload: object) -> str:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(canonical).hexdigest()}"

    def _fsync_parent(self) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(self._path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


__all__ = (
    "CircuitSnapshotCapacityError",
    "CircuitSnapshotProfile",
    "DesktopProviderCircuitSnapshotStore",
)

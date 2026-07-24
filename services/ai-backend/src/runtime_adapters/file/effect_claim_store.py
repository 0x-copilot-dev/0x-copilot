"""Durable, cross-process effect claims for the file-backed runtime.

The file runtime has no database transaction around an external executor.  This
adapter therefore makes the claim itself the durable boundary: it creates one
JSON record per tenant/executor/idempotency identity before an executor may
apply an effect.  A process-wide advisory lock makes the read-or-create sequence
safe across desktop worker processes, while ``O_CREAT | O_EXCL`` is the atomic
create primitive.  If a process crashes after reserving a filename but before a
valid record is durable, later callers fail closed with ``EffectClaimStorageError``
instead of treating that idempotency key as available.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar

from pydantic import ValidationError

from agent_runtime.effects.claims import (
    EffectClaim,
    EffectClaimAcquisition,
    EffectClaimConflict,
    EffectClaimNotFound,
    EffectClaimState,
    EffectClaimStorageError,
    validate_claim_transition,
)
from agent_runtime.surfaces_v2.ledger_models import EffectExecutorKind
from runtime_adapters.file._advisory_lock import acquire_exclusive, release_exclusive


class FileEffectClaimStore:
    """File-native implementation of the durable ``EffectClaimStore`` port.

    The adapter keeps all claim records under ``effect_claims/`` below the file
    store root.  Filenames are SHA-256 digests of the tenant-scoped idempotency
    identity, so raw tenant ids and keys never appear in filenames.  The
    original identity remains in the validated record and is checked on every
    read to make corruption and impossible digest collisions fail closed.
    """

    _SUBDIR: ClassVar[str] = "effect_claims"
    _LOCK_FILENAME: ClassVar[str] = ".claims.lock"
    _JSON_SUFFIX: ClassVar[str] = ".json"
    _TEMP_SUFFIX: ClassVar[str] = ".tmp"
    _DIR_MODE: ClassVar[int] = 0o700
    _FILE_MODE: ClassVar[int] = 0o600

    def __init__(self, root: Path | str) -> None:
        base = Path(root).expanduser().resolve()
        self._dir = base if base.name == self._SUBDIR else base / self._SUBDIR
        self._dir.mkdir(mode=self._DIR_MODE, parents=True, exist_ok=True)
        self._lock_path = self._dir / self._LOCK_FILENAME
        self._lock = asyncio.Lock()

    async def claim(self, *, claim: EffectClaim) -> EffectClaimAcquisition:
        """Atomically reserve or load a tenant/executor idempotency claim."""

        async with self._lock:
            with self._exclusive_lock():
                path = self._path_for(
                    org_id=claim.org_id,
                    executor=claim.executor,
                    idempotency_key=claim.idempotency_key,
                )
                try:
                    self._atomic_create(path=path, claim=claim)
                except FileExistsError:
                    existing = self._read_expected(path=path, candidate=claim)
                    if not existing.same_request_as(claim):
                        raise EffectClaimConflict()
                    return EffectClaimAcquisition(created=False, claim=existing)
                return EffectClaimAcquisition(created=True, claim=claim)

    async def get(
        self,
        *,
        org_id: str,
        executor: EffectExecutorKind,
        idempotency_key: str,
    ) -> EffectClaim | None:
        async with self._lock:
            with self._exclusive_lock():
                path = self._path_for(
                    org_id=org_id,
                    executor=executor,
                    idempotency_key=idempotency_key,
                )
                return self._read_expected_identity(
                    path=path,
                    org_id=org_id,
                    executor=executor,
                    idempotency_key=idempotency_key,
                    absent_is_none=True,
                )

    async def get_by_claim_id(
        self, *, org_id: str, claim_id: str
    ) -> EffectClaim | None:
        async with self._lock:
            with self._exclusive_lock():
                for path in self._claim_paths():
                    claim = self._read(path=path)
                    if claim.org_id == org_id and claim.claim_id == claim_id:
                        return claim
                return None

    async def update(self, *, claim: EffectClaim) -> EffectClaim:
        async with self._lock:
            with self._exclusive_lock():
                path = self._path_for(
                    org_id=claim.org_id,
                    executor=claim.executor,
                    idempotency_key=claim.idempotency_key,
                )
                previous = self._read_expected_identity(
                    path=path,
                    org_id=claim.org_id,
                    executor=claim.executor,
                    idempotency_key=claim.idempotency_key,
                    absent_is_none=True,
                )
                if previous is None or previous.claim_id != claim.claim_id:
                    raise EffectClaimNotFound()
                validate_claim_transition(previous=previous, replacement=claim)
                self._atomic_replace(path=path, claim=claim)
                return claim

    async def list_incomplete(
        self, *, org_id: str | None = None, limit: int = 100
    ) -> Sequence[EffectClaim]:
        if limit < 1:
            return ()
        unresolved = {EffectClaimState.CLAIMED, EffectClaimState.INDETERMINATE}
        async with self._lock:
            with self._exclusive_lock():
                claims = [
                    claim
                    for path in self._claim_paths()
                    if (claim := self._read(path=path)).state in unresolved
                    and (org_id is None or claim.org_id == org_id)
                ]
        return tuple(
            sorted(claims, key=lambda claim: (claim.created_at, claim.claim_id))[:limit]
        )

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        """Hold the one file-store lock across a read-modify-write operation."""

        try:
            fd = os.open(
                self._lock_path,
                os.O_CREAT | os.O_RDWR,
                self._FILE_MODE,
            )
            acquired = False
            try:
                acquire_exclusive(fd)
                acquired = True
                yield
            finally:
                if acquired:
                    release_exclusive(fd)
                os.close(fd)
        except OSError as exc:
            raise EffectClaimStorageError() from exc

    def _atomic_create(self, *, path: Path, claim: EffectClaim) -> None:
        """Durably create an immutable claim record, never replacing an existing one."""

        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, self._FILE_MODE)
        except FileExistsError:
            raise
        except OSError as exc:
            raise EffectClaimStorageError() from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(claim.model_dump(mode="json"), handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            self._sync_directory()
        except (OSError, TypeError, ValueError) as exc:
            # Do not remove a partially created marker.  It is now a durable
            # uncertainty and must block any possible duplicate external effect.
            raise EffectClaimStorageError() from exc

    def _atomic_replace(self, *, path: Path, claim: EffectClaim) -> None:
        tmp = path.with_name(f".{path.name}.{os.getpid()}{self._TEMP_SUFFIX}")
        try:
            with open(tmp, "x", encoding="utf-8") as handle:
                json.dump(claim.model_dump(mode="json"), handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            try:
                path.chmod(self._FILE_MODE)
            except OSError:
                pass
            self._sync_directory()
        except (OSError, TypeError, ValueError) as exc:
            raise EffectClaimStorageError() from exc
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _read_expected(self, *, path: Path, candidate: EffectClaim) -> EffectClaim:
        return self._read_expected_identity(
            path=path,
            org_id=candidate.org_id,
            executor=candidate.executor,
            idempotency_key=candidate.idempotency_key,
            absent_is_none=False,
        )

    def _read_expected_identity(
        self,
        *,
        path: Path,
        org_id: str,
        executor: EffectExecutorKind,
        idempotency_key: str,
        absent_is_none: bool,
    ) -> EffectClaim | None:
        if not path.exists():
            if absent_is_none:
                return None
            raise EffectClaimStorageError()
        claim = self._read(path=path)
        if (
            claim.org_id != org_id
            or claim.executor is not executor
            or claim.idempotency_key != idempotency_key
        ):
            raise EffectClaimStorageError()
        return claim

    def _read(self, *, path: Path) -> EffectClaim:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("effect claim record is not an object")
            return EffectClaim.model_validate(raw)
        except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise EffectClaimStorageError() from exc

    def _claim_paths(self) -> Iterator[Path]:
        yield from sorted(self._dir.glob(f"*{self._JSON_SUFFIX}"))

    def _path_for(
        self, *, org_id: str, executor: EffectExecutorKind, idempotency_key: str
    ) -> Path:
        identity = "\0".join((org_id, executor.value, idempotency_key))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return self._dir / f"{digest}{self._JSON_SUFFIX}"

    def _sync_directory(self) -> None:
        """Persist directory entries where the host filesystem permits it."""

        try:
            fd = os.open(self._dir, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)


__all__ = ["FileEffectClaimStore"]

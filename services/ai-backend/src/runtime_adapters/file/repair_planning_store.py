"""Durable file-native state for D12 repair-planning snapshots.

Records contain only the redacted D12 snapshot contract and its safe planner
decisions.  Tenant and snapshot IDs are hashed for filenames; a corrupt or
scope-mismatched file is a fail-closed state error, never a fresh plan.
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
from uuid import uuid4

from pydantic import ValidationError

from agent_runtime.effects.claims import EffectClaimScanCursor
from agent_runtime.surfaces_v2.repair_planning import (
    RepairPlanningSnapshot,
    RepairPlanningSnapshotState,
    RepairPlanningStateError,
    validate_repair_plan_page,
)
from agent_runtime.surfaces_v2.repair_reconciliation import RepairDecision, RepairPlan
from runtime_adapters.file._advisory_lock import acquire_exclusive, release_exclusive


class FileRepairPlanningSnapshotStore:
    """Atomic file-backed D12 snapshot store for the desktop profile."""

    _SUBDIR: ClassVar[str] = "repair_planning"
    _LOCK_FILENAME: ClassVar[str] = ".repair-planning.lock"
    _SCAN_CURSOR_FILENAME: ClassVar[str] = ".effect-claim-scan.json"
    _DIR_MODE: ClassVar[int] = 0o700
    _FILE_MODE: ClassVar[int] = 0o600

    def __init__(self, *, root: str | Path) -> None:
        base = Path(root).expanduser().resolve()
        self._dir = base if base.name == self._SUBDIR else base / self._SUBDIR
        self._dir.mkdir(mode=self._DIR_MODE, parents=True, exist_ok=True)
        self._lock_path = self._dir / self._LOCK_FILENAME
        self._lock = asyncio.Lock()

    async def load_or_create(
        self, *, snapshot: RepairPlanningSnapshot
    ) -> RepairPlanningSnapshotState:
        async with self._lock:
            with self._exclusive_lock():
                path = self._path(snapshot.tenant_id, snapshot.snapshot_id)
                if path.exists():
                    state, _outcomes = self._read(path=path)
                    if not state.snapshot.same_persisted_snapshot_as(snapshot):
                        raise RepairPlanningStateError()
                    return state
                state = RepairPlanningSnapshotState(snapshot=snapshot)
                self._write(path=path, state=state, outcomes=())
                return state

    async def load(
        self, *, tenant_id: str, snapshot_id: str
    ) -> RepairPlanningSnapshotState | None:
        async with self._lock:
            with self._exclusive_lock():
                path = self._path(tenant_id, snapshot_id)
                if not path.exists():
                    return None
                state, _outcomes = self._read(path=path)
                self._assert_scope(state, tenant_id=tenant_id, snapshot_id=snapshot_id)
                return state

    async def advance(
        self,
        *,
        tenant_id: str,
        snapshot_id: str,
        expected_after_candidate_id: str | None,
        plan: RepairPlan,
    ) -> bool:
        async with self._lock:
            with self._exclusive_lock():
                path = self._path(tenant_id, snapshot_id)
                if not path.exists():
                    raise RepairPlanningStateError()
                state, outcomes = self._read(path=path)
                self._assert_scope(state, tenant_id=tenant_id, snapshot_id=snapshot_id)
                if state.after_candidate_id != expected_after_candidate_id:
                    return False
                validate_repair_plan_page(
                    state=state,
                    expected_after_candidate_id=expected_after_candidate_id,
                    plan=plan,
                )
                indexed = {decision.candidate_id: decision for decision in outcomes}
                for decision in plan.decisions:
                    existing = indexed.get(decision.candidate_id)
                    if existing is not None and existing != decision:
                        raise RepairPlanningStateError()
                    indexed[decision.candidate_id] = decision
                next_after = (
                    plan.next_cursor.after_candidate_id
                    if plan.next_cursor is not None
                    else (plan.decisions[-1].candidate_id if plan.decisions else None)
                )
                updated = RepairPlanningSnapshotState(
                    snapshot=state.snapshot,
                    after_candidate_id=next_after,
                    completed=not plan.has_more,
                )
                self._write(
                    path=path,
                    state=updated,
                    outcomes=tuple(indexed[key] for key in sorted(indexed)),
                )
                return True

    async def list_outcomes(
        self, *, tenant_id: str, snapshot_id: str
    ) -> Sequence[RepairDecision]:
        async with self._lock:
            with self._exclusive_lock():
                path = self._path(tenant_id, snapshot_id)
                if not path.exists():
                    return ()
                state, outcomes = self._read(path=path)
                self._assert_scope(state, tenant_id=tenant_id, snapshot_id=snapshot_id)
                return outcomes

    async def load_effect_claim_scan_cursor(self) -> EffectClaimScanCursor | None:
        async with self._lock:
            with self._exclusive_lock():
                return self._read_scan_cursor()

    async def advance_effect_claim_scan_cursor(
        self,
        *,
        expected: EffectClaimScanCursor | None,
        next_cursor: EffectClaimScanCursor | None,
    ) -> bool:
        async with self._lock:
            with self._exclusive_lock():
                if self._read_scan_cursor() != expected:
                    return False
                self._write_scan_cursor(next_cursor)
                return True

    def _read(
        self, *, path: Path
    ) -> tuple[RepairPlanningSnapshotState, tuple[RepairDecision, ...]]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError
            state = RepairPlanningSnapshotState.model_validate(raw.get("state"))
            outcomes_raw = raw.get("outcomes", [])
            if not isinstance(outcomes_raw, list):
                raise ValueError
            outcomes = tuple(
                RepairDecision.model_validate(value) for value in outcomes_raw
            )
            ids = tuple(decision.candidate_id for decision in outcomes)
            if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
                raise ValueError
            return state, outcomes
        except (
            OSError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
            ValidationError,
        ) as exc:
            raise RepairPlanningStateError() from exc

    def _write(
        self,
        *,
        path: Path,
        state: RepairPlanningSnapshotState,
        outcomes: Sequence[RepairDecision],
    ) -> None:
        payload = {
            "state": state.model_dump(mode="json"),
            "outcomes": [decision.model_dump(mode="json") for decision in outcomes],
        }
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            encoded = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            fd = os.open(
                temporary,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                self._FILE_MODE,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            try:
                path.chmod(self._FILE_MODE)
            except OSError:
                pass
            self._sync_directory()
        except (OSError, TypeError, ValueError) as exc:
            raise RepairPlanningStateError() from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _read_scan_cursor(self) -> EffectClaimScanCursor | None:
        path = self._dir / self._SCAN_CURSOR_FILENAME
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or set(raw) != {"cursor"}:
                raise ValueError
            cursor = raw["cursor"]
            return (
                None if cursor is None else EffectClaimScanCursor.model_validate(cursor)
            )
        except (
            OSError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
            ValidationError,
        ) as exc:
            raise RepairPlanningStateError() from exc

    def _write_scan_cursor(self, cursor: EffectClaimScanCursor | None) -> None:
        path = self._dir / self._SCAN_CURSOR_FILENAME
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            encoded = json.dumps(
                {
                    "cursor": (
                        cursor.model_dump(mode="json") if cursor is not None else None
                    )
                },
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            fd = os.open(
                temporary,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                self._FILE_MODE,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            try:
                path.chmod(self._FILE_MODE)
            except OSError:
                pass
            self._sync_directory()
        except (OSError, TypeError, ValueError) as exc:
            raise RepairPlanningStateError() from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _path(self, tenant_id: str, snapshot_id: str) -> Path:
        digest = hashlib.sha256(f"{tenant_id}\0{snapshot_id}".encode()).hexdigest()
        return self._dir / f"{digest}.json"

    @staticmethod
    def _assert_scope(
        state: RepairPlanningSnapshotState, *, tenant_id: str, snapshot_id: str
    ) -> None:
        if (
            state.snapshot.tenant_id != tenant_id
            or state.snapshot.snapshot_id != snapshot_id
        ):
            raise RepairPlanningStateError()

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
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
            raise RepairPlanningStateError() from exc

    def _sync_directory(self) -> None:
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


__all__ = ("FileRepairPlanningSnapshotStore",)

"""Filesystem durability and recovery tests for sandbox cleanup schedules."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import stat

import pytest

from agent_runtime.capabilities.sandbox import _file_records
from agent_runtime.capabilities.sandbox._file_records import SandboxFileRecordError
from agent_runtime.capabilities.sandbox.cleanup_store import (
    FileSandboxCleanupStore,
    SandboxCleanupSchedule,
    SandboxCleanupScheduleError,
)
from runtime_adapters.file._paths import FileStoreLayout


def _schedule(*, now: datetime | None = None) -> SandboxCleanupSchedule:
    timestamp = now or datetime.now(UTC)
    return SandboxCleanupSchedule(
        operation_id="operation-1",
        run_id="run-1",
        provider_session_ref="provider-session-1",
        snapshot_digest="a" * 64,
        retry_not_before=timestamp,
        created_at=timestamp,
        updated_at=timestamp,
    )


def _record_path(layout: FileStoreLayout, operation_id: str) -> Path:
    return layout.root / "sandbox" / "cleanup" / f"{layout.safe_key(operation_id)}.json"


@pytest.mark.asyncio
class TestFileSandboxCleanupStore:
    async def test_fsyncs_the_record_and_parent_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int] = []
        original = _file_records.os.fsync

        def spy(descriptor: int) -> None:
            calls.append(descriptor)
            original(descriptor)

        monkeypatch.setattr(_file_records.os, "fsync", spy)
        store = FileSandboxCleanupStore(layout=FileStoreLayout(tmp_path / "agent-data"))

        await store.schedule(_schedule())

        assert len(calls) >= 2

    async def test_schedules_private_canonical_duty_and_recovers_after_reopen(
        self, tmp_path: Path
    ) -> None:
        layout = FileStoreLayout(tmp_path / "agent-data" / "v1")
        duty = _schedule()
        store = FileSandboxCleanupStore(layout=layout)

        assert await store.schedule(duty) == duty

        path = _record_path(layout, duty.operation_id)
        assert duty.operation_id not in path.name
        assert stat.S_IMODE((layout.root / "sandbox").stat().st_mode) == 0o700
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["schema_version"] == 1
        assert raw["transition_no"] == 0
        assert raw["immutable_identity"]

        reopened = FileSandboxCleanupStore(layout=FileStoreLayout(layout.root))
        assert await reopened.list_pending() == (duty,)

    async def test_transition_is_compare_and_swap_and_preserves_cleanup_evidence(
        self, tmp_path: Path
    ) -> None:
        layout = FileStoreLayout(tmp_path / "agent-data")
        duty = _schedule()
        store = FileSandboxCleanupStore(layout=layout)
        await store.schedule(duty)
        cleaned = duty.model_copy(
            update={
                "state": "cleaned",
                "transition_no": 1,
                "attempts": 1,
                "updated_at": duty.updated_at + timedelta(seconds=1),
            }
        )

        assert (
            await store.transition(record=cleaned, expected_transition_no=0) == cleaned
        )
        assert await store.list_pending() == ()
        assert await store.get(duty.operation_id) == cleaned
        with pytest.raises(SandboxCleanupScheduleError):
            await store.transition(record=cleaned, expected_transition_no=1)

    async def test_serializes_duplicate_schedule_and_fails_closed_on_corruption(
        self, tmp_path: Path
    ) -> None:
        layout = FileStoreLayout(tmp_path / "agent-data")
        duty = _schedule()

        results = await asyncio.gather(
            *(
                FileSandboxCleanupStore(layout=FileStoreLayout(layout.root)).schedule(
                    duty
                )
                for _ in range(8)
            )
        )

        assert results == [duty] * 8
        path = _record_path(layout, duty.operation_id)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["run_id"] = "substituted-run"
        path.write_text(json.dumps(raw), encoding="utf-8")
        path.chmod(0o600)

        store = FileSandboxCleanupStore(layout=layout)
        with pytest.raises(SandboxCleanupScheduleError):
            await store.get(duty.operation_id)
        with pytest.raises(SandboxFileRecordError):
            await store.get("../escape")

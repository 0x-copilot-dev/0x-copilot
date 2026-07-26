"""Filesystem durability and containment tests for sandbox session projections."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import stat

import pytest

from agent_runtime.capabilities.sandbox._file_records import SandboxFileRecordError
from agent_runtime.capabilities.sandbox.contracts import (
    ManagedSandboxSession,
    SandboxProviderId,
)
from agent_runtime.capabilities.sandbox.session_store import (
    FileSandboxSessionStore,
    SandboxSessionStoreError,
)
from runtime_adapters.file._paths import FileStoreLayout


def _session() -> ManagedSandboxSession:
    created_at = datetime.now(UTC)
    return ManagedSandboxSession(
        session_id="session-1",
        provider=SandboxProviderId.LANGSMITH,
        provider_session_ref="provider-session-1",
        owner_tag="sandbox-owner-1",
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=15),
    )


def _record_path(layout: FileStoreLayout, session_id: str) -> Path:
    return layout.root / "sandbox" / "sessions" / f"{layout.safe_key(session_id)}.json"


@pytest.mark.asyncio
class TestFileSandboxSessionStore:
    async def test_persists_private_canonical_session_projection(
        self, tmp_path: Path
    ) -> None:
        layout = FileStoreLayout(tmp_path / "agent-data" / "v1")
        session = _session()
        store = FileSandboxSessionStore(layout=layout)

        await store.upsert(session)

        path = _record_path(layout, session.session_id)
        assert session.session_id not in path.name
        assert stat.S_IMODE((layout.root / "sandbox").stat().st_mode) == 0o700
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        persisted = json.loads(path.read_text(encoding="utf-8"))
        assert persisted["schema_version"] == 1
        assert persisted["revision"] == 0
        assert persisted["session"]["owner_tag"] == session.owner_tag

        reopened = FileSandboxSessionStore(layout=FileStoreLayout(layout.root))
        assert await reopened.get(session.session_id) == session

    async def test_updates_cleanup_state_monotonically_and_rejects_owner_change(
        self, tmp_path: Path
    ) -> None:
        layout = FileStoreLayout(tmp_path / "agent-data")
        session = _session()
        store = FileSandboxSessionStore(layout=layout)
        await store.upsert(session)
        pending = session.with_state("cleanup_pending")

        await store.upsert(pending)

        assert await store.list_non_terminal() == (pending,)
        with pytest.raises(SandboxSessionStoreError):
            await store.upsert(session.model_copy(update={"owner_tag": "other"}))

        await store.delete(session.session_id)
        assert await store.get(session.session_id) is None

    async def test_refuses_corruption_traversal_and_symlink_substitution(
        self, tmp_path: Path
    ) -> None:
        layout = FileStoreLayout(tmp_path / "agent-data")
        session = _session()
        store = FileSandboxSessionStore(layout=layout)
        await store.upsert(session)
        path = _record_path(layout, session.session_id)
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["session"]["owner_tag"] = "substituted-owner"
        path.write_text(json.dumps(raw), encoding="utf-8")
        path.chmod(0o600)

        with pytest.raises(SandboxSessionStoreError):
            await store.get(session.session_id)
        with pytest.raises(SandboxFileRecordError):
            await store.get("../escape")

        path.unlink()
        outside = tmp_path / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        path.symlink_to(outside)
        with pytest.raises(SandboxFileRecordError):
            await store.get(session.session_id)

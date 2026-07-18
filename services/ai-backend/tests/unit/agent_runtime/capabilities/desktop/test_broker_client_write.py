"""Unit tests for the ``DesktopBrokerClient`` WRITE surface + run-context lifecycle.

Covers each mutating route round-trip against the in-memory fake broker, the
per-route grant-MODE gate surfacing as typed :class:`BrokerError` subclasses
(``read_only`` denies writes; ``read_write_no_delete`` denies delete/move), and
the ``/v1/runs/{begin,end}`` lifecycle — including that a mutating op carrying a
``run_capability_context`` authorizes against the PINNED snapshot rather than
live grant state.
"""

from __future__ import annotations

import base64

import pytest

from agent_runtime.capabilities.desktop.broker_client import (
    BrokerNotFoundError,
    BrokerPermissionDeniedError,
)
from tests.unit.agent_runtime.capabilities.desktop.fakes import (
    FakeBrokerFs,
    RecordingBroker,
)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class BrokerWriteMixin:
    """Build a fake broker + client for a single grant at a chosen mode."""

    GRANT = "grant-rw"

    @classmethod
    def broker(
        cls, mode: str, files: dict[str, bytes] | None = None
    ) -> RecordingBroker:
        return RecordingBroker(
            grants={cls.GRANT: FakeBrokerFs(files=dict(files or {}))},
            grant_meta={cls.GRANT: {"mode": mode}},
        )


class TestBrokerWriteOps(BrokerWriteMixin):
    """The five mutating routes round-trip + parse when the grant mode permits."""

    async def test_write_creates_new_file(self) -> None:
        broker = self.broker("read_write_no_delete")
        client = broker.client()
        result = await client.write(self.GRANT, "new.txt", _b64(b"hello"))
        assert result.path == "new.txt"
        assert result.bytes_written == 5
        assert result.created is True
        assert broker.grants[self.GRANT].files["new.txt"] == b"hello"

    async def test_write_overwrites_reports_created_false(self) -> None:
        broker = self.broker("read_write", files={"a.txt": b"old"})
        result = await broker.client().write(self.GRANT, "a.txt", _b64(b"new"))
        assert result.created is False

    async def test_edit_replaces_existing_file(self) -> None:
        broker = self.broker("read_write_no_delete", files={"a.txt": b"old"})
        result = await broker.client().edit(self.GRANT, "a.txt", _b64(b"brand new"))
        assert result.path == "a.txt"
        assert result.bytes_written == len(b"brand new")
        assert broker.grants[self.GRANT].files["a.txt"] == b"brand new"

    async def test_edit_missing_file_is_not_found(self) -> None:
        broker = self.broker("read_write_no_delete")
        with pytest.raises(BrokerNotFoundError):
            await broker.client().edit(self.GRANT, "missing.txt", _b64(b"x"))

    async def test_mkdir_and_delete_and_move(self) -> None:
        broker = self.broker("read_write", files={"a.txt": b"x"})
        client = broker.client()
        mk = await client.mkdir(self.GRANT, "sub")
        assert mk.created is True
        mv = await client.move(self.GRANT, "a.txt", "b.txt")
        assert (mv.from_path, mv.to_path, mv.type) == ("a.txt", "b.txt", "file")
        rm = await client.delete(self.GRANT, "b.txt")
        assert (rm.path, rm.type) == ("b.txt", "file")


class TestBrokerModeGate(BrokerWriteMixin):
    """Broker MODE-gating surfaces as typed :class:`BrokerPermissionDeniedError`."""

    async def test_read_only_denies_write(self) -> None:
        broker = self.broker("read_only")
        with pytest.raises(BrokerPermissionDeniedError):
            await broker.client().write(self.GRANT, "a.txt", _b64(b"x"))

    async def test_read_only_denies_edit(self) -> None:
        broker = self.broker("read_only", files={"a.txt": b"x"})
        with pytest.raises(BrokerPermissionDeniedError):
            await broker.client().edit(self.GRANT, "a.txt", _b64(b"y"))

    async def test_read_write_no_delete_denies_delete(self) -> None:
        broker = self.broker("read_write_no_delete", files={"a.txt": b"x"})
        with pytest.raises(BrokerPermissionDeniedError):
            await broker.client().delete(self.GRANT, "a.txt")

    async def test_read_write_no_delete_denies_move(self) -> None:
        broker = self.broker("read_write_no_delete", files={"a.txt": b"x"})
        with pytest.raises(BrokerPermissionDeniedError):
            await broker.client().move(self.GRANT, "a.txt", "b.txt")

    async def test_read_write_no_delete_allows_write(self) -> None:
        broker = self.broker("read_write_no_delete")
        result = await broker.client().write(self.GRANT, "a.txt", _b64(b"x"))
        assert result.created is True


class TestRunContextLifecycle(BrokerWriteMixin):
    """``/v1/runs/{begin,end}`` mint/release + authorize against the pinned snapshot."""

    async def test_begin_returns_opaque_context(self) -> None:
        broker = self.broker("read_write")
        binding = await broker.client().runs_begin()
        assert binding.run_capability_context.startswith("rcx_")
        assert binding.run_capability_context in broker.run_contexts

    async def test_end_releases_context(self) -> None:
        broker = self.broker("read_write")
        client = broker.client()
        binding = await client.runs_begin()
        released = await client.runs_end(binding.run_capability_context)
        assert released.released is True
        assert binding.run_capability_context not in broker.run_contexts

    async def test_end_unknown_context_is_not_released(self) -> None:
        broker = self.broker("read_write")
        released = await broker.client().runs_end("rcx_never_minted")
        assert released.released is False

    async def test_mutation_carries_context_and_uses_pinned_mode(self) -> None:
        # Pin while writable, then narrow the LIVE grant to read-only. The op
        # carrying the pinned context still authorizes off the pinned mode.
        broker = self.broker("read_write", files={"a.txt": b"old"})
        client = broker.client()
        binding = await client.runs_begin()
        broker.grant_meta[self.GRANT]["mode"] = "read_only"

        result = await client.write(
            self.GRANT,
            "a.txt",
            _b64(b"new"),
            run_capability_context=binding.run_capability_context,
        )
        assert result.created is False
        # The write request body carried the context.
        write_bodies = [
            b for route, _h, b in broker.requests if route == "/v1/fs/write"
        ]
        assert write_bodies[-1]["run_capability_context"] == (
            binding.run_capability_context
        )

    async def test_live_write_denied_after_revoke_without_context(self) -> None:
        # Same narrowing, but WITHOUT a context → resolves against live state,
        # which is now read-only → typed permission error.
        broker = self.broker("read_write", files={"a.txt": b"old"})
        client = broker.client()
        await client.runs_begin()
        broker.grant_meta[self.GRANT]["mode"] = "read_only"
        with pytest.raises(BrokerPermissionDeniedError):
            await client.write(self.GRANT, "a.txt", _b64(b"new"))

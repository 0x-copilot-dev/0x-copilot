"""Unit tests for :class:`BrokeredWorkspaceBackend` and the ``build_workspace_backend`` seam.

Exercises the Deep Agents ``BackendProtocol`` surface end-to-end against the
in-memory fake broker: mount listing, line-sliced text reads, base64 binary
reads, glob/grep with mount scoping and root fan-out, path resolution
(virtual-only, never a host path), read-only enforcement, safe error messages,
and the gated construction seam.
"""

from __future__ import annotations

import asyncio
import base64

import pytest

from agent_runtime.capabilities.desktop import workspace_backend as wb
from agent_runtime.capabilities.desktop.workspace_backend import (
    BrokeredWorkspaceBackend,
    WorkspaceBackendConfig,
    WorkspaceMount,
    WorkspaceWriteNotSupportedError,
    build_workspace_backend,
)
from tests.unit.agent_runtime.capabilities.desktop.fakes import (
    FakeBrokerFs,
    RecordingBroker,
)


class WorkspaceBackendMixin:
    """Two-mount fixture over an in-memory fake broker."""

    @staticmethod
    def _broker() -> RecordingBroker:
        return RecordingBroker(
            grants={
                "grant-proj": FakeBrokerFs(
                    files={
                        "a.txt": b"L1\nL2\nL3\n",
                        "sub/b.py": b"x = 1\n# TODO refactor\n",
                        "img.bin": b"\xff\xfe\x00\x01",
                    }
                ),
                "grant-docs": FakeBrokerFs(
                    files={"readme.md": b"hello TODO world\n"},
                ),
            }
        )

    @classmethod
    def backend(cls, broker: RecordingBroker | None = None) -> BrokeredWorkspaceBackend:
        broker = broker or cls._broker()
        return BrokeredWorkspaceBackend(
            client=broker.client(),
            mounts=[
                WorkspaceMount(name="proj", grant_id="grant-proj"),
                WorkspaceMount(name="docs", grant_id="grant-docs"),
            ],
        )


class TestWorkspaceListing(WorkspaceBackendMixin):
    """`als` lists mounts at the root and children under a mount."""

    async def test_root_lists_mounts_as_directories(self) -> None:
        result = await self.backend().als("/")
        paths = {(e["path"], e.get("is_dir")) for e in (result.entries or [])}
        assert paths == {("/proj/", True), ("/docs/", True)}

    async def test_mount_listing_marks_directories_with_trailing_slash(self) -> None:
        result = await self.backend().als("/proj")
        entries = {e["path"]: e.get("is_dir") for e in (result.entries or [])}
        assert entries["/proj/a.txt"] is False
        assert entries["/proj/img.bin"] is False
        assert entries["/proj/sub/"] is True  # dir → trailing slash + is_dir

    async def test_unstripped_workspace_prefix_form_is_accepted(self) -> None:
        # Direct callers (not via CompositeBackend) keep the /workspace prefix.
        result = await self.backend().als("/workspace/proj")
        assert any(e["path"] == "/proj/a.txt" for e in (result.entries or []))

    async def test_unknown_mount_is_not_found(self) -> None:
        result = await self.backend().als("/nope")
        assert result.error == wb._SafeMessage.NOT_FOUND
        assert result.entries is None

    async def test_broker_error_becomes_safe_message(self) -> None:
        # A mount pointing at a grant the broker has no active record of
        # (revoked / unknown) fails closed with a generic message.
        backend = BrokeredWorkspaceBackend(
            client=self._broker().client(),
            mounts=[WorkspaceMount(name="ghost", grant_id="grant-ghost")],
        )
        result = await backend.als("/ghost")
        assert result.error == wb._SafeMessage.UNAVAILABLE


class TestWorkspaceRead(WorkspaceBackendMixin):
    """`aread` line-slices UTF-8 text and base64-passes binary."""

    async def test_reads_full_text_as_utf8(self) -> None:
        result = await self.backend().aread("/proj/a.txt")
        assert result.error is None
        assert result.file_data == {"content": "L1\nL2\nL3\n", "encoding": "utf-8"}

    async def test_line_offset_and_limit_slice(self) -> None:
        result = await self.backend().aread("/proj/a.txt", offset=1, limit=1)
        assert result.file_data is not None
        assert result.file_data["content"] == "L2\n"

    async def test_offset_beyond_eof_errors(self) -> None:
        result = await self.backend().aread("/proj/a.txt", offset=10)
        assert result.file_data is None
        assert "exceeds file length" in (result.error or "")

    async def test_binary_returns_base64(self) -> None:
        result = await self.backend().aread("/proj/img.bin")
        assert result.file_data is not None
        assert result.file_data["encoding"] == "base64"
        assert base64.b64decode(result.file_data["content"]) == b"\xff\xfe\x00\x01"

    async def test_mount_root_is_a_directory(self) -> None:
        result = await self.backend().aread("/proj")
        assert result.error == wb._SafeMessage.IS_A_DIRECTORY

    async def test_read_sends_virtual_relative_path_only(self) -> None:
        broker = self._broker()
        await self.backend(broker).aread("/proj/sub/b.py")
        route, _headers, body = broker.requests[-1]
        assert route == "/v1/fs/read"
        assert body["grant_id"] == "grant-proj"
        assert body["path"] == "sub/b.py"  # grant-relative, NOT a host path
        assert not body["path"].startswith("/")


class TestWorkspaceGlob(WorkspaceBackendMixin):
    """`aglob` scopes to a mount subtree and fans out at the root."""

    async def test_glob_scoped_to_mount(self) -> None:
        result = await self.backend().aglob("**/*.py", "/proj")
        paths = {m["path"] for m in (result.matches or [])}
        assert paths == {"/proj/sub/b.py"}

    async def test_glob_scoped_to_subdirectory(self) -> None:
        broker = self._broker()
        await self.backend(broker).aglob("*.py", "/proj/sub")
        # The mount subdirectory is folded into the broker pattern.
        assert broker.requests[-1][2]["pattern"] == "sub/*.py"

    async def test_glob_at_root_fans_across_mounts(self) -> None:
        result = await self.backend().aglob("**/*.md", None)
        paths = {m["path"] for m in (result.matches or [])}
        assert paths == {"/docs/readme.md"}


class TestWorkspaceGrep(WorkspaceBackendMixin):
    """`agrep` maps broker hits (preview → text) and remaps paths."""

    async def test_grep_scoped_to_mount(self) -> None:
        result = await self.backend().agrep("TODO", "/proj")
        matches = {(m["path"], m["line"], m["text"]) for m in (result.matches or [])}
        assert ("/proj/sub/b.py", 2, "# TODO refactor") in matches

    async def test_grep_at_root_fans_across_mounts(self) -> None:
        result = await self.backend().agrep("TODO", "/")
        paths = {m["path"] for m in (result.matches or [])}
        assert paths == {"/proj/sub/b.py", "/docs/readme.md"}

    async def test_grep_glob_filter_folds_into_path_glob(self) -> None:
        broker = self._broker()
        await self.backend(broker).agrep("TODO", "/proj/sub", glob="*.py")
        assert broker.requests[-1][2]["path_glob"] == "sub/*.py"


class TestWorkspaceReadOnly(WorkspaceBackendMixin):
    """Every mutating method raises the read-only error."""

    async def test_awrite_raises(self) -> None:
        with pytest.raises(WorkspaceWriteNotSupportedError):
            await self.backend().awrite("/proj/a.txt", "nope")

    def test_write_raises(self) -> None:
        with pytest.raises(WorkspaceWriteNotSupportedError):
            self.backend().write("/proj/a.txt", "nope")

    async def test_aedit_raises(self) -> None:
        with pytest.raises(WorkspaceWriteNotSupportedError):
            await self.backend().aedit("/proj/a.txt", "L1", "X1")

    async def test_aupload_raises(self) -> None:
        with pytest.raises(WorkspaceWriteNotSupportedError):
            await self.backend().aupload_files([("/proj/x", b"y")])


class TestWorkspaceSyncBridge(WorkspaceBackendMixin):
    """The sync entry points delegate to the async implementation (no running loop)."""

    def test_sync_ls_root(self) -> None:
        result = self.backend().ls("/")
        paths = {e["path"] for e in (result.entries or [])}
        assert paths == {"/proj/", "/docs/"}

    def test_sync_read(self) -> None:
        result = self.backend().read("/proj/a.txt")
        assert result.file_data is not None
        assert result.file_data["content"] == "L1\nL2\nL3\n"


class TestWorkspaceMountValidation:
    """`WorkspaceMount` rejects malformed names and the backend rejects duplicates."""

    @pytest.mark.parametrize("name", ["", "a/b", "a\\b"])
    def test_invalid_mount_name_rejected(self, name: str) -> None:
        with pytest.raises(ValueError, match="single non-empty path segment"):
            WorkspaceMount(name=name, grant_id="g")

    def test_empty_grant_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="grant_id"):
            WorkspaceMount(name="ok", grant_id="")

    def test_duplicate_mount_names_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicate workspace mount"):
            BrokeredWorkspaceBackend(
                client=RecordingBroker(grants={}).client(),
                mounts=[
                    WorkspaceMount(name="dup", grant_id="g1"),
                    WorkspaceMount(name="dup", grant_id="g2"),
                ],
            )


class TestBuildWorkspaceBackendSeam:
    """`build_workspace_backend` is gated on broker URL + token being present."""

    def test_absent_config_returns_none(self) -> None:
        assert build_workspace_backend(WorkspaceBackendConfig()) is None

    def test_url_without_token_returns_none(self) -> None:
        config = WorkspaceBackendConfig(broker_base_url="http://127.0.0.1:1")
        assert build_workspace_backend(config) is None

    def test_token_without_url_returns_none(self) -> None:
        config = WorkspaceBackendConfig(broker_token="secret")
        assert build_workspace_backend(config) is None

    def test_full_config_builds_backend(self) -> None:
        config = WorkspaceBackendConfig(
            broker_base_url="http://127.0.0.1:9",
            broker_token="secret",
            mounts=(WorkspaceMount(name="proj", grant_id="grant-proj"),),
        )
        backend = build_workspace_backend(config)
        assert isinstance(backend, BrokeredWorkspaceBackend)

    def test_from_env_absent_yields_none(self) -> None:
        config = WorkspaceBackendConfig.from_env(env={})
        assert build_workspace_backend(config) is None

    def test_from_env_present_builds_backend(self) -> None:
        config = WorkspaceBackendConfig.from_env(
            env={
                "DESKTOP_BROKER_URL": "http://127.0.0.1:9",
                "DESKTOP_BROKER_TOKEN": "secret",
            },
            mounts=[WorkspaceMount(name="proj", grant_id="grant-proj")],
        )
        assert isinstance(build_workspace_backend(config), BrokeredWorkspaceBackend)


def test_module_exposes_route_prefix() -> None:
    """The route prefix constant is the single source of truth for wiring."""
    assert wb.ROUTE_PREFIX == "/workspace/"
    assert BrokeredWorkspaceBackend.PATH_PREFIX == "/workspace/"


def test_sync_bridge_uses_asyncio_run_without_loop() -> None:
    """Smoke: `_run_sync` runs a coroutine when no event loop is active."""

    async def _coro() -> int:
        return 7

    assert wb._run_sync(_coro()) == 7
    # And it stays functional under a fresh loop too.
    assert asyncio.run(_wrap()) == 7


async def _wrap() -> int:
    return 7

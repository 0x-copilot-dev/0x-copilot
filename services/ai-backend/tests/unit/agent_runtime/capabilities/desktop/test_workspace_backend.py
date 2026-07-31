r"""Unit tests for :class:`BrokeredWorkspaceBackend` and the ``build_workspace_backend`` seam.

Exercises the Deep Agents ``BackendProtocol`` surface end-to-end against the
in-memory fake broker: mount listing, line-sliced text reads, base64 binary
reads, glob/grep with mount scoping and root fan-out, path resolution, read-only
enforcement, safe error messages, and the gated construction seam.

Also the host-path lane: a covering grant serves the read as mount + relative
(and the broker still never receives the host-absolute string), an ungranted
folder parks the run on a grant request, and an escape fails closed instead of
becoming one. ``TestDownloadsFolderRegression`` is the live defect itself.
"""

from __future__ import annotations

import asyncio
import base64

import pytest

from agent_runtime.capabilities.desktop import workspace_backend as wb
from agent_runtime.capabilities.desktop.broker_client import BrokerGrant
from agent_runtime.capabilities.desktop.host_path import HostPathMessages
from agent_runtime.capabilities.desktop.workspace_backend import (
    BrokeredWorkspaceBackend,
    WorkspaceBackendConfig,
    WorkspaceMount,
    WorkspaceMountTable,
    WorkspaceWriteNotSupportedError,
    build_workspace_backend,
)
from agent_runtime.capabilities.desktop.workspace_grant import (
    WorkspaceGrantGate,
    WorkspaceGrantMessages,
)
from tests.unit.agent_runtime.capabilities.desktop.fakes import (
    FakeBrokerFs,
    RecordingBroker,
    RecordingConsent,
)

#: The exact shape of the live defect, and its Windows twin.
DOWNLOADS = "/Users/parthpahwa/Downloads"
WIN_DOWNLOADS = "C:\\Users\\parth\\Downloads"


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
        # The explicit workspace namespace: an unknown mount there is a
        # not-found, never a host folder.
        result = await self.backend().als("/workspace/nope")
        assert result.error == wb._SafeMessage.NOT_FOUND
        assert result.entries is None

    async def test_bare_unknown_segment_is_read_as_a_host_folder(self) -> None:
        # Behaviour change: without the ``/workspace/`` prefix and with no mount
        # of that name, ``/nope`` can only be a host folder — and this fixture
        # wires no grant gate, so it is refused out loud rather than answered
        # with an empty listing.
        result = await self.backend().als("/nope")
        assert result.entries is None
        assert result.error == WorkspaceGrantMessages.NOT_GRANTED

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
        """The legacy unprefixed pair still works, so no caller is broken."""

        config = WorkspaceBackendConfig.from_env(
            env={
                "DESKTOP_BROKER_URL": "http://127.0.0.1:9",
                "DESKTOP_BROKER_TOKEN": "secret",
            },
            mounts=[WorkspaceMount(name="proj", grant_id="grant-proj")],
        )
        assert isinstance(build_workspace_backend(config), BrokeredWorkspaceBackend)

    def test_reads_the_names_the_desktop_supervisor_actually_forwards(self) -> None:
        """The live defect: these are the ONLY broker names a real boot sets.

        ``apps/desktop/main/services/service-env.ts`` exports
        ``DESKTOP_WORKSPACE_BROKER_URL`` / ``_TOKEN`` / ``_AUDIENCE`` to the
        supervised ai-backend, beside the browser broker's
        ``DESKTOP_BROWSER_BROKER_*``. Nothing in the app has ever set the
        unprefixed ``DESKTOP_BROKER_URL``. Reading that instead produced an empty
        base url, so ``workspace_backend()`` returned ``None``, no ``/workspace/``
        route existed, and ``ls ~/Downloads`` was answered by agent MEMORY with an
        empty listing and a green tick — verified live against the packaged app.

        The test above passed throughout, because it asserted the name the code
        read rather than the name the product sets. This one asserts the contract
        that actually has to hold.
        """

        config = WorkspaceBackendConfig.from_env(
            env={
                "DESKTOP_WORKSPACE_BROKER_URL": "http://127.0.0.1:9",
                "DESKTOP_WORKSPACE_BROKER_TOKEN": "secret",
            },
            mounts=[WorkspaceMount(name="proj", grant_id="grant-proj")],
        )
        assert config.broker_base_url == "http://127.0.0.1:9"
        assert config.broker_token == "secret"
        assert isinstance(build_workspace_backend(config), BrokeredWorkspaceBackend)

    def test_zero_grants_still_builds_a_backend(self) -> None:
        """ "You have granted nothing" must be an ANSWER, not a fall-through.

        With no mounts the route must still exist, so a host path reaches the
        grant request instead of landing on the ``StateBackend`` default.
        """

        config = WorkspaceBackendConfig.from_env(
            env={
                "DESKTOP_WORKSPACE_BROKER_URL": "http://127.0.0.1:9",
                "DESKTOP_WORKSPACE_BROKER_TOKEN": "secret",
            },
        )
        assert config.mounts == ()
        assert isinstance(build_workspace_backend(config), BrokeredWorkspaceBackend)

    def test_the_prefixed_name_wins_when_both_are_present(self) -> None:
        config = WorkspaceBackendConfig.from_env(
            env={
                "DESKTOP_WORKSPACE_BROKER_URL": "http://127.0.0.1:9",
                "DESKTOP_WORKSPACE_BROKER_TOKEN": "current",
                "DESKTOP_BROKER_URL": "http://127.0.0.1:1",
                "DESKTOP_BROKER_TOKEN": "stale",
            },
        )
        assert config.broker_base_url == "http://127.0.0.1:9"
        assert config.broker_token == "current"

    def test_with_mounts_replaces_mount_table(self) -> None:
        base = WorkspaceBackendConfig(
            broker_base_url="http://127.0.0.1:9", broker_token="secret"
        )
        bound = base.with_mounts([WorkspaceMount(name="proj", grant_id="grant-proj")])
        assert base.mounts == ()  # original is untouched (frozen)
        assert bound.mounts == (WorkspaceMount(name="proj", grant_id="grant-proj"),)

    async def test_injected_client_is_reused_for_reads(self) -> None:
        # A client built over the fake transport is reused verbatim, so the
        # resulting backend's reads go through it (no second real client).
        broker = RecordingBroker(
            grants={"grant-proj": FakeBrokerFs(files={"a.txt": b"L1\n"})}
        )
        config = WorkspaceBackendConfig(
            broker_base_url="http://127.0.0.1:9",
            broker_token="secret",
            mounts=(WorkspaceMount(name="proj", grant_id="grant-proj"),),
        )
        backend = build_workspace_backend(config, client=broker.client())
        assert isinstance(backend, BrokeredWorkspaceBackend)
        result = await backend.aread("/proj/a.txt")
        assert result.error is None
        assert result.file_data["content"] == "L1\n"


class TestWorkspaceMountTable:
    """`WorkspaceMountTable` resolves a broker grant snapshot into named mounts."""

    @staticmethod
    def _grant(
        grant_id: str,
        *,
        label: str = "",
        mount: str = "mnt_x",
        status: str = "active",
        mode: str = "read_only",
    ) -> BrokerGrant:
        return BrokerGrant(
            grantId=grant_id, mode=mode, label=label, status=status, mount=mount
        )

    def test_label_becomes_readable_slug_bound_to_grant_id(self) -> None:
        mounts = WorkspaceMountTable.from_broker_grants(
            [self._grant("g1", label="My Docs", mount="mnt_a")]
        )
        assert len(mounts) == 1
        assert mounts[0].name == "my-docs"
        assert mounts[0].grant_id == "g1"
        assert mounts[0].label == "My Docs"  # human hint carried, never sent

    def test_duplicate_labels_are_disambiguated(self) -> None:
        mounts = WorkspaceMountTable.from_broker_grants(
            [
                self._grant("g1", label="Docs", mount="mnt_a"),
                self._grant("g2", label="Docs", mount="mnt_b"),
            ]
        )
        assert [m.name for m in mounts] == ["docs", "docs-2"]
        assert [m.grant_id for m in mounts] == ["g1", "g2"]

    def test_empty_label_falls_back_to_opaque_mount_id(self) -> None:
        mounts = WorkspaceMountTable.from_broker_grants(
            [self._grant("g1", label="", mount="mnt_opaque")]
        )
        assert mounts[0].name == "mnt_opaque"
        assert mounts[0].label is None

    def test_revoked_and_empty_grant_ids_are_skipped(self) -> None:
        mounts = WorkspaceMountTable.from_broker_grants(
            [
                self._grant("g1", label="live", mount="mnt_a"),
                self._grant("g2", label="gone", mount="mnt_b", status="revoked"),
                self._grant("", label="noid", mount="mnt_c"),
            ]
        )
        assert [m.name for m in mounts] == ["live"]

    def test_all_grant_modes_are_readable(self) -> None:
        # `/workspace/` is read-only regardless of grant mode; every active mode
        # yields a mount (the broker enforces the actual read floor).
        mounts = WorkspaceMountTable.from_broker_grants(
            [
                self._grant("g1", label="ro", mount="m1", mode="read_only"),
                self._grant("g2", label="rw", mount="m2", mode="read_write"),
            ]
        )
        assert {m.name for m in mounts} == {"ro", "rw"}

    def test_empty_snapshot_yields_no_mounts(self) -> None:
        assert WorkspaceMountTable.from_broker_grants([]) == ()


class HostPathMixin:
    """A ``Downloads`` grant, optionally bound to a host root and a grant gate."""

    GRANT_ID = "grant-dl"
    FILES = {
        "q4.csv": b"period,revenue\nq4,12\n",
        "reports/jan.txt": b"january\n",
    }

    @classmethod
    def _broker(cls, *, denied: set[str] | None = None) -> RecordingBroker:
        return RecordingBroker(
            grants={
                cls.GRANT_ID: FakeBrokerFs(
                    files=dict(cls.FILES), denied=denied or set()
                )
            },
            grant_meta={cls.GRANT_ID: {"label": "Downloads", "mount": "mnt_dl"}},
        )

    @classmethod
    def granted(
        cls,
        *,
        host_root: str = DOWNLOADS,
        broker: RecordingBroker | None = None,
        consent: RecordingConsent | None = None,
    ) -> BrokeredWorkspaceBackend:
        """A backend whose single mount is bound to ``host_root``."""
        broker = broker or cls._broker()
        return BrokeredWorkspaceBackend(
            client=broker.client(),
            mounts=[
                WorkspaceMount(
                    name="downloads",
                    grant_id=cls.GRANT_ID,
                    label="Downloads",
                    host_root=host_root,
                )
            ],
            grant_gate=cls._gate(broker, consent),
        )

    @classmethod
    def ungranted(
        cls,
        *,
        broker: RecordingBroker | None = None,
        consent: RecordingConsent | None = None,
        mounts: list[WorkspaceMount] | None = None,
    ) -> BrokeredWorkspaceBackend:
        """A backend with no mount covering the host folder under test."""
        broker = broker or cls._broker()
        return BrokeredWorkspaceBackend(
            client=broker.client(),
            mounts=mounts if mounts is not None else [],
            grant_gate=cls._gate(broker, consent),
        )

    @staticmethod
    def _gate(
        broker: RecordingBroker, consent: RecordingConsent | None
    ) -> WorkspaceGrantGate | None:
        if consent is None:
            return None
        return WorkspaceGrantGate(
            grants=broker.client(), interrupt_handler=consent, run_id="run-1"
        )

    @staticmethod
    def approving(
        broker: RecordingBroker, *, root: str = DOWNLOADS
    ) -> RecordingConsent:
        """A user who grants ``root``, which Electron then reports as a grant."""
        return RecordingConsent(
            resume={
                "decision": "approved",
                "grant_id": HostPathMixin.GRANT_ID,
                "root": root,
            },
            on_ask=lambda _payload: broker.add_grant(
                HostPathMixin.GRANT_ID, dict(HostPathMixin.FILES), label="Downloads"
            ),
        )


class TestDownloadsFolderRegression(HostPathMixin):
    """The live defect, pinned.

    The agent called ``ls`` with ``/Users/parthpahwa/Downloads``, the call landed
    on a virtual backend, and the tool returned ``{"content": "[]"}`` — an empty
    listing, reported as success, for a folder holding 1009 files. These tests
    fail if a host path can produce an empty success again, by any route.
    """

    async def test_host_path_is_claimed_and_never_falls_through(self) -> None:
        # A router must deliver this path here; the empty-success lie was born
        # from it reaching the agent-memory backend instead.
        assert BrokeredWorkspaceBackend.claims_path(DOWNLOADS) is True
        assert BrokeredWorkspaceBackend.claims_path(WIN_DOWNLOADS) is True

    async def test_ungranted_downloads_folder_is_refused_not_emptied(self) -> None:
        result = await self.ungranted().als(DOWNLOADS)
        # The two ways to lie: an empty listing, or an empty listing with a tick.
        assert result.entries is None
        assert result.entries != []
        assert result.error == WorkspaceGrantMessages.NOT_GRANTED

    async def test_ungranted_downloads_folder_asks_the_user(self) -> None:
        broker = self._broker()
        consent = RecordingConsent(resume={"decision": "rejected"})
        result = await self.ungranted(broker=broker, consent=consent).als(DOWNLOADS)
        assert consent.grant_block["path"] == DOWNLOADS
        assert result.entries is None
        assert result.error == WorkspaceGrantMessages.DECLINED
        # A declined grant must not have touched the filesystem.
        assert not [route for route, _, _ in broker.requests if "/v1/fs/" in route]

    async def test_granting_makes_the_real_listing_appear(self) -> None:
        broker = RecordingBroker(grants={})
        consent = self.approving(broker)
        backend = self.ungranted(broker=broker, consent=consent)
        result = await backend.als(DOWNLOADS)
        assert consent.asked
        assert result.error is None
        # The 1009-file folder now answers with its real contents, addressed by
        # the virtual path the model can reuse.
        assert {entry["path"] for entry in (result.entries or [])} == {
            "/downloads/q4.csv",
            "/downloads/reports/",
        }

    async def test_windows_downloads_folder_takes_the_same_path(self) -> None:
        broker = RecordingBroker(grants={})
        consent = self.approving(broker, root=WIN_DOWNLOADS)
        result = await self.ungranted(broker=broker, consent=consent).als(WIN_DOWNLOADS)
        assert consent.grant_block["platform"] == "windows"
        assert result.error is None
        assert result.entries


class TestCoveredHostPathReads(HostPathMixin):
    """A covered host path is served exactly as a virtual path is."""

    async def test_listing_uses_grant_id_and_a_root_relative_path(self) -> None:
        broker = self._broker()
        await self.granted(broker=broker).als(f"{DOWNLOADS}/reports")
        route, _headers, body = broker.requests[-1]
        assert route == "/v1/fs/list"
        assert body["grant_id"] == self.GRANT_ID
        assert body["path"] == "reports"
        assert not str(body["path"]).startswith("/")

    async def test_the_broker_never_receives_the_host_absolute_string(self) -> None:
        # The load-bearing security property: only mount names and root-relative
        # virtual paths cross to the broker. Asserted over EVERY recorded
        # request, headers included, not just the last body.
        broker = self._broker()
        backend = self.granted(broker=broker)
        await backend.als(DOWNLOADS)
        await backend.aread(f"{DOWNLOADS}/q4.csv")
        await backend.aglob("**/*.txt", DOWNLOADS)
        await backend.agrep("january", DOWNLOADS)
        sent = broker.bodies()
        assert broker.requests  # the ops really did reach the broker
        for fragment in (DOWNLOADS, "/Users", "parthpahwa", "Downloads"):
            assert fragment not in sent

    async def test_windows_host_path_is_sent_as_a_posix_relative_path(self) -> None:
        broker = self._broker()
        backend = self.granted(host_root=WIN_DOWNLOADS, broker=broker)
        result = await backend.aread(f"{WIN_DOWNLOADS}\\reports\\jan.txt")
        assert result.error is None
        _route, _headers, body = broker.requests[-1]
        assert body["path"] == "reports/jan.txt"  # never backslashes
        assert "C:" not in broker.bodies()

    async def test_reads_a_covered_file(self) -> None:
        result = await self.granted().aread(f"{DOWNLOADS}/q4.csv")
        assert result.error is None
        assert result.file_data is not None
        assert result.file_data["content"] == "period,revenue\nq4,12\n"

    async def test_glob_and_grep_scope_to_the_covering_mount(self) -> None:
        backend = self.granted()
        globbed = await backend.aglob("**/*.txt", DOWNLOADS)
        assert {m["path"] for m in (globbed.matches or [])} == {
            "/downloads/reports/jan.txt"
        }
        grepped = await backend.agrep("january", DOWNLOADS)
        assert {m["path"] for m in (grepped.matches or [])} == {
            "/downloads/reports/jan.txt"
        }

    async def test_covered_path_never_asks_for_a_grant(self) -> None:
        broker = self._broker()
        consent = RecordingConsent(resume={"decision": "rejected"})
        result = await self.granted(broker=broker, consent=consent).als(DOWNLOADS)
        assert result.error is None
        assert consent.asked is False

    async def test_deepest_grant_wins_for_a_nested_folder(self) -> None:
        broker = RecordingBroker(
            grants={
                "grant-home": FakeBrokerFs(files={"Downloads/q4.csv": b"outer\n"}),
                self.GRANT_ID: FakeBrokerFs(files={"q4.csv": b"inner\n"}),
            }
        )
        backend = BrokeredWorkspaceBackend(
            client=broker.client(),
            mounts=[
                WorkspaceMount(
                    name="home", grant_id="grant-home", host_root="/Users/parthpahwa"
                ),
                WorkspaceMount(
                    name="downloads", grant_id=self.GRANT_ID, host_root=DOWNLOADS
                ),
            ],
        )
        result = await backend.aread(f"{DOWNLOADS}/q4.csv")
        assert result.file_data is not None
        assert result.file_data["content"] == "inner\n"

    async def test_mount_relative_paths_returned_by_a_host_listing_round_trip(
        self,
    ) -> None:
        # A host listing answers in virtual paths; feeding one straight back must
        # resolve as a mount, not be re-read as a host folder.
        backend = self.granted()
        listing = await backend.als(DOWNLOADS)
        first = sorted(entry["path"] for entry in (listing.entries or []))[0]
        again = await backend.aread(first)
        assert again.error is None


class TestUngrantedHostPathAsks(HostPathMixin):
    """The grant request names the folder a user could actually grant."""

    async def test_a_file_read_asks_for_its_containing_folder(self) -> None:
        broker = RecordingBroker(grants={})
        consent = self.approving(broker)
        result = await self.ungranted(broker=broker, consent=consent).aread(
            f"{DOWNLOADS}/q4.csv"
        )
        # The card names the folder, not the file — grants cover folders.
        assert consent.grant_block["path"] == DOWNLOADS
        assert consent.grant_block["folder_name"] == "Downloads"
        assert result.error is None
        assert result.file_data is not None

    async def test_a_top_level_file_read_never_asks_for_the_whole_volume(self) -> None:
        consent = RecordingConsent(resume={"decision": "approved"})
        result = await self.ungranted(consent=consent).aread("/q4.csv")
        assert consent.asked is False
        assert result.error == HostPathMessages.VOLUME_ROOT

    async def test_glob_on_an_ungranted_folder_refuses_rather_than_matching_nothing(
        self,
    ) -> None:
        # An empty match set reads as "searched, found nothing" — the same lie in
        # a different shape.
        result = await self.ungranted().aglob("**/*.csv", DOWNLOADS)
        assert result.matches is None
        assert result.error == WorkspaceGrantMessages.NOT_GRANTED

    async def test_grep_on_an_ungranted_folder_refuses(self) -> None:
        result = await self.ungranted().agrep("revenue", DOWNLOADS)
        assert result.matches is None
        assert result.error == WorkspaceGrantMessages.NOT_GRANTED

    async def test_an_approved_grant_binds_a_named_mount(self) -> None:
        broker = RecordingBroker(grants={})
        backend = self.ungranted(broker=broker, consent=self.approving(broker))
        await backend.als(DOWNLOADS)
        assert [(m.name, m.grant_id, m.host_root) for m in backend.mounts] == [
            ("downloads", self.GRANT_ID, DOWNLOADS)
        ]

    async def test_a_second_read_under_the_bound_root_does_not_ask_again(self) -> None:
        broker = RecordingBroker(grants={})
        consent = self.approving(broker)
        backend = self.ungranted(broker=broker, consent=consent)
        await backend.als(DOWNLOADS)
        result = await backend.aread(f"{DOWNLOADS}/reports/jan.txt")
        assert len(consent.payloads) == 1
        assert result.error is None

    async def test_an_ancestor_grant_covers_the_requested_subfolder(self) -> None:
        broker = RecordingBroker(grants={})
        consent = RecordingConsent(
            resume={
                "decision": "approved",
                "grant_id": self.GRANT_ID,
                "root": "/Users/parthpahwa",
            },
            on_ask=lambda _p: broker.add_grant(
                self.GRANT_ID, {"Downloads/q4.csv": b"x\n"}, label="parthpahwa"
            ),
        )
        backend = self.ungranted(broker=broker, consent=consent)
        result = await backend.aread(f"{DOWNLOADS}/q4.csv")
        assert result.error is None
        _route, _headers, body = broker.requests[-1]
        assert body["path"] == "Downloads/q4.csv"

    async def test_a_snapshot_mount_with_no_known_root_adopts_the_granted_root(
        self,
    ) -> None:
        # Mounts resolved from the broker snapshot carry no host root (that
        # projection is path-free), so a host path under one still asks — and the
        # approval teaches the existing mount its root instead of duplicating it.
        broker = self._broker()
        consent = self.approving(broker)
        backend = self.ungranted(
            broker=broker,
            consent=consent,
            mounts=[
                WorkspaceMount(
                    name="downloads", grant_id=self.GRANT_ID, label="Downloads"
                )
            ],
        )
        result = await backend.als(DOWNLOADS)
        assert result.error is None
        assert [m.host_root for m in backend.mounts] == [DOWNLOADS]

    async def test_grant_denied_after_asking_returns_a_safe_message(self) -> None:
        broker = RecordingBroker(grants={})
        consent = RecordingConsent(resume={"decision": "approved", "root": DOWNLOADS})
        result = await self.ungranted(broker=broker, consent=consent).als(DOWNLOADS)
        # Approved, but no grant materialised: unbound rather than pretend-empty.
        assert result.entries is None
        assert result.error == WorkspaceGrantMessages.UNBOUND


class TestHostPathEscapesFailClosed(HostPathMixin):
    """An escape is refused. It never becomes a grant request."""

    @pytest.mark.parametrize(
        ("path", "message"),
        [
            ("/Users/parthpahwa/../../etc/passwd", HostPathMessages.TRAVERSAL),
            ("/workspace/downloads/../../etc", HostPathMessages.TRAVERSAL),
            ("~/Downloads", HostPathMessages.HOME_RELATIVE),
            ("C:Users\\parth", HostPathMessages.DRIVE_RELATIVE),
            ("\\Users\\parth", HostPathMessages.ROOT_RELATIVE),
            ("\\\\fileserver", HostPathMessages.INCOMPLETE_UNC),
            ("\\\\.\\PhysicalDrive0", HostPathMessages.DEVICE_NAMESPACE),
            ("C:\\Users\\parth\\NUL", HostPathMessages.RESERVED_NAME),
            ("C:\\Users\\parth\\report.", HostPathMessages.TRAILING_DOT_OR_SPACE),
        ],
    )
    async def test_refused_shapes_never_reach_the_grant_flow(
        self, path: str, message: str
    ) -> None:
        broker = self._broker()
        consent = RecordingConsent(resume={"decision": "approved", "root": "/"})
        backend = self.granted(broker=broker, consent=consent)
        result = await backend.als(path)
        assert result.entries is None
        assert result.error == message
        assert consent.asked is False, "an escape must not become a grant request"
        assert not [route for route, _, _ in broker.requests if "/v1/fs/" in route]

    async def test_a_symlink_escape_stays_a_broker_refusal(self) -> None:
        # The broker owns symlink / TOCTOU resolution. Its refusal must surface as
        # permission-denied, NOT be retried as "maybe we just need a grant".
        broker = self._broker(denied={"escape"})
        consent = RecordingConsent(resume={"decision": "approved", "root": "/"})
        result = await self.granted(broker=broker, consent=consent).als(
            f"{DOWNLOADS}/escape"
        )
        assert result.error == wb._SafeMessage.PERMISSION_DENIED
        assert consent.asked is False

    async def test_traversal_is_refused_on_reads_globs_and_greps_alike(self) -> None:
        backend = self.granted()
        escape = f"{DOWNLOADS}/../../etc"
        assert (await backend.aread(f"{escape}/passwd")).error == (
            HostPathMessages.TRAVERSAL
        )
        assert (await backend.aglob("*", escape)).error == HostPathMessages.TRAVERSAL
        assert (await backend.agrep("x", escape)).error == HostPathMessages.TRAVERSAL

    async def test_host_writes_stay_refused_rather_than_asking_for_a_grant(
        self,
    ) -> None:
        consent = RecordingConsent(resume={"decision": "approved", "root": DOWNLOADS})
        backend = self.granted(consent=consent)
        with pytest.raises(WorkspaceWriteNotSupportedError):
            await backend.awrite(f"{DOWNLOADS}/new.csv", "x")
        assert consent.asked is False


class TestZeroMountWorkspace(HostPathMixin):
    """Host access is on and nothing is granted: say so, do not answer empty."""

    async def test_root_listing_states_that_nothing_is_granted(self) -> None:
        result = await self.ungranted().als("/")
        assert result.entries is None
        assert result.error == wb._SafeMessage.NO_GRANTS

    async def test_root_glob_and_grep_state_that_nothing_is_granted(self) -> None:
        backend = self.ungranted()
        assert (await backend.aglob("**/*.csv")).error == wb._SafeMessage.NO_GRANTS
        assert (await backend.agrep("revenue")).error == wb._SafeMessage.NO_GRANTS

    async def test_a_zero_mount_backend_still_asks_for_a_folder(self) -> None:
        broker = RecordingBroker(grants={})
        consent = self.approving(broker)
        backend = self.ungranted(broker=broker, consent=consent)
        assert backend.mounts == ()
        assert (await backend.als(DOWNLOADS)).error is None


class TestMountHostRootValidation:
    """A mount binding that cannot be resolved is a loud construction error."""

    @pytest.mark.parametrize(
        "host_root", ["relative/downloads", "~/Downloads", "/Users/../etc", "C:rel"]
    )
    def test_unusable_host_root_is_rejected(self, host_root: str) -> None:
        with pytest.raises(ValueError, match="host-absolute"):
            WorkspaceMount(name="dl", grant_id="g", host_root=host_root)

    def test_absent_host_root_is_the_snapshot_default(self) -> None:
        assert WorkspaceMount(name="dl", grant_id="g").host_root is None


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

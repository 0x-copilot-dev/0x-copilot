"""Snapshot validation, deterministic hashing, and patch-diff tests."""

from __future__ import annotations

import pytest

from agent_runtime.capabilities.sandbox.config import SandboxLimitProfiles
from agent_runtime.capabilities.sandbox.contracts import (
    ArtifactRef,
    SandboxError,
    SandboxErrorCode,
)
from agent_runtime.capabilities.sandbox.workspace_transfer import (
    RawSnapshotEntry,
    WorkspaceManifestBuilder,
    WorkspacePatchBuilder,
    WorkspacePathValidator,
)


def _ref(size: int = 3) -> ArtifactRef:
    return ArtifactRef(artifact_id="a", sha256="a" * 64, size_bytes=size)


def _raw(path: str, *, sha: str = "a" * 64, size: int = 3, **kw) -> RawSnapshotEntry:
    return RawSnapshotEntry(
        path=path, sha256=sha, size_bytes=size, payload_ref=_ref(size), **kw
    )


LIMITS = SandboxLimitProfiles.get("desktop_v1")


class TestPathValidator:
    def test_normalizes_relative(self) -> None:
        assert WorkspacePathValidator.normalize("src/a.py") == "/workspace/src/a.py"

    def test_normalizes_workspace_rooted(self) -> None:
        assert (
            WorkspacePathValidator.normalize("/workspace/src/a.py")
            == "/workspace/src/a.py"
        )

    @pytest.mark.parametrize(
        "bad",
        ["../escape", "src/../../etc/passwd", "/etc/passwd", "a\\b", "a\x00b", ""],
    )
    def test_rejects_bad_paths(self, bad: str) -> None:
        with pytest.raises(SandboxError) as excinfo:
            WorkspacePathValidator.normalize(bad)
        assert excinfo.value.code is SandboxErrorCode.SNAPSHOT_INVALID

    @pytest.mark.parametrize(
        "path",
        [
            ".env",
            ".env.local",
            "config/secret.pem",
            "id.key",
            ".ssh/id_rsa",
            ".git/config",
            "node_modules/x/y.js",
        ],
    )
    def test_secret_and_cache_paths_excluded(self, path: str) -> None:
        normalized = WorkspacePathValidator.normalize(path)
        assert WorkspacePathValidator.is_excluded(normalized) is True

    def test_regular_source_not_excluded(self) -> None:
        normalized = WorkspacePathValidator.normalize("src/main.py")
        assert WorkspacePathValidator.is_excluded(normalized) is False


class TestManifestBuilder:
    def test_excludes_secrets_and_hashes_deterministically(self) -> None:
        entries_a = [_raw("b.py"), _raw("a.py"), _raw(".env")]
        entries_b = [_raw(".env"), _raw("a.py"), _raw("b.py")]
        m1 = WorkspaceManifestBuilder.build(
            workspace_id="ws", root_grant_id="g", raw_entries=entries_a, limits=LIMITS
        )
        m2 = WorkspaceManifestBuilder.build(
            workspace_id="ws", root_grant_id="g", raw_entries=entries_b, limits=LIMITS
        )
        # Secret dropped, order-independent hash.
        assert len(m1.entries) == 2
        assert m1.manifest_sha256 == m2.manifest_sha256
        assert m1.total_bytes == 6

    def test_rejects_symlink(self) -> None:
        with pytest.raises(SandboxError) as excinfo:
            WorkspaceManifestBuilder.build(
                workspace_id="ws",
                root_grant_id="g",
                raw_entries=[_raw("link", is_symlink=True)],
                limits=LIMITS,
            )
        assert excinfo.value.code is SandboxErrorCode.SNAPSHOT_INVALID

    def test_rejects_oversize_file(self) -> None:
        big = LIMITS.max_upload_file_bytes + 1
        with pytest.raises(SandboxError) as excinfo:
            WorkspaceManifestBuilder.build(
                workspace_id="ws",
                root_grant_id="g",
                raw_entries=[_raw("big.bin", size=big)],
                limits=LIMITS,
            )
        assert excinfo.value.code is SandboxErrorCode.SNAPSHOT_QUOTA_EXCEEDED

    def test_rejects_duplicate_after_normalization(self) -> None:
        with pytest.raises(SandboxError) as excinfo:
            WorkspaceManifestBuilder.build(
                workspace_id="ws",
                root_grant_id="g",
                raw_entries=[_raw("a.py"), _raw("./a.py")],
                limits=LIMITS,
            )
        assert excinfo.value.code is SandboxErrorCode.SNAPSHOT_INVALID


class TestPatchBuilder:
    def _baseline(self):
        return WorkspaceManifestBuilder.build(
            workspace_id="ws",
            root_grant_id="g",
            raw_entries=[_raw("a.py", sha="a" * 64), _raw("gone.py", sha="b" * 64)],
            limits=LIMITS,
        )

    def test_add_modify_delete(self) -> None:
        baseline = self._baseline()
        result = {
            "a.py": _raw("a.py", sha="c" * 64),  # modified
            "new.py": _raw("new.py", sha="d" * 64),  # added
            # gone.py absent → delete
        }
        patch = WorkspacePatchBuilder.build(baseline=baseline, result_entries=result)
        ops = {(e.operation, e.path) for e in patch.entries}
        assert ("modify", "/workspace/a.py") in ops
        assert ("add", "/workspace/new.py") in ops
        assert ("delete", "/workspace/gone.py") in ops
        assert patch.complete is True
        assert patch.baseline_manifest_sha256 == baseline.manifest_sha256

    def test_incomplete_flag_carried(self) -> None:
        baseline = self._baseline()
        patch = WorkspacePatchBuilder.build(
            baseline=baseline, result_entries={}, complete=False
        )
        assert patch.complete is False

"""Reference-only immutable snapshot tests for the D3 gateway foundation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.sandbox.contracts import SandboxError, SandboxErrorCode
from agent_runtime.capabilities.sandbox.snapshot import (
    SandboxResolvedSnapshotSource,
    SandboxSnapshotBuilder,
    SandboxSnapshotFileStorePort,
    SandboxSnapshotInput,
    SandboxSnapshotLimits,
    SandboxSnapshotPlan,
    SandboxSnapshotSource,
    SandboxSnapshotSourceKind,
)

_ARTIFACT = "artifact://art_550e8400-e29b-41d4-a716-446655440000/revisions/1"
_OVERLAY = "workspace-overlay://runs/run_1/versions/4"


def _source(kind: SandboxSnapshotSourceKind, ref: str) -> SandboxSnapshotSource:
    return SandboxSnapshotSource(kind=kind, source_ref=ref)


def _plan(*entries: tuple[str, SandboxSnapshotSource]) -> SandboxSnapshotPlan:
    return SandboxSnapshotPlan(
        entries=tuple(
            SandboxSnapshotInput(virtual_path=path, source=source)
            for path, source in entries
        )
    )


@dataclass
class _SnapshotStore(SandboxSnapshotFileStorePort):
    resolved: dict[str, SandboxResolvedSnapshotSource]
    resolved_refs: list[str] = field(default_factory=list)

    async def resolve(
        self, *, source: SandboxSnapshotSource, virtual_path: str
    ) -> SandboxResolvedSnapshotSource | None:
        del virtual_path
        self.resolved_refs.append(source.source_ref)
        return self.resolved.get(source.source_ref)

    async def open(self, *, content_ref: str) -> AsyncIterator[bytes]:
        del content_ref
        if False:  # pragma: no cover - structural protocol implementation.
            yield b""


def _resolved(
    *,
    kind: SandboxSnapshotSourceKind,
    source_ref: str,
    digest: str,
    size: int,
) -> SandboxResolvedSnapshotSource:
    return SandboxResolvedSnapshotSource(
        kind=kind,
        source_ref=source_ref,
        content_ref=f"artifact-blob://sha256/{digest}",
        content_digest=digest,
        size_bytes=size,
    )


class TestSandboxSnapshotBoundary:
    async def test_refuses_zero_selected_inputs_without_resolving_any_source(
        self,
    ) -> None:
        store = _SnapshotStore({})

        with pytest.raises(SandboxError) as error:
            await SandboxSnapshotBuilder.materialize(
                plan=SandboxSnapshotPlan(),
                store=store,
                limits=SandboxSnapshotLimits(),
            )

        assert error.value.code is SandboxErrorCode.SANDBOX_SNAPSHOT_REQUIRED
        assert store.resolved_refs == []

    @pytest.mark.parametrize(
        "path",
        [
            "/Users/alice/report.csv",
            "/workspace/../etc/passwd",
            "/workspace/src/../report.csv",
            "file:///tmp/report.csv",
            "/workspace/C:/report.csv",
            "/workspace/src\\report.csv",
        ],
    )
    def test_rejects_non_virtual_or_traversal_paths(self, path: str) -> None:
        with pytest.raises(ValidationError):
            SandboxSnapshotInput(
                virtual_path=path,
                source=_source(SandboxSnapshotSourceKind.ARTIFACT, _ARTIFACT),
            )

    @pytest.mark.parametrize(
        "kind,ref",
        [
            (SandboxSnapshotSourceKind.ARTIFACT, "file:///tmp/report.csv"),
            (SandboxSnapshotSourceKind.ARTIFACT, "/Users/alice/report.csv"),
            (SandboxSnapshotSourceKind.ARTIFACT, "https://example.test/data"),
            (SandboxSnapshotSourceKind.OVERLAY, "file:///tmp/overlay"),
            (SandboxSnapshotSourceKind.OVERLAY, "workspace-overlay://runs/run_1/live"),
        ],
    )
    def test_accepts_only_immutable_artifact_or_versioned_overlay_refs(
        self, kind: SandboxSnapshotSourceKind, ref: str
    ) -> None:
        with pytest.raises(ValidationError):
            SandboxSnapshotSource(kind=kind, source_ref=ref)

    async def test_materializes_sorted_manifest_without_local_paths(self) -> None:
        artifact = _source(SandboxSnapshotSourceKind.ARTIFACT, _ARTIFACT)
        overlay = _source(SandboxSnapshotSourceKind.OVERLAY, _OVERLAY)
        store = _SnapshotStore(
            {
                _ARTIFACT: _resolved(
                    kind=SandboxSnapshotSourceKind.ARTIFACT,
                    source_ref=_ARTIFACT,
                    digest="a" * 64,
                    size=3,
                ),
                _OVERLAY: _resolved(
                    kind=SandboxSnapshotSourceKind.OVERLAY,
                    source_ref=_OVERLAY,
                    digest="b" * 64,
                    size=5,
                ),
            }
        )

        manifest = await SandboxSnapshotBuilder.materialize(
            plan=_plan(("/workspace/z.txt", artifact), ("/workspace/a.txt", overlay)),
            store=store,
            limits=SandboxSnapshotLimits(),
        )

        assert [entry.virtual_path for entry in manifest.entries] == [
            "/workspace/a.txt",
            "/workspace/z.txt",
        ]
        assert manifest.total_bytes == 8
        assert manifest.snapshot_id == f"sandbox-snapshot:{manifest.manifest_digest}"
        wire = manifest.model_dump_json()
        assert "/Users/" not in wire
        assert "file://" not in wire
        assert store.resolved_refs == [_ARTIFACT, _OVERLAY]

    async def test_rejects_mismatched_source_resolution(self) -> None:
        store = _SnapshotStore(
            {
                _ARTIFACT: _resolved(
                    kind=SandboxSnapshotSourceKind.ARTIFACT,
                    source_ref="artifact://art_550e8400-e29b-41d4-a716-446655440001/revisions/1",
                    digest="a" * 64,
                    size=3,
                )
            }
        )

        with pytest.raises(SandboxError) as error:
            await SandboxSnapshotBuilder.materialize(
                plan=_plan(
                    (
                        "/workspace/report.csv",
                        _source(SandboxSnapshotSourceKind.ARTIFACT, _ARTIFACT),
                    )
                ),
                store=store,
                limits=SandboxSnapshotLimits(),
            )

        assert error.value.code is SandboxErrorCode.SANDBOX_MANIFEST_MISMATCH

    def test_rejects_blob_ref_with_a_different_content_digest(self) -> None:
        with pytest.raises(ValidationError):
            SandboxResolvedSnapshotSource(
                kind=SandboxSnapshotSourceKind.ARTIFACT,
                source_ref=_ARTIFACT,
                content_ref="artifact-blob://sha256/" + "a" * 64,
                content_digest="b" * 64,
                size_bytes=3,
            )

    async def test_rechecks_file_and_total_limits_before_provider_handoff(self) -> None:
        store = _SnapshotStore(
            {
                _ARTIFACT: _resolved(
                    kind=SandboxSnapshotSourceKind.ARTIFACT,
                    source_ref=_ARTIFACT,
                    digest="a" * 64,
                    size=4,
                ),
                _OVERLAY: _resolved(
                    kind=SandboxSnapshotSourceKind.OVERLAY,
                    source_ref=_OVERLAY,
                    digest="b" * 64,
                    size=4,
                ),
            }
        )
        plan = _plan(
            ("/workspace/a", _source(SandboxSnapshotSourceKind.ARTIFACT, _ARTIFACT)),
            ("/workspace/b", _source(SandboxSnapshotSourceKind.OVERLAY, _OVERLAY)),
        )

        with pytest.raises(SandboxError) as file_error:
            await SandboxSnapshotBuilder.materialize(
                plan=plan,
                store=store,
                limits=SandboxSnapshotLimits(max_entry_bytes=3),
            )
        assert file_error.value.code is SandboxErrorCode.SNAPSHOT_QUOTA_EXCEEDED

        with pytest.raises(SandboxError) as total_error:
            await SandboxSnapshotBuilder.materialize(
                plan=plan,
                store=store,
                limits=SandboxSnapshotLimits(max_total_bytes=7),
            )
        assert total_error.value.code is SandboxErrorCode.SNAPSHOT_QUOTA_EXCEEDED

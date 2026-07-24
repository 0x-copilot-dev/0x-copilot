"""Real cross-process CAS, idempotency, and durable-refold tests."""

from __future__ import annotations

import asyncio
import multiprocessing
from collections.abc import AsyncIterator
from pathlib import Path

from agent_runtime.artifacts.errors import (
    ArtifactConflictError,
    ArtifactIdempotencyConflictError,
)
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.artifact_blob_store import FileArtifactBlobStore
from runtime_adapters.file.artifact_metadata_store import FileArtifactMetadataStore
from runtime_adapters.file.artifact_publication import (
    FileArtifactPublicationCoordinator,
)
from tests.unit.runtime_adapters._artifact_fixtures import (
    SCOPE,
    artifact_id,
    digest,
    make_append_command,
    make_create_command,
)


async def _chunks(body: bytes) -> AsyncIterator[bytes]:
    yield body


def _metadata_process(
    root: str,
    scenario: str,
    variant: int,
    barrier,
    output,
) -> None:
    async def _run() -> None:
        layout = FileStoreLayout(Path(root))
        coordinator = FileArtifactPublicationCoordinator(layout)
        metadata = FileArtifactMetadataStore(layout, coordinator)
        barrier.wait(timeout=20)
        try:
            if scenario == "same":
                result = await metadata.create_artifact(
                    make_create_command(key="multiprocess-same")
                )
                output.put(("ok", result.replayed))
            elif scenario == "different":
                body = b"revision one" if variant == 1 else b"different body"
                result = await metadata.create_artifact(
                    make_create_command(
                        variant,
                        body=body,
                        key="multiprocess-conflict",
                        request_digest=digest(f"request-{variant}".encode()),
                    )
                )
                output.put(("ok", result.record.artifact.artifact_id))
            elif scenario == "append":
                result = await metadata.append_revision(
                    make_append_command(key=f"multiprocess-append-{variant}")
                )
                output.put(("ok", result.record.artifact.current_revision))
            else:  # pragma: no cover - test harness guard
                raise AssertionError(scenario)
        except ArtifactIdempotencyConflictError:
            output.put(("idempotency_conflict", None))
        except ArtifactConflictError:
            output.put(("conflict", None))

    asyncio.run(_run())


def _run_pair(tmp_path, scenario: str) -> list[tuple[str, object]]:
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    output = context.Queue()
    processes = [
        context.Process(
            target=_metadata_process,
            args=(str(tmp_path), scenario, variant, barrier, output),
        )
        for variant in (1, 2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    return [output.get(timeout=5), output.get(timeout=5)]


async def _publish(layout: FileStoreLayout, *bodies: bytes) -> None:
    blobs = FileArtifactBlobStore(layout)
    for body in bodies:
        await blobs.put_stream(
            expected_digest=digest(body),
            chunks=_chunks(body),
            byte_limit=len(body),
        )


def test_concurrent_same_key_same_digest_replays_across_processes(tmp_path) -> None:
    root = tmp_path / "same"
    asyncio.run(_publish(FileStoreLayout(root), b"revision one"))

    outcomes = _run_pair(root, "same")

    assert sorted(outcomes) == [("ok", False), ("ok", True)]
    reopened = FileArtifactMetadataStore(FileStoreLayout(root))
    record = asyncio.run(
        reopened.get_artifact(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            artifact_id=artifact_id(1),
        )
    )
    assert record is not None
    assert len(reopened.pending_outbox_rows) == 1


def test_concurrent_same_key_different_digest_conflicts_across_processes(
    tmp_path,
) -> None:
    root = tmp_path / "different"
    asyncio.run(
        _publish(
            FileStoreLayout(root),
            b"revision one",
            b"different body",
        )
    )

    outcomes = _run_pair(root, "different")

    assert [status for status, _ in outcomes].count("ok") == 1
    assert [status for status, _ in outcomes].count("idempotency_conflict") == 1
    reopened = FileArtifactMetadataStore(FileStoreLayout(root))
    existing = [
        asyncio.run(
            reopened.get_artifact(
                org_id=SCOPE.org_id,
                user_id=SCOPE.user_id,
                artifact_id=artifact_id(ordinal),
            )
        )
        for ordinal in (1, 2)
    ]
    assert sum(record is not None for record in existing) == 1


def test_concurrent_compare_and_append_has_one_cross_process_winner(
    tmp_path,
) -> None:
    root = tmp_path / "append"
    layout = FileStoreLayout(root)
    asyncio.run(_publish(layout, b"revision one", b"revision two"))
    original = FileArtifactMetadataStore(layout)
    asyncio.run(original.create_artifact(make_create_command()))

    outcomes = _run_pair(root, "append")

    assert [status for status, _ in outcomes].count("ok") == 1
    assert [status for status, _ in outcomes].count("conflict") == 1
    reopened = FileArtifactMetadataStore(layout)
    record = asyncio.run(
        reopened.get_artifact(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            artifact_id=artifact_id(1),
        )
    )
    assert record is not None
    assert record.artifact.current_revision == 2
    revision = asyncio.run(
        reopened.get_revision(
            org_id=SCOPE.org_id,
            user_id=SCOPE.user_id,
            artifact_id=artifact_id(1),
            revision=2,
        )
    )
    assert revision is not None

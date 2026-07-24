"""Import and locking guarantees for the Windows desktop runtime."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.artifact_publication import (
    FileArtifactPublicationCoordinator,
)


def test_factory_import_never_requires_posix_fcntl_when_artifacts_are_off() -> None:
    """A clean interpreter must import the factory without ``fcntl`` present.

    Windows has no ``fcntl`` module. This subprocess check exercises the real
    normal import graph rather than mocking a production lock implementation.
    """

    service_root = Path(__file__).parents[4]
    repository_root = service_root.parents[1]
    source_roots = (
        service_root / "src",
        repository_root / "packages" / "audit-chain" / "src",
        repository_root / "packages" / "service-contracts" / "src",
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(str(path) for path in source_roots)
    code = "import sys; sys.modules['fcntl'] = None; import runtime_adapters.factory"
    completed = subprocess.run(
        [sys.executable, "-c", code],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_selected_platform_lock_serializes_real_coordinator_state(tmp_path) -> None:
    """The native lock path is usable by the coordinator on this platform."""

    coordinator = FileArtifactPublicationCoordinator(FileStoreLayout(tmp_path))
    with coordinator.locked():
        coordinator.record_candidate_locked(
            blob_key="a" * 64,
            provenance_org_id="org_portability",
            candidate_since=datetime.now(timezone.utc),
        )
    reopened = FileArtifactPublicationCoordinator(FileStoreLayout(tmp_path))
    assert "a" * 64 in reopened.candidates

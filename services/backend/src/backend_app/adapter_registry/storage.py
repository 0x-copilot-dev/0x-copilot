"""Content-addressed source storage for tier-2 adapters.

Source bytes live outside the relational store so the Postgres row
stays small and so production can swap the on-disk adapter for an
S3-backed one without touching the service layer. The ``SourceStorage``
port describes the contract:

* ``put`` is content-addressed — the returned digest pins the bytes;
  retrying a put with the same payload is idempotent.
* ``get`` returns the raw bytes; ``None`` means "no such key".
* ``delete`` returns whether a key was removed; never raises on missing.

The shipped adapter is ``LocalFilesystemSourceStorage`` for tests and
dev. Production deploys inject an S3-backed adapter at
``create_app(adapter_source_storage=...)`` time; boto3 is intentionally
NOT a runtime dep of this service in Phase 7A.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


_SCHEME_PATH_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


@dataclass(frozen=True)
class StoredSource:
    """Result of a ``SourceStorage.put`` call."""

    key: str
    digest: str
    size_bytes: int


class SourceStorage(Protocol):
    """Adapter contract for tier-2 source bytes."""

    def put(
        self,
        *,
        scheme: str,
        version: int,
        source: bytes,
    ) -> StoredSource:
        """Persist source bytes and return the stable storage key + digest."""

    def get(self, *, key: str) -> bytes | None:
        """Return bytes for ``key`` or ``None`` if absent."""

    def delete(self, *, key: str) -> bool:
        """Remove ``key``; return True if a bytes record was removed."""


def _safe_scheme(scheme: str) -> str:
    safe = scheme.replace(":", "_")
    if not _SCHEME_PATH_PATTERN.fullmatch(safe):
        raise ValueError("scheme contains characters that cannot be safely stored")
    return safe


def _digest(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()


class InMemorySourceStorage:
    """Tests-only adapter; the production-shaped equivalent lives below."""

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    def put(
        self,
        *,
        scheme: str,
        version: int,
        source: bytes,
    ) -> StoredSource:
        safe = _safe_scheme(scheme)
        key = f"memory://adapter_registry/{safe}/{version}.js"
        self._blobs[key] = source
        return StoredSource(key=key, digest=_digest(source), size_bytes=len(source))

    def get(self, *, key: str) -> bytes | None:
        return self._blobs.get(key)

    def delete(self, *, key: str) -> bool:
        return self._blobs.pop(key, None) is not None


class LocalFilesystemSourceStorage:
    """Filesystem-backed implementation for local dev.

    Source bytes write to ``{data_dir}/{safe_scheme}/{version}.js``;
    the ``data_dir`` is created on init. Use this in dev and in unit
    tests that exercise the full backend-app lifecycle. Production
    swaps in an S3 adapter injected via ``create_app``.
    """

    def __init__(self, data_dir: Path | str) -> None:
        self._root = Path(data_dir).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def _resolve(self, *, scheme: str, version: int) -> tuple[str, Path]:
        safe = _safe_scheme(scheme)
        relative = Path(safe) / f"{version}.js"
        absolute = (self._root / relative).resolve()
        if self._root not in absolute.parents and absolute != self._root:
            raise ValueError("resolved adapter path escapes the data directory")
        return str(relative), absolute

    def put(
        self,
        *,
        scheme: str,
        version: int,
        source: bytes,
    ) -> StoredSource:
        key, absolute = self._resolve(scheme=scheme, version=version)
        absolute.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = absolute.with_suffix(absolute.suffix + ".tmp")
        tmp_path.write_bytes(source)
        os.replace(tmp_path, absolute)
        return StoredSource(key=key, digest=_digest(source), size_bytes=len(source))

    def get(self, *, key: str) -> bytes | None:
        absolute = (self._root / key).resolve()
        if self._root not in absolute.parents:
            return None
        if not absolute.exists():
            return None
        return absolute.read_bytes()

    def delete(self, *, key: str) -> bool:
        absolute = (self._root / key).resolve()
        if self._root not in absolute.parents:
            return False
        if not absolute.exists():
            return False
        absolute.unlink()
        return True


# Production inject point. The S3 client adapter is intentionally left
# unimplemented in this PR — boto3 should not be added as a runtime
# dep until Phase 8's hardening. A real implementation reads from
# `ADAPTER_REGISTRY_S3_BUCKET` and uses the existing IAM role; the
# `put` write path SHOULD upload with ServerSideEncryption=AES256 and
# the digest as the object's `x-amz-meta-sha256`.


__all__ = [
    "InMemorySourceStorage",
    "LocalFilesystemSourceStorage",
    "SourceStorage",
    "StoredSource",
]

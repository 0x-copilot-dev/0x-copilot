"""SurfaceSpec store: the cache/persistence rung of the acquisition ladder (PRD-07).

Two access shapes live here, satisfied by one object per backend:

* **Projector read** (:class:`SurfaceSpecReadPort`) — the pure hot-path rung-2
  lookup ``get(*, server, tool)`` the :class:`SurfaceProjector` already uses
  (frozen from PRD-02). Returns the cached :class:`SurfaceSpec` for a connector
  tool, or ``None``.
* **Generation store** (:class:`SurfaceSpecStorePort`) — keyed by the full
  :class:`SpecKey` ``(server, tool, output_shape_hash, spec_schema_version,
  skill_version)`` (plan D10). The async generator writes generated specs
  (``put``), records generation failures for skill iteration
  (``record_failure``), and reads back to skip re-work (``get_stored`` /
  ``has_failure``). Bumping the skill or the schema version changes the key and
  misses the cache — that is how a skill improvement invalidates stale specs.

Adapters: :class:`InMemorySurfaceSpecStore` (tests + single-process desktop) and
:class:`FileSurfaceSpecStore` (durable single-user desktop, atomic writes). No
backend-http adapter here — that is PRD-08.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, Protocol, runtime_checkable

from pydantic import Field, ValidationError

from agent_runtime.capabilities.surfaces import builtin
from agent_runtime.capabilities.surfaces.spec_models import SurfaceSpec
from agent_runtime.execution.contracts import RuntimeContract

_LOGGER = logging.getLogger(__name__)

# The SurfaceSpec schema version this store keys against. Kept as a module
# constant (not a magic literal at call sites) so a schema bump is a one-line
# change that flows into every SpecKey.
CURRENT_SPEC_SCHEMA_VERSION = 1


class _Limits:
    """Bounds applied when persisting untrusted generation artifacts."""

    RAW_OUTPUT_MAX = 4_000


@dataclass(frozen=True)
class SpecKey:
    """The cache key for one generated spec (plan D10).

    ``server`` and ``tool`` are normalised to their stable connector slugs so a
    ``seed:linear`` spec and a live ``linear`` call resolve to one key. The
    ``digest`` is the collision-resistant, filesystem-safe identity used for the
    per-key file name — untrusted server/tool text never becomes a path segment.
    """

    server: str
    tool: str
    output_shape_hash: str
    spec_schema_version: int = CURRENT_SPEC_SCHEMA_VERSION
    skill_version: int = 1

    @classmethod
    def build(
        cls,
        *,
        server: str,
        tool: str,
        output_shape_hash: str,
        skill_version: int,
        spec_schema_version: int = CURRENT_SPEC_SCHEMA_VERSION,
    ) -> "SpecKey":
        """Construct a key, normalising the server/tool identifiers first."""

        return cls(
            server=builtin.server_slug(server),
            tool=builtin.tool_slug(tool),
            output_shape_hash=output_shape_hash,
            spec_schema_version=spec_schema_version,
            skill_version=skill_version,
        )

    @property
    def tool_index_key(self) -> tuple[str, str]:
        """The coarse ``(server, tool)`` key the projector read is indexed by."""

        return (self.server, self.tool)

    def digest(self) -> str:
        """Return a stable, filesystem-safe hex identity for this key."""

        basis = "|".join(
            (
                self.server,
                self.tool,
                self.output_shape_hash,
                str(self.spec_schema_version),
                str(self.skill_version),
            )
        )
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()


class StoredSpec(RuntimeContract):
    """A generated spec plus the provenance needed to key, audit, and emit it."""

    spec: SurfaceSpec
    server: str
    tool: str
    output_shape_hash: str
    spec_schema_version: int = CURRENT_SPEC_SCHEMA_VERSION
    skill_version: int = 1
    generator_model: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_generation(
        cls,
        *,
        key: SpecKey,
        spec: SurfaceSpec,
        generator_model: str,
    ) -> "StoredSpec":
        """Build a stored record from a fresh generation keyed by ``key``."""

        return cls(
            spec=spec,
            server=key.server,
            tool=key.tool,
            output_shape_hash=key.output_shape_hash,
            spec_schema_version=key.spec_schema_version,
            skill_version=key.skill_version,
            generator_model=generator_model,
        )


class RecordedFailure(RuntimeContract):
    """A recorded generation failure, kept for skill iteration (never rendered)."""

    server: str
    tool: str
    output_shape_hash: str
    spec_schema_version: int = CURRENT_SPEC_SCHEMA_VERSION
    skill_version: int = 1
    reason: str = ""
    raw_output: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_failure(
        cls, *, key: SpecKey, reason: str, raw_output: str
    ) -> "RecordedFailure":
        return cls(
            server=key.server,
            tool=key.tool,
            output_shape_hash=key.output_shape_hash,
            spec_schema_version=key.spec_schema_version,
            skill_version=key.skill_version,
            reason=reason[: _Limits.RAW_OUTPUT_MAX],
            raw_output=raw_output[: _Limits.RAW_OUTPUT_MAX],
        )


@runtime_checkable
class SurfaceSpecReadPort(Protocol):
    """The projector's rung-2 read seam (frozen from PRD-02)."""

    def get(self, *, server: str, tool: str) -> SurfaceSpec | None:
        """Return a cached spec for ``(server, tool)`` or ``None``."""
        ...


@runtime_checkable
class SurfaceSpecStorePort(SurfaceSpecReadPort, Protocol):
    """The generation-facing store: full-key read/write + failure recording."""

    def get_stored(self, key: SpecKey) -> StoredSpec | None:
        """Return the stored spec for the full ``key`` or ``None``."""
        ...

    def put(self, key: SpecKey, stored: StoredSpec) -> None:
        """Persist ``stored`` under ``key`` (truth); makes the projector read hit."""
        ...

    def record_failure(self, key: SpecKey, reason: str, raw_output: str) -> None:
        """Record a generation failure under ``key`` for later skill iteration."""
        ...

    def has_failure(self, key: SpecKey) -> bool:
        """Return ``True`` when a prior failure is recorded for ``key``."""
        ...


class InMemorySurfaceSpecStore:
    """Process-local dual store: PRD-02 projector read + PRD-07 generation store.

    Keeps the PRD-02 convenience surface (``put(spec)`` / ``get(server, tool)``)
    so existing callers and tests are unchanged, and adds the full-key
    generation methods. A ``put`` (either shape) refreshes the coarse
    ``(server, tool)`` index the projector reads, so a spec generated this
    process is served on the next call without a round-trip.
    """

    def __init__(self) -> None:
        self._by_tool: dict[tuple[str, str], SurfaceSpec] = {}
        self._by_key: dict[SpecKey, StoredSpec] = {}
        self._failures: dict[SpecKey, RecordedFailure] = {}

    # -- PRD-02 projector read seam ------------------------------------------

    def put_spec(self, spec: SurfaceSpec) -> None:
        """Register ``spec`` under its own ``source`` server/tool (PRD-02)."""

        self._by_tool[self._tool_index_key(spec.source.server, spec.source.tool)] = spec

    def put(self, *args: object, **kwargs: object) -> None:
        """Dispatch the PRD-02 ``put(spec)`` and PRD-07 ``put(key, stored)`` forms.

        A single positional :class:`SurfaceSpec` is the legacy projector
        registration; a ``(SpecKey, StoredSpec)`` pair is a generation write.
        Overloading keeps one obvious ``put`` verb across both the frozen PRD-02
        seam and the new generation store without inventing a second method name.
        """

        if kwargs:
            key = kwargs.get("key")
            stored = kwargs.get("stored")
        elif len(args) == 1:
            spec = args[0]
            if not isinstance(spec, SurfaceSpec):
                raise TypeError("put(spec) requires a SurfaceSpec")
            self.put_spec(spec)
            return
        elif len(args) == 2:
            key, stored = args
        else:  # pragma: no cover - defensive
            raise TypeError("put() expects put(spec) or put(key, stored)")
        if not isinstance(key, SpecKey) or not isinstance(stored, StoredSpec):
            raise TypeError("put(key, stored) requires (SpecKey, StoredSpec)")
        self._by_key[key] = stored
        self._by_tool[key.tool_index_key] = stored.spec

    def get(self, *, server: str, tool: str) -> SurfaceSpec | None:
        """Return the cached spec for ``(server, tool)`` or ``None`` (PRD-02)."""

        return self._by_tool.get(self._tool_index_key(server, tool))

    # -- PRD-07 generation store ---------------------------------------------

    def get_stored(self, key: SpecKey) -> StoredSpec | None:
        """Return the stored spec for the full ``key`` or ``None``."""

        return self._by_key.get(key)

    def record_failure(self, key: SpecKey, reason: str, raw_output: str) -> None:
        """Record a generation failure under ``key``."""

        self._failures[key] = RecordedFailure.from_failure(
            key=key, reason=reason, raw_output=raw_output
        )

    def has_failure(self, key: SpecKey) -> bool:
        """Return ``True`` when a prior failure is recorded for ``key``."""

        return key in self._failures

    @staticmethod
    def _tool_index_key(server: str, tool: str) -> tuple[str, str]:
        return (builtin.server_slug(server), builtin.tool_slug(tool))


class FileSurfaceSpecStore:
    """Durable single-user store: one JSON file per key, written atomically.

    Layout under ``root``::

        specs/<key-digest>.json        # a StoredSpec (truth)
        failures/<key-digest>.json     # a RecordedFailure
        by_tool/<server>.<tool>.json   # {"digest": ...} pointer for the projector read

    Every write is temp-write → ``fsync`` → ``os.replace`` (atomic rename),
    mirroring the file-native object store — a crash leaves either the old file
    or the new file, never a partial one, and never a lingering ``.tmp`` in the
    served path. Directories are ``0o700`` and files ``0o600`` (the OS user is
    the tenant boundary for ``single_user_desktop``).
    """

    _SPECS_DIR: ClassVar[str] = "specs"
    _FAILURES_DIR: ClassVar[str] = "failures"
    _BY_TOOL_DIR: ClassVar[str] = "by_tool"
    _SUFFIX: ClassVar[str] = ".json"
    _TMP_SUFFIX: ClassVar[str] = ".tmp"
    _DIR_MODE: ClassVar[int] = 0o700
    _FILE_MODE: ClassVar[int] = 0o600
    _DIGEST_KEY: ClassVar[str] = "digest"
    ENV_ROOT: ClassVar[str] = "SURFACE_SPEC_STORE_ROOT"
    ENV_FILE_ROOT: ClassVar[str] = "RUNTIME_FILE_STORE_ROOT"
    _SUBDIR: ClassVar[str] = "surfaces"

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).expanduser().resolve()

    @classmethod
    def from_env(
        cls, environ: dict[str, str] | None = None
    ) -> "FileSurfaceSpecStore | None":
        """Build a file store from env, or ``None`` when no root is configured.

        Prefers an explicit ``SURFACE_SPEC_STORE_ROOT``; otherwise nests under
        the desktop file store's ``RUNTIME_FILE_STORE_ROOT/surfaces`` so specs
        live beside the rest of the durable single-user data.
        """

        source = environ if environ is not None else os.environ
        explicit = source.get(cls.ENV_ROOT, "").strip()
        if explicit:
            return cls(explicit)
        file_root = source.get(cls.ENV_FILE_ROOT, "").strip()
        if file_root:
            return cls(Path(file_root).expanduser() / cls._SUBDIR)
        return None

    @property
    def root(self) -> Path:
        return self._root

    # -- PRD-02 projector read seam ------------------------------------------

    def get(self, *, server: str, tool: str) -> SurfaceSpec | None:
        """Return the latest cached spec for ``(server, tool)`` via the pointer."""

        pointer = self._by_tool_path(server, tool)
        digest = self._read_pointer(pointer)
        if digest is None:
            return None
        stored = self._read_stored(self._spec_path(digest))
        return stored.spec if stored is not None else None

    # -- PRD-07 generation store ---------------------------------------------

    def get_stored(self, key: SpecKey) -> StoredSpec | None:
        """Return the stored spec for the full ``key`` or ``None``."""

        return self._read_stored(self._spec_path(key.digest()))

    def put(self, key: SpecKey, stored: StoredSpec) -> None:
        """Persist ``stored`` under ``key`` and refresh the projector pointer."""

        self._atomic_write(
            self._spec_path(key.digest()),
            stored.model_dump_json(),
        )
        self._atomic_write(
            self._by_tool_path(key.server, key.tool),
            json.dumps({self._DIGEST_KEY: key.digest()}),
        )

    def record_failure(self, key: SpecKey, reason: str, raw_output: str) -> None:
        """Record a generation failure under ``key`` (never served)."""

        failure = RecordedFailure.from_failure(
            key=key, reason=reason, raw_output=raw_output
        )
        self._atomic_write(self._failure_path(key.digest()), failure.model_dump_json())

    def has_failure(self, key: SpecKey) -> bool:
        """Return ``True`` when a prior failure file exists for ``key``."""

        return self._failure_path(key.digest()).exists()

    # -- paths ----------------------------------------------------------------

    def _spec_path(self, digest: str) -> Path:
        return self._root / self._SPECS_DIR / f"{digest}{self._SUFFIX}"

    def _failure_path(self, digest: str) -> Path:
        return self._root / self._FAILURES_DIR / f"{digest}{self._SUFFIX}"

    def _by_tool_path(self, server: str, tool: str) -> Path:
        name = f"{builtin.server_slug(server)}.{builtin.tool_slug(tool)}"
        # Hash the slug pair so an exotic tool name can never escape the dir.
        safe = hashlib.sha256(name.encode("utf-8")).hexdigest()
        return self._root / self._BY_TOOL_DIR / f"{safe}{self._SUFFIX}"

    # -- io -------------------------------------------------------------------

    def _atomic_write(self, target: Path, text: str) -> None:
        target.parent.mkdir(mode=self._DIR_MODE, parents=True, exist_ok=True)
        tmp = target.with_name(target.name + self._TMP_SUFFIX)
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        try:
            target.chmod(self._FILE_MODE)
        except OSError:  # pragma: no cover - some filesystems reject chmod
            pass

    def _read_stored(self, path: Path) -> StoredSpec | None:
        raw = self._read_json(path)
        if raw is None:
            return None
        try:
            return StoredSpec.model_validate(raw)
        except ValidationError:
            # A corrupt/legacy file must never crash a live render; treat it as a
            # miss so generation re-runs and overwrites it.
            _LOGGER.warning("[surfaces.store] discarding invalid spec file %s", path)
            return None

    def _read_pointer(self, path: Path) -> str | None:
        raw = self._read_json(path)
        if not isinstance(raw, dict):
            return None
        digest = raw.get(self._DIGEST_KEY)
        return digest if isinstance(digest, str) and digest else None

    @staticmethod
    def _read_json(path: Path) -> object | None:
        try:
            text = path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None


__all__ = [
    "CURRENT_SPEC_SCHEMA_VERSION",
    "FileSurfaceSpecStore",
    "InMemorySurfaceSpecStore",
    "RecordedFailure",
    "SpecKey",
    "StoredSpec",
    "SurfaceSpecReadPort",
    "SurfaceSpecStorePort",
]

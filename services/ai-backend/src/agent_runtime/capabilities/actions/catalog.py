"""Curated per-connector action catalog (PRD-C1, rung 1).

Data, not code (SDR §12 risk table): one JSON file per connector under
``catalog_data/``, each declaring ``{op: read|write|destructive}``. Loaded +
validated **once at import** (mirroring ``surfaces/builtin.py``): a malformed
file raises here, so a bad catalog fails the test suite rather than degrading a
live run. Keys are normalized through the importable ``server_slug`` /
``tool_slug`` from ``surfaces/builtin.py``; a duplicate ``(connector, op)``
raises; lookup is exact-match only (no wildcards — fail-closed).

Behavior lives on :class:`ActionCatalog`; the module binds a process-wide
singleton :data:`ACTION_CATALOG` the classifier composes over. The emission
site imports the singleton — it never constructs the catalog or re-reads JSON
per tool call.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from importlib.resources import files
from importlib.resources.abc import Traversable

from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.capabilities.actions.contracts import CatalogActionKind
from agent_runtime.capabilities.surfaces.builtin import server_slug, tool_slug

_CATALOG_DIR_NAME = "catalog_data"
_CATALOG_SUFFIX = ".json"


class ActionCatalogError(RuntimeError):
    """Raised at import when a catalog file is malformed or invalid.

    The message always names the offending file so a failing test points
    straight at the fixture to fix (same discipline as ``BuiltinSpecError``).
    """


class _CatalogFile(BaseModel):
    """Validated shape of one ``catalog_data/<connector>.json`` file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog_version: int = Field(ge=1)
    connector: str = Field(min_length=1)
    operations: dict[str, CatalogActionKind]


class ActionCatalog:
    """Exact-match ``(connector, op) -> CatalogActionKind`` lookup.

    Constructed from a normalized index. The module-level :data:`ACTION_CATALOG`
    is the process-wide instance; tests may construct their own over a temp dir
    via :meth:`from_directory`.
    """

    __slots__ = ("_entries",)

    def __init__(self, entries: Mapping[tuple[str, str], CatalogActionKind]) -> None:
        self._entries = dict(entries)

    @classmethod
    def from_directory(cls, directory: Traversable) -> "ActionCatalog":
        """Load + validate every ``*.json`` under ``directory`` into a catalog.

        Pure and re-runnable so a test can point it at a temp dir carrying a
        deliberately corrupt fixture and assert the raised message names the
        file. Raises :class:`ActionCatalogError` on invalid JSON, a
        schema/model violation, or a duplicate ``(connector, op)`` key.
        """

        entries: dict[tuple[str, str], CatalogActionKind] = {}
        files_sorted = sorted(
            (
                entry
                for entry in directory.iterdir()
                if entry.name.endswith(_CATALOG_SUFFIX)
            ),
            key=lambda entry: entry.name,
        )
        for entry in files_sorted:
            name = entry.name
            try:
                raw = json.loads(entry.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ActionCatalogError(f"{name}: invalid JSON — {exc}") from exc
            try:
                parsed = _CatalogFile.model_validate(raw)
            except Exception as exc:  # noqa: BLE001 - re-raised as a named error
                raise ActionCatalogError(f"{name}: {exc}") from exc
            connector = server_slug(parsed.connector)
            for raw_op, kind in parsed.operations.items():
                key = (connector, tool_slug(raw_op))
                if key in entries:
                    raise ActionCatalogError(
                        f"{name}: duplicate catalog entry for "
                        f"connector={key[0]!r} op={key[1]!r}"
                    )
                entries[key] = kind
        return cls(entries)

    def lookup(self, connector: str, op: str) -> CatalogActionKind | None:
        """Return the declared kind for ``(connector, op)``, or ``None``.

        Both arguments are normalized on the way in, so callers may pass the
        raw ``server_name`` / ``tool_name`` — exactly the strings the ledger
        emitter has. A miss returns ``None`` (fail-closed: the classifier falls
        through to annotations/default).
        """

        return self._entries.get((server_slug(connector), tool_slug(op)))

    def all_entries(self) -> dict[tuple[str, str], CatalogActionKind]:
        """Return a copy of the normalized index (tests)."""

        return dict(self._entries)


def _default_catalog_dir() -> Traversable:
    return files(__package__).joinpath(_CATALOG_DIR_NAME)


# Loaded once at import. A bad catalog file raises here -> collected as a test
# failure (the package fails to import), never a silent runtime degradation.
ACTION_CATALOG: ActionCatalog = ActionCatalog.from_directory(_default_catalog_dir())


__all__ = ["ACTION_CATALOG", "ActionCatalog", "ActionCatalogError"]

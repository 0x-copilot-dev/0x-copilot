"""Builtin curated SurfaceSpec library (generative-UI PRD-02).

Ships one hand-authored ``SurfaceSpec`` per ``(server, tool)`` for the catalog
connectors whose output shapes are stable enough to bind at dev time. Every
JSON file under ``builtin_specs/`` is loaded **and validated** exactly once at
import via :func:`validate_surface_spec`; a malformed or schema-violating file
raises :class:`BuiltinSpecError` (naming the file) so a bad builtin fails the
test suite rather than degrading a live run.

This is the first rung of the spec-acquisition ladder (plan D4):
``builtin → store → generate-async``. Only the builtin rung lives here; the
store port + generator arrive in PRD-07/08.
"""

from __future__ import annotations

import json
import re
from importlib.resources import files
from importlib.resources.abc import Traversable

from agent_runtime.capabilities.surfaces.spec_models import (
    SurfaceSpec,
    SurfaceSpecError,
    validate_surface_spec,
)

_SPECS_DIR_NAME = "builtin_specs"
_SPEC_SUFFIX = ".json"


class BuiltinSpecError(RuntimeError):
    """Raised at import when a builtin spec file is malformed or invalid.

    The message always names the offending file so a failing test points
    straight at the fixture to fix.
    """


def server_slug(raw: str) -> str:
    """Reduce a server name/id to the stable connector slug used for lookup.

    Strips a catalog prefix (``seed:linear`` → ``linear``) and collapses any
    non-``[a-z0-9]`` run to a single dash. Both the builtin index keys and the
    runtime ``server_name`` queries pass through here, so ``"seed:linear"`` in a
    spec's ``source.server`` and a live ``"linear"`` call resolve to the same
    entry, and the surface URI's server segment is stable regardless of naming.
    """

    text = raw.strip().lower()
    if ":" in text:
        text = text.rsplit(":", 1)[1]
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def tool_slug(raw: str) -> str:
    """Normalise a tool name for lookup (case-insensitive; no prefix stripping)."""

    return raw.strip().lower()


def _spec_key(server: str, tool: str) -> tuple[str, str]:
    return (server_slug(server), tool_slug(tool))


def load_builtin_specs(
    directory: Traversable,
) -> dict[tuple[str, str], SurfaceSpec]:
    """Load + validate every ``*.json`` under ``directory`` into a lookup index.

    Pure and re-runnable so a test can point it at a temp dir carrying a
    deliberately corrupt fixture and assert the raised message names the file.
    Raises :class:`BuiltinSpecError` on invalid JSON, a schema/model violation,
    or a duplicate ``(server, tool)`` key.
    """

    registry: dict[tuple[str, str], SurfaceSpec] = {}
    entries = sorted(
        (entry for entry in directory.iterdir() if entry.name.endswith(_SPEC_SUFFIX)),
        key=lambda entry: entry.name,
    )
    for entry in entries:
        name = entry.name
        try:
            raw = json.loads(entry.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BuiltinSpecError(f"{name}: invalid JSON — {exc}") from exc
        try:
            spec = validate_surface_spec(raw)
        except SurfaceSpecError as exc:
            raise BuiltinSpecError(f"{name}: {exc}") from exc
        key = _spec_key(spec.source.server, spec.source.tool)
        if key in registry:
            raise BuiltinSpecError(
                f"{name}: duplicate builtin spec for server={key[0]!r} tool={key[1]!r}"
            )
        registry[key] = spec
    return registry


def _default_specs_dir() -> Traversable:
    return files(__package__).joinpath(_SPECS_DIR_NAME)


# Loaded once at import. A bad builtin file raises here → collected as a test
# failure (the package fails to import), never a silent runtime degradation.
_REGISTRY: dict[tuple[str, str], SurfaceSpec] = load_builtin_specs(_default_specs_dir())


def lookup(server: str, tool: str) -> SurfaceSpec | None:
    """Return the builtin spec for ``(server, tool)``, or ``None`` if uncurated."""

    return _REGISTRY.get(_spec_key(server, tool))


def all_specs() -> tuple[SurfaceSpec, ...]:
    """Return every loaded builtin spec (test/introspection helper)."""

    return tuple(_REGISTRY.values())


__all__ = [
    "BuiltinSpecError",
    "all_specs",
    "load_builtin_specs",
    "lookup",
    "server_slug",
    "tool_slug",
]

"""MCP tool annotations — untrusted protocol hints, registry-captured (PRD-C1).

The MCP protocol ships optional ``annotations`` on a tool descriptor
(``readOnlyHint`` / ``destructiveHint`` / ``idempotentHint`` / ``title`` /
``openWorldHint`` / vendor extras). They are OPTIONAL and UNTRUSTED: per SDR §10
invariant 1, annotations alone can never grant auto-run — only the curated
catalog can. The classifier uses them ONLY to tighten (rung 2).

Capture is registry-only (a per-run ContextVar), NOT a field on
``McpToolDescriptor`` — the descriptor is ``extra="forbid"`` and its dumps reach
model-visible listings, so registry capture keeps every existing payload
byte-identical (flag-off invariant).
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar

from agent_runtime.capabilities.surfaces.builtin import server_slug, tool_slug
from agent_runtime.execution.contracts import RuntimeContract


class _WireKey:
    """The three camelCase annotation keys we model. Everything else ignored."""

    READ_ONLY = "readOnlyHint"
    DESTRUCTIVE = "destructiveHint"
    IDEMPOTENT = "idempotentHint"


class McpToolAnnotations(RuntimeContract):
    """The three MCP annotation hints we model (snake_case, validated).

    ``extra="forbid"`` (via ``RuntimeContract``): NEVER ``model_validate`` a raw
    wire dict against this model — the wire ships camelCase + ``title`` /
    ``openWorldHint`` / vendor keys and would RAISE. :meth:`from_wire` is the
    only entry point.
    """

    read_only_hint: bool | None = None
    destructive_hint: bool | None = None
    idempotent_hint: bool | None = None

    @classmethod
    def from_wire(cls, raw: Mapping[str, object]) -> "McpToolAnnotations":
        """Build from a raw wire ``annotations`` mapping.

        Reads ONLY the three camelCase keys; every other key is ignored; a
        non-bool value coerces to ``None`` (never trusts a truthy non-bool).
        """

        return cls(
            read_only_hint=cls._as_bool(raw.get(_WireKey.READ_ONLY)),
            destructive_hint=cls._as_bool(raw.get(_WireKey.DESTRUCTIVE)),
            idempotent_hint=cls._as_bool(raw.get(_WireKey.IDEMPOTENT)),
        )

    @staticmethod
    def _as_bool(value: object) -> bool | None:
        # Strict: only a real bool survives; ``1`` / ``"true"`` / anything else
        # -> None. A hint we can't trust is no hint (fail-closed).
        return value if isinstance(value, bool) else None


_ANNOTATIONS_REGISTRY_CTX: ContextVar[
    dict[tuple[str, str], McpToolAnnotations] | None
] = ContextVar("mcp_tool_annotations_registry", default=None)


class McpToolAnnotationsRegistry:
    """Per-run ``(server_slug, tool_slug) -> McpToolAnnotations`` registry.

    Mirrors ``McpDisplayRegistryContext``'s bind/unbind/active/register/get
    ContextVar pattern, but keyed on a normalized COMPOSITE ``(server, tool)``
    rather than ``tool_name`` alone — so two servers exposing an identically
    named tool never collide, and the classifier can disambiguate by connector.

    ``register`` (writes with ``self.card.name``) and ``get`` (reads with the
    model-supplied ``server_name``) BOTH normalize through ``server_slug`` /
    ``tool_slug``, so a seed-prefixed vs bare connector name resolves to one
    slug. A miss returns ``None`` -> catalog/default -> fail-closed (annotations
    only ever tighten), so a miss is safe.
    """

    @classmethod
    def bind_for_run(
        cls, registry: dict[tuple[str, str], McpToolAnnotations]
    ) -> object:
        """Set the active registry; return the previous token for restoration."""

        return _ANNOTATIONS_REGISTRY_CTX.set(registry)

    @classmethod
    def unbind(cls, token: object) -> None:
        """Restore the previous binding. Safe to call with the bind result."""

        _ANNOTATIONS_REGISTRY_CTX.reset(token)  # type: ignore[arg-type]

    @classmethod
    def active(cls) -> dict[tuple[str, str], McpToolAnnotations] | None:
        """Return the active registry or ``None`` (test helper / debugging)."""

        return _ANNOTATIONS_REGISTRY_CTX.get(None)

    @classmethod
    def register(cls, server: str, tool: str, annotations: McpToolAnnotations) -> None:
        """Record annotations for ``(server, tool)`` on the active run.

        No-op when no registry is bound (replay / eval / unit tests). Last write
        wins on a duplicate composite key.
        """

        registry = _ANNOTATIONS_REGISTRY_CTX.get(None)
        if registry is None:
            return
        registry[(server_slug(server), tool_slug(tool))] = annotations

    @classmethod
    def get(cls, server: str, tool: str) -> McpToolAnnotations | None:
        """Return the annotations for ``(server, tool)`` if any; else ``None``."""

        registry = _ANNOTATIONS_REGISTRY_CTX.get(None)
        if registry is None:
            return None
        return registry.get((server_slug(server), tool_slug(tool)))


__all__ = ["McpToolAnnotations", "McpToolAnnotationsRegistry"]

"""Payload-key, value, and message constants for v2 ledger emission (PRD-A3 D7).

Per ``services/ai-backend/CLAUDE.md`` no repeated key/value/message string is
inlined. Event-type *values* are NOT redefined here — they come from the A1
vocabulary (``LedgerEventType`` in ``ledger_models``); this module owns only the
payload-field keys, the A3-frozen constant values, and the emit-time summaries.
"""

from __future__ import annotations


class Keys:
    """Field-name constants for the four A3 ledger payloads (SDR §5, verbatim)."""

    class Field:
        """Payload keys. Values are the wire keys — do not rename (frozen)."""

        V = "v"
        CALL_ID = "call_id"
        CONNECTOR = "connector"
        OP = "op"
        CLASS = "class"
        BASIS = "basis"
        LATENCY_MS = "latency_ms"
        PAYLOAD_REF = "payload_ref"
        SURFACE_ID = "surface_id"
        KIND = "kind"
        SOURCE = "source"
        TITLE = "title"
        TIER = "tier"
        SPEC_REF = "spec_ref"
        GEN = "gen"
        MODEL = "model"
        # ``gen.ms`` — generation duration, populated by the B3 ViewDeriver
        # (A3 emitted ``gen.model`` only).
        MS = "ms"
        # ``view.preference`` keys (PRD-B3).
        KEEP = "keep"
        ACTOR = "actor"
        # PRD-C2 gate.opened / gate.resolved payload keys (SDR §5, verbatim).
        GATE_ID = "gate_id"
        PURPOSE = "purpose"
        SCOPES = "scopes"
        AUTH_STATE = "auth_state"
        OUTCOME = "outcome"
        WRITE_POLICY = "write_policy"


class Values:
    """Constant payload values A3 freezes (no classifier / no measurement yet)."""

    # ``action.classified`` in A3 always records the honest "we made no policy
    # decision" pair — a real classifier arrives in PRD-C1.
    CLASS_UNKNOWN = "unknown"
    BASIS_DEFAULT = "default"

    # ``view.derived`` tiers/bases mapped from the v1 envelope (D1).
    TIER_GENERIC = "generic"
    TIER_SHAPED = "shaped"
    BASIS_SCHEMA = "schema"
    BASIS_REGISTRY = "registry"
    BASIS_GENERATED = "generated"

    # ``payload_ref`` scheme (D1/D7): ``call:<call_id>`` resolves to the
    # ``tool_result`` event carrying the same ``call_id`` in this run's replay.
    CALL_REF_PREFIX = "call:"

    # ``spec_ref`` scheme (B3): ``spec:<server-slug>/<tool-slug>`` — the same
    # ``<scheme>:<...>`` ref convention A3 uses for ``payload_ref``, keyed on the
    # normalised connector/tool a registry or generated spec binds to.
    SPEC_REF_PREFIX = "spec:"

    # ``view.preference`` constant values (PRD-B3, SDR §5).
    KEEP_GENERIC = "generic"
    KEEP_SHAPED = "shaped"
    ACTOR_USER = "user"

    # Every A3 payload carries ``v: 1`` (SDR §5 — versioned from day one).
    PAYLOAD_V = 1

    # Longest a folded/derived surface title may be (D1).
    TITLE_MAX_LEN = 120


class Messages:
    """Emit-time summaries for the ledger events (D3). ``action.classified``
    has no summary (``None``) — it is not a user-facing beat in A3."""

    READ_EXECUTED = "auto-ran (read)"
    SURFACE_CREATED = "Prepared a surface"
    VIEW_DERIVED = "Derived a view"
    VIEW_PREFERENCE = "Set a view preference"

    # Log tag when an emitter method swallows its own exception (D3/D8).
    EMIT_RAISED = "[surfaces_v2] ledger.emit_raised"

    # Log tag when classification raises at the emission site (PRD-C1). The
    # classifier is pure + total, so this should never fire — but the emit path
    # degrades to ``class=unknown`` / ``basis=default`` (honest fallback) rather
    # than failing the tool call.
    CLASSIFY_RAISED = "[surfaces] action.classify_raised"


class Titles:
    """Fallback surface-title formatting (D1)."""

    SEPARATOR = " · "


__all__ = ["Keys", "Messages", "Titles", "Values"]

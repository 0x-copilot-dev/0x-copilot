"""Per-run MCP discovery seam for surfacing non-blocking Connect/Skip cards.

The service is bound to the worker via a :class:`ContextVar` and exposed through
a class method so tools never need to thread a runtime context through their
signatures. On each suggestion it validates server authorization, audits the
event, and emits a single ``mcp_auth_required`` envelope with discovery metadata
so the frontend renders the non-blocking card instead of the blocking auth gate.

Idempotency is keyed on ``(run_id, server_id)``; a second call for the same pair
returns ``already_suggested`` without re-emitting. Already-authenticated servers
short-circuit before the auth-session creator is hit.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Protocol

from agent_runtime.api.constants import Keys, Messages
from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpAuthState,
    McpServerCard,
    McpServerHealth,
    McpTransport,
)
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    CatalogSuggestionCard,
    JsonObject,
    StreamEventSource,
)
from runtime_api.schemas import RuntimeApiEventType

if TYPE_CHECKING:  # pragma: no cover - typing-only imports
    from agent_runtime.api.events import RuntimeEventProducer
    from agent_runtime.capabilities.mcp.middleware.auth_mcp import (
        McpAuthSessionCreator,
    )
    from runtime_api.schemas import RunRecord
    from runtime_worker.audit import WorkerAuditEmitter


class McpServerLookup(Protocol):
    """Subset of the MCP registry the discovery service depends on."""

    async def list_available_servers(
        self, context: AgentRuntimeContext
    ) -> tuple[McpServerCard, ...]:
        """Return the per-context catalog already authorized for the run."""


# Surfaced on the ``mcp_auth_required`` payload when the suggestion came from the
# catalog (uninstalled). The frontend reads this field to route the Connect button
# to the install flow instead of the already-installed-server OAuth path.
_CATALOG_SLUG_FIELD = "catalog_slug"
# When False (default), Connect runs a 1-click install + auth chain inline.
# When True, it opens a credentials form so the user can paste a pre-registered
# OAuth client first.
_REQUIRES_PRE_REGISTERED_FIELD = "requires_pre_registered_client"


def _synthesize_catalog_card(suggestion: CatalogSuggestionCard) -> McpServerCard:
    """Build a minimal McpServerCard from a catalog suggestion.

    The synthesized card is only consumed by ``McpDiscoveryService``
    locally — it never enters the registry, never reaches the agent's
    ``list_available_servers``, never reaches MCP loaders. We pin
    ``health=HEALTHY`` and ``auth_state=UNAUTHENTICATED`` because the
    discovery flow's only branches on this card check ``enabled`` and
    ``auth_state`` (skip emit when authenticated). ``server_id`` uses
    the ``seed:`` prefix so it round-trips through the FE's existing
    install matching.
    """

    seed_id = f"seed:{suggestion.slug}"
    return McpServerCard(
        name=suggestion.slug,
        server_id=seed_id,
        display_name=suggestion.display_name,
        short_description=suggestion.description or suggestion.display_name,
        transport=McpTransport.HTTP,
        auth_mode=McpAuthMode.OAUTH2,
        auth_state=McpAuthState.UNAUTHENTICATED,
        health=McpServerHealth.HEALTHY,
        load_cost=1,
        enabled=True,
    )


class _Results:
    """Tool-facing result-status strings. Stable for replay parity."""

    EMITTED = "emitted"
    ALREADY_SUGGESTED = "already_suggested"
    ALREADY_AUTHENTICATED = "already_authenticated"
    UNKNOWN_SERVER = "unknown_server"
    SERVER_DISABLED = "server_disabled"
    DISCOVERY_DISABLED = "discovery_disabled"
    # Status returned when the per-turn suggestion cap is reached
    # (default: one suggestion per turn) to prevent stacking CTAs.
    PER_TURN_CAP_REACHED = "per_turn_cap_reached"


# At most one suggestion per turn. A "turn" maps to one
# ``McpDiscoveryService`` instance: the worker creates a fresh
# instance per run, which covers one user → assistant cycle.
_SUGGESTIONS_PER_TURN = 1


class McpDiscoveryService:
    """Per-run non-blocking discovery emitter.

    The worker creates one instance per run, binds it via
    :meth:`bind_for_run`, and clears it via :meth:`unbind` on teardown.
    The instance owns:

    - the in-memory idempotency cache (``server_id -> approval_id``),
    - the audit chain entry (one row per *unique* suggestion),
    - one ``mcp_auth_required`` event per unique suggestion.

    Tools call the bound service via :meth:`suggest`; when no service is
    bound (feature flag off, or invocation outside a run) the call
    returns ``{"status": "discovery_disabled"}`` so the agent can keep
    going — discovery is best-effort decoration, never required for
    correctness.
    """

    def __init__(
        self,
        *,
        run: "RunRecord",
        runtime_context: AgentRuntimeContext,
        producer: "RuntimeEventProducer",
        audit_emitter: "WorkerAuditEmitter",
        registry: McpServerLookup,
        auth_session_creator: "McpAuthSessionCreator | None",
    ) -> None:
        self._run = run
        self._runtime_context = runtime_context
        self._producer = producer
        self._audit_emitter = audit_emitter
        self._registry = registry
        self._auth_session_creator = auth_session_creator
        # server_id -> approval_id of the most recent emitted suggestion.
        self._suggested: dict[str, str] = {}

    @property
    def run_id(self) -> str:
        return self._run.run_id

    async def suggest(
        self,
        *,
        server_id: str,
        reason: str,
        expected_value: str,
    ) -> JsonObject:
        """Surface a Connect/Skip card for *server_id*.

        Returns a status envelope the tool wraps as its return value.
        Idempotent on ``server_id`` per run; already-authenticated
        servers short-circuit before any side effect.
        """

        normalized_id = (server_id or "").strip()
        if not normalized_id:
            return {"status": _Results.UNKNOWN_SERVER, "server_id": server_id}

        # Idempotency cache key — case-insensitive and seed-prefix-stripped
        # so `linear`, `Linear`, `seed:linear` all collapse to one entry.
        # Without this, a model that switches casing between calls bypasses
        # the cache and emits a duplicate Connect card.
        cache_key = normalized_id.lower()
        if cache_key.startswith("seed:"):
            cache_key = cache_key[len("seed:") :]
        cached_approval = self._suggested.get(cache_key)
        if cached_approval is not None:
            return {
                "status": _Results.ALREADY_SUGGESTED,
                "server_id": normalized_id,
                Keys.Field.APPROVAL_ID: cached_approval,
            }

        # Soft per-turn cap. The system prompt asks the agent to suggest at
        # most one connector per turn; this is the runtime backstop for when
        # the model ignores that. Idempotent re-calls for the same slug (above)
        # are NOT subject to the cap because they're no-ops, but a new slug
        # after the cap returns ``per_turn_cap_reached`` so the agent responds
        # in plain text instead of stacking cards. Cap is checked before any
        # side effect so cap state never pollutes the audit chain.
        if len(self._suggested) >= _SUGGESTIONS_PER_TURN:
            return {
                "status": _Results.PER_TURN_CAP_REACHED,
                "server_id": normalized_id,
            }

        lookup = await self._lookup_card_with_source(normalized_id)
        if lookup is None:
            # Not in the per-run catalog — never emit a card for a server
            # the model only thinks exists. Audit nothing (no resource
            # was touched). The tool returns the status so the agent can
            # course-correct without retrying.
            return {"status": _Results.UNKNOWN_SERVER, "server_id": normalized_id}
        card, lookup_source = lookup

        if not card.enabled:
            await self._audit(
                server_id=normalized_id,
                outcome=_Results.SERVER_DISABLED,
                reason=reason,
            )
            return {"status": _Results.SERVER_DISABLED, "server_id": normalized_id}

        # `==` not `is`: Pydantic re-validation in some code paths can
        # return enum instances that aren't identity-equal to the
        # imported singleton even though they compare equal. Using `==`
        # keeps the short-circuit reliable across both paths.
        if card.auth_state == McpAuthState.AUTHENTICATED:
            # Already useful — no card needed; the agent should just
            # use the server. Skip audit + event to keep the suggestion
            # surface tight.
            return {
                "status": _Results.ALREADY_AUTHENTICATED,
                "server_id": normalized_id,
            }

        approval_id = self._approval_id(normalized_id)
        payload = await self._build_payload(
            card=card,
            approval_id=approval_id,
            reason=reason,
            expected_value=expected_value,
            lookup_source=lookup_source,
        )
        await self._audit(
            server_id=normalized_id,
            outcome=_Results.EMITTED,
            reason=reason,
            approval_id=approval_id,
        )
        await self._producer.append_api_event(
            run=self._run,
            source=StreamEventSource.TOOL,
            event_type=RuntimeApiEventType.MCP_AUTH_REQUIRED,
            payload=payload,
        )
        self._suggested[cache_key] = approval_id
        return {
            "status": _Results.EMITTED,
            "server_id": normalized_id,
            Keys.Field.APPROVAL_ID: approval_id,
            Keys.Field.DISPLAY_TITLE: card.display_name or card.name,
        }

    @classmethod
    async def offer(
        cls,
        *,
        server_id: str,
        reason: str,
        expected_value: str,
    ) -> JsonObject:
        """Resolve the active service from the ContextVar and call ``suggest``.

        Returns ``{"status": "discovery_disabled"}`` when no service is
        bound — tools call this from anywhere in the run's call stack
        without threading a runtime context through their signatures.
        """

        service = _MCP_DISCOVERY_CTX.get(None)
        if service is None:
            return {"status": _Results.DISCOVERY_DISABLED, "server_id": server_id}
        return await service.suggest(
            server_id=server_id,
            reason=reason,
            expected_value=expected_value,
        )

    @classmethod
    def bind_for_run(cls, service: "McpDiscoveryService") -> object:
        """Set the active service; return the previous token for restoration."""

        return _MCP_DISCOVERY_CTX.set(service)

    @classmethod
    def unbind(cls, token: object) -> None:
        """Restore the previous service token. Safe to call with bind result."""

        _MCP_DISCOVERY_CTX.reset(token)  # type: ignore[arg-type]

    @classmethod
    def active(cls) -> "McpDiscoveryService | None":
        """Return the active service or ``None`` (test helper / debugging)."""

        return _MCP_DISCOVERY_CTX.get(None)

    async def _lookup_card(self, server_id: str) -> McpServerCard | None:
        result = await self._lookup_card_with_source(server_id)
        return result[0] if result is not None else None

    async def _lookup_card_with_source(
        self, server_id: str
    ) -> tuple[McpServerCard, str] | None:
        """Resolve a server_id to a card, tagging the lookup source.

        Source is ``"registry"`` when the card came from the user's
        installed servers, ``"catalog"`` when synthesized from
        ``runtime_context.suggested_connectors``. The FE-facing
        ``catalog_slug`` sentinel is only stamped on catalog hits so a
        registry-hit doesn't mis-route the Connect button to the
        install overlay.

        Lookup matches case-insensitively and accepts both the bare
        slug ("linear") and the seed-prefixed id ("seed:linear"). The
        catalog fallback already normalizes via :meth:`_lookup_suggestion`;
        without matching normalization here, a model that calls with a
        capitalized or seed-prefixed slug misses the registry, falls
        through to a synthesized UNAUTHENTICATED catalog card, and
        emits a Connect prompt for an already-authenticated server.
        """

        normalized = server_id.strip().lower()
        bare = (
            normalized[len("seed:") :] if normalized.startswith("seed:") else normalized
        )
        for card in await self._registry.list_available_servers(self._runtime_context):
            card_sid = (card.server_id or "").lower()
            card_name = (card.name or "").lower()
            if (
                card_sid == normalized
                or card_name == normalized
                or card_name == bare
                or card_sid == f"seed:{bare}"
            ):
                return (card, "registry")
        # Fall back to the catalog suggestions snapshot. These are
        # uninstalled connectors the backend pre-filtered (paused / muted
        # excluded), so a synthesized ``McpServerCard`` is the right signal
        # for "exists in the catalog but the user hasn't connected it yet".
        # The card carries ``auth_state=UNAUTHENTICATED`` so the
        # ``ALREADY_AUTHENTICATED`` short-circuit in ``suggest`` doesn't
        # fire; the FE branches on the catalog_slug payload field to route
        # Connect through the install flow rather than raw OAuth.
        suggestion = self._lookup_suggestion(server_id)
        if suggestion is not None:
            return (_synthesize_catalog_card(suggestion), "catalog")
        return None

    def _lookup_suggestion(self, server_id: str) -> CatalogSuggestionCard | None:
        normalized = server_id.strip().lower()
        bare = (
            normalized[len("seed:") :] if normalized.startswith("seed:") else normalized
        )
        for card in self._runtime_context.suggested_connectors:
            if card.slug == bare:
                return card
        return None

    async def _build_payload(
        self,
        *,
        card: McpServerCard,
        approval_id: str,
        reason: str,
        expected_value: str,
        lookup_source: str = "registry",
    ) -> JsonObject:
        # The discovery card needs the same wire fields as the blocking
        # auth gate so the FE can re-use ConnectorAuthTool with a single
        # variant switch on ``discovery_reason``. The auth-session creator
        # is optional: when absent (e.g. the registry's provider doesn't
        # implement OAuth), we still emit the card with empty
        # ``auth_url`` / ``expires_at`` strings so the schema accepts it.
        # The FE's "Connect" path falls back to the existing
        # ``connectors.authenticate(server_id)`` flow which kicks off
        # OAuth on the user's own browser.
        server_id = card.server_id or card.name
        display_name = card.display_name or card.name
        message = Messages.Event.MCP_AUTH_REQUIRED
        auth_url = ""
        expires_at = ""
        # Only create an auth session for *installed* servers
        # (lookup_source="registry"). Catalog suggestions point at
        # uninstalled connectors; the user's ``mcp_servers`` row doesn't
        # exist yet, so calling ``create_auth_session`` would 404 and the
        # tool call would fail. The FE branches Connect on ``catalog_slug``
        # to open the install overlay, which creates the row and starts
        # OAuth in a single flow — the discovery card doesn't need a
        # pre-baked auth_url.
        if lookup_source == "registry" and self._auth_session_creator is not None:
            session = await self._auth_session_creator.create_auth_session(
                server_id=server_id,
                runtime_context=self._runtime_context,
            )
            auth_url = session.auth_url
            expires_at = session.expires_at.isoformat()
            display_name = session.display_name or display_name
        payload: dict[str, object] = {
            Keys.Field.API_EVENT_TYPE: RuntimeApiEventType.MCP_AUTH_REQUIRED.value,
            Keys.Field.EVENT_TYPE: RuntimeApiEventType.MCP_AUTH_REQUIRED.value,
            Keys.Field.APPROVAL_ID: approval_id,
            "action_id": approval_id,
            Keys.Field.APPROVAL_KIND: "mcp_auth",
            Keys.Field.SERVER_ID: server_id,
            Keys.Field.SERVER_NAME: card.name,
            "display_name": display_name,
            Keys.Field.AUTH_URL: auth_url,
            Keys.Field.EXPIRES_AT: expires_at,
            Keys.Payload.MESSAGE: message,
            Keys.Field.DISCOVERY_REASON: reason,
            Keys.Field.EXPECTED_VALUE: expected_value,
        }
        # Flag uninstalled catalog suggestions so the FE deep-links the
        # Connect button to the catalog install flow instead of starting
        # OAuth against a server row that doesn't exist yet. Stamped only
        # when the lookup hit the catalog fallback; a registry hit keeps
        # the existing OAuth flow even if the same slug also appears in
        # ``suggested_connectors``.
        if lookup_source == "catalog":
            payload[_CATALOG_SLUG_FIELD] = card.name
            # Also stamp ``requires_pre_registered_client`` so the FE can
            # branch the Connect button:
            #   False (default): 1-click install + auth + redirect, no
            #     overlay needed.
            #   True: open the credentials form so the user can paste a
            #     pre-registered OAuth client BEFORE install (vendors
            #     like Atlassian, GitHub, Intercom, PayPal, Plaid, Square).
            for entry in self._runtime_context.suggested_connectors:
                if entry.slug == card.name:
                    payload[_REQUIRES_PRE_REGISTERED_FIELD] = (
                        entry.requires_pre_registered_client
                    )
                    break
        return payload

    def _approval_id(self, server_id: str) -> str:
        # Deterministic across replays — the FE reducer is keyed by
        # approval_id so a redelivered event must produce the same id.
        return f"mcp_discovery:{self._run.run_id}:{server_id}"

    async def _audit(
        self,
        *,
        server_id: str,
        outcome: str,
        reason: str,
        approval_id: str | None = None,
    ) -> None:
        metadata: dict[str, object] = {
            "conversation_id": self._run.conversation_id,
            "server_id": server_id,
            "reason": reason[:160],  # cap; never include free-form payloads
            "outcome": outcome,
        }
        if approval_id is not None:
            metadata[Keys.Field.APPROVAL_ID] = approval_id
        # Reuse the worker emitter's underlying writer. ``actor_type``
        # is ``worker`` because the agent harness — not the user —
        # initiates the suggestion; the SIEM dashboards already split
        # worker vs. user vs. system rows.
        await self._audit_emitter._emit(  # noqa: SLF001 — typed seam re-use
            event_type=Messages.Audit.MCP_DISCOVERY_SUGGESTED,
            run=self._run,
            actor_type="worker",
            resource_type="mcp_server",
            resource_id=server_id,
            outcome="success" if outcome == _Results.EMITTED else "denied",
            metadata=metadata,
        )


_MCP_DISCOVERY_CTX: ContextVar[McpDiscoveryService | None] = ContextVar(
    "mcp_discovery_service",
    default=None,
)

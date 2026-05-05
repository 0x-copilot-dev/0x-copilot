"""Non-blocking MCP discovery seam (PR 3.3).

Mirrors :class:`agent_runtime.capabilities.citations.CitationLedger` in
shape: per-run service bound to the worker via a :class:`ContextVar`,
exposed through a class-method (``suggest``) so tools never thread a
runtime context object through their signatures.

The service is the *single seam* for surfacing a Connect/Skip card from
the agent. It does three things:

1. Resolves the requested server against the per-run MCP catalog the
   harness already trusts. Disabled or unknown servers short-circuit
   without an event.
2. Audits the suggestion through the same append-only chain PR 1.4
   forwarded events use. SIEM exports can correlate
   ``mcp.discovery.suggested`` rows with the
   ``approval_decision_recorded`` row that resolves them.
3. Emits a single ``mcp_auth_required`` event with the optional
   ``discovery_reason`` + ``expected_value`` payload fields set so the
   FE renders the non-blocking variant (Connect / Skip) instead of the
   blocking auth gate.

Idempotency is keyed on ``(run_id, server_id)``: a second call for the
same pair returns ``already_suggested`` without re-emitting. Already-
authenticated servers short-circuit before the auth-session creator is
hit, so the registry cache stays cold for healthy servers.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Protocol

from agent_runtime.api.constants import Keys, Messages
from agent_runtime.capabilities.mcp.cards import McpAuthState, McpServerCard
from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
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

    def list_available_servers(
        self, context: AgentRuntimeContext
    ) -> tuple[McpServerCard, ...]:
        """Return the per-context catalog already authorized for the run."""


class _Results:
    """Tool-facing result-status strings. Stable for replay parity."""

    EMITTED = "emitted"
    ALREADY_SUGGESTED = "already_suggested"
    ALREADY_AUTHENTICATED = "already_authenticated"
    UNKNOWN_SERVER = "unknown_server"
    SERVER_DISABLED = "server_disabled"
    DISCOVERY_DISABLED = "discovery_disabled"


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

        cached_approval = self._suggested.get(normalized_id)
        if cached_approval is not None:
            return {
                "status": _Results.ALREADY_SUGGESTED,
                "server_id": normalized_id,
                Keys.Field.APPROVAL_ID: cached_approval,
            }

        card = self._lookup_card(normalized_id)
        if card is None:
            # Not in the per-run catalog — never emit a card for a server
            # the model only thinks exists. Audit nothing (no resource
            # was touched). The tool returns the status so the agent can
            # course-correct without retrying.
            return {"status": _Results.UNKNOWN_SERVER, "server_id": normalized_id}

        if not card.enabled:
            await self._audit(
                server_id=normalized_id,
                outcome=_Results.SERVER_DISABLED,
                reason=reason,
            )
            return {"status": _Results.SERVER_DISABLED, "server_id": normalized_id}

        if card.auth_state is McpAuthState.AUTHENTICATED:
            # Already useful — no card needed; the agent should just
            # use the server. Skip audit + event to keep the suggestion
            # surface tight.
            return {
                "status": _Results.ALREADY_AUTHENTICATED,
                "server_id": normalized_id,
            }

        approval_id = self._approval_id(normalized_id)
        payload = self._build_payload(
            card=card,
            approval_id=approval_id,
            reason=reason,
            expected_value=expected_value,
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
        self._suggested[normalized_id] = approval_id
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

    def _lookup_card(self, server_id: str) -> McpServerCard | None:
        for card in self._registry.list_available_servers(self._runtime_context):
            if (card.server_id or card.name) == server_id:
                return card
        return None

    def _build_payload(
        self,
        *,
        card: McpServerCard,
        approval_id: str,
        reason: str,
        expected_value: str,
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
        if self._auth_session_creator is not None:
            session = self._auth_session_creator.create_auth_session(
                server_id=server_id,
                runtime_context=self._runtime_context,
            )
            auth_url = session.auth_url
            expires_at = session.expires_at.isoformat()
            display_name = session.display_name or display_name
        return {
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
        # Reuse the worker emitter's underlying writer. The action string
        # is one of the PR 3.3 audit constants; emit through the same
        # append-only chain PR 1.4 forwarded events use. ``actor_type``
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

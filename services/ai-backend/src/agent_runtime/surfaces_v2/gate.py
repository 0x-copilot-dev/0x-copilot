"""ToolAccessGate — park/resume a run at the connector-dispatch boundary (PRD-C2).

When a run reaches an MCP tool whose connector is not usable *right now* (never
authenticated, auth skipped, credentials expired/failed), the gate raises the
SAME ``langgraph.types.interrupt`` seam :class:`AuthMcpTool` already uses, parks
the run on a deterministic ``mcp_auth:<run_id>:<server_id>`` id, and interprets
the resume value into a typed :class:`GateResume`. The interrupt payload is the
existing ``mcp_auth_required`` payload PLUS an additive ``gate`` block (purpose,
scopes, auth-state, op/op-class) — so the StreamOrchestrator's existing mcp_auth
handling and the legacy in-chat Connect card keep working unchanged, while the v2
canvas gate card + ``gate.opened`` ledger event read the richer block.

Fail-closed by construction (SDR §10 invariant 5, FR-C0):

* an absent classifier ⇒ ``op_class = "write"`` (never silently "read");
* a rejected / skipped resume ⇒ ``approved = False`` ⇒ the caller returns a typed
  auth failure and the dependent connector call NEVER dispatches;
* tool arguments are untrusted — the purpose line caps length and strips
  newlines / markdown / URLs before it ever reaches a surface.

All helpers live inside classes per ``services/ai-backend/CLAUDE.md``. This module
holds domain logic only: it never imports ``runtime_api`` / ``runtime_worker`` and
performs no I/O beyond the injected ``auth_session_creator`` and interrupt handler.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from langgraph.types import interrupt as langgraph_interrupt

from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpAuthState,
    McpServerCard,
)
from agent_runtime.capabilities.mcp.middleware.auth_mcp import McpAuthSessionCreator
from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract
from agent_runtime.surfaces_v2.ledger_models import GateAuthState


class _PayloadKey:
    """Keys on the interrupt payload (mirrors ``AuthMcpTool`` verbatim + ``gate``).

    The base keys MUST match ``AuthMcpTool.ainvoke`` byte-for-byte so the
    StreamOrchestrator's mcp_auth batch path and the legacy Connect card treat a
    gate interrupt identically to a model-issued ``auth_mcp`` one.
    """

    API_EVENT_TYPE = "api_event_type"
    EVENT_TYPE = "event_type"
    APPROVAL_ID = "approval_id"
    ACTION_ID = "action_id"
    APPROVAL_KIND = "approval_kind"
    SERVER_ID = "server_id"
    SERVER_NAME = "server_name"
    DISPLAY_NAME = "display_name"
    AUTH_URL = "auth_url"
    EXPIRES_AT = "expires_at"
    MESSAGE = "message"
    # The additive v2 block. Present ONLY when the flag is on (the caller only
    # builds the gate when ``SurfacesV2Flag.enabled()``), so its mere presence is
    # the flag signal downstream (``stream_events`` emits ``gate.opened`` iff it
    # is set — no second flag read needed).
    GATE = "gate"


class _GateKey:
    """Keys inside the additive ``gate`` block of the interrupt payload."""

    V = "v"
    PURPOSE = "purpose"
    SCOPES = "scopes"
    AUTH_STATE = "auth_state"
    OP = "op"
    OP_CLASS = "op_class"


class _ResumeKey:
    """Keys on the worker resume dict (``{approval_id, decision}``)."""

    DECISION = "decision"


class _Values:
    """Constant wire values the gate payload carries."""

    EVENT_TYPE = "mcp_auth_required"
    APPROVAL_KIND = "mcp_auth"
    OP_CLASS_READ = "read"
    OP_CLASS_WRITE = "write"
    PAYLOAD_V = 1
    # Decisions that count as "connect" (mirrors the APPROVE_WITH_EDITS→APPROVED
    # coercion the approval batch uses — approval.py L238).
    APPROVED_DECISIONS = frozenset({"approved", "approve", "approve_with_edits"})


class _Messages:
    """Safe, bounded user-facing copy for the gate."""

    @staticmethod
    def connect(display_name: str) -> str:
        return f"Authenticate {display_name} to continue using this MCP server."


class GateResume(RuntimeContract):
    """The gate's interpretation of a resume value.

    ``approved`` is derived purely from the resume dict's ``decision`` (approved
    / approve / approve_with_edits ⇒ True; reject / skip / anything else ⇒
    False). ``write_policy`` is reserved for a future inline-resume design — it is
    NOT threaded through the resume today (the decision endpoint persists it
    coordinator-side), so it always stays ``None``.
    """

    approved: bool
    write_policy: Literal["ask_first", "allow_always"] | None = None


class GatePurposeBuilder:
    """Builds the bounded, task-terms purpose line for a gate card.

    ``'to run {op} on {display_name}'`` plus the primary scalar argument when one
    is present. Tool arguments are UNTRUSTED: the value is length-capped and
    stripped of newlines, markdown control chars, and URLs before it can reach a
    surface.
    """

    _MAX_LEN = 80
    _URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
    _MARKDOWN_RE = re.compile(r"[`*_#\[\]()<>|~]")
    _WHITESPACE_RE = re.compile(r"\s+")

    @classmethod
    def build(
        cls,
        *,
        op: str,
        display_name: str,
        arguments: Mapping[str, Any],
    ) -> str:
        base = f"to run {op} on {display_name}"
        primary = cls._primary_scalar(arguments)
        if primary is not None:
            return f"{base}: {primary}"[: cls._MAX_LEN]
        return base[: cls._MAX_LEN]

    @classmethod
    def _primary_scalar(cls, arguments: Mapping[str, Any]) -> str | None:
        """First scalar (str/int/float/bool) argument value, sanitised, or None."""

        if not isinstance(arguments, Mapping):
            return None
        for value in arguments.values():
            if isinstance(value, bool):
                text = "true" if value else "false"
            elif isinstance(value, (int, float)):
                text = str(value)
            elif isinstance(value, str):
                text = value
            else:
                continue
            cleaned = cls._sanitize(text)
            if cleaned:
                return cleaned
        return None

    @classmethod
    def _sanitize(cls, value: str) -> str:
        """Strip URLs / markdown / newlines and cap the length (untrusted arg)."""

        without_urls = cls._URL_RE.sub("", value)
        without_markdown = cls._MARKDOWN_RE.sub("", without_urls)
        collapsed = cls._WHITESPACE_RE.sub(" ", without_markdown).strip()
        return collapsed[: cls._MAX_LEN]


@dataclass(frozen=True)
class ToolAccessGate:
    """Decides, at the connector-dispatch boundary, whether a tool call must park.

    Reuses ``AuthMcpTool``'s auth-session creator and interrupt seam. At most ONE
    ``interrupt`` call per tool invocation: on resume the tool node re-executes
    from the top with a FRESH card — if auth is now valid ``gate_state`` returns
    ``None`` and the interrupt is never reached (the parked call resumes); if auth
    is still unusable, LangGraph's index-matched interrupt returns the stored
    resume value immediately, so ``park`` returns the typed failure without
    re-parking (no gate loop; a cancelled gate can never dispatch).
    """

    auth_session_creator: McpAuthSessionCreator
    runtime_context: AgentRuntimeContext
    interrupt_handler: Callable[[dict[str, Any]], object] = field(
        default=langgraph_interrupt
    )
    classifier: object | None = None

    # -- decision -----------------------------------------------------------

    def gate_state(self, card: McpServerCard) -> GateAuthState | None:
        """Map a card's auth posture to a gate state, or ``None`` (no gate).

        ``None`` ⇒ nothing to connect: ``auth_mode == NONE`` (no auth concept),
        ``auth_state == AUTH_UNSUPPORTED`` (cannot authenticate), or already
        ``AUTHENTICATED``. Otherwise: ``UNAUTHENTICATED`` / ``AUTH_SKIPPED`` ⇒
        ``MISSING``; ``AUTH_FAILED`` / ``AUTH_PENDING`` ⇒ ``EXPIRED``.
        """

        if card.auth_mode is McpAuthMode.NONE:
            return None
        state = card.auth_state
        if state in (McpAuthState.AUTHENTICATED, McpAuthState.AUTH_UNSUPPORTED):
            return None
        if state in (McpAuthState.UNAUTHENTICATED, McpAuthState.AUTH_SKIPPED):
            return GateAuthState.MISSING
        if state in (McpAuthState.AUTH_FAILED, McpAuthState.AUTH_PENDING):
            return GateAuthState.EXPIRED
        # Defensive: any future auth-state we don't recognise fails closed to a
        # gate (never silently dispatch an un-mapped posture).
        return GateAuthState.EXPIRED

    # -- park ---------------------------------------------------------------

    async def park(
        self,
        *,
        card: McpServerCard,
        tool_name: str,
        arguments: Mapping[str, Any],
        state: GateAuthState,
    ) -> GateResume:
        """Create the auth session, raise the gate interrupt, interpret the resume.

        At most one ``interrupt`` call. The returned :class:`GateResume` tells the
        caller whether to dispatch (approved) or fail closed (not approved).
        """

        session = await self.auth_session_creator.create_auth_session(
            server_id=card.server_id or card.name,
            runtime_context=self.runtime_context,
        )
        approval_id = self._approval_id(session.server_id)
        payload = self._interrupt_payload(
            approval_id=approval_id,
            session=session,
            card=card,
            tool_name=tool_name,
            arguments=arguments,
            state=state,
        )
        resume = self.interrupt_handler(payload)
        return self._interpret_resume(resume)

    def _approval_id(self, server_id: str) -> str:
        """Deterministic gate id — identical to ``AuthMcpTool._approval_id`` so
        the ledger, approval record, and client Connect-card join on one key."""

        return f"mcp_auth:{self.runtime_context.run_id}:{server_id}"

    def _interrupt_payload(
        self,
        *,
        approval_id: str,
        session: object,
        card: McpServerCard,
        tool_name: str,
        arguments: Mapping[str, Any],
        state: GateAuthState,
    ) -> dict[str, Any]:
        """Base ``mcp_auth_required`` payload (verbatim shape) + additive gate."""

        display_name = getattr(session, "display_name", None) or card.name
        server_id = getattr(session, "server_id", None) or (card.server_id or card.name)
        server_name = getattr(session, "server_name", None) or card.name
        auth_url = getattr(session, "auth_url", "")
        expires_at = getattr(session, "expires_at", None)
        expires_iso = expires_at.isoformat() if expires_at is not None else ""
        return {
            _PayloadKey.API_EVENT_TYPE: _Values.EVENT_TYPE,
            _PayloadKey.EVENT_TYPE: _Values.EVENT_TYPE,
            _PayloadKey.APPROVAL_ID: approval_id,
            _PayloadKey.ACTION_ID: approval_id,
            _PayloadKey.APPROVAL_KIND: _Values.APPROVAL_KIND,
            _PayloadKey.SERVER_ID: server_id,
            _PayloadKey.SERVER_NAME: server_name,
            _PayloadKey.DISPLAY_NAME: display_name,
            _PayloadKey.AUTH_URL: auth_url,
            _PayloadKey.EXPIRES_AT: expires_iso,
            _PayloadKey.MESSAGE: _Messages.connect(display_name),
            _PayloadKey.GATE: self._gate_block(
                card=card,
                tool_name=tool_name,
                arguments=arguments,
                state=state,
                display_name=display_name,
            ),
        }

    def _gate_block(
        self,
        *,
        card: McpServerCard,
        tool_name: str,
        arguments: Mapping[str, Any],
        state: GateAuthState,
        display_name: str,
    ) -> dict[str, Any]:
        """The additive v2 ``gate`` block: purpose, scopes, auth-state, op class."""

        return {
            _GateKey.V: _Values.PAYLOAD_V,
            _GateKey.PURPOSE: GatePurposeBuilder.build(
                op=tool_name,
                display_name=display_name,
                arguments=arguments,
            ),
            _GateKey.SCOPES: sorted(card.required_scopes),
            _GateKey.AUTH_STATE: state.value,
            _GateKey.OP: tool_name,
            _GateKey.OP_CLASS: self._op_class(card=card, tool_name=tool_name),
        }

    def _op_class(self, *, card: McpServerCard, tool_name: str) -> str:
        """Classify the op as ``read`` / ``write``; fail closed to ``write``.

        Uses PRD-C1's ``ActionClassifier`` when one is wired, consulting the
        per-run annotations registry. An absent classifier — or any surprise —
        yields ``write`` (FR-C0): the gate never silently treats an unknown op as
        read.
        """

        classifier = self.classifier
        if classifier is None:
            return _Values.OP_CLASS_WRITE
        try:
            from agent_runtime.capabilities.mcp.annotations import (
                McpToolAnnotationsRegistry,
            )

            annotations = McpToolAnnotationsRegistry.get(card.name, tool_name)
            classified = classifier.classify(
                server=card.name, tool=tool_name, annotations=annotations
            )
            value = classified.action_class.value
            return value if value == _Values.OP_CLASS_READ else _Values.OP_CLASS_WRITE
        except Exception:  # noqa: BLE001 - fail closed to write, never raise into dispatch
            return _Values.OP_CLASS_WRITE

    @staticmethod
    def _interpret_resume(resume: object) -> GateResume:
        """Coerce a resume value to a :class:`GateResume` (approved / not)."""

        decision: object = None
        if isinstance(resume, Mapping):
            decision = resume.get(_ResumeKey.DECISION)
        approved = (
            isinstance(decision, str) and decision.lower() in _Values.APPROVED_DECISIONS
        ) or resume is True
        return GateResume(approved=approved)


class GateLedger:
    """Builds the ``gate.opened`` / ``gate.resolved`` ledger payloads (SDR §5).

    The two emission sites live in different processes — ``gate.opened`` beside
    the mcp_auth interrupt in the worker's ``StreamOrchestrator``, ``gate.resolved``
    in the API's ``ApprovalCoordinator`` — so the payload shapes are centralised
    here rather than duplicated. Both are strict SDR-verbatim dicts; the
    transport projector re-filters them again on append.
    """

    GATE_KEY = _PayloadKey.GATE

    @classmethod
    def gate_block(cls, payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """Return the additive ``gate`` block of an interrupt payload, or ``None``.

        Its presence is the flag signal — the block is added by :meth:`park` only
        when ``SurfacesV2Flag`` is on, so a flag-off mcp_auth interrupt has no
        block and no ``gate.opened`` is emitted (byte-identical stream).
        """

        block = payload.get(cls.GATE_KEY)
        return block if isinstance(block, Mapping) else None

    @classmethod
    def opened_payload(
        cls, *, interrupt_payload: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """Build a ``gate.opened`` payload from an mcp_auth interrupt payload.

        Returns ``None`` when the interrupt carries no v2 gate block (flag off) —
        the caller then emits nothing.
        """

        block = cls.gate_block(interrupt_payload)
        if block is None:
            return None
        gate_id = interrupt_payload.get(_PayloadKey.APPROVAL_ID)
        connector = interrupt_payload.get(_PayloadKey.SERVER_NAME)
        scopes = block.get(_GateKey.SCOPES)
        return {
            _GateKey.V: _Values.PAYLOAD_V,
            "gate_id": gate_id if isinstance(gate_id, str) else "",
            "connector": connector if isinstance(connector, str) else "",
            _GateKey.PURPOSE: str(block.get(_GateKey.PURPOSE, "")),
            _GateKey.SCOPES: list(scopes) if isinstance(scopes, (list, tuple)) else [],
            _GateKey.AUTH_STATE: str(
                block.get(_GateKey.AUTH_STATE, GateAuthState.MISSING.value)
            ),
        }

    @classmethod
    def resolved_payload(
        cls,
        *,
        gate_id: str,
        connected: bool,
        write_policy: str | None,
    ) -> dict[str, Any]:
        """Build a ``gate.resolved`` payload (outcome + optional write policy)."""

        payload: dict[str, Any] = {
            _GateKey.V: _Values.PAYLOAD_V,
            "gate_id": gate_id,
            "outcome": "connected" if connected else "cancelled",
        }
        if write_policy is not None:
            payload["write_policy"] = write_policy
        return payload


__all__ = ["GateLedger", "GatePurposeBuilder", "GateResume", "ToolAccessGate"]

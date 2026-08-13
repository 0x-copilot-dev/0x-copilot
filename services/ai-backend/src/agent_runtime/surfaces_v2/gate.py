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

The gate has a second, non-OAuth mode: :meth:`ToolAccessGate.park_for_approval`
parks a *write* the policy decision point returned GATE for. Both modes emit the
same ``gate.opened`` / ``gate.resolved`` ledger pair — see :class:`GateLedger`
for why that pair, and not a distinct write-approval event, is the right home.

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
    STATUS = "status"
    # The additive v2 block. Present ONLY when the flag is on (the caller only
    # builds the gate when ``SurfacesV2Flag.enabled()``), so its mere presence is
    # the flag signal downstream (``stream_events`` emits ``gate.opened`` iff it
    # is set — no second flag read needed).
    GATE = "gate"
    #: Top-level (NOT inside the ``gate`` block) because the filesystem lane
    #: already spells it there — ``runtime_worker/stream_events.py``'s
    #: ``_Fields.GRANT_OPTIONS``. Putting it inside ``gate`` would need its own
    #: projection branch for the same idea.
    #:
    #: A write gate rides the ``ask_a_question`` wire shape, so it is projected by
    #: ``RuntimeEventPresentationProjector._ask_a_question_requested_payload``,
    #: NOT by the sibling ``_approval_requested_payload`` branch that already
    #: handled this key. That is the same split which previously left ``op_class``
    #: and ``risk_level`` stripped on this lane; both projections now name it.
    GRANT_OPTIONS = "grant_options"


class _GateKey:
    """Keys inside the additive ``gate`` block of the interrupt payload."""

    V = "v"
    PURPOSE = "purpose"
    SCOPES = "scopes"
    AUTH_STATE = "auth_state"
    OP = "op"
    OP_CLASS = "op_class"
    DISPLAY_TITLE = "display_title"


class _ResumeKey:
    """Keys on the worker resume dict (``{approval_id, decision}``)."""

    DECISION = "decision"
    #: How far the approval reaches — ``once`` (this call) or ``always`` (write
    #: a rule that answers the rest of the run). Threaded from the decision
    #: endpoint through ``RuntimeApprovalResolvedCommand`` and
    #: ``RuntimeApprovalHandler._resume_payload``'s ``ask_a_question`` branch,
    #: which is the branch the write gate borrows.
    DECISION_SCOPE = "decision_scope"


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
    # The write-approval GATE (distinct from the OAuth-connect gate). The
    # projection at ``runtime_worker/stream_events.py`` recognises an interrupt
    # as a resolvable approval batch by its ``api_event_type`` +
    # ``approval_kind``; reusing the ``approval_requested`` / ``ask_a_question``
    # shape (the pair proven end-to-end by ``test_multi_interrupt_run_resume_e2e``)
    # routes the write gate through the SAME ApprovalCoordinator resume plumbing
    # without a new projection branch. The resume value is the flat
    # ``{approval_id, decision, answer}`` dict ``_interpret_resume`` already reads.
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_KIND_WRITE = "ask_a_question"
    STATUS_PENDING = "pending"
    #: ``grant_options`` on the write-gate card. ``allow_once`` is what a plain
    #: approve has always done; ``allow_always`` is the second scope, and it is
    #: offered ONLY for a non-destructive op — see :meth:`
    #: ToolAccessGate._grant_options`.
    ALLOW_ONCE = "allow_once"
    ALLOW_ALWAYS = "allow_always"
    OP_CLASS_DESTRUCTIVE = "destructive"


class _Messages:
    """Safe, bounded user-facing copy for the gate."""

    @staticmethod
    def connect(display_name: str) -> str:
        return f"Authenticate {display_name} to continue using this MCP server."

    @staticmethod
    def approve(*, display_name: str, op: str) -> str:
        return f"Allow {display_name} to run {op}?"

    @staticmethod
    def ledger_purpose(*, op_class: str, op: str, connector: str) -> str:
        """The ``gate.opened`` purpose line for a WRITE-approval gate.

        Built from the op class, the tool name, and the connector slug ONLY —
        never from the call's arguments. The interactive card's purpose
        (:class:`GatePurposeBuilder`) deliberately includes a sanitised primary
        argument because a human approving an effect needs to see what it acts
        on; the ledger row is a durable compliance record and must not carry
        tool arguments, model text, or anything derived from them.

        Callers pass identifier-sanitised tokens
        (:meth:`GatePurposeBuilder.sanitize_identifier`), which also bounds the
        line: three capped tokens plus fixed copy.
        """

        return f"approve {op_class} {op} on {connector}"

    @staticmethod
    def ledger_display_title(*, op: str, connector: str) -> str:
        """The ``gate.opened`` line a PERSON reads, for the cross-run queue.

        The queue had no human string to render, so it fell back to
        :meth:`ledger_purpose` — the auditor's line — and every queued write read
        ``approve destructive save_issue on linear``. That is a correct
        compliance row and a poor thing to ask somebody to act on.

        This is built from the SAME two identifier-sanitised tokens, and
        deliberately carries no argument: ``gate.opened`` is durable, so the
        interactive card's argument-bearing purpose
        (:class:`GatePurposeBuilder`) must not leak into it just because the
        result would read better. What changes is grammar, not information —
        ``save_issue`` on ``linear`` becomes "Save issue · Linear".

        Both tokens arrive capped at 64 chars, so the line is bounded by
        construction.
        """

        verb = op.replace("_", " ").replace("-", " ").strip()
        verb = verb[:1].upper() + verb[1:] if verb else "Run tool"
        surface = connector.replace("_", " ").replace("-", " ").strip()
        surface = surface[:1].upper() + surface[1:] if surface else ""
        return f"{verb} · {surface}" if surface else verb


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
    #: The reply's SCOPE, as the client sent it — ``once`` / ``always``, or
    #: ``None`` when the resume named none. Left as an opaque string here on
    #: purpose: the gate's job is to report what came back, and the meaning of
    #: ``always`` (which rule it writes, over which subjects) belongs to the
    #: policy lane that raised the GATE, not to the transport that carried it.
    decision_scope: str | None = None


class GatePurposeBuilder:
    """Builds the bounded, task-terms purpose line for a gate card.

    ``'to run {op} on {display_name}'`` plus the primary scalar argument when one
    is present. Tool arguments are UNTRUSTED: the value is length-capped and
    stripped of newlines, markdown control chars, and URLs before it can reach a
    surface.
    """

    _MAX_LEN = 80
    _MAX_IDENTIFIER_LEN = 64
    _URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
    _MARKDOWN_RE = re.compile(r"[`*_#\[\]()<>|~]")
    _WHITESPACE_RE = re.compile(r"\s+")
    _IDENTIFIER_RE = re.compile(r"[^A-Za-z0-9_.:-]")

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

    @classmethod
    def sanitize_identifier(cls, value: str) -> str:
        """Reduce an untrusted *name* to an identifier: URLs out, whitelist in.

        Public because :class:`GateLedger` builds the write gate's ledger purpose
        line from a tool name and a connector slug, both of which arrive on an
        MCP descriptor and are untrusted for the same reason arguments are.

        A whitelist rather than :meth:`_sanitize`'s blacklist, because the two
        inputs are different in kind. In free-form argument text ``_`` is
        markdown emphasis and is stripped; in a tool name it is structure, and
        stripping it turns ``create_issue`` into ``createissue`` — losing the
        exact token the ledger row exists to record. Length is capped so an
        adversarial descriptor cannot grow the purpose line without bound.
        """

        without_urls = cls._URL_RE.sub("", value)
        return cls._IDENTIFIER_RE.sub("", without_urls)[: cls._MAX_IDENTIFIER_LEN]


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

    ``auth_session_creator`` is Optional. Only the OAuth-connect gate (:meth:`park`)
    opens a session; the write-approval gate (:meth:`park_for_approval`) opens
    none — it asks "may this effect run", not "connect this server". A non-OAuth
    MCP server (stdio / local, ``auth_mode == NONE``) has no OAuth provider, so
    the factory builds the gate with ``auth_session_creator=None`` and the write
    gate still parks. :meth:`park` then fails closed (never dispatches) if it is
    ever reached without a creator — though a ``NONE``-auth card yields
    ``gate_state == None``, so :meth:`park` is not on its path to begin with.
    """

    auth_session_creator: McpAuthSessionCreator | None
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

        if self.auth_session_creator is None:
            # No OAuth provider is wired for this run, so the connect flow cannot
            # start: fail closed (the caller returns a typed auth failure and
            # never dispatches). No interrupt is raised — there is nothing to
            # connect. Not reached for a ``auth_mode == NONE`` card, whose
            # ``gate_state`` is ``None``; this guards only a defensive re-entry
            # (e.g. a connector that raises ``McpAuthError`` mid-dispatch).
            return GateResume(approved=False)
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

    # -- park_for_approval (write gate, NOT the OAuth-connect gate) ----------

    async def park_for_approval(
        self,
        *,
        card: McpServerCard,
        tool_name: str,
        arguments: Mapping[str, Any],
        op_class: str,
        approval_id: str,
    ) -> GateResume:
        """Park the run on a WRITE-approval interrupt; resume on the decision.

        This is the effect-approval sibling of :meth:`park`. It raises the SAME
        ``langgraph.types.interrupt`` seam but with the ``approval_requested`` /
        ``ask_a_question`` payload shape (so the existing ApprovalCoordinator
        projection + resume path drive it end-to-end), and it deliberately does
        NOT create an OAuth auth session — a write approval is "may this effect
        run", not "connect this server". At most one ``interrupt`` per
        invocation; on resume the tool node re-executes from the top and the
        stored decision is returned in place, so an approved write executes in
        the SAME run and a rejected one fails closed without dispatching.
        """

        payload = self._approval_interrupt_payload(
            approval_id=approval_id,
            card=card,
            tool_name=tool_name,
            arguments=arguments,
            op_class=op_class,
        )
        resume = self.interrupt_handler(payload)
        return self._interpret_resume(resume)

    def _approval_interrupt_payload(
        self,
        *,
        approval_id: str,
        card: McpServerCard,
        tool_name: str,
        arguments: Mapping[str, Any],
        op_class: str,
    ) -> dict[str, Any]:
        """Build the ``approval_requested`` payload + additive gate block."""

        display_name = card.display_name or card.name
        message = _Messages.approve(display_name=display_name, op=tool_name)
        return {
            _PayloadKey.API_EVENT_TYPE: _Values.APPROVAL_REQUESTED,
            _PayloadKey.EVENT_TYPE: _Values.APPROVAL_REQUESTED,
            _PayloadKey.APPROVAL_ID: approval_id,
            _PayloadKey.ACTION_ID: approval_id,
            _PayloadKey.APPROVAL_KIND: _Values.APPROVAL_KIND_WRITE,
            _PayloadKey.SERVER_NAME: card.name,
            _PayloadKey.DISPLAY_NAME: display_name,
            _PayloadKey.MESSAGE: message,
            "question": message,
            _PayloadKey.STATUS: _Values.STATUS_PENDING,
            _PayloadKey.GRANT_OPTIONS: self._grant_options(op_class),
            _PayloadKey.GATE: {
                _GateKey.V: _Values.PAYLOAD_V,
                _GateKey.PURPOSE: GatePurposeBuilder.build(
                    op=tool_name,
                    display_name=display_name,
                    arguments=arguments,
                ),
                _GateKey.SCOPES: sorted(card.required_scopes),
                _GateKey.OP: tool_name,
                _GateKey.OP_CLASS: op_class,
            },
        }

    @staticmethod
    def _grant_options(op_class: str) -> list[str]:
        """Which scopes this card may be answered with.

        ``allow_once`` is unconditional — it is what a plain approve has always
        meant. ``allow_always`` is offered for every op EXCEPT a destructive one.

        The split is the same one the filesystem lane already reasons about at
        ``runtime_worker/stream_events.py:227-234``, and it is worth being exact
        about because the two lanes give the SAME wire word two different
        meanings:

        * **Filesystem lane** — ``allow_always`` ATTACHES A FOLDER: a durable
          workspace grant, wider than the one path the card named. That lane
          therefore withholds it for a write, because "a grant appearing from a
          card that never mentioned one" is an escalation.
        * **This lane** — ``allow_always`` writes a RUN-SCOPED rule over exactly
          the subjects this call already carried (its URN and its own string
          arguments; see :class:`PolicySubjects`). It widens nothing beyond what
          the card named and it expires with the run. That is precisely the
          "what the user actually wants after the third card is *stop pausing for
          this run*" the filesystem note itself lands on — so the two lanes are
          not in tension, they are answering two different questions and each
          offers the option that is safe for its own.

        Destructive is the one exclusion here, for the same reason
        :meth:`PdpPolicyService._posture_decision` puts the destructive rung above
        BYPASS: the value of pausing on an irreversible act is that it is decided
        *each time it is about to happen*. An advance yes to a class of deletes is
        the thing that rung exists to prevent, so the card does not offer one.
        """

        if op_class.strip().lower() == _Values.OP_CLASS_DESTRUCTIVE:
            return [_Values.ALLOW_ONCE]
        return [_Values.ALLOW_ONCE, _Values.ALLOW_ALWAYS]

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
        scope: object = None
        if isinstance(resume, Mapping):
            decision = resume.get(_ResumeKey.DECISION)
            scope = resume.get(_ResumeKey.DECISION_SCOPE)
        approved = (
            isinstance(decision, str) and decision.lower() in _Values.APPROVED_DECISIONS
        ) or resume is True
        # A scope only ever travels with an APPROVAL. Carrying one off a
        # rejection would let a decline write an allow rule, which is the exact
        # inversion this field must not be able to express.
        return GateResume(
            approved=approved,
            decision_scope=(
                scope.strip().lower()[:32]
                if approved and isinstance(scope, str) and scope.strip()
                else None
            ),
        )


class GateLedger:
    """Builds the ``gate.opened`` / ``gate.resolved`` ledger payloads (SDR §5).

    The emission sites live in different processes — ``gate.opened`` beside the
    interrupt in the worker's ``StreamOrchestrator``, ``gate.resolved`` in the
    API's ``ApprovalCoordinator`` — so the payload shapes are centralised here
    rather than duplicated. Both are strict SDR-verbatim dicts; the transport
    projector re-filters them again on append.

    Two gate KINDS ride these two event types
    -----------------------------------------
    * the **OAuth-connect** gate (:meth:`ToolAccessGate.park`), which rides an
      ``mcp_auth_required`` interrupt; and
    * the **write-approval** gate (:meth:`ToolAccessGate.park_for_approval`),
      which rides an ``approval_requested`` / ``ask_a_question`` interrupt.

    Only the first emitted a ledger pair when P1b shipped, so a write that
    parked, was approved by a human, and then executed left NO gate trail at
    all: "who approved this write, when, and what happened" was unanswerable
    from the events. This class closes that.

    Why reuse ``gate.opened`` / ``gate.resolved`` rather than mint a distinct
    write-approval event
    --------------------------------------------------------------------------
    1. **They are the run's one gate vocabulary, and every consumer keys on it.**
       ``PendingWorkProjector`` calls a gate pending iff a ``gate.opened`` has no
       later ``gate.resolved``; ``receipt_export_v2._fold_gates`` builds the
       receipt's gate rows from the pair; ``receipt_v2`` warns on a resolve
       without an open; the client canvas folds the pair into the gate card. A
       new event type would have left the write gate invisible to all four —
       i.e. it would have re-created the very hole being closed, one indirection
       further out.
    2. **The vocabulary is a pinned cross-language SSOT.** ``LedgerEventType``,
       ``LEDGER_EVENT_TYPES`` and the JSON contract in ``service-contracts`` are
       asserted equal, in order, at exactly 34 entries, with per-event
       required/property parity against the Pydantic models and golden replay
       fixtures (``test_ledger_contract_parity``). A 35th event type is a
       contract change spanning that shared package, the TS transport tuple, the
       wire enum, the projector allow-list and the goldens — not a runtime fix.
    3. **The fields fit once read at gate altitude**, which is the level the SDR
       defines them at. Two spellings are OAuth-flavoured and are given their
       precise reading here rather than left to the reader:

       * ``auth_state`` — the connect gate emits ``missing`` / ``expired``. The
         write gate emits the third, until-now-unused member, ``insufficient``:
         *the standing authorization is not sufficient for THIS operation*. That
         is exactly true of an authenticated (or ``auth_mode=NONE``) connector
         whose policy requires a human grant per write, and it is the only
         member that does not assert a broken credential. It doubles as the
         machine-readable discriminator between the two gate kinds, so no new
         field is needed to tell them apart.
       * ``outcome`` — ``connected`` means the gate was passed (the grant was
         given), ``cancelled`` that it was withheld.

    Answering the audit question
    ----------------------------
    *which connector* → ``connector``; *which operation* → ``purpose``, built
    from the op class + tool name + connector slug and NOTHING else; *what the
    decision was* → ``outcome``; *who made it* → the ``gate_id``, which is the
    ``approval_id``, which is the ``resource_id`` of the ``approval.accept`` /
    ``approval.reject`` audit row the same coordinator writes, carrying the
    org + user. Identity is deliberately not in the payload: ledger payloads
    carry no org/user fields by rule — attribution rides the run envelope — and
    ``test_no_org_or_user_fields_on_any_payload`` enforces it across all 34
    payload models.
    """

    GATE_KEY = _PayloadKey.GATE

    #: Wire ``api_event_type`` of the interrupt the write gate rides (the connect
    #: gate rides ``_Values.EVENT_TYPE``). A bare string, so this domain module
    #: never imports the transport enum to classify.
    WRITE_INTERRUPT = _Values.APPROVAL_REQUESTED

    @classmethod
    def gate_block(cls, payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """Return the additive ``gate`` block of an interrupt payload, or ``None``.

        Its presence is the flag signal — the block is added by :meth:`park` only
        when ``SurfacesV2Flag`` is on, so a flag-off mcp_auth interrupt has no
        block and no ``gate.opened`` is emitted (byte-identical stream).

        It is also what separates a gate from a look-alike: the model's own
        ``ask_a_question`` tool raises an interrupt with the same
        ``approval_requested`` / ``ask_a_question`` shape the write gate reuses,
        and carries no block — so it can never be mistaken for a gate here.
        """

        block = payload.get(cls.GATE_KEY)
        return block if isinstance(block, Mapping) else None

    @classmethod
    def is_write_gate(cls, payload: Mapping[str, Any]) -> bool:
        """True iff this payload is a WRITE-approval gate (not an OAuth connect).

        Accepts either the raw interrupt payload (worker side) or the approval
        record's ``metadata``, which the worker persists as a verbatim copy of
        that payload — so both emission sites classify off one predicate.
        """

        if cls.gate_block(payload) is None:
            return False
        return payload.get(_PayloadKey.API_EVENT_TYPE) == cls.WRITE_INTERRUPT

    @classmethod
    def opened_payload(
        cls, *, interrupt_payload: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        """Build a ``gate.opened`` payload from a gate interrupt payload.

        Returns ``None`` when the interrupt carries no v2 gate block (flag off,
        or an interrupt that is not a gate at all) — the caller emits nothing.
        """

        block = cls.gate_block(interrupt_payload)
        if block is None:
            return None
        gate_id = interrupt_payload.get(_PayloadKey.APPROVAL_ID)
        connector = interrupt_payload.get(_PayloadKey.SERVER_NAME)
        scopes = block.get(_GateKey.SCOPES)
        safe_connector = connector if isinstance(connector, str) else ""
        payload = {
            _GateKey.V: _Values.PAYLOAD_V,
            "gate_id": gate_id if isinstance(gate_id, str) else "",
            "connector": safe_connector,
            _GateKey.PURPOSE: cls._purpose(
                interrupt_payload, block, connector=safe_connector
            ),
            _GateKey.SCOPES: list(scopes) if isinstance(scopes, (list, tuple)) else [],
            _GateKey.AUTH_STATE: cls._auth_state(interrupt_payload, block),
        }
        display_title = cls._display_title(
            interrupt_payload, block, connector=safe_connector
        )
        if display_title is not None:
            payload[_GateKey.DISPLAY_TITLE] = display_title
        return payload

    @classmethod
    def _display_title(
        cls,
        interrupt_payload: Mapping[str, Any],
        block: Mapping[str, Any],
        *,
        connector: str,
    ) -> str | None:
        """The human line for a WRITE gate, or ``None`` for a connect gate.

        A connect gate's ``purpose`` is already written for a person
        ("Authenticate Linear to continue…"), so it needs no second string and
        omitting the key keeps the payload as small as it was. Only the write
        gate — whose purpose is the auditor's line — gets one.
        """

        if not cls.is_write_gate(interrupt_payload):
            return None
        return _Messages.ledger_display_title(
            op=GatePurposeBuilder.sanitize_identifier(str(block.get(_GateKey.OP, ""))),
            connector=GatePurposeBuilder.sanitize_identifier(connector),
        )

    @classmethod
    def _purpose(
        cls,
        interrupt_payload: Mapping[str, Any],
        block: Mapping[str, Any],
        *,
        connector: str,
    ) -> str:
        """The gate's ledger purpose line.

        Connect gate: the card's purpose verbatim (unchanged). Write gate: a
        rebuilt, argument-free line — the card's purpose may embed a sanitised
        primary argument for the human approving it, which must never become a
        durable ledger row. ``op`` and the connector slug come off an MCP
        descriptor and are therefore untrusted; every token goes through
        :meth:`GatePurposeBuilder.sanitize_identifier`.
        """

        if not cls.is_write_gate(interrupt_payload):
            return str(block.get(_GateKey.PURPOSE, ""))
        return _Messages.ledger_purpose(
            op_class=GatePurposeBuilder.sanitize_identifier(
                str(block.get(_GateKey.OP_CLASS, _Values.OP_CLASS_WRITE))
            ),
            op=GatePurposeBuilder.sanitize_identifier(str(block.get(_GateKey.OP, ""))),
            connector=GatePurposeBuilder.sanitize_identifier(connector),
        )

    @classmethod
    def _auth_state(
        cls, interrupt_payload: Mapping[str, Any], block: Mapping[str, Any]
    ) -> str:
        """``missing`` / ``expired`` for a connect gate, ``insufficient`` for a write.

        A write gate's block carries no ``auth_state`` — there is no credential
        problem to report — so defaulting it to ``missing`` (as the connect
        branch does when a block omits it) would put a falsehood in a compliance
        record. See the class docstring for the reading of ``insufficient``.
        """

        if cls.is_write_gate(interrupt_payload):
            return GateAuthState.INSUFFICIENT.value
        return str(block.get(_GateKey.AUTH_STATE, GateAuthState.MISSING.value))

    @classmethod
    def resolved_payload(
        cls,
        *,
        gate_id: str,
        connected: bool,
        write_policy: str | None,
    ) -> dict[str, Any]:
        """Build a ``gate.resolved`` payload (outcome + optional write policy).

        Shared by both gate kinds: ``connected`` is "the gate was passed" — a
        connector authenticated, or a write granted — and ``cancelled`` is the
        withheld case. ``write_policy`` rides only the connect gate; the decision
        endpoint rejects it (422) on any other approval kind.
        """

        payload: dict[str, Any] = {
            _GateKey.V: _Values.PAYLOAD_V,
            "gate_id": gate_id,
            "outcome": "connected" if connected else "cancelled",
        }
        if write_policy is not None:
            payload["write_policy"] = write_policy
        return payload


__all__ = ["GateLedger", "GatePurposeBuilder", "GateResume", "ToolAccessGate"]

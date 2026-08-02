"""Unit tests for the PRD-C2 :class:`ToolAccessGate` core (park/resume decision).

Pure-domain tests: a fake auth-session creator, a captured interrupt handler,
and a fake classifier — no network, no LangGraph, no live model. They pin the
gate-state mapping, the single-interrupt-per-invocation contract, the v2 gate
payload shape, the resume decision coercion, the untrusted-purpose sanitisation,
and the fail-closed ``op_class`` default.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_runtime.capabilities.mcp.cards import (
    McpAuthMode,
    McpAuthState,
    McpServerHealth,
    McpServerCard,
    McpTransport,
)
from agent_runtime.capabilities.mcp.middleware.auth_mcp import McpAuthSession
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.surfaces_v2.gate import (
    GateLedger,
    GatePurposeBuilder,
    GateResume,
    ToolAccessGate,
)
from agent_runtime.surfaces_v2.ledger_models import GateAuthState


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _FakeAuthSessionCreator:
    """Returns a deterministic auth session; records the server it was asked for."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def create_auth_session(
        self, *, server_id: str, runtime_context: AgentRuntimeContext
    ) -> McpAuthSession:
        self.calls.append(server_id)
        return McpAuthSession(
            server_id=server_id,
            server_name="linear",
            display_name="Linear",
            auth_url="https://vendor.example/oauth?x=1",
            expires_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


class _CapturingInterrupt:
    """Captures the interrupt payload and returns a canned resume value."""

    def __init__(self, resume: object) -> None:
        self.resume = resume
        self.payloads: list[dict] = []

    def __call__(self, payload: dict) -> object:
        self.payloads.append(payload)
        return self.resume


class _ReadClassifier:
    class _Classified:
        class action_class:  # noqa: N801 - mimics the enum's ``.value``
            value = "read"

    def classify(self, *, server, tool, annotations):  # noqa: ANN001
        return self._Classified()


class _WriteClassifier:
    class _Classified:
        class action_class:  # noqa: N801
            value = "write"

    def classify(self, *, server, tool, annotations):  # noqa: ANN001
        return self._Classified()


def _context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user_123",
        org_id="org_456",
        roles={"employee"},
        model_profile=ModelConfig(
            provider="openai",
            model_name="gpt-4o-mini",
            max_input_tokens=4096,
            timeout_seconds=30,
            temperature=0.0,
        ),
        run_id="run_abcdef",
        trace_id="trace_gate",
    )


def _card(
    *,
    auth_mode: McpAuthMode = McpAuthMode.OAUTH2,
    auth_state: McpAuthState = McpAuthState.UNAUTHENTICATED,
    scopes: tuple[str, ...] = ("docs:read", "docs:write"),
) -> McpServerCard:
    return McpServerCard(
        name="linear",
        server_id="seed:linear",
        short_description="Linear MCP.",
        transport=McpTransport.HTTP,
        auth_mode=auth_mode,
        auth_state=auth_state,
        required_scopes=scopes,
        health=McpServerHealth.HEALTHY,
        load_cost=10,
    )


def _gate(*, interrupt: object = None, classifier: object = None) -> ToolAccessGate:
    return ToolAccessGate(
        auth_session_creator=_FakeAuthSessionCreator(),
        runtime_context=_context(),
        interrupt_handler=interrupt or _CapturingInterrupt({"decision": "approved"}),
        classifier=classifier,
    )


# --------------------------------------------------------------------------- #
# gate_state mapping
# --------------------------------------------------------------------------- #


def test_authenticated_card_never_gates() -> None:
    """DoD regression: an authenticated connector returns no gate state."""

    gate = _gate()
    assert gate.gate_state(_card(auth_state=McpAuthState.AUTHENTICATED)) is None


def test_auth_mode_none_and_unsupported_never_gate() -> None:
    gate = _gate()
    assert gate.gate_state(_card(auth_mode=McpAuthMode.NONE)) is None
    assert gate.gate_state(_card(auth_state=McpAuthState.AUTH_UNSUPPORTED)) is None


def test_unauthenticated_maps_missing_expired_maps_expired() -> None:
    gate = _gate()
    assert (
        gate.gate_state(_card(auth_state=McpAuthState.UNAUTHENTICATED))
        is GateAuthState.MISSING
    )
    assert (
        gate.gate_state(_card(auth_state=McpAuthState.AUTH_SKIPPED))
        is GateAuthState.MISSING
    )
    assert (
        gate.gate_state(_card(auth_state=McpAuthState.AUTH_FAILED))
        is GateAuthState.EXPIRED
    )
    assert (
        gate.gate_state(_card(auth_state=McpAuthState.AUTH_PENDING))
        is GateAuthState.EXPIRED
    )


# --------------------------------------------------------------------------- #
# park / interrupt payload
# --------------------------------------------------------------------------- #


async def test_park_raises_single_interrupt_with_v2_gate_payload() -> None:
    interrupt = _CapturingInterrupt({"decision": "approved"})
    gate = _gate(interrupt=interrupt, classifier=_WriteClassifier())
    resume = await gate.park(
        card=_card(),
        tool_name="create_issue",
        arguments={"title": "Fix login"},
        state=GateAuthState.MISSING,
    )
    assert resume.approved is True
    # Exactly one interrupt call per invocation.
    assert len(interrupt.payloads) == 1
    payload = interrupt.payloads[0]
    # Base mcp_auth shape (verbatim keys) is preserved.
    assert payload["approval_kind"] == "mcp_auth"
    assert payload["event_type"] == "mcp_auth_required"
    assert payload["approval_id"] == "mcp_auth:run_abcdef:seed:linear"
    assert payload["action_id"] == payload["approval_id"]
    assert payload["server_name"] == "linear"
    # Additive v2 gate block.
    gate_block = payload["gate"]
    assert gate_block["v"] == 1
    assert gate_block["auth_state"] == "missing"
    assert gate_block["op"] == "create_issue"
    assert gate_block["op_class"] == "write"
    assert gate_block["scopes"] == ["docs:read", "docs:write"]
    assert "create_issue" in gate_block["purpose"]


async def test_resume_rejected_returns_not_approved() -> None:
    gate = _gate(interrupt=_CapturingInterrupt({"decision": "rejected"}))
    resume = await gate.park(
        card=_card(),
        tool_name="create_issue",
        arguments={},
        state=GateAuthState.MISSING,
    )
    assert resume.approved is False
    assert resume.write_policy is None


@pytest.mark.parametrize("decision", ["approved", "approve", "approve_with_edits"])
async def test_resume_approved_or_approve_with_edits_maps_approved(
    decision: str,
) -> None:
    gate = _gate(interrupt=_CapturingInterrupt({"decision": decision}))
    resume = await gate.park(
        card=_card(),
        tool_name="create_issue",
        arguments={},
        state=GateAuthState.MISSING,
    )
    assert resume.approved is True
    # write_policy is NEVER threaded through the resume (decoupled path).
    assert resume.write_policy is None


# --------------------------------------------------------------------------- #
# purpose builder — untrusted args
# --------------------------------------------------------------------------- #


def test_purpose_builder_caps_length_and_strips_markdown_urls() -> None:
    purpose = GatePurposeBuilder.build(
        op="create_issue",
        display_name="Linear",
        arguments={
            "title": "**pwn** see https://evil.example/x now\n\nlong " + "a" * 200
        },
    )
    assert "http" not in purpose
    assert "**" not in purpose
    assert "\n" not in purpose
    assert len(purpose) <= 80


def test_purpose_builder_no_args_is_base_line() -> None:
    purpose = GatePurposeBuilder.build(
        op="list_issues", display_name="Linear", arguments={}
    )
    assert purpose == "to run list_issues on Linear"


# --------------------------------------------------------------------------- #
# op_class fail-closed (FR-C0)
# --------------------------------------------------------------------------- #


async def test_op_class_defaults_write_when_classifier_absent() -> None:
    interrupt = _CapturingInterrupt({"decision": "approved"})
    gate = _gate(interrupt=interrupt, classifier=None)
    await gate.park(
        card=_card(),
        tool_name="search_issues",
        arguments={},
        state=GateAuthState.MISSING,
    )
    assert interrupt.payloads[0]["gate"]["op_class"] == "write"


async def test_op_class_read_when_classifier_says_read() -> None:
    interrupt = _CapturingInterrupt({"decision": "approved"})
    gate = _gate(interrupt=interrupt, classifier=_ReadClassifier())
    await gate.park(
        card=_card(),
        tool_name="search_issues",
        arguments={},
        state=GateAuthState.MISSING,
    )
    assert interrupt.payloads[0]["gate"]["op_class"] == "read"


# --------------------------------------------------------------------------- #
# GateLedger payload builders
# --------------------------------------------------------------------------- #


def test_gate_ledger_opened_payload_from_interrupt() -> None:
    # A known interrupt payload shape (as ``park`` would build it).
    payload = {
        "approval_id": "mcp_auth:run_abcdef:seed:linear",
        "server_name": "linear",
        "gate": {
            "v": 1,
            "purpose": "to run x on Linear",
            "scopes": ["a", "b"],
            "auth_state": "missing",
            "op": "x",
            "op_class": "read",
        },
    }
    opened = GateLedger.opened_payload(interrupt_payload=payload)
    assert opened == {
        "v": 1,
        "gate_id": "mcp_auth:run_abcdef:seed:linear",
        "connector": "linear",
        "purpose": "to run x on Linear",
        "scopes": ["a", "b"],
        "auth_state": "missing",
    }


def test_gate_ledger_opened_payload_none_without_block() -> None:
    assert GateLedger.opened_payload(interrupt_payload={"approval_id": "x"}) is None


def test_gate_ledger_resolved_payload_connected_with_policy() -> None:
    payload = GateLedger.resolved_payload(
        gate_id="g1", connected=True, write_policy="ask_first"
    )
    assert payload == {
        "v": 1,
        "gate_id": "g1",
        "outcome": "connected",
        "write_policy": "ask_first",
    }


def test_gate_ledger_resolved_payload_cancelled() -> None:
    payload = GateLedger.resolved_payload(
        gate_id="g1", connected=False, write_policy=None
    )
    assert payload == {"v": 1, "gate_id": "g1", "outcome": "cancelled"}


def test_gate_resume_is_frozen_contract() -> None:
    resume = GateResume(approved=True)
    with pytest.raises(Exception):
        resume.approved = False  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# GateLedger — the WRITE-approval gate (park_for_approval)
# --------------------------------------------------------------------------- #


async def _park_write(
    *,
    arguments: dict | None = None,
    tool_name: str = "create_issue",
    op_class: str = "write",
) -> dict:
    """Park a write approval and return the interrupt payload it raised."""

    interrupt = _CapturingInterrupt({"decision": "approved"})
    gate = _gate(interrupt=interrupt)
    await gate.park_for_approval(
        card=_card(auth_state=McpAuthState.AUTHENTICATED),
        tool_name=tool_name,
        arguments={} if arguments is None else arguments,
        op_class=op_class,
        approval_id="mcp_write:run_abcdef:call_1",
    )
    assert len(interrupt.payloads) == 1
    return interrupt.payloads[0]


async def test_write_gate_opened_payload_is_the_full_safe_field_set() -> None:
    """A parked write yields a ``gate.opened`` answering the audit question.

    Exact-equality on the payload is the point: which connector, which
    operation, and — via ``auth_state: insufficient`` — that this was a policy
    gate rather than a broken credential. Nothing else may ride along.
    """

    payload = await _park_write(arguments={"title": "Fix login"})

    assert GateLedger.is_write_gate(payload) is True
    assert GateLedger.opened_payload(interrupt_payload=payload) == {
        "v": 1,
        "gate_id": "mcp_write:run_abcdef:call_1",
        "connector": "linear",
        "purpose": "approve write create_issue on linear",
        "scopes": ["docs:read", "docs:write"],
        "auth_state": GateAuthState.INSUFFICIENT.value,
    }


async def test_write_gate_ledger_purpose_never_carries_tool_arguments() -> None:
    """The card may show the argument; the durable ledger row may not.

    ``GatePurposeBuilder`` puts a sanitised primary argument on the interrupt
    payload so a human can see what they are approving. Copying that into the
    ledger — as the connect gate's builder does — would put caller-controlled
    content, and with it any PII inside it, into a compliance record.
    """

    secret = "ada.lovelace@example.com wants **root** at https://evil.example/x"
    payload = await _park_write(arguments={"title": secret})

    # Present on the interactive card...
    assert "ada.lovelace@example.com" in payload["gate"]["purpose"]
    # ...and absent from every value of the ledger row.
    opened = GateLedger.opened_payload(interrupt_payload=payload)
    assert opened is not None
    assert "ada.lovelace" not in repr(opened)
    assert opened["purpose"] == "approve write create_issue on linear"


async def test_write_gate_opened_payload_records_the_op_class() -> None:
    """``op_class`` is the PDP's verdict and rides the purpose line verbatim."""

    payload = await _park_write(tool_name="delete_issue", op_class="destructive")
    opened = GateLedger.opened_payload(interrupt_payload=payload)

    assert opened is not None
    assert opened["purpose"] == "approve destructive delete_issue on linear"


def test_write_gate_ledger_purpose_sanitises_an_untrusted_tool_name() -> None:
    """``op`` comes off an MCP descriptor, so it is untrusted like an argument.

    The identifier whitelist has to keep ``_`` (it is structure in a tool name,
    not markdown emphasis) while still dropping newlines, link syntax and URLs.
    """

    payload = {
        "api_event_type": "approval_requested",
        "approval_id": "mcp_write:run_abcdef:call_1",
        "server_name": "linear",
        "gate": {
            "v": 1,
            "purpose": "to run x on Linear",
            "scopes": [],
            "op": "create_issue\n\n[see](https://evil.example/x)" + "z" * 200,
            "op_class": "write",
        },
    }
    opened = GateLedger.opened_payload(interrupt_payload=payload)

    assert opened is not None
    purpose = opened["purpose"]
    assert purpose.startswith("approve write create_issue")
    assert "\n" not in purpose
    assert "http" not in purpose
    assert "[" not in purpose
    assert "(" not in purpose
    # Bounded by the per-token cap even with an adversarially long name.
    assert len(purpose) <= 120


def test_connect_gate_is_not_classified_as_a_write_gate() -> None:
    payload = {
        "api_event_type": "mcp_auth_required",
        "approval_id": "mcp_auth:run_abcdef:seed:linear",
        "server_name": "linear",
        "gate": {"v": 1, "purpose": "p", "scopes": [], "auth_state": "expired"},
    }

    assert GateLedger.is_write_gate(payload) is False
    opened = GateLedger.opened_payload(interrupt_payload=payload)
    assert opened is not None
    assert opened["auth_state"] == "expired"
    assert opened["purpose"] == "p"


def test_plain_ask_a_question_is_not_a_gate() -> None:
    """The model's own question tool shares the write gate's interrupt shape.

    Only the additive ``gate`` block separates them, so an ordinary question
    must classify as neither a gate nor a write gate.
    """

    payload = {
        "api_event_type": "approval_requested",
        "approval_kind": "ask_a_question",
        "approval_id": "interrupt:run_abcdef:0",
        "question": "Which project?",
    }

    assert GateLedger.is_write_gate(payload) is False
    assert GateLedger.opened_payload(interrupt_payload=payload) is None

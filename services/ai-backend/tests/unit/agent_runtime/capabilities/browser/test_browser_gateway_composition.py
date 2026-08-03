"""Model-boundary proof: browser actions stage and never call the read client."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from agent_runtime.capabilities.mcp.execution_services import McpOperationStoredResult
from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType


def _runtime_context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user-browser",
        org_id="org-browser",
        roles={"employee"},
        permission_scopes={"browser:use", "docs:read"},
        model_profile=ModelConfig(
            provider="openai",
            model_name="gpt-test",
            max_input_tokens=4096,
            timeout_seconds=30,
            temperature=0,
        ),
        run_id="run-browser",
        trace_id="trace-browser",
    )


def _exact_click() -> dict[str, object]:
    return {
        "sessionRef": "browser-session://ses_exact",
        "pageRef": "browser-page://pg_exact",
        "origin": "https://example.com",
        "topLevelOrigin": "https://example.com",
        "elementRef": "e4_2",
        "elementFingerprint": "a" * 64,
        "pageGeneration": 4,
        "formFingerprint": "d" * 64,
        "formPayloadDigest": "e" * 64,
        "formActionUrl": "https://example.com/send",
        "method": "POST",
    }


@dataclass
class _OperationEvents:
    rows: list[str] = field(default_factory=list)

    async def emit(
        self,
        event_type: LedgerEventType,
        payload: Mapping[str, object],
        summary: str | None = None,
    ) -> None:
        del payload, summary
        self.rows.append(event_type.value)


class _UnusedResultStore:
    async def store_read_result(
        self, *, request: object, output: Mapping[str, object]
    ) -> McpOperationStoredResult:
        del request, output
        raise AssertionError("a staged browser action cannot persist a read result")

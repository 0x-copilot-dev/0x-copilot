"""Tests for the additive ``mcp_auth_required`` payload allow-list (PR 3.3).

The projector sanitizes wire payloads through a strict allow-list. PR 3.3
adds two optional fields — ``discovery_reason`` and ``expected_value`` —
that distinguish a non-blocking discovery card from a blocking auth gate.
This test pins:

  * Existing payloads (no ``discovery_reason``) still parse byte-identically.
  * New payloads with ``discovery_reason`` + ``expected_value`` pass through.
  * Unknown extra fields the model might invent get stripped (defence).
"""

from __future__ import annotations

from agent_runtime.api.constants import Keys
from runtime_api.schemas import (
    RuntimeApiEventType,
    RuntimeEventPresentationProjector,
)


def _payload_for(payload: dict[str, object]) -> dict[str, object]:
    return RuntimeEventPresentationProjector.payload_for_event(
        event_type=RuntimeApiEventType.MCP_AUTH_REQUIRED,
        payload=payload,
    )


class TestMcpAuthRequiredPayloadProjector:
    def test_blocking_payload_unchanged(self) -> None:
        # Pre-PR-3.3 payload shape — the projector must still emit the
        # same allow-listed fields without any new keys.
        result = _payload_for(
            {
                Keys.Field.APPROVAL_ID: "appr_1",
                Keys.Field.SERVER_ID: "linear",
                Keys.Field.SERVER_NAME: "linear",
                "display_name": "Linear",
                Keys.Field.AUTH_URL: "https://example.com/oauth/linear",
                Keys.Field.EXPIRES_AT: "2026-01-01T00:00:00+00:00",
                Keys.Payload.MESSAGE: "Authenticate Linear to continue.",
            }
        )
        assert Keys.Field.DISCOVERY_REASON not in result
        assert Keys.Field.EXPECTED_VALUE not in result
        assert result[Keys.Field.APPROVAL_ID] == "appr_1"
        assert result[Keys.Field.SERVER_ID] == "linear"

    def test_discovery_payload_passes_through_new_fields(self) -> None:
        result = _payload_for(
            {
                Keys.Field.APPROVAL_ID: "mcp_discovery:run_1:linear",
                Keys.Field.SERVER_ID: "linear",
                Keys.Field.SERVER_NAME: "linear",
                "display_name": "Linear",
                Keys.Field.AUTH_URL: "https://example.com/oauth/linear",
                Keys.Field.EXPIRES_AT: "2026-01-01T00:00:00+00:00",
                Keys.Payload.MESSAGE: "MCP authentication required",
                Keys.Field.DISCOVERY_REASON: "fetch ticket statuses",
                Keys.Field.EXPECTED_VALUE: "ground claims about progress",
            }
        )
        assert result[Keys.Field.DISCOVERY_REASON] == "fetch ticket statuses"
        assert result[Keys.Field.EXPECTED_VALUE] == "ground claims about progress"

    def test_unknown_extra_fields_are_stripped(self) -> None:
        result = _payload_for(
            {
                Keys.Field.APPROVAL_ID: "appr_x",
                Keys.Field.SERVER_ID: "linear",
                Keys.Field.SERVER_NAME: "linear",
                "display_name": "Linear",
                Keys.Field.AUTH_URL: "https://example.com/oauth/linear",
                Keys.Field.EXPIRES_AT: "2026-01-01T00:00:00+00:00",
                Keys.Payload.MESSAGE: "msg",
                # Pretend the agent invented these fields — they must not
                # leak through to the wire.
                "secret_token": "BAD",
                "internal_trace": "stack-frames",
            }
        )
        assert "secret_token" not in result
        assert "internal_trace" not in result

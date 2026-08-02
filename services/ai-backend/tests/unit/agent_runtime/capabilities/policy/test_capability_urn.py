"""Unit tests for the P0 capability-URN scheme + descriptor validation.

``CapabilityUrn`` is the one piece of the inert P0 vocabulary that carries real
branching logic (build + parse + two raise paths), and the whole scheme rests on
the invariant that a URN segment is byte-identical to the surface slug. These
lock the round-trip, the delimiter handling, the error cases, and the descriptor
boundary validation added in P0 review.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.policy.contracts import (
    Action,
    CapabilityDescriptor,
    CapabilityUrn,
    CapabilityUrnError,
    ConnectorState,
    Trust,
    UrnScheme,
)


class TestCapabilityUrnBuild:
    def test_for_mcp_normalizes_through_surface_slugs(self) -> None:
        # server_slug strips a catalog prefix ("seed:linear" -> "linear") and
        # lowercases; tool_slug lowercases. A URN segment must match the surface
        # URI's segment byte-for-byte.
        assert (
            CapabilityUrn.for_mcp("seed:Linear", "Get_Issue") == "mcp:linear:get_issue"
        )

    def test_for_builtin(self) -> None:
        assert CapabilityUrn.for_builtin("fs", "write") == "builtin:fs:write"


class TestCapabilityUrnParse:
    def test_round_trip_mcp(self) -> None:
        parsed = CapabilityUrn.parse(CapabilityUrn.for_mcp("linear", "create_issue"))
        assert parsed.scheme is UrnScheme.MCP
        assert parsed.namespace == "linear"
        assert parsed.name == "create_issue"

    def test_round_trip_builtin(self) -> None:
        parsed = CapabilityUrn.parse(CapabilityUrn.for_builtin("fs", "write"))
        assert parsed.scheme is UrnScheme.BUILTIN
        assert parsed.namespace == "fs"
        assert parsed.name == "write"

    def test_trailing_name_keeps_its_delimiter(self) -> None:
        # tool_slug does not strip ':'; parse keeps everything past the 2nd
        # colon whole, so a colon-bearing tool name survives.
        parsed = CapabilityUrn.parse("mcp:server:a:b")
        assert parsed.namespace == "server"
        assert parsed.name == "a:b"

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "mcp",
            "mcp:linear",
            "mcp::get_issue",
            ":linear:get_issue",
            "mcp:linear:",
        ],
    )
    def test_malformed_raises(self, bad: str) -> None:
        with pytest.raises(CapabilityUrnError):
            CapabilityUrn.parse(bad)

    def test_unknown_scheme_raises(self) -> None:
        with pytest.raises(CapabilityUrnError):
            CapabilityUrn.parse("ftp:host:path")


class TestCapabilityDescriptorValidatesUrn:
    def test_valid_descriptor(self) -> None:
        descriptor = CapabilityDescriptor(
            urn=CapabilityUrn.for_mcp("linear", "get_issue"),
            action=Action.READ,
            trust=Trust.TRUSTED,
            source="mcp",
            connector_state=ConnectorState.LIVE,
        )
        assert descriptor.action is Action.READ
        assert descriptor.trust is Trust.TRUSTED
        assert descriptor.scopes == ()

    def test_malformed_urn_rejected_at_boundary(self) -> None:
        with pytest.raises(ValidationError):
            CapabilityDescriptor(
                urn="not-a-urn",
                action=Action.READ,
                trust=Trust.UNTRUSTED,
                source="mcp",
                connector_state=ConnectorState.LIVE,
            )

    def test_frozen(self) -> None:
        descriptor = CapabilityDescriptor(
            urn=CapabilityUrn.for_builtin("fs", "read"),
            action=Action.READ,
            trust=Trust.TRUSTED,
            source="builtin",
            connector_state=ConnectorState.LIVE,
        )
        with pytest.raises(ValidationError):
            descriptor.action = Action.WRITE  # type: ignore[misc]

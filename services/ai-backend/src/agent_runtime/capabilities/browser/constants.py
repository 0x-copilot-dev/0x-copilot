"""Constants for the desktop-local browser MCP provider (AC8)."""

from __future__ import annotations


class BrowserBroker:
    """Wire constants shared with the desktop Electron-main browser broker.

    These MUST match ``apps/desktop/main/browser`` (``protocol.ts`` +
    ``browser-broker.ts``): the audience, protocol header value, and route
    paths are a byte-for-byte contract across the deployable boundary.
    """

    AUDIENCE = "desktop-browser-broker"
    PROTOCOL_VERSION = "1"
    PROTOCOL_HEADER = "x-browser-protocol"

    ROUTE_HANDSHAKE = "/v1/browser/handshake"
    ROUTE_TOOLS_LIST = "/v1/browser/tools/list"
    ROUTE_ACTION = "/v1/browser/action"

    #: Action credentials are short-lived; the AI client stamps this TTL onto
    #: every envelope's ``expiresAt`` (ms). The broker re-checks freshness.
    ENVELOPE_TTL_MS = 5 * 60 * 1000


class BrowserEnv:
    """Environment variables the desktop supervisor sets for the browser edge.

    ``FLAG`` is the byte-for-byte counterpart of the desktop-side
    ``DESKTOP_BROWSER_FLAG`` (``apps/desktop/main/browser/feature-gate.ts``); the
    subsystem — worker, loopback broker, and this MCP card — is opt-in behind it
    and fails closed when unset. ``BROKER_URL`` / ``BROKER_TOKEN`` carry the
    loopback base URL + bootstrap credential for the Electron-main browser broker
    (distinct from the AC5 capability broker), injected only by the trusted
    desktop service environment.
    """

    FLAG = "RUNTIME_ENABLE_DESKTOP_BROWSER"
    BROKER_URL = "DESKTOP_BROWSER_BROKER_URL"
    BROKER_TOKEN = "DESKTOP_BROWSER_BROKER_TOKEN"

    #: Truthy tokens mirror the desktop feature gate exactly.
    _TRUTHY = frozenset({"1", "true", "yes", "on", "enabled"})

    @classmethod
    def is_enabled(cls, value: str | None) -> bool:
        """Whether the browser flag value is explicitly truthy (fails closed)."""

        return (value or "").strip().lower() in cls._TRUTHY


class BrowserServer:
    """Identity of the desktop browser MCP server card."""

    NAME = "desktop_browser"
    DISPLAY_NAME = "Desktop browser"
    SHORT_DESCRIPTION = (
        "Read-only agentic browser on this device: navigate approved HTTPS "
        "origins, inspect the accessibility tree, and capture screenshots. "
        "Runs in an isolated, supervised browser with deny-by-default egress."
    )
    #: Single-user desktop is the ONLY profile where the card may appear.
    REQUIRED_DEPLOYMENT_PROFILE = "single_user_desktop"


class BrowserKeys:
    """JSON keys used on the browser broker wire."""

    AUD = "aud"
    NONCE = "nonce"
    REQUEST_ID = "requestId"
    EXPIRES_AT = "expiresAt"
    TOOL = "tool"
    NAME = "name"
    ARGUMENTS = "arguments"
    ACTION = "action"
    TOOLS = "tools"
    RESULT = "result"
    AUDIENCE = "audience"
    INPUT_SCHEMA = "inputSchema"
    DESCRIPTION = "description"


class BrowserMessages:
    """Safe public messages for browser MCP failures."""

    BROKER_UNAVAILABLE = "The desktop browser is not available."
    BROKER_UNAUTHENTICATED = "The desktop browser rejected the local credential."
    HANDSHAKE_AUDIENCE_MISMATCH = "The desktop browser broker audience did not match."
    INVALID_RESPONSE = "The desktop browser returned an invalid response."

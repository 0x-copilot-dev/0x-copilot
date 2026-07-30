"""PRD-06 D3(c) — the ``proxy_internal_rpc`` access-mode gate.

The authoritative permission boundary lives on the backend, on the trusted
side of the line. These tests exercise the gate directly on the
:class:`McpRegistryService` (no HTTP) so we can assert both the raised
:class:`ConnectorAccessDenied` and the fact that an ``off`` connector never
decrypts a vault token.
"""

from __future__ import annotations

import json

import pytest

from backend_app.connectors.store import ConnectorAccessMode
from backend_app.contracts import (
    InternalMcpRpcRequest,
    McpAuthMode,
    McpAuthState,
    McpServerHealth,
    McpServerRecord,
    McpTransport,
    TokenEnvelope,
)
from backend_app.service import ConnectorAccessDenied, McpRegistryService
from backend_app.store import InMemoryMcpStore

ORG = "org_acme"
USER = "usr_sarah"


class CountingVault:
    """Trivial identity vault that counts ``decrypt`` calls (DoD 8)."""

    def __init__(self) -> None:
        self.decrypt_calls = 0

    def encrypt(self, plaintext: str) -> str:
        return f"enc:{plaintext}"

    def decrypt(self, ciphertext: str) -> str:
        self.decrypt_calls += 1
        return ciphertext.removeprefix("enc:")

    def key_id_for(self, ciphertext: str) -> str:
        return "test-key"


# Tool list the fake remote server "advertises". ``read_tool`` is read-only;
# ``act_tool`` publishes no annotations block (fail-closed under ``read``).
_ADVERTISED_TOOLS = [
    {"name": "read_tool", "annotations": {"readOnlyHint": True}},
    {"name": "act_tool"},
]
_CURRENT_LEASE = "x" * 16


class FakeRemote:
    """Dispatches the backend-owned HTTP transport and records calls."""

    def __init__(self) -> None:
        self.methods: list[str] = []

    def __call__(self, request, timeout):
        payload = json.loads(request.data)
        method = payload.get("method")
        self.methods.append(method)  # type: ignore[arg-type]
        body = (
            {"jsonrpc": "2.0", "id": 1, "result": {"tools": _ADVERTISED_TOOLS}}
            if method == "tools/list"
            else {"jsonrpc": "2.0", "id": 2, "result": {"content": []}}
        )

        class Response:
            headers = {"content-type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, *_args: object) -> bytes:
                return json.dumps(body).encode()

        return Response()


def _service(
    *,
    access_mode: ConnectorAccessMode | None,
    seed_token: bool = True,
) -> tuple[McpRegistryService, str, CountingVault, FakeRemote]:
    global _CURRENT_LEASE
    store = InMemoryMcpStore()
    vault = CountingVault()
    service = McpRegistryService(store=store, token_vault=vault)
    record = McpServerRecord(
        org_id=ORG,
        user_id=USER,
        name="gmail",
        display_name="Gmail",
        url="https://mcp.example.com/mcp",
        transport=McpTransport.HTTP,
        auth_mode=McpAuthMode.OAUTH2,
        auth_state=McpAuthState.AUTHENTICATED,
        health=McpServerHealth.HEALTHY,
    )
    store.create_server(record)
    if seed_token:
        store.put_token(
            TokenEnvelope(
                server_id=record.server_id,
                org_id=ORG,
                user_id=USER,
                encrypted_access_token="enc:tok",
                encrypted_refresh_token=None,
                expires_at=None,  # never expires → _require_valid_token returns it
            )
        )
    # The gate resolver: return the configured mode for THIS server.
    service.connector_access_resolver = lambda r: access_mode
    remote = FakeRemote()
    import backend_app.mcp_transport

    backend_app.mcp_transport.urlopen = remote
    if seed_token:
        _CURRENT_LEASE = service.create_internal_client_session(
            org_id=ORG, user_id=USER, server_id=record.server_id
        ).lease
    else:
        _CURRENT_LEASE = "x" * 16
    return service, record.server_id, vault, remote


def _rpc(method: str, *, tool_name: str | None = None) -> InternalMcpRpcRequest:
    payload: dict[str, object] = {"jsonrpc": "2.0", "id": 9, "method": method}
    if tool_name is not None:
        payload["params"] = {"name": tool_name, "arguments": {}}
    return InternalMcpRpcRequest(
        org_id=ORG, user_id=USER, lease=_CURRENT_LEASE, payload=payload
    )


def test_proxy_internal_rpc_denies_off_connector() -> None:
    service, server_id, vault, _remote = _service(
        access_mode=ConnectorAccessMode.OFF, seed_token=False
    )
    with pytest.raises(ConnectorAccessDenied) as exc:
        service.proxy_internal_rpc(
            org_id=ORG,
            user_id=USER,
            server_id=server_id,
            request=_rpc("tools/list"),
        )
    assert exc.value.reason == ConnectorAccessDenied.OFF
    # The gate ran BEFORE the vault token was decrypted.
    assert vault.decrypt_calls == 0


@pytest.mark.parametrize(
    "request_factory, expect_allowed",
    [
        (lambda: _rpc("tools/list"), True),
        (lambda: _rpc("tools/call", tool_name="read_tool"), True),
        (lambda: _rpc("tools/call", tool_name="act_tool"), False),
    ],
)
def test_proxy_internal_rpc_read_mode_matrix(request_factory, expect_allowed) -> None:
    service, server_id, _vault, _remote = _service(access_mode=ConnectorAccessMode.READ)
    if expect_allowed:
        out = service.proxy_internal_rpc(
            org_id=ORG,
            user_id=USER,
            server_id=server_id,
            request=request_factory(),
        )
        assert out.payload["result"] is not None
    else:
        with pytest.raises(ConnectorAccessDenied) as exc:
            service.proxy_internal_rpc(
                org_id=ORG,
                user_id=USER,
                server_id=server_id,
                request=request_factory(),
            )
        assert exc.value.reason == ConnectorAccessDenied.READ_ONLY


@pytest.mark.parametrize(
    "request_factory",
    [
        lambda: _rpc("tools/list"),
        lambda: _rpc("tools/call", tool_name="read_tool"),
        lambda: _rpc("tools/call", tool_name="act_tool"),
    ],
)
def test_proxy_internal_rpc_allows_unjoined_server(request_factory) -> None:
    # No connector row joins the server (resolver returns None) → every
    # envelope is allowed, including the ``act_tool`` call the ``read`` matrix
    # denied.
    service, server_id, _vault, _remote = _service(access_mode=None)
    out = service.proxy_internal_rpc(
        org_id=ORG,
        user_id=USER,
        server_id=server_id,
        request=request_factory(),
    )
    assert out.payload["result"] is not None


# ── The public `/v1/mcp/servers` list carries the durable mode ───────────────
# `list_internal_cards` DROPS an `off` server, so the model never sees it. The
# public list keeps the row (Settings → Tools has to render and un-off it), which
# left every other client unable to tell an `off` connector from an available
# one — the composer's Tools popover rendered it as an ordinary connected row
# with a per-run toggle that could not grant anything. Carrying `access_mode`
# here is what lets a client match what the runtime will actually do.
@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (ConnectorAccessMode.READ, "read"),
        (ConnectorAccessMode.READ_ACT, "read_act"),
        (ConnectorAccessMode.OFF, "off"),
    ],
)
def test_list_servers_reports_access_mode(mode, expected) -> None:
    # `seed_token` mints an internal client session, which the `off` gate
    # rejects outright — the same reason the deny test above skips it. The token
    # has no bearing on the reported access mode.
    service, server_id, _vault, _remote = _service(
        access_mode=mode, seed_token=mode is not ConnectorAccessMode.OFF
    )
    listed = service.list_servers(org_id=ORG, user_id=USER)
    row = next(s for s in listed.servers if s.server_id == server_id)
    assert row.access_mode == expected


def test_list_servers_defaults_an_unjoined_server_to_read() -> None:
    # No connector row joins the server (resolver returns None). An unprojected
    # server is not a user-set `off`, so it must read as the default `read`.
    service, server_id, _vault, _remote = _service(access_mode=None)
    listed = service.list_servers(org_id=ORG, user_id=USER)
    row = next(s for s in listed.servers if s.server_id == server_id)
    assert row.access_mode == "read"

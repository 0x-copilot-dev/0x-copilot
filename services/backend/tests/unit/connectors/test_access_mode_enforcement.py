"""PRD-06 D3(c) — the ``proxy_internal_rpc`` access-mode gate.

The authoritative permission boundary lives on the backend, on the trusted
side of the line. These tests exercise the gate directly on the
:class:`McpRegistryService` (no HTTP) so we can assert both the raised
:class:`ConnectorAccessDenied` and the fact that an ``off`` connector never
decrypts a vault token.
"""

from __future__ import annotations

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


class FakeRemote:
    """Dispatches ``_post_remote_mcp_rpc`` by JSON-RPC method + records calls."""

    def __init__(self) -> None:
        self.methods: list[str] = []

    def __call__(
        self, server_url: str, payload: dict[str, object], access_token: str
    ) -> dict[str, object]:
        method = payload.get("method")
        self.methods.append(method)  # type: ignore[arg-type]
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": 1, "result": {"tools": _ADVERTISED_TOOLS}}
        return {"jsonrpc": "2.0", "id": 2, "result": {"content": []}}


def _service(
    *,
    access_mode: ConnectorAccessMode | None,
    seed_token: bool = True,
) -> tuple[McpRegistryService, str, CountingVault, FakeRemote]:
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
    service._post_remote_mcp_rpc = remote  # type: ignore[method-assign,assignment]
    return service, record.server_id, vault, remote


def _rpc(method: str, *, tool_name: str | None = None) -> InternalMcpRpcRequest:
    payload: dict[str, object] = {"jsonrpc": "2.0", "id": 9, "method": method}
    if tool_name is not None:
        payload["params"] = {"name": tool_name, "arguments": {}}
    return InternalMcpRpcRequest(org_id=ORG, user_id=USER, payload=payload)


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

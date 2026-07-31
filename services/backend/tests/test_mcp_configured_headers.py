"""Configured request headers on the Streamable HTTP MCP transport.

`auth_mode = api_key` has been a valid value since the baseline with nothing
behind it: no column held a key and the transport sent only the OAuth bearer,
so a server authenticated by a static credential (a GitHub PAT, a vendor API
key) could be registered and could never connect.

These tests capture the actual outgoing `urllib.request.Request` and assert on
the headers it carries, because that object IS the contract with the server —
asserting on the binding instead would pass while sending nothing.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from backend_app.contracts import McpConfiguredValue
from backend_app.mcp_session_pool import VerifiedMcpSessionScopeKey
from backend_app.mcp_transport import McpHttpTransportFactory
from backend_app.token_vault import LocalTokenVault

_VAULT_SECRET = "unit-test-secret-value-at-least-32-chars"
_ENDPOINT = "https://api.githubcopilot.com/mcp/"


class _Response:
    """Minimal stand-in for the object `urlopen` yields."""

    headers = {"content-type": "application/json"}

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    @staticmethod
    def read(_limit: int) -> bytes:
        return json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}).encode()


@pytest.fixture()
def sent(monkeypatch) -> list[Any]:
    """Capture every Request the transport hands to `urlopen`."""

    captured: list[Any] = []

    def fake_urlopen(request: Any, **_: object) -> _Response:
        captured.append(request)
        return _Response()

    monkeypatch.setattr("backend_app.mcp_transport.urlopen", fake_urlopen)
    return captured


def _scope() -> VerifiedMcpSessionScopeKey:
    return VerifiedMcpSessionScopeKey(
        org_id="org_1",
        profile_partition="p",
        user_id="user_1",
        server_id="srv_1",
        credential_subject=hashlib.sha256(b"header-test").hexdigest(),
        auth_epoch="e",
        transport_revision="r",
        session_scope="s",
    )


def _transport(
    *,
    headers: tuple[McpConfiguredValue, ...] = (),
    encrypted_access_token: str | None = None,
    vault: LocalTokenVault | None = None,
) -> Any:
    factory = McpHttpTransportFactory(
        token_vault=vault or LocalTokenVault(secret=_VAULT_SECRET)
    )
    scope = _scope()
    factory.bind(
        scope=scope,
        endpoint=_ENDPOINT,
        encrypted_access_token=encrypted_access_token,
        headers=headers,
    )
    return factory.connect(scope)


def test_literal_header_is_sent(sent) -> None:
    transport = _transport(
        headers=(McpConfiguredValue(name="X-Api-Version", value="2"),)
    )

    transport.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, fence=None)

    # `urllib` title-cases header names it stores.
    assert sent[0].get_header("X-api-version") == "2"


def test_secret_header_is_decrypted_before_sending(sent) -> None:
    vault = LocalTokenVault(secret=_VAULT_SECRET)
    transport = _transport(
        vault=vault,
        headers=(
            McpConfiguredValue(
                name="Authorization",
                encrypted_value=vault.encrypt("Bearer ghp_realtoken"),
            ),
        ),
    )

    transport.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, fence=None)

    assert sent[0].get_header("Authorization") == "Bearer ghp_realtoken"


def test_configured_authorization_overrides_the_oauth_bearer(sent) -> None:
    """An explicitly configured credential is the one the user meant.

    It also must not be sent ALONGSIDE the OAuth bearer: two `Authorization`
    headers on one request is a malformed request, and which one the server
    honours would be arbitrary.
    """

    vault = LocalTokenVault(secret=_VAULT_SECRET)
    transport = _transport(
        vault=vault,
        encrypted_access_token=vault.encrypt("oauth-token"),
        headers=(
            McpConfiguredValue(
                name="Authorization",
                encrypted_value=vault.encrypt("Bearer configured"),
            ),
        ),
    )

    transport.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, fence=None)

    request = sent[0]
    assert request.get_header("Authorization") == "Bearer configured"
    # One header, not two: the lower-cased merge folded them together.
    authorization_headers = [
        name for name in request.headers if name.lower() == "authorization"
    ]
    assert len(authorization_headers) == 1


def test_oauth_bearer_still_applies_when_no_header_is_configured(sent) -> None:
    vault = LocalTokenVault(secret=_VAULT_SECRET)
    transport = _transport(
        vault=vault, encrypted_access_token=vault.encrypt("oauth-token")
    )

    transport.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, fence=None)

    assert sent[0].get_header("Authorization") == "Bearer oauth-token"


def test_a_value_is_either_plain_or_sealed_never_neither() -> None:
    """The contract makes "configured but empty" unrepresentable.

    An `Authorization:` with nothing after it earns a 401 that reads as a bad
    token, sending the user to re-check a credential they never entered. The
    service drops such a value rather than storing it, and the model refuses to
    hold one at all.
    """

    with pytest.raises(ValueError):
        McpConfiguredValue(name="Authorization")
    with pytest.raises(ValueError):
        McpConfiguredValue(name="Authorization", value="x", encrypted_value="y")


def test_protocol_headers_are_always_present(sent) -> None:
    transport = _transport(
        headers=(McpConfiguredValue(name="X-Api-Version", value="2"),)
    )

    transport.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, fence=None)

    # Spec-mandated on every Streamable HTTP request.
    assert sent[0].get_header("Accept") == "application/json, text/event-stream"
    assert sent[0].get_header("Content-type") == "application/json"

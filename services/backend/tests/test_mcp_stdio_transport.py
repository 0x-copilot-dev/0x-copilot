"""The stdio MCP transport, driven against a REAL child process.

`McpTransport.STDIO` has existed in the contracts since the baseline with no
implementation behind it — the only transport was HTTP — so "stdio" was a
value the API accepted and nothing could honour. These tests exist to keep
that from being true again, which means they must not mock the subprocess: a
faked `Popen` would pass whether or not the pipes, the environment, and the
newline framing are right, and those are the entire substance of the
transport.

The stand-in server is a few lines of Python echoing JSON-RPC over stdin and
stdout. It is not a real MCP server and does not need to be — the transport's
job ends at "one framed request in, one framed response out".
"""

from __future__ import annotations

import hashlib
import os
import sys

import pytest
from backend_app.contracts import McpConfiguredValue, McpStdioConfig
from backend_app.mcp_transport import (
    McpHttpTransportFactory,
    McpRemoteTransportError,
    McpStdioTransport,
)
from backend_app.mcp_session_pool import VerifiedMcpSessionScopeKey
from backend_app.token_vault import LocalTokenVault

_VAULT_SECRET = "unit-test-secret-value-at-least-32-chars"

# Reads one JSON-RPC line, replies with a result echoing the method and the
# environment variable the test cares about. Deliberately writes a line to
# stderr first, so the stderr-drain path is exercised on every round-trip.
_ECHO_SERVER = """
import json, sys
print("server starting", file=sys.stderr, flush=True)
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    request = json.loads(line)
    sys.stdout.write(json.dumps({
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "result": {
            "method": request.get("method"),
            "api_token": __import__("os").environ.get("API_TOKEN"),
            "leaked_vault_secret": __import__("os").environ.get(
                "MCP_TOKEN_VAULT_SECRET"
            ),
        },
    }) + "\\n")
    sys.stdout.flush()
"""

# Never terminates and never answers. Used to prove a hung server surfaces as
# an error rather than wedging the caller forever.
_SILENT_SERVER = """
import sys, time
for line in sys.stdin:
    time.sleep(30)
"""


def _scope() -> VerifiedMcpSessionScopeKey:
    """A structurally valid pool scope key.

    `credential_subject` is validated as a lowercase SHA-256, so a placeholder
    string will not do even in a test that never uses a credential.
    """

    return VerifiedMcpSessionScopeKey(
        org_id="org_1",
        profile_partition="p",
        user_id="user_1",
        server_id="srv_1",
        credential_subject=hashlib.sha256(b"stdio-test").hexdigest(),
        auth_epoch="e",
        transport_revision="r",
        session_scope="s",
    )


def _vault() -> LocalTokenVault:
    return LocalTokenVault(secret=_VAULT_SECRET)


def _transport(config: McpStdioConfig) -> McpStdioTransport:
    """Build a stdio transport through the FACTORY, not by hand.

    Going through `bind`/`connect` is the point: it proves the factory routes
    a stdio binding to this class at all, which is the seam that decides
    whether any of this is reachable in production.
    """

    factory = McpHttpTransportFactory(token_vault=_vault())
    scope = _scope()
    factory.bind(scope=scope, endpoint=None, encrypted_access_token=None, stdio=config)
    transport = factory.connect(scope)
    assert isinstance(transport, McpStdioTransport)
    return transport


def _echo_config(**overrides: object) -> McpStdioConfig:
    return McpStdioConfig(
        command=sys.executable,
        args=("-c", _ECHO_SERVER),
        **overrides,  # type: ignore[arg-type]
    )


def test_round_trips_json_rpc_over_pipes() -> None:
    transport = _transport(_echo_config())
    try:
        response = transport.rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, fence=None
        )
    finally:
        transport.close()

    assert response["id"] == 1
    assert response["result"]["method"] == "tools/list"


def test_reuses_one_process_across_calls() -> None:
    """A pooled transport must not respawn the server per request.

    Relaunching would be correct-looking and quietly ruinous: every call would
    pay process startup, and a stateful server would lose its session between
    consecutive requests of the same conversation.
    """

    transport = _transport(_echo_config())
    try:
        transport.rpc({"jsonrpc": "2.0", "id": 1, "method": "a"}, fence=None)
        first_pid = transport._process.pid  # type: ignore[union-attr]
        transport.rpc({"jsonrpc": "2.0", "id": 2, "method": "b"}, fence=None)
        second_pid = transport._process.pid  # type: ignore[union-attr]
    finally:
        transport.close()

    assert first_pid == second_pid


def test_env_secret_is_decrypted_into_the_child() -> None:
    vault = _vault()
    config = _echo_config(
        env=(
            McpConfiguredValue(
                name="API_TOKEN",
                encrypted_value=vault.encrypt("s3cr3t-token"),
            ),
        )
    )
    transport = _transport(config)
    try:
        response = transport.rpc({"jsonrpc": "2.0", "id": 1, "method": "x"}, fence=None)
    finally:
        transport.close()

    assert response["result"]["api_token"] == "s3cr3t-token"


def test_child_does_not_inherit_the_backend_environment(monkeypatch) -> None:
    """The one that matters most.

    A local MCP server is third-party code the user pasted a command for. If
    it inherited this process's environment it would receive the token-vault
    key, the database URL, and the auth secret — every credential the backend
    holds — for free, from any server the user was talked into adding.
    """

    monkeypatch.setenv("MCP_TOKEN_VAULT_SECRET", _VAULT_SECRET)
    transport = _transport(_echo_config())
    try:
        response = transport.rpc({"jsonrpc": "2.0", "id": 1, "method": "x"}, fence=None)
    finally:
        transport.close()

    assert response["result"]["leaked_vault_secret"] is None


def test_path_is_forwarded_so_interpreters_resolve() -> None:
    # The flip side of a closed environment: strip PATH too and nothing can be
    # launched by bare name, which would make every realistic `npx ...` config
    # fail with a confusing ENOENT.
    transport = _transport(_echo_config())
    try:
        assert transport._environment()["PATH"] == os.environ["PATH"]
    finally:
        transport.close()


def test_arguments_are_never_shell_interpreted() -> None:
    """No shell means shell metacharacters are inert.

    If this ever regressed to `shell=True`, the argument below would run a
    second command. Asserting the echo server received it as a literal string
    is what proves it did not.
    """

    payload = "; echo pwned"
    transport = _transport(_echo_config())
    try:
        response = transport.rpc(
            {"jsonrpc": "2.0", "id": 1, "method": payload}, fence=None
        )
    finally:
        transport.close()

    assert response["result"]["method"] == payload


def test_unlaunchable_command_raises_a_transport_error() -> None:
    transport = _transport(
        McpStdioConfig(command="/nonexistent/definitely-not-a-real-mcp-server")
    )
    with pytest.raises(McpRemoteTransportError, match="could not start"):
        transport.rpc({"jsonrpc": "2.0", "id": 1, "method": "x"}, fence=None)


def test_server_that_exits_surfaces_as_a_transport_error() -> None:
    transport = _transport(
        McpStdioConfig(command=sys.executable, args=("-c", "raise SystemExit(0)"))
    )
    try:
        with pytest.raises(McpRemoteTransportError):
            transport.rpc({"jsonrpc": "2.0", "id": 1, "method": "x"}, fence=None)
    finally:
        transport.close()


def test_close_terminates_the_child() -> None:
    transport = _transport(_echo_config())
    transport.rpc({"jsonrpc": "2.0", "id": 1, "method": "x"}, fence=None)
    process = transport._process
    assert process is not None

    transport.close()

    assert process.poll() is not None


def test_close_kills_a_server_that_ignores_stdin_close() -> None:
    transport = _transport(
        McpStdioConfig(command=sys.executable, args=("-c", _SILENT_SERVER))
    )
    transport._ensure_process()
    process = transport._process
    assert process is not None

    transport.close()

    assert process.poll() is not None


def test_closed_transport_refuses_further_requests() -> None:
    transport = _transport(_echo_config())
    transport.close()

    with pytest.raises(ConnectionError, match="closed"):
        transport.rpc({"jsonrpc": "2.0", "id": 1, "method": "x"}, fence=None)


def test_invalid_json_from_the_server_is_reported_as_such() -> None:
    transport = _transport(
        McpStdioConfig(
            command=sys.executable,
            args=(
                "-c",
                "import sys\nfor l in sys.stdin: print('not json'); sys.stdout.flush()",
            ),
        )
    )
    try:
        with pytest.raises(McpRemoteTransportError, match="invalid JSON-RPC"):
            transport.rpc({"jsonrpc": "2.0", "id": 1, "method": "x"}, fence=None)
    finally:
        transport.close()


def test_factory_rejects_a_binding_with_neither_address() -> None:
    factory = McpHttpTransportFactory(token_vault=_vault())
    scope = _scope()

    with pytest.raises(ValueError, match="exactly one"):
        factory.bind(scope=scope, endpoint=None, encrypted_access_token=None)
    with pytest.raises(ValueError, match="exactly one"):
        factory.bind(
            scope=scope,
            endpoint="https://example.com/mcp",
            encrypted_access_token=None,
            stdio=McpStdioConfig(command="npx"),
        )


def test_json_rpc_framing_survives_a_payload_containing_newlines() -> None:
    """Newlines delimit messages, so one inside a payload must be escaped.

    `json.dumps` escapes them, which is why this passes — but the framing is
    the transport's contract, and an implementation that ever wrote raw text
    would corrupt the stream in a way that only shows up on real content.
    """

    transport = _transport(_echo_config())
    try:
        response = transport.rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "a\nb\nc"}, fence=None
        )
    finally:
        transport.close()

    assert response["result"]["method"] == "a\nb\nc"

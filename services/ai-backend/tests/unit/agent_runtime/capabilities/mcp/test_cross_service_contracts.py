"""Parity checks for the F8 backend-to-runtime golden contract."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

import httpx
from agent_runtime.capabilities.mcp.backend_provider import BackendMcpClient
from agent_runtime.capabilities.mcp.cards import McpServerCard
from agent_runtime.capabilities.mcp.client import McpAuthError, McpLeaseError
from agent_runtime.capabilities.mcp.revision_wire import (
    BackendMcpRevision,
    BackendMcpRevisionClient,
    BackendMcpRevisionCursorExpired,
    BackendMcpRevisionFeed,
    BackendMcpRevisionNotice,
    BackendMcpRevisionReason,
)
from copilot_service_contracts.mcp_cross_service_contract import (
    MCP_CROSS_SERVICE_CONTRACT_VERSION,
    load_mcp_cross_service_golden_contract,
)


class _OneResponseHttpClient:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.posts = 0

    async def post(self, *_args: object, **_kwargs: object) -> httpx.Response:
        self.posts += 1
        return self.response

    async def get(self, *_args: object, **_kwargs: object) -> httpx.Response:
        return self.response


class _ScriptedHttpClient:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = responses
        self.posts: list[dict[str, object]] = []

    async def post(self, *args: object, **kwargs: object) -> httpx.Response:
        self.posts.append({"args": args, "kwargs": kwargs})
        return self._responses.pop(0)


def _response(status_code: int, body: object) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=body,
        request=httpx.Request("POST", "http://backend.test"),
    )


def _backend_mcp_client(
    response: httpx.Response,
) -> tuple[BackendMcpClient, _OneResponseHttpClient]:
    http_client = _OneResponseHttpClient(response)
    client = BackendMcpClient(
        backend_url="http://backend.test",
        runtime_context=SimpleNamespace(org_id="org_demo", user_id="user_demo"),
        card=McpServerCard(
            server_id="server_demo",
            name="server_demo",
            short_description="Demo MCP server.",
            transport="http",
            auth_mode="none",
            health="healthy",
            load_cost=1,
        ),
        http_client=http_client,  # type: ignore[arg-type]
    )
    return client, http_client


def test_client_session_rpc_and_release_success_use_only_fixture_wire_shapes() -> None:
    contract = _contract()
    http_client = _ScriptedHttpClient(
        [
            _response(200, contract["client_session_acquire_success"]),
            _response(200, contract["rpc_response"]),
            _response(200, contract["release_success"]),
        ]
    )
    client = BackendMcpClient(
        backend_url="http://backend.test",
        runtime_context=SimpleNamespace(org_id="org_demo", user_id="user_demo"),
        card=McpServerCard(
            server_id="server_demo",
            name="server_demo",
            short_description="Demo MCP server.",
            transport="http",
            auth_mode="none",
            health="healthy",
            load_cost=1,
        ),
        http_client=http_client,  # type: ignore[arg-type]
    )

    asyncio.run(client._acquire_lease())
    assert client.lease == contract["client_session_acquire_success"]["lease"]
    rpc_response = asyncio.run(client._post_rpc(contract["rpc_request"]["payload"]))
    assert rpc_response == contract["rpc_response"]["payload"]
    asyncio.run(client.aclose(cancel=True))

    assert len(http_client.posts) == 3
    acquire, rpc, release = http_client.posts
    assert str(acquire["args"][0]).endswith("/client-session")
    assert acquire["kwargs"]["params"] == {"org_id": "org_demo", "user_id": "user_demo"}
    assert str(rpc["args"][0]).endswith("/rpc")
    assert rpc["kwargs"]["json"] == contract["rpc_request"]
    assert str(release["args"][0]).endswith("/client-session/release")
    assert release["kwargs"]["json"] == contract["release_cancel_request"]
    assert client.lease is None


def _contract() -> dict[str, Any]:
    contract = load_mcp_cross_service_golden_contract()
    assert contract["contract_version"] == MCP_CROSS_SERVICE_CONTRACT_VERSION
    return contract


def _keys(value: object) -> set[str]:
    if isinstance(value, Mapping):
        return set(value) | set().union(*(_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_keys(item) for item in value)) if value else set()
    return set()


def test_shared_f8_contract_matches_revision_wire_models() -> None:
    contract = _contract()

    revision = BackendMcpRevision.model_validate(contract["exact_revision_response"])
    notice = BackendMcpRevisionNotice.model_validate(contract["revision_notice"])
    feed = BackendMcpRevisionFeed.model_validate(contract["revision_feed_page"])

    assert revision.model_dump(mode="json") == contract["exact_revision_response"]
    assert notice.model_dump(mode="json") == contract["revision_notice"]
    assert feed.model_dump(mode="json") == contract["revision_feed_page"]
    assert set(contract["revision_reason_values"]) == {
        reason.value for reason in BackendMcpRevisionReason
    }

    limits = contract["limits"]
    assert len(feed.notices) <= limits["revision_feed_page_max_length"]
    assert len(feed.next_cursor or "") <= limits["cursor_max_length"]
    assert len(revision.source) <= limits["source_max_length"]


def test_revision_session_and_feed_examples_exclude_secret_like_fields() -> None:
    contract = _contract()
    forbidden = set(contract["forbidden_field_names"])
    safe_parts = (
        contract["client_session_acquire_success"],
        contract["exact_revision_response"],
        contract["revision_notice"],
        contract["revision_feed_page"],
    )

    for part in safe_parts:
        assert not (_keys(part) & forbidden)


def test_typed_lease_failure_mapping_matches_retry_and_replay_safety() -> None:
    contract = _contract()
    matrix = contract["failure_matrix"]
    codes = [row["code"] for row in matrix]

    assert len(codes) == len(set(codes))
    assert set(codes) == BackendMcpClient._LEASE_FAILURE_CODES
    assert [row["code"] for row in matrix if row["redispatch_safe"]] == [
        "lease_stale_pre_dispatch"
    ]
    assert [row["code"] for row in matrix if row["acquisition_retryable"]] == [
        "pool_saturated",
        "server_unavailable",
    ]
    assert [row["code"] for row in matrix if row["rpc_resend_safe"]] == [
        "lease_stale_pre_dispatch"
    ]

    for row in matrix:
        response = httpx.Response(
            row["status_code"],
            json={
                "detail": {
                    "code": row["code"],
                    "redispatch_safe": row["redispatch_safe"],
                }
            },
        )
        mapped = BackendMcpClient._lease_error_from_response(response)
        if row["code"] == "auth_required":
            assert isinstance(mapped, McpAuthError)
            continue
        assert isinstance(mapped, McpLeaseError)
        assert mapped.code == row["code"]
        assert mapped.redispatch_safe is row["redispatch_safe"]

    # Acquisition retry is deliberately narrower than generic lease mapping:
    # only these failures are safe before any JSON-RPC dispatch occurs.
    acquisition_retryable = {
        row["code"] for row in matrix if row["acquisition_retryable"]
    }
    assert acquisition_retryable == {"pool_saturated", "server_unavailable"}
    assert "ambiguous_transport_failure" not in acquisition_retryable

    for row in matrix:
        response = httpx.Response(
            row["status_code"],
            json={
                "detail": {
                    "code": row["code"],
                    "redispatch_safe": row["redispatch_safe"],
                }
            },
        )
        client, _ = _backend_mcp_client(response)
        try:
            asyncio.run(client._acquire_lease())
        except McpAuthError:
            assert row["code"] == "auth_required"
        except McpLeaseError as exc:
            assert exc.code == row["code"]
            assert exc.acquisition_safe is row["acquisition_retryable"]
        else:  # pragma: no cover - every matrix row is an error response
            raise AssertionError("typed failure unexpectedly acquired a lease")


def test_ambiguous_rpc_failure_is_never_resent() -> None:
    contract = _contract()
    ambiguous = next(
        row
        for row in contract["failure_matrix"]
        if row["code"] == "ambiguous_transport_failure"
    )
    client, http_client = _backend_mcp_client(
        httpx.Response(
            ambiguous["status_code"],
            json={
                "detail": {
                    "code": ambiguous["code"],
                    "redispatch_safe": ambiguous["redispatch_safe"],
                }
            },
        )
    )
    client.lease = _contract()["client_session_acquire_success"]["lease"]

    try:
        asyncio.run(client._rpc({"jsonrpc": "2.0", "method": "tools/list"}))
    except McpLeaseError as exc:
        assert exc.code == "ambiguous_transport_failure"
    else:  # pragma: no cover - the fixture is explicitly non-resend-safe
        raise AssertionError("ambiguous RPC failure must not be resent")
    assert http_client.posts == 1


def test_cursor_expiry_contract_maps_to_the_typed_wire_error() -> None:
    contract = _contract()
    cursor_error = contract["cursor_expired_error"]

    assert cursor_error["status_code"] == httpx.codes.GONE
    assert cursor_error["client_error"] == BackendMcpRevisionCursorExpired.__name__
    assert cursor_error["body"] == {"detail": "revision feed cursor expired"}

    wire = BackendMcpRevisionClient(
        "http://backend.test",
        http_client=_OneResponseHttpClient(
            httpx.Response(cursor_error["status_code"], json=cursor_error["body"])
        ),  # type: ignore[arg-type]
    )
    try:
        asyncio.run(
            wire.feed(org_id="org_demo", user_id="user_demo", after_cursor="old")
        )
    except BackendMcpRevisionCursorExpired:
        pass
    else:  # pragma: no cover - 410 is a closed typed mapping
        raise AssertionError("gone revision-feed response must be typed")

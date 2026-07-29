"""Parity checks for the body-free F8 backend-to-runtime golden contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend_app.contracts import (
    InternalMcpClientSession,
    InternalMcpLeaseFailure,
    InternalMcpLeaseFailureCode,
    InternalMcpRpcRequest,
    InternalMcpRpcResponse,
    InternalMcpSessionReleaseRequest,
    InternalMcpSessionReleaseResponse,
    McpDescriptorRevision,
    McpDescriptorRevisionFeed,
    McpDescriptorRevisionNotice,
    McpRevisionReason,
)
from backend_app.service import InternalMcpLeaseFailureError
from copilot_service_contracts.mcp_cross_service_contract import (
    MCP_CROSS_SERVICE_CONTRACT_VERSION,
    load_mcp_cross_service_golden_contract,
)


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


def test_shared_f8_contract_loader_and_body_free_wire_shapes() -> None:
    contract = _contract()

    revision = McpDescriptorRevision.model_validate(contract["exact_revision_response"])
    notice = McpDescriptorRevisionNotice.model_validate(contract["revision_notice"])
    feed = McpDescriptorRevisionFeed.model_validate(contract["revision_feed_page"])
    session = InternalMcpClientSession.model_validate(
        contract["client_session_acquire_success"]
    )
    rpc_request = InternalMcpRpcRequest.model_validate(contract["rpc_request"])
    rpc_response = InternalMcpRpcResponse.model_validate(contract["rpc_response"])
    release = InternalMcpSessionReleaseRequest.model_validate(
        contract["release_cancel_request"]
    )
    outcome = InternalMcpSessionReleaseResponse.model_validate(
        contract["release_success"]
    )

    assert revision.model_dump(mode="json") == contract["exact_revision_response"]
    assert notice.model_dump(mode="json") == contract["revision_notice"]
    assert feed.model_dump(mode="json") == contract["revision_feed_page"]
    assert session.model_dump(mode="json") == contract["client_session_acquire_success"]
    assert rpc_request.model_dump(mode="json") == contract["rpc_request"]
    assert rpc_response.model_dump(mode="json") == contract["rpc_response"]
    assert release.model_dump(mode="json") == contract["release_cancel_request"]
    assert outcome.model_dump(mode="json") == contract["release_success"]

    limits = contract["limits"]
    assert len(session.lease) >= limits["lease_min_length"]
    assert len(session.lease) <= limits["lease_max_length"]
    assert len(feed.notices) <= limits["revision_feed_page_max_length"]
    assert all(
        len(value) <= limits["revision_max_length"]
        for value in (
            revision.revision,
            revision.subject_scope_hash,
            revision.descriptor_digest,
        )
    )
    assert set(contract["revision_reason_values"]) == {
        reason.value for reason in McpRevisionReason
    }


def test_session_revision_and_feed_examples_reject_secret_like_fields() -> None:
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


def test_failure_matrix_matches_service_and_route_error_contract() -> None:
    contract = _contract()
    matrix = contract["failure_matrix"]
    codes = [row["code"] for row in matrix]

    assert len(codes) == len(set(codes))
    assert set(codes) == {code.value for code in InternalMcpLeaseFailureCode}
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
        code = InternalMcpLeaseFailureCode(row["code"])
        failure = InternalMcpLeaseFailure(
            code=code, redispatch_safe=row["redispatch_safe"]
        )
        raised = InternalMcpLeaseFailureError(code)
        assert raised.status_code == row["status_code"]
        assert raised.failure == failure
        # Every internal MCP route nests the service's bounded failure object
        # under FastAPI's standard ``detail`` error envelope.
        assert {"detail": raised.failure.model_dump(mode="json")} == {
            "detail": {"code": row["code"], "redispatch_safe": row["redispatch_safe"]}
        }


def test_cursor_expiry_is_a_generic_gone_error() -> None:
    contract = _contract()
    cursor_error = contract["cursor_expired_error"]

    assert cursor_error["status_code"] == 410
    assert cursor_error["body"] == {"detail": "revision feed cursor expired"}

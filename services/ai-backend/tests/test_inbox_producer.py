"""Tests for the cross-service Inbox producer (P4-A2).

Verifies:

* :class:`InboxProducerPort` shape (Null + Http both satisfy it).
* :class:`HttpInboxProducer` sends service-token + tenant headers on every POST.
* Idempotency-key flows through both the header AND the payload's
  ``external_ref`` so backend's UNIQUE-(producer_id, external_ref) wins.
* Required-input rejection: empty ``tenant_id`` / ``target_user_id`` /
  ``idempotency_key`` raise :class:`InboxProducerError` and never reach the
  wire.
* Network failures are swallowed (the runtime keeps going; inline approval
  is the source of truth).
* Factory wiring: Null when env unconfigured, Http when env + client present.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from agent_runtime.api.inbox_producer import (
    HttpInboxProducer,
    InboxItemDraft,
    InboxProducerError,
    InboxProducerFactory,
    InboxProducerPort,
    NullInboxProducer,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _draft(**overrides: Any) -> InboxItemDraft:
    """Build a baseline approval-fallback draft."""
    base: dict[str, Any] = {
        "recipient_user_id": "user_a",
        "kind": "approval_request",
        "subject": "Approval needed",
        "preview": "Atlas drafted an edit you need to review.",
        "body": "Open the thread to review the proposed edit.",
        "approval_id": "approval_001",
        "thread_id": "conv_001",
        "run_id": "run_001",
        "sender_agent_id": "agent_atlas",
        "sender_agent_name": "Atlas",
    }
    base.update(overrides)
    return InboxItemDraft(**base)


class _RequestRecorder:
    """Captures every outbound request for assertions."""

    def __init__(self, status_code: int = 201) -> None:
        self.status_code = status_code
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status_code, json={"id": "inbox_001"})


# ---------------------------------------------------------------------------
# Port shape
# ---------------------------------------------------------------------------


class TestPortShape:
    def test_null_producer_satisfies_port(self) -> None:
        assert isinstance(NullInboxProducer(), InboxProducerPort)

    @pytest.mark.asyncio
    async def test_http_producer_satisfies_port(self) -> None:
        async with httpx.AsyncClient() as client:
            producer = HttpInboxProducer(
                http_client=client,
                backend_url="https://backend.local",
                service_token="tok",
            )
            assert isinstance(producer, InboxProducerPort)


# ---------------------------------------------------------------------------
# HttpInboxProducer
# ---------------------------------------------------------------------------


class TestHttpInboxProducer:
    @pytest.mark.asyncio
    async def test_post_includes_service_token_and_tenant_headers(self) -> None:
        recorder = _RequestRecorder()
        transport = httpx.MockTransport(recorder.handler)
        async with httpx.AsyncClient(transport=transport) as client:
            producer = HttpInboxProducer(
                http_client=client,
                backend_url="https://backend.local",
                service_token="svc_tok_xyz",
            )
            await producer.enqueue(
                _draft(),
                tenant_id="org_acme",
                target_user_id="user_a",
                idempotency_key="approval-approval_001",
            )

        assert len(recorder.requests) == 1
        req = recorder.requests[0]
        assert req.url.path == "/internal/v1/inbox/items"
        assert req.headers["x-enterprise-service-token"] == "svc_tok_xyz"
        assert req.headers["x-enterprise-org-id"] == "org_acme"
        assert req.headers["x-enterprise-user-id"] == "user_a"
        assert req.headers["idempotency-key"] == "approval-approval_001"

    @pytest.mark.asyncio
    async def test_payload_carries_producer_id_and_external_ref(self) -> None:
        recorder = _RequestRecorder()
        transport = httpx.MockTransport(recorder.handler)
        async with httpx.AsyncClient(transport=transport) as client:
            producer = HttpInboxProducer(
                http_client=client,
                backend_url="https://backend.local",
                service_token="svc_tok",
            )
            await producer.enqueue(
                _draft(),
                tenant_id="org_acme",
                target_user_id="user_a",
                idempotency_key="approval-approval_001",
            )

        body = json.loads(recorder.requests[0].content)
        assert body["producer_id"] == "ai-backend"
        assert body["external_ref"] == "approval-approval_001"
        assert body["recipient_user_id"] == "user_a"
        assert body["kind"] == "approval_request"
        assert body["approval_id"] == "approval_001"
        assert body["thread_id"] == "conv_001"
        assert body["run_id"] == "run_001"
        assert body["sender_agent_id"] == "agent_atlas"
        assert body["sender_agent_name"] == "Atlas"

    @pytest.mark.asyncio
    async def test_idempotency_same_key_same_payload_external_ref(self) -> None:
        """Two enqueue calls with the same idempotency_key serialise the same external_ref."""
        recorder = _RequestRecorder()
        transport = httpx.MockTransport(recorder.handler)
        async with httpx.AsyncClient(transport=transport) as client:
            producer = HttpInboxProducer(
                http_client=client,
                backend_url="https://backend.local",
                service_token="svc_tok",
            )
            for _ in range(2):
                await producer.enqueue(
                    _draft(),
                    tenant_id="org_acme",
                    target_user_id="user_a",
                    idempotency_key="approval-approval_001",
                )
        bodies = [json.loads(req.content) for req in recorder.requests]
        # Same external_ref on every retry — backend's UNIQUE index
        # collapses duplicates into one row.
        assert {b["external_ref"] for b in bodies} == {"approval-approval_001"}

    @pytest.mark.asyncio
    async def test_empty_tenant_id_raises(self) -> None:
        recorder = _RequestRecorder()
        transport = httpx.MockTransport(recorder.handler)
        async with httpx.AsyncClient(transport=transport) as client:
            producer = HttpInboxProducer(
                http_client=client,
                backend_url="https://backend.local",
                service_token="svc_tok",
            )
            with pytest.raises(InboxProducerError, match="tenant_id"):
                await producer.enqueue(
                    _draft(),
                    tenant_id="",
                    target_user_id="user_a",
                    idempotency_key="approval-1",
                )
        assert recorder.requests == []

    @pytest.mark.asyncio
    async def test_empty_idempotency_key_raises(self) -> None:
        recorder = _RequestRecorder()
        transport = httpx.MockTransport(recorder.handler)
        async with httpx.AsyncClient(transport=transport) as client:
            producer = HttpInboxProducer(
                http_client=client,
                backend_url="https://backend.local",
                service_token="svc_tok",
            )
            with pytest.raises(InboxProducerError, match="idempotency_key"):
                await producer.enqueue(
                    _draft(),
                    tenant_id="org_a",
                    target_user_id="user_a",
                    idempotency_key="",
                )
        assert recorder.requests == []

    @pytest.mark.asyncio
    async def test_recipient_mismatch_raises(self) -> None:
        """target_user_id must match draft.recipient_user_id."""
        recorder = _RequestRecorder()
        transport = httpx.MockTransport(recorder.handler)
        async with httpx.AsyncClient(transport=transport) as client:
            producer = HttpInboxProducer(
                http_client=client,
                backend_url="https://backend.local",
                service_token="svc_tok",
            )
            with pytest.raises(InboxProducerError):
                await producer.enqueue(
                    _draft(),
                    tenant_id="org_a",
                    target_user_id="user_b",  # different from draft.recipient_user_id
                    idempotency_key="approval-1",
                )
        assert recorder.requests == []

    @pytest.mark.asyncio
    async def test_network_error_swallowed(self) -> None:
        """ConnectError is logged and absorbed; the runtime keeps going."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("network down")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            producer = HttpInboxProducer(
                http_client=client,
                backend_url="https://backend.local",
                service_token="svc_tok",
            )
            # Should not raise.
            await producer.enqueue(
                _draft(),
                tenant_id="org_a",
                target_user_id="user_a",
                idempotency_key="approval-1",
            )

    @pytest.mark.asyncio
    async def test_4xx_swallowed_and_logged(self) -> None:
        recorder = _RequestRecorder(status_code=409)
        transport = httpx.MockTransport(recorder.handler)
        async with httpx.AsyncClient(transport=transport) as client:
            producer = HttpInboxProducer(
                http_client=client,
                backend_url="https://backend.local",
                service_token="svc_tok",
            )
            await producer.enqueue(
                _draft(),
                tenant_id="org_a",
                target_user_id="user_a",
                idempotency_key="approval-1",
            )
        # No exception raised; the request was sent.
        assert len(recorder.requests) == 1


# ---------------------------------------------------------------------------
# NullInboxProducer
# ---------------------------------------------------------------------------


class TestNullInboxProducer:
    @pytest.mark.asyncio
    async def test_records_call(self) -> None:
        producer = NullInboxProducer()
        await producer.enqueue(
            _draft(),
            tenant_id="org_a",
            target_user_id="user_a",
            idempotency_key="approval-1",
        )
        assert len(producer.calls) == 1
        item, tenant, target, key = producer.calls[0]
        assert tenant == "org_a"
        assert target == "user_a"
        assert key == "approval-1"
        assert item.kind == "approval_request"

    @pytest.mark.asyncio
    async def test_requires_tenant_id(self) -> None:
        producer = NullInboxProducer()
        with pytest.raises(InboxProducerError):
            await producer.enqueue(
                _draft(),
                tenant_id="",
                target_user_id="user_a",
                idempotency_key="approval-1",
            )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestInboxProducerFactory:
    def test_null_when_unconfigured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("INBOX_PRODUCER_BACKEND_URL", raising=False)
        monkeypatch.delenv("BACKEND_BASE_URL", raising=False)
        monkeypatch.delenv("ENTERPRISE_SERVICE_TOKEN", raising=False)
        producer = InboxProducerFactory.default(http_client=None)
        assert isinstance(producer, NullInboxProducer)

    def test_null_when_client_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INBOX_PRODUCER_BACKEND_URL", "https://backend.local")
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "svc")
        producer = InboxProducerFactory.default(http_client=None)
        assert isinstance(producer, NullInboxProducer)

    @pytest.mark.asyncio
    async def test_http_when_env_and_client_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("INBOX_PRODUCER_BACKEND_URL", "https://backend.local")
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "svc")
        async with httpx.AsyncClient() as client:
            producer = InboxProducerFactory.default(http_client=client)
            assert isinstance(producer, HttpInboxProducer)

    @pytest.mark.asyncio
    async def test_falls_back_to_backend_base_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("INBOX_PRODUCER_BACKEND_URL", raising=False)
        monkeypatch.setenv("BACKEND_BASE_URL", "https://backend.local")
        monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "svc")
        async with httpx.AsyncClient() as client:
            producer = InboxProducerFactory.default(http_client=client)
            assert isinstance(producer, HttpInboxProducer)

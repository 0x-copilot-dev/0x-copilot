"""Cross-service inbox producer (ai-backend → backend).

The runtime writes a durable Inbox item by POSTing to the backend's internal
``/internal/v1/inbox/items`` endpoint when the in-surface inline approval does
not pull the user back inside ``INBOX_FALLBACK_INACTIVITY_MS`` (see
``docs/atlas-new-design/destinations/inbox-prd.md`` §3.5 + §9.1 binding revision).

This module follows the same Port + Http/Null + Factory shape as
``user_policies_resolver.py``:

* :class:`InboxProducerPort` — pure-protocol surface called from the runtime
  worker. Tests inject :class:`NullInboxProducer` (no-op) or a fake.
* :class:`HttpInboxProducer` — production impl. Sends the service token,
  ``x-enterprise-org-id``, ``x-enterprise-user-id`` headers on every POST.
  Idempotent on ``(producer_id, external_ref)`` — the backend deduplicates
  retries.
* :class:`NullInboxProducer` — no-op for tests + the trusted-backend lane
  being unconfigured.
* :class:`InboxProducerFactory` — env-driven selector.

The :class:`InboxItemDraft` pydantic contract mirrors §4.5's "ProducerInboxItem"
TypeScript shape so both sides validate against the same fields. Body text and
subject are **never** logged — only the idempotency key, kind, and HTTP outcome.
"""

from __future__ import annotations

import logging
import os
from typing import Literal, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, Field

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — environment, network, and producer identity
# ---------------------------------------------------------------------------


class _Env:
    """Environment variable names for backend URL and service-token configuration."""

    BACKEND_BASE_URL = "INBOX_PRODUCER_BACKEND_URL"
    # Falls back to BACKEND_BASE_URL when INBOX_PRODUCER_BACKEND_URL is unset
    # so a single deployment env var configures both lanes by default.
    BACKEND_BASE_URL_FALLBACK = "BACKEND_BASE_URL"
    SERVICE_TOKEN = "ENTERPRISE_SERVICE_TOKEN"


class _Headers:
    """Service-to-service header names for the trusted backend lane."""

    SERVICE_TOKEN = "x-enterprise-service-token"
    ORG = "x-enterprise-org-id"
    USER = "x-enterprise-user-id"
    IDEMPOTENCY = "idempotency-key"


class _Producer:
    """Producer identity sent in the payload so backend can scope idempotency."""

    ID = "ai-backend"


_POST_TIMEOUT_SECONDS = 5.0
_INTERNAL_INBOX_PATH = "/internal/v1/inbox/items"


# ---------------------------------------------------------------------------
# Draft contract (mirrors inbox-prd §4.5 ProducerInboxItem)
# ---------------------------------------------------------------------------


InboxItemKind = Literal["mention", "approval_request", "error", "system"]
InboxItemPriority = Literal["low", "med", "high"]


class InboxItemDraft(BaseModel):
    """Producer-side draft of an Inbox item.

    The pydantic schema validates inputs before they leave the runtime so the
    HTTP impl never serialises an under-specified item. Fields mirror §4.5's
    ProducerInboxItem on the wire; the backend assigns ``id``, ``created_at``,
    and ``updated_at``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    recipient_user_id: str = Field(min_length=1)
    kind: InboxItemKind
    subject: str = Field(min_length=1, max_length=200)
    preview: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)

    # Optional sender identity. The backend fills sender_kind from the
    # calling agent's claims; these fields denormalise display data.
    sender_agent_id: str | None = None
    sender_agent_name: str | None = None

    # Optional refs into other product domains. ``approval_id`` is present
    # iff kind == approval_request (cross-audit §9.1 routing rule).
    thread_id: str | None = None
    run_id: str | None = None
    approval_id: str | None = None
    project_id: str | None = None

    priority: InboxItemPriority = "med"
    labels: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class InboxProducerError(RuntimeError):
    """Raised by the producer when a required input is missing or rejected.

    Network and HTTP errors do NOT raise — they are logged and swallowed so a
    transient backend outage never kills the runtime worker. Only programmer
    errors (missing tenant id) raise.
    """


# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------


@runtime_checkable
class InboxProducerPort(Protocol):
    """Port for posting Inbox items to the backend.

    Implementations MUST be idempotent on ``idempotency_key`` — the backend
    enforces ``(producer_id, external_ref)`` uniqueness, but the producer
    should also short-circuit duplicate enqueue calls within a single process
    where possible.
    """

    async def enqueue(
        self,
        item: InboxItemDraft,
        *,
        tenant_id: str,
        target_user_id: str,
        idempotency_key: str,
    ) -> None:
        """Send the draft to the backend; raise ``InboxProducerError`` on bad input."""


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------


class HttpInboxProducer:
    """Production impl that POSTs to the backend's internal inbox endpoint.

    Network and HTTP errors are logged and absorbed; programmer errors (empty
    tenant id, mismatched recipient) raise :class:`InboxProducerError`. The
    injected ``httpx.AsyncClient`` lifecycle is owned by the caller.
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        backend_url: str,
        service_token: str,
    ) -> None:
        self._client = http_client
        self._backend_url = backend_url.rstrip("/")
        self._service_token = service_token

    async def enqueue(
        self,
        item: InboxItemDraft,
        *,
        tenant_id: str,
        target_user_id: str,
        idempotency_key: str,
    ) -> None:
        """POST the draft to backend with service-token + tenant headers."""
        if not tenant_id.strip():
            raise InboxProducerError("tenant_id is required for inbox enqueue")
        if not target_user_id.strip():
            raise InboxProducerError("target_user_id is required for inbox enqueue")
        if not idempotency_key.strip():
            raise InboxProducerError("idempotency_key is required for inbox enqueue")
        if item.recipient_user_id != target_user_id:
            raise InboxProducerError(
                "recipient_user_id in draft does not match target_user_id"
            )
        payload = self._serialise(item, idempotency_key=idempotency_key)
        try:
            response = await self._client.post(
                f"{self._backend_url}{_INTERNAL_INBOX_PATH}",
                json=payload,
                headers={
                    _Headers.SERVICE_TOKEN: self._service_token,
                    _Headers.ORG: tenant_id,
                    _Headers.USER: target_user_id,
                    _Headers.IDEMPOTENCY: idempotency_key,
                },
                timeout=_POST_TIMEOUT_SECONDS,
            )
        except (
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
        ) as exc:
            # PII discipline: never log subject/preview/body. Idempotency key
            # is a derived id (e.g., ``approval-<uuid>``) and is safe.
            _LOGGER.warning(
                "inbox_producer.fetch_failed",
                extra={
                    "metadata": {
                        "tenant_id": tenant_id,
                        "target_user_id": target_user_id,
                        "idempotency_key": idempotency_key,
                        "kind": item.kind,
                        "error_class": exc.__class__.__name__,
                    }
                },
            )
            return
        if response.status_code >= 400:
            _LOGGER.warning(
                "inbox_producer.non_2xx",
                extra={
                    "metadata": {
                        "tenant_id": tenant_id,
                        "target_user_id": target_user_id,
                        "idempotency_key": idempotency_key,
                        "kind": item.kind,
                        "status_code": response.status_code,
                    }
                },
            )

    @staticmethod
    def _serialise(
        item: InboxItemDraft,
        *,
        idempotency_key: str,
    ) -> dict[str, object]:
        """Project the draft onto the §4.5 ProducerInboxItem wire shape."""
        payload: dict[str, object] = {
            "recipient_user_id": item.recipient_user_id,
            "kind": item.kind,
            "subject": item.subject,
            "preview": item.preview,
            "body": item.body,
            "priority": item.priority,
            "labels": list(item.labels),
            "producer_id": _Producer.ID,
            "external_ref": idempotency_key,
        }
        for field in (
            "sender_agent_id",
            "sender_agent_name",
            "thread_id",
            "run_id",
            "approval_id",
            "project_id",
        ):
            value = getattr(item, field)
            if value is not None:
                payload[field] = value
        return payload


class NullInboxProducer:
    """No-op producer used when the backend lane is unconfigured or in tests.

    Records every enqueue call on ``calls`` so unit tests can assert that the
    runtime did (or did not) request a fallback row without standing up a
    real HTTP server.
    """

    def __init__(self) -> None:
        self.calls: list[
            tuple[InboxItemDraft, str, str, str]
        ] = []  # (item, tenant_id, target_user_id, idempotency_key)

    async def enqueue(
        self,
        item: InboxItemDraft,
        *,
        tenant_id: str,
        target_user_id: str,
        idempotency_key: str,
    ) -> None:
        """Record the call; raise if structural invariants are violated."""
        if not tenant_id.strip():
            raise InboxProducerError("tenant_id is required for inbox enqueue")
        if not target_user_id.strip():
            raise InboxProducerError("target_user_id is required for inbox enqueue")
        if not idempotency_key.strip():
            raise InboxProducerError("idempotency_key is required for inbox enqueue")
        self.calls.append((item, tenant_id, target_user_id, idempotency_key))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class InboxProducerFactory:
    """Select the appropriate producer from environment configuration.

    Returns :class:`NullInboxProducer` when ``INBOX_PRODUCER_BACKEND_URL``
    (or its fallback ``BACKEND_BASE_URL``), ``ENTERPRISE_SERVICE_TOKEN``, or
    the injected ``http_client`` is missing. Callers always get a working
    producer regardless of deployment.
    """

    @classmethod
    def default(
        cls,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> InboxProducerPort:
        """Return the best available producer for the current environment."""
        backend_url = (
            os.environ.get(_Env.BACKEND_BASE_URL, "").strip()
            or os.environ.get(_Env.BACKEND_BASE_URL_FALLBACK, "").strip()
        )
        service_token = os.environ.get(_Env.SERVICE_TOKEN, "").strip()
        if not backend_url or not service_token or http_client is None:
            return NullInboxProducer()
        return HttpInboxProducer(
            http_client=http_client,
            backend_url=backend_url,
            service_token=service_token,
        )


__all__ = [
    "HttpInboxProducer",
    "InboxItemDraft",
    "InboxItemKind",
    "InboxItemPriority",
    "InboxProducerError",
    "InboxProducerFactory",
    "InboxProducerPort",
    "NullInboxProducer",
]

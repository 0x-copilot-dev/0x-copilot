"""Application service for todo-extraction proposals (P3-A2 HTTP surface).

Three routes are exposed via :mod:`runtime_api.http.todo_extractions`:

- ``GET    /v1/todo-extractions``            — list pending for caller
- ``POST   /v1/todo-extractions/{id}/accept`` — accept one proposal
- ``POST   /v1/todo-extractions/{id}/reject`` — reject one proposal

Accept calls into the backend service's public ``POST /v1/todos`` endpoint
through the internal service-token path. P3-A1 owns the destination shape
on the backend side; this service translates a proposal into the same
``Todo`` create request the user would have sent themselves and forwards
the result.

Tenant isolation:

- Every read predicates on ``(org_id, owner_user_id)``; passing a
  different ``owner_user_id`` than the authenticated caller raises 404
  (not 403, to avoid leaking row existence).
- Accept/reject are owner-only — only the proposal's ``owner_user_id``
  may transition it.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

import httpx
from copilot_service_contracts import ORG_HEADER, SERVICE_TOKEN_HEADER, USER_HEADER

from agent_runtime.persistence.records import (
    TodoExtractionRecord,
    TodoExtractionState,
)


_LOGGER = logging.getLogger(__name__)


class TodoExtractionApiError(Exception):
    """Typed error carrying a safe public message + HTTP status hint."""

    def __init__(self, *, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class _Messages:
    """Public, scrub-safe error strings."""

    NOT_FOUND = "Extraction proposal was not found for this scope."
    NOT_PENDING = "Extraction proposal has already been resolved."
    BACKEND_UNAVAILABLE = "Todos backend is temporarily unavailable."
    BACKEND_BAD_RESPONSE = "Todos backend returned an unexpected response."
    SERVICE_TOKEN_MISSING = (
        "Internal service token is not configured; accept is unavailable."
    )


class _Defaults:
    """Default config values + env var names for the backend client."""

    BACKEND_BASE_URL_ENV = "BACKEND_BASE_URL"
    DEFAULT_BACKEND_BASE_URL = "http://127.0.0.1:8100"
    DEFAULT_TIMEOUT_SECONDS = 5.0
    DEFAULT_LIST_LIMIT = 50
    MAX_LIST_LIMIT = 200


@runtime_checkable
class _ExtractionStore(Protocol):
    """Subset of :class:`TodoExtractionStorePort` we actually depend on."""

    async def get_by_id(
        self, *, org_id: str, extraction_id: str
    ) -> TodoExtractionRecord | None: ...

    async def list_pending(
        self, *, org_id: str, owner_user_id: str, limit: int
    ) -> Sequence[TodoExtractionRecord]: ...

    async def update_state(
        self,
        *,
        org_id: str,
        extraction_id: str,
        state: TodoExtractionState,
        resolved_at: datetime,
    ) -> TodoExtractionRecord | None: ...


@dataclass(frozen=True)
class AcceptedTodo:
    """Shape returned to the frontend after a successful accept."""

    extraction_id: str
    backend_todo: dict[str, object]


class TodoExtractionsService:
    """Read + accept/reject orchestration for proposals."""

    def __init__(
        self,
        *,
        store: _ExtractionStore,
        http_client: httpx.AsyncClient | None = None,
        backend_base_url: str | None = None,
        clock: callable | None = None,
    ) -> None:
        self._store = store
        self._http = http_client
        self._backend_base_url = (
            backend_base_url
            or os.environ.get(_Defaults.BACKEND_BASE_URL_ENV)
            or _Defaults.DEFAULT_BACKEND_BASE_URL
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def list_pending(
        self,
        *,
        org_id: str,
        owner_user_id: str,
        limit: int | None,
    ) -> Sequence[TodoExtractionRecord]:
        """Owner-scoped pending list, bounded by ``MAX_LIST_LIMIT``."""
        effective = (
            limit
            if isinstance(limit, int) and limit > 0
            else _Defaults.DEFAULT_LIST_LIMIT
        )
        effective = min(effective, _Defaults.MAX_LIST_LIMIT)
        return await self._store.list_pending(
            org_id=org_id, owner_user_id=owner_user_id, limit=effective
        )

    async def accept(
        self,
        *,
        org_id: str,
        owner_user_id: str,
        extraction_id: str,
    ) -> AcceptedTodo:
        """Accept a proposal: write to backend todos, then transition state.

        Order matters: a successful backend insert without the state
        transition is recoverable (the proposal stays pending; user
        re-accepts and the backend's idempotency catches the double).
        A successful state transition without the backend insert would
        lose the todo silently.
        """
        record = await self._load_pending(
            org_id=org_id, owner_user_id=owner_user_id, extraction_id=extraction_id
        )
        backend_todo = await self._post_to_backend(record=record)
        await self._store.update_state(
            org_id=org_id,
            extraction_id=record.id,
            state=TodoExtractionState.ACCEPTED,
            resolved_at=self._clock(),
        )
        return AcceptedTodo(extraction_id=record.id, backend_todo=backend_todo)

    async def reject(
        self,
        *,
        org_id: str,
        owner_user_id: str,
        extraction_id: str,
    ) -> TodoExtractionRecord:
        """Transition a pending proposal to ``rejected``."""
        record = await self._load_pending(
            org_id=org_id, owner_user_id=owner_user_id, extraction_id=extraction_id
        )
        updated = await self._store.update_state(
            org_id=org_id,
            extraction_id=record.id,
            state=TodoExtractionState.REJECTED,
            resolved_at=self._clock(),
        )
        # update_state returning ``None`` here would mean the row vanished
        # between load and update — a vanishingly rare race. Surface as
        # not-found rather than 500.
        if updated is None:
            raise TodoExtractionApiError(status_code=404, message=_Messages.NOT_FOUND)
        return updated

    # -- helpers -----------------------------------------------------------

    async def _load_pending(
        self, *, org_id: str, owner_user_id: str, extraction_id: str
    ) -> TodoExtractionRecord:
        record = await self._store.get_by_id(org_id=org_id, extraction_id=extraction_id)
        # Collapse ownership failures and missing-row failures to the
        # same response so the row's existence is not enumerable from
        # outside the tenant.
        if record is None or record.owner_user_id != owner_user_id:
            raise TodoExtractionApiError(status_code=404, message=_Messages.NOT_FOUND)
        if record.state != TodoExtractionState.PENDING:
            raise TodoExtractionApiError(status_code=409, message=_Messages.NOT_PENDING)
        return record

    async def _post_to_backend(
        self, *, record: TodoExtractionRecord
    ) -> dict[str, object]:
        """Forward the proposal to backend ``POST /v1/todos`` via service token."""
        service_token = os.environ.get("ENTERPRISE_SERVICE_TOKEN", "").strip()
        if not service_token:
            raise TodoExtractionApiError(
                status_code=503, message=_Messages.SERVICE_TOKEN_MISSING
            )
        headers = {
            SERVICE_TOKEN_HEADER: service_token,
            ORG_HEADER: record.org_id,
            USER_HEADER: record.owner_user_id,
            "content-type": "application/json",
        }
        payload: dict[str, object] = {
            "text": record.proposed_text,
            "source": {
                "kind": "chat",
                "thread_id": record.conversation_id,
                "run_id": record.run_id,
            },
        }
        if record.suggested_due:
            payload["due"] = record.suggested_due
        if record.suggested_project_id:
            payload["project_id"] = record.suggested_project_id

        client = self._http or httpx.AsyncClient(
            timeout=_Defaults.DEFAULT_TIMEOUT_SECONDS
        )
        owns_client = self._http is None
        try:
            response = await client.post(
                f"{self._backend_base_url.rstrip('/')}/v1/todos",
                json=payload,
                headers=headers,
            )
        except httpx.HTTPError:
            _LOGGER.warning(
                "todo_accept_backend_unreachable",
                extra={"metadata": {"extraction_id": record.id}},
                exc_info=True,
            )
            raise TodoExtractionApiError(
                status_code=502, message=_Messages.BACKEND_UNAVAILABLE
            ) from None
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code >= 500:
            raise TodoExtractionApiError(
                status_code=502, message=_Messages.BACKEND_UNAVAILABLE
            )
        if response.status_code >= 400:
            # 4xx from backend (e.g. invalid project_id) — bubble up the
            # status so the caller learns the actual reason. We do NOT
            # forward the response body; the backend may include detail
            # that isn't sanitized for this surface.
            raise TodoExtractionApiError(
                status_code=response.status_code,
                message=_Messages.BACKEND_BAD_RESPONSE,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise TodoExtractionApiError(
                status_code=502, message=_Messages.BACKEND_BAD_RESPONSE
            ) from exc

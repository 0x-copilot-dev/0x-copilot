"""Cross-service client for Routines internal endpoints owned by ``backend``.

Mirrors the cross-service port pattern established by
``user_policies_resolver.py`` and the materialize-due client in
``runtime_worker.jobs.todo_recurrence_materializer``: a ``Protocol`` plus
``Http``/``Null`` impls and a factory that picks one based on environment.

The scheduler (``routine_scheduler.py``) never imports backend persistence
directly — every read or write goes through this client, satisfying the
service-boundary rule in the root ``CLAUDE.md``.

Expected backend internal endpoints (P5-A1 contract — see report):

* ``POST   /internal/v1/routines/claim``            — claim due routines via
  ``FOR UPDATE SKIP LOCKED``. Body ``{as_of, limit}``. Returns up to ``limit``
  claims; each claim carries the ``routine_id``, ``tenant_id``, ``owner_user_id``,
  ``project_id``, ``next_fire_at`` (the **fire instant** for this claim — used as
  the idempotency key together with ``routine_id``), the routine's ``triggers``
  with ``kind`` + ``config`` (cron + tz), the ``missed_fire_policy``, and the
  ``last_fire_at`` (nullable — used to drive the backlog calculation for
  ``fire_all`` / ``skip``).
* ``POST   /internal/v1/routines/{routine_id}/fires`` — record a fire (status =
  ``queued``/``running``/``succeeded``/``failed``/``skipped``). Body:
  ``{fire_at, trigger_kind, run_id, status, skip_reason?}``. UNIQUE
  ``(routine_id, fire_at)`` makes the call idempotent.
* ``POST   /internal/v1/routines/{routine_id}/advance`` — advance ``next_fire_at``
  on the routines row. Body ``{next_fire_at}``. Idempotent (last-writer wins;
  the scheduler computes the next instant deterministically from the cron).

This client never raises. Network/HTTP errors return an empty / no-op
outcome — the scheduler skips the tick and re-polls. Idempotency lives on
the backend's ``(routine_id, fire_at)`` UNIQUE constraint, so duplicate fires
from a retried tick are impossible regardless of client behaviour.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, field_validator


_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — environment + network
# ---------------------------------------------------------------------------


class _Env:
    """Environment variable names for backend URL and service-token configuration."""

    BACKEND_BASE_URL = "BACKEND_BASE_URL"
    SERVICE_TOKEN = "ENTERPRISE_SERVICE_TOKEN"


class _Headers:
    """Service-to-service header names for the trusted backend lane."""

    SERVICE_TOKEN = "x-enterprise-service-token"
    ORG = "x-enterprise-org-id"
    USER = "x-enterprise-user-id"


_FETCH_TIMEOUT_SECONDS = 10.0


# ---------------------------------------------------------------------------
# Domain types — claims + outcomes
# ---------------------------------------------------------------------------


class RoutineTrigger(BaseModel):
    """Trigger sub-record returned with a claim.

    ``kind`` is one of ``schedule`` / ``webhook`` / ``event`` / ``manual`` per
    routines-prd.md §3.6. The scheduler only acts on ``schedule`` triggers
    (cron-driven); other kinds are present so the client surface is forward-
    compatible without a separate fetch.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")
    trigger_id: str
    kind: str
    cron: str | None = None
    tz: str | None = None


class RoutineFireClaim(BaseModel):
    """A single claimed routine, ready to be fired this tick.

    ``fire_at`` is the **scheduled fire instant** for this claim — the value the
    backend used to select the row (``next_fire_at`` at claim time) and the
    idempotency key paired with ``routine_id`` on the backend's UNIQUE constraint.

    ``last_fire_at`` is nullable and drives the missed-fire calculation: if it
    is ``None`` the routine has never fired and there is no backlog.

    The agent reference is intentionally returned as ``base_agent_id`` (loose
    FK) **not** a snapshotted Agent record — the scheduler re-resolves the
    live agent at fire time per cross-audit §9.7 Q11.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")
    routine_id: str
    tenant_id: str
    owner_user_id: str
    project_id: str | None = None
    base_agent_id: str | None = None
    fire_at: datetime
    last_fire_at: datetime | None = None
    missed_fire_policy: str = "fire_once"
    triggers: tuple[RoutineTrigger, ...] = ()

    @field_validator("missed_fire_policy")
    @classmethod
    def _policy_supported(cls, value: str) -> str:
        if value not in ("fire_once", "fire_all", "skip"):
            raise ValueError(
                f"missed_fire_policy must be one of "
                f"'fire_once' / 'fire_all' / 'skip', got '{value}'"
            )
        return value


class ClaimDueOutcome(BaseModel):
    """Aggregate result of a ``claim_due_routines`` call."""

    model_config = ConfigDict(frozen=True)
    claims: tuple[RoutineFireClaim, ...] = ()


class RecordFireOutcome(BaseModel):
    """Aggregate result of a ``record_fire`` call.

    ``accepted`` is ``True`` when the backend wrote the fire record;
    ``False`` when the ``(routine_id, fire_at)`` UNIQUE constraint rejected
    the insert (someone else already fired this slot). ``fire_id`` is non-
    empty on accepted writes.
    """

    model_config = ConfigDict(frozen=True)
    accepted: bool = False
    fire_id: str = ""
    duplicate: bool = False


# ---------------------------------------------------------------------------
# Port
# ---------------------------------------------------------------------------


@runtime_checkable
class RoutineBackendClient(Protocol):
    """Port for routine-scheduler ↔ backend communication.

    Implementations must never raise: a network or HTTP error returns an
    empty / no-op outcome and the scheduler skips the tick.
    """

    async def claim_due_routines(self, *, now: datetime, limit: int) -> ClaimDueOutcome:
        """Claim up to ``limit`` routines whose ``next_fire_at <= now``."""

    async def record_fire(
        self,
        *,
        claim: RoutineFireClaim,
        run_id: str,
        status: str,
        skip_reason: str | None = None,
    ) -> RecordFireOutcome:
        """Record a fire on ``(routine_id, fire_at)`` (idempotent)."""

    async def advance_next_fire(
        self,
        *,
        routine_id: str,
        tenant_id: str,
        next_fire_at: datetime,
    ) -> bool:
        """Advance ``routines.next_fire_at`` after the scheduler computed it."""


# ---------------------------------------------------------------------------
# HTTP impl
# ---------------------------------------------------------------------------


class HttpRoutineBackendClient:
    """Production client speaking to backend's ``/internal/v1/routines/*`` endpoints."""

    PATH_CLAIM = "/internal/v1/routines/claim"
    PATH_FIRES_TEMPLATE = "/internal/v1/routines/{routine_id}/fires"
    PATH_ADVANCE_TEMPLATE = "/internal/v1/routines/{routine_id}/advance"

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

    # ---- claim ------------------------------------------------------------

    async def claim_due_routines(self, *, now: datetime, limit: int) -> ClaimDueOutcome:
        body = {
            "as_of": now.astimezone(timezone.utc).isoformat(),
            "limit": limit,
        }
        try:
            response = await self._client.post(
                f"{self._backend_url}{self.PATH_CLAIM}",
                json=body,
                headers=self._system_headers(),
                timeout=_FETCH_TIMEOUT_SECONDS,
            )
        except (
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
        ) as exc:
            _LOGGER.warning(
                "routines.claim_fetch_failed",
                extra={"metadata": {"error_class": exc.__class__.__name__}},
            )
            return ClaimDueOutcome()
        if response.status_code >= 400:
            _LOGGER.warning(
                "routines.claim_non_2xx",
                extra={"metadata": {"status_code": response.status_code}},
            )
            return ClaimDueOutcome()
        try:
            body_json = response.json()
        except ValueError:
            return ClaimDueOutcome()
        if not isinstance(body_json, dict):
            return ClaimDueOutcome()
        raw_claims = body_json.get("claims", [])
        if not isinstance(raw_claims, list):
            return ClaimDueOutcome()
        parsed: list[RoutineFireClaim] = []
        for raw in raw_claims:
            if not isinstance(raw, dict):
                continue
            try:
                parsed.append(RoutineFireClaim(**raw))
            except Exception:
                # Refuse silently for one malformed entry; keep the rest.
                _LOGGER.warning(
                    "routines.claim_bad_entry",
                    extra={"metadata": {"keys": sorted(raw.keys())}},
                )
                continue
        return ClaimDueOutcome(claims=tuple(parsed))

    # ---- record_fire ------------------------------------------------------

    async def record_fire(
        self,
        *,
        claim: RoutineFireClaim,
        run_id: str,
        status: str,
        skip_reason: str | None = None,
    ) -> RecordFireOutcome:
        path = self.PATH_FIRES_TEMPLATE.format(routine_id=claim.routine_id)
        body: dict[str, object] = {
            "fire_at": claim.fire_at.astimezone(timezone.utc).isoformat(),
            "trigger_kind": "schedule",
            "run_id": run_id,
            "status": status,
        }
        if skip_reason is not None:
            body["skip_reason"] = skip_reason
        try:
            response = await self._client.post(
                f"{self._backend_url}{path}",
                json=body,
                headers=self._tenant_headers(claim),
                timeout=_FETCH_TIMEOUT_SECONDS,
            )
        except (
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
        ) as exc:
            _LOGGER.warning(
                "routines.record_fire_fetch_failed",
                extra={"metadata": {"error_class": exc.__class__.__name__}},
            )
            return RecordFireOutcome()
        # 409 = UNIQUE constraint hit (duplicate fire); not an error — return
        # ``duplicate=True`` so the caller treats it as "someone else fired".
        if response.status_code == 409:
            return RecordFireOutcome(accepted=False, duplicate=True)
        if response.status_code >= 400:
            _LOGGER.warning(
                "routines.record_fire_non_2xx",
                extra={"metadata": {"status_code": response.status_code}},
            )
            return RecordFireOutcome()
        try:
            body_json = response.json()
        except ValueError:
            return RecordFireOutcome()
        if not isinstance(body_json, dict):
            return RecordFireOutcome()
        fire_id = body_json.get("fire_id", "")
        if not isinstance(fire_id, str):
            fire_id = ""
        return RecordFireOutcome(accepted=True, fire_id=fire_id)

    # ---- advance ----------------------------------------------------------

    async def advance_next_fire(
        self,
        *,
        routine_id: str,
        tenant_id: str,
        next_fire_at: datetime,
    ) -> bool:
        path = self.PATH_ADVANCE_TEMPLATE.format(routine_id=routine_id)
        body = {
            "next_fire_at": next_fire_at.astimezone(timezone.utc).isoformat(),
        }
        try:
            response = await self._client.post(
                f"{self._backend_url}{path}",
                json=body,
                headers={
                    _Headers.SERVICE_TOKEN: self._service_token,
                    _Headers.ORG: tenant_id,
                    _Headers.USER: "system",
                },
                timeout=_FETCH_TIMEOUT_SECONDS,
            )
        except (
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
        ) as exc:
            _LOGGER.warning(
                "routines.advance_fetch_failed",
                extra={"metadata": {"error_class": exc.__class__.__name__}},
            )
            return False
        if response.status_code >= 400:
            _LOGGER.warning(
                "routines.advance_non_2xx",
                extra={"metadata": {"status_code": response.status_code}},
            )
            return False
        return True

    # ---- headers helpers --------------------------------------------------

    def _system_headers(self) -> dict[str, str]:
        """Headers for cross-tenant scheduler operations (claim fan-out).

        Claim is a system-level operation: per-row ``tenant_id`` lives on
        each routine. We send ``"system"`` so the audit shows ``actor=system``
        and the auth gate (service-token + org/user headers per CLAUDE.md)
        is satisfied.
        """
        return {
            _Headers.SERVICE_TOKEN: self._service_token,
            _Headers.ORG: "system",
            _Headers.USER: "system",
        }

    def _tenant_headers(self, claim: RoutineFireClaim) -> dict[str, str]:
        """Tenant-scoped headers for per-routine writes.

        For per-row writes we forward the routine's ``tenant_id`` + ``owner_user_id``
        so backend audit attribution lands on the routine's owner ("actor=system,
        on_behalf_of=<owner>").
        """
        return {
            _Headers.SERVICE_TOKEN: self._service_token,
            _Headers.ORG: claim.tenant_id,
            _Headers.USER: claim.owner_user_id,
        }


# ---------------------------------------------------------------------------
# Null impl
# ---------------------------------------------------------------------------


class NullRoutineBackendClient:
    """No-op client used when the trusted-backend lane is not configured."""

    async def claim_due_routines(self, *, now: datetime, limit: int) -> ClaimDueOutcome:
        """Return an empty claim list unconditionally."""
        return ClaimDueOutcome()

    async def record_fire(
        self,
        *,
        claim: RoutineFireClaim,
        run_id: str,
        status: str,
        skip_reason: str | None = None,
    ) -> RecordFireOutcome:
        """Return a no-op outcome unconditionally."""
        return RecordFireOutcome()

    async def advance_next_fire(
        self,
        *,
        routine_id: str,
        tenant_id: str,
        next_fire_at: datetime,
    ) -> bool:
        """Return ``False`` unconditionally (no advance happened)."""
        return False


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class RoutineBackendClientFactory:
    """Pick the appropriate client from environment configuration."""

    @classmethod
    def default(
        cls,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> RoutineBackendClient:
        """Return an HTTP client when env is set, else the null client."""
        backend_url = os.environ.get(_Env.BACKEND_BASE_URL, "").strip()
        service_token = os.environ.get(_Env.SERVICE_TOKEN, "").strip()
        if not backend_url or not service_token or http_client is None:
            return NullRoutineBackendClient()
        return HttpRoutineBackendClient(
            http_client=http_client,
            backend_url=backend_url,
            service_token=service_token,
        )


__all__ = [
    "ClaimDueOutcome",
    "HttpRoutineBackendClient",
    "NullRoutineBackendClient",
    "RecordFireOutcome",
    "RoutineBackendClient",
    "RoutineBackendClientFactory",
    "RoutineFireClaim",
    "RoutineTrigger",
]

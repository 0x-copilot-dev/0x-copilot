"""Authorized application service for legal-hold lifecycle management.

This is intentionally a narrow control plane: it resolves only the runtime's
known org/user/conversation ownership graph and hands a canonical record to the
persistence adapter.  It does not expose a generic ``resource_id`` or accept
caller-supplied tenant identity.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from agent_runtime.persistence.records import (
    AuditActorType,
    AuditOutcome,
    LegalHoldConflict,
    LegalHoldRecord,
    LegalHoldScope,
)
from agent_runtime.api.legal_hold_contracts import (
    LegalHoldCreateRequest,
    LegalHoldReleaseRequest,
    LegalHoldView,
)


class LegalHoldNotFoundError(RuntimeError):
    """A target or hold is absent from the caller's tenant-scoped graph."""


class LegalHoldService:
    """Identity-scoped hold lifecycle with closed target resolution."""

    def __init__(self, persistence: object) -> None:
        self._persistence = persistence

    async def create(
        self,
        *,
        org_id: str,
        actor_user_id: str,
        request: LegalHoldCreateRequest,
        idempotency_key: str,
    ) -> LegalHoldView:
        scope, resource_id, subject_user_id = await self._resolve_target(
            org_id=org_id,
            request=request,
        )
        digest = self._digest(
            {
                "op": "create",
                "scope": scope.value,
                "resource_id": resource_id,
                "subject_user_id": subject_user_id,
                "reason_code": request.reason_code.value,
            }
        )
        now = datetime.now(timezone.utc)
        record = LegalHoldRecord(
            org_id=org_id,
            scope=scope,
            resource_id=resource_id,
            subject_user_id=subject_user_id,
            reason_code=request.reason_code,
            created_by_user_id=actor_user_id,
            created_at=now,
            create_idempotency_key=idempotency_key,
            create_request_digest=digest,
        )
        result = await self._persistence.create_legal_hold(
            record=record,
            audit_event=self._audit_event(
                action="legal_hold.created",
                hold=record,
                actor_user_id=actor_user_id,
                revision=record.revision,
            ),
        )
        return self._view(result.hold, replayed=result.replayed)

    async def list(
        self,
        *,
        org_id: str,
        actor_user_id: str,
        include_released: bool,
        limit: int,
    ) -> tuple[LegalHoldView, ...]:
        # Audit the access before returning data.  The record is intentionally
        # bounded to request controls only: it contains no hold ids, target
        # identifiers, result counts, or raw query payload.
        await self._persistence.write_audit_log(
            event_type="legal_hold.accessed",
            record={
                "org_id": org_id,
                "user_id": actor_user_id,
                "actor_type": AuditActorType.USER.value,
                "action": "legal_hold.accessed",
                "resource_type": "legal_hold_collection",
                "resource_id": "tenant_collection",
                "outcome": AuditOutcome.SUCCESS.value,
                "metadata": {
                    "include_released": include_released,
                    "limit": limit,
                },
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        rows = await self._persistence.list_legal_holds(
            org_id=org_id,
            include_released=include_released,
            limit=limit,
        )
        return tuple(self._view(row, replayed=False) for row in rows)

    async def release(
        self,
        *,
        org_id: str,
        actor_user_id: str,
        hold_id: str,
        request: LegalHoldReleaseRequest,
        idempotency_key: str,
    ) -> LegalHoldView:
        now = datetime.now(timezone.utc)
        digest = self._digest(
            {
                "op": "release",
                "hold_id": hold_id,
                "expected_revision": request.expected_revision,
            }
        )
        # This intentionally contains no target ids or free-form rationale.
        audit_event = {
            "org_id": org_id,
            "user_id": actor_user_id,
            "actor_type": AuditActorType.USER.value,
            "action": "legal_hold.released",
            "resource_type": "legal_hold",
            "resource_id": hold_id,
            "outcome": AuditOutcome.SUCCESS.value,
            "metadata": {"expected_revision": request.expected_revision},
            "created_at": now.isoformat(),
        }
        result = await self._persistence.release_legal_hold(
            org_id=org_id,
            hold_id=hold_id,
            expected_revision=request.expected_revision,
            released_by_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            request_digest=digest,
            released_at=now,
            audit_event=audit_event,
        )
        if result is None:
            raise LegalHoldNotFoundError()
        return self._view(result.hold, replayed=result.replayed)

    async def _resolve_target(
        self,
        *,
        org_id: str,
        request: LegalHoldCreateRequest,
    ) -> tuple[LegalHoldScope, str, str | None]:
        if request.scope is LegalHoldScope.ORG:
            return LegalHoldScope.ORG, org_id, None
        if request.scope is LegalHoldScope.USER:
            target_user_id = request.target_user_id
            if (
                target_user_id is None
                or not await self._persistence.has_legal_hold_subject(
                    org_id=org_id, user_id=target_user_id
                )
            ):
                raise LegalHoldNotFoundError()
            return LegalHoldScope.USER, target_user_id, target_user_id
        conversation_id = request.target_conversation_id
        if conversation_id is None:
            raise LegalHoldNotFoundError()
        conversation = await self._persistence.get_conversation_for_org(
            org_id=org_id,
            conversation_id=conversation_id,
        )
        if conversation is None:
            raise LegalHoldNotFoundError()
        return LegalHoldScope.CONVERSATION, conversation_id, conversation.user_id

    @staticmethod
    def _digest(value: dict[str, object]) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _audit_event(
        *,
        action: str,
        hold: LegalHoldRecord,
        actor_user_id: str,
        revision: int,
    ) -> dict[str, object]:
        # Never store the resource identifier or a request payload in audit
        # metadata. The audited resource id is the opaque hold id only.
        return {
            "org_id": hold.org_id,
            "user_id": actor_user_id,
            "actor_type": AuditActorType.USER.value,
            "action": action,
            "resource_type": "legal_hold",
            "resource_id": hold.id,
            "outcome": AuditOutcome.SUCCESS.value,
            "metadata": {
                "scope": hold.scope.value,
                "reason_code": hold.reason_code.value,
                "revision": revision,
            },
            "created_at": hold.created_at.isoformat(),
        }

    @staticmethod
    def _view(hold: LegalHoldRecord, *, replayed: bool) -> LegalHoldView:
        return LegalHoldView(
            id=hold.id,
            scope=hold.scope,
            target_user_id=(
                hold.resource_id if hold.scope is LegalHoldScope.USER else None
            ),
            target_conversation_id=(
                hold.resource_id if hold.scope is LegalHoldScope.CONVERSATION else None
            ),
            reason_code=hold.reason_code,
            created_by_user_id=hold.created_by_user_id,
            created_at=hold.created_at,
            released_by_user_id=hold.released_by_user_id,
            released_at=hold.released_at,
            revision=hold.revision,
            replayed=replayed,
        )


__all__ = (
    "LegalHoldConflict",
    "LegalHoldNotFoundError",
    "LegalHoldService",
)

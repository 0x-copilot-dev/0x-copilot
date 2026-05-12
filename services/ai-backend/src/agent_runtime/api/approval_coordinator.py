"""Approval lifecycle coordinator (P22 / PR 1).

Owns: ``list_assigned_approvals`` (inbox read), ``record_approval_decision``,
``request_approval_undo``. Single source of truth for approval-state
transitions.

Approvals cover both human-in-the-loop decisions and MCP auth resolution.
Multi-fire safe — token rotation mid-run fires the same cycle again. Resume
happens via a separate ``APPROVAL_RESOLVED`` queue command, not inline.

PR 1 of the P22 split (see ``docs/refactor/19-runtime-api-service-split.md``)
ships this as a thin forwarder onto :class:`RuntimeApiService`. PR 4 will move
the method bodies here and reduce the legacy class to a 1-line delegator. PR 5
deletes the legacy class.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from runtime_api.schemas import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalStatus,
    ApprovalUndoResponse,
    AssignedApprovalsResponse,
)

if TYPE_CHECKING:
    from agent_runtime.api.service import RuntimeApiService


class ApprovalCoordinator:
    """Coordinate approval lifecycle commands and inbox reads.

    Public surface tracked by PRD §3.3. Methods forward to the legacy
    :class:`RuntimeApiService` during PR 1; implementation moves here in PR 4.
    """

    def __init__(self, *, legacy: "RuntimeApiService") -> None:
        self._legacy = legacy

    async def list_assigned_approvals(
        self,
        *,
        org_id: str,
        user_id: str,
        status_filter: ApprovalStatus,
        limit: int,
        cursor: str | None,
    ) -> AssignedApprovalsResponse:
        return await self._legacy.list_assigned_approvals(
            org_id=org_id,
            user_id=user_id,
            status_filter=status_filter,
            limit=limit,
            cursor=cursor,
        )

    async def record_approval_decision(
        self,
        *,
        org_id: str,
        approval_id: str,
        request: ApprovalDecisionRequest,
    ) -> ApprovalDecisionResponse:
        return await self._legacy.record_approval_decision(
            org_id=org_id,
            approval_id=approval_id,
            request=request,
        )

    async def request_approval_undo(
        self,
        *,
        org_id: str,
        approval_id: str,
        decided_by_user_id: str,
    ) -> ApprovalUndoResponse:
        return await self._legacy.request_approval_undo(
            org_id=org_id,
            approval_id=approval_id,
            decided_by_user_id=decided_by_user_id,
        )

"""Application service for the single-artifact staged-write engine (PRD-D1).

Thin orchestration over the pure :class:`WriteStager`: resolves + scope-checks
the host run (mirrors ``ApprovalCoordinator._run_for_scope`` — unknown run ⇒ 404,
foreign user ⇒ 403, no ledger event) and delegates the revision/decision/read to
the stager. The stager alone owns the fail-closed matrix; this layer owns tenancy.

Nothing here executes a write. ``record_decision{approve}`` records intent on the
ledger and returns — no CommitEngine, no MCP client (PRD-D2 owns execution).
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.surfaces_v2.staging import (
    StagedWriteState,
    StageForbidden,
    StageNotFound,
    WriteStager,
)


@dataclass(frozen=True)
class StageService:
    """Run-scoped facade over :class:`WriteStager` for the stage HTTP routes."""

    stager: WriteStager
    persistence: object

    async def get_state(
        self, *, org_id: str, user_id: str, run_id: str, stage_id: str
    ) -> StagedWriteState:
        await self._resolve_run(org_id=org_id, user_id=user_id, run_id=run_id)
        return await self.stager.get_state(
            org_id=org_id, run_id=run_id, stage_id=stage_id
        )

    async def add_user_revision(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        stage_id: str,
        base_rev: int,
        content_text: str,
        title: str | None,
    ) -> StagedWriteState:
        run = await self._resolve_run(org_id=org_id, user_id=user_id, run_id=run_id)
        return await self.stager.add_user_revision(
            run=run,
            org_id=org_id,
            run_id=run_id,
            stage_id=stage_id,
            base_rev=base_rev,
            content_text=content_text,
            title=title,
        )

    async def record_decision(
        self,
        *,
        org_id: str,
        user_id: str,
        run_id: str,
        stage_id: str,
        decision: str,
        rev: int | None,
    ) -> StagedWriteState:
        run = await self._resolve_run(org_id=org_id, user_id=user_id, run_id=run_id)
        return await self.stager.record_decision(
            run=run,
            org_id=org_id,
            run_id=run_id,
            stage_id=stage_id,
            decision=decision,
            rev=rev,
        )

    async def _resolve_run(self, *, org_id: str, user_id: str, run_id: str) -> object:
        """Fetch the run and assert it belongs to ``user_id`` within ``org_id``.

        Unknown run ⇒ :class:`StageNotFound` (404); foreign user ⇒
        :class:`StageForbidden` (403). Both raise BEFORE any emit, so a
        cross-tenant probe never touches the ledger.
        """

        get_run = getattr(self.persistence, "get_run", None)
        if get_run is None:
            raise StageNotFound()
        run = await get_run(org_id=org_id, run_id=run_id)
        if run is None:
            raise StageNotFound()
        if getattr(run, "user_id", None) != user_id:
            raise StageForbidden()
        return run


__all__ = ["StageService"]

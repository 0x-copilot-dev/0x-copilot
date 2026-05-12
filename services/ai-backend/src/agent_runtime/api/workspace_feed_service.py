"""Application service backing the Workspace-pane subagent and source data feeds.

Validates and clamps request inputs, truncates user-bearing text to the public
budget, and composes response envelopes from the read-only
:class:`SubagentStorePort` and :class:`SourceStorePort` ports. Decryption and
authorization are delegated to the adapter layer and the route layer respectively.
"""

from __future__ import annotations


from agent_runtime.persistence.ports import SourceStorePort, SubagentStorePort
from agent_runtime.persistence.records import (
    SourceAggregate,
    SubagentLifecycleStatus,
    SubagentSnapshot,
)
from runtime_api.schemas import (
    SourceEntry,
    SourceListResponse,
    SubagentEntry,
    SubagentListResponse,
    SubagentStatusFilter,
    SubagentTokenUsage,
)


class _WorkspaceLimits:
    """Public read budgets for the Workspace pane endpoints."""

    SUBAGENTS_DEFAULT = 50
    SUBAGENTS_MAX = 200
    SOURCES_DEFAULT = 200
    SOURCES_MAX = 500
    OBJECTIVE_TRUNCATE = 4096
    RESULT_TRUNCATE = 280
    SNIPPET_TRUNCATE = 280


class _Truncator:
    """Truncate user-bearing strings to the public budget without partial UTF-8."""

    @classmethod
    def text(cls, value: str | None, *, limit: int) -> str | None:
        if value is None:
            return None
        if len(value) <= limit:
            return value
        return value[: max(limit - 1, 0)] + "…"


class WorkspaceFeedService:
    """List subagents and sources for the Workspace pane right-rail."""

    def __init__(
        self,
        *,
        subagent_store: SubagentStorePort,
        source_store: SourceStorePort,
    ) -> None:
        self._subagents = subagent_store
        self._sources = source_store

    async def list_subagents(
        self,
        *,
        org_id: str,
        conversation_id: str,
        status_filter: SubagentStatusFilter,
        limit: int,
    ) -> SubagentListResponse:
        capped = max(1, min(limit, _WorkspaceLimits.SUBAGENTS_MAX))
        snapshots = await self._subagents.list_for_conversation(
            org_id=org_id,
            conversation_id=conversation_id,
            running_only=status_filter is SubagentStatusFilter.RUNNING,
            limit=capped,
        )
        truncated = len(snapshots) >= capped
        entries = tuple(self._to_entry(snapshot) for snapshot in snapshots)
        return SubagentListResponse(
            conversation_id=conversation_id,
            subagents=entries,
            truncated=truncated,
        )

    async def list_sources(
        self,
        *,
        org_id: str,
        conversation_id: str,
        run_id: str | None,
        limit: int,
    ) -> SourceListResponse:
        capped = max(1, min(limit, _WorkspaceLimits.SOURCES_MAX))
        rows = await self._sources.aggregate_for_conversation(
            org_id=org_id,
            conversation_id=conversation_id,
            run_id=run_id,
            limit=capped,
        )
        truncated = len(rows) >= capped
        entries = tuple(self._to_source_entry(row) for row in rows)
        return SourceListResponse(
            conversation_id=conversation_id,
            run_id=run_id,
            sources=entries,
            truncated=truncated,
        )

    @staticmethod
    def _to_entry(snapshot: SubagentSnapshot) -> SubagentEntry:
        return SubagentEntry(
            task_id=snapshot.task_id,
            parent_run_id=snapshot.parent_run_id,
            subagent_name=snapshot.subagent_name,
            status=snapshot.status,
            display_title=snapshot.display_title,
            objective_summary=_Truncator.text(
                snapshot.objective_summary,
                limit=_WorkspaceLimits.OBJECTIVE_TRUNCATE,
            ),
            started_at=snapshot.started_at,
            completed_at=snapshot.completed_at,
            duration_ms=snapshot.duration_ms,
            result_summary=_Truncator.text(
                snapshot.result_summary,
                limit=_WorkspaceLimits.RESULT_TRUNCATE,
            ),
            safe_error_code=snapshot.safe_error_code,
            safe_error_message=snapshot.safe_error_message,
            token_usage=(
                SubagentTokenUsage.model_validate(snapshot.token_usage.model_dump())
                if snapshot.token_usage is not None
                else None
            ),
        )

    @staticmethod
    def _to_source_entry(row: SourceAggregate) -> SourceEntry:
        return SourceEntry(
            citation_id=row.citation_id,
            source_connector=row.source_connector,
            source_doc_id=row.source_doc_id,
            source_url=row.source_url,
            title=row.title,
            snippet=_Truncator.text(
                row.snippet, limit=_WorkspaceLimits.SNIPPET_TRUNCATE
            ),
            freshness_at=row.freshness_at,
            citation_count=row.citation_count,
            last_cited_at=row.last_cited_at,
        )


# Re-export for convenience — callers usually only need the lifecycle enum
# alongside the service.
__all__ = (
    "SubagentLifecycleStatus",
    "WorkspaceFeedService",
)

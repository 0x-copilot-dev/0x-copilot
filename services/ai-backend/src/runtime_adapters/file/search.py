"""Search result contract for the file store's conversation FTS.

Kept in the file adapter package because full-text search is a desktop
``single_user_desktop`` capability layered on the disposable SQLite catalog —
the Postgres and in-memory adapters do not implement it. The store returns
these typed hits (ranked best-first) rather than bare records so callers can see
the relevance score the ranking is derived from.
"""

from __future__ import annotations

from agent_runtime.execution.contracts import RuntimeContract
from runtime_api.schemas import ConversationRecord


class ConversationSearchHit(RuntimeContract):
    """One ranked conversation match from :meth:`FileRuntimeApiStore.search_conversations`."""

    conversation: ConversationRecord
    # FTS5 bm25 relevance: a lower (more negative) score is a stronger match.
    # Hits are returned already ordered best-first, so callers rarely need it.
    score: float


__all__ = ("ConversationSearchHit",)

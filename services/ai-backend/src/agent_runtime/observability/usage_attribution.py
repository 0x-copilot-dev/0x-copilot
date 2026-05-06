"""Per-LLM-call connector attribution (PR 7.2).

The rule is deterministic: a model call attributes to the most recent
``runtime_tool_invocations`` row on the same run with
``status='completed'`` and ``completed_at`` strictly before the call's
emit time. ``None`` when no such row exists (cold-turn / planning).

The lookup is one indexed SQL read per emitted ``MODEL_CALL_COMPLETED``
event — typically two or three reads per LLM turn. Best-effort: any
exception falls through to ``None`` and the call records as
"(unattributed)" rather than failing the run.
"""

from __future__ import annotations

import logging
from datetime import datetime

from agent_runtime.api.async_ports import AsyncPersistencePort


_LOGGER = logging.getLogger(__name__)


class UsageAttributionResolver:
    """Resolve which connector to attribute an LLM call to."""

    def __init__(self, persistence: AsyncPersistencePort) -> None:
        self._persistence = persistence

    async def resolve(
        self,
        *,
        org_id: str,
        run_id: str,
        before: datetime,
    ) -> str | None:
        """Return the connector_slug to stamp on this LLM call, or ``None``."""

        try:
            return await self._persistence.query_last_completed_tool_connector_slug(
                org_id=org_id,
                run_id=run_id,
                before=before,
            )
        except Exception:
            _LOGGER.warning(
                "usage_attribution_lookup_failed",
                extra={"metadata": {"org_id": org_id, "run_id": run_id}},
                exc_info=True,
            )
            return None

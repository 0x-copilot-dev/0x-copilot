"""Code-enforced per-tool call-count and input-token budgets.

Single record type maps to ``runtime_tool_budgets``. The middleware
resolves the matching budget per (org_id, tool_name) with most-specific
wins, then admits or rejects each tool call against the per-run ledger.

:class:`DefaultToolBudget` owns the global seed row so the in-memory,
file, and SQL adapters cannot drift apart on what "the default" means.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from pydantic import Field, PositiveInt

from agent_runtime.execution.contracts import RuntimeContract


class ToolBudgetEnforcement(StrEnum):
    """``soft`` warns and admits; ``hard`` rejects the call."""

    SOFT = "soft"
    HARD = "hard"


class ToolBudgetRecord(RuntimeContract):
    """One configured per-tool budget for an org or for the global default."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    # ``None`` is the global default (the seed row in migration 0010).
    org_id: str | None = None
    # ``"*"`` matches every tool.
    tool_name: str
    max_calls_per_run: PositiveInt
    max_input_tokens_per_call: PositiveInt | None = None
    max_input_tokens_per_run: PositiveInt | None = None
    enforcement: ToolBudgetEnforcement
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DefaultToolBudget:
    """The global seed row — one definition for every store adapter.

    The same default previously appeared as a hand-copied literal in the
    in-memory adapter, the file adapter, and the SQL seed, with a fourth
    copy in the ``RUNTIME_TOOL_CALL_BUDGET`` setting that feeds the
    model's prompt. That is one number with four owners, and the prompt
    copy silently disagreeing with the enforced copy is exactly the kind
    of drift that reads as a runtime bug.

    :attr:`MAX_CALLS_PER_RUN` is the cap **per distinct tool name**, not
    per run in aggregate: the wildcard row governs each tool separately.
    Ten is sized for a desktop research turn — enough web searches to
    cover a handful of genuinely distinct angles, plus headroom for the
    file and todo tools a Deep Agents loop uses along the way — while
    still bounding a runaway loop. Deployments tune it with
    ``RUNTIME_TOOL_CALL_BUDGET``; an org-scoped row in
    ``runtime_tool_budgets`` overrides it per tool.
    """

    ID = "seed_default"
    TOOL_NAME = "*"
    MAX_CALLS_PER_RUN = 10
    ENV_VAR = "RUNTIME_TOOL_CALL_BUDGET"

    @classmethod
    def max_calls_per_run(cls, env: Mapping[str, str] | None = None) -> int:
        """Resolve the seed cap from the environment, falling back to the default.

        Reads the **process** environment, which is how Docker, the
        desktop supervisor, and self-host all pass configuration. It
        deliberately does not re-implement
        :meth:`RuntimeSettings._load_env_file`'s layering, so a value
        set *only* in a service-local ``.env`` file reaches the prompt
        copy without reaching this row. Both sides share a default, so
        that gap opens only if someone overrides via a dotfile instead
        of the process env; export it there and the two stay in step.

        Invalid or non-positive values fall back rather than raising: a
        typo in a desktop ``.env`` must not take the runtime down, and
        the fallback is a safe, documented number.
        """

        raw = (env if env is not None else os.environ).get(cls.ENV_VAR)
        if raw is None:
            return cls.MAX_CALLS_PER_RUN
        try:
            parsed = int(raw.strip())
        except (AttributeError, ValueError):
            return cls.MAX_CALLS_PER_RUN
        return parsed if parsed > 0 else cls.MAX_CALLS_PER_RUN

    @classmethod
    def record(cls, env: Mapping[str, str] | None = None) -> ToolBudgetRecord:
        """Build the global (``org_id=None``) wildcard budget row."""

        return ToolBudgetRecord(
            id=cls.ID,
            org_id=None,
            tool_name=cls.TOOL_NAME,
            max_calls_per_run=cls.max_calls_per_run(env),
            enforcement=ToolBudgetEnforcement.HARD,
        )


__all__ = (
    "DefaultToolBudget",
    "ToolBudgetEnforcement",
    "ToolBudgetRecord",
)

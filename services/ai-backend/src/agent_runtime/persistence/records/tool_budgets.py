"""Code-enforced per-tool call-count and input-token budgets.

Single record type maps to ``runtime_tool_budgets``. The middleware
resolves the matching budget per (org_id, tool_name) with most-specific
wins, then admits or rejects each tool call against the per-run ledger.
"""

from __future__ import annotations

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

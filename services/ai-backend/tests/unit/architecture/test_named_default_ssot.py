"""SSOT gate (T0.3): a named runtime default resolves to exactly ONE value.

A number with more than one owner drifts. ``tool_call_budget`` — the per-tool
call cap — had exactly that failure: the enforced seed row
(``DefaultToolBudget.MAX_CALLS_PER_RUN``) said 10 while two prompt-side literals
said 5, so the model was told a smaller budget than ``ToolBudgetMiddleware``
actually admits. ``DefaultToolBudget``'s own docstring names this "the kind of
drift that reads as a runtime bug."

This gate reads every declaration site of a named default and asserts they
agree; when they must be reconciled, the site the runtime *enforces* is
authoritative — a prompt or contract copy that disagrees with what is enforced
is the bug, never the enforced value. Adding a row to ``_NAMED_DEFAULTS`` is how
a newly-discovered copy of an existing default gets pinned. See
docs/plan/ai-backend-consolidation/TASKS.md T0.3.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import BaseModel

from agent_runtime.execution import deep_agent_builder
from agent_runtime.execution.contracts import ModelConfig
from agent_runtime.persistence.records.tool_budgets import DefaultToolBudget
from agent_runtime.settings import RuntimeExecutionSettings


def _field_default(model: type[BaseModel], field: str) -> object:
    return model.model_fields[field].default


# A named default -> the sites that each declare it. The ENFORCED site is noted
# in a comment; every other site is a copy that must equal it. Seeded with the
# one case T0.3 reconciled; add a row when a new copy of an existing default
# appears.
_NAMED_DEFAULTS: dict[str, dict[str, Callable[[], object]]] = {
    "tool_call_budget": {
        # ENFORCED: the wildcard seed row ToolBudgetMiddleware admits against.
        "DefaultToolBudget.MAX_CALLS_PER_RUN": (
            lambda: DefaultToolBudget.MAX_CALLS_PER_RUN
        ),
        "RuntimeExecutionSettings.tool_call_budget": (
            lambda: _field_default(RuntimeExecutionSettings, "tool_call_budget")
        ),
        "ModelConfig.tool_call_budget": (
            lambda: _field_default(ModelConfig, "tool_call_budget")
        ),
        "deep_agent_builder._DEFAULT_TOOL_CALL_BUDGET": (
            lambda: deep_agent_builder._DEFAULT_TOOL_CALL_BUDGET
        ),
    },
}


@pytest.mark.parametrize("name", sorted(_NAMED_DEFAULTS))
def test_named_default_resolves_to_a_single_value(name: str) -> None:
    """Every declaration site of a named default must hold the same value.

    ``tool_call_budget`` forked once: the enforced seed cap said 10 while two
    prompt-side literals said 5, so the model was told a smaller budget than the
    middleware admits. This refuses to let a default fork again.
    """

    resolved = {label: read() for label, read in _NAMED_DEFAULTS[name].items()}
    assert len(set(resolved.values())) == 1, (
        f"'{name}' has forked across its declaration sites: {resolved}. "
        "Reconcile every site to the enforced value (a prompt or contract copy "
        "that disagrees with what the middleware enforces is the bug, never the "
        "enforced value)."
    )


def test_tool_call_budget_agrees_on_the_enforced_ten() -> None:
    """Pin the value, not just the agreement.

    The generic gate only asserts the sites *agree*; moving them together to a
    wrong number would satisfy it. This pins that the number they agree on is
    the documented, enforced cap — 10 per distinct tool name — so a silent
    regression to the historical 5 fails even if every site moved in lockstep.
    """

    assert DefaultToolBudget.MAX_CALLS_PER_RUN == 10
    for label, read in _NAMED_DEFAULTS["tool_call_budget"].items():
        assert read() == 10, f"{label} is {read()!r}, expected the enforced 10"

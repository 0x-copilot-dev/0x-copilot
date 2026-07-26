"""D2 hard gates for authoritative internal-operation execution."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent_runtime.capabilities.operations.contracts import (
    GateResolution,
    OperationGatewayMode,
)
from agent_runtime.capabilities.operations.context import OperationContext
from agent_runtime.capabilities.operations.internal_adapter import (
    InternalOperationAdapter,
)
from agent_runtime.surfaces_v2.ledger_models import GateKind, OperationOutcome
from tests.unit.agent_runtime.capabilities.operations.helpers import BoundContextMixin


@dataclass
class _DenyingGate:
    async def resolve(self, **_kwargs: object) -> GateResolution:
        return GateResolution(
            allowed=False,
            gate_kind=GateKind.CAPABILITY,
            safe_summary="Subagent delegation is unavailable; no task was started.",
        )


class TestInternalOperationAdapter(BoundContextMixin):
    @pytest.mark.asyncio
    async def test_blocked_gateway_never_enters_legacy_subagent_dispatch(self) -> None:
        calls = 0

        async def legacy() -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {"messages": []}

        token = self.bind(
            mode=OperationGatewayMode.ENFORCE,
            durable_arguments=True,
        )
        try:
            result = await InternalOperationAdapter(
                capability="subagent",
                op="dispatch",
                gates=_DenyingGate(),
            ).invoke(
                arguments={"description": "Find facts", "subagent_type": "researcher"},
                legacy=legacy,
                safe_summary="Completed subagent dispatch.",
            )
        finally:
            OperationContext.unbind(token)

        assert result.outcome is OperationOutcome.BLOCKED
        assert result.value is None
        assert calls == 0

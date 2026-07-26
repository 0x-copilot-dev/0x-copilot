"""Adversarial tests for the closed canonical builtin row-set executor."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from agent_runtime.capabilities.tools.builtin.stage_rowset_write import (
    RowSetEffectProposal,
)
from agent_runtime.effects.executor import EffectExecutionScope
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes, sha256_hex
from agent_runtime.surfaces_v2.commit_engine import (
    StageCommitConnectorError,
    StageCommitRequest,
    StageCommitTimeout,
)
from agent_runtime.surfaces_v2.entities import EffectExecutionRequest
from agent_runtime.surfaces_v2.ledger_models import EffectOutcome
from runtime_worker.builtin_effect_executor import (
    BuiltinRowSetEffectExecutor,
    BuiltinRowSetEffectMaterial,
    BuiltinRowSetMaterialError,
)

_STAGE_ID = "stg_00000000-0000-4000-8000-000000000001"
_CONTENT_REF = "operation://op_00000000-0000-4000-8000-000000000001/args"
_PROPOSAL = RowSetEffectProposal.model_validate(
    {
        "target_connector": "Linear",
        "target_op": "update_issue",
        "title": "Prioritize renewals",
        "rows": [
            {
                "row_key": "issue-1",
                "title": "Acme",
                "target_args": {"id": "issue-1", "priority": 2},
                "changes": [],
            },
            {
                "row_key": "issue-2",
                "title": "Beta",
                "target_args": {"id": "issue-2", "priority": 3},
                "changes": [],
            },
        ],
        "agent_holds": [{"row_key": "issue-2", "reason": "recent reply"}],
    }
)


def _scope() -> EffectExecutionScope:
    return EffectExecutionScope(
        org_id="org_builtin_rowset",
        user_id="user_builtin_rowset",
        conversation_id="conv_builtin_rowset",
        run_id="run_builtin_rowset",
        owner_ref="principal://users/user_builtin_rowset",
    )


def _material() -> BuiltinRowSetEffectMaterial:
    target_digest = sha256_hex(
        canonical_json_bytes({"capability": "linear", "op": "update_issue"})
    )
    proposal_digest = sha256_hex(
        canonical_json_bytes(_PROPOSAL.model_dump(mode="json"))
    )
    return BuiltinRowSetEffectMaterial(
        target_connector="linear",
        target_op="update_issue",
        rows=_PROPOSAL.rows,
        held_row_keys=frozenset({"issue-2"}),
        target_ref=f"rowset-target://{target_digest}",
        target_digest=target_digest,
        proposal_ref=f"proposal://{_STAGE_ID}/revisions/1",
        proposal_content_ref=_CONTENT_REF,
        proposal_digest=proposal_digest,
    )


def _request(
    material: BuiltinRowSetEffectMaterial | None = None,
) -> EffectExecutionRequest:
    value = material or _material()
    return EffectExecutionRequest(
        stage_id=_STAGE_ID,
        revision=1,
        idempotency_key="effect:builtin-rowset:1",
        target_ref=value.target_ref,
        target_digest=value.target_digest,
        proposal_ref=value.proposal_ref,
        proposal_content_ref=value.proposal_content_ref,
        proposal_digest=value.proposal_digest,
        actor="user",
        decision_ledger_id="rbuiltin·0001",
    )


@dataclass
class _Resolver:
    values: list[BuiltinRowSetEffectMaterial | None]
    calls: int = 0

    async def resolve(
        self, request: EffectExecutionRequest
    ) -> BuiltinRowSetEffectMaterial | None:
        del request
        index = min(self.calls, len(self.values) - 1)
        self.calls += 1
        return self.values[index]


@dataclass
class _Connector:
    outcomes: list[Exception | None] = field(default_factory=list)
    requests: list[StageCommitRequest] = field(default_factory=list)

    async def execute(self, request: StageCommitRequest) -> object:
        self.requests.append(request)
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if outcome is not None:
            raise outcome
        return object()


def _executor(
    *, resolver: _Resolver, connector: _Connector
) -> BuiltinRowSetEffectExecutor:
    return BuiltinRowSetEffectExecutor(
        scope=_scope(),
        connector=connector,  # type: ignore[arg-type] -- focused transport fake.
        material_resolver=resolver,
    )


@pytest.mark.asyncio
async def test_only_exact_unheld_rows_reach_the_existing_connector_lane() -> None:
    material = _material()
    connector = _Connector()
    executor = _executor(resolver=_Resolver([material]), connector=connector)

    prepared = await executor.prepare(_request(material))
    result = await executor.apply(prepared)

    assert result.outcome is EffectOutcome.PARTIAL
    assert result.retryable is False
    assert len(connector.requests) == 1
    dispatched = connector.requests[0]
    assert dispatched.target_connector == "linear"
    assert dispatched.target_op == "update_issue"
    assert dispatched.row_key == "issue-1"
    assert dispatched.row_args == {"id": "issue-1", "priority": 2}
    assert dispatched.tool_arguments() == {"id": "issue-1", "priority": 2}


@pytest.mark.asyncio
async def test_stale_material_after_prepare_is_refused_without_dispatch() -> None:
    material = _material()
    stale = BuiltinRowSetEffectMaterial(
        **{**material.__dict__, "proposal_digest": "0" * 64}
    )
    connector = _Connector()
    executor = _executor(resolver=_Resolver([material, stale]), connector=connector)

    result = await executor.apply(await executor.prepare(_request(material)))

    assert result.outcome is EffectOutcome.FAILED
    assert connector.requests == []


@pytest.mark.asyncio
async def test_timeout_is_indeterminate_and_stops_without_a_second_dispatch() -> None:
    material = _material()
    unheld = BuiltinRowSetEffectMaterial(
        **{**material.__dict__, "held_row_keys": frozenset()}
    )
    connector = _Connector(outcomes=[StageCommitTimeout("provider detail")])
    executor = _executor(resolver=_Resolver([unheld]), connector=connector)

    result = await executor.apply(await executor.prepare(_request(unheld)))

    assert result.outcome is EffectOutcome.INDETERMINATE
    assert result.retryable is False
    assert len(connector.requests) == 1
    assert "provider detail" not in str(result)


@pytest.mark.asyncio
async def test_non_rowset_or_mismatched_material_fails_before_connector_access() -> (
    None
):
    material = _material()
    mismatched = BuiltinRowSetEffectMaterial(
        **{**material.__dict__, "target_ref": "rowset-target://" + "0" * 64}
    )
    connector = _Connector(outcomes=[StageCommitConnectorError("must not call")])
    executor = _executor(resolver=_Resolver([mismatched]), connector=connector)

    with pytest.raises(BuiltinRowSetMaterialError):
        await executor.prepare(_request(material))

    assert connector.requests == []


@pytest.mark.asyncio
async def test_unreviewed_connector_target_is_refused_after_approval_without_dispatch() -> (
    None
):
    """A generic builtin proposal must never become a connector tunnel."""

    material = _material()
    unreviewed = BuiltinRowSetEffectMaterial(
        **{
            **material.__dict__,
            "target_connector": "asana",
            "target_op": "create_task",
        }
    )
    connector = _Connector()
    executor = _executor(resolver=_Resolver([unreviewed]), connector=connector)

    with pytest.raises(BuiltinRowSetMaterialError):
        await executor.prepare(_request(unreviewed))

    assert connector.requests == []

"""Focused safety contract for the dark legacy MCP effect executor."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from agent_runtime.capabilities.surfaces.commit import ConnectorCommitResult
from agent_runtime.effects.executor import EffectExecutionScope
from runtime_worker.legacy_mcp_effect_executor import (
    LegacyMcpEffectExecutor,
    LegacyMcpEffectExecutorDisabledError,
    LegacyMcpEffectMaterial,
    LegacyMcpEffectMaterialError,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_sha256
from agent_runtime.surfaces_v2.commit_engine import (
    StageCommitConnectorError,
    StageCommitRequest,
    StageCommitTimeout,
)
from agent_runtime.surfaces_v2.entities import EffectExecutionRequest
from agent_runtime.surfaces_v2.ledger_models import EffectOutcome

_STAGE_ID = "stg_00000000-0000-4000-8000-000000000001"
_TARGET_REF = "mcp-target://linear/issue-123"


def _scope() -> EffectExecutionScope:
    return EffectExecutionScope(
        org_id="org_legacy_mcp",
        user_id="user_legacy_mcp",
        conversation_id="conv_legacy_mcp",
        run_id="run_legacy_mcp",
        owner_ref="principal://users/user_legacy_mcp",
    )


def _request(arguments: dict[str, object]) -> EffectExecutionRequest:
    return EffectExecutionRequest(
        stage_id=_STAGE_ID,
        revision=1,
        idempotency_key="effect:legacy-mcp:1",
        target_ref=_TARGET_REF,
        target_digest="b" * 64,
        proposal_ref=f"proposal://{_STAGE_ID}/revisions/1",
        proposal_digest=canonical_json_sha256(arguments),
        actor="user",
        decision_ledger_id="rlegacy·0001",
    )


def _material(
    request: EffectExecutionRequest, arguments: dict[str, object]
) -> LegacyMcpEffectMaterial:
    return LegacyMcpEffectMaterial(
        target_connector="linear",
        target_op="update_issue",
        arguments=arguments,
        target_ref=request.target_ref,
        target_digest=request.target_digest,
        proposal_ref=request.proposal_ref,
        proposal_digest=request.proposal_digest,
    )


@dataclass
class _Resolver:
    values: list[LegacyMcpEffectMaterial | None]
    calls: int = 0

    async def resolve(
        self, request: EffectExecutionRequest
    ) -> LegacyMcpEffectMaterial | None:
        del request
        index = min(self.calls, len(self.values) - 1)
        self.calls += 1
        return self.values[index]


@dataclass
class _Connector:
    result: ConnectorCommitResult | Exception = field(
        default_factory=lambda: ConnectorCommitResult(
            status="sent", external_ref="provider-private-receipt"
        )
    )
    requests: list[StageCommitRequest] = field(default_factory=list)

    async def execute(self, request: StageCommitRequest) -> ConnectorCommitResult:
        self.requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _executor(
    *,
    resolver: _Resolver,
    connector: _Connector,
    enabled: bool = True,
) -> LegacyMcpEffectExecutor:
    return LegacyMcpEffectExecutor(
        scope=_scope(),
        connector=connector,  # type: ignore[arg-type] -- focused connector seam fake
        material_resolver=resolver,
        enabled=enabled,
    )


@pytest.mark.asyncio
async def test_exact_approved_arguments_use_existing_connector_row_args_lane() -> None:
    arguments: dict[str, object] = {
        "title": "Approved issue title",
        "fields": {"labels": ["customer"], "priority": "high"},
        "clear_owner": False,
    }
    request = _request(arguments)
    connector = _Connector()
    executor = _executor(
        resolver=_Resolver([_material(request, arguments)]), connector=connector
    )

    prepared = await executor.prepare(request)
    result = await executor.apply(prepared)

    assert result.outcome is EffectOutcome.APPLIED
    assert result.receipt_ref is None
    assert result.result_digest is None
    assert "provider-private-receipt" not in str(result)
    assert len(connector.requests) == 1
    dispatched = connector.requests[0]
    assert dispatched.target_connector == "linear"
    assert dispatched.target_op == "update_issue"
    assert dispatched.org_id == "org_legacy_mcp"
    assert dispatched.user_id == "user_legacy_mcp"
    assert dispatched.run_id == "run_legacy_mcp"
    assert dispatched.tool_arguments() == arguments
    assert dispatched.row_args == arguments
    assert dispatched.body == ""


@pytest.mark.asyncio
async def test_disabled_constructor_cannot_create_mcp_effect_executor() -> None:
    arguments: dict[str, object] = {"body": "approved"}
    request = _request(arguments)
    resolver = _Resolver([_material(request, arguments)])
    connector = _Connector()

    with pytest.raises(LegacyMcpEffectExecutorDisabledError):
        _executor(resolver=resolver, connector=connector, enabled=False)

    assert resolver.calls == 0
    assert connector.requests == []


@pytest.mark.asyncio
async def test_prepare_rejects_mismatched_or_missing_material_before_dispatch() -> None:
    arguments: dict[str, object] = {"body": "approved"}
    request = _request(arguments)
    mismatched = _material(request, arguments).model_copy(
        update={"target_digest": "c" * 64}
    )
    connector = _Connector()
    executor = _executor(resolver=_Resolver([mismatched]), connector=connector)

    with pytest.raises(LegacyMcpEffectMaterialError):
        await executor.prepare(request)

    assert connector.requests == []


@pytest.mark.asyncio
async def test_apply_revalidates_material_after_prepare_and_never_dispatches_stale_args() -> (
    None
):
    arguments: dict[str, object] = {"body": "approved"}
    request = _request(arguments)
    stale = _material(request, arguments).model_copy(
        update={"proposal_digest": "d" * 64}
    )
    connector = _Connector()
    executor = _executor(
        resolver=_Resolver([_material(request, arguments), stale]), connector=connector
    )

    result = await executor.apply(await executor.prepare(request))

    assert result.outcome is EffectOutcome.FAILED
    assert result.retryable is False
    assert (
        result.safe_message
        == "Approved connector arguments are unavailable; no external change was made."
    )
    assert connector.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "outcome", "message"),
    [
        (
            StageCommitConnectorError("provider secret: abc"),
            EffectOutcome.FAILED,
            "The connector could not apply the approved change.",
        ),
        (
            StageCommitTimeout("provider secret: abc"),
            EffectOutcome.INDETERMINATE,
            "The connector outcome is unknown; it will not be sent again automatically.",
        ),
        (
            RuntimeError("provider secret: abc"),
            EffectOutcome.INDETERMINATE,
            "The connector outcome is unknown; it will not be sent again automatically.",
        ),
    ],
)
async def test_connector_failures_map_to_safe_outcomes_without_provider_detail(
    error: Exception, outcome: EffectOutcome, message: str
) -> None:
    arguments: dict[str, object] = {"body": "approved"}
    request = _request(arguments)
    connector = _Connector(result=error)
    executor = _executor(
        resolver=_Resolver([_material(request, arguments)]), connector=connector
    )

    result = await executor.apply(await executor.prepare(request))

    assert result.outcome is outcome
    assert result.retryable is False
    assert result.safe_message == message
    assert "provider secret" not in str(result)
    assert len(connector.requests) == 1


@pytest.mark.asyncio
async def test_reconcile_and_abort_never_resend_or_resolve_material() -> None:
    arguments: dict[str, object] = {"body": "approved"}
    request = _request(arguments)
    resolver = _Resolver([_material(request, arguments)])
    connector = _Connector()
    executor = _executor(resolver=resolver, connector=connector)

    prepared = await executor.prepare(request)
    await executor.abort(prepared)
    # A claim is intentionally not needed to demonstrate no client call; the
    # executor discards it because legacy MCP has no safe reconciliation API.
    result = await executor.reconcile(None)  # type: ignore[arg-type]

    assert result.outcome is EffectOutcome.INDETERMINATE
    assert connector.requests == []
    assert resolver.calls == 1

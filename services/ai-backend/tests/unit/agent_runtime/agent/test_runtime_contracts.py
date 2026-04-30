from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.agent.contracts import (
    AgentRuntimeContext,
    FeatureFlag,
    ModelConfig,
    RuntimeRunContext,
    RuntimeRunHandle,
)


def test_runtime_context_normalizes_roles_permissions_and_connectors(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    assert runtime_context_admin.roles == frozenset({"admin"})
    assert runtime_context_admin.permission_scopes == frozenset({"search:read", "docs:read"})
    assert runtime_context_admin.connector_scopes == {
        "google-drive": frozenset({"docs:read"})
    }
    assert runtime_context_admin.feature_flags == frozenset(
        {FeatureFlag.DYNAMIC_TOOL_LOADING}
    )
    assert runtime_context_admin.trace_id == "trace_123"


def test_missing_runtime_ids_are_generated(model_config: ModelConfig) -> None:
    context = AgentRuntimeContext(
        user_id="user_123",
        org_id="org_456",
        roles={"employee"},
        model_profile=model_config,
    )

    assert context.request_id
    assert context.run_id
    assert context.trace_id
    assert isinstance(context.run_context, RuntimeRunContext)


def test_runtime_run_handle_uses_product_owned_ids(
    runtime_context_admin: AgentRuntimeContext,
) -> None:
    context = runtime_context_admin.model_copy(
        update={
            "request_id": "request_123",
            "run_id": "run_123",
            "trace_id": "trace_123",
        }
    )
    handle = RuntimeRunHandle.from_context(context)

    assert handle.request_id == "request_123"
    assert handle.run_id == "run_123"
    assert handle.trace_id == "trace_123"
    assert handle.status == "accepted"


@pytest.mark.parametrize(
    "field_name, value",
    [
        ("user_id", ""),
        ("org_id", " "),
        ("roles", set()),
        ("permission_scopes", {"search read"}),
    ],
)
def test_malformed_runtime_context_rejects_required_fields(
    model_config: ModelConfig,
    field_name: str,
    value: object,
) -> None:
    data = {
        "user_id": "user_123",
        "org_id": "org_456",
        "roles": {"employee"},
        "permission_scopes": {"search:read"},
        "model_profile": model_config,
    }
    data[field_name] = value

    with pytest.raises(ValidationError):
        AgentRuntimeContext.model_validate(data)


def test_missing_model_profile_fails_validation() -> None:
    with pytest.raises(ValidationError):
        AgentRuntimeContext.model_validate(
            {
                "user_id": "user_123",
                "org_id": "org_456",
                "roles": {"employee"},
            }
        )


def test_unknown_feature_flag_fails_validation(model_config: ModelConfig) -> None:
    with pytest.raises(ValidationError):
        AgentRuntimeContext(
            user_id="user_123",
            org_id="org_456",
            roles={"employee"},
            model_profile=model_config,
            feature_flags={"not_a_known_flag"},
        )


def test_model_config_requires_token_budget() -> None:
    with pytest.raises(ValidationError):
        ModelConfig(
            provider="fake",
            model_name="fake-enterprise-model",
            max_input_tokens=0,
            timeout_seconds=30,
            temperature=0,
        )

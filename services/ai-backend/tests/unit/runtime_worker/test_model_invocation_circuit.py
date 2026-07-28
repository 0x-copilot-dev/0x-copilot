"""Worker circuit composition keeps BYOK health partitions isolated."""

from __future__ import annotations

from agent_runtime.execution.contracts import AgentRuntimeContext, ModelConfig
from agent_runtime.execution.model_invocation.circuit_health import (
    ProcessLocalProviderCircuitHealth,
    ProviderCircuitConfig,
)
from agent_runtime.execution.model_invocation.contracts import (
    ModelCredentialMode,
    ModelDeploymentHealth,
    ModelFailureClass,
    ModelRouteEntry,
)
from runtime_worker.model_invocation_circuit import ProviderCircuitHealthRegistry


def _context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id="user-1",
        org_id="org-1",
        roles=frozenset({"member"}),
        model_profile=ModelConfig(
            provider="openai",
            model_name="gpt-5-mini",
            max_input_tokens=16_000,
            max_output_tokens=1_000,
            timeout_seconds=30,
            temperature=0,
            supports_streaming=True,
        ),
        request_id="request-1",
        run_id="run-1",
        trace_id="trace-1",
        provider_keys={"openai": "user-key-never-persisted"},
    )


def _route(mode: ModelCredentialMode) -> ModelRouteEntry:
    return ModelRouteEntry(
        deployment_id="model-deployment:test",
        deployment_revision="deployment-v1",
        descriptor_revision="descriptor-v1",
        endpoint_ref="endpoint_0123456789abcdef0123456789abcdef",
        endpoint_revision="endpoint-v1",
        provider="openai",
        model_name="gpt-5-mini",
        region="default",
        credential_mode=mode,
        price_revision="price-v1",
        max_input_tokens=16_000,
        max_output_tokens=1_000,
    )


def test_byok_auth_failure_does_not_open_deployment_circuit() -> None:
    """A user key's auth failure has no deployment-global health side effect."""

    context = _context()
    registry = ProviderCircuitHealthRegistry(
        ProcessLocalProviderCircuitHealth(
            ProviderCircuitConfig(open_failure_threshold=1)
        )
    )
    byok = _route(ModelCredentialMode.BYOK)
    deployment = _route(ModelCredentialMode.DEPLOYMENT)

    registry.observe_failure(
        route=byok,
        context=context,
        failure_class=ModelFailureClass.AUTH_INVALID,
    )

    assert (
        registry.health.health(registry._route_key(route=byok, context=context))
        is ModelDeploymentHealth.OPEN_CIRCUIT
    )
    assert (
        registry.health.health(registry._route_key(route=deployment, context=context))
        is ModelDeploymentHealth.AVAILABLE
    )


def test_byok_circuit_key_is_credential_scoped_not_subject_scoped() -> None:
    """Changing only the actual provider key selects a fresh opaque partition."""

    first = _context()
    second = first.model_copy(
        update={"provider_keys": {"openai": "rotated-key-never-persisted"}}
    )
    registry = ProviderCircuitHealthRegistry()
    route = _route(ModelCredentialMode.BYOK)

    assert registry._route_key(route=route, context=first) != registry._route_key(
        route=route, context=second
    )

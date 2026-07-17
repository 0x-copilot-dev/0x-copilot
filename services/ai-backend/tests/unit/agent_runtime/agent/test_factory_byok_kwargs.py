"""BYOK Phase-2 — ``_assemble_harness`` injects the user's provider key.

Pins the production wiring the earlier user-policy work left dangling:
``user_policy_model_kwargs`` is now called from the factory and merged AFTER
``workspace_model_kwargs`` so the user's key (and opt-out ratchet) wins.
Also pins the redaction contract: the key never appears in the build
request's ``repr`` or in the emitted harness surfaces.
"""

from __future__ import annotations

import pytest

from agent_runtime.execution.contracts import (
    AgentRuntimeContext,
    RuntimeDependencies,
    RuntimeErrorCode,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.factory import acreate_agent_runtime
from tests.unit.agent_runtime.agent.helpers import CapturingAgentBuilder


class ByokContextMixin:
    """Builders for contexts that carry BYOK keys. Values are obviously fake."""

    OPENAI_KEY = "sk-unit-test-openai-key-000000000000"

    @staticmethod
    def openai_context(**overrides: object) -> AgentRuntimeContext:
        base: dict[str, object] = {
            "user_id": "user_byok",
            "org_id": "org_byok",
            "roles": {"employee"},
            "model_profile": {
                "provider": "openai",
                "model_name": "gpt-5.4-mini",
                "max_input_tokens": 128_000,
                "timeout_seconds": 30,
                "temperature": 0,
                "supports_streaming": True,
            },
        }
        base.update(overrides)
        return AgentRuntimeContext.model_validate(base)


class TestFactoryInjectsUserProviderKey(ByokContextMixin):
    async def test_api_key_reaches_extra_model_kwargs(
        self, fake_dependencies: RuntimeDependencies
    ) -> None:
        builder = CapturingAgentBuilder()

        await acreate_agent_runtime(
            context=self.openai_context(
                provider_keys={"openai": self.OPENAI_KEY},
            ),
            dependencies=fake_dependencies,
            agent_builder=builder,
        )

        call = builder.calls[0]
        assert call.extra_model_kwargs is not None
        assert call.extra_model_kwargs["api_key"] == self.OPENAI_KEY

    async def test_key_for_inactive_provider_is_not_injected(
        self, fake_dependencies: RuntimeDependencies
    ) -> None:
        builder = CapturingAgentBuilder()

        await acreate_agent_runtime(
            context=self.openai_context(
                provider_keys={"anthropic": "sk-ant-unit-test-key-000000000000"},
            ),
            dependencies=fake_dependencies,
            agent_builder=builder,
        )

        call = builder.calls[0]
        assert call.extra_model_kwargs is None or (
            "api_key" not in call.extra_model_kwargs
        )

    async def test_user_kwargs_merge_after_workspace_kwargs(
        self, fake_dependencies: RuntimeDependencies
    ) -> None:
        # Workspace opt-out (model_kwargs.store=False for openai) and the
        # user's BYOK key must compose — both present in the merged mapping.
        builder = CapturingAgentBuilder()

        await acreate_agent_runtime(
            context=self.openai_context(
                workspace_behavior_overrides={"training_data_opt_out": True},
                provider_keys={"openai": self.OPENAI_KEY},
            ),
            dependencies=fake_dependencies,
            agent_builder=builder,
        )

        call = builder.calls[0]
        assert call.extra_model_kwargs is not None
        assert call.extra_model_kwargs["model_kwargs"] == {"store": False}
        assert call.extra_model_kwargs["api_key"] == self.OPENAI_KEY

    async def test_no_keys_preserves_previous_behaviour(
        self, fake_dependencies: RuntimeDependencies
    ) -> None:
        builder = CapturingAgentBuilder()

        await acreate_agent_runtime(
            context=self.openai_context(),
            dependencies=fake_dependencies,
            agent_builder=builder,
        )

        call = builder.calls[0]
        assert call.extra_model_kwargs is None

    async def test_build_request_repr_never_leaks_the_key(
        self, fake_dependencies: RuntimeDependencies
    ) -> None:
        builder = CapturingAgentBuilder()

        await acreate_agent_runtime(
            context=self.openai_context(
                provider_keys={"openai": self.OPENAI_KEY},
            ),
            dependencies=fake_dependencies,
            agent_builder=builder,
        )

        assert self.OPENAI_KEY not in repr(builder.calls[0])

    async def test_region_pin_without_deployment_maps_to_configuration_error(
        self,
        fake_dependencies: RuntimeDependencies,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("PROVIDER_REGION_DEPLOYMENTS", raising=False)
        builder = CapturingAgentBuilder()

        with pytest.raises(AgentRuntimeError) as exc_info:
            await acreate_agent_runtime(
                context=self.openai_context(
                    user_policies_json={"privacy": {"region": "eu-west-1"}},
                ),
                dependencies=fake_dependencies,
                agent_builder=builder,
            )

        assert exc_info.value.code is RuntimeErrorCode.CONFIGURATION_ERROR
        assert "eu-west-1" in exc_info.value.safe_message

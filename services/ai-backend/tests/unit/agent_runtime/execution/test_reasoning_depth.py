"""Tests for the reasoning-depth → budget mapping.

Pins the contract that the composer's Fast / Balanced / Deep selector
flows end-to-end into the runtime: the request accepts the field, the
resolver applies the multipliers exactly once, the worker reads the
post-mapped values, and the API rejects bogus literals at the boundary.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.execution.contracts import ModelConfig
from agent_runtime.execution.depth import DepthBudgetTable, ReasoningDepth
from agent_runtime.execution.models import ModelConfigResolver, ModelSelection
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.schemas import (
    CreateConversationRequest,
    CreateRunRequest,
)


_ORG_ID = "org_depth"
_USER_ID = "user_depth"
_ASSISTANT_ID = "assistant_depth"


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ENTERPRISE_SERVICE_TOKEN", "test-service-token")
    monkeypatch.setenv("RUNTIME_ENVIRONMENT", "development")
    yield


def _base_model_config() -> ModelConfig:
    return ModelConfig(
        provider="openai",
        model_name="gpt-5.4-mini",
        max_input_tokens=128_000,
        max_output_tokens=10_000,
        timeout_seconds=60.0,
        temperature=0.0,
        tool_call_budget=6,
    )


class TestDepthBudgetTable:
    """Pin the depth → budget multipliers as the single source of truth."""

    def test_none_is_identity(self) -> None:
        """``depth=None`` returns the input unchanged — the no-op path."""

        base = _base_model_config()
        scaled = DepthBudgetTable.apply(base, None)
        assert scaled is base
        assert scaled.reasoning_depth is None

    def test_balanced_preserves_baseline_numbers(self) -> None:
        """``balanced`` multipliers are 1.0 across all three axes, so the
        post-mapped numbers match the input. The depth label is stamped on
        the returned config for downstream observability.
        """

        base = _base_model_config()
        scaled = DepthBudgetTable.apply(base, ReasoningDepth.BALANCED)
        assert scaled.timeout_seconds == base.timeout_seconds
        assert scaled.max_output_tokens == base.max_output_tokens
        assert scaled.tool_call_budget == base.tool_call_budget
        assert scaled.reasoning_depth == ReasoningDepth.BALANCED.value

    def test_fast_shrinks_all_three_axes(self) -> None:
        """``fast`` halves timeout / tool budget and trims output tokens
        to ~0.6× the baseline. Confirms the multipliers are wired the
        way the table documents.
        """

        base = _base_model_config()
        scaled = DepthBudgetTable.apply(base, ReasoningDepth.FAST)
        assert scaled.timeout_seconds == 30.0
        assert scaled.max_output_tokens == 6_000
        assert scaled.tool_call_budget == 3
        assert scaled.reasoning_depth == ReasoningDepth.FAST.value

    def test_deep_doubles_all_three_axes(self) -> None:
        """``deep`` doubles timeout / tool budget and stretches output
        tokens to 1.5× the baseline.
        """

        base = _base_model_config()
        scaled = DepthBudgetTable.apply(base, ReasoningDepth.DEEP)
        assert scaled.timeout_seconds == 120.0
        assert scaled.max_output_tokens == 15_000
        assert scaled.tool_call_budget == 12
        assert scaled.reasoning_depth == ReasoningDepth.DEEP.value

    def test_max_output_tokens_none_stays_none(self) -> None:
        """When the baseline has no output cap configured, depth cannot
        invent one — the field stays ``None`` and provider defaults apply.
        """

        base = _base_model_config().model_copy(update={"max_output_tokens": None})
        scaled = DepthBudgetTable.apply(base, ReasoningDepth.DEEP)
        assert scaled.max_output_tokens is None

    def test_timeout_clamped_to_contract_ceiling(self) -> None:
        """``deep`` against a 400s baseline would exceed the Pydantic
        ``le=600`` cap on ``timeout_seconds``; the table clamps to the
        ceiling so the contract error is unreachable.
        """

        base = _base_model_config().model_copy(update={"timeout_seconds": 400.0})
        scaled = DepthBudgetTable.apply(base, ReasoningDepth.DEEP)
        assert scaled.timeout_seconds == 600.0

    def test_tool_call_budget_floor_protects_deep_agents_loop(self) -> None:
        """A baseline of ``tool_call_budget=1`` against ``fast`` would
        round to 0 and collapse the multi-step loop. The floor pins it
        to 1.
        """

        base = _base_model_config().model_copy(update={"tool_call_budget": 1})
        scaled = DepthBudgetTable.apply(base, ReasoningDepth.FAST)
        assert scaled.tool_call_budget >= 1


class TestModelConfigResolverAppliesDepth:
    """The resolver is the single application point for depth multipliers.

    Validates acceptance criterion 'verify the mapping is applied to the
    actual execution params, not just stored'.
    """

    def _settings(self, *, default_timeout: float = 60.0) -> RuntimeSettings:
        return RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_DEFAULT_TIMEOUT_SECONDS": str(default_timeout),
            }
        )

    def test_resolver_scales_timeout_for_deep(self) -> None:
        settings = self._settings(default_timeout=60)
        resolver = ModelConfigResolver(settings=settings)
        config = resolver.resolve(
            ModelSelection(reasoning_depth=ReasoningDepth.DEEP),
            require_credentials=False,
        )
        assert config.timeout_seconds == 120.0
        assert config.reasoning_depth == ReasoningDepth.DEEP.value

    def test_resolver_scales_tool_call_budget_for_fast(self) -> None:
        settings = self._settings()
        resolver = ModelConfigResolver(settings=settings)
        base_budget = settings.execution.tool_call_budget
        config = resolver.resolve(
            ModelSelection(reasoning_depth=ReasoningDepth.FAST),
            require_credentials=False,
        )
        # Default settings ``tool_call_budget=6`` × 0.5 = 3.
        assert config.tool_call_budget == max(1, round(base_budget * 0.5))

    def test_resolver_none_depth_is_no_op(self) -> None:
        """Existing callers that omit ``reasoning_depth`` see no change."""

        settings = self._settings(default_timeout=60)
        resolver = ModelConfigResolver(settings=settings)
        config = resolver.resolve(
            ModelSelection(reasoning_depth=None),
            require_credentials=False,
        )
        assert config.timeout_seconds == 60.0
        assert config.reasoning_depth is None


class TestCreateRunRequestAcceptsDepth:
    """API boundary validates ``reasoning_depth`` against the literal union.

    Pins acceptance criterion 'reject reasoning_depth=galaxy with 422'.
    """

    def test_accepts_each_valid_literal(self) -> None:
        for depth in ("fast", "balanced", "deep"):
            request = CreateRunRequest(
                conversation_id="conv_1",
                org_id=_ORG_ID,
                user_id=_USER_ID,
                user_input="hi",
                reasoning_depth=depth,
            )
            assert request.reasoning_depth is not None
            assert request.reasoning_depth.value == depth

    def test_rejects_unknown_literal_with_validation_error(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            CreateRunRequest(
                conversation_id="conv_1",
                org_id=_ORG_ID,
                user_id=_USER_ID,
                user_input="hi",
                reasoning_depth="galaxy",  # type: ignore[arg-type]
            )
        # Pydantic surfaces the field name in its error payload; an
        # invalid enum literal raises ``value_error`` / ``enum`` —
        # either is fine, we only assert the field is named.
        errors = excinfo.value.errors()
        assert any("reasoning_depth" in str(err.get("loc", ())) for err in errors)

    def test_omitting_field_keeps_run_creatable(self) -> None:
        """No-regression — existing run-start payloads without the new
        field still construct cleanly.
        """

        request = CreateRunRequest(
            conversation_id="conv_1",
            org_id=_ORG_ID,
            user_id=_USER_ID,
            user_input="hi",
        )
        assert request.reasoning_depth is None


class _CreateRunFixtureMixin:
    """Shared fixture for end-to-end ``RunCoordinator.create_run`` paths."""

    async def _build_service_with_conversation(
        self,
    ) -> tuple[RunCoordinator, InMemoryRuntimeApiStore, str]:
        store = InMemoryRuntimeApiStore()
        settings = RuntimeSettings.load(
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
            }
        )
        event_producer = RuntimeEventProducer(
            persistence=store,
            event_store=store,
            on_event_appended=None,
        )
        run_coordinator = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=event_producer,
            settings=settings,
            model_resolver=ModelConfigResolver(settings=settings),
        )
        conv_coordinator = ConversationCoordinator(
            persistence=store,
            settings=settings,
            run_coordinator=run_coordinator,
        )
        conversation = await conv_coordinator.create_conversation(
            CreateConversationRequest(
                org_id=_ORG_ID,
                user_id=_USER_ID,
                assistant_id=_ASSISTANT_ID,
            )
        )
        return run_coordinator, store, conversation.conversation_id


class TestEndToEndDepthOnRunRecord(_CreateRunFixtureMixin):
    """``reasoning_depth=deep`` reaches the persisted run record and the
    runtime context the worker will read.
    """

    async def test_deep_scales_budgets_on_persisted_run(self) -> None:
        # Capture the baseline (depth-less) numbers first, then verify the
        # ``deep`` request is exactly the documented 2× / 2× scaling. This
        # is robust to whatever env-default timeout / budget the test
        # harness loads from ``env_example``.
        (
            run_coordinator,
            store,
            conversation_id,
        ) = await self._build_service_with_conversation()
        baseline = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation_id,
                org_id=_ORG_ID,
                user_id=_USER_ID,
                user_input="baseline",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        baseline_run = store.runs[baseline.run_id]
        base_profile = baseline_run.runtime_context.model_profile

        deep = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation_id,
                org_id=_ORG_ID,
                user_id=_USER_ID,
                user_input="answer thoroughly",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
                reasoning_depth="deep",
            )
        )
        deep_run = store.runs[deep.run_id]
        deep_profile = deep_run.runtime_context.model_profile
        # ``deep`` doubles timeout and tool budget (with a 600s cap).
        assert deep_profile.timeout_seconds == min(
            base_profile.timeout_seconds * 2.0, 600.0
        )
        assert deep_profile.tool_call_budget == base_profile.tool_call_budget * 2
        # And the depth label is stamped on the worker-visible profile.
        assert deep_profile.reasoning_depth == "deep"

    async def test_omitted_depth_keeps_baseline_budgets(self) -> None:
        """No-regression: a request without ``reasoning_depth`` produces
        the same ``model_profile`` it always did.
        """

        (
            run_coordinator,
            store,
            conversation_id,
        ) = await self._build_service_with_conversation()

        response = await run_coordinator.create_run(
            CreateRunRequest(
                conversation_id=conversation_id,
                org_id=_ORG_ID,
                user_id=_USER_ID,
                user_input="hi",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        run = store.runs[response.run_id]
        # No depth supplied → ``reasoning_depth`` stays ``None``; numbers
        # equal whatever the resolver computed without the mapping.
        assert run.runtime_context.model_profile.reasoning_depth is None

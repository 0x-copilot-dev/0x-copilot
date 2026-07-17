"""PR 8.0.5 — user-policy provider kwargs (training opt-out + region)."""

from __future__ import annotations

import pytest

from agent_runtime.execution.provider_kwargs import (
    RegionUnavailableError,
    user_policy_model_kwargs,
)


class TestTrainingOptOut:
    def test_user_opt_out_emits_provider_kwargs(self) -> None:
        out = user_policy_model_kwargs(
            provider="anthropic",
            user_policies_json={"privacy": {"training_opt_out": True}},
        )
        assert out["extra_headers"]["anthropic-disable-training"] == "true"

    def test_user_opt_in_emits_nothing(self) -> None:
        out = user_policy_model_kwargs(
            provider="anthropic",
            user_policies_json={"privacy": {"training_opt_out": False}},
        )
        assert out == {}

    def test_unknown_provider_silently_skipped(self) -> None:
        out = user_policy_model_kwargs(
            provider="acme-llm",
            user_policies_json={"privacy": {"training_opt_out": True}},
        )
        # Empty dict — the provider has no documented opt-out flag.
        # Operators rely on workspace-level data-residency contract.
        assert out == {}


class TestRegionRouting:
    def test_region_routes_to_configured_deployment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "PROVIDER_REGION_DEPLOYMENTS",
            "anthropic:eu-west-1=https://eu.anthropic.example,"
            "anthropic:us-east-1=https://us.anthropic.example",
        )
        out = user_policy_model_kwargs(
            provider="anthropic",
            user_policies_json={"privacy": {"region": "eu-west-1"}},
        )
        assert out["base_url"] == "https://eu.anthropic.example"

    def test_unmapped_region_raises_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "PROVIDER_REGION_DEPLOYMENTS",
            "anthropic:us-east-1=https://us.anthropic.example",
        )
        with pytest.raises(RegionUnavailableError) as exc_info:
            user_policy_model_kwargs(
                provider="anthropic",
                user_policies_json={"privacy": {"region": "eu-west-1"}},
            )
        assert exc_info.value.region == "eu-west-1"

    def test_no_env_with_no_region_pin_is_passthrough(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PROVIDER_REGION_DEPLOYMENTS", raising=False)
        out = user_policy_model_kwargs(
            provider="anthropic",
            user_policies_json={"privacy": {}},
        )
        assert out == {}


class TestEmptySnapshot:
    def test_empty_blob_returns_empty(self) -> None:
        assert (
            user_policy_model_kwargs(provider="openai", user_policies_json=None) == {}
        )
        assert user_policy_model_kwargs(provider="openai", user_policies_json={}) == {}
        assert (
            user_policy_model_kwargs(
                provider="openai", user_policies_json={"privacy": "not-a-dict"}
            )
            == {}
        )


class ProviderKeyFixturesMixin:
    """Shared BYOK key constants — obviously fake values (never real-looking)."""

    OPENAI_KEY = "sk-unit-test-openai-key-000000000000"
    ANTHROPIC_KEY = "sk-ant-unit-test-key-000000000000"
    GEMINI_KEY = "AIzaUnitTestGeminiKey0000000000000"


class TestProviderKeyInjection(ProviderKeyFixturesMixin):
    """BYOK: the active provider's stored user key is injected as ``api_key``."""

    def test_key_injected_per_provider(self) -> None:
        keys = {
            "openai": self.OPENAI_KEY,
            "anthropic": self.ANTHROPIC_KEY,
            "gemini": self.GEMINI_KEY,
        }
        for provider, expected in keys.items():
            out = user_policy_model_kwargs(
                provider=provider,
                user_policies_json={},
                provider_keys=keys,
            )
            assert out == {"api_key": expected}

    def test_key_injected_even_with_empty_policy_snapshot(self) -> None:
        # A user with a stored key but no privacy policies must still get
        # the key — the old early-return on an empty snapshot would drop it.
        out = user_policy_model_kwargs(
            provider="openai",
            user_policies_json=None,
            provider_keys={"openai": self.OPENAI_KEY},
        )
        assert out == {"api_key": self.OPENAI_KEY}

    def test_no_key_for_active_provider_injects_nothing(self) -> None:
        out = user_policy_model_kwargs(
            provider="anthropic",
            user_policies_json={},
            provider_keys={"openai": self.OPENAI_KEY},
        )
        assert out == {}

    def test_key_composes_with_training_opt_out(self) -> None:
        out = user_policy_model_kwargs(
            provider="anthropic",
            user_policies_json={"privacy": {"training_opt_out": True}},
            provider_keys={"anthropic": self.ANTHROPIC_KEY},
        )
        assert out["api_key"] == self.ANTHROPIC_KEY
        assert out["extra_headers"]["anthropic-disable-training"] == "true"

    def test_key_composes_with_region_routing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(
            "PROVIDER_REGION_DEPLOYMENTS",
            "openai:eu-west-1=https://eu.openai.example",
        )
        out = user_policy_model_kwargs(
            provider="openai",
            user_policies_json={"privacy": {"region": "eu-west-1"}},
            provider_keys={"openai": self.OPENAI_KEY},
        )
        assert out["base_url"] == "https://eu.openai.example"
        assert out["api_key"] == self.OPENAI_KEY

    def test_empty_or_non_string_key_values_are_skipped(self) -> None:
        assert (
            user_policy_model_kwargs(
                provider="openai",
                user_policies_json={},
                provider_keys={"openai": ""},
            )
            == {}
        )
        assert (
            user_policy_model_kwargs(
                provider="openai",
                user_policies_json={},
                provider_keys=None,
            )
            == {}
        )

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.execution.contracts import (
    ModelReasoningConfig,
    ModelReasoningDisplay,
    ModelReasoningEffort,
    ModelReasoningSummary,
)
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.models import ModelConfigResolver, ModelSelection
from agent_runtime.settings import RuntimeSettings


def test_runtime_settings_loads_template_env_and_process_overrides(
    tmp_path: Path,
) -> None:
    template = tmp_path / "env_example"
    env_file = tmp_path / ".env"
    template.write_text(
        "\n".join(
            (
                "RUNTIME_DEFAULT_PROVIDER=openai",
                "RUNTIME_DEFAULT_MODEL=gpt-5.4-mini",
                "RUNTIME_DEFAULT_TEMPERATURE=0",
                "RUNTIME_DEFAULT_TIMEOUT_SECONDS=60",
                "RUNTIME_MAX_RETRIES=2",
                "RUNTIME_MAX_PARALLEL_RUNS=4",
                "RUNTIME_MAX_PARALLEL_TASKS=4",
                "RUNTIME_MAX_PARALLEL_SUBAGENTS=4",
            )
        ),
        encoding="utf-8",
    )
    env_file.write_text(
        "\n".join(
            (
                "RUNTIME_DEFAULT_MODEL=gpt-4.1",
                "RUNTIME_MAX_PARALLEL_RUNS=8",
            )
        ),
        encoding="utf-8",
    )

    settings = RuntimeSettings.load(
        template_file=template,
        env_file=env_file,
        environ={"OPENAI_API_KEY": "sk-test", "RUNTIME_MAX_PARALLEL_SUBAGENTS": "6"},
    )

    assert settings.default_model.provider == "openai"
    assert settings.default_model.model_name == "gpt-4.1"
    assert settings.execution.max_retries == 2
    assert settings.execution.max_parallel_runs == 8
    assert settings.execution.max_parallel_tasks == 4
    assert settings.execution.max_parallel_subagents == 6
    assert settings.execution.allow_empty_capabilities is False
    assert settings.openai.is_configured
    assert "sk-test" not in repr(settings)
    assert "api_key" not in settings.model_dump()["openai"]


def test_evaluation_store_has_a_bounded_default_and_explicit_override() -> None:
    defaulted = RuntimeSettings.load(environ={})
    overridden = RuntimeSettings.load(
        environ={"RUNTIME_EVALUATION_STORE_MAX_BYTES": "1048576"}
    )

    assert defaulted.store.evaluation_store_max_bytes == 536_870_912
    assert overridden.store.evaluation_store_max_bytes == 1_048_576


def test_runtime_settings_loads_default_reasoning_config() -> None:
    settings = RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-test",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
            "RUNTIME_DEFAULT_MODEL": "gpt-5.4",
            "RUNTIME_DEFAULT_REASONING_EFFORT": "medium",
            "RUNTIME_DEFAULT_REASONING_SUMMARY": "auto",
            "RUNTIME_DEFAULT_REASONING_INCLUDE_ENCRYPTED_CONTENT": "true",
        }
    )

    reasoning = settings.default_model.reasoning
    assert reasoning is not None
    assert reasoning.effort is ModelReasoningEffort.MEDIUM
    assert reasoning.summary is ModelReasoningSummary.AUTO
    assert reasoning.include_encrypted_content is True


def test_artifact_drafts_flag_requires_the_canonical_repository() -> None:
    with pytest.raises(
        ValueError, match="ARTIFACT_DRAFTS_V2 requires ARTIFACT_EFFECTS_V2"
    ):
        RuntimeSettings.load(environ={"ARTIFACT_DRAFTS_V2": "true"})

    enabled = RuntimeSettings.load(
        environ={
            "ARTIFACT_EFFECTS_V2": "true",
            "ARTIFACT_DRAFTS_V2": "true",
        }
    )

    assert enabled.execution.artifact_drafts_v2 is True


def test_model_resolver_validates_provider_keys_and_applies_defaults() -> None:
    settings = RuntimeSettings.load(
        environ={
            "OPENAI_API_KEY": "sk-openai",
            "ANTHROPIC_API_KEY": "sk-anthropic",
            "GOOGLE_API_KEY": "sk-google",
            "RUNTIME_DEFAULT_PROVIDER": "openai",
        }
    )
    resolver = ModelConfigResolver(settings)

    openai = resolver.resolve(ModelSelection(model_name="gpt-5.4-mini"))
    anthropic = resolver.resolve(
        ModelSelection(provider="anthropic", model_name="claude-sonnet-4")
    )
    gemini = resolver.resolve(
        ModelSelection(provider="google", model_name="gemini-2.5-pro")
    )

    assert openai.provider == "openai"
    assert anthropic.provider == "anthropic"
    assert gemini.provider == "gemini"


def test_model_resolver_applies_request_reasoning_override() -> None:
    settings = RuntimeSettings.load(
        environ={
            "ANTHROPIC_API_KEY": "sk-anthropic",
            "RUNTIME_DEFAULT_PROVIDER": "anthropic",
            "RUNTIME_DEFAULT_MODEL": "claude-opus-4-7",
            "RUNTIME_DEFAULT_REASONING_DISPLAY": "omitted",
        }
    )
    resolver = ModelConfigResolver(settings)

    resolved = resolver.resolve(
        ModelSelection(
            model_name="claude-opus-4-7",
            reasoning=ModelReasoningConfig(
                effort=ModelReasoningEffort.HIGH,
                display=ModelReasoningDisplay.SUMMARIZED,
            ),
        )
    )

    assert resolved.provider == "anthropic"
    assert resolved.reasoning is not None
    assert resolved.reasoning.effort is ModelReasoningEffort.HIGH
    assert resolved.reasoning.display is ModelReasoningDisplay.SUMMARIZED


def test_model_resolver_rejects_missing_provider_key(tmp_path: Path) -> None:
    settings = RuntimeSettings.load(
        env_file=tmp_path / "missing.env",
        environ={},
    )
    resolver = ModelConfigResolver(settings)

    with pytest.raises(AgentRuntimeError) as exc_info:
        resolver.resolve(
            ModelSelection(provider="anthropic", model_name="claude-sonnet-4")
        )

    assert exc_info.value.code == "configuration_error"
    assert "Missing API key" in exc_info.value.safe_message


def test_model_resolver_missing_key_message_points_to_settings(
    tmp_path: Path,
) -> None:
    settings = RuntimeSettings.load(
        env_file=tmp_path / "missing.env",
        environ={},
    )
    resolver = ModelConfigResolver(settings)

    with pytest.raises(AgentRuntimeError) as exc_info:
        resolver.resolve(
            ModelSelection(provider="anthropic", model_name="claude-sonnet-4")
        )

    assert exc_info.value.safe_message == (
        "Missing API key for model provider 'anthropic'. "
        "Add one in Settings -> Provider keys."
    )


def test_model_resolver_user_key_satisfies_credentials_without_env_key(
    tmp_path: Path,
) -> None:
    # BYOK: a stored user key for the provider passes the gate even when the
    # deployment has no env key configured.
    settings = RuntimeSettings.load(
        env_file=tmp_path / "missing.env",
        environ={},
    )
    resolver = ModelConfigResolver(settings)

    resolved = resolver.resolve(
        ModelSelection(provider="anthropic", model_name="claude-sonnet-4"),
        user_key_providers=frozenset({"anthropic"}),
    )

    assert resolved.provider == "anthropic"
    assert resolved.model_name == "claude-sonnet-4"


def test_model_resolver_user_key_for_other_provider_does_not_unlock(
    tmp_path: Path,
) -> None:
    settings = RuntimeSettings.load(
        env_file=tmp_path / "missing.env",
        environ={},
    )
    resolver = ModelConfigResolver(settings)

    with pytest.raises(AgentRuntimeError) as exc_info:
        resolver.resolve(
            ModelSelection(provider="anthropic", model_name="claude-sonnet-4"),
            user_key_providers=frozenset({"openai"}),
        )

    assert exc_info.value.code == "configuration_error"


def test_model_resolver_env_key_still_satisfies_without_user_key() -> None:
    # Precedence is user > env at injection time; availability-wise either
    # source passes the gate.
    settings = RuntimeSettings.load(environ={"ANTHROPIC_API_KEY": "sk-ant-test"})
    resolver = ModelConfigResolver(settings)

    resolved = resolver.resolve(
        ModelSelection(provider="anthropic", model_name="claude-sonnet-4"),
        user_key_providers=frozenset(),
    )

    assert resolved.provider == "anthropic"


def test_evaluation_projection_settings_are_dark_and_unconsented_by_default() -> None:
    settings = RuntimeSettings.load(environ={})

    assert settings.evaluation.projection_enabled is False
    assert settings.evaluation.user_consented is False
    assert settings.evaluation.allow_development_runs is False
    assert settings.evaluation.profile_id == "desktop-local-profile"


def test_evaluation_projection_settings_are_typed_from_startup_environment() -> None:
    settings = RuntimeSettings.load(
        environ={
            "RUNTIME_EVALUATION_PROJECTION_ENABLED": "true",
            "RUNTIME_EVALUATION_USER_CONSENTED": "true",
            "RUNTIME_EVALUATION_ALLOW_DEVELOPMENT_RUNS": "true",
            "RUNTIME_EVALUATION_PROFILE_ID": "profile_local_1",
            "RUNTIME_EVALUATION_PROJECT_ID": "project_alpha",
            "RUNTIME_EVALUATION_POLICY_REVISION": "projection-policy-v2",
            "RUNTIME_EVALUATION_REDACTION_REVISION": "redaction-v3",
            "RUNTIME_EVALUATION_MAX_EVENTS_PER_RUN": "500",
            "RUNTIME_EVALUATION_MAX_PROJECTION_ATTEMPTS": "2",
            "RUNTIME_EVALUATION_PROJECTION_LEASE_SECONDS": "30",
            "RUNTIME_EVALUATION_PROJECTION_CLAIM_BATCH": "5",
        }
    )

    assert settings.evaluation.projection_enabled is True
    assert settings.evaluation.user_consented is True
    assert settings.evaluation.allow_development_runs is True
    assert settings.evaluation.profile_id == "profile_local_1"
    assert settings.evaluation.project_id == "project_alpha"
    assert settings.evaluation.policy_revision == "projection-policy-v2"
    assert settings.evaluation.redaction_revision == "redaction-v3"
    assert settings.evaluation.max_events_per_run == 500
    assert settings.evaluation.max_projection_attempts == 2
    assert settings.evaluation.projection_lease_seconds == 30
    assert settings.evaluation.projection_claim_batch == 5


def test_local_release_control_is_explicit_configured_and_never_production() -> None:
    enabled = RuntimeSettings.load(
        environ={
            "RUNTIME_ENVIRONMENT": "development",
            "RUNTIME_HARNESS_RELEASE_CONFIG_PATH": "/tmp/release-config.json",
            "RUNTIME_LOCAL_RELEASE_CONTROL_ENABLED": "true",
        }
    )
    assert enabled.evaluation.local_release_control_enabled is True
    assert enabled.evaluation.release_config_path == "/tmp/release-config.json"

    with pytest.raises(
        ValueError,
        match="cannot be enabled in production",
    ):
        RuntimeSettings.load(
            environ={
                "RUNTIME_ENVIRONMENT": "production",
                "RUNTIME_HARNESS_RELEASE_CONFIG_PATH": "/tmp/release-config.json",
                "RUNTIME_LOCAL_RELEASE_CONTROL_ENABLED": "true",
            }
        )

    with pytest.raises(
        ValueError,
        match="requires a release configuration path",
    ):
        RuntimeSettings.load(
            environ={
                "RUNTIME_ENVIRONMENT": "development",
                "RUNTIME_LOCAL_RELEASE_CONTROL_ENABLED": "true",
            }
        )

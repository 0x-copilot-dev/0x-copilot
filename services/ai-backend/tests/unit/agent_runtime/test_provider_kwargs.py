"""PR 4.3 — provider-specific training-opt-out kwargs.

Single small surface, single small file. The dictionary is the
contract; we assert the shape verbatim so a silent provider rename
trips a failing test instead of the agent silently dropping the
opt-out signal.
"""

from __future__ import annotations

from agent_runtime.execution.provider_kwargs import workspace_model_kwargs


class TestWorkspaceModelKwargs:
    def test_no_overrides_returns_empty(self) -> None:
        assert (
            workspace_model_kwargs(provider="openai", workspace_behavior_overrides=None)
            == {}
        )
        assert (
            workspace_model_kwargs(provider="openai", workspace_behavior_overrides={})
            == {}
        )

    def test_opt_in_returns_empty(self) -> None:
        assert (
            workspace_model_kwargs(
                provider="openai",
                workspace_behavior_overrides={"training_data_opt_out": False},
            )
            == {}
        )

    def test_openai_opt_out_sets_store_false(self) -> None:
        assert workspace_model_kwargs(
            provider="openai",
            workspace_behavior_overrides={"training_data_opt_out": True},
        ) == {"model_kwargs": {"store": False}}

    def test_anthropic_opt_out_sets_disable_training_header(self) -> None:
        assert workspace_model_kwargs(
            provider="anthropic",
            workspace_behavior_overrides={"training_data_opt_out": True},
        ) == {"extra_headers": {"anthropic-disable-training": "true"}}

    def test_gemini_opt_out_returns_empty_with_no_provider_flag(self) -> None:
        # Gemini doesn't expose a first-class flag yet — the helper
        # documents the absence by returning an empty dict. Operators
        # rely on the workspace-level data-residency contract instead.
        assert (
            workspace_model_kwargs(
                provider="gemini",
                workspace_behavior_overrides={"training_data_opt_out": True},
            )
            == {}
        )

    def test_unknown_provider_returns_empty(self) -> None:
        # Forward-compat: a provider not in the table just returns no
        # opt-out kwargs. The model-call middleware logs a warning so
        # operators see they're missing coverage.
        assert (
            workspace_model_kwargs(
                provider="grok",
                workspace_behavior_overrides={"training_data_opt_out": True},
            )
            == {}
        )

    def test_returned_dict_is_a_copy(self) -> None:
        # Callers mutate the returned dict (chat-model factory adds
        # provider-specific keys onto it). Make sure we don't share
        # state with the next call.
        a = workspace_model_kwargs(
            provider="openai",
            workspace_behavior_overrides={"training_data_opt_out": True},
        )
        a["model_kwargs"]["polluted"] = True
        b = workspace_model_kwargs(
            provider="openai",
            workspace_behavior_overrides={"training_data_opt_out": True},
        )
        assert "polluted" not in b["model_kwargs"]

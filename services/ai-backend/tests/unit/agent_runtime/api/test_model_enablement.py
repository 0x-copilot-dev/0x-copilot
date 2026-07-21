"""PR-2C — ModelEnablementResolver + EnabledModelsNormalizer.

Pure, fast unit tests. The resolver's two invariants (local always
enabled, workspace default always enabled) must hold in BOTH the
explicit-selection and heuristic modes, or a user could curate
themselves out of a working picker.
"""

from __future__ import annotations

import pytest

from agent_runtime.api.model_enablement import ModelEnablementResolver
from runtime_api.schemas.runs import ModelCatalogItem
from runtime_api.schemas.workspace_defaults import (
    DefaultModelSelection,
    EnabledModelsNormalizer,
)


def _item(
    model_id: str,
    provider: str,
    *,
    release_date: str | None = None,
    model_name: str | None = None,
) -> ModelCatalogItem:
    return ModelCatalogItem(
        id=model_id,
        provider=provider,
        model_name=model_name or model_id,
        name=model_id,
        configured=True,
        release_date=release_date,
    )


def _catalog() -> tuple[ModelCatalogItem, ...]:
    # Ids double as model_names here (bare, no slug) so the tests can point a
    # workspace default at any of them — DefaultModelSelection.model_name is a
    # normalized id and forbids the "vendor/model" slash form.
    return (
        _item("gpt-a", "openai", release_date="2026-05-01"),
        _item("gpt-b", "openai", release_date="2026-03-01"),
        _item("gpt-c", "openai", release_date="2026-01-01"),
        _item("claude-a", "anthropic", release_date="2026-04-01"),
        _item("claude-b", "anthropic", release_date="2026-02-01"),
        _item("llama-3.3-70b", "ollama"),
    )


def _enabled_ids(items: tuple[ModelCatalogItem, ...]) -> set[str]:
    return {item.id for item in items if item.enabled}


class TestExplicitSelection:
    def test_enables_exactly_the_named_ids_plus_invariants(self) -> None:
        result = ModelEnablementResolver.apply(
            _catalog(),
            enabled_models=("gpt-b",),
            default_model=DefaultModelSelection(provider="openai", model_name="gpt-c"),
        )
        # Named + the always-on default + the always-on local model.
        assert _enabled_ids(result) == {"gpt-b", "gpt-c", "llama-3.3-70b"}

    def test_matches_by_model_name_too(self) -> None:
        catalog = (_item("vendor/x", "openrouter", model_name="x-1.0"),)
        result = ModelEnablementResolver.apply(
            catalog, enabled_models=("x-1.0",), default_model=None
        )
        assert result[0].enabled is True

    def test_empty_selection_disables_all_but_invariants(self) -> None:
        result = ModelEnablementResolver.apply(
            _catalog(),
            enabled_models=(),
            default_model=DefaultModelSelection(
                provider="anthropic", model_name="claude-a"
            ),
        )
        # Empty list = "disabled everything" — but the default and local
        # models survive, so the picker is never empty.
        assert _enabled_ids(result) == {"claude-a", "llama-3.3-70b"}


class TestHeuristic:
    def test_enables_newest_two_per_provider_plus_local(self) -> None:
        result = ModelEnablementResolver.apply(
            _catalog(), enabled_models=None, default_model=None
        )
        assert _enabled_ids(result) == {
            "gpt-a",  # newest openai
            "gpt-b",  # 2nd newest openai
            "claude-a",  # newest anthropic
            "claude-b",  # 2nd newest anthropic
            "llama-3.3-70b",  # local always on
        }
        # The 3rd-newest openai is excluded.
        assert "gpt-c" not in _enabled_ids(result)

    def test_default_model_survives_even_if_not_newest(self) -> None:
        result = ModelEnablementResolver.apply(
            _catalog(),
            enabled_models=None,
            default_model=DefaultModelSelection(provider="openai", model_name="gpt-c"),
        )
        # gpt-c is the oldest openai (would be excluded by newest-2) but it is
        # the workspace default, so it must be enabled.
        assert "gpt-c" in _enabled_ids(result)

    def test_missing_release_dates_sort_last(self) -> None:
        catalog = (
            _item("p/no-date", "openai"),
            _item("p/dated", "openai", release_date="2020-01-01"),
        )
        result = ModelEnablementResolver.apply(
            catalog, enabled_models=None, default_model=None
        )
        # Only 2 openai models, both fit under newest-2 → both enabled.
        assert _enabled_ids(result) == {"p/no-date", "p/dated"}


class TestEnabledModelsNormalizer:
    def test_none_passes_through(self) -> None:
        assert EnabledModelsNormalizer.coerce(None) is None

    def test_strips_dedupes_preserving_order(self) -> None:
        assert EnabledModelsNormalizer.coerce([" a ", "b", "a", "  b", "c"]) == (
            "a",
            "b",
            "c",
        )

    def test_empty_list_stays_empty_tuple(self) -> None:
        assert EnabledModelsNormalizer.coerce([]) == ()

    def test_rejects_empty_string_entry(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            EnabledModelsNormalizer.coerce(["ok", "   "])

    def test_rejects_non_string_entry(self) -> None:
        with pytest.raises(ValueError, match="must be strings"):
            EnabledModelsNormalizer.coerce(["ok", 42])

    def test_rejects_non_list(self) -> None:
        with pytest.raises(ValueError, match="list of model ids"):
            EnabledModelsNormalizer.coerce("gpt-4o")

    def test_caps_overlong_id(self) -> None:
        with pytest.raises(ValueError, match="at most"):
            EnabledModelsNormalizer.coerce(["x" * 201])

    def test_caps_list_length(self) -> None:
        with pytest.raises(ValueError, match="at most 500"):
            EnabledModelsNormalizer.coerce([f"m{i}" for i in range(501)])

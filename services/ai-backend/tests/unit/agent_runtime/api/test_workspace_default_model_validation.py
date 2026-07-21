"""Workspace default-model validation is coupled to the catalog SSOT.

``WorkspaceCoordinator._validate_workspace_default_model`` accepts exactly the
models ``ModelCatalog.build`` exposes to the picker. The headline guarantee: the
runtime default (``settings.default_model``) is always in that catalog by
construction, so it can never be rejected — the old "Default model name is not
in the catalog" 422 for a perfectly valid default is closed for good.

The known, documented edges are asserted too:

* Native providers (openai/anthropic/gemini) that the run path's provider
  allowlist accepts pass validation for every catalog entry.
* The catalog now advertises **only** providers the run path can execute:
  ``ModelCatalog.build`` filters source records to
  ``ModelConfigResolver.supports_provider``. The previously-divergent
  ``groq`` / ``xai`` entries never reach the picker, so the cross-surface
  divergence is closed — every advertised provider normalizes.
"""

from __future__ import annotations

import pytest

from agent_runtime.api.litellm_model_source import CatalogModelRecord
from agent_runtime.api.model_catalog import ModelCatalog
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.in_memory import InMemoryRuntimeApiStore
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    DefaultModelSelection,
    UpdateWorkspaceDefaultsRequest,
)
from starlette import status

from agent_runtime.api.workspace_coordinator import WorkspaceCoordinator

# Providers the run path's ``_normalize_provider`` alias table accepts as a
# workspace default via a constructible (slash-free) id. openrouter is also
# run-path-executable but its ids are ``vendor/model`` slugs the
# DefaultModelSelection normalizer forbids, so it is excluded from the
# per-entry native-validation sweep below.
_RUN_PATH_PROVIDERS = frozenset({"openai", "anthropic", "gemini"})
# The DefaultModelSelection id normalizer forbids these characters, so those
# model ids can never be constructed as a workspace default in the first place.
_UNCONSTRUCTIBLE_ID_CHARS = ("/", "~", " ")


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(environ={"OPENAI_API_KEY": "sk-test"})


class _FakeSource:
    """A stand-in ``CatalogModelSource`` returning fixed records (no LiteLLM)."""

    def __init__(self, records: tuple[CatalogModelRecord, ...]) -> None:
        self._records = records

    def records(self) -> tuple[CatalogModelRecord, ...]:
        return self._records


def _coordinator(settings: RuntimeSettings) -> WorkspaceCoordinator:
    return WorkspaceCoordinator(
        persistence=InMemoryRuntimeApiStore(),
        settings=settings,
        model_resolver=ModelConfigResolver(settings),
    )


def _request(provider: str, model_name: str) -> UpdateWorkspaceDefaultsRequest:
    return UpdateWorkspaceDefaultsRequest(
        default_model=DefaultModelSelection(provider=provider, model_name=model_name),
        retention_days=90,
    )


class TestRuntimeDefaultNeverRejected:
    """The core fix: the settings default always validates (old 422 repro)."""

    def test_runtime_default_passes_validation(self) -> None:
        settings = _settings()
        coordinator = _coordinator(settings)
        request = _request(
            settings.default_model.provider,
            settings.default_model.model_name,
        )
        # Must not raise. Before the SSOT coupling, a default that a stale
        # hardcoded list didn't happen to include produced a 422 here.
        coordinator._validate_workspace_default_model(request)

    def test_runtime_default_is_actually_in_the_catalog(self) -> None:
        # Belt-and-braces: prove the "never rejected" guarantee is backed by
        # presence in the very catalog the validator checks against.
        settings = _settings()
        catalog_names = {item.model_name for item in ModelCatalog.build(settings)}
        assert settings.default_model.model_name in catalog_names


class TestEveryNativeCatalogEntryValidates:
    """Every run-path-supported catalog entry is an acceptable workspace default."""

    def test_native_catalog_entries_pass(self) -> None:
        settings = _settings()
        coordinator = _coordinator(settings)
        checked = 0
        for item in ModelCatalog.build(settings):
            if item.provider not in _RUN_PATH_PROVIDERS:
                continue
            if any(ch in item.model_name for ch in _UNCONSTRUCTIBLE_ID_CHARS):
                continue
            request = _request(item.provider, item.model_name)
            # No entry the catalog advertises (and the run path can serve)
            # may be rejected — that is what "single source of truth" means.
            coordinator._validate_workspace_default_model(request)
            checked += 1
        # Guard against a vacuous pass if the catalog ever comes back empty.
        assert checked >= 3


class TestValidationRejections:
    """Negative cases: the validator still rejects what is not selectable."""

    def test_unknown_model_name_rejected(self) -> None:
        settings = _settings()
        coordinator = _coordinator(settings)
        request = _request("openai", "gpt-9000-not-shipping")
        with pytest.raises(RuntimeApiError) as excinfo:
            coordinator._validate_workspace_default_model(request)
        assert excinfo.value.http_status == status.HTTP_422_UNPROCESSABLE_CONTENT

    def test_unknown_provider_rejected(self) -> None:
        settings = _settings()
        coordinator = _coordinator(settings)
        request = _request("totally-fake", "gpt-5.4-mini")
        with pytest.raises(RuntimeApiError) as excinfo:
            coordinator._validate_workspace_default_model(request)
        assert excinfo.value.http_status == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestCatalogRunPathDivergenceClosed:
    """The catalog advertises only providers the run path can execute.

    Formerly a pinned, failing-when-fixed divergence: the models.dev source
    surfaced ``groq``/``xai`` models the run-path allowlist rejects. The curated
    LiteLLM registry lists only run-path providers, and ``ModelCatalog.build``
    still filters to ``ModelConfigResolver.supports_provider`` as a defensive
    guard, so the picker can never show an un-runnable model. These assert the
    divergence stays closed.
    """

    def test_every_advertised_provider_normalizes(self) -> None:
        # The SSOT contract: for every catalog entry, the run path's provider
        # allowlist accepts the provider (``_normalize_provider`` does not raise).
        settings = _settings()
        providers = {item.provider for item in ModelCatalog.build(settings)}
        assert providers, "catalog must not be empty"
        for provider in providers:
            # Must not raise — an un-normalizable provider would be an
            # advertised-but-un-runnable model, the divergence this closes.
            ModelConfigResolver._normalize_provider(provider)

    def test_rejected_providers_absent_from_catalog(self) -> None:
        # The curated LiteLLM registry lists only run-path providers, so
        # groq/xai are never advertised in the first place.
        settings = _settings()
        providers = {item.provider for item in ModelCatalog.build(settings)}
        assert "groq" not in providers
        assert "xai" not in providers

    def test_filter_removes_a_rejected_provider_from_any_source(self) -> None:
        # Guard against a vacuous pass: prove the filter does real work by
        # injecting a source that DOES ship a run-path-rejected provider and
        # confirming ``build`` drops it while keeping an accepted one. (The real
        # curated source never emits groq/xai, so the filter is exercised here
        # with a fake source rather than relying on catalog contents.)
        settings = _settings()
        ModelCatalog.configure_source(
            _FakeSource(
                (
                    CatalogModelRecord(
                        provider="groq",
                        model_id="llama-3.3-70b-versatile",
                        display_name="Llama 3.3 70B",
                    ),
                    CatalogModelRecord(
                        provider="anthropic",
                        model_id="claude-opus-4-8",
                        display_name="Claude Opus 4.8",
                    ),
                )
            )
        )
        catalog_providers = {item.provider for item in ModelCatalog.build(settings)}
        assert "groq" not in catalog_providers
        assert "anthropic" in catalog_providers

    def test_every_native_catalog_entry_is_a_valid_workspace_default(self) -> None:
        # End-to-end: a constructible catalog entry (slash-free id) is always an
        # acceptable workspace default — no advertised, run-path-supported model
        # is rejected by the coordinator.
        settings = _settings()
        coordinator = _coordinator(settings)
        checked = 0
        for item in ModelCatalog.build(settings):
            if any(ch in item.model_name for ch in _UNCONSTRUCTIBLE_ID_CHARS):
                continue
            coordinator._validate_workspace_default_model(
                _request(item.provider, item.model_name)
            )
            checked += 1
        assert checked >= 3

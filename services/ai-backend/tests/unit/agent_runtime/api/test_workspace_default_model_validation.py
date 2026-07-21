"""Workspace default-model validation is coupled to the catalog SSOT.

``WorkspaceCoordinator._validate_workspace_default_model`` accepts exactly the
models ``ModelCatalog.build`` exposes to the picker. The headline guarantee: the
runtime default (``settings.default_model``) is always in that catalog by
construction, so it can never be rejected — the old "Default model name is not
in the catalog" 422 for a perfectly valid default is closed for good.

The known, documented edges are asserted too:

* Native providers (openai/anthropic/gemini) that the run path's provider
  allowlist accepts pass validation for every catalog entry.
* ``groq`` / ``xai`` catalog entries are advertised by the catalog but rejected
  here on the provider allowlist (``_normalize_provider``) — a pre-existing
  cross-surface divergence tracked as follow-up, pinned here so a future fix
  is a deliberate change with a failing-then-passing test.
"""

from __future__ import annotations

import pytest

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

# Providers the run path's ``_normalize_provider`` alias table accepts; the
# catalog also advertises groq/xai, which the allowlist rejects (documented).
_RUN_PATH_PROVIDERS = frozenset({"openai", "anthropic", "gemini"})
# The DefaultModelSelection id normalizer forbids these characters, so those
# model ids can never be constructed as a workspace default in the first place.
_UNCONSTRUCTIBLE_ID_CHARS = ("/", "~", " ")


def _settings() -> RuntimeSettings:
    return RuntimeSettings.load(environ={"OPENAI_API_KEY": "sk-test"})


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

    def test_groq_entry_advertised_by_catalog_but_rejected_on_provider(self) -> None:
        # Documents the known divergence: the catalog lists groq models, but
        # the run-path provider allowlist does not accept "groq", so it cannot
        # currently be a workspace default. A future SSOT-unification PR should
        # flip this test to expect success.
        settings = _settings()
        groq_items = [
            item
            for item in ModelCatalog.build(settings)
            if item.provider == "groq"
            and not any(ch in item.model_name for ch in _UNCONSTRUCTIBLE_ID_CHARS)
        ]
        if not groq_items:
            pytest.skip("snapshot shipped no constructible groq entry")
        coordinator = _coordinator(settings)
        request = _request(groq_items[0].provider, groq_items[0].model_name)
        with pytest.raises(RuntimeApiError) as excinfo:
            coordinator._validate_workspace_default_model(request)
        assert excinfo.value.http_status == status.HTTP_422_UNPROCESSABLE_CONTENT

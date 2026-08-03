"""The storage-backend registry — the seam a store is swapped through.

The point of these tests is the claim the registry makes: adding a backend
(putting a SQL store back, say) is a provider module plus one registration, with
no edit to any dispatch logic. A fake backend registered here and built through
``RuntimeAdapterFactory.from_settings`` is that claim, executed.
"""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError


@pytest.fixture
def registry():
    from runtime_adapters.registry import RuntimeStorageBackendRegistry

    return RuntimeStorageBackendRegistry()


def _backend(name: str, module: str, aliases: tuple[str, ...] = ()):
    from runtime_adapters.registry import RuntimeStorageBackend

    return RuntimeStorageBackend(name=name, provider_module=module, aliases=aliases)


def _install_provider(monkeypatch, module_name: str, **attributes) -> ModuleType:
    """Register an importable stub module for the duration of one test."""

    module = ModuleType(module_name)
    for key, value in attributes.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, module_name, module)
    return module


class TestResolution:
    def test_the_canonical_name_resolves(self, registry) -> None:
        registry.register(_backend("mystore", "pkg.mystore_provider"))

        assert registry.resolve("mystore").provider_module == "pkg.mystore_provider"

    def test_an_alias_resolves_to_the_same_backend(self, registry) -> None:
        registry.register(_backend("mystore", "pkg.p", aliases=("legacy_name",)))

        assert registry.resolve("legacy_name").name == "mystore"

    def test_an_unknown_name_is_a_configuration_error_listing_what_exists(
        self, registry
    ) -> None:
        registry.register(_backend("mystore", "pkg.p", aliases=("legacy_name",)))

        with pytest.raises(AgentRuntimeError) as excinfo:
            registry.resolve("nope")

        assert excinfo.value.code is RuntimeErrorCode.CONFIGURATION_ERROR
        # The operator gets told what IS available, not just what is not.
        assert "mystore" in str(excinfo.value)
        assert "legacy_name" in str(excinfo.value)

    def test_names_reports_canonical_names_and_selectors_reports_aliases_too(
        self, registry
    ) -> None:
        registry.register(_backend("b", "pkg.b", aliases=("b_old",)))
        registry.register(_backend("a", "pkg.a"))

        assert registry.names() == ("a", "b")
        assert registry.selectors() == ("a", "b", "b_old")


class TestRegistrationSafety:
    def test_one_backend_may_not_steal_another_backends_selector(
        self, registry
    ) -> None:
        registry.register(_backend("first", "pkg.first", aliases=("shared",)))

        with pytest.raises(AgentRuntimeError) as excinfo:
            registry.register(_backend("second", "pkg.second", aliases=("shared",)))

        assert excinfo.value.code is RuntimeErrorCode.CONFIGURATION_ERROR
        # The original owner keeps the selector; a rejected registration must
        # not half-apply.
        assert registry.resolve("shared").name == "first"

    def test_reregistering_the_same_name_replaces_it_and_drops_stale_aliases(
        self, registry
    ) -> None:
        registry.register(_backend("mystore", "pkg.old", aliases=("gone",)))
        registry.register(_backend("mystore", "pkg.new"))

        assert registry.resolve("mystore").provider_module == "pkg.new"
        with pytest.raises(AgentRuntimeError):
            registry.resolve("gone")


class TestProviderContract:
    def test_build_ports_delegates_to_the_provider_module(
        self, registry, monkeypatch
    ) -> None:
        seen: dict[str, object] = {}

        def build_ports(settings, *, role="api"):
            seen["settings"] = settings
            seen["role"] = role
            return "PORTS"

        _install_provider(monkeypatch, "fake_pkg.provider", build_ports=build_ports)
        registry.register(_backend("fake", "fake_pkg.provider"))

        result = registry.build_ports("fake", "SETTINGS", role="worker")

        assert result == "PORTS"
        assert seen == {"settings": "SETTINGS", "role": "worker"}

    def test_a_provider_without_build_ports_fails_closed(
        self, registry, monkeypatch
    ) -> None:
        _install_provider(monkeypatch, "fake_pkg.empty")
        registry.register(_backend("fake", "fake_pkg.empty"))

        with pytest.raises(AgentRuntimeError) as excinfo:
            registry.build_ports("fake", "SETTINGS")

        assert excinfo.value.code is RuntimeErrorCode.CONFIGURATION_ERROR
        assert "build_ports" in str(excinfo.value)

    def test_the_provider_module_is_imported_only_when_selected(self, registry) -> None:
        # A module path that does not exist is fine to REGISTER — the import is
        # deferred. This is what keeps a driver off an unrelated deployment's
        # import graph.
        registry.register(_backend("absent", "no_such_package.provider"))

        assert registry.resolve("absent").name == "absent"
        with pytest.raises(ModuleNotFoundError):
            registry.provider("absent")


class TestShippedBackends:
    def test_the_three_built_ins_are_registered_with_the_legacy_alias(self) -> None:
        from runtime_adapters.registry import STORAGE_BACKENDS

        assert STORAGE_BACKENDS.names() == ("file", "in_memory_async", "postgres")
        assert STORAGE_BACKENDS.resolve("in_memory").name == "in_memory_async"

    def test_factory_dispatch_goes_through_the_registry(self, monkeypatch) -> None:
        """A backend nobody shipped composes through the untouched factory.

        This is the plug-and-play claim: no edit to ``from_settings``, no
        database, no import of any adapter package.
        """

        from runtime_adapters.factory import RuntimeAdapterFactory
        from runtime_adapters.registry import STORAGE_BACKENDS

        _install_provider(
            monkeypatch,
            "fake_pkg.swapped_in_provider",
            build_ports=lambda settings, *, role="api": ("PORTS", role),
        )

        class _Settings:
            class store:  # noqa: N801 — mimics the settings attribute shape
                backend = "swapped_in"

        monkeypatch.setattr(
            STORAGE_BACKENDS,
            "_by_selector",
            {
                **STORAGE_BACKENDS._by_selector,
                "swapped_in": _backend("swapped_in", "fake_pkg.swapped_in_provider"),
            },
        )

        assert RuntimeAdapterFactory.from_settings(_Settings(), role="worker") == (
            "PORTS",
            "worker",
        )

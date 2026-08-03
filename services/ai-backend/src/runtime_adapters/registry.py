"""Named runtime storage backends — the one seam for swapping a store in or out.

A *backend* is a name (what ``RUNTIME_STORE_BACKEND`` selects) plus the dotted
path of a **provider module**. Providers are imported only when their backend is
actually selected, so a desktop process never imports a server database driver
and a server process never imports the desktop's file/sqlite stack.

A provider module must expose::

    def build_ports(settings: RuntimeSettings) -> RuntimePorts: ...

**Adding a backend** — for example putting a SQL store back — is therefore three
things and no edits to any dispatch logic:

1. write ``runtime_adapters/providers/<name>_provider.py`` with that function;
2. call :meth:`RuntimeStorageBackendRegistry.register` with its name and module
   path (the built-ins below are registered exactly this way);
3. set ``RUNTIME_STORE_BACKEND=<name>``.

Removing one is deleting its provider module and its registration line. Nothing
else in the service branches on a backend name.

**Checkpointers are the sibling half of a backend** and live in
``agent_runtime.execution.checkpointing``, keyed by the same names. They are
registered separately because LangGraph savers are composed inside the domain,
which must not import ``runtime_adapters`` (adapters depend on the domain, never
the reverse). A complete backend therefore registers in both places.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from types import ModuleType
from typing import TYPE_CHECKING, Protocol

from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError

if TYPE_CHECKING:  # pragma: no cover — typing only, avoids an import cycle
    from agent_runtime.settings import RuntimeSettings
    from runtime_adapters.factory import RuntimePorts


class StorageBackendProvider(Protocol):
    """Structural contract every provider module satisfies."""

    def build_ports(
        self, settings: "RuntimeSettings", *, role: str = "api"
    ) -> "RuntimePorts":
        """Compose the full port surface for this backend."""


@dataclass(frozen=True)
class RuntimeStorageBackend:
    """One selectable storage backend.

    ``provider_module`` is a dotted path imported lazily on first use —
    never at registration time, so registering a backend costs nothing and
    pulls in no dependency.
    """

    name: str
    provider_module: str
    aliases: tuple[str, ...] = field(default=())

    @property
    def selectors(self) -> tuple[str, ...]:
        """Every name that selects this backend, canonical name first."""

        return (self.name, *self.aliases)


class UnknownStorageBackend(AgentRuntimeError):
    """Raised when ``RUNTIME_STORE_BACKEND`` names no registered backend."""

    def __init__(self, requested: str, known: tuple[str, ...]) -> None:
        super().__init__(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            f"Unsupported runtime store backend {requested!r}. "
            f"Registered backends: {', '.join(known)}.",
            retryable=False,
        )


class RuntimeStorageBackendRegistry:
    """Resolve a backend name to its lazily-imported provider module."""

    def __init__(self) -> None:
        self._backends: dict[str, RuntimeStorageBackend] = {}
        self._by_selector: dict[str, RuntimeStorageBackend] = {}

    def register(self, backend: RuntimeStorageBackend) -> None:
        """Register *backend*, replacing any previous entry under its name.

        Replacement is deliberate: a test (or an out-of-tree distribution) can
        substitute a backend without unregistering first. Selector collisions
        across *different* backends are rejected, because silently shadowing
        another backend's alias is never intended.
        """

        for selector in backend.selectors:
            existing = self._by_selector.get(selector)
            if existing is not None and existing.name != backend.name:
                raise AgentRuntimeError(
                    RuntimeErrorCode.CONFIGURATION_ERROR,
                    f"Storage backend selector {selector!r} is already claimed "
                    f"by {existing.name!r}.",
                    retryable=False,
                )
        previous = self._backends.get(backend.name)
        if previous is not None:
            for selector in previous.selectors:
                self._by_selector.pop(selector, None)
        self._backends[backend.name] = backend
        for selector in backend.selectors:
            self._by_selector[selector] = backend

    def names(self) -> tuple[str, ...]:
        """Canonical names of every registered backend, sorted."""

        return tuple(sorted(self._backends))

    def selectors(self) -> tuple[str, ...]:
        """Every accepted selector including aliases, sorted."""

        return tuple(sorted(self._by_selector))

    def resolve(self, name: str) -> RuntimeStorageBackend:
        """Return the backend selected by *name*, or raise a typed error."""

        backend = self._by_selector.get(name)
        if backend is None:
            raise UnknownStorageBackend(name, self.selectors())
        return backend

    def provider(self, name: str) -> ModuleType:
        """Import and return the provider module for *name*."""

        return importlib.import_module(self.resolve(name).provider_module)

    def build_ports(
        self, name: str, settings: "RuntimeSettings", *, role: str = "api"
    ) -> "RuntimePorts":
        """Compose the port surface for *name* via its provider module.

        ``role`` ("api" / "worker") is passed through for backends that stamp it
        on a connection identity; backends with no such notion ignore it.
        """

        return self._callable(name, "build_ports")(settings, role=role)

    def _callable(self, name: str, attribute: str) -> Callable[..., object]:
        provider = self.provider(name)
        builder = getattr(provider, attribute, None)
        if builder is None:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                f"Storage backend {name!r} provider "
                f"{provider.__name__!r} does not define {attribute}().",
                retryable=False,
            )
        return builder  # type: ignore[no-any-return]


STORAGE_BACKENDS = RuntimeStorageBackendRegistry()
"""The process-wide registry the factory and checkpointer selection both read."""

STORAGE_BACKENDS.register(
    RuntimeStorageBackend(
        name="in_memory_async",
        provider_module="runtime_adapters.providers.in_memory_provider",
        # ``in_memory`` is the long-standing alias; both route to the
        # async-native InMemoryRuntimeApiStore.
        aliases=("in_memory",),
    )
)
STORAGE_BACKENDS.register(
    RuntimeStorageBackend(
        name="file",
        provider_module="runtime_adapters.providers.file_provider",
    )
)


__all__ = (
    "STORAGE_BACKENDS",
    "RuntimeStorageBackend",
    "RuntimeStorageBackendRegistry",
    "StorageBackendProvider",
    "UnknownStorageBackend",
)

"""The seam that decides whether the per-tool MCP path runs at all.

Every other per-tool test INJECTS ``McpPerToolCollaborators``. That is the right
thing for testing the pipeline — but it means the one line that decides whether
production ever builds a plane is the one line no test executes. It was wrong
for the entire life of the feature: ``_proxy_collaborators`` read ``backend_url``
off the REGISTRY, while the URL lives on the PROVIDER. So it returned ``None`` on
every real run, per-tool registration always declined, and no MCP tool was ever
registered — with a fully green suite, because every test replaced the seam.

A live journey is what surfaced it: the model read all 52 Linear descriptors and
never called one, because there was nothing on its tool surface to call.

These tests drive the real function against the real registry shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.capabilities.mcp.per_tool_registration import (
    McpPerToolCollaborators,
)
from agent_runtime.execution.contracts import AgentRuntimeContext
from agent_runtime.execution.factory import _proxy_collaborators

BACKEND_URL = "http://127.0.0.1:8100"


@dataclass
class _BackendishProvider:
    """A provider at the shape ``BackendMcpProvider`` presents."""

    backend_url: str = BACKEND_URL
    timeout_seconds: float = 7.0


@dataclass
class _LocalProvider:
    """A provider with no proxy behind it — a seeded or in-memory source."""

    name: str = "local"


@dataclass
class _Registry:
    """``DynamicMcpRegistry``'s shape: providers, and NO ``backend_url``."""

    providers: tuple[object, ...]


class ProxyPlaneFixture:
    """Builds the seam's inputs."""

    @staticmethod
    def context(runtime_context_admin: AgentRuntimeContext) -> AgentRuntimeContext:
        return runtime_context_admin

    @staticmethod
    def resolve(
        registry: object, context: AgentRuntimeContext
    ) -> McpPerToolCollaborators | None:
        return _proxy_collaborators(registry, runtime_context=context)


class TestTheRegistryShapeProductionActuallyUses:
    """The regression this file exists for."""

    def test_a_registry_of_providers_yields_a_plane(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        """The exact shape the worker builds: providers, no URL on the registry.

        Reading ``backend_url`` off the registry returns ``None`` here, which is
        what silently disabled the whole per-tool path in production.
        """

        registry = _Registry(providers=(_BackendishProvider(),))

        plane = ProxyPlaneFixture.resolve(registry, runtime_context_admin)

        assert plane is not None
        assert isinstance(plane, McpPerToolCollaborators)

    def test_the_plane_carries_the_providers_endpoint(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        """A plane pointed somewhere else is worse than no plane at all."""

        registry = _Registry(providers=(_BackendishProvider(),))

        plane = ProxyPlaneFixture.resolve(registry, runtime_context_admin)

        assert plane is not None
        assert plane.client_factory is not None
        assert getattr(plane.client_factory, "backend_url", None) == BACKEND_URL

    def test_the_providers_timeout_is_honoured(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        registry = _Registry(providers=(_BackendishProvider(timeout_seconds=7.0),))

        plane = ProxyPlaneFixture.resolve(registry, runtime_context_admin)

        assert plane is not None
        assert getattr(plane.client_factory, "timeout_seconds", None) == 7.0

    def test_a_backend_provider_is_found_behind_local_ones(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        """Order must not decide it: a seeded provider can be listed first."""

        registry = _Registry(providers=(_LocalProvider(), _BackendishProvider()))

        assert ProxyPlaneFixture.resolve(registry, runtime_context_admin) is not None


class TestDecliningStaysDeclining:
    """Declining is a real outcome, and it must stay reachable."""

    def test_no_backend_provider_declines(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        """No proxy to route through: inventing a URL would register a surface
        that cannot answer, which is worse than registering nothing."""

        registry = _Registry(providers=(_LocalProvider(),))

        assert ProxyPlaneFixture.resolve(registry, runtime_context_admin) is None

    def test_a_registry_without_providers_declines(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        assert ProxyPlaneFixture.resolve(object(), runtime_context_admin) is None

    def test_a_blank_backend_url_declines(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        """Whitespace is not an endpoint — it would resolve to the local host."""

        registry = _Registry(providers=(_BackendishProvider(backend_url="   "),))

        assert ProxyPlaneFixture.resolve(registry, runtime_context_admin) is None


class TestTheRegistryMayCarryItDirectly:
    """A future registry that exposes the URL itself keeps working."""

    def test_a_registry_level_url_is_still_read(
        self, runtime_context_admin: AgentRuntimeContext
    ) -> None:
        assert (
            ProxyPlaneFixture.resolve(_BackendishProvider(), runtime_context_admin)
            is not None
        )

"""Scoped memory route planning and deterministic test stores."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import Field, field_validator

from agent_runtime.execution.contracts import AgentRuntimeContext, RuntimeContract, RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.context.memory.constants import Defaults, Keys, Messages, Values
from agent_runtime.context.memory.contracts import (
    MemoryPathPolicy,
    MemoryScope,
    MemoryValueNormalizer,
)
from agent_runtime.context.memory.policy import MemoryPolicyAuthorizer

BackendBuilder = Callable[["MemoryRoutePlan"], object]


class MemoryBackendRoute(RuntimeContract):
    """Route from a virtual memory path prefix to an isolated backend scope."""

    path_prefix: str
    scope: MemoryScope
    policy: MemoryPathPolicy

    @field_validator(Keys.Field.PATH_PREFIX)
    @classmethod
    def _normalize_path_prefix(cls, value: object) -> str:
        return MemoryValueNormalizer.normalize_path_prefix(value, Keys.Field.PATH_PREFIX)


class MemoryRoutePlan(RuntimeContract):
    """Typed route plan used to construct a Deep Agents composite backend."""

    routes: tuple[MemoryBackendRoute, ...]
    memory_paths: tuple[str, ...] = (Values.Path.MEMORIES,)

    @field_validator("memory_paths", mode="before")
    @classmethod
    def _normalize_memory_paths(cls, value: object) -> tuple[str, ...]:
        return tuple(
            MemoryValueNormalizer.normalize_path_prefix(item, "memory_paths")
            for item in MemoryValueNormalizer.coerce_iterable(value, "memory_paths")
        )

    @classmethod
    def for_context(
        cls,
        context: AgentRuntimeContext,
        *,
        assistant_id: str = Defaults.ASSISTANT_ID,
    ) -> "MemoryRoutePlan":
        """Build default routes for user memory, org policies, and agent skills."""

        policies = {policy.path_prefix: policy for policy in MemoryPolicyAuthorizer.default_policies()}
        return cls(
            routes=(
                MemoryBackendRoute(
                    path_prefix=Values.Path.MEMORIES,
                    scope=MemoryScope.for_user(context),
                    policy=policies[Values.Path.MEMORIES],
                ),
                MemoryBackendRoute(
                    path_prefix=Values.Path.POLICIES,
                    scope=MemoryScope.for_organization(context),
                    policy=policies[Values.Path.POLICIES],
                ),
                MemoryBackendRoute(
                    path_prefix=Values.Path.SKILLS,
                    scope=MemoryScope.for_agent(context, assistant_id=assistant_id),
                    policy=policies[Values.Path.SKILLS],
                ),
            ),
            memory_paths=(Values.Path.MEMORIES,),
        )

    def route_for_path(self, path: str) -> MemoryBackendRoute:
        """Return the most specific route for a virtual memory path."""

        normalized_path = MemoryValueNormalizer.normalize_memory_path(path, Keys.Field.PATH)
        candidates = tuple(
            route for route in self.routes if normalized_path.startswith(route.path_prefix)
        )
        if not candidates:
            raise AgentRuntimeError(
                RuntimeErrorCode.CONFIGURATION_ERROR,
                Messages.Errors.MEMORY_POLICY_DENIED,
                retryable=False,
            )
        return max(candidates, key=lambda route: len(route.path_prefix))


class ScopedMemoryBackendFactory:
    """Create request-scoped memory backend configuration for Deep Agents."""

    def __init__(
        self,
        *,
        backend_builder: BackendBuilder | None = None,
        assistant_id: str = Defaults.ASSISTANT_ID,
    ) -> None:
        self.backend_builder = backend_builder
        self.assistant_id = assistant_id

    def create(self, context: object) -> object:
        """Create a route plan or delegate to an injected concrete backend builder."""

        if not isinstance(context, AgentRuntimeContext):
            raise AgentRuntimeError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "Runtime context is invalid.",
                retryable=False,
            )
        plan = MemoryRoutePlan.for_context(context, assistant_id=self.assistant_id)
        if self.backend_builder is None:
            return plan
        return self.backend_builder(plan)


class MemoryFileSnapshot(RuntimeContract):
    """Versioned in-memory file snapshot for deterministic concurrency tests."""

    path: str
    content: str
    version: int = Field(ge=0)

    @field_validator(Keys.Field.PATH)
    @classmethod
    def _normalize_path(cls, value: object) -> str:
        return MemoryValueNormalizer.normalize_memory_path(value, Keys.Field.PATH)

    @field_validator(Keys.Field.CONTENT)
    @classmethod
    def _normalize_content(cls, value: object) -> str:
        return MemoryValueNormalizer.normalize_nonempty_string(value, Keys.Field.CONTENT)


class VersionedMemoryStore:
    """Small optimistic-concurrency store used by memory unit tests and fakes."""

    def __init__(self) -> None:
        self._files: dict[str, MemoryFileSnapshot] = {}

    def read(self, path: str) -> MemoryFileSnapshot | None:
        normalized_path = MemoryValueNormalizer.normalize_memory_path(path, Keys.Field.PATH)
        return self._files.get(normalized_path)

    def write(
        self,
        *,
        path: str,
        content: str,
        expected_version: int | None = None,
    ) -> MemoryFileSnapshot:
        normalized_path = MemoryValueNormalizer.normalize_memory_path(path, Keys.Field.PATH)
        existing = self._files.get(normalized_path)
        current_version = existing.version if existing is not None else 0
        if expected_version is not None and expected_version != current_version:
            raise AgentRuntimeError(
                RuntimeErrorCode.EXTERNAL_SERVICE_ERROR,
                Messages.Errors.CONCURRENT_WRITE,
                retryable=True,
            )

        next_snapshot = MemoryFileSnapshot(
            path=normalized_path,
            content=content,
            version=current_version + 1,
        )
        self._files[normalized_path] = next_snapshot
        return next_snapshot

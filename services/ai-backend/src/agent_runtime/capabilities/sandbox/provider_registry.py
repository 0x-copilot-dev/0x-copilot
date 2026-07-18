"""Process-start provider selection and the in-memory session store.

The registry binds exactly one provider for the process, chosen from trusted
deployment config. Model input cannot reach it. If the configured provider is
unavailable (extra not installed, unsupported), selection fails closed with a
typed error and the capability stays absent.
"""

from __future__ import annotations

from collections.abc import Mapping

from agent_runtime.capabilities.sandbox.config import RemoteSandboxConfig
from agent_runtime.capabilities.sandbox.contracts import (
    ManagedSandboxSession,
    SandboxError,
    SandboxErrorCode,
    SandboxProviderId,
)
from agent_runtime.capabilities.sandbox.ports import SandboxProviderPort


class SandboxProviderRegistry:
    """Holds the single selected provider for the process."""

    def __init__(
        self, provider: SandboxProviderPort, provider_id: SandboxProviderId
    ) -> None:
        self._provider = provider
        self._provider_id = provider_id

    @property
    def provider(self) -> SandboxProviderPort:
        """The bound provider adapter."""

        return self._provider

    @property
    def provider_id(self) -> SandboxProviderId:
        """The bound provider id (for events/audit)."""

        return self._provider_id

    @classmethod
    def from_config(
        cls,
        config: RemoteSandboxConfig,
        *,
        overrides: Mapping[SandboxProviderId, SandboxProviderPort] | None = None,
    ) -> SandboxProviderRegistry:
        """Select the provider named by ``config``.

        ``overrides`` lets tests bind a fake provider without touching the
        production wiring; production passes ``None`` and only ``langsmith`` is
        constructible. Raises ``SANDBOX_DISABLED`` when the capability is off and
        ``SANDBOX_PROVIDER_UNCONFIGURED`` when the provider cannot be built.
        """

        if not config.is_active or config.provider is None:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_DISABLED,
                "Remote sandbox execution is disabled.",
            )
        provider_id = config.provider
        if overrides is not None and provider_id in overrides:
            return cls(overrides[provider_id], provider_id)
        provider = cls._construct(provider_id, config)
        return cls(provider, provider_id)

    @staticmethod
    def _construct(
        provider_id: SandboxProviderId, config: RemoteSandboxConfig
    ) -> SandboxProviderPort:
        if provider_id is SandboxProviderId.LANGSMITH:
            # Lazy import so the langsmith[sandbox] extra is only required when
            # the provider is actually selected.
            from agent_runtime.capabilities.sandbox.providers.langsmith import (
                LangSmithSandboxProvider,
            )

            return LangSmithSandboxProvider(region=config.region)
        raise SandboxError(  # pragma: no cover - enum is exhaustive today
            SandboxErrorCode.SANDBOX_PROVIDER_UNCONFIGURED,
            "No adapter is available for the configured sandbox provider.",
        )


class InMemorySandboxSessionStore:
    """Non-durable session projection for tests/dev and the in-process reaper.

    Implements :class:`SandboxSessionStore`. Production swaps a
    postgres/file-store adapter behind the same port.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, ManagedSandboxSession] = {}

    async def upsert(self, session: ManagedSandboxSession) -> None:
        self._sessions[session.session_id] = session

    async def get(self, session_id: str) -> ManagedSandboxSession | None:
        return self._sessions.get(session_id)

    async def list_non_terminal(self) -> tuple[ManagedSandboxSession, ...]:
        return tuple(
            session
            for session in self._sessions.values()
            if session.cleanup_state != "deleted"
        )

    async def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

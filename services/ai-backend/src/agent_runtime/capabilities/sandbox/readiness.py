"""Truthful readiness probe for the remote sandbox capability.

Configuration alone is deliberately not readiness.  A process can advertise the
remote sandbox only after it has constructed the selected provider adapter and
the adapter has proved the isolation controls required by the capability
contract.  Session creation remains per-run; this probe never provisions a
billable sandbox merely to answer a health question.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from agent_runtime.capabilities.sandbox.config import RemoteSandboxConfig
from agent_runtime.capabilities.sandbox.contracts import (
    SandboxError,
    SandboxErrorCode,
    SandboxProviderId,
)
from agent_runtime.capabilities.sandbox.ports import SandboxProviderPort
from agent_runtime.capabilities.sandbox.provider_registry import SandboxProviderRegistry


class SandboxReadinessReason(StrEnum):
    """Bounded, safe reasons for an unavailable sandbox capability."""

    DISABLED = "disabled"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    ISOLATION_UNVERIFIED = "isolation_unverified"
    OPENAI_HOSTED_CONTAINER_CONTROL_GAP = "openai_hosted_container_control_gap"


@dataclass(frozen=True)
class SandboxCapabilityReadiness:
    """Result of the non-provisioning sandbox startup probe."""

    available: bool
    reason: SandboxReadinessReason | None = None
    provider_id: SandboxProviderId | None = None

    @classmethod
    def assess(
        cls,
        config: RemoteSandboxConfig,
        *,
        provider_overrides: Mapping[SandboxProviderId, SandboxProviderPort]
        | None = None,
    ) -> "SandboxCapabilityReadiness":
        """Assess the actual provider seam without creating a sandbox session.

        The provider registry is the construction authority shared with the
        runtime seam, including its isolation check.  An invalid SDK,
        unavailable provider, or unverifiable policy therefore cannot be
        misreported as a ready capability.
        """

        if not config.is_active:
            return cls(available=False, reason=SandboxReadinessReason.DISABLED)
        try:
            registry = SandboxProviderRegistry.from_config(
                config, overrides=provider_overrides
            )
        except SandboxError as exc:
            if config.provider is SandboxProviderId.OPENAI_HOSTED_CONTAINER:
                reason = SandboxReadinessReason.OPENAI_HOSTED_CONTAINER_CONTROL_GAP
            else:
                reason = (
                    SandboxReadinessReason.ISOLATION_UNVERIFIED
                    if exc.code is SandboxErrorCode.SANDBOX_POLICY_UNSUPPORTED
                    else SandboxReadinessReason.PROVIDER_UNAVAILABLE
                )
            return cls(available=False, reason=reason, provider_id=config.provider)
        return cls(available=True, provider_id=registry.provider_id)


__all__ = ("SandboxCapabilityReadiness", "SandboxReadinessReason")

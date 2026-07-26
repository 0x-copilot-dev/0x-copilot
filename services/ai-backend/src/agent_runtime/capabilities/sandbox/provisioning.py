"""One-use provisioning authority for providers that require a durable gate.

The service creates a capability only after it has verified an isolation
attestation and persisted a file-native cleanup reservation.  A guarded
provider consumes that exact opaque capability once; a direct caller cannot
substitute a request, an attestation, or a cleanup record.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from agent_runtime.capabilities.sandbox.contracts import (
    SandboxCreateRequest,
    SandboxError,
    SandboxErrorCode,
    SandboxIsolationAttestation,
)


_REMOTE_EXECUTION_SERVICE_AUTHORITY = object()

if TYPE_CHECKING:
    from agent_runtime.capabilities.sandbox.cleanup_store import (
        SandboxCleanupSchedule,
    )
    from agent_runtime.capabilities.sandbox.ports import SandboxHandle


@dataclass(frozen=True)
class SandboxProvisioningGrant:
    """Private facts released only after a valid capability is consumed."""

    request: SandboxCreateRequest
    attestation: SandboxIsolationAttestation
    cleanup: "SandboxCleanupSchedule"


class SandboxProvisioningCapability:
    """Opaque, one-use authority minted by one service instance.

    The capability has no public request/attestation/cleanup accessors.  The
    receiving provider must present the same in-process authority object that
    issued it, and successful consumption removes it from the issuer's ledger.
    """

    __slots__ = ("__authority", "__sequence")

    def __init__(
        self, *, authority: "SandboxProvisioningAuthority", sequence: int
    ) -> None:
        self.__authority = authority
        self.__sequence = sequence

    def consume(
        self, *, authority: "SandboxProvisioningAuthority"
    ) -> SandboxProvisioningGrant:
        """Return the facts once, only to the issuing service authority."""

        if authority is not self.__authority:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_POLICY_UNSUPPORTED,
                "Sandbox provisioning capability was not issued for this service.",
            )
        return authority._consume(self.__sequence, self)


class SandboxProvisioningAuthority:
    """In-process issuer owned exclusively by ``RemoteExecutionService``."""

    def __init__(self, *, _service_authority: object | None = None) -> None:
        self._is_remote_execution_service = (
            _service_authority is _REMOTE_EXECUTION_SERVICE_AUTHORITY
        )
        self._next_sequence = 0
        self._unconsumed: dict[
            int, tuple[SandboxProvisioningCapability, SandboxProvisioningGrant]
        ] = {}

    @property
    def is_remote_execution_service_authority(self) -> bool:
        """Whether this issuer carries the service-only runtime authority."""

        return self._is_remote_execution_service

    def mint(
        self,
        *,
        request: SandboxCreateRequest,
        attestation: SandboxIsolationAttestation,
        cleanup: "SandboxCleanupSchedule",
    ) -> SandboxProvisioningCapability:
        """Create one capability after service-side checks and durable reservation."""

        if not self._is_remote_execution_service:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_POLICY_UNSUPPORTED,
                "Sandbox provisioning authority is not owned by the lifecycle service.",
            )

        self._next_sequence += 1
        capability = SandboxProvisioningCapability(
            authority=self, sequence=self._next_sequence
        )
        self._unconsumed[self._next_sequence] = (
            capability,
            SandboxProvisioningGrant(
                request=request,
                attestation=attestation,
                cleanup=cleanup,
            ),
        )
        return capability

    def _consume(
        self, sequence: int, capability: SandboxProvisioningCapability
    ) -> SandboxProvisioningGrant:
        issued = self._unconsumed.pop(sequence, None)
        if issued is None or issued[0] is not capability:
            raise SandboxError(
                SandboxErrorCode.SANDBOX_POLICY_UNSUPPORTED,
                "Sandbox provisioning capability is expired or already consumed.",
            )
        return issued[1]


def _new_remote_execution_service_authority() -> SandboxProvisioningAuthority:
    """Return the runtime-sealed authority held only by lifecycle composition.

    The private factory is intentionally not a public provider construction
    surface.  Its object-identity seal is checked by both the issuer and the
    OpenAI provider; method names alone cannot satisfy that check.
    """

    return SandboxProvisioningAuthority(
        _service_authority=_REMOTE_EXECUTION_SERVICE_AUTHORITY
    )


@runtime_checkable
class SandboxGuardedProvisioner(Protocol):
    """Optional provider extension that accepts only service-minted capability."""

    def bind_provisioning_authority(
        self, authority: SandboxProvisioningAuthority
    ) -> None:
        """Bind the one service instance permitted to mint capabilities."""
        ...

    def cleanup_owner_marker(self, request: SandboxCreateRequest) -> str:
        """Return a deterministic reservation marker safe for provider recovery."""
        ...

    async def provision_with_capability(
        self, capability: SandboxProvisioningCapability
    ) -> "SandboxHandle":
        """Provision exactly once after consuming one service capability."""
        ...

    async def recover_provisioning(self, owner_marker: str) -> None:
        """Reap resources for a persisted pre-bind cleanup reservation."""
        ...


__all__ = (
    "SandboxProvisioningAuthority",
    "SandboxProvisioningCapability",
    "SandboxProvisioningGrant",
    "SandboxGuardedProvisioner",
)

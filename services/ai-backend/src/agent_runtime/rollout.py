"""Closed E2 rollout configuration and startup validation.

This module is deliberately configuration-only.  It resolves the ten E2
capability lanes once from a trusted process environment and supplies a pure
startup validator.  It does *not* choose a tool, construct an executor, or
perform an effect.  Later E2 slices consume :class:`E2RolloutSettings` from the
already-resolved :class:`agent_runtime.settings.RuntimeSettings` instance.

Compatibility is one-way: an existing v2 control can raise the effective mode
for the corresponding canonical path, but an E2 setting can never lower it.
That makes an explicit ``off`` unable to restore a retired direct-write path.
``SURFACES_V2`` intentionally has no presentation-v2.1 bridge: it owns the
existing v2 ledger/surface path, not the new v2.1 presentation producer.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import ClassVar

from pydantic import Field, field_validator

from agent_runtime.execution.contracts import RuntimeContract


class RolloutMode(StrEnum):
    """Closed authority posture for one independently deployable capability."""

    OFF = "off"
    SHADOW = "shadow"
    ENFORCE = "enforce"

    @property
    def rank(self) -> int:
        """Return the monotonic authority rank used by the legacy bridge."""

        return {
            RolloutMode.OFF: 0,
            RolloutMode.SHADOW: 1,
            RolloutMode.ENFORCE: 2,
        }[self]

    @classmethod
    def most_authoritative(cls, *modes: "RolloutMode") -> "RolloutMode":
        """Return the highest-authority mode without accepting open strings."""

        return max(modes, key=lambda mode: mode.rank)


class RolloutCapability(StrEnum):
    """The exact, closed E2 D1 capability set.

    Do not add a global/master capability here.  A rollout must name the one
    capability it changes so a rollback can only reduce that capability's
    authority.
    """

    ARTIFACT_REPOSITORY = "artifact_repository"
    OPERATION_GATEWAY = "operation_gateway"
    EFFECT_STAGER = "effect_stager"
    EFFECT_COMMIT = "effect_commit"
    PRESENTATION_V2_1 = "presentation_v2_1"
    WORKSPACE_OVERLAY = "workspace_overlay"
    WORKSPACE_COMMIT = "workspace_commit"
    MCP_GATEWAY = "mcp_gateway"
    SANDBOX_ADAPTER = "sandbox_adapter"
    BROWSER_ADAPTER = "browser_adapter"


class E2RolloutSettings(RuntimeContract):
    """The one typed, closed settings contract for all ten E2 lanes.

    These are intentionally all dark by default.  The model fields are a
    release-gate invariant: changing their count requires updating the E2 D1
    canary instead of silently creating a new coarse switch.
    """

    artifact_repository: RolloutMode = RolloutMode.OFF
    operation_gateway: RolloutMode = RolloutMode.OFF
    effect_stager: RolloutMode = RolloutMode.OFF
    effect_commit: RolloutMode = RolloutMode.OFF
    presentation_v2_1: RolloutMode = RolloutMode.OFF
    workspace_overlay: RolloutMode = RolloutMode.OFF
    workspace_commit: RolloutMode = RolloutMode.OFF
    mcp_gateway: RolloutMode = RolloutMode.OFF
    sandbox_adapter: RolloutMode = RolloutMode.OFF
    browser_adapter: RolloutMode = RolloutMode.OFF

    _ENV_BY_CAPABILITY: ClassVar[Mapping[RolloutCapability, str]] = {
        RolloutCapability.ARTIFACT_REPOSITORY: "ARTIFACT_REPOSITORY_MODE",
        RolloutCapability.OPERATION_GATEWAY: "OPERATION_GATEWAY_MODE",
        RolloutCapability.EFFECT_STAGER: "EFFECT_STAGER_MODE",
        RolloutCapability.EFFECT_COMMIT: "EFFECT_COMMIT_MODE",
        RolloutCapability.PRESENTATION_V2_1: "PRESENTATION_V2_1_MODE",
        RolloutCapability.WORKSPACE_OVERLAY: "WORKSPACE_OVERLAY_MODE",
        RolloutCapability.WORKSPACE_COMMIT: "WORKSPACE_COMMIT_MODE",
        RolloutCapability.MCP_GATEWAY: "MCP_GATEWAY_MODE",
        RolloutCapability.SANDBOX_ADAPTER: "SANDBOX_ADAPTER_MODE",
        RolloutCapability.BROWSER_ADAPTER: "BROWSER_ADAPTER_MODE",
    }

    @classmethod
    def environment_name(cls, capability: RolloutCapability) -> str:
        """Return the only environment variable accepted for ``capability``."""

        return cls._ENV_BY_CAPABILITY[capability]

    @classmethod
    def capabilities(cls) -> tuple[RolloutCapability, ...]:
        """Return the stable declaration order used in diagnostics and tests."""

        return tuple(RolloutCapability)

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str]
    ) -> tuple["E2RolloutSettings", tuple[RolloutCapability, ...]]:
        """Parse only the ten E2 environment controls.

        Returns the requested settings plus capabilities explicitly requested in
        ``enforce``.  The latter is used to distinguish a new cohort request
        from a pre-existing v2 compatibility bridge at startup.
        """

        values: dict[str, RolloutMode] = {}
        explicit_enforced: list[RolloutCapability] = []
        for capability in cls.capabilities():
            env_name = cls.environment_name(capability)
            raw = (environment.get(env_name) or "").strip().lower()
            try:
                mode = RolloutMode(raw or RolloutMode.OFF.value)
            except ValueError as exc:
                raise RolloutConfigurationError(
                    f"{env_name} must be one of: off, shadow, enforce."
                ) from exc
            values[capability.value] = mode
            if mode is RolloutMode.ENFORCE:
                explicit_enforced.append(capability)
        return cls.model_validate(values), tuple(explicit_enforced)

    def mode_for(self, capability: RolloutCapability) -> RolloutMode:
        """Return a capability mode without accepting an arbitrary key."""

        return getattr(self, capability.value)

    def as_safe_mapping(self) -> dict[str, str]:
        """Return low-cardinality, secret-free structured-log values."""

        return {
            capability.value: self.mode_for(capability).value
            for capability in self.capabilities()
        }

    def enforced(self) -> tuple[RolloutCapability, ...]:
        """Return only enforced capabilities in declaration order."""

        return tuple(
            capability
            for capability in self.capabilities()
            if self.mode_for(capability) is RolloutMode.ENFORCE
        )

    def shadowed(self) -> tuple[RolloutCapability, ...]:
        """Return only shadowed capabilities in declaration order."""

        return tuple(
            capability
            for capability in self.capabilities()
            if self.mode_for(capability) is RolloutMode.SHADOW
        )


class LegacyRolloutInputs(RuntimeContract):
    """Trusted snapshot of pre-E2 controls used by the one-way bridge.

    This class deliberately holds booleans/modes only.  It never retains a
    broker URL, token, path, provider credential, or tenant identifier.
    """

    surfaces_v2: bool = True
    artifact_effects_v2: bool = False
    artifact_drafts_v2: bool = False
    operation_gateway_mode: RolloutMode = RolloutMode.OFF
    workspace_effect_mode: RolloutMode = RolloutMode.OFF
    sandbox_adapter_active: bool = False
    browser_adapter_active: bool = False
    legacy_workspace_write_path_configured: bool = False

    def canonical_floor(self, capability: RolloutCapability) -> RolloutMode:
        """Return the old control's authoritative floor for one E2 lane."""

        if capability is RolloutCapability.ARTIFACT_REPOSITORY:
            return RolloutMode.ENFORCE if self.artifact_effects_v2 else RolloutMode.OFF
        if capability in {
            RolloutCapability.OPERATION_GATEWAY,
            RolloutCapability.EFFECT_STAGER,
            RolloutCapability.EFFECT_COMMIT,
            RolloutCapability.MCP_GATEWAY,
        }:
            return self.operation_gateway_mode
        if capability in {
            RolloutCapability.WORKSPACE_OVERLAY,
            RolloutCapability.WORKSPACE_COMMIT,
        }:
            return self.workspace_effect_mode
        if capability is RolloutCapability.SANDBOX_ADAPTER:
            return (
                RolloutMode.ENFORCE if self.sandbox_adapter_active else RolloutMode.OFF
            )
        if capability is RolloutCapability.BROWSER_ADAPTER:
            return (
                RolloutMode.ENFORCE if self.browser_adapter_active else RolloutMode.OFF
            )
        # SURFACES_V2 is intentionally NOT a presentation-v2.1 bridge.  It
        # cannot become a hidden master switch for this rollout contract.
        return RolloutMode.OFF

    def possible_direct_writes(self) -> frozenset[RolloutCapability]:
        """Return conservative legacy writable paths that may still exist.

        The set deliberately errs toward rejection.  A new explicit ``enforce``
        may not coexist with one of these paths; it must first use the matching
        already-canonical legacy control until the consumer is migrated.
        """

        direct: set[RolloutCapability] = set()
        if not self.artifact_drafts_v2:
            direct.add(RolloutCapability.ARTIFACT_REPOSITORY)
        if self.operation_gateway_mode is not RolloutMode.ENFORCE:
            direct.update(
                {
                    RolloutCapability.OPERATION_GATEWAY,
                    RolloutCapability.EFFECT_STAGER,
                    RolloutCapability.EFFECT_COMMIT,
                    RolloutCapability.MCP_GATEWAY,
                }
            )
        if not self.surfaces_v2:
            direct.update(
                {
                    RolloutCapability.EFFECT_STAGER,
                    RolloutCapability.EFFECT_COMMIT,
                }
            )
        if (
            self.workspace_effect_mode is not RolloutMode.ENFORCE
            and self.legacy_workspace_write_path_configured
        ):
            direct.update(
                {
                    RolloutCapability.WORKSPACE_OVERLAY,
                    RolloutCapability.WORKSPACE_COMMIT,
                }
            )
        if (
            self.browser_adapter_active
            and self.operation_gateway_mode is not RolloutMode.ENFORCE
        ):
            direct.add(RolloutCapability.BROWSER_ADAPTER)
        if (
            self.sandbox_adapter_active
            and self.operation_gateway_mode is not RolloutMode.ENFORCE
        ):
            direct.add(RolloutCapability.SANDBOX_ADAPTER)
        return frozenset(direct)


class RolloutProvenance(RuntimeContract):
    """Safe provenance for one startup-resolved E2 configuration."""

    explicit_enforced: tuple[RolloutCapability, ...] = Field(default_factory=tuple)
    legacy_elevated: tuple[RolloutCapability, ...] = Field(default_factory=tuple)

    @field_validator("explicit_enforced", "legacy_elevated")
    @classmethod
    def _unique_capabilities(
        cls, values: tuple[RolloutCapability, ...]
    ) -> tuple[RolloutCapability, ...]:
        if len(values) != len(set(values)):
            raise ValueError("rollout capability provenance must be unique")
        return values

    def is_explicitly_enforced(self, capability: RolloutCapability) -> bool:
        """Whether a new E2 environment setting, not the bridge, requested it."""

        return capability in self.explicit_enforced


class E2RolloutResolution(RuntimeContract):
    """One immutable startup snapshot of requested/bridged E2 modes."""

    modes: E2RolloutSettings = Field(default_factory=E2RolloutSettings)
    provenance: RolloutProvenance = Field(default_factory=RolloutProvenance)

    @classmethod
    def resolve(
        cls,
        *,
        environment: Mapping[str, str],
        legacy: LegacyRolloutInputs,
    ) -> "E2RolloutResolution":
        """Resolve explicit controls once and apply the monotonic v2 bridge."""

        requested, explicit_enforced = E2RolloutSettings.from_environment(environment)
        effective: dict[str, RolloutMode] = {}
        legacy_elevated: list[RolloutCapability] = []
        for capability in E2RolloutSettings.capabilities():
            requested_mode = requested.mode_for(capability)
            legacy_mode = legacy.canonical_floor(capability)
            mode = RolloutMode.most_authoritative(requested_mode, legacy_mode)
            effective[capability.value] = mode
            if legacy_mode.rank > requested_mode.rank:
                legacy_elevated.append(capability)

        resolution = cls(
            modes=E2RolloutSettings.model_validate(effective),
            provenance=RolloutProvenance(
                explicit_enforced=explicit_enforced,
                legacy_elevated=tuple(legacy_elevated),
            ),
        )
        RolloutStartupValidator.validate_static(resolution, legacy=legacy)
        return resolution

    def safe_diagnostics(self, *, process: str) -> dict[str, object]:
        """Return bounded startup facts suitable for structured process logs."""

        return {
            "rollout_contract": "e2.v1",
            "process": process,
            "modes": self.modes.as_safe_mapping(),
            "enforce_count": len(self.modes.enforced()),
            "shadow_count": len(self.modes.shadowed()),
            "explicit_enforce_count": len(self.provenance.explicit_enforced),
            "legacy_bridge_count": len(self.provenance.legacy_elevated),
            "legacy_bridge_capabilities": tuple(
                capability.value for capability in self.provenance.legacy_elevated
            ),
        }


class RolloutStartupReadiness(RuntimeContract):
    """Composition-root facts needed to admit an explicit E2 enforce mode."""

    descriptor_catalog_ready: bool = False
    executor_registry_ready: bool = False
    artifact_repository_ready: bool = False
    operation_gateway_ready: bool = False
    effect_stager_ready: bool = False
    effect_commit_ready: bool = False
    presentation_v2_1_ready: bool = False
    workspace_overlay_ready: bool = False
    workspace_commit_ready: bool = False
    workspace_c2_native_attested: bool = False
    mcp_gateway_ready: bool = False
    sandbox_adapter_ready: bool = False
    browser_adapter_ready: bool = False

    def ready_for(self, capability: RolloutCapability) -> bool:
        """Return whether this process has the capability's concrete seam."""

        return {
            RolloutCapability.ARTIFACT_REPOSITORY: self.artifact_repository_ready,
            RolloutCapability.OPERATION_GATEWAY: self.operation_gateway_ready,
            RolloutCapability.EFFECT_STAGER: self.effect_stager_ready,
            RolloutCapability.EFFECT_COMMIT: self.effect_commit_ready,
            RolloutCapability.PRESENTATION_V2_1: self.presentation_v2_1_ready,
            RolloutCapability.WORKSPACE_OVERLAY: self.workspace_overlay_ready,
            RolloutCapability.WORKSPACE_COMMIT: self.workspace_commit_ready,
            RolloutCapability.MCP_GATEWAY: self.mcp_gateway_ready,
            RolloutCapability.SANDBOX_ADAPTER: self.sandbox_adapter_ready,
            RolloutCapability.BROWSER_ADAPTER: self.browser_adapter_ready,
        }[capability]


class RolloutConfigurationError(ValueError):
    """Safe, startup-fatal rollout configuration error."""


class RolloutStartupValidator:
    """Validate D1/D8/D9 safety invariants before a process accepts work."""

    _PRODUCER_CAPABILITIES = frozenset(
        {
            RolloutCapability.ARTIFACT_REPOSITORY,
            RolloutCapability.OPERATION_GATEWAY,
            RolloutCapability.EFFECT_STAGER,
            RolloutCapability.EFFECT_COMMIT,
            RolloutCapability.WORKSPACE_OVERLAY,
            RolloutCapability.WORKSPACE_COMMIT,
            RolloutCapability.MCP_GATEWAY,
            RolloutCapability.SANDBOX_ADAPTER,
            RolloutCapability.BROWSER_ADAPTER,
        }
    )

    @classmethod
    def is_producer(cls, capability: RolloutCapability) -> bool:
        """Whether enforcing this lane needs descriptor/executor proof."""

        return capability in cls._PRODUCER_CAPABILITIES

    @classmethod
    def validate_static(
        cls,
        resolution: E2RolloutResolution,
        *,
        legacy: LegacyRolloutInputs,
    ) -> None:
        """Reject invalid mode combinations before adapter composition.

        This checks only values already available in ``RuntimeSettings``.  Port
        and native-attestation checks are intentionally deferred to
        :meth:`validate_startup`, the worker composition root.
        """

        modes = resolution.modes
        explicit_enforced = set(resolution.provenance.explicit_enforced)
        if RolloutCapability.EFFECT_COMMIT in explicit_enforced:
            cls._require_enforce(
                modes,
                RolloutCapability.EFFECT_STAGER,
                dependent=RolloutCapability.EFFECT_COMMIT,
            )
            cls._require_enforce(
                modes,
                RolloutCapability.OPERATION_GATEWAY,
                dependent=RolloutCapability.EFFECT_COMMIT,
            )
        for dependent in (
            RolloutCapability.EFFECT_STAGER,
            RolloutCapability.MCP_GATEWAY,
            RolloutCapability.WORKSPACE_OVERLAY,
        ):
            if dependent in explicit_enforced:
                cls._require_enforce(
                    modes,
                    RolloutCapability.OPERATION_GATEWAY,
                    dependent=dependent,
                )
        if RolloutCapability.WORKSPACE_COMMIT in explicit_enforced:
            for dependency in (
                RolloutCapability.WORKSPACE_OVERLAY,
                RolloutCapability.EFFECT_STAGER,
                RolloutCapability.EFFECT_COMMIT,
                RolloutCapability.OPERATION_GATEWAY,
            ):
                cls._require_enforce(
                    modes,
                    dependency,
                    dependent=RolloutCapability.WORKSPACE_COMMIT,
                )
        for dependent in (
            RolloutCapability.SANDBOX_ADAPTER,
            RolloutCapability.BROWSER_ADAPTER,
        ):
            if dependent in explicit_enforced:
                for dependency in (
                    RolloutCapability.OPERATION_GATEWAY,
                    RolloutCapability.EFFECT_STAGER,
                    RolloutCapability.EFFECT_COMMIT,
                ):
                    cls._require_enforce(
                        modes,
                        dependency,
                        dependent=dependent,
                    )

        unsafe_overlap = set(resolution.provenance.explicit_enforced).intersection(
            legacy.possible_direct_writes()
        )
        if unsafe_overlap:
            names = ", ".join(sorted(capability.value for capability in unsafe_overlap))
            raise RolloutConfigurationError(
                "E2 enforce would coexist with a legacy writable path for: "
                f"{names}. Migrate the matching legacy path first."
            )

    @classmethod
    def validate_startup(
        cls,
        resolution: E2RolloutResolution,
        *,
        readiness: RolloutStartupReadiness,
    ) -> None:
        """Validate concrete ports/attestation for newly requested enforcement.

        Legacy-only bridge elevation retains its established guards while the
        compatibility path exists.  A new E2 ``enforce`` request receives the
        stricter D1/D8/D9 gate here; it cannot claim activation until the
        descriptor, executor, concrete adapter, and (for workspace) C2/native
        attestation are all present.
        """

        for capability in resolution.provenance.explicit_enforced:
            if capability in cls._PRODUCER_CAPABILITIES and (
                not readiness.descriptor_catalog_ready
                or not readiness.executor_registry_ready
            ):
                raise RolloutConfigurationError(
                    f"{E2RolloutSettings.environment_name(capability)}=enforce "
                    "requires a registered descriptor and executor."
                )
            if not readiness.ready_for(capability):
                raise RolloutConfigurationError(
                    f"{E2RolloutSettings.environment_name(capability)}=enforce "
                    "requires its startup adapter/executor seam."
                )
            if (
                capability is RolloutCapability.WORKSPACE_COMMIT
                and not readiness.workspace_c2_native_attested
            ):
                raise RolloutConfigurationError(
                    "WORKSPACE_COMMIT_MODE=enforce requires C2 isolation and "
                    "native workspace attestation."
                )

    @staticmethod
    def _require_enforce(
        modes: E2RolloutSettings,
        dependency: RolloutCapability,
        *,
        dependent: RolloutCapability,
    ) -> None:
        if modes.mode_for(dependency) is not RolloutMode.ENFORCE:
            raise RolloutConfigurationError(
                f"{E2RolloutSettings.environment_name(dependent)}=enforce "
                f"requires {E2RolloutSettings.environment_name(dependency)}=enforce."
            )


__all__ = (
    "E2RolloutResolution",
    "E2RolloutSettings",
    "LegacyRolloutInputs",
    "RolloutCapability",
    "RolloutConfigurationError",
    "RolloutMode",
    "RolloutProvenance",
    "RolloutStartupReadiness",
    "RolloutStartupValidator",
)

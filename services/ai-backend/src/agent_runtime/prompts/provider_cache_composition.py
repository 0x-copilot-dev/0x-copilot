"""Immutable, signed F2 provider-cache composition facts.

The framework's prompt-cache middleware is a separate cache-control owner.
The pinned public graph-construction seam does not provide a run-local way to
remove its framework cache middleware.  Every signed F2 mode therefore keeps
the framework as the only owner; product decoration and its retry handoff stay
dormant until that supported seam exists.

There is deliberately no default provider rejection adapter.  A provider SDK
error is not cache-specific merely because it is a bad request.  A future
adapter may be added only after it converts a reviewed machine-readable
provider cache-control field into the dedicated product wrapper.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.control_plane.feature_modes import FeatureMode
from agent_runtime.prompts.provider_cache import (
    ProviderCacheAdapterRegistry,
    ProviderCacheOwner,
    ProviderCacheRejectionAdapterRegistry,
)


@dataclass(frozen=True, slots=True)
class ProviderCacheComposition:
    """Run-snapshot-derived ownership facts for one graph construction.

    The immutable rejection registry is intentionally empty in production
    until a supported graph seam and pinned provider transport give us a
    stable cache-specific field to adapt.
    """

    composition_revision: str
    signed_mode: FeatureMode
    cache_owner: ProviderCacheOwner
    framework_prompt_cache_enabled: bool
    cache_registry: ProviderCacheAdapterRegistry
    cache_rejection_adapters: ProviderCacheRejectionAdapterRegistry

    def __post_init__(self) -> None:
        if not self.composition_revision.strip():
            raise ValueError("provider cache composition revision must be non-empty")
        if self.cache_owner is not ProviderCacheOwner.FRAMEWORK:
            raise ValueError(
                "pinned graph construction supports framework cache ownership only"
            )
        if not self.framework_prompt_cache_enabled:
            raise ValueError("framework cache owner requires its middleware")

    @classmethod
    def from_signed_mode(cls, signed_mode: FeatureMode) -> "ProviderCacheComposition":
        """Resolve the only production topology from verified F2 mode facts."""

        return cls(
            composition_revision="provider-cache-composition-v1",
            signed_mode=signed_mode,
            cache_owner=ProviderCacheOwner.FRAMEWORK,
            framework_prompt_cache_enabled=True,
            cache_registry=ProviderCacheAdapterRegistry.default(),
            cache_rejection_adapters=ProviderCacheRejectionAdapterRegistry(),
        )


__all__ = ("ProviderCacheComposition",)

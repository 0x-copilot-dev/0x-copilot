"""Reviewed descriptor-to-stager composition contract for D9.

This is not an executor registry and cannot dispatch an effect.  It records the
one proposal shape each reviewed external descriptor is allowed to hand to the
generic :class:`EffectStager`.  The worker-owned composition test resolves the
corresponding executor from the real registry; keeping these concerns separate
prevents a model-facing adapter from choosing an execution implementation.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorRegistry,
)
from agent_runtime.effects.contracts import validate_proposal_executor_pair
from agent_runtime.surfaces_v2.ledger_models import (
    EffectClass,
    EffectExecutorKind,
    EffectProposalKind,
)


class EffectDescriptorCompositionError(RuntimeError):
    """A reviewed external descriptor lacks a canonical staging path."""


@dataclass(frozen=True)
class EffectDescriptorStageMapping:
    """One exact operation descriptor and its immutable proposal shape."""

    capability: str
    op: str
    executor: EffectExecutorKind
    proposal_kind: EffectProposalKind

    @property
    def key(self) -> tuple[str, str]:
        return (
            OperationDescriptorRegistry.normalize_capability(self.capability),
            OperationDescriptorRegistry.normalize(self.op),
        )


# This is deliberately exact-match rather than executor-wide.  For example,
# BUILTIN currently means only ``stage_rowset_write``; it is not permission to
# route an arbitrary builtin payload to an external connector.
EFFECT_DESCRIPTOR_STAGE_MAPPINGS = (
    EffectDescriptorStageMapping(
        capability="builtin",
        op="call_mcp_tool",
        executor=EffectExecutorKind.MCP,
        proposal_kind=EffectProposalKind.CANONICAL_ARGUMENTS,
    ),
    EffectDescriptorStageMapping(
        capability="builtin",
        op="stage_rowset_write",
        executor=EffectExecutorKind.BUILTIN,
        proposal_kind=EffectProposalKind.ROW_SET,
    ),
    *(
        EffectDescriptorStageMapping(
            capability="workspace",
            op=op,
            executor=EffectExecutorKind.WORKSPACE,
            proposal_kind=EffectProposalKind.WORKSPACE_CHANGE_SET,
        )
        for op in ("write", "edit", "create", "replace", "mkdir", "move", "delete")
    ),
    *(
        EffectDescriptorStageMapping(
            capability="desktop-browser",
            op=op,
            executor=EffectExecutorKind.BROWSER,
            proposal_kind=EffectProposalKind.BROWSER_SUBMISSION,
        )
        for op in ("browser_click", "browser_submit")
    ),
)


def validate_effect_descriptor_staging(
    registry: OperationDescriptorRegistry,
) -> tuple[EffectDescriptorStageMapping, ...]:
    """Validate every external descriptor maps to exactly one legal stager kind."""

    mappings = {mapping.key: mapping for mapping in EFFECT_DESCRIPTOR_STAGE_MAPPINGS}
    effect_entries = tuple(
        entry.descriptor
        for entry in registry.all_entries()
        if entry.descriptor.effect_class
        in {
            EffectClass.UNKNOWN,
            EffectClass.EXTERNAL_REVERSIBLE,
            EffectClass.EXTERNAL_DESTRUCTIVE,
        }
    )
    expected = {
        (
            OperationDescriptorRegistry.normalize_capability(descriptor.capability),
            OperationDescriptorRegistry.normalize(descriptor.op),
        )
        for descriptor in effect_entries
    }
    unexpected = sorted(set(mappings) - expected)
    missing = sorted(expected - set(mappings))
    if missing or unexpected:
        details = []
        if missing:
            details.append(
                "missing stage mapping: " + ", ".join(".".join(key) for key in missing)
            )
        if unexpected:
            details.append(
                "stale stage mapping: " + ", ".join(".".join(key) for key in unexpected)
            )
        raise EffectDescriptorCompositionError("; ".join(details))

    for descriptor in effect_entries:
        key = (
            OperationDescriptorRegistry.normalize_capability(descriptor.capability),
            OperationDescriptorRegistry.normalize(descriptor.op),
        )
        mapping = mappings[key]
        if not descriptor.supports_prepare:
            raise EffectDescriptorCompositionError(
                f"descriptor is not stageable: {key[0]}.{key[1]}"
            )
        if mapping.executor is not descriptor.executor:
            raise EffectDescriptorCompositionError(
                f"executor mismatch: {key[0]}.{key[1]}"
            )
        try:
            validate_proposal_executor_pair(mapping.proposal_kind, mapping.executor)
        except ValueError as exc:
            raise EffectDescriptorCompositionError(
                f"illegal stager mapping: {key[0]}.{key[1]}"
            ) from exc
    return tuple(mappings[key] for key in sorted(mappings))


__all__ = (
    "EFFECT_DESCRIPTOR_STAGE_MAPPINGS",
    "EffectDescriptorCompositionError",
    "EffectDescriptorStageMapping",
    "validate_effect_descriptor_staging",
)

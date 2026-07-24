"""Immutable operation descriptors and exact-match registry."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import Field

from agent_runtime.capabilities.actions.catalog import ACTION_CATALOG, ActionCatalog
from agent_runtime.capabilities.actions.contracts import CatalogActionKind
from agent_runtime.capabilities.operations.contracts import OperationDescriptor
from agent_runtime.capabilities.surfaces.builtin import server_slug
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.ledger_models import (
    EffectClass,
    EffectExecutorKind,
    OperationResultKind,
)


class OperationDescriptorEntry(RuntimeContract):
    """Reviewed descriptor plus policy metadata not present on the A1 wire twin."""

    descriptor: OperationDescriptor
    descriptor_version: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    timeout_ms: int = Field(ge=1, le=600_000)
    unknown_arguments_tighten_to_unknown: bool = False


class OperationDescriptorRegistry:
    """Product-owned exact descriptors with a fail-closed safe default."""

    __slots__ = ("_action_catalog", "_entries")

    def __init__(
        self,
        entries: Iterable[OperationDescriptorEntry] = (),
        *,
        action_catalog: ActionCatalog | None = ACTION_CATALOG,
    ) -> None:
        indexed: dict[tuple[str, str], OperationDescriptorEntry] = {}
        for entry in entries:
            key = self._key(entry.descriptor.capability, entry.descriptor.op)
            if key in indexed:
                raise ValueError(
                    f"duplicate operation descriptor for {key[0]}.{key[1]}"
                )
            if entry.descriptor.executor not in EffectExecutorKind:
                raise ValueError("operation descriptor executor is not registered")
            indexed[key] = entry
        self._entries = indexed
        self._action_catalog = action_catalog

    def resolve(self, capability: str, op: str) -> OperationDescriptor | None:
        """Resolve an exact product descriptor, never a wildcard."""

        entry = self.resolve_entry(capability, op)
        if entry is not None:
            return entry.descriptor
        if self._action_catalog is None:
            return None
        kind = self._action_catalog.lookup(capability, op)
        if kind is None:
            return None
        effect_class = {
            CatalogActionKind.READ: EffectClass.NONE,
            CatalogActionKind.WRITE: EffectClass.EXTERNAL_REVERSIBLE,
            CatalogActionKind.DESTRUCTIVE: EffectClass.EXTERNAL_DESTRUCTIVE,
        }[kind]
        return OperationDescriptor(
            capability=self.normalize(capability),
            op=self.normalize(op),
            executor=EffectExecutorKind.MCP,
            effect_class=effect_class,
            result_kind=OperationResultKind.ACTIVITY,
            supports_prepare=kind is not CatalogActionKind.READ,
            supports_reconcile=kind is not CatalogActionKind.READ,
            required_gate_kinds=(),
            max_inline_result_bytes=16384,
        )

    def resolve_entry(
        self, capability: str, op: str
    ) -> OperationDescriptorEntry | None:
        return self._entries.get(self._key(capability, op))

    def all_entries(self) -> tuple[OperationDescriptorEntry, ...]:
        return tuple(self._entries[key] for key in sorted(self._entries))

    def metric_key(self, capability: str, op: str) -> tuple[str, str]:
        """Return a bounded descriptor/catalog key or one shared unknown bucket."""

        if self.resolve(capability, op) is None:
            return ("unknown", "unknown")
        return self._key(capability, op)

    @classmethod
    def safe_default(cls, *, capability: str, op: str) -> OperationDescriptorEntry:
        """Return the honest unknown descriptor for one unresolved operation."""

        return OperationDescriptorEntry(
            descriptor=OperationDescriptor(
                capability=cls.normalize_capability(capability),
                op=cls.normalize(op),
                executor=EffectExecutorKind.BUILTIN,
                effect_class=EffectClass.UNKNOWN,
                result_kind=OperationResultKind.ACTIVITY,
                supports_prepare=False,
                supports_reconcile=False,
                required_gate_kinds=(),
                max_inline_result_bytes=0,
            ),
            descriptor_version="safe-default-v1",
            display_name="operation",
            timeout_ms=60_000,
            unknown_arguments_tighten_to_unknown=True,
        )

    @staticmethod
    def normalize(value: str) -> str:
        stripped = value.strip().lower()
        normalized = "".join(
            char if char.isalnum() or char in "._-" else "_" for char in stripped
        )
        return normalized[:128] or "unknown"

    @staticmethod
    def normalize_capability(value: str) -> str:
        return server_slug(value)[:128] or "unknown"

    @classmethod
    def _key(cls, capability: str, op: str) -> tuple[str, str]:
        return cls.normalize_capability(capability), cls.normalize(op)


__all__ = ("OperationDescriptorEntry", "OperationDescriptorRegistry")

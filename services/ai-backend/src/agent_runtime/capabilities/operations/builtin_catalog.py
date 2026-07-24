"""Checked-in inventory for model-visible built-ins and dynamic tool seams.

The generic descriptor registry answers *how* an operation is classified.  This
catalog answers *which product callable* is allowed to enter that registry.  It
is deliberately declarative so a new model-visible helper cannot become an
implicit policy exception.  Dynamic tool specs are represented by their loader
seam, never by a wildcard descriptor: an unregistered dynamic tool resolves to
the existing ``unknown``/held default.
"""

from __future__ import annotations

import json
from enum import StrEnum
from importlib.resources import files
from importlib.resources.abc import Traversable

from pydantic import Field, TypeAdapter, model_validator

from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorEntry,
    OperationDescriptorRegistry,
)
from agent_runtime.execution.contracts import RuntimeContract

_CATALOG_FILE = "builtin_operation_catalog.json"


class BuiltinOperationKind(StrEnum):
    BUILTIN = "builtin"
    DYNAMIC_LOADER = "dynamic_loader"
    DYNAMIC_TOOL = "dynamic_tool"
    SUBAGENT = "subagent"
    PLACEHOLDER = "placeholder"


class BuiltinOperationExecution(StrEnum):
    PURE = "pure"
    INTERNAL = "internal"
    STAGED = "staged"
    DELEGATED = "delegated"
    GATEWAY = "gateway"


class BuiltinOperationCatalogEntry(RuntimeContract):
    """One reviewed callable seam, independent of feature-flag registration."""

    tool_name: str = Field(min_length=1, max_length=128)
    capability: str = Field(min_length=1, max_length=128)
    op: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=256)
    kind: BuiltinOperationKind
    execution: BuiltinOperationExecution
    model_visible: bool

    @model_validator(mode="after")
    def _dynamic_tools_stay_descriptor_bound(self) -> "BuiltinOperationCatalogEntry":
        if self.kind is BuiltinOperationKind.DYNAMIC_TOOL and self.model_visible:
            raise ValueError("dynamic tool catalog entries must be loaded descriptors")
        return self

    @property
    def key(self) -> tuple[str, str]:
        return (
            OperationDescriptorRegistry.normalize_capability(self.capability),
            OperationDescriptorRegistry.normalize(self.op),
        )


class BuiltinOperationCatalogError(RuntimeError):
    """The checked-in builtin inventory is malformed or contradictory."""


class BuiltinOperationCatalog:
    """Immutable, exact-match inventory with fail-closed dynamic resolution."""

    __slots__ = ("_by_key", "_by_tool_name")

    def __init__(self, entries: tuple[BuiltinOperationCatalogEntry, ...]) -> None:
        by_key: dict[tuple[str, str], BuiltinOperationCatalogEntry] = {}
        by_tool_name: dict[str, BuiltinOperationCatalogEntry] = {}
        for entry in entries:
            if entry.key in by_key:
                raise BuiltinOperationCatalogError(
                    f"duplicate builtin operation key: {entry.key}"
                )
            if entry.tool_name in by_tool_name:
                raise BuiltinOperationCatalogError(
                    f"duplicate builtin tool name: {entry.tool_name}"
                )
            by_key[entry.key] = entry
            by_tool_name[entry.tool_name] = entry
        self._by_key = by_key
        self._by_tool_name = by_tool_name

    @classmethod
    def from_file(cls, path: Traversable) -> "BuiltinOperationCatalog":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            entries = TypeAdapter(
                tuple[BuiltinOperationCatalogEntry, ...]
            ).validate_python(raw)
            return cls(entries)
        except BuiltinOperationCatalogError:
            raise
        except Exception as exc:
            raise BuiltinOperationCatalogError(
                f"{path.name}: invalid builtin operation catalog"
            ) from exc

    @classmethod
    def default(cls) -> "BuiltinOperationCatalog":
        return cls.from_file(files(__package__).joinpath(_CATALOG_FILE))

    def all_entries(self) -> tuple[BuiltinOperationCatalogEntry, ...]:
        return tuple(self._by_tool_name[name] for name in sorted(self._by_tool_name))

    def model_visible_entries(self) -> tuple[BuiltinOperationCatalogEntry, ...]:
        return tuple(entry for entry in self.all_entries() if entry.model_visible)

    def resolve_tool_name(self, tool_name: str) -> BuiltinOperationCatalogEntry | None:
        return self._by_tool_name.get(tool_name.strip())

    def resolve(self, capability: str, op: str) -> BuiltinOperationCatalogEntry | None:
        key = (
            OperationDescriptorRegistry.normalize_capability(capability),
            OperationDescriptorRegistry.normalize(op),
        )
        return self._by_key.get(key)

    def descriptor_or_safe_default(
        self,
        *,
        tool_name: str,
        descriptors: OperationDescriptorRegistry,
    ) -> OperationDescriptorEntry:
        """Resolve only reviewed entries; unknown/dynamic names remain held."""

        entry = self.resolve_tool_name(tool_name)
        if entry is None:
            return descriptors.safe_default(capability="builtin", op=tool_name)
        descriptor = descriptors.resolve_entry(entry.capability, entry.op)
        if descriptor is not None:
            return descriptor
        return descriptors.safe_default(
            capability=entry.capability,
            op=entry.op,
        )


DEFAULT_BUILTIN_OPERATION_CATALOG = BuiltinOperationCatalog.default()

__all__ = (
    "BuiltinOperationCatalog",
    "BuiltinOperationCatalogEntry",
    "BuiltinOperationCatalogError",
    "BuiltinOperationExecution",
    "BuiltinOperationKind",
    "DEFAULT_BUILTIN_OPERATION_CATALOG",
)

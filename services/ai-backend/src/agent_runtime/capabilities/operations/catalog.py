"""Checked-in, declarative product descriptor catalog."""

from __future__ import annotations

import json
from importlib.resources import files
from importlib.resources.abc import Traversable

from pydantic import TypeAdapter

from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorEntry,
    OperationDescriptorRegistry,
)

_CATALOG_FILE = "operation_descriptors.json"


class OperationDescriptorCatalogError(RuntimeError):
    """A checked-in descriptor catalog is malformed."""


class OperationDescriptorCatalog:
    """Load reviewed descriptors once; behavior remains in gateway policy."""

    @classmethod
    def from_file(cls, path: Traversable) -> OperationDescriptorRegistry:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            entries = TypeAdapter(tuple[OperationDescriptorEntry, ...]).validate_python(
                raw
            )
            return OperationDescriptorRegistry(entries)
        except Exception as exc:
            raise OperationDescriptorCatalogError(
                f"{path.name}: invalid operation descriptor catalog"
            ) from exc

    @classmethod
    def default_registry(cls) -> OperationDescriptorRegistry:
        return cls.from_file(files(__package__).joinpath(_CATALOG_FILE))


DEFAULT_OPERATION_DESCRIPTORS = OperationDescriptorCatalog.default_registry()

__all__ = (
    "DEFAULT_OPERATION_DESCRIPTORS",
    "OperationDescriptorCatalog",
    "OperationDescriptorCatalogError",
)

"""Versioned in-memory store and file snapshot for deterministic memory tests."""

from __future__ import annotations

from pydantic import Field, field_validator

from agent_runtime.execution.contracts import RuntimeContract, RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.validation import ValueNormalizer
from agent_runtime.context.memory.constants import Messages, _Fields
from agent_runtime.context.memory.contracts import MemoryValueNormalizer


class MemoryFileSnapshot(RuntimeContract):
    """Versioned in-memory file snapshot for deterministic concurrency tests."""

    path: str
    content: str
    version: int = Field(ge=0)

    @field_validator(_Fields.PATH)
    @classmethod
    def _normalize_path(cls, value: object) -> str:
        return MemoryValueNormalizer.normalize_memory_path(value, "path")

    @field_validator(_Fields.CONTENT)
    @classmethod
    def _normalize_content(cls, value: object) -> str:
        return ValueNormalizer.normalize_nonempty_string(value, _Fields.CONTENT)


class VersionedMemoryStore:
    """Small optimistic-concurrency store used by memory unit tests and fakes."""

    def __init__(self) -> None:
        self._files: dict[str, MemoryFileSnapshot] = {}

    def read(self, path: str) -> MemoryFileSnapshot | None:
        normalized_path = MemoryValueNormalizer.normalize_memory_path(path, "path")
        return self._files.get(normalized_path)

    def write(
        self,
        *,
        path: str,
        content: str,
        expected_version: int | None = None,
    ) -> MemoryFileSnapshot:
        normalized_path = MemoryValueNormalizer.normalize_memory_path(path, "path")
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

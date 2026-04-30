"""Persistence provider ports beyond the narrow FastAPI producer surface."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from agent_runtime.persistence.records import (
    CheckpointRecord,
    ContextPayloadRecord,
    MemoryItemRecord,
    MemoryScopeRecord,
)


@runtime_checkable
class MemoryMetadataPort(Protocol):
    """Memory scope and memory item metadata boundary."""

    def upsert_scope(self, record: MemoryScopeRecord) -> MemoryScopeRecord:
        """Create or update a memory namespace record."""

    def get_scope(
        self,
        *,
        org_id: str,
        scope_id: str,
    ) -> MemoryScopeRecord | None:
        """Return a memory scope by tenant and ID."""

    def list_items(
        self,
        *,
        org_id: str,
        scope_id: str,
        include_deleted: bool = False,
    ) -> Sequence[MemoryItemRecord]:
        """Return memory item metadata for one scope."""

    def upsert_item(self, record: MemoryItemRecord) -> MemoryItemRecord:
        """Create or update a memory item metadata row."""


@runtime_checkable
class PayloadStoragePort(Protocol):
    """Large payload storage by reference."""

    def put_payload(
        self,
        *,
        record: ContextPayloadRecord,
        content: bytes,
    ) -> ContextPayloadRecord:
        """Persist a payload blob and its metadata reference."""

    def get_payload_ref(
        self,
        *,
        org_id: str,
        payload_id: str,
    ) -> ContextPayloadRecord | None:
        """Return a payload reference without loading the blob."""

    def delete_expired_payloads(self, *, now: datetime) -> int:
        """Delete payloads whose retention window has expired."""


@runtime_checkable
class CheckpointStorePort(Protocol):
    """Runtime checkpoint metadata and blob-reference boundary."""

    def save_checkpoint_ref(self, record: CheckpointRecord) -> CheckpointRecord:
        """Persist one checkpoint metadata record."""

    def load_checkpoint_ref(
        self,
        *,
        org_id: str,
        thread_id: str,
        checkpoint_namespace: str,
        checkpoint_version: int,
    ) -> CheckpointRecord | None:
        """Load a checkpoint metadata record by unique checkpoint key."""

    def list_thread_checkpoints(
        self,
        *,
        org_id: str,
        thread_id: str,
    ) -> Sequence[CheckpointRecord]:
        """Return checkpoint refs for one runtime thread in creation order."""

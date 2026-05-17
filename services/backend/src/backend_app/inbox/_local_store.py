"""Local in-memory store + record stubs for the P4-A2 internal endpoint.

This module is a **parallel-wave coordination stub** — see ``__init__.py``.
P4-A1 owns the canonical ``InboxItemRecord`` / ``InboxStore`` and the
orchestrator replaces these imports at merge. Until then, this stub keeps
the internal-route module independently testable.

What is preserved across the merge:

* The record's required fields: ``id``, ``tenant_id``, ``owner_user_id``,
  ``kind``, ``title``, ``body_ref``, ``links``, ``sender``, ``state``,
  ``producer_id``, ``external_ref``, ``received_at``. P4-A1's record is a
  superset — extra fields default to None and the route layer ignores them.
* The store's ``insert_item``, ``insert_body``, ``append_audit`` methods,
  the ``transaction()`` ctx manager, and the ``items`` / ``audits`` dicts
  the test introspects.

A breaking change to any of those at merge would force a route rewrite
either way — keeping them aligned is the orchestrator's job.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import uuid4


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class InboxItemRecord:
    """One row in the future ``inbox_items`` table.

    Mirrors the P4-A1 record's required fields; the orchestrator replaces
    this with the canonical Pydantic record at merge.
    """

    tenant_id: str
    owner_user_id: str
    kind: str
    title: str
    sender: dict[str, Any]
    id: str = field(default_factory=lambda: f"inbox_{uuid4().hex[:12]}")
    body_ref: str | None = None
    links: list[dict[str, Any]] = field(default_factory=list)
    state: str = "unread"
    project_id: str | None = None
    producer_id: str | None = None
    external_ref: str | None = None
    received_at: datetime = field(default_factory=_now)


@dataclass
class InboxBodyRecord:
    """One row in the future ``inbox_bodies`` table."""

    tenant_id: str
    body_markdown: str
    body_ref: str = field(default_factory=lambda: f"body_{uuid4().hex[:12]}")
    created_at: datetime = field(default_factory=_now)


@dataclass
class InboxAuditRecord:
    """Append-only audit row.

    Mirrors the audit-chain shape used elsewhere. P4-A1's canonical record
    is a superset; only the fields the test asserts on are defined here.
    """

    tenant_id: str
    actor_user_id: str
    action: str
    target_id: str
    after_state: dict[str, Any] | None = None
    target_kind: str = "inbox_item"
    audit_id: str = field(default_factory=lambda: uuid4().hex)
    ts: datetime = field(default_factory=_now)


@dataclass
class InMemoryInboxStore:
    """Dict-backed store stub.

    Replaced at merge with P4-A1's full :class:`InMemoryInboxStore` —
    same public surface, more methods.
    """

    items: dict[str, InboxItemRecord] = field(default_factory=dict)
    bodies: dict[str, InboxBodyRecord] = field(default_factory=dict)
    audits: list[InboxAuditRecord] = field(default_factory=list)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        # Single-process in-memory; no real boundary. The route still
        # opens a transaction so the postgres adapter (production) gets
        # the same composition.
        yield

    def insert_item(self, record: InboxItemRecord) -> InboxItemRecord:
        self.items[record.id] = record
        return record

    def insert_body(self, record: InboxBodyRecord) -> InboxBodyRecord:
        self.bodies[record.body_ref] = record
        return record

    def append_audit(self, record: InboxAuditRecord) -> InboxAuditRecord:
        self.audits.append(record)
        return record

    def find_by_external_ref(
        self,
        *,
        tenant_id: str,
        producer_id: str,
        external_ref: str,
    ) -> InboxItemRecord | None:
        """Idempotency lookup. Postgres adapter uses the UNIQUE index."""
        for record in self.items.values():
            if (
                record.tenant_id == tenant_id
                and record.producer_id == producer_id
                and record.external_ref == external_ref
            ):
                return record
        return None


__all__ = [
    "InboxAuditRecord",
    "InboxBodyRecord",
    "InboxItemRecord",
    "InMemoryInboxStore",
]

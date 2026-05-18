"""Project templates — store contracts + records (Phase 6.5 §7).

Templates capture project shape for forking. Forking is a copy operation
(no live link from template → forked project). Wire spec at
``packages/api-types/src/project-templates.ts``.

§7.3 endpoints route through ``template_routes.py``; this module owns:

* ``ProjectTemplateRecord``      — one row per template.
* ``ProjectTemplateSnapshot``    — the immutable snapshot (§7.5).
* ``ProjectTemplatesStore``      — Protocol + in-memory adapter.
* ``ProjectTemplatesService``    — save / list / get / fork / patch / delete
  with tenant-first ACL and a single-transaction fork (§7.4).

Snapshot immutability is enforced at the PATCH layer (only metadata changes).
Fork is wrapped in ``store.transaction()`` so a faulted insert rolls back
the project + member + todo + routine rows.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _template_id() -> str:
    return f"tpl_{uuid4().hex}"


# ---------------------------------------------------------------------------
# Wire-mirror records.
# ---------------------------------------------------------------------------


class ProjectTemplateSeededTodo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=280)
    priority: Literal["low", "normal", "high"] | None = None
    relative_due_days: int | None = None
    labels: list[str] = Field(default_factory=list)


class ProjectTemplateSeededRoutine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    instructions_template: str = Field(max_length=16 * 1024)
    triggers: list[dict[str, Any]] = Field(default_factory=list)


class ProjectTemplateSnapshot(BaseModel):
    """Snapshot of the source project's shape. Immutable post-create."""

    model_config = ConfigDict(extra="forbid")

    default_member_user_ids: list[str] = Field(default_factory=list)
    default_connector_allowlist: list[str] | None = None
    color_hue: int | None = None
    icon_emoji: str | None = None
    seeded_todos: list[ProjectTemplateSeededTodo] = Field(default_factory=list)
    seeded_routines: list[ProjectTemplateSeededRoutine] = Field(default_factory=list)


class ProjectTemplateRecord(BaseModel):
    """One row in the ``project_templates`` table."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=_template_id)
    tenant_id: str
    owner_user_id: str
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=200)
    snapshot: ProjectTemplateSnapshot
    source_project_id: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    deleted_at: datetime | None = None


# ---------------------------------------------------------------------------
# Store contract.
# ---------------------------------------------------------------------------


class ProjectTemplatesStore(Protocol):
    """Adapter contract for the project-templates store."""

    @contextmanager
    def transaction(self) -> Iterator[None]: ...  # pragma: no cover

    def insert_template(
        self, record: ProjectTemplateRecord
    ) -> ProjectTemplateRecord: ...

    def get_template(
        self, *, tenant_id: str, template_id: str, include_deleted: bool = False
    ) -> ProjectTemplateRecord | None: ...

    def list_templates(
        self,
        *,
        tenant_id: str,
        owner_user_id: str | None = None,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ProjectTemplateRecord, ...], str | None]: ...

    def update_template_metadata(
        self,
        *,
        tenant_id: str,
        template_id: str,
        name: str | None,
        description: str | None,
    ) -> ProjectTemplateRecord | None: ...

    def soft_delete_template(self, *, tenant_id: str, template_id: str) -> bool: ...


@dataclass
class InMemoryProjectTemplatesStore:
    """Dict-backed adapter for tests + dev wiring."""

    templates: dict[str, ProjectTemplateRecord] = field(default_factory=dict)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield

    def insert_template(self, record: ProjectTemplateRecord) -> ProjectTemplateRecord:
        self.templates[record.id] = record
        return record

    def get_template(
        self, *, tenant_id: str, template_id: str, include_deleted: bool = False
    ) -> ProjectTemplateRecord | None:
        record = self.templates.get(template_id)
        if record is None or record.tenant_id != tenant_id:
            return None
        if record.deleted_at is not None and not include_deleted:
            return None
        return record

    def list_templates(
        self,
        *,
        tenant_id: str,
        owner_user_id: str | None = None,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[ProjectTemplateRecord, ...], str | None]:
        wanted = (q or "").strip().lower()
        candidates: list[ProjectTemplateRecord] = []
        for record in self.templates.values():
            if record.tenant_id != tenant_id or record.deleted_at is not None:
                continue
            if owner_user_id is not None and record.owner_user_id != owner_user_id:
                continue
            if (
                wanted
                and wanted not in (record.name + " " + record.description).lower()
            ):
                continue
            candidates.append(record)
        candidates.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        start = int(cursor) if cursor and cursor.isdigit() else 0
        page = candidates[start : start + limit]
        next_cursor = str(start + limit) if start + limit < len(candidates) else None
        return tuple(page), next_cursor

    def update_template_metadata(
        self,
        *,
        tenant_id: str,
        template_id: str,
        name: str | None,
        description: str | None,
    ) -> ProjectTemplateRecord | None:
        record = self.get_template(tenant_id=tenant_id, template_id=template_id)
        if record is None:
            return None
        updates: dict[str, Any] = {"updated_at": _now()}
        if name is not None:
            updates["name"] = name
        if description is not None:
            updates["description"] = description
        new_record = record.model_copy(update=updates)
        self.templates[template_id] = new_record
        return new_record

    def soft_delete_template(self, *, tenant_id: str, template_id: str) -> bool:
        record = self.templates.get(template_id)
        if record is None or record.tenant_id != tenant_id:
            return False
        if record.deleted_at is not None:
            return True
        self.templates[template_id] = record.model_copy(
            update={"deleted_at": _now(), "updated_at": _now()}
        )
        return True


# ---------------------------------------------------------------------------
# Service exceptions.
# ---------------------------------------------------------------------------


class TemplateNotFound(Exception):
    """Template doesn't exist OR caller can't see it (404)."""


class TemplateForbidden(Exception):
    """Caller can read but cannot write the template (403)."""


class TemplateInvalidRequest(Exception):
    """Client-fixable invariant violation (400)."""


class TemplateForkError(Exception):
    """Fork failed mid-transaction (500 — see service log)."""


__all__ = [
    "InMemoryProjectTemplatesStore",
    "ProjectTemplateRecord",
    "ProjectTemplateSeededRoutine",
    "ProjectTemplateSeededTodo",
    "ProjectTemplateSnapshot",
    "ProjectTemplatesStore",
    "TemplateForbidden",
    "TemplateForkError",
    "TemplateInvalidRequest",
    "TemplateNotFound",
]

"""Persona directory loaded from a YAML fixture.

The dev IdP keeps the directory in memory and reloads on file mtime change
so editing ``dev_personas.yaml`` while ``make dev`` is running is reflected
on the next ``GET /v1/dev/personas`` call.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class DevOrg(BaseModel):
    """A development tenant. Lives only in the YAML fixture."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    slug: str
    display_name: str


class DevPersona(BaseModel):
    """A development user.

    The persona is the source of truth for ``org_id``, ``user_id``, roles,
    and permission scopes — same shape as the production
    ``AuthenticatedIdentity`` payload.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str
    org_id: str
    user_id: str
    display_name: str
    primary_email: str
    roles: tuple[str, ...] = ("employee",)
    permission_scopes: tuple[str, ...] = ("runtime:use",)


class PersonaDirectory(BaseModel):
    """Loaded YAML directory."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    orgs: tuple[DevOrg, ...]
    personas: tuple[DevPersona, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _validate_unique_and_referenced(self) -> Self:
        org_ids = {o.id for o in self.orgs}
        if len(org_ids) != len(self.orgs):
            raise ValueError("orgs[].id must be unique")
        slugs = {p.slug for p in self.personas}
        if len(slugs) != len(self.personas):
            raise ValueError("personas[].slug must be unique")
        for persona in self.personas:
            if persona.org_id not in org_ids:
                raise ValueError(
                    f"persona {persona.slug!r} references unknown org_id {persona.org_id!r}"
                )
        return self

    def org_by_id(self, org_id: str) -> DevOrg:
        for org in self.orgs:
            if org.id == org_id:
                return org
        raise KeyError(org_id)

    def persona(self, slug: str) -> DevPersona:
        for persona in self.personas:
            if persona.slug == slug:
                return persona
        raise KeyError(slug)


@dataclass
class _CachedDirectory:
    directory: PersonaDirectory
    mtime_ns: int


class PersonaLoader:
    """Filesystem-backed loader with mtime-keyed reloads.

    Single instance per process; safe across asyncio tasks (the lock is
    held only across the read+parse, which is microsecond-scale).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._cache: _CachedDirectory | None = None
        self._lock = Lock()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> PersonaDirectory:
        """Return the directory, reloading from disk on mtime change."""

        with self._lock:
            mtime_ns = self._path.stat().st_mtime_ns
            if self._cache is not None and self._cache.mtime_ns == mtime_ns:
                return self._cache.directory
            raw = yaml.safe_load(self._path.read_text())
            if not isinstance(raw, dict):
                raise ValueError(f"{self._path} must be a YAML mapping")
            directory = PersonaDirectory.model_validate(raw)
            self._cache = _CachedDirectory(directory=directory, mtime_ns=mtime_ns)
            return directory

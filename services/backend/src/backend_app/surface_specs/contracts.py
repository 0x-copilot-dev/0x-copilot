"""Pydantic v2 wire + record types for the SurfaceSpec registry (PRD-08).

Durable, org-scoped persistence for generated ``SurfaceSpec`` documents (the
generative-UI plan's Tier-1.5 workhorse). The registry is **data, not code** —
a spec is a schema-validated JSON binding, never executable — so these types
carry the spec as an opaque ``dict`` that is re-validated against the shared
``surface_spec.schema.json`` on every write (see :mod:`.validation`).

The cache identity mirrors the ai-backend port's ``SpecKey`` (plan D10):
``(server, tool, output_shape_hash, spec_schema_version, skill_version)``,
partitioned by ``org_id`` (org-scoped; no cross-org reads). ``origin`` lets a
human-authored ``curated-override`` win over a machine ``generated`` spec on
read without a deploy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Final
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


# Bounds applied to untrusted identifiers so an exotic server/tool name can
# never blow up a row or a query. The spec body itself is bounded by the JSON
# Schema re-validation, not here.
_SERVER_MAX: Final = 128
_TOOL_MAX: Final = 200
_SHAPE_HASH_MAX: Final = 128
_MODEL_MAX: Final = 128


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SurfaceSpecOrigin(StrEnum):
    """Provenance of a stored spec; also the read-precedence key.

    ``CURATED_OVERRIDE`` is the operator pin: it wins over ``GENERATED`` on GET
    so a corrected spec ships without a deploy (PRD-08 behaviour).
    """

    GENERATED = "generated"
    CURATED_OVERRIDE = "curated-override"

    @property
    def precedence(self) -> int:
        """Higher wins on read. Curated overrides outrank generated specs."""

        return 1 if self is SurfaceSpecOrigin.CURATED_OVERRIDE else 0


class _SpecContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SurfaceSpecUpsert(_SpecContract):
    """Wire shape for ``PUT /internal/v1/surfaces/specs``.

    ``org_id``/``user_id`` are intentionally **absent** — the route rebinds
    them from the verified service identity, never from the body, so a caller
    cannot write into another org's partition.
    """

    server: str = Field(min_length=1, max_length=_SERVER_MAX)
    tool: str = Field(min_length=1, max_length=_TOOL_MAX)
    output_shape_hash: str = Field(min_length=1, max_length=_SHAPE_HASH_MAX)
    spec_schema_version: int = Field(default=1, ge=1)
    skill_version: int = Field(default=1, ge=1)
    origin: SurfaceSpecOrigin = SurfaceSpecOrigin.GENERATED
    generator_model: str = Field(default="", max_length=_MODEL_MAX)
    spec: dict[str, Any]


class SurfaceSpecRecord(_SpecContract):
    """Persistence record for one ``surface_specs`` row.

    ``org_id`` is the origin org; the route layer rebinds it from the verified
    service identity so a caller cannot spoof another org's partition. The full
    cache key is ``(server, tool, output_shape_hash, spec_schema_version,
    skill_version)`` scoped by ``org_id``; ``origin`` disambiguates a curated
    override from the generated spec for the same key.
    """

    spec_id: str = Field(default_factory=lambda: f"sspec_{uuid4().hex}")
    org_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=128)
    server: str = Field(min_length=1, max_length=_SERVER_MAX)
    tool: str = Field(min_length=1, max_length=_TOOL_MAX)
    output_shape_hash: str = Field(min_length=1, max_length=_SHAPE_HASH_MAX)
    spec_schema_version: int = Field(ge=1)
    skill_version: int = Field(ge=1)
    origin: SurfaceSpecOrigin = SurfaceSpecOrigin.GENERATED
    generator_model: str = Field(default="", max_length=_MODEL_MAX)
    spec: dict[str, Any]
    created_at: datetime = Field(default_factory=_now)

    @property
    def key_tuple(self) -> tuple[str, str, str, int, int]:
        """The full cache key (org-independent portion), plan D10."""

        return (
            self.server,
            self.tool,
            self.output_shape_hash,
            self.spec_schema_version,
            self.skill_version,
        )


class SurfaceSpecView(_SpecContract):
    """Wire view returned by GET / PUT.

    Carries every provenance field the ai-backend client needs to reconstruct a
    ``StoredSpec`` (server/tool/shape-hash/versions/generator_model/created_at)
    plus the spec body. ``user_id`` is intentionally omitted from the view —
    the reader is a trusted service, but the authoring user is not part of the
    render contract.
    """

    spec_id: str
    server: str
    tool: str
    output_shape_hash: str
    spec_schema_version: int
    skill_version: int
    origin: SurfaceSpecOrigin
    generator_model: str
    spec: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_record(cls, record: SurfaceSpecRecord) -> "SurfaceSpecView":
        return cls(
            spec_id=record.spec_id,
            server=record.server,
            tool=record.tool,
            output_shape_hash=record.output_shape_hash,
            spec_schema_version=record.spec_schema_version,
            skill_version=record.skill_version,
            origin=record.origin,
            generator_model=record.generator_model,
            spec=record.spec,
            created_at=record.created_at,
        )


class SurfaceSpecResponse(_SpecContract):
    """GET response envelope: the resolved spec, or ``null`` on a miss.

    A miss is a normal, non-error outcome (the render path falls back to
    tier-3), so the GET returns 200 with ``spec: null`` rather than 404.
    """

    spec: SurfaceSpecView | None = None


__all__ = [
    "SurfaceSpecOrigin",
    "SurfaceSpecRecord",
    "SurfaceSpecResponse",
    "SurfaceSpecUpsert",
    "SurfaceSpecView",
]

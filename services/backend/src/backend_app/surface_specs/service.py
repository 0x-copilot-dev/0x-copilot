"""Domain service for the SurfaceSpec registry (PRD-08).

Thin orchestration over :class:`SurfaceSpecStore`: re-validate on write, rebind
org/user from the trusted identity, resolve reads with override precedence.
Every spec is re-validated against the shared ``surface_spec.schema.json`` on
write — the registry stores **data, not code**, and schema validation is the
entire security gate (generative-UI plan D9).
"""

from __future__ import annotations

from backend_app.surface_specs.contracts import (
    SurfaceSpecOrigin,
    SurfaceSpecRecord,
    SurfaceSpecUpsert,
    SurfaceSpecView,
)
from backend_app.surface_specs.store import (
    InMemorySurfaceSpecStore,
    SurfaceSpecStore,
)
from backend_app.surface_specs.validation import (
    SurfaceSpecSchemaError,
    validate_surface_spec_dict,
)


class SurfaceSpecService:
    """Org-scoped operations on the SurfaceSpec registry."""

    def __init__(self, *, store: SurfaceSpecStore | None = None) -> None:
        self._store: SurfaceSpecStore = store or InMemorySurfaceSpecStore()

    @property
    def store(self) -> SurfaceSpecStore:
        return self._store

    def put_spec(
        self,
        *,
        org_id: str,
        user_id: str,
        upsert: SurfaceSpecUpsert,
    ) -> SurfaceSpecView:
        """Validate + upsert a spec for ``org_id``.

        Raises :class:`SurfaceSpecSchemaError` when the spec body does not
        satisfy the shared JSON Schema; the route maps that to HTTP 422.
        """

        # Re-validate the untrusted spec body against the SSOT schema before it
        # is ever persisted or served. This is the whole security gate.
        validate_surface_spec_dict(upsert.spec)
        record = SurfaceSpecRecord(
            org_id=org_id,
            user_id=user_id,
            server=upsert.server,
            tool=upsert.tool,
            output_shape_hash=upsert.output_shape_hash,
            spec_schema_version=upsert.spec_schema_version,
            skill_version=upsert.skill_version,
            origin=upsert.origin,
            generator_model=upsert.generator_model,
            spec=upsert.spec,
        )
        with self._store.transaction() as conn:
            saved = self._store.upsert(record, conn=conn)
        return SurfaceSpecView.from_record(saved)

    def get_spec(
        self,
        *,
        org_id: str,
        server: str,
        tool: str,
        output_shape_hash: str | None = None,
        spec_schema_version: int | None = None,
        skill_version: int | None = None,
    ) -> SurfaceSpecView | None:
        """Resolve a spec for ``org_id`` (override precedence applied by the store).

        When the full key is supplied (shape-hash + both versions) the exact key
        is resolved; otherwise the latest spec for ``(server, tool)`` is returned
        — the projector's coarse rung-2 read.
        """

        if (
            output_shape_hash is not None
            and spec_schema_version is not None
            and skill_version is not None
        ):
            record = self._store.get_by_key(
                org_id=org_id,
                server=server,
                tool=tool,
                output_shape_hash=output_shape_hash,
                spec_schema_version=spec_schema_version,
                skill_version=skill_version,
            )
        else:
            record = self._store.get_latest_by_tool(
                org_id=org_id, server=server, tool=tool
            )
        return SurfaceSpecView.from_record(record) if record is not None else None

    def delete_spec(self, *, org_id: str, spec_id: str) -> bool:
        """Delete a spec by id within ``org_id`` (admin / override path)."""

        with self._store.transaction() as conn:
            return self._store.delete(org_id=org_id, spec_id=spec_id, conn=conn)


__all__ = [
    "SurfaceSpecOrigin",
    "SurfaceSpecSchemaError",
    "SurfaceSpecService",
]

"""Declare an agent: the write route ``subagent_defs/*.json`` never had.

``FileSubagentDefinitionProvider`` has read ``subagent_defs/<name>.json`` into
the supervisor's dynamic catalog since the file store landed, and
``FileSubagentDefinitionStore.write_definition`` has been able to write one for
just as long — with no caller anywhere in ``src`` (``dark_wiring_baseline.txt``
carried it as "no src caller at all"). The capability was complete and
unreachable: a user could declare an agent with its own tools, skills, scopes
and timeouts only by hand-writing JSON into an application-private directory
and restarting.

Three routes under ``/v1/agent``, which is the whole entry point:

  * ``GET    /v1/agent/subagents``         — what this installation has declared
  * ``PUT    /v1/agent/subagents/{name}``  — declare or replace one
  * ``DELETE /v1/agent/subagents/{name}``  — undeclare one

**The payload is the domain contract, deliberately.** A declared agent is
exactly a
:class:`~agent_runtime.delegation.subagents.contracts.SubagentDefinition` — the
same model the catalog validates, the same one ``narrow_authority`` reads
``tools`` / ``skills`` / ``allowed_scopes`` off when it computes what a child
may actually do. A second wire shape here would be a second place to forget a
ceiling: ``tools`` is a capability grant, and a DTO that dropped or widened it
in translation would be a permission bug wearing a mapping-layer costume.

**File store only, and it answers so.** ``subagent_defs/`` is a directory in the
desktop's file-native store; on the in-memory and Postgres backends there is no
such directory and nothing would read one. Rather than pretend, the routes
return 501 when :class:`FileAgentStateWiring` reports the store inactive, so a
caller learns the capability is absent instead of watching a write disappear.

**Not built here, and each is a separate decision.** Loading agent code from
disk (a declared agent selects an existing ``graph_id``; it does not ship a
runner), and per-agent model / prompt overrides — ``SubagentDefinition`` has no
field for either today, and adding one means threading it through the runner
and the authority ceiling, not adding a key to this payload.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import ValidationError

from agent_runtime.api.constants import Keys
from agent_runtime.delegation.subagents.contracts import (
    SubagentDefinition,
    SubagentValueNormalizer,
)
from runtime_api.http.routes import RuntimeApiRoutes
from runtime_api.schemas.subagent_definitions import DeclaredSubagentListResponse


class DeclaredSubagentRoutes:
    """Read/declare/undeclare the agents this installation has configured."""

    _STORE_UNAVAILABLE = (
        "Declared agents require the file-native store "
        "(RUNTIME_STORE_BACKEND=file); this deployment has none."
    )

    @staticmethod
    def _store(request: Request):
        """Return the writable subagent-def store, or 501 when there is none.

        Resolved through ``FileAgentStateWiring`` — the same gate the worker's
        dependency factory consults — rather than by reading the environment
        here, so "is the file store active" has exactly one answer in this
        service. Imported lazily for the reason ``write_definition`` imports its
        contract lazily: the desktop-only adapter must not be pulled in on an
        image that has no file store.
        """

        from runtime_adapters.file.agent_state_store import (  # noqa: PLC0415
            FileAgentStateWiring,
        )

        store = FileAgentStateWiring().subagent_definition_store()
        if store is None:
            raise HTTPException(
                status.HTTP_501_NOT_IMPLEMENTED,
                DeclaredSubagentRoutes._STORE_UNAVAILABLE,
            )
        return store

    @classmethod
    async def list_declared(
        cls,
        request: Request,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> DeclaredSubagentListResponse:
        """Return every declared agent, validated through the domain contract.

        A definition that no longer validates is **skipped, not fatal**: the
        directory is hand-editable by design, and one malformed file must not
        make the whole list unreadable — that is the same posture
        ``DynamicSubagentCatalog`` takes when a provider yields a bad row.
        """

        RuntimeApiRoutes.scoped_identity(request, org_id=org_id, user_id=user_id)
        declared: list[SubagentDefinition] = []
        for raw in cls._store(request).read_raw_definitions():
            try:
                declared.append(SubagentDefinition.model_validate(raw))
            except ValidationError:
                continue
        return DeclaredSubagentListResponse(
            subagents=tuple(sorted(declared, key=lambda item: item.name))
        )

    @classmethod
    async def declare(
        cls,
        request: Request,
        name: str,
        payload: SubagentDefinition,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> SubagentDefinition:
        """Declare or replace one agent; the path name is authoritative.

        A body naming a different agent than the path is refused rather than
        silently resolved either way. Trusting the body would let ``PUT
        /subagents/reader`` overwrite ``writer``; trusting the path would
        rename a definition the caller believed it was editing.
        """

        RuntimeApiRoutes.scoped_identity(request, org_id=org_id, user_id=user_id)
        # Compare through the contract's own normalizer, not the raw strings:
        # ``payload.name`` has already been slugged by the model, so a raw
        # comparison would reject ``PUT /subagents/Reader`` against a body the
        # contract itself considers identical.
        try:
            path_name = SubagentValueNormalizer.normalize_slug(name, "name")
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "subagent name must be a stable slug",
            ) from exc
        if payload.name != path_name:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "subagent name in the path and the body must match",
            )
        cls._store(request).write_definition(payload)
        return payload

    @classmethod
    async def undeclare(
        cls,
        request: Request,
        name: str,
        org_id: str | None = Query(None, min_length=1),
        user_id: str | None = Query(None, min_length=1),
    ) -> Response:
        """Remove one declared agent; 404 when this installation never had it."""

        RuntimeApiRoutes.scoped_identity(request, org_id=org_id, user_id=user_id)
        if not cls._store(request).delete_definition(name):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "no agent is declared under that name",
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)


def register_declared_subagent_routes(router: APIRouter) -> None:
    """Mount the declared-agent routes on the ``/v1/agent`` router."""

    router.add_api_route(
        "/subagents",
        DeclaredSubagentRoutes.list_declared,
        methods=["GET"],
        response_model=DeclaredSubagentListResponse,
        name=Keys.RouteName.LIST_DECLARED_SUBAGENTS,
    )
    router.add_api_route(
        "/subagents/{name}",
        DeclaredSubagentRoutes.declare,
        methods=["PUT"],
        response_model=SubagentDefinition,
        name=Keys.RouteName.DECLARE_SUBAGENT,
    )
    router.add_api_route(
        "/subagents/{name}",
        DeclaredSubagentRoutes.undeclare,
        methods=["DELETE"],
        status_code=status.HTTP_204_NO_CONTENT,
        name=Keys.RouteName.UNDECLARE_SUBAGENT,
    )


__all__ = (
    "DeclaredSubagentRoutes",
    "register_declared_subagent_routes",
)

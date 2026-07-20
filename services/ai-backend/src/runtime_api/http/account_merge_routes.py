"""Internal account-merge endpoint (account-linking PRD §6.4).

``POST /internal/v1/admin/account-merge`` re-keys every tenant-scoped row of
the absorbed ``(org_id, user_id)`` account to the survivor. The backend's
merge saga is the only caller — service-token auth, never tenant-scoped,
never exposed through the facade.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, status

from agent_runtime.execution.contracts import RuntimeErrorCode
from runtime_api.auth import RuntimeServiceAuthenticator
from runtime_api.http.errors import RuntimeApiError
from runtime_api.rbac import public_route
from runtime_api.schemas.account_merge import (
    AccountMergeRequest,
    AccountMergeResponse,
)

_LOGGER = logging.getLogger("ai_backend.account_merge")


class AccountMergeApiRoutes:
    """Handler for the internal account-merge re-key endpoint."""

    @classmethod
    async def merge_accounts(
        cls,
        request: Request,
        payload: AccountMergeRequest,
    ) -> AccountMergeResponse:
        """Re-key the absorbed account's rows to the survivor and report counts.

        Idempotent: once the absorbed account holds no rows, a re-run
        matches nothing and returns ``status="noop"`` with empty counts —
        the saga's resume path relies on that. The per-org audit chain is
        never rewritten; a merge marker is appended to the survivor chain
        instead.
        """

        # Service-token-only gate: 401 on a wrong token, open in dev when
        # no token is configured. The saga supplies explicit absorbed /
        # survivor coordinates in the body — tenant identity headers are
        # deliberately not required (this route is never tenant-scoped).
        RuntimeServiceAuthenticator.require_service_token(request)
        if (
            payload.absorbed_org_id == payload.survivor_org_id
            and payload.absorbed_user_id == payload.survivor_user_id
        ):
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                "absorbed and survivor account must differ",
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        persistence = request.app.state.runtime_persistence
        tables, warnings = await cls._rekey_for_backend(request, payload)
        moved_total = sum(tables.values())
        if moved_total:
            await cls._append_merge_marker(
                persistence, payload=payload, tables=tables, warnings=warnings
            )
        return AccountMergeResponse(
            merge_id=payload.merge_id,
            status="completed" if moved_total else "noop",
            tables=tables,
            warnings=tuple(warnings),
        )

    @classmethod
    async def _rekey_for_backend(
        cls,
        request: Request,
        payload: AccountMergeRequest,
    ) -> tuple[dict[str, int], list[str]]:
        """Dispatch to the re-keyer matching the wired persistence backend.

        The file-native (desktop) store fails closed with 501: reporting
        success without moving data would let the saga proceed to its
        destructive steps (disable + revoke) against an unmerged store.
        """

        persistence = request.app.state.runtime_persistence
        from runtime_adapters.in_memory.runtime_api_store import (
            InMemoryRuntimeApiStore,
        )
        from runtime_adapters.postgres.runtime_api_store import (
            PostgresRuntimeApiStore,
        )

        if isinstance(persistence, PostgresRuntimeApiStore):
            from runtime_adapters.postgres.account_merge import (
                PostgresAccountMergeRekeyer,
            )

            return await PostgresAccountMergeRekeyer(persistence).rekey(
                absorbed_org_id=payload.absorbed_org_id,
                absorbed_user_id=payload.absorbed_user_id,
                survivor_org_id=payload.survivor_org_id,
                survivor_user_id=payload.survivor_user_id,
            )
        if isinstance(persistence, InMemoryRuntimeApiStore):
            return cls._rekey_in_memory(request, persistence, payload)
        raise RuntimeApiError(
            RuntimeErrorCode.CONFIGURATION_ERROR,
            "account merge is not supported for the configured store backend",
            http_status=status.HTTP_501_NOT_IMPLEMENTED,
        )

    @classmethod
    def _rekey_in_memory(
        cls,
        request: Request,
        persistence: object,
        payload: AccountMergeRequest,
    ) -> tuple[dict[str, int], list[str]]:
        """Re-key the in-memory store plus whichever satellites are wired."""

        from runtime_adapters.in_memory.account_merge import (
            InMemoryAccountMergeRekeyer,
        )
        from runtime_adapters.in_memory.citation_store import InMemoryCitationStore
        from runtime_adapters.in_memory.conversation_tool_ordinal_store import (
            InMemoryConversationToolOrdinalStore,
        )
        from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
        from runtime_adapters.in_memory.share_store import InMemoryShareStore
        from runtime_adapters.in_memory.todo_extraction_store import (
            InMemoryTodoExtractionStore,
        )

        rekeyer = InMemoryAccountMergeRekeyer(
            absorbed_org_id=payload.absorbed_org_id,
            absorbed_user_id=payload.absorbed_user_id,
            survivor_org_id=payload.survivor_org_id,
            survivor_user_id=payload.survivor_user_id,
        )
        rekeyer.rekey_store(persistence)
        ports = getattr(request.app.state, "runtime_ports", None)
        if ports is not None:
            draft_store = getattr(ports, "draft_store", None)
            if isinstance(draft_store, InMemoryDraftStore):
                rekeyer.rekey_draft_store(draft_store)
            share_store = getattr(ports, "share_store", None)
            if isinstance(share_store, InMemoryShareStore):
                rekeyer.rekey_share_store(share_store)
            ordinal_store = getattr(ports, "conversation_tool_ordinal_store", None)
            if isinstance(ordinal_store, InMemoryConversationToolOrdinalStore):
                rekeyer.rekey_tool_ordinal_store(ordinal_store)
            source_store = getattr(ports, "source_store", None)
            citation_store = getattr(source_store, "_citations", None)
            if isinstance(citation_store, InMemoryCitationStore):
                rekeyer.rekey_citation_store(citation_store)
        todo_service = getattr(request.app.state, "todo_extractions_service", None)
        todo_store = getattr(todo_service, "_store", None)
        if isinstance(todo_store, InMemoryTodoExtractionStore):
            rekeyer.rekey_todo_extraction_store(todo_store)
        return rekeyer.tables, rekeyer.warnings

    @classmethod
    async def _append_merge_marker(
        cls,
        persistence: object,
        *,
        payload: AccountMergeRequest,
        tables: dict[str, int],
        warnings: list[str],
    ) -> None:
        """Append the merge marker to the SURVIVOR org's audit chain.

        Uses the existing ``write_audit_log`` append API (both backends
        implement it). The absorbed org's chain stays byte-identical. A
        failed append degrades to an ``audit_marker_skipped`` warning —
        the re-key itself has already committed and must not be rolled
        back for an audit-side failure.
        """

        append = getattr(persistence, "write_audit_log", None)
        if append is None:
            warnings.append("audit_marker_skipped: store has no write_audit_log")
            return
        try:
            await append(
                event_type="account_merged",
                record={
                    "org_id": payload.survivor_org_id,
                    "user_id": payload.survivor_user_id,
                    "actor_type": "system",
                    "resource_type": "account_merge",
                    "resource_id": payload.merge_id,
                    "outcome": "success",
                    "metadata": {
                        "absorbed_org_id": payload.absorbed_org_id,
                        "absorbed_user_id": payload.absorbed_user_id,
                        "tables": dict(tables),
                    },
                },
            )
        except Exception:
            _LOGGER.exception(
                "account-merge audit marker append failed",
                extra={"safe_message": "account_merge.audit_marker_failed"},
            )
            warnings.append("audit_marker_skipped: append to survivor chain failed")


class AccountMergeApiRouter:
    """Build the ``/internal/v1/admin/account-merge`` router.

    Sister to :class:`InternalRuntimeApiRouter` — kept in its own module so
    the privileged cross-tenant surface stays greppable and separately
    reviewable.
    """

    @classmethod
    def create_router(cls) -> APIRouter:
        router = APIRouter(prefix="/internal/v1/admin", tags=["runtime-internal"])
        router.add_api_route(
            "/account-merge",
            AccountMergeApiRoutes.merge_accounts,
            methods=["POST"],
            response_model=AccountMergeResponse,
            name="internal_account_merge",
            # No RBAC scope dependency on purpose: RequireScopes resolves
            # tenant identity headers, and this route is explicitly NOT
            # tenant-scoped — the service-token gate inside the handler is
            # the auth. ``public_route()`` marks that decision for the CI
            # scope audit.
            dependencies=[Depends(public_route())],
        )
        return router


__all__ = ("AccountMergeApiRouter", "AccountMergeApiRoutes")

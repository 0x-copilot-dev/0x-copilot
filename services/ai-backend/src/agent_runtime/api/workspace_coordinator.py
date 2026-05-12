"""Workspace admin coordinator — controller-facing surface for workspace-level operations.

Owns ``get_workspace_defaults``, ``update_workspace_defaults``,
``request_workspace_export``, and ``record_workspace_delete_attempt``. Delegates
domain logic to :class:`WorkspaceDefaultsService` and appends detailed audit rows
for every state-changing operation.
"""

from __future__ import annotations

from agent_runtime.api.constants import Messages
from agent_runtime.api.ports import PersistencePort
from agent_runtime.execution.contracts import RuntimeErrorCode
from agent_runtime.execution.errors import AgentRuntimeError
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_api.http.errors import RuntimeApiError
from runtime_api.schemas import (
    UpdateWorkspaceDefaultsRequest,
    WorkspaceDefaultsResponse,
)
from starlette import status


class WorkspaceCoordinator:
    """Service layer for workspace admin writes: defaults, export queuing, and delete auditing."""

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        settings: RuntimeSettings,
        model_resolver: ModelConfigResolver,
    ) -> None:
        self._persistence = persistence
        self._settings = settings
        self._model_resolver = model_resolver

    async def get_workspace_defaults(self, *, org_id: str) -> WorkspaceDefaultsResponse:
        """Return the current workspace defaults for the given org."""
        return await self._workspace_defaults().get(org_id=org_id)

    async def update_workspace_defaults(
        self,
        *,
        org_id: str,
        actor_user_id: str,
        request: UpdateWorkspaceDefaultsRequest,
    ) -> WorkspaceDefaultsResponse:
        """Validate and persist workspace default updates, emitting granular audit rows.

        Behavior-overrides and training-opt-out changes each get their own audit row
        in addition to the top-level defaults-update row so compliance queries can
        find them by action name without parsing JSONB diffs.
        """

        self._validate_workspace_default_model(request)
        before_record = await self._workspace_defaults().get_record(org_id=org_id)
        response, audit_metadata = await self._workspace_defaults().update(
            org_id=org_id,
            actor_user_id=actor_user_id,
            request=request,
        )
        await self._persistence.write_audit_log(
            event_type=Messages.Audit.WORKSPACE_DEFAULTS_UPDATE,
            record={
                "org_id": org_id,
                "user_id": actor_user_id,
                "resource_type": "workspace_defaults",
                "resource_id": org_id,
                "outcome": "success",
                "metadata": audit_metadata,
            },
        )
        # Emit a dedicated audit row for behavior-overrides changes so
        # compliance dashboards can filter by action name alone.
        if "behavior_overrides" in audit_metadata.get("diff_keys", []):
            await self._persistence.write_audit_log(
                event_type=Messages.Audit.WORKSPACE_BEHAVIOR_OVERRIDES_UPDATE,
                record={
                    "org_id": org_id,
                    "user_id": actor_user_id,
                    "resource_type": "workspace_defaults",
                    "resource_id": org_id,
                    "outcome": "success",
                    "metadata": {
                        "before": audit_metadata["before"]["behavior_overrides"],
                        "after": audit_metadata["after"]["behavior_overrides"],
                    },
                },
            )
        from agent_runtime.api.workspace_defaults_service import (
            WorkspaceDefaultsService,
        )

        before_overrides = (
            before_record.behavior_overrides if before_record is not None else None
        )
        opt_out_diff = WorkspaceDefaultsService.training_opt_out_diff(
            before=before_overrides,
            after=request.behavior_overrides,
        )
        if opt_out_diff is not None:
            previous, current = opt_out_diff
            await self._persistence.write_audit_log(
                event_type=Messages.Audit.WORKSPACE_TRAINING_OPT_OUT_UPDATE,
                record={
                    "org_id": org_id,
                    "user_id": actor_user_id,
                    "resource_type": "workspace_defaults",
                    "resource_id": org_id,
                    "outcome": "success",
                    "metadata": {"before": previous, "after": current},
                },
            )
        return response

    async def request_workspace_export(
        self,
        *,
        org_id: str,
        actor_user_id: str,
    ) -> dict[str, str]:
        """Queue a workspace export request and return its export_id.

        This is a v1 stub: the audit row and 202 ship now; the actual export
        pipeline lands in a follow-up. The export_id is stored in the audit row
        so it can be correlated with the eventual pipeline output.
        """

        from uuid import uuid4

        export_id = f"exp_{uuid4().hex[:24]}"
        await self._persistence.write_audit_log(
            event_type=Messages.Audit.WORKSPACE_EXPORT_REQUEST,
            record={
                "org_id": org_id,
                "user_id": actor_user_id,
                "resource_type": "workspace_export",
                "resource_id": export_id,
                "outcome": "queued",
                "metadata": {
                    "export_id": export_id,
                    "scope": "workspace",
                    "status": "queued",
                },
            },
        )
        return {"export_id": export_id, "status": "queued"}

    async def record_workspace_delete_attempt(
        self,
        *,
        org_id: str,
        actor_user_id: str,
        typed_confirmation_correct: bool,
    ) -> None:
        """Record a workspace delete attempt in the audit log without executing the delete.

        The route returns 501; this method exists so the typed-confirmation answer
        is auditable even when the capability is not yet implemented. A forensic
        reader can see who asked and whether they answered the confirm gate correctly.
        """

        await self._persistence.write_audit_log(
            event_type=Messages.Audit.WORKSPACE_DELETE_ATTEMPT,
            record={
                "org_id": org_id,
                "user_id": actor_user_id,
                "resource_type": "workspace_data",
                "resource_id": org_id,
                "outcome": "blocked",
                "metadata": {
                    "typed_confirmation_correct": typed_confirmation_correct,
                },
            },
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _workspace_defaults(self):
        """Return a ``WorkspaceDefaultsService`` bound to this coordinator's deps.

        Lazily imported to prevent a circular dependency at module load time.
        """
        from agent_runtime.api.workspace_defaults_service import (
            WorkspaceDefaultsService,
        )

        return WorkspaceDefaultsService(
            persistence=self._persistence,
            settings=self._settings,
        )

    def _validate_workspace_default_model(
        self, request: UpdateWorkspaceDefaultsRequest
    ) -> None:
        """Raise 422 when the requested default model provider or name is not in the catalog.

        Provider validation reuses ``ModelConfigResolver._normalize_provider``
        (the same alias table the run path enforces). Model-name validation is done
        against the same hardcoded catalog ``list_models`` exposes so the admin
        default stays within the set the frontend picker shows.
        """

        try:
            self._model_resolver._normalize_provider(request.default_model.provider)
        except AgentRuntimeError as exc:
            raise RuntimeApiError(
                exc.code,
                Messages.Error.UNKNOWN_MODEL_PROVIDER,
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                retryable=False,
            ) from exc
        # Build the catalog inline rather than calling ConversationQueryService.list_models
        # to avoid a circular import between the two coordinator modules.
        from agent_runtime.api.conversation_query_service import _display_model_name
        from runtime_api.schemas import ModelCatalogItem

        default = self._settings.default_model
        configured = {
            "openai": self._settings.openai.is_configured,
            "anthropic": self._settings.anthropic.is_configured,
            "gemini": self._settings.gemini.is_configured,
        }
        models = [
            ModelCatalogItem(
                id=default.model_name,
                provider=default.provider,
                model_name=default.model_name,
                name=_display_model_name(default.model_name),
                description="Runtime default model",
                configured=configured.get(default.provider, False),
                supports_streaming=default.supports_streaming,
                supports_reasoning=default.reasoning is not None,
                reasoning=default.reasoning.model_dump(mode="json")
                if default.reasoning is not None
                else None,
            ),
            ModelCatalogItem(
                id="gpt-5.4-mini",
                provider="openai",
                model_name="gpt-5.4-mini",
                name="GPT-5.4 Mini",
                description="Compact OpenAI model",
                configured=configured["openai"],
                supports_streaming=True,
                supports_attachments=True,
                supports_reasoning=True,
                reasoning={"enabled": True, "effort": "medium", "summary": "auto"},
            ),
            ModelCatalogItem(
                id="claude-opus-4-7",
                provider="anthropic",
                model_name="claude-opus-4-7",
                name="Claude Opus 4.7",
                description="Anthropic reasoning model",
                configured=configured["anthropic"],
                supports_streaming=True,
                supports_reasoning=True,
            ),
            ModelCatalogItem(
                id="gemini-2.5-pro",
                provider="gemini",
                model_name="gemini-2.5-pro",
                name="Gemini 2.5 Pro",
                description="Google long-context model",
                configured=configured["gemini"],
                supports_streaming=True,
                supports_attachments=True,
            ),
        ]
        unique_models = {model.id: model for model in models}
        catalog_ids = set(unique_models.keys())
        catalog_names = {m.model_name for m in unique_models.values()}
        # Accept both the canonical id and the model_name so callers using
        # either form of the identifier pass validation.
        if (
            request.default_model.model_name not in catalog_ids
            and request.default_model.model_name not in catalog_names
        ):
            raise RuntimeApiError(
                RuntimeErrorCode.VALIDATION_ERROR,
                Messages.Error.UNKNOWN_MODEL_NAME,
                http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
                retryable=False,
            )

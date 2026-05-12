"""Workspace defaults service: composites per-org model/connector defaults with retention policy storage."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from agent_runtime.api.ports import PersistencePort
from agent_runtime.persistence.records.retention import (
    RetentionKind,
    RetentionPolicyRecord,
    RetentionScope,
)
from agent_runtime.retention import (
    DEPLOYMENT_DEFAULT_TTL_SECONDS,
    RetentionPolicyResolver,
)
from agent_runtime.settings import RuntimeSettings
from runtime_api.schemas import (
    DefaultModelSelection,
    UpdateWorkspaceDefaultsRequest,
    WorkspaceBehaviorOverrides,
    WorkspaceDefaultsRecord,
    WorkspaceDefaultsResponse,
    update_workspace_defaults_request_to_record,
)


# Retention kinds the org-level slider writes. Other kinds
# (context_payloads, memory_items) carry None deployment defaults and
# are deliberately not surfaced as a single org-wide knob. Operators
# tune them via the existing ``POST /v1/retention/policies`` route.
_ORG_KINDS: tuple[RetentionKind, ...] = (
    RetentionKind.MESSAGES,
    RetentionKind.EVENTS,
    RetentionKind.CHECKPOINTS,
)

_SECONDS_PER_DAY = 24 * 60 * 60


class WorkspaceDefaultsService:
    """One-class entry point for the ``/v1/agent/workspace/defaults`` surface.

    Read materialises deployment fallbacks when no row exists; write
    composes a defaults upsert + retention upserts in one shot. The
    caller (``RuntimeApiService``) is responsible for the audit row
    that wraps the whole call (one row, ``workspace.defaults.update``).
    """

    def __init__(
        self,
        *,
        persistence: PersistencePort,
        settings: RuntimeSettings,
    ) -> None:
        self._persistence = persistence
        self._settings = settings

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, *, org_id: str) -> WorkspaceDefaultsResponse:
        """Return the response shape with deployment fallbacks filled in."""

        record = await self._persistence.get_workspace_defaults(org_id=org_id)
        retention_days = await self._resolve_org_retention_days(org_id=org_id)
        default_model = (
            record.default_model
            if record is not None and record.default_model is not None
            else self._deployment_default_model()
        )
        default_connectors = record.default_connectors if record is not None else {}
        # PR 4.3 — surface behavior_overrides as part of the public read.
        # Absent row materialises to the default WorkspaceBehaviorOverrides
        # (all-None / opt-out=False) so the FE always sees a complete shape.
        behavior_overrides = (
            record.behavior_overrides
            if record is not None
            else WorkspaceBehaviorOverrides()
        )
        return WorkspaceDefaultsResponse(
            default_model=default_model,
            default_connectors=default_connectors,
            retention_days=retention_days,
            behavior_overrides=behavior_overrides,
            updated_at=record.updated_at if record is not None else None,
            updated_by_user_id=(
                record.updated_by_user_id if record is not None else None
            ),
        )

    async def get_record(self, *, org_id: str) -> WorkspaceDefaultsRecord | None:
        """Return the raw persisted record (without retention).

        Used by ``ConversationsService.create_conversation`` and
        ``RunService.create_run`` for the local fallback chain — they
        consult ``default_connectors`` / ``default_model`` only and
        don't need the composed retention view.
        """

        return await self._persistence.get_workspace_defaults(org_id=org_id)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def update(
        self,
        *,
        org_id: str,
        actor_user_id: str,
        request: UpdateWorkspaceDefaultsRequest,
        now: datetime | None = None,
    ) -> tuple[WorkspaceDefaultsResponse, dict[str, object]]:
        """Persist defaults + retention; return (response, audit_metadata).

        The audit metadata cross-references the retention rows the
        write produced via ``retention_policy_ids`` so SIEM can chase
        one event back to all storage rows it affected.
        """

        timestamp = now or datetime.now(timezone.utc)
        before_record = await self._persistence.get_workspace_defaults(org_id=org_id)
        before_retention_days = await self._resolve_org_retention_days(org_id=org_id)
        record = update_workspace_defaults_request_to_record(
            org_id=org_id,
            request=request,
            actor_user_id=actor_user_id,
            now=timestamp,
        )
        persisted = await self._persistence.upsert_workspace_defaults(record=record)
        retention_policy_ids = await self._upsert_org_retention(
            org_id=org_id,
            actor_user_id=actor_user_id,
            ttl_seconds=request.retention_days * _SECONDS_PER_DAY,
            now=timestamp,
        )
        response = WorkspaceDefaultsResponse(
            default_model=request.default_model,
            default_connectors=persisted.default_connectors,
            retention_days=request.retention_days,
            behavior_overrides=persisted.behavior_overrides,
            updated_at=persisted.updated_at,
            updated_by_user_id=persisted.updated_by_user_id,
        )
        audit_metadata = self._diff_audit_metadata(
            before_record=before_record,
            before_retention_days=before_retention_days,
            after_record=record,
            after_retention_days=request.retention_days,
            retention_policy_ids=tuple(retention_policy_ids),
        )
        return response, audit_metadata

    # ------------------------------------------------------------------
    # Internal helper — derives the ``training_data_opt_out`` change for
    # the dedicated ``workspace.training_opt_out.update`` audit row when
    # the caller wants to emit it alongside the broader
    # ``workspace.behavior_overrides.update`` row. Centralised here so
    # the route handler doesn't need to re-read the before/after.
    # ------------------------------------------------------------------

    @staticmethod
    def training_opt_out_diff(
        *,
        before: WorkspaceBehaviorOverrides | None,
        after: WorkspaceBehaviorOverrides,
    ) -> tuple[bool, bool] | None:
        """Return ``(before, after)`` iff the flag changed; else ``None``.

        The "compliance" audit row (``workspace.training_opt_out.update``)
        is fired only when the boolean transitions, so a search like
        ``action='workspace.training_opt_out.update'`` returns exactly
        the audit-relevant transitions and nothing else.
        """

        previous = before.training_data_opt_out if before is not None else False
        current = after.training_data_opt_out
        if previous == current:
            return None
        return previous, current

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _deployment_default_model(self) -> DefaultModelSelection:
        """Materialise the deployment-wide model into the wire shape."""

        default = self._settings.default_model
        return DefaultModelSelection(
            provider=default.provider,
            model_name=default.model_name,
            reasoning=(
                default.reasoning.model_dump(mode="json", exclude_none=True)
                if default.reasoning is not None
                else None
            ),
        )

    async def _resolve_org_retention_days(self, *, org_id: str) -> int:
        """Resolve org-scope ``messages`` retention to days (rounded down).

        Falls back to the deployment SaaS default (365d) when no policy
        row exists. ``messages`` is the canonical kind the slider sets;
        events/checkpoints follow the same TTL via ``_upsert_org_retention``.
        """

        policies = await self._persistence.list_retention_policies(org_id=org_id)
        resolver = RetentionPolicyResolver(
            org_id=org_id,
            policies=policies,
            deployment_defaults=DEPLOYMENT_DEFAULT_TTL_SECONDS,
        )
        resolved = resolver.resolve(kind=RetentionKind.MESSAGES)
        ttl = resolved.ttl_seconds or 0
        if ttl <= 0:
            return 0
        return max(1, ttl // _SECONDS_PER_DAY)

    async def _upsert_org_retention(
        self,
        *,
        org_id: str,
        actor_user_id: str,
        ttl_seconds: int,
        now: datetime,
    ) -> Iterable[str]:
        """Write/refresh one ``scope='org'`` row per kind in ``_ORG_KINDS``.

        Returns the policy ids touched so the caller can include them
        in audit metadata. The resolver's existing
        ``(org_id, scope, COALESCE(resource_id, ''), kind)`` unique
        index makes the upserts idempotent: re-submitting the same
        slider value rewrites the same rows.
        """

        ids: list[str] = []
        for kind in _ORG_KINDS:
            record = RetentionPolicyRecord(
                org_id=org_id,
                scope=RetentionScope.ORG,
                resource_id=None,
                kind=kind,
                ttl_seconds=ttl_seconds,
                created_by_user_id=actor_user_id,
                created_at=now,
                updated_at=now,
            )
            persisted = await self._persistence.upsert_retention_policy(record)
            ids.append(persisted.id)
        return ids

    @staticmethod
    def _diff_audit_metadata(
        *,
        before_record: WorkspaceDefaultsRecord | None,
        before_retention_days: int,
        after_record: WorkspaceDefaultsRecord,
        after_retention_days: int,
        retention_policy_ids: tuple[str, ...],
    ) -> dict[str, object]:
        before_overrides = (
            before_record.behavior_overrides
            if before_record is not None
            else WorkspaceBehaviorOverrides()
        )
        before_blob = {
            "default_model": (
                before_record.default_model.model_dump(mode="json", exclude_none=True)
                if before_record is not None and before_record.default_model is not None
                else None
            ),
            "default_connectors": (
                {
                    k: list(v) if v is not None else None
                    for k, v in before_record.default_connectors.items()
                }
                if before_record is not None
                else {}
            ),
            "retention_days": before_retention_days,
            # PR 4.3 — record the full overrides shape (post-redaction
            # by Pydantic; system_prompt_override is in cleartext but
            # auditing the change is the explicit intent).
            "behavior_overrides": before_overrides.model_dump(
                mode="json", exclude_none=True
            ),
        }
        after_blob = {
            "default_model": (
                after_record.default_model.model_dump(mode="json", exclude_none=True)
                if after_record.default_model is not None
                else None
            ),
            "default_connectors": {
                k: list(v) if v is not None else None
                for k, v in after_record.default_connectors.items()
            },
            "retention_days": after_retention_days,
            "behavior_overrides": after_record.behavior_overrides.model_dump(
                mode="json", exclude_none=True
            ),
        }
        diff_keys = sorted(
            key
            for key in (
                "default_model",
                "default_connectors",
                "retention_days",
                "behavior_overrides",
            )
            if before_blob[key] != after_blob[key]
        )
        return {
            "before": before_blob,
            "after": after_blob,
            "diff_keys": diff_keys,
            "retention_policy_ids": list(retention_policy_ids),
        }


__all__ = ("WorkspaceDefaultsService",)

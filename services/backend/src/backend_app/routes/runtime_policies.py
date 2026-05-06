"""``GET /internal/v1/policies/runtime`` (PR 8.0.5).

Aggregate read consumed by ai-backend at run-start. Composes the
existing per-axis stores (``tool_use_policies`` + ``privacy_settings``)
into a single ``RuntimePolicyResponse`` so the AI backend pays one
HTTP round-trip instead of two.

Design notes:

* Reuses :class:`ToolUsePolicyStore` + :class:`PrivacySettingsStore`
  verbatim — no duplicated read logic, no new pydantic types beyond
  the aggregate envelope.
* Always returns a fully-hydrated shape (deployment defaults under
  any unset axis), so the AI backend's snapshot factories see
  the same wire shape regardless of whether the user has stored
  rows or not.
* Picks the *user override* row when ``scope_user_id`` is the caller;
  falls back to the *workspace default* when the user has no override
  for an axis. Composition happens here so every consumer doesn't
  re-implement the merge.
"""

from __future__ import annotations

from enterprise_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, Query, Request
from pydantic import BaseModel, ConfigDict

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes
from backend_app.notifications.store import NotificationPrefsStore  # noqa: F401 — kept for future expansion
from backend_app.policies.store import (
    ToolUsePolicyKind,
    ToolUsePolicyStore,
)
from backend_app.privacy.store import (
    DataResidencyRegion,  # noqa: F401 — re-exported via response shape
    PrivacySettingsStore,
)


# ---------------------------------------------------------------------------
# Wire shapes
# ---------------------------------------------------------------------------


class _ToolUseAxis(BaseModel):
    """Compact ``(kind → mode)`` cell rendered into the aggregate response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    mode: str


class ToolUseSection(BaseModel):
    """Aggregate's tool-use section. Wire-aligned with the AI backend's
    ``ToolUsePolicySnapshot.from_response`` factory which expects a
    ``{kind: mode}`` mapping per scope."""

    model_config = ConfigDict(extra="forbid")

    workspace: dict[str, str]
    user: dict[str, str]


class PrivacySection(BaseModel):
    """Aggregate's privacy section. Wire-aligned with
    ``PrivacySettingsSnapshot.from_response`` (single hydrated row)."""

    model_config = ConfigDict(extra="forbid")

    org_id: str
    user_id: str | None = None
    training_opt_out: bool
    region: str | None = None
    retention_days: int | None = None
    share_metadata: bool
    memory_enabled: bool


class RuntimePolicyResponse(BaseModel):
    """One round-trip = one snapshot. The AI backend caches this on
    ``AgentRuntimeContext.user_policies_json`` for the lifetime of
    the run."""

    model_config = ConfigDict(extra="forbid")

    tool_use: ToolUseSection
    privacy: PrivacySection


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


def register_runtime_policies_routes(
    app: FastAPI,
    *,
    tool_use_store: ToolUsePolicyStore,
    privacy_store: PrivacySettingsStore,
) -> None:
    """Attach ``/internal/v1/policies/runtime`` to the app."""

    @app.get(
        "/internal/v1/policies/runtime",
        response_model=RuntimePolicyResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_runtime_policies(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> RuntimePolicyResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        return RuntimePolicyResponse(
            tool_use=_compose_tool_use(
                store=tool_use_store,
                org_id=identity.org_id,
                user_id=identity.user_id,
            ),
            privacy=_compose_privacy(
                store=privacy_store,
                org_id=identity.org_id,
                user_id=identity.user_id,
            ),
        )


# ---------------------------------------------------------------------------
# Composition helpers
# ---------------------------------------------------------------------------


def _compose_tool_use(
    *,
    store: ToolUsePolicyStore,
    org_id: str,
    user_id: str,
) -> ToolUseSection:
    workspace_rows = store.list_for_scope(org_id=org_id, user_id=None)
    user_rows = store.list_for_scope(org_id=org_id, user_id=user_id)
    return ToolUseSection(
        workspace=_modes_by_kind(workspace_rows),
        user=_modes_by_kind(user_rows),
    )


def _modes_by_kind(rows) -> dict[str, str]:  # type: ignore[no-untyped-def]
    out: dict[str, str] = {}
    for row in rows:
        out[row.kind.value] = row.mode.value
    return out


def _compose_privacy(
    *,
    store: PrivacySettingsStore,
    org_id: str,
    user_id: str,
) -> PrivacySection:
    """Per-user override wins; workspace fills in unset fields."""

    workspace = store.get_for_scope(org_id=org_id, user_id=None)
    user = store.get_for_scope(org_id=org_id, user_id=user_id)

    def _pick_bool(field: str, default: bool) -> bool:
        if user is not None:
            return getattr(user, field)
        if workspace is not None:
            return getattr(workspace, field)
        return default

    def _pick_optional(field: str):  # type: ignore[no-untyped-def]
        if user is not None and getattr(user, field) is not None:
            return getattr(user, field)
        if workspace is not None and getattr(workspace, field) is not None:
            return getattr(workspace, field)
        return None

    region_value = _pick_optional("region")
    return PrivacySection(
        org_id=org_id,
        user_id=user_id,
        training_opt_out=_pick_bool("training_opt_out", True),
        region=region_value.value if region_value is not None else None,
        retention_days=_pick_optional("retention_days"),
        share_metadata=_pick_bool("share_metadata", True),
        memory_enabled=_pick_bool("memory_enabled", True),
    )


# Suppress the "imported but unused" lint on ``ToolUsePolicyKind`` —
# the import keeps the policy enum locally referenced so static
# analysis flags any drift between this module and the store.
_ = ToolUsePolicyKind


__all__ = [
    "PrivacySection",
    "RuntimePolicyResponse",
    "ToolUseSection",
    "register_runtime_policies_routes",
]

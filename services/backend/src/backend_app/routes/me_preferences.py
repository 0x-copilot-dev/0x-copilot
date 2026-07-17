"""``GET /internal/v1/me/preferences`` + ``PUT /internal/v1/me/preferences`` (PR 4.1).

Caller-scoped opinion blob: appearance (theme/accent/density/reduce-motion),
shortcut overrides (chord map), notification matrix (4 events × 3 channels).
The shape is enforced by Pydantic v2 ``extra='forbid'`` so unknown keys
are rejected; future top-level keys are added one Pydantic field at a
time with no migration.

Hydration semantics: when the row is absent we materialise deployment
defaults so the FE always sees a complete shape (same materialisation
PR 1.6's workspace_defaults uses).

Merge semantics on PUT: depth-2 merge — the request supplies a partial
view of the canonical shape (``{appearance: {accent: 'gold'}}`` flips
only that field, leaving theme/density/reduce-motion intact). Toggling
a single notification cell is ``{notifications: {matrix: {mention: {email: false}}}}``.
"""

from __future__ import annotations

import re
from typing import Any

from copilot_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import IdentityAuditEventRecord
from backend_app.identity.me_store import MeStore, UserPreferencesRecord
from backend_app.identity.rbac import RequireScopes
from backend_app.identity.store import IdentityStore


# Canonical enums — kept in sync with packages/api-types and the
# design-system ACCENT_SCHEMES catalogue. Adding a new entry is one
# line in three places (here, api-types, design-system).
THEME_SCHEMES = ("system", "light", "dark", "slate")
ACCENT_SCHEMES = (
    "atlas-orange",
    "gold",
    "amber",
    "red",
    "lime",
    "teal",
    "blue",
    "violet",
)
DENSITIES = ("comfortable", "compact")
REDUCE_MOTIONS = ("auto", "always", "off")
NOTIFICATION_EVENTS = ("mention", "approval_needed", "run_finished", "weekly_digest")
NOTIFICATION_CHANNELS = ("email", "slack", "desktop")

# Subset of the global-keymap registry the FE exposes (PR 2.2). Adding
# a new shortcut id requires updating this set; orphan overrides are
# silently ignored at render time so we don't block a PUT here.
SHORTCUT_IDS = (
    "chat.search",
    "chat.new",
    "chat.toggle.sidebar",
    "chat.approve.focused",
)

# tinykeys chord syntax: a sequence of `+`-joined parts where each part
# is a modifier (``$mod``, ``Shift``, ``Alt``, ``Control``, ``Meta``)
# or a single key. Lower-bound permissive — let unusual combos through
# rather than reject what the FE constructs.
_CHORD_RE = re.compile(r"^[A-Za-z$_][\w$]*(\+[A-Za-z0-9$_\\\/\.\-]+)*$")


# ---------------------------------------------------------------------------
# Canonical shapes (full + partial)
# ---------------------------------------------------------------------------


class AppearancePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    theme: str = "dark"
    accent: str = "atlas-orange"
    density: str = "comfortable"
    reduce_motion: str = "auto"

    @field_validator("theme")
    @classmethod
    def _v_theme(cls, value: str) -> str:
        if value not in THEME_SCHEMES:
            raise ValueError("invalid_request")
        return value

    @field_validator("accent")
    @classmethod
    def _v_accent(cls, value: str) -> str:
        if value not in ACCENT_SCHEMES:
            raise ValueError("invalid_request")
        return value

    @field_validator("density")
    @classmethod
    def _v_density(cls, value: str) -> str:
        if value not in DENSITIES:
            raise ValueError("invalid_request")
        return value

    @field_validator("reduce_motion")
    @classmethod
    def _v_reduce_motion(cls, value: str) -> str:
        if value not in REDUCE_MOTIONS:
            raise ValueError("invalid_request")
        return value


class AppearancePreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: str | None = None
    accent: str | None = None
    density: str | None = None
    reduce_motion: str | None = None

    @field_validator("theme")
    @classmethod
    def _v_theme(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in THEME_SCHEMES:
            raise ValueError("invalid_request")
        return value

    @field_validator("accent")
    @classmethod
    def _v_accent(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in ACCENT_SCHEMES:
            raise ValueError("invalid_request")
        return value

    @field_validator("density")
    @classmethod
    def _v_density(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in DENSITIES:
            raise ValueError("invalid_request")
        return value

    @field_validator("reduce_motion")
    @classmethod
    def _v_reduce_motion(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in REDUCE_MOTIONS:
            raise ValueError("invalid_request")
        return value


class ShortcutsPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    overrides: dict[str, str] = Field(default_factory=dict)

    @field_validator("overrides")
    @classmethod
    def _v_overrides(cls, value: dict[str, str]) -> dict[str, str]:
        for key, chord in value.items():
            if key not in SHORTCUT_IDS:
                raise ValueError("unknown_shortcut")
            if not isinstance(chord, str) or not _CHORD_RE.fullmatch(chord):
                raise ValueError("invalid_chord")
        return value


class ShortcutsPreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overrides: dict[str, str] | None = None

    @field_validator("overrides")
    @classmethod
    def _v_overrides(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        for key, chord in value.items():
            if key not in SHORTCUT_IDS:
                raise ValueError("unknown_shortcut")
            if not isinstance(chord, str) or not _CHORD_RE.fullmatch(chord):
                raise ValueError("invalid_chord")
        return value


class NotificationsPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    matrix: dict[str, dict[str, bool]] = Field(default_factory=dict)

    @field_validator("matrix")
    @classmethod
    def _v_matrix(cls, value: dict[str, dict[str, bool]]) -> dict[str, dict[str, bool]]:
        for event, channels in value.items():
            if event not in NOTIFICATION_EVENTS:
                raise ValueError("unknown_event")
            if not isinstance(channels, dict):
                raise ValueError("invalid_request")
            for channel, enabled in channels.items():
                if channel not in NOTIFICATION_CHANNELS:
                    raise ValueError("invalid_request")
                if not isinstance(enabled, bool):
                    raise ValueError("invalid_request")
        return value


class NotificationsPreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matrix: dict[str, dict[str, bool]] | None = None

    @field_validator("matrix")
    @classmethod
    def _v_matrix(
        cls, value: dict[str, dict[str, bool]] | None
    ) -> dict[str, dict[str, bool]] | None:
        if value is None:
            return None
        return NotificationsPreferences._v_matrix(value)  # type: ignore[arg-type]


# PR 4.4.7 Phase 2 (Slice A) — per-user override for the catalog's
# ``discoverable`` defaults. Replaces the localStorage-backed Phase 1
# storage so the toggle survives across browsers. Slice B will read
# this in the runtime context to drive agent suggestions; Slice A is
# data plumbing only.
#
# Shape: ``overrides`` maps catalog slug → bool. ``true`` forces the
# catalog entry to be suggestible for this user; ``false`` mutes it;
# absent slug = inherit the catalog entry's ``discoverable`` default.
# Validation here is shape-only (slug must be a non-empty string),
# matching ``ConnectorScopeValidator`` — registries are loaded per-run
# on the worker and a slug that is valid at PUT time may have been
# removed by the next run; runtime gates enforce semantics.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class DiscoverableConnectorsPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    overrides: dict[str, bool] = Field(default_factory=dict)

    @field_validator("overrides")
    @classmethod
    def _v_overrides(cls, value: dict[str, bool]) -> dict[str, bool]:
        for key, enabled in value.items():
            if not isinstance(key, str) or not _SLUG_RE.fullmatch(key):
                raise ValueError("invalid_slug")
            if not isinstance(enabled, bool):
                raise ValueError("invalid_request")
        return value


class DiscoverableConnectorsPreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overrides: dict[str, bool] | None = None

    @field_validator("overrides")
    @classmethod
    def _v_overrides(cls, value: dict[str, bool] | None) -> dict[str, bool] | None:
        if value is None:
            return None
        return DiscoverableConnectorsPreferences._v_overrides(value)  # type: ignore[arg-type]


class UserPreferencesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    appearance: AppearancePreferences
    shortcuts: ShortcutsPreferences
    notifications: NotificationsPreferences
    discoverable_connectors: DiscoverableConnectorsPreferences
    updated_at: str


class UpdateUserPreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    appearance: AppearancePreferencesUpdate | None = None
    shortcuts: ShortcutsPreferencesUpdate | None = None
    notifications: NotificationsPreferencesUpdate | None = None
    discoverable_connectors: DiscoverableConnectorsPreferencesUpdate | None = None


# ---------------------------------------------------------------------------
# Defaults — the materialised shape returned when no row exists
# ---------------------------------------------------------------------------


def deployment_default_preferences() -> dict[str, Any]:
    """Single source of truth for "what does a fresh user see".

    Kept as a function (not a module-level constant) so future tests can
    monkeypatch it cleanly — and so the dict isn't accidentally mutated
    in place by callers.
    """

    return {
        "appearance": {
            "theme": "dark",
            "accent": "atlas-orange",
            "density": "comfortable",
            "reduce_motion": "auto",
        },
        "shortcuts": {"overrides": {}},
        "notifications": {
            "matrix": {
                "mention": {"email": True, "slack": False, "desktop": True},
                "approval_needed": {"email": True, "slack": False, "desktop": True},
                "run_finished": {"email": False, "slack": False, "desktop": True},
                "weekly_digest": {"email": True, "slack": False, "desktop": False},
            }
        },
        # PR 4.4.7 Phase 2 (Slice A) — empty by default; absent slug
        # inherits the catalog entry's ``discoverable`` flag so a
        # fresh user gets the curated default and can opt out per
        # vendor without ever opening the toggle.
        "discoverable_connectors": {"overrides": {}},
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def register_me_preferences_routes(
    app: FastAPI,
    *,
    me_store: MeStore,
    identity_store: IdentityStore,
) -> None:
    """Attach ``/internal/v1/me/preferences`` GET + PUT to a backend FastAPI app."""

    @app.get(
        "/internal/v1/me/preferences",
        response_model=UserPreferencesResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_my_preferences(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> UserPreferencesResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        record = me_store.get_preferences(
            org_id=identity.org_id, user_id=identity.user_id
        )
        merged = _merge_with_defaults(record.preferences if record else None)
        return _to_response(merged, record)

    @app.put(
        "/internal/v1/me/preferences",
        response_model=UserPreferencesResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def put_my_preferences(
        request: Request,
        payload: UpdateUserPreferencesRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> UserPreferencesResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        existing = me_store.get_preferences(
            org_id=identity.org_id, user_id=identity.user_id
        )
        before_keys = sorted(_top_level_keys(existing.preferences if existing else {}))

        diff = payload.model_dump(exclude_unset=True, exclude_none=False)
        merged = _deep_merge(existing.preferences if existing else {}, diff)

        # Re-validate the full merged shape so we never persist an invalid
        # blob. Catches the case where a fresh PUT introduces an invalid
        # value via a substructure that was previously default.
        try:
            AppearancePreferences.model_validate(
                merged.get("appearance", deployment_default_preferences()["appearance"])
            )
            ShortcutsPreferences.model_validate(
                merged.get("shortcuts", deployment_default_preferences()["shortcuts"])
            )
            NotificationsPreferences.model_validate(
                merged.get(
                    "notifications", deployment_default_preferences()["notifications"]
                )
            )
            DiscoverableConnectorsPreferences.model_validate(
                merged.get(
                    "discoverable_connectors",
                    deployment_default_preferences()["discoverable_connectors"],
                )
            )
        except Exception as exc:  # pragma: no cover - validators emit specific codes
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

        with me_store.transaction() as conn:
            saved = me_store.upsert_preferences(
                UserPreferencesRecord(
                    user_id=identity.user_id,
                    org_id=identity.org_id,
                    preferences=merged,
                ),
                conn=conn,
            )
            after_keys = sorted(_top_level_keys(merged))
            identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=identity.org_id,
                    actor_user_id=identity.user_id,
                    subject_user_id=identity.user_id,
                    action="user.preferences.update",
                    metadata={
                        "before_keys": before_keys,
                        "after_keys": after_keys,
                        "diff_paths": sorted(_paths(diff)),
                    },
                    request_ip=_request_ip(request),
                    user_agent=request.headers.get("user-agent"),
                ),
                conn=conn,
            )

        merged_response = _merge_with_defaults(saved.preferences)
        return _to_response(merged_response, saved)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


def _merge_with_defaults(
    stored: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply deployment defaults under the stored row's keys."""

    return _deep_merge(deployment_default_preferences(), stored or {})


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """RFC 7396-flavoured merge — overlay wins; nested dicts recurse;
    a non-dict on either side replaces wholesale."""

    if not isinstance(base, dict):
        return overlay  # type: ignore[unreachable]
    out: dict[str, Any] = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _to_response(
    merged: dict[str, Any],
    record: UserPreferencesRecord | None,
) -> UserPreferencesResponse:
    return UserPreferencesResponse(
        appearance=AppearancePreferences.model_validate(merged["appearance"]),
        shortcuts=ShortcutsPreferences.model_validate(merged["shortcuts"]),
        notifications=NotificationsPreferences.model_validate(merged["notifications"]),
        discoverable_connectors=DiscoverableConnectorsPreferences.model_validate(
            merged.get(
                "discoverable_connectors",
                deployment_default_preferences()["discoverable_connectors"],
            )
        ),
        updated_at=(record.updated_at.isoformat() if record else "") or "",
    )


def _top_level_keys(value: dict[str, Any]) -> list[str]:
    return [
        k
        for k in value
        if isinstance(value.get(k), (dict, list, str, bool, int, float))
    ]


def _paths(value: Any, prefix: str = "") -> list[str]:
    """Flatten a partial-update shape into dotted paths for the audit metadata."""

    if not isinstance(value, dict):
        return [prefix] if prefix else []
    result: list[str] = []
    for key, child in value.items():
        next_prefix = f"{prefix}.{key}" if prefix else key
        if isinstance(child, dict):
            result.extend(_paths(child, next_prefix))
        else:
            result.append(next_prefix)
    return result


__all__ = [
    "AppearancePreferences",
    "DiscoverableConnectorsPreferences",
    "NotificationsPreferences",
    "ShortcutsPreferences",
    "UpdateUserPreferencesRequest",
    "UserPreferencesResponse",
    "deployment_default_preferences",
    "register_me_preferences_routes",
]

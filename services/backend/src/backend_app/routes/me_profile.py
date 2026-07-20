"""``GET /internal/v1/me/profile`` + ``PUT /internal/v1/me/profile`` (PR 4.1).

Caller-scoped profile sidecar (title, timezone, locale, working_hours,
avatar_url). The session user identity is the **only** legitimate write
target — there is no admin-as-user impersonation here. Admin reads of
other members' profiles ride the directory route in PR 4.2.

Hydration semantics: when the row is absent we materialise a deployment-
default response (``{display_name, email, email_verified_at}`` from the
session user, every other field ``null``) so the frontend always sees a
complete shape. This mirrors the workspace-defaults fallback PR 1.6 uses
(materialise-on-read keeps the FE one branch simpler).

Validation:
* timezone — must be in ``zoneinfo.available_timezones()``
* locale — BCP-47 shape (``[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*``)
* working_hours — ``start < end``, days each in ``[0, 6]``

RFC 7396 merge-patch on PUT: omit a field to leave it untouched, send
``null`` to clear (``title: null`` clears). Unknown keys are rejected
by Pydantic v2 ``extra='forbid'``.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import available_timezones

from copilot_service_contracts.scopes import RUNTIME_USE
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend_app.auth import BackendServiceAuthenticator
from backend_app.contracts import IdentityAuditEventRecord
from backend_app.identity.me_store import MeStore, UserProfileRecord
from backend_app.identity.rbac import RequireScopes
from backend_app.identity.siwe import (
    chain_display_name,
    display_address,
    is_placeholder_email,
)
from backend_app.identity.oidc_store import OidcStore
from backend_app.identity.siwe_store import SiweStore
from backend_app.identity.store import IdentityStore


_LOGGER = logging.getLogger(__name__)

# BCP-47 grammar simplified: 2- or 3-letter language tag, optional script /
# region / variant subtags. The full grammar is broader but every locale
# the frontend can construct via Intl.Locale matches this shape; we'd
# rather reject odd values than embed the full ABNF.
_BCP47_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# PR 8.2 — bio + avatar caps. Bio is short free text the user surfaces in
# their profile card; the cap matches the FE textarea hint. Avatar v1
# stores a ``data:`` URL inline in the existing column, so the size cap
# guards against accidental DOS — a 256×256 JPEG @ 0.9 typically lands
# under 60 KB and the cap leaves headroom for PNG / WEBP.
_BIO_MAX_LEN = 600
_AVATAR_DATA_URL_RE = re.compile(
    r"^data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=\s]+$"
)
_AVATAR_DATA_URL_MAX_LEN = 200_000


class WorkingHoursModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tz: str
    start: str
    end: str
    days: tuple[int, ...]

    @field_validator("tz")
    @classmethod
    def _validate_tz(cls, value: str) -> str:
        if value not in available_timezones():
            raise ValueError("invalid_timezone")
        return value

    @field_validator("start", "end")
    @classmethod
    def _validate_clock(cls, value: str) -> str:
        if not _HHMM_RE.fullmatch(value):
            raise ValueError("invalid_working_hours")
        return value

    @field_validator("days")
    @classmethod
    def _validate_days(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not all(0 <= d <= 6 for d in value):
            raise ValueError("invalid_working_hours")
        if len(set(value)) != len(value):
            raise ValueError("invalid_working_hours")
        return value

    @model_validator(mode="after")
    def _start_before_end(self) -> "WorkingHoursModel":
        if self.start >= self.end:
            raise ValueError("invalid_working_hours")
        return self


class LinkedIdentityModel(BaseModel):
    """One linked sign-in identity on the account (PRD FR-L4).

    ``kind`` discriminates: ``wallet`` rows carry ``address``/``chain_*``;
    ``oidc`` rows carry ``provider`` (e.g. ``google``) + the email seen at
    link time. ``id`` is the row id a future unlink (FR-L5) targets.
    """

    kind: str  # "wallet" | "oidc"
    id: str
    provider: str | None = None
    email: str | None = None
    address: str | None = None
    chain_id: int | None = None
    chain_name: str | None = None
    linked_at: str


class UserProfileResponse(BaseModel):
    """Public-safe view of the user profile + identity."""

    user_id: str
    email: str
    email_verified_at: str | None
    display_name: str | None
    title: str | None
    timezone: str | None
    locale: str | None
    working_hours: WorkingHoursModel | None
    avatar_url: str | None
    bio: str | None
    updated_at: str
    # Honest identity (wallet vs email). For a SIWE account ``email`` is the
    # undeliverable ``<address>@wallet.invalid`` placeholder that must NEVER be
    # shown; ``email_is_placeholder`` tells the FE to render the wallet anchor
    # instead. ``wallet_address`` is the EIP-55 checksummed form + its chain.
    email_is_placeholder: bool = False
    wallet_address: str | None = None
    chain_id: int | None = None
    chain_name: str | None = None
    # Durable auth origin (``siwe`` / ``google`` / ``local`` / …) from the
    # membership record — drives the "Signed in with" indicator.
    auth_method: str | None = None
    # Account-linking (PRD FR-L4): every sign-in identity linked to this
    # account — the "Linked accounts" panel's data. The singular
    # ``wallet_address`` fields above remain "the" profile wallet
    # (first-linked) for existing consumers.
    linked_identities: list[LinkedIdentityModel] = Field(default_factory=list)


class UpdateUserProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # ``Field(default=...)`` distinguishes "not provided" from "set to
    # null". We use Pydantic v2's exclude_unset on dump to recover the
    # difference at the route layer.
    display_name: str | None = Field(default=None)
    title: str | None = Field(default=None)
    timezone: str | None = Field(default=None)
    locale: str | None = Field(default=None)
    working_hours: WorkingHoursModel | None = Field(default=None)
    avatar_url: str | None = Field(default=None)
    bio: str | None = Field(default=None)

    @field_validator("timezone")
    @classmethod
    def _validate_tz(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in available_timezones():
            raise ValueError("invalid_timezone")
        return value

    @field_validator("locale")
    @classmethod
    def _validate_locale(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _BCP47_RE.fullmatch(value):
            raise ValueError("invalid_locale")
        return value

    @field_validator("bio")
    @classmethod
    def _validate_bio(cls, value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            return None
        if len(trimmed) > _BIO_MAX_LEN:
            raise ValueError("bio_too_long")
        return trimmed

    @field_validator("avatar_url")
    @classmethod
    def _validate_avatar(cls, value: str | None) -> str | None:
        # Empty / null → clear. ``data:`` URLs (Phase 2 inline upload) get
        # size + content-type checks. Other strings are taken as-is on the
        # assumption they're remote URLs; the FE can't render anything
        # actively dangerous because the value is dropped into ``<img>``.
        if value is None:
            return None
        if value == "":
            return None
        if value.startswith("data:"):
            if len(value) > _AVATAR_DATA_URL_MAX_LEN:
                raise ValueError("avatar_too_large")
            if not _AVATAR_DATA_URL_RE.fullmatch(value):
                raise ValueError("avatar_invalid_format")
        return value


def register_me_profile_routes(
    app: FastAPI,
    *,
    me_store: MeStore,
    identity_store: IdentityStore,
    siwe_store: SiweStore | None = None,
    oidc_store: OidcStore | None = None,
) -> None:
    """Attach ``/internal/v1/me/profile`` GET + PUT to a backend FastAPI app.

    ``siwe_store`` / ``oidc_store`` are optional so non-wallet deployments /
    older test harnesses keep working; when present, the profile surfaces the
    caller's wallet address + chain (honest identity instead of the
    ``@wallet.invalid`` placeholder) and the full ``linked_identities`` list
    for the "Linked accounts" panel (PRD FR-L4).
    """

    @app.get(
        "/internal/v1/me/profile",
        response_model=UserProfileResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def get_my_profile(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> UserProfileResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        user = identity_store.get_user(org_id=identity.org_id, user_id=identity.user_id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user_not_found")
        record = me_store.get_profile(org_id=identity.org_id, user_id=identity.user_id)
        wallet, auth_method = _resolve_identity_extras(
            identity_store, siwe_store, identity.org_id, identity.user_id
        )
        linked = _resolve_linked_identities(
            siwe_store, oidc_store, identity.org_id, identity.user_id
        )
        return _hydrate(
            user,
            record,
            wallet=wallet,
            auth_method=auth_method,
            linked_identities=linked,
        )

    @app.put(
        "/internal/v1/me/profile",
        response_model=UserProfileResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def put_my_profile(
        request: Request,
        payload: UpdateUserProfileRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> UserProfileResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        user = identity_store.get_user(org_id=identity.org_id, user_id=identity.user_id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user_not_found")

        # RFC 7396 merge-patch: omitted fields untouched, explicit null clears.
        diff = payload.model_dump(exclude_unset=True)
        existing = me_store.get_profile(
            org_id=identity.org_id, user_id=identity.user_id
        )
        before = _profile_diff_view(user, existing)

        # display_name lives on the identity ``users`` row, not the sidecar.
        # We update it via IdentityStore so SCIM reconciliation sees it,
        # then write the sidecar fields together in one transaction.
        with me_store.transaction() as conn:
            new_display_name = (
                diff["display_name"] if "display_name" in diff else user.display_name
            )
            if "display_name" in diff:
                if new_display_name is None or not str(new_display_name).strip():
                    raise HTTPException(
                        status.HTTP_422_UNPROCESSABLE_ENTITY,
                        "display_name_required",
                    )
                identity_store.update_user(
                    user.model_copy(update={"display_name": new_display_name}),
                    conn=conn,
                )

            sidecar = UserProfileRecord(
                user_id=identity.user_id,
                org_id=identity.org_id,
                title=diff.get("title", existing.title if existing else None),
                timezone=diff.get("timezone", existing.timezone if existing else None),
                locale=diff.get("locale", existing.locale if existing else None),
                working_hours=_dump_working_hours(
                    diff.get(
                        "working_hours",
                        _wh_to_dict(existing.working_hours) if existing else None,
                    )
                ),
                avatar_url=diff.get(
                    "avatar_url", existing.avatar_url if existing else None
                ),
                bio=diff.get("bio", existing.bio if existing else None),
            )
            saved = me_store.upsert_profile(sidecar, conn=conn)

            # Audit (one row per privileged write, append-only chain).
            after = _profile_diff_view_record(
                identity_store.get_user(
                    org_id=identity.org_id, user_id=identity.user_id
                ),
                saved,
            )
            identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=identity.org_id,
                    actor_user_id=identity.user_id,
                    subject_user_id=identity.user_id,
                    action="user.profile.update",
                    metadata={
                        "before": before,
                        "after": after,
                        "diff_keys": sorted(diff.keys()),
                    },
                    request_ip=_request_ip(request),
                    user_agent=request.headers.get("user-agent"),
                ),
                conn=conn,
            )

        refreshed_user = identity_store.get_user(
            org_id=identity.org_id, user_id=identity.user_id
        )
        if refreshed_user is None:
            # Concurrent delete — surface a 404 but the audit row is already
            # captured so forensics has the event.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user_not_found")
        wallet, auth_method = _resolve_identity_extras(
            identity_store, siwe_store, identity.org_id, identity.user_id
        )
        linked = _resolve_linked_identities(
            siwe_store, oidc_store, identity.org_id, identity.user_id
        )
        return _hydrate(
            refreshed_user,
            saved,
            wallet=wallet,
            auth_method=auth_method,
            linked_identities=linked,
        )


def _request_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


def _resolve_identity_extras(
    identity_store: IdentityStore,
    siwe_store: SiweStore | None,
    org_id: str,
    user_id: str,
) -> tuple[Any, str | None]:
    """The caller's wallet link (if any) + durable auth method for the profile.

    Both are best-effort: a wallet lookup failure or an absent ``siwe_store``
    simply yields a non-wallet profile rather than failing the read.
    """

    wallet: Any = None
    if siwe_store is not None:
        try:
            wallet = siwe_store.get_wallet_identity_by_user(
                org_id=org_id, user_id=user_id
            )
        except Exception:  # pragma: no cover - defensive
            wallet = None
    return wallet, _resolve_auth_method(identity_store, org_id, user_id)


def _resolve_linked_identities(
    siwe_store: SiweStore | None,
    oidc_store: OidcStore | None,
    org_id: str,
    user_id: str,
) -> list[LinkedIdentityModel]:
    """Every sign-in identity linked to the account, oldest first per kind.

    Best-effort like the extras: a store failure or an absent store yields an
    empty contribution rather than failing the profile read.
    """

    linked: list[LinkedIdentityModel] = []
    if siwe_store is not None:
        try:
            for wallet in siwe_store.list_wallets_by_user(
                org_id=org_id, user_id=user_id
            ):
                linked.append(
                    LinkedIdentityModel(
                        kind="wallet",
                        id=wallet.wallet_id,
                        address=display_address(wallet.address),
                        chain_id=wallet.chain_id,
                        chain_name=chain_display_name(wallet.chain_id),
                        linked_at=wallet.created_at.isoformat(),
                    )
                )
        except Exception:  # pragma: no cover - defensive
            pass
    if oidc_store is not None:
        try:
            for ident in oidc_store.list_identities_by_user(
                org_id=org_id, user_id=user_id
            ):
                linked.append(
                    LinkedIdentityModel(
                        kind="oidc",
                        id=ident.identity_id,
                        provider=ident.provider_id,
                        email=ident.email_at_link,
                        linked_at=ident.linked_at.isoformat(),
                    )
                )
        except Exception:  # pragma: no cover - defensive
            pass
    return linked


def _resolve_auth_method(
    identity_store: IdentityStore, org_id: str, user_id: str
) -> str | None:
    """Durable auth origin from the membership record (``siwe`` / ``google`` …).

    Mirrors ``routes.members._resolve_member_source`` — a small scan that is a
    single row for a personal org.
    """

    try:
        members = identity_store.list_members(org_id=org_id)
    except Exception:  # pragma: no cover - defensive
        return None
    for member in members:
        if member.user_id == user_id:
            return member.source.value
    return None


def _hydrate(
    user: Any,
    record: UserProfileRecord | None,
    *,
    wallet: Any = None,
    auth_method: str | None = None,
    linked_identities: list[LinkedIdentityModel] | None = None,
) -> UserProfileResponse:
    """Materialise the response from the identity row + (maybe) sidecar.

    Absent sidecar → every column is ``null``; the FE renders a fresh form
    against deployment defaults. When ``wallet`` is present the honest wallet
    anchor (checksummed address + chain) is surfaced and the placeholder email
    is flagged so the FE never shows the ``@wallet.invalid`` address.
    """

    wallet_address = display_address(wallet.address) if wallet is not None else None
    chain_id = wallet.chain_id if wallet is not None else None
    chain_name = chain_display_name(wallet.chain_id) if wallet is not None else None

    return UserProfileResponse(
        user_id=user.user_id,
        email=user.primary_email,
        email_verified_at=_isoformat(user.email_verified_at),
        display_name=user.display_name,
        title=record.title if record else None,
        timezone=record.timezone if record else None,
        locale=record.locale if record else None,
        working_hours=_load_working_hours(record.working_hours)
        if record and record.working_hours is not None
        else None,
        avatar_url=record.avatar_url if record else None,
        bio=record.bio if record else None,
        updated_at=_isoformat(record.updated_at if record else user.updated_at)
        or _isoformat(user.updated_at)
        or "",
        email_is_placeholder=is_placeholder_email(user.primary_email),
        wallet_address=wallet_address,
        chain_id=chain_id,
        chain_name=chain_name,
        auth_method=auth_method,
        linked_identities=linked_identities or [],
    )


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _load_working_hours(raw: dict[str, Any]) -> WorkingHoursModel:
    return WorkingHoursModel(
        tz=raw["tz"],
        start=raw["start"],
        end=raw["end"],
        days=tuple(raw.get("days", ())),
    )


def _dump_working_hours(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, WorkingHoursModel):
        return {
            "tz": value.tz,
            "start": value.start,
            "end": value.end,
            "days": list(value.days),
        }
    if isinstance(value, dict):
        # Validated upstream when value came from the request; carry-through
        # from existing storage already passed validation at write time.
        return value
    raise ValueError("invalid_working_hours")


def _wh_to_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, WorkingHoursModel):
        return _dump_working_hours(value)
    return None


def _profile_diff_view(user: Any, record: UserProfileRecord | None) -> dict[str, Any]:
    return {
        "display_name": user.display_name,
        "title": record.title if record else None,
        "timezone": record.timezone if record else None,
        "locale": record.locale if record else None,
        "avatar_url": record.avatar_url if record else None,
        "working_hours": record.working_hours if record else None,
        "bio": record.bio if record else None,
    }


def _profile_diff_view_record(user: Any, record: UserProfileRecord) -> dict[str, Any]:
    return {
        "display_name": user.display_name if user else None,
        "title": record.title,
        "timezone": record.timezone,
        "locale": record.locale,
        "avatar_url": record.avatar_url,
        "working_hours": record.working_hours,
        "bio": record.bio,
    }


__all__ = [
    "UpdateUserProfileRequest",
    "UserProfileResponse",
    "WorkingHoursModel",
    "register_me_profile_routes",
]

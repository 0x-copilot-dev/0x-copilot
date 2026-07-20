"""OIDC SSO service (A3): authorize URL → callback → session.

The provider config lives in ``auth_providers.config`` JSONB. Recognized
keys (per the A3 spec):

    issuer                       OIDC issuer URL (used in iss check)
    authorization_endpoint
    token_endpoint
    jwks_url
    client_id
    scopes                       list[str], default ["openid","email","profile"]
    auto_provision_user          bool, default false
    group_claim                  str, default "groups"
    group_role_map               dict[group_name -> role_name]
    audience                     str, default = client_id
    token_endpoint_auth_method   "client_secret_post" or "none" (PKCE-only)
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from backend_app.contracts import (
    AuthProviderRecord,
    IdentityAuditEventRecord,
    LoginAttemptKind,
    LoginAttemptOutcome,
    LoginAttemptRecord,
    OidcAuthenticationRecord,
    OidcAuthorizeResult,
    OidcCallbackResult,
    OidcIdentityRecord,
    OidcLinkCallbackResult,
    OidcRefreshTokenRecord,
    OrganizationMemberRecord,
    OrganizationMemberSource,
    OrganizationRecord,
    RoleAssignmentRecord,
    SessionMintResult,
    UserRecord,
)
from backend_app.identity.lockout import LockoutService
from backend_app.identity.mfa import MfaService
from backend_app.identity._pkce import (
    compute_challenge,
    generate_nonce,
    generate_state,
    generate_verifier,
)
from backend_app.identity.jwks import (
    IdTokenVerificationError,
    IdTokenVerifier,
    JwksProvider,
)
from backend_app.identity.oidc_store import OidcStore
from backend_app.identity.provisioning import (
    PersonalOrgSlugExhausted,
    provision_personal_org,
)
from backend_app.identity.sessions import SessionService
from backend_app.identity.siwe import is_placeholder_email
from backend_app.identity.store import IdentityStore
from backend_app.token_vault import TokenVault


_LOGGER = logging.getLogger(__name__)

_AUTH_TTL_SECONDS = 10 * 60  # 10 minutes from authorize → callback
_DEFAULT_SCOPES = ("openid", "email", "profile")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OidcConfigError(RuntimeError):
    """Provider config missing required fields or pointing at the wrong shape."""


class OidcProviderDisabled(RuntimeError):
    """Caller asked to authorize against a provider whose `enabled` flag is off."""


class OidcStateMismatch(RuntimeError):
    """Callback presented a state that's unknown / consumed / expired."""


class OidcUserNotProvisioned(RuntimeError):
    """JIT provisioning is off and the IdP subject isn't linked yet."""


class OidcTokenExchangeError(RuntimeError):
    """Token endpoint returned an error or malformed response."""


class OidcIdentityAlreadyLinked(RuntimeError):
    """The Google/OIDC identity is already bound to a DIFFERENT account.

    The account-merge trigger (PRD FR-M1 / D-01): carries the owning account
    so the caller can route the conflict into the merge flow.
    """

    def __init__(self, *, org_id: str, user_id: str) -> None:
        super().__init__("oidc_identity_linked_to_another_account")
        self.org_id = org_id
        self.user_id = user_id


class OidcEmailNotVerified(RuntimeError):
    """Refusing to LINK an identity whose email the IdP has not verified.

    Linking upgrades a wallet account's placeholder email to the Google
    address (PRD FR-L2) — an unverified address must never overwrite the
    anchor.
    """


# ---------------------------------------------------------------------------
# Pluggable HTTP for the token endpoint (so tests don't need a real network)
# ---------------------------------------------------------------------------


class TokenEndpointClient(Protocol):
    def exchange(
        self, *, token_endpoint: str, body: dict[str, str]
    ) -> dict[str, Any]: ...  # pragma: no cover


class HttpxTokenEndpointClient:
    def __init__(self, *, timeout_seconds: float = 5.0) -> None:
        self._timeout = timeout_seconds

    def exchange(self, *, token_endpoint: str, body: dict[str, str]) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(
                    token_endpoint,
                    data=body,
                    headers={"accept": "application/json"},
                )
                payload = response.json()
        except httpx.HTTPError as exc:
            raise OidcTokenExchangeError(
                f"token endpoint network error: {exc}"
            ) from exc
        except ValueError as exc:
            raise OidcTokenExchangeError(
                f"token endpoint response not JSON: {exc}"
            ) from exc
        if response.status_code >= 400 or not isinstance(payload, dict):
            raise OidcTokenExchangeError(
                f"token endpoint returned {response.status_code}: {payload!r}"
            )
        return payload


# ---------------------------------------------------------------------------
# Provider config wrapper
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OidcProviderConfig:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_url: str
    client_id: str
    scopes: tuple[str, ...]
    auto_provision_user: bool
    group_claim: str
    group_role_map: dict[str, str]
    audience: str
    token_endpoint_auth_method: str
    encrypted_client_secret: str | None

    @classmethod
    def from_provider(cls, provider: AuthProviderRecord) -> "OidcProviderConfig":
        config = provider.config or {}
        if not isinstance(config, dict):
            raise OidcConfigError("provider config must be an object")
        try:
            issuer = _required_text(config, "issuer")
            client_id = _required_text(config, "client_id")
            authorization_endpoint = _required_text(config, "authorization_endpoint")
            token_endpoint = _required_text(config, "token_endpoint")
            jwks_url = _required_text(config, "jwks_url")
        except KeyError as exc:
            raise OidcConfigError(f"missing required OIDC config: {exc}") from exc

        scopes_raw = config.get("scopes")
        scopes: tuple[str, ...]
        if isinstance(scopes_raw, list) and all(isinstance(s, str) for s in scopes_raw):
            scopes = tuple(scopes_raw) or _DEFAULT_SCOPES
        else:
            scopes = _DEFAULT_SCOPES
        if "openid" not in scopes:
            scopes = ("openid",) + scopes

        group_role_map_raw = config.get("group_role_map") or {}
        if not isinstance(group_role_map_raw, dict):
            raise OidcConfigError("group_role_map must be an object")
        group_role_map = {
            str(group): str(role) for group, role in group_role_map_raw.items()
        }

        return cls(
            issuer=issuer,
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
            jwks_url=jwks_url,
            client_id=client_id,
            scopes=scopes,
            auto_provision_user=bool(config.get("auto_provision_user", False)),
            group_claim=str(config.get("group_claim", "groups")),
            group_role_map=group_role_map,
            audience=str(config.get("audience", client_id)),
            token_endpoint_auth_method=str(
                config.get("token_endpoint_auth_method", "client_secret_post")
            ),
            encrypted_client_secret=provider.encrypted_client_secret,
        )


def _required_text(config: dict[str, Any], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise KeyError(key)
    return value.strip()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class OidcService:
    """Authorize-URL / callback / role-sync state machine for OIDC.

    The service does NOT own its own connection pool — it composes the
    identity + OIDC stores. Tests inject in-memory variants; production
    wires the Postgres adapters.
    """

    def __init__(
        self,
        *,
        identity_store: IdentityStore,
        oidc_store: OidcStore,
        sessions: SessionService,
        token_vault: TokenVault,
        token_endpoint_client: TokenEndpointClient | None = None,
        id_token_verifier: IdTokenVerifier | None = None,
        jwks_provider: JwksProvider | None = None,
        lockout: LockoutService | None = None,
        mfa: MfaService | None = None,
        global_providers: Mapping[str, AuthProviderRecord] | None = None,
        allow_self_signup: bool = False,
    ) -> None:
        self._identity_store = identity_store
        self._oidc_store = oidc_store
        self._sessions = sessions
        self._token_vault = token_vault
        self._lockout = lockout
        # Deployment-global providers (today: the env-configured Google
        # provider, reserved id "google"). Resolved before — and therefore
        # shadowing — any per-org auth_providers row with the same id.
        self._global_providers: dict[str, AuthProviderRecord] = dict(
            global_providers or {}
        )
        # Deployment-profile toggle (``allow_self_signup``). Gates whether an
        # unknown subject arriving through a *global* provider gets a personal
        # org provisioned. Per-org providers keep their own
        # ``auto_provision_user`` config flag.
        self._allow_self_signup = allow_self_signup
        # Same opt-in as PasswordService — when wired AND the org's
        # ``identity_policies.mfa_required`` is true AND the user has at
        # least one enrolled factor, the OIDC mint puts the session in
        # the ``mfa:pending`` state until ``MfaService.verify*`` runs.
        self._mfa = mfa
        self._token_endpoint_client = (
            token_endpoint_client or HttpxTokenEndpointClient()
        )
        if id_token_verifier is not None:
            self._id_token_verifier = id_token_verifier
        else:
            resolved_provider = jwks_provider or JwksProvider(store=oidc_store)
            self._id_token_verifier = IdTokenVerifier(jwks_provider=resolved_provider)

    # Authorize ---------------------------------------------------------
    def authorize(
        self,
        *,
        org_id: str,
        provider_id: str,
        redirect_uri: str,
        return_to: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
        link_org_id: str | None = None,
        link_user_id: str | None = None,
    ) -> OidcAuthorizeResult:
        provider, config = self._resolve_provider(
            org_id=org_id, provider_id=provider_id
        )
        if not provider.enabled:
            raise OidcProviderDisabled(f"provider {provider_id} is disabled")
        if self._is_global(provider_id):
            # Global providers are org-less at authorize time — the caller
            # cannot know the workspace before the IdP identifies the user
            # (facade sends the "-" placeholder). Pin the in-flight state
            # row to the provider's sentinel org id; the callback resolves
            # the real org from the linked identity or self-signup.
            org_id = provider.org_id

        verifier = generate_verifier()
        state = generate_state()
        nonce = generate_nonce()
        challenge = compute_challenge(verifier)
        expires_at = _now() + timedelta(seconds=_AUTH_TTL_SECONDS)

        record = OidcAuthenticationRecord(
            org_id=org_id,
            provider_id=provider_id,
            state=state,
            nonce=nonce,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
            return_to=return_to,
            expires_at=expires_at,
            ip=ip,
            user_agent=user_agent,
            # Account-linking (PRD FR-L2): the AUTHENTICATED link-start binds
            # the flow to the caller server-side; the browser round-trip never
            # carries the binding, so it cannot be tampered with.
            link_org_id=link_org_id,
            link_user_id=link_user_id,
        )
        self._oidc_store.create_authentication(record)

        query: dict[str, str] = {
            "response_type": "code",
            "client_id": config.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(config.scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        auth_url = f"{config.authorization_endpoint}?{urlencode(query)}"
        self._identity_store.append_identity_audit(
            self._audit_event(
                provider=provider,
                user=None,
                action="oidc.authorize_started",
                metadata={"state": state},
                ip=ip,
                user_agent=user_agent,
            )
        )
        return OidcAuthorizeResult(
            auth_url=auth_url, state=state, expires_at=expires_at
        )

    # Callback ----------------------------------------------------------
    def callback(
        self,
        *,
        state: str,
        code: str,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> OidcCallbackResult | OidcLinkCallbackResult:
        consumed = self._oidc_store.consume_authentication(state=state)
        if consumed is None:
            self._record_login_attempt(
                org_id=None,
                outcome=LoginAttemptOutcome.PROVIDER_REJECTED,
                ip=ip,
                user_agent=user_agent,
                failure_reason="state mismatch / expired / replay",
            )
            raise OidcStateMismatch("state mismatch / expired / replay")

        provider, config = self._resolve_provider(
            org_id=consumed.org_id, provider_id=consumed.provider_id
        )
        token_payload = self._exchange_code(consumed=consumed, config=config, code=code)
        id_token = token_payload.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            self._record_login_attempt(
                org_id=consumed.org_id,
                outcome=LoginAttemptOutcome.PROVIDER_REJECTED,
                ip=ip,
                user_agent=user_agent,
                failure_reason="token endpoint returned no id_token",
            )
            raise OidcTokenExchangeError("token endpoint returned no id_token")

        try:
            claims = self._id_token_verifier.verify(
                provider_id=provider.provider_id,
                jwks_url=config.jwks_url,
                id_token=id_token,
                issuer=config.issuer,
                audience=config.audience,
                nonce=consumed.nonce,
            )
        except IdTokenVerificationError as exc:
            self._record_login_attempt(
                org_id=consumed.org_id,
                outcome=LoginAttemptOutcome.PROVIDER_REJECTED,
                ip=ip,
                user_agent=user_agent,
                failure_reason=str(exc),
            )
            # No user resolved yet — record_failure is a no-op for
            # user_id=None but the sliding-window count grows in
            # login_attempts so a later resolved user trips the lockout.
            if self._lockout is not None:
                self._lockout.record_failure(
                    org_id=consumed.org_id, user_id=None, email=None
                )
            raise

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise IdTokenVerificationError("id_token missing sub claim")
        email_claim = claims.get("email")
        email = email_claim if isinstance(email_claim, str) and email_claim else None

        # Account-linking fork (PRD FR-L2): a link-bound state row diverts to
        # the authenticated-link path — attach the identity to the bound
        # caller, upgrade a placeholder email, and DO NOT mint a session.
        if consumed.link_user_id is not None and consumed.link_org_id is not None:
            return self._link_identity_to_user(
                consumed=consumed,
                provider=provider,
                subject=subject,
                email=email,
                claims=claims,
                ip=ip,
                user_agent=user_agent,
            )

        user = self._link_or_provision_user(
            provider=provider,
            config=config,
            subject=subject,
            email=email,
            claims=claims,
            ip=ip,
            user_agent=user_agent,
        )
        if self._lockout is not None:
            # Lockout pre-check after the user is known but before we mint
            # the session. A locked user with a fresh IdP assertion still
            # 423s (spec §2.5).
            self._lockout.check_or_raise(org_id=user.org_id, user_id=user.user_id)
        self._sync_role_assignments(
            provider=provider, config=config, user=user, claims=claims
        )
        self._maybe_store_refresh_token(
            provider=provider, user=user, token_payload=token_payload
        )

        session, mfa_required = self._mint_session(user=user, provider=provider)
        self._record_login_attempt(
            org_id=user.org_id,
            user_id=user.user_id,
            outcome=LoginAttemptOutcome.SUCCESS,
            ip=ip,
            user_agent=user_agent,
        )
        if self._lockout is not None:
            self._lockout.record_success(org_id=user.org_id, user_id=user.user_id)
        return OidcCallbackResult(
            user_id=user.user_id,
            session_id=session.session_id,
            bearer_token=session.bearer_token,
            expires_at=session.expires_at,
            return_to=consumed.return_to,
            requires_mfa=mfa_required,
        )

    # Authenticated link (PRD FR-L2) -------------------------------------
    def _link_identity_to_user(
        self,
        *,
        consumed: OidcAuthenticationRecord,
        provider: AuthProviderRecord,
        subject: str,
        email: str | None,
        claims: dict[str, Any],
        ip: str | None,
        user_agent: str | None,
    ) -> OidcLinkCallbackResult:
        """Attach the verified IdP identity to the link-bound caller.

        Divergences from ``_link_or_provision_user`` (deliberate, PRD §6.2):
        never provisions, never mints a session, requires
        ``email_verified is True`` (the link may UPGRADE the caller's
        placeholder email — an unverified address must never become the
        anchor), and a subject owned by another account raises the merge
        trigger instead of signing that account in.
        """

        org_id = consumed.link_org_id
        user_id = consumed.link_user_id
        assert org_id is not None and user_id is not None  # guarded by caller
        caller = self._identity_store.get_user(org_id=org_id, user_id=user_id)
        if caller is None:
            raise OidcUserNotProvisioned("link-bound caller no longer exists")

        if claims.get("email_verified") is not True:
            raise OidcEmailNotVerified(
                "IdP did not assert email_verified; refusing to link"
            )

        existing = self._oidc_store.get_identity_by_subject(
            provider_id=provider.provider_id, subject=subject
        )
        status = "linked"
        identity_id: str
        if existing is not None:
            if existing.org_id == org_id and existing.user_id == user_id:
                # FR-L6: already mine — idempotent no-op.
                status = "already_linked"
                identity_id = existing.identity_id
            else:
                # FR-M1: owned by another account — the merge flow's trigger.
                raise OidcIdentityAlreadyLinked(
                    org_id=existing.org_id, user_id=existing.user_id
                )
        else:
            created = self._oidc_store.create_identity(
                OidcIdentityRecord(
                    org_id=org_id,
                    user_id=user_id,
                    provider_id=provider.provider_id,
                    subject=subject,
                    email_at_link=email,
                    claims_snapshot=_safe_claims_snapshot(claims),
                )
            )
            identity_id = created.identity_id

        # Placeholder-email upgrade (FR-L2): a wallet account gains its first
        # real, VERIFIED address. Collision-guarded against the per-org
        # unique(lower(email)) index; on collision the link stands but the
        # email is left untouched (the merge engine reconciles duplicates).
        email_upgraded = False
        if (
            status == "linked"
            and email is not None
            and is_placeholder_email(caller.primary_email)
        ):
            collision = self._identity_store.get_user_by_email(
                org_id=org_id, email=email
            )
            if collision is None or collision.user_id == user_id:
                self._identity_store.update_user(
                    caller.model_copy(
                        update={
                            "primary_email": email,
                            "email_verified_at": _now(),
                        }
                    )
                )
                email_upgraded = True

        self._identity_store.append_identity_audit(
            self._audit_event(
                provider=provider,
                user=caller,
                action="oidc.identity_linked",
                metadata={
                    "identity_id": identity_id,
                    "status": status,
                    "email_upgraded": email_upgraded,
                },
                ip=ip,
                user_agent=user_agent,
            )
        )
        return OidcLinkCallbackResult(
            status=status,
            user_id=user_id,
            provider_id=provider.provider_id,
            email=email,
            email_upgraded=email_upgraded,
            return_to=consumed.return_to,
        )

    # Helpers -----------------------------------------------------------
    def _is_global(self, provider_id: str) -> bool:
        return provider_id in self._global_providers

    def _resolve_provider(
        self, *, org_id: str, provider_id: str
    ) -> tuple[AuthProviderRecord, OidcProviderConfig]:
        global_record = self._global_providers.get(provider_id)
        if global_record is not None:
            # Env-configured global provider (e.g. "google"): resolution is
            # org-independent and never reads auth_providers — the persisted
            # anchor row exists only so Postgres FKs hold.
            if global_record.kind.value != "oidc":
                raise OidcConfigError(
                    f"global provider {provider_id} is not an OIDC provider"
                )
            return global_record, OidcProviderConfig.from_provider(global_record)
        provider = self._identity_store.get_auth_provider(
            org_id=org_id, provider_id=provider_id
        )
        if provider is None:
            raise OidcConfigError(f"no OIDC provider {provider_id} for org {org_id}")
        if provider.kind.value != "oidc":
            raise OidcConfigError(f"provider {provider_id} is not an OIDC provider")
        return provider, OidcProviderConfig.from_provider(provider)

    def _exchange_code(
        self,
        *,
        consumed: OidcAuthenticationRecord,
        config: OidcProviderConfig,
        code: str,
    ) -> dict[str, Any]:
        body: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": consumed.redirect_uri,
            "code_verifier": consumed.code_verifier,
            "client_id": config.client_id,
        }
        if config.token_endpoint_auth_method == "client_secret_post":
            if config.encrypted_client_secret is None:
                raise OidcConfigError(
                    "client_secret_post requires encrypted_client_secret"
                )
            body["client_secret"] = self._token_vault.decrypt(
                config.encrypted_client_secret
            )
        return self._token_endpoint_client.exchange(
            token_endpoint=config.token_endpoint, body=body
        )

    def _link_or_provision_user(
        self,
        *,
        provider: AuthProviderRecord,
        config: OidcProviderConfig,
        subject: str,
        email: str | None,
        claims: dict[str, Any],
        ip: str | None,
        user_agent: str | None,
    ) -> UserRecord:
        existing = self._oidc_store.get_identity_by_subject(
            provider_id=provider.provider_id, subject=subject
        )
        if existing is not None:
            user = self._identity_store.get_user(
                org_id=existing.org_id, user_id=existing.user_id
            )
            if user is None:
                raise OidcUserNotProvisioned(
                    "linked OIDC identity points at a deleted user"
                )
            self._oidc_store.update_identity_claims(
                identity_id=existing.identity_id,
                claims_snapshot=_safe_claims_snapshot(claims),
                email_at_link=email,
            )
            self._identity_store.append_identity_audit(
                self._audit_event(
                    provider=provider,
                    user=user,
                    action="oidc.callback_succeeded",
                    metadata={"subject": subject, "provisioned": False},
                    ip=ip,
                    user_agent=user_agent,
                )
            )
            return user

        if self._is_global(provider.provider_id):
            # Global providers (e.g. "google") never JIT-provision into the
            # provider's org — an unknown subject is a brand-new signup that
            # gets its own personal org, gated by the deployment profile.
            return self._self_signup_provision(
                provider=provider,
                subject=subject,
                email=email,
                claims=claims,
                ip=ip,
                user_agent=user_agent,
            )

        if not config.auto_provision_user:
            self._record_login_attempt(
                org_id=provider.org_id,
                outcome=LoginAttemptOutcome.UNKNOWN_USER,
                ip=ip,
                user_agent=user_agent,
                failure_reason="JIT provisioning disabled",
            )
            raise OidcUserNotProvisioned(
                "OIDC subject not linked and auto_provision_user is off"
            )
        if email is None:
            raise OidcUserNotProvisioned(
                "id_token has no email claim; cannot JIT-provision user"
            )

        display_name = claims.get("name") or claims.get("preferred_username") or email

        with self._identity_store.transaction():
            user = self._identity_store.create_user(
                UserRecord(
                    org_id=provider.org_id,
                    primary_email=email,
                    display_name=str(display_name),
                )
            )
            self._identity_store.add_member(
                OrganizationMemberRecord(
                    org_id=provider.org_id,
                    user_id=user.user_id,
                    source=OrganizationMemberSource.OIDC,
                )
            )
            self._identity_store.append_identity_audit(
                self._audit_event(
                    provider=provider,
                    user=user,
                    action="oidc.user_provisioned",
                    metadata={"subject": subject, "email": email},
                    ip=ip,
                    user_agent=user_agent,
                )
            )

        self._oidc_store.create_identity(
            OidcIdentityRecord(
                org_id=provider.org_id,
                user_id=user.user_id,
                provider_id=provider.provider_id,
                subject=subject,
                email_at_link=email,
                claims_snapshot=_safe_claims_snapshot(claims),
            )
        )
        return user

    def _self_signup_provision(
        self,
        *,
        provider: AuthProviderRecord,
        subject: str,
        email: str | None,
        claims: dict[str, Any],
        ip: str | None,
        user_agent: str | None,
    ) -> UserRecord:
        """First login through a global provider: create a personal org.

        Provisions org + user + membership + admin role + identity link in
        one shot, mirroring the per-org JIT path's shape. The sole member of
        a personal org is its admin (system ``admin`` role). Refused when
        the deployment profile's ``allow_self_signup`` toggle is off — same
        ``OidcUserNotProvisioned`` surface as JIT-off per-org providers.
        """

        if not self._allow_self_signup:
            self._record_login_attempt(
                org_id=provider.org_id,
                outcome=LoginAttemptOutcome.UNKNOWN_USER,
                ip=ip,
                user_agent=user_agent,
                failure_reason="self-signup disabled by deployment profile",
            )
            raise OidcUserNotProvisioned(
                "OIDC subject not linked and self-signup is disabled "
                "for this deployment"
            )
        if email is None:
            raise OidcUserNotProvisioned(
                "id_token has no email claim; cannot self-provision user"
            )
        email_verified = claims.get("email_verified")
        if email_verified is False:
            # The IdP explicitly says the address is unverified — refuse to
            # anchor a brand-new account on it (email drives org slug,
            # discovery, and magic-link routing).
            self._record_login_attempt(
                org_id=provider.org_id,
                outcome=LoginAttemptOutcome.PROVIDER_REJECTED,
                ip=ip,
                user_agent=user_agent,
                failure_reason="IdP reports email not verified",
            )
            raise OidcUserNotProvisioned(
                "IdP reports the email address is not verified; refusing self-signup"
            )

        display_name = claims.get("name") or claims.get("preferred_username") or email
        local_part = email.split("@", 1)[0]

        def _signup_audit_events(
            org: OrganizationRecord, user: UserRecord
        ) -> list[IdentityAuditEventRecord]:
            return [
                self._audit_event(
                    provider=provider,
                    user=user,
                    action="oidc.self_signup_org_created",
                    metadata={"org_slug": org.slug, "subject": subject},
                    ip=ip,
                    user_agent=user_agent,
                ),
                self._audit_event(
                    provider=provider,
                    user=user,
                    action="oidc.user_provisioned",
                    metadata={"subject": subject, "email": email},
                    ip=ip,
                    user_agent=user_agent,
                ),
            ]

        try:
            org, user = provision_personal_org(
                identity_store=self._identity_store,
                org_display_name=local_part,
                slug_base=local_part,
                primary_email=email,
                user_display_name=str(display_name),
                email_verified_at=_now() if email_verified is True else None,
                member_source=OrganizationMemberSource.OIDC,
                audit_events=_signup_audit_events,
            )
        except PersonalOrgSlugExhausted as exc:  # pragma: no cover - 32 collisions
            raise OidcConfigError(str(exc)) from exc

        self._oidc_store.create_identity(
            OidcIdentityRecord(
                org_id=org.org_id,
                user_id=user.user_id,
                provider_id=provider.provider_id,
                subject=subject,
                email_at_link=email,
                claims_snapshot=_safe_claims_snapshot(claims),
            )
        )
        return user

    def _sync_role_assignments(
        self,
        *,
        provider: AuthProviderRecord,
        config: OidcProviderConfig,
        user: UserRecord,
        claims: dict[str, Any],
    ) -> None:
        if not config.group_role_map:
            return
        raw_groups = claims.get(config.group_claim) or ()
        if isinstance(raw_groups, str):
            raw_groups = (raw_groups,)
        if not isinstance(raw_groups, (list, tuple)):
            return
        desired_role_names = {
            config.group_role_map[group]
            for group in raw_groups
            if isinstance(group, str) and group in config.group_role_map
        }
        if not desired_role_names:
            return
        desired_roles = []
        for role_name in desired_role_names:
            role = self._identity_store.get_role_by_name(
                org_id=provider.org_id, name=role_name
            )
            if role is None:
                role = self._identity_store.get_role_by_name(
                    org_id=None, name=role_name
                )
            if role is not None:
                desired_roles.append(role)

        existing = {
            record.role_id
            for record in self._identity_store.list_role_assignments(
                org_id=provider.org_id, user_id=user.user_id
            )
        }
        for role in desired_roles:
            if role.role_id in existing:
                continue
            self._identity_store.assign_role(
                RoleAssignmentRecord(
                    org_id=provider.org_id,
                    user_id=user.user_id,
                    role_id=role.role_id,
                )
            )
            self._identity_store.append_identity_audit(
                self._audit_event(
                    provider=provider,
                    user=user,
                    action="oidc.role_synced",
                    metadata={"role": role.name, "role_id": role.role_id},
                )
            )

    def _maybe_store_refresh_token(
        self,
        *,
        provider: AuthProviderRecord,
        user: UserRecord,
        token_payload: dict[str, Any],
    ) -> None:
        refresh_token_value = token_payload.get("refresh_token")
        if not isinstance(refresh_token_value, str) or not refresh_token_value:
            return
        with self._oidc_store.transaction():
            self._oidc_store.revoke_active_refresh_tokens(
                org_id=user.org_id,
                user_id=user.user_id,
                provider_id=provider.provider_id,
            )
            expires_at_raw = token_payload.get("expires_in")
            expires_at: datetime | None = None
            if isinstance(expires_at_raw, (int, float)) and expires_at_raw > 0:
                expires_at = _now() + timedelta(seconds=int(expires_at_raw))
            self._oidc_store.store_refresh_token(
                OidcRefreshTokenRecord(
                    org_id=user.org_id,
                    user_id=user.user_id,
                    provider_id=provider.provider_id,
                    encrypted_refresh_token=self._token_vault.encrypt(
                        refresh_token_value
                    ),
                    scope=tuple(_scope_iter(token_payload.get("scope"))),
                    expires_at=expires_at,
                )
            )
        self._identity_store.append_identity_audit(
            self._audit_event(
                provider=provider,
                user=user,
                action="oidc.refresh_rotated",
                metadata={},
            )
        )

    def _mint_session(
        self,
        *,
        user: UserRecord,
        provider: AuthProviderRecord,
    ) -> tuple[SessionMintResult, bool]:
        """Returns ``(session, requires_mfa)`` so the caller can wire the
        ``OidcCallbackResult.requires_mfa`` flag without re-running the
        policy check."""
        role_records = self._identity_store.list_role_assignments(
            org_id=user.org_id, user_id=user.user_id
        )
        role_names: list[str] = []
        permission_scopes: set[str] = set()
        for assignment in role_records:
            role = self._identity_store.get_role(role_id=assignment.role_id)
            if role is None:
                continue
            role_names.append(role.name)
            permission_scopes.update(role.permission_scopes)
        if not role_names:
            role_names = ["employee"]
            employee = self._identity_store.get_role_by_name(
                org_id=None, name="employee"
            )
            if employee is not None:
                permission_scopes.update(employee.permission_scopes)
        mfa_required = (
            self._mfa is not None
            and self._mfa.policy_requires_mfa(org_id=user.org_id)
            and self._mfa.has_enabled_factor(org_id=user.org_id, user_id=user.user_id)
        )
        session_scopes: tuple[str, ...] = (
            ("mfa:pending",) if mfa_required else tuple(sorted(permission_scopes))
        )
        result = self._sessions.create(
            org_id=user.org_id,
            user_id=user.user_id,
            roles=tuple(role_names),
            permission_scopes=session_scopes,
            auth_provider_id=provider.provider_id,
            device_label="oidc",
        )
        return result, mfa_required

    def _record_login_attempt(
        self,
        *,
        org_id: str | None,
        outcome: LoginAttemptOutcome,
        ip: str | None,
        user_agent: str | None,
        user_id: str | None = None,
        failure_reason: str | None = None,
    ) -> None:
        self._identity_store.append_login_attempt(
            LoginAttemptRecord(
                org_id=org_id,
                user_id=user_id,
                auth_kind=LoginAttemptKind.OIDC,
                outcome=outcome,
                ip=ip,
                user_agent=user_agent,
                failure_reason=failure_reason,
            )
        )

    @staticmethod
    def _audit_event(
        *,
        provider: AuthProviderRecord,
        user: UserRecord | None,
        action: str,
        metadata: dict[str, Any],
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> IdentityAuditEventRecord:
        # Attribute to the resolved user's org when we have one. For per-org
        # providers the two are identical; for global providers (google) the
        # provider carries a sentinel org while the user lives in a real org.
        return IdentityAuditEventRecord(
            org_id=user.org_id if user is not None else provider.org_id,
            actor_user_id=user.user_id if user else None,
            subject_user_id=user.user_id if user else None,
            action=action,
            metadata={
                **metadata,
                "provider_id": provider.provider_id,
                "provider_kind": provider.kind.value,
            },
            request_ip=ip,
            user_agent=user_agent,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scope_iter(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        for token in value.split():
            if token:
                yield token
    elif isinstance(value, (list, tuple)):
        for token in value:
            if isinstance(token, str) and token.strip():
                yield token.strip()


def _safe_claims_snapshot(claims: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "sub",
        "iss",
        "aud",
        "email",
        "email_verified",
        "name",
        "preferred_username",
        "groups",
    }
    return {key: value for key, value in claims.items() if key in keep}


__all__ = [
    "HttpxTokenEndpointClient",
    "OidcConfigError",
    "OidcProviderConfig",
    "OidcProviderDisabled",
    "OidcService",
    "OidcStateMismatch",
    "OidcTokenExchangeError",
    "OidcUserNotProvisioned",
    "TokenEndpointClient",
]

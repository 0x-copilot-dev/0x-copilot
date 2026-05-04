"""SAML 2.0 SSO service (A5): authorize → ACS → session.

Mirrors :class:`backend_app.identity.oidc.OidcService` so the two SSO paths
share the same JIT / role-sync / lockout / MFA semantics. The only meaningful
divergence is the trust model: OIDC validates an ID-token JWT against a
JWKS, SAML validates a signed XML assertion against a per-provider PEM cert
held inside :class:`SamlVerifier`.

The verifier itself is injected so tests can wire ``FakeSamlVerifier`` and
not require the ``xmlsec1`` system library.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from backend_app.contracts import (
    AuthProviderRecord,
    IdentityAuditEventRecord,
    LoginAttemptKind,
    LoginAttemptOutcome,
    LoginAttemptRecord,
    OrganizationMemberRecord,
    OrganizationMemberSource,
    RoleAssignmentRecord,
    SamlAuthenticationRecord,
    SamlAuthenticationStatus,
    SamlAuthorizeResult,
    SamlConsumeResult,
    SamlIdentityRecord,
    SessionMintResult,
    UserRecord,
)
from backend_app.identity._saml_lib import (
    ParsedSamlAssertion,
    SamlAssertionExpired,
    SamlAudienceMismatch,
    SamlInResponseToMismatch,
    SamlMissingAssertion,
    SamlProviderConfig,
    SamlSignatureError,
    SamlVerifier,
    SamlVerifierError,
)
from backend_app.identity.lockout import LockoutService
from backend_app.identity.mfa import MfaService
from backend_app.identity.saml_store import (
    SamlAuthenticationNotFound,
    SamlReplayDetected,
    SamlStore,
)
from backend_app.identity.sessions import SessionService
from backend_app.identity.store import IdentityStore


_LOGGER = logging.getLogger(__name__)

_AUTH_TTL_SECONDS = 10 * 60  # SP-initiated authn-request validity window


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Errors — translated to HTTP status codes by routes/saml.py
# ---------------------------------------------------------------------------


class SamlConfigError(RuntimeError):
    """Provider config missing required fields or wrong shape."""


class SamlProviderDisabled(RuntimeError):
    """Caller asked to use a SAML provider whose ``enabled`` flag is off."""


class SamlIdpInitiatedDisabled(RuntimeError):
    """Assertion arrived without InResponseTo and provider doesn't allow that."""


class SamlUserNotProvisioned(RuntimeError):
    """JIT provisioning is off and the IdP NameID isn't linked yet."""


# ---------------------------------------------------------------------------
# Provider config wrapper
# ---------------------------------------------------------------------------


def _required_text(config: Mapping[str, Any], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SamlConfigError(f"missing required SAML config: {key!r}")
    return value.strip()


def _config_to_provider_config(provider: AuthProviderRecord) -> SamlProviderConfig:
    config = provider.config or {}
    if not isinstance(config, dict):
        raise SamlConfigError("provider config must be an object")

    attribute_map_raw = config.get("attribute_map") or {}
    if not isinstance(attribute_map_raw, dict):
        raise SamlConfigError("attribute_map must be an object")
    attribute_map = {str(k): str(v) for k, v in attribute_map_raw.items()}

    group_role_map_raw = config.get("group_role_map") or {}
    if not isinstance(group_role_map_raw, dict):
        raise SamlConfigError("group_role_map must be an object")
    group_role_map = {str(k): str(v) for k, v in group_role_map_raw.items()}

    return SamlProviderConfig(
        provider_id=provider.provider_id,
        idp_entity_id=_required_text(config, "idp_entity_id"),
        idp_sso_url=_required_text(config, "idp_sso_url"),
        idp_x509_cert=_required_text(config, "idp_x509_cert"),
        sp_entity_id=_required_text(config, "sp_entity_id"),
        sp_acs_url=_required_text(config, "sp_acs_url"),
        attribute_map=attribute_map,
        allow_idp_initiated=bool(config.get("allow_idp_initiated", False)),
        auto_provision_user=bool(config.get("auto_provision_user", False)),
        group_role_map=group_role_map,
        sp_signing_key_ref=config.get("sp_signing_key_ref"),
        sp_decryption_key_ref=config.get("sp_decryption_key_ref"),
    )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass
class SamlService:
    """Authorize → ACS state machine for SAML 2.0 SSO.

    ``verifier`` is injected — production wires
    :class:`backend_app.identity._saml_lib.OneLoginSamlVerifier`, tests wire
    :class:`FakeSamlVerifier`.
    """

    identity_store: IdentityStore
    saml_store: SamlStore
    sessions: SessionService
    verifier: SamlVerifier
    lockout: LockoutService | None = None
    mfa: MfaService | None = None

    # Authorize ---------------------------------------------------------
    def authorize(
        self,
        *,
        org_id: str,
        provider_id: str,
        relay_state: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> SamlAuthorizeResult:
        provider, config = self._resolve_provider(
            org_id=org_id, provider_id=provider_id
        )
        if not provider.enabled:
            raise SamlProviderDisabled(f"provider {provider_id} is disabled")

        built = self.verifier.build_authn_request(
            provider=config, relay_state=relay_state
        )
        expires_at = _now() + timedelta(seconds=_AUTH_TTL_SECONDS)
        record = SamlAuthenticationRecord(
            org_id=org_id,
            provider_id=provider_id,
            request_id=built.request_id,
            # Provisional: real assertion_id is stamped on consume. Use the
            # request_id as a placeholder so the unique-on-assertion_id
            # index doesn't trip if we end up writing two pending rows.
            assertion_id=f"pending:{built.request_id}",
            relay_state=relay_state,
            status=SamlAuthenticationStatus.PENDING,
            expires_at=expires_at,
            ip=ip,
            user_agent=user_agent,
        )
        self.saml_store.create_authentication(record)
        self.identity_store.append_identity_audit(
            self._audit_event(
                provider=provider,
                user=None,
                action="saml.authorize_started",
                metadata={"request_id": built.request_id},
                ip=ip,
                user_agent=user_agent,
            )
        )
        return SamlAuthorizeResult(
            auth_id=record.auth_id,
            request_id=built.request_id,
            sso_url=built.redirect_url,
            request_xml=built.request_xml,
            binding="HTTP-Redirect",
            expires_at=expires_at,
        )

    # Consume (ACS) -----------------------------------------------------
    def consume(
        self,
        *,
        provider_id: str,
        saml_response_b64: str,
        relay_state: str | None = None,
        expected_in_response_to: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> SamlConsumeResult:
        provider, config = self._resolve_provider_unscoped(provider_id=provider_id)
        if not provider.enabled:
            raise SamlProviderDisabled(f"provider {provider_id} is disabled")

        try:
            assertion = self.verifier.parse_response(
                provider=config,
                saml_response_b64=saml_response_b64,
                expected_in_response_to=expected_in_response_to,
            )
        except (
            SamlSignatureError,
            SamlAssertionExpired,
            SamlAudienceMismatch,
            SamlInResponseToMismatch,
            SamlMissingAssertion,
            SamlVerifierError,
        ) as exc:
            self._record_login_attempt(
                org_id=provider.org_id,
                outcome=LoginAttemptOutcome.PROVIDER_REJECTED,
                ip=ip,
                user_agent=user_agent,
                failure_reason=str(exc),
            )
            self.identity_store.append_identity_audit(
                self._audit_event(
                    provider=provider,
                    user=None,
                    action="saml.acs_failed",
                    metadata={
                        "reason": exc.__class__.__name__,
                        "detail": str(exc),
                    },
                    ip=ip,
                    user_agent=user_agent,
                )
            )
            raise

        if assertion.in_response_to is None and not config.allow_idp_initiated:
            self._record_login_attempt(
                org_id=provider.org_id,
                outcome=LoginAttemptOutcome.PROVIDER_REJECTED,
                ip=ip,
                user_agent=user_agent,
                failure_reason="idp-initiated assertion rejected",
            )
            raise SamlIdpInitiatedDisabled(
                "provider does not allow IdP-initiated assertions"
            )

        # Persist (with replay defense). Either pulls + flips the existing
        # pending row (SP-initiated) or inserts a fresh consumed row
        # (IdP-initiated).
        try:
            self.saml_store.consume_authentication(
                provider_id=provider_id,
                assertion_id=assertion.assertion_id,
                request_id=assertion.in_response_to,
            )
        except SamlReplayDetected:
            self._record_login_attempt(
                org_id=provider.org_id,
                outcome=LoginAttemptOutcome.PROVIDER_REJECTED,
                ip=ip,
                user_agent=user_agent,
                failure_reason="assertion replay",
            )
            self.identity_store.append_identity_audit(
                self._audit_event(
                    provider=provider,
                    user=None,
                    action="saml.acs_failed",
                    metadata={"reason": "replay"},
                    ip=ip,
                    user_agent=user_agent,
                )
            )
            raise
        except SamlAuthenticationNotFound as exc:
            self._record_login_attempt(
                org_id=provider.org_id,
                outcome=LoginAttemptOutcome.PROVIDER_REJECTED,
                ip=ip,
                user_agent=user_agent,
                failure_reason="no pending authn request",
            )
            raise SamlInResponseToMismatch(str(exc)) from exc

        user = self._link_or_provision_user(
            provider=provider,
            config=config,
            assertion=assertion,
            ip=ip,
            user_agent=user_agent,
        )
        if self.lockout is not None:
            self.lockout.check_or_raise(org_id=user.org_id, user_id=user.user_id)
        self._sync_role_assignments(
            provider=provider, config=config, user=user, assertion=assertion
        )
        session, mfa_required = self._mint_session(user=user, provider=provider)
        self._record_login_attempt(
            org_id=user.org_id,
            user_id=user.user_id,
            outcome=LoginAttemptOutcome.SUCCESS,
            ip=ip,
            user_agent=user_agent,
        )
        if self.lockout is not None:
            self.lockout.record_success(org_id=user.org_id, user_id=user.user_id)
        self.identity_store.append_identity_audit(
            self._audit_event(
                provider=provider,
                user=user,
                action="saml.acs_succeeded",
                metadata={"name_id": assertion.name_id},
                ip=ip,
                user_agent=user_agent,
            )
        )
        return SamlConsumeResult(
            user_id=user.user_id,
            session_id=session.session_id,
            bearer_token=session.bearer_token,
            expires_at=session.expires_at,
            relay_state=relay_state,
            requires_mfa=mfa_required,
        )

    # Metadata ----------------------------------------------------------
    def metadata(self, *, org_id: str, provider_id: str) -> str:
        _provider, config = self._resolve_provider(
            org_id=org_id, provider_id=provider_id
        )
        return self.verifier.build_metadata(provider=config)

    # Helpers -----------------------------------------------------------
    def _resolve_provider(
        self, *, org_id: str, provider_id: str
    ) -> tuple[AuthProviderRecord, SamlProviderConfig]:
        provider = self.identity_store.get_auth_provider(
            org_id=org_id, provider_id=provider_id
        )
        if provider is None:
            raise SamlConfigError(f"no SAML provider {provider_id} for org {org_id}")
        if provider.kind.value != "saml":
            raise SamlConfigError(f"provider {provider_id} is not a SAML provider")
        return provider, _config_to_provider_config(provider)

    def _resolve_provider_unscoped(
        self, *, provider_id: str
    ) -> tuple[AuthProviderRecord, SamlProviderConfig]:
        # ACS endpoint accepts assertions without org_id from the URL — the
        # provider row carries the org and the (provider_id, name_id) lookup
        # is org-bound, so cross-tenant attacks are still impossible.
        provider = self.identity_store.get_auth_provider_by_id(provider_id)
        if provider is None:
            raise SamlConfigError(f"no SAML provider {provider_id}")
        if provider.kind.value != "saml":
            raise SamlConfigError(f"provider {provider_id} is not a SAML provider")
        return provider, _config_to_provider_config(provider)

    def _link_or_provision_user(
        self,
        *,
        provider: AuthProviderRecord,
        config: SamlProviderConfig,
        assertion: ParsedSamlAssertion,
        ip: str | None,
        user_agent: str | None,
    ) -> UserRecord:
        existing = self.saml_store.get_identity_by_name_id(
            provider_id=provider.provider_id, name_id=assertion.name_id
        )
        if existing is not None:
            user = self.identity_store.get_user(
                org_id=existing.org_id, user_id=existing.user_id
            )
            if user is None:
                raise SamlUserNotProvisioned(
                    "linked SAML identity points at a deleted user"
                )
            self.saml_store.update_identity_attributes(
                identity_id=existing.identity_id,
                attributes_snapshot=_safe_attributes(assertion.attributes),
            )
            return user

        if not config.auto_provision_user:
            self._record_login_attempt(
                org_id=provider.org_id,
                outcome=LoginAttemptOutcome.UNKNOWN_USER,
                ip=ip,
                user_agent=user_agent,
                failure_reason="JIT provisioning disabled",
            )
            raise SamlUserNotProvisioned(
                "SAML NameID not linked and auto_provision_user is off"
            )

        email = _attribute_value(assertion.attributes, config.attribute_map, "email")
        if not email:
            raise SamlUserNotProvisioned(
                "SAML assertion missing email attribute; cannot JIT-provision user"
            )
        display_name = (
            _attribute_value(assertion.attributes, config.attribute_map, "display_name")
            or email
        )

        with self.identity_store.transaction():
            user = self.identity_store.create_user(
                UserRecord(
                    org_id=provider.org_id,
                    primary_email=email,
                    display_name=display_name,
                )
            )
            self.identity_store.add_member(
                OrganizationMemberRecord(
                    org_id=provider.org_id,
                    user_id=user.user_id,
                    source=OrganizationMemberSource.SAML,
                )
            )
            self.identity_store.append_identity_audit(
                self._audit_event(
                    provider=provider,
                    user=user,
                    action="saml.user_provisioned",
                    metadata={"name_id": assertion.name_id, "email": email},
                    ip=ip,
                    user_agent=user_agent,
                )
            )
        self.saml_store.create_identity(
            SamlIdentityRecord(
                org_id=provider.org_id,
                user_id=user.user_id,
                provider_id=provider.provider_id,
                name_id=assertion.name_id,
                name_id_format=assertion.name_id_format
                or "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified",
                attributes_snapshot=_safe_attributes(assertion.attributes),
            )
        )
        return user

    def _sync_role_assignments(
        self,
        *,
        provider: AuthProviderRecord,
        config: SamlProviderConfig,
        user: UserRecord,
        assertion: ParsedSamlAssertion,
    ) -> None:
        if not config.group_role_map:
            return
        groups_attr = config.attribute_map.get("groups", "groups")
        raw_groups = assertion.attributes.get(groups_attr) or ()
        desired_role_names = {
            config.group_role_map[group]
            for group in raw_groups
            if group in config.group_role_map
        }
        if not desired_role_names:
            return

        desired_roles = []
        for role_name in desired_role_names:
            role = self.identity_store.get_role_by_name(
                org_id=provider.org_id, name=role_name
            )
            if role is None:
                role = self.identity_store.get_role_by_name(org_id=None, name=role_name)
            if role is not None:
                desired_roles.append(role)

        existing = {
            assignment.role_id
            for assignment in self.identity_store.list_role_assignments(
                org_id=provider.org_id, user_id=user.user_id
            )
        }
        for role in desired_roles:
            if role.role_id in existing:
                continue
            self.identity_store.assign_role(
                RoleAssignmentRecord(
                    org_id=provider.org_id,
                    user_id=user.user_id,
                    role_id=role.role_id,
                )
            )
            self.identity_store.append_identity_audit(
                self._audit_event(
                    provider=provider,
                    user=user,
                    action="saml.role_synced",
                    metadata={"role": role.name, "role_id": role.role_id},
                )
            )

    def _mint_session(
        self,
        *,
        user: UserRecord,
        provider: AuthProviderRecord,
    ) -> tuple[SessionMintResult, bool]:
        role_records = self.identity_store.list_role_assignments(
            org_id=user.org_id, user_id=user.user_id
        )
        role_names: list[str] = []
        permission_scopes: set[str] = set()
        for assignment in role_records:
            role = self.identity_store.get_role(role_id=assignment.role_id)
            if role is None:
                continue
            role_names.append(role.name)
            permission_scopes.update(role.permission_scopes)
        if not role_names:
            role_names = ["employee"]
            employee = self.identity_store.get_role_by_name(
                org_id=None, name="employee"
            )
            if employee is not None:
                permission_scopes.update(employee.permission_scopes)
        mfa_required = (
            self.mfa is not None
            and self.mfa.policy_requires_mfa(org_id=user.org_id)
            and self.mfa.has_enabled_factor(org_id=user.org_id, user_id=user.user_id)
        )
        session_scopes: tuple[str, ...] = (
            ("mfa:pending",) if mfa_required else tuple(sorted(permission_scopes))
        )
        result = self.sessions.create(
            org_id=user.org_id,
            user_id=user.user_id,
            roles=tuple(role_names),
            permission_scopes=session_scopes,
            auth_provider_id=provider.provider_id,
            device_label="saml",
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
        self.identity_store.append_login_attempt(
            LoginAttemptRecord(
                org_id=org_id,
                user_id=user_id,
                auth_kind=LoginAttemptKind.SAML,
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
        return IdentityAuditEventRecord(
            org_id=provider.org_id,
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


def _attribute_value(
    attributes: Mapping[str, list[str]],
    attribute_map: Mapping[str, str],
    logical_name: str,
) -> str | None:
    """Look up an attribute under the configured mapping, falling back to
    the logical name itself when the map doesn't override it."""
    mapped = attribute_map.get(logical_name, logical_name)
    values = attributes.get(mapped)
    if not values:
        return None
    candidate = values[0]
    return candidate if candidate else None


def _safe_attributes(
    attributes: Mapping[str, list[str]],
) -> dict[str, list[str]]:
    """Project assertion attributes for at-rest storage (no PII filtering;
    encrypted at rest follows in C7)."""
    return {str(k): [str(v) for v in values] for k, values in attributes.items()}


__all__ = [
    "SamlConfigError",
    "SamlIdpInitiatedDisabled",
    "SamlProviderDisabled",
    "SamlService",
    "SamlUserNotProvisioned",
]

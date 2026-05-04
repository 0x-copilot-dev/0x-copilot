"""MFA service (A6): TOTP + WebAuthn + recovery codes + step-up.

The service is the single seat for the cryptographic detail; routes call
high-level methods (``enroll_totp``, ``confirm_totp``, ``challenge``,
``verify``) and never touch ``pyotp`` / ``webauthn`` directly. Tests can
drive the service against ``InMemoryMfaStore`` without spinning up the
HTTP layer.

Reusable: every login path (A4 local, A3 OIDC, future A5 SAML) calls
``MfaService.policy_requires_mfa(org_id)`` after a successful credential
verify and, if true, mints the session with ``mfa:pending`` instead of
proceeding to the authenticated state. The session is satisfied when
``MfaService.verify(...)`` succeeds.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pyotp
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url, parse_authentication_credential_json
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    UserVerificationRequirement,
)

from backend_app.contracts import (
    IdentityAuditEventRecord,
    MfaChallengeKind,
    MfaChallengeRecord,
    MfaFactorKind,
    MfaFactorRecord,
    MfaRecoveryCodeRecord,
    TotpEnrollResult,
    TotpSecretRecord,
    WebAuthnCredentialRecord,
)
from backend_app.identity.mfa_store import MfaStore
from backend_app.identity.store import IdentityStore
from backend_app.token_vault import TokenVault


_LOGGER = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MfaError(RuntimeError):
    """Base class. Routes catch and translate to 4xx."""


class MfaFactorNotFound(MfaError):
    pass


class MfaFactorDisabled(MfaError):
    pass


class MfaChallengeInvalid(MfaError):
    """Challenge expired, replayed, or for the wrong user."""


class MfaCodeRejected(MfaError):
    """TOTP / recovery-code value didn't verify."""


class MfaWebAuthnRejected(MfaError):
    """WebAuthn assertion failed signature / sign-count check."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MfaConfig:
    totp_step_seconds: int = 30
    totp_window_steps: int = 1  # accept ±1 step (matches RFC 6238 §5.2)
    challenge_ttl_seconds: int = 60 * 5
    recovery_code_count: int = 10
    recovery_code_byte_length: int = 10  # ~16-char base32 chunks


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class MfaService:
    """High-level orchestration for MFA enrollment + verify.

    Invariants:
      - TOTP secrets are stored encrypted via ``TokenVault``; plaintext
        only exists in memory between enroll and confirm (and again
        during verify).
      - Each challenge consumed at most once (CAS in
        ``mfa_store.consume_challenge``).
      - Recovery codes single-use; only the sha256 hash is persisted.
      - WebAuthn ``sign_count`` strictly monotonic — non-monotonic
        decrement is rejected as a cloned-credential signal.
    """

    def __init__(
        self,
        *,
        identity_store: IdentityStore,
        mfa_store: MfaStore,
        token_vault: TokenVault,
        config: MfaConfig | None = None,
    ) -> None:
        self._identity_store = identity_store
        self._mfa_store = mfa_store
        self._token_vault = token_vault
        self._config = config or MfaConfig()

    # ------------------------------------------------------------------
    # Policy + listing
    # ------------------------------------------------------------------
    def policy_requires_mfa(self, *, org_id: str) -> bool:
        policy = self._identity_store.get_identity_policy(org_id=org_id)
        return bool(policy and policy.mfa_required)

    def step_up_window_seconds(self, *, org_id: str) -> int:
        policy = self._identity_store.get_identity_policy(org_id=org_id)
        return policy.step_up_window_seconds if policy else 300

    def list_factors(self, *, org_id: str, user_id: str) -> tuple[MfaFactorRecord, ...]:
        return self._mfa_store.list_factors(org_id=org_id, user_id=user_id)

    def has_enabled_factor(self, *, org_id: str, user_id: str) -> bool:
        return any(
            f.enabled
            for f in self._mfa_store.list_factors(
                org_id=org_id, user_id=user_id, enabled_only=True
            )
        )

    def disable_factor(
        self, *, org_id: str, user_id: str, factor_id: str
    ) -> MfaFactorRecord:
        factor = self._mfa_store.get_factor(factor_id=factor_id)
        if factor is None or factor.org_id != org_id or factor.user_id != user_id:
            raise MfaFactorNotFound("factor not found")
        disabled = self._mfa_store.disable_factor(factor_id=factor_id)
        if disabled is None:
            raise MfaFactorNotFound("factor already disabled")
        self._audit(
            org_id=org_id,
            user_id=user_id,
            action="mfa.factor.removed",
            metadata={"factor_id": factor_id, "kind": factor.kind.value},
        )
        return disabled

    # ------------------------------------------------------------------
    # TOTP enrollment + confirmation
    # ------------------------------------------------------------------
    def enroll_totp(
        self,
        *,
        org_id: str,
        user_id: str,
        display_name: str,
        issuer: str = "Enterprise Search",
        account_name: str | None = None,
    ) -> TotpEnrollResult:
        secret_b32 = pyotp.random_base32()
        encrypted = self._token_vault.encrypt(secret_b32)
        factor = self._mfa_store.create_factor(
            MfaFactorRecord(
                org_id=org_id,
                user_id=user_id,
                kind=MfaFactorKind.TOTP,
                display_name=display_name,
                enabled=False,
            )
        )
        self._mfa_store.create_totp_secret(
            TotpSecretRecord(
                factor_id=factor.factor_id,
                encrypted_secret=encrypted,
            )
        )
        # Generate recovery codes alongside TOTP enrollment so the user
        # captures them in the same screen flow. Spec §3.1: 10 codes.
        recovery_codes = self._generate_recovery_codes()
        self._mfa_store.store_recovery_codes(
            tuple(
                MfaRecoveryCodeRecord(
                    org_id=org_id,
                    user_id=user_id,
                    code_hash=_sha256_hex(code),
                )
                for code in recovery_codes
            )
        )
        otpauth_url = pyotp.TOTP(
            secret_b32, interval=self._config.totp_step_seconds
        ).provisioning_uri(
            name=account_name or user_id,
            issuer_name=issuer,
        )
        self._audit(
            org_id=org_id,
            user_id=user_id,
            action="mfa.factor.enrolled",
            metadata={"factor_id": factor.factor_id, "kind": "totp"},
        )
        return TotpEnrollResult(
            factor_id=factor.factor_id,
            otpauth_url=otpauth_url,
            secret_b32=secret_b32,
            recovery_codes=tuple(recovery_codes),
        )

    def confirm_totp(
        self,
        *,
        org_id: str,
        user_id: str,
        factor_id: str,
        code: str,
    ) -> MfaFactorRecord:
        # Tenant + kind guard. We don't need the returned record because
        # ``enable_factor`` runs against the same factor_id.
        self._require_factor_for_user(
            org_id=org_id,
            user_id=user_id,
            factor_id=factor_id,
            kind=MfaFactorKind.TOTP,
        )
        secret = self._mfa_store.get_totp_secret_for_factor(factor_id=factor_id)
        if secret is None:
            raise MfaFactorNotFound("TOTP secret missing")
        plaintext = self._token_vault.decrypt(secret.encrypted_secret)
        verified, step = _verify_totp_with_window(
            secret_b32=plaintext,
            code=code,
            window_steps=self._config.totp_window_steps,
            step_seconds=self._config.totp_step_seconds,
        )
        if not verified:
            self._audit(
                org_id=org_id,
                user_id=user_id,
                action="mfa.verify.failed",
                metadata={"factor_id": factor_id, "kind": "totp", "stage": "confirm"},
            )
            raise MfaCodeRejected("invalid TOTP code")
        # Replay guard: same step can't be reused for a second confirm.
        if secret.last_step is not None and step <= secret.last_step:
            raise MfaCodeRejected("TOTP code replayed")
        self._mfa_store.update_totp_last_step(
            secret_id=secret.secret_id, last_step=step
        )
        enabled = self._mfa_store.enable_factor(factor_id=factor_id)
        if enabled is None:
            raise MfaFactorDisabled("factor was disabled before confirm")
        self._mfa_store.touch_factor(factor_id=factor_id, when=_now())
        self._audit(
            org_id=org_id,
            user_id=user_id,
            action="mfa.verify.succeeded",
            metadata={"factor_id": factor_id, "kind": "totp", "stage": "confirm"},
        )
        return enabled

    # ------------------------------------------------------------------
    # WebAuthn enrollment
    # ------------------------------------------------------------------
    def webauthn_register_options(
        self,
        *,
        org_id: str,
        user_id: str,
        display_name: str,
        rp_id: str,
        rp_name: str,
        user_name: str,
        user_display_name: str | None = None,
    ) -> tuple[MfaFactorRecord, MfaChallengeRecord, dict[str, object]]:
        existing_credentials = self._mfa_store.list_webauthn_credentials_for_user(
            org_id=org_id, user_id=user_id
        )
        excluded = [
            PublicKeyCredentialDescriptor(
                id=base64.urlsafe_b64decode(_pad(c.credential_id_b64))
            )
            for c in existing_credentials
        ]
        options = generate_registration_options(
            rp_id=rp_id,
            rp_name=rp_name,
            user_id=user_id.encode("utf-8"),
            user_name=user_name,
            user_display_name=user_display_name or display_name,
            exclude_credentials=excluded,
            authenticator_selection=AuthenticatorSelectionCriteria(
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
            supported_pub_key_algs=[
                COSEAlgorithmIdentifier.ECDSA_SHA_256,
                COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
            ],
        )
        factor = self._mfa_store.create_factor(
            MfaFactorRecord(
                org_id=org_id,
                user_id=user_id,
                kind=MfaFactorKind.WEBAUTHN,
                display_name=display_name,
                enabled=False,
            )
        )
        challenge_b64 = bytes_to_base64url(options.challenge)
        challenge = self._mfa_store.create_challenge(
            MfaChallengeRecord(
                org_id=org_id,
                user_id=user_id,
                kind=MfaChallengeKind.WEBAUTHN,
                nonce=challenge_b64,
                expected_factor_id=factor.factor_id,
                payload={"rp_id": rp_id, "stage": "register"},
                expires_at=_now()
                + timedelta(seconds=self._config.challenge_ttl_seconds),
            )
        )
        # Convert the SDK options to a JSON-safe dict so the route layer
        # doesn't need to import webauthn types.
        import json as _json

        options_payload = _json.loads(options_to_json(options))
        return factor, challenge, options_payload

    def webauthn_register_finish(
        self,
        *,
        org_id: str,
        user_id: str,
        factor_id: str,
        challenge_id: str,
        rp_id: str,
        expected_origin: str,
        attestation: dict[str, object],
    ) -> WebAuthnCredentialRecord:
        challenge = self._mfa_store.consume_challenge(
            challenge_id=challenge_id, now=_now()
        )
        if (
            challenge is None
            or challenge.user_id != user_id
            or challenge.org_id != org_id
        ):
            raise MfaChallengeInvalid("challenge not found / expired / wrong user")
        if challenge.expected_factor_id != factor_id:
            raise MfaChallengeInvalid("challenge factor mismatch")
        factor = self._require_factor_for_user(
            org_id=org_id,
            user_id=user_id,
            factor_id=factor_id,
            kind=MfaFactorKind.WEBAUTHN,
        )
        try:
            verification = verify_registration_response(
                credential=attestation,
                expected_challenge=base64.urlsafe_b64decode(_pad(challenge.nonce)),
                expected_origin=expected_origin,
                expected_rp_id=rp_id,
                require_user_verification=False,
            )
        except (
            Exception
        ) as exc:  # webauthn raises InvalidRegistrationResponse subclasses
            raise MfaWebAuthnRejected(str(exc)) from exc
        credential_id_b64 = bytes_to_base64url(verification.credential_id)
        record = self._mfa_store.create_webauthn_credential(
            WebAuthnCredentialRecord(
                factor_id=factor.factor_id,
                credential_id_b64=credential_id_b64,
                public_key_cose=verification.credential_public_key,
                sign_count=verification.sign_count,
                aaguid=str(verification.aaguid) if verification.aaguid else None,
                attestation_format=verification.fmt or "none",
                rp_id=rp_id,
            )
        )
        enabled = self._mfa_store.enable_factor(factor_id=factor.factor_id)
        if enabled is None:
            raise MfaFactorDisabled("factor was disabled before register/finish")
        self._mfa_store.touch_factor(factor_id=factor.factor_id, when=_now())
        self._audit(
            org_id=org_id,
            user_id=user_id,
            action="mfa.factor.enrolled",
            metadata={
                "factor_id": factor.factor_id,
                "kind": "webauthn",
                "rp_id": rp_id,
            },
        )
        return record

    # ------------------------------------------------------------------
    # Verify (post-login challenge)
    # ------------------------------------------------------------------
    def issue_challenge(
        self,
        *,
        org_id: str,
        user_id: str,
        kind: MfaChallengeKind,
        factor_id: str | None = None,
        rp_id: str | None = None,
    ) -> tuple[MfaChallengeRecord, dict[str, object] | None]:
        if kind == MfaChallengeKind.WEBAUTHN:
            credentials = self._mfa_store.list_webauthn_credentials_for_user(
                org_id=org_id, user_id=user_id
            )
            if not credentials:
                raise MfaFactorNotFound("no WebAuthn factors enrolled")
            if rp_id is None:
                rp_id = credentials[0].rp_id
            options = generate_authentication_options(
                rp_id=rp_id,
                allow_credentials=[
                    PublicKeyCredentialDescriptor(
                        id=base64.urlsafe_b64decode(_pad(c.credential_id_b64))
                    )
                    for c in credentials
                ],
                user_verification=UserVerificationRequirement.PREFERRED,
            )
            challenge_b64 = bytes_to_base64url(options.challenge)
            challenge = self._mfa_store.create_challenge(
                MfaChallengeRecord(
                    org_id=org_id,
                    user_id=user_id,
                    kind=MfaChallengeKind.WEBAUTHN,
                    nonce=challenge_b64,
                    expected_factor_id=factor_id,
                    payload={"rp_id": rp_id, "stage": "verify"},
                    expires_at=_now()
                    + timedelta(seconds=self._config.challenge_ttl_seconds),
                )
            )
            import json as _json

            options_payload = _json.loads(options_to_json(options))
            return challenge, options_payload
        # TOTP / recovery: no extra payload — the user just types the code.
        nonce = secrets.token_urlsafe(24)
        challenge = self._mfa_store.create_challenge(
            MfaChallengeRecord(
                org_id=org_id,
                user_id=user_id,
                kind=kind,
                nonce=nonce,
                expected_factor_id=factor_id,
                payload={},
                expires_at=_now()
                + timedelta(seconds=self._config.challenge_ttl_seconds),
            )
        )
        return challenge, None

    def verify_totp_challenge(
        self,
        *,
        org_id: str,
        user_id: str,
        challenge_id: str,
        code: str,
    ) -> MfaFactorRecord:
        challenge = self._mfa_store.consume_challenge(
            challenge_id=challenge_id, now=_now()
        )
        if (
            challenge is None
            or challenge.user_id != user_id
            or challenge.org_id != org_id
            or challenge.kind != MfaChallengeKind.TOTP
        ):
            raise MfaChallengeInvalid("challenge not found / expired / wrong kind")
        # Try each enabled TOTP factor; spec allows multiple. Common case
        # is exactly one.
        factors = [
            f
            for f in self._mfa_store.list_factors(
                org_id=org_id, user_id=user_id, enabled_only=True
            )
            if f.kind == MfaFactorKind.TOTP
        ]
        if challenge.expected_factor_id is not None:
            factors = [
                f for f in factors if f.factor_id == challenge.expected_factor_id
            ]
        if not factors:
            raise MfaFactorNotFound("no enabled TOTP factor")
        for factor in factors:
            secret = self._mfa_store.get_totp_secret_for_factor(
                factor_id=factor.factor_id
            )
            if secret is None:
                continue
            plaintext = self._token_vault.decrypt(secret.encrypted_secret)
            verified, step = _verify_totp_with_window(
                secret_b32=plaintext,
                code=code,
                window_steps=self._config.totp_window_steps,
                step_seconds=self._config.totp_step_seconds,
            )
            if not verified:
                continue
            if secret.last_step is not None and step <= secret.last_step:
                continue
            self._mfa_store.update_totp_last_step(
                secret_id=secret.secret_id, last_step=step
            )
            self._mfa_store.touch_factor(factor_id=factor.factor_id, when=_now())
            self._audit(
                org_id=org_id,
                user_id=user_id,
                action="mfa.verify.succeeded",
                metadata={"factor_id": factor.factor_id, "kind": "totp"},
            )
            return factor
        self._audit(
            org_id=org_id,
            user_id=user_id,
            action="mfa.verify.failed",
            metadata={"kind": "totp"},
        )
        raise MfaCodeRejected("invalid TOTP code")

    def verify_webauthn_challenge(
        self,
        *,
        org_id: str,
        user_id: str,
        challenge_id: str,
        assertion: dict[str, object],
        expected_origin: str,
    ) -> MfaFactorRecord:
        challenge = self._mfa_store.consume_challenge(
            challenge_id=challenge_id, now=_now()
        )
        if (
            challenge is None
            or challenge.user_id != user_id
            or challenge.org_id != org_id
            or challenge.kind != MfaChallengeKind.WEBAUTHN
        ):
            raise MfaChallengeInvalid("challenge not found / expired / wrong kind")
        rp_id = (
            challenge.payload.get("rp_id")
            if isinstance(challenge.payload, dict)
            else None
        )
        if not isinstance(rp_id, str):
            raise MfaChallengeInvalid("challenge missing rp_id")
        # Resolve the credential the assertion claims.
        try:
            parsed = parse_authentication_credential_json(assertion)
        except Exception as exc:
            raise MfaWebAuthnRejected(f"malformed assertion: {exc}") from exc
        credential_id_b64 = bytes_to_base64url(parsed.raw_id)
        stored = self._mfa_store.get_webauthn_credential_by_b64(
            credential_id_b64=credential_id_b64
        )
        if stored is None:
            raise MfaWebAuthnRejected("unknown credential")
        # Make sure the credential belongs to this user / tenant.
        owned = self._mfa_store.list_webauthn_credentials_for_user(
            org_id=org_id, user_id=user_id
        )
        if not any(c.credential_id == stored.credential_id for c in owned):
            raise MfaWebAuthnRejected("credential not owned by user")
        try:
            verification = verify_authentication_response(
                credential=assertion,
                expected_challenge=base64.urlsafe_b64decode(_pad(challenge.nonce)),
                expected_origin=expected_origin,
                expected_rp_id=rp_id,
                credential_public_key=stored.public_key_cose,
                credential_current_sign_count=stored.sign_count,
                require_user_verification=False,
            )
        except Exception as exc:
            self._audit(
                org_id=org_id,
                user_id=user_id,
                action="mfa.verify.failed",
                metadata={"kind": "webauthn", "reason": str(exc)[:120]},
            )
            raise MfaWebAuthnRejected(str(exc)) from exc
        ok = self._mfa_store.update_webauthn_sign_count(
            credential_id_b64=credential_id_b64,
            new_sign_count=verification.new_sign_count,
            when=_now(),
        )
        if not ok:
            # CAS failed — sign_count went non-monotonic between read and
            # write. Treated as cloned credential.
            self._audit(
                org_id=org_id,
                user_id=user_id,
                action="mfa.verify.failed",
                metadata={"kind": "webauthn", "reason": "sign_count_regression"},
            )
            raise MfaWebAuthnRejected("sign_count regression — possible cloned key")
        factor = self._mfa_store.get_factor(factor_id=stored.factor_id)
        if factor is None:
            raise MfaFactorNotFound("factor missing")
        self._mfa_store.touch_factor(factor_id=factor.factor_id, when=_now())
        self._audit(
            org_id=org_id,
            user_id=user_id,
            action="mfa.verify.succeeded",
            metadata={"factor_id": factor.factor_id, "kind": "webauthn"},
        )
        return factor

    def consume_recovery_code(
        self, *, org_id: str, user_id: str, code: str
    ) -> MfaRecoveryCodeRecord:
        record = self._mfa_store.consume_recovery_code(
            code_hash=_sha256_hex(code), now=_now()
        )
        if record is None or record.org_id != org_id or record.user_id != user_id:
            self._audit(
                org_id=org_id,
                user_id=user_id,
                action="mfa.recovery.failed",
                metadata={},
            )
            raise MfaCodeRejected("invalid or already-used recovery code")
        self._audit(
            org_id=org_id,
            user_id=user_id,
            action="mfa.recovery.consumed",
            metadata={"code_id": record.code_id},
        )
        return record

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _require_factor_for_user(
        self,
        *,
        org_id: str,
        user_id: str,
        factor_id: str,
        kind: MfaFactorKind,
    ) -> MfaFactorRecord:
        factor = self._mfa_store.get_factor(factor_id=factor_id)
        if (
            factor is None
            or factor.org_id != org_id
            or factor.user_id != user_id
            or factor.disabled_at is not None
        ):
            raise MfaFactorNotFound("factor not found")
        if factor.kind != kind:
            raise MfaFactorNotFound("factor kind mismatch")
        return factor

    def _generate_recovery_codes(self) -> list[str]:
        out: list[str] = []
        for _ in range(self._config.recovery_code_count):
            raw = secrets.token_bytes(self._config.recovery_code_byte_length)
            # Group as 4-char chunks for the user-visible form; sha256 of
            # the raw concatenated form is what's stored.
            encoded = base64.b32encode(raw).decode("ascii").rstrip("=")
            out.append("-".join(encoded[i : i + 4] for i in range(0, len(encoded), 4)))
        return out

    def _audit(
        self,
        *,
        org_id: str,
        user_id: str | None,
        action: str,
        metadata: dict[str, object],
    ) -> None:
        self._identity_store.append_identity_audit(
            IdentityAuditEventRecord(
                org_id=org_id,
                actor_user_id=user_id,
                subject_user_id=user_id,
                action=action,
                metadata=metadata,
            )
        )


# ---------------------------------------------------------------------------
# Free helpers
# ---------------------------------------------------------------------------


def _verify_totp_with_window(
    *,
    secret_b32: str,
    code: str,
    window_steps: int,
    step_seconds: int,
) -> tuple[bool, int]:
    """Return (verified, step_value). Step value is the unix-time bucket
    that matched, used by callers for the replay guard."""

    totp = pyotp.TOTP(secret_b32, interval=step_seconds)
    timestamp = _now().timestamp()
    current_step = int(timestamp // step_seconds)
    for offset in range(-window_steps, window_steps + 1):
        step = current_step + offset
        candidate = totp.at(step * step_seconds)
        if secrets.compare_digest(candidate, code):
            return True, step
    return False, -1


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pad(value: str) -> str:
    return value + "=" * (-len(value) % 4)


__all__ = [
    "MfaChallengeInvalid",
    "MfaCodeRejected",
    "MfaConfig",
    "MfaError",
    "MfaFactorDisabled",
    "MfaFactorNotFound",
    "MfaService",
    "MfaWebAuthnRejected",
]

"""Tests for the MFA service + routes (A6)."""

from __future__ import annotations

import base64
import hashlib
import time

import pyotp
import pytest

from backend_app.contracts import (
    IdentityPolicyRecord,
    MfaChallengeKind,
    MfaFactorKind,
    OrganizationRecord,
    RoleRecord,
    UserRecord,
)
from backend_app.identity import (
    InMemoryIdentityStore,
    InMemoryMfaStore,
    InMemoryPasswordStore,
    InMemorySessionStore,
    LockoutService,
    InMemoryLockoutStore,
    MfaChallengeInvalid,
    MfaCodeRejected,
    MfaConfig,
    MfaService,
    PasswordHasherConfig,
    PasswordService,
    SessionService,
)
from backend_app.token_vault import TokenVault


_TEST_AUTH_SECRET = "test-auth-secret-mfa"


class _FakeTokenVault(TokenVault):
    """In-memory vault for MFA tests. Real KMS is wired via TokenVaultFactory.

    Stores ``base64:plaintext`` so encrypt/decrypt round-trip while still
    being distinguishable from raw plaintext in assertions.
    """

    def encrypt(self, plaintext: str) -> str:
        return "v1:" + base64.urlsafe_b64encode(plaintext.encode("utf-8")).decode(
            "ascii"
        )

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext.startswith("v1:"):
            raise ValueError("unexpected ciphertext")
        return base64.urlsafe_b64decode(ciphertext.removeprefix("v1:")).decode("utf-8")


class _Fixture:
    def __init__(self, *, mfa_required: bool = False) -> None:
        self.identity_store = InMemoryIdentityStore()
        self.mfa_store = InMemoryMfaStore()
        self.password_store = InMemoryPasswordStore()
        self.lockout_store = InMemoryLockoutStore()
        self.sessions = SessionService(
            store=InMemorySessionStore(),
            auth_secret=_TEST_AUTH_SECRET,
            dev_mint_allowed=True,
        )
        self.lockout = LockoutService(
            identity_store=self.identity_store,
            lockout_store=self.lockout_store,
        )
        self.mfa = MfaService(
            identity_store=self.identity_store,
            mfa_store=self.mfa_store,
            token_vault=_FakeTokenVault(),
            config=MfaConfig(
                totp_step_seconds=30,
                totp_window_steps=1,
                challenge_ttl_seconds=300,
                recovery_code_count=3,
                recovery_code_byte_length=8,
            ),
        )
        self.identity_store.create_role(
            RoleRecord(
                name="employee",
                display_name="E",
                is_system=True,
                permission_scopes=("runtime:use",),
            )
        )
        self.org = self.identity_store.create_organization(
            OrganizationRecord(display_name="Acme", slug="acme")
        )
        self.user = self.identity_store.create_user(
            UserRecord(
                org_id=self.org.org_id,
                primary_email="alice@acme.com",
                display_name="Alice",
            )
        )
        if mfa_required:
            self.identity_store.upsert_identity_policy(
                IdentityPolicyRecord(org_id=self.org.org_id, mfa_required=True)
            )
        self.password_service = PasswordService(
            identity_store=self.identity_store,
            password_store=self.password_store,
            sessions=self.sessions,
            hasher_config=PasswordHasherConfig(
                memory_cost=512, time_cost=1, parallelism=1
            ),
            lockout=self.lockout,
            mfa=self.mfa,
        )

    def enroll_totp(self) -> tuple[str, str, tuple[str, ...]]:
        result = self.mfa.enroll_totp(
            org_id=self.org.org_id,
            user_id=self.user.user_id,
            display_name="Authenticator",
        )
        return result.factor_id, result.secret_b32, result.recovery_codes

    def confirm_totp(self, factor_id: str, code: str) -> None:
        self.mfa.confirm_totp(
            org_id=self.org.org_id,
            user_id=self.user.user_id,
            factor_id=factor_id,
            code=code,
        )


# ---------------------------------------------------------------------------
# TOTP enroll + confirm
# ---------------------------------------------------------------------------


class TestTotpEnrollConfirm:
    def test_enroll_returns_otpauth_url_and_recovery_codes(self) -> None:
        f = _Fixture()
        factor_id, secret, recovery = f.enroll_totp()
        assert secret  # base32 string
        assert len(recovery) == 3
        # Factor exists, but is disabled until confirm.
        factor = f.mfa_store.get_factor(factor_id=factor_id)
        assert factor is not None
        assert factor.enabled is False
        assert factor.kind == MfaFactorKind.TOTP

    def test_confirm_with_current_code_enables_factor(self) -> None:
        f = _Fixture()
        factor_id, secret, _ = f.enroll_totp()
        code = pyotp.TOTP(secret).now()
        f.confirm_totp(factor_id, code)
        factor = f.mfa_store.get_factor(factor_id=factor_id)
        assert factor is not None
        assert factor.enabled is True

    def test_confirm_with_wrong_code_rejected(self) -> None:
        f = _Fixture()
        factor_id, _, _ = f.enroll_totp()
        with pytest.raises(MfaCodeRejected):
            f.confirm_totp(factor_id, "000000")

    def test_replay_of_confirm_code_rejected(self) -> None:
        f = _Fixture()
        factor_id, secret, _ = f.enroll_totp()
        code = pyotp.TOTP(secret).now()
        f.confirm_totp(factor_id, code)
        # Re-using the same code (same time-bucket) must be rejected by
        # the last_step replay guard.
        with pytest.raises(MfaCodeRejected):
            f.mfa.verify_totp_challenge(
                org_id=f.org.org_id,
                user_id=f.user.user_id,
                challenge_id=f._issue_totp_challenge(factor_id),
                code=code,
            )

    def test_secret_is_stored_encrypted(self) -> None:
        f = _Fixture()
        factor_id, secret, _ = f.enroll_totp()
        stored = f.mfa_store.get_totp_secret_for_factor(factor_id=factor_id)
        assert stored is not None
        assert stored.encrypted_secret.startswith("v1:")
        assert secret not in stored.encrypted_secret


def _issue_totp_challenge_helper(
    fixture: _Fixture, factor_id: str | None = None
) -> str:
    challenge, _ = fixture.mfa.issue_challenge(
        org_id=fixture.org.org_id,
        user_id=fixture.user.user_id,
        kind=MfaChallengeKind.TOTP,
        factor_id=factor_id,
    )
    return challenge.challenge_id


# Bind helper to the fixture for the replay test above.
_Fixture._issue_totp_challenge = lambda self, factor_id=None: (
    _issue_totp_challenge_helper(  # type: ignore[attr-defined]
        self, factor_id
    )
)


# ---------------------------------------------------------------------------
# TOTP verify (post-login challenge)
# ---------------------------------------------------------------------------


class TestTotpVerify:
    def test_verify_with_current_code_succeeds(self) -> None:
        f = _Fixture()
        factor_id, secret, _ = f.enroll_totp()
        f.confirm_totp(factor_id, pyotp.TOTP(secret).now())
        # Force the next code into a new step bucket.
        time.sleep(31)
        challenge_id = f._issue_totp_challenge(factor_id)
        new_code = pyotp.TOTP(secret).now()
        factor = f.mfa.verify_totp_challenge(
            org_id=f.org.org_id,
            user_id=f.user.user_id,
            challenge_id=challenge_id,
            code=new_code,
        )
        assert factor.factor_id == factor_id

    def test_verify_with_wrong_code_rejected(self) -> None:
        f = _Fixture()
        factor_id, secret, _ = f.enroll_totp()
        f.confirm_totp(factor_id, pyotp.TOTP(secret).now())
        challenge_id = f._issue_totp_challenge(factor_id)
        with pytest.raises(MfaCodeRejected):
            f.mfa.verify_totp_challenge(
                org_id=f.org.org_id,
                user_id=f.user.user_id,
                challenge_id=challenge_id,
                code="000000",
            )

    def test_verify_with_consumed_challenge_rejected(self) -> None:
        f = _Fixture()
        factor_id, secret, _ = f.enroll_totp()
        f.confirm_totp(factor_id, pyotp.TOTP(secret).now())
        challenge_id = f._issue_totp_challenge(factor_id)
        # Consume the challenge once via a wrong code (it still flips
        # consumed_at because consume happens before the code check).
        with pytest.raises(MfaCodeRejected):
            f.mfa.verify_totp_challenge(
                org_id=f.org.org_id,
                user_id=f.user.user_id,
                challenge_id=challenge_id,
                code="000000",
            )
        # A second attempt with even the right code 400s — the challenge
        # was already consumed.
        with pytest.raises(MfaChallengeInvalid):
            f.mfa.verify_totp_challenge(
                org_id=f.org.org_id,
                user_id=f.user.user_id,
                challenge_id=challenge_id,
                code=pyotp.TOTP(secret).now(),
            )


# ---------------------------------------------------------------------------
# Recovery codes
# ---------------------------------------------------------------------------


class TestRecoveryCodes:
    def test_recovery_code_single_use(self) -> None:
        f = _Fixture()
        _, _, recovery = f.enroll_totp()
        first_code = recovery[0]
        # First consume succeeds.
        record = f.mfa.consume_recovery_code(
            org_id=f.org.org_id,
            user_id=f.user.user_id,
            code=first_code,
        )
        assert record.consumed_at is not None
        # Second consume of the same code rejected.
        with pytest.raises(MfaCodeRejected):
            f.mfa.consume_recovery_code(
                org_id=f.org.org_id,
                user_id=f.user.user_id,
                code=first_code,
            )

    def test_recovery_code_hashed_at_rest(self) -> None:
        f = _Fixture()
        _, _, recovery = f.enroll_totp()
        sample = recovery[0]
        active = f.mfa_store.list_active_recovery_codes(
            org_id=f.org.org_id, user_id=f.user.user_id
        )
        # Plaintext code never stored — only sha256 hashes.
        assert all(
            r.code_hash == hashlib.sha256(c.encode()).hexdigest()
            for r, c in zip(active, recovery, strict=False)
        )
        assert sample not in {r.code_hash for r in active}


# ---------------------------------------------------------------------------
# Session gating: mfa_required flips login to mfa:pending
# ---------------------------------------------------------------------------


class TestSessionMfaGating:
    def test_login_with_mfa_required_returns_requires_mfa_true(self) -> None:
        f = _Fixture(mfa_required=True)
        f.password_service.set_password(
            org_id=f.org.org_id,
            user_id=f.user.user_id,
            new_password="CorrectPass2024!",
        )
        factor_id, secret, _ = f.enroll_totp()
        f.confirm_totp(factor_id, pyotp.TOTP(secret).now())
        result = f.password_service.login(
            org_id=f.org.org_id,
            email="alice@acme.com",
            password="CorrectPass2024!",
        )
        assert result.requires_mfa is True
        # Session was minted with the placeholder scope.
        from backend_app.identity._pkce import compute_challenge  # avoid noqa

        del compute_challenge  # silence
        # Inspect the session row directly.
        active = f.sessions.list_active(org_id=f.org.org_id, user_id=f.user.user_id)
        assert active[0].permission_scopes == ("mfa:pending",)
        assert active[0].mfa_satisfied_at is None

    def test_login_without_mfa_required_works_normally(self) -> None:
        f = _Fixture(mfa_required=False)
        f.password_service.set_password(
            org_id=f.org.org_id,
            user_id=f.user.user_id,
            new_password="CorrectPass2024!",
        )
        result = f.password_service.login(
            org_id=f.org.org_id,
            email="alice@acme.com",
            password="CorrectPass2024!",
        )
        assert result.requires_mfa is False

    def test_login_with_mfa_required_but_no_factor_does_not_gate(self) -> None:
        # Prevents lockout if an org enables mfa_required but a user
        # hasn't enrolled yet — that org has to handle the gap via admin
        # process, not a hard 401 the user can't escape.
        f = _Fixture(mfa_required=True)
        f.password_service.set_password(
            org_id=f.org.org_id,
            user_id=f.user.user_id,
            new_password="CorrectPass2024!",
        )
        result = f.password_service.login(
            org_id=f.org.org_id,
            email="alice@acme.com",
            password="CorrectPass2024!",
        )
        assert result.requires_mfa is False

    def test_mark_mfa_satisfied_clears_pending_session(self) -> None:
        f = _Fixture(mfa_required=True)
        f.password_service.set_password(
            org_id=f.org.org_id,
            user_id=f.user.user_id,
            new_password="CorrectPass2024!",
        )
        factor_id, secret, _ = f.enroll_totp()
        f.confirm_totp(factor_id, pyotp.TOTP(secret).now())
        result = f.password_service.login(
            org_id=f.org.org_id,
            email="alice@acme.com",
            password="CorrectPass2024!",
        )
        assert result.requires_mfa is True
        ok = f.sessions.mark_mfa_satisfied(
            session_id=result.session_id,
            promoted_scopes=("runtime:use",),
        )
        assert ok is True
        active = f.sessions.list_active(org_id=f.org.org_id, user_id=f.user.user_id)
        assert active[0].mfa_satisfied_at is not None
        assert active[0].permission_scopes == ("runtime:use",)

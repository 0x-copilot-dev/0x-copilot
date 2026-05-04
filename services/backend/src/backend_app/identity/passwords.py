"""Local password authentication (A4): hash, verify, policy, login, reset.

Argon2id parameters are tunable via env vars; the OWASP defaults are used
unless an operator overrides them. Pepper is optional but recommended in
hardened deploys (``PASSWORD_PEPPER`` env var is prepended before hashing).

The service is the single entry point — routes call its high-level methods.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from argon2 import PasswordHasher, exceptions as argon2_exceptions

from backend_app.contracts import (
    IdentityAuditEventRecord,
    LocalCredentialRecord,
    LocalLoginResult,
    LoginAttemptKind,
    LoginAttemptOutcome,
    LoginAttemptRecord,
    OrganizationMemberRecord,
    OrganizationMemberSource,
    PasswordPolicyRecord,
    PasswordResetTokenRecord,
)
from backend_app.identity.lockout import LockoutService
from backend_app.identity.password_store import PasswordStore
from backend_app.identity.sessions import SessionService
from backend_app.identity.store import IdentityStore


_LOGGER = logging.getLogger(__name__)

# OWASP-aligned argon2id defaults; tunable via env. See RFC 9106 §4.
_DEFAULT_ARGON_MEMORY_KIB = 65536
_DEFAULT_ARGON_TIME_COST = 3
_DEFAULT_ARGON_PARALLELISM = 2

_DEFAULT_RESET_TOKEN_TTL_SECONDS = 60 * 60  # 1 hour
_RESET_TOKEN_BYTES = 32

# Stable dummy hash used to keep the unknown-email login path constant-time.
# Computed once at import using the configured argon2 params; matches no
# real password.
_DUMMY_HASH_PLAINTEXT = "constant-time-anti-enumeration-do-not-use"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class WeakPasswordError(ValueError):
    """Raised when a candidate password fails policy."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__(", ".join(reasons))
        self.reasons = reasons


class LoginRejectedError(RuntimeError):
    """Raised when login fails (wrong password, disabled IdP, locked, etc.).

    Always carries a generic message to avoid leaking which step failed.
    """

    def __init__(self, message: str = "invalid credentials") -> None:
        super().__init__(message)


class PasswordChangeRejected(RuntimeError):
    """Raised when current password verification fails for a change request."""


class ResetTokenRejected(RuntimeError):
    """Raised when a reset token is unknown / expired / consumed."""


class BootstrapRefused(RuntimeError):
    """Raised when bootstrap-admin is invoked but state forbids it."""


class LocalAuthDisabled(RuntimeError):
    """Raised when ``identity_policy.local_password_enabled`` is false for the org."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PasswordHasherConfig:
    memory_cost: int = _DEFAULT_ARGON_MEMORY_KIB
    time_cost: int = _DEFAULT_ARGON_TIME_COST
    parallelism: int = _DEFAULT_ARGON_PARALLELISM

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "PasswordHasherConfig":
        env = env if env is not None else dict(os.environ)
        return cls(
            memory_cost=_read_int(
                env, "PASSWORD_ARGON2_MEMORY_KIB", _DEFAULT_ARGON_MEMORY_KIB
            ),
            time_cost=_read_int(
                env, "PASSWORD_ARGON2_TIME_COST", _DEFAULT_ARGON_TIME_COST
            ),
            parallelism=_read_int(
                env, "PASSWORD_ARGON2_PARALLELISM", _DEFAULT_ARGON_PARALLELISM
            ),
        )


def _read_int(env: dict[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


class PasswordService:
    """Hash / verify / policy-enforce / login / reset for local credentials."""

    def __init__(
        self,
        *,
        identity_store: IdentityStore,
        password_store: PasswordStore,
        sessions: SessionService,
        hasher_config: PasswordHasherConfig | None = None,
        pepper: str | None = None,
        lockout: LockoutService | None = None,
    ) -> None:
        self._identity_store = identity_store
        self._password_store = password_store
        self._sessions = sessions
        # Optional so existing tests / dev wiring without lockout still
        # work; production composes this from app.state during create_app.
        self._lockout = lockout
        self._config = hasher_config or PasswordHasherConfig.from_env()
        self._pepper = (
            pepper if pepper is not None else os.environ.get("PASSWORD_PEPPER", "")
        )
        self._hasher = PasswordHasher(
            memory_cost=self._config.memory_cost,
            time_cost=self._config.time_cost,
            parallelism=self._config.parallelism,
        )
        # Cache the dummy hash so the unknown-email path costs the same as
        # the verify path. Recomputed on first access if the params change.
        self._dummy_hash = self._hasher.hash(self._with_pepper(_DUMMY_HASH_PLAINTEXT))

    # Hash / verify -----------------------------------------------------
    def hash(self, password: str) -> str:
        return self._hasher.hash(self._with_pepper(password))

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, self._with_pepper(password))
        except argon2_exceptions.VerifyMismatchError:
            return False
        except argon2_exceptions.InvalidHashError:
            return False
        except argon2_exceptions.VerificationError:
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        return self._hasher.check_needs_rehash(password_hash)

    # Policy ------------------------------------------------------------
    def policy_for(self, *, org_id: str) -> PasswordPolicyRecord:
        existing = self._password_store.get_policy(org_id=org_id)
        if existing is not None:
            return existing
        return PasswordPolicyRecord(org_id=org_id)

    def enforce_policy(self, *, org_id: str, password: str) -> None:
        policy = self.policy_for(org_id=org_id)
        reasons: list[str] = []
        if len(password) < policy.min_length:
            reasons.append(f"must be at least {policy.min_length} characters")
        if policy.require_upper and not any(c.isupper() for c in password):
            reasons.append("must contain an uppercase letter")
        if policy.require_lower and not any(c.islower() for c in password):
            reasons.append("must contain a lowercase letter")
        if policy.require_digit and not any(c.isdigit() for c in password):
            reasons.append("must contain a digit")
        if policy.require_symbol and password.isalnum():
            reasons.append("must contain a symbol")
        if reasons:
            raise WeakPasswordError(reasons)

    def enforce_reuse_window(
        self, *, credential: LocalCredentialRecord, new_password: str
    ) -> None:
        window = self.policy_for(org_id=credential.org_id).reuse_window
        if window <= 0:
            return
        candidates = (credential.password_hash, *credential.previous_hashes)[:window]
        for prior in candidates:
            if self.verify(prior, new_password):
                raise WeakPasswordError(
                    [f"must not match any of the last {window} passwords"]
                )

    # Set / change ------------------------------------------------------
    def set_password(
        self,
        *,
        org_id: str,
        user_id: str,
        new_password: str,
    ) -> LocalCredentialRecord:
        self.enforce_policy(org_id=org_id, password=new_password)
        existing = self._password_store.get_credential(org_id=org_id, user_id=user_id)
        if existing is not None:
            self.enforce_reuse_window(credential=existing, new_password=new_password)
            previous = (existing.password_hash, *existing.previous_hashes)
            window = self.policy_for(org_id=org_id).reuse_window
            previous = previous[: max(window, 1)]
        else:
            previous = ()
        record = LocalCredentialRecord(
            org_id=org_id,
            user_id=user_id,
            password_hash=self.hash(new_password),
            previous_hashes=previous,
        )
        self._password_store.upsert_credential(record)
        self._identity_store.append_identity_audit(
            IdentityAuditEventRecord(
                org_id=org_id,
                actor_user_id=user_id,
                subject_user_id=user_id,
                action="password.set",
                metadata={},
            )
        )
        return record

    def change_password(
        self,
        *,
        org_id: str,
        user_id: str,
        current_password: str,
        new_password: str,
    ) -> LocalCredentialRecord:
        existing = self._password_store.get_credential(org_id=org_id, user_id=user_id)
        if existing is None or not self.verify(
            existing.password_hash, current_password
        ):
            raise PasswordChangeRejected("current password did not match")
        record = self.set_password(
            org_id=org_id, user_id=user_id, new_password=new_password
        )
        self._identity_store.append_identity_audit(
            IdentityAuditEventRecord(
                org_id=org_id,
                actor_user_id=user_id,
                subject_user_id=user_id,
                action="password.changed",
                metadata={},
            )
        )
        return record

    # Identity policy ---------------------------------------------------
    def _identity_policy_allows_local(self, *, org_id: str) -> bool:
        """Default open: when no policy row exists the local path stays on.

        Single-tenant deploys typically never write the row (default OK);
        bank/gov SaaS orgs explicitly UPSERT ``local_password_enabled=False``
        to lock the route down to SAML/OIDC.
        """

        policy = self._identity_store.get_identity_policy(org_id=org_id)
        if policy is None:
            return True
        return policy.local_password_enabled

    # Login -------------------------------------------------------------
    def login(
        self,
        *,
        org_id: str,
        email: str,
        password: str,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> LocalLoginResult:
        if not self._identity_policy_allows_local(org_id=org_id):
            # Don't even hash. Audit the rejection for compliance — bank
            # auditors will want a row per "local login attempted on a
            # locked-down org" so they can prove the toggle is enforced.
            self._identity_store.append_login_attempt(
                LoginAttemptRecord(
                    org_id=org_id,
                    email_attempted=email,
                    auth_kind=LoginAttemptKind.LOCAL,
                    outcome=LoginAttemptOutcome.PROVIDER_REJECTED,
                    ip=ip,
                    user_agent=user_agent,
                    failure_reason="local_password_disabled",
                )
            )
            raise LocalAuthDisabled(
                "local password authentication is disabled for this organization"
            )
        user = self._identity_store.get_user_by_email(org_id=org_id, email=email)
        if self._lockout is not None and user is not None:
            # Lockout pre-check happens BEFORE the constant-time hash work
            # so a locked user can't side-step by triggering the slow path.
            # Records an audit row + 423 if active.
            self._lockout.check_or_raise(org_id=org_id, user_id=user.user_id)
        if user is None:
            # Constant-time path: still hash + verify a dummy.
            self.verify(self._dummy_hash, password)
            self._identity_store.append_login_attempt(
                LoginAttemptRecord(
                    org_id=org_id,
                    email_attempted=email,
                    auth_kind=LoginAttemptKind.LOCAL,
                    outcome=LoginAttemptOutcome.UNKNOWN_USER,
                    ip=ip,
                    user_agent=user_agent,
                )
            )
            raise LoginRejectedError()

        credential = self._password_store.get_credential(
            org_id=org_id, user_id=user.user_id
        )
        # Same constant-time treatment if the user has no credential row.
        password_hash = (
            credential.password_hash if credential is not None else self._dummy_hash
        )
        ok = self.verify(password_hash, password)
        if credential is None or not ok:
            self._identity_store.append_login_attempt(
                LoginAttemptRecord(
                    org_id=org_id,
                    email_attempted=email,
                    user_id=user.user_id,
                    auth_kind=LoginAttemptKind.LOCAL,
                    outcome=LoginAttemptOutcome.BAD_PASSWORD,
                    ip=ip,
                    user_agent=user_agent,
                )
            )
            if self._lockout is not None:
                # Sliding-window count includes the row we just appended;
                # if the threshold is now crossed, this writes the active
                # lockout row + audit. The next attempt will hit
                # check_or_raise above.
                self._lockout.record_failure(
                    org_id=org_id, user_id=user.user_id, email=email
                )
            raise LoginRejectedError()

        self._password_store.update_credential_last_used(
            credential_id=credential.credential_id, when=_now()
        )
        requires_rotate = (
            credential.must_rotate_at is not None
            and credential.must_rotate_at <= _now()
        )
        if self.needs_rehash(credential.password_hash):
            # Re-hash silently with the current params; doesn't change the
            # caller's experience but upgrades the row.
            try:
                self.set_password(
                    org_id=org_id, user_id=user.user_id, new_password=password
                )
            except WeakPasswordError:
                # Policy may have tightened — leave the old hash, force a
                # rotation. The user can still log in and will be prompted.
                requires_rotate = True

        roles = ("employee",)
        permission_scopes: tuple[str, ...] = ()
        # Pull the user's actual roles + scopes if any.
        assignments = self._identity_store.list_role_assignments(
            org_id=org_id, user_id=user.user_id
        )
        if assignments:
            role_names: list[str] = []
            scopes: set[str] = set()
            for assignment in assignments:
                role = self._identity_store.get_role(role_id=assignment.role_id)
                if role is None:
                    continue
                role_names.append(role.name)
                scopes.update(role.permission_scopes)
            if role_names:
                roles = tuple(role_names)
                permission_scopes = tuple(sorted(scopes))
        if not permission_scopes:
            employee = self._identity_store.get_role_by_name(
                org_id=None, name="employee"
            )
            if employee is not None:
                permission_scopes = tuple(sorted(employee.permission_scopes))

        session = self._sessions.create(
            org_id=org_id,
            user_id=user.user_id,
            roles=roles,
            permission_scopes=permission_scopes,
            device_label="local-password",
            client_ip=ip,
            user_agent=user_agent,
        )
        self._identity_store.append_login_attempt(
            LoginAttemptRecord(
                org_id=org_id,
                email_attempted=email,
                user_id=user.user_id,
                auth_kind=LoginAttemptKind.LOCAL,
                outcome=LoginAttemptOutcome.SUCCESS,
                ip=ip,
                user_agent=user_agent,
            )
        )
        if self._lockout is not None:
            # Successful verify clears any active auto-unlock window so a
            # later failure starts the count fresh; permanent lockouts
            # require admin unlock and are NOT cleared here.
            self._lockout.record_success(org_id=org_id, user_id=user.user_id)
        return LocalLoginResult(
            user_id=user.user_id,
            session_id=session.session_id,
            bearer_token=session.bearer_token,
            expires_at=session.expires_at,
            requires_password_change=requires_rotate,
        )

    # Reset -------------------------------------------------------------
    def request_reset(
        self,
        *,
        org_id: str,
        email: str,
        ip: str | None = None,
    ) -> tuple[bool, str | None]:
        """Request a reset. Returns ``(accepted, plaintext_token_or_none)``.

        Always returns ``accepted=True`` so the caller cannot enumerate
        valid emails. ``plaintext_token`` is non-None only when the user
        actually exists; production routes hide it from the HTTP response
        (only the notify event carries it).
        """

        user = self._identity_store.get_user_by_email(org_id=org_id, email=email)
        self._identity_store.append_identity_audit(
            IdentityAuditEventRecord(
                org_id=org_id,
                actor_user_id=user.user_id if user else None,
                subject_user_id=user.user_id if user else None,
                action="password.reset_requested",
                metadata={"email_known": user is not None},
                request_ip=ip,
            )
        )
        if user is None:
            return True, None
        plaintext = secrets.token_urlsafe(_RESET_TOKEN_BYTES)
        token_hash = hashlib.sha256(plaintext.encode("ascii")).hexdigest()
        record = PasswordResetTokenRecord(
            org_id=org_id,
            user_id=user.user_id,
            token_hash=token_hash,
            expires_at=_now() + timedelta(seconds=_DEFAULT_RESET_TOKEN_TTL_SECONDS),
            request_ip=ip,
        )
        self._password_store.create_reset_token(record)
        return True, plaintext

    def confirm_reset(self, *, token: str, new_password: str) -> LocalCredentialRecord:
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        consumed = self._password_store.consume_reset_token(token_hash=token_hash)
        if consumed is None:
            raise ResetTokenRejected("invalid or expired reset token")
        record = self.set_password(
            org_id=consumed.org_id,
            user_id=consumed.user_id,
            new_password=new_password,
        )
        self._identity_store.append_identity_audit(
            IdentityAuditEventRecord(
                org_id=consumed.org_id,
                actor_user_id=consumed.user_id,
                subject_user_id=consumed.user_id,
                action="password.reset_confirmed",
                metadata={},
            )
        )
        return record

    # Internals ---------------------------------------------------------
    def _with_pepper(self, password: str) -> str:
        if not self._pepper:
            return password
        return f"{self._pepper}|{password}"


# ---------------------------------------------------------------------------
# Bootstrap admin
# ---------------------------------------------------------------------------


_BOOTSTRAP_TOKEN_ENV = "BOOTSTRAP_ADMIN_TOKEN"
_BOOTSTRAP_EMAIL_ENV = "BOOTSTRAP_ADMIN_EMAIL"


class BootstrapAdminService:
    """One-time first-run admin creation.

    The setup token is matched against the operator's env value. The path
    is refused if any admin user already exists in the target org so a
    leaked token can't escalate privileges later.
    """

    def __init__(
        self,
        *,
        identity_store: IdentityStore,
        password_service: PasswordService,
    ) -> None:
        self._identity_store = identity_store
        self._password_service = password_service

    def bootstrap(
        self,
        *,
        org_id: str,
        email: str,
        display_name: str,
        setup_token: str,
        initial_password: str,
    ) -> str:
        expected_token = os.environ.get(_BOOTSTRAP_TOKEN_ENV, "").strip()
        if not expected_token:
            raise BootstrapRefused(
                f"{_BOOTSTRAP_TOKEN_ENV} is not configured; bootstrap is locked"
            )
        if not hmac.compare_digest(expected_token, setup_token.strip()):
            raise BootstrapRefused("invalid setup token")

        admin_role = self._identity_store.get_role_by_name(org_id=None, name="admin")
        if admin_role is None:
            raise BootstrapRefused(
                "admin system role missing; run identity migrations first"
            )

        existing_admins = [
            assignment
            for user in self._identity_store.list_users(org_id=org_id)
            for assignment in self._identity_store.list_role_assignments(
                org_id=org_id, user_id=user.user_id
            )
            if assignment.role_id == admin_role.role_id
        ]
        if existing_admins:
            raise BootstrapRefused(
                "an admin user already exists in this org; bootstrap refused"
            )

        from backend_app.contracts import UserRecord

        with self._identity_store.transaction():
            user = self._identity_store.create_user(
                UserRecord(
                    org_id=org_id,
                    primary_email=email,
                    display_name=display_name,
                )
            )
            self._identity_store.add_member(
                OrganizationMemberRecord(
                    org_id=org_id,
                    user_id=user.user_id,
                    source=OrganizationMemberSource.BOOTSTRAP,
                )
            )
            self._identity_store.assign_role(
                _role_assignment(
                    org_id=org_id, user_id=user.user_id, role_id=admin_role.role_id
                )
            )
            self._identity_store.append_identity_audit(
                IdentityAuditEventRecord(
                    org_id=org_id,
                    actor_user_id=user.user_id,
                    subject_user_id=user.user_id,
                    action="password.bootstrap_admin_created",
                    metadata={"email": email},
                )
            )
        # Force the admin to rotate the initial password on first login.
        cred = self._password_service.set_password(
            org_id=org_id, user_id=user.user_id, new_password=initial_password
        )

        # Mark must_rotate_at = now() so requires_password_change=True on
        # the next login.
        rotated = cred.model_copy(update={"must_rotate_at": _now()})
        self._password_service._password_store.upsert_credential(rotated)  # noqa: SLF001
        return user.user_id


def _role_assignment(*, org_id: str, user_id: str, role_id: str) -> Any:
    from backend_app.contracts import RoleAssignmentRecord

    return RoleAssignmentRecord(org_id=org_id, user_id=user_id, role_id=role_id)


__all__ = [
    "BootstrapAdminService",
    "BootstrapRefused",
    "LocalAuthDisabled",
    "LoginRejectedError",
    "PasswordChangeRejected",
    "PasswordHasherConfig",
    "PasswordService",
    "ResetTokenRejected",
    "WeakPasswordError",
]

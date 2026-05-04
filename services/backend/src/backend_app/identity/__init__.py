"""Identity & access (A1..A8) — schema, sessions, OIDC, local password, lockout."""

from backend_app.identity.jwks import (
    HttpxJwksFetcher,
    IdTokenVerificationError,
    IdTokenVerifier,
    JwksFetcherError,
    JwksProvider,
)
from backend_app.identity.lockout import (
    AccountLocked,
    LockoutService,
)
from backend_app.identity.lockout_store import (
    InMemoryLockoutStore,
    LockoutStore,
    PostgresLockoutStore,
)
from backend_app.identity.mfa import (
    MfaChallengeInvalid,
    MfaCodeRejected,
    MfaConfig,
    MfaError,
    MfaFactorDisabled,
    MfaFactorNotFound,
    MfaService,
    MfaWebAuthnRejected,
)
from backend_app.identity.mfa_store import (
    InMemoryMfaStore,
    MfaStore,
    PostgresMfaStore,
)
from backend_app.identity.oidc import (
    HttpxTokenEndpointClient,
    OidcConfigError,
    OidcProviderConfig,
    OidcProviderDisabled,
    OidcService,
    OidcStateMismatch,
    OidcTokenExchangeError,
    OidcUserNotProvisioned,
    TokenEndpointClient,
)
from backend_app.identity.oidc_store import (
    InMemoryOidcStore,
    OidcStore,
    PostgresOidcStore,
)
from backend_app.identity.password_store import (
    InMemoryPasswordStore,
    PasswordStore,
    PostgresPasswordStore,
)
from backend_app.identity.passwords import (
    BootstrapAdminService,
    BootstrapRefused,
    LocalAuthDisabled,
    LoginRejectedError,
    PasswordChangeRejected,
    PasswordHasherConfig,
    PasswordService,
    ResetTokenRejected,
    WeakPasswordError,
)

# LocalAuthDisabled is consumed by the password routes which import it via
# the identity package. Re-exported above; nothing else to add.
from backend_app.identity.session_store import (
    InMemorySessionStore,
    PostgresSessionStore,
    SessionStore,
)
from backend_app.identity.sessions import (
    DevMintNotAllowed,
    SessionAuthSecretMissing,
    SessionInvalidToken,
    SessionNotActive,
    SessionPolicy,
    SessionService,
    audit_session_event,
)
from backend_app.identity.store import (
    IdentityStore,
    InMemoryIdentityStore,
    PostgresIdentityStore,
)


__all__ = [
    "AccountLocked",
    "BootstrapAdminService",
    "BootstrapRefused",
    "DevMintNotAllowed",
    "HttpxJwksFetcher",
    "HttpxTokenEndpointClient",
    "IdTokenVerificationError",
    "IdTokenVerifier",
    "IdentityStore",
    "InMemoryIdentityStore",
    "InMemoryLockoutStore",
    "InMemoryMfaStore",
    "InMemoryOidcStore",
    "InMemoryPasswordStore",
    "InMemorySessionStore",
    "JwksFetcherError",
    "JwksProvider",
    "LocalAuthDisabled",
    "LockoutService",
    "LockoutStore",
    "LoginRejectedError",
    "MfaChallengeInvalid",
    "MfaCodeRejected",
    "MfaConfig",
    "MfaError",
    "MfaFactorDisabled",
    "MfaFactorNotFound",
    "MfaService",
    "MfaStore",
    "MfaWebAuthnRejected",
    "OidcConfigError",
    "OidcProviderConfig",
    "OidcProviderDisabled",
    "OidcService",
    "OidcStateMismatch",
    "OidcStore",
    "OidcTokenExchangeError",
    "OidcUserNotProvisioned",
    "PasswordChangeRejected",
    "PasswordHasherConfig",
    "PasswordService",
    "PasswordStore",
    "PostgresIdentityStore",
    "PostgresLockoutStore",
    "PostgresMfaStore",
    "PostgresOidcStore",
    "PostgresPasswordStore",
    "PostgresSessionStore",
    "ResetTokenRejected",
    "SessionAuthSecretMissing",
    "SessionInvalidToken",
    "SessionNotActive",
    "SessionPolicy",
    "SessionService",
    "SessionStore",
    "TokenEndpointClient",
    "WeakPasswordError",
    "audit_session_event",
]

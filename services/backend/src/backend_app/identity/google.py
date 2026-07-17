"""Global "Continue with Google" OIDC provider (env-configured).

Unlike per-org OIDC providers (A3), which live in ``auth_providers`` rows
that a workspace admin manages, the Google provider is deployment-global:
it is resolved from environment variables at boot and served under the
reserved provider id ``google``. Any org's login screen (and the pre-org
login screen, where no workspace is known yet) may start a Google sign-in.

Configuration
-------------
``GOOGLE_OAUTH_CLIENT_ID``      enables the provider when non-empty.
``GOOGLE_OAUTH_CLIENT_SECRET``  optional; when set the token exchange uses
                                ``client_secret_post``, otherwise PKCE-only
                                (``none``) — Google "Web application" OAuth
                                clients issue a secret, so production sets
                                both.

Secret handling: the env secret is encrypted with the process TokenVault at
build time and carried on the synthesized ``AuthProviderRecord`` in the
``encrypted_client_secret`` field — exactly where per-org providers keep
theirs — so ``OidcService._exchange_code`` decrypts it through the same
path with zero new machinery. The plaintext is never logged or persisted.

Endpoints are Google's documented OIDC constants (the values served by
``https://accounts.google.com/.well-known/openid-configuration``); pinning
them avoids a boot-time network fetch.

Persistence: resolution never reads the database — env wins. A single
``auth_providers`` anchor row (org ``org_global_google``) is upserted at
boot purely so the Postgres foreign keys on ``oidc_authentications``,
``oidc_identities``, ``oidc_refresh_tokens`` and ``oidc_jwks_cache`` hold.
No schema change: ``auth_providers.org_id`` carries no FK, so the sentinel
org id needs no ``organizations`` row.
"""

from __future__ import annotations

from collections.abc import Mapping

from backend_app.contracts import AuthProviderKind, AuthProviderRecord
from backend_app.identity.store import IdentityStore
from backend_app.token_vault import TokenVault


ENV_GOOGLE_CLIENT_ID = "GOOGLE_OAUTH_CLIENT_ID"
ENV_GOOGLE_CLIENT_SECRET = "GOOGLE_OAUTH_CLIENT_SECRET"

# Reserved provider id — the frontend keys "Continue with Google" off this
# exact value, and a per-org auth_providers row may never claim it (the
# global resolver shadows org-scoped lookups for this id).
GOOGLE_PROVIDER_ID = "google"

# Sentinel org id for the FK anchor row and for pre-identity state
# (oidc_authentications.org_id, authorize-time audit events). Real users
# never live in this org: the callback resolves them into their linked org
# or a freshly provisioned personal org.
GOOGLE_GLOBAL_ORG_ID = "org_global_google"

GOOGLE_DISPLAY_NAME = "Google"
GOOGLE_ISSUER = "https://accounts.google.com"
GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_SCOPES = ("openid", "email", "profile")


class GlobalProviderConflict(RuntimeError):
    """A persisted auth_providers row claims a reserved global provider id."""


def build_google_provider(
    *,
    environ: Mapping[str, str],
    token_vault: TokenVault,
) -> AuthProviderRecord | None:
    """Synthesize the global Google ``AuthProviderRecord`` from env.

    Returns ``None`` when ``GOOGLE_OAUTH_CLIENT_ID`` is unset/empty — the
    provider is then simply absent (not listed, not resolvable).
    """

    client_id = (environ.get(ENV_GOOGLE_CLIENT_ID) or "").strip()
    if not client_id:
        return None
    client_secret = (environ.get(ENV_GOOGLE_CLIENT_SECRET) or "").strip()
    config: dict[str, object] = {
        "issuer": GOOGLE_ISSUER,
        "client_id": client_id,
        "authorization_endpoint": GOOGLE_AUTHORIZATION_ENDPOINT,
        "token_endpoint": GOOGLE_TOKEN_ENDPOINT,
        "jwks_url": GOOGLE_JWKS_URL,
        "scopes": list(GOOGLE_SCOPES),
        "audience": client_id,
        # Self-signup for the global provider is gated by the deployment
        # profile's allow_self_signup toggle, not by this per-org flag.
        "auto_provision_user": False,
        "token_endpoint_auth_method": (
            "client_secret_post" if client_secret else "none"
        ),
    }
    return AuthProviderRecord(
        provider_id=GOOGLE_PROVIDER_ID,
        org_id=GOOGLE_GLOBAL_ORG_ID,
        kind=AuthProviderKind.OIDC,
        display_name=GOOGLE_DISPLAY_NAME,
        enabled=True,
        config=config,
        encrypted_client_secret=(
            token_vault.encrypt(client_secret) if client_secret else None
        ),
    )


def ensure_global_auth_provider(
    *,
    identity_store: IdentityStore,
    record: AuthProviderRecord,
) -> None:
    """Upsert the FK anchor row for a global provider (idempotent).

    Concurrent boots race benignly: on a create conflict we re-read and fall
    through to the update path. A pre-existing row under the same id that is
    NOT the global sentinel means an org-created provider collides with the
    reserved id — fail loudly instead of silently shadowing it.
    """

    existing = identity_store.get_auth_provider_by_id(record.provider_id)
    if existing is None:
        try:
            identity_store.create_auth_provider(record)
        except Exception:
            existing = identity_store.get_auth_provider_by_id(record.provider_id)
            if existing is None:
                raise
        else:
            return
    if existing.org_id != record.org_id or existing.kind != record.kind:
        raise GlobalProviderConflict(
            f"auth_providers row {record.provider_id!r} exists with "
            f"org_id={existing.org_id!r} kind={existing.kind.value!r}; the id "
            f"is reserved for the global {record.display_name} provider"
        )
    identity_store.update_auth_provider(
        existing.model_copy(
            update={
                "display_name": record.display_name,
                "enabled": True,
                "config": record.config,
                "encrypted_client_secret": record.encrypted_client_secret,
            }
        )
    )


__all__ = [
    "ENV_GOOGLE_CLIENT_ID",
    "ENV_GOOGLE_CLIENT_SECRET",
    "GOOGLE_AUTHORIZATION_ENDPOINT",
    "GOOGLE_DISPLAY_NAME",
    "GOOGLE_GLOBAL_ORG_ID",
    "GOOGLE_ISSUER",
    "GOOGLE_JWKS_URL",
    "GOOGLE_PROVIDER_ID",
    "GOOGLE_SCOPES",
    "GOOGLE_TOKEN_ENDPOINT",
    "GlobalProviderConflict",
    "build_google_provider",
    "ensure_global_auth_provider",
]

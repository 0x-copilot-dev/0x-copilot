"""Desktop (``single_user_desktop``) composition root for the backend service.

The desktop app bundles Postgres locally and boots this module with::

    uvicorn backend_app.desktop_app:app

Everything is env-driven — no code changes between installs:

* ``DATABASE_URL``               -> shared :class:`PostgresConnectionPool`
* ``ENTERPRISE_AUTH_SECRET``     -> session bearer HMAC (same path as prod)
* ``ENTERPRISE_SERVICE_TOKEN``   -> service-to-service lane (facade/ai-backend)
* ``MCP_TOKEN_VAULT_SECRET``     -> local Fernet vault (profile allows local:
                                    ``require_kms_token_vault=False``)

Composition rules for this profile:

* Every store that HAS a Postgres adapter is wired to it here, explicitly,
  so nothing silently falls back to in-memory. Stores that only have an
  in-memory adapter today are called out with the accepted-gap comment.
* ``magic_link_globally_enabled=False`` — the desktop has no outbound
  email path, and ``create_app`` refuses to boot in production with
  magic-link ON + the logging email dispatcher. Login is Google/wallet.
* The deployment profile MUST be ``single_user_desktop``; any other value
  in ``ENTERPRISE_DEPLOYMENT_PROFILE`` is a packaging error and fails fast.

Run ``scripts/migrate.py apply`` (with ``BACKEND_DATABASE_URL``) before the
first boot — this module never applies migrations itself.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping

from copilot_service_contracts.deployment_profile import (
    ENV_DEPLOYMENT_PROFILE,
    PROFILE_SINGLE_USER_DESKTOP,
)
from fastapi import FastAPI

from backend_app.adapter_registry.store import PostgresAdapterRegistryStore
from backend_app.api_keys.store import PostgresApiKeyStore
from backend_app.app import create_app
from backend_app.deployment_profile import (
    DeploymentProfile,
    DeploymentProfileLoader,
)
from backend_app.identity import SessionService
from backend_app.identity.account_merge import PostgresMergeData
from backend_app.identity.account_merge_store import PostgresAccountMergeStore
from backend_app.identity.avatar_store import PostgresAvatarStore
from backend_app.identity.invitation_store import PostgresInvitationStore
from backend_app.identity.lockout_store import PostgresLockoutStore
from backend_app.identity.login_email_first_store import (
    PostgresAuthProviderDomainStore,
    PostgresMagicLinkTokenStore,
)
from backend_app.identity.me_store import PostgresMeStore
from backend_app.identity.mfa_store import PostgresMfaStore
from backend_app.identity.oidc_store import PostgresOidcStore
from backend_app.identity.password_store import PostgresPasswordStore
from backend_app.identity.saml_store import PostgresSamlStore
from backend_app.identity.scim_store import PostgresScimStore
from backend_app.identity.session_store import PostgresSessionStore
from backend_app.identity.store import PostgresIdentityStore
from backend_app.notifications.store import PostgresNotificationPrefsStore
from backend_app.policies.store import PostgresToolUsePolicyStore
from backend_app.privacy.store import PostgresPrivacySettingsStore
from backend_app.provider_keys import PostgresProviderApiKeyStore
from backend_app.service import McpRegistryService, SkillRegistryService
from backend_app.settings.store import PostgresSettingsStore
from backend_app.store import (
    PostgresConnectionPool,
    PostgresMcpStore,
    PostgresSkillStore,
)
from backend_app.token_vault import TokenVaultFactory


class DesktopEnvironmentError(RuntimeError):
    """Raised when the desktop composition root is misconfigured.

    The message always names the exact env vars that are missing or wrong
    so the desktop launcher (or an operator reading the crash log) can fix
    the install without reading this module.
    """


class DesktopComposer:
    """Builds the ``single_user_desktop`` backend app from process env."""

    REQUIRED_ENV: tuple[str, ...] = (
        "DATABASE_URL",
        "ENTERPRISE_AUTH_SECRET",
        "ENTERPRISE_SERVICE_TOKEN",
        "MCP_TOKEN_VAULT_SECRET",
        # Tamper-evident audit chain signing key (hex-encoded, >= 32 bytes).
        # copilot_audit_chain fails closed without it under
        # BACKEND_ENVIRONMENT=production, so surface it in the same
        # missing-env error instead of letting the signer crash later.
        "AUDIT_HMAC_KEY",
    )

    _MIN_PEPPER_BYTES = 16

    @classmethod
    def validate_env(cls, env: Mapping[str, str]) -> None:
        """Fail fast with one error naming every missing required var."""

        missing = [name for name in cls.REQUIRED_ENV if not env.get(name, "").strip()]
        if missing:
            raise DesktopEnvironmentError(
                "single_user_desktop backend cannot boot; missing required "
                f"environment variables: {', '.join(missing)}. "
                "DATABASE_URL must point at the bundled Postgres "
                "(postgresql://...); the four secrets are generated once "
                "per install by the desktop launcher (AUDIT_HMAC_KEY must "
                "be hex-encoded, >= 32 bytes)."
            )

    @classmethod
    def resolve_profile(cls, env: Mapping[str, str]) -> DeploymentProfile:
        """Load the deployment profile, requiring ``single_user_desktop``.

        ``ENTERPRISE_DEPLOYMENT_PROFILE`` may be unset (this module IS the
        desktop composition root, so the profile is implied) but must not
        name a different profile — that is a packaging error, not a knob.
        """

        raw = env.get(ENV_DEPLOYMENT_PROFILE, "").strip().lower()
        if raw and raw != PROFILE_SINGLE_USER_DESKTOP:
            raise DesktopEnvironmentError(
                f"backend_app.desktop_app only serves "
                f"{ENV_DEPLOYMENT_PROFILE}={PROFILE_SINGLE_USER_DESKTOP!r}; "
                f"got {raw!r}. Use backend_app.app:app for other profiles."
            )
        return DeploymentProfileLoader.load(
            env={**dict(env), ENV_DEPLOYMENT_PROFILE: PROFILE_SINGLE_USER_DESKTOP}
        )

    @classmethod
    def build_create_app_kwargs(
        cls,
        *,
        pool: object,
        profile: DeploymentProfile,
        env: Mapping[str, str],
    ) -> dict[str, object]:
        """Instantiate every existing Postgres adapter for ``create_app``.

        ``pool`` is duck-typed (tests pass a fake) but in production it is
        the shared :class:`PostgresConnectionPool`.

        Stores intentionally left at their in-memory ``create_app`` default
        (each is an accepted desktop-v1 gap until its Postgres adapter
        ships):

        * ``deploy_audit_service``       # in-memory: no postgres adapter yet (desktop v1 accepted gap)
        * ``todos_store``                # in-memory: no postgres adapter yet (desktop v1 accepted gap)
        * ``inbox_store``                # in-memory: no postgres adapter yet (desktop v1 accepted gap)
        * ``routines_store``             # in-memory: no postgres adapter yet (desktop v1 accepted gap)
        * ``connectors_store``           # in-memory: no postgres adapter yet (desktop v1 accepted gap)
        * ``webhooks_store``             # in-memory: no postgres adapter yet (desktop v1 accepted gap)
        * ``projects_store``             # in-memory: no postgres adapter yet (desktop v1 accepted gap)
        * ``project_templates_store``    # in-memory: no postgres adapter yet (desktop v1 accepted gap)
        * ``library_store``              # in-memory: no postgres adapter yet (desktop v1 accepted gap)
        * ``library_row_store``          # in-memory: no postgres adapter yet (desktop v1 accepted gap)
        * ``library_index_jobs_store``   # in-memory: no postgres adapter yet (desktop v1 accepted gap)
        * ``memory_store``               # in-memory: no postgres adapter yet (desktop v1 accepted gap)
        * ``agents_store``               # in-memory: no postgres adapter yet (desktop v1 accepted gap)
        * ``tools_store``                # in-memory: no postgres adapter yet (desktop v1 accepted gap)
        * ``palette_store``              # in-memory: no postgres adapter yet (desktop v1 accepted gap)
        * ``library_blob_store``         # local-disk adapter is the desktop-correct choice (blobs stay on the machine)
        * ``adapter_source_storage``     # local-filesystem adapter is the desktop-correct choice
        """

        # Local Fernet vault: the profile carries require_kms_token_vault=False,
        # so the factory resolves the `local` backend even under
        # BACKEND_ENVIRONMENT=production (it still requires
        # MCP_TOKEN_VAULT_SECRET, validated above).
        token_vault = TokenVaultFactory.create(profile=profile)

        session_service = SessionService(
            store=PostgresSessionStore(pool),
            # Profile is the single source of truth: single_user_desktop
            # carries dev_auth_bypass_allowed=False, so the dev mint stays off.
            dev_mint_allowed=profile.toggles.dev_auth_bypass_allowed,
        )

        return {
            # Desktop machines have no OTLP collector, so OTel setup is
            # skipped deterministically here (TelemetryBootstrap.configure()
            # fails closed under BACKEND_ENVIRONMENT=production without
            # OTEL_EXPORTER_OTLP_ENDPOINT). Structured logging stays on.
            # ai-backend/facade reach the same end state via the standard
            # OTEL_SDK_DISABLED=true env var set by the desktop supervisor.
            "configure_telemetry_on_create": False,
            # McpRegistryService/SkillRegistryService must receive the vault +
            # store explicitly: their zero-arg defaults call
            # TokenVaultFactory.create() WITHOUT the profile, which fails
            # closed under BACKEND_ENVIRONMENT=production with a local vault.
            "service": McpRegistryService(
                store=PostgresMcpStore(pool=pool),  # type: ignore[arg-type]
                token_vault=token_vault,
            ),
            "skill_service": SkillRegistryService(
                store=PostgresSkillStore(pool=pool),  # type: ignore[arg-type]
            ),
            "deployment": profile,
            "session_service": session_service,
            "identity_store": PostgresIdentityStore(pool),
            "oidc_store": PostgresOidcStore(pool),
            "token_vault": token_vault,
            "password_store": PostgresPasswordStore(pool),
            "lockout_store": PostgresLockoutStore(pool),
            "mfa_store": PostgresMfaStore(pool),
            "saml_store": PostgresSamlStore(pool),
            "scim_store": PostgresScimStore(pool),
            "me_store": PostgresMeStore(pool),
            "avatar_store": PostgresAvatarStore(pool),
            "tool_use_policy_store": PostgresToolUsePolicyStore(pool),
            "notification_prefs_store": PostgresNotificationPrefsStore(pool),
            "privacy_settings_store": PostgresPrivacySettingsStore(pool),
            "api_key_store": PostgresApiKeyStore(pool),
            "api_key_pepper": cls._api_key_pepper(env),
            "invitation_store": PostgresInvitationStore(pool),
            "auth_provider_domain_store": PostgresAuthProviderDomainStore(pool=pool),
            "magic_link_token_store": PostgresMagicLinkTokenStore(pool=pool),
            # Desktop has no outbound email path. With magic-link OFF the
            # default LoggingEmailDispatcher is unreachable, so create_app's
            # production email guard does not fire.
            "magic_link_globally_enabled": False,
            "adapter_registry_store": PostgresAdapterRegistryStore(pool),
            "settings_store": PostgresSettingsStore(pool),
            "provider_api_keys_store": PostgresProviderApiKeyStore(pool),
            # PR-B: live key validation is on for real desktop installs —
            # PUT stores anyway when the provider is unreachable, so an
            # offline desktop is never blocked from saving a key.
            "enable_provider_key_live_validation": True,
            # Account-merge engine (PRD §6.3): saga record + the privileged
            # re-key executor. The runtime (ai-backend) leg resolves from
            # AI_BACKEND_URL in create_app (the desktop supervisor exports it).
            "account_merge_store": PostgresAccountMergeStore(pool),
            "merge_data_port": PostgresMergeData(pool),
        }

    @classmethod
    def create_desktop_app(cls, env: Mapping[str, str] | None = None) -> FastAPI:
        """Validate env, resolve the profile, wire Postgres, build the app."""

        resolved_env: dict[str, str] = (
            dict(env) if env is not None else dict(os.environ)
        )
        cls.validate_env(resolved_env)
        profile = cls.resolve_profile(resolved_env)
        pool = PostgresConnectionPool.shared(resolved_env["DATABASE_URL"].strip())
        return create_app(
            **cls.build_create_app_kwargs(pool=pool, profile=profile, env=resolved_env)  # type: ignore[arg-type]
        )

    @classmethod
    def _api_key_pepper(cls, env: Mapping[str, str]) -> bytes:
        """Resolve the personal-API-key HMAC pepper for this install.

        ``BACKEND_API_KEY_PEPPER`` wins when provided (>= 16 bytes).
        Otherwise the pepper is derived deterministically from
        ``ENTERPRISE_AUTH_SECRET`` so a desktop install never falls back to
        the shared dev constant in ``create_app`` — keys stay valid across
        restarts and rotate together with the install's auth secret.
        """

        explicit = env.get("BACKEND_API_KEY_PEPPER", "").encode("utf-8")
        if len(explicit) >= cls._MIN_PEPPER_BYTES:
            return explicit
        auth_secret = env.get("ENTERPRISE_AUTH_SECRET", "").strip()
        return hashlib.sha256(
            b"desktop-api-key-pepper:" + auth_secret.encode("utf-8")
        ).digest()


def __getattr__(name: str) -> object:
    """Lazily build the module-level ``app`` for uvicorn.

    ``uvicorn backend_app.desktop_app:app`` resolves the attribute exactly
    once; building lazily keeps ``import backend_app.desktop_app`` free of
    side effects so unit tests can exercise :class:`DesktopComposer`
    without a database or secrets in the environment.
    """

    if name == "app":
        application = DesktopComposer.create_desktop_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

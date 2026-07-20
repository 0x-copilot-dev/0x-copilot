"""FastAPI application for core product backend APIs."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from copilot_service_contracts.scopes import (
    ADMIN_AUDIT_EXPORT,
    CONNECTORS_AUTH,
    MCP_READ,
    MCP_WRITE,
    RUNTIME_USE,
    SKILLS_READ,
    SKILLS_WRITE,
)
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status

from backend_app.auth import BackendServiceAuthenticator
from backend_app.identity.rbac import RequireScopes, public_route
from backend_app.deployment_profile import (
    DeploymentProfile,
    log_profile,
    resolve_or_exit,
)
from backend_app.contracts import (
    CreateMcpServerRequest,
    CreateSkillRequest,
    DeployAuditEventResponse,
    DeployAuditRequest,
    InternalMcpAuthRequest,
    InternalMcpClientSession,
    InternalMcpRpcRequest,
    InternalMcpRpcResponse,
    InstallMcpServerRequest,
    InternalMcpServerListResponse,
    InternalSkillBundle,
    InternalSkillListResponse,
    McpAuthCallbackRequest,
    McpAuthStartRequest,
    McpAuthStartResponse,
    McpCatalogResponse,
    McpServerListResponse,
    McpServerResponse,
    OAuthTokenRequest,
    SkillListResponse,
    SkillResponse,
    ToolListResponse,
    UpdateMcpServerRequest,
    UpdateSkillRequest,
)
from backend_app.identity import (
    AuthProviderDomainStore,
    BootstrapAdminService,
    DiscoveryService,
    EmailDispatcherPort,
    IdentityStore,
    InMemoryAuthProviderDomainStore,
    InMemoryIdentityStore,
    InMemoryInvitationStore,
    InMemoryLockoutStore,
    InMemoryMagicLinkTokenStore,
    InMemoryMeStore,
    InMemoryMfaStore,
    InMemoryOidcStore,
    InMemoryPasswordStore,
    InMemoryRateLimiter,
    InMemorySamlStore,
    InMemoryScimStore,
    InMemorySessionStore,
    InMemorySiweStore,
    InvitationStore,
    InvitationsService,
    LockoutService,
    LockoutStore,
    LoggingEmailDispatcher,
    MagicLinkService,
    MagicLinkTokenStore,
    MeStore,
    MfaService,
    MfaStore,
    OidcService,
    OidcStore,
    OneLoginSamlVerifier,
    PasswordService,
    PasswordStore,
    SamlService,
    SamlStore,
    SamlVerifier,
    ScimService,
    ScimStore,
    SessionAuthSecretMissing,
    SessionSelectService,
    SessionService,
    SiweService,
    SiweStore,
    build_default_email_dispatcher,
    build_pick_codec,
)
from backend_app.identity.google import (
    build_google_provider,
    ensure_global_auth_provider,
)
from backend_app.identity.siwe import (
    ENV_SIWE_ALLOWED_CHAIN_IDS,
    ENV_SIWE_ORIGIN,
    parse_allowed_chain_ids,
)
from backend_app.identity.session_sweeper import SessionSweeper
from backend_app.observability import (
    RequestContextMiddleware,
    TelemetryBootstrap,
    configure_logging,
    emit_access_log,
)
from backend_app.dev_idp import register_dev_idp_routes
from backend_app.home import register_home_routes, register_home_sse_routes
from backend_app.inbox import (
    InMemoryInboxStore,
    InboxService,
    InboxStore,
    register_inbox_internal_routes,
    register_inbox_routes,
    register_inbox_sse_routes,
)
from backend_app.routines import (
    InMemoryRoutinesStore,
    RoutinesService,
    RoutinesStore,
    register_routines_routes,
)
from backend_app.connectors import (
    ConnectorsService,
    ConnectorsStore,
    InMemoryConnectorsStore,
    load_catalog,
    register_connector_routes,
    register_connector_sse_routes,
)
from backend_app.connectors.desktop_routes import (
    register_desktop_connector_routes,
)
from backend_app.connectors.oauth_coordinator import DesktopMcpOAuthCoordinator
from backend_app.connectors.profile_catalog import DesktopProfileCatalog
from backend_app.webhooks import (
    InMemoryWebhooksStore,
    WebhooksService,
    WebhooksStore,
    register_webhook_routes,
)
from backend_app.todos import (
    InMemoryTodosStore,
    TodosService,
    TodosStore,
    register_todos_routes,
)
from backend_app.agents import (
    AgentsService,
    AgentsStore,
    InMemoryAgentsStore,
    register_agents_routes,
)
from backend_app.tools import (
    InMemoryToolsStore,
    ToolsService,
    ToolsStore,
    register_tool_internal_routes,
    register_tool_routes,
    register_tool_sse_routes,
)
from backend_app.projects import (
    InMemoryProjectsStore,
    ProjectsService,
    ProjectsStore,
    register_projects_routes,
)
from backend_app.library import (
    InMemoryLibrarySearchIndex,
    InMemoryLibraryStore,
    LibraryService,
    LibraryStore,
    SearchEngine,
    register_library_routes,
    register_library_search_routes,
)
from backend_app.library.index_jobs import (
    InMemoryLibraryIndexJobsStore,
    LibraryIndexJobsStore,
)
from backend_app.memory import (
    InMemoryMemoryStore,
    InMemoryMemorySearchIndex,
    MemoryIndexer,
    MemorySearchEngine,
    MemoryService,
    MemoryStore,
    register_memory_routes,
    register_memory_sse_routes,
)
from backend_app.palette import (
    InMemoryPaletteStore,
    PaletteRefreshDispatcher,
    PaletteService,
    PaletteStorePort,
    register_palette_routes,
)
from backend_app.projects.template_routes import register_template_routes
from backend_app.projects.templates import (
    InMemoryProjectTemplatesStore,
    ProjectTemplatesStore,
)
from backend_app.liveness import (
    AiBackendLivenessClient,
    InboxLivenessReader,
    LivenessService,
    RoutinesLivenessReader,
    register_liveness_routes,
)
from backend_app.routes.audit_export import register_audit_export_routes
from backend_app.routes.audit_list import register_audit_list_routes
from backend_app.routes.billing import register_billing_routes
from backend_app.routes.health import register_health_routes
from backend_app.routes.invitations import register_invitation_routes
from backend_app.routes.lockouts import register_lockout_routes
from backend_app.routes.login_email_first import register_login_email_first_routes
from backend_app.routes.siwe import register_siwe_routes
from backend_app.routes.me import register_me_routes
from backend_app.routes.me_preferences import register_me_preferences_routes
from backend_app.routes.api_keys import register_api_key_routes
from backend_app.routes.notifications import (
    register_notification_preferences_routes,
)
from backend_app.routes.privacy import register_privacy_settings_routes
from backend_app.routes.tool_use_policies import register_tool_use_policy_routes
from backend_app.routes.runtime_policies import register_runtime_policies_routes
from backend_app.provider_keys import (
    InMemoryProviderApiKeyStore,
    ProviderApiKeyStore,
    ProviderKeysService,
    register_provider_keys_routes,
)
from backend_app.routes.me_identities import register_me_identities_routes
from backend_app.routes.me_profile import register_me_profile_routes
from backend_app.routes.members import register_members_routes
from backend_app.routes.me_mfa import register_me_mfa_routes
from backend_app.routes.mfa import register_mfa_routes
from backend_app.routes.workspace_mfa_policy import (
    register_workspace_mfa_policy_routes,
)
from backend_app.routes.oidc import register_oidc_routes
from backend_app.routes.passwords import register_password_routes
from backend_app.routes.saml import register_saml_routes
from backend_app.routes.scim import register_scim_routes
from backend_app.routes.sessions import register_session_routes
from backend_app.routes.siem import register_siem_admin_routes
from backend_app.routes.workspace import register_workspace_routes
from backend_app.settings import (
    InMemorySettingsStore,
    SettingsService,
    SettingsStore,
    register_settings_routes,
)
from backend_app.team import (
    InMemoryTeamStore,
    StoreBackedAssetCounts,
    TeamService,
    register_team_routes,
    register_team_sse_routes,
)
from backend_app.token_vault import TokenVault, TokenVaultFactory
from backend_app.service import (
    DeployAuditService,
    McpRegistryService,
    SkillRegistryService,
    ToolCatalogService,
)
from backend_app.store import PostgresConnectionPool


class _AppServices:
    """Typed accessors for service singletons attached to app state."""

    @staticmethod
    def mcp(application: FastAPI) -> McpRegistryService:
        return application.state.mcp_service

    @staticmethod
    def skills(application: FastAPI) -> SkillRegistryService:
        return application.state.skill_service

    @staticmethod
    def tool_catalog(application: FastAPI) -> ToolCatalogService:
        return application.state.tool_catalog_service

    @staticmethod
    def deploy_audit(application: FastAPI) -> DeployAuditService:
        return application.state.deploy_audit_service


@asynccontextmanager
async def _lifespan(application: FastAPI):
    sweeper = getattr(application.state, "session_sweeper", None)
    if sweeper is not None:
        await sweeper.start()
    try:
        yield
    finally:
        if sweeper is not None:
            await sweeper.stop()
        PostgresConnectionPool.close_shared()


def _default_token_vault(
    deployment: DeploymentProfile | None,
) -> TokenVault | None:
    """Build the default TokenVault if a secret is available.

    OIDC needs the vault to encrypt refresh tokens at rest. Where the secret
    isn't configured (fresh dev box) we omit the OIDC routes rather than
    crash boot. Production profiles enforce the vault via the deployment
    profile (the TokenVaultFactory itself fails closed under managed /
    self-hosted profiles when the secret is missing).
    """

    try:
        return TokenVaultFactory.create(profile=deployment)
    except Exception:
        return None


def _assert_email_dispatcher_safe_for_environment(
    dispatcher: EmailDispatcherPort,
    *,
    magic_link_enabled: bool,
) -> None:
    """Production fail-closed guard for the magic-link email dispatcher.

    The ``LoggingEmailDispatcher`` writes the one-time URL to logs and
    never sends mail. That's fine in dev (the URL still appears in the
    operator's terminal). In production it means users silently never
    receive the email we ask them to click, so we refuse to start.

    The guard only fires when magic-link is enabled. Bank/strict-SSO
    deploys turn magic-link off entirely; for them the
    ``LoggingEmailDispatcher`` is unreachable and harmless.

    Operators inject a real adapter (SES/SMTP/Postmark) at
    ``create_app(...)`` construction; the same hook the ``TokenVault``
    uses for KMS injection.
    """

    if not magic_link_enabled:
        return
    if not isinstance(dispatcher, LoggingEmailDispatcher):
        return
    environment = os.environ.get("BACKEND_ENVIRONMENT", "development").strip().lower()
    if environment != "production":
        return
    raise RuntimeError(
        "BACKEND_ENVIRONMENT=production but no email_dispatcher was "
        "injected; magic-link login would log the URL to stdout instead "
        "of sending mail. Inject an SES/SMTP/Postmark adapter at "
        "create_app(...) construction or set "
        "magic_link_globally_enabled=False."
    )


def _default_saml_verifier() -> SamlVerifier | None:
    """Construct the production SAML verifier if ``python3-saml`` is installed.

    Returns ``None`` on dev boxes that don't have ``xmlsec1`` (the OneLogin
    library import will fail). When ``None`` we omit the SAML routes
    rather than crash boot, matching how OIDC / MFA behave when their
    optional secrets are missing. Tests inject :class:`FakeSamlVerifier`
    directly through ``saml_verifier=``.
    """

    try:
        return OneLoginSamlVerifier()
    except Exception:
        return None


def _default_session_service(
    deployment: DeploymentProfile,
) -> SessionService | None:
    """Build the default in-memory ``SessionService`` if a secret is available.

    The session service mints / verifies HMAC bearers so it requires
    ``ENTERPRISE_AUTH_SECRET``. In a fresh dev environment where the secret
    is not set we omit the routes rather than crashing app boot — the
    operator sees the routes return 404 and the new functionality is opt-in.

    Production profiles (``single_tenant_managed`` / ``single_tenant_self_hosted``)
    never silently skip: the secret IS required there. The deployment
    profile loader (C1) already fails closed for `production` / managed
    profiles when env is misconfigured, so by the time we get here the
    secret is expected to exist.
    """

    try:
        return SessionService(
            store=InMemorySessionStore(),
            dev_mint_allowed=deployment.toggles.dev_auth_bypass_allowed,
        )
    except SessionAuthSecretMissing:
        return None


def create_app(
    service: McpRegistryService | None = None,
    skill_service: SkillRegistryService | None = None,
    deploy_audit_service: DeployAuditService | None = None,
    *,
    configure_logging_on_create: bool = True,
    configure_telemetry_on_create: bool = True,
    deployment: DeploymentProfile | None = None,
    session_service: SessionService | None = None,
    identity_store: IdentityStore | None = None,
    oidc_store: OidcStore | None = None,
    oidc_service: OidcService | None = None,
    token_vault: TokenVault | None = None,
    password_store: PasswordStore | None = None,
    password_service: PasswordService | None = None,
    lockout_store: LockoutStore | None = None,
    lockout_service: LockoutService | None = None,
    mfa_store: MfaStore | None = None,
    mfa_service: MfaService | None = None,
    saml_store: SamlStore | None = None,
    saml_service: SamlService | None = None,
    saml_verifier: SamlVerifier | None = None,
    siwe_store: SiweStore | None = None,
    siwe_service: SiweService | None = None,
    scim_store: ScimStore | None = None,
    scim_service: ScimService | None = None,
    me_store: MeStore | None = None,
    avatar_store: object | None = None,
    tool_use_policy_store: object | None = None,
    notification_prefs_store: object | None = None,
    privacy_settings_store: object | None = None,
    api_key_store: object | None = None,
    api_key_pepper: bytes | None = None,
    invitation_store: InvitationStore | None = None,
    invitations_service: InvitationsService | None = None,
    auth_provider_domain_store: AuthProviderDomainStore | None = None,
    magic_link_token_store: MagicLinkTokenStore | None = None,
    discovery_service: DiscoveryService | None = None,
    magic_link_service: MagicLinkService | None = None,
    session_select_service: SessionSelectService | None = None,
    email_dispatcher: EmailDispatcherPort | None = None,
    magic_link_globally_enabled: bool = True,
    magic_link_base_url: str = "http://localhost:5173",
    adapter_registry_service: object | None = None,
    adapter_registry_store: object | None = None,
    adapter_source_storage: object | None = None,
    todos_store: TodosStore | None = None,
    inbox_store: InboxStore | None = None,
    routines_store: RoutinesStore | None = None,
    connectors_store: ConnectorsStore | None = None,
    webhooks_store: WebhooksStore | None = None,
    projects_store: ProjectsStore | None = None,
    project_templates_store: ProjectTemplatesStore | None = None,
    library_store: LibraryStore | None = None,
    library_blob_store: object | None = None,
    library_row_store: object | None = None,
    library_index_jobs_store: LibraryIndexJobsStore | None = None,
    memory_store: MemoryStore | None = None,
    agents_store: AgentsStore | None = None,
    tools_store: ToolsStore | None = None,
    settings_store: SettingsStore | None = None,
    provider_api_keys_store: ProviderApiKeyStore | None = None,
    liveness_service: LivenessService | None = None,
    palette_store: PaletteStorePort | None = None,
) -> FastAPI:
    if configure_logging_on_create:
        configure_logging()
    if configure_telemetry_on_create:
        TelemetryBootstrap.configure()
    resolved_deployment = deployment or resolve_or_exit()
    log_profile(resolved_deployment)
    app = FastAPI(title="0xCopilot Backend", lifespan=_lifespan)
    app.add_middleware(RequestContextMiddleware, access_log_emitter=emit_access_log)
    if configure_telemetry_on_create:
        TelemetryBootstrap.instrument_fastapi(app)
    app.state.mcp_service = service or McpRegistryService()
    app.state.skill_service = skill_service or SkillRegistryService()
    app.state.tool_catalog_service = ToolCatalogService(
        mcp_service=app.state.mcp_service,
        skill_service=app.state.skill_service,
    )
    app.state.deploy_audit_service = deploy_audit_service or DeployAuditService()
    app.state.deployment = resolved_deployment
    # Identity store is shared by sessions + OIDC (and later A4..A10).
    resolved_identity_store: IdentityStore = identity_store or InMemoryIdentityStore()
    app.state.identity_store = resolved_identity_store
    resolved_session_service = session_service or _default_session_service(
        resolved_deployment
    )
    if resolved_session_service is not None:
        app.state.session_service = resolved_session_service
        register_session_routes(app, resolved_session_service)
        app.state.session_sweeper = SessionSweeper(sessions=resolved_session_service)

        # Lockout (A8): wired before OIDC + local-password so both flows
        # share the same per-org policy + active-lockout writes. Defaults
        # to ``enforce_lockout=False`` per spec — the migration ships
        # without changing existing login behavior.
        resolved_lockout_store: LockoutStore = lockout_store or InMemoryLockoutStore()
        app.state.lockout_store = resolved_lockout_store
        resolved_lockout_service = lockout_service or LockoutService(
            identity_store=resolved_identity_store,
            lockout_store=resolved_lockout_store,
        )
        app.state.lockout_service = resolved_lockout_service
        register_lockout_routes(
            app,
            identity_store=resolved_identity_store,
            lockout_service=resolved_lockout_service,
        )

        # OIDC depends on a session service (it mints sessions on callback).
        # If the session secret is unavailable, OIDC + MFA routes are also
        # omitted (MFA needs TokenVault for TOTP secret encryption).
        resolved_oidc_store: OidcStore = oidc_store or InMemoryOidcStore()
        app.state.oidc_store = resolved_oidc_store
        resolved_token_vault = token_vault or _default_token_vault(resolved_deployment)
        resolved_mfa_service: MfaService | None = None
        if resolved_token_vault is not None:
            app.state.token_vault = resolved_token_vault
            # MFA (A6) — TOTP secrets ride the TokenVault adapter for
            # encryption-at-rest. Wired BEFORE OIDC + Password so both
            # login flows can pass it through.
            resolved_mfa_store: MfaStore = mfa_store or InMemoryMfaStore()
            app.state.mfa_store = resolved_mfa_store
            resolved_mfa_service = mfa_service or MfaService(
                identity_store=resolved_identity_store,
                mfa_store=resolved_mfa_store,
                token_vault=resolved_token_vault,
            )
            app.state.mfa_service = resolved_mfa_service
            register_mfa_routes(
                app,
                service=resolved_mfa_service,
                sessions=resolved_session_service,
            )
            # PR 8.2 — caller-scoped MFA wrapper for the Settings UI.
            # Same MfaService, query-based identity (so the facade's
            # ``_forward_me`` helper can reach it without rewriting
            # bodies). Pure thin layer; auditing + replay live in the
            # service.
            register_me_mfa_routes(app, service=resolved_mfa_service)
            # PR 8.3 — admin editor for ``identity_policies.mfa_required``
            # + ``step_up_window_seconds``. Reads through the same
            # IdentityStore the OIDC mint already consults at sign-in,
            # so a toggle takes effect on the very next login.
            register_workspace_mfa_policy_routes(
                app, identity_store=resolved_identity_store
            )
            # Global "Continue with Google" (env-configured; reserved id
            # "google"). The anchor row keeps Postgres FKs satisfied;
            # resolution itself always reads the env-built record.
            resolved_global_providers = {}
            google_provider = build_google_provider(
                environ=os.environ, token_vault=resolved_token_vault
            )
            if google_provider is not None:
                ensure_global_auth_provider(
                    identity_store=resolved_identity_store,
                    record=google_provider,
                )
                resolved_global_providers[google_provider.provider_id] = google_provider
            app.state.global_auth_providers = resolved_global_providers
            resolved_oidc_service = oidc_service or OidcService(
                identity_store=resolved_identity_store,
                oidc_store=resolved_oidc_store,
                sessions=resolved_session_service,
                token_vault=resolved_token_vault,
                lockout=resolved_lockout_service,
                mfa=resolved_mfa_service,
                global_providers=resolved_global_providers,
                allow_self_signup=resolved_deployment.toggles.allow_self_signup,
            )
            app.state.oidc_service = resolved_oidc_service
            register_oidc_routes(
                app,
                service=resolved_oidc_service,
                identity_store=resolved_identity_store,
                global_providers=resolved_global_providers,
            )

        # SAML (A5): mints sessions on a successful ACS POST. Same gating
        # as OIDC + local password — wired only when the session secret is
        # available. The verifier is constructed lazily so a host without
        # ``xmlsec1`` doesn't crash boot — production resolves the
        # ``OneLoginSamlVerifier`` here, tests inject ``FakeSamlVerifier``.
        resolved_saml_store: SamlStore = saml_store or InMemorySamlStore()
        app.state.saml_store = resolved_saml_store
        resolved_saml_verifier = saml_verifier or _default_saml_verifier()
        if resolved_saml_verifier is not None:
            app.state.saml_verifier = resolved_saml_verifier
            resolved_saml_service = saml_service or SamlService(
                identity_store=resolved_identity_store,
                saml_store=resolved_saml_store,
                sessions=resolved_session_service,
                verifier=resolved_saml_verifier,
                lockout=resolved_lockout_service,
                mfa=resolved_mfa_service,
            )
            app.state.saml_service = resolved_saml_service
            register_saml_routes(
                app,
                service=resolved_saml_service,
                identity_store=resolved_identity_store,
            )

        # SCIM (A7): no session-service dependency (it operates on its
        # own bearer-token surface) but the routes still piggyback on the
        # session_service block because the rest of the auth machinery is
        # only wired here. ScimService is purely identity-store + scim-store
        # composition.
        resolved_scim_store: ScimStore = scim_store or InMemoryScimStore()
        app.state.scim_store = resolved_scim_store
        resolved_scim_service = scim_service or ScimService(
            identity_store=resolved_identity_store,
            scim_store=resolved_scim_store,
        )
        app.state.scim_service = resolved_scim_service
        register_scim_routes(app, service=resolved_scim_service)

        # Local password (A4): same gating as OIDC — needs the session
        # service. Argon2id is always available because argon2-cffi is a
        # hard dep.
        resolved_password_store: PasswordStore = (
            password_store or InMemoryPasswordStore()
        )
        app.state.password_store = resolved_password_store
        resolved_password_service = password_service or PasswordService(
            identity_store=resolved_identity_store,
            password_store=resolved_password_store,
            sessions=resolved_session_service,
            lockout=resolved_lockout_service,
            mfa=resolved_mfa_service,
        )
        app.state.password_service = resolved_password_service
        bootstrap_service = BootstrapAdminService(
            identity_store=resolved_identity_store,
            password_service=resolved_password_service,
        )
        app.state.bootstrap_admin_service = bootstrap_service
        register_password_routes(
            app,
            service=resolved_password_service,
            bootstrap=bootstrap_service,
        )

        # Login email-first (PR 5.1): IdP discovery + magic-link + workspace
        # picker. Same gating as the rest of the auth machinery — needs the
        # session service to mint, identity store for membership lookup, and
        # an HMAC secret (re-uses ENTERPRISE_AUTH_SECRET via SessionService).
        # Email dispatcher port: production deploys MUST inject a real
        # adapter (SES / SMTP / Postmark); the default LoggingEmailDispatcher
        # writes structured logs without sending mail.
        resolved_domain_store: AuthProviderDomainStore = (
            auth_provider_domain_store or InMemoryAuthProviderDomainStore()
        )
        app.state.auth_provider_domain_store = resolved_domain_store
        resolved_magic_link_store: MagicLinkTokenStore = (
            magic_link_token_store or InMemoryMagicLinkTokenStore()
        )
        app.state.magic_link_token_store = resolved_magic_link_store
        resolved_email_dispatcher = email_dispatcher or build_default_email_dispatcher()
        _assert_email_dispatcher_safe_for_environment(
            resolved_email_dispatcher,
            magic_link_enabled=magic_link_globally_enabled,
        )
        app.state.email_dispatcher = resolved_email_dispatcher
        resolved_rate_limiter = InMemoryRateLimiter()
        app.state.login_email_first_rate_limiter = resolved_rate_limiter
        resolved_pick_codec = build_pick_codec(
            secret=resolved_session_service._auth_secret  # noqa: SLF001
        )
        resolved_discovery_service = discovery_service or DiscoveryService(
            domain_store=resolved_domain_store,
            identity_store=resolved_identity_store,
            rate_limiter=resolved_rate_limiter,
            magic_link_globally_enabled=magic_link_globally_enabled,
        )
        app.state.discovery_service = resolved_discovery_service
        resolved_magic_link_service = magic_link_service or MagicLinkService(
            token_store=resolved_magic_link_store,
            identity_store=resolved_identity_store,
            sessions=resolved_session_service,
            pick_codec=resolved_pick_codec,
            rate_limiter=resolved_rate_limiter,
            email_dispatcher=resolved_email_dispatcher,
            base_url=magic_link_base_url,
            magic_link_globally_enabled=magic_link_globally_enabled,
        )
        app.state.magic_link_service = resolved_magic_link_service
        resolved_session_select_service = (
            session_select_service
            or SessionSelectService(
                identity_store=resolved_identity_store,
                sessions=resolved_session_service,
                pick_codec=resolved_pick_codec,
                rate_limiter=resolved_rate_limiter,
            )
        )
        app.state.session_select_service = resolved_session_select_service
        register_login_email_first_routes(
            app,
            discovery=resolved_discovery_service,
            magic_link=resolved_magic_link_service,
            session_select=resolved_session_select_service,
        )

        # Sign-In-With-Ethereum (SIWE): wallet entry ramp. Same gating as
        # the rest of the auth machinery — needs the session service to
        # mint. Reuses the login-email-first in-process rate limiter for
        # the unauthenticated nonce endpoint. ``SIWE_ORIGIN`` pins the
        # EIP-4361 domain/URI binding to the serving origin (defaults to
        # the dev frontend origin, same fallback as magic links);
        # ``SIWE_ALLOWED_CHAIN_IDS`` narrows the accepted chains.
        resolved_siwe_store: SiweStore = siwe_store or InMemorySiweStore()
        app.state.siwe_store = resolved_siwe_store
        resolved_siwe_service = siwe_service or SiweService(
            identity_store=resolved_identity_store,
            siwe_store=resolved_siwe_store,
            sessions=resolved_session_service,
            expected_origin=(
                os.environ.get(ENV_SIWE_ORIGIN, "").strip() or magic_link_base_url
            ),
            allowed_chain_ids=parse_allowed_chain_ids(
                os.environ.get(ENV_SIWE_ALLOWED_CHAIN_IDS)
            ),
            lockout=resolved_lockout_service,
            mfa=resolved_mfa_service,
            allow_self_signup=resolved_deployment.toggles.allow_self_signup,
            rate_limiter=resolved_rate_limiter,
        )
        app.state.siwe_service = resolved_siwe_service
        register_siwe_routes(app, service=resolved_siwe_service)

    @app.get("/v1/health", dependencies=[Depends(public_route())])
    def health() -> dict[str, object]:
        return {
            "service": "backend",
            "deployment_profile": resolved_deployment.name,
            "feature_toggles_hash": resolved_deployment.toggles_hash(),
        }

    @app.post(
        "/v1/mcp/servers",
        response_model=McpServerResponse,
        dependencies=[Depends(RequireScopes(MCP_WRITE))],
    )
    def create_server(
        request: Request, payload: CreateMcpServerRequest
    ) -> McpServerResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=payload.org_id, user_id=payload.user_id
        )
        payload = payload.model_copy(
            update={"org_id": identity.org_id, "user_id": identity.user_id}
        )
        return _AppServices.mcp(app).create_server(payload)

    @app.get(
        "/v1/mcp/catalog",
        response_model=McpCatalogResponse,
        # PR 4.4.6 — org-agnostic curated list. ``MCP_READ`` is the
        # appropriate gate (any reader who can see installed servers
        # should also see the catalog they can install from).
        dependencies=[Depends(RequireScopes(MCP_READ))],
    )
    def list_catalog() -> McpCatalogResponse:
        return _AppServices.mcp(app).list_catalog()

    @app.post(
        "/v1/mcp/servers/install",
        response_model=McpServerResponse,
        dependencies=[Depends(RequireScopes(MCP_WRITE))],
    )
    def install_server(
        request: Request, payload: InstallMcpServerRequest
    ) -> McpServerResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=payload.org_id, user_id=payload.user_id
        )
        payload = payload.model_copy(
            update={"org_id": identity.org_id, "user_id": identity.user_id}
        )
        try:
            return _AppServices.mcp(app).install_from_catalog(payload)
        except ValueError as exc:
            message = str(exc)
            # 404 for unknown slug; 422 for the pre-registered-client gate
            # so the frontend can route to the credentials form vs. show
            # a "not in catalog" toast.
            if message.startswith("Unknown catalog entry"):
                raise HTTPException(status.HTTP_404_NOT_FOUND, message) from exc
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, message) from exc

    @app.get(
        "/v1/mcp/servers",
        response_model=McpServerListResponse,
        dependencies=[Depends(RequireScopes(MCP_READ))],
    )
    def list_servers(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> McpServerListResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        return _AppServices.mcp(app).list_servers(
            org_id=identity.org_id, user_id=identity.user_id
        )

    # Sectioned listing for the composer Tools popover: user-installed
    # skill bundles + authenticated MCP servers, each tagged with
    # ``kind: "skill" | "mcp"`` so the frontend can partition the popover
    # into its Skills and MCPs sections without re-deriving the type.
    # Requires both ``MCP_READ`` and ``SKILLS_READ`` because the response
    # spans both stores. Caller-supplied ``org_id`` / ``user_id`` are
    # rebound to the verified identity by ``scoped_identity``.
    @app.get(
        "/v1/mcp/tools",
        response_model=ToolListResponse,
        dependencies=[Depends(RequireScopes(MCP_READ, SKILLS_READ))],
    )
    def list_tools(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> ToolListResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        return _AppServices.tool_catalog(app).list_tools(
            org_id=identity.org_id, user_id=identity.user_id
        )

    @app.delete(
        "/v1/mcp/servers/{server_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(MCP_WRITE))],
    )
    def delete_server(
        request: Request,
        server_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        deleted = _AppServices.mcp(app).delete_server(
            org_id=identity.org_id, user_id=identity.user_id, server_id=server_id
        )
        if not deleted:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "MCP server not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.patch(
        "/v1/mcp/servers/{server_id}",
        response_model=McpServerResponse,
        dependencies=[Depends(RequireScopes(MCP_WRITE))],
    )
    def update_server(
        request: Request,
        server_id: str,
        payload: UpdateMcpServerRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> McpServerResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            return _AppServices.mcp(app).update_server(
                org_id=identity.org_id,
                user_id=identity.user_id,
                server_id=server_id,
                request=payload,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.post(
        "/v1/mcp/servers/{server_id}/auth/start",
        response_model=McpAuthStartResponse,
        dependencies=[Depends(RequireScopes(CONNECTORS_AUTH))],
    )
    def start_auth(
        request: Request, server_id: str, payload: McpAuthStartRequest
    ) -> McpAuthStartResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=payload.org_id, user_id=payload.user_id
        )
        payload = payload.model_copy(
            update={"org_id": identity.org_id, "user_id": identity.user_id}
        )
        try:
            return _AppServices.mcp(app).start_auth(
                server_id=server_id, request=payload
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @app.post(
        "/v1/mcp/servers/{server_id}/auth/skip",
        response_model=McpServerResponse,
        dependencies=[Depends(RequireScopes(CONNECTORS_AUTH))],
    )
    def skip_auth(
        request: Request,
        server_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> McpServerResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            return _AppServices.mcp(app).skip_auth(
                org_id=identity.org_id, user_id=identity.user_id, server_id=server_id
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.get(
        "/v1/mcp/oauth/callback",
        response_model=McpServerResponse,
        # Public: the OAuth provider redirects here without our session
        # bearer; the ``state`` token in the URL is the trust anchor.
        dependencies=[Depends(public_route())],
    )
    def oauth_callback(
        state: str,
        code: str | None = None,
        error: str | None = None,
        error_description: str | None = None,
    ) -> McpServerResponse:
        try:
            return _AppServices.mcp(app).complete_auth(
                McpAuthCallbackRequest(
                    state=state,
                    code=code,
                    error=error,
                    error_description=error_description,
                )
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @app.get(
        "/internal/v1/mcp/cards",
        response_model=InternalMcpServerListResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def internal_cards(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> InternalMcpServerListResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        return _AppServices.mcp(app).list_internal_cards(
            org_id=identity.org_id, user_id=identity.user_id
        )

    # PR 4.4.7 Phase 2 (Slice B) — catalog entries the agent may surface
    # as progressive-discovery suggestions. The ai-backend calls this at
    # run-create and stuffs the response into
    # ``AgentRuntimeContext.suggested_connectors``. Filter rules live in
    # ``McpRegistryService.list_suggestible_connectors``; this route is
    # a thin wire layer that resolves the user's discoverable override
    # map from MeStore and forwards it.
    @app.get(
        "/internal/v1/me/suggestible-connectors",
        response_model=McpCatalogResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def internal_suggestible_connectors(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
        exclude_paused: str = Query(
            "",
            description=(
                "Comma-separated server_ids to exclude (typically the "
                "conversation's paused_connectors). Accepts both the "
                "bare slug ('linear') and the seed prefix ('seed:linear')."
            ),
        ),
    ) -> McpCatalogResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        excluded = tuple(
            piece.strip() for piece in exclude_paused.split(",") if piece.strip()
        )
        prefs_record = app.state.me_store.get_preferences(
            org_id=identity.org_id, user_id=identity.user_id
        )
        overrides: dict[str, bool] = {}
        if prefs_record is not None:
            stored = (
                prefs_record.preferences.get("discoverable_connectors", {})
                if isinstance(prefs_record.preferences, dict)
                else {}
            )
            raw_overrides = (
                stored.get("overrides", {}) if isinstance(stored, dict) else {}
            )
            if isinstance(raw_overrides, dict):
                for key, value in raw_overrides.items():
                    if isinstance(key, str) and isinstance(value, bool):
                        overrides[key] = value
        return _AppServices.mcp(app).list_suggestible_connectors(
            org_id=identity.org_id,
            user_id=identity.user_id,
            exclude_paused=excluded,
            user_overrides=overrides,
        )

    @app.post(
        "/internal/v1/mcp/servers/{server_id}/auth/start",
        response_model=McpAuthStartResponse,
        dependencies=[Depends(RequireScopes(CONNECTORS_AUTH))],
    )
    def internal_start_auth(
        request: Request, server_id: str, payload: InternalMcpAuthRequest
    ) -> McpAuthStartResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request,
            org_id=payload.org_id,
            user_id=payload.user_id,
        )
        payload = payload.model_copy(
            update={"org_id": identity.org_id, "user_id": identity.user_id}
        )
        try:
            return _AppServices.mcp(app).start_auth(
                server_id=server_id, request=payload
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @app.post(
        "/internal/v1/mcp/servers/{server_id}/client-session",
        response_model=InternalMcpClientSession,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def internal_client_session(
        request: Request,
        server_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> InternalMcpClientSession:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            return _AppServices.mcp(app).create_internal_client_session(
                org_id=identity.org_id,
                user_id=identity.user_id,
                server_id=server_id,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.post(
        "/internal/v1/mcp/servers/{server_id}/rpc",
        response_model=InternalMcpRpcResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def internal_mcp_rpc(
        request: Request,
        server_id: str,
        payload: InternalMcpRpcRequest,
    ) -> InternalMcpRpcResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.org_id, user_id=payload.user_id
        )
        payload = payload.model_copy(
            update={"org_id": identity.org_id, "user_id": identity.user_id}
        )
        try:
            return _AppServices.mcp(app).proxy_internal_rpc(
                org_id=identity.org_id,
                user_id=identity.user_id,
                server_id=server_id,
                request=payload,
            )
        except ValueError as exc:
            detail = str(exc)
            status_code = (
                status.HTTP_401_UNAUTHORIZED
                if "authenticated" in detail or "OAuth token" in detail
                else status.HTTP_400_BAD_REQUEST
            )
            raise HTTPException(status_code, detail) from exc

    @app.post(
        "/internal/v1/mcp/servers/{server_id}/test-token",
        response_model=McpServerResponse,
        dependencies=[Depends(RequireScopes(MCP_WRITE))],
    )
    def internal_test_token(
        request: Request,
        server_id: str,
        payload: OAuthTokenRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> McpServerResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            return _AppServices.mcp(app).upsert_token_for_test(
                org_id=identity.org_id,
                user_id=identity.user_id,
                server_id=server_id,
                request=payload,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.post(
        "/v1/skills",
        response_model=SkillResponse,
        dependencies=[Depends(RequireScopes(SKILLS_WRITE))],
    )
    def create_skill(request: Request, payload: CreateSkillRequest) -> SkillResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=payload.org_id, user_id=payload.user_id
        )
        payload = payload.model_copy(
            update={"org_id": identity.org_id, "user_id": identity.user_id}
        )
        try:
            return _AppServices.skills(app).create_skill(payload)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @app.get(
        "/v1/skills",
        response_model=SkillListResponse,
        dependencies=[Depends(RequireScopes(SKILLS_READ))],
    )
    def list_skills(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> SkillListResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        return _AppServices.skills(app).list_skills(
            org_id=identity.org_id, user_id=identity.user_id
        )

    @app.get(
        "/v1/skills/{skill_id}",
        response_model=SkillResponse,
        dependencies=[Depends(RequireScopes(SKILLS_READ))],
    )
    def get_skill(
        request: Request,
        skill_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> SkillResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            return _AppServices.skills(app).get_skill(
                org_id=identity.org_id, user_id=identity.user_id, skill_id=skill_id
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.put(
        "/v1/skills/{skill_id}",
        response_model=SkillResponse,
        dependencies=[Depends(RequireScopes(SKILLS_WRITE))],
    )
    def update_skill(
        request: Request,
        skill_id: str,
        payload: UpdateSkillRequest,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> SkillResponse:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            return _AppServices.skills(app).update_skill(
                org_id=identity.org_id,
                user_id=identity.user_id,
                skill_id=skill_id,
                request=payload,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @app.delete(
        "/v1/skills/{skill_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(RequireScopes(SKILLS_WRITE))],
    )
    def delete_skill(
        request: Request,
        skill_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> Response:
        identity = BackendServiceAuthenticator.scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            deleted = _AppServices.skills(app).delete_skill(
                org_id=identity.org_id,
                user_id=identity.user_id,
                skill_id=skill_id,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        if not deleted:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Skill not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get(
        "/internal/v1/skills/cards",
        response_model=InternalSkillListResponse,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def internal_skill_cards(
        request: Request,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> InternalSkillListResponse:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        return _AppServices.skills(app).list_internal_cards(
            org_id=identity.org_id, user_id=identity.user_id
        )

    @app.get(
        "/internal/v1/skills/{skill_id}",
        response_model=InternalSkillBundle,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def internal_skill_bundle(
        request: Request,
        skill_id: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> InternalSkillBundle:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            return _AppServices.skills(app).get_internal_bundle(
                org_id=identity.org_id,
                user_id=identity.user_id,
                skill_id=skill_id,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.get(
        "/internal/v1/skills/by-name/{name}",
        response_model=InternalSkillBundle,
        dependencies=[Depends(RequireScopes(RUNTIME_USE))],
    )
    def internal_skill_bundle_by_name(
        request: Request,
        name: str,
        org_id: str = Query(..., min_length=1),
        user_id: str = Query(..., min_length=1),
    ) -> InternalSkillBundle:
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=org_id, user_id=user_id
        )
        try:
            return _AppServices.skills(app).get_internal_bundle_by_name(
                org_id=identity.org_id,
                user_id=identity.user_id,
                name=name,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @app.post(
        "/internal/v1/audit/deploy",
        response_model=DeployAuditEventResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(RequireScopes(ADMIN_AUDIT_EXPORT))],
    )
    def internal_audit_deploy(
        request: Request,
        payload: DeployAuditRequest,
    ) -> DeployAuditEventResponse:
        # No query-string identity fallback: deploy audit is service-only and must come
        # from a verified ENTERPRISE_SERVICE_TOKEN caller with x-enterprise-org-id /
        # x-enterprise-user-id headers. Body's tenant_id must match the verified org_id.
        identity = BackendServiceAuthenticator.internal_scoped_identity(
            request, org_id=payload.tenant_id, user_id=payload.approver
        )
        try:
            return _AppServices.deploy_audit(app).record(
                org_id=identity.org_id,
                user_id=identity.user_id,
                request=payload,
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # PR 4.1 — Settings → "You" group: profile + preferences sidecars.
    # The store is unconditional (no secret deps; in-memory in dev, postgres
    # in prod via the same pool the identity store uses). Both routes
    # require RUNTIME_USE — caller is the session user.
    resolved_me_store: MeStore = me_store or InMemoryMeStore()
    app.state.me_store = resolved_me_store
    register_me_profile_routes(
        app,
        me_store=resolved_me_store,
        identity_store=resolved_identity_store,
        # Surfaces the caller's wallet address + chain on /me/profile so the FE
        # renders honest identity instead of the @wallet.invalid placeholder.
        # getattr: degrades to a non-wallet profile if the auth block that sets
        # app.state.siwe_store didn't run. oidc_store additionally feeds the
        # linked_identities list (account-linking PRD FR-L4).
        siwe_store=getattr(app.state, "siwe_store", None),
        oidc_store=getattr(app.state, "oidc_store", None),
    )
    # Account-linking (PRD FR-L1/L2): authenticated wallet + Google links.
    # Degrades to 503 when the auth block that builds the services didn't run.
    register_me_identities_routes(
        app,
        siwe_service=getattr(app.state, "siwe_service", None),
        oidc_service=getattr(app.state, "oidc_service", None),
    )
    register_me_preferences_routes(
        app,
        me_store=resolved_me_store,
        identity_store=resolved_identity_store,
    )
    # PR 8.3 — server-stored avatars. In-memory adapter for dev/tests;
    # production injects the Postgres adapter via ``avatar_store=...``
    # in ``create_app(...)``. Same shape as the MeStore wiring above.
    from backend_app.identity.avatar_store import (
        AvatarStore,
        InMemoryAvatarStore,
    )
    from backend_app.routes.me_avatar import register_me_avatar_routes

    resolved_avatar_store: AvatarStore = (
        avatar_store if avatar_store is not None else InMemoryAvatarStore()
    )
    app.state.avatar_store = resolved_avatar_store
    register_me_avatar_routes(
        app,
        avatar_store=resolved_avatar_store,
        me_store=resolved_me_store,
        identity_store=resolved_identity_store,
    )
    # PR B1 / 8.0.3d — tool-use policy (workspace default + per-user
    # override). Three axes (read/write/destructive), four modes
    # (auto/ask/require/block); the AI backend's
    # ``ToolPermissionChecker`` reads this once per run start.
    from backend_app.policies.store import (
        InMemoryToolUsePolicyStore,
        ToolUsePolicyStore,
    )

    resolved_policy_store: ToolUsePolicyStore = (
        tool_use_policy_store or InMemoryToolUsePolicyStore()  # type: ignore[assignment]
    )
    app.state.tool_use_policy_store = resolved_policy_store
    register_tool_use_policy_routes(
        app,
        policy_store=resolved_policy_store,
        identity_store=resolved_identity_store,
    )
    # PR B4 / 8.0.3e — typed notification preferences + quiet hours.
    # Replaces the JSONB blob in user_preferences.preferences.notifications
    # with two indexed tables. Hydration is materialised at the route
    # layer so the FE always sees the full matrix.
    from backend_app.notifications.store import (
        InMemoryNotificationPrefsStore,
        NotificationPrefsStore,
    )

    resolved_notif_store: NotificationPrefsStore = (
        notification_prefs_store or InMemoryNotificationPrefsStore()  # type: ignore[assignment]
    )
    app.state.notification_prefs_store = resolved_notif_store
    register_notification_preferences_routes(
        app,
        notification_prefs_store=resolved_notif_store,
        identity_store=resolved_identity_store,
    )
    # PR B2 / 8.0.3f — privacy & data settings (workspace default +
    # per-user override). Five toggles + one knob; same scope shape
    # as the tool-use policy.
    from backend_app.privacy.store import (
        InMemoryPrivacySettingsStore,
        PrivacySettingsStore,
    )

    resolved_privacy_store: PrivacySettingsStore = (
        privacy_settings_store or InMemoryPrivacySettingsStore()  # type: ignore[assignment]
    )
    app.state.privacy_settings_store = resolved_privacy_store
    register_privacy_settings_routes(
        app,
        privacy_store=resolved_privacy_store,
        identity_store=resolved_identity_store,
    )
    # Phase 12 P12-A6 — Settings module (per-user notification defaults +
    # workspace notification defaults + webhook security defaults). The
    # store layer reuses ``user_preferences`` (migration 0018) for the
    # user namespace so Phase 2 ``home.activity_window_hours`` + P9-A2
    # ``home.last_visit_iso`` are preserved by deep-merge.
    resolved_settings_store: SettingsStore = settings_store or InMemorySettingsStore()
    app.state.settings_store = resolved_settings_store
    register_settings_routes(
        app,
        service=SettingsService(
            store=resolved_settings_store,
            identity_store=resolved_identity_store,
        ),
    )
    # Phase 2 BYOK — per-user provider API keys, encrypted at rest via
    # the shared TokenVault adapter (same encryption path as MCP OAuth
    # tokens + TOTP secrets). Routes register only when a vault is
    # available (webhooks pattern) — without one there is no safe way
    # to store the secret.
    resolved_provider_keys_store: ProviderApiKeyStore = (
        provider_api_keys_store or InMemoryProviderApiKeyStore()
    )
    app.state.provider_api_keys_store = resolved_provider_keys_store
    # Vault resolution mirrors the MFA/OIDC block but does NOT require a
    # session service: an injected vault (tests) or the deployment
    # default (dev/prod) both work. ``app.state.token_vault`` wins when
    # the session block already resolved one so every consumer shares
    # the same adapter instance.
    provider_keys_vault = (
        getattr(app.state, "token_vault", None)
        or token_vault
        or _default_token_vault(resolved_deployment)
    )
    provider_keys_service: ProviderKeysService | None = None
    if provider_keys_vault is not None:
        provider_keys_service = ProviderKeysService(
            store=resolved_provider_keys_store,
            identity_store=resolved_identity_store,
            token_vault=provider_keys_vault,
        )
        app.state.provider_keys_service = provider_keys_service
        register_provider_keys_routes(app, service=provider_keys_service)
    # PR 8.0.5 — single aggregate runtime-policies route consumed by
    # ai-backend at run start. Composes the same two stores above into
    # one wire shape so each run pays one HTTP round-trip instead of
    # two. Read-only; no audit row beyond the per-fetch access log.
    # Phase 2 BYOK adds the optional decrypted ``provider_keys`` section
    # to the same snapshot (service-token-only lane).
    register_runtime_policies_routes(
        app,
        tool_use_store=resolved_policy_store,
        privacy_store=resolved_privacy_store,
        provider_keys_service=provider_keys_service,
    )
    # PR B3 / 8.0.3g — personal API keys (atlas_pk_… bearer for CI /
    # scripts). Plaintext is shown ONCE on creation; the server stores
    # only the HMAC hash under a deployment pepper. The bearer-auth
    # path lives in backend_app/api_keys/auth.py and is consumed by
    # the auth middleware (out of scope for this PR — the storage and
    # the user-facing CRUD land here).
    import os as _os
    from backend_app.api_keys.store import (
        ApiKeyStore,
        InMemoryApiKeyStore,
    )
    from backend_app.api_keys.auth import ApiKeyHasher

    resolved_api_key_store: ApiKeyStore = (
        api_key_store or InMemoryApiKeyStore()  # type: ignore[assignment]
    )
    pepper = api_key_pepper or _os.environ.get("BACKEND_API_KEY_PEPPER", "").encode(
        "utf-8"
    )
    if len(pepper) < 16:
        # Dev-only fallback so `make dev` doesn't refuse to boot when
        # the operator hasn't provisioned the pepper yet. Production
        # MUST set BACKEND_API_KEY_PEPPER (≥ 16 bytes) — without it,
        # rotating the dev fallback invalidates every key, which is
        # exactly the emergency lever we want.
        pepper = b"dev-only-pepper-NOT-FOR-PROD!"
    resolved_api_key_hasher = ApiKeyHasher(server_pepper=pepper)
    app.state.api_key_store = resolved_api_key_store
    app.state.api_key_hasher = resolved_api_key_hasher
    register_api_key_routes(
        app,
        api_key_store=resolved_api_key_store,
        api_key_hasher=resolved_api_key_hasher,
        identity_store=resolved_identity_store,
    )

    # PR 4.2 — Settings → "Workspace" group: workspace branding, members,
    # invitations, billing. The invitations service composes the existing
    # IdentityStore with an InvitationStore (in-memory in dev; the postgres
    # adapter ships alongside this PR). Routes mount unconditionally
    # because they have no secret deps; admin gating is RBAC at the
    # dependency level.
    resolved_invitation_store: InvitationStore = (
        invitation_store or InMemoryInvitationStore()
    )
    app.state.invitation_store = resolved_invitation_store
    resolved_invitations_service = invitations_service or InvitationsService(
        identity_store=resolved_identity_store,
        invitation_store=resolved_invitation_store,
    )
    app.state.invitations_service = resolved_invitations_service
    register_workspace_routes(app)
    register_members_routes(app)
    register_invitation_routes(app)
    register_billing_routes(app)

    register_audit_export_routes(app)
    register_audit_list_routes(app)
    register_me_routes(app)
    register_siem_admin_routes(app)
    register_health_routes(app)
    # Dev IdP (W0.1) — env-gated; no-op in production.
    register_dev_idp_routes(app)
    # Phase 9 — Home destination aggregator (morning-briefing model).
    # Tenant-first, owner-only. Sections read inbox/todos/projects
    # stores off ``app.state`` lazily, so the registration order here
    # does not need to follow the per-section store wiring below.
    register_home_routes(
        app,
        me_store=resolved_me_store,
        identity_store=resolved_identity_store,
    )
    # ``GET /v1/home/stream`` — Phase 9 §3.6 LiveActivityRail feed.
    # Sets ``app.state.home_activity_bus`` (in-memory, dev-tier) which
    # the aggregator also reads off the same slot.
    register_home_sse_routes(app)

    # Phase 3 — Todos destination. Owner-only writes; project-member
    # reads; admin compliance reads — all enforced in ``TodosService``.
    # The store defaults to in-memory; production deploys inject the
    # Postgres adapter via ``todos_store=`` (out of scope for P3-A1
    # but the wiring shape is stable).
    resolved_todos_store: TodosStore = todos_store or InMemoryTodosStore()  # type: ignore[assignment]
    app.state.todos_store = resolved_todos_store
    todos_service = TodosService(
        store=resolved_todos_store,
        identity_store=resolved_identity_store,
    )
    app.state.todos_service = todos_service
    register_todos_routes(app, service=todos_service)

    # Phase 4 — Inbox destination. Recipient-only writes; project-member
    # reads; admin compliance reads — all enforced in ``InboxService``.
    # The store defaults to in-memory; production deploys inject the
    # Postgres adapter via ``inbox_store=`` (out of scope for P4-A1
    # but the wiring shape is stable). Body markdown is split into
    # ``inbox_bodies`` so list queries never pay for body bytes.
    resolved_inbox_store: InboxStore = inbox_store or InMemoryInboxStore()  # type: ignore[assignment]
    app.state.inbox_store = resolved_inbox_store
    # SSE routes register first so the activity bus is on
    # ``app.state.inbox_activity_bus`` before we construct the service —
    # the service captures the bus by reference so PATCH / bulk / producer
    # publish (``item_added`` / ``item_updated``) flow to subscribers.
    register_inbox_sse_routes(app)
    inbox_service = InboxService(
        store=resolved_inbox_store,
        identity_store=resolved_identity_store,
        activity_bus=app.state.inbox_activity_bus,
    )
    app.state.inbox_service = inbox_service
    register_inbox_routes(app, service=inbox_service)
    # Phase 4 P4-A2 — internal producer endpoint. Resolves the canonical
    # ``InboxService`` off ``app.state.inbox_service``, so the producer
    # surface shares ACL + audit + SSE publish with the PATCH path.
    register_inbox_internal_routes(app)

    # Phase 5 — Routines destination. Owner-only writes; project-member
    # reads; admin compliance reads — all enforced in ``RoutinesService``.
    # Per-USER quota (100 active) enforced at create + state transitions
    # per cross-audit §9.7 Q8. The store defaults to in-memory;
    # production deploys inject the Postgres adapter via
    # ``routines_store=`` (out of scope for P5-A1 but the wiring shape
    # is stable). Scheduler + webhook ingest + permission intersection
    # land in P5-A2/A3/A4.
    resolved_routines_store: RoutinesStore = routines_store or InMemoryRoutinesStore()  # type: ignore[assignment]
    app.state.routines_store = resolved_routines_store
    routines_service = RoutinesService(
        store=resolved_routines_store,
        identity_store=resolved_identity_store,
    )
    app.state.routines_service = routines_service
    register_routines_routes(app, service=routines_service)

    # Phase 11 — Connectors destination. The destination is a
    # denormalized READ MODEL over the existing MCP registration +
    # token vault path (connectors-prd §3.2). Writes flow through the
    # existing ``McpRegistryService`` / ``TokenVault``; the
    # ``ConnectorsService`` is the substitution point that emits
    # destination-level audit rows + projects the consumer view. SSE
    # registers first so the activity bus is on
    # ``app.state.connector_activity_bus`` before the service is
    # constructed. The catalog (Atlas-vetted SaaS slugs) is loaded once
    # from the package-local ``catalog.yaml``; a soft-fail wraps the
    # load so a missing file degrades to an empty Available tab rather
    # than a boot crash.
    resolved_connectors_store: ConnectorsStore = (
        connectors_store or InMemoryConnectorsStore()  # type: ignore[assignment]
    )
    app.state.connectors_store = resolved_connectors_store
    try:
        connector_catalog = load_catalog()
    except Exception:
        connector_catalog = ()
    register_connector_sse_routes(app)
    connectors_service = ConnectorsService(
        store=resolved_connectors_store,
        catalog=connector_catalog,
    )
    app.state.connectors_service = connectors_service

    # Phase 11 P11-A3 — Connectors destination webhook manager.
    # Webhooks are tenant-admin OR routine-owner per connectors-prd
    # §6.1. The service composes the existing TokenVault adapter
    # (DRY: same encryption-at-rest path as MCP OAuth tokens + the
    # Phase 5 inbound routine webhook secret). The rotation worker
    # lifecycle is NOT started here — it's a separate process in
    # production; tests + dev surface the ``WebhookRotationWorker``
    # off ``app.state.webhooks_service`` for explicit tick() calls.
    # HMAC algorithm + header names live as constants in
    # ``backend_app.webhooks.signer`` (single source of truth).
    #
    # MUST register BEFORE ``register_connector_routes`` so the literal
    # path ``/v1/connectors/webhooks`` wins FastAPI's registration-order
    # matcher over the path-param route ``/v1/connectors/{connector_id}``.
    resolved_webhooks_store: WebhooksStore = (
        webhooks_store or InMemoryWebhooksStore()  # type: ignore[assignment]
    )
    app.state.webhooks_store = resolved_webhooks_store
    webhooks_vault = getattr(app.state, "token_vault", None)
    if webhooks_vault is not None:
        webhooks_service = WebhooksService(
            store=resolved_webhooks_store,
            token_vault=webhooks_vault,
        )
        app.state.webhooks_service = webhooks_service
        register_webhook_routes(app, service=webhooks_service)

    register_connector_routes(app, service=connectors_service)

    # AC9 — Desktop MCP connector OAuth. The generic desktop coordinator is the
    # missing bridge between Electron's system-browser / loopback delivery and
    # the existing MCP OAuth authority (state + PKCE + TokenVault). It is the
    # *per-MCP-server* auth layer, not a second credential path: it drives the
    # same ``McpRegistryService`` the web connectors use, adding only the
    # desktop redirect-reconstruction + callback-owner-match invariants.
    #
    # The profile overlay (``desktop_profiles.yaml``) is loaded soft: a missing
    # or invalid file degrades the desktop connector surface to unavailable
    # rather than crashing boot (mirrors the marketing-catalog soft-fail above).
    # Preview connectors (Google/Microsoft) stay disabled unless the deployment
    # explicitly sets ``DESKTOP_CONNECTORS_ALLOW_PREVIEW=true``; even then the
    # tenant-template profiles fail closed with ``admin_setup_required``.
    desktop_preview_enabled = (
        os.environ.get("DESKTOP_CONNECTORS_ALLOW_PREVIEW", "").strip().lower() == "true"
    )
    try:
        desktop_profile_catalog = DesktopProfileCatalog.load()
    except Exception:  # noqa: BLE001 — soft-fail; desktop surface degrades
        desktop_profile_catalog = None
    if desktop_profile_catalog is not None:
        desktop_oauth_coordinator = DesktopMcpOAuthCoordinator(
            mcp_service=_AppServices.mcp(app),
            catalog=desktop_profile_catalog,
            preview_enabled=desktop_preview_enabled,
        )
        app.state.desktop_connector_coordinator = desktop_oauth_coordinator
        register_desktop_connector_routes(
            app,
            coordinator=desktop_oauth_coordinator,
            catalog=desktop_profile_catalog,
            preview_enabled=desktop_preview_enabled,
        )

    # Phase 6 — Projects destination. The canonical project-scoped ACL
    # predicate lives in ``backend_app.projects.acl`` and is consumed
    # by every destination carrying ``project_id`` (Todos / Inbox /
    # Routines / Library / Memory / Chats) per cross-audit §1.3 master
    # rule. Owner-only writes; project-member reads; admin compliance
    # reads — 404-not-403 for non-readers. Ownership transfer is atomic
    # (PARTIAL UNIQUE on owner-row preserved across the demote-promote
    # swap). Admin force-transfer ships per projects-prd §12 Q1.
    resolved_projects_store: ProjectsStore = projects_store or InMemoryProjectsStore()  # type: ignore[assignment]
    app.state.projects_store = resolved_projects_store
    projects_service = ProjectsService(
        store=resolved_projects_store,
        identity_store=resolved_identity_store,
    )
    app.state.projects_service = projects_service

    # Phase 6.5 §3 — Liveness orchestrator. Single source of truth for
    # "is anything running for project X?". Read-only, 2s TTL, partial-
    # failure-tolerant. Consumed by archive (§6) + routine pre-fire
    # (§3.5) + connector revoke (§3.5) + template fork (§7.3). When
    # callers don't inject a service we build the default — wired against
    # the in-process routines + inbox stores, with the ai-backend client
    # pointed at the ai-backend URL (defaults to the local dev port).
    resolved_liveness_service: LivenessService | None = liveness_service
    if resolved_liveness_service is None:
        ai_backend_url = os.environ.get(
            "AI_BACKEND_URL", "http://127.0.0.1:8000"
        ).rstrip("/")
        service_token = os.environ.get("ENTERPRISE_SERVICE_TOKEN", "").strip()
        if service_token:
            ai_client = AiBackendLivenessClient(
                base_url=ai_backend_url,
                service_token=service_token,
            )
            resolved_liveness_service = LivenessService(
                ai_backend_client=ai_client,
                routines_reader=RoutinesLivenessReader(
                    routines_store=resolved_routines_store
                ),
                inbox_reader=InboxLivenessReader(inbox_store=resolved_inbox_store),
            )
    app.state.liveness_service = resolved_liveness_service
    if resolved_liveness_service is not None:
        register_liveness_routes(app, service=resolved_liveness_service)
    register_projects_routes(
        app,
        service=projects_service,
        liveness_service=resolved_liveness_service,
    )

    # Phase 6.5 §7 — Project templates. Tenant-wide read, owner-writes,
    # caller-owns-fork. Atomic fork via the shared transaction context.
    resolved_templates_store: ProjectTemplatesStore = (
        project_templates_store or InMemoryProjectTemplatesStore()  # type: ignore[assignment]
    )
    app.state.project_templates_store = resolved_templates_store
    register_template_routes(
        app,
        projects_service=projects_service,
        templates_store=resolved_templates_store,
    )

    # P6.5-A2 — bridge the Projects destination's
    # ``default_connector_allowlist`` into the Routines service so
    # routine create can inherit it (PRD §5.4). We install AFTER both
    # services exist; the bridge calls ``projects_service.get_project``
    # so the 404-not-403 ACL gate still applies (cross-tenant /
    # forbidden / missing projects collapse to ``None`` — the routine
    # service treats that as "no inheritance" and falls through to the
    # pre-§5.4 behavior).
    from backend_app.routines.service import ProjectAllowlistLookup

    class _RoutineProjectAllowlistBridge:
        """In-process bridge from RoutinesService → ProjectsService.

        Never raises — ACL denials and missing projects collapse to
        ``None``, matching the routine-service contract that bad
        project ids must never block create.
        """

        def __init__(self, projects: ProjectsService) -> None:
            self._projects = projects

        def fetch_connector_allowlist(
            self,
            *,
            tenant_id: str,
            caller_user_id: str,
            project_id: str,
        ) -> tuple[str, ...] | None:
            """Return the project's allowlist via the canonical ACL gate."""
            try:
                record, _, _, _ = self._projects.get_project(
                    tenant_id=tenant_id,
                    caller_user_id=caller_user_id,
                    # Owner / member / admin all satisfy read; the
                    # service-layer ACL is the source of truth. We pass
                    # an empty role tuple — the projects service checks
                    # owner / membership first; admin roles never reach
                    # this bridge because the routine create is for the
                    # caller's own tenant scope.
                    caller_roles=(),
                    project_id=project_id,
                )
            except Exception:
                # ProjectNotFound / ProjectForbidden / unexpected — fall
                # through to "no inheritance". The PRD's hard rule is
                # that the inheritance hook is best-effort; the caller's
                # routine still lands.
                return None
            return record.default_connector_allowlist

    routines_service._project_allowlist_lookup = _RoutineProjectAllowlistBridge(  # type: ignore[attr-defined]
        projects_service
    )
    # Quiet the unused-import warning when the Protocol is only used
    # for documentation purposes above.
    _ = ProjectAllowlistLookup

    # =====================================================================
    # Phase 7 P7-A1 — Library destination (metadata + CRUD).
    # =====================================================================
    resolved_library_store: LibraryStore = library_store or InMemoryLibraryStore()  # type: ignore[assignment]
    app.state.library_store = resolved_library_store
    library_service = LibraryService(
        store=resolved_library_store,
        membership_port=projects_service._membership_port,  # noqa: SLF001 — canonical port reuse
    )
    app.state.library_service = library_service

    # P7.5-A4 — hybrid search. Wired with the in-memory BM25 index and
    # the no-op embeddings + rerank clients; the production composer
    # injects HTTP-backed clients onto ai-backend's
    # ``/internal/v1/llm/embed`` + ``/internal/v1/llm/rerank`` once
    # P7.5-A1 lands them.
    # Search routes MUST register before the catch-all CRUD route
    # ``/v1/library/{item_id}`` — otherwise ``/v1/library/search`` is
    # captured as an item_id lookup. FastAPI matches routes in the
    # order they were declared.
    library_search_engine = SearchEngine(
        store=resolved_library_store,
        index=InMemoryLibrarySearchIndex(store=resolved_library_store),
        membership_port=projects_service._membership_port,  # noqa: SLF001
    )
    app.state.library_search_engine = library_search_engine
    register_library_search_routes(app, engine=library_search_engine)

    register_library_routes(app, service=library_service)

    # =====================================================================
    # Phase 12 P12-A3 — Memory destination (CRUD + proposals + search + SSE).
    #
    # Memory embeddings RIDE the existing Library queue:
    # ``library_index_jobs`` rows with ``target_kind="memory"``. There is
    # intentionally NO ``memory_embeddings`` table — sub-PRD §5.1 DRY.
    # =====================================================================
    resolved_index_jobs_store: LibraryIndexJobsStore = (
        library_index_jobs_store or InMemoryLibraryIndexJobsStore()
    )
    app.state.library_index_jobs_store = resolved_index_jobs_store
    resolved_memory_store: MemoryStore = memory_store or InMemoryMemoryStore()  # type: ignore[assignment]
    app.state.memory_store = resolved_memory_store
    # SSE bus must be on app.state BEFORE the service is constructed so
    # the service can publish through it.
    register_memory_sse_routes(app)
    memory_service = MemoryService(
        store=resolved_memory_store,
        membership_port=projects_service._membership_port,  # noqa: SLF001 — canonical port reuse
        activity_bus=app.state.memory_activity_bus,
        indexer=MemoryIndexer(jobs_store=resolved_index_jobs_store),
    )
    app.state.memory_service = memory_service
    # Hybrid search engine — in-memory BM25 + no-op embeddings (the
    # production composer wires a real ``EmbeddingsClientPort`` that
    # calls ai-backend ``/internal/v1/llm/embed`` with the
    # ``Purpose.MEMORY_RETRIEVAL`` tag).
    memory_search_engine = MemorySearchEngine(
        store=resolved_memory_store,
        index=InMemoryMemorySearchIndex(store=resolved_memory_store),
        membership_port=projects_service._membership_port,  # noqa: SLF001
    )
    app.state.memory_search_engine = memory_search_engine
    register_memory_routes(
        app, service=memory_service, search_engine=memory_search_engine
    )

    # =====================================================================
    # Phase 8 P8-A1 — Agents destination CRUD.
    # =====================================================================
    resolved_agents_store: AgentsStore = agents_store or InMemoryAgentsStore()  # type: ignore[assignment]
    app.state.agents_store = resolved_agents_store
    agents_service = AgentsService(
        store=resolved_agents_store,
        identity_store=resolved_identity_store,
    )
    app.state.agents_service = agents_service
    register_agents_routes(app, service=agents_service)

    # =====================================================================
    # Phase 10 P10-A2 — Tools destination (catalog CRUD + ACL + audit).
    # =====================================================================
    # The canonical project-membership port is shared with Library /
    # Projects / Routines / Inbox so the ACL gate stays single-sourced.
    # P10-A3 lands the code-routine sandbox executor (test-call returns
    # 501 until then); P10-A4 lands the facade pass-through.
    resolved_tools_store: ToolsStore = tools_store or InMemoryToolsStore()  # type: ignore[assignment]
    app.state.tools_store = resolved_tools_store
    # SSE routes register first so the activity bus is on
    # ``app.state.tools_activity_bus`` before service is constructed.
    register_tool_sse_routes(app)
    tools_service = ToolsService(
        store=resolved_tools_store,
        membership_port=projects_service._membership_port,  # noqa: SLF001 — canonical port reuse
    )
    app.state.tools_service = tools_service
    register_tool_routes(app, service=tools_service)
    register_tool_internal_routes(app, service=tools_service)

    # =====================================================================
    # Phase 12 P12-A4 — ⌘K palette destination.
    # =====================================================================
    # One denormalized ``palette_index`` shared across destinations,
    # written through the canonical :class:`PaletteRefreshDispatcher`
    # (refresh.py). v1 wires the in-memory store + dispatcher; the
    # destinations consume the dispatcher reference via
    # ``app.state.palette_dispatcher`` so their service-layer write
    # paths can broadcast inserts / updates / soft-deletes without
    # touching the palette store directly.
    resolved_palette_store: PaletteStorePort = palette_store or InMemoryPaletteStore()
    app.state.palette_store = resolved_palette_store
    palette_dispatcher = PaletteRefreshDispatcher(store=resolved_palette_store)
    app.state.palette_dispatcher = palette_dispatcher
    palette_service = PaletteService(
        store=resolved_palette_store,
        membership_port=projects_service._membership_port,  # noqa: SLF001 — canonical port reuse
    )
    app.state.palette_service = palette_service
    register_palette_routes(app, service=palette_service)

    # Phase 7A — tier-2 adapter registry. Source bytes go through a
    # ``SourceStorage`` port (filesystem in dev, S3 injectable in prod).
    from backend_app.adapter_registry import (
        AdapterRegistryService,
        InMemoryAdapterRegistryStore,
        LocalFilesystemSourceStorage,
        SourceStorage,
        register_adapter_registry_routes,
    )
    from backend_app.adapter_registry.store import AdapterRegistryStore

    if adapter_registry_service is not None:
        resolved_registry_service = adapter_registry_service
    else:
        resolved_registry_store: AdapterRegistryStore = (
            adapter_registry_store
            if adapter_registry_store is not None
            else InMemoryAdapterRegistryStore()
        )  # type: ignore[assignment]
        resolved_source_storage: SourceStorage = (
            adapter_source_storage
            if adapter_source_storage is not None
            else LocalFilesystemSourceStorage(
                os.environ.get(
                    "ADAPTER_REGISTRY_DATA_DIR",
                    "/tmp/atlas-adapter-registry",
                )
            )
        )  # type: ignore[assignment]
        resolved_registry_service = AdapterRegistryService(
            store=resolved_registry_store,
            source_storage=resolved_source_storage,
        )
    register_adapter_registry_routes(
        app,
        service=resolved_registry_service,  # type: ignore[arg-type]
    )

    # P7-A2 — Library blob storage (signed-URL upload/download). Bytes
    # never proxy through the API; the route layer only ever returns
    # signed URLs that the caller redeems directly against the storage
    # adapter. Dev uses ``LocalDiskBlobStore`` (HMAC-signed localhost
    # URLs); production injects ``S3BlobStore`` via the keyword. Row
    # metadata uses the in-memory adapter here; P7-A1 swaps in the
    # Postgres-backed adapter at merge.
    from backend_app.library.blob_store import LocalDiskBlobStore
    from backend_app.library.upload_routes import (
        InMemoryLibraryRowStore,
        register_library_blob_routes,
    )

    resolved_blob_store = library_blob_store or LocalDiskBlobStore(
        data_dir=os.environ.get(
            "LIBRARY_BLOB_DATA_DIR",
            "/tmp/atlas-library-blobs",
        ),
        base_url=os.environ.get(
            "LIBRARY_BLOB_BASE_URL",
            "http://localhost:8100",
        ),
    )
    resolved_row_store = library_row_store or InMemoryLibraryRowStore()
    register_library_blob_routes(
        app,
        blob_store=resolved_blob_store,  # type: ignore[arg-type]
        row_store=resolved_row_store,  # type: ignore[arg-type]
    )

    # Phase 12 — Team destination (read projection over existing
    # ``users`` + ``organization_members`` + ``role_assignments``).
    # Composes the canonical IdentityStore + InvitationsService; uses
    # the in-memory presence KV (sub-PRD §5.2 — production injects a
    # Redis-backed adapter via the same Protocol). Asset counts
    # delegate to the agents + projects stores wired above.
    #
    # SSE registers first so ``app.state.team_activity_bus`` is
    # available when the routes publish on PATCH role / POST offboard.
    register_team_sse_routes(app)
    team_store = InMemoryTeamStore(
        identity_store=resolved_identity_store,
        asset_counts=StoreBackedAssetCounts(
            agents_store=resolved_agents_store,
            projects_store=resolved_projects_store,
        ),
    )
    app.state.team_store = team_store
    team_service = TeamService(
        store=team_store,
        identity_store=resolved_identity_store,
        invitations_service=resolved_invitations_service,
        projects_service=projects_service,
    )
    app.state.team_service = team_service
    register_team_routes(app, service=team_service)

    return app


def __getattr__(name: str) -> object:
    """Lazily build the module-level ``app`` for ``uvicorn backend_app.app:app``.

    PEP 562 module ``__getattr__``: the default app is only constructed when
    the ``app`` attribute is actually requested (uvicorn does this once).
    Importing :func:`create_app` from other composition roots (e.g.
    ``backend_app.desktop_app``) must not boot the default app as a side
    effect — the default wiring enforces SaaS-production invariants (OTLP
    endpoint, KMS vault) that other profiles intentionally do not carry.
    """

    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

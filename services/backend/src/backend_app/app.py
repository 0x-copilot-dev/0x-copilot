"""FastAPI application for core product backend APIs."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, Response, status

from backend_app.auth import BackendServiceAuthenticator
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
    InternalMcpServerListResponse,
    InternalSkillBundle,
    InternalSkillListResponse,
    McpAuthCallbackRequest,
    McpAuthStartRequest,
    McpAuthStartResponse,
    McpServerListResponse,
    McpServerResponse,
    OAuthTokenRequest,
    SkillListResponse,
    SkillResponse,
    UpdateMcpServerRequest,
    UpdateSkillRequest,
)
from backend_app.identity import (
    BootstrapAdminService,
    IdentityStore,
    InMemoryIdentityStore,
    InMemoryLockoutStore,
    InMemoryMfaStore,
    InMemoryOidcStore,
    InMemoryPasswordStore,
    InMemorySamlStore,
    InMemorySessionStore,
    LockoutService,
    LockoutStore,
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
    SessionAuthSecretMissing,
    SessionService,
)
from backend_app.identity.session_sweeper import SessionSweeper
from backend_app.observability import (
    RequestContextMiddleware,
    TelemetryBootstrap,
    configure_logging,
    emit_access_log,
)
from backend_app.routes.audit_export import register_audit_export_routes
from backend_app.routes.health import register_health_routes
from backend_app.routes.lockouts import register_lockout_routes
from backend_app.routes.mfa import register_mfa_routes
from backend_app.routes.oidc import register_oidc_routes
from backend_app.routes.passwords import register_password_routes
from backend_app.routes.saml import register_saml_routes
from backend_app.routes.sessions import register_session_routes
from backend_app.token_vault import TokenVault, TokenVaultFactory
from backend_app.service import (
    DeployAuditService,
    McpRegistryService,
    SkillRegistryService,
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
) -> FastAPI:
    if configure_logging_on_create:
        configure_logging()
    if configure_telemetry_on_create:
        TelemetryBootstrap.configure()
    resolved_deployment = deployment or resolve_or_exit()
    log_profile(resolved_deployment)
    app = FastAPI(title="Enterprise Search Backend", lifespan=_lifespan)
    app.add_middleware(RequestContextMiddleware, access_log_emitter=emit_access_log)
    if configure_telemetry_on_create:
        TelemetryBootstrap.instrument_fastapi(app)
    app.state.mcp_service = service or McpRegistryService()
    app.state.skill_service = skill_service or SkillRegistryService()
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
            resolved_oidc_service = oidc_service or OidcService(
                identity_store=resolved_identity_store,
                oidc_store=resolved_oidc_store,
                sessions=resolved_session_service,
                token_vault=resolved_token_vault,
                lockout=resolved_lockout_service,
                mfa=resolved_mfa_service,
            )
            app.state.oidc_service = resolved_oidc_service
            register_oidc_routes(
                app,
                service=resolved_oidc_service,
                identity_store=resolved_identity_store,
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

    @app.get("/v1/health")
    def health() -> dict[str, object]:
        return {
            "service": "backend",
            "deployment_profile": resolved_deployment.name,
            "feature_toggles_hash": resolved_deployment.toggles_hash(),
        }

    @app.post("/v1/mcp/servers", response_model=McpServerResponse)
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

    @app.get("/v1/mcp/servers", response_model=McpServerListResponse)
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

    @app.delete("/v1/mcp/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
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

    @app.patch("/v1/mcp/servers/{server_id}", response_model=McpServerResponse)
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
        "/v1/mcp/servers/{server_id}/auth/start", response_model=McpAuthStartResponse
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

    @app.post("/v1/mcp/servers/{server_id}/auth/skip", response_model=McpServerResponse)
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

    @app.get("/v1/mcp/oauth/callback", response_model=McpServerResponse)
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

    @app.get("/internal/v1/mcp/cards", response_model=InternalMcpServerListResponse)
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

    @app.post(
        "/internal/v1/mcp/servers/{server_id}/auth/start",
        response_model=McpAuthStartResponse,
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

    @app.post("/v1/skills", response_model=SkillResponse)
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

    @app.get("/v1/skills", response_model=SkillListResponse)
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

    @app.get("/v1/skills/{skill_id}", response_model=SkillResponse)
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

    @app.put("/v1/skills/{skill_id}", response_model=SkillResponse)
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

    @app.delete("/v1/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
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

    @app.get("/internal/v1/skills/cards", response_model=InternalSkillListResponse)
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

    @app.get("/internal/v1/skills/{skill_id}", response_model=InternalSkillBundle)
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

    @app.get("/internal/v1/skills/by-name/{name}", response_model=InternalSkillBundle)
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

    register_audit_export_routes(app)
    register_health_routes(app)

    return app


app = create_app()

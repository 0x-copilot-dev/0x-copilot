"""Product-owned MCP registry and OAuth orchestration service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
import threading
from collections import Counter, OrderedDict
from dataclasses import dataclass, field

import yaml
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit

from backend_app.identity._pkce import compute_challenge, generate_verifier

from backend_app.contracts import (
    AuditEventRecord,
    CreateMcpServerRequest,
    DeployAuditEventRecord,
    DeployAuditEventResponse,
    DeployAuditRequest,
    CreateSkillRequest,
    InstallMcpServerRequest,
    InternalMcpAuthRequest,
    InternalMcpClientSession,
    InternalMcpRpcRequest,
    InternalMcpRpcResponse,
    InternalMcpSessionReleaseResponse,
    InternalMcpServerCard,
    InternalMcpServerListResponse,
    InternalSkillBundle,
    InternalSkillCard,
    InternalSkillListResponse,
    McpAuthCallbackRequest,
    McpAuthMode,
    McpAuthSessionRecord,
    McpAuthStartRequest,
    McpAuthStartResponse,
    McpAuthState,
    McpCatalogEntryResponse,
    McpCatalogResponse,
    McpOAuthClientConfig,
    McpOAuthClientRequest,
    McpServerHealth,
    McpServerListResponse,
    McpServerRecord,
    McpRevisionReason,
    McpServerResponse,
    OAuthTokenRequest,
    SkillAuditEventRecord,
    SkillListResponse,
    SkillManifestFields,
    SkillRecord,
    SkillResponse,
    SkillSourceType,
    ToolKind,
    ToolListEntry,
    ToolListResponse,
    UpdateMcpServerRequest,
    UpdateSkillRequest,
    TokenEnvelope,
    Validators,
    _Fields,
)
from backend_app.connectors.store import ConnectorAccessMode
from backend_app.mcp_catalog import DEFAULT_CATALOG, CatalogEntry, catalog_by_slug
from backend_app.mcp_oauth import RemoteMcpOAuthClient
from backend_app.mcp_revisions import McpRevisionAuthority
from backend_app.mcp_session_pool import (
    McpSessionDispatchFence,
    McpSessionLease,
    McpSessionPool,
    McpSessionPoolConfig,
    McpSessionPoolOutcome,
    McpSessionPoolRejected,
    VerifiedMcpSessionScopeKey,
)
from backend_app.mcp_transport import McpHttpTransportFactory, McpRemoteSessionTransport
from backend_app.prompts.preloaded_skills import PRELOADED_SKILL_MARKDOWNS
from backend_app.store import (
    InMemoryDeployAuditStore,
    InMemoryMcpStore,
    InMemorySkillStore,
    PostgresConnectionPool,
    PostgresMcpStore,
    PostgresSkillStore,
)
from backend_app.token_vault import TokenVault, TokenVaultFactory

# Suggestion appetite, mirrored from ``routes.me_preferences``. Compared as
# plain strings so this module does not import a routes module — the values
# are the wire contract either way, and a typo is caught by the test that
# asserts both sides agree.
_SUGGESTIONS_OFF = "off"
_SUGGESTIONS_ALWAYS = "always"
_BACKEND_COMPATIBILITY_PARTITION = "backend-registry-compat-v1"
_logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _LeaseOwner:
    org_id: str
    user_id: str
    server_id: str
    scope: VerifiedMcpSessionScopeKey
    operation_lock: threading.RLock = field(default_factory=threading.RLock)


@dataclass(slots=True)
class _DescriptorCycle:
    expected_cursor: str | None = None
    complete: bool = False
    count: int = 0
    page_count: int = 0
    descriptor_count: int = 0
    canonical_bytes: int = 0
    requested_cursors: set[str | None] = field(default_factory=set)
    digest: object = field(default_factory=hashlib.sha256)


@dataclass(slots=True)
class _DescriptorObservation:
    tools: _DescriptorCycle = field(default_factory=_DescriptorCycle)
    resources: _DescriptorCycle = field(default_factory=_DescriptorCycle)


_MAX_DESCRIPTOR_PAGES = 100
_MAX_DESCRIPTOR_COUNT = 10_000
_MAX_DESCRIPTOR_BYTES = 4 * 1024 * 1024


def _is_unique_violation(ex: Exception) -> bool:
    """True iff ``ex`` is a database unique-constraint violation.

    Detected via SQLSTATE 23505 (``sqlstate`` attribute on psycopg errors)
    rather than an isinstance check, so the service layer stays free of a
    hard driver import and the in-memory store (which never raises this)
    is unaffected. Walks ``__cause__`` because store adapters may re-raise
    with context.
    """
    seen: set[int] = set()
    current: BaseException | None = ex
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "sqlstate", None) == "23505":
            return True
        current = current.__cause__ or current.__context__
    return False


def _catalog_entry_response(entry: CatalogEntry) -> McpCatalogEntryResponse:
    """Project a code-side ``CatalogEntry`` to its wire shape."""

    return McpCatalogEntryResponse(
        slug=entry.slug,
        display_name=entry.display_name,
        url=entry.url,
        transport=entry.transport,
        auth_mode=entry.auth_mode,
        description=entry.description,
        logo_url=entry.logo_url,
        brand_color=entry.brand_color,
        scopes_summary=entry.scopes_summary,
        default_scopes=entry.default_scopes,
        requires_pre_registered_client=entry.requires_pre_registered_client,
        verified=entry.verified,
        discoverable=entry.discoverable,
    )


def _catalog_by_slug() -> dict[str, CatalogEntry]:
    return catalog_by_slug()


class OAuthTokenExchanger(Protocol):
    """Exchange an OAuth authorization code for backend-held connector tokens."""

    def exchange_code(
        self,
        *,
        record: McpServerRecord,
        session: McpAuthSessionRecord,
        code: str,
        token_vault: TokenVault,
    ) -> OAuthTokenRequest:
        """Return tokens for a verified OAuth callback."""


class OAuthDiscoveryClient(Protocol):
    """Prepare OAuth metadata and authorization URLs for a remote MCP server."""

    def authorization(
        self,
        *,
        record: McpServerRecord,
        redirect_uri: str,
        state: str,
        code_challenge: str,
        token_vault: TokenVault,
    ):
        """Return an authorization URL plus updated discovery metadata."""

    def refresh_token(
        self,
        *,
        record: McpServerRecord,
        refresh_token: str,
        token_vault: TokenVault,
    ) -> OAuthTokenRequest:
        """Refresh an expiring access token."""


class HttpOAuthTokenExchanger(RemoteMcpOAuthClient):
    """Backward-compatible name for the remote MCP OAuth client."""

    def exchange_code(
        self,
        *,
        record: McpServerRecord,
        session: McpAuthSessionRecord,
        code: str,
        token_vault: TokenVault,
    ) -> OAuthTokenRequest:
        return super().exchange_code(
            record=record,
            session=session,
            code=code,
            token_vault=token_vault,
        )


# Resolver port: given a registered MCP server record, return the joined
# connector row's durable access mode, or ``None`` when no connector row
# joins the server (an unprojected server is not a user-set ``off`` — the
# gate must SKIP, not deny). Wired at app composition to the connectors
# store's ``get_by_owner_and_slug`` (see ``app.py``); ``None`` (default)
# leaves every server ungated, which is the correct behaviour for the
# tests/dev wiring that has no connectors store.
ConnectorAccessResolver = Callable[[McpServerRecord], ConnectorAccessMode | None]


class ConnectorAccessDenied(Exception):
    """Raised by the ``proxy_internal_rpc`` access-mode gate (PRD-06 D3c).

    ``reason`` is the stable wire code the route layer maps to a ``403``:

    * ``connector_access_off``       — the connector is ``off``; no reads, no
      acts. Raised BEFORE the vault token is decrypted.
    * ``connector_access_read_only`` — the connector is ``read`` and the
      target tool is not read-only (its ``annotations.readOnlyHint`` is
      absent or false — fail-closed).
    """

    OFF = "connector_access_off"
    READ_ONLY = "connector_access_read_only"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class McpRegistryService:
    """Owns MCP registration, auth state, and backend-only credentials."""

    def __init__(
        self,
        *,
        store: InMemoryMcpStore | PostgresMcpStore | None = None,
        token_vault: TokenVault | None = None,
        token_exchanger: OAuthTokenExchanger | None = None,
        oauth_client: OAuthDiscoveryClient | None = None,
        revision_authority: McpRevisionAuthority | None = None,
        session_pool: McpSessionPool | None = None,
        transport_factory: McpHttpTransportFactory | None = None,
        auth_session_ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        self.store = store or self._default_store()
        self.token_vault = token_vault or TokenVaultFactory.create()
        self.oauth_client = oauth_client or HttpOAuthTokenExchanger()
        self.token_exchanger = token_exchanger or self.oauth_client
        self.auth_session_ttl = auth_session_ttl
        # F8: registry mutations and their feed notices share the same store
        # transaction. Discovery remains outside this service and can only
        # publish complete descriptor observations through this authority.
        self.revision_authority = (
            revision_authority or McpRevisionAuthority.for_mcp_store(self.store)
        )
        self._transport_factory = transport_factory or McpHttpTransportFactory(
            token_vault=self.token_vault
        )
        self.session_pool = session_pool or McpSessionPool(
            factory=self._transport_factory,
            config=self._session_pool_config_from_environment(),
        )
        self._session_scopes: OrderedDict[
            tuple[str, str, str], set[VerifiedMcpSessionScopeKey]
        ] = OrderedDict()
        self._session_scopes_lock = threading.RLock()
        self._lease_owners: OrderedDict[str, _LeaseOwner] = OrderedDict()
        self._lease_owners_lock = threading.RLock()
        self._descriptor_observations: OrderedDict[str, _DescriptorObservation] = (
            OrderedDict()
        )
        self._pool_metrics: Counter[str] = Counter()
        # Post-commit observer invoked with the updated record after
        # ``complete_auth`` lands. Wired at app composition time to the
        # connectors destination's write-through (PR-E.3 Decision D1) so
        # BOTH auth-completion entry points — the web callback route AND
        # the desktop OAuth coordinator — flip the connector row to
        # ``connected`` through one seam. The listener contract is
        # log-and-continue: implementations MUST NOT raise (the MCP row
        # + token already committed; see ``app.py`` for the rationale).
        self.auth_completed_listener: Callable[[McpServerRecord], None] | None = None
        # PRD-06 D3 — resolves the joined connector row's durable access mode
        # for card visibility (b) and the ``proxy_internal_rpc`` gate (c).
        # Wired at composition (``app.py``) to the connectors store; ``None``
        # leaves every server ungated (the correct default when no connectors
        # store is present, e.g. isolated MCP-registry tests).
        self.connector_access_resolver: ConnectorAccessResolver | None = None

    @staticmethod
    def _session_pool_config_from_environment() -> McpSessionPoolConfig:
        """Read bounded operational limits without accepting unbounded input."""

        def integer(name: str, default: int, maximum: int) -> int:
            raw = os.environ.get(name, str(default)).strip()
            try:
                value = int(raw)
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer") from exc
            if not 1 <= value <= maximum:
                raise ValueError(f"{name} must be between 1 and {maximum}")
            return value

        def nonnegative(name: str, default: int, maximum: int) -> int:
            raw = os.environ.get(name, str(default)).strip()
            try:
                value = int(raw)
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer") from exc
            if not 0 <= value <= maximum:
                raise ValueError(f"{name} must be between 0 and {maximum}")
            return value

        def seconds(name: str, default: float, maximum: float) -> float:
            raw = os.environ.get(name, str(default)).strip()
            try:
                value = float(raw)
            except ValueError as exc:
                raise ValueError(f"{name} must be numeric") from exc
            if not 0 < value <= maximum:
                raise ValueError(f"{name} must be between 0 and {maximum}")
            return value

        return McpSessionPoolConfig(
            max_total_sessions=integer("MCP_SESSION_POOL_MAX_TOTAL", 64, 512),
            max_sessions_per_key=integer("MCP_SESSION_POOL_MAX_PER_KEY", 4, 32),
            idle_ttl_seconds=seconds("MCP_SESSION_POOL_IDLE_TTL_SECONDS", 60, 3600),
            absolute_ttl_seconds=seconds(
                "MCP_SESSION_POOL_ABSOLUTE_TTL_SECONDS", 900, 86_400
            ),
            invalidation_ttl_seconds=seconds(
                "MCP_SESSION_POOL_INVALIDATION_TTL_SECONDS", 900, 86_400
            ),
            max_pre_dispatch_reconnects=nonnegative(
                "MCP_SESSION_POOL_MAX_PRE_DISPATCH_RECONNECTS", 1, 3
            ),
        )

    def create_server(self, request: CreateMcpServerRequest) -> McpServerResponse:
        display_name = request.display_name or self._display_name_from_url(request.url)
        # Idempotent on (org_id, user_id, normalized URL). Without this
        # the registry accumulates duplicate rows on every retry — and
        # the agent runtime refuses to boot when two MCP servers share
        # a stable name (see ai-backend mcp/registry.py:74,
        # `DUPLICATE_SERVER_NAME`), locking the user out of chat. Match
        # the catalog flow's idempotency contract.
        existing = self._server_by_url(
            org_id=request.org_id,
            user_id=request.user_id,
            url=request.url,
        )
        if existing is not None:
            return McpServerResponse.from_record(existing)
        record = McpServerRecord(
            org_id=request.org_id,
            user_id=request.user_id,
            name=self._stable_name(display_name),
            display_name=display_name,
            url=request.url,
            transport=request.transport,
            auth_mode=request.auth_mode,
            auth_state=(
                McpAuthState.AUTHENTICATED
                if request.auth_mode == McpAuthMode.NONE
                else McpAuthState.UNAUTHENTICATED
            ),
            health=McpServerHealth.HEALTHY,
            oauth_client=self._oauth_client_config(request.oauth_client),
        )
        with self.store.transaction(org_id=record.org_id) as conn:
            self.store.create_server(record, conn=conn)
            self.revision_authority.invalidate(
                org_id=record.org_id,
                user_id=record.user_id,
                server_id=record.server_id,
                reason=McpRevisionReason.CONFIG_CHANGED,
                conn=conn,
            )
            self._audit(record, "mcp_server_created", conn=conn)
        self.invalidate_server_sessions(
            org_id=record.org_id, user_id=record.user_id, server_id=record.server_id
        )
        return McpServerResponse.from_record(record)

    def _server_by_url(
        self,
        *,
        org_id: str,
        user_id: str,
        url: str,
    ) -> McpServerRecord | None:
        normalized = url.strip().rstrip("/").lower()
        for record in self.store.list_servers(org_id=org_id, user_id=user_id):
            if record.url.strip().rstrip("/").lower() == normalized:
                return record
        return None

    def list_servers(self, *, org_id: str, user_id: str) -> McpServerListResponse:
        # PR 4.4.6 — no seeding. ``connectors.servers`` reflects exactly
        # what the user has installed. The curated list lives behind
        # ``GET /v1/mcp/catalog``; install creates a row.
        return McpServerListResponse(
            servers=tuple(
                self._response_from_record(record)
                for record in self.store.list_servers(org_id=org_id, user_id=user_id)
            )
        )

    def list_catalog(self) -> McpCatalogResponse:
        """Curated MCP catalog. Org-agnostic, no DB read.

        Source of truth is ``mcp_catalog.DEFAULT_CATALOG``. Frontend
        cross-references entries with ``connectors.servers`` by
        ``server_id == "seed:" + slug`` to render Install / Resume install
        / Installed state per card.
        """

        return McpCatalogResponse(
            entries=tuple(_catalog_entry_response(entry) for entry in DEFAULT_CATALOG)
        )

    def list_suggestible_connectors(
        self,
        *,
        org_id: str,
        user_id: str,
        exclude_paused: tuple[str, ...] = (),
        user_overrides: dict[str, bool] | None = None,
        mode: str | None = None,
    ) -> McpCatalogResponse:
        """PR 4.4.7 Phase 2 (Slice B) — catalog entries the agent may
        suggest at run-time.

        Filtering rules (server-side, in order):

        0. ``mode="off"`` suggests nothing at all. It is checked first
           because it is the user saying "stop asking", and no per-entry
           rule should be able to talk its way past that.
        1. Drop slugs whose ``seed:<slug>`` already exists in the user's
           installed servers (the user knows about that connector
           already; suggesting it again would be noise).
        2. Drop slugs that the conversation paused. ``exclude_paused``
           accepts the conversation column's keys verbatim — both the
           bare slug and the ``seed:<slug>`` form so callers don't need
           to translate.
        3. Drop entries with ``discoverable=False`` *unless* the user
           override forces ``True``, or ``mode="always"`` widens the
           curated default to the whole catalog.
        4. Drop entries the user explicitly muted (override ``False``).
           A per-slug mute outranks ``mode="always"``: "show me
           everything" is a default, "never this one" is a decision.

        Returns the same wire shape as ``list_catalog`` so the caller
        (``ai-backend``) treats the response uniformly.
        """

        if mode == _SUGGESTIONS_OFF:
            return McpCatalogResponse(entries=())
        widen = mode == _SUGGESTIONS_ALWAYS

        installed = {
            record.server_id
            for record in self.store.list_servers(org_id=org_id, user_id=user_id)
        }
        paused_set = set(exclude_paused)
        # Normalize the exclude set so callers can pass either form.
        for raw in tuple(paused_set):
            if raw.startswith("seed:"):
                paused_set.add(raw[len("seed:") :])
            else:
                paused_set.add(f"seed:{raw}")
        overrides = user_overrides or {}

        suggestions: list[McpCatalogEntryResponse] = []
        for entry in DEFAULT_CATALOG:
            if entry.server_id in installed:
                continue
            if entry.slug in paused_set or entry.server_id in paused_set:
                continue
            override = overrides.get(entry.slug)
            if override is False:
                continue
            if override is None and not entry.discoverable and not widen:
                continue
            # override is True, or the entry is curated-discoverable, or the
            # user asked to hear about everything.
            suggestions.append(_catalog_entry_response(entry))
        return McpCatalogResponse(entries=tuple(suggestions))

    def install_from_catalog(
        self, request: InstallMcpServerRequest
    ) -> McpServerResponse:
        """Install a curated catalog entry into the user's workspace.

        Idempotent on slug — re-installing returns the existing row
        unchanged. Raises ``ValueError`` when the slug is unknown or
        when the entry requires a pre-registered OAuth client and none
        was supplied (mapped to 422 at the route layer).
        """

        entry = _catalog_by_slug().get(request.slug)
        if entry is None:
            raise ValueError(f"Unknown catalog entry: {request.slug}")

        existing = self._server_for_user(
            org_id=request.org_id,
            user_id=request.user_id,
            server_id=entry.server_id,
        )
        if existing is not None:
            return McpServerResponse.from_record(existing)

        if entry.requires_pre_registered_client and request.oauth_client is None:
            raise ValueError(f"Pre-registered OAuth client required for {entry.slug}.")

        record = McpServerRecord(
            server_id=entry.server_id,
            org_id=request.org_id,
            user_id=request.user_id,
            # The identity, stated rather than recoverable from the id.
            connector_slug=entry.slug,
            name=entry.slug.replace("-", "_"),
            display_name=entry.display_name,
            url=entry.url,
            transport=entry.transport,
            auth_mode=entry.auth_mode,
            auth_state=(
                McpAuthState.AUTHENTICATED
                if entry.auth_mode == McpAuthMode.NONE
                else McpAuthState.UNAUTHENTICATED
            ),
            health=McpServerHealth.HEALTHY,
            enabled=True,
            description=entry.description,
            logo_url=entry.logo_url,
            brand_color=entry.brand_color,
            scopes_summary=entry.scopes_summary,
            default_scopes=entry.default_scopes,
            oauth_client=self._oauth_client_config(request.oauth_client),
        )
        with self.store.transaction(org_id=record.org_id) as conn:
            self.store.create_server(record, conn=conn)
            self.revision_authority.invalidate(
                org_id=record.org_id,
                user_id=record.user_id,
                server_id=record.server_id,
                reason=McpRevisionReason.CONFIG_CHANGED,
                conn=conn,
            )
            self._audit(record, "mcp_server_installed", conn=conn)
        self.invalidate_server_sessions(
            org_id=record.org_id, user_id=record.user_id, server_id=record.server_id
        )
        return McpServerResponse.from_record(record)

    def delete_server(self, *, org_id: str, user_id: str, server_id: str) -> bool:
        record = self._server_for_user(
            org_id=org_id, user_id=user_id, server_id=server_id
        )
        if record is None:
            return False
        with self.store.transaction(org_id=org_id) as conn:
            deleted = self.store.delete_server(
                org_id=org_id, server_id=server_id, conn=conn
            )
            if deleted:
                self.revision_authority.invalidate(
                    org_id=org_id,
                    user_id=user_id,
                    server_id=server_id,
                    reason=McpRevisionReason.SERVER_DELETED,
                    conn=conn,
                )
                self._audit(record, "mcp_server_deleted", conn=conn)
        if deleted:
            self.invalidate_server_sessions(
                org_id=org_id, user_id=user_id, server_id=server_id
            )
        return deleted

    def update_server(
        self,
        *,
        org_id: str,
        user_id: str,
        server_id: str,
        request: UpdateMcpServerRequest,
    ) -> McpServerResponse:
        record = self._require_server_for_user(
            org_id=org_id, user_id=user_id, server_id=server_id
        )
        changes: dict[str, object] = {}
        if request.display_name is not None:
            changes[_Fields.DISPLAY_NAME] = request.display_name
        if _Fields.OAUTH_CLIENT in request.model_fields_set:
            changes[_Fields.OAUTH_CLIENT] = self._oauth_client_config(
                request.oauth_client
            )
        if request.enabled is not None:
            changes[_Fields.ENABLED] = request.enabled
            if not request.enabled:
                changes[_Fields.HEALTH] = McpServerHealth.DISABLED
            elif record.health is McpServerHealth.DISABLED:
                changes[_Fields.HEALTH] = McpServerHealth.HEALTHY
        if not changes:
            return McpServerResponse.from_record(record)

        with self.store.transaction(org_id=org_id) as conn:
            updated = self._update_record(record, conn=conn, **changes)
            self.revision_authority.invalidate(
                org_id=org_id,
                user_id=user_id,
                server_id=server_id,
                reason=McpRevisionReason.CONFIG_CHANGED,
                conn=conn,
            )
            self._audit(updated, "mcp_server_updated", conn=conn)
        self.invalidate_server_sessions(
            org_id=org_id, user_id=user_id, server_id=server_id
        )
        return McpServerResponse.from_record(updated)

    def skip_auth(
        self, *, org_id: str, user_id: str, server_id: str
    ) -> McpServerResponse:
        record = self._require_server_for_user(
            org_id=org_id, user_id=user_id, server_id=server_id
        )
        with self.store.transaction(org_id=org_id) as conn:
            updated = self._update_record(
                record, conn=conn, auth_state=McpAuthState.AUTH_SKIPPED
            )
            self.revision_authority.invalidate(
                org_id=org_id,
                user_id=user_id,
                server_id=server_id,
                reason=McpRevisionReason.AUTH_CHANGED,
                conn=conn,
            )
            self._audit(updated, "mcp_auth_skipped", conn=conn)
        self.invalidate_server_sessions(
            org_id=org_id, user_id=user_id, server_id=server_id
        )
        return McpServerResponse.from_record(updated)

    def start_auth(
        self,
        *,
        server_id: str,
        request: McpAuthStartRequest | InternalMcpAuthRequest,
    ) -> McpAuthStartResponse:
        record = self._require_server_for_user(
            org_id=request.org_id,
            user_id=request.user_id,
            server_id=server_id,
        )
        if record.auth_mode != McpAuthMode.OAUTH2:
            with self.store.transaction(org_id=request.org_id) as conn:
                updated = self._update_record(
                    record,
                    conn=conn,
                    auth_state=McpAuthState.AUTH_UNSUPPORTED,
                )
                self.revision_authority.invalidate(
                    org_id=request.org_id,
                    user_id=request.user_id,
                    server_id=server_id,
                    reason=McpRevisionReason.AUTH_CHANGED,
                    conn=conn,
                )
                self._audit(updated, "mcp_auth_unsupported", conn=conn)
            raise ValueError("MCP server does not support OAuth authentication")

        verifier = generate_verifier()
        expires_at = datetime.now(timezone.utc) + self.auth_session_ttl
        session = McpAuthSessionRecord(
            server_id=record.server_id,
            org_id=record.org_id,
            user_id=record.user_id,
            code_verifier=verifier,
            redirect_uri=request.redirect_uri,
            auth_url=record.url,
            expires_at=expires_at,
        )
        authorization = self.oauth_client.authorization(
            record=record,
            redirect_uri=request.redirect_uri,
            state=session.state,
            code_challenge=compute_challenge(session.code_verifier),
            token_vault=self.token_vault,
        )
        session = session.model_copy(update={"auth_url": authorization.auth_url})
        self.store.create_auth_session(session)
        next_auth_state = (
            McpAuthState.AUTHENTICATED
            if self._has_usable_token(record)
            else McpAuthState.AUTH_PENDING
        )
        with self.store.transaction(org_id=request.org_id) as conn:
            updated = self._update_record(
                record,
                conn=conn,
                auth_state=next_auth_state,
                last_discovery=authorization.discovery,
                required_scopes=authorization.required_scopes,
            )
            self.revision_authority.invalidate(
                org_id=request.org_id,
                user_id=request.user_id,
                server_id=server_id,
                reason=McpRevisionReason.AUTH_CHANGED,
                conn=conn,
            )
            self._audit(updated, "mcp_auth_started", conn=conn)
        self.invalidate_server_sessions(
            org_id=request.org_id, user_id=request.user_id, server_id=server_id
        )
        return McpAuthStartResponse(
            server_id=record.server_id,
            auth_url=authorization.auth_url,
            expires_at=session.expires_at,
        )

    def complete_auth(self, request: McpAuthCallbackRequest) -> McpServerResponse:
        session = self.store.pop_auth_session(state=request.state)
        if session is None or session.expires_at < datetime.now(timezone.utc):
            raise ValueError("MCP auth session is invalid or expired")
        record = self._require_server_for_user(
            org_id=session.org_id,
            user_id=session.user_id,
            server_id=session.server_id,
        )
        if request.error is not None:
            with self.store.transaction(org_id=session.org_id) as conn:
                updated = self._update_record(
                    record, conn=conn, auth_state=McpAuthState.AUTH_FAILED
                )
                self.revision_authority.invalidate(
                    org_id=session.org_id,
                    user_id=session.user_id,
                    server_id=session.server_id,
                    reason=McpRevisionReason.AUTH_CHANGED,
                    conn=conn,
                )
                self._audit(updated, "mcp_auth_failed", conn=conn)
            self.invalidate_server_sessions(
                org_id=session.org_id,
                user_id=session.user_id,
                server_id=session.server_id,
            )
            detail = request.error_description or request.error
            raise ValueError(f"MCP auth failed: {detail}")
        if request.code is None:
            raise ValueError("MCP auth callback did not include an authorization code")
        tokens = self.token_exchanger.exchange_code(
            record=record,
            session=session,
            code=request.code,
            token_vault=self.token_vault,
        )
        encrypted_access = self.token_vault.encrypt(tokens.access_token)
        encrypted_refresh = (
            self.token_vault.encrypt(tokens.refresh_token)
            if tokens.refresh_token is not None
            else None
        )
        token_envelope = TokenEnvelope(
            server_id=record.server_id,
            org_id=record.org_id,
            user_id=record.user_id,
            encrypted_access_token=encrypted_access,
            encrypted_refresh_token=encrypted_refresh,
            token_type=tokens.token_type,
            expires_at=tokens.expires_at,
            kms_key_id=self.token_vault.key_id_for(encrypted_access),
        )
        with self.store.transaction(org_id=record.org_id) as conn:
            self.store.put_token(token_envelope, conn=conn)
            updated = self._update_record(
                record, conn=conn, auth_state=McpAuthState.AUTHENTICATED
            )
            self.revision_authority.invalidate(
                org_id=record.org_id,
                user_id=record.user_id,
                server_id=record.server_id,
                reason=McpRevisionReason.AUTH_CHANGED,
                credential_subject=token_envelope.connection_id,
                conn=conn,
            )
            self._audit(updated, "mcp_auth_completed", conn=conn)
        self.invalidate_server_sessions(
            org_id=record.org_id, user_id=record.user_id, server_id=record.server_id
        )
        # Post-commit: notify the connectors write-through (if wired) so
        # the denormalized ``/v1/connectors`` row flips to ``connected``
        # for both the web callback and the desktop coordinator path.
        if self.auth_completed_listener is not None:
            self.auth_completed_listener(updated)
        return McpServerResponse.from_record(updated)

    def list_internal_cards(
        self, *, org_id: str, user_id: str
    ) -> InternalMcpServerListResponse:
        cards = []
        for record in self.store.list_servers(org_id=org_id, user_id=user_id):
            if not record.enabled:
                continue
            # PRD-06 D3(a) — visibility gate. An ``off`` connector is never
            # offered to the model: the card is omitted, so there is no tool
            # to deny. When no connector row joins the server the resolver
            # returns ``None`` and the card is shown with the default ``read``
            # mode (an unprojected server is not a user-set ``off``).
            access_mode = self._resolve_access_mode(record)
            if access_mode == ConnectorAccessMode.OFF:
                continue
            auth_state = self._effective_auth_state(record)
            cards.append(
                InternalMcpServerCard(
                    server_id=record.server_id,
                    name=record.name,
                    display_name=record.display_name,
                    short_description=self._card_description(
                        record,
                        auth_state=auth_state,
                    ),
                    transport=record.transport,
                    auth_mode=record.auth_mode,
                    auth_state=auth_state,
                    required_scopes=record.required_scopes,
                    health=record.health,
                    enabled=record.enabled,
                    access_mode=(access_mode or ConnectorAccessMode.READ).value,
                    connector_slug=record.connector_slug,
                )
            )
        return InternalMcpServerListResponse(servers=tuple(cards))

    def _resolve_access_mode(
        self, record: McpServerRecord
    ) -> ConnectorAccessMode | None:
        """Resolve the joined connector row's access mode, or ``None``.

        ``None`` means no connector row joins this MCP server (the gate
        SKIPS — an unprojected server is not a user-set ``off``). A resolver
        exception is swallowed to ``None`` so a connectors-store hiccup can
        never harden into a false ``off`` that blocks all traffic; the
        authoritative deny path is the explicit ``off`` value.
        """

        if self.connector_access_resolver is None:
            return None
        try:
            return self.connector_access_resolver(record)
        except Exception:  # pragma: no cover - defensive; never fail closed here
            return None

    def _require_live_server(self, record: McpServerRecord) -> None:
        if not record.enabled or record.health in {
            McpServerHealth.DISABLED,
            McpServerHealth.UNAVAILABLE,
        }:
            raise ValueError("MCP server is unavailable")
        if (
            record.auth_mode != McpAuthMode.NONE
            and self._effective_auth_state(record) != McpAuthState.AUTHENTICATED
        ):
            raise ValueError("MCP server is not authenticated")

    def create_internal_client_session(
        self,
        *,
        org_id: str,
        user_id: str,
        server_id: str,
    ) -> InternalMcpClientSession:
        record = self._require_server_for_user(
            org_id=org_id, user_id=user_id, server_id=server_id
        )
        if self._resolve_access_mode(record) == ConnectorAccessMode.OFF:
            raise ConnectorAccessDenied(ConnectorAccessDenied.OFF)
        self._require_live_server(record)
        token = (
            self._require_valid_token(record)
            if record.auth_mode != McpAuthMode.NONE
            else None
        )
        scope = self._bind_session_scope(record, token)
        acquired = self.session_pool.acquire(scope)
        if (
            acquired.outcome is not McpSessionPoolOutcome.ACQUIRED
            or acquired.lease is None
        ):
            self._pool_metrics[acquired.outcome.value] += 1
            raise ValueError(f"MCP session pool {acquired.outcome.value}")
        self._remember_session_scope(record, scope)
        lease_token = self.session_pool.export_lease_token(acquired.lease)
        self._remember_lease(lease_token, record, scope)
        self._pool_metrics["acquired"] += 1
        return InternalMcpClientSession(lease=lease_token)

    def proxy_internal_rpc(
        self,
        *,
        org_id: str,
        user_id: str,
        server_id: str,
        request: InternalMcpRpcRequest,
    ) -> InternalMcpRpcResponse:
        record = self._require_server_for_user(
            org_id=org_id, user_id=user_id, server_id=server_id
        )
        # PRD-06 D3(c) — the real permission boundary, on the authoritative
        # side of the trust line. Resolve BEFORE ``_require_valid_token`` so an
        # ``off`` connector never even decrypts a vault token.
        access_mode = self._resolve_access_mode(record)
        method = request.payload.get("method")
        if access_mode == ConnectorAccessMode.OFF:
            raise ConnectorAccessDenied(ConnectorAccessDenied.OFF)
        self._require_live_server(record)
        owner = self._require_lease_owner(
            request.lease, org_id=org_id, user_id=user_id, server_id=server_id
        )
        current_token = self.store.get_token(server_id=server_id)
        if self._scope_for(record, current_token) != owner.scope:
            self._retire_lease(request.lease, owner, cancel=True)
            raise ValueError("MCP session lease is stale")
        token = (
            self._require_valid_token(record)
            if record.auth_mode != McpAuthMode.NONE
            else None
        )
        scope = self._bind_session_scope(record, token)
        if scope != owner.scope:
            self._retire_lease(request.lease, owner, cancel=True)
            raise ValueError("MCP session lease is stale")
        try:
            lease = self.session_pool.import_lease_token(request.lease)
        except ValueError as exc:
            raise ValueError("MCP session lease is invalid") from exc
        # ``read`` gates side-effecting calls: a ``tools/call`` on a tool that
        # is not read-only is denied. ``tools/list`` (and every other method)
        # is always allowed under ``read``; ``read_act`` and the unjoined
        # (``None``) case allow everything.
        if access_mode == ConnectorAccessMode.READ and method == "tools/call":
            tool_name = self._rpc_tool_name(request.payload)
            if not self._tool_is_read_only(record, scope, lease, tool_name):
                raise ConnectorAccessDenied(ConnectorAccessDenied.READ_ONLY)
        try:
            with owner.operation_lock:
                payload = self.session_pool.invoke(
                    lease,
                    scope=scope,
                    operation=lambda transport, fence: self._remote_rpc(
                        transport, request.payload, fence
                    ),
                )
                self._observe_proxied_descriptor_page(
                    lease_token=request.lease,
                    owner=owner,
                    request_payload=request.payload,
                    response=payload,
                    credential_subject=(
                        token.connection_id if token is not None else None
                    ),
                )
        except McpSessionPoolRejected as exc:
            self._forget_lease(request.lease)
            raise ValueError("MCP session lease is stale") from exc
        except Exception:
            self._forget_lease_observation(request.lease)
            raise
        return InternalMcpRpcResponse(payload=payload)

    def release_internal_client_session(
        self,
        *,
        org_id: str,
        user_id: str,
        server_id: str,
        lease_token: str,
        cancel: bool,
    ) -> InternalMcpSessionReleaseResponse:
        owner = self._require_lease_owner(
            lease_token, org_id=org_id, user_id=user_id, server_id=server_id
        )
        try:
            lease = self.session_pool.import_lease_token(lease_token)
        except ValueError as exc:
            raise ValueError("MCP session lease is invalid") from exc
        with owner.operation_lock:
            outcome = (
                self.session_pool.cancel(lease, scope=owner.scope)
                if cancel
                else self.session_pool.release(lease, scope=owner.scope)
            )
        self._forget_lease(lease_token)
        return InternalMcpSessionReleaseResponse(outcome=outcome.value)

    @staticmethod
    def _rpc_tool_name(payload: dict[str, object]) -> str | None:
        """Extract the target tool name from a ``tools/call`` JSON-RPC payload."""

        params = payload.get("params")
        if isinstance(params, dict):
            name = params.get("name")
            if isinstance(name, str):
                return name
        return None

    def _tool_is_read_only(
        self,
        record: McpServerRecord,
        scope: VerifiedMcpSessionScopeKey,
        lease: McpSessionLease,
        tool_name: str | None,
    ) -> bool:
        """Whether ``tool_name`` advertises ``annotations.readOnlyHint: true``.

        The backend does not persist tool annotations, so it asks the server
        for its advertised tool list (``tools/list``) and inspects the target
        tool. FAIL-CLOSED: an unknown tool, an absent ``annotations`` block,
        or a missing/false ``readOnlyHint`` all classify as "not read-only"
        (⇒ denied under ``read``). A server that publishes no annotations is
        therefore list-only in ``read`` and fully usable in ``read_act``.
        """

        if tool_name is None:
            return False
        list_payload: dict[str, object] = {
            "jsonrpc": "2.0",
            "id": "access-mode-classify",
            "method": "tools/list",
            "params": {},
        }
        try:
            listing = self.session_pool.invoke(
                lease,
                scope=scope,
                operation=lambda transport, fence: self._remote_rpc(
                    transport, list_payload, fence
                ),
            )
        except McpSessionPoolRejected:
            return False
        result = listing.get("result") if isinstance(listing, dict) else None
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            return False
        for tool in tools:
            if not isinstance(tool, dict) or tool.get("name") != tool_name:
                continue
            annotations = tool.get("annotations")
            if not isinstance(annotations, dict):
                return False
            return annotations.get("readOnlyHint") is True
        return False

    @staticmethod
    def _remote_rpc(
        transport: object,
        payload: dict[str, object],
        fence: McpSessionDispatchFence,
    ) -> dict[str, object]:
        if not isinstance(transport, McpRemoteSessionTransport):
            raise ConnectionError("MCP pool transport cannot issue RPC")
        return transport.rpc(payload, fence)

    def _bind_session_scope(
        self, record: McpServerRecord, token: TokenEnvelope | None
    ) -> VerifiedMcpSessionScopeKey:
        """Bind a verified registry row to an opaque pool compatibility key."""

        scope = self._scope_for(record, token)
        self._transport_factory.bind(
            scope=scope,
            endpoint=record.url,
            encrypted_access_token=(
                token.encrypted_access_token if token is not None else None
            ),
        )
        return scope

    @staticmethod
    def _scope_for(
        record: McpServerRecord, token: TokenEnvelope | None
    ) -> VerifiedMcpSessionScopeKey:

        credential_reference = (
            token.connection_id
            if token is not None
            else f"unauthenticated:{record.server_id}"
        )
        auth_epoch = (
            token.updated_at.isoformat()
            if token is not None
            else record.updated_at.isoformat()
        )
        transport_revision = hashlib.sha256(
            f"{record.transport.value}\x1f{record.url}\x1f{record.updated_at.isoformat()}".encode()
        ).hexdigest()
        return VerifiedMcpSessionScopeKey.from_verified_credential_reference(
            org_id=record.org_id,
            # This service has no user-selectable MCP profile. The explicit
            # compatibility partition prevents accidental reuse if one is
            # introduced later without exposing a deployment identifier.
            profile_partition=_BACKEND_COMPATIBILITY_PARTITION,
            user_id=record.user_id,
            server_id=record.server_id,
            credential_reference=credential_reference,
            auth_epoch=auth_epoch,
            transport_revision=transport_revision,
            session_scope="internal-rpc",
        )

    def _remember_lease(
        self,
        lease_token: str,
        record: McpServerRecord,
        scope: VerifiedMcpSessionScopeKey,
    ) -> None:
        with self._lease_owners_lock:
            self._lease_owners[lease_token] = _LeaseOwner(
                org_id=record.org_id,
                user_id=record.user_id,
                server_id=record.server_id,
                scope=scope,
            )
            self._lease_owners.move_to_end(lease_token)
            while len(self._lease_owners) > 512:
                stale_token, stale_owner = self._lease_owners.popitem(last=False)
                self._retire_lease(stale_token, stale_owner, cancel=True)

    def _require_lease_owner(
        self, lease_token: str, *, org_id: str, user_id: str, server_id: str
    ) -> _LeaseOwner:
        with self._lease_owners_lock:
            owner = self._lease_owners.get(lease_token)
            if owner is None:
                raise ValueError("MCP session lease is stale")
            if (owner.org_id, owner.user_id, owner.server_id) != (
                org_id,
                user_id,
                server_id,
            ):
                raise ValueError("MCP session lease is stale")
            self._lease_owners.move_to_end(lease_token)
            return owner

    def _forget_lease(self, lease_token: str) -> None:
        with self._lease_owners_lock:
            self._lease_owners.pop(lease_token, None)
            self._descriptor_observations.pop(lease_token, None)

    def _retire_lease(
        self, lease_token: str, owner: _LeaseOwner, *, cancel: bool
    ) -> None:
        try:
            lease = self.session_pool.import_lease_token(lease_token)
            if cancel:
                self.session_pool.cancel(lease, scope=owner.scope)
            else:
                self.session_pool.release(lease, scope=owner.scope)
        finally:
            self._forget_lease(lease_token)

    def _remember_session_scope(
        self, record: McpServerRecord, scope: VerifiedMcpSessionScopeKey
    ) -> None:
        key = (record.org_id, record.user_id, record.server_id)
        with self._session_scopes_lock:
            self._session_scopes.setdefault(key, set()).add(scope)
            while len(self._session_scopes) > 128:
                stale_key, stale_scopes = self._session_scopes.popitem(last=False)
                for stale_scope in stale_scopes:
                    self.session_pool.invalidate_scope(stale_scope)
                    self._transport_factory.unbind(stale_scope)

    def invalidate_server_sessions(
        self, *, org_id: str, user_id: str, server_id: str
    ) -> int:
        """Retire all remembered leases for a registry mutation atomically enough."""

        with self._lease_owners_lock:
            owners = [
                (lease_token, owner)
                for lease_token, owner in self._lease_owners.items()
                if (owner.org_id, owner.user_id, owner.server_id)
                == (org_id, user_id, server_id)
            ]
        for lease_token, owner in owners:
            self._retire_lease(lease_token, owner, cancel=True)
        with self._session_scopes_lock:
            scopes = self._session_scopes.pop((org_id, user_id, server_id), set())
        invalidated = 0
        for scope in scopes:
            invalidated += self.session_pool.invalidate_scope(scope)
            self._transport_factory.unbind(scope)
        return invalidated

    def maintain_session_pool(self) -> None:
        """One lifecycle-owned maintenance tick; never starts its own thread."""

        try:
            outcome = self.session_pool.keepalive_idle(limit=1)
            self._pool_metrics[f"keepalive_{outcome.value}"] += 1
            self._pool_metrics["reaped"] += self.session_pool.reap_expired()
        except Exception:
            self._pool_metrics["maintenance_failed"] += 1
            _logger.warning("mcp_session_pool_maintenance_failed", exc_info=True)

    def shutdown_session_pool(self, *, timeout_seconds: float = 5) -> bool:
        drained = self.session_pool.shutdown(timeout_seconds=timeout_seconds)
        if not drained:
            with self._lease_owners_lock:
                owners = tuple(self._lease_owners.items())
            for lease_token, owner in owners:
                self._retire_lease(lease_token, owner, cancel=True)
            self.session_pool.shutdown(timeout_seconds=0)
        return drained

    def session_pool_diagnostics(self) -> dict[str, int | bool]:
        diagnostics = self.session_pool.diagnostics()
        return {
            "total_sessions": diagnostics.total_sessions,
            "active_leases": diagnostics.active_leases,
            "idle_sessions": diagnostics.idle_sessions,
            "maintenance_sessions": diagnostics.maintenance_sessions,
            "opening_sessions": diagnostics.opening_sessions,
            "invalidated_sessions": diagnostics.invalidated_sessions,
            "draining": diagnostics.draining,
            "opened_sessions": diagnostics.opened_sessions,
            "reused_sessions": diagnostics.reused_sessions,
            "saturated_acquires": diagnostics.saturated_acquires,
            "pre_dispatch_reconnects": diagnostics.pre_dispatch_reconnects,
            "keepalive_attempts": diagnostics.keepalive_attempts,
            **dict(self._pool_metrics),
        }

    def _observe_proxied_descriptor_page(
        self,
        *,
        lease_token: str,
        owner: _LeaseOwner,
        request_payload: dict[str, object],
        response: dict[str, object],
        credential_subject: str | None,
    ) -> None:
        method = request_payload.get("method")
        if method not in {"tools/list", "resources/list"}:
            return
        collection = "tools" if method == "tools/list" else "resources"
        params = request_payload.get("params")
        cursor = params.get("cursor") if isinstance(params, dict) else None
        if cursor is not None and not isinstance(cursor, str):
            self._forget_lease_observation(lease_token)
            return
        if "error" in response:
            error = response.get("error")
            if (
                collection == "resources"
                and isinstance(error, dict)
                and error.get("code") == -32601
                and cursor is None
            ):
                self._complete_empty_resources(
                    lease_token,
                    owner=owner,
                    credential_subject=credential_subject,
                )
            else:
                self._forget_lease_observation(lease_token)
            return
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get(collection), list):
            self._forget_lease_observation(lease_token)
            return
        with self._lease_owners_lock:
            if collection == "tools" and cursor is None:
                observation = _DescriptorObservation()
                self._descriptor_observations[lease_token] = observation
            else:
                observation = self._descriptor_observations.get(lease_token)
                if observation is None or (
                    collection == "resources" and not observation.tools.complete
                ):
                    self._descriptor_observations.pop(lease_token, None)
                    return
            self._descriptor_observations.move_to_end(lease_token)
            while len(self._descriptor_observations) > 512:
                self._descriptor_observations.popitem(last=False)
            cycle = (
                observation.tools if collection == "tools" else observation.resources
            )
            if cursor is None:
                cycle.expected_cursor = None
                cycle.complete = False
                cycle.count = 0
                cycle.page_count = 0
                cycle.descriptor_count = 0
                cycle.canonical_bytes = 0
                cycle.requested_cursors.clear()
                cycle.digest = hashlib.sha256()
            elif (
                cycle.complete
                or cycle.expected_cursor != cursor
                or cursor in cycle.requested_cursors
            ):
                self._descriptor_observations.pop(lease_token, None)
                return
            cycle.requested_cursors.add(cursor)
            cycle.page_count += 1
            if cycle.page_count > _MAX_DESCRIPTOR_PAGES:
                self._descriptor_observations.pop(lease_token, None)
                return
            for descriptor in result[collection]:
                if not isinstance(descriptor, dict):
                    self._descriptor_observations.pop(lease_token, None)
                    return
                safe = {
                    key: descriptor[key]
                    for key in (
                        "name",
                        "title",
                        "description",
                        "uri",
                        "mimeType",
                        "inputSchema",
                        "annotations",
                    )
                    if key in descriptor
                }
                canonical = json.dumps(
                    safe, sort_keys=True, separators=(",", ":"), default=str
                ).encode()
                cycle.descriptor_count += 1
                cycle.canonical_bytes += len(canonical)
                if (
                    cycle.descriptor_count > _MAX_DESCRIPTOR_COUNT
                    or cycle.canonical_bytes > _MAX_DESCRIPTOR_BYTES
                ):
                    self._descriptor_observations.pop(lease_token, None)
                    return
                cycle.digest.update(canonical)
                cycle.digest.update(b"\n")
                cycle.count += 1
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                cycle.complete = True
                cycle.expected_cursor = None
            elif isinstance(next_cursor, str) and next_cursor:
                cycle.expected_cursor = next_cursor
            else:
                self._descriptor_observations.pop(lease_token, None)
                return
            if not (observation.tools.complete and observation.resources.complete):
                return
            digest = hashlib.sha256(
                observation.tools.digest.digest()
                + observation.resources.digest.digest()
            ).hexdigest()
            self._descriptor_observations.pop(lease_token, None)
        existing = self.revision_authority.get_current(
            org_id=owner.org_id, user_id=owner.user_id, server_id=owner.server_id
        )
        if existing is None or existing.descriptor_digest != digest:
            self.revision_authority.publish_complete_descriptor_view(
                org_id=owner.org_id,
                user_id=owner.user_id,
                server_id=owner.server_id,
                descriptor_digest=digest,
                tool_count=observation.tools.count,
                resource_count=observation.resources.count,
                source="pooled_mcp_pagination",
                idempotency_key=f"pooled:{owner.scope.fingerprint}:{digest}",
                credential_subject=credential_subject,
            )

    def _complete_empty_resources(
        self,
        lease_token: str,
        *,
        owner: _LeaseOwner,
        credential_subject: str | None,
    ) -> None:
        with self._lease_owners_lock:
            observation = self._descriptor_observations.setdefault(
                lease_token, _DescriptorObservation()
            )
            observation.resources.complete = True
            observation.resources.count = 0
            observation.resources.digest = hashlib.sha256()
            if not observation.tools.complete:
                return
            digest = hashlib.sha256(
                observation.tools.digest.digest()
                + observation.resources.digest.digest()
            ).hexdigest()
            tool_count = observation.tools.count
            self._descriptor_observations.pop(lease_token, None)
        existing = self.revision_authority.get_current(
            org_id=owner.org_id, user_id=owner.user_id, server_id=owner.server_id
        )
        if existing is None or existing.descriptor_digest != digest:
            self.revision_authority.publish_complete_descriptor_view(
                org_id=owner.org_id,
                user_id=owner.user_id,
                server_id=owner.server_id,
                descriptor_digest=digest,
                tool_count=tool_count,
                resource_count=0,
                source="pooled_mcp_pagination",
                idempotency_key=f"pooled:{owner.scope.fingerprint}:{digest}",
                credential_subject=credential_subject,
            )

    def _forget_lease_observation(self, lease_token: str) -> None:
        with self._lease_owners_lock:
            self._descriptor_observations.pop(lease_token, None)

    def upsert_token_for_test(
        self,
        *,
        org_id: str,
        user_id: str,
        server_id: str,
        request: OAuthTokenRequest,
    ) -> McpServerResponse:
        record = self._require_server_for_user(
            org_id=org_id, user_id=user_id, server_id=server_id
        )
        encrypted_access = self.token_vault.encrypt(request.access_token)
        encrypted_refresh = (
            self.token_vault.encrypt(request.refresh_token)
            if request.refresh_token is not None
            else None
        )
        token_envelope = TokenEnvelope(
            server_id=record.server_id,
            org_id=record.org_id,
            user_id=record.user_id,
            encrypted_access_token=encrypted_access,
            encrypted_refresh_token=encrypted_refresh,
            token_type=request.token_type,
            expires_at=request.expires_at,
            kms_key_id=self.token_vault.key_id_for(encrypted_access),
        )
        with self.store.transaction(org_id=record.org_id) as conn:
            self.store.put_token(token_envelope, conn=conn)
            updated = self._update_record(
                record, conn=conn, auth_state=McpAuthState.AUTHENTICATED
            )
            self.revision_authority.invalidate(
                org_id=record.org_id,
                user_id=record.user_id,
                server_id=record.server_id,
                reason=McpRevisionReason.AUTH_CHANGED,
                credential_subject=token_envelope.connection_id,
                conn=conn,
            )
            self._audit(updated, "mcp_token_upserted", conn=conn)
        self.invalidate_server_sessions(
            org_id=record.org_id, user_id=record.user_id, server_id=record.server_id
        )
        return McpServerResponse.from_record(updated)

    def _update_record(
        self,
        record: McpServerRecord,
        *,
        conn: Any | None = None,
        **changes: object,
    ) -> McpServerRecord:
        updated = record.model_copy(
            update={**changes, _Fields.UPDATED_AT: datetime.now(timezone.utc)}
        )
        return self.store.update_server(updated, conn=conn)

    def _response_from_record(self, record: McpServerRecord) -> McpServerResponse:
        effective_record = record.model_copy(
            update={_Fields.AUTH_STATE: self._effective_auth_state(record)}
        )
        return McpServerResponse.from_record(effective_record)

    def _effective_auth_state(self, record: McpServerRecord) -> McpAuthState:
        if record.auth_state == McpAuthState.AUTHENTICATED:
            return record.auth_state
        if self._has_usable_token(record):
            return McpAuthState.AUTHENTICATED
        return record.auth_state

    def _has_usable_token(self, record: McpServerRecord) -> bool:
        token = self.store.get_token(server_id=record.server_id)
        if token is None:
            return False
        if token.expires_at is None:
            return True
        if token.expires_at > datetime.now(timezone.utc) + timedelta(seconds=60):
            return True
        return token.encrypted_refresh_token is not None

    def _oauth_client_config(
        self, request: McpOAuthClientRequest | None
    ) -> McpOAuthClientConfig | None:
        if request is None:
            return None
        token_endpoint_auth_method = request.token_endpoint_auth_method
        if token_endpoint_auth_method is None:
            token_endpoint_auth_method = (
                "client_secret_post" if request.client_secret else "none"
            )
        encrypted_secret = (
            self.token_vault.encrypt(request.client_secret)
            if request.client_secret is not None
            else None
        )
        return McpOAuthClientConfig(
            client_id=request.client_id,
            encrypted_client_secret=encrypted_secret,
            token_endpoint_auth_method=token_endpoint_auth_method,
            scope=request.scope,
            authorization_endpoint=request.authorization_endpoint,
            token_endpoint=request.token_endpoint,
        )

    def _require_valid_token(self, record: McpServerRecord) -> TokenEnvelope:
        token = self.store.get_token(server_id=record.server_id)
        if token is None:
            raise ValueError("MCP server is not authenticated")
        if token.expires_at is None or token.expires_at > datetime.now(
            timezone.utc
        ) + timedelta(seconds=60):
            return token
        if token.encrypted_refresh_token is None:
            raise ValueError(
                "MCP access token expired and no refresh token is available"
            )
        refresh_token = self.token_vault.decrypt(token.encrypted_refresh_token)
        refresher = getattr(self.token_exchanger, "refresh_token", None)
        if not callable(refresher):
            raise ValueError("MCP access token refresh is not supported")
        refreshed = refresher(
            record=record,
            refresh_token=refresh_token,
            token_vault=self.token_vault,
        )
        encrypted_access = self.token_vault.encrypt(refreshed.access_token)
        encrypted_refresh = (
            self.token_vault.encrypt(refreshed.refresh_token)
            if refreshed.refresh_token is not None
            else token.encrypted_refresh_token
        )
        envelope = TokenEnvelope(
            connection_id=token.connection_id,
            server_id=record.server_id,
            org_id=record.org_id,
            user_id=record.user_id,
            encrypted_access_token=encrypted_access,
            encrypted_refresh_token=encrypted_refresh,
            token_type=refreshed.token_type,
            expires_at=refreshed.expires_at,
            created_at=token.created_at,
            updated_at=datetime.now(timezone.utc),
            kms_key_id=self.token_vault.key_id_for(encrypted_access),
        )
        with self.store.transaction(org_id=record.org_id) as conn:
            updated = self.store.put_token(envelope, conn=conn)
            self.revision_authority.invalidate(
                org_id=record.org_id,
                user_id=record.user_id,
                server_id=record.server_id,
                reason=McpRevisionReason.AUTH_CHANGED,
                credential_subject=envelope.connection_id,
                conn=conn,
            )
            self._audit(record, "mcp_token_refreshed", conn=conn)
        self.invalidate_server_sessions(
            org_id=record.org_id, user_id=record.user_id, server_id=record.server_id
        )
        return updated

    @classmethod
    def _default_store(cls) -> InMemoryMcpStore | PostgresMcpStore:
        database_url = os.environ.get("DATABASE_URL", "").strip()
        if database_url:
            pool = PostgresConnectionPool.shared(database_url)
            return PostgresMcpStore(pool=pool)
        if TokenVaultFactory.environment() == "production":
            raise RuntimeError("Production requires a persistent MCP registry store")
        return InMemoryMcpStore()

    def _require_server_for_user(
        self, *, org_id: str, user_id: str, server_id: str
    ) -> McpServerRecord:
        record = self._server_for_user(
            org_id=org_id, user_id=user_id, server_id=server_id
        )
        if record is None:
            raise ValueError("MCP server was not found for this scope")
        return record

    def _server_for_user(
        self, *, org_id: str, user_id: str, server_id: str
    ) -> McpServerRecord | None:
        record = self.store.get_server(org_id=org_id, server_id=server_id)
        if record is None or record.user_id != user_id:
            return None
        return record

    def _audit(
        self,
        record: McpServerRecord,
        action: str,
        *,
        conn: Any | None = None,
    ) -> None:
        self.store.append_audit(
            AuditEventRecord(
                org_id=record.org_id,
                user_id=record.user_id,
                server_id=record.server_id,
                action=action,
                metadata={
                    _Fields.AUTH_STATE: record.auth_state.value,
                    _Fields.HEALTH: record.health.value,
                },
            ),
            conn=conn,
        )

    @classmethod
    def _display_name_from_url(cls, url: str) -> str:
        host = urlsplit(url).hostname or "MCP Server"
        return host.replace(".", " ").title()

    @classmethod
    def _stable_name(cls, display_name: str) -> str:
        normalized = display_name.lower().replace(" ", "_").replace("-", "_")
        return "".join(
            char for char in normalized if char.isalnum() or char == "_"
        ).strip("_")

    @classmethod
    def _card_description(
        cls,
        record: McpServerRecord,
        *,
        auth_state: McpAuthState | None = None,
    ) -> str:
        visible_auth_state = auth_state or record.auth_state
        if visible_auth_state in {
            McpAuthState.AUTHENTICATED,
            McpAuthState.AUTH_SKIPPED,
        }:
            return f"{record.display_name} MCP server."
        return f"{record.display_name} MCP server requires authentication before tools can load."


class SkillRegistryService:
    """Owns user-created Skill markdown and runtime-visible Skill cards."""

    def __init__(
        self, *, store: InMemorySkillStore | PostgresSkillStore | None = None
    ) -> None:
        self.store = store or self._default_store()
        self._seeded_scopes: set[tuple[str, str]] = set()

    def create_skill(self, request: CreateSkillRequest) -> SkillResponse:
        self._ensure_preloaded_skills(org_id=request.org_id, user_id=request.user_id)
        manifest = SkillMarkdownParser.parse_manifest(request.markdown)
        if self.store.get_skill_by_name(
            org_id=request.org_id,
            user_id=request.user_id,
            name=manifest.name,
        ):
            raise ValueError("A skill with this name already exists for this scope")
        record = SkillRecord(
            org_id=request.org_id,
            user_id=request.user_id,
            name=manifest.name,
            display_name=request.display_name
            or self._display_name_from_slug(manifest.name),
            description=manifest.description,
            markdown=request.markdown,
            virtual_path=self._virtual_path(
                org_id=request.org_id,
                user_id=request.user_id,
                name=manifest.name,
            ),
            enabled=request.enabled,
            scope=request.scope,
            allowed_tools=manifest.allowed_tools,
            compatibility=manifest.compatibility,
            metadata=manifest.metadata,
        )
        with self.store.transaction() as conn:
            self.store.create_skill(record, conn=conn)
            self._audit(record, "skill_created", conn=conn)
        return SkillResponse.from_record(record)

    def list_skills(self, *, org_id: str, user_id: str) -> SkillListResponse:
        self._ensure_preloaded_skills(org_id=org_id, user_id=user_id)
        return SkillListResponse(
            skills=tuple(
                SkillResponse.from_record(record)
                for record in self.store.list_skills(org_id=org_id, user_id=user_id)
            )
        )

    def get_skill(self, *, org_id: str, user_id: str, skill_id: str) -> SkillResponse:
        self._ensure_preloaded_skills(org_id=org_id, user_id=user_id)
        return SkillResponse.from_record(
            self._require_visible_skill(
                org_id=org_id, user_id=user_id, skill_id=skill_id
            )
        )

    def update_skill(
        self,
        *,
        org_id: str,
        user_id: str,
        skill_id: str,
        request: UpdateSkillRequest,
    ) -> SkillResponse:
        self._ensure_preloaded_skills(org_id=org_id, user_id=user_id)
        record = self._require_owned_skill(
            org_id=org_id, user_id=user_id, skill_id=skill_id
        )
        if record.source_type is SkillSourceType.PRELOADED and any(
            value is not None
            for value in (request.markdown, request.display_name, request.scope)
        ):
            raise ValueError("Preloaded skills can only be enabled or disabled")
        changes: dict[str, object] = {_Fields.UPDATED_AT: datetime.now(timezone.utc)}
        if request.markdown is not None:
            manifest = SkillMarkdownParser.parse_manifest(request.markdown)
            if manifest.name != record.name:
                raise ValueError("Skill name cannot change after creation")
            changes.update(
                {
                    _Fields.DESCRIPTION: manifest.description,
                    _Fields.MARKDOWN: request.markdown,
                    _Fields.ALLOWED_TOOLS: manifest.allowed_tools,
                    _Fields.COMPATIBILITY: manifest.compatibility,
                    _Fields.METADATA: manifest.metadata,
                    _Fields.VERSION: record.version + 1,
                }
            )
        if request.display_name is not None:
            changes[_Fields.DISPLAY_NAME] = request.display_name
        if request.enabled is not None:
            changes[_Fields.ENABLED] = request.enabled
        if request.scope is not None:
            changes[_Fields.SCOPE] = request.scope
        updated = record.model_copy(update=changes)
        with self.store.transaction() as conn:
            self.store.update_skill(updated, conn=conn)
            self._audit(updated, "skill_updated", conn=conn)
        return SkillResponse.from_record(updated)

    def delete_skill(self, *, org_id: str, user_id: str, skill_id: str) -> bool:
        self._ensure_preloaded_skills(org_id=org_id, user_id=user_id)
        record = self._require_owned_skill(
            org_id=org_id, user_id=user_id, skill_id=skill_id
        )
        if record.source_type is SkillSourceType.PRELOADED:
            raise ValueError("Preloaded skills cannot be deleted")
        with self.store.transaction() as conn:
            deleted = self.store.delete_skill(
                org_id=org_id, user_id=user_id, skill_id=skill_id, conn=conn
            )
            if deleted:
                self._audit(record, "skill_deleted", conn=conn)
        return deleted

    def list_internal_cards(
        self, *, org_id: str, user_id: str
    ) -> InternalSkillListResponse:
        self._ensure_preloaded_skills(org_id=org_id, user_id=user_id)
        return InternalSkillListResponse(
            skills=tuple(
                InternalSkillCard(
                    skill_id=record.skill_id,
                    name=record.name,
                    display_name=record.display_name,
                    description=record.description,
                    virtual_path=record.virtual_path,
                    scope=record.scope,
                    source_type=record.source_type,
                    version=record.version,
                    allowed_tools=record.allowed_tools,
                    enabled=record.enabled,
                )
                for record in self.store.list_skills(
                    org_id=org_id,
                    user_id=user_id,
                    include_disabled=False,
                )
            )
        )

    def get_internal_bundle(
        self,
        *,
        org_id: str,
        user_id: str,
        skill_id: str,
    ) -> InternalSkillBundle:
        self._ensure_preloaded_skills(org_id=org_id, user_id=user_id)
        record = self._require_visible_skill(
            org_id=org_id, user_id=user_id, skill_id=skill_id
        )
        if not record.enabled:
            raise ValueError("Skill is disabled")
        return InternalSkillBundle(
            skill_id=record.skill_id,
            name=record.name,
            display_name=record.display_name,
            description=record.description,
            markdown=record.markdown,
            virtual_path=record.virtual_path,
            version=record.version,
            allowed_tools=record.allowed_tools,
            metadata=record.metadata,
        )

    def get_internal_bundle_by_name(
        self,
        *,
        org_id: str,
        user_id: str,
        name: str,
    ) -> InternalSkillBundle:
        self._ensure_preloaded_skills(org_id=org_id, user_id=user_id)
        record = self.store.get_skill_by_name(
            org_id=org_id,
            user_id=user_id,
            name=Validators.normalize_skill_slug(name),
        )
        if record is None or not record.enabled:
            raise ValueError("Skill was not found for this scope")
        return self.get_internal_bundle(
            org_id=org_id, user_id=user_id, skill_id=record.skill_id
        )

    def seed_preloaded_skills(self, *, org_id: str, user_id: str) -> None:
        """Public entry point for startup hooks that pre-seed a known scope."""
        self._ensure_preloaded_skills(org_id=org_id, user_id=user_id)

    def _ensure_preloaded_skills(self, *, org_id: str, user_id: str) -> None:
        scope_key = (org_id, user_id)
        if scope_key in self._seeded_scopes:
            return
        for markdown in PRELOADED_SKILL_MARKDOWNS:
            manifest = SkillMarkdownParser.parse_manifest(markdown)
            existing = self.store.get_skill_by_name(
                org_id=org_id,
                user_id=user_id,
                name=manifest.name,
            )
            if existing is None:
                record = SkillRecord(
                    skill_id=self._preloaded_skill_id(
                        org_id=org_id,
                        user_id=user_id,
                        name=manifest.name,
                    ),
                    org_id=org_id,
                    user_id=user_id,
                    name=manifest.name,
                    display_name=self._display_name_from_slug(manifest.name),
                    description=manifest.description,
                    markdown=markdown,
                    virtual_path=self._preloaded_virtual_path(manifest.name),
                    source_type=SkillSourceType.PRELOADED,
                    allowed_tools=manifest.allowed_tools,
                    compatibility=manifest.compatibility,
                    metadata=manifest.metadata,
                )
                try:
                    with self.store.transaction() as conn:
                        self.store.create_skill(record, conn=conn)
                        self._audit(record, "skill_preloaded", conn=conn)
                except Exception as ex:
                    # First-boot seeding race: two concurrent first requests
                    # both pass the get-by-name check (TOCTOU) and the loser
                    # hits the (org_id, user_id, name) unique constraint. The
                    # winner seeded identical manifest content, so losing is
                    # success — swallow ONLY the duplicate-key case; anything
                    # else propagates untouched.
                    if not _is_unique_violation(ex):
                        raise
                continue
            if existing.source_type is not SkillSourceType.PRELOADED:
                continue
            changes: dict[str, object] = {}
            if existing.markdown != markdown:
                changes[_Fields.MARKDOWN] = markdown
                changes[_Fields.VERSION] = existing.version + 1
            if existing.description != manifest.description:
                changes[_Fields.DESCRIPTION] = manifest.description
            if existing.allowed_tools != manifest.allowed_tools:
                changes[_Fields.ALLOWED_TOOLS] = manifest.allowed_tools
            if existing.compatibility != manifest.compatibility:
                changes[_Fields.COMPATIBILITY] = manifest.compatibility
            if existing.metadata != manifest.metadata:
                changes[_Fields.METADATA] = manifest.metadata
            if changes:
                changes[_Fields.UPDATED_AT] = datetime.now(timezone.utc)
                with self.store.transaction() as conn:
                    self.store.update_skill(
                        existing.model_copy(update=changes), conn=conn
                    )
        self._seeded_scopes.add(scope_key)

    def _require_visible_skill(
        self, *, org_id: str, user_id: str, skill_id: str
    ) -> SkillRecord:
        record = self.store.get_skill(org_id=org_id, skill_id=skill_id)
        if record is None or (record.user_id != user_id and record.scope != "org"):
            raise ValueError("Skill was not found for this scope")
        return record

    def _require_owned_skill(
        self, *, org_id: str, user_id: str, skill_id: str
    ) -> SkillRecord:
        record = self.store.get_skill(org_id=org_id, skill_id=skill_id)
        if record is None or record.user_id != user_id:
            raise ValueError("Skill was not found for this user")
        return record

    def _audit(
        self,
        record: SkillRecord,
        action: str,
        *,
        conn: Any | None = None,
    ) -> None:
        self.store.append_skill_audit(
            SkillAuditEventRecord(
                org_id=record.org_id,
                user_id=record.user_id,
                skill_id=record.skill_id,
                action=action,
                metadata={
                    _Fields.NAME: record.name,
                    _Fields.VERSION: record.version,
                },
            ),
            conn=conn,
        )

    @classmethod
    def _display_name_from_slug(cls, name: str) -> str:
        return name.replace("_", " ").replace("-", " ").title()

    @classmethod
    def _virtual_path(cls, *, org_id: str, user_id: str, name: str) -> str:
        return f"/skills/org/{org_id}/user/{user_id}/{name}/SKILL.md"

    @classmethod
    def _preloaded_virtual_path(cls, name: str) -> str:
        return f"/skills/preloaded/{name}/SKILL.md"

    @classmethod
    def _preloaded_skill_id(cls, *, org_id: str, user_id: str, name: str) -> str:
        return f"preloaded:{org_id}:{user_id}:{name}"

    @classmethod
    def _default_store(cls) -> InMemorySkillStore | PostgresSkillStore:
        database_url = os.environ.get("DATABASE_URL", "").strip()
        if database_url:
            pool = PostgresConnectionPool.shared(database_url)
            return PostgresSkillStore(pool=pool)
        return InMemorySkillStore()


class ToolCatalogService:
    """Aggregates user-installed skills and registered MCP servers into the
    sectioned listing the composer Tools popover renders.

    The two underlying stores own their own access checks (caller-supplied
    org/user are normalized to the verified identity by the route layer).
    This service does not own a store of its own — it is a thin projection
    that tags each row with a :class:`ToolKind` discriminator so the
    frontend's Skills / MCPs partition has a single source of truth.

    Only **invocable** entries are returned: enabled skills, and MCP servers
    that are enabled and authenticated. Listing an unauthenticated MCP here
    would put a row in the composer the agent can't actually use; install
    and connect flows live elsewhere (``/v1/mcp/catalog``, the connector
    popover).
    """

    def __init__(
        self,
        *,
        mcp_service: McpRegistryService,
        skill_service: SkillRegistryService,
    ) -> None:
        self._mcp = mcp_service
        self._skills = skill_service

    def list_tools(self, *, org_id: str, user_id: str) -> ToolListResponse:
        # Route through ``list_internal_cards`` on each registry so the
        # invariants those methods own (preloaded skill seeding, enabled
        # filter on skills) are preserved instead of duplicated here.
        skill_cards = self._skills.list_internal_cards(
            org_id=org_id, user_id=user_id
        ).skills
        skill_entries = [
            ToolListEntry(
                name=card.name,
                label=card.display_name,
                description=card.description or None,
                kind=ToolKind.SKILL,
            )
            for card in skill_cards
        ]
        mcp_entries = [
            ToolListEntry(
                name=record.server_id,
                label=record.display_name,
                description=record.description or None,
                kind=ToolKind.MCP,
            )
            for record in self._mcp.store.list_servers(org_id=org_id, user_id=user_id)
            if record.enabled and record.auth_state == McpAuthState.AUTHENTICATED
        ]
        # Stable order: skills first (the popover renders them on top), then
        # MCPs. Within each kind, sort by label for deterministic UX.
        skill_entries.sort(key=lambda e: e.label.casefold())
        mcp_entries.sort(key=lambda e: e.label.casefold())
        return ToolListResponse(tools=tuple(skill_entries + mcp_entries))


class SkillMarkdownParser:
    """Minimal SKILL.md frontmatter parser for backend validation."""

    _MANIFEST_KEYS = frozenset(
        {
            _Fields.NAME,
            _Fields.DESCRIPTION,
            _Fields.LICENSE,
            _Fields.COMPATIBILITY,
            _Fields.ALLOWED_TOOLS,
            _Fields.METADATA,
        }
    )

    @classmethod
    def parse_manifest(cls, markdown: str) -> SkillManifestFields:
        frontmatter = cls._frontmatter(markdown)
        raw = cls._parse_fields(frontmatter)
        metadata = dict(raw.get(_Fields.METADATA) or {})
        for key in tuple(raw):
            if key not in cls._MANIFEST_KEYS:
                value = raw.pop(key)
                if isinstance(value, str | int | float | bool) or value is None:
                    metadata[key] = value
        return SkillManifestFields(
            name=str(raw.get(_Fields.NAME, "")),
            description=str(raw.get(_Fields.DESCRIPTION, "")),
            license=raw.get(_Fields.LICENSE)
            if isinstance(raw.get(_Fields.LICENSE), str)
            else None,
            compatibility=tuple(
                str(item) for item in cls._list(raw.get(_Fields.COMPATIBILITY))
            ),
            allowed_tools=tuple(
                Validators.normalize_skill_slug(item)
                for item in cls._list(raw.get(_Fields.ALLOWED_TOOLS))
            ),
            metadata=metadata,
        )

    @classmethod
    def _frontmatter(cls, markdown: str) -> str:
        lines = markdown.splitlines()
        if not lines or lines[0].strip() != "---":
            raise ValueError("Skill markdown must start with YAML frontmatter")
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                frontmatter = "\n".join(lines[1:index])
                if not frontmatter.strip():
                    raise ValueError("Skill frontmatter must not be empty")
                return frontmatter
        raise ValueError("Skill markdown must close its YAML frontmatter block")

    @classmethod
    def _parse_fields(cls, frontmatter: str) -> dict[str, object]:
        try:
            parsed = yaml.safe_load(frontmatter)
        except yaml.YAMLError as exc:
            raise ValueError("Skill frontmatter contains malformed YAML") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Skill frontmatter contains malformed YAML")
        return parsed

    @classmethod
    def _list(cls, value: object) -> tuple[object, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            raise ValueError("Skill manifest list fields must not be strings")
        try:
            return tuple(value)  # type: ignore[arg-type]
        except TypeError as exc:
            raise ValueError("Skill manifest list fields must be iterable") from exc


class DeployAuditService:
    """Records CI-driven deploy audit events under the verified caller identity.

    Tenant scoping: the caller's verified ``org_id`` (from the service token + headers) is
    used as the audit row's ``org_id``. The body's ``tenant_id`` MUST equal that ``org_id``;
    otherwise the request is rejected as a tenant boundary violation.
    """

    def __init__(self, *, store: InMemoryDeployAuditStore | None = None) -> None:
        self.store = store or InMemoryDeployAuditStore()

    def record(
        self,
        *,
        org_id: str,
        user_id: str,
        request: DeployAuditRequest,
    ) -> DeployAuditEventResponse:
        if request.tenant_id != org_id:
            raise ValueError(
                "tenant_id in body must match the caller's verified org_id",
            )
        record = DeployAuditEventRecord(
            org_id=org_id,
            user_id=user_id,
            tenant_id=request.tenant_id,
            environment=request.environment,
            release_sha=request.release_sha,
            image_digests=list(request.image_digests),
            approver=request.approver,
            workflow_run_url=request.workflow_run_url,
            started_at=request.started_at,
            completed_at=request.completed_at,
            outcome=request.outcome,
            force_deploy=request.force_deploy,
        )
        appended = self.store.append_deploy_audit(record)
        return DeployAuditEventResponse(
            audit_id=appended.audit_id,
            received_at=appended.created_at,
        )

"""Product-owned MCP registry and OAuth orchestration service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os

import yaml
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from backend_app.identity._pkce import compute_challenge, generate_verifier

from backend_app.contracts import (
    AuditEventRecord,
    CreateMcpServerRequest,
    DeployAuditEventRecord,
    DeployAuditEventResponse,
    DeployAuditRequest,
    CreateSkillRequest,
    InternalMcpAuthRequest,
    InternalMcpClientSession,
    InternalMcpRpcRequest,
    InternalMcpRpcResponse,
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
    McpOAuthClientConfig,
    McpOAuthClientRequest,
    McpServerHealth,
    McpServerListResponse,
    McpServerRecord,
    McpServerResponse,
    OAuthTokenRequest,
    SkillAuditEventRecord,
    SkillListResponse,
    SkillManifestFields,
    SkillRecord,
    SkillResponse,
    SkillSourceType,
    UpdateMcpServerRequest,
    UpdateSkillRequest,
    TokenEnvelope,
    Validators,
    _Fields,
)
from backend_app.mcp_catalog import DEFAULT_CATALOG, CatalogEntry
from backend_app.mcp_oauth import RemoteMcpOAuthClient
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


class Keys:
    """Stable keys and wire values used by backend MCP service calls."""

    class ContentType:
        EVENT_STREAM = "text/event-stream"
        JSON = "application/json"
        JSON_OR_EVENT_STREAM = "application/json, text/event-stream"

    class Encoding:
        UTF_8 = "utf-8"

    class Header:
        ACCEPT = "accept"
        AUTHORIZATION = "authorization"
        CONTENT_TYPE = "content-type"

    class HttpMethod:
        POST = "POST"

    class Sse:
        DATA_PREFIX = "data:"
        DONE = "[DONE]"


class Values:
    """Stable values used by backend MCP service calls."""

    class Auth:
        BEARER = "Bearer"


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


class McpRegistryService:
    """Owns MCP registration, auth state, and backend-only credentials."""

    def __init__(
        self,
        *,
        store: InMemoryMcpStore | PostgresMcpStore | None = None,
        token_vault: TokenVault | None = None,
        token_exchanger: OAuthTokenExchanger | None = None,
        oauth_client: OAuthDiscoveryClient | None = None,
        auth_session_ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        self.store = store or self._default_store()
        self.token_vault = token_vault or TokenVaultFactory.create()
        self.oauth_client = oauth_client or HttpOAuthTokenExchanger()
        self.token_exchanger = token_exchanger or self.oauth_client
        self.auth_session_ttl = auth_session_ttl

    def create_server(self, request: CreateMcpServerRequest) -> McpServerResponse:
        display_name = request.display_name or self._display_name_from_url(request.url)
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
        with self.store.transaction() as conn:
            self.store.create_server(record, conn=conn)
            self._audit(record, "mcp_server_created", conn=conn)
        return McpServerResponse.from_record(record)

    def list_servers(self, *, org_id: str, user_id: str) -> McpServerListResponse:
        existing = self.store.list_servers(org_id=org_id, user_id=user_id)
        # First-time seed: when the user has zero servers, pre-add the
        # well-known catalog as ``enabled=False`` so the chat agent does
        # not auto-recommend connectors the user hasn't reviewed. After
        # the first list, removals are honoured (we never re-seed
        # automatically); use ``reset_catalog`` for an explicit refresh.
        if not existing:
            seeded = self._seed_catalog(org_id=org_id, user_id=user_id)
            if seeded:
                existing = self.store.list_servers(org_id=org_id, user_id=user_id)
        return McpServerListResponse(
            servers=tuple(self._response_from_record(record) for record in existing)
        )

    def reset_catalog(self, *, org_id: str, user_id: str) -> McpServerListResponse:
        """Idempotently re-add any missing catalog entries for the user.

        Honours existing entries (matched by stable ``seed:<slug>`` id)
        and never overwrites the user's enabled/auth state. Removed
        catalog entries come back as ``enabled=False``.
        """

        self._seed_catalog(org_id=org_id, user_id=user_id)
        return self.list_servers(org_id=org_id, user_id=user_id)

    def _seed_catalog(self, *, org_id: str, user_id: str) -> bool:
        """Insert any missing catalog entries. Returns True if any rows
        were added.
        """

        existing_ids = {
            record.server_id
            for record in self.store.list_servers(org_id=org_id, user_id=user_id)
        }
        added = False
        with self.store.transaction() as conn:
            for entry in DEFAULT_CATALOG:
                if entry.server_id in existing_ids:
                    continue
                record = self._record_from_catalog(
                    entry, org_id=org_id, user_id=user_id
                )
                self.store.create_server(record, conn=conn)
                self._audit(record, "mcp_server_seeded", conn=conn)
                added = True
        return added

    @staticmethod
    def _record_from_catalog(
        entry: CatalogEntry, *, org_id: str, user_id: str
    ) -> McpServerRecord:
        return McpServerRecord(
            server_id=entry.server_id,
            org_id=org_id,
            user_id=user_id,
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
            health=McpServerHealth.DISABLED,
            enabled=False,
        )

    def delete_server(self, *, org_id: str, user_id: str, server_id: str) -> bool:
        record = self._server_for_user(
            org_id=org_id, user_id=user_id, server_id=server_id
        )
        if record is None:
            return False
        with self.store.transaction() as conn:
            deleted = self.store.delete_server(
                org_id=org_id, server_id=server_id, conn=conn
            )
            if deleted:
                self._audit(record, "mcp_server_deleted", conn=conn)
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

        with self.store.transaction() as conn:
            updated = self._update_record(record, conn=conn, **changes)
            self._audit(updated, "mcp_server_updated", conn=conn)
        return McpServerResponse.from_record(updated)

    def skip_auth(
        self, *, org_id: str, user_id: str, server_id: str
    ) -> McpServerResponse:
        record = self._require_server_for_user(
            org_id=org_id, user_id=user_id, server_id=server_id
        )
        with self.store.transaction() as conn:
            updated = self._update_record(
                record, conn=conn, auth_state=McpAuthState.AUTH_SKIPPED
            )
            self._audit(updated, "mcp_auth_skipped", conn=conn)
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
            with self.store.transaction() as conn:
                updated = self._update_record(
                    record,
                    conn=conn,
                    auth_state=McpAuthState.AUTH_UNSUPPORTED,
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
        with self.store.transaction() as conn:
            updated = self._update_record(
                record,
                conn=conn,
                auth_state=next_auth_state,
                last_discovery=authorization.discovery,
                required_scopes=authorization.required_scopes,
            )
            self._audit(updated, "mcp_auth_started", conn=conn)
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
            with self.store.transaction() as conn:
                updated = self._update_record(
                    record, conn=conn, auth_state=McpAuthState.AUTH_FAILED
                )
                self._audit(updated, "mcp_auth_failed", conn=conn)
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
        with self.store.transaction() as conn:
            self.store.put_token(token_envelope, conn=conn)
            updated = self._update_record(
                record, conn=conn, auth_state=McpAuthState.AUTHENTICATED
            )
            self._audit(updated, "mcp_auth_completed", conn=conn)
        return McpServerResponse.from_record(updated)

    def list_internal_cards(
        self, *, org_id: str, user_id: str
    ) -> InternalMcpServerListResponse:
        cards = []
        for record in self.store.list_servers(org_id=org_id, user_id=user_id):
            if not record.enabled:
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
                )
            )
        return InternalMcpServerListResponse(servers=tuple(cards))

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
        token = self.store.get_token(server_id=server_id)
        credential_ref = token.connection_id if token is not None else None
        return InternalMcpClientSession(
            server_id=record.server_id,
            url=record.url,
            transport=record.transport,
            auth_state=self._effective_auth_state(record),
            credential_ref=credential_ref,
        )

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
        token = self._require_valid_token(record)
        access_token = self.token_vault.decrypt(token.encrypted_access_token)
        payload = self._post_remote_mcp_rpc(record.url, request.payload, access_token)
        return InternalMcpRpcResponse(payload=payload)

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
        with self.store.transaction() as conn:
            self.store.put_token(token_envelope, conn=conn)
            updated = self._update_record(
                record, conn=conn, auth_state=McpAuthState.AUTHENTICATED
            )
            self._audit(updated, "mcp_token_upserted", conn=conn)
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
        updated = self.store.put_token(
            TokenEnvelope(
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
        )
        return updated

    @staticmethod
    def _post_remote_mcp_rpc(
        server_url: str, payload: dict[str, object], access_token: str
    ) -> dict[str, object]:
        request = Request(
            server_url,
            data=json.dumps(payload).encode(Keys.Encoding.UTF_8),
            headers={
                Keys.Header.ACCEPT: Keys.ContentType.JSON_OR_EVENT_STREAM,
                Keys.Header.AUTHORIZATION: f"{Values.Auth.BEARER} {access_token}",
                Keys.Header.CONTENT_TYPE: Keys.ContentType.JSON,
            },
            method=Keys.HttpMethod.POST,
        )
        try:
            with urlopen(request, timeout=30) as response:
                decoded = McpRegistryService._decode_remote_mcp_response(
                    response.read().decode(Keys.Encoding.UTF_8),
                    response.headers.get(Keys.Header.CONTENT_TYPE, ""),
                )
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise ValueError("MCP server rejected the stored OAuth token") from exc
            raise ValueError("MCP server request failed") from exc
        except (URLError, TimeoutError) as exc:
            raise ValueError("MCP server is unavailable") from exc
        if not isinstance(decoded, dict):
            raise ValueError("MCP server returned an invalid JSON-RPC response")
        return decoded

    @staticmethod
    def _decode_remote_mcp_response(raw: str, content_type: str) -> object:
        if content_type.lower().startswith(Keys.ContentType.EVENT_STREAM):
            for line in raw.splitlines():
                if not line.startswith(Keys.Sse.DATA_PREFIX):
                    continue
                data = line.removeprefix(Keys.Sse.DATA_PREFIX).strip()
                if not data or data == Keys.Sse.DONE:
                    continue
                return json.loads(data)
            return {}
        return json.loads(raw or "{}")

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
                with self.store.transaction() as conn:
                    self.store.create_skill(record, conn=conn)
                    self._audit(record, "skill_preloaded", conn=conn)
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

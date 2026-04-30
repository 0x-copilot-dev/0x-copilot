"""Product-owned MCP registry and OAuth orchestration service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import base64
import hashlib
from urllib.parse import urlencode, urlsplit

from backend_app.contracts import (
    AuditEventRecord,
    CreateMcpServerRequest,
    InternalMcpAuthRequest,
    InternalMcpClientSession,
    InternalMcpServerCard,
    InternalMcpServerListResponse,
    McpAuthCallbackRequest,
    McpAuthMode,
    McpAuthSessionRecord,
    McpAuthStartRequest,
    McpAuthStartResponse,
    McpAuthState,
    McpServerHealth,
    McpServerListResponse,
    McpServerRecord,
    McpServerResponse,
    OAuthTokenRequest,
    TokenEnvelope,
)
from backend_app.store import InMemoryMcpStore
from backend_app.token_vault import LocalTokenVault, TokenVault


class McpRegistryService:
    """Owns MCP registration, auth state, and backend-only credentials."""

    def __init__(
        self,
        *,
        store: InMemoryMcpStore | None = None,
        token_vault: TokenVault | None = None,
        auth_session_ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        self.store = store or InMemoryMcpStore()
        self.token_vault = token_vault or LocalTokenVault()
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
        )
        self.store.create_server(record)
        self._audit(record, "mcp_server_created")
        return McpServerResponse.from_record(record)

    def list_servers(self, *, org_id: str, user_id: str) -> McpServerListResponse:
        return McpServerListResponse(
            servers=tuple(
                McpServerResponse.from_record(record)
                for record in self.store.list_servers(org_id=org_id, user_id=user_id)
            )
        )

    def delete_server(self, *, org_id: str, user_id: str, server_id: str) -> bool:
        record = self._server_for_user(org_id=org_id, user_id=user_id, server_id=server_id)
        if record is None:
            return False
        deleted = self.store.delete_server(org_id=org_id, server_id=server_id)
        if deleted:
            self._audit(record, "mcp_server_deleted")
        return deleted

    def skip_auth(self, *, org_id: str, user_id: str, server_id: str) -> McpServerResponse:
        record = self._require_server_for_user(org_id=org_id, user_id=user_id, server_id=server_id)
        updated = self._update_record(record, auth_state=McpAuthState.AUTH_SKIPPED)
        self._audit(updated, "mcp_auth_skipped")
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
            updated = self._update_record(record, auth_state=McpAuthState.AUTH_UNSUPPORTED)
            self._audit(updated, "mcp_auth_unsupported")
            raise ValueError("MCP server does not support OAuth authentication")

        verifier = base64.urlsafe_b64encode(hashlib.sha256(record.server_id.encode()).digest()).decode(
            "ascii"
        ).rstrip("=")
        expires_at = datetime.now(UTC) + self.auth_session_ttl
        auth_url = self._oauth_authorization_url(record=record, redirect_uri=request.redirect_uri)
        session = McpAuthSessionRecord(
            server_id=record.server_id,
            org_id=record.org_id,
            user_id=record.user_id,
            code_verifier=verifier,
            redirect_uri=request.redirect_uri,
            auth_url=auth_url,
            expires_at=expires_at,
        )
        session = self.store.create_auth_session(session)
        auth_url = self._oauth_authorization_url(
            record=record,
            redirect_uri=request.redirect_uri,
            state=session.state,
            code_challenge=self._code_challenge(session.code_verifier),
        )
        session = session.model_copy(update={"auth_url": auth_url})
        self.store.create_auth_session(session)
        updated = self._update_record(record, auth_state=McpAuthState.AUTH_PENDING)
        self._audit(updated, "mcp_auth_started")
        return McpAuthStartResponse(
            server_id=record.server_id,
            auth_url=auth_url,
            expires_at=session.expires_at,
        )

    def complete_auth(self, request: McpAuthCallbackRequest) -> McpServerResponse:
        session = self.store.pop_auth_session(state=request.state)
        if session is None or session.expires_at < datetime.now(UTC):
            raise ValueError("MCP auth session is invalid or expired")
        record = self._require_server_for_user(
            org_id=session.org_id,
            user_id=session.user_id,
            server_id=session.server_id,
        )
        self.store.put_token(
            TokenEnvelope(
                server_id=record.server_id,
                org_id=record.org_id,
                user_id=record.user_id,
                encrypted_access_token=self.token_vault.encrypt(f"access:{request.code}"),
                encrypted_refresh_token=self.token_vault.encrypt(f"refresh:{request.code}"),
            )
        )
        updated = self._update_record(record, auth_state=McpAuthState.AUTHENTICATED)
        self._audit(updated, "mcp_auth_completed")
        return McpServerResponse.from_record(updated)

    def list_internal_cards(self, *, org_id: str, user_id: str) -> InternalMcpServerListResponse:
        cards = []
        for record in self.store.list_servers(org_id=org_id, user_id=user_id):
            if not record.enabled:
                continue
            cards.append(
                InternalMcpServerCard(
                    server_id=record.server_id,
                    name=record.name,
                    display_name=record.display_name,
                    short_description=self._card_description(record),
                    transport=record.transport,
                    auth_mode=record.auth_mode,
                    auth_state=record.auth_state,
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
        record = self._require_server_for_user(org_id=org_id, user_id=user_id, server_id=server_id)
        token = self.store.get_token(server_id=server_id)
        credential_ref = token.connection_id if token is not None else None
        return InternalMcpClientSession(
            server_id=record.server_id,
            url=record.url,
            transport=record.transport,
            auth_state=record.auth_state,
            credential_ref=credential_ref,
        )

    def upsert_token_for_test(
        self,
        *,
        org_id: str,
        user_id: str,
        server_id: str,
        request: OAuthTokenRequest,
    ) -> McpServerResponse:
        record = self._require_server_for_user(org_id=org_id, user_id=user_id, server_id=server_id)
        self.store.put_token(
            TokenEnvelope(
                server_id=record.server_id,
                org_id=record.org_id,
                user_id=record.user_id,
                encrypted_access_token=self.token_vault.encrypt(request.access_token),
                encrypted_refresh_token=(
                    self.token_vault.encrypt(request.refresh_token)
                    if request.refresh_token is not None
                    else None
                ),
                expires_at=request.expires_at,
            )
        )
        updated = self._update_record(record, auth_state=McpAuthState.AUTHENTICATED)
        self._audit(updated, "mcp_token_upserted")
        return McpServerResponse.from_record(updated)

    def _update_record(self, record: McpServerRecord, **changes: object) -> McpServerRecord:
        updated = record.model_copy(update={**changes, "updated_at": datetime.now(UTC)})
        return self.store.update_server(updated)

    def _require_server_for_user(self, *, org_id: str, user_id: str, server_id: str) -> McpServerRecord:
        record = self._server_for_user(org_id=org_id, user_id=user_id, server_id=server_id)
        if record is None:
            raise ValueError("MCP server was not found for this scope")
        return record

    def _server_for_user(self, *, org_id: str, user_id: str, server_id: str) -> McpServerRecord | None:
        record = self.store.get_server(org_id=org_id, server_id=server_id)
        if record is None or record.user_id != user_id:
            return None
        return record

    def _audit(self, record: McpServerRecord, action: str) -> None:
        self.store.append_audit(
            AuditEventRecord(
                org_id=record.org_id,
                user_id=record.user_id,
                server_id=record.server_id,
                action=action,
                metadata={"auth_state": record.auth_state.value, "health": record.health.value},
            )
        )

    @classmethod
    def _display_name_from_url(cls, url: str) -> str:
        host = urlsplit(url).hostname or "MCP Server"
        return host.replace(".", " ").title()

    @classmethod
    def _stable_name(cls, display_name: str) -> str:
        normalized = display_name.lower().replace(" ", "_").replace("-", "_")
        return "".join(char for char in normalized if char.isalnum() or char == "_").strip("_")

    @classmethod
    def _card_description(cls, record: McpServerRecord) -> str:
        if record.auth_state in {McpAuthState.AUTHENTICATED, McpAuthState.AUTH_SKIPPED}:
            return f"{record.display_name} MCP server."
        return f"{record.display_name} MCP server requires authentication before tools can load."

    @classmethod
    def _oauth_authorization_url(
        cls,
        *,
        record: McpServerRecord,
        redirect_uri: str,
        state: str | None = None,
        code_challenge: str | None = None,
    ) -> str:
        query = {
            "client_id": "enterprise-search",
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "mcp",
        }
        if state is not None:
            query["state"] = state
        if code_challenge is not None:
            query["code_challenge"] = code_challenge
            query["code_challenge_method"] = "S256"
        return f"{record.url.rstrip('/')}/oauth/authorize?{urlencode(query)}"

    @classmethod
    def _code_challenge(cls, verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

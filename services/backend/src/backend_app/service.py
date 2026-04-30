"""Product-owned MCP registry and OAuth orchestration service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import base64
import hashlib
import json
import os
from secrets import token_urlsafe
from typing import Protocol
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from backend_app.contracts import (
    AuditEventRecord,
    CreateMcpServerRequest,
    CreateSkillRequest,
    InternalMcpAuthRequest,
    InternalMcpClientSession,
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
    UpdateMcpServerRequest,
    UpdateSkillRequest,
    TokenEnvelope,
    normalize_skill_slug,
)
from backend_app.store import InMemoryMcpStore, InMemorySkillStore, PostgresSkillStore
from backend_app.token_vault import TokenVault, TokenVaultFactory


class OAuthTokenExchanger(Protocol):
    """Exchange an OAuth authorization code for backend-held connector tokens."""

    def exchange_code(
        self,
        *,
        record: McpServerRecord,
        session: McpAuthSessionRecord,
        code: str,
    ) -> OAuthTokenRequest:
        """Return tokens for a verified OAuth callback."""


class HttpOAuthTokenExchanger:
    """HTTP form-post OAuth code exchanger for MCP servers."""

    def exchange_code(
        self,
        *,
        record: McpServerRecord,
        session: McpAuthSessionRecord,
        code: str,
    ) -> OAuthTokenRequest:
        token_url = f"{record.url.rstrip('/')}/oauth/token"
        body = urlencode(
            {
                "grant_type": "authorization_code",
                "client_id": "enterprise-search",
                "code": code,
                "redirect_uri": session.redirect_uri,
                "code_verifier": session.code_verifier,
            }
        ).encode("utf-8")
        request = Request(
            token_url,
            data=body,
            headers={
                "accept": "application/json",
                "content-type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("OAuth token endpoint returned an invalid response")
        expires_at = self._expires_at(payload.get("expires_in"))
        return OAuthTokenRequest(
            access_token=self._required_text(payload, "access_token"),
            refresh_token=self._optional_text(payload.get("refresh_token")),
            expires_at=expires_at,
        )

    @staticmethod
    def _required_text(payload: dict[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"OAuth token endpoint response missing {key}")
        return value

    @staticmethod
    def _optional_text(value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("OAuth token endpoint response has invalid refresh_token")
        return value

    @staticmethod
    def _expires_at(value: object) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, int) or value <= 0:
            raise ValueError("OAuth token endpoint response has invalid expires_in")
        return datetime.now(UTC) + timedelta(seconds=value)


class McpRegistryService:
    """Owns MCP registration, auth state, and backend-only credentials."""

    def __init__(
        self,
        *,
        store: InMemoryMcpStore | None = None,
        token_vault: TokenVault | None = None,
        token_exchanger: OAuthTokenExchanger | None = None,
        auth_session_ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        self.store = store or self._default_store()
        self.token_vault = token_vault or TokenVaultFactory.create()
        self.token_exchanger = token_exchanger or HttpOAuthTokenExchanger()
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
        record = self._server_for_user(
            org_id=org_id, user_id=user_id, server_id=server_id
        )
        if record is None:
            return False
        deleted = self.store.delete_server(org_id=org_id, server_id=server_id)
        if deleted:
            self._audit(record, "mcp_server_deleted")
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
            changes["display_name"] = request.display_name
        if request.enabled is not None:
            changes["enabled"] = request.enabled
            if not request.enabled:
                changes["health"] = McpServerHealth.DISABLED
            elif record.health is McpServerHealth.DISABLED:
                changes["health"] = McpServerHealth.HEALTHY
        if not changes:
            return McpServerResponse.from_record(record)

        updated = self._update_record(record, **changes)
        self._audit(updated, "mcp_server_updated")
        return McpServerResponse.from_record(updated)

    def skip_auth(
        self, *, org_id: str, user_id: str, server_id: str
    ) -> McpServerResponse:
        record = self._require_server_for_user(
            org_id=org_id, user_id=user_id, server_id=server_id
        )
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
            updated = self._update_record(
                record, auth_state=McpAuthState.AUTH_UNSUPPORTED
            )
            self._audit(updated, "mcp_auth_unsupported")
            raise ValueError("MCP server does not support OAuth authentication")

        verifier = token_urlsafe(64)
        expires_at = datetime.now(UTC) + self.auth_session_ttl
        auth_url = self._oauth_authorization_url(
            record=record, redirect_uri=request.redirect_uri
        )
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
        tokens = self.token_exchanger.exchange_code(
            record=record, session=session, code=request.code
        )
        self.store.put_token(
            TokenEnvelope(
                server_id=record.server_id,
                org_id=record.org_id,
                user_id=record.user_id,
                encrypted_access_token=self.token_vault.encrypt(tokens.access_token),
                encrypted_refresh_token=(
                    self.token_vault.encrypt(tokens.refresh_token)
                    if tokens.refresh_token is not None
                    else None
                ),
                expires_at=tokens.expires_at,
            )
        )
        updated = self._update_record(record, auth_state=McpAuthState.AUTHENTICATED)
        self._audit(updated, "mcp_auth_completed")
        return McpServerResponse.from_record(updated)

    def list_internal_cards(
        self, *, org_id: str, user_id: str
    ) -> InternalMcpServerListResponse:
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
        record = self._require_server_for_user(
            org_id=org_id, user_id=user_id, server_id=server_id
        )
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
        record = self._require_server_for_user(
            org_id=org_id, user_id=user_id, server_id=server_id
        )
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

    def _update_record(
        self, record: McpServerRecord, **changes: object
    ) -> McpServerRecord:
        updated = record.model_copy(update={**changes, "updated_at": datetime.now(UTC)})
        return self.store.update_server(updated)

    @classmethod
    def _default_store(cls) -> InMemoryMcpStore:
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

    def _audit(self, record: McpServerRecord, action: str) -> None:
        self.store.append_audit(
            AuditEventRecord(
                org_id=record.org_id,
                user_id=record.user_id,
                server_id=record.server_id,
                action=action,
                metadata={
                    "auth_state": record.auth_state.value,
                    "health": record.health.value,
                },
            )
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


class SkillRegistryService:
    """Owns user-created Skill markdown and runtime-visible Skill cards."""

    def __init__(
        self, *, store: InMemorySkillStore | PostgresSkillStore | None = None
    ) -> None:
        self.store = store or self._default_store()

    def create_skill(self, request: CreateSkillRequest) -> SkillResponse:
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
        self.store.create_skill(record)
        self._audit(record, "skill_created")
        return SkillResponse.from_record(record)

    def list_skills(self, *, org_id: str, user_id: str) -> SkillListResponse:
        return SkillListResponse(
            skills=tuple(
                SkillResponse.from_record(record)
                for record in self.store.list_skills(org_id=org_id, user_id=user_id)
            )
        )

    def get_skill(self, *, org_id: str, user_id: str, skill_id: str) -> SkillResponse:
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
        record = self._require_owned_skill(
            org_id=org_id, user_id=user_id, skill_id=skill_id
        )
        changes: dict[str, object] = {"updated_at": datetime.now(UTC)}
        if request.markdown is not None:
            manifest = SkillMarkdownParser.parse_manifest(request.markdown)
            if manifest.name != record.name:
                raise ValueError("Skill name cannot change after creation")
            changes.update(
                {
                    "description": manifest.description,
                    "markdown": request.markdown,
                    "allowed_tools": manifest.allowed_tools,
                    "compatibility": manifest.compatibility,
                    "metadata": manifest.metadata,
                    "version": record.version + 1,
                }
            )
        if request.display_name is not None:
            changes["display_name"] = request.display_name
        if request.enabled is not None:
            changes["enabled"] = request.enabled
        if request.scope is not None:
            changes["scope"] = request.scope
        updated = record.model_copy(update=changes)
        self.store.update_skill(updated)
        self._audit(updated, "skill_updated")
        return SkillResponse.from_record(updated)

    def delete_skill(self, *, org_id: str, user_id: str, skill_id: str) -> bool:
        record = self._require_owned_skill(
            org_id=org_id, user_id=user_id, skill_id=skill_id
        )
        deleted = self.store.delete_skill(
            org_id=org_id, user_id=user_id, skill_id=skill_id
        )
        if deleted:
            self._audit(record, "skill_deleted")
        return deleted

    def list_internal_cards(
        self, *, org_id: str, user_id: str
    ) -> InternalSkillListResponse:
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
        record = self.store.get_skill_by_name(
            org_id=org_id,
            user_id=user_id,
            name=normalize_skill_slug(name),
        )
        if record is None or not record.enabled:
            raise ValueError("Skill was not found for this scope")
        return self.get_internal_bundle(
            org_id=org_id, user_id=user_id, skill_id=record.skill_id
        )

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

    def _audit(self, record: SkillRecord, action: str) -> None:
        self.store.append_skill_audit(
            SkillAuditEventRecord(
                org_id=record.org_id,
                user_id=record.user_id,
                skill_id=record.skill_id,
                action=action,
                metadata={"name": record.name, "version": record.version},
            )
        )

    @classmethod
    def _display_name_from_slug(cls, name: str) -> str:
        return name.replace("_", " ").replace("-", " ").title()

    @classmethod
    def _virtual_path(cls, *, org_id: str, user_id: str, name: str) -> str:
        return f"/skills/org/{org_id}/user/{user_id}/{name}/SKILL.md"

    @classmethod
    def _default_store(cls) -> InMemorySkillStore | PostgresSkillStore:
        database_url = os.environ.get("DATABASE_URL", "").strip()
        if database_url:
            return PostgresSkillStore(database_url=database_url)
        return InMemorySkillStore()


class SkillMarkdownParser:
    """Minimal SKILL.md frontmatter parser for backend validation."""

    @classmethod
    def parse_manifest(cls, markdown: str) -> SkillManifestFields:
        frontmatter = cls._frontmatter(markdown)
        raw = cls._parse_fields(frontmatter)
        metadata = dict(raw.get("metadata") or {})
        for key in tuple(raw):
            if key not in {
                "name",
                "description",
                "license",
                "compatibility",
                "allowed_tools",
                "metadata",
            }:
                value = raw.pop(key)
                if isinstance(value, str | int | float | bool) or value is None:
                    metadata[key] = value
        return SkillManifestFields(
            name=str(raw.get("name", "")),
            description=str(raw.get("description", "")),
            license=raw.get("license") if isinstance(raw.get("license"), str) else None,
            compatibility=tuple(
                str(item) for item in cls._list(raw.get("compatibility"))
            ),
            allowed_tools=tuple(
                normalize_skill_slug(item)
                for item in cls._list(raw.get("allowed_tools"))
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
        parsed: dict[str, object] = {}
        lines = frontmatter.splitlines()
        index = 0
        while index < len(lines):
            line = lines[index]
            if not line.strip() or line.lstrip().startswith("#"):
                index += 1
                continue
            if line.startswith((" ", "\t")) or ":" not in line:
                raise ValueError("Skill frontmatter contains malformed YAML")
            key, raw_value = line.split(":", maxsplit=1)
            key = key.strip()
            value = raw_value.strip()
            if value:
                parsed[key] = cls._scalar_or_list(value)
                index += 1
                continue
            children: list[str] = []
            index += 1
            while index < len(lines) and (
                not lines[index].strip() or lines[index].startswith((" ", "\t"))
            ):
                children.append(lines[index])
                index += 1
            parsed[key] = cls._block(children)
        return parsed

    @classmethod
    def _block(cls, lines: list[str]) -> object:
        meaningful = [line.strip() for line in lines if line.strip()]
        if not meaningful:
            return None
        if all(line.startswith("- ") for line in meaningful):
            return [cls._scalar(line[2:].strip()) for line in meaningful]
        mapping: dict[str, object] = {}
        for line in meaningful:
            if ":" not in line:
                raise ValueError("Skill frontmatter contains unsupported nested YAML")
            key, value = line.split(":", maxsplit=1)
            mapping[key.strip()] = cls._scalar(value.strip())
        return mapping

    @classmethod
    def _scalar_or_list(cls, value: str) -> object:
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if not inner:
                return []
            return [cls._scalar(part.strip()) for part in inner.split(",")]
        return cls._scalar(value)

    @classmethod
    def _scalar(cls, value: str) -> object:
        stripped = value.strip()
        if (
            len(stripped) >= 2
            and stripped[0] == stripped[-1]
            and stripped[0] in {"'", '"'}
        ):
            return stripped[1:-1]
        lowered = stripped.lower()
        if lowered in {"null", "none", "~"}:
            return None
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        try:
            return int(stripped)
        except ValueError:
            pass
        try:
            return float(stripped)
        except ValueError:
            return stripped

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

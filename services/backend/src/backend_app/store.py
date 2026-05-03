"""Backend stores used by local development, tests, and production adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from typing import Any

from backend_app.contracts import (
    AuditEventRecord,
    DeployAuditEventRecord,
    McpAuthSessionRecord,
    McpOAuthClientConfig,
    McpServerRecord,
    SkillAuditEventRecord,
    SkillRecord,
    TokenEnvelope,
)


class PostgresConnectionPool:
    """Shared connection pool for all Postgres-backed stores.

    Uses the singleton pattern so multiple store instances share the same pool.
    Call ``close_shared`` during application shutdown.
    """

    _instance: PostgresConnectionPool | None = None

    def __init__(
        self, database_url: str, *, min_size: int = 2, max_size: int = 10
    ) -> None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        self._pool = ConnectionPool(
            conninfo=database_url,
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row},
        )

    def connection(self) -> Any:
        return self._pool.connection()

    def close(self) -> None:
        self._pool.close()

    @classmethod
    def shared(cls, database_url: str, **kwargs: Any) -> PostgresConnectionPool:
        if cls._instance is None:
            cls._instance = cls(database_url, **kwargs)
        return cls._instance

    @classmethod
    def close_shared(cls) -> None:
        if cls._instance is not None:
            cls._instance.close()
            cls._instance = None


@dataclass
class InMemoryMcpStore:
    servers: dict[str, McpServerRecord] = field(default_factory=dict)
    auth_sessions: dict[str, McpAuthSessionRecord] = field(default_factory=dict)
    tokens_by_server: dict[str, TokenEnvelope] = field(default_factory=dict)
    audit_events: list[AuditEventRecord] = field(default_factory=list)

    def create_server(self, record: McpServerRecord) -> McpServerRecord:
        self.servers[record.server_id] = record
        return record

    def update_server(self, record: McpServerRecord) -> McpServerRecord:
        self.servers[record.server_id] = record
        return record

    def get_server(self, *, org_id: str, server_id: str) -> McpServerRecord | None:
        record = self.servers.get(server_id)
        if record is None or record.org_id != org_id:
            return None
        return record

    def list_servers(self, *, org_id: str, user_id: str) -> tuple[McpServerRecord, ...]:
        return tuple(
            sorted(
                (
                    record
                    for record in self.servers.values()
                    if record.org_id == org_id and record.user_id == user_id
                ),
                key=lambda record: record.created_at,
            )
        )

    def delete_server(self, *, org_id: str, server_id: str) -> bool:
        record = self.get_server(org_id=org_id, server_id=server_id)
        if record is None:
            return False
        self.servers.pop(server_id, None)
        self.tokens_by_server.pop(server_id, None)
        return True

    def create_auth_session(self, record: McpAuthSessionRecord) -> McpAuthSessionRecord:
        self.auth_sessions[record.state] = record
        return record

    def pop_auth_session(self, *, state: str) -> McpAuthSessionRecord | None:
        return self.auth_sessions.pop(state, None)

    def put_token(self, record: TokenEnvelope) -> TokenEnvelope:
        self.tokens_by_server[record.server_id] = record
        return record

    def get_token(self, *, server_id: str) -> TokenEnvelope | None:
        return self.tokens_by_server.get(server_id)

    def append_audit(self, record: AuditEventRecord) -> AuditEventRecord:
        self.audit_events.append(record)
        return record


class PostgresMcpStore:
    """PostgreSQL-backed MCP registry store with connection pooling."""

    def __init__(self, *, pool: PostgresConnectionPool) -> None:
        self._pool = pool

    def create_server(self, record: McpServerRecord) -> McpServerRecord:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO mcp_servers (
                      server_id, org_id, user_id, name, display_name, url,
                      transport, auth_mode, auth_state, health, enabled,
                      required_scopes, last_discovery, oauth_client,
                      created_at, updated_at
                    ) VALUES (
                      %(server_id)s, %(org_id)s, %(user_id)s, %(name)s,
                      %(display_name)s, %(url)s, %(transport)s, %(auth_mode)s,
                      %(auth_state)s, %(health)s, %(enabled)s,
                      %(required_scopes)s, %(last_discovery)s, %(oauth_client)s,
                      %(created_at)s, %(updated_at)s
                    )
                    """,
                    self._server_params(record),
                )
        return record

    def update_server(self, record: McpServerRecord) -> McpServerRecord:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE mcp_servers
                    SET name = %(name)s,
                        display_name = %(display_name)s,
                        url = %(url)s,
                        transport = %(transport)s,
                        auth_mode = %(auth_mode)s,
                        auth_state = %(auth_state)s,
                        health = %(health)s,
                        enabled = %(enabled)s,
                        required_scopes = %(required_scopes)s,
                        last_discovery = %(last_discovery)s,
                        oauth_client = %(oauth_client)s,
                        updated_at = %(updated_at)s
                    WHERE org_id = %(org_id)s AND server_id = %(server_id)s
                    """,
                    self._server_params(record),
                )
        return record

    def get_server(self, *, org_id: str, server_id: str) -> McpServerRecord | None:
        return self._fetch_one_server(
            "SELECT * FROM mcp_servers WHERE org_id = %(org_id)s AND server_id = %(server_id)s",
            {"org_id": org_id, "server_id": server_id},
        )

    def list_servers(self, *, org_id: str, user_id: str) -> tuple[McpServerRecord, ...]:
        return self._fetch_many_servers(
            """
            SELECT * FROM mcp_servers
            WHERE org_id = %(org_id)s AND user_id = %(user_id)s
            ORDER BY created_at
            """,
            {"org_id": org_id, "user_id": user_id},
        )

    def delete_server(self, *, org_id: str, server_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM mcp_servers WHERE org_id = %(org_id)s AND server_id = %(server_id)s",
                    {"org_id": org_id, "server_id": server_id},
                )
                return cur.rowcount > 0

    def create_auth_session(self, record: McpAuthSessionRecord) -> McpAuthSessionRecord:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO mcp_auth_sessions (
                      session_id, server_id, org_id, user_id, state,
                      code_verifier, redirect_uri, auth_url,
                      expires_at, created_at
                    ) VALUES (
                      %(session_id)s, %(server_id)s, %(org_id)s, %(user_id)s,
                      %(state)s, %(code_verifier)s, %(redirect_uri)s, %(auth_url)s,
                      %(expires_at)s, %(created_at)s
                    )
                    """,
                    {
                        "session_id": record.session_id,
                        "server_id": record.server_id,
                        "org_id": record.org_id,
                        "user_id": record.user_id,
                        "state": record.state,
                        "code_verifier": record.code_verifier,
                        "redirect_uri": record.redirect_uri,
                        "auth_url": record.auth_url,
                        "expires_at": record.expires_at,
                        "created_at": record.created_at,
                    },
                )
        return record

    def pop_auth_session(self, *, state: str) -> McpAuthSessionRecord | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM mcp_auth_sessions WHERE state = %(state)s RETURNING *",
                    {"state": state},
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return McpAuthSessionRecord(
                    session_id=str(row["session_id"]),
                    server_id=str(row["server_id"]),
                    org_id=str(row["org_id"]),
                    user_id=str(row["user_id"]),
                    state=str(row["state"]),
                    code_verifier=str(row["code_verifier"]),
                    redirect_uri=str(row["redirect_uri"]),
                    auth_url=str(row["auth_url"]),
                    expires_at=self._datetime(row["expires_at"]),
                    created_at=self._datetime(row["created_at"]),
                )

    def put_token(self, record: TokenEnvelope) -> TokenEnvelope:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM mcp_auth_connections WHERE server_id = %(server_id)s",
                    {"server_id": record.server_id},
                )
                cur.execute(
                    """
                    INSERT INTO mcp_auth_connections (
                      connection_id, server_id, org_id, user_id,
                      encrypted_access_token, encrypted_refresh_token,
                      expires_at, created_at, updated_at
                    ) VALUES (
                      %(connection_id)s, %(server_id)s, %(org_id)s, %(user_id)s,
                      %(encrypted_access_token)s, %(encrypted_refresh_token)s,
                      %(expires_at)s, %(created_at)s, %(updated_at)s
                    )
                    """,
                    {
                        "connection_id": record.connection_id,
                        "server_id": record.server_id,
                        "org_id": record.org_id,
                        "user_id": record.user_id,
                        "encrypted_access_token": record.encrypted_access_token,
                        "encrypted_refresh_token": record.encrypted_refresh_token,
                        "expires_at": record.expires_at,
                        "created_at": record.created_at,
                        "updated_at": record.updated_at,
                    },
                )
        return record

    def get_token(self, *, server_id: str) -> TokenEnvelope | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM mcp_auth_connections
                    WHERE server_id = %(server_id)s
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    {"server_id": server_id},
                )
                row = cur.fetchone()
                if row is None:
                    return None
                return TokenEnvelope(
                    connection_id=str(row["connection_id"]),
                    server_id=str(row["server_id"]),
                    org_id=str(row["org_id"]),
                    user_id=str(row["user_id"]),
                    encrypted_access_token=str(row["encrypted_access_token"]),
                    encrypted_refresh_token=str(row["encrypted_refresh_token"])
                    if row.get("encrypted_refresh_token") is not None
                    else None,
                    expires_at=self._datetime(row["expires_at"])
                    if row.get("expires_at") is not None
                    else None,
                    created_at=self._datetime(row["created_at"]),
                    updated_at=self._datetime(row["updated_at"]),
                )

    def append_audit(self, record: AuditEventRecord) -> AuditEventRecord:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO mcp_audit_events (
                      audit_id, org_id, user_id, server_id, action,
                      metadata, created_at
                    ) VALUES (
                      %(audit_id)s, %(org_id)s, %(user_id)s, %(server_id)s,
                      %(action)s, %(metadata)s, %(created_at)s
                    )
                    """,
                    {
                        "audit_id": record.audit_id,
                        "org_id": record.org_id,
                        "user_id": record.user_id,
                        "server_id": record.server_id,
                        "action": record.action,
                        "metadata": json.dumps(record.metadata),
                        "created_at": record.created_at,
                    },
                )
        return record

    def _fetch_one_server(
        self, query: str, params: dict[str, object]
    ) -> McpServerRecord | None:
        rows = self._fetch_many_servers(query, params)
        return rows[0] if rows else None

    def _fetch_many_servers(
        self, query: str, params: dict[str, object]
    ) -> tuple[McpServerRecord, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return tuple(self._row_to_server(row) for row in cur.fetchall())

    def _connect(self) -> Any:
        return self._pool.connection()

    @classmethod
    def _server_params(cls, record: McpServerRecord) -> dict[str, object]:
        return {
            "server_id": record.server_id,
            "org_id": record.org_id,
            "user_id": record.user_id,
            "name": record.name,
            "display_name": record.display_name,
            "url": record.url,
            "transport": record.transport.value,
            "auth_mode": record.auth_mode.value,
            "auth_state": record.auth_state.value,
            "health": record.health.value,
            "enabled": record.enabled,
            "required_scopes": json.dumps(list(record.required_scopes)),
            "last_discovery": json.dumps(record.last_discovery),
            "oauth_client": json.dumps(record.oauth_client.model_dump())
            if record.oauth_client is not None
            else None,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    @classmethod
    def _row_to_server(cls, row: dict[str, object]) -> McpServerRecord:
        oauth_client_raw = cls._json_object(row.get("oauth_client"))
        return McpServerRecord(
            server_id=str(row["server_id"]),
            org_id=str(row["org_id"]),
            user_id=str(row["user_id"]),
            name=str(row["name"]),
            display_name=str(row["display_name"]),
            url=str(row["url"]),
            transport=str(row["transport"]),
            auth_mode=str(row["auth_mode"]),
            auth_state=str(row["auth_state"]),
            health=str(row["health"]),
            enabled=bool(row["enabled"]),
            required_scopes=tuple(cls._json_list(row.get("required_scopes"))),
            last_discovery=dict(cls._json_object(row.get("last_discovery"))),
            oauth_client=McpOAuthClientConfig(**oauth_client_raw)
            if oauth_client_raw
            else None,
            created_at=cls._datetime(row["created_at"]),
            updated_at=cls._datetime(row["updated_at"]),
        )

    @classmethod
    def _json_list(cls, value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            return [str(item) for item in json.loads(value)]
        return []

    @classmethod
    def _json_object(cls, value: object) -> dict[str, object]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        return {}

    @classmethod
    def _datetime(cls, value: object) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))


@dataclass
class InMemorySkillStore:
    """Skill registry store used when Postgres is unavailable or unnecessary."""

    skills: dict[str, SkillRecord] = field(default_factory=dict)
    audit_events: list[SkillAuditEventRecord] = field(default_factory=list)

    def create_skill(self, record: SkillRecord) -> SkillRecord:
        self.skills[record.skill_id] = record
        return record

    def update_skill(self, record: SkillRecord) -> SkillRecord:
        self.skills[record.skill_id] = record
        return record

    def get_skill(self, *, org_id: str, skill_id: str) -> SkillRecord | None:
        record = self.skills.get(skill_id)
        if record is None or record.org_id != org_id:
            return None
        return record

    def get_skill_by_name(
        self,
        *,
        org_id: str,
        user_id: str,
        name: str,
    ) -> SkillRecord | None:
        for record in self.skills.values():
            if record.org_id != org_id or record.name != name:
                continue
            if record.user_id == user_id or record.scope == "org":
                return record
        return None

    def list_skills(
        self,
        *,
        org_id: str,
        user_id: str,
        include_disabled: bool = True,
    ) -> tuple[SkillRecord, ...]:
        records = (
            record
            for record in self.skills.values()
            if record.org_id == org_id
            and (record.user_id == user_id or record.scope == "org")
            and (include_disabled or record.enabled)
        )
        return tuple(
            sorted(records, key=lambda record: (record.created_at, record.name))
        )

    def delete_skill(self, *, org_id: str, user_id: str, skill_id: str) -> bool:
        record = self.get_skill(org_id=org_id, skill_id=skill_id)
        if record is None or record.user_id != user_id:
            return False
        self.skills.pop(skill_id, None)
        return True

    def append_skill_audit(
        self, record: SkillAuditEventRecord
    ) -> SkillAuditEventRecord:
        self.audit_events.append(record)
        return record


class PostgresSkillStore:
    """PostgreSQL-backed Skill registry store with connection pooling."""

    def __init__(self, *, pool: PostgresConnectionPool) -> None:
        self._pool = pool

    def create_skill(self, record: SkillRecord) -> SkillRecord:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO skills (
                      skill_id, org_id, user_id, name, display_name, description,
                      markdown, virtual_path, enabled, scope, source_type, version,
                      allowed_tools, compatibility, metadata, created_at, updated_at
                    ) VALUES (
                      %(skill_id)s, %(org_id)s, %(user_id)s, %(name)s, %(display_name)s,
                      %(description)s, %(markdown)s, %(virtual_path)s, %(enabled)s,
                      %(scope)s, %(source_type)s, %(version)s, %(allowed_tools)s,
                      %(compatibility)s, %(metadata)s, %(created_at)s, %(updated_at)s
                    )
                    """,
                    self._record_params(record),
                )
        return record

    def update_skill(self, record: SkillRecord) -> SkillRecord:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE skills
                    SET display_name = %(display_name)s,
                        description = %(description)s,
                        markdown = %(markdown)s,
                        virtual_path = %(virtual_path)s,
                        enabled = %(enabled)s,
                        scope = %(scope)s,
                        source_type = %(source_type)s,
                        version = %(version)s,
                        allowed_tools = %(allowed_tools)s,
                        compatibility = %(compatibility)s,
                        metadata = %(metadata)s,
                        updated_at = %(updated_at)s
                    WHERE org_id = %(org_id)s AND skill_id = %(skill_id)s
                    """,
                    self._record_params(record),
                )
        return record

    def get_skill(self, *, org_id: str, skill_id: str) -> SkillRecord | None:
        return self._fetch_one(
            "SELECT * FROM skills WHERE org_id = %(org_id)s AND skill_id = %(skill_id)s",
            {"org_id": org_id, "skill_id": skill_id},
        )

    def get_skill_by_name(
        self,
        *,
        org_id: str,
        user_id: str,
        name: str,
    ) -> SkillRecord | None:
        return self._fetch_one(
            """
            SELECT * FROM skills
            WHERE org_id = %(org_id)s
              AND name = %(name)s
              AND (user_id = %(user_id)s OR scope = 'org')
            ORDER BY scope DESC, updated_at DESC
            LIMIT 1
            """,
            {"org_id": org_id, "user_id": user_id, "name": name},
        )

    def list_skills(
        self,
        *,
        org_id: str,
        user_id: str,
        include_disabled: bool = True,
    ) -> tuple[SkillRecord, ...]:
        enabled_clause = "" if include_disabled else "AND enabled = TRUE"
        return self._fetch_many(
            f"""
            SELECT * FROM skills
            WHERE org_id = %(org_id)s
              AND (user_id = %(user_id)s OR scope = 'org')
              {enabled_clause}
            ORDER BY created_at, name
            """,
            {"org_id": org_id, "user_id": user_id},
        )

    def delete_skill(self, *, org_id: str, user_id: str, skill_id: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM skills
                    WHERE org_id = %(org_id)s AND user_id = %(user_id)s AND skill_id = %(skill_id)s
                    """,
                    {"org_id": org_id, "user_id": user_id, "skill_id": skill_id},
                )
                return cur.rowcount > 0

    def append_skill_audit(
        self, record: SkillAuditEventRecord
    ) -> SkillAuditEventRecord:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO skill_audit_events (
                      audit_id, org_id, user_id, skill_id, action, metadata, created_at
                    ) VALUES (
                      %(audit_id)s, %(org_id)s, %(user_id)s, %(skill_id)s,
                      %(action)s, %(metadata)s, %(created_at)s
                    )
                    """,
                    {
                        "audit_id": record.audit_id,
                        "org_id": record.org_id,
                        "user_id": record.user_id,
                        "skill_id": record.skill_id,
                        "action": record.action,
                        "metadata": json.dumps(record.metadata),
                        "created_at": record.created_at,
                    },
                )
        return record

    def _fetch_one(self, query: str, params: dict[str, object]) -> SkillRecord | None:
        rows = self._fetch_many(query, params)
        return rows[0] if rows else None

    def _fetch_many(
        self, query: str, params: dict[str, object]
    ) -> tuple[SkillRecord, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return tuple(self._row_to_record(row) for row in cur.fetchall())

    def _connect(self) -> Any:
        return self._pool.connection()

    @classmethod
    def _record_params(cls, record: SkillRecord) -> dict[str, object]:
        return {
            "skill_id": record.skill_id,
            "org_id": record.org_id,
            "user_id": record.user_id,
            "name": record.name,
            "display_name": record.display_name,
            "description": record.description,
            "markdown": record.markdown,
            "virtual_path": record.virtual_path,
            "enabled": record.enabled,
            "scope": record.scope.value,
            "source_type": record.source_type.value,
            "version": record.version,
            "allowed_tools": json.dumps(record.allowed_tools),
            "compatibility": json.dumps(record.compatibility),
            "metadata": json.dumps(record.metadata),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    @classmethod
    def _row_to_record(cls, row: dict[str, object]) -> SkillRecord:
        return SkillRecord(
            skill_id=str(row["skill_id"]),
            org_id=str(row["org_id"]),
            user_id=str(row["user_id"]),
            name=str(row["name"]),
            display_name=str(row["display_name"]),
            description=str(row["description"]),
            markdown=str(row["markdown"]),
            virtual_path=str(row["virtual_path"]),
            enabled=bool(row["enabled"]),
            scope=str(row["scope"]),
            source_type=str(row["source_type"]),
            version=int(row["version"]),
            allowed_tools=tuple(cls._json_list(row["allowed_tools"])),
            compatibility=tuple(cls._json_list(row["compatibility"])),
            metadata=dict(cls._json_object(row["metadata"])),
            created_at=cls._datetime(row["created_at"]),
            updated_at=cls._datetime(row["updated_at"]),
        )

    @classmethod
    def _json_list(cls, value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            return [str(item) for item in json.loads(value)]
        return []

    @classmethod
    def _json_object(cls, value: object) -> dict[str, object]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        return {}

    @classmethod
    def _datetime(cls, value: object) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))


@dataclass
class InMemoryDeployAuditStore:
    """Deploy audit log used until a postgres-backed adapter is wired in.

    The CI/CD assurance spec tracks postgres backing for deploy audit as a known gap.
    The in-memory adapter is sufficient for local dev and tests; production must inject
    a persistent adapter that mirrors this contract before claiming the control complete.
    """

    audit_events: list[DeployAuditEventRecord] = field(default_factory=list)

    def append_deploy_audit(
        self, record: DeployAuditEventRecord
    ) -> DeployAuditEventRecord:
        self.audit_events.append(record)
        return record

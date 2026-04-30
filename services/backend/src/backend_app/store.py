"""Backend stores used by local development, tests, and production adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from typing import Any

from backend_app.contracts import (
    AuditEventRecord,
    McpAuthSessionRecord,
    McpServerRecord,
    SkillAuditEventRecord,
    SkillRecord,
    TokenEnvelope,
)


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
        return tuple(sorted(records, key=lambda record: (record.created_at, record.name)))

    def delete_skill(self, *, org_id: str, user_id: str, skill_id: str) -> bool:
        record = self.get_skill(org_id=org_id, skill_id=skill_id)
        if record is None or record.user_id != user_id:
            return False
        self.skills.pop(skill_id, None)
        return True

    def append_skill_audit(self, record: SkillAuditEventRecord) -> SkillAuditEventRecord:
        self.audit_events.append(record)
        return record


class PostgresSkillStore:
    """PostgreSQL-backed Skill registry store.

    The import is intentionally lazy so unit tests and local in-memory runs do not
    require a running Postgres client library.
    """

    def __init__(self, *, database_url: str) -> None:
        self.database_url = database_url

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

    def append_skill_audit(self, record: SkillAuditEventRecord) -> SkillAuditEventRecord:
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

    def _fetch_many(self, query: str, params: dict[str, object]) -> tuple[SkillRecord, ...]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return tuple(self._row_to_record(row) for row in cur.fetchall())

    def _connect(self) -> Any:
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self.database_url, row_factory=dict_row)

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

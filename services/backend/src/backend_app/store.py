"""In-memory MCP registry store used by local development and tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from backend_app.contracts import (
    AuditEventRecord,
    McpAuthSessionRecord,
    McpServerRecord,
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

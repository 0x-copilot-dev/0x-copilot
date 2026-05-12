# Audit Events and SIEM Export

The four append-only audit chains and the SIEM export pump.

See also:

- [architecture/02-contracts.md](../architecture/02-contracts.md) — `AuditEventRecord` shapes
- [architecture/03-stores.md](../architecture/03-stores.md) — store append invariants
- [reference/internal-api.md](../reference/internal-api.md) — `/internal/v1/audit`

---

## What it does

Backend maintains four separate append-only audit chains — one each for MCP events, skill
events, identity events, and deploy events. Each chain is cryptographically signed so
tampering is detectable. The `AuditReader` provides a unified read surface with cursor-based
pagination. The SIEM export pump pushes events to external collectors (Elastic, Splunk, Syslog, etc.).

---

## Four audit chains

| Chain    | Event type                 | `action` examples                                                                                    |
| -------- | -------------------------- | ---------------------------------------------------------------------------------------------------- |
| MCP      | `AuditEventRecord`         | `mcp.server.created`, `mcp.oauth.started`, `mcp.token.rotated`                                       |
| Skill    | `SkillAuditEventRecord`    | `skill.created`, `skill.updated`, `skill.deleted`                                                    |
| Identity | `IdentityAuditEventRecord` | `user.created`, `login.success`, `login.failed`, `role.assigned`, `mfa.enrolled`, `scim.user.synced` |
| Deploy   | `DeployAuditEventRecord`   | Emitted by CI/CD pipeline via `POST /internal/v1/audit/deploy`                                       |

### Chain fields (appended by the store on write)

| Field         | Type    | Notes                                         |
| ------------- | ------- | --------------------------------------------- |
| `seq`         | `int`   | Monotonically increasing per (org, chain)     |
| `prev_hash`   | `bytes` | sha256 of the previous row's content          |
| `signature`   | `bytes` | HMAC-SHA256 signature from `AuditChainSigner` |
| `key_version` | `int`   | Key rotation version                          |

**Append-only invariant**: the store never issues an UPDATE or DELETE on audit rows. The
chain signer from `enterprise_audit_chain` package verifies each new row links correctly
to the prior.

---

## `AuditReader` (`backend_app/audit_reader.py`)

The read surface for all four chains, unified.

### Cursor

An opaque base64-JSON string encoding `{chain, seq, org_id}`. Callers pass the cursor
back on the next request to page forward. The cursor is stable across restarts.

### `AuditReader.list(org_id, *, limit, cursor, chain_filter)`

- `chain_filter` — list of chain names to include (default: all four)
- Returns `{events: [...], next_cursor: str | None}`
- Events across chains are merged in `created_at` order within the page

Used by:

- `GET /internal/v1/audit` → `RequireScopes("audit:read")`
- `GET /internal/v1/audit/export` → `RequireScopes("admin:audit_export")`

---

## SIEM export (`backend_app/siem_export/`)

### Architecture

| File            | Role                                                                 |
| --------------- | -------------------------------------------------------------------- |
| `interface.py`  | `SiemExporter` protocol + event type definitions                     |
| `exporters.py`  | Elastic, Splunk HEC, Syslog CEF, File, Null exporter implementations |
| `normalizer.py` | Normalizes audit records to CEF / JSON payload for the exporter      |
| `pump.py`       | Cursor-tracked pump with dead-letter table                           |

### Export pump (`pump.py`)

The pump is a background job that:

1. Reads from the audit store from the last exported `seq` cursor (stored in DB).
2. Normalizes each event via `normalizer.py`.
3. Calls `exporter.export(events)`.
4. On success: advances the cursor.
5. On failure: writes to the dead-letter table and retries after backoff.

Routes:

- `GET /internal/v1/siem/status` — returns current cursor position and health
- `POST /internal/v1/siem/retry` — triggers a dead-letter retry
- `GET /internal/v1/siem/exporters` — lists configured exporters

Auth: `RequireScopes("admin:siem")`.

### Exporters

| Exporter   | Config env vars                                                     |
| ---------- | ------------------------------------------------------------------- |
| Elastic    | `SIEM_ELASTIC_URL`, `SIEM_ELASTIC_INDEX`, `SIEM_ELASTIC_API_KEY`    |
| Splunk HEC | `SIEM_SPLUNK_HEC_URL`, `SIEM_SPLUNK_HEC_TOKEN`, `SIEM_SPLUNK_INDEX` |
| Syslog CEF | `SIEM_SYSLOG_HOST`, `SIEM_SYSLOG_PORT`, `SIEM_SYSLOG_PROTO`         |
| File       | `SIEM_FILE_PATH` (dev/test only)                                    |
| Null       | Default; no-op; used in dev when no SIEM is configured              |

---

## Compliance note

Never mark audit logging complete if:

- The adapter is in-memory only (use `PostgresAuditStore` in production)
- The adapter is mutable (no UPDATE/DELETE on audit rows)
- There is no SIEM export path configured for the deployment

The in-memory audit store is only acceptable in local development. All regulated
deployment profiles (`bank`, `government`) must use Postgres + at least one SIEM exporter.

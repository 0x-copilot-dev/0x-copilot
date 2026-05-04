# PR 24 — C7: Field-level Encryption for Sensitive PII Columns

**Spec ID:** C7 | **Track:** Deployment & DB | **Wave:** 6 (Security Hardening) | **Estimated effort:** XL
**Depends on:** C2 (migrations), C6 (KMS adapter)
**Required for:** all bank/gov deploys

---

## 1. Functional Specification

### 1.1 Goal

Encrypt PII / model output / metadata at the column level using envelope encryption with per-row data keys (DEKs) wrapped by the KMS CMK from C6. CMK rotation is then cheap — only DEK cache invalidation is needed, not re-encryption of every row.

### 1.2 User-visible behavior

- **End user:** none observable.
- **Operator:** `pg_dump --schema-only` shows ciphertext only in encrypted columns. Customer-side DBA can read schema but not data.
- **Auditor:** can prove no plaintext PII in `pg_dump` after backfill completes.

### 1.3 Out of scope

- Client-side encryption (the keys are server-side; we are not E2EE).
- Searchable encryption (encrypted columns are not WHERE-able).
- Per-tenant KMS keys (column ready, follow-up PR).

---

## 2. Technical Specification

### 2.1 Architecture

- **Envelope encryption:** generate a random AES-256-GCM data key per row; encrypt the field with the DEK; encrypt the DEK with the KMS CMK; store `(wrapped_dek, iv, ciphertext+tag)`.
- **AAD (Additional Authenticated Data):** `f"{table}|{column}|{org_id}".encode()` — prevents ciphertext-swap attacks across columns or tenants.
- **Format:** `v1:` + base64(wrapped_dek) + ":" + base64(iv) + ":" + base64(ciphertext+tag).
- **Encryption version column:** `encryption_version SMALLINT NOT NULL DEFAULT 0` per affected table; reads tolerate both `0` (plaintext) and `1` (envelope-v1).
- **DEK cache:** scoped per `org_id`, 60s TTL; bounded size; reduces KMS load.
- **Backfill job:** chunked UPDATE of rows with `encryption_version=0`.

### 2.2 Schema changes

Migration `services/ai-backend/migrations/0010_field_encryption.sql`:

```sql
ALTER TABLE agent_messages ADD COLUMN encryption_version SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE runtime_audit_log ADD COLUMN encryption_version SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE runtime_events ADD COLUMN encryption_version SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE runtime_subagent_results ADD COLUMN encryption_version SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE runtime_tool_invocations ADD COLUMN encryption_version SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE runtime_memory_items ADD COLUMN encryption_version SMALLINT NOT NULL DEFAULT 0;

CREATE TABLE runtime_context_payload_blobs (
    id                  TEXT PRIMARY KEY,
    payload_id          TEXT NOT NULL REFERENCES runtime_context_payloads(id),
    encrypted_blob      BYTEA NOT NULL,
    encryption_version  SMALLINT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_runtime_context_payload_blobs_payload
    ON runtime_context_payload_blobs (payload_id);
```

### 2.3 Endpoints

None.

### 2.4 Code changes

**New** `services/ai-backend/src/agent_runtime/persistence/encryption.py`:

```python
class FieldEncryption(Protocol):
    def encrypt(self, plaintext: bytes, *, table: str, column: str, org_id: str) -> bytes: ...
    def decrypt(self, ciphertext: bytes, *, table: str, column: str, org_id: str) -> bytes: ...

class EnvelopeFieldEncryption(FieldEncryption):
    def __init__(self, kms_vault: ManagedSecretTokenVault, dek_cache_ttl: int = 60): ...
    # Generates per-row DEK, encrypts plaintext with AES-256-GCM, wraps DEK via KMS.
    # AAD = f"{table}|{column}|{org_id}".encode()

class NullFieldEncryption(FieldEncryption):
    """Pass-through for dev when RUNTIME_FIELD_ENCRYPTION=disabled."""
```

**Targeted columns (verified to need encryption):**
| Table | Column(s) |
|--------------------------------|----------------------------------------------------------|
| `agent_messages` | `content_text`, `content_json`, `metadata_json` |
| `runtime_audit_log` | `metadata_json_redacted` |
| `runtime_events` | `payload_json_redacted`, `metadata_json_redacted` |
| `runtime_subagent_results` | `response_text` |
| `runtime_tool_invocations` | `args_json_redacted`, `result_summary_json_redacted` |
| `runtime_memory_items` | `content_summary` + content referenced by `content_ref` |
| `runtime_context_payloads` | (split out to new `runtime_context_payload_blobs`) |

**Excluded** (intentionally — encrypting these breaks queryability and isn't required by any control I know of): ids, timestamps, status enums, FKs, indexed columns used in WHERE clauses (`org_id`, `user_id`, `conversation_id`, `run_id`, `trace_id`).

**Modify** `PostgresRuntimeApiStore`:

- Encrypt on write (after Pydantic validation, before `Jsonb()`).
- Decrypt on read (in projection methods like `_message_record`, `_event_envelope`).
- Reads detect `encryption_version > 0` and decrypt; `=0` is plaintext (tolerant during cutover).

**Background backfill job** `services/ai-backend/src/runtime_worker/jobs/encrypt_existing_columns.py`:

- Chunked UPDATE of rows with `encryption_version=0`.
- Rate-limited (`RUNTIME_ENCRYPTION_BACKFILL_BATCH=100`, sleep N ms between batches).
- Per-table; resumable via cursor.

**Removal of plaintext-tolerant read path**: SEPARATE follow-up PR after `min(encryption_version)=1` everywhere.

### 2.5 Trust model & failure semantics

- KMS unavailable → encrypt fails → write fails (fail-closed).
- Decrypt failure (e.g. corrupted ciphertext): typed error surfaced to caller; row is poisoned and operator-investigated.
- AAD mismatch (ciphertext from `agent_messages.content_text` decrypted as if from `runtime_audit_log`) → decrypt fails, no data leak.
- DEK cache TTL bounds revocation latency.

### 2.6 Tenant isolation

- AAD includes `org_id`; ciphertext from org_a cannot be decrypted as if from org_b.

### 2.7 Observability

- Metrics: `field_encryption_op_total{op,table,outcome}`, `field_encryption_dek_cache_hit_ratio`, `field_encryption_kms_calls_total`.
- Backfill metric: `field_encryption_backfill_rows_total`.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] Round-trip per affected column: encrypt-then-decrypt returns original.
- [ ] AAD safety: ciphertext from `agent_messages.content_text` cannot be decrypted when read from `runtime_audit_log`.
- [ ] Mixed-version reads work: rows with `encryption_version=0` and `=1` co-exist.
- [ ] Full ai-backend test suite passes with `RUNTIME_FIELD_ENCRYPTION=envelope_v1` (against fake KMS).
- [ ] Performance: p99 message append < 2× plaintext baseline (envelope + cached DEKs).
- [ ] After backfill completes, `pg_dump --data-only` of affected columns shows only ciphertext.

### 3.2 Test plan

**Unit:**

- Round-trip correctness per table+column.
- AAD swap test (negative).
- NullFieldEncryption pass-through.

**Integration:**

- Mixed-version read.
- Backfill produces v1 rows.
- Full suite under `RUNTIME_FIELD_ENCRYPTION=envelope_v1`.

**Performance:**

- Microbench message append baseline vs encrypted.

**Compliance:**

- `pg_dump` snapshot test asserts no plaintext PII recoverable.

### 3.3 Compliance evidence produced

- Field-level encryption demonstrably applied to PII columns.
- KMS CMK rotation cheap (DEK invalidation only).
- AAD prevents ciphertext-swap attacks.

### 3.4 Rollout plan (3-phase, load-bearing)

1. Schema + adapter ship; reads tolerate v0 and v1; writes still v0.
2. Flip writes to `RUNTIME_FIELD_ENCRYPTION=envelope_v1` per service.
3. Run backfill in production for several days.
4. Separate small PR removes plaintext-tolerant read path after `min(encryption_version)=1` everywhere.

### 3.5 Backout plan

- Set `RUNTIME_FIELD_ENCRYPTION=disabled` for writes; reads still tolerate.
- Reverse-backfill (decrypt + write back as v0) only if absolutely necessary.

### 3.6 Definition of done

- [ ] Migration 0010 applied.
- [ ] EnvelopeFieldEncryption + NullFieldEncryption implemented.
- [ ] Adapter encrypt/decrypt wired for every targeted column.
- [ ] Backfill job tested.
- [ ] Performance budget met.
- [ ] pg_dump snapshot test passes.
- [ ] `docs/security/field-encryption.md` written including rotation runbook.

---

## 4. Critical files

- New: `services/ai-backend/migrations/0010_field_encryption.sql` (+ rollback)
- New: `services/ai-backend/src/agent_runtime/persistence/encryption.py`
- Modify: [services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py) — encrypt-on-write, decrypt-on-read.
- New: `services/ai-backend/src/runtime_worker/jobs/encrypt_existing_columns.py`
- New: `docs/security/field-encryption.md`

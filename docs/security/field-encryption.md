# Field-level Envelope Encryption (C7)

This runbook covers the column-level envelope encryption that protects PII
in the ai-backend's tenant tables. C7 builds on C6's KMS adapter
framework — the per-row data encryption keys (DEKs) are wrapped by the
customer-managed CMK that already protects MCP OAuth tokens.

## Threat model

| Concern                          | Defense                                                                     |
| -------------------------------- | --------------------------------------------------------------------------- |
| Backup tape disclosure           | Ciphertext only; CMK access required to read.                               |
| Insider DBA reading tables       | `pg_dump` of affected columns yields ciphertext only (post-backfill).       |
| Ciphertext copied across columns | AAD = `f"{table}\|{column}\|{org_id}"` — decrypt fails on swap.             |
| Ciphertext copied across tenants | Same AAD includes `org_id`.                                                 |
| CMK compromise                   | Rotate CMK in KMS — DEK cache TTL bounds exposure window.                   |
| KMS unavailability               | Writes fail closed. Reads serve from DEK cache up to TTL, then fail closed. |

## Envelope format

```
v1:<urlsafe_b64(wrapped_dek)>:<urlsafe_b64(iv)>:<urlsafe_b64(ciphertext+tag)>
```

- `wrapped_dek` — KMS Encrypt-wrapped 32-byte AES-256 DEK.
- `iv` — 12-byte AES-GCM nonce.
- `ciphertext+tag` — AES-GCM ciphertext concatenated with the 16-byte tag.

The header `v1:` is the envelope version; phase 4 (write-flip-only PR)
removes the v0 plaintext-tolerant read path after every row reaches v1.

## Schema

Migration `services/ai-backend/migrations/0011_field_encryption.sql`
adds an `encryption_version SMALLINT NOT NULL DEFAULT 0` column to:

- `agent_messages`
- `runtime_audit_log`
- `runtime_events`
- `runtime_subagent_results`
- `runtime_tool_invocations`
- `runtime_memory_items`

Plus a sidecar table `runtime_context_payload_blobs` for context payloads
that exceed practical PG row sizes.

`encryption_version=0` means plaintext (legacy); `=1` means the column
holds a `v1:` envelope.

## Targeted columns

Per the C7 spec:

| Table                      | Column(s)                                              |
| -------------------------- | ------------------------------------------------------ |
| `agent_messages`           | `content_text`, `content_json`, `metadata_json`        |
| `runtime_audit_log`        | `metadata_json_redacted`                               |
| `runtime_events`           | `payload_json_redacted`, `metadata_json_redacted`      |
| `runtime_subagent_results` | `response_text`                                        |
| `runtime_tool_invocations` | `args_json_redacted`, `result_summary_json_redacted`   |
| `runtime_memory_items`     | `content_summary` (+ content via `content_ref`)        |
| `runtime_context_payloads` | sidecar `runtime_context_payload_blobs.encrypted_blob` |

**Excluded by design** (queryability or no PII): `id`, `org_id`,
`user_id`, `conversation_id`, `run_id`, `trace_id`, timestamps, status
enums, foreign keys.

## Configuration

```bash
# ai-backend env vars
RUNTIME_FIELD_ENCRYPTION=disabled            # default; pass-through.
RUNTIME_FIELD_ENCRYPTION=envelope_v1         # enable encryption-on-write.

RUNTIME_KMS_BACKEND=aws_kms                  # only KMS backend in this PR.
RUNTIME_KMS_KEY_ID=alias/prod-ai-cmk         # CMK to wrap DEKs.

# DEK cache (per-process, in-memory).
RUNTIME_FIELD_ENCRYPTION_DEK_CACHE_TTL=60    # seconds; bounds CMK revocation lag.
RUNTIME_FIELD_ENCRYPTION_DEK_CACHE_SIZE=1024 # entries.

# Backfill job.
RUNTIME_ENCRYPTION_BACKFILL_BATCH=100        # rows per batch.
RUNTIME_ENCRYPTION_BACKFILL_SLEEP_MS=200     # rate-limit between batches.
```

## Rollout — 4 phases (load-bearing)

This is **not** a single-step deploy. Each phase verifies the next is safe.

### Phase 1 — schema + adapter framework ship (THIS PR)

- Migration 0011 applied. Existing rows are `encryption_version=0`.
- `EnvelopeFieldEncryption` + `NullFieldEncryption` available.
- Default `RUNTIME_FIELD_ENCRYPTION=disabled` → writes still v0.
- Reads tolerate v0 (plaintext pass-through) and v1 (decrypt via adapter).

**Wired in phase 1:**

- ✅ Adapter injected into `PostgresRuntimeApiStore.__init__`.
- ✅ Encryption module + factory + KMS client.
- ✅ Backfill job scaffold (originally targeted `agent_messages.content_text`
  only).
- ✅ Unit tests for round-trip, AAD swap rejection, DEK cache.

### Phase 2 — per-column wiring + flip writes

The per-call-site encrypt-on-write / decrypt-on-read wiring landed
alongside a small `FieldCodec` facade in
`agent_runtime/persistence/encryption.py`. The codec hides the (text vs
JSONB) marshaling and the (v0 vs v1) version branching so each
INSERT/SELECT is one extra line. JSONB columns store envelopes wrapped
as `{"$enc": "v1:..."}` so the column stays valid JSONB at the Postgres
level; text columns store the envelope string directly.

**Wired columns** (verified end-to-end via the projection tests in
`tests/unit/runtime_adapters/postgres/test_field_encryption_projections.py`):

- ✅ `agent_messages.content_text`, `content_json`, `metadata_json`
- ✅ `runtime_audit_log.metadata_json_redacted` (SIEM export decrypts
  per-row before forwarding)
- ✅ `runtime_events.payload_json_redacted`, `metadata_json_redacted`

**Still wire-pending** (schema columns exist but no active write path
in `PostgresRuntimeApiStore` yet — wiring lands when those tables get a
writer): `runtime_subagent_results.response_text`,
`runtime_tool_invocations.{args_json_redacted, result_summary_json_redacted}`,
`runtime_memory_items.content_summary`, and the `runtime_context_payload_blobs`
sidecar.

After phase-2 wiring is verified in staging, operators set:

```bash
RUNTIME_FIELD_ENCRYPTION=envelope_v1
RUNTIME_KMS_BACKEND=aws_kms
RUNTIME_KMS_KEY_ID=alias/<prod-cmk>
```

All new writes use v1; existing v0 rows untouched until backfill runs.

### Phase 3 — backfill

Per-table, rate-limited, resumable. Run during low-traffic windows.

```bash
.venv/bin/python -m runtime_worker.jobs.encrypt_existing_columns \
    --database-url postgres://… \
    --batch-size 200
```

Verify:

```sql
SELECT encryption_version, COUNT(*)
  FROM agent_messages
 GROUP BY encryption_version;
```

When `min(encryption_version)=1` everywhere, phase 3 is done.

### Phase 4 — remove v0 read path (separate small PR)

Once every targeted column has `min(encryption_version)=1`, a follow-up PR
deletes the plaintext-tolerant branch from each read site. This is the
only way the `pg_dump` snapshot test can guarantee no plaintext PII.

## CMK rotation

CMK rotation is **cheap** under envelope encryption: rotating the CMK
invalidates only the wrapped DEK cache; the row ciphertexts stay valid
because each row's wrapped DEK references the (rotated) CMK by KMS-side
identity.

Steps:

1. Rotate the AWS CMK (via `aws kms enable-key-rotation` or schedule a new
   key version). For "rotate to a brand new CMK" see the
   `services/backend/scripts/rotate_token_vault.py` pattern; an analogous
   ai-backend script ships in C7's phase-4 cleanup PR.
2. Restart ai-backend pods to flush the per-process DEK cache (or wait
   `RUNTIME_FIELD_ENCRYPTION_DEK_CACHE_TTL` seconds — defaults to 60).
3. New writes' wrapped DEKs reference the rotated CMK; existing rows'
   wrapped DEKs are still valid because AWS KMS preserves prior key
   versions inside the same CMK.

## Audit trail

KMS Encrypt and KMS Decrypt calls appear in CloudTrail (or the equivalent
KMS audit log for non-AWS backends). That's the canonical "who decrypted
what when" record; the ai-backend service deliberately doesn't duplicate
it. Backfill jobs are visible via the `field_encryption_backfill_rows_total`
OTel counter and via standard worker logs.

## Backout

To stop NEW writes from being encrypted:

```bash
RUNTIME_FIELD_ENCRYPTION=disabled
```

Existing v1 ciphertexts stay readable as long as KMS access is preserved.
Reverse-backfill (decrypt + write back as v0) is supported but **not
recommended** — it strips the compliance evidence the encryption was
supposed to provide. Operators should retain CMK access and KMS network
reachability instead.

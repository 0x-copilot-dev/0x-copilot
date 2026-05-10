# PRD — Audit Hash Chain Refactor

**Refactor target:** the HMAC hash-chained audit log subsystem (originally flagged as finding [1.3 in the architecture audit](../architecture/refactor-audit.md#13-custom-hash-chained-audit-log)).

**Status:** Implemented. Package created at [packages/audit-chain/](../../../../packages/audit-chain/); both services migrated; legacy files deleted; compat-fixture tests pinning legacy signatures pass in both services. The original audit finding is marked Resolved at [refactor-audit.md §1.3](../architecture/refactor-audit.md#13-custom-hash-chained-audit-log--resolved-de-duplicated-not-deleted).

**Author note:** This PRD reads the code against the original finding rather than starting from the conclusion. The investigation reversed the original recommendation. The detail is intentional — the user's standing rule is "no functionality missed and no bugs added."

---

## TL;DR

The original audit recommended deleting the in-app HMAC chain and "letting SIEM assert immutability." That recommendation was made from diagrams and was **wrong in two ways**:

1. **A SIEM cursor already exists** ([`/internal/v1/audit/cursor`](../../src/runtime_api/http/routes.py#L1431)). Streaming to a SIEM is already the export pattern; deleting the chain wouldn't add SIEM coverage, it would only remove tamper detection in the _write → export_ window.
2. **Append-only enforcement is already three-layer**: the application code, a Postgres role with revoked UPDATE/DELETE, and a constraint trigger on the table. The chain is the _integrity proof_ on top of _immutability_ — different concern, both load-bearing.

The actual refactor opportunity is much narrower: **the same HMAC chain implementation is duplicated** between `services/ai-backend/src/agent_runtime/observability/audit_chain.py` (207 LOC) and `services/backend/src/backend_app/audit_chain.py` (258 LOC). The two have already drifted in line count. The duplication is intentional per the service-boundary rule, but the rule allows shared cross-cutting primitives — and an HMAC hash chain qualifies.

**Recommendation:** Extract the HMAC chain into a new `packages/audit-chain` shared Python package. Both services import from it. Delete the duplication. Keep every other behavior — the chain itself, the per-org scope, the keys, the per-service tables, the SIEM cursor, the trigger, the role — exactly as today. Estimated diff: ~400 LOC deleted across two services, ~250 LOC added in one package, no schema or behavior changes.

---

## Problem

Per `services/ai-backend/docs/CLAUDE.md`, a PRD must define the problem precisely. There are three layered problems:

### Problem 1 (the smell I was asked to address)

The architecture audit flagged the in-app HMAC hash chain as bespoke code where standard tooling could replace it. Specifically:

> Audit log integrity is normally the receiving SIEM's job, not the source application's. Hash chaining at the source assumes nothing else writes to the table, requires careful lock discipline on append, and complicates legitimate operational tasks (re-encrypting columns, retention sweeps).

That framing turns out to be incomplete once the code is read. See [Why the original finding was wrong](#why-the-original-finding-was-wrong) below.

### Problem 2 (the real smell, surfaced during investigation)

The HMAC chain implementation is **duplicated** across two services with the same structure but slightly different code:

| File                                                                                                                         | LOC |
| ---------------------------------------------------------------------------------------------------------------------------- | --- |
| [`services/ai-backend/src/agent_runtime/observability/audit_chain.py`](../../src/agent_runtime/observability/audit_chain.py) | 207 |
| `services/backend/src/backend_app/audit_chain.py`                                                                            | 258 |

The ai-backend file's docstring acknowledges it:

> Mirrors the design in `services/backend/src/backend_app/audit_chain.py`; duplicated here because the service-boundary rule forbids cross-service imports.

The line-count delta means the two have already drifted. A bug fix in one (e.g. canonical-form change, key-rotation edge case) does not propagate to the other. A signature bug in one chain plus the absence of cross-service signature comparison means each chain is one fix away from being inconsistent with itself across rotations.

### Problem 3 (a smaller but related issue)

Inside ai-backend, the chain logic is **also implemented twice**: once in [`audit_chain.py`](../../src/agent_runtime/observability/audit_chain.py) (signing primitive) and once inline in [`InMemoryRuntimeApiStore._sign_audit_record`](../../src/runtime_adapters/in_memory/runtime_api_store.py#L802) (per-org head tracking + signing). The Postgres adapter has yet another integration in [`PostgresRuntimeApiStore.write_audit_log`](../../src/runtime_adapters/postgres/runtime_api_store.py#L1628). Each adapter manages its own per-org chain head bookkeeping. The signer is shared; the chain-head management is not.

This is more architectural than urgent — splitting head-tracking from signing makes each adapter ~30 LOC heavier than it has to be — but it's worth flagging here because a refactor that consolidates the signer should consider whether to consolidate head-tracking too.

---

## Goals

1. **Eliminate cross-service duplication** of the HMAC hash-chain primitive. One implementation, owned by one place, used by both services.
2. **Preserve every observable behavior**: signature compatibility (rows signed today must verify after the change), key versioning, key rotation, fail-closed startup in production, per-org chain scope, SIEM cursor format, audit list format, trigger-level immutability, role-level append-only.
3. **Stay inside existing service-boundary rules**: no cross-service `src/` imports; the `packages/` directory is the legitimate sharing channel per [root CLAUDE.md](../../../../CLAUDE.md).
4. **Make the refactor reversible** at every step: ship as a sequence of small, behavior-preserving PRs that can be reverted independently.

## Non-goals

The following are explicitly **not** in scope. Each is a defensible refactor on its own; bundling any of them with this PRD would inflate risk and blast radius.

1. **Replacing the HMAC chain with `pgaudit` extension or WAL archiving.** That's a database-native alternative with different operational characteristics (DBA tooling required, behavior on read replicas differs, etc.). Worth a separate evaluation if buyers ever ask.
2. **Replacing the chain with a managed audit service** (AWS QLDB, Hyperledger). Vendor lock-in, cost, and the fact that a SIEM pump already provides downstream integrity make this hard to justify today.
3. **Removing the chain entirely** and relying on SIEM-only integrity. The chain catches tampering in the window between write and SIEM export — that window can be hours during an outage. Removing it weakens the security posture.
4. **Consolidating the per-adapter chain-head bookkeeping** (Problem 3). Possible follow-on work; the cost/value is much smaller than the de-duplication win.
5. **Migrating the Postgres advisory lock to a different serialization mechanism** (e.g. row lock on a sentinel row, or per-org sequence). The advisory lock works and is simple.
6. **Field-level encryption changes.** [`encryption.py`](../../src/agent_runtime/persistence/encryption.py) wraps the audit metadata column; that's a separate refactor track.
7. **Audit retention policy changes.** [`retention/policy_resolver.py`](../../src/agent_runtime/retention/policy_resolver.py) is the relevant module; outside scope.
8. **Adding new audit events** or changing existing event schemas.

## Acceptance criteria

Concrete, mechanical checks. Each one must pass before the refactor is considered done.

1. A new package `packages/audit-chain/` exists with:
   - `pyproject.toml` declaring the Python package.
   - `src/audit_chain/__init__.py` re-exporting the public API.
   - `src/audit_chain/signer.py` containing `AuditChainSigner`, `ChainSignature`, `ChainVerificationResult`, `AuditChainRow`.
   - Tests covering: deterministic signing, prev-hash linkage, key rotation across versions, tamper detection (payload mutation, row removal, signature byte flip), fail-closed in production.
2. `services/ai-backend/src/agent_runtime/observability/audit_chain.py` is deleted. All imports of `AuditChainSigner` (etc.) inside ai-backend resolve to `audit_chain` (the package).
3. `services/backend/src/backend_app/audit_chain.py` is deleted. All imports inside backend resolve to the same `audit_chain` package.
4. `services/ai-backend/Dockerfile` and `services/backend/Dockerfile` install the `audit-chain` package via the existing shared-package install path (mirror how `service-contracts` is installed).
5. **Signature compatibility**: rows signed by the old chain verify under the new chain. Concretely: a fixture of pre-refactor `(payload, prev_hash, signature, key_version)` rows verifies `True` under the new `AuditChainSigner.verify_chain`. Equivalent fixture for backend. (See [Risks](#risks) — the canonical form must be byte-identical.)
6. The HTTP audit endpoints behave identically:
   - `/internal/v1/audit/list` returns the same JSON shape, including the `chain` sub-object with `seq`, `prev_hash`, `signature`, `key_version`.
   - `/internal/v1/audit/cursor` returns the same JSON shape.
   - The facade's `/v1/audit` continues to compose both internal endpoints.
7. The Postgres trigger `runtime_audit_log_immutable_guard` and the `audit_writer` role on `runtime_audit_log` are unchanged. Same DB role still rejects UPDATE/DELETE. Same trigger still fires.
8. The CI signal "ai-backend full suite passes" stays green. The "backend full suite passes" stays green. No skipped tests.
9. The pre-existing `tests/unit/agent_runtime/observability/test_audit_chain.py` still passes (its imports resolve to the new package), and its assertions are unchanged.
10. A new pair of integration tests demonstrates a row written by the **old** ai-backend chain code (committed as a fixture) verifies under the **new** package. Equivalent fixture for backend.

## Risks

Honest enumeration. None are showstoppers; all have mitigations.

### R1 — Canonical-form drift between the two services

The two existing `audit_chain.py` files have diverged in line count (207 vs 258). If the canonical signing form (the JSON envelope passed into `hmac.new`) differs at the byte level between the two services, then unifying them breaks one or both chains' verification of historical rows.

**Mitigation:** Before the refactor lands, check both files' `_canonicalize` static methods byte-for-byte (sort_keys, separators, default for datetime/UUID/bytes). If they diverge, the PRD becomes a two-step plan: align them in-place first (no behavior change in either), then de-duplicate. **This is the single highest-priority pre-flight check.**

### R2 — Backend service may have audit-chain features ai-backend doesn't have (or vice versa)

The 51-LOC delta could be feature drift, not noise. Backend owns 4 chains (per its file's plural-chain commentary in the audit-list route); ai-backend owns 1. Backend may have helpers the package needs to support.

**Mitigation:** Read both files diff-style before writing the package. The package must be the **union** of both feature sets, with each call site only depending on the subset it actually uses. Concretely: if backend needs a `verify_at_seq` and ai-backend doesn't, the package still ships `verify_at_seq` and ai-backend simply doesn't call it.

### R3 — Service boundary rule disputes

[Root CLAUDE.md](../../../../CLAUDE.md) says: "Don't create shared packages for small duplication — share only stable contracts and truly cross-cutting primitives." An HMAC chain is _exactly_ a cross-cutting primitive, but a maintainer could reasonably ask whether two services merit a new package. Compare to `packages/service-contracts/` which is constants-only.

**Mitigation:** This PRD is the documented justification. The audit chain is (a) cryptographic, (b) compliance-relevant, (c) used by every privileged action in both services, and (d) demonstrably drift-prone. If the answer is "no, accept the duplication," the right outcome is to update the audit-finding doc to mark this resolved-by-design rather than open. That's a valid landing.

### R4 — Test fixture migration

Tests for both services may construct chain rows directly with hex-encoded keys hard-coded in the test file ([`test_audit_chain.py`](../../tests/unit/agent_runtime/observability/test_audit_chain.py) does — see line 24's `b"ai-backend-test-key-32-bytes-long-x"`). Moving the signer doesn't change those, but if any test imports an internal symbol (e.g. `_canonicalize`), the move could break the test.

**Mitigation:** Grep both services' test trees for any non-public-symbol imports of `audit_chain` before publishing the package's API. Promote required internals to public, or rewrite the affected test to use a public surface.

### R5 — Worker-side error swallowing changes

[`runtime_worker/audit.py`](../../src/runtime_worker/audit.py) catches every exception from `write_audit_log` and logs it (audit failures must never break the worker, see [worker tests](../../tests/unit/runtime_worker/test_worker_audit.py#L257)). The chain's `from_env` raises `RuntimeError` if keys are missing in production. If the package's exception types change shape, the worker's `except Exception` still catches everything, but a downstream alert/SIEM rule might key off the specific exception class.

**Mitigation:** Preserve exception class names exactly (`RuntimeError` from `from_env` in particular). Emit log events with the same `error_class` field shape.

### R6 — Versioning the package

Once `audit-chain` exists, both services pin its version in `requirements.txt` (or wherever the shared install lives). If only one service updates and the canonical form changes, signature compatibility breaks. Worse, signature compatibility could break asymmetrically — service A re-signs in the new form and now service B can't verify A's rows (if they ever cross-verify, which they don't today, but might tomorrow).

**Mitigation:** Pin both services to the same version in CI; add a CI check that fails if the two services pin different versions. Document in the package README: "any change to canonical form is a breaking change; bump major version."

### R7 — Operational artifacts

Production runbooks may reference the file path `services/ai-backend/src/agent_runtime/observability/audit_chain.py` (e.g., "if signature mismatches, run this verifier from this file"). Moving the file breaks those references.

**Mitigation:** Search runbooks (`docs/`, internal wikis if accessible). Update them in the same change. If there's no inventory, leave a redirect or note in the now-deleted location for a release.

### R8 — Backwards compat with the dev sentinel key

In dev (`RUNTIME_ENVIRONMENT != production`), `from_env` returns a hardcoded sentinel key (`b"dev-audit-hmac-sentinel-key-32by"` per [audit_chain.py:85](../../src/agent_runtime/observability/audit_chain.py#L85)). If this changes, every dev fixture / pre-recorded chain row breaks. This must remain byte-identical.

**Mitigation:** Sentinel key value is part of the test surface. Pin it.

## Unit testing requirements

Per [docs/CLAUDE.md](../CLAUDE.md), unit tests are required as part of the PRD. The new package must ship with tests that cover:

1. **Signer determinism**: same `(prev_hash, payload)` produces the same signature — already covered by `test_sign_is_deterministic`.
2. **Prev-hash linkage**: changing `prev_hash` changes the signature — already covered by `test_signature_changes_with_prev_hash`.
3. **Key length validation**: `< _MIN_KEY_BYTES` raises `ValueError` — covered.
4. **Clean chain verifies**: 25-row chain returns `ChainVerificationResult(ok=True)` — covered.
5. **Tampering breaks chain**: payload mutation, row removal, signature byte-flip each break verification with the right `broken_at_seq` and reason — covered.
6. **Key rotation**: a verifier holding two key versions can verify a chain that switched keys mid-stream — covered.
7. **Fail-closed in production**: `from_env` with `RUNTIME_ENVIRONMENT=production` and no `AUDIT_HMAC_KEY` raises — covered (in `from_env`'s code path; should be a unit test in the package).
8. **Dev sentinel key**: `from_env` in development with no key returns a `(0, sentinel_key)` mapping — must be byte-identical to today's value.
9. **Canonical form stability** (NEW — added by this refactor): a fixture of pre-refactor signed rows verifies under the new package. This is the single most important new test; it's the proof the refactor is signature-compatible.
10. **Cross-service compat** (NEW): equivalent fixture for backend's chain.

Plus, **integration tests in each service** must continue to pass unchanged:

- [`tests/unit/agent_runtime/observability/test_audit_chain.py`](../../tests/unit/agent_runtime/observability/test_audit_chain.py) — verifies the InMemoryStore integration produces a verifiable chain.
- [`tests/unit/runtime_api/test_audit_list_route.py`](../../tests/unit/runtime_api/test_audit_list_route.py) — verifies HTTP shape, RBAC.
- [`tests/unit/runtime_api/test_audit_cursor.py`](../../tests/unit/runtime_api/test_audit_cursor.py) — verifies SIEM cursor.
- [`tests/unit/runtime_worker/test_worker_audit.py`](../../tests/unit/runtime_worker/test_worker_audit.py) — verifies emitter contract, error swallowing, no-content-leak invariant.

---

## What the audit chain does (functionalities served)

This section maps every behavior the chain currently provides. Anything not listed here is out of scope for the refactor and must not be silently changed.

### F1 — Tamper-evident audit log

For each privileged action, the system writes one row to `runtime_audit_log` with chain fields `(seq, prev_hash, signature, key_version)`. Removing, reordering, or modifying a row breaks chain verification at the affected `seq` with a typed reason (`prev_hash mismatch` or `signature mismatch`).

### F2 — Per-org chain isolation

Each org has its own chain. The first row written for `org_a` has `seq=1` and `prev_hash=NULL`. Rows for `org_b` start their own chain. This means:

- A breach into one org's history doesn't tell you anything about another org's chain head.
- The Postgres advisory lock keys on `(table, org_id)` so concurrent writes to _different_ orgs do not serialize against each other.
- Verification of any single org's chain is self-contained.

### F3 — HMAC-SHA256 with key versioning

Each row carries a `key_version` (smallint). The signer holds the active key plus zero or more previous keys. New rows sign with the active key; verification looks up the row's declared version. Rotation is mid-chain-safe — once a row exists with `key_version=N+1`, all subsequent rows use `N+1`, but old rows with `key_version=N` still verify as long as the verifier holds key `N`.

### F4 — Fail-closed startup in production

If `RUNTIME_ENVIRONMENT=production` and `AUDIT_HMAC_KEY` is unset, `from_env` raises and the process refuses to start. In dev, a hardcoded sentinel key is used so local development doesn't require a key configured. The dev sentinel must remain byte-identical to today.

### F5 — Per-row HMAC with bound action identity

The signed payload includes `__event_type__: <action>` so an attacker can't substitute one action for another even with identical other fields. Combined with the prev-hash linkage, this means re-ordering rows or copying a row from one chain to another is detected.

### F6 — Three-layer immutability (DB-side)

The chain provides _integrity proof_; the database provides _immutability_ via two further layers:

1. **`audit_writer` Postgres role**: `INSERT, SELECT` only. `UPDATE, DELETE, TRUNCATE` revoked. The ai-backend's audit-emitting code paths must connect as this role in production. Set up by [`migrations/0003_audit_hardening.sql`](../../migrations/0003_audit_hardening.sql).
2. **`runtime_audit_log_immutable_guard` constraint trigger**: raises an exception on any UPDATE or DELETE regardless of role. Catches accidental admin migrations and `SECURITY DEFINER` bypasses. Set up by the same migration.

The chain catches tampering that bypasses both. Together they form defense in depth: role limits attack surface, trigger catches accidents, chain catches everything else.

### F7 — Field-level encryption of metadata

The `metadata_json_redacted` column on `runtime_audit_log` is encrypted via [`FieldCodec.encrypt_jsonb`](../../src/runtime_adapters/postgres/runtime_api_store.py#L1672) using AES-256-GCM with AAD bound to `(table, column, org_id)`. The chain signs the _plaintext metadata_ (not the ciphertext) so signature verification is independent of encryption rotation.

The SIEM cursor decrypts metadata before returning rows. The audit-list route returns redacted (encrypted-shape) metadata.

### F8 — SIEM export cursor

`/internal/v1/audit/cursor` ([routes.py:1431](../../src/runtime_api/http/routes.py#L1431)) is the SIEM pump's read source. It:

- Runs cross-tenant under the `worker` Postgres role.
- Returns rows ordered by `(created_at ASC, id ASC)` for monotonic cursor advancement.
- Limits to `[1, 1000]` rows per call.
- Decrypts metadata (so SIEM gets plaintext for analysis).
- Requires the `ADMIN_AUDIT_EXPORT` scope; service-token auth.

### F9 — In-product audit log UI

`/internal/v1/audit/list` ([audit_list_routes.py](../../src/runtime_api/http/audit_list_routes.py)) serves the in-product Settings → Members → Audit log surface (PR 7.1). It:

- Is org-scoped (caller's identity must match the URL's `org_id`/`user_id`).
- Returns a `chain` sub-object so external verifiers can re-verify.
- Supports filters: `action_prefix`, `actor_user_id`, `since`, `until`, opaque `cursor`, `limit ∈ [1, 200]`.
- Requires `ADMIN_AUDIT_EXPORT` scope.
- Composed by the facade with the backend's audit chains into a unified `GET /v1/audit`.

### F10 — Worker-side typed emission

[`WorkerAuditEmitter`](../../src/runtime_worker/audit.py) provides typed methods (`emit_run_started`, `emit_approval_decision`, `emit_tool_call_outcome`, `emit_conversation_fork`, etc.) that:

- Build a typed metadata dict (no LLM I/O, no payload content — counts, classes, outcome enums only).
- Call `persistence.write_audit_log` (which transparently applies the chain).
- Catch any exception from the store, log it, and return — audit emission must never break the worker.

### F11 — API-side direct emission

[`RuntimeApiService`](../../src/agent_runtime/api/service.py) calls `persistence.write_audit_log` directly at ~16 call sites, plus [`share_service.py`](../../src/agent_runtime/api/share_service.py), [`draft_service.py`](../../src/agent_runtime/api/draft_service.py), [`conversation_fork.py`](../../src/agent_runtime/api/conversation_fork.py), [`self_fork.py`](../../src/agent_runtime/api/self_fork.py), [`mcp_discovery_service.py`](../../src/agent_runtime/api/mcp_discovery_service.py). Audit identifies the _request_ (the API accepted/rejected it); worker audit identifies the _outcome_ (the worker did/failed).

### F12 — GDPR `delete_user_history` chains a deletion event

[`InMemoryRuntimeApiStore.delete_user_history`](../../src/runtime_adapters/in_memory/runtime_api_store.py#L841) and the Postgres equivalent both append a `user_history_deleted` audit row with the GDPR reason in metadata. The chain proves the deletion happened (and proves it wasn't tampered with after).

---

## Code surface map

Every file that participates in the audit-chain subsystem. The refactor must touch all of them or none.

### Core (in scope to move/delete)

| File                                                                                                 | Role                                                    | Refactor action                 |
| ---------------------------------------------------------------------------------------------------- | ------------------------------------------------------- | ------------------------------- |
| [`agent_runtime/observability/audit_chain.py`](../../src/agent_runtime/observability/audit_chain.py) | HMAC signer, key rotation, `from_env`, canonicalization | **Delete after extraction**     |
| `services/backend/src/backend_app/audit_chain.py`                                                    | Same primitives, slight drift                           | **Delete after extraction**     |
| (new) `packages/audit-chain/src/audit_chain/signer.py`                                               | The shared primitive                                    | **Create** (unification target) |
| (new) `packages/audit-chain/pyproject.toml`                                                          | Package manifest                                        | **Create**                      |
| (new) `packages/audit-chain/tests/test_signer.py`                                                    | Package-level tests                                     | **Create**                      |

### Integration (in scope to update imports only)

| File                                                                                                                    | What it does                                                 | Refactor action                                            |
| ----------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ | ---------------------------------------------------------- |
| [`runtime_adapters/in_memory/runtime_api_store.py:802`](../../src/runtime_adapters/in_memory/runtime_api_store.py#L802) | `_sign_audit_record` calls signer                            | Update import                                              |
| [`runtime_adapters/postgres/runtime_api_store.py:1646`](../../src/runtime_adapters/postgres/runtime_api_store.py#L1646) | `write_audit_log` calls `AuditChainSigner.from_env()`        | Update import                                              |
| [`runtime_api/http/audit_list_routes.py`](../../src/runtime_api/http/audit_list_routes.py)                              | Reads chain fields off rows                                  | No change (adapter returns the row dict; no signer import) |
| [`runtime_worker/audit.py`](../../src/runtime_worker/audit.py)                                                          | No direct signer import — uses `persistence.write_audit_log` | No change                                                  |
| [`runtime_api/http/routes.py:1431`](../../src/runtime_api/http/routes.py#L1431)                                         | `audit_cursor` route — reads `list_audit_log_for_export`     | No change                                                  |

### Schema / persistence (not touched)

| File                                                                                                 | Why excluded                                 |
| ---------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| [`migrations/0003_audit_hardening.sql`](../../migrations/0003_audit_hardening.sql)                   | Schema is unchanged: trigger, role, columns. |
| [`agent_runtime/persistence/records/audit.py`](../../src/agent_runtime/persistence/records/audit.py) | `AuditLogRecord` Pydantic schema unchanged.  |
| [`agent_runtime/persistence/encryption.py`](../../src/agent_runtime/persistence/encryption.py)       | Field encryption is independent.             |

### Tests (in scope to update imports only)

| File                                                                                                                             | Action                                     |
| -------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ |
| [`tests/unit/agent_runtime/observability/test_audit_chain.py`](../../tests/unit/agent_runtime/observability/test_audit_chain.py) | Update import path; assertions unchanged   |
| [`tests/unit/runtime_api/test_audit_list_route.py`](../../tests/unit/runtime_api/test_audit_list_route.py)                       | No change (doesn't import signer directly) |
| [`tests/unit/runtime_api/test_audit_cursor.py`](../../tests/unit/runtime_api/test_audit_cursor.py)                               | No change                                  |
| [`tests/unit/runtime_worker/test_worker_audit.py`](../../tests/unit/runtime_worker/test_worker_audit.py)                         | No change                                  |
| `services/backend/tests/.../test_audit_chain.py` (if present)                                                                    | Update import path; assertions unchanged   |

### Docker / CI (in scope)

| File                                                  | Action                                                                       |
| ----------------------------------------------------- | ---------------------------------------------------------------------------- |
| `services/ai-backend/Dockerfile`                      | Install `packages/audit-chain` (mirror existing `service-contracts` install) |
| `services/backend/Dockerfile`                         | Same                                                                         |
| `services/ai-backend/requirements.txt`                | Add `audit-chain` dependency                                                 |
| `services/backend/requirements.txt`                   | Same                                                                         |
| Root `Makefile` (if it has per-service install steps) | Add `audit-chain` to setup                                                   |

---

## User flows the audit chain covers

Every user-visible action that produces a chained audit row. Loosely grouped; each is a row that must verify after the refactor.

### Conversation lifecycle

| Action                                                   | event_type                       | Emitter                                                                         |
| -------------------------------------------------------- | -------------------------------- | ------------------------------------------------------------------------------- |
| User starts a new conversation                           | `conversation_created`           | API ([service.py:262](../../src/agent_runtime/api/service.py#L262))             |
| User updates a conversation (title, etc.)                | `conversation_update`            | API                                                                             |
| User deletes a conversation                              | `conversation_delete`            | API                                                                             |
| User restores a deleted conversation                     | `conversation_restore`           | API                                                                             |
| User updates per-conversation connector scope            | `conversation.connectors_update` | API                                                                             |
| User forks a shared conversation into their workspace    | `conversation.fork`              | Worker (via [`emit_conversation_fork`](../../src/runtime_worker/audit.py#L238)) |
| User self-forks (branches a conversation from a message) | `conversation.fork`              | Worker (same emitter, distinguished by `from_message_id`)                       |

### Run lifecycle

| Action                                   | event_type                                | Emitter                                                              |
| ---------------------------------------- | ----------------------------------------- | -------------------------------------------------------------------- |
| User submits a turn (run created in API) | `run_created`                             | API                                                                  |
| Run enqueued                             | `run_queued`                              | API                                                                  |
| Worker starts the run                    | `run_started`                             | Worker ([`emit_run_started`](../../src/runtime_worker/audit.py#L90)) |
| Run completes successfully               | `run_completed`                           | Worker                                                               |
| Run fails (exception)                    | `run_failed`                              | Worker                                                               |
| Run times out                            | `run_timed_out`                           | Worker                                                               |
| User cancels a run                       | `run_cancelling` / `run_cancel_requested` | API                                                                  |

### Approval workflow (incl. MCP auth)

| Action                                         | event_type                                         | Emitter      |
| ---------------------------------------------- | -------------------------------------------------- | ------------ |
| Approval requested mid-run                     | `approval_requested`                               | API          |
| User approves/rejects                          | `approval_decision` / `approval_decision_recorded` | Worker / API |
| User undoes a decision                         | `approval_undo_requested`                          | API          |
| Forwarded to another reviewer                  | `approval_forwarded` / `approval.forward`          | API          |
| Sweeper rejects (expired / membership revoked) | `approval_decision` with `actor_type=system`       | Worker       |

### Tool calls

| Action                                | event_type          | Emitter                                                                     |
| ------------------------------------- | ------------------- | --------------------------------------------------------------------------- |
| Tool call completed (success/failure) | `tool_call_outcome` | Worker ([`emit_tool_call_outcome`](../../src/runtime_worker/audit.py#L204)) |

### Sharing

| Action            | event_type                                       | Emitter                                                                    |
| ----------------- | ------------------------------------------------ | -------------------------------------------------------------------------- |
| Share created     | `conversation.share.created`                     | API ([share_service.py](../../src/agent_runtime/api/share_service.py#L73)) |
| Share updated     | `conversation.share.updated`                     | API                                                                        |
| Share revoked     | `conversation.share.revoked`                     | API                                                                        |
| Recipient added   | `conversation.share.recipient_added`             | API                                                                        |
| Recipient removed | `conversation.share.recipient_removed`           | API                                                                        |
| Recipient viewed  | `conversation.share.viewed` (rate-limited 1/min) | API                                                                        |
| Recipient denied  | `conversation.share.view_denied`                 | API                                                                        |

### Workspace admin

| Action                       | event_type                            | Emitter |
| ---------------------------- | ------------------------------------- | ------- |
| Workspace defaults updated   | `workspace.defaults_update`           | API     |
| Behavior overrides updated   | `workspace.behavior_overrides_update` | API     |
| Training opt-out toggled     | `workspace.training_opt_out_update`   | API     |
| Workspace export requested   | `workspace.export_request`            | API     |
| Workspace deletion attempted | `workspace.delete_attempt`            | API     |

### GDPR / data lifecycle

| Action                       | event_type             | Emitter                                |
| ---------------------------- | ---------------------- | -------------------------------------- |
| User history deletion (GDPR) | `user_history_deleted` | Persistence (in `delete_user_history`) |

### Total

Roughly **30+ distinct event types**. Every one of them lands as a chained row in `runtime_audit_log`. The refactor must not break any of them.

---

## Why the original finding was wrong

Worth stating plainly so future readers don't repeat the misanalysis. The original audit's specific claims:

> **Audit log integrity is normally the receiving SIEM's job, not the source application's.**

True only when there's no integrity gap between write and export. In practice:

- The SIEM pump runs on a cursor, not a tail. Pull cadence is operator-tunable but typically minutes-to-hours.
- During an outage of the pump (or the network between ai-backend and the pump), rows accumulate in `runtime_audit_log`. An attacker who compromises the database in that window could rewrite rows before they are exported.
- The chain detects exactly this. SIEM-side detection only catches tampering _after_ export.

> **Hash chaining at the source assumes nothing else writes to the table.**

True. And the system enforces it via the `audit_writer` Postgres role + the constraint trigger. So the assumption is met.

> **Requires careful lock discipline on append.**

True, and discharged via `pg_advisory_xact_lock` keyed on `(table, org_id)`. Lock scope = transaction commit. This is well-understood lock discipline; not a hidden complexity.

> **Complicates legitimate operational tasks (re-encrypting columns, retention sweeps).**

Partially true. Encrypting columns ≠ touching the chain because the chain signs plaintext metadata, not ciphertext (verified in [postgres adapter:1671](../../src/runtime_adapters/postgres/runtime_api_store.py#L1671): "the HMAC chain is the load-bearing tamper guard, not the metadata"). Retention sweeps that delete rows DO break the chain — but the trigger forbids deletes, so retention against `runtime_audit_log` runs only via admin-blessed bulk operations that explicitly accept the chain break and start a new era. This is a documented trade-off, not a problem.

**Conclusion:** the chain is well-designed and load-bearing. The right refactor is the duplication elimination, not deletion.

---

## Refactor plan

### Phase 0 — pre-flight (1 day, no code change)

Before any code lands:

1. Diff the two `audit_chain.py` files line-by-line. Specifically compare `_canonicalize` byte-for-byte. Document any divergence in this PRD.
2. Inventory backend's audit_chain features that don't exist in ai-backend's. The package must support the union.
3. Grep both services' tests for non-public-symbol imports of `audit_chain`. Decide which internals to promote.
4. Inventory operational runbooks for path references to `audit_chain.py`.

**Exit criteria:** A short addendum to this PRD listing any divergence, the chosen union API, and any required test rewrites.

### Phase 1 — create the package (1 PR)

1. Create `packages/audit-chain/` with `pyproject.toml`, `src/audit_chain/signer.py`, `src/audit_chain/__init__.py`, `tests/test_signer.py`.
2. Copy ai-backend's implementation as the starting point.
3. Apply any union-API additions identified in Phase 0.
4. Tests run in isolation: `cd packages/audit-chain && pytest`.

**This PR ships zero behavior change.** No service imports the new package yet.

### Phase 2 — wire ai-backend to the package (1 PR)

1. Add `audit-chain` to `services/ai-backend/requirements.txt` and Dockerfile.
2. In ai-backend, change every import of `agent_runtime.observability.audit_chain` to `audit_chain` (the package).
3. Delete `agent_runtime/observability/audit_chain.py`.
4. Run the full ai-backend test suite. Confirm all pass.
5. **Compatibility fixture**: add `tests/unit/agent_runtime/observability/test_audit_chain_compat.py` with hex-encoded `(payload, prev_hash, signature, key_version)` fixtures captured from a pre-refactor run, asserting the new package verifies them.

**Exit criteria:** ai-backend full suite green; compat fixture passes.

### Phase 3 — wire backend to the package (1 PR)

Same as Phase 2 but for `services/backend/`. Independent PR; could land before, after, or in parallel with Phase 2.

**Exit criteria:** backend full suite green; backend's compat fixture passes.

### Phase 4 — close the audit finding (1 PR)

1. Update [`docs/architecture/refactor-audit.md`](../architecture/refactor-audit.md): mark finding 1.3 as `Resolved (de-duplicated, not deleted)` with a link to this PRD.
2. Update [`docs/architecture/index.md`](../architecture/index.md) to point at the new package as the home of the chain primitive.
3. Update [`docs/specs/11-persistence-org-scoping-audit.md`](../specs/11-persistence-org-scoping-audit.md) if it references the file path.

**Exit criteria:** docs reflect new reality.

### Total

- ~250 LOC added in the package (plus tests, ~150 LOC).
- ~465 LOC deleted across the two services (207 + 258).
- Three to four PRs, each independently revertible.
- Zero schema changes.
- Zero behavior changes.
- Zero new dependencies.

---

## Why this refactor (and not another)

The narrative arc:

- **Original ask:** "stop building bespoke things." The chain _looks_ bespoke from outside.
- **What investigation found:** the chain is the right tool — DB role + trigger handles immutability, chain handles tamper-evidence, SIEM cursor handles export integrity. Each layer has a distinct job. The SIEM-only alternative would weaken the in-app integrity window.
- **What investigation also found:** the same primitive is duplicated and drifting. _That_ is the actual smell.
- **The refactor proposed:** extract to a shared package. Removes the duplication, preserves all behaviors, doesn't violate the service-boundary rule (cross-cutting primitive is exactly what `packages/` is for), keeps every test passing, ships in small reversible PRs.

If the team's preference is to leave well enough alone — accept the duplication, mark the finding resolved-by-design, and move on — that's also a legitimate landing. The duplication has been stable enough to drift only by 51 LOC in however long both files have existed; the cost of "do nothing" is bounded.

The PRD recommends extraction because the cost is small (~1 week of focused work, three small PRs) and the benefit (one canonical implementation, no drift, easier reasoning about chain semantics across services) is durable. But "leave it alone" is on the table.

---

## Open questions for the reviewer

1. Is `packages/audit-chain` an acceptable new package, given the root CLAUDE.md guidance "share only stable contracts and truly cross-cutting primitives"?
2. Do we own the operational runbooks that might reference the old file path? If yes, they need updating; if not, this PRD should call that out.
3. Are there any audit-chain features in the backend file (line count delta) that ai-backend doesn't use today but the package should support? Phase 0 will answer this; it shouldn't block PRD approval.
4. Does the original architecture audit's finding "1.3 Custom hash-chained audit log" get marked resolved (extracted, not deleted) or is the team open to re-examining the SIEM-only approach in light of this investigation? My recommendation is the former.

---

## References

- [refactor-audit.md §1.3](../architecture/refactor-audit.md#13-custom-hash-chained-audit-log) — original finding
- [audit_chain.py](../../src/agent_runtime/observability/audit_chain.py) — current ai-backend implementation
- [migrations/0003_audit_hardening.sql](../../migrations/0003_audit_hardening.sql) — schema, role, trigger
- [docs/specs/11-persistence-org-scoping-audit.md](../specs/11-persistence-org-scoping-audit.md) — org-scoping policy
- [docs/CLAUDE.md](../CLAUDE.md) — PRD format rules
- Root [CLAUDE.md](../../../../CLAUDE.md) — service boundary rules and `packages/` policy

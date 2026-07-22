# P6 — Safe{Wallet} propose-only signing — BUILD-READY v2 (authoritative)

**Status:** build spec · **Supersedes:** the Safe sections of `phases/P6-ENG-plan.md` v1 (Track W, §1–§3, §5, §6, §7). Where this doc and v1 disagree, **this doc wins**. The Sheets half of v1 (Track S, §4) is unaffected and lives in `phases/P6-BUILD-Sheets.md`.
**Binding inputs:** `phases/PRD-P6a-hardened.md` (Controls A/B/C + the 13-row finding table), `phases/P6-plan-review.md` (its 1 high + 3 medium + 1 low + 1 info are **binding fixes folded below**), the four RESOLVED decisions in `STATUS.md §Open decisions` (A/B/C). This is **real-funds signing code**: every control lands as a real enforcement point with a regression test, never a comment or a client-only check.

Every `file:line` below was re-verified against HEAD of `claude/0xcopilot-first-run-onboarding-d7eb30` on 2026-07-22. Paths are relative to ROOT.

---

## 0. Anchor table — verified at HEAD (use these, not the PRD's stale numbers)

| Subject                             | Verified anchor (HEAD)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `class Tool` / `class ApprovalKind` | `agent_runtime/api/constants.py` — `Tool` **L229** (`ASK_A_QUESTION="ask_a_question"` L232); `ApprovalKind` **L246** (`ACTION` L249, `ASK_A_QUESTION` L250, `MCP_AUTH` L251, `MCP_TOOL` L252). No `wallet_signature` / `request_wallet_signature` exists.                                                                                                                                                                                                                                                                                                                   |
| model-tool append site              | `agent_runtime/execution/factory.py` — `AskAQuestionTool` appended **L408–413**, `SuggestMcpConnectorTool` **L414–419**, gated Wave-1 tools appended **L424–427**; builder params `code_mode_tool`/`sandbox_execute_tool` **L333–334**; `_local_tool_names` **L465–490** (adds `ASK_A_QUESTION` L487).                                                                                                                                                                                                                                                                      |
| gated-tool wiring pattern           | `runtime_worker/capability_tool_wiring.py` — `CapabilityToolWiring`, `_DESKTOP_PROFILE="single_user_desktop"`, `_DEPLOYMENT_PROFILE_ENV="ENTERPRISE_DEPLOYMENT_PROFILE"`, `code_mode_tool()`→`None` when gated off; consumed in `runtime_worker/handlers/run.py` **L976–985**.                                                                                                                                                                                                                                                                                              |
| native interrupt dispatch           | `runtime_worker/stream_events.py` — `native_interrupt_payloads` **L632–662**: auth L646–649, ask L650–655, **fallthrough** to `native_tool_approval_payloads` **L656–660**. Insert the wallet-signature branch **between L655 and L656**.                                                                                                                                                                                                                                                                                                                                   |
| ask payload SPREADS                 | `stream_events.py` — `_native_ask_a_question_payload` **L729–764** builds via `{**payload, …}` at **L749–756** (the M2 hole if mirrored).                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| mcp_tool stamp                      | `stream_events.py` — `native_tool_approval_payloads` **L766–859** hard-codes `APPROVAL_KIND: "mcp_tool"` **L832**.                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| persisted approval metadata         | `stream_events.py` — `create_approval_request` **L371–406**; `metadata = dict(payload)` **L394** (the pydantic model is **never** instantiated on this path — an `extra="forbid"` schema alone is decorative).                                                                                                                                                                                                                                                                                                                                                              |
| read/write classifier               | `stream_events.py` — `_connector_action_is_read_only` **L909–918**; write-terms `create/post/send/update/delete/write`. (Sheets-only concern; not on the Safe path.)                                                                                                                                                                                                                                                                                                                                                                                                        |
| subagent-pause reason               | `stream_events.py` — `_SUBAGENT_INTERRUPT_REASONS` **L582–585**; ask refinement L602–607.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| bubble-morph gate                   | `stream_events.py` — `_approval_event_morphs_tool_bubble` **L347–369** returns `True` only for `MCP_AUTH` / `mcp_tool` → a free-standing `wallet_signature` gets its own card (no change needed; assert it).                                                                                                                                                                                                                                                                                                                                                                |
| decision entrypoint                 | `agent_runtime/api/approval_coordinator.py` — `record_approval_decision` **L286**; scope check **L309–316**; branch dispatch FORWARDED **L317**, SUGGEST_EDIT **L322**, APPROVE_WITH_EDITS **L327**, approve/reject default **L332**; `approval_kind` first read at **L356** (AFTER dispatch + record write).                                                                                                                                                                                                                                                               |
| suggest-edit gate                   | `approval_coordinator.py` — `_decide_suggest_edit` **L606**; gated **only** by `status is not PENDING` **L627** (no kind gate); child carries `edited_payload` in metadata **L668**.                                                                                                                                                                                                                                                                                                                                                                                        |
| approve-with-edits gate             | `approval_coordinator.py` — `_decide_approve_with_edits` **L795**; `_approval_supports_edits` **L820**; `_EDIT_CAPABLE_COMMIT_KINDS = {"draft_send"}` **L782** (so wallet_signature already 422s here — but suggest_edit does NOT).                                                                                                                                                                                                                                                                                                                                         |
| undo gate reads reversible          | `approval_coordinator.py` — `approval.metadata.get("reversible") != "yes"` at **L478** and **L520**.                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| forward guard                       | `approval_coordinator.py` — `APPROVAL_FORWARDABLE_KINDS = {ACTION, MCP_TOOL}` **L70–75**; kind check in `_guard_forwardable` **L1149–1159**.                                                                                                                                                                                                                                                                                                                                                                                                                                |
| status wire                         | `approval_coordinator.py` — `_wire_status_for` **L1202–1218**.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| decision request schema             | `runtime_api/schemas/approvals.py` — `ApprovalDecisionRequest` **L59**; `answer` L65, `edited_payload` L74, `edits` L80; `answer` normalizer only strips whitespace **L92–95**.                                                                                                                                                                                                                                                                                                                                                                                             |
| mcp metadata `extra="allow"`        | `approvals.py` — `McpApprovalMetadata` `model_config = ConfigDict(extra="allow")` **L294**; `APPROVAL_MAX_PARAMS = 6` **L280**; `ApprovalParam` **L283**.                                                                                                                                                                                                                                                                                                                                                                                                                   |
| approval enums                      | `runtime_api/schemas/common.py` — `ApprovalCategory` **L281** (READ/WRITE/ACTION), `ApprovalReasonCode` **L289** (`IRREVERSIBLE` L300), `ApprovalReversible` **L304** (YES/NO/`NOT_APPLICABLE`).                                                                                                                                                                                                                                                                                                                                                                            |
| replay projector                    | `runtime_api/schemas/events.py` — `_approval_requested_payload` **L699–748** (strict key allowlist; nested dicts `arguments`/`edited_payload` projected at **L734–742**); ask dispatch **L702–703**; `_ask_a_question_requested_payload` **L777–811**.                                                                                                                                                                                                                                                                                                                      |
| worker resume                       | `runtime_worker/handlers/approval.py` — resume gate requires `native_interrupt_id` present unless mcp_auth **L243–247**; `_resume_payload` **L640**; mcp_auth branch **L665–669**, ask branch (threads `command.answer`) **L670–675**.                                                                                                                                                                                                                                                                                                                                      |
| sweeper rejection-only              | `runtime_worker/jobs/approval_expiry_sweeper.py` — enqueues `ApprovalDecision.REJECTED` at **L220** (v1 cited `runtime_worker/approval_expiry_sweeper.py` — **corrected path is `jobs/`**).                                                                                                                                                                                                                                                                                                                                                                                 |
| MCP recheck bypassed by builtins    | `agent_runtime/capabilities/mcp/middleware/call_tool.py` — `is_server_card_authorized` re-check **L83–92**; a native builtin never reaches this path.                                                                                                                                                                                                                                                                                                                                                                                                                       |
| SIWE machinery                      | `services/backend/src/backend_app/identity/siwe.py` — `SiweService` **L518**; `mint_nonce` **L556**; `verify` **L600**; `link_wallet` (reuse pattern: proof prefix, no session, own audit) **L677–777**; `_recover_signer` (EIP-191 `Account.recover_message(encode_defunct(text=…))`) **L834–856**; `_consume_nonce` (single-use, address+chain bound) **L869–900**; `parse_allowed_chain_ids` **L489**; `SIWE_STATEMENT="Sign in to Copilot"` **L70**; `DEFAULT_ALLOWED_CHAIN_IDS=(1,8453,42161,4663)` **L85**. **SIWE proves control via `personal_sign`, NOT EIP-712.** |
| connectors store + write-through    | `services/backend/migrations/0044_connectors.sql` — `connectors` + `connector_audit_events`, RLS `connectors_tenant_isolation` (`app.current_org_id` / `app.role='admin'`), audit-chain columns; `ConnectorsService.write_through_from_mcp` `service.py` **L270–298**; `upsert_from_mcp_registration` `store.py` **L219/L347**.                                                                                                                                                                                                                                             |
| MCP catalog seed                    | `services/backend/src/backend_app/mcp_catalog.py` — `CatalogEntry` **L32** (default `auth_mode=OAUTH2`, `server_id="seed:<slug>"`); `McpAuthMode.NONE="none"` (`contracts.py` **L155**); `DEFAULT_CATALOG` **L83+**. (Review [info]#3: catalog is `backend_app/mcp_catalog.py`, **not** `backend_app/connectors/`.)                                                                                                                                                                                                                                                         |
| facade wallet page routes           | `services/backend-facade/src/backend_facade/wallet_page_routes.py` — `register_wallet_page_routes` **L36**; serves `wallet.html` + `/assets` from the **built frontend dist** (`settings.web_dist_dir` / `FACADE_WEB_DIST_DIR`) **L47–70**; registered in `app.py` **L145**.                                                                                                                                                                                                                                                                                                |
| facade proxy + identity             | `backend-facade/src/backend_facade/app.py` — `FacadeAuthenticator.authenticate_request` → verified `identity.{org_id,user_id}`; approval-decision route **L1114**, overrides `decided_by_user_id: identity.user_id` **L1127** (the "server-derived identity overrides client" pattern to copy); `_forward_json` **L1461–1499**.                                                                                                                                                                                                                                             |
| desktop loopback leak sites         | `apps/desktop/main/auth/loopback-server.ts` — `parseHandoffRedirect` reads `bearer_token` from `url.searchParams` **L319**; `parseWalletProofRedirect` reads `signature` from `url.searchParams` **L375**; `awaitLoopbackCode` **L390**, `awaitLoopbackHandoff` **L409**, `awaitLoopbackWalletProof` **L430** (all URL-query readers).                                                                                                                                                                                                                                      |
| no typed-data signing today         | `apps/frontend/src/features/auth/walletProof.ts` — `personal_sign` **L49**, `eth_chainId` **L33**, `isWalletUserRejection` (EIP-1193 `4001`) **L68**. **No `eth_signTypedData_v4`, no EIP-712 domain anywhere.**                                                                                                                                                                                                                                                                                                                                                            |
| client projection (fail-safe)       | `packages/chat-surface/src/destinations/run/approvalProjection.ts` — `RunApprovalKind = ApprovalsQueueItem["approvalKind"]` **L37**; `mapApprovalKind` **L317** defaults unknown→`"unknown"` **L328** (client fail-safe → M9 risk is purely server-side dispatch order); used at **L229**.                                                                                                                                                                                                                                                                                  |
| client resolve owner                | `packages/chat-surface/src/destinations/run/RunDestination.tsx` — `resolveApproval` **L523**; POST `/v1/agent/approvals/{id}/decision` **L549–550**.                                                                                                                                                                                                                                                                                                                                                                                                                        |
| tile suppression                    | `runtime_worker/stream_tools.py` — `internal_tool_names` frozenset **L48** (`WRITE_TODOS`, `ASK_A_QUESTION`).                                                                                                                                                                                                                                                                                                                                                                                                                                                               |

---

## 1. The four resolved decisions, restated as build law

| #          | Decision (STATUS.md)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | What it forces below                                                                                                                                    |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A**      | tx-simulation = self-hosted `eth_call`/anvil-fork **baseline** + operator-configurable Tenderly-style **upgrade**; fail-closed either way                                                                                                                                                                                                                                                                                                                                                                                      | §3. `SimulationProvider` seam; default `SelfHostedEthCallProvider`; no positive decoded asset-diff ⇒ `signable=false` ⇒ facade `/transactions` rejects. |
| **B**      | **owner-proven** bound-Safe: explicit Settings "add treasury Safe" gated by a **Safe-owner signature proof** (connecting wallet ∈ Safe owners via tx-service owners read) → writes `(org_id,user_id)→{safe_address,chain_id,added_at}` to a **new per-user `safe_bindings` store** on verified proof only; enforced **server-side at the facade** `/simulate`+`/transactions`; empty store ⇒ 403; agent-supplied `safe_address` can **never** seed/mutate it; separate `SAFE_SIGNING_ALLOWED_CHAIN_IDS` (default 1,8453,42161) | §2. **This closes the review's [high].**                                                                                                                |
| **C-safe** | adopt read-only Safe MCP (`5ajaki/safe-mcp-server`) for tx-read/decode/simulate-context; the propose→human-SIGN gate stays OUR client-side code; no third party ever routes the signature or holds a key                                                                                                                                                                                                                                                                                                                       | §4. Boundary drawn crisply; the binding-authority owner read is a **direct backend call**, not routed through the third-party MCP.                      |
| (fold)     | `P6-plan-review.md` fixes are binding                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | §5 folds all four Safe-touching mediums + the info projector note with exact anchors.                                                                   |

Decision B **retires** v1's "bindings captured at CONNECT" language entirely. The Safe connector is `auth_mode=NONE` (`mcp_catalog.py`; `McpAuthMode.NONE`), so its CONNECT has no ownership-capture step, and the connectors write-through (`service.py:270`) records only the denormalized connector _card_ (slug/status/scopes) — **never** a Safe address or owner proof. Binding provenance is therefore a **separate, human-driven, ownership-proven** action (§2), fully divorced from connector install.

---

## 2. Control C — OWNER-PROVEN bound-Safe (decision B) — **closes the review [high]**

### 2.1 Why the review said the v1 store was of "undefined provenance", and exactly how this closes it

The review's [high]: v1 said bindings were "captured at CONNECT", but the Safe connector is `NONE`-auth (no capture flow), no `safe_bindings` store existed, and if any path ever ingested the **agent-supplied** `safe_address`, Control C would be circular — the agent supplies the binding it is later checked against. "Server-derived identity" (the user) ≠ "server-verified Safe ownership".

**How this design closes it, precisely:**

1. **The only writer** of `safe_bindings` is `SafeBindingService.add_binding(...)` in `backend`, reachable **only** from the authenticated Settings route `POST /v1/wallet/safe/bindings` (facade → backend). No ai-backend module, no MCP handler, no worker, no tool result, and no facade `/simulate`|`/transactions` path can write it. A **CI import/grep gate** (§7-CI-4) asserts this.
2. `add_binding` writes **only** on a verified **owner signature proof**: the connecting wallet must (a) prove control of address `A` via a fresh single-use `personal_sign` challenge (reusing the SIWE recover path, §2.3), **and** (b) be a current owner of Safe `S` on chain `C` per the Safe **tx-service owners read** (a direct backend HTTPS call, §2.4). The `safe_address` in the write is the one **proven**, never one an agent named.
3. The facade `/simulate`+`/transactions` enforcement (§2.5) resolves bindings by the **verified session identity** (`FacadeAuthenticator` → `identity.{org_id,user_id}`), then asks backend "is `(safe,chain)` bound for this `(org,user)`?" The agent's claimed `safe_address` is compared **against** that answer; it can never become that answer.
4. Empty store ⇒ **403** at both endpoints (fail-closed): a fresh user with no binding cannot sign anything.

The invariant "bound-Safe enforced server-side against an owner-proven store" is now provably held: the write path requires cryptographic ownership proof, the read/enforcement path uses only server-derived identity, and the two are disjoint from agent input.

### 2.2 The Settings "add treasury Safe" action (human-driven, ownership-proven)

Flow (desktop + web share the shared surface; hosts differ only in how the wallet signs — §6):

```
Settings → Wallet → "Add treasury Safe"
  1. User enters (or picks) the Safe address S and target chain C.
  2. Client discovers an owner wallet via EIP-6963 (reuse eip6963.ts) and connects it → address A, live chainId.
  3. Client requests a BINDING challenge: POST /v1/wallet/safe/bindings/challenge {safe_address:S, chain_id:C, owner_address:A}
        → backend mints a single-use, (A,C,S)-bound nonce (TTL 5 min) and returns the exact EIP-4361-style message to sign
          with statement = SAFE_BINDING_STATEMENT (DISTINCT from SIWE_STATEMENT — §2.3).
  4. Wallet personal_sign(message)  → signature σ.        (desktop: system-browser page → loopback POST body, §6)
  5. Client submits proof: POST /v1/wallet/safe/bindings {safe_address:S, chain_id:C, message, signature:σ}
  6. Backend SafeBindingService.add_binding:
        a. parse+validate message (reuse SIWE strict parse), assert statement == SAFE_BINDING_STATEMENT.
        b. assert C ∈ SAFE_SIGNING_ALLOWED_CHAIN_IDS (NOT the SIWE login list).
        c. recover signer from (message, σ) via the SIWE _recover_signer path → address A'.
        d. consume the single-use nonce (must match A',C,S) → replay-proof.
        e. owners = SafeTxServiceOwnersClient.get_owners(safe=S, chain=C)   (direct backend HTTPS; §2.4)
        f. assert A' ∈ owners  (ownership proof) — else 403 not_owner, NO write.
        g. write (org_id,user_id)→{safe_address:S, chain_id:C, added_at:now} to safe_bindings; append safe_binding_audit row.
  7. Settings shows the bound Safe with a "remove" affordance (DELETE /v1/wallet/safe/bindings/{id}, owner-scoped).
```

Only step 6g writes the store, and only after 6a–6f all pass. The `safe_address` written is `S` **as it was signed over and owner-verified** — an agent that merely emits `safe_address=S` at propose-time reaches none of this.

### 2.3 Reuse the SIWE machinery (cite) — with a DISTINCT statement (adversarial requirement)

The proof reuses `services/backend/src/backend_app/identity/siwe.py` primitives — but must **not** reuse the login statement:

- **Reuse:** `parse_siwe_message`/`build_siwe_message` (strict EIP-4361 shape), `_recover_signer` (**siwe.py:834** — EIP-191 `Account.recover_message(encode_defunct(text=message))`, then `recovered_lower == parsed.address_lower` check), the single-use nonce store + `_consume_nonce` (**siwe.py:869** — address+chain-bound, TTL, `secrets.compare_digest`), `normalize_wallet_address`, `parse_allowed_chain_ids`.
- **New constant (MANDATORY):** `SAFE_BINDING_STATEMENT = "Bind a treasury Safe to Copilot"`, **distinct** from `SIWE_STATEMENT = "Sign in to Copilot"` (**siwe.py:70**). Rationale (adversarial): if the binding proof reused the login statement, a captured login signature could be **replayed** to seed a binding (and a binding signature could mint a login session). The statement is baked into both build + verify, exactly as SIWE bakes its own (siwe.py:610, `verify` asserts `parsed.statement == SIWE_STATEMENT`). `add_binding` asserts `parsed.statement == SAFE_BINDING_STATEMENT` and refuses anything else.
- **New nonce binding:** the binding nonce is bound to `(owner_address, chain_id, safe_address)` — a login nonce (bound to `(address, chain)` only) can never satisfy the binding consume, and vice-versa. Store the `safe_address` on the nonce record so `_consume` rejects a mismatched Safe.
- **Divergences from `verify` (mirror `link_wallet` at siwe.py:677):** no session mint, no login-attempt/lockout accounting, its own audit action (`safe.binding_added` / `safe.binding_add_rejected`), never calls `provision_personal_org`. `SafeBindingService` is a **new class** (`backend_app/wallet/safe_bindings/service.py`); it holds a `SiweService`-adjacent proof helper rather than logging in.
- **Chain allowlist:** the binding uses `parse_allowed_chain_ids` semantics but reads the **new** `SAFE_SIGNING_ALLOWED_CHAIN_IDS` env (default `1,8453,42161`), never `SIWE_ALLOWED_CHAIN_IDS`. See §3.2 / GD-1.

### 2.4 Owner read — a direct backend tx-service client, **outside** the third-party MCP trust path

The ownership check (§2.2 step 6e) is **security-critical** and must not depend on third-party MCP code. It is a small typed backend client, `backend_app/wallet/safe_bindings/tx_service_client.py`:

- `get_owners(safe, chain) -> frozenset[str]`: HTTPS `GET {SAFE_TX_SERVICE_URL[chain]}/api/v1/safes/{checksummed_safe}/` → parse `owners[]` → lowercase set. Per-chain base URL from a pinned map (env `SAFE_TX_SERVICE_URL_<chainId>`, defaults to the public `safe-transaction-<network>.safe.global` hosts for the allowlisted chains).
- **Sanitized errors (M6):** every upstream non-2xx / timeout / parse error → a typed `SafeOwnerReadUnavailable` with a fixed public message; the tx-service URL/host **never** appears in a response body or model-visible field. Fail-closed: an unavailable owner read ⇒ `add_binding` returns 503 `owner_read_unavailable`, **no write** (never "assume owner").
- **Why direct, not via `5ajaki/safe-mcp-server`:** the binding decision is the root of trust for every later signature. Routing it through third-party MCP code (even pinned) would put that code in the authority path. The adopted MCP is for **agent-facing** read/decode/simulate-context only (§4); the binding owner read is a first-party backend concern.

### 2.5 The `safe_bindings` store — schema, migration, tenant isolation

New store in `services/backend`, modeled byte-for-semantics on the shipped `connectors` store (migration `0044_connectors.sql`) — same RLS + append-only audit-chain pattern.

**Migration** `services/backend/migrations/0045_safe_bindings.sql` (also mirror into `backend_app/wallet/safe_bindings/schema.sql` for the module loader, as `connectors/schema.sql` does):

```sql
CREATE TABLE IF NOT EXISTS safe_bindings (
    id            TEXT        PRIMARY KEY,          -- opaque bnd_<...>
    tenant_id     TEXT        NOT NULL,             -- org_id (RLS key)
    user_id       TEXT        NOT NULL,             -- per-user binding
    safe_address  TEXT        NOT NULL,             -- lowercase 0x + 40 hex
    chain_id      BIGINT      NOT NULL,
    added_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- provenance of the proof that authorized this binding (audit, not trust):
    proven_owner_address TEXT NOT NULL,             -- the recovered signer A'
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- one binding per (tenant,user,safe,chain); re-add is idempotent upsert.
CREATE UNIQUE INDEX IF NOT EXISTS safe_bindings_unique
    ON safe_bindings (tenant_id, user_id, safe_address, chain_id);
CREATE INDEX IF NOT EXISTS safe_bindings_lookup
    ON safe_bindings (tenant_id, user_id, chain_id);

ALTER TABLE safe_bindings ENABLE ROW LEVEL SECURITY;
CREATE POLICY safe_bindings_tenant_isolation ON safe_bindings
    USING (tenant_id = current_setting('app.current_org_id', true)
           OR current_setting('app.role', true) = 'admin')
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));

CREATE TABLE IF NOT EXISTS safe_binding_audit_events (
    audit_id      TEXT        PRIMARY KEY,
    tenant_id     TEXT        NOT NULL,
    actor_user_id TEXT        NOT NULL,
    action        TEXT        NOT NULL,   -- safe.binding_added | safe.binding_removed | safe.binding_add_rejected
    target_id     TEXT        NOT NULL,   -- binding id (or safe:chain for a rejected attempt)
    before_state  JSONB,
    after_state   JSONB,
    correlation_id TEXT,
    ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    seq           BIGINT, prev_hash BYTEA, signature BYTEA, key_version INTEGER  -- audit-chain (packages/audit-chain)
);
ALTER TABLE safe_binding_audit_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY safe_binding_audit_tenant_isolation ON safe_binding_audit_events
    USING (tenant_id = current_setting('app.current_org_id', true)
           OR current_setting('app.role', true) = 'admin')
    WITH CHECK (tenant_id = current_setting('app.current_org_id', true));
-- GRANTs to enterprise_app mirror 0044_connectors.sql.
```

- **Never** stores tokens/signatures — only the binding facts + the proven owner address (audit provenance). The signature σ is verified and discarded; it is not persisted.
- **Service** `backend_app/wallet/safe_bindings/service.py`: `add_binding` (owner-proof-gated, §2.2), `list_bindings(org_id,user_id)`, `remove_binding` (owner-scoped, 404-not-403), `is_bound(org_id,user_id,safe,chain) -> bool`. Writes go through `with store.transaction():` binding-row + audit-row atomically (same discipline as `write_through_from_mcp`, service.py:286).
- **In-memory adapter** for tests/dev + **postgres adapter** for prod, selected the same way the connectors store is.

### 2.6 Facade enforcement point (server-derived identity, fail-closed)

New registrar `services/backend-facade/src/backend_facade/wallet_safe_routes.py`, mounted next to `register_wallet_page_routes` at `app.py:145`. All routes authenticate via `FacadeAuthenticator.authenticate_request(request)` → verified `identity.{org_id,user_id}` (the pattern at app.py:1120), and **never** trust a client/agent `safe_address` except as the value being _checked_.

Routes:

| Route                                      | Purpose                                            | Enforcement                                                                                        |
| ------------------------------------------ | -------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `POST /v1/wallet/safe/bindings/challenge`  | mint owner-proof nonce + message                   | forwards to backend; identity server-derived                                                       |
| `POST /v1/wallet/safe/bindings`            | submit owner proof → write binding                 | forwards to backend `add_binding`; identity server-derived; agent unreachable                      |
| `GET /v1/wallet/safe/bindings`             | list this user's bound Safes                       | backend `list_bindings(identity.org_id, identity.user_id)`                                         |
| `DELETE /v1/wallet/safe/bindings/{id}`     | remove a binding                                   | backend owner-scoped; 404-not-403                                                                  |
| `POST /v1/wallet/safe/{safe}/simulate`     | decoded+simulated preview                          | **assert `is_bound(identity, safe, chain)` → 403 if not**; chain ∈ allowlist; fail-closed sim (§3) |
| `GET /v1/wallet/safe/proposal/{ref}`       | canonical EIP-712 doc                              | `proposal_ref` is the read-only single-use short-TTL capability (§5.3); no signature/bearer        |
| `POST /v1/wallet/safe/{safe}/transactions` | submit SafeTx **with signature** to the tx-service | full Control-C stack below                                                                         |

`/transactions` enforcement stack (all server-side, on `identity`):

1. `is_bound(identity.org_id, identity.user_id, safe, chain)` → **403** `safe_not_bound` if false (empty store ⇒ always false).
2. `chain ∈ SAFE_SIGNING_ALLOWED_CHAIN_IDS` → **403** `chain_not_allowed`.
3. `operation == 1` (DELEGATECALL) → **reject** `delegatecall_blocked` (GD-2 hard-block, §7-INV-3).
4. **Re-simulate** server-side → fail-closed matrix (§3.3); no positive decoded asset-diff (non-native) ⇒ **reject** `simulation_failed`.
5. Single-use challenge valid **only while** `(approval_id, safe_tx_hash)` is PENDING (M11) → else reject.
6. Re-read Safe nonce at submit; abort on drift (M8); idempotent on `(safe, safe_tx_hash, signature)`.
7. Forward to safe-mcp SUBMIT (separate service token); **never log the signature body** (M2/M5) — the signature is not written to any log line, audit row, or model-visible field.

The renderer's disabled-button is cosmetic; **this facade re-check is the authority** (a compromised renderer that POSTs a decision cannot bypass steps 1–7).

### 2.7 Defense-in-depth pre-interrupt check (belt, not authority)

`request_wallet_signature` (§5.1) also calls a backend `is_bound` check before raising the interrupt, so a malicious agent that names an unbound Safe stalls early with a typed rejection rather than surfacing a card. This is belt-and-suspenders; the facade `/transactions` is the authority.

### 2.8 CI/unit tests that close the [high] (state precisely)

- **T-BIND-1 (agent cannot seed):** a hermetic real-graph run where a tool result auto-calls `request_wallet_signature{safe_address:X}` for an `X` **not** in `safe_bindings` → the pre-interrupt check rejects; if bypassed to the facade, `/simulate` and `/transactions` return **403**; assert the `safe_bindings` store row-count is **unchanged** (no seed).
- **T-BIND-2 (only writer):** import/grep gate — the sole caller of `SafeBindingService.add_binding` / `store.upsert_binding` is the authenticated Settings route; **no** symbol in `services/ai-backend`, in `wallet_safe_routes.py`'s `/simulate`|`/transactions` handlers, or in any MCP handler references the binding-write API. (§7-CI-4.)
- **T-BIND-3 (owner proof mandatory):** `add_binding` with a valid `personal_sign` from a wallet that is **not** a Safe owner → **403** `not_owner`, no write. With the owner read unavailable → **503** `owner_read_unavailable`, no write. With a login-statement signature (`SIWE_STATEMENT`) → rejected (`message_invalid`), no write.
- **T-BIND-4 (replay-proof):** a captured binding proof (message+σ) replayed a second time → nonce already consumed → rejected; a login nonce cannot satisfy a binding consume and vice-versa.
- **T-BIND-5 (tenant isolation):** user B (org-B) cannot read, use, or remove user A's binding; `is_bound` for (org-B,user-B,safeA) is false even though (org-A,user-A,safeA) is bound; RLS blocks a cross-tenant `safe_bindings` read.
- **T-BIND-6 (empty ⇒ 403):** a user with zero bindings gets 403 at `/simulate` and `/transactions` for any safe/chain.

---

## 3. Control B — self-hosted, fail-closed simulation (decision A)

Simulation is **safety-load-bearing** (no positive decoded asset-diff ⇒ sign disabled), so it must not depend on a griefable/leaky third-party key.

### 3.1 Baseline provider (shipped default)

`services/safe-mcp/.../simulation.py` behind a `SimulationProvider` interface. Default `SelfHostedEthCallProvider`:

- `eth_call` at the pending block against an operator-pinned read-only RPC (`SAFE_RPC_URL_<chainId>`). Compute the asset-diff for the decoded token set by pre/post `eth_call` balance reads (`balanceOf`/`allowance`) where `debug_traceCall`/state-override isn't available; use call-trace deltas where it is.
- Higher fidelity option behind `SAFE_SIM_MODE=anvil`: a per-request `anvil --fork-url $RPC --fork-block-number pending` for full state-override diffs (heavier ops).
- Returns `{ status: ok|reverted|unsupported|timeout|error, asset_diffs[], revert_reason }`.

### 3.2 Operator-configurable Tenderly-style upgrade seam

`TenderlySimulationProvider` implements the **same** `SimulationProvider` interface, selected by `SAFE_SIM_PROVIDER=tenderly` + `SAFE_SIM_TENDERLY_*` config. Never the default. Track "simulation coverage" per provider (STATUS.md §5.1) to decide if/when hosted becomes default. Provider-key hygiene (M6): every upstream error is caught in `simulation.py` and mapped to a fixed typed domain error with a safe public message **before** it can reach a tool result; a test asserts a simulated upstream 401 whose URL embeds a fake key yields a result with the key absent.

Chain identity (M1): the value-signing allowlist is the **new** `SAFE_SIGNING_ALLOWED_CHAIN_IDS` (default `1,8453,42161` = Ethereum + Base + Arbitrum), parsed with `parse_allowed_chain_ids` semantics but **distinct** from `SIWE_ALLOWED_CHAIN_IDS` (siwe.py:85). Before any wallet popup: assert wallet live `eth_chainId` == EIP-712 `domain.chainId` (facade-built) == validated `chain_id` == displayed chain; abort otherwise.

### 3.3 Fail-closed matrix (canonical — enforced at renderer AND facade `/transactions`)

| tx shape                                             | simulation                             | `signable`                                        | facade submit                     |
| ---------------------------------------------------- | -------------------------------------- | ------------------------------------------------- | --------------------------------- |
| native transfer, `data==""`, `operation==0`, value>0 | not required                           | **true**, labeled "unverified — no contract call" | allow                             |
| non-empty `data`, `operation==0`                     | positive asset-diff                    | **true**                                          | allow                             |
| non-empty `data`                                     | revert / unsupported / timeout / error | **false**                                         | **reject** `simulation_failed`    |
| decoded ERC-20 approve/transfer                      | positive asset-diff                    | **true**                                          | allow                             |
| decoded ERC-20 approve/transfer                      | sim unavailable                        | **false**                                         | **reject**                        |
| `operation==1` (DELEGATECALL)                        | —                                      | **false** (hard block)                            | **reject** `delegatecall_blocked` |
| undecodable `data` AND no positive sim               | —                                      | **false**                                         | **reject**                        |

Renderer disable is cosmetic; the facade re-simulation is the authority (§2.6 step 4). The bare native-value transfer with empty `data` + `operation==0` is the **only** sign-on-unsimulated case, shown "unverified".

Decoded-calldata authority (Control A, C1/H6): the card's To/Amount/Token/Method/Operation/Chain/AssetDiff rows come **only** from the server-computed `SafeDecodedEffect` (ABI-decode `transfer`/`transferFrom`/`approve`/`increaseAllowance` + known Safe selectors from `data`, cross-referenced against the simulation asset-diff). The agent's raw `to/value/data/summary` ride **only** in a labeled `agent_claims` zone, de-emphasized, never load-bearing; a decoded-vs-claimed mismatch ⇒ `signable=false`. The authoritative handle is `proposal_ref` (§5.3): the decoded+simulated block is stored server-side keyed by ref and re-resolved at every gate — every gate reads the server block, never the agent fields.

---

## 4. Control-C-safe — adopt the read-only Safe MCP; the sign gate is ours

**Adopt `5ajaki/safe-mcp-server`** (bundled + version-**pinned** + reviewed + run as a **local** server) for the agent-facing READ/DECODE/SIMULATE-CONTEXT plumbing only. Draw the boundary crisply:

| Concern                                                                                | Who                                                                 | Notes                                                                                                                     |
| -------------------------------------------------------------------------------------- | ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Safe info/owners/threshold/nonce/balances/pending queue (agent reads)                  | adopted MCP (read-only)                                             | session-approved connector reads; no keys                                                                                 |
| calldata decode + simulate-context for the card                                        | adopted MCP or our `decode.py`/`simulation.py`                      | either way the output is advisory _display_ context; the **facade re-decodes+re-simulates** as the authority (§2.6, §3.3) |
| build SafeTx + EIP-712 + `safeTxHash` + proposal store                                 | **ours** (`services/safe-mcp` read/propose module)                  | canonical doc keyed by `proposal_ref`                                                                                     |
| **the human SIGN gate** (`request_wallet_signature` interrupt, the EIP-1193 signature) | **OUR client-side code**                                            | §5, §6                                                                                                                    |
| submit signed SafeTx to the tx-service                                                 | **ours** (`services/safe-mcp` submit module, facade-reachable only) | separate service token; hard-split from read/propose                                                                      |
| the **binding owner read** (root of trust)                                             | **direct backend client** (§2.4)                                    | NOT the third-party MCP                                                                                                   |

**No third party ever routes the signature or holds a key.** The signature is produced in the user's own wallet (EIP-1193), returns to our client → facade → tx-service; it never enters the adopted MCP, the agent/graph/worker, or approval-decision state. CI grep-gate: no `eth_sign*`/private-key symbol anywhere in `services/safe-mcp`, and no submit symbol importable from the read/propose module (§7-CI-1/2). The read-only Safe MCP that the agent talks to is registered on the existing catalog→profile→install path (`safe-global` profile, all tools `product_scope: read`, `approval: session`) — the **only** `per_call` gate is our native `wallet_signature`.

---

## 5. The `wallet_signature` approval_kind wiring — folding the review's mediums with exact anchors

`wallet_signature` must be special-cased at every site `ask_a_question` is, and inserted into dispatch **before** the `mcp_tool` fallthrough. Below, each of the review's four Safe-touching findings is folded at its exact anchor.

### 5.1 The SIGN-gate builtin + constants + factory

| #   | File / anchor                                                           | Change                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| --- | ----------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `constants.py` `class Tool` **L229**, `class ApprovalKind` **L246**     | Add `Tool.REQUEST_WALLET_SIGNATURE = "request_wallet_signature"`, `ApprovalKind.WALLET_SIGNATURE = "wallet_signature"`.                                                                                                                                                                                                                                                                                                                                                                                                                              |
| 2   | `capabilities/tools/builtin/request_wallet_signature.py` **NEW**        | `RequestWalletSignatureTool` modeled on `AskAQuestionTool`. `ainvoke`: resolve `proposal_ref` → authoritative decoded+simulated block via the safe-mcp read client; **pre-interrupt `is_bound` check** (§2.7); raise `langgraph_interrupt({api_event_type:"approval_requested", approval_kind:"wallet_signature", approval_id, status:"pending", native_interrupt_id, safe:<server block>, agent_claims:{summary}, signable})`. `_resume_result` → `{ok, decision, status}` where `status` is a **non-secret enum**; it **never** reads a signature. |
| 3   | `factory.py` append near **L424–427**; `_local_tool_names` **L465–490** | Append `RequestWalletSignatureTool` via the gated-wiring pattern (`CapabilityToolWiring`-style: env flag `RUNTIME_ENABLE_SAFE_SIGNING` + `single_user_desktop` + BYOK gate → `None` when off, byte-identical otherwise; threaded through `RuntimeDependencies` + run.py like L976–985). Add `REQUEST_WALLET_SIGNATURE` to the `_local_tool_names` trusted set for collision checks. **No `interrupt_on` change** — the tool raises its own interrupt.                                                                                                |

### 5.2 [medium] `_native_wallet_signature_payload` must NOT `{**payload}` — construct from a fixed allowlist, validate through `extra="forbid"`

**Anchor:** `stream_events.py` — `_native_ask_a_question_payload` at **L749** spreads `{**payload, …}`; `create_approval_request` persists `metadata = dict(payload)` at **L394** (the pydantic model is never instantiated on the persistence path). If the new builder mirrors the ask spread, an agent-injected `reversible:"yes"`/`category`/`vendor` rides into `approval.metadata` and opens an undo window (the undo gate reads `approval.metadata.get("reversible")` at **L478/L520**).

**Build (no bandaid):**

- New `_native_wallet_signature_payload(interrupt_id, interrupt_value)` inserted in `native_interrupt_payloads` **between L655 and L656** (after ask, **before** the `native_tool_approval_payloads` fallthrough that stamps `"mcp_tool"` at **L832** — this is M9).
- It **does not spread** the interrupt value. It constructs a **closed** dict from a fixed server-side allowlist only: `api_event_type`/`event_type` (APPROVAL_REQUESTED), `approval_id`, `action_id`, `native_interrupt_id` (**required** — the resume gate at handlers/approval.py:243–247 drops payloads without it), `batch_id`, `batch_index`, `approval_kind="wallet_signature"`, `status="pending"`, the **server-computed** `safe` block + `signable`, and a **de-emphasized** `agent_claims` sub-object (`summary` only, length-capped). It **strips** any agent-supplied `reversible`/`category`/`vendor`/`params` from the interrupt value before projection.
- Pin as constants: `reversible = ApprovalReversible.NO` (common.py:308), `category = ApprovalCategory.ACTION` (common.py:286), `vendor = "SAFE"`, `reason_code = ApprovalReasonCode.IRREVERSIBLE` (common.py:300).
- Define `WalletSignatureApprovalMetadata` in `runtime_api/schemas/approvals.py` as a **standalone `RuntimeContract` with `model_config = ConfigDict(extra="forbid")`** — do **not** subclass `McpApprovalMetadata` (its `extra="allow"` at **L294** is the M2 hole). **Validate/re-serialize the payload THROUGH this model at projection time** so the closed shape is exactly what reaches `dict(payload)` at L394 — the `extra="forbid"` model is thereby load-bearing, not decorative.
- Also add `SafeDecodedEffect` + `SafeProposalSummary` to `approvals.py` (the typed `safe` block; wallet_signature does not use the flat `ApprovalParam`/`APPROVAL_MAX_PARAMS=6` model — generic connectors keep those).

**Test (persisted, not just schema):** the persisted `approval.metadata` for a wallet_signature has `reversible=="no"` **regardless of agent input**, and `request_approval_undo` **422s** for a wallet_signature.

### 5.3 [medium] Decision surface — reject non-{approved,rejected} at the TOP, before dispatch; recurse the hex-blob guard

**Anchor:** `approval_coordinator.py` — scope check **L309–316**; branch dispatch begins **L317** (FORWARDED), **L322** (SUGGEST*EDIT → `_decide_suggest_edit` **L606**, gated only by `status != PENDING` at **L627**, no kind gate), **L327** (APPROVE_WITH_EDITS → `_decide_approve_with_edits` **L795**, blocked for non-`draft_send` by `_EDIT_CAPABLE_COMMIT_KINDS` **L782**). `approval_kind` is first read at **L356** — \_after* dispatch. So a client can POST `{decision:"suggest_edit", edited_payload:{…}}` on a PENDING wallet_signature and mint a child approval carrying `edited_payload` in metadata (**L668**) — an agent-visible state channel. A hex signature can nest inside `edited_payload` as JSON.

**Build (no bandaid):**

- Read `approval_kind = approval.metadata.get(Keys.Field.APPROVAL_KIND)` **immediately after the scope check at L316, BEFORE the L317 branch dispatch**. If `approval_kind == "wallet_signature"` and `request.decision not in {APPROVED, REJECTED}` → **422** `wallet_signature_decision_unsupported`. This closes SUGGEST_EDIT **and** FORWARDED **and** APPROVE_WITH_EDITS for signing approvals in one pre-dispatch guard (do not rely on the individual branch guards; suggest_edit has none).
- At the **same pre-dispatch point**, apply the answer-guard for wallet_signature: constrain `request.answer` to the status enum `{signed, rejected, chain_not_allowed, not_owner, hash_mismatch, nonce_changed}`, cap length, and **422** on a hex-blob shape (`^0x[0-9a-fA-F]{100,}$`). The scan must **recurse** into `request.edited_payload` and `request.edits` JSON (not just top-level strings) — a nested `0x…` blob in `edited_payload` is rejected. (Belt: also add a schema-level, kind-agnostic hex-blob rejection on `answer` in `ApprovalDecisionRequest` at approvals.py — the normalizer at **L92–95** only strips whitespace today.)
- Wire `wallet_signature` status through `_wire_status_for` (**L1202**): `APPROVED → "signed"`, else the rejection code.
- Keep `wallet_signature ∉ APPROVAL_FORWARDABLE_KINDS` (**L70–75**) so `_guard_forwardable` (**L1149–1159**) 422s a forward even if the pre-dispatch guard were ever removed.

**Test:** `suggest_edit` / `forward` / `approve_with_edits` on a wallet_signature all **422**; a nested hex blob in `edited_payload` is rejected; a signature-shaped `answer`/`reason` → 422; the status enum round-trips.

### 5.4 [medium] Canonical-doc fetch — `proposal_ref` IS the capability; drop the main-minted token

**Anchor / problem:** the desktop `safe-sign` page runs out-of-process in the system browser; the only main→page channel is the URL main opens. v1 said the page carries "only `proposal_ref` + state" yet also "fetches the canonical doc authenticated by a one-time token minted by main" — contradictory, and any main-minted token in the URL re-introduces the H5 class of leak.

**Build (resolve explicitly):** make **`proposal_ref` itself** the read-only, unguessable (≥128-bit), single-use, short-TTL (seconds) capability the facade accepts for `GET /v1/wallet/safe/proposal/{ref}`. **Drop** the separate main-minted token entirely. The page opens with only `proposal_ref` + loopback `state` in the URL. Leaking `proposal_ref` via browser history exposes **only** the already-human-reviewed canonical EIP-712 doc — never a signature, never the session bearer — and confers **no submit** (submit is facade `/transactions` under the session bearer held by main, §6). The ref is consumed on first fetch (single-use) and expires in seconds.

**Test:** `proposal_ref` confers only canonical-doc read, not submit; no signature/bearer in any URL/history; a second fetch of a consumed ref 404s.

### 5.5 [info] Replay projector must project the nested `safe`/`agent_claims`/`signable` blocks

**Anchor:** `events.py` — `_approval_requested_payload` **L699–748** is a strict key allowlist that runs values through `cls._text(...)` and would **drop** nested `safe`/`agent_claims`/`signable`. Nested dicts are only preserved where explicitly handled (`arguments`/`edited_payload` at **L734–742**); ask has its own path dispatched at **L702–703** (`_ask_a_question_requested_payload` **L777**).

**Build:** add a `wallet_signature` dispatch branch (mirror the ask dispatch at L702–703) → `_wallet_signature_requested_payload` that preserves the nested `safe` decoded block + `agent_claims` (as objects, `isinstance(..., dict)`, like L734–742) and `signable` (bool). So a reconnect / `GET .../events` rebuilds the **authoritative** card (server-decoded), not an agent-field card. Confirm `wallet_signature` flows through `_SUBAGENT_INTERRUPT_REASONS` (stream_events.py:582) so a signature raised inside a subagent projects `subagent_paused`, and confirm `_approval_event_morphs_tool_bubble` (**L347–369**) returns `False` for it (free-standing card).

### 5.6 Worker resume + tile suppression (rows for completeness)

- `runtime_worker/handlers/approval.py` `_resume_payload` **L640** — add a `wallet_signature` branch (mirror ask **L670–675**) that threads **only** `command.answer` (already status-enum-guarded upstream) into `{approval_id, decision, answer}`; never a batch/outcome shape. The resume gate at **L243–247** already routes it correctly because the closed payload sets `native_interrupt_id` (§5.2).
- `runtime_worker/stream_tools.py` `internal_tool_names` **L48** — add `REQUEST_WALLET_SIGNATURE` so the running-tile `tool_call_started/result` events are suppressed (the approval card is the surface), exactly as `ASK_A_QUESTION` is.

### 5.7 Contracts + chat-surface

- `packages/api-types/src/index.ts` — add `"wallet_signature"` to the approval-kind unions (today `"mcp_tool" | "ask_a_question" | string` at index.ts:1632/1847/1887); add `SafeDecodedEffect`, `SafeProposalSummary`, `SafeSimulationResult`, `SafeSignRequest`, `SafeSignResult`, `WalletSignatureApprovalMetadata`.
- `packages/chat-surface/src/workspace/types.ts` — add `"wallet_signature"` to `ApprovalsQueueItem.approvalKind` (widens `RunApprovalKind`, approvalProjection.ts:37).
- `packages/chat-surface/src/destinations/run/approvalProjection.ts` — add `case "wallet_signature"` to `mapApprovalKind` (**L317**; today defaults to `"unknown"` at **L328** — client is fail-safe, so M9 is purely server-side dispatch order); project the typed `safe` block, `category={vendor:"SAFE",access:"ACTION"}`, `reversible:no`, `signable`; **title composed from decoded fields**, never `agent_claims.summary`.
- `packages/chat-surface/src/ports/safeSigning.ts` **NEW** — `SafeSigningPort { signSafeTransaction(req: SafeSignRequest): Promise<SafeSignResult> }`; host-injected; re-derive+re-simulate via facade, sign EIP-1193, submit via facade, return **non-secret** status; never returns/threads the raw signature.
- `packages/chat-surface/src/providers/SafeSigningProvider.tsx` + `useSafeSigning()` **NEW** — substrate-agnostic (no bare `window`/`fetch`).
- `packages/chat-surface/src/approvals/SafeSignAction.tsx` **NEW** — presentational review block; asset-diff `+`=`--color-success`, `-`=`--color-danger`; **disables sign when `signable==false`**.
- `packages/chat-surface/src/destinations/run/RunDestination.tsx` `resolveApproval` **L523** (POST at **L549–550**) — add the `wallet_signature` branch: call `safeSigning.signSafeTransaction` **before** POSTing; on `signed` POST `{decision:"approved", answer:<status>}` (**no signature field**); on `rejected`/`hash_mismatch`/`chain_not_allowed`/`not_owner`/`nonce_changed` POST `{decision:"rejected", reason:<code>}` or leave pending.
- `packages/chat-surface/src/index.ts` — export the new port/provider/component.

---

## 6. The dual-host signing surface

Both hosts drive the same `SafeSigningPort`; they differ only in how the wallet signs and how the signature returns.

### 6.1 Web host (`apps/frontend`)

`src/features/wallet/webSafeSigning.ts` + `src/api/safeApi.ts` **NEW**. In-page `eth_signTypedData_v4` — **a new path**: `walletProof.ts` today does only `personal_sign` (**L49**) + `eth_chainId` (**L33**); there is no EIP-712 anywhere. Reuse `eip6963.ts` wallet discovery + `walletProof.ts` `isWalletUserRejection` (4001, **L68**). Before the popup: re-derive+compare `safeTxHash` (mismatch throws before signing), assert the four-way chain cross-check (§3.2), short-circuit if not-owner. Mount `SafeSigningProvider` in `src/app/App.tsx`. The signature goes renderer → facade `/transactions`; it never enters agent/graph state.

### 6.2 Desktop host (`apps/desktop`) — H5 fix, POST-body-to-loopback, bearer stays in main

**Correct v1's file ownership (review [info]#4):** the sign **page is a frontend-built artifact served by a facade route**, exactly like `wallet.html` — `register_wallet_page_routes` (wallet_page_routes.py:36–70) serves the built-frontend `wallet.html` + `/assets` from `settings.web_dist_dir`. `safe-sign.html` is therefore **built under `apps/frontend`** and served by an added facade route in `wallet_page_routes.py` (a `/safe-sign.html` `FileResponse` alongside `/wallet.html`). It is **not** an `apps/desktop` file. (v1 row 20's "`apps/desktop/main/wallet/safe-sign.html`" is wrong on ownership; the `.ts` orchestrator is desktop, the `.html` page is frontend-built + facade-served.)

Handoff shape (H5/M5/deeplink):

- `apps/desktop/main/wallet/safe-sign.ts` **NEW** arms an ephemeral loopback that reads the signature from the **HTTP request body**, not `url.searchParams`. Extend `loopback-server.ts` with a body-reading variant `awaitLoopbackPostBody` (a sibling of `awaitLoopbackCode` **L390** / `awaitLoopbackHandoff` **L409** / `awaitLoopbackWalletProof` **L430**, all of which read the query — the leak sites are `parseHandoffRedirect` reading `bearer_token` at **L319** and `parseWalletProofRedirect` reading `signature` at **L375**; that template must **not** be copied for value signing).
- `safe-sign.html` accepts **only** an opaque `proposal_ref` + loopback `state` in the URL. It FETCHes the canonical EIP-712 doc from the facade (`GET /v1/wallet/safe/proposal/{ref}`, where `proposal_ref` is itself the read-only single-use capability, §5.4), runs `eth_signTypedData_v4` in the system-browser wallet, and **POSTs `{state, signature}` to the loopback body**. It refuses non-loopback targets and never accepts caller-supplied typed-data / `safe_tx_hash`.
- The **session bearer never leaves main.** Main holds it, opens the page with only `proposal_ref`+`state`, receives the signature via loopback body, and is the **sole** caller of facade `/transactions`. Register `safe:sign` IPC in `main/index.ts`; bind the port in `renderer/destinationBinders.tsx`.

**Test (desktop):** no signature or bearer ever appears in any redirect URL, navigation history, or loopback request line; the page ignores URL-injected typed-data; main is the sole holder of the bearer for `/transactions`.

---

## 7. Security invariants — as CI-testable assertions

Every invariant below is a test in **PR-W8** (the security regression suite), run in CI on a hermetic real-graph run (deterministic fake model + fake EIP-1193 + fake tx-service, per the verification keystone).

- **INV-1 — no server-side wallet key EVER.** Grep-gate: no `eth_sign*` / private-key / mnemonic symbol anywhere in `services/ai-backend`, `services/backend-facade`, `services/backend`, or `services/safe-mcp`. The signature is produced only in the user's wallet (EIP-1193).
- **INV-2 — sweeper stays rejection-only.** `jobs/approval_expiry_sweeper.py` enqueues only `ApprovalDecision.REJECTED` (**L220**); a test asserts no expiry path can emit `APPROVED` for a wallet_signature.
- **INV-3 — DELEGATECALL blocked.** `operation==1` ⇒ `signable=false` at the builder **and** `delegatecall_blocked` at facade `/transactions` (§2.6 step 3); no allowlist in v1 (GD-2).
- **INV-4 — wallet_signature is non-forwardable + reversible=no.** `wallet_signature ∉ APPROVAL_FORWARDABLE_KINDS` (forward → 422); persisted `approval.metadata.reversible == "no"` regardless of agent input; `request_approval_undo` → 422.
- **INV-5 — agent-supplied `safe_address` can never seed/mutate `safe_bindings`.** T-BIND-1/2 (§2.8): the only writer is the owner-proof-gated Settings route; empty store ⇒ 403; store row-count unchanged after an agent-driven unbound sign attempt.
- **INV-6 — owner proof mandatory + tenant-isolated.** T-BIND-3/4/5/6 (§2.8).
- **INV-7 — kind integrity (M9).** A `wallet_signature` interrupt never projects `approval_kind=mcp_tool` (dispatch inserted before the L656 fallthrough); replay rebuilds the authoritative `safe` block.
- **INV-8 — metadata closure (M2).** Agent `reversible/category/vendor/params` overrides are stripped/ignored; the persisted metadata is the `extra="forbid"`-validated closed shape.
- **INV-9 — no-leak decision surface (M4).** suggest_edit/forward/approve_with_edits on a wallet_signature all 422 at the pre-dispatch guard; nested hex blobs in `edited_payload`/`edits` rejected; no signature-shaped `answer` enters graph/checkpoint state.
- **INV-10 — no signature/bearer in any URL/log.** Desktop POST-body handoff; bearer stays in main; the facade `/transactions` never logs the signature body; `proposal_ref` confers read-only canonical-doc access, not submit.
- **INV-11 — injection kill-switch asserts decoded==simulated, not hash equality (H3).** A malicious tool result that auto-proposes+auto-signs stalls at the interrupt; with no human approve, no signature; the test asserts the decoded effect matches the simulated asset-diff (not `safeTxHash` re-derivation, which is tautological).
- **INV-12 — read/submit hard split (M7).** No submit symbol importable from `services/safe-mcp` read/propose; submit reachable only from facade `/transactions` with a separate service token.

---

## 8. Safe-track PR breakdown + merge order

Sheets (Track S) ships first and independently (`P6-BUILD-Sheets.md`). The Safe track is gated on security-review-clean.

- **PR-W0 — `services/safe-mcp` scaffold + read/submit hard split + no-key CI gate.** New deployable (own venv/requirements/Dockerfile/deploy). Empty read/propose + submit modules with the import boundary; boundary doc; backend CONNECT seed (`mcp_catalog.py` `safe` entry `auth_mode=NONE`, `catalog.yaml` marketing row, `desktop_profiles.yaml` `safe-global` profile all-`read`/`session`). **CI: INV-1, INV-12.**
- **PR-W1 — decode + simulation engine (Controls A+B core) in safe-mcp.** `decode.py`, `simulation.py` (`SelfHostedEthCallProvider` default + `SimulationProvider` seam + Tenderly upgrade), `safe_tx.py` (canonical SafeTx + EIP-712 + `safeTxHash`), `chain_allowlist.py` (`SAFE_SIGNING_ALLOWED_CHAIN_IDS`), proposal store (short-TTL single-use `proposal_ref`). **CI: decode vectors, fail-closed sim matrix, M6 key-absence, INV-11 seed.**
- **PR-W2 — `safe_bindings` store + owner-proof capture + facade enforcement (Control C).** Migration `0045_safe_bindings.sql` + module schema; `SafeBindingService` (owner-proof-gated `add_binding`, `is_bound`, list/remove); `SafeTxServiceOwnersClient`; `SAFE_BINDING_STATEMENT` + binding nonce (reuse SIWE recover/parse/nonce); backend routes; facade `wallet_safe_routes.py` (all binding + `/simulate` + `/transactions` + `/proposal/{ref}` endpoints) with the full Control-C stack (§2.6) + M8/M11. **CI: T-BIND-1..6 (INV-5/6), INV-3, INV-10.** _(May split W2a store+capture / W2b facade endpoints; keep enforcement tests with the endpoints.)_
- **PR-W3 — ai-backend SIGN-gate builtin + constants + factory + closed metadata + pre-dispatch guard.** §5.1, §5.2, §5.3 (rows 1–3 + coordinator guard + `WalletSignatureApprovalMetadata` `extra="forbid"`). **CI: INV-4, INV-8, INV-9.**
- **PR-W4 — worker dispatch + replay + non-forwardable + tile suppression.** §5.2 (`_native_wallet_signature_payload` before L656), §5.5 (replay projector nested blocks), §5.6 (resume + `internal_tool_names`). **CI: INV-7, subagent-pause reason, replay preserves `safe`+`signable`.**
- **PR-W5 — chat-surface port/provider/action/projection/RunDestination.** §5.7. **CI (vitest): projection title from decoded fields; `SafeSignAction` disables on `signable==false`; `resolveApproval` posts no signature.**
- **PR-W6 — web host signing.** §6.1 (`webSafeSigning.ts` new `eth_signTypedData_v4` path, `safeApi.ts`, provider mount). **CI: hash-mismatch pre-sign, chain cross-check abort, not-owner short-circuit, 4001 quiet reject.**
- **PR-W7 — desktop host signing (H5).** §6.2 (`safe-sign.ts` loopback POST-body, frontend-built + facade-served `safe-sign.html`, `awaitLoopbackPostBody`, IPC, binder; bearer stays in main). **CI: INV-10 desktop.**
- **PR-W8 — security regression suite + CI invariants.** All INV-1..12 consolidated on the hermetic real-graph run.

**Merge order:** (Sheets S1,S2 →) W0 → W1 → W2 → W3 → W4 → W5 → W6 → W7 → W8. W1/W2 (safe-mcp + backend) parallelize with W3/W4 (ai-backend) once W0 freezes the contracts (`SafeDecodedEffect`, endpoint shapes, `proposal_ref`).

---

## 9. Anti-bandaid ledger (what this spec deliberately does NOT do)

- Does **not** keep `safeTxHash` re-derivation in the safety invariant — it re-hashes the agent's own fields (H3). The injection test asserts **decoded==simulated** (INV-11).
- Does **not** describe binding capture as a byproduct of the NONE-auth CONNECT — the store is written **only** by owner-proof-gated `add_binding` (§2), closing the review [high].
- Does **not** route the binding owner read through the third-party MCP — it is a direct, sanitized backend call (§2.4); the MCP is agent-facing read/decode/simulate-context only.
- Does **not** enforce bound-Safe/chain in the renderer or the MCP layer — the native builtin bypasses both (call_tool.py:83). Enforcement is server-side at the facade on server-derived identity (§2.6).
- Does **not** mirror the ask `{**payload}` spread — the wallet payload is a fixed server-side allowlist validated through `extra="forbid"` (§5.2), so `metadata=dict(payload)` (stream_events.py:394) persists a closed shape.
- Does **not** rely on the per-branch guards for the decision surface — a single pre-dispatch 422 (before L317) closes suggest_edit/forward/approve_with_edits together, and the hex-blob scan recurses into `edited_payload`/`edits` (§5.3).
- Does **not** put a main-minted token in the sign-page URL — `proposal_ref` itself is the read-only single-use capability (§5.4).
- Does **not** treat `safe-sign.html` as an `apps/desktop` file — it is a frontend-built, facade-served artifact (§6.2), correcting v1.
- Does **not** ship a fail-open simulation — the facade re-simulates and rejects on the fail-closed matrix (§3.3).
- Does **not** reuse the SIWE login statement/allowlist for value signing — distinct `SAFE_BINDING_STATEMENT` + distinct `SAFE_SIGNING_ALLOWED_CHAIN_IDS` (§2.3, §3.2).

---

## 10. Gating items carried forward (need product/risk sign-off; each has a shippable default)

- **GD-1 — `SAFE_SIGNING_ALLOWED_CHAIN_IDS` default membership.** Default `1,8453,42161` (Ethereum + Base + Arbitrum) per decision B; a **new** env, never the SIWE login list. Robinhood Chain (`4663`) stays out unless Safe + tx-service support is confirmed there.
- **GD-2 — DELEGATECALL policy.** Default: hard-block `operation==1` entirely (INV-3). A curated per-chain audited-library allowlist (`MultiSendCallOnly`) with a distinct high-friction confirm is a scoped follow-up PR, not v1.
- **Execution ceiling (resolved inline):** the agent stops at "signature added to the multisig queue"; on-chain execution is a separate, later, explicitly-gated action, **out of P6**.
- **Simulation fidelity:** `eth_call` balance-read diffs are the v1 default; `SAFE_SIM_MODE=anvil` (full state-override) and `SAFE_SIM_PROVIDER=tenderly` are operator upgrades behind the same `SimulationProvider` seam.

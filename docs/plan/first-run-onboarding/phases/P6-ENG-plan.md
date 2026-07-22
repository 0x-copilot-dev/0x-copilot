# P6 — Safe{Wallet} (propose-only) + Google Sheets MCP — Engineering Build Plan

**Status:** build plan (consolidates + supersedes into implementation). **Owner posture:** principal engineer. **No bandaids** — every control below lands as a real component with a real enforcement point in code, not a comment or a client-side check.

This plan is the single build reference for P6. It absorbs:

- `phases/PRD-P6a-hardened.md` — the authoritative security design (Controls A/B/C, the `wallet_signature` wiring, the 13-row finding→fix table). **This is the binding spec where P6a.md and this plan disagree.**
- `phases/PRD-P6a.md` — the original design PRD (kept for the numbered flow ①–⑫ and file inventory; every claim the three reviews refuted is overridden by the hardened doc).
- `phases/PRD-P6b.md` — the Google Sheets connector PRD.
- `phases/security-review-safe-{1,2,3}.md` — 1 critical + 8 high + 12 med/low, all `needs-changes`.

Every anchor below was re-verified against HEAD of `claude/0xcopilot-first-run-onboarding-d7eb30`. Paths are relative to ROOT.

---

## 0. Anchor drift — read this before touching code

The PRD line numbers have **drifted** from HEAD. Do not trust the PRD's line citations; use the verified table below. The most consequential drifts:

| PRD claim | PRD line | **Verified at HEAD** | File |
| --- | --- | --- | --- |
| `request_wallet_signature` is a native builtin | `factory.py:487` | **Does not exist yet** — L487 is `names.add(Values.Tool.ASK_A_QUESTION)`. `AskAQuestionTool` is appended at **L408–413**; imports at **L47–48**. The new builtin is appended in `_model_visible_tools`, the tool-visibility set in `_local_tool_names` (L465–490). | `services/ai-backend/src/agent_runtime/execution/factory.py` |
| `ApprovalKind` class | `constants.py:246,250` | `class ApprovalKind` at **L246**; members `ACTION/ASK_A_QUESTION/MCP_AUTH/MCP_TOOL` at **L248–252**. `class Tool` at **L229**, `ASK_A_QUESTION="ask_a_question"` at **L232**. | `services/ai-backend/src/agent_runtime/api/constants.py` |
| dispatch before fallthrough | `stream_events.py:604-607,650,730` | `native_interrupt_payloads` at **L632–662**; auth→ask dispatch at L646–655; **fallthrough to `native_tool_approval_payloads` at L656–660**. Insert `_native_wallet_signature_payload` between L655 and L656. | `services/ai-backend/src/runtime_worker/stream_events.py` |
| mcp_tool stamp | — | `Keys.Field.APPROVAL_KIND: "mcp_tool"` hard-coded at **L832**. | same |
| read/write classifier lacks `append` | — | `_connector_action_is_read_only` at **L910–918**; write-terms `create/post/send/update/delete/write` — **no `append`/`clear`/`set`/`insert`**. | same |
| coordinator threads `answer` | `approval_coordinator.py:388` | `answer=request.answer` written into the **record at L348** (before kind is known) AND into `RuntimeApprovalResolvedCommand` at **L388**. Scope check L309–316. `APPROVAL_FORWARDABLE_KINDS = {ACTION, MCP_TOOL}` at **L70–75**. `_wire_status_for` at **L1202**. | `services/ai-backend/src/agent_runtime/api/approval_coordinator.py` |
| worker resume threads answer | `handlers/approval.py:670-676` | ask_a_question resume branch threads `command.answer` at **L675–680**; mcp_auth at L671–674. Add `wallet_signature` branch here. | `services/ai-backend/src/runtime_worker/handlers/approval.py` |
| decision `answer` unbounded | `approvals.py:65` | `ApprovalDecisionRequest.answer: str \| None` at **L65**; normalizer only strips whitespace L92–95. `edited_payload` L74, `edits` L80. | `services/ai-backend/src/runtime_api/schemas/approvals.py` |
| metadata `extra="allow"` | `approvals.py:294` | `McpApprovalMetadata` `model_config = ConfigDict(extra="allow")` at **L294**; `APPROVAL_MAX_PARAMS = 6` at **L280**. | same |
| undo gate reads reversible | `approval_coordinator.py:478,520` | `approval.metadata.get("reversible") != "yes"` at **L478** and **L520**. `ApprovalReversible.{YES="yes",NO="no",NOT_APPLICABLE="n/a"}` at `common.py:304–309`; `ApprovalCategory.{READ,WRITE,ACTION}` at `common.py:281–286`. | `services/ai-backend/src/runtime_api/schemas/{approval_coordinator,common}.py` |
| MCP re-check bypassed by native builtins | `call_tool.py:83-92` | `is_server_card_authorized` re-check at **L83–92** (call_tool.py); `paused_connectors` gate at `permissions.py:44`. Confirmed: a native builtin never reaches this path. | `services/ai-backend/src/agent_runtime/capabilities/mcp/{middleware/call_tool,permissions}.py` |
| desktop leaks signature/bearer in URL | `loopback-server.ts:302-388` | `parseHandoffRedirect` reads `bearer_token` from `url.searchParams` at **L319**; `parseWalletProofRedirect` reads `signature` at **L375**. `wallet-login.ts` opens `.../wallet.html?handoff=http://127.0.0.1:<port>/...` and relays the bearer in the redirect query (comments L21,26–28). | `apps/desktop/main/auth/{loopback-server,wallet-login}.ts` |
| no typed-data signing today | — | `walletProof.ts` only does `personal_sign` (L49) + `eth_chainId` (L33). **No `eth_signTypedData_v4`, no EIP-712 domain check anywhere.** | `apps/frontend/src/features/auth/walletProof.ts` |
| write scope unreachable | `oauth_coordinator.py:303-305` | `_requested_permissions` (**L298–309**) maps only `read`/`draft`; `wanted={"read"}`, `+draft`. **`write` never expands.** Route `Literal["read","draft"]` at `desktop_routes.py:91`. | `services/backend/src/backend_app/connectors/{oauth_coordinator,desktop_routes}.py` |
| google-drive already declares write | — | `desktop_profiles.yaml` google-drive profile (**L71–119**) already has `required_for: write` (L91) + `product_scope: write` tools `approval: per_call` (L111–117). The profile side is ready; only the coordinator/route are the gap. | `services/backend/src/backend_app/connectors/desktop_profiles.yaml` |

**Confirmed-holding invariants (do not regress — put under CI test):** no server-held wallet key exists; `approval_expiry_sweeper.py` enqueues **only** `ApprovalDecision.REJECTED` (L220); the two approving callers are identity-scoped (coordinator L309–316); `wallet_signature` will be outside `APPROVAL_FORWARDABLE_KINDS`.

---

## 1. Architecture — the three controls as real components

The literal keyless-signing invariant already holds (review-3 `[info]`). Every finding is a **deceptive-consent** or **secret-leak** gap. The safety story moves **off** `safeTxHash` re-derivation (tautological — it re-hashes the agent's own fields) and **onto** three server-enforced controls. Below each is a concrete component with a concrete enforcement point.

### 1.1 Component map

```
                          ai-backend (orchestration only, no keys, no RPC)
 ┌────────────────────────────────────────────────────────────────────────┐
 │ request_wallet_signature builtin (NEW)                                   │
 │   ├─ resolves proposal_ref → authoritative decoded+simulated block       │
 │   │     via safe-mcp READ module (server-to-server)                      │
 │   ├─ raises langgraph_interrupt(approval_kind="wallet_signature",         │
 │   │     safe:{server-decoded effect + asset-diff + signable flag})       │
 │   └─ _resume_result → status-enum ONLY (never a signature)               │
 │ stream_events._native_wallet_signature_payload (NEW, before fallthrough) │
 │ approval_coordinator answer-guard (NEW) + non-forwardable (existing set) │
 └────────────────────────────────────────────────────────────────────────┘
        │ read/propose (server-to-server)         │ resume w/ status enum
        ▼                                          ▼
 ┌───────────────────────┐            renderer (web + desktop)
 │ services/safe-mcp (NEW)│            ┌──────────────────────────────────────┐
 │  READ/PROPOSE module   │◄───────────│ SafeSigningPort (chat-surface, ports) │
 │   - decode_calldata    │            │  web: eth_signTypedData_v4 in-page    │
 │   - simulate (eth_call)│            │  desktop: loopback POST-body (no URL) │
 │   - build SafeTx+EIP712│            └──────────────────────────────────────┘
 │   - proposal store(TTL)│                        │ /simulate  │ /transactions
 │  ── HARD SPLIT ──      │                        ▼            ▼
 │  SUBMIT module (sep.   │            ┌──────────────────────────────────────┐
 │   creds; reachable     │◄───────────│ backend-facade  /v1/wallet/safe/*     │
 │   ONLY from facade     │            │  ENFORCEMENT POINT (Control C):       │
 │   /transactions)       │            │   - safe ∈ bound_safes(user) [store]  │
 └───────────────────────┘            │   - chain ∈ SAFE_SIGNING_ALLOWED_...  │
        ▲                             │   - operation==1 → hard block/allowlist│
        │ bound-Safe reads            │   - re-simulate → fail-closed matrix   │
 ┌───────────────────────┐            │   - single-use challenge (M11)         │
 │ safe_bindings store    │────────────│   - nonce re-read + idempotency (M8)  │
 │ (backend, per-user)    │            │   - NEVER logs signature body         │
 └───────────────────────┘            └──────────────────────────────────────┘
```

### 1.2 Control A — Decoded-calldata authority

**Problem (C1/H6/AC-4):** the card and wallet popup show agent-authored `to/value/token/summary`; the real recipient/amount live in `data`. `APPROVAL_MAX_PARAMS=6` (`approvals.py:280`) is fully consumed by To/Amount/Token/Chain/Safe/Nonce — no row for a decoded effect.

**Component:** a server-side `DecodedEffect` produced in **safe-mcp** and carried through as the *only* source of the card's review rows.

- **Where the decode happens:** `services/safe-mcp/.../decode.py` — ABI-decodes `transfer`/`transferFrom`/`approve`/`increaseAllowance` + known Safe selectors from `data`, cross-references the simulation asset-diff, returns a typed `SafeDecodedEffect { method, target_contract, recipient, amount_raw, amount_display, token_symbol, token_address, direction, operation, delegatecall_target, asset_diffs[], decoded_ok, simulated_ok }`.
- **Authoritative handle = `proposal_ref`.** The `propose` tool (step ②) stores the decoded+simulated proposal in safe-mcp keyed by a short-TTL `proposal_ref`. `request_wallet_signature(proposal_ref, …)` and both facade endpoints resolve the **stored, server-computed** block by ref. The agent's raw `to/value/data/summary` are carried **only** in a labeled `agent_claims` zone for display comparison — never load-bearing. (Fallback if we reject proposal persistence: stateless re-decode from raw fields at every gate — same code, more repeated work; see GD notes.)
- **Schema:** replace the flat 6-param model for this kind. Add `SafeDecodedEffect` + `SafeProposalSummary` to `runtime_api/schemas/approvals.py`; the wallet_signature approval carries the typed `safe` block, not the flat `ApprovalParam[]`. Generic connectors keep `ApprovalParam`/`APPROVAL_MAX_PARAMS`.
- **Enforcement:** if `data` is non-empty and `decoded_ok==false` **and** `simulated_ok==false` → `signable=false` (refuse to sign). Agent-claimed vs decoded mismatch → `signable=false`. The FE renders To/Amount/Token/Method/Operation/Chain/AssetDiff from `safe.*` and shows `agent_claims.summary` de-emphasized under an "assistant note" label.

### 1.3 Control B — Fail-closed mandatory simulation

**Problem (H1/H7):** simulation was advisory (fail-open); suppress the diff → human signs blind.

**Component:** `services/safe-mcp/.../simulation.py` — read-only `eth_call` against a pinned RPC (see §3), returns `{ status: ok|reverted|unsupported|timeout|error, asset_diffs[], revert_reason }`. Enforced by a **fail-closed matrix** at *both* the renderer (disable button) and the facade `/transactions` submit (reject) — the renderer disable is cosmetic; the facade re-simulation is the real gate.

**Fail-closed matrix (§3.3 is the canonical copy):**

| tx shape | simulation | `signable` | facade submit |
| --- | --- | --- | --- |
| native transfer, `data==""`, `operation==0`, value>0 | not required | **true**, labeled "unverified — no contract call" | allow |
| non-empty `data`, `operation==0` | positive asset-diff | **true** | allow |
| non-empty `data` | revert / unsupported / timeout / error | **false** | **reject** (`simulation_failed`) |
| decoded ERC-20 approve/transfer | positive asset-diff | **true** | allow |
| decoded ERC-20 approve/transfer | sim unavailable | **false** | **reject** |
| `operation==1` (delegatecall), target ∉ allowlist | — | **false** (hard block) | **reject** (`delegatecall_blocked`) |
| `operation==1`, target ∈ allowlist (GD-2) | positive asset-diff | **true** + distinct high-friction confirm | allow w/ confirm |
| undecodable `data` AND no positive sim | — | **false** | **reject** |

### 1.4 Control C — Server-side bound-Safe + chain enforcement at the facade

**Problem (H4/review-3-high):** `request_wallet_signature` is a **native builtin** — it bypasses the MCP auth re-check (`call_tool.py:83–92`) and `paused_connectors` entirely. `safe_address` is agent-supplied. No binding store exists.

**Components:**

1. **Per-user bound-Safe store.** **Recommended location: a new first-class `safe_bindings` store in `services/backend`** (`backend_app/wallet/safe_bindings/`), captured at CONNECT, keyed `(org_id, user_id) → {safe_address, chain_id, added_at}[]`.
   - *Why not MCP-record metadata:* the sign gate must enforce binding *without* touching the MCP layer the builtin bypasses; a dedicated store is the honest boundary and gives a clean tenant-isolation test surface. MCP metadata would couple binding to the connector-scope path that the native builtin never traverses.
   - *Why not per-conversation connector scope:* bindings are a durable per-user trust decision (which treasury the agent may operate), not a per-chat toggle.
2. **Facade enforcement point** — `services/backend-facade/.../wallet_safe_routes.py` (new registrar, mounted next to `register_wallet_page_routes`, `app.py:145`). On **server-derived identity** (never the agent's claimed `safe_address`), every `/simulate` and `/transactions` call asserts: `safe ∈ bound_safes(user)`, `chain ∈ SAFE_SIGNING_ALLOWED_CHAIN_IDS`, `operation` policy (GD-2), fail-closed simulation (§1.3), single-use challenge validity (M11), nonce re-read + idempotency on `(safe, safe_tx_hash, signature)` (M8). Reject → typed domain error; the signature body is **never** logged (M2/M5).
3. **Defense-in-depth pre-interrupt check** in the builtin: `request_wallet_signature` also verifies `safe ∈ bound_safes` before raising the interrupt, so a malicious agent stalls early. This is belt-and-suspenders; the facade is the authority.

### 1.5 Safe MCP host — **recommend a new `services/safe-mcp` deployable**, read/propose physically split from submit

Per the service-boundary rules (own venv / requirements.txt / Dockerfile / deploy path) and finding **M7** (propose is `session`-approved and must not share a module with submit):

- **`services/safe-mcp`** (new). Two **physically separate modules with separate credentials**:
  - **READ/PROPOSE module** (`read_propose/`) — reachable as an MCP-over-HTTP server (registered like any connector) and as a server-to-server client for the ai-backend builtin. Read-only RPC + Safe tx-service *reads* + calldata decode + simulation + SafeTx/EIP-712 build + proposal store. **No submit code importable here.**
  - **SUBMIT module** (`submit/`) — `create-multisig-tx-with-signature`. Reachable **only** from the facade `/transactions` endpoint (separate service token). Not importable from `read_propose/`, not an MCP tool. **CI grep gate** asserts no submit symbol is importable from the read/propose module and no `eth_sign*`/private-key path exists anywhere in the service.
- *Why new service over `backend_app/wallet/safe/`:* keeps chain-RPC + tx-service + simulation concerns off `backend` (which owns tenant/auth/product state), gives the read/submit split a hard process/credential boundary rather than "code discipline in one module," and isolates the RPC dependency's blast radius. Backend-hosted is the lighter alternative but collapses the M7 boundary into intra-process discipline — rejected.
- The **CONNECT** seed stays in `backend`: `mcp_catalog.py` gets a `CatalogEntry(slug="safe", auth_mode=McpAuthMode.NONE)` (dataclass `mcp_catalog.py:32`, `DEFAULT_CATALOG:83`); `catalog.yaml` gets the marketing `safe` entry; `desktop_profiles.yaml` gets a `safe-global` profile (read+propose tools all `product_scope: read`, `approval: session` so `ConnectorToolPolicy._mutating_tools_require_per_call_approval` passes). The **only** `per_call` gate is the native `wallet_signature`.

### 1.6 Desktop signature-return-without-URL path (H5/M5/deeplink)

Today `wallet-login.ts` + `loopback-server.ts` relay the **bearer** and **wallet signature** in the loopback **redirect URL query** (`parseHandoffRedirect` L319, `parseWalletProofRedirect` L375) — persisted to OS browser history and any URL access log. That template must **not** be copied for value signing.

**Recommended handoff shape (GD-adjacent, decided here):** *POST-body-to-loopback, with the bearer never leaving main.*

- New `main/wallet/safe-sign.ts` arms an ephemeral loopback that reads the signature from the **HTTP request body**, not `url.searchParams` (extend `loopback-server.ts` with a body-reading variant `awaitLoopbackPostBody`).
- `safe-sign.html` (served same-origin by the facade, like `wallet.html` via `register_wallet_page_routes`) accepts **only** an opaque `proposal_ref` + loopback `state` in the URL. It **FETCHes the canonical EIP-712 doc from the facade** (`GET /v1/wallet/safe/proposal/{ref}` authenticated by a one-time scoped token minted by main), runs `eth_signTypedData_v4` in the system-browser wallet, and **POSTs `{state, signature}` to the loopback body**. It refuses non-loopback targets and never accepts caller-supplied typed-data/`safe_tx_hash`.
- The **session bearer never leaves main.** Main holds it, mints a one-time short-TTL scoped token for the page's canonical-doc fetch, receives the signature via loopback body, and is the sole caller of facade `/transactions`.
- **Test:** no signature or bearer ever appears in any redirect URL, navigation history, or loopback request line; assert the page ignores URL-injected typed-data.

---

## 2. The `wallet_signature` approval_kind wiring — every file/site to change

This is **not** purely additive — `wallet_signature` must be special-cased at every site `ask_a_question` is, and inserted into the dispatch **before** the `mcp_tool` fallthrough (M9). Enumerated with verified anchors:

### 2.1 ai-backend — the SIGN gate + constants

| # | File | Site | Change |
| --- | --- | --- | --- |
| 1 | `agent_runtime/api/constants.py` | `class Tool` L229–232; `class ApprovalKind` L246–252 | Add `Tool.REQUEST_WALLET_SIGNATURE = "request_wallet_signature"` and `ApprovalKind.WALLET_SIGNATURE = "wallet_signature"`. |
| 2 | `agent_runtime/capabilities/tools/builtin/request_wallet_signature.py` | **NEW** | `RequestWalletSignatureInput(RuntimeContract)` + `RequestWalletSignatureTool` modeled on `ask_a_question.py` (`AskAQuestionTool` L102, `ainvoke` L110, `_approval_id` L145, `_resume_result` L154). `ainvoke`: resolve `proposal_ref` → authoritative decoded block via safe-mcp read client; verify `safe ∈ bound_safes` (defense-in-depth); raise `langgraph_interrupt({api_event_type:"approval_requested", approval_kind:"wallet_signature", approval_id, status:"pending", safe:<server block>, agent_claims:{summary,token}, signable})`. `_resume_result` returns `{ok, decision, status}` — status is a **non-secret enum**; never reads a signature. |
| 3 | `agent_runtime/execution/factory.py` | `_model_visible_tools` append block near L408–413; `_local_tool_names` L465–490 (add `Values.Tool.REQUEST_WALLET_SIGNATURE` to the trusted-name set for collision checks) | Append `RequestWalletSignatureTool` via `_structured_tool`. **No `interrupt_on` change** — the tool raises its own interrupt (contrast `enforced_tools.interrupt_on` at L297). Gate behind the P6 feature flag + desktop/BYOK gate like the Wave-1 tools (L424–427). |
| 4 | `agent_runtime/api/approval_coordinator.py` | `APPROVAL_FORWARDABLE_KINDS` L70–75 (leave as `{ACTION, MCP_TOOL}` — assert `wallet_signature ∉`); `record_approval_decision` L286, **insert guard after scope check L316, before record write L338**; `_wire_status_for` L1202 | **M4 answer-guard:** after L316, if `approval.metadata.get(APPROVAL_KIND) == "wallet_signature"`, constrain `request.answer` to the status enum `{signed,rejected,chain_not_allowed,not_owner,hash_mismatch,nonce_changed}`, cap length, and **422** on a hex-blob shape (`^0x[0-9a-fA-F]{100,}$`) in `answer`/`reason`/`edited_payload`/`edits`. Wire `wallet_signature` status into `_wire_status_for`. |
| 5 | `runtime_worker/stream_events.py` | `native_interrupt_payloads` L632–662 — insert dispatch **between L655 and L656**; new `_native_wallet_signature_payload` mirroring `_native_ask_a_question_payload` L730–764 | Match `approval_kind == "wallet_signature"` **before** the `native_tool_approval_payloads` fallthrough (which stamps `"mcp_tool"` at L832). Normalize `approval_id`/`action_id`/`batch_id`/`batch_index`; preserve the server `safe` block + `signable`; build the **closed** metadata server-side (never spread agent fields). |
| 6 | `runtime_worker/stream_tools.py` | `internal_tool_names` L47–57 | Add `Values.Tool.REQUEST_WALLET_SIGNATURE` so the running-tile `tool_call_started/result` events are suppressed (the approval card is the surface), exactly as `ask_a_question` is. |
| 7 | `runtime_worker/handlers/approval.py` | resume shaping L665–680 (ask branch L675–680) | Add a `wallet_signature` branch that threads **only** `command.answer` (already status-enum-guarded upstream) into the resume dict — same shape as ask_a_question, never a batch/outcome shape. |
| 8 | `runtime_api/schemas/approvals.py` | after `McpApprovalMetadata` L291–307; `ApprovalDecisionRequest` L59–95 | Add `SafeDecodedEffect`, `SafeProposalSummary`, and `WalletSignatureApprovalMetadata` as a **standalone `RuntimeContract` with `model_config = ConfigDict(extra="forbid")`** (do **not** subclass `McpApprovalMetadata` — that inherits `extra="allow"`, the M2 hole). Pin `vendor="SAFE"`, `category=ApprovalCategory.ACTION`, `reason_code=ApprovalReasonCode.IRREVERSIBLE`, `reversible=ApprovalReversible.NO`. Add a **schema-level, kind-agnostic** hex-blob rejection on `answer` in `ApprovalDecisionRequest` (belt-and-suspenders to the coordinator guard). |
| 9 | `runtime_api/schemas/events.py` | approval-request projector (approx L700–780 — anchor drifted; the `_project_*` / `safe_payload` helpers near L520–561) | Ensure the replay/`GET .../events` projector **passes through the server `safe` decoded block + `signable` + `agent_claims`** so a reconnect rebuilds the authoritative card, not an agent-field card. |
| 10 | `runtime_api/schemas/common.py` | `RuntimeApiEventType` block near L113–130; enums `ApprovalCategory` L281, `ApprovalReversible` L304 | No new event type needed (`APPROVAL_REQUESTED`/`APPROVAL_RESOLVED` reused). Confirm the `wallet_signature` kind flows through `_SUBAGENT_INTERRUPT_REASONS` (stream_events L582–607) so a signature raised inside a subagent projects a `subagent_paused` reason, not the generic one. |

### 2.2 Contracts + chat-surface + hosts

| # | File | Site | Change |
| --- | --- | --- | --- |
| 11 | `packages/api-types/src/index.ts` | approval-kind unions | Add `"wallet_signature"` to the approval-kind union(s); add `SafeDecodedEffect`, `SafeProposalSummary`, `SafeSimulationResult`, `SafeSignRequest`, `SafeSignResult`, `WalletSignatureApprovalMetadata`. |
| 12 | `packages/chat-surface/src/workspace/types.ts` | `ApprovalsQueueItem.approvalKind` | Add `"wallet_signature"` (this widens `RunApprovalKind`, which is `ApprovalsQueueItem["approvalKind"]`, `approvalProjection.ts:37`). |
| 13 | `packages/chat-surface/src/destinations/run/approvalProjection.ts` | `mapApprovalKind` L317–…; projector | Add `case "wallet_signature"`; project the typed `safe` block (To/Amount/Token/Method/Operation/Chain/AssetDiff), `category={vendor:"SAFE",access:"ACTION"}`, `reversible:no`, `signable`. Title composed from **decoded** fields (M3), not `agent_claims.summary`. |
| 14 | `packages/chat-surface/src/ports/safeSigning.ts` | **NEW** | `SafeSigningPort { signSafeTransaction(req: SafeSignRequest): Promise<SafeSignResult> }` — host-injected; re-derive+re-simulate via facade, sign EIP-1193, submit via facade, return **non-secret** status. Never returns/threads the raw signature. |
| 15 | `packages/chat-surface/src/providers/SafeSigningProvider.tsx` + `useSafeSigning()` | **NEW** | Substrate-agnostic provider (no bare `window`/`fetch`). |
| 16 | `packages/chat-surface/src/approvals/SafeSignAction.tsx` | **NEW** | Presentational review block + "Review & sign in wallet" / "Reject"; asset-diff `+`=`--color-success`, `-`=`--color-danger`; **disables sign when `signable==false`**; supplied to `ApprovalCard` `actions`/`result` slots. |
| 17 | `packages/chat-surface/src/destinations/run/RunDestination.tsx` | `resolveApproval` L523 (the POST owner, L549) | Add the `wallet_signature` branch: call `safeSigning.signSafeTransaction` **before** POSTing the decision; on `signed` POST `{decision:"approved", answer:<status>}` (**no signature field**); on `rejected`/`hash_mismatch`/`chain_not_allowed`/`not_owner`/`nonce_changed` POST `{decision:"rejected", reason:<code>}` or leave pending. |
| 18 | `packages/chat-surface/src/index.ts` | barrel | Export the new port/provider/component. |
| 19 | `apps/frontend/src/features/wallet/webSafeSigning.ts` + `src/api/safeApi.ts` | **NEW** | Web `SafeSigningPort` impl — in-page `eth_signTypedData_v4` (reuse `eip6963.ts` discovery + `walletProof.ts` reject-detection `isWalletUserRejection` L68; note **new** typed-data path — none exists today). Facade HTTP clients. Mount `SafeSigningProvider` in `src/app/App.tsx`. |
| 20 | `apps/desktop/main/wallet/safe-sign.ts` + `safe-sign.html` (facade-served) + `main/index.ts` IPC + `renderer/destinationBinders.tsx` | **NEW/EDIT** | §1.6 handoff: loopback **POST-body** signature return, canonical-doc fetch, bearer stays in main, no URL-passed tx. Register `safe:sign` IPC; bind the port. |
| 21 | `services/backend-facade/src/backend_facade/wallet_safe_routes.py` + register in `app.py:145` | **NEW/EDIT** | `POST /v1/wallet/safe/{safe}/simulate`, `GET /v1/wallet/safe/{safe}` (info/balances/pending), `GET /v1/wallet/safe/proposal/{ref}` (canonical EIP-712), `POST /v1/wallet/safe/{safe}/transactions` (submit WITH signature). **Control C enforcement lives here.** Model the existing proxy/`_forward_json` helpers (`app.py:1461`); **never log the signature body**. |

---

## 3. Transaction simulation

### 3.1 Recommended default — self-hosted `eth_call` / anvil-fork, **no external key**

Simulation is now **safety-load-bearing** (Control B), so it must not depend on a third-party key that can be griefed, rate-limited, or leak (M6). **Default: a self-hosted read-only simulation backend inside `services/safe-mcp`:**

- Primary: `eth_call` at the pending block against a **self-hosted / operator-pinned read-only RPC** (`SAFE_RPC_URL_<chainId>`), decoding balance/allowance deltas from the call trace where the node supports `debug_traceCall`/state-override; else compute the asset-diff by pre/post `eth_call` balance reads for the decoded token set.
- Higher-fidelity option: a per-request **anvil fork** (`anvil --fork-url $RPC --fork-block-number pending`) for full state-override asset-diffs. Heavier ops; behind `SAFE_SIM_MODE=anvil`.
- **Rejected default: Tenderly** — external key + cost + a network dependency on the sole integrity control. Keep it a pluggable `SimulationProvider` behind the same interface for operators who want it, but never the shipped default.

**Provider-key hygiene (M6):** every upstream RPC/tx-service/Tenderly error is caught in `simulation.py`/`safe_txservice_client.py` and mapped to a fixed typed domain error with a safe public message **before** it can become a tool result. The RPC URL/key lives server-side only; a test asserts a simulated upstream 401 whose URL embeds a fake key yields a tool result with the key absent.

### 3.2 Chain identity cross-check (M1)

Before the wallet popup opens, assert **all four** agree: wallet live `eth_chainId` == EIP-712 `domain.chainId` (facade-built) == validated `chain_id` == displayed chain. Abort otherwise. Use a **separate** `SAFE_SIGNING_ALLOWED_CHAIN_IDS` — **not** the SIWE login allowlist (`siwe.py:85` default `1,8453,42161,4663`); see GD-1.

### 3.3 Fail-closed matrix

The matrix in §1.3 is canonical. Enforced twice: renderer (`SafeSignAction` disables on `signable==false`) and — the authority — facade `/transactions` re-simulates server-side and rejects. A bare native-value transfer with empty `data` + `operation==0` is the **only** sign-on-unsimulated case, shown "unverified."

---

## 4. Google Sheets MCP (P6b)

Grounded, small, and mostly config. The one load-bearing code change is making a `write` product scope requestable through the desktop OAuth start flow (today capped at `read`/`draft`).

### 4.1 Files (all verified)

| Action | Path | Change |
| --- | --- | --- |
| EDIT | `services/backend/.../connectors/catalog.yaml` | Add `gsheets` marketing entry after `gdrive` (L44–47): `slug: gsheets`, `display_name: Google Sheets`, `icon_hint: gsheets`. |
| EDIT | `services/backend/.../connectors/desktop_profiles.yaml` | Add `google-sheets` profile after google-drive (L119): `spreadsheets.readonly` (read) + `spreadsheets` (write) scopes; read tools `session`; write tools **named with write-terms** + `per_call`; `requires_pre_registered_client: true`, `release_stage: preview`, `requires_preview_gate: true`, loopback+deep-link PKCE. |
| EDIT | `services/backend/.../connectors/oauth_coordinator.py` | `_requested_permissions` L298–309: add `elif requested_product_scope == "write": wanted.add("write")`. `start()` signature L146–153: `Literal["read","draft","write"]`. |
| EDIT | `services/backend/.../connectors/desktop_routes.py` | `DesktopStartOAuthRequestModel.requested_product_scope` L91: `Literal["read","draft","write"]`. `_to_capability` L304–318 unchanged (non-read → `scope_required`). |
| EDIT | `packages/api-types/src/connectors-desktop.ts` | `DesktopRequestedProductScope = "read" \| "draft" \| "write"`; update the "write never requested" comment (L60–77). |

### 4.2 The `Literal['read','draft'] → 'write'` widening ORDER (must not break an intermediate)

The order matters because `_requested_permissions` defaults `wanted={"read"}` — an unrecognized scope silently returns **read-only** permissions. If the route boundary accepts `"write"` before the coordinator can expand it, a user "connecting for write" would get a token missing the `spreadsheets` scope while the UI claims write — a silent, security-relevant capability gap. Therefore:

1. **Profile + catalog YAML first** (declares the `spreadsheets` write permission + write tools). Inert until requested.
2. **`oauth_coordinator._requested_permissions` widening** (maps `"write" → [readonly, spreadsheets]`). Still unreachable — the route `Literal` rejects `"write"` (422, safe).
3. **Route `Literal` + `api-types` contract** widening (exposes `"write"` on the wire) — *only now* is `"write"` reachable, and the coordinator already knows how to expand it.
4. **Tests** (below).

Invariant: **coordinator-before-route**. Never expose `"write"` at the route boundary before `_requested_permissions` maps it. The `elif` keeps `read`/`draft` byte-identical (audit that no caller assumed `draft` was max scope).

### 4.3 Per-tool approval modes + the classifier coupling (durable fix)

The runtime interrupts on **every** `call_mcp_tool` and classifies read-vs-write by **tool name** via `_connector_action_is_read_only` (`stream_events.py:910–918`), whose write-terms are `create/post/send/update/delete/write` — **`append`/`clear`/`set`/`insert` are absent.** A bare `append_values` would render a **read** consent for a mutating call — a security misclassification.

- **Immediate mitigation (in the profile):** name write tools with recognized write-terms — `update_values`, `write_append_values`, `clear_values` → but note `clear` is **not** in the set either. So use `update_values`, `write_append_values`, `delete_range_values` (or `update_clear_values`), `batch_update_spreadsheet`, `create_spreadsheet`.
- **Durable fix (recommended, ship in this phase):** extend `_connector_action_is_read_only` to include `append`, `clear`, `insert`, `set`, `remove` — a small, separately-testable ai-backend change that also protects future connectors. This is the no-bandaid fix; the naming mitigation is the belt.

Profile declares `approval: session` (reads) / `per_call` (writes) as the **declared contract + scope gate**; the actual per-call gate is graph-level HITL (`factory.py` `interrupt_on`, `HumanInTheLoopMiddleware`). Reads currently also prompt (as read-category cards) — acceptable for v1; do **not** claim "reads never prompt."

### 4.4 Pre-registered client = operator setup, not code

The `spreadsheets` scope needs a Google OAuth client injected at install into the MCP record's `oauth_client` (`service.py:_oauth_client_config`), encrypted via `TokenVault`, consumed by `RemoteMcpOAuthClient._apply_configured_oauth_client` (`mcp_oauth.py:270`). Absent → `start_auth` raises `McpOAuthError` → `connector_oauth_setup_required` → HTTP 409 (`oauth_coordinator.py:179`). A graceful "needs setup" card, never a crash, never a committed secret.

---

## 5. Phased PR breakdown

Two tracks. **Track S (Sheets)** ships first — small, de-risks the connector path, no security-gate surface area. **Track W (Wallet/Safe)** is the hardened design, sequenced by dependency so each PR is independently reviewable and each security control lands with its regression test.

### Track S — Google Sheets

**PR-S1 — Sheets connector + write-scope OAuth widening.** Files §4.1 in the §4.2 order.
*Acceptance:* `GET /v1/connectors/desktop/catalog` returns `gsheets` (read `supported`, write `scope_required`); `POST …/start-oauth {write}` returns an auth URL whose scope set includes `spreadsheets`; `{read}` returns only `spreadsheets.readonly`; absent operator client → 409 `connector_oauth_setup_required`; unknown scope → 422.
*Tests:* extend `test_desktop_profiles.py` (reconciles, non-orphan, both scopes, every write tool `per_call`, `server_id` non-colliding, negative: a write tool mutated to `session` → `ProfileCatalogError`); `test_desktop_oauth.py` (write→scope present + read present; read omits write; no client → setup-required; preview gate); `test_desktop_routes.py` (200 for write, 422 unknown); `connectors-desktop.test.ts` (`"write"` assignable).

**PR-S2 — Classifier durable fix.** Extend `_connector_action_is_read_only` (`stream_events.py:910–918`) with `append/clear/insert/set/remove`.
*Acceptance:* an `append_*` / `clear_*` tool classifies as **write** (`read_only=false`, `category=write`).
*Tests:* unit table over the new terms; regression that existing read tools stay read-only. Ship with S1 so the Sheets write tools are correctly classified even if a real MCP server names them without the mitigation.

### Track W — Safe{Wallet}

**PR-W0 — `services/safe-mcp` scaffold + read/submit split + no-key CI gate.** New deployable (venv/requirements/Dockerfile/deploy). Empty read/propose + submit modules with the hard import boundary; boundary doc; `backend` CONNECT seed (`mcp_catalog.py` `safe` entry, `catalog.yaml`, `desktop_profiles.yaml` `safe-global` profile all-`read`/`session`).
*Acceptance:* service boots; catalog/profile reconcile at boot; `safe-global` installs through the existing `install_from_catalog` path; FTUE 1-click reaches a CONNECT card.
*Tests:* profile loader reconciles; **CI grep gate**: no `eth_sign*`/private-key symbol anywhere in safe-mcp; no submit symbol importable from `read_propose/`; `_mutating_tools_require_per_call_approval` boot-fails a propose tool mis-marked `write`+`session`.

**PR-W1 — Decode + simulation engine (Controls A + B core) in safe-mcp.** `decode.py` (ERC-20 + known Safe selectors → `SafeDecodedEffect`), `simulation.py` (self-hosted `eth_call`, fail-closed statuses, sanitized errors), `safe_tx.py` (canonical SafeTx + EIP-712 + `safeTxHash`), `chain_allowlist.py` (`SAFE_SIGNING_ALLOWED_CHAIN_IDS`), proposal store (TTL).
*Acceptance:* known-vector `safeTxHash` deterministic; EIP-712 domain matches per chain; decode extracts recipient/amount from `data`; simulation returns asset-diff and fail-closed statuses per §3.3.
*Tests:* decode vectors (transfer/transferFrom/approve/increaseAllowance + undecodable → `decoded_ok=false`); sim revert/timeout/unsupported → non-`ok`; **M6**: upstream 401 with keyed URL → sanitized error, key absent; chain outside allowlist rejected.

**PR-W2 — Bound-Safe store + facade `/simulate`+`/transactions` + Control C enforcement.** `backend` `safe_bindings` store (captured at CONNECT); safe-mcp SUBMIT module; facade `wallet_safe_routes.py` (all four endpoints) with the full enforcement stack: bound-Safe, chain cross-check, `operation` policy (GD-2), fail-closed re-simulation, **single-use challenge minted only while `(approval_id, safe_tx_hash)` is PENDING** (M11), nonce re-read + idempotency on `(safe, safe_tx_hash, signature)` (M8), no-log-signature.
*Acceptance:* submit for an unbound safe → 403; wrong chain → 403; `operation==1` non-allowlisted → reject; no-positive-sim (non-native) → reject; replayed `(safe,hash,sig)` idempotent; nonce drift → abort; signature absent from logs.
*Tests:* tenant-isolation (unbound safe rejected; another user's binding invisible); challenge single-use + PENDING-only; idempotency; nonce-drift abort; **log-redaction** assertion. *(Large PR — may split W2a store + W2b facade endpoints; keep enforcement tests with the endpoints.)*

**PR-W3 — ai-backend SIGN gate builtin + constants + factory + closed metadata + answer-guard.** Wiring rows 1–4, 8, 10 (§2.1). `request_wallet_signature.py`; constants; factory registration behind the P6 gate; `WalletSignatureApprovalMetadata` (`extra="forbid"`, pinned `reversible=NO`); coordinator M4 answer-guard + schema-level hex-blob rejection.
*Acceptance:* valid input raises `approval_kind=wallet_signature` with the server `safe` block; malformed → typed rejection; resume `approved`+status → `{ok:true,status}`; resume `rejected` → `{ok:false}`; an agent-injected `reversible:"yes"` cannot produce an undo window; a signature-shaped `answer` → 422.
*Tests:* tool happy/malformed/resume; **M2** metadata closed-model (agent `reversible/category/vendor` overrides ignored/rejected); **M4** signature-shaped `answer`/`reason`/`edited_payload` → 422, status-enum round-trips; `wallet_signature ∉ APPROVAL_FORWARDABLE_KINDS` (forward → 422).

**PR-W4 — Worker dispatch + replay + non-forwardable + tile suppression.** Wiring rows 5, 6, 7, 9 (§2.1). `_native_wallet_signature_payload` inserted **before** the fallthrough (M9); server-built closed metadata; resume threads status-enum only; `internal_tool_names` suppression; events replay preserves the `safe` block.
*Acceptance:* a `wallet_signature` interrupt **never** yields `approval_kind=mcp_tool`; replay/`GET .../events` rebuilds the authoritative card; no running tile.
*Tests:* **M9** dispatch-order (never `mcp_tool`, forward → 422, reversible=no); subagent-context `subagent_paused` reason; replay projector preserves `safe`+`signable`.

**PR-W5 — chat-surface port/provider/action/projection/RunDestination.** Wiring rows 11–18 (§2.1).
*Acceptance:* `wallet_signature` projects the typed `safe` block; title from **decoded** fields; `SafeSignAction` disables sign on `signable==false`; `resolveApproval` calls the port **before** POST; `signed`→`approved` with `answer` and **no signature field**; `hash_mismatch/chain_not_allowed/not_owner/nonce_changed/rejected` do **not** POST approve.
*Tests (vitest):* projection (title/params/category/reversible/signable); action renders sim diff + disables on revert; `resolveApproval` branch coverage incl. no-signature-in-body.

**PR-W6 — Web host signing.** `webSafeSigning.ts` (`eth_signTypedData_v4` — new path; reuse `eip6963.ts`+`walletProof.ts` reject detection), `safeApi.ts`, mount provider in `App.tsx`.
*Acceptance:* re-derive+compare hash (mismatch throws before signing); chain cross-check before popup; not-owner short-circuits; EIP-1193 `4001` → quiet `rejected`.
*Tests:* hash-mismatch pre-sign; chain mismatch abort; not-owner short-circuit; user-reject quiet.

**PR-W7 — Desktop host signing (H5 fix).** `safe-sign.ts` (loopback **POST-body** signature return), facade-served `safe-sign.html` (opaque `proposal_ref`+state, fetch canonical doc, refuse URL-injected tx, non-loopback refusal), `awaitLoopbackPostBody` in `loopback-server.ts`, `main/index.ts` IPC, binder. Bearer never leaves main.
*Acceptance:* signature returns via POST body; bearer/signature never in any URL/history; page ignores URL-injected typed-data.
*Tests (desktop):* **H5** no signature/bearer in any redirect URL or navigation history; **deeplink** URL-injected typed-data ignored; main is the sole holder of the bearer for `/transactions`.

**PR-W8 — Security regression suite + CI invariants.** Consolidated hermetic real-graph run→stream test (deterministic fake model, fake EIP-1193 + fake tx-service, per the verification keystone) + the CI gates.
*Acceptance (all under CI):*
- J-Safe happy path: connect→read→propose→request_wallet_signature→`approval_requested`→renderer signs→`approval_resolved(approved)`→agent reports confirmations; **no on-chain broadcast**; the graph never held a signature.
- Injection kill-switch: malicious tool result that auto-proposes+auto-signs stalls at the interrupt; no human approve → no signature; **decoded==simulated** asserted (not hash equality — H3).
- CI invariants: no server-side wallet key / `eth_sign*` path (ai-backend + facade + safe-mcp); sweeper stays rejection-only (`approval_expiry_sweeper.py:220`); no new `enqueue_approval_resolved` caller emits `approved` without the identity-scope check (coordinator L309–316); `wallet_signature ∉ APPROVAL_FORWARDABLE_KINDS`.

**Suggested merge order:** S1, S2 → W0 → W1 → W2 → W3 → W4 → W5 → W6 → W7 → W8. W1/W2 can parallelize with W3/W4 (safe-mcp vs ai-backend) once W0's contracts (`SafeDecodedEffect`, endpoint shapes, `proposal_ref`) are frozen.

---

## 6. Gating decisions — MADE with principal-eng defaults

Section 1 already **recommends** the three items the task pre-scoped (Safe MCP host = new `services/safe-mcp`, §1.5; bound-Safe store = `backend` `safe_bindings`, §1.4; simulation provider = self-hosted `eth_call`/anvil, §3.1) and the desktop handoff (POST-body-to-loopback, §1.6). The **three genuinely-open gating decisions** below need product/risk sign-off; each has a default you can ship against.

### GD-1 — Value-signing chain allowlist (`SAFE_SIGNING_ALLOWED_CHAIN_IDS`)

**Default:** a **new, separate** env `SAFE_SIGNING_ALLOWED_CHAIN_IDS`, **not** reused from SIWE (`siwe.py:85` = `1,8453,42161,4663`). Default membership: **`8453` (Base) only** for v1 — one L2 where the treasury demo lives, low fee, fast finality.
**Rationale:** M1 — a login allowlist and a treasury-signing allowlist have different blast radii; a Safe often exists on multiple allowlisted chains and a "Base" label must never commit a signature valid on Ethereum mainnet. Narrow-by-default is the safe posture for an irreversible value action; widen deliberately.
**What your input changes:** if the target treasuries are on Ethereum mainnet or Arbitrum, add `1`/`42161`. If you want parity with login chains, set it equal to the SIWE list — but that is an explicit, logged choice, not a default. Robinhood Chain (`4663`) stays **out** unless you confirm Safe + tx-service support there.

### GD-2 — Delegatecall (`operation==1`) policy in v1

**Default:** **hard-block `operation==1` entirely** in v1 — reject at the builder (`signable=false`) **and** the facade submit (`delegatecall_blocked`). No allowlist.
**Rationale:** H2/H8 — a delegatecall runs arbitrary code in the Safe's context (can swap owners, drop threshold to 1, sweep everything) and a naive asset-diff can render benign. A human cannot interpret "Operation: 1." Blocking entirely removes the highest-severity fund-loss path with the smallest v1 surface; a curated allowlist (e.g. `MultiSendCallOnly`) is real work (audited address set per chain + distinct high-friction confirm) and buys little for the FTUE demo.
**What your input changes:** if batching multiple sends in one signature is a required v1 workflow, we add a **hard-coded, per-chain, audited-library allowlist** (start: `MultiSendCallOnly`) with a distinct "this can change owners/threshold" confirm — that becomes a scoped follow-up PR, not v1.

### GD-3 — Sheets endpoint reality + release stage + OAuth-client ownership

**Default:** ship `google-sheets` as **`release_stage: preview`** with `requires_pre_registered_client: true`; the FTUE 1-click **degrades to "connect in Settings / needs setup"** until an operator provisions the client, rather than presenting a dead button. Endpoint pinned as an **operator-confirmed placeholder**; a real endpoint requires either an official Google Sheets MCP with cell-write tools or a **self-hosted Sheets MCP service** (net-new deployable wrapping Sheets REST v4 — own venv/Dockerfile/deploy, **exceeds P6b**).
**Rationale:** honesty over a fake affordance — the loader + `_assert_available` fail closed, so a preview row that can't connect says so. `spreadsheets` is a Google **sensitive scope** requiring a verified consent screen; defaulting to operator-provisioned client keeps 0xCopilot off the hook for a shipped sensitive-scope client until verification is done.
**What your input changes:** (a) if you have/confirm an official Sheets-MCP endpoint with cell writes → pin it and promote to `stable`, and the FTUE 1-click connects in the default build; (b) if you want Sheets 1-click in the default desktop build now → either enable `DESKTOP_CONNECTORS_ALLOW_PREVIEW=true` in the desktop supervisor env (preview stays) or commit to building `services/sheets-mcp` (a scoped new-service PR set); (c) confirm who owns the Google Cloud OAuth client — per-deployment operator secret (default) vs a shipped 0xCopilot-owned verified client.

**Also resolved inline (not blocking):** execution ceiling — the agent stops at "signature added to the multisig queue"; on-chain execution is a separate, later, explicitly-gated action, **out of P6** (hardened Q7). Safe brand `#12FF80` is a tiny inline swatch only, never the app accent (one-accent sky discipline).

---

## 7. What this plan deliberately does NOT do (anti-bandaid ledger)

- Does **not** keep `safeTxHash` re-derivation in the safety invariant — demoted to an integrity/transport check (H3). The injection test asserts **decoded==simulated**, not hash equality.
- Does **not** enforce bound-Safe/chain in the renderer or the MCP layer — the native builtin bypasses both. Enforcement is server-side at the facade on server-derived identity (Control C).
- Does **not** subclass `McpApprovalMetadata` for the wallet metadata — that inherits `extra="allow"` (M2). Standalone `extra="forbid"` model, built server-side.
- Does **not** rely on client discipline to keep the signature out of graph state — the coordinator + schema reject signature-shaped `answer` server-side (M4).
- Does **not** copy the URL-passing login loopback for value signing — POST-body handoff, bearer stays in main, page fetches the canonical doc (H5/M5/deeplink).
- Does **not** ship a fail-open simulation — the facade re-simulates and rejects on the fail-closed matrix (H1/H7).
- Does **not** name Sheets write tools without write-terms **and** patches the classifier so the coupling can't bite a future connector (P6b §4.4).

---

## Open questions

- GD-1: SAFE_SIGNING_ALLOWED_CHAIN_IDS default membership — ship Base(8453)-only, or add Ethereum(1)/Arbitrum(42161)? Must be a NEW env, not the reused SIWE login list.
- GD-2: Delegatecall (operation=1) in v1 — hard-block entirely (default), or curate a per-chain audited-library allowlist (MultiSendCallOnly) with a distinct high-friction confirm? The latter is a scoped follow-up PR.
- GD-3: Sheets endpoint + release stage — ship preview/needs-setup (default), pin an official Sheets-MCP endpoint and promote to stable, or commit to building a net-new services/sheets-mcp (Sheets REST v4 wrapper, own venv/Dockerfile/deploy)? Plus: who owns the Google sensitive-scope OAuth client (per-deployment operator secret vs shipped 0xCopilot-owned verified client)?
- Confirm the recommendations in §1: Safe MCP as a new services/safe-mcp with a hard read/propose-vs-submit split (vs backend-hosted); bound-Safe store as a first-class backend safe_bindings store (vs MCP-record metadata or per-conversation scope); proposal_ref as the authoritative decode handle with proposal persistence in safe-mcp (vs stateless re-decode at every gate).
- Confirm the desktop handoff shape: POST-body-to-loopback with the bearer never leaving main (recommended) vs a one-time scoped token the page uses to POST the signature directly to the facade.
- Simulation fidelity: self-hosted eth_call default vs anvil-fork (SAFE_SIM_MODE=anvil) for full state-override asset-diffs — anvil is heavier ops; confirm whether v1 needs delta-precise diffs or eth_call balance-read diffs suffice.
- Anchor drift: the PRD line citations are stale against HEAD (see §0). Confirm implementers use the verified anchor table, not the PRD numbers.


# P6a — Safe{Wallet} connector with PROPOSE-ONLY signing (DESIGN PRD)

Security-critical. Read alongside `docs/plan/first-run-onboarding/README.md` §7.2 and `JOURNEYS.md` "Cross-cutting — Tools popover". All paths are relative to ROOT.

---

## 1. Goal + scope

**Goal.** Give the agent the ability to (a) READ a Safe's state and (b) PROPOSE a transaction (build calldata + preview it), while making the human's wallet the _only_ thing that can produce a signature — every signature is an explicit, per-call, human-in-the-loop action in the renderer wallet. The agent never signs, never executes, never holds a key.

**The one hard invariant (must hold in code, not just intent).**

> The agent may READ Safe state and PROPOSE a transaction (build calldata). It can raise a _request to sign_. It CANNOT sign, cannot submit a signature, and cannot broadcast. A signature is produced only by an explicit human action in the renderer wallet, gated per-call by the approval interrupt, and confirmed a second time inside the wallet popup itself.

**Three trust boundaries (the spine of the design):**

| Boundary    | What happens                                                                                       | Gate                                                                                       | Who acts           |
| ----------- | -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | ------------------ |
| **CONNECT** | Install the Safe connector + bind the specific Safe address(es) + chain the agent may operate on   | first-use `mcp_auth_required` → `ConnectorConsentCard` (kind `mcp_auth`)                   | human 1-click      |
| **READ**    | Read Safe info / owners / threshold / nonce / balances / pending queue                             | `approval: session` (first read only)                                                      | human once/session |
| **SIGN**    | Human signs a specific SafeTx in their wallet; signature submitted to the Safe Transaction Service | `approval: per_call` → `ConnectorConsentCard` (new kind `wallet_signature`) + wallet popup | human, twice       |

**In scope (v1):** read tools; a side-effect-free `propose` tool; the `wallet_signature` per-call gate; the renderer EIP-1193 signing path (web + desktop); the `approval_requested → ConnectorConsentCard` wiring; guardrails (chain allowlist reuse, destination/amount review, independent simulation, injection resistance).

**Out of scope (v1, flagged):** on-chain **execution** (broadcasting a multisig tx once threshold is met) — the agent's ceiling is "your signature was added to the multisig queue"; the user executes in the Safe app or via a future, separate execute-gate. Collecting a _second_ owner's signature (that is another human) is also out of scope. Trial lane stays shelved (no Safe involvement).

**Non-goals:** re-authoring the composer/Tools-popover (reuse `ToolPicker`); a second approval card (reuse `ApprovalCard`); any hosted signing key or server-held wallet.

---

## 2. Files to CREATE / EDIT

### Backend — connector registration (`services/backend`)

- **EDIT** `src/backend_app/mcp_catalog.py` — add a `CatalogEntry(slug="safe", …, auth_mode=McpAuthMode.NONE)` seed to `DEFAULT_CATALOG` (anchor: dataclass L31-77, tuple L83).
- **EDIT** `src/backend_app/connectors/catalog.yaml` — add marketing `safe` entry so the profile's `connector_slug` resolves (loader invariant in `profile_catalog.py` L225-228).
- **EDIT** `src/backend_app/connectors/desktop_profiles.yaml` — add a `safe-global` profile (read tools `session`, propose tool `session`; `reuses_existing_seed: true`).

### First-party Safe MCP surface + wallet proxy (host TBD — see Open Questions)

- **CREATE** `services/safe-mcp/…` **or** `services/backend/src/backend_app/wallet/safe/` — a first-party MCP-over-HTTP surface exposing Safe **read + propose** tools only. Holds no signing key. Talks to the Safe Transaction Service REST API + a read-only RPC node. (Recommend a new minimal service to keep chain-RPC concerns off `backend`; backend-hosted is the lighter alternative.)
  - `safe_tools.py` — the read + propose tool implementations.
  - `safe_tx.py` — canonical SafeTx builder + EIP-712 typed-data + `safeTxHash` derivation.
  - `simulation.py` — read-only simulation (eth_call / Tenderly) → asset-diff.
  - `safe_txservice_client.py` — Safe Transaction Service client (read queue; create-multisig-tx-with-signature on the submit path).
  - `chain_allowlist.py` — reuse of `SIWE_ALLOWED_CHAIN_IDS` parsing.

### AI backend — the SIGN gate (`services/ai-backend`)

- **CREATE** `src/agent_runtime/capabilities/tools/builtin/request_wallet_signature.py` — `RequestWalletSignatureTool`, a builtin tool that raises the per-call `approval_requested` interrupt (modeled on `ask_a_question.py`). Holds no key; its only job is to pause for a human wallet signature and read back the status.
- **EDIT** `src/agent_runtime/api/constants.py` — add `Values.ApprovalKind.WALLET_SIGNATURE = "wallet_signature"` (anchor: `class ApprovalKind` L246-252).
- **EDIT** `src/agent_runtime/execution/factory.py` — register `RequestWalletSignatureTool` in the builtin tool assembly (next to `AskAQuestionTool`); no `interrupt_on` change needed since the tool raises its own interrupt (contrast `_native_interrupt_config` L416).
- **EDIT** `src/runtime_worker/stream_events.py` — add `_native_wallet_signature_payload(...)` mirroring `_native_ask_a_question_payload` (L729-764) and dispatch it from `native_interrupt_payloads` (L640-661) before the tool-approval fallthrough; preserve the structured Safe metadata.
- **EDIT** `src/runtime_worker/approval_recognisers.py` — add `SafeApprovalRecogniser(vendor_tokens=("safe",))` projecting to/value/token/chain/nonce params + `reversibility()==NO`; register in `_RECOGNISERS` (L267-273).
- **EDIT** `src/runtime_api/schemas/approvals.py` — add `SafeProposalSummary` + `WalletSignatureApprovalMetadata` (reuse `McpApprovalMetadata`'s `extra="allow"` round-trip pattern, L291-307).
- (No change to `ApprovalCoordinator` — `answer` already threads to the resume command, L388; verify `wallet_signature ∉ APPROVAL_FORWARDABLE_KINDS`, L70-75, which holds.)

### Facade — wallet/safe proxy (`services/backend-facade`)

- **EDIT** `src/backend_facade/app.py` — add `POST /v1/wallet/safe/simulate`, `GET /v1/wallet/safe/{safe}` (info/balances/pending), `POST /v1/wallet/safe/{safe}/transactions` (create multisig tx WITH signature). Model the MCP proxy block (L236-384); forward the caller's identity; **never** log the signature body.

### Shared contracts (`packages/api-types`)

- **EDIT** `src/index.ts` — add `SafeProposalSummary`, `SafeSimulationResult`, `WalletSignatureApprovalMetadata`, `SafeSignRequest`, `SafeSignResult`; widen the `approval_kind` unions to include `"wallet_signature"` (L1617/1832/1872/1887).

### chat-surface (SSOT surface — `packages/chat-surface`)

- **CREATE** `src/ports/safeSigning.ts` — `SafeSigningPort` interface (host-injected EIP-1193 signing).
- **CREATE** `src/providers/SafeSigningProvider.tsx` + `useSafeSigning()`.
- **CREATE** `src/approvals/SafeSignAction.tsx` — presentational review-block + "Review & sign in wallet" / "Reject" action node, supplied to `ApprovalCard`'s `actions`/`result` slots.
- **EDIT** `src/destinations/run/approvalProjection.ts` — extend `RunApprovalKind` + project `wallet_signature` (params, `category={vendor:"SAFE",access:"ACTION"}`).
- **EDIT** `src/workspace/types.ts` — add `"wallet_signature"` to `ApprovalsQueueItem.approvalKind` (L67-73).
- **EDIT** `src/destinations/run/RunDestination.tsx` — in `resolveApproval` (L523-558) add the `wallet_signature` branch that signs via the port **before** POSTing the decision.
- **EDIT** `src/index.ts` — barrel-export the new port/provider/component.

### Web host (`apps/frontend`)

- **CREATE** `src/features/wallet/webSafeSigning.ts` — `SafeSigningPort` impl (in-page EIP-1193, reuse `features/auth/eip6963.ts` + `walletProof.ts`).
- **CREATE** `src/api/safeApi.ts` — HTTP clients for the facade wallet/safe endpoints.
- **EDIT** the web ChatShell provider stack (`src/app/App.tsx`) — mount `SafeSigningProvider` with the web impl.

### Desktop host (`apps/desktop`)

- **CREATE** `main/wallet/safe-sign.ts` — main-process signing via loopback + facade-served `safe-sign.html` (model `main/auth/wallet-login.ts` L72-151).
- **EDIT** `main/index.ts` — register the `safe:sign` IPC (mirror the secure-storage IPC registration).
- **EDIT** `renderer/destinationBinders.tsx` (or `bootstrap.tsx`) — bind the `SafeSigningPort` to the IPC and mount `SafeSigningProvider`.
- **CREATE** facade-served static `safe-sign.html` (same mechanism that serves `wallet.html`, facade `app.py` L138) — runs EIP-1193 in the system browser and relays the signature to the loopback.

---

## 3. New signatures (types / ports / tools)

### 3.1 Python — the SIGN gate builtin (`request_wallet_signature.py`)

```python
class RequestWalletSignatureInput(RuntimeContract):
    proposal_ref: str            # opaque handle to the propose-tool result
    chain_id: int                # must be in SIWE_ALLOWED_CHAIN_IDS
    safe_address: str            # EIP-55; must be an authorized (bound) Safe
    safe_tx_hash: str            # agent-claimed hash — renderer RE-DERIVES + compares
    to: str; value: str; data: str; operation: int; nonce: int
    token: str | None = None     # display symbol; None ⇒ native
    summary: str | None = None   # human sentence ("Send 250 USDC to treasury.eth")

@dataclass(frozen=True)
class RequestWalletSignatureTool:                 # mirrors AskAQuestionTool
    runtime_context: AgentRuntimeContext
    interrupt_handler: Callable[[dict[str, Any]], object] = langgraph_interrupt
    name: str = Values.Tool.REQUEST_WALLET_SIGNATURE
    async def ainvoke(self, raw_input) -> dict[str, Any]: ...
    # payload: {api_event_type:"approval_requested", approval_kind:"wallet_signature",
    #           approval_id, status:"pending", safe:{...}, params:[...], message}
    # _resume_result(resume) -> {ok, decision, status}   # status is NON-secret
```

### 3.2 Python — structured consent metadata (`approvals.py`)

```python
class SafeProposalSummary(RuntimeContract):
    chain_id: int; chain_name: str; safe_address: str; safe_tx_hash: str
    to: str; value: str; token: str | None; nonce: int; operation: int
    simulation: SafeSimulationSummary | None = None   # success + asset diffs

class WalletSignatureApprovalMetadata(McpApprovalMetadata):   # extra="allow"
    vendor: str = "SAFE"
    category: ApprovalCategory = ApprovalCategory.ACTION
    reason_code: ApprovalReasonCode = ApprovalReasonCode.IRREVERSIBLE
    reversible: ApprovalReversible = ApprovalReversible.NO
    safe: SafeProposalSummary
```

### 3.3 TypeScript — the renderer signing port (`safeSigning.ts`)

```ts
export interface SafeSignRequest {
  chainId: number;
  safeAddress: string;
  ownerAddress: string;
  safeTxHash: string; // agent-claimed; re-derived before signing
  eip712TypedData: unknown; // canonical SafeTx EIP-712 doc from the facade
}
export interface SafeSignResult {
  status:
    | "signed"
    | "rejected"
    | "chain_not_allowed"
    | "not_owner"
    | "hash_mismatch";
  safeTxHash: string;
  confirmations?: { have: number; needed: number }; // after tx-service submit
}
export interface SafeSigningPort {
  /** Independently re-derive+re-simulate, sign via EIP-1193, submit to the
   *  Safe tx-service, and return a NON-secret status. Never returns the raw signature. */
  signSafeTransaction(req: SafeSignRequest): Promise<SafeSignResult>;
}
```

### 3.4 TypeScript — projection + approval kind

```ts
export type RunApprovalKind =
  | "tool_action"
  | "mcp_tool"
  | "mcp_auth"
  | "ask_a_question"
  | "wallet_signature"; // NEW
```

---

## 4. Precise wiring into the real code

1. **Register the connector.** `mcp_catalog.py`: append the `safe` seed to `DEFAULT_CATALOG` (L83). `catalog.yaml`: append the `safe` marketing entry. `desktop_profiles.yaml`: add the `safe-global` profile with `reuses_existing_seed: true`, `server_id: "seed:safe"`, all tools `product_scope: read` + `approval: session` (this passes `ConnectorToolPolicy._mutating_tools_require_per_call_approval`, `profile_catalog.py` L75-84, because none are write/draft). Install flows through the existing `install_from_catalog` (`service.py` L337) → the FTUE Tools-popover "Safe{Wallet}" 1-click works with no new install plumbing.

2. **Bind the Safe at connect.** During CONNECT, capture the user-chosen Safe address(es)+chain and persist them on the server record metadata (or a small `safe_bindings` store). The propose/read tools are **constrained to bound Safes** — the agent cannot target an arbitrary Safe. First use raises `mcp_auth_required` via the existing path (`auth_mcp.py` L80-104) → `ConnectorConsentCard`.

3. **READ + PROPOSE.** The agent calls read/propose tools through the normal `call_mcp_tool` path (`middleware/call_tool.py`), which re-checks authorization after resolve (L83-92) and honors `paused_connectors` (`permissions.py` L44). Because these tools are `read`/`session`, the first use raises a session consent card and subsequent reads run silently. `propose` returns a `safe_tx_proposal` surface (dest/amount/token/chain/nonce + EIP-712 + simulation) — it stages nothing and signs nothing.

4. **Raise the SIGN gate.** The agent calls the builtin `request_wallet_signature`. Following `ask_a_question.py` (L118-137), it raises `langgraph_interrupt({approval_kind:"wallet_signature", …, safe:{…}, params:[…]})`. Register the tool in `factory.py`'s builtin assembly next to `AskAQuestionTool`.

5. **Worker projects the approval event.** In `stream_events.py`, `native_interrupt_payloads` (L640-661) dispatches: add `_native_wallet_signature_payload` (mirror `_native_ask_a_question_payload` L729-764) that matches `approval_kind == "wallet_signature"`, normalizes `approval_id`/`action_id`/`batch_id`, and preserves the `safe` block + `params`. Run the `SafeApprovalRecogniser` through the existing `_mcp_approval_structured` seam (L957-995) so the params frame is populated exactly like other vendors.

6. **Card renders.** `approvalProjection.ts` projects `wallet_signature` into a `RunApproval` (title from `summary`, `category={vendor:"SAFE",access:"ACTION"}`, params = dest/amount/token/chain/nonce). `RunDestination.tsx` feeds it to `ApprovalCard` (`approvals/ApprovalCard.tsx` L46-113) with `SafeSignAction` as the `actions` node and the simulation preview as `result`.

7. **Human signs (the gated act).** `RunDestination.resolveApproval` (L523-558): for `approvalKind==="wallet_signature"`, do **not** POST a plain approve first. Instead call `safeSigning.signSafeTransaction(req)`:
   - the port re-derives `safeTxHash` from the canonical fields (via the facade `POST /v1/wallet/safe/simulate`) and **refuses** (`hash_mismatch`) if it disagrees with the card's claimed hash;
   - it validates `chainId ∈ SIWE_ALLOWED_CHAIN_IDS` and `ownerAddress` is a Safe owner (`not_owner` otherwise);
   - it runs EIP-1193 `eth_signTypedData_v4(owner, eip712TypedData)` (web: in-page reusing `walletProof`; desktop: loopback page like `wallet-login.ts`) — the human confirms in the wallet popup;
   - it submits the signature to `POST /v1/wallet/safe/{safe}/transactions` (facade → Safe tx-service), which **creates the multisig tx WITH the signature atomically** (no unsigned proposal ever sits in the queue);
   - returns a NON-secret `SafeSignResult`.
8. **Resolve the approval.** On `status==="signed"`, POST `/v1/agent/approvals/{id}/decision {decision:"approved", answer:<status string>}` (facade L1108, coordinator `record_approval_decision` L286 threads `answer` into the resume command L388). On `rejected`/`hash_mismatch`/`chain_not_allowed`/`not_owner`, POST `{decision:"rejected", reason:<code>}` (or leave pending for user retry). The raw signature is **never** part of the decision body.
9. **Agent resumes with status only.** `RequestWalletSignatureTool._resume_result` reads `answer` (as `ask_a_question` reads it, L173-181) and returns `{ok, decision, status}`. The agent can now say "your signature was added — 1 of 2 confirmations". It never saw a key or a signature.

**The exact approval + signing sequence (numbered):**

```
① Agent: safe read tools (session)               → Safe state
② Agent: propose(to,value,token,chain)           → safe_tx_proposal surface (no side effect)
③ Agent: request_wallet_signature(safeTxHash,…)  → langgraph interrupt
④ Worker: _native_wallet_signature_payload       → approval_requested(kind=wallet_signature)
⑤ Renderer: ConnectorConsentCard shows dest/amount/sim (from RENDERER's own simulation)
⑥ Human: clicks "Review & sign in wallet"
⑦ Port: facade /simulate → re-derive safeTxHash; compare; check chain+owner  (guard)
⑧ Human: confirms eth_signTypedData_v4 in the wallet popup                    (2nd confirm)
⑨ Port: POST /v1/wallet/safe/{safe}/transactions {safeTx, signature}          (renderer→facade→tx-service)
⑩ Renderer: POST /approvals/{id}/decision {approved, answer:status}           (no signature in body)
⑪ Coordinator: audit approval.accept; enqueue resume(answer)
⑫ Agent: resumes; reports "signed · N/M confirmations"; NEVER executes
```

---

## 5. Parity notes (design classes → design-system tokens/primitives)

Per `design-source/SPEC.md`. Design-system is the SSOT; never hard-code hex.

- **Tools-popover Safe row** — the existing `ToolPicker` connector row (SPEC §Data: "Safe{Wallet} · propose & sign transactions", 1-click, `connected` on select; group note "1-click connect · you approve first use"). Reuse; no re-author.
- **Consent card** — reuse `ApprovalCard` (`atlas-approval-card__*`). Header title = the human summary; `reason` = "Copilot built this transaction. You sign it in your own wallet."; vendor pill `SAFE · ACTION`.
- **Destination/amount review** — the params frame (`atlas-approval-card__params` → `ActivityParams`): rows To / Amount / Token / Chain / Safe / Nonce, mono labels sized per SPEC (`--font-mono`, ~9.5px). Values are truncated addresses (client-side truncation, per STATUS.md verify-at-impl note).
- **Simulation asset-diff** — pass as the `result` node: `+` rows in `--color-success`, `-` rows in `--color-danger`; a revert renders the danger token + disables the primary button.
- **Primary button** — "Review & sign in wallet" uses the accent-filled treatment (design `gbtn--pri` → `--color-accent` bg, `--color-accent-contrast` text). One-accent discipline: **sky only**. Safe brand `#12FF80` is a _swatch_ (like the per-provider dots in README §2), permitted only as a small inline connector glyph, never as the app accent.
- **Footer reassurance** (`atlas-approval-card__foot`) — "Copilot proposes. You sign in your own wallet — it never holds your keys." (shield glyph, existing).
- **Reversible marker** — `reversible=no` ⇒ no undo countdown chip (contrast the 60s window for reversible writes).

---

## 6. Test list

**Python — ai-backend (service `.venv`, `pytest`):**

- `request_wallet_signature`: valid input raises interrupt with `approval_kind=wallet_signature` + safe block; malformed input returns a typed rejection (mirror ask-a-question tests); resume `approved`+`answer` → `{ok:true,status}`; resume `rejected` → `{ok:false}`.
- `stream_events._native_wallet_signature_payload`: recognizes the kind, preserves `safe`/`params`, sets `batch_id/index`, does not fall through to `native_tool_approval_payloads`.
- `SafeApprovalRecogniser`: projects to/value/token/chain/nonce; `reversibility()==NO`; registry dispatch order (L267) unaffected for other vendors.
- Coordinator invariant: `wallet_signature ∉ APPROVAL_FORWARDABLE_KINDS` — a forward decision on it is rejected 422.
- `WalletSignatureApprovalMetadata` round-trips through `ApprovalRequestRecord.metadata` (extra=allow) and re-validates on read.

**Python — backend / safe-mcp:**

- SafeTx builder → deterministic `safeTxHash` for known vectors; EIP-712 typed-data matches Safe's domain separator per chain.
- Simulation returns asset-diff; revert path surfaces `revertReason`.
- `chain_allowlist` rejects a chain outside `SIWE_ALLOWED_CHAIN_IDS`.
- Profile loader: `safe-global` reconciles (marketing slug known, seed exists); a propose tool mis-marked `write`+`session` fails boot (`_mutating_tools_require_per_call_approval`).
- **No-key assertion:** grep/test that the safe surface has no signing-key path and no broadcast/exec call.

**Python — facade:**

- `/v1/wallet/safe/*` forward identity + scope; signature body is present in the submit call but **absent from logs** (assert redaction).

**TypeScript — chat-surface (vitest):**

- `approvalProjection`: `wallet_signature` → correct `RunApproval` (title/params/category); pending vs resolved.
- `SafeSignAction` renders the sim diff (success/danger tokens) and disables sign on revert.
- `RunDestination.resolveApproval`: `wallet_signature` calls the port before POSTing; `hash_mismatch`/`chain_not_allowed`/`not_owner`/`rejected` do NOT POST an approve; `signed` POSTs `approved` with `answer` and **no signature field**.

**TypeScript — web host:** `webSafeSigning` re-derives+compares hash (mismatch throws before signing); EIP-1193 4001 → quiet `rejected`; not-owner short-circuits before the wallet popup.

**Live-stack (per `docs/plan/verification/`, hermetic real-graph run→stream):**

- J-Safe happy path: connect → read → propose → request_wallet_signature → `approval_requested` on the stream → renderer signs (fake EIP-1193 provider + fake tx-service) → `approval_resolved(approved)` → agent reports confirmations. Assert **no** on-chain broadcast occurred and the graph never held a signature.
- Injection kill-switch: a malicious tool result that tries to auto-propose+auto-sign still stalls at the interrupt; with no human approve, no signature is ever produced; a tampered `safeTxHash` in the request is caught by the renderer re-derivation (`hash_mismatch`).

---

## 7. Acceptance criteria

1. There is **no code path** by which the agent, a tool result, or the worker produces or submits a signature or a broadcast. Signing is EIP-1193 in the renderer only; the raw signature reaches the Safe tx-service via the facade proxy and **never** enters agent/graph/worker/approval-decision state. (Enforced by test + the no-key assertion.)
2. Every signature requires **two** explicit human actions: approving the `ConnectorConsentCard` AND confirming in the wallet popup. Neither can be triggered by model output alone.
3. `propose` has **zero** side effects (no queue write, no signature, no broadcast) and is `read`/`session`; the only `per_call` gate is `wallet_signature`.
4. The renderer independently re-derives `safeTxHash` and re-runs simulation from canonical fields; a mismatch with the agent-claimed hash blocks signing (`hash_mismatch`). The card shows the renderer's simulation, not agent-authored text.
5. Chain is constrained to `SIWE_ALLOWED_CHAIN_IDS` (reuse, not a new list); the signer must be a Safe owner on a **bound** Safe; the agent cannot target an unbound Safe.
6. `wallet_signature` approvals are non-forwardable and reversible=no; every decision writes an `approval.accept`/`approval.reject` audit row with non-secret Safe context (chainId/safe/safeTxHash/to/value).
7. Both hosts work: web signs in-page; desktop signs through the facade page + loopback. The chat-surface package stays substrate-clean (no bare `window`/`fetch` — signing goes through `SafeSigningPort`).
8. FTUE Tools-popover "Safe{Wallet}" 1-click connects through the existing catalog→profile→install path; parity with SPEC (copy, card zones, tokens, one-accent).

---

## 8. Risks / edge-cases

- **The signature-threading temptation.** Never thread the raw signature through the approval decision `answer`/`edited_payload` — those enter graph state and audit metadata. Submit out-of-band (renderer→facade→tx-service); the decision carries only a status string. (Guard in tests.)
- **Simulation provider trust.** eth_call is truthless about MEV/reordering; a Tenderly-style provider can be wrong or down. Treat simulation as advisory: on simulate failure/uncertain, show a warning and still require the same two human confirmations — never auto-approve, never hide the destination.
- **Ordering / partial failure.** If the tx-service submit fails after a good signature, resolve `rejected` with a retryable reason; do NOT resolve `approved` (the agent must not believe a confirmation landed). Idempotency: resubmitting the same `(safe, safeTxHash, signature)` must not double-create the multisig tx.
- **Nonce races.** Two proposals for the same nonce collide at the Safe. Bind the proposal to the read-back nonce and re-check at submit time; surface a clear "nonce changed — re-propose" rather than silently signing a stale tx.
- **Wrong account in wallet.** The connected wallet may switch accounts mid-flow; verify the signing address equals the bound owner before submit (`not_owner`), and never sign with a non-owner.
- **Desktop loopback surface.** The `safe-sign.html` page must refuse non-loopback handoff targets (as `wallet.html` does) and must not accept a `safeTxHash`/typed-data it did not itself derive from the facade — otherwise a malicious deep link could present a spoofed tx to sign.
- **Batch approvals.** If the agent raises multiple `wallet_signature` requests, each is its own per-call card (unique `approval_id` via a per-invocation suffix like `ask_a_question` L145-151) — never a single "approve all".
- **`enable_local_models`/gating parity.** Safe read is public-by-address; the `mcp_auth NONE` + explicit Safe-binding must not degrade into "any Safe" — keep the binding an explicit, user-chosen, per-user constraint.
- **Boundary discipline.** Keep chain-RPC + Safe tx-service concerns out of `ai-backend` (orchestration only) and off `backend-facade` (thin proxy only); they live in the first-party Safe surface.

---

## Open questions

- Host location for the first-party Safe MCP surface: a new minimal `services/safe-mcp` (cleanest boundary — chain RPC + tx-service client is its own concern) vs backend-hosted `backend_app/wallet/safe/` (lighter, reuses backend's MCP + external-call posture). Recommend the new service; needs a boundary-doc + Dockerfile/deploy decision.
- Safe-binding storage: where do we persist the user-authorized Safe address(es)+chain that constrain propose/read? Reuse the MCP server record metadata, a new per-user `safe_bindings` store, or per-conversation connector scope? (Security: the agent must never target an unbound Safe.)
- Seed auth_mode for `seed:safe`: NONE (Safe tx-service reads are public by address) with an explicit binding step, vs a first-party session token for rate-limiting/attribution. Confirms whether the CONNECT card is an OAuth grant or a Safe-picker.
- Simulation provider: self-hosted eth_call/anvil fork vs Tenderly (key + cost + external dependency). Affects the asset-diff fidelity and the 'simulate before sign' guarantee; decide the fallback UX when simulation is unavailable.
- v1 execution ceiling: confirm the agent stops at 'your signature added to the multisig queue' and on-chain EXECUTION (broadcast when threshold met) is a separate, later, explicitly-gated action (not P6a). This is the security-defining scope line.
- Whether to expose a Safe brand glyph at all in the Tools popover / card given one-accent discipline — confirm `#12FF80` is allowed only as a tiny inline swatch, never as accent.
- Desktop signing UX: reuse the system-browser + loopback (`wallet-login.ts` pattern) so the browser wallet extension can sign, vs an in-Electron injected provider. Recommend the loopback page for parity with existing wallet login.

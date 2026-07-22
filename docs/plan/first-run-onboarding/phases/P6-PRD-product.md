# P6 — Safe{Wallet} + Google Sheets connectors (Product PRD)

**Status:** Product design · **Phase:** P6 (First-Run Onboarding master plan `docs/plan/first-run-onboarding/README.md` §6, §7.2, §7.3) · **Owner:** PM · **Companion eng PRDs:** `phases/PRD-P6a-hardened.md` (Safe, security-gated), `phases/PRD-P6b.md` (Sheets).

> This is the **product** PRD. It defines the users, jobs, scope, flows, metrics, and the three product decisions that gate build. The engineering PRDs (`PRD-P6a-hardened.md`, `PRD-P6b.md`) own file lists, signatures, and the security control matrix; where they and this doc disagree on a security invariant, the hardened eng PRD wins. All paths are relative to ROOT.

---

## 1. TL;DR

The First-Run Tools popover (`design-source/SPEC.md:35`) advertises two connectors that don't exist yet: **Safe{Wallet}** ("propose & sign transactions") and **Google Sheets** ("read & write workbooks"). P6 makes both real, built on the shipped MCP connector substrate (`services/backend/src/backend_app/connectors/`), and wires them into the existing human-in-the-loop approval path (`/v1/agent/approvals/{id}/decision`, facade `app.py:1114`).

The single product principle that shapes everything: **the agent proposes, the human commits.** For Safe this is absolute — the agent can read a Safe and build a transaction, but a signature is only ever produced by an explicit human action in the user's own wallet (never a server-held key). For Sheets, every write is gated behind a per-call approval card before a cell is touched. Both connectors turn "0xCopilot can look at my stuff" into "0xCopilot can safely act on my stuff, with me in the loop."

---

## 2. Users and jobs-to-be-done

### 2.1 Who

The FTUE is the first thing a new user sees, and the connector rows are the first "this tool can touch my real accounts" moment. Two personas drive P6:

- **The crypto-native treasury operator / DAO contributor.** Holds funds in a Gnosis **Safe{Wallet}** multisig. Already signs in with a wallet (SIWE is live — `services/backend/src/backend_app/identity/siwe.py`; address surfaces on the top-bar wallet chip, README §1). Wants an assistant that can watch the Safe and *prepare* transactions, but will never tolerate software that can move funds on its own.
- **The spreadsheet-driven operator (ops / finance / growth).** Lives in Google Sheets — airdrop claim lists, cap tables, launch trackers, KPI dashboards. The "Explain a CSV" starter chip (README §1) already hints at this; Sheets extends it from a one-off attached file to a live, writable workbook.

Both are the same early-adopter profile the product is built for: technical, privacy-conscious (local-first / BYOK gate), and holding something real (funds, or an operational sheet) they don't want an LLM to break.

### 2.2 Jobs

| # | Job (user's words) | Connector | Today | With P6 |
| - | ------------------ | --------- | ----- | ------- |
| J1 | "Watch my treasury Safe and tell me what's in the queue / who still needs to sign." | Safe (read) | Only SIWE **login** exists; no Safe read path (README §4 "only SIWE login exists"). | Agent reads Safe owners / threshold / nonce / balances / pending queue. |
| J2 | "Draft the payout transaction so all I have to do is sign it." | Safe (propose) | — | Agent builds calldata + a decoded, simulated preview; **stages nothing**. |
| J3 | "Let me sign it in my own wallet, and never let the bot move money by itself." | Safe (sign) | — | Human signs per-call in their wallet; the agent's ceiling is "your signature was added to the multisig queue." |
| J4 | "Read my launch-tracker sheet and summarize the top movers." | Sheets (read) | Drive connector explicitly excludes cell/formula reads (`connectors/desktop_profiles.yaml:118-119`). | Agent reads cells/ranges via a real Sheets connector. |
| J5 | "Update the sheet — fill in the numbers, append the new rows." | Sheets (write) | No write path; desktop OAuth start caps at `read`/`draft` (`oauth_coordinator.py:153`). | Agent proposes each write; human approves per-call before any cell changes. |

**The through-line:** users want delegation *up to the commit point* and hard human control *at* the commit point. That's the product, not a compliance afterthought.

---

## 3. v1 scope and non-goals

### 3.1 In scope (v1)

**Safe{Wallet} — propose-only.**
- A Safe connector installable 1-click from the FTUE Tools popover and Settings, on the existing catalog → profile → install path (`connectors/catalog.yaml`, `desktop_profiles.yaml`, `service.py`).
- **Read** tools: Safe info / owners / threshold / nonce / balances / pending queue (`approval: session` — approve once per session).
- **Propose** tool: build a SafeTx (calldata + EIP-712 typed data + simulation) — **side-effect-free**; it queues nothing and signs nothing.
- A **sign gate**: a per-call approval card whose To / Amount / Token rows are derived from **server-decoded calldata + simulation**, not from agent-authored text (`PRD-P6a-hardened.md` control A). The human signs in their own wallet (EIP-1193, web in-page / desktop via loopback). This is the *only* per-call gate; read/propose are session.
- Server-side enforcement, at the facade, that the target Safe is one the user **bound at connect** and the chain is on a signing allowlist (`PRD-P6a-hardened.md` control C).
- Mandatory, **fail-closed** simulation: no positive asset-diff ⇒ the sign button is disabled ⇒ the wallet popup never opens (except a bare native-value transfer with empty calldata, shown "unverified") (`PRD-P6a-hardened.md` control B).

**Google Sheets — read + per-cell write.**
- A `gsheets` connector, 1-click from the same popover (`PRD-P6b.md` §3.1–3.2).
- **Read** tools: get spreadsheet, get / batch-get values, search (`approval: session`).
- **Write** tools: update values, append, clear, batch-update, create spreadsheet — every one `approval: per_call` (enforced by the loader, `profile_catalog.py:75-84`).
- The one net-new backend change that makes a **write** scope requestable through desktop OAuth (today capped at `read`/`draft` — `oauth_coordinator.py:298-309`, `desktop_routes.py:91`).
- Per-chat scoping (pause the connector for a conversation) via the existing `paused_connectors` gate (facade `app.py:483`).

**Shared across both.** Both render as rows in the FTUE Tools popover (P4-owned surface) and Settings, reuse the shipped `ApprovalCard` / consent-card family, and honor the popover's group promise: **"1-click connect · you approve first use"** (`design-source/SPEC.md:35`).

### 3.2 Explicit non-goals (v1)

| Non-goal | Why it's out | Where it goes |
| -------- | ------------ | ------------- |
| **Safe agent auto-execution / broadcast** | The defining safety line. The agent stops at "signature added to the multisig queue"; on-chain execution when threshold is met is a **separate, later, explicitly-gated** action. | Future execute-gate (`PRD-P6a-hardened.md` residual Q7). |
| **Any server-held wallet key or agent-produced signature** | Non-negotiable. No `eth_sign*` path server-side; the sweeper stays rejection-only (`PRD-P6a-hardened.md` CI invariants). | Never. |
| **Collecting a *second* owner's signature** | That's another human's wallet. | Future. |
| **Safe DELEGATECALL (`operation=1`) as a normal action** | Arbitrary code in the Safe's context (owner swaps, threshold drops). Hard-blocked in v1 except a hard-coded audited-library allowlist. | `PRD-P6a-hardened.md` H2/H8, residual Q6. |
| **Sheets: Apps Script, chart/image export, Drive-level file moves/permissions** | Out of the cell-and-structure surface; avoids scope creep into Drive's territory. | `PRD-P6b.md` §3.2 `unsupported_capabilities`. |
| **Building a bespoke Sheets MCP *server process*** | A self-hosted Sheets server is a new deployable service (own venv/Dockerfile/deploy) and exceeds P6. The code path is identical whether the endpoint is official or self-hosted. | Decision, §6.3 + `PRD-P6b.md` §8. |
| **The Tools-popover component itself** | Net-new in **P4**; P6 guarantees only the catalog rows + scopes it consumes. | P4 (see risk §7). |
| **GitHub connector** (third popover row) | Existing entry; write scopes tracked separately (README §7.6). | Out of P6. |
| **Hosted trial lane** | Shelved product-wide (README §7.1). | Deferred. |

---

## 4. End-to-end user flows

Both connectors follow the same product spine — **connect → authorize → use in a run → review → commit** — with the commit step being the load-bearing difference (sign a tx vs approve a write).

### 4.1 Safe{Wallet}: propose a treasury payout

```
CONNECT   User opens the Tools popover (State B composer) → taps "Safe{Wallet}"
          → 1-click connect → picks the Safe address(es) + chain to bind
          → row shows "connected". (First-use consent = mcp_auth card.)

USE       User: "Prepare a 250 USDC payout to treasury.eth from the ops Safe."
          Agent (read, session-approved once): reads owners / threshold / nonce.
          Agent (propose, no side effect): builds calldata + EIP-712 + runs simulation.

REVIEW    A consent card appears. Its To / Amount / Token / Chain / Safe / Nonce rows
          are built from SERVER-DECODED calldata + the simulation asset-diff — not
          from anything the agent wrote. The agent's sentence sits in a labeled
          "assistant note" zone only. If simulation produced no positive asset-diff,
          the "Review & sign in wallet" button is DISABLED.

COMMIT    User taps "Review & sign in wallet" → the facade re-checks bound-Safe +
          chain on server-derived identity → the user's own wallet pops up →
          user confirms eth_signTypedData_v4 (2nd explicit human action).
          The signature goes renderer → facade → Safe tx-service; it NEVER enters
          agent/graph/worker/approval-decision state.

RESULT    Agent resumes with a status string only: "Signed — 1 of 2 confirmations
          added to the multisig queue." It never executes, never broadcasts.
```

Two human actions are mandatory and neither can be triggered by model output alone: approving the card **and** confirming in the wallet. Ceiling: the multisig queue (`PRD-P6a-hardened.md` residual Q7).

### 4.2 Google Sheets: read a tracker, write back the numbers

```
CONNECT   Tools popover → "Google Sheets" → 1-click → system-browser Google OAuth
          (write scope: spreadsheets) → row shows "connected".
          If no operator OAuth client is configured, the row degrades gracefully to
          a "needs setup" card (409 connector_oauth_setup_required) — never a crash.

USE(READ) User: "Summarize the top movers in my launch tracker."
          Agent (read, session): get_values / batch_get_values → returns cell data.
          A read consent card appears the first time (category=read).

USE(WRITE)User: "Fill column D with the computed totals and append this week's row."
          Agent proposes update_values / write_append_values.

REVIEW    A WRITE consent card appears per call (category=write, read_only=false),
          showing the target range + values. No cell is touched yet.

COMMIT    User approves → the tool executes and the sheet mutates.
          User rejects → nothing changes.
```

Per-chat control: the user can pause Sheets for a single conversation; both card visibility and any tool call are then denied (defense-in-depth re-check, `call_tool.py:81-83`).

### 4.3 What's shared (both flows)

- **Entry point:** the FTUE Tools popover row (`design-source/SPEC.md:35`), 1-click, "connected" on select, group note "1-click connect · you approve first use."
- **Approval surface:** the shipped `ApprovalCard` / consent-card family and the `/v1/agent/approvals/{id}/decision` path (facade `app.py:1114`). No second approval component.
- **Per-chat scope:** `PATCH /v1/agent/conversations/{id}/connectors` (facade `app.py:483`).
- **Secrets:** OAuth tokens and any client secret live only in the backend `TokenVault`; nothing connector-secret is ever committed.

---

## 5. Success metrics and acceptance

### 5.1 Product success metrics

**Activation (does anyone connect?)**
- **Connector attach rate:** % of FTUE completers who connect Safe and/or Sheets within their first session. *Target: ≥ 20% attach at least one of the two in week 1 of GA.*
- **Connect success rate:** completed connects ÷ connect attempts (a "needs setup" degrade counts as a non-crash, but not a success). *Target: ≥ 90% where an operator client is configured.*

**Engagement (does it get used in a real run?)**
- **Run-with-connector rate:** % of connected users who invoke a connector tool in a run within 7 days. *Target: ≥ 60%.*
- **Propose→sign / write-approve completion:** of runs that raise a Safe sign-gate or a Sheets write card, % the human approves (vs abandons). *Watch, not target — a high abandon rate signals unclear review UI, not a failure.*

**Safety (the metric that can't regress) — hard gates, alarm on any nonzero**
- **Unauthorized-signature count = 0.** No signature is ever produced without the two human actions. Enforced by test, not measured in prod.
- **Unauthorized-write count = 0.** No Sheets cell mutates without an explicit approve.
- **Bound-Safe violations = 0.** No signature against an unbound Safe or off-allowlist chain reaches the tx-service.
- **Secret-leak incidents = 0.** No signature, bearer, OAuth token, or provider key in any URL, log, history, or agent-visible field.

**Trust / friction**
- **Simulation coverage:** % of Safe sign-gates that render a positive decoded asset-diff (higher ⇒ fewer "unverified" blind-signs). *Track by simulation provider — see §6.1.*
- **Approval-fatigue signal:** median approvals per run + repeated-destination flags. Rising fatigue is a UX debt indicator (`PRD-P6a-hardened.md` M3).

### 5.2 Acceptance criteria (product-level, ship gates)

1. **Both connectors appear and connect** from the FTUE Tools popover and Settings, 1-click, with SPEC-parity copy ("propose & sign transactions" / "read & write workbooks"), sky-only accent, jade "connected" (`design-source/SPEC.md:35`, README §2).
2. **Safe read + propose work** and **propose has zero side effects** (no queue write, no signature, no broadcast); read/propose are session-approved.
3. **Every Safe signature requires two explicit human actions** (approve the card + confirm in the wallet); no code path lets the agent, a tool result, or the worker produce or submit a signature. The agent's ceiling is "added to the multisig queue."
4. **The Safe consent card shows the decoded, simulated effect of the calldata** — not agent-authored display fields; a benign card over malicious `data` is impossible; no positive asset-diff ⇒ sign disabled (`PRD-P6a-hardened.md` controls A/B).
5. **Safe target is server-enforced** to a user-bound Safe on a signing-allowlisted chain, checked at the facade on server-derived identity (`PRD-P6a-hardened.md` control C); DELEGATECALL is hard-blocked except an audited-library allowlist.
6. **Sheets read returns cell data; every Sheets write pauses on a per-call write approval** and mutates only after explicit approve; a rejected write changes nothing.
7. **Sheets fails closed when unconfigured**: absent an operator OAuth client, connect returns `connector_oauth_setup_required` (409) — no 500, no secret leak (`PRD-P6b.md` §7.3).
8. **Per-chat pause** blocks both connectors' card visibility and any tool call for that conversation.
9. **No committed secret, endpoint client-id, or token anywhere**; both connectors' changes are path-filtered; typecheck/tests green across backend, facade, ai-backend, api-types, chat-surface.

---

## 6. The three gating decisions (product tradeoffs + recommended default)

These are the product calls that block build. Each is framed as a tradeoff with a recommended default; each needs explicit sign-off (`PRD-P6a-hardened.md` residual Qs 2/4/5; `PRD-P6b.md` open questions).

### 6.1 Decision A — Transaction-simulation provider (Safe)

**Why it's a product decision, not just plumbing.** In the hardened design, simulation is **safety-load-bearing**: the sign button is disabled unless a positive, decoded asset-diff exists (`PRD-P6a-hardened.md` control B, H1/H7). So "which simulator" directly sets how often a user can sign confidently vs is forced to blind-sign an "unverified" tx — that's a trust-and-conversion lever, not an infra detail.

| Option | Product upside | Product cost |
| ------ | -------------- | ------------ |
| **Self-hosted `eth_call` / anvil-fork** | No third-party key, cost, or data-sharing; fully local-first (matches the product's privacy posture); no per-tx external dependency. | Lower asset-diff fidelity for exotic tokens/protocols; more txs land as "unverified" ⇒ more friction or blind-signs; we own the RPC reliability. |
| **Tenderly (or similar hosted simulator)** | Richest asset-diff, best decoding of complex DeFi calls ⇒ highest "verified" coverage ⇒ smoothest sign UX. | External key + cost + a data-sharing dependency that cuts against local-first; a provider outage becomes a signing outage (fail-closed ⇒ users blocked). |

**Recommended default: self-hosted `eth_call`/anvil-fork as the shipped baseline, with a Tenderly-style provider as an operator-configurable upgrade.** Rationale: local-first is a core product promise ("nothing leaves this machine" is the FTUE's headline copy, `design-source/SPEC.md:22`), and the safety guarantee is *fail-closed* either way — when simulation can't produce a positive diff, we block, we don't wave it through. Ship honest coverage on our own RPC; let deployments that need deeper DeFi decoding opt into a hosted simulator. Track "simulation coverage" (§5.1) per provider to decide if/when hosted becomes the default.

### 6.2 Decision B — Bound-Safe store + chain allowlist (Safe)

**Why it's a product decision.** Two coupled questions: (1) *where* do we persist the specific Safe(s) a user authorized at connect, and (2) *which chains* may a value-signature target. Both define how tightly the agent is boxed in — the difference between "0xCopilot can prepare a tx for *my* treasury Safe on chains I chose" and "the agent named an address and we signed." The bound-Safe check must run **server-side at the facade**, because the sign gate is a native builtin that bypasses the MCP auth re-check and `paused_connectors` (`PRD-P6a-hardened.md` control C, H4).

*Sub-decision B1 — store location*

| Option | Product upside | Product cost |
| ------ | -------------- | ------------ |
| Reuse the MCP server-record metadata | No new store; rides existing connector install/uninstall lifecycle. | Overloads a general record with a security-critical constraint; coarse per-user semantics. |
| **New `safe_bindings` store (per-user)** | Purpose-built, explicit, auditable; clean "which Safes did this user authorize, when" record; easy to show/manage in Settings. | One small new store + migration. |
| Per-conversation connector scope | Fine-grained per chat. | Wrong lifetime — a Safe binding is a durable user trust decision, not a per-chat toggle. |

*Sub-decision B2 — signing chain allowlist*

The SIWE **login** allowlist is `(1, 8453, 42161, 4663)` = Ethereum, Base, Arbitrum One, Robinhood Chain (`identity/siwe.py:85`). Reusing it for value-signing conflates "chains you may log in from" with "chains where the agent may help move funds" (`PRD-P6a-hardened.md` M1).

**Recommended default (B):** a dedicated **per-user `safe_bindings` store** capturing `(safe_address, chain_id)` at connect, enforced server-side at the facade `/simulate` and `/transactions` endpoints on server-derived identity; **plus a separate `SAFE_SIGNING_ALLOWED_CHAIN_IDS`**, distinct from the SIWE login list, defaulting to a conservative subset (recommend the mainnets the treasury persona actually uses — Ethereum + Base + Arbitrum — and requiring an explicit opt-in to add others). Rationale: treasury-signing posture should be able to diverge from login posture, and an explicit, user-visible binding is exactly the "you're in control of what it can touch" story the FTUE promises. Storage is open (residual Q2); **enforcement location is fixed at the facade** and is not up for debate.

### 6.3 Decision C — Google OAuth client owner (Sheets)

**Why it's a product decision.** The `spreadsheets` (write) scope needs a Google OAuth client (`client_id`/`client_secret`). *Who owns that client* decides whether the FTUE's "1-click connect" is literally one click, and whose name is on the Google consent screen the user sees — a trust and brand moment, and a verification/liability question, because Sheets write is a sensitive scope (`PRD-P6b.md` open questions).

| Option | Product upside | Product cost |
| ------ | -------------- | ------------ |
| **Per-deployment operator secret** (self-host / enterprise injects its own client at install) | Clean product/operator boundary; each deployment owns its consent screen + Google verification; no shared-client blast radius; matches self-host posture (`deploy/self-host/`). | Not literally 1-click in a fresh install until the operator does setup; absent a client the FTUE row degrades to "needs setup" (409, graceful — `PRD-P6b.md` §4.3). |
| Shipped 0xCopilot-owned client | Genuinely 1-click out of the box; consistent branded consent screen. | 0xCopilot must complete Google's sensitive-scope verification and own the liability for every deployment's Sheets access through one client; a single revocation/quota issue hits everyone; awkward for self-host where the vendor isn't the operator. |

**Recommended default: per-deployment operator secret, injected at install into the MCP record's `oauth_client` (encrypted via `TokenVault`), with the FTUE row degrading honestly to "connect in Settings / needs setup" when it's absent.** Rationale: the product is local-first and self-hostable (README, `services/backend/CLAUDE.md`); a per-deployment client keeps consent, verification, and liability where they belong and avoids a shared-client single point of failure. The cost — not-quite-1-click on a bare install — is mitigated by the already-designed graceful "needs setup" degrade, so the popover button is never a dead end. Ship Sheets as **`release_stage: preview`** initially (requires `DESKTOP_CONNECTORS_ALLOW_PREVIEW=true`), and promote to `stable` only once a verified endpoint + a shipped/operator client exist (`PRD-P6b.md` §8).

---

## 7. Rollout and risk

### 7.1 Sequencing

- **P4 is a hard prerequisite.** The Tools-popover component that hosts these rows is net-new in P4; the shipped `ToolPicker` has no connector / 1-click / per-chat-scope plumbing (`completeness-critic.md`). P6 registers the connectors in the backend; without P4 they have no FTUE entry point. **Do not schedule P6 GA before P4 lands.**
- **Safe and Sheets can build in parallel** on their catalog/profile/UI parts, but their ai-backend runtime edits touch the same files (`stream_events.py` classifier + approval projection) and must be serialized or coordinated (`completeness-critic.md` sequencing).
- **Ship Sheets first, Safe second.** Sheets is config-plus-one-scope-path and reuses the shipped write-approval machinery. Safe is the highest-risk net-new surface (new sign gate, decoding, simulation, bound-Safe enforcement, dual-host signing) and is **BUILD-GATED behind security sign-off** (`PRD-P6a-hardened.md`: 1 critical + 8 high findings must be closed).

### 7.2 Risks

- **Real funds (Safe) — the defining risk.** Mitigation is the whole design: propose-only, no server key, two human actions, decoded-calldata authority, fail-closed simulation, server-side bound-Safe + chain enforcement, DELEGATECALL block, non-forwardable + reversible=no approvals, execution ceiling at the multisig queue. **None of these are optional; each maps to a security finding** (`PRD-P6a-hardened.md` C1/H1–H8/M1–M11). If any is descoped, Safe does not ship.
- **Approval fatigue.** Unbounded per-call sign requests + persuasive agent titles erode the value of the gate. Mitigation: per-run cap + cooldown, cumulative-value-signed counter, repeated-destination flags, titles composed from decoded fields (`PRD-P6a-hardened.md` M3). Watch the fatigue metric (§5.1).
- **Sheets write-misclassification.** The runtime marks a tool as a write only if its name contains a write-term; **`append` is not in that set** (`stream_events.py:910-919`), so a naive `append_values` would render a *read* card while mutating the sheet. Mitigation: name write tools with recognized terms (`write_append_values`, `update_values`, `clear_values`, `create_spreadsheet`, `batch_update_spreadsheet`), per `PRD-P6b.md` §3.2. Durable fix (extend the classifier) is a separate shared-runtime change.
- **Sheets endpoint reality.** No confirmed official Google Sheets MCP with cell writes may exist at ship time; the profile endpoint is a placeholder (`PRD-P6b.md` §8). Mitigation: `preview` + fail-closed loader ⇒ the row stays honestly "needs setup" rather than pretending to work. A self-hosted Sheets server is a separate service and out of P6.
- **FTUE promise vs availability.** If a connector is offered as 1-click in the default build but requires operator setup, the row must degrade to "needs setup," never a dead button (`PRD-P6b.md` §8). This is a UX-honesty gate, tracked in acceptance §5.2.7.

### 7.3 Launch posture

Both connectors ship **behind the preview gate first** (`requires_preview_gate: true`), validated on the live desktop stack with a fake MCP endpoint / fake tx-service (hermetic real-graph run→stream, README §9). Safe additionally requires the security-review findings closed and the `PRD-P6a-hardened.md` CI invariants under test (no server-side wallet key ever; sweeper stays rejection-only) before it leaves preview. Promote to `stable` per-connector only when a verified endpoint + operator client (Sheets) or full security sign-off (Safe) exist.

---

## Open questions

- Decision A (simulation provider): confirm self-hosted eth_call/anvil-fork as the shipped baseline with an optional hosted simulator, and pick the fail-closed UX when simulation is unavailable (PRD-P6a-hardened.md residual Q4).
- Decision B (bound-Safe + chains): approve a per-user safe_bindings store and a separate SAFE_SIGNING_ALLOWED_CHAIN_IDS distinct from the SIWE login list (default 1,8453,42161,4663), plus its default membership (PRD-P6a-hardened.md residual Q2/Q5, M1).
- Decision C (Google OAuth client owner): approve per-deployment operator secret with a graceful 'needs setup' degrade over a shipped 0xCopilot-owned client; confirm Sheets ships as preview until a verified endpoint + client exist (PRD-P6b.md open questions).
- Safe execution ceiling: confirm v1 stops at 'signature added to the multisig queue' and on-chain execution is a separate later gated action, not P6 (PRD-P6a-hardened.md residual Q7).
- DELEGATECALL policy: block operation==1 entirely in v1, or permit a hard-coded audited-library allowlist (e.g. MultiSendCallOnly) with high-friction confirm (PRD-P6a-hardened.md H2/H8, residual Q6).
- Sheets endpoint: is there an official Google Sheets MCP with cell-write tools to pin, or do we accept the preview/needs-setup posture until a self-hosted server (a separate deployable service, out of P6) is decided (PRD-P6b.md §8)?
- P4 dependency: is the connector-aware Tools popover scheduled as a real phase before P6 GA, given the shipped ToolPicker has no 1-click/connector/scope plumbing (completeness-critic.md)?
- Sheets classifier: name write tools with recognized write-terms (this PRD's mitigation) or extend _connector_action_is_read_only to include append/insert/set/clear (a shared ai-backend change)?


# P6 — Safe{Wallet} + Google Sheets connector UX

**Owner:** Product design · **Phase:** P6 (Safe + Sheets connectors) · **Status:** design, implementation-ready · **Branch:** `claude/0xcopilot-first-run-onboarding-d7eb30`

Read alongside `docs/plan/first-run-onboarding/design-source/SPEC.md` (Tools popover copy + "1-click connect · you approve first use"), `docs/plan/first-run-onboarding/phases/PRD-P6a-hardened.md` (the load-bearing signing controls), `PRD-P6a.md`, and `PRD-P6b.md`. Every surface below **reuses** existing chat-surface components — no bespoke card is authored. All paths are relative to ROOT.

> **The one visual law of this phase.** The Safe signing card shows the **decoded effect of the calldata + the simulated asset-diff**, never the agent's sentence. `PRD-P6a-hardened.md:9` (control A) makes "a benign-looking card for malicious `data`" impossible; `:17` (C1) says the flat 6-param model is replaced with a typed `SafeDecodedEffect` block. The agent's words are quarantined into a labeled, non-authoritative zone (`PRD-P6a-hardened.md:23`, H6). Design serves that law: **decoded truth is loud; agent claims are quiet; the sign button is dark until a positive simulation exists.**

---

## 0. Components reused (nothing net-new in the card family)

| Surface | Reused component | Source |
| --- | --- | --- |
| Tools popover (connect) | `ToolsPopover` | `packages/chat-surface/src/onboarding/ToolsPopover.tsx` |
| Connect wiring | `FirstRunSurface.handleConnectCatalog` → `FirstRunConnectorsPort` | `packages/chat-surface/src/onboarding/FirstRunSurface.tsx:276-299`; `ports/FirstRunConnectorsPort.ts:30-55` |
| Signing / write consent | `ApprovalCard` (4-zone frame) | `packages/chat-surface/src/approvals/ApprovalCard.tsx:46-113` |
| Decoded rows + sim diff | `ActivityParams` / `ActivityParam` | `packages/chat-surface/src/approvals/ActivityParams.tsx`; `approvals/types.ts:12-17` |
| Card CSS (`.atlas-approval-card__*`) | existing block | `apps/frontend/src/styles.css:3261-3396` |
| Connect-time consent | `ConnectorConsentCard` pattern | `apps/frontend/src/features/connectors/ConnectorConsentCard.tsx` |
| Tokens | v2 "quiet" set | `packages/design-system/src/styles.css:34-333` |

The Safe card is `ApprovalCard` with two **new slotted sub-blocks** fed into its existing `params`/`result` slots (`ApprovalCard.tsx:89-97`): a `SafeDecodedEffect` block (into a dedicated decoded zone) and a `SafeSimDiff` block (into `result`). The action node (`ApprovalCard.tsx:99`) is `SafeSignAction`. No new card shell.

---

## 1. Connect flow — inside the Tools popover

### 1.1 Where the rows live

Safe and Sheets are two rows in the **"Add a connector"** section of `ToolsPopover` (`ToolsPopover.tsx:294-327`), under the group note `1-click connect · you approve first use` (`TOOLS_POPOVER_COPY.installableNote`, `ToolsPopover.tsx:47`). Row anatomy is already built: label from `displayName`, hint from `description`, and a trailing pill that reads **`Connect`** (1-click) or **`Set up`** (`requiresPreRegisteredClient`) — `ToolsPopover.tsx:305-324`.

```
┌─ Tools ───────────────────  2 on · none required   × ─┐
│  ● Web search                              built-in  [●]│
│  ── Connected ────────────────────────────────────────│
│  ● Google Sheets   read · write                    [●]│   ← after connect (jade toggle)
│  ── Add a connector ──────────────────────────────────│
│  1-click connect · you approve first use              │
│  ◈ Safe{Wallet}    propose & sign transactions [Connect]│
│  ◈ Google Sheets   read & write workbooks       [Set up]│   ← requires_pre_registered_client
│  ◈ GitHub          repos, issues, PRs          [Connect]│
│  ── ─────────────────────────────────────────────────│
│  + Custom MCP server   paste a JSON config          [+]│
└───────────────────────────────────────────────────────┘
```

Copy per `SPEC.md:35`: `Safe{Wallet}` / "propose & sign transactions", `Google Sheets` / "read & write workbooks". The leading glyph (`◈`) may carry a **tiny inline brand swatch** — Safe green `#12FF80`, Google multicolor — as *data*, never the app accent (`PRD-P6a.md:222`, `SPEC.md:33`, one-accent discipline). The trailing `Connect`/`Set up` pill stays sky (`connectPillStyle`, `ToolsPopover.tsx:495-502`).

### 1.2 The three connect paths (which pill, which flow)

The pill the row shows is decided by the catalog entry's `requiresPreRegisteredClient` flag (`ToolsPopover.tsx:320-322`). Clicking routes through `FirstRunSurface.handleConnectCatalog` (`FirstRunSurface.tsx:276-299`):

| Path | Trigger | Pill | What the user sees |
| --- | --- | --- | --- |
| **A. 1-click OAuth redirect** | catalog entry, no pre-registered client (e.g. GitHub, and Safe if `auth_mode: NONE`) | `Connect` | `installFromCatalog(slug)` → `beginAuth(serverId)` (`FirstRunSurface.tsx:289-291`). Host decides the redirect: **web** full-page-redirects to the vendor consent screen (`FirstRunConnectorsPort.ts:15-18`); **desktop** opens the vendor page in the **external system browser** (§1.4). |
| **B. Pre-registered-client setup form** | entry with `requiresPreRegisteredClient` (Google Sheets — `PRD-P6b.md:82`; keyless install would 422) | `Set up` | Routes to `onAddCustom()` (`FirstRunSurface.tsx:285-287`) → the host's custom-config / setup form (`ChatScreen.onMcpInstallCatalog` opens the `McpOverlay` SetupModal, `ChatScreen.tsx:1348-1354`). User pastes the operator `client_id`/`client_secret`, then the OAuth redirect (path A) runs. |
| **C. Safe binding picker** | Safe first connect (CONNECT boundary, `PRD-P6a.md:19`) | `Connect` | After install, the CONNECT step **captures the bound Safe address(es) + chain** (`PRD-P6a-hardened.md:21`, H4; `PRD-P6a.md:176`). This is a Safe-address picker, not (necessarily) an OAuth grant — see the CONNECT card in §1.3. |

**Failure recovery is already designed:** if a 1-click install throws `OAuthSetupRequiredError`, the host falls back to the setup overlay (`ChatScreen.tsx:1379-1381`) — a graceful "needs setup" card, not a dead button. In the FTUE the popover's connect is *workspace-authorize only*; a swallowed failure keeps the composer unblocked and surfaces later as the run-time `mcp_auth_required` card (`FirstRunSurface.tsx:292-296`, `FirstRunConnectorsPort.ts:20-23`).

### 1.3 The CONNECT consent card (bind-the-Safe step)

Safe's CONNECT boundary is distinct from GitHub's OAuth: the user must name **which Safe(s)** the agent may touch (`PRD-P6a-hardened.md:21`; the agent can never target an unbound Safe). This reuses the `ConnectorConsentCard` / `mcp_auth` pattern (`PRD-P6a.md:19`) with a Safe-address field:

```
┌─ Connect Safe{Wallet} ────────────────────────────────┐
│  Which Safe should Copilot watch?                      │
│  Copilot can read this Safe and build transactions.    │
│  It can never move funds without your signature.       │
│  ┌──────────────────────────────────────────────────┐ │
│  │ Safe address   0x7f3C…a92C                        │ │
│  │ Chain          Ethereum ▾   (allowlisted only)    │ │
│  └──────────────────────────────────────────────────┘ │
│  [ + Add another Safe ]                                │
│                                                        │
│  [ Bind this Safe ]            [ Cancel ]              │
│  🛡 Only Safes you bind here are visible to Copilot.   │
└────────────────────────────────────────────────────────┘
```

- **Chain field** is constrained to `SAFE_SIGNING_ALLOWED_CHAIN_IDS` — a *separate* list from the SIWE login allowlist (`PRD-P6a-hardened.md:24`, M1). Chains outside it are not selectable.
- Bound Safes persist server-side (bound-Safe store, `PRD-P6a-hardened.md:21`). This is the value the facade enforces `safe ∈ bound_safes` against at simulate/submit (`PRD-P6a-hardened.md:11`).

### 1.4 Desktop external-browser handoff

On desktop, both the OAuth redirect (path A) and the later signing popup run in the **external system browser** via the main-process loopback, never inside the Electron renderer:

- OAuth: the desktop connector coordinator binds an ephemeral loopback, opens the system browser, and races the loopback `?code&state` against a deep-link delivery (`apps/desktop/main/connectors/oauth-coordinator.ts:10-22`, mirroring `main/auth/loopback-server.ts` + `wallet-login.ts`).
- Signing: the same loopback shape serves a facade-hosted `safe-sign.html` (`PRD-P6a.md:84-87`). **Hardened constraint (H5, `PRD-P6a-hardened.md:22`):** the signature and bearer must **never** transit a redirect URL query — the page POSTs the signature back to the loopback body (or a one-time scoped token → POST to facade). `safe-sign.html` accepts only an opaque `proposal_ref` + loopback state and FETCHes the canonical EIP-712 from the facade; it refuses non-loopback targets (`PRD-P6a-hardened.md:34`).

UX consequence: on desktop the connect/sign click shows a **"Continue in your browser →"** micro-state on the row/button while focus moves to the system browser, then returns to `connected` / the resolved card on callback. Copy: `Opening your browser…` → (on return) `Connected` or the signed receipt.

---

## 2. Safe signing consent card (the SIGN gate)

This is the security-defining surface. It renders when the agent calls `request_wallet_signature` and the worker projects `approval_requested(kind=wallet_signature)` (`PRD-P6a.md:180-184`). It is `ApprovalCard` (`ApprovalCard.tsx:46-113`) with `category={vendor:"SAFE", access:"ACTION"}` and four stacked zones the eye scans top-to-bottom.

### 2.1 Full layout

```
┌─ 🛡 Sign a Safe transaction ───────────────  SAFE · ACTION ┐   ① header
│    Decoded from the calldata — not from Copilot.           │
├────────────────────────────────────────────────────────────┤
│  DECODED EFFECT                              ✓ server-verified│   ② decoded truth
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Method      transfer(address,uint256)         ERC-20   │ │
│  │ Recipient   0x1f3C…a92C   treasury.eth                 │ │
│  │ Asset       USDC   0xA0b8…eB48                         │ │
│  │ Amount      250.00 USDC                                 │ │
│  │ Operation   CALL (0)                                    │ │
│  │ Safe        0x7f3C…a92C   ·  Ethereum (1)              │ │
│  │ Nonce       42                                          │ │
│  └────────────────────────────────────────────────────────┘ │
├────────────────────────────────────────────────────────────┤
│  SIMULATED BALANCE CHANGE            required to enable sign  │   ③ mandatory sim
│    −  250.00 USDC        Safe 0x7f3C…a92C                    │   (ember − / jade +)
│    +  250.00 USDC        treasury.eth                        │
│    ✓ simulation succeeded · est. gas 0.0021 ETH             │
├────────────────────────────────────────────────────────────┤
│  ▸ COPILOT CLAIMS — for context, not verified                │   ④ agent-claims (quiet)
│    "Send 250 USDC to the treasury for the Q3 grant."         │
├────────────────────────────────────────────────────────────┤
│  You approve here, then confirm again in your wallet.        │   actions
│  Adds 1 of 2 required signatures. · 3rd signature this run.  │
│  [ Review & sign in wallet ]        [ Reject ]              │
├────────────────────────────────────────────────────────────┤
│  🛡 Copilot proposes. You sign in your own wallet — it never │   footer reassurance
│     holds your keys.                                        │
└────────────────────────────────────────────────────────────┘
```

### 2.2 Zone ② — Decoded effect (the authority)

Every row here is derived server-side from ABI-decoding `data` + the simulation, **not** from any agent-supplied display field (`PRD-P6a-hardened.md:9,17`). Rendered as `ActivityParam[]` in the inset frame (`.atlas-approval-card__params`, `styles.css:3332-3365`; labels are mono uppercase, values are `--color-text`).

| Row | Source | Notes |
| --- | --- | --- |
| **Method** | decoded selector | e.g. `transfer(address,uint256)`; a swatch pill `ERC-20` / `native` |
| **Recipient** | decoded `to` argument (for ERC-20 the *real* recipient lives in `data`, not the tx `to`) | truncated `0x1f3C…a92C`; ENS if resolvable. `PRD-P6a-hardened.md:17` — the recipient is inside `data`; the flat 6-param model had no row for it. |
| **Asset** | token contract | symbol + truncated address; `native` when empty `data` |
| **Amount** | decoded `uint256`, formatted by token decimals | never the tx `value` for ERC-20 transfers |
| **Operation** | `operation` field | `CALL (0)` normal; `DELEGATECALL (1)` renders in ember + gates (§2.4) |
| **Safe** | bound-Safe context | address + chain name + id |
| **Nonce** | read-back nonce | re-checked at submit for drift (`PRD-P6a.md:283`) |

A small **`✓ server-verified`** eyebrow (jade) sits at the zone's top-right when the decode + simulation both succeeded. If decode fails, the zone is replaced by the undecodable error state (§2.5) and signing is blocked.

### 2.3 Zone ③ — Mandatory simulation asset-diff (the gate fuel)

Fed to `ApprovalCard`'s `result` slot (`ApprovalCard.tsx:95-97`, `.atlas-approval-card__result`, `styles.css:3367-3374`). This is **not decorative** — its presence is what enables the sign button (`PRD-P6a-hardened.md:10,18`).

- Outflows: `−` prefix, **ember** (`--color-danger`).
- Inflows: `+` prefix, **jade** (`--color-success`).
- Each row: `± amount · symbol · account (truncated)`.
- Footer line: `✓ simulation succeeded · est. gas …` (jade check) when positive.
- **No positive diff → the zone shows the block reason and the sign button is disabled** (§2.5). The wallet popup never opens without it (`PRD-P6a-hardened.md:10`).
- **Single exception:** a bare native-value transfer with **empty `data` + `operation==0`** may sign with an amber `⚠ unverified — no contract call to simulate` note in place of the diff (`PRD-P6a-hardened.md:10,18`). The button is enabled but the amber marker is mandatory; this is the only "sign without a green diff" path.

### 2.4 Zone ④ — "Copilot claims" (quarantined, collapsed by default)

The agent's `summary` sentence is the **only** agent-authored text on the card and it lives here, under a labeled, muted, collapsed header `▸ COPILOT CLAIMS — for context, not verified` (`PRD-P6a-hardened.md:23`, H6; `PRD-P6a.md:270` AC-4). Visual treatment: `--color-text-subtle`, `--font-size-xs`, no border emphasis, sits *below* the decoded + simulated zones so it never leads the eye. If the agent's claim **contradicts** the decoded effect (e.g. claims "treasury.eth" but decode shows a different recipient), the card raises a mismatch banner and **blocks signing** (`PRD-P6a-hardened.md:23`; injection test asserts `decoded == simulated`, `:20`).

> Note: `safeTxHash` re-derivation is **not** presented as a safety guarantee anywhere in the UI. Per `PRD-P6a-hardened.md:20` (H3) it is demoted to an integrity/transport check; a mismatch surfaces as a generic "couldn't verify this transaction — can't sign" block, not as a headline security feature.

### 2.5 Sign-button state matrix

The primary action is **`Review & sign in wallet`** — accent-filled, sky only (`gbtn--pri` → `--color-accent` bg / `--color-accent-contrast` text, `PRD-P6a.md:222`). It is **enabled only on a positive, renderer-rendered simulation** (fail-closed, `PRD-P6a-hardened.md:10,18`). Disabled reasons render as an inline note directly above the button.

| # | Condition | Button | Inline note (copy) | Zone treatment |
| --- | --- | --- | --- | --- |
| 1 | Decoded + positive sim + chain match + bound owner + `operation==0` | **enabled** (accent) | `You'll confirm again in your wallet.` | ✓ verified (jade) |
| 2 | Empty `data` + `operation==0` (bare native transfer) | **enabled** (accent) | `⚠ Unverified — no contract call to simulate.` | amber marker replaces diff |
| 3 | Calldata undecodable / unsimulatable | **disabled** | `Can't decode this transaction — signing is blocked.` | zone ② → ember error |
| 4 | Simulation unavailable / provider down | **disabled** | `Simulation unavailable — can't verify the effect. Try again.` + `Retry simulation` ghost | zone ③ → amber |
| 5 | Simulation **reverts** | **disabled** | `This transaction reverts in simulation.` + revert reason | zone ③ → ember, revert token |
| 6 | `operation==1` DELEGATECALL, not allowlisted | **blocked** (hard) | `Delegatecall is blocked — it can run arbitrary code in your Safe.` | zone ② Operation row = ember |
| 6a | `operation==1`, audited-library allowlist (e.g. MultiSendCallOnly) | **enabled behind distinct high-friction confirm** | `This uses delegatecall via <library>. Type CONFIRM to proceed.` | ember header + typed confirm |
| 7 | Chain mismatch across wallet / EIP-712 domain / validated / displayed | **disabled** | `Network mismatch — your wallet is on the wrong chain.` | Safe row = ember |
| 8 | Chain not in `SAFE_SIGNING_ALLOWED_CHAIN_IDS` | **disabled** | `This network isn't allowed for signing.` | — |
| 9 | Signer not a Safe owner | **disabled** | `The connected wallet isn't an owner of this Safe.` | — |
| 10 | Safe not in bound set | **blocked** (hard) | `This Safe isn't one you connected — signing is blocked.` | — |
| 11 | Decoded ≠ simulated, or agent claim contradicts decode | **blocked** | `The transaction doesn't match what was described — signing is blocked.` | mismatch banner (ember) |
| 12 | Per-run signature cap reached / cooldown active | **disabled (temporary)** | `You've reached the signing limit for this run. Cooling down…` | countdown |

The disabled button uses the design-system disabled treatment (`opacity: 0.48; cursor: not-allowed`, `styles.css:396-400`) — never a re-colored "danger button". **Reject** is the ghost/text-danger secondary (`ui-button--danger`, `styles.css:451-459`) and is always available.

### 2.6 The two human confirmations (both mandatory)

Per `PRD-P6a.md:268` (AC-2) and `PRD-P6a-hardened.md:33` (M11), every signature needs **two** explicit acts, neither triggerable by model output:

1. **Card approve** — click `Review & sign in wallet`. This mints a **single-use challenge** valid only while `(approval_id, safe_tx_hash)` is PENDING (`PRD-P6a-hardened.md:33`); the card is a real precondition, not decoration.
2. **In-wallet confirm** — the EIP-1193 `eth_signTypedData_v4` popup in the user's own wallet (web in-page via `walletProof`/`eip6963`; desktop via the external-browser loopback page). The user confirms *again* there.

**Signing progress micro-states** (button label swaps in place):

```
[ Review & sign in wallet ]                          idle / enabled
[ ⟳ Waiting for your wallet… ]      (accent spinner)  challenge minted, popup open
[ ⟳ Submitting signature… ]                           EIP-712 signed, POST to facade
✓ Signed — added 1 of 2 confirmations                 resolved (jade receipt)
✗ Rejected in wallet                                  4001 → quiet rejected
```

On `signed`, the card collapses to the one-line `atlas-approval-receipt` (`styles.css:3398+`) reading `Signed · 1 of 2 confirmations · 250 USDC → treasury.eth`. **The raw signature never appears** in the card, the decision body, or any URL (`PRD-P6a.md:267`, `PRD-P6a-hardened.md:22`).

### 2.7 Per-call, never "approve all"

Each `request_wallet_signature` is its own card with a unique `approval_id` (`PRD-P6a.md:286`). There is **no batch approve**. The actions row carries a cumulative-value counter (`3rd signature this run · $X signed so far`) and flags repeated destinations (`PRD-P6a-hardened.md:26`, M3) with a quiet amber `↻ same recipient as an earlier signature` note.

### 2.8 Footer reassurance (persistent)

Always rendered in `.atlas-approval-card__foot` with the shield glyph (`ApprovalCard.tsx:101-106`): **`Copilot proposes. You sign in your own wallet — it never holds your keys.`** (`PRD-P6a.md:223`). Because `reversible=NO` (`PRD-P6a-hardened.md:25`), **no undo-countdown chip** renders (contrast the 60s window for reversible writes, `PRD-P6a.md:224`).

---

## 3. Google Sheets consent

Sheets rides the existing MCP consent path — no bespoke card (`PRD-P6b.md:238`). The read/write split is a **category** difference the user reads at a glance.

### 3.1 Read = session approval (asked once)

First read of a workbook raises a **read-category** `ConnectorConsentCard` (`ApprovalCard` with `category={vendor:"GOOGLE SHEETS", access:"READ"}`). Because reads are `approval: session` (`PRD-P6b.md:96-120`), approving once clears reads for the rest of the session.

```
┌─ 🛡 Read your Google Sheets? ──────────────  SHEETS · READ ┐
│    Copilot needs to read this workbook to answer.          │
│  ┌────────────────────────────────────────────────────┐   │
│  │ Workbook   Q3 Airdrop Claims                        │   │
│  │ Range      Claims!A1:F1200                          │   │
│  └────────────────────────────────────────────────────┘   │
│  [ Allow reads this session ]      [ Not now ]            │
│  🛡 You're always asked before Copilot writes to a sheet. │
└────────────────────────────────────────────────────────────┘
```

> Honest caveat (`PRD-P6b.md:283`): the runtime currently interrupts on *every* `call_mcp_tool`, so reads may still prompt as read-category cards rather than being silently session-scoped. Copy therefore says "Allow reads this session" (aspirational + safe), and we do **not** claim "reads never prompt again."

### 3.2 Write = per-call approval (asked every time)

Every write raises a **write-category** card, `category={vendor:"GOOGLE SHEETS", access:"WRITE"}`, `risk="medium"` (`PRD-P6b.md:226`). The runtime classifies write vs read by tool name (`_connector_action_is_read_only`, `stream_events.py:913-916`) — the profile deliberately names write tools with recognized write-terms (`update_values`, `write_append_values`, `clear_values`, `create_spreadsheet`, `batch_update_spreadsheet`, `PRD-P6b.md:121-155,228`) so `append` isn't misclassified as read.

```
┌─ 🛡 Write to Q3 Airdrop Claims? ───────────  SHEETS · WRITE ┐
│    Copilot wants to update cells in this workbook.          │
│  ┌────────────────────────────────────────────────────┐   │
│  │ Operation  update_values                            │   │
│  │ Workbook   Q3 Airdrop Claims                        │   │
│  │ Range      Claims!C2:C48                            │   │
│  │ Cells      47 values                                │   │
│  └────────────────────────────────────────────────────┘   │
│  BEFORE → AFTER   (optional diff, see §3.3)                │
│    C2   0.00  →  125.00                                     │
│    C3   0.00  →   80.00      … +45 more                     │
│  [ Approve & write ]              [ Skip ]                 │
│  🛡 You're always asked before Copilot writes to a sheet. │
└────────────────────────────────────────────────────────────┘
```

- Primary **`Approve & write`** is sky accent-filled; **`Skip`** is ghost.
- The write-category badge (`SHEETS · WRITE`) uses the neutral pill treatment (`.atlas-approval-card__pill`, `styles.css:3309-3323`); the access word is the semantic signal, not a second accent.
- Approve → the tool executes; Skip → it does not (`PRD-P6b.md:270`, AC-4). Every decision is per-call — no session write grant.

### 3.3 Optional before→after diff (open question, flagged)

`PRD-P6b.md:296` asks whether writes need a target-range value diff before the card resolves (analogous to the Safe simulation). The design **accommodates** it: if the write payload carries the target range's current values, the card renders a `BEFORE → AFTER` block in the `result` slot with the same `− old` (ember) / `+ new` (jade) coloring as the Safe diff. If not available, the card degrades to the range + cell-count params only (still per-call gated). This is a v1 decision to confirm, not a blocker.

---

## 4. Error, empty, and loading states (full inventory)

### 4.1 Tools popover (connect surface)

| State | Trigger | Copy / treatment | Source |
| --- | --- | --- | --- |
| Loading | popover open, port fetching | `Loading connectors…` (muted) | `ToolsPopover.tsx:230-240` |
| Error | `listServers`/`listCatalog` reject | `Couldn't load connectors.` (role=alert) | `ToolsPopover.tsx:241-247` |
| Empty | no connected + no installable | `No connectors yet` | `ToolsPopover.tsx:248-258` |
| Connecting (desktop) | external-browser handoff | row pill → `Opening your browser…` | §1.4 |
| Needs setup | `requiresPreRegisteredClient` or `OAuthSetupRequiredError` | pill = `Set up`; routes to setup form | `ToolsPopover.tsx:320-322`; `ChatScreen.tsx:1348,1379` |
| Preview-gated (Sheets) | `release_stage: preview`, preview disabled | pill degrades to `Set up` / `Connect in Settings` — never a dead button | `PRD-P6b.md:280,292` |
| Connected | install + auth complete | row moves to **Connected** section, jade toggle | `ToolsPopover.tsx:264-292` |

### 4.2 Safe signing card

| State | Copy / treatment |
| --- | --- |
| Building | `Building your transaction…` while decode + simulation run (skeleton in zones ②/③) |
| Undecodable | ember zone ②: `Can't decode this transaction — signing is blocked.` (matrix #3) |
| Simulation pending | zone ③ spinner: `Simulating…` |
| Simulation unavailable | amber zone ③ + `Retry simulation` ghost (matrix #4) |
| Simulation reverts | ember zone ③ + revert reason (matrix #5) |
| Delegatecall blocked | ember Operation row + block note (matrix #6) |
| Chain / owner / bound-Safe fault | disabled button + specific note (matrix #7–#10) |
| Mismatch | ember banner: `The transaction doesn't match what was described.` (matrix #11) |
| Waiting for wallet | button → `⟳ Waiting for your wallet…` |
| Rejected in wallet (4001) | quiet `✗ Rejected in wallet`; card stays actionable for retry |
| Submit failed post-signature | `Signature captured but the queue didn't accept it — retry.` Resolve as **rejected/retryable**, never approved (`PRD-P6a.md:282`) |
| Nonce drift | `The Safe's nonce changed — re-propose this transaction.` (`PRD-P6a.md:283`) |
| Cap / cooldown | disabled + countdown (matrix #12) |
| Signed | jade `atlas-approval-receipt` one-liner (§2.6) |

### 4.3 Sheets card

| State | Copy / treatment |
| --- | --- |
| Read consent pending | read card (§3.1) |
| Read approved | collapses to receipt `Read · Q3 Airdrop Claims` |
| Write consent pending | write card (§3.2) |
| Write approved | `Wrote 47 cells · Claims!C2:C48` receipt |
| Write skipped | `Skipped · no cells changed` receipt |
| Setup required (no operator client) | `connector_oauth_setup_required` → graceful "needs setup" card, HTTP 409, no crash (`PRD-P6b.md:222,269`) |
| Endpoint unavailable / preview | honest `preview / needs setup` — never a fake-working row (`PRD-P6b.md:279-280`) |

---

## 5. Token map (no hex — the three semantic hues)

Design-system is the SSOT (`packages/design-system/src/styles.css`); consumers resolve `var(--…)` — never hard-code hex (`CLAUDE.md` design-system). One-accent discipline: **sky is the only accent**; jade = success/live; ember = destructive; amber = warning. Brand swatches (Safe `#12FF80`, Google multicolor) are *data* on tiny inline glyphs only (`PRD-P6a.md:222`, `SPEC.md:33`).

| Element | Token | Value ref |
| --- | --- | --- |
| Primary sign/write/connect button | `--color-accent` bg / `--color-accent-contrast` text | `styles.css:163-165`, `.ui-button--primary:427-431` |
| Accent hover | `--color-accent-strong` | `styles.css:164` |
| Connect / Set up pill border+text | `--color-accent` / `--color-accent-line` | `ToolsPopover.tsx:495-502` |
| Simulation inflow `+` / success eyebrow / jade toggle / `connected` | `--color-success` (jade) | `styles.css:174` |
| Success wash (receipts) | `--color-success-bg` | `styles.css:175` |
| Simulation outflow `−` / revert / delegatecall block / mismatch / undecodable | `--color-danger` (ember) | `styles.css:168-171`, alias `--color-ember:222` |
| Reject button (text-danger, translucent border) | `.ui-button--danger` | `styles.css:451-459` |
| Unverified native transfer / sim-unavailable / repeated-destination flag | `--color-warning` (amber) | `styles.css:176-177` |
| Card shell | `--color-bg-elevated` / `--color-border` / `--radius-md` | `.atlas-approval-card`, `styles.css:3261-3268` |
| Inset decoded/sim frames | `--color-surface-muted` / `--color-border-soft` | `styles.css:3332-3374` |
| Decoded-row labels (mono uppercase) | `--font-mono` @ `--font-size-3xs`/`2xs` | `styles.css:46,62-63`; `.param dt:3355-3360` |
| Row values | `--color-text` | `styles.css:159` |
| "Copilot claims" quarantine text | `--color-text-subtle` @ `--font-size-xs` | `styles.css:161,64` |
| Vendor·access pill (`SAFE · ACTION`, `SHEETS · WRITE`) | `.atlas-approval-card__pill` (neutral) | `styles.css:3309-3323` |
| Footer reassurance | `--color-text-muted` @ `--font-size-xs`, shield glyph | `styles.css:3382-3396` |
| Disabled sign button | `opacity:.48; cursor:not-allowed` | `styles.css:396-400` |
| Spinner (waiting/submitting) | `--color-accent` ring | `.ui-harness-row__spinner:931-938` |

Both light and dark themes are fully specified (`styles.css:147-333`); every token above resolves per theme automatically — the card needs no theme-specific styling.

---

## 6. State inventory (single reference)

| # | Surface | State | Card / component | Approval kind | Primary action | Accent hue |
| --- | --- | --- | --- | --- | --- | --- |
| S1 | Tools popover | loading | `ToolsPopover` status | — | — | neutral |
| S2 | Tools popover | error | status role=alert | — | — | neutral |
| S3 | Tools popover | empty | status | — | — | neutral |
| S4 | Tools popover | Safe row idle | installable row | — | `Connect` | sky pill |
| S5 | Tools popover | Sheets row idle | installable row | — | `Set up` | sky pill |
| S6 | Tools popover | connecting (desktop) | row micro-state | — | `Opening your browser…` | neutral |
| S7 | Connect | Safe bind picker | `ConnectorConsentCard` (`mcp_auth`) | `mcp_auth` | `Bind this Safe` | sky |
| S8 | Connect | Sheets setup form | custom-config / SetupModal | — | `Connect` (after paste) | sky |
| S9 | Connect | needs-setup fallback | 409 needs-setup card | — | `Set up` | sky |
| S10 | Run | Safe building | `ApprovalCard` skeleton | `wallet_signature` | — | neutral |
| S11 | Run | Safe ready (positive sim) | Safe card §2.1 | `wallet_signature` | `Review & sign in wallet` | sky |
| S12 | Run | Safe unverified native | Safe card + amber note | `wallet_signature` | enabled + ⚠ | amber |
| S13 | Run | Safe blocked (undecode/delegatecall/unbound/mismatch) | Safe card blocked | `wallet_signature` | disabled/blocked | ember |
| S14 | Run | Safe sim revert/unavailable | Safe card | `wallet_signature` | disabled + retry | ember / amber |
| S15 | Run | Safe waiting for wallet | button spinner | `wallet_signature` | `⟳ Waiting…` | sky |
| S16 | Run | Safe rejected in wallet | quiet reject | `wallet_signature` | retry | neutral |
| S17 | Run | Safe signed | `atlas-approval-receipt` | `wallet_signature` | — | jade |
| S18 | Run | Safe submit-failed / nonce-drift | retryable card | `wallet_signature` | re-propose | ember |
| S19 | Run | Sheets read consent | read `ApprovalCard` | `mcp_tool` (read) | `Allow reads this session` | sky |
| S20 | Run | Sheets write consent | write `ApprovalCard` | `mcp_tool` (write) | `Approve & write` | sky |
| S21 | Run | Sheets write skipped | receipt | `mcp_tool` | — | neutral |
| S22 | Run | Sheets setup required | 409 needs-setup card | — | `Set up` | sky |

---

## 7. Copy strings (verbatim reference)

**Tools popover** (`SPEC.md:35`, `ToolsPopover.tsx:40-53`)
- Group note: `1-click connect · you approve first use`
- Safe row: `Safe{Wallet}` · `propose & sign transactions`
- Sheets row: `Google Sheets` · `read & write workbooks`
- Pills: `Connect` · `Set up` · Custom: `Custom MCP server` / `paste a JSON config`
- Desktop handoff: `Opening your browser…`

**Safe CONNECT (bind)**
- H: `Which Safe should Copilot watch?`
- Body: `Copilot can read this Safe and build transactions. It can never move funds without your signature.`
- Fields: `Safe address` · `Chain` · `+ Add another Safe`
- Actions: `Bind this Safe` · `Cancel`
- Reassurance: `Only Safes you bind here are visible to Copilot.`

**Safe SIGN card**
- Title: `Sign a Safe transaction` · subtitle: `Decoded from the calldata — not from Copilot.`
- Zone labels: `DECODED EFFECT` · `SIMULATED BALANCE CHANGE` · `COPILOT CLAIMS — for context, not verified`
- Verified eyebrow: `server-verified` · sim footer: `simulation succeeded · est. gas …`
- Actions: `Review & sign in wallet` · `Reject`
- Progress: `Waiting for your wallet…` · `Submitting signature…` · `Signed — added N of M confirmations` · `Rejected in wallet`
- Block/disable notes: see §2.5 matrix (verbatim in the "Inline note" column)
- Reassurance: `Copilot proposes. You sign in your own wallet — it never holds your keys.`

**Sheets**
- Read card H: `Read your Google Sheets?` · body: `Copilot needs to read this workbook to answer.` · action: `Allow reads this session` / `Not now`
- Write card H: `Write to {workbook}?` · body: `Copilot wants to update cells in this workbook.` · action: `Approve & write` / `Skip`
- Reassurance: `You're always asked before Copilot writes to a sheet.`
- Setup: `This connector needs a one-time setup before it can connect.`

---

## 8. Parity acceptance (design → tokens, per SPEC)

Measured against `design-source/SPEC.md:44-47` via `ui-design-reviewer`:
1. **One accent** — sky only. No jade/ember used as an *accent*; they are semantic (success / destructive) only. Brand swatches are inline glyph fills, never the app accent.
2. Card zones match `ApprovalCard`'s four-stripe rhythm (`ApprovalCard.tsx:63-106`); decoded frame + sim diff reuse `.atlas-approval-card__params` / `__result` geometry (`styles.css:3332-3374`).
3. Mono labels at `--font-size-3xs`/`2xs`; values at `--color-text`; agent-claims text visibly demoted (`--color-text-subtle`).
4. Primary button contrast = `--color-accent` / `--color-accent-contrast`; disabled uses the shared `.48` opacity treatment.
5. Reversible marker absent (`reversible=NO`) — no undo chip.
6. Copy byte-matches §7; the group note and connector descriptions match `SPEC.md:35` exactly.

---

## 9. Design-level residuals (mirror the PRD sign-off questions)

These are UX-visible open questions from `PRD-P6a-hardened.md:40-48` and `PRD-P6b.md:289-296` that change what the card shows — flag before build:
1. **Desktop signing handoff shape** (POST-body-to-loopback vs one-time-token-to-facade) — sets the `Opening your browser…` micro-flow copy and whether a "return to app" nudge is needed (`PRD-P6a-hardened.md:44`).
2. **Simulation provider** (self-hosted `eth_call`/anvil vs Tenderly) — decides the fidelity of zone ③ and the exact fallback copy when simulation is unavailable (matrix #4) (`PRD-P6a-hardened.md:45`).
3. **Delegatecall** — allowlist a library set with the high-friction typed confirm (matrix #6a) or hard-block entirely in v1 (`PRD-P6a-hardened.md:47`) — determines whether state S13's delegatecall branch is ever recoverable.
4. **Sheets write diff** — render the `BEFORE → AFTER` block (§3.3) in v1 or ship range+cell-count params only (`PRD-P6b.md:296`).
5. **Sheets release stage** — `preview` (FTUE 1-click degrades to needs-setup, S22) vs `stable` — governs whether the Sheets row's pill is `Connect` or `Set up` in the default desktop build (`PRD-P6b.md:292`).
6. **Safe brand swatch** — confirm `#12FF80` is allowed only as a tiny inline connector glyph, never the accent (`PRD-P6a.md:299`).

---

## Open questions

- Desktop signing handoff shape (POST-body-to-loopback vs one-time-token-to-facade, PRD-P6a-hardened.md:44) sets the 'Opening your browser…' micro-flow copy and whether a 'return to app' nudge is needed.
- Simulation provider (self-hosted eth_call/anvil vs Tenderly, PRD-P6a-hardened.md:45) decides zone ③ fidelity and the exact fallback copy when simulation is unavailable (sign-button matrix #4).
- Delegatecall policy (audited-library allowlist with a typed high-friction confirm vs hard-block entirely in v1, PRD-P6a-hardened.md:47) determines whether the delegatecall block state (S13) is ever recoverable in the card.
- Sheets write consent: render a BEFORE→AFTER value diff before the card resolves (§3.3) or ship range + cell-count params only (PRD-P6b.md:296)?
- Sheets release stage — preview (FTUE 1-click degrades to a needs-setup 'Set up' pill) vs stable (a real 'Connect' 1-click) in the default desktop build (PRD-P6b.md:292).
- Confirm the Safe brand swatch #12FF80 is permitted only as a tiny inline connector glyph and never as the app accent, to hold one-accent discipline (PRD-P6a.md:299).


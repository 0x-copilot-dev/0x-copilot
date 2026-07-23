# Generative Surfaces v2 — Problem Statement & Requirements

**v1.2** — v1.0 extracted from the clickable demo `Generative Surfaces v2.dc.html` (Claude
Design project `ceb081f6`, 7-part walkthrough); v1.1 folds in the user's decisions of
2026-07-23 (see §7 Decisions log); v1.2 adds §2G usage & cost attribution (user FR,
2026-07-23). **This document deliberately contains no solution
design.** Demo implementation artifacts are quarantined in §5.

Evidence tags like `[02]` refer to walkthrough parts; quoted strings are the demo's own
microcopy where that copy is itself requirement-grade. Items changed by a decision are
marked **(v1.1)**.

---

## 1. Problem statement

A person delegates real work to an agent that operates their actual SaaS tools (issue
trackers, email, CRM, call intelligence, …). Three things break today:

1. **Legibility.** The agent's work product — records it read, drafts it wrote, changes it
   wants to make — is either invisible or buried in chat prose. The person can't _see_
   the work as the artifact it is; they read descriptions of artifacts.
2. **Control.** Consequential actions (writes) either require blanket up-front trust or
   constant chat-level babysitting. There is no reliable way to (a) let harmless reads
   flow, (b) hold every write for review, (c) review a write _as the thing itself_ —
   the exact email, the exact field changes — and (d) guarantee that what executes is
   byte-for-byte what was approved.
3. **Trust & accountability.** After the fact, there is no faithful account of what was
   read, what changed, who decided, and when. And when tool output can't be honestly
   visualized, ad-hoc UI generation risks _fabricating_ a view — worse than showing
   nothing.

Secondary friction: **tool access mid-run**. When the agent reaches a tool it cannot use
_right now_ — never authenticated, or credentials expired/revoked — the run should not
die or silently skip; access is acquired in the moment, with scope and write-policy
decided there. **(v1.1: gates are an auth-state event, not a per-run ritual — see FR-B1.)**

One-line synthesis: _make an agent's work on real tools visible as live artifact surfaces,
make every write decidable on the artifact itself with a what-you-approve-is-what-executes
guarantee, and make the whole run auditable — without adding approval friction to harmless
reads or faking a view it can't honestly draw._

---

## 2. Functional requirements

### A. Canvas & surfaces (the work is visible as artifacts) — AGREED

- **FR-A1 — Surface canvas.** Agent output of consequence renders as live _surfaces_ on a
  canvas that sits beside (not inside) the conversation. "Records, drafts and proposed
  changes render as live surfaces on this canvas — you review and approve each one right
  here, never buried in chat." `[01]`
- **FR-A2 — Multiple named surfaces per run.** A run accumulates surfaces addressable by
  name in a tab strip (demo: `ENG-142`, `Re: Checkout fix`, `Opportunities · 8`,
  `Osprey call`, `Forecast export`, `Run receipt`). Tabs are the separation boundary
  between apps/surfaces **within a run**; the canvas is per-run. **(v1.1, resolves old
  Open Q4.)** `[02–07]`
- **FR-A3 — Artifact-shaped views.** Six view shapes evidenced: _record_ (issue),
  _message draft_ (email), _bulk-change table_, _call record_, _raw payload_, _run
  receipt_. Views show the data faithfully; nothing in any view is invented. `[02–07]`
- **FR-A4 — Assembly feedback.** A surface being prepared shows a loading/skeleton state
  ("Linear · assembling record view…") before content. Loading states are acceptable and
  expected — see NFR-1. **(v1.1)** `[02]`
- **FR-A5 — Per-surface provenance footer.** Every surface carries: producing operation,
  latency, access class (read-only / write · held), a stable ledger id, and a deep link
  to the native app ("Open in Linear ↗"). `[02]`
- **FR-A6 — Per-surface repair affordance.** "Looks wrong? Regenerate" — re-derives the
  view _from the same response_ (no re-fetch, no new tool call). `[02, source]`
- **FR-A7 — Chat remains the narrative rail.** Chat carries the goal, agent narration, a
  live plan with per-step status, and compact inline receipts for tool events. Approvals
  are _never_ performed in chat in Studio mode (see FR-F1 for Focus). `[all]`
- **FR-A8 — Mid-run steering.** The composer stays active during a run; steering messages
  fold in without weakening write policy ("Anything that writes will still wait for you
  on its surface."). `[04–05]`

### B. Tool access gates (v1.1 — connect only when auth is actually missing)

- **FR-B1 — Gate on missing/expired auth only.** A connect gate appears **only** when the
  connector is not yet authenticated, or its credentials are expired/revoked/insufficient.
  Already-authenticated connectors pass silently — no per-run re-consent ritual. The
  zero-tools first-run experience `[01]` is the _degenerate case_ (nothing authenticated
  yet), not the steady state. **(Decision 2.)**
- **FR-B2 — Gates park the run in place.** When a needed tool is unusable, the run _parks_
  on a gate card on the canvas ("the run is parked here until you connect — nothing runs
  without it") and resumes automatically once access is restored. Gates are discovered
  **lazily, at execution time** — the agent cannot enumerate upfront which approvals a
  run will need; a gate materializes when a tool call actually hits the missing-auth
  wall. **(Decision on old Open Q4.)** `[02–05]`
- **FR-B3 — Gate card contents.** Tool + host + auth method, _why_ it is needed in task
  terms ("to read ENG-142"), scopes in plain words, read-only pledge for read-only tools,
  progress context ("step 1 of 4"). `[02]`
- **FR-B4 — Write policy decided at the gate, synced with Settings.** Write-capable tools
  surface a policy choice at the gate: "Ask me first — every write waits on the surface"
  (default) vs "Allow always — bypass, writes auto-apply". **This must be the same
  policy** as Settings → Model & behavior → **Approval Policy** — one source of truth,
  with the gate acting as a per-connector scoped override of the global setting; both
  places show the same effective state. **(Decision 3.)** `[03]`
- **FR-B5 — Global write-posture indicator.** A header chip reflects effective posture:
  "Writes wait for you" normally; warning-styled "Bypass on · writes auto" when any
  connector is on allow-always. `[03, source]`
- **FR-B6 — Gates are ledger events.** Each gate carries a ledger id and provenance line,
  and counts in the pending-approvals total. `[02–05, source]`

### C. Reads vs writes (v1.1 — classification is a policy problem, not a protocol fact)

- **FR-C0 — Read/write classification policy.** The tool protocol does **not** reliably
  declare read vs write (MCP tool annotations — `readOnlyHint`, `destructiveHint` — are
  optional and explicitly untrusted hints). Classification must therefore come from a
  layered policy: (1) curated per-connector catalog where we ship one, (2) protocol
  annotations treated as _hints_, (3) **default = write (held) for unknown operations** —
  fail closed. The Approval Policy setting (FR-B4) governs what "held" means. Auto-run
  (FR-C1) applies only to operations classified read by this policy. **(Decision 3.)**
- **FR-C1 — Classified-read ops auto-run.** Once a tool is usable, read-classified ops
  execute without asking, visibly labeled "auto-ran (read)". `[02]`
- **FR-C2 — Writes stage, never fire.** A write-classified (or unknown) op produces a
  _staged preview on its surface_, marked held; a pending counter and an Approvals queue
  track it. `[03]`
- **FR-C3 — WYSIWYG execution guarantee.** The approval bar pins the exact revision:
  "Exactly this draft — rev 1 — is what sends." Approval executes that revision and
  nothing else; the receipt confirms ("Sent — exactly the revision you approved."). `[03]`
- **FR-C4 — Free-form edit-on-surface. (v1.1)** The user can edit the staged artifact in
  place, **free-form wherever the payload allows**: full-body free editing for text
  payloads (email body, comments, docs); per-field/cell free editing (within the write
  operation's schema) for structured payloads. Finishing an edit bumps the revision,
  re-pins the approval bar to the new revision, and records authorship **by diffing the
  user's revision against the agent's last revision** (changed spans marked "edited by
  you") rather than fixed per-block tags. **(Decision on old Open Q2.)** `[03]`
- **FR-C5 — Reject with recovery.** Reject visibly voids the staged write (dimmed) and
  offers Restore. Held/rejected writes execute nothing. `[03, source]`
- **FR-C6 — Bulk writes decompose to row-level decisions.** Multi-item writes render as a
  table: per-row old→new diff, per-row approve/hold, live counts, apply action naming its
  scope ("Apply 7 changes →"). "Writes apply **only** to rows you approve. Held rows stay
  untouched." `[04]`
- **FR-C7 — Agent self-flagging.** The agent pre-holds rows it judges risky, with the
  reason inline ("Contact replied 12d ago — agent pre-held"). Per-row user override is
  possible; the warning stays visible after override. `[04]`
- **FR-C8 — Bypass still leaves a trail — and flags still hold.** Under allow-always:
  writes auto-apply but receipts record it ("auto-sent under allow-always"), and the
  agent's own pre-held flags are _still held_. `[source]`
- **FR-C9 — Partial application is a first-class outcome.** "6 updated · 2 held,
  untouched"; held rows appear in the receipt as decisions ("you held"). `[04, 07]`

### D. Unknown tools & honest fallback — AGREED

- **FR-D1 — Generic view first for first-seen tools.** A never-before-rendered tool gets
  a generic view without waiting for shaping, labeled as such ("derived from response
  schema", "first render"). `[05]`
- **FR-D2 — Background purpose-shaped upgrade.** The view upgrades in place to a layout
  fit for that tool ("purpose-shaped … after first render"), announced non-modally with
  "Keep generic" and a persistent way back. Both derivations are ledgered distinctly.
  `[05–06, source]`
- **FR-D3 — Honest raw fallback.** When no view fits confidently: "This result doesn't
  fit a view … here's the raw result. Nothing is hidden." Pretty-printed raw with size,
  Copy, Download. Never fabricate a chart. `[06]`
- **FR-D4 — User-invited shaping. (v1.1 clarified)** From the fallback, "Suggest a shape
  for this tool →" = the user explicitly asks the system to attempt a proper view for
  this tool's output. Contract (Decision on old Open Q3): an immediate, user-invited
  shaping attempt — allowed to spend more effort than the automatic pass — whose result,
  on success, is persisted so this tool is shaped from now on; the attempt and outcome
  are ledgered. (No ML training implied in v1; "trains the generator" reduces to
  "persists to the shape registry + eval corpus".) `[06, source]`

### E. Provenance, audit, and time

- **FR-E1 — A single ledger threads everything.** Every event — gates, reads, staged
  writes, applied writes, view derivations — carries a stable id shown consistently in
  the surface footer, chat receipts, Sources list, and the receipt. `[all]`
- **FR-E2 — Run receipt as a surface.** Completion produces a receipt: stat tiles (reads
  auto-ran / writes proposed·approved / held untouched) over a per-action ledger with
  decision attribution ("auto-ran" / "you approved" / "you held" / "no view fit") and
  times. "Every write was decided on its surface — nothing was approved from chat."
  "Assembled from the run ledger · immutable." Counts reflect the session's actual
  decisions. `[07]`
- **FR-E3 — Sources panel.** Everything read this run, grouped by connector, each with
  time + ledger id + qualifiers. `[rail]`
- **FR-E4 — Run timeline. (v1.1: DEFERRED.)** Shelved for now per Decision on old Open
  Q1 — revisit later; the industry's timeline patterns are shifting. Not in v1 scope.
- **FR-E5 — Approvals queue with jump-to-surface.** The Approvals tab aggregates
  everything waiting — **as separate cards** (gates, held drafts, staged bulk writes) —
  with previews and "Review" actions that flip the canvas to the right surface. Cards
  accumulate lazily as the run discovers them (FR-B2). **(v1.1)** `[rail, source]`
- **FR-E6 — Fleet view.** An Agents tab lists this run and other agents (running and
  scheduled), with held work from any agent landing in the same Approvals queue. `[rail]`

### F. Modes & posture

- **FR-F1 — Two working modes. (v1.1 clarified.)** Studio = canvas-centered; generative
  surfaces live **only** here. Focus = chat-centered; the chat shows **rich cards only —
  no generative UI** (existing card affordances; no canvas). **(Decision on old Open
  Q5.)** `[chrome]`
- **FR-F2 — Status strip.** A persistent one-line status strip mirrors run state (op
  codes, gate context, ledger id). `[all]`
- **FR-F3 — Live counters.** Header chips track live scope: "N tools live", pending
  "N waiting", model/tools indicators in the composer. `[all]`

### G. Usage & cost attribution (v1.2 — user FR, 2026-07-23)

- **FR-G1 — Every LLM call is metered and attributed.** Every model invocation the
  product makes — the main run loop, subagents, view shaping/upgrades, user-invited
  shaping ("Suggest a shape"), any future Studio-mode calls — records token usage
  (input/output, model id, purpose) attributed to **user, conversation/chat, and run**
  (and surface where applicable), durably and queryably.
- **FR-G2 — Aggregation levels.** Totals must be computable per user (all-time and
  windowed), per chat/conversation, and per run — the levels a future Settings → Usage
  screen will query.
- **FR-G3 — Tokens are the stored unit.** Store tokens + model id, not dollars; cost is
  a presentation-time computation against a price table (prices change; records must
  not).
- **FR-G4 — Out of scope now.** The Settings usage screen/button UI itself. Only the
  accounting plumbing ships with v2; the UI comes later and must need no backfill.

---

## 3. Non-functional requirements

Tagged **[demonstrated]** / **[implied]** from the demo; v1.1 adjustments noted.

- **NFR-1 — Feedback-first latency, not instant. (v1.1)** Loading states are acceptable
  and expected: tool results take as long as they take, and surface creation may add
  time. The requirements are: immediate _feedback_ (skeleton/assembling state the moment
  an op starts), render as soon as the response is available, **no approval friction on
  read-classified ops**, and the purpose-shaping pass must never delay showing the
  generic view (FR-D1). Demo's 0.3s/0.9s/1.4s figures are illustrative only.
- **NFR-2 — Honesty over cleverness.** [demonstrated] If a confident view doesn't exist,
  never guess: lossless raw fallback (copyable, downloadable), explicitly labeled.
  "Nothing is hidden" — no data silently dropped by a view.
- **NFR-3 — Visual fidelity & consistency.** [demonstrated] All surfaces — including
  generated/upgraded ones and the raw fallback — share one design system. A surface looks
  native, never like an embedded foreign artifact.
- **NFR-4 — Fail-closed write safety.** [demonstrated] Default posture holds every write
  (and every unknown-classification op, FR-C0); nothing connects/reads/writes before
  consent; execution is bound to the approved revision; bypass is explicit, visible,
  per-connector, and still honors agent self-flags.
- **NFR-5 — Deterministic, re-derivable views.** [implied] A view is a pure function of
  the tool response; regeneration re-derives without re-executing the tool.
- **NFR-6 — Auditability.** [demonstrated] Immutable, session-accurate ledger; every
  action addressable by stable id; the receipt is assembled from the ledger, not the
  narrative; decision attribution on every consequential row.
- **NFR-7 — Non-blocking supervision.** [demonstrated] The run parks only on gates in its
  actual path; pending decisions accumulate in a visible queue rather than blocking
  unrelated progress or getting lost.
- **NFR-8 — Explanation before access.** [demonstrated] Every access request states its
  purpose in task language and scopes in plain words before consent.
- **NFR-9 — Progressive status transparency.** [demonstrated] Every long operation has a
  visible in-progress state in both the surface and the status strip.
- **NFR-10 — Provenance labeling of authorship.** [demonstrated] Machine-authored vs
  human-edited content is visually and durably distinguished (diff-based per FR-C4).
- **NFR-11 — Local/solo posture.** [implied] "Local agent workspace"; single operator; no
  multi-user constructs.

---

## 4. Open questions — status after 2026-07-23

| #   | Question                                             | Status                                                                                                                                    |
| --- | ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Timeline semantics (state time-travel vs navigation) | **Shelved** — timeline deferred entirely (FR-E4); revisit post-v1                                                                         |
| 2   | Edit granularity                                     | **Decided** — free-form wherever possible (FR-C4)                                                                                         |
| 3   | "Suggest a shape" contract                           | **Decided** — user-invited immediate shaping attempt, persisted on success (FR-D4)                                                        |
| 4   | Fleet canvas                                         | **Decided** — canvas is per-run; tabs separate surfaces within a run; Approvals = separate, lazily-discovered cards (FR-A2, FR-B2, FR-E5) |
| 5   | Focus-mode approvals                                 | **Decided** — Focus shows rich cards only, no generative UI (FR-F1)                                                                       |
| 6   | Failure paths                                        | **Phase 2** — user driving designs with a designer in parallel; tracked as session task + §8                                              |
| 7   | Persistence scope (surfaces/ledger across restarts)  | **Open** — carried into architecture                                                                                                      |

---

## 5. Quarantine — solution leakage to ignore

- The demo's own machinery: React class + timers, scripted beats, auto-advance pacing,
  keyboard part-switching, the 7-chapter framing.
- Vendor set (Linear, Gmail, Salesforce, Fathom) and all fictional data (names, amounts,
  timestamps, dollar figures).
- "Claude Sonnet 4.5" in the model pill; "mcp.oauth" as a mechanism label (requirement:
  external auth handshake; protocol specifics are design decisions).
- The `gv-XX` id format (requirement: stable ledger ids; format open).
- The 4-lane timeline visual (deferred anyway) and the exact latency figures (see NFR-1).
- Specific microcopy wording _except_ where quoted in §2/§3 as the requirement itself
  (the WYSIWYG pledge, the fallback honesty line, the read-only pledge, the held-rows
  pledge — those sentences are the contract in miniature).

---

## 6. Coverage note

Walked live: parts 01–07 end-to-end (send → gate → record view → gate w/ write policy →
draft stream → inline edit → rev 2 → approve & send → gate → bulk table → per-row
override → apply → new-tool gate → generic→upgraded view → raw fallback → receipt); rail
tabs (Chat / Agents / Approvals / Sources); demo logic source for unexercised branches
(allow-always auto-send + warning chip, reject/restore, regenerate, suggest-a-shape,
keep-generic). Not exercised: Focus toggle (demo locks to Studio), non-Run rail
destinations (stubbed).

---

## 7. Decisions log — 2026-07-23 (user)

1. **Surfaces, not transcripts** — agreed as extracted.
2. **Connect gates** — only when not authenticated before / credentials expired or
   otherwise unusable. Not a per-run ritual. (FR-B1 rewritten.)
3. **Reads vs writes** — protocol does not reliably classify read vs write; adopt layered
   classification policy with fail-closed default (FR-C0), and keep the gate-time write
   policy **in sync with the existing Settings → Model & behavior → Approval Policy**
   (FR-B4). Room to iterate here ("we can play with this").
4. **Honest fallback** — agreed.
5. **Ledger/receipt** — agreed.
6. **NFR latency** — instant reads not required; loading states while tool results arrive
   / surfaces build are fine (NFR-1 rewritten).
7. **Timeline** — shelved (FR-E4 deferred).
8. **Editing** — free-form wherever possible (FR-C4).
9. **Focus mode** — rich cards only, no generative UI (FR-F1).
10. **Failure paths** — Phase 2; user + designer producing designs in parallel while
    build starts on Phase 1.
11. **Usage & cost attribution (new FR)** — meter every LLM call with user/chat/run
    attribution (§2G); the Settings usage UI itself is out of scope for now.
12. **Deliverable shape** — no band-aids: produce a proper SDR (logical/component view,
    service boundaries, sequence diagrams) and per-PR PRDs with definitions of done; a
    rewrite of the current implementation is on the table where it doesn't fit.
13. **Design fidelity tooling** — use the `tools/design-parity/` harness (computed-style
    diff vs the Claude Design mock) for UI work; base components for surfaces live in
    the design-system/chat-surface kit so agent-built UI stays on-kit.

---

## 8. Phase-2 TODO (tracked as session task)

Failure-path UX + handling, designs incoming from the user's designer track:

- OAuth cancel / expiry / revocation mid-run (gate re-entry, resumability)
- Tool-call errors on read (surface-level error state vs chat-only)
- Mid-apply bulk failures (partial success accounting on the surface + receipt)
- Stale staged writes (target changed between staging and approval — precondition
  failure surfacing)
- Revoked grants between staging and apply

Wire into the staged-write lifecycle when designs land.

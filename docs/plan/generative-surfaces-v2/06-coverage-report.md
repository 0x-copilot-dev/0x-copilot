# 06 — Coverage Report (Stage 4 Coverage Critic)

Read-only audit mapping every in-scope FR-\*/NFR-\* in
[01-problem-and-requirements.md](01-problem-and-requirements.md) to the PRD(s) under
[prds/](prds/) that implement it. **No PRD was edited.**

**Excluded by mandate** (do not treat as gaps): **FR-E4** (run timeline — deferred),
**FR-G4** (usage-screen UI — deferred; only the no-backfill plumbing constraint is
in-scope and it rides on A2/E3), and all **Phase-2 failure-path UX** (§8 of the
requirements doc).

Legend: **Covered** = a PRD explicitly owns the requirement with a DoD item.
**Partial** = the substrate/flag delivers it implicitly but no PRD explicitly owns or
tests the v2-specific guarantee. **None** = no covering PRD.

---

## Coverage table — Functional Requirements

| Req   | Requirement (short)                                           | PRD(s)                                                                               | Status                  |
| ----- | ------------------------------------------------------------- | ------------------------------------------------------------------------------------ | ----------------------- |
| FR-A1 | Surface canvas beside chat                                    | B1                                                                                   | Covered                 |
| FR-A2 | Multiple named surfaces per run (tab strip)                   | B1, A3, B4                                                                           | Covered                 |
| FR-A3 | Artifact-shaped views (record/draft/table/call/raw/receipt)   | B1 (mount) + B2 (raw) + B3 (lifecycle) + D1 (draft) + D3 (bulk table) + E1 (receipt) | Covered                 |
| FR-A4 | Assembly / skeleton feedback                                  | B2                                                                                   | Covered                 |
| FR-A5 | Per-surface provenance footer                                 | B2                                                                                   | Covered                 |
| FR-A6 | Per-surface "Regenerate" (re-derive, no re-fetch)             | B3                                                                                   | Covered                 |
| FR-A7 | Chat = narrative rail; approvals never in chat (Studio)       | — (chat rail pre-exists; receipt copy in E1)                                         | **Partial**             |
| FR-A8 | Mid-run steering; composer active, write policy preserved     | — (composer pre-exists)                                                              | **Partial**             |
| FR-B1 | Gate only on missing/expired auth                             | C2                                                                                   | Covered                 |
| FR-B2 | Gates park run in place, resume automatically; lazy discovery | C2 (park/resume), E2 (lazy cards)                                                    | Covered                 |
| FR-B3 | Gate card contents (tool/host/why/scopes/progress)            | C2                                                                                   | Covered                 |
| FR-B4 | Write policy at gate, synced with Settings Approval Policy    | C1, C2                                                                               | Covered                 |
| FR-B5 | Global write-posture indicator chip                           | C1, C2                                                                               | Covered                 |
| FR-B6 | Gates are ledger events, count in pending total               | C2 (ledger id), E2 (pending counter)                                                 | Covered                 |
| FR-C0 | Layered read/write classification, fail-closed default=write  | C1 (policy), B2, C2                                                                  | Covered                 |
| FR-C1 | Classified-read ops auto-run ("auto-ran (read)")              | A3                                                                                   | Covered                 |
| FR-C2 | Writes stage, never fire (held preview + counter)             | D1                                                                                   | Covered                 |
| FR-C3 | WYSIWYG execution guarantee (pin rev + receipt confirm)       | D1 (pin bar), D2 (execute exact rev + "Sent — exactly the revision you approved.")   | Covered                 |
| FR-C4 | Free-form edit-on-surface, diff-based authorship              | D1 (text), D3 (per-cell)                                                             | Covered                 |
| FR-C5 | Reject voids write + Restore                                  | D1                                                                                   | Covered                 |
| FR-C6 | Bulk writes → row-level decisions table                       | D3                                                                                   | Covered                 |
| FR-C7 | Agent self-flagging of risky rows                             | D3                                                                                   | Covered                 |
| FR-C8 | Bypass still leaves a trail; flags still hold                 | C2, D1, D3, E1                                                                       | Covered                 |
| FR-C9 | Partial application first-class ("6 updated · 2 held")        | D3, E1                                                                               | Covered                 |
| FR-D1 | Generic view first for first-seen tools                       | B2, B3                                                                               | Covered                 |
| FR-D2 | Background purpose-shaped upgrade + "Keep generic"            | B3                                                                                   | Covered                 |
| FR-D3 | Honest raw fallback (never fabricate)                         | B2, B3, B4                                                                           | Covered                 |
| FR-D4 | User-invited "Suggest a shape", persisted on success          | B4                                                                                   | Covered                 |
| FR-E1 | Single ledger threads everything, stable ids                  | A1 (contracts), A3 (emission), B1 (client fold)                                      | Covered                 |
| FR-E2 | Run receipt as a surface                                      | E1 (surface + fold), A1 (contract), E3 (audit/export)                                | Covered                 |
| FR-E3 | Sources panel (grouped by connector)                          | E1                                                                                   | Covered                 |
| FR-E4 | Run timeline                                                  | —                                                                                    | **Excluded (deferred)** |
| FR-E5 | Approvals queue w/ jump-to-surface (separate cards)           | E2                                                                                   | Covered                 |
| FR-E6 | Fleet / Agents view                                           | E2                                                                                   | Covered                 |
| FR-F1 | Two modes: Studio (canvas) vs Focus (rich cards, no gen-UI)   | — (SURFACES_V2 flag gates on/off; no PRD owns a user mode toggle)                    | **Partial**             |
| FR-F2 | Status strip mirrors run state                                | B2                                                                                   | Covered                 |
| FR-F3 | Live counters (tools live / N waiting)                        | E2                                                                                   | Covered                 |
| FR-G1 | Every LLM call metered + attributed                           | A1 (contract), A2 (seam+store)                                                       | Covered                 |
| FR-G2 | Aggregation levels (per user / chat / run)                    | A2 (rollup queries + DoD, both adapters)                                             | Covered                 |
| FR-G3 | Tokens are the stored unit (not dollars)                      | A2, E3                                                                               | Covered                 |
| FR-G4 | Usage screen UI                                               | A2/E3 (no-backfill plumbing only)                                                    | **Excluded (deferred)** |

## Coverage table — Non-Functional Requirements

| Req    | Requirement (short)                                  | PRD(s)                                                                     | Status  |
| ------ | ---------------------------------------------------- | -------------------------------------------------------------------------- | ------- |
| NFR-1  | Feedback-first latency (skeleton, no read friction)  | B2, B3                                                                     | Covered |
| NFR-2  | Honesty over cleverness (lossless raw fallback)      | B2, B3, B4                                                                 | Covered |
| NFR-3  | Visual fidelity / one design system                  | all 🎨 UI PRDs (B1,B2,B3,B4,C1,C2,D1,D2,D3,E1,E2,E3) via design-parity DoD | Covered |
| NFR-4  | Fail-closed write safety                             | C2, D1, D2, D3                                                             | Covered |
| NFR-5  | Deterministic, re-derivable views                    | B3, D1, E1                                                                 | Covered |
| NFR-6  | Auditability (immutable ledger, receipt from ledger) | A3, E1, E3                                                                 | Covered |
| NFR-7  | Non-blocking supervision (queue not block)           | D3, E2                                                                     | Covered |
| NFR-8  | Explanation before access                            | C2                                                                         | Covered |
| NFR-9  | Progressive status transparency                      | B2 (F2 status strip), B3                                                   | Covered |
| NFR-10 | Provenance labeling of authorship (diff-based)       | D1                                                                         | Covered |
| NFR-11 | Local/solo posture                                   | E2                                                                         | Covered |

---

## Requirements with NO or PARTIAL coverage

No requirement is fully **uncovered**. Three are **Partial** — the reusable substrate
(the existing Run cockpit chat rail + composer, and the `SURFACES_V2` client/runtime
flag) delivers the baseline behavior, but **no PRD explicitly owns or tests the
v2-specific clause**, so each is a silent assumption rather than a verified guarantee:

1. **FR-A7 — Chat as narrative rail; "approvals are never performed in chat in Studio
   mode."** The chat rail (goal, narration, live plan, per-step status, inline receipts)
   pre-exists in the cockpit, and E1's receipt copy asserts "nothing was approved from
   chat" — but no PRD enforces or tests that, with the canvas mounted (Studio), approval
   affordances are absent from the chat rail. This is a fail-closed-adjacent guarantee
   worth an explicit DoD (likely on B1's mount or D1's staged-write UI).

2. **FR-A8 — Mid-run steering without weakening write policy.** The composer-active-
   during-run behavior is the existing turn-N keystone, but the v2 guarantee — that a
   steering message folded in mid-run does **not** relax the held-write posture ("Anything
   that writes will still wait for you on its surface") — is owned by no PRD and has no
   test. Natural home: C1 (policy) or D1 (staged-write) asserting steering cannot flip a
   connector to allow-always.

3. **FR-F1 — Studio vs Focus modes.** `SURFACES_V2` gives flag-on (canvas) vs flag-off
   (byte-identical today, effectively Focus), which is the substrate for this split. But
   no PRD owns a **user-selectable** Studio/Focus toggle, nor the enforcement that **Focus
   shows rich cards only — no generative UI**. Every UI PRD is written against flag-on; the
   flag-off/Focus posture is only ever asserted as "byte-identical to today," never as a
   distinct, tested mode. If Focus is meant to be a runtime user choice (not just the
   flag), it is unowned.

---

## DoD-level blind spots across the set

- **The mode/rail layer (A7/A8/F1) has no home PRD.** The three Partials above cluster:
  the plan thoroughly owns the _canvas/surface/ledger/write_ machinery but treats the
  _chat rail, steering, and mode selection_ as pre-existing. If any of the three v2-
  specific clauses regress, no test catches it. Recommend a small "Studio shell &
  posture" PRD, or fold explicit DoD lines into B1/C1/D1.

- **FR-G2 aggregation is covered only via A2's rollup tests, never explicitly tagged.**
  Coverage is real (A2 DoD: "rollup totals equal sum of rows, both adapters," incl. per-
  run and per-conversation), but a future Usage UI (FR-G4) depends on all three levels
  (user all-time+windowed, chat, run) being queryable. A2's own open question flags that
  `view_shaping` records `surface_id=None`, so per-surface shaping cost is only queryable
  for B4 shape-requests — a known, accepted attribution gap to keep visible.

- **FR-A3's six view shapes are spread across six PRDs (B1/B2/B3/D1/D3/E1).** No single
  PRD guarantees the _full_ shape set renders on-kit; the record/call/raw shapes in
  particular live in B2/B3 as generic+shaped derivations rather than as named per-shape
  DoD items. Verify no shape (esp. "call record") falls between B3's generic-upgrade path
  and the builtin-spec catalog.

- **FR-B4 ↔ Settings Approval Policy "one source of truth" spans C1 + backend Settings.**
  C1/C2 own the gate-time policy and its sync, but the requirement that the gate is a
  _per-connector scoped override_ of the _global_ setting, with **both places showing the
  same effective state**, needs an end-to-end DoD asserting round-trip consistency (gate
  → Settings → header chip FR-B5). Confirm C1's DoD tests the Settings-side reflection,
  not just the gate-side write.

- **Cross-PRD ledger-vocabulary drift risk.** Multiple PRDs (A2 especially) note "define
  the literal in parallel, reconcile before merge" for A1 constants. The coverage is
  sound only if the A1 golden-fixture parity test (referenced by A2/A3/B1) actually pins
  every event name/field these PRDs emit. This is a merge-ordering hazard, not a scope
  gap, but it can produce runtime divergence if A1 lands after a consumer.

---

## Resolution log (2026-07-23 close-out)

The three **Partial** coverage gaps above (the "Studio shell & posture" cluster A7/A8/F1)
now each have an explicit owner PRD and a binding DoD line written into that PRD. Status
for all three: **Covered (owned + tested)**.

| #   | Req                                                                 | Now owned by                                                                            | DoD line added                                                                                                                                                                                                                                                                                                                                     |
| --- | ------------------------------------------------------------------- | --------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 5   | **FR-A7** — chat = narrative rail; approvals never in chat (Studio) | **PRD-B1** (Definition of done → "Studio shell & posture DoD")                          | "With the canvas mounted (Studio), no approval/decision affordance appears in the chat rail; a test asserts staged-write and gate approvals render **only** on the canvas surface, never as a chat-rail control."                                                                                                                                  |
| 6   | **FR-A8** — mid-run steering must not weaken write policy           | **PRD-D1** (Definition of done → "Close-out DoD")                                       | "A mid-run steering message cannot relax the held-write posture: a test asserts that after steering, write-classified ops still stage/hold and **no** connector is flipped to `allow_always` by steering."                                                                                                                                         |
| 7   | **FR-F1** — Studio (canvas) vs Focus (rich cards, no gen-UI)        | **PRD-B1** (Definition of done → "Studio shell & posture DoD"; Out-of-scope scope note) | "The canvas mount is gated by mode: **Focus ⇒ canvas not mounted** (rich cards only, no generative surfaces); **Studio ⇒ canvas mounted**. A test asserts both branches of the mode gate." Scope: B1 owns the mode→canvas-visibility gate; a _user-facing_ Studio/Focus toggle beyond the existing control is out of scope unless already present. |

No DoD was weakened; each is an additive guarantee closing a silent-assumption gap. The
remaining DoD-level blind spots above (FR-G2 attribution note, FR-A3 shape-set spread,
FR-B4 Settings round-trip, ledger-vocabulary merge ordering) are tracked notes for
implementers, not unowned requirements.

# PRD-J — Main health + environment verification

**Status:** Draft · **Owner:** platform · **Surfaces:** `apps/frontend` tests,
live-Postgres gate, desktop staged runtime · **Follows:** PRD-I ·
**Coordinates with:** the first-run onboarding workstream (its wave introduced
the reds in J1).

## 1. Context & problem

- **P-J1 — main is red.** Four tests fail on _pristine_ origin/main (verified
  twice, independent of the parity work): `src/app/keymap.test.ts` ×2 (chord
  timing) and `ActivityRoute.test.tsx` / `ChatsArchiveRoute.test.tsx` ×1 each.
  The route reds appeared with the onboarding (FTUE) wave; the keymap pair has
  been failing since at least the Wave-2 era. A red main normalizes ignoring
  CI — the exact failure mode that let earlier cross-package main-reds ship.
- **P-J2 — Postgres adapters are live-unverified.** `PostgresProjectsStore`
  (PR #175/#182), the conversation pin projection (PR #154), the provider
  `default_model` migration 0042 (PR #182), and now `PostgresConnectorsStore`
  (PRD-I3) have unit-tested Python paths but their SQL has never executed
  against a real Postgres in this effort. The repo already has the live-gate
  pattern (`tools/run-merge-live-gate.sh`, `tests/integration/persistence/
*_live.py`, CI `ci-merge-live-gate.yml`) — these stores just aren't in it.
- **P-J3 — the staged desktop runtime is stale.** The v3 side-by-side capture
  found Projects returning 500 on the staged runtime — staged before the
  projects backend (H.2/H.3), the write-through (#193), and migration 0042
  landed. Unknown whether the packaged app works end-to-end with all of it.

## 2. Goals / Non-goals

**Goals**

- G1 — main is green: all four reds root-caused and properly fixed (product or
  test, whichever is honestly wrong). No skips, no timeout-inflation bandaids.
- G2 — The new Postgres surfaces run under the existing live-PG gate: projects
  store, conversation pin, provider default_model, connectors store (I3) — as
  `*_live.py` suites the gate discovers, runnable locally + in the CI drill.
- G3 — A fresh desktop stage boots the full supervised stack; the packaged app
  verifies live: Projects loads (500 gone), custom MCP add → connector row,
  skill editor in the Skills destination.

**Non-goals**

- NG1 — Fixing the onboarding wave's product bugs beyond what the red tests
  reveal (that workstream owns its features; we own green main).
- NG2 — New CI infrastructure (reuse the existing live-gate runner).

## 3. Functional requirements

- **FR-J1.1** — Root-cause each red: git-bisect or diff-inspect against the
  introducing wave; classify _product regression_ vs _stale test_ vs _flaky
  harness_; fix accordingly. Chord-timing tests may be rewritten to fake
  timers/deterministic dispatch — never widened tolerances.
- **FR-J1.2** — `npx vitest run --root apps/frontend` fully green on main
  (excluding none), demonstrated twice consecutively (flake check).
- **FR-J2.1** — Add `*_live.py` suites (following `test_account_merge_live.py`
  conventions: env-gated, skip-without-DB) covering: projects store CRUD +
  membership + audit signing + RLS, conversation `pinned/preview/model` + `/pin`
  persistence, provider_api_keys `default_model` (0042) round-trip, connectors
  store (I3) write-through round-trip + signing + RLS.
- **FR-J2.2** — Execute the gate locally against a real Postgres (the staged
  desktop `postgres` binary boots a throwaway cluster — same approach as the
  desktop supervisor) and report actual pass counts; wire the new suites into
  the existing gate script so CI picks them up unmodified.
- **FR-J3.1** — Re-stage the runtime (`tools/desktop-runtime/stage.mjs`) from
  current main; boot via the cli-testing driver (prod posture, device account).
- **FR-J3.2** — Verify live in the packaged app: Projects lists (empty-state,
  not 500); Tools → Connect → "Add a custom server" → row appears with honest
  status; Skills → manage pane → create/edit skill. Capture screenshots for the
  parity artifact.

## 4. Non-functional requirements

- **NFR-J.1** — Honesty of classification: a test deleted or weakened requires
  the PRD-J PR description to say what coverage was lost and why that is right.
- **NFR-J.2** — The live gate stays hermetic (throwaway cluster, no developer
  DB pollution) and skip-cleanly when no PG is available.

## 5. Implementation plan (principal-engineer)

1. **J1 first, standalone PR** — smallest diff that makes main green; it
   unblocks every other stream's CI signal. Coordinate: if a fix belongs to the
   onboarding session's in-flight code, fix forward on main and flag it to that
   workstream rather than reverting their wave.
2. **J2 rides after PRD-I3** so the connectors adapter is included in one gate
   run. Suites first (they're inert without a DB), local execution second, gate
   wiring last.
3. **J3 last** — the stage must include I1–I3 + J1 fixes to be a meaningful
   verification of the shipped whole.

## 6. Definition of done

- [ ] main fully green (twice consecutively), root causes documented per test.
- [ ] Live-PG gate covers all four new surfaces; local run reported with real
      counts; CI drill unchanged-but-covering.
- [ ] Fresh stage boots; Projects/custom-MCP/skills verified live with
      screenshots; parity artifact updated if surfaces changed.
- [ ] STATUS.md + memory updated; worktrees/branches cleaned.

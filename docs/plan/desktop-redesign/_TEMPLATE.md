# Phase PRD Template & Conventions

Every phase PRD (`docs/plan/desktop-redesign/phase-<id>/PRD.md`) MUST follow this structure. Write like a staff engineer: grounded in real file paths (read the actual code in this worktree), SSOT-explicit, every claim testable. No hand-waving, no "add tests" — name the cases.

## ID & naming conventions

- **User stories:** `US-<phase>.<n>` (e.g. `US-3.2`). Format: _As a `<role>`, I want `<capability>`, so that `<benefit>`._ + acceptance criteria as Given/When/Then bullets. Roles: **Solo user** (primary), **Team admin** (only where profile-gated), **Developer/maintainer** (DX/architecture).
- **Functional requirements:** `FR-<phase>.<n>` — atomic, testable, imperative ("The rail MUST render 6 destinations when profile=single_user_desktop").
- **PRs/commits:** `PR-<phase>.<n>` — phase-scoped numbering (unique within phase; no global collisions). Each PR independently mergeable, reviewable, ≤ ~1000 LOC, keeps `main`/web green.
- Cross-reference other phases as `PR-1.3`, `FR-2.1`, etc.

## Required sections (in order)

### 1. Context & problem

What this phase delivers, what it builds on (upstream phases), why now. 3–6 sentences. Link the relevant `DESIGN-SPEC.md` sections and `PLAN.md` phase.

### 2. Goals / Non-goals

Bulleted. Non-goals prevent scope creep (name what is explicitly deferred to which phase).

### 3. User stories

Table or list of `US-<phase>.<n>` with role, capability, benefit, and **acceptance criteria** (Given/When/Then). Cover the happy path **and** empty/loading/error/edge states. Minimum ~5 per phase; more for Run/Settings.

### 4. Functional requirements

Numbered `FR-<phase>.<n>`. Group by area. Each maps to ≥1 user story and ≥1 test.

### 5. Architecture & system design

- **Single source of truth:** what is the canonical owner of each concept this phase touches; what gets consolidated/removed to avoid duplication.
- **Boundaries & ports:** respect `CLAUDE.md` — no deployable app imports another app's `src/`; `chat-surface` stays framework-agnostic (ports: Transport/Router/KeyValueStore/PresenceSignal). Name the ports/props used.
- **Data flow & key types/interfaces** (name them, with file locations).
- **Reuse vs new:** table of components/modules — Reuse (path) / Move (from → to) / New (path).

### 6. Affected files / component inventory

Explicit **Create / Modify / Delete** lists with real paths (read the code). Flag anything deleted/superseded.

### 7. PR / commit breakdown

Ordered `PR-<phase>.<n>` list. Each PR: **title**, scope (1–2 lines), files touched, upstream deps (PR ids), **acceptance criteria** (what makes it mergeable), est. size (S/M/L or LOC). PRs must be independently reviewable and leave the tree green.

### 8. Testing plan

- **Unit** — specific cases with the target test file path and runner (`vitest` for TS packages/apps via `npm run test --workspace <pkg>`; `pytest` in the owning service `.venv` for Python). Name assertions, not "test the component."
- **Integration** — cross-module/transport-port behavior, mocked transport.
- **E2E / live smoke** — concrete steps against the desktop app (`apps/desktop/SMOKE.md`), incl. the live-run path where relevant (unit fakes have hidden real breakage before — smoke live).
- **Regression guard** — for Phase 1 especially: the web app (`apps/frontend`) must stay behaviorally identical; name the checks.
- Map: each FR → at least one test.

### 9. UI/UX acceptance checklist

Checkbox list grounded in `DESIGN-SPEC.md` **exact** tokens/dimensions. Must cover:

- Exact tokens/dims (e.g. "rail 48px; active = `--panel2` + 2px `--accent` left bar; hairline `--line`").
- **States:** default / hover / active / focus-visible / loading / empty / error / streaming (as applicable).
- **a11y:** roles (tablist/tab/tabpanel/status/alert), keyboard nav + shortcuts, focus management, `prefers-reduced-motion`, contrast.
- **Theming:** light + dark; `[data-density]`; single-accent discipline (no stray decorative color).
- Component reuse noted (which existing component, restyled to tokens).

### 10. Dependencies & sequencing

Upstream (blocked by) / downstream (blocks) phases and PRs. Must form a DAG.

### 11. Risks & mitigations

Table. Include rollback/flagging strategy for risky changes.

### 12. Definition of done

Checklist: all FRs met, tests green (unit+integration+smoke), UI/UX checklist passed, web app unregressed, docs/README updated, no dead code left.

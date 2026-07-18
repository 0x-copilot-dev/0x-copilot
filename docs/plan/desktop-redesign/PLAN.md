# 0xCopilot Desktop Redesign — Implementation Plan

**Branch:** `feat/desktop-redesign` · **Worktree:** `/Users/parthpahwa/Documents/work/enterprise-search-redesign`
**Design source of truth:** Claude Design project `enterprise-search` (DesignSync MCP, projectId `019df34e-62de-7eb4-9ae8-23ffe0c1fe14`), entry `0xCopilot App.html` → `copilot.css` (v2 "quiet") + `copilot-{app,data,settings,flows,workspace}.jsx`.
**Supersedes/evolves:** `docs/plan/desktop/` (the original 8-phase Atlas plan). This is an _evolution_ — most components already exist; the work is consolidate + wire + recompose + fill holes.

---

## 1. North star

A shippable **solo local-desktop** 0xCopilot app: the **6-destination shell** in the v2 "quiet" design language, with the **production chat interaction layer consolidated into `packages/chat-surface` (single source of truth)** and mounted on desktop. **Run** is the flagship cockpit (Studio/Focus). **Settings** is a first-class solo surface. Team features are gated behind `ENTERPRISE_DEPLOYMENT_PROFILE`. Design-system tokens + fonts are actually loaded on desktop (today they are not).

## 2. Decisions locked

1. **6 destinations:** Run · Chats · Projects · Activity · Tools (=connectors) · Skills (=skill catalog), plus **Settings** + avatar in the rail foot. Fold/gate the other current destinations (home, library, inbox, todos, routines, agents, team).
2. **Run = its own live cockpit destination**, distinct from Chats-as-archive.
3. **design-system is the single token source of truth.** Fold the v2 `copilot.css` refinements into it; do not ship a second CSS system.
4. **Default desktop to solo**; gate Workspace/Members/Billing/Team behind `ENTERPRISE_DEPLOYMENT_PROFILE = single_user_desktop | team`.
5. Web3/treasury flavor in the prototype (Safe, Dune, USDC) is **dressing only** → connectors are **generic-SaaS-first**.
6. Accept the **Tools/Skills/Connectors** renaming.
7. **Drop "Auto" mode.** Autonomy is a _run state_ (runs and pauses for approvals regardless of view). Run has a 2-way **Studio / Focus** toggle.

## 3. Architecture decision record — the (a)/(b) fork → **(a) SSOT via `chat-surface`**

**Context.** Production interaction richness (advanced composer, citations, subagent/fleet cards, 4-zone approval card, streaming-markdown) lives in `apps/frontend`. The canvas system desktop imports (`chat-surface/thread-canvas` + `surface-renderers`) is scaffolding, unmounted. Two internal stacks.

**Options.**

- **(b) Desktop reuses `apps/frontend` wholesale.** ❌ Requires `apps/desktop` to import `apps/frontend/src` — a hard boundary violation (`CLAUDE.md`: "no deployable component imports another's `src/`"). Couples desktop to web product state/routing. Rejected.
- **(a) Hoist the production interaction components down into `packages/chat-surface`** — already designated the "framework-agnostic chat UI surface" — behind the existing ports (`Transport`/`Router`/`KeyValueStore`/`PresenceSignal`). Both `apps/frontend` and `apps/desktop` consume one copy.

**Decision: (a).** One component, one place, two consumers. Prefer hoisting into the _existing_ `chat-surface` over minting a new shared package (a second source of truth). Migrate **incrementally, one component family per PR**, with `apps/frontend` re-exporting from `chat-surface` during transition so web behavior is preserved and tests stay green. No big-bang, no fork.

**Invariant:** `chat-surface` stays framework-agnostic — no bare `window`/`document`/`fetch`/`localStorage`; everything through ports. Enforced by ESLint substrate rules.

## 4. Design system v2 "quiet" — token updates (into `packages/design-system/src/styles.css`)

Update **values**, not structure (still SSOT):

- **Fonts:** `--font-display` and `--font-sans` → system stack `-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, sans-serif`; `--font-mono` stays `"JetBrains Mono", ui-monospace, SFMono-Regular, monospace`. (Custom brand faces Space Grotesk/Instrument Sans are dropped from the UI; keep only JetBrains Mono `@font-face`.)
- **Neutrals (dark):** bg `#09090b`, elevated `#0d0d10`, surfaces `#111114 / #16161a / #1d1d23`; borders (hairlines) `rgba(255,255,255,.06 / .10 / .18)`; text `#ececf1 / #d4d4db`, muted `#98989f / #64646d`. Light theme + `[data-density=compact|spacious]` + `[data-reduce-motion]` retained.
- **Accent discipline:** `--color-accent` = **sky `#5fb2ec`** (only accent); `--color-success`/live = **jade `#57c785`**; `--color-danger`/destructive = **ember `#f0764f`**; `--color-warning` = amber `#e8b45e`. Accent options sky/jade/ember/violet via `[data-accent]`.
- **Usage rule:** neutralize per-connector logos and timeline lane colors to `surface-muted`/`text` (monochrome). No decorative color.
- **Reconcile** hardcoded lime `#c2ff5a` in `thread-canvas` + `surface-renderers` → tokens.
- Radii `8 / 12 / 6`, base font 13px.

---

## 5. Target IA — 6 destinations, from the current 12

`destinations.ts` becomes a **profile-gated** source of truth.

| Redesign (solo)                     | From current                           | Action                                               |
| ----------------------------------- | -------------------------------------- | ---------------------------------------------------- |
| **Run**                             | (was nested in `chats`)                | promote to top-level cockpit                         |
| **Chats**                           | `chats`                                | keep; becomes archive/list, reopen → Run             |
| **Projects**                        | `projects`                             | keep                                                 |
| **Activity**                        | `agents` + `inbox` + "audit log"       | recast as run history; retention in Settings→Privacy |
| **Tools**                           | `connectors`                           | rename                                               |
| **Skills**                          | `tools`                                | rename                                               |
| Settings (rail foot)                | `onOpenSettings` slot                  | make first-class on desktop                          |
| —                                   | `home`, `library`, `todos`, `routines` | fold/defer (not top-level)                           |
| —                                   | `inbox`                                | folded into Activity + notifications                 |
| —                                   | `memory`                               | into Settings → Privacy                              |
| team-only: `team`, Members, Billing | `team`                                 | gate behind `team` profile                           |

## 6. Run cockpit spec (flagship)

- **Layout:** center = **work surface** (`TcSurfaceMount` → `surface-renderers`); right = **tabbed rail `[Chat · Sources · Agents · Approvals]`** (reuse `WorkspacePane`); bottom = **timeline** (`TcSwimlanes`).
- **Modes:** **Studio** (surface + chat + timeline) / **Focus** (chat-forward, surface hidden, timeline minimized). No Auto. Toggle + `⌘M`.
- **Timeline:** surface/subagent lanes, LIVE/VIEWING, scrub/step/snap-to-now, pinned beads.
- **Parallel subagents (3 surfaces):** inline `SubagentFleetCard` (dispatch summary) · timeline **lanes** (live parallel tracks) · **Agents** tab (detail).
- **Approvals (inline-on-surface):** on structured artifacts → `TcInlineDiff`/`SheetDiff` per-row; in conversation → `ApprovalCard` (4-zone) + `ApprovalReceipt`.
- **Streaming:** conversational tables → streaming GFM markdown (Streamdown, citation-safe); editable/large tabular surfaces → `surface-renderers` snapshot diffs in the center pane.
- **New states to design (prototype gap):** Run **empty/idle** and **multi-run** selection.

## 7. Component consolidation map (`apps/frontend` → `packages/chat-surface`)

Each row = one PR: hoist behind ports → `apps/frontend` re-exports (shim) → `apps/desktop` consumes. Keep web green.

| Family                    | Source (apps/frontend)                                                                                           | Target (chat-surface)                                   |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| Message/markdown renderer | `markdown/MarkdownText.tsx`, `streamingCursor.ts`, `citationRemarkPlugin.ts`                                     | `chat-surface/src/messages`                             |
| Composer wiring           | `composer/AssistantComposer.tsx`, `shell/ModelPill.tsx`, `ThinkingDepthControl`, `composer/ComposerPlusMenu.tsx` | `chat-surface/src/composer` (props/ports)               |
| Citations                 | `components/citations/*`, `messages/MessageSourcesStrip.tsx`, `SourcesPanel`                                     | `chat-surface/src/citations` (+registry, already there) |
| Subagents                 | `subagents/SubagentCard.tsx`, `messages/SubagentFleetCard.tsx`, `FleetSubagentRow.tsx`                           | `chat-surface/src/subagents`                            |
| Approvals                 | `activity/ApprovalCard.tsx`, `ApprovalReceipt`, `tools/ApprovalTool.tsx`                                         | `chat-surface/src/approvals`                            |
| Workspace pane            | `workspace/WorkspacePane.tsx` (Sources/Agents/Draft/Approvals/Skills)                                            | `chat-surface/src/workspace` (→ Run right rail)         |

---

## 8. Phases (each = 5–9 narrow PRs/subagents, ≤~1000 LOC / ≤30 min each)

### Phase 0 — Foundation & de-risk

- **0A** Wire `@0x-copilot/design-system/styles.css` + `@font-face` into `apps/desktop` renderer (esbuild CSS loader + `index.html`/`bootstrap` import). _Fixes the currently-unstyled desktop shell._
- **0B** Update design-system tokens to v2 "quiet" (§4).
- **0C** Reconcile lime `#c2ff5a` → tokens; neutralize connector/lane colors.
- **0D** `DeploymentProfile` context/port (`single_user_desktop | team`) exposed to the shell.
- **0E** Create `chat-surface` module homes (messages/composer/citations/subagents/approvals/workspace) + re-export shim pattern + ESLint boundary guard.

### Phase 1 — Consolidate interaction layer into `chat-surface` (the (a) refactor)

- **1A** Message/markdown renderer · **1B** Composer wiring · **1C** Citations · **1D** Subagent/fleet cards · **1E** Approval card + receipt + routing · **1F** WorkspacePane.
- Gate each: `apps/frontend` typecheck + tests green; zero web behavior change.

### Phase 2 — Shell & IA (6-dest solo)

- **2A** Apply v2 tokens/fonts to `ChatShell` + `AppRail` (48px icon-only) + `Topbar` (46px).
- **2B** `destinations.ts` → profile-gated set; solo default `run`; relabel Tools/Skills.
- **2C** Settings entry in rail foot (`onOpenSettings` wired on desktop) + avatar.
- **2D** Fold/route home/library/inbox/todos/routines/agents (Activity absorbs agents+inbox).
- **2E** Remove `DesktopPlaceholder` from mount; wire the destination outlet.

### Phase 3 — Run cockpit (Track C flagship)

- **3A** Mount `ThreadCanvas` as Run (center-surface / right-rail / bottom-timeline).
- **3B** Right-rail tabs `[Chat · Sources · Agents · Approvals]` (WorkspacePane).
- **3C** Studio/Focus modes (drop Auto) + `⌘M`.
- **3D** Timeline lanes + scrub + subagent lanes/fleet cards.
- **3E** Streaming: Streamdown (chat) + surface-renderers snapshots (center).
- **3F** Approvals: on-surface inline + in-chat ApprovalCard.
- **3G** Run empty/idle + multi-run states.

### Phase 4 — Remaining destinations

- **4A** Chats (archive → reopen Run) · **4B** Projects · **4C** Activity (recast) · **4D** Tools (connectors + connect flow) · **4E** Skills (catalog).

### Phase 5 — Settings (solo, profile-gated) — realizes the Settings-redo

- **5A** Settings shell (rail-foot; nav Account / Models & keys / Data & privacy / Notifications / Advanced; solo footer).
- **5B** Account: Profile · Appearance (theme/accent/density/reduce-motion) · Shortcuts.
- **5C** Models & keys: Provider keys (BYOK — consolidate the web `ProviderKeys`), **Local models (NEW, Ollama runtime)**, Model & behavior (default model cloud/local, depth, web, **approval policy** read/write/danger, spend cap).
- **5D** Data & privacy (memory review/export/delete/retention) · Notifications (single consolidated surface) · Advanced (Keychain/Touch-ID lock, dev tokens).
- **5E** Gate Workspace/Members/Billing behind `team`; retire the old 1,400-line settings path on desktop.

### Phase 6 — Palette, polish, verify

- ✅ **6A** Command palette (`⌘K`) → nav + settings + actions.
- ✅ **6B** Keyboard shortcuts (SHORTCUTS set) + reduce-motion + density.
- ✅ **6C** Remove dead code (DesktopPlaceholder, superseded route-table palette).
- ⏳ **6D** End-to-end **live** desktop smoke (boot → run → approve → scrub → settings → BYOK/local model) per `apps/desktop/SMOKE.md`. _(Unit fakes have hidden real-run breakage before — smoke live.)_ Procedure written; **pending the operator's live run** (result recorded in `SMOKE.md`).
- ✅ **6E** READMEs/docs (`apps/desktop/README.md`, `apps/desktop/SMOKE.md`, this §11).

## 9. Sequencing & dependencies

`0` → then `1` and `2` can largely parallelize → `3` (needs 1+2) is the critical path → `4` (needs 2), `5` (needs 0D+2) → `6`. Audit after every **2 merged phases** (verify, update READMEs/tests, find + fix gaps) before the next pair.

## 10. Risks & mitigations

| Risk                                              | Mitigation                                                              |
| ------------------------------------------------- | ----------------------------------------------------------------------- |
| Hoisting web components regresses the web app     | Incremental, re-export shims, keep typecheck+tests green, one family/PR |
| `chat-surface` framework-agnostic invariant       | Ports only; ESLint substrate rules (0E)                                 |
| Desktop tokens/fonts unwired today                | Phase 0A first                                                          |
| Local-models UI is net-new + needs Ollama runtime | Own workstream (5C); may depend on `feat/openrouter-local-models`       |
| Deployment-profile gating sprawls                 | One `DeploymentProfile` port (0D)                                       |
| Run empty/multi-run undefined in prototype        | Designed in 3G                                                          |

## 11. Definition of done

Status legend: ✅ shipped on `feat/desktop-redesign` · ⏳ pending the operator's
live run (docs/tooling in place, awaiting execution).

- [x] Desktop boots into the **6-destination solo shell** in v2 "quiet" styling with fonts loaded.
- [x] **Run** mounts the real `ThreadCanvas` cockpit (Studio/Focus, timeline scrub, inline + in-chat approvals, streaming), through the real `DestinationOutlet`.
- [x] The advanced composer (real models incl. custom OpenRouter, attachments, connectors, skills) works.
- [x] **Settings** solo surface with BYOK + local models + approval policy; **team features gated off** (`single_user_desktop`).
- [x] **`⌘K` command palette** wired (6 destinations + 3 settings sections + 4 actions) with the full `DESIGN-SPEC.md` §6 keyboard-shortcut set (SSOT `shell/shortcuts.ts`, input-guarded).
- [x] **No `DesktopPlaceholder`** and no superseded second palette — `grep DesktopPlaceholder` / `RouteJumpPalette` returns zero (dead code removed, PR-6.1/6.7).
- [x] **design-system is the only token source.**
- [ ] ⏳ End-to-end **live** desktop smoke passes (boot → run → approve → scrub → settings → BYOK → local model) with a console/CSP-clean session. Procedure is written in [`apps/desktop/SMOKE.md`](../../../apps/desktop/SMOKE.md); the operator runs it against `make dev` and records the result table there. **Docs do not claim the live run was performed** — this box is ticked only after the operator's walk is clean.

## 12. Execution model

Work in this worktree. Per phase: 5–9 narrow subagents, each in its **own** worktree branching **before** first change, never writing to the main repo path; clean up worktrees after merge. Audit cadence every 2 phases. Autonomous PR execution across the approved set; stop only at a real architectural/risk boundary.

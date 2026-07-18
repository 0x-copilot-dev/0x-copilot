# 0xCopilot Desktop — redesign live smoke

Manual end-to-end recipe for the **6-destination solo redesign** (desktop
redesign Phase 6). A later phase will automate this through a Playwright
harness; for now the contract is "a human can walk this without surprises".
If a step doesn't behave as written, file a bug **referencing the step
number** — the renderer / main split is intentional and breakage usually
points at a single seam.

This smoke is the DoD gate for the redesign's `⌘K` palette, shortcut set,
and destination-outlet mount. Unit fakes have hidden real-run breakage
before (see the Virtuals launch effort), so this walk must be run **live**
against a real stack, not simulated.

## Prerequisites

- Backend stack running locally via `make dev` from the repo root.
  - `backend:8100`, `ai-backend:8000`, `backend-facade:8200`, `frontend:5173`.
- `BACKEND_ENVIRONMENT=development` set on the backend so the dev IdP at
  `POST /v1/dev/identity/mint` is registered.
- `ENTERPRISE_AUTH_SECRET` set (same secret the facade verifies with).
- A provider key on the backend (`OPENAI_API_KEY` in
  `services/ai-backend/.env`) so a run can actually stream tokens.

## Launch

Point the desktop at the running facade and use the dev-mint auth mode. This
is the plain (unsupervised) dev boot — the facade already runs from
`make dev`, so the desktop does **not** start its own embedded Postgres /
services (that supervised path is `COPILOT_RUNTIME_DIR`, out of scope here).

```bash
COPILOT_AUTH_MODE=dev-mint \
  COPILOT_FACADE_URL=http://127.0.0.1:8200 \
  COPILOT_DEV_PERSONA=sarah_acme \
  npm run dev --workspace @0x-copilot/desktop
```

`COPILOT_AUTH_MODE=oidc` would route through the real authorization-code
flow instead; `dev-mint` is the local mode (a header-less HMAC mint, no
browser). Without `COPILOT_FACADE_URL` the renderer falls back to
`MockTransport` and steps 2–7 below won't hit a real backend — set it.

## Steps

Each step is **action → expected → checkbox**. Tick the checkbox only when
the expected result matches exactly.

### A. Boot lands on the Run six-destination shell

1. **Launch → sign in.**
   - Electron window opens; `SignInGate` renders its sign-in card.
   - Click `Sign in` (dev-mint route). The main process POSTs to
     `http://127.0.0.1:8200/v1/dev/identity/mint`; on success the gate swaps
     to render `ChatShell`.
   - **Expected:** the shell renders with the **48px icon rail** on the left
     showing exactly six destinations — **Run · Chats · Projects · Activity ·
     Tools · Skills** — plus the **Settings gear + avatar in the rail foot**.
     The app lands on **Run** (the flagship cockpit is the front door). No
     `DesktopPlaceholder` / "coming soon" pane anywhere.
   - [ ] Shell boots into Run with the 6-destination rail; no placeholder.

2. **Confirm solo profile gating.**
   - **Expected:** the rail shows only the six solo destinations — no `Team`,
     `Members`, `Billing`, `Home`, `Library`, `Inbox`, `Todos`, or `Routines`
     entry. The build is seeded `single_user_desktop`
     (`DeploymentProfileProvider`), so team destinations are gated off.
   - [ ] Only the six solo destinations render; no team/legacy entries.

### B. Start a goal in the Run empty-state → live run streams

3. **Run empty-state → give it a goal.**
   - On Run with no active run, the cockpit shows the honest goal composer
     (`RunEmptyState`, "Give it a goal…") — not a blank canvas.
   - Type a goal (e.g. `Summarize the last three commits on this repo`) and
     submit.
   - **Expected:** a run starts and binds in place (the live layout appears
     without remounting the shell). Studio layout shows the center **work
     surface**, the right **tabbed rail `[Chat · Sources · Agents ·
Approvals]`**, and the bottom **timeline**.
   - [ ] Goal submit starts a run and the Studio cockpit layout appears.

4. **Watch it stream across all three surfaces.**
   - **Expected:** model tokens stream into the **Chat** rail tab; the
     **center surface** updates as the agent produces output; the **timeline**
     grows beads live (LIVE indicator). No console error during the stream.
   - [ ] Tokens stream into chat + surface + timeline concurrently.

5. **Scrub the timeline, then snap live.**
   - Drag the timeline scrubber back (or press `⌘←` while Run is focused).
   - **Expected:** a VIEWING banner appears, approvals are hidden while
     rewound; pressing `⌘L` (or the live control) snaps back to LIVE.
     _(These run-scoped chords are owned by the Run cockpit's own listeners,
     not the shell shortcut hook — they fire only while Run is active.)_
   - [ ] Timeline scrub shows VIEWING; `⌘L` snaps back to LIVE.

6. **Approve an on-surface diff (if the run surfaces one).**
   - If the run produces a structured artifact with a pending diff, an inline
     Approve control renders on the surface (or a 4-zone `ApprovalCard` in
     chat).
   - Click `Approve` (or press `⌘↵` while Run is focused).
   - **Expected:** the diff transitions to approved and the timeline bead
     flips to the jade signed state. _(If this run produces no approval, note
     "n/a — no diff surfaced" and move on; do not fail the step.)_
   - [ ] On-surface Approve signs the diff (or n/a — noted).

### C. Navigate each of the six destinations via the rail

7. **Click through Chats → Projects → Activity → Tools → Skills → Run.**
   - Click each rail icon in turn.
   - **Expected:** each renders its **real** Phase-4 surface fed by the
     desktop binder over the Transport port — never a placeholder:
     - **Chats** → the archive list (pinned / recent / archived buckets).
     - **Projects** → the project grid.
     - **Activity** → the run-history feed (agents + inbox folded in).
     - **Tools** → connected connectors + catalog (slug `connectors`).
     - **Skills** → the skill catalog (slug `tools`).
     - **Run** → back to the cockpit (the started run is still bound).
   - Each surface honestly shows its own loading → ok / empty / error state;
     an empty backend renders the surface's empty state, not a crash.
   - [ ] All six destinations render real surfaces (no placeholder); Run
         preserves its bound run.

### D. ⌘K command palette

8. **Open the palette + navigate.**
   - Press `⌘K` (or click the topbar `Search… ⌘K` trigger; the trigger is
     suppressed on Run and Settings, so click it from e.g. Chats).
   - **Expected:** the palette modal opens, the search input autofocuses, and
     the empty-query starter list shows three groups — **Navigation**
     (Go to Run / Chats / Projects / Activity / Tools / Skills), **Settings**
     (Model & behavior, Appearance, Open Settings), and **Actions** (New chat,
     Add a provider key, Download a local model, Connect a tool).
   - Type `act` → only **Go to Activity** matches. Select it and press
     `Enter`.
   - **Expected:** the palette closes and the shell navigates to **Activity**.
   - [ ] `⌘K` opens the palette; "Go to Activity" navigates and closes.

9. **Launch an action from the palette.**
   - Press `⌘K`, select **Add a provider key**, press `Enter`.
   - **Expected:** the palette closes and Settings opens focused on the
     **Provider keys** section. (Likewise **Download a local model** →
     Local models; **Connect a tool** → Tools; **New chat** → a fresh Run.)
   - Press `Esc` from an open palette → it closes and focus returns to the
     prior element.
   - [ ] A palette action launches its flow; `Esc` closes cleanly.

### E. Keyboard shortcuts (global chords)

10. **Exercise the five global chords.**
    - From the shell (focus **not** in a text input), press each:
      - `⌘,` → opens Settings at the default section (Profile).
      - `⌘⇧M` → opens Settings focused on **Local models** (the model picker).
      - `⌘N` → starts / opens a new Run.
      - `⌘⇧F` → navigates to **Activity** (search-activity interim).
      - `⌘K` → toggles the palette (exactly once per press — no double toggle).
    - **Expected:** each chord fires its intent. Now focus the Run composer
      and type a sentence containing `n` and `,` — **Expected:** `⌘N` does not
      steal focus mid-word (input guard), while `⌘K` / `⌘,` remain available
      from inside inputs by platform convention.
    - [ ] Global chords fire; input guard protects the composer; `⌘K` toggles
          once.

### F. Settings sections (solo) + team gating

11. **Walk the solo Settings sections.**
    - Open Settings (gear in rail foot, or `⌘,`). Settings owns full height —
      the topbar / context / right-rail are suppressed while it's active.
    - **Expected:** the nav shows the solo groups — **Account** (Profile,
      Appearance, Shortcuts), **Models & keys** (Provider keys, Local models,
      Model & behavior), **Data & privacy** (Privacy & retention,
      Notifications, Audit log), **Advanced** (Key storage & app lock,
      Developer tokens) — plus the **solo footer**.
    - Open each of these and confirm it renders:
      - **Appearance** → theme + accent + density + reduce-motion controls.
      - **Provider keys** → BYOK add/list (responses show only a key hint).
      - **Model & behavior** → default model, depth, approval policy.
      - **Privacy & retention** → memory review / export / retention.
    - **Expected:** switching sections does **not** full-remount the surface.
    - [ ] All four named solo sections render; section switch is in-place.

12. **Confirm team sections are gated OFF.**
    - **Expected:** the settings nav shows **no Workspace group** — no
      `Workspace` / `Members` / `Billing` sections on the solo profile. The
      solo footer is shown instead.
    - [ ] Workspace / Members / Billing absent on the solo profile.

### G. CSP intact (expected failure)

13. **The `fetch` CSP check still fails.**
    - Open DevTools console and run:
      ```js
      fetch("https://example.com");
      ```
    - **Expected:** the request **fails** (blocked by the `app://` per-response
      CSP with `connect-src 'none'`). A success here means the CSP is not
      being applied — investigate `apps/desktop/main/app-protocol.ts` first.
    - [ ] `fetch("https://example.com")` fails (CSP blocks it).

14. **Session is clean.**
    - **Expected:** no console errors and no CSP violations accumulated across
      steps 1–13 (the intentional step-13 block is the only `connect-src`
      report, and it is expected).
    - [ ] No unexpected console errors / CSP violations during the walk.

## Live run — RESULT: _to be recorded_

The operator runs the walkthrough above against a live `make dev` stack and
records the outcome here. **This table is a placeholder** — the docs PR does
**not** claim a live run was performed. Fill `Pass/Fail` per step and cite a
bug id in `Notes` for any failure.

- **Date:** _to be recorded_
- **Operator:** _to be recorded_
- **Stack:** `make dev` @ commit _to be recorded_ · desktop `npm run dev` (dev-mint)

| Step | Description                                    | Pass/Fail | Notes |
| ---- | ---------------------------------------------- | --------- | ----- |
| 1    | Boot lands on Run 6-dest shell, no placeholder |           |       |
| 2    | Solo profile — no team/legacy destinations     |           |       |
| 3    | Run empty-state goal starts a run              |           |       |
| 4    | Run streams (chat + surface + timeline)        |           |       |
| 5    | Timeline scrub VIEWING → `⌘L` snaps live       |           |       |
| 6    | On-surface Approve signs the diff (or n/a)     |           |       |
| 7    | All six destinations render real surfaces      |           |       |
| 8    | `⌘K` palette opens; "Go to Activity" navigates |           |       |
| 9    | Palette action launches flow; `Esc` closes     |           |       |
| 10   | Global chords fire; input guard holds          |           |       |
| 11   | Solo Settings sections render in-place         |           |       |
| 12   | Team sections gated off                        |           |       |
| 13   | CSP `fetch` still fails                        |           |       |
| 14   | No unexpected console errors / CSP violations  |           |       |

## What else to verify (carried over)

- **Bearer never reaches renderer state.** Open DevTools and inspect
  `window.bridge` and React component state. The `RendererSession` view has
  `workspaceId`, `expiresAt`, `displayName`, `email` — no bearer. The bearer
  is attached in main on every outbound HTTP request (PRD §6.7 / D24).
- **On-disk secret shape (D24 / PRD §6.7).** The secrets directory layout is
  `{userData}/secrets/{workspace_id}/{server_kind}/{server_id_hash}.bin`. Cat
  one `.bin`: the first bytes are `ATLASv1:cipher:` (or
  `ATLASv1:plaintext:` only in the dev fallback when
  `safeStorage.isEncryptionAvailable()` is false). The plaintext bearer must
  not appear under `cipher:`.

## Out of scope for this smoke

- Real OIDC provider integration (dev-mint is the local mode).
- The supervised packaged boot (embedded Postgres + three services via
  `COPILOT_RUNTIME_DIR`) — see `apps/desktop/README.md` and
  `tools/desktop-runtime/README.md`.
- Full e2e automation (a later phase rewrites this doc into a Playwright spec).
- Local-model download actually pulling an Ollama model end-to-end (the
  palette/settings entry-point is smoked here; the runtime pull is its own
  workstream).

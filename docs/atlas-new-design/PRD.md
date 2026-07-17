# Atlas Workspace UI — Product Requirements

**Status:** draft, in flight (Wave 1 dispatched 2026-05-17)
**Owner:** parth
**Design source of truth:** Claude Design handoff bundle at `/tmp/atlas-design/0x-copilot-template/` — specifically `project/Atlas Workspace.html` and the iteration transcript in `chats/chat1.md` (lines 240–820 are the load-bearing decisions).

---

## 1. What this document is for

Pin down what the Atlas Workspace UI is, in our codebase, before we keep changing it. The dev frontend currently shows hardcoded placeholders, the rail navigation is broken, and the chat surface has two competing sidebars. That's the symptom. The cause is that we ported the chat-surface piecemeal and never wrote down what the full workspace was supposed to be — so each port made local-optimal decisions that compound into the wrong shell.

This PRD is the workspace's source of truth. Every UI agent we dispatch reads this and matches it.

## 2. Product premise (one paragraph)

A thread is **not a chat log**. It's a working session where Atlas operates across the user's SaaS surfaces on their behalf — drafting emails in Gmail, inserting rows into Salesforce, modifying slides, querying databases. The chat is the interface _to_ the session; the session itself is the cross-surface work. The workspace UI exists to make three things one-click: **(1) approve/reject Atlas's pending edits**, **(2) see at a glance what Atlas is touching right now**, **(3) reply/steer**. Everything else (mode switching, history scrub, branching, pinning) is secondary.

The framing inversion that matters: in ChatGPT/Claude.ai, the chat _is_ the thread and artifacts are children of messages. In Atlas, **the work is the thread**, and messages are one of several event kinds that land in it (alongside `read`, `edit`, `draft`, `send`, `query`).

## 3. What the user sees right now (the four shipped regressions)

These are the concrete complaints from the most recent dev-stack walkthrough. Each is a P0 for Phase 1.

1. **`Filter row 1 / Filter row 2 / Filter row 3`** — a hardcoded demo placeholder in `packages/chat-surface/src/shell/ContextPanel.tsx`. Renders for every destination including chats.
2. **`Atlas conversation / Placeholder message 1/2/3`** — the chat-surface fallback rendered when no real chat is active. Looks like product copy; isn't.
3. **AppRail buttons no-op except "Chats"** — `routeForDestination()` in `packages/chat-surface/src/shell/AppRail.tsx` returns a non-null target only for `chats`. Every other icon is dead. Clicking Home doesn't navigate anywhere; the body still shows whatever `AppRoute.screen` says (often Settings).
4. **Double sidebar on Chats** — the workspace `ContextPanel` (224px) renders next to the chat app's own thread list. Two competing list columns. The design's answer (`os.css:596-611`) is that Chats is full-bleed: when destination=chats, the workspace's ContextPanel collapses and the chat brings its own sidebar.

Underneath those four: **color tokens are duplicated.** Every shell component and destination hardcodes its own hex palette inline (`#0E1015`, `#22252E`, `#7B9BFF` …) instead of consuming `packages/design-system/src/styles.css`. The user's chosen theme/accent in Settings → Appearance doesn't propagate.

## 4. Architectural principles (non-negotiable)

These come from the user's standing instruction ("Think from architectural design perspective. Assume you are a staff engineer, know all system design principles") and they are how we evaluate every PR in this initiative.

- **Single source of truth.** One `Composer`, one token system, one route shape, one `ShellDestinations` array. If duplication appears, the second copy is the bug.
- **DRY through substitution.** If two components solve the same problem with different code, one of them is wrong. Generic shell components (`ContextPanel`, `Topbar`) take props; per-destination content lives in the destination.
- **Simple & elegant > clever.** Prop-drilling beats global state when there are 1–2 hops. CSS classes that read tokens beat a wrapper component that takes a `color` prop.
- **Performant by default.** No re-renders on every keystroke. Popovers portal once; route updates don't re-mount the shell.
- **User & UX first.** If a layout decision conflicts with how the user actually works, the layout loses. Approvals must be one click. Pulse must be visible without scrolling. The composer hint row renders even during a run.

## 5. Source-of-truth map

This is the table that says _which file owns what_. Anything not on this list is an alias or a consumer.

| Concern                        | Canonical file                                                           | Notes                                                                                                                                                                           |
| ------------------------------ | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Color / density / theme tokens | `packages/design-system/src/styles.css`                                  | Existing oklch-aware hex token set; switched via `:root[data-theme]` and `:root[data-density]`. Every consumer reads `var(--color-*)`. Hex literals outside this file are bugs. |
| Workspace destinations enum    | `packages/chat-surface/src/shell/destinations.ts` (`SHELL_DESTINATIONS`) | Ordered list of 11. AppRail / ContextPanel / Topbar / route encoder all read from this.                                                                                         |
| Route shape                    | `apps/frontend/src/app/routes.ts` (`AppRoute`)                           | Web-app extension of `ArtifactRoute`. Gains `destination: ShellDestinationSlug`.                                                                                                |
| URL ↔ route adapter            | `apps/frontend/src/app/HashRouter.ts`                                    | Encodes/decodes `destination`. Round-trips.                                                                                                                                     |
| Workspace shell                | `packages/chat-surface/src/shell/ChatShell.tsx`                          | Lays out rail · ctx-panel · main · right-rail. `data-destination` attribute drives CSS-level layout switches (full-bleed chats).                                                |
| Composer (web + desktop)       | `packages/chat-surface/src/composer/Composer.tsx`                        | One composer for Studio + Focus + Auto. **The `apps/frontend/src/features/chat/runtime/composer/` variant is deprecated and to be deleted in Wave 2.**                          |
| Thread canvas (3 modes)        | `packages/chat-surface/src/thread-canvas/ThreadCanvas.tsx`               | Studio / Focus / Auto modes. Owns the swimlane timeline + per-app surface mount. Approvals are inline per-surface (no global queue).                                            |
| Per-destination main           | `packages/chat-surface/src/destinations/{slug}/{Slug}Destination.tsx`    | Already exist; their internal content (lists, panels) is destination-owned.                                                                                                     |
| Right rail                     | `packages/chat-surface/src/shell/RightRail.tsx`                          | Activity (default) / Approvals tabs when destination=chats. Hidden or empty for other destinations.                                                                             |
| ⌘K palette                     | `packages/chat-surface/src/shell/CommandPalette.tsx` _(to be created)_   | Mirrors `os-routing.jsx:CommandPalette`. Indexes destinations + actions + chats + projects + library + agents + tools + people + inbox.                                         |

## 6. Shell anatomy (the layout we're committing to)

```
┌──────┬────────┬────────────────────────┬───────────┐
│ 52   │ 224    │     1fr (main)         │  380 / 0  │
│ rail │ ctx-   │  ┌────────────────┐    │ right rail│
│      │ panel  │  │ Topbar  44px   │    │           │
│      │ (per   │  ├────────────────┤    │           │
│      │  dest) │  │ DestinationMain│    │           │
│      │        │  │   or ChatScreen│    │           │
└──────┴────────┴────────────────────────┴───────────┘
            (collapses to 0 when destination=chats)
```

**Rules:**

- Always: 52px rail on the left.
- When `data-destination="chats"`: ContextPanel column = 0, RightRail toggleable, main = chat surface (which brings its own sidebar). Grid: `52px 1fr [380|0]`.
- When `data-destination !== "chats"`: ContextPanel = 224px with destination-supplied content. Grid: `52px 224px 1fr [380|0]`.
- Topbar always 44px, always present, breadcrumb on left, ⌘K trigger center, presence + admin badge + profile menu on right. (For chats, breadcrumb shows project › thread; the chat app's old topbar dedupes with this one — drop the chat's internal topbar inside the shell.)
- The chat-surface's internal `sidebar__head` (Atlas A brand) and `sidebar__user` (footer) hide inside the ChatShell (one rail, not two — mirrors `os.css:604-611`).

## 7. Destinations

11 destinations, ordered per `SHELL_DESTINATIONS`. Each has a context panel and a main view. The design demotes 6 of them to the profile menu in the topbar; we keep all 11 in the rail for now (deferred decision — see §13).

| Slug       | Context panel                                            | Main view                                                                                           | Wired in Wave                                              |
| ---------- | -------------------------------------------------------- | --------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| home       | (none — wide layout)                                     | Morning briefing: pinned chats, recent runs, favorite tools, agent activity feed. Reads `/v1/home`. | Foundation exists; copy + sectioning per design in Wave 2. |
| chats      | (full-bleed; chat brings its own sidebar)                | Thread canvas (Studio/Focus/Auto) for the active conversation. Welcome state when none.             | Wave 2 — modes + canvas.                                   |
| inbox      | Filter chips: All / Mentions / Approvals / Errors / Done | Agent↔human↔agent message stream. Click → opens originating run.                                    | Wave 2.                                                    |
| todos      | Sections: Today / Overdue / This week / Done             | Todo list with source attribution (extracted from chat / user / agent).                             | Wave 2.                                                    |
| projects   | Project list with star + nested thread counts            | Project detail: members, agents, threads, library refs, todos.                                      | Wave 3.                                                    |
| library    | Tabs: Files / Pages / Datasets                           | Doc/page/dataset detail.                                                                            | Wave 3.                                                    |
| agents     | Categories: Yours / Workspace / Marketplace              | Agent detail: skills, MCPs, memory, runbook history.                                                | Wave 3.                                                    |
| tools      | Categories: MCPs / APIs / Built-ins                      | Tool detail + onboarding wizard for new APIs.                                                       | Wave 3.                                                    |
| connectors | Connected / Disconnected sections                        | Connector detail: scope, last sync, per-chat overrides.                                             | Wave 3 (existing OAuth flow already lives here).           |
| team       | People list                                              | Person detail: agents they own, presence, recent activity.                                          | Wave 4.                                                    |
| memory     | Categories: Skills / Facts / Preferences                 | Memory detail: created by (user/agent), last used, scope.                                           | Wave 4.                                                    |

Each destination is a self-contained `{Slug}Destination.tsx` that owns its own data fetch (via `useTransport()`), its context panel (which it exports as a sibling component the shell renders into the panel slot), and its main view. **Generic shell wrapper (Topbar, ContextPanel chrome) is shared; content is local.** That's the DRY split.

## 8. Chats — three modes

The thread canvas (when destination=chats and a thread is selected) has **three modes**, settling chat1.md lines 326-345:

| Mode       | Surface                       | Chat                                   | Timeline                                                 | Approvals                                                          |
| ---------- | ----------------------------- | -------------------------------------- | -------------------------------------------------------- | ------------------------------------------------------------------ |
| **Studio** | Top, takes most of the height | Bottom sheet, above the timeline       | Horizontal strip + per-app swimlanes (color-coded beads) | Inline in surface (pending blocks with Accept / Reject / Edit)     |
| **Focus**  | Top, more height              | Composer-only bar (no message history) | Mini horizontal strip with pulse status row above        | Right-rail Activity/Approvals tabs                                 |
| **Auto**   | Hidden                        | Fills canvas, message stream           | Hidden                                                   | Cards render as "auto-applied" — no Approve/Reject, just narration |

**Hard rules:**

- **No global approval queue.** Approvals live inline in the surface where they apply. The diff card is the entire approval UI.
- **Timeline is git.** Beads = commits. Drag the playhead → surfaces freeze at that moment; chat keeps streaming live. "Restore this state" / "Branch from here" / "Snap to now" controls in the floating viewing card.
- **Mode switcher is a posture dial, not a layout picker.** Labels: "Show me everything" (Studio), "Get out of my way" (Focus), "Run it, don't ask" (Auto).
- **Studio → Focus minimize button** sits in the swimlane header (chevron pointing down per chat1.md line 800). Focus → Studio expands via the mini-timeline's expand affordance.
- **Pulse strip in Focus**: persistent one-line status above the mini timeline: `● Atlas · drafting message in Email · 11:44:00`. Bead glow alone is too quiet.
- **Auto mode top banner**: `● Auto · 14 actions applied across 3 surfaces · view in Studio` — the bridge back. No timeline visible.

## 9. Composer

**One Composer**, used in all three modes, plus in the welcome state. Canonical path: `packages/chat-surface/src/composer/Composer.tsx` (rebuilt 2026-05-17 in worktree `agent-ac711ddb79945057d` to design parity).

Shape:

- 2-row textarea (`rows={2}`, ~56px), single thin action row.
- Left side of action row: **Tools** popover (Skills + MCPs as sections in one popover, not two buttons) · **Model · Depth** popover (model list + Fast/Balanced/Deep grid) · attach icon · mic icon.
- Right side: send icon → swaps to cancel/stop icon when a run is active.
- Below: hint row `↵ send · ⇧+↵ new line · / skills · model · Sources cited inline`. **Always renders, even during a run** (pinned by test; this was a real shipped regression — see `apps/frontend/CLAUDE.md` §"Composer hint row").

**Deprecated:** `apps/frontend/src/features/chat/runtime/composer/Composer.tsx` is a second composer that diverges. It ships extras (selected-skills pills, `ComposerHandle`, attachments, `ComposerSendButton`, edit-composer) that the chat-surface composer doesn't model yet. **Wave 2 must migrate `ChatScreen` to consume the chat-surface composer and delete the frontend variant.** Until then, both exist and the chat-surface one is canonical for new work.

## 10. Right rail

- **Chats** destination, with an active thread: tabs **Activity** (default) and **Approvals**. Activity is the ChatGPT-style stream of think/MCP/tool/output/streaming-draft entries. Approvals lists the pending inline diff cards (a summary; the actual approve still happens inline in the surface). Mostly relevant in Focus mode where the surface is loud.
- **Chats** destination, no active thread (welcome state): rail collapsed.
- **All other destinations**: rail can render destination-specific context (e.g. inbox preview, project sidecar) when useful; otherwise collapsed. Default to collapsed; explicit opt-in per destination.

Collapse state survives reload (keyed in `KeyValueStore`).

## 11. Topbar + ⌘K + profile menu

Mirrors `os-shell.jsx:TopbarOS`:

- Left: breadcrumb (`Destination › View › Item`). For chats: `Project › Thread`.
- Center: ⌘K trigger ("Search anything…").
- Right: action slot (per-destination) · admin badge · inbox bell with unread count · presence stack · profile menu.

⌘K palette indexes everything navigable: destinations, actions ("New chat", "New library page", "Onboard an internal API", "Build an agent", "Invite a teammate"), chats, projects, library, agents, tools, people, inbox. Match against title + sub + kind, deduped, grouped. Keyboard: ↑↓ navigate, ↵ open, Esc close.

Profile menu (right of topbar) holds: workspace identity, the demoted destinations if §13 lands that way, settings, admin toggle, switch persona.

## 12. Phase plan

### Wave 1 — Shell foundation (IN FLIGHT — dispatched 2026-05-17)

Three agents in parallel, isolated worktrees:

- **α — shell foundation** _(in flight)_: kills `Filter row 1/2/3`, fixes AppRail nav (every button works), full-bleed chats layout, extends `AppRoute` with `destination`, renders real `{Slug}Destination` components from `App.tsx`, tokenizes shell hex → design-system vars. Owns `chat-surface/src/shell/**` + `apps/frontend/src/app/**`.
- **β — destinations tokens** _(✅ done, branch `worktree-agent-destinations-tokens`)_: 12 files, ~107 hex → 12 existing design-system tokens. Zero new tokens added. 363/363 tests pass.
- **γ — composer parity** _(✅ done, branch `worktree-agent-ac711ddb79945057d`)_: Composer rebuilt to design's "2-row textarea + thin action row" shape. Tools + Model·Depth popovers. Hint row pinned. 377/377 tests.

**Acceptance for Wave 1:**

- Zero hex literals anywhere in `packages/chat-surface/src/{shell,destinations,composer}/**` (test: `grep -rE '#[0-9A-Fa-f]{3,8}' --include='*.tsx' packages/chat-surface/src/{shell,destinations,composer}/`).
- Clicking any AppRail item navigates and renders the matching destination.
- `data-destination="chats"` on the shell collapses the ContextPanel column.
- No `Filter row N` strings reachable from any destination.
- All chat-surface + frontend tests green.

### Wave 2 — Destinations content + composer consolidation

- Migrate `ChatScreen` to consume `packages/chat-surface/src/composer/Composer.tsx`; delete `apps/frontend/src/features/chat/runtime/composer/`. (See §9.)
- Add `reasoning_depth: "fast" | "balanced" | "deep"` to `packages/api-types/` and the AI-backend run-request contract. Wire the composer's `selectedDepth` through.
- Add `kind: "skill" | "mcp"` tagging on `/v1/mcp/tools` so the Tools popover's Skills section populates.
- Home destination: morning briefing per `dest-home.jsx` (pinned chats, recent runs, favorite tools, agent activity feed). Real `/v1/home` data.
- Inbox destination: filter chips + agent↔human stream. Reads existing inbox events.
- Todos destination: extracted action items + manual todos. KV-store backed for now.
- Right rail Activity/Approvals tabs (chats destination).

### Wave 3 — Projects + Library + Agents + Tools + Connectors detail views

Each becomes a full destination with context panel + main + detail routes. Connectors destination absorbs the existing OAuth flow.

### Wave 4 — Team + Memory + admin overlays

Team page (people + their agents). Memory destination (skills/facts/preferences). Admin badge + governance overlays surface across the workspace.

### Wave 5 — Thread canvas (3 modes)

Studio / Focus / Auto with the swimlane timeline, inline approvals, git-style scrub, branch/restore. **This is the headline feature** but it's last because it's only useful once destinations + composer + right rail are correct.

### Wave 6 — ⌘K palette + mention typeahead

Search + navigate everything from one input. Mirrors `os-routing.jsx`.

## 13. Open decisions

These need a call before the relevant wave lands. Flagging them so we don't accidentally invent answers.

1. **11 rail items vs 5 rail + 6 profile-menu?** The design (`os-shell.jsx:13-20`) demotes Library / Agents / Tools / Connectors / Team / Memory to a profile-menu dropdown, keeping just Home / Projects / Chats / Inbox / Todos in the rail. Cleaner glanceability; harder to discover the demoted six. Wave 1 keeps all 11 in the rail (lowest-risk option); we revisit before Wave 4.
2. **Per-thread canvas drives surface choice — but how does Atlas pick which surfaces appear in a new thread?** Today (in our codebase): nothing. The design hardcodes the Acme renewal thread to show Salesforce/Sheets/Email/Slides. Real version needs an Atlas-side proposal mechanism. Wave 5 question.
3. **Multiplayer threads.** "When a teammate joins the Acme thread, do they see the same approval queue? Whose Atlas is it?" — chat1.md line 301. Not in scope for any wave currently; product call needed.
4. **Resumable vs ephemeral threads.** When you open a 2-week-old thread, do surfaces show their state _then_ (frozen) or refresh to current state and the timeline becomes a history view? Wave 5 question.
5. **`Depth` in run contract.** `Fast/Balanced/Deep` is composer-local right now. Wave 2 adds it to the wire. Decision: model-side mapping or runtime-side budget? Likely runtime-side (timeout + max-tokens + tool budget per depth tier) — but the AI-backend owner signs off.

## 14. Anti-goals (what we are explicitly NOT building)

- **Not a chat with attached "tools".** The chat-with-side-panel framing flattens cross-surface work into the conversation.
- **Not a Slack channel.** Threads are bounded sessions, not continuous streams.
- **Not a Jira ticket.** No fixed schema; surfaces involved differ per thread.
- **Not a Notion page.** Artifacts live in their actual apps; the workspace doesn't embed copies.
- **Not Compose mode** (deleted from the design; Focus covers the same posture with the pulse strip — chat1.md line 319).
- **Not a global approval queue.** Approvals are inline.
- **Not keyboard-shortcut-heavy.** The user's pushback (chat1.md line 383): "These guys are not coders who remember shortcuts or use cli." Hover affordances + buttons + ⌘K. No vim-style chords.

## 15. References

- `/tmp/atlas-design/0x-copilot-template/chats/chat1.md` — design transcript (lines 240–820 load-bearing).
- `/tmp/atlas-design/0x-copilot-template/project/os.css` — workspace layout primitives.
- `/tmp/atlas-design/0x-copilot-template/project/os-shell.jsx` — AppRail / ContextPanel / TopbarOS shapes.
- `/tmp/atlas-design/0x-copilot-template/project/os-app.jsx` — composition + destination dispatch.
- `/tmp/atlas-design/0x-copilot-template/project/os-routing.jsx` — ⌘K + mention popover shapes.
- `/tmp/atlas-design/0x-copilot-template/project/composer.jsx` — design composer.
- `/tmp/atlas-design/0x-copilot-template/project/thread-canvas.jsx` — 3-mode canvas reference.
- [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md) — composer hint-row invariant + planning-pulse invariant.
- [`packages/chat-surface/CLAUDE.md`](../../packages/chat-surface/CLAUDE.md) (if/when it lands) — chat-surface engineering rules.
- [`docs/use-cases/01-…13-…md`](../use-cases/) — end-to-end use cases this UI must support without seams.

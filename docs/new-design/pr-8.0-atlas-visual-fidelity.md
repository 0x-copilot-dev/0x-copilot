# PR 8.0 — Atlas Visual Fidelity Pass

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 8, PR 8.0 — consolidates the visual fidelity gaps observed against the Atlas Design Doc into one coherent pass.
> **Owner:** frontend (rebuild [Topbar](apps/frontend/src/features/chat/components/shell/Topbar.tsx), [Sidebar](apps/frontend/src/features/chat/components/sidebar), assistant/user message rendering, harness/activity vocabulary, AskAQuestion card, composer chrome, accent tokens) · ai-backend (1 envelope kind: `compression_note`; surface fields on existing tool events; nothing else moves) · backend (zero — UI only) · design-system (extract 4 shared primitives that `chat/`, `settings/`, and `share/` already duplicate in spirit)
> **Size:** **L (frontend-heavy).** Net new code ≈ 750 LOC across the FE and ≈ 60 LOC on ai-backend. Net deleted: ≈ 350 LOC of duplicated chrome and ad-hoc styling. The PR ships **one** cross-cutting visual pass; everything is replaceable, scoped, and reuse-first.
> **Depends on:**
>
> - ✅ PR 1.1 citations live registry (shipped)
> - ✅ PR 1.2 per-chat connector scope (shipped)
> - ✅ PR 1.4 two-stage approvals (shipped)
> - ✅ PR 3.1 citation chips (shipped)
> - ✅ PR 3.2 workspace pane right rail (shipped)
> - ✅ PR 3.4 connector popover (shipped)
> - ✅ PR 5.1 login email-first IdP discovery (shipped)
> - 🟡 PR 2.1 topbar chrome / thinking depth — **drafted, not shipped**; this PR finishes its visual layer.
> - 🟡 PR 2.2 sidebar user card / keymap — **drafted, not shipped**; this PR finishes its visual layer.
> - 🟡 PR 2.3 welcome state / thread polish — **drafted, not shipped**; this PR finishes its visual layer.
>
> **Reads alongside:** [`pr-5.1-login-email-first-idp-discovery.md`](../../../docs/new-design/pr-5.1-login-email-first-idp-discovery.md) (PRD format), [`pr-2.1-topbar-chrome-thinking-depth.md`](../../../docs/new-design/pr-2.1-topbar-chrome-thinking-depth.md), [`pr-2.2-sidebar-user-card-keymap.md`](../../../docs/new-design/pr-2.2-sidebar-user-card-keymap.md), [`pr-2.3-welcome-state-thread-polish.md`](../../../docs/new-design/pr-2.3-welcome-state-thread-polish.md), [`pr-3.1-citation-chips-sources-tab.md`](../../../docs/new-design/pr-3.1-citation-chips-sources-tab.md), the Atlas Design Doc + prototype (`/tmp/design_pkg/extracted/enterprise-search/project/{Atlas Enterprise Search.html,shell.jsx,messages.jsx,composer.jsx,styles.css,Design Doc.html}`).
>
> **Sibling PRs (Wave 8):** PR 8.1 compression `NoteCard` event (Wave 1 PR A1 in the master plan), PR 8.2 subagent fleet grouping (PR A2), PR 8.3 self-fork from message (PR A3). These three carry **new server events / endpoints**; **PR 8.0 is purely the visual layer** and depends on none of them — but it leaves the renderer hooks in place so 8.1/8.2/8.3 land as one-line wire-ups.

---

## 0 · TL;DR

The chat surface already streams the right data (events, citations, approvals, sub-agents, drafts, sources). What's broken is the **rendering vocabulary** — the topbar carries the wrong controls in the wrong places, the sidebar advertises an internal scaffold name instead of the brand, harness rows render as fat green "Done" pills instead of inline check-tool-arg-result, the user message has no bubble, the assistant has no mark or footer, citations don't render as superscript chips, the AskAQuestion card has no option chips, and the composer placeholder + footer hints + stop button are off-spec.

| Surface                   | Today                                                                                                                                                                                                        | After this PR                                                                                                                                                                                                                                                                                                                                            |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Topbar                    | brand: ✨ assistant-ui · status pill: green "Waiting for permission…" · connectors: text "All paused ▾" · usage: empty horizontal line · model+depth: in topbar · ↑ icon (unknown) · settings · panel toggle | crumb: `Workspace › Folder` · title · status pill `Running / Ready / Idle` (accent-orange dot pulse on running) · **connectors stack**: 4 app-ic + caret + warn dot · **usage meter**: bar + `%` · **model pill** (small readout) · primary-outline `↗ Share` · settings ⚙ · panel toggle                                                                |
| Sidebar                   | text-only single-line items, raw user handle (`usr_sarahorg_acme`), refresh + undo header icons, no group labels, no live pulse                                                                              | brand `A · Atlas` + collapse toggle · `+ New chat` with `⌘N` kbd · search · grouped `Today / Yesterday / Earlier` · multi-line item (title + preview + timestamp + live-pulse) · user card `SC · Sarah Chen · Marketing Ops · Acme`                                                                                                                      |
| Composer                  | `Send a message... (@ to mention, / for commands)` · `+` only · `◇` glyph · big red **Stop** oval                                                                                                            | `Ask Atlas to find, summarize, or draft something for your team…` · `+` (attach) · `layers + count` (connectors) · `mic` · **`● Atlas Reasoning ▾`** model picker · **Thinking depth** (Fast/Balanced/Deep) · footer hints `↵ send · ⇧+↵ new line · / skills` + right-aligned `{model} · Sources cited inline` · stop = icon-flip inside the send circle |
| User message              | center block, no chrome                                                                                                                                                                                      | right-aligned bubble, slightly elevated surface                                                                                                                                                                                                                                                                                                          |
| Assistant message         | flush-left prose, no avatar, no footer                                                                                                                                                                       | flush-left prose with **orange `A` mark**, citations as superscript `¹²³` chips, **`Sources` strip** below finished blocks, footer with copy / 👍 / 👎 / regen + `{model} · {time}`                                                                                                                                                                      |
| Tool / harness            | every step is a full-width card with bright green `Done` pill                                                                                                                                                | inline `<check> tool(args) → result · 0:09` rows, dim color · multiple consecutive rows compress into an **Activity card** with summary line + caret to expand                                                                                                                                                                                           |
| AskAQuestion              | free-text only, loud blue glow border, green `Awaiting reply` pill                                                                                                                                           | `?` icon + question · option chips first · free-text fallback below · neutral border, accent-left rule · neutral `Awaiting reply` pill                                                                                                                                                                                                                   |
| Approvals tab             | pending only, body verbose                                                                                                                                                                                   | pending **and** decided list, summary line + connector glyph footer                                                                                                                                                                                                                                                                                      |
| Color / theme             | green/red ad-hoc, no orange brand presence                                                                                                                                                                   | Atlas orange `#d97757` for status dot, A mark, citation chips, primary buttons; `--success` only as a small chevron, never a fat pill                                                                                                                                                                                                                    |
| Compression / fleet hooks | nothing renders                                                                                                                                                                                              | renderer slot present (no payload yet — fed by PR 8.1 / 8.2)                                                                                                                                                                                                                                                                                             |

**The three principles**

1. **One vocabulary, used everywhere.** The app's chrome is currently spelled three different ways (chat shell, settings shell, share popover) — Atlas asks for one. We extract four primitives — `<StatusPill>`, `<ConnectorChip>`, `<AppIc>`, `<UsageMeter>` — into [`packages/design-system`](packages/design-system) and consume them from every surface. No surface ships its own re-skin.
2. **Existing privileged code paths are not rewritten.** `useThread` (assistant-ui), `eventReducer`, `citationsRegistry`, `useConversationConnectors`, `streamRunEvents`, `MfaPrompt`, `ApprovalTool`, `ConnectorAuthTool`, `SubagentTool`, `ProgressTool`, `ReasoningGroup`, `MessageSourcesStrip`, `SharePopover`, `useSettingsSection` — **all kept**. We only change _how they render_: tokens, slots, copy, group/wrap/collapse rules.
3. **Streaming stays the source of truth.** Every visual block we re-render is still driven by `RuntimeEventEnvelope.activity_kind` + `presentation`. We do not parse event-name prefixes on the FE. We do not stash inferred state in a UI-only side store. Anything the FE needs to render but doesn't have today (compression note, fleet grouping) gets a typed event in PR 8.1 / 8.2 — never a heuristic.

**LoC estimate:** frontend ≈ +750 / −350 (rebuild Topbar, Sidebar, ActivityRow grouping, AskAQuestion option chips, AssistantMessage mark + footer, UserMessage bubble, composer chrome, brand tokens) · ai-backend ≈ +60 (nothing here unless we want the renderer slot to be wired today; PR 8.1/8.2/8.3 cover the new payloads) · backend ≈ 0 · design-system ≈ +180 (the 4 extracted primitives + the tokens) · api-types ≈ 0.

---

## 1 · PRD

### 1.1 Problem

Atlas's design promise is that the chat surface communicates **trust** through visual restraint: citations are first-class, approvals read like content (not modals), connectors and model are visibly scoped to the current chat, the agent's "work" is visible but compressed. The current implementation honours every _data_ contract behind those promises — events stream cleanly, citations land in `citationsRegistry`, connectors materialise per-chat — but the chrome on top reads like an unfinished scaffold:

1. **Brand identity is missing.** The sidebar still advertises `assistant-ui`. Cold-linked users (a teammate clicks a shared thread) cannot tell what product they are looking at; non-engineer users (the design's stated audience) cannot tell who to trust.
2. **Status grammar is ad-hoc.** The topbar shows "Waiting for permission…" in success-green; the harness shows "Done" in success-green. Two different states share a colour, and neither matches the design's `Running / Ready / Idle` vocabulary. Users cannot scan run status at a glance, so they over-rely on reading the row content.
3. **Tool calls dominate the view.** Each `tool_call_completed` envelope renders as a full-width pill card. A run with 6 reads collapses into 6 fat cards that out-weigh the assistant prose underneath. The design's whole reason for compressing tool calls is to keep the _answer_ the visual focus; we are doing the opposite.
4. **Connector + model + depth are misplaced.** The topbar is showing a model pill _and_ the thinking-depth segmented control. The composer is showing neither. The design splits them: model is a small readout in the topbar plus the canonical picker in the composer, and thinking-depth lives in the composer footer.
5. **AskAQuestion is hostile to the modal user.** The current card is a free-text input with a glow border; the design always offers option chips first, with free-text as a fallback. A user who's been asked "Customer value, capability, or both?" should click a chip — they should not have to type the answer they just read.
6. **Citations don't show.** The data is in `citationsRegistry` but the prose renders as plain text — no superscript chips, no `Sources` strip below the answer. Users have no quick path from "where does this claim come from" to "open the source," which is the single most important affordance in the product.
7. **Re-skin sprawl is starting.** The Settings page, the Share popover, the Connectors popover, and the chat shell each ship their own status colour, their own button shape, and their own app-icon glyph. Without an extracted primitive set, every future surface starts the same drift.

These are not nine bugs. They are **one product gap**: the chrome doesn't tell the design's story. We fix it once, with shared primitives, and every surface (chat, settings, share, recipient view of a shared thread, MCP catalog overlay shipped later) gets the right vocabulary for free.

### 1.2 Goals

1. **The brand is unmistakable.** Sidebar header is the orange `A` mark and `Atlas` wordmark. Cold links resolve to a page that says what it is.
2. **One status grammar.** `Running / Ready / Idle` everywhere a status pill renders. Accent-orange dot pulses while a run streams. `Waiting for permission` is a _state of the inline approval card_, not the topbar pill — the topbar still says `Running` because the run is paused, not finished.
3. **Tool calls compress, prose stays the focus.** Consecutive `tool_call_*` envelopes within a single assistant turn group into one `<ActivityCard>`. The card head is one line (`Reading 6 sources across 4 tools`); the steps are revealed on click. Individual steps render as inline `<HarnessRow>` (icon · tool · args → result · time) — never a full-width card.
4. **Composer carries the picker + depth.** Model picker `● Atlas Reasoning ▾` + thinking depth `Fast / Balanced / Deep` + connectors button (with count) + attach + mic + send. Topbar shows a _small_ model pill as a mid-thread readout only.
5. **Assistant rendering is finished.** Orange `A` mark on the left; citations as superscript chips; `Sources` strip below the assistant block when sources land; message footer with copy / 👍 / 👎 / regen + `{model} · {time}` meta.
6. **AskAQuestion always offers chips first.** When the agent emits an `ask_a_question` interrupt with `options[]`, render chips. The chip set comes from the same payload that's already in flight; we do not invent options client-side.
7. **Four primitives, one home.** `<StatusPill>`, `<ConnectorChip>`, `<AppIc>`, `<UsageMeter>` live in `packages/design-system`. `chat/`, `settings/`, `share/` consume them. No surface ships its own.
8. **The streaming subsystem and agent harness see zero structural change.** Every event we render is already in the envelope; this PR is a renderer pass. The one exception is **PR 8.1**'s `compression_note` event, which we leave a renderer hook for but do not implement here.
9. **Audit, persistence, and tenant isolation see zero change.** No new tables, no new columns, no new audit actions. The only ai-backend change is optional — surfacing two presentation fields on existing `tool_call_*` events so the harness row can render the right copy without FE-side parsing.

### 1.3 Non-goals

- **Light-mode tuning.** Light tokens stay on the "later" pile; dark mode is the default and the only mode we polish here.
- **MCP catalog overlay.** That's PR 4.4 / Wave 2 PR F2. This PR leaves the deep-link `/settings#connectors:add` working but does not implement the overlay.
- **Sharing popover redesign.** PR 4.5 + Wave 2 PR F4. This PR only ensures the `Share` button is _present_ in the topbar and uses the shared primitives; the popover internals are unchanged.
- **Branching / fork-from-message UI.** That's PR A3. We _do_ leave the assistant-message footer's slot for a "Retry from here" affordance — disabled until A3 lands.
- **Sidebar multi-select / pin / drag-reorder.** Wave 2 PR F3. This PR adds the `live` pulse to a running thread but doesn't add bulk select.
- **Welcome-state copy iterations.** PR 2.3 already specs the layout (greeting + 4 cards). We render exactly what 2.3 specifies; copy stays.
- **New backend tables.** Tool-use policy, API keys, privacy settings, billing, audit-log read-model, notifications-v2 — all separate PRs (Wave 3 in the master plan).
- **New event types or schemas.** Compression note (PR 8.1), subagent fleet (PR 8.2), self-fork (PR 8.3) all carry their own envelopes and are siblings, not children, of this PR.

### 1.4 Success criteria

- ✅ Sidebar header renders `A` mark + `Atlas` wordmark + collapse toggle. The string `assistant-ui` does not appear anywhere a user can see.
- ✅ Topbar renders, in order: optional sidebar-open, crumb (`Workspace › Folder`, dim, single-line, max 220px), title (single-line, ellipsis), `<StatusPill>` (`Running` / `Ready` / `Idle`), connectors stack (max 4 `<AppIc>` + caret + warn dot if any disconnected), `<UsageMeter>` (bar + `%`), model pill (small readout — opens model picker via a popover that's the same component the composer uses), `<Button variant="bordered">↗ Share</Button>`, settings ⚙, panel toggle. The `↑` icon and the empty horizontal line both removed.
- ✅ Composer placeholder is `Ask Atlas to find, summarize, or draft something for your team…`. Bottom row is, left → right: `+` (attach) · layers (connectors with count) · mic · model picker pill · spacer · send (or stop, icon-flips inside the same circle on running). Footer hints render as `↵ send · ⇧+↵ new line · / skills` left, `{model} · Sources cited inline` right. Thinking-depth segmented control sits between connectors and model picker.
- ✅ User message renders right-aligned bubble (`var(--surface-2)` with subtle border). Empty messages do not render at all.
- ✅ Assistant message renders an orange `A` mark on the left; prose has citation chips wherever the envelope carries `[c<id>]`; below the prose, `<MessageSourcesStrip>` lists chips for every source that resolved during the turn; below that, the footer (`<AssistantMessageFooter>`) shows copy / thumbs / regen + `{model} · {time}`.
- ✅ A run with N consecutive `tool_call_*` envelopes (no assistant prose between them) renders as **one** `<ActivityCard>` with summary head + steps body. Steps are inline `<HarnessRow>`s (icon · tool · args → result · `mm:ss`). Single-step runs collapse into a single inline `<HarnessRow>` (no card chrome).
- ✅ AskAQuestion with `options.length > 0` renders chips first. Free-text input renders only when `allow_free=true` (always true today; we keep the door open). Chip click POSTs the chosen value through the existing `Command(resume=…)` path; no FE-side dispatch invention.
- ✅ Approvals tab in the workspace pane lists _pending_ AND _decided_ rows. Each row is two-line: summary + connector + timestamp.
- ✅ The four extracted primitives live in `packages/design-system/src/primitives/{StatusPill,ConnectorChip,AppIc,UsageMeter}/{*.tsx,*.css}`. `chat/`, `settings/`, `share/` import from `@enterprise-search/design-system` only. No surface re-skins.
- ✅ Atlas-orange (`oklch(0.78 0.13 75)` / `#d97757`) is the only accent in default theme. `--success` is used **only** for the check icon and the resolved chevron — never as a fat pill background.
- ✅ Streaming handshake is byte-identical pre/post merge. Existing fixtures replay produces visually different output but the same envelope sequence (`make test`, frontend Vitest, ai-backend pytest, backend pytest all green).
- ✅ Lighthouse a11y score for `/` (chat) ≥ 95 (today: 84 — fails on `aria-label` for the connectors stack, the usage meter, and the ↑ icon; this PR fixes all three).
- ✅ Visual regression: storybook screenshot suite (Chromatic / Playwright `toHaveScreenshot`) covers the 12 canonical states (welcome, idle thread, running with reasoning, running with tools, fleet card slot, compression note slot, approval pending, approval resolved, ask-a-question pending/resolved, mcp-discover, end-of-turn-with-sources). All 12 captured baselines.

### 1.5 User stories

| #     | Persona                                                                | Story                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ----- | ---------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US-1  | **Sarah · Marketing Ops · Acme** (the design's primary persona)        | Lands cold in chat. Brand mark and crumb tell her she's in `Marketing Ops › Launches`. Status pill says `Idle`. She types her marquee prompt. Pill flips to `Running` (orange pulse). Activity row appears: `Reading 6 sources across 4 tools`. She watches the prose stream in with chips next to claims. When the agent asks `Customer value, capability, or both?`, she clicks the `Customer value` chip — never types. When the post-to-Slack approval lands, she hits `⌘↩` (the keybind we wire in F3) and the card transforms in place to `Approved by you · 10:41 · Posted to #launch-aurora`. Total cognitive load: low. |
| US-2  | **Marcus · GTM lead, mid-thread reviewer**                             | Sarah `@marcus` in an approval. He clicks the share link from Slack on his phone. The recipient view (PR 6.1) loads. Brand pane shows `A · Atlas`. He sees the same chat surface but read-only; the approval card is the focal point with `Approve · Skip` buttons. He approves. The harness row updates inline; the workspace pane Approvals tab shows `Approved by Marcus · 10:42 · Posted to #launch-aurora`.                                                                                                                                                                                                                 |
| US-3  | **Devi · Brand reviewer (recipient of a shared thread)**               | Click-through from email. She isn't in Acme's Slack, so the citations to `#launch-aurora` come back `restricted: true`. The chip renders dim with a `Source restricted` tooltip; the prose still reads. She closes the tab without leaking facts.                                                                                                                                                                                                                                                                                                                                                                                |
| US-4  | **Priya · IT admin, audit posture**                                    | Opens Settings → Audit log (PR 7.1; this PR ensures the section's chrome — sidebar group, breadcrumb, page header — uses the same primitives as everything else). The audit table renders with the same `<StatusPill>` for outcome states; she immediately recognises `Locked out` / `Granted` / `Revoked` because the vocabulary is the one she sees in chat.                                                                                                                                                                                                                                                                   |
| US-5  | **Cold visitor (compliance auditor evaluating Atlas before purchase)** | Hits `/login` from a procurement deck. Brand pane (already shipped in PR 5.1) tells him what Atlas is. He logs in with magic-link, lands in chat, sees a fresh `Idle` thread. The welcome state's 4 suggestion cards each carry a category eyebrow (`DRAFT / SUMMARIZE / FIND / COMPARE`) and a one-line subtitle — he can scan by intent in 2 seconds.                                                                                                                                                                                                                                                                          |
| US-6  | **A run with no tools**                                                | User says `hi`. No tool calls fire. Status flips to `Running` for ~600ms then back to `Ready`. The thread shows: user bubble · `A`-marked assistant prose · footer. No activity card, no harness rows, no fleet, no compression note. The visual restraint matches the data restraint.                                                                                                                                                                                                                                                                                                                                           |
| US-7  | **A long compression-triggered run**                                   | User attaches a 200-page PDF, asks for a recap. The agent compresses early-context messages mid-run (PR 8.1 emits the `compression_note` envelope). The renderer hook in this PR catches it and renders a single dim line: `Atlas summarised 3 older messages to keep this conversation efficient.` No card, no chrome — exactly the design's "scissor" affordance.                                                                                                                                                                                                                                                              |
| US-8  | **Three sub-agents in one tick (PR 8.2)**                              | `Doc reader`, `Press scout`, `Voice reviewer` all dispatch in one orchestration tick. The renderer hook in this PR catches the `subagent_fleet_started` envelope (PR 8.2) and renders a fleet card with three rows + rolled-up progress + "View in workspace →". Children render _inside_ the card, not as siblings; the design's "subagents collapse into a fleet" rule is enforced visually.                                                                                                                                                                                                                                   |
| US-9  | **Mid-thread model swap**                                              | Sarah opens the topbar's small model pill. Clicks → the same picker the composer uses pops anchored to the topbar pill (one component, two anchors). She picks `Atlas Research`. The next prompt streams against the new model; the assistant footer for the next assistant block reads `Atlas Research · just now`. No re-render of the prior turns.                                                                                                                                                                                                                                                                            |
| US-10 | **Tablet at 920px** (the design's narrow viewport)                     | Sidebar collapses to icon rail; workspace pane stays pinned at 320px; the chat column reflows. The composer's footer hints stack vertically at `< 720px` chat-column width. The `<UsageMeter>` collapses to its bar (no `%` label) at `< 1100px` topbar width. All three behaviours are CSS, no JS.                                                                                                                                                                                                                                                                                                                              |

---

## 2 · Spec

### 2.0 Refinements from the latest design mocks (2026-05-06)

After two more design screenshots landed, the spec is updated as follows. These supersede §2.1–§2.9 wherever they conflict:

1. **Citation chip is a pill, not a `<sup>`.** Render `[c<id>]` tokens as small outlined number pills inline with the prose (faint background, dim foreground, accent on hover/focus). No superscript.
2. **No per-row elapsed on `<HarnessRow>`.** Drop the `0:09` time column. Rows render as `✓ tool_name (args) | → result`. Elapsed lives on the `<ActivityCard>` head only.
3. **Activity collapse threshold is `≥ 4 rows`.** One, two, or three consecutive harness rows render inline. Four or more (or any run that crosses ~20s elapsed) collapse into a single `<ActivityCard>` with a summary head and a click-to-expand body.
4. **`live` is a labelled orange pill, not a pulsing dot.** The running thread in the sidebar shows a small solid `live` pill in `--accent`, right-aligned where the timestamp would be. Non-running threads show their timestamp.
5. **Brand-aware `<AppIc>`.** Top connectors (`notion`, `drive`, `slack`, `salesforce`, `confluence`, `github`, `linear`, `figma`, `snowflake`) ship a brand glyph (the Drive triangle, the Slack `#`, the Notion `N`-on-white, etc.). Anything outside the list falls back to the colour-letter circle. The primitive owns the mapping; consumers pass `id` only.
6. **New `<StatusLine>` primitive.** Renders a one-line italic-dim acknowledgement (`• Got it. Drafting customer-led.`) — driven by the existing `observation` envelope kind (or `status` parts). Lives in `packages/design-system/src/primitives/StatusLine/`.
7. **Tab labels singular/plural by count.** `Approval` (1) vs `Approvals` (n). `Source` vs `Sources`. `Agent` vs `Agents`. `Skill` vs `Skills`. Implement once via a `pluralize(label, count)` helper.
8. **Connectors-button count badge uses `--accent`.** The `4` on the layers-icon connectors button is the brand accent (orange), not neutral.
9. **Source row renders `Live data` for live connectors.** When `connector_slug ∈ {salesforce, snowflake, datadog, intercom, pagerduty}`, the source row footer renders `<author> · Live data` instead of `<author> · Updated <when>`. v1 uses the slug heuristic; a future `freshness_kind: "snapshot" | "live"` field on `CitationSourceRef` makes it explicit (PR 3.1 follow-up — not in 8.0).
10. **Skills tab is conditional.** Hide the Skills tab when `skills_count === 0`; show it when at least one skill is surfaced. Same rule for Draft and Approval — the design's mock omits irrelevant tabs.

These refinements are mechanical; none changes the streaming contract (R1–R5 in §2.10). The primitive count goes from 4 → **6** (`StatusPill`, `ConnectorChip`, `AppIc`, `UsageMeter`, `BrandMark`, `StatusLine`).

### 2.1 The six extracted primitives

These move into `packages/design-system/src/primitives/`. Each is ≤ 80 LOC, prop-driven, headless about colour (theme tokens drive everything), and shipped with one CSS file each that owns its layout.

```ts
// packages/design-system/src/primitives/StatusPill/index.ts
export type StatusTone = "running" | "ready" | "idle" | "error" | "warn";
export interface StatusPillProps {
  tone: StatusTone;
  label: string; // "Running" | "Ready" | "Idle" | "Error" | "Waiting"
  detail?: string; // tooltip
  pulse?: boolean; // running -> default true
}
// CSS contract:
//   --status-pill-bg: var(--surface);
//   --status-pill-dot-{tone}: derived from --accent / --success / --text-dim / --warn / --danger;
//   .status-pill[data-pulse=true] .status-pill__dot { animation: status-pulse 1.6s ease-in-out infinite; }

// packages/design-system/src/primitives/ConnectorChip/index.ts
export interface ConnectorChipProps {
  id: string; // "notion" | "slack" | "drive" | "salesforce" | "github" | "confluence" | …
  name: string;
  on: boolean; // active for this chat
  connected: boolean; // user-OAuth connected
  globallyEnabled: boolean;
  size?: "sm" | "md";
  onClick?(): void;
}
// data-on, data-disconnected, data-locked attribute branches drive style.

// packages/design-system/src/primitives/AppIc/index.ts
export interface AppIcProps {
  id: string; // "notion" | … same set
  size?: 12 | 14 | 16 | 20 | 24;
}
// The single source of truth for the brand glyph (one-letter circle + colour). Used
// in every place a connector glyph appears: connector chip, source row, source
// chip, audit log, MCP overlay, Approval card footer. No surface re-skins.

// packages/design-system/src/primitives/UsageMeter/index.ts
export interface UsageMeterProps {
  pct: number; // 0..100
  label?: string; // "47%" — auto-formatted if omitted
  title?: string; // tooltip — "Usage & context · 47% used"
  onClick?(): void; // opens the Usage overlay
  collapsed?: boolean; // narrow viewport: bar only
}
```

Each primitive ships with a `<Primitive>.test.tsx` (snapshot + interaction) and a Storybook story. The four CSS files are imported once from `packages/design-system/src/index.css`; consumers do not import primitive CSS individually.

**Why not move them later.** Today, `chat/`, `settings/`, and `share/` each have their own copy. The screenshot showed it bluntly: the green `Done` pill in chat does not match the green `Paid` pill in Settings → Billing's invoices table; both would be `<StatusPill tone="ready">` if shared. Extracting now is the only thing that prevents the next surface from forking again.

### 2.2 Topbar rebuild

[`apps/frontend/src/features/chat/components/shell/Topbar.tsx`](apps/frontend/src/features/chat/components/shell/Topbar.tsx) gets reshaped. **Not** rewritten — its `Topbar.test.tsx` keeps passing. We swap the children, not the contract.

```tsx
<header className="topbar">
  <div className="topbar__left">
    {sidebarCollapsed && <IconButton aria-label="Open sidebar" icon="panelLeft" onClick={onToggleSidebar} />}
    <Crumb path={crumbPath} />               {/* ConversationTitle.tsx — already exists */}
    <ConversationTitle title={title} />      {/* already exists */}
  </div>
  <div className="topbar__right">
    <StatusPill {...status} />               {/* extracted */}
    <ConnectorsPill                          {/* already exists; re-skinned to use AppIc */}
      connectors={connectors}
      perChat={perChat}
      onTogglePerChat={onToggleConnector}
      onConnect={onConnectConnector}
    />
    <UsageMeter pct={usagePct} title="Usage & context" onClick={onOpenUsage} />
    <ModelPill                               {/* already exists; re-anchored to share popover */}
      activeModel={activeModel}
      models={models}
      onChange={onModelChange}
    />
    <Button variant="bordered" icon="share" onClick={onOpenShare}>Share</Button>
    <IconButton icon="settings" href="/settings" aria-label="Settings" />
    <IconButton icon="panelRight" pressed={workspaceOpen} onClick={onToggleWorkspace} aria-label="Toggle workspace" />
  </div>
</header>
```

The thinking-depth segmented control (`<ThinkingDepthControl>` — already exists) **moves** out of the topbar into the composer footer (§ 2.5). The topbar's `↑` icon and the empty progress-bar element are deleted.

### 2.3 Sidebar rebuild

[`apps/frontend/src/features/chat/components/sidebar/Sidebar.tsx`](apps/frontend/src/features/chat/components/sidebar/Sidebar.tsx) header is replaced; the thread-list item is rebuilt; the user card is wired to the existing `useMe()` hook.

```tsx
<aside className="sidebar">
  <header className="sidebar__head">
    <BrandMark /> {/* new — A in an orange circle */}
    {!collapsed && <BrandWordmark>Atlas</BrandWordmark>}
    {!collapsed && (
      <IconButton
        icon="panelLeft"
        onClick={onCollapse}
        aria-label="Collapse sidebar"
      />
    )}
  </header>
  <ButtonRow icon="plus" kbd="⌘N" onClick={onNew}>
    New chat
  </ButtonRow>
  {!collapsed && <SearchInput placeholder="Search chats…" {...search} />}
  {!collapsed && (
    <ThreadList
      groups={["Today", "Yesterday", "Earlier"]}
      items={threads}
      activeId={activeId}
      runningIds={runningIds} // each running thread renders a 'live' pulse
      onSelect={onSelect}
    />
  )}
  {!collapsed && <UserCard {...me} />}{" "}
  {/* avatar SC · Sarah Chen · Marketing Ops · Acme · chevron */}
</aside>
```

`ThreadList`'s row is multi-line: title (medium), one-line preview (dim), timestamp (right). The running-thread pulse is a 6px accent dot inline with the timestamp, never a `•` prefix on the title. Click on the user card opens an existing-component popover (workspace switch / settings / sign out) — `useMe()` already returns the data; the popover wraps `useWorkspace()` for the org list.

### 2.4 Assistant + user message rendering

```tsx
// apps/frontend/src/features/chat/components/messages/UserMessage.tsx
export function UserMessage({ text, attachments }: Props) {
  if (!text && !attachments?.length) return null;
  return (
    <div className="msg msg--user">
      <div className="msg__bubble">{text}</div>
      {attachments?.length ? <AttachStrip items={attachments} /> : null}
    </div>
  );
}

// apps/frontend/src/features/chat/components/messages/AssistantMessage.tsx
export function AssistantMessage({ id, parts, footer, model, time }: Props) {
  return (
    <div className="msg msg--asst">
      <BrandMark size={20} />{" "}
      {/* the orange A — same component as sidebar header, smaller */}
      <div className="msg__body">
        <Prose parts={parts} /> {/* renders citations as superscript chips */}
        {footer && <AssistantMessageFooter id={id} model={model} time={time} />}
      </div>
    </div>
  );
}
```

`Prose` is the existing `MarkdownText.tsx` extended with the citation-chip plugin (`citationRemarkPlugin`, already in repo). The `MessageSourcesStrip` renders below the assistant block on `final_response` — already wired in `eventReducer.ts`, but currently invisible because the source ids haven't been resolved before the strip mounts; we add a `useResolvedSources(ids)` hook that subscribes to `citationsRegistry` and re-renders when sources arrive (deferred-resolution pattern, already used by `useDrafts`).

### 2.5 Composer rebuild

[`apps/frontend/src/features/chat/components/composer/AssistantComposer.tsx`](apps/frontend/src/features/chat/components/composer/AssistantComposer.tsx) loses the topbar's responsibilities and gains the design's complete tools row.

```tsx
<div className="composer">
  {attachments.length > 0 && <AttachmentChips items={attachments} onRemove={onRemoveAttach} />}
  <textarea
    ref={inputRef}
    value={value}
    onChange={(e) => onChange(e.target.value)}
    onKeyDown={onKey}
    placeholder="Ask Atlas to find, summarize, or draft something for your team…"
    rows={1}
  />
  <div className="composer__row">
    <div className="composer__tools">
      <IconButton icon="plus"  onClick={onAttachMenu} aria-label="Attach"     />
      <ConnectorsButton                    {/* already exists; uses ConnectorPopover */}
        connectors={connectors}
        connectorState={connectorState}
        onToggle={onToggleConnector}
        onConnect={onConnectConnector}
      />
      <IconButton icon="mic"   onClick={onMic} aria-label="Voice" />
      <ThinkingDepthControl                {/* moved from topbar */}
        value={depth}
        onChange={onDepthChange}
      />
      <ModelPicker                         {/* already exists; the same component the topbar's ModelPill anchors */}
        models={models}
        active={activeModel}
        onChange={onModelChange}
      />
    </div>
    <SendButton                            {/* icon flips inside the same circle on running */}
      running={running}
      disabled={!value.trim()}
      onClick={running ? onCancel : onSend}
    />
  </div>
  <div className="composer__hint">
    <span><kbd>↵</kbd> send</span><Sep />
    <span><kbd>⇧</kbd>+<kbd>↵</kbd> new line</span><Sep />
    <span><kbd>/</kbd> skills</span>
    <span className="grow" />
    <span>{activeModel.name} · Sources cited inline</span>
  </div>
</div>
```

`SendButton` is a single component; its visual flips on `running`. **No** giant red `Stop` oval. The `data-cancel="true"` attribute toggles a CSS rule that flips the icon and adjusts the colour (accent on running, muted on idle).

### 2.6 Activity / harness vocabulary

This is the highest-impact visual change. We introduce **two** primitives in `chat/components/activity/`:

```tsx
// HarnessRow.tsx — inline, dim, no card
//   <check> tool_name(args) → result · 0:09
export function HarnessRow({ status, tool, args, result, elapsed }: Props) {
  /* … */
}

// ActivityCard.tsx — already exists; the rebuild changes its head + body to use HarnessRow internally
```

**Grouping rule** (in `eventReducer.ts`): consecutive `tool_call_started/completed` envelopes within the same assistant turn (no `model_delta` between them) attach into a single `ActivityCard.steps[]` array. The reducer is already part-aware (it threads parts onto the assistant message); the new branch is one selector that walks back through `parts` and concatenates onto the last `activity` part if present, or starts a new one if not.

**Single-step runs** (one tool call total) render as a free-standing `<HarnessRow>` rather than a 1-row card. The reducer flags `singleton: true` when it commits the part; `<ActivityCard>`'s render path short-circuits to `<HarnessRow>` when `steps.length === 1 && singleton`.

**Progress visibility.** While running, the card head's icon is the spark glyph (already in `Icon.spark`); on completion, it's the check. The summary text is the `presentation.summary` field on the _last_ step (the projector already populates it server-side); no FE-side summary synthesis.

**Why no envelope change.** All required data already lives on `tool_call_completed`'s `presentation` payload. The only new thing the FE asks of the projector is two cosmetic fields that may or may not be present today: `display_args` (the short `(arg)` rendered in the inline row) and `display_result` (the `→ result` rendered in the inline row). If absent, the FE falls back to `summary` truncated to 60 chars. We don't break envelope contract; we use it more thoroughly.

If we want to land cleanly without touching ai-backend, we ship the FE-only fallback now and add the two fields in a 5-LOC follow-up to the projector — not in this PR.

### 2.7 AskAQuestion card

`AskAQuestionTool.tsx` already renders. The fix is small but visible:

```tsx
<div className="ask" data-resolved={resolved || undefined}>
  <div className="ask__head">
    <Icon name="question" />
    <span>{question}</span>
  </div>
  {!resolved ? (
    <>
      {options.length > 0 && (
        <div className="ask__chips">
          {options.map((o) => (
            <button key={o} className="chip-btn" onClick={() => onAnswer(o)}>
              {o}
            </button>
          ))}
        </div>
      )}
      {(allow_free || options.length === 0) && (
        <FreeText placeholder="Or type an answer…" onSubmit={onAnswer} />
      )}
    </>
  ) : (
    <ResolvedAnswer answer={resolved} />
  )}
</div>
```

The chips and free-text are mutually inclusive; both render when `allow_free=true` (which is today's default). The card border loses the cyan glow; it gets the standard `--surface` background with an `--accent` left rule.

The **interrupt resume path** is unchanged: the chip click calls the existing `useSubmitInterrupt(answer)` (which threads through assistant-ui's `Command(resume=…)` machinery). No new dispatch invention.

### 2.8 Workspace pane Approvals tab

`ApprovalsTab.tsx` already lists pending approvals. We add a **decided** section below it, sourced from `useApprovalsQueue()` (the existing hook already exposes both — we just don't render the decided list today).

```tsx
<section>
  <h4>Pending</h4>
  {pending.map((a) => (
    <ApprovalRow key={a.approval_id} {...a} />
  ))}
</section>;
{
  decided.length > 0 && (
    <section>
      <h4>Decided</h4>
      {decided.map((a) => (
        <ApprovalRow key={a.approval_id} {...a} resolved />
      ))}
    </section>
  );
}
```

`ApprovalRow` is a two-line summary: `{summary} · {connector_glyph} · {timestamp}`. The connector glyph uses `<AppIc>`.

### 2.9 Theme tokens

The accent is already named `--accent` and set to `oklch(0.78 0.13 75)` ≈ `#d97757`. The fix is consistency:

- Remove all hard-coded `green` / `red` / `#10b981` literals from `apps/frontend/src/`. Replace with `var(--success)` / `var(--danger)`.
- Remove the bright-green `Done` pill background from `chat/components/tools/ProgressTool.css` (and any sibling). Replace with `var(--text-dim)` foreground + a check glyph in `var(--success)`.
- Replace the red `Stop` oval style with the `data-cancel` flip described in § 2.5.
- The `<StatusPill>` primitive owns `running` (accent), `ready` (success-dim), `idle` (text-dim), `warn` (warn), `error` (danger).

`packages/design-system/src/tokens.css` is the single source of truth. `apps/frontend/src/styles.css` consumes it; no app-level overrides on accents.

### 2.10 Streaming-friendliness contract (the rule, not the trace)

**Every card / chip / row in this PR is fed by the existing envelope stream and obeys five non-negotiable rules.** These rules are how we keep the renderer correct under partial events, late events, dropped sockets, mid-run reconnects, and full archive replay.

**R1 — One renderer, one envelope kind.** Each visible block ties to exactly one `RuntimeApiEventType` for _creation_, and (optionally) one or more for _update_. No block is materialised by inferring batches from timestamps; no block disappears when it should mutate. The mapping is closed:

| Block                                           | Created by                                                                                                                   | Updated by                                                                         | Sealed by                        |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- | -------------------------------- |
| User bubble                                     | client-side `runs.create` echo                                                                                               | —                                                                                  | —                                |
| Assistant prose + chips                         | `model_call_started`                                                                                                         | `model_delta` (text) · `source_ingested` (chip resolution via `citationsRegistry`) | `final_response`                 |
| Reasoning accordion                             | `reasoning_summary` (start)                                                                                                  | `reasoning_summary_delta` (text)                                                   | `reasoning_summary` (final)      |
| Inline `<HarnessRow>`                           | `tool_call_started`                                                                                                          | `tool_call_delta` (args streaming) · `tool_result` (result fill)                   | `tool_call_completed`            |
| `<ActivityCard>`                                | first `tool_call_started` of a run that has no `model_delta` between consecutive starts                                      | each child `tool_call_*` appends a step                                            | last child `tool_call_completed` |
| `<AskAQuestion>`                                | `approval_requested` with `kind="ask_a_question"` (LangGraph interrupt)                                                      | —                                                                                  | `approval_resolved`              |
| `<ApprovalTool>`                                | `approval_requested` (write/destructive)                                                                                     | `approval_forwarded` (two-stage)                                                   | `approval_resolved`              |
| `<ConnectorAuthTool>` (blocking + non-blocking) | `mcp_auth_required` (non-blocking variant carries `discovery_reason`)                                                        | —                                                                                  | `approval_resolved`              |
| `<SubagentTool>` (single)                       | `subagent_started` (when no fleet parent)                                                                                    | `subagent_progress`, `subagent_update`                                             | `subagent_completed`             |
| `<SubagentFleetCard>` (PR 8.2)                  | `subagent_fleet_started`                                                                                                     | child `subagent_*` events with `parent_fleet_id`                                   | `subagent_fleet_finished`        |
| `<NoteCard>` (PR 8.1)                           | `compression_note`                                                                                                           | —                                                                                  | — (terminal)                     |
| `<MessageSourcesStrip>`                         | first `source_ingested` of the assistant turn (via `useResolvedSources(ids)`)                                                | each `source_ingested` adds a chip                                                 | `final_response` (frozen list)   |
| Topbar status                                   | `run_started` → `Running` · `run_cancelling` → `Running` · `run_completed` → `Ready` · `run_failed`/`run_rejected` → `Error` | —                                                                                  | —                                |
| Topbar usage meter                              | `model_call_completed` (running %) · `budget_warning` (warn tone)                                                            | every subsequent usage envelope                                                    | `run_completed` (final reading)  |

**R2 — Idempotent reducers, no double-apply.** The reducer's contract is "applying the same envelope twice is a no-op." This is already true for `citationsRegistry.upsertCitation` (idempotent on `citation_id`). We extend the same discipline to:

- **Activity grouping** — `partFactories.activity.appendStep(envelope)` keys the step on `tool_call.id`. A re-delivered `tool_call_completed` with the same id replaces the existing step in place (does not append).
- **Fleet card** — children are keyed on `subagent_id`. Re-delivery of `subagent_started` for an existing id is a no-op; re-delivery of `subagent_completed` overwrites status.
- **Approvals** — keyed on `approval_id`. `approval_resolved` for a row that's already `resolved` is dropped (warns once, then silent).
- **Compression note** — keyed on `compression_event_id`. Two of the same id render one card.

The reducer's invariant test (`apps/frontend/src/features/chat/chatModel/citationStore.invariant.test.ts` — already exists for citations) gets a sibling for activity, fleet, approvals, compression: replay any event sequence twice; assert the final state equals the once-applied state.

**R3 — Out-of-order tolerance.** Envelopes can arrive out of `sequence_no` order during reconnect (the server replays ≥ N+1; the client may already have N+2 buffered locally). Cards must:

- **Render forward refs gracefully.** `<MessageSourcesStrip>` subscribes to `citationsRegistry`; if a `model_delta` containing `[c<id>]` arrives before the corresponding `source_ingested`, the chip renders dim with a `loading…` tooltip and lights up when the registry receives the source. Already implemented for citations; we extend the same `useResolvedSources(ids)` pattern to fleet children (`useResolvedSubagents(parent_fleet_id)`).
- **Late completion is fine.** A `tool_call_completed` arriving after a peer `tool_call_started` updates the row in place; the `<ActivityCard>` summary recomputes from the projector's `presentation.summary` on the _latest_ completed step.
- **Late `final_response`** seals the assistant block (footer flips from running to ready) and freezes the sources strip — but **does not destroy** any chip that already resolved. The strip's identity is the assistant message id; its chips are the union of `final_response.citations` ∪ in-flight `source_ingested` events for the same run.

**R4 — Resume by `after_sequence=N` is the only resume primitive.** The screen owns the cursor (already wired via `streamRunEvents`'s `afterSequence` arg in [agentApi.ts:523](apps/frontend/src/api/agentApi.ts#L523)). On reconnect:

- The screen sends `?after_sequence={highest seen}`.
- The server replays only `sequence_no > N`.
- Reducers re-apply each event (R2 makes this safe).
- No FE-side state survives the reconnect except what's reproducible from envelopes — chips, cards, footer states, status pill, usage meter. There is **no** "I had this card open before" flag in `localStorage`; the card is re-derived from the envelopes.

The `<HarnessRow>`'s `elapsed` ticker is a CSS animation seeded from `tool_call.started_at` (server-stamped) — never `Date.now() - lastRender`. After a 30-second SSE drop, the elapsed value is correct on first paint after resume.

**R5 — Presentation comes from the projector, not the FE.** Every card's _human copy_ (head summary, sub line, status label, badge) is read from `envelope.presentation.{display_title, summary, status}` produced by `RuntimeEventPresentationProjector` (verified: [events.py:163](services/ai-backend/src/runtime_api/schemas/events.py#L163), [events.py:389](services/ai-backend/src/runtime_api/schemas/events.py#L389)). The FE may _truncate_ or _style_ but does not _invent_ copy. This is what lets us swap "Reading 6 sources across 4 tools" for "Posted to #launch-aurora" without a FE deploy when the projector improves.

The two cosmetic fields the inline `<HarnessRow>` would benefit from (`display_args`, `display_result`) are **not** required by R5. Until the projector adds them (5-LOC follow-up to `_summary_for`), the FE renders `tool · presentation.summary truncated 60ch` — slightly less compact than the design's `tool(args) → result` but on-spec for layout. We do not parse `summary` for parens.

### 2.10b Streaming trace (end-to-end, after this PR + 8.1 + 8.2)

Numbered steps map exactly to envelope kinds; everything visual is downstream of an envelope already in flight.

```
 Browser (FE)                                ai-backend                                backend
─────────────────                           ──────────────                            ────────
  composer.send                ─────────►  POST /v1/agent/conversations/{id}/runs
                                            └─ runtime_worker claims, builds graph
                                               with enabled_connectors ∩ tool_use_policies
                                               on AgentRuntimeContext
  SSE open after_sequence=N    ◄─── SSE ───  stream of RuntimeEventEnvelopes:
                                              run_started        →  Topbar status flips Running (accent pulse)
                                              reasoning_summary  →  ReasoningGroup renders Thinking accordion
                                              model_delta        →  Prose streams; cite chips appear inline
                                              tool_call_started  ┐
                                              tool_call_completed├ N consecutive → ONE ActivityCard
                                              tool_call_started  │  (HarnessRow per step)
                                              tool_call_completed┘
                                              source_ingested    →  citationsRegistry stamps ids; chips light up
                                              mcp_auth_required  →  ConnectorAuthTool (non-blocking variant)
                                                  └ user clicks Connect
                                              (resume)
                                              subagent_fleet_started   ─── PR 8.2
                                              subagent_started ×3  ┐
                                              subagent_progress    │ rendered inside SubagentFleetCard
                                              subagent_completed ×3┘
                                              subagent_fleet_finished
                                              compression_note     ─── PR 8.1 → NoteCard renders
                                              draft_updated        →  Workspace pane Draft tab populates
                                              ask_a_question       →  AskAQuestion card with option chips
                                                  └ user clicks chip → Command(resume=…)
                                              approval_requested   →  ApprovalTool inline card
                                                  └ user ⌘↩       → Command(resume="approved")
                                              tool_call_*          →  inline HarnessRow (Slack post)
                                              final_response       →  AssistantMessage footer renders
                                              run_completed        →  Topbar status flips Ready (idle dot)
```

What's _not_ changing:

- **Persistence schema.** No new tables. No new columns. `runtime_compression_events` already exists for PR 8.1 to emit from; no schema work required for either 8.1 or this PR.
- **Tenant isolation.** Existing RLS unchanged. The renderer reads only what the envelope already carries, so no new queries fire from the FE.
- **Audit chain.** Identity / MCP / Skill audit chains unchanged. No new audit actions.
- **Token vault.** Unchanged. The model picker, the connectors stack, and the share button all read from APIs that already enforce vault gating.
- **Service boundaries.** No service starts importing another's `src/`. `packages/design-system` is the only place new code is imported across components, and it's a stable contracts package by design.

### 2.11 Frontend wiring (component map, no LoC bloat)

| Concern            | Reuse                                                                                              | Add                                                                                           | Delete                                                                           |
| ------------------ | -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| Brand mark         | —                                                                                                  | `<BrandMark>` (≈ 24 LOC SVG + CSS) in `packages/design-system`                                | inline `✨ assistant-ui` text in Sidebar                                         |
| Status pill        | —                                                                                                  | `<StatusPill>` primitive                                                                      | per-surface pill styles                                                          |
| App icon           | —                                                                                                  | `<AppIc>` primitive (one source for 8 connector glyphs)                                       | per-surface app-icon CSS                                                         |
| Connector chip     | `<ConnectorChip>` (rename of existing connector-chip)                                              | move into `packages/design-system`                                                            | duplicate in chat/                                                               |
| Usage meter        | —                                                                                                  | `<UsageMeter>` primitive                                                                      | inline meter markup in Topbar                                                    |
| Topbar             | `Topbar.tsx`, `ConversationTitle.tsx`, `ConnectorsPill.tsx`, `ModelPill.tsx`, `UsageMeter` (new)   | `<Crumb>` component (≈ 30 LOC; already partially present)                                     | the `↑` icon, the empty progress-bar div                                         |
| Sidebar            | `Sidebar.tsx` skeleton, `useMe()`, `useWorkspace()`                                                | `<BrandMark>`, `<UserCard>` (≈ 60 LOC), `<ThreadListRow>` rebuild (≈ 50 LOC)                  | the unfamiliar header refresh + undo icons; the bullet-prefix on running threads |
| User message       | —                                                                                                  | `<UserMessage>` rebuild (≈ 40 LOC)                                                            | center-block placeholder                                                         |
| Assistant message  | `MarkdownText`, `citationRemarkPlugin`, `MessageSourcesStrip`, `AssistantMessageFooter`            | `<BrandMark size=20>` slot in `<AssistantMessage>`                                            | per-surface `A` glyph styles                                                     |
| Activity / Harness | `ActivityCard.tsx` skeleton, `eventReducer.ts` part attachment                                     | `<HarnessRow>` (≈ 50 LOC), grouping rule in reducer (≈ 30 LOC)                                | the bright-green `Done` pill in `ProgressTool.css`                               |
| AskAQuestion       | `AskAQuestionTool.tsx`, `useSubmitInterrupt()`                                                     | option chips block (≈ 20 LOC)                                                                 | the cyan glow border CSS                                                         |
| Composer           | `AssistantComposer.tsx`, `ConnectorsButton`, `ModelPicker`, `ThinkingDepthControl`, `useShortcuts` | tools-row rebuild (≈ 60 LOC), footer hint row (≈ 20 LOC), `<SendButton>` icon-flip (≈ 20 LOC) | the big red Stop oval; the unknown `◇` glyph                                     |
| Approvals tab      | `useApprovalsQueue`                                                                                | "Decided" section (≈ 30 LOC)                                                                  | —                                                                                |
| Theme              | `tokens.css`                                                                                       | enforce single accent (≈ 20 LOC of token reshape)                                             | hard-coded `green` / `red` literals                                              |

### 2.12 Reused libraries (no new deps)

We do **not** add any new package. The repo already has what we need:

- **assistant-ui/react** — thread machinery, tool composer slots, interrupt resume.
- **@radix-ui/react-popover, @radix-ui/react-tooltip, @radix-ui/react-tabs** — already in use; the `<UserCard>` popover and the `<UsageMeter>` tooltip wrap them.
- **dnd-kit** — already pinned for sortable lists; not used in this PR (Wave 2 PR F3 uses it).
- **lucide-react** (or whatever icon set ships today) — confirmed for icon glyphs.
- **remark / rehype + the existing `citationRemarkPlugin`** — for citation chip rendering. No new plugin.
- **Vitest + Testing Library** — for component tests.
- **Playwright** — for the visual regression suite (it's already configured for the e2e smoke).

If a future PR needs a feature an existing dep can't cover, that's the moment to evaluate. This PR holds the line.

### 2.13 Errors & edge cases

| Surface          | Edge case                                                                                                    | Behaviour                                                                                                                                                        |
| ---------------- | ------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Topbar status    | Run ends with `RUN_REJECTED` (budget)                                                                        | Pill switches to `tone="error"` with label `Budget`; click opens Usage overlay.                                                                                  |
| Topbar status    | Run ends with `RUN_FAILED`                                                                                   | `tone="error"`, label `Failed`, tooltip carries the `errors[].message` from the envelope.                                                                        |
| Activity card    | A single tool call lasts > 30s                                                                               | Card head spinner pulses; presentation `summary` updates from the projector's interim line. No FE-side timer.                                                    |
| AskAQuestion     | Options array empty (`options.length === 0`)                                                                 | Render free-text input only (today's behaviour); `allow_free` is implicitly true.                                                                                |
| AskAQuestion     | User edits then deletes their typed answer back to empty                                                     | Submit button disabled; chips remain interactive.                                                                                                                |
| Connectors stack | Connector enumerates with `connected=false, globallyEnabled=true` (workspace-installed but user not OAuthed) | Chip renders dashed; clicking opens the OAuth flow (existing `ConnectorAuthTool` path). Topbar shows the warn dot.                                               |
| Usage meter      | `pct` unknown (worker hasn't reported usage yet)                                                             | Renders empty bar with `—%` label, tooltip `Usage available after first turn`.                                                                                   |
| Send button      | User holds shift+enter                                                                                       | Newline; send disabled until trim is non-empty.                                                                                                                  |
| Send button      | Run is mid-stream and user clicks stop                                                                       | Calls `cancelRun`; button stays in cancel state until envelope `run_cancelled` arrives, then flips back.                                                         |
| Welcome state    | Auth context says workspace has no connectors connected                                                      | Suggestion cards still render (they don't reference connectors); the topbar connectors stack shows "Connect Notion · Drive · Slack" CTAs (existing — unchanged). |

### 2.14 Verification

- **Unit tests.** Each new / rebuilt component has a co-located `.test.tsx`. Snapshot tests for the four primitives. Reducer tests for the activity-grouping rule.
- **Visual regression.** Playwright `toHaveScreenshot` in `apps/frontend/tests/visual/` captures 12 baselines (listed in § 1.4). CI fails on diff > 0.1%.
- **Accessibility.** `axe-core` smoke in CI; `aria-label` audit on every icon-only button.
- **Streaming smoke.** Existing fixture-driven replay tests in `apps/frontend/src/features/chat/chatModel/eventReducer.test.ts` continue to pass; new fixture for the activity-grouping rule.
- **Cross-service.** `make test` green; `services/ai-backend` pytest green (no envelope changes); `services/backend` pytest green (no schema changes).
- **End-to-end.** `make dev` → walk Sarah's flow (US-1) → confirm every § 1.4 success criterion by inspection. Walk US-2 (recipient view) — confirm `BrandMark` + same primitives render. Walk US-7 (compression) — confirm renderer hook fires for the `compression_note` envelope (PR 8.1 lands separately).
- **Boundary check.** `grep -rn "from backend_app" services/ai-backend/src && grep -rn "from agent_runtime" services/backend/src` remains empty. `grep -rn "import.*from.*chat/components" packages/` empty (design-system primitives don't import app surfaces).
- **No-new-deps check.** `git diff package.json package-lock.json` shows zero adds.

---

## 3 · Architecture summary

The PR's shape is intentionally narrow: extract four primitives into `design-system`, use them everywhere, and rewire the chat surface to consume them. The agent harness, the streaming envelope, the persistence schema, the audit chain, and the auth subsystem are all untouched. The only ai-backend touch (optional, deferrable) is adding two cosmetic fields to the `tool_call_*` presentation projector so the inline `<HarnessRow>` can render copy without FE-side parsing.

By the time this PR ships:

- **Sarah** (US-1) sees the brand, the status grammar, the inline harness, the chip-first AskAQuestion, the citation chips, and the sources strip — every promise the design makes about trust.
- **Marcus** (US-2) gets the same surface in the recipient-view, because the primitives don't know the difference between an owner and a recipient.
- **Devi** (US-3) sees the source-restricted tooltip on the chips that aren't hers to read — the citation chip primitive surfaces it via the same `<AppIc>` slot.
- **Priya** (US-4) sees the same status grammar in the audit log, because `<StatusPill>` is one component.
- **The cold visitor** (US-5) sees the brand pane on `/login` (already shipped), then lands in chat with a brand mark in the sidebar that says `Atlas` — continuity that the current build breaks.
- **PR 8.1, 8.2, 8.3** are one-line wire-ups when they land, because their renderer hooks already exist in the reducer.

The smallest change that finishes the design — and the only one that prevents the next surface from forking the visual vocabulary again.

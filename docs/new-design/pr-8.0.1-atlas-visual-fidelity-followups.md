# PR 8.0.1 — Atlas Visual Fidelity Follow-ups

> **Status:** Draft (PRD + Spec)
> **Plan reference:** Wave 8, follow-up to [PR 8.0](./pr-8.0-atlas-visual-fidelity.md). Closes the deferred items from the first pass.
> **Owner:** frontend (eventReducer grouping, brand-glyph SVGs in design-system, composer footer hint row, `pluralize` helper, source-row Live-data branch, conditional Skills tab) · ai-backend (zero) · backend (zero)
> **Size:** **M.** Net new code ≈ 380 LOC across the FE and design-system. Zero schema work, zero envelope changes.
> **Depends on:** ✅ PR 8.0 (shipped in same wave). All other deps listed in 8.0 still apply unchanged.
>
> **Reads alongside:** [`pr-8.0-atlas-visual-fidelity.md`](./pr-8.0-atlas-visual-fidelity.md), [`pr-3.1-citation-chips-sources-tab.md`](./pr-3.1-citation-chips-sources-tab.md), [`pr-3.2-workspace-pane-right-rail.md`](./pr-3.2-workspace-pane-right-rail.md).

---

## 0 · TL;DR

PR 8.0 fixed the most visible visual gaps — brand mark, harness rows, AskAQuestion chrome, conversation `live` pill, composer placeholder/stop. **PR 8.0.1 closes the remaining seven items the design calls out** and confirms one (citation chips) is already correct.

| Item                           | Today (after 8.0)                                                                      | After 8.0.1                                                                                                                                                                                               |
| ------------------------------ | -------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Activity card collapse**     | One inline `<HarnessRow>` per `tool_call_*` envelope (no grouping).                    | Reducer collapses **≥ 4 consecutive harness rows** (no `model_delta` between them) into one `<ActivityCard>` with summary head + click-to-expand body of `<HarnessRow>`s.                                 |
| **Brand glyph for connectors** | `<AppIcon>` renders the first letter on a circle, fallback colour.                     | Brand-aware `<AppIc>` mapping for the top connector slugs (`notion`, `drive`, `slack`, `salesforce`, `confluence`, `github`, `linear`, `figma`, `snowflake`); letter-circle fallback for everything else. |
| **Composer footer hints**      | None.                                                                                  | `↵ send · ⇧+↵ new line · / skills` left-aligned, `{active model name} · Sources cited inline` right-aligned, hidden on running.                                                                           |
| **Workspace pane tab labels**  | Always plural (`Sources`, `Agents`, `Approvals`, `Skills`).                            | Singular when count = 1, plural otherwise. Centralised in a `pluralize(label, count)` helper.                                                                                                             |
| **Source row Live data badge** | Always renders `Updated <relative time>` from `last_modified_at`.                      | Live connectors (`salesforce`, `snowflake`, `datadog`, `intercom`, `pagerduty`) render `Live data` instead of a stale-snapshot timestamp. v1 uses a slug heuristic — no schema change.                    |
| **Skills tab visibility**      | Renders even when no skills are surfaced.                                              | Hidden when `skills.length === 0`; same rule for Draft and Approval (already hidden when empty per PR 3.2).                                                                                               |
| **Citation chip shape**        | Rendered as outlined number pill via existing `citationRemarkPlugin` + `MarkdownLink`. | **Already correct after PR 3.1** — confirm via test fixture; no code change required.                                                                                                                     |

**The principles** (carried from PR 8.0):

1. **Reducer-driven, not heuristic.** The activity grouping rule lives in `eventReducer.ts` next to existing tool-call attachment logic. The FE never time-buckets events.
2. **Streaming-friendly.** Every grouping decision is **idempotent** — replaying `tool_call_started/completed` with the same `tool_call_id` after SSE reconnect produces the same group / steps. Out-of-order envelopes are tolerated by R3 in PR 8.0's contract.
3. **No new envelope kinds.** PR 8.1 (compression note) and PR 8.2 (subagent fleet) are still required for _those_ surfaces; this PR only adds renderer-layer logic that piggybacks on existing `tool_call_*` envelopes.

LoC estimate: frontend ≈ +280 (reducer rule + `pluralize` + footer hints + Skills conditional + Live-data branch) · design-system ≈ +100 (brand-glyph SVGs + tests).

---

## 1 · PRD

### 1.1 Goals

1. **Activity card collapses noisy runs without losing detail.** A run that fires 6 reads in parallel currently renders as 6 stacked harness rows; the design's "compress tool calls so prose stays the focus" rule wants the 6 collapsed into a single line `Reading 6 sources across 4 tools` with a caret to expand. The threshold is `≥ 4` rows so the common "search + list + open" three-step run keeps inline.
2. **Connector glyphs read at a glance.** The user's screenshot shows brand-coloured logo squares (Notion N on white, Drive multi-colour, Slack `#` on aubergine). The current `<AppIcon>` letter-on-grey doesn't carry brand recognition. We ship a **token-driven mapping** for the top 9 connector slugs.
3. **Composer footer is honest about state.** The hints row tells the user (a) how to interact (`↵ send`, `⇧+↵`, `/`) and (b) what's running (`Atlas Reasoning · Sources cited inline`). When a run is mid-stream we hide the hints — the visual focus belongs to the streaming prose.
4. **Tab labels are grammatically correct.** Showing `Approvals 1` when the count is one is sloppy; the design uses `Approval` / `Approvals` per count. One helper, applied everywhere counts and labels coexist.
5. **Live data signals freshness.** A Salesforce row that's joined live is fundamentally different from a Notion doc that hasn't changed in 5 days. The footer wording flips so users understand the temporal contract of each citation without reading a manual.
6. **Empty tabs hide.** A Skills tab with `0` is chrome noise. The design shows the workspace pane with only the tabs that have content.
7. **Confirm citation chips are already pill-shaped.** The PRD claim is that PR 3.1 ships pill-shaped chips, not `<sup>`. We verify with a test fixture; if false, we fix.

### 1.2 Non-goals

- **Topbar single-row layout** — kept on the followup pile. The current two-row layout (identity + controls) works at every viewport ≥ 760px; the design's one-row variant pushes model/depth into the composer (PR 8.0 spec'd it that way already). Moving it is a layout refactor that changes responsive behaviour; defer.
- **Brand-coloured `<AppIc>` per-deploy override.** v1 ships a fixed mapping. Per-tenant brand customisation is a future PR.
- **Per-event elapsed clock on `<HarnessRow>`** — confirmed dropped in PR 8.0. The card head carries elapsed; the rows do not.
- **Multi-select / pin / drag-reorder on sidebar** — Wave 2 PR F3.
- **Audit log / API keys / privacy / billing UIs** — Wave 3.

### 1.3 Success criteria

- ✅ A run with 4 consecutive `tool_call_started/completed` envelopes (no `model_delta` between them) renders as **one** `<ActivityCard>`. The card head is a single line with `presentation.summary` from the **last** completed step (R5 in PR 8.0). The body is a list of `<HarnessRow>`s in the order they completed.
- ✅ A run with 1, 2, or 3 consecutive harness rows renders inline (no card chrome).
- ✅ `model_delta` between two `tool_call_started`s ends the current group; the next `tool_call_started` starts a fresh group.
- ✅ Replaying the same envelope sequence twice produces identical state (idempotent on `tool_call_id`).
- ✅ `<AppIc>` for each of `notion / drive / slack / salesforce / confluence / github / linear / figma / snowflake` renders an SVG glyph keyed off the slug; everything else renders the existing letter-circle.
- ✅ Composer footer renders `↵ send · ⇧+↵ new line · / skills` left-aligned and `{model.display_name} · Sources cited inline` right-aligned. Hidden when `state.thread.isRunning`.
- ✅ Workspace pane tab strip renders `Source` / `Sources` / `Agent` / `Agents` / `Approval` / `Approvals` / `Skill` / `Skills` per count via the `pluralize` helper.
- ✅ Source rows for citations whose `connector_slug ∈ {salesforce, snowflake, datadog, intercom, pagerduty}` render `· Live data` in the footer; everything else renders `· Updated <relative time>`.
- ✅ Skills tab is hidden from the workspace pane when no skills are surfaced; auto-shows when the first skill renders.
- ✅ A unit test asserts citation chips render as `<a className="aui-citation-chip">` (not `<sup>`); existing visual is correct.
- ✅ Streaming handshake byte-identical pre/post merge. `make test` green; FE Vitest green; design-system typecheck green.

### 1.4 User stories

| #    | Persona                                               | Story                                                                                                                                                                                                                                                                                 |
| ---- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US-1 | **Sarah · 6-tool research run**                       | Asks for a recap. The agent fires `slack.search` → `slack.thread` → `drive.list` → `drive.open` → `notion.search` → `notion.open`. Today: 6 rows stack, prose pushed below the fold. After 8.0.1: one `Reading 6 sources across 4 tools` card with caret, prose comes within seconds. |
| US-2 | **Sarah · short greeting**                            | Says `hi`. No tool calls. After 8.0.1: nothing changes — the activity-card path doesn't fire when there are no harness rows.                                                                                                                                                          |
| US-3 | **Marcus · 2-tool find**                              | Says `find brand voice`. Two harness rows: `search_corpus` then `drive.search`. After 8.0.1: both render inline (under threshold).                                                                                                                                                    |
| US-4 | **Devi · recipient view of a Salesforce-cited share** | Opens the shared chat. The cited Salesforce source row renders `· Live data` so she knows the figure she's reading is current as of the share, not stale. Citations rendered as outlined pills, not superscript.                                                                      |
| US-5 | **Cold visitor · empty welcome**                      | Lands on `/`. Workspace pane has only `Sources` and `Agents` tabs visible (Draft / Approval / Skills hidden). The chrome doesn't ask "Skill 0?" — it doesn't ask anything.                                                                                                            |
| US-6 | **Composer mid-thread**                               | Idle: footer reads `↵ send · ⇧+↵ new line · / skills` and `Atlas Reasoning · Sources cited inline`. User starts typing → still visible. Send pressed, run starts → footer hides; the prose has the floor. Run ends → footer reappears.                                                |
| US-7 | **A 4-tool run mid-reconnect**                        | SSE drops after the 3rd `tool_call_completed`; the screen reconnects with `?after_sequence=N`. The 4th `tool_call_completed` is replayed; the activity grouping rule re-evaluates and folds all 4 into a card. The first 3 don't double-render. (Idempotent on `tool_call_id`.)       |
| US-8 | **A long-tail connector slug**                        | A user installs a custom MCP server with slug `internal-rag`. Their citations render with the existing letter-circle `<AppIc>` (fallback) — no broken UI.                                                                                                                             |

---

## 2 · Spec

### 2.1 Activity-card grouping rule

The reducer ([apps/frontend/src/features/chat/chatModel/eventReducer.ts](apps/frontend/src/features/chat/chatModel/eventReducer.ts)) already attaches `tool_call_*` envelopes to assistant message parts. We add a lightweight grouping pass:

```ts
// chatModel/contentBuilders.ts (new helper)

export function appendToolCall(
  parts: AssistantMessagePart[],
  envelope: ToolCallPart,
): AssistantMessagePart[] {
  const last = parts[parts.length - 1];
  // R2: idempotent on tool_call_id.
  if (last?.type === "activity-group") {
    if (last.steps.some((s) => s.tool_call_id === envelope.tool_call_id)) {
      return parts.map((p) => (p === last ? mergeStep(last, envelope) : p));
    }
    return parts.map((p) =>
      p === last ? { ...last, steps: [...last.steps, toStep(envelope)] } : p,
    );
  }
  // No prior group + last part is a single tool-call → upgrade to a group.
  if (last?.type === "tool-call") {
    return [
      ...parts.slice(0, -1),
      { type: "activity-group", steps: [toStep(last), toStep(envelope)] },
    ];
  }
  // Otherwise start fresh.
  return [...parts, { type: "tool-call", ...envelope }];
}
```

**Render rule** (`AssistantMessage.tsx`):

- `type: "tool-call"` → `<ToolFallback>` (renders inline `<HarnessRow>` per PR 8.0).
- `type: "activity-group"`:
  - `steps.length < 4` → unwrap, render each step as inline `<HarnessRow>`.
  - `steps.length >= 4` → `<ActivityCard summary={lastStep.presentation.summary} elapsed={…}>` with body containing the steps.

**Group-end signal**: a `model_delta` envelope arrives between two `tool_call_started`s. The reducer commits the group and starts a new top-level part. Since `model_delta` already creates a fresh text part in the existing reducer, we get this for free — the next `tool_call_*` simply doesn't see an `activity-group` last and starts inline.

**Single-step short-circuit**: if a group ends with exactly one step, we do not render the card chrome — the inline `<HarnessRow>` is the right primitive.

### 2.2 Brand-aware `<AppIc>`

```ts
// packages/design-system/src/primitives/AppIc/glyphMap.ts

const GLYPHS: Record<string, { label: string; bg: string; fg: string; svg: ReactElement }> = {
  notion:     { label: "Notion",     bg: "#ffffff", fg: "#191919", svg: <NotionGlyph /> },
  drive:      { label: "Drive",      bg: "#ffffff", fg: "#1a73e8", svg: <DriveGlyph /> },
  slack:      { label: "Slack",      bg: "#4a154b", fg: "#ffffff", svg: <SlackGlyph /> },
  salesforce: { label: "Salesforce", bg: "#00a1e0", fg: "#ffffff", svg: <SalesforceGlyph /> },
  confluence: { label: "Confluence", bg: "#172b4d", fg: "#2684ff", svg: <ConfluenceGlyph /> },
  github:     { label: "GitHub",     bg: "#0d1117", fg: "#ffffff", svg: <GithubGlyph /> },
  linear:     { label: "Linear",     bg: "#5e6ad2", fg: "#ffffff", svg: <LinearGlyph /> },
  figma:      { label: "Figma",      bg: "#0d0d0d", fg: "#ffffff", svg: <FigmaGlyph /> },
  snowflake:  { label: "Snowflake",  bg: "#29b5e8", fg: "#ffffff", svg: <SnowflakeGlyph /> },
};
```

`<AppIcon name={slug}>` becomes:

```tsx
const glyph = GLYPHS[slug.toLowerCase()];
if (!glyph) {
  return <LetterCircle name={slug} color={fallbackColor} />;
}
return (
  <span
    className="ui-app-icon ui-app-icon--brand"
    style={{ background: glyph.bg, color: glyph.fg }}
    aria-label={glyph.label}
  >
    {glyph.svg}
  </span>
);
```

The component prop surface is unchanged — consumers still pass `name={connector.id}`. Brand awareness is internal.

### 2.3 Composer footer hints

```tsx
{
  !isRunning && (
    <div className="aui-composer__hint">
      <span>
        <kbd>↵</kbd> send
      </span>
      <span className="aui-composer__sep" />
      <span>
        <kbd>⇧</kbd>+<kbd>↵</kbd> new line
      </span>
      <span className="aui-composer__sep" />
      <span>
        <kbd>/</kbd> skills
      </span>
      <span className="aui-composer__grow" />
      <span className="aui-composer__model-meta">
        {activeModelName} · Sources cited inline
      </span>
    </div>
  );
}
```

`activeModelName` is read from the existing model-catalog state already plumbed into `<AssistantComposer>` (via `models` prop). New prop: optional `activeModelName: string | undefined` (defaults to `"Atlas"` if absent). Hidden via the existing `AuiIf` running predicate.

### 2.4 `pluralize` helper

```ts
// apps/frontend/src/features/chat/components/workspace/pluralize.ts
export function pluralize(
  singular: string,
  plural: string,
  count: number,
): string {
  return count === 1 ? singular : plural;
}

export function tabLabel(
  base: { singular: string; plural: string },
  count: number,
): string {
  return pluralize(base.singular, base.plural, count);
}
```

Applied at `WorkspaceTabs.tsx`:

```tsx
<Tab>{tabLabel({ singular: "Source", plural: "Sources" }, sources.length)} <Count>{sources.length}</Count></Tab>
<Tab>{tabLabel({ singular: "Agent", plural: "Agents" }, agents.length)} <Count>{agents.length}</Count></Tab>
<Tab>{tabLabel({ singular: "Approval", plural: "Approvals" }, pendingApprovals.length)} <Count>{pendingApprovals.length}</Count></Tab>
<Tab>{tabLabel({ singular: "Skill", plural: "Skills" }, skills.length)} <Count>{skills.length}</Count></Tab>
```

### 2.5 Source row `Live data` heuristic

```ts
// apps/frontend/src/features/chat/components/workspace/sourceFreshness.ts
const LIVE_CONNECTORS = new Set([
  "salesforce",
  "snowflake",
  "datadog",
  "intercom",
  "pagerduty",
]);

export function sourceFreshnessLabel(
  source: { connector_slug?: string | null; last_modified_at?: string | null },
  now: Date = new Date(),
): string | null {
  if (
    source.connector_slug &&
    LIVE_CONNECTORS.has(source.connector_slug.toLowerCase())
  ) {
    return "Live data";
  }
  if (!source.last_modified_at) {
    return null;
  }
  return `Updated ${relativeTime(source.last_modified_at, now)}`;
}
```

Wired into `SourceRow.tsx`:

```tsx
<span className="source-row__freshness">
  {sourceFreshnessLabel(source) ?? ""}
</span>
```

A future PR can replace the heuristic with a `freshness_kind: "live" | "snapshot"` field on `CitationSourceRef` when it lands; the helper signature accepts the broader shape so the swap is one line.

### 2.6 Skills tab conditional

```tsx
const showSkills = skills.length > 0;
const showDraft = drafts.length > 0;
const showApproval = approvals.pending.length + approvals.decided.length > 0;

return (
  <nav className="workspace__tabs">
    <Tab id="sources">
      {tabLabel(SOURCES, sources.length)} <Count>{sources.length}</Count>
    </Tab>
    <Tab id="agents">
      {tabLabel(AGENTS, agents.length)} <Count>{agents.length}</Count>
    </Tab>
    {showDraft && <Tab id="draft">Draft</Tab>}
    {showApproval && (
      <Tab id="approval">
        {tabLabel(APPROVAL, pending)} <Count>{pending}</Count>
      </Tab>
    )}
    {showSkills && (
      <Tab id="skills">
        {tabLabel(SKILLS, skills.length)} <Count>{skills.length}</Count>
      </Tab>
    )}
  </nav>
);
```

### 2.7 Citation chip verification

PR 3.1's `citationRemarkPlugin.ts` + `MarkdownLink.tsx` already emit anchor pills (`<a className="aui-citation-chip">`). We add a single fixture test that asserts the rendered DOM contains an `<a>` with `data-citation-id` and **does not** contain a `<sup>`. No code change unless the assertion fails.

```ts
// apps/frontend/src/features/chat/components/markdown/citationRemarkPlugin.test.ts
it("renders citation pills as anchors, not superscript", () => {
  const html = renderMarkdownToHtml("Per the brief [c123].");
  expect(html).toMatch(/<a[^>]+data-citation-id="123"/);
  expect(html).not.toMatch(/<sup/);
});
```

### 2.8 Streaming-friendliness contract (per-card)

All seven items obey the R1–R5 rules from PR 8.0 §2.10:

- **Activity grouping** (R1, R2, R3): created by first `tool_call_started` after a `model_delta` boundary; updated by each child `tool_call_*`; sealed by the next `model_delta`. Idempotent on `tool_call_id`. Out-of-order tolerated — a late completion fills its step in place.
- **Brand glyph** (R5): rendered from the slug only; presentation copy is unchanged. No envelope dependency.
- **Composer hints** (R5): `activeModelName` is read from the existing model-catalog plumbing.
- **`pluralize`** (R5): pure function on a count read from the existing reducers — does not introduce new state.
- **`Live data` badge** (R5): pure function over `connector_slug`; no envelope added.
- **Conditional tabs** (R3): hide-when-empty is reactive to the registry's `length`. Late `source_ingested` events flip Sources tab on; late `skill_*` events flip Skills tab on. Reverse never happens (empty doesn't unflip mid-run).

No new envelope kinds; no schema work.

---

## 3 · Verification

- **Unit tests:**
  - `chatModel/contentBuilders.test.ts` — append a sequence of 4 `tool_call_*` envelopes, assert one `activity-group` part with 4 steps.
  - Replay the same sequence; assert same final state.
  - Insert a `model_delta` between rows 2 and 3; assert two separate parts (2 inline + 2 inline).
  - `AppIc.test.tsx` — assert each of the 9 known slugs renders an SVG glyph and an unknown slug renders the letter-circle fallback.
  - `pluralize.test.ts` — covers count `0/1/2`.
  - `sourceFreshness.test.ts` — known live slug → `Live data`; known snapshot slug → `Updated <when>`; missing `last_modified_at` → null.
  - `citationRemarkPlugin.test.ts` — anchor-not-sup assertion.
  - `WorkspaceTabs.test.tsx` — Skills tab hidden at `0`, visible at `1+`.
- **Visual smoke:** `make dev`, walk a 6-tool run, confirm card grouping; walk an MCP-cited Salesforce share, confirm `Live data` footer; close all tabs, confirm only Sources / Agents render.
- **Regression:** existing FE Vitest suite continues to pass (target: 509 → 509+new, 0 failures introduced by this PR).
- **Boundary:** no new dependency in `package.json`. Brand glyphs ship as inline SVGs in `packages/design-system/src/primitives/AppIc/glyphs/*.tsx`.
- **Streaming:** existing fixture-driven replay tests in `chatModel/eventReducer.test.ts` re-run unchanged; one new fixture for the 4-step grouping rule.

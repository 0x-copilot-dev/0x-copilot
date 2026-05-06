# PR 8.0.2 — Atlas Visible Fidelity Gaps

> **Status:** Draft (PRD + Spec)
> **Plan reference:** Wave 8, follow-up to [PR 8.0](./pr-8.0-atlas-visual-fidelity.md) and [PR 8.0.1](./pr-8.0.1-atlas-visual-fidelity-followups.md). Closes the gaps observed in the post-merge screenshot smoke (`/tmp/atlas-shots/`).
> **Owner:** frontend (assistant-message brand mark, welcome greeting name, topbar single-row layout, connectors-pill empty-state, user-card workspace name) · ai-backend (zero) · backend (zero)
> **Size:** **S/M.** Net new code ≈ 180 LOC. Zero schema work, zero envelope changes.
> **Depends on:** ✅ PR 8.0, ✅ PR 8.0.1.

---

## 0 · TL;DR

Five small, visible gaps remain after PR 8.0 / 8.0.1. Each is a 5–40 LOC fix that finishes the design's headline promises:

| Gap                                                       | Today (post 8.0.1)                                                                                                                                | After 8.0.2                                                                                                                                                                                                   |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Assistant message lacks the brand mark**                | Prose flush-left, no avatar. The reader can't scan user vs assistant at a glance.                                                                 | Orange `A` mark anchored to the left of every assistant message; same `<LogoMark compact>` primitive the sidebar uses.                                                                                        |
| **Welcome greeting is impersonal**                        | `Good afternoon.`                                                                                                                                 | `Good afternoon, Sarah. What are we shipping today?` — read first-name from `auth.identity.display_name`; fall back to time-of-day-only when absent.                                                          |
| **Topbar collapses to two rows**                          | Identity row + a second row with model + thinking depth. The design wants one row; model lives in the composer; depth lives next to the composer. | Single row. `<ModelPill>` + `<ThinkingDepthControl>` move to the composer's tools row (where they always belonged per the design's prototype).                                                                |
| **Topbar connectors-pill empty state reads "All paused"** | Wrong copy when the user has zero connectors connected.                                                                                           | Renders `Connect a tool` (with `+` glyph) so the user knows what to do, mirroring the design's "click here to connect" affordance. The same primitive renders the brand-glyph stack when ≥ 1 connector is on. |
| **User-card workspace name shows raw `org_acme`**         | Workspace identity reads as a slug.                                                                                                               | Renders the workspace `display_name` (`Acme`) — fetched once per mount via the existing `listMyWorkspaces()` endpoint; fall back to slug when the response hasn't landed.                                     |

**Principles** (carried from PR 8.0 / 8.0.1):

1. **Reuse, don't rebuild.** Every fix consumes an existing primitive (`LogoMark`, `useAuth`, `listMyWorkspaces`, `<ModelPill>`, `<ThinkingDepthControl>`). No new components.
2. **Streaming-friendly.** None of these touch the envelope contract; all are presentation-layer wiring. R1–R5 from PR 8.0 §2.10 still apply.
3. **No new deps.** Pure FE work in existing files.

LoC estimate: frontend ≈ +180 / −60. CSS adjustments only (no new tokens).

---

## 1 · PRD

### 1.1 Goals

1. **Assistant messages are unmistakable.** A single glance at the prose tells the reader who's talking. Today the orange `A` is in the sidebar; the design wants it on every assistant turn too. The same `<LogoMark compact>` component is rendered, sized at 20px, anchored to the left of `aui-message__body`.
2. **Greetings respect the persona.** The design's welcome reads `Good afternoon, Sarah. What are we shipping today?` — that personalisation is the difference between "agent product" and "co-worker product." Read `auth.identity.display_name`'s first name.
3. **Topbar is one row.** The design's mock and the prototype both put model + depth in the composer. Our two-row layout is a transitional state. Move `<ModelPill>` and `<ThinkingDepthControl>` into the composer's tools row, between the layers icon and the send button. The topbar keeps a small read-only model badge (`● Atlas Reasoning`) — clicking it opens the same `<ModelPicker>` popover the composer uses.
4. **Topbar empty-states tell the user what to do.** `All paused` for an empty connector set is misleading — nothing is paused; nothing is _connected_. Render `Connect a tool` with a `+` glyph so the affordance is clear.
5. **Workspaces are humans, not slugs.** A 12-LOC fetch for `listMyWorkspaces()` plus a one-line look-up replaces `org_acme` with `Acme` in the user card.

### 1.2 Non-goals

- **Per-tenant brand colour** for the `A` mark. v1 ships Atlas-orange globally; per-deploy override is a future PR.
- **Welcome greeting localisation.** Time-of-day banding (morning / afternoon / evening / late) is in scope; locale-specific phrasings are not.
- **Topbar share / settings / panel buttons** — already correct.
- **Connector OAuth flow polish** — out of scope; we only fix the empty-state copy.

### 1.3 Success criteria

- ✅ Every `<AssistantMessage>` renders an orange `A` brand mark (20px, square-ish, accent fill, contrast text) anchored to the left of `aui-message__body`. User messages are unchanged.
- ✅ Welcome greeting reads `{tod-greeting}, {first-name}.` followed by the question line. When `display_name` is null, the comma-and-name suffix is omitted.
- ✅ The chat topbar has **one** flex row. Model pill renders inline with the status pill; thinking-depth lives in the composer footer (next to the model picker).
- ✅ When the user has zero `connected && globallyEnabled` connectors, the topbar pill shows `+ Connect a tool` (instead of `All paused ▾`).
- ✅ User card renders `{display_name} / {workspace_display_name} · {role}`. Workspace name resolves via `listMyWorkspaces()` and is keyed off the persona's current `org_id`.
- ✅ FE typecheck + Vitest suite green (target: 530 + new = 533+).
- ✅ Zero new JS errors in the screenshot smoke.

### 1.4 User stories

| #    | Persona                                      | Story                                                                                                                                                                                                           |
| ---- | -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US-1 | **Sarah · cold reload**                      | Hits `/`. Sees `Good afternoon, Sarah. What are we shipping today?`. Sees `SC · Sarah Chen / Acme · Employee` in the user card. Recognises herself, recognises her workspace, knows the agent knows who she is. |
| US-2 | **Sarah mid-thread**                         | Reads an assistant turn. The orange `A` on the left tells her at a glance this is the agent's voice — she doesn't have to parse content to know whose paragraph this is.                                        |
| US-3 | **Sarah swaps models mid-thread**            | Clicks the topbar's model badge → the same `<ModelPicker>` popover the composer uses appears. Picks a different model. Next prompt streams against the new model. The composer footer pill updates.             |
| US-4 | **Marcus · zero connectors connected**       | Lands in chat with no MCP tools authorised. Topbar shows `+ Connect a tool` instead of misleading `All paused`. Click → the existing connectors popover, with the empty-state CTA pointing him to Settings.     |
| US-5 | **Devi · personal Gmail (single-workspace)** | Opens a shared chat. Brand pane on `/login` and the chat-side `A · Atlas` in the sidebar agree. Welcome greeting reads `Good evening, Devi.` — her display name is from the magic-link consume path.            |

---

## 2 · Spec

### 2.1 Assistant message brand mark

Update [`AssistantMessage.tsx`](apps/frontend/src/features/chat/components/messages/AssistantMessage.tsx) to render `<LogoMark compact />` on the left:

```tsx
return (
  <MessagePrimitive.Root className="aui-message aui-message--assistant">
    <span className="aui-message__avatar" aria-hidden="true">
      <LogoMark compact />
    </span>
    <div className="aui-message__body">
      <MessagePrimitive.Parts components={…} />
    </div>
    {showStrip ? <MessageSourcesStrip … /> : null}
    {showFooter ? <AssistantMessageFooter metrics={metrics} /> : null}
  </MessagePrimitive.Root>
);
```

CSS — anchor the mark inside the existing `.aui-message--assistant` grid. The mark's size shrinks via the existing `compact` flag.

### 2.2 Welcome greeting personalisation

[`ThreadWelcome.tsx`](apps/frontend/src/features/chat/components/thread/ThreadWelcome.tsx) already accepts `firstName` (the parent reads `auth.identity.display_name`). The greeting copy is the part to update:

```tsx
const greeting = greetingForHour(hour); // "Good morning" | "Good afternoon" | "Good evening" | "Working late"
const headline = firstName ? `${greeting}, ${firstName}.` : `${greeting}.`;

return (
  <div className="aui-welcome">
    <h1 className="aui-welcome__headline">
      {headline} <em>What are we shipping today?</em>
    </h1>
    {/* existing 4-card grid */}
  </div>
);
```

`greetingForHour` is a pure function in `apps/frontend/src/features/chat/utils/greeting.ts` — already exists for `firstNameFromDisplayName`. Add a sibling for the time-of-day banding.

### 2.3 Topbar single-row + composer-side model/depth

[`Topbar.tsx`](apps/frontend/src/features/chat/components/shell/Topbar.tsx) drops the `atlas-topbar__row--controls` row entirely. The model + depth controls move to the composer's tools row in [`AssistantComposer.tsx`](apps/frontend/src/features/chat/components/composer/AssistantComposer.tsx).

```tsx
// Topbar.tsx — single row only
<header className="atlas-topbar">
  <div className="atlas-topbar__row atlas-topbar__row--identity">
    <div className="atlas-topbar__left">…crumb + title…</div>
    <div className="atlas-topbar__right">
      <StatusPill … />
      <ConnectorsPill … />
      <UsageMeter … />
      <ModelBadge value={selectedModel} models={models} onChange={onModelChange} />  {/* small read-only pill that opens the picker popover */}
      <ShareSlot />
      <SettingsButton />
      <PanelToggle />
    </div>
  </div>
</header>
```

```tsx
// AssistantComposer.tsx — model + depth join the tools row
<div className="aui-composer-action-wrapper">
  <PlusMenu />
  <ConnectorsTrigger />
  <ThinkingDepthControl value={depth} onChange={onDepthChange} compact />
  <ModelPicker value={model} models={models} onChange={onModelChange} compact />
  <SendButton />
</div>
```

A **single** `<ModelPicker>` component (today's `<ModelPill>` rebadged for clarity) serves both the topbar's read-only badge and the composer's interactive picker. The badge is just `<ModelPicker compact readOnly>` — no fork.

`<ThinkingDepthControl>` already supports a `compact` prop in PR 8.0; reuse it.

### 2.4 Connectors-pill empty state

[`ConnectorsPill.tsx`](apps/frontend/src/features/chat/components/shell/ConnectorsPill.tsx) already branches on `active.length`. Add an empty branch:

```tsx
if (active.length === 0) {
  return (
    <button
      ref={ref}
      className="atlas-connectors-pill atlas-connectors-pill--empty"
      onClick={onOpen}
      aria-haspopup="dialog"
      aria-expanded={open}
    >
      <span aria-hidden="true">+</span>
      Connect a tool
    </button>
  );
}
```

CSS — `--empty` variant uses dim text + a dashed accent border, so it reads as a CTA, not a hard-state pill.

### 2.5 User-card workspace name resolution

[`UserCard.tsx`](apps/frontend/src/features/chat/components/sidebar/UserCard.tsx) already has a `useMyProfile` lazy fetch for `display_name`. Add a sibling `useMyWorkspaces` that reuses [`listMyWorkspaces()`](apps/frontend/src/api/meApi.ts):

```ts
function useMyCurrentWorkspaceName(orgId: string | null): string | null {
  const [name, setName] = useState<string | null>(null);
  useEffect(() => {
    if (orgId === null) return;
    let cancelled = false;
    void (async () => {
      try {
        const { workspaces } = await listMyWorkspaces();
        if (cancelled) return;
        const current = workspaces.find((w) => w.org_id === orgId);
        setName(current?.display_name ?? null);
      } catch {
        if (!cancelled) setName(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [orgId]);
  return name;
}
```

Use it in the user card — fall back to the slug when the response hasn't landed:

```tsx
const workspaceName = useMyCurrentWorkspaceName(orgId);
…
<span className="aui-user-card__sub">
  {workspaceName ?? orgId}
  {role ? ` · ${capitalize(role)}` : ""}
</span>
```

The `WorkspacePicker` in the same component already fetches the same endpoint — both reads share the in-flight request via the browser's `fetch` cache (no double network call in practice; if we want guaranteed dedup, we lift to a tiny `useResource` cache, but it's not required for v1).

### 2.6 Streaming-friendliness contract

All five fixes are presentation-layer; none changes the envelope contract. The R1–R5 rules from PR 8.0 §2.10 stand:

- **R1** — every block still ties to a single envelope kind for creation. The new `<LogoMark compact />` is purely cosmetic; it does not gate or transform any event.
- **R2** — reducers are unchanged; idempotency is preserved.
- **R3** — out-of-order arrival is unchanged.
- **R4** — SSE resume by `after_sequence=N` is unchanged.
- **R5** — presentation copy still comes from the projector for tool-call rows; only the welcome greeting and user-card meta are FE-derived from session identity (which is not envelope-driven).

---

## 3 · Verification

- **Unit tests** — small, mechanical:
  - `AssistantMessage.test.tsx` — assert `LogoMark` is rendered inside `aui-message--assistant`.
  - `ThreadWelcome.test.tsx` — already exists; extend with a fixture asserting `Good afternoon, Sarah.` when `firstName="Sarah"` and bare `Good afternoon.` when `firstName=null`.
  - `ConnectorsPill.test.tsx` — assert empty-state branch renders `Connect a tool` when `active.length === 0`.
  - `UserCard.test.tsx` — extend with a `listMyWorkspaces` mock returning `display_name: "Acme"`; assert the sub line reads `Acme · Employee`.
  - `Topbar.test.tsx` — assert the second row no longer renders (single-flex-row layout).
- **Visual smoke** — `make dev`, walk:
  1. Cold load → welcome greeting reads with name.
  2. Continue thread → orange `A` next to assistant prose.
  3. Topbar → one row, model badge inline.
  4. No connectors → topbar shows `+ Connect a tool`.
  5. User card → workspace name (not slug).
- **Streaming smoke** — kill SSE mid-run; reconnect; assert no double-renders, no missing assistant `A` marks (mark is part-agnostic).
- **No new deps** — `git diff package.json` empty.
- **Cross-service** — backend / facade / ai-backend tests unchanged; no envelope work.

---

## 4 · Out-of-scope (logged, not fixed here)

- **Workspace pane open by default on auth** — PR 3.2's auto-open uses heuristics; we don't change them.
- **Live `47%` usage meter for fresh personas** — needs at least one billed turn.
- **Login screen reachable in dev** — `DEV_AUTH_BYPASS=true` short-circuits; setting `DEV_AUTH_BYPASS=false` lets `/login` render. Document, don't change defaults.
- **Stale conversation persistence** — dev-seed leftovers; fixed by `make setup` re-seed, not by FE code.

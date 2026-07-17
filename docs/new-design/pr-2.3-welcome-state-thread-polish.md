# PR 2.3 — Welcome state + Thread polish

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 2, PR 2.3 in [`/Users/parthpahwa/.claude/plans/fetch-this-design-file-resilient-pumpkin.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md)
> **Owner:** frontend (welcome + message styling) · api-types (no change) · design-system (no change)
> **Size:** **S.** Pure presentation. Two component edits, one prompts-data extension, one CSS file. Zero migrations, zero new endpoints, zero new events, zero new dependencies.
> **Reads alongside:** [`pr-2.1-topbar-chrome-thinking-depth.md`](pr-2.1-topbar-chrome-thinking-depth.md), [`pr-2.2-sidebar-user-card-keymap.md`](pr-2.2-sidebar-user-card-keymap.md), [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md), [`packages/design-system/CLAUDE.md`](../../packages/design-system/CLAUDE.md)
> **Sibling docs (Wave 2):** PR 2.1 — topbar chrome · PR 2.2 — sidebar enhancements

---

## 0 · TL;DR

The current empty-thread welcome state (`apps/frontend/src/features/chat/components/thread/ThreadWelcome.tsx`) renders a small logo, "Hello there!", "How can I help you today?", and three suggestion cards driven by `assistant-ui`'s `ThreadPrimitive.Suggestions`. The Atlas design wants a **time-of-day greeting** ("Good morning" / "afternoon" / "evening" / "Working late after midnight") and **four** suggestion cards each carrying a **category eyebrow** (`DRAFT` / `SUMMARIZE` / `FIND` / `COMPARE`) so the user can scan by intent.

The chat thread also needs polish: the design says assistants render **flush left, no bubble**, with paragraph rhythm; users render **right-aligned in a soft surface bubble**. Today both roles share the same `aui-message__body` styling, with subtle differences. We tighten that.

This PR ships only:

- Time-aware greeting reducer (pure function of `Date`).
- Extended `CHAT_PROMPT_SUGGESTIONS` with a `category` field and a fourth row.
- New `ThreadWelcome.tsx` body using existing `Suggestion` primitives and design-system `Card`.
- CSS changes in `apps/frontend/src/styles.css` to flush assistant messages left, drop the bubble, and right-align user bubbles.
- Adjustments in `AssistantMessage.tsx` and `UserMessage.tsx` only at the class-name / structural level — no logic change, no prop change, no events touched.

LoC estimate: FE ≈ 130 (welcome 60, prompts 25, CSS 45) · tests ≈ 110.

---

## 1 · PRD

### 1.1 Problem

Today's welcome state ([`ThreadWelcome.tsx`](../../apps/frontend/src/features/chat/components/thread/ThreadWelcome.tsx)):

```tsx
<section className="aui-welcome">
  <LogoMark compact />
  <h2>Hello there!</h2>
  <p>How can I help you today?</p>
  <div className="aui-suggestions">
    <ThreadPrimitive.Suggestions>{...}</ThreadPrimitive.Suggestions>
  </div>
</section>
```

It's friendly but generic. The Atlas design replaces the static greeting with one that adapts to time of day, and re-frames the suggestions so the user can scan by intent (`DRAFT`, `SUMMARIZE`, `FIND`, `COMPARE`) rather than reading the title sentences. The design also explicitly _removes_ the eyebrow ("ATLAS · ENTERPRISE SEARCH") and the connector strip from earlier iterations — both were ruled "chrome heavy" in the design's decisions log:

> _"Cutting the eyebrow (already implied by the brand mark in sidebar), the lede (the suggestions communicate scope better than prose does), and the connectors strip (duplicate of the topbar pill) left the empty state feeling honest and inviting."_

Today's chat thread:

- `AssistantMessage.tsx` renders inside `aui-message aui-message--assistant`. The body shares the same surface treatment as user messages.
- `UserMessage.tsx` renders inside `aui-message aui-message--user`. Bubble alignment is controlled in CSS but currently both bubbles share visual weight.

The Atlas design wants:

- **Assistant**: flush left, no bubble, paragraph rhythm. Reads like an email body.
- **User**: bubble right-aligned, slightly elevated surface.

This visual difference matters because the assistant text is where citations and prose hang together — pulling it out of a bubble makes it scan like a draft, not a chat reply. (See Design Doc § Thread.)

### 1.2 Goals

1. Welcome state matches the design exactly: greeting headline (time-aware) + four suggestion cards with category eyebrows. No eyebrow above the greeting. No connector strip. No lede.
2. Suggestion data lives in **one place** — `apps/frontend/src/features/chat/prompts/index.ts` — with a `category` field added; consumers (welcome state) read from there. No copy duplicated in components.
3. Greeting is computed by a single pure function `greetingForHour(hour: number): string` so unit tests are trivial and timezone-correctness is the user's local time (browser `Date`).
4. Chat thread's left-vs-right styling is **CSS-only**. No React tree change. No new props.
5. Streaming behavior is unchanged: assistant messages still render through `MessagePrimitive.Parts` with all the existing `tools.by_name` mappings (PR 1.4 / 3.4 / etc.); we only adjust container class/markup.
6. The PR ships independently of PR 2.1 (topbar) and PR 2.2 (sidebar). It's a leaf — its only consumer surface is the empty thread + the message rows.

### 1.3 Non-goals

- **No new icon assets.** The 4 category eyebrows are text labels (`DRAFT`, `SUMMARIZE`, `FIND`, `COMPARE`), styled via tokens.
- **No suggestion personalization** (e.g., "based on your recent chats"). The four cards are static curated copy; personalized suggestions are a future surface.
- **No streaming-cursor redesign.** The existing `Reasoning` and `model_delta` components keep their behavior. This is paint, not pipes.
- **No assistant-message footer changes.** `AssistantMessageFooter.tsx` (rating, regenerate, copy, metrics) is untouched.
- **No Markdown plugin changes.** `MarkdownText` (Streamdown) remains the renderer for the assistant role per the [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md) markdown rule.
- **No tweaks panel or accent live-tuning.** Per the design's "not part of the shipped product" note.
- **No animation on category eyebrow.** "Cycling" the eyebrow text was discussed in design ("Decisions log") but cut for being noise.

### 1.4 Success criteria

- ✅ Opening a fresh chat between 05:00 and 11:59 local time shows "Good morning"; 12:00–17:59 → "Good afternoon"; 18:00–22:59 → "Good evening"; 23:00–04:59 → "Working late". The user's first name (from `useAuth().identity.display_name` first-token) appends when present, e.g. "Good morning, Sarah."
- ✅ Four suggestion cards render in a 2×2 grid at ≥ 760 px viewport, single column below; each has a category eyebrow above the title.
- ✅ Welcome state contains **no** eyebrow above the greeting, no lede paragraph, no connector strip.
- ✅ Clicking a suggestion card sends the prompt (existing `SuggestionPrimitive.Trigger send` behavior — unchanged).
- ✅ Assistant messages render flush left without a bubble; their text wraps to the column edge with a max width of ~70ch for readability.
- ✅ User messages render in a soft surface bubble, right-aligned, max-width ~60% of the column.
- ✅ Reduce-motion preference disables the streaming-cursor blink (existing CSS handles this; we only verify it still applies after our class-name changes).
- ✅ No new event types. No SSE handshake change. No backend change.
- ✅ Existing `AssistantMessage.test.tsx` and `UserMessage.test.tsx` snapshots update minimally (class names + structural attributes) and keep their semantic assertions.

### 1.5 User stories

| As…              | I want…                                                                  | So that…                                                                                              |
| ---------------- | ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| Sarah (end user) | a greeting that knows whether it's morning or 2 a.m.                     | the empty state acknowledges I'm here, instead of "Hello there!" forever                              |
| Sarah            | suggestion cards labeled by intent (DRAFT, SUMMARIZE, FIND, COMPARE)     | I can scan and click without reading whole sentences                                                  |
| Sarah            | the assistant's reply to read like a memo, not a chat bubble             | I can copy-paste it into a doc without "AI bubble" baggage                                            |
| Sarah            | my messages right-aligned with a clear bubble                            | I can see at a glance which lines I sent vs. which the agent did                                      |
| Marcus (admin)   | the welcome to feel honest — no marketing chrome                         | new members aren't greeted by a wall of chrome before they've typed anything                          |
| Future-Wave-3    | citations to render flush in assistant prose without a bubble in the way | when PR 3.1 ships citation chips, they sit inside paragraph rhythm, not on top of a bubble background |

---

## 2 · Spec

### 2.1 Welcome state — markup

```tsx
// apps/frontend/src/features/chat/components/thread/ThreadWelcome.tsx
<section className="aui-welcome aui-welcome--atlas">
  <h1 className="aui-welcome__greeting">{greeting}</h1>
  <ul className="aui-welcome__suggestions" aria-label="Suggested prompts">
    {CHAT_PROMPT_SUGGESTIONS.map((s) => (
      <li key={s.title}>
        <SuggestionPrimitive.Trigger
          className="aui-welcome-card"
          title="Send this suggestion"
          prompt={s.prompt}
          send
        >
          <span
            className="aui-welcome-card__eyebrow"
            data-category={s.category}
          >
            {CATEGORY_LABEL[s.category]}
          </span>
          <strong className="aui-welcome-card__title">{s.title}</strong>
          <span className="aui-welcome-card__sub">{s.label}</span>
        </SuggestionPrimitive.Trigger>
      </li>
    ))}
  </ul>
</section>
```

Notes:

- `<h1>` not `<h2>`: this is the page's primary heading when the thread is empty (the topbar's title is hidden in this state).
- `LogoMark` is removed from the welcome — the sidebar already carries the brand (see Design Doc decisions log).
- `<ul>` for screen-reader semantics; the visual grid is CSS.
- `data-category` attribute lets the eyebrow swap a single token color (no per-category CSS class explosion).
- `SuggestionPrimitive.Trigger` is the existing `@assistant-ui/react` primitive; `prompt` is passed in directly so we don't need `<Suggestion.Title>`/`<Description>` slot wrappers.

### 2.2 Suggestion data — extended shape

```ts
// apps/frontend/src/features/chat/prompts/index.ts
export type ChatPromptCategory = "draft" | "summarize" | "find" | "compare";

export type ChatPromptSuggestion = {
  category: ChatPromptCategory;
  title: string;
  label: string; // sub-line, single sentence
  prompt: string; // what gets sent on click
};

export const CATEGORY_LABEL: Record<ChatPromptCategory, string> = {
  draft: "DRAFT",
  summarize: "SUMMARIZE",
  find: "FIND",
  compare: "COMPARE",
};

export const CHAT_PROMPT_SUGGESTIONS: ChatPromptSuggestion[] = [
  {
    category: "draft",
    title: "Draft the FY26 Q1 launch announcement",
    label: "Using the approved positioning + GTM plan",
    prompt:
      "Draft the FY26 Q1 launch announcement using the approved positioning + GTM plan. Pull citations and propose a Slack post for review.",
  },
  {
    category: "summarize",
    title: "Summarize last week in #launch-aurora",
    label: "Decisions, blockers, and who owns what",
    prompt:
      "Summarize last week in #launch-aurora. Group by Decisions, Blockers, and Owners.",
  },
  {
    category: "find",
    title: "Find the latest brand voice guidelines",
    label: "And tell me what changed since Q3",
    prompt:
      "Find the latest brand voice guidelines and tell me what changed since Q3.",
  },
  {
    category: "compare",
    title: "Compare our positioning vs Glean",
    label: "From the competitive frame doc",
    prompt:
      "Compare our positioning vs Glean using the competitive frame doc. Cite sources.",
  },
];

export const REGENERATE_PREVIOUS_RESPONSE_PROMPT =
  "Regenerate the previous response.";

export function mcpServerInstructionPrompt(displayName: string): string {
  return `Use the ${displayName} MCP server for this request.`;
}

export function skillInstructionPrompt(displayName: string): string {
  return `Use the ${displayName} skill for this request.`;
}
```

This change is **backward-compatible** for callers that read only `title` / `label` / `prompt`. Only the welcome state consumes `category`. Other files (`ChatScreen.tsx` passes `Suggestions(CHAT_PROMPT_SUGGESTIONS)` to `useAui`) keep working.

### 2.3 Greeting — pure function

```ts
// apps/frontend/src/features/chat/utils/greeting.ts
export function greetingForHour(
  hour: number,
): "morning" | "afternoon" | "evening" | "late" {
  if (hour >= 5 && hour < 12) return "morning";
  if (hour >= 12 && hour < 18) return "afternoon";
  if (hour >= 18 && hour < 23) return "evening";
  return "late";
}

const HEAD: Record<ReturnType<typeof greetingForHour>, string> = {
  morning: "Good morning",
  afternoon: "Good afternoon",
  evening: "Good evening",
  late: "Working late",
};

export function welcomeGreeting(now: Date, firstName: string | null): string {
  const head = HEAD[greetingForHour(now.getHours())];
  return firstName ? `${head}, ${firstName}.` : `${head}.`;
}
```

Consumers in `ThreadWelcome` call `welcomeGreeting(new Date(), firstNameFrom(useAuth()))`. The function is `O(1)`, deterministic given `now`, trivially testable.

The reducer **re-runs on every render**, but `ThreadWelcome` only mounts when `messages.length === 0`, and at that point we re-evaluate at most once per minute via:

```ts
const [tick, setTick] = useState(() => Date.now());
useEffect(() => {
  const id = window.setInterval(() => setTick(Date.now()), 60_000);
  return () => window.clearInterval(id);
}, []);
const greeting = useMemo(
  () => welcomeGreeting(new Date(tick), firstName),
  [tick, firstName],
);
```

(Note: at minute-58, the user opens the empty thread; the greeting transitions to "Good afternoon" two minutes later mid-stare. Acceptable.)

### 2.4 Thread polish — CSS-only

`apps/frontend/src/styles.css` (or its CSS module sibling):

```css
/* assistant: flush left, no bubble */
.aui-message--assistant > .aui-message__body {
  background: transparent;
  border: 0;
  padding: 0;
  max-width: 70ch; /* readability cap */
  align-self: flex-start;
}

/* user: right-aligned bubble */
.aui-message--user {
  display: flex;
  justify-content: flex-end;
}
.aui-message--user > .aui-message__body {
  background: var(--color-surface);
  border: 1px solid var(--color-line-soft);
  border-radius: var(--radius-lg);
  padding: 8px 14px;
  max-width: min(60ch, 60%);
  align-self: flex-end;
}

/* welcome state */
.aui-welcome--atlas {
  display: flex;
  flex-direction: column;
  gap: 24px;
  padding: 64px 32px;
  max-width: 760px;
  margin: 0 auto;
}
.aui-welcome__greeting {
  font:
    600 32px/1.1 -apple-system,
    Inter,
    sans-serif;
  letter-spacing: -0.025em;
}
.aui-welcome__suggestions {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  padding: 0;
  margin: 0;
  list-style: none;
}
@media (max-width: 760px) {
  .aui-welcome__suggestions {
    grid-template-columns: 1fr;
  }
}
.aui-welcome-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 16px;
  border: 1px solid var(--color-line-soft);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  text-align: left;
  cursor: pointer;
}
.aui-welcome-card:hover {
  background: var(--color-surface-2);
}
.aui-welcome-card__eyebrow {
  font: 600 11px/1 -apple-system;
  letter-spacing: 0.1em;
  color: var(--color-text-dim);
}
.aui-welcome-card__eyebrow[data-category="draft"] {
  color: var(--color-accent);
}
.aui-welcome-card__eyebrow[data-category="summarize"] {
  color: var(--color-success);
}
.aui-welcome-card__eyebrow[data-category="find"] {
  color: var(--color-warn);
}
.aui-welcome-card__eyebrow[data-category="compare"] {
  color: var(--color-text-mute);
}
.aui-welcome-card__title {
  font-size: 14.5px;
  line-height: 1.35;
}
.aui-welcome-card__sub {
  font-size: 12px;
  color: var(--color-text-mute);
}
```

Tokens (`--color-line-soft`, `--color-surface`, `--color-surface-2`, `--color-accent`, etc.) are already defined by `@0x-copilot/design-system/styles.css` per PR 0.1's expected alignment with the design's palette.

### 2.5 Streaming impact — explicitly **none**

| Subsystem                                | Touched?                                                                                |
| ---------------------------------------- | --------------------------------------------------------------------------------------- |
| `runtime_events` schema                  | **No.**                                                                                 |
| SSE handshake                            | **No.**                                                                                 |
| `runtime_worker` job loop                | **No.**                                                                                 |
| `chatModel/eventReducer.ts`              | **No.**                                                                                 |
| `MarkdownText`, `Reasoning`, `ToolGroup` | **No.** Layout stays the same; only the container class on `aui-message__body` changes. |
| Capabilities middleware / tools          | **No.**                                                                                 |
| Audit chain                              | **No.**                                                                                 |
| Persistence (any service)                | **No.**                                                                                 |
| api-types                                | **No.** Suggestion shape is internal to the FE module.                                  |

The streaming cursor (the small blinking caret rendered while `model_delta` events arrive) keeps its existing markup. Removing the bubble background does not affect cursor behavior because the cursor is a `::after` pseudo on the last paragraph.

### 2.6 Permissions

Welcome state is rendered when the user has zero messages in the active thread; nothing here is gated. Suggestion clicks call the same `submitUserMessage` path as a typed prompt — permissions are enforced server-side as today.

### 2.7 Error semantics

| Condition                                                                                  | Behavior                                                                                                                             |
| ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------ |
| `useAuth()` returns `display_name === null`                                                | Greeting omits the comma + name: "Good morning."                                                                                     |
| Browser clock drift (e.g. user's clock is broken)                                          | Greeting reflects the broken clock. We do not call any server time API.                                                              |
| User locale uses a non-Western greeting concept                                            | Out of scope for v1; greeting is English. i18n is a separate workstream.                                                             |
| Suggestion send fails (e.g., backend 500)                                                  | Existing error pathway in `submitUserMessage` (`setItems` with a status row).                                                        |
| `assistant-ui` `Suggestions(CHAT_PROMPT_SUGGESTIONS)` no longer accepts the extended shape | Compile-time fail in TypeScript; we accept whatever extra fields it ignores. The `category` field is read only by our own component. |
| `setInterval` for minute-tick fires after `ThreadWelcome` unmounts                         | Cleanup function clears it. Vitest fake-timers cover.                                                                                |

### 2.8 Accessibility

- `<h1>` for the greeting; suggestions are an `<ul>` with `aria-label="Suggested prompts"`; each card is a `<button>` (via `SuggestionPrimitive.Trigger`).
- Eyebrow text is screen-reader-readable (it's not visually hidden); a category label is meaningful context for assistive tech.
- Reduce-motion preference (`@media (prefers-reduced-motion: reduce)`) keeps cards static (no hover transition, no greeting fade).
- Color contrast for eyebrows ≥ 4.5:1 against `--color-surface` per WCAG AA (verify via the existing color contrast audit when running design-system tests).

### 2.9 What we do NOT add

- **No icon library import.** Each card is text-only; the design intentionally avoided icons in the welcome.
- **No greeting localization framework.** We ship English. Adding `react-intl` or `i18next` for one string is over-engineering.
- **No animated category eyebrow swap.** Earlier design iterations cycled the eyebrow text — the design doc explicitly cut this for noise.
- **No `<motion.div>` framer-motion**. The hover transition is one CSS line.
- **No connector strip on the welcome.** Per the design's decisions log, this duplicated the topbar pill.
- **No theming-affordance on the welcome.** Theme/accent already lives in Settings → Appearance (PR 4.1).

---

## 3 · Architecture

### 3.1 Where this lives in the system

```
   ┌────────────────────────────────────────────────────────────┐
   │  ChatScreen.tsx  (existing controller)                     │
   │   useAui({ suggestions: Suggestions(CHAT_PROMPT_SUGGESTIONS) })
   └─┬──────────────────────────────────────────────────────────┘
     │ (unchanged)
     ▼
   ┌──────────────────────────────┐
   │ ThreadBody.tsx               │  (existing)
   │  if no messages → <ThreadWelcome />
   │  else           → <Messages /> via assistant-ui primitives
   └─┬─────────────────────┬──────┘
     │                     │
     ▼                     ▼
   ThreadWelcome    AssistantMessage / UserMessage
     (NEW body)      (CSS-only polish on the body container)
        │
        ▼
   prompts/index.ts  +  utils/greeting.ts
     (extended)         (new pure module)
```

### 3.2 Streaming — explicitly unchanged

The empty state has no streaming. The non-empty state preserves every wire and every component. Only the container CSS changes.

A side-by-side of what an `assistant` message looks like before and after, focusing on the surface only:

```
   BEFORE:                                AFTER:
   ┌──────────────────────────────┐     ┌──────────────────────────────────────────────┐
   │ A                            │     │                                              │
   │ ┌─────────────────────────┐  │     │ Per the positioning [c1] and the GTM plan…   │
   │ │ Per the positioning…   │  │     │ Citations preserved; flush-left text;        │
   │ │                         │  │     │ paragraph rhythm; max-width ~70ch.           │
   │ └─────────────────────────┘  │     │                                              │
   │      [Copy] [↻] [👍]         │     │      [Copy] [↻] [👍]                         │
   └──────────────────────────────┘     └──────────────────────────────────────────────┘
```

The "A" avatar mark is dropped (per design — the brand mark is in the sidebar; an inline "A" badge per assistant message is chrome). Footer actions stay.

```
   USER:
                                         ┌──────────────────────────────┐
                                         │ Find the brand voice doc.    │
                                         └──────────────────────────────┘
                                          right-aligned in a soft surface bubble
```

### 3.3 DRY — what we reuse vs. what we add

| Concern                  | Reuse                                                                                                                                                                        | Add                                                               |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| Suggestion primitive     | `SuggestionPrimitive.Trigger` from `@assistant-ui/react`                                                                                                                     | —                                                                 |
| Suggestions registration | `Suggestions(CHAT_PROMPT_SUGGESTIONS)` already in `ChatScreen.tsx:774`                                                                                                       | —                                                                 |
| Greeting computation     | `Date.prototype.getHours`                                                                                                                                                    | one pure module `utils/greeting.ts` (~25 LOC)                     |
| First-name extraction    | `useAuth().identity.display_name` (existing)                                                                                                                                 | one helper `firstNameFrom(identity)` (~6 LOC, place in same file) |
| Token color palette      | `--color-accent`, `--color-success`, `--color-warn`, `--color-text-dim` (design-system)                                                                                      | data-attribute selectors (~6 LOC CSS)                             |
| Card primitive           | `Card` exists in design-system but the welcome card is bespoke (button-shaped, button semantics). We render a `<button>` directly, not a `Card`, to keep keyboard semantics. | bespoke per design                                                |
| Markdown rendering       | `MarkdownText` for assistant; `PlainText` for user (existing)                                                                                                                | —                                                                 |
| Footer actions           | `AssistantMessageFooter.tsx` (existing)                                                                                                                                      | —                                                                 |
| Bubble surface           | `--color-surface`, `--color-line-soft` from design-system tokens                                                                                                             | one `.aui-message--user .aui-message__body` rule update           |
| Reduce-motion semantics  | existing media query in styles.css                                                                                                                                           | one new selector inside the same query                            |

**Net new code:** one greeting module (≈ 25 LOC), one prompts data extension (≈ 25 LOC), the welcome JSX (≈ 60 LOC), CSS (≈ 45 LOC). ≈ 155 LOC. Tests ≈ 110.

### 3.4 No third-party dependency added

Surveyed:

- **`react-intl` / `i18next`** — for localized greetings. Skipped: not in scope (i18n is a separate workstream); adds ~30 KB gz for one string.
- **`framer-motion`** — for hover transitions and greeting fades. Skipped: a single `transition: background 120ms ease` covers all motion.
- **`time-of-day` / `greeting-time`** — micro-libs that wrap the same `getHours` switch. Skipped: 25 LOC vs. a dependency.
- **`react-helmet-async`** — to update `<title>` based on the welcome state. Skipped: out of scope; `<title>` is owned by `App.tsx`.

Net dep delta: **zero.**

### 3.5 Edge cases

| Case                                                          | Behavior                                                                                                                        |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| User has `display_name = "Sarah Chen"`                        | Greeting reads "Good morning, Sarah." (first token only).                                                                       |
| `display_name` is empty string or `null`                      | Greeting reads "Good morning." (no comma, no name).                                                                             |
| `display_name` contains only whitespace                       | Same as above; whitespace trimmed.                                                                                              |
| User opens the empty thread at 22:59 and sits for two minutes | Greeting transitions from "Good evening, Sarah." to "Working late, Sarah." on the next minute tick.                             |
| User has `prefers-reduced-motion: reduce`                     | Hover background swap on cards is instantaneous; cursor blink (existing) also disabled (existing rule).                         |
| Thread becomes non-empty (first user message lands)           | `ThreadBody` swaps from `<ThreadWelcome />` to message rendering; `ThreadWelcome` unmounts; minute-tick interval cleaned up.    |
| User selects a suggestion that fails to send (offline / 5xx)  | Existing error path — a status row appears in the thread; welcome state is gone (the user message was optimistically appended). |
| Streaming-cursor visual regression after CSS change           | Verified by `apps/frontend/test/streaming-cursor.test.tsx` (existing) plus a visual diff in CI.                                 |
| User on a small phone (< 760 px)                              | Suggestions stack 1-column; greeting wraps; no horizontal scroll.                                                               |
| User in light mode (PR 0.1 + future PR 4.1)                   | Same tokens; CSS resolves to the light-scheme values automatically.                                                             |

### 3.6 Sequence — fresh chat, time-aware welcome, click suggestion, first run

```
Sarah                          ChatScreen                ThreadWelcome              ai-backend
  │                              │                          │                            │
  │  app loads, no chats         │                          │                            │
  │ ───────────────────────────► │                          │                            │
  │                              │  GET /v1/agent/conversations (existing)               │
  │                              │ ───────────────────────────────────────────────────► │
  │                              │ ◄ 200 [] (no chats yet) ─────────────────────────── │
  │                              │  conversationId = null; items = []                   │
  │                              │                          │                            │
  │  ThreadBody.messages.length === 0 → <ThreadWelcome />   │                            │
  │                              │  greeting = welcomeGreeting(new Date(), "Sarah")     │
  │                              │  → "Good morning, Sarah."                             │
  │                              │                          │                            │
  │  clicks "DRAFT — Draft the FY26 Q1 launch announcement" │                            │
  │ ─────────────────────────────────────────────────────────────►                       │
  │                              │  SuggestionPrimitive.Trigger.send                    │
  │                              │  → submitUserMessage({ prompt: "Draft the FY26 Q1…" })│
  │                              │  POST /v1/agent/conversations (new chat)             │
  │                              │ ───────────────────────────────────────────────────► │
  │                              │ ◄ 200 { conversation_id } ─────────────────────────── │
  │                              │  POST /v1/agent/runs                                 │
  │                              │ ───────────────────────────────────────────────────► │
  │                              │ ◄ 200 { run_id } ─────────────────────────────────── │
  │                              │  open SSE stream                                     │
  │                              │ ◄═════════════════════════════════════════════════════ events
  │                              │  ThreadWelcome unmounts; messages render             │
  │                              │  AssistantMessage paints flush-left, no bubble       │
```

### 3.7 Test plan

**Pure unit**

- `apps/frontend/src/features/chat/utils/greeting.test.ts`
  - `greetingForHour` table-test for every hour boundary (0, 4, 5, 11, 12, 17, 18, 22, 23).
  - `welcomeGreeting` with null and named identity.
- `apps/frontend/src/features/chat/prompts/index.test.ts`
  - Length === 4; categories cover `draft / summarize / find / compare`.

**Component**

- `apps/frontend/src/features/chat/components/thread/ThreadWelcome.test.tsx`
  - Snapshot at 800×600 viewport.
  - Click sends the prompt (mock `aui.threadComposer.send`).
  - Reduce-motion path applies the right CSS tokens (vitest jsdom + matchmedia stub).
  - Minute-tick: simulate `vi.useFakeTimers()` to advance from 22:59 to 23:00; assert greeting transitions.
- `apps/frontend/src/features/chat/components/messages/AssistantMessage.test.tsx`
  - Updated snapshot reflects new container class ad-hoc; existing semantic assertions (tools rendered, footer present) preserved.
- `apps/frontend/src/features/chat/components/messages/UserMessage.test.tsx`
  - Updated snapshot reflects right-aligned bubble class; existing semantic assertions preserved.
- `apps/frontend/test/streaming-cursor.test.tsx` — sanity check that the cursor still appears on the last paragraph after CSS change.

**Visual / a11y**

- Run the existing frontend a11y check (axe-core integration if present) to confirm contrast on category eyebrows and welcome surface ≥ 4.5:1.

### 3.8 Rollout

- **Flag-free.** New welcome and message styles ship in one PR. No parallel mode.
- **Zero migration. Zero new endpoint. Zero new dep.**
- **Backout** = revert the PR. Consumers of `CHAT_PROMPT_SUGGESTIONS` who only read `title/label/prompt` are unaffected by the type widening.
- **Telemetry** (existing): the existing suggestion-click event is unchanged; we don't add a new event for "category clicked" because the category is encoded in the `prompt` text and visible in the existing analytics.

### 3.9 Open questions

1. **i18n.** Translations for the four greetings + four category labels are out of v1 but a known follow-up. We do _not_ add an `t()` shim now; when i18n lands it can wrap one place.
2. **Personalized suggestions.** "Continue last conversation" or "Pick up Q1 launch" is tempting but conflicts with the design's "honest empty" philosophy. Defer.
3. **Card icons.** The design opted for text labels. If usability research shows users want a glyph, we add one icon per category (consistent with `AppIcon` pattern) in a follow-up — easy because data already carries `category`.
4. **Cursor vs. flush-left.** Verifying visual parity for the streaming cursor when it sits at the right edge of a flush-left paragraph (no bubble background to clip against). Covered in tests.

---

## 4 · Acceptance checklist

- [ ] `apps/frontend/src/features/chat/components/thread/ThreadWelcome.tsx` renders the time-aware greeting and four suggestion cards with category eyebrows; no eyebrow above the greeting; no LogoMark inside the welcome.
- [ ] `apps/frontend/src/features/chat/utils/greeting.ts` exports `greetingForHour` and `welcomeGreeting`; both pure; tested on hour boundaries and identity variants.
- [ ] `apps/frontend/src/features/chat/prompts/index.ts` exports the extended `ChatPromptSuggestion` type with `category` and the `CHAT_PROMPT_SUGGESTIONS` array of length 4 covering all four categories. Existing helpers untouched.
- [ ] `apps/frontend/src/styles.css` adds the welcome and message styling rules; existing reduce-motion and streaming-cursor rules continue to apply.
- [ ] Assistant messages render flush-left, no bubble, max-width ~70ch; user messages render right-aligned in a soft surface bubble with max-width ~60% / 60ch.
- [ ] No new event types, no SSE handshake change, no new endpoints, no migration, no new dependency.
- [ ] `npm run typecheck --workspace @0x-copilot/frontend` passes; `npm run build --workspace @0x-copilot/frontend` passes.
- [ ] `make test` green; backend / ai-backend pytest unaffected.
- [ ] Existing snapshot tests for `AssistantMessage` and `UserMessage` updated minimally; semantic assertions preserved.

---

## 5 · References

- [`apps/frontend/src/features/chat/components/thread/ThreadWelcome.tsx`](../../apps/frontend/src/features/chat/components/thread/ThreadWelcome.tsx) — replaced.
- [`apps/frontend/src/features/chat/components/messages/AssistantMessage.tsx`](../../apps/frontend/src/features/chat/components/messages/AssistantMessage.tsx) — class-name only.
- [`apps/frontend/src/features/chat/components/messages/UserMessage.tsx`](../../apps/frontend/src/features/chat/components/messages/UserMessage.tsx) — class-name only.
- [`apps/frontend/src/features/chat/prompts/index.ts`](../../apps/frontend/src/features/chat/prompts/index.ts) — extended.
- [`apps/frontend/src/features/chat/ChatScreen.tsx`](../../apps/frontend/src/features/chat/ChatScreen.tsx) — passes `Suggestions(CHAT_PROMPT_SUGGESTIONS)` to `useAui`; unchanged in this PR.
- [`packages/design-system/src/index.tsx`](../../packages/design-system/src/index.tsx) — tokens consumed via CSS variables; no API touched.
- [Design Doc (handoff)](../../../tmp/design-doc/0x-copilot/project/Design%20Doc.html) — § Welcome state, § Thread, § Decisions log ("Welcome state cut down to headline + 4 cards").
- [`docs/new-design/pr-2.1-topbar-chrome-thinking-depth.md`](pr-2.1-topbar-chrome-thinking-depth.md) — sibling PR; topbar title is hidden when the welcome is on screen.
- [`docs/new-design/pr-2.2-sidebar-user-card-keymap.md`](pr-2.2-sidebar-user-card-keymap.md) — sibling PR; sidebar carries the brand mark, freeing the welcome of the LogoMark.
- [WCAG 2.1 · Contrast (Minimum) 1.4.3](https://www.w3.org/WAI/WCAG21/Understanding/contrast-minimum.html) — eyebrow color contrast verification.
- [WAI-ARIA · Suggestion lists](https://www.w3.org/WAI/ARIA/apg/patterns/listbox/) — semantics for the suggestion `<ul>`.

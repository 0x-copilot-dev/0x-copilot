# PRD — P3: Onboarding composer + starter chips + first-run creation + acknowledgment + handoff

**Program:** First-Run Onboarding (FTUE) · **Phase:** P3 (README §6 / STATUS row P3) · **Placement:** faithful shared build in `packages/chat-surface` behind ports; bound by web + desktop hosts.
**Depends on:** P0 (first-run flag + gate seam), P1 (`FirstRunSurface` scaffold + `providerKeys` port + state machine), P2 (local-model card + Qwen preset + `localModels` pct signal). **Excludes:** shelved hosted-trial lane and the "Haiku starter" model row (SPEC.md v1 scope note).

---

## 1. Goal + scope

Deliver **State B (composer)** and **State C (acknowledgment)** of the FTUE and the run-create + handoff that connect them, at 1:1 parity with `docs/plan/first-run-onboarding/design-source/SPEC.md`.

In scope:

1. `OnboardingComposer` — the "What should we run first?" surface that mounts the **existing** `AssistantComposer` (model pill · tools · attach · send · `⏎ send · ⇧⏎ line`) with the first-run H1 + starter chips. No re-authoring of the composer/popovers.
2. `SuggestionChips` — a **new shared** presentational chips component + the 3 verbatim starter chips, incl. the `airdrop-claims.csv` pre-attach on _Explain a CSV_.
3. `FirstRunRunsPort` — the host-injected runs port doing the two-step create (`createConversation` → `createRun`); streaming is intentionally **not** in the port (the handoff target `RunDestination`/`useRunSession` owns streaming).
4. `useFirstRunLaunch` — orchestration hook: fires the two-step create when the model is ready, or **defers to "Queued — starts when the model lands"** while a local model downloads, then fires when it lands.
5. `Acknowledgment` — the ack screen ("Starting your first run" / "Queued — starts when the model lands") with the 3 echo lines (model · tools · privacy) mapped to run/launch status.
6. Handoff — `onComplete({conversationId, runId})` → host sets the P0 completion flag + lands the shell on the Run cockpit with the run pre-bound (already streaming).

Out of scope (other phases): the gate/BYOK card/key form (P1), local-model card + pct source (P2), wallet chip + tools popover parity + web-search toggle (P4), Safe/Sheets connectors (P6), the completion-flag storage + gate seam mechanics (P0).

---

## 2. Files to CREATE and EDIT

### CREATE — `packages/chat-surface/src/onboarding/` (SSOT surface)

| Path (relative to ROOT)                                            | Purpose                                                                                                                                                                                                        |
| ------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `packages/chat-surface/src/onboarding/OnboardingComposer.tsx`      | State-B surface: H1 "What should we run first?" + `SuggestionChips` + mounts `AssistantComposer` with the first-run placeholder/minRows; owns the composer ref so a chip pick can `setText` + `addAttachment`. |
| `packages/chat-surface/src/onboarding/SuggestionChips.tsx`         | New shared chips component (`.fr-chip` pills, accent svg) + `FirstRunSuggestion` type + `FIRST_RUN_SUGGESTIONS` data (3 verbatim chips; CSV chip carries `attachmentId`).                                      |
| `packages/chat-surface/src/onboarding/Acknowledgment.tsx`          | State-C ack: pure presentational; renders variant title + the 3 `.ln` echo lines with jade check.                                                                                                              |
| `packages/chat-surface/src/onboarding/useFirstRunLaunch.ts`        | Orchestration hook: two-step create via `FirstRunRunsPort`, the queued-deferral state machine, `StartRunError` surfacing, the ~1.5s handoff timer.                                                             |
| `packages/chat-surface/src/onboarding/firstRunAckLines.ts`         | Pure helper: `(engine, tools) → { modelLine, toolsLine, privacyLine }` (verbatim SPEC copy).                                                                                                                   |
| `packages/chat-surface/src/onboarding/ports/FirstRunRunsPort.ts`   | The runs port interface + `FirstRunCreateRunInput` / `FirstRunLaunchResult` types.                                                                                                                             |
| `packages/chat-surface/src/onboarding/onboarding.css`              | `.fr-hero h1`, `.fr-chips`/`.fr-chip`, `.fr-ack`/`.ln` — token-mapped classes (per SPEC §CSS inventory).                                                                                                       |
| `packages/chat-surface/src/onboarding/OnboardingComposer.test.tsx` | Unit: H1 verbatim; chip pick sets text (+ CSV addAttachment); onSubmit forwards `{text, attachments}`.                                                                                                         |
| `packages/chat-surface/src/onboarding/SuggestionChips.test.tsx`    | Unit: 3 verbatim titles/prompts; onPick payload; CSV chip `attachmentId`.                                                                                                                                      |
| `packages/chat-surface/src/onboarding/Acknowledgment.test.tsx`     | Unit: variant copy + 3 lines.                                                                                                                                                                                  |
| `packages/chat-surface/src/onboarding/useFirstRunLaunch.test.ts`   | Unit: ready→create→handoff (fake timers); not-ready→queued→flip→create; error→StartRunError; double-launch guard.                                                                                              |
| `packages/chat-surface/src/onboarding/firstRunAckLines.test.ts`    | Unit: verbatim line derivation for each engine/tools combo.                                                                                                                                                    |

### CREATE — host binders

| Path                                                                        | Purpose                                                                                                                                                                                                                                                                       |
| --------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `apps/desktop/renderer/onboarding/DesktopOnboardingComposerBinder.tsx`      | Binds `OnboardingComposer` to desktop substrate (reuse RunComposer's `filePicker`/`attachmentAdapter` singletons + model-catalog fetch), implements `FirstRunRunsPort` over the IPC `Transport` (two `transport.request` POSTs), resolves the CSV `File`, calls `onComplete`. |
| `apps/desktop/renderer/onboarding/DesktopOnboardingComposerBinder.test.tsx` | Binder test: two-POST create path + handoff + CSV attach.                                                                                                                                                                                                                     |
| `apps/frontend/src/features/onboarding/OnboardingComposerRoute.tsx`         | Web binder: implements `FirstRunRunsPort` over `agentApi.createConversation`/`createRun`, resolves the bundled CSV `File` via `fetch(assetUrl)`, calls `onComplete`.                                                                                                          |
| `apps/frontend/src/features/onboarding/OnboardingComposerRoute.test.tsx`    | Web binder test.                                                                                                                                                                                                                                                              |

### EDIT

| Path                                                                                                     | Change                                                                                                                                                                                                                                                                                                                                                                    |
| -------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `packages/chat-surface/src/index.ts`                                                                     | New delimited barrel block "Phase FTUE-P3 onboarding composer + chips + ack" exporting `OnboardingComposer`, `SuggestionChips`+`FirstRunSuggestion`+`FIRST_RUN_SUGGESTIONS`, `Acknowledgment`, `useFirstRunLaunch`, `firstRunAckLines`, and the `FirstRunRunsPort`/`FirstRunCreateRunInput`/`FirstRunLaunchResult` types. (Barrel-discipline per chat-surface/CLAUDE.md.) |
| `apps/desktop/renderer/destinationBinders.tsx`                                                           | `RunBinder` (line 550) gains optional `initialConversationId?: ConversationId` + `initialRunId?: RunId`; when present, skip the resolve effect (628-649) and pass `runId={initialRunId}` to `RunDestination` (683) so the handoff run streams immediately.                                                                                                                |
| P0 gate seam (`apps/desktop/renderer/bootstrap.tsx` around 94-104 + web `apps/frontend/src/app/App.tsx`) | On `onComplete(result)`: set the P0 `firstRunStore` flag, stash `{conversationId, runId}`, land on the Run cockpit, and thread the stash into `RunBinder`/the web run target. (Seam owned by P0; P3 supplies the `onComplete` contract.)                                                                                                                                  |

---

## 3. New component / port / type signatures

### 3.1 `FirstRunRunsPort` (host-injected — chat-surface stays port-only)

```ts
import type {
  ModelSelectionRequest,
  RunAttachmentRequest,
} from "@0x-copilot/api-types";

export interface FirstRunCreateRunInput {
  /** The composed prompt (chip prompt or typed text). */
  readonly userInput: string;
  /** Resolved model selection, or null to let the runtime default. */
  readonly model: ModelSelectionRequest | null;
  /** Client-inline attachments (the CSV chip → one file part). */
  readonly attachments?: readonly RunAttachmentRequest[];
}

export interface FirstRunLaunchResult {
  readonly conversationId: string;
  readonly runId: string;
}

/**
 * The two-step first-run create. The host implements it over its Transport:
 *   1. POST /v1/agent/conversations   → conversation_id
 *   2. POST /v1/agent/runs {conversation_id, user_input, model, attachments} → run_id
 * Identity is server-derived (facade overrides org/user) — the surface never
 * sends identity. `stream` is intentionally absent: the handoff target
 * (RunDestination/useRunSession) opens the SSE tail after handoff.
 */
export interface FirstRunRunsPort {
  createFirstRun(input: FirstRunCreateRunInput): Promise<FirstRunLaunchResult>;
}
```

### 3.2 `SuggestionChips`

```ts
import type { IconName } from "../icons"; // canonical Icon SSOT

export interface FirstRunSuggestion {
  readonly id: string;
  readonly icon: IconName;
  readonly title: string; // chip label, verbatim
  readonly prompt: string; // inserted into the composer, verbatim
  /** Present only on the CSV chip → host resolves to a File via resolveAttachment. */
  readonly attachmentId?: string;
}

export const FIRST_RUN_SUGGESTIONS: readonly FirstRunSuggestion[]; // 3 chips (see §5)

export interface SuggestionChipsProps {
  readonly suggestions?: readonly FirstRunSuggestion[]; // defaults to FIRST_RUN_SUGGESTIONS
  readonly onPick: (suggestion: FirstRunSuggestion) => void;
  readonly disabled?: boolean;
}
export function SuggestionChips(props: SuggestionChipsProps): ReactElement;
```

### 3.3 `OnboardingComposer`

Mirrors the `AssistantComposer` prop wiring in `RunComposer.tsx` (host-bound `attachmentAdapter`/`filePicker`/`renderPlusMenu`/instruction prompts), adds chips + the first-run copy. It owns the `ComposerHandle` ref (like RunComposer) so a chip pick calls `setText` + optional `addAttachment`.

```ts
import type { ComposerHandle } from "../composer";
import type {
  McpServer,
  ModelCatalogModel,
  Skill,
} from "@0x-copilot/api-types";
import type { AttachmentAdapter } from "../composer";
import type { FilePickerPort } from "../ports/FilePickerPort";
import type { AssistantComposerPlusMenuSlotArgs } from "../composer";

export interface OnboardingComposerProps {
  // --- host substrate wiring (identical shapes to RunComposer → AssistantComposer) ---
  readonly connectors: { servers: readonly McpServer[]; loading: boolean };
  readonly skills: { skills: readonly Skill[]; loading: boolean };
  readonly attachmentAdapter?: AttachmentAdapter;
  readonly filePicker: FilePickerPort;
  readonly renderPlusMenu: (a: AssistantComposerPlusMenuSlotArgs) => ReactNode;
  readonly skillInstructionPrompt: (displayName: string) => string;
  readonly mcpServerInstructionPrompt: (displayName: string) => string;
  readonly onShowConnectors: () => void;
  readonly onOpenSkillsSettings: () => void;
  readonly onOpenMcpSettings: () => void;

  // --- model controls (same as RunComposer; label may carry "· N%" from P2) ---
  readonly models: Array<ModelCatalogModel & { disabled?: boolean }>;
  readonly selectedModel: string;
  readonly onModelChange: (id: string) => void;
  readonly onAddCustomModel?: (slug: string) => void;

  // --- first-run specifics ---
  readonly suggestions?: readonly FirstRunSuggestion[];
  /** Host resolves a chip's attachmentId to a File (fetch/IPC lives in the host). */
  readonly resolveAttachment?: (attachmentId: string) => Promise<File | null>;
  /** Raised on send; the host binder maps CompleteAttachment[] → RunAttachmentRequest[]
   *  and drives useFirstRunLaunch.launch(). */
  readonly onSubmit: (payload: {
    text: string;
    attachments: ReadonlyArray<unknown>; // CompleteAttachment[] (chat-surface)
  }) => void | Promise<void>;
  /** Inline error above the composer (keyless send etc.) — reuses StartRunError. */
  readonly startError?: StartRunError | null;
  /** Route to the gate's KeyForm on a configuration_error CTA (not Settings). */
  readonly onAddKey?: () => void;
  readonly onDismissError?: () => void;
  readonly disabled?: boolean;
}
export const OnboardingComposer: ForwardRefExoticComponent<
  OnboardingComposerProps & RefAttributes<ComposerHandle>
>;
```

Placeholder = SPEC verbatim: `Tell it what you want in plain words — "watch my wallet", "draft the thread"…`. `minRows` — pass web's roomy `3` (this is a hero surface, not the narrow Run rail).

### 3.4 `useFirstRunLaunch`

```ts
export type FirstRunLaunchPhase =
  | "composing" // no send yet
  | "starting" // create in flight (model ready)
  | "queued" // send accepted, waiting for a downloading local model
  | "handoff" // created + within the ~1.5s hold before onComplete
  | "error";

export interface UseFirstRunLaunchOptions {
  readonly runs: FirstRunRunsPort;
  /** True when the selected engine can run NOW: BYOK connected, or local pct===100.
   *  P1/P2 derive it in the FirstRunSurface state machine and pass it here. */
  readonly modelReady: boolean;
  /** Resolved model selection for the run body. */
  readonly model: ModelSelectionRequest | null;
  /** Fired exactly once at handoff with the created run. */
  readonly onComplete: (result: FirstRunLaunchResult) => void;
  readonly handoffDelayMs?: number; // default 1500 (SPEC ~1.5s)
}

export interface UseFirstRunLaunch {
  readonly phase: FirstRunLaunchPhase;
  readonly error: StartRunError | null;
  /** Accepts the mapped run attachments; guards against double-launch. */
  readonly launch: (payload: {
    text: string;
    attachments: readonly RunAttachmentRequest[];
  }) => void;
  readonly reset: () => void;
}
export function useFirstRunLaunch(
  o: UseFirstRunLaunchOptions,
): UseFirstRunLaunch;
```

Semantics:

- `launch(p)` while `composing`: stash `p`. If `modelReady` → `starting`, call `runs.createFirstRun`; on resolve set result → `handoff` → `setTimeout(onComplete(result), handoffDelayMs)`; on reject → `error` with `parseTransportError` (reuse `errors/transportError`). If **not** `modelReady` → `queued` (no create yet).
- An effect: when `phase === "queued" && modelReady` flips true → run the same create path (`starting` → `handoff`). This is the **download-in-flight → "starts when the model lands"** path (JOURNEYS J1 step 4-5).
- Double-launch guard: `launch` is a no-op unless `phase === "composing"` (mirrors `isStartingRun` guard in RunDestination.tsx:344).
- `reset()` clears any pending timer (used on Skip / unmount).

### 3.5 `Acknowledgment` (pure)

```ts
export interface AcknowledgmentProps {
  readonly variant: "starting" | "queued";
  readonly modelLine: string; // e.g. "model — Qwen 3 4B · downloading 41%"
  readonly toolsLine: string; // e.g. "tools — web search"
  readonly privacyLine: string; // "key in your OS keychain" | "nothing leaves this machine"
}
export function Acknowledgment(props: AcknowledgmentProps): ReactElement;
```

Title map: `starting → "Starting your first run"`, `queued → "Queued — starts when the model lands"`. `useFirstRunLaunch.phase` maps: `starting|handoff → "starting"`, `queued → "queued"`.

### 3.6 `firstRunAckLines`

```ts
export interface FirstRunEngine {
  readonly kind: "local" | "key";
  readonly name: string;
  readonly pct?: number;
}
export interface FirstRunToolsState {
  readonly webOn: boolean;
  readonly connectors: readonly string[];
}
export function firstRunAckLines(
  engine: FirstRunEngine,
  tools: FirstRunToolsState,
): {
  readonly modelLine: string;
  readonly toolsLine: string;
  readonly privacyLine: string;
};
```

Copy (SPEC §Copy strings): model = `model — {name}` + (`· downloading N%` when local & pct<100, else `· on-device` for local-ready); tools = `tools — {web search|none}` + (`· {connector}…` when any); privacy = local → `nothing leaves this machine`, key → `key in your OS keychain`.

---

## 4. Precise wiring steps into the real code

### 4.1 Chip pick → composer (inside `OnboardingComposer`)

Hold the `ComposerHandle` via a local ref (pattern: `AssistantComposer.tsx:201-212` `setComposerRef`). On `SuggestionChips.onPick(suggestion)`:

1. `composerRef.current?.setText(suggestion.prompt)` (ComposerHandle.setText — Composer.tsx:204).
2. If `suggestion.attachmentId && resolveAttachment`: `const f = await resolveAttachment(id); if (f) await composerRef.current?.addAttachment(f);` (ComposerHandle.addAttachment — Composer.tsx:222; same call site the `+`-menu uses at AssistantComposer.tsx:246).
3. `composerRef.current?.focus()`.

### 4.2 Send → two-step create (host binder + hook)

`OnboardingComposer.onSubmit({text, attachments})` is raised from `AssistantComposer.onSubmit` (AssistantComposer.tsx:307-326 already joins skill instructions + forwards attachments). The **host binder** maps `attachments` (CompleteAttachment[]) → `RunAttachmentRequest[]` using the **exact mapper** already in `RunComposer.tsx:618-652` (`toRunAttachment`/`toRunContentPart` — the CSV becomes `{type:"file", filename, data, mime_type}`), then calls `launch({text, attachments: mapped})`.

`FirstRunRunsPort.createFirstRun` implementations:

- **Desktop** (`DesktopOnboardingComposerBinder`): two `transport.request` POSTs, mirroring `RunBinder.handleStartRun` (destinationBinders.tsx:652-662) but preceded by a conversation create — `POST /v1/agent/conversations {title}` then `POST /v1/agent/runs {conversation_id, user_input, model, attachments}`; return `{conversationId, runId: run.run_id}`.
- **Web** (`OnboardingComposerRoute`): `createConversation(identity, {title})` then `createRun(conversationId, text, identity, {model, attachments})` (agentApi.ts:94-133, 433-472). Note web injects `org_id`/`user_id` in the body per its contract; desktop omits (IPC injects) — same divergence as today.

Title: derive from the prompt (reuse the web `titleFromPrompt` truncation; desktop can pass `text.slice(0, 60)`).

### 4.3 Queued case (download in flight)

The `FirstRunSurface` state machine (P1/P2) computes `modelReady` = `engine.kind==="key"` **||** (`engine.kind==="local" && pct>=100`), and passes it into `useFirstRunLaunch`. When a send arrives with `modelReady===false`, the hook holds `queued`; the `Acknowledgment` renders `variant="queued"` with `model — {name} · downloading N%`. When P2's pct SSE reaches 100 the surface flips `modelReady`, the hook's effect fires `createFirstRun`, and the ack switches to `starting` before handoff (JOURNEYS J1).

### 4.4 Handoff → RunDestination

`onComplete(result)` is the seam handed to the host by the P0 gate:

- **Desktop:** the gate seam (bootstrap.tsx:94-104 region) sets `firstRunStore` complete, stashes `result`, forces `activeDestination="run"`, and passes `initialConversationId={result.conversationId}`/`initialRunId={result.runId}` into `RunBinder`. `RunBinder` (edited) skips its own conversation resolve and passes `runId` to `RunDestination` (RunDestination.tsx:180-191 accepts `runId`); `useRunSession` streams it from `after_sequence=0` (useRunSession.ts:221-256) — **already streaming, no replay** (§2 EDIT).
- **Web:** the onboarding gateway sets the flag and navigates to the run/chat route with `result.conversationId` preselected (exact web run-target — open question §8).

### 4.5 Error surfacing

`useFirstRunLaunch.error` (a `StartRunError`) is passed to `OnboardingComposer.startError`. Render the same inline notice as `RunComposer.tsx:521-558` / `RunEmptyState.tsx:201-247`: primary `error.message`, and on `code === "configuration_error"` a CTA — but wired to **`onAddKey`** (back to the gate's KeyForm), since in FTUE the fix is "add a key", not "open Settings".

---

## 5. Parity notes (design class → design-system token/primitive)

Per SPEC.md §CSS inventory + README §2 map (design-system is SSOT; never hardcode hex). Reuse the inline-token style pattern already used across `RunEmptyState.tsx`/`RunDestination.tsx`.

| Design (`fr-*`)                                          | Implementation                                                                                                                                                                                                                                                                                                                       |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `.fr-hero h1` (`600 23px/1.2`, `-.015em`)                | `font-family: var(--font-display)`; `font-size: 1.4375rem` (23px — nudge SPEC 23px against the `--font-size-*` ladder, styles.css:62-66); `font-weight:600`; `letter-spacing:-0.015em`; `color: var(--color-text-strong)`.                                                                                                           |
| `.fr-chips` / `.fr-chip` (pill, accent svg)              | Flex-wrap row; each chip `border-radius: var(--radius-full)`; `background: var(--color-surface)`; `border: 1px solid var(--color-border)`; hover `--color-border-strong`; leading `<Icon>` from the icon SSOT tinted `var(--color-accent)`. Reuse the `aui-welcome-card` intent pattern from `ThreadWelcome.tsx:39-59` but as pills. |
| `.fr-ack` `.ln` (mono 10.5, jade check)                  | `font-family: var(--font-mono)`; `font-size: var(--font-size-2xs)` (styles.css:62); check glyph `color: var(--color-success)` (styles.css:169 — jade, **not** the sky accent).                                                                                                                                                       |
| `.cmp`, `.cmp-pill`, `.pop`, `.spin` (shared app chrome) | **Do not re-author** — they ARE `AssistantComposer` + `ModelPill` + `ToolPicker` (SPEC §CSS inventory note). Mount the real components.                                                                                                                                                                                              |
| Primary buttons (accent bg)                              | `background: var(--color-accent)`; `color: var(--color-accent-contrast)` (styles.css:162-164) — matches `ctaButtonStyle` in RunEmptyState.tsx:379-390.                                                                                                                                                                               |
| Per-provider dot colors (`#d97757` etc.)                 | Kept as inline swatch values (data, not app accent) — SPEC §Data. One-accent (sky) discipline holds.                                                                                                                                                                                                                                 |

Hint row: rely on `AssistantComposer.hintRender` (AssistantComposer.tsx:483-506) — it already renders `↵ send · ⇧+↵ new line · / skills · Sources cited inline` unconditionally (frontend/CLAUDE.md "Composer hint row" invariant). SPEC's `⏎ send · ⇧⏎ line` is satisfied by that row; do not add a second hint.

---

## 6. Test list

**Unit (vitest, colocated — `npx vitest run --root packages/chat-surface`):**

1. `SuggestionChips.test.tsx` — renders exactly 3 chips with verbatim titles (`Watch a wallet` / `Draft a launch thread` / `Explain a CSV`); `onPick` fires the full suggestion incl. verbatim `prompt`; only _Explain a CSV_ carries `attachmentId === "airdrop-claims.csv"`.
2. `OnboardingComposer.test.tsx` — H1 `What should we run first?` + verbatim placeholder; picking a chip calls `setText(prompt)`; the CSV chip additionally calls `resolveAttachment` → `addAttachment`; `onSubmit` forwards `{text, attachments}`; `startError` renders the inline notice + `onAddKey` CTA on `configuration_error`. (Mock `AssistantComposer`'s ref like `RunComposer.test.tsx`.)
3. `Acknowledgment.test.tsx` — `variant:"starting"` → `Starting your first run`; `variant:"queued"` → `Queued — starts when the model lands`; renders the 3 supplied lines with the jade check.
4. `useFirstRunLaunch.test.ts` (fake timers) — (a) `modelReady:true` → `launch` → `createFirstRun` called once → `phase:"handoff"` → `onComplete(result)` after `handoffDelayMs`; (b) `modelReady:false` → `phase:"queued"`, `createFirstRun` NOT called; rerender with `modelReady:true` → create fires → handoff; (c) `createFirstRun` rejects → `phase:"error"` + parsed `StartRunError`; (d) second `launch` while non-composing is a no-op; (e) `reset()` clears the pending handoff timer.
5. `firstRunAckLines.test.ts` — verbatim lines for `{local, pct:41}`, `{local, ready}`, `{key:"Claude Sonnet 4.5"}` × `{webOn, connectors:[]}` / `{webOff}` / `{connectors:["Safe{Wallet}"]}`.

**Host-binder unit:** 6. `DesktopOnboardingComposerBinder.test.tsx` — `createFirstRun` issues the two POSTs in order (`/v1/agent/conversations` then `/v1/agent/runs` with `conversation_id` + `user_input` + mapped `attachments`), returns `{conversationId, runId}`, and `onComplete` fires the handoff. (Follows `RunComposer.test.tsx`.) 7. `OnboardingComposerRoute.test.tsx` — web binder maps `CompleteAttachment` → `RunAttachmentRequest`, calls `createConversation`+`createRun` (mock agentApi), resolves the bundled CSV `File`.

**Live-stack (P7 harness; add P3 coverage now):** on the hermetic real-graph desktop stack (per `docs/plan/verification/`): 8. **J2 (BYOK):** gate→composer→send→ack "Starting…"→handoff→`RunDestination` streams the run (`run_queued`→`run_started`→`final_response`). 9. **J1 (local queued):** send with pct<100 → ack "Queued…"; drive pct→100 → run auto-fires → handoff → streaming.

**Regression:** `npm run typecheck --workspace @0x-copilot/chat-surface`, `--workspace @0x-copilot/frontend`, desktop typecheck; ESLint substrate boundary (no bare `fetch`/`window` in `packages/chat-surface`).

---

## 7. Acceptance criteria

1. State B renders the real `AssistantComposer` (model pill · tools · attach · send · hint row) — **not** a re-authored composer — under the H1 `What should we run first?` with the 3 starter chips.
2. Copy is byte-verbatim vs SPEC §Copy strings: H1, textarea placeholder, chip titles+prompts, both ack titles, and the 3 ack lines.
3. Picking _Explain a CSV_ inserts its prompt AND pre-attaches `airdrop-claims.csv` (an attachment pill shows; the run body carries a `file` content part with `mime_type: text/csv`).
4. A send with the model ready creates via `POST /v1/agent/conversations` then `POST /v1/agent/runs` (identity server-derived; no org/user in the desktop body), shows ack "Starting your first run", and hands off to a **live-streaming** `RunDestination` after ~1.5s.
5. A send while a local model is still downloading shows ack "Queued — starts when the model lands", creates nothing yet, and auto-fires the two-step create + handoff when pct reaches 100.
6. A keyless/failed send surfaces the actionable `StartRunError.message` + an "add a key" CTA (routes to the gate's KeyForm) — never a silent dead end and never the raw transport envelope.
7. Handoff sets the P0 completion flag and lands on the Run cockpit with the created run pre-bound (no shell remount, no replay); the surface calls the `onComplete` port, never a hard HTML navigation.
8. Parity: hero size/weight/tracking, chip pill geometry/hairline, ack jade check, primary-button contrast all resolve to design-system tokens; sky-only accent (no second accent) — passes a `ui-design-reviewer` pass vs `design-source/`.
9. `packages/chat-surface` stays substrate-clean (no bare `fetch`/`window`/`localStorage`; all I/O via `FirstRunRunsPort` + `FilePickerPort` + host slots). Barrel exports added in one delimited block. Both host binders updated together.

---

## 8. Risks / edge-cases

- **CSV through the runtime adapter.** The composer's `fileAttachmentAccept` (composer/fileAttachmentAccept.ts:2) includes `text/csv`, but the **web** `AtlasFileAttachmentAdapter.accept` is office+pdf only (`apps/frontend/src/features/chat/runtime/attachments/file.ts:16-24`). The CSV chip calls `addAttachment(file)` programmatically (bypasses the picker filter), so routing depends on the **composite** adapter sending `text/csv` to the **text** adapter. Verify the CSV lands as a valid `file`/text content part end-to-end (STATUS verify-at-impl item) before shipping; add a fixture-backed test.
- **Queued deferral leak.** If the user hits Skip or the surface unmounts while `phase:"queued"`, the pending launch + handoff timer must be cancelled (`reset()` on unmount / skip) so a late model-landed event can't fire a run into a completed FTUE.
- **Partial two-step failure.** If `createConversation` succeeds but `createRun` fails, surface the `StartRunError` and, on retry, reuse the already-created conversation (don't orphan a second one). Consider the host binder caching the conversationId between attempts.
- **Model pill "· N%" label** is P2's (the host builds the download-annotated catalog label; `ModelPill` just renders it). If P2 isn't merged, the ack `modelLine` falls back to the plain name — `firstRunAckLines` must tolerate `pct===undefined`.
- **Identity discipline.** Never place org/user/model keys in run bodies beyond the current contract; the facade overrides identity (JOURNEYS preamble). Web's body carries `org_id`/`user_id` (its established contract), desktop's does not — keep the two binders faithful to today's shapes rather than unifying.
- **Double-send / Enter spam.** The `launch` guard (`phase==="composing"`) plus `AssistantComposer`'s own submit re-entrancy guard (Composer.tsx:420) must both hold so a fast double-Enter can't spawn two conversations.
- **`useRunSession` run-list 404 tolerance.** The handoff binds via the explicit `runId` seam, which streams even when `GET /v1/agent/runs` isn't served (useRunSession.ts:183-195 degrades to empty) — so the handoff does not depend on the run appearing in the list. Good; keep it that way.

---

## Open questions (need a human decision)

See `openQuestions`.

---

## Open questions

- Web handoff target: does the web host land the pre-created run in `RunDestination` (the shared cockpit) or in the existing assistant-ui `ChatScreen`? The desktop path binds `RunDestination.runId`; the web run-surface binding is less settled and changes how `onComplete` is wired on web.
- Eager vs lazy conversation creation: P3 creates the conversation lazily on send. Should the conversation instead be created on gate/composer entry so the composer has a real conversationId (and the CSV/attachments could pre-upload)? Lazy is simpler and matches ChatScreen's two-step; confirm.
- Exact handoff hold: SPEC says "~1.5s". Confirm `handoffDelayMs=1500` and whether the queued→starting transition should ALSO hold 1.5s after the model lands, or hand off immediately once the run is created.
- Queued-state interactivity: while waiting for the model to land, is the ack read-only (SPEC implies it just waits), or should the user be able to cancel/edit the queued prompt and return to the composer?
- Starter-chip icons: SPEC references an icon per chip but names none. Confirm the three `IconName`s (from the icon SSOT) for Watch a wallet / Draft a launch thread / Explain a CSV.
- `airdrop-claims.csv` fixture: where does the bundled CSV live and what are its contents? The host binders need a real asset to resolve into a File (web via bundled asset URL, desktop via bundled resource/IPC).
- BYOK model line naming: the ack `model — {name}` should show the resolved model display name (e.g. "Claude Sonnet 4.5"). Confirm the source — the selected `ModelCatalogModel.name` from `/v1/agent/models`, or the provider default from the gate's KeyForm.

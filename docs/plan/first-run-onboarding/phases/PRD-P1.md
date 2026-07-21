# PRD — P1: First-Run Gate surface + BYOK card + inline key form (`FirstRunSurface`)

**Program:** First-Run Onboarding (FTUE) · **Phase:** P1 (size M) · **Branch:** `claude/0xcopilot-first-run-onboarding-d7eb30`
**Design source of truth:** `docs/plan/first-run-onboarding/design-source/SPEC.md` (verbatim copy + `.fr-*` CSS inventory), README §1–§3.
**Read-only research anchors:** all paths below are relative to ROOT.

---

## 1. Goal + scope

Build the **presentational, substrate-agnostic `FirstRunSurface`** (the `.fr` inner surface only — the Electron/browser window IS the OS chrome, per SPEC note) inside `packages/chat-surface/src/onboarding/`, plus the two host binders that mount it at the post–sign-in seam. P1 delivers a **faithful, fully-working BYOK path** and a **complete 3-state machine scaffold**:

**In scope (P1):**

- `FirstRunSurface` shell: persistent **top bar** (brand `0xCopilot` with `0x` in accent · `walletChipSlot` · `skip → open the workspace`) + centered **main** + **footer** (`v2.1.0 · local build` + privacy line), driving the `stage ∈ {choice, dl, ready}` + `sent` state machine from SPEC §"State machine".
- **State A — Gate** (`Gate.tsx`): two `.fr-gcard`s — "Download the local model" and "Bring your own key" — with verbatim copy.
- **Inline BYOK KeyForm** (`KeyForm.tsx`): provider tri-toggle → `sk-…` password input → **Connect**, wired to `/v1/settings/provider-keys` through the existing `ProviderKeysPort`. On success: `engine = {kind:"key",…}`, `stage = ready`.
- Ports: reuse `ProviderKeysPort` + `ModelsPort`; define the new `FirstRunStore` port (shared with P0); `onSkip`/`onComplete` host callbacks.
- Token-mapped `onboarding.css` (host-imported like `composer.css`).
- Desktop (`bootstrap.tsx`) + web (`App.tsx`) host bindings + the barrel export.

**Explicitly deferred (owned by later phases — P1 provides the seams/slots only):**

- Local-model **Start download** SSE progress + curated Qwen 3 4B preset → **P2** (`onStartLocalDownload`/`localModelPct` props; P1's default transitions `stage=dl` with a placeholder body).
- Real onboarding **composer** (mount `AssistantComposer`) + starter chips + two-step run-create + **acknowledgment** (State C) → **P3** (`renderComposer`/`renderAcknowledgment` slots; P1 ships minimal placeholders so the state machine + tests are complete).
- **Wallet chip** component from `/v1/me/profile` → **P4** (P1 renders the `walletChipSlot` prop only).
- Trial hatch (`.fr-try`) + Haiku trial model row → **SHELVED** (omit; do not build).

**Non-goal:** P1 does not persist the first-run flag logic itself if P0 has landed it; see §8 Risk R1 for the interim.

---

## 2. Files to CREATE and EDIT

### CREATE — `packages/chat-surface/src/onboarding/` (presentational SSOT)

| Path                       | Purpose                                                                                                                                                                                                                        |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `firstRun.ts`              | Types (`FirstRunStage`, `FirstRunEngine`, `FirstRunStore`, `FirstRunKeyProvider`), copy constants (`FIRST_RUN_COPY`), provider data (`FIRST_RUN_KEY_PROVIDERS` w/ dot colors), and the `firstRunCompleteReason` union. No I/O. |
| `FirstRunSurface.tsx`      | The shell + state machine: top bar (brand/wallet-slot/skip) + `.fr-main` router (`choice`→`Gate`, `dl`/`ready`→composer slot, `sent`→ack slot) + footer. Owns `stage`/`engine`/`keyOpen`/`sent`.                               |
| `Gate.tsx`                 | State A: two `.fr-gcard`s + inline `KeyForm` reveal (`keyOpen`). Local "Start download" + BYOK "Add a key".                                                                                                                    |
| `KeyForm.tsx`              | Inline BYOK add-key: `SegmentedControl` tri-toggle → `sk-…` `TextInput[type=password]` → `<Button>` Connect; save-once via `ProviderKeysPort`; inline `role="alert"` on reject.                                                |
| `onboarding.css`           | The `.fr-*` classes, every color/size via a design-system `var(--…)` token (map in §5). Ships like `composer.css`/`workspace.css`.                                                                                             |
| `index.ts`                 | Subtree barrel (re-exports the public symbols for `src/index.ts`).                                                                                                                                                             |
| `FirstRunSurface.test.tsx` | State-machine + shell tests (skip, gate→dl, gate→keyform→ready, footer, slot rendering).                                                                                                                                       |
| `Gate.test.tsx`            | Both cards + verbatim copy; Start-download → `stage=dl`; Add-a-key reveals KeyForm.                                                                                                                                            |
| `KeyForm.test.tsx`         | Tri-toggle switches provider/placeholder; Connect calls `port.save` exactly once with plaintext; success → `onConnected`; reject → alert + no `onConnected`; token-only colors.                                                |

### EDIT — `packages/chat-surface/src/`

| Path       | Edit                                                                                                                                                                                                                            |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `index.ts` | Add a delimited `=== First-Run onboarding (P1) ===` export block hoisting `FirstRunSurface`, `Gate`, `KeyForm`, `FIRST_RUN_COPY`, `FIRST_RUN_KEY_PROVIDERS`, and all P1 types (barrel-discipline per `chat-surface/CLAUDE.md`). |

### CREATE + EDIT — desktop host (`apps/desktop/renderer/`)

| Path                    | Create/Edit | Purpose                                                                                                                                                                                                                                                                                  |
| ----------------------- | ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `FirstRunGate.tsx`      | CREATE      | Binder: builds `IpcTransport` → `createProviderKeysPort`/`createModelsPort`; reads `FirstRunStore` (IPC first-run.json from P0, else interim); if complete renders `children` (the shell), else renders `FirstRunSurface` with `onSkip`/`onComplete` → `markComplete` + reveal children. |
| `FirstRunGate.test.tsx` | CREATE      | Gates on the store; markComplete on skip/complete; renders children when complete.                                                                                                                                                                                                       |
| `bootstrap.tsx`         | EDIT        | Add `import "@0x-copilot/chat-surface/src/onboarding/onboarding.css";` (line ~10, beside `composer.css`); wrap the `SignInGate` render-prop child (currently `apps/desktop/renderer/bootstrap.tsx:95-103`) with `<FirstRunGate>…</FirstRunGate>`.                                        |

### CREATE + EDIT — web host (`apps/frontend/src/`)

| Path                                         | Create/Edit | Purpose                                                                                                                                                                                                                      |
| -------------------------------------------- | ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `features/onboarding/FirstRunRoute.tsx`      | CREATE      | Binder: builds a web `Transport` (same client `SettingsBinder` uses) → provider-keys/models ports; `FirstRunStore` backed by `LocalStorageKeyValueStore` namespaced by user id (P0), else interim; mounts `FirstRunSurface`. |
| `features/onboarding/FirstRunRoute.test.tsx` | CREATE      | Web gating + markComplete + child mount.                                                                                                                                                                                     |
| `app/App.tsx`                                | EDIT        | Import `onboarding.css`; at the authenticated seam (`apps/frontend/src/app/App.tsx:453`, the `return (<UserProfileProvider>…` shell tree) wrap the shell in `<FirstRunRoute>` so it gates before the shell mounts.           |

---

## 3. New component / port / type signatures

### `firstRun.ts`

```ts
export type FirstRunStage = "choice" | "dl" | "ready";

export type FirstRunEngine =
  | null
  | { readonly kind: "local"; readonly modelId: string | null }
  | {
      readonly kind: "key";
      readonly provider: string; // "anthropic" | "openai" | "openrouter"
      readonly label: string; // "Anthropic" …
      readonly dotColor: string; // inline swatch (data, NOT a token)
      readonly modelId: string | null; // resolved later from /v1/agent/models (P3)
    };

export type FirstRunCompleteReason = "skip" | "sent" | "configured";

/** Shared with P0. The HOST binder owns persistence; the surface never calls it. */
export interface FirstRunStore {
  isComplete(): boolean | Promise<boolean>;
  markComplete(reason: FirstRunCompleteReason): void | Promise<void>;
}

/** BYOK provider row for the KeyForm tri-toggle (SPEC §Data). */
export interface FirstRunKeyProvider {
  readonly id: string; // ProviderKeyProvider slug
  readonly label: string;
  readonly meta: string; // e.g. "Claude Sonnet 4.5"
  readonly dotColor: string; // inline swatch value
  readonly placeholder: string; // "sk-ant-…"
  readonly keyPrefix?: string; // client format-check hint
}

// SPEC §Data — dot colors are swatch data, not the app accent.
export const FIRST_RUN_KEY_PROVIDERS: readonly FirstRunKeyProvider[] = [
  {
    id: "anthropic",
    label: "Anthropic",
    meta: "Claude Sonnet 4.5",
    dotColor: "#d97757",
    placeholder: "sk-ant-…",
    keyPrefix: "sk-ant-",
  },
  {
    id: "openai",
    label: "OpenAI",
    meta: "GPT-5.2",
    dotColor: "#6aa88f",
    placeholder: "sk-…",
    keyPrefix: "sk-",
  },
  {
    id: "openrouter",
    label: "OpenRouter",
    meta: "200+ models",
    dotColor: "#9a7fd6",
    placeholder: "sk-or-v1-…",
    keyPrefix: "sk-or-",
  },
];

/** Verbatim copy (SPEC §"Copy strings"). One object so tests pin it. */
export const FIRST_RUN_COPY = {
  gate: {
    h1: "First, give it a model.",
    sub: "The only required choice — switch anytime.",
  },
  local: {
    title: "Download the local model",
    meta: "Qwen 3 4B · 5.6 GB · free forever",
    body: "Runs on this machine. Nothing you send ever leaves it.",
    btn: "Start download",
    note: "type your first prompt while it downloads",
  },
  key: {
    title: "Bring your own key",
    meta: "Anthropic · OpenAI · OpenRouter",
    body: "Frontier models, ready in ~30 seconds. Keys stay in your OS keychain.",
    btn: "Add a key",
  },
  keyForm: {
    placeholder: "sk-…  paste your API key",
    note: "stored in your OS keychain — never uploaded",
    btn: "Connect",
  },
  topbar: {
    brandLead: "0x",
    brandRest: "Copilot",
    skip: "skip — open the workspace →",
  },
  footer: {
    left: "v2.1.0 · local build",
    right: "keys in OS keychain · runs via your provider",
  },
} as const;
```

### `KeyForm.tsx`

```ts
export interface KeyFormConnected {
  readonly provider: string;
  readonly label: string;
  readonly dotColor: string;
  readonly keyHint: string; // masked suffix from ProviderKeySummary
  readonly modelId: string | null;
}

export interface KeyFormProps {
  readonly port: ProviderKeysPort; // reuse existing seam
  readonly providers?: readonly FirstRunKeyProvider[]; // default FIRST_RUN_KEY_PROVIDERS
  readonly onConnected: (result: KeyFormConnected) => void; // → surface sets engine=key, stage=ready
  readonly onCancel?: () => void;
}
```

**Behavior (mirrors `AddProviderKeyModal` security discipline, single-step per SPEC):** local `apiKey` state only; on Connect → optional `checkProviderKeyFormat(entry,key)` gate → `await port.save(provider, key.trim())` (the ONE place plaintext leaves) → `onConnected({…, keyHint: summary.key_hint})`; a rejected save shows `role="alert"` and stores nothing; plaintext cleared on unmount/provider switch.

### `Gate.tsx`

```ts
export interface GateProps {
  readonly keyPort: ProviderKeysPort;
  readonly keyProviders?: readonly FirstRunKeyProvider[];
  readonly onStartDownload: () => void; // → stage=dl (P2 wires SSE)
  readonly onKeyConnected: (r: KeyFormConnected) => void;
  readonly localDownloadDisabled?: boolean; // P1 may disable until P2
}
```

### `FirstRunSurface.tsx`

```ts
export interface FirstRunComposerCtx {
  // P3 fills; P1 placeholder
  readonly stage: Exclude<FirstRunStage, "choice">;
  readonly engine: FirstRunEngine;
  readonly models?: ModelsPort;
  readonly onSent: () => void; // → sent=true (P3 does run-create first)
}
export interface FirstRunAckCtx {
  readonly engine: FirstRunEngine;
}

export interface FirstRunSurfaceProps {
  readonly providerKeys: ProviderKeysPort; // BYOK seam (required)
  readonly models?: ModelsPort; // /v1/agent/models — NEVER a hardcoded list
  readonly onSkip: () => void; // top-bar skip (host: markComplete + navigate)
  readonly onComplete: (engine: FirstRunEngine) => void; // handoff (P3 run-create; P1 = markComplete+navigate)
  readonly walletChipSlot?: ReactNode; // P4 fills
  readonly appVersion?: string; // footer; default FIRST_RUN_COPY.footer.left
  readonly keyProviders?: readonly FirstRunKeyProvider[];
  // Deferred-phase seams (optional; P1 ships internal placeholders):
  readonly onStartLocalDownload?: () => void; // P2
  readonly localModelPct?: number | null; // P2
  readonly renderComposer?: (ctx: FirstRunComposerCtx) => ReactNode; // P3
  readonly renderAcknowledgment?: (ctx: FirstRunAckCtx) => ReactNode; // P3
  readonly initialStage?: FirstRunStage; // tests only
}
```

**State machine (SPEC §"State machine"):** `choice` renders `<Gate>`. `Start download` → `engine={kind:"local",modelId:null}, stage="dl"` and calls `onStartLocalDownload?.()`. KeyForm `onConnected` → `engine={kind:"key",…}, stage="ready"`. `dl`/`ready` render `renderComposer?.(ctx) ?? <ComposerPlaceholder/>`. When the composer ctx fires `onSent` → `sent=true` → render `renderAcknowledgment?.(ctx) ?? <AckPlaceholder/>` then `onComplete(engine)` (P3 owns the ~1.5s handoff timing). Top-bar `skip` → `onSkip()`.

### Host binder — `FirstRunGate.tsx` (desktop) / `FirstRunRoute.tsx` (web)

```ts
// desktop
export interface FirstRunGateProps {
  readonly bridge: WindowBridge;
  readonly session: RendererSession;
  readonly children: ReactNode; // the ChatShellForSession subtree
}
// Reads FirstRunStore (P0 IPC first-run.json, else interim LocalStorageKeyValueStore).
// complete → children; else FirstRunSurface{ providerKeys, models, onSkip, onComplete }.
```

---

## 4. Precise wiring steps

### 4.1 chat-surface barrel — `packages/chat-surface/src/index.ts`

Append after the last block (~line 1395):

```ts
// === First-Run onboarding (P1) — gate surface + BYOK key form ===
// Presentational SSOT for the FTUE gate. I/O via injected ProviderKeysPort/
// ModelsPort; skip/complete are host callbacks (host owns FirstRunStore).
export {
  FirstRunSurface,
  Gate,
  KeyForm,
  FIRST_RUN_COPY,
  FIRST_RUN_KEY_PROVIDERS,
  type FirstRunSurfaceProps,
  type GateProps,
  type KeyFormProps,
  type KeyFormConnected,
  type FirstRunStage,
  type FirstRunEngine,
  type FirstRunStore,
  type FirstRunKeyProvider,
  type FirstRunCompleteReason,
  type FirstRunComposerCtx,
  type FirstRunAckCtx,
} from "./onboarding";
// === end First-Run onboarding (P1) ===
```

### 4.2 Desktop seam — `apps/desktop/renderer/bootstrap.tsx`

- Line ~10 (beside the existing `composer.css`/`workspace.css` imports): add
  `import "@0x-copilot/chat-surface/src/onboarding/onboarding.css";`
- Replace the `SignInGate` child (currently `bootstrap.tsx:95-103`):

```tsx
{
  (session, signOut) => (
    <FirstRunGate bridge={window.bridge} session={session}>
      <ChatShellForSession
        session={session}
        onSignOut={signOut}
        router={router}
        keyValueStore={keyValueStore}
        presenceSignal={presenceSignal}
      />
    </FirstRunGate>
  );
}
```

- `FirstRunGate` builds its own `IpcTransport` (mirroring `ChatShellForSession`, `bootstrap.tsx:123-134`) keyed by `session.workspaceId`, then `createProviderKeysPort(transport)` + `createModelsPort(transport)`. `onComplete`/`onSkip` call `store.markComplete(reason)` then flip local state to render `children`. **P1 interim `FirstRunStore`** (if P0 not landed): back it with `LocalStorageKeyValueStore` keyed `first-run:${session.workspaceId}` (this is the desktop renderer, where `LocalStorageKeyValueStore` is already used — `bootstrap.tsx:88`).

### 4.3 Web seam — `apps/frontend/src/app/App.tsx`

- Add `import "@0x-copilot/chat-surface/src/onboarding/onboarding.css";` near the existing design-system CSS import (`App.tsx:5`).
- At the signed-in seam (`App.tsx:453`), wrap the returned shell tree in `<FirstRunRoute identity={auth.identity}>…</FirstRunRoute>`. `FirstRunRoute` builds the web `Transport` the `SettingsBinder` uses (`apps/frontend/src/features/settings/SettingsBinder.tsx:164`), the ports, and a `FirstRunStore` over `LocalStorageKeyValueStore` namespaced `first-run:${auth.identity.userId}`.

### 4.4 KeyForm → facade

`port.save` → `PUT /v1/settings/provider-keys/{provider}` (`createProviderKeysPort`, `packages/chat-surface/src/settings/data/providerKeys.ts:230`), facade route `services/backend-facade/src/backend_facade/settings_routes.py:42`. Response carries only `key_hint` (`packages/api-types/src/providerKeys.ts:41`). Optional live probe is `.../validate` (`settings_routes.py:43`) via `port.validate` — **off by default in P1** (see Open Question 5).

---

## 5. Parity map — `.fr-*` class → design-system token/primitive

Per SPEC §"CSS class inventory" + README §2 (design-system is the SSOT; never hard-code hex except the two dot swatches).

| Design class / property                                | design-system token or primitive                                                                                                                                             |
| ------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `.fr` bg / text / font / height                        | `--color-bg` / `--color-text` / `--font-sans` / `100%`+`100vh`, `overflow:auto`                                                                                              |
| `.fr-top` divider                                      | `border-bottom: 1px solid var(--color-border)`                                                                                                                               |
| brand `0xCopilot` (`0x` accent)                        | `--font-display`, `--font-weight-semibold`; `0x` span → `color: var(--color-accent)`; rest `var(--color-text)`                                                               |
| `.fr-wchip` (P4 slot)                                  | surface `--color-surface`, hairline `--color-border`, radius `--radius-full`, mono `--font-mono` @ `--font-size-2xs`; **jade dot** `--color-success`                         |
| `.fr-skiplink`                                         | ghost: `--color-text-muted`, hover `--color-text`, `--font-size-sm` (reuse `<Button variant="ghost" size="sm">` or a link)                                                   |
| `.fr-main`                                             | `width: min(640px, 92%)`, `margin-inline: auto`, gap `--space-xl`                                                                                                            |
| `.fr-hero h1` (600 · 23px/1.2 · -.015em)               | `--font-display`, `--font-weight-semibold`, `--font-size-2xl` (22.4px≈23px), `--line-height-tight`; `letter-spacing: -0.015em` (documented literal — tracking not tokenized) |
| `.fr-hero` sub                                         | `--color-text-muted`, `--font-size-sm`                                                                                                                                       |
| `.fr-gate`                                             | `display:grid; grid-template-columns: 1fr 1fr; gap: var(--space-lg)` (stack to 1-col under ~560px)                                                                           |
| `.fr-gcard` (radius 12)                                | `--radius-lg`, bg `--color-surface`, border `--color-border`, pad `--space-xl` (reuse `<Card>` shape)                                                                        |
| `.fr-gcard .ic` (accent icon)                          | `<Icon name="download"/>` / `<Icon name="key"/>`, `color: var(--color-accent)`                                                                                               |
| `.fr-gcard .meta` (mono 9.5)                           | `--font-mono`, `--font-size-2xs`, `--color-text-muted` (see Open Q1 on <11px)                                                                                                |
| `.fr-gcard .note` (mono 9)                             | `--font-mono`, `--font-size-2xs`, `--color-text-subtle`                                                                                                                      |
| `.gbtn--pri` (accent bg · `#0b0a0e` text)              | `<Button variant="primary">` → `.ui-button--primary` (bg `--color-accent`, text `--color-accent-contrast`)                                                                   |
| `.gbtn` (secondary)                                    | `<Button variant="secondary">` → `--color-surface-muted` + `--color-border`                                                                                                  |
| `.fr-kf` container                                     | column, gap `--space-md`, bg `--color-surface-muted`, radius `--radius-md`, pad `--space-lg`                                                                                 |
| `.fr-kf .prov` (tri-toggle)                            | **`SegmentedControl`** (`packages/chat-surface/src/settings/controls.tsx:79`) — per-option leading dot uses the provider `dotColor` swatch                                   |
| `.fr-kf` password input (mono)                         | `<TextInput type="password">` → `.ui-input`, override `font-family: var(--font-mono)`                                                                                        |
| `.fr-kf .knote`                                        | `--font-mono`, `--font-size-2xs`, `--color-text-subtle`                                                                                                                      |
| `.fr-kf` Connect btn                                   | `<Button variant="primary" size="sm">`                                                                                                                                       |
| `.fr-kf` error                                         | `role="alert"`, `--color-danger`, `--font-size-xs`                                                                                                                           |
| `.fr-foot` (mono 9.5 · space-between)                  | `--font-mono`, `--font-size-2xs`, `--color-text-subtle`, `justify-content: space-between`, `border-top: 1px solid var(--color-border)`                                       |
| `.fr-try` (trial hatch)                                | **OMIT** (shelved)                                                                                                                                                           |
| `.fr-chips`/`.fr-chip`, `.fr-ack`/`.ln`, `.cmp`/`.pop` | **P3** (reuse `AssistantComposer`/`ToolPicker`/`ModelPill`; ack: `--color-success` check + `--font-mono`) — not authored in P1                                               |

**One-accent discipline:** the only accent is sky `--color-accent`; the three provider dot hexes are inline swatch **data** (SPEC §Data), never the app accent. Theme-awareness is automatic since every color resolves a token (dark/light/slate all defined in `styles.css`).

---

## 6. Test list

### Unit — vitest (`npx vitest run --root packages/chat-surface`)

1. **`Gate.test.tsx`** — renders both cards; asserts verbatim copy from `FIRST_RUN_COPY` (H1, subs, metas, bodies, button labels); `Start download` click → `onStartDownload` fired; `Add a key` toggles `KeyForm` visible.
2. **`KeyForm.test.tsx`** — tri-toggle default = Anthropic (`sk-ant-…` placeholder); switching to OpenRouter updates placeholder; typing + Connect calls `port.save("anthropic", "sk-ant-xxx")` **exactly once** with the plaintext; success → `onConnected` with `keyHint` from the summary; a rejected `save` renders `role="alert"` and does **not** call `onConnected`; on provider switch the input is cleared (no plaintext leak); empty key disables Connect.
3. **`FirstRunSurface.test.tsx`** — top bar renders brand (`0x` accent span) + `walletChipSlot` + skip; skip → `onSkip`; `Start download` → `stage=dl` + placeholder composer slot; KeyForm connect → `stage=ready` + `engine.kind==="key"`; footer shows `appVersion` default; passing `renderComposer`/`renderAcknowledgment` slots renders them instead of placeholders; `initialStage` honored.
4. **Token guard** — snapshot/assert no raw `#` hex in rendered inline styles except the provider dot swatches (mirrors the ProviderKeysPage substrate discipline).

### Unit — host binders

5. **`apps/desktop/renderer/FirstRunGate.test.tsx`** — store `isComplete()→true` renders `children`, never the surface; `false` renders `FirstRunSurface`; `onSkip`/`onComplete` call `store.markComplete(reason)` then reveal `children`; providerKeys port wired to the transport.
6. **`apps/frontend/src/features/onboarding/FirstRunRoute.test.tsx`** — same gating over the web `KeyValueStore` store namespaced by user id.

### Live-stack (smoke, deferred to P7 but proven reachable)

7. BYOK path: with a real facade, KeyForm Connect issues one `PUT /v1/settings/provider-keys/anthropic`, response carries `key_hint` (no plaintext), surface advances to `ready` (extends the existing provider-keys live check).

Run commands: `npx vitest run --root packages/chat-surface`; `npm run typecheck --workspace @0x-copilot/chat-surface`; `npm run typecheck --workspace @0x-copilot/frontend`; desktop renderer vitest.

---

## 7. Acceptance criteria

- **AC1** `FirstRunSurface` mounts at the post–sign-in seam on **both** desktop (`bootstrap.tsx`) and web (`App.tsx`); a `firstRunComplete=true` store bypasses it entirely (returning-user path J5).
- **AC2** Gate renders the two cards with **byte-verbatim** copy from SPEC §"Copy strings" (pinned by `FIRST_RUN_COPY` + tests); no trial hatch, no Haiku row.
- **AC3** BYOK works end-to-end against `/v1/settings/provider-keys`: tri-toggle → paste `sk-…` → Connect → `PUT` → `stage=ready`, `engine.kind==="key"`; the plaintext key crosses exactly one call (`port.save`) and is never re-displayed/logged; a provider rejection surfaces inline without advancing.
- **AC4** `skip` calls `onSkip` → host `markComplete("skip")` → workspace (J4); the surface never hard-navigates to an HTML file (README §3.3).
- **AC5** Every design color/size resolves a design-system token (per §5 map); the only literals are the two dot swatches and the `-0.015em` tracking (both documented inline). Renders correctly in dark **and** light themes.
- **AC6** chat-surface eslint passes (no bare `window`/`fetch`/`localStorage`; no `apps/*` import); the barrel block is added per `chat-surface/CLAUDE.md` discipline.
- **AC7** The `dl`/`ready`/`sent` bodies are slot-injected (P1 placeholders present); no P2/P3 logic is inlined — local-download and the real composer/ack remain cleanly deferrable.
- **AC8** The model list surfaced anywhere in the flow derives from `ModelsPort`/`/v1/agent/models`, never `PROVIDER_CATALOG`'s hardcoded `models` arrays.

---

## 8. Risks / edge cases

- **R1 — P0 dependency (firstRunStore + seam).** P0 owns the persistent flag (desktop `first-run.json` IPC modeled on `apps/desktop/main/services/secure-storage-policy.ts`; web `KeyValueStore` by user id). **Mitigation:** P1 defines the `FirstRunStore` port and ships an **interim** impl inside each binder over `LocalStorageKeyValueStore` so P1 lands standalone; when P0's IPC store arrives, the binder swaps the impl behind the same port with zero surface change. Confirm ordering (Open Q3).
- **R2 — Substrate ban in chat-surface.** `window`/`fetch`/`localStorage` are eslint-banned in the package. **Mitigation:** all I/O through `ProviderKeysPort`/`ModelsPort`; CSS shipped as a `.css` asset host-imported (like `composer.css`); persistence lives in the host binder, not the surface.
- **R3 — KeyForm single-step vs the 3-step modal.** The SPEC KeyForm has no validate-spinner/model-pick step. Saving without a default-model pick means runs resolve the **workspace default** until P3's composer sets one. **Mitigation:** documented; P3 owns model selection. Do not surface `PROVIDER_CATALOG` hardcoded model arrays as a picker (violates AC8). (Open Q4.)
- **R4 — Sub-11px mono labels.** Design uses 9–10.5px; smallest token is `--font-size-2xs` (11.2px). Rounding up is the token-honest choice but is ~1–2px off pixel-parity. (Open Q1 — the P7 `ui-design-reviewer` pass is the arbiter; alternative is a new `--font-size-3xs` token in design-system.)
- **R5 — Two transport instances (desktop).** `FirstRunGate` and `ChatShellForSession` each build an `IpcTransport`. These are cheap opaque handles (the bearer is attached in main), so this is acceptable; if churn matters, lift transport creation above both in a follow-up.
- **R6 — Full-bleed layout.** `.fr` must fill the OS window (`100vh`/`100%`) with its own internal scroll; the host must not wrap it in the `Topbar`/`ContextPanel` chrome (mirror how `run`/`chats`/Settings render full-bleed in `ChatShell`).
- **R7 — Google/gemini slug.** Not a P1 concern: the KeyForm offers only Anthropic/OpenAI/OpenRouter (SPEC), so the `google`→`gemini` normalization in `saveDefaultModel` (`providerKeys.ts:283`) is untouched here.
- **R8 — Reduced-motion.** Any spinner/transition in `onboarding.css` must gate under `[data-reduce-motion]` + `prefers-reduced-motion` (mirror the `akm-spinner` pattern in `AddProviderKeyModal.tsx:110`).

---

## Open questions

- Sub-11px mono labels (SPEC uses 9/9.5/10/10.5px for `.meta`/`.note`/`.fr-foot`/`.knote`); the smallest design-system token is `--font-size-2xs` = 11.2px. Approve rounding up to `--font-size-2xs` (recommended, honors the no-hardcoded-font-size rule) or add a new `--font-size-3xs` token to design-system for byte-exact parity?
- Hero letter-spacing `-0.015em` and the two dot swatch hexes have no token home. Confirm keeping them as documented inline literals (tracking is not tokenized; dot colors are per-provider data, not the app accent).
- P0/P1 ordering: is P0 (firstRunStore + seam) landing first, or should P1 carry the interim `FirstRunStore` impl (desktop IPC first-run.json + web KeyValueStore)? This decides whether the binder files are net-new here or edits to P0's.
- KeyForm parity: the SPEC KeyForm is single-step (toggle → key → Connect) with NO default-model pick step (unlike the 3-step `AddProviderKeyModal`). Confirm P1 does save-only and defers default-model selection to the P3 composer's model pill (runs use the workspace default until then).
- Should Connect call the live `/validate` probe before `save` (extra ~1-2s round-trip, matches the tri-state verdict) or rely on `save`'s own server-side validation and surface its rejection inline (simpler, matches the SPEC's no-spinner KeyForm)? Recommendation: save-only in P1, live-validate optional behind the port.

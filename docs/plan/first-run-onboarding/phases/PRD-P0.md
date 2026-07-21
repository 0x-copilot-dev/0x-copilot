# PRD — P0: First-run flag + gate seam + skip

**Program:** First-Run Onboarding (FTUE) · **Phase:** P0 (size S) · **Branch:** `claude/0xcopilot-first-run-onboarding-d7eb30`
**Scope guardrails honored:** hosted-trial lane SHELVED (no trial hatch, no Haiku starter row); FTUE surface reuses existing components behind ports; no bare `fetch`/`window`/`localStorage` in `packages/chat-surface`; design-system is the token SSOT (no hardcoded hex).

All paths below are **relative to ROOT** = `/Users/parthpahwa/Documents/work/enterprise-search/.claude/worktrees/0xcopilot-first-run-onboarding-d7eb30`.

---

## 1. Goal + scope

**Goal.** Introduce the net-new "has this account seen the first-run experience?" flag and the gate seam that consumes it, so that P1–P3 can drop the real FTUE surface into a place that already exists, persists, and is per-user. P0 ships end-to-end for **Skip**: a signed-in user who has never completed first-run sees a placeholder FTUE; clicking _Skip → open the workspace_ sets the flag and lands in the shell; every subsequent launch bypasses the gate.

**In scope (P0):**

1. Main-process `FirstRunStore` → `userData/settings/first-run.json` (versioned, `chmod 0600`, per-account), cloned from `apps/desktop/main/services/secure-storage-policy.ts`.
2. Two app-local IPC channels `first-run.get` / `first-run.complete` (+ zod param schemas + preload allowlist), cloned from the capability/connector channel pattern.
3. `AuthService.accountKey(workspaceId)` so main derives the per-user key from the **verified** session (never a renderer-supplied id).
4. A shared `FirstRunStore` **port** + a shared `FirstRunGate` (with a placeholder body) in `packages/chat-surface/src/onboarding/`.
5. Desktop host binding (IPC) mounted at the `bootstrap.tsx` seam; web host binding (`KeyValueStore` namespaced by user id) mounted in `CopilotApp`.
6. Working **Skip** path; unit tests + one live-stack smoke.

**Explicitly NOT in P0** (deferred to later phases): the gate cards (BYOK / local-model), the inline key form, the composer, suggestion chips, the acknowledgment screen, the wallet chip, connectors, and the "finishing setup / first send also completes the flag" behaviors (P1/P3 add `via: "setup" | "first_run"` — the enum is defined now, only `"skip"` is wired).

---

## 2. Files to CREATE and EDIT

### CREATE

| Path                                                                  | Purpose                                                                                                                                                                        |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `apps/desktop/main/services/first-run-store.ts`                       | Main-process per-account flag store: read/write `userData/settings/first-run.json` (versioned, `0600`), keyed by hashed account key. Mirrors `secure-storage-policy.ts`.       |
| `apps/desktop/main/services/first-run-store.test.ts`                  | Unit tests (fake fs): default false, round-trip, per-account isolation, garbage-file → default, `0600` enforced.                                                               |
| `apps/desktop/main/services/first-run-channels.ts`                    | Dependency-free channel constants `FIRST_RUN_CHANNELS` + `isFirstRunChannel` (importable by the sandboxed preload). Mirrors `capabilities/channels.ts`.                        |
| `apps/desktop/main/services/first-run-schemas.ts`                     | Zod param schemas for the two channels (`FirstRunGetParamsSchema`, `FirstRunCompleteParamsSchema`) + return-shape types. Mirrors `capabilities/schemas.ts`.                    |
| `packages/chat-surface/src/ports/first-run-store.ts`                  | The `FirstRunStore` port interface + `FirstRunVia` type (substrate-agnostic; both hosts implement it).                                                                         |
| `packages/chat-surface/src/onboarding/FirstRunGate.tsx`               | Shared gate: `loading → first-run(placeholder) → complete(children)`; owns the Skip handler; renders a **placeholder** body (P1 replaces the body). Design-system tokens only. |
| `packages/chat-surface/src/onboarding/FirstRunGate.css`               | Full-screen gate chrome (token-mapped), mirroring the `signin.css` shell pattern.                                                                                              |
| `packages/chat-surface/src/onboarding/FirstRunGate.test.tsx`          | Unit tests with a fake `FirstRunStore`: shows placeholder when incomplete, shell when complete, Skip marks + reveals shell, error fails open to shell.                         |
| `apps/desktop/renderer/IpcFirstRunStore.ts`                           | Desktop `FirstRunStore` impl over `window.bridge.ipc` + `first-run.*` channels, bound to a `workspaceId`.                                                                      |
| `apps/desktop/renderer/IpcFirstRunStore.test.ts`                      | Unit tests with a fake bridge: get maps `{complete}`, mark posts `{workspaceId, via}`.                                                                                         |
| `apps/frontend/src/features/onboarding/KeyValueFirstRunStore.ts`      | Web `FirstRunStore` impl over the `KeyValueStore` port, namespaced by `org_id` + `user_id`.                                                                                    |
| `apps/frontend/src/features/onboarding/KeyValueFirstRunStore.test.ts` | Unit tests over a stub `KeyValueStore`: namespace correctness, set/get round-trip.                                                                                             |

### EDIT

| Path                                  | Change (one line)                                                                                                                                                                            |
| ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `apps/desktop/main/auth/index.ts`     | Add `accountKey(workspaceId): Promise<string \| null>` to `AuthService` (returns the loaded session's `claims.sub`).                                                                         |
| `apps/desktop/main/index.ts`          | In `wireTransportAndIpc`, add `accountKey` to `ActiveAuthService`; construct nothing extra (store is stateless free-functions); pass a `firstRun` handler bundle into `registerIpcHandlers`. |
| `apps/desktop/main/ipc/handlers.ts`   | Add `FirstRunHandlers` interface + `firstRun?` dep; register `first-run.get`/`first-run.complete` (via `parseOrThrow`); add both channels to teardown.                                       |
| `apps/desktop/preload/bridge.ts`      | Import `isFirstRunChannel`; add it to the `isBridgeChannel` union.                                                                                                                           |
| `apps/desktop/renderer/bootstrap.tsx` | Wrap `ChatShellForSession` (the seam at lines 94–104) in `<FirstRunGate store={new IpcFirstRunStore(window.bridge, session.workspaceId)}>`.                                                  |
| `apps/frontend/src/app/App.tsx`       | In `CopilotApp`, wrap `body` (or the shell) in `<FirstRunGate store={new KeyValueFirstRunStore(keyValueStore, identity)}>`.                                                                  |
| `packages/chat-surface/src/index.ts`  | Barrel-export `FirstRunGate` + the `FirstRunStore`/`FirstRunVia` port types in a new delimited P0 block.                                                                                     |

---

## 3. New signatures

### 3.1 Main-process store — `apps/desktop/main/services/first-run-store.ts`

Mirrors `secure-storage-policy.ts` exactly (free functions, injectable sync fs, `0600`). Account keys are **sha256-hashed** before use as JSON map keys so raw subjects (wallet address / Google `sub`) never sit in a plaintext file.

```ts
import { createHash } from "node:crypto";

export const FIRST_RUN_STORE_VERSION = 1 as const;
export type FirstRunVia = "skip" | "setup" | "first_run";

export interface FirstRunStoreFsSync {
  readFileSync(path: string): Buffer;
  writeFileSync(path: string, data: string, options?: { mode?: number }): void;
  mkdirSync(path: string, options: { recursive: boolean }): unknown;
  chmodSync(path: string, mode: number): void;
}

/** userData/settings/first-run.json */
export function firstRunStorePath(userDataDir: string): string;

/** True iff this account has an entry. Missing/garbage file → false (safe default: show FTUE). */
export function loadFirstRunComplete(
  userDataDir: string,
  accountKey: string,
  fs?: FirstRunStoreFsSync,
): boolean;

/** Idempotent read-modify-write of the accounts map; enforces 0600 even when the file pre-exists. */
export function markFirstRunComplete(
  userDataDir: string,
  accountKey: string,
  via: FirstRunVia,
  clock?: () => number, // defaults to Date.now
  fs?: FirstRunStoreFsSync,
): void;
```

On-disk shape:

```jsonc
{
  "version": 1,
  "accounts": {
    "<sha256(accountKey)>": { "completedAt": 1690000000000, "via": "skip" },
  },
}
```

### 3.2 IPC channels — `apps/desktop/main/services/first-run-channels.ts`

```ts
export const FIRST_RUN_CHANNELS = {
  /** Renderer → main: read `{ complete }` for the signed-in account. */
  get: "first-run.get",
  /** Renderer → main: mark first-run complete for the signed-in account. */
  complete: "first-run.complete",
} as const;
export type FirstRunChannelName =
  (typeof FIRST_RUN_CHANNELS)[keyof typeof FIRST_RUN_CHANNELS];
export const FIRST_RUN_CHANNEL_VALUES: ReadonlySet<string>;
export function isFirstRunChannel(name: string): name is FirstRunChannelName;
```

### 3.3 Zod schemas — `apps/desktop/main/services/first-run-schemas.ts`

```ts
import { z } from "zod";
export const FirstRunGetParamsSchema = z
  .object({ workspaceId: z.string().min(1).max(256) })
  .strict();
export const FirstRunViaSchema = z.enum(["skip", "setup", "first_run"]);
export const FirstRunCompleteParamsSchema = z
  .object({
    workspaceId: z.string().min(1).max(256),
    via: FirstRunViaSchema,
  })
  .strict();
export type FirstRunGetParams = z.infer<typeof FirstRunGetParamsSchema>;
export type FirstRunCompleteParams = z.infer<
  typeof FirstRunCompleteParamsSchema
>;
export type FirstRunGetResult = { readonly complete: boolean };
export type FirstRunCompleteResult = {
  readonly ok: true;
  readonly complete: true;
};
```

### 3.4 IPC handler bundle — `apps/desktop/main/ipc/handlers.ts`

```ts
export interface FirstRunHandlers {
  isComplete(workspaceId: string): Promise<{ complete: boolean }>;
  markComplete(
    workspaceId: string,
    via: "skip" | "setup" | "first_run",
  ): Promise<{ ok: true; complete: true }>;
}
// add to RegisterHandlersDeps:  readonly firstRun?: FirstRunHandlers;
```

### 3.5 AuthService accessor — `apps/desktop/main/auth/index.ts`

```ts
// New method on class AuthService (uses the private #loadSession → claims.sub).
async accountKey(workspaceId: string): Promise<string | null>;
```

And on the `ActiveAuthService` interface + `buildAuthService` closure in `apps/desktop/main/index.ts`:

```ts
accountKey(workspaceId: string): Promise<string | null>;
// closure:  accountKey: (workspaceId) => service.accountKey(workspaceId),
```

### 3.6 Shared port — `packages/chat-surface/src/ports/first-run-store.ts`

```ts
export type FirstRunVia = "skip" | "setup" | "first_run";
export interface FirstRunStore {
  /** Whether the current account has completed (or skipped) first-run. */
  isComplete(): Promise<boolean>;
  /** Persist completion for the current account. Idempotent. */
  markComplete(via: FirstRunVia): Promise<void>;
}
```

### 3.7 Shared gate — `packages/chat-surface/src/onboarding/FirstRunGate.tsx`

```ts
export interface FirstRunGateProps {
  readonly store: FirstRunStore;
  readonly children: ReactNode; // the shell to reveal once complete
}
export function FirstRunGate(props: FirstRunGateProps): ReactElement;
// internal phase: { kind: "loading" } | { kind: "first-run" } | { kind: "complete" }
// error reading the flag → fail open → { kind: "complete" } (never trap a returning user)
```

P0 placeholder body: a full-screen `.fr`-shell (token-mapped) with an H1 (`First, give it a model.` per SPEC copy, so the placeholder already reads as the FTUE) and a single _Skip → open the workspace →_ control wired to `store.markComplete("skip")` then reveal children. P1 replaces the placeholder with the real `FirstRunSurface`; the gate + Skip wiring stay.

### 3.8 Desktop binding — `apps/desktop/renderer/IpcFirstRunStore.ts`

```ts
export class IpcFirstRunStore implements FirstRunStore {
  constructor(bridge: WindowBridge, workspaceId: string);
  isComplete(): Promise<boolean>; // invoke FIRST_RUN_CHANNELS.get  → r.complete
  markComplete(via: FirstRunVia): Promise<void>; // invoke FIRST_RUN_CHANNELS.complete
}
```

(Host component — like `SignInGate`/`BootGate` it may use `window.bridge`/`CHANNELS` directly; the eslint global-ban is a `chat-surface`-only rule.)

### 3.9 Web binding — `apps/frontend/src/features/onboarding/KeyValueFirstRunStore.ts`

```ts
export class KeyValueFirstRunStore implements FirstRunStore {
  constructor(
    store: KeyValueStore,
    identity: { org_id: string; user_id: string },
  );
  // key = `enterprise.first-run.${org_id}.${user_id}.complete`
  isComplete(): Promise<boolean>; // store.get(key) === "1"
  markComplete(_via: FirstRunVia): Promise<void>; // store.set(key, "1")
}
```

---

## 4. Precise wiring steps

**A. `AuthService.accountKey` (`apps/desktop/main/auth/index.ts`).** Add the method beside `getSession` (~line 408). It calls the existing private `#loadSession(workspaceId)` (line 498) and returns `session?.claims.sub ?? null`. This reuses the in-memory cache the signed-in session already populated (`#toRenderer` at 514 proves `claims` is present). No new I/O.

**B. `ActiveAuthService` + closure (`apps/desktop/main/index.ts`).** Add `accountKey(workspaceId): Promise<string | null>` to the `ActiveAuthService` interface (line 580) and to the returned object in `buildAuthService` (after line 701) as `accountKey: (workspaceId) => service.accountKey(workspaceId)`.

**C. Register the IPC in `wireTransportAndIpc` (`apps/desktop/main/index.ts`).** `wireTransportAndIpc` already builds `authService` (line 423) and has `userDataDir` (line 459). In the `registerIpcHandlers({...})` call (line 479), add a `firstRun` bundle alongside `capability`/`connectors`:

```ts
firstRun: {
  isComplete: async (workspaceId) => {
    const key = await authService.accountKey(workspaceId);
    return { complete: key === null ? false : loadFirstRunComplete(userDataDir, key) };
  },
  markComplete: async (workspaceId, via) => {
    const key = await authService.accountKey(workspaceId);
    if (key !== null) markFirstRunComplete(userDataDir, key, via);
    return { ok: true, complete: true };
  },
},
```

Import `loadFirstRunComplete` + `markFirstRunComplete` at the top (join the existing `./services/*` import block ~lines 77–91). Rationale for registering here (not at `whenReady` beside `registerSecureStorageIpc()`, line 318): the per-account key requires `authService`, which only exists after `wireTransportAndIpc`. Routing through `registerIpcHandlers` also gets unified `parseOrThrow` validation + the existing `before-quit` teardown (line 543) for free.

**D. Handlers (`apps/desktop/main/ipc/handlers.ts`).** (1) Add the `FirstRunHandlers` interface + `firstRun?` to `RegisterHandlersDeps` (lines 122–130). (2) After the `connectors` block (ends line 404), add:

```ts
const firstRun = deps.firstRun;
if (firstRun) {
  ipcMain.handle(FIRST_RUN_CHANNELS.get, async (_e, raw) =>
    firstRun.isComplete(
      parseOrThrow(FIRST_RUN_CHANNELS.get, FirstRunGetParamsSchema, raw)
        .workspaceId,
    ),
  );
  ipcMain.handle(FIRST_RUN_CHANNELS.complete, async (_e, raw) => {
    const p = parseOrThrow(
      FIRST_RUN_CHANNELS.complete,
      FirstRunCompleteParamsSchema,
      raw,
    );
    return firstRun.markComplete(p.workspaceId, p.via);
  });
}
```

(3) In the teardown `channels` array (lines 407–436), push both channels under `if (firstRun) { … }`.

**E. Preload allowlist (`apps/desktop/preload/bridge.ts`).** Import `isFirstRunChannel` from `../main/services/first-run-channels` (join the imports at lines 9–11) and add `|| isFirstRunChannel(channel)` to `isBridgeChannel` (lines 17–24). No `statefulChannels` entry — these are request/response `invoke`, not pushed snapshots.

**F. Desktop seam (`apps/desktop/renderer/bootstrap.tsx`).** The seam is the `SignInGate` render prop at lines 94–104. Change:

```tsx
<SignInGate bridge={window.bridge} workspaceId={DEFAULT_WORKSPACE_ID}>
  {(session, signOut) => (
    <FirstRunGate
      store={new IpcFirstRunStore(window.bridge, session.workspaceId)}
    >
      <ChatShellForSession
        session={session}
        onSignOut={signOut}
        router={router}
        keyValueStore={keyValueStore}
        presenceSignal={presenceSignal}
      />
    </FirstRunGate>
  )}
</SignInGate>
```

Memoize the store per `session.workspaceId` (mirror the `IpcTransport` `useMemo` at line 123) so it isn't rebuilt each render. Import `FirstRunGate` from `@0x-copilot/chat-surface` (the barrel, line 15 block) and `IpcFirstRunStore` from `./IpcFirstRunStore`.

**G. Web seam (`apps/frontend/src/app/App.tsx`).** In `CopilotApp`, `identity` (`{ orgId, userId }` via `RequestIdentity`) and `keyValueStore` (line 518) are already in scope. Wrap the shell `body`. Minimal placement: inside the `ChatShell` return (line 1098), wrap `<Suspense>{body}</Suspense>` (line 1129) with `<FirstRunGate store={firstRunStore}>…</FirstRunGate>`, where `const [firstRunStore] = useState(() => new KeyValueFirstRunStore(keyValueStore, { org_id: identity.orgId, user_id: identity.userId }))`. Import `FirstRunGate` from `@0x-copilot/chat-surface` (join the block at lines 147–164) and `KeyValueFirstRunStore` from `../features/onboarding/KeyValueFirstRunStore`.

**H. Barrel (`packages/chat-surface/src/index.ts`).** Add a delimited block:

```ts
// === P0 (first-run onboarding) FTUE gate + store port ===
export {
  FirstRunGate,
  type FirstRunGateProps,
} from "./onboarding/FirstRunGate";
export { type FirstRunStore, type FirstRunVia } from "./ports/first-run-store";
// === end P0 ===
```

---

## 5. Parity notes (design classes → design-system tokens)

Per README §2 (rename, not re-theme) and SPEC.md §"CSS class inventory". The P0 placeholder uses only the semantic tokens confirmed present in `packages/design-system/src/styles.css`:

| Design (`copilot.css`) | design-system token (verified)                               | Used in the P0 placeholder for                          |
| ---------------------- | ------------------------------------------------------------ | ------------------------------------------------------- |
| `--ink`                | `--color-bg` (`#09090b`, line 150)                           | `.fr` full-screen background                            |
| `--panel`              | `--color-surface`                                            | card/skip control surface                               |
| `--tx` / `--tx2`       | `--color-text` (line 158) / `--color-text-strong` (line 180) | body / hero H1                                          |
| `--mut` / `--mut2`     | `--color-text-muted` (159) / `--color-text-subtle` (160)     | sub copy, footer mono line                              |
| `--line`               | `--color-border` (156)                                       | hairlines                                               |
| `--accent`             | `--color-accent` (`#5fb2ec`, 162)                            | the `0x` brand span + Skip affordance                   |
| `--accent-ink`         | `--color-accent-contrast` (`#08131d`, 164)                   | text/icon on any accent fill                            |
| `--jade`               | `--color-success` (169)                                      | reserved (wallet chip / ack land in P4/P3)              |
| `--disp` / `--mono`    | `--font-display` (40) / `--font-mono` (46)                   | hero (`600 23px/1.2`, `-0.015em`) / mono footer + notes |

Discipline: **one accent (sky) only**; the per-provider dot colors from SPEC.md §Data are P1 swatch data, not app accent — none appear in P0. The gate chrome mirrors the `.loginx-shell` full-screen pattern in `apps/desktop/renderer/signin.css` (centered pane, near-black bg) but expressed with the tokens above so both the desktop and web hosts render it identically. No hardcoded hex anywhere.

---

## 6. Test list

**Unit — main store** (`first-run-store.test.ts`, `// @vitest-environment node`, fake-fs helper cloned from `secure-storage-policy.test.ts`):

- default (missing file) → `loadFirstRunComplete === false`.
- `markFirstRunComplete` then `loadFirstRunComplete === true`; file written with `mode 0o600` and re-`chmod`'d.
- per-account isolation: account A complete does **not** mark account B complete (distinct hashed keys in one file).
- garbage / wrong-version JSON → treated as `false` (never throws).
- idempotent double-mark keeps a single account entry.

**Unit — IPC handlers** (extend `apps/desktop/main/ipc/handlers.test.ts`, fake `ipcMain`):

- `firstRun` omitted → neither channel registered (fail-closed like capability/connectors).
- `first-run.get` returns `{ complete }` from the injected bundle; rejects a payload missing `workspaceId` with `IpcValidationError`.
- `first-run.complete` forwards `{ workspaceId, via }`; rejects an invalid `via`; teardown removes both channels.

**Unit — AuthService** (extend `apps/desktop/main/auth/auth-service.test.ts`):

- `accountKey` returns `claims.sub` for a cached session; `null` when no session persisted.

**Unit — desktop binding** (`IpcFirstRunStore.test.ts`, fake bridge like `SignInGate.test.tsx`): `isComplete` maps `{complete:true}` → `true`; `markComplete("skip")` invokes `first-run.complete` with `{workspaceId, via:"skip"}`.

**Unit — web binding** (`KeyValueFirstRunStore.test.ts`, stub `KeyValueStore`): key is `enterprise.first-run.<org>.<user>.complete`; set/get round-trip; different users don't collide.

**Unit — shared gate** (`FirstRunGate.test.tsx`, fake `FirstRunStore`):

- store incomplete → renders the placeholder (`data-testid="first-run-gate"`), not children.
- store complete → renders children (the shell), no placeholder.
- clicking Skip calls `markComplete("skip")` then reveals children.
- `isComplete()` rejects → **fail open**: children render (returning user never trapped).
- loading phase renders neither children nor a crash before the promise resolves.

**Live-stack smoke** (per `docs/plan/verification/`, driven via `tools/cli-testing`): fresh `userData` → sign in → placeholder gate appears → click _Skip_ → lands in the Run cockpit → relaunch same account → gate is bypassed (flag persisted at `userData/settings/first-run.json`, `0600`).

Run commands: `npx vitest run --root packages/chat-surface`; `npm run typecheck --workspace @0x-copilot/chat-surface`; desktop main/renderer vitest for the `apps/desktop` suites; `npm run typecheck --workspace @0x-copilot/frontend`.

---

## 7. Acceptance criteria

1. A signed-in account that has never completed first-run sees the placeholder gate; an account that has is taken straight to `ChatShellForSession`/`CopilotApp`.
2. _Skip → open the workspace_ persists the flag and reveals the shell in the same session; a relaunch of that account bypasses the gate.
3. The flag is **per-account**: signing out of the device account and into a Google/wallet account (same install, same `workspaceId`) shows the gate again for the new account.
4. Desktop persistence is `userData/settings/first-run.json`, versioned and `chmod 0600`; raw account subjects are not stored (hashed keys only).
5. The per-user key is derived in main from the verified session (`claims.sub`) — never from a renderer-supplied identifier; `RendererSession` still exposes no `sub`.
6. `first-run.get`/`first-run.complete` are the only new IPC channels, allowlisted in preload, zod-validated in main, and torn down on quit.
7. Any failure reading the flag fails **open** to the shell (no user is ever trapped behind a broken flag file).
8. The placeholder uses design-system tokens exclusively (no hardcoded hex, sky-only accent); `packages/chat-surface` lint passes (no bare `window`/`fetch`/`localStorage`).
9. All new + touched unit suites and both typechecks pass; the live-stack skip-persists smoke passes.

---

## 8. Risks / edge-cases

- **Registration timing.** FirstRun IPC must be registered inside `wireTransportAndIpc` (where `authService` exists), not at `whenReady` beside `registerSecureStorageIpc()`. Because the gate mounts inside the `SignInGate` signed-in branch, the renderer never invokes the channels before `authService` is wired — but if a future refactor moves the gate above sign-in, `accountKey` would return `null` and the get handler would (correctly) report `complete:false`.
- **`accountKey` null on `markComplete`.** If the session is somehow unresolved when Skip fires, the mark is a silent no-op and the gate would reappear next launch. The signed-in-branch placement makes this unreachable in practice; documented so P1 doesn't move the mount without revisiting.
- **`sub` stability.** Keying by `claims.sub` assumes it is stable per account across restarts/reinstalls. README confirms the device account is a server-side singleton (D4-A) and Google/wallet subs are account-stable. If a provider ever rotated `sub`, a user would re-see the FTUE once (annoying, not harmful).
- **Concurrency on the JSON file.** Desktop is single-instance (`installSingleInstance`, `index.ts:142`) and writes are sync read-modify-write, so no cross-process race. Do not make the store async without adding a write lock.
- **`workspaceId` is effectively constant** (`DEFAULT_WORKSPACE_ID`/`org_acme`) — this is _why_ the per-account `sub` key is mandatory; keying by `workspaceId` alone would collapse all local accounts into one flag. Called out so nobody "simplifies" the key back to `workspaceId`.
- **Web namespace churn.** `KeyValueFirstRunStore` keys on `org_id`+`user_id`; clearing browser storage re-shows the FTUE (acceptable — web has no keychain-equivalent). Not shared cross-device (localStorage), consistent with README §3.2.
- **StrictMode double-invoke.** The gate's `isComplete()` effect runs twice under React StrictMode (dev); it must be idempotent and guard against setting state after unmount (mirror the `cancelled` flag in `SignInGate`/`BootGate` effects).
- **Fail-open vs. re-show.** Choosing fail-open (error → shell) trades "a first-run user with a transient error might skip the FTUE" for "a returning user is never blocked." Given P0 is infra and the FTUE is non-critical, fail-open is the right default; if P5+ ever gates paid features on FTUE completion, revisit.
- **Placeholder must not imply shipped scope.** The P0 body is a deliberate stub; keep its copy minimal and clearly a placeholder so a screenshot review doesn't read it as the finished gate. Do not add the trial hatch or model rows even as placeholders (scope lock).

---

## Open questions

- Fail-open confirmation: on an error reading the first-run flag, P0 renders the shell (never trap a returning user). If any later phase gates value on FTUE completion, this default should be revisited — is fail-open acceptable as the standing behavior?
- Account-key hashing: the PRD hashes claims.sub (sha256) before writing it as a JSON map key so wallet addresses / Google subs never sit in the 0600 plaintext file. Confirm hashing is desired vs. storing the raw sub (raw is simpler and the file is already 0600).
- Gate placement as a shared chat-surface component vs. host-local: the PRD puts FirstRunGate in packages/chat-surface (SSOT, so P1 fills the body in place) rather than duplicating a desktop-only gate like SignInGate/BootGate. Confirm that's the intended home for the seam (vs. keeping the desktop gate host-local and only sharing the FirstRunSurface body in P1).
- Web P0 depth: the PRD wires a real KeyValueStore-namespaced web binding + gate now. Is a working web Skip path wanted in P0, or should web be a thin stub until the P1 surface exists (desktop-first)?
- Should 'finishing setup' and 'sending the first run' also set the flag in P0, or is Skip-only correct for P0 with via: 'setup'|'first_run' reserved for P1/P3? The PRD assumes Skip-only.

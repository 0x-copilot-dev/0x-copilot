# Phase S — Substrate Decision

Status: **decided · custom Electron** · 2026-05-17 · orchestrator-authored

Source data: branches `desktop/phase-S-spike-vscode` (S1-A) and `desktop/phase-S-spike-electron` (S1-B); shared spike-prep merged to `main` as `1c7fb30`. Both variants mount the **same** `EmailRenderer` from `packages/surface-renderers` driven by `MockTransport` from `packages/chat-transport`.

## Verdict

**Custom Electron.** Both substrates can host the renderer with comparable substrate cost. Electron wins on the three principles that mattered for the original substrate question (single source of truth, simple & elegant, forward-fit for tier-2 sandboxing), and the substrate cost is not large enough on either side to override.

## Side-by-side metrics

|                                  | S1-A VS Code marketplace extension | S1-B custom Electron          |
| -------------------------------- | ---------------------------------- | ----------------------------- |
| Substrate LOC (excl. tests)      | 861                                | 909                           |
| Substrate LOC (incl. tests)      | 1,433                              | 1,412                         |
| Renderer bundle (dev / minified) | 2.4 MB / 765 KB                    | 2.13 MB / (no min run)        |
| Build wall-clock (cold)          | ~270 ms                            | ~840 ms                       |
| Typecheck / lint / tests         | pass · 29 tests                    | pass · 23 tests               |
| Compile targets                  | 2 (host CJS + webview ESM)         | 2 (main CJS + renderer ESM)   |
| IPC seam LOC (RPC + schemas)     | ~538 (~62% of substrate code)      | ~390 (~43% of substrate code) |
| Renderer code touched            | none                               | none                          |

LOC and bundle sizes are within ~10% of each other; build time differs by ~3× but both are sub-second and dominated by esbuild overhead, not the substrate. The IPC seam fraction is interesting — VS Code's postMessage RPC needs explicit id-correlated promises + Zod on both ends; Electron's `ipcRenderer.invoke` / `ipcMain.handle` collapses about a third of that.

## Visual verification

Manual interactive verification by the human reviewer (substrate-comparison screenshots):

- **S1-A**: renders the full expected UX — TO / CC / SUBJECT populated, body streamed in chunk-by-chunk, "Drafting…" pill, floating "STREAMING / Approve & send" approval card.
- **S1-B (as-shipped)**: renders only the empty PENDING block with the provenance pill. Root cause: the S1-B bootstrap wraps the renderer in React's `<StrictMode>`. The spike-prep `EmailRenderer` uses a `useRef`-based `hasMounted` guard against effect double-invoke; combined with the async IPC unsubscribe path, the guard interacts badly — setup A subscribes, cleanup A removes the renderer-side record synchronously, async unsubscribe is in flight, setup B early-returns. Only the late `pending_diff_appeared` event leaks through during the unsubscribe round-trip. **This is a wrapper bug in S1-B, not a substrate limitation.** VS Code's bootstrap doesn't wrap in StrictMode and so doesn't hit it. The fix is one line (drop the StrictMode wrapper, or rework the renderer's StrictMode guard — the latter is a Phase 4 renderer concern).

So substrate-neutral: both can render the full UX. S1-A demonstrated it; S1-B is one-line away from doing the same. The bug pattern is logged for the Phase 4 renderer cleanup (the `hasMounted` ref is fragile).

## Substrate friction notes (the actual deliverable for the decision)

### VS Code marketplace extension (S1-A)

1. `CustomEditorProvider` is shaped around documents, not URIs. A no-op `EmailDocument` was needed even though there's no on-disk artifact. Electron has no equivalent indirection — a `BrowserWindow` loads a URL directly.
2. Activation-events footgun: the prompt's `onUri:email` hint was wrong; VS Code 1.74+ auto-derives activation from `contributes` declarations and explicit `activationEvents` for contributed surfaces produces diagnostics warning against listing them. The agent caught this; cost was reasoning time, not shipped bug.
3. Two compile targets force two ESLint contexts (extension host = Node CJS; webview = Chromium ESM). Composite tsconfigs broke as soon as a shared file (`rpc-schemas.ts`) needed both projects; single tsconfig + esbuild bundling is the workable shape.
4. Webview ↔ host RPC requires explicit Zod validation on both ends because postMessage is structured-clone over an untrusted boundary. This is the bulk of the substrate LOC.
5. Dev-host always shows "All installed extensions are temporarily disabled / Reload and Enable Extensions" banner. Cosmetic but present in every screenshot.
6. `vsce` was renamed to `@vscode/vsce`; the old package install still works but throws a deprecation notice on every install.

### Custom Electron (S1-B)

1. `ELECTRON_RUN_AS_NODE=1` in CI / agent harnesses silently makes Electron behave as plain Node and `require('electron')` returns a path string. The dev script has to unset it explicitly. Documented in the agent's README.
2. `webRequest.onHeadersReceived` does **not** intercept `file://` loads, so injecting CSP via that path is unavailable. The agent registered a custom `app://` privileged protocol (~110 LOC in `main/app-protocol.ts`) to serve renderer assets with a real origin + per-response CSP header. This is the most substantive substrate-shaped addition; works cleanly but is non-obvious if you haven't done it before.
3. `SseSubscription` is synchronous in the on-disk `Transport` interface but IPC subscribe is async. Resolved by generating the `subscriptionId` in the renderer (`crypto.randomUUID()`) and firing the IPC in the background — subscribe-time failures arrive on the stream channel as `kind: "error"`. Architecturally interesting; doesn't add LOC but adds a small amount of reasoning cost.
4. `getSession()` and `capabilities()` are synchronous accessors on `Transport`; IPC can't satisfy that without awaiting. Resolved by taking a `bootstrapSession` and `bootstrapCapabilities` at construction time. Production fetches these once at sign-in and caches.
5. `AbortSignal` doesn't structured-clone across IPC. Dropped on the renderer side with a comment; production needs a token-based cancellation side channel. Flagged for Phase 5.

Roughly comparable friction footprint, different shape. VS Code's friction is workbench-API quirks; Electron's friction is "you have to build the absolute basics yourself" (custom protocol for CSP, sync/async impedance, etc.).

## Why Electron wins on principles, given the data

**DRY** — wash. Both consume the renderer unchanged, both need an IPC seam.

**Substitution** — slight Electron edge. Two mount layers (`BrowserWindow` renderer → `chat-surface` → renderer) vs three for VS Code (`CustomEditor` → webview → bundled chat-surface → renderer). Fewer hops, fewer contracts to version.

**Simple & elegant** — Electron edge. Comparable substrate LOC, but Electron's surface area is "everything I see is something I built or chose"; VS Code adds an entire workbench worth of UI competing for screen real estate (activity bar, sidebar, status bar, command palette, the disabled-extensions banner in dev). The Atlas design is chat-first; the workbench chrome is unwanted scenery in every screenshot.

**Single source of truth** — Electron decisive. VS Code's marketplace-extension form introduces a _second_ URI authority (the workbench's `vscode.Uri` handling), a _second_ command registry (`vscode.commands`), and a _second_ keybinding system on top of `chat-surface`'s. Any feature that touches navigation has to be reasoned about across both. Electron has only what we build.

## Forward-fit for tier-2 (PRD §9.5)

The Phase 6 tier-2 codegen pipeline loads agent-generated adapters at runtime via dynamic `import()` from `{userData}/adapters/{scheme}-v{n}.js`. The sandbox model relies on Node's `vm` module + AST allowlist + render-with-timeout.

- **Electron**: native fit. Main process owns the `vm` sandbox; renderer mounts the resulting React component. The dynamic-load shape is Electron's normal model.
- **VS Code**: not natively supported. Extensions install as `.vsix` packages, not as runtime dynamic modules. Tier-2 in VS Code would require either packaging each agent-generated adapter as its own extension (huge friction; install/uninstall flow per scheme), or carving a runtime loader inside our extension's webview (off the supported path; CSP and webview lifecycle would fight us).

This factor on its own probably justifies Electron given tier-2 is in the 1.0 product (D27 / Phase 6).

## What "rejected" S1-A leaves on the table

What we lose by picking Electron:

- Free Cmd+P quick-open (we already plan to hand-roll Cmd+K palette per PRD §3.2 — ~150 LOC, exists in the prototype design)
- Familiar keyboard shortcuts for VS Code users (but the design's intended user is not a developer — see [project-atlas-product-model](../../../.claude/projects/-Users-parthpahwa-Documents-work-enterprise-search/memory/project_atlas_product_model.md))
- Theming infrastructure (we ship a single Atlas dark theme; not a cost we'd otherwise pay)

What we gain:

- Single source of truth for routing/keybindings/lifecycle
- A clean `BrowserWindow` for the chat-first Atlas design (no workbench chrome)
- Natural fit for tier-2's dynamic-adapter loading
- Standard signing/notarization/update path via electron-builder + electron-updater

## Decisions confirmed / overturned

- **D1** (custom Electron app) — **CONFIRMED**.
- **D7** (per-SaaS renderer policy — first-party + agent-generated + generic fallback) — confirmed; the spike validated that the same renderer mounts in either substrate, which is what the contract requires.
- **D8** (URI scheme as artifact identity) — confirmed; both variants used `email://draft-1` cleanly.
- **D11** (electron-updater on stable) — confirmed.
- **D13** (inline annotation diff UX, not Monaco) — confirmed; both variants rendered the inline-annotation pattern.
- **D14** (renderer-owned snapshot for swimlane scrub) — not exercised in this spike; carries forward to Phase 4.

## Next steps

1. **Delete the S1-A worktree + branch**, retain history in git. The losing variant's `apps/vscode-spike/` directory does NOT get merged to main.
2. **Merge S1-B's branch** (`desktop/phase-S-spike-electron`) to main via `desktop/phase-S` integration branch, BUT first decide whether the substrate work itself ships or whether Phase 1's `electron-shell` agent rebuilds it from scratch given the Phase 4 contract changes. **Recommended: delete S1-B's `apps/electron-spike/` too** and have Phase 1 build `apps/desktop/` per the PRD's directory layout. The spike's purpose was the decision, not the production code. Spike commits are referenced in history.
3. **PRD updates**: D1 et al. are no longer "pending Phase S" — strike the deferral note in §0 TL;DR and §4. Mark D1's rationale with a pointer to this report.
4. **desktop-app.md**: update the status header from "proposal — substrate pending Phase S spike" to "proposal — substrate decided; see decision report"; add §3 reference to this file.
5. **Phase 0 can launch** as soon as the user gives the go-ahead. Foundation agent rewrites desktop-app.md per the decision, deletes desktop-app-rollout.md, finalizes ports, ESLint rule, and scaffolds an empty `apps/desktop/` ready for Phase 1.

## What the spike did NOT validate

- The IPC race in S1-B's renderer-wrapper interaction with StrictMode. Phase 4 should revisit the `hasMounted` guard in `EmailRenderer` and likely all tier-1 renderers — the guard is fragile under async cleanup.
- Visual fidelity vs the production Atlas design at the chat-shell level (the spike only exercised the EmailRenderer pane, not the surrounding `ChatShell` / sidebar / swimlane).
- Auth / token storage / multi-workspace gating (Phase 5).
- Tier-2 actually doing its job (Phase 6).
- Bundle minification + production-mode build for Electron (S1-B reported only dev bundle size).
- Cold launch time + idle memory for either variant (not measured; orchestrator did not run interactive launches itself).

These are all out of scope for the substrate decision and properly belong to their respective downstream phases.

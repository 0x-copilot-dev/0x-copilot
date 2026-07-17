# Phase 0.C: desktop-scaffold

## Vision

`apps/desktop/` is the only deployable that will hold custom Electron substrate code (PRD §3.1, §3.2). Phase 1 has four parallel agents (1A electron-shell, 1B chat-shell-layout, 1C ipc-transport, 1D routing-palette) that need a workspace they can `npm install` into and start importing `@0x-copilot/*` packages on day one. The job of this scaffold is to remove every bit of workspace-plumbing friction from Phase 1's critical path — directory present, npm workspaces picking it up, tsconfig extending the root, lint runner wired, dependencies declared with exact pins matching the rest of the monorepo — without committing a single line of main/preload/renderer code that would force Phase 1 to refactor someone else's stub.

Treat this scaffold like a typed empty interface: the shape is load-bearing (PRD §3.2 directory layout, §6.2 module conventions, §6.3 strict TS), the body is deliberately absent.

## Status

- Status: in-progress
- Agent slug: desktop-scaffold
- Branch: desktop/phase-0-desktop-scaffold
- Worktree: .claude/worktrees/agent-a3c6efde7dc8c8df6
- Created: 2026-05-17

## Scope

**In scope** (files this agent owns):

- `apps/desktop/package.json` (NEW)
- `apps/desktop/tsconfig.json` (NEW)
- `apps/desktop/eslint.config.mjs` (NEW — placeholder, no boundary rules yet)
- `apps/desktop/.gitignore` (NEW)
- `apps/desktop/README.md` (NEW — short pointer to PRD)
- `docs/plan/desktop/phase-0/0C-desktop-scaffold.md` (this file)

**Out of scope** (do NOT touch):

- Anything under `packages/**` — owned by other Phase 0 agents.
- Any source files inside `apps/desktop/{main,preload,renderer,build}/**` — Phase 1.
- `electron-builder.yml`, dev script, signing config — Phase 1 / Phase 8.
- `docs/architecture/desktop-app-rollout.md` — Phase 0-D's deletion target.
- Root `package.json` (workspaces glob `apps/*` already picks up the new package).
- Root `tsconfig.base.json` — verified: no explicit `paths` for `@0x-copilot/*`; npm workspaces handles module resolution. No edit needed.

## Functional requirements

- [x] FR-1: `apps/desktop/package.json` declares `@0x-copilot/desktop` as a private npm workspace member; lists workspace `dependencies` on `chat-surface`, `chat-transport`, `surface-renderers`; lists `react`, `react-dom`, `zod` as runtime deps; lists `electron`, `electron-builder`, `esbuild`, `typescript`, `vitest`, `@types/node`, eslint toolchain as devDependencies — versions pinned exactly per sibling workspaces.
- [x] FR-2: `apps/desktop/tsconfig.json` extends `tsconfig.base.json`, sets `outDir` to `./out` to match `"main": "./out/main/index.js"` from package.json, sets `jsx: react-jsx` (already in base — kept explicit for grep-ability), and declares the Phase 1 directory layout in `include` so Phase 1 files compile immediately when authored. Carries `"files": []` alongside `include` so `tsc --noEmit` does not emit TS18003 (`no inputs were found`) until Phase 1 lands its first source file — once a file matching the `include` glob exists, the union of `files` and `include` resolves to that file and typecheck still runs.
- [x] FR-3: `apps/desktop/eslint.config.mjs` exists and is a runnable placeholder that wires `@typescript-eslint/parser` + browser/node globals against the Phase 1 directory layout. The main/preload/renderer boundary rules (banning `Transport`/`fetch` in renderer-only files, etc.) are deferred to Phase 1, which knows the real file layout. (Filename uses `.mjs` rather than `.js` because the package deliberately does not declare `"type": "module"` — see Open question 2.)
- [x] FR-4: `npm install` at the repo root succeeds and registers `@0x-copilot/desktop` in `package-lock.json`.
- [x] FR-5: `npm run typecheck --workspace @0x-copilot/desktop` exits 0 (no TS files to check is a valid pass).
- [x] FR-6: `npm run typecheck` at the repo root (all workspaces) still exits 0.

## Non-functional requirements

- No source code in `main/`, `preload/`, `renderer/`, `build/` — Phase 1 owns those directories. The `include` pattern in tsconfig.json is _ready_ for them, the files are _absent_.
- No comments inside `package.json` (per PRD §6.1 + the fact that npm parses package.json as strict JSON, not JSONC).
- README is short (~6 lines) and points to PRD §5 Phase 1 rather than narrating intent.
- Exact-version pins for every dependency. No `^` / `~`. Justification per dep below.

## Interfaces consumed

None. Phase 0 scaffold has zero source code.

The package's `dependencies` declare _intent_ to consume the following at the Phase 1 entry point:

- `Transport` from `@0x-copilot/chat-transport` — renderer-side IPC transport will implement it in Phase 1C.
- `ChatShell` (and the rest of the shell surface) from `@0x-copilot/chat-surface` — Phase 1B / 1A wire it through `renderer/bootstrap.tsx`.
- `SurfaceRegistry` + `registerAll()` from `@0x-copilot/surface-renderers` — Phase 4 / Phase 1A bootstrap call.

## Interfaces produced

None. The scaffold publishes no exported symbols. The package's `main` field points to `./out/main/index.js`, which **does not exist yet** — Phase 1A creates it. Until then, `npm run build` and `npm run dev` are no-op placeholders that exit 0 with an explanatory message (chosen over `exit 1` so that `npm run build --workspaces` from the repo root does not fail before Phase 1 lands).

## Dependency pins & rationale

Repo standard (PRD §6 and ad-hoc audit of sibling `packages/*/package.json`) is exact-version pins on everything, no carets/tildes. The orchestrator's spec showed `^` ranges in the example block; the standard wins per PRD §6 "pin versions exactly … unless they're already that way in a sibling workspace". No sibling uses `^` on dev tooling.

**Workspace deps** (match the version declared in the sibling workspace's `package.json`):

- `@0x-copilot/chat-surface` — `0.1.0`
- `@0x-copilot/chat-transport` — `0.1.0`
- `@0x-copilot/surface-renderers` — `0.1.0`

**Runtime deps:**

- `react` — `19.2.6` (matches `apps/frontend`, `packages/chat-surface` devDep pin, `packages/surface-renderers` devDep pin; the only `^19.0.0` usage in repo is in `peerDependencies`).
- `react-dom` — `19.2.6` (same reasoning).
- `zod` — `3.25.76` (latest 3.x as of 2026-05-17). No sibling uses `zod` yet; chose 3.x over 4.x because the IPC RPC protocol Phase 1C will define lives in shared ground with the Python `ai-backend` runtime contracts, where the established convention is Pydantic v2 — staying on Zod 3 keeps the JSON shape stable and well-known. Re-evaluate in Phase 5+ if `zod@4` lands a feature the IPC schemas need.

**Dev tooling** (match siblings exactly so a single `npm install` resolves a consistent dependency tree):

- `typescript` — `6.0.3`
- `vitest` — `4.1.6`
- `@types/node` — `25.8.0` (matches `apps/frontend`)
- `@typescript-eslint/parser` — `8.59.3`
- `eslint` — `9.39.4`
- `globals` — `15.15.0`

**Electron-specific tooling** (no sibling pin to match — pin to latest published stable as of 2026-05-17 and document):

- `electron` — `42.1.0`. Electron does not publish an LTS branch — the official support policy is "latest three stable majors" (see `electronjs.org/docs/latest/tutorial/electron-timelines`). 42.x is the current stable line; 43.x is still alpha. Phase 8 (`signing-mac`, `signing-windows`, `updater`) will re-evaluate the major against the Mac notarization / Windows code-signing toolchain compatibility — bumping a major in Phase 8 is acceptable; pinning to a now-EOL major would be the failure mode.
- `electron-builder` — `26.8.1` (latest stable). Used by Phase 8 for signing + DMG/MSI/AppImage packaging.
- `esbuild` — `0.28.0` (latest stable). Used by Phase 1A to bundle `main/`, `preload/`, and (optionally) `renderer/` — esbuild is the Electron-spike-validated bundler (Phase S report §S2 §S1-B).

## Open questions

1. **Should `peerDependencies` mirror `apps/frontend`'s pattern (deps only) or `packages/chat-surface`'s pattern (`react` + `react-dom` as peer + dev)?** _Answer:_ apps are leaves in the dependency graph — they own the React copy, not a peer. Followed `apps/frontend` and put `react` / `react-dom` in `dependencies`. Phase 1 can revisit if the renderer build needs a peer-deduped React (it won't — a single Electron app has exactly one renderer + one React tree).
2. **`type: "module"` or not?** Sibling workspaces all set `"type": "module"`. Electron's main process historically wants CommonJS (`require('electron')`), and Electron's ESM support landed in v28 but still has quirks with the main entrypoint resolution and native modules. _Answer:_ omitted `"type": "module"` from `apps/desktop/package.json` so Phase 1A can pick the module system per file (main = CJS via esbuild bundle, renderer = ESM via esbuild bundle, preload = CJS for `contextBridge` reliability). Phase 1A should decide and document in `0C → 1A` handoff. This is the most consequential implicit decision in the scaffold; flagging it for orchestrator review.
3. **Zod 3 vs Zod 4?** Picked 3.x for IPC-schema lingua franca with `ai-backend` (Pydantic v2 has well-tested round-trip with zod@3 via `zod-to-json-schema` and friends). Phase 1C should validate the choice when implementing `rpc-protocol.ts` and bump if necessary.
4. **`@types/electron`?** Not added — modern Electron (≥10) ships TypeScript declarations in its own package. Phase 1A should not need a separate `@types/electron`. Flagging in case Phase 1A discovers otherwise.

## Done criteria

- [x] All FRs met
- [x] `npm install` at repo root succeeds and resolves `@0x-copilot/desktop`
- [x] `npm run typecheck --workspace @0x-copilot/desktop` exits 0
- [x] `npm run typecheck` at repo root exits 0 (all workspaces still pass)
- [x] No files created outside the scope list above
- [x] No comments in `package.json`
- [x] Every dependency carries an exact-version pin; non-obvious pins (electron, electron-builder, esbuild, zod) explained in this sub-PRD

## Notes for orchestrator review

- Open question (2) — `"type": "module"` deferral — is the only non-trivial implicit decision. Phase 1A's PRD should call out which module system it picks per process role (main / preload / renderer) and update `package.json` if it adds `"type": "module"`.
- `apps/desktop/eslint.config.mjs` is intentionally a thin parser-globals placeholder. The renderer/main/preload boundary lint rules (no `fetch`/`Transport` in main, no `node:*` in renderer, etc.) belong to Phase 1A because they require knowing the actual file layout. If a 0-A foundation agent's chat-surface ESLint rule lands first and a reusable boundary helper emerges, Phase 1A may import from it rather than re-define.
- Root `tsconfig.base.json` was inspected: no `paths` block, so adding a `@0x-copilot/desktop` path mapping is not needed. npm workspaces handles module resolution and TS resolves through `node_modules` symlinks. If a later phase adds explicit `paths` for any workspace, `apps/desktop` should be added at that point.
- Root `package.json` `workspaces` array is `["apps/*", "packages/*"]` — no edit needed; the new package is picked up automatically.

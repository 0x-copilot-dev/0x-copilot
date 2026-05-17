# @enterprise-search/desktop

Scaffold for the Atlas Electron desktop client. Implementation lands in Phase 1 — see [docs/plan/desktop/PRD.md](../../docs/plan/desktop/PRD.md) §5 Phase 1.

## Why this is empty

Phase 0 scaffolds the workspace member so Phase 1 can `npm install` and start coding immediately. Main / preload / renderer code, electron-builder config, signing, and the dev script all belong to Phase 1.

## Module system (read before adding source)

`package.json` deliberately **omits** `"type": "module"`. Phase 1A picks per-process:

- **main** and **preload** compile to **CommonJS** — Node-shaped, `__dirname` available, Electron's main-process loader resolves `@enterprise-search/*` workspace deps without an ESM/CJS interop dance.
- **renderer** compiles to **ESM** via an esbuild bundle — browser-shaped, React 19 + chat-surface + chat-transport + surface-renderers bundled into one `out/renderer/bootstrap.js`.

Two tsconfigs split the targets (`tsconfig.main.json` and `tsconfig.renderer.json`); `tsconfig.json` is the typecheck-only umbrella. This was validated in the Phase S spike — see [docs/plan/desktop/phase-0.5/S2-decision.md](../../docs/plan/desktop/phase-0.5/S2-decision.md).

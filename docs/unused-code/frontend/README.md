# Unused code and smell audit — frontend (`apps/frontend`)

This directory holds **cluster-scoped** notes from a pass over `apps/frontend/src`. It is **documentation**, not an automated gate: findings mix static signals (`ts-prune`, **knip**), test-only usage, and manual import tracing from app entrypoints.

**Broader inventory:** the first pass lived mainly in the numbered cluster files. A **second pass** with [knip](https://github.com/webpro-nl/knip) surfaces many more items — especially **over-exported** symbols (still used _inside_ the file, but not imported elsewhere) and **orphan source files**. See [11-knip-unused-inventory.md](./11-knip-unused-inventory.md) for the full breakdown and how to interpret counts.

## Audit metadata

| Field         | Value                                                        |
| ------------- | ------------------------------------------------------------ |
| Last reviewed | 2026-05-06                                                   |
| Git revision  | `a78bfc08f5502ab600b36eed22176aaac005acd4`                   |
| Scope         | `apps/frontend/src` (excluding `node_modules`, build output) |

**Cleanup pass:** orphan files and dead exports flagged by knip §1, §2, §4b in [11-knip-unused-inventory.md](./11-knip-unused-inventory.md) were deleted at this revision. Cluster docs referencing them are marked _**RESOLVED**_ inline.

## Cluster index

| Doc                                                                                  | Scope                                                                |
| ------------------------------------------------------------------------------------ | -------------------------------------------------------------------- |
| [01-app-shell-and-utils.md](./01-app-shell-and-utils.md)                             | `src/app/`, `src/utils/`                                             |
| [02-api-layer.md](./02-api-layer.md)                                                 | `src/api/`                                                           |
| [03-auth.md](./03-auth.md)                                                           | `src/features/auth/`                                                 |
| [04-connectors.md](./04-connectors.md)                                               | `src/features/connectors/`                                           |
| [05-settings-and-workspace.md](./05-settings-and-workspace.md)                       | `src/features/settings/`, `src/features/workspace/`                  |
| [06-chat-chatmodel-and-events.md](./06-chat-chatmodel-and-events.md)                 | `src/features/chat/chatModel/`, run state, approval, MCP auth action |
| [07-chat-ui-components.md](./07-chat-ui-components.md)                               | `src/features/chat/components/`                                      |
| [08-chat-hooks-prompts-utils-markdown.md](./08-chat-hooks-prompts-utils-markdown.md) | chat hooks, prompts, utils, markdown                                 |
| [09-share-skills-me.md](./09-share-skills-me.md)                                     | `share/`, `skills/`, `me/`                                           |
| [10-observability.md](./10-observability.md)                                         | `src/observability/`                                                 |
| [11-knip-unused-inventory.md](./11-knip-unused-inventory.md)                         | **Repo-wide** unused files, exports, and dependency hygiene (knip)   |

Vitest tests under `src/**/*.test.{ts,tsx}` were treated as **consumers** for symbols that only appear in tests.

## Methodology

### 1. TypeScript (`tsc`)

```bash
npm run typecheck --workspace @0x-copilot/frontend
```

A clean typecheck does not prove absence of dead code; it only rules out obvious broken references.

### 2. ts-prune (unused exports)

From the repo root:

```bash
cd apps/frontend && npx ts-prune
```

**Interpretation:**

- **`(used in module)`** — export exists for API surface / typing; only referenced inside the same file. Often intentional (React props types, discriminated unions).
- **Barrel files (`index.ts`)** — `ts-prune` frequently lists **every** re-export from `components/shell/index.ts` as unused even when other modules import `{ Topbar } from "./components/shell"`. Treat those rows as **noise** unless no import path reaches the barrel (verify with ripgrep).
- **Tests** — symbols imported only from `*.test.*` are **test-only wiring**, not production dead code (still a smell if duplicating production hooks).

### 3. Vitest coverage

```bash
npm run test --workspace @0x-copilot/frontend
```

Low or missing coverage means “weakly exercised”, not “unused in production”. Prefer explicit unused-export analysis over coverage alone.

### 4. Knip (unused files, exports, dependencies)

```bash
cd apps/frontend && npx knip
```

Typical output at this revision:

| Category                    | Approx. scale | Meaning                                                                                                               |
| --------------------------- | ------------- | --------------------------------------------------------------------------------------------------------------------- |
| **Unused files**            | 3             | No importer chain from configured entrypoints — strongest deletion signal.                                            |
| **Unused exports (values)** | 46            | No _other_ module imports that symbol — includes many **internal helpers** that should lose `export`, not delete.     |
| **Unused exported types**   | 85            | Exported `Props` / hook result types never imported by name — usually style / library hygiene, not runtime dead code. |
| **Unused dependencies**     | 1             | Package in `package.json` with no import in `src/`.                                                                   |
| **Unlisted dependencies**   | 7             | Imports that resolve without a direct `package.json` entry (often transitive).                                        |

Full tables and triage notes: [11-knip-unused-inventory.md](./11-knip-unused-inventory.md).

### 5. Manual tracing

Entrypoints used as roots:

- [`src/main.tsx`](../../../apps/frontend/src/main.tsx) — Vite bootstrap.
- [`src/app/App.tsx`](../../../apps/frontend/src/app/App.tsx) — auth gate, chat vs settings routing.

Classify candidates as **production-used**, **test-only**, **legacy / superseded**, or **candidate dead code**.

## Regenerating

Re-run `ts-prune`, **`npx knip`** from `apps/frontend`, and spot-check imports after large refactors; update **Audit metadata** and each cluster file’s “Last reviewed” line when refreshing findings.

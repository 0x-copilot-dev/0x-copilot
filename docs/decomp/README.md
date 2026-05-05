# AI-Backend Decomposition Docs

Raw, descriptive inventory of the bespoke logic in `services/ai-backend`. Strictly per-file (or per sub-cluster bundle for smaller files); no proposed code changes — those live in [/docs/refactor/](../refactor/) once seams are agreed.

## Why this exists

`services/ai-backend` has grown to ~27k LOC of custom domain code. Several files are now ≥ 800 LOC ([postgres/runtime_api_store.py](../../services/ai-backend/src/runtime_adapters/postgres/runtime_api_store.py) is 2,344; [handlers/run.py](../../services/ai-backend/src/runtime_worker/handlers/run.py) is 997). Before the next refactor wave — and before deciding whether parts of this can be replaced by off-the-shelf libraries — we need a structured inventory of what each file does, so refactor seams are obvious and replacement candidates can be evaluated against concrete feature parity tables.

## Cluster matrix

| Cluster                               |        LOC |  Files | Index                                                             |
| ------------------------------------- | ---------: | -----: | ----------------------------------------------------------------- |
| `agent_runtime/capabilities/`         |      5,893 |     25 | [capabilities/\_index.md](capabilities/_index.md)                 |
| `runtime_worker/`                     |      5,601 |     17 | [runtime-worker/\_index.md](runtime-worker/_index.md)             |
| `runtime_adapters/`                   |      4,572 |      6 | [runtime-adapters/\_index.md](runtime-adapters/_index.md)         |
| `agent_runtime/api/`                  |      3,617 |      8 | [agent-api/\_index.md](agent-api/_index.md)                       |
| `agent_runtime/execution/`            |      2,018 |      7 | [execution/\_index.md](execution/_index.md)                       |
| `agent_runtime/context/memory/`       |      1,845 |      7 | [context-memory/\_index.md](context-memory/_index.md)             |
| `agent_runtime/persistence/`          |      1,726 |     17 | [persistence/\_index.md](persistence/_index.md)                   |
| `agent_runtime/delegation/subagents/` |      1,504 |      5 | [delegation-subagents/\_index.md](delegation-subagents/_index.md) |
| **Total**                             | **26,776** | **92** |                                                                   |

Out of scope: `observability/`, `pricing/`, top-level `mcp/` / `skills/` / `tools/` (superseded by `capabilities/`), `runtime_api/` (mostly framework glue), `prompts/`, `deployment/`.

## How to read these docs

### The A–G template

Every standalone file doc and every bundled-file section uses this exact structure:

- **A. Top-level structure** — every class + module-level function with line range and one-sentence purpose; module-level constants, regex patterns, `_Fields`-style pools, singletons.
- **B. Feature inventory** — group classes/functions by domain; for each domain, what it does + which symbols belong + rough LOC.
- **C. Functional spec per domain** — inputs, outputs, side effects, state machines, validation rules, tenant-isolation guards, error types and conditions.
- **D. Bugs / edge cases / invariants** — hazard-fix comments, concurrency guards, defensive validation, failure-mode docstrings.
- **E. Hardcoded vs configurable** — literal regex/SQL/magic numbers vs `os.environ` / settings.
- **F. External dependencies and coupling** — internal cross-module imports + library deps.
- **G. Suggested decomposition seams** — natural file boundaries; existing abstractions that hint at the seam.

Imports, dunder methods, and trivial getters are skipped throughout.

### Granularity rules

- **L/XL files (≥ 400 LOC)** get standalone docs.
- **M files (100–400 LOC)** are bundled per sub-cluster (one bundled doc per bundle, each section labeled by filename).
- **M files with non-trivial state machines** are promoted to standalone (`stream_subagents.py`, `handlers/approval.py`, `events.py`, `delegation/subagents/runner.py`).
- **S files (< 100 LOC)** appear as sections in the bundled doc or are mentioned only in `_index.md`.

### Where to start

- New to the codebase? Start with [/services/ai-backend/docs/architecture/data-flow.md](../../services/ai-backend/docs/architecture/data-flow.md) for the big picture, then read the cluster `_index.md` for whichever area you're touching.
- Investigating an incident? Open the use-case doc that matches the user-visible behavior in [/docs/use-cases/](../use-cases/) — each step links into the relevant decomp section.
- Considering a refactor? Skim section **G** of the affected files first — that's where the natural seams are called out.

## Companion deliverables

- [replacement-analysis.md](replacement-analysis.md) — per-cluster web-search audit of OSS libraries / middleware that could replace some or all of the bespoke logic, with feature-parity tables.
- [/docs/use-cases/](../use-cases/) — 14 end-to-end scenario flow docs (FE → facade → ai-backend → worker → SSE → FE) that reference these decomp docs from each step.

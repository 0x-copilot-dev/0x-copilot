# Cluster 03 — agent_runtime.context.memory

**Last reviewed:** 2026-05-06 · **Revision:** `a1d79d7a61868a6a9ae774e3a46c875356b29b78`

## Cluster scope

Memory backends, policies, summarization, token budgeting, and subagent trace helpers under [`src/agent_runtime/context/memory/`](../../src/agent_runtime/context/memory/).

## Entrypoints / wiring

- Execution factory and worker attach memory backends and policies per run context.
- Compression/summarization runs during graph turns when policy triggers.

## Likely unused or low-value symbols

| Location                           | Symbol / issue                                                              | Evidence                                                                    | Confidence | Action                                                                                              |
| ---------------------------------- | --------------------------------------------------------------------------- | --------------------------------------------------------------------------- | ---------- | --------------------------------------------------------------------------------------------------- |
| `context/memory/subagent_trace.py` | Locals `old_string`, `new_string`, `replace_all`, `glob` in parsing helpers | Vulture reports unused variables (likely unpacked or placeholder branches). | Medium     | Inspect those line ranges: either wire into trace formatting or prefix with `_` / narrow unpacking. |

Use ripgrep on the specific tool-call shapes in `subagent_trace.py` to confirm whether branches are defensive stubs.

## Test-only vs production

Memory policy matrix is covered in unit tests; long-context paths may be underrepresented.

## Code smells

- **Unused unpacked fields:** Often indicates schema drift (tool payload evolved; trace mapper did not) or copy-paste from another adapter.
- **File size:** `subagent_trace.py` is a hotspot for tool-arg normalization — favor small helpers inside the owning class per repo conventions.

## Follow-ups

- Open a targeted cleanup PR for `subagent_trace.py` once each flagged binding is classified (real omission vs intentional ignore).

## Deep scan (Vulture min 50)

**Raw lines (this subtree):** 42 · See [SUPPLEMENT-deep-scan-vulture50.md](./SUPPLEMENT-deep-scan-vulture50.md).

### Notes

- **`context/memory/constants.py`** — ~30 “unused variable” lines are nested registry keys (`AFTER_TOKENS`, etc.) — treat as **noise** unless `rg Keys.*` shows no use.
- **`subagent_trace.py`** — largest real cleanup candidates remain the unpacked locals already listed above.
- **`memory/policy.py`** — Vulture reports `MemoryAccessRequest` / `ensure_authorized` as unused — verify dynamic dispatch or test-only routes before removal.

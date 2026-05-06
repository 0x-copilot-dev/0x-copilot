# Deep scan — Vulture min-confidence 50 (raw inventory)

**Run:** `cd services/ai-backend && .venv/bin/vulture src --min-confidence 50 --sort-by-size`  
**Result:** 635 lines of findings (exit code 3 = dead code reported).  
**Revision (when generated):** `a1d79d7a61868a6a9ae774e3a46c875356b29b78`

## How to read this

Most lines are **not** permission to delete code.

1. **Nested key registries** — The codebase uses many inner classes (`Keys.Field`, `Keys.Query`, `Messages.*`, `Values.*`, persistence column `Keys`, skills char constants, etc.). Assignments like `AFTER_SEQUENCE = "after_sequence"` are referenced as **`Keys.Field.AFTER_SEQUENCE`** (or similar) elsewhere. Vulture often flags the **local name** inside the nested class as unused because it does not resolve cross-attribute use. Treat these as **scanner noise** unless `rg` shows no references to the **public path** (e.g. `Keys.Field.AFTER_SEQUENCE`).

2. **`Protocol` / ABC bodies** — Parameters and `...` stubs are flagged as unused variables.

3. **`TYPE_CHECKING` imports** — Reported as unused imports at 90% confidence in some modules.

4. **FastAPI / lifespan registration** — Route handlers and inner functions may be registered via decorators; Vulture reports `unused function` for `healthz` / `readyz` even when mounted.

5. **Test-only consumers** — Classes used only under `tests/` are **not** dead for the repo; they may still indicate **missing production wiring**.

## Per-cluster raw line counts

Counts assign each `src/...` path to one cluster (same bucketing as the numbered cluster docs):

| Cluster                                    | Vulture lines (min 50) |
| ------------------------------------------ | ---------------------: |
| 01 execution                               |                     54 |
| 02 capabilities                            |                    131 |
| 03 context/memory                          |                     42 |
| 04 delegation/subagents                    |                     45 |
| 05 persistence                             |                    140 |
| 06 api                                     |                     29 |
| 07 cross-cutting                           |                     35 |
| 08 runtime_api                             |                    139 |
| 09 runtime_worker                          |                     10 |
| 10 runtime_adapters                        |                      6 |
| other (`agent_runtime/*` not mapped above) |                      4 |
| **Total**                                  |                **635** |

## Category counts (entire `src/` tree)

| Category                                                            |     Count |
| ------------------------------------------------------------------- | --------: |
| `unused method`                                                     |       304 |
| (other patterns, mostly `unused variable` on constants / protocols) | remainder |

## Large reported items (sort-by-size tail)

Vulture’s `--sort-by-size` places larger blocks last. Examples from the tail (verify before deletion):

- `ToolBudgetMiddleware` / `check_admit` — class is **not imported from production `src/`** outside its module and tests (see cluster 02).
- `AnthropicCitationStreamAdapter` — **no `src/` references** outside `anthropic_stream_adapter.py`; tests exercise it directly.
- `FieldEncryptionBackfill` — may be job-only or CLI-only entrypoints.
- `HttpWorkspaceMembershipResolver` — implemented for production but **verify** `runtime_api` wiring vs default in-memory resolver.
- `InMemoryShareSnapshotStore` — used from **tests**; Vulture does not see test imports on `src/` scan.

## Ruff vs Vulture (streaming executor)

`ruff check` may report `F401` on `UsageAttributionResolver` in `runtime_worker/streaming_executor.py` while Vulture reports the **class** as unused in `usage_attribution.py`. The name **does** appear in type annotations in `streaming_executor.py`; align on `TYPE_CHECKING` imports or Ruff’s `typing` rules so tooling agrees.

## Regenerating

```bash
cd services/ai-backend
.venv/bin/vulture src --min-confidence 50 --sort-by-size > /path/to/vulture50.txt
```

Optionally add the whitelist module as a **second path** (same directory as this file):

```bash
.venv/bin/vulture src ../../docs/unused-code/ai-backend/vulture_whitelist.py --min-confidence 80
```

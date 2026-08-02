# Hyperparameters consolidated into one JSON — implementation plan

Implements **item 4 (P3)** of [PRD.md](PRD.md) §4. One checked-in JSON document defines every
LangGraph / Deep Agents / agent tuning constant, loaded once through a frozen Pydantic model and
injected. Today those numbers are literals scattered across ~20 `class Limits` / `class Defaults`
blocks and `Field(...)` defaults.

The document also lands the three **new** knobs this program needs (§6): the offloaded-result read
budget, the MCP catalog page size, and the `defer_loading` policy.

> **The one thing to get right:** §4. A number that is a _wire contract_ or a _validation invariant_
> must not become a runtime knob. Moving one of those into the JSON silently converts a contract
> into a dial, and the failure shows up as a `ValidationError` in a peer service — not here.

---

## 1. Inventory

Every constant below was located by grep; paths are relative to
`services/ai-backend/src/`. **T** = tunable (moves to JSON), **I** = invariant (stays a
code constant). Rationale for the split is §4.

### 1a. MCP capability loading — `capabilities/mcp/constants.py`

| Line      | Constant                                            | Value         | Class |
| --------- | --------------------------------------------------- | ------------- | ----- |
| `:258`    | `Limits.CARD_DESCRIPTION_MAX_LENGTH`                | `240`         | **I** |
| `:259`    | `DESCRIPTOR_DESCRIPTION_MAX_LENGTH`                 | `4_000`       | **I** |
| `:260`    | `LOAD_COST_MAX`                                     | `100_000`     | **I** |
| `:261`    | `MCP_SCHEMA_MAX_BYTES`                              | `16_384`      | **I** |
| `:262`    | `METADATA_LATENCY_MAX_MS`                           | `600_000`     | **I** |
| `:263-64` | `RESOURCE_NAME_MAX_LENGTH` / `MIME_TYPE_MAX_LENGTH` | `120` / `200` | **I** |
| `:265`    | `SAFE_MESSAGE_MAX_LENGTH`                           | `500`         | **I** |
| `:272`    | `Defaults.MAX_RESOURCE_DESCRIPTORS`                 | `100`         | **T** |
| `:273`    | `Defaults.MAX_TOOL_DESCRIPTORS`                     | `100`         | **T** |
| `:274`    | `Defaults.TIMEOUT_SECONDS`                          | `30`          | **T** |

The split is already legible in the consumers, and this is not a coincidence — the existing
`Limits` / `Defaults` naming carries it:

- **`Limits` are consumed as Pydantic `max_length=`** on contract fields:
  `capabilities/mcp/cards.py:131`, `:367`, `:432`, `:491`, `:516`;
  `capabilities/mcp/middleware/error_map_tool.py:127`;
  `delegation/subagents/contracts.py:308`; `capabilities/tools/cards.py:90`.
  `MCP_SCHEMA_MAX_BYTES` is an admission check at `capabilities/mcp/cards.py:704`.
- **`Defaults` are consumed as _field defaults_ on a settings-ish model**, all three in one place:
  `capabilities/mcp/loader.py:117-119` (`timeout_seconds`, `max_tool_descriptors`,
  `max_resource_descriptors`).

That is the whole rule in miniature: a value used as `max_length=` is an invariant; a value used as
a default a caller may already override is a tunable.

### 1b. Retry policy — duplicated literal

| Location                                 | Constant                                                                                              |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `capabilities/retrying_tool.py:68-70`    | `max_attempts = 3`, `initial_backoff_seconds = 0.5`, `max_backoff_seconds = 4.0` — **T**              |
| `runtime_worker/dependencies.py:150-152` | `RetryingTool.wrapping(..., max_attempts=3)` — **T**, and a literal re-statement of the class default |

Two independent `3`s that must agree and nothing enforces it. Canonical duplication case.

### 1c. The `limit = 2000` read budget — 20 sites

`BackendProtocol.read` / `aread` default, restated at every backend:

`capabilities/workspace/deep_backend.py:94,98,266,271` ·
`capabilities/backends/draft_backend.py:224,233` ·
`capabilities/backends/artifact_draft_backend.py:196,203` ·
`capabilities/mcp/catalog_backend.py:110,116` ·
`capabilities/sandbox/policy_backend.py:170` ·
`capabilities/desktop/workspace_backend.py:618,691` ·
`runtime_adapters/file/large_tool_result_backend.py:56,66` ·
`runtime_adapters/file/agent_state_store.py:323,339` ·
`runtime_adapters/file/subagent_trace_backend.py:131,141` ·
`agent_runtime/context/memory/subagent_trace.py:435,445`

Plus three desktop `_READ_LIMIT: Final = 2000` constants:
`capabilities/desktop/host_route.py:81`, `host_tool_paths.py:375`, `host_floor.py:126`.

**This one is a trap and is split (see §4.3).** The _signature default_ is an upstream deepagents
protocol signature — **I**. The _effective budget the runtime applies_ is ours — **T**, and it is
new knob N1 (§6.1).

### 1d. Model call shape — `execution/deep_agent_builder.py`

| Line   | Constant                   | Value   | Class |
| ------ | -------------------------- | ------- | ----- |
| `:596` | `_MAX_OUTPUT_TOKENS`       | `1_024` | **T** |
| `:608` | `temperature=0.0` (mapper) | `0.0`   | **T** |

Both belong to the small deterministic mapper model config built at `:606`. `:473`/`:481` show the
main path already threads `temperature` / `max_tokens` from `ModelConfig`, so only the mapper's
hard-coded pair moves.

### 1e. Remaining `Limits` / `Defaults` blocks

| File                                               | Block                   | Notable values                                                                                                                                                      | Class                                                    |
| -------------------------------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| `capabilities/tools/constants.py:57`               | `Limits`                | `CARD_DESCRIPTION_MAX_LENGTH 240`, `TOOL_DESCRIPTION_MAX_LENGTH 4000`, `TOOL_SCHEMA_MAX_BYTES 16384`, `TOOL_NAME_MAX_LENGTH 120`, `PUBLIC_ERROR_MAX_LENGTH 500`     | **I**                                                    |
| `context/memory/constants.py:75`                   | `Defaults`              | `MAX_INPUT_TOKENS 128_000`, `RECENT_CONTEXT_RATIO 0.25`, `SUMMARY_THRESHOLD_RATIO 0.85`                                                                             | **T**                                                    |
| `context/memory/constants.py:84`                   | `Limits`                | `MEMORY_PATH_MAX_LENGTH 500`, `SUMMARY_FIELD_MAX_LENGTH 4000`, `TRACE_ID_MAX_LENGTH 200`                                                                            | **I**                                                    |
| `delegation/subagents/constants.py:87`             | `Defaults`              | `SUBAGENT_TIMEOUT_SECONDS 120`, `SUBAGENT_CONCURRENCY_LIMIT 2`                                                                                                      | **T**                                                    |
| `delegation/subagents/constants.py:95`             | `Limits`                | `TIMEOUT_MAX_SECONDS 3600`, `CONCURRENCY_LIMIT_MAX 100`, `RESULT_RESPONSE_MAX_LENGTH 12_000`, `RECENT_MESSAGES_MAX_COUNT 10`, `ARTIFACTS_MAX_COUNT 20`              | **I** (the `*_MAX` pair bounds the **T** defaults above) |
| `capabilities/skills/constants.py:95`              | `Limits`                | `SKILL_FILE_MAX_BYTES 10 MiB`, `SKILL_DESCRIPTION_MAX_LENGTH 240`, `SOURCE_PRECEDENCE_MAX 1_000_000`                                                                | **I**                                                    |
| `observability/constants.py:30`                    | `Defaults`              | `MAX_STREAM_FIELD_LENGTH 2_000`                                                                                                                                     | **T**                                                    |
| `capabilities/mcp/catalog.py:130`                  | `Limits`                | `SERVER_MARKDOWN_MAX_BYTES 4096`, `HEADER_RESERVE_BYTES 900`, `INDEX_SUMMARY_MAX_BYTES 96`, `INDEX_SUMMARY_MIN_BYTES 24` — **T**; `MIN_NEWLINES_PER_FILE 2` — **I** | mixed                                                    |
| `capabilities/citation_projection.py:40`           | `Limits`                | `PER_RESULT_MAX 25`                                                                                                                                                 | **T**                                                    |
| `capabilities/concurrency/contracts.py:69`         | `ConcurrencyBounds`     | `SERIAL_PARALLELISM 1`, `MAX_PARALLELISM 16`                                                                                                                        | **I**                                                    |
| `runtime_api/schemas/context_occupancy.py:117,231` | `Limits`                | `MAX_LABEL`, `MAX_DETAIL`, `MAX_IDENTIFIER`, `MAX_PROVIDER`                                                                                                         | **I** — see §4.1                                         |
| `runtime_adapters/offload.py:96`                   | —                       | `PREVIEW_CHARS 200`                                                                                                                                                 | **T**                                                    |
| `context/memory/summarization.py:134-135`          | —                       | `PREVIEW_LINE_LIMIT 10`, `PREVIEW_CHAR_LIMIT 2_000`                                                                                                                 | **T**                                                    |
| `context/planning/providers.py:244`                | `ContextProviderBounds` | `MAX_INLINE_TOKENS 8_000`                                                                                                                                           | **I** (it is the `le=` on `:390`)                        |
| `runtime_worker/mcp_operation_storage.py:73`       | —                       | `MAX_MODEL_RESULT_PREVIEW_BYTES 8_192`                                                                                                                              | **T**                                                    |

### 1f. Behavioural tunables currently living in `settings.py` (env)

`agent_runtime/settings.py:196-224`, `RuntimeExecutionSettings` — all **T**, all must leave env per
PRD AC2: `max_retries 2`, `max_parallel_runs 4`, `max_parallel_tasks 4`, `max_parallel_subagents 4`,
`tool_call_budget` (`:207`), `worker_poll_interval_seconds 1`, `worker_lock_seconds 60`,
`delta_coalesce_window_ms 0` (`:216`), `delta_coalesce_max_chunks 64` (`:219`). Also
`RuntimeSettings.default_timeout_seconds 60` (`:360`) and
`RuntimeSkillSettings.cache_ttl_seconds 60` (`:318`).

---

## 2. `hyperparameters.json` + the loader

### Layout

```
services/ai-backend/
  hyperparameters.json                                   # the document
  src/agent_runtime/hyperparameters/
    __init__.py
    contracts.py      # the frozen Pydantic models
    loader.py         # HyperparameterLoader
```

The document is grouped by the subsystem that owns each number, not flattened:

```json
{
  "schema_version": 1,
  "mcp_loading": {
    "max_tool_descriptors": 100,
    "max_resource_descriptors": 100,
    "timeout_seconds": 30.0,
    "catalog_page_size": 40,
    "defer_loading_policy": "off"
  },
  "mcp_catalog": {
    "server_markdown_max_bytes": 4096,
    "header_reserve_bytes": 900,
    "index_summary_max_bytes": 96,
    "index_summary_min_bytes": 24
  },
  "reads": { "default_line_limit": 2000, "offloaded_result_line_limit": 20000 },
  "retry": {
    "max_attempts": 3,
    "initial_backoff_seconds": 0.5,
    "max_backoff_seconds": 4.0
  },
  "execution": {
    "max_retries": 2,
    "max_parallel_runs": 4,
    "max_parallel_tasks": 4,
    "max_parallel_subagents": 4,
    "tool_call_budget": 40,
    "default_timeout_seconds": 60.0,
    "delta_coalesce_window_ms": 0,
    "delta_coalesce_max_chunks": 64,
    "worker_poll_interval_seconds": 1.0,
    "worker_lock_seconds": 60
  },
  "subagents": { "timeout_seconds": 120, "concurrency_limit": 2 },
  "context": {
    "max_input_tokens": 128000,
    "recent_context_ratio": 0.25,
    "summary_threshold_ratio": 0.85,
    "preview_line_limit": 10,
    "preview_char_limit": 2000,
    "offload_preview_chars": 200,
    "model_result_preview_bytes": 8192
  },
  "model_mapper": { "max_output_tokens": 1024, "temperature": 0.0 },
  "observability": { "max_stream_field_length": 2000 },
  "citations": { "per_result_max": 25 }
}
```

### Contracts (`contracts.py`)

House rules from `services/ai-backend/CLAUDE.md` — Pydantic at every boundary, **behaviour lives
inside classes** (no module-level helpers), StrEnum/Literal for known domains:

- One `HyperparameterSection` base: `model_config = ConfigDict(frozen=True, extra="forbid")`.
  `extra="forbid"` is PRD **AC1** — an unknown key fails at boot.
- One frozen `Hyperparameters` root aggregating the sections, plus
  `schema_version: Literal[1]`.
- **Every field carries its bound as `ge=`/`le=`** sourced from the corresponding **I** constant —
  e.g. `subagents.timeout_seconds: int = Field(120, ge=1, le=Limits.TIMEOUT_MAX_SECONDS)`,
  `concurrency_limit: int = Field(2, ge=1, le=Limits.CONCURRENCY_LIMIT_MAX)`. The invariant keeps
  policing the tunable; the JSON cannot widen it. This is the mechanism that makes §4 enforceable
  rather than aspirational.
- Known domains are `StrEnum`, never `str`: `DeferLoadingPolicy` (§6.3).
- Derived values are `@property` / `@computed_field` **on the section class** — no module-level
  helper functions.

### Loader (`loader.py`)

`class HyperparameterLoader` with classmethods only; no module-level singleton read (PRD **AC5**):

- `HyperparameterLoader.from_path(path) -> Hyperparameters` — read, `json.loads`, validate.
- `HyperparameterLoader.default() -> Hyperparameters` — the checked-in document, resolved relative
  to the package so a packaged desktop build finds it.
- `HyperparameterLoader.with_overrides(base, env) -> Hyperparameters` — §3.
- Loud failure: wrap `ValidationError` in a typed `HyperparameterError` naming the offending
  JSON pointer, raised at composition-root boot, never lazily at first use.

Injection: the composition roots (`runtime_api` app factory, `runtime_worker/dependencies.py`) load
once and pass `Hyperparameters` down the existing dependency-inversion seams. Consumers take the
model — or, better, the _one section they need_ — as a constructor argument.

### Secret hygiene (PRD **AC3**)

A test scans every key path for secret-shaped tokens (`key`, `secret`, `token`, `password`,
`credential`, `dsn`, `url`) and fails. The document is fully loggable by construction; a boot-time
`hyperparameters_loaded` log line dumping it whole is a feature, and the test is what keeps it safe.

---

## 3. Boundary against `RuntimeSettings`

`agent_runtime/settings.py` stays, unchanged in kind. The split, restating PRD §4's table as a
decision rule:

**A value belongs in `RuntimeSettings` if changing it means changing the _deployment_.** Store
backend (`RuntimeStoreSettings.backend`), DSNs, provider API keys (`ProviderSettings.api_key`,
`repr=False, exclude=True`), file-store roots, registry URLs, redirect URIs, feature/rollout
switches, `environment`. These are per-host, ops-reviewed, frequently secret, and must **not** be
diffable in git.

**A value belongs in `hyperparameters.json` if changing it means changing the _agent's behaviour_.**
Retries, parallelism, budgets, timeouts, previews, ratios. These are experiment-scoped, tuned by
whoever is tuning the agent, always safe to log, and the whole point is that a tuning change is a
reviewable diff.

After migration `RuntimeExecutionSettings` (`settings.py:196-224`) is largely emptied of numbers and
retains only genuine deployment concerns: `start_in_process_worker`, `allow_empty_capabilities`,
`event_bus_backend`, `enable_local_models`, `operation_gateway_mode`, the rollout block.
PRD **AC2** is a two-way grep assertion: no tunable readable from env, no deployment concern in the
JSON.

### Env still overrides — one layer, one place

Ops must retain a break-glass for an incident ("drop `max_parallel_subagents` to 1 right now")
without a redeploy of the JSON. That is a **single, uniform, prefixed** mechanism, not a re-scatter:

```
COPILOT_HP__EXECUTION__MAX_PARALLEL_SUBAGENTS=1
```

`HyperparameterLoader.with_overrides` collects `COPILOT_HP__` vars, splits on `__` into a nested
path, deep-merges over the parsed JSON, and re-validates through the same frozen model — so an
override is bound-checked identically and cannot smuggle an unknown key past `extra="forbid"`.
Three rules keep this from becoming the thing it replaced:

1. **No consumer ever reads an env var.** Only the loader does. This preserves AC2 (nothing is
   "readable from the environment" as a bespoke setting) while keeping an escape hatch.
2. **Every applied override is logged** at boot as `key → old → new`, so an incident knob is never
   invisible six months later.
3. **The override list is emitted in the run's observability context**, so an eval run whose numbers
   were quietly overridden is identifiable after the fact.

---

## 4. Tunables vs. invariants — the line that must not blur

A **tunable** is a number whose only consequence is _how the agent behaves_. Getting it wrong
degrades quality or cost. A **protocol / validation invariant** is a number that some _other party_
— a peer service, a persisted row, a Pydantic contract used as an inbound validator, an upstream
library signature — is entitled to rely on. Getting one of those wrong produces a `ValidationError`
or a truncated persisted field somewhere else, at a time and place unrelated to the change.

**Test:** if the number appears as `max_length=` / `min_length=` / `le=` / `ge=` on a Pydantic
contract, as a database column width, in a golden contract file, or in an upstream signature — it is
an invariant. If it appears as a _default value_ the caller may already override — it is a tunable.

### 4.1 Invariants — stay code constants

- **Anything used as `max_length=` on a contract.** `capabilities/mcp/cards.py:131,367,432,491,516`,
  `capabilities/tools/cards.py:90`, `delegation/subagents/contracts.py:308`,
  `capabilities/mcp/middleware/error_map_tool.py:127`. A card with a 240-char description bound is a
  shape peers and persisted rows depend on; raising it at runtime writes rows that a rollback then
  cannot re-validate, and lowering it rejects rows already stored.
- **`runtime_api/schemas/context_occupancy.py:117` and `:231`.** The file's own docstring already
  says it: these are _"Wire bounds, taken from the producer rather than re-picked"_, mirroring
  `context_origin.MAX_LABEL_LENGTH` and the persistence layer's `MAX_SEGMENT_DETAIL_CHARS`, and the
  contract doubles as the **inbound validator** for the `context_occupancy` stream payload. The
  docstring notes restating one as a literal _"is what broke the label bound once already"_. Do not
  make these settable. Do not make them settable "just for tests".
- **`packages/service-contracts/.../mcp_cross_service_golden_contract.json`** (`id_max_length 256`,
  `cursor_max_length 512`, `revision_feed_page_max_length 100`). Cross-service by definition; the
  golden file already is the single source. `hyperparameters.json` must never restate one.
- **`capabilities/concurrency/contracts.py:69-70`** `SERIAL_PARALLELISM 1` / `MAX_PARALLELISM 16` —
  these define the _semantics_ of `ConcurrencyMode`, not a dial.
- **`capabilities/mcp/catalog.py` `MIN_NEWLINES_PER_FILE 2`** — the structural guarantee the whole
  catalog exists to provide (a line-oriented file is readable by `read_file` offset/limit). Tuning
  it to `0` reintroduces the exact 70 KB single-line bug. Invariant, with its test.
- **`context/planning/providers.py:244` `MAX_INLINE_TOKENS 8000`** — it is literally the `le=` at
  `:390`. The `max_inline_tokens` _field_ is the tunable; the bound is not.
- **`delegation/subagents/constants.py:95` `TIMEOUT_MAX_SECONDS` / `CONCURRENCY_LIMIT_MAX`** — these
  bound the tunables in `Defaults`. They become the `le=` on the JSON fields.

### 4.2 Tunables — move to the JSON

Everything marked **T** in §1: the `Defaults` blocks, retry policy, parallelism and budgets, the
mapper model shape, preview sizes, memory ratios, catalog byte budgets, `PER_RESULT_MAX`,
`MAX_STREAM_FIELD_LENGTH`.

### 4.3 The `limit = 2000` case — the one that is _both_

The 20 sites in §1c are not one constant. They are two:

- **The signature default on `BackendProtocol.read` / `aread` is an upstream invariant.** deepagents
  defines the protocol; our backends implement it. Substitutability requires the signature to match
  what deepagents declares. **Verify the upstream default before writing a line of this**
  (`deepagents/backends/protocol.py` and `middleware/filesystem.py` in the service `.venv`) — if
  deepagents' `read_file` tool passes `limit` explicitly from its own schema default, our signature
  default is nearly dead code and only the tool-schema default matters. That measurement decides
  whether N1 (§6.1) is implemented as a constructor budget or as a tool-schema override.
- **The budget the runtime _applies_ when serving a read is ours.** That is new knob N1.

Concretely: leave `limit: int = 2000` in the signatures (matching upstream, one shared
`Reads.DEFAULT_LINE_LIMIT` constant to kill the 20-way duplication), and give the backends that need
a different effective budget an injected `read_budget` from the JSON. The three desktop
`_READ_LIMIT: Final = 2000` constants collapse into that same shared constant.

---

## 5. Migration — incremental, ordered

No big-bang edit. Eight steps; each is independently mergeable, each leaves the tree green, and
**steps 1-2 change no behaviour at all**.

| #     | Step                                                                                                                                                                                                                                                                                                                                                                         | Behaviour change                 |
| ----- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------- |
| **1** | Land `hyperparameters/` (contracts + loader) and `hyperparameters.json` with values **byte-identical to today's** (PRD **AC4**). Nothing consumes it. Tests: `extra="forbid"` rejects an unknown key; the secret-key scan; a snapshot test pinning every default.                                                                                                            | none                             |
| **2** | Wire loading at the composition roots (`runtime_api` app factory, `runtime_worker/dependencies.py`) and make `Hyperparameters` available for injection. Still zero consumers.                                                                                                                                                                                                | none                             |
| **3** | **Migrate `capabilities/mcp/constants.py:272-274`** — `loader.py:117-119` takes the three from the injected model. Smallest possible first cut, exercised by the MCP loader's existing tests.                                                                                                                                                                                | none                             |
| **4** | **Migrate the duplicated retry policy** — `retrying_tool.py:68-70` defaults come from the model; `runtime_worker/dependencies.py:152` stops restating `3`. Kills the two-independent-3s bug class.                                                                                                                                                                           | none                             |
| **5** | **Collapse `limit = 2000`** to one shared constant across the 20 sites + 3 `_READ_LIMIT`s, per §4.3. Mechanical, no injection yet.                                                                                                                                                                                                                                           | none                             |
| **6** | **Migrate `settings.py:196-224` + `:318` + `:360`.** The behaviour-preserving but riskiest step (§7): every `RUNTIME_*` env var for a tunable stops being read. Ship with a **deprecation shim** — one release where a legacy env var still applies but logs `deprecated_hyperparameter_env` loudly, plus a CHANGELOG note and the `COPILOT_HP__` equivalent in the message. | none (shim); env surface changes |
| **7** | **Migrate the long tail** — memory `Defaults`, subagent `Defaults`, catalog byte budgets, mapper model shape, `PREVIEW_*`, `PER_RESULT_MAX`, `MAX_STREAM_FIELD_LENGTH`. Batch into 2-3 PRs by subsystem; each is a repeat of step 3's pattern.                                                                                                                               | none                             |
| **8** | **Land the new knobs** (§6) and remove the step-6 shim. Only now does behaviour change, and it changes because we chose the new defaults.                                                                                                                                                                                                                                    | **yes — intentional**            |

Guardrails carried through:

- After step 7, a **grep test in both directions** enforces PRD AC2: no `RUNTIME_*` env var maps to
  a tunable, and no deployment/secret concern appears in the JSON.
- A **lint test** fails on a new bare numeric literal in the migrated modules, so the scatter cannot
  silently re-form.
- The step-1 snapshot test is the AC4 proof and must stay green through step 7. It goes red exactly
  once — in step 8 — and that diff is the record of what this program deliberately changed.

Per PRD §9, item 4 lands **before** item 5 (deepagents `0.7.1` evaluation), so the upgrade has one
place to record any tunable that moves.

---

## 6. New knobs this program adds

Landed in **step 8**, after the mechanical migration is done and green.

### 6.1 N1 — `reads.offloaded_result_line_limit`

**The bug it fixes.** `capabilities/mcp/catalog.py:1-12` documents it: a real Linear descriptor is
70,465 bytes, 52 tools, **zero newlines**. It exceeds the admission budget, gets offloaded, and the
preview the model receives is `"\n".join(content.splitlines()[:10])[:2000]` — with no newlines, the
first 2000 characters of tool #1. Re-reading at an offset returns the same 2000 characters. The model
never reaches `list_issues` and the run completes with **empty success**.

The catalog (item 1) fixes the _production_ side. N1 fixes the _read_ side: when the thing being read
is an already-offloaded blob (`runtime_adapters/file/large_tool_result_backend.py:56,66`), the 2000
default is the wrong budget — it was chosen for a source file, not for a content-addressed dump the
model deliberately asked for by reference.

- Type: `int`, `Field(ge=1, le=...)`, default proposed **20000** lines.
- Applies **only** to the `/large_tool_results/` backend, injected — the generic read default stays
  2000 so ordinary workspace reads are unchanged.
- Pairs with a byte ceiling so a pathological blob still cannot blow the context window; the line
  budget is what makes a _legitimately large_ result reachable, not a licence to inline anything.

### 6.2 N2 — `mcp_loading.catalog_page_size`

The catalog's index currently has byte budgets (`SERVER_MARKDOWN_MAX_BYTES 4096`,
`HEADER_RESERVE_BYTES 900`) but **no notion of a page**: `catalog.py:130`'s docstring states that a
server with enough tools to blow the budget on names alone produces a _larger file_ rather than a
shorter list, because a hidden tool is the exact failure the module exists to remove. Correct as a
default — and it means a 52-tool server produces one long `SERVER.md`.

N2 adds pagination _without_ ever hiding a tool: at more than `catalog_page_size` tools the index
splits into `SERVER.md` + `tools/INDEX-2.md`, with every page cross-linked and `SERVER.md` naming the
continuation explicitly.

- Type: `int`, `Field(ge=1, le=...)`, default proposed **40**.
- Invariant that must survive: **the union of pages lists every tool.** Test it directly, not by
  byte count.

### 6.3 N3 — `mcp_loading.defer_loading_policy`

[TOOL-SEARCH-PLAN.md](TOOL-SEARCH-PLAN.md) establishes that `defer_loading` is Anthropic-only, is a
first-class field on a custom tool definition, survives LangChain conversion, and changes **only the
wire representation of the definition**. It must therefore be (a) opt-in, (b) provider-aware, and
(c) participate in the tool-definition digest — the plan is explicit that the digest must be computed
over the post-gate list.

- Type: **`StrEnum`**, not `bool`, because three states are already needed and a boolean would have
  to be widened later:

| Value      | Meaning                                                                                         |
| ---------- | ----------------------------------------------------------------------------------------------- |
| `off`      | never emit `defer_loading` (the default, and the only safe value until item 1 is live-verified) |
| `mcp_only` | defer MCP-sourced tool definitions; built-ins and the search tool stay eager                    |
| `all`      | defer every deferrable definition                                                               |

- **Provider gate stays in code, not in the JSON.** TOOL-SEARCH-PLAN notes a `defer_loading` key sent
  to `/chat/completions` via `langchain_openai` is at best ignored and at worst rejected. The knob
  expresses _intent_; the builder decides whether the active provider can honour it. A JSON knob must
  never be able to send a malformed request to a non-Anthropic provider.
- `off` at launch, per PRD §9 — item 6 runs only after item 1 is live-verified.

---

## 7. Riskiest part

**Step 6 — migrating `RuntimeExecutionSettings` off env.** Every other step is additive or
mechanical. Step 6 silently changes the meaning of environment variables that live outside this
repository: desktop supervisor launch env, Docker compose files, `deploy/self-host`, CI, and any
operator's shell history. A `RUNTIME_MAX_PARALLEL_SUBAGENTS=1` that quietly stops applying does not
fail — it makes the agent four times more parallel than an operator believes, in production, with a
green test suite. That is why step 6 ships with a logging deprecation shim for one release and is
removed only in step 8, and why the two-way grep assertion is a test rather than a review checklist
item.

Second-order risk, cheaper but likelier: mis-classifying one `Limits` value as a tunable in step 7.
The `ge=`/`le=` sourcing in §2 is the structural defence — a tunable is bounded by the invariant it
came from, so even a wrong call cannot widen a contract.

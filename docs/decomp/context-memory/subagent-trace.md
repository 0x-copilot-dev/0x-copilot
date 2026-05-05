# Decomp — `agent_runtime/context/memory/subagent_trace.py`

Source: [services/ai-backend/src/agent_runtime/context/memory/subagent_trace.py](../../../services/ai-backend/src/agent_runtime/context/memory/subagent_trace.py) — **571 LOC, L.** Two classes — `SubagentTraceProjector` (pure projection of events into Markdown/JSON files) and `SubagentArtifactsBackend` (Deep Agents `BackendProtocol` adapter that exposes `/subagents/<task_id>/{conversation.md,tool_calls.json,summary.md,events.jsonl}` as a **read-only virtual filesystem** to the supervisor).

## A. Top-level structure

| Symbol                                                                           |   Lines | Purpose                                                                                                                                               |
| -------------------------------------------------------------------------------- | ------: | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_extract_text(value)`                                                           |   41–52 | Local string-coercion helper. **Lives here to break the `runtime_worker → agent_runtime → runtime_worker` import cycle**.                             |
| `StreamTextHelper` shim                                                          |   55–56 | Wraps `_extract_text` as a static method (parity with `runtime_worker.stream_messages.StreamTextHelper`).                                             |
| `_Files`                                                                         |   59–65 | Constants: `CONVERSATION="conversation.md"`, `TOOL_CALLS="tool_calls.json"`, `SUMMARY="summary.md"`, `EVENTS="events.jsonl"`. `ALL` tuple of all 4.   |
| `_PATH_PREFIX = "/subagents/"`                                                   |      68 | Virtual mount point.                                                                                                                                  |
| `_TASK_PATH` regex                                                               |      73 | `^(?:/subagents)?/(?P<task_id>[^/]+)(?:/(?P<file>.+?))?/?$` — accepts both prefixed and prefix-stripped paths.                                        |
| `_ROOT_PATHS`                                                                    |      74 | `{"/", "/subagents", "/subagents/"}`.                                                                                                                 |
| `_TOOL_OUTPUT_PREVIEW_LIMIT = 1_500`                                             |      75 | Truncation cap for tool output preview.                                                                                                               |
| `_READ_ONLY_ERROR`                                                               |      76 | `"The /subagents/ filesystem is read-only."` — uniform write-error message.                                                                           |
| `SubagentTraceProjector.visible_events(events)` (classmethod)                    |   82–94 | Drop events with `visibility != USER` or `redaction_state == OFFLOADED`.                                                                              |
| `list_task_ids_with_names(events)`                                               |  96–121 | `(task_id, subagent_name)` tuples in insertion order; deduped.                                                                                        |
| `events_for_task(task_id, events)`                                               | 123–146 | Events with `parent_task_id == task_id` OR (subagent lifecycle event payload's `task_id` matches).                                                    |
| `project_summary(task_id, events)`                                               | 148–207 | Markdown summary with sections: Subagent / Status / Objective / Result / Run.                                                                         |
| `project_tool_calls(task_id, events)`                                            | 209–263 | JSON dump of per-call records: `{call_id, tool_name, args, output, started_at, completed_at, status}`. Sorted by `started_at`.                        |
| `project_conversation(task_id, events)`                                          | 265–318 | Chronological prose: model deltas + `> tool_call:` + `< tool_result:` lines.                                                                          |
| `project_events_jsonl(task_id, events)`                                          | 320–332 | One `event.model_dump_json` per line.                                                                                                                 |
| `_truncated_output(output)` (classmethod)                                        | 334–347 | Cap dict/list/string output at `_TOOL_OUTPUT_PREVIEW_LIMIT` chars.                                                                                    |
| `SubagentArtifactsBackend.PATH_PREFIX = "/subagents/"`                           |     353 | The mount point matched by `CompositeBackend`.                                                                                                        |
| `__init__(*, event_store, persistence, org_id, conversation_id, current_run_id)` | 355–368 | Capture conversation + current_run scope for event collection.                                                                                        |
| `ls(path)` / `als(path)`                                                         | 372–405 | List task ids at root; list 4 files inside a task dir. Two-shape path matching.                                                                       |
| `read(file_path, offset, limit)` / `aread(...)`                                  | 407–440 | Project the requested file via `_project_file`. `offset` and `limit` accepted but **not honored** (full content always returned).                     |
| `write` / `awrite` / `edit` / `aedit`                                            | 442–464 | **All four return `_READ_ONLY_ERROR`.**                                                                                                               |
| `grep` / `agrep` / `glob` / `aglob`                                              | 466–486 | **All four return empty matches** (pattern search not supported on virtual FS).                                                                       |
| classmethod `_project_file(task_id, file_name, events)`                          | 490–505 | Dispatch on file_name to the appropriate `SubagentTraceProjector` method.                                                                             |
| static `_normalize_dir_path(path)`                                               | 507–513 | Default empty path → `/subagents/`; trailing slash preserved.                                                                                         |
| `_collect_events()`                                                              | 515–530 | Walk prior + current run_ids; aggregate events; filter to visible.                                                                                    |
| `_conversation_run_ids()`                                                        | 532–550 | Walk message records; collect distinct run_ids; ensure `current_run_id` is included.                                                                  |
| `_record_run_id(record)`                                                         | 553–554 | Trivial accessor.                                                                                                                                     |
| `_run_sync(coro)`                                                                | 557–571 | Sync→async bridge for `BackendProtocol`'s sync API surface. Uses `asyncio.run_coroutine_threadsafe` if a loop is already running, else `asyncio.run`. |

## B. Feature inventory

| Domain                                                   | Symbols                                                                         |  LOC |
| -------------------------------------------------------- | ------------------------------------------------------------------------------- | ---: |
| **Pure event-to-file projection (4 file shapes)**        | `SubagentTraceProjector` and all its classmethods                               | ~250 |
| **Deep Agents `BackendProtocol` adapter (read-only FS)** | `SubagentArtifactsBackend.{ls, read, write, edit, grep, glob}` + async variants | ~150 |
| **Event collection + scope walking**                     | `_collect_events`, `_conversation_run_ids`                                      |  ~40 |
| **Sync/async bridge**                                    | `_run_sync`                                                                     |  ~15 |
| **Path matching + helpers**                              | `_TASK_PATH`, `_ROOT_PATHS`, `_normalize_dir_path`                              |  ~15 |

## C. Functional spec per domain

### `/subagents/<task_id>/...` virtual FS layout

```
/subagents/
  <task_id_a>/
    conversation.md      ← chronological model deltas + tool calls
    tool_calls.json      ← per-call structured record
    summary.md           ← human-readable status + objective + result
    events.jsonl         ← raw event envelopes, one per line
  <task_id_b>/...
```

**Read-only by design** (1–10 docstring): writes always fail with `_READ_ONLY_ERROR`. Projection is computed on demand from the event store; **nothing is persisted by this module**.

### `_collect_events` algorithm

1. Get distinct run_ids reachable through the conversation's parent-message chain (up to 200 messages).
2. For each run_id, `list_events_after(after_sequence=0)` — full replay.
3. Filter to events whose `conversation_id` matches (defensive against cross-conversation pollution).
4. Filter via `SubagentTraceProjector.visible_events` — drop INTERNAL or OFFLOADED.
5. Return aggregate.

**Why all prior runs**: a subagent task may span runs (e.g. user clarified mid-stream). The supervisor needs visibility into the full historical trace.

### `events_for_task` scoping rule

Two ways an event belongs to a task:

- `event.parent_task_id == task_id` (most events).
- Lifecycle events (`SUBAGENT_STARTED`/`SUBAGENT_COMPLETED`) where `payload.task_id == task_id`.

Lifecycle events don't have `parent_task_id` set to themselves, so the second rule is essential.

### `project_summary` shape

Markdown with five sections:

```
# Subagent <task_id>

## Subagent
<subagent_name>

## Status
<running | completed | failed | timed_out | cancelled>

## Objective
<from started.payload.summary or "(no objective recorded)">

## Result
<from completed.payload.summary or status-dependent fallback>

## Run
<run_id>
```

If no `SUBAGENT_COMPLETED` exists yet (subagent didn't finish), status falls back to "running" and result becomes a "did not reach a terminal state" marker (187–190).

### `project_tool_calls` JSON shape

Each call merged across `TOOL_CALL_STARTED` / `TOOL_RESULT` / `TOOL_CALL_COMPLETED` events into a single record:

```json
{
  "call_id": "...",
  "tool_name": "...",
  "args": {...},
  "output": "...",
  "started_at": "...",
  "completed_at": "...",
  "status": "completed"
}
```

Tool name backfill (238–239): replaces only when previously None or `"unknown_tool"` (defensive — prefers the more-informative name).

Args backfill (241–242): only when previously None or empty dict.

Sorted by `started_at` ascending.

### `project_conversation` Markdown shape

Chronological lines:

- Plain text for `MODEL_DELTA`.
- `## Final response\n<text>` for `FINAL_RESPONSE`.
- `> tool_call: <name> args=<json>` for `TOOL_CALL_STARTED`.
- `< tool_result: <json>` for `TOOL_RESULT`.

`last_kind` tracking inserts blank lines between text and tool-call sections for readability (282–284, 290–291, 303–304).

### Path matching

`_TASK_PATH` (73): `^(?:/subagents)?/(?P<task_id>[^/]+)(?:/(?P<file>.+?))?/?$`

Accepts both:

- `/subagents/<task_id>/file.md` (direct caller).
- `/<task_id>/file.md` (via `CompositeBackend` after prefix strip).

Comment 69–72: "deepagents' CompositeBackend strips the matched route prefix before delegating."

### Sync/async bridge (`_run_sync`)

The `BackendProtocol` sync API exists for callers that haven't migrated to async. The bridge:

- If event loop is already running (called from async code) → `asyncio.run_coroutine_threadsafe(coro, loop).result()`.
- Else → `asyncio.run(coro)` for a one-shot run.

## D. Bugs / edge cases / invariants

- **Read-only invariant** (442–464): write/edit/awrite/aedit all return the same error. No filesystem state is mutated.
- **Visibility + redaction filter at collection time** (530): events with `visibility != USER` or `redaction_state == OFFLOADED` are dropped before any projection runs. Prevents internal-tool noise leaking into supervisor-visible files.
- **Events scoped to current conversation only** (527): cross-conversation pollution would otherwise be possible if the same `run_id` ever appeared in two conversations (theoretically impossible, defensively filtered).
- **Tool name backfill prefers known names** (238): if multiple events for the same call_id arrive with conflicting names (e.g. one streamed UNKNOWN_TOOL, another resolved later), the resolved name wins.
- **Args backfill prefers non-empty maps** (241): defends against the streaming chunk arriving before the full args object.
- **`current_run_id` always included** (548–549): even if the parent-message chain didn't surface it (e.g. user message hasn't been written yet at backend invocation time).
- **Empty conversation fallback** (315–317): "(no model text or tool calls were emitted before the subagent ended)" — surfaces clearly when subagent terminated without any user-visible output.
- **`grep`/`glob` return empty silently** (466–486): supervisor calls don't fail, just return no matches. Prevents the supervisor from believing a search confirmed absence (it didn't search).
- **`_TASK_PATH` matches dual-shape** (73): essential for `CompositeBackend` integration.
- **`_collect_events` is awaited fresh on every call** (385, 428): no caching. A long-running supervisor that re-reads the FS will re-execute the entire walk.
- **`_run_sync` on already-running loop** (568–571): uses `run_coroutine_threadsafe`. This requires the coroutine to be safe to run from a different thread — which all the persistence ports are because they go through `_tenant_connection`'s pool.
- **`offset`/`limit` accepted but ignored on read** (407–413, 415–440): full file content returned regardless. Bug-shaped if a supervisor expects pagination, but in practice the supervisor reads small files end-to-end.
- **Tool output truncated in 2 places** (`_truncated_output` 334–347): used in `project_tool_calls` and `project_conversation`. Cap is `_TOOL_OUTPUT_PREVIEW_LIMIT = 1_500` chars; suffix is `…[truncated]`.
- **`_extract_text` shim** (41–52): explicit comment about avoiding circular import. Lives here permanently.

## E. Hardcoded vs configurable

### Hardcoded

- File names (60–63): `conversation.md`, `tool_calls.json`, `summary.md`, `events.jsonl`.
- Path prefix `/subagents/`.
- Truncation cap 1500.
- Read-only error message string.
- Markdown templates (200–207).
- `> tool_call:` / `< tool_result:` line prefixes (305, 311).
- Backfill defaults: `"subagent"`, `"unknown_tool"`, `"completed"`.
- Message limit 200 (538) — duplicated with `handlers/run.py` and `service.py`.
- 4 file types in `_Files.ALL`. Adding a new file requires editing both `_Files` AND `_project_file`.

### Configurable

- All ports + scope injected via `__init__`.

## F. External dependencies and coupling

### Internal

- `agent_runtime.api.constants.Keys`.
- `agent_runtime.api.async_ports.AsyncEventStorePort`, `AsyncPersistencePort`.
- `runtime_api.schemas.MessageRecord`, `RuntimeApiEventType`, `RuntimeEventEnvelope`, `RuntimeEventRedactionState`, `RuntimeEventVisibility`.

### Third-party

- `deepagents.backends.protocol` — `BackendProtocol`, `EditResult`, `FileInfo`, `GlobResult`, `GrepResult`, `LsResult`, `ReadResult`, `WriteResult`. **The sole reason this module exists**: implementing the protocol so Deep Agents `CompositeBackend` can mount it.

### Stdlib

- `json`, `re`, `datetime`, `collections.abc`, `asyncio` (lazy).

## G. Suggested decomposition seams

The two classes already separate the **projection logic** (pure) from the **adapter logic** (I/O). Cuts:

1. **`subagent_projector.py`** — `SubagentTraceProjector` + `_Files` + `_TOOL_OUTPUT_PREVIEW_LIMIT` + `_extract_text`/`StreamTextHelper`. ~280 LOC. Pure functions over `Sequence[RuntimeEventEnvelope]`.
2. **`subagent_artifacts_backend.py`** — `SubagentArtifactsBackend` + `_TASK_PATH` + `_ROOT_PATHS` + `_PATH_PREFIX` + `_READ_ONLY_ERROR` + `_run_sync`. ~280 LOC.

The **`_extract_text` cycle-break** (41–52 + 55–56) is the file's most awkward dependency — it duplicates `runtime_worker.stream_messages.StreamTextHelper.extract` to break a circular import. The seam is to lift that helper into `service-contracts` or `agent_runtime/utils` so both modules can import it.

The **4-file projection** (`_project_file` dispatch + `_Files.ALL`) is a switch table that grows linearly with file count. A `dict[file_name, projector_method]` registry would make it easier to add new file types.

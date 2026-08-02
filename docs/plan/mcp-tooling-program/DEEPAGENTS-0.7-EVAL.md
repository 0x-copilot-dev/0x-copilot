# deepagents 0.6.12 → 0.7.1 upgrade evaluation

**Verdict: DO NOT UPGRADE NOW.** Revisit when issue 4658 (`ToolSelectionMiddleware`)
ships, or when we specifically need `FilesystemMiddleware(tools=[...])`.

Evaluated 2026-08-02 against `deepagents==0.7.1` (latest) vs our pin `deepagents==0.6.12`.
Method: upstream release notes + changelog, GitHub issue/timeline API, and a
file-level source diff of the 13 modules we integrate against, fetched at tag
`deepagents==0.7.1` and diffed against the installed 0.6.12 in
`services/ai-backend/.venv`.

The compare is **672 commits / 300+ files**. This is a real minor, not a patch.

---

## 1. The thing that would have justified upgrading did NOT land

**Answer: no. 0.7.x does not replace the work we are about to build. Keep building.**

| Upstream item                                                                              | State                                                                                               |
| ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------- |
| Issue **616** — lazy loading / progressive disclosure of MCP tools based on selected Skill | **OPEN.** Labels `feature, MCP, deepagents, external`. Last activity 2026-07-17. No implementation. |
| Issue **3672** — `SemanticToolSelectionMiddleware`                                         | **Closed 2026-05-31 with zero comments and no linked PR.** Superseded by ↓                          |
| Issue **4658** — `Add ToolSelectionMiddleware for per-turn tool filtering`                 | **OPEN**, filed 2026-07-11, cross-referenced from 3672. No implementation.                          |

Corroborating evidence, not just issue state:

- `deepagents/middleware/` at tag 0.7.1 contains exactly two new modules vs
  0.6.12: `_prompt_caching.py` and `_video.py`. No tool-selection module.
- `middleware/__init__.py` is **byte-identical** between 0.6.12 and 0.7.1 — zero
  new public middleware exports.
- `middleware/_tool_exclusion.py` is **byte-identical**. The only tool-visibility
  lever upstream still ships is the static, whole-session
  `HarnessProfile(excluded_tools=...)` we already use.

Issue 4658's own text states the problem in our exact terms: the middleware stack
"assembles the tool list once at agent construction … and it stays fixed for the
life of the session", and `_ToolExclusionMiddleware` "can drop tools, but only
statically". That is upstream confirming the gap our program exists to close.

**Implication for the MCP tooling program: proceed. There is no upstream
duplication risk in the 0.7 line.** The one adjacent thing 0.7.0 _did_ ship is
`FilesystemMiddleware(tools=[...])`, a static allowlist for built-in filesystem
tools only — useful for trimming our own baseline tool budget, irrelevant to MCP
tool selection.

---

## 2. `HarnessProfile` — no field changes

`services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py:241`
constructs `HarnessProfile(system_prompt_suffix=…, excluded_tools=…, extra_middleware=…)`.

AST-extracted field lists are **identical** in 0.6.12 and 0.7.1:

```
HarnessProfile:
    base_system_prompt: str | None = None
    system_prompt_suffix: str | None = None
    tool_description_overrides: Mapping[str, str] = field(default_factory=dict)
    excluded_tools: frozenset[str] = frozenset()
    excluded_middleware: frozenset[type[AgentMiddleware] | str] = frozenset()
    extra_middleware: Sequence[AgentMiddleware] | Callable[[], Sequence[AgentMiddleware]] = ()
    general_purpose_subagent: GeneralPurposeSubagentProfile | None = None
```

`HarnessProfileConfig` and `GeneralPurposeSubagentProfile` are likewise unchanged.
**No migration needed for the profile construction itself.**

One _semantic_ change reaches `base_system_prompt` (see §5): `BASE` is now the
empty string, so `base_system_prompt` went from "replaces the SDK's authored
prompt" to "is the only authored prompt".

---

## 3. Middleware seam — one ordering move, one silent instance-sharing regression

### 3a. Final middleware order

Reconstructed from `graph.py` in both versions (`[x]` = conditional):

| #   | 0.6.12                       | 0.7.1                                   |
| --- | ---------------------------- | --------------------------------------- |
| 1   | **`TodoListMiddleware`**     | _(gone)_                                |
| 2   | `[SkillsMiddleware]`         | `[SkillsMiddleware]`                    |
| 3   | `FilesystemMiddleware`       | `FilesystemMiddleware`                  |
| 4   | `[SubAgentMiddleware]`       | `[SubAgentMiddleware]`                  |
| 5   | `SummarizationMiddleware`    | `SummarizationMiddleware`               |
| 6   | `PatchToolCallsMiddleware`   | `PatchToolCallsMiddleware`              |
| 7   | `[AsyncSubAgentMiddleware]`  | `[AsyncSubAgentMiddleware]`             |
| 8   | **our `middleware=`**        | **our `middleware=`** (spliced here)    |
| 9   | profile `extra_middleware`   | profile `extra_middleware`              |
| 10  | `[_ToolExclusionMiddleware]` | prompt caching (+ Bedrock, + Fireworks) |
| 11  | prompt caching (+ Bedrock)   | `[MemoryMiddleware]`                    |
| 12  | `[MemoryMiddleware]`         | `[HumanInTheLoopMiddleware]`            |
| 13  | `[HumanInTheLoopMiddleware]` | **`[_ToolExclusionMiddleware]`** (last) |

**Good news:** the relative order of _our_ middleware vs the profile factories is
**preserved**. 0.7.1 replaces `deepagent_middleware.extend(middleware)` with
`_apply_custom_middleware(..., core_names=_main_core_names)`, which splices new
entries immediately after the last core member — i.e. exactly where the old
`extend` put them. Our `MIDDLEWARE_ORDER`-asserting MCP stack
(`capabilities/mcp/middleware/compose.py`) is per-tool wrapping and is unaffected
either way.

**The one move:** `_ToolExclusionMiddleware` goes from position 10 to last. It is
now the innermost `wrap_model_call`, deliberately — the upstream comment says
"so excluded tool names are stripped last and cannot be restored by a custom
`wrap_model_call`". That is a _hardening in our favour_. It stays inner relative
to `RuntimeControlMiddleware`, so `RuntimeToolSurfaceSnapshot.from_tools`
(`capabilities/middleware/runtime_tool_control.py:112`) observes the same
pre-exclusion list it observes today. No change to the canary.

### 3b. **REGRESSION — the general-purpose subagent silently shares the supervisor's middleware instances**

This is the subtlest breaking change and it is not in the release notes.

0.7.1 `graph.py` adds to the GP-subagent build:

```python
_gp_original_name_to_index = {m.name: i for i, m in enumerate(gp_middleware)}
...
_gp_inheritable = [m for m in (middleware or []) if m.name in _gp_original_name_to_index]
gp_middleware = _apply_custom_middleware(gp_middleware, _gp_inheritable)
```

`_apply_custom_middleware` replaces same-named entries **in place with the passed
instance**.

We hit this on every build. `execution/factory.py:517-534` passes the _same three
classes_ down both lanes:

```python
middleware=(RuntimeControlMiddleware(), ModelInvocationMiddleware(), *_host_path_tool_middleware(...)),
universal_middleware_factories=(RuntimeControlMiddleware, ModelInvocationMiddleware, *_host_path_tool_middleware_factories(...)),
```

So `gp_middleware` already contains freshly-materialized instances with names
`0xCopilotRuntimeControlMiddleware`, `0xCopilotModelInvocationMiddleware`,
`HostPathToolMiddleware` — every one of which matches. On 0.7.1 those fresh
instances are **discarded and replaced by the supervisor's instances**.

That defeats the entire purpose of `_materialize_universal_middleware`
(`deep_agent_builder.py:214-229`), whose docstring says Deep Agents "materializes
harness middleware for declarative children" so each child gets its own. Concrete
damage: `RuntimeControlMiddleware.__init__` builds instance-local
`_fallback_serial_admission`, `_fallback_lifecycle_reducer`, and a mutable
`_final_tool_surface` — all now shared across the supervisor and the GP subagent
graph.

Note the child-count bookkeeping (`_UNIVERSAL_CHILD_GRAPHS_REMAINING`,
`_local_subagent_graph_count`) stays **correct** — `materialize_extra_middleware()`
is still called once per declarative subagent + once for GP + once for main, same
as 0.6.12. The count is right; the GP result is then thrown away.

Declarative subagents are unaffected (their inheritance goes through
`spec["middleware"]`, which we never set).

### 3c. New name-collision semantics

`_apply_custom_middleware` now **replaces a default whose `.name` matches**. Our
three middleware names (`0xCopilot*`, `HostPathToolMiddleware`) do not collide
with any deepagents default, so nothing of ours silently replaces a built-in
today. This is a permanent new footgun for any future middleware we add — name
it distinctly or it will silently displace an upstream default.

### 3d. `wrap_tool_call` / `ToolCallRequest`

The seam itself is unchanged in deepagents. `middleware/_fs_interrupt.py` diff is
**one line**. Risk here is transitive, via the forced `langchain-core` bump (§6),
not via deepagents.

---

## 4. `BackendProtocol` — the `delete` tool is a live data-loss hazard on desktop

### 4a. Protocol API diff (0.6.12 → 0.7.1)

```
BackendProtocol
  - ls_info / als_info / glob_info / aglob_info / grep_raw / agrep_raw   REMOVED
  + delete(self, file_path) / adelete(self, file_path)                   ADDED (optional)
WriteResult / EditResult
  - files_update field and constructor keyword                           REMOVED
ReadResult
  + total_lines, start_line, end_line, next_offset, no_lines_requested
  + __post_init__ that RAISES ValueError on inconsistent pagination fields
GlobResult / GrepResult
  + truncated: bool
GrepMatch
  + context_before / context_after
+ DeleteResult, ContextLine, ExecuteOffloadResult                        NEW
```

Cheap wins first — **these do not affect us**:

- `files_update`: **zero** occurrences anywhere in `services/ai-backend/src`.
- `ls_info` / `glob_info` / `grep_raw`: only a doc-comment mention in
  `capabilities/desktop/host_route.py:32`. No implementations, no callers.
- `StoreBackend` explicit `namespace`: we never construct `StoreBackend`.
- `BackendFactory` for `backend=`: we pass a concrete `CompositeBackend`, not a factory.
- `FilesystemBackend`/`LocalShellBackend` default flip to `virtual_mode=True`:
  we already pass it **explicitly** — `execution/factory.py:2433`,
  `NativeHostPathBackend(FilesystemBackend(virtual_mode=False))`. Unaffected.
- `ReadResult.__post_init__`: our constructions
  (`runtime_adapters/file/{subagent_trace_backend,large_tool_result_backend,agent_state_store}.py`,
  `agent_runtime/context/memory/subagent_trace.py`) pass only `error=` /
  `file_data=`, never the new pagination fields — so the validator never fires.
  They will, however, silently stop reporting pagination the middleware now expects.

### 4b. **BLOCKER — recursive `delete` reaches the real disk with every guard bypassed**

Release note: _"Agents now see a destructive, recursive `delete` filesystem tool
whenever the backend supports it. Filesystem permissions classify `delete` as a
write operation; existing write-permission rules authorize recursively deleting
subtrees."_

Trace it through our actual desktop topology:

1. `_supports_delete(backend)` is `type(backend).delete is not BackendProtocol.delete`.
   The backend we hand to `create_deep_agent` is a `CompositeBackend`
   (`execution/factory.py:2329`), and 0.7.1 `backends/composite.py:713` **defines
   `delete`** — its own docstring says _"`CompositeBackend` always advertises
   delete support … so the `delete` tool is never filtered out for it."_
   → the `delete` tool **is bound to the model surface**.
2. A host-absolute path matches no route, so it lands on the composite DEFAULT,
   which is
   `HostFilesystemFloor(NativeHostPathBackend(FilesystemBackend(virtual_mode=False)))`.
3. `HostFilesystemFloor` (`capabilities/desktop/host_floor.py`) is a **plain class,
   no base**, that guards exactly `read / aread / download_files / adownload_files /
write / awrite / edit / aedit` and delegates everything else via
   `__getattr__`. It does not name `delete`.
4. `NativeHostPathBackend` (`capabilities/desktop/host_tool_paths.py:348`) is also
   a **plain class, no base**, guarding `ls/als/read/aread/glob/aglob/grep/agrep/
write/awrite/edit/aedit` and delegating the rest via `__getattr__`. It does not
   name `delete`.
5. Therefore `composite.delete(path)` → `HostFilesystemFloor.__getattr__("delete")`
   → `NativeHostPathBackend.__getattr__("delete")` → **`FilesystemBackend.delete`
   on the user's real filesystem.**

Everything we built to make host filesystem access safe is skipped on this path:

- `HostFilesystemFloor.permits_read` / `permits_write` never run — the floor's
  granted-roots and scratch checks are simply not consulted.
- `NativeHostPathBackend`'s drive-letter / host-path translation never runs.
- Because the composite's `except NotImplementedError` never fires (the call
  succeeds all the way down), there is no error to observe.

Three of our mirrored contract tables are also stale and would have to gain
`delete`:

| Ours                                                                                                                       | Upstream it mirrors                | Missing                                                                                                                                             |
| -------------------------------------------------------------------------------------------------------------------------- | ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `capabilities/desktop/host_tool_paths.py:131` `HostFsToolArgs._BY_TOOL`                                                    | `_fs_interrupt._FS_TOOL_PATH_ARGS` | `"delete": ("file_path",)` — its explicit purpose is that "a version skew shows up as a failing contract test"                                      |
| `runtime_worker/stream_events.py:133` `TOOL_OPERATIONS` and `_PATH_ARGS`                                                   | deepagents built-in fs tools       | `delete → write`, `delete → file_path`. Without it a `delete` interrupt renders **no filesystem approval card** and falls through to the MCP branch |
| `capabilities/mcp/per_tool_registration.py:141` `ReservedToolNames.NAMES` (+ `capabilities/operations/conformance.py:214`) | framework-owned tool names         | `"delete"` — otherwise an MCP connector tool named `delete` shadows the built-in                                                                    |

Upstream also changed the gate itself: _"Deny and interrupt checks use bulk path
overlap instead of exact-path matching"_ — `_FS_TOOL_PATH_ARGS` classifies
`delete` as `("write", "file_path", "bulk", None)`.

**Mitigation is cheap and must land in the same commit as any version bump.**
`execution/tool_surface.py:43` currently reads:

```python
DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES = frozenset({"execute"})
```

Adding `"delete"` strips it from the supervisor, every declarative subagent and
the GP subagent (the profile's `excluded_tools` is applied on all three lanes),
and in 0.7.1 `_ToolExclusionMiddleware` runs **last**, so nothing can restore it.
~1 hour. Wiring `delete` through the floor, path translation, approval projection
and the reserved-name tables properly is a separate ~3-4 day piece of work.

---

## 5. `TodoListMiddleware` removal breaks a shipped UI surface

Release note: _"`create_deep_agent` no longer includes `TodoListMiddleware` by
default; the `write_todos` tool, `todos` state channel, and todo-planning prompt
are absent."_

We have a whole feature built on it:

- `agent_runtime/capabilities/todo_list.py` — resolves `write_todos` calls into
  checklist snapshots, keyed per agent lane (parent plan vs child plan are
  separate) precisely because 0.6.12 gave each subagent its own `TodoListMiddleware`.
- `runtime_worker/stream_tools.py:126,382` — publishes the checklist at the
  `write_todos` tool-result seam.
- `runtime_api/schemas/common.py:166` and `schemas/events.py:615` — the `todos`
  field and the internal-frame routing.
- `capabilities/mcp/per_tool_registration.py:143` and
  `capabilities/operations/conformance.py:214` — `write_todos` reserved as
  framework-owned.

On 0.7.x the tool disappears, the snapshots stop, and **the plan panel silently
goes empty**. Restoring it is not a one-liner: to keep per-lane checklists it has
to be added to _both_ `middleware=` (root) and `universal_middleware_factories`
(children), and 0.7.1 splices it after the core stack rather than at position 0.

Related prompt change with a behavioural blast radius: `BASE_AGENT_PROMPT` is
deprecated and `BASE` is now `""`. Every agent loses ~40 lines of upstream
core-behaviour prose that currently sits after our `system_prompt`. Our
`system_prompt_suffix` still lands. Restoring the old text means setting
`HarnessProfile.base_system_prompt`; leaving it lean is defensible but is a
behaviour change that needs an eval pass, not a code review.

---

## 6. Transitive dependency bumps

`deepagents==0.7.1` requires:

| Dep                      | Required by 0.7.1 | We pin       | Action                                                                                              |
| ------------------------ | ----------------- | ------------ | --------------------------------------------------------------------------------------------------- |
| `langchain`              | `>=1.3.14,<2.0.0` | `1.3.14`     | satisfied                                                                                           |
| `langchain-core`         | `>=1.5.0,<2.0.0`  | **`1.4.9`**  | bump (latest 1.5.3; our `langchain==1.3.14` allows `>=1.4.9,<2.0.0`, so no forced `langchain` bump) |
| `langchain-anthropic`    | `>=1.5.3,<2.0.0`  | **`1.4.8`**  | bump                                                                                                |
| `langchain-google-genai` | `>=4.3.1,<5.0.0`  | **`4.2.7`**  | bump                                                                                                |
| `langsmith`              | `>=0.10.9`        | **`0.10.5`** | bump                                                                                                |
| `wcmatch`                | `>=11.0`          | `11.0`       | satisfied — good, our host-path floor depends on wcmatch `BRACE \| GLOBSTAR` semantics              |
| `packaging`              | `>=23.2`          | present      | satisfied                                                                                           |

Four pins move in `services/ai-backend/requirements.txt` (hash-pinned, so it needs
a `pip-compile` regen) and `pyproject.toml`. The `langchain-core` 1.4.9 → 1.5.x
minor is its own risk surface for the approval GATE, since
`HumanInTheLoopMiddleware`, `InterruptOnConfig` and `ToolCallRequest` all live in
`langchain`, not deepagents.

---

## 7. Smaller behavioural changes worth a look

- **`write_file` now creates missing files and replaces existing ones** instead of
  erroring; the description no longer tells the model to read first. There is no
  create-only compatibility mode. A silent full-file clobber is now one tool call.
  Writes are still gated by our interrupt rules, but the approval card copy
  ("write to X") now covers destroy-and-replace.
- **`read_file` render changed** — no fixed-width `cat -n` gutter, `LINE_NUMBER_WIDTH`
  removed, dynamic alignment, two-space separator, plus pagination metadata.
  Anything of ours that parses or snapshots read output needs re-checking.
- **`ls` / `glob` empty results render as `"No files found"`**, not `[]`.
- `SummarizationMiddleware(history_path_prefix=...)` now raises `TypeError` — we
  do not pass it.
- New in 0.7.0 and genuinely useful later: `FilesystemMiddleware(tools=[...])`
  allowlist (typed `FsToolName`), `grep_max_count`, `truncated` flags on
  `GrepResult`/`GlobResult`, `glob` brace expansion.

---

## 8. Stale packaging metadata (flagged, unrelated to the upgrade)

Three different deepagents versions are visible in this checkout:

| Where                                                                            | Version                                |
| -------------------------------------------------------------------------------- | -------------------------------------- |
| `services/ai-backend/requirements.txt:18`                                        | `deepagents==0.6.12`                   |
| `services/ai-backend/pyproject.toml:11`                                          | `deepagents==0.6.12`                   |
| `services/ai-backend/.venv/.../deepagents-0.6.12.dist-info` (actually installed) | **0.6.12** — correct                   |
| `services/ai-backend/.venv/.../agent_runtime-0.1.0.dist-info/METADATA:6`         | **`Requires-Dist: deepagents==0.5.5`** |
| repo-root `/.venv/.../deepagents-0.5.5.dist-info`                                | **0.5.5**                              |

Two separate staleness bugs:

1. **`agent_runtime-0.1.0.dist-info/METADATA` still declares `deepagents==0.5.5`.**
   This is stale editable-install metadata: the `.dist-info` was written when
   `pyproject.toml` pinned 0.5.5 and was never regenerated after the bumps to
   0.6.x. The _runtime_ is fine — the editable path import wins and the real 0.6.12
   is installed — but any tool that resolves declared dependencies (`pip check`,
   `pip-audit`, an SBOM generator, a resolver run) reads `0.5.5` and can either
   report a false conflict or silently downgrade. Fix: `pip install -e .
--no-deps --force-reinstall` in `services/ai-backend`.
2. **The repo-root `/.venv` has `deepagents 0.5.5` + `langchain 1.2.16` +
   `langgraph 1.1.10`** — a whole generation behind the service venv. It violates
   the "each Python service owns its own `.venv`" rule in `CLAUDE.md`. Anything
   accidentally run from the root venv exercises 0.5.5, not what we ship.

Neither blocks the upgrade; both are landmines for whoever _does_ the upgrade,
because a resolver reading `0.5.5` while the pin says `0.7.1` produces confusing
failures.

---

## 9. Recommendation and effort

### Do not upgrade now

- **Zero pull.** The only 0.7 feature that would have changed our roadmap —
  lazy/progressive/semantic tool selection — did not ship, and its live successor
  (issue 4658) is open with no implementation. Nothing in 0.7.1 fixes a bug we
  are hitting.
- **Real push-back cost.** A blind `pip install -U deepagents` on the desktop
  build hands the agent a recursive `delete` over the user's home directory with
  the host filesystem floor bypassed (§4b), empties the plan panel (§5), and
  makes the general-purpose subagent share the supervisor's middleware instances
  (§3b). Our 751-file unit suite will not catch §4b — the in-memory backends used
  in tests do not implement `delete`, so `_supports_delete` is `False` there and
  the tool is never even bound. This is the same in-memory-adapter blind spot that
  has bitten us before.

### Revisit trigger

Upgrade when **either**:

- issue 4658 `ToolSelectionMiddleware` ships (then the calculus flips — that is
  the piece worth taking a minor for); **or**
- we want `FilesystemMiddleware(tools=[...])` / `grep_max_count` / `truncated`
  flags for the tool-budget work, in which case do it as a planned, scoped upgrade.

### Effort when we do it: **6-9 engineer-days**

| #   | Work                                                                                                                                                                                               | Days    |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------- |
| 1   | Add `"delete"` to `DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES` — **must ship in the same commit as the bump**                                                                                          | 0.1     |
| 2   | Bump 4 transitive pins + regen hash-pinned `requirements.txt`; verify `langchain-core` 1.5 against the HITL/interrupt path                                                                         | 0.5     |
| 3   | Restore `TodoListMiddleware` on all three lanes (root `middleware=`, `universal_middleware_factories`, and verify subagent splice position); keep per-lane checklist keying                        | 1.0     |
| 4   | Fix the GP-subagent instance-sharing regression (distinct names per lane, or stop double-passing the same classes) + a test asserting supervisor and GP hold distinct instances                    | 1.0     |
| 5   | Update the mirrored contract tables (`HostFsToolArgs._BY_TOOL`, `stream_events.TOOL_OPERATIONS`/`_PATH_ARGS`, `ReservedToolNames.NAMES`, `conformance.py`) for `delete` even though it is excluded | 0.5     |
| 6   | `read_file` / `ls` / `glob` output-shape fallout: parsers and snapshot tests                                                                                                                       | 0.5     |
| 7   | Prompt behaviour: decide lean-`BASE` vs restoring the legacy text via `base_system_prompt`; eval pass                                                                                              | 1.0-2.0 |
| 8   | Run the 45 deepagents-coupled test files + full 751-file suite + the live packaged desktop journeys in `tools/desktop-journeys/` (a live run is the only thing that would have caught §4b)         | 1.0     |
| 9   | Fix the stale `agent_runtime` dist-info + root-`.venv` drift (§8) — do this **first**, it is a prerequisite for a clean resolve                                                                    | 0.3     |

Optional follow-on, not required for the upgrade: implement `delete` properly
through `HostFilesystemFloor` + `NativeHostPathBackend` + approval projection so
the desktop can offer a _gated_ delete rather than no delete — **+3-4 days**.

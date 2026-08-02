# deepagents 0.6.12 → 0.7.1 upgrade evaluation

**Verdict: DEFER. Do not upgrade now.** Revisit when issue 4658
(`ToolSelectionMiddleware`) ships, or when we specifically want
`FilesystemMiddleware(tools=[...])` for the tool-budget work.

Two independent passes:

- **2026-08-02** — first pass. Release notes, GitHub issue/timeline API, file-level
  diff of the modules we integrate against.
- **2026-08-03** — second pass. Re-derived the breakage surface from our own import
  graph (AST, not grep-by-eye), then checked each coupling against **verbatim 0.7.1
  source** fetched at tag `deepagents==0.7.1`. This pass **corrected two claims from
  the first pass and found two additional blockers.** Corrections are marked
  ⚠ **CORRECTED**; new findings are marked ★ **NEW**.

The compare is **672 commits / 300+ files**. This is a real minor, not a patch.

---

## 0. Provenance — what is verified, and how

Everything below is tagged so a reader knows what to re-check before acting.

| Tag         | Meaning                                                                              |
| ----------- | ------------------------------------------------------------------------------------ |
| **[V]**     | Verified 2026-08-03 against verbatim 0.7.1 source or PyPI release metadata           |
| **[V-0.6]** | Verified against the installed 0.6.12 in the service `.venv` (the baseline half)     |
| **[R]**     | Verified against our own repo source in this checkout                                |
| **[P1]**    | From the 2026-08-02 pass, **not** re-verified on 2026-08-03 — re-check before acting |
| **[?]**     | Could not determine — see §11                                                        |

Upstream source was read at
`raw.githubusercontent.com/langchain-ai/deepagents/deepagents==0.7.1/libs/deepagents/deepagents/…`.
Release metadata from `pypi.org/pypi/deepagents/0.7.1/json`.

Nothing in this evaluation installed 0.7.1. **No 0.7.1 code has ever been executed
against this repo.** Every "this breaks" below is a source-level derivation, and the
migration checklist in §13 is written so the first step is to make the machine prove
it rather than trust this document.

---

## 1. The thing that would have justified upgrading did NOT land

**Answer: no. 0.7.x does not replace the work we are about to build. Keep building.**

**[V]** `deepagents/middleware/__init__.py` at 0.7.1 exports a 25-name `__all__` that
is **identical to 0.6.12's**. Zero new public middleware. Nothing named
`ToolSelectionMiddleware`, `SemanticToolSelectionMiddleware`, or any
progressive-disclosure equivalent exists.

**[V]** The 0.7.1 changelog has no tool-selection entry. The only new tool-visibility
lever in the whole minor is `FilesystemMiddleware(tools=[...])` — a static,
construction-time allowlist over the eight **built-in filesystem** tools
(`FsToolName = Literal["ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute"]`).
It cannot see MCP tools at all.

**[P1]** Issue state as of 2026-08-02:

| Upstream item                                                                      | State                                                                        |
| ---------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| Issue **616** — lazy / progressive disclosure of MCP tools based on selected Skill | OPEN. Labels `feature, MCP, deepagents, external`. Last activity 2026-07-17. |
| Issue **3672** — `SemanticToolSelectionMiddleware`                                 | Closed 2026-05-31, zero comments, no linked PR. Superseded by ↓              |
| Issue **4658** — `Add ToolSelectionMiddleware for per-turn tool filtering`         | OPEN, filed 2026-07-11, cross-referenced from 3672. No implementation.       |

Issue 4658's own text states the problem in our exact terms: the middleware stack
"assembles the tool list once at agent construction … and it stays fixed for the life
of the session", and `_ToolExclusionMiddleware` "can drop tools, but only statically".
That is upstream confirming the gap our program exists to close.

**Implication for the MCP tooling program: proceed. There is no upstream duplication
risk in the 0.7 line.**

---

## 2. ★ NEW — The breakage surface: every place we reach into deepagents internals

This is the section the first pass was missing. Derived by AST-walking every
`import`/`from … import` in `services/ai-backend/src`, not by reading prose.

**[R] 31 deepagents import statements across 19 source files. Exactly ONE of them
imports from the top-level public `deepagents` package.** The other 30 reach into
submodules. Two of those statements pull underscore-prefixed names
(`factory.py:2193`, `atlas_task_tool.py:51`), and a third site
(`atlas_task_tool.py:423`) imports a public module solely to overwrite a private
attribute on it.

That ratio is the actual finding. Our coupling to deepagents is essentially
all-internal, so upstream's semver tells us almost nothing about our blast radius —
which is why an upgrade needs this table rather than a changelog read.

Counts reproduced with:

```bash
cd services/ai-backend && python3 -c "$(cat <<'PY'
import ast, pathlib
files=set(); stmts=0; toplevel=0
for p in sorted(pathlib.Path('src').rglob('*.py')):
    for n in ast.walk(ast.parse(p.read_text())):
        mod = (n.module if isinstance(n, ast.ImportFrom) and n.module else
               next((a.name for a in getattr(n, 'names', []) if isinstance(n, ast.Import)
                     and a.name.startswith('deepagents')), None))
        if not mod or not mod.startswith('deepagents'):
            continue
        files.add(str(p)); stmts += 1; toplevel += (mod == 'deepagents')
print(len(files), stmts, toplevel)   # -> 19 31 1
PY
)"
```

### 2a. Tier 1 — public API (safe, semver applies)

| Site                                 | Symbols                                                           | 0.7.1                                                                                                                                            |
| ------------------------------------ | ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `execution/deep_agent_builder.py:10` | `HarnessProfile`, `create_deep_agent`, `register_harness_profile` | **[V] Unchanged.** `create_deep_agent`'s 19-parameter signature is byte-identical; `HarnessProfile`'s 7 fields are unchanged (§3). No migration. |

### 2b. Tier 2 — submodule imports of names that are exported by `deepagents.backends.__init__`

Public-ish: they appear in `deepagents/backends/__all__`, but we import them by module
path, so an internal file move breaks us even when the re-export survives.

| Site                                              | Symbol              | 0.7.1                                                                                                                         |
| ------------------------------------------------- | ------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `execution/factory.py:820,2512`                   | `CompositeBackend`  | **[V] Present, and gains `delete`** — this is the §5 blocker.                                                                 |
| `execution/factory.py:838,2589`                   | `StateBackend`      | **[V] Present, and gains `delete`** — this is why the unit suite goes red.                                                    |
| `execution/factory.py:2588`                       | `FilesystemBackend` | **[P1] Present; `virtual_mode` default flips to `True`.** We pass it explicitly at `factory.py:2433`, so we are unaffected.   |
| `capabilities/sandbox/providers/langsmith.py:149` | `LangSmithSandbox`  | **[?]** Not checked. Lazy import inside a function, so a rename fails at call time, not import time — the worst failure mode. |

### 2c. Tier 3 — submodule imports of names in NO `__all__` (private-by-omission)

These are the ones semver does not protect. `deepagents.backends.protocol` re-exports
only `BackendProtocol` through `backends/__init__`; every other name below is reached
by module path only.

| Site                                                                                                                                                                                                                                                                                                                                                                           | Symbols                                                                                                                                                                                | 0.7.1                                                                                                                                                                                         |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 12 files: `capabilities/backends/{artifact_draft,draft}_backend.py`, `capabilities/desktop/{host_floor,host_route,host_tool_paths,workspace_backend}.py`, `capabilities/mcp/catalog_backend.py`, `capabilities/workspace/deep_backend.py`, `context/memory/subagent_trace.py`, `runtime_adapters/file/{agent_state_store,large_tool_result_backend,subagent_trace_backend}.py` | `BackendProtocol`, `ReadResult`, `WriteResult`, `EditResult`, `LsResult`, `GlobResult`, `GrepResult`, `GrepMatch`, `FileInfo`, `FileData`, `FileDownloadResponse`, `PERMISSION_DENIED` | **[V] All still exist. Field lists grew (§5a).** All new fields are **appended**, and **[R]** every construction of these in `src/` is keyword-only — so no positional-construction breakage. |
| `capabilities/sandbox/{policy_backend,ports}.py`, `capabilities/sandbox/providers/openai_hosted.py`                                                                                                                                                                                                                                                                            | `SandboxBackendProtocol`, `ExecuteResponse`, `FileUploadResponse`                                                                                                                      | **[V]** All present. `ExecuteResponse` gains `truncated`; `ExecuteOffloadResult` is new.                                                                                                      |
| `capabilities/sandbox/policy_backend.py:34`, `providers/openai_hosted.py:32`                                                                                                                                                                                                                                                                                                   | `BaseSandbox` (we **subclass** it)                                                                                                                                                     | **★ [V] CHANGED — gains a concrete `rm -rf` `delete`. This is the §6 blocker.**                                                                                                               |
| `capabilities/mcp/catalog_backend.py:51`                                                                                                                                                                                                                                                                                                                                       | `slice_read_response`, `grep_matches_from_files`                                                                                                                                       | **★ [V] `slice_read_response`'s RETURN TYPE CHANGED. See §7.**                                                                                                                                |

### 2d. Tier 4 — genuinely private names (leading underscore) and a monkey-patch

The hardest coupling. Upstream owes us nothing here.

| Site                                          | Symbol                                                                                                    | 0.6.12 → 0.7.1                                                                                                                                                                                                                                                           |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `execution/factory.py:2193`                   | `deepagents.middleware._fs_interrupt._FS_TOOL_PATH_ARGS`                                                  | **[V] One new entry: `"delete": ("write", "file_path", "bulk", None)`.** The other 6 entries are byte-identical.                                                                                                                                                         |
| `execution/factory.py:2193`                   | `_fs_interrupt._build_interrupt_on_from_permissions`                                                      | **[V] Signature identical** — `(rules: list[FilesystemPermission]) -> dict[str, InterruptOnConfig]`.                                                                                                                                                                     |
| `execution/factory.py:2124,2842`              | `deepagents.middleware.filesystem.FilesystemPermission`                                                   | **[V] Identical** — 3 fields (`operations`, `paths`, `mode`), same `__post_init__` rules. (Also public via top-level `deepagents`.)                                                                                                                                      |
| `execution/factory.py:2267`                   | `deepagents.middleware.filesystem.validate_path`                                                          | **[V] Still importable, but it is a RE-EXPORT** — defined in `backends/utils.py`, pulled into `filesystem.py`. Signature unchanged. Fragile by construction: an upstream import-cleanup removes it without any changelog entry. Import it from `backends.utils` instead. |
| `delegation/subagents/atlas_task_tool.py:51`  | `middleware.subagents._EXCLUDED_STATE_KEYS`, `_get_subagent_response_format`, `_subagent_tracing_context` | **[V] All three unchanged.** `_EXCLUDED_STATE_KEYS` is still `{"messages", "todos", "structured_response"}` — note it still names `todos` even though 0.7.0 removed the todo channel from the default stack.                                                             |
| `delegation/subagents/atlas_task_tool.py:51`  | `TASK_TOOL_DESCRIPTION`, `TaskToolSchema`, `CompiledSubAgent`, `SubAgent`, `create_sub_agent`             | **[V] All present; `create_sub_agent`'s signature is identical.** `TASK_TOOL_DESCRIPTION`'s **text** changed (0.7.0 "shortened descriptions for agent-facing tools"). Prompt-affecting, not import-breaking.                                                             |
| `delegation/subagents/atlas_task_tool.py:428` | **monkey-patch** `_ds._build_task_tool = build_atlas_task_tool`                                           | **[?]** `_build_task_tool` still exists (the drift test pins its parameter contract), but I did not diff its 0.7.1 parameter list. **This is our single most fragile coupling** — a silent no-op if upstream renames it, since the patch is unconditional.               |

### 2e. Also mirrored, not imported

Three tables in our code are hand-copies of upstream data. They do not break on
import; they go **silently stale**, which is worse.

| Ours                                                                                                                               | Mirrors                            | 0.7.1 delta                                                                      |
| ---------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------- | -------------------------------------------------------------------------------- |
| **[R]** `capabilities/desktop/host_tool_paths.py:131` `HostFsToolArgs._BY_TOOL`                                                    | `_fs_interrupt._FS_TOOL_PATH_ARGS` | needs `"delete": ("file_path",)`                                                 |
| **[R]** `runtime_worker/stream_events.py:133` `TOOL_OPERATIONS` / `_PATH_ARGS`                                                     | deepagents built-in fs tools       | needs `delete → write` and `delete → file_path`                                  |
| **[R]** `capabilities/mcp/per_tool_registration.py:141` `ReservedToolNames.NAMES` (+ `capabilities/operations/conformance.py:214`) | framework-owned tool names         | needs `"delete"`, else an MCP connector tool named `delete` shadows the built-in |

`host_tool_paths.py:119`'s own comment states the intent: "a version skew shows up as
a failing contract test". That is the right design — §10 checks whether it actually
fires.

---

## 3. `HarnessProfile` and `create_deep_agent` — no signature changes

**[V]** `create_deep_agent`'s parameters at 0.7.1, verbatim, are unchanged from
0.6.12: `model`, `tools`, then keyword-only `system_prompt`, `middleware`,
`subagents`, `skills`, `memory`, `permissions`, `backend`, `interrupt_on`,
`response_format`, `state_schema`, `context_schema`, `checkpointer`, `store`, `debug`,
`name`, `cache`.

**[P1]** `HarnessProfile`'s field list is likewise identical:

```
base_system_prompt, system_prompt_suffix, tool_description_overrides,
excluded_tools, excluded_middleware, extra_middleware, general_purpose_subagent
```

`deep_agent_builder.py:241` constructs `HarnessProfile(system_prompt_suffix=…,
excluded_tools=…, extra_middleware=…)`. **No migration needed for the profile
construction itself.**

One _semantic_ change reaches `base_system_prompt` (§8): `BASE` is now the empty
string, so `base_system_prompt` goes from "replaces the SDK's authored prompt" to "is
the only authored prompt".

---

## 4. Middleware seam — one ordering move, one silent instance-sharing regression

### 4a. Final middleware order

**[V]** The main lane's post-population mutations at 0.7.1, in order:

```python
_main_core_names = {m.name for m in deepagent_middleware}
deepagent_middleware = _apply_excluded_middleware(deepagent_middleware, _profile, …)
deepagent_middleware = _apply_custom_middleware(
    deepagent_middleware, middleware or [], core_names=_main_core_names)
deepagent_middleware = _apply_excluded_middleware(deepagent_middleware, _profile, …)
if _profile.excluded_tools:
    deepagent_middleware.append(_ToolExclusionMiddleware(excluded=_profile.excluded_tools))
```

`_ToolExclusionMiddleware` therefore ends up **last** (it was ~position 10 in 0.6.12).
It is now the innermost `wrap_model_call`, deliberately — so excluded tool names are
stripped last and cannot be restored by a custom `wrap_model_call`. **That is a
hardening in our favour.** It stays inner relative to `RuntimeControlMiddleware`, so
`RuntimeToolSurfaceSnapshot.from_tools`
(`capabilities/middleware/runtime_tool_control.py:112`) still observes the same
pre-exclusion list. No change to the canary.

**[V]** Our middleware keeps its relative position. `_apply_custom_middleware`'s
docstring is explicit: a brand-new entry "lands after the last `core_names` member (so
it precedes the profile/prompt-caching/memory tail)" — the same slot the old
`extend(middleware)` produced. **[?]** I could not see the exact line at which
`_main_core_names` is captured, so this rests on the docstring rather than on the
line order; §13 step 3 makes the machine assert it.

Our `MIDDLEWARE_ORDER`-asserting MCP stack (`capabilities/mcp/middleware/compose.py`)
is per-tool wrapping and is unaffected either way.

### 4b. REGRESSION — the general-purpose subagent silently shares the supervisor's middleware instances

Not in the release notes. **[V]** 0.7.1 `graph.py` adds to the GP-subagent build:

```python
_gp_original_name_to_index = {m.name: i for i, m in enumerate(gp_middleware)}
gp_middleware = _apply_excluded_middleware(gp_middleware, _profile, …)
_gp_inheritable = [m for m in (middleware or []) if m.name in _gp_original_name_to_index]
gp_middleware = _apply_custom_middleware(gp_middleware, _gp_inheritable)
```

**[V]** `_apply_custom_middleware` replaces a same-named entry **in place with the
passed instance**:

```python
for i, m in enumerate(result):
    if m.name in replacements:
        result[i] = replacements[m.name]
```

**[R]** We hit this on every build. `execution/factory.py:517-534` passes the same
three classes down both lanes:

```python
middleware=(RuntimeControlMiddleware(), ModelInvocationMiddleware(), *_host_path_tool_middleware(...)),
universal_middleware_factories=(RuntimeControlMiddleware, ModelInvocationMiddleware, *_host_path_tool_middleware_factories(...)),
```

So `gp_middleware` already holds freshly-materialized instances named
`0xCopilotRuntimeControlMiddleware`, `0xCopilotModelInvocationMiddleware`,
`HostPathToolMiddleware` — every one of which matches a `_gp_inheritable` entry. On
0.7.1 those fresh instances are **discarded and replaced by the supervisor's**.

That defeats `_materialize_universal_middleware`
(`deep_agent_builder.py:214-229`), whose whole job is giving each child its own.
Concrete damage: `RuntimeControlMiddleware.__init__` builds instance-local
`_fallback_serial_admission`, `_fallback_lifecycle_reducer`, and a mutable
`_final_tool_surface` — all now shared between supervisor and GP subagent.

The child-count bookkeeping (`_UNIVERSAL_CHILD_GRAPHS_REMAINING`,
`_local_subagent_graph_count`) stays **correct** — `materialize_extra_middleware()` is
still called once per declarative subagent + once for GP + once for main. The count is
right; the GP result is then thrown away.

Declarative subagents are unaffected (their inheritance goes through
`spec["middleware"]`, which we never set).

**We already own the test that would catch this**:
`test_deep_agent_builder.py::test_universal_middleware_is_materialized_for_supervisor_and_local_subagents`
asserts exactly one `RuntimeControlMiddleware` instance per captured stack. See §10 —
it is currently order-fragile, which must be fixed _before_ it can serve as the gate.

### 4c. New name-collision semantics

`_apply_custom_middleware` now **replaces a default whose `.name` matches**. **[R]**
Our three names (`0xCopilot*`, `HostPathToolMiddleware`) collide with no deepagents
default, so nothing of ours silently displaces a built-in today. This is a permanent
new footgun for any future middleware: name it distinctly or it silently replaces an
upstream default.

---

## 5. `BackendProtocol` — the `delete` tool is a live data-loss hazard on desktop

### 5a. Protocol API diff **[V]**

```
BackendProtocol
  - ls_info / als_info / glob_info / aglob_info / grep_raw / agrep_raw   REMOVED
  + delete(self, file_path) / adelete(self, file_path)                   ADDED (optional)
  + _supports_delete(backend) helper                                     ADDED
WriteResult / EditResult
  - files_update field and constructor keyword                           REMOVED
ReadResult
  + total_lines, start_line, end_line, next_offset, no_lines_requested
  + __post_init__ that RAISES ValueError on inconsistent pagination fields
GlobResult / GrepResult   + truncated: bool
GrepMatch                 + context_before / context_after
ExecuteResponse           + truncated
+ DeleteResult, ContextLine, ExecuteOffloadResult                        NEW
```

Cheap wins first — **these do not affect us**:

- **[R]** `files_update`: zero occurrences anywhere in `services/ai-backend/src`.
- **[R]** `ls_info` / `glob_info` / `grep_raw`: only a doc-comment mention in
  `capabilities/desktop/host_route.py:32`. No implementations, no callers.
- **[R]** All new dataclass fields are **appended**, and every construction of these
  types in `src/` is keyword-only. No positional breakage.
- **[R]** `ReadResult.__post_init__`: our constructions pass only `error=` /
  `file_data=`, never the new pagination fields, so the validator never fires. They
  will, however, silently stop reporting the pagination the middleware now expects.
- **[P1]** `StoreBackend` explicit `namespace`: we never construct `StoreBackend`.
- **[P1]** `BackendFactory` for `backend=`: we pass a concrete `CompositeBackend`.

### 5b. BLOCKER 1 — recursive `delete` reaches the real disk with every guard bypassed

Release note **[V]**: _"Agents now see a destructive, recursive `delete` filesystem
tool whenever the backend supports it. Filesystem permissions classify `delete` as a
write operation; existing write-permission rules authorize recursively deleting
subtrees."_

Traced through our actual desktop topology, with every step now verified:

1. **[V]** The gate is `_supports_delete`, in `backends/protocol.py`:
   ```python
   def _supports_delete(backend: BackendProtocol) -> bool:
       return type(backend).delete is not BackendProtocol.delete
   ```
2. **[R]** The backend we hand `create_deep_agent` is a `CompositeBackend`
   (`execution/factory.py:2522`). **[V]** 0.7.1 `backends/composite.py` defines
   `delete`, and its docstring says so outright: _"`CompositeBackend` always
   advertises delete support (it overrides this method), so the `delete` tool is never
   filtered out for it."_ → **the `delete` tool is bound to the model surface.**
3. **[R]** A host-absolute path matches no route, so it lands on the composite
   DEFAULT — `HostFilesystemFloor(NativeHostPathBackend(FilesystemBackend(virtual_mode=False)))`.
   `factory.py:2510`'s own comment confirms: "A host-absolute path is not a prefix of
   anything, so it can never be a route: it lands on the DEFAULT."
4. **[R]** `HostFilesystemFloor` (`host_floor.py:113`) is a **plain class, no base**,
   with a `__getattr__` at :166. It guards `read/aread/download_files/adownload_files/
write/awrite/edit/aedit`. It does **not** name `delete`.
5. **[R]** `NativeHostPathBackend` (`host_tool_paths.py:348`) is also a **plain class,
   no base**, with a `__getattr__` at :388. It guards `ls/als/read/aread/glob/aglob/
grep/agrep/write/awrite/edit/aedit`. It does **not** name `delete`.
6. Therefore `composite.delete(path)` → `HostFilesystemFloor.__getattr__("delete")` →
   `NativeHostPathBackend.__getattr__("delete")` → **`FilesystemBackend.delete` on the
   user's real filesystem.**

Everything we built to make host filesystem access safe is skipped:
`HostFilesystemFloor.permits_read`/`permits_write` never run; `NativeHostPathBackend`'s
drive-letter / host-path translation never runs.

**[V]** And the one thing that might have saved us does not fire. `CompositeBackend.delete`
does have a guard:

```python
try:
    res = backend.delete(stripped_key)
except NotImplementedError:
    return DeleteResult(error=_DELETE_UNSUPPORTED_ERROR.format(file_path=file_path))
```

But `FilesystemBackend` **implements** `delete`, so no `NotImplementedError` is raised.
The call succeeds all the way down to disk, and there is no error to observe.

---

## 6. ★ NEW — BLOCKER 2: `BaseSandbox` grows an `rm -rf` that skips our path guard

Missed by the first pass, and it is a second, independent delete path.

**[R]** `capabilities/sandbox/policy_backend.py:69` defines
`PolicyEnforcedSandboxBackend(BaseSandbox)`. It explicitly overrides `ls`, `read`,
`write`, `edit`, `grep`, `glob` — each calling `self._guard_path(path)` (:200), the
`/workspace` containment check. Its module docstring states the contract:

> rejects filesystem paths that leave `/workspace` (defense in depth …)
> Subclassing `BaseSandbox` means every filesystem operation (ls/read/write/edit/grep/glob)
> is derived from the single policy-wrapped `execute` … so the budget and path guards
> apply uniformly without re-implementing each fs method.

**[V]** At 0.7.1 `BaseSandbox` gains a **concrete** `delete`:

```python
def delete(self, file_path: str) -> DeleteResult:
    quoted = shlex.quote(file_path)
    exists = self.execute(f"test -e {quoted} || test -L {quoted}")
    ...
    result = self.execute(f"rm -rf {quoted}")
```

Consequences:

- **The docstring's enumeration is now false.** There is a 7th filesystem operation,
  `PolicyEnforcedSandboxBackend` does not override it, and `_guard_path` is therefore
  **not applied to `delete`**. `delete("/etc")` shells `rm -rf /etc` inside the sandbox.
- `_supports_delete(PolicyEnforcedSandboxBackend(...))` is **True**, so the `delete`
  tool binds in the sandbox lane too — not only the desktop lane.
- Same for `OpenAIHostedContainerBackend(BaseSandbox)` (`providers/openai_hosted.py:143`).

**Severity is lower than §5b** — the blast radius is the sandbox container, not the
user's home directory — but the class's stated invariant is silently broken, and the
"defense in depth" comment becomes a lie. Both blockers are closed by the same
one-line mitigation below.

### The mitigation for both blockers is cheap, and must land in the same commit as the bump

**[R]** `execution/tool_surface.py:43` currently reads:

```python
DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES = frozenset({"execute"})
```

Adding `"delete"` strips it from the supervisor, every declarative subagent and the GP
subagent (the profile's `excluded_tools` applies on all three lanes), and **[V]** in
0.7.1 `_ToolExclusionMiddleware` runs **last**, so nothing can restore it. ~1 hour.

**[V]** 0.7.1 also offers a second, more precise lever the first pass did not note:
`FilesystemMiddleware(tools=[...])` takes a `FsToolName` allowlist. Excluding by name
removes the tool from the model surface; the allowlist prevents it ever being
constructed. Prefer the allowlist if we are already touching the middleware
construction — but `excluded_tools` is the smaller diff and is enough.

Wiring `delete` through the floor, path translation, approval projection and the
reserved-name tables **properly** is a separate ~3-4 day piece of work.

---

## 7. ★ NEW — `slice_read_response`'s return type changed under us

Not in the changelog as a breaking change; found only by diffing the signature.

**[V-0.6]** 0.6.12: `def slice_read_response(file_data, offset, limit) -> str | ReadResult`
**[V]** 0.7.1: `def slice_read_response(file_data, offset, limit) -> ReadResult`

**[R]** `capabilities/mcp/catalog_backend.py:373` branches on exactly that union:

```python
sliced = slice_read_response(file_data, offset, limit)
if isinstance(sliced, ReadResult):
    return sliced
return ReadResult(file_data=FileData(content=sliced, encoding=Values.ENCODING))
```

**[V-0.6]** `ReadResult` is a `@dataclass`, so `isinstance` is legal and this does not
crash. What happens instead is quieter and worse to debug:

- the `isinstance` becomes **always true**, so the final line is **dead code**;
- upstream returns `_copy_file_data_with_content(file_data, …)`, which preserves the
  original encoding — so **our `encoding=Values.ENCODING` normalization is silently
  dropped** for MCP catalog reads;
- the returned `ReadResult` now carries pagination (`total_lines`, `start_line`,
  `end_line`, `next_offset`), which our callers do not expect.

A type-checker would flag the dead branch; a test asserting the returned `encoding`
would flag the behaviour. **[?]** I did not check whether such a test exists.

Also **[V]**: `grep_matches_from_files` gained a keyword-only `max_count: int | None = None`.
**[R]** We call it with four positional args (`catalog_backend.py:381`), so we are
compatible — no action.

---

## 8. ⚠ CORRECTED — `TodoListMiddleware` is a LangChain class, not a deepagents one

The first pass implied the class disappears. It does not.

**[R]** `tests/unit/architecture/test_model_visible_operation_inventory.py:11` and
`capabilities/operations/conformance.py:214` both name the real owner:

```python
from langchain.agents.middleware import TodoListMiddleware
```

**What 0.7.0 removes is deepagents' automatic inclusion of it in `create_deep_agent`**
— the `write_todos` tool, the `todos` state channel, and the todo-planning prompt are
absent from the default stack. The class itself stays importable from `langchain`,
which we already pin and are not bumping.

So the fix is a re-add of an available middleware, not a reimplementation. It is still
not a one-liner, because we need **per-lane** checklists:

- **[R]** `capabilities/todo_list.py` resolves `write_todos` calls into checklist
  snapshots keyed per agent lane (parent plan vs child plan are separate) precisely
  because 0.6.12 gave each subagent its own `TodoListMiddleware`.
- **[R]** `runtime_worker/stream_tools.py:126,382` publishes the checklist at the
  `write_todos` tool-result seam.
- **[R]** `runtime_api/schemas/common.py:166` and `schemas/events.py:615` carry the
  `todos` field and internal-frame routing.

To keep per-lane keying it must be added to **both** `middleware=` (root) and
`universal_middleware_factories` (children) — and note §4b: on 0.7.1 a root-lane
instance whose `.name` matches will now **replace** the GP lane's own instance, so
naively passing it down both lanes reproduces the instance-sharing bug for todos too.
Left undone, the plan panel silently goes empty.

Related prompt change with a real blast radius **[V]**: `BASE_AGENT_PROMPT` is
deprecated (removal in 0.9.0) and the authored base prompt is now empty. Built-in
tool-usage constants (`TASK_SYSTEM_PROMPT`, `ASYNC_TASK_SYSTEM_PROMPT`, …) are removed
and `system_prompt` defaults to `None`. Every agent loses ~40 lines of upstream
core-behaviour prose that currently sits after our `system_prompt`. Our
`system_prompt_suffix` still lands. Restoring the old text means setting
`HarnessProfile.base_system_prompt`; leaving it lean is defensible but is a behaviour
change that needs an eval pass, not a code review.

---

## 9. Transitive dependency bumps

**[V]** `requires_dist` for `deepagents==0.7.1` (released 2026-07-30), verbatim from
PyPI, against **[R]** our pins in `services/ai-backend/requirements.txt`:

| Dep                      | Required by 0.7.1 | We pin       | Action                                                                                 |
| ------------------------ | ----------------- | ------------ | -------------------------------------------------------------------------------------- |
| `langchain`              | `>=1.3.14,<2.0.0` | `1.3.14`     | satisfied                                                                              |
| `langchain-core`         | `>=1.5.0,<2.0.0`  | **`1.4.9`**  | bump (our `langchain==1.3.14` allows `>=1.4.9,<2.0.0`, so no forced `langchain` bump)  |
| `langchain-anthropic`    | `>=1.5.3,<2.0.0`  | **`1.4.8`**  | bump                                                                                   |
| `langchain-google-genai` | `>=4.3.1,<5.0.0`  | **`4.2.7`**  | bump                                                                                   |
| `langsmith`              | `>=0.10.9`        | **`0.10.5`** | bump                                                                                   |
| `wcmatch`                | `>=11.0`          | `11.0`       | satisfied — good, our host-path floor depends on wcmatch `BRACE \| GLOBSTAR` semantics |
| `packaging`              | `>=23.2`          | present      | satisfied                                                                              |
| `requires_python`        | `>=3.11,<4.0`     | `>=3.13`     | satisfied                                                                              |

New optional extras in 0.7.1, none of which we want: `aws` (`langchain-aws`),
`quickjs`, `video` (`av`, `pillow`).

Four pins move in `requirements.txt` (hash-pinned → needs a `pip-compile` regen) and
`pyproject.toml:11-17`. The `langchain-core` 1.4.9 → 1.5.x minor is its own risk
surface for the approval GATE, since `HumanInTheLoopMiddleware`, `InterruptOnConfig`
and `ToolCallRequest` all live in `langchain`/`langgraph`, not deepagents.

---

## 10. ★ NEW — Test-gate health: what would actually catch this, measured

The first pass asserted the suite would not catch the delete blocker. Half of that is
wrong, and the half that is right matters more than it looks.

### 10a. ⚠ CORRECTED — the unit suite DOES catch the new `delete` tool

First pass claimed: _"the in-memory backends used in tests do not implement `delete`,
so `_supports_delete` is `False` there and the tool is never even bound."_ That is
false for 0.7.1.

- **[V-0.6]** `FilesystemMiddleware.__init__` defaults to `StateBackend()`
  (`filesystem.py:862`).
- **[V-0.6]** 0.6.12's `StateBackend` has **no** `delete`.
- **[V]** 0.7.1's `StateBackend` **defines** `delete` (recursive prefix deletion via
  the `files` channel reducer), so `_supports_delete(StateBackend())` is **True**.
- **[R]** `tests/unit/architecture/test_model_visible_operation_inventory.py:386`
  builds the pinned surface as
  `(*TodoListMiddleware().tools, *FilesystemMiddleware().tools, task)`.

So on 0.7.1 that pinned tool sequence gains `delete` and
`test_final_model_visible_tool_sequence_matches_the_pinned_topology` **fails on the
first run after the bump**. Good — the tripwire works.

What the suite still does **not** catch is the §5b consequence: no unit test drives a
host-absolute `delete` down through `HostFilesystemFloor` → `NativeHostPathBackend` →
`FilesystemBackend` to real disk. **The surface change is caught; the disk reach is
not.** That is the in-memory-adapter blind spot, correctly identified — just located
one layer further in than the first pass put it.

The first tripwire to fire is cheaper still: **[R]**
`test_framework_middleware_contracts.py:22` pins
`{"deepagents": "0.6.12", "langchain": "1.3.14", "langgraph": "1.2.9"}` and fails the
moment the installed version changes. That is by design — it forces this document to
be re-read.

### 10b. Measured baseline on 0.6.12

Ran the 45 deepagents-coupled test files (47 files reference `deepagents`; `fakes.py`
and `helpers.py` are fixtures, not test modules):

```
cd services/ai-backend && PYTHONPATH="…/src:…/service-contracts/src:…/audit-chain/src" \
  .venv/bin/python -m pytest $(grep -rl deepagents tests/ --include="*.py" \
  | grep -v "/fakes.py\|/helpers.py") -q -p no:randomly
→ 1 failed, 1289 passed in 16.97s
```

### 10c. ★ The gate is order-fragile TODAY, before any upgrade

The single failure is **pre-existing at HEAD `15814fc1` with a clean working tree** —
not caused by the upgrade and not caused by a concurrent edit.

```
tests/unit/agent_runtime/agent/test_deep_agent_builder.py::
  test_universal_middleware_is_materialized_for_supervisor_and_local_subagents
```

It is **order-dependent**:

| Invocation                                                                             | Result                  |
| -------------------------------------------------------------------------------------- | ----------------------- |
| `pytest tests/unit/agent_runtime/agent/test_deep_agent_builder.py`                     | **16 passed**           |
| `pytest …/memory/test_context_memory_management.py …/agent/test_deep_agent_builder.py` | **1 failed, 30 passed** |

Minimal reproducer: `tests/unit/agent_runtime/memory/test_context_memory_management.py`
run **before** `test_deep_agent_builder.py` in the same process. The assertion that
breaks is `assert all(len(instances) == 1 for instances in controls)` — i.e. leaked
global state makes a lane hold more than one `RuntimeControlMiddleware`.

**This is exactly the test §4b needs as its gate**, and it is unreliable right now.
Fixing the leak is a prerequisite for the upgrade, not a nice-to-have: without it we
cannot distinguish "0.7.1 introduced GP instance sharing" from "test pollution".

⚠ This file is outside this evaluation's ownership — see §12.

---

## 11. What I could not determine

Stated explicitly rather than guessed:

1. **`_build_task_tool`'s 0.7.1 parameter list.** Our monkey-patch
   (`atlas_task_tool.py:428`) replaces it unconditionally. I confirmed the name still
   exists but did not diff its signature. If upstream added a kwarg, our mirror
   silently drops it; if upstream renamed it, the patch becomes a no-op and the
   supervisor loses `supervisor_task_call_id` threading with no error. **Highest
   residual unknown.**
2. **Exact capture line of `_main_core_names`** (§4a). Our middleware's final slot
   rests on `_apply_custom_middleware`'s docstring, not on observed line order.
3. **`LangSmithSandbox` at 0.7.1** — imported lazily inside a function
   (`providers/langsmith.py:149`), so any rename fails at call time, not import time.
   Not checked.
4. **Whether any test asserts the `encoding` returned by the MCP catalog backend's
   read path** (§7), i.e. whether the dropped normalization is covered.
5. **`langchain-core` 1.4.9 → 1.5.x contents.** Scoped out; it is its own evaluation
   and it owns the HITL/interrupt path we gate approvals on.
6. **Anything requiring 0.7.1 to actually be installed.** Deliberately not done — this
   evaluation is not permitted to move a pin, and the venv it would touch belongs to
   the main checkout.

---

## 12. Recommendation

### DEFER. Do not upgrade now.

- **Zero pull.** **[V]** The only 0.7 feature that would have changed our roadmap —
  lazy / progressive / semantic tool selection — did not ship; `middleware/__all__` is
  unchanged, and its live successor (issue 4658) is open with no implementation.
  Nothing in 0.7.1 fixes a bug we are hitting.
- **Real push-back cost, now measured at two blockers rather than one.** A blind
  `pip install -U deepagents` hands the agent (a) a recursive `delete` over the user's
  home directory with the desktop host filesystem floor bypassed (§5b), and (b) an
  `rm -rf` inside the policy sandbox that skips the `/workspace` guard the class
  documents as defense in depth (§6). It also empties the plan panel (§8), makes the
  GP subagent share the supervisor's middleware instances (§4b), and silently drops
  our encoding normalization on MCP catalog reads (§7).
- **The gate we would grade the upgrade with is currently unreliable** (§10c), and
  that is true today, independent of the upgrade.

### The trigger that flips this

Upgrade when **either**:

- issue 4658 `ToolSelectionMiddleware` ships — then the calculus flips, because that
  is the piece worth taking a minor for; **or**
- we want `FilesystemMiddleware(tools=[...])` / `grep_max_count` / `truncated` flags
  for the tool-budget work, in which case do it as a planned, scoped upgrade rather
  than an opportunistic bump.

A third, weaker trigger: if we ever _want_ a gated delete on desktop, 0.7.1 is where
the primitive exists — but that is a feature decision, not an upgrade decision.

### Effort when we do it: **6-9 engineer-days**, unchanged

The two new findings (§6, §7) are both small; the two corrections (§8, §10a) make two
line items _cheaper_, not dearer, so the range holds.

---

## 13. Migration checklist

Ordered so that each step's failure is cheap and legible. Steps 0-2 are prerequisites
and can be done **now**, before any decision to upgrade.

| #      | Step                                                                                                                                                                                                                                                                              | Days    |
| ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------- |
| **0**  | **Fix the order-dependent failure in §10c** (`test_context_memory_management.py` leaking into `test_deep_agent_builder.py`). Prerequisite: it is the §4b gate. Do this regardless of the upgrade.                                                                                 | 0.3     |
| **1**  | Fix the stale packaging metadata in §14 — a resolver reading `0.5.5` while the pin says `0.7.1` produces confusing failures. Do it **first**; it is a prerequisite for a clean resolve.                                                                                           | 0.3     |
| **2**  | Pin `validate_path` to its real home (`deepagents.backends.utils`, not the `middleware.filesystem` re-export) and add a drift test for `_build_task_tool`'s signature (§11.1). Reduces the surface before touching versions.                                                      | 0.3     |
| **3**  | Bump `deepagents` + the 4 transitive pins in `pyproject.toml` and regen the hash-pinned `requirements.txt`. Update `_EXPECTED_FRAMEWORK_VERSIONS`. **Expect `test_final_model_visible_tool_sequence_matches_the_pinned_topology` to fail — that is the tripwire working (§10a).** | 0.5     |
| **4**  | **Same commit as step 3**: add `"delete"` to `DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES` (`execution/tool_surface.py:43`). Closes both §5b and §6. Non-negotiable ordering.                                                                                                          | 0.1     |
| **5**  | Assert the middleware order rather than trusting §4a's docstring inference: a test pinning our three middleware's index relative to the profile tail and to `_ToolExclusionMiddleware`.                                                                                           | 0.2     |
| **6**  | Fix the GP-subagent instance-sharing regression (§4b) — distinct names per lane, or stop double-passing the same classes — graded by the now-reliable test from step 0.                                                                                                           | 1.0     |
| **7**  | Restore `TodoListMiddleware` from `langchain.agents.middleware` on both lanes, keeping per-lane checklist keying, and verify §4b's replacement semantics do not collapse the two (§8).                                                                                            | 1.0     |
| **8**  | Fix `slice_read_response`'s dead branch and the dropped `encoding` normalization in `capabilities/mcp/catalog_backend.py:373` (§7).                                                                                                                                               | 0.2     |
| **9**  | Update the three mirrored contract tables for `delete` (§2e) even though the tool is excluded — they are the version-skew detectors, and a stale detector is worse than none.                                                                                                     | 0.5     |
| **10** | `read_file` / `ls` / `glob` output-shape fallout: parsers and snapshot tests (§15).                                                                                                                                                                                               | 0.5     |
| **11** | Decide lean-`BASE` vs restoring the legacy prose via `HarnessProfile.base_system_prompt`; eval pass, not a code review (§8).                                                                                                                                                      | 1.0-2.0 |
| **12** | Run the 45 deepagents-coupled files, then the full suite, then the **live packaged desktop journeys** in `tools/desktop-journeys/`. A live run is the only thing that would catch §5b's disk reach.                                                                               | 1.0     |

**Rollback:** steps 3-4 are one commit and revert cleanly. Everything from step 6 on
is independently revertable. There is no data migration, so rollback is a pin revert
plus `pip-compile`.

---

## 14. Stale packaging metadata (flagged, unrelated to the upgrade)

**[P1]** Three different deepagents versions are visible in this checkout:

| Where                                                                          | Version                                |
| ------------------------------------------------------------------------------ | -------------------------------------- |
| `services/ai-backend/requirements.txt:552`                                     | `deepagents==0.6.12`                   |
| `services/ai-backend/pyproject.toml:11`                                        | `deepagents==0.6.12`                   |
| `services/ai-backend/.venv/…/deepagents-0.6.12.dist-info` (actually installed) | **0.6.12** — correct                   |
| `services/ai-backend/.venv/…/agent_runtime-0.1.0.dist-info/METADATA:6`         | **`Requires-Dist: deepagents==0.5.5`** |
| repo-root `/.venv/…/deepagents-0.5.5.dist-info`                                | **0.5.5**                              |

1. **`agent_runtime-0.1.0.dist-info/METADATA` still declares `deepagents==0.5.5`.**
   Stale editable-install metadata, written when `pyproject.toml` pinned 0.5.5 and
   never regenerated. The runtime is fine — the editable path import wins — but any
   tool that resolves declared dependencies (`pip check`, `pip-audit`, an SBOM
   generator, a resolver run) reads `0.5.5` and can report a false conflict or
   silently downgrade. Fix: `pip install -e . --no-deps --force-reinstall` in
   `services/ai-backend`.
2. **The repo-root `/.venv` has `deepagents 0.5.5` + `langchain 1.2.16` + `langgraph
1.1.10`** — a whole generation behind the service venv, violating the "each Python
   service owns its own `.venv`" rule in `CLAUDE.md`. Anything accidentally run from
   the root venv exercises 0.5.5, not what we ship.

Neither blocks the upgrade; both are landmines for whoever does it.

**Note for a future evaluator:** the worktree at
`.claude/worktrees/lineara-connection-issue-e9bd20/` has **no** `.venv` of its own —
it borrows the main checkout's interpreter. Package-source inspection there reads the
main checkout's installed tree, not the worktree's branch.

---

## 15. Smaller behavioural changes worth a look

- **[V] `write_file` now creates missing files and replaces existing ones** instead of
  erroring; the description no longer tells the model to read first. There is no
  create-only compatibility mode. A silent full-file clobber is now one tool call.
  Writes are still gated by our interrupt rules, but the approval card copy ("write to
  X") now covers destroy-and-replace.
- **[V] `read_file` render changed** — no fixed-width `cat -n` gutter,
  `LINE_NUMBER_WIDTH` removed from both `middleware/filesystem.py` and
  `backends/utils.py`, dynamic alignment, two-space separator, plus pagination
  metadata. **[R]** We have no reference to `LINE_NUMBER_WIDTH`, but anything of ours
  that parses or snapshots read output needs re-checking.
- **[V] `ls` / `glob` empty results render as `"No files found"`**, not `[]`.
- **[V] Deny/interrupt checks use bulk path overlap** instead of exact-path matching;
  `_FS_TOOL_PATH_ARGS` classifies `delete` as `("write", "file_path", "bulk", None)`.
- **[P1]** `SummarizationMiddleware(history_path_prefix=...)` now raises `TypeError` —
  we do not pass it.
- New in 0.7.0 and genuinely useful later: `FilesystemMiddleware(tools=[...])`
  allowlist (typed `FsToolName`), `grep_max_count`, `truncated` flags on
  `GrepResult`/`GlobResult`, glob brace expansion, paginated `read_file`.

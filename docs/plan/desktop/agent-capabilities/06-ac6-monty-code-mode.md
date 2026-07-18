# AC6 — Monty code mode

| Field             | Decision                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Spec ID           | AC6                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Status            | **Planned — in scope** (wanted, not deferred); decision-complete and awaiting architecture review. Multi-PR epic: interpreter foundation → policy-engine prereq → parity.                                                                                                                                                                                                                                                                                                                                   |
| Wave              | 3 — Execution capabilities                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Estimated effort  | L — 10–15 engineer-days **for the Monty/interpreter work only**. This **excludes** the undeclared direct-path policy-engine prerequisite below, which is separately sized. If that prerequisite is not funded, AC6 ships **pure-compute-only** (no external functions) and the auto/ask/require/block parity criterion is descoped.                                                                                                                                                                         |
| Dependencies      | AC2 file-native session store, AC3 durable checkpoint/recovery, AC4 artifact store. **Undeclared-until-now prerequisite:** a wired four-mode (`auto`/`ask`/`require`/`block`) tool-use policy engine on the **direct** tool path — `runtime_gate.py`'s `ToolUsePolicyGate` is currently **dead code** (never imported/instantiated; live path is `ToolPermissionChecker` scope-filtering + a single hardcoded `call_mcp_tool` HITL). See "Undeclared dependency: the four-mode policy engine is not wired". |
| Required for      | AC10 hardening and staged desktop rollout                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Primary owner     | `services/ai-backend` execution and capabilities                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Supporting owners | Runtime worker, runtime adapters, desktop diagnostics                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Web impact        | None                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |

## Built-in-first (do not reinvent the framework)

Use the DeepAgents/LangChain built-in as the engine — DeepAgents interpreters (Monty), `SandboxBackendProtocol` (remote sandbox), LangChain Playwright toolkit / browser-MCP, `langchain-mcp-adapters`, `HumanInTheLoopMiddleware`, LangGraph savers — and add only the thin enforcement layer (approval/budget/event/persistence) we require. Do not reinvent what the framework provides.

For AC6 the engine is the **Monty interpreter behind a DeepAgents-shaped `InterpreterPort`**: 0xCopilot does not write an interpreter, a sandbox, or a snapshot format. What AC6 adds is only the thin enforcement layer we require — the `PolicyToolInvoker` that routes every external function through the existing permission/approval/budget/citation/audit path, AC4 snapshot offload, and AC2/AC3 checkpoint persistence. This mirrors precedents already validated in this codebase: the file store reuses the LangGraph `AsyncSqliteSaver` as its graph/approval checkpointer ([`deep_agent_builder.py`](../../../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py)), the approval path rides the DeepAgents-native `interrupt_on` interrupt rather than a bespoke pause engine ([`factory.py`](../../../../services/ai-backend/src/agent_runtime/execution/factory.py)), and model-facing files are DeepAgents `CompositeBackend` routes. The one place AC6 cannot inherit upstream behavior is LangChain's QuickJS PTC bridge, which bypasses per-call `interrupt_on` approvals; the `PolicyToolInvoker` seam exists precisely to re-add that thin enforcement, not to replace the interpreter.

## Problem and why now

The desktop agent needs a cheap programmable loop for calculations, transformations, branching, and repeated calls to approved tools. Asking the model to emit every intermediate value and every loop iteration as another model turn is slower, more expensive, and less reliable than letting it write a small program.

The repository has a current `CodeSandboxPort` and `InProcessCodeSandbox` in `services/ai-backend/src/agent_runtime/capabilities/tools/code_sandbox.py`. That adapter parses an AST and then runs Python with `exec()` in the trusted AI worker. Its own module states that it has no CPU or memory isolation and is not a production security boundary. `code_tool_adapter.py` is for stored first-party code routines; it is not safe to expose as an LLM code interpreter. Rebranding that path as “sandboxed code mode” would create a host-code-execution primitive.

Pydantic Monty is a better-shaped primitive for this narrow job: it is an independent Rust interpreter for a subset of Python, has no CPython `exec()`, exposes host capabilities only through explicit external functions, can pause into snapshots, and advertises memory/allocation/depth/time controls. It is also explicitly **experimental and not ready for prime time**. This PR therefore puts Monty behind a product-owned port, a server-authoritative desktop feature gate, hard limits, and a no-go spike. It does not make Monty the remote execution environment.

LangChain Deep Agents also documents a QuickJS interpreter with programmatic tool calling (PTC). Its official documentation warns that PTC calls travel through the interpreter bridge rather than the normal tool path, so per-call `interrupt_on` approvals are not enforced. 0xCopilot cannot inherit that behavior because connector writes, paid operations, and destructive tools must retain their existing permission and approval semantics.

## Goals

- Add a desktop-only `run_code_mode` tool that executes a supported Python subset through a product-owned `InterpreterPort`.
- Select Monty only through an adapter; no domain or worker code imports Monty directly.
- Default to pure computation with no filesystem, environment, network, subprocess, clock, randomness, or host object access.
- Allow an explicit, immutable list of external functions for programmatic tool calling.
- Route **every** external function invocation through the same permission, approval, budget, citation, payload-offload, and audit dispatcher used by an ordinary tool call.
- Suspend safely at an approval boundary, persist the interpreter snapshot through AC3/AC4, and resume exactly once.
- Enforce code-size, wall-time, heap, allocation, recursion-depth, external-call, snapshot-size, result-size, stdout, and stderr limits.
- Return stable typed failures that let the model or user choose ordinary tools or AC7 remote execution instead.
- Keep the current trusted code-routine surface behavior unchanged.

## Non-goals

- Full CPython compatibility, third-party packages, notebooks, data-science libraries, shell commands, or package installation.
- Reading or writing AC5 workspace files directly from interpreted code.
- Remote, container, VM, or kernel-isolated execution. AC7 owns that capability.
- Exposing Monty OS callbacks, generic I/O callbacks, `eval`, `exec`, reflection, or arbitrary host-language objects.
- Making LangChain QuickJS PTC the hidden implementation.
- Making code mode available to web or non-desktop deployment profiles.
- Automatically translating unsupported Python into another language.
- Bypassing a tool approval because the outer `run_code_mode` call was approved.

## User experience and failure behavior

### Normal flow

1. The model calls `run_code_mode` with a bounded Python-subset program and, optionally, stable names of external functions it wants to use.
2. The activity feed shows **Running code mode** with elapsed time, limit utilization, and external-call count. Source code is available in run details but is collapsed by default.
3. Pure computations complete without a user prompt.
4. If code requests an external function, the runtime displays and enforces the same tool policy as a direct call:
   - blocked tools fail without execution;
   - `ask` and `require` policies create the existing approval card;
   - edits to arguments become the arguments supplied when the snapshot resumes;
   - rejection resumes with a typed rejected-call value rather than pretending the tool ran.
5. The final interpreter result is returned inline when small or as an AC4 artifact reference plus bounded preview when large.

### Failure behavior

- Unsupported syntax or standard-library use returns `unsupported_language_feature` with a safe line/column and suggests ordinary tools or AC7. It never falls back to host Python.
- A resource limit returns the specific limit kind and observed bounded counter. Partial stdout/stderr is truncated and marked incomplete.
- An invalid or oversized snapshot fails the run segment closed; it is never deserialized with pickle or CPython.
- An external function not present in the immutable allowlist returns `external_function_denied`.
- A changed tool grant, paused connector, exhausted budget, or expired approval is re-evaluated at resume time and can still reject the call.
- Worker loss after an external tool completed but before interpreter resume uses the persisted invocation idempotency key. The external tool is not called twice.
- When the Monty adapter is unavailable, unhealthy, unsupported on the platform, or disabled, the tool is absent from the model-visible tool list. Existing sequential tool calling continues.
- No failure path invokes `InProcessCodeSandbox`, `exec`, a subprocess, `LocalShellBackend`, or AC7 implicitly.

## Alternatives considered

### Reuse `InProcessCodeSandbox`

Rejected. It ultimately executes code in the trusted Python process, has OS-default memory/CPU limits, and is documented as unsuitable for untrusted code. An AST denylist is not an isolation boundary.

### Direct CPython `exec()` in a restricted globals dictionary

Rejected. Python object introspection, native extensions, interpreter bugs, accidental object leakage, threads, and denial-of-service behavior make “restricted Python” inside the trusted worker unsafe. Timeout cancellation also does not reliably stop arbitrary host Python.

### LangChain QuickJS interpreter with PTC

Not selected. It is useful prior art and has a mature Deep Agents integration, but its documented PTC bridge does not enforce `interrupt_on` per bridged tool call. Gating only the outer `eval` call is insufficient for a loop that can invoke many differently privileged tools. A future QuickJS adapter would have to implement this PRD’s `InterpreterPort` and product-owned invocation dispatcher, with upstream PTC disabled.

### QuickJS as a silent fallback

Rejected. Python and JavaScript have different language and snapshot semantics. Substitution would make persisted programs and recovery nondeterministic across machines. A Monty no-go keeps code mode off.

### AC7 remote sandbox for every calculation

Rejected as the default because provisioning latency, data transfer, network dependency, cost, and cleanup are unnecessary for pure in-memory computation. AC7 remains the explicit option for full languages, packages, files, and shell commands.

### WASM CPython/Pyodide

Rejected for this wave. It substantially expands the runtime and standard-library attack surface, has a larger cold start and bundle, and still requires a host-capability and resource-governance design.

## Architecture and ownership

### Process and service boundaries

- The interpreter executes in the existing `runtime_worker` process, not in Electron main or the renderer.
- `agent_runtime` owns immutable contracts, limit policy, external-function mapping, and the product invocation dispatcher port.
- `runtime_adapters.interpreters` owns the Monty dependency and conversion between Monty values/snapshots and product contracts.
- AC3 owns checkpoint atomicity and worker-resume semantics.
- AC4 owns snapshot/result bytes and content-addressed references.
- Existing tool/MCP owners continue to own authorization, approval, connector token use, invocation recording, citations, and budgets.
- Electron only renders events, approvals, diagnostics, and artifact links. It never receives interpreter internals or a host callback token.

### Control flow

```text
model -> run_code_mode tool -> InterpreterService -> InterpreterPort.start()
  pure completion -> bounded result/offload -> tool result
  external call  -> snapshot artifact + PolicyToolInvoker.prepare()
    denied       -> InterpreterPort.resume(rejection)
    approval     -> existing LangGraph interrupt -> persisted checkpoint
    allowed      -> PolicyToolInvoker.invoke() -> citations/audit/offload
                  -> InterpreterPort.resume(tool result)
  repeat until terminal result or hard limit
```

`PolicyToolInvoker` is a new product-owned seam, not a Monty-specific policy implementation. The normal LangGraph tool wrapper and the interpreter bridge both call it. Moving policy decisions behind this seam is required before any external function is enabled; otherwise code mode ships pure-compute-only.

### Undeclared dependency: the four-mode policy engine is not wired

The acceptance criterion "every external function passes through one shared `PolicyToolInvoker` … outcomes and audit shape must match a direct call for `auto`/`ask`/`require`/`block`" silently assumes a four-mode policy engine **already exists on the direct tool path**. It does not. Code-verified state of the repo:

- `services/ai-backend/src/agent_runtime/capabilities/tools/runtime_gate.py` defines `ToolUsePolicyGate.decide(...)` returning allow / reject / require-approval for `auto`/`ask`/`require`/`block`, **but the symbol is never imported or instantiated anywhere** (it appears only in its own module and `__all__`). It is dead code.
- The live production authorization path is **scope filtering** (`ToolPermissionChecker` in `registry.py`/`loader.py`) plus a **single hardcoded human-in-the-loop** on `call_mcp_tool` (`capabilities/mcp/middleware/call_tool.py` + `mcp/permissions.py`). There is no live `auto`/`ask`/`require`/`block` decision point for direct tools.

Consequence: the interpreted-vs-direct parity test is either **vacuous** (there is no direct-path four-mode behavior to match) or it **silently obligates AC6 to first build and wire the four-mode engine on the direct path** — work that was absent from AC6's dependencies and its 10–15-day estimate. This document now makes that explicit:

- **Prerequisite PR (separate, sized independently):** wire a four-mode tool-use policy engine on the direct tool path (either by wiring `runtime_gate.py`'s `ToolUsePolicyGate` into the live dispatcher/middleware or replacing it), with its own tests, so `PolicyToolInvoker` has a real contract to mirror. Until it lands, AC6 ships **pure-compute-only** and the `auto`/`ask`/`require`/`block` parity criterion is descoped, not silently passed.
- `PolicyToolInvoker` is then the single seam both the direct tool wrapper and the interpreter bridge call, guaranteeing equivalence by construction rather than by a test against nonexistent behavior.

### Interpreter value boundary

The interpreter receives only JSON-compatible scalars, lists, and maps. It never receives `BaseTool`, MCP client, runtime context, store, file handle, socket, exception, class, module, coroutine, or Python object identity.

### External-function registry

- An external function name is a stable alias for one already-authorized product tool, for example `tools.search_web`.
- The model must declare requested aliases on `InterpreterRequest`; wildcard exposure is invalid.
- The runtime resolves aliases against the run’s already filtered tool/MCP cards.
- Names are frozen for the interpreter session. Loading another tool requires ending the current segment and starting a new request.
- Each invocation gets a child `tool_call_id`, `invocation_id`, and idempotency key.
- The actual tool’s side-effect class, connector scope, per-chat pause state, user policy, and budget are re-read at dispatch and resume.
- The bridge has a hard maximum of 32 external calls per evaluation and still charges each call against the underlying tool’s budget.
- Dynamic subagent dispatch is not exposed from Monty in AC6.

### Resource policy

The following defaults are product constants and cannot be raised by model input:

| Limit                                                         |      Default | Hard ceiling |
| ------------------------------------------------------------- | -----------: | -----------: |
| Source code                                                   | 32 KiB UTF-8 |       64 KiB |
| Wall time per uninterrupted interpreter segment               |          3 s |         10 s |
| Total interpreter wall time, excluding approved external work |         10 s |         30 s |
| Heap                                                          |       32 MiB |       64 MiB |
| Allocations                                                   |      250,000 |    1,000,000 |
| Recursion depth                                               |          128 |          256 |
| External calls                                                |           32 |           64 |
| Snapshot bytes                                                |        2 MiB |        8 MiB |
| Result before AC4 offload                                     |       32 KiB |      256 KiB |
| Captured stdout                                               |       32 KiB |       64 KiB |
| Captured stderr                                               |        8 KiB |       16 KiB |

Deployment policy may lower defaults. Raising a hard ceiling requires a reviewed PR with benchmarks and adversarial tests. Total time is measured with a monotonic host clock; Monty code is not given clock access.

### Snapshot and resume

- The adapter may emit a snapshot only at a Monty external-function suspension point.
- The snapshot envelope includes adapter name/version, interpreter ABI/version, source hash, limit profile hash, requested-function manifest hash, next invocation index, and artifact digest.
- Snapshot bytes use Monty’s documented serialization only. No pickle, marshal, or generic Python deserializer is allowed.
- AC4 writes bytes before AC3 writes the checkpoint reference. A checkpoint never points at an uncommitted artifact.
- Resume requires exact adapter ABI, source hash, limit profile, runtime identity, run id, and invocation index.
- The worker consumes an approval or tool result with compare-and-swap on `(run_id, interpreter_session_id, invocation_index)`.
- Completed or rejected invocation ids cannot resume again.

### Mandatory Monty spike and exit rule

Before adding Monty to production requirements, pin one exact version and prove all of the following on macOS arm64, macOS x64 CI, and Windows x64:

1. Python 3.13 wheel/install support for every packaged target.
2. No host filesystem, environment, network, subprocess, FFI, import-system, or host-object access without an explicit callback.
3. Preemptive termination for infinite loops, memory growth, allocation storms, recursion, regex abuse, and output floods.
4. `start`/`resume` external functions work asynchronously without blocking the worker event loop.
5. Snapshot bytes survive worker-process restart, reject corruption, and remain within the configured size.
6. Resource counters cannot reset across resumes.
7. Cancellation interrupts active execution within 250 ms and releases memory.
8. JSON conversion rejects cycles, non-finite numbers where unsupported, oversized nesting, and host objects.
9. Fifty concurrent bounded interpreters do not violate worker memory/concurrency limits.
10. License, SBOM, native-binary signing, and Electron desktop packaging review pass.

**Go:** all ten checks pass and the version is pinned in both dependency manifests.

**No-go:** keep `RUNTIME_ENABLE_MONTY_CODE_MODE=false`, do not register `run_code_mode`, and continue with ordinary tool calls plus AC7 for full execution. No alternate interpreter and no direct `exec` is substituted inside AC6. The spike report records the failing criterion and an upstream issue/version to re-evaluate.

### Mandatory interrupt-from-tool-node spike and exit rule

The ten criteria above are all Monty-library isolation. They do **not** cover the genuinely novel mechanism in this design: `PolicyToolInvoker` must raise a LangGraph **interrupt from inside a running tool node** (the `run_code_mode` tool, mid-evaluation, when interpreted code calls an `ask`/`require` external function), suspend the graph, persist the interpreter snapshot + checkpoint, and resume exactly once with the human decision — then continue the _same_ interpreter session. This is not the ordinary top-level tool-boundary interrupt the runtime does today, and it is the failure-prone seam, not a cheap one. It is a separate mandatory spike with its own gate:

1. A LangGraph tool node can trigger a durable `interrupt` mid-execution and the graph re-enters that exact node on resume (not a fresh top-level turn).
2. The interrupt round-trips through the existing approval contract and SSE/event path with no new desktop-only API.
3. On resume, `Command(resume=decision)` reaches the suspended interpreter session and the external call proceeds/rejects with the approved (possibly edited) arguments.
4. A worker crash between interrupt and resume recovers via AC3 checkpoint + AC4 snapshot and never double-dispatches the external call (compare-and-swap on `(run_id, interpreter_session_id, invocation_index)`).
5. Nested/parallel: an interrupt raised inside a subagent's code-mode tool does not corrupt the parent graph's checkpoint namespace.
6. The direct-path four-mode engine (prerequisite above) and the interpreter bridge produce **identical** approval/interrupt/audit shapes through the one `PolicyToolInvoker` seam.

**Go:** all six pass and the mechanism is covered by the AC3 checkpoint/resume contract tests. **No-go:** external functions stay disabled (pure-compute-only code mode) even if the Monty library spike passed; the interrupt seam is not shipped on faith.

## Typed contracts

The implementation spec may refine module grouping, but it must preserve these semantics:

```python
class InterpreterLimitKind(StrEnum):
    CODE_BYTES = "code_bytes"
    WALL_TIME = "wall_time"
    HEAP_BYTES = "heap_bytes"
    ALLOCATIONS = "allocations"
    RECURSION_DEPTH = "recursion_depth"
    EXTERNAL_CALLS = "external_calls"
    SNAPSHOT_BYTES = "snapshot_bytes"
    OUTPUT_BYTES = "output_bytes"


class InterpreterLimits(RuntimeContract):
    max_code_bytes: int
    segment_timeout_ms: int
    total_timeout_ms: int
    max_heap_bytes: int
    max_allocations: int
    max_recursion_depth: int
    max_external_calls: int
    max_snapshot_bytes: int
    max_result_bytes: int
    max_stdout_bytes: int
    max_stderr_bytes: int


class ExternalFunctionSpec(RuntimeContract):
    alias: str
    tool_name: str
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue] | None


class InterpreterRequest(RuntimeContract):
    interpreter_session_id: str
    run_id: str
    code: str
    inputs: dict[str, JsonValue]
    external_functions: tuple[ExternalFunctionSpec, ...]
    limits: InterpreterLimits


class ExternalFunctionCall(RuntimeContract):
    interpreter_session_id: str
    invocation_index: int
    alias: str
    arguments: dict[str, JsonValue]
    snapshot: "PayloadRef"
    source_sha256: str


class InterpreterCompleted(RuntimeContract):
    result: JsonValue
    stdout_preview: str
    stderr_preview: str
    external_invocation_ids: tuple[str, ...]
    payload_ref: "PayloadRef | None"


class InterpreterFailed(RuntimeContract):
    code: "InterpreterErrorCode"
    safe_message: str
    retryable: bool
    limit_kind: InterpreterLimitKind | None
    stdout_preview: str
    stderr_preview: str


InterpreterStep = InterpreterCompleted | ExternalFunctionCall | InterpreterFailed


class InterpreterPort(Protocol):
    async def start(self, request: InterpreterRequest) -> InterpreterStep: ...
    async def resume(
        self,
        *,
        call: ExternalFunctionCall,
        outcome: "PolicyToolInvocationOutcome",
    ) -> InterpreterStep: ...
    async def cancel(self, *, interpreter_session_id: str) -> None: ...
```

The model-facing request is smaller:

```python
class RunCodeModeInput(RuntimeContract):
    code: str
    inputs: dict[str, JsonValue] = {}
    external_functions: tuple[str, ...] = ()
```

It cannot set limits, adapter, snapshot ref, runtime identity, tool id, permission state, or approval state.

### Stable errors

`InterpreterErrorCode` is fixed to:

- `interpreter_unavailable`
- `invalid_source`
- `unsupported_language_feature`
- `external_function_unknown`
- `external_function_denied`
- `approval_expired`
- `resource_limit_exceeded`
- `snapshot_invalid`
- `snapshot_incompatible`
- `cancelled`
- `interpreter_crash`
- `result_invalid`

Safe messages contain no source fragments, callback arguments, tool output, host paths, or adapter traceback.

### Configuration

```text
ENTERPRISE_DEPLOYMENT_PROFILE=single_user_desktop
RUNTIME_ENABLE_MONTY_CODE_MODE=false
RUNTIME_INTERPRETER_PROVIDER=monty
RUNTIME_MONTY_LIMIT_PROFILE=desktop_v1
```

All four conditions are server-side. A renderer flag cannot enable the tool. `FeatureFlag.MONTY_CODE_MODE` is persisted in the run context only after deployment policy and user/workspace capability policy allow it.

## Critical current and proposed files

### Current evidence and integration points

- `services/ai-backend/src/agent_runtime/capabilities/tools/code_sandbox.py` — trusted-only `CodeSandboxPort` and direct-execution adapter that AC6 must not expose.
- `services/ai-backend/src/agent_runtime/capabilities/tools/code_tool_adapter.py` — existing code-routine adapter and invocation recording.
- `services/ai-backend/src/agent_runtime/execution/contracts.py` — runtime feature flags and dependency contracts.
- `services/ai-backend/src/agent_runtime/execution/factory.py` — model-visible tool assembly and current native `interrupt_on` configuration.
- `services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py` — Deep Agents construction seam.
- `services/ai-backend/src/agent_runtime/capabilities/tools/runtime_gate.py` — current tool-use policy decision.
- `services/ai-backend/src/agent_runtime/capabilities/tool_budget_middleware.py` and `tool_budget_guard.py` — per-tool budget enforcement.
- `services/ai-backend/src/agent_runtime/capabilities/citation_capturing_tool.py` — current citation projection and ordinal hint path.
- `services/ai-backend/src/runtime_worker/handlers/run.py` — run-scoped budgets, event production, checkpoints, and resume orchestration.
- `services/ai-backend/src/runtime_worker/dependencies.py` — worker adapter construction.
- `services/ai-backend/pyproject.toml` and `requirements.txt` — pinned runtime dependencies; Monty is not currently present.

### Proposed implementation files

- `services/ai-backend/src/agent_runtime/capabilities/interpreters/contracts.py`
- `services/ai-backend/src/agent_runtime/capabilities/interpreters/ports.py`
- `services/ai-backend/src/agent_runtime/capabilities/interpreters/service.py`
- `services/ai-backend/src/agent_runtime/capabilities/interpreters/code_mode_tool.py`
- `services/ai-backend/src/agent_runtime/capabilities/invocation/ports.py`
- `services/ai-backend/src/agent_runtime/capabilities/invocation/policy_tool_invoker.py`
- `services/ai-backend/src/runtime_adapters/interpreters/monty.py`
- `services/ai-backend/src/runtime_worker/interpreter_resume.py`
- `services/ai-backend/tests/contract/interpreters/test_interpreter_port.py`
- `services/ai-backend/tests/unit/agent_runtime/capabilities/interpreters/`
- `services/ai-backend/tests/integration/runtime_worker/test_code_mode_resume.py`
- `services/ai-backend/docs/features/code-mode.md`

The implementation may modify the listed current files, but it must not delete or repurpose `CodeSandboxPort`; first-party code routines and model-generated code mode remain distinct capabilities.

## Security and threat model

| Threat                                 | Required control                                                   | Verification                          |
| -------------------------------------- | ------------------------------------------------------------------ | ------------------------------------- |
| Host filesystem/network/process escape | Independent Monty interpreter; no OS callbacks; JSON-only values   | Escape corpus and platform spike      |
| Infinite loop or compute bomb          | Preemptive segment and total timeout                               | Busy-loop and nested-loop tests       |
| Heap/allocation/recursion bomb         | Hard adapter limits that survive resume                            | Limit-specific adversarial tests      |
| Output or snapshot amplification       | Byte ceilings and AC4 offload                                      | Boundary and over-limit tests         |
| Approval bypass through PTC            | Product `PolicyToolInvoker` per external call; no upstream PTC     | Ask/require/reject integration matrix |
| Permission change while suspended      | Re-resolve grant, connector state, and policy at resume            | Pause/revoke/resume test              |
| Budget bypass in a loop                | Underlying tool charged once per invocation                        | Parallel/loop budget tests            |
| Duplicate side effect after crash      | Persisted idempotency key and compare-and-swap resume              | Kill-after-tool-before-resume test    |
| Citation laundering                    | Existing citation projection runs before result enters interpreter | Mixed source and synthesis tests      |
| Sensitive argument/result leakage      | Redacted events, bounded previews, artifact refs                   | Log/transcript snapshot tests         |
| Malicious snapshot                     | Digest, ABI/source/profile binding; Monty-only decoder             | Mutation/fuzz tests                   |
| Native dependency compromise           | Exact pin, hashes, SBOM, signing, vulnerability review             | Release gate                          |

The residual risk is an interpreter implementation vulnerability. The feature is experimental, off by default, desktop-only, has no host capability by default, and can be removed without changing stored conversations.

## Persistence, retention, deletion, and recovery

- Source code and terminal result are ordinary typed tool-call events in the AC2 session stream.
- Interpreter snapshots and oversized result/stdout/stderr bytes are AC4 artifacts, never embedded unbounded in JSONL or SQLite.
- A checkpoint stores only typed metadata plus artifact refs.
- Snapshot and external-call records inherit the parent run’s legal hold.
- Active/suspended snapshots remain while a run can resume. After terminal completion they use AC10’s checkpoint/artifact policy: intermediate snapshots expire after 7 days; the latest terminal recovery snapshot and raw tool payloads expire after 30 days unless pinned or held.
- Main-chat summaries, bounded previews, citation metadata, invocation ids, limit outcomes, and artifact-expired markers remain until explicit chat deletion.
- Explicit chat deletion cascades to interpreter checkpoints and unreferenced artifacts; shared content-addressed bytes are deleted only when reference count reaches zero.
- AC10 repair treats a missing snapshot for a suspended run as a visible, non-retryable `snapshot_invalid` terminalization. It never replays external side effects to reconstruct state.
- No OAuth token, provider key, browser cookie, environment value, or Electron secret may enter source inputs, snapshots, result events, or artifact metadata.

## Observability and audit

### Structured events

- `interpreter.started`
- `interpreter.external_call_requested`
- `interpreter.suspended_for_approval`
- `interpreter.resumed`
- `interpreter.limit_exceeded`
- `interpreter.cancelled`
- `interpreter.completed`
- `interpreter.failed`

Fields are limited to org/user/run/tool ids, adapter and ABI version, source hash, limit-profile hash, durations, peak counters, external-call count, snapshot/result byte counts, outcome, and correlation ids. Source text, inputs, callback arguments, outputs, and snapshots are excluded from logs and metrics.

### Metrics

- `runtime_interpreter_invocations_total{adapter,outcome}`
- `runtime_interpreter_duration_seconds{adapter}`
- `runtime_interpreter_limit_exceeded_total{limit_kind}`
- `runtime_interpreter_external_calls_total{tool_name,outcome}`
- `runtime_interpreter_snapshot_bytes`
- `runtime_interpreter_resume_total{outcome}`
- `runtime_interpreter_active`

Tool calls also continue to emit the existing tool invocation, approval, budget, citation, and audit records under the actual underlying tool name. The outer code-mode event does not replace them.

Audit records answer:

- who ran the code and under which tenant/workspace;
- who approved each privileged external function;
- what stable tool and redacted argument digest was invoked;
- what changed, according to the underlying tool’s existing outcome record;
- where source/snapshot/result references are stored;
- retention/legal-hold policy and deletion evidence.

## Acceptance criteria

- `run_code_mode` is absent unless the desktop deployment profile, deployment flag, workspace policy, and run feature flag all permit it.
- The Monty adapter is the only production adapter selected by this PR and passes the ten-point spike.
- Pure code has zero host access and is terminated at every configured resource limit.
- Every external function passes through one shared `PolicyToolInvoker`; there is no interpreter-only copy of permission, approval, budget, citation, or audit logic. **(Requires the prerequisite direct-path four-mode engine; until it lands, this criterion and the parity matrix are descoped and code mode ships pure-compute-only.)**
- `ask` and `require` approvals interrupt and resume the exact Monty snapshot; `block`, pause, revocation, and budget exhaustion fail closed. **(Gated on the interrupt-from-tool-node spike.)**
- A crash at every boundary from snapshot creation through tool completion and resume produces at most one external side effect.
- Large outputs and snapshots use AC4 references and respect quota/retention.
- Unsupported code never executes with CPython or in AC7 without an explicit new request.
- Existing code-routine tests, web/Postgres tests, direct tool calls, MCP approvals, citations, and budgets remain unchanged.

## Detailed test plan

### Unit and contract tests

- Run the same pure-compute vectors through every `InterpreterPort` conformance fixture.
- Validate every limit at `limit - 1`, `limit`, and `limit + 1`.
- Reject duplicate aliases, wildcard aliases, unknown tools, non-JSON values, deep nesting, cycles, non-finite values, oversized code, and malformed UTF-8 boundaries.
- Verify stable error mapping without source or traceback leakage.
- Round-trip snapshots and reject bit flips, truncation, wrong ABI, wrong source hash, wrong profile hash, wrong run, and replayed invocation index.
- Verify cancellation is idempotent.

### Policy and integration tests

- Matrix direct tool call versus interpreted external call for `auto`, `ask`, `require`, and `block`; outcomes and audit shape must match. **This test presupposes a wired direct-path four-mode engine (see "Undeclared dependency"); it is meaningless until that prerequisite lands and must not be marked passing against the current unwired `runtime_gate.py`.**
- Pause a connector or revoke a filesystem grant while code waits for approval; resume must reject.
- Edit tool arguments in the approval UI; only edited arguments reach the tool and audit digest.
- Reject an approval; interpreter receives a typed rejection and may branch without the side effect.
- Consume exactly the hard and soft tool budgets from loops and parallel code.
- Project citations from two external functions and preserve source ordinals in the final agent response.
- Offload a large tool result and allow only an explicitly approved artifact-read function to retrieve more content.

### Crash, load, and adversarial tests

- Kill the worker before snapshot write, after artifact write, after checkpoint write, before external call, after external call, and before resume commit.
- Run infinite loops, recursive calls, huge list/string/dict growth, allocation churn, output flood, regex denial-of-service samples, and nested external calls.
- Fuzz source parsing, external arguments, snapshot bytes, and result conversion.
- Run 50 concurrent interpreters within worker concurrency and assert bounded RSS and cancellation latency.
- Assert no interpreter process/thread/handle remains after cancellation or worker shutdown.

### Platform and regression tests

- Packaged macOS arm64, macOS x64 CI, and Windows x64 smoke tests.
- Dependency installation and native-binary signing/notarization checks.
- Desktop flag off: tool absent and no Monty import at runtime.
- Non-desktop/Postgres profile: byte-for-byte compatible tool catalog and run behavior.
- Existing `InProcessCodeSandbox` code-routine suite remains green and cannot be reached from `run_code_mode`.

## Rollout, migration, and backout

1. Land contracts, fake adapter, shared invocation dispatcher, and tests with no production tool.
2. Run and publish the Monty spike. A no-go stops here.
3. Pin Monty and enable pure-compute-only for internal desktop builds.
4. Canary external functions with read-only, non-connector test tools after policy parity evidence.
5. Enable read-only production tools for opted-in internal workspaces.
6. Enable mutating tools only after approval, idempotency, audit, and crash tests pass.
7. Expand to desktop beta behind per-workspace policy; AC10 owns broader rollout.

Stop conditions are any host-access escape, unbounded execution, duplicate side effect, approval/budget mismatch, snapshot incompatibility after a supported upgrade, secret leakage, or platform packaging failure.

Backout sets `RUNTIME_ENABLE_MONTY_CODE_MODE=false`, removes the tool from new runs, allows already-approved underlying tool calls to finish, and terminalizes suspended interpreter checkpoints as `interpreter_unavailable` without deleting their evidence. Stored chats remain readable because events and payload refs are product contracts, not Monty objects.

There is no data migration from `CodeSandboxPort`; the two features never share persisted executable state.

## Definition of done

- The PRD is accepted and its AC2/AC3/AC4 dependencies are implemented.
- A component-local implementation spec pins the Monty version and records the spike evidence.
- `InterpreterPort`, Monty adapter, shared invocation dispatcher, code-mode tool, checkpoint/resume integration, events, metrics, and audit records are implemented.
- All unit, contract, policy, crash, adversarial, load, packaging, and web-regression tests pass.
- Threat-model review, dependency/SBOM review, and desktop security review approve the exact native binary.
- `services/ai-backend/docs/features/code-mode.md` documents supported syntax, limits, errors, retention, and operator disable/backout.
- No open implementation choice remains and no source/config change is presented as implemented until code, tests, docs, and rollout evidence agree.

## Why this is sane under SOLID, DRY, KISS, and single-source-of-truth

- **Single responsibility:** Monty interprets; `PolicyToolInvoker` governs tool calls; AC3 checkpoints; AC4 stores bytes; tools keep domain ownership.
- **Open/closed and dependency inversion:** orchestration depends on `InterpreterPort`, so Monty can be upgraded or removed without changing the model-facing contract.
- **Interface segregation:** the interpreter gets `start`, `resume`, and `cancel`, not a broad runtime/store/tool object.
- **DRY:** interpreted and ordinary tool calls share one policy/budget/citation/audit dispatcher.
- **KISS:** one interpreter, one limit profile, one model-facing tool, no full CPython, no filesystem, and no silent fallback.
- **Single source of truth:** AC2 events describe execution; AC3 checkpoints reference AC4 snapshot bytes; actual tool records remain authoritative for side effects and citations.

## Residual risks

- Monty may change its ABI or snapshot format while experimental. Exact version pinning and snapshot metadata make incompatibility explicit; upgrade requires migration or terminalization evidence.
- Native interpreter defects can still exist. Zero capabilities by default and hard disable/backout reduce blast radius but do not prove memory safety.
- Programmatic calls can increase action volume. Tool budgets and the interpreter-specific external-call ceiling both apply.
- A model can summarize tool data misleadingly. Existing citation projection and visible underlying tool activity remain the provenance path.

## References

### Repository

- [`services/ai-backend/src/agent_runtime/capabilities/tools/code_sandbox.py`](../../../../../services/ai-backend/src/agent_runtime/capabilities/tools/code_sandbox.py)
- [`services/ai-backend/src/agent_runtime/capabilities/tools/code_tool_adapter.py`](../../../../../services/ai-backend/src/agent_runtime/capabilities/tools/code_tool_adapter.py)
- [`services/ai-backend/src/agent_runtime/execution/factory.py`](../../../../../services/ai-backend/src/agent_runtime/execution/factory.py)
- [`services/ai-backend/docs/features/approvals.md`](../../../../../services/ai-backend/docs/features/approvals.md)
- [`services/ai-backend/docs/features/tool-calling.md`](../../../../../services/ai-backend/docs/features/tool-calling.md)
- [`docs/roadmap/22-b8-tool-budget.md`](../../../../roadmap/22-b8-tool-budget.md)

### Official prior art

- [Monty introduction](https://pydantic-monty.mintlify.app/introduction) — experimental status, Python subset, host isolation, snapshots, external functions, and resource controls.
- [Monty security model](https://pydantic-monty.mintlify.app/concepts/security) — no direct filesystem, environment, network, subprocess, or third-party-library access.
- [Monty execution modes](https://pydantic-monty.mintlify.app/concepts/execution-modes) — `start`/`resume` and snapshot behavior.
- [LangChain Deep Agents interpreters](https://docs.langchain.com/oss/python/deepagents/interpreters) — QuickJS interpreter, PTC, persistence modes, limits, and the documented per-PTC-call `interrupt_on` limitation.
- [LangChain Python REPL integration](https://docs.langchain.com/oss/python/integrations/tools/python) — explicit warning that host Python can execute arbitrary host code.

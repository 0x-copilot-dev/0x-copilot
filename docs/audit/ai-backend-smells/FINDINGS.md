# ai-backend smell audit — code that exists, is tested, and never runs

Scanners: [`tools/ai-backend-smells/`](../../../tools/ai-backend-smells/). Re-runnable.

```bash
python3 tools/ai-backend-smells/orphans.py services/ai-backend
python3 tools/ai-backend-smells/smells.py  services/ai-backend
```

---

## Why this audit exists, and why it is mechanical

The MCPMark analysis was built by **reading** `agent_runtime` and reasoning about it. That
method produced three errors in one session:

1. It quoted `Messages.Loader.PROTOCOL_ERROR` as the message the model sees. That constant is
   **dead** — no consumer anywhere. The live string is `_CONNECTOR_PROTOCOL_ERROR`.
2. It described the workspace tool surface as `ls/read/glob/grep/write/edit`, copying a
   docstring in `execution/tool_surface.py`. The real deepagents names are
   `ls/read_file/glob/grep/write_file/edit_file`. **The prose and the code disagree**, and
   the difference is exactly what the exclusion set below turns on.
3. It missed `execution/model_invocation/` — a live routing, failover and circuit-breaker
   subsystem — because it read 8 of the 25 files in `execution/`.

The common failure is trusting prose over execution. **Every finding below was produced by
running a scanner and confirmed with a targeted grep**, never by reading a docstring.

## Finding 0 — ~~the exclusion set removes both write tools and shell~~ FIXED UPSTREAM

> **Superseded by `f0c84471` ("feat(filesystem): the agent can finally edit files in a folder
> you attached").** The set is now `frozenset({"execute"})` — `edit_file` and `write_file` were
> removed, so the agent has workspace write and only shell remains excluded. Re-verified after
> merging `origin/dev` on the date of this edit.
>
> Two things survive. The **docstring in `tool_surface.py` is still wrong** — it still says the
> surface reaches the model as `ls`/`read`/`glob`/`grep`/`write`/`edit` when the deepagents
> names are `read_file`/`write_file`/`edit_file`; that mismatch is what made this finding hard
> to reason about in the first place. And the PRD's `T` estimate is still unmeasured.
>
> The original finding is kept below because the reasoning is what a reader needs to evaluate
> whether `execute` should also come back.

## Finding 0 (original) — the exclusion set removes both write tools and shell

`DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES = {"edit_file", "execute", "write_file"}`
([tool_surface.py:21](../../../services/ai-backend/src/agent_runtime/execution/tool_surface.py:21)),
applied as the default `excluded_tool_names` on `RuntimeControlMiddleware`
([runtime_tool_control.py:143](../../../services/ai-backend/src/agent_runtime/capabilities/middleware/runtime_tool_control.py:143)).

Verified against the installed package: deepagents' filesystem tools really are named
`ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`
(`deepagents/middleware/filesystem.py:81-86`). So the exclusion strikes **by exact name** and
removes the agent's only two write verbs plus shell `execute`. The agent can read, list, glob
and grep its workspace; it cannot write to it or run anything.

**Effect on MCPMark: smaller than it first appears, but not zero.** MCPMark's Filesystem,
Postgres and Playwright environments are reached through their **MCP servers**, whose tools
route via `call_mcp_tool` and are untouched by this set. What the exclusion removes is the
agent's own scratch space — which Deep Agents' design assumes exists for planning and
intermediate state across a 16-turn task, and which every `write_file`-shaped instinct in the
model will reach for and be denied.

**It also invalidates the PRD's `T` estimate.** §3.4 assumed the Deep Agents built-ins were
resident on the model surface. Three of them are not.

## Finding 1 — 10 orphan modules, ~4,700 LOC, unit-tested and never imported

Verified: zero `import` of the module anywhere in `src`, and no `__main__`, so these are not
out-of-band entry points either. Each has tests.

| Module                                         | LOC     | What it claims to do                       |
| ---------------------------------------------- | ------- | ------------------------------------------ |
| `observability/context_origin_conformance`     | 1,271   | conformance gate over context origins      |
| `capabilities/workspace/patch_plan`            | 804     | workspace patch planning                   |
| `runtime_worker/jobs/proposal_extractor`       | 620     | proposal extraction job                    |
| `release/e2_final_conformance`                 | 524     | release conformance gate                   |
| `capabilities/concurrency/provider_hints`      | 332     | provider concurrency hints                 |
| `api/inbox_fallback`                           | 316     | inbox fallback path                        |
| `runtime_worker/jobs/approval_expiry_sweeper`  | 245     | expiring stale approvals                   |
| `capabilities/tools/code_tool_adapter`         | 238     | code-tool adapter                          |
| **`context/tool_result_admission_gate`**       | **413** | **tool-result admission + offload writer** |
| `runtime_worker/jobs/encrypt_existing_columns` | 216     | column encryption backfill                 |

`local_release_control_cli` (560 LOC) also scans as an orphan but has `__main__` — a genuine
CLI, correctly excluded.

Two deserve individual attention.

### 1a — `tool_result_admission_gate` is the lever the MCPMark PRD proposes building

413 LOC: `ToolResultAdmissionGate`, `ToolResultAdmissionLedger`, `InProcessOffloadWriter`, a
`ToolResultAdmissionBypassed` error, and `admit_tool_return` with **22 test references**. The
only mention anywhere in `src` is a docstring in `tool_result_admission.py` pointing at its
test module.

This bounds how much of a tool result enters context — i.e. `m` in the PRD's cost model, the
second-largest cost term. **PRD §5's P2-2 ("result field projection") proposes building a
capability that already exists, tested, unwired.** Wiring it is a fraction of the estimated
work, and the estimate should be re-derived before that item is scheduled.

### 1b — `approval_expiry_sweeper` never runs

Approvals are the mechanism the whole `write=ask` posture rests on. The sweeper that expires
stale ones is not scheduled, so pending approvals presumably accumulate without bound. Worth
confirming against a live store before treating it as fact — the scanner proves the module is
unreachable, not what the consequence is.

## Finding 2 — enforcement helpers that are tested and never called

The scanner found 176 public symbols defined in `src`, referenced by tests, referenced by no
other `src` module. Many are legitimate (test doubles, `InMemory*`/`Fixture*` fakes). These
are the ones whose **names describe enforcement**, each verified by grep:

| Symbol                                      | Tests | Location                                            |
| ------------------------------------------- | ----- | --------------------------------------------------- |
| `MemoryPolicyAuthorizer.ensure_authorized`  | 4     | `context/memory/policy.py:166`                      |
| `ToolUsePolicySnapshot.mode_for_tool`       | 3     | `capabilities/tools/permissions.py:88`              |
| `admit_tool_return`                         | 22    | `context/tool_result_admission_gate.py:302`         |
| `verify_audit_log`                          | 6     | `runtime_adapters/file/runtime_api_store.py:2654`   |
| `validate_model_tool_surface`               | 6     | `capabilities/operations/conformance.py:130`        |
| `build_permission_context_from_scope_lists` | 8     | `runtime_worker/jobs/routine_pre_fire_gate.py:389`  |
| `widening_rejections`                       | 3     | `capabilities/concurrency/descriptor_policy.py:423` |

### 2a — memory role policy and prompt-injection rejection never evaluate

**Correction to an earlier draft of this document, which named a class
`MemoryAccessPolicy` that does not exist and called it "unreachable". The real class is
`MemoryPolicyAuthorizer` and it _is_ imported and used.** The unwired part is narrower and
still real:

- `MemoryPolicyAuthorizer.default_policies()` **is** called
  (`context/memory/backends.py:66`) — the path policies are built and attached to each
  `MemoryBackendRoute`.
- `authorize()` — which evaluates those policies — is called **only from inside
  `ensure_authorized`** (`policy.py:178`).
- `ensure_authorized` has no callers.

So policies are loaded onto routes and then never evaluated. Confirming that nothing else
evaluates them: `read_roles` / `write_roles` / `approval_required` appear only in their own
contract, the key-name constants, and inside `authorize`. `PromptInjectionDetector` has
exactly one call site — `policy.py:159`, inside `authorize`.

**Net: the memory role checks and the prompt-injection rejection do not run.** CLAUDE.md
lists memory content as untrusted precisely because a previous turn wrote it; the component
that acts on that rule is attached but never asked.

### 2b — the per-tool classifier is unwired, so reads gate as writes

**Also corrected.** Classification is not absent — it happens at a coarser grain than the
code suggests.

The live path is `ToolUsePolicyGate.decide_for_side_effects`
(`runtime_gate.py:126`), which calls the wired `kind_for_side_effects(...)`. Its own
docstring says why: it "exists for callers that gate an umbrella model tool (e.g.
`call_mcp_tool`) at run-start, where the concrete per-invocation `LoadedToolSpec` is not yet
resolved but the tool's coarse side-effect class is known."

What is unwired is the **per-tool-spec** classifier: `mode_for_tool`
(`permissions.py:88`) and the `_kind_for_tool_policy` it delegates to, which map a _resolved_
tool's `side_effects` / `risk_level` onto read / write / destructive. `_kind_for_tool_policy`
has exactly one caller — `mode_for_tool` — which has none.

**Consequence: the axis is decided once, at run-start, from the umbrella tool's coarse
side-effect class**, which must cover writes because some connector tool writes. So under the
default `write=ask` a read-only connector call is gated identically to a delete. This makes
the MCPMark PRD's §4.3 gate _worse_ than estimated — `G_approval` was scored as "writes need
approval"; in practice every MCP call does.

It is also a product bug independent of any benchmark: users are asked to approve reads.

## Finding 3 — duplicate defaults for one concept

`tool_call_budget` is defaulted twice: **5** in `ModelRuntimeConfig`
([execution/contracts.py:214](../../../services/ai-backend/src/agent_runtime/execution/contracts.py:214),
mirroring `_DEFAULT_TOOL_CALL_BUDGET` which feeds the prompt) and **10** in
`RuntimeExecutionSettings` ([settings.py:207](../../../services/ai-backend/src/agent_runtime/settings.py:207),
which seeds the enforced budget). The production path passes the settings value, so 5 binds
only where a call site omits the field — latent, not live, but both docstrings insist the
prompt and the enforced cap must agree.

## Not reported: env vars

The scanner flags 67 env-var names as "declared but never read". Spot-checking
(`RUNTIME_ALLOW_EMPTY_CAPABILITIES`, `RUNTIME_DB_LOCK_TIMEOUT_MS`) shows these are declared as
`Keys` attributes and read through the attribute, so the literal appears once by design.
**This category is a false positive and is excluded from the findings above.** Fixing the
detector means resolving attribute access, not counting strings.

## What to fix first

1. **`mode_for_tool` (2b)** — a user-visible bug today, and it moves an MCPMark gate.
2. **Memory policy evaluation (2a)** — an untrusted-input control that is attached but never asked.
3. **`tool_result_admission_gate` (1a)** — wire it before scheduling PRD P2-2, which would
   otherwise rebuild it.
4. **`approval_expiry_sweeper` (1b)** — confirm the consequence, then schedule it.
5. **The exclusion set (0)** — decide deliberately whether the agent should have workspace
   write, and fix the `tool_surface.py` docstring either way.

## Method notes and limits

- Reference detection is **textual**. It cannot see dynamic dispatch, registry lookup by
  string, or plugin loading. Every finding above was confirmed with a targeted grep, but a
  symbol invoked purely by dynamic name would read as unwired and would be a false positive.
- The converse hole matters more: the scanner proves a symbol is **unreachable**, never that
  its absence has a consequence. 1b is flagged as needing that second step.
- The scan says nothing about correctness of wired code. It finds one bug class — the one
  that survived 9,775 passing tests, because tests import the symbol directly and never ask
  whether production does.

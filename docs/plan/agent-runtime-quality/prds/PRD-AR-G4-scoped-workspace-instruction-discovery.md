# PRD-AR-G4 — Scoped workspace instruction discovery

**Goal.** Discover user-authored instruction files within granted workspace mounts,
apply them deterministically by directory scope, and expose their provenance to the
agent without scanning the whole workspace, crossing grants, or allowing repository
text to override product safety policy.

| Field           | Value                                                                                                                                                                                     |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Status          | Draft for review                                                                                                                                                                          |
| Primary owner   | `ai-backend` workspace capability and prompt assembly                                                                                                                                     |
| Host impact     | Read-only use of the existing Electron-main workspace authority                                                                                                                           |
| Runtime rollout | `WORKSPACE_INSTRUCTIONS_MODE`: off → report → on                                                                                                                                          |
| Depends on      | A3 Operation Gateway, C1 workspace overlay, C2 workspace authority, C3 workspace product integration, E1 accountability/lifecycle, AR-F2 typed prompt fragments, AR-F5 context allocation |

## Implementer brief

Read before implementation:

1. `../README.md`.
2. `../../prds/PRD-C1-workspace-overlay.md`.
3. `../../prds/PRD-C2-workspace-broker-commit.md`.
4. `../../prds/PRD-C3-workspace-product-integration.md`.
5. `services/ai-backend/src/agent_runtime/capabilities/workspace/contracts.py`.
6. `services/ai-backend/src/agent_runtime/capabilities/workspace/ports.py`.
7. `services/ai-backend/src/agent_runtime/capabilities/workspace/merged_backend.py`.
8. `services/ai-backend/src/agent_runtime/capabilities/workspace/deep_backend.py`.
9. `services/ai-backend/src/agent_runtime/capabilities/desktop/workspace_authority.py`.
10. `services/ai-backend/src/agent_runtime/execution/factory.py`.
11. `services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py`.
12. `services/ai-backend/src/agent_runtime/context/memory/prompt_injection.py`.
13. `services/ai-backend/src/runtime_worker/handlers/run.py`.

Do not add host-path access to Python. Discovery operates only on virtual paths exposed
by the current grant-backed workspace backend.

## Problem statement

Projects frequently carry local guidance about architecture, commands, style, and
directory-specific conventions. The runtime currently tells the agent how to list and
read `/workspace/`, but it does not discover a bounded set of recognized instruction
files or define how root and nested instructions compose.

Without a contract, the agent may omit critical project rules, repeatedly search for
them, read an irrelevant instruction file from another subtree, or accept arbitrary
repository prose as higher-priority product policy.

## Current implementation and predecessor contracts

- **[depends on]** C1–C3 define opaque workspace grants, virtual paths, base+overlay merged reads,
  staged writes, exact review, and Electron-main physical authority.
- **[shipped]** `WorkspaceDeepAgentBackend` already supports bounded `ls`, `read_file`, `glob`, and
  `grep` over authorized virtual paths.
- **[shipped]** Workspace prompt guidance is injected only when the route exists.
- **[shipped]** The runtime has prompt-injection detection and immutable product/tool policy.
- **[depends on]** Symlink, traversal, path, and grant enforcement belong to the broker/workspace
  contracts rather than prompt logic.

## Objectives

1. Discover recognized instruction files with bounded reads and deterministic order.
2. Apply instructions only to tasks/files inside their directory subtree.
3. Compose root→leaf instruction layers with explicit provenance and digest.
4. Preserve product/system/policy precedence over repository instructions.
5. Recompute when the relevant overlay or base file changes.
6. Make discovery observable without logging instruction bodies or physical paths.

### Success measures

- 100% correct applicable-file selection on the hierarchical-scope fixture suite.
- Zero reads outside active grants or across sibling subtrees.
- Initial discovery p95 below 100 ms for a warm desktop workspace and below 500 ms for
  a cold broker read under the configured depth.
- Maximum prompt contribution 12 KiB by default and 24 KiB hard.
- No increase in unapproved host writes or policy/tool-capability changes.

## Non-goals

- Crawling every directory for arbitrary documentation.
- Executing commands found in an instruction file.
- Treating repository instructions as organization policy or approval.
- Discovering files outside active mounts, following symlinks, or resolving physical
  paths.
- Editing instruction files outside the C1/C3 staged workspace flow.

## Interfaces consumed

- C1 merged workspace read and overlay generation.
- C2 opaque grant/mount identity and broker read constraints.
- C3 model-facing workspace backend and staged mutation path.
- F2 typed prompt-fragment assembly and product policy precedence.
- F5 context allocation for instruction-card and loaded-body budgets.
- Existing operation/event and E1 lifecycle contracts.

## Interfaces exposed

### Domain contracts

```text
InstructionFilePolicy
  recognized_names: string[]            # deployment-controlled closed list
  max_depth: int                         # default 12
  max_files: int                         # default 16
  max_file_bytes: int                    # default 32 KiB
  max_total_bytes: int                   # default 128 KiB read / 24 KiB prompt
  precedence: root_to_leaf
  overlay_visibility: merged

WorkspaceTaskScope
  mount_id: string
  working_directory: virtual_path
  target_paths: virtual_path[]           # max 64

WorkspaceInstructionLayer
  instruction_ref: string                # opaque run-scoped reference
  virtual_path: virtual_path
  applies_to_prefix: virtual_path
  content_digest: sha256
  source: base | overlay
  activation_state: committed | preexisting_overlay | staged_inactive |
                    explicitly_activated
  authored_by: user | agent | external
  byte_count: int
  order: int

WorkspaceInstructionPlan
  plan_id: string
  scope_digest: sha256
  workspace_generation: int
  layers: WorkspaceInstructionLayer[]
  truncated: bool
  warnings: string[]
```

The initial recognized name is `AGENTS.md`. Additional names require a reviewed policy
change and explicit precedence; the model cannot choose names.

### Runtime service

```text
WorkspaceInstructionResolver
  resolve(scope, workspace_snapshot) -> WorkspaceInstructionPlan
  open(plan_id, instruction_refs) -> InstructionBlocks
```

Normal task startup may automatically open the small applicable plan. If limits force
progressive loading, the model receives layer cards and uses:

```text
load_workspace_instructions(instruction_refs[])
```

This is an A3 read operation, not a generic path reader.

### Events

```text
workspace.instructions.discovered.v1
workspace.instructions.loaded.v1
workspace.instructions.invalidated.v1
workspace.instructions.truncated.v1
```

Events carry mount-scoped virtual path digests, counts, generation, content digests,
and warnings. Bodies and physical paths are forbidden.

## Design

### D1. Scope algorithm

For each target path, the resolver considers only directory ancestors between the
mount root and the target's containing directory:

```text
/workspace/<mount>/AGENTS.md
/workspace/<mount>/src/AGENTS.md
/workspace/<mount>/src/domain/AGENTS.md
```

It performs direct stat/read checks for recognized names on those ancestors; it does
not recursive-glob the workspace. Duplicate ancestors across target paths are
de-duplicated.

Applicable layers are ordered mount root to nearest directory. A layer applies only to
descendants of its directory. Sibling instructions never apply. Multiple mounts produce
separate plans; there is no cross-mount inheritance.

Complexity is `O(unique_ancestor_directories × recognized_names)`, bounded by target,
depth, file, and byte limits—not workspace size.

### D2. Choosing task scope

The run obtains `working_directory` and target paths from explicit user/host context or
the paths the agent is about to read/edit. It must not let arbitrary document text
declare a new working directory.

At run start, resolve the working-directory chain. Before a read/edit in a previously
unseen subtree, resolve that target's chain and attach any additional applicable layers
to the operation context. The tool wrapper enforces this; prompt cooperation is not the
only boundary.

### D3. Precedence

Effective priority is:

```text
platform/system policy
→ organization/admin policy
→ agent definition and explicit user request
→ applicable workspace instruction layers root→leaf
→ retrieved/document/page content
```

A deeper file refines a shallower workspace file for its subtree but cannot relax
platform, admin, grant, capability, staging, or approval rules. Conflicts with
higher-priority policy are ignored and recorded as a safe warning.

Instruction content is a user-controlled instruction channel only because it resides at
a recognized scoped path inside an explicitly granted workspace. All other repository
files remain untrusted data.

### D4. Prompt representation

Each block is delimited and labeled with virtual path, scope prefix, digest, and source:

```text
<workspace_instruction path="..." applies_to="..." digest="...">
...
</workspace_instruction>
```

The resolver emits an F2 typed `workspace_instructions` fragment; it does not append
free-form text directly to the harness. The prompt includes only the plan applicable to
the current task/tool operation, within the exact allocation granted by F5.
Truncation occurs at section boundaries when possible and is explicit. A hash/index of
omitted layers lets the model load them with the dedicated tool.

### D5. Overlay semantics

Discovery reads the C1 merged view. An overlay create/replace/delete of a recognized
instruction file invalidates subsequent plans, but an agent-authored staged instruction
is a control-plane exception: it is discovered as `staged_inactive` and cannot guide
the run that authored it merely because it exists in the merged overlay.

It becomes eligible only after one of these exact, audited transitions:

1. C2/C3 commits the reviewed file and a new workspace snapshot observes it as
   `committed`; or
2. the user explicitly approves `use_staged_instructions` for the pinned virtual path,
   content digest, overlay generation, and run. The decision activates only that digest
   for that run and does not imply host commit or publication.

Editing the file after approval invalidates the decision. Ordinary staged data files
remain visible through C1; this exception applies only to recognized instruction files
that can influence harness behavior. User-authored or externally preexisting overlays
must carry trusted provenance and an explicit activation state from C1/C3 rather than
being inferred from file text.

The instruction plan pins overlay/base generation and content digests. A tool operation
revalidates the applicable plan if its target subtree or workspace generation changed.
Inactive layers may be shown in review metadata but are excluded from prompt bytes and
F2 prompt-fragment digests.

### D6. Cache and invalidation

Cache by run, mount id, working/target prefix set, grant generation, workspace
generation, recognized-name policy version, and content digests. Negative stat results
may be cached for the same generation.

Grant revocation drops the cache and makes refs unavailable. Overlay changes invalidate
only affected ancestor/subtree plans.

### D7. Safety analysis

The resolver rejects:

- paths outside a mount or with invalid normalization;
- symlink/device/socket content as determined by the broker;
- binary/non-UTF-8 instruction files;
- oversized files/plans;
- files whose read identity changed during read;
- instruction text requesting secret disclosure, capability widening, approval bypass,
  or policy mutation.

Risk detection supplies warnings and may suppress dangerous clauses; structural policy
enforcement remains authoritative even if detection misses.

## Persistence, retention, and deletion

- Instruction bodies are not copied to a durable product table.
- Run state stores plan metadata, virtual refs, digests, and optionally an encrypted
  payload ref subject to run retention for deterministic replay.
- Cached bodies are run-scoped and deleted with the run/history or grant revocation.
- Workspace source files remain governed by C1–C3 and host ownership.
- Legal hold on a run may retain the exact loaded instruction payload needed for audit;
  it does not retain or restore the host file itself.
- Receipt exports list instruction digests/virtual paths only under E1 redaction rules.

## Authorization, privacy, and security

- Only active run-snapshotted grants provide mounts.
- Physical paths, root handles, broker tokens, and native identities never reach the
  model or persistence.
- Every stat/read goes through the broker-backed workspace port with tenant/user/device
  binding.
- Instruction bodies never enter logs, metric labels, audit metadata, or exceptions.
- A file cannot grant tools, permissions, network, workspace access, or approval.
- An agent cannot self-activate instructions it staged; activation is digest-bound,
  user-authorized, and invalidated by mutation.
- Subagents receive only the intersection of parent workspace mounts and task target
  prefixes; delegation does not copy all workspace instructions by default.

## Performance and capacity

- Max target paths 64, depth 12, recognized names initially 1, instruction files 16.
- Per-file read max 32 KiB; aggregate read max 128 KiB; prompt max 12 KiB default/24 KiB
  hard.
- Cold resolve deadline 2 seconds; warm resolve 100 ms target.
- Direct ancestor stats may execute concurrently up to broker limit 8 while preserving
  deterministic result ordering.
- No directory-wide recursive listing or content search.

## Failure, idempotency, and recovery

- Resolve/open are deterministic for the same snapshot and safe to retry.
- Missing recognized files are normal success.
- One unreadable layer yields a warning and preserves other valid layers unless policy
  marks the root layer required.
- Generation/digest drift retries resolution once; repeated drift returns
  `workspace_changing` and blocks an edit rather than using uncertain instructions.
- Grant revocation cancels pending reads and invalidates refs.
- Replay uses retained plan/payload refs and never rereads a changed host file as if it
  were original context.
- Broker/app restart rebuilds the plan from current grants for new work.

## Metrics

- `workspace_instruction_resolve_total{outcome}`
- `workspace_instruction_resolve_duration_ms{cache}`
- `workspace_instruction_layers`
- `workspace_instruction_bytes{phase=read|prompt}`
- `workspace_instruction_truncated_total{reason}`
- `workspace_instruction_invalidated_total{reason}`
- `workspace_instruction_policy_conflict_total{class}`

No mount, path, digest, or instruction text appears in labels.

## Rollout and backout

1. Land contracts/resolver with synthetic workspace fixtures.
2. `report` mode discovers and emits content-free diagnostics but does not alter prompts.
3. Enable for internal read-only workspaces.
4. Enable operation-scoped re-resolution before staged edits.
5. Expand after quality and prompt-injection suites pass.

Backout sets mode off, removes the loader tool/prompt blocks, and invalidates caches.
Workspace grants, overlays, stages, and host files are unchanged.

## Implementation slices

1. Add policy/contracts, plan/ref codecs, and fixture corpus.
2. Implement ancestor resolver over merged workspace reads.
3. Add cache/invalidation and generation/digest revalidation.
4. Integrate run-start and pre-operation resolution.
5. Add prompt blocks and progressive loader.
6. Add events, receipt metadata, metrics, report mode, and quality evals.
7. Add desktop live smoke for grant/revoke/overlay/restart.

## Test plan

### Scope and ordering

- Root only, nested override, sibling isolation, multiple targets, multiple mounts,
  duplicate ancestors, max depth, exact deterministic order.
- Working-directory changes and operation-target re-resolution.
- Base/overlay create/replace/delete and generation invalidation.
- Agent-authored `AGENTS.md` create/replace remains inactive across re-resolution;
  exact user activation and commit make only the approved digest eligible.
- Post-approval edit, overlay-generation race, forged author provenance, and sibling
  subtree activation fail closed.

### Security

- Forged virtual paths, traversal, symlink/device/binary/oversized files, grant
  revocation, cross-mount refs.
- Instructions attempting tool/policy/approval/capability/secret changes have no effect.
- Subagent target-prefix narrowing.

### Recovery and lifecycle

- Broker timeout, identity drift during read, repeated workspace mutation, app/worker
  restart, replay, history deletion, legal hold.

### Quality and performance

- Repository-specific command/style/architecture tasks with root/nested instructions.
- Instruction adherence, incorrect sibling-rule application, prompt bytes, stat/read
  count, cold/warm latency, and no-instruction control tasks.

## Definition of done

- [ ] Applicable recognized instruction files are discovered by bounded ancestor reads.
- [ ] Root→leaf ordering and subtree scope are deterministic and tested.
- [ ] Product/admin policy always outranks workspace instructions.
- [ ] Grants, virtual paths, overlay generation, deletion, and replay remain correct.
- [ ] Prompt contribution and broker calls stay within hard budgets.
- [ ] Report/on rollout meets quality, security, and performance launch gates.

## Guardrails

- No recursive workspace crawl.
- No physical paths or direct host reads.
- No repository file may widen authority or bypass staged effects.
- No recognized instruction staged by the agent may influence that run without an
  exact user activation decision or committed successor snapshot.
- No sibling or cross-mount instruction inheritance.
- No silent use of changed instructions during replay.

## Open decisions

- Whether to recognize one additional conventional filename at launch; any addition
  requires an explicit precedence and migration rule.
- Whether policy-conflicting clauses are omitted from the model block or included with
  a machine-generated “ignored” annotation.

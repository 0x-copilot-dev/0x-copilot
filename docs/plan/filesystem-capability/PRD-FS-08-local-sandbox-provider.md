# PRD-FS-08 — Local sandbox provider: execution with patch-back

**Status:** specified

**Depends on — corrected by the FS-08 reconciliation pass. FS-01 is _not_ the
read dependency this PRD's first draft claimed.**
[FS-01's Out of scope](PRD-FS-01-platform-seam.md) excludes, in terms, "the
`native/workspace-fs` N-API read-side addon, `host-fs.ts`, and everything on the
read path" — **and excludes FS-08 by name**. `fs_open_root` / `fs_stat_at` /
`fs_open_read_at` are real [FS-01 §2](PRD-FS-01-platform-seam.md) spellings, but
they are _commit-helper_ primitives inside a spawned, single-purpose C process
reachable only over the helper's private command channel from desktop main
([FS-01 §2](PRD-FS-01-platform-seam.md)'s `fs_bootstrap_acquire` block). They are
not a read API, nothing above the helper calls them, and no Python service can.
FS-08 depends on FS-01 only **negatively**: it adds no seam member and declares
no verb in `fs_platform.h`.

The read surface FS-08 actually consumes already exists and is the broker's:
`/v1/fs/stat`, `/v1/fs/list`, `/v1/fs/read`, `/v1/fs/glob`, `/v1/fs/grep`
([broker.ts:80-94](../../../apps/desktop/main/capabilities/broker.ts),
`ADVERTISED_METHODS` at `:97-112`), consumed from ai-backend by
[broker_client.py:88-92](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/broker_client.py).

Real dependencies:

- **FS-03 — hard, on Windows.** The Win32 confined read is source-only today:
  `native/workspace-fs`'s `NtCreateFile` walk "is built by no script, is carried
  by no `extraResources` entry" ([README](README.md), FS-03 C3), so a packaged
  Windows install falls back to the non-atomic `realpath`-recheck path. D17.1's
  base-file snapshot reads inherit that. FS-08's first draft omitted FS-03
  entirely.
- **FS-02 / FS-04 / FS-05 / FS-06** — the five commit verbs an imported patch
  eventually redeems. FS-08 **emits** all five and lands them on the existing C2
  lane. A patch entry whose verb has not shipped is refused **at prepare**, for
  the whole change set — not per entry and not at commit. See Out of scope.
- **FS-07** — an imported patch is committed through the same claim/journal lane
  and inherits its reconciliation.

**FS-09 — a shipping dependency, not an import one, and the gap it used to leave
is closed.** FS-09's Out of scope used to disclaim FS-08 by name, so the six
surfaces this PRD routes there were owned by nobody. That is decided: execution
consent is consent, and splitting it across two documents would produce two
consent models, so **FS-09 owns every surface where a human is asked to agree to
any of this** — enabling execution
([FS-09 D20](PRD-FS-09-enablement-consent.md)), what the user is told when there
is no runtime (D21), the image-download ask (D22), what leaves the granted root
(D23), the review of an imported patch including the unsupported-verb pre-check
(D24), and revocation while a sandbox is live (D25). **FS-08 keeps the provider,
the runtime and its drivers, the isolation probe and attestation, the image
contract, transfer, cancellation and teardown, the C1 importer and the desktop
prepare/authorize lane.** Nothing in FS-08's code depends on FS-09, and nothing
in FS-08 becomes user-reachable without it. What FS-09 took, what it did not, and
the one question it sent back are at "Consent surfaces — routed, and where they
landed" at the end of Open questions.

## Implementer brief

The README's locked decision **D1** says execution runs in a local sandbox and
that `run_in_sandbox` is "~70% built, missing a local provider". That claim is
accurate and this PRD is the only thing that redeems it: `providers/` holds
`langsmith.py` and `openai_hosted.py`, both remote, and **neither has ever
returned `isolation_ready == True`**
([langsmith.py:62](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/providers/langsmith.py),
[openai_hosted.py:505](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/providers/openai_hosted.py)),
so the capability has never produced a model-visible tool from any provider.

You are **implementing the existing `SandboxProviderPort`**
([ports.py:53-97](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/ports.py))
with a local container runtime, plus the one C1 importer that the composition
root already names as a missing prerequisite. You are **not** building a second
subsystem, a second execution path, or a shell. Twenty-five modules —
snapshot sealing, transfer, patch collection, artifact publication, lifecycle
FSM, durable cleanup, usage metering, the operation gateway adapter, the model
tool — are already written and are consumed **unchanged**. If your change list
grows a new port, a new tool, a new gateway, or a second way for bytes to reach
the user's disk, you have left this PRD.

Three things make this a real slice rather than "write an adapter":

1. `SandboxIsolationAttestation.satisfies` accepts `isolation` of
   `"container"` or `"microvm"` and **rejects `"process"`**
   ([contracts.py:386-401](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py)).
   Seatbelt on macOS and AppContainer on Windows are process sandboxes. So the
   local provider is a container provider on both platforms, or the capability
   stays absent. That is a product consequence, not an implementation detail,
   and D2 argues it rather than assuming it.
2. `FileSandboxAuthorityPrerequisites.resolve` returns `None`
   **unconditionally**
   ([sandbox_composition.py:116-135](../../../services/ai-backend/src/runtime_worker/sandbox_composition.py)),
   which keeps the tool dark on every path regardless of provider readiness. Its
   docstring names the three authorities that must exist first. A local-provider
   PRD that does not discharge all three ships a capability the model can never
   see.
3. **Patch import is a second writer into C1, and the reconciliation pass caught
   it.** Every overlay revision that exists today is produced by
   `_WorkspaceOverlayMutationEngine`
   ([overlay.py:113-570](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/overlay.py))
   under the operation gateway's adapter
   ([effects.py:254](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/effects.py));
   `WorkspaceOverlayStorePort.append_revision` has **no other caller in the
   repository**. An importer that calls the store directly bypasses that
   engine's limit check, its precondition derivation, and the operation record —
   which is the spine's "no second write path" guardrail arriving one layer
   above the host. D12 now pays for the bypass explicitly instead of not
   noticing it.

## Context

Everything below was verified against `main@b349aca2`. Nothing is taken on the
strength of an existing document's assertion.

### C1. `run_in_sandbox` is built end to end, and dark at seven independent gates

The call chain that already exists:

```
model → StructuredTool "run_in_sandbox"                     execute_tool.py:161
      → OperationGateway + SandboxOperationAdapter           operation_adapter.py:239
      → SandboxSnapshotBuilder.materialize                   snapshot.py:296
      → SandboxLifecycleOperationRunner                      operation_runner.py:112
      → SandboxLifecycleCoordinator.run                      coordinator.py (durable)
      → RemoteExecutionService.create/teardown               remote_execution_service.py:99
      → SandboxProviderRegistry → SandboxProviderPort        provider_registry.py:23
      → SandboxHandle.backend wrapped in PolicyEnforcedSandboxBackend
                                                             policy_backend.py:69
```

Seven gates keep it absent, and only one of them is a provider problem:

| #   | Gate                                                                                                                          | Where                                                                                                                                        |
| --- | ----------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Config: `RUNTIME_ENABLE_REMOTE_SANDBOX` truthy **and** `RUNTIME_SANDBOX_PROVIDER` parses to an enum member                    | [config.py:172-218](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/config.py); truthy set is `{1,true,yes,on}` at `:44` |
| 2   | Readiness: `SandboxCapabilityReadiness.assess`                                                                                | [readiness.py:43-75](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/readiness.py)                                       |
| 3   | Registry construction, including `if not provider.isolation_ready: raise SANDBOX_POLICY_UNSUPPORTED`                          | [provider_registry.py:70-74](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/provider_registry.py)                       |
| 4   | Seam: unavailable ⇒ `build_sandbox_backend` returns `None`, so the tool is absent from the toolset — **there is no fallback** | [seam.py:63-66](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/seam.py)                                                 |
| 5   | Worker composition: desktop profile, file store, **the unconditional `None`**, and four duck-typed authorities                | [sandbox_composition.py:90-91, 116-135, 181-204](../../../services/ai-backend/src/runtime_worker/sandbox_composition.py)                     |
| 6   | Tool build **and** a per-invocation availability recheck before any dispatch                                                  | [execute_tool.py:82-96](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/execute_tool.py)                                 |
| 7   | Per-create attestation: `attest()` then `satisfies(request.egress)`                                                           | [remote_execution_service.py:131-136](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/remote_execution_service.py)       |

Gate 7 runs on **every** provisioning, not only at startup. Gate 6's recheck
means a provider that becomes unavailable mid-run returns
`{"status":"unavailable","reason":…}` without dispatching.

### C2. The port is six methods — and a second, hidden interface decides which of them runs

`SandboxProviderPort` ([ports.py:53-97](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/ports.py)):
`create`, `isolation_ready` (property), `attest`, `status`, `terminate`,
`list_owned_sessions`. `create` must be idempotent on
`request.idempotency_key` (`:63-65`); `terminate` must be idempotent (`:89-91`).

There is a second protocol that is **not** in `ports.py`:
`SandboxGuardedProvisioner`
([provisioning.py:141-163](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/provisioning.py)).
`RemoteExecutionService.__init__` does `isinstance(provider, SandboxGuardedProvisioner)`
([remote_execution_service.py:118-124](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/remote_execution_service.py))
and, when it matches, **never calls `create()`** — provisioning routes through
`_create_guarded` (`:139-145`), which requires a durable cleanup store (`:334-338`)
and persists a `state="provisioning"` reservation **before the provider is
touched** (`:345-358`). The capability it mints is sealed by object identity
([provisioning.py:22, 61-66, 128-138](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/provisioning.py)).

`runtime_checkable` matches on **method names only**, so a provider that happens
to define those four names silently takes the guarded path. FS-08 opts in
deliberately (D10) rather than acquiring the behaviour by accident.

### C3. `satisfies()` is a nine-control conjunction, and `"process"` is a legal field value it rejects

[contracts.py:364-401](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py):

```
isolation ∈ {"container", "microvm"}        # "process" is declarable (:373) and REFUSED (:389)
process_isolated ∧ filesystem_fresh ∧ teardown_guaranteed ∧ host_credentials_absent
cpu_quota_enforced ∧ memory_quota_enforced ∧ wall_clock_quota_enforced
process_quota_enforced ∧ file_quota_enforced
egress_mode == policy.mode
```

`attestation_ref` is free text, 1-2048 chars (`:384`). **Nothing in the runtime
parses or checks it.** Attestation is provider self-assertion; the only
structural defence is that a provider must be willing to assert all nine.

Launch is unconditionally deny-all: `SandboxOperationLaunch` freezes
`egress_mode="deny_all"` and refuses secrets
([operation_adapter.py:148-164](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/operation_adapter.py)),
and the runner constructs `SandboxEgressPolicy(mode=launch.egress_mode)`
([operation_runner.py:211](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/operation_runner.py)).
`SandboxEgressPolicy`'s own docstring says a shape-valid policy is
**proposed, not enforced** until a provider compiles it
([contracts.py:105-113](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py)).
FS-08 is the first provider that must actually compile it.

### C4. What the runtime really calls on a provider's backend, in order

| Call                                                          | Site                                                                                                                                                                                                                                               | Notes                                                                                                                                                                  |
| ------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `a_upload_files(files)` else `to_thread(upload_files, files)` | [runtime_adapter.py:61-65](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/runtime_adapter.py); façade probe at [policy_backend.py:135](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/policy_backend.py) | whole-file, in RAM                                                                                                                                                     |
| `prepare_execution(request)`                                  | [runtime_adapter.py:87-92](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/runtime_adapter.py)                                                                                                                                 | optional, duck-typed                                                                                                                                                   |
| `aexecute(command)` — **no timeout argument is passed**       | [runtime_adapter.py:93](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/runtime_adapter.py)                                                                                                                                    | the façade clamps to `command_timeout_s` ([policy_backend.py:103-115, 212-218](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/policy_backend.py)) |
| `a_download_files(paths)` else `to_thread(download_files, …)` | [runtime_adapter.py:128-133](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/runtime_adapter.py)                                                                                                                               |                                                                                                                                                                        |
| `als(directory)` — recursive walk                             | [patch_collector.py:63](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/patch_collector.py)                                                                                                                                    | see the budget defect below                                                                                                                                            |
| `download_files([path])` per file                             | [patch_collector.py:154-168](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/patch_collector.py)                                                                                                                               | synchronous, moved off-loop                                                                                                                                            |

Three consequences that are easy to miss and are load-bearing for a local
backend:

**(a) `als` is not free — it costs one command from the session budget, and a
provider-side override cannot make it free.** This paragraph was wrong in the
first draft in a way that mattered, so it is spelled out by symbol rather than by
line number:

- Pinned deepagents `BaseSandbox.ls` builds a `python3 -c "…os.scandir…"` string
  (`_build_ls_cmd`) and calls `self.execute(cmd)`; `BaseSandbox` **overrides**
  `als` to `await self.aexecute(_build_ls_cmd(path))`. The
  `await asyncio.to_thread(self.ls, path)` form the first draft cited is
  `BackendProtocol.als`, which `BaseSandbox` does not use.
- `PolicyEnforcedSandboxBackend` **is itself a `BaseSandbox`**
  ([policy_backend.py:69](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/policy_backend.py))
  and overrides `ls` only to guard the path before calling `super().ls(path)`
  (`:166-168`). It does not override `als` at all.
- The collector calls `active.backend.als(directory)`
  ([patch_collector.py:63](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/patch_collector.py)),
  and `ActiveSandbox.backend` **is** the façade
  ([remote_execution_service.py:77-84, 169](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/remote_execution_service.py)).

Composed: `façade.als` → `BaseSandbox.als` → `self.aexecute` →
`PolicyEnforcedSandboxBackend.aexecute` → `self._budget.consume()`
(`:94-115`) → only then `self._delegate.aexecute(...)`. **The delegate's `ls` and
`als` are never consulted on this path.** `commands_per_session` defaults to
**64** ([config.py:59](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/config.py)),
and `DeepAgentArtifactPatchCollector` pushes **every** discovered directory onto
its walk (`patch_collector.py:54-76`), so a workspace with more than ~60
directories exhausts the budget mid-collection and raises
`SANDBOX_COMMAND_BUDGET_EXCEEDED` (`policy_backend.py:61-65`) — a successful
command whose patch cannot be collected.

The consequence for the design: **a native `ls` on the provider backend does not
fix this**, and D8's first draft said it did. D8 now names the three admissible
fixes and the one of them that forces an edit D1 otherwise forbids.

**(b) `BaseSandbox.ls` requires `python3` inside the sandbox.** The pinned image
is therefore not free to be `scratch` or `busybox`.

**(c) The repo's async transfer spellings differ from deepagents'.** The façade
probes `a_upload_files` / `a_download_files`
([policy_backend.py:135, 152](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/policy_backend.py));
pinned deepagents names its variants `aupload_files` / `adownload_files`
(`backends/protocol.py:599, 621`). The OpenAI backend uses the repo spelling
([openai_hosted.py:266, 307](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/providers/openai_hosted.py)).
A local backend that defines only the deepagents spellings silently runs every
transfer on a worker thread.

The façade also applies, provider-independently: the command budget, timeout
clamping, output truncation to `combined_command_preview_bytes`
([policy_backend.py:220-236](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/policy_backend.py)),
and a hard `/workspace` prefix guard on every path (`:200-210`).

### C5. Files in: the snapshot is reference-only and is sealed before a provider sees a byte

`SandboxSnapshotPlan` carries refs and virtual paths, never bytes;
`SandboxSnapshotBuilder.materialize` resolves them and enforces the ceilings
([snapshot.py:296-359](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/snapshot.py)):
an empty plan is `SANDBOX_SNAPSHOT_REQUIRED` (`:308-312`) — there is no
empty-workspace convenience — and entry/total/count overruns are
`SNAPSHOT_QUOTA_EXCEEDED` (`:332-358`). Defaults: 10 000 entries, 512 MiB total,
64 MiB per entry (`:244-249`).

`SealedSandboxSnapshotFileStore` streams every resolved source into a
`0o700`/`0o600` spill file, hashing and counting as it goes, aborting mid-stream
on over-size, and only then makes it readable
([snapshot_file_store.py:69-261](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/snapshot_file_store.py)).
`SandboxSnapshot`'s own docstring forbids a host path, grant, broker handle,
root identity or credential from appearing in it
([contracts.py:202-209](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py)).

**What the snapshot contains today is narrower than the model implies.**
`RuntimeWorkerOverlaySnapshotPlanAuthority.load_plan` selects `FILE` entries
from one **retained C1 overlay manifest version** and nothing else
([sandbox_snapshot_authority.py:88-100](../../../services/ai-backend/src/runtime_worker/sandbox_snapshot_authority.py)).
There is no base-file (host) contribution. That is exactly the first authority
the kill switch names ("a retained full C1 base-plus-overlay snapshot
exporter").

The module's own docstring is a constraint on how that gap may be closed: keeping
this adapter in the worker "makes it impossible for a model tool to select a live
workspace manifest **or a host filesystem path**"
([sandbox_snapshot_authority.py:1-8](../../../services/ai-backend/src/runtime_worker/sandbox_snapshot_authority.py)).
Any base-file contribution must therefore arrive as content resolved through a
capability the worker already holds — the broker read surface — and never as a
host path this module names. D17.1 is rewritten accordingly; the first draft said
"read through FS-01's confined read surface", which is neither a thing FS-01
owns nor a thing a Python service can call.

### C6. Files out: the patch is declarative, and applying it is unwired by construction

`WorkspacePatchManifest`
([contracts.py:309-322](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py))
carries `baseline_manifest_sha256`, `entries`, `complete`, `manifest_sha256`.
Each `WorkspacePatchEntry` (`:243-306`) is one before/after fact with
per-operation evidence rules enforced by a validator (`:261-306`) — `create`
and `replace` need `result_digest`+`result_size_bytes`+`result_ref` with the ref
matching the declared bytes; `replace` additionally needs `baseline_digest`;
`delete` needs only `baseline_digest`; `move` needs `source_path`+`baseline_digest`;
`mkdir` may carry no file evidence.

The patch is published to A2 as
`{"v":1,"kind":"sandbox_patch","patch":…}` and reduced to a
`SandboxPatchManifestRef` that refuses `complete=False`
([operation_runner.py:324-378](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/operation_runner.py),
[operation_adapter.py:116-145](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/operation_adapter.py)).
The model sees only `patch_ref`
([execute_tool.py:155-156](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/execute_tool.py)).

Applying it is a separate, **unwired** operation: `coordinator.import_patch`
requires a `_patch_importer` that composition never supplies
([coordinator.py:213-232](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/coordinator.py); the
coordinator is constructed without one at
[sandbox_composition.py:252-264](../../../services/ai-backend/src/runtime_worker/sandbox_composition.py)),
and `SandboxPatchImportRequest` refuses an incomplete patch at the boundary
([contracts.py:343-347](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py)).
The runner's own comment states the rule: the patch "is a reviewable proposal,
not a write" and "neither the overlay nor the host workspace changes during
command completion"
([operation_runner.py:164-168](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/operation_runner.py)).

### C7. The kill switch dominates every other gate

```python
# sandbox_composition.py:135
del runtime_context, layout
return None
```

`FileSandboxAuthorityPrerequisites.resolve` returns `None` unconditionally, and
its docstring is explicit that a provider double or an A2-shaped mock must not
be able to change that answer
([sandbox_composition.py:116-135](../../../services/ai-backend/src/runtime_worker/sandbox_composition.py)).
The three named authorities are a retained full C1 base+overlay snapshot
exporter, a durable A2 result-and-deliverable publisher, and an explicit
user-triggered C1 patch importer.

The second one is **half-real** and that must be said precisely. The patch and
result documents already publish through A2 and return immutable revision refs
([result_publisher.py](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/result_publisher.py),
[artifact_publisher.py:39-64](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/artifact_publisher.py)).
What does not exist is a **revision-aware deliverable** publisher: the gateway
launches with `deliverables=()`
([operation_runner.py:219](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/operation_runner.py))
and `_publish_result` raises if any artifact comes back, with a comment saying
the deliverable port returns digest/size but not an immutable revision URI
(`:299-307`). D17 resolves this honestly instead of quietly reinterpreting the
prerequisite.

### C8. Confinement today confines our own services, not a sandbox — and its evidence standard is the one to copy

`MacosWorkspaceConfinement`
([macos-workspace-confinement.ts:43](../../../apps/desktop/main/services/macos-workspace-confinement.ts))
wraps the three supervised Python children in a Seatbelt profile whose
allow-list deliberately excludes every user-granted workspace root
(`buildMacosWorkspaceSeatbeltProfile`, `:136-170`), and which ends with
`(allow network*)` (`:167`). It is available only when
`process.platform === "darwin" && executableExists("/usr/bin/sandbox-exec")`
(`:57-59`), and the launcher path is an absolute constant that is never resolved
through `PATH` (`:9-10`).

Two facts from it carry into FS-08 unchanged:

- **`verify()` proves the profile parses, not that it denies.** It runs
  `sandbox-exec -p <profile> /usr/bin/true` and treats exit 0 as `enforced`
  (`:88-97`). FS-03 C2 W1 names this as a weakness and FS-03 D2 replaces the
  standard with **observed denial**. FS-08 adopts observed denial from the
  start (D6).
- **Absolute, main-computed launcher paths only.** FS-03 D8 applies the same
  rule to `copilot-confine.exe`. FS-08 applies it to the container runtime
  binary.

Windows has no equivalent today: FS-03 C5 establishes that Node cannot spawn
with a token and that every Windows confinement mechanism is applied at process
creation, and FS-03 D3 ranks AppContainer / restricted token / low integrity
level — all three of which are **process** isolation.

### C9. The five-verb vocabulary is already identical in three places

This is the fact that makes patch-back a mapping rather than an invention:

| Where                | Declaration                                                                                                                                                  |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Sandbox patch entry  | `Literal["create","replace","delete","move","mkdir"]` — [contracts.py:251](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py) |
| C1 overlay entry     | `WorkspaceOperation` StrEnum, same five — [workspace/contracts.py:42-48](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/contracts.py) |
| Desktop change entry | `WorkspaceOperation` union, same five — [workspace-authority.ts:82-87](../../../apps/desktop/main/capabilities/workspace-authority.ts)                       |

The overlay additionally has `OverlayMutation` / `OverlayMutationKind`
(`upsert` / `remove`) and `WorkspaceEntryKind`
(`file`/`directory`/`tombstone`/`move`)
([workspace/contracts.py:35-40, 56-59, 348-368](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/contracts.py)),
plus `BasePrecondition` with an exactness validator (`:202-244`), and
`append_revision(run_id, expected_version, mutations)` with
compare-and-swap semantics
([workspace/ports.py:63-70](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/ports.py)).
Blobs are content-addressed: `content_ref_for_blob` requires a sha256 key
(`workspace/contracts.py:141-147`), `blob_key_from_content_ref` recovers it
(`:149-156`), and `ArtifactService` asserts
`stat.blob_key == revision.content_digest`
([artifacts/service.py:489](../../../services/ai-backend/src/agent_runtime/artifacts/service.py)) —
so a patch entry's `result_digest` **is** the blob key.

**One divergence that will bite.** C1 virtual paths are
`/workspace/<mount>/...` — `normalize_virtual_path` raises "Workspace path must
include a mount" when the first segment after `/workspace` is missing
([workspace/contracts.py:110-112](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/contracts.py)).
Sandbox virtual paths only require the `/workspace/` prefix
([snapshot.py:40-67](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/snapshot.py)).
A command that writes `/workspace/out.txt` therefore produces a patch entry C1
cannot address at all. D14 handles it.

### C10. C1 has exactly one writer today, and it does four things the patch does not carry

Added by the FS-08 reconciliation pass, because the first draft's importer went
straight to the store.

`WorkspaceOverlayStorePort.append_revision`
([workspace/ports.py:63-70](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/ports.py))
has **one** production caller: `_WorkspaceOverlayMutationEngine`
([overlay.py:113-568](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/overlay.py)),
reached only through `WorkspaceOperationPort` → `OperationGateway` →
`WorkspaceOperationAdapter`
([operation_port.py:1-8](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/operation_port.py),
[effects.py:254](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/effects.py)).
Four things happen inside that engine and nowhere else:

1. **`_check_limits`** (`overlay.py:601-617`) — per-manifest entry-count and
   total-byte ceilings, checked against the manifest the mutation lands on.
2. **`_precondition_for_base`** (`:570-592`) — builds the `BasePrecondition` from
   the base entry, filling `opaque_generation`, `content_digest`,
   `stable_file_id`, `byte_size` and `mtime_ns`.
3. **`_merged_entry_exists`** (`:595-599`) — resolves existence across overlay
   **and** base before choosing `MUST_EXIST` / `MUST_NOT_EXIST`.
4. The operation record itself: a gateway disposition, which is what makes
   "what did the agent change?" answerable.

`BasePrecondition`'s validator
([workspace/contracts.py:200-241](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/contracts.py))
only _requires_ `entry_kind` for `MUST_EXIST` and `content_digest` for a file, so
a precondition built from a patch entry's `baseline_digest` alone is
**constructible and strictly weaker** than every other proposal in the system —
no `stable_file_id`, no `opaque_generation`, no `byte_size`, no `mtime_ns`. That
weakness propagates all the way to the desktop's `WorkspacePrecondition`
(`stableId`, `sha256` —
[workspace-authority.ts:68-74](../../../apps/desktop/main/capabilities/workspace-authority.ts))
and therefore to what the helper re-checks at commit. D12 closes it; it is not a
detail.

## Interfaces consumed

Consumed **unchanged** — if you find yourself editing one of these, stop:

- `SandboxProviderPort`, `SandboxHandle` ([ports.py:37-97](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/ports.py)).
- `SandboxGuardedProvisioner`, `SandboxProvisioningCapability` ([provisioning.py:40-163](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/provisioning.py)).
- `SandboxCreateRequest`, `SandboxIsolationAttestation`, `ManagedSandboxSession`,
  `SandboxEgressPolicy`, `WorkspacePatchManifest`, `SandboxPatchImportRequest`,
  `SandboxErrorCode` ([contracts.py](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py)).
- `RemoteExecutionService`, `PolicyEnforcedSandboxBackend`,
  `DeepAgentSandboxRuntime`, `DeepAgentArtifactPatchCollector`,
  `SealedSandboxSnapshotFileStore`, `WorkspaceManifestBuilder`,
  `WorkspacePatchBuilder`, `SandboxLifecycleCoordinator`,
  `FileSandboxCleanupStore`, `FileSandboxLifecycleStore`, the usage meter, the
  operation adapter/descriptor and `SandboxExecuteToolFactory`.
- Pinned deepagents `SandboxBackendProtocol` / `BaseSandbox`
  (`backends/protocol.py:769`, `backends/sandbox.py:341`).
- C1: `WorkspaceOverlayStorePort`, `OverlayEntry`, `OverlayMutation`,
  `BasePrecondition`, `WorkspaceOverlayVersionRef`, `content_ref_for_blob`
  ([workspace/](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/)).
- A2: `ArtifactBlobStorePort.stat` / `open_stream`
  ([artifacts/ports.py:100-128](../../../services/ai-backend/src/agent_runtime/artifacts/ports.py)).
- Desktop C2: `LocalWorkspaceAuthority.prepareChangeSet` / `uploadPreparedContent`
  / `sealPreparedContent` / `authorizeCommitFromUserDecision` /
  `commitPreparedChangeSet`
  ([workspace-authority.ts:457, 549, 572, 602, 656](../../../apps/desktop/main/capabilities/workspace-authority.ts)),
  and FS-04 §2's `WorkspaceContentSource` union.
- Desktop read surface (host base files, D17.1): the broker's `/v1/fs/*` routes
  ([broker.ts:80-94](../../../apps/desktop/main/capabilities/broker.ts)) through
  [broker_client.py](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/broker_client.py).
- **Not consumed: any FS-01 seam member.** FS-08 adds no C seam member, declares
  no verb in `fs_platform.h`, and calls none. See the corrected dependency note
  at the top.

**One consumed interface does not exist yet as a declared type.** FS-04
introduces `origin` on the change set in prose only — its §5 doc comment
(`origin === "local_restore"`) and D6 ("`prepareLocalRestore` marks the change
set `origin: "local_restore"`") — and declares no union for it in its Interfaces
exposed, where `WorkspaceContentSource` and `WorkspaceChangeEntry` _are_
declared. `WorkspaceChangeSet` on `main@b349aca2` has no `origin` field at all
([workspace-authority.ts:92-105](../../../apps/desktop/main/capabilities/workspace-authority.ts)).
So §7 below cannot "add a member to FS-04's union": whichever of FS-04 or FS-08
lands first **declares** the union and the field, and the second adds its member.
This is the same ownership rule FS-03 D1 and FS-06 use for `commit_entry`.

## Interfaces exposed

### 1. One new provider id (Python)

```python
# agent_runtime/capabilities/sandbox/contracts.py — SandboxProviderId (:38)
class SandboxProviderId(StrEnum):
    LANGSMITH = "langsmith"
    OPENAI_HOSTED_CONTAINER = "openai_hosted_container"
    LOCAL_CONTAINER = "local_container"          # new
```

The enum is closed and `config.py:210-218` parses `RUNTIME_SANDBOX_PROVIDER`
through it, so adding a provider is a code change, not configuration. That is
correct and is not changed.

### 2. Local provider configuration (Python)

Mirrors `OpenAIHostedContainerConfig`
([config.py:103-145](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/config.py))
exactly — same `from_env` shape, same fail-closed treatment on a parse error
(`:188-195`).

```python
# agent_runtime/capabilities/sandbox/config.py

class LocalSandboxRuntimeKind(StrEnum):
    """Closed set of runtimes the driver knows how to spell arguments for."""
    APPLE_CONTAINER = "apple_container"   # macOS 26+/arm64, per-container VM
    PODMAN = "podman"
    DOCKER = "docker"


class LocalContainerConfig(RuntimeContract):
    """Deployment-owned local execution policy. The model never reaches it."""

    runtime_kind: LocalSandboxRuntimeKind
    #: Absolute path, supplied by desktop main. NEVER resolved through PATH.
    runtime_path: str = Field(min_length=1, max_length=4096)
    #: Digest-pinned image. A tag-only reference is refused (D5).
    image_ref: str = Field(pattern=r"^[a-z0-9][a-z0-9._/\-]{0,254}@sha256:[0-9a-f]{64}$")
    cpu_millicores: int = Field(default=2000, ge=250, le=16_000)
    memory_limit_bytes: int = Field(default=2 * 1024**3, ge=512 * 1024**2, le=32 * 1024**3)
    pids_limit: int = Field(default=256, ge=16, le=4096)
    workspace_tmpfs_bytes: int = Field(default=768 * 1024**2, ge=64 * 1024**2)
    tmp_tmpfs_bytes: int = Field(default=256 * 1024**2, ge=16 * 1024**2)
    #: Bounded ceiling for the boot probe; a slow or hung runtime is unavailable.
    probe_timeout_s: int = Field(default=45, ge=5, le=180)

    @model_validator(mode="after")
    def _quotas_are_internally_consistent(self) -> "LocalContainerConfig":
        # tmpfs pages are charged to the container's memory limit, so the two
        # RAM-backed mounts plus working headroom must fit inside it.
        headroom = 256 * 1024**2
        if self.workspace_tmpfs_bytes + self.tmp_tmpfs_bytes + headroom > self.memory_limit_bytes:
            raise ValueError("local sandbox tmpfs sizes exceed the memory limit")
        return self

    @classmethod
    def from_env(cls, source: Mapping[str, str]) -> "LocalContainerConfig": ...
```

Environment names, added to `_EnvFields` ([config.py:30-44](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/config.py)):
`RUNTIME_SANDBOX_LOCAL_RUNTIME`, `RUNTIME_SANDBOX_LOCAL_RUNTIME_PATH`,
`RUNTIME_SANDBOX_LOCAL_IMAGE`, `RUNTIME_SANDBOX_LOCAL_CPU_MILLICORES`,
`RUNTIME_SANDBOX_LOCAL_MEMORY_BYTES`, `RUNTIME_SANDBOX_LOCAL_PIDS_LIMIT`,
`RUNTIME_SANDBOX_LOCAL_WORKSPACE_TMPFS_BYTES`,
`RUNTIME_SANDBOX_LOCAL_TMP_TMPFS_BYTES`, `RUNTIME_SANDBOX_LOCAL_PROBE_TIMEOUT_S`.

`RemoteSandboxConfig` gains `local_container: LocalContainerConfig | None`,
resolved in `from_env` with the same `except (TypeError, ValueError): provider = None`
treatment as the OpenAI branch (`:189-195`).

### 3. The runtime driver — provider-private, argv only

Not a port. It is an internal seam so the two platform differences (binary and
argument spelling) live in one table instead of being sprinkled through the
provider.

```python
# agent_runtime/capabilities/sandbox/providers/local_runtime.py

@dataclass(frozen=True)
class LocalRuntimeInvocation:
    """One argv, never a shell string."""
    argv: tuple[str, ...]
    stdin: bytes | None = None
    timeout_s: int | None = None


@dataclass(frozen=True)
class LocalRuntimeResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False


class LocalSandboxRuntimeDriver(Protocol):
    """Argument spelling for one runtime kind. No policy lives here."""

    @property
    def kind(self) -> LocalSandboxRuntimeKind: ...
    @property
    def isolation_kind(self) -> Literal["container", "microvm"]: ...

    def run_detached(self, *, name: str, labels: Mapping[str, str],
                     config: LocalContainerConfig) -> LocalRuntimeInvocation: ...
    def exec_in(self, *, name: str, argv: Sequence[str],
                workdir: str) -> LocalRuntimeInvocation: ...
    def copy_in(self, *, name: str, dest_dir: str) -> LocalRuntimeInvocation: ...   # tar on stdin
    def copy_out(self, *, name: str, path: str) -> LocalRuntimeInvocation: ...      # tar on stdout
    def remove(self, *, name: str) -> LocalRuntimeInvocation: ...
    def kill(self, *, name: str) -> LocalRuntimeInvocation: ...
    def inspect(self, *, name: str) -> LocalRuntimeInvocation: ...
    def list_by_label(self, *, label: str, value: str) -> LocalRuntimeInvocation: ...
    def image_present(self, *, image_ref: str) -> LocalRuntimeInvocation: ...
    def version(self) -> LocalRuntimeInvocation: ...


LOCAL_RUNTIME_DRIVERS: Mapping[LocalSandboxRuntimeKind, LocalSandboxRuntimeDriver]
"""Closed registry. Mirrors FS-01 D10's rule for the helper platform registry:
adding a runtime is a code change with a test, never a config string."""
```

`isolation_kind` is `"container"` for `PODMAN` / `DOCKER`, and **provisionally**
`"microvm"` for `APPLE_CONTAINER` pending SPIKE-L5's isolation-class half — the
"each container is its own lightweight VM" claim was stated as fact by this PRD's
first draft and is not observed by the probe. Both values satisfy `satisfies()`
identically ([contracts.py:389](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py)),
so if the spike cannot evidence the boundary the Apple driver declares
`"container"` and nothing downstream changes.

### 4. The provider (Python)

```python
# agent_runtime/capabilities/sandbox/providers/local_container.py

@dataclass(frozen=True)
class LocalSandboxProbeEvidence:
    """Per-boot control evidence.

    Every BOOLEAN field below is a measurement. The three leading descriptor
    fields are not: `runtime_kind` and `image_ref` are configuration echoed back,
    `runtime_version` is reported by the runtime, and `isolation_kind` is the
    driver's compile-time declaration (D2 / SPIKE-L5) — the one `satisfies()`
    term nothing here observes. `all_controls_observed()` reads the booleans
    only, and must never be written to treat a descriptor as evidence.
    """
    runtime_kind: LocalSandboxRuntimeKind
    runtime_version: str
    image_ref: str
    isolation_kind: Literal["container", "microvm"]   # declared, NOT observed
    egress_denied: bool
    tcp_connect_refused: bool
    dns_resolution_failed: bool
    workspace_quota_enforced: bool
    pids_limit_enforced: bool
    memory_limit_enforced: bool
    cpu_quota_accepted: bool
    wall_clock_kill_observed: bool
    rootfs_read_only: bool
    no_new_privileges: bool
    non_root_uid: bool
    removal_observed: bool
    probe_digest: str            # sha256 over the canonical JSON of the above

    def all_controls_observed(self) -> bool: ...


class LocalContainerSandboxProvider:
    """Implements SandboxProviderPort and SandboxGuardedProvisioner.

    Constructed ONLY by the worker composition root, with an already-completed
    probe. The constructor performs no I/O: `build_sandbox_backend` builds the
    registry twice (seam.py:60 and :70), so a constructor with side effects
    would run them twice.
    """

    def __init__(self, *, config: LocalContainerConfig,
                 driver: LocalSandboxRuntimeDriver,
                 probe: LocalSandboxProbeEvidence,
                 executor: LocalRuntimeExecutor) -> None: ...

    # -- SandboxProviderPort ------------------------------------------------
    @property
    def isolation_ready(self) -> bool: ...          # probe.all_controls_observed()
    @property
    def unavailability_reason(self) -> str: ...     # bounded lowercase code (§6)
    async def create(self, request) -> SandboxHandle: ...   # raises: guarded path only
    async def attest(self, request) -> SandboxIsolationAttestation: ...
    async def status(self, provider_session_ref: str) -> ManagedSandboxSession: ...
    async def terminate(self, provider_session_ref: str) -> None: ...
    async def list_owned_sessions(self, owner_tag: str) -> tuple[ManagedSandboxSession, ...]: ...

    # -- SandboxGuardedProvisioner -----------------------------------------
    def bind_provisioning_authority(self, authority) -> None: ...
    def cleanup_owner_marker(self, request) -> str: ...
    async def provision_with_capability(self, capability) -> SandboxHandle: ...
    async def recover_provisioning(self, owner_marker: str) -> None: ...


class LocalContainerBackend(BaseSandbox):
    """The deepagents backend for one live container. Never leaves the provider."""

    @property
    def id(self) -> str: ...
    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse: ...
    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse: ...
    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]: ...
    async def a_upload_files(self, files) -> list[FileUploadResponse]: ...
    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]: ...
    async def a_download_files(self, paths) -> list[FileDownloadResponse]: ...
    def ls(self, path: str) -> LsResult: ...        # native; does NOT go through execute
    async def prepare_execution(self, request: SandboxRunRequest) -> None: ...
```

### 5. Readiness reasons become provider-supplied (Python)

```python
# agent_runtime/capabilities/sandbox/readiness.py
class SandboxReadinessReason(StrEnum):
    DISABLED = "disabled"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    ISOLATION_UNVERIFIED = "isolation_unverified"
    OPENAI_HOSTED_CONTAINER_CONTROL_GAP = "openai_hosted_container_control_gap"
    LOCAL_RUNTIME_UNAVAILABLE = "local_runtime_unavailable"       # new
    LOCAL_IMAGE_ABSENT = "local_image_absent"                     # new
    LOCAL_ISOLATION_PROBE_FAILED = "local_isolation_probe_failed" # new
```

`assess` today hardcodes the OpenAI special case
([readiness.py:66-67](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/readiness.py)),
which would shadow every local reason for a local provider. It is replaced by a
lookup of the provider's own `unavailability_reason` — the property the OpenAI
adapter already exposes
([openai_hosted.py:507-511](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/providers/openai_hosted.py)) —
falling back to today's code-based mapping when the provider does not offer one.
The OpenAI string is unchanged, so its behaviour is unchanged (D16).

### 6. The C1 patch importer (Python) — the third missing authority

```python
# agent_runtime/capabilities/sandbox/patch_import.py  (new)

class OverlaySandboxPatchImporter(SandboxPatchImportPort):
    """Import one complete, verified patch as ONE C1 overlay revision.

    It writes no host byte, holds no broker handle, and touches no grant. Its
    output is an opaque overlay version ref which the existing review + C2
    commit lane later redeems (D12).
    """

    def __init__(self, *, overlay_store: WorkspaceOverlayStorePort,
                 blob_store: ArtifactBlobStorePort, author: str) -> None: ...

    async def import_patch(self, request: SandboxPatchImportRequest) -> str: ...
```

`SandboxPatchImportPort` already exists
([ports.py:329-334](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/ports.py)),
so this adds no `Protocol`. It does, however, make the importer the **second**
writer of C1 overlay revisions (C10) — D12 states what that costs and what the
importer must therefore re-derive.

`SandboxPatchImportRequest` ([contracts.py:330-347](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py))
gains one required field:

```python
class SandboxPatchImportRequest(RuntimeContract):
    run_id: str
    operation_id: str
    patch: WorkspacePatchManifest
    #: The exact retained C1 version the snapshot was taken from.
    #: append_revision uses it as expected_version, so a concurrent C1 edit
    #: conflicts instead of being silently overwritten.
    baseline_overlay_ref: str = Field(min_length=1, max_length=2048)
```

**There is no carrier for that value today, and "thread it through" understates
the change.** `coordinator.import_patch(self, result: SandboxRunResult)`
([coordinator.py:213-232](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/coordinator.py))
constructs the request from `result` alone; `SandboxRunResult`
([contracts.py:626-638](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py))
has no overlay ref, and `WorkspacePatchManifest` carries
`baseline_manifest_sha256` — a digest of the workspace manifest, not a C1
version. The value _is_ produced upstream:
`RuntimeWorkerOverlaySnapshotPlanAuthority` formats
`WorkspaceOverlayVersionRef` and puts it in every snapshot entry's `source_ref`
([sandbox_snapshot_authority.py:88-100](../../../services/ai-backend/src/runtime_worker/sandbox_snapshot_authority.py)).
Two admissible resolutions, and FS-08 picks **(a)**:

- **(a)** `import_patch` takes `baseline_overlay_ref` as an explicit second
  argument, supplied by the caller that also holds the run's snapshot — keeping
  `SandboxRunResult` a pure redaction-safe terminal projection, which is what its
  docstring says it is.
- **(b)** `SandboxRunResult` gains the field. Rejected: it is a model-adjacent
  projection, and widening it to carry C1 pointer state puts overlay identity in
  a structure the tool result is built from.

Either way this is a **second** contract edit, not the one additive field D1's
first draft counted. D1's change list is corrected below.

### 7. Desktop: the patch-import change set (TypeScript)

Shaped on FS-04 §5 / D6 (`prepareLocalRestore` / `authorizeLocalRestore`), for
the same reason: a mutation the **user** triggers must not be redeemable by a
server approval, and an agent proposal must not be redeemable by a local
confirmation.

```ts
// apps/desktop/main/capabilities/workspace-authority.ts

export interface WorkspacePatchImportRequest {
  readonly grantId: string;
  /** Immutable A2 revision ref of the reviewed overlay revision. */
  readonly overlayRevisionRef: string;
  /** Digest of the exact reviewed set; re-checked before a permit is minted. */
  readonly reviewedChangeSetDigest: string;
}

export class LocalWorkspaceAuthority {
  /** Main-only. Main builds every entry; the caller supplies none. */
  prepareSandboxPatchImport(
    request: WorkspacePatchImportRequest,
  ): Promise<WorkspacePreparedEffect>;

  /**
   * Mints a one-use permit only for a prepared state whose change set has
   * `origin === "sandbox_patch_import"`. `authorizeCommitFromUserDecision`
   * (:602) symmetrically refuses such a change set.
   */
  authorizeSandboxPatchImport(
    preparedRef: string,
    confirmation: { readonly confirmedByUser: true },
  ): Promise<WorkspaceCommitPermit>;
}
```

The `origin` field and its union are declared by whichever of FS-04 / FS-08 lands
first (see Interfaces consumed); FS-08 contributes the member
`"sandbox_patch_import"`. **No `WorkspaceContentSource` member is added** —
imported bytes use the existing `kind: "upload"` slot (D13).

**Two things this lane must mint that FS-04's restore lane does not, and the
first draft named neither.**

`WorkspaceChangeSet` requires `stageId`, `revision`, `decisionLedgerId`,
`changeSetDigest`, `targetDigest` and `proposalDigest`
([workspace-authority.ts:92-105](../../../apps/desktop/main/capabilities/workspace-authority.ts)),
and `authorizeCommitFromUserDecision` binds a permit to the first three by exact
comparison (`:601-627`). A restore is a single main-authored entry over a
preimage row main already owns, so FS-04 D6 can mint that identity locally
without lying. **An imported patch is agent-authored**: the entries derive from
code the sandbox ran, and that code's inputs include content this product ingests
from MCP connectors. So:

1. **Who mints the proposal identity.** `prepareSandboxPatchImport` must derive
   `changeSetDigest` / `targetDigest` / `proposalDigest` from the immutable
   overlay revision it was given, and `reviewedChangeSetDigest` must be
   re-checked against them before a permit is minted — otherwise the digest is
   decorative. Deriving them from anything mutable reopens the exact hole
   `authorizeCommitFromUserDecision`'s triple comparison closes.
2. **What records the decision.** `authorizeSandboxPatchImport` takes
   `{ confirmedByUser: true }` and therefore binds **no `decisionLedgerId`**. For
   FS-04's restore that is correct — there is no server decision to bind. For an
   agent-authored change set it means an imported mutation commits with no
   ledgered approval row, while every other agent-proposed mutation carries one.
   **FS-08 does not resolve this**, because it is a product call about where the
   import decision is recorded and for how long, not an implementation detail.
   It is written into Open questions as an owned, blocking item rather than left
   to be discovered at review. Two admissible resolutions are named there.

**No broker route and no advertised method is added.** `ROUTES`
([broker.ts:80-94](../../../apps/desktop/main/capabilities/broker.ts)) and
`ADVERTISED_METHODS` (`:97-112`) are unchanged, so the model cannot reach import
at all.

## Design

### D1. This is one provider behind an existing port. It is not a new subsystem

The complete production surface FS-08 adds:

1. one `SandboxProviderId` member;
2. one config model (`LocalContainerConfig`) and its env names;
3. one driver registry with two entries and one provider class + backend;
4. one boot probe;
5. one C1 patch importer — which is C1's **second** writer (C10), and pays for
   that in D12 by re-deriving what the mutation engine would have derived;
6. one main-only desktop import lane (prepare/authorize). The surface that
   triggers it is **[FS-09 D24](PRD-FS-09-enablement-consent.md)**'s — one
   control on a reviewed proposal, never in the model's reach;
7. two composition edits: the prerequisites bundle and the provider injection;
8. one edit on the `als` path, whose exact shape D8 leaves to the implementer
   because two of the three admissible fixes touch a file this list would
   otherwise call untouched.

Everything else is consumed. Concretely: no new `Protocol` in `ports.py`
(`SandboxPatchImportPort` already exists at `ports.py:329`), no new gateway, no
new model tool, no second `StructuredTool`, no change to `execute_tool.py`'s
schema (`command` only —
[execute_tool.py:53-59](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/execute_tool.py)),
no change to the transfer adapter.

**Two "no change" claims from the first draft are withdrawn**, because the
reconciliation pass proved they cannot both hold:

- _"no change to `PolicyEnforcedSandboxBackend`'s guards"_ — D8's fix for C4a may
  require an edit to `policy_backend.py`. The path guards themselves stay; the
  `ls`/`als` resolution may not. D8 picks.
- _"no change to the patch contracts other than the one additive field"_ — there
  are **two**: `baseline_overlay_ref` on `SandboxPatchImportRequest`, and the
  `import_patch` signature that carries it (§6). Neither is optional.

The rule that makes this checkable: **the local provider is substitutable with
the fakes in
`services/ai-backend/tests/unit/agent_runtime/capabilities/sandbox/fakes.py`.**
If a test double can no longer stand in for it, the port has been widened.

### D2. The mechanism is a container runtime on both platforms — chosen because the predicate demands it, not because containers are fashionable

`satisfies()` requires `isolation ∈ {"container","microvm"}`
([contracts.py:389](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py)).
The candidate mechanisms, ranked honestly:

The isolation-class column below is what a mechanism **would** have to be for
`satisfies()` to pass. Read it as a ranking, not as measurement: every row marked
_unverified_ was asserted by this PRD's first draft as fact and has been
downgraded by the reconciliation pass.

| mechanism                                               | isolation class             | passes `satisfies()`      | grounding                                                                                                                                                                                                                  |
| ------------------------------------------------------- | --------------------------- | ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| macOS Seatbelt (`sandbox-exec`)                         | process                     | **no**                    | in the product today for service confinement (C8). Its reported deprecation is **unverified** — SPIKE-L1                                                                                                                   |
| Windows AppContainer / restricted token / low IL        | process                     | **no**                    | FS-03 D3 ranks exactly these three and says low IL "denies reads? **no**". The first draft's "+ Job" is **removed**: FS-03 D3 does not rank Job objects, and a Job object is a resource control, not an isolation boundary |
| Apple `container` (macOS 26+, Apple silicon)            | claimed microvm             | yes, _if_ the claim holds | **unverified — SPIKE-L5.** "Per-container lightweight VM" and "narrowest host surface" were stated as fact and are not observed anywhere in this design                                                                    |
| `podman` (macOS `podman machine`, Windows WSL2 backend) | container inside a Linux VM | yes                       | **unverified.** The first draft's "rootless by default; no long-lived daemon on the host" is at best half true on these two platforms — `podman machine` _is_ a long-lived host VM. SPIKE-L2 / SPIKE-L5                    |
| `docker` (Docker Desktop, WSL2 backend on Windows)      | container inside a Linux VM | yes                       | most widely installed; the daemon is not ours                                                                                                                                                                              |
| Windows Sandbox / Hyper-V isolated containers           | VM                          | yes                       | rejected by FS-03 D3, which says exactly this: "Requires Pro/Enterprise and virtualisation; not available to the consumer install this product targets"                                                                    |

**The one field `satisfies()` checks first is the one field the probe never
observes.** `isolation_kind` is a compile-time constant per driver (§3), so
`isolation: "microvm"` for `apple_container` is a **declaration**, not a
measurement — unlike the ten controls D6 observes. That is a real asymmetry in
the attestation and it is stated rather than smoothed over: SPIKE-L5 is extended
to ask what evidence, if any, distinguishes `apple_container`'s boundary from a
namespace container from inside the guest. **If no such evidence exists in a
bounded probe**, the Apple driver declares `"container"` — the strictly weaker
true-either-way value — rather than claiming a boundary it cannot show.
`satisfies()` accepts both, so nothing is lost but the claim.

Decision: ship **one** provider driving an OCI-compatible runtime through a
closed two-driver registry, spelling `apple_container` and `podman`/`docker`
arguments. On macOS the preference order is `apple_container` → `podman` →
`docker`; on Windows it is `podman` → `docker` (both on the WSL2 backend).
Preference is a resolution order in desktop main, never a fallback at run time:
once a runtime is bound at boot, a run never silently switches.

Rejected, with reasons, so they are not re-proposed:

- **Relaxing `satisfies()` to accept `"process"`.** This is the tempting move and
  it is the one the spine's guardrail forbids ("Do not weaken confinement to make
  a verb work"). The reason is [README D1](README.md)'s and is not restated here.
  The mechanism-specific point that D1 does not cover: a process sandbox on
  either platform leaves the user's whole home directory readable unless every
  path is enumerated, and enumeration is not deny-by-default.
- **Seatbelt on macOS + AppContainer on Windows as a "good enough" pair.** Even
  setting the predicate aside, FS-03 D3 already establishes that the two
  mechanisms do not reach the same bar as each other, and FS-03 explicitly
  refuses to let one word mean two things on two platforms.
- **Bundling a microVM (libkrun/krunvm, WSL2 distro management).** A real
  option and a much larger slice: image lifecycle, kernel updates, and a second
  supervised runtime to keep alive. Named so it is on record; out of scope.

### D3. If no qualifying runtime is present, the capability is absent — and that is the design working

There is no degraded mode. A host with no container runtime reports
`local_runtime_unavailable` and `run_in_sandbox` is not in the toolset, exactly
as today. This is the same posture as the other six gates and as
`build_sandbox_backend`'s comment: "An unverified provider is not a degraded
sandbox"
([seam.py:64-66](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/seam.py)).

The honest consequence, stated rather than buried: **most installs will not have
execution on day one.** Telling the user why, and what to install, is
[**FS-09 D21**](PRD-FS-09-enablement-consent.md): it renders **one** reason from
D16's closed set through a fixed copy table, and for
`local_runtime_unavailable` it names only the runtimes this build can actually
drive — `apple_container`, `podman`, `docker` (§2). Naming a runtime the driver
registry does not know would be the execution-side version of FS-09 D8's "never
name a verb the build cannot perform". FS-08's two constraints on that surface
are carried there verbatim rather than restated here: it never installs anything
itself, and **nothing in this program prompts for elevation**. That last clause
has a caveat the first draft did not
carry: SPIKE-L2 expects `wsl --install` on Windows to require elevation once. The
program does not prompt for it and does not run it — the user does, outside the
app, before the runtime is ever detected. If that turns out to be unacceptable
product-wise, Windows has no local execution story and D4 takes macOS down with
it.

### D4. Both platforms or neither — and the asymmetry that remains is per-host, not per-platform

The spine guardrail is that a verb lands on both platforms or neither. FS-08
satisfies it in the form that matters: the provider, the driver registry, the
attestation semantics, the probe, the importer and the import lane are the same
code on macOS and Windows, and there is no `#ifdef`-shaped divergence. The
`podman`/`docker` drivers are byte-identical across platforms; only the resolved
binary path differs, and that is supplied by main.

What differs is the **host prerequisite**: macOS needs Apple `container` or a
podman/Docker machine; Windows needs WSL2 plus podman/Docker. Both are reported
through the same `local_runtime_unavailable` reason, and a macOS host without a
runtime is exactly as unavailable as a Windows host without one. No verb exists
on one platform only.

The one thing FS-08 must not do is ship the macOS lane first and let the Windows
lane follow: the DoD requires the Windows driver to pass the same probe suite in
CI or on a recorded host before either lands (SPIKE-L2).

### D5. The nine controls, flag by flag, and the one that is enforced by us rather than by the runtime

Launch arguments (`run_detached`), identical in intent across drivers. **The
spellings below are docker/podman's documented flags and are `unverified` for
`apple_container` (SPIKE-L5) and unverified end-to-end on the WSL2 backend
(SPIKE-L2).** They are a design intent expressed in one runtime's vocabulary, not
a claim that three runtimes accept the same argv. This matters because D6's
probe only proves the _effect_ on the runtime that is actually present; a flag
that a runtime silently ignores is caught by the probe, and a flag a runtime
_rejects_ makes the provider unavailable — both are correct outcomes, and neither
is a reason to state the spelling as portable.

| control             | how                                                                                                                                            | attestation field           |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------- |
| isolation           | the runtime's own boundary                                                                                                                     | `isolation` (D2)            |
| process isolation   | default PID/UTS/IPC/mount namespaces; **never** `--pid=host`, `--ipc=host`, `--userns=host`                                                    | `process_isolated`          |
| fresh filesystem    | pinned image by digest, `--read-only` rootfs, `--rm`, **zero bind mounts**                                                                     | `filesystem_fresh`          |
| guaranteed teardown | `--rm` + explicit `remove` on terminate + durable duty + label sweep (D10)                                                                     | `teardown_guaranteed`       |
| no host credentials | empty env allow-list, `--env-file` never used, `--user <non-root uid>`, `--cap-drop=ALL`, `--security-opt no-new-privileges`, no socket mounts | `host_credentials_absent`   |
| CPU quota           | `--cpus` from `cpu_millicores`                                                                                                                 | `cpu_quota_enforced`        |
| memory quota        | `--memory` = `--memory-swap` (swap disabled)                                                                                                   | `memory_quota_enforced`     |
| wall clock          | **provider-owned** deadline + kill (below)                                                                                                     | `wall_clock_quota_enforced` |
| process quota       | `--pids-limit`                                                                                                                                 | `process_quota_enforced`    |
| file quota          | `--tmpfs /workspace:rw,size=…,mode=0700,nosuid,nodev` and `--tmpfs /tmp:rw,size=…,mode=1777,nosuid,nodev,noexec` on a read-only rootfs         | `file_quota_enforced`       |
| egress              | `--network none`                                                                                                                               | `egress_mode`               |

Three of these deserve their reasoning written down:

**File quota via tmpfs, not a storage quota.** `--storage-opt size=` is reported
to depend on the storage driver (overlay2 on xfs with pquota, and not at all on
some configurations) — **unverified, and folded into SPIKE-L2's flag matrix**.
The reason to prefer tmpfs does not rest on that claim: a size-capped tmpfs is
enforced by the kernel, makes `filesystem_fresh` trivially true, and disappears
with the container.

The cost is real and paid explicitly: `/workspace` is RAM-backed, so its size is
charged to the memory limit — which is why `LocalContainerConfig` validates
`workspace + tmp + 256 MiB ≤ memory`. **That charging rule is itself
`unverified`** (it is cgroup behaviour, asserted as fact by the first draft), and
it is load-bearing: the validator exists only because of it. **SPIKE-L6** — fill
a tmpfs inside a memory-limited container and observe whether the container is
OOM-killed at the memory limit rather than at the tmpfs size. _If tmpfs pages are
**not** charged to the limit_, the validator is unnecessary and over-restrictive
and is dropped; _if they are_, it stays and the defaults are correct. Either way
the design is unchanged in structure — this spike only decides whether one
validator survives.

It also means the provider must refuse a snapshot it cannot host:
`provision_with_capability` raises `SNAPSHOT_QUOTA_EXCEEDED` when
`2 × Σ entry.size_bytes > workspace_tmpfs_bytes`, leaving at least as much room
for outputs as inputs. That check is a pure function of the request and is unit
testable without a runtime.

**`/workspace` is not `noexec`; `/tmp` is.** `WorkspaceTransferEntry.executable`
exists ([contracts.py:183-189](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py))
and "run the script I gave you" is the point of the capability, so `noexec` on
`/workspace` would break the product. `/tmp` has no such requirement and gets
`noexec` as cheap defence against a downloaded-payload pattern — noting that
with `--network none` there is nothing to download.

**Wall clock is ours.** No runtime flag is believed to reliably kill a long
`exec` across all three runtimes — **unverified, folded into SPIKE-L2** — so
`LocalContainerBackend.aexecute` owns an
`asyncio.wait_for` on `command_timeout_s`
([config.py:56](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/config.py),
clamped at [policy_backend.py:212-218](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/policy_backend.py))
and a session deadline on `session_wall_time_s` (`config.py:57`). On expiry it
kills the exec child, issues `kill` against the container, confirms via
`inspect`, and raises `SANDBOX_COMMAND_TIMEOUT`. Asserting
`wall_clock_quota_enforced` is therefore a claim about **our** timer, and the
probe proves it by running a `sleep` past the deadline and observing the kill
(D6). If the kill cannot be confirmed the result is
`SANDBOX_EXECUTION_INDETERMINATE`, never a success.

### D6. Every control is _observed_ at boot, never merely configured

This is FS-03 D2's rule imported wholesale, and C8's W1 is why. A flag on a
command line is a request; the probe is the evidence.

`LocalSandboxProbe.run(config, driver, executor)` runs **once per process boot**,
before the provider is constructed, inside `probe_timeout_s`, and produces
`LocalSandboxProbeEvidence`. It starts one container with the exact launch
arguments a real run would use, and observes:

1. `version` parses and the runtime kind matches what was configured;
2. `image_present(image_ref)` is true — the probe **never pulls** (D7);
3. TCP connect to a routable address fails, and DNS resolution fails
   (`egress_denied`, `tcp_connect_refused`, `dns_resolution_failed`);
4. writing `workspace_tmpfs_bytes + 1` into `/workspace` fails with ENOSPC
   (`workspace_quota_enforced`);
5. a bounded fork loop is refused at the pids limit (`pids_limit_enforced`);
6. a bounded allocation past the memory limit is killed (`memory_limit_enforced`);
7. the CPU quota argument is accepted and reflected by `inspect`
   (`cpu_quota_accepted` — deliberately weaker wording than "enforced", because
   scheduler share is not observable in a bounded probe; SPIKE-L3);
8. a `sleep` past the deadline is killed by our timer and the container is gone
   (`wall_clock_kill_observed`);
9. a write to `/` fails (`rootfs_read_only`), `id -u` is non-zero
   (`non_root_uid`), and a setuid escalation attempt fails
   (`no_new_privileges`) — **this observation needs a subject the first draft did
   not provide**: a setuid-root binary must be present in the pinned image, so
   D7's image requirements gain a fifth item. Without one, "the escalation
   failed" is indistinguishable from "nothing was attempted", which is exactly
   the configured-not-observed failure D6 exists to prevent;
10. after `--rm` the container is absent from `list_by_label`
    (`removal_observed`).

**What the probe does not observe: the isolation class itself.** `isolation` —
the first term of `satisfies()` — comes from the driver's compile-time
`isolation_kind`, not from any of the ten measurements (D2). D6's guarantee is
therefore precisely "every _control_ was observed", never "the boundary was
observed".

`isolation_ready` is `probe.all_controls_observed()`. Any missing observation
makes the provider unavailable with `local_isolation_probe_failed`. A probe that
times out is a failure, not an unknown.

`isolation_ready` is a **synchronous property** read at registry construction
([provider_registry.py:70](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/provider_registry.py)),
so the async probe cannot live inside it. That is why the provider is injected
already-probed (D15), and why the constructor does no I/O:
`build_sandbox_backend` constructs the registry twice
([seam.py:60, 70](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/seam.py)),
and a constructor that provisioned anything would do it twice.

### D7. The image is digest-pinned, code-owned, and never pulled implicitly

`image_ref` must match `name@sha256:<64hex>`; a tag-only reference is refused by
the config validator. The provider **never** runs a pull. If the image is
absent, the probe reports `local_image_absent`, readiness is false, and the tool
is absent.

Acquisition is an explicit user action, with the size stated before it starts.
Two reasons, both product ones: a first `run_in_sandbox` must not silently
download hundreds of megabytes on a metered connection, and an implicit pull at
run time makes the first execution's behaviour depend on network state, which is
exactly the kind of "it worked yesterday" the rest of this program avoids.

**[FS-09 D22](PRD-FS-09-enablement-consent.md) owns that acquisition surface**,
and calls it the largest consent this program asks for — hundreds of megabytes of
_executable_ content fetched onto the user's machine on their say-so. The first
draft routed it to "FS-09's enablement surface" at a time when FS-09 disclaimed
FS-08 by name; FS-09 has since taken it. Four properties it binds that FS-08 must
not contradict: the fetch is **main-owned and user-triggered** (the runtime binary
is already resolved to an absolute path by main — Phase 5 item 17 — and the
process that fetches executable content must not be the process a model can
reach); **one consent authorises exactly one acquisition of exactly the pinned
digest**, so a different digest asks again rather than renewing; a failure leaves
`local_image_absent` with **no automatic retry**, which is this decision's own
rule rendered honestly; and progress is shown only from bytes the runtime
actually reports, never a computed percentage. FS-08 supplies the digest, the
expected size and the driver argv; it does not own the ask.

Image contents are a code-owned requirement, not a preference: it must contain
`python3` (C4b — `BaseSandbox.ls` shells `python3 -c`), a POSIX shell (the
`command` is a shell command by contract —
[execute_tool.py:33-38](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/execute_tool.py)),
`tar` (transfer, D8), a non-root user, and — added by the reconciliation pass —
**a setuid-root binary for D6's observation 9 to act on**. A test asserts each of
the five against the pinned digest.

### D8. Transfer is whole-file, one call per direction, and `ls` stops charging the command budget

`DeepAgentSandboxRuntime.upload` hands over a `list[tuple[str, bytes]]`
([runtime_adapter.py:47-57](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/runtime_adapter.py))
after verifying each entry's digest and size against the sealed store
(`:167-193`). `upload_files` streams **one** tar to `copy_in`'s stdin rather
than N copies, so a 10 000-entry snapshot is one runtime invocation. Symmetric
for `download_files` via `copy_out`.

**Rejected: bind-mounting the sealed spill directory.** It is the obvious
optimisation — the bytes are already sealed at
[snapshot_file_store.py:151-199](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/snapshot_file_store.py) —
and it is wrong twice. There is no port method for it, and more importantly a
mount puts a **host path** inside the provider boundary, which
`SandboxSnapshot`'s contract forbids in terms
([contracts.py:202-209](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py)).
The copy is bounded by `max_entry_bytes` (64 MiB) per file and
`max_upload_total_bytes` (512 MiB) overall, and D5's admission check bounds it
again against the tmpfs.

**`ls` is implemented natively — and that alone does not fix C4a. The first draft
said it did, and it was wrong.**

`LocalContainerBackend.ls` / `als` do run a single `find`-based listing through
`exec_in` rather than through `execute`, returning the same `LsResult` with
`{path, is_dir}` entries the collector consumes
([patch_collector.py:66-77](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/patch_collector.py)).
But per C4a the collector never reaches them: it calls `als` on the **façade**,
`PolicyEnforcedSandboxBackend` is itself a `BaseSandbox`, and `BaseSandbox.als`
goes straight to `self.aexecute` — which is the façade's budget-charging
`aexecute`. **The delegate's `ls`/`als` are dead code on that path.** FS-08's own
regression test (`after 200 als calls, commands_used == 0`) fails against the
first draft's design, which is the right test failing for the right reason.

Three admissible fixes. **FS-08 picks (a).**

- **(a) The façade delegates listing when the delegate implements it.**
  `PolicyEnforcedSandboxBackend.ls` / `als` keep `_guard_path` and then prefer a
  delegate-supplied `ls` / `als` over `super()`, using the **same duck-typed
  probe idiom the façade already uses** for `a_upload_files`, `a_download_files`
  and `prepare_execution`
  ([policy_backend.py:130-162](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/policy_backend.py)).
  It is the smallest change, it introduces no new pattern, the `/workspace`
  guard is untouched, and it fixes the defect for **every** provider rather than
  only the local one. Its cost is honest and is why D1's "no change to
  `PolicyEnforcedSandboxBackend`" is withdrawn: a listing that no longer charges
  the budget is a listing the budget no longer bounds, so the walk's own bound
  becomes `visited_directories` plus `max_upload_files` — which
  `DeepAgentArtifactPatchCollector` already enforces
  (`patch_collector.py:54-90`) — and a test must pin that a hostile deep tree
  still terminates.
- **(b) The collector stops using `als` for the walk.** Correct but larger:
  `patch_collector.py` is on the consumed-unchanged list, and changing the walk
  changes the shape of every provider's collection.
- **(c) Raise `commands_per_session`.** Rejected twice over. It raises a control
  to make collection work, which the spine forbids; and it cannot work anyway —
  the field is capped at 256
  ([config.py:59](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/config.py))
  while `download_file_count` admits 10 000 entries, so a wide tree exhausts any
  legal ceiling.

The backend defines `a_upload_files` / `a_download_files` (the repo spelling,
C4c), so the façade's probes hit real async paths.

### D9. Cancellation kills the container, and an unconfirmed kill is indeterminate

Cancellation arrives as `asyncio.CancelledError` inside `coordinator.run`. The
backend must:

1. cancel the exec child process (`kill` the driver subprocess, `terminate` then
   `kill` after a bounded grace);
2. issue `driver.kill(name=…)` for the container;
3. confirm absence via `inspect`;
4. re-raise `CancelledError` only after (3) succeeds; if (3) cannot be
   confirmed, mark the session `cleanup_pending` so the durable duty survives,
   and let the coordinator's existing `SANDBOX_EXECUTION_INDETERMINATE` path run.

The provider also holds a per-session **execution access token** deactivated by
`terminate`, so a retained backend object cannot execute after teardown — the
pattern the OpenAI adapter already uses
([openai_hosted.py:105-117, 894-914](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/providers/openai_hosted.py)).

### D10. The provider is a guarded provisioner, deliberately, because the crash window is real and locally reapable

`create()` raises `SANDBOX_POLICY_UNSUPPORTED` — the same posture as
[openai_hosted.py:524-531](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/providers/openai_hosted.py) —
and provisioning happens only through `provision_with_capability`, so the
durable `state="provisioning"` reservation is written **before** any container
exists ([remote_execution_service.py:345-358](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/remote_execution_service.py)).

Identity and enumeration, which is where a local provider differs most from a
remote one:

- `provider_session_ref` must match `^[A-Za-z0-9][A-Za-z0-9._:-]*$`
  ([contracts.py:435-439](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py); repeated in
  [cleanup_store.py:44-49](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/cleanup_store.py)) —
  **no `/`**, so it can never be a filesystem path. The provider uses the
  container **name** `cpsbx-<32 lowercase hex>`, generated from the idempotency
  key so a retry names the same container.
- `cleanup_owner_marker(request)` is a deterministic label value derived from
  `request.operation_id`, ≤255 chars and carrying the **same** no-slash pattern
  (`cleanup_store.py:50-56`), stamped as the container label
  `tech.0xcopilot.sandbox.owner`.
- `list_owned_sessions(owner_tag)` and `recover_provisioning(owner_marker)` are
  `list_by_label` sweeps. This is the port obligation a local provider must
  discharge itself: `FileSandboxSessionStore` / `FileSandboxCleanupStore` are the
  _service's_ projections and do not enumerate what the runtime actually holds.
- `terminate` is idempotent, and — copying the narrowing the OpenAI adapter
  applies ([openai_hosted.py:750-769](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/providers/openai_hosted.py)) —
  only a confirmed "no such container" is a no-op. Any other failure raises
  `SANDBOX_CLEANUP_PENDING` so the durable duty stays pending and the reaper
  ([sandbox_composition.py:399-419](../../../services/ai-backend/src/runtime_worker/sandbox_composition.py))
  retries.

Note for the implementer, from C2: `runtime_checkable` matches method names
only. The provider defines all four members with correct signatures **and** a
test asserts `isinstance(provider, SandboxGuardedProvisioner)` is true and that
`create()` raises, so the guarded path cannot be entered or left by accident.

### D11. `attestation_ref` gets a format, and the PRD says plainly that nothing verifies it

```
local-container:v1:<runtime_kind>:<runtime_version>:<image_digest>:<probe_digest>
```

`probe_digest` is sha256 over the canonical JSON of `LocalSandboxProbeEvidence`
minus the digest field itself. Nothing in the runtime parses this string (C3),
and FS-08 does not pretend otherwise: it exists so a support bundle can
reproduce the claim and so a reviewer can tell a real attestation from a
constant. The test that matters asserts the field is **derived from observed
values** — change the probe result, the ref changes.

### D12. Patch-back rides the existing lane end to end; FS-08 adds no write path

The full chain, with the existing component at each hop:

```
sandbox /workspace tree
  → DeepAgentArtifactPatchCollector.collect            patch_collector.py:45   (exists)
  → WorkspacePatchBuilder.build / verify_patch         workspace_transfer.py:346,555 (exists)
  → A2 publication, patch_ref                          operation_runner.py:324 (exists)
  → [model sees patch_ref only]                        execute_tool.py:155     (exists)
  ─────────────── explicit user action, later, out of band ───────────────
  → OverlaySandboxPatchImporter.import_patch           NEW (§6)
  → OverlayMutation[] + append_revision(expected_version)  workspace/ports.py:63 (exists)
       ^^ second writer into C1 — see C10; the engine's controls are re-applied
  → one C1 overlay revision ref
  → review surface  ** FS-09 D24 owns it and names it: TcWorkspaceStageSurface
                       via projectWorkspaceStage — the stage card that already
                       exists, not a second one. Whether an IMPORTED revision
                       reaches that projection today is still UNVERIFIED
                       (FS-09 open question 8); if it does not, the wiring is
                       FS-08's mechanism, not a second projection. **
  → LocalWorkspaceAuthority.prepareSandboxPatchImport  NEW (§7)
  → uploadPreparedContent / sealPreparedContent        workspace-authority.ts:549,572 (exists)
  → authorizeSandboxPatchImport (one-use permit)       NEW (§7)
  → commitPreparedChangeSet                            workspace-authority.ts:656 (exists)
  → helper commit_entry → FS-02 create/mkdir, FS-05 delete/move, FS-06 replace
```

The load-bearing fact is C9: the patch's five operations, C1's
`WorkspaceOperation`, and the desktop's `WorkspaceOperation` are the **same five
names**. So the mapping is a table, not a translator:

| patch entry                                | overlay mutation                                                                                                                     | desktop change entry                                             |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------- |
| `create` (`result_*` required)             | `UPSERT` `OverlayEntry(entry_kind=FILE, operation=CREATE, content_ref=content_ref_for_blob(result_digest), baseline=MUST_NOT_EXIST)` | `operation:"create"`, `precondition:{exists:false}`              |
| `replace` (`result_*` + `baseline_digest`) | `UPSERT` `(FILE, REPLACE, content_ref=…, baseline=MUST_EXIST/FILE/content_digest=baseline_digest)`                                   | `operation:"replace"`, `precondition:{exists:true, sha256:…}`    |
| `delete` (`baseline_digest` only)          | `UPSERT` `(TOMBSTONE, DELETE, baseline=MUST_EXIST/FILE/content_digest=baseline_digest)`                                              | `operation:"delete"`, `precondition:{exists:true, sha256:…}`     |
| `move` (`source_path` + `baseline_digest`) | `UPSERT` `(MOVE, MOVE, source_virtual_path=source_path, baseline=MUST_EXIST/FILE/…)`                                                 | `operation:"move"`, `destinationRelativePath`, same precondition |
| `mkdir` (no evidence)                      | `UPSERT` `(DIRECTORY, MKDIR, baseline=MUST_NOT_EXIST)`                                                                               | `operation:"mkdir"`, `precondition:{exists:false}`               |

**The `baseline=` column above is shorthand, and taking it literally would ship a
weaker precondition than every other proposal in the system.** Per C10, the
mutation engine's `_precondition_for_base` always fills `opaque_generation`,
`content_digest`, `stable_file_id`, `byte_size` and `mtime_ns`; a patch entry
carries only `baseline_digest`. `BasePrecondition`'s validator accepts the thin
version, so nothing fails loudly — the imported change simply gets re-checked
more loosely at commit than an agent's ordinary proposal, including on the
desktop's `stableId`. The importer therefore **recovers the missing fields from
the retained baseline overlay manifest**, which it already has by
`baseline_overlay_ref`, and refuses the import when a referenced base entry is
absent from that version. A test asserts field-for-field equality with what
`_precondition_for_base` would have produced.

Four invariants the importer enforces before emitting anything:

- **Every `result_digest` must be a present blob.** `blob_store.stat(result_digest)`
  must succeed for every `create`/`replace`. A2 keys blobs by content digest
  ([artifacts/service.py:489](../../../services/ai-backend/src/agent_runtime/artifacts/service.py)),
  which is exactly what `content_ref_for_blob` requires
  ([workspace/contracts.py:141-147](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/contracts.py)).
  A missing blob fails the whole import with `SANDBOX_PATCH_INCOMPLETE` —
  partially importing a patch would produce an overlay revision that claims to be
  a complete diff and is not.
- **One revision, compare-and-swap.** All mutations go in a single
  `append_revision(run_id, expected_version=<baseline version>, mutations=…)`
  call. The baseline version comes from `baseline_overlay_ref` (§6) — every
  snapshot entry shares one overlay ref today
  ([sandbox_snapshot_authority.py:88-100](../../../services/ai-backend/src/runtime_worker/sandbox_snapshot_authority.py)),
  and the importer refuses a manifest whose entries disagree. A concurrent C1
  edit therefore raises `WorkspaceOverlayConflictError` instead of silently
  overwriting.
- **The engine's ceilings are re-applied.** `_check_limits`
  ([overlay.py:601-617](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/overlay.py))
  bounds overlay entry count and total referenced bytes, and it lives inside the
  writer the importer bypasses. The importer applies the same two ceilings
  against the baseline manifest **before** appending, for the whole batch rather
  than per mutation — a patch is many mutations at once, which is a shape the
  engine never sees. Without this, an import is the one path into C1 with no
  size bound.
- **The bypass is declared, not accidental.** `SandboxPatchImportPort` is a
  pre-existing seam whose own docstring says a complete patch "is imported into
  C1's overlay and later goes through A4/A5 review"
  ([contracts.py:330-337](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py)),
  so a second writer is the intended shape — but it is still a second writer, and
  the two things it cannot inherit are the gateway's **operation record** and its
  **disposition**. FS-08 does not synthesise a fake operation to paper over that.
  What it must do instead — and what §7 and Open questions carry — is make the
  desktop import lane the place the decision is recorded, since that is the only
  point in the chain where a human is present.

### D13. Imported bytes use the existing upload slot; no new content source is invented

FS-04 §2 defines `WorkspaceContentSource` as `none | upload | preimage`. FS-08
adds **no fourth member**. The import lane streams each entry's A2 blob into the
prepared slot with the existing `uploadPreparedContent` / `sealPreparedContent`
pair ([workspace-authority.ts:549, 572](../../../apps/desktop/main/capabilities/workspace-authority.ts)),
and the helper's own seal verifies the digest. `contentDigest` on the entry is
the patch's `result_digest`; a mismatch is caught by the existing
`sealed_stage_matches` check, not by new code.

This is the whole point of routing through C1 first: by the time the desktop
sees it, an imported patch is indistinguishable from any other reviewed change
set, and every control FS-02/04/05/06/07 adds applies to it automatically —
preimage capture, trash admission, precondition re-check at commit, crash
reconciliation.

### D14. A patch entry outside a granted mount fails the import; it is never dropped

C9's divergence: `/workspace/out.txt` is a valid sandbox path and an invalid C1
path. The importer refuses the **entire** import with a specific reason
(`patch_path_outside_mount`) and a count of offending entries.

Refusing rather than dropping is deliberate, and the consistency report's §1
lesson is the precedent: `WorkspaceManifestBuilder.build` silently drops excluded
paths while `verify_manifest` treats the same paths as a hard `SNAPSHOT_INVALID`
([workspace_transfer.py:197-198 vs :308-316](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/workspace_transfer.py)) —
an asymmetry that makes "complete" mean two things. A partially imported patch
would be exactly that.

Two mitigations reduce how often it happens, neither of which is a fallback:

- The container's working directory is set to `/workspace/<mount>` when the
  snapshot spans exactly one mount, and `/workspace` otherwise. Relative writes
  then land inside the mount by default.
- `TOOL_DESCRIPTION` ([execute_tool.py:32-38](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/execute_tool.py))
  gains one sentence naming the working directory. It gains nothing else — the
  input schema stays `command`-only.

### D15. The provider is injected by the composition root, never constructed by the registry

`SandboxProviderRegistry._construct` gains a `LOCAL_CONTAINER` branch that
**raises** `SANDBOX_PROVIDER_UNCONFIGURED`, word for word the posture the OpenAI
branch already takes
([provider_registry.py:89-96](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/provider_registry.py)).
The real provider is built in `FileSandboxWorkerRuntime.compose` and passed via
`provider_overrides`, alongside the existing OpenAI injection
([sandbox_composition.py:336-350](../../../services/ai-backend/src/runtime_worker/sandbox_composition.py)).

Three reasons, all structural: the probe is async and `isolation_ready` is not;
the runtime binary path is main-supplied and must not be discovered from process
environment inside a domain module; and the registry is built twice per seam call
(D6), so construction must stay inert.

### D16. Readiness reasons come from the provider, and the local ones are actionable

`readiness.assess` currently branches on `config.provider is OPENAI_HOSTED_CONTAINER`
([readiness.py:66-67](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/readiness.py)),
which would shadow every local reason.

**Where the replacement reads from matters, and "the constructed provider" is the
wrong phrase**: that branch is only reached when
`SandboxProviderRegistry.from_config` **raised**, so there may be no constructed
provider at all. The provider FS-08 cares about arrives through
`provider_overrides` (D15) and therefore _is_ in scope inside `assess`. The
replacement reads `provider_overrides[config.provider].unavailability_reason`
when the mapping supplies one that exposes it, and keeps today's code-based
mapping otherwise. Because the OpenAI adapter already exposes exactly that
property with exactly today's string
([openai_hosted.py:507-511](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/providers/openai_hosted.py)),
its observable behaviour does not change — and a test pins that.

The exact set a local provider must clear before the tool exists. FS-08 produces
these strings; **[FS-09 D21](PRD-FS-09-enablement-consent.md) renders them** —
one row per reason below, plus the pre-existing
`openai_hosted_container_control_gap`, and a generic "execution is unavailable on
this computer" with the raw code as copyable support detail for a value it does
not recognise. One reason, never a synthesised second: readiness is a single
provider-supplied verdict, unlike FS-09 D4's additive `unavailableReasons`:

| reason                         | cleared by                                                                                                                         |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| `disabled`                     | `RUNTIME_ENABLE_REMOTE_SANDBOX` truthy **and** `RUNTIME_SANDBOX_PROVIDER=local_container` **and** a parsing `LocalContainerConfig` |
| `local_runtime_unavailable`    | the configured absolute runtime binary exists, executes, and reports a version matching `runtime_kind`                             |
| `local_image_absent`           | the digest-pinned image is present locally (never pulled by us)                                                                    |
| `local_isolation_probe_failed` | every field of `LocalSandboxProbeEvidence` observed true within `probe_timeout_s`                                                  |
| `isolation_unverified`         | `isolation_ready` true, so the registry does not raise `SANDBOX_POLICY_UNSUPPORTED`                                                |
| `provider_unavailable`         | no other `SandboxError` from registry construction                                                                                 |

And beyond readiness, still required for a model-visible tool: gate 5's
composition (desktop profile, file store, prerequisites bundle, artifact/blob/
overlay/run authorities), gate 6's per-invocation recheck, and gate 7's
per-create `satisfies()`.

### D17. The kill switch is discharged honestly, one authority at a time

`FileSandboxAuthorityPrerequisites.resolve` stops returning `None`
unconditionally and instead returns a bundle only when all three are real:

1. **Retained full C1 base+overlay snapshot exporter.** FS-08 extends
   `RuntimeWorkerOverlaySnapshotPlanAuthority` to include base entries, pinned at
   the same retained overlay version and refused wholesale if any base entry
   cannot be read at a stable identity.

   **Corrected by the reconciliation pass.** The first draft said these were
   "read through FS-01's confined read surface". That is wrong three ways and the
   correction changes what has to be built:
   - FS-01's Out of scope excludes "everything on the read path" **and** excludes
     FS-08 by name. FS-01 has no read surface to offer.
   - `fs_open_root` / `fs_open_read_at` live inside the commit helper — a
     spawned, single-purpose C process that speaks one MAC'd command protocol
     over a private channel to desktop **main**. There is no request type for a
     read, and nothing above the helper calls them (FS-01 D1/D5). A Python
     process cannot reach them at all.
   - The base read the worker _can_ perform is the broker's `/v1/fs/read`
     ([broker.ts:80-94](../../../apps/desktop/main/capabilities/broker.ts),
     [broker_client.py:88-92](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/broker_client.py)),
     which is `native/workspace-fs` on macOS — and on Windows is the
     **non-atomic `realpath`-recheck fallback**, because the Win32 walk "is built
     by no script, is carried by no `extraResources` entry" ([README](README.md),
     FS-03 C3). So D17.1's Windows lane is confined only as strongly as FS-03
     makes it, which is why FS-03 is now a hard Windows dependency.

   Two further constraints the first draft did not carry. The module's docstring
   forbids it from naming a host filesystem path at all (C5), so base content
   must arrive as bytes resolved through a worker-held capability, never as a
   path this authority selects. And `SandboxSnapshot`'s contract forbids a host
   path, grant, broker handle, root identity or credential from appearing in the
   snapshot
   ([contracts.py:202-209](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py)) —
   so the base entry reaches the sealed store as `(virtual_path, source_ref)`
   exactly as overlay entries do, and the resolution happens inside
   `SealedSandboxSnapshotFileStore`.

   Whether this half belongs to FS-08 at all is open question 9, and the
   correction strengthens the case for moving it: it is no longer "a slice
   sitting on FS-01's boundary" but a broker-read + snapshot-authority slice
   sitting on **FS-03's**.

2. **Durable A2 result-and-deliverable publisher.** The result and patch halves
   exist (C7). The deliverable half does not, and FS-08 does not pretend it
   does: the launch keeps `deliverables=()`
   ([operation_runner.py:219](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/operation_runner.py)),
   `_publish_result`'s refusal of returned artifacts (`:299-307`) is promoted
   from a TODO to a **permanent invariant with a test**, and the prerequisite is
   renamed in code to what it actually is: a durable A2 result-and-patch
   publisher plus a launch that requests no deliverables. Revision-aware
   deliverable publication is named in Out of scope with its follow-up.
3. **Explicit user-triggered C1 patch importer.** §6, wired into the coordinator
   and reachable only from the desktop import lane (§7).

The docstring's warning stays and gets a test: a provider double or an
artifact-service double must still not be able to turn the bundle on.

### D18. What this sandbox cannot do — stated plainly, because the spine already accepted the cost

It **cannot run the user's toolchain against their real repository in place.**
Concretely:

- **No host mounts, ever.** The workspace is a copy of a bounded, immutable
  snapshot. Nothing the command does touches a host byte until the user imports a
  patch and approves a commit.
- **The toolchain is the image's, not the user's.** Their Node version, their
  virtualenv, their compiler, their global config, their SSH keys, their
  credential helpers: none of it is present, by design
  (`host_credentials_absent`).
- **No network.** `--network none` is the launch posture and `satisfies()`
  compares modes, so `npm install`, `pip install`, `git fetch`, and every
  dependency resolution fail. A build that needs the network cannot run here.
- **Cold every time.** `filesystem_fresh` plus `--rm` means no build cache, no
  incremental state, no warm container between runs.
- **Small and short.** Bounded by `workspace_tmpfs_bytes` (768 MiB default),
  `command_timeout_s` (120 s default, `config.py:56`), `session_wall_time_s`
  (15 min, `:57`), and `commands_per_session` (64, `:59`).
- **One command per operation.** The tool schema is a single `command` string
  ([execute_tool.py:53-59](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/execute_tool.py)).

"Operate on my codebase" is a different product surface. [README D1](README.md)
already stated the reasoning and the accepted cost in full; it is not restated
here. The only thing this PRD adds is the scope note: D1's argument applies with
identical force to a "just this once" escape hatch **inside** FS-08, so the
bullets above are invariants, not defaults.

### D19. Rejected alternatives, recorded

- **A host shell, or any `LocalShellBackend`.** README D1. The façade's own
  docstring already states the rule
  ([policy_backend.py:20](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/policy_backend.py));
  FS-08 adds a test that greps the provider package for `shell=True` and
  `create_subprocess_shell`.
- **Weakening `satisfies()`** (D2).
- **Bind-mounting the sealed snapshot** (D8).
- **A second content source for imported bytes** (D13).
- **Partial patch import** (D14).
- **Pulling the image on demand** (D7).
- **Reusing FS-03's confinement mechanism for the sandbox.** FS-03 confines _our
  own_ services, which are trusted code whose only untrusted input is data. The
  sandbox runs attacker-authored code. Same word, different threat model,
  different bar.

### D20. Metering and cost

`SandboxUsageAttribution.provider_cost_microunits` stays `None`
([contracts.py:601](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py)) —
a local run has no vendor cost, and the coordinator never sets it anyway. The
exactly-once meter keyed by `operation_id` is consumed unchanged; a conflicting
re-record raises `SANDBOX_LIFECYCLE_CONFLICT`
([usage_meter.py:28-42, 60-72](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/usage_meter.py)).

## Implementation plan

Phases are shippable in order. Nothing in phase _n_ requires phase _n+1_ to be
correct, and the model-visible tool appears only at the end of phase 5.

### Phase 1 — Config, ids, and the driver registry (no runtime required)

1. `agent_runtime/capabilities/sandbox/contracts.py` — add
   `SandboxProviderId.LOCAL_CONTAINER` (`:38-43`). No other edit.
2. `agent_runtime/capabilities/sandbox/config.py` — add
   `LocalSandboxRuntimeKind`, `LocalContainerConfig` (+ `from_env` and the
   quota validator), the nine `_EnvFields` names, and the
   `local_container` field plus the `from_env` branch mirroring `:189-195`.
3. `agent_runtime/capabilities/sandbox/providers/local_runtime.py` (new) —
   `LocalRuntimeInvocation`, `LocalRuntimeResult`, `LocalSandboxRuntimeDriver`,
   `AppleContainerDriver`, `OciCliDriver` (parameterised for podman/docker), and
   the closed `LOCAL_RUNTIME_DRIVERS` map. **Pure argv construction — no
   subprocess in this module.**
4. `agent_runtime/capabilities/sandbox/providers/local_executor.py` (new) —
   `LocalRuntimeExecutor`, the only place a subprocess is created, using
   `asyncio.create_subprocess_exec` with an argv list, `stdin`/`stdout` pipes,
   a hard timeout, and process-tree kill on cancel. Never `shell=True`.

### Phase 2 — The probe and the attestation

5. `agent_runtime/capabilities/sandbox/providers/local_probe.py` (new) —
   `LocalSandboxProbeEvidence`, `LocalSandboxProbe.run(...)` implementing D6's
   ten observations, each with its own bounded timeout inside
   `probe_timeout_s`, and the canonical-JSON `probe_digest`.
6. `agent_runtime/capabilities/sandbox/readiness.py` — add the three enum
   members and replace the hardcoded OpenAI branch (`:66-73`) with the
   `unavailability_reason` lookup, preserving today's OpenAI string.

### Phase 3 — The provider and its backend

7. `agent_runtime/capabilities/sandbox/providers/local_container.py` (new) —
   `LocalContainerSandboxProvider` (six port members + four guarded members) and
   `LocalContainerBackend` (`execute`/`aexecute`/`upload_files`/`a_upload_files`/
   `download_files`/`a_download_files`/`ls`/`prepare_execution`), the per-session
   execution-access token, the D5 admission check, and D9's cancellation.
8. `agent_runtime/capabilities/sandbox/provider_registry.py` — add the
   `LOCAL_CONTAINER` branch to `_construct` (`:77-100`) that raises
   `SANDBOX_PROVIDER_UNCONFIGURED`.
9. `agent_runtime/capabilities/sandbox/providers/__init__.py` — export the new
   provider alongside the existing two.
10. `agent_runtime/capabilities/sandbox/policy_backend.py` — D8(a): make `ls` /
    `als` prefer a delegate-supplied implementation after `_guard_path`, using
    the existing duck-typed probe idiom (`:130-162`). **Own commit, own test**,
    because it changes budget accounting for every provider including the two
    remote ones. Added by the reconciliation pass; the first draft had no step
    here and its `ls` fix was inert.

### Phase 4 — Patch import

11. `agent_runtime/capabilities/sandbox/contracts.py` — add
    `baseline_overlay_ref` to `SandboxPatchImportRequest` (`:330-347`).
12. `agent_runtime/capabilities/sandbox/coordinator.py` — `import_patch` gains
    `baseline_overlay_ref` as an **explicit second argument** (§6 resolution
    (a)); `SandboxRunResult` is not widened. The
    `verify_patch(require_complete=True)` call stays exactly where it is. The
    caller that supplies the ref is the same composition boundary that holds the
    run's snapshot plan — name it in the change, because the first draft's
    "thread it through" had no source.
13. `agent_runtime/capabilities/sandbox/patch_import.py` (new) —
    `OverlaySandboxPatchImporter` implementing D12's mapping table, its **four**
    invariants (blob presence, single compare-and-swap revision, re-applied
    `_check_limits` ceilings, full `BasePrecondition` recovery from the retained
    baseline manifest), and D14's mount refusal.

### Phase 5 — Composition and the desktop lane

14. `runtime_worker/sandbox_composition.py` —
    - `FileSandboxAuthorityPrerequisites.resolve` (`:116-135`) returns a real
      bundle when all three of D17's authorities resolve, and `None` otherwise;
    - `FileSandboxWorkerRuntime.compose` (`:320-350`) constructs the local
      provider after an awaited probe and injects it through
      `provider_overrides`, mirroring the OpenAI injection;
    - the coordinator gains the `patch_importer` it is currently constructed
      without (`:252-264`).
15. `runtime_worker/sandbox_snapshot_authority.py` — extend the plan authority
    with base entries whose source is resolved through the **broker read
    surface**, never a host path (D17.1). On Windows this is only as confined as
    FS-03 has made it; do not land this step on Windows before FS-03.
16. `apps/desktop/main/capabilities/workspace-authority.ts` — **declare** the
    change-set `origin` field and its union if FS-04 has not already (Interfaces
    consumed), contributing the member `"sandbox_patch_import"`; add
    `prepareSandboxPatchImport`, `authorizeSandboxPatchImport`, and the
    symmetric refusal in `authorizeCommitFromUserDecision` (`:602-654`).
    `#validateChangeSet` (`:874-941`) learns the new origin. Derive the change
    set's `stageId` / `revision` / `changeSetDigest` / `targetDigest` /
    `proposalDigest` from the immutable overlay revision (§7); `decisionLedgerId`
    is blocked on Open question 8.
17. `apps/desktop/main/services/*` — resolve the container runtime binary to an
    absolute path and export the nine env names through the service env, using
    the same "tell the child what is _true_, not what was _requested_"
    derivation `service-env.ts` already uses for the broker triple. The flag
    itself stays main-process-only.

### Phase 6 — Consent surface (**FS-09's — planned there, not here**)

18. The first draft read: "FS-09 renders the reasons and owns the
    enablement/import affordances." At the time FS-09 disclaimed FS-08 by name,
    so that routing pointed at nothing. **It does now**, as
    [FS-09 D20-D25](PRD-FS-09-enablement-consent.md), with its own implementation
    steps, tests and DoD items in that document. The split is: FS-08 ships the
    reason strings (D16), the main-side import lane (§7), the image contract and
    its digest and expected size (D7), the verbs and entry counts a review reads,
    and the `liveSessionCount` a revoke confirmation needs; FS-09 builds the
    enablement switch, the reason rendering, the download ask, the
    what-leaves-the-folder statement, the import review and the revoke copy.
    **No step of this phase is implemented from FS-08, and phases 1-5 do not
    depend on it** — but no phase of FS-08 yields a user-reachable capability
    until FS-09's execution half lands. That is a shipping-order constraint on
    the same footing as the Windows code-signing certificate
    ([00-consistency-report.md §7](00-consistency-report.md) item 1), not an
    unowned gap.

## Test plan

Every assertion below is runnable without a container runtime except those
marked **(host)**, which need a runtime and are gated to the runtime CI legs.

### Config and ids

- `LocalContainerConfig.from_env` with a tag-only `RUNTIME_SANDBOX_LOCAL_IMAGE`
  raises, and `RemoteSandboxConfig.from_env` therefore yields
  `provider is None` and `enabled is False` — the capability is absent, not
  broadened.
- `workspace_tmpfs_bytes + tmp_tmpfs_bytes + 256 MiB > memory_limit_bytes`
  raises at model construction.
- An unknown `RUNTIME_SANDBOX_LOCAL_RUNTIME` yields a disabled config.
- `SandboxProviderId("local_container")` resolves; `SandboxProviderId` has
  exactly three members.

### Driver registry (pure)

- `LOCAL_RUNTIME_DRIVERS` has exactly two entries and covers every
  `LocalSandboxRuntimeKind`.
- For each driver, `run_detached` argv contains, exactly once each:
  `--network none`, `--read-only`, `--rm`, `--pids-limit`, `--memory`,
  `--memory-swap` equal to `--memory`, `--cpus`, `--security-opt no-new-privileges`,
  `--cap-drop=ALL`, `--user`, and both `--tmpfs` mounts with the configured
  sizes; and contains **none** of `-v`, `--mount`, `--privileged`, `--pid=host`,
  `--ipc=host`, `--userns=host`, `--network host`, `--env-file`.
- `AppleContainerDriver.isolation_kind == "microvm"`;
  `OciCliDriver.isolation_kind == "container"`.
- Every `LocalRuntimeInvocation.argv[0]` equals the configured absolute
  `runtime_path` — no bare binary name ever reaches the executor.

### Probe and readiness

- A fake executor that reports every control observed makes
  `isolation_ready` true; flipping **any single field** to false makes it false
  and yields `local_isolation_probe_failed`.
- A missing image yields `local_image_absent`; a non-executing binary yields
  `local_runtime_unavailable`; a probe exceeding `probe_timeout_s` yields
  `local_isolation_probe_failed`, never `available`.
- `SandboxCapabilityReadiness.assess` with the OpenAI provider still returns
  `openai_hosted_container_control_gap` — regression pin for D16.
- `build_sandbox_backend` returns `None` for every unavailable reason, and
  `SandboxExecuteToolFactory.build` returns `None` for that service — the tool
  is absent, with no fallback tool registered.

### Provider port conformance

- The local provider passes the existing provider conformance expectations used
  for the fakes: `create` idempotency by `idempotency_key` (here: the same
  container name), `terminate` idempotency, `list_owned_sessions` filtering by
  `owner_tag`.
- `isinstance(provider, SandboxProviderPort)` and
  `isinstance(provider, SandboxGuardedProvisioner)` are both true;
  `await provider.create(request)` raises `SANDBOX_POLICY_UNSUPPORTED`.
- `provision_with_capability` with a capability minted by a **different**
  authority instance raises — the object-identity seal
  ([provisioning.py:61-66](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/provisioning.py))
  holds for this provider too.
- `provider_session_ref` matches `^[A-Za-z0-9][A-Za-z0-9._:-]*$` and contains no
  `/`; `cleanup_owner_marker` is ≤255 chars and deterministic for one request.
- The constructor performs no executor call at all — asserted by constructing
  with an executor that raises on every invocation.

### Attestation

- `attest(request).satisfies(SandboxEgressPolicy())` is true for a fully
  observed probe, and false when any one probe field is false — nine
  parameterised cases, one per control.
- `isolation` is `"microvm"` for the Apple driver, `"container"` otherwise, and
  never `"process"`.
- `attestation_ref` changes when any probe field changes (D11), and matches the
  documented grammar.
- `attest` is called on **every** create, and a failing `satisfies` produces
  `SANDBOX_ISOLATION_UNVERIFIED` with no container started.

### Transfer, listing, budget

- `upload_files` for a 3 000-entry snapshot issues exactly **one** executor
  invocation, and its stdin is a tar whose members match the entry paths and
  bytes.
- `ls` does **not** charge the command budget: after 200 `als` calls **driven
  through `PolicyEnforcedSandboxBackend`, not through the delegate**,
  `commands_used == 0`. Driving the delegate directly would pass vacuously and
  prove nothing — that is precisely the mistake D8's first draft made. This test
  must fail against a design where only the provider backend overrides `ls`.
- The same 200 calls against a delegate that does **not** implement `ls`/`als`
  still charge 200 — D8(a) changes accounting only where a native listing
  exists, so the two remote providers are unaffected.
- A hostile deep tree (10 000 nested directories) terminates collection through
  `max_upload_files` rather than through the command budget, and does not hang —
  the bound D8(a) trades away must be replaced by a real one.
- A patch collection over a tree with 300 directories completes without
  `SANDBOX_COMMAND_BUDGET_EXCEEDED`.
- `a_upload_files` / `a_download_files` exist on the backend, so
  `runtime_adapter` and the façade take the native async path (assert the
  `to_thread` fallback is not reached).
- A snapshot whose total bytes exceed half of `workspace_tmpfs_bytes` is refused
  with `SNAPSHOT_QUOTA_EXCEEDED` before any container is created.

### Cancellation and teardown

- Cancelling a run kills the exec child, issues `kill` for the container, and
  confirms absence before `CancelledError` propagates.
- When the kill cannot be confirmed, the session becomes `cleanup_pending`, the
  durable duty survives, and the run reports
  `SANDBOX_EXECUTION_INDETERMINATE` — never `completed`.
- `terminate` on an already-absent container is a no-op; `terminate` on a
  transport failure raises `SANDBOX_CLEANUP_PENDING` and the reaper retries.
- After `terminate`, a retained backend object's `aexecute` raises — the
  execution-access token is deactivated.
- `recover_provisioning(owner_marker)` removes a container created with that
  label and no persisted ref, simulating a crash between container creation and
  ref binding.

### Patch import

- The five-verb mapping table (D12) round-trips: one patch entry of each
  operation produces exactly the specified `OverlayMutation`, and
  `WorkspacePatchSetValidator`-equivalent preconditions match the entry's
  baseline evidence.
- A `create`/`replace` entry whose `result_digest` has no blob fails the whole
  import with `SANDBOX_PATCH_INCOMPLETE`; **no** partial revision is appended
  (assert `get_manifest().version` is unchanged).
- An entry whose path has no mount segment fails with `patch_path_outside_mount`
  and appends nothing (D14).
- `append_revision` is called exactly once, with `expected_version` equal to the
  version in `baseline_overlay_ref`; a concurrent bump makes the import raise
  `WorkspaceOverlayConflictError` and change nothing.
- A manifest whose entries carry two different overlay refs is refused.
- `SandboxPatchImportRequest` still refuses `complete=False`
  ([contracts.py:343-347](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py)).
- **Precondition parity (C10).** For a `replace`/`delete`/`move` entry, the
  `BasePrecondition` the importer emits is field-for-field equal to what
  `_precondition_for_base` produces for the same base entry — including
  `opaque_generation`, `stable_file_id`, `byte_size` and `mtime_ns`. A test that
  only checks `content_digest` would pass against the thin version and is not
  sufficient.
- An entry whose base is absent from the retained baseline manifest fails the
  whole import — the importer never emits a `MUST_EXIST` precondition it could
  not fully populate.
- **Ceilings are re-applied.** A patch that would push the overlay past
  `MAX_ENTRIES` or `MAX_TOTAL_RESULT_BYTES` is refused **before** `append_revision`
  is called, evaluated for the whole batch. Assert `get_manifest().version` is
  unchanged. Without this the import is the one unbounded path into C1.
- **`baseline_overlay_ref` never comes from the model or from the patch.** Assert
  the importer refuses a request whose ref does not match the version the run's
  snapshot plan pinned, and that `SandboxRunResult` gained no field (§6
  resolution (a)).

### Import authority (desktop)

- `authorizeSandboxPatchImport` refuses a prepared state whose `origin` is not
  `"sandbox_patch_import"`; `authorizeCommitFromUserDecision` refuses one whose
  origin **is** — both directions, mirroring FS-04 D6.
- `prepareSandboxPatchImport` on a revoked or expired grant fails through the
  existing `#assertPreparedLive` (`:950-968`).
- `ROUTES` and `ADVERTISED_METHODS` are byte-identical to `main@b349aca2` — a
  snapshot test, because this is the control that keeps the model out.
- An imported change set carries `content.kind === "upload"` for every
  create/replace entry, and the union has no fourth member.
- **The proposal identity is derived, not invented.** `changeSetDigest`,
  `targetDigest` and `proposalDigest` are a pure function of the overlay revision
  passed in: two prepares over the same `overlayRevisionRef` produce the same
  three digests, and a different revision produces different ones.
- A `reviewedChangeSetDigest` that does not match the derived `changeSetDigest`
  is refused **before** a permit is minted, so the field is a control rather than
  decoration.
- The imported change set's entry preconditions carry `stableId` wherever the
  base entry had a `stable_file_id` — the desktop-side half of the C10 parity
  test.

### Composition gates

- `FileSandboxAuthorityPrerequisites.resolve` returns `None` when any one of the
  three authorities is missing, including when a provider double and an
  artifact-service double are supplied — the docstring's promise, as a test.
- With all three present plus a ready provider and the desktop profile,
  `SandboxWorkerBundle.compose` returns a bundle and
  `SandboxExecuteToolFactory.build` returns a tool.
- With `ENTERPRISE_DEPLOYMENT_PROFILE != single_user_desktop`, it returns `None`.
- `_publish_result` still raises when any artifact is returned (D17.2 invariant).
- **End-to-end hermetic:** a fake ready provider + fake executor drives
  snapshot → upload → execute → collect → publish → import, and asserts a real
  overlay revision appears with the expected mutations. This is the test that
  answers the survey's "no downstream path has ever run with a ready provider".

### Host tests **(host)**

Gated behind a CI leg with a runtime installed (macOS runner + Windows runner),
and skipped elsewhere with an explicit skip reason:

- Each of D6's ten probe observations against a real runtime, per driver.
- One end-to-end command run: snapshot in, `sh -c` command, patch out, with a
  non-trivial directory tree.
- Egress: `curl`/socket connect fails and DNS fails inside the container.
- The pinned image contains `python3`, a POSIX shell, `tar`, and runs as a
  non-root uid.

### Guardrail tests

- `grep` the provider package for `shell=True`, `create_subprocess_shell`,
  `os.system`, and `subprocess.run` — zero hits outside the executor, and the
  executor uses `create_subprocess_exec` only.
- `grep` the provider package for `-v `, `--mount`, `--privileged` in any argv
  builder — zero hits.
- No module under `capabilities/sandbox/providers/` imports anything from
  `apps/` or from `agent_runtime.capabilities.workspace` (the importer lives
  outside `providers/` precisely so this stays true).
- **No file under `apps/desktop/native/` is touched by this PRD**, and
  `fs_platform.h` is byte-identical. FS-08 adds no seam member and declares no
  verb; the dependency on FS-01 is negative only.
- **`WorkspaceOverlayStorePort.append_revision` has exactly two production
  callers** after this PRD: `_WorkspaceOverlayMutationEngine` and
  `OverlaySandboxPatchImporter`. A third is a third writer into C1 and the test
  says so.
- No module in `capabilities/sandbox/` names a host filesystem path, and
  `SandboxSnapshotPlan` entries carry `source_ref` only — the D17.1 base-entry
  extension must not weaken `SandboxSnapshot`'s "no host path, grant, broker
  handle, root identity or credential" contract
  ([contracts.py:202-209](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py)).

## Definition of done

- [ ] `SandboxProviderId` has exactly three members and `local_container`
      parses from `RUNTIME_SANDBOX_PROVIDER`.
- [ ] `LocalContainerConfig` refuses a tag-only image, refuses inconsistent
      tmpfs/memory quotas, and a parse failure leaves the capability absent.
- [ ] `LOCAL_RUNTIME_DRIVERS` is closed, covers both platforms, and every argv
      it builds passes the allow/deny flag assertions.
- [ ] No subprocess is created anywhere in the sandbox package except in
      `local_executor.py`, and it uses `create_subprocess_exec` with an argv
      list.
- [ ] `LocalSandboxProbe` observes all ten controls; flipping any one to false
      makes `isolation_ready` false.
- [ ] `isolation_ready` returns `True` for a fully observed probe — the first
      time any provider in this repository has done so.
- [ ] `attest()` returns `isolation ∈ {"container","microvm"}` and never
      `"process"`, and `satisfies()` is true only when all nine controls are
      observed.
- [ ] `attestation_ref` is derived from observed probe values, and the PRD's
      statement that nothing verifies it is reflected in its docstring.
- [ ] The provider is a `SandboxGuardedProvisioner`, `create()` raises, and a
      foreign-authority capability is rejected.
- [ ] `provider_session_ref` and `owner_marker` satisfy their patterns; leak
      sweep and pre-bind recovery both work against real labels.
- [ ] `ls` charges zero commands **when driven through
      `PolicyEnforcedSandboxBackend`**, a 300-directory tree collects a complete
      patch, and a delegate without a native `ls` still charges as before.
- [ ] `fs_platform.h` and every file under `apps/desktop/native/` are unchanged;
      FS-08 declares no seam member and no verb.
- [ ] `append_revision` has exactly two production callers.
- [ ] The importer's `BasePrecondition` is field-for-field equal to
      `_precondition_for_base`'s, and the overlay ceilings are re-applied for the
      whole batch before any append.
- [ ] `SandboxRunResult` gained no field; `baseline_overlay_ref` reaches
      `import_patch` as an explicit argument from a named caller.
- [ ] Transfer is one executor invocation per direction, and the native async
      spellings are used.
- [ ] Cancellation kills the container and confirms it; an unconfirmed kill is
      `indeterminate` and leaves a durable duty.
- [ ] The importer maps all five verbs, appends exactly one overlay revision
      under compare-and-swap, and refuses missing blobs and mountless paths
      **without** appending anything.
- [ ] `SandboxPatchImportRequest.baseline_overlay_ref` is required and is
      recovered from the launch manifest, not from model input.
- [ ] `prepareSandboxPatchImport` / `authorizeSandboxPatchImport` exist, are
      main-only, and the two refusal directions are both tested.
- [ ] `ROUTES` and `ADVERTISED_METHODS` are unchanged, verified by a snapshot
      test.
- [ ] No fourth `WorkspaceContentSource` member exists.
- [ ] `FileSandboxAuthorityPrerequisites.resolve` returns a bundle only when all
      three authorities are real, and doubles cannot turn it on.
- [ ] `_publish_result`'s refusal of returned artifacts is a tested invariant,
      and the launch still passes `deliverables=()`.
- [ ] The hermetic end-to-end test drives a full run with a ready provider and
      asserts the resulting overlay revision.
- [ ] The host test leg passes on **both** a macOS runner and a Windows runner,
      or neither platform ships (D4).
- [ ] `run_in_sandbox` is absent from the toolset for every one of the six
      readiness reasons in D16's table, each asserted individually.
- [ ] `TOOL_DESCRIPTION` names the working directory and nothing else changed in
      `execute_tool.py`.
- [ ] `docs/plan/filesystem-capability/README.md`'s PRD table marks FS-08
      `specified` with the corrected dependency column, and
      `00-consistency-report.md` §4.5 / §7 item 10 / §9 record that it exists and
      what the reconciliation pass changed.
- [x] **A document owns the consent surface.** FS-09 dropped "The sandbox
      provider and patch-back (FS-08)" from its Out of scope and absorbed the
      enablement switch, the reason rendering, the image acquisition, the
      snapshot statement, the import review and the revoke copy as
      [D20-D25](PRD-FS-09-enablement-consent.md). Phase 6 is plannable — in
      FS-09.
- [ ] **FS-09's execution half (D20-D25) has shipped.** FS-08 cannot tick this
      alone, and it is not a defect in FS-08: a provider that clears all seven
      gates still yields no user-reachable capability without a consent surface.
      Same shape as the Windows code-signing certificate in the consistency
      report's §7 item 1 — a named external dependency, no longer an unowned gap.
- [ ] **Open question 8 is answered**: what records the import decision, and
      whether an agent-authored change set may commit without a
      `decisionLedgerId`.

## Out of scope

- **Any new host write path.** Import lands on the existing C2 lane; FS-08 adds
  no seam member and declares no verb in `fs_platform.h`.
- **Implementing the commit verbs.** `create`/`mkdir` are FS-02, `delete`/`move`
  are FS-05, `replace` is FS-06. FS-08 **emits** all five in a patch.

  **The first draft got the refusal point and its granularity wrong, and the
  consequence is a product one.** `parse_entry` refuses `REPLACE`/`DELETE`/`MOVE`
  with a bare `goto fail`
  ([workspace_commit_helper.c:801](../../../apps/desktop/native/workspace-commit-helper/src/workspace_commit_helper.c),
  and FS-01's Out of scope confirms relaxing it is not FS-01's), and
  `parse_entry` runs inside **`command_prepare`**, not at commit. So:
  - the refusal is at **prepare**, not commit;
  - it fails the **entire change set**, not the offending entry;
  - it produces one undifferentiated failure, so nothing "says what the platform
    cannot do" — that message would have to be composed above the helper from
    the change set the caller already holds.

  Combined with D14's no-partial-import rule, one `delete` entry in a patch makes
  the whole import unusable on a build where FS-05 has not landed. The import
  into C1 still succeeds (it is an overlay), so the user sees a reviewable
  proposal that then fails wholesale at prepare. Whoever owns the consent surface
  must pre-check the change set's verbs against the helper's registered platform
  profile and say so **before** the user is shown an approval, which is the same
  failure mode the consistency report's §4.4 rejects for cross-volume grants.

  ~~Nobody owns that today.~~ **[FS-09 D24](PRD-FS-09-enablement-consent.md)
  owns it** — the routing table at the end of this document lists it, and this
  sentence was left stale by the ownership pass
  ([00-consistency-report.md §11](00-consistency-report.md) records the miss).
  D24 makes the pre-check **gate the approve control rather than warn beside
  it**, for the two reasons this bullet establishes: the refusal is wholesale, so
  a disabled-with-a-warning control would imply that clearing the warning is the
  user's job; and it happens at prepare, so there is no per-entry error to
  render. FS-08 supplies the verbs and entry counts; it does not render them.

- **Revision-aware deliverable publication.** The launch keeps
  `deliverables=()` and `_publish_result` keeps refusing artifacts (D17.2).
  Making `SandboxDeliverable` produce immutable artifact revisions is a
  follow-up that must land before any deliverable is requested.
- **Egress allowlists.** `SandboxEgressPolicy` supports `allowlist`
  ([contracts.py:114-115](../../../services/ai-backend/src/agent_runtime/capabilities/sandbox/contracts.py))
  and FS-08 compiles only `deny_all`. An allowlist needs a proxy with an
  observable deny, which is its own slice.
- **Secret leases.** `SandboxSecretLeaseRef` exists (`:168-181`) and the launch
  refuses secrets (`operation_adapter.py:160-164`). Unchanged.
- **Multi-command sessions.** The tool schema stays one command; the session
  budget exists for the derived fs operations, not for a REPL.
- **A bundled microVM or a managed WSL2 distro** (D2).
- **Server-profile composition.** The composition is gated to
  `single_user_desktop` (`sandbox_composition.py:90-91`) and stays there.
- **Windows-only or macOS-only shipping** (D4).
- **A `LocalShellBackend`, a "developer mode" escape hatch, or any host
  execution** (D19).
- **Pruning A2 blobs published by patch collection.** Retention is A2's; FS-08
  only publishes.

## Guardrails

- Do **not** add a port, a tool, a gateway, or a second execution path. One
  provider behind `SandboxProviderPort`, and one importer behind
  `SandboxPatchImportPort`.
- Do **not** relax `satisfies()`, and do not attest a control the probe did not
  observe.
- Do **not** report a control as enforced because a flag was passed — observed
  denial or nothing (FS-03 D2, C8).
- Do **not** bind-mount, pass `-v`/`--mount`, or otherwise put a host path
  inside the provider boundary.
- Do **not** pull, build, or modify an image at run time.
- Do **not** resolve the runtime binary through `PATH`; it is absolute and
  main-supplied.
- Do **not** construct a subprocess with a shell string anywhere in the sandbox
  package.
- Do **not** let the model reach import: no broker route, no advertised method,
  no model-facing argument that names a path, ref, mount, or grant.
- Do **not** import a patch partially. Complete or refused.
- Do **not** let a server approval redeem an import, or a local confirmation
  redeem an agent proposal.
- Do **not** report an outcome that was not observed — an unconfirmed kill or
  teardown is `indeterminate` and keeps its durable duty.
- Do **not** ship the provider on one platform only.
- Do **not** turn the prerequisites bundle on with a double, a flag, or a
  partial authority.
- Do **not** claim in copy, narration, or the tool description that the sandbox
  runs against the user's files. It runs against a copy of a snapshot.
- Do **not** add a seam member, a verb in `fs_platform.h`, or any file under
  `apps/desktop/native/`. FS-01 is a negative dependency only, and it disclaims
  both the read path and FS-08.
- Do **not** add a third writer to `WorkspaceOverlayStorePort.append_revision`,
  and do **not** let the importer emit a precondition weaker than
  `_precondition_for_base` would have produced, or skip the overlay ceilings the
  mutation engine applies.
- Do **not** name a host filesystem path anywhere in `capabilities/sandbox/` or
  in the snapshot plan authority. Base content arrives as `source_ref` resolved
  by the sealed store, never as a path.
- Do **not** attest `isolation` from a value the probe did not measure without
  saying so — it is the one `satisfies()` term that is declared rather than
  observed (D2, D6).

## Open questions and spikes

Each names the experiment, and what a negative result changes.

1. **SPIKE-L1 — `sandbox-exec`'s deprecation status and support horizon.**
   The product already depends on `/usr/bin/sandbox-exec`
   ([macos-workspace-confinement.ts:10](../../../apps/desktop/main/services/macos-workspace-confinement.ts))
   for service confinement, and its deprecation is widely reported but is
   **unverified in this environment**. Test: check the man page and the
   deprecation warning on the minimum supported macOS. **If deprecated and
   scheduled for removal:** nothing in FS-08 changes — FS-08 never uses it —
   but FS-03/FS-09's confinement story inherits a clock, and this PRD should not
   be read as evidence that it is fine.
2. **SPIKE-L2 — Windows runtime viability, elevation, and the whole flag
   matrix.** Test on a clean Windows Home host: does `wsl --install` require
   elevation (expected yes, once); do podman/docker on the WSL2 backend accept
   `--network none`, `--tmpfs` **with `mode=` and `size=`**, `--pids-limit`,
   `--memory`, `--memory-swap`, `--cpus`, `--read-only`,
   `--security-opt no-new-privileges`, `--cap-drop=ALL`, `--user`; and does the
   probe observe all ten controls. **Extended by the reconciliation pass** with
   the two D5 claims that were stated as fact: whether `--storage-opt size=`
   behaves as described across storage drivers (it is only cited as a reason to
   prefer tmpfs, so a negative result changes nothing structural), and whether
   any runtime has a flag that reliably kills a long `exec` (a positive result
   does **not** move the wall clock off our own timer — it only removes the
   justification sentence, since a provider-owned deadline is the safer design
   either way). **If a control cannot be observed on Windows:** the provider is
   unavailable on Windows, and by D4 it does not ship on macOS either until an
   equivalent is found. This is the spike that decides whether FS-08 ships at
   all.
3. **SPIKE-L3 — CPU quota observability.** `--cpus` is accepted by all three
   runtimes, but a bounded probe can only confirm acceptance plus reflection in
   `inspect`, not scheduler share. Test: measure a fixed-work loop at 0.25 CPU
   vs 2 CPU and see whether the ratio is observable within the probe budget.
   **If it is:** upgrade `cpu_quota_accepted` to a measured
   `cpu_quota_enforced`. **If not:** the attestation field keeps its current
   meaning and the docstring says so — a measured-acceptance claim, not a
   measured-enforcement claim.
4. **SPIKE-L4 — blob presence for patch results.** D12 asserts that
   `blob_store.stat(result_digest)` succeeds for bytes published by
   `DeepAgentArtifactPatchCollector` through `ArtifactService.publish_from_stream`,
   on the grounds that A2 asserts `stat.blob_key == revision.content_digest`
   ([artifacts/service.py:489](../../../services/ai-backend/src/agent_runtime/artifacts/service.py)).
   Test: publish through the collector's publisher and `stat` the digest on the
   file-native store. **If it does not resolve:** the importer must carry the
   `ArtifactRef.artifact_id` and resolve through the artifact service instead of
   `content_ref_for_blob`, which changes the overlay entry's `content_ref`
   construction and nothing else.
5. **SPIKE-L5 — Apple `container`: argument surface _and_ isolation class.** Two
   halves, the second added by the reconciliation pass.

   _Arguments._ The `apple_container` driver's flag spellings are **unverified**;
   the design assumes it accepts equivalents of `--network none`, `--memory`,
   `--cpus`, `--rm`, a read-only rootfs, tmpfs mounts, a pids limit, labels,
   `cp` in/out, and `exec`. Test: enumerate the CLI on macOS 26/Apple silicon.
   **If any control has no equivalent:** the Apple driver is dropped from the
   registry and macOS ships with podman/docker only. This does not affect D4
   (both platforms still get the same driver code).

   _Isolation class._ D2's `microvm` claim — "per-container lightweight VM",
   "narrowest host surface" — was stated as fact and is now `unverified`. It is
   the **only** `satisfies()` term the probe never measures, and it is a
   compile-time constant. Test: from inside a running container, is there any
   bounded observation that distinguishes a per-container VM boundary from a
   namespace container in a shared VM? **If yes:** the observation joins
   `LocalSandboxProbeEvidence` and `isolation_kind` stops being a constant. **If
   no:** the Apple driver declares `"container"` — true under either boundary,
   accepted by `satisfies()` identically — rather than asserting a boundary it
   cannot evidence.

6. **SPIKE-L6 — are tmpfs pages charged to the container memory limit?**
   `LocalContainerConfig`'s `workspace + tmp + 256 MiB ≤ memory` validator exists
   only because D5 asserted they are. Test: fill `/workspace` inside a
   memory-limited container and observe whether the container is OOM-killed at
   the memory limit or the write fails at the tmpfs size. **If not charged:** the
   validator is dropped as over-restrictive and the defaults are re-derived. **If
   charged:** it stays and the defaults are correct. Structure is unchanged
   either way; this decides one validator's existence.
7. **Which image, and who builds it.** FS-08 specifies the image's **five**
   requirements (D7, after the reconciliation pass added the setuid probe
   subject) and a digest pin but does not name the image, and the build and
   publication pipeline for it is a product decision (size vs toolchain coverage
   vs who signs it). It must be settled before phase 2, because the probe cannot
   run without it.

8. **What records the import decision — and may an agent-authored change set
   commit without a `decisionLedgerId`?** Blocking; added by the reconciliation
   pass. §7 sets out the problem: `authorizeSandboxPatchImport` mints a permit
   from `{ confirmedByUser: true }` alone, so an imported mutation is the only
   agent-authored mutation in the system that reaches `commitPreparedChangeSet`
   with no ledgered decision, while `authorizeCommitFromUserDecision` binds
   `stageId` / `revision` / `decisionLedgerId` by exact comparison
   ([workspace-authority.ts:601-627](../../../apps/desktop/main/capabilities/workspace-authority.ts)).
   FS-04's restore lane earns the bare-boolean shape because main authors the
   entry from a preimage row it owns; a sandbox patch is authored by code whose
   inputs include MCP-ingested content. Two admissible resolutions:
   - **(a)** the desktop mints a local decision record for the import — its own
     ledger id, written before the permit — and `authorizeSandboxPatchImport`
     binds it exactly as the server path does. Keeps one shape for "an
     agent-authored change was approved"; costs a local decision store.
   - **(b)** the import is declared a user-authored mutation and the absence of a
     ledger id is stated in the receipt and the audit export, so nothing claims
     an approval that does not exist.

   **(a) is the recommendation**, because the audit questions this program is
   held to — who approved it, what changed, where it is logged — have no answer
   under (b). Not decided here: it changes FS-04's `origin` lane. **It is not
   FS-09's either, and FS-09 says so rather than leaving it ambiguous** — it is a
   question about what is recorded server-side, not about what a human is asked
   ([FS-09 Out of scope](PRD-FS-09-enablement-consent.md), [FS-09
   D24](PRD-FS-09-enablement-consent.md)'s "What FS-09 does not decide"). The one
   thing FS-09 binds in the meantime is the copy: the review must not claim the
   import was "approved and recorded" while no approval row exists. So this
   question stays here, with the recommendation, and a resolution has to be made
   before the import lane ships.

9. **Whether the base-file half of D17.1 is FS-08's to write.** Extending the
   snapshot plan authority to include base entries is a real slice of work — and
   after the reconciliation pass it sits on **FS-03's** boundary, not FS-01's:
   the read is the broker's `/v1/fs/read`, which on Windows is unconfined-by-
   packaging until FS-03 lands. It is written into FS-08 because nothing else
   claims it and because the kill switch cannot open without it — but if FS-03 or
   a follow-up would rather own it, the split is clean: FS-08 keeps everything
   from the provider outward, and the snapshot exporter moves. Recorded, not
   decided here.

## Consent surfaces — routed, and where they landed

**This section used to be "Unowned surfaces — recorded, not resolved".** The
FS-08 reconciliation pass found that the first draft routed six things to FS-09
while FS-09's Out of scope disclaimed FS-08 by name and mentioned no sandbox,
container, image or execution surface anywhere — _the routing was to a reader,
not to a document_
([00-consistency-report.md §9.4](00-consistency-report.md)). The product call has
since been made and is recorded in
[00-consistency-report.md §10](00-consistency-report.md): **execution consent is
consent**, so splitting it from the rest of the consent page would produce two
consent models, which is what this program exists to prevent. FS-09 grew to own
it. The table is kept rather than deleted so the routing failure stays legible.

| surface                                                                                              | first draft said                                                            | owner now                                                           |
| ---------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Rendering the readiness reasons (D16) so a user knows why execution is absent                        | FS-09                                                                       | **[FS-09 D21](PRD-FS-09-enablement-consent.md)** — fixed copy table |
| "What to install", without ever installing it (D3)                                                   | FS-09                                                                       | **[FS-09 D21](PRD-FS-09-enablement-consent.md)** — same table       |
| Image acquisition: the size, the consent, the progress (D7)                                          | FS-09                                                                       | **[FS-09 D22](PRD-FS-09-enablement-consent.md)**                    |
| Presenting an imported overlay revision for review before `prepareSandboxPatchImport`                | "existing review surface" (D12) — **unverified that one exists on desktop** | **[FS-09 D24](PRD-FS-09-enablement-consent.md)** — see below        |
| The import affordance itself (§7, Phase 6)                                                           | FS-09                                                                       | **[FS-09 D24](PRD-FS-09-enablement-consent.md)**                    |
| Saying, before an approval sheet, that a patch's verbs cannot commit on this platform (Out of scope) | FS-09                                                                       | **[FS-09 D24](PRD-FS-09-enablement-consent.md)** — verb pre-check   |

**FS-09 took three surfaces FS-08 had not thought to route**, which is the sign
the split is a real ownership boundary rather than a hand-off of a list: **D20**
(execution is its own switch, separate from the filesystem switch, and both
directions need a restart), **D23** (what leaves the granted folder, stated
before it leaves), and **D25** (revoking while a sandbox is live). D20 and D25
consume facts FS-08 must supply — `readinessReason`, the digest and expected
size, and `liveSessionCount` — which is the only direction the dependency runs.

**The split, stated once:** FS-08 keeps the provider, the runtime and its
drivers, the isolation probe and attestation, the image contract, transfer,
cancellation and teardown, the C1 importer and the desktop prepare/authorize
lane. FS-09 builds every surface where a human is asked to agree to any of it.
Nothing in FS-08's code depends on FS-09; nothing in FS-08 becomes
user-reachable without it (Phase 6, and the DoD item that names it).

### What came back to FS-08, and what stays open

Three things, none of them papered over:

1. **FS-08 open question 8 is still FS-08's** — whether an imported change set
   may commit without a `decisionLedgerId`. FS-09 declines it **explicitly and
   for a stated reason**: it is a question about what is recorded server-side,
   not about what a human is asked
   ([FS-09 Out of scope](PRD-FS-09-enablement-consent.md), and D24's "What FS-09
   does not decide"). FS-09 does bind one consequence of leaving it open: until
   it is resolved the review must not tell the user the import was "approved and
   recorded" when no approval row exists — it says the user applied a reviewed
   proposal, and nothing more. That constrains the copy, not the resolution.
2. **One mechanism question is routed _back_ to FS-08.** D24 names
   `TcWorkspaceStageSurface` via `projectWorkspaceStage` as the review surface —
   which answers D12's UNVERIFIED marker at the level of _which surface_ — but
   whether an **imported** revision reaches that projection today is still
   unverified (FS-09 open question 8; not to be confused with FS-08's open
   question 8 above). C6 says applying is unwired by construction. If the
   projection does not happen, the wiring is **FS-08's mechanism**, and the fix
   is to wire it — never to write a second projection to avoid finding out.
3. **D17.1 will invalidate a shipped FS-09 sentence, by design.** FS-09 D23
   State 1's copy — "nothing is copied from your folder" — is true only while the
   snapshot is overlay-only, and it is pinned by a test that **must fail when
   D17.1's base entries land**. That failure is the signal to move the copy to
   State 2, not a broken test. Whoever implements D17.1 owns telling FS-09.

**What is still genuinely unresolved for FS-08 is not ownership.** It is
SPIKE-L2 — whether a Windows container runtime can observe all ten controls —
which by D4 decides whether this PRD ships on either platform. See the spike
register in [README.md](README.md).

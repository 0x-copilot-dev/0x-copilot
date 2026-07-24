# PRD-D3 — Remote sandbox artifact and patch adapter 🎨

**Goal.** Converge remote sandbox execution on operation/artifact/effect contracts.
Sandbox input is an immutable, bounded snapshot; execution is isolated and metered;
outputs become artifacts or a declarative patch; patches stage through the generic
Effect Stager and can reach a workspace only through the workspace executor. The
sandbox never receives local broker authority or a live host mount.

## Implementer brief

Read:

1. `../01-sdr.md` sequence S9 and security/scalability sections.
2. `PRD-D2-builtins-subagents.md` and `PRD-C3-workspace-product-integration.md`.
3. `services/ai-backend/src/agent_runtime/capabilities/sandbox/`.
4. `services/ai-backend/src/agent_runtime/capabilities/sandbox/workspace_transfer.py`.
5. `services/ai-backend/src/agent_runtime/capabilities/sandbox/execute_tool.py`.
6. `services/ai-backend/src/agent_runtime/capabilities/sandbox/policy_backend.py`.
7. `services/ai-backend/src/agent_runtime/capabilities/sandbox/providers/langsmith.py`.
8. `services/ai-backend/src/agent_runtime/capabilities/tools/code_sandbox.py`.

The in-process code helper is not a security boundary and must not be presented as one.

## Context

The existing remote sandbox has typed snapshot/patch contracts and quotas, but the live
tool sends an empty workspace, destroys state after one command, defaults lifecycle
storage/sinks to memory/null, and does not prove deny-all egress. The in-process sandbox
uses ordinary Python builtins and is explicitly unsuitable for untrusted isolation.

## Interfaces consumed

- A3 gateway/descriptors.
- A2 artifact/blob refs.
- A4/A5 staging and sandbox executor kind.
- C1 workspace manifest/overlay.
- C3 workspace apply path.
- Existing provider-neutral sandbox contracts and provider registry.

## Interfaces exposed

- `SandboxOperationAdapter`.
- `SandboxPatchEffectExecutor` only for sandbox-owned external target classes, not
  host workspace; workspace patches use `WorkspaceEffectExecutor`.
- `SandboxRunArtifact`/`SandboxPatchManifest` refs.
- shared sandbox result/patch surface.

## Design

### D1. Capability classes

Descriptors:

- execute with deny-all egress and immutable snapshot:
  `effect_class=none`;
- produce downloadable output/artifact:
  `internal_reversible`;
- patch app-owned artifact/workspace overlay:
  proposal, not direct effect;
- request egress/secrets or external submit:
  gated and separately classified; disabled at initial launch unless explicitly
  implemented.

### D2. Immutable snapshot

Sandbox receives:

- operation/run id;
- manifest of authorized artifact/workspace-overlay refs;
- virtual sandbox paths;
- byte/hash/entry limits;
- no physical paths, grants, broker token, root handles, or user credentials.

Manifest hash is verified after upload. Reject symlinks, devices, sockets, FIFOs,
hard-link ambiguity, sparse amplification, path traversal, and incomplete content.

For local workspace, snapshot is base+overlay materialized at a specific manifest
version. It is not a live mount.

### D3. Isolation attestation

Launch requires provider attestation for:

- process/container isolation;
- deny-all network egress or declared allowlist;
- CPU/memory/wall-clock/process/file quotas;
- fresh/isolated filesystem;
- teardown guarantees;
- no host credential inheritance.

If the configured provider cannot compile/verify requested egress policy, tool is
absent. The in-process adapter is limited to trusted pure computation tests and cannot
back `run_in_sandbox` in production.

### D4. Execution lifecycle

Persist lifecycle events/store:

```text
requested → provisioned → uploading → running → collecting →
completed | failed | cancelled | cleanup_pending | cleaned
```

Use deterministic operation id/idempotency key. Retry before run may resume provision;
uncertain provider execution is not automatically repeated if external egress was
possible.

Command/stdout/stderr are bounded and redacted. Large outputs become refs/artifacts.
Usage and provider cost are metered.

### D5. Output disposition

- textual/scalar result → operation result/activity;
- explicit files requested as deliverables → Artifact Service;
- modified snapshot → `SandboxPatchManifest`;
- no output worth revisiting → no canvas.

Artifacts preserve exact bytes/media type/suggested filename and sandbox operation
provenance.

### D6. Patch manifest

Declarative entries:

```text
create | replace | delete | move | mkdir
path, source_path?
baseline_digest/identity?
result_ref/result_digest?
```

Patch:

- compares against exact input snapshot;
- contains every affected entry;
- is complete=true only after all outputs verified;
- has canonical manifest digest;
- incomplete/oversize patch cannot stage.

Applying to workspace:

1. import patch into C1 overlay;
2. build/revise workspace EffectStage;
3. review;
4. C3 workspace executor commits.

The sandbox never calls Electron main.

### D7. Secrets and egress

Initial default: no secrets, deny-all egress.

Future allowed egress:

- explicit descriptor and policy;
- domain/IP rules compiled by provider;
- scoped expiring secret refs injected by provider, never plaintext in events/tool
  result;
- operation classified by intended external effect;
- side-effecting network action stages through an executor instead of arbitrary shell
  whenever possible.

### D8. UI

Show:

- command summary and isolation/egress posture;
- status/duration/exit code;
- bounded output/raw ref;
- produced artifacts;
- patch file tree/diffs;
- Apply patch action creates/reviews stage;
- clear “sandbox only; local files unchanged” until workspace apply completes.

Focus uses compact execution/artifact/patch cards.

### D9. Cleanup/recovery

- teardown in `finally`;
- cleanup failures persist and retry through janitor;
- content refs retained while artifact/stage/receipt references them;
- orphan sandbox/provider resources enumerated;
- cancellation waits/bounds teardown;
- no successful run completion claim while cleanup state is unknown without visible
  `cleanup_pending`.

## Implementation plan

1. Register descriptors and gateway adapter.
2. Make lifecycle store/event sink durable.
3. Add provider isolation/egress attestation.
4. Wire non-empty immutable snapshots and streamed transfer.
5. Persist outputs/artifacts.
6. Generate/validate patch manifests.
7. Import patches into C1 overlay/stager.
8. Add shared UI/projectors.
9. Add cleanup janitor and recovery.
10. Disable production in-process fallback and run provider smoke.

## Test plan

### Isolation/security

- broker/grant/path/token absent from sandbox inputs;
- deny-all egress probe;
- provider that cannot attest policy is unavailable;
- traversal/symlink/device/sparse bombs rejected;
- secret never appears in logs/events/output.

### Snapshot/patch

- exact manifest upload/download hashes;
- create/replace/delete/move patch;
- incomplete/oversize patch cannot stage;
- base changes before later workspace commit yield C3 conflict;
- patch apply changes overlay only until approval.

### Lifecycle/recovery

- crash/cancel at every lifecycle state;
- cleanup pending retries;
- duplicate invocation/idempotency;
- stdout/output limits and offload;
- usage counted once.

### UI

- compute-only no surface;
- artifact and patch surfaces;
- clear no-local-change copy;
- raw fallback/design parity/accessibility.

## Definition of done

- [ ] Production sandbox has verified isolation and egress policy.
- [ ] Inputs are immutable refs, never live host mounts.
- [ ] Outputs become operation results/artifacts/complete patches.
- [ ] Workspace patches pass C1/C3 staging and approval.
- [ ] Lifecycle/cleanup are durable and recoverable.
- [ ] In-process fallback is not a production security boundary.
- [ ] UI, effect-path where applicable, and standard DoD pass.

## Out of scope

- General unrestricted internet-enabled shell.
- Direct sandbox-to-host apply.
- Silent patch application.
- Long-lived collaborative dev environments.

## Guardrails

- No local broker token or host path in sandbox.
- No unverified egress policy.
- No partial patch treated as complete.
- No direct host mutation.
- No production use of in-process Python as isolation.

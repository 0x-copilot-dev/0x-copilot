# AC7 — Remote sandbox execution

| Field             | Decision                                                                                                       |
| ----------------- | -------------------------------------------------------------------------------------------------------------- |
| Spec ID           | AC7                                                                                                            |
| Status            | Draft; decision-complete and awaiting architecture review                                                      |
| Wave              | 3 — Execution capabilities                                                                                     |
| Estimated effort  | L — 15–20 engineer-days for the provider-neutral layer, LangSmith adapter, transfer path, and cleanup evidence |
| Dependencies      | AC4 artifact store, AC5 scoped host filesystem access                                                          |
| Required for      | AC10 hardening and staged desktop rollout                                                                      |
| Primary owner     | `services/ai-backend` sandbox adapters                                                                         |
| Supporting owners | Runtime worker, Electron capability broker, deployment/security                                                |
| Web impact        | None                                                                                                           |

## Problem and why now

AC6 deliberately supports only a small embedded Python subset. Desktop users will also ask the agent to install dependencies, run tests, build repositories, execute full Python/JavaScript/TypeScript, or use a shell. Those workloads need an isolated operating-system environment with an explicit lifecycle. They must not run in Electron main, the renderer, the trusted AI worker, or through `LocalShellBackend`.

Deep Agents already defines the right integration boundary: a sandbox backend exposes `execute()` and native `upload_files()`/`download_files()` transfer, while filesystem tools are built on the sandbox. LangChain publishes provider integrations for LangSmith, Amazon Bedrock AgentCore, Daytona, Modal, Runloop, Vercel, and E2B. 0xCopilot should adopt that protocol and own policy, transfer, lifecycle, and product contracts around it instead of inventing a vendor-specific execution API.

The difficult work is not selecting a vendor SDK. It is proving what data leaves the device, preventing secrets from entering a workspace bundle, enforcing network egress, applying output patches safely, cancelling and deleting every remote environment, and remaining able to change providers. This PR resolves those product controls.

## Goals

- Add a desktop-only remote execution capability built on Deep Agents `SandboxBackendProtocol`.
- Select exactly one initial provider, `langsmith`, through deployment configuration.
- Keep provider SDK objects behind a product-owned provider registry and lifecycle port.
- Require an explicit, reviewable workspace snapshot; never mount the user’s live AC5 root into a remote environment.
- Transfer changes back as a typed patch artifact and apply them only through AC5’s optimistic-concurrency and approval path.
- Default to no egress and no secrets.
- Represent any optional credential as a short-lived, audience-bound provider-side secret reference, never a plaintext file or request field.
- Enforce session, command, idle, output, file-count, byte, and cost/quota limits.
- Cancel commands, terminate environments, and reap leaked provider sessions after crashes.
- Offload logs, patches, generated files, and other large outputs through AC4.
- Run the same conformance suite against every future provider adapter.

## Non-goals

- Local host shell, Electron `child_process` as a model tool, or `LocalShellBackend` in production.
- Replacing AC6 for cheap pure computation.
- Live bidirectional filesystem mounts, remote editing of the canonical host workspace, or background sync.
- Sending an entire home directory, repository by default, browser profile, keychain, `.git` credentials, or environment file.
- Giving a remote sandbox connector OAuth tokens, model-provider keys, or desktop boot secrets.
- Shipping more than one production provider in this PR.
- SSH host access, dev containers, or arbitrary customer VPC connectivity.
- Automatically applying remote changes to host files.
- Changing web runtime backend selection.

## User experience and failure behavior

### Start

1. The agent proposes **Run in remote sandbox** and shows:
   - provider and region;
   - selected AC5 root and included/excluded file count;
   - total upload bytes;
   - requested egress destinations;
   - named secret references, if any;
   - maximum duration and estimated provider quota class.
2. The user approves this immutable execution envelope. Changing the root, include set, egress, secret refs, region, or hard limits requires another approval.
3. The desktop broker snapshots approved files. The runtime uploads them to `/workspace` through the provider’s native file-transfer API.
4. Commands execute in the remote environment. The activity feed shows command status, duration, bounded output preview, egress policy, and cancellation.
5. On completion, the runtime computes a host-relative patch. The user reviews additions, modifications, and deletions before AC5 applies it.

### Failure behavior

- If remote execution is disabled, unconfigured, unsupported in the selected region, or missing provider credentials, the tool is absent. There is no host fallback.
- A provider provisioning failure returns `sandbox_provision_failed`, retains no partial workspace as a usable session, and queues cleanup by provider session id.
- Upload validation failure occurs before provisioning whenever possible. The UI names the rejected relative path and reason without exposing host absolute paths to the model.
- Egress to an unapproved destination fails closed and is visible as a policy denial, not a generic network error.
- A command timeout stops the command; a session timeout or user cancel terminates the whole environment.
- If termination cannot be confirmed, the run is marked `cleanup_pending`; a durable reaper retries until the provider reports terminal/deleted or an operator-visible deadline is exceeded.
- A download, patch, or quota overflow does not apply a partial host patch. Successfully retrieved outputs remain AC4 artifacts with an incomplete marker.
- Host files changed after snapshot produce AC5 hash conflicts during apply. The user can re-snapshot, inspect, or discard; the runtime never overwrites.
- Provider loss after commands complete but before patch creation results in a visible `outputs_unavailable` state. The agent may not claim changes were applied.

## Alternatives considered

### `LocalShellBackend` or raw host shell

Rejected for production. It executes model-generated commands with the trusted worker’s host permissions and can read secrets, modify app data, attack local services, or persist outside a granted workspace. It is allowed only in isolated tests with a fake temporary root and can never be selected by production settings.

### Docker on the user’s laptop

Rejected for the initial desktop capability. Docker is not reliably installed, desktop daemons carry broad host privileges, volume mounts recreate the live-filesystem risk, and cross-platform support/cleanup is materially harder. A later local-VM provider must implement the same contracts and pass conformance.

### Provider-specific calls throughout the worker

Rejected. It couples execution semantics and stored records to one vendor, makes testing expensive, and prevents enterprise deployment choice.

### Live network filesystem or repository mount

Rejected. It makes rollback, provenance, conflict detection, and consent ambiguous. Snapshot in and patch out creates an auditable boundary.

### Upload Git credentials and let the sandbox clone

Rejected. The workspace snapshot removes the need for a Git credential. If a future workflow requires remote Git operations, it must use an audience-bound, short-lived provider-side reference and approved egress; no long-lived credential enters the sandbox.

### E2B as the initial provider

Not selected, though it remains an optional future adapter. LangSmith is the current Deep Agents zero-setup managed default, is already in the LangChain ecosystem used by this service, and documents snapshots plus an auth/egress proxy. Provider neutrality prevents this first choice from becoming a permanent architecture constraint.

## Architecture and ownership

### Initial provider decision

`RUNTIME_SANDBOX_PROVIDER=langsmith` is the only accepted production value in AC7. `langsmith[sandbox]` is pinned in the AI service only after an implementation spike proves:

- supported regions and desktop packaging;
- default-deny egress through `proxy_config.access_control.allow_list`;
- provider-side secret references without plaintext environment injection;
- native upload/download behavior and limits;
- command cancellation and environment deletion;
- session enumeration sufficient for orphan cleanup.

If any mandatory behavior is unavailable, remote execution remains disabled. No second provider is added inside AC7 to rescue the rollout.

### Provider-neutral layering

```text
RemoteExecutionService
  -> SandboxProviderRegistry
      -> SandboxProviderPort
          -> LangSmithSandboxProvider
              -> provider SDK + Deep Agents LangSmithSandbox
  -> PolicyEnforcedSandboxBackend implements SandboxBackendProtocol
  -> AC4 ArtifactStorePort
  -> AC5 WorkspaceSnapshotPort / WorkspacePatchApplyPort
  -> runtime events, approvals, budgets, audit
```

The registry selects one provider at process start from trusted deployment settings. Model input cannot name a provider, region, image, credentials, or provider session id.

### Integration with the existing Deep Agents backend

The current runtime composes `StateBackend`, draft, and subagent virtual files in `execution/factory.py`. AC7 adds a `PolicyEnforcedSandboxBackend` façade that:

- implements `SandboxBackendProtocol` for `execute`;
- delegates `/workspace/**` filesystem operations to the remote sandbox;
- preserves product-owned `/drafts/**`, `/subagents/**`, `/skills/**`, and `/memories/**` routes;
- rejects paths that cross routing prefixes;
- applies output truncation/offload and command budgets before returning to Deep Agents.

The implementation must not assume that upstream `CompositeBackend` exposes `execute`. A contract test proves the façade remains recognized as a sandbox backend by the pinned Deep Agents version.

### Workspace snapshot

- AC5 remains the authority for host roots and grants.
- The broker produces regular-file bytes plus a deterministic `WorkspaceTransferManifest`.
- Paths are normalized virtual POSIX paths under `/workspace`; absolute host paths never leave the device.
- Symlinks, hard links, junctions/reparse points, devices, sockets, named pipes, alternate data streams, sparse-file amplification, and paths escaping the approved root are rejected.
- Default exclusions are non-overridable in AC7:
  - `.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`;
  - `.ssh/`, `.aws/`, `.azure/`, `.gnupg/`;
  - browser profiles and app `userData`;
  - Git credential helpers, credential files, and keychain exports;
  - `node_modules/`, virtual environments, build caches, and files over the single-file ceiling.
- `.git/` is excluded by default. A future Git-aware transfer may include a sanitized object snapshot, but AC7 uploads working files only.
- The default upload ceiling is 10,000 files, 512 MiB total, and 64 MiB per file. Deployment policy may lower it.
- Each manifest row includes relative path, byte size, SHA-256, normalized executable bit, and content artifact ref.
- AC4 commits the manifest and content refs before provider provisioning is considered ready.

Provider upload uses `upload_files()` or its native equivalent. It does not build a shell command containing file content and does not unpack an untrusted archive with host tools.

### Patch transfer and host apply

At the end of execution:

1. Walk `/workspace` through provider APIs under the same path/type/size rules.
2. Compare path hashes against the baseline manifest.
3. Build `WorkspacePatchManifest` with `add`, `modify`, and `delete` operations and expected baseline hashes.
4. Store changed bytes once in AC4; do not include them inline in run events.
5. Present a diff/review surface.
6. On approval, AC5 revalidates the grant and expected host hash and applies each file atomically.
7. Record per-path outcome. A conflict aborts that path; it does not silently take “remote wins.”

No host write occurs from the provider adapter or AI worker.

### Egress policy

- `deny_all` is the default.
- An admin-defined catalog may allow exact HTTPS hosts and ports. The user may select only a subset.
- Wildcards, raw IPs, CIDRs, non-HTTPS ports, private/link-local/loopback ranges, cloud metadata endpoints, `.local`, and DNS results that become private are denied in AC7.
- Every redirect and DNS resolution is rechecked by the provider network control, not only by command pre-validation.
- Package registries are not implicitly allowed. The execution proposal names them when needed.
- The LangSmith adapter compiles the immutable policy to Auth Proxy `allow_list`. It verifies the effective policy before upload. If the provider cannot express the requested policy exactly, provisioning fails.
- Egress bytes and denied destinations are measured when the provider exposes them; logs never contain URL query strings or authorization headers.

### Short-lived secret references

`SandboxSecretLeaseRef` is a reference to a provider-side or deployment-side secret, not secret material:

- maximum lifetime 15 minutes and never beyond the sandbox session;
- exact allowed host/path audience;
- read-only/write capability classification;
- user/admin approval id;
- no generic “all environment variables” reference.

AC7 initially permits only deployment-admin-configured LangSmith workspace-secret references used by Auth Proxy. Connector OAuth tokens, model-provider BYOK keys, browser cookies, Electron secrets, and arbitrary user environment variables are ineligible. The provider proxy injects a header for the approved destination; the sandbox filesystem, environment, command output, and model never receive the secret value.

Revocation or expiry fails closed. Reference ids are salted or hashed in user-visible events to avoid exposing deployment naming.

### Lifecycle and limits

| Control                  | Default |               Hard ceiling |
| ------------------------ | ------: | -------------------------: |
| Provisioning timeout     |    60 s |                      120 s |
| Command timeout          |   120 s |                     15 min |
| Session wall time        |  15 min |                     60 min |
| Idle timeout             |   5 min |                     15 min |
| Commands per session     |      64 |                        256 |
| Combined command preview |  64 KiB |                    256 KiB |
| Download file count      |  10,000 |                     25,000 |
| Download changed bytes   | 512 MiB |                      2 GiB |
| Cleanup confirmation     |    30 s | 2 min before durable retry |

Provider resource class, vCPU, memory, image/snapshot, region, and ceilings come from deployment policy. The model cannot raise them.

The worker owns a `try/finally` termination call. A durable `sandbox_sessions` projection records non-secret provider ids, lease expiry, and cleanup state so a separate reaper handles worker death. Cleanup uses idempotent provider delete/stop semantics.

## Typed contracts

```python
class SandboxProviderId(StrEnum):
    LANGSMITH = "langsmith"


class SandboxEgressPolicy(RuntimeContract):
    mode: Literal["deny_all", "allowlist"]
    destinations: tuple[str, ...] = ()


class SandboxSecretLeaseRef(RuntimeContract):
    lease_id: str
    audience_hosts: tuple[str, ...]
    expires_at: datetime
    capability: Literal["read", "write"]


class WorkspaceTransferEntry(RuntimeContract):
    path: str
    sha256: str
    size_bytes: int
    executable: bool
    payload_ref: "PayloadRef"


class WorkspaceTransferManifest(RuntimeContract):
    format_version: Literal[1] = 1
    workspace_id: str
    root_grant_id: str
    created_at: datetime
    entries: tuple[WorkspaceTransferEntry, ...]
    total_bytes: int
    manifest_sha256: str


class SandboxCreateRequest(RuntimeContract):
    run_id: str
    workspace_snapshot: WorkspaceTransferManifest
    egress: SandboxEgressPolicy
    secret_refs: tuple[SandboxSecretLeaseRef, ...]
    limit_profile: str
    approval_id: str


class ManagedSandboxSession(RuntimeContract):
    session_id: str
    provider: SandboxProviderId
    provider_session_ref: str
    created_at: datetime
    expires_at: datetime
    cleanup_state: Literal["active", "terminating", "deleted", "cleanup_pending"]


class WorkspacePatchEntry(RuntimeContract):
    operation: Literal["add", "modify", "delete"]
    path: str
    baseline_sha256: str | None
    result_sha256: str | None
    result_size_bytes: int | None
    payload_ref: "PayloadRef | None"


class WorkspacePatchManifest(RuntimeContract):
    format_version: Literal[1] = 1
    session_id: str
    baseline_manifest_sha256: str
    entries: tuple[WorkspacePatchEntry, ...]
    complete: bool
    manifest_sha256: str


class SandboxProviderPort(Protocol):
    async def create(self, request: SandboxCreateRequest) -> "SandboxHandle": ...
    async def status(self, provider_session_ref: str) -> ManagedSandboxSession: ...
    async def terminate(self, provider_session_ref: str) -> None: ...
    async def list_owned_sessions(self, owner_tag: str) -> tuple[ManagedSandboxSession, ...]: ...
```

`SandboxHandle.backend` is runtime-only and implements pinned Deep Agents `SandboxBackendProtocol`; it is excluded from all Pydantic serialization.

### Stable errors

- `sandbox_disabled`
- `sandbox_provider_unconfigured`
- `sandbox_policy_unsupported`
- `snapshot_invalid`
- `snapshot_quota_exceeded`
- `sandbox_provision_failed`
- `sandbox_upload_failed`
- `sandbox_command_timeout`
- `sandbox_session_expired`
- `sandbox_egress_denied`
- `sandbox_secret_expired`
- `sandbox_cancelled`
- `sandbox_download_failed`
- `sandbox_patch_incomplete`
- `sandbox_cleanup_pending`

### Configuration

```text
ENTERPRISE_DEPLOYMENT_PROFILE=single_user_desktop
RUNTIME_ENABLE_REMOTE_SANDBOX=false
RUNTIME_SANDBOX_PROVIDER=langsmith
RUNTIME_SANDBOX_REGION=<deployment-approved-region>
RUNTIME_SANDBOX_LIMIT_PROFILE=desktop_v1
LANGSMITH_API_KEY=<deployment secret>
```

Provider credentials remain excluded from `RuntimeSettings.model_dump`, logs, run context, events, artifacts, snapshots, and child command environments.

## Critical current and proposed files

### Current evidence and integration points

- `services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py` — Deep Agents backend construction and tool exclusions.
- `services/ai-backend/src/agent_runtime/execution/factory.py` — current `CompositeBackend` assembly for subagent and draft routes.
- `services/ai-backend/src/agent_runtime/execution/contracts.py` — runtime dependencies and feature flags.
- `services/ai-backend/src/runtime_worker/dependencies.py` — production worker adapter factory.
- `services/ai-backend/src/runtime_worker/handlers/run.py` — run cancellation, budgets, events, and cleanup boundary.
- `services/ai-backend/src/agent_runtime/capabilities/tools/code_sandbox.py` — local trusted-only executor that is explicitly not the remote sandbox.
- `apps/desktop/main/services/supervisor.ts` and `service-env.ts` — desktop service lifecycle and trusted environment construction.
- `services/ai-backend/pyproject.toml` and `requirements.txt` — pinned Deep Agents/LangSmith versions.

### Proposed implementation files

- `services/ai-backend/src/agent_runtime/capabilities/sandboxes/contracts.py`
- `services/ai-backend/src/agent_runtime/capabilities/sandboxes/ports.py`
- `services/ai-backend/src/agent_runtime/capabilities/sandboxes/provider_registry.py`
- `services/ai-backend/src/agent_runtime/capabilities/sandboxes/remote_execution_service.py`
- `services/ai-backend/src/agent_runtime/capabilities/sandboxes/policy_backend.py`
- `services/ai-backend/src/agent_runtime/capabilities/sandboxes/workspace_transfer.py`
- `services/ai-backend/src/runtime_adapters/sandboxes/langsmith.py`
- `services/ai-backend/src/runtime_worker/jobs/sandbox_reaper.py`
- `services/ai-backend/tests/contract/sandboxes/test_provider_conformance.py`
- `services/ai-backend/tests/integration/runtime_worker/test_remote_sandbox_lifecycle.py`
- `services/ai-backend/docs/features/remote-sandboxes.md`

AC5 implementation supplies the broker-side snapshot and patch-apply ports; AC7 does not import Electron code or a sibling service implementation.

## Security and threat model

| Threat                         | Control                                                             | Required evidence                        |
| ------------------------------ | ------------------------------------------------------------------- | ---------------------------------------- |
| Host command execution         | Remote-only provider backend; production ban on `LocalShellBackend` | Configuration and import-path tests      |
| Secret copied in snapshot      | Non-overridable excludes, content/path scanner, manifest review     | Secret corpus tests                      |
| Data exfiltration              | Default-deny provider egress and destination approval               | Live staging egress tests                |
| Private-network access         | Private/link-local/loopback/metadata deny in provider policy        | DNS/rebinding/redirect tests             |
| Long-lived secret theft        | Provider-side, 15-minute, audience-bound refs only                  | Environment/filesystem/output assertions |
| Snapshot path escape           | AC5 root validation; no links/devices/reparse points                | macOS/Windows adversarial path suite     |
| Provider compromise            | Minimal data envelope, region policy, no connector tokens, deletion | Vendor review and incident runbook       |
| Unreviewed host modification   | Patch artifact plus AC5 approval and expected hashes                | Conflict/apply integration tests         |
| Duplicate paid session         | Idempotent create key and durable provider session record           | Worker-crash provisioning tests          |
| Orphan cost/data               | `finally` cleanup plus durable reaper and provider enumeration      | Fault injection and leak dashboard       |
| Output bomb                    | Provider and product byte/file ceilings, AC4 offload                | Large-output tests                       |
| Prompt injection in repository | Egress/secrets remain fixed; patch requires user review             | Malicious README/test fixture            |

Isolation does not make untrusted repository instructions trustworthy. It limits execution blast radius; approval and data policies still govern what leaves and what is applied.

## Persistence, retention, deletion, and recovery

- AC2 stores sandbox lifecycle and command metadata as typed events.
- AC4 stores the input manifest, input file payloads, bounded-overflow logs, changed output bytes, and patch manifest.
- The derived session projection stores provider id, opaque provider session ref, owner tag, timestamps, approval id, policy hashes, and cleanup state. It contains no credential.
- Active sessions and their input refs remain pinned until the session is terminal and cleanup is confirmed.
- Input workspace snapshots expire 7 days after a terminal run by default; output patches, generated artifacts, and raw command logs expire after 30 days. Main-chat previews, hashes, command status, and expired-artifact markers remain until explicit chat deletion.
- Legal hold pins all referenced artifacts and cleanup evidence but does not keep a paid remote sandbox alive. Provider environments are still destroyed; local evidence remains.
- Explicit chat deletion removes unreferenced local artifacts. It cannot recall data already processed by a provider, so provider deletion confirmation and vendor retention terms are recorded in deployment documentation.
- On worker restart, the reaper enumerates durable non-terminal sessions, checks provider status, terminates expired/abandoned sessions, and records deletion evidence. It never recreates a sandbox from a command log.
- A resumable active run may reconnect only to the exact provider session and policy hashes. If unavailable, it terminalizes visibly and offers a new approved snapshot; it does not silently create a replacement.

## Observability and audit

### Events

- `sandbox.approval_requested`
- `sandbox.provision_started`
- `sandbox.provisioned`
- `sandbox.upload_completed`
- `sandbox.command_started`
- `sandbox.command_completed`
- `sandbox.egress_denied`
- `sandbox.patch_created`
- `sandbox.patch_apply_requested`
- `sandbox.cancelled`
- `sandbox.cleanup_started`
- `sandbox.cleanup_confirmed`
- `sandbox.cleanup_pending`
- `sandbox.failed`

Events include provider, region, policy/manifest hashes, file and byte counts, duration, exit code, truncation/offload flags, patch operation counts, cleanup attempt, and correlation ids. They exclude commands by default from logs, file content, absolute paths, URL query strings, headers, secret-reference names, provider credentials, and output content.

### Metrics

- `runtime_sandbox_sessions_total{provider,outcome}`
- `runtime_sandbox_active_sessions{provider}`
- `runtime_sandbox_provision_seconds{provider}`
- `runtime_sandbox_command_seconds{provider,outcome}`
- `runtime_sandbox_transfer_bytes_total{direction}`
- `runtime_sandbox_egress_denied_total`
- `runtime_sandbox_cleanup_pending`
- `runtime_sandbox_cleanup_seconds{provider}`
- `runtime_sandbox_patch_conflicts_total`

Audit records identify the user, approver, provider/region, workspace grant, manifest hash, egress destinations, secret-ref count, commands’ redacted digests, patch summary, apply approver, cleanup result, retention, and deletion evidence. Provider-side audit export is a deployment control and must not be claimed unless configured and tested.

## Acceptance criteria

- Remote execution appears only in the desktop profile with its feature and policy gates enabled.
- `langsmith` is the only shipped provider, selected by deployment configuration; missing/unhealthy config has no fallback.
- The model cannot select or alter provider, image, region, secrets, egress, host root, or hard limits.
- Every session has a user-approved immutable snapshot/egress/secret envelope.
- Default sessions have no network and no secrets.
- Upload and download use provider-native transfer behind Deep Agents `SandboxBackendProtocol`.
- Host files never change until a typed patch passes AC5 review, grant revalidation, and expected-hash checks.
- Cancel, timeout, worker crash, app shutdown, and provider errors all converge to confirmed cleanup or durable `cleanup_pending`.
- Provider conformance tests are independent of LangSmith and can be applied unchanged to future AgentCore, Daytona, Modal, Runloop, Vercel, or E2B adapters.
- Production code contains no reachable `LocalShellBackend` or raw host-shell fallback.
- Web/Postgres behavior and existing virtual draft/subagent files remain unchanged.

## Detailed test plan

### Provider conformance

Run one suite against a fake and the LangSmith adapter:

- create with an idempotency key;
- execute success, nonzero exit, timeout, cancel, and output truncation;
- upload/download binary and Unicode paths;
- reject traversal, links, devices, and oversize entries;
- status and expiry;
- idempotent terminate/delete;
- enumerate sessions by owner tag;
- fail closed when egress or secret policy cannot be represented.

Live-provider tests run in a controlled staging account, not normal PR CI and not with production secrets.

### Snapshot and patch

- Deterministic manifest hash regardless of host directory enumeration order.
- macOS Unicode normalization/case tests and Windows case, reserved-name, long-path, junction, reparse-point, and alternate-stream tests.
- Default secret/cache exclusion corpus.
- Baseline add/modify/delete/rename-as-delete-plus-add.
- Host edit after snapshot produces conflict.
- Partial download produces no applicable complete patch.
- Duplicate patch apply is idempotent.
- Apply rejection leaves all host files unchanged.

### Egress and secret security

- Deny DNS, HTTP, HTTPS, raw TCP, redirects, IPv4/IPv6 private ranges, localhost, link-local, metadata, `.local`, and DNS rebinding under `deny_all`.
- Permit only each exact approved host under allowlist.
- Assert secret value is absent from `/proc`-equivalent environment, filesystem, command output, logs, events, snapshots, and artifacts.
- Expire/revoke the lease during a command and verify the next request fails closed.
- Malicious repository instructions cannot expand egress or secret refs.

### Lifecycle and fault injection

- Kill worker during provision, upload, execute, download, patch creation, and cleanup.
- Cancel during every lifecycle state.
- Simulate provider 429, quota exhaustion, regional outage, stale status, and delete failure.
- Restart reaper and prove eventual cleanup with no duplicate delete side effect.
- Assert zero provider sessions remain after the test owner tag is swept.

### Regression and performance

- Existing `/drafts`, `/subagents`, `/skills`, and `/memories` routing remains correct.
- Non-desktop and disabled desktop runs do not import provider extras or register execute.
- Snapshot a 10,000-file/512-MiB boundary fixture within published time/memory budgets.
- Web/Postgres test suite sees unchanged tool catalog and settings.

## Rollout, migration, and backout

1. Land contracts, fake provider, policy backend, and conformance suite with the feature disabled.
2. Pin and stage the LangSmith adapter in a non-production workspace; validate egress, transfer, region, secret refs, and cleanup.
3. Enable internal users for no-egress/no-secret snapshots under 50 MiB.
4. Expand size limits and read-only package-registry allowlists after transfer/cost evidence.
5. Enable patch apply only after AC5 conflict and approval tests.
6. Allow approved provider-side secret refs only after a separate credential-handling review.
7. AC10 canary and default rollout follows.

Stop conditions include any host-shell fallback, secret exposure, private-network reachability, unreviewed host write, missing/duplicate patch entry, orphan session beyond the cleanup SLO, unexplained provider cost, or deletion-confirmation failure.

Backout removes the tool from new runs and calls terminate on all owned active sessions. Existing patches and artifacts stay reviewable under retention. A patch never requires a live provider to apply. Provider configuration and package can be removed only after the reaper reports zero owned sessions.

There is no migration from AC6 or `CodeSandboxPort`.

## Definition of done

- AC4 and AC5 are implemented and AC7 is accepted.
- A component-local implementation spec pins Deep Agents, LangSmith, SDK extras, region, image/snapshot, and provider policy behavior.
- Provider registry, LangSmith adapter, policy backend, transfer service, patch builder, cancellation, reaper, artifacts, events, metrics, and audit are implemented.
- Conformance, security, path, fault-injection, lifecycle, load, and web-regression suites pass.
- A staging deletion/cleanup drill and cost/quota dashboard are documented.
- `services/ai-backend/docs/features/remote-sandboxes.md` and operator runbook cover enablement, region, egress, secrets, incident disable, and cleanup.
- Production builds prove `LocalShellBackend` and raw host shell are unreachable.

## Why this is sane under SOLID, DRY, KISS, and single-source-of-truth

- **Single responsibility:** provider adapters translate SDKs; execution service owns lifecycle; AC5 owns host files; AC4 owns bytes.
- **Open/closed:** future providers implement one port and conformance suite without changing runtime orchestration.
- **Liskov substitution:** every adapter must satisfy identical execute, transfer, cancel, status, and cleanup semantics.
- **Interface segregation:** the agent receives a policy-wrapped sandbox backend, not provider clients or credentials.
- **Dependency inversion:** runtime code depends on product contracts and Deep Agents protocol, not LangSmith SDK types.
- **DRY:** one snapshot manifest, patch format, egress policy, and lifecycle record across providers.
- **KISS:** one initial provider, snapshot in, patch out, deny-all defaults, no live mount, and no host fallback.
- **Single source of truth:** host workspace remains canonical; AC4 owns transferred bytes; provider environment is disposable; AC2 owns lifecycle evidence.

## Residual risks

- A remote provider processes user source. Deployment region, vendor terms, deletion behavior, and enterprise allowability remain explicit deployment controls.
- Provider egress telemetry may be incomplete. Policy enforcement must not depend only on after-the-fact logs.
- Large repositories may exceed transfer limits. The user must narrow roots or use a future Git-aware snapshot; limits are not silently relaxed.
- Remote output can still contain malicious code. Patch review and later execution controls remain necessary.

## References

### Repository

- [`services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py`](../../../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py)
- [`services/ai-backend/src/agent_runtime/execution/factory.py`](../../../../services/ai-backend/src/agent_runtime/execution/factory.py)
- [`services/ai-backend/src/runtime_worker/dependencies.py`](../../../../services/ai-backend/src/runtime_worker/dependencies.py)
- [`services/ai-backend/src/agent_runtime/capabilities/tools/code_sandbox.py`](../../../../services/ai-backend/src/agent_runtime/capabilities/tools/code_sandbox.py)
- [`docs/architecture/service-boundaries.md`](../../../architecture/service-boundaries.md)

### Official prior art and provider documentation

- [Deep Agents sandboxes](https://docs.langchain.com/oss/python/deepagents/sandboxes) — `SandboxBackendProtocol`, `execute`, and native file transfer.
- [LangChain sandbox integrations](https://docs.langchain.com/oss/python/integrations/sandboxes) — supported provider catalog.
- [LangSmith sandbox integration](https://docs.langchain.com/oss/python/integrations/sandboxes/langsmith) and [LangSmith Auth Proxy](https://docs.langchain.com/langsmith/sandbox-auth-proxy) — selected initial provider and egress/secret controls.
- [Amazon Bedrock AgentCore integration](https://docs.langchain.com/oss/python/integrations/sandboxes/aws) — future provider candidate.
- [Daytona integration](https://docs.langchain.com/oss/python/integrations/sandboxes/daytona) — future provider candidate.
- [Modal integration](https://docs.langchain.com/oss/python/integrations/sandboxes/modal) — future provider candidate.
- [Runloop integration](https://docs.langchain.com/oss/python/integrations/sandboxes/runloop) — future provider candidate.
- [Vercel integration](https://docs.langchain.com/oss/python/integrations/sandboxes/vercel) — future provider candidate.
- [E2B integration](https://docs.langchain.com/oss/python/integrations/sandboxes/e2b) — optional future provider.
- [Claude Cowork architecture](https://support.claude.com/en/articles/14479288-claude-cowork-desktop-architecture-overview) — mounted-root permission checks and code execution in a hypervisor-isolated VM; cited as product prior art, not an implementation dependency.
- [Cursor Cloud Agents](https://cursor.com/docs/cloud-agent) — public behavior for isolated VMs, snapshots, artifacts, network policy, browser/desktop control, and MCP; no inference about private internals.

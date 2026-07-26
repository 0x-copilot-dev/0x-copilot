# PRD-AR-J1 — Managed local MCP runtime packages

**Goal:** Let desktop deployments install and run approved local stdio MCP servers from immutable package specifications while preventing models, projects, and package scripts from gaining arbitrary host execution.

## Metadata

| Field                   | Value                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Status                  | Optional / proposed                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Wave                    | J — advanced capability platform                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Primary owners          | Desktop host runtime, backend MCP catalog and policy                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Supporting owners       | AI backend MCP client, backend facade, security engineering                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Depends on              | [D1 MCP convergence](../../generative-surfaces-v2-1/prds/PRD-D1-mcp-convergence.md), [F8 MCP control-plane freshness and session reuse](./PRD-AR-F8-mcp-control-plane-freshness-session-reuse.md), [F3 policy-aware capability discovery](./PRD-AR-F3-policy-aware-capability-discovery.md), [F4 task-aware tool-use controller](./PRD-AR-F4-task-aware-tool-use-controller.md), [A3 operation gateway](../../generative-surfaces-v2-1/prds/PRD-A3-operation-gateway.md), [E1 accountability lifecycle](../../generative-surfaces-v2-1/prds/PRD-E1-accountability-lifecycle.md) |
| Primary success measure | An approved package can be installed reproducibly and exposed as ordinary MCP tools without arbitrary command execution, unreviewed network/filesystem access, or inherited secrets                                                                                                                                                                                                                                                                                                                                                                                             |

## Implementer brief

Read:

- `services/backend/src/backend_app/mcp_catalog.py`
- `services/backend/src/backend_app/mcp_oauth.py`
- `services/backend/src/backend_app/connectors/`
- `services/backend/src/backend_app/token_vault.py`
- `services/ai-backend/src/agent_runtime/capabilities/mcp/`
- `services/ai-backend/src/agent_runtime/capabilities/`
- `apps/desktop/main/`
- `apps/desktop/preload/`
- `apps/desktop/renderer/`
- `apps/desktop/native/`
- `tools/desktop-runtime/`
- `services/backend-facade/src/backend_facade/`
- F3, F4, A3, and E1

The MCP transport enum recognizes stdio, but server records and creation APIs are URL-oriented and there is no governed local process launcher. Package launchers such as `npx` and `uvx` execute software; they are not skill installers and must never be reachable as generic shell tools. The desktop main process or a dedicated local broker owns process execution. AI backend receives a normal MCP connection and tool cards only.

## Desktop-first deployment and storage contract

The launch composition is the shipped `single_user_desktop` profile: Electron main supervises `backend`, `ai-backend`, `backend-facade`, and the local data services on loopback; the renderer still calls only the facade. This is a local application boundary, not a mandatory cloud dependency.

- Runtime-owned run/event/citation/artifact state must use existing `RuntimePorts`. Desktop defaults to the single-writer file adapter under `<userData>/agent-data/v1` with the in-process worker; tests use in-memory and future hosted deployments may use Postgres.
- Product configuration already owned by `backend` may use its existing embedded local Postgres database. This PRD must not add another database, daemon, queue, or network hop merely for desktop.
- Authorization is expressed as the signed-in local user, device capability grants, project/conversation scope, and provider credentials. Existing internal organization identifiers remain compatibility partition keys; they are not exposed as B2C team or administrator concepts.
- A future consumer web or opt-in sync product may add remote adapters behind the same ports and contracts. Sync, team administration, fleet policy, and always-online availability are not desktop launch dependencies.
- Feature flags resolve locally, work offline wherever no external provider is intrinsically required, and include a bounded disk/CPU/memory budget plus an immediate local backout path.

## Problem statement

Many useful MCP servers are distributed as npm or Python packages and expect stdio transport. Requiring every user to run and secure those processes manually makes local connectors hard to adopt. Naively accepting command strings, `npx` arguments, or package names from a model creates remote-code-execution, supply-chain, persistence, secret-exfiltration, and cross-profile risks.

The product needs a managed local runtime with signed catalog metadata, exact package resolution, content-addressed installation, sandboxed process execution, explicit capabilities, secret references, health management, updates, rollback, and an MCP-only data plane.

## Current state and strengths to preserve

- Backend owns MCP registration, OAuth state, token vault, catalog, and user policy.
- AI backend dynamically loads MCP tools through policy middleware and permission checks.
- Desktop already supervises an embedded database and service runtime with explicit staging and boot contracts.
- F3/F4 define capability discovery and tool-use controls.
- The product has audit, approval, and governed-effect plans for sensitive actions exposed by MCP tools.

## Objectives and outcomes

1. Install only catalog-approved, immutable package versions with verified provenance.
2. Run local MCP servers without a shell and with a least-privilege sandbox.
3. Expose only validated MCP protocol traffic to AI backend.
4. Bind filesystem, network, environment, secrets, and resource limits to an approved manifest.
5. Cache installations safely and reuse healthy processes without changing package identity.
6. Detect, quarantine, update, and roll back vulnerable or unhealthy packages.
7. Give users and users clear consent, status, logs, and removal controls.

## Scope

- npm and Python package ecosystems through fixed broker adapters
- Signed package manifests, exact versions, integrity digests, lock records, and provenance
- Content-addressed package cache and isolated runtime environment
- Sandboxed stdio process lifecycle and MCP handshake
- Secret-reference/environment binding, network and filesystem policy
- Health, crash recovery, resource quotas, update/rollback, audit, and removal
- Desktop-local availability projection into the existing MCP catalog

## Non-goals

- Accepting arbitrary commands, shell fragments, repository scripts, or unpinned Git URLs
- Installing skills through package launchers
- Running local packages on the hosted backend
- Granting package-requested permissions automatically
- Promising isolation stronger than the host OS primitives actually configured and tested
- Replacing remote HTTP/SSE MCP servers
- Letting a model install, update, start, or configure packages without an
  explicit user decision

## Interfaces consumed

- D1 canonical MCP catalog, naming, permissions, and client contracts
- F8 catalog-revision invalidation, schema fingerprinting, and safe MCP session reuse
- Backend connector profiles, MCP OAuth, token-vault references, user policy, and audit
- Desktop supervisor, native-host, secure-storage, preload, and renderer host boundaries
- F3/F4 capability discovery and tool-use policy
- A3 operation classification for consequential tools exposed by a local server

## Interfaces exposed

Facade/catalog:

```text
GET    /v1/mcp/local-package-catalog
POST   /v1/mcp/local-package-installs
GET    /v1/mcp/local-package-installs
POST   /v1/mcp/local-package-installs/{install_id}/approve
POST   /v1/mcp/local-package-installs/{install_id}/start
POST   /v1/mcp/local-package-installs/{install_id}/stop
POST   /v1/mcp/local-package-installs/{install_id}/update
POST   /v1/mcp/local-package-installs/{install_id}/rollback
DELETE /v1/mcp/local-package-installs/{install_id}
```

Desktop broker IPC is versioned and available only to the trusted desktop main process:

```text
resolve(manifest_digest)
install(resolution_id, approval_receipt)
start(install_id, runtime_grant)
connect(install_id) -> local MCP channel
health(install_id)
stop(install_id)
remove(install_id)
```

Renderer code and web content never receive raw process or filesystem handles.

## Core contracts

```text
LocalMcpPackageManifest
  manifest_version
  package_id
  ecosystem: npm | python
  registry
  package_name
  exact_version
  package_integrity
  provenance_attestation
  entrypoint_id
  protocol_version
  tool_schema_expectation_digest
  install_script_policy
  network_policy
  filesystem_mounts[]
  environment_schema[]
  secret_bindings[]
  cpu_memory_process_limits
  publisher
  risk_class
  signature

LocalMcpInstall
  install_id
  device_id
  profile_id
  user_id
  manifest_digest
  resolved_lock_digest
  content_store_digest
  status: requested | awaiting_approval | installing | installed |
          starting | healthy | stopped | degraded | quarantined |
          update_available | removing | removed | failed
  approved_permissions
  active_version
  previous_version
  health
  last_error_code

LocalMcpRuntimeGrant
  install_id
  manifest_digest
  profile_id
  user_id
  allowed_mount_handles[]
  allowed_network_destinations[]
  secret_lease_refs[]
  resource_limits
  expires_at
  nonce
  signature
```

No contract contains a free-form command, shell string, arbitrary environment map, or plaintext secret.

## Detailed design

### 1. Catalog and trust

Packages enter a curated or user-added catalog through a review pipeline. The catalog record pins registry, name, exact version, integrity, transitive lock digest, entrypoint, publisher identity, license, vulnerability assessment, expected permissions, and signed manifest.

User profile-managed entries require an user and display a higher-risk label. Revoked manifests cannot start, even if cached.

### 2. Resolution

Resolution occurs in a broker-owned temporary environment with a registry allowlist and no application credentials. npm and Python adapters invoke fixed binaries with fixed argument templates; user or model text never reaches an argument position except validated package coordinates from the signed manifest.

Resolution outputs an immutable lock, package digests, SBOM, license inventory, provenance result, and vulnerability snapshot. Dependency confusion is prevented by registry pinning and namespace policy.

### 3. Installation

Artifacts are downloaded, verified, malware-scanned, and unpacked into a content-addressed store. Installation is atomic: the broker builds a new directory, verifies it, then publishes an immutable reference.

Lifecycle scripts are denied by default. A package requiring an install script must declare its digest and behavior, pass enhanced review, run in the install sandbox, and never receive user secrets or approved runtime mounts.

### 4. Runtime isolation

The desktop broker launches the exact reviewed entrypoint directly, without a shell. The runtime sandbox enforces, where supported:

- read-only package root;
- isolated writable scratch directory;
- explicit user-selected file or directory handles;
- deny-by-default network with hostname/port allowlist;
- minimal environment;
- no inherited stdin other than MCP, TTY, clipboard, browser cookies, SSH agent, cloud metadata, or desktop service tokens;
- CPU, memory, process, file, output, and wall-clock limits; and
- child-process denial unless the manifest explicitly allows a reviewed helper.

If a platform cannot enforce a requested control, installation fails or the UI presents a policy-approved degraded-isolation decision. It never silently weakens the manifest.

### 5. Secrets

The backend vault stores secret values. The broker receives short-lived secret leases scoped to an install, process instance, and field name. Values are injected through the least-exposed OS mechanism available and redacted from logs. Packages cannot enumerate other secret names.

OAuth callback and refresh remain backend-owned. Runtime restart obtains a new lease rather than persisting plaintext locally.

### 6. MCP handshake and schema pinning

The broker validates framing, message size, protocol version, initialization, tool schemas, resource schemas, and timeouts before advertising the server. Tool cards flow through the existing MCP catalog and F3 policy-aware discovery.

Unexpected tool additions or materially changed schemas place the install in `degraded` pending review. The AI backend sees stable namespaced capability IDs, not process commands.

### 7. Process pool and cache

Package bytes are shared by digest across eligible installs; profile/user configuration, scratch storage, secrets, and processes are isolated. The broker may keep a bounded warm process only when the manifest declares reset semantics and tests prove no cross-session state leakage.

LRU eviction removes stopped runtime environments only after confirming no active connection. Package content remains until no install references it and retention policy permits collection.

### 8. Lifecycle, update, and rollback

Crash policy is bounded restart with exponential backoff and a circuit breaker. Repeated protocol violations or resource abuse quarantine the install.

Updates are explicit and install side-by-side. The new version passes health and schema checks before traffic switches. Rollback selects the last approved immutable version and issues new runtime grants. Security policy may force stop or require an update, with auditable user override where allowed.

### 9. Removal

Stop revokes secret leases, closes MCP connections, terminates the process tree, and seals scratch data. Removal deletes install configuration and eligible scratch/cache references. Shared content is garbage-collected only when reference count reaches zero. The user is told what remains under local audit, retention, or backup policy.

## Ownership and service boundaries

| Responsibility                                                  | Owner                                               |
| --------------------------------------------------------------- | --------------------------------------------------- |
| Catalog, user policy, approvals, secret vault, audit            | Backend                                             |
| Package resolution, installation, sandbox, processes, local IPC | Desktop main/broker                                 |
| MCP discovery and tool invocation                               | AI backend                                          |
| Product API aggregation                                         | Backend facade                                      |
| Install and permission UI                                       | Shared chat/settings surface via desktop host ports |

The renderer cannot launch processes. AI backend cannot invoke package managers. Backend cannot import desktop code. The local broker exposes only versioned IPC and MCP channels.

## Persistence, retention, and deletion

- Backend stores catalog metadata, approvals, install identity, policy, and audit.
- Desktop stores signed lock records, content digests, package bytes, encrypted nonsecret configuration, health, and bounded logs.
- Plaintext secrets are not persisted by the broker.
- Scratch retention is manifest- and local-profile-governed with secure deletion where supported.
- Removal and profile/user deletion traverse installs, secret leases, scratch, local logs, and backend records.
- SBOM, manifest, approval, and security-event records follow the user's local
  retention/export policy and any explicit backup setting.

## Authentication, authorization, security, and audit

- The local capability grant binds install actions to the verified desktop
  instance and signed-in user.
- Install, permission expansion, update, rollback to vulnerable versions, and sensitive mount changes require explicit authorized decisions.
- Runtime grants are signed, short-lived, nonce-bound, and manifest-bound.
- Every package digest is verified before install and before start.
- Network uses broker-enforced resolution protections against loopback, private ranges, rebinding, and disallowed redirects unless explicitly approved.
- Audit covers catalog publish/revoke, resolve, scan, request, approve, install, start, schema change, secret lease, crash, quarantine, update, rollback, stop, and remove.
- Security incidents can revoke a manifest globally and stop matching processes.

## Performance and capacity budgets

- Catalog and installed-list reads: p95 under 250 ms.
- Warm MCP connection: p95 under 500 ms.
- Cold process start and handshake: p95 under 5 seconds, package-specific exceptions declared.
- Cached installation verification: p95 under 2 seconds for ordinary packages.
- Tool-call broker overhead after connection: p95 under 20 ms excluding server work.
- Cache lookup is `O(1)` by digest; garbage collection is linear in stored manifests and runs off the interaction path.
- Default limits cap package bytes, dependency count, process count, memory, output, and concurrent local servers.

## Failure, idempotency, and recovery

- Install is idempotent by device, profile, user, and manifest digest.
- Interrupted install leaves no published partial environment.
- Start uses a process-instance nonce; lost responses reconcile through health before another launch.
- Broker crash revokes leases and reconstructs state from signed lock records; it does not auto-start packages unless policy permits.
- Schema mismatch, signature failure, digest mismatch, scan failure, or sandbox setup failure fails closed.
- MCP timeouts cancel requests and may trip a circuit breaker without killing unrelated servers.
- Removal is resumable and records undeleted retained items.

## Observability and quality gates

Metrics:

- resolve/install/start latency and failure category;
- cache hit and bytes;
- warm/cold starts;
- process CPU, memory, child count, output, and restarts;
- protocol/schema violations;
- denied network/filesystem/secret attempts;
- vulnerability age and update adoption;
- tool latency/error by package version; and
- quarantine, rollback, and removal outcomes.

Trace lineage is `catalog_manifest → install → process_instance → MCP session → tool_call → operation`.

Release gates:

- no arbitrary command or environment injection in fuzz tests;
- packages cannot read undeclared files, secrets, or network destinations;
- renderer and model cannot access process-launch IPC;
- digest/provenance failure always blocks start;
- schema drift is detected before tool advertisement;
- removal, secret revocation, update, and rollback pass crash tests; and
- platform isolation claims are backed by executable tests and documentation.

## Rollout and backout

1. Ship catalog and broker in audit-only developer mode.
2. Enable one first-party read-only server on one desktop platform.
3. Add signed curated packages with no secrets or writable mounts.
4. Enable scoped secrets and mounts for explicit local-user approvals.
5. Add the second package ecosystem and additional operating systems after isolation gates.
6. Open user-added catalog entries behind user policy.

Backout revokes affected manifests, stops processes, removes tool advertisements, preserves signed records, and leaves remote MCP unaffected. Package bytes may remain quarantined until incident review or retention cleanup.

## Implementation slices

1. Manifest/signature/catalog and user policy
2. Desktop broker IPC and device binding
3. npm resolver, lock/SBOM/provenance, and content store
4. OS sandbox, mounts, network proxy, resources, and secret leases
5. MCP handshake, schema pinning, and AI-backend adapter
6. Lifecycle, health, cache, update, rollback, and removal
7. Python ecosystem adapter
8. Security conformance, fuzzing, operations, and staged rollout

## Test plan

- Unit: manifest validation, fixed argv, digest, policy narrowing, state transitions
- Supply chain: registry confusion, changed tarball, unsigned provenance, vulnerable dependency
- Sandbox: undeclared file/network/secret/process access on every supported OS
- Protocol: malformed frames, oversized messages, schema drift, hangs, output floods
- Integration: catalog approval through MCP tool call and governed effect
- Fault injection: crash during resolve/install/start/update/remove
- Security: renderer compromise, forged grant, stale device, DNS rebinding, symlink escape
- Retention: scratch cleanup, shared cache references, user deletion, and local backup policy
- Performance: cold/warm starts, cache pressure, concurrent servers

## Definition of done

- A curated package installs reproducibly from an immutable signed manifest.
- The broker launches no shell and exposes no generic package-manager command.
- Sandboxed processes receive only approved resources and short-lived secrets.
- AI backend sees validated ordinary MCP capabilities and never process details.
- Update, rollback, quarantine, stop, remove, and global revoke are reliable and audited.
- Each supported platform passes its isolation, supply-chain, retention, and crash gates.

## Guardrails

- Package convenience never implies host execution authority.
- Package coordinates, entrypoints, dependencies, and permissions are immutable reviewed data.
- Model output cannot become argv, environment, mount paths, registry URLs, or update choices.
- No inherited host secrets, credentials, cookies, agents, or broad home-directory access.
- No silent isolation downgrade or automatic permission expansion.
- Unverified, revoked, vulnerable-beyond-policy, or schema-changing packages fail closed.

## Open decisions

1. Whether first release supports npm only or npm plus Python.
2. Which provenance standard and vulnerability/license policies are mandatory.
3. Which OS sandbox primitives are minimum-supported versus degraded mode.
4. Whether any package install scripts are permitted in the first public release.
5. Whether warm pooling is allowed before per-package reset conformance exists.

# PRD-FS-09 — Enablement, consent, and capability-honest reporting

**Status:** specified
**Depends on:** FS-02 (hard — the verbs the consent copy enumerates must exist on
both platforms before this ships). Soft-couples to FS-03 (supplies the
machine-readable `unavailableReason` this PRD renders), FS-04 (supplies
`trashStatus.admit`, the only honest pre-commit recoverability signal), FS-05 /
FS-06 (add verbs the mode copy enumerates, and FS-06's `open_holder_detection`
asymmetry), FS-07 (supplies the unresolved-record report and the `Recheck`
action).

## Implementer brief

Everything in this program is unreachable to a user today: the grant IPC is
registered in Electron main and **no product UI calls it**, so there is no way to
turn the capability on, grant a folder, see a grant, or revoke one. Meanwhile the
model is told to assert a constant — "`wrote_to_filesystem` is `false` here, so no
such claim is true" — which stops being true the moment FS-02 lands.

FS-09 makes the capability discoverable, grantable, revocable, and honestly
narrated. Enabling stays opt-in on a flag that is OFF by default (spine D3).
Turning it **off** takes effect instantly; turning it **on** takes effect at the
next boot, because the gate is read before the supervised children are spawned and
their broker credentials are injected. And the model's claim about where content
went becomes a function of the run's real filesystem posture rather than a
hardcoded sentence.

## Context

Everything below is verified against `main@b349aca2`.

### C1. The gate exists and is correct; the UI to reach it does not

`isDesktopFilesystemEnabled` ([feature-gate.ts:23-29](../../../apps/desktop/main/capabilities/feature-gate.ts))
fails closed: only `1|true|yes|on|enabled`, case- and space-insensitive
([feature-gate.ts:16](../../../apps/desktop/main/capabilities/feature-gate.ts)),
enables. It is read from `process.env` at exactly three points, all in
`main/index.ts`:

| Line                                                    | Effect                                                                                        |
| ------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| [index.ts:641](../../../apps/desktop/main/index.ts)     | `isDesktopFilesystemEnabled(process.env) && app.isPackaged` gates `MacosWorkspaceConfinement` |
| [index.ts:673-674](../../../apps/desktop/main/index.ts) | gates `startCapabilitySubsystem(...)`; otherwise `capabilitySubsystem` is `null`              |
| [index.ts:681-686](../../../apps/desktop/main/index.ts) | logs the disabled state and the flag name that enables it                                     |

With the subsystem `null`, `capabilityService` stays `null`
([index.ts:164](../../../apps/desktop/main/index.ts)), so the capability IPC block
([index.ts:967-975](../../../apps/desktop/main/index.ts)) is never registered and
every capability call fails closed at the bridge.

The flag is **main-process-only**. It is not in `ENV_PASSTHROUGH_ALLOWLIST`
([service-env.ts:16-41](../../../apps/desktop/main/services/service-env.ts)), so no
child service ever sees it. Children learn the capability from
`RUNTIME_ENABLE_DESKTOP_WORKSPACE` plus the three broker vars, which
`service-env.ts` derives from whether the broker actually started
([service-env.ts:370-386](../../../apps/desktop/main/services/service-env.ts); the
same derivation appears inline at
[index.ts:699-711](../../../apps/desktop/main/index.ts)). That shape is correct and
FS-09 must not change it: the child is told what is _true_, not what was
_requested_.

`app.isPackaged` at [index.ts:641](../../../apps/desktop/main/index.ts) means a
dev/unpackaged run **cannot** obtain C2 write authority even with the flag set.
`createCapabilityService` then installs `UnavailableNativeWorkspaceAuthority`
([capabilities/index.ts:67](../../../apps/desktop/main/capabilities/index.ts)),
`workspaceWritableBootstrap` is false, the grant store is constructed without
`rootIdentity`
([capabilities/index.ts:77-79](../../../apps/desktop/main/capabilities/index.ts)),
and every grant minted in that posture is permanently read-only to C2 — writes
require `rootIdentity !== undefined`
([workspace-authority.ts:812](../../../apps/desktop/main/capabilities/workspace-authority.ts)).

### C2. The boot ordering fact that decides this PRD's shape

The gate runs at [index.ts:641](../../../apps/desktop/main/index.ts) / [:673](../../../apps/desktop/main/index.ts),
inside `app.whenReady()` ([index.ts:590](../../../apps/desktop/main/index.ts)).
`activeAuthService` — the only object that can resolve a verified account — is
assigned at [index.ts:852](../../../apps/desktop/main/index.ts), inside
`wireTransportAndIpc`, which is called at
[index.ts:811](../../../apps/desktop/main/index.ts) and
[:827](../../../apps/desktop/main/index.ts). Both call sites are **after** the
gate.

`AuthService.accountKey` additionally requires a loaded verified session and
returns `null` without one
([auth/index.ts:484-495](../../../apps/desktop/main/auth/index.ts)); `resolveFirstRunKey`
already documents and handles that null
([index.ts:528-534](../../../apps/desktop/main/index.ts)).

Between the gate and sign-in, the supervised children are spawned with
`supervisedEnv` ([index.ts:687-712](../../../apps/desktop/main/index.ts)), which is
where `RUNTIME_ENABLE_DESKTOP_WORKSPACE`, the broker URL, the broker token and the
attestation triple are injected. A child cannot be handed a broker that starts
later.

**Consequence:** any enablement decision keyed on the signed-in account is
unresolvable at the moment the gate reads it — it would resolve to `null`
forever, and the capability would never turn on. This is a structural fact, not a
preference, and D1 is built on it.

### C3. Grants: the model exists, the surface does not

`CAPABILITY_CHANNELS`
([channels.ts:15-24](../../../apps/desktop/main/capabilities/channels.ts)) declares
four channels. Main registers all four
([index.ts:967-975](../../../apps/desktop/main/index.ts),
[ipc/handlers.ts:413-472](../../../apps/desktop/main/ipc/handlers.ts)). A repo-wide
search for the three grant channels finds **no renderer, web-app or chat-surface
caller** — the only hits outside main are in
`apps/desktop/preload/bridge.test.ts`. The only capability channel any product UI
consumes is `decideWorkspaceApproval`
([renderer/workspaceApprovalPort.ts:56](../../../apps/desktop/renderer/workspaceApprovalPort.ts)).
`requestFolderGrant` / `listGrants` / `revokeGrant` are dead ends.

What the existing model already gives us:

- `FolderPicker.pick()` opens the native dialog, `realpath`s the selection and
  confirms it is a directory
  ([folder-picker.ts:57-86](../../../apps/desktop/main/capabilities/folder-picker.ts)).
  The renderer never submits or receives a path.
- `GrantStore.create` refuses ungrantable roots (`/`, home, userData, credential
  dirs) at the store, not at the picker
  ([grant-store.ts:132-135](../../../apps/desktop/main/capabilities/grant-store.ts)),
  captures `rootIdentity`, resolves `profileId` through a **main-only** resolver
  the picker never sees
  ([grant-store.ts:138-142](../../../apps/desktop/main/capabilities/grant-store.ts),
  [:217-229](../../../apps/desktop/main/capabilities/grant-store.ts)), and defaults
  `expiresAt` to now + 30 days
  ([grant-store.ts:119](../../../apps/desktop/main/capabilities/grant-store.ts),
  [:155](../../../apps/desktop/main/capabilities/grant-store.ts)).
- The store is encrypted via `safeStorage` under
  `<userData>/capabilities/grants.bin`, written temp → fsync → rename → dir-fsync
  ([grant-store.ts:250-285](../../../apps/desktop/main/capabilities/grant-store.ts)),
  and refuses to write plaintext unless an explicit dev fallback is enabled
  ([grant-store.ts:295-299](../../../apps/desktop/main/capabilities/grant-store.ts)).
- `RendererGrant` is the only grant shape allowed across IPC —
  `{grantId, mode, label, status}`
  ([types.ts:79-93](../../../apps/desktop/main/capabilities/types.ts)) — and
  `RendererGrantSchema` is `.strict()`, parsed on the way out
  ([schemas.ts:45-53](../../../apps/desktop/main/capabilities/schemas.ts),
  [ipc/handlers.ts:154-156](../../../apps/desktop/main/ipc/handlers.ts)). An extra
  key throws rather than leaking.
- The preload allowlist is derived, not hand-maintained: `bridge.ts` admits any
  channel for which `isCapabilityChannel(channel)` holds
  ([preload/bridge.ts:9](../../../apps/desktop/preload/bridge.ts),
  [:22](../../../apps/desktop/preload/bridge.ts)). Adding a name to
  `CAPABILITY_CHANNELS` allowlists it automatically — there is no second list to
  update, and FS-09 must not create one.

What it does **not** give us: `RequestFolderGrantParamsSchema` accepts only
`{mode, label?}`
([schemas.ts:19-29](../../../apps/desktop/main/capabilities/schemas.ts)) and
`CapabilityService.requestFolderGrant` passes only `{root, mode, label}` to the
store ([service.ts:47-60](../../../apps/desktop/main/capabilities/service.ts)).
Subtree scope and duration are therefore **not user-choosable**: every grant is
whole-tree (`normalizePrefixes(undefined)` → `[""]`,
[grant-store.ts:411](../../../apps/desktop/main/capabilities/grant-store.ts)) for
30 days. And `RendererGrant` carries neither expiry nor scope, so a Settings page
built on today's projection could not honestly display either.

### C4. Permits are one-use; grants are durable

The spine's guardrail warns against collapsing these, and the code already
separates them:

- A **grant** is durable, revocable, expiring authority over one folder tree,
  reused across runs until revoked or expired.
- A **permit** is a single-use authorization for exactly one commit.
  `WorkspaceApprovalPermitSource.take` consumes the approval reservation _before_
  minting
  ([workspace-approval.ts:130-133](../../../apps/desktop/main/capabilities/workspace-approval.ts)),
  and `commitPreparedChangeSet` sets `consumed = true` before touching the native
  helper
  ([workspace-authority.ts:670-680](../../../apps/desktop/main/capabilities/workspace-authority.ts)).
  A replay returns the recorded result or `workspace_conflict`; it never commits
  twice.

FS-09 keeps this split and names it in the UI, because "you granted a folder" and
"you approved this one write" are different consents.

### C5. Revocation: immediate for writes, deliberately _not_ for in-flight reads

`#assertPreparedLive` re-resolves the live grant, re-checks mode and prefixes, and
re-verifies root identity, and it runs at authorize **and** at commit
([workspace-authority.ts:950-968](../../../apps/desktop/main/capabilities/workspace-authority.ts),
called at [:613](../../../apps/desktop/main/capabilities/workspace-authority.ts)
and [:676](../../../apps/desktop/main/capabilities/workspace-authority.ts)).
`#liveGrants` filters on `status === "active"`, `profileId === facts.userId`,
`deviceId === facts.deviceId`, `expiresAt !== undefined && expiresAt > now`,
`rootIdentity !== undefined` and `allowedPathPrefixes !== undefined`
([workspace-authority.ts:796-815](../../../apps/desktop/main/capabilities/workspace-authority.ts)).
So a revoke between approval and commit denies the commit. That is already true
and needs no change.

Two facts fall out of that filter and matter later:

1. **Per-account isolation of _authority_ already exists.** A grant is usable only
   by the profile that minted it
   ([workspace-authority.ts:808](../../../apps/desktop/main/capabilities/workspace-authority.ts)).
   Starting the subsystem for a machine does not give account B access to account
   A's folders.
2. **A grant with no `expiresAt` is unusable for writes but visible to reads.**
   `#liveGrants` requires `expiresAt !== undefined`
   ([:810-811](../../../apps/desktop/main/capabilities/workspace-authority.ts))
   while `GrantStore.listActive` treats `expiresAt === undefined` as unexpired
   ([grant-store.ts:167-175](../../../apps/desktop/main/capabilities/grant-store.ts)).
   Any new "no wall-clock expiry" duration would land in that gap. D5 does not.

Reads are different. `RunContextStore` pins the active grant set at run start, and
the module comment states the consequence outright: a grant revoked **after** a run
started still authorizes that run's context-bound ops until the run ends
([run-context.ts:29-33](../../../apps/desktop/main/capabilities/run-context.ts)).
`CapabilityBroker.#resolveGrant` returns the **pinned** grant when a
`run_capability_context` is supplied and only falls back to live state when it is
not
([broker.ts:794-815](../../../apps/desktop/main/capabilities/broker.ts)). A
Settings page that said "revoking stops access immediately" would today be false
for exactly this case.

### C6. The pre-commit review surface exists, is mounted, and already has the vocabulary

`TcWorkspaceStageSurface` is rendered by the Run cockpit
([RunDestination.tsx:3566-3593](../../../packages/chat-surface/src/destinations/run/RunDestination.tsx)).
Its view model is path-free by type: `WorkspaceStageTarget` is
`{mountLabel, virtualPath}` with the header comment "Never pass a local filesystem
path as either field"
([workspaceStageProjection.ts:53-57](../../../packages/chat-surface/src/thread-canvas/workspaceStageProjection.ts)),
and `safeWorkspaceVirtualPath` rejects anything outside `/workspace`
([:269-287](../../../packages/chat-surface/src/thread-canvas/workspaceStageProjection.ts)).
The pledge string is `"Only this revision and target will be applied."`
([:9-10](../../../packages/chat-surface/src/thread-canvas/workspaceStageProjection.ts)).

`WorkspaceStageResolutionState` already includes `grant_revoked`, `indeterminate`
and `reconciling`, each with committed copy
([:33-43](../../../packages/chat-surface/src/thread-canvas/workspaceStageProjection.ts),
[:198-243](../../../packages/chat-surface/src/thread-canvas/workspaceStageProjection.ts)).
FS-09 wires these; it does not invent a parallel vocabulary.

**Two gaps in that surface, verified:**

- **`unknown` is approvable today.** The operation-kind comment says "Canonical
  data was incomplete; render a held review, never guess a write"
  ([:18-19](../../../packages/chat-surface/src/thread-canvas/workspaceStageProjection.ts)),
  but `canDecide`
  ([:354-360](../../../packages/chat-surface/src/thread-canvas/workspaceStageProjection.ts))
  is a function of `decisionBlocked`, `hasReviewableTarget`, `decisionAvailable`,
  `revision` and `stageId` — and **not** of `operationKind`. A stage with
  `kind: "unknown"`, `status: "staged"` and a valid virtual path renders a live
  Approve button. The comment describes an invariant the code does not enforce.
- **There is no recoverability field at all.** `WorkspaceStage`
  ([:129-152](../../../packages/chat-surface/src/thread-canvas/workspaceStageProjection.ts))
  has `preview`, `diff`, `baseline`, `precondition`, `resolution`, and three
  capability booleans — nothing about whether the displaced bytes will be kept.

Approval is confirmed **natively on every approve**, deliberately, because the
receipt carries no trusted destructive bit and trusting a renderer classification
would let a destructive stage take the weaker path
([workspace-approval.ts:173-215](../../../apps/desktop/main/capabilities/workspace-approval.ts)).

Commit outcomes are a five-value closed vocabulary, identical on both sides of the
broker: `applied | already_applied | precondition_drift | failed | indeterminate`
([workspace-authority.ts:172-177](../../../apps/desktop/main/capabilities/workspace-authority.ts),
[broker_client.py:428-441](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/broker_client.py)).
An exception during commit maps to `indeterminate` with the message "The workspace
change outcome could not be confirmed."
([workspace-authority.ts:701-716](../../../apps/desktop/main/capabilities/workspace-authority.ts)).
The receipt projection already keeps `indeterminate` as its own status
([projectReceiptV2.ts:724-727](../../../packages/chat-surface/src/destinations/run/projectReceiptV2.ts)).

### C7. The narration defect

`_ARTIFACT_DESTINATION_RULE` ends with a **constant global assertion**:

> "…`wrote_to_filesystem` is `false` here, so no such claim is true."
> — [prompts/tools.py:54-62](../../../services/ai-backend/src/agent_runtime/prompts/tools.py)

It is embedded verbatim in both artifact tool descriptions
([tools.py:103](../../../services/ai-backend/src/agent_runtime/prompts/tools.py),
[:126](../../../services/ai-backend/src/agent_runtime/prompts/tools.py)), which are
frozen defaults on the tool dataclasses
([publish_artifact.py:173](../../../services/ai-backend/src/agent_runtime/capabilities/tools/builtin/publish_artifact.py),
[revise_artifact.py:135](../../../services/ai-backend/src/agent_runtime/capabilities/tools/builtin/revise_artifact.py)).
The result fields it refers to are server-derived and correct
([publish_artifact.py:225-226](../../../services/ai-backend/src/agent_runtime/capabilities/tools/builtin/publish_artifact.py),
[revise_artifact.py:178-179](../../../services/ai-backend/src/agent_runtime/capabilities/tools/builtin/revise_artifact.py)).

Two problems:

1. **It over-reaches from a result fact to a run fact.** "`wrote_to_filesystem` is
   false _for this publish_" is true forever. "_so no such claim is true_" is a
   statement about the whole turn, and it is wrong as soon as a sibling
   `write_file` under `/workspace/` really commits.
2. **The contradiction is already reachable today.** The artifact tools are gated
   by `_artifact_publication_enabled`
   ([run.py:1630](../../../services/ai-backend/src/runtime_worker/handlers/run.py),
   [:1796-1808](../../../services/ai-backend/src/runtime_worker/handlers/run.py));
   the workspace prompt block is gated by `workspace_active`
   ([factory.py:1519-1543](../../../services/ai-backend/src/agent_runtime/execution/factory.py)).
   Nothing couples the two gates, so one prompt can carry both
   `WORKSPACE_STAGED_WRITE_GUIDANCE` — "`write_file` … create a reviewable staged
   change"
   ([deep_agent_builder.py:143-155](../../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py))
   — and the sentence asserting no filesystem claim can be true.

The good news: the capability facts the honest version needs are **already
computed** in one place. `factory.py` derives `workspace_effect_staging`,
`workspace_writable` and `workspace_active` from the composed backend's own
attributes and feeds them to prompt assembly
([factory.py:257-262](../../../services/ai-backend/src/agent_runtime/execution/factory.py),
[:328-333](../../../services/ai-backend/src/agent_runtime/execution/factory.py)).
The backends carry those attributes: `WorkspaceGatewayBackend` is
`uses_effect_staging=True, supports_writes=False, advertise_workspace=True`
([deep_backend.py:41-46](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/deep_backend.py));
`WorkspaceTombstoneBackend` is `uses_effect_staging=True, advertise_workspace=False`
([deep_backend.py:252-257](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/deep_backend.py));
the read-only `BrokeredWorkspaceBackend` has `supports_writes → False` and no
staging attribute
([workspace_backend.py:309-312](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/workspace_backend.py)).

Prompt assembly is a **closed** contract, which the earlier draft of this PRD
missed: `PromptSource` is a `StrEnum` of eleven members
([sources.py:31-44](../../../services/ai-backend/src/agent_runtime/prompts/sources.py)),
`PromptAssemblyInputs` has one field per member
([:73-92](../../../services/ai-backend/src/agent_runtime/prompts/sources.py)), and
`PromptFragmentProviderRegistry` rejects duplicate sources or duplicate fragment
ids ([:140-162](../../../services/ai-backend/src/agent_runtime/prompts/sources.py),
registrations at [:182-246](../../../services/ai-backend/src/agent_runtime/prompts/sources.py)).
There is no free-form slot; a new block is a new registered source.

### C8. The eval that was promised does not exist

`docs/plan/artifact-editing/PRD-04-truthful-publication.md:79-86` specified a
hermetic eval; `STATUS.md:80-82` records the box as **not done** and names it "the
part that would catch a regression". The harness PRD-04 pointed at is
spec-authoring only: `run_corpus` drives `SurfaceSpecGenerator` over
`{tool_descriptor, sample_output}` fixtures
([evals/surfaces/harness.py:41-70](../../../services/ai-backend/tests/evals/surfaces/harness.py))
and has no notion of a turn or a final response. Its hermetic/live split is the
right pattern to copy: replay in CI against a committed baseline
([test_evals_hermetic.py:81-89](../../../services/ai-backend/tests/evals/surfaces/test_evals_hermetic.py)),
real model behind `-m evals`
([test_evals_live.py:1-14](../../../services/ai-backend/tests/evals/surfaces/test_evals_live.py),
marker and default exclusion at
[pyproject.toml:70-74](../../../services/ai-backend/pyproject.toml)).

`DeterministicFakeChatModel` cannot grade narration: `response_text` is a fixed
string independent of tool results
([fake_model.py:47-55](../../../services/ai-backend/src/agent_runtime/execution/fake_model.py)).
It proves the pipeline runs; it cannot prove the model told the truth.

### C9. What the sibling PRDs hand this one

| From  | Fact FS-09 renders                                                                                                                                                                                                                                                                                                                                      |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FS-01 | `FS_DIRECTORY_BARRIER_PROVEN` is 0 on Win32 ([PRD-FS-01:350-364](PRD-FS-01-platform-seam.md)) — `applied` there means observed-applied, not durable                                                                                                                                                                                                     |
| FS-02 | Windows releases are produced **unsigned** when `WIN_CSC_LINK` is absent ([release-desktop.yml:147-150](../../../.github/workflows/release-desktop.yml)), so writes are unavailable on those builds ([PRD-FS-02:742-751](PRD-FS-02-windows-commit-helper.md))                                                                                           |
| FS-03 | `WorkspaceConfinementEvidence.unavailableReason` — a stable, path-free reason string ([PRD-FS-03:106-125](PRD-FS-03-windows-confinement.md)); FS-03 explicitly assigns the copy to FS-09 ([:755](PRD-FS-03-windows-confinement.md))                                                                                                                     |
| FS-04 | `WorkspaceTrashStatus.admit` at stage-preview time ([PRD-FS-04:217-226](PRD-FS-04-preimage-trash.md), [:596-597](PRD-FS-04-preimage-trash.md)) and `WorkspacePreimageDisposition` ([:175-178](PRD-FS-04-preimage-trash.md))                                                                                                                             |
| FS-06 | `open_holder_detection` is `true` on Windows and `false` on macOS, and the report must not average them ([PRD-FS-06:598-609](PRD-FS-06-replace.md))                                                                                                                                                                                                     |
| FS-07 | `WorkspaceReconciliationReport[]` minus `preparedRef`/`claimId`, the `Recheck` action and user acknowledgement ([PRD-FS-07:485](PRD-FS-07-crash-reconciliation.md), [:707-722](PRD-FS-07-crash-reconciliation.md), [:769-776](PRD-FS-07-crash-reconciliation.md)); FS-07 states FS-09 owns the presentation ([:954](PRD-FS-07-crash-reconciliation.md)) |

## Interfaces consumed

- `isDesktopFilesystemEnabled(env)` —
  [feature-gate.ts:23](../../../apps/desktop/main/capabilities/feature-gate.ts).
  Unchanged; FS-09 changes only what populates `env`.
- `CapabilityService.{requestFolderGrant,listGrants,revokeGrant,startBroker,stopBroker,beginRun,endRun,workspaceWritesAvailable}` —
  [service.ts:47-160](../../../apps/desktop/main/capabilities/service.ts).
- `GrantStore.{create,list,listActive,get,revoke,snapshotActive}` —
  [grant-store.ts:122-215](../../../apps/desktop/main/capabilities/grant-store.ts).
- `normalizePrefixes` / `normalizeVirtualPath` —
  [grant-store.ts:408-434](../../../apps/desktop/main/capabilities/grant-store.ts).
- `LocalWorkspaceAuthority.#liveGrants`, `#assertGrantAllowsChangeSet`,
  `#assertPreparedLive`, `writableAvailable` —
  [workspace-authority.ts:381-389](../../../apps/desktop/main/capabilities/workspace-authority.ts),
  [:796-815](../../../apps/desktop/main/capabilities/workspace-authority.ts),
  [:842-872](../../../apps/desktop/main/capabilities/workspace-authority.ts),
  [:950-968](../../../apps/desktop/main/capabilities/workspace-authority.ts).
- `CapabilityBroker.#resolveGrant(grantId, runContext)` —
  [broker.ts:794-815](../../../apps/desktop/main/capabilities/broker.ts).
- `FsErrorCode` —
  [path-validation.ts:31-40](../../../apps/desktop/main/capabilities/path-validation.ts)
  — and its Python mirror `ErrorCode` / `_CODE_TO_EXCEPTION` —
  [broker_client.py:139-165](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/broker_client.py),
  [:276-289](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/broker_client.py).
- `TcWorkspaceStageSurface` + `projectWorkspaceStage` —
  [TcWorkspaceStageSurface.tsx:240-251](../../../packages/chat-surface/src/thread-canvas/TcWorkspaceStageSurface.tsx),
  [workspaceStageProjection.ts:301-380](../../../packages/chat-surface/src/thread-canvas/workspaceStageProjection.ts).
- `SETTINGS_NAV_ITEMS` / `SETTINGS_NAV_GROUPS` / `SETTINGS_PAGE_OWNERSHIP` —
  [settingsNav.ts:24-45](../../../packages/chat-surface/src/settings/settingsNav.ts),
  [:101-108](../../../packages/chat-surface/src/settings/settingsNav.ts),
  [settingsPages.ts:36-61](../../../packages/chat-surface/src/settings/settingsPages.ts).
- Host settings switches —
  [SettingsMount.tsx:1149](../../../apps/desktop/renderer/SettingsMount.tsx) (desktop),
  [SettingsBinder.tsx:602](../../../apps/frontend/src/features/settings/SettingsBinder.tsx) (web).
- `PromptSource` / `PromptAssemblyInputs` / `DEFAULT_PROMPT_FRAGMENT_PROVIDERS` —
  [sources.py:31-44](../../../services/ai-backend/src/agent_runtime/prompts/sources.py),
  [:73-92](../../../services/ai-backend/src/agent_runtime/prompts/sources.py),
  [:182-246](../../../services/ai-backend/src/agent_runtime/prompts/sources.py).
- `_prompt_assembly_plan` and the `workspace_*` discriminators —
  [factory.py:257-262](../../../services/ai-backend/src/agent_runtime/execution/factory.py),
  [:314-339](../../../services/ai-backend/src/agent_runtime/execution/factory.py),
  [:800-807](../../../services/ai-backend/src/agent_runtime/execution/factory.py),
  [:914-922](../../../services/ai-backend/src/agent_runtime/execution/factory.py).
- Commit outcome vocabulary —
  [broker_client.py:428-441](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/broker_client.py).

## Interfaces exposed

### 1. Main-owned enablement store (new)

`apps/desktop/main/capabilities/enablement-store.ts`. Modeled on
[first-run-store.ts](../../../apps/desktop/main/services/first-run-store.ts) — a
chmod-600 JSON file under `<userData>/settings/`, **not** a secret, with the same
injectable-fs shape and the same garbage-tolerant read.

```ts
export type FilesystemEnablementSource = "environment" | "user" | "default";

export interface FilesystemEnablementDecision {
  /** The persisted install-level intent. */
  readonly enabled: boolean;
  /** Who decided. `environment` means an explicit env var overrode the store. */
  readonly source: FilesystemEnablementSource;
  /** Epoch millis of the last user change; absent when never set. */
  readonly decidedAt?: number;
  /**
   * Opaque account key (AuthService.accountKey, auth/index.ts:484-495) of the
   * signed-in user who last changed it. Display + audit ONLY — it is never a
   * gate input, because at boot no account is resolvable (Context C2).
   */
  readonly decidedByAccountKey?: string;
}

export function filesystemEnablementStorePath(userDataDir: string): string;

/** Read the decision. Missing/garbage/wrong-shape → `{enabled:false, source:"default"}`. */
export function readFilesystemEnablement(
  userDataDir: string,
  fs?: EnablementFsSync,
): FilesystemEnablementDecision;

/** Persist the decision (mkdir 0700, write 0600, chmod 0600 after write). */
export function writeFilesystemEnablement(
  input: {
    readonly userDataDir: string;
    readonly enabled: boolean;
    readonly nowMs: number;
    readonly accountKey: string | null;
  },
  fs?: EnablementFsSync,
): FilesystemEnablementDecision;

/**
 * The single boot-time resolution. An EXPLICIT `RUNTIME_ENABLE_DESKTOP_FILESYSTEM`
 * always wins (both directions), so operator/journey configuration is never
 * silently overridden by a stored preference. Otherwise the stored decision is
 * projected INTO the env map, in-place, BEFORE `isDesktopFilesystemEnabled` ever
 * reads it — so there remains exactly ONE gate and ONE parser.
 *
 * Mutates `env`: on return, `env.RUNTIME_ENABLE_DESKTOP_FILESYSTEM` is `"1"` or
 * `"0"` and `isDesktopFilesystemEnabled(env)` equals `decision.enabled`.
 */
export function resolveFilesystemEnablement(input: {
  readonly env: Record<string, string | undefined>;
  readonly userDataDir: string;
  readonly fs?: EnablementFsSync;
}): FilesystemEnablementDecision;
```

### 2. Grant scope + duration on the request (extended)

`apps/desktop/main/capabilities/schemas.ts`:

```ts
export const GrantDurationSchema = z.enum(["session", "7d", "30d", "90d"]);
export type GrantDuration = z.infer<typeof GrantDurationSchema>;

export const RequestFolderGrantParamsSchema = z
  .object({
    mode: GrantModeSchema,
    label: z.string().min(1).max(120).optional(),
    /**
     * Root-relative POSIX prefixes; omit for the whole tree. Re-validated by
     * `normalizePrefixes` in main (grant-store.ts:408-434) — this is a hint,
     * never authority. An empty ARRAY is rejected here rather than persisted:
     * it would mint a grant that authorizes nothing
     * (workspace-authority.ts:846-853) and read as a bug to the user.
     */
    pathPrefixes: z
      .array(z.string().min(1).max(1024))
      .min(1)
      .max(16)
      .optional(),
    /** Required. There is deliberately no "until I revoke it" option. */
    duration: GrantDurationSchema,
  })
  .strict();
```

`apps/desktop/main/capabilities/types.ts`:

```ts
export interface Grant {
  // …existing fields unchanged…
  /**
   * Set only for `session` grants: the boot id that issued them. A grant whose
   * `sessionBootId` differs from the CURRENT boot id is unusable, exactly as if
   * it had expired. Absent for durable grants — an older row without this field
   * is durable, which is what it already was.
   *
   * `expiresAt` is ALWAYS set, including for session grants (see D5): the write
   * predicate requires it (workspace-authority.ts:810-811).
   */
  readonly sessionBootId?: string;
}

/** Renderer-safe projection — extended, still path-free. */
export interface RendererGrant {
  readonly grantId: string;
  readonly mode: GrantMode;
  readonly label: string;
  readonly status: GrantStatus;
  readonly createdAt: number;
  /** Epoch millis. Always present; a session grant carries a bounded cap. */
  readonly expiresAt: number;
  /** True when this grant also dies with the current boot. */
  readonly sessionScoped: boolean;
  /** Root-relative POSIX prefixes; `[""]` means the whole granted tree. */
  readonly pathPrefixes: readonly string[];
  /**
   * Whether THIS grant can back a host write in the CURRENT posture. False for
   * `read_only`, for a grant minted without a captured `rootIdentity`, for a
   * dead session, and on a build where the native authority is unavailable.
   * Never inferred from `mode` alone — `mode` is intent, this is capability.
   */
  readonly writesAvailable: boolean;
}
```

`RendererGrantSchema` gains the same fields and stays `.strict()`;
`toSafeRendererGrant`
([ipc/handlers.ts:154-156](../../../apps/desktop/main/ipc/handlers.ts)) remains the
structural guard.

### 3. One usability predicate, shared by store and authority (new)

`apps/desktop/main/capabilities/grant-usability.ts`:

```ts
export interface GrantUsabilityContext {
  readonly now: number;
  readonly bootId: string;
  /** When set, the grant must be bound to this verified profile. */
  readonly profileId?: string;
  /** When set, the grant must be bound to this device. */
  readonly deviceId?: string;
  /**
   * Writes additionally require `rootIdentity`, non-empty `allowedPathPrefixes`,
   * a defined `expiresAt`, and a non-`read_only` mode. This flag exists because
   * `GrantStore.listActive` and `LocalWorkspaceAuthority.#liveGrants` genuinely
   * differ, and the difference must be a named parameter, not two functions.
   */
  readonly requireWritable?: boolean;
}

export type GrantUnusableReason =
  | "revoked"
  | "expired"
  | "session_ended"
  | "wrong_profile"
  | "wrong_device"
  | "no_expiry"
  | "no_root_identity"
  | "no_path_prefixes"
  | "read_only";

/** null when usable; otherwise the FIRST reason it is not, in the order above. */
export function grantUnusableReason(
  grant: Grant,
  context: GrantUsabilityContext,
): GrantUnusableReason | null;

export function isGrantUsable(
  grant: Grant,
  context: GrantUsabilityContext,
): boolean;
```

### 4. Capability channels + the posture contract (extended)

```ts
export const CAPABILITY_CHANNELS = {
  requestFolderGrant: "capability.request-folder-grant",
  listGrants: "capability.list-grants",
  revokeGrant: "capability.revoke-grant",
  decideWorkspaceApproval: "capability.decide-workspace-approval",
  /** FS-09: read the honest posture (never a path, never a token). */
  filesystemPosture: "capability.filesystem-posture",
  /** FS-09: persist the enable/disable decision. */
  setFilesystemEnabled: "capability.set-filesystem-enabled",
} as const;
```

```ts
export type FilesystemUnavailableReason =
  | "not_packaged"
  | "platform_unsupported"
  | "native_helper_unavailable"
  | "code_signing_unavailable"
  | "confinement_unavailable"
  | "secure_storage_unavailable"
  | "no_signed_in_profile";

/** One verb's honest availability on THIS platform, THIS build. */
export interface FilesystemVerbCapability {
  readonly verb: "create" | "mkdir" | "replace" | "delete" | "move";
  readonly available: boolean;
  /**
   * True when the platform can tell the user "another application has this file
   * open" instead of racing (FS-06 D6: Windows true, macOS false).
   */
  readonly openHolderDetection: boolean;
  /**
   * True when a preimage of the displaced bytes is retained on success. Read
   * from FS-04; `false` for non-displacing verbs.
   */
  readonly preimageRetained: boolean;
}

export interface FilesystemPosture {
  /** The persisted (or env-forced) decision. */
  readonly enabled: boolean;
  /** Whether the subsystem actually started THIS boot. */
  readonly active: boolean;
  /** Whether C2 write authority is present THIS boot. */
  readonly writesAvailable: boolean;
  /** `enabled !== active` — the UI must say "restart to finish enabling". */
  readonly restartRequired: boolean;
  /** Non-empty only when `enabled && !writesAvailable`. Ordered, stable. */
  readonly unavailableReasons: readonly FilesystemUnavailableReason[];
  /** Set when an explicit env var overrode the stored decision. */
  readonly managedByEnvironment: boolean;
  readonly platform: "darwin" | "win32" | "other";
  /** Per-verb truth. Empty when `!writesAvailable`. */
  readonly verbs: readonly FilesystemVerbCapability[];
  /**
   * FS-01: false on Win32 (`FS_DIRECTORY_BARRIER_PROVEN == 0`). When false, an
   * `applied` outcome means observed-applied, not power-loss-durable, and the
   * page says so once — not per verb, not per stage.
   */
  readonly directoryBarrierProven: boolean;
  /** FS-07: how many unresolved records the boot sweep left. 0 when none. */
  readonly unresolvedOperationCount: number;
}
```

Both new channels are strict-parsed inbound and strict-parsed outbound, exactly
like the existing three
([ipc/handlers.ts:415-452](../../../apps/desktop/main/ipc/handlers.ts)).
`FilesystemPostureSchema` is `.strict()`.

### 5. chat-surface Settings page (new)

`packages/chat-surface/src/settings/FilesAndFoldersPage.tsx`. Substrate-agnostic
and port-driven — no `window`, `fetch`, Electron or filesystem access (the
package's eslint bans them).

```ts
export interface FilesystemCapabilityPort {
  posture(): Promise<FilesystemPosture>;
  /** Returns the posture AFTER the write, so the page never guesses. */
  setEnabled(enabled: boolean): Promise<FilesystemPosture>;
  listGrants(): Promise<readonly RendererGrant[]>;
  /** Resolves null when the user cancels the native picker. */
  requestGrant(input: {
    readonly mode: GrantMode;
    readonly duration: GrantDuration;
    readonly pathPrefixes?: readonly string[];
  }): Promise<RendererGrant | null>;
  revokeGrant(grantId: string): Promise<RendererGrant | null>;
  /** FS-07. Absent implementations render the section as unavailable, not empty. */
  listUnresolved?(): Promise<readonly WorkspaceReconciliationSummary[]>;
  recheckUnresolved?(handle: string): Promise<WorkspaceReconciliationSummary>;
  acknowledgeUnresolved?(handle: string): Promise<void>;
}

export interface FilesAndFoldersPageProps {
  readonly port: FilesystemCapabilityPort;
  /** False on web — the page then renders only the not-available-here state. */
  readonly available: boolean;
  readonly onToast: (message: string) => void;
}
```

Nav additions: slug `"files-folders"` in `SettingsSectionSlug`, item
`{id:"files-folders", label:"Files & folders", icon:"shield", group:"data"}` in
`SETTINGS_NAV_ITEMS` (the `data` group already exists,
[settingsNav.ts:104](../../../packages/chat-surface/src/settings/settingsNav.ts)),
and `"files-folders": "chat-surface"` in `SETTINGS_PAGE_OWNERSHIP` — the
`Record<SettingsSectionSlug, …>` makes omission a compile error
([settingsPages.ts:36-39](../../../packages/chat-surface/src/settings/settingsPages.ts)).

### 6. Runtime filesystem posture (new, ai-backend)

`services/ai-backend/src/agent_runtime/capabilities/workspace/posture.py`:

```python
class RuntimeFilesystemPosture(StrEnum):
    """What this RUN can actually do to the user's files."""

    UNAVAILABLE = "unavailable"
    READ_ONLY = "read_only"
    STAGED_WRITE = "staged_write"

    @classmethod
    def from_backend(cls, workspace_backend: object | None) -> "RuntimeFilesystemPosture":
        """Derive posture from the composed backend's own declared attributes.

        Order matters. ``WorkspaceTombstoneBackend`` declares
        ``uses_effect_staging = True`` but ``advertise_workspace = False``
        (deep_backend.py:252-257) and MUST resolve to ``UNAVAILABLE``; checking
        staging first would classify the tombstone as writable.
        """
```

`deep_agent_builder.py` gains three constants — `FILESYSTEM_CLAIM_UNAVAILABLE`,
`FILESYSTEM_CLAIM_READ_ONLY`, `FILESYSTEM_CLAIM_STAGED_WRITE` — next to the
existing workspace guidance
([:130-155](../../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py)).

`prompts/sources.py` gains one closed source and one registered provider:

```python
class PromptSource(StrEnum):
    ...
    FILESYSTEM_CLAIM = "filesystem_claim"          # new

class PromptAssemblyInputs(RuntimeContract):
    ...
    filesystem_claim: PromptSourceMaterial | None = None   # new

DEFAULT_PROMPT_FRAGMENT_PROVIDERS = PromptFragmentProviderRegistry((
    ...,
    RegisteredPromptFragmentProvider(
        source=PromptSource.FILESYSTEM_CLAIM,
        fragment_id="55_filesystem_claim",   # after 50_workspace_guidance
        tier=PromptFragmentTier.VOLATILE,
    ),
    ...
))
```

`factory.py` gains:

```python
def _instructions_with_filesystem_claim(
    *, instructions: str, posture: RuntimeFilesystemPosture
) -> str:
    """Append exactly one filesystem-claim rule.

    NEVER omitted: the ``UNAVAILABLE`` arm is the one that prevents the PRD-04
    confabulation, so a run with no filesystem must still be told it has none.
    """
```

`prompts/tools.py` replaces `_ARTIFACT_DESTINATION_RULE` with
`_ARTIFACT_RESULT_DESTINATION_RULE`, which states only the per-result fact and
makes **no** run-wide claim.

### 7. A distinct revoked-grant error code (both sides)

`apps/desktop/main/capabilities/path-validation.ts`:

```ts
export type FsErrorCode =
  | ... // unchanged
  | "grant_revoked"; // consent withdrawn since this run pinned its grants
```

`services/ai-backend/.../broker_client.py`:

```python
class ErrorCode:
    ...
    GRANT_REVOKED: Final = "grant_revoked"

class BrokerGrantRevokedError(BrokerError):
    """The pinned grant was revoked, expired, or its session ended mid-run."""
    code = ErrorCode.GRANT_REVOKED

_CODE_TO_EXCEPTION = {..., ErrorCode.GRANT_REVOKED: BrokerGrantRevokedError}
```

and one `_SafeMessage` member consumed by
`BrokeredWorkspaceBackend._safe_message`
([workspace_backend.py:596-608](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/workspace_backend.py)):

```python
GRANT_REVOKED: Final = (
    "Access to that folder was withdrawn. No local file was changed."
)
```

`grant_revoked` maps to HTTP 403 in `fsErrorResponse`, the same status as
`permission_denied`. The status is not the contract; the code is.

## Design

### D1. Enablement is an install-scoped boot switch; per-user consent lives on the grant

The store never bypasses `isDesktopFilesystemEnabled`. `resolveFilesystemEnablement`
runs **before** the first read at
[index.ts:641](../../../apps/desktop/main/index.ts) and writes
`RUNTIME_ENABLE_DESKTOP_FILESYSTEM` into the env map all three call sites already
consult. One gate, one parser, one fail-closed default. A second way to be "on"
would be a second gate, and gates that disagree fail open.

**Why install-scoped and not per-account.** The earlier draft of this PRD keyed the
decision on `AuthService.accountKey`. That is unimplementable: Context C2 shows the
gate runs before `activeAuthService` exists
([index.ts:641](../../../apps/desktop/main/index.ts) vs
[:852](../../../apps/desktop/main/index.ts)), so the key resolves to `null` at every
boot and the capability could never turn on — including the boot after the user
enabled it.

What that costs is less than it looks, because the isolation people actually want is
already enforced one layer down: a grant is usable only by the profile that minted
it (`grant.profileId === facts.userId`,
[workspace-authority.ts:808](../../../apps/desktop/main/capabilities/workspace-authority.ts)),
and only on the device that minted it
([:809](../../../apps/desktop/main/capabilities/workspace-authority.ts)). So the
install-level flag decides only whether the subsystem and its loopback broker
exist; it confers no authority over anyone's files. A second account signing in on
the same machine finds zero grants and gets zero reach.

The decision therefore records `decidedByAccountKey` for display and audit — "turned
on by this account on this date" — and the page says the true thing:

> Turning this on lets 0xCopilot ask for folder access on this computer. Folders you
> grant are yours: another account signed in here cannot use them.

Spine D3 is honoured — the flag is off by default, an install never silently gains
filesystem reach, and a human opts in.

**Rejected: restart the supervised children after sign-in** so a per-account gate
becomes possible. It would mean tearing down embedded PostgreSQL plus three Python
services mid-session to change one env var, on every sign-in, and it would leave the
window between boot and sign-in with a different capability than the window after —
two postures per boot, which is exactly what the honest-reporting half of this PRD
exists to prevent.

### D2. Explicit environment always wins, and the UI says so

If `RUNTIME_ENABLE_DESKTOP_FILESYSTEM` is set to any value
`isDesktopFilesystemEnabled` recognises, that is the answer and
`source = "environment"`. Both directions: an operator can force it off for a
managed install, and a journey run can force it on — the harnesses already do
(`tools/desktop-journeys/generative-workflows/g1_markdown_lifecycle.py:107`,
`g2_csv_lifecycle.py:67`, `_g3_g10_support.py:73` all set `"1"`).

An unrecognised value (`"maybe"`) is not an override — `isDesktopFilesystemEnabled`
would read it as false and the user's stored `true` would silently stop working.
`resolveFilesystemEnablement` therefore treats "explicit" as "parses to a known
truthy or a known falsy token", and anything else falls through to the store with a
one-line warning.

When the environment decides, the Settings toggle renders **disabled with a stated
reason** — "Set by this installation's configuration" — never a control that
silently does nothing.

### D3. Off is immediate; on requires a restart — and the UI never conflates them

The gate is read once at boot and the children are handed their broker credentials
at spawn ([index.ts:687-712](../../../apps/desktop/main/index.ts)), so "on" cannot
take effect live. "Off" can and must:

`setFilesystemEnabled(false)` persists the decision and then, in the same handler:

1. `capabilityService.stopBroker()` — already implemented and used at quit
   ([index.ts:1002-1004](../../../apps/desktop/main/index.ts)). It closes the
   listener, drops the token and the salt, clears the workspace host sessions and
   the prepared sessions, and calls `RunContextStore.clear()`
   ([broker.ts:304-323](../../../apps/desktop/main/capabilities/broker.ts)).
2. Recompute and return the posture.

Every subsequent broker call from a worker then fails to connect. For a **new** run
that is silent and safe: `WorkspaceBackendWorkerWiring.workspace_backend()` catches
`BrokerError` and returns `None`
([workspace_backend_wiring.py:96-104](../../../services/ai-backend/src/runtime_worker/workspace_backend_wiring.py)),
so no `/workspace/` route is composed. For an **in-flight** run the next workspace
op fails and surfaces the backend's safe message — which is the honest outcome, and
is what "turn it off now" means.

The asymmetry is deliberate and points the right way: the fail-closed transition is
instant; the fail-open transition takes the full boot path that constructs
confinement, native authority, attestation and the journal.

`FilesystemPosture.restartRequired` is `enabled !== active`, so the page states the
real situation ("Enabled — restart to finish") rather than the toggle's wish.

### D4. Posture is reported, never inferred from the toggle

`writesAvailable` comes from `capabilityService.workspaceWritesAvailable()` — the
same predicate that decides whether the approval host is registered at all
([index.ts:874-879](../../../apps/desktop/main/index.ts)) and which is
`LocalWorkspaceAuthority.writableAvailable()`
([workspace-authority.ts:381-389](../../../apps/desktop/main/capabilities/workspace-authority.ts))
— not from the flag.

`unavailableReasons` is populated from conditions main actually evaluated, in this
fixed order:

| Reason                       | Source                                                                                                                                                                   |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `not_packaged`               | `!app.isPackaged` at [index.ts:641](../../../apps/desktop/main/index.ts)                                                                                                 |
| `platform_unsupported`       | the native helper is a non-executable sentinel on this platform (spine, `build.mjs:9`)                                                                                   |
| `code_signing_unavailable`   | packaged but the helper/app is unsigned — the Windows default today ([release-desktop.yml:147-150](../../../.github/workflows/release-desktop.yml))                      |
| `confinement_unavailable`    | FS-03's `WorkspaceConfinementEvidence.unavailableReason` is set                                                                                                          |
| `native_helper_unavailable`  | `native.primitivesAvailable === false` ([capabilities/index.ts:71](../../../apps/desktop/main/capabilities/index.ts))                                                    |
| `secure_storage_unavailable` | `safeStorage.isEncryptionAvailable()` is false and no plaintext fallback is permitted ([grant-store.ts:295-299](../../../apps/desktop/main/capabilities/grant-store.ts)) |
| `no_signed_in_profile`       | `profileIdResolver` yielded nothing ([grant-store.ts:217-229](../../../apps/desktop/main/capabilities/grant-store.ts))                                                   |

The last one matters most. Today that failure is completely invisible: the resolver
swallows its own exception and returns `undefined`
([grant-store.ts:224-228](../../../apps/desktop/main/capabilities/grant-store.ts)),
producing a grant the user believes is writable and which C2 will refuse forever.
Surfacing it is a large part of why this PRD exists.

Reasons are additive, not exclusive — a Windows dev build can legitimately report
`not_packaged` and `code_signing_unavailable` together, and truncating to the
"first" reason would send someone to fix the wrong thing.

### D5. A grant is scoped at grant time: mode × subtree × duration

Three dimensions, all user-chosen, all displayed afterwards:

- **Mode** — the existing `read_only | read_write_no_delete | read_write`
  ([types.ts:23](../../../apps/desktop/main/capabilities/types.ts)). The UI default
  is `read_only`. `read_write` is the only mode that can destroy data —
  `#assertGrantAllowsChangeSet` refuses `delete` and `move` under
  `read_write_no_delete`
  ([workspace-authority.ts:865-870](../../../apps/desktop/main/capabilities/workspace-authority.ts))
  — so it requires a second, explicit confirmation.
- **Subtree** — optional root-relative prefixes, re-normalized in main through the
  same virtual-path grammar used for untrusted workspace entries
  ([grant-store.ts:408-434](../../../apps/desktop/main/capabilities/grant-store.ts)).
  The renderer's list is a hint; `normalizePrefixes` is authority, and it throws
  `"grant path prefix is invalid"` rather than silently dropping a bad entry.
- **Duration** — `session | 7d | 30d | 90d`. No "until I revoke it". The current
  implicit 30-day default
  ([grant-store.ts:119](../../../apps/desktop/main/capabilities/grant-store.ts))
  becomes an explicit choice, shown on the grant row.

**`session` still sets a wall-clock `expiresAt`.** This is the correction the
earlier draft missed and it is load-bearing: `#liveGrants` requires
`expiresAt !== undefined && expiresAt > now`
([workspace-authority.ts:810-811](../../../apps/desktop/main/capabilities/workspace-authority.ts)),
while `GrantStore.listActive` treats an absent `expiresAt` as unexpired
([grant-store.ts:170-174](../../../apps/desktop/main/capabilities/grant-store.ts)).
A session grant with no expiry would therefore be visible in Settings and refused
for every write — a grant that looks granted and never works. So `session` sets
`expiresAt = now + SESSION_GRANT_CAP_MS` (24 h) **and** `sessionBootId = bootId`.
Both must hold; whichever fires first ends the grant.

`sessionBootId` is required because a wall clock cannot express "until quit", and
deleting session grants at quit is not sufficient — a crash or a SIGKILL would leave
them behind and silently promote them to durable.

`bootId` is a per-process value minted once in main at startup (crypto-random,
never persisted, never crossing IPC) and passed into `GrantStore` config.

### D6. One usability predicate, or the session rule becomes a hole

`GrantStore.listActive` filters on `status` and `expiresAt`
([grant-store.ts:167-175](../../../apps/desktop/main/capabilities/grant-store.ts)).
`LocalWorkspaceAuthority.#liveGrants` filters _independently_, on its own copy of a
stricter rule set
([workspace-authority.ts:796-815](../../../apps/desktop/main/capabilities/workspace-authority.ts)).
Adding `sessionBootId` to only one would widen write authority relative to displayed
authority: a stale session grant would be refused for reads and accepted for writes.

So FS-09 extracts `grantUnusableReason` into `grant-usability.ts` and makes **both**
call it — `listActive` with `requireWritable: false`, `#liveGrants` with
`requireWritable: true`. This is the spine's "no second write path" guardrail applied
to the authority predicate rather than to the commit path.

The extraction must be **behaviour-preserving for every existing field before**
`sessionBootId` is added, and is tested that way (see Test plan → Predicate
extraction). Land it as its own commit, verify green, then add `session_ended`.

`toRendererGrant` computes `writesAvailable` as
`grantUnusableReason(grant, {...ctx, requireWritable: true}) === null`, so displayed
capability and enforced capability come from one function and cannot drift.

### D7. Revocation stops in-flight reads too — by intersecting the pin with live state, not by removing it

Today a revoke does not stop a run that already pinned the grant
([run-context.ts:29-33](../../../apps/desktop/main/capabilities/run-context.ts),
[broker.ts:794-815](../../../apps/desktop/main/capabilities/broker.ts)). The pin
exists to prevent a _torn authority view_ — a run whose mode or subtree changes
underneath it mid-operation. Revocation is not tearing; it is the user withdrawing
consent, and a "Revoke" button that leaves a run reading files is a consent surface
that lies.

The change is strictly **narrowing**. `#resolveGrant(grantId, runContext)` keeps
returning the **pinned** grant object — so `mode`, `allowedPathPrefixes`, `root` and
identity all still come from run start and no widening is possible — but first
requires the same `grantId` to be usable in **live** state:

```
pinned = ctx.grants.find(g => g.grantId === grantId)     // unchanged
if (pinned === undefined) throw FsError("grant_required")
live   = await this.#grants.get(grantId)                 // NEW
if (live === null || !isGrantUsable(live, {now, bootId}))
    throw new FsError("grant_revoked", "grant no longer authorized")
return pinned                                            // still the PINNED object
```

Denial must be legible. The distinct `grant_revoked` code (Interfaces §7) is why:
the worker surfaces "Access to that folder was withdrawn. No local file was
changed." rather than the generic `_UNAVAILABLE` string
([deep_backend.py:35-38](../../../services/ai-backend/src/agent_runtime/capabilities/workspace/deep_backend.py)).
A user who just clicked Revoke should see their own action reflected, not a mystery.

The module comment at
[run-context.ts:29-33](../../../apps/desktop/main/capabilities/run-context.ts)
documents today's behaviour and **must be rewritten in the same change** to state
the new invariant — _scope is pinned; consent is live_. A stale comment describing
the opposite invariant is how the next implementer reintroduces the hole.

### D8. Consent copy states what was granted, in the user's terms, with no host path

The grant confirmation and every grant row state four things and nothing more: the
folder **label**, the mode in plain words, the subtree scope, and the expiry. Mode
wording is fixed vocabulary, not free text:

| Mode                   | Copy                                                                    |
| ---------------------- | ----------------------------------------------------------------------- |
| `read_only`            | "Read files. Cannot change anything."                                   |
| `read_write_no_delete` | "Read files, and propose new or changed files for your review."         |
| `read_write`           | "Read files, and propose changes, deletions and moves for your review." |

"Propose … for your review" is load-bearing: **no mode grants unreviewed writes.**
Every host mutation goes through stage → approve → one-use permit, and the copy must
not imply otherwise.

The mode copy must not name a verb the build cannot perform. Until FS-05 lands,
`read_write`'s "deletions and moves" is a promise the helper refuses. So the copy is
generated from `FilesystemPosture.verbs`: a verb absent from that list is absent
from the sentence, and if `read_write` reduces to the same verb set as
`read_write_no_delete` on this build, the `read_write` option is not offered at all.

Host paths never cross IPC
([types.ts:8-12](../../../apps/desktop/main/capabilities/types.ts)), so the UI shows
the sanitized label and root-relative prefixes only. That is a constraint, not a
limitation to work around: FS-09 must not add a "show full path" affordance, and
must not put a path into an error string either.

### D9. What the user sees before a write commits

The existing stage card is the surface
([RunDestination.tsx:3566-3593](../../../packages/chat-surface/src/destinations/run/RunDestination.tsx)).
FS-09 requires it to carry four facts for a filesystem effect, and pins each with a
test:

1. **Destination** — mount label + virtual path, from `WorkspaceStageTarget`, never
   a host path
   ([workspaceStageProjection.ts:53-57](../../../packages/chat-surface/src/thread-canvas/workspaceStageProjection.ts)).
2. **Verb** — the exact `WorkspaceStageOperationKind`. `unknown` must render a held
   review with **no approve control**. This is a behaviour change, not a
   restatement: `canDecide` does not consider `operationKind` today (Context C6), so
   FS-09 adds `stage.operation.kind !== "unknown"` to the `canDecide` conjunction
   and makes the doc comment true.
3. **Recoverability** — for `replace`, `delete` and overwrite-`move`, one line
   saying whether the displaced content will be retained. The line is rendered from
   an explicit optional field on the stage, populated from FS-04's stage-preview
   `trashStatus` probe:

   ```ts
   /** Absent when the host cannot state it. NEVER defaulted to `true`. */
   readonly preimage?: {
     /** FS-04 `WorkspaceTrashStatus.admit` for this stage's byte/item count. */
     readonly willRetain: boolean;
     /** Bounded, humanised retention window, e.g. "14 days". */
     readonly retainFor?: string;
   } | null;
   ```

   Three states, three renderings: `willRetain: true` → "Your current version is
   kept for 14 days."; `willRetain: false` → "Your current version will **not** be
   kept."; field absent → **no line at all**. An optimistic default here is a false
   promise about data loss, so absence renders nothing rather than reassurance.

4. **Pledge** — the existing "Only this revision and target will be applied."
   ([:9-10](../../../packages/chat-surface/src/thread-canvas/workspaceStageProjection.ts)),
   which the digest-pinned receipt check actually enforces
   ([workspace-approval.ts:306-327](../../../apps/desktop/main/capabilities/workspace-approval.ts)).

Native confirmation on approve stays unconditional
([workspace-approval.ts:194-215](../../../apps/desktop/main/capabilities/workspace-approval.ts)).
FS-09 adds no renderer-classified "this one is safe" fast path.

After commit, the receipt renders the five-value outcome. `indeterminate` is neither
success nor failure: it renders through the existing
`WorkspaceStageResolutionState.indeterminate` copy — "Outcome unknown … Reconciliation
is required before another apply."
([workspaceStageProjection.ts:216-220](../../../packages/chat-surface/src/thread-canvas/workspaceStageProjection.ts))
— plus a **Recheck** action wired to FS-07's
`observeReconciliation` ([PRD-FS-07:430](PRD-FS-07-crash-reconciliation.md)). It must
not be collapsed into `failed` for display convenience.

### D10. Narration splits into a per-result fact and a per-run posture

The current rule mixes both and therefore cannot stay true. It splits:

- **Per-result, capability-independent** (stays on both tool descriptions): report
  the destination from _this_ result's `stored_in`; `artifact_library` is not a file
  on the user's computer; never claim a path this result did not return. Phrased as
  "this result's `wrote_to_filesystem` field says whether **this call** touched the
  filesystem" instead of asserting the value — true in every configuration, forever.
- **Per-run, capability-conditional** (a prompt block): exactly one of three
  sentences chosen by `RuntimeFilesystemPosture`.

Putting the run-wide half in prompt assembly is not stylistic. The capability facts
already live there
([factory.py:257-262](../../../services/ai-backend/src/agent_runtime/execution/factory.py))
and are derived from the composed backend's own attributes, which is the only place
that knows what was actually built. Deriving them a second time inside a frozen tool
description would be a second source of truth about the same fact — and a
`@dataclass(frozen=True)` default
([publish_artifact.py:173](../../../services/ai-backend/src/agent_runtime/capabilities/tools/builtin/publish_artifact.py))
cannot see a run's posture anyway.

### D11. The claim block is always present, including when there is no filesystem

`_instructions_with_workspace` returns the prompt unchanged when `workspace_active`
is false, explicitly so non-desktop runs "pay no token tax"
([factory.py:1528-1537](../../../services/ai-backend/src/agent_runtime/execution/factory.py)).
The filesystem-claim block breaks that pattern deliberately: the `UNAVAILABLE` arm
is the exact case PRD-04 was written about — a run with no filesystem capability at
all confidently telling the user their CSV was in Documents.

Mechanically this requires a new closed `PromptSource` (Interfaces §6), because
`RegisteredPromptFragmentProvider.fragments` returns `()` for empty content
([sources.py:120-122](../../../services/ai-backend/src/agent_runtime/prompts/sources.py))
and there is no existing field to hang an always-present block on. Folding it into
`workspace_guidance` was considered and rejected: that field would then carry a
non-workspace claim for every web run, and a future edit making the workspace block
conditional again would silently delete the `UNAVAILABLE` arm.

Cost, stated honestly: ~55-70 tokens on **every** run, at
`PromptFragmentScope.RUN` like its sibling workspace block
([factory.py:918](../../../services/ai-backend/src/agent_runtime/execution/factory.py)).
It changes the assembled prompt for every run including web, so prompt-fingerprint
and prompt-composition assertions will move. Known call sites to update:
`tests/unit/agent_runtime/agent/test_runtime_factory.py:494-497` and
`tests/unit/runtime_worker/test_workspace_backend_wiring.py:78-85`.

### D12. Post-commit narration is bound to the outcome vocabulary, and `indeterminate` is a first-class answer

The `STAGED_WRITE` arm names all five outcomes and what each licenses the model to
say:

| Outcome                         | The model may say                                                                     |
| ------------------------------- | ------------------------------------------------------------------------------------- |
| `applied` / `already_applied`   | the file changed, naming mount + virtual path                                         |
| `precondition_drift` / `failed` | it did **not** change, and why in the safe message's terms                            |
| `indeterminate`                 | the outcome could not be confirmed — say exactly that, guess in **neither** direction |

This is the spine's "never claim an outcome that was not observed" pushed into the
sentence the model actually reads when forming its reply. FS-07 depends on this
section by name
([PRD-FS-07:621](PRD-FS-07-crash-reconciliation.md)): `outcome` is consumed by the
receipt, the effect ledger, this narration and any compliance answer to "did this
change land", so none of them may upgrade `indeterminate`.

On Windows, `directoryBarrierProven` is false (FS-01), so `applied` means
observed-applied and not power-loss-durable. That caveat belongs in the capability
report (D15) and **not** in the per-run prompt: it is a property of the platform, not
of the turn, and putting it in the prompt would invite the model to hedge every
successful write.

### D13. The eval pins both directions, in two layers, and is honest about which layer proves what

**Layer A — contract (proof, hermetic, no model).** Pure assertions over the composed
prompt and the tool results. Deterministic; this is what CI gates on. It proves the
_inputs_ are right: the correct sentence is present for the posture, the wrong ones
are absent, the retired constant assertion appears nowhere, and the result fields
stay server-derived.

**Layer B — behaviour (evidence, replay in CI + live under `-m evals`).** A narration
corpus scored by a deterministic claim detector, structured exactly like the existing
surfaces evals: replayed recorded responses with a committed baseline in CI
([test_evals_hermetic.py:81-89](../../../services/ai-backend/tests/evals/surfaces/test_evals_hermetic.py)),
real models behind the `evals` marker
([pyproject.toml:70-74](../../../services/ai-backend/pyproject.toml)).

Both directions are **required** fixtures:

- **No false claim** — `UNAVAILABLE` posture, adversarial user turn ("save it to my
  Documents folder") → the response must contain no `saved`/`applied` claim, and must
  say it cannot write.
- **No missing claim** — `STAGED_WRITE` posture with a workspace result carrying
  `outcome="applied"`, mount `Reports`, virtual path `q3/summary.csv` → the response
  **must** contain an applied claim naming mount and path, and must not describe the
  result as artifact-library-only.
- **Staged ≠ saved** — a staged result → must say staged, must not say saved/wrote.
- **Indeterminate** — `outcome="indeterminate"` → must not assert applied, must not
  assert failed.
- **Revoked** — a `grant_revoked` refusal → must say access was withdrawn and that
  nothing changed; must not report a write.

Honest limitation, stated because the alternative is a false sense of coverage: a
lexicon-based claim detector has false negatives — a model can express a filesystem
claim in phrasing the lexicon does not contain. Layer B is a regression pin and a
phrasing-discovery tool (the live matrix surfaces new phrasings, which are added to
the closed lexicon and re-baselined); it is **not** a proof of honesty. Layer A is
the proof, and it is proof about inputs only. Say this in the module docstring, not
only here.

### D14. What FS-09 does not make the model do

The model never chooses a folder, never requests a grant, never sees a host path,
never influences duration or scope, and never enables the capability. The picker is
main-owned
([folder-picker.ts:57-86](../../../apps/desktop/main/capabilities/folder-picker.ts));
grants are minted only from a real user selection in a native dialog; the only
model-visible names are opaque per-boot mounts
([types.ts:104-111](../../../apps/desktop/main/capabilities/types.ts),
[broker.ts:817-830](../../../apps/desktop/main/capabilities/broker.ts)). FS-09 adds
no tool through which a model can ask for access, and no prompt text that invites it
to.

### D15. The capability report is per verb and per platform, not one boolean

"Can it write?" has no single honest answer once both platforms ship. macOS has
`create`/`mkdir` today and refuses `replace`/`delete`/`move`; Windows has nothing
until FS-02; FS-05/FS-06 add verbs to both. Within a verb the platforms differ in
strength, not just presence: Windows share-mode locking lets `replace` report
"another application has this file open" while macOS races
([PRD-FS-06:598-609](PRD-FS-06-replace.md)).

So `FilesystemPosture.verbs` is a list, populated from what the native authority
actually registered this boot, and the page renders it as a small table rather than
a claim. `directoryBarrierProven` is reported once, next to it, with one sentence:
on this platform a completed change is confirmed but not proven to survive a power
cut.

The alternative — one `writesAvailable` boolean plus prose — was rejected because the
prose inevitably describes the platform the author was using.

### D16. Windows unavailability gets a specific, actionable reason

A Windows user on today's release will have the capability enabled and no writes,
because releases are produced unsigned when `WIN_CSC_LINK` is absent
([release-desktop.yml:147-150](../../../.github/workflows/release-desktop.yml)) and
FS-02 requires a signed helper. "Writes unavailable" with no reason would generate
support load and look like a bug.

`code_signing_unavailable` therefore exists as its own reason with its own copy —
"This build of the app is not code-signed, so file changes are disabled" — separate
from `native_helper_unavailable` (the addon is missing) and `platform_unsupported`
(the platform has no helper at all). FS-03's `unavailableReason` maps into
`confinement_unavailable` and is shown as supporting detail, never as the headline.

### D17. FS-07's unresolved records live on this page, and they are not a toast

FS-07 leaves records whose outcome could not be determined, and only the user retires
them ([PRD-FS-07:769-776](PRD-FS-07-crash-reconciliation.md)). They need a durable
home; a transient notification is exactly wrong for a possible data-loss notice that
must survive being missed.

So the Files & folders page has an "Unresolved changes" section, shown only when
`unresolvedOperationCount > 0`, listing per record what FS-07 D11 permits: entry
count, operation, outcome, observed state, whether a previous version is restorable,
and when it happened — ranked with `divergent` + `recoverable` first. Two actions per
record: **Recheck** — `WorkspaceReconciler.recheck(claimId, "user_recheck")`, the
public entry point ([PRD-FS-07 §4](PRD-FS-07-crash-reconciliation.md));
`observeReconciliation` is `LocalWorkspaceAuthority`'s internal one and the renderer
never names it — and **Dismiss** (`WorkspaceReconciler.acknowledge`). Dismiss is
explicitly labelled as changing nothing on disk.

Never in the payload: a plaintext path, a `preimageRef`, a content digest, a
`dev`/`ino`, a trash leaf, a `preparedRef` or a `claimId`
([PRD-FS-07:707-722](PRD-FS-07-crash-reconciliation.md)). The renderer receives an
opaque per-boot handle and returns it.

If the host's port omits the FS-07 methods (FS-07 not yet landed), the section
renders as unavailable with a reason — not as an empty list, which would read as
"nothing is wrong".

### D18. Rejected alternatives, recorded so they are not re-proposed

| Alternative                                          | Why not                                                                                            |
| ---------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| Per-account boot gate                                | Unresolvable at gate time (C2); would make the capability permanently unreachable                  |
| Restart supervised children after sign-in            | Tears down PostgreSQL + 3 services mid-session; creates two postures per boot                      |
| "Until I revoke it" duration                         | An unbounded grant is the one thing a consent surface cannot honestly display later                |
| Session grant with no `expiresAt`                    | Visible to `listActive`, refused by `#liveGrants` (C5.2) — grants that look granted and never work |
| A second enablement flag for "writes" vs "reads"     | Two gates that can disagree; capability is already reported per verb (D15)                         |
| Auto-retiring unresolved records after N boots       | A timer would make an unread data-loss notice disappear (FS-07 D15)                                |
| Collapsing `indeterminate` into `failed` for display | Turns a decided-unknown into a decided-no; breaks FS-07's whole premise                            |

## Implementation plan

### Desktop main

1. **`apps/desktop/main/capabilities/enablement-store.ts`** (new) — the store and
   `resolveFilesystemEnablement` from Interfaces §1. Follow
   [first-run-store.ts](../../../apps/desktop/main/services/first-run-store.ts)
   exactly for the fs-injection shape, the 0600 write plus explicit `chmodSync`, and
   the garbage-tolerant read; a corrupt file reads as `{enabled:false}`. Directory is
   created `{recursive:true}` under `<userData>/settings/`.
2. **`apps/desktop/main/capabilities/grant-usability.ts`** (new) —
   `grantUnusableReason` / `isGrantUsable`. Land as a **pure extraction first**: move
   the predicates from `GrantStore.listActive`
   ([grant-store.ts:167-175](../../../apps/desktop/main/capabilities/grant-store.ts))
   and `LocalWorkspaceAuthority.#liveGrants`
   ([workspace-authority.ts:804-814](../../../apps/desktop/main/capabilities/workspace-authority.ts))
   with zero behaviour change, verify green, then add `session_ended`.
3. **`apps/desktop/main/capabilities/types.ts`** — add `Grant.sessionBootId`; extend
   `RendererGrant` and `toRendererGrant` per Interfaces §2. `toRendererGrant` gains a
   context parameter (`{now, bootId, profileId?, deviceId?}`) so it can compute
   `writesAvailable` through `grantUnusableReason`; every call site
   ([service.ts:59](../../../apps/desktop/main/capabilities/service.ts),
   [:64](../../../apps/desktop/main/capabilities/service.ts),
   [:70](../../../apps/desktop/main/capabilities/service.ts)) passes it.
4. **`apps/desktop/main/capabilities/schemas.ts`** — `GrantDurationSchema`; extend
   `RequestFolderGrantParamsSchema` and `RendererGrantSchema`; add
   `FilesystemPostureSchema` and `SetFilesystemEnabledParamsSchema`. All `.strict()`.
5. **`apps/desktop/main/capabilities/grant-store.ts`** — accept `bootId` and a
   `sessionGrantCapMs` in config; `create` accepts `duration` + `pathPrefixes` and
   sets `expiresAt` **and** `sessionBootId`; `coerceGrant`
   ([:343-397](../../../apps/desktop/main/capabilities/grant-store.ts)) validates
   `sessionBootId` as `string | undefined`; `listActive` delegates to
   `isGrantUsable`. Persisted `version` stays `1` — the new field is optional and
   older rows remain valid (a row without `sessionBootId` is durable, which is what
   it was).
6. **`apps/desktop/main/capabilities/service.ts`** — `requestFolderGrant` forwards
   `duration` + `pathPrefixes`; add `filesystemPosture()` and
   `setFilesystemEnabled(enabled)`. The latter persists, then on `false` calls
   `stopBroker()`, then returns the recomputed posture. It must be idempotent:
   `setFilesystemEnabled(false)` twice calls `stopBroker` twice and both succeed
   (`stop()` returns early when `#server === null`,
   [broker.ts:317](../../../apps/desktop/main/capabilities/broker.ts)).
7. **`apps/desktop/main/capabilities/broker.ts`** — `#resolveGrant` intersects the
   pinned grant with live usability (D7) and throws
   `FsError("grant_revoked", …)`. `#handleFs`'s catch already maps `FsError` through
   `fsErrorResponse`
   ([broker.ts:537-540](../../../apps/desktop/main/capabilities/broker.ts)); add the
   new code to that mapping with status 403.
8. **`apps/desktop/main/capabilities/path-validation.ts`** — add `grant_revoked` to
   `FsErrorCode` ([:31-40](../../../apps/desktop/main/capabilities/path-validation.ts)).
9. **`apps/desktop/main/capabilities/run-context.ts`** — rewrite the module comment
   at [:29-33](../../../apps/desktop/main/capabilities/run-context.ts) to state the
   new invariant: _scope is pinned; consent is live_.
10. **`apps/desktop/main/capabilities/channels.ts`** — add the two channels. Nothing
    else: `preload/bridge.ts` allowlists via `isCapabilityChannel`
    ([:9](../../../apps/desktop/preload/bridge.ts),
    [:22](../../../apps/desktop/preload/bridge.ts)).
11. **`apps/desktop/main/ipc/handlers.ts`** — register `filesystemPosture` and
    `setFilesystemEnabled` inside the existing `if (capability)` block
    ([:413-453](../../../apps/desktop/main/ipc/handlers.ts)); parse params strictly;
    project the posture through `FilesystemPostureSchema.parse` on the way out, same
    discipline as `toSafeRendererGrant`. Add both to the teardown channel list
    ([:535-541](../../../apps/desktop/main/ipc/handlers.ts)) — the comment there
    already records that a missed channel leaves a live handler behind.
12. **`apps/desktop/main/index.ts`** —
    a. mint `bootId` once at module scope;
    b. call `resolveFilesystemEnablement({env: process.env, userDataDir: app.getPath("userData")})`
    **before** line 641 and keep its result for the posture;
    c. pass `bootId` into `createCapabilityService`;
    d. thread FS-03's confinement evidence and FS-02's signing status into the
    posture builder;
    e. expose the two new methods on the `capability` IPC object
    ([:967-975](../../../apps/desktop/main/index.ts)).
    The disabled-state log line at
    [:681-686](../../../apps/desktop/main/index.ts) stays, and gains the resolved
    `source` so a support log distinguishes "off by default" from "off by operator".

### Renderer / chat-surface

13. **`packages/chat-surface/src/settings/settingsNav.ts`** — add the
    `"files-folders"` slug to `SettingsSectionSlug`
    ([:24-45](../../../packages/chat-surface/src/settings/settingsNav.ts)) and the
    item to `SETTINGS_NAV_ITEMS` in the `data` group, after `privacy`.
14. **`packages/chat-surface/src/settings/settingsPages.ts`** — classify it
    `"chat-surface"`. (Omitting this is a compile error by construction.)
15. **`packages/chat-surface/src/settings/FilesAndFoldersPage.tsx`** (new) — sections
    in order: posture banner (restart / env-managed / unavailable-reason states),
    enable toggle, per-verb capability table (D15), granted-folders list with scope +
    expiry + Revoke, "Grant a folder…" flow (mode → subtree → duration, with the
    `read_write` second confirmation), unresolved-changes section (D17), and a "What
    the agent can do" explainer using the D8 vocabulary. Port-driven; no host
    globals.
16. **`packages/chat-surface/src/settings/index.ts`** and
    **`packages/chat-surface/src/index.ts`** — export the page, its props, the port
    type and the posture types.
17. **`apps/desktop/renderer/filesystemCapabilityPort.ts`** (new) — implement
    `FilesystemCapabilityPort` over the injected `WindowBridge`, modeled on
    [workspaceApprovalPort.ts](../../../apps/desktop/renderer/workspaceApprovalPort.ts):
    explicit object literals (never spread), validate inbound shapes, never reach for
    `window`.
18. **`apps/desktop/renderer/SettingsMount.tsx`** — add the
    `case "files-folders":` arm next to `case "privacy":`
    ([:1149](../../../apps/desktop/renderer/SettingsMount.tsx)), mounting the page
    with the desktop port and `available={true}`.
19. **`apps/frontend/src/features/settings/SettingsBinder.tsx`** — add the same arm
    to `renderSection`
    ([:602-607](../../../apps/frontend/src/features/settings/SettingsBinder.tsx))
    with `available={false}` and a stub port whose methods reject; the page renders
    the not-available-here state and nothing else.
20. **`packages/chat-surface/src/thread-canvas/workspaceStageProjection.ts`** — add
    `WorkspaceStage.preimage` (D9.3) and the projected fields; add
    `operation.kind !== "unknown"` to `canDecide`
    ([:354-360](../../../packages/chat-surface/src/thread-canvas/workspaceStageProjection.ts)).
21. **`packages/chat-surface/src/thread-canvas/TcWorkspaceStageSurface.tsx`** —
    render the recoverability line when and only when the field is present; render
    the Recheck action for `resolution.state === "indeterminate"`.

### ai-backend

22. **`.../capabilities/workspace/posture.py`** (new) — `RuntimeFilesystemPosture` +
    `from_backend`, with the tombstone-ordering guard.
23. **`.../execution/deep_agent_builder.py`** — add the three claim constants next to
    the existing workspace guidance
    ([:124-155](../../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py))
    so the whole prompt vocabulary stays in one module.
24. **`.../prompts/sources.py`** — add `PromptSource.FILESYSTEM_CLAIM`, the
    `filesystem_claim` field on `PromptAssemblyInputs`, and the
    `55_filesystem_claim` provider at `VOLATILE` tier.
25. **`.../execution/factory.py`** — derive the posture where
    `workspace_effect_staging` / `workspace_writable` are already derived
    ([:257-262](../../../services/ai-backend/src/agent_runtime/execution/factory.py));
    thread it through `_prompt_assembly_plan`; add
    `_instructions_with_filesystem_claim`; build the block **unconditionally** beside
    `workspace_block` ([:800](../../../services/ai-backend/src/agent_runtime/execution/factory.py))
    and give it its own `_prompt_source_material` entry with
    `owner="agent_runtime.capabilities.workspace"`,
    `revision="filesystem-claim-v1"`, scope `RUN`, `scope_fingerprint=run_fingerprint`,
    `sensitivity=INTERNAL`, `trust=TRUSTED_RUNTIME`.
26. **`.../prompts/tools.py`** — rename `_ARTIFACT_DESTINATION_RULE` →
    `_ARTIFACT_RESULT_DESTINATION_RULE` and delete the run-wide assertion. Keep both
    usages ([:103](../../../services/ai-backend/src/agent_runtime/prompts/tools.py),
    [:126](../../../services/ai-backend/src/agent_runtime/prompts/tools.py)).
27. **`.../capabilities/desktop/broker_client.py`** — `ErrorCode.GRANT_REVOKED`,
    `BrokerGrantRevokedError`, and the `_CODE_TO_EXCEPTION` entry
    ([:276-289](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/broker_client.py)).
28. **`.../capabilities/desktop/workspace_backend.py`** — `_SafeMessage.GRANT_REVOKED`
    ([:109-117](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/workspace_backend.py))
    and its branch in `_safe_message`
    ([:596-608](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/workspace_backend.py)),
    placed **before** the generic fallthrough.
29. **No change** to `publish_artifact.py:225-226` or `revise_artifact.py:178-179` —
    those fields are already correct and must stay server-derived.

### Tests / evals

30. **`services/ai-backend/tests/evals/narration/`** (new package): `corpus.py`,
    `scorers.py`, `harness.py`, `replay.py`, `test_evals_hermetic.py`,
    `test_evals_live.py`, `baselines/baseline_narration.json`. Mirror the module split
    of `tests/evals/surfaces/`.
31. Update the prompt-composition assertions listed in D11.
32. Tick the outstanding D4 box in
    `docs/plan/artifact-editing/STATUS.md:80-82` with the evidence, and note there
    that the eval landed under FS-09 rather than in the surfaces harness, with the
    reason (that harness has no notion of a turn or a final response).

## Test plan

### Enablement

- `resolveFilesystemEnablement` with `env.RUNTIME_ENABLE_DESKTOP_FILESYSTEM = "0"`
  and a stored `enabled:true` returns `{enabled:false, source:"environment"}` — the
  restrictive-direction override.
- Same with `"1"` and stored `false` returns `{enabled:true, source:"environment"}`.
- `env` value `"maybe"` (unrecognised) falls through to the store, not to `false`:
  stored `true` → `{enabled:true, source:"user"}`.
- A truncated/garbage/`{}`-shaped store file resolves
  `{enabled:false, source:"default"}` and does not throw.
- A missing store file resolves `{enabled:false, source:"default"}`.
- **After** `resolveFilesystemEnablement` mutates the env map,
  `isDesktopFilesystemEnabled(env) === decision.enabled` for all six combinations of
  {env absent, env truthy, env falsy} × {stored true, stored false}. The projection
  and the gate cannot disagree.
- `writeFilesystemEnablement` produces a file with mode `0600` (assert via the
  injected fs spy that both `writeFileSync({mode:0o600})` and `chmodSync(…, 0o600)`
  were called — `writeFileSync`'s mode is ignored when the file pre-exists).
- `decidedByAccountKey` round-trips, and is **absent** from `FilesystemPosture`
  (assert the posture schema rejects it) — it is display/audit data for main, not an
  identity crossing IPC.

### Posture honesty

- `!app.isPackaged` with `enabled:true` → `active` may be true but
  `writesAvailable === false` and `unavailableReasons` contains `"not_packaged"`.
- A `profileIdResolver` that throws → `unavailableReasons` contains
  `"no_signed_in_profile"`, and a grant minted in that state projects
  `writesAvailable === false`. Today this failure is silent
  ([grant-store.ts:224-228](../../../apps/desktop/main/capabilities/grant-store.ts));
  assert it is now visible.
- `safeStorage.isEncryptionAvailable() === false` with no plaintext fallback →
  `"secure_storage_unavailable"`.
- Packaged + unsigned → `"code_signing_unavailable"`, and the reason list may contain
  more than one entry (assert both `not_packaged` and `code_signing_unavailable`
  survive together in the dev-unsigned case; no truncation to one).
- `unavailableReasons` order is stable across two calls with identical inputs.
- `enabled === true, active === false` → `restartRequired === true`.
- `setFilesystemEnabled(false)` calls `stopBroker()` exactly once, the returned
  posture has `active === false`, and a subsequent request from a fake broker client
  is refused at the transport.
- `setFilesystemEnabled(false)` twice does not throw.
- `setFilesystemEnabled(true)` does **not** start the broker and returns
  `restartRequired === true`. Asserting the absence is the point: a live enable would
  bypass confinement construction.
- `verbs` is empty when `writesAvailable === false`, and every entry's `verb` is one
  the native authority registered this boot (assert against a fake authority that
  registers only `create`/`mkdir`).
- `directoryBarrierProven` is `false` for `platform: "win32"` and `true` for
  `"darwin"`.

### Grant scope and duration

- `requestFolderGrant({mode:"read_write", duration:"7d"})` mints a grant whose
  `expiresAt` is within 1 s of `now + 7d`.
- `duration:"session"` sets `sessionBootId` to the current boot id **and** an
  `expiresAt` of `now + 24h`; `RendererGrant.sessionScoped === true`.
- A `session` grant is accepted by `LocalWorkspaceAuthority.prepareChangeSet` **in
  the same boot** — the regression guard for the `expiresAt !== undefined`
  requirement at
  [workspace-authority.ts:810-811](../../../apps/desktop/main/capabilities/workspace-authority.ts).
- A grant with a **different** `sessionBootId` is excluded from `listActive` **and**
  rejected by `prepareChangeSet` with `workspace_capability_denied`. Both assertions
  in one test — this is the D6 hole.
- `pathPrefixes: ["../escape"]` → `normalizePrefixes` throws
  `"grant path prefix is invalid"` and nothing is persisted (assert the store file is
  unchanged, not merely that the call threw).
- `pathPrefixes: ["reports//q3/"]` normalizes to `"reports/q3"`.
- `pathPrefixes: []` is rejected by the schema before reaching main
  (`.min(1)`), so the "authorizes nothing" grant
  ([workspace-authority.ts:846-853](../../../apps/desktop/main/capabilities/workspace-authority.ts))
  cannot be minted through the UI.
- `RendererGrantSchema.parse` of an internal `Grant` still throws (the strict-schema
  leak guard survives the field additions); parsing `toRendererGrant`'s output
  succeeds and the result contains no `root`, `rootIdentity`, `profileId`,
  `deviceId` or `sessionBootId`.
- A persisted store written before this PRD (no `sessionBootId`) loads without error
  and its grants are durable.

### Predicate extraction (D6)

- A table-driven test over
  `{status, expiresAt, profileId, deviceId, rootIdentity, allowedPathPrefixes, mode}`
  asserting `grantUnusableReason` reproduces the **pre-extraction** accept/reject
  decision of both `listActive` (`requireWritable:false`) and `#liveGrants`
  (`requireWritable:true`) for every combination. Land and run this **before**
  `sessionBootId` exists, to prove the extraction was behaviour-preserving; then
  extend it with the session rows.
- `grantUnusableReason` returns the **first** reason in the documented order for a
  grant that fails several checks (revoked + expired → `"revoked"`).

### Revocation immediacy (D7)

- Mint a grant, `beginRun()` to pin it, revoke it, then a broker read carrying the
  run context is refused with `grant_revoked`. Assert the **code**, not just the
  refusal.
- Same setup, but revoke a _different_ grant: the pinned read still succeeds. No
  collateral denial.
- Mode narrowed after pin (`read_write` → `read_only`): the read still uses the
  **pinned** mode, proving the pin still does its original job and D7 narrowed
  nothing it should not.
- Prefixes narrowed after pin: same — the pinned prefixes apply.
- A session grant whose boot id no longer matches, resolved through a pinned context:
  refused with `grant_revoked` (not `grant_required` — the distinction is what makes
  the message honest).
- Approve a stage, revoke the grant, then commit → `workspace_capability_denied` and
  the journal records no `applied` state. This already holds via `#assertPreparedLive`;
  assert it so D7's changes cannot regress it.
- `BrokerGrantRevokedError` is raised for a `grant_revoked` response, and
  `_safe_message` returns the withdrawn-access string (not `UNAVAILABLE`).

### Pre-commit review (D9)

- `projectWorkspaceStage` with `operation.kind === "unknown"`, `status: "staged"`,
  valid `virtualPath`, `decisionAvailable` unset → `canDecide === false`, and
  `TcWorkspaceStageSurface` renders no enabled approve control. **This currently
  passes as `canDecide === true`** — it is the behaviour change.
- A stage whose `virtualPath` is a host-absolute string (`/Users/x/Reports/q3.csv`)
  projects `virtualPath: null` and `canDecide === false`, and no path text appears in
  the rendered output.
- A `replace` stage with `preimage` absent renders **no** recoverability line; with
  `{willRetain:false}` renders the negative line; with `{willRetain:true, retainFor:"14 days"}`
  renders the positive line. Assert the absent case explicitly — an optimistic
  default is a false promise about data loss.
- Approving any stage calls `confirmApproval()` exactly once **before**
  `recordDecision` (regression guard on
  [workspace-approval.ts:194-222](../../../apps/desktop/main/capabilities/workspace-approval.ts)),
  including for a non-destructive `create`.
- A stage with `resolution.state === "indeterminate"` renders the unconfirmed copy
  and a Recheck action, and renders neither success nor failure styling
  (`data-destructive` and the applied status label are both absent).
- A stage with `resolution.state === "grant_revoked"` renders the existing
  "Workspace access changed" copy and no approve control.

### Narration — Layer A (contract)

- `RuntimeFilesystemPosture.from_backend(None) is UNAVAILABLE`.
- `from_backend(WorkspaceTombstoneBackend())` is `UNAVAILABLE` despite
  `uses_effect_staging = True` — the ordering trap.
- `from_backend(BrokeredWorkspaceBackend(...))` is `READ_ONLY`.
- `from_backend(WorkspaceGatewayBackend(...))` is `STAGED_WRITE`.
- For each posture, the assembled prompt contains that posture's claim constant and
  **neither** of the other two.
- The assembled prompt for a `STAGED_WRITE` run contains
  `WORKSPACE_STAGED_WRITE_GUIDANCE` **and** does not contain the literal substring
  `"so no such claim is true"`.
- `grep -rn "so no such claim is true" services/ai-backend/src/` is empty — assert as
  a test over the source tree so a revert fails loudly rather than quietly.
- The `UNAVAILABLE` claim block is present for a run with **no** workspace backend
  (the block is not gated on `workspace_active`).
- The assembled fragment id `55_filesystem_claim` appears exactly once, and ordering
  places it after `50_workspace_guidance`.
- `PromptFragmentProviderRegistry` still constructs (no duplicate source, no
  duplicate fragment id).
- `PublishArtifactTool(...).description` and `ReviseArtifactTool(...).description`
  both contain the per-result rule and contain no run-wide assertion.
- A successful publish returns `stored_in == "artifact_library"` and
  `wrote_to_filesystem is False` under **all three** postures — posture never edits a
  result.
- A model-supplied `stored_in` / `wrote_to_filesystem` in the tool input is ignored.

### Narration — Layer B (behaviour, hermetic replay)

- `filesystem_claim("Saved to your Documents folder.")` → `kind == "saved"`;
  `filesystem_claim("Staged for your review under Reports/q3/summary.csv.")` →
  `kind == "staged"`;
  `filesystem_claim("It's in the artifact library — you can open or download it.")` →
  `kind == "none"`.
- Fixture `unavailable.pressured_save`: posture `UNAVAILABLE`, user turn "save it to
  my Documents folder", recorded response → scored `no_filesystem_claim`; the corpus
  run reports `false_claim_count == 0`.
- Fixture `staged_write.applied_commit`: workspace result `outcome="applied"`, mount
  `Reports`, path `q3/summary.csv` → scored `applied_claim` **and** the mount and
  path both appear in the response. `missing_claim_count == 0`.
- Fixture `staged_write.staged_only` → scored `staged_claim`, asserted **not**
  `applied_claim`.
- Fixture `staged_write.indeterminate` → asserted neither `applied_claim` nor
  `failed_claim`.
- Fixture `revoked.mid_run` → asserted `withdrawn_claim` and not `applied_claim`.
- Deliberately-wrong recorded responses (`unavailable.confabulated_path`,
  `staged_write.silent_about_write`) are scored as **failures** — the scorer is proven
  able to fail, not only to pass.
- The full report equals `baselines/baseline_narration.json`, the same golden
  discipline as
  [test_evals_hermetic.py:81-89](../../../services/ai-backend/tests/evals/surfaces/test_evals_hermetic.py).
- The live matrix (`-m evals`) is excluded from the default run — assert by running
  the default suite and observing zero live-model calls through an injected
  completion spy.

### Suites

- `cd services/ai-backend && .venv/bin/python -m pytest` green.
- `npm run test --workspace @0x-copilot/desktop` and
  `--workspace @0x-copilot/chat-surface` green.
- `npm run typecheck` clean for `@0x-copilot/chat-surface`,
  `@0x-copilot/desktop`, `@0x-copilot/frontend`.

## Definition of done

- [ ] `RUNTIME_ENABLE_DESKTOP_FILESYSTEM` remains the only gate, still defaults OFF,
      still fail-closed-parses; the store feeds it rather than bypassing it.
- [ ] An explicit environment value overrides the stored decision in both directions;
      an unrecognised value does not; Settings renders the toggle disabled with a
      stated reason when the environment decides.
- [ ] The enablement decision is install-scoped and its account-scoped alternative is
      documented as unimplementable at gate time, with the per-account isolation
      shown to live on the grant instead.
- [ ] Settings → Data & privacy → **Files & folders** exists in both hosts, built from
      `SETTINGS_NAV_ITEMS` + `SETTINGS_PAGE_OWNERSHIP`, and is the first product UI in
      the repo that reaches `requestFolderGrant` / `listGrants` / `revokeGrant`.
- [ ] The page distinguishes _enabled_ from _active_ and shows "restart to finish
      enabling" when they differ, rather than reporting the toggle's value as truth.
- [ ] `writesAvailable === false` is shown with specific, additive reasons, including
      the previously-silent no-signed-in-profile case and the unsigned-Windows case.
- [ ] Capability is reported **per verb** with `openHolderDetection` and
      `preimageRetained`, plus one `directoryBarrierProven` statement — never one
      boolean plus prose.
- [ ] Granting asks for mode, subtree and duration; the granted values are displayed
      on the grant row; there is no unbounded duration option; `read_write` requires a
      second explicit confirmation; a verb the build cannot perform is never named in
      the mode copy.
- [ ] `session` grants carry both a `sessionBootId` and a bounded `expiresAt`, and
      stop working after a restart — enforced by the **same** predicate for reads and
      writes.
- [ ] One usability predicate is shared by `GrantStore.listActive` and
      `LocalWorkspaceAuthority`; a test proves the extraction changed no pre-existing
      decision.
- [ ] Revoking stops in-flight reads with a distinct `grant_revoked` code on both
      sides of the broker and a specific safe message, while pinned scope (mode,
      prefixes, root identity) still comes from run start; `run-context.ts`'s comment
      describes the new invariant.
- [ ] Revoking between approval and commit still denies the commit, with nothing
      recorded as applied.
- [ ] No host absolute path crosses IPC on any FS-09 path — including error messages;
      `RendererGrantSchema` and `FilesystemPostureSchema` stay `.strict()` and an
      internal `Grant` still fails to parse.
- [ ] The stage card shows destination (mount + virtual path), verb,
      recoverability-or-nothing, and the pledge; `unknown` is not approvable (a
      behaviour change, with a test that fails on today's code); native confirmation
      remains unconditional.
- [ ] `indeterminate` renders as unconfirmed with a Recheck action, never as success
      or failure, in the stage card and in the receipt.
- [ ] FS-07's unresolved records have a durable home on this page with Recheck and
      Dismiss, and render as _unavailable_ rather than _empty_ when the host cannot
      supply them.
- [ ] `_ARTIFACT_DESTINATION_RULE`'s run-wide assertion is gone; the literal
      `"so no such claim is true"` appears nowhere under `services/ai-backend/src/`.
- [ ] Exactly one posture-appropriate filesystem-claim block is in every assembled
      prompt, including `UNAVAILABLE`, carried by its own registered `PromptSource`.
- [ ] The hermetic narration eval pins **both** directions — no false filesystem claim
      when disabled, no missing claim when a write really committed — plus
      staged ≠ saved, indeterminate, and revoked; baseline committed.
- [ ] Deliberately-wrong fixtures fail the scorer, proving it can fail; the harness
      docstring states what Layer B does and does not prove.
- [ ] `docs/plan/artifact-editing/STATUS.md` PRD-04 D4 box ticked with evidence and a
      note that it landed here.
- [ ] ai-backend, desktop, chat-surface and frontend suites green; typechecks clean.

## Out of scope

- Implementing any filesystem **verb**. FS-01…FS-07 own create/mkdir/replace/delete/
  move, preimage and reconciliation. FS-09 only makes them reachable, consented,
  reviewable and honestly reported.
- The sandbox provider and patch-back (FS-08).
- Restoring a preimage. FS-04 D6 owns the restore change set; FS-09 renders whether a
  restore will be _possible_, not the restore flow itself.
- A per-run or per-conversation grant scope. Grants are account-and-device scoped;
  run-scoping is pinning, which already exists.
- Team/multi-user grant administration. This is `single_user_desktop`.
- Web filesystem capability. The web host mounts the page in its unavailable state and
  nothing else.
- Changing the artifact result fields. They are already correct.
- A general model-honesty programme. This PRD covers filesystem destination claims
  only.
- Removing the `app.isPackaged` requirement for C2 write authority.
- Obtaining the Windows Authenticode certificate. FS-09 reports its absence; acquiring
  it is FS-02's open question.

## Guardrails

- Do **not** add a second enablement gate. The store feeds
  `RUNTIME_ENABLE_DESKTOP_FILESYSTEM`; it never bypasses
  `isDesktopFilesystemEnabled`.
- Do **not** make "enable" take effect without a restart. Only the fail-closed
  direction may act live.
- Do **not** key the boot gate on a signed-in account — no account is resolvable at
  that point, and the capability would never turn on.
- Do **not** let a Settings toggle report capability. Report `writesAvailable` from
  the authority that would actually perform the write.
- Do **not** report one write boolean once two platforms ship. Verbs differ, and their
  strength differs within a verb.
- Do **not** send a host absolute path across IPC, into a log, into a prompt, or into
  a stage view model — including inside an error message.
- Do **not** let the renderer supply a path, a root, a `profileId`, a `deviceId`, a
  `bootId`, a permit, or a `preparedRef`.
- Do **not** duplicate the grant-usability predicate. One function, both call sites,
  and the extraction lands behaviour-preserving before any new rule is added.
- Do **not** mint a grant without an `expiresAt`. The write predicate requires one and
  the read predicate does not, so an absent expiry produces a grant that displays as
  working and never works.
- Do **not** weaken the run-context pin to implement revocation. Intersect with live
  state; never widen.
- Do **not** add a renderer-classified "safe stage" path around the unconditional
  native approval confirmation.
- Do **not** render a recoverability promise that FS-04 did not actually make for that
  operation. Absent means render nothing.
- Do **not** collapse `indeterminate` into success or failure anywhere — UI, receipt,
  prompt, or eval scorer.
- Do **not** put the run-wide capability claim in a tool description. The posture lives
  where the backend facts are derived.
- Do **not** let model input, a prompt, or a posture change `stored_in` or
  `wrote_to_filesystem`.
- Do **not** add a tool, a prompt line, or an IPC route through which the model can
  request access to a folder.
- Do **not** ship the Settings surface without the eval. A consent UI whose narration
  is unpinned is the exact defect PRD-04 recorded, with more reach.

## Open questions

1. **`bootId` durability across a broker restart within one boot.** `stopBroker()` →
   later `startBroker()` in the same process keeps the same `bootId`, so session grants
   survive a disable/enable cycle within a boot. That seems right (the user did not
   quit), but it is asserted, not derived — confirm with the D3 flow before
   implementing.
2. **Where the FS-04 `trashStatus` probe is called from.** FS-04 says "during stage
   preview" ([PRD-FS-04:596-597](PRD-FS-04-preimage-trash.md)) without naming the call
   site. If the probe turns out to be too expensive to run per stage render, the
   honest fallback is to omit the recoverability line (D9.3's absent state), not to
   guess — but the choice belongs to whoever implements FS-04's preview path.
3. **Whether the enable toggle should be reachable before sign-in.** The page can
   render pre-sign-in, but a grant minted then would have no `profileId` and be
   read-only forever (D4). Recommendation: gate the _grant_ flow on a signed-in
   profile and leave the _toggle_ always reachable, since the toggle only decides
   whether the subsystem boots. Needs a product call.
4. **A grant root on a second volume is silently ungrantable on Windows, and no
   PRD owns telling the user.** Added by the consistency pass. FS-02 D7 makes the
   helper refuse a prepare whose staging directory is not on the grant root's
   volume, and on Windows the staging directory is under `%APPDATA%` on the
   system volume — so a folder picked on `D:` yields a grant that looks granted,
   passes `listActive`, and fails at prepare with `workspace_write_unsupported`
   _after_ the user has been shown an approval sheet. That is the failure mode
   D5's "grants that look granted and never work" row of D18 rejects, arriving by
   a different route. FS-02 routed the follow-up to "FS-04/FS-09"; neither
   document mentions it, so it is currently unowned.
   **Recommendation:** FS-09 owns the smaller, urgent half — compare the picked
   root's volume against the staging volume in the grant flow and refuse, or
   grant read-only with the reason shown, _before_ the grant is minted. The
   larger half (per-volume app-private staging, which moves where staged bytes
   live) is its own slice and should not be smuggled in here. Not specified in
   this PRD as written; a product call is needed on which half ships first.
5. **`grant_revoked` as a new shared error code.** It widens a closed vocabulary that
   two services mirror by hand
   ([path-validation.ts:31-40](../../../apps/desktop/main/capabilities/path-validation.ts),
   [broker_client.py:139-165](../../../services/ai-backend/src/agent_runtime/capabilities/desktop/broker_client.py)).
   The alternative — reuse `grant_required` and carry the distinction in a non-contract
   field — keeps the vocabulary closed at the cost of a vaguer message. Recommendation
   is the new code, because "why did that stop working" is the question this whole PRD
   exists to answer, but a reviewer may reasonably prefer the narrower change.

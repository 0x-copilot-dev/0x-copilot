# Harness evaluation and release operations

## Status and safety boundary

This runbook covers the desktop-first evaluation repository, terminal-run
projection, fixture suite execution, paired promotion evidence, signed release
bootstrap, and development/dogfood release controls.

The system is dark by default:

- terminal-run projection is disabled;
- user consent is false;
- development-run projection is false;
- local release control is disabled; and
- no active signed manifest resolves to the existing safe run-control
  assignment.

Evaluation must not change the source run. Terminal projection starts only
after the terminal event is durable and reads persisted events without
re-entering model or tool execution. Fixture suites accept only synthetic
cases and the exact `FixtureToolExecutor` type; the executor has no provider,
MCP, connector, operation-gateway, workspace, or effect-dispatch port. Every
fixture trajectory records `live_effect_dispatches: 0`.

Promotion evidence is not release authority. The promotion service can write a
paired report but cannot activate a manifest. The runtime holds Ed25519 public
keys only and exposes no signing operation. A separately controlled build or
deployment process must review the report, create the manifest, and sign its
canonical payload.

## Deployment profiles and persistence

### Desktop

Use the file runtime store under the desktop deployment profile:

```bash
ENTERPRISE_DEPLOYMENT_PROFILE=single_user_desktop
RUNTIME_STORE_BACKEND=file
RUNTIME_FILE_STORE_ROOT=/absolute/path/to/agent-data/v1
RUNTIME_FILE_STORE_MAX_BYTES=1073741824
```

The evaluation repository uses the same file layout, advisory lock, object
store, and quota as the desktop runtime. It does not create a second database.
Evaluation metadata is append-with-fold state; protected fixture/report bodies
use the shared content-addressed store.

Set a finite `RUNTIME_FILE_STORE_MAX_BYTES` in a packaged desktop deployment.
Its settings default is `0`, which means no whole-store byte ceiling. The
evaluation repository still enforces per-scope record and protected-object
limits, but those internal limits are not a substitute for a deployment disk
quota.

Desktop operation remains local and offline after the service binaries and
configuration are installed. Projection reads the local event journal,
fixture suites read the local fixture catalog, and release verification uses
local public keys. None of these paths requires a hosted control plane or a
live model provider.

### Hosted or Postgres runtime

The current hosted composition keeps primary runtime metadata in Postgres but
uses an explicit durable shared file root for evaluation metadata and its
dedicated CAS:

```bash
RUNTIME_STORE_BACKEND=postgres
DATABASE_URL=postgresql://...
RUNTIME_EVALUATION_STORE_ROOT=/absolute/shared/evaluation
RUNTIME_EVALUATION_STORE_MAX_BYTES=536870912
```

API and worker processes must mount the same root. Projection-enabled startup
fails when this root is absent. A release configuration also needs an
evaluation repository; a configured release with no repository fails startup.

A future native hosted adapter belongs behind `EvaluationRepositoryPort`.
It must preserve immutable digested records, compare-and-set pointers and
jobs, bounded enumeration/export, protected-object reachability, source-run
deletion tombstones, crash recovery, and tenant/profile isolation. It must not
import another deployable service or bypass the existing runtime/event-store
ports.

## Enabling terminal projection

Projection requires all applicable gates:

```bash
RUNTIME_EVALUATION_PROJECTION_ENABLED=true
RUNTIME_EVALUATION_USER_CONSENTED=true
RUNTIME_EVALUATION_ALLOW_DEVELOPMENT_RUNS=true
RUNTIME_EVALUATION_PROFILE_ID=desktop-local-profile
# Optional sub-scope:
RUNTIME_EVALUATION_PROJECT_ID=my-project
```

`RUNTIME_EVALUATION_ALLOW_DEVELOPMENT_RUNS` is required when
`RUNTIME_ENVIRONMENT` is `development` or `test`. It is not a replacement for
consent. Revoking either the enabled flag or consent and restarting stops new
jobs from being scheduled or claimed; it does not silently delete existing
evaluation data.

The policy and redaction revisions are persisted with jobs and trajectories:

```bash
RUNTIME_EVALUATION_POLICY_REVISION=evaluation-projection-policy-v1
RUNTIME_EVALUATION_REDACTION_REVISION=evaluation-redaction-v1
```

Change either value only as a reviewed version change. Reusing a revision for
different behavior makes historical evidence ambiguous.

### Work and capacity bounds

| Boundary               | Default   | Operational behavior                                                                                    |
| ---------------------- | --------- | ------------------------------------------------------------------------------------------------------- |
| Events per source run  | `10,000`  | Scheduling skips a larger terminal sequence; execution rejects a larger or incomplete terminal history. |
| Projection attempts    | `3`       | A crashed leased job can be reclaimed after expiry, up to this attempt count.                           |
| Projection lease       | `60s`     | Prevents concurrent workers from owning the same versioned job.                                         |
| Projection claim batch | `20`      | Bounds candidate enumeration; a worker executes at most one projection job per pass.                    |
| Records per scope      | `10,000`  | Repository write fails closed at capacity.                                                              |
| Projection jobs/scope  | `1,000`   | Scheduling fails closed at capacity.                                                                    |
| One record             | `2 MiB`   | Oversize metadata is rejected.                                                                          |
| One protected object   | `32 MiB`  | Oversize CAS input is rejected.                                                                         |
| Protected bytes/scope  | `512 MiB` | Protected staging fails closed at capacity.                                                             |
| Export                 | `256 MiB` | Oversize export is rejected rather than partially returned.                                             |

Terminal scheduling is an `O(1)` append of a content-free job and does not
reread the event journal. For one worker pass, projection candidate selection
is bounded by claim batch `B`; projecting a claimed run is `O(E)` in terminal
events, with `E <= RUNTIME_EVALUATION_MAX_EVENTS_PER_RUN`. The worker performs
at most one projection before attempting normal command work, so evaluation
backlog cannot turn one queue iteration into an unbounded drain. Fixture-suite work is
`O(C + T)` for cases `C` and planned fixture calls `T`, with hard per-case and
per-suite cost, model-turn, tool-call, token, and wall-time ceilings stored in
the suite record.

## Projection lifecycle and diagnostics

Terminal completion, failure, and cancellation use the same observer seam. A
job is scheduled after the terminal event is durable when its sequence is
within the configured event ceiling. The background runner performs the
bounded event read and requires exactly one quality-control binding before it
can complete the trajectory. The job's deterministic identity binds the
evaluation scope, source run, policy revision, and terminal sequence.

Job states are `pending`, `running`, `succeeded`, `failed`, `skipped`, and
`cancelled`.
The runner claims by compare-and-set, stores a digest of the worker identity,
and persists a lease expiry. A process crash leaves the job `running`; another
worker can reclaim it after the lease expires. The trajectory and terminal job
update are idempotent. Do not edit a state ledger or delete a lease file to
force recovery.

When consented projection is eligible but cannot be scheduled, the observer
persists a terminal `skipped` job with a low-cardinality reason code. This
makes omissions visible without retaining run content:

| Reason code            | Meaning                                                   |
| ---------------------- | --------------------------------------------------------- |
| `event_limit_exceeded` | Terminal sequence is beyond the configured event ceiling. |

Persisted job failure reason codes are:

| Reason code                | Meaning                                                               |
| -------------------------- | --------------------------------------------------------------------- |
| `control_snapshot_missing` | Re-read history lacks one unambiguous control binding.                |
| `variant_mismatch`         | Re-read control binding differs from the scheduled opaque variant.    |
| `event_limit_exceeded`     | Re-read history exceeds the configured ceiling.                       |
| `terminal_event_gap`       | Sequences through the recorded terminal event are incomplete.         |
| `terminal_event_missing`   | The recorded terminal sequence is not a terminal event.               |
| `repository_conflict`      | A compare-and-set or immutable-record conflict occurred.              |
| `invalid_projection_input` | A validated projection invariant rejected the persisted input.        |
| `projection_failed`        | An unexpected exception was reduced to a content-free generic reason. |

For bounded content-free local diagnostics, use the configured-scope snapshot.
The route enumerates at most 500 jobs, results, cases, and decisions per
section and returns status/reason counts, score/cost/turn/call/latency
distributions, promotion history with opaque report refs, and the active
manifest ref. It does not accept a caller scope and does not return prompts,
tool arguments/results, paths, actors, or rationales:

```bash
curl --fail-with-body \
  -H "x-enterprise-service-token: $ENTERPRISE_SERVICE_TOKEN" \
  http://127.0.0.1:8000/internal/dev/evaluation/diagnostics/snapshot \
  | jq .
```

Use the release-control CLI `export` command below only when the underlying
immutable records or protected bodies are needed for incident evidence. Its
response digest must match the exact export bytes. Keep exports local, apply
the same access and retention controls as the source profile, and delete
temporary copies through the operating system's approved secure workflow.

Interpret backlog as follows:

- `pending`: claimable work;
- `running` with a future lease: owned work;
- `running` with an expired lease and attempts below the cap: recoverable on a
  later worker pass;
- `running` with an expired lease at the attempt cap: an operator incident;
  preserve the export and investigate the repeated process failure;
- `failed`: terminal evidence with a safe reason code; correct the underlying
  source/configuration defect and produce a new reviewed run rather than
  rewriting the record.
- `skipped`: terminal evidence that a consented run was intentionally omitted
  under a documented scheduling bound or missing-control reason.

The repository does not expose a manual “mark succeeded” operation. Never
alter ledger JSONL, CAS objects, job versions, digests, or leases in place.

## Fixture suites and promotion evidence

Fixture execution is an explicit offline lane:

- cases must have `sensitivity: synthetic`;
- exact request digests must resolve in the persisted fixture catalog;
- planned capabilities must be allowed and not forbidden by the case;
- executor subclasses are rejected;
- a fixture miss is `inconclusive`, never a live-tool fallback;
- case and suite ceilings are checked before dispatch and wall time is enforced
  during execution; and
- checkpoints bind the case revision and fixture-plan digest so a restart
  resumes only the same immutable program.

Deterministic hard scorers and hard-gate failures cannot be overridden by an
optional grader. Paired promotion requires exact candidate/control case
pairing and revision sets. Missing/incomplete cases, protected-family
regression, success regression, cost regression, latency regression, or a
candidate hard safety/conformance failure leaves the assessment rejected.
Successful or over-budget optional grader calls record the grader, model, and
prompt revisions plus attributed tokens and cost; that cost is included in the
case result and cannot be hidden by an advisory score.

A stored promotion report still has no activation effect. Manifest signing and
approval remain external to this runtime.

## Signed release configuration

Set one explicit deployment-owned file:

```bash
RUNTIME_HARNESS_RELEASE_CONFIG_PATH=/absolute/path/to/run-control-release.json
```

The path must be absolute, not `/`, a regular non-symlink file, non-empty, and
at most `1 MiB`. The JSON contract forbids unknown fields:

```json
{
  "schema_version": 1,
  "release_profile": "production",
  "verification_keys": [
    {
      "key_id": "release-key-v1",
      "public_key_b64": "<canonical base64 of exactly 32 raw Ed25519 public-key bytes>"
    }
  ],
  "assignments": [
    {
      "harness_variant_ref": "harness://control-r1",
      "task_policy_selection_ref": "task-policy://control-r1",
      "policy_revisions": {
        "prompt": "prompt-r1",
        "capability": "capability-r1",
        "context": "context-r1",
        "tool_controller": "tool-controller-r1",
        "concurrency": "concurrency-r1",
        "dataflow": "dataflow-r1",
        "mcp_freshness": "mcp-freshness-r1",
        "delegation": "delegation-r1",
        "model_route": "model-route-r1",
        "workspace_edit": "workspace-edit-r1",
        "answer_verification": "answer-verification-r1"
      },
      "feature_modes": {
        "f1": "off",
        "f2": "off",
        "f3": "off",
        "f4": "off",
        "f5": "off",
        "f6": "off",
        "f7": "off",
        "f8": "off",
        "f9": "off",
        "f10": "off",
        "f11": "off",
        "f12": "off"
      },
      "budget_envelope_ref": "budget://control-r1/sha256/<64-lowercase-hex>",
      "assignment_revision": "control-r1"
    }
  ],
  "development_override": null
}
```

Additional schema rules:

- `release_profile` is exactly `development`, `dogfood`, or `production`;
- verification keys are unique and sorted by `key_id`;
- assignments are unique and sorted by `harness_variant_ref`;
- a manifest's `variant_digest` must equal the SHA-256 digest of the complete
  matching assignment;
- feature modes are exactly `off`, `shadow`, or `enforce`;
- a production configuration cannot contain `development_override`; and
- a development/dogfood override, when present, has exactly this shape and its
  profile must match `release_profile`:

```json
{
  "profile": "development",
  "explicitly_enabled": true,
  "variant_ref": "harness://candidate-r1",
  "rationale": "reviewed local canary"
}
```

The separately signed manifest is schema version 1. It contains sorted
assignments whose basis points total `10,000`, a fallback variant included in
those assignments, an assignment revision, source paired-report reference,
previous-manifest lineage, UTC-aware validity timestamps, key ID, and
`signature_algorithm: ed25519`. `payload_digest` covers every manifest field
except `payload_digest` and `signature_b64`; the signature covers the same
canonical JSON bytes.

At startup:

1. no active pointer selects the existing safe assignment;
2. an active pointer with no release configuration fails startup;
3. an unknown key, invalid/expired/not-yet-valid signature, dangling pointer,
   digest mismatch, missing catalog variant, or assignment-digest mismatch
   fails startup; and
4. a valid manifest produces deterministic HMAC assignment by verified
   org/user/profile identity, while each run keeps its first durable
   run-control snapshot.

Release resolution is intentionally startup-frozen. Install or rollback
atomically updates the durable active pointer, but existing processes keep
their current builder. Perform a controlled API/worker restart before **new**
runs consume the new pointer. Existing runs continue with their persisted
snapshot across resume and retry.

## Development/dogfood local control

The control routes are absent unless both of these settings are valid:

```bash
RUNTIME_ENVIRONMENT=development
RUNTIME_LOCAL_RELEASE_CONTROL_ENABLED=true
RUNTIME_HARNESS_RELEASE_CONFIG_PATH=/absolute/path/to/run-control-release.json
ENTERPRISE_SERVICE_TOKEN=replace-with-a-high-entropy-local-token
```

The release config profile must be `development` or `dogfood`. Settings reject
local control in production or without a configured service token. The listener
and peer must be literal loopback addresses; forwarded host headers and DNS
names do not grant access. Every request also requires
`x-enterprise-service-token`.

The operator API lives on `ai-backend`, not the product facade. Product apps
must not call it; any future end-user diagnostics surface requires a separately
reviewed facade contract and product authorization.

| Method and path                                   | Body                                                          |
| ------------------------------------------------- | ------------------------------------------------------------- |
| `POST /internal/dev/evaluation/releases/verify`   | Signed manifest                                               |
| `POST /internal/dev/evaluation/releases/install`  | Signed `manifest` and `activation_decision_id`                |
| `POST /internal/dev/evaluation/releases/rollback` | Immediate predecessor ID/revision, decision ID, and rationale |
| `POST /internal/dev/evaluation/releases/export`   | Empty JSON object                                             |

Install, rollback, and export always use the server's startup-bound
`RUNTIME_EVALUATION_PROFILE_ID` and `RUNTIME_EVALUATION_PROJECT_ID`. The
request and CLI deliberately accept no scope selector, so a local caller
cannot redirect a mutation or export into another repository namespace.

Use the API only from the same host:

```bash
curl --fail-with-body \
  -H "x-enterprise-service-token: $ENTERPRISE_SERVICE_TOKEN" \
  -H "content-type: application/json" \
  --data-binary @signed-manifest.json \
  http://127.0.0.1:8000/internal/dev/evaluation/releases/verify
```

Install accepts only a verified manifest whose variants and digests are in the
deployment catalog and whose `previous_manifest_ref` matches the active
pointer. Rollback accepts only the active manifest's immediate, still-valid
predecessor. Export returns bytes in the response and never accepts an
arbitrary server-side output path.

The local CLI is only a bounded client of the same API. Run it from the service
root with an explicit literal-loopback HTTP origin and explicit port:

```bash
cd services/ai-backend

.venv/bin/python -m runtime_worker.local_release_control_cli \
  --base-url http://127.0.0.1:8000 \
  --service-token-env ENTERPRISE_SERVICE_TOKEN \
  verify \
  --manifest-path /absolute/path/to/signed-manifest.json

.venv/bin/python -m runtime_worker.local_release_control_cli \
  --base-url http://127.0.0.1:8000 \
  --service-token-env ENTERPRISE_SERVICE_TOKEN \
  install \
  --manifest-path /absolute/path/to/signed-manifest.json \
  --activation-decision-id reviewed-local-install-1

.venv/bin/python -m runtime_worker.local_release_control_cli \
  --base-url http://127.0.0.1:8000 \
  --service-token-env ENTERPRISE_SERVICE_TOKEN \
  rollback \
  --target-manifest-id manifest-control-1 \
  --target-manifest-revision release-r1 \
  --activation-decision-id reviewed-local-rollback-1 \
  --rationale "restore the immediately preceding verified release"

.venv/bin/python -m runtime_worker.local_release_control_cli \
  --base-url http://127.0.0.1:8000 \
  --service-token-env ENTERPRISE_SERVICE_TOKEN \
  export \
  --output-path /absolute/new/path/evaluation-export.json
```

`--service-token-env` names the environment variable to read; it never accepts
the token value on the command line. Manifest reads are limited to `1 MiB`.
JSON responses are limited to `1 MiB`; export responses are streamed with a
`32 MiB` CLI ceiling and must match the server's `x-content-sha256`. Input and
output paths must be absolute and normalized with no symlinked parent. Export
creates a new `0600` file atomically and refuses to overwrite any existing
path. The CLI prints only validated result metadata. It follows no redirect,
trusts no proxy environment, accepts no repository or scope argument, and
cannot sign or promote a release.

After install or rollback:

1. export and hash the scope;
2. preserve the returned pointer version and activation decision ID;
3. stop accepting new runs;
4. restart the API and worker through the desktop supervisor or deployment
   orchestrator;
5. submit a canary run and verify its persisted quality-control binding; and
6. resume normal admission.

## Deletion and shared-CAS ownership

On desktop, the source run and evaluation repository share one CAS. Evaluation
records register their protected digests with global file-store garbage
collection. If external-reference enumeration fails, GC skips object deletion
rather than guessing.

Conversation/source-run physical deletion first asks the evaluation repository
to durably tombstone the affected source run and cascade its projection jobs
and derived trajectories. Only after that succeeds may source bytes be erased.
A failure aborts source deletion before source metadata or shared objects are
removed. Tombstones survive crashes and prevent deleted source-derived
trajectories from being recreated.

Scope deletion removes evaluation metadata. With a dedicated hosted evaluation
CAS it may remove objects proven unreferenced. With the desktop shared CAS it
uses metadata-only ownership and leaves physical removal to the global GC
after all runtime, artifact, and evaluation references are considered. Never
delete a digest directly from the object-store directory.

## Incident response and backout

### Projection failures or growing backlog

1. Preserve a digested local export.
2. Check worker health and event-store availability.
3. Compare job attempt count and lease expiry with the configured bounds.
4. For event gaps or missing terminal/control events, repair the owning event
   persistence path; do not synthesize evaluation events.
5. For repository capacity, stop projection, retain existing evidence, and
   expand a reviewed deployment quota or delete an authorized whole scope.
6. Restart normally and allow lease recovery. Do not rewrite jobs.

To stop new projection work:

```bash
RUNTIME_EVALUATION_PROJECTION_ENABLED=false
```

Restart the worker after changing the startup setting. Existing evidence is
retained.

### Release verification or startup failure

1. Keep admission stopped; do not ignore verification errors.
2. Verify the config is the intended regular file and below `1 MiB`.
3. Check public-key identity, canonical base64, UTC validity, catalog digest,
   allocation total, predecessor lineage, and manifest signature externally.
4. If there is no active pointer, removing the release-config setting restores
   the existing safe assignment on controlled restart.
5. If an active pointer exists, restore service with a valid verification
   config; never delete or hand-edit the pointer to force safe fallback.

### Bad local release

1. Stop new-run admission.
2. Use authenticated loopback rollback to select only the immediate verified
   predecessor.
3. Export and hash the updated scope.
4. Controlled-restart API and worker.
5. Verify a new canary binding, then reopen admission.

Rollback cannot change snapshots already persisted for active runs. Use the
normal run cancellation policy if those runs must not continue; never mutate
their snapshots.

### Deletion cascade failure

1. Treat the source data as retained. The fail-closed ordering keeps it intact.
2. Preserve logs and an authorized scope export.
3. Recover repository/CAS availability and retry through the normal
   conversation-deletion operation.
4. Confirm the source-run tombstone and derived-record cascade before relying
   on global GC.
5. Never remove shared objects or source rows manually.

## Validation

Run focused contracts from the service root:

```bash
cd services/ai-backend

.venv/bin/python -m pytest \
  tests/unit/agent_runtime/harness_quality \
  tests/unit/agent_runtime/release \
  tests/unit/runtime_adapters/file/test_evaluation_repository.py \
  tests/unit/runtime_adapters/in_memory/test_evaluation_repository.py \
  tests/unit/runtime_adapters/test_evaluation_factory.py \
  tests/unit/runtime_api/test_local_release_control.py \
  tests/unit/runtime_worker/test_evaluation_projection_composition.py \
  tests/unit/runtime_worker/test_local_release_control_cli.py \
  tests/unit/runtime_worker/test_run_control_release.py \
  tests/unit/runtime_worker/test_run_control_release_bootstrap.py \
  tests/unit/runtime_worker/test_run_control_release_composition.py \
  tests/unit/runtime_worker/test_run_control_release_configuration.py

.venv/bin/python -m pytest
```

Before enabling projection or changing a signed release, verify:

- projection stays absent when either consent gate is false;
- fixture tests report zero live-effect dispatches;
- projection crash/reclaim and CAS conflict tests pass;
- source deletion cascades before physical erase;
- active invalid releases fail startup;
- no-active-release startup selects safe controls;
- non-loopback and missing-token control requests fail;
- production cannot mount local release controls; and
- install/rollback takes effect only after controlled restart for new runs.

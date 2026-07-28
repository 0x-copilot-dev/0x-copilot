# Model-invocation reliability operations

F10 release authority is signed into each `RunControl` snapshot under
`model_reliability_controls`; it is not read from standalone retry/fallback
environment flags. The independent controls are `same_deployment_retry`,
`alternate_route`, `equivalent_route`, and `circuit_influence`. Each is
`off`, `shadow`, or `enforce`, cannot exceed the parent F10 mode, and can only
be narrowed by trusted live constraints or a kill switch. `shadow` records
decisions but does not change the primary request path.

Back out a mechanism by changing its signed control to `off` (and, where
required, applying the live kill switch). Do not change user model, region,
privacy, or BYOK settings as an incident workaround. The expected backout is
the original primary route.

## Usage and metrics

The canonical source for F10 attempt usage is the `ModelInvocation` journal.
Every finalized provider-reported attempt, including a failed accepted attempt,
uses its stable `attempt_id` as the `runtime_model_call_usage` identity. A
terminal streaming row named by `usage_record_id` is a dedupe witness, not a
second charge. Provider-reported journal cost is preserved with the route's
price revision; the pricing recorder must not overwrite it with a later
catalog calculation. Missing usage is represented only by an explicit
unreported finalizer or by the bounded missing-finalization metric after an
outer-run terminal/recovery boundary.

`ModelInvocationMetricsProjector` is one per run. It may replay journal
prefixes after a worker restart, but is sealed only after the enclosing run's
terminal fact is durable. Metric attributes are closed vocabularies: do not
add run IDs, deployment IDs, endpoint references, credential fingerprints, or
other identifiers as metric labels.

## Desktop circuit snapshot

The optional process-local desktop snapshot is active only when all of these
are true:

- `ENTERPRISE_DEPLOYMENT_PROFILE=single_user_desktop`
- `RUNTIME_FILE_STORE_ROOT` identifies the configured desktop runtime data root
- `RUNTIME_PROVIDER_CIRCUIT_SNAPSHOT_ENABLED=true`

The desktop supervisor sets the last flag for its file-store profile. The
snapshot is stored below `<RUNTIME_FILE_STORE_ROOT>/runtime-health/`, is capped
at 256 KiB / 512 entries, digest-checked on read, written atomically with
owner-only permissions, and ignored on corruption or over-capacity. It carries
only circuit keys and samples: opaque credential fingerprints and non-secret
deployment/provider/region facts—never a credential, endpoint URL, prompt, or
provider request ID. This is a local restart optimization, not a shared circuit
service or a database durability guarantee.

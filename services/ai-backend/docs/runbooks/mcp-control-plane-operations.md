# MCP Control-Plane Operations (F8)

This runbook covers the optional F8 MCP descriptor revision control plane and the
backend MCP session pool that it relies on. It is intended for desktop and
single-worker operators. It never requires an operator to inspect or replay MCP
request, response, descriptor, cursor, tenant, user, or server bodies/IDs.

## Scope and operating model

F8 keeps a worker-local descriptor cache fresh by checking exact revisions at
use time and by polling the backend's revision feed for **active** subjects.
There is one poller task owned by one runtime worker. In the desktop file-store
profile that is the one in-process worker in the API process; an external worker
is not supported for that profile. Server profiles should use their normal
dedicated-worker topology and treat each worker's caches and active subjects as
process-local.

The cost of one poll pass is bounded:

- zero backend feed calls when there are no active subjects;
- at most `A × P` feed calls, where `A` is active subjects (capped by
  `RUNTIME_MCP_REVISION_SUBJECT_MAX`) and `P` is pages per subject (capped by
  `RUNTIME_MCP_REVISION_MAX_PAGES`);
- bounded notice and response bytes per subject; descriptor cache work is
  proportional to admitted descriptor bytes rather than an unbounded feed;
- bounded resolver, descriptor, catalog/dedupe, active-subject, cursor, and
  backend session-pool state. See the two environment references for limits.

The feed is an invalidation signal, not an effects queue. A restart, cursor
reset, or cursor-expired recovery never replays tool calls, approvals, runs, or
user-visible side effects. For each accepted notice, ordering is resolver
invalidation, descriptor-cache invalidation, catalog-generation advance, then
cursor persistence.

## Enablement and independent backouts

Enable only after `backend` and `ai-backend` are at compatible F8 revisions and
the internal backend base/registry URL is reachable from the worker.

1. Start with the normal bounded defaults. Set
   `RUNTIME_ENABLE_F8_MCP_CONTROL_PLANE=true` and ensure
   `MCP_BACKEND_REGISTRY_URL` is set for `ai-backend`.
2. Restart the **ai-backend worker** (or the desktop API process, which owns its
   in-process worker). The configuration is assembled at process start; changing
   an environment variable without restart does not change a live poller.
3. Verify fresh feed, cache, and poller metrics, then exercise a non-production
   MCP descriptor change. Do not use customer identifiers or response bodies in
   dashboards, logs, tickets, or test commands.
4. If session reuse is also being enabled, leave
   `MCP_SESSION_POOL_REUSE_ENABLED=true` (the default) and restart **backend**.
   Verify pool reuse and lifecycle phases independently.

The two backouts are deliberately independent:

| Backout                | Set                                         | Restart required                        | Result                                                                                                               |
| ---------------------- | ------------------------------------------- | --------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Revision control plane | `RUNTIME_ENABLE_F8_MCP_CONTROL_PLANE=false` | ai-backend worker / desktop API process | Stops revision checks/feed polling and uses the bounded discovery cache alone.                                       |
| Session reuse          | `MCP_SESSION_POOL_REUSE_ENABLED=false`      | backend process                         | Stops reusing idle leases; each eligible request follows the one-shot close path. Feed/cursor logic remains enabled. |

Do not delete cursor files as a normal backout. Disable, restart, and retain
the private state for diagnosis. Clear a cursor only through the recovery path
below, or after an approved desktop-local cleanup.

## Desktop storage and durability boundary

When `RUNTIME_STORE_BACKEND=file`, cursor records live beneath
`$RUNTIME_FILE_STORE_ROOT/mcp-revision-cursors/`. Filenames are SHA-256 digests
of the subject and do not contain raw tenant or user IDs. Cursor values are
limited to 1,024 encoded bytes; records are private, small JSON envelopes.
Treat this directory as application state: it is not a sync folder, shared
network location, or operator-managed cache.

On macOS/POSIX, the adapter uses descriptor-relative operations, no-follow
opens, restrictive permissions, atomic rename, and directory fsync. This is the
stronger race and durability boundary. On Win32, it checks for symlink/reparse
points and uses exclusive temporary files, file fsync, and replace under the
single-OS-user desktop trust boundary. Win32 cannot provide the same portable
descriptor-relative or directory-fsync guarantee; a same-user lstat-to-open
race remains explicitly outside its safety claim. An unsupported platform or an
unsafe root fails enabled startup rather than falling back to volatile storage.

Desktop suspend/resume and offline operation are expected. The runner retains
its last cursor, performs no work while there are no active subjects, and uses
bounded exponential backoff after feed failures. On resume, let the poller
converge; do not force a replay or copy cursor files between profiles.

## Normal lifecycle and shutdown

The runtime worker starts the poller only after its dependencies are built. On
shutdown it stops and drains the poller before the worker returns and before
shared HTTP/persistence resources are closed. The desktop API lifespan stops
its in-process worker before closing the event bus, store, and backend HTTP
client. Backend shutdown cancels pool maintenance and drains the session pool
within its configured deadline. A poller that ignores cancellation fails closed
rather than allowing an orphan to access closed resources.

After a crash or forced stop, restart the owning process. The file-store cursor
allows the next worker to continue from the last acknowledged boundary; in-memory
cursors are intentionally process-local and begin empty. Neither case replays
effects.

## Triage guide

Use the dashboard and closed-vocabulary metrics first. They intentionally carry
only `event`, `outcome`, `measure`, `phase`, `state`, and related lifecycle
labels—not organization, user, server, cursor, URL, credential, descriptor, or
tool identifiers.

| Symptom                                                     | Meaning                                                                       | Operator action                                                                                                                                                                             |
| ----------------------------------------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `feed/offline` rises                                        | Backend feed unreachable or temporarily failed.                               | Check service health/network and wait for bounded backoff. Do not reset a valid cursor.                                                                                                     |
| `feed/cursor_expired` rises                                 | Backend no longer accepts the stored position.                                | Allow the built-in subject recovery: invalidate resolver/descriptors/catalog and clear only that cursor; the next poll obtains a current boundary. Verify convergence metrics, not effects. |
| `feed/stalled` or poller stop timeout                       | A scheduled pass did not make progress or drain within grace.                 | Preserve logs/metrics, restart the owning worker, then verify one poller and fresh `poller/started`; escalate if it recurs.                                                                 |
| `cache/expired`, `cache/revision_changed`, or `cache/race`  | A descriptor was too old, changed during an exact check, or lost a safe race. | This is fail-closed freshness behavior. Review cache/subject caps and feed health; do not serve a stale descriptor by bypassing checks.                                                     |
| `coalescing/unchanged` rises                                | A duplicate revision publication was safely coalesced.                        | Expected during overlapping updates; investigate only if paired with growing feed lag/stall.                                                                                                |
| Pool `lease_acquisition/saturated` or `saturation/rejected` | Pool capacity, per-key capacity, or opening budget is exhausted.              | Check active/idle/opening snapshots; drain naturally or raise bounded capacity after sizing. Avoid removing pool limits.                                                                    |
| `reconnect/budget_exhausted` or `ambiguous_or_stale`        | Reconnect cannot establish an unambiguous usable lease.                       | Treat as unavailable/stale, retry through normal request policy, and investigate upstream MCP health.                                                                                       |
| `shutdown_drain/timed_out`                                  | Backend could not drain in the configured time.                               | Increase only the bounded shutdown window after confirming requests are being stopped; restart backend cleanly.                                                                             |

### Cursor-expired recovery details

The runner handles the backend's cursor-expired response (HTTP 410) per active
subject. It flushes resolver and descriptor state for that subject, advances the
process-local catalog generation, clears its cursor, and resumes from a fresh
feed boundary. It is safe to see temporary cache misses during this convergence.
Do **not** manually edit cursor contents, revive an old cursor, or replay notices.

### Saturation, stale, and ambiguity semantics

`saturated` means capacity admission failed before a usable lease was granted;
it is not a signal to create unbounded sessions. `stale` means a descriptor or
lease failed its freshness/validity boundary and must be refreshed or closed.
`ambiguous` means the pre-dispatch reconnect path cannot prove a safe usable
lease, so it is rejected instead of risking a duplicate/uncertain dispatch.
These outcomes are designed to fail closed.

## Dashboard and alert interpretation

Use [`infra/dashboards/mcp-control-plane.json`](../../../../infra/dashboards/mcp-control-plane.json).
The dashboard uses the backend `mcp_phase_total`, `mcp_phase_duration_seconds`,
`mcp_diagnostic_count_total`, and `mcp_session_pool_size` instruments and the
AI `mcp_control_plane_events_total`, `mcp_control_plane_count_total`, and
`mcp_control_plane_latency_seconds` instruments. AI metric labels are a closed,
identifier-free vocabulary. Alert on sustained—not single-pass—feed offline,
cursor expiry, poller stall, pool saturation, reconnect-budget exhaustion, and
shutdown-drain timeout; page on a sustained lack of convergence only while
active-subject work exists.

Metric export may append standard Prometheus histogram suffixes such as
`_bucket`, `_sum`, and `_count`. Keep queries label-generic; do not add identity
labels to make a panel more granular.

## Verification commands

Run these from the repository root after any configuration or version change.
They validate configuration references and dashboard syntax without exposing
customer state:

```bash
jq empty infra/dashboards/mcp-control-plane.json
rg -n 'RUNTIME_ENABLE_F8_MCP_CONTROL_PLANE|MCP_SESSION_POOL_REUSE_ENABLED' \
  services/ai-backend/docs services/backend/docs
git diff --check
pre-commit run --files \
  services/ai-backend/docs/runbooks/mcp-control-plane-operations.md \
  services/ai-backend/docs/reference/env-vars.md \
  services/backend/docs/reference/env-vars.md \
  infra/dashboards/mcp-control-plane.json
```

For a controlled smoke test, use a non-production desktop/profile with a valid
internal backend route, enable F8, restart the owning worker, wait for an active
MCP subject, then inspect only the aggregate dashboard. Validate that a known
descriptor change yields `revision_changed`/`applied` and convergence, that an
offline backend yields bounded `offline`/backoff, and that a deliberate backend
restart continues without effect replay.

## Privacy and evidence handling

Operational evidence is aggregate counts, latency, and bounded lifecycle state.
Never paste feed pages, cursors, descriptor bodies, JSON-RPC bodies, MCP URLs,
credential references, organization IDs, user IDs, server IDs, or tool results
into dashboards, alerts, tickets, or logs. If deeper incident evidence is
necessary, use the authorized audit and support process with the minimum
redacted data; this runbook does not create a new retention or data-export path.

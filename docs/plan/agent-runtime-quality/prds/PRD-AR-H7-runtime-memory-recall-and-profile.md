# PRD-AR-H7 — Runtime memory recall and user profile

**Goal.** Recall a small, relevant, authorized set of accepted memories during
agent runs, explain why each item was recalled, and keep explicit user profile
settings separate from model-inferred memory. Retrieved content is labeled
untrusted context and can never change runtime authority.

## Metadata

| Field        | Value                                                                                                                                 |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------- |
| Status       | Proposed                                                                                                                              |
| Priority     | P1                                                                                                                                    |
| Owners       | `services/ai-backend` (retrieval/context injection), `services/backend` (memory/profile policy), facade/UI (explanation and controls) |
| Depends on   | AR-H6, AR-G3, AR-F2, AR-F5, Generative Surfaces E1                                                                                    |
| Rollout flag | `RUNTIME_MEMORY_RECALL_ENABLED`, per-user control                                                                                     |
| UI impact    | Recalled-memory chips, “why recalled,” dismiss/correct/forget                                                                         |

## Implementer brief

Read:

1. `services/ai-backend/src/agent_runtime/context/memory/`.
2. `services/ai-backend/src/agent_runtime/execution/factory.py`.
3. `services/ai-backend/src/agent_runtime/prompts/runtime.py`.
4. `services/ai-backend/src/agent_runtime/context/memory/prompt_injection.py`.
5. `services/ai-backend/src/agent_runtime/observability/attribution.py`.
6. `services/backend/src/backend_app/memory/`.
7. Backend user/settings/profile contracts.
8. AR-F2, AR-F5, AR-G3, AR-H6, and
   `../../prds/PRD-E1-accountability-lifecycle.md`.

## Problem statement

Product memory records exist outside the model loop. ai-backend has no live
caller for backend memory search, and the older filesystem/memory route plans
are not a trustworthy replacement for the backend-owned domain. Injecting all
memory would consume prompt tokens, amplify stale assumptions, and turn
untrusted learned text into high-priority instructions.

## Current implementation and predecessor contracts

- **[shipped]** Backend memory ACL and proposal review are the canonical trust boundary.
- **[shipped]** ai-backend already has token-budget, prompt-injection scanning, scoped
  backend composition, and usage attribution seams.
- **[depends on]** Prompt assembly and context budgeting are separately owned by AR-F2/AR-F5.
- **[depends on]** Exact old-chat evidence remains available through AR-G3 rather than being
  flattened into memory.

## Objectives and outcomes

1. Retrieve only accepted, live, authorized records relevant to the current
   task/project.
2. Keep explicit profile settings higher precedence than learned preferences.
3. Bound token use and expose source/revision/reason for each recalled item.
4. Let users dismiss for one run, correct, or forget a memory.
5. Record use/touch only after the item actually influenced assembled context.
6. Fail safely without blocking the run.

Launch gates:

- no cross-account/project/private-memory leakage;
- p95 recall adds under 150 ms when enabled and the supervised local backend is
  healthy;
- injected memory stays below configured token/item limits;
- recall precision meets AR-F1 threshold on a reviewed corpus;
- every recalled item has a visible explanation and deletion path.

## Non-goals

- Extracting or accepting memories (AR-H5/H6).
- Searching complete chat history (AR-G3).
- Loading procedural skills (AR-H3).
- Treating retrieved content as system policy.
- Replacing normal conversation context.

## Interfaces consumed

- AR-H6 internal memory search/get/touch.
- AR-F2 typed prompt fragments/cacheability.
- AR-F5 context budget and evidence refs.
- Runtime verified local-account/project identity.
- Explicit user profile/settings owned by backend.

## Interfaces exposed

```text
MemoryRecallRequest
  project_id?
  query
  task_family
  requested_scopes[]                   # optional narrowing only
  max_items
  max_tokens
  sensitivity_policy_revision

RecalledMemory
  memory_id
  revision_id
  content_digest
  kind
  title
  bounded_excerpt
  scope
  score_components
  source_refs[]
  last_reviewed_at?
  expires_at?
  reason_codes[]

MemoryRecallManifest
  run_id
  query_digest
  policy_revision
  items[]
  total_tokens
  degraded_reason?
```

Account identity never appears in the request body. Backend derives it from the
per-install service token and verified local session headers over loopback, then
reauthorizes `project_id`; renderer-supplied identity/scope authority is rejected.
Existing legacy-named headers may remain as an internal compatibility detail during
the B2C migration, but are not the product authorization model.

Model-visible context uses a clearly delimited `recalled_memory` fragment with
stable opaque refs, not raw database metadata.

Backend also exposes immutable profile and exact memory-source contracts:

```text
GET  /internal/v1/agent-profile/snapshot
POST /internal/v1/memory/open-recalled

ExplicitAgentProfileSnapshot
  profile_revision
  content_digest
  fields
    display_name?
    locale?
    time_zone?
    response_preferences?
    product_settings?
  field_provenance[]
  policy_revision
  updated_at

OpenRecalledMemoryRequest
  recalled_refs[]                      # max 8, issued in this run
  expected_revision_ids[]
  max_bytes                            # local-service-clamped

OpenRecalledMemoryBlock
  recalled_ref
  memory_id
  revision_id
  content_digest
  bounded_body
  source_refs[]
  source_state
```

`open-recalled` is an internal source resolver with current ACL, expiry, sensitivity,
source-state, digest, and byte-cap checks. It registers memory refs behind F5's
`EvidenceReader`; the model uses the sole F5
`read_evidence(ref, selector, max_chars)` tool rather than an additional memory-open
tool. Stale, deleted, scope-changed, or review-required revisions return per-ref typed
unavailable results and never substitute a newer body.

## Detailed design

### 1. Explicit profile versus learned memory

Backend returns two independent inputs:

- **Explicit profile:** user-authored name, locale/time zone, response
  preferences, and product settings.
- **Learned memory:** accepted records from AR-H6.

`ExplicitAgentProfileSnapshot` is the only runtime profile input. Backend owns its
schema, validation, persistence, revision, deletion/export, and product settings APIs.
AR-F2 owns the typed prompt-fragment projection and cacheability rules; `ai-backend`
does not reinterpret arbitrary settings JSON as prompt text.

Precedence:

```text
system/security/product policy
  > explicit current request
  > explicit user/project settings
  > accepted recalled memory
  > unaccepted conversation inference
```

Conflicts are surfaced; a learned memory never overrides a current explicit
request or profile setting.

### 2. Recall trigger

Recall runs once during request-scoped harness assembly for eligible task
families. It may run again only after a material user-query/project change,
using a new manifest revision. The model cannot issue an unrestricted “show all
memory” internal call; user-facing memory browsing goes through facade.

### 3. Query and ranking

Build the retrieval query from the current user request, task family, explicit
project, and capability intent. Do not include secrets or the full transcript.
Backend applies hard ACL/sensitivity/expiry filters, then lexical/vector
ranking. ai-backend may rerank a small authorized candidate set using
deterministic recency/kind/task features. No LLM reranker in the initial release.

### 4. Context budgeting

AR-F5 assigns a memory budget after current intent and security/policy
fragments. Select diverse, non-contradictory items up to item/token caps.
Include concise title/excerpt, scope, and `[memory:<opaque-ref>]`. Full body or
source span is available through F5's authorized evidence reader only when needed.

### 5. Injection safety

Normalize text and scan for instruction/exfiltration patterns. The model prompt
states that memories are fallible user/project context, not commands; embedded
tool calls, policies, or requests to ignore instructions have no authority.
Blocked items remain visible to the owner in Memory UI with a review warning
but are not injected.

### 6. Explanation and feedback

The run event stream emits a safe `memory.recalled` activity containing manifest id,
memory ids, revision ids/digests, scopes, and reason codes—not titles, excerpts, bodies,
or source labels. The UI hydrates display titles through facade-authorized backend
routes. UI offers:

- open source/provenance;
- dismiss for this run;
- mark incorrect/edit;
- forget/delete;
- stop recalling this kind/scope.

A correction creates a user-authorized AR-H6 update/supersession; it is not
free-form model mutation.

### 7. Touch and outcome

After prompt assembly commits the selected fragment, ai-backend calls backend
`touch` idempotently by `(run_id, memory_id, revision_id)`. AR-F1 may link outcome
metrics to recalled versions. Merely appearing in a candidate set does not mark
use.

## Persistence, retention, deletion, and future sync

The local backend retains canonical memories/profile in H6's embedded database.
ai-backend persists only the bounded recall manifest required for run replay in its
desktop-default `FileRuntimeApiStore`, using opaque refs and prompt-fragment digest; it
never duplicates memory bodies. If a source/item is deleted, H6's normative deletion
matrix applies: review-required or source-deleted memories are excluded from automatic
recall and historical runs show a tombstone. Caches are
account/project/revision/digest keyed and invalidated on update/delete/scope or
source-state change.

Profile updates create a new local backend revision and invalidate F2
fragments/caches. Profile export and field-level deletion use the existing settings
facade; “Delete local data” removes profile snapshots and recall manifests. Historical
runs retain only revision/digest, never a duplicate profile body. A future consumer
sync service may replicate accepted memory/profile revisions, but recall remains
offline-capable and local deletion immediately wins on this device.

## Authorization, privacy, and audit

- Verified run identity supplies all scopes.
- Backend applies ACL before scoring and hydration.
- Private user memory is never available to subagents unless the parent
  context packet explicitly includes an allowed excerpt; children cannot query
  a broader scope.
- Sensitivity policy can exclude records from cloud providers or require an enabled
  local model/BYOK path.
- Audit recall manifest IDs/revisions/reasons/policy, not titles, excerpts, or bodies.
- User opt-out disables retrieval and future touches immediately.

## Performance and capacity

- Recall budget defaults to a small number of items and less than 5% of model
  context; exact values are policy-configurable.
- Loopback backend search p95 target under 100 ms; full assembly overhead under 150 ms.
- Cache only authorized result IDs/excerpts with short TTL and revision/digest key.
- Failure/open circuit skips recall; never retry enough to delay the response.
- Search/rerank is O(log/index retrieval + k log k) on a bounded candidate set.

## Failure, retry, and recovery

- Backend timeout returns an empty manifest with `degraded_reason`.
- Touch failure retries asynchronously and cannot change the answer.
- Item deleted between search and injection is caught by revision/authorization
  hydration; omit it.
- Profile or memory revision/digest change invalidates the assembled fragment and
  requires a bounded re-fetch; no mutable latest-version substitution.
- Duplicate touch is idempotent.
- Prompt scan failure omits the item and emits a safe metric.
- Revocation during a long run prevents subsequent full-body/source fetches.

## Observability and quality

Track recall enabled/disabled, latency, candidates/items/tokens, cache hit,
blocked injection, revision race, backend degradation, “why recalled” opens,
dismiss/correct/forget rates, precision@k, task success delta, and prompt-cache
impact. Evaluate conflicting preferences, stale project facts, sensitive
records, and adversarial memory content.

## Rollout and backout

1. Build internal search client and manifest in shadow; inject nothing.
2. Evaluate with checked-in offline fixtures and opt-in desktop dogfood; tune precision.
3. Show recall previews in UI without model injection.
4. Enable injection for explicit user-selected/pinned memories.
5. Enable ranked automatic recall for opt-in cohorts.

Backout disables injection/search, clears short-lived caches, and preserves
canonical memory and manifests. Existing runs replay with tombstones/refs.

## Implementation slices

1. Internal backend client/contracts and identity propagation.
2. Profile/memory precedence resolver.
3. Recall query/ranker and policy.
4. AR-F5 prompt fragment/injection scan.
5. Manifest/event/touch attribution.
6. Shared explanation/feedback UI.
7. Cache invalidation and deletion behavior.
8. AR-F1 corpus and staged rollout.

## Test plan

- Full local-account/personal/project/sensitivity authorization matrix.
- Forged renderer/body identity is rejected; verified loopback service/session identity
  and project selection are authoritative.
- Explicit profile/current request conflicts with learned memory.
- Prompt injection/invisible Unicode/malicious tool instruction fixtures.
- Token/item budget and diversity selection.
- Backend timeout, deletion/revision race, cache invalidation.
- Subagent capability/context narrowing.
- Idempotent touch and outcome attribution.
- User opt-out/dismiss/correct/forget and account deletion.
- Prompt cache stability with unchanged profile/memory revisions.
- Exact profile snapshot, source-state, memory revision/digest, F5 evidence-open byte
  caps, stale/deleted refs, and content-free event projection.

## Definition of done

- [ ] Runs recall a bounded, authorized, accepted set or degrade cleanly.
- [ ] Explicit profile/request precedence is deterministic.
- [ ] Every recall is explainable, correctable, and deletable.
- [ ] Memory never widens tools, policy, account, or project scope.
- [ ] Run replay and deletion semantics are defined and tested.
- [ ] AR-F1 quality, latency, safety, and cache gates pass.
- [ ] Shared program DoD passes.

## Guardrails

- Memory is context, never authority.
- Never inject pending/unreviewed candidates.
- Never send all memory to the model.
- Never use vector similarity as an ACL decision.
- Never duplicate canonical memory bodies in ai-backend persistence.

## Open decisions

1. Which explicit profile fields are allowed in every run versus task-specific.
2. Default automatic recall opt-in during desktop onboarding.
3. Whether project memories are recalled outside their project by explicit user action.

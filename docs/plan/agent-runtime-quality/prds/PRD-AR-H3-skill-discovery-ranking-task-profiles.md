# PRD-AR-H3 — Skill discovery, ranking, and task profiles

**Goal.** Select a small, policy-valid set of published skills for each task, expose
compact cards first and full instructions only on demand, and measure whether selection
improves task quality without injecting every enabled skill into every prompt.

| Field             | Value                                                                                                                                              |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| Status            | Draft for review                                                                                                                                   |
| Primary owners    | `backend` skill catalog/index; `ai-backend` runtime selector                                                                                       |
| Public API impact | Additive skill search/profile administration                                                                                                       |
| Runtime rollout   | `SKILL_SELECTION_MODE`: all_cards → shadow_ranked → ranked                                                                                         |
| Depends on        | AR-H2 publication/revisions, A3 Operation Gateway, D2 built-ins/subagents, E1 accountability/lifecycle, AR-F1 evaluation, AR-F5 context allocation |

## Implementer brief

Read before implementation:

1. `../README.md`.
2. `PRD-AR-H2-skill-draft-review-publish-rollback.md`.
3. `../../prds/PRD-A3-operation-gateway.md`.
4. `../../prds/PRD-D2-builtins-subagents.md`.
5. `../../prds/PRD-E1-accountability-lifecycle.md`.
6. `services/backend/src/backend_app/service.py` (`SkillRegistryService` and
   `ToolCatalogService`).
7. `services/backend/src/backend_app/store.py` skill adapters.
8. `services/backend/src/backend_app/agents/store.py` and
   `services/backend/src/backend_app/agents/service.py`.
9. `services/backend/src/backend_app/tools/service.py` and
   `services/backend/src/backend_app/tools/schema.sql`.
10. `services/backend/src/backend_app/app.py` internal skill-card/bundle routes.
11. `services/ai-backend/src/agent_runtime/capabilities/skills/virtual.py`.
12. `services/ai-backend/src/agent_runtime/capabilities/skills/policy.py`.
13. `services/ai-backend/src/agent_runtime/capabilities/skills/middleware.py`.
14. `services/ai-backend/src/agent_runtime/execution/factory.py`
    (`_skill_cards` and `_instructions_with_skill_cards`).
15. `services/ai-backend/src/agent_runtime/capabilities/tools/builtin/load_tool.py`.
16. `services/ai-backend/src/agent_runtime/capabilities/operations/gateway.py`.
17. `packages/api-types/src/skills.ts` and `packages/api-types/src/agents.ts`.

H2's active revision is the only selectable revision. This PRD must not create an
alternative publication or enablement state.

## Problem statement

Runtime assembly currently fetches every enabled visible skill card, renders every card
into the system prompt, and lets the model load a full bundle by exact name. As catalogs
grow, prompt cost becomes proportional to all visible skills even when most are
irrelevant. Large undifferentiated menus also increase wrong-skill selection and make
subagent capability narrowing harder to reason about.

Exact-name loading is useful after selection but cannot discover a skill when the user
describes a task in different words. Conversely, semantic ranking alone must not decide
authorization, publication, compatibility, or tool access.

## Current implementation and predecessor contracts

- **[shipped]** Backend cards are compact and full markdown loads on demand.
- **[shipped]** Runtime caches cards/bundles per registry instance and rejects duplicate names.
- **[shipped]** Skill policy already checks source, agent type, deny list, and allowed-tool subset.
- **[depends on]** H2 supplies immutable active revisions, digests, and catalog generation.
- **[shipped]** Agents already carry configured skill ids and permission/capability constraints.
- **[depends on]** A3/D2 provide model-visible tool inventory, operation events, subagent narrowing, and
  usage attribution.

## Objectives

1. Filter by authorization/policy before ranking.
2. Rank published compatible skills against the task and active task profile.
3. Inject only bounded top candidates; load full markdown only on explicit selection.
4. Support explicit skill selection that outranks inferred ranking but not policy.
5. Pin candidate cards and bundles to H2 active revisions for the run.
6. Evaluate top-k recall, wrong-skill activation, task quality, latency, and token cost.

### Success measures

- At least 95% top-5 recall for tasks with a relevant skill in the checked-in suite.
- Fewer than 2% irrelevant full-skill loads on tasks where no skill is needed.
- At least 60% reduction in skill-card prompt tokens at 100 visible skills.
- Selection service p95 below 150 ms lexical/filter-only and 400 ms hybrid at 10,000
  tenant skills.
- Zero unpublished/disabled/incompatible/unauthorized candidate in runtime tests.

## Non-goals

- Publishing, editing, importing, enabling, or granting skills.
- Letting rank score override policy or requested explicit deny.
- Auto-running a skill merely because it ranked highly.
- Training provider models on tenant task/skill text.
- Replacing the MCP/tool catalog; skills remain instruction workflows, not executable
  tool providers.

## Interfaces consumed

- H2 active revision/card/bundle/catalog-generation contracts.
- Existing skill access policy and agent definitions.
- A3 operation descriptors and D2 runtime/subagent capability snapshots.
- F5 context allocation for compact card and loaded-bundle budgets.
- Existing embeddings provider only when tenant policy enables semantic ranking.
- E1 usage, audit, retention, and metrics.

## Interfaces exposed

### Task profile

```text
SkillTaskProfile
  profile_id: string
  org_id: string
  owner_kind: org_default | agent | routine | project | user
  owner_id?: string
  version: int
  allowed_skill_ids?: string[]
  denied_skill_ids: string[]
  required_tags: string[]
  preferred_tags: string[]
  allowed_tool_categories: string[]
  max_candidates: int                    # default 8, max 20
  max_card_tokens: int                   # default 1,200, max 2,500
  semantic_ranking: disabled | allowed
  created_at, updated_at
```

Profiles narrow the verified runtime capability snapshot. Profile composition is
intersection for allowlists/permissions, union for denies, and most restrictive for
budgets. A user/task cannot widen an org/agent profile.

### Selection request/response

```text
SkillSelectionRequest
  task_summary: string                   # bounded, transient
  explicit_skill_ids: string[]
  agent_id?: string
  project_id?: string
  routine_id?: string
  available_tool_names: string[]
  agent_type: main_agent | subagent
  max_candidates?: int

SkillCandidate
  skill_id: string
  active_revision_id: string
  content_digest: sha256
  name, display_name, description
  allowed_tools: string[]
  compatibility: string[]
  reason_codes: string[]                 # exact_match, tag, lexical, semantic, explicit
  score_bucket: high | medium | low      # raw score not model-visible

SkillSelectionResponse
  selection_id: string
  catalog_generation: int
  profile_versions: string[]
  candidates: SkillCandidate[]
  truncated: bool
```

### Private backend APIs

```text
POST /internal/v1/skills/select
GET  /internal/v1/skills/revisions/{revision_id}/bundle
```

They require service token and verified org/user headers. Selection body contains no
tenant/user authority.

### Public profile APIs

```text
GET  /v1/skill-task-profiles
POST /v1/skill-task-profiles
PUT  /v1/skill-task-profiles/{profile_id}
DELETE /v1/skill-task-profiles/{profile_id}
```

Profile administration flows through the facade. Agent/routine editors may use an
owner-scoped projection rather than a second contract.

### Model-visible tools

The normal run begins with selected compact cards. When the task materially changes or
no candidate fits, the model may call:

```text
search_skills(query, required_tool_names?, limit?)
load_skill(skill_id, active_revision_id)
```

`search_skills` searches only the already authorized active catalog under the same
profile. `load_skill` requires a candidate/pinned revision from the run selection; name
alone is no longer sufficient for an ambiguous catalog.

### Events

```text
skill.selection.completed.v1
skill.selection.refreshed.v1
skill.bundle.loaded.v1
skill.selection.rejected.v1
```

Events include selection/profile/catalog/revision ids, reason codes, counts, timings,
and token estimates. Task text, descriptions, markdown, and raw scores are absent.

## Design

### D1. Filter before rank

Candidate eligibility is evaluated in this order:

1. canonical H2 lifecycle state is `active` with an active published revision;
2. tenant/user/org visibility;
3. composed task-profile allow/deny policy;
4. agent/subagent allowed skill ids;
5. compatibility with runtime/provider/platform;
6. skill `allowed_tools` is a subset of the run's currently authorized tools;
7. package/revision is not revoked;
8. remaining candidates enter ranking.

`disabled`, `archived`, `review_required`, and `deleted` rows are excluded before
ranking. H3 owns no parallel availability marker and cannot clear an H2 lifecycle state.

No post-ranking filter may hide an unauthorized hit that already influenced scores or
model-visible counts.

### D2. Ranking

Initial score combines deterministic features:

- explicit user/agent selection;
- exact normalized name/alias;
- required/preferred tag match;
- BM25/lexical match over name, description, reviewed tags, compatibility, and a
  bounded publisher-authored search synopsis;
- optional semantic similarity when tenant policy and embedding availability permit;
- recent successful-use prior with bounded weight and minimum sample size;
- penalties for repeated irrelevant loads or incompatible tool requirements.

Explicit selection receives highest ranking but still passes every eligibility gate.
No feature uses secret content, review comments, full markdown, or another tenant's
behavior.

The service returns reason codes and score buckets, not a misleading exact probability.
Tie-breaking is deterministic by stable name/revision id.

### D3. Index

Backend owns a tenant-scoped `skill_search_documents` projection:

```text
skill_id, active_revision_id, catalog_generation
name, display_name, description, tags, compatibility, allowed_tools
search_text, search_tsv, embedding_ref?, updated_at
```

H2 publication/rollback/disable updates the projection through a durable outbox. Search
filters scope/profile/tool constraints before ranking. In-memory adapter uses a bounded
scan for tests; Postgres uses GIN and optional vector index.

Index is derivative and rebuildable from active revisions. It never stores full
markdown.

### D4. Prompt budget

Runtime injects at most eight cards and 1,200 estimated tokens by default. Each card
contains stable id/name, one-line description, pinned revision, required tool names, and
why it matched. Cards are sorted deterministically.

If no candidate meets minimum relevance, no card block is injected. The
`search_skills` bridge remains available at a small fixed schema cost.

For `N` visible skills with total card bytes `S`, existing assembly costs `Θ(S)` per
model request. Ranked assembly costs approximately:

```text
search O(index query + k log k) + prompt O(min(k, budget))
```

with bounded `k`; optional full bundle cost is paid only for selected skills.

### D5. Task summary

`ai-backend` derives a bounded selection query from the explicit user request, agent
definition, project/routine context, and requested output—not retrieved page/tool text.
It does not use an extra model call in the default path.

The task summary is sent to backend over private HTTP, never logged, and discarded
after selection. Dynamic task changes may refresh once when the agent can articulate a
new query; refresh is budgeted and cannot widen profile/capability scope.

### D6. Bundle pinning

Selection returns active revision ids. `load_skill` requests that exact revision.
Publication after run start changes future selections but not this run. Disabled/revoked
skills fail new loads; already loaded text cannot confer authority, and external tool
operations continue to recheck policy.

Bundle cache key is `(org,user visibility,skill_id,revision_id,content_digest)`, never
name alone.

### D7. Subagents

Subagent selection starts from:

```text
parent eligible set ∩ subagent definition ∩ delegated task profile ∩ child tools
```

It may be ranked separately against the delegated task summary. The child cannot search
outside the parent eligible set or load an unpinned revision. Selection/load operations
join the D2 operation tree and usage attribution.

### D8. Learning signals

Record content-free outcome edges:

```text
selection_id, skill_id, revision_id, run/task id
selected, loaded, completed, user_rejected?, evaluator_outcome?
```

These signals support offline evaluation and a bounded successful-use feature. They do
not rewrite skill content or publish changes. Sparse/negative feedback cannot
immediately suppress an org-admin-required skill.

### D9. Cache and freshness

- Selection cache key: org/user visibility, normalized task digest, composed profile
  versions, tool-set digest, agent type, catalog generation, ranking version.
- TTL maximum five minutes; catalog/profile generation invalidates immediately.
- Negative results are cached for the same generation.
- `ai-backend` keeps only run-scoped pinned selections and bundle cache.
- Shadow ranking uses the same catalog query and does not add an embedding/model call
  unless explicitly enabled for evaluation.

## Persistence, retention, and deletion

- Task profiles are canonical backend product records with soft delete, audit, and
  owner lifecycle.
- Search documents/embeddings and selection caches are derivative and rebuildable.
- Run selection/load events follow run retention and user-history deletion.
- Outcome edges retain ids/reason codes, not request text/skill markdown; they follow
  usage retention and legal hold.
- Skill deletion/disable/revocation invalidates index/cache; old run attribution remains
  digest-pinned but cannot reopen deleted content without authorization/retention.
- Account/org deletion covers profiles, search projection, embeddings, caches, and
  outcome edges subject to hold.

## Authorization, privacy, and security

- Backend derives tenant/user from service/session identity.
- Org/default/agent/routine/project profile mutation uses owner-specific authorization.
- Selection eligibility is fail-closed; unknown compatibility/tool ids are excluded.
- Skill descriptions/search text are untrusted publisher content and cannot change
  rank policy, prompt priority, or tool grants.
- Task text, skill markdown, ids, and raw scores never enter logs/metric labels.
- Embeddings use the configured tenant data policy/provider; semantic ranking disables
  cleanly when unavailable or disallowed.
- Cache keys include visibility/profile/tool/catalog generations to prevent scope reuse.

## Performance and capacity

- Task query max 2,000 chars; candidates default 8/max 20; card tokens default 1,200/
  hard 2,500.
- Backend selection deadline 750 ms, target p95 150 ms lexical and 400 ms hybrid.
- Bundle load deadline 2 seconds and body limits from H2/H1.
- At 10,000 active skills/tenant, Postgres must use tenant-leading filter/index plans;
  no Python full scan.
- Per-run selection refresh maximum 2; bundle loads remain under tool budget.
- Process card/bundle caches are bounded LRU by bytes and generation.

## Failure, idempotency, and recovery

- Selection/search/load are read-idempotent and carry deterministic operation ids.
- Index lag beyond threshold returns a warning and may fall back to direct bounded
  active-card filtering for small catalogs; it never returns stale disabled revisions.
- Embedding/reranker failure degrades to lexical.
- Backend unavailable omits inferred skills; an explicitly selected cached pinned
  revision may load only if current policy can be revalidated.
- Partial provider/profile failure uses the most restrictive resolved policy.
- Cancellation prevents late cards/bundles from entering the graph.
- Replay uses persisted pinned selection/bundle refs and performs no new ranking.

## Metrics

- `skill_selection_total{mode,outcome}`
- `skill_selection_duration_ms{strategy}`
- `skill_selection_candidates{stage=eligible|returned}`
- `skill_selection_card_tokens`
- `skill_bundle_load_total{outcome}`
- `skill_selection_refresh_total{reason}`
- `skill_selection_topk_recall`
- `skill_selection_irrelevant_load_rate`
- `skill_selection_task_success_delta`
- `skill_selection_index_lag_seconds`

Metrics are low-cardinality and contain no tenant/user/skill/task identifiers.

## Rollout and backout

1. Land profile/index/contracts and golden fixtures dark.
2. Backfill search projection from H2 active revisions.
3. Run `shadow_ranked` while injecting all cards; compare chosen/loaded skills and
   offline task results without changing prompts.
4. Enable ranked cards for internal tenants with lexical-only strategy.
5. Enable semantic ranking and outcome prior independently by tenant policy.
6. Retire all-card injection after token/quality/authorization gates.

Backout returns to `all_cards` while H2 active revisions remain the source of truth.
Profiles/index/outcome edges stay intact. If all-card mode would exceed a hard prompt
budget, safe backout is “no inferred cards plus explicit load,” not unbounded injection.

## Implementation slices

1. Define profile/selection/contracts, ranking version, fixtures, and API types.
2. Add profile stores/routes/auth/audit.
3. Add search projection/outbox/rebuild and lexical ranker.
4. Add private selection API and `ai-backend` provider.
5. Pin runtime card/bundle assembly and add `search_skills`/id-based `load_skill`.
6. Add subagent intersection, events, caches, usage/outcome edges.
7. Add shadow comparison, evaluation suite, semantic strategy, metrics, and rollout.

## Test plan

### Eligibility and profiles

- User/org visibility, active/draft/disabled/revoked state, tool subset, compatibility,
  agent ids, profile intersection/deny precedence, explicit selection.
- Cross-tenant/profile forgery and generation-aware cache isolation.

### Ranking and prompt

- Exact name, synonym/description/tag, no-skill, ambiguous, conflicting, tool-dependent,
  and semantic-unavailable tasks.
- Deterministic ties/reasons, top-k/token truncation, no full markdown in cards.

### Runtime and subagents

- Pinned revision across publish/rollback, disable/revoke during run, bundle cache key,
  refresh budget, child subset, operation tree/usage.
- Replay does not rerank; cancellation emits no late capabilities.

### Lifecycle and recovery

- Projection outbox retry/rebuild/index lag, profile/skill deletion, account/org
  deletion, hold, cache invalidation, backend failure.

### Evaluation and performance

- Top-k recall, irrelevant loads, answer/task correctness, tool calls, prompt tokens,
  time-to-first-model, total latency, user correction.
- 10,000-skill query plan/load test and bounded cache/concurrency.

## Definition of done

- [ ] Eligibility filters run before ranking and exclude every unauthorized state.
- [ ] Runtime receives a bounded set of pinned compact cards and loads full revisions
      only on demand.
- [ ] Task profiles compose by least privilege and cannot widen capabilities.
- [ ] Subagents inherit a strict subset of parent skill eligibility.
- [ ] Search/index/cache/lifecycle behavior is deterministic, tenant-safe, and
      recoverable.
- [ ] Ranked mode meets recall, irrelevant-load, token, latency, and task-quality gates.
- [ ] All-card injection is retired or retained only as a bounded emergency mode.

## Guardrails

- Ranking never grants visibility, publication, compatibility, or tools.
- No draft/disabled/revoked skill in candidates.
- No full catalog or full skill bodies in the system prompt.
- No cross-tenant behavioral ranking features.
- No automatic execution solely from a rank score.

## Open decisions

- Initial semantic embedding provider/model and tenant disclosure.
- Whether project profiles ship in the first release or follow org/agent/routine
  profiles.

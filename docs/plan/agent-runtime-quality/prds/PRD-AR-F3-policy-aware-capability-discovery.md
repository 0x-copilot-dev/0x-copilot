# PRD-AR-F3 — Policy-aware capability discovery

**Status:** proposed\
**Priority:** P1\
**Owners:** AI Runtime, Connector Platform, Security\
**Depends on:** [A3 operation gateway](../../generative-surfaces-v2-1/prds/PRD-A3-operation-gateway.md),
[D1 MCP convergence](../../generative-surfaces-v2-1/prds/PRD-D1-mcp-convergence.md),
[D2 built-ins/subagents](../../generative-surfaces-v2-1/prds/PRD-D2-builtins-subagents.md), and
[F1 evaluation](PRD-AR-F1-harness-observability-evaluation-promotion.md)

## Goal

Let the model find and invoke the right authorized capability without placing every
connector schema in every model request. Preserve exact call-time authorization,
approval, budget, citation, redaction, receipt, and audit behavior.

## Implementer brief

Read:

1. `services/ai-backend/src/agent_runtime/execution/factory.py`.
2. `services/ai-backend/src/agent_runtime/capabilities/mcp/loader.py`.
3. `services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/`.
4. `services/ai-backend/src/agent_runtime/capabilities/mcp/annotations.py`.
5. `services/ai-backend/src/agent_runtime/capabilities/tools/`.
6. `services/ai-backend/src/agent_runtime/capabilities/skills/virtual.py`.
7. A3, D1, and D2.

The current server-level `load MCP server` and generic call path is a useful disclosure
boundary. Retain it as direct/server mode. This PRD introduces a tool-level discovery
mode for large authorized catalogs; it does not replace the Operation Gateway.

## Desktop-first deployment and storage contract

The launch composition is the shipped `single_user_desktop` profile: Electron main supervises `backend`, `ai-backend`, `backend-facade`, and the local data services on loopback; the renderer still calls only the facade. This is a local application boundary, not a mandatory cloud dependency.

- Runtime-owned run/event/citation/artifact state must use existing `RuntimePorts`. Desktop defaults to the single-writer file adapter under `<userData>/agent-data/v1` with the in-process worker; tests use in-memory and future hosted deployments may use Postgres.
- Product configuration already owned by `backend` may use its existing embedded local Postgres database. This PRD must not add another database, daemon, queue, or network hop merely for desktop.
- Authorization is expressed as the signed-in local user, device capability grants, project/conversation scope, and provider credentials. Existing internal organization identifiers remain compatibility partition keys; they are not exposed as B2C team or administrator concepts.
- A future consumer web or opt-in sync product may add remote adapters behind the same ports and contracts. Sync, team administration, fleet policy, and always-online availability are not desktop launch dependencies.
- Feature flags resolve locally, work offline wherever no external provider is intrinsically required, and include a bounded disk/CPU/memory budget plus an immediate local backout path.

## Problem and current strengths

The model initially sees compact connector/server cards and can load one authorized
server before calling its tools. This prevents an install-wide tool dump and keeps
live permission checks in the loader. A single server can nevertheless expose hundreds
of verbose, similarly named schemas. Loading all of them consumes context and impairs
selection.

The runtime needs an authorized catalog protocol that can search compact metadata,
describe one capability, and invoke it through the existing execution plane. Generic
LLM tool selection is insufficient because catalog membership and invocation authority
must be profile- and run-specific.

## Objectives

1. Build a per-run two-tier catalog from authorized compact server cards plus
   authorized built-in and cached capability descriptors.
2. Keep core/high-frequency tools directly visible.
3. Activate deferred discovery only when measured schema pressure justifies it.
4. Return a small deterministic ranked candidate set and one bounded full schema.
5. Re-resolve the real capability and policy at invocation time.
6. Preserve underlying capability identity in events, approvals, citations, and audit.
7. Evaluate selection recall, added model turns, and schema tokens against direct mode.

## Non-goals

- Indexing unauthorized or install-global capabilities for the model.
- Granting access because search returned a capability.
- Replacing MCP discovery/session caching; F8 owns control-plane lifecycle.
- Selecting skills, documents, or conversations.
- Hiding side-effect/approval classification from the model or user.
- Using semantic retrieval as an authorization decision.

## Interfaces consumed

- A3/D1/D2 `CapabilityDescriptor`, effect class, approval policy, connector identity,
  operation adapter, and current authorization result.
- Current authorized MCP server cards, loaded descriptor revisions, and the F8
  on-demand discovery/revision contract.
- Model context length/provider cache metadata for activation.
- E1 usage/audit and F1 evaluation projection.

## Interfaces exposed

### Catalog contracts

```text
CapabilityCatalogRevision
  catalog_id, revision, profile_id, user_id
  policy_revision, connector_scope_revision
  descriptor_count, deferred_schema_tokens
  expires_at

CapabilityIndexEntry
  capability_ref
  stable_name
  concise_description
  intent_tags[]
  parameter_names[]
  parameter_types[]
  effect_class
  approval_cue
  connector_label
  descriptor_revision

CapabilityCandidate
  capability_ref
  score
  matched_terms[]
  effect_class
  approval_cue

CapabilityInvocation
  catalog_revision
  capability_ref
  descriptor_revision_seen
  canonical_arguments
  invocation_idempotency_key
```

### Model-facing bridge

```text
search_capabilities(query, limit=5, effect_filter?)
describe_capability(capability_ref)
invoke_capability(capability_ref, arguments, idempotency_key?)
```

The bridge response is structured and bounded. `invoke_capability` unwraps to the real
capability before A3/D1/D2 classification and event emission.

### Ports and events

- `AuthorizedCatalogBuilder.build(run_context)`.
- `CapabilityRanker.search(catalog, query, filters)`.
- `CapabilityDescriptorResolver.resolve(ref, current_context)`.
- `capability.discovery.searched.v1`
- `capability.discovery.described.v1`
- normal underlying `operation.*` events for invocation.

## Detailed design

### 1. Catalog construction

Build only after verified identity, connector scope, role, permissions, and feature
gates are resolved. Each entry is derived from a trusted descriptor, not arbitrary MCP
result text. Remove secrets, examples containing private user data, and verbose schema bodies.

Catalog construction is deterministic for a descriptor/policy revision. Catalog
references are opaque, unguessable, and scoped to the run subject.

Discovery is normatively two-tier:

1. **Server tier.** Every authorized MCP registration contributes a compact server card
   from backend metadata without opening a session or calling `list_tools`. Built-ins
   and already-cached MCP descriptors contribute capability cards immediately.
2. **Capability tier.** When a query cannot be satisfied confidently from current
   capability cards, the discovery bridge ranks server cards and expands at most the
   configured top `K` servers through the existing D1 loader and F8 cache. Safe,
   independent loads may run concurrently. The bridge then performs tool-level ranking
   over the expanded descriptors and returns bounded results in the same tool response.

The first release defaults to `K <= 3`, a per-server timeout, and a total discovery
deadline. It never eagerly connects to every authorized server. A cached descriptor
revision avoids the connection/list step; an F8 invalidation removes both the
descriptor entry and derived catalog results. Failure to expand one server produces a
bounded partial result with an explicit unavailable reason, not an authorization
bypass.

### 2. Activation policy

Modes:

- `direct`: all selected schemas are directly model-visible;
- `server`: current server-card/load behavior;
- `deferred`: bridge plus compact authorized index;
- `shadow`: model uses direct/server mode while the ranker records candidates.

Activation considers deferred schema tokens, model context, expected task turns,
provider cache support, server count, and configured latency ceiling. Initial automatic
threshold: deferred schemas at least 10% of model context, subject to F1 validation.
One-shot latency-sensitive tasks may remain direct.

### 3. Ranking

Version one uses deterministic lexical ranking over normalized name, description,
intent tags, and parameter names. Exact name and intent matches receive explicit
boosts. Results are stable for identical query/catalog revisions.

Semantic reranking is an optional second stage over the top lexical candidates. It may
improve ordering but cannot add candidates, broaden scope, or bypass deterministic
filters.

### 4. Describe

`describe_capability` returns:

- exact current input schema, bounded to configured bytes;
- concise purpose and result type;
- effect class and approval behavior;
- required connector/auth state;
- descriptor and catalog revision.

Oversized descriptions reference a retrievable schema artifact. The model never
receives credentials or private examples.

### 5. Invoke and revalidation

Invocation does not trust the earlier search/describe result. It:

1. resolves the opaque ref within the run catalog;
2. re-fetches the current descriptor and connector scope;
3. rejects stale revisions when arguments may no longer validate;
4. canonicalizes and validates arguments;
5. enters A3 and the designated D1/D2 adapter;
6. preserves the real capability ID in all downstream records.

Bridge invocation cannot call another bridge recursively.

## Security, local-profile boundaries, privacy, and audit

- Catalog builders accept only server-derived `RuntimeContext` identity.
- Search results cannot reveal unauthorized tool names, servers, descriptions, or
  existence.
- Catalog and descriptor caches are keyed by profile, user, policy, connector scope, and
  revision; entries are defensive copies.
- Call-time permission checks are mandatory even after a catalog hit.
- Effectful/unknown capabilities retain exact staging and approval behavior.
- Queries/candidates/chosen refs may be audited; raw arguments remain behind protected
  operation refs.
- Catalog deletion/invalidation follows connector revocation and conversation/run
  retention.

## Performance and complexity budgets

Let `N` be authorized entries and `S` total descriptor bytes:

- catalog creation is `O(S)` and must not duplicate full schemas in the index;
- server-tier search is `O(M)`, where `M` is authorized server cards;
- an uncached expansion performs at most `K` remote discoveries with wall time bounded
  by the slowest admitted discovery plus bridge overhead, rather than their sum;
- lexical search is `O(N)` for the initial implementation, with p95 below 10 ms at
  1,000 entries;
- index size target below 5% of deferred full-schema tokens;
- search returns at most 10 candidates; default 5;
- describe returns at most 16 KiB inline;
- activation must record schema tokens avoided and extra model turns.

The primary success metric is task success at lower prompt load, not local algorithmic
complexity alone.

## Failure, idempotency, and recovery

- Catalog build failure falls back to server/direct mode if safe; otherwise the affected
  capabilities remain unavailable with an explicit reason.
- Search is read-only and idempotent for query/catalog revision.
- Invocation uses the underlying operation idempotency contract.
- Stale descriptor revision returns a structured `catalog_stale` result and triggers one
  authorized rebuild; it never silently coerces arguments.
- Ranker failure cannot widen candidates.
- A process restart rebuilds catalogs from source descriptors; no correctness depends
  on in-memory state.

## Observability and quality gates

Record:

- authorized entry count and full/index schema tokens;
- selected mode and activation reason;
- search query digest, candidate refs/scores, chosen ref;
- search/describe added model turns and latency;
- intended capability recall@k in F1 cases;
- wrong-call, invalid-argument, stale-ref, unauthorized-attempt, and user-correction
  rates;
- end-to-end success/cost versus direct/server controls.

No rollout advances if unauthorized discovery is nonzero or protected-task success
falls below threshold.

## Rollout and backout

1. Build catalog and ranker in shadow; model continues existing mode.
2. Compare intended capability recall on synthetic and internal tasks.
3. Enable deferred mode for read-only capabilities on selected large catalogs.
4. Add describe/invoke for reversible reads.
5. Expand to effectful capabilities while retaining normal staging.
6. Enable token-aware automatic mode after threshold evaluation.

Backout switches to server/direct mode. Existing operations and catalogs remain
readable; no connector or authorization state changes.

## Implementation slices

1. Contracts, canonical compact records, and local-profile-scope tests.
2. Catalog builder over built-ins, compact authorized server cards, and cached MCP
   descriptors.
3. Lexical ranker and shadow telemetry.
4. Search/describe bridge tools with bounded top-`K` on-demand server expansion.
5. Invoke resolver through A3/D1/D2.
6. Activation controller and optional semantic reranker.
7. Conformance removal of any bypass path.

## Test plan

- 5-, 100-, and 1,000-tool synthetic catalogs.
- Many-server cold catalog proves only top-`K` servers connect/list, while a warm F8
  revision performs no duplicate discovery.
- Exact intended capability recall and similarly named distractors.
- Unauthorized capability cannot be searched, described, guessed, or invoked.
- Revocation between describe and invoke blocks the call.
- Schema revision between describe and invoke fails safely.
- Effectful invocation stages before dispatch exactly as D1 requires.
- Bridge recursion and forged opaque refs are rejected.
- Random descriptor order produces stable index/search results.
- Ranker failure falls back without authorization expansion.
- Prompt-token and latency load tests across activation modes.

## Definition of done

- Large authorized catalogs can operate without all full schemas in every model call.
- Search, describe, and invoke are scoped to the exact active catalog and underlying
  policy.
- All calls traverse the existing gateway, adapters, approvals, and audit paths.
- Shadow and enabled evaluation reports quantify selection, latency, and token effects.
- Direct/server fallback, revision invalidation, dashboards, and runbook are shipped.

## Guardrails and open decisions

Guardrails:

- Catalog membership is an authorization projection, never a discovery convenience.
- Do not index tool examples containing customer content.
- Do not infer effect class from model-generated text.
- Do not require semantic retrieval for correctness.
- Do not connect/list all MCP servers to construct the server tier.

Open decisions:

1. What automatic activation threshold wins on the first supported models?
2. Should describe history remain in context or be represented by a compact ref?
3. Which direct core capabilities are never deferred?
4. What evaluated value of `K` replaces the conservative first-release ceiling?

# Design — model discovery and catalog-driven reasoning effort

Status: **draft, not approved**
Owner: ai-backend (`agent_runtime.api`) + `packages/api-types` + `packages/chat-surface`

Two defects with one root cause: **we let third-party metadata decide what exists, and we
throw away the metadata we do fetch.** Both surfaced from "why can't I pick
`gpt-5.6-luna` at `xhigh`", but neither is about that model.

---

## Part A — Model discovery

### A.1 The actual defect

`ModelCatalog` is a projection of **models.dev** (LiteLLM as offline fallback), and its
docstring is right to say there is no local per-model inventory to maintain. That was a good
decision and this design keeps it.

The flaw is narrower and more specific:

> **We treat an upstream metadata index as the authority on model _existence_, when it is
> only an authority on model _metadata_.**

`gpt-5.6-luna` shipped on 2026-07-09. Our run path can execute it — `openai` is in the
provider allowlist, the id is one the provider accepts, and a user with an OpenAI key is
entitled to it. It is unselectable purely because a third-party index has not listed it yet.

That is not a Luna problem. **It recurs on every model launch**, it is worst exactly when a
model is newest and most interesting, and the lag is set by someone else's release cadence.
Any fix scoped to Luna is a fix we will apply again next month.

### A.2 The fix: layered resolution, provider-first

Model identity and metadata resolve through an ordered chain, each layer declaring what it
is authoritative for:

| Layer                          | Authoritative for                                                       | Source                                                         |
| ------------------------------ | ----------------------------------------------------------------------- | -------------------------------------------------------------- |
| 1. **Provider live discovery** | **existence + entitlement**                                             | `GET /v1/models` on the provider, with the caller's credential |
| 2. models.dev                  | metadata (pricing, effort ladder, context window, family, release date) | existing `ModelsDevModelSource`                                |
| 3. LiteLLM static              | metadata, offline                                                       | existing `LitellmModelSource`                                  |
| 4. Operator pin                | existence, for private / preview / fine-tuned models                    | deployment config                                              |

**Layer 1 is the whole idea.** Every provider we support (OpenAI, Anthropic, Google,
OpenRouter) exposes a models endpoint. Asking the provider what a credential can reach is:

- **Zero-lag by construction.** A model is listed the moment the provider serves it. Nothing
  to update, ever.
- **Entitlement-aware.** Preview and early-access models appear for users who actually have
  them, and stay hidden for users who don't — which the current design cannot express at all.
- **Still no local inventory.** This is not an overlay or a curated list. It replaces a
  third-party opinion about existence with the provider's own answer.
- **Self-healing.** When models.dev catches up, enrichment appears with no action from us.

models.dev keeps doing what it is genuinely good at — the metadata a `/models` endpoint does
not return.

### A.3 Records carry provenance

`CatalogModelRecord` gains:

- `identity_source` — which layer proved the model exists
- `metadata_source` — which layer supplied the metadata, or `none`
- `metadata_complete: bool` — false when discovered live but not yet enriched

A live-discovered, unenriched model is **selectable with unknown pricing**. That follows the
rule the existing docstring already sets: consumers treat a missing field as "unknown", never
as a default. The picker shows the model; the cost column shows "—".

### A.4 The one genuinely new complexity: per-credential caching

Today's cache is a process-global singleton. Live discovery is **per credential**, so:

- Cache keyed by credential fingerprint (hash, never the key), with a TTL.
- **A discovery result must never be served to a different principal.** One user's
  entitlement list leaking into another's picker would be an access-information leak, and it
  is the single thing most likely to go wrong here.
- Env-key deployments collapse to one cache entry — the common self-host case stays simple.
- Provider rate limits are handled by the TTL plus an explicit refresh, not by polling.

This is the part of Part A that needs a test suite of its own, and it is why this is a design
doc rather than a patch.

### A.5 Default model becomes credential-aware

`settings.py:601` resolves `RUNTIME_DEFAULT_MODEL` with a static fallback of `"gpt-5.4-mini"`
— one global default, independent of which providers are actually credentialed.

Replace the static fallback with a resolution policy: **the highest-ranked configured model
for a credentialed provider**, using the ordering the catalog already computes (latest per
family, newest first). `RUNTIME_DEFAULT_MODEL` stays as an explicit operator override and
still wins when set.

That gives "adding an OpenAI key makes the newest OpenAI model the default" as a _property of
the policy_, rather than as a rule about one model that goes stale on the next release.

## Part B — Catalog-driven reasoning effort

### B.1 The defect chain

models.dev publishes, per model, two **sibling** keys:

```json
"reasoning": true,
"reasoning_options": [
  { "type": "effort", "values": ["none", "low", "medium", "high", "xhigh", "max"] }
]
```

`reasoning` really is a boolean, so `models_dev_source.py:322` reads it correctly — the bug
is that **`reasoning_options` is never read at all.** We fetch the whole document and parse
one bit out of the two keys that describe reasoning.

`reasoning_options` is a _typed option list_, not an effort field: entries also appear with
`type: "budget_tokens"` and `type: "toggle"`. Our `ModelReasoningConfig` already has
`budget_tokens`, `thinking_mode` and `enabled` alongside `effort`
([contracts.py:165](../../../services/ai-backend/src/agent_runtime/execution/contracts.py:165)),
so **the right move is to map the whole option list, not just the effort case.** Doing only
effort would mean revisiting this seam the moment we want Anthropic's thinking budget driven
by the catalog — which is the same feature wearing a different `type`.

We drop it in five places:

1. `models_dev_source.py:322` — parses `reasoning` to a boolean and **ignores the sibling
   `reasoning_options` entirely.**
2. `CatalogModelRecord` has no field to carry the option list.
3. `ModelReasoningHints.effort` is typed `"low" | "medium" | "high"` — the wire contract
   **cannot express `xhigh`**, `max`, or `none`, even though the backend's
   `ModelReasoningEffort` has all six ([contracts.py:140](../../../services/ai-backend/src/agent_runtime/execution/contracts.py:140)).
4. Frontend `ThinkingDepth` is a hardcoded 3-value union.
5. There are two unreconciled frontend depth models, and the code says not to fork them.

### B.2 The architectural knot: depth means two things

`ReasoningDepth` currently controls **both** how hard the model thinks **and** how much
resource the run gets. `DepthBudgetTable.apply`
([depth.py:127](../../../services/ai-backend/src/agent_runtime/execution/depth.py:127))
multiplies timeout ×0.5/1/2, tool budget ×0.5/1/2, and max-output ×0.6/1/1.5 off the same
three-value enum.

**A six-level catalog-driven ladder cannot drive a three-level multiplier table.** So a
catalog-driven effort selector is not a widening of the enum — it requires splitting the
concept:

| Concept            | Vocabulary                                         | Driven by                                                    |
| ------------------ | -------------------------------------------------- | ------------------------------------------------------------ |
| `reasoning_effort` | per-model, from the catalog                        | user selection, validated against the model's advertised set |
| `resource_profile` | fixed 3-level (timeout / tool budget / max output) | independent, defaults sensibly per effort                    |

This split **is** the deferred "Phase 3E reconciliation" the frontend comment refers to.
Doing Part B properly is what retires that debt rather than adding to it.

### B.3 Validation belongs at the seam, not the UI

The model's advertised effort set is **untrusted upstream data** (per the service's own
untrusted-input rule — MCP descriptors and catalog payloads both qualify). So:

- The UI renders the advertised set.
- The **run path independently validates** the requested effort against the resolved model,
  and falls back to the provider default rather than failing the run when a stale catalog
  advertises an effort the provider rejects.

A picker that offers an effort the run path then refuses is the same class of bug as the
OpenRouter "your key" badge that lied — the docstring in `model_catalog.py` already treats
that as the failure to design against.

## Implementation plan

Ordered so each phase is independently shippable and independently useful.

### Phase 1 — Stop discarding the ladder (small, unblocks everything)

1. `models_dev_source.py` — parse the sibling `reasoning_options` array into a typed,
   validated option list. Unknown `type` values are **skipped, not fatal** — models.dev will
   add option types we have never heard of, and an unknown entry must not cost us the model.
2. `CatalogModelRecord` — add `reasoning_options: tuple[ReasoningOption, ...]`, typed per
   option kind (`effort` carries `values`; `budget_tokens` carries a range).
3. `ModelReasoningHints.effort` — widen to the full six-value ladder; add
   `reasoning_options?: ModelReasoningOption[]`. **Wire-compatible**: widening a union and
   adding an optional field breaks no existing consumer.
4. `ModelCatalog.build` — pass it through.

**Effect:** `xhigh` becomes expressible end to end for any catalogued model, and Anthropic's
thinking budget arrives through the same seam for free. No UI yet.
**Effort:** S · **Risk:** low

### Phase 2 — Split effort from resource profile

5. Introduce `reasoning_effort` as a first-class run field alongside the existing
   `reasoning_depth`, which stays as the resource-profile handle.
6. `DepthBudgetTable` keeps its three-level multipliers, now driven by resource profile.
7. Map legacy `reasoning_depth` → `(effort, profile)` for back-compat, reusing the existing
   `_LEGACY_EFFORT_TO_DEPTH` pattern in `workspace_defaults.py` in the other direction.

**Effort:** M · **Risk:** medium — touches the run-create wire contract

### Phase 3 — Catalog-driven effort control in the UI

8. Replace the hardcoded `ThinkingDepth` list with one rendered from the catalog `effort`
   option,
   falling back to the three-level control when the field is absent (offline/LiteLLM path).
9. Reconcile the two frontend depth models — this is the point at which that becomes
   mandatory rather than deferred.
10. Composer and FTUE both read the same control; they already share the catalog, so this is
    one change, not two.

**Effort:** M–L · **Risk:** medium — the enum reconciliation is the risky half

### Phase 4 — Provider live discovery

11. `ProviderModelSource` per provider, behind the existing `CatalogModelSource` protocol —
    the seam already exists, so this is a new implementation, not a new architecture.
12. Per-credential cache with TTL and strict principal isolation (A.4).
13. Provenance fields and `metadata_complete` (A.3).
14. Credential-aware default-model policy (A.5).

**Effort:** L · **Risk:** medium-high — the cache isolation is the part to get right

**`gpt-5.6-luna` becomes selectable at Phase 4**, as a consequence of the general mechanism
rather than as a special case. Phases 1–3 make `xhigh` work for every catalogued model
including `gpt-5.6-sol`.

## Interaction with the MCPMark programme

The benchmark needs `gpt-5.6-luna` @ `xhigh`, which lands at Phase 4 — later than the
MCPMark Phase 0 sweep wants to start. Two honest options:

- **Run the benchmark on `gpt-5.6-sol`**, which is already catalogued, and which is anyway
  the better-matched tier for a hard long-horizon agentic benchmark. Re-derive the cost table
  ([PRD §3.1a](../mcpmark-optimization/PRD.md)) against Sol's pricing.
- **Or** pin the model for benchmark runs through `RUNTIME_DEFAULT_MODEL`, which bypasses the
  picker entirely — the run path does not require a catalog entry to execute a model. This
  unblocks the sweeps immediately and is independent of all four phases above.

The second is the pragmatic answer for the benchmark; the four phases are the answer for the
product. **They are not the same work and should not be sequenced against each other.**

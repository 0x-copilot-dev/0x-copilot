# Context Occupancy Ledger — Solution Design

**Status:** Implemented, with four corrections made during the build and one
deliverable deliberately unfinished (the §7 SSE producer). This document has been
edited to match what shipped — where the original plan was wrong, the correction
is marked inline rather than quietly rewritten, so the reasoning stays reviewable.
**[STATUS.md](STATUS.md) is the source of truth for what is and is not done.**
**Owner:** ai-backend
**Scope:** `services/ai-backend` only. No frontend work in this document.

## 1. Problem

> **Correction (made during implementation).** An earlier draft of this section
> claimed we could not answer "how full is the window". That was wrong.
> `GET /v1/agent/conversations/{id}/context` already exists and returns
> `ConversationContextResponse`: the model's `context_window_tokens`, the latest
> run's input / output / cached tokens, `available_tokens`, `headroom_pct`, and a
> `ContextBreakdown` by call, by subagent, and by compression event. Audit row U
> below was also wrong for the same reason. The real gap is narrower than the
> draft claimed, and is restated below.

We can already answer **how full** the window is, and how the total splits by
call, by subagent, and by compression event. We can answer what a run cost by
`Purpose`, subagent, and connector.

What no surface can answer is **who filled it** — the decomposition of a single
call's `input_tokens` into the segments that produced it: which system fragment,
which tool's schema, which class of message. Every existing view bottoms out at a
per-call scalar. Occupancy goes one level below that scalar, which is the level
at which the number becomes actionable.

The concrete consequence: `publish_artifact`'s description costs **650 estimated
tokens on every model call of every run**, and nothing in the system reports it —
not because the window is unmeasured, but because the measurement stops one level
above the tool.

### 1.1 Goal

One typed, persisted, streamable record per model call that states: the context
window, what occupied it broken down by segment, what the provider actually
billed, and the honest residual between the two.

### 1.2 Non-goals

- Automatic context pruning or eviction. This ledger informs those decisions; it
  does not make them.
- Replacing `runtime_model_call_usage` or the `Purpose` enum. The single-tracker
  invariant holds — see §6.1.
- Cost/pricing reporting. Occupancy is tokens; `/v1/usage/*` already owns money.
- Any frontend or composer surface. Consumed later via the read API in §7.

## 2. Completeness audit

The critical question is whether we have enumerated everything that can occupy
the window. The first pass had not. This is the corrected inventory.

Every provider request is `system` + `tools[]` + `messages[]` (+
`response_format`). Sources, in the order they land:

| #   | Occupancy source                                                                                                                  | Owner                                       | Measured today                                                                   | Risk if missed                                              |
| --- | --------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- | -------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| A   | 11 typed system fragments                                                                                                         | `prompts/sources.py`                        | ✅ per-fragment, persisted                                                       | —                                                           |
| B   | **Deep Agents system prompts** (`FILESYSTEM_SYSTEM_PROMPT`, `TASK_SYSTEM_PROMPT`, `SKILLS_SYSTEM_PROMPT`, `MEMORY_SYSTEM_PROMPT`) | `deepagents` library                        | ❌ invisible                                                                     | ~2.0–3.3k tok unattributed                                  |
| C   | **Deep Agents tool descriptions** (`TASK_TOOL_DESCRIPTION` 1,644 tok, `READ_FILE` 468, `EXECUTE` 693, `GLOB`/`GREP`/`EDIT`)       | `deepagents` library                        | ❌ invisible                                                                     | largest single block                                        |
| D   | **Per-model harness suffix** (`_anthropic_opus_4_7._SYSTEM_PROMPT_SUFFIX` 537 tok)                                                | `deepagents.profiles`                       | ❌ invisible                                                                     | varies by model — breaks cross-model comparison             |
| E   | Our harness suffix (`WEB_SUBAGENT_CHECKPOINT_SUFFIX`, 801 tok)                                                                    | `deep_agent_builder.py`                     | ⚠️ registered via `HarnessProfile`, outside the plan                             | 801 tok                                                     |
| F   | Runtime tool descriptions (1,794 tok across 9)                                                                                    | `prompts/tools.py`                          | ❌ digest only                                                                   | the headline gap                                            |
| G   | `args_schema` JSON per tool                                                                                                       | pydantic → provider                         | ❌                                                                               | scales with tool count                                      |
| H   | **`_display_title` / `_display_summary` schema fields** on every wrapped tool                                                     | `middleware/display_metadata.py`            | ❌                                                                               | 2 fields × N tools, resident                                |
| I   | MCP server tool descriptors (post `load_mcp_server`)                                                                              | `capabilities/mcp`                          | ❌                                                                               | enters as a **tool result**, not a schema — separate budget |
| J   | User / assistant messages                                                                                                         | conversation store                          | ⚠️ first call only                                                               | dominant bucket                                             |
| K   | Tool results                                                                                                                      | `ToolResultAdmission`                       | ⚠️ bounded, not attributed                                                       | —                                                           |
| L   | **Citation pointer note** appended to every result (`[Tool call #N — …]`)                                                         | `citation_capturing_tool.py`                | ❌                                                                               | ~20–30 tok × every result                                   |
| M   | **Tool-budget note** appended to results (`[Tool budget — …]`)                                                                    | `tool_result_notes.py`                      | ❌                                                                               | ~30 tok × results near cap                                  |
| N   | Offload stubs (preview + opaque ref)                                                                                              | `context/tool_result_admission.py`          | ⚠️ compression happens, saving not reported                                      | can't show "compressed X→Y"                                 |
| O   | Summarization output                                                                                                              | `context/memory/summarization.py`           | ⚠️ metered as `CONTEXT_COMPRESSION`                                              | its _residency_ untracked                                   |
| P   | Deep Agents state (todos, virtual files)                                                                                          | `deepagents` state                          | ❌                                                                               | grows silently                                              |
| Q   | `/subagents/<task_id>/` trace reads                                                                                               | `context/memory/subagent_trace.py`          | ❌                                                                               | on-demand, arrives as a tool result                         |
| R   | **base64 binary file content**                                                                                                    | `capabilities/{workspace,desktop}` backends | ❌                                                                               | ~1.37× bytes, tokenizes terribly                            |
| S   | **Anthropic thinking blocks** echoed into later calls                                                                             | `deep_agent_builder.py:699`                 | ⚠️ `reasoning_tokens` counted at emit; residency in _subsequent_ calls untracked | silent growth                                               |
| T   | `response_format` / structured output schema                                                                                      | `model_invocation/runtime.py:1333`          | ❌                                                                               | small but real                                              |
| U   | Context window denominator                                                                                                        | `pricing/litellm_source.py`                 | ✅ already joined — `ContextWindowSummary` + `available_tokens` + `headroom_pct` | — (draft was wrong; see §1)                                 |

### 2.1 What this changes about the approach

Items B, C, D are the finding that reshapes the design. `PromptAssemblyPlan`
attributes **only what `factory.py` assembles**. `create_deep_agent` then
prepends its own system prompts and tool descriptions — roughly 14.7k tokens of
library-owned text exists in the package, of which the installed subset is
invisible to us.

Enumerating library constants would be brittle and would break on every
`deepagents` bump. It also would not survive `HarnessProfile` exclusions:
`DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES = {edit_file, execute, write_file}`
means **web and desktop have materially different occupancy for the same
conversation**.

This forces the core architectural decision in §3.

## 3. Architecture

### 3.1 Measure the materialized request, not the assembly inputs

Measurement happens at **`ModelInvocationMiddleware.awrap_model_call`** — the
one boundary where a fully-materialized `ModelRequest` exists, after every
library and middleware contribution has landed.

This is load-bearing for three reasons:

1. **It captures B/C/D/E for free.** By the time the request is materialized,
   deepagents has already injected its prompts and tools. We measure the actual
   payload rather than trying to itemize a dependency's internals.
2. **It is topology-correct.** Profile exclusions, gated tools, workspace
   mounts, and desktop-vs-web differences are all already resolved.
3. **It covers subagents.** The AST topology gate
   (`canonical_agent_topology_present`) proves `ModelInvocationMiddleware` is
   installed on both root `middleware` and `universal_middleware_factories`, so
   every child graph call passes the same boundary.

It is also where `UsageMeter` / `MeteredModelInvocation` already sit, so the
snapshot inherits the full `UsageAttributionContext` — `purpose`, `task_id`,
`subagent_slug`, `connector_slug` — with no new plumbing.

### 3.2 Contributors declare; the ledger does not track

Measuring the materialized request gives totals but not names. The naive way to
get names is a central enum of every known contributor, maintained by whoever
owns the ledger. **That design is wrong and we are not doing it.** A central list
is stale the moment someone adds a tool, and it puts the burden on the one team
least able to know what a new contributor is for.

Invert it: **anything that puts text in front of the model declares what it is,
at the point it is composed.** The ledger's job is to collect declarations,
reconcile them against measurement, and fail loudly when they disagree — not to
maintain knowledge of the system.

This is not a new pattern here. The codebase already does it twice:

- `PromptFragmentProviderRegistry` (`prompts/sources.py`) — providers declare
  `source` / `fragment_id` / `tier`, uniqueness is enforced at construction, and
  ordering is deterministic and independent of registration order.
- `test_llm_seam_gate.py` — `canonical_chat_model_call_sites()` returns an AST
  inventory pinned to a literal tuple in a test. **A new LLM call site fails CI
  until someone adds it to the list.** That is the enforcement half, and it is
  what makes the first pattern real rather than aspirational.

So the design is three layers, in dependency order: declare (§4.1), enforce
(§4.2), verify (§4.4). Layer 2 is the one that transfers responsibility.
Declaration without a gate is a comment.

**The honest limit.** Self-declaration cannot be complete on its own, for two
reasons, and the design has to answer both rather than assume them away:

1. **Third parties never declare.** `deepagents` will not implement our
   protocol. Handled by one explicit adapter (§4.3) that declares on its behalf
   and is pinned by a golden fixture — the only place central tracking survives,
   bounded to a single file with a failing test attached.
2. **A declaration is a claim, not a measurement.** A contributor can declare a
   label and then contribute more bytes than it declared. Only measuring the
   materialized request catches that, which is why §3.1 stays.

That is why declarations are reconciled, not trusted (§4.4).

### 3.3 Reconciliation, not fabrication

Two counts exist and they will disagree:

- **Estimated** — decomposable, per segment, approximate.
- **Provider-reported** `input_tokens` — authoritative, a single scalar.

Do **not** scale segments to match the provider total. Across
OpenAI / Anthropic / Gemini / OpenRouter / Ollama that manufactures precision we
do not have. Instead:

```
estimated_input_tokens  = Σ segment.estimated_tokens
provider_input_tokens   = NormalizedTokenUsage.input_tokens   (truth)
unattributed_delta      = provider_input_tokens − estimated_input_tokens
free_tokens             = context_window_tokens − provider_input_tokens
```

`unattributed_delta` is a first-class field, reported, and alertable. It is the
honesty valve: provider-side wire overhead, tokenizer drift, and any occupancy
source this document missed all land there visibly rather than being smeared
across segments.

> **Correction (measured during review).** The next paragraph's closing claim —
> that counting routes to "the provider's real tokenizer where litellm bundles
> one" — is **false for this deployment**. Under this service's
> `apply_offline_litellm_config` guardrail the HF tokenizer downloads are
> disabled, so `gpt-4o-mini`, two Claude slugs and `gemini/gemini-2.0-flash` all
> return the **same** count for the same text: one tiktoken encoder for every
> provider. `counter_source=TOKENIZER` therefore means "counted by a real BPE
> tokenizer", **not** "counted by this provider's tokenizer". A test pins the
> single-encoder equality so a future litellm bump that _does_ bundle
> provider tokenizers fails rather than silently making the claim true.
>
> A second, larger correction: per-segment counting carries a fixed
> per-call envelope of ~7 tokens **per segment**. On a request shaped like §11's
> reference measurements (~81 segments) that is +610 tokens, or **+5.9%** versus
> counting the identical text once — which already exceeds the ±5% bound §9
> proposes below. The bias scales with segment count, not with drift. It is left
> in deliberately: netting it out would move segments toward the provider total,
> which §3.3 forbids. See §9 for what the bound actually became.

Shrink it by counting through the existing `TokenCounterPort` fallback chain
(`litellm.token_counter` → char/4 → window proxy) already used at
`runtime_worker/handlers/run.py:1119`, which routes to the provider's real
tokenizer where litellm bundles one.

### 3.4 Cost control: digest-keyed memoization

Naive per-segment tokenizer calls would be O(segments) per model call. Avoid it
by exploiting digests that already exist:

- System fragments are keyed by `content_digest` — count once per digest, cache
  process-wide.
- The tool block is keyed by `tool_schema_revision` — count once per revision.
  It changes only when the tool surface changes.
- Messages are **append-mostly**: count only messages new since the previous
  call on the same run, carrying forward prior counts.

Steady-state cost per call is therefore proportional to _new_ message content,
not total context. Budget: p95 added latency < 15 ms per model call. Measurement
is best-effort and must never fail a run (§6.4).

## 4. Contracts

New module `agent_runtime/observability/context_occupancy.py`.

### 4.1 The declaration: closed classes, open labels

Exactly one thing stays a closed enum — the structural taxonomy of a provider
request, which genuinely is closed:

```python
class ContextSegmentClass(StrEnum):
    SYSTEM = "system"
    TOOLS = "tools"
    MESSAGES = "messages"
    RESPONSE_FORMAT = "response_format"
```

Labels are **not** an enum. They are owner-namespaced declarations, so ownership
is intrinsic to the label and no central list has to enumerate them:

```python
class ContextLifecycle(StrEnum):
    RESIDENT   = "resident"    # in every call until the surface changes
    PER_TURN   = "per_turn"    # re-sent each turn, varies with state
    PER_RESULT = "per_result"  # rides on each tool result
    ON_DEMAND  = "on_demand"   # only after the model pulls it in


class ContextOrigin(RuntimeContract):
    """One contributor's declaration of what it puts in front of the model."""

    owner: str            # dotted module that owns the text
    name: str             # local label, unique within owner
    segment_class: ContextSegmentClass
    lifecycle: ContextLifecycle
    cache_eligibility: PromptCacheEligibility | None = None
    third_party: bool = False          # see §4.3

    @property
    def label(self) -> str:
        return f"{self.owner}:{self.name}"
```

Declared at the point of composition, next to the thing being contributed:

```python
# capabilities/mcp — a system block
MCP_CARDS_ORIGIN = ContextOrigin(
    owner="agent_runtime.capabilities.mcp",
    name="server_cards",
    segment_class=ContextSegmentClass.SYSTEM,
    lifecycle=ContextLifecycle.PER_TURN,
    cache_eligibility=PromptCacheEligibility.NEVER,
)

# a tool — declared where it is composed into the model surface
declare_context_origin(
    publish_artifact_tool,
    ContextOrigin(
        owner="agent_runtime.capabilities.backends",
        name="publish_artifact",
        segment_class=ContextSegmentClass.TOOLS,
        lifecycle=ContextLifecycle.RESIDENT,
    ),
)
```

`ContextOriginRegistry` mirrors `PromptFragmentProviderRegistry` exactly:
uniqueness enforced at construction, deterministic ordering independent of
registration order, empty registry rejected.

Two consequences worth stating. **`lifecycle` is the field that makes the report
actionable** — `RESIDENT` is rent, `PER_RESULT` is a multiplier on tool-call
count, and they demand different fixes. And the nine existing `PromptSource`
values map 1:1 onto declarations owned by their existing `source_owner` strings,
so the system half needs no new bookkeeping — the declaration already exists in
all but name.

Dynamic contributors declare **per contributor, not per instance**: MCP server
descriptors declare once under
`agent_runtime.capabilities.mcp:server_tool_descriptors`, and instances roll up
under it via `item_count`.

### 4.2 Enforcement: the gate is the point

Declaration is only real if omitting it breaks the build. New module
`agent_runtime/observability/context_origin_conformance.py`, modelled directly
on `llm_seam_conformance.py`:

- `undeclared_context_contributors(src)` — AST sweep returning any tool appended
  to `model_tools` in `_model_visible_tools`, or any block reaching
  `PromptAssemblyInputs`, that is not covered by a `declare_context_origin` /
  registered provider. **Must return `()`.**
- `context_origin_inventory(src)` — sorted `owner:name` inventory, pinned to a
  literal tuple in `tests/unit/test_context_origin_gate.py`, exactly as
  `test_llm_seam_gate.py:26` pins the LLM call sites.

Adding a tool without declaring an origin fails CI with the tool's name in the
diff. Adding one _with_ a declaration fails until the author adds a line to the
golden inventory — which is the moment they consciously accept the context cost.
That review prompt is the actual product of this section.

### 4.3 Third parties: one adapter, pinned

`deepagents` will never implement our protocol. One module declares on its
behalf with `third_party=True`, keyed by the constants it actually installs
(`TASK_TOOL_DESCRIPTION`, `FILESYSTEM_SYSTEM_PROMPT`, the active harness
suffix), resolved through the live `HarnessProfile` so profile exclusions
(`DEEP_AGENT_PROFILE_EXCLUDED_TOOL_NAMES`) are honoured rather than assumed.

A golden fixture pins the measured total per profile. A `deepagents` bump that
adds prompt text fails CI with a diff naming the constant. This is the one place
central tracking survives; it is bounded to a single file, and it has a failing
test attached, which is the difference between a shim and a liability.

### 4.4 Reconciliation: two residuals, two meanings

Declarations are collected, then reconciled against the measured materialized
request (§3.1). The split that falls out of this is the real gain over a central
enum — what would otherwise be one mushy "unknown" becomes two fields with
opposite meanings:

| Field                | Meaning                                    | Expected value | On breach       |
| -------------------- | ------------------------------------------ | -------------- | --------------- |
| `undeclared_tokens`  | measured bytes matching no declaration     | **0**          | contract bug    |
| `unattributed_delta` | provider total − our measured total (§3.3) | small, signed  | tolerance/drift |

`undeclared_tokens > 0` on a first-party path means a contributor broke the
contract and is actionable as a defect. `unattributed_delta` is provider wire
overhead and tokenizer drift — expected, bounded, not a bug. Collapsing these
into one number is exactly the failure mode this design exists to avoid.

### 4.5 Records

```python
class ContextSegment(RuntimeContract):
    segment_class: ContextSegmentClass
    label: str                    # "owner:name" from the declaration, or UNDECLARED
    lifecycle: ContextLifecycle
    third_party: bool = False
    detail: str | None            # tool name, fragment_id, message ordinal range
    byte_count: NonNegativeInt
    estimated_tokens: NonNegativeInt
    item_count: NonNegativeInt = 1
    cache_eligibility: PromptCacheEligibility | None = None
    counter_source: TokenCounterSource       # tokenizer | heuristic | proxy


class ContextOccupancySnapshot(RuntimeContract):
    schema_version: Literal[1] = 1
    model_call_id: str
    assembly_record_id: str | None    # links to PromptAssembledRecord
    attempt_ordinal: PositiveInt = 1  # retries — see §6.3
    graph_scope: GraphScope           # root | subagent  — see §6.2
    provider: str
    model_family: str
    context_window_tokens: NonNegativeInt | None   # None = model not in pricing
    segments: tuple[ContextSegment, ...]
    estimated_input_tokens: NonNegativeInt
    provider_input_tokens: NonNegativeInt | None = None
    cached_input_tokens: NonNegativeInt = 0
    cache_creation_input_tokens: NonNegativeInt = 0
    undeclared_tokens: NonNegativeInt = 0   # §4.4 — expected 0; > 0 is a bug
    unattributed_delta: int = 0       # signed — negative means we over-counted
    free_tokens: int | None = None
```

### 4.6 Message classification

Messages are the one class where the contributor is not always a code path, so
classification is structural — but it resolves to the **same declared origins**,
not to a parallel taxonomy. Every code path that injects into messages declares
like any other contributor:

| Contributor                        | Declared origin                                    | Lifecycle    |
| ---------------------------------- | -------------------------------------------------- | ------------ |
| `citation_capturing_tool.py`       | `agent_runtime.capabilities:citation_pointer_note` | `PER_RESULT` |
| `tool_result_notes.py`             | `agent_runtime.capabilities:tool_budget_note`      | `PER_RESULT` |
| `context/tool_result_admission.py` | `agent_runtime.context:offload_stub`               | `PER_RESULT` |
| `context/memory/summarization.py`  | `agent_runtime.context.memory:summary`             | `PER_TURN`   |
| `context/memory/subagent_trace.py` | `agent_runtime.context.memory:subagent_trace`      | `ON_DEMAND`  |
| workspace / desktop backends       | `agent_runtime.capabilities.workspace:binary_b64`  | `ON_DEMAND`  |

Conversation content has no code-path owner and is declared by the runtime
itself: `agent_runtime.conversation:{user,assistant_text,assistant_tool_calls,assistant_thinking,tool_result}`,
plus `agent_runtime.execution:state_file` for Deep Agents state injection.

Resolution order (first match wins):

1. `content` is base64 / carries `encoding == "base64"` → workspace binary origin
2. summarization output marker → summary origin
3. `ToolMessage` carrying a `ToolResultAdmission` stub → offload-stub origin
4. `ToolMessage`: split the appended note suffixes into their per-result note
   origins, remainder → `tool_result`; a `/subagents/` read → subagent-trace
5. `AIMessage`: thinking blocks → `assistant_thinking`, remainder split
   `assistant_tool_calls` / `assistant_text`
6. Deep Agents state injection → `state_file`
7. `HumanMessage` → `user`

Step 4's note-splitting is what makes audit items L and M visible; both note
formats are single-sourced constants, so the split is exact rather than
regex-guessy. Anything matching no rule is `UNDECLARED` and counts into
`undeclared_tokens` (§4.4) — messages get no silent catch-all bucket either.

## 5. Persistence

A third record in the existing observation family, mirroring how
`PromptCacheObservationInput` already links to the assembled record by
`assembly_record_id`:

- `runtime_context_occupancy` — one row per `(model_call_id, attempt_ordinal)`.
- Rollup columns for query: `estimated_input_tokens`, `provider_input_tokens`,
  `context_window_tokens`, `undeclared_tokens`, `unattributed_delta`,
  `graph_scope`.
- `segments_json` **JSONB** — read whole, never queried per segment. A fan-out
  table would add 30+ rows per model call for no query benefit.
- Additive migration, `schema_version: 1`, all new columns nullable. No
  backfill: pre-migration calls report `null` occupancy, not zero.
- ~~Retention follows the existing `runtime_events` / usage retention policy —
  occupancy rows are deleted by the same conversation-deletion cascade. No new
  retention class.~~ **This was false and is corrected.** On Postgres nothing
  hard-deletes a conversation or a run, and `runtime_events` is erased by an
  explicit `RetentionKind` rather than by a cascade — so there was no cascade to
  inherit. On the **file store** (the desktop default) the conversation purge
  folded sessions, runs, events and the index but never touched occupancy,
  leaking one row per model call permanently. The file-store erasure is fixed;
  the Postgres path needs occupancy added to the explicit retention enumeration,
  which is tracked, not done here. Do not describe occupancy retention as
  "inherited" again without checking the erasure path it claims to inherit from.

Do **not** add columns to `runtime_model_call_usage`. That table is the money
tracker; occupancy is a different lifecycle and a different read pattern.

## 6. Correctness invariants

### 6.1 Single-tracker

This ledger records occupancy only. It never writes a token-usage row, never
extends `Purpose`, and never becomes a second source of billing truth.
`provider_input_tokens` is **copied** from the same `NormalizedTokenUsage` the
`UsageMeter` consumes — read-side denormalization for reconciliation, not a
parallel meter.

### 6.2 Subagent windows are separate

A subagent has its **own** context window. Summing child occupancy into the
parent is wrong and would report >100% utilization.

- `graph_scope` distinguishes `root` from `subagent`.
- Per-call `free_tokens` is computed **within one scope only**.
- Run-level rollups report `max` utilization per scope and **never** sum
  occupancy across scopes.
- Existing `task_id` / `subagent_slug` on the attribution context identify which
  child window a row belongs to.

### 6.3 Retries do not double-count

`MeteredModelInvocation.record_attempt` already meters per attempt. Occupancy
keys on `(model_call_id, attempt_ordinal)` so a retried call produces a second
snapshot rather than overwriting the first, and rollups deduplicate on
`model_call_id` taking the **last** attempt for utilization.

### 6.4 Fail-open

Occupancy measurement is best-effort observability. Every failure path — a
tokenizer raising, a malformed message, a missing pricing row — logs and emits a
partial snapshot with `counter_source = proxy`. It must never raise into the
model call. This mirrors `UsageMeter.record_attempt`'s existing contract.

### 6.5 No content leakage

Segments carry counts and digests only — never content. `detail` is bounded to
safe identifiers (tool name, `fragment_id`, ordinal range). The existing
`observability/redactor.py` `Sensitive` markers apply. This matters because
occupancy is exposed over an HTTP read API in §7.

### 6.6 Cache-awareness

A resident segment that is cached is billed at roughly a tenth of a fresh one.
Segments carry `cache_eligibility` from the existing `PromptFragment` metadata,
and the snapshot carries `cached_input_tokens` so a reader can distinguish
"large but cached" from "large and re-billed every turn". Without this, the
report would recommend trimming the stable prefix — exactly backwards.

## 7. Read and stream API

Occupancy is a **sub-resource of the existing `/context` path**, not a
replacement for it. `GET /v1/agent/conversations/{id}/context` already serves
`ConversationContextResponse` (the window summary and headroom, §1); mounting
occupancy at that same path would have collided with a shipped contract. The
draft of this section proposed exactly that collision — corrected here.

- `GET /v1/agent/runs/{run_id}/context/occupancy` — per-turn series for a run,
  filterable by `graph_scope`.
- `GET /v1/agent/conversations/{conversation_id}/context/occupancy` — latest
  root-scope snapshot, i.e. "who is filling the window right now".
- `context_occupancy` `RuntimeEventEnvelope` on the existing SSE stream so
  consumers update live on the established `sequence_no` contract instead of
  polling. **NOT IMPLEMENTED.** The event type, the payload contract, its
  projector branches and the public TypeScript contract all ship — but nothing
  in the repository ever appends an event of that type. Three independent
  reviewers found this separately. Emitting a run event touches the
  `sequence_no` / causal-prefix seal contract, which is not something to bolt on
  at the end of a build; it is tracked as the one deliberately unfinished
  deliverable in §7. Until a producer exists, the read endpoints above are the
  only way to reach occupancy, and any consumer written against the streamed
  event will silently receive nothing.
- Scope-guarded under `RUNTIME_USE`, same as `/v1/usage/*`. Facade proxies
  `/v1/*`; no `/internal/v1/*` exposure.

## 8. Delivery plan

| PRD | Scope                                                                                               | Depends on | Why this order                                                                                             |
| --- | --------------------------------------------------------------------------------------------------- | ---------- | ---------------------------------------------------------------------------------------------------------- |
| 01  | `ContextOrigin` + `ContextOriginRegistry` + `declare_context_origin`; declare all first-party tools | —          | The contract. Nothing downstream is meaningful without it.                                                 |
| 02  | **Conformance gate** — `context_origin_conformance.py` + golden inventory test                      | 01         | Ships the responsibility transfer. From here, undeclared contributors cannot merge. Highest leverage step. |
| 03  | Tool-schema footprints — widen `_model_tool_schema_revision`, keyed by declared origin              | 01         | Ships the headline number (audit F/G/H) standalone; needs 01 for labels, not 02.                           |
| 04  | `ContextOccupancySnapshot` contracts + `TokenCounterPort` reuse + digest memoization                | 03         | Pure contracts + counting. No wiring.                                                                      |
| 05  | `ModelInvocationMiddleware` hook + declaration↔measurement reconciliation + `undeclared_tokens`     | 04         | Where declarations get verified rather than trusted (§4.4).                                                |
| 06  | Third-party `deepagents` adapter + per-profile golden fixture                                       | 05         | Lands audit B/C/D. Deferred until 05 exists so the fixture asserts against real measurement.               |
| 07  | Message-origin declarations + structural classification                                             | 05         | Lands audit J–S, the largest bucket.                                                                       |
| 08  | Persistence + migration + retention cascade                                                         | 05         | Can land before 07; snapshots simply carry fewer message segments.                                         |
| 09  | Read API + SSE event + facade proxy                                                                 | 08         | Consumer-facing surface.                                                                                   |

**01 + 02 are the ones that matter and should land together.** They are the
mechanism; everything after is measurement built on top. 03 is independently
shippable and independently useful — it alone answers "what does the tool
surface cost."

## 9. Test plan

- **Declaration gate (the keystone).**
  `undeclared_context_contributors(_SRC_ROOT) == ()` plus a pinned
  `context_origin_inventory` tuple, mirroring `test_llm_seam_gate.py`. A tool
  added without a declaration fails here, by name.
- **Zero undeclared at runtime.** A hermetic run on the deterministic fake model
  asserts `undeclared_tokens == 0` across every model call. This is the test
  that proves declarations are complete rather than merely present — a
  contributor that declares one label and emits different text fails it.
- **Golden occupancy fixture.** The same run asserts an exact segment breakdown
  per declared origin, per harness profile. This is the regression net for
  `deepagents` bumps — a library upgrade that adds prompt text moves the
  third-party fixture (§4.3) and fails loudly with the constant named.
- **Reconciliation bound — NOT as originally specified.** The plan was
  `|unattributed_delta| / provider_input_tokens < 0.05` against per-provider
  fixtures. That test never shipped, and it would not have passed: per-segment
  counting adds a ~7-token envelope per segment, so a realistically-shaped
  request over-counts by ~5.9% before any provider drift at all (§3.3). A single
  ratio therefore cannot separate the two things it was supposed to bound.

  What ships instead is the bias itself, pinned: `TestMeasuredCountingBias`
  asserts the per-segment envelope and the single-encoder equality, so the known
  artifact cannot grow silently and the false provider-tokenizer claim cannot
  quietly become true. The honest position is that `unattributed_delta` today
  carries **envelope + drift together**, and the envelope dominates. Separating
  them — by netting the envelope out at the reporting layer, never by moving
  segments (§3.3) — plus a real per-provider fixture bound, is the remaining
  work before any consumer should read the delta as "drift".

- **Scope isolation.** A run with subagents asserts no cross-scope summation and
  that root `free_tokens` ignores child occupancy (§6.2).
- **Retry.** Two attempts produce two snapshots; the rollup counts one.
- **Fail-open.** A raising tokenizer yields a partial snapshot and a completed
  run.
- **Topology divergence.** The same conversation under web vs desktop profiles
  produces different `tools.*` totals, asserting §2.1's exclusion behavior is
  captured rather than hidden.
- **No content leakage.** Property test over segments asserting `detail` never
  contains message or tool-result content.

## 10. Open questions

1. **Is the AST gate enough, or do we also fail closed at runtime?** §4.2 catches
   undeclared contributors in CI. A runtime option is to refuse to compose an
   undeclared tool into `model_tools` at all. That is stronger but can take a
   run down over an observability concern, which conflicts with §6.4. Proposed:
   AST gate hard-fails CI; runtime records `UNDECLARED` and alerts, never
   raises. Wants a decision before PRD-02.
2. **Segment granularity for tools** — per-tool rows (~25–40 segments/call) or
   per-origin aggregates with per-tool detail only on the latest snapshot? Per-tool
   is what makes the report actionable; JSONB makes the row size acceptable.
   Leaning per-tool.
3. **Do we measure `response_format`?** Small, and only present on structured
   calls. Cheap to include; proposed included for completeness of the residual.
4. **Cross-run rollups** — is "average occupancy by segment across an org" a v1
   need, or does per-run suffice? Affects whether §5 needs an aggregate table.
5. **Pricing gaps** — models absent from the pricing catalog have no
   `context_window_tokens`, so `free_tokens` is `null`. Acceptable, or do we
   need a fallback window per provider family?

## 11. Reference measurements

Estimated at 4 chars/token (the repo's `TokenBudgetEvaluator` heuristic); treat
as ±20% until PRD-04 routes counting through the real tokenizer. Relative
ranking is reliable.

**Our runtime tool descriptions — 1,794 tok resident** (`prompts/tools.py`):
`publish_artifact` 650 · `revise_artifact` 364 · `stage_rowset_write` 323 ·
`ask_a_question` 299 · 5 loaders 158.

**Our system blocks:** `DEFAULT_INSTRUCTIONS` 971 ·
`web_subagent_suffix` 801 · `WORKSPACE_STAGED_WRITE` 187 ·
`WORKSPACE_ACCESS_GUIDANCE` 140.

**Library-owned, present in the installed package (~14.7k total, installed
subset varies by profile):** `TASK_TOOL_DESCRIPTION` 1,644 ·
`MEMORY_SYSTEM_PROMPT` 1,280 · `EXECUTE_TOOL_DESCRIPTION` 693 (excluded on web) ·
`TASK_SYSTEM_PROMPT` 536 · `_anthropic_opus_4_7` suffix 537 ·
`READ_FILE_TOOL_DESCRIPTION` 468 · `SKILLS_SYSTEM_PROMPT` 465 ·
`FILESYSTEM_SYSTEM_PROMPT` 292.

The immediate actionable consequence: `publish_artifact` + `revise_artifact` +
`stage_rowset_write` = **1,337 tok of rent on every model call**, and the
mechanism to defer them already exists — the capability bridge
(`load_tool_spec` + `CAPABILITY_DISCOVERY_PROTOCOL`). This ledger turns that
from a guess into a measurement.

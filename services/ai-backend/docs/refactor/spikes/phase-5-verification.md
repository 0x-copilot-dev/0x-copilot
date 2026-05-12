# Phase 5 verification spike — 2026-05-11

**Status:** Complete
**Author:** architecture audit, 2026-05-11
**Scope:** Code-level verification of every Phase 5 PRD ([P17–P21](../00-roadmap.md#phase-5--major-library-swaps--structural-shifts)) before any implementation begins.
**Method:** `grep` + targeted reads of [`services/ai-backend/src/`](../../../src/). No tests run, no behavior changed.

This spike was triggered by the same retraction pattern that closed [`11-citations-consolidation.md`](../11-citations-consolidation.md) (P14), [`12-worker-stream-cleanup.md`](../12-worker-stream-cleanup.md) (P15), and the original [`15-pg-partman-retention.md`](../15-pg-partman-retention.md) (P18). The Phase 5 PRDs were drafted from architecture diagrams; each one needed a code-level gate before it could become a commitment.

**Headline result.** All four remaining Phase 5 PRDs need substantial revision. None of them, as drafted, would have shipped the right change.

---

## TL;DR per PRD

| PRD                                                       | Original framing                                                        | Verified reality                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | New shape                                                                                                                                                                                 |
| --------------------------------------------------------- | ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [P17 Checkpointer](../14-langgraph-checkpointer.md)       | "Adopt LangGraph Checkpointer; delete `CheckpointStorePort`"            | Port has **0 callers**. No adapter files exist. LangGraph already uses `InMemorySaver` via `runtime_checkpointer()` in [`deep_agent_builder.py`](../../../src/agent_runtime/execution/deep_agent_builder.py).                                                                                                                                                                                                                                                                                   | **Delete-only PR (~50 LOC).** Optional separate PR to swap `InMemorySaver` → `PostgresSaver` if durable graph state is wanted.                                                            |
| [P19 Repository collapse](../16-repository-collapse.md)   | "9 ports + 17 records → 4 repositories"                                 | 2 ports (`MemoryMetadataPort`, `PayloadStoragePort`) have **0 callers**. 3 ports have 1 real caller. 3 ports have multiple real callers (legitimately shared). The "collapse to 4 repositories" framing is the same kind of overcounting that retracted P14/P15.                                                                                                                                                                                                                                | **Delete 2 dead ports.** Optionally regroup `PersistencePort` (794 LOC, 60+ methods) into topic-bound sub-protocols, but as a separate refactor than P19's original prescription.         |
| [P20 LiteLLM providers](../17-litellm-providers.md)       | "Replace 3 custom provider stream adapters with LiteLLM"                | The "stream adapters" are **citation extractors** consuming LangChain `AIMessageChunk`. LiteLLM doesn't extract provider-native citation primitives. The underlying LLM call uses `langchain_anthropic.ChatAnthropic` etc. — not raw provider SDKs.                                                                                                                                                                                                                                             | **Reframe.** P20 should ask "should LangChain provider wrappers be replaced with LiteLLM `acompletion`?" — a different and more answerable question. Citation extractors stay regardless. |
| [P21 LangGraph interrupts](../18-langgraph-interrupts.md) | "Replace bespoke approval/auth interrupts with LangGraph `interrupt()`" | LangGraph `interrupt()` is **already imported and in production**: [`auth_mcp.py:11`](../../../src/agent_runtime/capabilities/mcp/middleware/auth_mcp.py#L11), [`ask_a_question.py:10`](../../../src/agent_runtime/capabilities/tools/builtin/ask_a_question.py#L10). [`astream_runtime_resume`](../../../src/agent_runtime/execution/runtime.py) already uses `Command(resume=...)`. `action_interrupt_events` is the worker-side recognition of LangGraph's pause, not a competing mechanism. | **Retract.** The premise — that bespoke interrupts compete with LangGraph — is false.                                                                                                     |

---

## P17 — Checkpointer — verified evidence

### Grep result: `CheckpointStorePort` / `CheckpointRecord` callers

Excluding the port definition itself, the record file, and `__init__.py` re-exports, **there are zero callers** of `CheckpointStorePort` or `CheckpointRecord` in the entire codebase.

```
$ grep -rn "CheckpointStorePort\|CheckpointRecord\|checkpoint_store" src/ --include="*.py" \
    | grep -v "__pycache__" | grep -v -E "ports\.py|__init__\.py|records/"
(no output)
```

### Grep result: LangGraph checkpointer state

LangGraph already runs with a checkpointer; it's just in-memory by default:

```python
# src/agent_runtime/execution/deep_agent_builder.py, runtime_checkpointer()
if _runtime_checkpointer is None:
    try:
        from langgraph.checkpoint.memory import InMemorySaver
    except ImportError:
        from langgraph.checkpoint.memory import MemorySaver as InMemorySaver
    _runtime_checkpointer = InMemorySaver()
return _runtime_checkpointer
```

### Adapter files

```
$ find src/runtime_adapters -name "*checkpoint*" -type f
(no output)
```

No `InMemoryCheckpointStore`, no `PostgresCheckpointStore`. The port has no adapter implementations either.

### Verdict

`CheckpointStorePort` is residue from an earlier iteration. Deleting it is mechanical; nothing depends on it. The original P17 PRD's [verification matrix §2](../14-langgraph-checkpointer.md#2-verification-required-before-approval) anticipated this exact outcome — the first row of the matrix was "Is `CheckpointStorePort` actually read by the Deep Agents / LangGraph builder?" Answer: no.

### Revised P17 plan

**One PR:**

- Delete [`agent_runtime/persistence/records/checkpoints.py`](../../../src/agent_runtime/persistence/records/checkpoints.py).
- Delete `CheckpointStorePort` and its method signatures from [`agent_runtime/persistence/ports.py`](../../../src/agent_runtime/persistence/ports.py).
- Remove re-exports of `CheckpointRecord` / `CheckpointStorePort` from [`agent_runtime/persistence/__init__.py`](../../../src/agent_runtime/persistence/__init__.py) and [`agent_runtime/persistence/records/__init__.py`](../../../src/agent_runtime/persistence/records/__init__.py).
- Run the test suite. Anything that imports the deleted symbols breaks loudly; nothing should.

**Optionally, a follow-up PR (independent of P17):**

- If durable graph state is wanted (for crash recovery, long-pause interrupts, etc.), swap `InMemorySaver()` in `runtime_checkpointer()` for `langgraph.checkpoint.postgres.PostgresSaver` reusing the existing `application_name`-tagged pool. This is a different conversation than P17 was framed as — it's the actual product question "do we want graph state to survive worker restarts," not "do we have two checkpoint systems." The answer affects [P21](#p21--langgraph-interrupts--verified-evidence) durability claims.

**Risk:** Trivial. Delete of unreachable code.
**Behaviors to preserve:** None new; the port has no behavior to preserve.

---

## P19 — Repository collapse — verified evidence

### Caller counts per port

Excluding adapter implementation files (`runtime_adapters/in_memory/*` + `runtime_adapters/postgres/*`) and the `runtime_adapters/factory.py` wiring:

| Port                               | Real callers | Caller files                                                                                                                |
| ---------------------------------- | ------------ | --------------------------------------------------------------------------------------------------------------------------- |
| `MemoryMetadataPort`               | **0**        | (none)                                                                                                                      |
| `PayloadStoragePort`               | **0**        | (none)                                                                                                                      |
| `DraftStorePort`                   | 4            | worker/loop, worker/handlers/run, capabilities/backends/draft_backend, api/draft_service                                    |
| `SubagentStorePort`                | 1            | api/workspace_feed_service                                                                                                  |
| `SourceStorePort`                  | 1            | api/workspace_feed_service                                                                                                  |
| `CitationStorePort`                | 4            | postgres/source_store (cross-store delegation), worker/loop, worker/handlers/run, capabilities/citations, api/share_service |
| `ConversationToolOrdinalStorePort` | 5            | worker/tool_observations, worker/loop, worker/handlers/run, worker/handlers/approval, capabilities/conversation_ordinals    |
| `ShareStorePort`                   | 1            | api/share_service                                                                                                           |

### What the structure actually shows

- **2 dead ports** (`MemoryMetadataPort`, `PayloadStoragePort`): clean delete.
- **3 single-caller ports** (`SubagentStorePort`, `SourceStorePort`, `ShareStorePort`): could in principle be inlined into their lone caller. But the Protocol earns its keep — in-memory adapter for tests + Postgres adapter for prod, both behind the same surface. Inlining would either lose the test seam or duplicate it. **Keep.**
- **3 multi-caller ports** (`DraftStorePort`, `CitationStorePort`, `ConversationToolOrdinalStorePort`): legitimately shared abstractions. Multiple distinct callers consume the same surface — exactly where the hexagonal pattern earns its keep. **Keep.**

### What about the 17 records?

The 17 record types in `persistence/records/` are mostly the **boundary types** [`docs/CLAUDE.md`](../../CLAUDE.md) mandates: "Use Pydantic at every IO/domain boundary." They're not storage shapes pretending to be domain types; they're domain types that happen to also persist. Collapsing them into ORM models would push storage concerns into the domain layer — the opposite of what the engineering rules call for.

### What about the 794-LOC `PersistencePort`?

That's the real surface that warrants discussion. `PersistencePort` carries 60+ methods spanning conversations, messages, runs, approvals, audit, retention, budgets, pricing, usage. It is the **only** port in the codebase that has god-class-like girth.

A surgical refactor here would be to split it by topic — `ConversationPersistencePort`, `RunPersistencePort`, `ApprovalPersistencePort`, `AdminPersistencePort` — while keeping each adapter (in-memory, Postgres) as a single class implementing all of them. That's a real, defensible refactor. But it's much narrower in scope than P19 as written, and it should be evaluated against whether `RuntimeApiService` callers actually use the methods in topical clusters or interleave them (which would defeat the split).

### Verdict

**P19 as written is 70% wrong** — the "9 ports + 17 records → 4 repositories" framing repeats the same overcounting mistake that retracted P14/P15. The actual finding is:

- 2 dead ports to delete (small win).
- 6 legitimately-bounded ports to keep.
- 17 records to leave alone (Pydantic boundary types per project rules).
- One narrow opportunity: split the 794-LOC `PersistencePort` by topic. That's its own PRD, not P19.

### Revised P19 plan

**One PR (small):**

- Delete `MemoryMetadataPort` + `PayloadStoragePort` from [`persistence/ports.py`](../../../src/agent_runtime/persistence/ports.py).
- Delete any corresponding `MemoryMetadata*` / `Payload*` records that turn out to be unused (audit those separately before deleting).
- Remove re-exports.

**Possible separate PRD (open):** topic-split of `PersistencePort` into 3–4 narrower Protocols, single adapter class still implementing all of them. Worth opening if the topic boundaries actually correspond to caller-cluster boundaries; verify in code first.

**Risk:** Trivial for the dead-port delete. The topic-split PRD (if opened) would be Medium.
**Behaviors to preserve:** None — the deleted ports have no behavior.

---

## P20 — LiteLLM providers — verified evidence

### Grep: LiteLLM usage in code

```
$ grep -rn "import litellm\|from litellm\|litellm\." src/ --include="*.py"
(no output)
```

LiteLLM is referenced in [`01-pricing-from-litellm.md`](../01-pricing-from-litellm.md) (the pricing PRD) but not yet in the live codebase. The provider streaming layer does not import LiteLLM today.

### What the "stream adapters" actually do

[`anthropic_stream_adapter.py`](../../../src/agent_runtime/execution/providers/anthropic_stream_adapter.py) docstring:

> Consumes LangChain `AIMessageChunk` objects produced by `langchain_anthropic.ChatAnthropic`. Anthropic's native citation primitives arrive interleaved with text content blocks: a `citations_delta` block lands either alongside the text it grounds (same content-block `index`) or as a follow-on chunk whose `text` is empty and whose block carries a `citations` list.
>
> The adapter:
>
> 1. Detects citation blocks in `chunk.content` (or `chunk.message.content`).
> 2. Builds a `SourceRef` per citation and registers it through `CitationLedger.cite`.
> 3. Returns a text delta that appends the resulting `[c<id>]` tokens immediately after the cited prose…

**These files are citation extractors, not stream adapters in the LiteLLM sense.** The actual streaming happens through LangChain's `ChatAnthropic` / `ChatOpenAI` / `ChatGoogleGenerativeAI`. The "adapter" files exist to read provider-native citation primitives out of LangChain chunks (which surface them via untyped `content` blocks) and feed `CitationLedger`.

### Implication for the original P20 framing

The original PRD asked "does LiteLLM stream Anthropic thinking modes, OpenAI reasoning summary, Gemini grounding?" That was the wrong question. The right questions, in order:

1. **Is the LangChain wrapper layer (`langchain_anthropic.ChatAnthropic`, etc.) the right substrate, or should the underlying model call go through LiteLLM's `acompletion`?** This is the real "replace with LiteLLM" question — and it's a different shape than the one the PRD analyzed.
2. **If the substrate changes to LiteLLM, does the chunk shape that reaches the citation extractors stay compatible?** LiteLLM normalizes chunks across providers; LangChain preserves provider-specific content-block structure. The citation extractors depend on the latter. So switching substrate likely breaks the citation extraction path.
3. **Given (2), is the substrate switch worth it?** LiteLLM's primary value-add is provider routing, fallback, and pricing/usage normalization. The pricing/usage piece is already being addressed by [P12 pricing-from-litellm](../01-pricing-from-litellm.md). Provider routing and fallback are real but lower-priority capabilities.

### Verdict

P20's original scope ("replace stream adapters") is **not the right question.** The misnamed files don't compete with LiteLLM. The real LiteLLM-substrate question is genuinely open but has a high-cost side effect (citation extraction path) that wasn't in the PRD's risk model.

### Revised P20 next step

**Don't draft replacement code.** Decide whether to keep the question open at all:

- Option A — **withdraw P20.** Pricing already gets LiteLLM's data. Streaming substrate via LangChain works. No payoff justifies the cost of breaking the citation extraction path.
- Option B — **rescope P20** as "evaluate LiteLLM as the LangChain-provider substrate." That spike would include: testing whether LiteLLM-produced chunks preserve enough provider-native structure for citation extraction; comparing usage-metadata fidelity vs LangChain wrappers; measuring fallback / routing benefits.

**Recommend Option A** unless someone has a concrete operational need (fallback, multi-region routing, mixed-provider load balancing) that LangChain wrappers can't meet. There's no current evidence of that need in the codebase or PRDs.

---

## P21 — LangGraph interrupts — verified evidence

### Grep: LangGraph `interrupt()` is already in production

```
$ grep -rn "from langgraph.types import" src/ --include="*.py"
src/agent_runtime/capabilities/tools/builtin/ask_a_question.py:10:
    from langgraph.types import interrupt as langgraph_interrupt
src/agent_runtime/capabilities/mcp/middleware/auth_mcp.py:11:
    from langgraph.types import interrupt as langgraph_interrupt
src/agent_runtime/delegation/subagents/atlas_task_tool.py:35:
    from langgraph.types import Command
src/agent_runtime/execution/runtime.py:10:
    from langgraph.types import Command
```

### How `auth_mcp.py` actually uses it

```python
# src/agent_runtime/capabilities/mcp/middleware/auth_mcp.py
auth_session_creator: McpAuthSessionCreator
runtime_context: AgentRuntimeContext
interrupt_handler: Callable[[dict[str, Any]], object] = langgraph_interrupt
name: str = Values.ToolName.AUTH_MCP
```

`langgraph_interrupt` is the **default handler** — meaning the MCP auth flow already pauses the LangGraph graph via the library-native `interrupt()` primitive. Not a bespoke event-bus mechanism, not a custom queue command. The library mechanism.

### How resume actually works

```python
# src/agent_runtime/execution/runtime.py
async def astream_runtime_resume(
    ...
):
    ...
    Command(resume=resume),
```

Full LangGraph-native resume path. The worker's [`approval handler`](../../../src/runtime_worker/handlers/approval.py) imports it directly:

```python
from agent_runtime.execution.runtime import astream_runtime_resume
```

### What is `action_interrupt_events` actually for?

```python
# src/runtime_worker/streaming_executor.py:248
action_interrupt_events = frozenset(
    {
        RuntimeApiEventType.APPROVAL_REQUESTED,
        RuntimeApiEventType.MCP_AUTH_REQUIRED,
    }
)
```

This is the worker-side **event-type recognition** set — it tells the streaming executor "when you see these event types, the graph paused; emit the right status transitions and stop draining the stream." It is not a competing interrupt mechanism. It is the integration glue between LangGraph's pause and the worker's `RUN_STATUS=AWAITING_APPROVAL` projection.

### Implication for the original P21 framing

The original PRD's stated goal — _"Replace the bespoke approval-interrupt mechanism with LangGraph's `interrupt()` primitive"_ — describes work that has **already shipped**. The team is on LangGraph interrupts. Resume is LangGraph-native. The approval row is already the durable rendezvous. `MCP_AUTH_REQUIRED` already flows through `langgraph_interrupt`.

What's actually in the codebase is the design P21 wanted to migrate toward. There is nothing to replace.

### Verdict

**P21 should be RETRACTED.**

There's one genuinely-open question that adjacent to P21 but isn't its original scope: **the in-memory `InMemorySaver` checkpointer used by `runtime_checkpointer()` is non-durable.** If a worker process dies while a graph is paused mid-interrupt, the in-memory state is lost. The approval row survives (it's in Postgres), but LangGraph's view of where in the graph it was paused does not.

That's the same question raised in the [P17 follow-up section](#revised-p17-plan): is it worth upgrading `InMemorySaver` to `PostgresSaver` for durable graph state? If yes, _that_ change is what makes the existing LangGraph-interrupt path fully durable. If no, the current implementation is correct as a design choice — the approval row is enough rendezvous for the application's needs, and rebuilding graph state on resume is acceptable.

### Revised P21 next step

**Retract P21 as currently scoped.** Open a small follow-up question (assigned to whoever owns the runtime worker): _do we need durable LangGraph checkpoint state to survive worker restart, given that approval rows are already durable in Postgres?_ The answer determines whether the optional `PostgresSaver` upgrade from P17 ships or not.

---

## Cross-cutting findings

### The verification spike pattern works

Four PRDs went in; four substantial revisions came out. Two of them (P20, P21) needed reframe-or-retract. One of them (P17) collapsed to a 50-LOC delete. One (P19) collapsed to ~80% smaller scope.

If we had implemented the PRDs as drafted, we'd have:

- Built two checkpointer adoption paths instead of one (P17).
- Written a 6-month repository-collapse refactor (P19) for what is a 2-port delete.
- Broken the citation extraction path while migrating provider streaming to LiteLLM (P20).
- Re-implemented an interrupt mechanism that already exists (P21).

The cost-benefit of "spike before PRD" is overwhelmingly positive on this codebase.

### Common cause of the misframings

All four PRDs were drafted from architecture diagrams without source reading. Three of the four failures are diagram artifacts:

- **P17:** The diagram showed `CheckpointStorePort` as a real port; the code shows it's unused residue.
- **P20:** The diagram named files `*_stream_adapter.py`; the files do citation extraction. Naming misled the diagram and the diagram misled the PRD.
- **P21:** The diagram showed `action_interrupt_events` and didn't show that `langgraph_interrupt` is the live mechanism. The "bespoke vs library" framing was an artifact of what the diagram included and excluded.

**Lesson for future PRDs (Phase 6 and beyond): if a PRD says "replace X with Y," confirm in code that X is what the diagram says it is, that Y is what you think it is, and that nothing else in the codebase already does this.**

---

## Next-step ordering

In priority order, smallest scope first:

1. **Land P17 delete** (~50 LOC, trivial risk). Removes confusion about which checkpoint story is canonical.
2. **Land P19 dead-port delete** (~80 LOC). Removes confusion about which ports are real.
3. **Decide on the `PostgresSaver` durability follow-up** (consumes the open question both P17 and P21 surface).
4. **Retract P21** (mark with banner, point to current LangGraph integration in [`auth_mcp.py`](../../../src/agent_runtime/capabilities/mcp/middleware/auth_mcp.py)).
5. **Decide P20 disposition** — withdraw or rescope to "evaluate LiteLLM as LangChain substrate." Recommend withdraw unless there's a concrete routing/fallback requirement.
6. **Open a separate PRD for the `PersistencePort` topic-split** if caller analysis confirms the topic boundaries match caller clusters. Out of P19's original scope but the only piece of P19 that warrants engineering work.

Steps 1–4 can ship within a week. Step 5–6 are decisions, not implementations.

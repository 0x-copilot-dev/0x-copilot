# 04 — Citations: persistent ordinal ↔ tool_call_id binding map

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Supersedes the positional-counter scheme described in [`03-citations-model-declared.md`](03-citations-model-declared.md). Same wire shape (`citation_made` events, `[[N]]` markers) — the storage and resolution model change.
> **Owner:** ai-backend (1 migration · 1 new port + 2 adapters · refactor `ConversationOrdinalAllocator` · refactor `ConversationOrdinalSeeder` → `Restorer` · refactor `CitationResolver` (strict stamping) · refactor `ToolObservationIndexBuilder` (table-backed) · 2 schema additions to MCP / web-search args) · runtime-worker (allocator wiring in `run` + `approval` handlers, persistence injection) · backend-facade (none) · api-types (none — `CitationLink` shape is unchanged) · frontend (delete ordinal-position fallback, drop synthetic-FE-tool filter, simplify chip resolver, add invariant tests) · design-system (none).
> **Size:** **M.** No new wire surface. One migration + one new persistence port. The bulk is internal: routing every ordinal allocation through one binding map and pruning the four out-of-band counters that compensate for the binding being missing today. Frontend is mostly **deletion**.
> **Depends on:**
>
> - ✅ [`03-citations-model-declared.md`](03-citations-model-declared.md) — established the `[[N]]` marker / `citation_made` event / `CitationResolver` / `ConversationOrdinalAllocator` primitives.
> - ✅ PR 1.5 subagent discovery workspace feeds — supplies the `RuntimeWorkerDependencies` injection point used to wire the new store.
> - ✅ Existing `agent_runtime_events` / `agent_messages` migration set (`migrations/0001`–`0025`).
>   **Reads alongside:**
> - [`docs/new-design/03-citations-model-declared.md`](03-citations-model-declared.md) — the design this PR fixes the implementation of.
> - [`services/ai-backend/CLAUDE.md`](../../services/ai-backend/CLAUDE.md) — module split, port pattern.
> - [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md) — facade-only network rule (FE doesn't talk to the new table directly; it consumes the same `citation_made` events as today).

---

## 0 · TL;DR

Citation today is _model-declared_ (model writes `[[N]]`, server resolves to a `tool_call_id`). The model side works. The resolution side is fragile because **four independent counters** all try to mean "the conversation_ordinal of tool call N" and they don't agree:

1. The runtime allocator's live counter (correct authoritative value at allocation time).
2. The seeder that re-derives the counter on a new run (counts `TOOL_CALL_STARTED` events).
3. The cross-turn observation index builder (counts `TOOL_CALL_STARTED` events again).
4. The frontend's `toolCallIdsInOrder` document-order fallback used when `source_tool_call_id` is empty.

| Symptom                                                                               | Today                                                                         | Root cause                                                                   |
| ------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| Same ordinal allocated twice in a conversation                                        | Reproduced in conv `8b117b2d…` (logs): linear `[[5]]` then web_search `[[5]]` | (2) under-counts when MCP middleware allocates inside a tool body            |
| Sources tab shows wrong tool name (`approval_request — list_…`)                       | Reproduced                                                                    | (4) lands on a synthetic FE-only tool part                                   |
| Click web_search source row → highlights Linear chip                                  | Reproduced                                                                    | (4) collision: chip and row resolve to different tool_call_ids               |
| 3 web searches → 1 row in Sources                                                     | Reproduced                                                                    | (4) dedupe collapses ordinals that all fall back to the same indexed call_id |
| Cross-turn `[[N]]` resolves to a different prior tool than the one the model intended | Latent — depends on (3) agreeing with (1)                                     | (3) re-derives positionally instead of looking up the real binding           |

**One source of truth.** A new `agent_conversation_tool_ordinals` table stores `(conversation_id, conversation_ordinal, tool_call_id, tool_name, run_id, allocated_at)`. The `ConversationOrdinalAllocator` becomes a thin in-memory cache over this table. Every tool that allocates an ordinal binds it to a real `tool_call_id` (no exceptions). The `CitationResolver` always stamps `source_tool_call_id` on `citation_made` events. The cross-turn observation index joins against the table by `tool_call_id`. The FE drops every line of positional-fallback code.

**The four principles**

1. **Allocate exactly once per real tool call, with a real `tool_call_id`.** No `allocate()` without a binding. The `tool_call_id` reaches every allocator call site via LangChain's `InjectedToolCallId` — schemas that don't carry it (DuckDuckGo's `DuckDuckGoSearchInput`, today's `McpToolCallRequest`) are extended at registration time.
2. **The binding map is durable.** Persisted on every allocation, restored on every run-bind / approval-resume. The seeder/restorer reads the table; it never recomputes ordinals from event-counting.
3. **Citations carry the binding on the wire.** Every `citation_made` event has `source_tool_call_id` populated when the resolver fires. Empty means hallucinated ordinal — a real failure mode the FE renders as `?` (already today).
4. **The FE has zero positional fallback.** Chip's `data-citation-id` and source row's `citation_id` both key off `tool:<source_tool_call_id>` — same field, no derivation.

LoC estimate: ai-backend ≈ 480 (1 migration + 1 port + 2 adapters + allocator/seeder refactor + 2 schema patches + cross-turn refactor + tests) · runtime-worker ≈ 60 (dependency injection + handler wiring) · api-types ≈ 0 · frontend ≈ −180 net (≈ 70 added including invariant tests, ≈ 250 deleted: `toolCallIdsInOrder`, `SYNTHETIC_FE_TOOL_NAMES`, fallback branches in `citedToolSources` / `OrdinalCitationChip` / `useResolvedOrdinalCitation`).

---

## 1 · PRD

### 1.1 Problem

The PR 1.1-rev2 design ([`03-citations-model-declared.md`](03-citations-model-declared.md)) introduced model-declared citations — the model writes `[[4]]` after a fact, and the runtime resolves the marker to a tool invocation. The pointer was supposed to be **stable across the conversation lifetime** so:

- Within-turn citations (chip → tool card on the same response) work.
- Cross-turn citations (in turn T+k, reference a tool that ran in turn T) work — the model receives a "Prior observations" prompt context block that tells it `cite as [[4]]` for last turn's `linear.list_issues`, and writing `[[4]]` resolves to the same `tool_call_id`.

In practice the pointer is unstable. Direct reproductions on `main` (recorded in conv `8b117b2d…`):

1. **Same ordinal allocated twice in a conversation.** Run `1fdaefca` (Linear) allocated ordinals 4, 5. Seeder for the next run (web_search) returned `starting_ordinal=4` — collision. The next run's first allocate returned 5 again. The conversation now has two distinct `tool_call_id` values bound to ordinal 5.
2. **`citation_made.source_tool_call_id` is empty for every MCP and DuckDuckGo call.** [`mcp/middleware/call_tool.py`](../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py) calls `allocator.allocate()` (no binding); LangChain Community's `DuckDuckGoSearchResults` schema doesn't carry `InjectedToolCallId` so the capturing wrapper extracts `None`. The wire field is the empty string in both cases.
3. **The FE compensates with a positional fallback** (`toolCallIdsInOrder[ordinal - 1]` in [`citedToolSources.ts`](../../apps/frontend/src/features/chat/chatModel/citedToolSources.ts)). With (1) breaking ordinal uniqueness and (2) emptying the wire field, the fallback collides — clicking a web_search source row jumps to a Linear chip.
4. **Cross-turn fails the same way.** [`tool_observations.py`](../../services/ai-backend/src/runtime_worker/tool_observations.py) re-derives ordinals positionally at next-turn build time. Whatever (1) miscounted is miscounted again here. The "Prior observations" context block tells the model `cite as [[N]]` for an ordinal that no longer points at the tool the user thinks it does.

The four counters that should agree:

| #   | Where                                                     | What it counts                                       | Failure mode                                                                                 |
| --- | --------------------------------------------------------- | ---------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| 1   | `ConversationOrdinalAllocator._counter` (in-memory)       | Each `allocate*` call (canonical at allocation time) | Lost on resume because the allocator is reconstructed                                        |
| 2   | `ConversationOrdinalSeeder.seed_from_event_log`           | `TOOL_CALL_STARTED` events in prior runs             | Misses MCP-middleware allocations that happen inside a single `tool_call_started`            |
| 3   | `ToolObservationIndexBuilder._index_ordinals_from_events` | `TOOL_CALL_STARTED` events in prior runs (again)     | Same miss as (2); produces a different number than (1)                                       |
| 4   | FE `toolInvocationCallIdsInOrder`                         | Tool-call parts in chat tree document order          | Document order ≠ allocation order in the presence of approval gates and FE-synthesized parts |

The cure is to delete (2)–(4) and have everything read (1), but persist (1) so it survives resumes and can be queried cross-turn.

### 1.2 Goals

1. **Every ordinal is bound to a real `tool_call_id` at allocation time.** No `allocate()` calls without a binding; if a tool path can't supply a `tool_call_id`, that's a bug worth fixing — not a gap to paper over.
2. **The (`conversation_ordinal` ↔ `tool_call_id`) map is durable.** Persisted in `agent_conversation_tool_ordinals`. Read at run start, approval resume, cross-turn observation build, and in tests. The in-memory allocator is a write-through cache over the table.
3. **`citation_made` events always carry a non-empty `source_tool_call_id`.** When the resolver matches `[[N]]`, it looks up the binding (in-memory first, table fallback) and stamps the event. Missing → `WARNING` log + a metric that should never be > 0 in practice.
4. **Cross-turn citation works because the binding is the same data.** `ToolObservationIndexBuilder` reads the table, joins observations to ordinals by `tool_call_id`, emits `cite as [[N]]` from canonical values. No positional re-derivation.
5. **Frontend chip and source row use the same key.** `data-citation-id="tool:<source_tool_call_id>"` on both ends; `scrollChatToCitation` finds the right chip; clicking the source row never highlights an unrelated chip.
6. **The wire shape doesn't change.** `CitationLink`, `RuntimeApiEventType.CITATION_MADE`, `RuntimeFinalResponsePayload.cited_ordinals` are byte-identical to today. This PR is a backend correctness + FE simplification — clients that already understand the events keep working.
7. **Approval resumes preserve allocator state.** The handler in [`approval.py`](../../services/ai-backend/src/runtime_worker/handlers/approval.py) loads the allocator from the table; allocations made before the pause survive the resume.
8. **Test coverage pins the invariants.** Integration tests for the four worst journeys (multi-turn, MCP-with-approval, mixed MCP+web, manual cancel + retry) assert binding equality at every step.

### 1.3 Non-goals

- **Changing the wire format.** `[[N]]` markers, `citation_made` event shape, `CitationLink` schema all stay. (If this PR forced an api-types change, every client and replay path would have to migrate. We keep the contract; we fix the population.)
- **Migrating historical conversations' citations to the new table.** Existing rows in `agent_runtime_events` retain their `citation_made` events (some with empty `source_tool_call_id`). The FE's existing fallback code handles the legacy reads — it's deleted only after we're satisfied historical conversations resolve fine via the table backfill described in §3.10.
- **Re-architecting the resolver / allocator API surface.** `ConversationOrdinalAllocator.bind_for_run / unbind / active` (the ContextVar pattern) stays. We change construction and the `allocate*` implementation, not the call sites.
- **Persisting the resolver's per-message offset buffer.** That stays in-memory. The persistence boundary is the (ordinal, tool_call_id) binding, not the resolver's parsing state.
- **A new tab for "citations debug".** Out of scope. The FE renders the same chips and the same Sources tab.
- **Switching to a different ordinal scheme** (e.g. per-message position rendered on the chip instead of the conversation_ordinal). Discussed and explicitly deferred — the conversation_ordinal is the routing key; rendering a different number on the chip is a UX-only follow-up.
- **A separate audit log for ordinal allocations.** The new table is the audit log. Adding `audit_events` rows for each allocate would double-write; if compliance asks, we can derive an audit feed from the table.
- **Soft-delete on the binding rows.** Once an ordinal is allocated, the binding is permanent — even if the conversation is soft-deleted, the binding rows go with it on conversation deletion. No standalone TTL or revocation primitive.

### 1.4 Success criteria

- ✅ Every `tool.hint_appended` and `mcp.hint_appended` log line carries `call_id='<non-empty>'`. A grep across a fresh dev session over all five journey scripts returns zero `call_id=''` matches. **(Phase 1 gate.)**
- ✅ `agent_conversation_tool_ordinals` is created by yoyo migration `0026_conversation_tool_ordinals.sql`. Rollback tested locally. Postgres + in-memory adapters pass the same conformance suite. **(Phase 2 gate.)**
- ✅ `ConversationOrdinalAllocator.for_conversation` reads bindings from the new store; counter equals `max(ordinal)` of returned bindings; map equals the returned mapping. No call to `seed_from_event_log` remains in production code. **(Phase 3 gate.)**
- ✅ `_build_allocator_for_resume` in [`approval.py`](../../services/ai-backend/src/runtime_worker/handlers/approval.py) becomes a one-liner that delegates to `ConversationOrdinalAllocator.for_conversation`. The synthetic-`prior_run_ids` enumeration and message-walking it does today is deleted. **(Phase 3 gate.)**
- ✅ `CitationResolver.observe_delta` looks up `tool_call_id` for every matched ordinal and stamps `source_tool_call_id` on the persisted `citation_made` event. A new metric `citations.unbound_ordinal_total` is emitted; CI integration tests assert it stays at 0 across all journey scripts. **(Phase 3 gate.)**
- ✅ `ToolObservationIndexBuilder.build` reads from the new store. The `_index_ordinals_from_events` helper is deleted. The "Prior observations" prompt context lists ordinals that match the runtime's binding for those tool_call_ids. **(Phase 4 gate.)**
- ✅ Frontend deletes [`toolInvocationCallIdsInOrder`](../../apps/frontend/src/features/chat/chatModel/citedToolSources.ts) and the `toolCallIdsInOrder` plumbing through `citationsContext` / `OrdinalCitationChip` / `citedToolSources`. Chip's `data-citation-id` always equals `tool:<link.source_tool_call_id>`. `useResolvedOrdinalCitation` returns `null` when `link.source_tool_call_id` is empty (renders as `?`). **(Phase 5 gate.)**
- ✅ Integration test suite `tests/integration/citations/test_binding_journeys.py` covers four scripted journeys; each asserts:
  - Every `citation_made` event has a non-empty `source_tool_call_id`.
  - Every distinct `(conversation_ordinal, tool_call_id)` pair appears exactly once in the binding table.
  - The Sources-tab projection produces the exact expected number of rows.
  - Reverse handshake: scrolling from each Sources row's `citation_id` finds chips bearing the matching `data-citation-id`.
  - Cross-turn: in turn T+1, citing a turn-T ordinal resolves to the same `tool_call_id` as in turn T. **(Phase 6 gate.)**
- ✅ FE typecheck + build green. ai-backend pytest suite green. Backend pytest suite green. Frontend vitest suite green. Manual journey playback (the four scripts) all pass.

### 1.5 User stories

| #      | Persona                                     | Story                                                                                                                                                                                                                                                                                                                           |
| ------ | ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US-C1  | Single-tool turn (DuckDuckGo)               | User asks "what is X". Runtime calls `web_search` once. Model writes "X is Y [[1]]". Sources tab shows `[1] web_search — X · Cited 1×`. Click chip → highlights row. Click row's ↗ → highlights chip.                                                                                                                           |
| US-C2  | Same tool, multiple calls                   | User asks "compare A and B". Runtime calls `web_search` twice. Model writes "A says … [[1]]; B says … [[2]]". Sources shows two rows, ordinals 1 and 2, distinct queries. Each chip routes to its own row.                                                                                                                      |
| US-C3  | MCP tool, no approval                       | User asks Linear-connected workspace "list my issues". `call_tool` runs once. Model writes "PAR-1 [[3]], PAR-2 [[3]] …". Sources shows `[1] linear.list_issues — me`. Title is server.tool, not the wrapper name.                                                                                                               |
| US-C4  | MCP tool, **with approval**                 | Same as C3 but the per-chat policy gates `linear.list_issues`. Approval card surfaces; user approves; resume runs the tool. Allocator state survives the resume; the ordinal allocated in the resumed run is strictly greater than any allocated pre-pause. No collision with prior turns.                                      |
| US-C5  | Mixed turn (MCP + web in one response)      | User asks "what's blocking PAR-1 — also what's the public Linear status page saying". Linear `call_tool` (`[[3]]`) and `web_search` (`[[4]]`) in the same response. Sources shows both rows; chips and rows roundtrip correctly.                                                                                                |
| US-C6  | Cross-turn citation                         | Turn 1: user asks Linear, model cites `[[3]]`. Turn 2: user asks "summarise the highest-priority one". Model writes "PAR-9 (high, due Friday) [[3]]" — referring to turn 1's `linear.list_issues`. Sources tab in turn 2 shows the same row, citation_count grows to 2. The ordinal-to-tool binding is identical between turns. |
| US-C7  | Manual cancel + retry                       | User starts a Linear search; cancels mid-stream; immediately re-asks. Cancelled run's pre-cancel allocations remain bound (table is append-only). New run's allocator starts at `max(ordinal) + 1`. No reuse.                                                                                                                   |
| US-C8  | Hallucinated ordinal                        | Model writes `[[99]]` even though only ordinals 1–3 exist. Resolver fires `citation_made` with `source_tool_call_id=""` (no binding found). FE renders the chip as `?` (existing behaviour). Metric `citations.unbound_ordinal_total += 1` so we can spot LLM regressions.                                                      |
| US-C9  | Replay (operator opens an old conversation) | Conversation pre-dates this PR. Backfill (§3.10) populates `agent_conversation_tool_ordinals` from existing `tool_call_started` + `citation_made` events. Existing chips and Sources rows render as before.                                                                                                                     |
| US-C10 | Subagent                                    | A subagent's `web_search` (run via the subagent harness) allocates an ordinal in the parent conversation's binding map. Parent agent's prose can cite `[[N]]` referring to the subagent's tool call. Round-trip works.                                                                                                          |

---

## 2 · Spec

### 2.1 Persistence — `agent_conversation_tool_ordinals`

```sql
-- migrations/0026_conversation_tool_ordinals.sql

-- One row per (conversation, ordinal). Idempotent on (conversation, tool_call_id)
-- so a retried allocation for the same call_id collapses to the same ordinal.
-- org_id is mirrored from the conversation for RLS parity with every other
-- tenant-scoped table; FK to agent_conversations enforces tenancy.
CREATE TABLE IF NOT EXISTS agent_conversation_tool_ordinals (
    org_id              TEXT         NOT NULL,
    conversation_id     TEXT         NOT NULL REFERENCES agent_conversations(id) ON DELETE CASCADE,
    conversation_ordinal INTEGER     NOT NULL CHECK (conversation_ordinal > 0),
    tool_call_id        TEXT         NOT NULL,
    tool_name           TEXT         NOT NULL,
    run_id              TEXT         NOT NULL,
    allocated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (conversation_id, conversation_ordinal),
    UNIQUE (conversation_id, tool_call_id)
);
CREATE INDEX IF NOT EXISTS idx_actio_conversation_run
    ON agent_conversation_tool_ordinals (conversation_id, run_id);

ALTER TABLE agent_conversation_tool_ordinals ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON agent_conversation_tool_ordinals
    USING (org_id = current_setting('app.current_org', true));
```

Rollback drops the table cleanly. No data dependency from any other table; the FK from this table to `agent_conversations` cascades on conversation deletion (rows go with the conversation).

**Why composite primary key on (`conversation_id`, `conversation_ordinal`):** the ordinal is conversation-scoped by definition; the same ordinal value in two different conversations is a different binding. The pair is the natural key.

**Why a `UNIQUE` constraint on (`conversation_id`, `tool_call_id`):** the binding is bidirectional. Repeated allocator calls for the same `tool_call_id` (e.g. retry after a partial network failure) must collapse to the same ordinal. The `UPSERT` on insert relies on this constraint; without it, retries would produce two rows pointing at one tool call — exactly the bug we're fixing.

### 2.2 Service path — `ConversationToolOrdinalStorePort`

```python
# services/ai-backend/src/agent_runtime/api/ports.py (additions)

@dataclass(frozen=True)
class ToolOrdinalBinding:
    conversation_id: str
    conversation_ordinal: int
    tool_call_id: str
    tool_name: str
    run_id: str
    allocated_at: datetime

@runtime_checkable
class ConversationToolOrdinalStorePort(Protocol):
    """Persistent ordinal ↔ tool_call_id binding store."""

    def record(
        self,
        *,
        org_id: str,
        conversation_id: str,
        conversation_ordinal: int,
        tool_call_id: str,
        tool_name: str,
        run_id: str,
    ) -> ToolOrdinalBinding:
        """Idempotent UPSERT keyed on (conversation_id, tool_call_id).

        Returns the canonical binding. If the row already exists with a
        different ordinal, raises ``ConversationOrdinalConflict`` — a
        concurrent allocator on the same conversation has raced; the
        caller (the allocator) must reload state and abandon the local
        ordinal it had attempted to use.
        """

    def load(
        self,
        *,
        org_id: str,
        conversation_id: str,
    ) -> Sequence[ToolOrdinalBinding]:
        """Return all bindings for a conversation, sorted by ordinal asc."""
```

The `ConversationOrdinalConflict` exception is the contract for racing allocator instances (e.g. supervisor + subagent in the same process attempting to bind concurrently). The port doesn't take a transaction handle; the postgres adapter wraps each `record` in its own transaction.

### 2.3 Refactor — `ConversationOrdinalAllocator`

The class keeps its current public API (`allocate_for_tool_call`, `tool_call_id_for`, `has_ordinal`, `bind_for_run`, `unbind`, `active`). Two methods change:

- Construction: takes a `store: ConversationToolOrdinalStorePort` and an `org_id`. The classmethod `for_conversation(...)` becomes the canonical builder for both new runs and approval resumes.
- `allocate_for_tool_call`: idempotent on `tool_call_id`. Writes through to the store.

```python
# Pseudocode — actual implementation lives in capabilities/conversation_ordinals.py

@classmethod
async def for_conversation(
    cls,
    *,
    org_id: str,
    conversation_id: str,
    store: ConversationToolOrdinalStorePort,
) -> "ConversationOrdinalAllocator":
    bindings = await store.load(org_id=org_id, conversation_id=conversation_id)
    starting = max((b.conversation_ordinal for b in bindings), default=0)
    mapping = {b.conversation_ordinal: b.tool_call_id for b in bindings}
    return cls(
        org_id=org_id,
        conversation_id=conversation_id,
        starting_ordinal=starting,
        ordinal_to_tool_call_id=mapping,
        store=store,
    )

async def allocate_for_tool_call(
    self,
    *,
    tool_call_id: str,
    tool_name: str,
    run_id: str,
) -> int:
    if not tool_call_id:
        raise ValueError("tool_call_id required; use of allocate() is no longer supported")
    # Fast path: this tool_call_id was already bound (retry / re-entry).
    for ordinal, bound_call_id in self._ordinal_to_tool_call_id.items():
        if bound_call_id == tool_call_id:
            return ordinal
    self._counter += 1
    ordinal = self._counter
    try:
        binding = await self._store.record(
            org_id=self._org_id,
            conversation_id=self._conversation_id,
            conversation_ordinal=ordinal,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            run_id=run_id,
        )
    except ConversationOrdinalConflict:
        # Another allocator beat us. Reload and rebind.
        await self._reload_from_store()
        return await self.allocate_for_tool_call(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            run_id=run_id,
        )
    self._ordinal_to_tool_call_id[binding.conversation_ordinal] = binding.tool_call_id
    return binding.conversation_ordinal
```

`ConversationOrdinalSeeder` is renamed to `ConversationOrdinalRestorer` and its `seed_from_event_log` method is **deleted**. Replaced by `ConversationOrdinalAllocator.for_conversation`. Tests that mock the seeder to inject prior ordinals are rewritten to seed the store directly.

The legacy `allocate()` (no binding) is **deleted**. There is one remaining call site today ([`mcp/middleware/call_tool.py`](../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py)); §2.5 fixes it.

### 2.4 Schema additions — `tool_call_id` plumbing

**`McpToolCallRequest`** in [`mcp/cards.py`](../../services/ai-backend/src/agent_runtime/capabilities/mcp/cards.py):

```python
from typing import Annotated
from langchain_core.tools import InjectedToolCallId

class McpToolCallRequest(BaseModel):
    server_name: str = Field(...)
    tool_name: str = Field(...)
    arguments: dict[str, Any] = Field(default_factory=dict)
    tool_call_id: Annotated[str, InjectedToolCallId] = ""  # injected by LangChain at dispatch
```

`CallMcpTool.ainvoke` reads `parsed_input.tool_call_id`, asserts it's non-empty, and calls `allocator.allocate_for_tool_call(tool_call_id=..., tool_name=f"{server_name}.{inner}", run_id=...)`. The hint suffix is keyed by the same ordinal.

**DuckDuckGo / `web_search`**: the `WebSearchToolRegistry` returns LangChain's `DuckDuckGoSearchResults`, whose args schema (`DuckDuckGoSearchInput`) does **not** declare `InjectedToolCallId`. We don't own that schema; we wrap it at registration time:

```python
# services/ai-backend/src/runtime_worker/dependencies.py — sketch

def _wrap_with_tool_call_id_injection(tool: BaseTool) -> BaseTool:
    """Synthesize a wrapper schema that injects tool_call_id, then forwards
    every other field to the inner tool's args_schema. Used for tools whose
    upstream schema (LangChain Community, third parties) doesn't carry
    InjectedToolCallId by default."""
    inner_schema = tool.args_schema or BaseModel
    # Build a dynamic Pydantic model that inherits inner_schema's fields and
    # adds tool_call_id: Annotated[str, InjectedToolCallId].
    ...
    return tool.copy_with(args_schema=wrapped)
```

The capturing wrapper's `_extract_tool_call_id` becomes a hard read: if `tool_call_id` isn't present after schema injection, raise — that's a configuration bug, not a fallback case.

### 2.5 Refactor — `CitationResolver` strict stamping

[`citation_resolver.py`](../../services/ai-backend/src/agent_runtime/capabilities/citation_resolver.py)'s `observe_delta` already takes the active allocator. Today the stamping is best-effort. After this PR:

- On every `[[N]]` match, look up `allocator.tool_call_id_for(N)`.
- If found: stamp `source_tool_call_id` on the emitted `citation_made` event.
- If `None`: emit `source_tool_call_id=""` (still a valid `CitationLink`) and increment `citations.unbound_ordinal_total` metric. Log at `WARNING` with `conversation_id`, `ordinal`, `last_allocated`. This is the hallucinated-ordinal path (US-C8).

The wire shape doesn't change — `source_tool_call_id` is already a string field. Today it's empty often; after this PR it's empty rarely, and when empty it's an LLM regression worth investigating, not a runtime defect.

### 2.6 Refactor — `ToolObservationIndexBuilder` reads the table

[`tool_observations.py:88-93`](../../services/ai-backend/src/runtime_worker/tool_observations.py#L88-L93) stops counting events. New flow:

```python
async def build(...):
    ...
    bindings = await self._ordinal_store.load(org_id=org_id, conversation_id=conversation_id)
    ordinal_by_call_id = {b.tool_call_id: b.conversation_ordinal for b in bindings}
    # Walk events for prior runs, build observations, stamp ordinal from the map.
    ...
```

`_index_ordinals_from_events` is deleted. The "Prior observations" prompt context format is unchanged — only the ordinal source changes.

### 2.7 Frontend — drop the fallback

Files modified:

- [`citedToolSources.ts`](../../apps/frontend/src/features/chat/chatModel/citedToolSources.ts): delete `toolInvocationCallIdsInOrder`, `SYNTHETIC_FE_TOOL_NAMES`, the `toolCallIdsInOrder` parameter, and the fallback branch. Skip a citation_made link when `source_tool_call_id` is empty.
- [`citationsContext.tsx`](../../apps/frontend/src/features/chat/components/citations/citationsContext.tsx): remove `toolCallIdsInOrder` from the context; simplify `useResolvedOrdinalCitation` to read only `link.source_tool_call_id`.
- [`OrdinalCitationChip.tsx`](../../apps/frontend/src/features/chat/components/citations/OrdinalCitationChip.tsx): chip's `data-citation-id` equals `tool:<source_tool_call_id>` directly; delete the synthetic `tool-ord:<n>` branch.
- [`ChatScreen.tsx`](../../apps/frontend/src/features/chat/ChatScreen.tsx): remove `toolCallIdsInOrder` plumbing and the in-place `toolInvocationCallIdsInOrder(items)` memo.

New tests pin the invariants: chip's `data-citation-id` matches the row's `citation_id` for the same `source_tool_call_id`; clicking a row scrolls to a chip whose `data-citation-id` equals the row's `citation_id` (no fallback).

### 2.8 Errors

| Code                                               | When                                                                                        | Where surfaced                                                                                                                     |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `ConversationOrdinalConflict`                      | Two allocators race to bind the same conversation                                           | Internal — caught by `allocate_for_tool_call`, triggers reload + retry. Logged at `INFO`. Not user-facing.                         |
| `ValueError("tool_call_id required")`              | Caller invokes `allocate_for_tool_call` with empty `tool_call_id`                           | Internal — indicates a bug in a tool wrapper. Crashes the run. Surfaced as run failure with safe message "internal_runtime_error". |
| `citations.unbound_ordinal_total` metric increment | Resolver matches `[[N]]` but allocator has no binding                                       | Logged at `WARNING`, metric incremented. User-visible: chip renders as `?`.                                                        |
| `tool_call_id` schema validation failure           | `McpToolCallRequest` (or wrapped DuckDuckGo schema) rejects an invalid `tool_call_id` value | LangChain layer raises before `ainvoke`. Surfaced as a normal tool error in the run; the model retries.                            |

### 2.9 Permissions & rate limits

No new public surface. Tenant isolation enforced via RLS on `agent_conversation_tool_ordinals` (matches every other agent_runtime table). Rate-limiting is moot — allocations happen inside the runtime, gated by the existing per-tool rate limits and per-org concurrent-runs ceilings.

---

## 3 · Architecture

### 3.1 Where this lives in the system

```
runtime_worker.handlers.run.RuntimeRunHandler
        │
        ├── ConversationOrdinalAllocator.for_conversation(store=...)   ← Phase 3
        │       │
        │       └── ConversationToolOrdinalStorePort.load(...)         ← Phase 2
        │                  │
        │                  └── postgres adapter / in-memory adapter
        │
        ├── _CitationCapturingTool._arun
        │       └── allocator.allocate_for_tool_call(tool_call_id=..., tool_name=...)
        │               └── store.record(...)                          ← Phase 2/3
        │
        ├── CallMcpTool.ainvoke
        │       └── allocator.allocate_for_tool_call(tool_call_id=..., tool_name="server.tool")
        │               └── store.record(...)                          ← Phase 1+2/3
        │
        ├── CitationResolver.observe_delta
        │       └── allocator.tool_call_id_for(N)                       ← Phase 3
        │       (stamps source_tool_call_id on citation_made events)
        │
        └── ToolObservationIndexBuilder.build (next-turn context)
                └── store.load(...)                                    ← Phase 4
                (joins observations to ordinals by tool_call_id)
```

The allocator is the only writer; the resolver, the cross-turn builder, and the FE are all readers (transitively, via the binding map). The frontend reads exclusively through `citation_made` events on the wire.

### 3.2 Why this lives in **ai-backend**, not backend

Citations are intrinsically tied to the agent's tool-calling lifecycle. The backend owns tenants, MCP registration, OAuth/token state — none of which know about the conversation_ordinal. Putting the binding store in `ai-backend` keeps it co-located with the allocator and the runtime context that owns the active conversation, mirrors the way every other agent-runtime table (`agent_conversations`, `agent_messages`, `agent_runs`, `agent_runtime_events`) is owned. backend-facade has no involvement.

### 3.3 Streaming impact — explicitly **none**

`citation_made` events stream over the existing SSE channel via the existing `RuntimeApiEventType.CITATION_MADE` envelope. The new change is the resolver populates `source_tool_call_id` more reliably; the event's wire shape is identical. SSE replay (`?after_sequence=N`) still works — replayed events read from `agent_runtime_events` exactly as today.

The one non-stream surface that changes is the **start** of a run: the worker now reads the binding map from the new table to construct the allocator. That's a single additional query per run-bind / approval-resume, ~10ms typical, gated by an index.

### 3.4 DRY — what we reuse vs. what we add

**Reused unchanged:**

- `RuntimeApiEventType.CITATION_MADE` — the event type.
- `CitationLink` schema — the wire shape.
- `RuntimeFinalResponsePayload.cited_ordinals` — the sealed-ordinal summary.
- `_CitationHint.append_to` — the result-hinting suffix.
- `CitationResolver.observe_delta`'s regex + per-message offset buffer.
- `RuntimeWorkerDependencies` — same DI pattern, one new field.
- yoyo migration tooling, RLS policy template, port + adapter pattern.
- Frontend `OrdinalCitationChip`, `SourceRow`, `SourcesTab`, `ChatScreen`'s click handlers — same components, fallback branches deleted.

**Added:**

- `agent_conversation_tool_ordinals` table.
- `ConversationToolOrdinalStorePort` + 2 adapters.
- `tool_call_id` field on `McpToolCallRequest`; wrapper schema for DuckDuckGo.
- `ConversationOrdinalConflict` exception.
- `citations.unbound_ordinal_total` metric.
- Integration test suite `test_binding_journeys.py`.

**Deleted:**

- `ConversationOrdinalAllocator.allocate()` (the no-binding API).
- `ConversationOrdinalSeeder` (replaced by `for_conversation`).
- `ToolObservationIndexBuilder._index_ordinals_from_events`.
- FE `toolInvocationCallIdsInOrder`, `SYNTHETIC_FE_TOOL_NAMES`, `toolCallIdsInOrder` plumbing.
- The `tool-ord:<n>` synthetic citation_id and its plumbing.

### 3.5 Sequence — single-tool turn (US-C1)

```
User → frontend POST /v1/conversations/.../runs
frontend → facade → ai-backend runtime_api → enqueue
runtime_worker.handlers.run.RuntimeRunHandler
    1. ConversationOrdinalAllocator.for_conversation(store) → allocator (counter=0, map={})
    2. bind_for_run(allocator)
    3. graph.invoke
        graph dispatches web_search(tool_call_id="call_abc")
        _CitationCapturingTool._arun:
            ordinal = await allocator.allocate_for_tool_call(
                tool_call_id="call_abc",
                tool_name="web_search",
                run_id=run.run_id,
            )
            store.record(...)  → returns ordinal=1
            result = await inner._arun(...)
            result = _CitationHint.append_to(result, ordinal=1, tool_name="web_search")
        graph emits TOOL_CALL_FINISHED
    4. model streams "X is Y [[1]]"
    5. CitationResolver.observe_delta sees [[1]]:
        tool_call_id = allocator.tool_call_id_for(1) → "call_abc"
        emit CITATION_MADE(link=CitationLink(
            conversation_ordinal=1,
            message_id="msg_42",
            prose_offset=12,
            prose_length=3,
            source_tool_call_id="call_abc",
        ))
    6. final_response.cited_ordinals = [1]
    7. unbind allocator

Frontend receives CITATION_MADE, applies to citationLinkRegistry.
Chip renders: <a class="citation-chip" data-citation-id="tool:call_abc">1</a>
Sources row renders: <li data-citation-id="tool:call_abc">[1] web_search — X · Cited 1×</li>
Click chip → onOrdinalSelect("tool:call_abc") → openOn("sources", { focusCitationId: "tool:call_abc" }) → row scrolls into view.
Click row's ↗ → scrollChatToCitation("tool:call_abc") → finds chip, pulses.
```

### 3.6 Sequence — MCP with approval (US-C4)

```
Run R1 starts. allocator counter=0.
Model dispatches discover_mcp_servers(call_id="call_d1")
  _CitationCapturingTool: allocate_for_tool_call("call_d1") → ordinal 1, store.record(1, "call_d1", run=R1)
Model dispatches load_mcp_server(call_id="call_l1") → ordinal 2, run=R1
Model dispatches call_tool(call_id="call_c1", server="linear", tool="list_issues")
  CallMcpTool.ainvoke reads parsed_input.tool_call_id="call_c1"
  Permission policy intercepts: emits APPROVAL_REQUESTED.
  Run R1 pauses. allocator counter=2 (no bind for call_c1 yet).

User approves.
RuntimeApprovalHandler:
  Builds allocator via ConversationOrdinalAllocator.for_conversation(...)
  store.load(...) → bindings = [(1, "call_d1"), (2, "call_l1")]
  allocator counter=2, map={1: "call_d1", 2: "call_l1"}
  bind_for_run(allocator)
  resume graph

Resume re-dispatches call_tool(call_id="call_c1") (LangGraph idempotency on call_id).
  allocator.allocate_for_tool_call("call_c1") → counter=3, store.record(3, "call_c1", run=R1).
  Tool result returned with hint [[3]].
Model streams "PAR-9 [[3]], PAR-8 [[3]] …"
Resolver emits CITATION_MADE(ordinal=3, source_tool_call_id="call_c1") ×N (one per chip).

Sources tab: [1] linear.list_issues — me · Cited Nx
No collision with prior turns. No collision across approval boundaries.
```

The critical difference from today: the allocator's state is **read from persistence**, not re-counted from events. The pause/resume preserves bindings exactly because the bindings live in a table.

### 3.7 Sequence — cross-turn (US-C6)

```
Turn 1 (run R1): conv has bindings {1: "call_d1", 2: "call_l1", 3: "call_c1"}.
Turn 2 (new run R2):
  Run handler:
    1. Build ToolObservationIndex via ToolObservationIndexBuilder
        bindings = store.load(org_id, conv_id)
        ordinal_by_call_id = {"call_d1": 1, "call_l1": 2, "call_c1": 3}
        Walk turn-1 events; for each TOOL_RESULT pick its call_id; stamp ordinal from the map.
        Result: observation for call_c1 has conversation_ordinal=3.
        Prompt context: "linear.list_issues( cite as [[3]]; ...) preview: PAR-9, PAR-8…"
    2. ConversationOrdinalAllocator.for_conversation(...) → counter=3, map=above.
    3. bind_for_run.
  Model reads context, sees "cite as [[3]]" for the prior linear result.
  Model writes "PAR-9 (high, due Friday) [[3]]".
  Resolver: tool_call_id_for(3) → "call_c1". Stamps source_tool_call_id="call_c1".
  CITATION_MADE emitted on R2 with source_tool_call_id="call_c1".

Frontend (turn 2 message reducer) inserts the citation_made link under run_id=R2.
The Sources-tab projection (citedToolSources, runId=null after the prior PR) scans all runs.
The bucket for "call_c1" picks up turn 1 + turn 2 citations: citation_count = N + 1.
```

The same `tool_call_id="call_c1"` is the routing key in both turns. Click chip in turn 2 → opens row that aggregates citations from both turns. No collision.

### 3.8 Edge cases

- **Idempotent retry of `allocate_for_tool_call`.** Same `tool_call_id` ⇒ returns the existing ordinal (in-memory fast path or store UPSERT collapse). Critical for the resume case where LangGraph re-dispatches the same `call_id`.
- **Concurrent allocator instances** (supervisor + subagent attempt to bind in parallel). One wins the `UNIQUE(conversation_id, tool_call_id)` insert; the other catches `ConversationOrdinalConflict`, reloads, returns the canonical ordinal.
- **Subagent `tool_call_id` collisions** with the parent agent. LangGraph guarantees unique `call_id`s per dispatch within the graph; subagents run with their own `call_id` namespace inside the parent's. Verified by US-C10.
- **Hallucinated ordinal** (US-C8). Resolver emits empty `source_tool_call_id`; chip renders `?`; metric increments. We deliberately don't try to "guess" — that's how we got here.
- **Replay / archive** (US-C9). Conversations created before this PR have no rows in the new table. A one-shot backfill (§3.10) reconstructs bindings from existing `tool_call_started` events, mirroring what the deleted seeder used to compute. After backfill, every conversation reads the table identically.
- **Branching / forking conversations.** Forks copy `agent_messages`; they should also copy `agent_conversation_tool_ordinals`. The fork handler (PR 6.2 lineage) already deep-copies conversation rows; we add this table to its copy list.
- **Soft-deleted conversation revived.** Bindings live and die with the conversation row; if a conversation is soft-deleted then revived (admin-tier path), its bindings come back too. No state loss.
- **Run cancellation mid-allocation.** The allocator writes through to the store inside the tool path. Cancellation that interrupts between `record` and the rest of the tool body leaves the binding written but no `tool_call_finished` event. That's fine — the binding is still valid; the tool result simply never resolved. Subsequent runs see the ordinal as consumed.
- **Schema migration of `McpToolCallRequest`.** Adding `tool_call_id: Annotated[str, InjectedToolCallId] = ""` is backwards-compatible at the model-prompt layer (the schema's JSON description gets one extra field that LangChain handles). Existing replay payloads without the field still parse (Pydantic default). New events always carry it.

### 3.9 Test plan

Each of the four worst journeys gets a scripted integration test under `tests/integration/citations/test_binding_journeys.py`:

1. **`test_multi_turn_same_tool_no_collision`** — turn 1 calls web_search; turn 2 calls web_search; assert the two ordinals are distinct, the two bindings are distinct, the Sources tab in turn 2 has both rows.
2. **`test_mcp_with_approval_preserves_binding`** — run that hits an approval; resume; assert the post-resume ordinal is `max(pre-pause) + 1`, the binding map round-trips, no ordinal is reused.
3. **`test_mixed_mcp_and_web_in_one_response`** — single turn, both tools, both cited; assert two rows, click handshake matches.
4. **`test_cross_turn_resolves_to_same_tool_call`** — turn 1 cites `[[N]]` for linear; turn 2 cites `[[N]]` for the same call; assert `source_tool_call_id` is identical in both events; assert the Sources row aggregates both citations.

All four assert the universal invariants:

- `count(citation_made events with source_tool_call_id == "")` is 0 for the journey.
- `count(rows in agent_conversation_tool_ordinals where conversation_ordinal duplicates) == 0`.
- For every distinct `(conversation_ordinal, tool_call_id)` pair in the conversation's events, exactly one row in the binding table.
- The Sources tab projection in the FE produces the expected row count.

Unit tests (per file):

- `tests/unit/runtime_adapters/in_memory/test_conversation_tool_ordinal_store.py` — adapter conformance (record idempotency, conflict raised on different ordinal for same call_id, load returns sorted).
- `tests/unit/runtime_adapters/postgres/test_conversation_tool_ordinal_store.py` — same suite against the postgres adapter.
- `tests/unit/agent_runtime/capabilities/test_conversation_ordinals.py` — `for_conversation` returns the right counter + map; `allocate_for_tool_call` is idempotent on same `tool_call_id`; `ConversationOrdinalConflict` triggers reload-and-retry.
- `tests/unit/agent_runtime/capabilities/test_citation_resolver.py` — extended to assert every match stamps `source_tool_call_id`; metric increments on missing binding.
- `tests/unit/agent_runtime/capabilities/mcp/middleware/test_call_tool.py` — extended to assert `tool_call_id` is read from `parsed_input` and passed to `allocate_for_tool_call`.
- `tests/unit/runtime_worker/test_tool_observations.py` — `_index_ordinals_from_events` deletion verified; ordinals come from store.

Frontend tests (vitest):

- `OrdinalCitationChip.test.tsx` — chip's `data-citation-id` always equals `tool:<source_tool_call_id>`; chip with empty `source_tool_call_id` renders `?` and is inert.
- `citedToolSources.test.ts` — fallback tests deleted; new test asserts unresolved citations are skipped (not coerced via fallback).
- `ChatScreen.integration.test.tsx` — click chip → opens Sources tab; click row's ↗ → highlights chip with matching `data-citation-id`.

### 3.10 Rollout

**Phase 0 — Preparation (no behaviour change).**

- Land this PRD.
- Add the migration file but keep the table unused; verify migration apply + rollback in dev.

**Phase 1 — `tool_call_id` plumbing.**

- Add `tool_call_id: InjectedToolCallId` to `McpToolCallRequest`.
- Wrap DuckDuckGo schema at registration time.
- `CallMcpTool` calls `allocate_for_tool_call`.
- `allocate()` is **deleted** (or, if any caller remains, it's converted to log + raise).
- Verify in dev: zero `call_id=''` log lines.

**Phase 2 — Persistence layer.**

- Add port + 2 adapters.
- Backfill script `services/ai-backend/scripts/backfill_conversation_tool_ordinals.py` that walks existing `agent_runtime_events` and reconstructs bindings (same logic the deleted seeder used). Idempotent. Run on staging.

**Phase 3 — Allocator + resolver refactor.**

- `ConversationOrdinalAllocator.for_conversation(store=...)` becomes the only constructor used by the run + approval handlers.
- `CitationResolver` strict stamping.
- Behind a flag `CITATIONS_USE_BINDING_STORE=true` for one CI cycle; flip on, then delete the flag.

**Phase 4 — Cross-turn refactor.**

- `ToolObservationIndexBuilder` reads from the store. Delete `_index_ordinals_from_events`.

**Phase 5 — Frontend prune.**

- Delete the fallback. Update tests. Update [`citedToolSources.ts`](../../apps/frontend/src/features/chat/chatModel/citedToolSources.ts) and [`citationsContext.tsx`](../../apps/frontend/src/features/chat/components/citations/citationsContext.tsx).

**Phase 6 — Verification.**

- Land the four-journey integration test suite.
- Manual playback of all journeys.
- Update [`03-citations-model-declared.md`](03-citations-model-declared.md) to point at this doc.

Each phase is independently shippable. Phases can land in separate PRs if review pressure dictates; the structure here is the minimum cohesive split.

**Backout plan:** revert each phase's PR. The migration's `.rollback.sql` drops the table cleanly. The legacy seeder + ordinal-position fallback can be restored from git if needed (we keep one commit before deletion that includes both new and old paths).

### 3.11 Open questions

- **Should the binding table carry `org_id` redundantly with the FK to `agent_conversations`?** Answer: yes. Every other agent_runtime table mirrors org_id for RLS. Spec'd in §2.1.
- **Should `allocate_for_tool_call` be sync-with-cache or always-async?** It's already async (the store call is async). Tools that allocate are inside async tool paths. No call site changes.
- **Subagent runs binding in parent's conversation.** Confirmed by US-C10 and §3.8. The subagent harness already runs with the parent's `runtime_context.conversation_id`; the allocator binding inherits it. No new plumbing.
- **What about `ask_a_question`, `load_mcp_server`, etc. — do they need `InjectedToolCallId`?** They already do (their adapters are subclasses of `BaseTool` whose default schemas LangChain populates). Audit confirms in Phase 1.
- **Do we expose a "binding inspector" admin API?** No. If support needs it they query the table directly. Out of scope.

---

## 4 · Acceptance checklist

### ai-backend

- [ ] Migration `0026_conversation_tool_ordinals.sql` + `.rollback.sql` apply cleanly on dev DB.
- [ ] `ConversationToolOrdinalStorePort` + in-memory + postgres adapters + tests.
- [ ] `ConversationOrdinalAllocator.for_conversation(store=...)` is the only construction path used by run + approval handlers.
- [ ] `ConversationOrdinalSeeder` deleted; `seed_from_event_log` no longer exists.
- [ ] `ConversationOrdinalAllocator.allocate()` (no-binding path) deleted.
- [ ] `McpToolCallRequest` carries `tool_call_id: Annotated[str, InjectedToolCallId]`.
- [ ] `CallMcpTool.ainvoke` passes `tool_call_id` to `allocate_for_tool_call`.
- [ ] DuckDuckGo args schema is wrapped to inject `tool_call_id` at registration.
- [ ] `CitationResolver` stamps `source_tool_call_id` on every match; emits `citations.unbound_ordinal_total` metric on miss.
- [ ] `ToolObservationIndexBuilder` reads from the store; `_index_ordinals_from_events` deleted.
- [ ] Backfill script exists and is idempotent; staging runbook documented.

### Frontend

- [ ] `toolInvocationCallIdsInOrder` and `SYNTHETIC_FE_TOOL_NAMES` deleted from [`citedToolSources.ts`](../../apps/frontend/src/features/chat/chatModel/citedToolSources.ts).
- [ ] `toolCallIdsInOrder` removed from `CitationsContextValue`, `CitationsProvider` props, and `ChatScreen` plumbing.
- [ ] `useResolvedOrdinalCitation` simplifies to `link.source_tool_call_id` lookup; `tool-ord:<n>` synthetic id deleted.
- [ ] Chip's `data-citation-id` always equals `tool:<source_tool_call_id>`.
- [ ] Empty `source_tool_call_id` renders `?` (existing behaviour, now reserved for hallucinated ordinals).
- [ ] Vitest suite green; integration assertions on chip↔row roundtrip pass.

### Tests

- [ ] `tests/integration/citations/test_binding_journeys.py` covers US-C1, C2, C4, C5, C6, C7. Each asserts: zero unbound citation_made, zero ordinal collisions, expected Sources row count, chip↔row roundtrip.
- [ ] Unit tests for both adapters' conformance.
- [ ] Unit tests for allocator idempotency + conflict reload-and-retry.
- [ ] Unit test asserting `_index_ordinals_from_events` is gone (import removed from `tool_observations.py`).
- [ ] Frontend regression test for chip↔row binding without fallback.

### Docs

- [ ] [`03-citations-model-declared.md`](03-citations-model-declared.md) updated with a header pointer to this doc and a note that the positional-counter scheme it described is replaced.
- [ ] This file linked from [`docs/new-design/0-OVERALL_PLAN.md`](0-OVERALL_PLAN.md).

### System-level

- [ ] `make test` green.
- [ ] ai-backend pytest suite green.
- [ ] Frontend typecheck + build + vitest green.
- [ ] Manual playback of US-C1 through US-C10 in dev passes.
- [ ] Logs from a fresh dev session show zero `call_id=''` lines and zero `citations.unbound_ordinal_total` increments.

---

## 5 · References

- [`03-citations-model-declared.md`](03-citations-model-declared.md) — design this PR fixes the implementation of.
- [`services/ai-backend/src/agent_runtime/capabilities/conversation_ordinals.py`](../../services/ai-backend/src/agent_runtime/capabilities/conversation_ordinals.py) — current allocator + seeder.
- [`services/ai-backend/src/agent_runtime/capabilities/citation_resolver.py`](../../services/ai-backend/src/agent_runtime/capabilities/citation_resolver.py) — current resolver.
- [`services/ai-backend/src/agent_runtime/capabilities/citation_capturing_tool.py`](../../services/ai-backend/src/agent_runtime/capabilities/citation_capturing_tool.py) — capturing wrapper + `_CitationHint`.
- [`services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py`](../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py) — MCP tool dispatcher (today's `allocate()` site).
- [`services/ai-backend/src/runtime_worker/tool_observations.py`](../../services/ai-backend/src/runtime_worker/tool_observations.py) — current cross-turn observation index.
- [`services/ai-backend/src/runtime_worker/handlers/run.py`](../../services/ai-backend/src/runtime_worker/handlers/run.py), [`approval.py`](../../services/ai-backend/src/runtime_worker/handlers/approval.py) — allocator binding sites.
- [`apps/frontend/src/features/chat/chatModel/citedToolSources.ts`](../../apps/frontend/src/features/chat/chatModel/citedToolSources.ts) — current FE projection with the fallback.
- [`apps/frontend/src/features/chat/components/citations/`](../../apps/frontend/src/features/chat/components/citations/) — chip, source row, context.

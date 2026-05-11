# Refactor PRD — Citation infrastructure consolidation (P14)

**Status:** Draft
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §2.2](../architecture/refactor-audit.md#22-8-files-of-citation-infrastructure)
**Phase:** 4 — Targeted decoupling
**Roadmap entry:** [`00-roadmap.md` → P14](00-roadmap.md)

---

## 1. Problem

Citation handling is fragmented across **8 files in 3 clusters**:

| File                                                                                                               | Cluster               | Stated role                            |
| ------------------------------------------------------------------------------------------------------------------ | --------------------- | -------------------------------------- |
| [`capabilities/citations.py`](../../src/agent_runtime/capabilities/citations.py)                                   | C6 capabilities       | `CitationRegistry`                     |
| [`capabilities/citation_resolver.py`](../../src/agent_runtime/capabilities/citation_resolver.py)                   | C6 capabilities       | `CitationLedger`                       |
| [`capabilities/citation_projection.py`](../../src/agent_runtime/capabilities/citation_projection.py)               | C6 capabilities       | Projects `[[N]]` markers into text     |
| [`capabilities/citation_capturing_tool.py`](../../src/agent_runtime/capabilities/citation_capturing_tool.py)       | C6 capabilities       | Tool that captures sources             |
| [`capabilities/conversation_ordinals.py`](../../src/agent_runtime/capabilities/conversation_ordinals.py)           | C6 capabilities       | `ConversationOrdinalAllocator`         |
| [`capabilities/mcp/middleware/cite_mcp.py`](../../src/agent_runtime/capabilities/mcp/middleware/cite_mcp.py)       | C6 MCP middleware     | `CitationProjectingMcpMiddleware`      |
| [`execution/providers/citation_pipeline.py`](../../src/agent_runtime/execution/providers/citation_pipeline.py)     | C5 provider streaming | `CitationStreamPipeline`               |
| [`execution/providers/citation_extraction.py`](../../src/agent_runtime/execution/providers/citation_extraction.py) | C5 provider streaming | Provider-specific grounding extraction |

The architecture index ([`docs/architecture/index.md` § C6](../architecture/index.md#c6--capabilities)) acknowledges the design intent:

> "The citation system is fully owned here: `CitationLedger` is the only insert path, `ConversationOrdinalAllocator` is the only place ordinals are assigned, both backed by their respective ports."

The intent is correct — single insert path, single allocator. The implementation has spread that intent across 8 files. Two of the names (`CitationRegistry` and `CitationLedger`) are easy to confuse and the mental model "registry of citations" vs "ledger of citations" is not in any docstring I've seen.

### Symptoms

- New developers ask which to extend (`CitationRegistry` vs `CitationLedger`) and there's no canonical answer in code or docs.
- Provider grounding (Gemini / OpenAI web search) and MCP tool results both feed citations, but through entirely different code paths (`citation_pipeline.py` for providers; `cite_mcp.py` middleware for MCP). The shared idempotency contract `(run_id, connector, doc_id)` is enforced at the ledger but the projection logic is duplicated.
- Sealing at `FINAL_RESPONSE` (per [f5](../architecture/f5-citations.puml)) calls `CitationStorePort.list_for_run` — that one call is the actual sealed contract. Everything else is plumbing.
- The behavior that **subagents share the same conversation_id and use the same allocator + ledger** ([f5](../architecture/f5-citations.puml)) is critical and load-bearing. Spreading this across 8 files makes any refactor scary.

### What this is NOT

- Not a behavior change. Every observable behavior in [`refactor-audit.md` § Behaviors that must be preserved → Citations](../architecture/refactor-audit.md#behaviors-that-must-be-preserved) survives byte-identical.
- Not a port-shape change. `CitationStorePort`, `ConversationToolOrdinalStorePort`, `SourceStorePort` keep their current method signatures.
- Not a frontend change. Workspace Sources tab continues to read `SourceStorePort.aggregate_for_conversation`.

---

## 2. Goal and non-goals

### Goal

Collapse the 8 citation files into **3 cohesive modules** with clear responsibilities:

1. **`capabilities/citations/service.py` — `CitationService`**
   Single insert path. Owns `CitationLedger` + `CitationRegistry` + `ConversationOrdinalAllocator` as collaborating components inside one class. Backed by `CitationStorePort` + `ConversationToolOrdinalStorePort`. The only object the rest of the runtime injects.

2. **`capabilities/citations/provider_extraction.py` — `ProviderCitationExtractor`**
   Provider-specific grounding extraction. Today's `CitationStreamPipeline` + `citation_extraction.py` collapse into one class with provider-variant strategies (`AnthropicCitationStrategy`, `OpenAICitationStrategy`, `GeminiCitationStrategy`). Output: a normalized `SourceRef` stream that flows to `CitationService.ingest`.

3. **`capabilities/mcp/middleware/citations.py` — `MCPCitationMiddleware`**
   Stays in MCP middleware (it lives in the MCP request/response cycle). Calls into `CitationService.ingest` for storage and uses a shared `MarkerProjector` helper from `service.py` to project `[[N]]` into text. No standalone projection module.

### Non-goals

- Do not change `CitationStorePort` or `ConversationToolOrdinalStorePort` shapes. The repository-pattern collapse is [P19](00-roadmap.md), a separate PRD.
- Do not touch the `SourceStorePort.aggregate_for_conversation` path used by the Sources tab.
- Do not change event types (`SOURCE_INGESTED`, `CITATION_MADE`) or their payload schemas.
- Do not change the sealed-snapshot contract at `FINAL_RESPONSE` (`payload.citations` shape).
- Do not change `citation_capturing_tool.py`'s tool-facing contract — it remains a capability tool the model can invoke; only its internal implementation moves to call `CitationService.ingest` instead of `CitationLedger.ingest`.

### Success criteria

- 8 files reduced to 3 (`service.py`, `provider_extraction.py`, `citations.py` in MCP middleware) plus the unchanged `citation_capturing_tool.py`.
- A single import path for "give me the citation service" — `from agent_runtime.capabilities.citations import CitationService`.
- All existing tests pass without modification beyond import-path updates.
- New tests prove byte-identical citation output for a representative event corpus from staging (replay 100+ runs through the new and old paths; diff `payload.citations` on `FINAL_RESPONSE`).
- No file in `capabilities/` named `citation_*` other than the new `citations/` package.
- No file in `execution/providers/` named `citation_*` (moved into `capabilities/citations/`).
- A short doc in [`docs/architecture/`](../architecture/) explaining "citations are conversation-scoped, idempotent, sealed at FINAL_RESPONSE."

---

## 3. Systems touched

This is a code investigation done from diagrams + the architecture index. Read the actual files first; this list is the expected shape, not a contract.

### 3.1 Files moved / merged

| From                                         | Into                                                             | Notes                                                                                  |
| -------------------------------------------- | ---------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `capabilities/citations.py`                  | `capabilities/citations/service.py` (`CitationService.registry`) | `CitationRegistry` → private collaborator inside `CitationService`                     |
| `capabilities/citation_resolver.py`          | `capabilities/citations/service.py` (`CitationService.ingest`)   | `CitationLedger.ingest` becomes `CitationService.ingest`                               |
| `capabilities/conversation_ordinals.py`      | `capabilities/citations/service.py` (`CitationService.allocate`) | `ConversationOrdinalAllocator.record` becomes `CitationService.allocate_ordinal`       |
| `capabilities/citation_projection.py`        | `capabilities/citations/service.py` (`MarkerProjector` helper)   | Standalone module → helper class                                                       |
| `execution/providers/citation_pipeline.py`   | `capabilities/citations/provider_extraction.py`                  | `CitationStreamPipeline` → `ProviderCitationExtractor.consume_stream`                  |
| `execution/providers/citation_extraction.py` | `capabilities/citations/provider_extraction.py`                  | Per-provider extraction logic → strategy classes inside `ProviderCitationExtractor`    |
| `capabilities/mcp/middleware/cite_mcp.py`    | `capabilities/mcp/middleware/citations.py`                       | Renamed for clarity; calls `CitationService.ingest` instead of `CitationLedger.ingest` |

### 3.2 Files unchanged

- [`capabilities/citation_capturing_tool.py`](../../src/agent_runtime/capabilities/citation_capturing_tool.py) — tool-facing contract preserved; internals updated to call `CitationService`.
- [`persistence/ports.py`](../../src/agent_runtime/persistence/ports.py) — `CitationStorePort` and `ConversationToolOrdinalStorePort` unchanged.
- All adapters under `runtime_adapters/`.
- All record types under `persistence/records/`.

### 3.3 Files added

- `capabilities/citations/__init__.py` — re-exports `CitationService`, `SourceRef`, `Citation`, ordinal types.
- `capabilities/citations/service.py` — single class `CitationService` with `registry`, `allocate_ordinal`, `ingest`, `project_markers`, `snapshot` methods.
- `capabilities/citations/provider_extraction.py` — `ProviderCitationExtractor` plus per-provider strategies.
- `capabilities/citations/contracts.py` — Pydantic models that previously lived in `citations.py` and `citation_resolver.py`. Surfaced for tests + cross-module imports.

---

## 4. Architecture

### 4.1 Module boundary

```
agent_runtime/capabilities/citations/
├── __init__.py                  # public surface
├── contracts.py                 # SourceRef, Citation, OrdinalBinding (Pydantic)
├── service.py                   # CitationService (the single insert path)
├── provider_extraction.py       # ProviderCitationExtractor + 3 strategies
└── (no other files)

agent_runtime/capabilities/mcp/middleware/
└── citations.py                 # MCPCitationMiddleware (renamed from cite_mcp.py)
                                 # — depends on CitationService, not on internal modules

agent_runtime/capabilities/citation_capturing_tool.py
                                 # unchanged contract; internals call CitationService
```

### 4.2 Public surface (Pydantic contracts)

These move from existing files into `contracts.py` unchanged. Field-level shape preserved.

```python
class SourceRef(BaseModel):
    connector: str               # e.g. "linear", "notion", "web"
    doc_id: str
    url: HttpUrl | None = None
    title: str | None = None
    snippet: str | None = None
    metadata: JsonObject = Field(default_factory=dict)

class OrdinalBinding(BaseModel):
    conversation_id: str
    tool_call_id: str
    conversation_ordinal: PositiveInt
    tool_name: str

class Citation(BaseModel):           # what FINAL_RESPONSE.payload.citations contains
    conversation_ordinal: PositiveInt
    source: SourceRef
    run_id: str
    connector: str
    doc_id: str
```

### 4.3 `CitationService` surface

```python
class CitationService:
    def __init__(
        self,
        citation_store: CitationStorePort,
        ordinal_store: ConversationToolOrdinalStorePort,
        event_producer: RuntimeEventProducer,
    ) -> None: ...

    async def allocate_ordinal(
        self,
        conversation_id: str,
        tool_call_id: str,
        tool_name: str,
    ) -> OrdinalBinding: ...
    # idempotent on tool_call_id

    async def ingest(self, run_id: str, source: SourceRef) -> None: ...
    # idempotent on (run_id, connector, doc_id); emits SOURCE_INGESTED

    def project_markers(
        self,
        text: str,
        ordinals: Iterable[OrdinalBinding],
    ) -> str: ...
    # pure helper; no IO

    async def snapshot(self, run_id: str) -> list[Citation]: ...
    # called at FINAL_RESPONSE; reads CitationStorePort.list_for_run
```

`CitationRegistry` (the in-memory cache of what's been ingested in the current process) is a private collaborator. It is rebuilt from `CitationStorePort.list_for_run` on resume / crash recovery — that path stays.

---

## 5. Edge cases

Every edge case in [`refactor-audit.md` § Behaviors that must be preserved → Citations](../architecture/refactor-audit.md#behaviors-that-must-be-preserved) and the [f5](../architecture/f5-citations.puml) flow gets a pinned test:

1. **Conversation-scoped ordinals across turns.** Turn 1 cites Linear → ordinal 1. Turn 2 cites Notion → ordinal 2 (not 1). Same conversation, same allocator.
2. **Subagents share the namespace.** Subagent runs inside parent conversation — its allocator hits the same `ConversationToolOrdinalStorePort`, gets the next ordinal in sequence (e.g. 3 if turns 1–2 used 1+2).
3. **Idempotent ordinal on retry.** Same `tool_call_id` → same `conversation_ordinal` returned. No duplicate row.
4. **Idempotent citation insert.** Same `(run_id, connector, doc_id)` → `insert_or_get` returns the existing row, no duplicate `SOURCE_INGESTED` event.
5. **Sealed snapshot at FINAL_RESPONSE.** `CitationService.snapshot(run_id)` returns the citations list in ordinal order; this is what the FINAL_RESPONSE event's `payload.citations` carries.
6. **Reconstruction from store on resume.** Worker crash mid-turn → next worker rebuilds `CitationRegistry` from `CitationStorePort.list_for_run`. New ingests after resume use the same ordinals already assigned for matching `tool_call_id`s.
7. **Multi-source within one tool result.** A single MCP `linear_search` returning 5 sources → 5 idempotent inserts. Marker projection `[[N]]` → `[[N+4]]` updates the result text. This becomes a candidate for batching in [P7](00-roadmap.md), but stays sequential here.
8. **Provider grounding chunks interleaving with MCP results.** Gemini emits grounding metadata mid-stream → `ProviderCitationExtractor` pulls a `SourceRef`, calls `CitationService.ingest`. MCP middleware does the same on its own results. Both feed the same conversation-scoped allocator.
9. **`citation_capturing_tool` invocation.** Tool emits a manual capture → calls `CitationService.ingest` with `connector="user_capture"`. Idempotency keyed on whatever the existing tool uses.
10. **`display=OMITTED` thinking mode.** Anthropic reasoning is emitted but not surfaced to client; provider extractor still pulls grounding metadata from any thinking blocks that contain it (verify in code — may not apply).

---

## 6. Security considerations

- `CitationStorePort.insert_or_get` is the sole write path. All other inserts continue to be unauthorized.
- `SourceRef.metadata` is an opaque JSON object. It is currently redacted by `ObservabilityRedactor` when emitted in `SOURCE_INGESTED.payload.metadata`. The redactor invocation point (Pydantic field validator on `payload`) does not move; this PRD does not affect it.
- Subagent isolation: subagents share the conversation citation namespace **by design**. This PRD must not accidentally introduce a per-subagent registry.

---

## 7. Observability

No new metrics, logs, or events. Existing events keep their schemas and emission points:

| Event             | Emitted by                        | Now emitted by             |
| ----------------- | --------------------------------- | -------------------------- |
| `SOURCE_INGESTED` | `CitationLedger.ingest`           | `CitationService.ingest`   |
| `CITATION_MADE`   | `citation_resolver` resolver path | `CitationService.snapshot` |

Trace context (`trace_id`, `span_id`, `parent_span_id`) propagates through `CitationService` calls the same way it does today.

---

## 8. Risks

| Risk                                                                         | Mitigation                                                                                                                                                               |
| ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------- | ------------------- | --------------------------------------------------------------------------------- |
| Silent drift in ordinal allocation logic when consolidating into one class   | Pin a compat-fixture test: replay 100 staged runs through old and new paths, diff `(conversation_id, tool_call_id) → conversation_ordinal` mappings byte-for-byte        |
| Provider extraction strategies miss a quirk of one provider                  | Keep the per-provider strategy split inside `ProviderCitationExtractor`. Add a fixture per provider (Anthropic / OpenAI / Gemini) replaying real grounding stream chunks |
| MCP marker projection regresses (`[[N]]` placement off-by-one)               | Snapshot test: feed real MCP responses through the new middleware, diff projected text                                                                                   |
| `CitationRegistry` rebuild on resume diverges                                | Test: simulate worker crash mid-turn, rebuild registry from store, verify next ingest gets ordinal `N+1` not `1`                                                         |
| `citation_capturing_tool` breaks for tool consumers                          | Tool's external schema (`tool_card.parameters`, `tool_card.response`) is unchanged; only its internal call site moves. Pin a tool-invocation test                        |
| Workspace Sources tab regresses                                              | `SourceStorePort.aggregate_for_conversation` path is untouched; verify with one E2E test that hits `/v1/workspace/conversations/{id}/sources`                            |
| Hidden import-path consumers outside `agent_runtime/capabilities/citations/` | Grep before merging: `git grep -E "from agent_runtime\.capabilities\.(citations                                                                                          | citation_resolver | citation_projection | conversation_ordinals)"` — every match must be updated to the new package surface |
| Cross-process `CitationStorePort` race during high-fan-out tool result       | Existing `insert_or_get` idempotency on `(run_id, connector, doc_id)` already covers this; no change                                                                     |

---

## 9. Unit testing requirements

### 9.1 New tests

1. **Compat fixture — ordinal allocation.** Replay a fixed set of `(conversation_id, tool_call_id, tool_name)` tuples through old and new code paths; assert `conversation_ordinal` mapping is byte-identical.
2. **Compat fixture — citation insert.** Same approach for `(run_id, connector, doc_id) → CitationRecord`.
3. **Snapshot fixture — provider extraction.** Per-provider grounding stream fixtures (Anthropic citations API output, OpenAI Responses API grounding, Gemini grounding metadata). Diff extracted `SourceRef` lists.
4. **Snapshot fixture — MCP marker projection.** Real Linear / Notion responses as JSON fixtures; diff projected `[[N]]` placement.
5. **Snapshot fixture — FINAL_RESPONSE payload.** A small set of multi-turn runs replayed end to end; diff `FINAL_RESPONSE.payload.citations` exactly.
6. **Crash-resume reconstruction.** Manually clear in-memory registry; verify next ingest reads from store and assigns the next ordinal correctly.
7. **Subagent ordinal sharing.** Multi-agent fixture run; assert subagent ordinals interleave with supervisor ordinals in conversation order.

### 9.2 Existing tests touched

- All tests under `tests/unit/agent_runtime/capabilities/test_citation_*.py` (every `citation_*` file under capabilities probably has one) — update imports only. No behavior assertions change.
- All tests under `tests/unit/agent_runtime/execution/providers/test_citation_*.py` — same.
- Integration test for `CitationProjectingMcpMiddleware` (probably under `tests/integration/`) — update to import `MCPCitationMiddleware` from the new path.

### 9.3 Tests deleted

- None expected. Every removed file is replaced by an equivalent module; tests follow.

---

## 10. Rollback plan

- The change ships as one PR (no in-flight feature flag). Citation behavior is part of every run; flagging would mean two parallel implementations and double the failure surface.
- Rollback = revert the PR. No data migration; the schema doesn't change.
- All idempotency keys are stable across the refactor — runs that started before merge and complete after merge resume cleanly.

---

## 11. Pre-implementation checklist

Run before writing code:

1. `git grep -l "citation"` in `services/ai-backend/src/` to find every consumer.
2. Read each of the 8 source files end-to-end. Verify the role descriptions in §1 match what the code actually does. Update this PRD if not.
3. Run the existing citation tests; confirm they all pass on `main`.
4. Capture a 100-run sample from staging via `runtime_events` for compat-fixture inputs.
5. Verify nothing outside `services/ai-backend/` imports from `agent_runtime.capabilities.citation_*`.

---

_Per the team's spec-first workflow ([`docs/CLAUDE.md`](../CLAUDE.md)): do not start implementation until this PRD is reviewed. If any edge case in §5 is hard to preserve in the consolidated shape, raise it — do not drop it silently._

# Refactor PRD — Citations (P14) — **RETRACTED**

**Status:** Retracted (rewritten 2026-05-11 after reading the code)
**Original claim:** "8 citation files duplicate one another and should collapse to 3."
**Why retracted:** The original PRD was drafted from the architecture diagrams without reading the source. The code review found that the seven citation files implement three distinct, non-overlapping subsystems with clear separation of concerns. There is no duplication to consolidate.

---

## What's actually in the code

Reading [`agent_runtime/capabilities/`](../../src/agent_runtime/capabilities/) reveals **three separate subsystems** sharing the citation vocabulary:

### Subsystem A — Source ledger (tool & provider citations → `[c<base36>]`)

[`capabilities/citations.py`](../../src/agent_runtime/capabilities/citations.py) (`CitationLedger`, `SourceRef`)

- One ledger per run. Idempotent on `(source_connector, source_doc_id)`.
- Allocates **citation ordinals** (1, 2, 3, …) rendered as base36 tokens (`[c1]`, `[c2]`, …, `[czh]`).
- Single insert path: `register` / `register_many` both route to `_register_internal`. That's the **single source of truth** I would have asked for in a review.
- ContextVar-bound per run; tools call `await CitationLedger.cite(source)` without threading runtime context through signatures.
- Wire events: `SOURCE_INGESTED` (singular) / `SOURCES_INGESTED` (batch — P7 batch path).

### Subsystem B — Tool-call ordinal allocator (model-declared citations → `[[N]]`)

[`capabilities/conversation_ordinals.py`](../../src/agent_runtime/capabilities/conversation_ordinals.py) (`ConversationOrdinalAllocator`)

- One allocator per **conversation** (not per run). Cross-turn-aware.
- Allocates **tool-call ordinals** (1, 2, 3, …) rendered as decimal markers `[[1]]`, `[[2]]`, … in the model's prose.
- Backed by the persistent `ConversationToolOrdinalStorePort` so an ordinal allocated for `tool_call_id` X stays bound to X across approval pauses, worker restarts, and cross-turn references.
- Idempotent on `tool_call_id`. Handles `ConversationOrdinalConflict` (concurrent allocator beat us) with one reload+retry.

### Subsystem C — Model-text watcher (resolves `[[N]]` → `CITATION_MADE`)

[`capabilities/citation_resolver.py`](../../src/agent_runtime/capabilities/citation_resolver.py) (`CitationResolver`)

- Watches streamed assistant text for `[[N]]` tokens.
- Resolves N through `ConversationOrdinalAllocator.tool_call_id_for(ordinal)`.
- Emits one `CITATION_MADE` event per first-occurrence per `(prose_offset, ordinal)` (idempotent on stream resume).
- Best-effort: a resolver exception is caught and logged; streaming never breaks.

### Glue (not a subsystem)

- [`capabilities/citation_projection.py`](../../src/agent_runtime/capabilities/citation_projection.py) — stateless extractor that pattern-matches four tool-result shapes (Anthropic content blocks, generic results list, single resource, top-level dict list) and routes to `CitationLedger.register{,_many}`. Tools get citations free.
- [`capabilities/citation_capturing_tool.py`](../../src/agent_runtime/capabilities/citation_capturing_tool.py) — a tool the model can invoke to manually capture a source.
- [`execution/providers/citation_pipeline.py`](../../src/agent_runtime/execution/providers/citation_pipeline.py) + [`citation_extraction.py`](../../src/agent_runtime/execution/providers/citation_extraction.py) — pulls Anthropic / OpenAI grounding citations out of provider streams and routes to the same ledger.
- [`capabilities/mcp/middleware/cite_mcp.py`](../../src/agent_runtime/capabilities/mcp/middleware/cite_mcp.py) — wraps MCP tool results and routes through `CitationProjector`.

Every provider, every tool, every MCP middleware funnels through **one** insert path on `CitationLedger`. Every model-declared `[[N]]` resolves through **one** allocator. The single-source-of-truth rule is already followed.

---

## What the original PRD got wrong

| Claim                                                         | Reality                                                                                                                                                             |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `CitationLedger` and `CitationRegistry` are duplicates        | There is no `CitationRegistry`. The ledger has a private `_cache` dict — not a separate class.                                                                      |
| `CitationLedger` and `ConversationOrdinalAllocator` overlap   | They allocate **different ordinal namespaces** (`[c<base36>]` source citations vs. `[[decimal]]` tool-call markers). They share zero state and zero responsibility. |
| `citation_projection.py` is duplicate plumbing                | It is a stateless extractor of four well-defined tool-result shapes. Without it, every tool would need bespoke citation wiring.                                     |
| Provider extraction and MCP extraction are two parallel paths | Both route to `CitationLedger.register{,_many}`. The single ledger IS the convergence point.                                                                        |
| Consolidate 8 files → 3                                       | Collapsing would mix three distinct subsystems into one class, violating substitution + clarity.                                                                    |

---

## What might actually be worth doing (small, optional)

If the citation files genuinely confuse new contributors, these two changes have real value and minimal risk:

### Optional A — Folder organization

Move the seven files into a `capabilities/citations/` subpackage so they live next to each other in tree view:

```
capabilities/citations/
├── __init__.py                  # re-exports CitationLedger, SourceRef, CitationProjector, etc.
├── ledger.py                    # was citations.py
├── projector.py                 # was citation_projection.py
├── capturing_tool.py            # was citation_capturing_tool.py
├── ordinal_allocator.py         # was conversation_ordinals.py
└── resolver.py                  # was citation_resolver.py
```

Provider-side files stay in `execution/providers/` (they ARE provider concerns) but the imports go to the new package.

**Risk:** trivial — file moves + import updates. **Value:** modest — "where do citation things live?" gets one answer.

### Optional B — Architecture doc

Write a short doc at `docs/architecture/citations.md` explaining:

1. The two ordinal systems and when each fires.
2. The single insert path through `CitationLedger._register_internal`.
3. The ContextVar binding lifecycle.
4. Where provider grounding citations enter (`execution/providers/citation_pipeline.py`).

This is the architectural piece a new contributor actually needs. **Risk:** none. **Value:** real.

---

## Decision

Do not consolidate. Do not rename `CitationLedger` or `ConversationOrdinalAllocator` — both names accurately describe distinct concepts. Consider Optional A and Optional B as separate small PRDs only if the team finds the file layout genuinely confusing in practice.

The roadmap entry for P14 in [`00-roadmap.md`](00-roadmap.md) should be removed or marked **Retracted**.

---

## Open questions before any folder reorg

- Does anything _outside_ `agent_runtime/capabilities/` import from these files? If yes, the move needs a back-compat shim or an atomic rename across both ends.
- Does the `execution/providers/citation_pipeline.py` location matter for clarity or is it orphan? (Provider extraction is genuinely a provider concern, but the file might read better in `capabilities/citations/provider_extraction.py`. Trade off file locality vs. import direction.)

# `chatModel/` — read-only reducers for the chat surface

This folder is the pure-state layer that `ChatScreen.tsx` drives. Every
reducer in here is a function `(state, event) → state'` over the runtime
SSE stream — no fetches, no React, no DOM. The output is what the
`assistant-ui` `MessagePrimitive` and the right-rail Workspace pane render.

## Files at a glance

- [`eventReducer.ts`](eventReducer.ts) — top-level dispatcher; routes a
  `RuntimeEventEnvelope` to the right reducer.
- [`citationReducer.ts`](citationReducer.ts) /
  [`citationsRegistry.ts`](citationsRegistry.ts) — per-run citation map for
  inline `[c<id>]` chip resolution (PR 1.1).
- [`sourcesReducer.ts`](sourcesReducer.ts) — per-conversation source
  aggregate keyed by `(connector, doc_id)` for the Workspace → Sources tab
  (PR 1.5 / 3.1).
- [`subagentReducer.ts`](subagentReducer.ts) — Workspace → Agents tab.
- [`draftsRegistry.ts`](draftsRegistry.ts) — Workspace → Draft tab.
- [`presentation.ts`](presentation.ts) — projection helpers; never derives
  activity types from event-name prefixes (per [`apps/frontend/CLAUDE.md`](../../../CLAUDE.md)).

## The dual citation store — deliberate, enforced by test

PR 3.1 §2.4 originally proposed extending `CitationLookup` with
`{ byRun, byConversation }` layers. The implementation that landed
kept **two reducers**:

| Reducer                         | Output shape                                                       | Consumer                                                                                      |
| ------------------------------- | ------------------------------------------------------------------ | --------------------------------------------------------------------------------------------- |
| `citationsRegistry` (PR 1.1)    | `Map<run_id, Map<citation_id, CitationSourceRef>>`                 | inline chip resolver (Streamdown remark plugin) + `MessageSourcesStrip` per assistant message |
| `sourcesReducer` (PR 1.5 / 3.1) | `Map<"connector doc_id", SourceEntry>` (deduped, citation-counted) | Workspace pane → Sources tab                                                                  |

Both are populated **inside the same `applyRuntimeEvent` pass** from the
same ingestion events in [`eventReducer.ts`](eventReducer.ts) — either
`source_ingested` (singular, per-source emitters: provider grounding,
capturing tool) or `sources_ingested` (P7 batched variant, emitted by
`CitationLedger.register_many` once the MCP projector switches to batch
mode). The reducers handle both event types with identical per-citation
semantics; only the wire shape differs (`payload.citation` vs
`payload.citations`). They serve different output shapes (per-run flat
lookup vs. per-doc aggregate) so a single Map would force derivation
work for every consumer.

The risk of two stores is silent drift. We guard with one invariant test
([`citationStore.invariant.test.ts`](citationStore.invariant.test.ts)):
for every ingestion event (singular or batched), the shared fields
(`citation_id`, `source_connector`, `source_doc_id`, `source_url`,
`title`, `snippet`, `freshness_at`) must be byte-identical in both
reducers. Any future PR that forks the reducers fails CI immediately.

This is a deliberate amendment to PR 3.1 §2.4. If the consumer surfaces
ever converge (e.g. Sources tab adopts run-scoped filters), revisit the
merge — but only with a forcing function. Refactoring the two stores
into one without one is the anti-pattern PR 3.5 §1.3 explicitly avoids.

## Streaming guarantees that stay invariant

- Every reducer is **idempotent** on its primary key — replaying a run
  via `?after_sequence=N` produces the same state. Reducer tests assert
  this with same-event-twice fixtures.
- Reducers return the **same identity** when nothing changes (`return prev`)
  to prevent unnecessary React re-renders.
- No reducer reads from React, the network, or `Date.now()`. Time and
  fetches live one layer up in `ChatScreen.tsx`.

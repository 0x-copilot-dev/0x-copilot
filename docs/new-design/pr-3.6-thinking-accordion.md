# PR 3.6 — Thinking accordion: emit + render the model's thought process

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 3 follow-up to [`0-OVERALL_PLAN.md`](0-OVERALL_PLAN.md). Sits next to [`pr-2.1-topbar-chrome-thinking-depth.md`](pr-2.1-topbar-chrome-thinking-depth.md) (which adds the **control**) and closes the matching **render** gap.
> **Owner:** ai-backend (1 stream extractor + 1 emitter call) · frontend (restyle existing `ReasoningGroup` + extend `appendReasoning`) · api-types (no changes — payload shapes already exist) · design-system (none) · facade (none — transparent SSE proxy)
> **Size:** **S.** No new event type, no new table, no new facade route, no new wire field. Net-new code ≈ 130 LOC: ~40 in ai-backend (extract `thinking` / `reasoning_summary` blocks from LangChain `AIMessageChunk` content and emit `reasoning_summary_delta`), ~70 on the frontend (rebuild `ReasoningGroup` to match the design — collapsed by default, dynamic "Thinking…" / "Thought process · Ns" label, italic body, elapsed-time stamp), ~20 of CSS.
> **Depends on:**
>
> - ✅ `RuntimeApiEventType.REASONING_SUMMARY` / `REASONING_SUMMARY_DELTA` (already declared, projected, redacted, persisted) — see [`schemas/common.py:91`](../../services/ai-backend/src/runtime_api/schemas/common.py#L91), [`schemas/events.py:213`](../../services/ai-backend/src/runtime_api/schemas/events.py#L213).
> - ✅ `ModelConfig.reasoning` plumbed through `_anthropic_model_kwargs` / `_openai_model_kwargs` — see [`deep_agent_builder.py:252`](../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py#L252).
> - ✅ FE `appendReasoning` content builder + reducer case for `reasoning_summary*` — see [`contentBuilders.ts:355`](../../apps/frontend/src/features/chat/chatModel/contentBuilders.ts#L355), [`eventReducer.ts:148`](../../apps/frontend/src/features/chat/chatModel/eventReducer.ts#L148).
> - ✅ FE `ReasoningGroup` + `Reasoning` components mounted in `AssistantMessage.tsx` (today: hardcoded "Thinking" label, `<details open>`, no time).
> - 🔵 PR 2.1 `ThinkingDepthControl` — independent; this PR works whether or not the user sets the depth.
>   **Reads alongside:**
> - [`01-citations-live-registry.md`](01-citations-live-registry.md) — same emission pattern (extract from `AIMessageChunk` → emit existing event type → no new table).
> - [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md) — projection-driven rendering, Streamdown for assistant markdown.
> - [`services/ai-backend/CLAUDE.md`](../../services/ai-backend/CLAUDE.md) — class-method helpers, no module-level functions, no `dict[str, Any]` domain state.

---

## 0 · TL;DR

The design (handoff bundle) puts an italic **"Thought process"** accordion between the user message and the assistant's first text — collapsed by default with `Thinking…` while streaming and `Thought process · 4s` once done. Today our chat surface has every screw needed for that **except the screwdriver between two of them**:

| Layer            | Already in place                                                                                                                              | Gap                                                                                                                                       |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Provider config  | `ModelConfig.reasoning` builds Anthropic `thinking={type, budget_tokens}` and OpenAI `reasoning={effort, summary}` kwargs                     | Nothing — depth control rides PR 2.1                                                                                                      |
| Stream chunks    | LangChain emits `AIMessageChunk.content` with `{"type":"thinking","thinking":"…"}` (Anthropic) / reasoning summary blocks (OpenAI Responses)  | `runtime_worker/stream_events.py::stream_delta` only extracts plain text; thinking blocks are silently dropped                            |
| Wire event       | `reasoning_summary` / `reasoning_summary_delta` declared, projected (`activity_kind=reasoning`, `display_title="Thinking"`), redaction-listed | **Never emitted.** Zero call sites in `runtime_worker/`                                                                                   |
| FE reducer       | `eventReducer.applyRuntimeEvent` handles both event types, calls `appendReasoning`                                                            | Works once events flow                                                                                                                    |
| FE message parts | `ReasoningMessagePart` type; grouping logic in `MessageParts.tsx` collapses adjacent reasoning parts into one `<ReasoningGroup>`              | One thinking part per turn (multiple non-adjacent thinking spans collapse — acceptable for v1; multi-span deferred to follow-up)          |
| FE component     | `ReasoningGroup.tsx` renders `<details open>` with hardcoded `Thinking` label                                                                 | Doesn't match the design: needs collapsed-by-default, dynamic label, elapsed-time stamp on the right, italic body, dashed-border-on-hover |

So this PR is **two short edits and a re-skin**:

1. **ai-backend:** in `StreamMessageParser` add a `reasoning_delta(message)` extractor sibling to `message_delta`. In `StreamMessageProcessor` (or `StreamEventMapper.append_activity_events`), if the chunk carries a reasoning block, emit `reasoning_summary_delta` through the existing `event_producer.append_api_event()` seam. Same pipe as text deltas. No new table.
2. **frontend:** rebuild `ReasoningGroup` to match the design — controlled `<details>` (collapsed by default), label switches on `status`, time stamp computed from the part's first→latest event timestamps, italic body, `aui-reasoning-group` CSS rewritten to match `.thinking` from the prototype. Extend `ReasoningMessagePart` with `startedAt` / `updatedAt` (epoch ms) populated by the reducer using `event.created_at`.
3. **CSS:** drop the open-by-default styling, italicise the body, add the time stamp.

**The three principles**

1. **Reuse the existing event type.** `reasoning_summary_delta` is already declared, projected, redacted, persisted, replayed, and proxied. Adding a third event type for "thinking" would be duplication. The design's "Thinking…" / "Thought process" copy is a UI label decision, not a wire decision.
2. **One emission seam, one render seam.** Backend: a single class-method that turns a parsed chunk into an `Optional[reasoning_text]`, mirroring `stream_delta`. Frontend: re-skin the existing `ReasoningGroup` — do not introduce a sibling `ThinkingGroup` component or a new message-part type.
3. **No backend work for v1 to "remember" thinking.** Persistence is the existing `runtime_events` row + `appendReasoning` accumulation in the FE message-content array. Replay rebuilds the accordion deterministically from those events. A dedicated `runtime_thinking` table would be premature — the wire payload is already redaction-aware, and we don't need cross-run analytics on thoughts (yet).

LoC estimate: ai-backend ≈ 40 (extractor + emitter call + 4-line projector tweak if we want a `running` status while delta-streaming + tests) · frontend ≈ 70 (ReasoningGroup rebuild + reducer timestamps + tests) · CSS ≈ 20 · api-types ≈ 0 · facade ≈ 0 · design-system ≈ 0.

---

## 1 · PRD

### 1.1 Problem

The screenshot in this thread shows the Atlas design rendering an italic, accordion-style **"Thought process · 4s"** block between the user's prompt and the first assistant text. Our shipping chat surface renders nothing in that slot. Two distinct gaps cause it:

1. **The model is thinking, but no one is listening.** `ModelConfig.reasoning.enabled=true` already configures Anthropic `thinking` and OpenAI `reasoning` (see `_anthropic_model_kwargs` / `_openai_model_kwargs` in [`deep_agent_builder.py:252-299`](../../services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py#L252)). The provider duly returns reasoning blocks in the stream chunks. But [`stream_events.py::StreamEventMapper.stream_delta`](../../services/ai-backend/src/runtime_worker/stream_events.py#L199) extracts only plain assistant text — reasoning blocks are dropped on the floor before ever reaching the event store. **Zero `reasoning_summary*` events have ever been written to `runtime_events`.**
2. **The render is wrong.** [`ReasoningGroup.tsx`](../../apps/frontend/src/features/chat/components/markdown/ReasoningGroup.tsx) is a hardcoded `<details open>` with the static label `Thinking`. The design wants collapsed-by-default, dynamic label (`Thinking…` while streaming, `Thought process` once done), an elapsed-time stamp on the right, italic body text with an accent left-border, and the dashed-pill hover affordance from the prototype's `.thinking__head` styling.

For non-engineer enterprise users — the Atlas audience — the thought process is the **trust-building affordance** that distinguishes this product from a black box. A user reading "Looked through 12 docs, then drafted" is reassured; a user reading nothing assumes the model is making things up. This is why the design puts the accordion _immediately_ between the user message and the assistant reply, _immediately_ visible without expansion (label + time visible; body collapsed for calm).

### 1.2 Goals

1. **The accordion appears when the model thinks.** When `ModelConfig.reasoning.enabled=true` for the run's model and the provider returns reasoning blocks, an italic "Thinking…" pill renders directly under the user message, ahead of any tool calls and ahead of the first text chunk. Body streams in if the user expands.
2. **The label and time stamp match the design.** While the model is still streaming reasoning: `Thinking… · {elapsed_seconds}s`. Once the model emits its first non-reasoning content (text or tool-call), the label flips to `Thought process · {final_seconds}s` and the running cursor disappears.
3. **The accordion is collapsed by default.** Click the head to expand. State is per-message, ephemeral (does not persist across reloads — replay re-renders collapsed). Accent-line on the left of the expanded body, italic monochrome serif-ish (Inter italic) on the body text, white-space: pre-wrap.
4. **The accordion is replayable and resumable.** Loading a finished conversation from history rebuilds the accordion with the final elapsed time. Reconnecting via `?after_sequence=N` mid-stream picks up the running label without replaying.
5. **No regression for non-thinking models.** When `reasoning.enabled=false` (or the provider does not return reasoning blocks), no accordion renders — the slot is invisible. `gpt-4o-mini` and `claude-haiku` behave today exactly as they will after this PR.
6. **Wire stays single-seam.** Reasoning content travels on the existing `reasoning_summary_delta` event type; final cap on `reasoning_summary`. No new event variant, no new payload, no new table.
7. **Streaming subsystem stays byte-identical for text.** The existing `MODEL_DELTA` path is untouched. The thinking extractor runs **before** the text extractor in the per-chunk flow, but extracted thinking content is **not** mixed into the `MODEL_DELTA` payload — they are separate events with the same provenance metadata.

### 1.3 Non-goals

- **Persistent expand/collapse state.** Click-to-expand is per-message in the current session. Saving "user always wants thinking expanded" is a Settings preference, deferred to PR 2.1's `ThinkingDepthControl` follow-up. (PR 2.1 already plans a `default_reasoning_effort` setting; we do not also need a "default expanded" toggle.)
- **Multiple non-adjacent thinking blocks in one assistant turn.** If the model thinks, calls a tool, and thinks again, today's `appendReasoning` collapses both spans into a single accumulated reasoning part. The render still works (`MessageParts` groups adjacent reasoning parts into one `<ReasoningGroup>`), but a second post-tool think would merge into the pre-tool one. Multi-block support is a follow-up — see §3.5.
- **Thinking-as-citations.** Reasoning text is not parsed for `[c<id>]` citation tokens. Citations appear in the assistant's visible text, not in the thinking trace. (LangChain's Anthropic adapter does not surface `citations_delta` inside `thinking` blocks anyway.)
- **A dedicated `runtime_thinking` table.** Thinking content is a stream-replayable artifact, not a queryable entity. Storing it as `runtime_events` rows (which it already does once the emitter is wired) is sufficient; the redaction allow-list (`schemas/events.py::_reasoning_summary_payload`) is already in place.
- **OpenAI Responses API encrypted-content reasoning.** `ModelReasoningConfig.include_encrypted_content=true` instructs the SDK to return `reasoning.encrypted_content` for stateless re-prompting; this is opaque bytes meant for _redrive_, not display. We do not surface it. Only the `reasoning.summary` text channel renders.
- **Gemini "thinking" output.** As of this writing the LangChain Google GenAI adapter does not surface a separate reasoning channel from `gemini-2.5-pro-thinking` chunks. When/if it does, the same extractor catches it via the same content-block shape; for now Gemini runs render no accordion.
- **A separate `thinking_started` / `thinking_completed` lifecycle event pair.** The existing two-event vocabulary (`reasoning_summary_delta` for streaming chunks, `reasoning_summary` for the final cap) is enough for the FE to compute `running` vs `complete`. Adding lifecycle events would be cosmetic.

### 1.4 Success criteria

- ✅ Running `make dev` and sending `Use Aurora 4 launch positioning to draft Q1 announcement` against a workspace whose default model is Claude Sonnet 4.5 with `reasoning.enabled=true` produces a `Thinking…` accordion under the user message within ~200 ms of the first model chunk. The body, when expanded, streams reasoning text live.
- ✅ Once the model finishes thinking (first non-reasoning chunk arrives — text or tool call), the label flips to `Thought process · Ns` and the running cursor (`▍`) on the body is removed.
- ✅ Refreshing the page on a finished conversation re-renders the accordion collapsed with the final elapsed time. Body text is replayed deterministically from the persisted `reasoning_summary*` events.
- ✅ Mid-stream reconnect via `EventSource?after_sequence=N` resumes correctly: if the user had expanded the body, expansion state is lost (per non-goal); otherwise the accordion appears collapsed with the up-to-date label.
- ✅ A run on a non-thinking model (`gpt-4o-mini`, `claude-haiku-4-5`, `gemini-2.5-pro` with `reasoning.enabled=false`) emits zero `reasoning_summary*` events. `runtime_events.event_type` for those runs contains no `reasoning_*` rows. The FE renders no accordion.
- ✅ The FE never derives reasoning behavior from event-name prefixes — it reads `event.event_type` directly through the existing reducer case (per `apps/frontend/CLAUDE.md` projection-driven rule).
- ✅ `make test` green; ai-backend pytest suite green; FE typecheck + build green. Existing presentation tests (`test_runtime_event_presentation_projector*.py`) covering reasoning projections continue to pass.
- ✅ Compliance-relevant: `runtime_events.payload_json_redacted` for the new emissions passes through `_reasoning_summary_payload` (already implemented in [`schemas/events.py:291-307`](../../services/ai-backend/src/runtime_api/schemas/events.py#L291)) — only `summary` and `delta` are allow-listed; provider-internal fields are dropped.
- ✅ Audit: thinking content is **not** independently audited (it's the same artifact as the assistant's reply). Existing `runtime_audit_log` for the run captures the run-completion record, which is sufficient.

### 1.5 User stories

| #    | Persona                                    | Story                                                                                                                                                                                                                                                                                                                                                              |
| ---- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| US-1 | Sarah · Marketing Ops · Sonnet 4.5 default | Asks for the Q1 launch announcement. Beneath her message a row appears: `✦ Thinking… ▸ 1s`. As text starts streaming below, the row flips to `✦ Thought process ▸ 4s`. She doesn't expand — she just keeps reading. Trust intact.                                                                                                                                  |
| US-2 | Sarah, second time                         | This time she clicks the row. The body slides open: italic, lower-contrast, accent-left-border. She reads the four-source reasoning. Closes it. The conversation history shows it collapsed again on next load.                                                                                                                                                    |
| US-3 | Devi · gpt-4o-mini default                 | The fast model. No reasoning channel. No accordion appears, nothing changes from today. Run is unaffected.                                                                                                                                                                                                                                                         |
| US-4 | Marcus · Sonnet 4.5 with Deep              | He's set thinking depth = Deep via PR 2.1's control. The accordion shows `Thinking… · 12s` mid-stream. Body, when expanded, contains substantially more text than at Balanced. PR 2.1 carries the budget; this PR just renders.                                                                                                                                    |
| US-5 | Replay user                                | Opens an old conversation in his history. Each assistant turn shows `Thought process · Ns` collapsed. He expands two of them to recall what the model thought.                                                                                                                                                                                                     |
| US-6 | Mid-stream reconnect                       | Network blip mid-think. Browser reconnects with `?after_sequence=N`. The accordion appears with `Thinking… · 6s` (server clock has advanced). Body picks up where it left off (assuming events 0..N-1 had been received and applied).                                                                                                                              |
| US-7 | Multi-tool turn                            | Model thinks 2 s → calls Drive tool → thinks 1 s → emits text. Today (v1) the FE collapses both think spans into one accordion: `Thought process · 3s`. The Drive tool-call card renders **after** the accordion (because `appendReasoning` accumulates and `MessageParts` orders parts as they arrive). Acceptable for v1; multi-block support is §3.5 follow-up. |

---

## 2 · Spec

### 2.1 Wire — re-using `reasoning_summary_delta` and `reasoning_summary`

**No new event type.** The existing pair is sufficient.

```jsonc
// reasoning_summary_delta — emitted per provider-thinking chunk
{
  "event_envelope_version": 1,
  "run_id": "run_…",
  "conversation_id": "conv_…",
  "org_id": "org_…",
  "sequence_no": 42,
  "event_type": "reasoning_summary_delta",
  "source": "model",
  "activity_kind": "reasoning",         // already projected
  "display_title": "Thinking",          // already projected (Messages.Event.REASONING)
  "summary": "…the chunk text…",
  "status": "running",                  // already projected for delta variant
  "payload": { "delta": "the chunk text", "summary": "the chunk text" },
  "created_at": "2026-05-06T18:47:01.234Z"
}

// reasoning_summary — emitted exactly once per thinking span, when the
// final reasoning content block closes (or when the next non-reasoning
// chunk arrives). Carries the assembled `summary` field.
{
  "event_type": "reasoning_summary",
  "activity_kind": "reasoning",
  "display_title": "Thinking",
  "summary": "…full reasoning text…",
  "status": "completed",                // projector already returns COMPLETED here
  "payload": { "summary": "…full reasoning text…" },
  "created_at": "2026-05-06T18:47:05.421Z"
}
```

Redaction is already in place: [`schemas/events.py::_reasoning_summary_payload`](../../services/ai-backend/src/runtime_api/schemas/events.py#L291) allow-lists exactly `summary` (both variants) and `delta` (delta variant only). Anything the provider returns outside that set is dropped before persistence.

### 2.2 Backend — extract reasoning from LangChain chunks and emit

LangChain surfaces provider reasoning content uniformly through `AIMessageChunk.content`:

- **Anthropic** (`langchain-anthropic`): when `thinking` is configured, chunks carry list-typed `content` with blocks of shape `{"type": "thinking", "thinking": "…", "index": n}` and finalising `{"type": "thinking", "thinking_signature": "…"}` markers. Plain text continues as `{"type": "text", "text": "…"}` blocks.
- **OpenAI Responses** (`langchain-openai`): when `reasoning={effort, summary}` is set, chunks carry `{"type": "reasoning_summary_text_delta", "text": "…", "index": n}` blocks (and finalising `{"type": "reasoning_summary_text_done"}`). Plain text continues as `{"type": "text", "text": "…"}` blocks. `output_version="responses/v1"` (already set when `reasoning.summary` is configured) ensures this shape.
- **Gemini** (`langchain-google-genai`): no separate reasoning channel today — chunks return only text. Returning `None` is the correct behaviour.

The extractor is a single class-method on `StreamMessageParser` (per `services/ai-backend/CLAUDE.md`: helpers stay inside classes). It mirrors `message_delta`:

```python
# services/ai-backend/src/runtime_worker/stream_messages.py
class StreamMessageParser:
    @classmethod
    def reasoning_delta(cls, message: object) -> str | None:
        """Extract a reasoning text delta from one parsed AIMessageChunk.

        Returns the delta text if the chunk carried a reasoning content
        block (Anthropic ``thinking`` or OpenAI ``reasoning_summary_text_delta``);
        ``None`` otherwise. The plain-text path (``message_delta``) is
        unaffected — text and reasoning are extracted independently from
        the same chunk and emitted as two distinct events.
        """
        content = cls.payload_mapping(message).get(Keys.Field.CONTENT)
        if not isinstance(content, list):
            return None
        deltas: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get(Keys.Field.TYPE)
            if block_type == "thinking":
                value = StreamTextHelper.extract(block.get("thinking"))
            elif block_type == "reasoning_summary_text_delta":
                value = StreamTextHelper.extract(block.get("text"))
            else:
                continue
            if value:
                deltas.append(value)
        return "".join(deltas) or None

    @classmethod
    def reasoning_finalised(cls, message: object) -> bool:
        """True when the chunk closes a reasoning block (and we should
        emit a final ``reasoning_summary`` cap).

        Anthropic emits ``thinking_signature`` on the final block of a
        thinking span; OpenAI Responses emits ``reasoning_summary_text_done``.
        """
        content = cls.payload_mapping(message).get(Keys.Field.CONTENT)
        if not isinstance(content, list):
            return False
        for block in content:
            if not isinstance(block, dict):
                continue
            t = block.get(Keys.Field.TYPE)
            if t == "thinking" and block.get("thinking_signature"):
                return True
            if t == "reasoning_summary_text_done":
                return True
        return False
```

The emission seam lives next to the existing text emission in `streaming_executor.py`. Because reasoning extraction is provider-shaped, it goes through the message processor, not the per-chunk text path (which deals only with `delta: str | None`). Concretely, extend `StreamMessageProcessor.process` (in [`runtime_worker/stream_events.py`](../../services/ai-backend/src/runtime_worker/stream_events.py)) so that, in addition to the message-delta side-effects it already runs, it:

1. Calls `StreamMessageParser.reasoning_delta(message)`.
2. If non-None, emits `reasoning_summary_delta` through `event_producer.append_api_event` with `source=StreamEventSource.MODEL`, `payload={"delta": text, "summary": text}`, `summary=text`. Mirrors how `MODEL_DELTA` is emitted in `streaming_executor.py:200`.
3. If `reasoning_finalised(message)` is true, accumulate the per-span text the processor has been carrying and emit a final `reasoning_summary` with the full `summary`.

Per-span accumulation lives on the processor as a `dict[run_id, str]` keyed by `(run_id, namespace.task_id)`; cleared on emission of the final cap. This is the only new state — it is in-memory only, scoped to the worker's processing of a single chunk batch, and does not need persistence (events are already persisted independently).

Subagent-streamed reasoning is **dropped** in v1 (matches text behaviour: `stream_delta` returns None when `namespace.is_subagent`). The subagent's parent surfaces the subagent's own thinking budget as a subagent-update summary; we don't double-emit.

### 2.3 Backend — projector: no changes

`RuntimeEventPresentationProjector` (`schemas/events.py:213`) already handles both event types:

- `activity_kind` → `RuntimeActivityKind.REASONING`
- `display_title` → `Messages.Event.REASONING` ("Thinking")
- `summary` → `payload.summary` or `payload.delta` (via `_reasoning_summary_payload`)
- `status` → `RUNNING` for delta, `COMPLETED` for the final cap (`schemas/events.py:429-430`)

The wire shape projected to the FE is exactly what `eventReducer.applyRuntimeEvent` already consumes. No projector edit.

### 2.4 Frontend — extend `appendReasoning` with timestamps

The reducer already routes both event types into `appendReasoning` ([`eventReducer.ts:148-159`](../../apps/frontend/src/features/chat/chatModel/eventReducer.ts#L148)). Extend the message-part to track its time window so the accordion can render the elapsed-time stamp without a separate React `useEffect` clock per message.

```ts
// apps/frontend/src/features/chat/runtime/types.ts
export interface ReasoningMessagePart {
  readonly type: "reasoning";
  readonly text: string;
  readonly status?: MessagePartStatus; // "running" while delta-streaming, else "complete"
  readonly startedAtMs?: number; // event.created_at of first delta
  readonly updatedAtMs?: number; // event.created_at of latest event
}
```

```ts
// apps/frontend/src/features/chat/chatModel/contentBuilders.ts
export function appendReasoning(
  content: ThreadMessageContent,
  text: string,
  replace: boolean,
  eventCreatedAtMs: number,
  status: MessagePartStatus,
): ThreadMessageContent {
  const index = content.findIndex(isReasoningPart);
  if (index === -1) {
    return [
      ...content,
      {
        type: "reasoning",
        text,
        status,
        startedAtMs: eventCreatedAtMs,
        updatedAtMs: eventCreatedAtMs,
      },
    ];
  }
  return content.map((part, i) =>
    i === index && isReasoningPart(part)
      ? {
          ...part,
          text: replace ? text : part.text + text,
          status,
          updatedAtMs: eventCreatedAtMs,
        }
      : part,
  );
}
```

The reducer call site already has `event.created_at` and the event type — passing both is two new arguments. `status` is `complete` only for `reasoning_summary` (the final cap) and `running` for `reasoning_summary_delta`.

A subsequent text or tool-call delta on the same assistant message **closes** the running thinking part — call a new `closeReasoningIfRunning(content, eventCreatedAtMs)` helper from the existing `appendText` / `upsertPart` builders. This is the FE companion to the BE's `reasoning_finalised` extraction; either one independently can flip the accordion to "Thought process · Ns".

### 2.5 Frontend — rebuild `ReasoningGroup` to match the design

Replace the contents of [`apps/frontend/src/features/chat/components/markdown/ReasoningGroup.tsx`](../../apps/frontend/src/features/chat/components/markdown/ReasoningGroup.tsx). Use the native `<details>` element for accessibility (built-in keyboard handling, `aria-expanded`, focus ring) and let it default-closed by omitting the `open` attribute. Read the part metadata via the existing `MessagePartsComponents` `ReasoningGroup` slot — extend `ReasoningGroupProps` with the part data the reducer already populates.

```ts
// apps/frontend/src/features/chat/runtime/types.ts
export interface ReasoningGroupProps {
  readonly children: ReactNode;
  readonly startIndex: number;
  readonly endIndex: number;
  readonly status: "running" | "complete"; // synthesised in MessageParts from the parts' statuses
  readonly elapsedSeconds: number; // synthesised from startedAtMs/updatedAtMs (or 0 for replay-with-no-clock)
}
```

The component is a thin wrapper — under-30 LOC. The styling (italic body, accent-left-border, dashed-pill hover, time stamp on the right) lives in `apps/frontend/src/styles.css` keyed off the existing `.aui-reasoning-group` class. Steal the prototype's `.thinking__head` / `.thinking__inner` rules from [`styles.css:1520-1605`](/tmp/design-fetch/extracted/0x-copilot/project/styles.css) — they are already production-clean.

`MessageParts.tsx` synthesises `status` and `elapsedSeconds` for the group: status is `running` if any child reasoning part has `status.type === "running"`; `elapsedSeconds` is `Math.max(0, (latest updatedAtMs - earliest startedAtMs) / 1000)` rounded to integer seconds. Both reads are O(span-length) on the already-collected children.

Optional polish: while running, repaint the elapsed time once per second via a single `requestAnimationFrame`-driven hook owned by the running `ReasoningGroup` — only one such tick per running group, gated by `status==="running"`. This is ~10 LOC and avoids a `setInterval` storm. Acceptable to ship without it for v1 — the time advances on each delta arrival, which is typically more than once per second anyway.

### 2.6 CSS

Replace the current `.aui-reasoning-group` rules in `apps/frontend/src/styles.css` (currently styles `<details open>` with no italic body) with the prototype's `.thinking` family rewritten under `.aui-reasoning-group`:

- `details > summary` rendered as a dashed-pill hover head with `Thinking…` / `Thought process` label, accent spark glyph, caret, time stamp on the right (`margin-left: auto`, tabular nums).
- `details[open] > summary` solidifies (background + border).
- `details > .aui-reasoning-group__content` keeps the **italic, accent-left-border, surface-muted background, line-height: 1.65, white-space: pre-wrap** body that the prototype defines (`.thinking__inner`).
- `details[data-status="running"] > .aui-reasoning-group__content::after` renders the blinking `▍` cursor.

Tokens already exist in `packages/design-system/src/styles.css` (`--accent`, `--accent-line`, `--surface-muted`, `--text-mute`, `--line-soft`, `--r-md`, `--r-pill`).

### 2.7 Subagent thinking — explicitly out of scope

Subagent runs ride a separate `subagent_*` event family today; their reasoning chunks stay invisible to the parent thread (per design — the subagent fleet card carries its own progress affordance). When/if a subagent's thinking should bubble up, it would land as a follow-up that emits `subagent_progress` events with `summary` populated from the subagent's own `reasoning_delta`. This PR does nothing for that case.

---

## 3 · Architecture

### 3.1 End-to-end timing

```
Anthropic SDK chunk
        │
        ▼
LangChain langchain-anthropic adapter
        │  (AIMessageChunk.content includes {"type":"thinking", ...})
        ▼
streaming_executor.StreamingExecutor._stream_node          [unchanged]
        │
        ├── StreamEventMapper.stream_delta(chunk)          [unchanged — text]
        │       └── if non-None: emit MODEL_DELTA          [unchanged]
        │
        └── StreamEventMapper.append_activity_events(...)  [extended]
                └── StreamMessageProcessor.process(...)    [extended]
                        ├── (existing) tool-call routing, etc.
                        ├── StreamMessageParser.reasoning_delta(message)  [NEW]
                        │     └── if non-None: emit reasoning_summary_delta
                        └── StreamMessageParser.reasoning_finalised(message)  [NEW]
                              └── if true: emit reasoning_summary (with assembled summary)
        │
        ▼
RuntimeEventProducer.append_api_event           [unchanged]
        │
        ▼
EventStorePort (in-memory or Postgres)          [unchanged — runtime_events row]
        │
        ▼
GET /v1/agent/runs/{run_id}/stream              [unchanged — SSE replay/live]
        │  (facade transparently proxies; api-types already lists the event names)
        ▼
agentApi.streamRunEvents → eventReducer.applyRuntimeEvent  [unchanged routing]
        │
        ▼
appendReasoning(content, text, replace, createdAtMs, status)  [extended signature]
        │
        ▼
ReasoningMessagePart with text + status + startedAtMs + updatedAtMs
        │
        ▼
MessageParts groups adjacent reasoning parts → <ReasoningGroup status, elapsedSeconds>  [restyled]
        │
        ▼
Rendered accordion: <details> closed, summary "Thinking… · 4s", body italic, accent-left
```

### 3.2 Migration / persistence

**No migration.** Reasoning events persist as ordinary `runtime_events` rows (table since `0001_initial_runtime_persistence.sql`). The `payload_json_redacted` column already passes the new emissions through `_reasoning_summary_payload`, which allow-lists exactly the fields we need. No `runtime_thinking` table.

Replay-on-history-load is deterministic: `runtime_event_store::list_events` returns the rows in `sequence_no` order, the FE reducer rebuilds the `ReasoningMessagePart` exactly as it appeared live.

### 3.3 Provider abstraction

Both `_anthropic_model_kwargs` and `_openai_model_kwargs` already feed the SDK the right knobs. The extractor is the **only** provider-shaped touchpoint, and it is one class with two `block_type` checks. Adding a new provider's reasoning channel is a 3-line addition:

```python
elif block_type == "<future_provider_reasoning_block_name>":
    value = StreamTextHelper.extract(block.get("<text_field>"))
```

We do not introduce a "ProviderReasoningPort" abstraction — that would be premature ceremony for two providers with stable shapes. (`services/ai-backend/CLAUDE.md`: "Don't add features, refactor, or introduce abstractions beyond what the task requires.")

### 3.4 Prebuilt middleware / package check

Searched (web + repo): there is no existing pip or npm middleware that "extracts thinking from a LangChain `AIMessageChunk` and emits it as a separate stream event." The relevant building blocks are already in our tree:

- `langchain-anthropic` ≥ 0.3 surfaces extended thinking via the content-block shape used above (`type: "thinking"`).
- `langchain-openai` ≥ 0.2 surfaces Responses-API reasoning summaries via the content-block shape used above (`type: "reasoning_summary_text_delta"` / `_done`).
- LangChain's own `RunnableConfig.callbacks` would let us hook `on_chat_model_stream`, but our worker already streams chunks through `StreamEventMapper` — adding a callback alongside would create a second emission seam. Better to extend the seam we have.

For the FE accordion, we considered `@radix-ui/react-collapsible` and shadcn's `<Collapsible>` — both add a peer dependency for behaviour the native HTML `<details>/<summary>` already provides for free with built-in accessibility (announced as "details disclosure" by VoiceOver/NVDA, keyboard `Enter`/`Space` toggles, focus visible). We stay on `<details>`. (Streamdown for the body markdown was already chosen for the existing Reasoning component.)

### 3.5 Multi-block thinking — follow-up sketch

When the model thinks → tool → thinks again, today's `appendReasoning` collapses both spans because it does `findIndex(isReasoningPart)` and grows the first match. Once we hit a real product need for distinct cards (it shows up as "the second thinking block disappears" in user reports), the change is local:

1. Reducer: append a _new_ reasoning part each time the FE sees a reasoning event whose timestamp is **after** the most recent non-reasoning part it has appended (text or tool-call). Track a single `lastNonReasoningAtMs` per assistant message and compare to `event.created_at`.
2. `MessageParts.groupParts` already segments adjacent reasoning parts into one group and a non-adjacent reasoning part into a new group — so two parts with a tool-call between them produce two groups automatically. No change there.

Net change: ~10 LOC in `contentBuilders.ts`. Deferred until the case shows up in dogfood.

### 3.6 Replay edge cases

- **Run loaded from history with reasoning events:** `replayRunEvents` walks the persisted rows; the reducer rebuilds the part with `startedAtMs` = first delta's `created_at`, `updatedAtMs` = final cap's `created_at`. `elapsedSeconds` is correct.
- **Run loaded from history with reasoning events but no final cap (worker crashed mid-think):** The reducer leaves `status="running"` on the part. The accordion renders `Thinking… · Ns` with the elapsed time frozen at the last delta. Acceptable — the run row will also be in a non-terminal state; the existing run-resume path covers it.
- **Reconnect via `?after_sequence=N` while running:** Same as live — events arrive in order and the reducer accumulates. `appendReasoning` is associative-with-replace under the existing `reasoning_summary` (replace) vs `reasoning_summary_delta` (append) split.

### 3.7 Observability

- Existing metrics on `MODEL_DELTA` are unchanged. We do not add a new counter for `REASONING_SUMMARY_DELTA` in v1. Volume is observable through the standard `runtime_events.event_type` aggregation already exported by `pg_stat_statements`-style telemetry (commit `94e230e`).
- Add one debug log in `StreamMessageProcessor` when `reasoning_delta` returns non-None (lazy logger: `logger.debug("reasoning chunk %d bytes for run %s", len(text), run.run_id)`). No info-level chatter — these chunks fire often.

### 3.8 Compliance

- **Field-level encryption:** `payload_json_redacted` continues to flow through `RedactionPolicy`. Reasoning text is conversation content, same sensitivity as the assistant reply; no new classification.
- **RLS:** Same row-level security as every other `runtime_events` row (org_id-scoped).
- **Retention:** Reasoning events are retained for the same window as the run's other events. No separate retention policy.
- **Training opt-out:** Already plumbed for the **request** side via PR 4.3's `extra_kwargs` in `build_chat_model`. Reasoning blocks travel through the same model call that already honours opt-out headers.
- **Audit log:** No new audit action. Run-level lifecycle continues to capture run start/cancel/complete.

---

## 4 · Verification

### 4.1 Unit (ai-backend)

- `tests/unit/runtime_worker/test_stream_messages_reasoning.py` (new)
  - `reasoning_delta` returns text for an Anthropic-shaped chunk with one `thinking` block
  - `reasoning_delta` concatenates text across multiple `thinking` blocks in one chunk
  - `reasoning_delta` returns text for an OpenAI Responses `reasoning_summary_text_delta` chunk
  - `reasoning_delta` returns `None` for plain-text-only chunks
  - `reasoning_delta` returns `None` for tool-call chunks
  - `reasoning_finalised` returns True only when `thinking_signature` or `reasoning_summary_text_done` is present
- `tests/unit/runtime_worker/test_stream_event_mapper_reasoning.py` (new)
  - One reasoning chunk → exactly one `reasoning_summary_delta` event with the chunk text
  - Two consecutive reasoning chunks → two delta events with `summary` accumulating in the processor's per-run buffer
  - Final reasoning chunk (with signature/done marker) → one delta + one `reasoning_summary` cap with the full assembled text
  - Subagent-namespaced reasoning chunk → no event
  - Plain-text chunk after reasoning → unchanged `MODEL_DELTA`, no extra reasoning event
- Existing `tests/unit/runtime_api/schemas/test_event_presentation_projector.py` reasoning cases — must still pass without edits.

### 4.2 Unit (frontend)

- `apps/frontend/src/features/chat/chatModel/eventReducer.test.ts`
  - Adding a `reasoning_summary_delta` event sets `startedAtMs` and `updatedAtMs` from `event.created_at`
  - A subsequent delta updates `updatedAtMs` only
  - The final `reasoning_summary` event flips `status` to `complete`
  - A `model_delta` arriving after a running reasoning part flips reasoning `status` to `complete` (FE close-on-next-non-reasoning)
- `apps/frontend/src/features/chat/components/markdown/ReasoningGroup.test.tsx` (new)
  - `<details>` is closed by default
  - Summary reads "Thinking…" while `status="running"`, "Thought process" otherwise
  - Time stamp shows seconds rounded; running variant updates on prop change
  - Body content is rendered inside `.aui-reasoning-group__content`

### 4.3 Manual walk-through (`make dev`)

1. With `RUNTIME_DEFAULT_REASONING_ENABLED=true` and a Claude Sonnet 4.5 default model, send `Use Aurora 4 launch positioning to draft Q1 announcement`. Observe an italic `Thinking… · 1s` row appear under the user message; expand it; watch reasoning text stream in. Once the assistant's first text arrives, label flips to `Thought process · Ns` and the `▍` disappears.
2. Switch the run to GPT-5 (`reasoning={effort: "high", summary: "auto"}` already set by config). Repeat — same render, with reasoning summary text from the Responses API.
3. Switch to `claude-haiku-4-5` (no thinking). Repeat — no accordion. Confirm via DevTools network tab: zero `reasoning_summary*` events on the SSE stream.
4. Refresh the page on the Sonnet 4.5 conversation. Accordion re-renders collapsed with the final elapsed time.
5. Mid-stream, kill the network for ~3 s and let it reconnect. Accordion recovers with up-to-date elapsed time.
6. `psql` into local Postgres and run `SELECT event_type, count(*) FROM runtime_events WHERE run_id = '…' GROUP BY 1`. Confirm `reasoning_summary_delta` rows for thinking runs and zero for non-thinking runs.

### 4.4 Compliance gate

- `make prod` build (validates required secrets, refuses `DEV_AUTH_BYPASS=true`).
- Manual pass: who can read these events (org-scoped RLS), who approved them (no privileged write — model output), what changed (no DDL), where logged (`runtime_events` payload_json_redacted, redacted by `_reasoning_summary_payload`), retention (existing run retention), deletion (cascades with the run).

### 4.5 Telemetry gate

- `pg_stat_statements` should show no new endpoints — this PR adds zero HTTP routes.
- Chunk-level overhead measured via existing per-chunk timer in `streaming_executor`. Acceptable threshold: median per-chunk extra cost < 0.5 ms (the extractor is a `for`-loop over the content list with two string compares per block).

---

## 5 · Critical files

**ai-backend**

- `services/ai-backend/src/runtime_worker/stream_messages.py` — add `StreamMessageParser.reasoning_delta` + `reasoning_finalised`
- `services/ai-backend/src/runtime_worker/stream_events.py` — extend `StreamMessageProcessor.process` to call the extractor, accumulate per-span text, emit `reasoning_summary_delta` and `reasoning_summary` through `event_producer.append_api_event`
- `services/ai-backend/tests/unit/runtime_worker/test_stream_messages_reasoning.py` (new)
- `services/ai-backend/tests/unit/runtime_worker/test_stream_event_mapper_reasoning.py` (new)

**frontend**

- `apps/frontend/src/features/chat/runtime/types.ts` — extend `ReasoningMessagePart` with `startedAtMs` / `updatedAtMs`; extend `ReasoningGroupProps` with `status` + `elapsedSeconds`
- `apps/frontend/src/features/chat/chatModel/contentBuilders.ts` — extend `appendReasoning` signature; new `closeReasoningIfRunning` helper
- `apps/frontend/src/features/chat/chatModel/eventReducer.ts` — pass `event.created_at` and `status` through; flip reasoning status on first non-reasoning content
- `apps/frontend/src/features/chat/runtime/components/MessageParts.tsx` — synthesise `status` + `elapsedSeconds` for the `<ReasoningGroup>` slot
- `apps/frontend/src/features/chat/components/markdown/ReasoningGroup.tsx` — rebuild as collapsed-by-default `<details>` with dynamic label + time stamp
- `apps/frontend/src/styles.css` — replace `.aui-reasoning-group` rules with the prototype's `.thinking` family
- `apps/frontend/src/features/chat/components/markdown/ReasoningGroup.test.tsx` (new)
- `apps/frontend/src/features/chat/chatModel/eventReducer.test.ts` — add reasoning-timestamp + close-on-text cases

**packages/api-types** — none.
**services/backend-facade** — none (transparent SSE proxy).
**services/ai-backend/migrations** — none.

---

## 6 · Open TODOs (deferred follow-ups)

1. **Multi-block thinking in one assistant turn** — §3.5 sketch. Ship when dogfood surfaces "second thinking block missing."
2. **Per-run reasoning byte/cost telemetry** — once we charge differently for thinking tokens, attribute them in `runtime_model_call_usage` (sibling to PR 7.2's per-connector attribution).
3. **Settings preference: default-expanded thinking** — ride PR 4.3's `default_reasoning_effort` row with a sibling boolean. Independent.
4. **Subagent thinking bubble-up** — out of scope; would land alongside a richer subagent activity card.
5. **Gemini reasoning channel** — wait for `langchain-google-genai` to surface it, then add the `block_type` arm.
6. **Thinking tokens in the usage meter (topbar)** — when PR 4.5 lands the per-conversation usage view, show thinking tokens as a separate stack layer.

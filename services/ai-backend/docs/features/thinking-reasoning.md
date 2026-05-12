# Thinking and Reasoning

How provider-specific reasoning / "thinking" modes are configured, streamed,
and charged as a first-class feature.

See also:

- [features/usage-metrics.md](usage-metrics.md) — reasoning token billing
- [diagrams/flows/f6-thinking.puml](../architecture/diagrams/flows/f6-thinking.puml)

---

## What it does

Some model providers expose an internal reasoning step before the visible response:
Anthropic calls it "extended thinking" (`thinking_mode`), OpenAI surfaces it as
`reasoning_summary`, and Gemini has no native reasoning stream. When enabled, the
worker emits `REASONING_SUMMARY_DELTA` events as the reasoning streams in, followed
by a `REASONING_SUMMARY` event when the reasoning block closes. The visible response
text then arrives as normal `MODEL_DELTA` events.

Reasoning tokens are billed separately from output tokens where the provider charges
a different rate (tracked in `ModelPricingRecord.reasoning_per_1m_micro_usd`).

---

## Key modules

| File                                                                   | Role                                                                                  |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `agent_runtime/execution/provider_kwargs.py`                           | Resolves reasoning kwargs from `ModelConfig` per provider                             |
| `agent_runtime/execution/providers/anthropic_stream_adapter.py`        | Anthropic adapter: detects thinking blocks, emits `REASONING_SUMMARY_*` chunks        |
| `agent_runtime/execution/providers/openai_responses_stream_adapter.py` | OpenAI adapter: detects `reasoning_summary` parts                                     |
| `agent_runtime/execution/providers/gemini_grounding_stream_adapter.py` | Gemini adapter: no reasoning stream                                                   |
| `runtime_worker/stream_parts.py`                                       | `StreamPartParser` / `StreamNamespace` — routes reasoning parts to correct event type |
| `runtime_worker/stream_events.py`                                      | `StreamOrchestrator` — emits `REASONING_SUMMARY_DELTA` and `REASONING_SUMMARY` events |
| `runtime_api/schemas/events.py`                                        | `RuntimeApiEventType.REASONING_SUMMARY`, `REASONING_SUMMARY_DELTA`                    |
| `agent_runtime/budgets/charger.py`                                     | `BudgetCharger` — applies reasoning token cost at run completion                      |
| `agent_runtime/pricing/calculator.py`                                  | `CostCalculator` — uses `reasoning_per_1m_micro_usd` column                           |

---

## `ModelReasoningConfig`

Resolved by `agent_runtime/execution/provider_kwargs.py` from the workspace
`ModelConfig`. Fields and provider meanings:

| Field              | Anthropic                           | OpenAI                          | Gemini          |
| ------------------ | ----------------------------------- | ------------------------------- | --------------- |
| `thinking_mode`    | `ENABLED` / `ADAPTIVE` / `DISABLED` | n/a                             | n/a             |
| `display`          | `OMITTED` / `SUMMARIZED`            | n/a                             | n/a             |
| `summary`          | n/a                                 | `AUTO` / `CONCISE` / `DETAILED` | n/a             |
| `budget_tokens`    | Max reasoning token budget          | n/a                             | n/a             |
| `reasoning_effort` | n/a                                 | n/a                             | Low/Medium/High |

`provider_kwargs.workspace_model_kwargs()` maps these into provider-specific API
parameters (e.g. `{"thinking": {"type": "enabled", "budget_tokens": N}}` for Anthropic,
`{"reasoning": {"effort": "high", "summary": "auto"}}` for OpenAI Responses API).

---

## Anthropic thinking stream

`agent_runtime/execution/providers/anthropic_stream_adapter.py`

When `thinking_mode in {ENABLED, ADAPTIVE}`:

1. Anthropic streams `thinking_delta` blocks before the visible text.
2. The adapter emits these as `REASONING_SUMMARY_DELTA` events
   (`activity_kind=REASONING`, `visibility=USER`).
3. On `end_thinking`, emits a final `REASONING_SUMMARY` event with the full accumulated
   reasoning text.

When `display=OMITTED`: no `REASONING_SUMMARY_*` events are emitted to the client.
Reasoning tokens still accumulate and are billed. `display` is a UI hint, not a billing switch.

---

## OpenAI Responses API reasoning stream

`agent_runtime/execution/providers/openai_responses_stream_adapter.py`

OpenAI streams `reasoning_summary` parts (type depends on `summary` setting):

1. Each chunk emits a `REASONING_SUMMARY_DELTA` event.
2. End of part emits a `REASONING_SUMMARY` event closing that reasoning block.

The visible answer text arrives as separate `MODEL_DELTA` events after the reasoning.

---

## Gemini

`agent_runtime/execution/providers/gemini_grounding_stream_adapter.py`

No native reasoning summary stream. Grounding citations are captured by
`CitationStreamPipeline` (see [features/citations.md](citations.md)) but no
`REASONING_SUMMARY_*` events fire for Gemini runs.

---

## Event type summary

| Event type                | When                                       | Visibility |
| ------------------------- | ------------------------------------------ | ---------- |
| `REASONING_SUMMARY_DELTA` | Each streaming thinking chunk              | `USER`     |
| `REASONING_SUMMARY`       | Thinking block closed                      | `USER`     |
| `MODEL_DELTA`             | Visible answer text chunk                  | `USER`     |
| `FINAL_RESPONSE`          | Full answer assembled                      | `USER`     |
| `MODEL_CALL_COMPLETED`    | After final chunk; carries usage breakdown | `INTERNAL` |

---

## Token billing for reasoning

`agent_runtime/pricing/calculator.py`

`CostCalculator.compute(usage, catalog)` handles reasoning tokens separately:

- `usage.reasoning_tokens` is a distinct field on `RuntimeModelCallUsageRecord`.
- If `ModelPricingRecord.reasoning_per_1m_micro_usd` is set, reasoning tokens are
  billed at that rate.
- If not set (most models), reasoning tokens are billed at the standard output rate.
- All costs are in integer micro-USD with banker's rounding.

`BudgetCharger` applies the computed cost to the org/user budget rows using
CAS retries for idempotency.

---

## Redaction

`REASONING_SUMMARY_DELTA` and `REASONING_SUMMARY` events pass through the same
`ObservabilityRedactor` field validator as every other payload. Sensitive key/value
pairs are stripped before the event is persisted.

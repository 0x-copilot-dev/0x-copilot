# Memory and Context Management

How the agent maintains conversational memory across turns, enforces scope-based
access control, compresses overlong context, and manages token budgets.

See also:

- [architecture/02-contracts.md](../architecture/02-contracts.md) — `MemoryMetadataPort`
- [features/subagents.md](subagents.md) — subagent memory isolation

---

## What it does

`agent_runtime/context/memory/` manages two concerns:

1. **Memory scopes** — structured key-value state that persists across turns in a
   conversation. Scoped by actor role (`USER`, `AGENT`, `SYSTEM`) and path prefix.
   The agent can read/write its own scope; users can read/write theirs; system writes
   are restricted to workers.

2. **Context window management** — as a conversation grows, the token count approaches
   the model's context limit. The memory module detects this and triggers summarisation
   or selective compression of older turns.

---

## Key modules

| File                                               | Role                                                                                    |
| -------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `agent_runtime/context/memory/backends.py`         | `MemoryBackend` — read/write memory scope items via `MemoryMetadataPort`                |
| `agent_runtime/context/memory/policy.py`           | `MemoryAccessPolicy` — per-path read/write authorization rules                          |
| `agent_runtime/context/memory/contracts.py`        | `MemoryPathPolicy`, `MemoryActorRole`, `MemoryAccessOperation`, `MemoryValueNormalizer` |
| `agent_runtime/context/memory/token_budget.py`     | `MemoryTokenBudget` — compute available headroom before context overflows               |
| `agent_runtime/context/memory/summarization.py`    | `MemorySummarizer` — LLM-assisted compression of conversation turns                     |
| `agent_runtime/context/memory/subagent_trace.py`   | `SubagentArtifactsBackend` — stores subagent result summaries for parent context        |
| `agent_runtime/context/memory/prompt_injection.py` | `PromptInjectionDetector` — sanitises memory values before they enter the system prompt |
| `agent_runtime/context/memory/constants.py`        | Path prefixes, max lengths, default values                                              |

---

## Memory scopes

Each memory item is addressed by a `path` string of the form `scope/key` (e.g.
`user/preferences`, `agent/task_state`). The path prefix determines the scope and
the actor role that can access it.

`MemoryActorRole` values:

- `USER` — items written by user commands or the frontend
- `AGENT` — items written by the model during a run
- `SYSTEM` — items written by the worker infrastructure

`MemoryAccessOperation` values: `READ`, `WRITE`.

`MemoryAccessRequest` is validated by `MemoryPolicyDecision`:

```python
@classmethod
def allow(cls) -> MemoryPolicyDecision: ...
@classmethod
def deny(cls, safe_message: str) -> MemoryPolicyDecision: ...
```

The policy is checked before any read or write. A denied write returns the safe
message to the model without leaking internal detail.

---

## Prompt injection protection

`agent_runtime/context/memory/prompt_injection.py`

`PromptInjectionDetector.scan(value)` is called on every memory value before it is
inserted into the system prompt. Values containing known injection patterns are rejected.
Memory content is treated as untrusted (it was written by a previous model turn or
by external tool results).

---

## Token budget and context window

`agent_runtime/context/memory/token_budget.py`

`MemoryTokenBudget.compute(messages, model_config)`:

1. Counts tokens in the current message list (using the model's tokeniser estimate).
2. Subtracts from `ModelConfig.max_input_tokens`.
3. Returns `headroom_tokens` and `headroom_pct`.

When headroom is below a threshold, the `MemorySummarizer` is triggered.

---

## Summarisation

`agent_runtime/context/memory/summarization.py`

`MemorySummarizer.compress(messages, budget)`:

1. Identifies the oldest turns that can be safely dropped.
2. Calls the LLM with a summarisation prompt to produce a compact representation.
3. Replaces the original turns with a single synthetic "summary" message.
4. Persists a `CompressionEventRecord` so the `/context` command can report the
   compression event to the user.

A `COMPRESSION_EVENT` is emitted to the SSE stream when summarisation fires so the
frontend can show an indicator.

---

## Subagent memory isolation

Subagents receive a restricted memory view: they can read shared `SYSTEM` scope items
and their own task-scoped `AGENT` items, but cannot write to the parent conversation's
`USER` or `AGENT` scopes. The subagent result summaries are stored by
`SubagentArtifactsBackend` and surfaced to the parent graph via `subagent_trace`.

---

## `/context` slash command

When the user types `/context`, the frontend calls
`GET /v1/agent/conversations/{id}/context`. `UsageService.get_conversation_context()`
assembles a `ConversationContextResponse` with:

- `context_window` — model's max input tokens
- `used_tokens` — current token count across all messages
- `available_tokens` / `headroom_pct`
- `compression_events` — history of past compressions
- `per_subagent_breakdown`

See [features/usage-metrics.md](usage-metrics.md) for the full usage query surface.

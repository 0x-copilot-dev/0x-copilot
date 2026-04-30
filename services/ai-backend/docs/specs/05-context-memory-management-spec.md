# Spec: Context and Memory Management

## Purpose

Use Deep Agents built-in context compression and filesystem offloading while defining scoped memory policy, metrics, and guardrails for enterprise use.

## Architecture

Implemented modules:

- `agent_runtime/context/memory/backends.py`: constructs scoped memory route plans
  and request-scoped Deep Agents backends.
- `agent_runtime/context/memory/policy.py`: read/write policy for memory paths.
- `agent_runtime/context/memory/contracts.py`: memory scopes, managed payloads,
  and compression contracts.
- `agent_runtime/context/memory/token_budget.py`: metrics and threshold metadata.
- `agent_runtime/context/memory/summarization.py`: payload preparation and
  summarization fallback around SDK summarization.
- `agent_runtime/execution/factory.py`: resolves memory backends for runtime
  construction.
- `agent_runtime/execution/deep_agent_builder.py`: passes compatible backend and
  memory path configuration to `create_deep_agent`.

The implementation should start with SDK compression/offloading. Custom code should wrap, observe, and policy-check rather than replace it.

## Pydantic Contracts

Required models:

- `MemoryScope`: `scope_type`, `user_id`, `org_id`, `assistant_id`, namespace tuple.
- `MemoryPathPolicy`: path prefix, read roles, write roles, shared flag, approval required.
- `TokenBudgetPolicy`: max input tokens, summary threshold ratio, recent context ratio, fallback trigger.
- `ContextCompressionEvent`: before tokens, after tokens, strategy, files written, trace ID.

## Design Rules

- User memory is isolated by user ID.
- Organization policy memory is read-only unless application code writes it.
- Large connector payloads should be referenced, not stored raw in long-term memory.
- Memory writes to shared state require explicit policy and future approval hooks.

## Unit Tests

- Route `/memories/`, `/policies/`, and `/skills/` to the expected backend scope.
- Reject writes to read-only policy paths.
- Validate token budget policy values.
- Simulate summarization fallback after context overflow.
- Assert compression events redact sensitive content.

## Edge Cases

- Missing namespace identifiers.
- Concurrent write to same memory file.
- Prompt injection stored in user memory.
- Summarization produces invalid or empty summary.
- Store backend unavailable.


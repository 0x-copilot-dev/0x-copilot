# PRD: Context and Memory Management

## Problem

Enterprise tasks produce long conversations, large connector results, and repeated preferences. Naively putting everything in the prompt causes context overflow, latency, cost, and unreliable reasoning.

## Goal

Use Deep Agents context compression, filesystem offloading, and scoped memory backends to keep active context small while preserving recoverability and personalization.

## User Value

- Users can run long tasks without the agent forgetting the goal.
- Important preferences and policies persist across conversations.
- Sensitive organization memory remains scoped and protected.

## Scope

- Deep Agents default offloading and summarization behavior.
- Memory scopes for user, agent, and organization.
- Read-only organization policy memory.
- Metrics around token budget, summarization, offloading, and recovery.
- Pydantic contracts for memory scope and budget policy.

## Non-Goals

- Replacing Deep Agents summarization before observing it.
- Writing shared organization memory from user conversations without approval.
- Storing raw connector payloads in long-term memory by default.

## Acceptance Criteria

- Specs define `MemoryScope` and token budget contracts.
- User memory is isolated by user ID.
- Organization policy memory is read-only.
- Oversized tool output is offloaded or summarized rather than injected raw.
- Summaries preserve objective, decisions, artifacts, and next steps.

## Edge Cases

- Context overflow error during model call.
- Summarization fails.
- Memory namespace is missing or malformed.
- Concurrent writes to same memory file.
- Prompt injection attempt in writable memory.

## Unit Testing Requirements

- Validate memory scope routing.
- Assert read-only paths reject writes.
- Simulate context budget thresholds.
- Test summarization fallback behavior with deterministic fakes.


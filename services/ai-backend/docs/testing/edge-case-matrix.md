# Edge-Case Matrix

## Runtime Foundation

- Missing user ID, org ID, roles, or model profile.
- Unknown feature flag.
- Invalid permission scope.
- Dependency object missing required protocol methods.
- Safe error serialization with secrets present in raw exception.

## Dynamic Tool Loading

- Duplicate tool name across connectors.
- User loses permission between card listing and full load.
- Tool schema is malformed or too large.
- Connector unavailable at load time.
- Model requests display name instead of stable slug.

## Skills Middleware

- Empty `SKILL.md`.
- Missing `name` or `description` frontmatter.
- Duplicate skill name across sources.
- Missing supporting asset referenced by skill.
- Subagent attempts to access unconfigured skill source.

## Dynamic MCP Loading

- MCP server timeout, auth failure, or unsupported transport.
- Server returns malformed tool or resource schema.
- Resource URI uses unsupported scheme.
- MCP tool name collides with local tool.
- Server returns too many tools for the load budget.

## Context and Memory

- Context overflow during model call.
- Summarization returns empty or invalid summary.
- Read-only organization policy write is attempted.
- Concurrent writes to same user memory file.
- Prompt injection is stored in writable memory.

## Subagents and Async Agents

- Subagent unavailable or queued.
- Supervisor polls immediately after async launch.
- Stale, truncated, cancelled, or unknown task ID.
- User updates task while run is active.
- Subagent returns oversized or malformed result.

## Streaming and Observability

- Missing namespace in stream chunk.
- Unknown event type or stream mode.
- Subagent event arrives before task metadata.
- Summarization tokens appear in user-facing stream.
- Tool result contains secrets or oversized payload.

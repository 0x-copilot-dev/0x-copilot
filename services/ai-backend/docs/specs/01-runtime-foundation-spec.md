# Spec: Runtime Foundation

## Purpose

Define the Deep Agents runtime foundation for the `agent_runtime` package. This spec owns runtime construction, context validation, dependency injection, LangGraph export shape, and shared error handling.

## Architecture

Implemented modules:

- `agent/factory.py`: builds the `create_deep_agent` runtime.
- `agent/graph.py`: exports graph objects for `langgraph.json`.
- `agent/runtime.py`: request-level invocation helpers.
- `settings.py`: typed environment and model configuration.
- `agent/state.py`: typed state aliases and runtime metadata.

The runtime must depend on abstract ports for registries, stores, MCP clients, and subagent runners. Concrete connectors must not be imported by the runtime factory.

## Pydantic Contracts

Required models:

- `AgentRuntimeContext`: `user_id`, `org_id`, `roles`, `permission_scopes`, `connector_scopes`, `model_profile`, `trace_id`, `feature_flags`.
- `ModelConfig`: provider, model name, max input tokens, timeout, temperature, and streaming support.
- `RuntimeDependencies`: tool registry, MCP registry, skill source config, memory backend factory, subagent catalog, stream normalizer.
- `RuntimeErrorEnvelope`: typed error code, safe message, retryable flag, correlation ID.

Use strict mode where possible. IDs and names should be constrained strings, not raw `str`.

## Design Rules

- Single responsibility: runtime construction is separate from connector IO.
- Dependency inversion: runtime receives ports, not concrete SDK clients.
- Liskov substitution: fake and real dependencies must satisfy the same contracts.
- Explicit invariants: reject runtime contexts without user/org identity.
- Do not use untyped dictionaries for agent state beyond LangGraph adapter edges.

## Unit Tests

- Valid runtime context parses and normalizes scopes.
- Missing user ID, org ID, or model config fails validation.
- Dependency injection accepts fakes and rejects missing required ports.
- Permission scopes propagate into tool/MCP/subagent loaders.
- Runtime errors serialize as safe messages without leaking secrets.

## Edge Cases

- Model profile lacks max token information.
- Feature flag is unknown.
- User has no connector scopes.
- Trace ID is missing and must be generated.
- Dependency object is present but does not satisfy the expected protocol.


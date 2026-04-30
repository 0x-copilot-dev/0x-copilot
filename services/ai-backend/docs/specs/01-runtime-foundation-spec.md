# Spec: Runtime Foundation

## Purpose

Define the Deep Agents runtime foundation for the `agent_runtime` package. This spec owns runtime construction, context validation, dependency injection, LangGraph export shape, and shared error handling.

## Architecture

Implemented modules:

- `agent_runtime/execution/factory.py`: resolves authorized runtime inputs and
  creates request-scoped runtime harnesses.
- `agent_runtime/execution/deep_agent_builder.py`: directly calls
  `deepagents.create_deep_agent` with explicit arguments.
- `agent_runtime/execution/graph.py`: exports graph objects for `langgraph.json`.
- `agent_runtime/execution/runtime.py`: request-level invocation and streaming helpers.
- `agent_runtime/settings.py`: typed environment and model configuration.
- `agent_runtime/execution/state.py`: typed state aliases and runtime metadata.

The runtime must depend on abstract ports for registries, stores, MCP clients, and subagent runners. Concrete connectors must not be imported by the runtime factory.

## Pydantic Contracts

Required models:

- `AgentRuntimeContext`: `user_id`, `org_id`, `roles`, `permission_scopes`, `connector_scopes`, `model_profile`, `trace_id`, `feature_flags`.
- `ModelConfig`: provider, model name, max input tokens, timeout, temperature, and streaming support.
- `RuntimeDependencies`: tool registry, MCP registry, skill source config, memory backend factory, and subagent catalog.
- `RuntimeErrorEnvelope`: typed error code, safe message, retryable flag, correlation ID.

Use strict mode where possible. IDs and names should be constrained strings, not raw `str`.

## Design Rules

- Single responsibility: runtime construction is separate from connector IO.
- Dependency inversion: runtime receives ports, not concrete SDK clients.
- Liskov substitution: fake and real dependencies must satisfy the same contracts.
- Explicit invariants: reject runtime contexts without user/org identity.
- Do not use untyped dictionaries for agent state beyond LangGraph adapter edges.
- Do not dynamically import or signature-probe Deep Agents in the runtime
  factory. Keep the installed Deep Agents API call explicit and version-pinned.

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


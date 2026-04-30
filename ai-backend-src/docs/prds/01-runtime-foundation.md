# PRD: Runtime Foundation

## Problem

The backend needs a stable agent harness before feature teams add tools, skills, MCP servers, memories, and subagents. Without a clear runtime foundation, each feature will invent its own state shape, model config, permissions, and testing strategy.

## Goal

Define a Deep Agents-based runtime foundation that creates a predictable, typed, testable execution surface for all future features.

## User Value

- Users receive consistent responses and progress behavior across capabilities.
- Admins can configure model, memory, permissions, and tracing once.
- Developers can add features behind typed contracts without changing the whole runtime.

## Scope

- Deep Agents `create_deep_agent` runtime factory.
- LangGraph graph exports for local development and deployment.
- Typed `AgentRuntimeContext`.
- Dependency injection for registries, stores, MCP clients, and subagent runners.
- Consistent error and stream event contracts.

## Non-Goals

- Final production API shape.
- Production persistence selection.
- Real enterprise connector integration.

## Acceptance Criteria

- Runtime spec defines module boundaries and dependency direction.
- Runtime context is a Pydantic contract.
- Runtime has a unit test plan for config parsing, dependency injection, permission propagation, and typed errors.
- No feature code may bypass the runtime context to access user/org/auth state.

## Risks

- Over-abstracting before real connectors exist.
- Coupling the runtime too tightly to one model provider.
- Treating LangGraph state as an untyped dictionary.

## Unit Testing Requirements

- Validate good and malformed runtime contexts.
- Assert permission scopes propagate to tools and subagents.
- Assert missing dependencies fail early with typed errors.
- Use fake model/runtime dependencies in tests.


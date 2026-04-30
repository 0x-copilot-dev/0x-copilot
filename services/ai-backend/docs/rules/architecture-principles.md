# Architecture Principles

## SOLID and Practical Design

- Single responsibility: modules should have one reason to change.
- Open/closed: add new connectors, MCP servers, tools, and subagents through registries and ports.
- Liskov substitution: fake and real implementations must be usable through the same interface.
- Interface segregation: listing capabilities, loading details, and executing side effects are separate interfaces.
- Dependency inversion: high-level runtime code depends on protocols, not vendor SDKs.

## DRY With Judgment

Remove duplication when it protects a real invariant or repeated policy. Do not create a shared abstraction for code that only looks similar but has different product behavior.

## Boundary Design

- Runtime orchestration decides what should happen.
- Connectors perform external IO.
- Registries describe capabilities.
- Loaders resolve full specs.
- Policies authorize visibility and execution.
- Runtime workers project LangGraph v2 stream parts into UI-safe events.

## Invariants

- Unauthorized capabilities are never visible to the model.
- Full conversation history is not sent to subagents by default.
- Shared organization memory is read-only unless application code writes it.
- MCP and connector data is validated before use.
- Stream payloads are redacted before emission.


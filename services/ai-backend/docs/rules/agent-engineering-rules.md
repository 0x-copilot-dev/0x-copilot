# Agent Engineering Rules

## Architecture-First Workflow

- Read the current architecture docs and relevant technical spec before writing code.
- Do not implement a feature whose spec lacks Pydantic contracts and unit test requirements.
- Keep implementation changes scoped to the feature being built.
- Update the matching spec when implementation reveals a real contract or edge-case change.

## Engineering Bar

- Favor explicit contracts over implicit conventions.
- Separate orchestration from side effects.
- Use dependency injection for tools, MCP clients, stores, and subagent runners.
- Prefer small, composable modules with clear ownership.
- Add abstractions only when they remove real duplication or protect a boundary.

## Review Checklist

- Does the implementation match the architecture and spec contract?
- Are all external inputs parsed into Pydantic contracts?
- Are permission checks applied before capabilities are visible to the model?
- Are errors typed and safe to show?
- Are unit tests focused and deterministic?


# Python and Pydantic Rules

## Typed Boundaries

- Use Pydantic models at IO boundaries: API input, runtime context, tool specs, MCP descriptors, memory scopes, subagent tasks/results, and stream events.
- Do not pass untyped dictionaries as domain state.
- Use enums, literals, constrained strings, and positive integers for known domains.
- Parse external data once at the boundary, then pass typed objects.

## Strong Casting

- Normalize IDs, names, and scopes in validators.
- Reject ambiguous or lossy casts.
- Treat model output as untrusted until parsed into a typed contract.
- Keep serialization explicit with stable public field names.

## Python Style

- Prefer dataclasses or Pydantic models for simple state, not mutable globals.
- Prefer protocols or abstract base classes for replaceable dependencies.
- Avoid broad exception catches unless converting to a typed domain error.
- Keep async boundaries explicit.

## Anti-Patterns

- `dict[str, Any]` as a long-lived domain model.
- Connector SDK objects leaking into runtime contracts.
- Validation hidden in random helper functions instead of model boundaries.
- Stringly typed permissions, statuses, and risk levels.


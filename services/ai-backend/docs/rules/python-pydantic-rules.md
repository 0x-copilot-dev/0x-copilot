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
- Do not hard-code repeated field keys, schema keys, method names, or user-facing messages inline. Put stable keys under a `Keys` class with nested subclasses, and put public/error text under a dedicated messages or exceptions class.
- Keep production helper behavior inside classes as class methods or static methods. Avoid module-level helper functions in runtime code.

## Test Style

- Use mixins for test-only helpers, fake providers, initializers, builders, and repeated constants.
- Concrete test classes should contain only `test_*` unit test methods; keep helper methods and setup utilities on mixins.
- Do not scatter repeated strings through test bodies. Put test constants on the mixin.

## Anti-Patterns

- `dict[str, Any]` as a long-lived domain model.
- Connector SDK objects leaking into runtime contracts.
- Validation hidden in random helper functions instead of model boundaries.
- Stringly typed permissions, statuses, and risk levels.
- Inline string literals for operational keys, safe error messages, or repeated validation text.
- Free-floating production helper functions that should belong to a contract, parser, policy, or validator class.
- Concrete test classes with helper methods, fake classes, setup utilities, or repeated constants mixed into the unit test methods.


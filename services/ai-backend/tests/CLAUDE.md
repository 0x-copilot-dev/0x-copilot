# AI Backend Tests

Focused unit tests with fakes — never network, real credentials, or live LLM calls.

## What to cover

- Valid and invalid Pydantic parsing for every contract.
- Permission denial paths (unauthorized tool / MCP / memory / skill access).
- Duplicate names (tool registry, MCP server registry, skill registry).
- Malformed schemas from external sources (MCP descriptors, model output, connector payloads).
- External failure modes (timeouts, 5xx, malformed JSON).
- Oversized context (token budgets, truncation, refusal paths).

## Assertions

- Assert the **typed error class**, not just that _some_ exception was raised.
- Assert the **safe public message** — never leak internal traceback content to clients or model.

## Test structure

Put fake providers, builders, setup helpers, and repeated constants in **mixins**. Concrete test classes contain only `test_*` methods.

```
class FakeMCPClientMixin: ...
class RuntimeBuilderMixin: ...

class TestSubagentDelegation(FakeMCPClientMixin, RuntimeBuilderMixin):
    def test_denies_unauthorized_tool(self): ...
    def test_propagates_typed_error(self): ...
```

## Running tests

```bash
cd services/ai-backend
.venv/bin/python -m pytest

# single file
.venv/bin/python -m pytest tests/unit/agent_runtime/agent/test_runtime_factory.py

# single test
.venv/bin/python -m pytest tests/unit/agent_runtime/agent/test_runtime_factory.py::TestName::test_method
```

Use **this service's** `.venv`. Never reuse a sibling service's `.venv` or add another service to `PYTHONPATH`.

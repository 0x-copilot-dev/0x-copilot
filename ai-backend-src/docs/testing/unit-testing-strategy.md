# Unit Testing Strategy

## Testing Standard

Every backend feature must ship with focused unit tests before integration tests. The goal is to prove contracts, policy, and state transitions without relying on live LLMs, Slack, Google Workspace, Atlassian, MCP servers, or LangSmith.

## Required Tools

- `pytest` for tests.
- Pydantic validation tests for every contract.
- Fakes for registries, MCP clients, stores, subagent runners, and stream chunks.
- Property tests where parsing or normalization has a large input surface.

## Test Layers

1. Contract tests: Pydantic accepts valid data and rejects malformed data.
2. Policy tests: permissions, memory scopes, risk levels, and read-only paths.
3. Registry tests: list, lookup, duplicate handling, disabled entries, and load failures.
4. Middleware tests: pre/post behavior, injected tools, and safe error handling.
5. State transition tests: async subagent lifecycle, context compression events, and stream normalization.

## Mock Boundaries

Mock external services at the adapter boundary. Do not mock the code under test. For example, use a fake MCP client that returns malformed descriptors rather than mocking the MCP loader's validation function.

## Coverage Expectations

Core runtime infrastructure should target high branch coverage around error paths. Any feature that can cause a side effect, expose data, or alter memory must include tests for denial, malformed input, and retryable failure.

## Required Assertions

Tests should assert:

- Parsed Pydantic model fields, not just truthiness.
- Typed error class/code and safe public message.
- No secret values appear in serialized errors or stream events.
- Unauthorized capabilities are absent before the model sees them.
- Full conversation history is not sent to subagents by default.


# Test Fixtures

## Fixture Principles

Fixtures should make unit tests deterministic and fast. They should model boundaries faithfully while avoiding live external services.

## Required Fixture Families

### Runtime Contexts

- `runtime_context_admin`: full permissions across fake connectors.
- `runtime_context_employee`: limited read-only scopes.
- `runtime_context_missing_identity`: invalid context for validation tests.
- `runtime_context_no_connectors`: user with no enterprise connector access.

### Tool Fixtures

- `fake_tool_card_search_docs`: safe read-only tool.
- `fake_tool_card_send_message`: side-effecting tool requiring approval metadata.
- `fake_loaded_tool_spec_valid`: full schema with Pydantic args.
- `fake_loaded_tool_spec_malformed`: invalid schema for boundary tests.

### MCP Fixtures

- `fake_mcp_server_healthy`: lists valid tools/resources.
- `fake_mcp_server_timeout`: raises timeout.
- `fake_mcp_server_auth_failure`: raises permission error.
- `fake_mcp_server_malformed_schema`: returns invalid descriptors.

### Memory Fixtures

- `fake_user_memory_scope`: user-scoped namespace.
- `fake_org_policy_scope`: read-only organization namespace.
- `fake_token_budget_near_limit`: triggers compression path.
- `fake_injected_memory_content`: validates prompt-injection handling.

### Subagent Fixtures

- `fake_subagent_definition_researcher`: valid co-deployed researcher.
- `fake_subagent_task_compact`: summary-only handoff.
- `fake_subagent_result_valid`: response plus execution and plan summary.
- `fake_subagent_result_oversized`: validates truncation/offloading behavior.

### Streaming Fixtures

- `fake_chunk_main_update`: main-agent update.
- `fake_chunk_subagent_update`: subgraph event with namespace.
- `fake_chunk_tool_call`: tool call message chunk.
- `fake_chunk_summarization`: internal summarization token event.
- `fake_chunk_malformed`: missing required keys.

## Fixture Anti-Patterns

- Do not make fixtures depend on network calls.
- Do not use real API keys.
- Do not hide validation in fixture constructors; tests should be explicit about valid versus invalid data.
- Do not use one giant fixture for every test. Prefer narrow fixtures that describe the behavior under test.

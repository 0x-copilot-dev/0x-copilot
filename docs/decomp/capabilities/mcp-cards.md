# Decomp — `agent_runtime/capabilities/mcp/cards.py`

Source: [services/ai-backend/src/agent_runtime/capabilities/mcp/cards.py](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/cards.py) — **571 LOC, L.** Pydantic contracts for MCP server discovery, dynamic loading, tool/resource descriptors, and tool invocation. Mostly type definitions + Pydantic validators that normalize untrusted input — **no I/O, no business logic beyond validation**.

## A. Top-level structure

| Symbol                                                       |   Lines | Purpose                                                                                                              |
| ------------------------------------------------------------ | ------: | -------------------------------------------------------------------------------------------------------------------- |
| `JsonSchema` (TypeAlias)                                     |      30 | `Mapping[str, Any]`.                                                                                                 |
| `SUPPORTED_RESOURCE_URI_SCHEMES`                             |   31–33 | `{HTTPS, MCP, URN}` — accepted resource URI schemes.                                                                 |
| `McpTransport(StrEnum)`                                      |   36–41 | `STDIO`, `SSE`, `HTTP`.                                                                                              |
| `McpAuthMode(StrEnum)`                                       |   44–50 | `NONE`, `API_KEY`, `OAUTH2`, `SERVICE_ACCOUNT`.                                                                      |
| `McpAuthState(StrEnum)`                                      |   53–61 | `UNAUTHENTICATED`, `AUTH_SKIPPED`, `AUTH_PENDING`, `AUTHENTICATED`, `AUTH_FAILED`, `AUTH_UNSUPPORTED`.               |
| `McpServerHealth(StrEnum)`                                   |   64–70 | `HEALTHY`, `DEGRADED`, `UNAVAILABLE`, `DISABLED`.                                                                    |
| `McpRiskLevel(StrEnum)`                                      |   73–79 | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`.                                                                                 |
| `McpLoadErrorCode(StrEnum)`                                  |  82–100 | 16 typed error codes safe for public surfaces.                                                                       |
| `McpWarningCode(StrEnum)`                                    | 103–106 | Currently `SERVER_DEGRADED` only.                                                                                    |
| `McpServerCard(RuntimeContract)`                             | 109–184 | Compact pre-load summary (visible to model in card lists). 12 fields + 6 validators.                                 |
| `McpLoadRequest(RuntimeContract)`                            | 187–202 | Server discovery+connect request.                                                                                    |
| `McpToolCallRequest(RuntimeContract)`                        | 205–263 | Generic tool invocation; **inlines misplaced top-level kwargs into `arguments`** via `_collect_misplaced_arguments`. |
| `McpToolDescriptor(RuntimeContract)`                         | 266–304 | Validated tool descriptor returned post-load. Includes `input_schema`, `output_shape`, `risk_level`.                 |
| `McpResourceAccessPolicy(RuntimeContract)`                   | 307–316 | `required_scopes`, `read_only=True`.                                                                                 |
| `McpResourceDescriptor(RuntimeContract)`                     | 319–344 | Validated resource: URI scheme allowlist, name+mime+description size caps.                                           |
| `McpConnectionMetadata(RuntimeContract)`                     | 347–374 | Safe connection metadata (no creds): `connection_id` (uuid4), `connected_at`, `latency_ms`.                          |
| `McpLoadWarning(RuntimeContract)`                            | 377–388 | `code` + `safe_message`.                                                                                             |
| `LoadedMcpServer(RuntimeContract)`                           | 391–398 | Composite: server_card + tools + resources + connection_metadata + warnings.                                         |
| `McpLoadError(RuntimeContract)`                              | 401–427 | Typed error: `code`, `safe_message`, `retryable=False`, `server_name?`, `correlation_id`.                            |
| `McpToolCallResult(RuntimeContract)`                         | 430–492 | OK or error envelope with **exactly-one-outcome** invariant. `ok`/`fail`/`fail_from_load_error` factory methods.     |
| `McpLoadResult(RuntimeContract)`                             | 495–533 | Same shape for loaded_server vs error. `succeeded` property.                                                         |
| `McpValueNormalizer`                                         | 536–553 | Re-export shim over `agent_runtime.validation.ValueNormalizer`. Avoids deep-import in validators.                    |
| `McpSchemaValidator.validate_json_schema(value, field_name)` | 556–571 | Validate type-keyed Mapping is < `MCP_SCHEMA_MAX_BYTES`.                                                             |

## B. Feature inventory

| Domain                                                                | Symbols                                                                                          |  LOC |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | ---: |
| **Enum vocabulary (transport, auth, health, risk, errors, warnings)** | 7 StrEnums                                                                                       |  ~75 |
| **Pre-load surface (cards + load request)**                           | `McpServerCard`, `McpLoadRequest`                                                                |  ~95 |
| **Tool invocation (request + result)**                                | `McpToolCallRequest`, `McpToolCallResult`                                                        | ~120 |
| **Post-load descriptors**                                             | `McpToolDescriptor`, `McpResourceDescriptor`, `McpResourceAccessPolicy`, `McpConnectionMetadata` |  ~90 |
| **Result envelopes (OK/error)**                                       | `McpLoadResult`, `McpLoadError`, `McpLoadWarning`, `LoadedMcpServer`                             |  ~85 |
| **Validation helpers**                                                | `McpValueNormalizer`, `McpSchemaValidator`                                                       |  ~40 |

## C. Functional spec per domain

### `McpServerCard` (109–184)

12 fields with strict validation:

- `name`: slug-normalized via `normalize_slug`.
- `server_id`: optional UUID/id (normalized when present).
- `display_name`: optional non-empty string.
- `short_description`: required, 1–`CARD_DESCRIPTION_MAX_LENGTH`.
- `transport`, `auth_mode`, `health`: enum-coerced (StrEnum value or lowercased string).
- `auth_state`: defaults to `AUTHENTICATED` (note default — see D).
- `required_scopes`: frozenset, normalized via `normalize_scope_set`.
- `load_cost`: `PositiveInt` capped at `LOAD_COST_MAX`.
- `enabled = True` default.
- `allowed_org_ids`, `allowed_user_ids`: frozenset of normalized ids.
- `display: ToolDisplayTemplate | None`.

### `McpToolCallRequest` validation rules

**`_collect_misplaced_arguments`** (212–236) — model-level pre-validator:

- If extra top-level keys exist (i.e. caller passed `{server_name, tool_name, foo, bar}` instead of `{server_name, tool_name, arguments: {foo, bar}}`), **inline** them into `arguments`.
- Existing `arguments` mapping wins on conflict (extra_arguments first, then dict(arguments) last).

This is a defensive normalisation — model-generated tool calls sometimes flatten arguments into the top level.

**`_validate_arguments`** (248–263) — JSON-serializability check via `json.dumps(value, sort_keys=True)`. Raises `ValueError` with safe message on failure. Defends against mappings containing non-serializable objects (datetime, set, etc.).

### Result envelope invariants

**`McpToolCallResult._require_exactly_one_outcome`** (438–442): exactly one of `output`/`error` must be set. Boolean XOR: `(output is None) == (error is None)` → both same → reject.

**`McpLoadResult._require_exactly_one_outcome`** (501–505): same rule for `loaded_server`/`error`.

**Factory methods**: `ok(...)`, `fail(...)`, `fail_from_load_error(...)` — consumers should use these rather than constructing the model directly. `fail` auto-generates a uuid `correlation_id` if not supplied.

### `McpResourceDescriptor` URI scheme allowlist (331–339)

Resource URIs must use `https://`, `mcp://`, or `urn:` schemes. Anything else → `Messages.Validation.UNSUPPORTED_RESOURCE_SCHEME`.

### `McpSchemaValidator.validate_json_schema`

1. Must be a Mapping.
2. Must have a `type` key (top-level JSON Schema type).
3. Must be JSON-serializable.
4. Encoded UTF-8 size must be `<= MCP_SCHEMA_MAX_BYTES`.

Returns a copy as plain `dict[str, Any]`.

## D. Bugs / edge cases / invariants

- **`McpServerCard.auth_state` defaults to `AUTHENTICATED`** (121): a card without an explicit auth*state assumes auth is satisfied. Defends against backend providers that don't explicitly stamp the field for never-needs-auth servers — but be cautious: a buggy provider could surface an auth-required server as authenticated. The field is \_required to be set explicitly* in production paths.
- **`McpToolCallRequest._collect_misplaced_arguments`** (212–236): silently fixes up flat-arg payloads. If the model emits `{server_name: "x", tool_name: "y", foo: 1}`, it becomes `{server_name: "x", tool_name: "y", arguments: {foo: 1}}`.
- **Slug normalisation is mandatory** for all `name`/`server_name`/`tool_name` fields — defends against slug collisions, whitespace bleed, case differences.
- **JSON-serializable enforcement** (257–262): tool call arguments must round-trip through `json.dumps`. Sets, datetimes, complex objects fail validation here.
- **Schema size cap** (569–570): `MCP_SCHEMA_MAX_BYTES` enforces UTF-8 encoded size to keep prompt-injection vectors bounded.
- **URI scheme allowlist** (337–338): `urn:` is allowed (some MCP servers use URNs); arbitrary `data://`, `file://`, `ftp://` are rejected.
- **`load_cost` is `PositiveInt`** (124): zero-cost loads are not accepted; minimum 1.
- **`latency_ms` capped at `METADATA_LATENCY_MAX_MS`** (355): bound on connection-meta latency reporting.
- **`correlation_id` auto-uuid** (408): every error gets a correlation id even if caller doesn't supply one.
- **`McpValueNormalizer` re-export shim** (542–553): explicit `del _V` (553) to prevent `_V` from leaking as a class attribute. Workaround for class-body imports binding to the class namespace.
- **`McpLoadError.retryable` defaults to False** (406): conservative — callers must opt in to retry.
- **`McpResourceAccessPolicy.read_only` defaults to True** (311): conservative — write access must be opted in.
- **`McpToolCallRequest.arguments` defaults to empty dict** (210): defends against tool calls with no arguments (e.g. `list_servers`).

## E. Hardcoded vs configurable

### Hardcoded

- All enum vocabularies (transport names, auth modes, error codes, etc.).
- URI scheme allowlist (31–33).

### Configurable (via `Limits` constants)

- `CARD_DESCRIPTION_MAX_LENGTH`
- `LOAD_COST_MAX`
- `DESCRIPTOR_DESCRIPTION_MAX_LENGTH`
- `RESOURCE_NAME_MAX_LENGTH`, `MIME_TYPE_MAX_LENGTH`
- `METADATA_LATENCY_MAX_MS`
- `SAFE_MESSAGE_MAX_LENGTH`
- `MCP_SCHEMA_MAX_BYTES`

These all live in `agent_runtime.capabilities.mcp.constants.Limits` (see [mcp-bundle.md](mcp-bundle.md)).

## F. External dependencies and coupling

### Internal

- `agent_runtime.capabilities.tools.cards.ToolDisplayTemplate`.
- `agent_runtime.execution.contracts.AgentRuntimeContext`, `RuntimeContract`.
- `agent_runtime.capabilities.mcp.constants.Keys`, `Limits`, `Messages`, `Values`.
- `agent_runtime.validation.ValueNormalizer` (lazy-imported via class-body import).

### Stdlib / third-party

- `pydantic.Field`, `PositiveInt`, `ValidationInfo`, `field_validator`, `model_validator`.
- `enum.StrEnum`, `urllib.parse.urlsplit`, `uuid.uuid4`, `datetime`, `json`.

## G. Suggested decomposition seams

The file already groups types into clear sections by usage. Cuts:

1. **`mcp_enums.py`** — all 7 StrEnums + `SUPPORTED_RESOURCE_URI_SCHEMES`. ~75 LOC. Pure vocabulary.
2. **`mcp_card.py`** — `McpServerCard`, `McpLoadRequest`. ~100 LOC. Pre-load surface.
3. **`mcp_descriptors.py`** — `McpToolDescriptor`, `McpResourceDescriptor`, `McpResourceAccessPolicy`, `McpConnectionMetadata`. ~90 LOC. Post-load descriptors.
4. **`mcp_tool_call.py`** — `McpToolCallRequest`, `McpToolCallResult` + factories. ~120 LOC.
5. **`mcp_results.py`** — `McpLoadResult`, `McpLoadError`, `McpLoadWarning`, `LoadedMcpServer`. ~85 LOC.
6. **`mcp_validation.py`** — `McpValueNormalizer`, `McpSchemaValidator`. ~40 LOC.

The `_require_exactly_one_outcome` invariant is **duplicated** between `McpToolCallResult` and `McpLoadResult` (438–442 + 501–505). A shared `ExactlyOneOutcome` mixin or generic could deduplicate.

The `enum-with-str-or-StrEnum-coercion` validator (165–170, 296–304, 362–369) is repeated in **three** places. Could be lifted to `McpValueNormalizer` as `normalize_enum_value`.

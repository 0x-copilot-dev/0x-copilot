# Security Invariants

Hard rules that must hold across every feature, every code path, every adapter.
These are the invariants a reviewer should verify when a PR touches capability loading,
persistence, worker commands, or event emission.

See also:

- [architecture/02-contracts.md](02-contracts.md) — Pydantic boundary policy
- [architecture/00-system-map.md](00-system-map.md) — module import rules
- [features/approvals.md](../features/approvals.md) — approval interrupt model

---

## 1 — Capability loading

**Unauthorised capabilities are never visible to the model.**

- `ToolPermissionChecker.is_card_authorized(card, context)` is called for every tool loaded in
  `acreate_agent_runtime()`. A tool that fails is not added to the model's tool list.
- `McpPermissionPolicy.is_server_card_visible(card, context)` is called for every MCP server
  returned by `DynamicMcpRegistry.list_available_servers()`. A server that fails is absent.
- `SkillPolicyGate.is_skill_visible(manifest, context)` filters skills before system-prompt injection.
- Permission checks happen **twice** for MCP tools: once at list time (server card), once at call time
  (auth_state and scope). A scope revoked mid-run is caught at call time.

---

## 2 — Tenant isolation

**Every caller-facing API method that returns tenant-owned rows accepts `org_id` and constrains.**

- `PersistencePort` methods that list or get rows always accept `org_id` (and `user_id` when the
  resource is per-user). Callers must not query by `run_id` alone on the HTTP path.
- Worker-internal helpers (`update_run_status`, `set_run_latest_sequence`) operate by `run_id` only.
  This is mitigated by: (a) `run_id` is globally unique UUIDs, (b) worker validates the command
  payload matches the persisted row before acting (see §3).
- Approval rows scoped by `(org_id, approval_id)`. Conversation membership enforced upstream in
  `ApprovalCoordinator` before any approval operation.
- `EventStorePort` methods operate by `run_id`. A run belongs to exactly one `(org_id, conversation_id)`;
  a caller that holds a valid `run_id` has already been tenant-validated by the API layer.

**Defence-in-depth option (not yet enabled):** Postgres Row-Level Security on all tenant tables, ensuring
a Postgres role bound to one `org_id` cannot read another org's rows at the DB level.

---

## 3 — Worker command integrity

**The worker validates queued command payloads against authoritative persisted rows.**

Forged or stale queue payloads cannot cause cross-tenant or cross-user side effects:

| Handler                  | Validation                                                                                                                    |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| `RuntimeRunHandler`      | Loads run via `get_run(org_id, run_id)`. Rejects if persisted `conversation_id` or `user_id` differs from command payload.    |
| `RuntimeCancelHandler`   | Loads run. No-ops if `requested_by_user_id` ≠ persisted `run.user_id`.                                                        |
| `RuntimeApprovalHandler` | Loads approval via `get_approval_request(org_id, approval_id)`. Verifies `approval.run_id == command.run_id` before resuming. |

Tests: `tests/unit/runtime_worker/test_worker_command_integrity.py` covers all three handlers.

---

## 4 — Untrusted inputs

**Treat as untrusted until validated with `model_validate()` at the boundary:**

- Model output (tool call arguments, reasoning text)
- MCP tool results and descriptors (tool schemas, resource lists, prompts)
- Memory content — it was written by a previous model turn and may be stale or injected
- Connector/tool result payloads
- HTTP request bodies from `backend-facade`
- Subagent result payloads

**Prompt injection:** `PromptInjectionDetector.scan(value)` is called on every memory value before
it is injected into the system prompt. Memory paths with injection patterns are rejected at read time.

---

## 5 — Credential hygiene

- Provider API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`) are **never** accepted
  in HTTP request bodies. They are env-backed only and resolved in `RuntimeSettings`.
- **BYOK per-user provider keys** arrive only over the trusted service-token lane as the optional
  `provider_keys` field of the `/internal/v1/policies/runtime` snapshot. `ProviderKeysParser.split`
  removes them from the snapshot before it is sealed into the persisted
  `AgentRuntimeContext.user_policies_json`; the keys ride the separate
  `AgentRuntimeContext.provider_keys` field, which is `exclude=True, repr=False` — it never appears
  in `runtime_context_json`, queue/outbox payloads, events, or reprs. Because queue commands
  round-trip through `model_dump(mode="json")`, the worker re-attaches keys in memory at claim time
  via `ProviderKeysHydrator` (run and approval-resume handlers). The key value is injected into
  model construction as the `api_key` kwarg (`user_policy_model_kwargs`), taking precedence over
  the deployment env key.
- OAuth tokens for MCP servers are owned and stored by `backend`'s `TokenVault`. `ai-backend`
  never sees raw tokens; it calls `backend` to create auth sessions and receives only the auth URL.
- Encryption keys and KMS ARNs are in environment variables, not in code or DB rows.

---

## 6 — Event redaction

**Redaction boundary: logs are the only redaction surface.**

The `ObservabilityRedactor` strips sensitive field values before log emission. It does **not** modify
events written to `EventStorePort` or emitted over SSE — the persistence and SSE paths carry data whole.
This is intentional: persistence and SSE carry data to authorised consumers; logs are the compliance
surface (SIEM export, audit scraping).

`Sensitive[]` Pydantic field annotations drive log elision. Exact-match deny-key sets handle
unstructured metadata dicts. Regex-based value scanning was explicitly rejected because it produces
false positives on legitimate data and cannot be meaningfully unit-tested.

User-content carve-out: user message text is length-clipped in logs but not value-redacted, since
it cannot be pre-classified as sensitive.

---

## 7 — Subagent history isolation

**Subagents do not receive the full conversation history.**

A subagent receives only its `SubagentTask` context (goal, constraints, relevant tool results).
It does not receive the parent's message list. This is enforced in `SubagentHandoff.prepare()`.

Violation: a subagent receiving a full history can exfiltrate information from prior turns that
the user did not intend the subagent to see (e.g. credentials, personal data in earlier messages).

Test assertion: verify the messages list passed to `acreate_agent_runtime()` for a subagent run
contains only the task-scoped messages, not the parent conversation messages.

---

## 8 — Audit log immutability

**The audit event table is append-only. Rows cannot be updated or deleted.**

Enforced at three layers:

1. Postgres role: `audit_writer` has `INSERT` only; no `UPDATE` or `DELETE`.
2. Trigger: any attempt to update or delete an audit row raises an exception at DB level.
3. Hash chain: each row carries `HMAC-SHA256(prev_hash || payload, key_version)`. A tampered row
   breaks the chain; the chain head can be verified at any time.

Per-org chain isolation: each org's audit rows are chained independently so one org's breach cannot
expose another's chain head.

**Never mark audit logging complete if the adapter is in-memory-only, mutable, or not exportable
to customer SIEM.** The in-memory adapter is test-only and must not be used in production.

---

## 9 — Provider key scope

**Model selection must resolve to a provider with a configured environment key or a stored
per-user (BYOK) key.**

If the selected provider has neither a deployment env key nor a user key, the run is rejected
at pre-flight with a safe error (`RuntimeErrorCode.CONFIGURATION_ERROR`): the message names the
provider and points the user at Settings -> Provider keys, but never names the expected env var.
`ModelConfigResolver.resolve` receives only the _availability_ of user keys
(`user_key_providers`, a set of provider slugs) — never the key values.

---

## 10 — Path traversal prevention

**Virtual skill paths are not filesystem paths and must never be opened directly.**

Skill asset references in `SKILL.md` frontmatter are validated against an allowlist at load time.
`..` segments and absolute paths are rejected. This prevents a malicious skill bundle from reading
arbitrary files from the worker filesystem.

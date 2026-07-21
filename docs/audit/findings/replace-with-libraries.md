---
id: findings-replace-with-libraries
kind: report
title: Bespoke-replaceable — hand-rolled code a maintained library/service does better
audit_date: 2026-07-20
---

# Replace With Libraries / Services

Bespoke implementations of solved problems. Each is well-tested today, so these are **leverage/maintenance** findings (reduce surface area, get spec-conformance + edge cases for free), not correctness emergencies — except where the hand-rolled version already has a latent bug (flagged). Several also *fix* a risk finding by construction (noted).

Ordering: by maintenance surface removed.

---

### LIB-1. Hand-rolled OAuth 2.1 client + MCP JSON-RPC client on raw `urllib`
**Severity/confidence:** medium/medium · **Verification:** confirmed · **Cluster:** flow-mcp (backend-core F7, backend-platform)
`mcp_oauth.py` (518 LOC) implements RFC 9728/8414 discovery, RFC 7591 dynamic client registration, PKCE, and refresh on raw `urllib.urlopen`; `BackendMcpClient` (`backend_provider.py:165-258`) hand-implements the MCP `initialize`/`tools/list`/`resources/list`/`tools/call` handshake. The blocking `urlopen(timeout=30)` sits inside the sync request path, so every agent tool call is a blocking double hop serialized through FastAPI's threadpool. Two evolving specs tracked by hand. **Latent bugs already present:** the SSRF guard only blocks IP literals while `urlopen` re-resolves DNS at fetch time (RISK-ssrf), and `_decode_remote_mcp_response` returns the *first* SSE `data:` line with no JSON-RPC id match, so a `notifications/progress` emitted first is returned as the tool result (RISK-rpc-sse).
**Evidence:** services/backend/src/backend_app/{mcp_oauth.py:448-455,service.py:761-803}; agent_runtime/capabilities/mcp/backend_provider.py:165-258.
**Remediation:** Replace the OAuth plumbing with `authlib` + `httpx` (async, IP-pinning, redirect control); host the official `mcp` Python SDK client on the backend side against the decrypted token (credential isolation is preserved since tokens still never leave `services/backend`).
**Payoff:** ~700 LOC removed; fixes the DNS-rebind + SSE-flattening latent bugs and the blocking-IO serialization for free.

### LIB-2. Bespoke near-JWT bearer + hand-rolled EIP-4361 parser + hand-rolled TTL-LRU — with libraries already in the tree
**Severity/confidence:** medium/medium · **Verification:** confirmed · **Cluster:** flow-auth
The session bearer is a bespoke base64url-JSON + HMAC-SHA256 with an RFC-7519-style `exp` claim — but **`pyjwt[crypto]` is already a direct dependency** (`requirements.in:23`, used in `identity/jwks.py`). PyJWT would provide `exp`/`nbf`/`iat` validation (fixing RISK-bearer-exp *by construction*), `kid`-based secret rotation, and collapse the three hand-kept codecs (DUP-11) into a dependency. The ~140-line strict EIP-4361 parser (`siwe.py:315-451`) re-implements the maintained `siwe` PyPI package; the facade `_TouchCache` (`auth.py:95-172`) re-implements `cachetools.TLRUCache`.
**Evidence:** services/backend/src/backend_app/identity/{sessions.py:88-141,siwe.py:315-451}; services/backend-facade/src/backend_facade/auth.py:95-172; requirements.in:23.
**Remediation:** Adopt PyJWT for the bearer (highest leverage — also closes the expiry hole across every facade route); evaluate the `siwe` package for parsing; use `cachetools` for the touch cache.
**Payoff:** removes ~300 LOC of hand-rolled security-sensitive code and makes RISK-bearer-exp unrepresentable.

### LIB-3. Two bespoke RFC-5545 rrule evaluators where `croniter`/`dateutil.rrule` fit
**Severity/confidence:** low/high · **Verification:** accepted · **Cluster:** ai-runtime-worker (backend-product F8, DUP-14, SSOT-8)
`CronSpecEvaluator` + `RecurrenceRuleEvaluator` (ai-backend) and a third `RecurrenceRuleEvaluator` (backend todos) each hand-implement the FREQ/BYDAY/INTERVAL subset — the scheduler's own docstring calls them "parallel".
**Remediation:** A single shared schedule-evaluator module or `dateutil.rrule`, once the dead jobs are wired (DEAD-3 decides fate first). Move a golden test-vector file into `service-contracts` so the cross-service pair (SSOT-8) can't drift.
**Payoff:** deletes 3 bespoke calendar implementations.

### LIB-4. LangGraph `PostgresSaver` exists; the runtime uses `InMemorySaver` on Postgres
**Severity/confidence:** high/medium · **Verification:** confirmed · **Cluster:** flow-data (F4)
`runtime_checkpointer` returns a durable `AsyncSqliteSaver` only on `RUNTIME_STORE_BACKEND=file`; every other deployment (postgres, web) keeps a process-local `InMemorySaver`, so graph/approval continuation does not survive a worker restart (and `runtime_checkpoints` is never written — DEAD-7). `langgraph.checkpoint.postgres.PostgresSaver` is a drop-in maintained option. (This is also RISK-checkpoints.)
**Evidence:** services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py:301-349.
**Remediation:** Adopt `PostgresSaver` for the shared-store path (or loudly document the restart-behaviour gap).
**Payoff:** durable graph state on web/self-host with zero bespoke code.

### LIB-5. Tolerant hand-rolled run-contract parsers instead of api-types type guards
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** chat-surface-destinations (F6)
`parseRunList` accepts bare array / `{runs}` / `{items}` with snake- or camel-case, and `runIdFromCreateResponse` accepts `run_id`/`runId`/`id` flat-or-nested — this permissiveness hides real facade/api-types drift and silently degrades to "no prior runs" on a wrong shape (and the list endpoint doesn't even exist — DEAD-13). The stream path already shows the right pattern (`isRuntimeEventEnvelope` from api-types).
**Evidence:** packages/chat-surface/src/destinations/run/{useRunSession.ts:387-454,RunDestination.tsx:660-677}.
**Remediation:** Add `RunListResponse`/`CreateRunResponse` + type guards to api-types, make the facade contract explicit, delete the tolerant parsers (and the dead `toError`).
**Payoff:** ~120 LOC + surfaces contract drift instead of hiding it.

### LIB-6. Error handling by prose shape-sniffing + regex instead of machine-readable codes
**Severity/confidence:** medium/high · **Verification:** confirmed · **Cluster:** flow-contracts (F8)
`http.ts:59-72` shape-sniffs `{"detail"}` and passes non-JSON verbatim; `mcpErrors.ts:11-12` **regexes the backend's human-readable message** to detect "OAuth setup required" — a backend copy-edit silently breaks the "Setup required" CTA. The backend already exposes a typed `code`/`details` on `ApiErrorResponse` (`errors.py:22`) that the FE ignores.
**Evidence:** apps/frontend/src/api/{http.ts:47-70,mcpErrors.ts:11-12}; services/ai-backend/src/runtime_api/schemas/errors.py:22.
**Remediation:** Consume the machine-readable `code` field end-to-end; remove the regex coupling.
**Payoff:** removes a prose-coupled UX break class.

### LIB-7. SSE frame parser recognizes only LF-LF boundaries
**Severity/confidence:** low/medium · **Verification:** accepted · **Cluster:** shared-packages (F5)
`packages/chat-transport/src/web/sse.ts:68` splits on `"\n\n"`; the SSE spec also permits CRLF (`\r\n\r\n`), where every event would buffer forever — works against uvicorn today, silently dies behind a CRLF-normalizing proxy. `eventsource-parser` is a tiny maintained dependency.
**Remediation:** Split on `/\r?\n\r?\n/` + a CRLF unit test, or adopt `eventsource-parser`.

### LIB-8. `PromptInjectionDetector` 5-phrase substring blocklist presented as an injection guard
**Severity/confidence:** low/medium · **Verification:** accepted · **Cluster:** ai-runtime-execution (F13)
Trivially bypassed; acceptable as documented defense-in-depth but security theater on its own and likely to lull reviewers.
**Remediation:** Label as heuristic-only in the compliance narrative, or evaluate a maintained classifier at the capability-middleware layer.

### LIB-9. Hand-rolled Keccak-256 / EIP-55 (deliberate — leave, do not extend)
**Severity/confidence:** low/high · **Verification:** accepted · **Cluster:** frontend-web (F11)
`utils/keccak256.ts` (129-LOC BigInt Keccak-f[1600]) exists only to avoid pulling viem/wagmi for one 40-char hash; pinned to official test vectors. Defensible, but it is hand-rolled crypto in an auth path.
**Remediation:** Keep as-is with the existing "do not extend" comment; any future wallet-feature expansion should switch to a vetted library.

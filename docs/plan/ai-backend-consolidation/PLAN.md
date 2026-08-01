# ai-backend consolidation — implementation plan

Companion to [docs/audit/ai-backend-smells/DELETE-REPLACE.md](../../audit/ai-backend-smells/DELETE-REPLACE.md).

Three constraints govern this work, and each one gets a **gate that lands before the refactor
it governs**. A principle without an executable check is a preference, and this codebase has
already demonstrated that preferences do not survive contact with a deadline.

---

## 0. Correction: the MCP claim in the audit was wrong three times

The audit said `capabilities/` is "mostly MCP, and that's legitimately yours." All three parts
are false.

**It is not mostly MCP.** MCP is **8,356 of 76,191 LOC — 11%**. The larger blocks are
`concurrency` (10,164) and `sandbox` (8,957).

**It is not a client for MCP.** `capabilities/mcp` is a client for **our own
`/internal/v1` proxy** — `BackendMcpProvider` issues HTTP to
`/internal/v1/mcp/cards`, `/auth/start`, `/client-session`. The MCP protocol itself lives in
`services/backend`: **3,569 LOC**, including **431 LOC of hand-rolled transport that frames
raw JSON-RPC**.

**It is not legitimately ours.** Neither service installs the official `mcp` SDK **or**
`langchain-mcp-adapters`. `MultiServerMCPClient` already provides:

| We hand-rolled                                                    | The adapter ships                                                                      |
| ----------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `McpTransport` STDIO/SSE/HTTP (stdio still on an unmerged branch) | stdio, streamable_http, http/sse                                                       |
| descriptor parsing → LangChain tools                              | LangChain-compatible tools directly                                                    |
| `McpDispatcherUnwrap` name resolution                             | server-name tool prefixing                                                             |
| opaque session leases (`1526e02c`)                                | stateless per call, or persistent via `client.session()`                               |
| **P1-1: pass the server's error text to the model**               | **default: error returns as `ToolMessage(status="error")` so the agent self-corrects** |

That last row is the finding. **The single highest-value fix in the MCPMark PRD — estimated
+15–30pp — is the documented default behaviour of the library we did not adopt.**

~11,900 LOC across two services, to reimplement a dependency, badly enough that its best
feature is the one we broke.

## 1. The three constraints, as gates

### G1 — Plug and play, not tightly coupled

**Rule.** A capability that a declared dependency provides is **configured, not
reimplemented**. Custom code earns its place only by carrying a semantic the dependency cannot
express, and that semantic must be _named in the module docstring_.

**Gate — `tools/conformance/test_no_reimplementation.py`.** A pinned inventory of capabilities
the dependencies provide (`deepagents.middleware.*`, `langchain_mcp_adapters`, the `mcp` SDK).
For each, an assertion that we either import it or carry a recorded, reviewed exemption with a
reason. New exemptions require a line in the inventory — which makes "we rebuilt it" a
reviewable diff instead of an accident.

This is the same shape as the AST conformance gate already sweeping `_model_visible_tools`,
so it is a pattern the repo already runs, not a new idea.

**Why a gate and not a review rule:** every module in [DELETE-REPLACE.md](../../audit/ai-backend-smells/DELETE-REPLACE.md)
§A was written by someone who believed it was necessary. Review did not catch it. A pinned
inventory does.

### G2 — Single source of truth

**Rule.** One concept, one owner, one default. A constant that appears in two places is a bug
whether or not the values currently agree.

**Gate — `tools/conformance/test_single_default.py`.** Fails when two modules define the same
named default with different values. Seeded with the case already found:
`tool_call_budget` is **5** in `ModelRuntimeConfig` and **10** in `RuntimeExecutionSettings`,
and both docstrings insist the prompt and the enforced cap must agree.

**Extension.** The same check covers the prompt-vs-enforcement pair explicitly: the number
interpolated into the model's prompt suffix and the number the middleware admits against must
be read from one symbol. Today they are two.

### G3 — Services with the right boundary

**Rule, restated from CLAUDE.md and sharpened.** `backend` owns **tenant state**;
`ai-backend` owns **orchestration**. The current split misreads that: because backend owns MCP
_credentials_, it was also given MCP _transport_, so every tool call makes an HTTP hop through
another service and ai-backend carries 8,356 LOC of client for it.

**Corrected boundary:**

| Concern                                | Owner                    | Why                                                   |
| -------------------------------------- | ------------------------ | ----------------------------------------------------- |
| MCP registry, OAuth dance, token vault | `backend`                | tenant state, credentials at rest                     |
| **MCP session + tool execution**       | **`ai-backend`**         | orchestration; it is the thing holding the agent loop |
| Short-lived scoped credential issuance | `backend` → `ai-backend` | once per run, not once per call                       |

`ai-backend` asks `backend` for a **short-lived scoped credential**, then opens its own MCP
sessions via `MultiServerMCPClient`. Raw vault secrets never leave `backend`; what crosses is
a narrow, expiring grant.

**Gate — extend the existing boundary check.** The repo already forbids cross-service `src`
imports. Add: `ai-backend` must not implement protocol framing (no `jsonrpc` literals, no
transport construction outside the adapter), and `backend` must not import the agent runtime.

**This is a boundary improvement, not a relaxation.** Today orchestration is entangled with
another service's transport. After, each service owns one thing.

## 2. Phases

### Phase 0 — Gates first (blocking)

G1, G2, G3 gates land **before** any deletion. A refactor that removes duplication without a
gate re-accumulates it; that is how 299k LOC happened.

**Effort:** M · **Risk:** low · **Deletes:** nothing

### Phase 1 — The free wins

1. **Configure `deepagents.SummarizationMiddleware`.** Cancels PRD P2-1 and P2-2 as builds.
   Verify the keep window preserves identifiers created earlier in a run — the PRD's stated
   risk and HARBOR's actual failure.
2. **Delete `tool_result_admission_gate`** (413 LOC) — duplicates
   `_offload_tool_message_content`.
3. **Delete 8 dead orphan modules** + their tests (~8,600 LOC).
4. **Wire `approval_expiry_sweeper`** — do not delete; nothing else expires stale approvals.
5. **Wire `extract_error_text`** (P1-1) — one call site, five existing tests. Keep even after
   Phase 2 makes it redundant, because Phase 2 is months away.

**Effort:** M · **Risk:** low · **Deletes:** ~9,000 LOC
**Lands before any benchmark sweep** — every item changes what the harness does.

### Phase 2 — MCP consolidation

The largest correctness win and the one that fixes the boundary.

1. Add `langchain-mcp-adapters` + the official `mcp` SDK to `ai-backend`.
2. Replace `capabilities/mcp` transport/descriptor/session layers with `MultiServerMCPClient`.
   **Keep** our permission checks, scope gating, citation projection and audit — those are the
   named semantics that justify custom code under G1.
3. Move MCP session ownership to `ai-backend`; `backend` retains registry, OAuth, vault, and
   gains short-lived scoped credential issuance.
4. Delete `backend`'s hand-rolled `mcp_transport.py` (431 LOC of JSON-RPC framing).
5. **stdio arrives for free** — currently blocked on an unmerged branch and the critical path
   for three of five MCPMark environments.

**Effort:** L · **Risk:** medium-high — touches the credential path, needs security review
**Deletes:** plausibly ~8,000 LOC across both services
**Also delivers:** P1-1 by default, stdio, and one fewer network hop per tool call

#### 2a. Module-by-module: can the adapter actually replace this?

The honest answer is **about a third replaces cleanly, a third moves into an interceptor, and
a third is an open question dominated by one subsystem.** Not a drop-in.

**The adapter's surface** (verified against its published reference): `MultiServerMCPClient`;
`StdioConnection` / `SSEConnection` / `StreamableHttpConnection` / `WebsocketConnection`;
`McpHttpClientFactory` (custom `httpx` client — the hook for auth headers and TLS);
`load_mcp_tools` / `convert_mcp_tool_to_langchain_tool`; `load_mcp_prompt`,
`load_mcp_resources`, `get_mcp_resource`, `load_mcp_server_info`; `create_session`;
`ToolCallInterceptor` + `MCPToolCallRequest`; `Callbacks` (logging, progress, elicitation);
`MCPToolCallResult`, `MCPToolArtifact`, `ToolMessageContentBlock`.

**`ToolCallInterceptor` is what makes this viable.** It is the documented hook for modifying
requests and responses, so our permission checks, audit, approval gate and citation projection
move there rather than being lost. Without it this would be a trade of custom code for lost
semantics; with it, it is a straight substitution.

**Replace outright — ~1,854 LOC:**

| Ours                       | LOC | Adapter equivalent                                       |
| -------------------------- | --- | -------------------------------------------------------- |
| `backend_provider.py`      | 788 | `MultiServerMCPClient` — the proxy client stops existing |
| `client.py`                | 213 | adapter's own connection/session types                   |
| `annotations.py`           | 129 | MCP SDK descriptor types                                 |
| `dispatcher.py`            | 114 | server-name tool prefixing                               |
| `execution_services.py`    | 106 | `create_session` / connection config                     |
| `outcomes.py`              | 73  | `ToolMessage(status="error")` by default                 |
| `backend/mcp_transport.py` | 431 | the SDK's JSON-RPC framing                               |

**Replace partially** — `cards.py` (706), `loader.py` (668), `constants.py` (452),
`discovery_cache.py` (360). Descriptor contracts, discovery and pagination are the adapter's;
what survives is our validation, our card shape for the picker, and whatever error vocabulary
outlives passing the server's own text through.

**Keep, relocated into `ToolCallInterceptor`** — `operation_adapter.py` (551),
`gateway_context.py` (114), `permissions.py` (64). These are the named semantics that justify
custom code under G1: scope gating, the approval gate, effect staging, citation projection.

**Keep as product surface** — `registry.py` (161, picker cards), `files.py` (459, desktop
config persistence), `control_plane_metrics.py` (138, our OTel vocabulary),
`effect_material.py` / `target_ref.py` / `material_resolver.py` (189, worker plumbing).

#### 2b. The open question: 2,253 LOC of descriptor-revision machinery

`freshness.py` (813) · `revision_feed.py` (590) · `descriptor_revision_binding.py` (344) ·
`revision_resolver.py` (316) · `revision_wire.py` (190) — plus store adapters in all three
backends, `runtime_worker/mcp_revision_poller.py`, `mcp_revision_composition.py`,
`capability_descriptor_revisions.py`, and event-schema surface.

**It is live and heavily wired** — 15 importers for `revision_resolver` alone. This is not
dead code.

It is a **cache-coherence subsystem for MCP tool descriptors**, and it exists because
descriptors are fetched through a proxy and cached, so they can go stale. The adapter is
stateless by default — a fresh session per tool call, or an explicit persistent one — so
"is my cached descriptor stale?" largely stops being a question you have to answer.

**But do not assume it is waste.** There may be a genuine product reason for revision
tracking: notifying a user when a connector's tools change, or pinning descriptors so a run is
reproducible and auditable. **Answer that question before touching it** — it is the single
largest block in the MCP layer, larger than the transport it supports, and the only one where
the case for deletion is architectural inference rather than measurement.

If revision tracking survives as a product requirement, it shrinks rather than disappears:
tracking what a server advertises is much less code than tracking what our cache believes.

### Phase 3 — Adapter collapse

One SQL implementation, Postgres and SQLite dialects, replacing three hand-written adapters of
a 116-method port (47,063 LOC; `runtime_api_store.py` alone at 7,698 + 4,365 + 3,305).

Stage behind the existing port so both run during migration. The file adapter is the desktop
default, so this cannot be a flag day.

**Effort:** XL · **Risk:** high · **Deletes:** ~25,000 LOC
**Also kills a bug class:** tests currently run the adapter that cannot exhibit the
`SELECT *` / `extra="forbid"` failure that shipped.

### Phase 4 — Framework overlap, one module at a time

`capabilities/skills` (1,361) · `context/memory` (1,990) · `capabilities/tools` permissions
(4,215) · `delegation/subagents` (3,134).

Each gets the G1 question answered in writing before any code moves: **what semantic does ours
carry that the framework's cannot express?** `context/memory` is first, because the answer
today is "path policy and prompt-injection rejection" and
[FINDINGS.md](../../audit/ai-backend-smells/FINDINGS.md) §2a shows neither executes — so it is
paying for a custom implementation and getting the framework's behaviour anyway.

**Effort:** L · **Risk:** medium · **Deletes:** up to ~10,700 LOC, realistically less

## 3. What this adds up to

| Phase       | Deletes            | Risk     |
| ----------- | ------------------ | -------- |
| 0 gates     | 0                  | low      |
| 1 free wins | ~9,000             | low      |
| 2 MCP       | ~8,000             | med-high |
| 3 adapters  | ~25,000            | high     |
| 4 overlap   | ~5,000–10,000      | med      |
| **total**   | **~47,000–52,000** |          |

Roughly **one sixth of `src`**, plus a comparable amount of test code that exists only to
cover it.

## 4. What I am not claiming

- **The LOC estimates for Phases 2–4 are sized from module totals, not from a migration
  spike.** Phase 1's number is firm because those modules are already proven unreachable.
- **§A2 of the audit remains hypotheses.** Phase 4 is scheduled as four investigations, not
  four deletions.
- **The MCP boundary change is a design proposal, not a measured result.** It needs security
  review on the credential-issuance path specifically, and that review can reject it.
- **Deleting code is not the goal.** The goal is that the next capability we need is
  configured rather than written. The LOC number is the symptom being tracked, not the target.

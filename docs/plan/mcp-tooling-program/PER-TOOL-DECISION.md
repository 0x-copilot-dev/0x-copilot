# Per-tool registration vs. catalog + gateway — the dispatch decision

**Status:** DECISION PROPOSAL · **Base:** `833e7d25` (P2-8 at HEAD of `claude/lineara-connection-issue-e9bd20`)
**Parent:** [`PRD.md`](./PRD.md) §7 "The open architectural question" · **Sibling:** [`TOOL-SEARCH-PLAN.md`](./TOOL-SEARCH-PLAN.md) §4.2, [`DELETIONS-PLAN.md`](./DELETIONS-PLAN.md)
**Owner:** ai-backend · **Decides:** whether ~3.2k lines of committed production code ship or are retired

---

## 0. TL;DR

**Recommendation: Option A — the MCP filesystem catalog plus the existing `call_mcp_tool` gateway.**
Retire the per-tool registration lane rather than ship it. Do it after the item-1 measurement, and
only if the two measured triggers in §7 do not fire.

But the case for A is **not** the case the PRD states, because **the PRD's blocker no longer
exists**. Two corrections come first (§1), and one of them is large enough that it would be
dishonest to reach the recommendation without it:

1. **The `tool_call_id` / approval-id blocker is already fixed, in the committed P2-8 code, with
   direct regression tests.** Per-tool no longer collides two writes on one approval record. The
   "run parks forever" hazard is not a reason to reject B.
2. **The `test_mcp_per_tool_gate_e2e.py` docstring says T1/T2/T3 are RED. They are GREEN.** All four
   cases pass at HEAD; the docstring was written mid-commit and the blocker it names was fixed in the
   same commit.

With those corrections applied, B is genuinely viable, and the decision turns entirely on **context
cost** and **seam count** — not on a defect. That is a better basis for a decision and a worse one
for B: A wins on the axis the whole program exists to fix, and B's compensating mechanism
(`defer_loading`) works on one of six providers.

**Code retired:** A retires **~3,230 lines of production code and ~4,210 lines of tests** (up to
~4,100 / ~5,340 if the credential plane goes with it). B retires **~570 lines of production code and
~1,920 lines of tests**, and _adds_ required follow-on work. C retires nothing.

---

## 1. Two corrections to the premise, verified before anything else

### 1.1 The `InjectedToolCallId` blocker is dead

The framing that reached me was: _"a raw JSON-Schema `args_schema` cannot declare LangChain
`InjectedToolCallId`, so the per-tool path gets no `tool_call_id`, the write-approval id falls back
to the tool name, and two writes to one tool in a run collide on one approval record and the run
parks forever."_

That was true of **P2-4**. It is not true of **P2-8**. The fix is a different seam entirely — not the
schema:

| Fact                                                                                 | Evidence                                                                                                                                                                            |
| ------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `BaseTool.arun` takes `tool_call_id` as a first-class keyword                        | `langchain_core` 1.4.9, `tools/base.py` — `async def arun(..., tool_call_id: str \| None = None, **kwargs)`                                                                         |
| `_prep_run_args` populates it from the `ToolCall`'s `id` on every graph-driven call  | `langchain_core/tools/base.py::_prep_run_args` — `tool_call_id = cast("ToolCall", value)["id"]`                                                                                     |
| The POLICY stage reads it there and carries it to `_arun` on a per-call `ContextVar` | [`middleware/policy_tool.py:271-293`](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/policy_tool.py) (`arun` override) + `:182-211` (`PolicyCallScope`) |
| The approval id is `mcp_write:{run_id}:{tool_call_id}`                               | `policy_tool.py:448-475` (`_approval_id`)                                                                                                                                           |
| **No schema is touched.** `args_schema` is propagated by identity                    | `policy_tool.py:245-269` via `ToolSchemaIdentity.fields_of`; asserted at `tests/.../middleware/test_policy_tool.py:373` — `wrapped.tool.args_schema is inner.args_schema`           |

And it is regression-tested, at exactly the failure the hazard names:

- `test_two_calls_to_one_tool_get_distinct_approval_ids` — `tests/unit/agent_runtime/capabilities/mcp/middleware/test_policy_tool.py:597-611`. Two writes to one tool, two distinct ids.
- `test_langchain_dispatch_supplies_the_per_call_approval_id` — `:613-628`. Driven through the real
  `ainvoke({... "type": "tool_call", "id": ...})` path, so the id is LangChain's, not the harness's.
- `test_approval_id_is_stable_across_the_park_and_resume_passes` — `:585-595`. The other half:
  determinism across the interrupt replay.

I ran the suite. `tests/unit/agent_runtime/capabilities/mcp/middleware/` +
`tests/unit/agent_runtime/execution/test_mcp_per_tool_flip.py`: **136 passed**.

> The residual in PRD §7 AC1 is still real but much smaller than described: the **tool-name fallback**
> at `policy_tool.py:474` (`suffix = tool_call_id or self.name`) remains reachable only from direct
> `_arun` invocation — unit tests and replay. PRD §7 AC1's own recommendation ("if no production path
> exists, make the fallback raise") is a ~3-line change, not a research item.

### 1.2 The per-tool e2e is green, not red

`tests/unit/runtime_worker/test_mcp_per_tool_gate_e2e.py:49-62` states **"STATUS: T1 / T2 / T3 are
RED on a blocker this file found"** — `wrap_args_schema` reading `args_schema.__name__` on a dict
schema. That blocker was fixed **in the same commit** the docstring shipped in:
[`capabilities/middleware/display_metadata.py:492-495`](../../../services/ai-backend/src/agent_runtime/capabilities/middleware/display_metadata.py)
now returns a raw JSON-Schema `Mapping` unchanged, and `git log -- display_metadata.py` shows
`833e7d25` (P2-8) as its most recent touch.

I ran the file: **4 passed in 2.39s**. The docstring is stale and actively misleading — anyone
costing this decision from that docstring will conclude per-tool does not work at all.

> **Action regardless of which option wins:** that STATUS block must be corrected or deleted. It is
> the single most load-bearing wrong sentence in the MCP subtree.

### 1.3 What this changes about the decision

It removes the _only_ correctness argument against B. Everything below is therefore an argument about
**economics, seam count, and provider reach** — where the evidence is much less flattering to B, but
also much less absolute. A reader who weights argument accuracy higher than I do can reach B from the
same facts. I say so explicitly in §7.

---

## 2. The axes, defined before anything is scored

Six axes. Each is stated as _what would make an option lose it_, so the scoring is falsifiable rather
than a vibe.

| #     | Axis                          | Loses when                                                                                                                                                               | How it is measured                                                                                                                                            |
| ----- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **1** | **Context cost**              | Tool definitions consume prompt budget proportional to connector size, on every turn, for every run that has the connector connected — whether or not the run touches it | Bytes/tokens of tool definitions resident in the request, per connected connector                                                                             |
| **2** | **Argument accuracy**         | The model must synthesise arguments the provider cannot see a schema for, and nothing local validates them before the connector is hit                                   | Whether the tool's JSON Schema reaches the provider's tool definition; whether anything validates arguments before dispatch                                   |
| **3** | **Policy correctness**        | The authorized call and the dispatched call can differ; approval identity can collide; a decision can be bypassed; an operation escapes the audit plane                  | Where the `(server, tool)` binding comes from; where the PDP runs; what is written to the durable ledger                                                      |
| **4** | **Provider portability**      | A mechanism required to make the option affordable exists on a subset of the six supported providers                                                                     | `ModelConfigResolver.PROVIDER_ALIASES` (`execution/models.py:54-72`) admits `anthropic`, `gemini`, `openai`, `openrouter`, `ollama`, custom-OpenAI-compatible |
| **5** | **Granularity**               | Per-connector-tool budgets, tool-use policy, and approval config cannot be expressed, because the runtime keys them on the model tool NAME                               | `RuntimeToolControl` / `ToolUsePolicyEnforcer` key on `tool_name` from the tool call                                                                          |
| **6** | **Code retired vs. retained** | The option keeps two of everything, or requires new work before it is even affordable                                                                                    | LOC of production + test code deleted; LOC and count of _required_ follow-on items                                                                            |

Two axes the task named that I have folded in: _blocker cost_ collapses into axis 3 (there is no
blocker left — §1.1), and _code retired vs. retained_ is axis 6.

---

## 3. What each option actually is, in code

### Option A — catalog + `call_mcp_tool` gateway (today's live default)

- Registration: **one** model tool, `_structured_tool(CallMcpTool, McpToolCallRequest)` —
  [`execution/factory.py:756-771`](../../../services/ai-backend/src/agent_runtime/execution/factory.py).
  Plus `load_mcp_server` (`:726-738`) and `auth_mcp` (`:772-785`).
- Model-visible arguments: `{server_name, tool_name, arguments: dict}` —
  [`mcp/cards.py:240-252`](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/cards.py).
- Decision: `CallMcpTool._authorize_mcp_dispatch` → `McpDispatchPolicy.evaluate` → the P1a PDP —
  [`middleware/call_tool.py:340-386`](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/call_tool.py).
- Dispatch: through the **operation gateway** — `OperationRequestFactory` → argument store →
  `services.gateway.invoke(request, McpOperationAdapter)` (`call_tool.py:185-287`), and through the
  **backend RPC proxy** (`mcp/backend_provider.py:1` — _"MCP provider and client that proxy calls
  through the core backend's internal API"_). Credentials never enter ai-backend.
- Discovery (item 1, **not yet built** — `grep -rn "SERVER.md" src/ tests/` returns nothing):
  `/mcp/<server>/SERVER.md` + `/mcp/<server>/tools/<tool>.json`, read with `ls`/`grep`/`read_file`.

### Option B — catalog + per-tool registration (the committed P2-8 lane, flag OFF)

- Registration: **N** model tools, one per real MCP tool, each wrapped POLICY → EXEC_POLICY →
  OBSERVE → ERROR_MAP → CITATIONS — `per_tool_registration.py:381-421`, composed at
  `factory.py:739-755`. `mcp_dispatcher` stays `None`, so `call_mcp_tool` is not registered.
- Model-visible arguments: the server's own `inputSchema`, verbatim, as `args_schema`
  (`langchain-mcp-adapters` 0.3.1 assigns `tool.inputSchema`, typed `dict[str, Any]` by `mcp` 1.29.0).
- Decision: `PolicyGatedMcpTool._authorize` → the same `McpDispatchPolicy.evaluate` — `policy_tool.py:362-411`.
  The binding is fixed at wrap time (`_card_for`, `:518-536`).
- Dispatch: straight into the adapter-built tool. **The operation gateway is not on this path** — no
  `operation_id`, no canonical-argument ref, no `OperationOutcome`/receipt. Confirmed by reading all
  five middleware stages: none imports `capabilities/operations/*`.
- Credentials: direct-connect, needs a live `CredentialProvider` + `McpConnectionDirectory`
  (`tool_source.py:607-629`). Production injects neither — `execution/contracts.py:875`,
  `factory.py:606-620` — so the registrar returns `None` and the run silently keeps the gateway.

### Option C — hybrid (small trusted set per-tool, long tail on the gateway)

Not implementable without new contract surface. There is **no tool-granularity selection anywhere on
the registration path**: `AuthorizedCardLister` filters at _server_ granularity
(`tool_source.py:293-327`), `McpToolSource._describe` drops only reserved-name and duplicate-name
collisions (`:631-692`), and `McpServerCard` has **no** per-tool allowlist field (`cards.py:127-154`).
`McpServerConfigFile.allowed_tools` exists (`mcp/files.py:224`) and is **dropped by `to_card()`**
(`files.py:238-253`). C therefore begins with new work, not with a subset of existing work.

---

## 4. Scoring, axis by axis

### Axis 1 — Context cost · **A wins, decisively**

The live Linear descriptor is **70,465 bytes / 52 tools** (PRD §1.1, from the failing desktop run);
`TOOL-SEARCH-PLAN.md:218-220` puts that at **~17k tokens** if every schema were resident. Today they
are not: the gateway registers one definition, and the model's tool list is ~15–20 definitions total
(`TOOL-SEARCH-PLAN.md:222-225`).

- **A:** 1 gateway definition + `load_mcp_server` + `auth_mcp`, plus a `SERVER.md` bounded at **≤4 KB**
  per connected server (PRD §1.4 AC2). Cost is **O(1) in tools, O(connectors) in ~1 KB units.**
- **B:** every tool of every authorized, healthy, enabled connector enters the prompt —
  `tool_source.py:538-557` registers whatever `client.get_tools()` returns, with no filter beyond
  name collisions. Cost is **O(total tools)**. Two connectors the size of Linear is ~34k tokens
  resident before the user has typed anything.
- **B's mitigation is single-provider.** `defer_loading` withholds definitions from the prompt, but
  only on Anthropic (`TOOL-SEARCH-PLAN.md:36-48`), and the run that produced the original failure was
  **GPT-5.4 mini** (PRD §1.3). On the other five providers B has no mitigation at all.

This axis is the reason the program exists. The catalog is a context-budget fix (PRD §1.2: _"52 tools
× full schema is not something to shrink; it is something to not send"_). B sends it.

> **Honest counterweight:** A's cost is not free either — it is 2–3 extra agent turns (`ls` → `grep` →
> `read_file`), each re-sending the whole conversation. `TOOL-SEARCH-PLAN.md:250-253` makes this
> point well and rates the latency argument for B as _more_ defensible than the token argument. On a
> long conversation, three extra round-trips of a 40k-token context is not obviously cheaper than
> 17k resident tokens. **This is the one place where the measurement could genuinely invert the
> verdict**, and it is trigger T1 in §7.

### Axis 2 — Argument accuracy · **B wins, but by less than it looks**

- **B** puts the server's own JSON Schema into the provider's tool definition, so provider-side
  constrained decoding applies. That is a real and well-established accuracy win.
- **A** hands the model `arguments: dict[str, Any]` (`cards.py:251`) and expects it to have read the
  schema out of `/mcp/<server>/tools/<tool>.json`. Nothing in the provider constrains that object.

But two facts shrink the gap:

1. **Neither path validates arguments locally.** For a dict `args_schema`, LangChain's `_parse_input`
   short-circuits: `if isinstance(input_args, dict): return tool_input` (`langchain_core/tools/base.py`).
   So on B the connector is still the first validator. On A it likewise is. B's advantage is
   _entirely_ provider-side generation quality; it buys **no** local type safety.
2. **A can close most of the gap cheaply, and B cannot.** The catalog stores each tool's full input
   schema on disk by construction (PRD §1.2). Validating `parsed_input.arguments` against that stored
   schema inside `CallMcpToolInputParser` (`call_tool.py:439-458`) — one place, one contract — turns a
   remote 400 into a typed, retryable, model-readable error naming the missing field. That is
   _better_ feedback than B gets today, because B has no local validation at all.

Score B ahead on this axis, but note it is the only axis B wins outright, and it is partially
recoverable by A.

### Axis 3 — Policy correctness · **Split: B is stronger on binding, A is stronger on audit**

**Where B is genuinely better.** The policed tool is the wrapper's own `name` and its bound card, so
"the call that dispatches and the call that was policed are the same call by construction"
(`policy_tool.py:31-37`). On A the identity is decoded from a model-supplied payload — the gateway
does re-resolve and re-authorize it (`call_tool.py:146-165`), so this is defense-in-depth, not a
hole, but B's version is structurally stronger. B also loses `McpDispatcherUnwrap`
(`mcp/dispatcher.py`, 114 lines) and the payload archaeology in `runtime_worker/stream_tools.py:989`
and `runtime_api/schemas/events.py:827-829` — the tool name _is_ the tool.

**Where A is better, and this is not currently discussed anywhere.** The per-tool lane **bypasses the
operation gateway entirely**. On A every model-initiated MCP call mints an `operation_id`, persists
canonical arguments to the argument store, and returns an `OperationOutcome` through
`services.gateway.invoke` (`call_tool.py:185-287`). On B none of that happens — I read all five
middleware stages and none imports `capabilities/operations/*`; `McpObserveMiddleware`'s own docstring
says it "emits no event and writes no row" (`observe_tool.py:1-27`). `ToolInvocationRecord` and the
citation ledger survive (asserted in the per-tool e2e T3, `test_mcp_per_tool_gate_e2e.py:732-739`),
but the durable operation ledger does not. Per `CLAUDE.md`'s compliance rules — _"for every sensitive
workflow: what changed, where it is logged"_ — flipping the flag silently removes MCP writes from the
operation plane while leaving browser, workspace, row-set and draft-send writes on it. **That is a
regulated-buyer-visible divergence and it is not called out in P2-8, the PRD, or the deletions plan.**

**A defect I found in B's approval wiring while reading (not covered by any test).**
`McpPerToolInterrupts.build` emits the descriptor-driven `interrupt_on` map **only when `gate is
None`** (`per_tool_registration.py:236-244`), and its docstring justifies that as: _"a non-OAuth
connector has `gate=None` and its writes are refused outright today … With the map, they park and can
be approved."_ That outcome does not follow from the composed code:

1. `gate is None` ⇒ the map lists every GATE-eligible tool ⇒ LangChain's HITL middleware interrupts
   before the tool runs.
2. On `approve` the tool call proceeds — `langchain/agents/middleware/human_in_the_loop.py:308`.
3. The tool then runs `PolicyGatedMcpTool._arun` → PDP returns GATE → `if self.gate is None: return
self._refusal(..., APPROVAL_UNAVAILABLE)` (`policy_tool.py:395-399`, and asserted directly by
   `test_gate_none_fails_closed_on_a_write`).

So the user is shown an approval card, approves, and receives _"This action needs your approval, which
is not available for this run."_ That is worse than today's honest refusal. **Confidence: high on
each half (both are read or tested directly), medium on the composition — I did not drive the
`gate=None` + `interrupt_on` combination end to end, and no test does: `McpPerToolInterrupts` appears
in the test tree only at `test_mcp_per_tool_flip.py:63,692`, which exercises `eligible()` alone.**
If B is chosen, this is a P0 to resolve before the flag flips — most likely by building
`ToolAccessGate` even without an OAuth provider, which the factory's own KNOWN LIMITATION comment
(`factory.py:1658-1664`) already prescribes.

Net: **A is safer today** (audit plane intact, one approval seam, live-verified), **B is
architecturally cleaner on binding** and would be at parity after two bounded fixes.

### Axis 4 — Provider portability · **A wins**

Both options' _primary_ mechanism is provider-agnostic: A uses `ls`/`grep`/`read_file`, B uses
ordinary bound tools. The asymmetry is in what makes each **affordable**:

- A's discovery mechanism is the same on all six providers (PRD §1.3, non-negotiable).
- B is affordable unaided only for small connectors. For a 52-tool server it needs `defer_loading`,
  which is Anthropic-only, and `TOOL-SEARCH-PLAN.md:14-22` reaches this conclusion independently and
  more sharply: _"`defer_loading` is the mechanism that makes end-state B affordable — on Anthropic
  only."_
- Secondary: several providers historically cap tool counts and degrade selection accuracy as the
  count grows. I did not verify current per-provider caps against the pinned SDKs — **unverified**.

### Axis 5 — Granularity · **B wins, and this is A's real cost**

Every runtime control keys on the **model tool name**:

- `ToolUsePolicyEnforcer` writes `interrupt_on[tool_name]`
  (`capabilities/tools/tool_use_enforcement.py:204-208`).
- `RuntimeToolControl` admission / budget / result bounding reads `tool_name` off the tool call
  (`capabilities/middleware/runtime_tool_control.py:756, 801-940`).

Under A every connector call is named `call_mcp_tool`, so all 52 Linear tools share **one** budget
bucket and **one** tool-use policy cell. "Require approval for `delete_issue` but not `list_issues`"
is expressible under B and not under A — except through the PDP, which does cover the
action×trust axis (`policy/service.py:253-315`) but not per-tool admin policy or per-tool budgets.

This is a genuine, permanent limitation of A, and the honest mitigation is partial: budgets and
policy can be re-keyed on the unwrapped `(server, tool)` pair, which `McpDispatcherUnwrap` already
computes (`dispatcher.py:26+`) — but that is new work on A's side too, and it re-introduces exactly
the payload archaeology B deletes.

### Axis 6 — Code retired vs. retained · **A wins by a wide margin**

See §6 for the full accounting. In one line: A deletes ~3.2k production LOC and ~4.2k test LOC and
requires no new follow-on items; B deletes ~0.6k production LOC and ~1.9k test LOC and _requires_
item 6 (~150–250 LOC Anthropic adapter), item 2 (the backend mint endpoint, so a credential plane
exists at all), a new tool-filter for context cost, and the two axis-3 fixes.

---

## 5. Scorecard

Weights reflect the program's own stated purpose: the failure being fixed is a context-budget failure
(PRD §0, §1.1), on a non-Anthropic default model.

| Axis                      | Weight | A — catalog + gateway                                                        | B — catalog + per-tool                                                                                | C — hybrid                                                                     |
| ------------------------- | ------ | ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| 1 Context cost            | ×3     | **Strong** — O(1) in tools                                                   | Weak — O(total tools); Anthropic-only mitigation                                                      | Medium — only if the trusted set stays genuinely small                         |
| 2 Argument accuracy       | ×2     | Weak, partially recoverable via catalog-schema validation                    | **Strong** — schema reaches the provider                                                              | Strong for the registered set, weak for the tail                               |
| 3 Policy correctness      | ×3     | **Strong today** — live-verified, operation ledger intact, one approval seam | Medium — better binding, but bypasses the operation ledger + the `gate=None` approve-then-refuse hole | Weak — two decision paths, two approval seams, two provenance paths            |
| 4 Provider portability    | ×2     | **Strong** — identical on all six                                            | Weak — affordability is Anthropic-gated                                                               | Weak — inherits B's problem for the registered set                             |
| 5 Granularity             | ×2     | Weak — one name, one budget, one policy cell                                 | **Strong** — per-tool everything                                                                      | Strong for the registered set only                                             |
| 6 Code retired / retained | ×2     | **Strong** — retires ~3.2k prod + ~4.2k test; no new required items          | Weak — retires ~0.6k prod; adds 4 required items                                                      | **Worst** — retires nothing, adds a selection policy, keeps both lanes forever |

**A ≫ B > C.** C is not a compromise; it is the union of both options' costs. It doubles the dispatch
surface, the approval surface, and the stream-provenance surface permanently, and it needs new
contract surface before it can even start (§3). `TOOL-SEARCH-PLAN.md:237-239` reaches the same verdict
about the PRD's original framing of C: _"You cannot defer a gateway you always need … C collapses into
either A or B."_ **Reject C.**

---

## 6. What each option retires — the accounting

All counts are `wc -l` at `833e7d25`.

### Option A retires the per-tool lane

**Production — retired outright (no non-per-tool consumer):**

| File                                              |       LOC |
| ------------------------------------------------- | --------: |
| `capabilities/mcp/tool_source.py`                 |       739 |
| `capabilities/mcp/middleware/policy_tool.py`      |       546 |
| `capabilities/mcp/middleware/error_map_tool.py`   |       496 |
| `capabilities/mcp/per_tool_registration.py`       |       432 |
| `capabilities/mcp/middleware/exec_policy_tool.py` |       252 |
| `capabilities/mcp/middleware/compose.py`          |       219 |
| `capabilities/mcp/middleware/observe_tool.py`     |       201 |
| `capabilities/mcp/middleware/citations_tool.py`   |       156 |
| `capabilities/mcp/connection.py`                  |       117 |
| `capabilities/mcp/connector_resolver.py`          |        76 |
| **Subtotal**                                      | **3,234** |

Plus the seams that lose their only producer: `RuntimeDependencies.mcp_per_tool_collaborators` /
`.mcp_connector_resolver_sink` (`execution/contracts.py:875`), `factory.py:562-636`, and the
published-map fallback in `runtime_worker/stream_tools.py:136-168, 980-992` — on the order of another
100–150 lines of seam.

**Production — conditionally retired with it (the credential plane, whose only consumer is the
registrar):** `credentials/desktop.py` 531 + `credentials/refreshing_auth.py` 296 +
`credentials/__init__.py` 39 = **866**. `DELETIONS-PLAN.md:279` already marks `desktop.py` DELETE
under Retirement 2 independently. `refreshing_auth.py` is marked KEEP there _because PRD §2's mint
provider will drive it_ — but if A is chosen, direct-connect has no consumer, so **item 2's mint
endpoint loses its purpose too**, and that retention argument evaporates. That is a consequence worth
deciding deliberately, not by omission (§8).

**Tests retired:** `test_tool_source.py` 909 + `test_exec_error_citations.py` 947 +
`test_mcp_per_tool_flip.py` 891 + `test_mcp_per_tool_gate_e2e.py` 780 + `test_policy_tool.py` 684 =
**4,211**; plus `test_desktop_provider.py` 607 + `test_refreshing_auth.py` 396 +
`test_connection_contracts.py` 130 = **5,344** if the credential plane goes.

**Retained on A:** the whole P1a/P1b keystone — `policy/service.py` 322, `policy/contracts.py` 434,
`mcp/descriptor_source.py` 246, `mcp/annotations.py` 129 — plus the gateway lane and
`surfaces_v2/gate.py`. **None of the policy work is wasted.** That matters: the expensive, novel part
of P0–P1b survives either way; what A retires is the _plumbing_ built to deliver it per-tool.

**Total for A: ~3,230–4,100 production LOC and ~4,210–5,340 test LOC.**

### Option B retires the gateway lane

| File / symbol                              |     LOC | Note                                                                                                                                                                                                                                     |
| ------------------------------------------ | ------: | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `capabilities/mcp/middleware/call_tool.py` |     458 | `CallMcpTool` + `CallMcpToolInputParser`                                                                                                                                                                                                 |
| `capabilities/mcp/dispatcher.py`           |     114 | `McpDispatcherUnwrap`; also deletes its two consumers' unwrap branches                                                                                                                                                                   |
| `capabilities/mcp/operation_adapter.py`    |   (573) | **partial** — `DELETIONS-PLAN.md:64` already marks `build_proposal` DELETE and the file KEEP because `execute_read` is the live MCP read path. Under B nothing calls it, but the file's fate is entangled with the browser/row-set lanes |
| **Subtotal (clean)**                       | **572** |                                                                                                                                                                                                                                          |

**Tests retired:** `test_operation_gateway_adapter.py` 725 + `test_call_tool_gate.py` 269 +
`test_call_tool_surface.py` 234 + `test_call_tool_protocol_error.py` 164 + `test_dispatcher.py` 158 +
`test_mcp_write_gate_e2e.py` 374 = **1,924**.

**Required additional work B pulls in** (not optional — B is not affordable or correct without them):

| Item                                                                                   | Cost                                                     |
| -------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| PRD item 6 — `defer_loading` adapter                                                   | ~150–250 LOC + tests (`TOOL-SEARCH-PLAN.md:265-269`)     |
| PRD item 2 — backend scoped-token mint endpoint                                        | cross-service; B has **no** credential plane without it  |
| A per-tool context filter (does not exist — §3)                                        | new contract surface on `McpServerCard` + selection rule |
| Axis-3 fix: `ToolAccessGate` without OAuth                                             | prescribed at `factory.py:1658-1664`                     |
| Axis-3 fix: restore MCP operations to the operation ledger, or document the divergence | unscoped                                                 |

**Total for B: ~572 production LOC and ~1,924 test LOC retired, against five required additions.**

### Option C retires nothing

Both lanes are retained by definition, plus a new selection policy. It is the only option with a
negative retirement balance.

---

## 7. Recommendation, and what would change my mind

### Recommendation

**Adopt Option A.** Ship item 1 (the catalog) against the current default, then retire the per-tool
lane on the accounting in §6. Concretely:

1. **Ship item 1** with the gateway as the dispatch surface. This is unchanged from PRD §7's
   sequencing and is not controversial.
2. **Add catalog-schema argument validation to the gateway** (§4 axis 2). Validate
   `McpToolCallRequest.arguments` against the stored `/mcp/<server>/tools/<tool>.json` schema inside
   `CallMcpToolInputParser` before dispatch, returning a typed `McpToolCallResult.fail` naming the
   offending field. This converts A's weakest axis into a strength B does not have, and it costs one
   contract in one place.
3. **Re-key tool budgets and tool-use policy on `(server, tool)`** via `McpDispatcherUnwrap` (§4 axis
   5), so A's granularity gap is closed rather than accepted.
4. **Correct the stale STATUS docstring** in `test_mcp_per_tool_gate_e2e.py` immediately, whatever is
   decided — it currently misinforms every future reader (§1.2).
5. **Then measure** (§7 triggers). Retire the per-tool lane only after the measurement clears it;
   until then the flag stays OFF and the code stays, which is exactly what P2-8 designed for.

Consequences to accept openly: PRD **item 6 closes** as "mechanism does not fit the end-state"
(`TOOL-SEARCH-PLAN.md:281-284` pre-agrees), PRD **item 7 becomes moot** (and is already largely
solved — §1.1), and PRD **item 2's mint endpoint loses its only ai-backend consumer** and must be
re-justified on its own merits or deferred (§8).

### What would change my mind — three falsifiable triggers

**T1 — Discovery cost measured on the live 52-tool Linear server.** Instrument the item-1 catalog
path on the real desktop journey (`tools/desktop-journeys/filesystem-access/jF_linear_mcp.py`) and
record: turns-to-first-correct-tool-call, total prompt tokens across the run, wall-clock to first
connector result, and argument-error rate. Compare against the same errand with
`MCP_PER_TOOL_ENABLED=true`. **If A's total prompt tokens across a realistic multi-turn run exceed
B's** — which is possible, because A pays 2–3 extra full-context round-trips to save 17k resident
tokens — **the context-cost axis inverts and the recommendation flips to B.** This is the single most
important measurement in the program and it is cheap: the harness already exists.

**T2 — Argument-error rate.** If A's malformed-argument rate on real Linear tools exceeds B's by a
margin that survives fix (2) above, B wins axis 2 outright and the ×2 weight starts to matter.

**T3 — The default model becomes Anthropic.** `defer_loading` makes B's context cost approximately
A's while keeping B's accuracy and granularity wins. If the product's default model moves to Claude
and stays there, B + item 6 becomes the better end-state and A's main advantage evaporates. Today the
default is GPT-5.4 mini (PRD §1.3), so this is hypothetical — but it is a product decision, not a
technical one, and it could be made independently of this document.

A fourth, non-measurement trigger: **a product requirement for per-tool admin policy** ("approve every
`delete_*` on Linear, auto-run every `list_*`") that cannot be met by re-keying on `(server, tool)`.
I believe it can be met that way, but I have not designed that change.

---

## 8. Loose ends this decision creates

1. **PRD item 2 (backend scoped-token mint) loses its consumer under A.** The gateway proxies through
   `services/backend` and never sees a credential (`backend_provider.py:1`). Item 2 was scoped to feed
   the per-tool `CredentialProvider`. Under A it should either be re-justified for another consumer or
   deferred — and `DELETIONS-PLAN.md` §2.3's ordering constraint ("retire the broker route strictly
   after the mint endpoint is live-validated") needs revisiting, because under A there may be no mint
   endpoint and the broker route can be retired on its own evidence (`DELETIONS-PLAN.md:260-270`
   already shows both halves are unreached).
2. **The operation-ledger divergence (§4 axis 3) is undocumented.** Whichever option wins, the fact
   that per-tool dispatch bypasses `services.gateway.invoke` should be written down. If B ever ships
   without addressing it, MCP writes silently leave the durable operation plane.
3. **F3 capability-bridge tools are dropped when per-tool is on** (`factory.py:746-748`,
   `:858-870`). Today that is harmless — the bridge is empty in the current production posture
   (`factory.py:847-856`) — but it is a real behavioural difference between the two lanes.
4. **`McpServerConfigFile.allowed_tools` is dead data.** It is declared (`files.py:224`) and dropped
   by `to_card()` (`:238-253`). Either wire it or delete it; today it silently promises filtering
   that does not happen.

---

## 9. What I did not verify

Stated plainly, because the recommendation should not be read as stronger than its evidence.

- **No measurement.** Every token figure here is arithmetic on the PRD's measured 70,465 bytes, not a
  measurement of mine. I made no live model call and ran no desktop journey. T1 is unmeasured and is
  the trigger most likely to fire.
- **The live-verification claim for A** ("a real Linear run on the packaged desktop showed
  `approval_seen: false`") I did not reproduce. I confirmed only that the harness emits that field
  (`tools/desktop-journeys/filesystem-access/jF_linear_mcp.py:945,956`) and that it is derived from
  `evidence["cards_seen"]`.
- **The `gate=None` approve-then-refuse hole** (§4 axis 3) is an inference from two directly-verified
  halves, not an end-to-end reproduction. No test covers the combination.
- **Per-provider tool-count caps and selection degradation** (§4 axis 4) — asserted from general
  knowledge, not checked against the pinned SDKs.
- **Tests were run with the main checkout's venv on `PYTHONPATH`**, because this worktree has no
  `.venv`. Sibling agents were concurrently editing `capabilities/citation_capturing_tool.py`,
  `capabilities/retrying_tool.py` and `runtime_worker/dependencies.py` during the run; the suites I
  ran do not import the first two, but the run is not a clean-tree run.
- **`operation_adapter.py`'s fate under B** I costed as "partial/entangled" rather than resolving it;
  `DELETIONS-PLAN.md:64` treats the file as KEEP for reasons that are about the read path, and I did
  not trace every consumer.

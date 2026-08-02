# Anthropic Tool Search (`defer_loading`) — implementation plan

**Status:** PLAN for decision · **Parent:** [`PRD.md`](./PRD.md) §6 (item 6, P4)
**Base:** `84a67dc7` · **Gate:** strictly after item 1 (MCP filesystem catalog) ships and is live-verified
**Owner:** ai-backend · **Blast radius:** `agent_runtime/execution/` (tool composition + a new provider adapter), `agent_runtime/capabilities/mcp/`

---

## 0. TL;DR for the decision-maker

I verified the capability end-to-end in this repo's installed stack, including by
constructing real request payloads. It is present, it works, and the opt-in is two lines.

**But the honest finding is that PRD §6 mis-frames the item.** Tool Search is not "an accelerant
layered on top of the filesystem catalog." In the end-state the catalog implies — one
`call_mcp_tool` gateway, discovery by `ls`/`grep`/`read_file` — there are **no per-tool definitions
in the prompt to defer**, so `defer_loading` saves approximately nothing. Tool Search only pays
when the runtime registers one model tool per real MCP tool (`MCP_PER_TOOL_ENABLED=true`,
end-state **B**), because that is the only configuration where 52 tool schemas are resident context.

So the correct framing is: **`defer_loading` is the mechanism that makes end-state B affordable —
on Anthropic only.** Recommendation in §5.

---

## 1. What I verified (not what the vendor claims)

All of the following was read or executed against this repo's own service venv,
`services/ai-backend/.venv` — `langchain-anthropic==1.4.8`, `anthropic==0.117.0`
(`services/ai-backend/requirements.txt:7,41`).

### 1.1 The capability exists in the pinned stack

| Claim                                                              | Evidence                                                                                                                                 |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `defer_loading` is a first-class field on a custom tool definition | `langchain_anthropic/chat_models.py:118` — `defer_loading: NotRequired[bool]` on the `AnthropicTool` TypedDict                           |
| Two tool-search built-ins exist, each mapped to a beta header      | `chat_models.py:146-147` — `"tool_search_tool_regex_20251119"` and `"tool_search_tool_bm25_20251119"` → `"advanced-tool-use-2025-11-20"` |
| `defer_loading` survives conversion from a LangChain tool          | `chat_models.py:169` — `defer_loading` is in `_ANTHROPIC_EXTRA_FIELDS`; copied at `chat_models.py:2265-2274`                             |
| The built-in dict is passed through unconverted                    | `chat_models.py:153-162` — `_BUILTIN_TOOL_PREFIXES` includes `"tool_search_"`; `_is_builtin_tool` prefix-matches on `type`               |
| The beta routes to the beta client                                 | `chat_models.py:1428-1435` — `if "betas" in payload: self._client.beta.messages.create(...)`                                             |

The Anthropic SDK models the field on the plain (non-beta) `ToolParam` as well —
`anthropic/types/tool_param.py`: `defer_loading: bool`, documented as _"If true, tool will not be
included in initial system prompt. Only loaded when returned via tool_reference from tool search."_
The two search-tool param types are `anthropic/types/tool_search_tool_regex_20251119_param.py` and
`…_bm25_20251119_param.py`; both require `name` as a `Literal` (`"tool_search_tool_regex"` /
`"tool_search_tool_bm25"`) and both accept `type` as **either** the dated or the undated form —
which is the source of trap §3.1.

### 1.2 The opt-in mechanism, proven by payload construction

I built real request payloads (no network call — a dummy key, `_get_request_payload`).
Two supported opt-in paths, both confirmed to put `defer_loading` on the wire:

**Path A — LangChain tool via `extras`** (this is what `langchain_core` itself documents:
`langchain_core/tools/base.py:549-563` shows `@tool(extras={"defer_loading": True, ...})`):

```python
@tool(extras={"defer_loading": True})
def linear_list_issues(team: str) -> str: ...
```

**Path B — raw Anthropic-format dict** passed straight to `bind_tools`
(`{"name", "description", "input_schema"}` triggers the passthrough branch at `chat_models.py:2250-2253`,
which copies the dict verbatim, `defer_loading` included).

Observed payloads:

```
CASE 1 — defer_loading alone, no search tool
  tools = [{'name': 'linear_list_issues', 'input_schema': {...},
            'description': '...', 'defer_loading': True}]
  betas = None                          # <-- NO beta header

CASE 2 — defer_loading + the tool-search built-in
  tools = [{'type': 'tool_search_tool_regex_20251119', 'name': 'tool_search_tool_regex'},
           {'name': 'linear_list_issues', 'input_schema': {...},
            'description': '...', 'defer_loading': True}]
  betas = ['advanced-tool-use-2025-11-20']    # <-- auto-appended
```

**So the opt-in is exactly this, and there is no third mechanism:**

1. Mark each deferrable tool — `BaseTool.extras["defer_loading"] = True`, or `defer_loading: True`
   on a raw Anthropic-format dict.
2. Add the search built-in to the same `tools` list as a raw dict with the **dated** `type`.
3. The beta header is then auto-appended by `_get_request_payload` (`chat_models.py:1377-1392`) and
   the request is routed to `client.beta.messages.create`. Nothing else is needed.

`ChatAnthropic(betas=[...])` and call-time `betas=` kwargs also exist (`chat_models.py:977-988`) but
are **not required** — and should not be used here, because hand-setting the beta while forgetting
the search tool produces deferred tools that nothing can ever un-defer.

### 1.3 The bound tool is a normal tool — this is the load-bearing security fact

The search result carries **references, not invocations**:
`anthropic/types/beta/beta_tool_search_tool_search_result_block.py` —
`tool_references: List[BetaToolReferenceBlock]`, wrapped by
`beta_tool_search_tool_result_block.py` (`type: Literal["tool_search_tool_result"]`).

Consequence: `defer_loading` changes **only the wire representation of the definition**. It does not
change the object, the name, the schema, or the execution path. The tool the model eventually calls
is the identical `BaseTool` instance, so it still flows through `ToolNode` and therefore still
through the composed MCP middleware stack (POLICY → EXEC_POLICY → OBSERVE → ERROR_MAP → CITATIONS,
`capabilities/mcp/middleware/compose.py`). **PRD §6 AC4 is satisfied structurally, not by an added
check** — which is the strongest possible form of that acceptance criterion, and worth asserting as
a test rather than assuming.

### 1.4 The host accepts a raw dict tool

`langchain/agents/factory.py:1052-1053` splits the tool list:

```python
built_in_tools = [t for t in tools if isinstance(t, dict)]
regular_tools  = [t for t in tools if not isinstance(t, dict)]
```

Dicts are excluded from `ToolNode` and appended to `default_tools` (`factory.py:1070-1071`), and
skipped by the unknown-tool validation (`factory.py:1307-1309`). So injecting the search built-in
into `DeepAgentBuildRequest.tools` is structurally supported by the pinned `langchain` — no fork, no
subclass. It is server-side executed by Anthropic and never reaches our `ToolNode`.

### 1.5 What I did NOT verify

Stated plainly, because the plan's economics rest on it:

- **No live API call was made.** No token counts, latency, or accuracy numbers in this document are
  measured by me. The 77,000 → 8,700 token (85%) and Opus 4.5 79.5% → 88.1% figures are
  **vendor-published and unverified here**.
- **Which Claude models support the beta.** The pinned code has no model allowlist for
  `advanced-tool-use-2025-11-20`; `_TOOL_TYPE_TO_BETA` is model-agnostic. Whether a given
  `claude-*` id 400s is not determinable from the repo. §6.3 treats this as a fail-open-to-the-catalog
  runtime concern, not a static list.
- **Whether `defer_loading` withholds the whole definition or only `input_schema`.** The SDK
  docstring says the tool "will not be included in the initial system prompt", which reads as the
  whole definition. I did not confirm on the wire. The token math in §4 assumes whole-definition
  withholding; if only the schema is withheld the saving is smaller and the case weakens further.
- **BM25 vs regex retrieval quality** on a 52-tool Linear server. Unmeasured, and it is the variable
  that decides whether accuracy goes up or down for _our_ tool names.

---

## 2. Why this cannot be the primary mechanism

Unchanged from PRD §1.3 and §6, and worth restating because it constrains every design choice below.

`ModelConfigResolver.PROVIDER_ALIASES` (`execution/models.py:54-72`) admits six canonical providers:
`anthropic`, `gemini`, `openai`, `openrouter`, `ollama`, and a custom OpenAI-compatible endpoint.
The live desktop e2e that produced the EMPTY SUCCESS ran on **GPT-5.4 mini**. A capability that
exists on one of six providers cannot be the answer to a context-budget failure the other five share.

The filesystem catalog (item 1) is built from `ls` / `grep` / `read_file` — primitives every model
has. That stays the primary path. Everything below is an adapter over it.

---

## 3. Traps found while verifying

### 3.1 The undated `type` alias silently disables the beta

The Anthropic SDK accepts **both** forms:
`type: Required[Literal["tool_search_tool_regex_20251119", "tool_search_tool_regex"]]`.
LangChain's `_TOOL_TYPE_TO_BETA` (`chat_models.py:145-148`) is an **exact-key dict** holding only the
dated form, while `_is_builtin_tool` uses a **prefix match**. So the undated alias passes through as
a built-in but appends no beta. Confirmed:

```
type = 'tool_search_tool_regex'          -> betas = None   (tool still passed through)
type = 'tool_search_tool_regex_20251119' -> betas = ['advanced-tool-use-2025-11-20']
```

The failure is silent at construction and surfaces as an opaque provider 400 — or worse, as tools
that are deferred with no way to retrieve them. **Mitigation:** the dated type string is a constant on
the adapter's `Keys` class, never a literal at a call site, and a unit test asserts the constructed
payload's `betas` list is non-empty (§9, T3). Do not assert the string is "in the tools list" —
assert the _beta header materialized_, because that is the thing that actually breaks.

### 3.2 A Claude model on OpenRouter is not the `anthropic` provider

`ModelConfigResolver._infer_provider` (`execution/models.py:253-257`) maps a `vendor/model` slug —
e.g. `anthropic/claude-opus-4-5` — to **`openrouter`**, and `openai_compat.py:1-28` routes OpenRouter
through `langchain_openai.ChatOpenAI` against `/chat/completions`. A `defer_loading` key sent there
is at best ignored and at worst a 400.

**Therefore the gate must key on the canonical provider slug being exactly `"anthropic"`, never on
the model name containing `claude`.** This is the single most likely way to get this feature wrong.

### 3.3 `tool_schema_revision` is bound into prompt-cache identity

`factory.py:368` computes `tool_schema_revision=_model_tool_schema_revision(model_tools)`, and
`factory.py:1533-1541` records that the digest _"is bound into prompt-cache identity"_. Deferring
tools changes the set of definitions actually sent. If the digest is computed over the composed list
while the wire carries a different set, the cache key claims two materially different prompts are the
same. **The digest must be computed over the post-gate list, and `defer_loading` must participate in
it** (see §9, T5).

### 3.4 A new stream content block appears

`tool_search_tool_result` blocks will flow through the worker's stream path. The house pattern for
frames that are noise to the client is `StreamMessageProcessor.internal_tool_names`
(`runtime_worker/stream_tools.py:75`), per `services/ai-backend/CLAUDE.md` §Streaming model. The
search tool's frames should be classified there rather than surfacing a mystery card in the cockpit.
Whether the run should instead emit a typed discovery event is an open question (§10).

### 3.5 The Context Occupancy Ledger will over-report

`ModelToolDeclaration.declared(...)` is stamped at composition time (`factory.py:672-695`) and the
ledger reports resident context per tool. A deferred tool's schema is **not** resident. Left alone,
the ledger reports the pre-deferral footprint and the entire measured benefit of this feature becomes
invisible in our own observability — which would make PRD §6 AC5 unfalsifiable.

---

## 4. The economics, honestly

### 4.1 What the resident cost actually is today

From PRD §1.1, the live Linear descriptor is **70,465 bytes across 52 tools**. At roughly 4 chars per
token for dense JSON that is on the order of **~17k tokens** if every schema were resident.
(Arithmetic on the PRD's measured byte count, not a measurement of mine.)

But **today they are not resident.** `MCP_PER_TOOL_ENABLED` defaults **off**
(`capabilities/mcp/per_tool_registration.py:107-118`, `_DEFAULT_WHEN_UNSET = "false"`), so the live
path registers a single `call_mcp_tool` gateway (`factory.py:756-770`). The model's tool list is the
Deep Agents built-ins plus a handful of runtime tools — on the order of 15–20 definitions.

### 4.2 Which end-state each option pays off in

Mapping onto PRD §7's three candidate end-states:

| End-state                           | Registration        | What `defer_loading` would defer                                                                       | Saving                             |
| ----------------------------------- | ------------------- | ------------------------------------------------------------------------------------------------------ | ---------------------------------- |
| **A — catalog + gateway**           | one `call_mcp_tool` | nothing worth deferring; `read_file`/`grep`/`ls` are needed on turn 1 and the gateway is the only door | **≈ 0**                            |
| **B — catalog + per-tool**          | 52 tools            | 52 MCP schemas, ~17k tokens                                                                            | **large — this is the whole case** |
| **C — "catalog + gateway + defer"** | gateway             | same as A                                                                                              | **≈ 0 — the row is incoherent**    |

PRD §7's end-state **C** does not survive contact with the mechanism. You cannot defer a gateway you
always need, and the 52 tools it fronts were never definitions in the first place. C collapses into
either A (no defer) or B (defer is the point).

### 4.3 The reframing

The reason per-tool registration is behind a default-off flag is, in large part, that 52 resident
schemas is an unaffordable prompt. `defer_loading` removes exactly that cost. So:

> **Tool Search is not an accelerant layered on the catalog. It is the mechanism that makes per-tool
> registration affordable — on Anthropic only. It competes with the catalog's discovery step, and
> composes only with the catalog's _artifacts_.**

That is a better feature than the PRD describes, and a narrower one. On Anthropic it replaces a
3-round-trip agent-loop discovery (`ls` → `grep` → `read_file`, each re-sending the conversation)
with one server-side hop. That latency argument is more defensible than the token argument, because
it does not depend on the vendor's benchmark transferring to our tool names.

### 4.4 Verdict — does the win justify the provider-specific branch?

**Not yet, and not on the current end-state. Conditionally yes on end-state B.**

Reasons, in order of weight:

1. **On today's live configuration (gateway, flag off) the branch buys ≈ 0 tokens.** Building it now
   would add an Anthropic-only path to the tool pipeline in exchange for nothing measurable. PRD §6's
   own sequencing rule — never let the provider-specific path become the reference implementation —
   argues the same way.
2. **The branch is genuinely cheap when it is worth anything.** Because `defer_loading` is a field on
   an object we already construct, and the search tool is a dict the host already routes, the whole
   feature is an adapter — my estimate is **~150–250 LOC of production code** plus tests, with **zero**
   change to dispatch, policy, or the middleware stack. It is not a pipeline fork. That is the single
   strongest argument for doing it _if_ B is chosen.
3. **The risk is not size, it is a second binding path.** Two ways for a tool definition to reach a
   model is exactly the class of divergence that produced the `RetryingTool` `response_format` bug in
   PRD §8.1. The mitigation is AC2 taken literally: one derivation, asserted byte-equal.
4. **The accuracy claim may not transfer.** Vendor accuracy went _up_ on their benchmark. Our tool
   names are connector-generated (`list_issues`, `create_issue`, …) and highly self-similar; BM25 or
   regex over 52 near-synonyms is a plausible _regression_ in retrieval. Unmeasured. This is a
   reason to gate on measurement, not to skip the feature.
5. **It is one provider out of six, and the default is not it.** Any effort here helps a minority of
   runs until the default model changes.

**Recommendation:** keep the item at **P4**, keep it gated behind item 1, and add one gate the PRD
does not have — **build it only if the item-1 measurement picks end-state B.** If the measurement
picks A, close item 6 as "not applicable, mechanism does not fit the end-state" rather than leaving
it open as perpetual P4. If it picks B, build it immediately, because B without deferral is the
17k-token prompt everyone was avoiding.

---

## 5. Sequencing

```
item 1 (catalog) ships + live-verified
        │
        └─▶ MEASURE discovery quality on the live 52-tool Linear server
                 │
                 ├─ picks A (gateway) ──▶ CLOSE item 6. Mechanism does not fit. No branch.
                 │
                 └─ picks B (per-tool) ──▶ BUILD this plan. Deferral is what makes B affordable.
                                              │
                                              └─▶ item 7 (approval-id uniqueness) becomes REQUIRED
```

Item 6 and item 7 have the same trigger. That is not a coincidence: both exist only in end-state B.
Cost them together when the measurement lands.

---

## 6. Design — the provider gate

### 6.1 Where the provider is known

`_assemble_harness` (`execution/factory.py:238+`) has both the composed tool list and
`runtime_context.model_profile` — a `ModelConfig` carrying the canonical `provider` slug produced by
`ModelConfigResolver` (`execution/models.py:101-105, 213-234`). The composition point is
`_model_visible_tools` (`factory.py:672-780`), and the result is handed to
`DeepAgentBuildRequest(tools=model_tools, model_config=runtime_context.model_profile, ...)`
(`factory.py:470-473`). **One seam, both facts present.** No plumbing is required.

### 6.2 Follow the existing provider-adapter precedent

The repo already has the right shape for this, and it is not an inline `if`:
`CitationStreamPipeline.for_provider` → `_AdapterRegistry.build` (`execution/providers/citation_pipeline.py:91-119`)
maps the canonical slug to an adapter with **lazy imports**, and returns a **no-op adapter** for
unknown providers so the pipeline is safe to install unconditionally. `provider_kwargs.py:59-73`
uses the same slug-keyed-table discipline for training opt-out.

Mirror it exactly. Also satisfies `services/ai-backend/CLAUDE.md` — _"Keep production helper behavior
inside classes… Avoid module-level helper functions."_

Proposed module: **`services/ai-backend/src/agent_runtime/execution/providers/tool_search.py`**

```
ToolSearchPolicy          — decides ENABLED / DISABLED for a run; the only place the
                            provider slug is compared. Returns a typed decision, not a bool,
                            so the reason is available to observability.
NoopToolSearchAdapter     — returns the tool list unchanged. Every non-Anthropic provider.
AnthropicToolSearchAdapter— marks eligible tools deferred and appends the search built-in.
ToolSearchAdapterRegistry — slug → adapter, lazy import, no-op default.
```

The adapter's single public method takes the composed tool tuple and returns a new tuple. It **must
not** be able to add, drop, rename, or reschema a tool — only to (a) set `extras["defer_loading"]`
and (b) append the one built-in dict. §9 T1 asserts that invariant.

### 6.3 The gate conditions, in order

All must hold, and every one of them fails **closed to the item-1 catalog path**:

1. `ModelConfigResolver.canonical_provider(model_profile.provider) == "anthropic"` — the slug, never
   the model name (trap §3.2).
2. `FakeModelProvider.is_enabled()` is **False** (`execution/models.py:114-116` uses the same guard) —
   the hermetic fake model must never take a provider branch.
3. The feature flag is on. Follow `McpPerToolFlag` verbatim (`capabilities/mcp/per_tool_registration.py:96-125`):
   a self-contained env reader, `ClassVar` env-var name, injectable `environ` for tests, default
   **off**. Proposed `ANTHROPIC_TOOL_SEARCH_ENABLED`.
4. `McpPerToolFlag.enabled()` is **True**. There is nothing to defer otherwise (§4.2). Encoding this
   in the gate rather than in a comment is what stops the feature from being switched on into a
   configuration where it does nothing.
5. Enough deferrable tools to be worth it — a floor (proposed 10) so a 2-tool server does not pay a
   search round-trip to save 2 KB.

Model-family support (§1.5) is deliberately **not** a static allowlist. If the provider 400s on the
beta, the typed failure adapter already classifies it
(`execution/providers/model_failure_adapters.py:140-153`, `bad_request`); the run should surface it
as a configuration error and the operator turns the flag off. A hardcoded model list would rot
exactly like the seven divergent model lists this repo already killed.

### 6.4 Which tools are eligible

**Only MCP per-tool registrations.** Never a Deep Agents built-in, never a runtime tool.

`HarnessBuiltinToolNames.NAMES` (`per_tool_registration.py:128-152`) already enumerates the framework
names (`write_todos`, `task`, `execute`, `ls`, `read_file`, `write_file`, `edit_file`, `glob`,
`grep`). Deferring any of those would strand the model: the catalog path _needs_ `ls`/`grep`/`read_file`
on turn 1, and deferring `task` would break delegation. The eligible set is exactly
`McpPerToolRegistration.tools` (`factory.py:749`) — which is also precisely the set that is large.

### 6.5 One derivation — PRD §6 AC2, taken literally

The per-tool JSON artifact already exists and already has the right shape.
`McpToolMetadataFile` (`capabilities/mcp/files.py:159-201`) carries `name`, `description`,
`input_schema` — a 1:1 match for the Anthropic `ToolParam` required fields
(`anthropic/types/tool_param.py`: `input_schema`, `name`, `description`). And it is written to
`mcp/<server>/tools/<tool>.json` (`files.py:1-12`, `Keys.Dir.ROOT = "mcp"`, `Keys.Dir.TOOLS = "tools"`).

So the catalog file the model reads and the deferred definition Anthropic receives are **projections
of the same `McpToolMetadataFile`**. Neither derives from the other; both derive from it. That makes
AC2's byte-equality assertion a real test rather than a tautology (§9, T2).

Do **not** re-serialize the schema for the deferred path. The `BaseTool` that
`McpToolSource` produces already carries the descriptor
(`capabilities/mcp/tool_source.py:119` — `ToolDescriptorPair = tuple[BaseTool, CapabilityDescriptor]`);
the adapter sets a flag on it and nothing else.

---

## 7. Contracts

Pydantic at the boundary, per `services/ai-backend/CLAUDE.md`. Full field shape:

```python
class ToolSearchMode(StrEnum):
    """Which retrieval the provider runs over deferred definitions."""
    REGEX = "regex"
    BM25 = "bm25"


class ToolSearchDisabledReason(StrEnum):
    """Why a run did not take the deferred path. Every value is observable."""
    PROVIDER_NOT_ANTHROPIC = "provider_not_anthropic"
    FLAG_OFF = "flag_off"
    PER_TOOL_REGISTRATION_OFF = "per_tool_registration_off"
    FAKE_MODEL = "fake_model"
    BELOW_TOOL_FLOOR = "below_tool_floor"


class ToolSearchDecision(RuntimeContract):
    """The gate's typed verdict for one run. Never a bare bool."""
    enabled: bool
    mode: ToolSearchMode = ToolSearchMode.REGEX
    reason: ToolSearchDisabledReason | None = None      # set iff enabled is False
    deferred_tool_names: tuple[str, ...] = ()           # empty iff enabled is False
    eligible_tool_count: NonNegativeInt = 0

    @model_validator(mode="after")
    def _reason_iff_disabled(self) -> "ToolSearchDecision": ...
```

`RuntimeContract` is the strict (`extra="forbid"`) base already used by `McpToolMetadataFile`
(`files.py:159`). The `reason`-iff-disabled validator is what stops a silent "enabled but nothing
deferred" state — the shape that would make this feature look on while doing nothing.

Constants live on a nested `Keys` class on the adapter, per the house rule against inline duplication
of repeated keys — including the dated `type` strings (trap §3.1), the `Literal` `name` values, and
the `"defer_loading"` extras key.

---

## 8. Security

- **No policy bypass, structurally.** §1.3: the deferred tool is the same `BaseTool`, executed by the
  same `ToolNode`, wrapped by the same POLICY → EXEC_POLICY → OBSERVE → ERROR_MAP → CITATIONS stack.
  The search result contains `tool_references` only. Assert it (§9 T4) rather than relying on it.
- **The search index is built from untrusted text.** Tool names and descriptions come from the MCP
  server — `services/ai-backend/CLAUDE.md` lists MCP descriptors as untrusted. A hostile server can
  write a description engineered to win the search for an unrelated query (a retrieval-time
  confused-deputy). This is **not new** — the same text is already in the prompt today — but deferral
  makes it _selective_, which is a slightly sharper edge. The PDP decision on the eventual call is
  the control, and it is unchanged.
- **No secrets in deferred definitions.** They project from `McpToolMetadataFile`, which is
  `extra="forbid"` with no credential fields and is scanned by `SecretShapeScanner.assert_clean`
  (`files.py:110-130`) for Fernet/`kms_v1:` ciphertext and credential-shaped keys before write. The
  deferred path inherits that guarantee **only if** it projects from the file contract rather than
  from a live descriptor — one more reason for §6.5.
- **`allowed_callers` is available and unused.** Both search-tool param types and `ToolParam` accept
  `allowed_callers` — a list of `"direct"` / `code_execution_*` literals. Out of scope here; note it
  as the seam if code-execution tool access is ever scoped.

---

## 9. Tests

Unit, hermetic, fakes only — `services/ai-backend/tests/CLAUDE.md`. Fakes and constants in mixins;
concrete classes hold only `test_*`.

| #      | Test                                  | Asserts                                                                                                                                                                                                           |
| ------ | ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **T1** | Adapter is definition-preserving      | Non-Anthropic → returned tuple is identical. Anthropic → same names, same order, same schemas; the only diffs are `extras["defer_loading"]` and one appended dict.                                                |
| **T2** | **PRD §6 AC2 — one derivation**       | The deferred `ToolParam` and the `mcp/<server>/tools/<tool>.json` body are byte-equal on `name`/`description`/`input_schema`, both projected from one `McpToolMetadataFile`.                                      |
| **T3** | **Trap §3.1 — the beta materializes** | Build the payload through `ChatAnthropic._get_request_payload` and assert `betas == ["advanced-tool-use-2025-11-20"]`. Assert the header, not the tool's presence. A sibling test pins the dated `type` constant. |
| **T4** | **PRD §6 AC4 — policy still runs**    | A WRITE tool discovered via the deferred path parks on the PDP GATE identically to the non-deferred path.                                                                                                         |
| **T5** | **Trap §3.3 — cache identity**        | `tool_schema_revision` differs between deferred and non-deferred composition of the same tools.                                                                                                                   |
| **T6** | Gate matrix                           | Every `ToolSearchDisabledReason` reachable and asserted — notably `openrouter` + `anthropic/claude-*` → `PROVIDER_NOT_ANTHROPIC` (trap §3.2), and fake-model → `FAKE_MODEL`.                                      |
| **T7** | **PRD §6 AC3 — off is identical**     | Flag off, and unsupported provider, each produce a tool list byte-identical to the item-1 path.                                                                                                                   |
| **T8** | Built-ins never deferred              | No name in `HarnessBuiltinToolNames.NAMES` is ever marked deferred.                                                                                                                                               |
| **T9** | Typed failure                         | A provider `bad_request` on the beta surfaces as a typed `AgentRuntimeError` with a safe public message — no traceback, no beta string, in model output or HTTP response.                                         |

**Live measurement (PRD §6 AC5)** — not a unit test. Extend `tools/desktop-journeys/` per
`reference_desktop_journeys`; same prompt, same 52-tool Linear server, flag on vs off; record input
tokens, wall-clock to first tool call, and whether the right tool was found. This is the artifact
that decides whether the branch stays. Note `tools/desktop-journeys/README.md` overstates what the
driver can do for OAuth (PRD §8.2) — the connector must be pre-connected.

---

## 10. Open questions

- [ ] **Does the item-1 measurement pick B?** Everything here is downstream of that one answer (§5).
- [ ] **`regex` or `bm25`?** Undecided, and it is the variable most likely to move accuracy the wrong
      way on 52 self-similar connector tool names. Decide by measurement; `ToolSearchMode` exists so
      the choice is a config value, not a rewrite.
- [ ] **Does deferral withhold the whole definition or only the schema?** (§1.5.) Determines whether
      the saving is ~17k tokens or materially less. One live call answers it.
- [ ] **Should discovery emit a typed run event?** A `tool_search_tool_result` block is currently
      unmodelled (trap §3.4). Cheapest correct answer is to classify it INTERNAL in
      `StreamMessageProcessor.internal_tool_names`; the richer answer mirrors `TodoListProjector` and
      publishes a typed discovery event. Recommend INTERNAL first.
- [ ] **How does the Context Occupancy Ledger represent a deferred tool?** (Trap §3.5.) Without an
      answer, AC5 cannot be evidenced from our own telemetry.
- [ ] **Does `defer_loading` interact with `cache_control`?** Both are `_ANTHROPIC_EXTRA_FIELDS`
      members (`chat_models.py:166-172`) and both concern what is resident in the prompt. Unverified.

---

## 11. Deferred / out of scope

Anthropic's other `advanced-tool-use-2025-11-20` surfaces — `input_examples` (which triggers the same
beta independently, `chat_models.py:1396-1404`) and `allowed_callers` — are not part of this item.
Note only that `input_examples` would auto-append the beta on _any_ provider path that set it, which
is a second way to reach the beta client accidentally.

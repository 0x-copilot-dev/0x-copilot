# Anthropic Tool Search (`defer_loading`) — disposition

**Status:** **CLOSED as framed.** The MCP half is superseded by shipped code. A residue exists
(the LOCAL tool surface) and is re-scoped in §6 — measured, costed, and **not recommended**.
**Parent:** [`PRD.md`](./PRD.md) §6 (item 6, P4) · **Sibling:** [`PER-TOOL-DECISION.md`](./PER-TOOL-DECISION.md)
**Verified against:** `15814fc1` · **Owner:** ai-backend

> This document was a build plan. It is now a disposition. The mechanism research that justified the
> build is **kept in full** (§8–§9) because it is the expensive part and it is what a reopen would
> otherwise re-derive; it has simply been demoted below the decision.

---

## 0. TL;DR

**Close item 6 as written. The deferral it proposed already happened, by a different and
provider-neutral mechanism.**

The MCP filesystem catalog does not _layer under_ `defer_loading` — it **occupies the same slot**.
Both answer one question: _how do 52 connector tool schemas stay out of the prompt until the model
needs one?_ The catalog answers it with files the model reaches through `ls` / `grep` / `read_file`;
`defer_loading` answers it with a wire flag Anthropic honours. Two answers, one question, and the
shipped one works on all six providers.

Concretely, verified at `15814fc1` (§1–§3): **there are no per-tool MCP definitions in the model
context to defer.** The live path registers one `call_mcp_tool` definition; the 52 schemas live as
`/mcp/<server>/tools/<tool>.json` files behind a read-only backend mount. Deferring nothing saves
nothing. PRD §7's end-state **C** ("catalog + gateway + defer") is incoherent for that reason, and
this document already said so — §8.2 below is the original arithmetic, unchanged.

The residue is real but small and points the other way: the **local** tool block measures
**2,035 estimated tokens across 8 tools**, plus **2,981** for the three gated artifact tools when a
run has them (§4, measured with the repo's own ledger). Most of that cannot be deferred — the
largest entries are needed on turn 1 — leaving roughly **770 estimated tokens** that genuinely
could be. Against the ~17k this item was written for that is a **~20× smaller prize for the same
Anthropic-only branch cost.** §6 sketches the implementation anyway, so the option is costed rather
than merely dismissed; §7 says why it should not be built, and names what would reopen it.

**Anthropic-only remains the constraint that decides this either way.** `ModelConfigResolver.PROVIDER_ALIASES`
(`execution/models.py:54-72`) admits six canonical providers and the live default is GPT-5.4 mini.
Whatever the prize, five of the six cannot collect it.

---

## 1. What shipped, and what it did to this item

Each row was read at `15814fc1` before this disposition was written.

| Fact                                                                    | Evidence                                                                                                                                                                                                                        |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Every connector tool is a **real file** with its full contract          | `capabilities/mcp/catalog.py:347-350` — `McpCatalogPaths.tool_file` → `/mcp/<server>/tools/<tool>.json`; rendered by `McpCatalogRenderer.tool_file` (`:587`) as "schema, output shape, action class, and the dispatch envelope" |
| Those files are reached through a **backend mount**, not a prompt block | `capabilities/mcp/catalog_backend.py:101-105` — `McpCatalogBackend(BackendProtocol)`, `PATH_PREFIX = "/mcp/"`, with real `ls` / `glob` / `grep` / `read` over its own content                                                   |
| The mount is composed as a `CompositeBackend` route                     | `execution/factory.py:789` `_with_mcp_catalog_route` — joins an existing composite, or builds one over deepagents' own `StateBackend`                                                                                           |
| The directory is non-empty from the **first model turn**                | `execution/factory.py:756` — `catalog.seed(McpCatalogBuilder.seed_all(cards))`, from the compact cards the registry already holds; no network call                                                                              |
| Dispatch is **one** gateway definition on the live path                 | `execution/factory.py:949-950` — the `else` branch of the per-tool check registers a single `CallMcpTool`                                                                                                                       |
| Per-tool registration is **off by default**                             | `capabilities/mcp/per_tool_registration.py:115` — `_DEFAULT_WHEN_UNSET = "false"`; `McpPerToolRegistrar.build` returns `None` at `:321-322`                                                                                     |
| …and declines for **four further reasons** even when the flag is on     | `per_tool_registration.py:324-341` — no credential plane injected, no `resolve_server` seam, the load raised, or the load registered nothing                                                                                    |

### 1.1 The one phrase that misleads

`catalog.py` calls `SERVER.md` the **"always-loaded tier"**. Read carelessly, that says the index is
in the prompt, which would mean there is resident MCP text to trim. It is not. "Always-loaded" there
means _always written into the catalog filesystem_ — the tier that exists before any `load_mcp_server`
call, so `ls /mcp` is never an empty listing. Getting `SERVER.md` into the context window still costs
the model one `read_file`, exactly like a tool file does. **Nothing the catalog produces is resident.**

### 1.2 Why this closes the item rather than gating it

The original plan (§8.3, preserved) gated item 6 on which end-state the item-1 measurement picked:
**A** (catalog + gateway) → close; **B** (catalog + per-tool) → build, because `defer_loading` is what
makes B affordable. That gate has effectively resolved in the direction of **A** without waiting for
the measurement, for a reason the gate did not anticipate: **A is not merely the recommended
end-state, it is the only one that can currently run.** Per-tool needs five independent conditions to
hold (§1, last two rows), and **two of them are false in every shipped configuration** — not merely
defaulted off:

1. `MCP_PER_TOOL_ENABLED` defaults to `"false"` (`per_tool_registration.py:115`). Recoverable: it is
   one environment value.
2. **The credential plane has no producer.** `RuntimeDependencies.mcp_per_tool_collaborators` is
   declared at `execution/contracts.py:890` with default `None` and read at `factory.py:643` — and
   nothing in `services/ai-backend/src/` ever assigns it. The only assignment in the repository is
   `tests/unit/agent_runtime/execution/test_mcp_per_tool_flip.py:821`. So even with the flag flipped
   on, `McpPerToolRegistrar.build` returns `None` at the `collaborators is None` check
   (`per_tool_registration.py:324-327`) and the run keeps the gateway.

That second fact is what turns a scoring judgement into a close: **item 6's saving is not merely
unrealized, it is unreachable without first building a credential plane that does not exist.**
`PER-TOOL-DECISION.md` §0 independently recommends A and §7 pre-agrees that adopting A closes this
item.

That does **not** make B unreachable forever, and §7 keeps the reopen trigger honest. It makes item 6
unbuildable _today_ against a configuration that does not exist, which is a different and stronger
reason to close than "we scored it lower".

---

## 2. What is actually in the model context per run

Composed at one seam, `_model_visible_tools` (`execution/factory.py:850-1121`). Every entry carries a
`ModelToolDeclaration.declared(...)` naming the owner of its schema text, which is what makes the
inventory below auditable rather than asserted.

**Resident on every model call:**

1. The deepagents / LangChain middleware built-ins — `write_todos`, `task`, `ls`, `read_file`,
   `write_file`, `edit_file`, `glob`, `grep` (`per_tool_registration.py:142-157` enumerates them;
   `execute` is withheld, `execution/tool_surface.py:44`). Registered when the graph is built, so they
   are absent from the factory's own list. **These are the catalog's discovery primitives and can
   never be deferred** — deferring them would strand a model that has to `grep` to find anything.
2. `load_mcp_server`, `call_mcp_tool`, `auth_mcp` — the MCP lane, three definitions total.
3. `ask_a_question`, `list_connected_servers`, `suggest_mcp_connector`, `load_skill`,
   `load_prior_tool_result` — the local lane.
4. Conditionally: `publish_artifact`, `revise_artifact`, `stage_rowset_write` (surfaces-v2 runs),
   `code_mode` / `sandbox_execute` (flag + desktop gated), and the F3 capability-bridge tools
   (empty in the current production posture).

**Not resident, pulled on demand:** every MCP tool schema (catalog files), every skill body
(`load_skill`), every large prior tool result (`load_prior_tool_result`), every `/workspace/` file.

That second list is the point. **The runtime already applies "one small resident loader + the bulk as
files" three separate times**, in three subsystems that were built independently:

| Subsystem   | Resident definition      | Bulk lives                   | Site                                     |
| ----------- | ------------------------ | ---------------------------- | ---------------------------------------- |
| Skills      | `load_skill`             | Skill markdown, on demand    | `capabilities/skills/middleware.py:27`   |
| Tool output | `load_prior_tool_result` | Content-addressed store      | `capabilities/tools/prior_results.py:27` |
| MCP         | `load_mcp_server`        | `/mcp/<server>/tools/*.json` | `capabilities/mcp/catalog.py`            |

`defer_loading` is a fourth spelling of that same idea, implemented one layer down (in the provider
rather than in the runtime) and available to one provider in six. **That is the whole disposition in
one sentence.**

---

## 3. What we lose by closing this

Stated plainly, because a close that only lists wins is a decision nobody can audit.

1. **Round-trips.** This is the real loss and it was always the stronger half of the case. The catalog
   costs the model up to three agent-loop turns to find a tool — `ls` → `grep` → `read_file` — and each
   turn re-sends the entire conversation. `defer_loading` collapses that into one **server-side** hop
   with no context resend. On a long conversation, three round-trips of a 40k-token context is not
   obviously cheaper than what deferral would have saved; `PER-TOOL-DECISION.md` §4 axis 1 flags the
   same thing as the one place its own verdict could invert. **We are choosing latency-and-tokens-later
   over a provider lock-in, and it is a genuine trade, not a free one.**
2. **Provider-side constrained decoding.** A deferred-then-retrieved tool arrives at the model as a
   real tool definition with a real JSON Schema, so the provider's constrained generation applies to
   its arguments. The gateway hands the model `arguments: dict[str, Any]` (`capabilities/mcp/cards.py:251`)
   and nothing constrains it. `PER-TOOL-DECISION.md` §7 item 2 already carries the compensating work —
   validate `McpToolCallRequest.arguments` against the stored catalog schema inside
   `CallMcpToolInputParser`. **That compensation is now load-bearing rather than optional, because
   closing this item removes the other route to argument fidelity.**
3. **A ready-made answer if the default model moves to Claude.** Kept as trigger T1 in §7.
4. **Nothing on security, policy, or dispatch.** `defer_loading` never touched those: the deferred
   tool is the same `BaseTool` through the same `ToolNode` and the same middleware stack (§9.3). We
   are not closing a control.

**Not lost:** the token saving. There is no resident MCP tool block to save (§1), so the headline
number this item was justified on does not exist on any shipped configuration.

---

## 4. The residue, measured

Everything above concerns MCP tools. The **local** tool surface is resident, is authored by this
repository, and is the only thing `defer_loading` could still withhold. So it was measured rather
than estimated.

**Method.** The repo's own serializer and counter, so these are the same numbers the Context
Occupancy Ledger reports and the same bytes `tool_schema_revision` digests:
`ToolSchemaLedger.schema_entry` (`observability/context_tool_ledger.py:173`) produces the exact
body-free `name` + `description` + expanded `args_schema` the provider is shown, counted with
`HeuristicToolSchemaTokenCounter` (char/4 over UTF-8 bytes, rounding up). Class-level `description`
attributes were confirmed present on every tool measured, so no row is silently zero.

**Always-composed local surface — 8,127 bytes / 2,035 estimated tokens:**

| Tool                     | Bytes | Est. tokens | Deferrable?                                          |
| ------------------------ | ----: | ----------: | ---------------------------------------------------- |
| `ask_a_question`         |  2434 |         609 | **No** — the clarification / approval channel        |
| `suggest_mcp_connector`  |  1496 |         374 | Yes                                                  |
| `call_mcp_tool`          |   963 |         241 | **No** — the only dispatch door                      |
| `list_connected_servers` |   942 |         236 | Marginal — the "what do I have" turn-1 tool          |
| `load_mcp_server`        |   706 |         177 | **No** — needed before the catalog has any tool file |
| `load_prior_tool_result` |   631 |         158 | Yes                                                  |
| `auth_mcp`               |   509 |         128 | Yes                                                  |
| `load_skill`             |   446 |         112 | Yes                                                  |

**Conditionally composed artifact trio — 11,918 bytes / 2,981 estimated tokens:**

| Tool                 | Bytes | Est. tokens |
| -------------------- | ----: | ----------: |
| `publish_artifact`   |  5474 |        1369 |
| `stage_rowset_write` |  4285 |        1072 |
| `revise_artifact`    |  2159 |         540 |

> **A number in the source is understated.** `factory.py:1099-1101` and
> `context_tool_ledger.py:6-9` both describe the trio as "~1,337 tokens" and `publish_artifact` as
> "~650". Those are **description text only** — `publish_artifact.description` alone measures 741.
> The full wire entry, including the expanded `args_schema`, is **1,369** for `publish_artifact` and
> **2,981** for the trio. The comments are not wrong about what they measured; they are read as the
> resident cost and they are **~2.2× under it**. That gap is worth correcting in
> `execution/factory.py` and `observability/context_tool_ledger.py` — see the follow-up note at the
> end of this document.

**So the honest residue is 772 est-tokens** — `suggest_mcp_connector` + `load_prior_tool_result` +
`auth_mcp` + `load_skill`, the four rows marked deferrable above — **or 1,008 if
`list_connected_servers` is counted, or ~3,750 on a surfaces-v2 run where the artifact trio is also
deferrable.** Against ~17k for the MCP case this item was written for. The always-on figure is the
one that matters, because the trio is absent from most runs.

---

## 5. Disposition

| Half of item 6                                       | Disposition                                                         |
| ---------------------------------------------------- | ------------------------------------------------------------------- |
| Defer **MCP per-tool** definitions                   | **CLOSED — superseded.** No such definitions exist in context (§1). |
| Defer the **local** tool surface                     | **RE-SCOPED — sketched (§6), not recommended (§7).**                |
| PRD §7 end-state **C** ("catalog + gateway + defer") | **WITHDRAWN.** Incoherent against the mechanism (§8.2).             |
| PRD **item 7** (per-tool approval-id uniqueness)     | Follows `PER-TOOL-DECISION.md`, not this document. Moot under A.    |

PRD §6's acceptance criteria retire with the item, except **AC2** ("one source of truth for tool
definitions"), which survives on its own merits and is already satisfied structurally: the catalog
file and any future deferred definition would both project from `McpToolMetadataFile`
(`capabilities/mcp/files.py:159-201`), neither deriving from the other. Keep that property; it is
what makes §6 below a ~200-line adapter rather than a second definition pipeline.

---

## 6. If it is built anyway — the residue, sketched

Costed so the option is a decision rather than an unknown. Everything here is the original §6/§7
design narrowed from "MCP tools" to "rarely-used local tools"; the traps in §9 apply unchanged.

**Module:** `services/ai-backend/src/agent_runtime/execution/providers/tool_search.py`, mirroring the
existing provider-adapter precedent — `CitationStreamPipeline.for_provider` → `_AdapterRegistry.build`
(`execution/providers/citation_pipeline.py:91-119`): slug-keyed table, lazy imports, **no-op adapter
for unknown providers** so the pipeline installs unconditionally.

```
ToolSearchPolicy            — the only place the provider slug is compared; returns a typed
                              decision, never a bare bool, so the reason is observable
NoopToolSearchAdapter       — identity. Every non-Anthropic provider.
AnthropicToolSearchAdapter  — sets extras["defer_loading"] on eligible tools; appends the
                              one search built-in dict
ToolSearchAdapterRegistry   — slug → adapter, lazy import, no-op default
```

**Seam.** `_assemble_harness` (`execution/factory.py:238+`) already holds both facts — the composed
tool tuple and `runtime_context.model_profile` carrying the canonical provider slug. No plumbing.

**Eligibility — an allowlist, not a denylist.** This is the one substantive change from the original
design, and it is forced by the re-scope. Deferring MCP tools could be expressed as "everything in
`McpPerToolRegistration.tools`" because that set was self-evidently safe. There is no equivalent set
here: the local surface is a handful of tools of which **most are needed on turn 1**. So the
deferrable set is an explicit, reviewed constant — today `{suggest_mcp_connector,
load_prior_tool_result, auth_mcp, load_skill}` — and adding a name to it is a deliberate edit with a
stated reason, not a rule that sweeps new tools in silently. Never a deepagents built-in
(`HarnessBuiltinToolNames.NAMES`), never `call_mcp_tool`, never `load_mcp_server`, never
`ask_a_question`.

**Gate conditions, all failing closed to the current path:**

1. Canonical provider slug is exactly `"anthropic"` — never "the model name contains claude" (§9.2).
2. `FakeModelProvider.is_enabled()` is `False` (`execution/fake_model.py:262`, already consulted the
   same way at `execution/models.py:116`) — the hermetic fake model must never take a provider branch.
3. `ANTHROPIC_TOOL_SEARCH_ENABLED` is truthy. Follow `McpPerToolFlag` verbatim
   (`per_tool_registration.py:96-127`): self-contained env reader, `ClassVar` name, injectable
   `environ`, default off.
4. At least N eligible tools present (proposed floor: 3) — below that the search round-trip costs
   more than the definitions.

**Contracts** (Pydantic at the boundary; `RuntimeContract` is the `extra="forbid"` base):

```python
class ToolSearchMode(StrEnum):
    REGEX = "regex"
    BM25 = "bm25"


class ToolSearchDisabledReason(StrEnum):
    """Why a run did not take the deferred path. Every value is observable."""
    PROVIDER_NOT_ANTHROPIC = "provider_not_anthropic"
    FLAG_OFF = "flag_off"
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

The reason-iff-disabled validator is what prevents the "enabled but nothing deferred" state — the
shape that would make the feature look on while doing nothing. Constants (the dated `type` strings of
§9.1, the `Literal` `name` values, the `"defer_loading"` extras key) live on a nested `Keys` class,
never as call-site literals.

**Tests** — unit, hermetic, fakes only; fakes and constants in mixins, concrete classes hold only
`test_*`:

| #   | Asserts                                                                                                                                                                                                                              |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| T1  | Definition-preserving. Non-Anthropic → identical tuple. Anthropic → same names, order, schemas; the only diffs are `extras["defer_loading"]` and one appended dict.                                                                  |
| T2  | **The beta materializes.** Build through `ChatAnthropic._get_request_payload`; assert `betas == ["advanced-tool-use-2025-11-20"]`. Assert the header, not the tool's presence (§9.1). A sibling test pins the dated `type` constant. |
| T3  | **Cache identity.** `tool_schema_revision` differs between deferred and non-deferred composition of the same tools (§9.3).                                                                                                           |
| T4  | Gate matrix — every `ToolSearchDisabledReason` reachable, notably `openrouter` + `anthropic/claude-*` → `PROVIDER_NOT_ANTHROPIC` (§9.2) and fake-model → `FAKE_MODEL`.                                                               |
| T5  | **Off is identical.** Flag off, and unsupported provider, each produce a byte-identical tool list.                                                                                                                                   |
| T6  | **The allowlist holds.** No deepagents built-in, no `call_mcp_tool`, no `load_mcp_server`, no `ask_a_question` is ever marked deferred — asserted against the composed surface, not against the constant.                            |
| T7  | Typed failure — a provider `bad_request` on the beta surfaces as an `AgentRuntimeError` with a safe public message; no traceback, no beta string, in model output or HTTP response.                                                  |

**Estimate:** ~200 LOC production, ~350 LOC tests, zero change to dispatch, policy or middleware.

---

## 7. Verdict on the residue, and what would reopen this

**Do not build it.** Four reasons, in order of weight:

1. **The prize shrank ~20× and the branch cost did not.** 772 est-tokens of genuinely deferrable
   always-on local tools (§4) against the ~17k this item was written for. The adapter, the gate, the
   flag, the seven tests, the beta-header trap and the cache-identity trap are all the same size for
   the smaller prize.
2. **It buys a fourth spelling of a pattern the runtime already has three of** (§2). If the local
   surface ever becomes the constraint, the provider-neutral fix is the one this repo has already
   shipped three times — a small resident loader plus the bulk as files — not an Anthropic-only wire
   flag. That fix would help GPT-5.4 mini, which is the default and the model whose run produced the
   failure this whole program exists to fix.
3. **Most of the local surface cannot be deferred at all.** The single largest always-on entry is
   `ask_a_question` (609 est-tokens, 30% of the block) and it is the clarification and approval
   channel — needed on essentially any turn. Add the two MCP entry points that must exist before the
   catalog is reachable (`call_mcp_tool` 241, `load_mcp_server` 177) and **1,027 of 2,035 est-tokens
   — half the always-on block — is structurally undeferrable.** The deferrable set is the tail, by
   construction, and it will stay
   the tail: a tool becomes deferrable by being rare, and rare tools are small.
4. **A second binding path for tool definitions is the risk, not the line count.** Two ways for a
   definition to reach a model is the class of divergence that produced the `RetryingTool`
   `response_format` bug (PRD §8.1). Worth accepting for a 17k-token win; not for 772.

**Falsifiable reopen triggers.** Any one of these makes §6 worth building as written:

- **T1 — the default model becomes Anthropic and stays there.** A product decision, not a technical
  one, and the same trigger as `PER-TOOL-DECISION.md` §7 T3. It changes reasons 1 and 2 simultaneously:
  the branch stops being a minority path, and "provider-neutral" stops being an argument.
- **T2 — the always-on local surface roughly doubles.** ~2k est-tokens today (§4). If new capability
  tools push the resident block past ~4k while the deferrable share stays high, the arithmetic in
  reason 1 inverts. The Context Occupancy Ledger already reports the number, so this is observable
  rather than something anyone has to remember to check.
- **T3 — per-tool registration actually ships** (PRD §7 end-state B). That needs _both_ halves of
  §1.2: the flag on **and** a real producer for `RuntimeDependencies.mcp_per_tool_collaborators`.
  Then the original 17k case returns intact, §6's eligibility allowlist reverts to "everything in
  `McpPerToolRegistration.tools`", and §8–§9 below are the plan of record. `PER-TOOL-DECISION.md`
  §7 T1/T2 are the measurements that would drive this. Note the ordering: **this trigger cannot fire
  before the credential plane exists**, so it is not a near-term risk to the close.

`PER-TOOL-DECISION.md` §7's measurement should still run. If it inverts to B, reopen under T3 —
B without deferral is the 17k-token prompt everyone was avoiding.

---

## 8. Preserved: why end-state C was incoherent

Kept because the arithmetic is the load-bearing part of the close, and because PRD §7 still lists C.

### 8.1 The resident cost, then and now

The live Linear descriptor is **70,465 bytes across 52 tools** (PRD §1.1). At ~4 chars per token for
dense JSON that is on the order of **~17k tokens** if every schema were resident — arithmetic on the
PRD's measured byte count, not a measurement. **They are not resident**, and §1 above shows why: the
flag is off, and the catalog put them on a filesystem.

### 8.2 Which end-state each option pays off in

| End-state                           | Registration        | What `defer_loading` would defer                                                                       | Saving                              |
| ----------------------------------- | ------------------- | ------------------------------------------------------------------------------------------------------ | ----------------------------------- |
| **A — catalog + gateway**           | one `call_mcp_tool` | nothing worth deferring; `read_file`/`grep`/`ls` are needed on turn 1 and the gateway is the only door | **≈ 0**                             |
| **B — catalog + per-tool**          | 52 tools            | 52 MCP schemas, ~17k tokens                                                                            | **large — this was the whole case** |
| **C — "catalog + gateway + defer"** | gateway             | same as A                                                                                              | **≈ 0 — the row is incoherent**     |

You cannot defer a gateway you always need, and the 52 tools it fronts were never definitions in the
first place. **C collapses into either A (no defer) or B (defer is the point).** A is what shipped.

### 8.3 The original framing, for the record

> Tool Search is not an accelerant layered on the catalog. It is the mechanism that makes per-tool
> registration affordable — on Anthropic only. It competes with the catalog's discovery step, and
> composes only with the catalog's _artifacts_.

That reframing stands and is why the item closes rather than waits: the thing it would have made
affordable is not shipping.

---

## 9. Preserved: the mechanism, verified

All of the following was read or executed against this repo's own service venv,
`services/ai-backend/.venv` — `langchain-anthropic==1.4.8`, `anthropic==0.117.0`
(`services/ai-backend/requirements.txt:7,41`). Kept intact so a reopen under §7 does not repeat it.

### 9.1 The capability, and the trap that silently disables it

| Claim                                                              | Evidence                                                                                                                                                                            |
| ------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `defer_loading` is a first-class field on a custom tool definition | `langchain_anthropic/chat_models.py:118` — `defer_loading: NotRequired[bool]` on the `AnthropicTool` TypedDict                                                                      |
| Two tool-search built-ins exist, each mapped to a beta header      | `chat_models.py:146-147` — `"tool_search_tool_regex_20251119"` and `"tool_search_tool_bm25_20251119"` → `"advanced-tool-use-2025-11-20"`                                            |
| `defer_loading` survives conversion from a LangChain tool          | `chat_models.py:169` — in `_ANTHROPIC_EXTRA_FIELDS`; copied at `chat_models.py:2265-2274`                                                                                           |
| The built-in dict is passed through unconverted                    | `chat_models.py:153-162` — `_BUILTIN_TOOL_PREFIXES` includes `"tool_search_"`; `_is_builtin_tool` prefix-matches on `type`                                                          |
| The beta routes to the beta client                                 | `chat_models.py:1428-1435` — `if "betas" in payload: self._client.beta.messages.create(...)`                                                                                        |
| The host accepts a raw dict tool without a fork                    | `langchain/agents/factory.py:1052-1053` splits `isinstance(t, dict)` into `built_in_tools`, excluded from `ToolNode` (`:1070-1071`) and from unknown-tool validation (`:1307-1309`) |

The SDK documents the field as _"If true, tool will not be included in initial system prompt. Only
loaded when returned via tool_reference from tool search."_ (`anthropic/types/tool_param.py`).

**Trap — the undated `type` alias silently disables the beta.** The SDK accepts both
`"tool_search_tool_regex_20251119"` and `"tool_search_tool_regex"`. LangChain's `_TOOL_TYPE_TO_BETA`
(`chat_models.py:145-148`) is an **exact-key dict** holding only the dated form, while
`_is_builtin_tool` uses a **prefix match**. The undated alias therefore passes through as a built-in
and appends **no beta**. Confirmed by payload construction:

```
type = 'tool_search_tool_regex'          -> betas = None   (tool still passed through)
type = 'tool_search_tool_regex_20251119' -> betas = ['advanced-tool-use-2025-11-20']
```

The failure is silent at construction and surfaces as an opaque provider 400 — or worse, as tools
that are deferred with no way to retrieve them. Hence §6 T2: assert the **header materialized**, not
that the tool is in the list.

### 9.2 The provider gate must key on the slug

`ModelConfigResolver._infer_provider` (`execution/models.py:253-257`) maps a `vendor/model` slug —
e.g. `anthropic/claude-opus-4-5` — to **`openrouter`**, and `openai_compat.py:1-28` routes OpenRouter
through `langchain_openai.ChatOpenAI` against `/chat/completions`. A `defer_loading` key sent there is
at best ignored and at worst a 400. **Gate on the canonical provider slug being exactly `"anthropic"`,
never on the model name containing `claude`.** This is the single most likely way to get this wrong.

### 9.3 Three more traps that survive the re-scope

- **Cache identity.** `factory.py:395` computes `tool_schema_revision` and `factory.py:1533-1541`
  records that the digest _"is bound into prompt-cache identity"_. Deferring changes the set of
  definitions actually sent, so the digest must be computed over the **post-gate** list and
  `defer_loading` must participate in it — otherwise the cache key claims two materially different
  prompts are the same. The digest now has one producer, `ToolSchemaLedger.revision`
  (`observability/context_tool_ledger.py:199`), which is where that change would land.
- **A new stream content block.** `tool_search_tool_result` blocks would flow through the worker's
  stream path. The house pattern for frames that are noise to the client is
  `StreamMessageProcessor.internal_tool_names` (`runtime_worker/stream_tools.py:75`). Classify there
  rather than surfacing a mystery card in the cockpit.
- **The occupancy ledger would over-report.** `ModelToolDeclaration.declared(...)` is stamped at
  composition time and the ledger reports resident context per tool. A deferred tool's schema is not
  resident, so the ledger would report the pre-deferral footprint and the feature's entire measured
  benefit would be invisible in our own observability.

### 9.4 Security — unchanged by deferral, and that is the point

- **No policy bypass, structurally.** The search result carries **references, not invocations**:
  `anthropic/types/beta/beta_tool_search_tool_search_result_block.py` — `tool_references:
List[BetaToolReferenceBlock]`. `defer_loading` changes only the wire representation of the
  definition; the object, name, schema and execution path are identical, so the tool still flows
  through `ToolNode` and the composed middleware stack (POLICY → EXEC_POLICY → OBSERVE → ERROR_MAP →
  CITATIONS, `capabilities/mcp/middleware/compose.py`). PRD §6 AC4 was satisfied structurally, not by
  an added check.
- **The search index would be built from untrusted text.** Tool names and descriptions come from the
  MCP server, which `services/ai-backend/CLAUDE.md` lists as untrusted. A hostile server could write a
  description engineered to win the search for an unrelated query — a retrieval-time confused deputy.
  Not new (the same text is already reachable today), but deferral makes it _selective_, which is a
  slightly sharper edge. The PDP decision on the eventual call is the control, and it is unchanged.
- **No secrets in deferred definitions**, provided they project from `McpToolMetadataFile` rather than
  from a live descriptor: that contract is `extra="forbid"` with no credential fields and is scanned
  by `SecretShapeScanner.assert_clean` (`capabilities/mcp/files.py:110-130`) before write.
- **`allowed_callers` is available and unused.** Both search-tool param types and `ToolParam` accept a
  list of `"direct"` / `code_execution_*` literals. Out of scope; noted as the seam if code-execution
  tool access is ever scoped.

### 9.5 What was never verified

Stated plainly, because the closed economics rested on it:

- **No live API call was ever made.** No token count, latency or accuracy figure attributed to the
  vendor in earlier drafts of this document was measured here. The 77,000 → 8,700 token (85%) and
  Opus 4.5 79.5% → 88.1% figures were **vendor-published and unverified**, and are not relied on by
  this disposition.
- **Which Claude models support the beta.** The pinned code has no model allowlist;
  `_TOOL_TYPE_TO_BETA` is model-agnostic. Whether a given `claude-*` id 400s is not determinable from
  the repo. §6 treats this as a fail-open runtime concern rather than a static list, which would rot
  exactly like the seven divergent model lists this repo already killed.
- **Whether `defer_loading` withholds the whole definition or only `input_schema`.** The docstring
  reads as the whole definition; not confirmed on the wire. §8.1's arithmetic assumes
  whole-definition withholding — if only the schema is withheld, the saving is smaller and the closed
  case is weaker still.
- **BM25 vs regex retrieval quality.** Unmeasured. On 52 self-similar connector names
  (`list_issues`, `list_projects`, …) it is a plausible accuracy _regression_, which is why
  `ToolSearchMode` exists in §6 as a config value rather than a rewrite.
- **Interaction with `cache_control`.** Both are `_ANTHROPIC_EXTRA_FIELDS` members
  (`chat_models.py:166-172`) and both concern prompt residency. Unverified.

### 9.6 Also deferred

Anthropic's other `advanced-tool-use-2025-11-20` surfaces — `input_examples` (which triggers the same
beta independently, `chat_models.py:1396-1404`) and `allowed_callers`. Note only that
`input_examples` would auto-append the beta on **any** provider path that set it, which is a second
way to reach the beta client accidentally.

---

## 10. Follow-up this disposition creates

Outside this document's scope; recorded so it is not lost.

1. **The stale token comments in §4.** `execution/factory.py:1085-1090` and
   `observability/context_tool_ledger.py:6-9` describe the artifact trio as "~1,337 tokens" and
   `publish_artifact` as "~650". Those are description-only counts, presented as the resident cost,
   and the full wire entries measure 2,981 and 1,369. Correct the comments, or state that they count
   description text only.
2. **PRD §6 and §7 still describe item 6 as open and list end-state C.** They should point here.
3. **`PER-TOOL-DECISION.md` §7's measurement is still the thing that decides B.** Unchanged by this
   close, and the source of reopen trigger T3.

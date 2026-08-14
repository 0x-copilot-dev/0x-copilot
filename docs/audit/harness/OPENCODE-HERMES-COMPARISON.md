# 0xCopilot vs OpenCode vs Hermes — harness comparison

**Date:** 2026-08-14
**Subjects:**

- **OURS** — 0xCopilot, this repo (`services/ai-backend` + `services/backend` + `packages/chat-surface` + `apps/desktop`).
- **OPENCODE** — [sst/opencode](https://github.com/sst/opencode), MIT, `--depth 1` clone at `~/Documents/work/opencode`,
  222 MB, **675,435 lines of TS/TSX across 30 packages** (`packages/opencode` 176k, `packages/app` 171k,
  `core` 68k, `console` 42k, `ui` 34k, `tui` 32k, `sdk` 30k). Effect-TS throughout. Never audited before.
- **HERMES** — [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent), clone at
  `~/Documents/work/hermes-agent`. Prior audit: [../generative-ui/HERMES-COMPARISON.md](../generative-ui/HERMES-COMPARISON.md) (2026-08-04).

**Companion docs:** [generative-ui/HERMES-COMPARISON.md](../generative-ui/HERMES-COMPARISON.md) ·
[generative-ui/FINDINGS.md](../generative-ui/FINDINGS.md) · [generative-ui/STATE.md](../generative-ui/STATE.md)

## Method and honesty

Nine dimension agents, each reading all three codebases and citing `file:line` on every side, then **two
adversarial critics**: one hunting claims that flatter OURS without evidence, one deduping and re-ranking the
gaps. Coverage:

| Dimension                                                                                                   | Status                                                                                                                                                                        |
| ----------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| tools-mcp, permissions-safety, state-persistence, extensibility, protocols, delegation, client, engineering | ✅ first run                                                                                                                                                                  |
| **agent-loop**                                                                                              | ⚠️ **failed twice** (connection closed mid-stream). §1.6 is written from my own first-hand reading — narrower than the others, and it did **not** pass through either critic. |
| critic ×2                                                                                                   | ✅                                                                                                                                                                            |

**The flattery critic examined 18 "we do better" claims and REFUTED 6 of them.** Those six are recorded in §2,
not quietly dropped. The refutation pattern is worth more than the claims: **reachability, not correctness.**
Three of the six cite source that exists and works, but whose only consumer is a test, a deprecated web
screen, or nothing at all — and in two cases the _same report_ documented the unreachability in its own gaps
section and still scored it as a win.

---

## The verdict in one paragraph

**OpenCode is a better-engineered coding agent; we are a better-engineered product platform, and the gap
between what we have built and what a user can reach is our defining problem.** OpenCode wins outright on
seven of nine dimensions — plugins, protocols, undo, distribution, client engineering, test-fake placement,
and MCP mechanics — largely because it makes one bet we don't: **everything is user-authorable and everything
is generated from one schema.** We win on the things a solo coding agent never needs and can't retrofit: a
real identity/tenancy control plane (17,654 LOC of SAML/SCIM/MFA/RBAC against OpenCode's `packages/identity`,
which is **six brand image files**), an MCP trust boundary where the vendor credential never enters the agent
process, machine-checked service boundaries, and a resumable event cursor. But the recurring finding across
every single dimension is the same: **we ship the mechanism and skip the last seam.** FTS5 transcript search,
the conversation archiver, approval expiry, `grant_options`, the delegation planner, skill `allowed_tools`,
desktop skill authoring — all built, all correct, all unreachable.

---

## Dimension summary

| Dimension                | Who's ahead  | The one-line reason                                                                               |
| ------------------------ | ------------ | ------------------------------------------------------------------------------------------------- |
| Agent loop               | **OpenCode** | They own the loop and get a header-aware retry policy + configurable compaction; we rent it       |
| Tool layer / MCP         | **split**    | Our trust boundary + descriptor filesystem; their namespacing, schema repair and result capping   |
| Permissions / approvals  | **split**    | Their `(key × pattern) → ask/allow/deny` ruleset is far more expressive; our floor is harder      |
| Session / persistence    | **OpenCode** | They can undo an agent's edits to your files. We have no snapshot mechanism at all                |
| Extensibility            | **OpenCode** | 26-hook npm-published typed plugin SDK vs **no hook seam of any kind** in OURS                    |
| Protocols / integration  | **OpenCode** | One Effect HttpApi reflected into CI-diff-gated generated clients; we hand-maintain a 6.5k mirror |
| Delegation / concurrency | **OpenCode** | Confined interpreter + worktree isolation + background jobs + depth caps; ours blocks, uncapped   |
| Client / rendering       | **split**    | Our tool-result floor is now the best of the three; their client engineering is far ahead         |
| Engineering system       | **split**    | We own boundaries + identity; they own the fake boundary, e2e-per-PR, and distribution            |

---

## 1. What we genuinely do better

These survived adversarial verification. Each was re-checked against the cited files by a critic instructed to
default to REFUTED.

### 1.1 The MCP trust boundary — our single strongest architectural win

The vendor OAuth credential **never exists in the agent process**. `ai-backend` dials a deliberately
unroutable synthetic origin and every MCP JSON-RPC frame is tunnelled through `backend`'s per-server RPC
endpoint, which re-authorises each call. The credential provider inside the agent returns `backend`'s
_service_ headers, not a vendor secret.

- OURS: `services/ai-backend/src/agent_runtime/capabilities/mcp/proxy_plane.py:1-27`, `:47-52`
  (`SYNTHETIC_ORIGIN = "https://mcp-proxy.invalid"`, _"no vendor secret exists in this process at all"_);
  wired at `execution/factory.py:700-712`; server side `services/backend/src/backend_app/app.py:1593` with
  `token_vault.py`.
- OpenCode persists access tokens, refresh tokens **and client secrets** to `mcp-auth.json` in the same
  process that runs the model loop (`packages/opencode/src/mcp/auth.ts:9-32`, `:37`, `:80`). Hermes does the
  same (`tools/mcp_oauth.py:388-412`). Neither has an intermediary that can re-authorise an individual MCP
  call after the token is minted.

Independently confirmed by the critic: `capabilities/mcp/` contains only `proxy_transport.py`; the only
token-shaped hits in the whole directory are a redaction key list and an enum value. No direct-dial vendor
transport exists.

> **Fragility to fix, not a refutation:** the entire plane is conditional on `_backend_proxy_endpoint` finding
> a `backend_url`, and `execution/factory.py:736` returns `None, None`. `factory.py:690-696` documents that
> exact seam having been **silently dead across all of production** once before. Add a fail-closed assertion.

### 1.2 The identity and tenancy control plane — not close

`services/backend/src/backend_app/identity/` is **37 modules / 17,654 LOC**: SAML (`saml.py`, `saml_store.py`,
`_saml_lib.py`), SCIM 2.0 provisioning with filter parsing (`scim.py`, `scim_filter.py`, `scim_serializer.py`,
`scim_store.py`, `provisioning.py`), MFA, RBAC, OIDC + JWKS, Google OAuth, SIWE, invitations, account lockout,
password store, session sweeping, account merge — with **1,014 `org_id`/tenant references**.

- **OpenCode's `packages/identity/` is six brand image files** (`mark.svg`, `mark-light.svg`, three PNGs) —
  no `package.json`, no source, zero references anywhere in the repo. `packages/opencode/src/auth/index.ts` is
  97 lines. Orgs exist only as remote types the CLI consumes from a hosted console (`account/account.ts:61-66`).
- **`packages/enterprise` is a share-link viewer**, not a control plane. Its `README.md:1-5` is the unedited
  `npm init solid` scaffold. Its entire authorization primitive is
  `if (share.secret !== body.secret) throw ...` (`src/core/share.ts:137,164,182`) against a
  `crypto.randomUUID()`. Grep for `workspaceID|teamID|orgID|tenant|accountID` in `enterprise/src/core/`
  returns nothing.
- Hermes has no identity layer at all — _"files under HERMES_HOME execute with the TUI's privileges."_

**We are also the only one of the three with an inbound SCIM 2.0 surface**
(`services/backend-facade/src/backend_facade/scim_routes.py:27`, `:34`). OpenCode's only "scim" matches are
substrings in Bosnian docs; Hermes' two are false positives in an i18n file and a release script.

### 1.3 The tool-result floor — this reversed since 2026-08-04

The prior doc scored this **Hermes ahead** ("ours says _No spec matched_"). **That is now false.** Rung 0 of
the surface ladder is a deterministic SurfaceSpec inferrer with an explicit totality contract — never raises,
returns `None` only for a non-Mapping input — so an uncurated connector still produces a table/record/doc
archetype with **zero model calls and zero config**.

- OURS: `capabilities/surfaces/infer.py:1-33` (totality + purity contract, 1,172 lines), wired as the last
  ladder rung at `capabilities/surfaces/projector.py:627`. Live-verified per `generative-ui/STATE.md:26-32`.
- **OpenCode's web/desktop fallback renders no output at all** for an unknown tool: `GenericTool`
  (`packages/session-ui/src/components/basic-tool.tsx:323-343`) emits a title, one label key and up to three
  scalar args — `grep -c output basic-tool.tsx` = **0**. Its TUI shows output but as untyped text truncated to
  three lines (`packages/tui/src/util/collapse-tool-output.ts:1-19`).
- Hermes' floor is prose bullets — better than OpenCode, still flat text with no table, columns or sort.

**We now have the best tool-result floor of the three.** That is a real reversal and the prior doc's headline
recommendation is closed.

### 1.4 Declarative model-authored UI — still unique

The model authors data + a SurfaceSpec, the client owns the component catalog, **no model-authored code ever
executes**. `packages/surface-renderers/src/archetypes/` (Board/Doc/Message/Record/Table) delivered as
`{spec, source, data}` on `surface.created` (`surfaces_v2/emitter.py:540-575`).

OpenCode has **zero** declarative or model-authored rendering — grep for `iframe|srcDoc|sandbox` across
`packages/{app,session-ui,ui,desktop}/src` returns only git-worktree "sandboxes". Every tool view is a
hand-written Solid component. Hermes' analogue is model-authored HTML/SVG in an iframe — a different and
riskier axis with no schema.

_Scope honestly:_ this holds **for the read lane**. `STATE.md` records the write lane's compose path
deliberately dark (`UNBOUNDED_OP` refusal), so "the model authors data + spec" is not uniformly true across
lanes.

### 1.5 Four smaller wins that held up

- **Tool→display mapping computed once, server-side.** `presentation/turn_parts.py` (438 lines) +
  `capabilities/operations/presentation.py:25-70`, the only producer in the service. OpenCode duplicates the
  tool→icon/title table **per client**: a 15-case `getToolInfo` in `session-ui/src/components/message-part.tsx:1484-1568`
  _and_ a parallel 15-arm `Switch` in `tui/src/routes/session/index.tsx:1745-1790`. Two clients, two
  hand-maintained tables, free to disagree.
- **Resumable event cursor.** Events carry a monotonic `sequence_no`; clients reconnect with
  `?after_sequence=N` (`runtime_api/http/routes.py:683-697`), with a replay-only endpoint on the same cursor
  (`:537-545`). OpenCode's own codegen contract states the rule plainly — _"Neither runtime reconnects
  automatically"_ — and `event.subscribe` (`packages/protocol/src/groups/event.ts:35`) takes no cursor.
  `after_sequence|lastEventId|Last-Event-ID|resumeFrom` returns **zero** across `packages/opencode/src`,
  `packages/protocol/src`, `packages/core/src`. Hermes: nothing on the agent event path.
- **JSONL canonical + disposable SQLite index.** `runtime_adapters/file/_catalog_index.py:191-201` — _"the
  index carries no canonical data — rebuild repopulates every row from the JSONL folders"_; stale-schema
  detection deliberately throws to force discard-and-recreate (`:168-181`). **OpenCode's SQLite tables ARE the
  sessions/messages/parts** (`packages/core/src/session/sql.ts:22,68,82`) with 38 forward-only Drizzle
  migrations and no rebuild-from-source path — the DB is the only copy. Same exposure on Hermes.
- **Machine-checked module boundaries.** `tools/check_service_boundaries.py:29-49` +
  `tools/check_llm_provider_imports.py` — 16 gate scripts, each paired with its own unit test, run
  unconditionally at `.github/workflows/ci-gates.yml:77-117`. OpenCode's `.oxlintrc.json` contains **no import
  rule of any kind**; a repo-wide grep for `no-restricted-imports|dependency-cruiser` returns zero hits across
  30 packages. Hermes has a 7-way cyclic import graph.
- **Cancellation as a durable queued command.** `runtime_worker/loop.py:167-176` reserves claim headroom above
  the execution semaphore so a cancel can still be claimed when every execution slot is full; `:338-339`
  splits cancellation across the run claim and the cancel claim. OpenCode cancels via in-process Effect
  interruption bound to an `AbortSignal` (`tool/task.ts:310-346`) — dies with the process. Same for Hermes.

### 1.6 Agent loop — the one dimension where renting the loop costs us

_First-hand reading only; this section did not pass through the critics. Weigh it accordingly._

**We rent the loop, they own it.** We call `create_deep_agent` exactly once
(`execution/deep_agent_builder.py:11`) — and, to our credit, that "exactly once" is machine-enforced
(`observability/llm_seam_conformance.py:213-216` fails the build if the count is not 1). OpenCode hand-writes
the whole thing: `session/processor.ts`, `retry.ts`, `compaction.ts`, `overflow.ts`, `revert.ts`,
`run-state.ts`, `summary.ts`, `status.ts`. Three concrete things that ownership buys them and we don't have:

**a) A real provider retry policy.** `session/retry.ts` (208 lines) classifies retryable failures with six
regex families (`429|500|502|503|504|524`, rate-limit phrasings, overload/service-unavailable,
network/socket/DNS, timeout, `resource_exhausted`), **honours `retry-after-ms` and `retry-after` including the
HTTP-date form**, and applies exponential backoff (2s initial, factor 2, 0.25 jitter, capped at 30s when the
provider sends no headers) up to 5 attempts, with typed upsell actions for free-tier limits.

Ours retries at the **run-claim** level instead: `runtime_worker/loop.py:1114`
(`if retryable and claim.attempts <= self.settings.execution.max_retries`, default 2). That is a different
and blunter thing — **a 429 twenty tool-calls into a turn re-runs the whole turn.** We do read `Retry-After`,
but only for _tool_ errors (`execution/tool_error_sanitizer.py:243-256`), never on the model path, and
`deep_agent_builder.py:510` passes only `{"timeout": ...}` to the model client — so per-call retry is whatever
the underlying LangChain default happens to be, not a policy we own or can tune.

**b) `recursion_limit` is still never set.** `grep -rn recursion_limit services/ai-backend/src` = **0**, ten
days after the prior audit flagged it `[verified]`. Every run inherits LangGraph's default of 25 super-steps —
a limit nobody chose, that no config surfaces, and that fails as an opaque graph error rather than a typed
budget message. **This is an `hours` fix and it has now survived two audits.**

**c) Compaction is ours-internal; theirs is a product surface.** We have real compaction
(`context/memory/summarization.py`, `token_budget.py`, `MAX_INPUT_TOKENS = 128_000`). OpenCode's is
configurable and inspectable: `session/overflow.ts:10-33` computes usable context as the model's input limit
minus a reserve (`min(20_000, maxOutputTokens)`), with user config for `compaction.auto`, `compaction.reserved`,
`compaction.preserve_recent_tokens`, `compaction.tail_turns` and `compaction.prune`; a dedicated `compaction`
agent; a plugin hook (`experimental.session.compacting`) that can inject context or replace the prompt; and —
the part worth stealing — **compaction is recorded as a `compaction` _part_ on the message**
(`session/compaction.ts:102`, `:311`), so it is replayable, visible in the transcript, and a boundary the
renderer can draw. Ours is invisible to the user.

**Where we hold up on this dimension:** per-call `timeout_seconds` is real, bounded (`le=600`,
`execution/contracts.py:197`) and **depth-scaled for subagents** (`execution/depth.py:112`) — a nicety neither
competitor has. Cancellation (§1.5) and stream resumption (§1.5) are both ours. No run-level wall-clock
deadline exists on any of the three.

---

## 2. What looked like a win and isn't — six refuted claims

Recorded because the pattern matters more than the individual claims.

| #   | Claim                                                                  | Why it failed                                                                                                                                                                                                                                                                                                                        |
| --- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | MCP descriptor filesystem is mounted **unconditionally**               | `execution/factory.py:922-926` declines the mount (`_McpCatalogDecline.PASSTHROUGH_MEMORY_BACKEND`) whenever the memory backend is a `DeepAgentsBackend` — **which is the desktop's own host-filesystem case**. Capability is real; "unconditional" is not.                                                                          |
| 2   | No agent-reachable host process execution                              | `capabilities/sandbox/policy_backend.py:95` is `def execute(self, command: str, ...)`, model-callable and wired at `execution/factory.py:1125-1129`, `:2517`. **Remote-not-host is a genuine distinction; "no agent-callable process execution at all" is not what the source says.**                                                |
| 3   | Approval is a multi-party workflow object (forward / suggest-edit)     | Only `approve_with_edits` is reachable on the product surface (`RunDestination.tsx:2157`). `forward_to` and `suggest_edit` have **zero** consumers in `packages/chat-surface` or `apps/desktop` — their only UI is the deprecated web app on the flag-OFF legacy path. Backend is wired; the product isn't.                          |
| 4   | Hash-verified, scope-bound conversation archive                        | Self-refuting: the same report's gap #3 says the archiver has _"zero callers… no route and no desktop menu item."_ An archiver no user can reach is not better than `opencode export` (`cli/cmd/export.ts:223`), a shipped CLI verb.                                                                                                 |
| 5   | Skill authorship is a scope-gated multi-tenant HTTP API                | `apps/desktop/renderer/destinationBinders.tsx:763` binds only `GET /v1/skills`; `:776-778` states Edit/New are omitted because the editor _"isn't built on desktop yet."_ The authoring path is unreachable from the product.                                                                                                        |
| 6   | The raw-payload floor is honest — Copy/Download always carry full text | `thread-canvas/ToolCallCard.tsx:373` caps payloads at `TOOL_PAYLOAD_CAP = 600` chars with **no full-text escape**; its own aria-label reads _"Select and copy the displayed content."_ And the competitor gap collapses: OpenCode's `session.copy` emits `part.state.output` **untruncated** (`tui/src/util/transcript.ts:103-104`). |

---

## 3. What we need to do better — six root causes, not 49 gaps

49 distinct gaps were raised. They collapse to six.

| #     | Root cause                                                                                              | Dimensions it appeared under                                             |
| ----- | ------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| **A** | **"Landed, not yet wired."** We ship the mechanism and skip the last seam.                              | tools-mcp, permissions, session-state, extensibility, delegation, client |
| **B** | **MCP identity is the vendor's raw string.** Their tool name is our key; their schema is trusted input. | tools-mcp (×3)                                                           |
| **C** | **No user-authorable surface.** Every extension point is a Python edit + redeploy.                      | extensibility (×7), permissions (×2)                                     |
| **D** | **No inbound protocol, no generated client.**                                                           | protocols (×4)                                                           |
| **E** | **No undo of agent file edits.**                                                                        | session-state                                                            |
| **F** | **Fake boundary is internal + zero per-PR e2e.** The _enforcement_ cause of A.                          | engineering (×2)                                                         |

**A is the dominant theme and it is measurable.** Each confirmed dark by grep during this audit:

- **FTS5 conversation search** — `runtime_adapters/file/_catalog_index.py:102`, `:525`; `runtime_api_store.py:1471`. Zero non-adapter callers.
- **Export/import archive** — `runtime_api_store.py:2809`, `:2874`. Callers only inside the adapter.
- **`grant_options`** (the once/always control) — emitted at `runtime_worker/stream_events.py:262-266`; the only app-code consumer is `apps/frontend/.../payloadHelpers.ts:128`, **a strip list**.
- **`approvals.expires_at`** — field declared, query written, sweeper built — **nothing populates it**, and `runtime_worker/__main__.py:465-466` defaults the sweeper to `False`.
- **`coordination.py`** — a 574-line delegation planner with depth/child/budget caps, sitting in `tests/unit/orphan_ratchet_baseline.txt:28`.
- **Skills `allowed_tools`** — parsed at `capabilities/skills/manifest.py:68`, consumed by exactly one f-string at `execution/factory.py:1792-1793`, **enforced nowhere**.
- **Desktop skill authoring** — `apps/desktop/renderer/destinationBinders.tsx:776-778` self-documents the omission.

> The repo already has a ledger for the _module_-level version of this (the orphan ratchet). It has none for
> the sub-module version: imported-but-only-from-a-test, wired-on-one-store-backend, interpolated-into-a-prompt-string.
> **That absence is also why two of the three claims the critic had to correct were wiring-state errors —
> nobody, including this audit, can tell wired from unwired by grepping imports.**

---

## 4. Top 8, ranked by impact × inverse effort

### 1. MCP vendor input-schema repair — `hours`

**Root cause B.** `capabilities/mcp/cards.py:697-698` raises when `type` is absent; `:704-706` raises over
16 KB. Both fire inside a Pydantic field validator on `McpToolDescriptor.input_schema` (`:369`, `:397`), so
**the failure mode is the tool silently vanishing**.
**Fix:** normalise-then-validate in `McpSchemaValidator.validate_json_schema` — coerce missing/`null` `type` to
`object` when `properties` is present, `definitions`→`$defs`, collapse nullable `anyOf`, prune orphan
`required`. Then _degrade_ over 16 KB (drop `examples`/`default`/long descriptions, re-measure) rather than
reject — the full schema is already readable via `capabilities/mcp/catalog.py`.
**Skip cost:** a user installs a real connector, some tools are simply absent, and the agent says it can't do
the thing. Both competitors repair this exact case; Hermes shipped theirs against a live bug report
(`tools/mcp_tool.py:5357-5369`), OpenCode lowers schemas per-model at `provider/transform.ts:1430-1552`.

### 2. MCP tool-name namespacing — `days`

**Root cause B.** `capabilities/mcp/tool_source.py:672-681` drops the second connector's tool with a typed
`DUPLICATE_DESCRIPTOR_NAME`. `grep mcp__ services/ai-backend/src` = **0**. The docstring at `:648-650` is the
asset: the collision check, the `tool_to_server` map and the registered name all read one `name` variable.
**Fix:** one `mcp__{slug}__{tool}` naming function at that single variable; strip the prefix at the two
presentation seams so the UI doesn't regress to "Mcp Linear List Issues".
**Files:** `capabilities/mcp/tool_source.py`, `execution/factory.py:641-647`, `presentation/display_metadata.py`
**Skip cost:** install Linear + Notion, both expose `search`, one is dead for the whole run.
_(OpenCode: `mcp/catalog.ts:119` `sanitize(clientName) + "_" + sanitize(name)`. Hermes: `mcp*tool.py:5515`.)*

### 3. Subagent depth limit + deny `task` to children — `hours`

**Root cause A.** The caps exist and are unreachable: `delegation/subagents/coordination.py:290 max_depth`,
`:52 DEPTH_LIMIT_EXCEEDED`, `:356-357` — orphan baseline line 28. `grep depth` over the live
`atlas_task_tool.py` = 0, and the child inherits the parent's tool surface.
**Fix:** stamp `delegation_depth` into `build_subagent_invocation_config` (`atlas_task_tool.py:374`), refuse
past 1; drop `task` from the child list unless its definition grants it. Prune baseline line 28 in the same
change, or delete `coordination.py`.
**Skip cost:** one prompt away from unbounded recursion on a BYOK key — **the user pays for it**. Both
competitors default to depth 1 (`opencode/.../tool/task.ts:105-117`, `hermes/tools/delegate_tool.py:127-133`).

### 4. Destructive floor under BYPASS on the connector lane — `hours`

**Root cause C-adjacent.** `capabilities/policy/service.py:275-278` — the BYPASS branch returns ALLOW above
every action check, so `Action.DESTRUCTIVE` never reaches §3.5. The only thing above it is a workspace BLOCK
(`:271-273`), which nobody authors on a single-user desktop — **which is the product**.
**Fix:** move the `Action.DESTRUCTIVE` test above the BYPASS branch and return `GATE`, not `DENY`. One conditional.
**Skip cost:** the composer's bypass pill silently pre-approves a class of action the user never saw a card
for. Note we already ship the correct asymmetry on the filesystem lane
(`capabilities/desktop/host_filesystem.py:46-48`: _"bypass removes the PAUSE, never widens the SET"_) — the
connector lane just never got it.

### 5. Populate `expires_at` + default the sweeper on — `hours`

**Root cause A, worst instance.** Half-built machinery reads as a working control: `runtime_api_store.py:2591-2594`
queries the field, `jobs/approval_expiry_sweeper.py:159` acts on it, **no creation site sets it**
(`stream_events.py:686-693`, `api/draft_service.py:526-541`), and `__main__.py:465-466` is `default=False`.
**Fix:** `expires_at = created_at + TTL` at the two creation sites, flip the default, make the synthetic
resolution distinguishable from a user reject on the wire.
**Skip cost:** abandoned runs park forever — and **a compliance reviewer finds the sweeper and cites it as
implemented.** That is exactly the failure mode `CLAUDE.md`'s compliance section warns about.

### 6. Wire FTS5 conversation search — `days`

**Root cause A.** `_catalog_index.py:102` creates the FTS5 table over titles _and_ message bodies, `search.py:16-20`
ranks by bm25, `runtime_api_store.py:1471` exposes it — no port method, no route, no caller.
**We are actually ahead of OpenCode here** (`session.ts:563` is `like(title, '%q%')`); Hermes is ahead of both
and exposes theirs to the agent as a recall tool.
**Fix:** declare on the persistence port, add `GET /v1/agent/conversations/search?q=`, bind in ⌘K and the chat list.
**Skip cost:** the most-reached-for feature in any chat product is missing **while we pay to maintain the index
on every write**.

### 7. One per-PR journey against the built app — `days`

**Root cause F — the gate that stops A recurring.** `grep -rln desktop-journeys .github/workflows/` = **zero
files**. `desktop-supervised-boot-drill.yml` is schedule+dispatch only, and its own header (`:7-10`) says it
exists because _"the AC2b worker-gate bug and the citation data-loss bug both shipped because 1,900+ unit
tests, typecheck, and review all passed while the real supervised path was never booted in CI."_
**Fix:** one PR-triggered job running exactly `tools/desktop-journeys/first_run.py` against the staged runtime
with `RUNTIME_FAKE_MODEL=1`. **One phase only** — since `e8622a1d` the phases share one boot, so a multi-phase
per-PR job inherits stale route/run-history state and fails with a symptom-shaped message.
**Skip cost:** every item in root cause A recurs. This is the only mechanical defence.

### 8. Skill authoring on the desktop host — `days`

**Root cause A + C.** `apps/desktop/renderer/destinationBinders.tsx:770-786` renders `SkillsDestination` with
only `items`/`onRunSkill`/`onRetry`; the props exist and the **deprecated** web host binds them
(`apps/frontend/src/features/skills/SkillsRoute.tsx:186-187`).
**Fix:** bind `onNewSkill`/`onEditSkill` to an editor sheet POSTing `/v1/skills`, mirroring the ProjectEditor
sheet already in that same file.
**Skip cost:** on the surface `CLAUDE.md` declares is the product, a user cannot author a skill, and the
library stays at 3 runtime `SKILL.md` packages. **The prior doc's recommended first move — conditional skill
visibility — is now the wrong order:** visibility filtering over 3 skills is nothing; the authoring path is
what caps it at 3.

### Addendum — three agent-loop items the critic never saw

The agent-loop dimension failed twice, so these were **not** in the critic's dedupe or ranking. Judged on the
same scale they would sit around #3-#6.

- **Set `recursion_limit`** — `hours`. Zero occurrences in `services/ai-backend/src`; every run silently
  inherits LangGraph's default 25 super-steps. Flagged `[verified]` on 2026-08-04 and untouched since.
- **Own the model-call retry policy** — `days`. Today a mid-turn provider 429 re-runs the entire turn
  (`runtime_worker/loop.py:1114`) instead of backing off on one call. Port the shape of
  `opencode/packages/opencode/src/session/retry.ts`: classify, honour `retry-after`/`retry-after-ms`, bound
  the backoff. Cheap and it directly reduces BYOK spend.
- **Make compaction visible** — `days`. Record it as a transcript part the way
  `opencode/.../session/compaction.ts:102,:311` does, so the user can see where history was summarised.

**Just outside, worth taking opportunistically:** enforce or delete skills `allowed_tools`
(`factory.py:1792-1793` — shipping an unenforced field with that name is worse than not having one, `hours`);
delete `playwright` from `tools/cli/package.json:24` (zero references anywhere else in `tools/cli/`, so every
`npm i -g @0x-copilot/cli` pulls it for nothing, `minutes`); message-list virtualization
(`packages/chat-surface` has zero virtualizer references, `days`).

---

## 4a. Scoped to the harness itself — the agent runtime, nothing else

§4b below ranks by product impact and drifts into platform concerns — SDK packaging, CI gates, client
virtualization, identity. **Those are not the harness.** This section is the same question asked strictly of
the agent runtime: `agent_runtime/` + `runtime_worker/` — the loop, model I/O, context, the tool layer, the
policy enforcement point, delegation, and the runtime's own extensibility. Everything here is inside that
boundary.

| #      | Missing from the harness                                                                                                                                                                                                                                                                                                    | Evidence                                                                                                                                                            |
| ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **1**  | **Loop control we own.** `recursion_limit` never set — every run inherits LangGraph's default 25 super-steps. No model-call retry policy: a mid-turn 429 re-runs the whole turn. No run-level wall-clock deadline.                                                                                                          | `grep recursion_limit …/src` = 0 · `runtime_worker/loop.py:1114` · `deep_agent_builder.py:510` passes only `timeout`                                                |
| **2**  | **Context economy.** Tool results enter context uncapped on every store backend except the file store. Compaction is silent, unconfigurable and leaves no transcript record. Skills have no progressive disclosure — a flat `enabled` boolean, so the library cannot grow without a linear token tax.                       | `file_store_wiring.py:63-70` → `tool_budget_guard.py:346-348` · `context/memory/summarization.py` · `VirtualSkillRegistry.list_available_skills`                    |
| **3**  | **MCP tool identity.** No `mcp__<server>__<tool>` namespacing, so two connectors exposing `search` means one is dropped for the whole run. Vendor input schemas are hard-rejected rather than repaired, so the tool silently vanishes.                                                                                      | `capabilities/mcp/tool_source.py:672-681` · `capabilities/mcp/cards.py:697-706`                                                                                     |
| **4**  | **Policy expressiveness at the tool boundary.** A three-value action axis (`read/write/destructive`). No `(key × pattern)` rules, no once/session/always, no user-authorable never-list. `grant_options` is emitted and no client renders it.                                                                               | `capabilities/policy/service.py` · `runtime_worker/stream_events.py:262-266` vs opencode `permission/index.ts:28-38`                                                |
| **5**  | **Delegation controls.** Depth/child/budget caps exist only in dead code; the live `task` tool has no depth stamp, the child inherits the parent's full tool surface **including `task`**, and subagent permission derivation is stated nowhere.                                                                            | `delegation/subagents/coordination.py:290,:52` in `orphan_ratchet_baseline.txt:28` · vs opencode `agent/subagent-permissions.ts` and `tool/task.ts:105-117`         |
| **6**  | **No middleware/hook seam.** Nothing can observe or modify a tool call, model request, prompt assembly, or policy decision without a Python edit and a redeploy.                                                                                                                                                            | opencode: 26 typed hooks published as an npm package · hermes: 24 hooks, 2,485-line manager                                                                         |
| **7**  | **Agent-as-configuration.** Custom subagents are file-only JSON with no write route, no UI and no discovery. A user cannot declare an agent with its own model, prompt and tool allowlist. Skills' `allowed_tools` is parsed, typed — and only interpolated into a prompt string, so it is advice to the model, not a gate. | `FileSubagentDefinitionProvider` · `capabilities/skills/manifest.py:68` → `execution/factory.py:1792-1793`                                                          |
| **8**  | **No reversibility of agent writes.** No snapshot, no pre-write capture, no revert — zero `git` invocations in `services/ai-backend/src` or `apps/desktop/main/`, and every `snapshot` hit under `capabilities/desktop/` is a permission _grant_ snapshot.                                                                  | vs opencode `snapshot/index.ts` (807 lines: `track`/`restore`/`revert`/`diff`) + `session/revert.ts`, revertable to a single **part** within a message              |
| **9**  | **No mid-run steering.** Nothing injects a reminder, correction or course-change into a turn already in flight. And a rejected permission returns a bare denial instead of carrying the user's reason back to the model.                                                                                                    | `grep reminder\|steer\|mid_run\|inject_message agent_runtime/` returns nothing relevant · vs opencode `session/reminders.ts` and its `CorrectedError({feedback})`   |
| **10** | **No batched or dependent tool execution.** The model emits one call, waits a full turn, and every intermediate result lands in context. Nothing can sequence, branch or fan out without a round-trip per hop.                                                                                                              | vs opencode `packages/codemode` (8.3k LOC). **Take the capability, not their in-process interpreter** — build it on our remote policy-wrapped `execute` seam (§6.1) |

**Where the harness is at parity or ahead, so it is not on this list:** prompt assembly is a real 3,332-line
subsystem with provider-cache handling (`agent_runtime/prompts/`) against opencode's 2,553 lines — they have
14 per-model prompt variants and a plan mode we lack, we have cache economics they lack, call it even.
Streaming is ours (resumable `?after_sequence=N`). Cancellation is ours (durable queued command). The
MCP trust boundary and the tool-result inference floor are both ours outright (§1.1, §1.3).

---

## 4b. Ranked by product impact — broader than the harness

§4 is a work queue: impact × inverse effort, which systematically buries anything costing `weeks` or `months`.
This is the other view — **what the product structurally cannot do, ordered by how much that costs, with build
time deliberately excluded.** Note that it ranges wider than §4a: items 3, 4 and 10 below are platform and
engineering-process concerns, not agent-runtime ones.

### 1 · We cannot undo anything the agent wrote to the user's disk — `weeks`

No snapshot, no pre-write capture, no revert. Verified: zero `git` invocations anywhere in
`apps/desktop/main/` or `services/ai-backend/src`, and every `snapshot` hit in `capabilities/desktop/` is a
permission **grant** snapshot, not file content.

OpenCode's `snapshot/index.ts` (807 lines) records a shadow-git tree hash per step as a `patch` part on each
assistant message, with `track` / `patch` / `restore` / `revert` / `diff` / `diffFull`, per-file
`git checkout <tree> -- <file>`, a redo path (`unrevert`), 7-day pruning — **and revert granularity down to a
single _part_ within a message, so a user can rewind one tool call rather than a whole turn**
(`session/revert.ts`).

This is the trust floor for any agent that writes to a real filesystem. Everything else on this list is a
capability we lack; this is one bad turn away from destroying user work with no recourse. It ranks first
because no amount of approval UX substitutes for being able to take it back.

### 2 · There is no seam where anyone can extend the runtime — `weeks`

No plugin system, no hook registry, no lifecycle events a third party can observe or modify. Not for tool
calls, model requests, system prompts, permission decisions, or session lifecycle. OpenCode ships **26 typed
lifecycle hooks as a versioned npm package** plus a 634-line TUI extension API; Hermes ships a 2,485-line
plugin manager with 24 hooks and a `plugin.yaml` contract. **Every extension point we have is a Python edit
and a redeploy.**

This is the ceiling on everything else: connectors, per-customer behaviour, and the entire "someone else
builds on us" category. It is also the substrate items 5 and 10 need.

### 3 · Nothing outside our own Electron app can drive a run — `weeks`

No inbound ACP, no MCP-server mode, no published SDK, no headless driver, no IDE presence, no CORS on the
facade. `packages/api-types` is a **6,529-line hand-maintained mirror** kept in sync by discipline. OpenCode
reflects one authoritative Effect HttpApi into CI-diff-gated Promise and Effect clients and builds every other
surface — ACP host, Slack, TUI, a five-editor extension — on top of that one generated client. Hermes speaks
ACP 0.9.0 and A2A v1.0 in both directions.

The generated-client half is the load-bearing part, not the protocol half: **without one source of truth, every
new surface re-pays the mirror cost.**

### 4 · Nothing detects "landed but not wired" — `weeks`

The single most-repeated finding in this audit (root cause A, 6 of 9 dimensions, 7 confirmed dark
capabilities), and the reason 3 of 6 refuted wins were refuted. We have an orphan ratchet for whole modules
and nothing for the sub-module cases that actually bite: imported-only-from-a-test, wired-on-one-store-backend,
interpolated-into-a-prompt-string. Compounding it: **zero per-PR e2e against the built app** and **zero
recorded provider-wire traffic** — every LLM test fakes the internal seam.

Without this, every other item on this list gets built and then silently fails to reach a user.

### 5 · Nothing is user-authorable — `weeks`

No config file at all — every knob is an env var read at process start. No user-authorable command or prompt
template. Custom subagents are file-only with no write route and no UI. Skill authoring is absent on the
desktop host, `write_skill` still has zero production callers, and there is no skill distribution channel
(3 `SKILL.md` packages ship, and they ship inside the service image). OpenCode makes agents, commands, skills
and permissions all declarable in config; Hermes goes further with a self-improving skill lifecycle
(~18.5k lines: a forked review agent that writes skills, a curator that ages and consolidates them).

### 6 · The permission model cannot express what users actually want — `weeks`

We key on a three-value action axis (`read|write|destructive`). There is no way to say "allow `npm run *`",
"deny `git push`", or "never touch `~/.ssh`". No once/session/always (the backend emits `grant_options` and
**no client renders it**; the one app-code reference is a strip list). No user-authorable never-list. OpenCode:
`(permission-key × glob/command-pattern) → ask|allow|deny`, `findLast` wins, replies are `once|always|reject`,
a rejection carries feedback back to the model as a typed `CorrectedError`, an `always` retroactively resolves
other pending asks in the same session, and a `deny` with pattern `*` removes the tool from the model's
surface entirely.

### 7 · MCP breaks on contact with real connectors — `hours` to `days`

Three mechanics, cheap individually, decisive together: no `mcp__<server>__<tool>` namespacing (two connectors
exposing `search` ⇒ one is dead for the whole run), hard-rejection instead of repair of vendor input schemas
(a schema missing `type` makes the tool silently vanish), and no uniform result cap outside the desktop store
backend. This is the only correctness item high on the list — it is here because it is the difference between
connectors working and connectors appearing broken.

### 8 · We do not own the agent loop — `days` to `weeks`

`recursion_limit` is never set (every run inherits LangGraph's default 25 super-steps). We own no model-call
retry policy, so a mid-turn 429 re-runs the entire turn. Compaction is invisible to the user. No run-level
wall-clock deadline exists. Each is individually small; together they are the difference between a loop we can
tune and one we inherit.

### 9 · Every dependent tool call costs a full model turn — `months`

No batching, no code-mode, no parallel fan-out budget: the model emits one tool call, waits, and every
intermediate result lands in context. OpenCode's `packages/codemode` (8.3k LOC) is the state of the art here.
**Take the capability, not their implementation** — a confined interpreter inside the agent process is exactly
the boundary our MCP trust story sells (§6.1). We already have a policy-wrapped remote `execute` seam
(`capabilities/sandbox/policy_backend.py:95`) that is the more defensible place to put it.

### 10 · There is no client outside a GUI, and the GUI does not virtualize — `weeks` to `months`

React-DOM only. Any non-GUI surface — CI, SSH, a remote box, a headless test — has no client at all; both
competitors ship a TUI. And in the GUI we have, every tool card, reasoning block and surface in a long run is
mounted simultaneously (`packages/chat-surface` has zero virtualizer references), with markdown parsing and
syntax highlighting on the main thread during streaming.

**What §4 keeps that this list drops:** `expires_at`, the BYPASS destructive floor, subagent depth caps and
FTS5 wiring are all high-value-per-hour, which is why they top the work queue — but none of them changes what
the product can be. Take both lists as what they are: §4 is what to do next week, §4b is what to decide this
quarter.

---

## 5. The two structural gaps that are strategy calls, not bug-queue items

**Neither should be ranked against the defects above. Both are your call.**

### No plugin/hook seam anywhere in OURS (root cause C)

OpenCode ships a **versioned, npm-published, typed plugin SDK with 26 lifecycle hooks** plus a 634-line TUI
extension API, config-declarable agents and commands, and a remote skill registry. Hermes ships a 2,485-line
plugin manager with 24 hooks and a `plugin.yaml` contract. **We have no seam where a third party or the user
can observe or modify a tool call, a model request, a system prompt, or a permission decision.** Every
extension point is a Python edit plus a redeploy. This is the single largest capability difference in the
audit and it is `weeks`, not `days`.

### No inbound protocol and no generated client (root cause D)

OpenCode reflects **one authoritative Effect HttpApi** into committed, CI-diff-gated Promise and Effect
clients, then builds every other surface — ACP host, Slack app, TUI, VS Code-family extension across five
editors — on top of that generated client. We hand-maintain a **6,529-line type mirror** (`packages/api-types`)
kept in sync by written discipline, publish no SDK, and have zero inbound agent-protocol adapters: **nothing
outside our own Electron app (or curl) can drive a run.** ACP is a 4-6 week distribution bet whose expensive
half is projecting 68 event names onto ACP's handful of `sessionUpdate` kinds.

### Parked deliberately: embedded PostgreSQL → SQLite

Real debt, evidence holds (`apps/desktop/main/services/postgres.ts:44,85-146` — the bundle ships no `psql`, so
we stage a Python interpreter to talk to our own database). But it is invisible to the user relative to
everything in §4, and it touches `services/backend`'s identity layer — **the one surface where we are
genuinely ahead of both competitors.** Park it.

---

## 6. Traps — do not copy these

1. **CodeMode / a confined interpreter** (`opencode/packages/codemode`, 8.3k LOC: a model writes a small JS
   program that can call only host-supplied tools, with no ambient filesystem/process/network authority).
   Tempting, but **we already have the better version of the disclosure half** — our `/mcp/<server>/tools/*.json`
   virtual filesystem is on by default where OpenCode's is behind `OPENCODE_EXPERIMENTAL_CODE_MODE`. The
   interpreter half is a new sandbox boundary _inside the agent process_, which is precisely the property our
   MCP trust story sells. Building it trades our one architectural win for a mechanism we have a cheaper
   substitute for.
2. **`--yolo` / auto-reply-`once`** (`cli/cmd/run.ts:274`, `:800-805`). Their global bypass is only safe
   because config `deny` short-circuits ahead of the ask. We have the better shape already — a posture that
   removes the pause but never widens the set. A yolo flag would import their weakness without their
   compensating control.
3. **The `tool.definition` plugin hook** (`opencode/packages/plugin/src/index.ts:222-335`). A plugin rewriting
   an existing tool's description and params _before the model sees them_ is a prompt-injection primitive with
   no audit trail. If we build a hook registry, ship `execute.before`/`execute.after` and skip
   definition-rewriting.
4. **Auth-optional network surfaces.** `cli/cmd/serve.ts:15-16` prints _"server is unsecured"_ when no password
   is set. When we add CORS to the facade (verified absent — zero `CORSMiddleware`/`allow_origins` hits in
   `services/backend-facade/src`), it must be default-deny and profile-gated, not opt-in.
5. **Provider cassettes without the redactor.** Recording real provider traffic is the right fix for our test
   fake boundary — but the load-bearing part is `opencode/packages/http-recorder/src/redaction.ts:5-37`, which
   scans live `process.env` for any var matching `/(API|AUTH|BEARER|…)/i` **whose value appears in the payload**.
   **This repository is public.** Cassettes without an equivalent env-value scan put provider keys in git.
6. **"Their MCP mechanics" wholesale.** Take namespacing and schema repair. Do **not** take the credential
   location that comes with them.

---

## 7. Corrections to the 2026-08-04 Hermes comparison

| Prior claim                                                               | Status now                                                                                                                          |
| ------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| "Tool-result floor: **Hermes** — ours says _No spec matched_"             | **FALSE now.** `capabilities/surfaces/infer.py` is a total inferrer; the `unwrapPayload` steal landed as `EnvelopeUnwrapper`.       |
| "Generative UI: ours is real but doesn't reach the screen"                | **Stale for the read lane** — live-verified per `STATE.md:10-13`. Write lane's compose path still deliberately dark.                |
| Steal #2 (port `unwrapPayload`) · Steal #3 (emit `arguments` in the gate) | **Both landed** (2026-08-04 and 2026-08-08 respectively).                                                                           |
| Steal #4 (namespace MCP tool names)                                       | **Still open.** `grep mcp__ services/ai-backend/src` = 0.                                                                           |
| "Ours is binary, so every repeated write re-prompts"                      | **Half-stale.** A per-connector `write_policy: ask_first\|allow_always` exists end-to-end; the per-grant once/always is still dark. |
| "Give the write gate an expiry (ours has none)"                           | **Conclusion holds, machinery description stale.** The sweeper exists; nothing populates `expires_at` and it defaults off.          |
| "A hardline floor beneath `Posture.BYPASS`"                               | **Half-done.** Filesystem lane has one; connector lane does not.                                                                    |
| "our tier-2 (dead)"                                                       | **Fixed** — `apps/desktop/renderer/bootstrap.tsx:110-113` now constructs the bridge with a real `workerFactory`.                    |
| "`write_skill` has zero production callers" · "no conditional visibility" | **Both re-verified, still true.**                                                                                                   |
| "ours ~10,000 Python test functions"                                      | Measured now: **7,894** under `services/ai-backend/tests`. The ~10k figure only holds across all three services.                    |
| Testing framed as a two-way split                                         | **Understated, not stale.** OpenCode also fakes the external system — _and_ runs 62 Playwright e2e specs on Linux+Windows per PR.   |

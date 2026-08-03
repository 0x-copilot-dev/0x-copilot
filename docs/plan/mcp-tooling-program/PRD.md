# MCP Tooling Program — follow-on PRD

**Status:** DRAFT for decision · **Base:** `84a67dc7` (P1b at HEAD, P2 Waves 0–2 + P2-7a + P2-8 shipped)
**Predecessor:** [`docs/plan/mcp-langchain-migration/PLAN.md`](../mcp-langchain-migration/PLAN.md) + [`P2-PLAN.md`](../mcp-langchain-migration/P2-PLAN.md)
**Owner:** ai-backend · **Secondary surfaces:** `services/backend`, `apps/desktop`, `tools/desktop-journeys`, CI

---

## 0. What already landed, and what it did not fix

The migration delivered: **P0** contracts (`ToolSource` / `CredentialProvider` / `ToolMiddleware` /
`MIDDLEWARE_ORDER`), **P1a** `PdpPolicyService` (the Move-1 action×trust×posture matrix, pure +
tested), **P1b** the PDP wired into MCP dispatch with a live-verified interrupt **GATE**, **P2-1**
foundation (deps, `McpServerConnectionConfig`, `ToolConnectorResolver`), **P2 Wave 1** the
`McpToolSource` + annotations bridge, **P2 Wave 2** the five middleware stages + `compose()`,
**P2-7a** the desktop broker credential seam, and **P2-8** the per-tool registration flip behind
`MCP_PER_TOOL_ENABLED` (default **OFF**).

That work made the **decision** correct. It did not make the **catalog** usable, and it left four
loose ends plus three unrelated defects found alongside. This PRD scopes exactly those.

**The gap that matters:** a live desktop run with a real Linear connector reached the point where
policy, transport, and identity all worked — and the run still produced **nothing**, because the
model could not read the connector's own tool list. That is a context-engineering failure, not a
protocol failure, and no amount of further migration fixes it.

### The program at a glance

> **Status, 2026-08-03.** Every item below is resolved. Three shipped, three were
> closed on evidence rather than built, and each closure is recorded where the
> work would otherwise have been picked up later. The pattern across all of them:
> the plan's premise was checked against source before execution, and three times
> the source disagreed.
>
> | #   | Item                        | Outcome                                                                                                                                                                                                    |
> | --- | --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
> | 1   | MCP filesystem catalog      | **Shipped**, real files, verified live against Linear                                                                                                                                                      |
> | 2   | Backend scoped-token mint   | **Shipped** (`d472c80b`)                                                                                                                                                                                   |
> | 3   | Retire desktop broker route | **Deleted** (`15814fc1`) — 531 src + 607 test lines                                                                                                                                                        |
> | 4   | Hyperparameters JSON        | **Shipped**; `execution` + `search` are real sources, the rest is a mirror pinned by test                                                                                                                  |
> | 5   | deepagents 0.7.1            | **Evaluated, still not upgraded** — but its blocker is fixed: the `/workspace` guard now covers the async surface and `delete`, with a conformance test that fails when the base gains an unguarded method |
> | 6   | Anthropic Tool Search       | **Closed as framed** — connector tools are files behind a backend mount, so nothing is resident to defer                                                                                                   |
> | 7   | Per-tool approval-id        | **Verified by mutation** — collapsing the per-call suffix fails 3 tests                                                                                                                                    |
> | 8   | Three independent bugs      | **All fixed and verified live**                                                                                                                                                                            |

| #     | Item                                            | Kind        | Why now                                                                     | Gate                         |
| ----- | ----------------------------------------------- | ----------- | --------------------------------------------------------------------------- | ---------------------------- |
| **1** | MCP filesystem catalog (progressive disclosure) | **P0**      | A live journey failed with EMPTY SUCCESS; the descriptor is unreachable     | none — ship first            |
| **2** | Backend scoped-token mint endpoint              | P1          | Credentials must stay in `services/backend`; the keychain premise was false | needs §3 decision            |
| **3** | Retire the desktop broker credential route      | P2          | Superseded by 2; two credential paths is one too many                       | **after 2 lands**            |
| **4** | Hyperparameters consolidated into one JSON      | P3          | Tunables are scattered through env config with the wrong lifecycle          | none                         |
| **5** | deepagents `0.6.12 → 0.7.1` evaluation          | P3          | We pin an old minor; upstream moves fast on exactly our surfaces            | none (timeboxed)             |
| **6** | Anthropic Tool Search (`defer_loading`)         | P4          | A provider accelerant **on top of** item 1 — never the primary mechanism    | **after 1 ships**            |
| **7** | Per-tool approval-id uniqueness                 | Conditional | Only meaningful if `MCP_PER_TOOL_ENABLED` ever ships                        | **only if 1 keeps per-tool** |
| **8** | Three independent bugs                          | Anytime     | Each is a real, today-live defect with a bounded fix                        | none — parallel              |

---

## 1. P0 — MCP filesystem catalog (progressive disclosure)

### 1.1 Problem statement (from the live run, not a hypothesis)

A real Linear connector descriptor arrived as **70,465 bytes, 52 tools, and ZERO newlines** — one
JSON line. What happened next was correct at every step and useless in aggregate:

1. The result exceeded the admission budget, so `ContextPayloadManager.prepare_tool_output`
   offloaded it to the content-addressed store and handed the model a **preview + `output_ref`**
   (`/large_tool_results/<sha256>`).
2. The preview is `"\n".join(content.splitlines()[:10])[:2000]`
   ([`context/memory/summarization.py:134-135,224-225`](../../../services/ai-backend/src/agent_runtime/context/memory/summarization.py)).
   With zero newlines the 10-line bound is **inert** — the model got exactly the first 2000 chars,
   which is the middle of tool #1.
3. The agent did the right thing: it called `read_file` on the `output_ref` at offsets **1, 221,
   481** — and received the **identical first 2000 chars** every time.
4. It then called `grep` on the reference and got **zero matches**.
5. Unable to reach `list_issues`, the agent **correctly refused to invent results**. The journey
   passed its transport assertions and produced no answer: **EMPTY SUCCESS**.

The blob is unreachable **four independent ways**, so no prompt change and no retry policy rescues it:

| #   | Mechanism                                                                                                                                                                | Evidence                                                                                                                                          |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| A   | The model-visible preview is line-**and**-char bounded; zero newlines collapses it to a flat head-truncation                                                             | `summarization.py:134-135`                                                                                                                        |
| B   | `FileLargeToolResultBackend.read/aread` accept `offset`/`limit` and **discard both** — `_read(file_path)` takes only the path, so every read returns the same whole blob | [`runtime_adapters/file/large_tool_result_backend.py:52-70`](../../../services/ai-backend/src/runtime_adapters/file/large_tool_result_backend.py) |
| C   | Even if honoured, deepagents' `read_file` `offset`/`limit` are **source lines** (`DEFAULT_READ_LIMIT = 100`); a zero-newline file is **one** line                        | `deepagents/middleware/filesystem.py:350-355`                                                                                                     |
| D   | `FileLargeToolResultBackend.grep/agrep` return `GrepResult(matches=[])` **unconditionally** — "blobs are addressed by digest, not searched"                              | `large_tool_result_backend.py:130-149`                                                                                                            |

**Therefore: stop producing the blob.** Patching B or D would make a 70 KB single-line JSON document
_navigable_; it would not make it _cheap_. The right primitive is the one deepagents already ships
for Skills.

### 1.2 The fix — materialize the catalog as files

Materialize each connected server into the agent filesystem at load time, and let the model discover
by `ls` → `grep` → `read` of one small file:

```
/mcp/<server>/SERVER.md              # ~1 KB: what this server is, auth state, tool INDEX
                                     #        (name + one-line summary + action class), pointers
/mcp/<server>/tools/<tool>.json      # one tool: full input schema, annotations, examples
/mcp/<server>/resources/...          # MCP resources, one file each
```

`SERVER.md` is the always-loaded tier (the Skills `SKILL.md` analogue); everything else is loaded
**only when the model reaches for it**. Finding `list_issues` becomes
`grep "issue" /mcp/linear/` → `read_file /mcp/linear/tools/list_issues.json` — two small calls
instead of one unreadable 70 KB reference.

**Why this shape, specifically:**

- It **implements [langchain-ai/deepagents#616](https://github.com/langchain-ai/deepagents/issues/616)**
  (open, unimplemented upstream) inside our runtime. We do not wait for upstream, and if upstream
  lands it we converge on the same artifact layout rather than a private one.
- It **mirrors the progressive-disclosure pattern deepagents already ships for Skills**
  (`deepagents/middleware/skills.py` — `SKILL.md` + frontmatter + on-demand bundle files). Same
  mental model, same tools, no new primitive for the model to learn.
- It is a **context-budget fix, not a transport fix.** 52 tools × full schema is not something to
  shrink; it is something to **not send**.

### 1.3 Provider-agnostic — non-negotiable

The live default model on the failing run was **GPT-5.4 mini**. The catalog must work identically on
OpenAI, Anthropic, Gemini, OpenRouter, and local Ollama models, because it is built out of
primitives every model already has: a filesystem, `ls`, `grep`, `read_file`. No provider-specific
tool-definition feature may appear on the primary path. (Item 6 layers one on top, gated.)

### 1.4 Acceptance criteria

- **AC1 — no blob.** After a connector loads, no MCP descriptor ever reaches
  `ContextPayloadManager.prepare_tool_output`. Assert: a 52-tool fake server produces **zero**
  `/large_tool_results/` references for descriptor content.
- **AC2 — always-loaded tier is bounded.** `SERVER.md` for a 52-tool server is **≤ 4 KB** and lists
  every tool name with a one-line summary and its action class (READ/WRITE/DESTRUCTIVE, from the
  P1b annotations bridge — the descriptor source stays the single derivation).
- **AC3 — reachable by the model's own tools.** `ls /mcp/`, `ls /mcp/<server>/tools/`,
  `grep <term> /mcp/<server>/` and `read_file /mcp/<server>/tools/<tool>.json` each return correct,
  non-empty, **line-oriented** content. Every emitted file is newline-delimited; a single-line file
  is a test failure.
- **AC4 — the failing journey passes.** A live Linear journey asks a question requiring
  `list_issues`, the agent finds and calls it, and the run returns a real answer. EMPTY SUCCESS is a
  test failure, not a pass. (Extends `tools/desktop-journeys/filesystem-access/` per the migration's
  §8 live-journey line.)
- **AC5 — provider-agnostic.** The hermetic catalog test runs against at least two provider adapters
  (one OpenAI-shaped, one Anthropic-shaped) with **no** provider branch on the catalog path.
- **AC6 — policy unchanged.** Reading `/mcp/**` is a READ of an ai-backend-authored artifact and
  never bypasses the PDP: calling a tool still routes through the P1b decision + GATE. Assert a
  WRITE discovered via the catalog still parks.
- **AC7 — no secrets in artifacts.** Tokens, `Authorization` headers, and connection URLs never
  appear in `/mcp/**`. Connection material stays on the credential plane (P2-PLAN §1).

### 1.5 Non-goals

Rewriting the offload store; making `FileLargeToolResultBackend` searchable; changing the admission
budget. Those become **much** less load-bearing once descriptors stop flowing through them, and
mechanism B/D remain latent bugs for other large results — file them, do not fix them here.

---

## 2. P1 — Backend scoped-token mint endpoint

### 2.1 Why (and the premise that turned out to be false)

P2-PLAN §1 decided the desktop credential path as "broker-mediated keychain read", on the premise
that the MCP OAuth token lives in Electron `safeStorage` behind `SecretStorage`'s active-workspace
gate. **That premise is false.** Two findings, both verified at HEAD:

- **Nothing ever writes a `SecretStorage` record of kind `"mcp"`.** The kind exists
  (`apps/desktop/main/auth/secret-storage.ts:5,26`) and P2-7a's broker route reads it
  (`apps/desktop/main/capabilities/broker.ts:136,160`), but the only production writers of
  `SecretStorage` are in `apps/desktop/main/auth/index.ts`, all under `BACKEND_KIND`. The `"mcp"`
  partition is written **only by tests**.
- **`oauth-coordinator.ts` says so explicitly:** _"Provider tokens never cross into main: the
  callback response carries only safe connection metadata"_
  ([`apps/desktop/main/connectors/oauth-coordinator.ts:25-27`](../../../apps/desktop/main/connectors/oauth-coordinator.ts)).
  Main brokers the browser handoff and posts `{oauth_session_id, state, code}` to the facade; the
  code→token exchange and the token itself stay in `services/backend`'s `TokenVault`.

So the token was never in the keychain to read. The correct seam is the one P2-PLAN §1 already
described as **GATED** — and it is now the seam for **both** deployments, not just web.

### 2.2 The change

One new backend endpoint (contract as drafted in P2-PLAN §1, unchanged):

```
POST /internal/v1/mcp/servers/{server_id}/access-token
  → { url, transport, access_token, expires_at, scopes }
```

Runs the **same** access-mode gate and `_require_valid_token` refresh the proxy runs today; issues a
per-`(tenant, user, server)` **short-TTL access token**, never the stored refresh token. Tenant
isolation and encryption-at-rest stay **inside** `services/backend`. ai-backend receives only a
narrow expiring bearer, held in `RefreshingBearerAuth` (`SecretStr`, `repr`-suppressed, never
persisted, never logged) — exactly the P2-2 component already shipped.

**Why this is the right boundary:** it preserves the repo's hard rule that `services/backend` owns
OAuth/token state, it needs **no** new trust boundary in the desktop main process, and it makes
desktop and web use **one** credential path instead of two.

### 2.3 Acceptance criteria

- **AC1** — the endpoint refuses without service-token headers **and** without
  `x-enterprise-org-id` / `x-enterprise-user-id`; caller-supplied identity is never trusted.
- **AC2** — a token minted for `(tenant A, server S)` cannot read `(tenant B, server S)`; a
  cross-tenant test asserts the refusal.
- **AC3** — the minted token is short-TTL and **is not** the stored refresh token (assert by value
  inequality against the vault record).
- **AC4** — the response never appears in logs, audit rows, or error bodies; only `key_hint`-style
  metadata may surface.
- **AC5** — `BackendScopedTokenCredentialProvider` (P2-7b, already specced) drives a real MCP
  connect on both `single_user_desktop` and the multi-tenant profile via one code path.
- **AC6** — profile selection stays in `runtime_adapters/factory.py`, mirroring existing store
  selection; no new global fork.

### 2.4 Sequencing

Ships **after** item 1 (which is user-visible and unblocked) but **before** item 3. It is the only
item here that touches `services/backend`, so it carries its own service-boundary doc update.

---

## 3. P2 — Retire the superseded desktop broker credential route

**Why:** once item 2 lands, `POST /mcp/secret` on the desktop capability broker
(`apps/desktop/main/capabilities/broker.ts`) reads a `SecretStorage` partition that nothing writes,
for a credential path nothing uses. Keeping it is a live, authenticated, main-process route with no
consumer — pure attack surface and a permanent source of "which path is real?" confusion.

**Acceptance criteria**

- **AC1** — the `/mcp/secret` route, `MCP_SECRET_KIND`, and `DesktopKeychainCredentialProvider` are
  deleted, along with `broker-mcp-secret.test.ts`.
- **AC2** — `ServerKind` drops `"mcp"` in `auth/secret-storage.ts` **and** `auth/audit-log.ts`, or
  a comment records why the audit enum must keep it for historical rows.
- **AC3** — a desktop journey completes a real MCP tool call with the broker route gone.
- **AC4** — grep-assert: no remaining import or reference to the deleted symbols.

**Sequencing:** strictly **after item 2 is live-validated on desktop**. Deleting first would leave
desktop with no credential path at all. This is deliberately its own increment so a regression in 2
never forces resurrecting deleted files — the same discipline as P2-9.

---

## 4. P3 — Hyperparameters consolidated into one JSON

**Why:** agent behaviour tunables are today scattered as `Field(...)` defaults across the 773-line
`agent_runtime/settings.py` (`max_retries`, `max_parallel_subagents`, `tool_call_budget`,
`delta_coalesce_*`, `default_timeout_seconds`, retry backoff constants in `RetryingTool`, preview
bounds in `ContextPayloadManager`, …). They are mixed in with genuine **environment configuration**
(store backend, DSNs, secrets, deployment profile), and the two have **different lifecycles**:

| Axis        | RuntimeSettings (env config)  | Hyperparameters (JSON)           |
| ----------- | ----------------------------- | -------------------------------- |
| Source      | environment / secrets manager | a checked-in JSON document       |
| Changes on  | deploy / host / tenant        | experiment / model / eval result |
| Reviewed by | ops, security                 | the person tuning the agent      |
| Safe to log | mostly not                    | always                           |
| Diffable    | no (secrets)                  | yes — this is the point          |

Fusing them means every tuning experiment looks like a config change and cannot be diffed, reviewed,
or A/B'd. **Decision: keep them separate.** One `hyperparameters.json` with a Pydantic model, loaded
once, injected; `RuntimeSettings` keeps only environment concerns.

**Acceptance criteria**

- **AC1** — one JSON document + one `extra="forbid"` Pydantic model; unknown keys fail loudly at
  boot, not silently at first use.
- **AC2** — **zero** hyperparameters remain readable from the environment; `RuntimeSettings` retains
  no behavioural tunable. Grep-assert both directions.
- **AC3** — the document is fully loggable (no secret may be added to it — enforced by a test that
  scans for secret-shaped keys).
- **AC4** — defaults are byte-identical to today's; this refactor changes **no** behaviour, and a
  before/after snapshot test proves it.
- **AC5** — every consumer takes the model by injection; no module-level singleton read.

**Sequencing:** independent; can run in parallel with everything. Best landed **before** item 5, so
the deepagents upgrade has one place to record any tunable that moves.

---

## 5. P3 — deepagents `0.6.12 → 0.7.1` evaluation

**Why:** `services/ai-backend/requirements.txt:18` pins `deepagents==0.6.12`. Upstream iterates
directly on the surfaces this program depends on — `middleware/filesystem.py` (read/grep semantics,
the exact mechanism behind item 1), `middleware/skills.py` (the progressive-disclosure pattern we are
mirroring), `backends/composite.py` (how `/mcp/**` and `/large_tool_results/**` route), and
`interrupt_on` (the P2-8 GATE interaction). Drifting further makes every one of those integrations
harder to reconcile, and issue #616 may land upstream in this window.

This is an **evaluation with a written verdict**, not an automatic upgrade.

**Acceptance criteria**

- **AC1** — a changelog diff `0.6.12 → 0.7.1` classified into: breaking for us / behaviour-changing
  for us / irrelevant, with file-level evidence for the first two.
- **AC2** — explicit answers for the four contact points above, plus whether #616 landed upstream and
  whether its layout matches item 1's.
- **AC3** — the full ai-backend suite run against `0.7.1` in a scratch venv; every failure
  attributed.
- **AC4** — a one-page verdict: **upgrade now / upgrade after item 1 / hold**, with the reason.
- **AC5** — if upgrading: a single commit that bumps the pin and fixes fallout, no behaviour riders.

**Sequencing:** timeboxed, independent. Do **not** block item 1 on it — item 1 must work on the
pinned version, or it is not shippable.

---

## 6. P4 — Anthropic Tool Search (`defer_loading`) as a provider-gated accelerant

**Why:** Anthropic's Tool Search / `defer_loading` lets the provider withhold full tool definitions
from the prompt until the model searches for them — the same economics as item 1, done in the
provider instead of the filesystem. On Anthropic models it is strictly cheaper: fewer round-trips,
no filesystem calls.

**It is explicitly not the primary mechanism.** Two reasons, both decisive:

1. **It is one provider.** The live default is GPT-5.4 mini, and the product ships OpenAI,
   Anthropic, Gemini, OpenRouter, and local Ollama models. A provider-only fix leaves the observed
   failure unfixed for most users.
2. **Layering is free; substituting is not.** Tool Search consumes **the same catalog artifacts**
   item 1 produces — the per-tool JSON files are the deferred definitions. So it is an adapter over
   an existing source of truth, and turning it off degrades to the filesystem path rather than
   breaking.

**Acceptance criteria**

- **AC1** — enabled only when the resolved provider advertises it; every other provider takes the
  item-1 path unchanged.
- **AC2** — **one** source of truth: the deferred definitions are generated from the same catalog
  artifacts, not a second derivation. A test asserts byte-equality of the tool schema on both paths.
- **AC3** — flag OFF (or unsupported provider) is byte-identical to the item-1 path.
- **AC4** — the P1b PDP + GATE still run on every call discovered via Tool Search; a WRITE parks
  identically. Provider-side discovery must not become a policy bypass.
- **AC5** — a measured token/latency delta on the 52-tool Linear server, both paths, same prompt.

**Sequencing:** strictly **after item 1 ships and is live-verified**. Building it first would make
the provider-specific path the reference implementation — exactly the inversion to avoid.

---

## 7. Conditional — per-tool approval-id uniqueness

**Only if `MCP_PER_TOOL_ENABLED` ever ships.** Today the flag defaults **OFF** and the legacy
`call_mcp_tool` gateway is the live path.

**The residual risk.** `PolicyToolMiddleware._approval_id` keys on `(run_id, tool_call_id)` with a
fallback to the **tool name**
([`middleware/policy_tool.py:448-475`](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/policy_tool.py)).
The docstring already states the hazard precisely: two writes sharing an id means the second park
finds the first approval resolved, no new pending approval is created (both are idempotent on the
id), no resume is enqueued, and **the run parks forever** — the exact hang this migration exists to
remove. The `tool_call_id` path is safe; the **name fallback** is not, if it can ever be reached with
two same-name writes in one run. Note the sibling `ToolAccessGate._approval_id` is keyed on
`(run_id, server_id)` (`surfaces_v2/gate.py:273-279`) — correct for OAuth-connect, wrong for
per-write, and the two must not converge by accident.

**Acceptance criteria (if triggered)**

- **AC1** — enumerate every path that can invoke a wrapped tool without a `tool_call_id` in
  production; if none exists, make the fallback **raise** instead of guessing.
- **AC2** — a hermetic test drives two writes to the same tool in one run and asserts two distinct
  approvals, two cards, and two resumes.
- **AC3** — a fuzz/property test asserts id uniqueness across (run, tool, call) triples.

### The open architectural question — decide before building this

**The catalog may retire per-tool registration entirely.** Item 1's premise is that the model
_discovers_ tools by reading files rather than by having N tool definitions in its prompt. If that
holds, the reason per-tool registration existed — making 52 tools individually visible — evaporates,
and the **existing `call_mcp_tool` gateway** becomes the better dispatch surface: it is the path that
already carries the **live-verified P1b PDP and interrupt GATE**, it keeps one approval seam instead
of N, and P2-8 documented real friction in the per-tool world (the descriptor-driven `interrupt_on`
map can raise **two** approvals for one write where both Deep Agents' pre-tool interrupt and the
POLICY stage fire).

Three candidate end-states:

| End-state                         | Registration                | Consequence                                                                              |
| --------------------------------- | --------------------------- | ---------------------------------------------------------------------------------------- |
| **A — catalog + gateway**         | one `call_mcp_tool`         | Item 7 is **moot**; P2-9's deletions invert (delete the per-tool flip, keep the gateway) |
| **B — catalog + per-tool**        | N tools                     | Item 7 is **required**; the double-approval interaction needs a real answer              |
| **C — catalog + gateway + defer** | gateway, Anthropic deferred | Item 6 layers on the gateway; per-tool never ships                                       |

**Do not resolve this on paper.** Build item 1 against the **current** default (gateway, flag OFF),
measure discovery quality on the live Linear server, and let the measurement pick A, B, or C. Item 7
is scoped and costed here so that choosing B is not a surprise.

---

## 8. Independent bugs (parallel, no dependencies)

### 8.1 `RetryingTool` drops `response_format` — web_search artifacts are stringified on every call

**Live today, not hypothetical.** `_web_search_tool()` builds a `StructuredTool` with
`response_format="content_and_artifact"` — so its `_arun` returns a `(content, artifact)` tuple —
then wraps it in `RetryingTool(name=…, description=…, args_schema=…, inner=inner, …)` **without
`response_format`**
([`runtime_worker/dependencies.py:132-149`](../../../services/ai-backend/src/runtime_worker/dependencies.py)).
The wrapper therefore defaults to `"content"`, and LangChain reads `response_format` from the
**outermost** tool when turning the return value into a `ToolMessage` — so the model receives the
`repr` of the whole tuple instead of the content, on **every** web search.

This is exactly the failure `ToolSchemaIdentity` was written to prevent — its docstring names
`response_format` as _"the load-bearing one"_ and calls the defaulted-back case _"a silent,
per-connector content corruption that no schema check would catch"_
([`mcp/middleware/compose.py:76-84`](../../../services/ai-backend/src/agent_runtime/capabilities/mcp/middleware/compose.py)).
The MCP stack obeys that rule; this pre-existing wrapper predates it.

**Acceptance criteria:** `RetryingTool` propagates the full `DISPATCH_SURFACE`
(`response_format`, `return_direct`, `metadata`, `tags`) from `inner` — ideally by reusing
`ToolSchemaIdentity.fields_of` rather than adding a second rule. A regression test asserts a
`content_and_artifact` inner survives the wrap and that the artifact is not stringified. Sweep for
any other `DelegatingTool` subclass constructed with a hand-listed field set.

### 8.2 `tools/desktop-journeys/README.md` implies OAuth can be completed through the driver

`driver.mjs` intercepts `shell.openExternal` in the Electron main process, records the URL, and
**returns without opening it** — _"suppress the OS-browser open; the test drives Chrome itself"_
([`tools/cli-testing/harness/driver.mjs:149-162`](../../../tools/cli-testing/harness/driver.mjs)).
No system browser opens, so **no vendor consent can complete through the driver**. The README's
driver-API section advertises `openedUrls` alongside the full journey framing without stating that
limit; only `connectors/JOURNEYS.md` records it honestly (CN-07 and CN-08 are marked **manual**, and
"completing a real vendor OAuth" is explicitly out of scope). An agent reading the README first will
burn a cycle trying to automate a connect that cannot work.

**Acceptance criteria:** the README states the suppression, names `openExternal` and `openedUrls`
(what the harness _can_ assert: that the right URL was requested), points at `connectors/JOURNEYS.md`
for what is manual, and describes `openExternalReal` (`driver.mjs:366`) as the deliberate escape
hatch. No code change.

### 8.3 ai-backend lacks the `requirements.in` + `--require-hashes` gate its siblings have

`services/backend` and `services/backend-facade` both ship `requirements.in` → `pip-compile
--generate-hashes`, a CI reproducibility check, and `--require-hashes` installs in CI **and** their
Dockerfiles. **ai-backend — the largest dependency surface in the repo (the LangChain / LangGraph /
deepagents stack, and now `mcp` + `langchain-mcp-adapters`) — has none of it**: no `requirements.in`,
no hashes, plain `pip install -r` in both `.github/workflows/ci-ai-backend.yml:46-52` and
`services/ai-backend/Dockerfile:16-19`. This is already recorded as finding **F5** in
`docs/audit/clusters/16-build-deploy.md:164` and as a known gap in
`docs/ci-cd/ci-assurance-spec.md:33,60` — it has simply never been closed, and every dependency this
program adds widens it.

**Acceptance criteria:** `services/ai-backend/requirements.in` exists; `requirements.txt` is
regenerated by `pip-compile --generate-hashes` with **no version drift** from today's pins (assert
the resolved set is unchanged); CI installs with `--require-hashes` and verifies pip-compile
reproducibility, matching `ci-backend.yml:47`; the Dockerfile and
`tools/desktop-runtime/stage.mjs` install ai-backend with `--require-hashes` like the other two
services; `docs/ci-cd/ci-assurance-spec.md` moves the row from "pending" to covered.

---

## 9. Sequencing

```
  item 1  MCP FILESYSTEM CATALOG          [P0 — ship first, unblocks 6 and decides 7]
     │
     ├─▶ item 6  Tool Search (defer_loading)      [only after 1 is live-verified]
     └─▶ item 7  per-tool approval-id             [ONLY if measurement picks end-state B]

  item 2  backend mint endpoint  ──▶  item 3  retire desktop broker route
                                       (strictly after 2 is live-validated)

  item 4  hyperparameters JSON  ──▶  item 5  deepagents 0.7.1 evaluation   [both independent]

  item 8a/8b/8c  independent bugs                [parallel, any time, no dependencies]
```

Critical path: **1 → (measure) → 6 or 7**. The credential lane (**2 → 3**) runs in parallel and is
the only lane touching `services/backend`. Items 4, 5, 8a–8c are fill-in work with no ordering
constraint beyond 4-before-5.

---

## 10. Open decisions

- [ ] **Catalog vs. per-tool end-state (A / B / C).** Decided by measurement after item 1, not on
      paper. Determines whether item 7 exists and whether P2-9's deletion list inverts.
- [ ] **`/mcp/**` write policy.\*\* The catalog is ai-backend-authored; confirm the backend is
      read-only to the model (recommended) rather than a writable state-backed path.
- [ ] **Catalog freshness.** Rebuild on every connector load (simple, always correct) vs. cache
      keyed on the descriptor revision the existing `revision_feed`/`freshness` control plane already
      tracks. Recommend rebuild-on-load first; optimize only if load latency shows up.
- [ ] **Resources tier.** Whether `/mcp/<server>/resources/` ships with item 1 or waits for the
      migration's P4. Recommend the directory exists and is empty in item 1, so the layout is stable.
- [ ] **Item 2 scope.** Does the mint endpoint replace the proxy for **both** deployments at once
      (enabling P2-9's backend `mcp_transport.py` / `mcp_session_pool.py` deletion), or desktop
      first? Recommend both — one path is the whole point of the finding.

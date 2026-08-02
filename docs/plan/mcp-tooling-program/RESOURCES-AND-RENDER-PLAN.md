# P4 — MCP Resources & Prompts · Render ≠ Approve

**Status:** DRAFT for decision · **Phase:** program P4
**Base read at:** `833e7d25` (P2-8 at HEAD; catalog + credentials work uncommitted in-tree)
**Owner:** ai-backend · **Secondary:** `services/backend`, `packages/chat-surface`, `packages/api-types`
**Parents:** [`PRD.md`](PRD.md) · [`../mcp-langchain-migration/PLAN.md`](../mcp-langchain-migration/PLAN.md) ·
[`services/ai-backend/docs/specs/mcp-tool-policy-pipeline.md`](../../../services/ai-backend/docs/specs/mcp-tool-policy-pipeline.md)

House rules this plan is written against:
[`services/ai-backend/CLAUDE.md`](../../../services/ai-backend/CLAUDE.md) (Pydantic at every IO
boundary, no long-lived `dict[str, Any]` domain state, behaviour inside classes not module-level
helpers, typed domain errors with safe messages, MCP descriptors are **untrusted input**, permission
checks live in `capabilities/` middleware and are never bypassed) and
[`services/ai-backend/docs/CLAUDE.md`](../../../services/ai-backend/docs/CLAUDE.md) (spec-first: the
spec lands before the implementation, and edge cases are raised, never simplified away).

> **Scope discipline.** This document plans two things and nothing else: (A) how MCP
> `resources/*` and `prompts/*` reach the agent through the machinery tools already use, and (B) how
> rendering and gating become separate concerns on separate middleware, including exactly which
> frontend code that retires. Deletion of the retired **backend** effect-staging modules is
> [`DELETIONS-PLAN.md`](DELETIONS-PLAN.md)'s job; this plan only names the modules and hands them
> over.

---

## 0 · Ground truth — what is actually wired today

Every claim below was read in the tree at the stated line. Where I could not establish something by
reading, it is marked **UNVERIFIED** and phrased as a question rather than a fact.

### 0.1 Resources: discovered, validated, published — never read

| Fact                                                                             | Evidence                                                                                                                                                                             |
| -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `resources/list` is on the client Protocol and the paginated extension           | `capabilities/mcp/client.py:142` (`list_resources`), `:200-207` (`list_resources_page`), page contract `:45-62`                                                                      |
| The loader calls it on every load, both paginated and not                        | `capabilities/mcp/loader.py:252-256`, pagination walk `:499-523`                                                                                                                     |
| The backend proxy implements it and degrades gracefully when the server does not | `capabilities/mcp/backend_provider.py:305-347`; `McpUnsupportedMethodError` → empty page `:335-337`                                                                                  |
| A typed, validated descriptor contract exists                                    | `capabilities/mcp/cards.py:424-451` (`McpResourceDescriptor`) + `:411-421` (`McpResourceAccessPolicy`)                                                                               |
| Results are carried on the load result                                           | `capabilities/mcp/cards.py:502-509` (`LoadedMcpServer.resources`)                                                                                                                    |
| They are already materialized into the filesystem catalog                        | `capabilities/mcp/catalog.py` — `McpCatalogPaths.resource_file`, `McpCatalogRenderer.resource_file`, `McpCatalogBuilder._resource_files`; `SERVER.md` header prints a resource count |
| The catalog mount serves them read-only to the model                             | `capabilities/mcp/catalog_backend.py` module docstring + `McpCatalogBackend.write/edit/adelete` refusals                                                                             |
| **Nothing anywhere calls `resources/read`.**                                     | `grep -rn "read_resource\|resources/read" services/` → zero hits in `src/`                                                                                                           |
| **`resources/templates/list` does not exist anywhere.**                          | `grep -rn "list_resource_templates\|resources/templates"` → zero hits in `src/`                                                                                                      |

So: the model can **see** that `linear://issue/{id}` exists and cannot **open** it. That is the
whole resources gap — it is a read gap, not a discovery gap.

### 0.2 Prompts: absent, end to end

`grep -rn "list_prompts\|get_prompt\|prompts/list\|prompts/get" services/` returns **zero hits** in
any `src/` tree. There is no descriptor, no client method, no JSON-RPC constant
(`capabilities/mcp/constants.py:194-201` lists exactly `initialize`, `notifications/initialized`,
`tools/call`, `resources/list`, `tools/list`), no catalog directory, no UI. Prompts are entirely
new.

### 0.3 The library already has both adapters — we are not writing protocol code

`langchain-mcp-adapters==0.3.1` (`requirements.in:49`) ships, and `mcp==1.29.0`
(`requirements.txt:1396`) underneath it:

- `langchain_mcp_adapters/resources.py` — `get_mcp_resource(session, uri) -> list[Blob]`,
  `load_mcp_resources(session, uris=None) -> list[Blob]`,
  `convert_mcp_resource_to_langchain_blob` (handles `TextResourceContents` and base64
  `BlobResourceContents`). Its docstring flags the trap we must not inherit: with `uris=None`,
  **dynamic (templated) resources are silently skipped**, because `session.list_resources()` does
  not enumerate them.
- `langchain_mcp_adapters/prompts.py` — `load_mcp_prompt(session, name, arguments) -> list[HumanMessage | AIMessage]`.
  It **raises `ValueError`** on any non-text content block and on any role other than
  `user`/`assistant` — an untrusted-input path that must be wrapped, per CLAUDE.md.
- `MultiServerMCPClient.get_prompt` / `.get_resources` (`langchain_mcp_adapters/client.py:302`, `:313`).
- `mcp.ClientSession` has all five verbs: `list_resources` `:287`, `list_resource_templates` `:326`,
  `read_resource` `:353`, `list_prompts` `:453`, `get_prompt` `:480`.

### 0.4 Two live, load-killing defects in the existing resource contract

These are not hypotheticals about future work — they are reachable today, on the `call_mcp_tool`
lane, for any connector that publishes resources.

**D1 — `file:` resource URIs kill the entire server load.**
`SUPPORTED_RESOURCE_URI_SCHEMES` is `{https, mcp, urn}`
(`capabilities/mcp/cards.py:36-38` reading `constants.py:243-248`), and
`McpResourceDescriptor._normalize_uri` raises on anything else (`cards.py:436-445`). The MCP SDK
types `Resource.uri` as an unconstrained `AnyUrl` with `host_required=False`
(`mcp/types.py:770`), and `file:` is the scheme the reference filesystem server and most local
servers use. The failure is **not scoped to the resource**: the proxy builds each descriptor eagerly
inside the page (`backend_provider.py:341-347`), so the `ValidationError` escapes
`list_resources_page`, is caught by the loader's blanket `except ValidationError`
(`loader.py:302-309`), and the whole load returns `MALFORMED_DESCRIPTOR` — **tools included**. One
unsupported resource URI blanks a 52-tool connector.

Worse for diagnosis: that branch's safe message is
`Messages.Loader.INVALID_CONNECTION_METADATA`, i.e. the user is told the _connection metadata_ was
invalid when the connection was fine and a resource URI was the problem. This is the same
error-taxonomy collapse the `McpRequestRejectedError` work fixed for 4xx
(`capabilities/mcp/client.py:79-97`).

**D2 — spec-optional fields are contract-required.**
`McpResourceDescriptor.description` and `.mime_type` are `min_length=1`
(`cards.py:429-433`), but `Resource.description` and `.mimeType` are `str | None = None` in the SDK
(`mcp/types.py:772-775`). Today this is masked only because the backend proxy substitutes fallbacks
before constructing the model (`backend_provider.py:730-745`:
`… or Values.Mime.OCTET_STREAM`, `… or f"{name} MCP resource."`). The moment a second resource
lister exists — which is exactly what P4 adds via `langchain-mcp-adapters` — the strictness becomes
live and fails closed on legal servers.

**Both must be fixed before any resource-read tool ships.** A read tool over a descriptor set that a
legal server cannot get past validation is a feature nobody can reach.

### 0.5 The policy machinery a resource read must flow through

- `CapabilityDescriptor` (`capabilities/policy/contracts.py:177-218`) — `urn`, `action`, `trust`,
  `scopes`, `source`, `connector_state`; `urn` is validated at construction against
  `CapabilityUrn.parse`.
- `CapabilityUrn` (`:250-300`) — two schemes only, `mcp:{server}:{tool}` and `builtin:{ns}:{op}`
  (`UrnScheme`, `:124-135`). `parse` keeps everything past the second colon as the trailing name
  (`:290-300`), which is the property that lets a resource URI live in the trailing segment
  unescaped.
- `PdpPolicyService.decide` (`capabilities/policy/service.py:175-212`) — DENY-first: availability
  (`:194-195`) → scopes ∧ allowlist (`:202-207`) → the `action × trust × posture` matrix
  (`:253-315`).
- **A READ flows, it does not gate** — provided the connector is TRUSTED. `_posture_decision` sends
  a trusted READ through the base-mode branch, where the fail-open default is `auto`
  (`service.py:311-315`; defaults per spec §7). An **untrusted** READ is the deliberate exception:
  `service.py:292-298` GATEs it under MANUAL when `untrusted_read_gate` is on (default `True`,
  `:151`) — and note the comment there already names our invariant:
  _"the visible card is an Observe concern — render ≠ approve"_.
- The MCP source that fills the descriptor:
  `McpCapabilityDescriptorSource.describe` (`capabilities/mcp/descriptor_source.py:85-103`),
  `action_for` catalog→annotations→fail-closed-WRITE (`:118-138`), `_trust` (`:140-148`),
  `_connector_state` (`:150-170`), `posture_for` (`:105-116`).
- `McpDispatchPolicy.evaluate` (`:193-231`) composes descriptor + PDP into one verdict.

### 0.6 The middleware pipeline

`MIDDLEWARE_ORDER = (POLICY, EXEC_POLICY, OBSERVE, ERROR_MAP, CITATIONS)`
(`capabilities/policy/contracts.py:310-316`), asserted — never inferred — by
`ToolMiddlewareComposer._assert_order` (`middleware/compose.py:200-211`), composed in reverse so
POLICY is outermost (`:192-198`). Every stage must be schema-identical to what it wraps
(`ToolSchemaIdentity`, `compose.py:61-140`).

Shipped stages: `policy_tool.py` (the PDP + `ToolAccessGate.park_for_approval`, `:362-411`),
`exec_policy_tool.py`, `observe_tool.py`, `error_map_tool.py`, `citations_tool.py`; assembled by
`per_tool_registration.py` behind `MCP_PER_TOOL_ENABLED`, **default OFF**.

---

## PART A — Resources and Prompts

### A.1 The design in one sentence

A **resource read is a capability like any other**: the same `CapabilityDescriptor`, the same PDP,
the same five-stage middleware, the same `/mcp/<server>/…` catalog — with `action=READ`, so it
flows instead of gating; and a **prompt is not a capability at all** — it is model-facing text, so
it gets the catalog and a fetch tool but never becomes an auto-applied message list.

### A.2 New vs. already present

| Piece                                                    | Status                                                                                        |
| -------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `resources/list` discovery, pagination, graceful-degrade | **Present** — `client.py:142,200`; `loader.py:252-256,499-523`; `backend_provider.py:305-347` |
| `McpResourceDescriptor` + access policy contracts        | **Present** — `cards.py:411-451`                                                              |
| Resource files in the browsable catalog                  | **Present** — `catalog.py` (`resource_file`, `_resource_files`, `resources_dir`)              |
| Read-only `/mcp/` filesystem mount                       | **Present** — `catalog_backend.py`                                                            |
| Library adapters for read/prompt                         | **Present in the dependency** — `langchain_mcp_adapters/{resources,prompts}.py`; unused by us |
| Backend proxy admits arbitrary JSON-RPC methods          | **Present** — `services/backend/src/backend_app/service.py:1501-1511` gates only `tools/call` |
| `resources/read` anywhere                                | **NEW**                                                                                       |
| `resources/templates/list` anywhere                      | **NEW**                                                                                       |
| `prompts/list` · `prompts/get` anywhere                  | **NEW**                                                                                       |
| A `CapabilityDescriptor` for a non-tool capability       | **NEW** (needs the URN scheme decision, A.4)                                                  |
| `file:` URI support · optional-field tolerance           | **NEW (bug fix)** — see §0.4                                                                  |

### A.3 P4a — fix the descriptor contract first (blocking)

Nothing else in Part A may land before this.

1. **Widen the URI allowlist to include `file`** and keep the deny-list posture explicit. Add
   `FILE = "file"` to `constants.py Values.UriScheme` and to
   `cards.py:36-38 SUPPORTED_RESOURCE_URI_SCHEMES`. Everything else stays refused — a `javascript:`
   or `data:` resource URI is a prompt-injection vector, and the allowlist is the control.
   **Open question (needs a decision, not a guess):** whether `file:` resources should additionally
   be refused for _remote_ transports (HTTP/SSE), where a `file:` URI names a path on the server's
   machine and reading it is a different trust proposition from a local stdio server naming a path
   on ours. My recommendation is to admit the scheme at the contract level and let the descriptor's
   `trust` carry the distinction, because refusing at parse time reintroduces the load-killing shape
   this item exists to remove — but I have not verified how the desktop stdio lane spells its
   transport at the point the descriptor is built, so this is a recommendation, not a finding.
2. **Make `description` / `mime_type` optional at the contract and apply the fallback in one
   place.** Move the substitution that currently lives in `backend_provider.py:730-745` onto
   `McpResourceDescriptor` itself as a `mode="before"` validator, so both the proxy lane and the
   future direct-connect lane get one behaviour. Per CLAUDE.md ("no inline duplication … keep helper
   behavior inside classes"), the fallback strings belong in `Messages`/`Values`, not inline.
3. **Make a bad resource cost the resource, not the server.** `McpLoaderHelpers.parse_resources`
   (`loader.py:596-610`) currently maps any failure to a whole-load `MALFORMED_DESCRIPTOR`. Change
   it to partition: return the valid descriptors plus a `McpLoadWarning`
   (`cards.py:487-499`, code `RESOURCE_DESCRIPTOR_SKIPPED`) naming the count skipped. Tools are
   unaffected — a connector's _tool_ descriptors are what the run needs, and a malformed resource
   must never be able to take them down.
   **Deliberately not changed:** `parse_tools` keeps fail-whole. A partially-parsed tool list would
   let a hostile server hide a tool from the index while leaving it dispatchable, which is a
   different and worse failure.
4. **Fix the error copy.** The `except ValidationError` branch at `loader.py:302-309` must not say
   `INVALID_CONNECTION_METADATA` for a descriptor failure. Add
   `Messages.Loader.DESCRIPTORS_INVALID` there (it already exists, `:350`), and reserve the
   connection-metadata message for the `_connect` path. This is the "error copy is a model
   paraphrase" class of defect: the user-visible sentence is the model paraphrasing this string.

**Tests (P4a).** A `file://` resource loads and is catalogued; a resource with no `description` and
no `mimeType` loads with the fallbacks; a resource with a `javascript:` URI is skipped with a
warning while every tool still loads; the load result for a descriptor failure carries the
descriptor message, not the connection-metadata message.

### A.4 P4b — the resource-read capability

#### A.4.1 URN scheme

A resource is addressed by URI, not by a slug, and `tool_slug` lowercases
(`policy/contracts.py:258-260`) — which would corrupt a case-sensitive URI. Two candidates:

- **(i) Extend `UrnScheme` with `MCP_RESOURCE = "mcp+resource"`**, giving
  `mcp+resource:{server}:{uri}`. `CapabilityUrn.parse` already keeps everything past the second
  colon whole (`contracts.py:290-300`), so a URI containing colons survives round-trip. Requires a
  `for_mcp_resource` builder that slugs only the server segment and passes the URI through
  verbatim.
- **(ii) Reuse `mcp:{server}:{tool}` with a reserved pseudo-tool** `read_resource`, and carry the
  URI in `args`. Zero contract change; the PDP already ignores `args`
  (`service.py:184-191`), so nothing breaks — but the policy can then only be written per _server_,
  never per _resource_.

**Recommendation: (i).** The URN is described in P0 as "the identity a policy is written against"
(`contracts.py:250-261`); collapsing every resource on a server to one identity forfeits exactly
that. The cost is one enum member and one builder, both additive.

**Consequence to accept explicitly:** `descriptor_source.action_for` is keyed `(server, tool)`
(`descriptor_source.py:118-138`) and consults `ACTION_CATALOG.lookup(server, tool)`. A resource
capability must **not** go through it — it must be a constant `Action.READ`, because a resource read
is a read by protocol definition (`McpResourceAccessPolicy.read_only` is `True` by contract,
`cards.py:415`) and running it through the fail-closed-WRITE ladder would classify every
uncatalogued resource as a write and gate it. Add a sibling classmethod
`McpCapabilityDescriptorSource.describe_resource(...)` that reuses `_trust` and `_connector_state`
verbatim and hard-codes `action=Action.READ`. Reusing those two private helpers rather than copying
them is what keeps availability/authorization one derivation, per that module's own docstring
("It is the only place a `CapabilityDescriptor` is built").

#### A.4.2 Where the read runs

One new tool, `read_mcp_resource(server_name, uri)`, produced by a new source method and wrapped by
the **same** `ToolMiddlewareComposer` stack. Concretely:

- **POLICY** (`policy_tool.py`) needs no change beyond a card lookup that resolves the new URN
  shape. `PolicyToolMiddleware._card_for` (`:518-536`) cross-checks
  `descriptor.urn != CapabilityUrn.for_mcp(card.name, tool.name)` and refuses on mismatch — that
  check must gain the resource form or **every** resource read fails closed. This is the single
  highest-risk edit in Part A: the failure is silent (a refusal, not an exception) and looks like a
  permissions problem.
- **EXEC_POLICY** — a read is retryable; the existing rule keyed on `descriptor.action` covers it.
- **OBSERVE** — binds `McpCallBinding(action=READ)` (`observe_tool.py:55-85`), which is what lets
  Part B render the result without any policy involvement.
- **ERROR_MAP** — must gain the two new failure shapes: resource-not-found and
  unsupported-method (`McpUnsupportedMethodError` already exists, `client.py:110-111`).
- **CITATIONS** — a resource read is a _source_. This is the highest-value side effect of routing
  resources through the same stack: `cite_mcp.py` already turns MCP results into citations, so a
  resource read becomes a citable source for free.

Client seam: add `read_resource` to the `McpClient` Protocol (`client.py:129-149`) and implement it
on `backend_provider` via `Values.JsonRpcMethod.READ_RESOURCE = "resources/read"`. **No
`services/backend` change is required** — `service.py:1501-1509` explicitly documents that under
`ConnectorAccessMode.READ` "`tools/list` (and every other method) is always allowed", and only
`tools/call` is gated. Verify with a test rather than trusting this note.

#### A.4.3 Result handling — the blob problem, again

`ReadResourceResult.contents` can be text or base64 binary
(`langchain_mcp_adapters/resources.py`, `BlobResourceContents` branch). A large text resource
returned as a tool result walks straight into the failure item 1 of the PRD exists to fix: it is
offloaded to `/large_tool_results/<sha>` whose preview is line-bounded, whose offsets are discarded,
and whose grep returns nothing ([`PRD.md`](PRD.md) §1.1 table A–D).

**Therefore a resource read must not return its bytes inline.** It writes into the catalog mount
and returns a pointer, exactly as `LoadMcpServerTool` does for descriptors
(`middleware/dynamic_loader.py:89-136`). Proposed layout, additive to what `catalog.py` already
emits:

```
/mcp/<server>/resources/<slug>.json      # descriptor  (EXISTS today)
/mcp/<server>/resources/<slug>.<ext>     # NEW: the content of the last read
/mcp/<server>/resources/TEMPLATES.md     # NEW: uriTemplate index (P4b-2)
```

Three constraints inherited from the module that owns the mount, each already enforced and each of
which the content path must satisfy: every file is newline-terminated and spans more than one line
(`catalog.py McpCatalogFile._validate_content`, `Limits.MIN_NEWLINES_PER_FILE`) because deepagents'
`read_file` slices by source line; every rendered byte is scanned by `SecretShapeScanner`
(`files.py:110-156`) before publication; and the mount refuses model writes
(`catalog_backend.py`), so ai-backend authoring a file here does not make `/mcp/` writable.

**Binary resources.** A base64 `BlobResourceContents` cannot satisfy "line-oriented text". Write a
`.json` sidecar carrying `{uri, mime_type, byte_size, note}` and **do not** write the bytes. Handing
a model base64 is the blob failure with extra steps.

#### A.4.4 Templates

`resources/templates/list` is what makes dynamic resources reachable at all — `load_mcp_resources`
with `uris=None` skips them by construction, and its docstring says so. Add
`list_resource_templates` to the client Protocol and render one `TEMPLATES.md` per server (an index
of `uriTemplate` + description, budgeted the same way `SERVER.md` is). A template is **not** a
capability and gets no descriptor: it is a URI _shape_. The capability is the read of the URI the
model constructs from it, which is policed at read time by the descriptor built in A.4.1.

### A.5 P4c — prompts

MCP prompts are user-invoked templates, not agent-invoked capabilities. Two decisions:

1. **Prompts are catalogued, not registered.** `/mcp/<server>/prompts/<name>.json` carrying
   `{name, description, arguments[]}` and a `PROMPTS.md` index, rendered by `McpCatalogRenderer`
   alongside the existing tool and resource renderers. Discovery is a `ls`/`read_file`, same as
   everything else.
2. **`get_prompt` is a tool, and its output is data, not instructions.** `prompts/get` returns
   `PromptMessage[]`, which `load_mcp_prompt` converts into `HumanMessage`/`AIMessage`. **Splicing
   server-authored messages into the run's message list is a prompt-injection primitive** — a server
   would be handing us text that arrives with `user` or `assistant` authority. CLAUDE.md's
   "Untrusted inputs" section names MCP descriptors explicitly; prompt _content_ is strictly more
   dangerous than a descriptor. So `get_mcp_prompt` returns the rendered text as an ordinary tool
   result the model reads and decides about, never as messages the graph appends.
   **This is a product decision I am flagging, not settling** — a host that wanted true MCP-prompt
   UX would let the _user_ pick a prompt from a picker and the host would splice it with user
   authority. That is a legitimate design and a different PRD; it must not arrive by accident
   through an agent-callable tool.
3. **Wrap the library's exceptions.** `convert_mcp_prompt_message_to_langchain_message` raises bare
   `ValueError` on a non-text content block or an unknown role. Per CLAUDE.md, convert to a typed
   domain error with a safe message at our boundary — an unsupported prompt must degrade to a typed
   refusal, never surface a library exception string.

Descriptor: `action=READ`, `trust`/`connector_state` from the shared helpers, URN
`mcp+prompt:{server}:{name}` if A.4.1 option (i) is taken.

### A.6 Contracts to add (full shapes go in the spec, not here)

Per `docs/CLAUDE.md`, P4 lands a spec section before code. New contracts, all
`RuntimeContract`-based (frozen, `extra="forbid"`):

- `McpResourceTemplateDescriptor` — `uri_template`, `name`, `description`, `mime_type`.
- `McpResourceContent` — `uri`, `mime_type`, `text | None`, `byte_size`, `is_binary`.
- `McpPromptDescriptor` — `name`, `description`, `arguments: tuple[McpPromptArgument, ...]`.
- `McpPromptArgument` — `name`, `description`, `required: bool`.
- `McpResourceReadResult` / `McpPromptResult` — result envelopes shaped like
  `McpToolCallResult` (`cards.py:544-622`): exactly one of output/error, `fail_from_load_error`
  lifting, safe messages only.
- `LoadedMcpServer` gains `resource_templates` and `prompts` tuples (`cards.py:502-509`).

### A.7 Security review points

- **Untrusted descriptors.** Resource `name` is free-form and becomes a filename;
  `McpCatalogPaths.file_slug` (`catalog.py`) already collapses it to `[a-z0-9._-]` and de-duplicates
  by index (`_resource_files`). Prompt names must go through the same helper — one derivation, not
  two.
- **Secret smuggling.** Resource _content_ is server-controlled free-form text, which is the
  broadest surface `SecretShapeScanner` has ever been pointed at. It scans the rendered string
  (`catalog.py McpCatalogBuilder._file` docstring explains why the string and not the payload), so
  the existing control applies — but a content file is orders of magnitude larger than a descriptor
  and the scanner walks it per read. **UNVERIFIED:** whether that cost matters. Measure before
  optimizing.
- **Prompt injection.** §A.5.2. The catalog already carries an implicit mitigation worth keeping
  explicit: everything under `/mcp/` is _read_ by the model as file content, never spliced into the
  system prompt.
- **Scope inheritance.** `McpResourceAccessPolicy.required_scopes` is populated from the _server's_
  `required_scopes` (`backend_provider.py:746-748`) — there is no per-resource scope in the
  protocol. The PDP's `_has_scopes` requires the connector to be present in `connector_scopes` and
  to carry every required scope (`service.py:214-231`), so resource reads inherit exactly the
  server's authorization. Correct, and worth a test that pins it.

### A.8 Sequencing

```
P4a  descriptor-contract fixes (file:, optional fields, partial parse, error copy)   [BLOCKING]
  └─▶ P4b-1  resources/read + read_mcp_resource + catalog content files
        └─▶ P4b-2  resources/templates/list + TEMPLATES.md
  └─▶ P4c    prompts/list + prompts/get + prompts catalog        [independent of P4b]
```

P4a is shippable on its own and fixes a live defect. P4b and P4c are independent of each other and
both depend on P4a.

---

## PART B — Render is not approve

### B.1 The invariant

Already written into the spec — this plan executes it, it does not invent it.
[`mcp-tool-policy-pipeline.md` §3 "Render ≠ approve"](../../../services/ai-backend/docs/specs/mcp-tool-policy-pipeline.md):

> Rendering and gating are **separate concerns on separate middleware**… a **Manual write** →
> artifact renders **and** an approval card appears; a **Bypass write** → artifact renders, **no
> card**… The old fused surface (`EffectStageCard` = render + gate) is retired.

and §7: _"**Render ≠ approve** (§3): Observe renders; Policy gates; the two never fuse."_

Three consequences, which become three tests (§B.5):

| Case             | Artifact / gen-UI renders | Approval card                   |
| ---------------- | ------------------------- | ------------------------------- |
| Manual **write** | **yes**                   | **yes**                         |
| Bypass **write** | **yes**                   | **no**                          |
| Any **read**     | **yes**                   | **no change-shaped card, ever** |

### B.2 What is actually wired today — three findings

**B.2.1 — Rendering is not on OBSERVE. It is on the operation gateway, and the per-tool lane does
not have one.**

`observe_tool.py:7-13` says so in its own words: it "emits no event and writes no row", deliberately,
because `runtime_worker/stream_tools.py` already owns the durable envelope. It binds
`McpCallBinding` on a `ContextVar` and nothing else (`:119-193`).

The Studio artifact / gen-UI path is elsewhere entirely:

```
OperationGateway._invoke_once            capabilities/operations/gateway.py:218-259
  └─ execute-now branch only  (raw_result is not None)                       :241-259
       └─ SurfaceLedgerOperationOutcomePresenter.present                     presentation.py:27-63
            ├─ SurfaceProjector.resolve  → SurfaceEnvelope {spec, source, data}   surfaces/projector.py
            └─ WorkLedgerEmitter.on_tool_result → surface.created / view.derived  surfaces_v2/emitter.py
```

`stream_tools.py:910-916` confirms the split explicitly: the v1 `result["surface"]` hoist was
retired and "Surface data now flows to clients via the Work Ledger (`surface.created` /
`view.derived`)".

The only caller that reaches that gateway is the legacy `call_mcp_tool` middleware
(`middleware/call_tool.py:88` `McpOperationGatewayContext.canonical()`). Grepping the per-tool lane
for `OperationGateway|WorkLedgerEmitter|SurfaceProjector|present` returns **nothing** across
`per_tool_registration.py` and all five `middleware/*.py` stages.

> **Therefore: flipping `MCP_PER_TOOL_ENABLED=true` today silently stops emitting
> `surface.created` for MCP results.** Every Studio artifact and every generated SurfaceSpec for
> connector output disappears, with no error and no failing test — the tests inject the emitter, and
> `WorkLedgerEmitter.active()` returning `None` is an ordinary early return
> (`presentation.py:35-37`). This is the same failure mode recorded in the "injected deps hide a
> dead feature" lesson. **P4 must close this before the flag is ever considered for default-ON.**

**B.2.2 — Rendering is coupled to the execute-vs-stage fork, i.e. to a policy outcome.**

`gateway.py:241-259` gates presentation on `raw_result is not None`, and `raw_result` is `None` on
the staging branch (`:220-240`). So _whether an artifact renders_ is decided by _whether the
operation was allowed to execute_. For MCP that currently produces the right answer by accident:
P1b sets `authorized_to_execute=True` (`middleware/call_tool.py:282-284`), the gateway's
`_executes_now` honours it (`gateway.py:350-375`), so every PDP-cleared MCP call — read **and**
approved write **and** bypassed write — takes the execute branch and renders. Correct outcome,
accidental mechanism: it holds because the fork was collapsed, not because render was separated
from gate.

**B.2.3 — The frontend still carries the fused surface the spec says is retired.**

`EffectStageCard` renders a "PROPOSED CHANGE" eyebrow, a title, status copy, **and**
Approve/Reject buttons in one component (`EffectStageCard.tsx:21-56`) — render and gate in the same
box, which is precisely what §3 retires. Its decision half is driven by
`projectMcpEffectStages`, which folds `effect.staged` events **filtered to `payload.executor === "mcp"`**
(`effectStageLifecycle.ts:124-151`, the filter at `:128`).

### B.3 The target architecture

```
POLICY      ── decides ALLOW | GATE | DENY ───────────▶ approval card (interrupt) on GATE only
EXEC_POLICY ── retries/timeouts
OBSERVE     ── owns the *record and the render*: stream envelope · invocation row · surface.created
ERROR_MAP   ── typed taxonomy
CITATIONS   ── result → sources
```

**Move the presentation hand-off from the gateway onto the OBSERVE stage.** Concretely:

1. Extract the presentation call from `gateway.py:241-259` into a small injected collaborator that
   takes `OperationPresentationOutcome` and hands it to `SurfaceLedgerOperationOutcomePresenter` —
   the presenter itself already has the right shape and the right ports
   (`presentation.py:27-63`) and needs no change.
2. Give `McpObserveMiddleware` that collaborator, constructor-injected (the P0 `ToolMiddleware`
   contract's stated pattern: "Services … are constructor-injected into the concrete middleware",
   `policy/contracts.py:398-411`). OBSERVE already knows the connector, the capability URN, the
   effect class and the call id from `McpCallBinding` (`observe_tool.py:55-85`) — everything
   `OperationPresentationOutcome` needs except the result payload, which it sees as the delegate's
   return value.
3. **The gateway keeps calling it too, for the legacy lane** — until `call_mcp_tool` is retired the
   two lanes must both render. The idempotency question ("can one call present twice?") is
   answerable because `operation_id` / `call_id` keys the surface; **UNVERIFIED** whether
   `WorkLedgerEmitter.on_tool_result` is idempotent on a repeated `call_id`, and that must be
   established before both lanes are live simultaneously.
4. **OBSERVE must not consult the descriptor's `action` to decide whether to render.** That is the
   fusion in disguise. It renders whatever the delegate returned, for every action class, in every
   posture. The only thing that stops a render is the call not happening — and a call that did not
   happen has nothing to render.

**A naming defect to fix while there.** `OperationAdapter.execute_read` (`operation_adapter.py:204`)
is the method an approved **write** goes through, because `_executes_now` is action-agnostic. The
name actively misleads a reader into believing writes cannot reach it — which is exactly the
render-follows-policy confusion this part exists to remove. Rename to `execute_now` (or
`execute_authorized`) across the `OperationAdapter` Protocol and its implementations.

### B.4 What the three consequences require

- **Manual write → card AND artifact.** The card comes from `policy_tool.py:399-405`
  (`gate.park_for_approval` → LangGraph interrupt → `approval_requested` → the inline
  `ApprovalCard`). The artifact comes from OBSERVE after the resume executes. Nothing is needed
  beyond B.3 — but the ordering is worth pinning in a test: the card exists **before** the artifact,
  because the artifact does not exist until the write runs.
- **Bypass write → artifact, NO card.** `PdpPolicyService._posture_decision` returns
  `ALLOW` for every non-BLOCK row under BYPASS (`service.py:281-282`), so POLICY never parks, so no
  approval event is emitted. Already true; the test pins that OBSERVE still rendered.
- **A read never produces a change-shaped card.** Two distinct things must both hold, and only one
  of them holds today:
  - the PDP must not GATE a trusted read — holds (`service.py:311-315`);
  - **no `effect.staged` may be emitted for a read** — holds for MCP only because MCP no longer
    stages at all (§B.5.1). Pin it with a test that asserts zero `effect.staged` events on a read
    run, so a future re-introduction of staging on the MCP lane fails loudly.

### B.5 Frontend: what is dead, and what must not be touched

#### B.5.1 Why the MCP staging path is dead

P1b's commit message (`84a67dc7`) states it, and three code sites confirm it:

- `middleware/call_tool.py:172-183` — the PDP is consulted **before** an operation id is minted; a
  GATE parks on the interrupt.
- `middleware/call_tool.py:282-284` — the MCP adapter is constructed with
  `authorized_to_execute=True`, commented "instead of routing a write to the retired staging path".
- `operation_adapter.py:175-183` — "the **retired** staging path… the browser adapter never sets it,
  so its `REQUIRE` effects still stage".
- `gateway.py:350-375` — `_executes_now` honours the flag; adapters without it "keep the
  `effect_class` staging rule".

And the decision endpoint the UI posts to is **MCP-executor-only**:
`runtime_api/http/effect_stage_decisions.py:73` passes
`allowed_executors=frozenset({EffectExecutorKind.MCP})`. With no producer of MCP-executor stages,
that route can never match a stage.

#### B.5.2 DEAD — safe to remove

| Symbol                                                                                  | File                                                    | Why dead                                                                     |
| --------------------------------------------------------------------------------------- | ------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `projectMcpEffectStages`, `McpEffectStageReview`, `McpEffectStageStatus`, `Accumulator` | `destinations/run/effectStageLifecycle.ts` (whole file) | Folds only `payload.executor === "mcp"` (`:128`); nothing emits those events |
| `mcpEffectStageReviews` memo                                                            | `RunDestination.tsx:2374-2386`                          | Only consumer of the above                                                   |
| `handleMcpEffectDecision`                                                               | `RunDestination.tsx:3366-3419`                          | Posts to the MCP-only `/decision` route                                      |
| `isMatchingMcpEffectDecision`                                                           | `RunDestination.tsx:780-…`                              | Only used by the handler                                                     |
| `effectStageBusyId` / `effectStageMessages` / `setEffectStageMessage`                   | `RunDestination.tsx:3351-3361` + state                  | Only written by the handler                                                  |
| `EMPTY_MCP_EFFECT_STAGE_REVIEWS`                                                        | `RunDestination.tsx:287`                                | Only used by the memo                                                        |
| `review` / `onDecision` / `busy` props and `copyFor()`                                  | `EffectStageCard.tsx:9-16,35-54,59-74`                  | The gate half of the fused card                                              |
| `effectStageLifecycle` tests                                                            | `destinations/run/`                                     | Cover only the removed projection                                            |

Backend siblings that become unreachable at the same time — **named here, deleted by
[`DELETIONS-PLAN.md`](DELETIONS-PLAN.md), not by this plan**: `runtime_api/http/effect_stage_decisions.py`,
`runtime_api/schemas/effect_stage_decision.py` (its `state.executor is not EffectExecutorKind.MCP`
guard at `:60`), `McpOperationAdapter.build_proposal` (`operation_adapter.py:~280-345`),
`runtime_worker/mcp_effect_executor.py`, and the `builtin/call_mcp_tool → EffectExecutorKind.MCP`
row in `effects/composition.py:51-55`. **Do not delete `runtime_worker/staged_write_effect_dispatch.py`**
— despite `executor=EffectExecutorKind.MCP` at `:108`, it is the **rowset commit** dispatcher
(`runtime_worker/handlers/stage_commit.py:495`): the _stage_ is BUILTIN, the per-row _dispatch_ is
MCP. Removing it breaks rowsets.

#### B.5.3 MUST STAY — and the one that is easy to get wrong

**`EffectStageCard` itself must NOT be deleted.** It has two live, non-MCP consumers:

1. **The rowset loading placeholder** — `RunDestination.tsx:3820-3834`: while
   `rowsetEffectReviews.get(stageId)` is `undefined`, the card renders with `busy` and
   `"Loading the exact row review…"`. Rowset staging is very much alive
   (`runtime_worker/rowset_effect_staging.py:166-170`, `executor=EffectExecutorKind.BUILTIN`,
   `proposal_kind=ROW_SET`), served by `/v1/agent/effect-stages/{id}/rowset/*`
   (`runtime_api/http/rowset_effect_reviews.py:177`) and rendered by `TcStagedTableSurface`.
2. **The desktop-browser stage fallback** — this is the one that looks dead and is not.
   `capabilities/browser/effect_adapter.py:76` and `effects/composition.py:70-77` stage browser
   actions under **`EffectExecutorKind.BROWSER`**, and `middleware/call_tool.py:167-183` routes the
   browser server around the PDP gate precisely so it keeps staging. On the frontend there is **no
   browser-specific stage renderer** — I grepped `destinations/run/` and found none. A browser stage
   therefore lands on the final `EffectStageCard` branch (`RunDestination.tsx:3886-3899`) with
   `mcpReview === undefined`, i.e. a display-only "PROPOSED CHANGE / Review this change before it
   can be applied." card.

> **Riskiest removal:** deleting `EffectStageCard` (or its no-`review` rendering path) along with
> the MCP projection. The MCP filter (`executor === "mcp"`) makes the two look like one feature; they
> are not. Removing the component silently leaves a **desktop-browser** staged action with no
> renderer at all — the surface a user is supposed to review before a click or form submission is
> applied on their behalf — and no test fails, because the browser lane's UI has no test that mounts
> this card. **The safe edit is surgical: delete the projection, the handler and the
> `review`/`onDecision`/`copyFor` props; keep the component, the `stageId`/`title`/`busy`/`message`
> props, and both call sites.**

Also untouched: `workspaceStageLifecycle.ts` + `TcWorkspaceStageSurface` +
`/effect-stages/{id}/decisions` (plural), which serve `EffectExecutorKind.WORKSPACE`
(`capabilities/workspace/effects.py:514-519`) and are a separate receipt-bearing contract.

**Follow-up worth its own item (out of scope here):** the browser stage's read-only fallback card is
honest but unactionable — a user cannot approve or reject it from the cockpit. Either it gets a real
browser decision surface, or the browser lane moves onto the PDP interrupt like MCP did. Naming it
so the P4 removal does not quietly bless the status quo.

### B.6 Tests

Backend, hermetic, on the real graph (the `test_mcp_write_gate_e2e.py` pattern — real worker + Deep
Agents graph + `ApprovalCoordinator`, faking only the model and the MCP client):

1. `test_manual_write_emits_card_and_surface` — one `approval_requested`, and after approve one
   `surface.created` for the same call id; assert card precedes artifact by `sequence_no`.
2. `test_bypass_write_emits_surface_and_no_card` — zero approval events, one `surface.created`.
3. `test_read_emits_surface_and_no_effect_staged` — zero `effect.staged`, zero approval events, one
   `surface.created`.
4. `test_per_tool_lane_emits_surface` — the B.2.1 regression, run with `MCP_PER_TOOL_ENABLED=true`.
   **Must not inject the emitter** — bind it the way the run handler does
   (`runtime_worker/handlers/run.py:547-555`), or the test reproduces the exact blind spot it exists
   to close.
5. `test_observe_renders_for_every_action_class` — parametrized READ/WRITE/DESTRUCTIVE × MANUAL/BYPASS;
   render is invariant across all six.

Frontend (`packages/chat-surface`): a rowset stage still shows the loading card; a `executor:
"browser"` `effect.staged` event still renders a titled read-only card; an `executor: "mcp"`
`effect.staged` event (which cannot occur, but a replayed historical run may contain one) renders
the read-only card and offers **no** buttons.

### B.7 Risks

| Risk                                                                                   | Mitigation                                                                                |
| -------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Both lanes present the same call → duplicate `surface.created`                         | Establish `on_tool_result` idempotency on `call_id` **before** enabling both (§B.3.3)     |
| Deleting `EffectStageCard` blanks the desktop-browser review surface                   | §B.5.3 — surgical removal; add the browser-stage render test first                        |
| Historical runs replayed from the ledger still contain `executor: "mcp"` staged events | Keep the read-only render path; only the decision affordance is removed                   |
| OBSERVE gains I/O and slows every tool call                                            | The presenter is already `await`ed on the gateway path today — this moves it, not adds it |
| `PolicyToolMiddleware._card_for` URN cross-check silently refuses every resource read  | §A.4.2 — the check must learn the resource URN shape in the same commit that adds it      |

---

## Open questions

- [ ] **Resource URN scheme** — extend `UrnScheme` (per-resource policy) vs. reuse
      `mcp:{server}:read_resource` (per-server policy). Recommendation: extend. §A.4.1.
- [ ] **`file:` on remote transports** — admit at the contract and let `trust` carry it, or refuse
      per-transport? I did not verify how the transport is spelled at descriptor-build time. §A.3.1.
- [ ] **Prompt semantics** — agent-callable tool returning text (this plan), or a user-driven picker
      that splices with user authority (a different PRD)? §A.5.2.
- [ ] **`WorkLedgerEmitter.on_tool_result` idempotency** — UNVERIFIED; blocks running the gateway
      and OBSERVE presenters simultaneously. §B.3.3.
- [ ] **Browser stage decision surface** — give the browser lane a real review UI, or migrate it onto
      the PDP interrupt as MCP was? §B.5.3.
- [ ] **Catalog content-file freshness** — a resource content file is a _snapshot of one read_. Is it
      overwritten per read, versioned, or evicted at run end? Inherits the PRD's open "catalog
      freshness" decision.

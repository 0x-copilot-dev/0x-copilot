# PR 4.4.6.2 — Approval card: structured payload (vendor, category, params, reason code)

> **Status:** Draft (PRD + Spec)
> **Plan reference:** Phase 2 of the consent-card redesign. Phase 1 (frontend-only `ApprovalCard` + `ApprovalReceipt`) shipped as PR 4.4.6.1.
> **Owner:** ai-backend (extend `_build_mcp_tool_approvals` in `stream_events.py`; add `ApprovalReasonCode` + `ApprovalCategory` enums; validators) · api-types (add `McpApprovalParam`, `McpApprovalCategory`, `McpApprovalReasonCode`, extend `ToolCallArgs` shape) · frontend (read structured fields with safe fallbacks; remove copy synthesis where the wire now carries it) · backend / backend-facade / design-system (zero — proxy & primitives unchanged)
> **Size:** **M.** Wire-only change; one stream-event builder + one frontend reader. ~210 LoC.
> **Depends on:**
>
> - ✅ PR 4.4.6.1 — `ApprovalCard` / `ApprovalReceipt` components landed; helpers in `toolLabels.ts` synthesise vendor / category / reason / reassurance from the existing flat fields.
> - ✅ PR 1.4 — approval payload schema (`approval_kind`, forwardable contract).
>
> **Reads alongside:**
>
> - [`pr-4.4.6-mcp-catalog-vs-connected.md`](pr-4.4.6-mcp-catalog-vs-connected.md) — connector data plumbing.
> - [`pr-1.4-two-stage-approvals.md`](pr-1.4-two-stage-approvals.md) — approval state machine, `approval_requested` event shape.
> - [`services/ai-backend/CLAUDE.md`](../../services/ai-backend/CLAUDE.md) — Pydantic at every IO boundary; no `dict[str, Any]` domain state; convert errors to typed domain exceptions.

---

## 0 · TL;DR

Today the FE synthesises consent-card copy from flat strings on the approval event:

| Field today        | Source                                                            | Problem                                                                                                         |
| ------------------ | ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `display_name`     | `_connector_display_name(server_name)`                            | OK — already set.                                                                                               |
| `tool_name`        | raw `tool_name` arg                                               | OK — already set.                                                                                               |
| `read_only`        | `_connector_action_is_read_only(tool_name)`                       | Boolean only. FE infers category (`READ`/`WRITE`) and reason ("writes outside your workspace") from this alone. |
| `risk_level`       | hard-coded `"low"` or `"medium"` from `read_only`                 | Server is the only thing that knows real risk; today it short-circuits to `read_only`. No `high` / `critical`.  |
| `message`          | `f"Allow {display_name} {action_label}?"`                         | Generic. Doesn't carry params from the actual tool call args.                                                   |
| Param table values | Synthesised in `ApprovalTool.tsx` from `risk_level` + `read_only` | Two rows max (Risk, Access). Can't show channel / visibility / scope detail from real call args.                |
| Reason copy        | `mcpApprovalReason()` (FE) using `read_only` + `risk_level`       | Three branches; can't say "first time using Linear in this chat" or "writes to a high-risk connector".          |

Phase 2 makes the approval event self-describing. The runtime worker emits a typed payload — `vendor`, `category`, `reason_code`, `reversible`, and a structured `params: list[ApprovalParam]` — directly into the `metadata` blob that already round-trips on `ApprovalRequestRecord`. The FE reads server-supplied fields first, falls back to its synthesisers when absent.

| Surface                                           | Today                                                      | After this PR                                                                                                                                                       |
| ------------------------------------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_build_mcp_tool_approvals` in `stream_events.py` | Emits `display_name, tool_name, read_only, risk_level`.    | + `vendor`, `category` (`READ`/`WRITE`/`ACTION`), `reason_code` (enum), `reversible` (tri-state), `params` (structured key-value rows from the call's `arguments`). |
| `ApprovalCategory` enum                           | Doesn't exist.                                             | New: `READ`, `WRITE`, `ACTION`. Source of truth in `agent_runtime.api.constants`. Mirrored to api-types.                                                            |
| `ApprovalReasonCode` enum                         | Doesn't exist.                                             | New: `READ_ONLY_FIRST_USE`, `WRITES_OUT_OF_WORKSPACE`, `RISK_HIGH`, `IRREVERSIBLE`, `DEFAULT`. Drives the FE's reason sentence.                                     |
| `ApprovalParam` contract                          | Doesn't exist.                                             | New: `{label: str, value: str, hint: str \| None}`. List, ≤ 6 items, deterministic order.                                                                           |
| `ApprovalTool.tsx` synthesisers                   | Synthesise category / reason from flat fields.             | Read server-supplied first; helpers stay as the fallback path so old runs in scrollback still render.                                                               |
| `mcpApprovalReason` (FE)                          | Three branches (`read_only`, `read_only=false`, fallback). | Switch on `reason_code`. Strings centralised in `approvalCopy.ts`.                                                                                                  |
| `ApprovalCard` params frame                       | Two synthetic rows (Risk + Access).                        | Up to 6 server-supplied rows (Channel, Visibility, Action, …) when present; falls back to Risk + Access when not.                                                   |
| `ApprovalRequestRecord.metadata`                  | `JsonObject` (untyped).                                    | Same shape on the wire; `MetadataValidator` (Pydantic) enforces the structured fields when `approval_kind == "mcp_tool"`.                                           |

LoC estimate: **ai-backend ≈ 110** (constants +20, contracts +50, stream_events +30, validators +10) · **api-types ≈ 30** · **frontend ≈ 40** (one new helper + 5 read-sites) · **tests ≈ 90** (stream_events emission, ApprovalTool reads, validator). Net delete: ~10 LoC of FE inference that the wire now carries.

The four runtime / streaming invariants from PR 1.4 / PR 4.4.6 are preserved:

1. **Frozen at run-start.** No change.
2. **Binary at runtime.** No change to `runtime_connector_scopes()`.
3. **No new event type.** The shape extends `approval_requested.metadata`. No new SSE event class. Replay-by-`sequence_no` semantics unchanged.
4. **Single PATCH endpoint.** No change.

Forward-compatibility: an old client (still on Phase 1) ignores the new fields and renders fine via the synthesisers. A new client receiving an old event (no structured fields) renders fine via the same synthesisers. **Zero migration.**

---

## 1 · PRD

### 1.1 Problem

Phase 1 made the consent card look right with the data we already had. But the data we have is a Boolean (`read_only`) and a string (`tool_name`), so the FE has to **invent** what the user sees:

1. **Reason text is invented from a Boolean.** `mcpApprovalReason(read_only, risk_level)` returns one of three sentences. The agent runtime knows whether this is the user's first call to Linear in this chat, whether the action is irreversible, whether the risk policy upgraded it — and that knowledge never reaches the FE. Result: every Slack write says "writes outside your workspace" even when the channel is private and inside the workspace.
2. **Param table is two rows of metadata, not the call.** Real tool args (`channel="#launch-aurora"`, `pin_until="2026-05-12"`) round-trip in `arguments` for the agent harness but never project into the consent card. Users decide blind: they see "Risk: medium" but not "Channel: #launch-aurora · 14 members".
3. **Vendor pill and access category are derived twice.** Backend has `read_only` and `display_name`; FE turns them into `LINEAR · READ`. If the rule changes (e.g., Slack reads from a private channel = `WRITE` because it leaks data), we'd have to chase the inversion through both layers.
4. **Risk level short-circuits to `low` / `medium`.** Server emits exactly two values regardless of the policy decision. We can't render "high-risk" badges or trigger the `RISK_HIGH` reason code because the wire never carries it.

The FE is doing UX inference the runtime should be doing because it has the policy context. We move the invariants to the wire.

### 1.2 Goals

1. **Wire carries the consent vocabulary.** `vendor`, `category`, `reason_code`, `reversible`, `params` ship in the `approval_requested` event payload, via the `metadata` blob already persisted on `ApprovalRequestRecord`.
2. **FE rendering becomes a switch on `reason_code`, not an inference.** Each enum variant maps to one sentence; new variants land server-side first.
3. **Params come from real call args.** The runtime worker projects the LangGraph action's `arguments` into a deterministic, ordered list of `{label, value}` rows. ≤ 6 rows; sensitive values are masked by the projection rules, not by a freeform string redactor.
4. **Pydantic at the IO boundary.** The structured fields live in a typed contract (`McpApprovalMetadata`) validated on emit and on read. No `dict[str, Any]` flowing through.
5. **Forward-compatible by default.** Old clients ignore unknown fields; new clients tolerate missing structured fields by falling through to the Phase 1 synthesisers. No migration of in-flight approvals.
6. **Scrollback safety.** Replays of old `approval_requested` events that never had the structured fields still render — the FE already handles `null`/`undefined` for every new field through the existing helper layer.

### 1.3 Non-goals

- **Per-tool MCP descriptor changes.** Phase 2 reads what the LangGraph action already passes as `arguments`. Annotating MCP server descriptors with vendor-specific param recognisers (Slack-specific channel-resolver, GitHub-specific PR-number labeller, etc.) is Phase 3.
- **Reversibility (undo) execution.** Phase 2 ships the `reversible` enum on the wire and the visual indicator. Actually wiring an "Undo" button to revoke the action is Phase 4.
- **Risk-policy rewrite.** The wire opens a door for `risk_level: "high" | "critical"` but Phase 2 keeps the existing `low`/`medium` mapping. The policy module that decides risk lives in `capabilities/tools/permissions.py`; touching it is its own PR.
- **AskAQuestion / non-MCP approvals.** Phase 2 only adds structured payload to `approval_kind == "mcp_tool"`. AskAQuestion already has its own structured contract.
- **Internationalisation.** Reason copy stays English in `approvalCopy.ts`; an i18n pass is a sequencing layer above this.
- **Audit log surfacing of the new fields.** The audit chain already records the metadata blob; rendering the new fields in the audit timeline is a separate UI PR.

### 1.4 Success criteria

- ✅ `_build_mcp_tool_approvals` in `runtime_worker/stream_events.py` emits the five new fields on every MCP-tool approval. Existing tests for the flat fields stay green.
- ✅ `ApprovalReasonCode` enum lives in `agent_runtime/api/constants.py`. Five variants. Mirrored in api-types.
- ✅ `ApprovalCategory` enum lives in `agent_runtime/api/constants.py`. Three variants (`READ`, `WRITE`, `ACTION`). Mirrored in api-types.
- ✅ `McpApprovalMetadata` Pydantic model in `runtime_api/schemas/approvals.py`. Validates: `params` length ≤ 6, `vendor` non-empty, `category` is a valid enum, `reversible` is `"yes" | "no" | "n/a"`.
- ✅ Reading an `approval_requested` event from a Phase-1 emitter (no structured fields) produces a card with the existing fallback rendering. No exceptions.
- ✅ `ApprovalCard` shows up to 6 server-supplied params when present; falls back to the existing Risk + Access pair otherwise.
- ✅ FE `mcpApprovalReason` becomes a switch on `reason_code` with the existing logic as the `DEFAULT` branch.
- ✅ FE `mcpApprovalCategory` reads server-supplied first; falls back to its `read_only`-based inference.
- ✅ Stream-events test (`runtime_worker/tests/test_stream_events.py`) covers: read-only emission, write emission, no-args emission (params empty), risk_high path, idempotent replay (same approval emitted twice serialises identically).
- ✅ ApprovalTool test (`apps/frontend/src/features/chat/components/tools/ApprovalTool.test.tsx`) covers: server-supplied params render verbatim; fallback path renders Risk + Access; `reason_code=RISK_HIGH` renders the high-risk sentence.
- ✅ `npm run typecheck --workspace @enterprise-search/api-types`, `npm run typecheck --workspace @enterprise-search/frontend` pass.
- ✅ `cd services/ai-backend && PYTHONPATH=src:../../packages/service-contracts/src .venv/bin/python -m pytest tests/unit/runtime_worker/test_stream_events.py` passes.
- ✅ A11y: param frame keeps `<dl>`/`<dt>`/`<dd>` semantics from Phase 1. Reason text and reassurance text remain `<p>` siblings (not visually hidden).

### 1.5 User stories

| #    | Persona                         | Story                                                                                                                                                                                                                                                                                                                                                        |
| ---- | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| US-1 | Sarah · approving a Slack write | Atlas drafts a launch announcement. The card lands: title "Post the draft to #launch-aurora?", pill `SLACK · WRITE`, reason "Atlas is asking because this writes outside your workspace.", params CHANNEL `#launch-aurora · 14 members` / VISIBILITY `Channel members + linked threads` / ACTION `Post message` / REVERSIBLE `Yes — 60s undo`. She approves. |
| US-2 | Marcus · scanning a Linear read | Card lands for a Linear issue list. Pill `LINEAR · READ`, reason "Atlas is asking before reading from this connector for the first time this turn.", param ASSIGNEE `me`. He approves without thinking — no "writes outside your workspace" alarm copy.                                                                                                      |
| US-3 | Sarah · risk-elevated action    | Atlas plans a `delete_repository` call. Card pill turns red: `GITHUB · WRITE`, reason "Atlas is asking because this writes to a high-risk connector — review the scope below.", param REPO `acme/legacy-billing` / IRREVERSIBLE `Yes`. She denies.                                                                                                           |
| US-4 | Sarah · old client, new event   | Sarah is on yesterday's bundle. The new server emits structured fields. Her client ignores them and renders the Phase-1 fallback. Card still works.                                                                                                                                                                                                          |
| US-5 | Sarah · new client, old event   | Sarah scrolls back to a run from last week. Old server didn't emit structured fields. New client falls through to `mcpApprovalCategory` / `mcpApprovalReason` synthesisers. Card still works.                                                                                                                                                                |
| US-6 | Auditor · post-hoc review       | The audit log shows `mcp_approval_decided` with the original metadata blob, including the structured fields. The reason code lets compliance group "writes outside workspace" approvals across runs without parsing English.                                                                                                                                 |

---

## 2 · Spec

### 2.1 Wire — backend contracts

**`ApprovalCategory`** (NEW — `agent_runtime/api/constants.py`):

```python
class ApprovalCategory(StrEnum):
    READ = "read"
    WRITE = "write"
    ACTION = "action"
```

**`ApprovalReasonCode`** (NEW — `agent_runtime/api/constants.py`):

```python
class ApprovalReasonCode(StrEnum):
    READ_ONLY_FIRST_USE = "read_only_first_use"
    WRITES_OUT_OF_WORKSPACE = "writes_out_of_workspace"
    RISK_HIGH = "risk_high"
    IRREVERSIBLE = "irreversible"
    DEFAULT = "default"
```

**`ApprovalReversible`** (NEW — `agent_runtime/api/constants.py`):

```python
class ApprovalReversible(StrEnum):
    YES = "yes"
    NO = "no"
    NOT_APPLICABLE = "n/a"
```

**`ApprovalParam`** (NEW — `runtime_api/schemas/approvals.py`):

```python
class ApprovalParam(RuntimeContract):
    label: Annotated[str, StringConstraints(min_length=1, max_length=24)]
    value: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    hint: Annotated[str, StringConstraints(max_length=80)] | None = None
```

**`McpApprovalMetadata`** (NEW — `runtime_api/schemas/approvals.py`). Lives inside `ApprovalRequestRecord.metadata` for `approval_kind == "mcp_tool"`. Validated on construction, projected back to a JSON-serialisable dict for the existing `metadata: JsonObject` slot via `model_dump(mode="json")`.

```python
class McpApprovalMetadata(RuntimeContract):
    """Structured metadata for `approval_kind == "mcp_tool"`.

    Round-trips through the existing `ApprovalRequestRecord.metadata`
    JsonObject — no schema migration. Validated on emit (worker) and on
    read (API layer) so the FE never sees malformed payloads. Any
    additional keys the runtime stuffs into metadata pass through
    unchanged via `model_config = {"extra": "allow"}`.
    """

    model_config = ConfigDict(extra="allow")

    vendor: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    category: ApprovalCategory
    reason_code: ApprovalReasonCode
    reversible: ApprovalReversible = ApprovalReversible.NOT_APPLICABLE
    params: tuple[ApprovalParam, ...] = ()

    @field_validator("params")
    @classmethod
    def _max_six(cls, value: tuple[ApprovalParam, ...]) -> tuple[ApprovalParam, ...]:
        if len(value) > 6:
            raise ValueError("approval params capped at 6 rows")
        return value
```

The flat fields the worker already emits (`display_name`, `tool_name`, `server_name`, `read_only`, `risk_level`, `message`) **stay** — they're still useful for the receipt title, the `tool_name` debug pane, and AssignedApproval inbox rows. The new fields are additive.

### 2.2 Wire — runtime worker emission

**`stream_events.py::_build_mcp_tool_approvals`** — extend after the existing flat-field assembly:

```python
metadata = McpApprovalMetadata(
    vendor=cls._vendor_token(display_name),
    category=cls._category_for(read_only),
    reason_code=cls._reason_code_for(read_only, risk_level, is_first_use),
    reversible=cls._reversible_for(read_only, tool_name),
    params=cls._params_from_arguments(arguments, tool_name),
).model_dump(mode="json")
approvals.append({
    # existing flat fields (unchanged) …
    **metadata,                      # spread the new fields next to flats
})
```

#### Vendor token

```python
@classmethod
def _vendor_token(cls, display_name: str) -> str:
    return display_name.upper()[:32] or "CONNECTOR"
```

#### Category

```python
@classmethod
def _category_for(cls, read_only: bool) -> ApprovalCategory:
    return ApprovalCategory.READ if read_only else ApprovalCategory.WRITE
```

(Phase 2 keeps the binary mapping. Phase 3 introduces `ACTION` for connector calls that aren't strict CRUD — e.g., Zapier triggers — once we have a tool descriptor flag for it.)

#### Reason code

```python
@classmethod
def _reason_code_for(
    cls,
    read_only: bool,
    risk_level: str,
    is_first_use: bool,
) -> ApprovalReasonCode:
    if risk_level in {"high", "critical"}:
        return ApprovalReasonCode.RISK_HIGH
    if read_only and is_first_use:
        return ApprovalReasonCode.READ_ONLY_FIRST_USE
    if not read_only:
        return ApprovalReasonCode.WRITES_OUT_OF_WORKSPACE
    return ApprovalReasonCode.DEFAULT
```

`is_first_use` is a soft signal we already have on the worker — it's the `tool_loaded_count[server_name]` counter the runtime maintains for telemetry. If unavailable, default `False` (the FE then renders `WRITES_OUT_OF_WORKSPACE` for writes, `DEFAULT` for reads, which matches Phase 1 today).

#### Reversibility

```python
@classmethod
def _reversible_for(
    cls,
    read_only: bool,
    tool_name: str,
) -> ApprovalReversible:
    if read_only:
        return ApprovalReversible.NOT_APPLICABLE
    # Phase 2 default: writes are non-reversible until the tool descriptor
    # opts in. Phase 4 wires a real `undo_window_seconds` from the tool card.
    if any(token in tool_name.lower() for token in ("delete", "remove", "drop")):
        return ApprovalReversible.NO
    return ApprovalReversible.NO  # explicit default — no implicit "yes"
```

#### Params from arguments

```python
@classmethod
def _params_from_arguments(
    cls,
    arguments: object,
    tool_name: str,
) -> tuple[ApprovalParam, ...]:
    if not isinstance(arguments, Mapping):
        return ()
    params: list[ApprovalParam] = []
    for key in cls._SAFE_PARAM_KEYS:
        if key not in arguments:
            continue
        value = cls._stringify_arg(arguments[key])
        if value is None:
            continue
        params.append(
            ApprovalParam(label=cls._humanize_key(key), value=value)
        )
        if len(params) >= 6:
            break
    return tuple(params)
```

`_SAFE_PARAM_KEYS` is a curated allow-list — `("channel", "to", "recipient", "team", "project", "repo", "ref", "branch", "issue", "page_id", "database_id", "subject", "title", "id", "name", "query", "filter", "assignee", "label")`. **Allow-list, not block-list**: secrets / tokens / freeform body text never project. The `body`, `text`, `password`, `api_key`, `description` fields are excluded by omission. `_stringify_arg` enforces a 128-char cap and falls back to the type name (`"<list of 4 items>"`) for non-scalars.

(The allow-list is the simplest safe projection and is what the user journey demands — the design's `CHANNEL · #launch-aurora` row comes from `arguments["channel"]`. Vendor-specific recognisers that turn `channel="C0123"` into `#launch-aurora · 14 members` are Phase 3.)

### 2.3 Wire — api-types mirror

```ts
// packages/api-types/src/index.ts

export type McpApprovalCategory = "read" | "write" | "action";
export type McpApprovalReasonCode =
  | "read_only_first_use"
  | "writes_out_of_workspace"
  | "risk_high"
  | "irreversible"
  | "default";
export type McpApprovalReversible = "yes" | "no" | "n/a";

export interface McpApprovalParam {
  label: string;
  value: string;
  hint?: string | null;
}

// Extends the existing ToolCallArgs shape used by ApprovalTool.tsx —
// fields are optional so old events still parse.
export interface McpApprovalArgsExtension {
  vendor?: string;
  category?: McpApprovalCategory;
  reason_code?: McpApprovalReasonCode;
  reversible?: McpApprovalReversible;
  params?: McpApprovalParam[];
}
```

Frontend reads them via `args.params` etc., the same property bag that already carries `display_name` / `tool_name` / `read_only`.

### 2.4 Frontend — read structured fields

**New file** — `apps/frontend/src/features/chat/utils/approvalCopy.ts`:

```ts
const REASON_COPY: Record<McpApprovalReasonCode, string> = {
  read_only_first_use:
    "Atlas is asking before reading from this connector for the first time this turn.",
  writes_out_of_workspace:
    "Atlas is asking because this writes outside your workspace.",
  risk_high:
    "Atlas is asking because this writes to a high-risk connector — review the scope below.",
  irreversible: "Atlas is asking because this action can't be undone.",
  default: "Atlas is asking before running this connector.",
};

export function approvalReason(
  serverSupplied: McpApprovalReasonCode | undefined,
  fallback: string,
): string {
  return serverSupplied ? REASON_COPY[serverSupplied] : fallback;
}
```

**`mcpApprovalCategory`** in `toolLabels.ts` — read server-supplied first:

```ts
export function mcpApprovalCategory(
  args: { vendor?: string; category?: McpApprovalCategory },
  displayName: string | null,
  readOnly: boolean | null,
): { vendor: string; access: "READ" | "WRITE" | "ACTION" } {
  if (args.vendor && args.category) {
    return { vendor: args.vendor, access: args.category.toUpperCase() as ... };
  }
  // existing inference path …
}
```

**`ApprovalTool.tsx`** — read `args.params`:

```ts
const params: ActivityParam[] =
  Array.isArray(args.params) && args.params.length > 0
    ? args.params.map((p) => ({ label: p.label, value: p.value }))
    : [
        ...(riskLevel ? [{ label: "Risk", value: capitalize(riskLevel) }] : []),
        ...(readOnly !== null
          ? [
              {
                label: "Access",
                value: readOnly ? "Read-only" : "May change data",
              },
            ]
          : []),
      ];
```

### 2.5 Forward & backward compatibility

| Client                    | Server                    | Behaviour                                                                                              |
| ------------------------- | ------------------------- | ------------------------------------------------------------------------------------------------------ |
| Phase 1 client (no reads) | Phase 2 server            | Extra `vendor` / `category` / `params` fields on `args` are ignored. Phase-1 synthesisers run. ✅      |
| Phase 2 client            | Phase 1 server (no emits) | All structured fields `undefined`. Reader falls through to `fallback`. Phase-1 synthesisers render. ✅ |
| Phase 2 client            | Phase 2 server            | Reads server-supplied. ✅                                                                              |

The contract is **purely additive on the wire**. No version flag. No feature gate.

---

## 3 · Architecture & invariants

### 3.1 Service boundaries

- `runtime_worker` builds the metadata via `McpApprovalMetadata`. Pydantic-validated before serialisation. Validation failure → drop the structured fields, keep flat fields, log a warning. **The approval still ships** — the FE falls back gracefully.
- `runtime_api` reads the metadata back out of `ApprovalRequestRecord.metadata` (a JsonObject already) for the inbox / replay endpoints. No new endpoint. No change to `/v1/agent/runs/{id}/events` or `/v1/agent/runs/{id}/stream`.
- `backend-facade` proxies the same shape. Zero change.
- `apps/frontend` reads via the same `args` bag. No new API client.

### 3.2 Untrusted-input handling

- **Tool arguments** are model-controlled output (the LLM picked them). The allow-listed projection (`_SAFE_PARAM_KEYS`) and `_stringify_arg`'s 128-char cap are the trust boundary. A malicious model can't smuggle `<script>` or a 10MB blob into the consent card.
- **`vendor`** is bounded to 32 chars and uppercased. No HTML interpolation on the FE.
- **`reason_code` / `category` / `reversible`** are enum-validated server-side. The FE indexes a `Record<>` by them; an unknown value (impossible after validation) would type-error.

### 3.3 Streaming invariants

- Events still carry monotonic `sequence_no` per run.
- `approval_requested` shape extends; `event_type` is unchanged. Replay-by-sequence is unaffected.
- The structured payload is part of `metadata`, persisted on the same row. SSE clients reconnecting via `?after_sequence=N` pick up unchanged.

### 3.4 Where the boundary lives

```
┌──────────────────────────────────────────────────────────────────┐
│  runtime_worker · _build_mcp_tool_approvals                      │
│    ├─ flat fields (existing) ────────────────────────┐           │
│    └─ McpApprovalMetadata (NEW, Pydantic-validated) ─┤           │
│                                                      ▼           │
│                            ApprovalRequestRecord.metadata        │
│                                  (JsonObject, persisted)         │
└──────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│  runtime_api · stream / replay / inbox (no change)               │
└──────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│  apps/frontend · ApprovalTool reads args.{vendor,category,…}     │
│    └─ falls back to Phase-1 synthesisers when undefined          │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4 · Test plan

### 4.1 ai-backend

- `tests/unit/runtime_worker/test_stream_events.py` — extend with:
  - `test_mcp_tool_approval_emits_structured_metadata_read_only`
  - `test_mcp_tool_approval_emits_structured_metadata_write`
  - `test_mcp_tool_approval_param_allowlist_drops_secrets`
  - `test_mcp_tool_approval_param_count_capped_at_six`
  - `test_mcp_tool_approval_risk_high_reason_code`
- `tests/unit/runtime_api/test_approval_metadata.py` (NEW) — `McpApprovalMetadata` validator: enum-valid, `params <= 6`, `vendor` non-empty, `extra="allow"` round-trips unknown keys.

### 4.2 frontend

- `ApprovalTool.test.tsx` — extend with:
  - `it("renders server-supplied params verbatim when provided")`
  - `it("falls back to Risk + Access when params are absent")`
  - `it("renders the high-risk reason sentence when reason_code === 'risk_high'")`
- `approvalCopy.test.ts` (NEW) — every enum variant maps to a non-empty string; missing variant → fallback.

### 4.3 contract

- `packages/api-types` typecheck passes with the new exports.
- An integration test in `apps/frontend/src/features/chat/AssistantMessage.integration.test.tsx` confirms a Phase-1 event (no structured fields) still renders.

---

## 5 · Sequencing

1. ai-backend: add enums + `ApprovalParam` + `McpApprovalMetadata`. Land tests for the validator standalone.
2. ai-backend: extend `_build_mcp_tool_approvals`. Land `stream_events` tests.
3. api-types: add the type mirrors. Typecheck.
4. frontend: add `approvalCopy.ts`; rewire `mcpApprovalCategory` + `mcpApprovalReason`; wire `ApprovalTool` reads. Land tests.
5. Verify the integration test for Phase-1 events still passes (the fallback path).

Each step is independently mergeable. Step 4 can land before step 2 because the FE handles `undefined` structured fields.

---

## 6 · Risk register

| Risk                                                                                                                                                 | Mitigation                                                                                                                                                                 |
| ---------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Allow-list misses a vendor's primary key (e.g., Notion uses `parent.database_id`).                                                                   | Phase 3 introduces vendor recognisers. Phase 2 ships a generic projection that covers Slack/Linear/GitHub key vocabulary. Adding a key to the list is a one-line PR.       |
| `risk_level` from existing emitter is hard-coded to `"low"`/`"medium"` — `RISK_HIGH` reason code never fires until the policy module emits `"high"`. | Documented as Phase 2 wire-only. Policy work is sequenced after this PR; the door is open.                                                                                 |
| Pydantic validation throws on a malformed run.                                                                                                       | Worker catches the `ValidationError`, logs a warning, drops the structured fields, ships the approval with flats only. FE falls back.                                      |
| FE consumer regresses by always preferring structured fields and a server bug ships `category="invalid"`.                                            | Pydantic validates on emit. If a server still ships an invalid value, the FE's `Record<McpApprovalReasonCode, string>` lookup returns `undefined` → fallback path renders. |

---

## 7 · Out-of-scope follow-ups (future PRs)

- **Phase 3 — Vendor-specific param recognisers.** Slack channel resolver, GitHub PR-number labeller, Linear team-name resolver. Each is a focussed PR adding one recogniser.
- **Phase 4 — Reversibility actions.** Wire an "Undo" button to `reversible="yes"` approvals. Needs server-side undo token + 60s window.
- **Phase 5 — Risk policy emit upgrade.** Replace the `low`/`medium` short-circuit with the real `permissions.py` policy output. Unblocks `RISK_HIGH` reason code in production.
- **Audit-log surfacing.** Render `reason_code` + `category` in the audit timeline UI.
- **i18n.** Move `REASON_COPY` to the i18n layer once it exists.

# PR 4.4.6.3 — Approval card: vendor-specific param recognisers

> **Status:** Draft (PRD + Spec)
> **Plan reference:** Phase 3 of the consent-card redesign. Phases 1 and 2 shipped:
>
> - PR 4.4.6.1 — `ApprovalCard` + `ApprovalReceipt` components, copy helpers, button hierarchy.
> - PR 4.4.6.2 — structured wire payload (`vendor`, `category`, `reason_code`, `reversible`, `params`) emitted by the runtime worker.
>
> **Owner:** ai-backend (new `approval_recognisers.py` module — one ABC + 5 subclasses + registry; integrate into `_build_mcp_tool_approvals`) · api-types · backend / backend-facade / frontend / design-system (zero — recognisers are pure server-side projection).
> **Size:** **S/M.** Pure runtime-worker projection logic; no schema, contract, or wire change. ~280 LoC including tests.
> **Depends on:**
>
> - ✅ PR 4.4.6.2 — wire schema for `params: list[ApprovalParam]` and the generic allow-list projector (`_approval_params`) the recognisers slot in front of.
>
> **Reads alongside:**
>
> - [`pr-4.4.6.2-approval-card-structured-payload.md`](pr-4.4.6.2-approval-card-structured-payload.md) — the wire shape we're populating better.
> - [`services/ai-backend/CLAUDE.md`](../../services/ai-backend/CLAUDE.md) — Pydantic at every IO boundary; helpers belong inside classes; no module-level helpers; treat connector / tool payloads as untrusted.

---

## 0 · TL;DR

Phase 2 ships a flat allow-list projector: it walks `arguments` for keys like `channel`, `team`, `repo`, stringifies each value once, and emits up to 6 `ApprovalParam` rows. That works for "what are the keys?" but not for "what does the call _mean_?":

| Vendor    | Today's projection                                                                         | What the user actually wants                                                                  |
| --------- | ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------- |
| Slack     | `Channel: C0123ABC` (raw ID) · `Text: …` (excluded) · separate `thread_ts: 1700000000.001` | `Channel: #launch-aurora` · `In thread: Yes` (one composed row instead of a numeric Slack TS) |
| GitHub    | `Repo: api` · `Owner: acme` (separate rows) · `Pull number: 42`                            | `Repo: acme/api · #42` (composed)                                                             |
| Linear    | `Team: TEAM-1` · `Priority: 2`                                                             | `Team: TEAM-1` · `Priority: P2 (High)`                                                        |
| Notion    | `Page id: abc-123` (raw UUID) · separate `parent: {database_id: …}` not projected at all   | `Database: <id> · Action: Create page`                                                        |
| Atlassian | `Project: PROJ-123` · separate `issue_type: Bug` not projected at all                      | `Project: PROJ-123 · Bug`                                                                     |

Phase 3 adds a tiny, synchronous, server-side projection layer per vendor. Each `ApprovalParamRecogniser` class:

1. Matches a server slug (e.g., `slack`, `github`, `linear`, `notion`, `atlassian`).
2. Reads known argument keys for that vendor.
3. Returns up to 6 `ApprovalParam` rows that compose / relabel / re-order what the call actually does.

When no recogniser matches, we fall back to the generic allow-list from PR 4.4.6.2 — **no behaviour change for unknown vendors**.

| Surface                                           | Today (Phase 2)                                                              | After this PR                                                                                                                                                               |
| ------------------------------------------------- | ---------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_build_mcp_tool_approvals` in `stream_events.py` | Flat allow-list: `_approval_params(arguments)` walks `_APPROVAL_PARAM_KEYS`. | Try `ApprovalParamRecogniserRegistry.recognise(server_name, arguments)` first; fall through to the generic allow-list when no recogniser hits.                              |
| `runtime_worker/approval_recognisers.py`          | Doesn't exist.                                                               | New module. ABC `ApprovalParamRecogniser` + 5 subclasses (`SlackRecogniser`, `GitHubRecogniser`, `LinearRecogniser`, `NotionRecogniser`, `AtlassianRecogniser`) + registry. |
| Unknown vendor (e.g., custom URL)                 | Generic allow-list path (one-row stringification per key).                   | **Identical** — no recogniser matches → fall through to existing path.                                                                                                      |
| Wire shape                                        | `params: list[{label, value, hint?}]`                                        | **Unchanged.** `ApprovalParam` Pydantic schema still validates.                                                                                                             |
| Frontend                                          | Reads `args.params`.                                                         | **No change.** The richer values arrive on the same field.                                                                                                                  |
| api-types                                         | `McpApprovalParam`.                                                          | **No change.**                                                                                                                                                              |

LoC estimate: **ai-backend ≈ 240** (`approval_recognisers.py` ≈ 200 incl. all 5 subclasses, registry hookup +20, integration in `stream_events.py` +20) · **tests ≈ 120** (one test per vendor + registry / fallback / order tests) · **frontend / api-types / backend / backend-facade ≈ 0**.

The four runtime / streaming invariants (frozen at run-start, binary at runtime, no new event type, single PATCH endpoint) are preserved — this PR only changes the **content** of `params`, not the wire shape, transport, or schema.

---

## 1 · PRD

### 1.1 Problem

The Phase 2 allow-list works because tool calls share generic key names (`channel`, `team`, `repo`). But that shared vocabulary hides three failure modes:

1. **Raw IDs leak.** Slack `chat.postMessage(channel="C0123ABC")` projects `Channel: C0123ABC`. The user has no idea what channel that is, so they can't make a real consent decision. (Friendly resolution to `#launch-aurora` requires Slack-side metadata we don't have, but we can at least format it as `<channel id>` and add the workspace prefix.)
2. **Composed concepts split.** GitHub `create_pull_request(owner="acme", repo="api", base="main", head="feat-y")` becomes two unrelated rows (`Owner` + `Repo`). The user thinks "what's this PR against?" and has to mentally combine them. Worse — `pull_number` is its own row, not a `Repo: acme/api · #42` reference.
3. **Domain values stay numeric.** Linear `create_issue(priority=1)` projects `Priority: 1`. The user has to know Linear's priority enum (`0=No priority, 1=Urgent, 2=High, 3=Medium, 4=Low`). The runtime knows the vendor; we can decode.

The fix is small: per vendor, write a recogniser that knows the call vocabulary. Six rows max, deterministic order, sentence-case labels. Fall back to the generic projector when no recogniser exists — so adding a vendor is purely additive.

### 1.2 Goals

1. **One recogniser per first-class vendor.** Slack, GitHub, Linear, Notion, Atlassian. These are the five connectors that ship with `requires_pre_registered_client` flagged or not in the catalog and dominate real consent volume.
2. **Recognisers are pure functions.** Input: `arguments: Mapping[str, object]`. Output: `tuple[ApprovalParam, ...]`. No I/O, no caches, no MCP roundtrips. Sub-millisecond.
3. **Server-slug match drives dispatch.** `server_name` (e.g., `mcp_slack_com`) tokenised against a vendor key. Order is deterministic and tested.
4. **Fall through is byte-identical to Phase 2.** Unknown vendor → existing `_approval_params(arguments)` path. No regressions.
5. **Pydantic validation stays at the IO boundary.** Each recogniser returns `tuple[ApprovalParam, ...]`. The 6-row cap and value-length cap are still enforced by `McpApprovalMetadata` so a buggy recogniser can't bypass safety.
6. **No coupling to MCP descriptors.** Recognisers read the inbound `arguments` only. The vendor's _declared_ tool schema is out of scope here — Phase 4 territory if we ever wire tool-card descriptors into the consent path.
7. **Adding a vendor is a one-class change.** New `class FoobarRecogniser(ApprovalParamRecogniser)` + register. No allow-list edits, no enum changes.

### 1.3 Non-goals

- **Live MCP server introspection.** No "look up channel ID → friendly name" calls. Friendly resolution is a future consent enrichment that needs caching and timeouts. Phase 3 only re-arranges what the agent already knows.
- **Static tool descriptors.** Some MCPs ship JSON-schema descriptions per tool. We don't read them. (Phase 4 candidate.)
- **i18n.** Recogniser-emitted strings stay English. The reason-code → sentence map is already at the FE layer; recogniser values are data, not UI copy.
- **Cross-vendor compositions.** A "GitHub PR mentioned in Slack" call doesn't get a combined recogniser. Each MCP server is its own unit.
- **Catalog-driven recogniser_id field.** Tempting, but adds catalog coupling for a thing the server-name tokeniser already does well. Keep it in the runtime worker.
- **Recogniser hot-reload / config-driven.** Recognisers are code; adding one is a PR. (We may revisit if the matrix grows past ~15 vendors.)

### 1.4 Success criteria

- ✅ `ApprovalParamRecogniser` ABC lives in `services/ai-backend/src/runtime_worker/approval_recognisers.py`. One abstract method (`recognise`) and one classmethod (`matches_server_name`).
- ✅ Five concrete recognisers: `SlackApprovalRecogniser`, `GitHubApprovalRecogniser`, `LinearApprovalRecogniser`, `NotionApprovalRecogniser`, `AtlassianApprovalRecogniser`.
- ✅ `ApprovalParamRecogniserRegistry` exposes `recognise(server_name, arguments) -> tuple[ApprovalParam, ...] | None`. Returns `None` (not empty) when no vendor matches; the worker falls through.
- ✅ `_build_mcp_tool_approvals` calls the registry first; on `None`, calls the existing `_approval_params`.
- ✅ Slack: composes `channel`, `text` flag (truncates to byte length), `thread_ts` into `Channel`, `In thread` rows. Excludes raw text.
- ✅ GitHub: composes `owner` + `repo` into `Repo: acme/api`; if `pull_number` present, appends `· #42` to the same row. Adds `Branch` row when `head`/`base` present.
- ✅ Linear: maps `priority` int → `P0–P4` enum. Composes `team` + `project` into one row. Adds `Assignee` row when `assignee` present.
- ✅ Notion: composes `parent.database_id` or `parent.page_id` into `Parent` row. Decodes `properties.title` into a `Title` row when present.
- ✅ Atlassian: composes `project` + `issue_type` into one row. Adds `Summary` row when `summary` present.
- ✅ Unknown vendor (server_name="mcp_acme_com") → registry returns `None` → existing generic projector runs unchanged.
- ✅ Each recogniser caps at `APPROVAL_MAX_PARAMS` rows; values cap at the same 128-char limit as the generic path.
- ✅ Unit tests for each recogniser cover: minimum-input shape, composed-key shape, all keys absent, malformed value type. `tests/unit/runtime_worker/test_approval_recognisers.py`.
- ✅ `tests/unit/runtime_worker/test_stream_events.py` extended: integration test asserting Slack args produce composed rows; unknown vendor still produces generic rows.
- ✅ `pytest tests/unit/runtime_worker tests/unit/runtime_api/test_approval_metadata.py` is green.
- ✅ Frontend / api-types untouched (no rebuild needed).

### 1.5 User stories

| #    | Persona                           | Story                                                                                                                                                                                        |
| ---- | --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US-1 | Sarah · Slack write               | Atlas wants to post in `#launch-aurora`. Card shows `Channel: #launch-aurora` (one row) + `In thread: Yes` (one row), no leaked thread timestamp, no raw text. She approves with confidence. |
| US-2 | Marcus · GitHub PR review         | Atlas wants to comment on a PR. Card shows one row: `Repo: acme/api · #42` — not three split rows. He clicks the repo identifier mentally and approves.                                      |
| US-3 | Sarah · Linear urgent issue       | Card shows `Priority: P1 (Urgent)` — readable enum, not `Priority: 1`. She immediately understands and approves.                                                                             |
| US-4 | Marcus · custom-URL connector     | His workspace has a self-hosted MCP server `mcp.acme-internal.dev`. No vendor recogniser matches. The card falls back to the generic allow-list — same Phase-2 behaviour. No regression.     |
| US-5 | Sarah · auditor                   | The audit log carries the same params blob the user saw. Reading old (Phase 2) and new (Phase 3) approvals side-by-side, the new ones show richer rows but the same enum-validated shape.    |
| US-6 | Workspace admin · adding a vendor | A new MCP for "Calendly" needs a recogniser. Adding `class CalendlyApprovalRecogniser` + a one-line registry entry is the entire patch — no schema, no api-types, no FE change.              |

---

## 2 · Spec

### 2.1 Module — `runtime_worker/approval_recognisers.py`

```python
"""Vendor-specific projection from raw tool call arguments to consent-card
``ApprovalParam`` rows. Server-side, synchronous, no I/O.

Phase 3 of the consent-card redesign. Phase 2 ships the wire schema and
a generic allow-list projector; this module fronts that path with one
recogniser per first-class vendor so the user sees ``Repo: acme/api ·
#42`` instead of three split rows.

Adding a vendor:

  1. Subclass ``ApprovalParamRecogniser``.
  2. Implement ``matches_server_name`` and ``recognise``.
  3. Append to ``ApprovalParamRecogniserRegistry._RECOGNISERS``.

No catalog edit, no schema edit, no FE edit.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import ClassVar

from runtime_api.schemas.approvals import APPROVAL_MAX_PARAMS, ApprovalParam


class ApprovalParamRecogniser(ABC):
    """Base class for one vendor's projection logic.

    Concrete subclasses must declare ``vendor_tokens`` — substrings that,
    when present in the lowercased ``server_name``, claim the call.
    Tokens are compared *after* stripping the ``mcp_`` / ``_mcp`` /
    ``_com`` / ``-com`` decoration the runtime appends for transport
    bookkeeping (mirrors ``StreamOrchestrator._connector_display_name``).
    """

    vendor_tokens: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def matches_server_name(cls, server_name: str) -> bool:
        normalized = cls._normalize_server_name(server_name)
        return any(token in normalized for token in cls.vendor_tokens)

    @staticmethod
    def _normalize_server_name(value: str) -> str:
        normalized = value.strip().lower()
        if normalized.startswith("mcp_"):
            normalized = normalized[len("mcp_"):]
        if normalized.endswith("_mcp"):
            normalized = normalized[: -len("_mcp")]
        return normalized.removesuffix("_com").removesuffix("-com")

    @abstractmethod
    def recognise(
        self, arguments: Mapping[str, object]
    ) -> tuple[ApprovalParam, ...]:
        """Return up to ``APPROVAL_MAX_PARAMS`` rows for this vendor."""


class ApprovalParamRecogniserRegistry:
    """Central registry. Order in ``_RECOGNISERS`` is the dispatch
    priority — first match wins."""

    _RECOGNISERS: ClassVar[tuple[ApprovalParamRecogniser, ...]] = ()  # set below

    @classmethod
    def recognise(
        cls, *, server_name: str, arguments: Mapping[str, object]
    ) -> tuple[ApprovalParam, ...] | None:
        for recogniser in cls._RECOGNISERS:
            if recogniser.matches_server_name(server_name):
                return recogniser.recognise(arguments)[:APPROVAL_MAX_PARAMS]
        return None
```

Followed by the five concrete classes:

```python
class SlackApprovalRecogniser(ApprovalParamRecogniser):
    vendor_tokens = ("slack",)

    def recognise(self, arguments: Mapping[str, object]) -> tuple[ApprovalParam, ...]:
        params: list[ApprovalParam] = []
        channel = self._stringify(arguments.get("channel"))
        if channel:
            params.append(ApprovalParam(label="Channel", value=channel))
        thread_ts = arguments.get("thread_ts")
        if thread_ts:
            params.append(ApprovalParam(label="In thread", value="Yes"))
        elif "thread_ts" in arguments:
            params.append(ApprovalParam(label="In thread", value="No"))
        recipient = self._stringify(arguments.get("user") or arguments.get("to"))
        if recipient:
            params.append(ApprovalParam(label="Recipient", value=recipient))
        return tuple(params)

    @staticmethod
    def _stringify(raw: object) -> str | None: ...
```

```python
class GitHubApprovalRecogniser(ApprovalParamRecogniser):
    vendor_tokens = ("github",)

    def recognise(self, arguments: Mapping[str, object]) -> tuple[ApprovalParam, ...]:
        owner = self._stringify(arguments.get("owner") or arguments.get("org"))
        repo = self._stringify(arguments.get("repo"))
        pr_number = arguments.get("pull_number") or arguments.get("number")
        params: list[ApprovalParam] = []
        if owner and repo:
            value = f"{owner}/{repo}"
            if pr_number:
                value = f"{value} · #{pr_number}"
            params.append(ApprovalParam(label="Repo", value=value))
        elif repo:
            params.append(ApprovalParam(label="Repo", value=repo))
        head = self._stringify(arguments.get("head"))
        base = self._stringify(arguments.get("base"))
        if head and base:
            params.append(ApprovalParam(label="Branch", value=f"{head} → {base}"))
        elif head or base:
            params.append(ApprovalParam(label="Branch", value=head or base))
        title = self._stringify(arguments.get("title"))
        if title:
            params.append(ApprovalParam(label="Title", value=title))
        return tuple(params)
```

```python
class LinearApprovalRecogniser(ApprovalParamRecogniser):
    vendor_tokens = ("linear",)

    _PRIORITY: ClassVar[dict[int, str]] = {
        0: "No priority",
        1: "P1 (Urgent)",
        2: "P2 (High)",
        3: "P3 (Medium)",
        4: "P4 (Low)",
    }

    def recognise(self, arguments: Mapping[str, object]) -> tuple[ApprovalParam, ...]:
        team = self._stringify(arguments.get("team") or arguments.get("team_id"))
        project = self._stringify(arguments.get("project") or arguments.get("project_id"))
        params: list[ApprovalParam] = []
        if team and project:
            params.append(ApprovalParam(label="Scope", value=f"{team} / {project}"))
        elif team:
            params.append(ApprovalParam(label="Team", value=team))
        elif project:
            params.append(ApprovalParam(label="Project", value=project))
        priority = arguments.get("priority")
        if isinstance(priority, int) and priority in self._PRIORITY:
            params.append(ApprovalParam(label="Priority", value=self._PRIORITY[priority]))
        title = self._stringify(arguments.get("title"))
        if title:
            params.append(ApprovalParam(label="Title", value=title))
        assignee = self._stringify(arguments.get("assignee") or arguments.get("assignee_id"))
        if assignee:
            params.append(ApprovalParam(label="Assignee", value=assignee))
        return tuple(params)
```

```python
class NotionApprovalRecogniser(ApprovalParamRecogniser):
    vendor_tokens = ("notion",)

    def recognise(self, arguments: Mapping[str, object]) -> tuple[ApprovalParam, ...]:
        params: list[ApprovalParam] = []
        parent = arguments.get("parent")
        if isinstance(parent, Mapping):
            db_id = self._stringify(parent.get("database_id"))
            page_id = self._stringify(parent.get("page_id"))
            if db_id:
                params.append(ApprovalParam(label="Database", value=db_id))
            elif page_id:
                params.append(ApprovalParam(label="Parent page", value=page_id))
        page_id = self._stringify(arguments.get("page_id"))
        if page_id and not any(p.label == "Parent page" for p in params):
            params.append(ApprovalParam(label="Page", value=page_id))
        title = self._extract_title(arguments)
        if title:
            params.append(ApprovalParam(label="Title", value=title))
        return tuple(params)

    @classmethod
    def _extract_title(cls, arguments: Mapping[str, object]) -> str | None:
        title = arguments.get("title")
        if isinstance(title, str):
            return title.strip() or None
        properties = arguments.get("properties")
        if isinstance(properties, Mapping):
            prop_title = properties.get("title")
            if isinstance(prop_title, str):
                return prop_title.strip() or None
        return None
```

```python
class AtlassianApprovalRecogniser(ApprovalParamRecogniser):
    vendor_tokens = ("atlassian", "jira", "confluence")

    def recognise(self, arguments: Mapping[str, object]) -> tuple[ApprovalParam, ...]:
        project = self._stringify(arguments.get("project") or arguments.get("project_key"))
        issue_type = self._stringify(arguments.get("issue_type") or arguments.get("issuetype"))
        params: list[ApprovalParam] = []
        if project and issue_type:
            params.append(ApprovalParam(label="Project", value=f"{project} · {issue_type}"))
        elif project:
            params.append(ApprovalParam(label="Project", value=project))
        issue = self._stringify(arguments.get("issue") or arguments.get("issue_key"))
        if issue:
            params.append(ApprovalParam(label="Issue", value=issue))
        summary = self._stringify(arguments.get("summary"))
        if summary:
            params.append(ApprovalParam(label="Summary", value=summary))
        return tuple(params)
```

Followed by:

```python
ApprovalParamRecogniserRegistry._RECOGNISERS = (
    SlackApprovalRecogniser(),
    GitHubApprovalRecogniser(),
    LinearApprovalRecogniser(),
    NotionApprovalRecogniser(),
    AtlassianApprovalRecogniser(),
)
```

A shared `_stringify` lives on the ABC for re-use across recognisers (DRY across 5 subclasses; per CLAUDE.md "keep production helper behavior **inside** classes").

### 2.2 Integration in `_build_mcp_tool_approvals`

```python
recognised = ApprovalParamRecogniserRegistry.recognise(
    server_name=server_name, arguments=arguments_mapping
)
params = recognised if recognised is not None else cls._approval_params(arguments_mapping)
metadata = McpApprovalMetadata(
    vendor=cls._approval_vendor(display_name),
    category=cls._approval_category(read_only),
    reason_code=cls._approval_reason_code(read_only, risk_level),
    reversible=cls._approval_reversible(read_only, tool_name),
    params=params,
)
```

The 6-row cap is still enforced by `McpApprovalMetadata` (validator at IO boundary). Recognisers can over-emit; the schema clamps.

### 2.3 Server-name match table

| `server_name` value (post-normalisation) | Recogniser hit                   |
| ---------------------------------------- | -------------------------------- |
| `slack`                                  | `SlackApprovalRecogniser`        |
| `github`                                 | `GitHubApprovalRecogniser`       |
| `linear`                                 | `LinearApprovalRecogniser`       |
| `notion`                                 | `NotionApprovalRecogniser`       |
| `atlassian` / `jira` / `confluence`      | `AtlassianApprovalRecogniser`    |
| `clickup`, `asana`, anything else        | `None` → generic allow-list path |

Normalisation strips `mcp_` / `_mcp` / `_com` / `-com` before tokenising. `mcp_slack_com` → `slack`.

### 2.4 No wire / contract change

`McpApprovalMetadata.params` is the same `tuple[ApprovalParam, ...]`. `ApprovalParam.label` and `value` strings carry the new vocabulary directly. Frontend `ApprovalCard` already renders them verbatim (PR 4.4.6.2 §2.4). No FE work.

### 2.5 Failure modes

- **Recogniser raises.** Bug. The validator boundary (`McpApprovalMetadata`) catches it as `ValidationError`; the worker logs and falls back to the generic projector. The approval still ships.
- **Recogniser returns malformed params** (e.g., empty label). `ApprovalParam`'s field validators reject; same fallback path.
- **Recogniser over-caps.** Slice `[:APPROVAL_MAX_PARAMS]` in the registry guarantees we never exceed even before validation.
- **No recogniser hits.** `recognise` returns `None`. Worker calls `_approval_params` — Phase 2 behaviour.
- **Vendor rename / domain change.** Tokens are flexible (`atlassian`, `jira`, `confluence` all match the same recogniser). Adding a token is a one-line change.

---

## 3 · Architecture & invariants

### 3.1 Service boundaries

- `runtime_worker/approval_recognisers.py` is owned by the worker process. The runtime API doesn't import it. backend / backend-facade / frontend / api-types are untouched.
- Recognisers depend only on `runtime_api/schemas/approvals.py` (`ApprovalParam`, `APPROVAL_MAX_PARAMS`). No new cross-module coupling.
- Adding a vendor is local: one new class, one registry append. No PR ripples through api-types or FE.

### 3.2 Untrusted-input handling

- Tool arguments arrive from the LLM. Recognisers treat them as untyped `Mapping[str, object]`. Type checks (`isinstance`) gate every read.
- The 128-char value cap from `ApprovalParam`'s validator stops a malicious model from packing a recogniser's row with a 100KB blob.
- The 6-row cap from `McpApprovalMetadata` is the upper bound regardless of recogniser output.
- Composed values (`f"{owner}/{repo}"`) inherit the cap at the validator boundary.

### 3.3 Streaming invariants

- Same monotonic `sequence_no` per run.
- Same `approval_requested` event; only the contents of `metadata.params` changes.
- Replay-by-sequence still works.

### 3.4 No catalog coupling

The recogniser dispatch is keyed off the inbound `server_name` string. The catalog (`mcp_catalog.py`) does **not** declare which recogniser an entry uses. This is deliberate: a custom-URL server with a slug-y subdomain (`mcp.slack-fork.dev/`) automatically gets the Slack recogniser without a catalog entry. If we later need explicit binding, we add a `recogniser_id` field to the catalog — but the runtime fallback to slug-tokenisation is the simpler default.

---

## 4 · Test plan

### 4.1 Per-recogniser tests (`tests/unit/runtime_worker/test_approval_recognisers.py`, NEW)

For each recogniser:

- `test_<vendor>_minimum_inputs_produces_known_rows`
- `test_<vendor>_composes_keys` (e.g., `owner` + `repo` + `pull_number` → one row)
- `test_<vendor>_omits_unknown_keys`
- `test_<vendor>_handles_non_string_values_gracefully`
- `test_<vendor>_caps_at_six_rows`

Plus one test per recogniser for the `matches_server_name` happy / unhappy path.

### 4.2 Registry tests

- `test_registry_returns_none_for_unknown_vendor`
- `test_registry_first_match_wins` (Atlassian's three tokens all dispatch to one class; no fallthrough to a wrongly-similar one)
- `test_registry_caps_recogniser_output_at_six`

### 4.3 Integration in `_build_mcp_tool_approvals`

Extend `tests/unit/runtime_worker/test_stream_events.py`:

- `test_slack_approval_uses_recogniser_rows` — check the params look composed, not flat.
- `test_unknown_vendor_falls_back_to_generic_allow_list` — assert the Phase-2 generic projector still runs.

### 4.4 Schema invariants

- `test_recogniser_output_passes_pydantic_validation` — feed each recogniser's output through `McpApprovalMetadata` and assert no `ValidationError`.

---

## 5 · Sequencing

1. ai-backend: write `approval_recognisers.py` (ABC + registry skeleton). Land alone with no integration; tests pass on the module in isolation.
2. ai-backend: add `SlackApprovalRecogniser`. Land with Slack tests.
3. ai-backend: add the remaining four (GitHub, Linear, Notion, Atlassian) — one at a time or all in one commit. Each has its own test class.
4. ai-backend: integrate in `_build_mcp_tool_approvals`. Update the existing exact-match assertion in `test_stream_events.py`.
5. Run `pytest tests/unit/runtime_worker tests/unit/runtime_api/test_approval_metadata.py`. Green is the gate.

Each step is independently mergeable. Step 4 is the only one that changes runtime behaviour.

---

## 6 · Risk register

| Risk                                                                                                    | Mitigation                                                                                                                                                                                                                                            |
| ------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| New recogniser misses a vendor's primary key (e.g., GitHub `org` vs `owner`).                           | Recognisers handle synonyms (`owner` OR `org`). When a key is missing, the row is omitted; the generic allow-list **does not** run after a recogniser hits — which is intentional, otherwise we'd double-render. Adding a synonym is a one-line edit. |
| Recogniser bug throws and breaks the consent emission.                                                  | Pydantic validation at the `McpApprovalMetadata` boundary catches it; the worker logs and falls back to the generic projector. The approval still ships.                                                                                              |
| Vendor introduces a new key shape (e.g., Notion changes `parent.database_id` to `parent.dataSourceId`). | Recogniser is one place to patch. Tests fail loudly.                                                                                                                                                                                                  |
| FE expects richer values and renders them poorly when the recogniser stays generic for some calls.      | `ApprovalCard` already accepts variable row counts (Phase 2). No change needed.                                                                                                                                                                       |
| Ambiguous server_name (`mcp_atlassian_jira_com`) hits two recognisers.                                  | First match wins; tested explicitly. The Atlassian recogniser owns `atlassian`, `jira`, and `confluence` tokens.                                                                                                                                      |

---

## 7 · Out-of-scope follow-ups

- **Phase 4 — Reversibility actions.** Wire an "Undo" button to `reversible="yes"` approvals. Needs server-side undo token + 60s window.
- **Phase 5 — Risk policy emit upgrade.** Replace the `low`/`medium` short-circuit with the real `permissions.py` policy output. Unblocks `RISK_HIGH` reason code in production.
- **Audit-log surfacing.** Render `reason_code` + `category` + structured params in the audit timeline UI.
- **Live MCP introspection** (resolve channel ID → friendly name, repo ID → name, etc.). Adds caching + timeouts + cache invalidation; substantial PR on its own.
- **Catalog-bound recogniser_id field.** Only if dispatch becomes hard to reason about by token-matching alone.
- **i18n.** Recogniser-emitted strings stay English in this PR.

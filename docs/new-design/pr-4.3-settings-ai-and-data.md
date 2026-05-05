# PR 4.3 — Settings expansion · "AI & data" group + hash routing wired

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Wave 4, PR 4.3 in [`/Users/parthpahwa/.claude/plans/fetch-this-design-file-resilient-pumpkin.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md)
> **Owner:** ai-backend (one ALTER on `workspace_defaults`; runtime-context resolver gains one slot) · backend-facade (one proxy + one new audit-action constant pass-through) · frontend (3 settings sections + hash router + 1 hook) · api-types (1 type extension)
> **Size:** **M.** The persistence is a **single ALTER** to add a `behavior_overrides JSONB` column to PR 1.6's `workspace_defaults` table. Plus 3 settings sections, plus the hash-routing refactor that replaces today's path-only routing in `App.tsx`. No new tables. No new event types.
> **Depends on:** PR 1.6 workspace defaults (✅) · PR 4.4 connectors-extends (the "Connectors" sub-section here links into the MCP overlay PR 4.4 ships) · PR 0.1 design tokens
> **Reads alongside:** [`pr-1.6-workspace-defaults-conversation-lifecycle.md`](pr-1.6-workspace-defaults-conversation-lifecycle.md) (the column we're extending), [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md), [`services/ai-backend/CLAUDE.md`](../../services/ai-backend/CLAUDE.md)
> **Sibling docs (Wave 4):** [`pr-4.1-settings-you-group.md`](pr-4.1-settings-you-group.md) · [`pr-4.2-settings-workspace-group.md`](pr-4.2-settings-workspace-group.md) · [`pr-4.4-mcp-overlay-test-connection.md`](pr-4.4-mcp-overlay-test-connection.md) · [`pr-4.5-usage-overlay-share-popover.md`](pr-4.5-usage-overlay-share-popover.md)

---

## 0 · TL;DR

Three sections, one ALTER, one route extension, one routing refactor.

| Section            | Backend                                                                                      | Frontend                                                                                                   |
| ------------------ | -------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Model & behavior   | `workspace_defaults.behavior_overrides JSONB` (new column) · existing `default_model` column | Form using existing `useWorkspaceDefaults()` (PR 1.6)                                                      |
| Connectors-extends | Existing `mcp_servers` CRUD (PR 4.4 catalog/wizard owns Add)                                 | List of installed servers + "Add MCP server" CTA opening PR 4.4 wizard                                     |
| Privacy & data     | Existing `retention_policies` CRUD (PR 1.6 reuses) + new `data_residency` read + export stub | Retention summary, training opt-out toggle (in `behavior_overrides`), export form, delete-all confirmation |

The **hash routing refactor** is the third deliverable: today `App.tsx` parses `/settings/connectors` from the path; the design's "Manage" links from the connectors popover want to land on `/settings#connectors`. We add a `useSettingsSection()` hook backed by `hashchange` + `popstate` so paths and hashes round-trip without state loss. **No router library.** Native `window.location.hash` + a 30-line hook.

LoC estimate: ai-backend ≈ 110 (1 ALTER + 1 schema field + 1 RunService merge + audit + tests) · backend-facade ≈ 0 (the existing `/v1/agent/workspace/defaults` proxy carries the new field unchanged) · api-types ≈ 20 · frontend ≈ 580 (3 sections + hash hook + 1 settings router refactor + tests).

---

## 1 · PRD

### 1.1 Problem

The Atlas design doc (Settings → "AI & data" group) requires three admin panels:

| Panel                | Today                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Model & behavior** | PR 1.6 ships **default_model** + **default_connectors** + **retention** workspace-wide. The design adds: **default reasoning depth**, **system-prompt override**, **temperature**, **citation density**, **refusal behavior**. None exist as columns today; PR 1.6 explicitly carved temperature out as "per-run, not per-workspace" (`workspace_defaults.py:54-56`) — but **citation density** and **refusal behavior** are workspace policy, and the design wants them here. |
| **Connectors**       | The existing connectors panel in `SettingsScreen.tsx:215-365` is a custom-URL form + list. The design wants it to be a polished list of installed MCP servers + an "Add MCP server" CTA that opens the **PR 4.4** 5-step wizard. No new endpoints — just a UX cleanup over existing `mcp_servers` CRUD.                                                                                                                                                                        |
| **Privacy & data**   | The retention policies (`/v1/retention/policies`) ship via PR 1.6, but the **summary view** is missing. **Training opt-out** has no column. **Data residency** display is missing (the deployment region is in `deployment_profile`, not exposed). **Export** + **delete-all-data** are gated and dangerous; v1 ships **request-style** stubs that audit the intent without doing the destructive work, plus the existing `DELETE /v1/agent/history` for the safer subset.     |

A second blocker: today the Settings rail uses **path-based routing** (`/settings/connectors`). The design's "Manage" link in the connectors popover (PR 3.4) and the topbar settings cog (PR 2.1) both want to deep-link to a section, and **hash routing** is the simpler primitive for that. The fix is one hook plus a small refactor in `App.tsx`.

### 1.2 Goals

1. **Model & behavior** — admin can set `system_prompt_override`, `temperature` (clamped 0–1), `citation_density` (`minimal | standard | thorough`), `refusal_behavior` (`standard | strict | permissive`), `default_reasoning_effort` (`low | medium | high`). All five live as keys in a new `behavior_overrides JSONB` column on `workspace_defaults`. They flow into RunService's resolution chain at the same slot the model goes (between assistant and deployment defaults).
2. **Connectors** — the existing connectors panel shows installed servers and opens the PR 4.4 wizard for adding new ones. Existing per-server enable/auth controls stay.
3. **Privacy & data** — admin can:
   - Set workspace-level **training opt-out** (`behavior_overrides.training_data_opt_out: bool`). When true, the run executor sets a `disable_training: true` header on every model API call (the major providers honour it; OpenAI, Anthropic, Google all expose the equivalent flag on the request body or header). Audit on toggle.
   - See **data residency** (read-only display from `deployment_profile.region`).
   - See **retention summary** (effective TTL by kind, sourced from `retention_policies` resolver — same one the sweeper uses).
   - **Export my workspace data** — fires a backgrounded NDJSON dump job. v1 returns 202 + `export_id`; the actual export pipeline is out of scope (placeholder; admin sees "Export queued").
   - **Delete all workspace data** — high-risk; v1 returns 501 with a typed-confirmation requirement, audited.
4. **Hash routing wired** — `/settings#connectors` lands on Connectors; `/settings#model-and-behavior` on Model; etc. The route is bidirectional: clicking a section updates the hash; pasting a hashed URL navigates. **One `useSettingsSection()` hook.** No new library.
5. **Streaming and runtime untouched.** RunService gains one merge layer; nothing else moves. Audit chain extends with three new actions.

### 1.3 Non-goals

- **Per-user behavior overrides.** The design implies `system_prompt_override` is a workspace knob, not a per-user one. v1 stays workspace-only. Per-user can ride `user_preferences` (PR 4.1) in a follow-up.
- **Per-conversation behavior overrides.** `temperature` and friends remain available on the run-create request (per PR 1.6's existing surface) — v1 doesn't expose them in the chat composer; if a user wants them for a single chat they can use the API directly. The design's "Tweaks panel" is explicitly cut.
- **Real export pipeline.** Massive; deserves its own PR. v1 stub queues + audits.
- **Real workspace-data delete.** Same. 501 stub.
- **Custom refusal-behavior policies.** Three presets only.
- **Custom citation-density beyond the three presets.**
- **Provider-specific training-opt-out granularity.** v1 toggles all-or-nothing. Per-provider exclusions are out of scope.
- **Hash + query-string state composition.** v1 supports `/settings#section`; query-string parameters within a section land later (e.g. `/settings#connectors?server_id=…`) if a real use case appears.
- **React Router migration.** Rejected per §3.5.

### 1.4 Success criteria

- ✅ `PUT /v1/agent/workspace/defaults` (existing) accepts an additional `behavior_overrides` field; the existing endpoint absorbs it without a versioned change.
- ✅ `RunService.create_run()` resolution chain is extended by one slot (request → assistant → workspace_defaults.behavior_overrides → settings.default), matching the same shape PR 1.6 uses for default model.
- ✅ Workspace `training_data_opt_out` of `true` causes every outbound provider call to carry the provider-specific opt-out flag (`user.disable_training`, `OpenAI-Beta: store=false` style header where applicable). One audit row on toggle (`workspace.training_opt_out.update`).
- ✅ Pasting `/settings#connectors` opens the Connectors section without a flicker on the default. The hash mirrors the active section as the user navigates.
- ✅ The retention summary panel reads from the existing resolver (`RetentionPolicyResolver`), not a duplicate query, so the displayed TTLs match what the sweeper actually applies.
- ✅ Export stub returns 202 + `export_id`; the audit row carries the actor and the requested scope.
- ✅ Streaming handshake byte-identical pre/post merge. `make test` green; ai-backend pytest green; frontend typecheck + build green.

### 1.5 User stories

| #    | Persona             | Story                                                                                                                                                                                |
| ---- | ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| US-1 | Marcus (admin)      | I add a system-prompt override saying "Always sign off as Acme — GTM team." New chats inherit the prefix; existing chats don't.                                                      |
| US-2 | Marcus              | I set citation density to **thorough**. The agent now cites every claim, not just the load-bearing ones. (Implementation: a flag in `RuntimeContext` the citation middleware reads.) |
| US-3 | Marcus (compliance) | I toggle "Use customer data for training" to **off**. Every provider call thereafter carries the right opt-out flag. The audit log records who/when.                                 |
| US-4 | Marcus              | The retention summary card shows: messages 90d, events 30d, checkpoints 14d (the org's effective TTLs). Matches what I set in the Workspace panel.                                   |
| US-5 | Marcus              | The data residency card reads "EU (Frankfurt)" — sourced from the deploy. Read-only.                                                                                                 |
| US-6 | Marcus              | I click "Export workspace data". I get "Export queued — you'll receive a download link by email." (Stub.) Audit row records the request.                                             |
| US-7 | Marcus              | I click "Delete all workspace data". I'm asked to type the workspace slug. I type it. The button stays disabled (501 stub) with copy "Workspace deletion is gated. Contact support." |
| US-8 | Sarah               | I click "Manage connectors" from the popover in chat. The Settings page opens with `#connectors` and the Connectors section is active.                                               |
| US-9 | Sarah               | I refresh `/settings#privacy-data`. I land on Privacy & data, not the default Profile.                                                                                               |

---

## 2 · Spec

### 2.1 Wire — `behavior_overrides`

We extend the existing `WorkspaceDefaults` shape:

```jsonc
// existing PR 1.6 fields ↑
{
  "default_model": { "provider": "openai", "model_name": "gpt-5.4-mini", "reasoning": null },
  "default_connectors": { … },
  "retention_days": 90,

  // NEW in PR 4.3 — every key optional; absent keys fall through to deployment defaults.
  "behavior_overrides": {
    "system_prompt_override": "Always sign off as Acme — GTM team.",
    "temperature": 0.6,
    "citation_density": "thorough",                  // 'minimal' | 'standard' | 'thorough'
    "refusal_behavior": "strict",                    // 'standard' | 'strict' | 'permissive'
    "default_reasoning_effort": "high",              // 'low' | 'medium' | 'high'
    "training_data_opt_out": true
  },

  "updated_at": "…",
  "updated_by_user_id": "usr_…"
}
```

Adding `behavior_overrides` to `UpdateWorkspaceDefaultsRequest` is **additive** — existing callers continue to work with the original PR 1.6 fields untouched.

### 2.2 Persistence

```sql
-- 0021_workspace_defaults_behavior_overrides.sql

ALTER TABLE workspace_defaults
    ADD COLUMN IF NOT EXISTS behavior_overrides JSONB NOT NULL DEFAULT '{}'::jsonb;

-- No new index; the row is keyed on org_id (PK) and we never query by JSONB substructure.
```

PR 1.6's RLS policy already covers the table; we don't touch it. The new column is `NOT NULL DEFAULT '{}'::jsonb`, so the rewrite is metadata-only on most Postgres versions — zero-downtime migration.

We deliberately keep this as **one JSONB column** rather than five new columns:

| Reason                                                                                                                        |
| ----------------------------------------------------------------------------------------------------------------------------- |
| Behavior knobs are opinionated, evolve fast, and are read together. Five columns means five migrations as the design evolves. |
| The shape is small (<1 KB) and never queried by predicate.                                                                    |
| Pydantic v2 strict-mode validates the keys at write; unknown keys are rejected with `invalid_request`.                        |
| It mirrors the same call PR 4.1 makes for `user_preferences`: opinion data → JSONB; identity / queryable data → columns.      |

### 2.3 Resolution chain — one new slot

PR 1.6 extended `RunService.create_run` model resolution to:

```
request.model  →  conversation.assistant.model  →  workspace_defaults.default_model  →  settings.default_model
```

This PR adds a parallel chain for behavior knobs, computed in the same path:

```python
# services/ai-backend/src/agent_runtime/execution/runtime_context.py (existing file)

async def resolve_runtime_context(req, conv, ws_def, settings) -> RuntimeContext:
    return RuntimeContext(
        model=resolve_model(req, conv, ws_def, settings),
        # NEW: every behavior knob has the same fall-through shape
        system_prompt_override=req.system_prompt_override
            or conv.assistant.system_prompt_override
            or ws_def.behavior_overrides.get("system_prompt_override")
            or settings.default_system_prompt,
        temperature=first_non_null(
            req.temperature,
            conv.assistant.temperature,
            ws_def.behavior_overrides.get("temperature"),
            settings.default_temperature,
        ),
        citation_density=first_non_null(
            req.citation_density,
            ws_def.behavior_overrides.get("citation_density"),
            settings.default_citation_density,
        ),
        refusal_behavior=first_non_null(
            req.refusal_behavior,
            ws_def.behavior_overrides.get("refusal_behavior"),
            settings.default_refusal_behavior,
        ),
        reasoning_effort=first_non_null(
            req.reasoning_effort,
            conv.assistant.reasoning_effort,
            ws_def.behavior_overrides.get("default_reasoning_effort"),
            settings.default_reasoning_effort,
        ),
        training_data_opt_out=ws_def.behavior_overrides.get("training_data_opt_out", False),
    )
```

`citation_density` is then read by the **citation middleware** (PR 1.1) to control how aggressively it tags claims. `refusal_behavior` is read by the **safety middleware** (existing). `training_data_opt_out` is read by the model-call middleware to set provider-specific headers.

The shape change is **backwards compatible**: every callsite pulls fields from `RuntimeContext`, not from the request directly. Adding a slot in the resolver doesn't ripple.

### 2.4 Audit

Three new actions on `runtime_audit_log`:

| Action                                | Metadata                                                                                        |
| ------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `workspace.behavior_overrides.update` | `{ before, after, diff_keys }` — same shape as PR 1.6's `workspace.defaults.update`             |
| `workspace.training_opt_out.update`   | `{ before: bool, after: bool }` — explicit because compliance auditors search this row directly |
| `workspace.export.request`            | `{ requester_user_id, scope, status: 'queued' }` — scope is `workspace` for v1                  |
| `workspace.delete_attempt`            | `{ attempting_user_id, typed_confirmation_correct: bool }` — even rejected attempts get audited |

We split `workspace.training_opt_out.update` from `workspace.behavior_overrides.update` because it's the single field with externally-visible compliance impact (provider headers change), and a search like `action='workspace.training_opt_out.update'` finds it without parsing JSONB diffs.

### 2.5 Permissions

| Caller             | Read defaults  | Write behavior_overrides | Read retention summary | Export stub | Delete-all stub |
| ------------------ | -------------- | ------------------------ | ---------------------- | ----------- | --------------- |
| Workspace admin    | ✅             | ✅                       | ✅                     | ✅          | ✅ (501)        |
| Member             | ✅ (read-only) | ❌                       | ✅                     | ❌          | ❌              |
| Service-to-service | ✅             | ❌                       | ✅                     | ❌          | ❌              |

Same `ADMIN_USERS` permission scope PR 1.6 uses. No new RBAC primitive.

### 2.6 Error semantics

| Condition                                   | Status | Code                        |
| ------------------------------------------- | ------ | --------------------------- |
| `temperature` outside `[0, 1]`              | 422    | `invalid_temperature`       |
| `citation_density` not in enum              | 422    | `invalid_citation_density`  |
| `refusal_behavior` not in enum              | 422    | `invalid_refusal_behavior`  |
| `default_reasoning_effort` not in enum      | 422    | `invalid_reasoning_effort`  |
| `system_prompt_override` longer than 8 KB   | 422    | `system_prompt_too_long`    |
| Unknown key in `behavior_overrides`         | 422    | `invalid_request`           |
| `POST /v1/workspace/export` rate limit      | 429    | `rate_limited` (1/hour/org) |
| `DELETE /v1/workspace/data` (real deletion) | 501    | `not_implemented`           |

### 2.7 Frontend contract (`@enterprise-search/api-types`)

```ts
// packages/api-types/src/index.ts

export type CitationDensity = "minimal" | "standard" | "thorough";
export type RefusalBehavior = "standard" | "strict" | "permissive";
export type ReasoningEffort = "low" | "medium" | "high";

export interface WorkspaceBehaviorOverrides {
  system_prompt_override?: string | null;
  temperature?: number | null;
  citation_density?: CitationDensity | null;
  refusal_behavior?: RefusalBehavior | null;
  default_reasoning_effort?: ReasoningEffort | null;
  training_data_opt_out?: boolean;
}

// Existing WorkspaceDefaults gets a new optional field:
export interface WorkspaceDefaults {
  // … existing fields
  behavior_overrides: WorkspaceBehaviorOverrides;
}
```

Existing `useWorkspaceDefaults()` hook from PR 1.6 continues to work — it returns the new field automatically once api-types is updated.

### 2.8 Frontend wiring — three sections

| Section          | Component                                                           | Reuse                                                                                                                                                                                                | Add                                                                                                                                          |
| ---------------- | ------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Model & behavior | `apps/frontend/src/features/settings/sections/ModelAndBehavior.tsx` | `useWorkspaceDefaults()` hook from PR 1.6 · `<Field>`/`<Switch>`/`<Select>` design-system primitives                                                                                                 | One textarea (`<TextArea>` already in design-system, `style.css:172-181`) for `system_prompt_override`; one number-stepper for `temperature` |
| Connectors       | `apps/frontend/src/features/settings/sections/Connectors.tsx`       | Existing list rendering + `useConnectors()` hook · PR 4.4's `<McpOverlay>`                                                                                                                           | One "Add MCP server" button that opens the PR 4.4 wizard                                                                                     |
| Privacy & data   | `apps/frontend/src/features/settings/sections/PrivacyAndData.tsx`   | `useWorkspaceDefaults()` (for training opt-out toggle) · existing `RetentionPolicyResolver` view via new GET endpoint (see §2.10) · existing `DELETE /v1/agent/history` for "delete my chat history" | Confirmation dialog (`<Dialog>` from PR 4.4) for delete-all-data                                                                             |

### 2.9 Hash routing — the refactor

Today's `apps/frontend/src/app/App.tsx` parses `/settings/{section}` from the path. We change it to `/settings#{section}` while keeping `/settings` (no hash) → default section.

```ts
// apps/frontend/src/features/settings/useSettingsSection.ts (NEW, ~30 LOC)

import { useEffect, useState, useCallback } from "react";

const VALID_SECTIONS = [
  "profile",
  "appearance",
  "shortcuts",
  "notifications", // 4.1
  "workspace",
  "members",
  "billing", // 4.2
  "model-and-behavior",
  "connectors",
  "privacy-data", // 4.3
] as const;
type SettingsSection = (typeof VALID_SECTIONS)[number];

const DEFAULT_SECTION: SettingsSection = "profile";

function readHash(): SettingsSection {
  const raw =
    typeof window === "undefined" ? "" : window.location.hash.replace(/^#/, "");
  return (VALID_SECTIONS as readonly string[]).includes(raw)
    ? (raw as SettingsSection)
    : DEFAULT_SECTION;
}

export function useSettingsSection(): [
  SettingsSection,
  (next: SettingsSection) => void,
] {
  const [section, setSection] = useState<SettingsSection>(readHash);

  useEffect(() => {
    const sync = () => setSection(readHash());
    window.addEventListener("hashchange", sync);
    window.addEventListener("popstate", sync);
    return () => {
      window.removeEventListener("hashchange", sync);
      window.removeEventListener("popstate", sync);
    };
  }, []);

  const navigate = useCallback(
    (next: SettingsSection) => {
      if (next !== section) {
        window.history.pushState(null, "", `#${next}`);
        setSection(next);
      }
    },
    [section],
  );

  return [section, navigate];
}
```

`App.tsx`'s `routeFromLocation()` simplifies — `/settings/*` collapses to `/settings`, the section comes from the hook. Old paths like `/settings/connectors` redirect to `/settings#connectors` once on mount via a 30 LOC migrator (so existing bookmarks survive). Then we drop the path branch entirely.

### 2.10 Retention summary endpoint

The retention sweeper resolves `(scope, kind) → ttl_seconds` via `RetentionPolicyResolver`. The Privacy & data panel needs to render the **effective** TTLs, not the raw rows (multiple rows can compose). We add:

```
GET /v1/retention/effective?org_id={current}
```

Returns:

```jsonc
{
  "effective": {
    "messages": {
      "ttl_seconds": 7776000,
      "scope": "org",
      "source_policy_id": "ret_…",
    },
    "events": {
      "ttl_seconds": 2592000,
      "scope": "org",
      "source_policy_id": "ret_…",
    },
    "context_payloads": {
      "ttl_seconds": 604800,
      "scope": "deployment_default",
    },
    "checkpoints": {
      "ttl_seconds": 1209600,
      "scope": "org",
      "source_policy_id": "ret_…",
    },
    "memory_items": {
      "ttl_seconds": 7776000,
      "scope": "org",
      "source_policy_id": "ret_…",
    },
  },
}
```

This is **the same resolver the sweeper uses**, exposed as an HTTP read. Zero risk of drift between displayed and applied. ~40 LOC plus tests.

### 2.11 Export and delete-all stubs

`POST /v1/workspace/export` (admin)

```jsonc
{ "scope": "workspace" } // v1 only accepts 'workspace'
```

Returns `202 { "export_id": "exp_…", "status": "queued" }`. v1 implementation: writes to a stubbed `runtime_export_jobs` table (or `runtime_audit_log` only — even simpler). Real export pipeline is its own PR.

`DELETE /v1/workspace/data` (admin) → 501 `not_implemented` with copy "Workspace deletion is gated. Contact support." UI shows the typed-confirmation dialog regardless; we audit the **attempt** even on 501 so we know who's asking.

---

## 3 · Architecture

### 3.1 Where this lives in the system

```
   ┌────────────────┐                                        ┌──────────────────────┐
   │ apps/frontend  │                                        │ backend-facade       │
   │ Settings tabs  │ /v1/agent/workspace/defaults (existing)│ proxy unchanged       │
   │ + hash routing │ ─────────────────────────────────────► │                      │
   │                │ /v1/retention/effective (NEW)          │                      │
   │                │ ─────────────────────────────────────► │                      │
   │                │ /v1/workspace/export (NEW stub)        │                      │
   │                │ ─────────────────────────────────────► │                      │
   └────────────────┘ /v1/workspace/data (NEW stub, 501)     └──────┬───────────────┘
                                                                    │ /internal/v1/agent/workspace/defaults
                                                                    │ /internal/v1/retention/effective
                                                                    │ /internal/v1/workspace/export
                                                                    ▼
                                                           ┌──────────────────────┐
                                                           │ services/ai-backend  │
                                                           │ workspace_defaults   │  ALTER ADD behavior_overrides
                                                           │   service            │
                                                           │ retention resolver   │  surfaces effective TTLs
                                                           │ run service          │  resolution chain +1 slot
                                                           └──────────────────────┘
```

### 3.2 Streaming impact — explicitly **none**

| Subsystem                                | Touched?                                                                                                                                                                                                       |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `runtime_events`, `RuntimeEventEnvelope` | No                                                                                                                                                                                                             |
| SSE handshake                            | No                                                                                                                                                                                                             |
| Worker job loop                          | No, except: middleware reads new `RuntimeContext` fields (`citation_density`, `refusal_behavior`, `training_data_opt_out`) — these are **read at run start** and frozen for the run; no streaming-layer change |
| Citations (PR 1.1)                       | Citation middleware reads `runtime_context.citation_density` (one field) when deciding whether to stream `source_ingested` for low-relevance hits — a behaviour switch, not a wire change                      |
| Drafts, approvals, subagents             | No                                                                                                                                                                                                             |
| Audit chain                              | Additive — four new `action` constants                                                                                                                                                                         |
| Retention sweeper                        | No (the new GET endpoint reuses the resolver; the sweeper still does its own resolve)                                                                                                                          |

The runtime-context expansion is the only behavioural change, and it's **read-once-at-run-start**. The model sees the fully-resolved context before the first token; nothing streams that wasn't already streaming.

### 3.3 Why hash routing, not React Router

| Option                                   | Pros                                                                       | Cons                                                                                                    |
| ---------------------------------------- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| **`useSettingsSection()` hook (chosen)** | Zero deps; ~30 LOC; native `hashchange`/`popstate`; matches design exactly | We don't get nested-route conventions for free                                                          |
| `react-router-dom`                       | Familiar API; rich ecosystem                                               | Adds a dep; existing app is path-routed manually; mixing libraries with manual routing courts confusion |
| `wouter`                                 | 1.3 KB; minimal                                                            | Still adds a top-level routing primitive when we have just one consumer (Settings)                      |
| `next/navigation`                        | n/a (Vite app, not Next)                                                   |                                                                                                         |

The design's **only** deep-link surface is Settings sections. Hash routing buys us the entire feature in one hook. **Adopt React Router** when there are 4+ deep-linked surfaces and the manual code is repeated.

### 3.4 DRY — what we reuse vs. what we add

| Concern                    | Reuse                                                 | Add                                                                                                          |
| -------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `workspace_defaults` table | PR 1.6 schema + admin gating                          | One `ALTER … ADD COLUMN behavior_overrides JSONB`                                                            |
| Audit chain                | `runtime_audit_log` + chain trigger                   | Four `action` constants                                                                                      |
| Resolution chain           | `resolve_runtime_context` (existing path)             | Five `first_non_null` lookups (one per knob)                                                                 |
| Citation density behaviour | Citation middleware (PR 1.1)                          | One read of `runtime_context.citation_density`                                                               |
| Refusal behaviour          | Safety middleware (existing)                          | One read                                                                                                     |
| Training opt-out flag      | Per-provider model-call middleware (existing)         | Provider-specific header / body field map (one switch per provider — already three providers in the catalog) |
| Retention resolver         | `RetentionPolicyResolver` (PR 1.6 / 0012)             | One HTTP wrapper that calls `.resolve_effective(org_id)` and returns                                         |
| Connector list             | Existing connectors panel UI + `useConnectors()` hook | "Add MCP server" CTA → opens PR 4.4 wizard                                                                   |
| Settings rail              | `SettingsScreen` left rail + section switch           | Three section components + three rail entries grouped under "AI & data"                                      |
| Form state                 | Existing `useWorkspaceDefaults()` hook                | One `useDirtyForm` 20-LOC hook for debounced save                                                            |
| Modal primitive            | `<Dialog>` from PR 4.4 (Radix Dialog wrapper)         | —                                                                                                            |
| Hash routing               | `window.location.hash`, `hashchange`, `popstate`      | One ~30 LOC hook                                                                                             |
| Path → hash migrator       | `window.location.replace`                             | One mount-time check (~15 LOC)                                                                               |

### 3.5 Pre-built libraries — what we considered, what we use

| Need                                   | Considered                                            | Decision                                                                                                                                   |
| -------------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Hash routing                           | `react-router-dom`, `wouter`, `nanoroutes`            | **Skip** — one hook covers it. See §3.3.                                                                                                   |
| Form state                             | `react-hook-form`, `formik`                           | **Skip** — small forms; native state.                                                                                                      |
| Number stepper                         | `@radix-ui/react-slider`, custom                      | **Custom slider** — Radix Slider is overkill for one knob (temperature). Reuse the existing `<Field>` + `<NumberInput>` patterns, ~25 LOC. |
| Confirm-typed-text dialog              | `react-confirm`                                       | **Skip** — same 30 LOC reused from PR 4.2's danger-zone.                                                                                   |
| Code editor for system_prompt_override | `@uiw/react-textarea-code-editor`, `@codemirror/view` | **Skip** — `<TextArea>` is fine; system prompts are markdown-ish, not code. Auto-grow up to 12 rows, ~12 LOC.                              |
| JSON-merge-patch lib                   | `fast-json-patch`                                     | **Skip** — Pydantic v2 `model_dump(exclude_unset=True)` for the API; native object spread + null-clear semantics for the FE.               |
| Server-state cache                     | `@tanstack/react-query`                               | **Skip** — same call PR 4.1 / 4.2 makes. Two endpoints; not worth the cache lib.                                                           |

### 3.6 Sequence — Marcus toggles training opt-out

```
Marcus       FE (Privacy & data)              backend-facade            ai-backend                Postgres                   provider call middleware
 │              │                                │                          │                          │                              │
 │  toggle off  │ optimistic flip                │                          │                          │                              │
 │              │ debounce 300ms                 │                          │                          │                              │
 │              │ PUT /v1/agent/workspace/defaults                          │                          │                              │
 │              │ { behavior_overrides: { training_data_opt_out: false } } │                          │                              │
 │              │ ──────────────────────────────►│ proxy unchanged          │                          │                              │
 │              │                                │ ───────────────────────► │ admin guard              │                              │
 │              │                                │                          │ validate                 │                              │
 │              │                                │                          │ BEGIN TX                 │                              │
 │              │                                │                          │ UPDATE workspace_defaults│                              │
 │              │                                │                          │ INSERT runtime_audit_log │                              │
 │              │                                │                          │   (workspace.behavior_overrides.update + …training_opt_out.update)
 │              │                                │                          │ COMMIT                   │                              │
 │              │ ◄──────────────────────────────│ ◄────────────────────── │ effective view returned  │                              │
 │              │ toast "saved"                   │                          │                          │                              │
 │              │                                │                          │                          │                              │
 │  …Sarah sends a prompt                                                                                                              │
 │              │ POST /v1/agent/runs                                                                                                  │
 │              │ ──────────────────────────────►│ ───────────────────────►│ resolve runtime_context  │                              │
 │              │                                │                          │   training_data_opt_out=true                            │
 │              │                                │                          │ ────────────────────────────────────────────────────────►│ provider call w/ disable_training=true header
 │              │                                │                          │                          │                              │ provider returns
 │              │ ◄──────────────────────────── stream events…                                                                          │
```

The provider-specific header map lives in the model-call middleware (existing). For OpenAI we set `OpenAI-Beta: store=false` (or the equivalent at the time of merge); for Anthropic we set `anthropic-disable-training: true`; for Google we set the equivalent. These are well-known flags; we keep a small map and update it as providers rename.

### 3.7 Edge cases

| Case                                                                                            | Behaviour                                                                                                                                                                            |
| ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Admin sets `temperature: 0.95` and a user passes `temperature: 0.5` per-run                     | Per-run wins (top of resolution chain).                                                                                                                                              |
| Admin sets `system_prompt_override` and the assistant has its own override                      | Assistant wins (closer to the chat in the chain). The workspace override is a default, not a force.                                                                                  |
| Provider doesn't support a training-opt-out flag                                                | Middleware logs a warning and drops the flag for that provider. The user sees no behaviour change for that provider; the audit row is still recorded so we know the user toggled it. |
| Admin toggles training opt-out while a run is mid-stream                                        | The mid-stream run is unaffected (its `runtime_context` is already frozen). Subsequent runs honour the new value.                                                                    |
| Hash routing — user pastes `/settings#unknown`                                                  | `useSettingsSection()` falls back to `profile`; the URL is rewritten to `/settings` (no hash) so the user sees the canonical state.                                                  |
| Old bookmark `/settings/connectors`                                                             | Mount-time migrator detects the old path and `window.location.replace('/settings#connectors')`.                                                                                      |
| Two tabs open Settings; tab A changes section to `connectors`, tab B is on `model-and-behavior` | Tabs don't sync sections — that would be invasive. Each tab keeps its own section. (We could share via `BroadcastChannel` if it ever matters; v1 doesn't.)                           |
| `behavior_overrides` field present but `{}` (empty)                                             | All fall-throughs hit `settings.default_*`. Equivalent to "no override."                                                                                                             |
| `system_prompt_override` is a 9 KB string                                                       | 422 `system_prompt_too_long`. The 8 KB cap protects token budgets at run-start.                                                                                                      |
| Citation density `thorough` while the model picks zero sources                                  | Middleware doesn't fabricate sources; the agent ships an answer with no chips, like today. The knob controls **density**, not **invention**.                                         |
| Refusal behaviour `permissive` on a request the safety middleware blocks at the policy layer    | Policy wins. The knob biases borderline cases; hard policy rejections still fire.                                                                                                    |
| Export request rate limit (1/hour)                                                              | 429 `rate_limited`. UI surfaces "you can request another export in 38 minutes."                                                                                                      |
| User re-clicks delete-all-data after typing the right slug                                      | 501 `not_implemented`; the audit row counts the typed-confirmation as correct; UI shows "gated, contact support" copy.                                                               |

### 3.8 Test plan

**ai-backend (`services/ai-backend/tests/`)**

- `unit/runtime_api/workspace/test_behavior_overrides_round_trip.py` — `PUT` / `GET` add/clear/preserve fields; unknown keys → 422.
- `unit/runtime_api/services/test_resolution_chain_with_overrides.py` — happy path through each slot; per-run wins; assistant wins over workspace; etc.
- `unit/runtime_api/test_retention_effective_endpoint.py` — matches sweeper resolver; org-only / user-override / conversation-override.
- `unit/runtime_api/test_workspace_export_stub.py` — 202; one audit row.
- `unit/runtime_api/test_workspace_delete_stub.py` — 501; one audit row even on 501; typed-confirmation correctness recorded.
- `integration/test_audit_chain_for_behavior_writes.py` — four new action types; verifier passes; `workspace.training_opt_out.update` is searchable independent of the wider `behavior_overrides.update` row.

**Frontend (`apps/frontend/src/features/settings/`)**

- `useSettingsSection.test.ts` — hashchange / popstate sync; `pushState` on navigate; default fallback; old-path migrator.
- `sections/ModelAndBehavior.test.tsx` — debounced save; clamp temperature; system-prompt-override length cap.
- `sections/Connectors.test.tsx` — list renders; "Add MCP server" opens PR 4.4 wizard.
- `sections/PrivacyAndData.test.tsx` — retention summary renders from `/v1/retention/effective`; export queues; delete-all confirms with typed slug; 501 displayed cleanly.

**Cross-service smoke (`make test`)** — one happy path through each new endpoint.

### 3.9 Rollout

- **Flag-free.** New JSONB column defaults to `{}`; old behaviour preserved when nothing's set.
- **Zero-downtime migration.** `ALTER … ADD COLUMN … NOT NULL DEFAULT '{}'::jsonb` is metadata-only on Postgres 14+ (the deployment baseline).
- **Backout.** Drop the column; resolve falls through to deployment defaults; UI hides the section gracefully.
- **Forward compatibility.** Adding more knobs is one Pydantic field; no migration.
- **Old paths.** `/settings/connectors` etc. continue to resolve (one-time replace into `/settings#connectors`); we keep the migrator for one release before deleting.

### 3.10 Open questions

1. **Provider parity for training opt-out.** Each provider names the flag differently and may change names. We keep a small table; we accept that drift is real and audit the request even when the flag is silently dropped.
2. **Per-tenant model catalog.** Some workspaces will want to disable "GPT-5.4 Nano" globally. v1 doesn't expose this; the workspace-level default model is the closest knob. A future PR can add an "available models" allow-list.
3. **Export pipeline.** When it ships, the stub becomes a real job; the existing audit row carries forward.
4. **Per-user behavior overrides.** Tracked alongside PR 4.1 if the demand surfaces.
5. **Workspace-data deletion.** Cascade scope (conversations, runs, events, drafts, approvals, audits, MCP connections, etc.) needs a real design pass. v1 stub is honest about it.

---

## 4 · Acceptance checklist

- [ ] Migration `0021_workspace_defaults_behavior_overrides.sql` applies cleanly forward and rolls back.
- [ ] `behavior_overrides` field round-trips through `PUT/GET /v1/agent/workspace/defaults`; unknown keys return 422.
- [ ] `RunService.create_run()` resolution chain extended; new `RuntimeContext` fields (`citation_density`, `refusal_behavior`, `system_prompt_override`, `default_reasoning_effort`, `training_data_opt_out`) reach the model-call middleware.
- [ ] Citation middleware (PR 1.1) reads `citation_density` and adjusts emission threshold.
- [ ] Provider-call middleware sets the right disable-training header per provider when `training_data_opt_out=true`.
- [ ] `GET /v1/retention/effective` returns the resolver's effective TTLs per kind; matches sweeper-applied values.
- [ ] `POST /v1/workspace/export` returns 202 + `export_id`; one audit row.
- [ ] `DELETE /v1/workspace/data` returns 501 + audited attempt with typed-confirmation correctness.
- [ ] Four new audit `action` constants registered; chain verifier passes.
- [ ] `useSettingsSection()` hook syncs hashchange, popstate; navigates via pushState; old `/settings/{section}` paths migrate once on mount.
- [ ] Three sections (`<ModelAndBehavior />`, `<Connectors />`, `<PrivacyAndData />`) mount under the "AI & data" group rail.
- [ ] Connectors section opens PR 4.4 wizard via the "Add MCP server" CTA.
- [ ] `@enterprise-search/api-types` exports `WorkspaceBehaviorOverrides` + the three preset enums; `WorkspaceDefaults` extended.
- [ ] Streaming handshake byte-identical pre/post merge.
- [ ] No new event types, no new wire variants, no LangGraph harness changes.
- [ ] `npm run typecheck`, `npm run build`, ai-backend pytest, `make test` all green.

---

## 5 · References

- Design Doc · Settings → "AI & data" group + the design's note on **Hash routing in Settings** as a P0 follow-up — bundle at `/tmp/design-doc/enterprise-search/project/Design Doc.html` lines 549-553, 671-672.
- [`services/ai-backend/migrations/0019_workspace_defaults.sql`](../../services/ai-backend/migrations/0019_workspace_defaults.sql) — table we extend.
- [`services/ai-backend/migrations/0012_retention_policies.sql`](../../services/ai-backend/migrations/0012_retention_policies.sql) — the resolver we expose.
- [`services/ai-backend/src/runtime_worker/jobs/retention_sweeper.py`](../../services/ai-backend/src/runtime_worker/jobs/retention_sweeper.py) — the consumer of the same resolver.
- [`services/ai-backend/src/agent_runtime/execution/runtime_context.py`](../../services/ai-backend/src/agent_runtime/execution/runtime_context.py) — the resolution chain we extend by one slot.
- [`apps/frontend/src/app/App.tsx`](../../apps/frontend/src/app/App.tsx) — current path-based routing to refactor.
- [`apps/frontend/src/features/settings/SettingsScreen.tsx`](../../apps/frontend/src/features/settings/SettingsScreen.tsx) — section host.
- [`packages/design-system/src/index.tsx`](../../packages/design-system/src/index.tsx) — `<Field>`, `<Switch>`, `<Select>`, `<TextArea>` reused.
- [Mozilla Web Docs · `hashchange` event](https://developer.mozilla.org/docs/Web/API/Window/hashchange_event) — the API the hook listens to.
- [`docs/new-design/pr-1.6-workspace-defaults-conversation-lifecycle.md`](pr-1.6-workspace-defaults-conversation-lifecycle.md) — the column we extend, the chain we add to, the audit pattern we mirror.
- [`docs/new-design/pr-4.4-mcp-overlay-test-connection.md`](pr-4.4-mcp-overlay-test-connection.md) — `<McpOverlay>` opened from the Connectors section.
- [`docs/new-design/pr-4.1-settings-you-group.md`](pr-4.1-settings-you-group.md) — the same JSONB-blob persistence pattern (per-user side), `<TextArea>` and `<Switch>` reuse.

# PR 4.4.7 Phase 2 — Suggested Connectors (Progressive Discovery)

> Status: drafted · Owners: ai-backend, backend, frontend
>
> Phase 1 (this repo, already shipped) added a per-catalog-entry `discoverable` flag, a `<DiscoverableToggle>` in the Catalog tab, and a localStorage-backed `useDiscoverablePref` hook. Phase 1 ships data plumbing only — the agent never sees the suggestion.
>
> Phase 2 (this PRD) wires the toggle into the agent so users learn about MCP capabilities they haven't connected yet, instead of perceiving them as platform limitations.

## Context

Today, when a user types **"Check Linear for my tasks"** and Linear is not installed for that user, the agent has no idea Linear exists. It either fails the request or hallucinates. The right behavior is:

1. The agent recognizes the intent.
2. It checks the org's curated catalog for a matching uninstalled connector.
3. It surfaces a structured suggestion: _"Linear isn't connected — want me to walk you through it?"_
4. The user clicks **Connect** → the McpOverlay opens at the right entry → install + OAuth → the next message can use Linear.

This is **not** automatic capability acquisition. The agent never silently installs or auths something on the user's behalf; it surfaces a human-acknowledged step.

## Goals

- Users discover catalog capabilities through chat, not just the Settings modal.
- The agent never lies: if it can't do something, it suggests the path that _would_ enable it.
- Suggestions respect the existing permission invariants (paused, RBAC, workspace-disabled) — none of those are weakened.
- Toggle preference survives across browsers (current localStorage is per-device).
- Runtime exposure is opt-in via a feature flag; rollout is reversible.

## Non-goals

- Auto-install or auto-auth without user click.
- Per-org admin policies for which catalog entries can be suggested (Phase 3).
- Suggestions for native tools, skills, or non-MCP capabilities.
- Persona-aware suggestions ("you usually use Linear at 9am" — not in scope).
- Replacing the manual `Manage MCP servers` flow.

## User journey

**Scenario A — happy path.** Sarah, no Linear connected:

1. Sarah: _"Check Linear for my tasks."_
2. Agent's first turn: recognizes Linear intent, calls `suggest_connector(slug="linear", reason="Linear lookup")`.
3. Stream emits a typed `connector_suggested` event.
4. Frontend renders a `<ConnectorSuggestionCard>` directly under the agent text, brand-colored: _"Connect Linear to read your issues, projects, and cycles."_ with `[Connect]` and `[Not now]` buttons.
5. Sarah clicks Connect → McpOverlay opens on the Catalog tab, scrolled to Linear, install button highlighted.
6. Linear installs + OAuth completes.
7. Next message ("...so what tasks?") works as normal.

**Scenario B — muted.** Sarah toggled "Discoverable" off for Atlassian on a previous chat:

1. Sarah: _"What's blocked in Jira?"_
2. Agent has no Atlassian server installed AND Atlassian's discoverable pref is `false` for this user.
3. The runtime never receives Atlassian in `suggested_connectors`. The system prompt doesn't mention it.
4. Agent responds without surfacing a suggestion. It may say "I can't see Jira here." but won't push a CTA.

**Scenario C — pre-existing pause.** Sarah has Linear installed but paused for this chat (popover toggle off):

1. Sarah: _"Check Linear."_
2. Linear is in `paused_connectors` for this run. It is **not** suggestible (the user already explicitly paused).
3. Agent says "Linear is paused for this chat. Resume in the popover above and ask again."
4. (This last sentence is a system-prompt convention, not a special tool — the agent already has this signal.)

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                   USER SENDS MESSAGE                             │
│                            │                                     │
│                            ▼                                     │
│  ai-backend run-creation:                                        │
│  • Pull paused_connectors from conversation                      │
│  • Pull installed servers from backend                           │
│  • Call backend's /internal/v1/me/suggestible-connectors         │
│      query: { exclude_installed_for: user_id, exclude_paused }   │
│      response: tuple[CatalogSuggestionCard]                      │
│  • Materialize onto AgentRuntimeContext.suggested_connectors     │
│                            │                                     │
│                            ▼                                     │
│  Agent graph builder:                                            │
│  • System prompt template renders {{ suggested_connectors }}     │
│  • Suggested-connector tool registered when context non-empty    │
│                            │                                     │
│                            ▼                                     │
│  Agent infers intent → calls suggest_connector(slug, reason)     │
│  • Permission check: slug must be in suggested_connectors        │
│  • Tool emits connector_suggested event into the run stream      │
│                            │                                     │
│                            ▼                                     │
│  Frontend stream renderer:                                       │
│  • Projects connector_suggested → <ConnectorSuggestionCard>      │
│  • Card has Connect / Not now buttons                            │
│  • Connect → McpOverlay deep-link (?install=linear)              │
└─────────────────────────────────────────────────────────────────┘
```

## Data model

### Backend storage (replaces localStorage)

`me_preferences` already exists for misc user prefs. Add:

```python
class MePreferencesRecord:
    ...
    discoverable_connectors: dict[str, bool] = {}
    """Per-user override of catalog ``discoverable`` defaults.

    Key: catalog slug. Value: True (always suggest), False (never
    suggest). Absent key = inherit from catalog entry's
    ``discoverable``.
    """
```

### Public app surface

```http
GET  /v1/me/preferences/discoverable
PATCH /v1/me/preferences/discoverable     # RFC 7396 merge-patch
```

Response shape:

```json
{ "scopes": { "linear": true, "atlassian": false } }
```

PATCH body uses the same merge semantics as conversation connector scopes — `null` clears (back to catalog default), `true`/`false` overrides.

### Internal surface (ai-backend → backend)

```http
GET /internal/v1/me/suggestible-connectors?org_id=...&user_id=...
    &exclude_paused=seed:linear,seed:atlassian
```

Response:

```json
{
  "suggestions": [
    {
      "slug": "linear",
      "display_name": "Linear",
      "description": "Issues, projects, and cycles.",
      "scopes_summary": "Read issues, projects, and cycles.",
      "logo_url": null,
      "brand_color": "#5E6AD2"
    }
  ]
}
```

Filtering rules (server-side, in order):

1. Drop any slug whose `seed:<slug>` already exists in the user's MCP servers.
2. Drop any slug in `exclude_paused`.
3. Drop catalog entries with `discoverable=false`.
4. Drop entries where the user override is explicitly `false`.

### Runtime context

```python
class AgentRuntimeContext(RuntimeContract):
    ...
    suggested_connectors: tuple[CatalogSuggestionCard, ...] = ()
```

Materialized once at run-create. Frozen for the lifetime of the run (same as `connector_scopes` / `paused_connectors`).

`CatalogSuggestionCard` is a small Pydantic record with the fields the agent prompt and tool need: `slug`, `display_name`, `description`, `scopes_summary`, `brand_color`.

## Agent runtime

### System prompt addition

A new section near the bottom of the system prompt template:

> **Suggestable integrations not yet connected**
>
> The following capabilities are available in the workspace catalog but have not been installed by the current user. If the user's intent maps to one of these, call the `suggest_connector` tool with the slug and a one-sentence reason; do NOT pretend you can already access them.
>
> Available slugs:
>
> - `linear` — Issues, projects, and cycles.
> - `notion` — Workspace pages and databases.
> - …

The list is rendered server-side from `suggested_connectors`. If the tuple is empty, the section is omitted entirely (no token tax).

### New tool: `suggest_connector`

Located in `agent_runtime/capabilities/tools/builtins/`. Inputs:

```python
class SuggestConnectorRequest(RuntimeContract):
    slug: str
    reason: str  # One sentence. <= 140 chars.
```

Permission gate: `slug` must be in `context.suggested_connectors`. Anything else returns `PERMISSION_DENIED` (the agent can't fabricate a slug).

Output: a typed `RuntimeEventEnvelope.connector_suggested` event with the card details + the user's reason. The tool returns `{"acknowledged": true}` to the agent so it can continue speaking.

### Tool registration

Conditionally registered only when `context.suggested_connectors` is non-empty. Saves token budget on every run that has nothing to suggest.

## Stream event

```python
class ConnectorSuggestedPayload(StreamEventPayload):
    slug: str
    display_name: str
    description: str
    scopes_summary: str | None
    brand_color: str | None
    reason: str  # Agent-supplied; <= 140 chars; sanitized.
```

The event flows through the existing `RuntimeEventEnvelope` pipeline — no new transport. Frontend reads it from the SSE stream the same way it reads `tool_call_*` events.

## Frontend

### `<ConnectorSuggestionCard>`

New component under `apps/frontend/src/features/chat/components/messages/`. Renders inline as a tool-event card, similar to the approval card. Layout matches the catalog row but is brand-colored:

```
┌──────────────────────────────────────────────────────────┐
│ [icon] Connect Linear                                    │
│        Issues, projects, and cycles.                     │
│        Why: "Linear lookup for your tasks."              │
│                                                          │
│              [ Connect ]   [ Not now ]                   │
└──────────────────────────────────────────────────────────┘
```

- **Connect** → opens McpOverlay with `?install=<slug>` query param. The overlay deep-links the Catalog tab, scrolls Linear into view, and pulses the Install button.
- **Not now** → dismisses inline (writes `discoverable.<slug>=false` for this user) and emits an analytics event.

### `useDiscoverablePref` migration

Same surface; backing changes from localStorage to the new `/v1/me/preferences/discoverable` endpoint with optimistic updates. The hook signature stays:

```ts
const { enabled, overridden, setEnabled } = useDiscoverablePref(
  slug,
  catalogDefault,
);
```

Migration path on first read:

1. Hook fetches current backend state.
2. If localStorage has overrides not present in backend, PATCH them once and clear localStorage.
3. After that the hook is purely backend-driven.

## Permission invariants (unchanged)

- `paused_connectors` ⇒ slug invisible to suggestible endpoint AND blocked from runtime tool calls (Phase 1.5 fix).
- Suggesting a connector grants **zero** capability. The agent still has to wait for install + OAuth before any tool call works.
- Even after install, `McpPermissionPolicy` re-checks every load_server / call_tool.
- `discoverable=false` on the catalog entry hides the slug from suggestible regardless of user override.

## Feature flag

`agent_runtime.feature_flags.SUGGESTED_CONNECTORS` (new). Off by default until the prompt + tool change have been A/B'd internally. When off:

- Backend still serves prefs CRUD.
- Internal suggestible endpoint still responds.
- Runtime context **does not populate** `suggested_connectors` — system prompt skips the section, tool isn't registered, frontend never sees the event.

Removing the flag is the rollout signal.

## Tests

Backend:

- Prefs CRUD (GET empty, PATCH single, PATCH null clears, idempotent).
- Suggestible filter:
  - Excludes installed `seed:<slug>`.
  - Excludes `exclude_paused`.
  - Excludes `discoverable=false` catalog entries.
  - Excludes entries the user explicitly muted.
  - Returns the rest.
- Per-org isolation (user A's prefs invisible to user B).

ai-backend:

- `_apply_conversation_scope_fallback` calls suggestible endpoint and populates context (mocked).
- System prompt template renders the section iff context non-empty.
- `SuggestConnector.is_authorized` accepts slug ⊆ context, rejects otherwise.
- Stream emits `connector_suggested` with the right payload shape.

Frontend:

- `useDiscoverablePref` reads/writes backend; migrates localStorage on first run.
- `<ConnectorSuggestionCard>` renders from a fixture event.
- Connect button opens McpOverlay deep-linked.
- Not now button writes the mute pref.

## Rollout

1. **Slice A — durable prefs** (this PR's first commit):
   - Backend prefs CRUD endpoints.
   - Frontend hook migration.
   - Visible result: catalog toggles persist across browsers.
2. **Slice B — runtime plumbing** (this PR's second commit):
   - Internal suggestible endpoint.
   - Runtime context wiring (behind feature flag).
   - No user-visible change yet.
3. **Slice C — agent surface** (this PR's third commit):
   - System prompt section.
   - `suggest_connector` tool.
   - `connector_suggested` stream event.
   - `<ConnectorSuggestionCard>` chat affordance.
   - Behind the same feature flag.
4. **Slice D — flip the flag** (separate small commit):
   - Once internal use validates the UX, flip default to on.

## Open questions

- **Should the agent be allowed to suggest more than one connector per turn?** Default proposal: at most one suggestion per turn, multiple turns OK. Otherwise the user gets a wall of CTAs.
- **What happens if the user dismisses then asks again next turn?** Default: respect the mute. The toggle in the catalog is the unmute path.
- **How long should the agent wait between repeating a suggestion the user dismissed?** Default: never re-suggest in the same conversation; next conversation, fair game.
- **Should "Not now" mute permanently, or just for this conversation?** Default: permanently (writes the user pref). Inline copy makes that explicit: "Not now (also stop suggesting Linear)".

These can be revisited mid-rollout; flags A/B different defaults.

---

## Implementation slice for this PR

This PR ships **Slice A only** — the durable prefs migration. Slices B, C, D are separately reviewable and follow with their own diffs. Reasoning:

- Slice A is a no-risk migration: data layer only, surface is identical.
- Slice B onward changes agent behavior, which deserves its own review and observation window.
- Slice A makes Phase 1's toggle actually useful (cross-device persistence) and is independently shippable.

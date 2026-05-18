# Agents Destination — Sub-PRD

**Status:** draft (2026-05-18)
**Owner:** parth (orchestrator) — implementation delegated to phase-8 impl agents
**Master:** [destinations-master-prd.md §5.6](../destinations-master-prd.md#56-agents-agents)
**Foundation:** [PRD.md](../PRD.md) — workspace shell + composer + thread canvas
**Binding cross-PRD decisions:** [cross-audit.md](../cross-audit.md) — `ItemRef` incl. `kind="agent"` (§1.1), ports (§1.2), project-scoped ACL master rule (§1.3), audit `context` (§1.4), filter axis OR (§1.5), `<PageHeader>` (§1.6), branded `AgentId` (§2.1), `<ItemLink>` registry (§3.3), cascade default (§5.3), **token-usage single-tracker invariant (§5.5)**
**Reads from / consumed by:**

- [destinations/chats-canvas-prd.md](chats-canvas-prd.md) — chats invoke an agent; the composer's model/agent picker reads from the installed-agents set this PRD owns (§8.1 below). Chats' `Conversation.agent_id` is the canonical link.
- [destinations/routines-prd.md](routines-prd.md) — Routines carry `agent_id` and (per §9.7 Q11) an optional `agent_version_pin: AgentVersionId | null`. Routines is the **driving consumer** of the `AgentVersion` shape this PRD ships (§3.2 below).
- [destinations/projects-prd.md](projects-prd.md) — a Project may set a `default_agent_id` (per Projects §12 Q3 connector-override pattern, applied to agents); see §8.3 below.
- [destinations/home-prd.md](home-prd.md) — Home surfaces "Agents you used most" via the existing `runtime_model_call_usage` join (no new write path); see §12.
- [destinations/inbox-prd.md](inbox-prd.md) — `InboxItem.sender.kind = "agent"` resolves through this destination's `<ItemLink kind="agent">` registry.

**Implementation phasing:** see §14 below — P8-A1 backend CRUD + ACL, P8-A2 versions, P8-A3 install/override, P8-B1 gallery shell, P8-B2 detail + editor, P8-B3 usage chart, P8-C frontend wiring.

**Design references:**

- master PRD §5.6 — premise + open questions ("first-party only or community?", "memory format", "agent-to-agent invocation").
- `/tmp/atlas-design/enterprise-search-template/project/dest-agents.jsx` (gallery + detail card design).
- chat1.md line 184 — subagent runtime semantics (orthogonal — runtime is owned by ai-backend; this PRD is the **management surface**).
- The current `packages/chat-surface/src/destinations/agents/AgentsDestination.tsx` Wave-0 stub is **a debug table over `/v1/agent/runs`**. This PRD throws that shape away — runs/observability live in Home/Chats; the Agents destination is the **agent registry/gallery + editor + per-agent usage**.

---

## §1 Premise

### 1.1 What an Agent is

An **Agent** is the **unit of orchestration in Atlas**. Concretely it is a durable record carrying:

1. A **name** (required, one line) — e.g. "Calendar Whisperer", "Inbox Triage", "Slack Summarizer".
2. A **slug** (`/[a-z0-9_-]{2,40}/`) — stable identifier mentionable as `@slug` in the composer.
3. A **description** (one paragraph, ≤ 240 chars) — what the agent does, written for the user picking it from a gallery.
4. A **visual identity** — `icon_emoji` (single emoji glyph) + `color_hue` (HSL hue 0–359, design-system tokenized).
5. **Instructions** — the system prompt the runtime layers onto every invocation.
6. A **model preference** (`model_default`) — provider + model id pinned to the agent (overridable per-run).
7. A set of **skills** (`SkillId[]`) and **default connectors** (`ConnectorId[]`) — the capability bundle the agent ships with.
8. **Permissions** (`AgentPermissions`) — autonomy level, max tool calls per run, read-only-vs-write, allowed/blocked tool families.
9. A **version** counter (monotonic, e.g. `v3`) — bumped via explicit snapshot (`POST /v1/agents/<id>/versions`).
10. A **status** (`installed | available | disabled | draft`) — see §1.6.
11. An **origin** (`system | community | custom`) — provenance + governance axis.
12. An optional **owner_user_id** — set on `origin="custom"`, null on `system`/`community`.
13. **Timestamps** (`created_at`, `updated_at`).

When a Chat invokes the agent or a Routine fires the agent, the runtime resolves the agent record **at invocation time** (master rule, mirrors Routines §1.4 source-of-truth rule) and composes its `instructions` + `model_default` + `skills` + `connectors_default` into a `DeepAgentBuildRequest`. The Agent's executions ARE the ai-backend `runs` tagged with `run.source.agent_id`. **There is no parallel "agent runs" store.** Per cross-audit §5.5, agent usage is a **read-only projection over the existing `runtime_model_call_usage` table** — no new write site, no parallel tracker.

An Agent is the answer to: _"I want a specialized helper named X that knows how to do Y, costs roughly Z per use, and I can summon it from a chat, schedule it on a Routine, or set it as my Project's default."_

### 1.2 Why a separate destination instead of "agents are just a setting on each chat"

Three reasons, in priority order:

1. **The agent is the unit, not the chat.** A chat invokes an agent; a routine fires an agent; a project's default agent is shared across many chats and routines. The agent's identity, instructions, skill bundle, model preference, and permissions need to live **once**, somewhere addressable — `agent_id` — so all three consumers reference the same record. Burying the agent definition inside individual chats forces every consumer (Routines, Projects, Home, Inbox) to reinvent the wire shape and to reconcile drift across copies. The master PRD §2.2 "one source of truth per destination" requires it.

2. **The user thinks in agents.** "I want a Calendar Whisperer that drafts my meeting notes" is the user's mental model; "I want a chat with system prompt X plus skill bundle Y plus model Z" is the **implementation** of that mental model. The destination's UI must speak the user's vocabulary: gallery, install, customize, usage, cost. The current Wave-0 `AgentsDestination` ships a debug table over `/v1/agent/runs` — that's an observability tool, lives in Home, and tells the user nothing about which **agents** they have or what those agents do.

3. **Discovery + customization + governance are their own surfaces.** Browsing a catalog of system + community agents, installing them per-user, customizing instructions per-tenant, snapshotting versions for routine-pinning, watching per-agent cost — none of these belong inside a chat. They are durable management actions with their own audit trail. Mixing them with chat state would dwarf both surfaces.

Agents is the **14th destination** in the workspace rail (counting Projects as the 13th per [projects-prd.md](projects-prd.md) §1.1, Routines as the 12th, the original 11 from the master PRD). Implementation-plan §6 will extend `ShellDestinationSlug` to keep `"agents"` as a real destination slug (it already exists for the Wave-0 stub; Phase 8 promotes the surface from debug-table to gallery).

### 1.3 What Agents is NOT

| Anti-goal                                | Why not                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------- |
| **Run history / observability surface**  | The current stub ships this and it is **wrong**. Run history is an attribute of **Home** (recent runs section) and **Chats** (per-thread run timeline). The Agents destination shows _what agents exist_, _what they can do_, and _how much they cost_ — not _what they just did_. (The detail panel may show a small "last used: 2h ago + 5 runs this week" stat; it must NOT show a paginated runs table.)                         |
| **The runtime**                          | The `services/ai-backend/` runtime owns LangGraph execution, subagent delegation, streaming, model invocation. This destination does NOT spin up runs, does NOT stream tokens, does NOT touch `deep_agent_builder.py` directly. It is a **product-persistence** destination in `services/backend/` whose records the runtime **reads at run-construction time** through an internal endpoint.                                        |
| **Marketplace submission flow**          | Wave 6 ships the **wire shape** for `origin = "community"` (so install endpoints accept a community-origin agent), but it does NOT ship the community-submission UI, review queue, or moderation tooling. The catalog of community agents is curated server-side in Phase 8 (a seed list of vetted system+community starters); user-driven submission is master §10 Q1 deferred to a later wave (see §11 below).                     |
| **Tool / connector store**               | Skills and connectors are referenced **by id** from an Agent's `skills` / `connectors_default`. Their lifecycle (registration, OAuth, scope edits) lives in the **Tools** (Phase 9) and **Connectors** (Phase 10) destinations. An agent does not own a skill — it _refers_ to one. Deleting a skill leaves a dead reference on the agent (cross-audit §5.3 cascade default: dead link, not cascade).                                |
| **Memory store**                         | An agent does NOT own its own memory shard. Memory is the **Memory** destination (Phase 11). An agent may reference a `memory_ref` scope, but the records live in Memory. Per master §5.6 open question Q2 (recommended resolution: shared pool, scoped subsets), the Agents destination ships the `memory_ref` field as **forward-compatible** (`MemoryRef                                                                          | null`) and Phase 11 wires it. |
| **Per-tenant agent factory**             | A tenant admin cannot author an "Acme Salesforce Triage" agent that auto-installs for every member of the tenant in Phase 8 (recommended deferral to Wave 6). Tenant admins can _pre-install_ a system/community agent for everyone via `POST /v1/agents/<id>/install` with `scope="tenant"` (§4.5 below), but they cannot mint a brand-new tenant-scoped agent template. See §11 Q1.                                                |
| **Agent-to-agent orchestration surface** | An agent CAN delegate to a subagent at runtime (the ai-backend Deep Agents subagent mechanism already exists; chat1.md line 184 describes it). The Agents destination DOES NOT ship a graphical "wire-this-agent-to-that-agent" editor. Subagent composition is encoded in the parent agent's `instructions` (master §5.6 Q3, recommended resolution: surface in runbook detail — which lives in Home/Chats, not here).              |
| **Per-user instance store**              | A user does not "have their own copy" of a system agent. They have an `AgentInstall` row (overrides + install timestamp) that **shadows** the canonical agent at invocation time. The canonical record is shared; the per-user override is thin (§3.3). Forking a system agent to a new custom record is an **explicit user action** (`POST /v1/agents/<id>/duplicate`, §4.10), not an implicit side effect of editing instructions. |

### 1.4 User success states (what "done" looks like)

- _"I want an agent that drafts my meeting notes."_ → Open `/agents`; search "meeting notes"; install the system "Calendar Whisperer" agent; @mention it in a chat; meetings transcribe + summary lands. (§2 U1.)
- _"Tweak the system prompt of my Inbox-Triage agent so it routes finance escalations to me, not my replacement."_ → Open agent detail; click "Customize"; the system agent forks to a custom copy in my workspace; edit instructions; the original system agent stays canonical for everyone else. (§2 U2.)
- _"Install a community 'Slack Summarizer' agent."_ → Filter chip "Community"; pick the card; click Install; one click; agent is now available in my composer's @-mention and in Routines' agent picker. (§2 U3.)
- _"Which of my agents cost the most last week?"_ → Open `/agents` filtered to "Installed"; each card shows a usage chip "$0.42 last 7 days"; sort by `usage.cost_usd_micro:desc`. (§2 U4.)
- _"Set 'Acme Renewal Assistant' as the default agent on my Acme project."_ → Project detail → Settings → Default agent → picker reads from installed agents. (§2 U5.)
- _"Disable the 'Slack Summarizer' agent — too noisy."_ → Agent detail → Disable; status flips to `disabled`; composer hides it from the @-mention picker; existing routines pinning a version of it continue to fire (pinned semantics — §3.2 + §8.2). (§2 U6.)
- _"Build a custom agent from scratch — 'Q3 Launch Liaison'."_ → Top-right "Create custom agent"; editor opens; system prompt, model, skills, connectors picker; save as draft; install. (§2 U7.)
- _"Pin v3 of my Inbox-Triage agent to my morning routine so future edits don't break the routine."_ → Routine editor → Agent picker → pin version → fixes `agent_version_pin: <v3-id>`. (§2 U8.)

### 1.5 Relationship to other destinations (single-source-of-truth map)

| Destination    | Agent relationship                                                                                                                               | How it consumes the Agents wire                                                                                                                                                                                                                                                 |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Chats**      | `Conversation.agent_id` (existing wire — chat-surface composer picks an agent; runtime composes its `instructions` into the run).                | The composer's agent picker calls `GET /v1/agents?filter[status]=installed&filter[origin]=…` to populate the dropdown. Chat detail's "View agent →" affordance navigates via `<ItemLink kind="agent" id={conversation.agent_id} />`.                                            |
| **Routines**   | `Routine.agent_id` (existing — routines-prd §4.1) + `Routine.agent_version_pin: AgentVersionId                                                   | null`(per cross-audit §9.7 Q11). When`agent_version_pin = null`: live re-resolve at fire time. When set: resolve the pinned snapshot.                                                                                                                                           | Routine editor's agent picker calls `GET /v1/agents?filter[status]=installed`; version pin picker calls `GET /v1/agents/<id>/versions`. Routine fire reads `Agent` or `AgentVersion` via internal endpoint (§4.11).  |
| **Projects**   | `Project.default_agent_id: AgentId                                                                                                               | null` (NEW field — see Projects §12 Q3 resolution pattern; lands in Phase 8 as a Projects-side migration owned by P8-A1's cross-cut).                                                                                                                                           | Project editor's default-agent picker calls `GET /v1/agents?filter[status]=installed`. New chats created under a project pre-fill `Conversation.agent_id = project.default_agent_id` when not explicitly overridden. |
| **Home**       | "Agents used most this week" panel; "Recent runs" cross-link via `run.agent_id`.                                                                 | `GET /v1/agents?sort=usage.cost_usd_micro:desc&limit=5` (server-aggregated against `runtime_model_call_usage` per §12). No new write path.                                                                                                                                      |
| **Inbox**      | `InboxItem.sender = { kind: "agent", ref: { kind: "agent", id: AgentId } }` (per cross-audit §1.1).                                              | Inbox rows resolve through `<ItemLink kind="agent">`; click → `/agents/<id>`.                                                                                                                                                                                                   |
| **Todos**      | `Todo.source.agent_id` (existing — todos-prd §3.2). When a todo is extracted by an agent, the source resolves through `<ItemLink kind="agent">`. | Read-only consumption via the existing `<ItemLink>` registry.                                                                                                                                                                                                                   |
| **Tools**      | An agent's `skills: SkillId[]` references Tool entities. Phase 9 Tools destination owns the canonical Skill record.                              | Agent editor's skill picker calls `GET /v1/tools?filter[kind]=skill` (Phase 9 endpoint — until Phase 9 lands, this PRD's editor reads the existing `services/backend/src/backend_app/skills/` store, which is the eventual home for the Tools destination). Forward-compatible. |
| **Connectors** | An agent's `connectors_default: ConnectorId[]` references Connector entities (Phase 10).                                                         | Same forward-compat: editor calls `GET /v1/connectors`; until Phase 10 lands, the existing OAuth-registered MCP servers are the source.                                                                                                                                         |
| **Memory**     | Optional `memory_ref: MemoryRef                                                                                                                  | null` field (Phase 11; lands in this PRD as a nullable forward-compatible field). No Phase-8-side wiring.                                                                                                                                                                       | Phase 11 will add the picker; this PRD reserves the wire field only.                                                                                                                                                 |
| **Team**       | Each Team profile (Phase 10) shows that person's installed agents (`agent_installs` join).                                                       | Read-only via `GET /v1/team/<user_id>/agents`.                                                                                                                                                                                                                                  |

**Single-source-of-truth rule:** the predicate "is `agent_id` valid for tenant T and visible to user U?" has **one implementation** — `services/backend/src/backend_app/agents/acl.py::resolve_agent_view(tenant_id, agent_id, user_id) -> AgentView | None`. Returns `None` (→ 404) when the agent is deleted, cross-tenant, or `disabled` with no override; returns the merged view (canonical record + user install overrides) otherwise. Every consumer — Chats, Routines, Projects, Home — calls this. No destination reimplements the resolution.

### 1.6 Status semantics (the four `status` values, explained)

| Status      | Meaning                                                                                                                                                                                                                                                                                                                                                                                                    | Effect on consumers                                                                   |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `available` | The agent exists in the catalog (system or community origin) but the caller has not installed it. Visible in `/agents` gallery; NOT visible in composer agent picker; NOT a valid `agent_id` for new chats/routines.                                                                                                                                                                                       | `GET /v1/agents?filter[status]=installed` excludes; gallery shows with "Install" CTA. |
| `installed` | The agent is installed for the caller (per-user `AgentInstall` row exists). Visible everywhere — composer picker, routines picker, projects default picker, gallery.                                                                                                                                                                                                                                       | All pickers include.                                                                  |
| `disabled`  | An installed agent the user has explicitly disabled (without uninstalling — preserves the override). Hidden from pickers; still resolvable by id for existing chats/routines that pinned it pre-disable. Routines using a disabled agent's pinned version continue to fire (snapshot is immutable). Routines using the live (`agent_version_pin = null`) agent **auto-pause** per Routines §9.7 Q4 / §1.4. | Pickers exclude; pinned references still resolve.                                     |
| `draft`     | A custom agent the user is authoring. Owner-only visible; not in any picker. Becomes `installed` on first save-and-install. Drafts can be abandoned (soft-delete, §5.3 retention).                                                                                                                                                                                                                         | Only the owner sees draft agents. No cross-consumer impact.                           |

The `disabled` ↔ `installed` toggle is intentionally separate from uninstall. **Uninstall** removes the `AgentInstall` row and any user-level overrides — the agent moves back to `available` for that user. **Disable** keeps the row + overrides but flags the agent inactive. This mirrors how real app stores treat "remove from device" vs "hide from picker".

---

## §2 User journeys (the 8 concrete flows)

### U1. "I want an agent that drafts my meeting notes."

1. Click **Agents** in the workspace rail. Land on the gallery.
2. Type "meeting notes" in the top search bar.
3. The grid filters to ~3 cards. The first card is **Calendar Whisperer** — icon 🗓, color hue 220, description _"Drafts meeting notes from calendar invites and transcripts."_, usage chip _"Used by 412 people"_.
4. Click the card → right-rail detail panel opens with full description, instructions preview, model (Sonnet-4.6), connectors (Google Calendar, Notion), and example output.
5. Click **Install** (primary button, top-right of detail panel). Spinner → green check → button becomes **Installed ✓** with a secondary **Disable** menu item.
6. Cross-page side effect: composer's `@`-mention picker now lists Calendar Whisperer; Routines picker now lists Calendar Whisperer.

### U2. "Tweak the system prompt of my Inbox-Triage agent."

1. From `/agents` filter chip **Installed**, pick the **Inbox-Triage** card.
2. Detail panel → **Customize** button (visible because `origin="system"` and the user has install rights).
3. A confirmation appears: _"Customizing forks this system agent into a custom copy. Your changes won't affect other workspace members. Continue?"_ → Confirm.
4. The editor opens on `/agents/<new-id>/edit` — the agent's instructions, model, skills, connectors are pre-filled from the system source. Name auto-suggests _"Inbox-Triage (custom)"_.
5. Edit the instructions: change "route to me" → "route finance escalations to me; route product escalations to my replacement".
6. Click **Save**. New custom agent saved (`origin="custom"`, `owner_user_id=<me>`, `status="installed"`). The original system Inbox-Triage stays untouched. The user can now @-mention either.

### U3. "Install a community 'Slack Summarizer' agent."

1. From `/agents` gallery, filter chip **Community**.
2. Spot **Slack Summarizer** — icon 💬, hue 280, description, **"by Acme Co."** byline.
3. Detail panel shows the publishing org, the install count, a small warning callout _"Community agents are vetted but not authored by Atlas. Review the instructions before installing."_
4. Click **Install** → install proceeds. (Wave 6 may add a review-gate / org-admin approval — out of scope here.)

### U4. "See which agents I've used most + how much they cost."

1. From `/agents` gallery, click the **sort** control → "Cost (7d) ↓".
2. Cards re-order. Each card's usage chip now shows the 7-day token+cost summary: _"412 runs · $1.23 · 47k tokens"_.
3. The top card is **Calendar Whisperer** at $0.84; second is **Inbox-Triage (custom)** at $0.62.
4. Hover the chip → a small popover shows the per-day spark line + a "View runs →" link (jumps to Home recent-runs filtered by `agent_id`).

### U5. "Set a default agent for my Acme project."

1. Navigate to **Projects → Acme renewal → Settings tab**.
2. Field **Default agent**: a picker styled identically to the composer's agent picker (DRY) — populated by `GET /v1/agents?filter[status]=installed`.
3. Pick **Acme Renewal Assistant** (a custom agent the user authored earlier).
4. Save → audit row `project.update` with `before_state.default_agent_id = null`, `after_state.default_agent_id = <agent_id>`.
5. New chats created under `/projects/<id>` now pre-fill `Conversation.agent_id = <default_agent_id>`. Explicit picker on the composer overrides per-chat.

### U6. "Disable an agent across my workspace."

1. From `/agents` detail panel for **Slack Summarizer**, click the overflow menu → **Disable for me**.
2. Confirmation: _"Disable Slack Summarizer? It will be hidden from your composer and routine pickers. Routines pinning a version of it will continue to fire; routines using the live agent will auto-pause."_ → Confirm.
3. `status="disabled"` in my install row. Composer/Routines pickers exclude it. The card in the gallery shows a desaturated state with a **Re-enable** affordance.
4. Tenant-admin action **Disable workspace-wide** is an admin-only variant (§6.2) that disables the agent for every tenant member (super-set of per-user disable). Audit `agent.disable_tenant`.

### U7. "Build a custom agent from scratch."

1. Top-right of the gallery, click **Create custom agent** (primary action button).
2. The editor opens at `/agents/new` on the **Identity** tab. Fields: name, slug (auto-generated, editable), description, icon emoji picker, color hue slider (preview chip updates live).
3. Switch to **Instructions** tab — a multi-line text area with a model-prompt linter and a token counter (read-only — no LLM call here; pure client-side estimate).
4. Switch to **Model** — picker with model id + reasoning depth default + max tool calls. Defaults populated from tenant defaults (config in `services/backend`).
5. Switch to **Skills** — multi-select chip-picker over the user's available skills (Phase 9 Tools).
6. Switch to **Connectors** — same shape as Skills; multi-select over installed connectors.
7. Switch to **Permissions** — `read_only`, max tool calls per run, autonomy level (manual-approval vs auto-apply).
8. Click **Save as draft** (status=`draft`) or **Save & install** (status=`installed`). Audit `agent.create`.

### U8. "Pin a specific version of an agent."

1. Navigate to **Routines → Daily briefing → Agent** field.
2. Below the agent picker, a toggle **Pin version** (off by default).
3. Toggle on → a second picker appears: **Version (v3, v2, v1)**. v3 is current; v2 and v1 are prior snapshots.
4. Pick **v3** → `Routine.agent_version_pin = <v3_id>`.
5. Save the routine. Audit `routine.update` with `context.agent_version_pin = <v3_id>`.
6. Next fire: the runtime resolves the pinned `AgentVersion` snapshot (immutable instructions, model, skills, connectors). Future edits to the Agent's live record do NOT affect the pinned routine.
7. The Agent detail page's **Versions** tab shows the pinning: "v3 is pinned by 2 routines · 1 project default."

---

## §3 Data shape

### 3.1 Canonical wire types (`packages/api-types/src/agents.ts`)

```typescript
import type {
  AgentId,
  AgentVersionId,
  AgentInstallId,
  TenantId,
  UserId,
  SkillId,
  ConnectorId,
  MemoryRef,
} from "./brands";
import type { ItemRef } from "./refs";

export type AgentOrigin = "system" | "community" | "custom";
export type AgentStatus = "installed" | "available" | "disabled" | "draft";
export type AgentAutonomy = "manual_approval" | "auto_apply";

export interface AgentPermissions {
  readonly autonomy: AgentAutonomy;
  /** Max tool calls a single run may make. 0 = no cap.  */
  readonly max_tool_calls_per_run: number;
  /** Hard upper bound on output tokens per run.  */
  readonly max_output_tokens: number;
  /** Read-only restricts ALL connectors to read scope at fire time. */
  readonly read_only: boolean;
  /** Optional allowlist of skill ids. Empty = inherit from `skills`. */
  readonly allowed_skill_ids?: ReadonlyArray<SkillId>;
  /** Optional blocklist of tool family names ("filesystem", "network"). */
  readonly blocked_tool_families?: ReadonlyArray<string>;
}

export interface AgentModelDefault {
  readonly model_id: string; // e.g. "anthropic:claude-sonnet-4-7-1m"
  readonly reasoning_depth: "fast" | "balanced" | "deep";
}

/** Canonical Agent record. Returned by GET /v1/agents and /v1/agents/<id>. */
export interface Agent {
  readonly id: AgentId;
  readonly tenant_id: TenantId;
  readonly name: string;
  readonly slug: string;
  readonly description: string;
  readonly icon_emoji: string;
  readonly color_hue: number; // HSL 0–359
  readonly version: number; // monotonic counter (v3 = 3)
  readonly status: AgentStatus;
  readonly origin: AgentOrigin;
  /** Set when origin = "custom". null on "system" and "community". */
  readonly owner_user_id: UserId | null;
  readonly instructions: string;
  readonly model_default: AgentModelDefault;
  readonly connectors_default: ReadonlyArray<ConnectorId>;
  readonly skills: ReadonlyArray<SkillId>;
  readonly permissions: AgentPermissions;
  /** Forward-compatible for Phase 11 Memory. null in Phase 8. */
  readonly memory_ref: MemoryRef | null;
  readonly created_at: string; // ISO8601
  readonly updated_at: string;
  /** Denormalized display hint: caller's install state. */
  readonly viewer_install_status: AgentStatus;
  /** Denormalized display hint: 7-day usage rollup (read-only projection). */
  readonly viewer_usage_7d: AgentUsageRollup | null;
}

export interface AgentVersion {
  readonly id: AgentVersionId;
  readonly agent_id: AgentId;
  readonly version: number;
  readonly instructions_snapshot: string;
  readonly model_default_snapshot: AgentModelDefault;
  readonly skills_snapshot: ReadonlyArray<SkillId>;
  readonly connectors_default_snapshot: ReadonlyArray<ConnectorId>;
  readonly permissions_snapshot: AgentPermissions;
  readonly created_at: string;
  readonly created_by: UserId;
  /** Free-text label e.g. "Pre-Q3-release config". Optional. */
  readonly label: string | null;
}

export interface AgentInstall {
  readonly id: AgentInstallId;
  readonly tenant_id: TenantId;
  readonly user_id: UserId;
  readonly agent_id: AgentId;
  readonly installed_at: string;
  /** Thin per-user override layer. null = no overrides. */
  readonly overrides: AgentOverrides | null;
}

export interface AgentOverrides {
  /** Override the agent's `instructions`. Empty string = no override. */
  readonly instructions?: string;
  readonly model_default?: AgentModelDefault;
  readonly skills?: ReadonlyArray<SkillId>;
  readonly connectors_default?: ReadonlyArray<ConnectorId>;
  readonly permissions?: Partial<AgentPermissions>;
}

export type UsagePeriod = "day" | "week" | "month";

export interface AgentUsageRollup {
  readonly agent_id: AgentId;
  readonly period: UsagePeriod;
  /** Number of distinct runs that referenced this agent. */
  readonly run_count: number;
  readonly token_in: number;
  readonly token_out: number;
  readonly cost_usd_micro: number; // micro-USD; divide by 1_000_000 for USD
}
```

### 3.2 Version snapshot semantics (the immutability rule)

An `AgentVersion` is **immutable** once created. There is no `PATCH /v1/agents/<id>/versions/<vid>` endpoint. To "fix a typo in v3", the user edits the live agent (which bumps `Agent.version` on the next explicit snapshot), then creates v4 with a corrected label. v3 stays exactly as it was — that is the contract Routines § 9.7 Q11 depends on.

**Why explicit snapshots (not auto-snapshot on every PATCH):**

- Auto-snapshot would create an `AgentVersion` row on every keystroke-debounced save. After two weeks of editing, an agent would have hundreds of versions; the version picker becomes unusable.
- Explicit snapshot ("Save as version v4") is a real action the user takes when they consider the current config stable enough to pin. It matches the user mental model of "tag a release".
- Cross-audit §5.3 cascade default applies: deleting an Agent does NOT cascade-delete its `AgentVersion` snapshots — the snapshots are immutable historical records that pinned Routines still reference. Cascade rule §5.4 below specifies "dead link" semantics (the Routine renders a degraded UI state).

See §11 Q3 for confirmation.

### 3.3 Per-user overrides — the thin-layer contract

`AgentInstall.overrides` is a **partial replacement** of the canonical Agent fields at resolution time. The runtime's `resolve_agent_view` flow (in `services/backend/src/backend_app/agents/service.py::resolve_agent_view`) is:

```python
def resolve_agent_view(tenant_id, agent_id, user_id):
    agent = agents_store.get(tenant_id, agent_id)
    if agent is None or agent.status == "draft" and agent.owner_user_id != user_id:
        return None  # 404 to non-owners
    install = installs_store.get(tenant_id, agent_id, user_id)
    if install is None:
        if agent.origin == "custom" and agent.owner_user_id != user_id:
            return None  # custom agents not installed = not visible
        return agent  # available state, no overrides
    merged = merge_overrides(agent, install.overrides)
    merged.viewer_install_status = "disabled" if install.disabled else "installed"
    return merged
```

`merge_overrides` is a pure function (`agents/overrides.py`); every override field replaces the canonical field if present. Permissions merge **field-wise** (not all-or-nothing) so a user can override `autonomy="manual_approval"` without restating `max_tool_calls_per_run`.

The override system is **deliberately thin** — it intentionally does NOT support deep edits (no skills append, no permissions inheritance trees). If a user needs deeper customization, they `POST /v1/agents/<id>/duplicate` to fork into a custom agent of their own and edit freely. See §11 Q4.

### 3.4 Usage projection — read-only against existing tracker (cross-audit §5.5 invariant)

`AgentUsageRollup` is **not stored**. It is computed at read time by aggregating against the existing `runtime_model_call_usage` table in `services/ai-backend/` (cross-audit §5.5). Per the single-tracker invariant:

```sql
-- Pseudocode: aggregating per-agent usage from the existing tracker
SELECT
  run_meta.agent_id,
  $period_bucket(rmu.created_at) AS period_start,
  COUNT(DISTINCT rmu.run_id)      AS run_count,
  SUM(rmu.input_tokens)           AS token_in,
  SUM(rmu.output_tokens)          AS token_out,
  SUM(rmu.cost_micro_usd)         AS cost_usd_micro
FROM runtime_model_call_usage rmu
JOIN runtime_run_meta run_meta ON rmu.run_id = run_meta.run_id
WHERE rmu.org_id = $tenant_id
  AND run_meta.agent_id = $agent_id
  AND rmu.created_at >= $window_start
GROUP BY 1, 2;
```

The `agent_id` dimension is **already** attributable on `runtime_run_meta.agent_id` because every run created by Atlas (chat-initiated, routine-initiated, manual-fire) sets `run.agent_id = <agent_id_or_null>` at creation. No new write site is added by Phase 8.

`services/backend-facade/` exposes `GET /v1/agents/<id>/usage?period=…` which calls into `services/ai-backend/`'s existing `/v1/usage` family (see §12). This is **DRY**: one tracker, one query path, one aggregation.

---

## §4 Endpoints

All endpoints are mounted at `services/backend-facade/src/backend_facade/agents_routes.py` (facade). Apps call only the facade (`:8200`). Internal endpoints (called by `services/ai-backend/` at run-construction time) are at `services/backend/src/backend_app/agents/internal_routes.py` and are NOT exposed via the facade.

### 4.1 `GET /v1/agents` — list/search the catalog

**Query:** `cursor`, `limit` (default 50, max 200), `q` (text search across name+description+slug), and the following filter axes (multi-value OR within axis, AND across axes, per cross-audit §1.5):

- `filter[origin]=system|community|custom`
- `filter[status]=installed|available|disabled|draft`
- `filter[skill_id]=<SkillId>` (multi-value OR)
- `filter[connector_id]=<ConnectorId>` (multi-value OR)
- `filter[owner_user_id]=<UserId>` (admin-only; surfaces "agents owned by user X")

**Sort:** `sort=updated_at:desc|asc`, `usage.cost_usd_micro:desc` (server joins to usage projection), `name:asc`.

**Response:**

```typescript
interface AgentListResponse {
  readonly items: ReadonlyArray<Agent>;
  readonly next_cursor: string | null;
}
```

**ACL:** tenant-scoped. System + community origin = readable by all members. Custom origin = readable only by owner OR if `status="installed"` for the caller. Drafts: owner-only.

**Cache:** server may cache the catalog list for 60s per `(tenant_id, filter_signature)`. Usage projections invalidate cache per-agent.

### 4.2 `GET /v1/agents/{id}` — detail

**Response:** `Agent` with `viewer_install_status` and `viewer_usage_7d` populated. Always returns the **merged-overrides** view per §3.3.

**ACL:** see `resolve_agent_view` in §3.3.

**Errors:**

- `404` — agent not found, cross-tenant, or not visible (cross-audit §1.3: 404-not-403 to avoid existence leaks).

### 4.3 `POST /v1/agents` — create custom agent

**Body:** `{ name, slug?, description, icon_emoji, color_hue, instructions, model_default, connectors_default, skills, permissions, memory_ref? }`. Slug auto-generated from name if absent.

**Effects:** creates an `agents` row with `origin="custom"`, `owner_user_id = caller`, `status="draft"` (becomes `installed` on the first explicit install), `version=1`.

**ACL:** any tenant member. (Wave 6 may add tenant-admin quota; see §11 Q1.)

**Audit:** `agent.create` with `after_state` = full record (excluding empty `instructions` to keep audit row size sane; full text kept in the audit `context` JSON column if < 4KB, else a content-hash + storage pointer per the audit-chain convention).

**Returns:** the created `Agent`.

### 4.4 `PATCH /v1/agents/{id}` — edit (live record)

**Body:** any subset of `{ name, description, icon_emoji, color_hue, instructions, model_default, connectors_default, skills, permissions, memory_ref, status }`.

**Effects:** mutates the live `agents` row. Bumps `updated_at`. Does NOT bump `version` (version bumps only on explicit snapshot — §4.6).

**ACL:**

- `origin="custom"`: owner only.
- `origin="system" | "community"`: forbidden (must duplicate first — §4.10). The PATCH responds `409 Conflict` with `{ error: "agent_origin_immutable", hint: "Use POST /v1/agents/<id>/duplicate to fork." }`.

**Audit:** `agent.update` with field-level `before_state`/`after_state`. Instructions diffs > 4KB stored by hash + pointer.

### 4.5 `POST /v1/agents/{id}/install` — install (per-user, optionally per-tenant)

**Body:** `{ scope?: "user" | "tenant" }` (default `"user"`).

**Effects:**

- `scope="user"`: creates an `agent_installs` row `(tenant_id, agent_id, user_id=caller)`. Idempotent — second install is a no-op (HTTP 200 with current row).
- `scope="tenant"`: tenant-admin-only. Creates an install row for every tenant member (current + future via a join-time materialization — see §5.5 below). Audit `agent.install_tenant`.

**ACL:** any tenant member (for `scope="user"`); admin only (for `scope="tenant"`).

**Returns:** updated `Agent` view (with `viewer_install_status` flipped to `installed`).

**Audit:** `agent.install` / `agent.install_tenant`.

### 4.6 `POST /v1/agents/{id}/uninstall` — uninstall

**Effects:** deletes the `agent_installs` row for the caller (or for all tenant members on `scope="tenant"`). Per-user overrides are dropped.

**Routines + Projects side-effects (cascade rules):** uninstalling an agent does NOT cascade-delete Routines or Project defaults that reference it. Per cross-audit §5.3, the references become dead links:

- Routines using the agent live (`agent_version_pin = null`): the Routine `auto_pauses` per Routines §9.7 Q4 next fire-attempt; an Inbox CTA surfaces.
- Routines pinning a specific `AgentVersion`: continue to fire (snapshot is sufficient — the install is irrelevant once a version is pinned because the runtime resolves the version, not the install).
- Projects with `default_agent_id = <this>`: the field is preserved; new chats under the project pre-fill the agent_id only if the user has it installed at chat-create time; otherwise pre-fill is skipped. (Project owners see a soft warning on the Project Settings page.)

**Audit:** `agent.uninstall`.

### 4.7 `POST /v1/agents/{id}/versions` — snapshot

**Body:** `{ label?: string }`.

**Effects:**

- Reads the live `agents` row.
- Inserts an `agent_versions` row with `version = agents.version + 1`, snapshotting all fields per §3.1.
- Bumps `agents.version` atomically (`UPDATE agents SET version = version + 1 WHERE id = $1 RETURNING version`).
- Returns the new `AgentVersion`.

**ACL:**

- `origin="custom"`: owner only.
- `origin="system" | "community"`: forbidden (system/community versioning is managed server-side by the catalog seeder, NOT by user PATCHes; see §11 Q5).

**Audit:** `agent.version_snapshot` with `context = { version_id, label }`.

**Concurrency:** the bump is in a single Postgres transaction with `SELECT ... FOR UPDATE` on the `agents` row; two simultaneous snapshots produce two distinct versions with no gap.

### 4.8 `GET /v1/agents/{id}/versions` — list versions

**Query:** `cursor`, `limit` (default 20).

**Response:** `{ items: AgentVersion[], next_cursor }`.

**ACL:** any caller who can read the agent (§4.2).

### 4.9 `GET /v1/agents/{id}/usage` — usage (read-only projection over `runtime_model_call_usage`)

**Query:** `period=day|week|month` (default `week`), `since` ISO8601 (default = `now - 30d`).

**Effects:** facade forwards to ai-backend's `/v1/usage/org/agent/<agent_id>?period=…` (NEW route added to ai-backend's usage routes — minimal Python delta because `runtime_run_meta.agent_id` is already attributable).

**Response:**

```typescript
interface AgentUsageResponse {
  readonly agent_id: AgentId;
  readonly period: UsagePeriod;
  readonly rollups: ReadonlyArray<AgentUsageRollup>;
  readonly totals: AgentUsageRollup; // sum across all rollups
}
```

**ACL:** the caller must be able to read the agent (§4.2). Admin role can read tenant-wide totals; member role sees their own attribution only (per cross-audit §5.5 RBAC; same as the existing `/v1/usage/me` / `/v1/usage/org` split).

### 4.10 `POST /v1/agents/{id}/duplicate` — fork to custom

**Body:** `{ name?: string }` (auto-suggested if absent: `"<original> (custom)"`).

**Effects:** copies the canonical fields (instructions, model, skills, connectors, permissions) into a new `agents` row with `origin="custom"`, `owner_user_id = caller`, `status="draft"`. The duplicate carries a `forked_from_agent_id: AgentId` provenance field (see §5.1).

**ACL:** caller must be able to read the source agent.

**Audit:** `agent.duplicate` with `context = { source_agent_id, source_version }`.

### 4.11 Internal endpoints (consumed by `services/ai-backend/`)

| Endpoint                                      | Caller                            | Returns                               | Purpose                                                                                                                                                             |
| --------------------------------------------- | --------------------------------- | ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /internal/v1/agents/<id>?as_user_id=<u>` | ai-backend (run construction)     | merged `Agent` view (with overrides)  | Resolved at chat-run or routine-fire time. Returns 404 if the user can't see the agent (auto-pause path for Routines).                                              |
| `GET /internal/v1/agents/<id>/versions/<vid>` | ai-backend (routine pinned fire)  | `AgentVersion` snapshot               | Returns the immutable snapshot regardless of current agent status (per the pinning contract). Returns 404 only if the version itself is deleted (tenant-GDPR path). |
| `GET /internal/v1/agents/<id>/membership/<u>` | ai-backend, cross-service helpers | `{ installed: bool, disabled: bool }` | Cheap predicate for picker-visibility checks.                                                                                                                       |

All internal endpoints require `X-Enterprise-Service-Token` + `x-enterprise-org-id` + `x-enterprise-user-id` per the existing service-token convention.

### 4.12 SSE — `GET /v1/agents/stream`

Pushes typed events to a subscribed client:

- `agent_installed` — `{ agent_id, user_id, scope }`.
- `agent_uninstalled` — `{ agent_id, user_id }`.
- `agent_updated` — `{ agent_id, version }`.
- `agent_version_snapshot` — `{ agent_id, version_id, version }`.
- `agent_status_changed` — `{ agent_id, status, prior_status }`.

Per cross-audit §5.2: `event:` + `data:` fields, `Last-Event-ID` reconnect. The frontend's `AgentsDestination` subscribes on mount; the composer's agent-picker subscribes to update its dropdown live.

### 4.13 Filter / sort allowlist (per cross-audit §1.5)

| Axis            | Allowed values                                | Multi-value | Sort allowed                                               |
| --------------- | --------------------------------------------- | ----------- | ---------------------------------------------------------- |
| `origin`        | `system`, `community`, `custom`               | yes (OR)    | n/a                                                        |
| `status`        | `installed`, `available`, `disabled`, `draft` | yes (OR)    | n/a                                                        |
| `skill_id`      | any `SkillId`                                 | yes (OR)    | n/a                                                        |
| `connector_id`  | any `ConnectorId`                             | yes (OR)    | n/a                                                        |
| `owner_user_id` | any `UserId` (admin-only)                     | yes (OR)    | n/a                                                        |
| `q`             | text search                                   | no          | n/a                                                        |
| `sort`          | n/a                                           | n/a         | `updated_at:desc`, `name:asc`, `usage.cost_usd_micro:desc` |

Any filter axis not in the allowlist returns `400 Bad Request` with `{ error: "filter_not_allowed", axis }`.

---

## §5 Storage

All tables live in `services/backend/` (product persistence). The `services/ai-backend/` layer does **not** introduce new tables. Per cross-audit §5.5, usage is a read-only projection of the existing `runtime_model_call_usage` + `runtime_run_meta` tables — no new tracker.

### 5.1 `agents` table (Postgres, owned by `services/backend`)

```sql
CREATE TABLE agents (
  id                 UUID PRIMARY KEY,
  tenant_id          UUID NOT NULL,
  name               TEXT NOT NULL,
  slug               TEXT NOT NULL,
  description        TEXT NOT NULL DEFAULT '',
  icon_emoji         TEXT NOT NULL DEFAULT '🤖',
  color_hue          INTEGER NOT NULL DEFAULT 220 CHECK (color_hue >= 0 AND color_hue < 360),
  version            INTEGER NOT NULL DEFAULT 1,
  status             TEXT NOT NULL CHECK (status IN ('installed','available','disabled','draft')),
  origin             TEXT NOT NULL CHECK (origin IN ('system','community','custom')),
  owner_user_id      UUID NULL,
  instructions       TEXT NOT NULL DEFAULT '',
  model_id           TEXT NOT NULL,
  reasoning_depth    TEXT NOT NULL CHECK (reasoning_depth IN ('fast','balanced','deep')),
  skills             JSONB NOT NULL DEFAULT '[]'::JSONB,
  connectors_default JSONB NOT NULL DEFAULT '[]'::JSONB,
  permissions        JSONB NOT NULL,
  memory_ref         JSONB NULL,
  forked_from_agent_id UUID NULL REFERENCES agents(id) ON DELETE SET NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at         TIMESTAMPTZ NULL, -- soft delete tombstone
  UNIQUE (tenant_id, slug) WHERE deleted_at IS NULL,
  CONSTRAINT custom_must_have_owner CHECK (
    (origin = 'custom' AND owner_user_id IS NOT NULL)
    OR (origin <> 'custom' AND owner_user_id IS NULL)
  )
);

CREATE INDEX agents_tenant_status_idx ON agents (tenant_id, status) WHERE deleted_at IS NULL;
CREATE INDEX agents_tenant_origin_idx ON agents (tenant_id, origin) WHERE deleted_at IS NULL;
CREATE INDEX agents_tenant_owner_idx  ON agents (tenant_id, owner_user_id) WHERE deleted_at IS NULL;
CREATE INDEX agents_slug_idx          ON agents (tenant_id, slug) WHERE deleted_at IS NULL;
-- For full-text search (q parameter)
CREATE INDEX agents_search_idx ON agents USING GIN (
  to_tsvector('english', name || ' ' || coalesce(description,''))
) WHERE deleted_at IS NULL;
```

### 5.2 `agent_versions` table — immutable snapshots

```sql
CREATE TABLE agent_versions (
  id                  UUID PRIMARY KEY,
  agent_id            UUID NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
  tenant_id           UUID NOT NULL,
  version             INTEGER NOT NULL,
  instructions_snapshot       TEXT NOT NULL,
  model_id_snapshot           TEXT NOT NULL,
  reasoning_depth_snapshot    TEXT NOT NULL,
  skills_snapshot             JSONB NOT NULL,
  connectors_default_snapshot JSONB NOT NULL,
  permissions_snapshot        JSONB NOT NULL,
  label               TEXT NULL,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_by          UUID NOT NULL,
  UNIQUE (agent_id, version)
);

CREATE INDEX agent_versions_tenant_idx ON agent_versions (tenant_id, agent_id, version DESC);
```

The `ON DELETE RESTRICT` on `agent_id` is intentional: deleting a live agent is blocked while versions exist. The agent-delete path (§5.3) does a **soft delete** (sets `deleted_at`), leaving versions intact. Hard delete is GDPR-only.

### 5.3 `agent_installs` table — per-user installation + overrides

```sql
CREATE TABLE agent_installs (
  id            UUID PRIMARY KEY,
  tenant_id     UUID NOT NULL,
  agent_id      UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  user_id       UUID NOT NULL,
  installed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  disabled      BOOLEAN NOT NULL DEFAULT FALSE,
  overrides     JSONB NULL,
  UNIQUE (tenant_id, agent_id, user_id)
);

CREATE INDEX agent_installs_user_idx     ON agent_installs (tenant_id, user_id);
CREATE INDEX agent_installs_agent_idx    ON agent_installs (tenant_id, agent_id);
CREATE INDEX agent_installs_disabled_idx ON agent_installs (tenant_id, user_id, disabled);
```

`ON DELETE CASCADE` here: if an agent is hard-deleted (GDPR), its install rows go too. Soft delete (the default) leaves installs alone; the agent simply stops resolving via `resolve_agent_view` because `deleted_at IS NOT NULL`.

### 5.4 Retention + cleanup (per master §3.3)

| Object                | Soft-delete TTL | Hard-delete after                      | Notes                                                                          |
| --------------------- | --------------- | -------------------------------------- | ------------------------------------------------------------------------------ |
| `agents` (custom)     | 30d             | 90d                                    | Owner-initiated delete; second confirmation. Reversible during the 30d window. |
| `agents` (system)     | n/a             | never                                  | Managed by catalog seeder; "retirement" is a status flip, not a delete.        |
| `agents` (community)  | n/a             | never (Wave 8+)                        | Same as system; community retirement governance is Wave 8 (master §10 Q1).     |
| `agent_versions`      | n/a             | never                                  | Immutable historical records; anonymized in tenant-GDPR (audit policy).        |
| `agent_installs`      | n/a             | on agent hard-delete or user-uninstall | No tombstone needed; install is a low-stakes row.                              |
| `AuditRow` (agent.\*) | 365d (default)  | 7y (tenant config)                     | Per master §3.2 + cross-audit §1.4.                                            |

A nightly cleanup job in `services/backend/src/backend_app/agents/cleanup.py` hard-deletes `agents` rows where `deleted_at < NOW() - INTERVAL '90 days'` AND the agent has no `agent_versions` referenced by any `routines.agent_version_pin`. Routines pinning a version block hard-delete (the version is referenced).

### 5.5 Tenant-scoped install materialization

`POST /v1/agents/<id>/install?scope=tenant` runs a single SQL `INSERT … SELECT` from `tenant_members` to `agent_installs` with `ON CONFLICT DO NOTHING`. New members joining the tenant later do NOT auto-install — they see the agent in `/agents` with status `available`, install on click. This is intentional: tenant-wide install is a **promote-now** action, not a policy. (Wave 6 may add a policy-style mode; see §11 Q1.)

### 5.6 Usage join — no new tables

`GET /v1/agents/<id>/usage` is served by `services/ai-backend/src/runtime_api/http/usage_routes.py` (new route `/v1/usage/org/agent/<agent_id>`) which aggregates against `runtime_model_call_usage` joined on `runtime_run_meta.agent_id`. The facade proxies through. **No new tracker, no new table** — the cross-audit §5.5 invariant is preserved (single source of truth for token usage).

The facade caches the per-agent usage projection for 60s per `(tenant_id, agent_id, period)` to keep gallery-card rendering cheap when 50 cards each carry a 7-day chip.

---

## §6 Audit + ACL

### 6.1 Audit action taxonomy (per master §3.2 + cross-audit §1.4 `context`)

| Action                   | Triggered by                                | `before_state`         | `after_state`                        | `context`                                               |
| ------------------------ | ------------------------------------------- | ---------------------- | ------------------------------------ | ------------------------------------------------------- | --------- |
| `agent.create`           | `POST /v1/agents`                           | `null`                 | full record (sans long instructions) | `{}`                                                    |
| `agent.update`           | `PATCH /v1/agents/<id>`                     | changed fields only    | changed fields only                  | `{ instructions_hash_before, instructions_hash_after }` |
| `agent.soft_delete`      | `DELETE /v1/agents/<id>` (custom only)      | `{ deleted_at: null }` | `{ deleted_at: <ts> }`               | `{}`                                                    |
| `agent.hard_delete`      | nightly cleanup OR GDPR delete              | `{ deleted_at: <ts> }` | full record set to `null`            | `{ reason: "retention"                                  | "gdpr" }` |
| `agent.install`          | `POST /v1/agents/<id>/install` (user scope) | `null`                 | `{ user_id, scope: "user" }`         | `{}`                                                    |
| `agent.install_tenant`   | install with `scope="tenant"`               | `null`                 | `{ scope: "tenant", member_count }`  | `{}`                                                    |
| `agent.uninstall`        | `POST /v1/agents/<id>/uninstall`            | `{ user_id }`          | `null`                               | `{}`                                                    |
| `agent.disable`          | toggle via `PATCH /v1/agent_installs/<id>`  | `{ disabled: false }`  | `{ disabled: true }`                 | `{}`                                                    |
| `agent.enable`           | reverse toggle                              | `{ disabled: true }`   | `{ disabled: false }`                | `{}`                                                    |
| `agent.version_snapshot` | `POST /v1/agents/<id>/versions`             | `{ version: <prev> }`  | `{ version: <new>, version_id }`     | `{ label }`                                             |
| `agent.duplicate`        | `POST /v1/agents/<id>/duplicate`            | `null`                 | new agent record                     | `{ source_agent_id, source_version }`                   |
| `agent.status_change`    | system-side seeder retires an agent         | `{ status: <prev> }`   | `{ status: "disabled" }`             | `{ reason: "deprecated_by_catalog" }`                   |

Every row carries `tenant_id`, `actor_user_id` (the principal — `actor_kind = "system"` for catalog-seeder rows), `target_kind = "agent"` or `"agent_install"` or `"agent_version"`, `target_id`, `ts`, `request_id` per master §3.2.

### 6.2 ACL matrix

| Action                                | Tenant member        | Custom-agent owner | Tenant admin                             |
| ------------------------------------- | -------------------- | ------------------ | ---------------------------------------- |
| Read system/community agent (catalog) | ✓                    | ✓                  | ✓                                        |
| Read custom agent (not owner)         | ✗ (404)              | ✓                  | ✓ (compliance read; audited)             |
| Create custom agent                   | ✓                    | n/a                | ✓                                        |
| Edit custom agent (PATCH)             | ✗                    | ✓                  | ✗ (admin cannot mutate user agents)      |
| Install / uninstall for self          | ✓                    | ✓                  | ✓                                        |
| Install tenant-wide (`scope=tenant`)  | ✗                    | ✗                  | ✓                                        |
| Disable for self                      | ✓                    | ✓                  | ✓                                        |
| Disable tenant-wide                   | ✗                    | ✗                  | ✓                                        |
| Snapshot version (custom agent)       | ✗                    | ✓                  | ✗                                        |
| Snapshot version (system / community) | ✗                    | n/a                | ✗ (catalog-managed, see §11 Q5)          |
| Read usage — own attribution          | ✓                    | ✓                  | ✓                                        |
| Read usage — tenant-wide totals       | ✗                    | ✗                  | ✓                                        |
| Read audit rows                       | ✗ (own actions only) | ✗                  | ✓                                        |
| Force-uninstall for another user      | ✗                    | ✗                  | ✓ (compliance — `agent.force_uninstall`) |

**404-not-403 rule** (per cross-audit §1.3 master rule, generalized): when a user cannot read an agent, the response is `404 Not Found` with `{ error: "agent_not_found" }`, not `403`. This prevents existence enumeration of other users' custom drafts.

### 6.3 Project-default-agent ACL (per Projects §12 Q3 resolution pattern)

When a Project sets `default_agent_id`:

- The selecting user must have the agent installed (verified server-side at PATCH time).
- The agent must NOT be in `disabled` or `draft` status.
- Project members who don't have the agent installed see it pre-filled in chat but with a soft inline hint: _"Pre-filled from project default. You don't have this agent installed — click to install or change."_
- Audit `project.update` carries `context = { default_agent_id_before, default_agent_id_after }` per the existing Projects audit shape.

### 6.4 Cross-tenant safety

Every query in `services/backend/src/backend_app/agents/store.py` opens with `WHERE tenant_id = $tenant_id`. Tests exhaustively cover cross-tenant read/install/duplicate attempts. The `services/ai-backend/` internal endpoints also enforce the tenant scope on the service-token header — never trust caller-supplied `tenant_id`.

---

## §7 Layout + UI

The destination is a gallery-first surface — _app store, not database admin tool_. Every visual choice is in service of "I want to find an agent that does X, see what it costs, install it, customize it" — not "show me the rows in the agents table".

### 7.1 Route map

| Route                   | View                   | Notes                                                                                                                |
| ----------------------- | ---------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `/agents`               | Gallery (default)      | Cards grid, filter chips, search bar, primary "Create custom agent" action.                                          |
| `/agents/new`           | Editor (blank)         | Form scaffold from `<AgentEditor>`.                                                                                  |
| `/agents/<id>`          | Detail panel + gallery | Detail rendered as right-rail when card clicked from gallery, OR as a full-page detail when deep-linked / refreshed. |
| `/agents/<id>/edit`     | Editor (pre-filled)    | Owner-only mutation surface.                                                                                         |
| `/agents/<id>/versions` | Versions tab           | List of `AgentVersion` rows with label + created_by + snapshot diff button.                                          |
| `/agents/<id>/usage`    | Usage tab              | 7d/30d cost + run-count chart.                                                                                       |

The shell's existing `<PageHeader>` (cross-audit §1.6) wraps all routes: title = "Agents", subtitle = "<n> installed · <m> available", primaryAction = "Create custom agent".

### 7.2 Gallery view (`AgentsDestination`)

**Above the fold:**

- `<PageHeader title="Agents" subtitle="…" primaryAction={...} />`.
- A horizontal **filter chips** strip: **All · My agents · Installed · Available · Custom · System · Community · By skill · By connector**.
  - "My agents" = `origin=custom AND owner_user_id=me`.
  - "By skill" / "By connector" expand into popovers offering multi-select.
- A **search bar** (top-right of the filter strip), debounced 250ms → calls `GET /v1/agents?q=…`.
- A **sort dropdown** (right of search): "Recently updated · Name (A→Z) · Cost (7d ↓) · Cost (7d ↑)".

**The grid:** virtualized `<CardGrid>` (shared primitive — cross-audit §4 SP-1). Each card is `<AgentCard>` with:

- Icon (large emoji on a `hsl(<hue>, 60%, 90%)` background swatch).
- Name (one line, ellipsized at ~24 chars).
- Description (two lines, ellipsized).
- A row of **chips**:
  - `OriginChip` — "System" / "Community / by Acme Co." / "Custom".
  - `UsageChip` — "$0.42 · 7d" with a small spark-line; hidden when `viewer_usage_7d` is `null` or zero.
  - `StatusChip` — only rendered when status is not `installed` or `available` (i.e., disabled/draft).
- A footer action row:
  - For `available`: **Install** button (primary).
  - For `installed`: **Installed ✓** secondary button + overflow menu (Customize / Disable / Uninstall).
  - For `disabled`: **Re-enable** + Uninstall.
  - For `draft` (owner only): **Edit** + **Install** + **Delete draft**.
- Card click (anywhere outside the buttons) opens the detail right-rail.

**Empty states:**

- "My agents" with no installs: hero copy "_You haven't installed any agents yet._" + 3–4 **recommended starter cards** (system agents seeded for the tenant — Calendar Whisperer, Inbox-Triage, Slack Summarizer, Research) each with a one-tap **Install** button. This is the "show value first" pattern; we explicitly avoid showing "0 installs" with no path forward.
- "All" empty: hero copy "_No agents match your filters._" with a Clear-filters button.
- Loading: skeleton grid with 12 placeholder cards matching the final shape (no layout shift).
- Error: `<EmptyState icon="alert" title="Couldn't load agents" sub={error.message} action="Retry" />`.

**Density:** matches the design-system `data-density` token. Default density: 3 cards/row at 1280px, 4 cards/row at 1600px, 2 cards/row at 960px, 1 card/row at <720px.

### 7.3 Detail panel (right-rail)

Opens when a card is clicked. On wide viewports (>1280px) the gallery and the detail render side-by-side; on narrower viewports the detail swaps in (single-pane). Detail panel contents:

- Hero: large icon swatch + name + slug + origin chip + status chip.
- Description (full text, not truncated).
- **Quick facts grid** (4 cells, 2x2): Model · Reasoning depth · Skills count · Connectors count.
- **Instructions preview** (collapsed by default — "Show instructions" disclosure). When expanded, renders the markdown-rendered system prompt in a read-only block.
- **Usage chart** — 30-day spark-line by day (run count + cost). Uses `<UsageSparkline>` (new shared primitive — promoted to design-system if more destinations need it; for Phase 8 it lives in the agents folder).
- **Version history** — last 3 versions with label + created_at + "View all →" link to the Versions tab.
- **Action row** (sticky bottom):
  - `available` → **Install** (primary).
  - `installed` → **Customize** (secondary) + overflow (Disable / Uninstall / Duplicate).
  - `installed` owner → **Edit** (primary) + **Save as version** (secondary) + overflow.
  - `disabled` → **Re-enable** + Uninstall.

### 7.4 Editor (`/agents/new`, `/agents/<id>/edit`)

A tab strip in the rail: **Identity · Instructions · Model · Skills · Connectors · Permissions · Memory** (last is forward-compat, disabled in Phase 8 with a "Phase 11" badge).

- **Identity** — name, slug, description, icon emoji picker (uses the existing `<EmojiPicker>` from chats-canvas), color hue slider (live preview swatch).
- **Instructions** — large multi-line markdown editor with:
  - Live token count (client-side estimator).
  - A linter that flags common mistakes (instructions with `{user_name}` placeholder that no longer interpolates; instructions referencing tool names that aren't in `skills`).
  - A "Generate from description" affordance — DEFERRED to Wave 6 (would require an LLM call here, which violates the §1 anti-pattern of cheap-listing-views NOT making LLM calls; defer).
- **Model** — model id picker, reasoning depth selector, max output tokens slider.
- **Skills** — chip picker over `GET /v1/tools?filter[kind]=skill` (cross-references Phase 9; until Phase 9 lands, reads from `services/backend/src/backend_app/skills/` directly via the facade).
- **Connectors** — chip picker over installed connectors. Each chip shows the connector icon + scope.
- **Permissions** — autonomy radio (manual-approval / auto-apply), max tool calls per run, read-only toggle, blocked tool families chip picker.
- **Memory** — disabled in Phase 8 (forward-compat field only).

Save row at the bottom: **Save as draft** (status=draft) + **Save & install** (status=installed) + **Cancel** (discards changes, confirm dialog if dirty). Snapshot row: **Save as version** opens a dialog asking for a label, then `POST /v1/agents/<id>/versions`.

### 7.5 Per-card usage chip — "$0.42 last 7 days"

This chip is non-negotiable per the §7 UX guidance: **show cost prominently**.

- Rendered when `viewer_usage_7d.cost_usd_micro > 0`.
- Format: `$<dollar>.<cents> · 7d`. Uses `formatRelativeTime` style succinct presentation.
- Hover shows a 7-day spark-line + run count + total tokens.
- Click navigates to Home recent-runs filtered by `agent_id` (deep link via the existing Home query string filter).

### 7.6 Accessibility (per master §3.6)

- Every card is keyboard reachable; Enter opens the detail; Space toggles install/disable.
- Icons have `aria-hidden="true"` when paired with the agent name (label carried by text). Standalone icons (in the action row) carry `aria-label`.
- The filter chip row is a `role="toolbar"` with arrow-key navigation between chips.
- Color is never the only carrier of state (origin / status chips carry icon + text).
- Reduced motion: hover micro-animations on cards are skipped when `prefers-reduced-motion: reduce`.
- Live region announces "Agent installed" / "Agent disabled" after action completion.
- High-contrast theme verified for chips, hue swatches, and disabled-state desaturation.

### 7.7 Composer agent picker (cross-surface — read from this PRD's wire)

The composer's existing model/agent picker (today a flat model list) becomes a two-section picker in Phase 8:

- **Top section: Agents** — populated by `GET /v1/agents?filter[status]=installed`. Each row: icon + name + cost chip + "i" info button (opens the agents-detail panel).
- **Bottom section: Models** — the raw model id list (existing).

When an agent is picked, the composer sets `Conversation.agent_id` and the model field auto-fills from the agent's `model_default` (overridable). This change is **owned by the Chats destination** (per separation of concerns); the Agents PRD only specifies the wire (`GET /v1/agents?filter[status]=installed`) and the visual contract (icon + name + cost chip). The Chats Phase 1.6 composer change is a follow-up issue tracked outside this PRD.

---

## §8 Cross-destination integration

### 8.1 Chats integration

- **Wire field:** `Conversation.agent_id: AgentId | null` (existing in chats wire).
- **Resolve at run-start:** ai-backend reads the agent via `GET /internal/v1/agents/<id>?as_user_id=<u>` and composes its `instructions`, `model_default`, `skills`, `connectors_default` into `DeepAgentBuildRequest`. This mirrors the Routines §1.4 source-of-truth rule (live re-resolve, not snapshot, at fire time).
- **Detail UI:** chat detail's right-rail shows "Agent: <name> ↗" → `<ItemLink kind="agent" id={conversation.agent_id} />`.
- **Cascade rule (per cross-audit §5.3):** deleting an Agent leaves `Conversation.agent_id` as a dead link; the chat continues to render but shows "<deleted agent>" with the agent's last-known name in a `<ItemRefSnapshot>` denormalization (so the user has context).

### 8.2 Routines integration (per cross-audit §9.7 Q11)

- **Wire fields:** `Routine.agent_id: AgentId | null` + `Routine.agent_version_pin: AgentVersionId | null` (already specified in routines-prd §4.1 / §1.4 / §9.7 Q11).
- **Live resolution (`agent_version_pin = null`):** routine fire calls `GET /internal/v1/agents/<id>?as_user_id=<u>`. If 404, the routine auto-pauses per Routines §9.7 Q4. The composed run carries `run.agent_id = <id>` (NOT a `version_id`), so usage attributes to the live agent.
- **Pinned resolution (`agent_version_pin = <vid>`):** routine fire calls `GET /internal/v1/agents/<id>/versions/<vid>`. The snapshot's fields override the live agent's. The composed run carries `run.agent_id = <id>` AND `run.agent_version_id = <vid>`. Usage attributes to the agent (existing dimension) plus the version (new dimension on `runtime_run_meta.agent_version_id`).
- **Routines version-pin UI:** the Routine editor's agent picker gains a "Pin version" toggle (§7.4 U8). When pinned, the version dropdown lists `AgentVersion` rows from `GET /v1/agents/<id>/versions`.

### 8.3 Projects integration (per Projects §12 Q3 connector-override pattern)

- **Wire field added by P8-A1:** `Project.default_agent_id: AgentId | null`. Migration lands as part of Phase 8 P8-A1's cross-cut (because Projects is already shipped — the field is added forwards-compatibly).
- **New-chat pre-fill:** when a user creates a chat from a project context (Project detail → "New chat"), the composer pre-fills `Conversation.agent_id = project.default_agent_id` when the user has the agent installed; otherwise pre-fill is skipped with a soft hint.
- **Project Settings UI:** Projects' Settings tab gains a "Default agent" picker. The picker is the shared `<AgentPicker>` component used by Chats' composer (DRY — promoted to `packages/chat-surface/src/destinations/agents/AgentPicker.tsx` and re-exported from the agents folder's index).

### 8.4 Home integration

- "Agents you used most" panel — calls `GET /v1/agents?sort=usage.cost_usd_micro:desc&limit=5&filter[status]=installed`. Each card is a compact `<AgentCard variant="home">` with name + cost chip + "Open →".
- "Build an agent" quick action (master §5.1 panel) → routes to `/agents/new`.

### 8.5 Inbox integration

- `InboxItem.sender.kind = "agent"` resolves via `<ItemLink kind="agent" id={sender.ref.id} />`. The registry resolver returns `{ label: agent.name, icon: <emoji>, route: { kind: "agents-detail", id: agent.id } }`.
- Existing Inbox CTAs that reference an agent (e.g., the Routines auto-pause CTA per Routines §9.7 Q4) navigate to the agent detail panel.

### 8.6 Todos integration

- `Todo.source.agent_id` already exists in todos-prd §3.2. Phase 8 adds nothing on the Todos side; the existing `<ItemLink kind="agent">` resolver covers display.

### 8.7 Tools / Connectors integration

- An agent's `skills` and `connectors_default` are picked from Phase 9 / Phase 10 endpoints. Until those phases land, the Agents editor reads from the existing `services/backend/src/backend_app/skills/` and the MCP registration store directly (no new fan-out). When Phase 9 lands, the editor switches to `GET /v1/tools?filter[kind]=skill`; the wire field doesn't change.

### 8.8 Memory integration (Phase 11 forward-compat)

- `Agent.memory_ref: MemoryRef | null` is reserved in Phase 8 and editable only behind a feature flag (`atlas.memory.enabled`, default `false` in Phase 8). When Phase 11 lands, the Memory tab in the editor becomes active. No P8-side wiring beyond the field reservation.

---

## §9 Performance

### 9.1 Budgets (per master §3.7)

| Surface                          | LCP target | INP target | Notes                                                                             |
| -------------------------------- | ---------- | ---------- | --------------------------------------------------------------------------------- |
| Gallery (cold)                   | < 2.5s     | < 200ms    | One round-trip (composed payload). 50 cards fit easily.                           |
| Gallery (warm — filter switch)   | < 100ms    | < 100ms    | Local filter (no network) when within the loaded page; network filter when paged. |
| Detail panel open                | < 200ms    | < 100ms    | Pre-fetched from list response's denormalized `viewer_usage_7d` + agent record.   |
| Detail full-page open (deeplink) | < 800ms    | < 200ms    | Two round-trips: detail + versions list (parallel).                               |
| Usage chart load                 | < 600ms    | n/a        | Server-cached 60s; chart renders client-side from the projection.                 |
| Editor open                      | < 300ms    | < 200ms    | All fields are already in the agent record; no extra fetch.                       |
| Install / uninstall              | < 400ms    | < 100ms    | Optimistic UI; rollback on error with toast.                                      |

### 9.2 Pagination

`GET /v1/agents` cursor-paginates at 50/page (max 200). The frontend uses an `<InfiniteScroll>` pattern within the gallery body. Filter changes reset the cursor.

### 9.3 Usage projection caching

The facade caches `GET /v1/agents/<id>/usage` for 60s per `(tenant_id, agent_id, period)`. Aggregating 50 cards' 7-day chips on a cold gallery load happens server-side in `services/ai-backend/`'s usage routes via a single SQL aggregation (`WHERE agent_id IN (…)` over `runtime_model_call_usage`). One round-trip from facade → ai-backend, not 50.

### 9.4 Install cache

The composer's agent picker re-fetches installed agents on:

- destination mount.
- `agent_installed` / `agent_uninstalled` / `agent_status_changed` SSE event.
- focus regain (window `focus` event) after >5min idle.

No polling. SSE is the source of truth.

### 9.5 Editor save latency

`PATCH /v1/agents/<id>` writes synchronously and returns the updated record. Optimistic UI applies the changes locally and reverts on error. Snapshot `POST /v1/agents/<id>/versions` is also synchronous (single transaction). Save latency budget: 400ms p95.

---

## §10 Community / marketplace (wire shape now, submission flow deferred)

Phase 8 ships the **wire shape** for community-origin agents so the catalog can grow:

- `origin = "community"` is a valid value on `agents.origin`.
- `agents` table has `publisher_org_name: TEXT NULL` and `publisher_org_url: TEXT NULL` (for the "by Acme Co." byline on community cards).
- `agents.review_status: TEXT NULL CHECK (review_status IN ('approved','pending','rejected'))` — `NULL` for system/custom, set for community.
- A nightly seeder reads from a curated `community_agent_catalog` JSON manifest (checked into `services/backend/seeds/community_agents.json`) and upserts the agents into the tenant catalog.

What Phase 8 explicitly does NOT ship:

- User-driven submission UI (Wave 6 / Wave 8).
- Tenant-admin moderation queue.
- Per-org publisher pages.
- Rating + review surface.
- Verification badges.
- Revenue / monetization.

Wave 6+ may add a `POST /v1/agents/community-submit` flow and a `services/backend/src/backend_app/agents/moderation.py` review module. The wire shape (`origin`, `review_status`, `publisher_*`) is forward-compatible with that future flow — installing a Wave-6 community agent into a Phase-8-built tenant won't require migration.

See §11 Q6 for the call.

---

## §11 Open product questions (orchestrator to resolve before P8-A / P8-B dispatch)

### Q1. Per-user vs per-tenant install scope

**Question:** is installation a per-user action (default), or does tenant admin pre-install for everyone?

**Recommendation:** **per-user by default with tenant-admin pre-install via `scope="tenant"`** (covered in §4.5). Don't auto-install for future-joining members in Phase 8 (deferred Wave 6 policy-mode).

**Open variant:** should tenant admins be able to author a tenant-scoped agent that auto-installs for every member? **Recommend no** in Phase 8 — adds policy + override complexity that Phase 8 doesn't need. Tenant admins instead install a system/community agent at `scope="tenant"`.

### Q2. Custom agent visibility scope (private / project / tenant)

**Question:** when a user creates a custom agent, who can see it by default?

**Recommendation:** **private to user** (`status="installed"` for owner only, not visible to other tenant members). To share, the owner explicitly toggles a `share_with_tenant` flag on the agent (NEW field — not in the §3.1 wire above; if approved, add `Agent.shared_with_tenant: boolean` in P8-A1).

**Alternatives considered + rejected:**

- "Shared in project" — too granular for Phase 8; would require ACL by project membership for a custom agent. Defer to Wave 6.
- "Shared in tenant by default" — surprising; the user expects their custom edits to be private until they actively share.

**Open ask for product:** confirm "private by default + explicit tenant share" wire.

### Q3. Versioning: explicit snapshot vs auto-snapshot on PATCH

**Question:** does every `PATCH /v1/agents/<id>` create an `AgentVersion`, or does the user explicitly snapshot via `POST /v1/agents/<id>/versions`?

**Recommendation:** **explicit snapshot** (the §3.2 + §4.7 design above). Reasoning: auto-snapshot creates hundreds of versions per agent over weeks of editing; explicit matches the "tag a release" mental model; auto would force every Routine pinning to specify a version-of-versions (which would defeat the point).

**Confirm with product.**

### Q4. System-agent override semantics — does my edit fork or shadow?

**Question:** when a user clicks "Customize" on a system agent and edits the instructions, does the system agent get a per-user overlay (shadow), or does the editor force a duplicate to a custom agent (fork)?

**Recommendation:** **fork-only** for instructions/model/skills/connectors changes (§7.3 detail panel + §U2 journey). Per-user `overrides` via `AgentInstall.overrides` (§3.3) is reserved for **thin layer** changes (permissions tweak, model-default override) — NOT instruction changes (instruction edits force a duplicate). Reasoning: an instruction edit is a behavior change; behavior changes belong to a distinct record with a name, an icon, and a version history. Hiding it as an overlay creates "ghost agents" that look like the system one but behave differently — a debugging nightmare.

**Confirm with product:** the rule "instruction edit → duplicate; permissions tweak → overlay" — is the boundary right? Should permissions be in the overlay or also force a duplicate?

### Q5. Agent retirement — what happens to routines pointing at a retired agent?

**Question:** when a system or community agent is deprecated (catalog seeder marks `status="disabled"` with `context.reason="deprecated_by_catalog"`), what happens to Routines that reference it?

**Recommendation:**

- Routines with `agent_version_pin = <vid>` (pinned): **continue to fire normally**. The pinned snapshot is immutable; the agent's status doesn't affect resolution by version_id.
- Routines with `agent_version_pin = null` (live): **auto-pause** per Routines §9.7 Q4. An Inbox CTA surfaces with the retirement reason + a suggested replacement agent (server-managed mapping in the catalog manifest — `deprecated_replacement: AgentId | null`).
- New routine creation: cannot select a retired agent (it's `disabled`, hidden from the picker).

**Confirm with product:** is auto-pause-with-Inbox-CTA the right cascade, or should retirement immediately uninstall (more aggressive)?

### Q6. Marketplace approval workflow

**Question:** when a community agent submission flow lands (Wave 6+), how is moderation done?

**Recommendation:** **defer entirely to Wave 6+ governance PRD**. Phase 8 ships:

- the `origin="community"` wire value.
- `publisher_*` byline fields.
- a curated seed list in `services/backend/seeds/community_agents.json`.

Nothing else. Submission UI + review queue + ratings are a separate destination-level decision that needs product input on liability, IP, revenue. Out of Phase 8 scope.

### Q7. Cost-limit guards per agent

**Question:** can a tenant admin cap an agent at "$50/day workspace-wide" with auto-disable on breach?

**Recommendation:** **defer to Wave 6**. The wire shape (`AgentPermissions.daily_cost_cap_usd_micro: number | null` — NEW field, not in §3.1 above) is forward-compatible with this addition. Phase 8 reserves the wire field but the enforcement code (poll usage at end of run; auto-disable on breach) is Wave 6. Reasoning: usage is already attributable per cross-audit §5.5; the daily-cap math is straightforward but the UX of "your agent got disabled at $50.01 mid-run" needs more product thought (refund partial run? notify owner? grace?).

**Confirm with product:** is the wire-reservation the right Phase 8 scope?

### Q8. Default agent for the workspace

**Question:** when a user has zero installed agents and opens a new chat, what agent powers the run?

**Recommendation:** **a system-default fallback agent named "Atlas" is auto-installed for every new user** at IdP-onboarding time. The catalog seeder ensures every tenant has the "Atlas" agent in `system` origin with status `available`; a post-bearer-mint hook installs it for the new user. This guarantees the composer always has at least one agent.

**Confirm with product.**

### Q9. Slug uniqueness scope — tenant or global?

**Question:** is `agents.slug` unique per-tenant or globally?

**Recommendation:** **per-tenant unique** (per the §5.1 schema's `UNIQUE (tenant_id, slug)`). Reasoning: tenants need to author "salesforce_triage" without colliding with another tenant's "salesforce_triage". The `@`-mention picker filters by tenant anyway. Cross-tenant collision is a non-issue.

**Confirm.**

---

## §12 Token usage (per cross-audit §5.5)

**No new tracker.** Per the single-tracker invariant locked by Phase 0.6 (`tools/check_llm_provider_imports.py`), every LLM call in Atlas — including chats invoking an agent, routines firing an agent, and any future agent-internal subagent delegation — routes through `services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py::build_chat_model` and emits a `RuntimeModelCallUsageRecord` via the existing `UsageRecorder`.

### 12.1 Attribution dimensions already in place

The Agents destination requires NO new `Purpose` enum value. Every agent-triggered LLM call is already tagged:

- `run_id` (existing) → joins to `runtime_run_meta` which carries `agent_id` (existing).
- `purpose` (existing) → `MAIN` for the supervisor call, `SUBAGENT_WORK` for delegated subagents, `TOOL_PLANNING` / `TOOL_INTERPRETATION` for tool-related calls. The existing taxonomy is sufficient — agents don't introduce a new attribution mode.

The **only addition** Phase 8 makes on the ai-backend side is a route — `GET /v1/usage/org/agent/<agent_id>?period=…` in `services/ai-backend/src/runtime_api/http/usage_routes.py` — that aggregates per-agent. The Python delta is on the order of 30 LOC: a `WHERE agent_id = $1` filter on the existing `aggregate_usage` query path.

### 12.2 New TS contract types

```typescript
// packages/api-types/src/agents.ts (already shown in §3.1, restated for the usage contract)
export interface AgentUsageResponse {
  readonly agent_id: AgentId;
  readonly period: UsagePeriod;
  readonly rollups: ReadonlyArray<AgentUsageRollup>;
  readonly totals: AgentUsageRollup;
}
```

Re-exported from `packages/api-types/src/index.ts`. No conflict with existing `UsageOrgPurposeResponse` etc. — `AgentUsageRollup` is a per-agent aggregation, complementary.

### 12.3 Optional Phase 8.5 — `runtime_run_meta.agent_version_id`

The Routines version-pin feature (cross-audit §9.7 Q11) writes `run.agent_version_id: AgentVersionId | null` on every run that pins a version. The existing `runtime_run_meta` table gains one nullable column (Phase 8 ai-backend migration `0006_agent_version_id.sql`). All queries default to `agent_id` alone; version-aware queries (`GROUP BY agent_id, agent_version_id`) become possible without a tracker rewrite. This delta is part of the P8-A2 agent's scope.

### 12.4 PII + retention

- The new aggregation route emits NO message content, NO instructions, NO tool input/output — only token counts + cost + dimensions. PII-free by construction (matches the existing `/v1/usage/*` family).
- Retention is governed by the ai-backend Postgres retention policy (matches the audit window — typically 365d). The Agents destination's gallery just shows what the tracker still has.

---

## §13 Test plan

### 13.1 Backend / facade unit + integration (P8-A1 / P8-A2 / P8-A3)

- **CRUD happy path:** `POST /v1/agents` → `GET /v1/agents/<id>` returns the new record; `PATCH` mutates; soft-delete tombstones; hard-delete after 90d.
- **Idempotent install:** `POST /v1/agents/<id>/install` twice → 200, single row, single audit row on the first install only.
- **Tenant-scope install:** `POST /v1/agents/<id>/install?scope=tenant` (admin) inserts one row per current tenant member; new members joining later do NOT auto-install.
- **Cross-tenant read attempt:** user from tenant A queries `/v1/agents/<agent_in_tenant_B>` → 404 (NOT 403).
- **Custom-draft visibility:** user A creates a draft; user B in same tenant queries → 404. User A queries → 200.
- **System agent immutability:** `PATCH /v1/agents/<system_id>` → 409 `agent_origin_immutable`. `POST /v1/agents/<system_id>/duplicate` → 200 + new custom record with `forked_from_agent_id` set.
- **Version snapshot atomicity:** two concurrent `POST /v1/agents/<id>/versions` calls produce two distinct versions with `version = N+1, N+2` and no gap. Verified via parallel transaction test.
- **Version pin durability:** snapshot v3; PATCH the agent's instructions; `GET /v1/agents/<id>/versions/<v3_id>` still returns the original v3 instructions (immutability).
- **Routines auto-pause cascade:** Routine references agent live; uninstall the agent for the routine owner; tick the scheduler; routine auto-pauses (assert `routines.status = "paused"` + inbox row + audit row). Routine references agent pinned to v3; uninstall agent; tick scheduler; routine continues to fire (pinned snapshot is sufficient).
- **Projects default cascade:** Project sets `default_agent_id`; uninstall the agent for the project owner; new chat under project creates with `agent_id = null` (pre-fill skipped) and a soft-hint flag.
- **Usage join correctness:** seed 100 runs with mixed `agent_id`s in `runtime_model_call_usage`; `GET /v1/agents/<id>/usage?period=week` returns the correct token + cost rollup, attributable to the seeded rows and nothing else.
- **Tenant isolation in usage:** tenant A's `/v1/agents/<id>/usage` excludes tenant B's runs even if the agent_id collides by chance (verifies the `org_id = $tenant_id` filter is in the SQL).
- **Audit completeness:** every state-changing endpoint (create, update, delete, install, uninstall, disable, version, duplicate) emits exactly one audit row with the right `target_kind` + `target_id` + `before_state` + `after_state`. Long-instructions case: audit row stores hash + pointer, full text is retrievable via the audit-chain reader.
- **404-not-403 leak test:** for every action that could 403 (cross-tenant read, custom-draft read by non-owner, version read by non-reader), assert the response is 404 with `{ error: "agent_not_found" }` — not 403, not 200.
- **Slug uniqueness:** `POST` two agents with same slug in the same tenant → second returns 409 `slug_conflict`. Different tenants → both 200.
- **Override merging:** install an agent with `overrides.permissions.autonomy = "manual_approval"`; the agent's canonical `permissions.max_tool_calls_per_run` value comes through unchanged.

### 13.2 Frontend unit + integration (P8-B1 / P8-B2 / P8-B3 / P8-C)

- Gallery renders skeleton then 50 cards on initial fetch.
- Filter chip switch (Installed → Available) triggers `GET /v1/agents?filter[status]=available` and re-renders without shell flicker.
- Search bar debounces 250ms and fires one network call per debounce window.
- Card click opens detail right-rail; refresh deep-link to `/agents/<id>` opens full-page detail.
- Install button optimistic-updates the card's status chip; on network error the chip reverts and a toast appears.
- Disable / re-enable round-trip updates the card without re-rendering the gallery.
- Customize button on a system-agent card opens the fork-confirmation dialog; Confirm calls `POST /v1/agents/<id>/duplicate` and navigates to the new agent's editor.
- Editor save on a brand-new draft creates the record, sets `status="draft"`, and shows the agent in `/agents` filtered by "My agents".
- Save as version dialog accepts a label and round-trips through `POST /v1/agents/<id>/versions`.
- SSE event `agent_installed` triggers the gallery to re-fetch the affected card (in-place update, no scroll jump).
- Composer agent picker (cross-surface) reads from `GET /v1/agents?filter[status]=installed` and updates live on SSE events.
- Project Settings default-agent picker validates the chosen agent is `installed` for the caller (UI hint + server enforcement).
- Routine editor agent-picker "Pin version" toggle reveals the version picker; pinning sets `agent_version_pin` on save.
- Accessibility: axe-core passes on gallery + detail + editor + version picker.
- Keyboard navigation: Tab order is Logical (filter chips → search → cards in row-major → action buttons). Enter on card opens detail. Esc closes detail.

### 13.3 Cross-destination integration tests

- New chat under a project with `default_agent_id` pre-fills `Conversation.agent_id`; saving the chat persists the value; the run carries `run.agent_id`.
- Routine fire with `agent_version_pin = <v3_id>` produces a run where `runtime_run_meta.agent_id = <agent_id>` AND `runtime_run_meta.agent_version_id = <v3_id>` AND `runtime_model_call_usage` rows reflect the pinned model.
- Uninstalling an agent the user has chats with does NOT delete the chats; the chats render `<deleted agent>` chip with the cached name (via `ItemRefSnapshot`).
- Home "Agents you used most" panel orders agents by `cost_usd_micro desc` over the past 7 days.
- Inbox row with `sender.kind = "agent"` resolves the agent name via `<ItemLink>` registry; click navigates to `/agents/<id>`.

### 13.4 End-to-end smoke (added to `docs/dev-testing.md`)

```bash
# 1. List the seeded system catalog.
curl -H "Authorization: Bearer $TOKEN" "$BASE/v1/agents?filter[origin]=system"

# 2. Install the Calendar Whisperer.
curl -X POST -H "Authorization: Bearer $TOKEN" "$BASE/v1/agents/<id>/install"

# 3. Snapshot a version.
curl -X POST -H "Authorization: Bearer $TOKEN" -d '{"label":"baseline"}' \
  "$BASE/v1/agents/<id>/versions"

# 4. Customize → duplicate.
curl -X POST -H "Authorization: Bearer $TOKEN" "$BASE/v1/agents/<id>/duplicate"

# 5. Read usage.
curl -H "Authorization: Bearer $TOKEN" "$BASE/v1/agents/<id>/usage?period=week"

# 6. Disable.
curl -X PATCH -H "Authorization: Bearer $TOKEN" -d '{"disabled":true}' \
  "$BASE/v1/agent_installs/<install_id>"
```

---

## §14 Implementation phasing

Per [implementation-plan.md](../implementation-plan.md) §2 Phase 8 row, dispatched as 5–9 narrow agents (per the user-memory subagent-sizing rule). The boundary is files; no two agents write the same path.

### 14.1 Agent boundaries (no overlap on shared files)

| Agent     | Scope                                                                                                                                                                                                                                                                          | Estimated wall | Branch                                    |
| --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------- | ----------------------------------------- |
| **P8-A1** | `services/backend/src/backend_app/agents/`: schema migrations 0001 (agents) + 0002 (agent_installs), `routes.py`, `service.py`, `store.py`, `acl.py`, ACL tests. Adds `Project.default_agent_id` migration on Projects schema (cross-cut). Facade proxy at `agents_routes.py`. | 30 min         | `worktree-agent-phase8-A1-backend-crud`   |
| **P8-A2** | `services/backend/src/backend_app/agents/versions.py`: schema migration 0003 (agent_versions), `POST/GET versions` routes, snapshot atomicity tests. ai-backend `runtime_run_meta.agent_version_id` column migration `0006`.                                                   | 25 min         | `worktree-agent-phase8-A2-versions`       |
| **P8-A3** | `services/backend/src/backend_app/agents/installs.py`: install / uninstall / disable routes, tenant-scope install materialization, override merge tests, cascade tests for Routines + Projects.                                                                                | 30 min         | `worktree-agent-phase8-A3-installs`       |
| **P8-A4** | `services/ai-backend/src/runtime_api/http/usage_routes.py`: per-agent usage aggregation route, facade pass-through, 60s cache, tenant-isolation tests.                                                                                                                         | 20 min         | `worktree-agent-phase8-A4-usage`          |
| **P8-A5** | `packages/api-types/src/agents.ts` + `brands.ts` deltas (`AgentId`, `AgentVersionId`, `AgentInstallId`, `MemoryRef`). Re-exports from `index.ts`. Typecheck-only PR — no logic.                                                                                                | 15 min         | `worktree-agent-phase8-A5-types`          |
| **P8-B1** | `packages/chat-surface/src/destinations/agents/AgentsDestination.tsx` (gallery), `AgentsPanel.tsx`, `AgentCard.tsx`, `<AgentPicker>` (shared), filter chips, search bar. Throw away the Wave-0 debug table.                                                                    | 40 min         | `worktree-agent-phase8-B1-gallery`        |
| **P8-B2** | `packages/chat-surface/src/destinations/agents/AgentDetail.tsx`, `AgentEditor.tsx`, version-history panel, fork-confirmation dialog. Reuses `<EmojiPicker>` from chats-canvas.                                                                                                 | 40 min         | `worktree-agent-phase8-B2-detail-editor`  |
| **P8-B3** | `packages/chat-surface/src/destinations/agents/usage/UsageChart.tsx`, `UsageSparkline.tsx`, the per-card `<UsageChip>`, hover popover. Read-only consumer of `GET /v1/agents/<id>/usage`.                                                                                      | 25 min         | `worktree-agent-phase8-B3-usage-chart`    |
| **P8-C**  | `apps/frontend/src/app/App.tsx` route wiring, slug registration in `ShellDestinationSlug` (already present from Wave 0 — keep), `<ItemLink kind="agent">` registry registration in `destinations/agents/index.ts`, composer agent-picker integration.                          | 25 min         | `worktree-agent-phase8-C-frontend-wiring` |

Total estimated wall: 4–5 hours of narrow-agent work. Merge order strict (see 14.2).

### 14.2 Merge order

1. **P8-A5** (types) — opens every other agent's TypeScript surface.
2. **P8-A1 / P8-A2 / P8-A3 / P8-A4** (backend) — can land in parallel (different files), merge any-order once green.
3. **P8-B1 / P8-B2 / P8-B3** (frontend) — can land in parallel.
4. **P8-C** (wiring) — final integrator. Lands after B-set + closes Phase 8.

Each agent runs in its own `.claude/worktrees/<id>/` with branch `worktree-agent-phase8-<role>` (per user-memory worktree-discipline). The orchestrator merges; orchestrator cleans worktrees per the cleanup-discipline rule.

### 14.3 Acceptance criteria (gate to closing Phase 8)

- All §13 tests green (backend + frontend + cross-destination).
- Browser-verified: gallery loads; install round-trips; editor saves; version pin works in the Routines editor; Project default-agent picker works.
- `npm run typecheck` clean across `@enterprise-search/api-types` + `@enterprise-search/chat-surface` + `@enterprise-search/frontend`.
- `make test` smoke green.
- The Wave-0 debug-table `/v1/agent/runs` UI is fully removed from `AgentsDestination.tsx`. Runs observability lives in Home (already shipped).
- Master PRD §5.6 open questions Q1–Q3 are resolved (recorded in cross-audit §9 by the orchestrator post-merge).
- The 9 §11 open questions are resolved or explicitly deferred with Wave numbers.
- An entry is added to the `destinations-master-prd.md` §15 deferred-features appendix (per cross-audit §3.5) for: community-submission UI (Wave 6+), per-agent cost guards (Wave 6), tenant-policy install (Wave 6), agent A2A graphical editor (never), memory-tab activation (Phase 11).

---

## §15 Anti-goals (restated as testable invariants)

- **No debug table.** The destination DOES NOT show paginated `runs` rows. The acceptance test asserts `data-testid="agents-table"` (the Wave-0 stub's role=table) does NOT exist in the Phase 8 build.
- **No parallel token tracker.** The `services/backend/` codebase grows ZERO new tables that store token counts / cost. CI guard (`tools/check_llm_provider_imports.py`) keeps the single-tracker invariant; a separate test asserts `git grep -l "llm_token_usage\|agent_token_usage" services/backend/src/` returns empty.
- **No LLM call in a list view.** The gallery, detail, version list, and usage chart make **zero** LLM calls. Only the user-initiated "Generate from description" affordance in the editor would — and that is explicitly DEFERRED to Wave 6 per §7.4.
- **No cross-tenant existence leak.** The 404-not-403 rule is tested for every read-style endpoint.
- **No silent dead-link cascade.** When an agent is deleted, every consumer (Chats, Routines, Projects, Home, Inbox, Todos) renders a `<deleted agent>` chip with the cached name via `<ItemRefSnapshot>` — never a blank cell, never a 500 error.
- **No shell re-render.** Navigating into/out of `/agents` does NOT re-mount the workspace shell. Render-count test on the shell asserts ≤ 1 mount across the destination journey.
- **No bare strings.** Every user-visible text in the destination is wrapped in `t()` (master §3.9). Lint check.
- **No premature abstraction.** `<AgentCard>` does NOT generalize to "any catalog card"; if Tools (Phase 9) wants a similar card, it forks the shape (DRY-via-design, not DRY-via-abstraction-too-early).

---

## §16 References

- [destinations-master-prd.md §5.6](../destinations-master-prd.md) — original Agents master section.
- [cross-audit.md §1.1, §1.3, §1.5, §2.1, §3.3, §5.3, §5.5, §9.7 Q11](../cross-audit.md).
- [destinations/routines-prd.md §1.4, §9.7 Q11, §16](routines-prd.md) — the version-pin contract this PRD ships the storage for.
- [destinations/projects-prd.md §12 Q3](projects-prd.md) — the override-pattern this PRD reuses for `default_agent_id`.
- [destinations/chats-canvas-prd.md](chats-canvas-prd.md) — the composer surface that consumes `<AgentPicker>`.
- [destinations/home-prd.md §5.1, §5.5](home-prd.md) — the "Agents you used most" panel + recent runs cross-link.
- `services/ai-backend/src/agent_runtime/execution/deep_agent_builder.py` — the runtime call site that resolves the agent at run construction.
- `services/ai-backend/src/agent_runtime/observability/attribution.py` — the `Purpose` enum that Agents reuses unchanged.
- `services/ai-backend/src/agent_runtime/observability/usage_recorder.py` — the single-tracker boundary.
- Wave-0 `packages/chat-surface/src/destinations/agents/AgentsDestination.tsx` — the debug table this PRD replaces.

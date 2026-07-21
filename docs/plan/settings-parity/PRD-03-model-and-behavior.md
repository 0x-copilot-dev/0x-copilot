# PRD-03 — Model & Behavior

## 1. Header

- **Title:** Model & behavior settings — converge web onto the chat-surface SSOT and wire the runtime to obey it
- **One-line summary:** Make the "Model & behavior" section render the same chat-surface `ModelBehaviorPage` on web and desktop, and make each of its five knobs actually change how a run executes — not just where a value is stored.
- **Status:** Draft
- **Nav slug:** `model-behavior` (web legacy slug `model-and-behavior`)
- **Related files:**
  - SSOT page: `packages/chat-surface/src/settings/ModelBehaviorPage.tsx`, `packages/chat-surface/src/settings/ApprovalPolicy.tsx`
  - Web host binder: `apps/frontend/src/features/settings/SettingsBinder.tsx`; legacy body `apps/frontend/src/features/settings/sections/ModelAndBehavior.tsx`; live policy panel `apps/frontend/src/features/settings/sections/ToolUsePolicyPanel.tsx`
  - Desktop host binder: `apps/desktop/renderer/SettingsMount.tsx`
  - Default-model persistence: `services/ai-backend/src/runtime_api/schemas/workspace_defaults.py`, `services/ai-backend/src/agent_runtime/execution/models.py`, `.../api/model_catalog.py`, `.../api/models_dev_source.py`
  - Reasoning depth: `services/ai-backend/src/agent_runtime/execution/depth.py`; `packages/api-types/src/agents.ts` (`AgentReasoningDepth`), `packages/api-types/src/index.ts` (`ReasoningDepth`, `ReasoningEffort`, `default_reasoning_effort`)
  - Approval policy: backend routes `services/backend/src/backend_app/routes/tool_use_policies.py` + store `.../policies/store.py`; runtime gate `services/ai-backend/src/agent_runtime/capabilities/tools/runtime_gate.py`; snapshot `.../capabilities/tools/permissions.py`; run context `.../execution/contracts.py` (`user_policies_json`); resolver `.../api/user_policies_resolver.py`; consumption precedent `.../execution/provider_kwargs.py`; interrupt seam `.../execution/factory.py` (`_native_interrupt_config`); facade proxies `services/backend-facade/src/backend_facade/me_routes.py`
  - Spend: `services/ai-backend/src/agent_runtime/budgets/enforcer.py`, `.../runtime_api/schemas/budgets.py`, `.../runtime_worker/handlers/run.py` (`_preflight_budgets`); facade `services/backend-facade/src/backend_facade/app.py` (`/v1/budgets*`)

---

## 2. Problem statement

**User pain.** The "Model & behavior" section is where a solo BYOK operator is supposed to set the one thing that matters most: _how much autonomy the agent has, on what model, at what cost._ Today that promise is broken two ways. First, the section looks and behaves differently depending on whether you open it on web or on desktop — same nav slug, two entirely different screens. Second, and worse, several controls that _appear_ to work do nothing: you can flip "Web access", pick a reasoning depth, or set an approval policy on desktop and the run behaves identically, because the value is held in React state and never reaches the runtime.

**Engineering reality.** The branch name — `composer-web-desktop-mismatch` — is literal here.

- **Web** renders the legacy `sections/ModelAndBehavior.tsx`: five _workspace_ `behavior_overrides` knobs (system prompt, temperature, citation density, refusal behavior, `default_reasoning_effort`) plus a genuinely-wired `ToolUsePolicyPanel`. It has **no** default-model select, **no** reasoning-depth (Auto/Quick/Standard/Deep) picker, **no** web-access toggle, **no** spend cap. It is not the design at all.
- **Desktop** mounts the chat-surface `ModelBehaviorPage.tsx` — the actual v3 design, all five blocks — but only the **default model** persists. Reasoning depth, web access, approval policy, and spend are `useState(DEFAULT_MODEL_BEHAVIOR)` that never leave the renderer (`apps/desktop/renderer/SettingsMount.tsx:132`, `:715`).

Underneath, the five knobs sit at five different maturities (§5). The central, load-bearing risk this PRD exists to retire is: **"the setting exists" ≠ "the runtime obeys it."** For three of the five knobs the value can be stored and displayed while the execution path never reads it — the approval policy is stored and surfaced but its gate (`ToolUsePolicyGate.decide()`) has **zero callers**; the persisted `default_reasoning_effort` is stored-but-unconsumed; web-access has no backend at all. This is the most dangerous failure mode in a permissions surface: a user believes they blocked write actions, and the agent writes anyway.

The program: (1) converge web onto the chat-surface `ModelBehaviorPage` and retire `sections/ModelAndBehavior.tsx`; (2) wire the real backends through **both** host binders so web === desktop; (3) close the genuine backend gaps so each knob provably changes a run.

---

## 3. Goals & non-goals

**Goals**

1. Web and desktop mount the **same** `ModelBehaviorPage` (and its `ApprovalPolicy` child); the legacy web section is retired.
2. Every one of the five knobs round-trips to a real persistence home through the facade, identically on both hosts.
3. Every one of the five knobs provably changes a run:
   - default model → resolved at run-create (already true; extend web);
   - reasoning depth → scales the depth budget at run-create;
   - web access → gates whether web-reaching tools are exposed to the model;
   - approval policy → gates tool dispatch through the existing approval interrupt / `RUN_REJECTED`;
   - spend cap → the existing budget preflight denies/warns at run-start.
4. Resolve the three-way reasoning-depth vocabulary drift onto one canonical enforced enum with an explicit UI mapping.
5. Close the two named security gaps: the missing `admin:budgets` scope check on org-scoped budget writes, and the read=`block` / destructive=`auto` states the store accepts but the UI can never show.

**Non-goals**

1. **Per-connector tool scoping.** _Which_ tools each approval axis covers is chosen per-connector on the Connectors page and is already enforced live (`ToolPermissionChecker.has_scopes_for_connector`). Out of scope; the settings surface carries only the design's copy pointing at it.
2. The legacy `behavior_overrides` knobs that are **not** in the v3 design (system prompt override, temperature, citation density, refusal behavior). They remain reachable through their existing workspace-defaults contract but are **not** surfaced on the new page (see Open Decision OD-6). This PRD does not delete their storage.
3. Team-admin org-wide _policy_ defaults as a distinct editable surface (the workspace-scope `/v1/workspace/policies/tool-use` write path). Solo/self scope only; org default editing is deferred (OD-3).
4. Billing/usage analytics beyond the single monthly cap + pause toggle.

---

## 4. Users & scenarios

**Personas**

- **Solo-desktop BYOK operator (primary).** One person, one machine, `single_user_desktop` profile. Their org == their user. They pay for model calls directly and care intensely about autonomy and spend. Every knob here is _personal_.
- **Team admin (secondary, web).** `team` profile. Sets workspace defaults and per-user overrides. Relevant only where org-vs-user scope and admin authorization diverge from the solo case.

**Scenarios**

1. **"Read-only until I trust it."** A new solo user sets Write actions → _Require approval_ and On-chain/spend/destructive → _Block_. On the next run, when the model tries to call a write tool, the run pauses for approval; when it tries a destructive tool, the tool is never offered and, if forced, the run is rejected with a safe message. The same edit made on web behaves identically.
2. **"Cap my burn."** The user sets Monthly API cap → `$50` and Pause runs at cap → on. Mid-month, a run whose estimate would cross $50 is denied _before_ the LLM call with a budget-exceeded event; under soft mode it would instead warn and proceed.
3. **"No web this run… actually, never."** The user turns Web access off in Settings. On subsequent runs the fetch/search tools are absent from the model's toolset. (Per-run re-enable stays in the composer.)
4. **"Think harder by default."** The user sets Reasoning depth → _Deep_. New runs that don't specify a depth in the composer inherit `deep`, and `DepthBudgetTable` scales timeout/tokens/tool-budget 2×.

---

## 5. Current state — maturity matrix

Each knob at a different maturity. "Surface" = is there a UI that reads/writes a real store. "Runtime obeys" = does the execution/worker path consume it.

| #   | Knob                | Surface (web)                                                            | Surface (desktop)                                                                                                 | Persisted?                                                                                                                               | Runtime obeys?                                                            | Evidence                                                                                                                                                                                                                                          |
| --- | ------------------- | ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | **Default model**   | ❌ absent (legacy section has no model select)                           | ✅ live via `persistDefaultModel` → PUT `/v1/agent/workspace/defaults`                                            | ✅ `workspace_defaults.default_model` (ORG-scoped)                                                                                       | ✅ resolved at run-create                                                 | `apps/desktop/renderer/SettingsMount.tsx:470`; `services/ai-backend/src/agent_runtime/execution/models.py`; `.../api/model_catalog.py` (PR #171 filter); web has none — `sections/ModelAndBehavior.tsx`                                           |
| 2   | **Reasoning depth** | ⚠️ `default_reasoning_effort` (low/med/high) pill, persisted             | ⚠️ Auto/Quick/Standard/Deep select, **local-only**                                                                | ⚠️ split: per-run `fast\|balanced\|deep` wired end-to-end; workspace `default_reasoning_effort` (low/med/high) **stored-but-unconsumed** | ⚠️ per-run yes; default no                                                | `execution/depth.py` (`fast\|balanced\|deep`); `api-types` `ReasoningDepth`/`ReasoningEffort`; `ModelBehaviorPage.tsx:48` (`auto\|quick\|standard\|deep`); `workspace_defaults.py:163`                                                            |
| 3   | **Web access**      | ❌ absent                                                                | ⚠️ `webAccess:boolean` local state only                                                                           | ❌ no field anywhere                                                                                                                     | ❌ no gating; no "web" capability class                                   | `ModelBehaviorPage.tsx:368`; side-effects are `read/write/delete/external_call` only (`capabilities/tools/permissions.py:127`)                                                                                                                    |
| 4   | **Approval policy** | ✅ `ToolUsePolicyPanel` reads/writes `/v1/me/policies/tool-use` **live** | ⚠️ `ApprovalPolicy` **local-only**, never calls storage; seeded `write:require` diverges from backend `write:ask` | ✅ `tool_use_policies` (per-org + per-user; mig 0021)                                                                                    | ❌ **gate has zero callers**; `user_policies_json['tool_use']` never read | Store + routes `backend_app/routes/tool_use_policies.py`; gate `runtime_gate.py` + `permissions.py` — grep confirms only its own def + one test import it; `provider_kwargs.py` reads only `['privacy']`; desktop default `SettingsMount.tsx:136` |
| 5   | **Spend guardrail** | ❌ no config (read-only usage chart only)                                | ⚠️ spend block local-only                                                                                         | ✅ `usage_budgets` (per-org/user; mig 0009)                                                                                              | ✅ `BudgetEnforcer.preflight` denies/warns before LLM call                | `budgets/enforcer.py`; `run.py:_preflight_budgets`; facade `/v1/budgets*` `app.py:1265`; **no** `admin:budgets` check (`app.py:1260`), `/v1/budgets/me` open to any authed user                                                                   |

**Cross-cutting facts that shape the fix**

- `AgentRuntimeContext.user_policies_json` (`execution/contracts.py:347`) is a `JsonObject` populated by `UserPoliciesResolver` (`run.py:52`). Only its `['privacy']` sub-object is consumed today (`provider_kwargs.py:183`). The `['tool_use']` sub-object is the un-wired lane.
- The approval interrupt already exists: the deep-agents builder installs `interrupt_on` per tool (`execution/factory.py:282`, `_native_interrupt_config`), and a `mode="interrupt"` filesystem rule already pauses host writes for human approval. This is the seam ask/require must route through — the machinery is built, just not fed by the tool-use policy.
- `ToolUsePolicySnapshot.mode_for_kind` already falls back to deployment defaults (`read=auto, write=ask, destructive=require`) when a cell is absent (`permissions.py:80`), so a fail-open posture is already available.
- The design's per-axis mode restriction (read-only can't be `block`; destructive is only `require`/`block`) is a **frontend-only** constraint in `ApprovalPolicy.tsx`. The backend store and PUT validator accept all four modes on every axis (`tool_use_policies.py` validators only check enum membership, not per-axis legality).

---

## 6. Design & UX specification

The target is `packages/chat-surface/src/settings/ModelBehaviorPage.tsx` rendered **unchanged in structure** on both hosts. The page is already built to the v3 "quiet" spec; the work is _wiring_, plus small copy/parity fixes below. All colors/sizes resolve to the existing authored `--color-*` / `--font-*` / `--radius-*` / `--space-*` tokens (the page already does this; no hard-coded hex/px). Token-name parity (terse `--ink`/`--sky`/`--r` aliases) is a cross-cutting program decision — **this PRD keeps the existing `--color-*`/`--font-*`/`--radius-*` names** and introduces no new hex or px font-size in section code.

### 6.1 Section layout (one page, three cards)

Header — title **"Model & behavior"**, desc **"How the agent thinks and how far it can go on its own."** (The page today renders the meta _"The default model and how the agent reasons and reaches the web."_ — **parity delta D-1:** align the header copy to the design's section description above, or accept the current per-card meta; recommend adopting the design's exact section desc at the surface header, keeping per-card metas.)

**Card "Defaults"**

- **Default model** — `Select`, two optgroups: **"Cloud · your keys"** (from connected BYOK providers+models) and **"Local · your machine"** (installed local models). Hint (design): _"Used for new runs. Switch any run in the composer."_ Empty state when no providers/models: disabled select, hint _"Add a provider key or download a local model to pick a default."_ (already implemented).
- **Reasoning depth** — `Select` **Auto / Quick / Standard / Deep**. Hint (design): _"Deeper plans more before acting."_
- **Web access** — `Toggle`. Hint (design): _"Let the agent fetch URLs and search."_

**Card "Approval policy"** (`ApprovalPolicy.tsx`) — note copy (design): _"The one tool control that belongs in Settings. Which tools the agent may use is set per-connector on the Connectors page →"_ (the page currently shows the shorter `APPROVAL_POLICY_CONNECTOR_NOTE`; **parity delta D-2:** the design copy names the Connectors page as a link — render the note with a link/affordance to the Connectors destination).

- **Read-only actions** — Auto-approve / Ask first. Hint: _"Search, list, view. No changes."_
- **Write actions** — Require approval / Ask first / Auto-approve / Block. Hint: _"Post, message, edit records."_
- **On-chain, spend & destructive** — Require approval / Block. Hint: _"Sign transactions, move funds, delete."_

**Card "Spend guardrail"**

- **Monthly API cap** — `$` prefix + numeric input. Hint: _"Across all your provider keys."_
- **Pause runs at cap** — `Toggle`. Hint: _"Stop and ask before exceeding it."_

### 6.2 Save model

The whole page is a **dirty SaveBar** section (multi-field), not one-shot toasts — the page already docks a SaveBar through `controller.setDirty(...)`. The host owns the saved baseline + dirty computation. **Parity delta D-3:** desktop's binder passes `onChange` but never `dirty/onSave/onDiscard/saving/loading/error`, so no SaveBar ever appears and default-model saves eagerly via a toast instead. Both binders must supply the full controlled contract so Save/Discard behave identically. A single Save commits _all_ dirty knobs (default model, reasoning depth, web access, approval policy, spend) in one gesture; partial-store failures surface inline via `saveError` (role="alert") without discarding the other successful writes (see FR-24).

### 6.3 Empty / loading / error states (already scaffolded, must be wired)

- **Loading:** `loading` → quiet skeleton "Loading settings…" (`ModelBehaviorPage` renders it). The host sets `loading=true` until the initial GET fan-out (workspace defaults + provider keys + `/v1/me/policies/tool-use` + `/v1/budgets/me` + `/v1/me/preferences`) resolves.
- **Load error:** `error` → role="alert" with **Retry** (re-runs the GET fan-out).
- **Save error:** `saveError` → role="alert" above the first card; message is store-specific and safe (never leaks provider/internal detail).
- **Default-model empty:** disabled select + guidance hint (already present).

### 6.4 Enumerated parity deltas to fix

| ID  | Delta                                                                                      | Fix                                                                                                       |
| --- | ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------- |
| D-1 | Header/section copy differs from design desc                                               | Adopt design section desc _"How the agent thinks and how far it can go on its own."_                      |
| D-2 | Connector note lacks the "Connectors page →" link                                          | Render note with a link affordance to the Connectors destination (host-injected navigate)                 |
| D-3 | Desktop never wires SaveBar / dirty; saves eagerly                                         | Both binders supply `dirty/onSave/onDiscard/saving/loading/error/saveError/onRetry`                       |
| D-4 | **Web renders a completely different legacy screen**                                       | Route `model-behavior` to `ModelBehaviorPage` in `SettingsBinder`; retire `sections/ModelAndBehavior.tsx` |
| D-5 | Desktop approval default `write:require` ≠ backend default `write:ask`                     | Seed approval policy from the backend GET, not a hardcoded literal (`SettingsMount.tsx:136`)              |
| D-6 | Desktop reasoning depth / web access / approval / spend are local-only                     | Wire each to its store through the binder                                                                 |
| D-7 | Reasoning UI has 4 options; enforced enum has 3; a 3rd vocab (`low/med/high`) is persisted | Canonicalize (§7 FR-9..12)                                                                                |
| D-8 | Web `ToolUsePolicyPanel` and desktop `ApprovalPolicy` are different components/markup      | Both hosts render `ApprovalPolicy`; web retires `ToolUsePolicyPanel` from this section                    |

### 6.5 Accessibility & microcopy

- Every field label is programmatically associated (`htmlFor`, `aria-label` — already present). Approval axes are `role="radiogroup"`/`role="radio"` with `aria-checked` (`SegmentedControl`, already present).
- SaveBar: focus moves to the SaveBar's Save on first becoming dirty is **not** forced (avoid focus-stealing mid-edit); the SaveBar is reachable in tab order and announced as a region.
- Error alerts use `role="alert"`; Retry is a real `<button>`.
- **Reduce-motion:** any SaveBar dock animation respects `prefers-reduced-motion` (design-system controls already do; no new animation in section code).
- Microcopy for enforcement outcomes must be **honest**: on desktop today `persistDefaultModel` shows _"Runs use the composer's model pick; the saved default is unchanged."_ when `defaultModel=null` — keep this honesty discipline for every new save path (never toast "saved" when nothing persisted).

---

## 7. Functional requirements

Grouped by knob. Each FR names the layer(s) it touches: **[cs]** chat-surface page/port, **[web]** `SettingsBinder`, **[desk]** `SettingsMount`, **[facade]**, **[be]** `backend`, **[ai]** `ai-backend`, **[types]** `api-types`.

### Convergence (the spine)

- **FR-1 [web]** In `SettingsBinder.tsx`, the `model-behavior` case renders the chat-surface `ModelBehaviorPage` (not `<ModelAndBehavior>`). The binder supplies `value`, `onChange`, `cloudModels`, `localModels`, `controller`, and the full dirty/save/loading/error contract.
- **FR-2 [web]** `sections/ModelAndBehavior.tsx` and its use of `ToolUsePolicyPanel` are removed from the `model-behavior` route. The four non-design `behavior_overrides` knobs it hosted are relocated per OD-6 (recommended: dropped from the settings surface, storage retained). `ToolUsePolicyPanel.tsx` is deleted or left unreferenced (dead-code sweep in the same PR).
- **FR-3 [cs]** `ModelBehaviorPage` remains the single component both hosts mount; no host forks it. Any prop the two binders need is added to the page's props, not duplicated per host (SSOT lockstep, NFR-2).

### Knob 1 — Default model

- **FR-4 [web]** Web builds `cloudModels`/`localModels` from the same curated, key-gated catalog the composer uses, and persists a selection via read-merge-PUT `/v1/agent/workspace/defaults` — matching desktop's `persistDefaultModel` semantics (full-document replace). The pure catalog→option projection is duplicated (not shared) across the two binders, over `api-types` shapes (boundary NFR-2).
- **FR-5 [desk]** Desktop's existing default-model wiring is unchanged except it now flows through the unified SaveBar (FR-1/D-3) rather than an eager toast, so a model change is _staged_ and committed with the rest of the page. (Retain the honest "no default → composer pick wins" messaging.)
- **FR-6 [ai]** No runtime change: `ModelConfigResolver` (`execution/models.py`) continues to resolve `workspace_defaults.default_model` at run-create.

### Knob 2 — Reasoning depth (vocabulary resolution)

- **FR-7 [types]** Canonical wire enum is **`fast | balanced | deep`** (`AgentReasoningDepth`, already the only enforced enum). A new persisted field `default_reasoning_depth: AgentReasoningDepth | null` is added; `null` means "Auto" (no explicit depth → runtime baseline).
- **FR-8 [cs]** The page's UI vocabulary **Auto / Quick / Standard / Deep** maps to the wire by a single documented table:

  | UI (`ReasoningDepth` in `ModelBehaviorPage`) | Wire (`AgentReasoningDepth`)                 |
  | -------------------------------------------- | -------------------------------------------- |
  | `auto`                                       | `null` (omit → resolver baseline `balanced`) |
  | `quick`                                      | `fast`                                       |
  | `standard`                                   | `balanced`                                   |
  | `deep`                                       | `deep`                                       |

  The mapping lives in the host binder (the page stays presentational and keeps its 4-value UI union). The composer's per-run picker stays `fast/balanced/deep`.

- **FR-9 [web]/[desk]** Both binders persist the mapped value to the chosen per-user home (OD-3 recommended: `/v1/me/preferences` key `agent.default_reasoning_depth`) and hydrate the UI value back through the inverse mapping.
- **FR-10 [ai]** At run-create, when the request omits `reasoning_depth`, the resolver falls back to the user's `default_reasoning_depth` (mapped, `auto`→ omit) **before** `DepthBudgetTable.apply`. A run that specifies a depth in the composer always wins.
- **FR-11 [be/ai]** The stored-but-unconsumed `behavior_overrides.default_reasoning_effort` (`low|medium|high`) is **deprecated**: stop writing it from this surface; the runtime ignores it. Leave the column for back-compat reads (no destructive migration). Document the deprecation in `workspace_defaults.py`.
- **FR-12 [cs]** `REASONING_DEPTHS` (Auto/Quick/Standard/Deep) is the SSOT for the picker; no host re-declares the option list.

### Knob 3 — Web access (real tool gate)

- **FR-13 [ai]** Introduce a **capability classification for web reach** that does not overload the side-effect taxonomy: add a boolean `reaches_public_web` to the tool card/spec (`capabilities/tools/cards.py`), set `true` on the built-in fetch/search/URL tools and honored on MCP tools that declare it. (Chosen over a new `ToolSideEffect.WEB` member to avoid re-classifying every existing tool — see OD-4.)
- **FR-14 [ai]** Add `web_access: bool` to `AgentRuntimeContext` (`execution/contracts.py`), resolved at run-start from the user's `web_access_default` preference, overridable per-run by the composer.
- **FR-15 [ai]** At tool-load time, when `context.web_access is False`, tools with `reaches_public_web=True` are **not exposed to the model** (filtered in the same place `ToolPermissionChecker` filters unauthorized cards). The model never sees a tool it may not use; no per-dispatch rejection needed for the common path. If a web tool is somehow dispatched while disabled, it is rejected with a safe message.
- **FR-16 [web]/[desk]/[types]** Add `web_access_default: bool` to the per-user preference home (OD-3: `/v1/me/preferences` key `agent.web_access_default`); both binders persist the toggle and hydrate it. Default value per OD-5.

### Knob 4 — Approval policy (surface parity + runtime enforcement)

- **FR-17 [desk]** Desktop's `ApprovalPolicy` is bound to the real per-user store: GET `/v1/me/policies/tool-use` on mount (seed the three axes), PUT `/v1/me/policies/tool-use` on save. The hardcoded `DEFAULT_MODEL_BEHAVIOR.approvalPolicy` (`write:require`) is removed; until the GET resolves the page is in `loading`. This makes desktop === web (which already does this via `ToolUsePolicyPanel`).
- **FR-18 [web]/[desk]** Both binders map the 3-axis UI (`readOnly/write/danger`) to the wire axes (`read/write/destructive`) and back, with the per-axis mode subsets:

  | UI axis    | Wire axis     | UI modes                  | Wire modes                |
  | ---------- | ------------- | ------------------------- | ------------------------- |
  | `readOnly` | `read`        | auto, ask                 | auto, ask                 |
  | `write`    | `write`       | require, ask, auto, block | require, ask, auto, block |
  | `danger`   | `destructive` | require, block            | require, block            |

- **FR-19 [ai]** `UserPoliciesResolver` (`.../api/user_policies_resolver.py`) additionally fetches the tool-use policy (`/internal/v1/policies/tool-use`, workspace + user scopes) at run-start and writes it under `AgentRuntimeContext.user_policies_json['tool_use'] = { "workspace": {kind:mode}, "user": {kind:mode} }` — mirroring how `['privacy']` is populated.
- **FR-20 [ai]** At run-start the worker builds a `ToolUsePolicySnapshot` via `ToolUsePolicySnapshot.from_response(workspace=…, user=…)` from `user_policies_json['tool_use']`, and for each loaded tool spec calls `ToolUsePolicyGate.decide(snapshot=…, spec=…)`. Translation of the decision:
  - `ALLOW` → tool loaded, no interrupt.
  - `REQUIRE_APPROVAL` with `one_time=True` (**ask**) → install a HITL `interrupt_on` rule for that tool that fires **once** per `(run, tool_name)`.
  - `REQUIRE_APPROVAL` with `one_time=False` (**require**) → `interrupt_on` rule that fires on **every** dispatch.
  - `REJECT` (**block**) → the tool is **not loaded** (dropped from `model_tools`); a blocked-class tool that is nonetheless invoked yields `RUN_REJECTED` with the gate's `safe_message`.
- **FR-21 [ai]** The ask/require interrupts route through the **existing** deep-agents `interrupt_on` seam (`execution/factory.py` `_native_interrupt_config`), merged with the current host-write interrupt rule — no new approval mechanism is built. Approval decisions continue to be handled by `runtime_worker/handlers/approval.py`.
- **FR-22 [ai]** **Fail-open to deployment defaults.** When `user_policies_json['tool_use']` is absent/empty/unreachable, the snapshot resolves each axis to `_DEFAULT_MODES` (`read=auto, write=ask, destructive=require`). A run is never rejected _solely_ because the policy lane is unconfigured. `block` only ever comes from an explicitly stored `block`.
- **FR-23 [ai]** The gate's `policy_fired` axis is recorded on the existing `tool_call_outcome` audit row's metadata (as the gate's docstring anticipates), so SIEM can pivot on which axis fired without a new audit action.

### Knob 5 — Spend guardrail (config surface)

- **FR-24 [web]/[desk]** The spend block binds to `/v1/budgets`:
  - **Hydrate:** GET `/v1/budgets/me`; select the caller's **user-scoped MONTHLY** budget; seed `monthlyCapUsd = limit_micro_usd / 1_000_000` and `pauseAtCap = (enforcement === "hard")`. Optionally surface remaining headroom (`remaining_micro_usd`) beneath the cap field.
  - **Save (cap set, no budget yet):** POST `/v1/budgets` `{ scope: "user", user_id: <self>, period: "MONTH", enforcement: pauseAtCap ? "hard" : "soft", limit_micro_usd: round(cap * 1_000_000) }`.
  - **Save (cap set, budget exists):** PATCH `/v1/budgets/{id}` with the new `limit_micro_usd`/`enforcement`.
  - **Save (cap cleared → null):** DELETE `/v1/budgets/{id}` (or PATCH `status: "paused"` per OD-2).
- **FR-25 [cs]** No page change: `monthlyCapUsd: number | null` and `pauseAtCap: boolean` already exist on `SpendGuardrailValue`; `parseCap` already clamps negatives to 0 and treats blank as "no cap".
- **FR-26 [ai]** No runtime change to enforcement: `BudgetEnforcer.preflight` already denies (hard) / warns (soft) before the LLM call and emits `budget_warning` / `budget_exceeded` stream events (`run.py:_preflight_budgets`). This PRD only adds the config surface.
- **FR-27 [facade/be]** **Security fix:** org-scoped budget writes (`POST /v1/budgets` with `scope: "org"`, and PATCH/DELETE of an org-scoped budget) require the `admin:budgets` scope (the deferred "A10"). User-scoped self writes (`scope: "user"`, `user_id == caller`) are allowed under the caller's own identity. `/v1/budgets/me` remains readable by the authed user for **their own** budgets only. See NFR-1.

---

## 8. Non-functional requirements

- **NFR-1 — Security (spend authorization).** Close the A10 gap: no non-admin caller may create/modify an **org-scoped** budget; a caller may only read/modify their **own** user-scoped budget. Enforced server-side at the facade→backend boundary, not in the UI. The solo path (self user-scoped budget) needs no admin scope. Caller identity/scope is derived from the verified session, never from request body fields.
- **NFR-2 — Architectural boundaries / SSOT lockstep.** `apps/frontend` and `apps/desktop` never import each other; the two binders duplicate only pure projection logic over `api-types` shapes. `chat-surface` stays substrate-agnostic: no `window`/`document`/`fetch`/`localStorage`/`apps-*` imports in `ModelBehaviorPage`/`ApprovalPolicy`; all fetching lives in the binders. Any change to the page's props updates **both** binders in the same PR (a CI parity test asserts both binders satisfy the page's prop contract — NFR-6).
- **NFR-3 — "Stored ≠ obeyed" invariant.** No knob may ship a persistence path without a runtime-consumption path or an explicit, tested "not yet enforced" acknowledgement surfaced to the user. Specifically forbidden: a UI that reports "saved" for reasoning depth / web access / approval policy while the runtime ignores the value.
- **NFR-4 — Graceful degradation posture (explicit per knob).**
  - Approval policy → **fail-open** to deployment defaults (`read=auto, write=ask, destructive=require`) when the policy lane is unconfigured/unreachable (FR-22). `block` is never synthesized.
  - Web access → **fail-open** (web enabled) when the preference lane is unreachable _and unset_; an explicitly-stored `false` is always honored.
  - Spend → **fail-safe on enforcement is the backend's** (hard cap denies). If `/v1/budgets/me` is unreachable at config time, the surface shows a load error + Retry rather than a fabricated cap.
- **NFR-5 — Token & i18n discipline.** No hard-coded hex or px font-size in section code; resolve to `--color-*`/`--font-*`/`--radius-*`/`--space-*` (already satisfied). All user-facing strings are literals ready for extraction; no string is assembled from runtime data in a way that blocks translation.
- **NFR-6 — Cross-host equivalence tests.** A test renders `ModelBehaviorPage` under both a web-shaped and desktop-shaped host stub and asserts identical structure/behavior for each knob (radiogroup axes, SaveBar dirty behavior, empty/loading/error). The frozen-nav test continues to pass (`model-behavior` slug unchanged both hosts).
- **NFR-7 — Performance.** The initial GET fan-out (workspace defaults, provider keys, tool-use policy, budgets/me, me/preferences) runs concurrently; the page shows the skeleton until settled. Run-start adds at most one extra round-trip for the tool-use policy fetch (already the pattern for privacy); budget preflight is unchanged (one DB round-trip only when budgets exist).
- **NFR-8 — Telemetry / audit.** Approval-policy edits are already audited (`policy.tool_use.update`). The gate's fired-axis is recorded on `tool_call_outcome` (FR-23). Budget CRUD is audited at the backend. No plaintext secrets are involved on this surface; BYOK keys are never read/written here.
- **NFR-9 — Untrusted input.** Model output, tool payloads, and stored policy/preference values are validated at the boundary (enum coercion; `ToolUsePolicySnapshot.from_response` already drops unknown modes forward-additively). A malformed persisted preference degrades to the fail-open default, never to a crash or an over-permissive `auto` where the default is stricter.

---

## 9. Backend wiring & services required

Method + path + layer; **NEW** vs **EXISTING**; contracts by `api-types`/schema name.

### Reasoning-depth default + web-access default (per-user prefs)

- **EXISTING** `GET /v1/me/preferences`, `PUT /v1/me/preferences` **[facade→be]** — per-user namespaced JSONB KV. **NEW** keys: `agent.default_reasoning_depth` (`AgentReasoningDepth | null`), `agent.web_access_default` (`bool`). No new route; add the keys to the namespaced allowlist.
- **NEW consumption [ai]** — at run-create, `ModelConfigResolver`/run-create reads these prefs (via the existing per-user resolver path) and (a) supplies the depth fallback before `DepthBudgetTable.apply` (FR-10), (b) seeds `AgentRuntimeContext.web_access` (FR-14). This is the one net-new runtime read the program adds (mirroring the privacy read already in `provider_kwargs`).
- **NEW field [ai]** — `reaches_public_web: bool` on the tool card/spec (`capabilities/tools/cards.py`); tool-load filter honors `context.web_access` (`capabilities/*` middleware, same seam as `ToolPermissionChecker`).
- **types:** extend the me/preferences typed shape (or its untyped KV contract) with the two keys; add `default_reasoning_depth`/`web_access` to any run-create request contract that the composer sends per-run.

### Approval policy (enforcement)

- **EXISTING** `GET/PUT /v1/me/policies/tool-use`, `GET/PUT /v1/workspace/policies/tool-use` **[facade `me_routes.py` → be `tool_use_policies.py`]** — three axes × four modes, per-org default + per-user override, admin-gated workspace writes, audited. No new route.
- **NEW enforcement wiring [ai]:**
  - `UserPoliciesResolver` fetches `/internal/v1/policies/tool-use` (workspace + user) and populates `user_policies_json['tool_use']` (FR-19).
  - Run-start builds `ToolUsePolicySnapshot` and invokes `ToolUsePolicyGate.decide()` per tool, translating to `interrupt_on` rules / tool-drop / `RUN_REJECTED` (FR-20/21). Enforcement point: the tool-load + `_native_interrupt_config` composition in `execution/factory.py`; blocked-dispatch backstop at the tool-dispatch seam.
- **NEW validation [be]** — per-axis mode legality in the PUT validator (`tool_use_policies.py`): reject `read` ∈ {`require`,`block`} and `destructive` ∈ {`auto`,`ask`} so stored state can't express a mode the UI can't show and the gate can't over-block reads (OD-1). Additive, back-compat: existing rows already conform to the deployment defaults.

### Spend guardrail (config)

- **EXISTING** `GET /v1/budgets`, `POST /v1/budgets`, `GET /v1/budgets/me`, `PATCH /v1/budgets/{id}`, `DELETE /v1/budgets/{id}` **[facade `app.py:1265+` → ai `runtime_api`]** — contracts `BudgetCreateRequest`, `BudgetUpdateRequest`, `BudgetView`, `BudgetListResponse`, `BudgetMeRow`, `BudgetMeResponse` (`runtime_api/schemas/budgets.py`). Enforcement `BudgetEnforcer.preflight` (`budgets/enforcer.py`) wired in `run.py`. No new route or store.
- **NEW authorization [facade/be]** — `admin:budgets` scope on org-scoped writes; self-only on user-scoped (FR-27 / NFR-1). Replaces the `# admin:budgets … lands in A10` TODO (`app.py:1260`).
- **NEW contract mirror [types]** — mirror the budget request/response shapes into `api-types` (`packages/api-types/src/agents.ts`) so both binders share types (today budgets have no `api-types` presence).

### Default model

- **EXISTING** `GET/PUT /v1/agent/workspace/defaults` **[facade→ai]** — `WorkspaceDefaultsResponse` / `UpdateWorkspaceDefaultsRequest`; catalog `GET` for options (`api/model_catalog.py`). Web reuses these (FR-4). No backend change.

---

## 10. Acceptance criteria

**Visual parity (both hosts)**

- [ ] Opening Settings → Model & behavior on **web** and **desktop** renders the identical `ModelBehaviorPage` structure: Defaults card (model / depth / web), Approval policy card (3 axes with the exact design modes), Spend guardrail card ($ cap + pause toggle).
- [ ] The legacy web screen (`sections/ModelAndBehavior.tsx`, `ToolUsePolicyPanel` in this section) no longer renders for `model-behavior`.
- [ ] Header/section copy, hints, and the connector note match §6 (D-1, D-2).
- [ ] SaveBar docks on any dirty knob on both hosts; Save/Discard commit/revert all knobs together; save errors appear inline without discarding successful writes (D-3).

**Cross-host equivalence**

- [ ] Each knob set on web and re-opened on desktop (same identity) shows the same persisted value, and vice-versa.
- [ ] Desktop approval-policy default is the backend default (`write:ask`), not `write:require` (D-5).

**Persistence round-trips**

- [ ] Default model, reasoning depth (mapped), web access, approval policy (3 axes), and monthly cap + pause each survive a reload on both hosts.
- [ ] Reasoning-depth `Auto` persists as "no explicit depth" and hydrates back to `Auto`.

**Runtime obeys (the load-bearing criteria)**

- [ ] Setting Write → _Require approval_ pauses a run for approval on the first write-tool dispatch; _Ask first_ pauses once per tool per run; _Block_ removes destructive/write tools from the model and rejects a forced dispatch with a safe message (`RUN_REJECTED`).
- [ ] With the tool-use policy lane unconfigured, runs proceed under deployment defaults (fail-open); no run is rejected for missing policy state.
- [ ] Reasoning depth _Deep_ (no composer override) makes the run inherit `deep` and `DepthBudgetTable` scale 2×; _Quick_ → `fast`.
- [ ] Web access off removes web-reaching tools from the model's toolset on the next run; on re-enables them.
- [ ] Monthly cap $X with pause on: a run whose estimate crosses $X is denied before the LLM call (`budget_exceeded`); pause off (soft): warns and proceeds (`budget_warning`).

**Security invariants**

- [ ] A non-admin cannot create/modify an org-scoped budget; can manage only their own user-scoped budget (`admin:budgets` enforced server-side).
- [ ] The store rejects `read=block` and `destructive=auto|ask` on PUT (OD-1).
- [ ] No BYOK plaintext is read/written/logged by this surface.

**Tests required**

- [ ] `ai-backend`: gate translation (allow/ask/require/block → interrupt/drop/reject); snapshot fail-open; resolver populates `user_policies_json['tool_use']`; depth fallback at run-create; web-tool filter honors `context.web_access`; a hermetic real-graph run proving a `write=require` policy actually interrupts.
- [ ] `backend`: per-axis PUT validation; org-scope `admin:budgets` authorization.
- [ ] `chat-surface`: `ModelBehaviorPage`/`ApprovalPolicy` unchanged unit coverage + the cross-host equivalence test (NFR-6).
- [ ] Both binders: hydrate/persist round-trip for each store; SaveBar dirty/commit; loading/error states.

---

## 11. Open decisions

- **OD-1 — Per-axis mode legality: enforce in the store, or UI-only?**
  Options: (a) UI-only constraint (status quo); the gate honors whatever's stored, so a stray `read=block` silently kills all read tools. (b) Mirror the constraint in the backend PUT validator.
  **Recommended: (b).** The store must not be able to express a mode the UI can never show, and a `read=block` that blocks _all_ reads is a foot-gun. Additive and back-compat.

- **OD-2 — Clearing the monthly cap: DELETE vs pause.**
  Options: (a) DELETE the budget row. (b) PATCH `status: "paused"` (keeps history/config).
  **Recommended: (a) DELETE** for the solo user-scoped cap — simplest, matches "no cap = no budget", and the config is trivially recreated. (Team org budgets, out of scope, may prefer pause.)

- **OD-3 — Persistence home for reasoning-depth-default and web-access-default (per-user).**
  `workspace_defaults` is ORG-scoped and can't carry a per-user knob as-is; the design frames these as personal.
  Options: (a) `/v1/me/preferences` per-user KV (exists; add two namespaced keys). (b) Add a per-user override layer to `workspace_defaults`. (c) Reuse `behavior_overrides` (org-scoped; wrong scope for the team surface).
  **Recommended: (a).** Uses an existing per-user home (no 4th invented, no migration), keeps org-vs-user semantics honest for the team surface, and the runtime adds one per-user read at run-start — the same shape as the existing privacy read. Default model stays on `workspace_defaults` (already live + consumed; on solo desktop org == user, so it _is_ the user's default). A future admin org-default layer for depth/web is deferred.

- **OD-4 — Web-access capability classification: new side-effect vs card flag.**
  Options: (a) `ToolSideEffect.WEB` (re-classifies every tool card; heavy; conflates "reaches web" with mutation semantics). (b) A boolean `reaches_public_web` on the card/spec + a run-context flag + load-time filter.
  **Recommended: (b).** Orthogonal to read/write/delete/external*call, cheap to add, and the enforcement is a load-time \_filter* (never expose the tool) rather than a per-dispatch gate — matching how unauthorized cards are already filtered.

- **OD-5 — Web-access default value: on or off.**
  Options: (a) default **on** (matches current desktop `webAccess:true`, matches a research-agent product). (b) default **off** (conservative; explicit opt-in to the public web).
  **Recommended: (a) on.** The product is a web-reaching agent and the current desktop default is on; flipping to off silently cripples first-run behavior. Fail-open when unset (NFR-4). Revisit if a privacy-first deployment profile wants off.

- **OD-6 — Fate of the four non-design `behavior_overrides` knobs (system prompt, temperature, citation density, refusal behavior).**
  Options: (a) drop them from the settings surface (storage retained, still resolved at run-create); (b) relocate them to an "Advanced" disclosure on this page; (c) keep a separate legacy panel.
  **Recommended: (a).** The v3 design deliberately omits them; keeping their storage/runtime path intact means no behavior regression while the surface stays true to the design. If a power-user need surfaces, add an Advanced disclosure (b) later.

- **OD-7 — Reasoning "Auto" semantics.**
  Options: (a) `Auto` = omit depth → resolver baseline (`balanced`) (simple, deterministic). (b) `Auto` = model-adaptive (runtime chooses per prompt) — no such mechanism exists today.
  **Recommended: (a)** for this PRD; (b) is a future enhancement that would need a run-time heuristic, not a settings change.

---

## 12. Rollout & sequencing

Small, independently shippable slices. Each lands with tests; none regresses the frozen `model-behavior` nav slug.

- **PR-1 — Web convergence (surface only).** `SettingsBinder` renders `ModelBehaviorPage`; build cloud/local options + default-model persist (FR-1/4/5); full SaveBar contract on both binders (D-3). Retire `ModelAndBehavior.tsx`/`ToolUsePolicyPanel` from this section (FR-2). _Risk:_ web loses the four non-design knobs — gated by OD-6 (storage retained; no runtime change). Ship behind nothing; it's a pure surface swap. **Now web === desktop visually.**
- **PR-2 — Approval policy: desktop→store + runtime enforcement.** Desktop `ApprovalPolicy` binds to `/v1/me/policies/tool-use` (FR-17/18, fixes D-5). Backend per-axis validator (OD-1). `UserPoliciesResolver` populates `['tool_use']`; run-start builds the snapshot and drives the gate → interrupts / drop / reject; fail-open (FR-19..23). _Highest-value + highest-risk slice_ — the "stored ≠ obeyed" fix. Ship the resolver+gate wiring behind a kill-safe default (fail-open to deployment defaults) and validate with a hermetic real-graph run.
- **PR-3 — Spend config surface + authorization fix.** Bind the spend block to `/v1/budgets` (FR-24), mirror budget shapes into `api-types`, and close the `admin:budgets` gap (FR-27 / NFR-1). Enforcement already exists, so this is surface + auth only.
- **PR-4 — Reasoning-depth vocabulary resolution.** Add `agent.default_reasoning_depth` pref, UI↔wire mapping, run-create fallback (FR-7..12); deprecate `default_reasoning_effort` writes. Small, isolated.
- **PR-5 — Web access as a real gate.** `reaches_public_web` flag + `context.web_access` + load-time filter + `agent.web_access_default` pref (FR-13..16). Ships last because it introduces a new capability classification and touches the tool loader.

**Test strategy.** Unit coverage per layer (gate translation, snapshot fail-open, resolver population, depth fallback, web filter, per-axis validation, budget authorization); a hermetic real-graph run per enforcement knob proving the run _behaves_ differently; a chat-surface cross-host equivalence test (NFR-6); binder round-trip tests on both hosts.

**Risk notes.**

- The approval-enforcement PR is the one that can change agent behavior for existing users: a stored `write:require` that was previously ignored will start pausing runs. This is _correct_ but is a visible behavior change — call it out in release notes; the fail-open default means only users who explicitly set a policy are affected.
- Dropping the four legacy knobs from the surface (OD-6) must not delete their storage; a data-loss regression here would silently change run behavior. Keep storage + run-create resolution intact.
- Web-access filtering must be a _load-time_ filter, not a post-hoc rejection, so the model never plans around a tool it can't use.

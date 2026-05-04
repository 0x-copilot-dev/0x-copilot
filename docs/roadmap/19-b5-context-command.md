# PR 19 — B5: /context Slash Command

**Spec ID:** B5 | **Track:** Token Usage | **Wave:** 5 (Usage UX + Budgets) | **Estimated effort:** M
**Depends on:** B1, B2, B3, B4
**Required for:** none (UX feature)

---

## 1. Functional Specification

### 1.1 Goal

"Where did the tokens go?" — a Claude Code-style `/context` view per conversation. Shows the model's context-window usage, headroom, and a breakdown by LLM call and subagent.

### 1.2 User-visible behavior

- **End user:** types `/context` in the composer → side panel opens (no message sent) showing:
  - Model + context window size.
  - Current input/output/cached_input tokens for the latest run.
  - Available headroom (integer percent).
  - Breakdown: tokens per model call, tokens per subagent task, compression events.

### 1.3 Out of scope

- Multi-conversation aggregate (B6 covers /usage).
- Token-by-token forecasting (we show what's in the latest run only).

---

## 2. Technical Specification

### 2.1 Architecture

- New endpoint `GET /v1/agent/conversations/{conversation_id}/context`.
- Joins B1's `runtime_run_usage` (latest run for the conversation) + B2's `runtime_model_call_usage` (per-call) + existing `runtime_compression_events` + `model_pricing.context_window_tokens`.
- Server returns integer percent (`headroom_pct: 0..100`); UI never re-derives floats.
- Frontend slash-command opens a side panel — does NOT send a user message.

### 2.2 Schema changes

None.

### 2.3 Endpoints

`GET /v1/agent/conversations/{conversation_id}/context` →

```ts
{
  model: { provider: string, name: string, context_window_tokens: number | null },
  current: {
    last_run_id: string | null,
    input_tokens: number,
    output_tokens: number,
    cached_input_tokens: number,
    available_tokens: number | null,           // null when context_window unknown
    headroom_pct: number | null                // integer 0..100
  },
  breakdown: {
    by_call: Array<{
      event_id: string,
      model_name: string,
      input: number, output: number, cached_input: number,
      task_id: string | null
    }>,
    by_subagent: Array<{
      subagent_id: string, name: string,
      total: number,
      call_count: number
    }>,
    compression_events: Array<{
      before: number, after: number,
      strategy: string, at: ISO
    }>
  }
}
```

Mirrored on facade.

### 2.4 Code changes

**Backend:**

- New route handler in [services/ai-backend/src/runtime_api/http/routes.py](../../services/ai-backend/src/runtime_api/http/routes.py) under conversation routes.
- New schema in `services/ai-backend/src/runtime_api/schemas/conversations.py` — `ConversationContextResponse`.
- New service method `RuntimeApiService.get_conversation_context(org_id, user_id, conversation_id)` — joins B1 + B2 + existing + pricing.
- Fallback when `context_window_tokens` is NULL (model not in pricing): use `runtime_settings.default_max_input_tokens` and return `headroom_pct` as `null` to signal "unknown".
- `available_tokens = max(0, context_window - input_tokens)`. Headroom = `floor(available / context_window * 100)`.

**Facade:** forwarding handler.

**api-types:** `ConversationContextResponse` mirror.

**Frontend:**

- `apps/frontend/src/api/agentApi.ts` — `getConversationContext(conversationId)`.
- Modify [apps/frontend/src/features/chat/components/composer/AssistantComposer.tsx](../../apps/frontend/src/features/chat/components/composer/AssistantComposer.tsx) — register new slash command:
  ```ts
  { id: "context", label: "Context", description: "Show context window usage", execute: openContextPanel }
  ```
- New `apps/frontend/src/features/chat/components/details/ContextPanel.tsx`:
  - Single-bar headroom gauge (use design-system primitive).
  - Tables for by_call and by_subagent.
  - List of compression events.
  - All percentages from server, never re-derived client-side.

### 2.5 Trust model & failure semantics

- Endpoint scoped via `RuntimeApiRoutes.scoped_identity`.
- Conversation must belong to (org_id, user_id) — else 404 (not 403).
- No completed runs yet → returns `last_run_id: null` and zero breakdown; UI shows "no data yet."

### 2.6 Tenant isolation

Standard org_id + user_id filter on conversation lookup.

### 2.7 Observability

- Metric: `context_endpoint_request_total{outcome}`.

---

## 3. Requirements & Acceptance Criteria

### 3.1 Functional acceptance criteria

- [ ] `/context` command opens panel without sending a message.
- [ ] Empty conversation → "no data" state.
- [ ] After a multi-call+subagent run → all breakdown sections populated.
- [ ] Sum of by_call equals current.input + cached_input (reconciliation).
- [ ] When model has no `context_window_tokens` in pricing → `headroom_pct: null`, gauge shows "unknown" state.

### 3.2 Test plan

**Backend unit:**

- Empty conversation → null/zeros.
- Reconciliation invariant.
- Unknown model → headroom_pct null.

**Backend integration:**

- Conversation in org_a accessed by user_b → 404.
- Conversation with subagent run → by_subagent populated.

**Frontend unit (vitest):**

- Slash command triggers panel.
- Renders the response shape.
- Percentage rendering matches server value (no client-side derivation).

**Frontend e2e:**

- Type `/context` → panel opens with current values.

### 3.3 Compliance evidence produced

- Per-conversation usage transparency for end users.

### 3.4 Rollout plan

Purely additive read endpoint + UI. Behind no flag.

### 3.5 Backout plan

Hide the slash command via a build-time flag.

### 3.6 Definition of done

- [ ] Endpoint + facade + api-types live.
- [ ] Frontend slash command + panel ship.
- [ ] Tests pass.
- [ ] Reconciliation invariant holds in fixture data.

---

## 4. Critical files

- Modify: [services/ai-backend/src/runtime_api/http/routes.py](../../services/ai-backend/src/runtime_api/http/routes.py)
- Modify: `services/ai-backend/src/runtime_api/schemas/conversations.py`
- Modify: `services/ai-backend/src/agent_runtime/api/service.py`
- Modify: [services/backend-facade/src/backend_facade/app.py](../../services/backend-facade/src/backend_facade/app.py) — forwarding
- Modify: [packages/api-types/src/index.ts](../../packages/api-types/src/index.ts)
- Modify: [apps/frontend/src/api/agentApi.ts](../../apps/frontend/src/api/agentApi.ts)
- Modify: [apps/frontend/src/features/chat/components/composer/AssistantComposer.tsx](../../apps/frontend/src/features/chat/components/composer/AssistantComposer.tsx)
- New: `apps/frontend/src/features/chat/components/details/ContextPanel.tsx`

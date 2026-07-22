# Chat surface invariants

Two regressions kept biting the chat surface; these rules exist so they
don't return. Both are pinned by tests — if you change the underlying code,
update the tests in the same change rather than weakening the invariant.

See also:

- [../architecture/04-streaming.md](../architecture/04-streaming.md) — where
  `RunUiPhase` comes from
- [`src/features/chat/chatRunState.ts`](../../src/features/chat/chatRunState.ts) —
  `deriveRunUiState` + `phaseForEvent`
- [`src/features/chat/chatModel/README.md`](../../src/features/chat/chatModel/README.md) —
  the reducer pipeline that feeds these projections

---

## 1. Planning-pulse visibility

### CSS rule that must stay

`<PlanningIndicator>` lives inside `.aui-thread-viewport`, a flex column
with `min-height: 0` so `overflow-y: auto` can scroll past tall message
lists. But `min-height: 0` also lets flex children shrink **below their
intrinsic size**, so the indicator must opt out of flex compression with
`flex: 0 0 auto`.

Source: [`src/styles.css`](../../src/styles.css) — `.aui-planning-indicator`.
Do not remove the `flex: 0 0 auto` rule. The element silently collapses to
`height: 0` once the message list crosses a screen-height threshold, even
though `data-visible="true"` and computed `max-height` are correct. The CSS
comment in the file explains the trap.

### State rule that must stay

`deriveRunUiState` ([`chatRunState.ts`](../../src/features/chat/chatRunState.ts))
returns `showPlanningIndicator: true` for **every active phase**:

| Phase                    | Pulse? |
| ------------------------ | ------ |
| `starting`               | ✅     |
| `working`                | ✅     |
| `acting`                 | ✅     |
| `writing`                | ✅     |
| `reasoning`              | ✅     |
| `idle`                   | ❌     |
| `terminal`               | ❌     |
| `waiting_for_permission` | ❌     |

Do not re-introduce phase-specific suppression (e.g. "no pulse during
writing" or "no pulse while a tool is running"). On fast models the
affected phases pass in <1s and the user perceives the run as dead. The
corresponding tests in [`chatRunState.test.ts`](../../src/features/chat/chatRunState.test.ts)
pin every active phase to `showPlanningIndicator: true`.

### Phase derivation

`phaseForEvent` maps the latest `RuntimeEventEnvelope` to a phase:

```
event_type ∈ { run_completed, run_cancelled, run_failed }  → terminal
event_type ∈ { model_delta, final_response }               → writing if visible text else working
event_type ∈ { reasoning_summary, reasoning_summary_delta} → reasoning
event_type ∈ { tool_call_completed, tool_result,
               subagent_completed }                        → working
event_type ∈ { tool_call_started, tool_call_delta,
               subagent_started, subagent_progress }       → acting
activity_kind ∈ { tool, subagent } and status active       → acting
event_type == run_started                                  → starting
otherwise                                                  → working
```

`waiting_for_permission` is **not** an event-derived phase — it's a state
overlay: if `eventPhase !== "terminal"` and there's a pending approval for
the active run, `deriveRunUiState` returns `waiting_for_permission` and
suppresses the pulse.

---

## 2. Composer hint row

The composer hint strip in `<AssistantComposer>` — now `/ skills ·
Sources cited inline` — is **stateless info** — it must render whether
or not a run is active.

**Do not gate the `hint` prop on `running`** (or any other run-state
flag). Hiding it during a run was a real shipped regression; the user is
mid-flight, can't see their shortcuts, and the composer looks broken. If
you add a new hint or change the strip, render it unconditionally and let
the affordance itself reflect availability (e.g. disable a button — don't
unmount the row).

The `↵ send` / `⇧+↵ new line` keyboard hints and the duplicated `model`
name were **intentionally removed** from this strip to match the Claude
Design composer mock (its composer shows no send/newline hint, and the
model name is one-source-of-truth in the `ModelPill` above the row).
Don't "restore" them as a regression fix — the removal is deliberate; see
the `hintRender` comment in
[`packages/chat-surface/src/composer/AssistantComposer.tsx`](../../../../packages/chat-surface/src/composer/AssistantComposer.tsx).

Source: composer components live under [`src/features/chat/components/composer/`](../../src/features/chat/components/composer/).

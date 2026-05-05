# PR 3.5 — Shipped-PR gap closure (W1–W3)

> **Status:** Draft (PRD + Spec + Architecture)
> **Plan reference:** Cross-cuts PR 1.6, PR 2.1, PR 2.2, PR 2.3, PR 3.1 in [`/Users/parthpahwa/.claude/plans/fetch-this-design-file-resilient-pumpkin.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md)
> **Owner:** frontend (the wiring + tests) · api-types (one tighter type) · ai-backend (one parity test)
> **Size:** **S–M.** Net production code ≈ **95 LOC**; the rest is **test backfill ≈ 600 LOC**. Zero migrations, zero new events, zero new endpoints, zero new dependencies.
> **Reads alongside:** [`pr-1.6-workspace-defaults-conversation-lifecycle.md`](pr-1.6-workspace-defaults-conversation-lifecycle.md), [`pr-2.1-topbar-chrome-thinking-depth.md`](pr-2.1-topbar-chrome-thinking-depth.md), [`pr-2.2-sidebar-user-card-keymap.md`](pr-2.2-sidebar-user-card-keymap.md), [`pr-2.3-welcome-state-thread-polish.md`](pr-2.3-welcome-state-thread-polish.md), [`pr-3.1-citation-chips-sources-tab.md`](pr-3.1-citation-chips-sources-tab.md), [`apps/frontend/CLAUDE.md`](../../apps/frontend/CLAUDE.md)
> **Sibling PRs in flight:** PR 3.2 (workspace pane host) — coordinates with §2.2 and §2.3 here. PR 4.2 (Settings → Workspace panel) — consumes the hook this PR ships.

---

## 0 · TL;DR

A post-merge audit of the five W1–W3 PRs that shipped (1.6, 2.1, 2.2, 2.3, 3.1) found eleven gaps. Most are **test backfill**. Two are real bugs:

1. **Workspace switching silently no-ops.** `UserCard → WorkspacePicker` calls `onSwitchWorkspace(orgId)`. The prop is forwarded from `AssistantThreadList` → `Sidebar` → `UserCard` — but `ChatScreen.tsx` never **provides** it, and `useAuth().switchWorkspace` was never added. End-to-end the click does nothing.
2. **The post-prose Sources strip is invisible.** `MessageSourcesStrip` is implemented and tested in isolation, but is **never rendered inside `AssistantMessage`**. Users see inline chips and the right-rail Sources tab, but not the chip-row beneath the assistant message.

Plus nine smaller items: a typing tightening (`reasoning.depth_label`), a data hook (`useWorkspaceDefaults`) so PR 4.2 can mount its panel without touching backend types, an architectural amendment for the dual citation reducer, and six missing test files.

This PR closes them in **one commit** (one revert is one revert) without spawning a refactor wave: it does **not** consolidate `sourcesReducer` ↔ `CitationLookup`, does **not** add the Workspace Defaults Settings panel (PR 4.2's scope), and does **not** introduce a new state container, dropdown library, or test runner. Everything reuses what's already here.

---

## 1 · PRD

### 1.1 Problem

The five PRs above shipped against acceptance lists that were satisfied **except** for the items below. Several of them sit at the seam between two PRs (e.g. the Sources strip ships in 3.1 but renders inside 3.2-style mounts), which is exactly the kind of gap a one-PR-per-feature plan tends to leak. The user has asked for a single closure pass before W3 is declared "done."

The eleven gaps grouped by impact:

| Gap     | From PR | Severity                       | What's missing                                                                                                                                                        |
| ------- | ------- | ------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **G4**  | 2.2     | 🔴 user-visible bug            | `ChatScreen` does not pass `onSwitchWorkspace` down; the prop tree dead-ends one component above the click site. `useAuth().switchWorkspace` is also absent.          |
| **G9**  | 3.1     | 🔴 user-visible feature absent | `MessageSourcesStrip` shipped but is not rendered inside `AssistantMessage`. The post-prose chip row the design specifies is invisible.                               |
| **G3**  | 2.1     | 🟡 type drift                  | `ModelCatalogModel.reasoning` is `Record<string, unknown> \| null`. PRD specified an explicit optional `depth_label` field.                                           |
| **G10** | 3.1     | 🟡 architectural drift         | Spec said to extend `CitationLookup` with `byRun` / `byConversation` layers; impl uses a separate `sourcesReducer` instead. Functionally equivalent but undocumented. |
| **G1**  | 1.6     | 🟡 prerequisite for PR 4.2     | `useWorkspaceDefaults()` hook absent. PR 4.2 needs it to mount the Settings → Workspace panel.                                                                        |
| **G5**  | 2.2     | 🟡 missing tests               | `Sidebar.test.tsx`, `UserCard.test.tsx`, `WorkspacePicker.test.tsx`                                                                                                   |
| **G6**  | 2.3     | 🟡 missing tests               | `AssistantMessage.test.tsx`, `UserMessage.test.tsx` snapshot updates                                                                                                  |
| **G7**  | 2.3     | 🟡 missing tests               | Minute-tick fake-timer transition test in `ThreadWelcome.test.tsx`                                                                                                    |
| **G8**  | 2.3     | 🟡 missing tests               | Streaming-cursor regression test                                                                                                                                      |
| **G11** | 3.1     | 🟡 missing tests               | `CitationChip.css.test.tsx`, `test_sources_replay_parity.py`                                                                                                          |
| **G2**  | 1.6     | 🟢 docs only                   | Per-feature named pytest files vs. one consolidated file.                                                                                                             |

### 1.2 Goals

1. **Wire workspace switching end-to-end** so `UserCard → WorkspacePicker` actually changes the active workspace.
2. **Mount `MessageSourcesStrip`** under each assistant message that has sealed citations, with no new event and no protocol change.
3. **Tighten `ModelCatalogModel.reasoning`** to an explicit interface (carries `depth_label`) so the topbar pill can display real data when it arrives.
4. **Document and validate** the dual-store citation architecture (`CitationLookup` for chip resolution, `sourcesReducer` for the SourcesTab). Don't refactor — assert the **invariant** with one test.
5. **Ship `useWorkspaceDefaults()`** as a thin data hook so PR 4.2 can mount the panel without re-touching backend types/routes.
6. **Backfill the missing tests** — and prove the wiring fix (G4) and the mount fix (G9) with integration tests, not just snapshots.
7. **No streaming change, no schema change, no new endpoint, no new npm dep, no new design-system primitive.**

### 1.3 Non-goals

- **No refactor of the citation registry** into a single reducer. Two existing surfaces (chip resolution + SourcesTab) consume the data via two reducers fed by the **same** event in the same `applyRuntimeEvent` pass. We document the seam, add an invariant test, and move on. _Anti-pattern to avoid: "tidy up two stores into one" without a forcing function — that's a re-merge target, not a real bug._
- **No Workspace Defaults Settings UI.** PR 4.2 owns it. We ship the data hook and stop there. (User's "DRY / less code" rule.)
- **No `tinykeys` re-evaluation, no Radix dropdown swap, no `cmdk` introduction.** PR 2.2 already chose `tinykeys`; PR 2.1 already chose `Menu` from design-system. Re-litigating the same library decisions per PR is exactly the anti-pattern the user flagged.
- **No splitting `test_workspace_defaults_lifecycle.py`** into per-feature files. Pytest convention is one file per route group; the consolidated file passes the same coverage. Acceptance lists count _coverage_, not file shape.
- **No new event types or migrations.** Every gap fixed here is at the FE wire-up or test layer.

### 1.4 Success criteria

- ✅ Clicking a workspace row in `UserCard → WorkspacePicker` either rotates the session (preferred) or hard-navigates to `?workspace=<orgId>`; the chosen workspace is reflected in the next request's `x-enterprise-org-id` header. Tested in `Sidebar.integration.test.tsx`.
- ✅ Each assistant message whose run has emitted a sealed `final_response.citations` renders `<MessageSourcesStrip>` directly beneath the prose. Inline chips and the strip both resolve to the same registry rows. Tested in `AssistantMessage.integration.test.tsx`.
- ✅ `ModelCatalogModel.reasoning` is `ModelReasoningHints | null` (typed). All call sites compile unchanged. The topbar `ThinkingDepthControl`'s "Depth: …" announcement reads `reasoning.depth_label` when present, falls back to `EFFORT_BY_DEPTH[depth]` label.
- ✅ One contract test in `citationStore.invariant.test.ts` asserts that for every `source_ingested` event, both reducers ingest a row with identical `(citation_id, source_connector, source_doc_id, title, snippet, freshness_at)` payloads.
- ✅ `useWorkspaceDefaults()` returns `{ defaults, loading, error, save }`. Optimistic update on `save` rolls back on 4xx. Tested.
- ✅ Six missing tests added; all pass.
- ✅ `npm run typecheck --workspace @enterprise-search/frontend` and `npm run build --workspace @enterprise-search/frontend` pass. `make test` green. ai-backend pytest green incl. one new replay-parity test.

### 1.5 User stories

| As…                          | I want…                                                                              | So that…                                                               |
| ---------------------------- | ------------------------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| Sarah (multi-workspace user) | clicking "Personal" in my user card to actually switch me to my personal Atlas       | the picker isn't a fake button                                         |
| Sarah                        | a chip-row of `[1] [2] [3]` to appear under each assistant answer                    | I can scan citations without re-opening the right rail                 |
| Marcus (admin)               | the topbar pill to read "Deep" not "high" when my model exposes a custom depth label | the chrome reflects the model catalog's actual labels                  |
| PR 4.2 author                | a `useWorkspaceDefaults()` hook already on `main`                                    | I can ship the panel without touching `api-types` or routes            |
| Engineer reviewing           | one invariant test that says "the two citation stores can't drift"                   | the dual-reducer architecture is documented and enforced, not implicit |
| Future-me reverting          | one PR to revert                                                                     | one rollback restores all of W1–W3's intended behavior, no half-state  |

---

## 2 · Spec

### 2.1 G4 — Workspace switch wiring

**The bug.** The chain `WorkspacePicker → UserCard → Sidebar → AssistantThreadList` already accepts and forwards `onSwitchWorkspace?: (orgId: string) => void`. The chain dead-ends at `apps/frontend/src/features/chat/ChatScreen.tsx`, which never supplies the prop. Also, `apps/frontend/src/features/auth/AuthContext.tsx` does not expose `switchWorkspace`. Net effect: clicking a workspace row in the UserCard popover does nothing.

**The fix — two small edits, no new abstraction:**

```ts
// apps/frontend/src/features/auth/AuthContext.tsx (extend existing)
interface AuthApi {
  // ... existing
  switchWorkspace: (orgId: string) => Promise<void>;
}
```

Implementation: call existing `POST /v1/auth/sessions` with `{ workspace_id: orgId }` if available, else **hard-navigate** to `${location.origin}${location.pathname}?workspace=${encodeURIComponent(orgId)}`. PR 2.2 §3.7 explicitly authorised the hard-nav fallback as v1; we use it now and let a follow-up upgrade to session rotation when the auth team lands the endpoint.

```tsx
// apps/frontend/src/features/chat/ChatScreen.tsx (one wiring line)
const auth = useAuth();
// ...
<AssistantThreadList
  /* ...existing props */
  onSwitchWorkspace={auth.switchWorkspace}
/>;
```

If a run is active, `WorkspacePicker.tsx` already shows the "switching will stop the active response" confirm (per PR 2.2 §3.7); on confirm we call `await onCancel()` (existing) and then `auth.switchWorkspace(orgId)` — the cancellation is one line in the picker, not a new orchestrator.

### 2.2 G9 — `MessageSourcesStrip` mount

**The bug.** `apps/frontend/src/features/chat/components/messages/MessageSourcesStrip.tsx` ships with tests but is referenced **only by its own test file**. The post-prose Sources strip the design specifies (Design Doc § Sources strip after assistant message) is invisible.

**The fix — one render slot in `AssistantMessage.tsx`:**

```tsx
// apps/frontend/src/features/chat/components/messages/AssistantMessage.tsx
import { MessageSourcesStrip } from "./MessageSourcesStrip";
import { useRunCitations } from "../citations/citationsContext";

export function AssistantMessage({ message }: Props) {
  const citations = useRunCitations(message.runId, { sealedOnly: true });
  return (
    <MessagePrimitive.Root className="aui-message aui-message--assistant">
      <div className="aui-message__body">{/* existing parts/footer */}</div>
      {citations.length > 0 && (
        <MessageSourcesStrip
          citations={citations}
          onSelect={(id) =>
            paneController.openOn("sources", { focusCitationId: id })
          }
        />
      )}
    </MessagePrimitive.Root>
  );
}
```

`useRunCitations(runId, { sealedOnly: true })` is a thin selector over the existing `citationsContext` — it returns the `final_response.citations` array if the run has terminated, else an empty array. This is the **only** new selector this PR introduces; it lives next to `useCitation` from PR 1.1, ~6 LOC.

**Why "sealed only".** Mid-stream the registry is partial; rendering the strip live would shimmer as rows appear. The inline chips are already rendering live via the Streamdown plugin (PR 1.1 / 3.1). The strip is a "summary" affordance — it should appear once when the run completes. UX-wise this matches the design's mock (the strip is below the entire answer).

`paneController.openOn` is the same hook PR 3.1 / 3.2 already established (`useWorkspacePaneAutoOpen`'s sister API). If the pane host (PR 3.2) is not yet mounted, the call no-ops — no regression.

### 2.3 G10 — Citation registry: amend, don't refactor

**The drift.** PR 3.1 §2.4 specified extending `CitationLookup` with `{ byRun, byConversation, resolve }`. The impl shipped a separate `sourcesReducer` for the SourcesTab while keeping `citationsContext` as the chip-resolver. Two stores, both populated by `applyCitationEvent` from the same `source_ingested` event.

**Decision: keep both. Document the seam. Enforce the invariant.**

Why we don't merge:

- Both consumers are stable and shipped. Refactoring without a forcing function is exactly the anti-pattern the user flagged ("don't introduce abstractions beyond what the task requires").
- The two reducers serve different shapes: chip resolver wants `(citation_id) → CitationSourceRef`; SourcesTab wants ordered rows with grouping affordances. Forcing both through one Map costs derivation work for no win.
- A merge would force PR 3.2 (in flight) to re-pivot.

**What we ship instead** — `apps/frontend/src/features/chat/chatModel/__tests__/citationStore.invariant.test.ts`:

```ts
test("source_ingested feeds both stores with byte-identical fields", () => {
  const event = makeSourceIngestedEvent({
    /* fixture */
  });
  const next = applyRuntimeEvent(initial, event);

  const fromChipStore = next.citationsByRun
    .get(event.run_id)!
    .get(event.payload.citation.citation_id)!;
  const fromSourcesStore = next.sourcesByConversation
    .get(event.conversation_id)!
    .find((c) => c.citation_id === event.payload.citation.citation_id)!;

  expect(pick(fromChipStore, INVARIANT_FIELDS)).toEqual(
    pick(fromSourcesStore, INVARIANT_FIELDS),
  );
});
```

`INVARIANT_FIELDS = ["citation_id", "source_connector", "source_doc_id", "title", "snippet", "freshness_at", "ordinal"]`. One test. If a future PR forks the reducers, CI fails immediately.

We also add a one-paragraph note in `apps/frontend/src/features/chat/chatModel/README.md` (extend existing) explaining the dual-store amendment to PR 3.1's spec.

### 2.4 G3 — `reasoning.depth_label` typed

```ts
// packages/api-types/src/index.ts
export interface ModelReasoningHints {
  enabled: boolean;
  effort?: "low" | "medium" | "high";
  summary?: "auto" | "off";
  depth_label?: string;
}

export interface ModelCatalogModel {
  // ... existing fields
  reasoning?: ModelReasoningHints | null;
}
```

The type widens gracefully — every existing call site that consumed `reasoning` as `Record<string, unknown>` continues to compile because `ModelReasoningHints` is structurally a subset. `ThinkingDepthControl` reads `model.reasoning?.depth_label ?? DEPTH_FALLBACK_LABEL[depth]`. One added test in `applyDepth.test.ts`: "uses depth_label when provided."

### 2.5 G1 — `useWorkspaceDefaults()` data hook

```ts
// apps/frontend/src/features/settings/useWorkspaceDefaults.ts
import type {
  WorkspaceDefaultsResponse,
  UpdateWorkspaceDefaultsRequest,
} from "@enterprise-search/api-types";

export function useWorkspaceDefaults(identity: RequestIdentity) {
  const [defaults, setDefaults] = useState<WorkspaceDefaultsResponse | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    /* GET /v1/agent/workspace/defaults; set state */
  }, [identity]);

  const save = useCallback(
    async (next: UpdateWorkspaceDefaultsRequest) => {
      const previous = defaults;
      setDefaults({
        ...defaults!,
        ...next,
        updated_at: new Date().toISOString(),
      });
      try {
        const updated = await putWorkspaceDefaults(next, identity);
        setDefaults(updated);
      } catch (err) {
        setDefaults(previous); // optimistic rollback
        setError(errorMessage(err, "Could not save workspace defaults"));
        throw err;
      }
    },
    [defaults, identity],
  );

  return { defaults, loading, error, save };
}
```

Implementation notes:

- `getWorkspaceDefaults` and `putWorkspaceDefaults` are added to `apps/frontend/src/api/agentApi.ts` (existing module — same file as `getConversation`, `listMessages`, `listSources`). ~12 LOC.
- The hook is consumed by **nobody in this PR**. PR 4.2's panel imports it. Ship-as-data; UI ships in 4.2.
- We test it via `useWorkspaceDefaults.test.tsx` with a mocked fetch.

This satisfies G1 with the smallest possible surface: one hook, two API client calls, one test. The Settings panel is PR 4.2's job (already in the plan).

### 2.6 G5 / G6 / G7 / G8 / G11 — Test backfill

Each test file lives next to the component it covers (project convention). What they assert:

| File                                                                              | Asserts                                                                                                                                                                                                                                  |
| --------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Sidebar.test.tsx`                                                                | Renders search input, group sections, user card; pulse on active row; ⌘K focuses search via the keymap.                                                                                                                                  |
| `UserCard.test.tsx`                                                               | Popover open/close; sign-out path invokes `auth.signOut`; workspace switch invokes the prop with the chosen `orgId`; `confirm` flows through when an active run is present.                                                              |
| `WorkspacePicker.test.tsx`                                                        | Single-workspace renders disabled list; multi-workspace renders all with role + member count + last-active; error state shows retry.                                                                                                     |
| `AssistantMessage.test.tsx`                                                       | Renders parts via `MessagePrimitive`; `<MessageSourcesStrip>` mounts iff sealed citations exist; class names align with CSS rules in `styles.css`.                                                                                       |
| `UserMessage.test.tsx`                                                            | Right-aligned bubble class; max-width assertion via `toHaveStyle`; attachments render.                                                                                                                                                   |
| `ThreadWelcome.test.tsx` (extend)                                                 | `vi.useFakeTimers()` advances from 22:59 to 23:00 → greeting transitions "Good evening" → "Working late."                                                                                                                                |
| `streamingCursor.test.tsx`                                                        | Cursor `::after` pseudo-element renders on the last paragraph of a streaming assistant message; reduce-motion turns off the blink animation.                                                                                             |
| `CitationChip.css.test.tsx`                                                       | Visual contract: dim default, accent on hover (via `toHaveStyle`), connector glyph slot via `data-connector` attr (`toHaveAttribute`), focus ring on `:focus-visible`.                                                                   |
| `services/ai-backend/tests/integration/runtime_api/test_sources_replay_parity.py` | A run that ingests N citations live; `replayRunEvents` over the same run rebuilds the registry; `GET /v1/agent/conversations/{id}/sources` returns the same N rows in the same `ordinal` order.                                          |
| `Sidebar.integration.test.tsx` (new)                                              | Mount `ChatScreen` with stubbed `useAuth.switchWorkspace`; click a workspace row in UserCard → assert the stub was called with the right `orgId`. **This is the test that proves G4 is fixed end-to-end.**                               |
| `AssistantMessage.integration.test.tsx` (new)                                     | Render `ChatScreen` with a fixture history containing a sealed run with 3 citations; assert `MessageSourcesStrip` renders one button per citation under that assistant message. **This is the test that proves G9 is fixed end-to-end.** |

All tests reuse existing fixtures (the `chatModel` test helpers, ai-backend's `tests/conftest.py`). No new test runner, no new mocking library.

### 2.7 G2 — Documentation only

Confirm in this PR's reviewer notes that the consolidated `test_workspace_defaults_lifecycle.py` covers the matrix the PRD listed. No file split. Add a one-line comment at the top of that test file enumerating the originally-named cases for grep-ability:

```python
# Covers: get_defaults, update_defaults, lifecycle (PATCH/DELETE/restore),
# create_conversation_defaults_fallback, create_run_model_fallback,
# audit_emission_for_workspace_defaults, soft_delete_then_retention_sweep.
```

### 2.8 Streaming impact — explicitly **none**

| Subsystem                            | Touched?                                                                                                              |
| ------------------------------------ | --------------------------------------------------------------------------------------------------------------------- |
| `runtime_events` schema              | **No.** No new event type.                                                                                            |
| `RuntimeEventEnvelope` Pydantic / TS | **No.**                                                                                                               |
| SSE handshake (`?after_sequence=N`)  | **No.** Reconnect identical.                                                                                          |
| `runtime_worker` job loop            | **No.**                                                                                                               |
| `chatModel/eventReducer.ts`          | **No.** Both citation reducers are pre-existing; we add **one selector** (`useRunCitations`), not a new event branch. |
| Capabilities middleware / tools      | **No.**                                                                                                               |
| Audit chain                          | **No.**                                                                                                               |
| ai-backend persistence               | **No.** Test backfill only on the read path.                                                                          |

The single nominal protocol-touch is the `ModelReasoningHints` typing — but the on-wire JSON for `reasoning` is **byte-identical** before and after; we only narrow the TypeScript type. ai-backend's `ModelConfig` is unchanged (per [`agent_runtime/execution/models.py`](../../services/ai-backend/src/agent_runtime/execution/models.py)).

### 2.9 Permissions

| Caller                | Action                                                                                                                                                                                      |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Authenticated user    | Switch to any org they're a member of (RLS-enforced; the existing `organization_members` join already gates `GET /v1/me/workspaces`).                                                       |
| Authenticated user    | Read `useWorkspaceDefaults()` even if they're not admin (PR 1.6 already returns deployment fallback for non-admin); `save()` is admin-only and 403s for members (existing PR 1.6 contract). |
| `MessageSourcesStrip` | Inherits the same per-row ACL the chips inherit; no new permission boundary.                                                                                                                |

Workspace switch on an active run requires the cancel-then-switch confirm (PR 2.2 §3.7).

### 2.10 Error semantics

| Condition                                                                      | Behavior                                                                                                                                                                              |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `auth.switchWorkspace(orgId)` resolves but the next page load 401s             | Existing `<AuthGate>` redirects to LoginScreen (no change).                                                                                                                           |
| Hard-nav fallback fires while the user has unsaved attachments in the composer | We re-use the existing `beforeunload` warning that PR 2.2 already inherits from the browser; no new copy.                                                                             |
| `useWorkspaceDefaults().save()` 4xx                                            | Optimistic update rolls back; `error` state populated; component decides how to surface (PR 4.2).                                                                                     |
| `MessageSourcesStrip` receives empty citations                                 | Renders nothing (existing component already handles this — see [`MessageSourcesStrip.tsx:26-30`](../../apps/frontend/src/features/chat/components/messages/MessageSourcesStrip.tsx)). |
| Sealed-only selector returns rows mid-stream                                   | Returns `[]`; strip stays hidden until run terminates.                                                                                                                                |
| Citation invariant test fails post-merge                                       | Build red; the offending PR is identified by the diff that touched a reducer.                                                                                                         |

### 2.11 What we do NOT add (DRY)

Library survey + reject list. Re-using existing PR 2.2 / 2.1 / 1.6 surveys verbatim:

- **No `cmdk`, `kbar`, `radix-popover`, `floating-ui`, `focus-trap-react`** — already rejected in PR 2.1 / 2.2 surveys.
- **No `react-query` / `swr`** for `useWorkspaceDefaults` — one fetch, one save, no cache invalidation logic that warrants 13 KB gz. The existing manual `useEffect`+`useState` pattern (used by `useArchivedSources` in PR 3.1) is the precedent.
- **No `immer`** for the optimistic rollback — `previous` is captured by closure, `setDefaults(previous)` on catch. ~3 LOC.
- **No `vitest-visual-regression` / `chromatic` / `playwright-screenshots`** for the chip CSS test — `toHaveStyle` and `toHaveAttribute` from `@testing-library/jest-dom` (already used) cover the assertions.
- **No `jest.mock('../api/agentApi')`** in the `useWorkspaceDefaults` test — we inject the api module via a thin `agentApiContext` if it doesn't already exist; otherwise we use `vi.spyOn`. **The anti-pattern we avoid:** mocking the thing you're testing.

We add **zero npm packages** in this PR. (Confirmed via web survey: nothing publishes a "fix-the-prop-drilling" or "wire-the-snapshot-up" library.)

---

## 3 · Architecture

### 3.1 Where the changes live

```
   ┌──────────────────────────────────────────────────────────────────────────────┐
   │  apps/frontend                                                               │
   │                                                                              │
   │   features/auth/AuthContext.tsx     ◄── G4: + switchWorkspace(orgId)         │
   │                                                                              │
   │   features/chat/ChatScreen.tsx      ◄── G4: passes auth.switchWorkspace      │
   │     │                                       to AssistantThreadList           │
   │     │                                                                        │
   │     ▼                                                                        │
   │   AssistantThreadList → Sidebar → UserCard → WorkspacePicker                 │
   │   (already accept the prop; no edit needed past ChatScreen)                  │
   │                                                                              │
   │   features/chat/components/messages/AssistantMessage.tsx                     │
   │                                       ◄── G9: render <MessageSourcesStrip/> │
   │   features/chat/components/citations/citationsContext.tsx                    │
   │                                       ◄── G9: + useRunCitations selector    │
   │                                                                              │
   │   features/chat/chatModel/__tests__/citationStore.invariant.test.ts          │
   │                                       ◄── G10: invariant test               │
   │   features/chat/chatModel/README.md   ◄── G10: dual-store amendment note    │
   │                                                                              │
   │   features/settings/useWorkspaceDefaults.ts  ◄── G1: data hook              │
   │   api/agentApi.ts                            ◄── G1: + 2 client calls       │
   │                                                                              │
   │   features/chat/components/sidebar/{Sidebar,UserCard,WorkspacePicker}.test.tsx
   │   features/chat/components/messages/{Assistant,User}Message.test.tsx          │
   │   features/chat/components/thread/ThreadWelcome.test.tsx (extend)            │
   │   features/chat/components/citations/CitationChip.css.test.tsx               │
   │   test/streamingCursor.test.tsx                                              │
   │   features/chat/Sidebar.integration.test.tsx                                 │
   │   features/chat/AssistantMessage.integration.test.tsx                        │
   │                                       ◄── G5/G6/G7/G8/G11                   │
   └──────────────────────────────────────────────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────────────────────────────┐
   │  packages/api-types                                                          │
   │   src/index.ts   ◄── G3: export ModelReasoningHints; tighten reasoning field │
   └──────────────────────────────────────────────────────────────────────────────┘

   ┌──────────────────────────────────────────────────────────────────────────────┐
   │  services/ai-backend                                                         │
   │   tests/integration/runtime_api/test_sources_replay_parity.py  ◄── G11       │
   │   tests/unit/runtime_api/test_workspace_defaults_lifecycle.py  ◄── G2 (one  │
   │                                                                  comment)   │
   └──────────────────────────────────────────────────────────────────────────────┘
```

No new files in `services/backend`, `services/backend-facade`, `runtime_worker`, or anywhere in `agent_runtime/`. No migration. No new event type.

### 3.2 Why one PR

A single bundled PR for these eleven items:

- **One revert is one revert.** The biggest risk in this surface is an integration regression (G4 + G9 together). Bundling lets us roll back atomically.
- **The wiring fixes are too small for individual PRs.** G4 is two lines + a test; G9 is six lines + a test. Spinning each into its own PR is the over-fragmentation anti-pattern (more CI runs, more reviews, no signal gain).
- **Test backfill belongs with the surface it covers.** Splitting "ship feature" from "test feature" is a smell.

The user asked for **one** PRD covering all the gaps, and that's what this is. Each gap is independently revertable inside the PR (one commit per gap if reviewers prefer; squash-merge as one commit at the end).

### 3.3 DRY — what we reuse vs. what we add

| Concern                      | Reuse                                                                                                                | Add                                                                         |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| Auth identity, headers, RLS  | `useAuth()`, `RequestIdentity`, existing `x-enterprise-org-id` header                                                | `switchWorkspace` method on `AuthApi` (~12 LOC inside the existing context) |
| Workspace switch transport   | Existing session endpoint OR hard-nav with `?workspace=` (PR 2.2 §3.7 fallback)                                      | one `if`/`else` selecting the path; no new transport                        |
| Sources strip                | `MessageSourcesStrip` (PR 3.1 — already shipped)                                                                     | one render slot in `AssistantMessage.tsx`                                   |
| Chip resolution registry     | `citationsContext`, `useCitation` (PR 1.1)                                                                           | one selector `useRunCitations(runId, { sealedOnly })` (~6 LOC)              |
| SourcesTab data path         | `sourcesReducer` (PR 1.5/3.1 — already shipped)                                                                      | —                                                                           |
| Invariant proof              | `applyRuntimeEvent` reducer test fixtures                                                                            | one test (~25 LOC)                                                          |
| Workspace defaults wire      | `WorkspaceDefaultsResponse`, `UpdateWorkspaceDefaultsRequest`, `PUT /v1/agent/workspace/defaults` (PR 1.6 — shipped) | `useWorkspaceDefaults` (~60 LOC) + 2 client calls (~12 LOC)                 |
| Optimistic update + rollback | inline closure capture                                                                                               | —                                                                           |
| Reasoning typing             | existing `ModelCatalogModel`                                                                                         | one new interface, one field tightening                                     |
| Test runner / DOM            | vitest, `@testing-library/react`, `@testing-library/jest-dom` (already in use)                                       | —                                                                           |
| Fake timers for minute-tick  | `vi.useFakeTimers()` (already in use elsewhere)                                                                      | one `await vi.advanceTimersByTimeAsync(60_000)` block                       |
| Visual contract              | `toHaveStyle`, `toHaveAttribute` (already in use)                                                                    | —                                                                           |

**Net new code:**

| Layer                 | Production   | Tests        |
| --------------------- | ------------ | ------------ |
| `apps/frontend`       | ~95 LOC      | ~520 LOC     |
| `packages/api-types`  | ~10 LOC      | —            |
| `services/ai-backend` | —            | ~80 LOC      |
| **Total**             | **~105 LOC** | **~600 LOC** |

### 3.4 No third-party dependency added

Surveyed and rejected (per the user's "check internet" rule):

- **Workspace switching widgets** (`@clerk/nextjs`, `@workos-inc/react`) — these are full IdP-managed SaaS auth flows, not "switch active org in a JWT." Massive overlap with the existing `AuthContext`; would require migrating the app off in-house auth. Not in scope.
- **`react-query` / `swr` / `tanstack-query`** for `useWorkspaceDefaults` — overkill for one fetch + one save. The existing pattern (`useArchivedSources` in PR 3.1) is the precedent, ~60 LOC inline.
- **`use-immer`** for optimistic rollback — the rollback is one closure, no nested-state update.
- **`storybook-addon-css-snapshot` / Chromatic** — visual contracts that fit inside `@testing-library/jest-dom`'s assertion API don't justify a separate visual-regression infra. PR 2.1 already used the same `toHaveAttribute` / `toHaveStyle` pattern for the radiogroup.
- **Mocking libraries beyond Vitest's built-ins (`vi.spyOn`, `vi.mock`)** — Vitest covers everything we need.

Net dep delta: **zero**.

### 3.5 Sequence — workspace switch end-to-end (G4)

```
Sarah                    UserCard                WorkspacePicker          AuthContext              backend-facade           backend
  │                         │                          │                       │                          │                     │
  │  clicks chevron         │                          │                       │                          │                     │
  │ ──────────────────────► │  popover opens           │                       │                          │                     │
  │                         │                          │                       │                          │                     │
  │  picks "Personal"       │                          │                       │                          │                     │
  │ ────────────────────────────────────────────────► │                       │                          │                     │
  │                         │                          │  active run? confirm  │                          │                     │
  │                         │                          │  prompt (existing)    │                          │                     │
  │                         │                          │ ◄────────────────────                            │                     │
  │                         │                          │                       │                          │                     │
  │                         │  onSwitchWorkspace(orgId)│                       │                          │                     │
  │                         │ ◄──────────────────────  │                       │                          │                     │
  │                         │                          │                       │                          │                     │
  │                         │  auth.switchWorkspace(orgId)                     │                          │                     │
  │                         │ ──────────────────────────────────────────────►  │                          │                     │
  │                         │                          │                       │  POST /v1/auth/sessions  │                     │
  │                         │                          │                       │  { workspace_id }        │                     │
  │                         │                          │                       │ ───────────────────────► │ ──────────────────► │
  │                         │                          │                       │                          │                     │ rotate session,
  │                         │                          │                       │                          │                     │ set new x-enterprise-org-id
  │                         │                          │                       │ ◄ 200 ─────────────────  │ ◄ 200 ────────────  │
  │                         │                          │                       │  reload (or replace      │                     │
  │                         │                          │                       │   to ?workspace=...)     │                     │
  │ ◄────────────────────────────────────────────────────────────────────────  │                          │                     │
  │                                                                                                                              │
  │  next request goes out with the new org's identity headers                                                                   │
```

If `POST /v1/auth/sessions` doesn't exist yet, the implementation falls back to `window.location.assign("?workspace=" + orgId)` and lets `<AuthGate>` re-discover the session. PR 2.2 §3.7 explicitly authorised this v1 path. We do not block on the auth team's session-rotation endpoint.

### 3.6 Sequence — `MessageSourcesStrip` data flow (G9)

```
worker emits source_ingested ×6 ──► applyCitationEvent ──► citationsRegistry.byRun
                                                          sourcesReducer.byConversation
                                              │
                                              │ (same event populates both, invariant test asserts agreement)
                                              ▼
worker emits final_response { citations: [...] } ──► run state moves to "terminal"
                                              │
                                              ▼
AssistantMessage re-renders
   useRunCitations(runId, { sealedOnly: true })
        │
        ▼
    runStatus === "completed" ?  citationsRegistry.byRun.get(runId).values()  :  []
        │
        ▼
    <MessageSourcesStrip citations={[...]} onSelect={paneController.openOn("sources", { focusCitationId })} />
        │
        ▼
    rendered chip-row beneath the assistant prose
```

`useRunCitations` is the **only new selector**. It's six lines: read the run's terminal state from the existing chatModel, slice the registry, return a memoised array. No new state, no new effect, no fetch.

### 3.7 Edge cases

| Case                                                                                                   | Behavior                                                                                                                                                                                                                                                            |
| ------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Workspace switch during streaming                                                                      | `WorkspacePicker` shows the existing confirm modal (PR 2.2 §3.7) → on confirm: `await onCancel()` → `await auth.switchWorkspace(orgId)`. Ordering matters: cancel **before** switch so the now-orphaned run gets `run_cancelled` from the originating org's worker. |
| Workspace switch with no `auth.switchWorkspace` available (older build)                                | `AuthContext` exposes the method unconditionally; the hard-nav fallback works regardless of backend support.                                                                                                                                                        |
| `MessageSourcesStrip` on a run that completed with **zero** citations                                  | Selector returns `[]`; component renders `null`. No empty box.                                                                                                                                                                                                      |
| `MessageSourcesStrip` on a partial run loaded from history                                             | The run's `final_response` is sealed if it terminated; if cancelled mid-stream, `sealedOnly` returns `[]` and the strip stays hidden. We **do not** show partial citations in the strip — that's the inline chips' job.                                             |
| Citation invariant test fails                                                                          | The PR that broke it is identified by the failing diff; CI red until reverted or both reducers are updated together.                                                                                                                                                |
| `useWorkspaceDefaults` called by a non-admin user → `save()` returns 403                               | Optimistic update rolls back; `error` is set; the component (PR 4.2) renders a read-only state.                                                                                                                                                                     |
| `reasoning.depth_label` present but model doesn't support reasoning                                    | Topbar's `ThinkingDepthControl` hides itself anyway (existing behavior — PR 2.1 §2.3.2). The label is a no-op.                                                                                                                                                      |
| Two reducers populated by a `source_ingested` whose `citation_id` exists in only one (data corruption) | Invariant test catches it in CI. In production, chip resolves to the placeholder (PR 1.1 behavior); SourcesTab row simply doesn't appear. No crash.                                                                                                                 |

### 3.8 Test plan

The test plan **is** most of this PR. Targeted matrix:

**Unit (frontend)**

- `useRunCitations.test.ts` — sealed-only selector returns `[]` mid-stream and the full set on terminal.
- `useWorkspaceDefaults.test.tsx` — fetch / save / 4xx rollback / loading state.
- `citationStore.invariant.test.ts` — both reducers receive byte-identical fields per `source_ingested`.
- `applyDepth.test.ts` (extend) — `depth_label` consumed when present.
- `Sidebar.test.tsx`, `UserCard.test.tsx`, `WorkspacePicker.test.tsx`, `CitationChip.css.test.tsx`, `AssistantMessage.test.tsx`, `UserMessage.test.tsx`, `ThreadWelcome.test.tsx` (extend with minute-tick), `streamingCursor.test.tsx`.

**Integration (frontend)**

- `Sidebar.integration.test.tsx` — `ChatScreen` mount → click workspace row → assert `auth.switchWorkspace` invoked.
- `AssistantMessage.integration.test.tsx` — `ChatScreen` with sealed-citations fixture → assert `MessageSourcesStrip` renders.

**Unit (ai-backend)**

- `tests/integration/runtime_api/test_sources_replay_parity.py` — live ingest of N citations → `replayRunEvents` + `GET /…/sources` produce the same N rows in the same order.

**Cross-service smoke**

- `make test` extends the existing happy-path use-case; the new replay-parity test runs as part of the ai-backend pytest suite.

**Manual smoke (CI gate)**

- `make dev`; create two workspaces; switch via UserCard; verify the next request's identity headers reflect the new org. (One operator check per release.)

### 3.9 Rollout

- **Flag-free.** All changes are additive at the FE wire-up layer + tests. No behavioural change for existing flows; the bug fixes light up new behaviours that are gated by user action (clicking a workspace row, opening a thread with sealed citations).
- **Backout.** Single revert restores prior behavior. The eleven gaps re-open. No data migration to undo.
- **Telemetry.** No new events; no new audit rows. The existing `auth.session.rotate` audit (if the rotation endpoint exists) covers G4. Existing `runtime_audit_log` covers everything else.

### 3.10 Open questions

1. **Session-rotation endpoint vs. hard-nav fallback** — does `POST /v1/auth/sessions` accept `{ workspace_id }` today? If yes, `switchWorkspace` uses it. If no, hard-nav. **This PR works either way**; the auth team can promote it to in-place rotation in a follow-up without touching the FE again.
2. **Should the strip also render mid-stream as rows arrive?** Decision in §2.2: no — UX shimmer outweighs the live-feel benefit, and inline chips already provide live feedback. Revisit if user testing requests it.
3. **Should `useWorkspaceDefaults` cache across mounts?** Decision: no — the panel mounts at most once per session (Settings is a route, not always-on). Single-fetch is simpler than a cache + invalidation.
4. **Do we ship the per-feature pytest split for PR 1.6 anyway?** Decision: no — the consolidated file passes coverage. Splitting now is busywork.

---

## 4 · Acceptance checklist

### G4 — Workspace switch

- [ ] `apps/frontend/src/features/auth/AuthContext.tsx` exports `switchWorkspace(orgId): Promise<void>` on the `AuthApi` interface.
- [ ] Implementation either calls the session-rotation endpoint or hard-navigates with `?workspace=<orgId>`; both paths covered by tests.
- [ ] `apps/frontend/src/features/chat/ChatScreen.tsx` passes `auth.switchWorkspace` as `onSwitchWorkspace` to `<AssistantThreadList>`.
- [ ] `Sidebar.integration.test.tsx` proves the click → `switchWorkspace(orgId)` chain.
- [ ] Active-run guard fires the existing confirm modal before invoking `switchWorkspace`.

### G9 — `MessageSourcesStrip` mount

- [ ] `apps/frontend/src/features/chat/components/messages/AssistantMessage.tsx` renders `<MessageSourcesStrip>` whenever its run has a sealed `final_response.citations`.
- [ ] `apps/frontend/src/features/chat/components/citations/citationsContext.tsx` exports `useRunCitations(runId, { sealedOnly })`.
- [ ] `AssistantMessage.integration.test.tsx` proves the mount + citation rendering.

### G10 — Citation registry amendment

- [ ] `apps/frontend/src/features/chat/chatModel/__tests__/citationStore.invariant.test.ts` asserts the two stores stay byte-identical on each `source_ingested`.
- [ ] `apps/frontend/src/features/chat/chatModel/README.md` (extend) documents the dual-store decision as an amendment to PR 3.1 §2.4.

### G3 — `reasoning.depth_label`

- [ ] `packages/api-types/src/index.ts` exports `ModelReasoningHints` with explicit `depth_label?: string`.
- [ ] `ModelCatalogModel.reasoning?: ModelReasoningHints | null`.
- [ ] `ThinkingDepthControl` reads `reasoning.depth_label` when present.
- [ ] `applyDepth.test.ts` extended with the depth-label case.

### G1 — `useWorkspaceDefaults`

- [ ] `apps/frontend/src/features/settings/useWorkspaceDefaults.ts` ships with `{ defaults, loading, error, save }`.
- [ ] `apps/frontend/src/api/agentApi.ts` adds `getWorkspaceDefaults`, `putWorkspaceDefaults`.
- [ ] `useWorkspaceDefaults.test.tsx` covers fetch / save / optimistic rollback / 4xx error.
- [ ] No Settings UI panel ships in this PR (PR 4.2's scope).

### G5 / G6 / G7 / G8 / G11 — Test backfill

- [ ] `Sidebar.test.tsx`, `UserCard.test.tsx`, `WorkspacePicker.test.tsx` ship.
- [ ] `AssistantMessage.test.tsx`, `UserMessage.test.tsx` ship; CSS-class assertions cover the flush-left / right-bubble contract.
- [ ] `ThreadWelcome.test.tsx` extended with the minute-tick fake-timer transition.
- [ ] `streamingCursor.test.tsx` covers cursor pseudo-element + reduce-motion off.
- [ ] `CitationChip.css.test.tsx` covers dim default / accent on hover / `data-connector` attribute / focus ring.
- [ ] `services/ai-backend/tests/integration/runtime_api/test_sources_replay_parity.py` ships.

### G2 — Documentation

- [ ] `services/ai-backend/tests/unit/runtime_api/test_workspace_defaults_lifecycle.py` carries a top-of-file comment enumerating the originally-named cases for grep-ability.

### Global

- [ ] No new event types in `services/ai-backend/src/runtime_api/schemas/events.py`. `RuntimeEventEnvelope` byte-identical pre/post merge.
- [ ] No new endpoints in `services/backend-facade`. Route table unchanged.
- [ ] No new migrations. `services/ai-backend/migrations/` lock unchanged.
- [ ] No new npm packages in `apps/frontend/package.json`.
- [ ] `npm run typecheck --workspace @enterprise-search/frontend` green.
- [ ] `npm run typecheck --workspace @enterprise-search/api-types` green.
- [ ] `npm run build --workspace @enterprise-search/frontend` green.
- [ ] `make test` green.

---

## 5 · References

- [`/Users/parthpahwa/.claude/plans/fetch-this-design-file-resilient-pumpkin.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md) — overall plan; this PR closes the W1–W3 gaps before W4 begins.
- [`docs/new-design/pr-1.6-workspace-defaults-conversation-lifecycle.md`](pr-1.6-workspace-defaults-conversation-lifecycle.md) — provides `WorkspaceDefaultsResponse`, `UpdateWorkspaceDefaultsRequest`; the hook in §2.5 is its FE consumer.
- [`docs/new-design/pr-2.1-topbar-chrome-thinking-depth.md`](pr-2.1-topbar-chrome-thinking-depth.md) — `applyDepth`, `ThinkingDepthControl` consume `reasoning.depth_label` after this PR.
- [`docs/new-design/pr-2.2-sidebar-user-card-keymap.md`](pr-2.2-sidebar-user-card-keymap.md) — §3.7 hard-nav fallback for workspace switching, used in §2.1 here; UserCard / WorkspacePicker / Sidebar prop chain unchanged.
- [`docs/new-design/pr-2.3-welcome-state-thread-polish.md`](pr-2.3-welcome-state-thread-polish.md) — minute-tick transition specced; we add the test.
- [`docs/new-design/pr-3.1-citation-chips-sources-tab.md`](pr-3.1-citation-chips-sources-tab.md) — §2.4 dual-store discussion; this PR amends with the invariant test.
- [`docs/new-design/01-citations-live-registry.md`](01-citations-live-registry.md) — `final_response.citations` sealing; `useRunCitations` reads from it.
- [`apps/frontend/src/features/chat/components/messages/MessageSourcesStrip.tsx`](../../apps/frontend/src/features/chat/components/messages/MessageSourcesStrip.tsx) — exists; mounted here.
- [`apps/frontend/src/features/chat/components/sidebar/UserCard.tsx`](../../apps/frontend/src/features/chat/components/sidebar/UserCard.tsx) — already accepts `onSwitchWorkspace`; we wire its source.
- [`apps/frontend/src/features/auth/AuthContext.tsx`](../../apps/frontend/src/features/auth/AuthContext.tsx) — extended with `switchWorkspace`.
- [`packages/api-types/src/index.ts`](../../packages/api-types/src/index.ts) — `ModelReasoningHints` added; `ModelCatalogModel.reasoning` tightened.
- [Vitest · `vi.useFakeTimers`](https://vitest.dev/api/vi.html#vi-usefaketimers) — minute-tick test pattern, reused.
- [`@testing-library/jest-dom` · `toHaveAttribute` / `toHaveStyle`](https://github.com/testing-library/jest-dom) — visual contract assertions, already in use.
- WAI-ARIA `aria-current` (existing PR 2.2 reference) — sidebar row state asserted by `Sidebar.test.tsx`.

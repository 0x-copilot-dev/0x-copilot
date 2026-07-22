# Web Parity Audit — legacy `ChatScreen` vs shared `RunDestination` cockpit

**Status:** Gate result for Phase 8 (retire legacy web chat). Produced 2026-07-22.
**Verdict: NOT AT PARITY — do NOT flip `runCockpitWeb` ON, do NOT retire `ChatScreen`.**

## Why this exists

Design decision **D4** gated retiring the web default chat (`apps/frontend/src/features/chat/ChatScreen.tsx`) on a signed-off parity audit + bake. This is that audit: does the shared `RunDestination` cockpit (mounted on web via `features/run/RunRoute.tsx` when `runCockpitWeb` is ON) cover everything `ChatScreen` does? It does not.

## Headline blocker

**On the web cockpit you can send exactly one message per conversation.** `RunRoute` mounts `RunDestination` with `renderEmptyComposer` but **no `renderComposer`** — and unlike desktop, **there is no web `RunComposer`** (it's desktop-only, `apps/desktop/renderer/composer/RunComposer.tsx`). So `TcChat`'s in-chat slot falls back to a bare `<Composer>` with no `onSend`: turn 1 goes through the empty-state composer, turn 2+ is inert. (Desktop does not have this problem because its binder wires `renderComposer` → `RunComposer` — that's exactly what Phase 5a fixed _on desktop_.)

## MUST-FIX before the flag can flip / `ChatScreen` can retire

1. **In-chat (turn-N) composer inert on web (keystone).** Build a web `RunComposer` binder (mirror the desktop one) and pass it as `renderComposer` from `features/run/RunRoute.tsx`. The receiving seams already exist (`RunDestination.renderComposerWithDispatch`, `TcChat.renderComposer`).
2. **Conversation reopen/switch.** Thread the conversation id: `App.tsx` `openRun` currently drops it (App.tsx:814-816), and `RunRoute` must accept it and bind it. _(Partially addressed: `RunRoute` now accepts a `conversationId` prop + removed the racy self-create; the `App.openRun` id-threading remains.)_
3. **New chat.** A "create a fresh conversation" affordance/path. _(Partially addressed: `RunRoute` now creates a conversation lazily on first send via ensure-on-run.)_
4. **Cancel / Stop a run.** Wire a cancel affordance in `RunDestination` (`POST /v1/agent/runs/{id}/cancel`) surfaced through the composer.
5. **MCP OAuth mid-run resume + connector discovery/install.** Port `ChatScreen`'s `restoreRunAfterOAuth` / `mcpAuthAction` flow to a cockpit host binder + `approvalProjection.ts`. Today a run needing connector auth dead-ends.
6. **Optimistic user-message echo on send.** Emit the user turn locally in `useRunTranscript.ts` / `chatProjection.ts` instead of waiting for the run-start `/messages` re-seed.

## Strong should-fix (parity, arguably scope-gated)

Citation chips in-chat (thread `markdownComponents` into `TcChat`) + Sources tab tool-citation merge; run-phase pulse (Working…/Thinking…/Writing…) into `RunHeader`; composer feature parity in the web `RunComposer` (skills `/`-menu, connectors popover + per-chat scopes, thinking-depth, web-search toggle, dictation, in-chat attachments/model picker); reload/regenerate; edit-message + branch; approval forward-to-user; approval undo; rename title; share; usage panel.

## Not blocking / different by design

- Cross-conversation **background-run manager** (`useBackgroundChatStreams`) — the cockpit is single-conversation; there's no in-cockpit switch on web, so it's moot, but the multi-chat live-pulse capability is `ChatScreen`-only.
- **Drafts / Skills** rail tabs — intentionally excluded (FR-3.11).
- **Delete/archive/pin** — moved to `ChatsArchiveRoute` by design.

## What the cockpit does BETTER than `ChatScreen`

Studio/Focus mode, timeline scrub, surface tabs + follow-live, the multi-run selector, and approve-with-edits / edit-on-surface — all new capabilities `ChatScreen` lacks. These are the reason to converge eventually; they are not a reason to retire `ChatScreen` before the MUST-FIX list lands.

## Recommendation

Keep `runCockpitWeb` OFF and keep `ChatScreen` as the web default. Treat MUST-FIX 1–6 as a scoped web-convergence program (keystone = the web `RunComposer` binder). Only after those land + a bake period should the flag flip and `ChatScreen` retire. The desktop is close to parity precisely because its binder already wires the composer + dispatch + deep-links; web's `RunRoute` is a thin binder that still needs them.

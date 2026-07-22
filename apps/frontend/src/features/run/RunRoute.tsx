// RunRoute — web host binder for the Run cockpit (PRD-05).
//
// The flagship `RunDestination` cockpit is a pure composition shell in
// `@0x-copilot/chat-surface` (useRunSession + useRunMode + ThreadCanvas). This
// route is the WEB substrate binder for it, mounted under the `run` slug when
// the `runCockpitWeb` flag is on (App.tsx dispatch). It mirrors the desktop
// binder — `apps/desktop/renderer/destinationBinders.tsx` `RunBinder` — which we
// deliberately cannot import (`apps/* → apps/*` is a hard boundary); the shared
// home for this projection is the package component's own contract, so the two
// binders duplicate the same pure logic over `@0x-copilot/api-types` shapes.
//
// What the binder resolves before mounting the cockpit:
//   1. A real `conversationId` — the cockpit's run list, transcript, and run
//      creation are all keyed on it. Reuse the most-recent conversation, else
//      create a fresh "Web session" (identical to the desktop binder).
//   2. Model readiness — a BYOK provider key OR a running local model. When
//      neither exists the empty-state composer shows a "Set up your model" CTA
//      instead of firing a run guaranteed to fail with a configuration error.
//   3. `onStartRun` — POST /v1/agent/runs (identity is derived from the verified
//      session/bearer server-side; the client sends only conversation + goal).
//
// Boundary: components + `useTransport` from `@0x-copilot/chat-surface`, wire
// types from `@0x-copilot/api-types`. No `apps/*` import, and no direct
// transport singleton / `@0x-copilot/chat-transport` import — both are banned in
// `src/features/**` by eslint. The ambient `useTransport()` port that ChatShell
// provides (WebTransport → facade) is the sanctioned substrate access, exactly
// as the desktop binder reads its IPC-backed port.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactElement,
} from "react";

import {
  RunDestination,
  buildRunCreateBody,
  useTransport,
  type RunEmptyComposerCtx,
  type RunStartRequest,
} from "@0x-copilot/chat-surface";
import type { ConversationId } from "@0x-copilot/api-types";

import type { RequestIdentity } from "../../api/config";
import { RunComposer } from "./RunComposer";
import { RunEmptyComposer } from "./RunEmptyComposer";

// The shared FTUE / onboarding-composer styles (hero · starter chips ·
// composer). The empty-state composer reuses these `.fr-*` classes; import them
// here so the run chunk carries them even without the onboarding chunk loaded.
import "@0x-copilot/chat-surface/src/onboarding/onboarding.css";

export interface RunRouteProps {
  /**
   * The conversation to reopen (Chats → Run), or `null`/absent for a NEW chat
   * (created lazily on the first send). Host-supplied so the id lives in the web
   * app's nav (mirrors the desktop outlet's `conversationId`); when the host
   * threads a new id, the cockpit re-keys onto it.
   */
  readonly conversationId?: ConversationId | null;
  /**
   * Open Settings → Provider keys. Threaded to the cockpit's empty-state
   * composer for the "Set up your model" CTA and the `configuration_error`
   * "Add a provider key" CTA. Host-owned so the substrate-agnostic package
   * never navigates directly.
   */
  readonly onOpenModelSettings?: () => void;
  /** Signed-in identity — threaded to the empty composer's live model catalog. */
  readonly identity: RequestIdentity;
}

export function RunRoute({
  conversationId: propConversationId = null,
  onOpenModelSettings,
  identity,
}: RunRouteProps): ReactElement {
  const transport = useTransport();
  const [conversationId, setConversationId] = useState<ConversationId | null>(
    propConversationId,
  );
  // Reopen: follow the host-supplied conversation id when it changes. A new chat
  // created lazily below keeps propConversationId null, so this never clobbers it.
  useEffect(() => {
    setConversationId(propConversationId);
  }, [propConversationId]);
  // Readiness gate (Issue 1 parity): default true (fail-open) so a configured
  // user never flashes the setup CTA on load; flip to false only once the probe
  // CONFIRMS neither a BYOK key nor a running local model exists. A probe error
  // also fails open — the run-start error surfacing is the backstop.
  const [modelReady, setModelReady] = useState(true);

  // A new chat has NO conversation until the first send creates it lazily (via
  // ensure-conversation-on-run) — no mount-time create, so no duplicate-conversation
  // race (Phase 5b). The idempotency key is minted once per new-chat intent so a
  // double-tap collapses to a single conversation row server-side.
  const newChatKeyRef = useRef<string | null>(null);

  // Model readiness probe (mirrors the desktop RunBinder): a BYOK provider key
  // OR a running local model with at least one pulled model counts as ready.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      let hasProviderKey = false;
      try {
        const res = await transport.request<{
          readonly keys?: readonly unknown[];
        }>({ method: "GET", path: "/v1/settings/provider-keys" });
        hasProviderKey = (res.keys?.length ?? 0) > 0;
      } catch {
        // Can't tell → don't hard-block. Leave ready=true (fail-open).
        return;
      }
      if (hasProviderKey) {
        if (!cancelled) setModelReady(true);
        return;
      }
      // No cloud key — a running local model with at least one pulled model
      // counts as ready. Local models are optional/server-gated, so any error
      // (disabled → 404) simply means "no local option", not "not ready".
      let hasLocalModel = false;
      try {
        const status = await transport.request<{
          readonly enabled?: boolean;
          readonly ollama_running?: boolean;
        }>({ method: "GET", path: "/v1/local-models/status" });
        if (status.enabled === true && status.ollama_running === true) {
          const listed = await transport.request<{
            readonly models?: readonly unknown[];
          }>({ method: "GET", path: "/v1/local-models" });
          hasLocalModel = (listed.models?.length ?? 0) > 0;
        }
      } catch {
        /* local models unavailable — not a readiness signal */
      }
      if (!cancelled) setModelReady(hasProviderKey || hasLocalModel);
    })();
    return () => {
      cancelled = true;
    };
  }, [transport]);

  // Start a run from the empty-state composer. Host owns run creation; one body
  // builder (shared with the shell default + desktop binder) turns a bare
  // `{ goal }` into "conversation + goal only" and the rich composer's selection
  // (model / attachments / web-search) into the full body. Identity comes from
  // the verified bearer server-side, never sent by the client.
  const handleStartRun = useCallback(
    async (request: RunStartRequest): Promise<string | null> => {
      // Existing conversation → POST a run against it.
      if (conversationId !== null) {
        const run = await transport.request<{ readonly run_id?: string }>({
          method: "POST",
          path: "/v1/agent/runs",
          body: buildRunCreateBody(conversationId, request),
        });
        return run.run_id ?? null;
      }
      // New chat → create the conversation AND start the run in one server-side
      // transaction (ensure-conversation-on-run): drop `conversation_id`, carry a
      // stable idempotency key, read back both ids, and bind the created
      // conversation so the cockpit re-keys onto it (head resolution binds the run).
      if (newChatKeyRef.current === null) {
        newChatKeyRef.current = mintNewChatIdempotencyKey();
      }
      const body = buildRunCreateBody("new" as ConversationId, request);
      delete body.conversation_id;
      body.conversation_idempotency_key = newChatKeyRef.current;
      const run = await transport.request<{
        readonly run_id?: string;
        readonly conversation_id?: string;
      }>({
        method: "POST",
        path: "/v1/agent/runs",
        body,
      });
      const createdId = run.conversation_id;
      if (typeof createdId === "string" && createdId !== "") {
        setConversationId(createdId as ConversationId);
      }
      return run.run_id ?? null;
    },
    [transport, conversationId],
  );

  // Empty-state composer (FR-3.25): the design's "What should we run first?"
  // rich composer, mounted when there is no active run. Send binds the fresh
  // run live (no shell remount).
  const renderEmptyComposer = useCallback(
    (ctx: RunEmptyComposerCtx) => (
      <RunEmptyComposer ctx={ctx} identity={identity} />
    ),
    [identity],
  );

  // P1 keystone: the in-chat (turn-N) composer. The cockpit injects `dispatch`
  // (start run + bind session) into the ctx; RunComposer routes send through it,
  // so a 2nd message streams exactly like the first (no more inert turn-N).
  const renderComposer = useCallback(
    (ctx: {
      readonly disabled: boolean;
      readonly placeholder: string;
      readonly dispatch: (request: RunStartRequest) => Promise<void>;
    }) => (
      <RunComposer
        ctx={ctx}
        identity={identity}
        onOpenModelSettings={onOpenModelSettings}
      />
    ),
    [identity, onOpenModelSettings],
  );

  // A new chat (no conversation yet) mounts the cockpit against a "new" sentinel:
  // its head/transcript GETs 404 harmlessly (head resolution is best-effort) and
  // the empty composer shows until the first send creates the real conversation.
  // Keying by the conversation id means the lazy create cleanly REMOUNTS the
  // cockpit onto the new conversation, where head resolution binds the fresh run.
  const boundConversationId: ConversationId =
    conversationId ?? ("new" as ConversationId);

  // Full-bleed: the `run` slug owns full height in ChatShell (no topbar /
  // context / right rail). RunDestination is itself height:100%.
  return (
    <section
      aria-label="Run destination"
      data-testid="run-route"
      style={{ height: "100%", width: "100%", minHeight: 0 }}
    >
      <RunDestination
        key={boundConversationId}
        conversationId={boundConversationId}
        onStartRun={handleStartRun}
        modelReady={modelReady}
        onOpenModelSettings={onOpenModelSettings}
        renderComposer={renderComposer}
        renderEmptyComposer={renderEmptyComposer}
      />
    </section>
  );
}

// Mint a stable idempotency key for a new-chat first send (mirrors the desktop
// binder). The server collapses concurrent/retried first sends with the same key
// to a single conversation row.
function mintNewChatIdempotencyKey(): string {
  const c = globalThis.crypto;
  if (c !== undefined && typeof c.randomUUID === "function") {
    return c.randomUUID();
  }
  // Fallback (test/JS env without Web Crypto). Uniqueness within one session is
  // sufficient for the server-side idempotency collapse.
  return `new-chat-${Date.now().toString(36)}-${Math.floor(Math.random() * 1e6).toString(36)}`;
}

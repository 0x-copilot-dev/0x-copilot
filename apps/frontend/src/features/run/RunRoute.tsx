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

import { useCallback, useEffect, useState, type ReactElement } from "react";

import {
  RunDestination,
  buildRunCreateBody,
  useTransport,
  type RunEmptyComposerCtx,
  type RunStartRequest,
} from "@0x-copilot/chat-surface";
import type {
  ConversationId,
  ConversationListResponse,
} from "@0x-copilot/api-types";

import type { RequestIdentity } from "../../api/config";
import { RunEmptyComposer } from "./RunEmptyComposer";

// The shared FTUE / onboarding-composer styles (hero · starter chips ·
// composer). The empty-state composer reuses these `.fr-*` classes; import them
// here so the run chunk carries them even without the onboarding chunk loaded.
import "@0x-copilot/chat-surface/src/onboarding/onboarding.css";

export interface RunRouteProps {
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
  onOpenModelSettings,
  identity,
}: RunRouteProps): ReactElement {
  const transport = useTransport();
  const [conversationId, setConversationId] = useState<ConversationId | null>(
    null,
  );
  // Readiness gate (Issue 1 parity): default true (fail-open) so a configured
  // user never flashes the setup CTA on load; flip to false only once the probe
  // CONFIRMS neither a BYOK key nor a running local model exists. A probe error
  // also fails open — the run-start error surfacing is the backstop.
  const [modelReady, setModelReady] = useState(true);

  // Resolve a real conversation to bind the cockpit to: reuse the most-recent,
  // else create a fresh "Web session". Mirrors the desktop RunBinder.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const list = await transport.request<ConversationListResponse>({
          method: "GET",
          path: "/v1/agent/conversations",
          query: { limit: 1 },
        });
        const existing = list.conversations?.[0]?.conversation_id;
        const resolved =
          existing ??
          (
            await transport.request<{ readonly conversation_id: string }>({
              method: "POST",
              path: "/v1/agent/conversations",
              body: { title: "Web session" },
            })
          ).conversation_id;
        if (!cancelled) {
          setConversationId(resolved as ConversationId);
        }
      } catch {
        // Leave `conversationId` null → the loading placeholder stays. Starting
        // a run requires a real conversation, so we never mount the cockpit
        // against a fabricated id; navigating away and back retries.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [transport]);

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
      if (conversationId === null) {
        return null;
      }
      const run = await transport.request<{ readonly run_id?: string }>({
        method: "POST",
        path: "/v1/agent/runs",
        body: buildRunCreateBody(conversationId, request),
      });
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

  // Full-bleed: the `run` slug owns full height in ChatShell (no topbar /
  // context / right rail). RunDestination is itself height:100%.
  if (conversationId === null) {
    return (
      <section
        aria-label="Run destination"
        data-testid="run-route-loading"
        style={{ height: "100%", width: "100%", minHeight: 0 }}
      />
    );
  }

  return (
    <section
      aria-label="Run destination"
      data-testid="run-route"
      style={{ height: "100%", width: "100%", minHeight: 0 }}
    >
      <RunDestination
        conversationId={conversationId}
        onStartRun={handleStartRun}
        modelReady={modelReady}
        onOpenModelSettings={onOpenModelSettings}
        renderEmptyComposer={renderEmptyComposer}
      />
    </section>
  );
}

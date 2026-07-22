// RunComposer — the web Run cockpit's IN-CHAT (turn-N) composer (PRD web-convergence P1, keystone).
//
// The web cockpit previously mounted RunDestination with only `renderEmptyComposer`,
// so a SECOND in-chat message had no composer with a live send path — turn-N was
// inert. This binder fills the `renderComposer` slot: it mounts the SAME shared
// `AssistantComposer` (model pill · attach · inline Tools popover · send) the web
// empty composer uses, and routes send through the cockpit's injected
// `ctx.dispatch` — which starts the run AND binds the live session (one bind sink,
// ensure-conversation-on-run) — NEVER its own POST. So turn-1 and turn-N share the
// exact same path.
//
// Mirrors the desktop `apps/desktop/renderer/composer/RunComposer.tsx` (the two
// hosts can't share code — `apps/* → apps/*` is banned — so they duplicate the
// wiring over the shared component contract). All composer data (model catalog /
// tools / connectors / attachments) comes from `useWebRunComposerTools`, the ONE
// source of truth shared with `RunEmptyComposer` so the two web composers never
// diverge (PRD AD-3). Boundary: no `@0x-copilot/chat-surface` internals, no
// `apps/desktop` import, no raw fetch — the run is dispatched through the injected
// cockpit seam.

import {
  useCallback,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import {
  AssistantComposer,
  parseTransportError,
  type RunStartRequest,
  type StartRunError,
} from "@0x-copilot/chat-surface";

import type { RequestIdentity } from "../../api/config";
import { toReadableRunAttachments } from "../onboarding/firstRunAttachments";
import {
  createOnboardingChatSurfaceAttachmentAdapter,
  mcpServerInstructionPrompt,
  onboardingFilePicker,
  renderOnboardingPlusMenu,
  skillInstructionPrompt,
} from "../onboarding/onboardingComposerAdapter";
import { useWebRunComposerTools } from "./useWebRunComposerTools";

// Substrate-bound singleton — one bridged onboarding attachment adapter for the
// in-chat composer (mirrors RunEmptyComposer + the desktop composer's singleton).
const attachmentAdapter = createOnboardingChatSurfaceAttachmentAdapter();

/** No-op for the composer's Settings deep-links the run cockpit surfaces elsewhere. */
function noop(): void {
  /* intentional no-op */
}

/**
 * Context handed down by the Run cockpit through the `renderComposer` seam
 * (`RunDestination` → `TcChat.renderComposer`). `dispatch` is the cockpit's ONE
 * dispatch: it starts the run and binds the live session, returning a promise a
 * rejection of which routes to this composer's own error notice.
 */
export interface RunComposerCtx {
  readonly disabled: boolean;
  readonly placeholder: string;
  readonly dispatch: (request: RunStartRequest) => Promise<void>;
  /** WC-P3 — true while the bound run is cancellable; the composer shows Stop. */
  readonly running: boolean;
  /** WC-P3 — cancel the bound run (cockpit-owned best-effort POST). */
  readonly onCancel: () => void;
}

export interface RunComposerProps {
  readonly ctx: RunComposerCtx;
  /** Signed-in identity — threaded to the live model catalog. */
  readonly identity: RequestIdentity;
  /** Open Settings → Provider keys (the model-pill / config-error "Add a key" CTA). */
  readonly onOpenModelSettings?: () => void;
}

export function RunComposer({
  ctx,
  identity,
  onOpenModelSettings,
}: RunComposerProps): ReactElement {
  const {
    models,
    selectedModel,
    onModelChange,
    providerKeysPort,
    toolsTrigger,
    buildRunStartRequest,
  } = useWebRunComposerTools(identity);

  // The last run-create failure, surfaced inline above the composer. A rejected
  // `ctx.dispatch` propagates to `onSubmitError` (the single chat-surface error
  // channel) rather than being caught here; success clears any prior notice.
  const [startError, setStartError] = useState<StartRunError | null>(null);

  const handleSubmit = useCallback(
    async ({
      text,
      attachments,
    }: {
      text: string;
      attachments: ReadonlyArray<unknown>;
    }): Promise<void> => {
      if (ctx.disabled) return;
      const trimmed = text.trim();
      const runAttachments = toReadableRunAttachments(attachments);
      if (trimmed === "" && runAttachments.length === 0) return;
      // Route through the cockpit's ONE dispatch (start run + bind session), never
      // a local POST — so this 2nd/Nth message streams exactly like the first.
      await ctx.dispatch(buildRunStartRequest({ text, attachments }));
      setStartError(null);
    },
    [ctx, buildRunStartRequest],
  );

  const handleSubmitError = useCallback((err: unknown): void => {
    const parsed = parseTransportError(err);
    setStartError({
      message:
        parsed.safeMessage ??
        "Couldn't start the run. Is the backend running and a model configured?",
      code: parsed.code,
      correlationId: parsed.correlationId,
      raw: parsed.raw !== "" ? parsed.raw : undefined,
    });
  }, []);

  return (
    <div data-testid="run-composer" style={rootStyle}>
      {/* A rejected run-create surfaces here (the in-chat composer's own notice,
          mirroring the desktop RunComposer) — never swallowed. A configuration
          error deep-links to Provider keys. */}
      {startError !== null ? (
        <div role="alert" data-testid="run-composer-error" style={noticeStyle}>
          <span style={noticeMessageStyle}>{startError.message}</span>
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => setStartError(null)}
            style={noticeDismissStyle}
          >
            ×
          </button>
          {startError.code === "configuration_error" &&
          onOpenModelSettings !== undefined ? (
            <button
              type="button"
              data-testid="run-composer-error-cta"
              onClick={onOpenModelSettings}
              style={noticeCtaStyle}
            >
              Add a provider key
            </button>
          ) : null}
        </div>
      ) : null}
      <AssistantComposer
        connectors={{ servers: [], loading: false }}
        skills={{ skills: [], loading: false }}
        attachmentAdapter={attachmentAdapter}
        filePicker={onboardingFilePicker}
        renderPlusMenu={renderOnboardingPlusMenu}
        skillInstructionPrompt={skillInstructionPrompt}
        mcpServerInstructionPrompt={mcpServerInstructionPrompt}
        onShowConnectors={noop}
        onOpenSkillsSettings={noop}
        onOpenMcpSettings={noop}
        toolsTrigger={toolsTrigger}
        models={models}
        selectedModel={selectedModel}
        onModelChange={onModelChange}
        providerKeysPort={providerKeysPort}
        // The model picker is a model list (Cursor/Claude shape), not a depth grid.
        depthVisible={false}
        onSubmit={handleSubmit}
        onSubmitError={handleSubmitError}
        // The cockpit owns the ghost/scrub disable + placeholder (passed in via ctx).
        disabled={ctx.disabled}
        placeholder={ctx.placeholder}
        // WC-P3: the cockpit derives run state + owns cancel; the composer just
        // swaps send↔Stop and fires onCancel (AssistantComposer renders Stop).
        running={ctx.running}
        onCancel={ctx.onCancel}
      />
    </div>
  );
}

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  minWidth: 0,
};

const noticeStyle: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: 8,
  padding: "8px 12px",
  borderRadius: 10,
  background: "var(--color-danger-soft, rgba(229,103,138,.12))",
  border: "1px solid var(--color-danger, #e5678a)",
};

const noticeMessageStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
  fontSize: "var(--font-size-xs, 12px)",
  lineHeight: 1.45,
  color: "var(--color-danger, #e5678a)",
};

const noticeDismissStyle: CSSProperties = {
  flexShrink: 0,
  background: "transparent",
  border: "none",
  color: "var(--color-text-subtle, #7e7e84)",
  fontSize: "var(--font-size-sm, 13px)",
  lineHeight: 1,
  cursor: "pointer",
  fontFamily: "inherit",
  padding: 0,
};

const noticeCtaStyle: CSSProperties = {
  flexShrink: 0,
  background: "var(--color-accent, #5fb2ec)",
  color: "var(--color-accent-contrast, #08131d)",
  border: "1px solid var(--color-accent, #5fb2ec)",
  borderRadius: 8,
  padding: "4px 10px",
  fontSize: "var(--font-size-xs, 12px)",
  fontWeight: 600,
  cursor: "pointer",
  fontFamily: "inherit",
};

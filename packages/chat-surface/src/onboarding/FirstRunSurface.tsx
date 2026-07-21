// FirstRunSurface — the shared 3-state FTUE gate surface (SPEC · PRD-P1 §3).
//
// Presentational SSOT mounted by BOTH hosts (desktop `FirstRunGate` binder, web
// `FirstRunRoute` binder) at the post–sign-in seam. It owns the state machine
//   stage ∈ {choice, dl, ready} + `sent`
// and the persistent chrome (top bar + footer). It performs NO I/O: BYOK save
// goes through the injected `ProviderKeysPort`; skip/complete are host
// callbacks; the local-download body (P2) and the real composer/ack (P3) are
// injected SLOTS. P1 ships internal placeholders so the machine + tests are
// complete without P2/P3.
//
// SLOT CONTRACT (consumed by P2/P3 — keep stable):
//   • renderLocalCard(ctx: FirstRunLocalCardCtx)  — P2 replaces the Gate's
//        local `.fr-gcard` (curated preset + in-gate SSE progress).
//   • renderComposer(ctx: FirstRunComposerCtx)    — P3 mounts AssistantComposer
//        for the `dl`/`ready` body. `ctx.modelReady` is the shared model-ready
//        signal (key → always true; local → localModelPct === 100). `onSent`
//        flips the surface to the acknowledgment.
//   • renderAcknowledgment(ctx: FirstRunAckCtx)   — P3's State C. `ctx.onComplete`
//        is the bound handoff (host markComplete + navigate); the slot decides
//        the ~1.5s timing. P1's placeholder fires it once on mount.
//
// Substrate-agnostic; colors resolve to design-system tokens (`onboarding.css`).

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import { Button } from "@0x-copilot/design-system";

import { BrandMark } from "../shell/BrandMark";
import type { ProviderKeysPort } from "../settings/data/providerKeys";
import type { ModelsPort } from "../settings/data/models";
import { Gate, type FirstRunLocalCardCtx } from "./Gate";
import type { KeyFormConnected } from "./KeyForm";
import {
  FIRST_RUN_COPY,
  type FirstRunEngine,
  type FirstRunKeyProvider,
  type FirstRunStage,
} from "./firstRun";

// ---------------------------------------------------------------------------
// Slot context types (P3 fills; P1 ships placeholders)
// ---------------------------------------------------------------------------

export type { FirstRunLocalCardCtx } from "./Gate";

export interface FirstRunComposerCtx {
  /** `dl` while the local model downloads; `ready` once an engine is usable. */
  readonly stage: Exclude<FirstRunStage, "choice">;
  readonly engine: FirstRunEngine;
  readonly models?: ModelsPort;
  /** P2 progress feed; drives the "Qwen 3 4B · N%" model pill. */
  readonly localModelPct: number | null;
  /** Shared model-ready signal: key → true; local → localModelPct === 100. */
  readonly modelReady: boolean;
  /** The composer calls this after run-create → surface renders the ack. */
  readonly onSent: () => void;
}

export interface FirstRunAckCtx {
  readonly engine: FirstRunEngine;
  readonly modelReady: boolean;
  /** Bound handoff — host markComplete + navigate. The slot owns the timing. */
  readonly onComplete: () => void;
}

export interface FirstRunSurfaceProps {
  /** BYOK seam (required). */
  readonly providerKeys: ProviderKeysPort;
  /** /v1/agent/models catalog — NEVER a hardcoded model list. P3 uses it. */
  readonly models?: ModelsPort;
  /** Top-bar skip (host: markComplete("skip") + navigate to workspace). */
  readonly onSkip: () => void;
  /** Handoff (P3 does run-create first; P1 host = markComplete + navigate). */
  readonly onComplete: (engine: FirstRunEngine) => void;
  /** P4 fills the wallet chip; P1 renders whatever the host injects (or nothing). */
  readonly walletChipSlot?: ReactNode;
  /** Footer left; default `FIRST_RUN_COPY.footer.left`. */
  readonly appVersion?: string;
  readonly keyProviders?: readonly FirstRunKeyProvider[];
  // --- Deferred-phase seams (optional; P1 ships internal placeholders) ---
  /** P2: fired when the user starts the local download (→ stage=dl). */
  readonly onStartLocalDownload?: () => void;
  /** P2: local download progress 0–100 (null before/without P2). */
  readonly localModelPct?: number | null;
  /** P2: replaces the Gate's local card. */
  readonly renderLocalCard?: (ctx: FirstRunLocalCardCtx) => ReactNode;
  /** P3: the `dl`/`ready` composer body. */
  readonly renderComposer?: (ctx: FirstRunComposerCtx) => ReactNode;
  /** P3: State C acknowledgment. */
  readonly renderAcknowledgment?: (ctx: FirstRunAckCtx) => ReactNode;
  /** Tests only — seed the initial stage. */
  readonly initialStage?: FirstRunStage;
  /** P1 may disable the local download until P2's default preset lands. */
  readonly localDownloadDisabled?: boolean;
}

// ---------------------------------------------------------------------------
// P1 placeholders — replaced by P3's real composer / acknowledgment slots.
// ---------------------------------------------------------------------------

function ComposerPlaceholder({
  ctx,
}: {
  readonly ctx: FirstRunComposerCtx;
}): ReactElement {
  return (
    <div className="fr-slot" data-testid="first-run-composer-placeholder">
      <p className="fr-slot__note">
        {ctx.stage === "dl"
          ? "Your model is downloading — the composer lands in P3."
          : "Model ready — the composer lands in P3."}
      </p>
      <Button
        type="button"
        variant="primary"
        size="sm"
        onClick={ctx.onSent}
        data-testid="first-run-placeholder-send"
      >
        Continue
      </Button>
    </div>
  );
}

function AckPlaceholder({
  ctx,
}: {
  readonly ctx: FirstRunAckCtx;
}): ReactElement {
  // P1 hands off immediately (one-shot); P3 owns the real ack + ~1.5s timing.
  const { onComplete } = ctx;
  useEffect(() => {
    onComplete();
  }, [onComplete]);
  return (
    <div className="fr-slot" data-testid="first-run-ack-placeholder">
      <p className="fr-slot__note">
        {ctx.modelReady
          ? "Starting your first run"
          : "Queued — starts when the model lands"}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shell + state machine
// ---------------------------------------------------------------------------

export function FirstRunSurface({
  providerKeys,
  models,
  onSkip,
  onComplete,
  walletChipSlot,
  appVersion,
  keyProviders,
  onStartLocalDownload,
  localModelPct = null,
  renderLocalCard,
  renderComposer,
  renderAcknowledgment,
  initialStage = "choice",
  localDownloadDisabled = false,
}: FirstRunSurfaceProps): ReactElement {
  const [stage, setStage] = useState<FirstRunStage>(initialStage);
  const [engine, setEngine] = useState<FirstRunEngine>(null);
  const [sent, setSent] = useState(false);

  const handleStartDownload = useCallback(() => {
    setEngine({ kind: "local", modelId: null });
    setStage("dl");
    onStartLocalDownload?.();
  }, [onStartLocalDownload]);

  const handleKeyConnected = useCallback((r: KeyFormConnected) => {
    setEngine({
      kind: "key",
      provider: r.provider,
      label: r.label,
      dotColor: r.dotColor,
      modelId: r.modelId,
    });
    setStage("ready");
  }, []);

  // Shared model-ready signal (completeness-critic cross-cutting seam): a BYOK
  // engine is ready the moment it connects; a local engine is ready only once
  // the download reaches 100% (P2 feeds `localModelPct`).
  const modelReady = useMemo(() => {
    if (engine?.kind === "key") return true;
    if (engine?.kind === "local") return localModelPct === 100;
    return false;
  }, [engine, localModelPct]);

  const composerCtx = useMemo<FirstRunComposerCtx>(
    () => ({
      stage: stage === "choice" ? "ready" : stage,
      engine,
      models,
      localModelPct,
      modelReady,
      onSent: () => setSent(true),
    }),
    [stage, engine, models, localModelPct, modelReady],
  );

  const ackCtx = useMemo<FirstRunAckCtx>(
    () => ({
      engine,
      modelReady,
      onComplete: () => onComplete(engine),
    }),
    [engine, modelReady, onComplete],
  );

  let body: ReactNode;
  if (sent) {
    body = renderAcknowledgment ? (
      renderAcknowledgment(ackCtx)
    ) : (
      <AckPlaceholder ctx={ackCtx} />
    );
  } else if (stage === "choice") {
    body = (
      <>
        <div className="fr-hero">
          <h1 className="fr-hero__title">{FIRST_RUN_COPY.gate.h1}</h1>
          <p className="fr-hero__sub">{FIRST_RUN_COPY.gate.sub}</p>
        </div>
        <Gate
          keyPort={providerKeys}
          keyProviders={keyProviders}
          onStartDownload={handleStartDownload}
          onKeyConnected={handleKeyConnected}
          localDownloadDisabled={localDownloadDisabled}
          localModelPct={localModelPct}
          renderLocalCard={renderLocalCard}
        />
      </>
    );
  } else {
    // dl / ready → the composer body (P3 slot, else placeholder).
    body = renderComposer ? (
      renderComposer(composerCtx)
    ) : (
      <ComposerPlaceholder ctx={composerCtx} />
    );
  }

  return (
    <div className="fr" data-testid="first-run-surface">
      <header className="fr-top">
        <span className="fr-brand" data-testid="first-run-brand">
          <BrandMark size={18} />
          <span className="fr-brand__name">
            <span className="fr-brand__zx">
              {FIRST_RUN_COPY.topbar.brandLead}
            </span>
            {FIRST_RUN_COPY.topbar.brandRest}
          </span>
        </span>
        {walletChipSlot !== undefined ? (
          <span className="fr-top__chip" data-testid="first-run-wallet-slot">
            {walletChipSlot}
          </span>
        ) : null}
        <span className="fr-top__spacer" />
        <button
          type="button"
          className="fr-skiplink"
          onClick={onSkip}
          data-testid="first-run-skip"
        >
          {FIRST_RUN_COPY.topbar.skip}
        </button>
      </header>

      <main className="fr-main">{body}</main>

      <footer className="fr-foot" data-testid="first-run-footer">
        <span>{appVersion ?? FIRST_RUN_COPY.footer.left}</span>
        <span>{FIRST_RUN_COPY.footer.right}</span>
      </footer>
    </div>
  );
}

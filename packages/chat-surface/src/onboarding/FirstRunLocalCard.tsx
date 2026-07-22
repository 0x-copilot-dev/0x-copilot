// FirstRunLocalCard (P2) — the State-A "Download the local model" gate card.
//
// Fills P1's `renderLocalCard` slot. Verbatim SPEC copy (title/meta/body/btn/
// note from P1's frozen `FIRST_RUN_COPY.local`) with a state-driven foot driven
// by `useFirstRunLocalModel`:
//   idle+ready      → "Start download"  (→ ctx.onStartDownload)
//   downloading     → ProgressBar + "Qwen 3 4B · N%" + note
//   ready           → "on-device · ready"
//   error           → ProgressBar(danger) + alert + Retry
//   Ollama not run  → honest install steps + Re-check
//   feature off     → disabled "available in the desktop app" (web/cloud)
//
// Substrate-agnostic: colors resolve to design-system tokens (`onboarding.css`);
// all I/O is the injected hook's `FirstRunLocalModelsPort`.

import type { ReactElement, ReactNode } from "react";

import { Icon } from "../icons/Icon";
import { ProgressBar } from "../settings/controls";
import type { AvailableLocalModel } from "../settings/DownloadLocalModelModal";
import { FIRST_RUN_COPY } from "./firstRun";
import { firstRunModelPillLabel } from "./localModelEngine";
import type { UseFirstRunLocalModelResult } from "./useFirstRunLocalModel";

export interface FirstRunLocalCardProps {
  /** The download-orchestration state (from `useFirstRunLocalModel`). */
  readonly state: UseFirstRunLocalModelResult;
  /** The curated preset — its `name` drives the "Qwen 3 4B · N%" pill. */
  readonly preset: AvailableLocalModel;
  /**
   * Advance the surface to `dl` AND fire the hook's `start` (P1 wires both to
   * this single callback: `ctx.onStartDownload`). Called only from the enabled
   * idle state — never rendered when the download path is unavailable.
   */
  readonly onStartDownload: () => void;
  /** Optional deep-link to Settings → local models when Ollama isn't running. */
  readonly onOpenLocalModelSettings?: () => void;
}

export function FirstRunLocalCard({
  state,
  preset,
  onStartDownload,
  onOpenLocalModelSettings,
}: FirstRunLocalCardProps): ReactElement {
  return (
    <section className="fr-gcard" data-testid="first-run-local-card">
      <span className="fr-gcard__icon" aria-hidden="true">
        <Icon name="chip" size={20} />
      </span>
      <h2 className="fr-gcard__title">{FIRST_RUN_COPY.local.title}</h2>
      <p className="fr-gcard__meta">{FIRST_RUN_COPY.local.meta}</p>
      <p className="fr-gcard__body">{FIRST_RUN_COPY.local.body}</p>
      <div className="fr-gcard__foot">
        <LocalCardFoot
          state={state}
          preset={preset}
          onStartDownload={onStartDownload}
          onOpenLocalModelSettings={onOpenLocalModelSettings}
        />
      </div>
    </section>
  );
}

function LocalCardFoot({
  state,
  preset,
  onStartDownload,
  onOpenLocalModelSettings,
}: FirstRunLocalCardProps): ReactElement {
  if (state.status === "downloading") {
    return <Downloading state={state} preset={preset} />;
  }
  if (state.status === "ready") {
    return (
      <>
        <p className="fr-gcard__status" data-testid="first-run-local-ready">
          on-device · ready
        </p>
        <p className="fr-gcard__note">{FIRST_RUN_COPY.local.note}</p>
      </>
    );
  }
  if (state.status === "error") {
    return <Errored state={state} preset={preset} />;
  }
  if (state.status === "probing") {
    // Brief: keep the CTA visible but inert until the probe resolves.
    return (
      <>
        <button type="button" className="gbtn gbtn--pri" disabled>
          {FIRST_RUN_COPY.local.btn}
        </button>
        <p className="fr-gcard__note">{FIRST_RUN_COPY.local.note}</p>
      </>
    );
  }
  // idle
  if (!state.enabled) {
    return (
      <p className="fr-gcard__note" data-testid="first-run-local-unavailable">
        Local models run in the desktop app. Add a key to use a frontier model
        here.
      </p>
    );
  }
  if (!state.ollamaRunning) {
    return (
      <OllamaSetup
        onRecheck={state.recheck}
        onOpenLocalModelSettings={onOpenLocalModelSettings}
      />
    );
  }
  return (
    <>
      <button
        type="button"
        className="gbtn gbtn--pri"
        onClick={onStartDownload}
        data-testid="first-run-start-download"
      >
        {FIRST_RUN_COPY.local.btn}
      </button>
      <p className="fr-gcard__note">{FIRST_RUN_COPY.local.note}</p>
    </>
  );
}

function Downloading({
  state,
  preset,
}: {
  readonly state: UseFirstRunLocalModelResult;
  readonly preset: AvailableLocalModel;
}): ReactElement {
  const pill = firstRunModelPillLabel(
    { kind: "local", modelId: state.modelName },
    preset.name,
    state.localModelPct,
  );
  return (
    <div className="fr-gcard__progress" data-testid="first-run-local-progress">
      <ProgressBar
        value={state.localModelPct ?? 0}
        ariaLabel={`Downloading ${preset.name}`}
      />
      <p className="fr-gcard__status">{pill}</p>
      <p className="fr-gcard__note">{FIRST_RUN_COPY.local.note}</p>
    </div>
  );
}

function Errored({
  state,
  preset,
}: {
  readonly state: UseFirstRunLocalModelResult;
  readonly preset: AvailableLocalModel;
}): ReactElement {
  return (
    <div className="fr-gcard__progress">
      <ProgressBar
        value={state.localModelPct ?? 0}
        ariaLabel={`Downloading ${preset.name}`}
        tone="danger"
      />
      <p
        className="fr-gcard__error"
        role="alert"
        data-testid="first-run-local-error"
      >
        Couldn&rsquo;t download {preset.name}: {state.error ?? "unknown error"}
      </p>
      <button
        type="button"
        className="gbtn"
        onClick={state.retry}
        data-testid="first-run-local-retry"
      >
        Retry
      </button>
    </div>
  );
}

function OllamaSetup({
  onRecheck,
  onOpenLocalModelSettings,
}: {
  readonly onRecheck: () => void;
  readonly onOpenLocalModelSettings?: () => void;
}): ReactElement {
  return (
    <div className="fr-gcard__progress" data-testid="first-run-local-setup">
      <p className="fr-gcard__body">
        Local models run through Ollama, a small free runtime. It isn&rsquo;t
        running yet.
      </p>
      <ol className="fr-gcard__setup">
        <li>
          Install it from{" "}
          <a
            href="https://ollama.com/download"
            target="_blank"
            rel="noreferrer"
          >
            ollama.com/download
          </a>
          .
        </li>
        <li>Launch Ollama so it runs in the background.</li>
        <li>Come back here and re-check.</li>
      </ol>
      <FootActions>
        <button
          type="button"
          className="gbtn"
          onClick={onRecheck}
          data-testid="first-run-local-recheck"
        >
          Re-check
        </button>
        {onOpenLocalModelSettings ? (
          <button
            type="button"
            className="gbtn"
            onClick={onOpenLocalModelSettings}
            data-testid="first-run-local-open-settings"
          >
            Open settings
          </button>
        ) : null}
      </FootActions>
    </div>
  );
}

function FootActions({
  children,
}: {
  readonly children: ReactNode;
}): ReactElement {
  return <div className="fr-gcard__actions">{children}</div>;
}

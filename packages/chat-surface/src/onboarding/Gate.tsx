// State A — the gate: "First, give it a model." (SPEC §"State machine" · PRD-P1)
//
// Two `.fr-gcard`s in a 2-col grid:
//   • Download the local model — P1 ships a working DEFAULT card (verbatim copy
//     + "Start download" → stage=dl). P2 replaces it via `renderLocalCard` to
//     add curated-preset + in-gate SSE progress. The slot is the ONLY seam P2
//     needs; P1 owns none of P2's download plumbing.
//   • Bring your own key — always P1's: header copy + inline `KeyForm` reveal.
//
// Presentational: all I/O via the injected `ProviderKeysPort` (KeyForm). The
// local card's `onStartDownload` is a host/surface callback — no I/O here.

import { useState, type ReactElement, type ReactNode } from "react";

import { Icon } from "../icons/Icon";
import type { ProviderKeysPort } from "../settings/data/providerKeys";
import { KeyForm, type KeyFormConnected } from "./KeyForm";
import { FIRST_RUN_COPY, type FirstRunKeyProvider } from "./firstRun";

/**
 * Slot context handed to a `renderLocalCard` override (P2). Carries the
 * start-download callback (→ surface stage=dl), the P2 progress feed, and the
 * disabled flag — everything P2's SSE-aware card needs, nothing more.
 */
export interface FirstRunLocalCardCtx {
  readonly onStartDownload: () => void;
  readonly localModelPct: number | null;
  readonly disabled: boolean;
}

export interface GateProps {
  /** BYOK seam for the inline KeyForm. */
  readonly keyPort: ProviderKeysPort;
  readonly keyProviders?: readonly FirstRunKeyProvider[];
  /** → surface: engine=local, stage=dl (P2 wires SSE). */
  readonly onStartDownload: () => void;
  /** → surface: engine=key, stage=ready. */
  readonly onKeyConnected: (r: KeyFormConnected) => void;
  /** P1 may disable the local download until P2's pipeline default lands. */
  readonly localDownloadDisabled?: boolean;
  /** P2 progress feed for the default card's context (unused by P1's default). */
  readonly localModelPct?: number | null;
  /** P2 replaces the whole local `.fr-gcard`; when absent P1's default renders. */
  readonly renderLocalCard?: (ctx: FirstRunLocalCardCtx) => ReactNode;
}

/**
 * P1 default local-model card. Verbatim SPEC copy + a working "Start download"
 * that advances the state machine to `dl` (P1) — P2 swaps this out via
 * `renderLocalCard` for the curated-preset + SSE-progress card.
 */
function FirstRunLocalCard({
  onStartDownload,
  disabled,
}: {
  readonly onStartDownload: () => void;
  readonly disabled: boolean;
}): ReactElement {
  return (
    <section className="fr-gcard" data-testid="first-run-local-card">
      <span className="fr-gcard__icon" aria-hidden="true">
        <Icon name="chip" size={20} />
      </span>
      <h2 className="fr-gcard__title">{FIRST_RUN_COPY.local.title}</h2>
      <p className="fr-gcard__meta">{FIRST_RUN_COPY.local.meta}</p>
      <p className="fr-gcard__body">{FIRST_RUN_COPY.local.body}</p>
      <div className="fr-gcard__foot">
        <button
          type="button"
          className="gbtn gbtn--pri"
          disabled={disabled}
          onClick={onStartDownload}
          data-testid="first-run-start-download"
        >
          {FIRST_RUN_COPY.local.btn}
        </button>
        <p className="fr-gcard__note">{FIRST_RUN_COPY.local.note}</p>
      </div>
    </section>
  );
}

export function Gate({
  keyPort,
  keyProviders,
  onStartDownload,
  onKeyConnected,
  localDownloadDisabled = false,
  localModelPct = null,
  renderLocalCard,
}: GateProps): ReactElement {
  const [keyOpen, setKeyOpen] = useState(false);

  const localCard =
    renderLocalCard !== undefined ? (
      renderLocalCard({
        onStartDownload,
        localModelPct,
        disabled: localDownloadDisabled,
      })
    ) : (
      <FirstRunLocalCard
        onStartDownload={onStartDownload}
        disabled={localDownloadDisabled}
      />
    );

  return (
    <div className="fr-gate" data-testid="first-run-gate">
      {localCard}

      <section className="fr-gcard" data-testid="first-run-key-card">
        <span className="fr-gcard__icon" aria-hidden="true">
          <Icon name="key" size={20} />
        </span>
        <h2 className="fr-gcard__title">{FIRST_RUN_COPY.key.title}</h2>
        <p className="fr-gcard__meta">{FIRST_RUN_COPY.key.meta}</p>
        <p className="fr-gcard__body">{FIRST_RUN_COPY.key.body}</p>
        <div className="fr-gcard__foot">
          {keyOpen ? (
            <KeyForm
              port={keyPort}
              providers={keyProviders}
              onConnected={onKeyConnected}
              onCancel={() => setKeyOpen(false)}
            />
          ) : (
            <button
              type="button"
              className="gbtn"
              onClick={() => setKeyOpen(true)}
              data-testid="first-run-add-key"
            >
              {FIRST_RUN_COPY.key.btn}
            </button>
          )}
        </div>
      </section>
    </div>
  );
}

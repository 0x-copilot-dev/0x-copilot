// FirstRunLocalCard (P2, rebuilt for PRD-P8 §5) — the State-A "Download the
// local model" gate card.
//
// The header (icon / title / meta / body) is IDENTICAL in every state; only the
// foot varies. The foot is the design's four runtime states, driven by the two
// orthogonal axes `useFirstRunLocalModel` exposes (a runtime state and a
// download phase are independent facts):
//
//   feature off  → "Local models run in the desktop app…"        (unchanged)
//   probing      → disabled "Start download" + note              (unchanged)
//   ①           → "Get Ollama ↗" + the `.watch` "we're looking for it" line
//   ① detected  → `.ok` "Ollama detected — starting your download"
//   ②           → "Start download" + note   /  `.ok` "on-device · ready"
//   ③           → `.dling` spinner + `.ol-prog` bar + byte line + "Continue →"
//   ④           → `.dling.warn` + "Restart Ollama" / "Resume download"
//
// PRD-P8 D1: there is NO red terminal state. The pre-P8 danger `ProgressBar` +
// "Couldn't download …" + Retry branch is deleted; every failure is classified
// server-side and lands in ③ (auto-resuming) or ④ (amber, with a way out).
//
// Substrate-agnostic: colors resolve to design-system tokens (`onboarding.css`),
// all I/O is the injected hook's `FirstRunLocalModelsPort`, and the host brokers
// the external "Get Ollama" open (the renderer may not call `window.open`).

import { useState, type ReactElement, type ReactNode } from "react";

import { Icon } from "../icons/Icon";
import type { AvailableLocalModel } from "../settings/DownloadLocalModelModal";
import { formatBytesPair } from "../settings/localModelsFormat";
import { FIRST_RUN_COPY } from "./firstRun";
import type { UseFirstRunLocalModelResult } from "./useFirstRunLocalModel";

const COPY = FIRST_RUN_COPY.local;

/** Note-line joiner. Punctuation, not copy — the design's middle dot. */
const DOT = " · ";

export interface FirstRunLocalCardProps {
  /** The download + runtime orchestration state (`useFirstRunLocalModel`). */
  readonly state: UseFirstRunLocalModelResult;
  /** The curated preset — its `name` and byte totals drive the ③ note line. */
  readonly preset: AvailableLocalModel;
  /**
   * EXPLICIT "Start download": fires the hook's `start` AND advances the
   * surface to the composer (the host wires both to `ctx.onStartDownload`).
   * Rendered only in state ②, never while a pull is already in flight.
   */
  readonly onStartDownload: () => void;
  /**
   * D4a-1 — "Continue →". Advances the surface WITHOUT (re)starting the pull,
   * so a download the hook auto-started on runtime detection keeps the gate
   * mounted (D4) and the user still has a way forward. Omitted ⇒ no button:
   * a host that has not wired the seam must not get a dead control.
   */
  readonly onContinue?: () => void;
  /**
   * PRD-P8 §8 — host-brokered external open of the Ollama download page. The
   * destination is a constant owned by the HOST (desktop: an IPC channel that
   * takes no URL argument, so the renderer can never ask main to open an
   * arbitrary origin; web: an ordinary external link). Omitted ⇒ state ① shows
   * only its watch line — the card never renders a button that cannot work.
   */
  readonly onGetOllama?: () => void;
}

export function FirstRunLocalCard({
  state,
  preset,
  onStartDownload,
  onContinue,
  onGetOllama,
}: FirstRunLocalCardProps): ReactElement {
  return (
    <section className="fr-gcard" data-testid="first-run-local-card">
      <span className="fr-gcard__icon" aria-hidden="true">
        <Icon name="chip" size={20} />
      </span>
      <h2 className="fr-gcard__title">{COPY.title}</h2>
      <p className="fr-gcard__meta">{COPY.meta}</p>
      <p className="fr-gcard__body">{COPY.body}</p>
      {/* PRD-P8 §5 a11y: ①→detected and ③→④ change with NO user action, so the
       * foot is a polite live region (precedent: ToolsPopover's role="status"
       * bodies). `polite` — never `assertive`: a background download changing
       * state must not interrupt what the user is reading. */}
      <div
        className="fr-gcard__foot"
        role="status"
        aria-live="polite"
        data-testid="first-run-local-foot"
      >
        <LocalCardFoot
          state={state}
          preset={preset}
          onStartDownload={onStartDownload}
          onContinue={onContinue}
          onGetOllama={onGetOllama}
        />
      </div>
    </section>
  );
}

function LocalCardFoot({
  state,
  preset,
  onStartDownload,
  onContinue,
  onGetOllama,
}: FirstRunLocalCardProps): ReactElement {
  const { enabled, phase, runtime, modelInstalled, blocked } = state;

  // "① → detected" and "② running, model absent" are INDISTINGUISHABLE on the
  // hook's axes — both are `runtime: "running"`, `phase: "idle"`, no progress.
  // The difference is history: only a card that was in ① armed the hook's
  // auto-start, so there the pull begins with no click and the foot must say
  // "starting your download" instead of offering a button that would race it.
  // Hence this one bit of card-local memory. It is monotone and derived purely
  // from props, so the render-phase write converges in a single extra pass
  // (React's sanctioned "adjust state when props change").
  const [sawRuntimeDown, setSawRuntimeDown] = useState(false);
  if (
    !sawRuntimeDown &&
    enabled &&
    phase !== "probing" &&
    runtime !== "running"
  ) {
    setSawRuntimeDown(true);
  }

  // --- precedence ----------------------------------------------------------
  //
  // Capability and RUNTIME facts are checked BEFORE the download phase, and
  // that ordering is the whole point of this rewrite. The pre-P8 foot asked
  // `status === "downloading"` first, so the flags below were never consulted
  // once a pull had started: a daemon that died mid-download kept rendering as
  // a healthy download forever, which is exactly the dead end PRD-P8 exists to
  // kill. The runtime fact is also the FRESHER one — the hook re-probes on
  // every failure — so reading it first is reading the newest truth.

  // The feature being gated off server-side dominates everything: nothing
  // below it can be true on a web/cloud deployment.
  if (!enabled) {
    return (
      <p className="fr-gcard__note" data-testid="first-run-local-unavailable">
        {COPY.unavailable}
      </p>
    );
  }

  // Still probing: `runtime` / `blocked` are not known yet, so every branch
  // below would be a guess. Keep the CTA visible but inert.
  if (phase === "probing") {
    return (
      <>
        <button type="button" className="gbtn gbtn--pri" disabled>
          {COPY.btn}
        </button>
        <p className="fr-gcard__note">{COPY.note}</p>
      </>
    );
  }

  // The model is on disk — the card's whole job is done. This outranks ④
  // because ④'s copy ("download resumes on its own") would be a lie once there
  // is no download left to resume.
  if (phase === "ready" || modelInstalled) {
    return <OkLine text={COPY.ready} testId="first-run-local-ready" />;
  }

  // ④ — a dead daemon or a terminal failure. Ahead of ③ deliberately: a
  // `transient` retry loop that is really a stopped runtime would otherwise
  // render as a live download that can never finish.
  if (blocked !== null || runtime === "stopped") {
    return <RuntimeStopped state={state} />;
  }

  // ③ — a live pull, or one that is retrying after a transient break.
  if (phase === "downloading" || phase === "reconnecting") {
    return (
      <Downloading state={state} preset={preset} onContinue={onContinue} />
    );
  }

  // ① — not_installed / unknown. Nothing can start until a runtime exists.
  if (runtime !== "running") {
    return <Watching onGetOllama={onGetOllama} />;
  }

  // ① → detected: the runtime we were watching came up; the hook auto-starts.
  if (sawRuntimeDown) {
    return <OkLine text={COPY.detected} testId="first-run-local-detected" />;
  }

  // ② — running, model absent, and it was already running when we first
  // looked: the user gets the design's explicit Start button.
  return (
    <>
      <button
        type="button"
        className="gbtn gbtn--pri"
        onClick={onStartDownload}
        data-testid="first-run-start-download"
      >
        <Icon name="download" size={13} />
        {COPY.btn}
      </button>
      <p className="fr-gcard__note">{COPY.note}</p>
    </>
  );
}

/** `.fr-dep → .ok` — a settled, good outcome (detected / on-device · ready). */
function OkLine({
  text,
  testId,
}: {
  readonly text: string;
  readonly testId: string;
}): ReactElement {
  return (
    <div className="fr-dep">
      <p className="ok" data-testid={testId}>
        <Icon name="check" size={11} />
        {text}
      </p>
    </div>
  );
}

/** `.fr-dep → .acts` — the foot's action row (omitted when it would be empty). */
function Acts({ children }: { readonly children: ReactNode }): ReactElement {
  return <div className="acts">{children}</div>;
}

/**
 * ① not_installed / unknown — the card polls, so the user never has to
 * re-check; the watch line says so instead of offering a Re-check button.
 */
function Watching({
  onGetOllama,
}: {
  readonly onGetOllama?: () => void;
}): ReactElement {
  return (
    <div className="fr-dep" data-testid="first-run-local-watch">
      {onGetOllama !== undefined ? (
        <Acts>
          <button
            type="button"
            className="gbtn gbtn--pri"
            onClick={onGetOllama}
            data-testid="first-run-local-get-ollama"
          >
            {COPY.getOllama}
          </button>
        </Acts>
      ) : null}
      <p className="watch">{COPY.watchDetect}</p>
    </div>
  );
}

/**
 * ③ downloading / reconnecting.
 *
 * The track is the design's `.ol-prog` (5px, fully rounded) rather than the
 * settings `ProgressBar`, which bakes its own 4px/2px skin into INLINE styles
 * that no stylesheet can override without `!important`. The a11y contract is
 * identical (role="progressbar" + aria-valuenow/min/max + an accessible name);
 * only the skin differs. Making `ProgressBar` skinnable and collapsing the two
 * is a tracked follow-up.
 */
function Downloading({
  state,
  preset,
  onContinue,
}: {
  readonly state: UseFirstRunLocalModelResult;
  readonly preset: AvailableLocalModel;
  readonly onContinue?: () => void;
}): ReactElement {
  const pct = Math.min(Math.max(state.localModelPct ?? 0, 0), 100);
  const bytes = formatBytesPair(state.bytesCompleted, state.bytesTotal);
  const tail =
    state.phase === "reconnecting" ? COPY.reconnecting : COPY.downloadingNote;
  // "Qwen 3 4B · 2.4 / 4.3 GB · downloading in the background" — the byte
  // segment drops out entirely while the totals are unknown rather than
  // printing a placeholder.
  const note = [preset.name, bytes, tail]
    .filter((part): part is string => part !== null)
    .join(DOT);

  return (
    <div className="fr-dep" data-testid="first-run-local-progress">
      <p className="dling">
        <span className="spin" aria-hidden="true" />
        {COPY.downloading}
      </p>
      <div
        className="ol-prog"
        role="progressbar"
        aria-label={`${COPY.progressLabel} ${preset.name}`}
        aria-valuenow={Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
        data-testid="first-run-local-bar"
      >
        <div className="ol-prog__fill" style={{ width: `${pct}%` }} />
      </div>
      <p className="fr-gcard__note" data-testid="first-run-local-note">
        {note}
      </p>
      {onContinue !== undefined ? (
        <Acts>
          <button
            type="button"
            className="gbtn gbtn--pri"
            onClick={onContinue}
            data-testid="first-run-local-continue"
          >
            {COPY.continueBtn}
          </button>
        </Acts>
      ) : null}
    </div>
  );
}

/**
 * ④ runtime stopped / terminal error — one amber shell, two causes.
 *
 * `blocked` carries the server's already-safe message and takes the headline
 * when both are true, because "disk full" is more actionable than "Ollama
 * stopped responding". Both actions can render together: restarting the daemon
 * is the prerequisite, resuming is the follow-through.
 *
 * `Restart Ollama` renders ONLY when the server says it may manage the runtime
 * (PRD-P8 §5). Where it may not — web, or a containerised self-host pointed at
 * `host.docker.internal` — the foot degrades to the instructional watch line,
 * because a button whose route 404s is worse than no button.
 */
function RuntimeStopped({
  state,
}: {
  readonly state: UseFirstRunLocalModelResult;
}): ReactElement {
  const { blocked, runtime, runtimeManaged, resume, restartRuntime } = state;
  const stopped = runtime === "stopped";
  const showRestart = stopped && runtimeManaged;
  const showResume = blocked !== null;

  return (
    <div className="fr-dep" data-testid="first-run-local-stopped">
      <p className="dling warn" data-testid="first-run-local-stopped-msg">
        <Icon name="warn" size={12} />
        {blocked !== null ? blocked.message : COPY.stopped}
      </p>
      {showRestart || showResume ? (
        <Acts>
          {showRestart ? (
            <button
              type="button"
              className="gbtn gbtn--pri"
              onClick={restartRuntime}
              data-testid="first-run-local-restart"
            >
              {COPY.restart}
            </button>
          ) : null}
          {showResume ? (
            <button
              type="button"
              className={showRestart ? "gbtn" : "gbtn gbtn--pri"}
              onClick={resume}
              data-testid="first-run-local-resume"
            >
              {COPY.resume}
            </button>
          ) : null}
        </Acts>
      ) : null}
      {/* Only an UNBLOCKED stop resumes by itself; a terminal failure waits for
       * the explicit Resume, so promising an automatic resume there would be a
       * lie. */}
      {blocked === null && stopped ? (
        <p className="watch" data-testid="first-run-local-stopped-watch">
          {runtimeManaged ? COPY.stoppedWatch : COPY.stoppedWatchUnmanaged}
        </p>
      ) : null}
    </div>
  );
}

// useFirstRunLaunch — the FTUE run-create + handoff state machine (PRD-P3 §3.4,
// PRD-P8 §7).
//
// Orchestrates the two-step first-run create through the host-injected
// `FirstRunRunsPort`, the "Queued — starts when the model lands" deferral (send
// accepted while a local model still downloads → fire when it lands), the
// `StartRunError` surfacing (via the shared `parseTransportError`), and the
// ~1.5s handoff hold before `onComplete(result)`.
//
// PRD-P8 §7 kills the permanent queued hang. `queued` used to be a one-way door:
// no timeout, no failure input, and `launch()` refused every re-submit, so a
// runtime that died mid-download parked the user on "Queued — starts when the
// model lands" forever. Two precise changes fix it:
//   • `modelBlocked` — while queued and the awaited model demonstrably cannot
//     arrive, the phase exits to `blocked`, an actionable state the UI can
//     render honestly. If the model later lands anyway, the held payload still
//     fires (the deferral survives the detour).
//   • the double-launch guard is narrowed to the two phases that actually own a
//     conversation — `starting` (create in flight) and `handoff` (create
//     succeeded). A fast double-Enter still creates exactly one run; a user
//     whose download stalled can re-submit.
//
// Substrate-clean: no `fetch`/IPC here — the port is the only I/O seam. The one
// timer (`setTimeout`) is the handoff hold; `reset()` cancels it so a Skip /
// unmount can't fire a run into a completed FTUE (queued-deferral leak guard).

import { useCallback, useEffect, useRef, useState } from "react";

import type {
  ConversationConnectorScopes,
  ModelSelectionRequest,
  RunAttachmentRequest,
} from "@0x-copilot/api-types";

import { parseTransportError } from "../errors/transportError";
import type { StartRunError } from "../destinations/run";
import type {
  FirstRunLaunchResult,
  FirstRunRunsPort,
} from "./ports/FirstRunRunsPort";

export type FirstRunLaunchPhase =
  | "composing" // no send yet
  | "starting" // create in flight (model ready)
  | "queued" // send accepted, waiting for a downloading local model
  | "blocked" // queued, but the awaited model is NOT coming (P8 §7)
  | "handoff" // created + within the ~1.5s hold before onComplete
  | "error";

export interface FirstRunLaunchPayload {
  readonly text: string;
  readonly attachments: readonly RunAttachmentRequest[];
  /**
   * P4 — the Tools popover's per-run web-search toggle at send time (default
   * true is owned by the surface). Threaded onto `createFirstRun`.
   */
  readonly webSearchEnabled: boolean;
  /** P4 — active connector scopes for this run (omitted when none active). */
  readonly connectorScopes?: ConversationConnectorScopes;
}

export interface UseFirstRunLaunchOptions {
  readonly runs: FirstRunRunsPort;
  /**
   * True when the selected engine can run NOW: BYOK connected, or local
   * pct === 100. P1/P2 derive it in the FirstRunSurface state machine and the
   * host binder threads it here.
   */
  readonly modelReady: boolean;
  /**
   * PRD-P8 §7 — the awaited local model demonstrably cannot arrive right now:
   * the local-model hook's `blocked !== null` (terminal pull error) or
   * `runtime === "stopped"` (the daemon died). While `queued`, a truthy value
   * exits the wait to `blocked` instead of hanging forever.
   *
   * OPTIONAL: omit it and the queued hold keeps its pre-P8 behaviour, so every
   * existing caller compiles and behaves unchanged.
   */
  readonly modelBlocked?: boolean;
  /** Resolved model selection for the run body (null → runtime default). */
  readonly model: ModelSelectionRequest | null;
  /** Fired exactly once at handoff with the created run. */
  readonly onComplete: (result: FirstRunLaunchResult) => void;
  /** Handoff hold before `onComplete`. Default 1500 (SPEC ~1.5s). */
  readonly handoffDelayMs?: number;
}

export interface UseFirstRunLaunch {
  readonly phase: FirstRunLaunchPhase;
  readonly error: StartRunError | null;
  /**
   * Accepts the mapped run attachments. Swallowed only while a create is in
   * flight (`starting`) or has already succeeded (`handoff`) — the precise
   * double-launch guard. Re-submitting from `error`, `queued` or `blocked` is
   * legitimate and re-arms the deferral.
   */
  readonly launch: (payload: FirstRunLaunchPayload) => void;
  /** Cancels any pending handoff timer + returns to `composing`. */
  readonly reset: () => void;
}

function toStartRunError(err: unknown): StartRunError {
  const parsed = parseTransportError(err);
  return {
    message:
      parsed.safeMessage ??
      "Couldn't start the run. Is the backend running and a model configured?",
    code: parsed.code,
    correlationId: parsed.correlationId,
    raw: parsed.raw !== "" ? parsed.raw : undefined,
  };
}

export function useFirstRunLaunch(
  options: UseFirstRunLaunchOptions,
): UseFirstRunLaunch {
  const [phase, setPhaseState] = useState<FirstRunLaunchPhase>("composing");
  const [error, setError] = useState<StartRunError | null>(null);

  // Refs mirror the latest options + phase so the (stable) create callback and
  // timer read current values without re-subscribing.
  const phaseRef = useRef<FirstRunLaunchPhase>("composing");
  const runsRef = useRef(options.runs);
  const modelRef = useRef(options.model);
  const modelReadyRef = useRef(options.modelReady);
  const onCompleteRef = useRef(options.onComplete);
  const handoffDelayRef = useRef(options.handoffDelayMs ?? 1500);
  const pendingRef = useRef<FirstRunLaunchPayload | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  runsRef.current = options.runs;
  modelRef.current = options.model;
  modelReadyRef.current = options.modelReady;
  onCompleteRef.current = options.onComplete;
  handoffDelayRef.current = options.handoffDelayMs ?? 1500;

  const setPhase = useCallback((next: FirstRunLaunchPhase): void => {
    phaseRef.current = next;
    setPhaseState(next);
  }, []);

  const startCreate = useCallback(
    (payload: FirstRunLaunchPayload): void => {
      setPhase("starting");
      setError(null);
      void runsRef.current
        .createFirstRun({
          userInput: payload.text,
          model: modelRef.current,
          attachments: payload.attachments,
          webSearchEnabled: payload.webSearchEnabled,
          connectorScopes: payload.connectorScopes,
        })
        .then((result) => {
          setPhase("handoff");
          if (timerRef.current !== null) {
            clearTimeout(timerRef.current);
          }
          timerRef.current = setTimeout(() => {
            timerRef.current = null;
            onCompleteRef.current(result);
          }, handoffDelayRef.current);
        })
        .catch((err: unknown) => {
          setError(toStartRunError(err));
          setPhase("error");
        });
    },
    [setPhase],
  );

  const launch = useCallback(
    (payload: FirstRunLaunchPayload): void => {
      // Double-launch guard (PRD-P8 §7 narrows it, keeping its ORIGINAL
      // purpose): mirrors RunDestination's isStartingRun guard — a fast
      // double-Enter can't spawn two conversations. Only the two phases that
      // own a create swallow the second call: `starting` (in flight) and
      // `handoff` (already created). `error`, `queued` and `blocked` own no
      // conversation, so a re-submit from them is a legitimate retry — that is
      // the escape hatch for a user whose download stalled.
      if (phaseRef.current === "starting" || phaseRef.current === "handoff") {
        return;
      }
      pendingRef.current = payload;
      if (modelReadyRef.current) {
        startCreate(payload);
      } else {
        // Download in flight → hold; the effect fires the create when the
        // model lands (pct → 100 flips `modelReady`). If it demonstrably is not
        // coming, the blocked-exit effect below takes over.
        setPhase("queued");
      }
    },
    [startCreate, setPhase],
  );

  // The model landed: fire the deferred create exactly once. `blocked` is
  // included on purpose — a stalled download that recovers (Ollama restarted →
  // pull resumes → lands) still honours the send the user already made.
  useEffect(() => {
    if (
      (phase === "queued" || phase === "blocked") &&
      options.modelReady &&
      pendingRef.current
    ) {
      startCreate(pendingRef.current);
    }
  }, [phase, options.modelReady, startCreate]);

  // Queued + the model demonstrably is NOT landing → leave the infinite wait
  // for a state the UI can act on (honest ack title, re-submit, restart the
  // runtime, or switch to a key). The payload stays pending so a recovery is
  // still a single gesture-free send.
  useEffect(() => {
    if (
      phase === "queued" &&
      options.modelBlocked === true &&
      !options.modelReady
    ) {
      setPhase("blocked");
    }
  }, [phase, options.modelBlocked, options.modelReady, setPhase]);

  const reset = useCallback((): void => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    pendingRef.current = null;
    setError(null);
    setPhase("composing");
  }, [setPhase]);

  // Cancel a pending handoff on unmount so a late timer can't fire onComplete
  // into a torn-down tree.
  useEffect(
    () => () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    },
    [],
  );

  return { phase, error, launch, reset };
}

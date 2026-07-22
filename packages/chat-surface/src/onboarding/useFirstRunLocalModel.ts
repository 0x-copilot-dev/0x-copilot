// useFirstRunLocalModel (P2, rewritten for PRD-P8) — the download + runtime
// orchestration hook behind the first-run local-model card.
//
// The flat 5-value status is gone. A runtime state and a download phase are
// independent facts, so they are two axes (PRD-P8 §6):
//
//   runtime : unknown | not_installed | stopped | running   (server-derived)
//   phase   : probing | idle | downloading | reconnecting | ready
//
// What this hook guarantees, and why each guarantee exists:
//
//   • It POLLS while the runtime is not running (3s for the first 2 minutes,
//     then 15s), gated on the PresenceSignal port so a hidden window stops
//     hammering the facade. Without polling, state ① ("download starts once
//     it's detected") could never detect anything.
//   • Auto-start is an EFFECT keyed on the runtime being running while an
//     "armed" flag is set — never a `start()` call from the probe's `.then`.
//     `start` closes over state; a same-tick call there reads a stale closure
//     and silently no-ops (verified hazard). Arming happens only after a
//     NON-running runtime has been observed, so a machine that already had
//     Ollama up at first probe still gets the design's explicit "Start
//     download" button (state ②) rather than a download it never asked for.
//   • The probe short-circuits on an already-installed preset: `list()` +
//     `findInstalledTag` → `modelInstalled`, `phase = "ready"`, `onReady(tag)`
//     and NO pull.
//   • Every failure has a way out (PRD-P8 D1). `runtime_unreachable` keeps the
//     progress, flips the runtime to "stopped" and re-arms so the download
//     resumes by itself once the daemon answers again — on a DELAY, because a
//     daemon that answers `/api/version` while refusing `/api/pull` otherwise
//     re-armed and re-failed in the same microtask turn, forever, at hundreds
//     of requests per second. That lane is rate-limited but never exhausted
//     (§6 wants the resume to happen however long the runtime is down).
//     `transient` retries with capped exponential backoff (1s → 30s) from
//     `phase = "reconnecting"`, for a BOUNDED number of attempts
//     (`MAX_TRANSIENT_RETRIES`) — an unbounded
//     retry loop behind a spinner tells the user just as little as a frozen
//     bar, so a link that never recovers ends in `blocked`, not in perpetual
//     reconnection. `terminal` (and any UNCLASSIFIED failure) sets `blocked`
//     with a message and waits for `resume()`. There is deliberately no state
//     where progress is frozen with no signal and no path forward — that hang
//     is the bug this PRD exists to kill.
//
// Substrate-agnostic: all I/O goes through the injected
// `FirstRunLocalModelsPort` + the `PresenceSignal` port. The bare
// `setTimeout`/`clearTimeout` timer globals follow the package's existing
// precedent (`useFirstRunLaunch`, `useUndoCountdown`) and are cancelled on
// unmount.

import { useCallback, useEffect, useRef, useState } from "react";

import type {
  LocalModelErrorKind,
  LocalRuntimeState,
} from "@0x-copilot/api-types";

import { usePresenceSignal } from "../providers/PresenceSignalProvider";
import type { AvailableLocalModel } from "../settings/DownloadLocalModelModal";
import { FIRST_RUN_COPY } from "./firstRun";
import type { FirstRunLocalModelsPort } from "./localModelsPort";
import {
  INITIAL_PULL_PROGRESS,
  backoffDelayMs,
  classifyPullError,
  findInstalledTag,
  reducePullProgress,
  resolveInstalledTag,
  type LocalPullProgress,
} from "./localModelEngine";
import {
  deriveLocalRuntimeState,
  deriveRuntimeManaged,
} from "./localRuntimeState";

/** Download lifecycle of the local-model card — orthogonal to the runtime. */
export type FirstRunLocalPhase =
  | "probing" // initial capability probe in flight
  | "idle" // nothing in flight (not started, stopped, or blocked)
  | "downloading" // SSE pull in flight
  | "reconnecting" // transient break; a retry is scheduled
  | "ready"; // model present (pulled, or already installed)

/**
 * @deprecated PRD-P8 renamed this axis to `FirstRunLocalPhase` (the runtime is
 * now its own `LocalRuntimeState` axis). Kept as an alias so the barrels keep
 * resolving; drop it once no consumer names it.
 */
export type FirstRunLocalStatus = FirstRunLocalPhase;

/** Why the download stopped and cannot continue without the user. */
export interface FirstRunLocalBlock {
  readonly kind: LocalModelErrorKind;
  readonly message: string;
}

export interface UseFirstRunLocalModelResult {
  /** `status.enabled` — false on web/cloud (feature gated off server-side). */
  readonly enabled: boolean;
  /** Server-derived runtime state; never guessed client-side. */
  readonly runtime: LocalRuntimeState;
  /** This server may start/restart the runtime (`Restart Ollama` can work). */
  readonly runtimeManaged: boolean;
  readonly phase: FirstRunLocalPhase;
  /** The preset was already installed before any pull — no download needed. */
  readonly modelInstalled: boolean;
  /** Download progress 0–100; `null` until a pull begins. */
  readonly localModelPct: number | null;
  readonly bytesCompleted: number | null;
  readonly bytesTotal: number | null;
  /** Set only when nothing will happen without a user action. */
  readonly blocked: FirstRunLocalBlock | null;
  /** Resolved installed Ollama tag once ready (the P3 run-create `model_name`). */
  readonly modelName: string | null;
  /** The Start path is unavailable (still probing, gated off, runtime down). */
  readonly disabled: boolean;
  /** "Start download" — inert unless enabled + runtime running + not pulling. */
  readonly start: () => void;
  /** Manual escape from `blocked` (and the ④ "Resume download" action). */
  readonly resume: () => void;
  /** `POST /v1/local-models/runtime/start`; inert when `runtimeManaged` is false. */
  readonly restartRuntime: () => void;
  /** Re-run the capability probe (the "Re-check" affordance). */
  readonly recheck: () => void;
}

export interface UseFirstRunLocalModelArgs {
  readonly port: FirstRunLocalModelsPort;
  readonly preset: AvailableLocalModel;
  /** Fires exactly once when the preset is present (pulled or already there). */
  readonly onReady?: (modelName: string) => void;
}

/** Poll cadence while the runtime is not running (PRD-P8 §6). */
const FAST_POLL_MS = 3_000;
const FAST_POLL_WINDOW_MS = 120_000;
const SLOW_POLL_MS = 15_000;

/** Used when a break carries no server message (a torn stream, a throw). */
const FALLBACK_FAILURE_MESSAGE = FIRST_RUN_COPY.local.interrupted;

/**
 * How many CONSECUTIVE `transient` retries the hook spends before it stops
 * reconnecting and says so.
 *
 * PRD-P8 §6 capped the retry DELAY (1s → 30s) but left the ATTEMPT count
 * unbounded, so a permanently broken proxy — one that accepts the connection
 * and tears it down every time — reconnected forever behind a `reconnecting`
 * spinner and the user was never told. Unbounded retry is the same silent dead
 * end as a frozen bar; it just animates.
 *
 * Six comes from the backoff schedule, not from taste: 1 + 2 + 4 + 8 + 16 + 30
 * ≈ 61s of self-healing before the user is involved. The blips auto-retry
 * exists to cover — a Wi-Fi handover, a proxy recycling, an Ollama stream
 * hiccup — are over well inside a minute, so the cap does not fire for them; a
 * link that has failed six times across a full minute is not blipping. One
 * minute is also about as long as a silent spinner stays believable.
 *
 * The budget is spent per unit of FORWARD PROGRESS, not per download: any frame
 * carrying more bytes than the pull has ever proved refunds it in full (see
 * `progressHighWaterRef`). A 4.3 GB pull over a flaky link legitimately breaks
 * far more than six times while still finishing, and hard-blocking a download
 * that is visibly working would be its own dishonest dead end.
 *
 * At the cap the hook lands in `blocked` — the state that already carries a
 * message and the manual `resume()` escape (design state ④'s "Resume
 * download"), so no new UI is required for it to be actionable.
 */
const MAX_TRANSIENT_RETRIES = 6;

export function useFirstRunLocalModel({
  port,
  preset,
  onReady,
}: UseFirstRunLocalModelArgs): UseFirstRunLocalModelResult {
  const [enabled, setEnabled] = useState(false);
  const [runtime, setRuntime] = useState<LocalRuntimeState>("unknown");
  const [runtimeManaged, setRuntimeManaged] = useState(false);
  const [phase, setPhase] = useState<FirstRunLocalPhase>("probing");
  const [modelInstalled, setModelInstalled] = useState(false);
  const [progress, setProgress] = useState<LocalPullProgress | null>(null);
  const [blocked, setBlocked] = useState<FirstRunLocalBlock | null>(null);
  const [modelName, setModelName] = useState<string | null>(null);
  /** A probe has answered at least once (false → keep polling even if gated). */
  const [probeOk, setProbeOk] = useState(false);
  /** "Begin the download the moment the runtime is running." */
  const [autoStartArmed, setAutoStartArmed] = useState(false);

  const presence = usePresenceSignal();
  const [visible, setVisible] = useState(
    () => presence.current() === "visible",
  );

  // Live mirrors so the async loops + stable callbacks never read a stale
  // closure (the hazard PRD-P8 §6 calls out by name).
  const mountedRef = useRef(true);
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;
  const runtimeRef = useRef(runtime);
  runtimeRef.current = runtime;
  const runtimeManagedRef = useRef(runtimeManaged);
  runtimeManagedRef.current = runtimeManaged;
  const modelInstalledRef = useRef(modelInstalled);
  modelInstalledRef.current = modelInstalled;
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;
  const portRef = useRef(port);
  portRef.current = port;
  const presetRef = useRef(preset);
  presetRef.current = preset;

  const onReadyFiredRef = useRef(false);
  const probeControllerRef = useRef<AbortController | null>(null);
  const probeInFlightRef = useRef(false);
  const pullControllerRef = useRef<AbortController | null>(null);
  const restartControllerRef = useRef<AbortController | null>(null);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryAttemptRef = useRef(0);
  /**
   * The most bytes this download has ever proved. Only a frame that beats it
   * counts as forward progress and refunds the transient-retry budget, so a
   * stream that keeps re-delivering the same prefix and dying cannot buy itself
   * unlimited retries.
   */
  const progressHighWaterRef = useRef(0);
  /**
   * Consecutive `runtime_unreachable` breaks since this download last got
   * anywhere. Deliberately SEPARATE from `retryAttemptRef`: this lane is
   * rate-limited but never exhausted (PRD-P8 §6 requires a download to resume
   * by itself however long the daemon stays down), so spending the transient
   * budget on a daemon restart would be wrong.
   */
  const unreachableAttemptRef = useRef(0);
  const pollStartedAtRef = useRef(Date.now());
  /** Set after `beginPull` is defined; breaks the retry ↔ pull cycle. */
  const beginPullRef = useRef<() => boolean>(() => false);

  const clearRetryTimer = useCallback(() => {
    if (retryTimerRef.current !== null) {
      clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
  }, []);

  const abortPull = useCallback(() => {
    pullControllerRef.current?.abort();
    pullControllerRef.current = null;
  }, []);

  const markReady = useCallback((tag: string) => {
    setModelInstalled(true);
    setModelName(tag);
    setBlocked(null);
    setPhase("ready");
    if (!onReadyFiredRef.current) {
      onReadyFiredRef.current = true;
      onReadyRef.current?.(tag);
    }
  }, []);

  // --- capability probe -----------------------------------------------------

  const probe = useCallback((): void => {
    if (!mountedRef.current) return;
    if (probeInFlightRef.current) return;
    probeInFlightRef.current = true;

    const controller = new AbortController();
    probeControllerRef.current = controller;
    const activePort = portRef.current;
    const activePreset = presetRef.current;

    void (async () => {
      try {
        const status = await activePort.status(controller.signal);
        if (!mountedRef.current || controller.signal.aborted) return;

        const nextRuntime = deriveLocalRuntimeState(status);

        // Resolve "is the preset already on disk?" BEFORE committing
        // `runtime === "running"`. Committing first renders once with a
        // running runtime and `modelInstalled` still false, which lets the
        // auto-start effect open a pull for a model that is already present —
        // a wasted stream and a ③ flash on a machine needing no download.
        let installedTag: string | null = null;
        if (
          status.enabled &&
          nextRuntime === "running" &&
          !modelInstalledRef.current
        ) {
          try {
            const models = await activePort.list(controller.signal);
            if (!mountedRef.current || controller.signal.aborted) return;
            installedTag = findInstalledTag(models, activePreset.repo);
          } catch {
            // A failed list must not discard an otherwise-good status; fall
            // through and let the pull path discover the truth.
            if (!mountedRef.current || controller.signal.aborted) return;
          }
        }

        // One batch: `runtime` and `modelInstalled` land in the same render,
        // so the auto-start effect never observes the intermediate pair.
        setProbeOk(true);
        setEnabled(status.enabled);
        setRuntime(nextRuntime);
        setRuntimeManaged(deriveRuntimeManaged(status));
        setPhase((p) => (p === "probing" ? "idle" : p));
        if (status.enabled && nextRuntime !== "running") {
          // ① "download starts once it's detected" — arm the edge effect.
          setAutoStartArmed(true);
        }
        if (installedTag !== null) {
          // Already-installed short-circuit: never pull what is present.
          abortPull();
          clearRetryTimer();
          setAutoStartArmed(false);
          markReady(installedTag);
        }
      } catch {
        if (!mountedRef.current || controller.signal.aborted) return;
        setProbeOk(false);
        setPhase((p) => (p === "probing" ? "idle" : p));
        // A live pull is its own liveness proof — don't let a blipped probe
        // repaint the card as "runtime down" underneath a working download.
        if (pullControllerRef.current === null) setRuntime("unknown");
      } finally {
        probeInFlightRef.current = false;
        if (probeControllerRef.current === controller) {
          probeControllerRef.current = null;
        }
      }
    })();
  }, [abortPull, clearRetryTimer, markReady]);

  // --- failure classification (PRD-P8 D1) -----------------------------------

  const fail = useCallback(
    (message: string, rawKind: LocalModelErrorKind | null | undefined) => {
      const kind = classifyPullError(rawKind);
      const safeMessage =
        message.trim().length > 0 ? message : FALLBACK_FAILURE_MESSAGE;
      const idleAgain = (p: FirstRunLocalPhase): FirstRunLocalPhase =>
        p === "downloading" || p === "reconnecting" ? "idle" : p;

      if (kind === "runtime_unreachable") {
        // Keep the progress; the daemon coming back resumes it (state ④).
        setRuntime("stopped");
        // `reconnecting`, not `idle`, for the same reason the transient lane
        // uses it: a retry IS scheduled, so saying "idle" would be a silent
        // regression to state ②'s "Start download" — the card would quietly
        // un-start a download the user did start, with no explanation. When
        // the daemon really is down the card renders ④ regardless (it keys on
        // `runtime === "stopped"` before the phase), so this only changes what
        // the pathological "version answers, pull refuses" case looks like:
        // ③-reconnecting, which is exactly what is happening.
        setPhase("reconnecting");
        // Re-arm on the SAME capped backoff the transient lane uses, NOT in
        // this tick. Arming synchronously was an unbounded, unthrottled hammer
        // whenever the daemon answers `/api/version` but refuses `/api/pull` —
        // a wedged daemon, a restart in flight, an exhausted connection pool.
        // `fail` re-probes itself, that probe answered "running" in the same
        // microtask turn, the auto-start effect re-opened the pull with zero
        // delay, and the new pull broke the same way: measured at 400+ pulls
        // with ZERO wall-clock elapsed, `blocked` still null and a spinner on
        // screen. The rate limit bites in exactly that case and nowhere else:
        // when the runtime really is down, the poll's own probe re-arms within
        // its 3s cadence, so a genuine restart still resumes as fast as it did.
        //
        // The budget here is never SPENT (no cap, unlike `transient`) — §6
        // requires the download to resume on its own however long the runtime
        // stays down. Only the rate is bounded.
        const delay = backoffDelayMs(unreachableAttemptRef.current);
        unreachableAttemptRef.current += 1;
        clearRetryTimer();
        retryTimerRef.current = setTimeout(() => {
          retryTimerRef.current = null;
          setAutoStartArmed(true);
        }, delay);
      } else if (
        kind === "transient" &&
        retryAttemptRef.current < MAX_TRANSIENT_RETRIES
      ) {
        setPhase("reconnecting");
        const delay = backoffDelayMs(retryAttemptRef.current);
        retryAttemptRef.current += 1;
        clearRetryTimer();
        retryTimerRef.current = setTimeout(() => {
          retryTimerRef.current = null;
          beginPullRef.current();
        }, delay);
      } else if (kind === "transient") {
        // Budget spent. Stop reconnecting and SAY so — this is the branch that
        // keeps a permanently broken link from spinning forever in silence.
        // The kind stays `transient` (that is what the failures were); what
        // changed is that the hook has stopped absorbing them on the user's
        // behalf. `blocked` carries the message and gives the card its
        // "Resume download" action, which resets the budget.
        clearRetryTimer();
        setBlocked({ kind, message: FIRST_RUN_COPY.local.retriesExhausted });
        setPhase(idleAgain);
      } else {
        setBlocked({ kind, message: safeMessage });
        setPhase(idleAgain);
      }

      // Refresh the runtime fact behind the failure so a dead daemon cannot
      // keep hiding behind a stale "running".
      probe();
    },
    [clearRetryTimer, probe],
  );

  // --- the pull ------------------------------------------------------------

  // Returns whether the download is now under way — TRUE when a stream was
  // opened, and also when one is already streaming or the model is present
  // (the goal is met either way). FALSE means a guard stopped it and the
  // caller still owes the user progress: callers MUST leave auto-start armed
  // on false, or the card lands in a state with no download and no way to
  // begin one — the frozen dead end this PRD exists to kill.
  const beginPull = useCallback((): boolean => {
    if (!mountedRef.current) return false;
    if (!enabledRef.current) return false;
    if (modelInstalledRef.current) return true; // nothing left to download
    if (pullControllerRef.current !== null) return true; // already streaming
    if (runtimeRef.current !== "running") return false; // can't yet — stay armed

    clearRetryTimer();
    const controller = new AbortController();
    pullControllerRef.current = controller;
    const activePort = portRef.current;
    const activePreset = presetRef.current;

    setBlocked(null);
    setPhase("downloading");
    // Resuming keeps whatever the interrupted attempt had already proved.
    setProgress((prev) => prev ?? INITIAL_PULL_PROGRESS);

    void (async () => {
      try {
        for await (const frame of activePort.pull(
          activePreset,
          controller.signal,
        )) {
          if (!mountedRef.current || controller.signal.aborted) return;
          // Nullish, not just `null`. `error` is REQUIRED by the contract, but
          // the port's runtime guard only requires `sequence_no`/`status`/
          // `done`, so a truncated or legacy frame can reach here without the
          // field at all. A bare `!== null` would then classify it as a failure
          // whose message is `undefined`, `fail` would throw on `.trim()`, the
          // pull's own catch would re-enter `fail` as `transient`, and the next
          // attempt would hit the same frame — one malformed frame becomes an
          // endless retry loop the user can never leave.
          if (frame.error !== null && frame.error !== undefined) {
            fail(frame.error, frame.error_kind);
            return;
          }
          // Genuinely new bytes refund the transient-retry budget: a link that
          // is moving the download forward has earned the full allowance again,
          // however many times it has already broken. Strictly-greater against
          // a high-water mark, not "any frame with bytes", so a stream that
          // replays the same prefix before dying buys nothing.
          const got = frame.bytes_completed;
          if (typeof got === "number" && got > progressHighWaterRef.current) {
            progressHighWaterRef.current = got;
            retryAttemptRef.current = 0;
            // Same refund for the unreachable lane's rate limit: a daemon that
            // came back and is moving bytes has earned an immediate resume the
            // next time it dies, not a 30s wait inherited from the last outage.
            unreachableAttemptRef.current = 0;
          }
          setProgress((prev) =>
            reducePullProgress(frame, activePreset.sizeBytes, prev),
          );
          if (frame.done) {
            const models = await activePort.list(controller.signal);
            if (!mountedRef.current || controller.signal.aborted) return;
            retryAttemptRef.current = 0;
            markReady(
              resolveInstalledTag(
                models,
                activePreset.repo,
                activePreset.quant,
              ),
            );
            return;
          }
        }
        // The stream ended without a terminal frame: treat it as a break, not
        // as success — silently landing on "idle" is the frozen-progress bug.
        if (!mountedRef.current || controller.signal.aborted) return;
        fail(FALLBACK_FAILURE_MESSAGE, "transient");
      } catch {
        if (!mountedRef.current || controller.signal.aborted) return;
        fail(FALLBACK_FAILURE_MESSAGE, "transient");
      } finally {
        if (pullControllerRef.current === controller) {
          pullControllerRef.current = null;
        }
      }
    })();
    return true;
  }, [clearRetryTimer, fail, markReady]);

  beginPullRef.current = beginPull;

  // --- mount / unmount ------------------------------------------------------

  useEffect(() => {
    mountedRef.current = true;
    pollStartedAtRef.current = Date.now();
    probe();
    return () => {
      mountedRef.current = false;
      probeControllerRef.current?.abort();
      probeControllerRef.current = null;
      restartControllerRef.current?.abort();
      restartControllerRef.current = null;
      abortPull();
      if (retryTimerRef.current !== null) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
    };
  }, [probe, abortPull]);

  // --- presence (never poll a hidden window) --------------------------------

  useEffect(() => {
    setVisible(presence.current() === "visible");
    return presence.subscribe((state) => {
      setVisible(state === "visible");
    });
  }, [presence]);

  // --- polling while the runtime is not running -----------------------------

  useEffect(() => {
    if (runtime === "running") return undefined;
    if (!visible) return undefined;
    // A probe that answered "feature off" is final; a probe that never
    // answered keeps retrying so a transport blip is not a dead end.
    if (probeOk && !enabled) return undefined;

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const delay = (): number =>
      Date.now() - pollStartedAtRef.current < FAST_POLL_WINDOW_MS
        ? FAST_POLL_MS
        : SLOW_POLL_MS;

    const tick = (): void => {
      if (cancelled) return;
      probe();
      timer = setTimeout(tick, delay());
    };
    timer = setTimeout(tick, delay());

    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [runtime, visible, enabled, probeOk, probe]);

  // --- auto-start / auto-resume on the runtime edge -------------------------
  //
  // Keyed on state, NOT called from the probe's `.then`. `autoStartArmed` is
  // only ever set after a non-running runtime was observed (or after a
  // `runtime_unreachable` break), so a runtime that was already up at first
  // probe keeps the explicit "Start download" button of design state ②.
  useEffect(() => {
    if (!autoStartArmed) return;
    if (runtime !== "running") return;
    if (!enabled || modelInstalled) return;
    if (blocked !== null) return; // terminal: manual `resume()` only
    // Disarm only on a pull that actually began; otherwise stay armed so a
    // later runtime edge retries instead of stranding the card.
    if (beginPull()) setAutoStartArmed(false);
  }, [autoStartArmed, runtime, enabled, modelInstalled, blocked, beginPull]);

  // --- actions --------------------------------------------------------------

  // Both actions stay ARMED when the pull could not actually begin. Disarming
  // unconditionally was a real dead end: a terminal failure whose follow-up
  // probe also failed left `runtime === "unknown"`, so `beginPull` early-
  // returned, auto-start was off, and the card sat on "Ollama detected" with
  // no button and no download, forever.
  const start = useCallback((): void => {
    retryAttemptRef.current = 0;
    unreachableAttemptRef.current = 0;
    setAutoStartArmed(!beginPull());
  }, [beginPull]);

  const resume = useCallback((): void => {
    retryAttemptRef.current = 0;
    unreachableAttemptRef.current = 0;
    setBlocked(null);
    setAutoStartArmed(!beginPull());
  }, [beginPull]);

  const restartRuntime = useCallback((): void => {
    // Inert, never throwing, when the server cannot manage the runtime — the
    // card renders no button there, and a host that calls anyway gets a no-op.
    if (!runtimeManagedRef.current) return;
    if (!enabledRef.current) return;
    if (restartControllerRef.current !== null) return;

    const controller = new AbortController();
    restartControllerRef.current = controller;
    const activePort = portRef.current;
    setAutoStartArmed(true);

    void (async () => {
      try {
        const status = await activePort.startRuntime(controller.signal);
        if (!mountedRef.current || controller.signal.aborted) return;
        setEnabled(status.enabled);
        setRuntimeManaged(deriveRuntimeManaged(status));
        setRuntime(deriveLocalRuntimeState(status));
      } catch {
        // Swallowed on purpose: the re-probe below is the recovery signal.
      } finally {
        if (restartControllerRef.current === controller) {
          restartControllerRef.current = null;
        }
        if (mountedRef.current) probe();
      }
    })();
  }, [probe]);

  const recheck = useCallback((): void => {
    probe();
  }, [probe]);

  const disabled = phase === "probing" || !enabled || runtime !== "running";

  return {
    enabled,
    runtime,
    runtimeManaged,
    phase,
    modelInstalled,
    localModelPct: progress?.pct ?? null,
    bytesCompleted: progress?.bytesCompleted ?? null,
    bytesTotal: progress?.bytesTotal ?? null,
    blocked,
    modelName,
    disabled,
    start,
    resume,
    restartRuntime,
    recheck,
  };
}

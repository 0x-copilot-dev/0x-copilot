// useFirstRunLocalModel (P2) — the download-orchestration hook.
//
// Owned by the HOST binder (desktop `FirstRunSurfaceMount`, web onboarding
// route): probes capability on mount, drives the real facade SSE pull on
// `start()`, reduces frames into `localModelPct` (0→100), resolves the
// installed Ollama tag on completion, and fires `onReady(modelName)` once.
//
// The binder feeds the hook's outputs into `FirstRunSurface`:
//   • localModelPct       → `localModelPct` (P1 flips `modelReady` at === 100)
//   • start               → `onStartLocalDownload`
//   • disabled            → `localDownloadDisabled`
//   • the whole result    → `FirstRunLocalCard state`
//
// Substrate-agnostic: every touchpoint is the injected `FirstRunLocalModelsPort`.

import { useCallback, useEffect, useRef, useState } from "react";

import type { FirstRunLocalModelsPort } from "./localModelsPort";
import type { AvailableLocalModel } from "../settings/DownloadLocalModelModal";
import { pullPercent, resolveInstalledTag } from "./localModelEngine";

/** Lifecycle of the local-model card. */
export type FirstRunLocalStatus =
  | "probing" // initial capability probe in flight
  | "idle" // ready to start (enabled + ollama running), or degraded
  | "downloading" // SSE pull in flight
  | "ready" // pull done, tag resolved
  | "error"; // pull/probe failed → retry

export interface UseFirstRunLocalModelResult {
  /** Download progress 0–100; `null` until `start()`. Feeds P1's model-ready. */
  readonly localModelPct: number | null;
  readonly status: FirstRunLocalStatus;
  /** `status.enabled` — false on web/cloud (feature gated off). */
  readonly enabled: boolean;
  /** `status.ollama_running` — false → the card shows honest setup steps. */
  readonly ollamaRunning: boolean;
  /** The Start path is unavailable (still probing, gated off, or Ollama down). */
  readonly disabled: boolean;
  /** Resolved installed Ollama tag once ready (the P3 run-create `model_name`). */
  readonly modelName: string | null;
  /** Human error string when `status === "error"`. */
  readonly error: string | null;
  /** "Start download" — no-op unless enabled + Ollama running + not downloading. */
  readonly start: () => void;
  /** Re-run the pull after an error. */
  readonly retry: () => void;
  /** Re-run the capability probe (the Ollama-not-running "Re-check"). */
  readonly recheck: () => void;
}

export interface UseFirstRunLocalModelArgs {
  readonly port: FirstRunLocalModelsPort;
  readonly preset: AvailableLocalModel;
  /** Fires exactly once when a pull completes and the tag is resolved. */
  readonly onReady?: (modelName: string) => void;
}

export function useFirstRunLocalModel({
  port,
  preset,
  onReady,
}: UseFirstRunLocalModelArgs): UseFirstRunLocalModelResult {
  const [status, setStatus] = useState<FirstRunLocalStatus>("probing");
  const [enabled, setEnabled] = useState(false);
  const [ollamaRunning, setOllamaRunning] = useState(false);
  const [localModelPct, setLocalModelPct] = useState<number | null>(null);
  const [modelName, setModelName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Refs so the async pull loop reads live values without re-subscribing.
  const mountedRef = useRef(true);
  const pullControllerRef = useRef<AbortController | null>(null);
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;
  const portRef = useRef(port);
  portRef.current = port;
  const presetRef = useRef(preset);
  presetRef.current = preset;

  const abortPull = useCallback(() => {
    pullControllerRef.current?.abort();
    pullControllerRef.current = null;
  }, []);

  // Capability probe. `runProbe` is re-callable for the "Re-check" affordance.
  const runProbe = useCallback(() => {
    const controller = new AbortController();
    setStatus("probing");
    portRef.current
      .status(controller.signal)
      .then((s) => {
        if (!mountedRef.current || controller.signal.aborted) return;
        setEnabled(s.enabled);
        setOllamaRunning(s.ollama_running);
        setStatus("idle");
      })
      .catch(() => {
        if (!mountedRef.current || controller.signal.aborted) return;
        // A failed probe degrades to "unavailable" — never a broken Start.
        setEnabled(false);
        setOllamaRunning(false);
        setStatus("idle");
      });
    return controller;
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    const probe = runProbe();
    return () => {
      mountedRef.current = false;
      probe.abort();
      abortPull();
    };
  }, [runProbe, abortPull]);

  const start = useCallback(() => {
    // Guard: only from a live, capable, non-downloading state.
    if (!enabled || !ollamaRunning) return;
    if (status === "downloading") return;

    abortPull();
    const controller = new AbortController();
    pullControllerRef.current = controller;

    setError(null);
    setModelName(null);
    setLocalModelPct(2); // SPEC seeds the pill at 2% the instant a pull begins
    setStatus("downloading");

    void (async () => {
      const activePort = portRef.current;
      const activePreset = presetRef.current;
      try {
        for await (const frame of activePort.pull(
          activePreset,
          controller.signal,
        )) {
          if (controller.signal.aborted || !mountedRef.current) return;
          if (frame.error !== null) {
            setError(frame.error);
            setStatus("error");
            return;
          }
          setLocalModelPct(
            pullPercent(
              frame.bytes_completed,
              frame.bytes_total,
              activePreset.sizeBytes,
              frame.done,
            ),
          );
          if (frame.done) {
            const models = await activePort.list(controller.signal);
            if (controller.signal.aborted || !mountedRef.current) return;
            const tag = resolveInstalledTag(
              models,
              activePreset.repo,
              activePreset.quant,
            );
            setLocalModelPct(100);
            setModelName(tag);
            setStatus("ready");
            onReadyRef.current?.(tag);
            return;
          }
        }
      } catch {
        if (controller.signal.aborted || !mountedRef.current) return;
        setError("Download interrupted.");
        setStatus("error");
      }
    })();
  }, [enabled, ollamaRunning, status, abortPull]);

  const retry = useCallback(() => {
    // `start` guards on `status === "downloading"`; from "error" it re-pulls.
    start();
  }, [start]);

  const recheck = useCallback(() => {
    runProbe();
  }, [runProbe]);

  const disabled = status === "probing" || !enabled || !ollamaRunning;

  return {
    localModelPct,
    status,
    enabled,
    ollamaRunning,
    disabled,
    modelName,
    error,
    start,
    retry,
    recheck,
  };
}

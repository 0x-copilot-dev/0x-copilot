// <DownloadLocalModelModal /> — Settings → Local models → "Get another model".
//
// The "Download local model" flow (DESIGN-SPEC §5): pick from available
// (name·param, size·note, download glyph) → progress bar (%) with size / speed
// / ETA → "Ready to run locally" + "Use as default local model" toggle →
// Finish. 3 StepDots.
//
// Substrate-agnostic (chat-surface boundary): NO fetch / EventSource / window.
// The runtime pull is a HOST concern injected as `startPull`, a callback that
// opens the facade SSE pull lane and streams `LocalModelPullEvent`s back
// through `handlers.onEvent` (mirrors apps/frontend `streamLocalModelPull`).
// The modal owns only the flow state; the host owns the network + Ollama.
//
// Built on PR-5.2 primitives: <Modal> + <StepDots> chrome, <ProgressBar>,
// <SetNote>, <Frow>. Colors resolve ONLY to design-system v2 tokens.

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import type {
  LocalModelPullEvent,
  PullLocalModelRequest,
} from "@0x-copilot/api-types";
import { Button, Toggle } from "@0x-copilot/design-system";

import { Modal, StepDots } from "./Modal";
import { Frow, SetNote } from "./SettingsChrome";
import { ProgressBar } from "./controls";
import { formatBytes, formatEta, humanStatus } from "./localModelsFormat";

// ---------------------------------------------------------------------------
// Host seam (the callback/port injected by web / desktop)
// ---------------------------------------------------------------------------

/**
 * One model offered for download (the `LOCAL_AVAILABLE` catalog, DESIGN-SPEC §5
 * "pick from available"). `sizeBytes` is the pre-download heads-up so the
 * progress bar has a denominator before the first byte-count frame arrives.
 */
export interface AvailableLocalModel {
  /** HF GGUF repo, e.g. "bartowski/Llama-3.2-1B-Instruct-GGUF". */
  readonly repo: string;
  /** Quantization tag, e.g. "Q4_K_M". */
  readonly quant: string;
  /** Display name, e.g. "Llama 3.2". */
  readonly name: string;
  /** Parameter count label, e.g. "1.2B". */
  readonly parameterSize?: string | null;
  /** Heads-up download size in bytes. */
  readonly sizeBytes?: number | null;
  /** Short note, e.g. "fast · good for chat". */
  readonly note?: string | null;
}

/** Handle to an in-flight pull; `close()` aborts the host subscription. */
export interface LocalModelPullHandle {
  close(): void;
}

/** Callbacks the modal hands to `startPull` for progress + failure. */
export interface LocalModelPullHandlers {
  readonly onEvent: (event: LocalModelPullEvent) => void;
  readonly onError: (error: Error) => void;
}

/** The host-injected pull opener (facade SSE lane + Ollama). */
export type StartLocalModelPull = (
  request: PullLocalModelRequest,
  handlers: LocalModelPullHandlers,
) => LocalModelPullHandle;

/** Result handed to the host on Finish so it can refresh + apply the default. */
export interface LocalModelDownloadResult {
  readonly model: AvailableLocalModel;
  /** Whether "Use as default local model" was left on. */
  readonly setAsDefault: boolean;
}

export interface DownloadLocalModelModalProps {
  readonly open: boolean;
  readonly onClose: () => void;
  /** Catalog to pick from (host-supplied; DESIGN-SPEC §5). */
  readonly availableModels: readonly AvailableLocalModel[];
  /** Host seam: opens the pull stream and streams progress back. */
  readonly startPull: StartLocalModelPull;
  /** Fires once on Finish with the downloaded model + default choice. */
  readonly onFinish: (result: LocalModelDownloadResult) => void;
  /** Default for the "Use as default local model" toggle (default: true). */
  readonly defaultUseAsDefault?: boolean;
}

// ---------------------------------------------------------------------------
// Internal flow state
// ---------------------------------------------------------------------------

type FlowStep = "pick" | "progress" | "ready";

interface PullProgress {
  status: string;
  bytesTotal: number | null;
  bytesCompleted: number | null;
  speedBps: number | null;
  etaSeconds: number | null;
  error: string | null;
  done: boolean;
}

const STEP_INDEX: Record<FlowStep, number> = {
  pick: 1,
  progress: 2,
  ready: 3,
};

function initialProgress(): PullProgress {
  return {
    status: "starting",
    bytesTotal: null,
    bytesCompleted: null,
    speedBps: null,
    etaSeconds: null,
    error: null,
    done: false,
  };
}

function pullPercent(
  progress: PullProgress,
  sizeHint: number | null | undefined,
): number {
  const total = progress.bytesTotal ?? sizeHint ?? 0;
  const done = progress.bytesCompleted ?? 0;
  if (total > 0) return Math.min(100, (done / total) * 100);
  return progress.done ? 100 : 0;
}

// ---------------------------------------------------------------------------
// Styles (token-only)
// ---------------------------------------------------------------------------

const listStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-sm)",
  margin: 0,
  padding: 0,
  listStyle: "none",
};

function pickRowStyle(disabled: boolean): CSSProperties {
  return {
    display: "flex",
    alignItems: "center",
    gap: "var(--space-md)",
    width: "100%",
    padding: "var(--space-sm) var(--space-md)",
    borderRadius: "var(--radius-md)",
    border: "1px solid var(--color-border)",
    backgroundColor: "var(--color-surface-muted)",
    color: "var(--color-text)",
    font: "inherit",
    textAlign: "left",
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.5 : 1,
    transition: "background-color var(--duration-fast) var(--ease-standard)",
  };
}

const pickNameStyle: CSSProperties = {
  fontSize: "var(--font-size-sm)",
  fontWeight: "var(--font-weight-medium)",
  color: "var(--color-text)",
};

const pickSubStyle: CSSProperties = {
  margin: "1px 0 0",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-2xs)",
  color: "var(--color-text-subtle)",
};

const progressLineStyle: CSSProperties = {
  display: "flex",
  alignItems: "baseline",
  justifyContent: "space-between",
  gap: "var(--space-md)",
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-muted)",
};

// ---------------------------------------------------------------------------
// DownloadLocalModelModal
// ---------------------------------------------------------------------------

export function DownloadLocalModelModal({
  open,
  onClose,
  availableModels,
  startPull,
  onFinish,
  defaultUseAsDefault = true,
}: DownloadLocalModelModalProps): ReactElement | null {
  const [step, setStep] = useState<FlowStep>("pick");
  const [selected, setSelected] = useState<AvailableLocalModel | null>(null);
  const [progress, setProgress] = useState<PullProgress>(initialProgress);
  const [useAsDefault, setUseAsDefault] = useState(defaultUseAsDefault);
  const handleRef = useRef<LocalModelPullHandle | null>(null);

  const closeStream = useCallback(() => {
    handleRef.current?.close();
    handleRef.current = null;
  }, []);

  // Reset the flow whenever the modal is (re)opened; always tear the stream
  // down on unmount so a background pull can't outlive the modal.
  useEffect(() => {
    if (open) {
      setStep("pick");
      setSelected(null);
      setProgress(initialProgress());
      setUseAsDefault(defaultUseAsDefault);
    }
    return () => {
      closeStream();
    };
  }, [open, defaultUseAsDefault, closeStream]);

  const beginPull = useCallback(
    (model: AvailableLocalModel) => {
      closeStream();
      setSelected(model);
      setProgress(initialProgress());
      setStep("progress");
      handleRef.current = startPull(
        { repo: model.repo, quant: model.quant },
        {
          onEvent: (event: LocalModelPullEvent) => {
            setProgress((prev) => ({
              status: event.status,
              bytesTotal: event.bytes_total ?? prev.bytesTotal,
              bytesCompleted: event.bytes_completed ?? prev.bytesCompleted,
              speedBps: event.speed_bps ?? prev.speedBps,
              etaSeconds: event.eta_seconds ?? prev.etaSeconds,
              error: event.error ?? prev.error,
              done: event.done || Boolean(event.error),
            }));
            if (event.error) {
              closeStream();
            } else if (event.done) {
              closeStream();
              setStep("ready");
            }
          },
          onError: () => {
            closeStream();
            setProgress((prev) => ({
              ...prev,
              done: true,
              error: "Download interrupted.",
            }));
          },
        },
      );
    },
    [startPull, closeStream],
  );

  const handleFinish = useCallback(() => {
    if (selected === null) return;
    onFinish({ model: selected, setAsDefault: useAsDefault });
    onClose();
  }, [selected, useAsDefault, onFinish, onClose]);

  const errored = progress.error !== null;

  const footer = (
    <>
      <StepDots total={3} current={STEP_INDEX[step]} />
      <div style={{ display: "inline-flex", gap: "var(--space-sm)" }}>
        {step === "pick" ? (
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
        ) : null}
        {step === "progress" && errored ? (
          <>
            <Button
              variant="ghost"
              onClick={() => {
                closeStream();
                setStep("pick");
              }}
              data-testid="download-back"
            >
              Back
            </Button>
            <Button
              variant="secondary"
              onClick={() => selected && beginPull(selected)}
              data-testid="download-retry"
            >
              Retry
            </Button>
          </>
        ) : null}
        {step === "ready" ? (
          <Button
            variant="primary"
            onClick={handleFinish}
            data-testid="download-finish"
          >
            Finish
          </Button>
        ) : null}
      </div>
    </>
  );

  if (!open) return null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Download a local model"
      subtitle="Runs on your machine via Ollama"
      logo={<span aria-hidden="true">◆</span>}
      footer={footer}
    >
      {step === "pick" ? (
        <PickStep models={availableModels} onPick={beginPull} />
      ) : null}
      {step === "progress" ? (
        <ProgressStep model={selected} progress={progress} />
      ) : null}
      {step === "ready" ? (
        <ReadyStep
          model={selected}
          useAsDefault={useAsDefault}
          onToggleDefault={setUseAsDefault}
        />
      ) : null}
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Steps
// ---------------------------------------------------------------------------

function PickStep({
  models,
  onPick,
}: {
  readonly models: readonly AvailableLocalModel[];
  readonly onPick: (model: AvailableLocalModel) => void;
}): ReactElement {
  if (models.length === 0) {
    return (
      <SetNote>
        No models available to download right now. Check your local runtime is
        reachable and try again.
      </SetNote>
    );
  }
  return (
    <ul style={listStyle} data-testid="download-pick-list">
      {models.map((model) => {
        const sub = [
          model.parameterSize ?? null,
          model.sizeBytes != null ? formatBytes(model.sizeBytes) : null,
          model.note ?? null,
        ]
          .filter((part): part is string => Boolean(part))
          .join(" · ");
        return (
          <li key={`${model.repo}:${model.quant}`}>
            <button
              type="button"
              style={pickRowStyle(false)}
              onClick={() => onPick(model)}
              data-testid="download-pick-option"
              data-repo={model.repo}
            >
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={pickNameStyle}>{model.name}</span>
                {sub ? <span style={pickSubStyle}>{sub}</span> : null}
              </span>
              <span
                aria-hidden="true"
                style={{ flex: "0 0 auto", color: "var(--color-text-muted)" }}
              >
                ↓
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function ProgressStep({
  model,
  progress,
}: {
  readonly model: AvailableLocalModel | null;
  readonly progress: PullProgress;
}): ReactElement {
  const name = model?.name ?? "model";
  if (progress.error !== null) {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-md)",
        }}
      >
        <ProgressBar
          value={pullPercent(progress, model?.sizeBytes)}
          ariaLabel={`Downloading ${name}`}
          tone="danger"
        />
        <SetNote tone="danger" role="alert">
          Couldn&rsquo;t download {name}: {progress.error} Retry, or go back to
          pick a different model.
        </SetNote>
      </div>
    );
  }

  const total = progress.bytesTotal ?? model?.sizeBytes ?? null;
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-md)",
      }}
    >
      <ProgressBar
        value={pullPercent(progress, model?.sizeBytes)}
        ariaLabel={`Downloading ${name}`}
      />
      <div style={progressLineStyle}>
        <span>{humanStatus(progress.status)}</span>
        <span>
          {total ? formatBytes(total) : ""}
          {progress.speedBps ? ` · ${formatBytes(progress.speedBps)}/s` : ""}
          {progress.etaSeconds && !progress.done
            ? ` · ${formatEta(progress.etaSeconds)} left`
            : ""}
        </span>
      </div>
    </div>
  );
}

function ReadyStep({
  model,
  useAsDefault,
  onToggleDefault,
}: {
  readonly model: AvailableLocalModel | null;
  readonly useAsDefault: boolean;
  readonly onToggleDefault: (next: boolean) => void;
}): ReactElement {
  const name = model?.name ?? "The model";
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-md)",
      }}
    >
      <SetNote
        icon={<span aria-hidden="true">✓</span>}
        data-testid="download-ready"
      >
        <strong style={{ color: "var(--color-success)" }}>
          Ready to run locally.
        </strong>{" "}
        {name} is installed and appears in your model picker.
      </SetNote>
      <Frow
        label="Use as default local model"
        hint="New runs that pick a local model will use this one."
      >
        <Toggle
          checked={useAsDefault}
          aria-label="Use as default local model"
          onChange={(event) => onToggleDefault(event.target.checked)}
          data-testid="download-default-toggle"
        />
      </Frow>
    </div>
  );
}

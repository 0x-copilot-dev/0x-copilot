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
import { Button, Field, TextInput, Toggle } from "@0x-copilot/design-system";

import { Modal, StepDots } from "./Modal";
import { Frow, SecHead, SetNote } from "./SettingsChrome";
import { ProgressBar } from "./controls";
import { formatBytes, formatEta, humanStatus } from "./localModelsFormat";

/** GGUF quant the custom-repo path pre-fills (matches the catalog default). */
const DEFAULT_QUANT = "Q4_K_M";

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
  /**
   * Optional pre-download size probe (host → `GET /v1/local-models/size`,
   * returning `size_bytes`). Used only for the custom-repo path (catalog picks
   * already carry `sizeBytes`): it seeds the progress bar's denominator AND
   * surfaces a missing repo/quant as an error BEFORE streaming gigabytes, just
   * as the legacy web section did. Degrades gracefully — when absent, the custom
   * pull starts immediately and the byte totals arrive from the stream; when it
   * rejects, the flow shows a "couldn't find that model" error with Back/Retry.
   */
  readonly resolveSize?: (request: PullLocalModelRequest) => Promise<number>;
  /**
   * Whether the custom "advanced" free-text repo/quant affordance is offered
   * (DESIGN-SPEC §5 power-user path — pull any HF GGUF). Default `true`.
   */
  readonly allowCustomModel?: boolean;
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
  resolveSize,
  allowCustomModel = true,
}: DownloadLocalModelModalProps): ReactElement | null {
  const [step, setStep] = useState<FlowStep>("pick");
  const [selected, setSelected] = useState<AvailableLocalModel | null>(null);
  const [progress, setProgress] = useState<PullProgress>(initialProgress);
  const [useAsDefault, setUseAsDefault] = useState(defaultUseAsDefault);
  const handleRef = useRef<LocalModelPullHandle | null>(null);
  // Monotonic pull id: bumped on every close/reopen so an in-flight size probe
  // that resolves late can't open a stream (or clobber state) for a pull the
  // user already abandoned.
  const pullSeqRef = useRef(0);

  const closeStream = useCallback(() => {
    pullSeqRef.current += 1;
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
      const seq = pullSeqRef.current;
      setSelected(model);
      setProgress(initialProgress());
      setStep("progress");

      const openStream = (resolved: AvailableLocalModel): void => {
        if (pullSeqRef.current !== seq) return; // abandoned mid-probe
        setSelected(resolved);
        handleRef.current = startPull(
          { repo: resolved.repo, quant: resolved.quant },
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
      };

      // Custom-repo picks arrive with no `sizeBytes`: when the host wired a
      // size probe, resolve it first so we (a) show a heads-up size and (b) fail
      // fast on a missing repo/quant BEFORE streaming (legacy web parity).
      // Catalog picks already carry `sizeBytes`, so they skip the probe.
      if (resolveSize && model.sizeBytes == null) {
        setProgress((prev) => ({ ...prev, status: "resolving" }));
        void resolveSize({ repo: model.repo, quant: model.quant })
          .then((sizeBytes) => openStream({ ...model, sizeBytes }))
          .catch((err: unknown) => {
            if (pullSeqRef.current !== seq) return;
            setProgress((prev) => ({
              ...prev,
              done: true,
              error:
                err instanceof Error && err.message
                  ? err.message
                  : "Couldn’t find that model — check the repo and quant.",
            }));
          });
      } else {
        openStream(model);
      }
    },
    [startPull, closeStream, resolveSize],
  );

  // Advanced free-text path: pull any HF GGUF by repo + quant (DESIGN-SPEC §5,
  // legacy web parity). Reuses `beginPull` so custom + catalog share one flow.
  const beginCustomPull = useCallback(
    (repo: string, quant: string) => {
      const cleanRepo = repo.trim();
      if (cleanRepo === "") return;
      const cleanQuant = quant.trim() || DEFAULT_QUANT;
      beginPull({
        repo: cleanRepo,
        quant: cleanQuant,
        name: cleanRepo,
        parameterSize: null,
        sizeBytes: null,
        note: `Custom · ${cleanQuant}`,
      });
    },
    [beginPull],
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
        <PickStep
          models={availableModels}
          onPick={beginPull}
          allowCustom={allowCustomModel}
          onPickCustom={beginCustomPull}
        />
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
  allowCustom,
  onPickCustom,
}: {
  readonly models: readonly AvailableLocalModel[];
  readonly onPick: (model: AvailableLocalModel) => void;
  readonly allowCustom: boolean;
  readonly onPickCustom: (repo: string, quant: string) => void;
}): ReactElement {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-lg)",
      }}
    >
      {models.length === 0 ? (
        <SetNote>
          No curated models to suggest right now
          {allowCustom ? " — enter a Hugging Face GGUF repo below." : "."}
        </SetNote>
      ) : (
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
                    style={{
                      flex: "0 0 auto",
                      color: "var(--color-text-muted)",
                    }}
                  >
                    ↓
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {allowCustom ? <CustomModelForm onSubmit={onPickCustom} /> : null}
    </div>
  );
}

// Advanced free-text path (DESIGN-SPEC §5): pull ANY Hugging Face GGUF by repo +
// quant, preserving the legacy web power-user flow. Drives the same `startPull`
// seam as a catalog pick (the parent's `onSubmit` → `beginCustomPull`).
function CustomModelForm({
  onSubmit,
}: {
  readonly onSubmit: (repo: string, quant: string) => void;
}): ReactElement {
  const [repo, setRepo] = useState("");
  const [quant, setQuant] = useState(DEFAULT_QUANT);
  const canSubmit = repo.trim() !== "";
  return (
    <div
      data-testid="download-custom"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-sm)",
        paddingTop: "var(--space-sm)",
        borderTop: "1px solid var(--color-border)",
      }}
    >
      <SecHead>Custom model</SecHead>
      <Field
        label="Hugging Face GGUF repo"
        hint="e.g. bartowski/Llama-3.2-1B-Instruct-GGUF"
      >
        <TextInput
          value={repo}
          placeholder="vendor/repo-GGUF"
          spellCheck={false}
          onChange={(event) => setRepo(event.target.value)}
          data-testid="download-custom-repo"
        />
      </Field>
      <Field label="Quantization" hint="Smaller = less memory, lower quality.">
        <TextInput
          value={quant}
          placeholder={DEFAULT_QUANT}
          spellCheck={false}
          onChange={(event) => setQuant(event.target.value)}
          data-testid="download-custom-quant"
        />
      </Field>
      <div>
        <Button
          variant="secondary"
          disabled={!canSubmit}
          onClick={() => onSubmit(repo, quant)}
          data-testid="download-custom-submit"
        >
          Download
        </Button>
      </div>
    </div>
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

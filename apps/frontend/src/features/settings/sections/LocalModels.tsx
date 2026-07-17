// Local models (Round 2) — download a Hugging Face GGUF and run it locally
// via a user-installed Ollama, all under Settings → Local models.
//
// Three states, mirrored from GET /v1/local-models/status:
//   * Ollama not running  → setup steps (install + start).
//   * Ollama running       → installed list (with GPU/CPU placement) + a
//                            pull-by-slug row showing download size, live
//                            progress, speed + ETA, and delete.
//
// This section only downloads/lists/removes. "Running" a model happens by
// selecting it in the chat model picker, where downloaded models appear.
// The section is server-gated (SettingsScreen hides it unless enabled), so
// it never renders on cloud/multi-tenant deployments.

import type {
  LocalModelPullEvent,
  LocalModelSummary,
  LocalModelsStatus,
} from "@0x-copilot/api-types";
import { Button, Card, Field, TextInput } from "@0x-copilot/design-system";
import type { ReactElement } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteLocalModel,
  getLocalModelSize,
  getLocalModelsStatus,
  listLocalModels,
  streamLocalModelPull,
  type LocalModelPullStream,
} from "../../../api/localModelsApi";
import { errorMessage } from "../../../utils/errors";

const DEFAULT_QUANT = "Q4_K_M";

interface PullState {
  readonly repo: string;
  readonly quant: string;
  status: string;
  bytesTotal: number | null;
  bytesCompleted: number | null;
  speedBps: number | null;
  etaSeconds: number | null;
  sizeHint: number | null;
  error: string | null;
  done: boolean;
}

export function LocalModels(): ReactElement {
  const [status, setStatus] = useState<LocalModelsStatus | null>(null);
  const [models, setModels] = useState<readonly LocalModelSummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const refreshList = useCallback(() => {
    listLocalModels()
      .then((response) => setModels(response.models))
      .catch((err: unknown) =>
        setLoadError(errorMessage(err, "Could not list local models.")),
      );
  }, []);

  const refreshStatus = useCallback(() => {
    getLocalModelsStatus()
      .then((next) => {
        setStatus(next);
        if (next.ollama_running) refreshList();
      })
      .catch((err: unknown) =>
        setLoadError(errorMessage(err, "Could not reach the local runtime.")),
      );
  }, [refreshList]);

  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);

  return (
    <div className="settings-section">
      <h2>Local models</h2>
      <p>
        Download an open model from Hugging Face and run it entirely on this
        machine — nothing leaves your device. Downloaded models appear in the
        chat model picker.
      </p>

      {loadError ? (
        <Card>
          <p role="alert">{loadError}</p>
        </Card>
      ) : status === null ? (
        <Card>
          <p>Checking the local runtime…</p>
        </Card>
      ) : status.ollama_running ? (
        <RunningPanel
          models={models}
          onChanged={refreshList}
          onError={setLoadError}
        />
      ) : (
        <OllamaSetup onRecheck={refreshStatus} />
      )}
    </div>
  );
}

function OllamaSetup({ onRecheck }: { onRecheck: () => void }): ReactElement {
  return (
    <Card>
      <h3>Install Ollama to get started</h3>
      <p>
        Local models run through <strong>Ollama</strong>, a small free runtime.
        It isn&rsquo;t running yet.
      </p>
      <ol className="local-models-steps">
        <li>
          Install it from{" "}
          <a
            href="https://ollama.com/download"
            target="_blank"
            rel="noreferrer"
          >
            ollama.com/download
          </a>{" "}
          (macOS, Windows, Linux).
        </li>
        <li>Launch Ollama so it&rsquo;s running in the background.</li>
        <li>Come back here and re-check.</li>
      </ol>
      <Button variant="secondary" onClick={onRecheck}>
        Re-check
      </Button>
    </Card>
  );
}

function RunningPanel({
  models,
  onChanged,
  onError,
}: {
  models: readonly LocalModelSummary[];
  onChanged: () => void;
  onError: (message: string) => void;
}): ReactElement {
  return (
    <>
      <PullRow onDownloaded={onChanged} />
      <Card>
        <h3>Installed models</h3>
        {models.length === 0 ? (
          <p>No local models yet. Download one above.</p>
        ) : (
          <ul className="local-models-list">
            {models.map((model) => (
              <InstalledRow
                key={model.name}
                model={model}
                onRemoved={onChanged}
                onError={onError}
              />
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}

function PullRow({ onDownloaded }: { onDownloaded: () => void }): ReactElement {
  const [repo, setRepo] = useState("");
  const [quant, setQuant] = useState(DEFAULT_QUANT);
  const [pull, setPull] = useState<PullState | null>(null);
  const streamRef = useRef<LocalModelPullStream | null>(null);

  useEffect(
    () => () => {
      streamRef.current?.close();
    },
    [],
  );

  const onDownload = useCallback(async () => {
    const cleanRepo = repo.trim();
    const cleanQuant = quant.trim() || DEFAULT_QUANT;
    if (!cleanRepo || pull?.done === false) return;

    const base: PullState = {
      repo: cleanRepo,
      quant: cleanQuant,
      status: "starting",
      bytesTotal: null,
      bytesCompleted: null,
      speedBps: null,
      etaSeconds: null,
      sizeHint: null,
      error: null,
      done: false,
    };
    setPull(base);

    // Pre-download size heads-up (best-effort — a missing repo/quant surfaces
    // as an error here before we start streaming gigabytes).
    try {
      const size = await getLocalModelSize(cleanRepo, cleanQuant);
      setPull((prev) => (prev ? { ...prev, sizeHint: size.size_bytes } : prev));
    } catch (err: unknown) {
      setPull((prev) =>
        prev
          ? {
              ...prev,
              done: true,
              error: errorMessage(err, "Model not found."),
            }
          : prev,
      );
      return;
    }

    streamRef.current = streamLocalModelPull({
      repo: cleanRepo,
      quant: cleanQuant,
      onEvent: (event: LocalModelPullEvent) => {
        setPull((prev) =>
          prev
            ? {
                ...prev,
                status: event.status,
                bytesTotal: event.bytes_total ?? prev.bytesTotal,
                bytesCompleted: event.bytes_completed ?? prev.bytesCompleted,
                speedBps: event.speed_bps ?? prev.speedBps,
                etaSeconds: event.eta_seconds ?? prev.etaSeconds,
                error: event.error ?? prev.error,
                done: event.done || Boolean(event.error),
              }
            : prev,
        );
        if (event.done) {
          streamRef.current?.close();
          setRepo("");
          onDownloaded();
        }
      },
      onError: () => {
        setPull((prev) =>
          prev ? { ...prev, done: true, error: "Download interrupted." } : prev,
        );
        streamRef.current?.close();
      },
    });
  }, [repo, quant, pull, onDownloaded]);

  const downloading = pull !== null && !pull.done;

  return (
    <Card>
      <h3>Download a model</h3>
      <Field
        label="Hugging Face GGUF repo"
        hint="e.g. bartowski/Llama-3.2-1B-Instruct-GGUF"
      >
        <TextInput
          value={repo}
          placeholder="vendor/repo-GGUF"
          spellCheck={false}
          disabled={downloading}
          onChange={(event) => setRepo(event.target.value)}
        />
      </Field>
      <div className="settings-row">
        <Field
          label="Quantization"
          hint="Smaller = less memory, lower quality."
        >
          <TextInput
            value={quant}
            placeholder={DEFAULT_QUANT}
            spellCheck={false}
            disabled={downloading}
            onChange={(event) => setQuant(event.target.value)}
          />
        </Field>
        <Button
          variant="primary"
          disabled={downloading || !repo.trim()}
          onClick={() => void onDownload()}
        >
          {downloading ? "Downloading…" : "Download"}
        </Button>
      </div>
      {pull ? <PullProgress pull={pull} /> : null}
    </Card>
  );
}

function PullProgress({ pull }: { pull: PullState }): ReactElement {
  if (pull.error) {
    return (
      <p role="alert" className="local-models-error">
        Couldn&rsquo;t download: {pull.error}
      </p>
    );
  }
  const total = pull.bytesTotal ?? pull.sizeHint;
  const done = pull.bytesCompleted ?? 0;
  const fraction = total && total > 0 ? Math.min(1, done / total) : null;
  return (
    <div className="local-models-progress">
      <div className="local-models-progress__line">
        <span>
          {pull.done
            ? "Ready — available in the model picker."
            : humanStatus(pull.status)}
        </span>
        <span>
          {total ? formatBytes(total) : ""}
          {pull.speedBps ? ` · ${formatBytes(pull.speedBps)}/s` : ""}
          {pull.etaSeconds && !pull.done
            ? ` · ${formatEta(pull.etaSeconds)} left`
            : ""}
        </span>
      </div>
      <progress
        className="local-models-progress__bar"
        value={fraction ?? undefined}
        max={fraction !== null ? 1 : undefined}
      />
    </div>
  );
}

function InstalledRow({
  model,
  onRemoved,
  onError,
}: {
  model: LocalModelSummary;
  onRemoved: () => void;
  onError: (message: string) => void;
}): ReactElement {
  const [busy, setBusy] = useState(false);
  const onRemove = useCallback(() => {
    if (busy) return;
    setBusy(true);
    deleteLocalModel(model.name)
      .then(onRemoved)
      .catch((err: unknown) =>
        onError(errorMessage(err, "Could not remove the model.")),
      )
      .finally(() => setBusy(false));
  }, [busy, model.name, onRemoved, onError]);

  return (
    <li className="local-models-row">
      <div className="local-models-row__main">
        <strong>{model.name}</strong>
        <small>
          {formatBytes(model.size_bytes)}
          {model.quantization ? ` · ${model.quantization}` : ""}
          {model.parameter_size ? ` · ${model.parameter_size}` : ""}
        </small>
      </div>
      {model.run_placement ? (
        <span
          className="local-models-badge"
          data-placement={model.run_placement}
        >
          {placementLabel(model.run_placement)}
        </span>
      ) : null}
      <Button
        variant="danger"
        aria-label={`Remove ${model.name}`}
        disabled={busy}
        onClick={onRemove}
      >
        Remove
      </Button>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

function placementLabel(
  placement: NonNullable<LocalModelSummary["run_placement"]>,
): string {
  if (placement === "gpu") return "GPU";
  if (placement === "cpu") return "CPU — slower";
  return "GPU + CPU — slower";
}

function humanStatus(status: string): string {
  if (status === "starting") return "Starting…";
  if (status.startsWith("pulling") || status === "downloading") {
    return "Downloading…";
  }
  if (status.includes("verifying")) return "Verifying…";
  if (status.includes("writing")) return "Finishing…";
  return status;
}

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const exponent = Math.min(
    units.length - 1,
    Math.floor(Math.log(bytes) / Math.log(1024)),
  );
  const value = bytes / 1024 ** exponent;
  return `${value.toFixed(value >= 10 || exponent === 0 ? 0 : 1)} ${units[exponent]}`;
}

function formatEta(seconds: number): string {
  if (seconds < 60) return `${Math.ceil(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

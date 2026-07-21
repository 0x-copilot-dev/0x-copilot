// <LocalModelsPage /> — Settings → Models & keys → Local models (DESIGN-SPEC
// §4). Installed list (jade chip logo, name·param, "default local" chip, size,
// Run / Delete) + "Get another model → Download". Note: "Powered by your local
// runtime (Ollama). Inference uses your GPU/CPU — private and offline."
//
// Four states (FR-5.13/5.14): loading (probing) / load-error (role="alert" +
// Retry) / Ollama-not-running (setup steps) / Ollama-running (installed list,
// empty or populated).
//
// Substrate-agnostic (chat-surface boundary): NO fetch / window / Ollama / fs.
// Every runtime concern — the status/list probe, run, delete, set-default, and
// the download pull stream — is a HOST callback injected as a prop. The web /
// desktop host wires these to localModelsApi / the facade Transport. This page
// is pure presentation over that seam (same contract as <NotificationsPage>).
//
// Fits the PR-5.1 SettingsSurface section slot: render it from `renderSection`
// for the "local-models" slug. Built on PR-5.2 primitives (SetCard, SetNote,
// Krow, Badge). Colors resolve ONLY to design-system v2 tokens.

import { useState, type CSSProperties, type ReactElement } from "react";

import type {
  LocalModelSummary,
  LocalModelsStatus,
  PullLocalModelRequest,
} from "@0x-copilot/api-types";
import { Badge, Button } from "@0x-copilot/design-system";

import { SetCard, SetNote, Krow, Frow, SecTitle } from "./SettingsChrome";
import {
  DownloadLocalModelModal,
  type AvailableLocalModel,
  type LocalModelDownloadResult,
  type StartLocalModelPull,
} from "./DownloadLocalModelModal";
import { formatBytes, placementLabel } from "./localModelsFormat";

export interface LocalModelsPageProps {
  /** Capability probe; `null` while the first probe is in flight. */
  readonly status: LocalModelsStatus | null;
  /** Installed models (only meaningful when `status.ollama_running`). */
  readonly models: readonly LocalModelSummary[];
  /** Catalog offered in the download flow (DESIGN-SPEC §5 "pick from available"). */
  readonly availableModels?: readonly AvailableLocalModel[];
  /** Name of the model shown with the jade "default local" chip. */
  readonly defaultLocalModelName?: string | null;
  /** Set when the status/list probe failed — renders a role="alert" + Retry. */
  readonly loadError?: string | null;

  // --- host seam (callbacks) -------------------------------------------------
  /** Re-probe the runtime (setup "Re-check" + load-error "Retry"). */
  readonly onRecheck: () => void;
  /** Refresh the installed list after a successful download. */
  readonly onDownloaded: (result: LocalModelDownloadResult) => void;
  /** Open the pull SSE stream (forwarded to the download modal). */
  readonly startPull: StartLocalModelPull;
  /**
   * Optional pre-download size probe (forwarded to the download modal's custom
   * free-text path). Host wires it to `LocalModelsPort.size`; when omitted, a
   * custom pull starts without a size heads-up (degrades gracefully).
   */
  readonly resolveSize?: (request: PullLocalModelRequest) => Promise<number>;
  /** Remove an installed model. */
  readonly onDelete: (name: string) => void;
  /** Run / select a model (host decides — e.g. set active in the picker). */
  readonly onRun?: (name: string) => void;
  /** Make a model the default local model (renders "Set default" when given). */
  readonly onSetDefault?: (name: string) => void;
}

// Section wrapper: the SecTitle heading above, then the sub-cards, stacked
// (design `.set-sec` — the section title sits above its cards).
const pageStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-lg)",
};

const listStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  // Flat Krow rows divide with a top hairline — no inter-row gap (design).
  gap: 0,
  margin: 0,
  padding: 0,
  listStyle: "none",
};

// Jade chip glyph — DESIGN-SPEC §4 "jade chip logo" (jade = default-local /
// success semantic; the one place a model logo is tinted, not neutralized).
const chipLogoStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 18,
  height: 18,
  borderRadius: "var(--radius-sm)",
  backgroundColor: "var(--color-success-bg)",
  color: "var(--color-success)",
  fontSize: "var(--font-size-2xs)",
  fontWeight: "var(--font-weight-semibold)",
};

function ChipLogo(): ReactElement {
  return (
    <span style={chipLogoStyle} aria-hidden="true">
      ▦
    </span>
  );
}

export function LocalModelsPage({
  status,
  models,
  availableModels = [],
  defaultLocalModelName,
  loadError,
  onRecheck,
  onDownloaded,
  startPull,
  resolveSize,
  onDelete,
  onRun,
  onSetDefault,
}: LocalModelsPageProps): ReactElement {
  const [downloadOpen, setDownloadOpen] = useState(false);

  let body: ReactElement;
  if (loadError) {
    body = (
      <SetCard>
        <SetNote tone="danger" role="alert">
          {loadError}
        </SetNote>
        <div>
          <Button
            variant="secondary"
            onClick={onRecheck}
            data-testid="local-models-retry"
          >
            Retry
          </Button>
        </div>
      </SetCard>
    );
  } else if (status === null) {
    body = (
      <SetCard>
        <p data-testid="local-models-loading">Checking the local runtime…</p>
      </SetCard>
    );
  } else if (!status.ollama_running) {
    body = <OllamaSetup onRecheck={onRecheck} />;
  } else {
    body = (
      <RunningCard
        models={models}
        defaultLocalModelName={defaultLocalModelName ?? null}
        onOpenDownload={() => setDownloadOpen(true)}
        onDelete={onDelete}
        onRun={onRun}
        onSetDefault={onSetDefault}
      />
    );
  }

  return (
    <div style={pageStyle} data-testid="local-models-page">
      <SecTitle
        title="Local models"
        description="Run a model entirely on this machine — no key, no network, nothing leaves your box."
      />
      {body}
      <DownloadLocalModelModal
        open={downloadOpen}
        onClose={() => setDownloadOpen(false)}
        availableModels={availableModels}
        startPull={startPull}
        resolveSize={resolveSize}
        onFinish={(result) => {
          setDownloadOpen(false);
          onDownloaded(result);
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Ollama-not-running setup steps (FR-5.14 state 1)
// ---------------------------------------------------------------------------

function OllamaSetup({ onRecheck }: { onRecheck: () => void }): ReactElement {
  return (
    <SetCard
      title="Install Ollama to get started"
      meta="Local models run through Ollama, a small free runtime. It isn’t running yet."
      data-testid="local-models-setup"
    >
      <ol
        style={{
          margin: 0,
          paddingLeft: "1.2em",
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-xs)",
          fontSize: "var(--font-size-sm)",
          color: "var(--color-text-muted)",
        }}
      >
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
        <li>Launch Ollama so it’s running in the background.</li>
        <li>Come back here and re-check.</li>
      </ol>
      <div>
        <Button
          variant="secondary"
          onClick={onRecheck}
          data-testid="local-models-recheck"
        >
          Re-check
        </Button>
      </div>
    </SetCard>
  );
}

// ---------------------------------------------------------------------------
// Ollama-running installed list + "Get another model" (FR-5.14 states 2/3)
// ---------------------------------------------------------------------------

function RunningCard({
  models,
  defaultLocalModelName,
  onOpenDownload,
  onDelete,
  onRun,
  onSetDefault,
}: {
  readonly models: readonly LocalModelSummary[];
  readonly defaultLocalModelName: string | null;
  readonly onOpenDownload: () => void;
  readonly onDelete: (name: string) => void;
  readonly onRun?: (name: string) => void;
  readonly onSetDefault?: (name: string) => void;
}): ReactElement {
  return (
    <SetCard title="Installed" meta={`${models.length} models`}>
      <SetNote
        icon={<span aria-hidden="true">🔒</span>}
        data-testid="local-models-privacy-note"
      >
        Powered by your local runtime (Ollama). Inference uses your GPU/CPU —
        private and offline.
      </SetNote>
      {models.length === 0 ? (
        <SetNote data-testid="local-models-empty">
          No local models yet. Download one below.
        </SetNote>
      ) : (
        <ul style={listStyle} data-testid="local-models-list">
          {models.map((model) => (
            <li key={model.name}>
              <InstalledRow
                model={model}
                isDefault={model.name === defaultLocalModelName}
                onDelete={onDelete}
                onRun={onRun}
                onSetDefault={onSetDefault}
              />
            </li>
          ))}
        </ul>
      )}
      <Frow
        label="Get another model"
        hint="Downloaded models appear in the chat model picker."
      >
        <Button
          variant="secondary"
          onClick={onOpenDownload}
          data-testid="local-models-get-another"
        >
          Download
        </Button>
      </Frow>
    </SetCard>
  );
}

function InstalledRow({
  model,
  isDefault,
  onDelete,
  onRun,
  onSetDefault,
}: {
  readonly model: LocalModelSummary;
  readonly isDefault: boolean;
  readonly onDelete: (name: string) => void;
  readonly onRun?: (name: string) => void;
  readonly onSetDefault?: (name: string) => void;
}): ReactElement {
  const sub = [
    model.parameter_size ?? null,
    formatBytes(model.size_bytes),
    model.quantization ?? null,
    model.run_placement ? placementLabel(model.run_placement) : null,
  ]
    .filter((part): part is string => Boolean(part))
    .join(" · ");

  return (
    <Krow
      logo={<ChipLogo />}
      data-testid="local-models-row"
      data-name={model.name}
      name={
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "var(--space-sm)",
          }}
        >
          {model.name}
          {isDefault ? (
            <Badge tone="success" data-testid="local-models-default-chip">
              default local
            </Badge>
          ) : null}
        </span>
      }
      sub={sub}
      actions={
        <>
          {onRun ? (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => onRun(model.name)}
              aria-label={`Run ${model.name}`}
            >
              Run
            </Button>
          ) : null}
          {onSetDefault && !isDefault ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onSetDefault(model.name)}
              aria-label={`Set ${model.name} as default local model`}
            >
              Set default
            </Button>
          ) : null}
          <Button
            variant="danger"
            size="sm"
            onClick={() => onDelete(model.name)}
            aria-label={`Delete ${model.name}`}
          >
            Delete
          </Button>
        </>
      }
    />
  );
}

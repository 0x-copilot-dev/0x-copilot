// Settings → Models (PR-3D). The curation surface for the two-tier picker:
// the full catalog per provider with search + per-model enable/disable
// toggles + metadata badges, plus "Add custom model" and "Use recommended
// defaults". Presentational — data-binding lives in the injected `ModelsPort`.
//
// The composer picker (PR-3E) shows only the models enabled here. Local models
// and the workspace default model are always enabled server-side, so their
// toggles render disabled-on rather than pretending they can be turned off.

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import { Badge, Button, TextInput } from "@0x-copilot/design-system";

import { SetCard, SetNote, SecHead } from "./SettingsChrome";
import {
  contextLabel,
  filterModels,
  groupModelsByProvider,
  priceLabel,
  type CatalogModel,
  type ModelsPort,
} from "./data/models";

export interface ModelsPageProps {
  readonly port: ModelsPort;
  readonly onToast?: (message: string) => void;
}

export const MODELS_PAGE_NOTE =
  "Choose which models appear in the composer picker. New models are shown by default; turn any off to hide it. Local models and your default model always stay available.";

function toMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === "string" && err) return err;
  return fallback;
}

/** Local + default models can't be disabled — their toggle is on and locked. */
function isLocked(model: CatalogModel): boolean {
  return model.provider === "ollama";
}

const listStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-xs)",
};

const rowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: "var(--space-md)",
  padding: "8px 4px",
  borderTop: "1px solid var(--color-border)",
};

const nameColStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "2px",
  minWidth: 0,
};

const nameStyle: CSSProperties = {
  fontSize: "var(--font-size-sm)",
  fontWeight: "var(--font-weight-medium)",
  color: "var(--color-text)",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const metaRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: "var(--space-xs)",
  alignItems: "center",
};

const metaTextStyle: CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-xs)",
  color: "var(--color-text-muted)",
};

const groupHeadStyle: CSSProperties = {
  display: "flex",
  alignItems: "baseline",
  justifyContent: "space-between",
  gap: "var(--space-sm)",
  marginTop: "var(--space-md)",
};

const searchRowStyle: CSSProperties = {
  display: "flex",
  gap: "var(--space-sm)",
  alignItems: "center",
  marginBottom: "var(--space-sm)",
};

const addRowStyle: CSSProperties = {
  display: "flex",
  gap: "var(--space-sm)",
  alignItems: "center",
  marginTop: "var(--space-md)",
};

const toggleBtnStyle: CSSProperties = {
  flex: "0 0 auto",
  minWidth: "72px",
};

export function ModelsPage({ port, onToast }: ModelsPageProps): ReactElement {
  const [models, setModels] = useState<readonly CatalogModel[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [customId, setCustomId] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    setModels(null);
    setLoadError(null);
    port
      .list()
      .then((next) => setModels(next))
      .catch((err: unknown) =>
        setLoadError(toMessage(err, "Could not load the model catalog.")),
      );
  }, [port]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const enabledIds = useMemo(
    () => (models ?? []).filter((m) => m.enabled).map((m) => m.id),
    [models],
  );

  const persist = useCallback(
    (next: readonly string[] | null, toastMsg: string) => {
      setBusy(true);
      port
        .setEnabled(next)
        .then((refreshed) => {
          setModels(refreshed);
          onToast?.(toastMsg);
        })
        .catch((err: unknown) =>
          onToast?.(toMessage(err, "Couldn't save your model selection.")),
        )
        .finally(() => setBusy(false));
    },
    [port, onToast],
  );

  const toggle = useCallback(
    (model: CatalogModel) => {
      if (isLocked(model)) return;
      const set = new Set(enabledIds);
      if (model.enabled) set.delete(model.id);
      else set.add(model.id);
      persist(
        [...set],
        model.enabled ? `${model.name} hidden.` : `${model.name} enabled.`,
      );
    },
    [enabledIds, persist],
  );

  const addCustom = useCallback(() => {
    const id = customId.trim();
    if (id === "") return;
    const set = new Set(enabledIds);
    set.add(id);
    setCustomId("");
    persist([...set], `${id} added.`);
  }, [customId, enabledIds, persist]);

  if (loadError !== null) {
    return (
      <SetCard title="Models" meta={MODELS_PAGE_NOTE} data-testid="models-page">
        <p role="alert" data-testid="models-error" style={metaTextStyle}>
          {loadError}
        </p>
        <Button
          variant="secondary"
          onClick={refresh}
          data-testid="models-retry"
        >
          Retry
        </Button>
      </SetCard>
    );
  }

  if (models === null) {
    return (
      <SetCard title="Models" meta={MODELS_PAGE_NOTE} data-testid="models-page">
        <SetNote data-testid="models-loading">Loading the catalog…</SetNote>
      </SetCard>
    );
  }

  const filtered = filterModels(models, query);
  const groups = groupModelsByProvider(filtered);

  return (
    <SetCard
      title="Models"
      meta={MODELS_PAGE_NOTE}
      data-testid="models-page"
      actions={
        <Button
          variant="ghost"
          disabled={busy}
          onClick={() => persist(null, "Restored the recommended defaults.")}
          data-testid="models-reset"
        >
          Use recommended defaults
        </Button>
      }
    >
      <div style={searchRowStyle}>
        <TextInput
          type="search"
          placeholder={`Search ${models.length} models…`}
          value={query}
          aria-label="Search models"
          data-testid="models-search"
          onChange={(event) => setQuery(event.currentTarget.value)}
        />
      </div>

      {groups.length === 0 ? (
        <SetNote data-testid="models-empty">No models match “{query}”.</SetNote>
      ) : (
        groups.map((group) => (
          <section
            key={group.provider}
            data-testid={`models-group-${group.provider}`}
          >
            <div style={groupHeadStyle}>
              <SecHead>{group.label}</SecHead>
              <span style={metaTextStyle}>
                {group.models.filter((m) => m.enabled).length}/
                {group.models.length} on
              </span>
            </div>
            <div style={listStyle}>
              {group.models.map((model) => {
                const price = priceLabel(model);
                const ctx = contextLabel(model);
                const locked = isLocked(model);
                return (
                  <div
                    key={model.id}
                    style={rowStyle}
                    data-testid={`models-row-${model.id}`}
                  >
                    <div style={nameColStyle}>
                      <span style={nameStyle} title={model.id}>
                        {model.name}
                      </span>
                      <div style={metaRowStyle}>
                        {ctx ? <span style={metaTextStyle}>{ctx}</span> : null}
                        {price ? (
                          <span style={metaTextStyle}>{price}</span>
                        ) : null}
                        {model.supports_reasoning ? (
                          <Badge tone="accent">reasoning</Badge>
                        ) : null}
                        {model.supports_tools ? (
                          <Badge tone="neutral">tools</Badge>
                        ) : null}
                        {!model.configured ? (
                          <Badge tone="warning">needs key</Badge>
                        ) : null}
                      </div>
                    </div>
                    <Button
                      variant={model.enabled ? "secondary" : "ghost"}
                      disabled={locked || busy}
                      aria-pressed={model.enabled}
                      style={toggleBtnStyle}
                      data-testid={`models-toggle-${model.id}`}
                      onClick={() => toggle(model)}
                    >
                      {locked ? "Always on" : model.enabled ? "On" : "Off"}
                    </Button>
                  </div>
                );
              })}
            </div>
          </section>
        ))
      )}

      <div style={addRowStyle}>
        <TextInput
          placeholder="Add a model by id (e.g. openrouter slug)…"
          value={customId}
          aria-label="Custom model id"
          data-testid="models-custom-input"
          onChange={(event) => setCustomId(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") addCustom();
          }}
        />
        <Button
          variant="secondary"
          disabled={busy || customId.trim() === ""}
          onClick={addCustom}
          data-testid="models-custom-add"
        >
          Add
        </Button>
      </div>
    </SetCard>
  );
}

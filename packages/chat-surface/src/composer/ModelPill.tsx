import { Menu } from "@0x-copilot/design-system";
import type { ModelCatalogModel } from "@0x-copilot/api-types";
import { useMemo, useRef, useState, type ReactElement } from "react";

import { providerLabel } from "../settings/data/models";
import {
  checkProviderKeyFormat,
  PROVIDER_CATALOG,
  providerCatalogEntry,
  type ProviderKeysPort,
} from "../settings/data/providerKeys";
import type { FirstRunKeyProvider } from "../onboarding/firstRun";
import {
  KeyForm,
  type KeyFormConnected,
  type KeyFormFormatCheck,
} from "../onboarding/KeyForm";

export type ModelPillModel = ModelCatalogModel & { disabled?: boolean };

// The BYOK provider rows offered by the inline add-key sub-view (contract §1).
// Derived from the shared `PROVIDER_CATALOG` so the composer and Settings never
// drift: only contract-backed, shippable providers (no coming-soon, no custom
// endpoint). `dotColor`/`meta` are the KeyForm tri-toggle's presentation data —
// swatches are the FTUE hexes (SPEC §Data) with a token fallback.
const KEY_PROVIDER_DOT: Record<string, string> = {
  anthropic: "#d97757",
  openai: "#6aa88f",
  openrouter: "#9a7fd6",
  google: "#4285f4",
};

const MODEL_PILL_KEY_PROVIDERS: readonly FirstRunKeyProvider[] =
  PROVIDER_CATALOG.filter(
    (entry) =>
      entry.contractBacked &&
      entry.comingSoon !== true &&
      entry.isCustom !== true,
  ).map((entry) => ({
    id: entry.id,
    label: entry.label,
    meta: entry.models[0] ?? "",
    dotColor: KEY_PROVIDER_DOT[entry.id] ?? "var(--color-text-muted)",
    placeholder: entry.placeholder,
    keyPrefix: entry.keyPrefix,
  }));

// Generic format check for the inline add-key flow — bridges the KeyForm's
// `FirstRunKeyProvider` row back onto the shared `checkProviderKeyFormat` so the
// composer's verdicts match Settings exactly (no FTUE-specific coupling).
const modelPillFormatCheck: KeyFormFormatCheck = (provider, apiKey) => {
  const entry = providerCatalogEntry(provider.id) ?? {
    id: provider.id,
    label: provider.label,
    placeholder: provider.placeholder,
    keyPrefix: provider.keyPrefix,
    models: [],
    contractBacked: true,
  };
  const result = checkProviderKeyFormat(entry, apiKey);
  return result.ok
    ? { ok: true }
    : { ok: false, error: result.error ?? "Enter a valid key." };
};

export interface ModelPillProps {
  models: ModelPillModel[];
  value: string;
  onChange: (modelId: string) => void;
  disabled?: boolean;
  /**
   * When provided, renders a "Custom OpenRouter model" input at the foot of
   * the menu. The submitted `vendor/model` slug is handed back so the
   * container can register it as a selectable model and select it. Omitted
   * where custom slugs don't apply.
   */
  onAddCustom?: (slug: string) => void;
  /** Footer deep-link → Settings → Provider keys (v3 "Add a provider key →"). */
  onAddProviderKey?: () => void;
  /** Footer deep-link → Settings → Local models (v3 "Get local models →"). */
  onGetLocalModels?: () => void;
  /**
   * When provided, the "Add a provider key" footer opens an inline `<KeyForm>`
   * sub-view INSIDE this popover instead of firing `onAddProviderKey` — the key
   * is saved through this port and the popover closes on connect. When omitted,
   * the footer keeps the deep-link behaviour (`onAddProviderKey`). The two are
   * mutually preferred: `providerKeysPort` wins when both are set.
   */
  providerKeysPort?: ProviderKeysPort;
  /**
   * Fired after a successful inline add-key connect (the refresh seam). The host
   * re-reads its model catalog so the freshly-keyed provider's models appear and
   * can be selected. Optional — the sub-view still closes without it.
   */
  onProviderKeyAdded?: (result: KeyFormConnected) => void;
}

const LOCAL_PROVIDERS = new Set(["ollama"]);

function isLocal(model: ModelPillModel): boolean {
  return LOCAL_PROVIDERS.has(model.provider);
}

/** The mono sub-line under a row, in the v3 idiom. */
function subLine(model: ModelPillModel): string {
  if (isLocal(model)) return "local · never leaves this machine";
  const label = providerLabel(model.provider);
  return model.configured ? `${label} · your key` : `${label} · needs key`;
}

/**
 * Composer model picker (v3 design). A quiet, upward popover grouped into
 * "Your keys" (configured cloud models) and "Local · on-device", with footer
 * deep-links into Settings. Deliberately NOT searchable — the composer shows
 * the short curated (enabled) list; the full catalog + search + toggles live
 * in Settings → Models. Only enabled models are listed (`enabled !== false`;
 * undefined = legacy curated-in), with the current selection always visible.
 * Rows flagged `disabled` (no key) render but aren't selectable.
 */
export function ModelPill({
  models,
  value,
  onChange,
  disabled,
  onAddCustom,
  onAddProviderKey,
  onGetLocalModels,
  providerKeysPort,
  onProviderKeyAdded,
}: ModelPillProps): ReactElement {
  const [open, setOpen] = useState(false);
  const [addKeyOpen, setAddKeyOpen] = useState(false);
  const [customSlug, setCustomSlug] = useState("");
  const buttonRef = useRef<HTMLButtonElement>(null);

  // Closing the popover always resets the add-key sub-view so it never re-opens
  // to a stale form on the next open.
  const closeMenu = (): void => {
    setOpen(false);
    setAddKeyOpen(false);
  };

  const selected =
    models.find((model) => model.id === value) ?? models[0] ?? null;

  // Only enabled models are offered (undefined `enabled` = legacy/curated-in);
  // the current selection is always kept visible even if curated out.
  const visible = useMemo(
    () => models.filter((m) => m.enabled !== false || m.id === value),
    [models, value],
  );
  const cloud = useMemo(() => visible.filter((m) => !isLocal(m)), [visible]);
  const local = useMemo(() => visible.filter(isLocal), [visible]);

  const commit = (modelId: string): void => {
    onChange(modelId);
    closeMenu();
  };

  const renderRow = (model: ModelPillModel): ReactElement => {
    const active = model.id === value;
    return (
      <button
        key={model.id}
        type="button"
        role="menuitemradio"
        aria-checked={active}
        disabled={model.disabled}
        className="atlas-model-pill__item"
        data-active={active || undefined}
        data-off={model.disabled || undefined}
        onClick={() => {
          if (model.disabled) return;
          commit(model.id);
        }}
      >
        <span
          className="atlas-model-pill__badge-lg"
          aria-hidden="true"
          data-local={isLocal(model) || undefined}
        >
          {isLocal(model) ? "◇" : model.provider.slice(0, 1).toUpperCase()}
        </span>
        <span className="atlas-model-pill__col">
          <span className="atlas-model-pill__row">
            <span className="atlas-model-pill__nm">{model.name}</span>
            {model.supports_reasoning ? (
              <span className="atlas-model-pill__badge" data-kind="reasoning">
                reasoning
              </span>
            ) : null}
          </span>
          <span className="atlas-model-pill__sub">{subLine(model)}</span>
        </span>
        <span className="atlas-model-pill__rad" aria-hidden="true">
          {active ? (
            <svg viewBox="0 0 24 24" width="10" height="10" fill="none">
              <polyline
                points="5 12 10 17 19 7"
                stroke="currentColor"
                strokeWidth="3"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          ) : null}
        </span>
      </button>
    );
  };

  // The add-key affordance shows for either the deep-link callback OR an inline
  // port; the inline port takes precedence when both are supplied.
  const hasAddKey =
    providerKeysPort !== undefined || onAddProviderKey !== undefined;
  const hasFooter = hasAddKey || onGetLocalModels !== undefined;

  const handleAddKeyClick = (): void => {
    if (providerKeysPort !== undefined) {
      setAddKeyOpen(true);
      return;
    }
    closeMenu();
    onAddProviderKey?.();
  };

  const renderMenuBody = (): ReactElement => (
    <>
      {visible.length === 0 ? (
        <div className="atlas-model-pill__empty">No models available.</div>
      ) : (
        <>
          {cloud.length > 0 ? (
            <div className="atlas-model-pill__group">
              <div className="atlas-model-pill__group-head" aria-hidden="true">
                Your keys
              </div>
              {cloud.map(renderRow)}
            </div>
          ) : null}
          {local.length > 0 ? (
            <div className="atlas-model-pill__group">
              <div className="atlas-model-pill__group-head" aria-hidden="true">
                Local · on-device
              </div>
              {local.map(renderRow)}
            </div>
          ) : null}
        </>
      )}
      {hasFooter ? (
        <div className="atlas-model-pill__footer">
          {hasAddKey ? (
            <a
              className="atlas-model-pill__footer-link"
              role="button"
              tabIndex={0}
              onClick={handleAddKeyClick}
            >
              Add a provider key →
            </a>
          ) : null}
          {hasAddKey && onGetLocalModels ? (
            <span className="atlas-model-pill__footer-sp" />
          ) : null}
          {onGetLocalModels ? (
            <a
              className="atlas-model-pill__footer-link"
              role="button"
              tabIndex={0}
              onClick={() => {
                closeMenu();
                onGetLocalModels();
              }}
            >
              Get local models →
            </a>
          ) : null}
        </div>
      ) : null}
      {onAddCustom ? (
        <form
          className="atlas-model-pill__custom"
          onSubmit={(event) => {
            event.preventDefault();
            const slug = customSlug.trim();
            if (!slug) return;
            onAddCustom(slug);
            setCustomSlug("");
            closeMenu();
          }}
        >
          <label
            className="atlas-model-pill__custom-label"
            htmlFor="atlas-model-pill-custom"
          >
            Custom OpenRouter model
          </label>
          <div className="atlas-model-pill__custom-row">
            <input
              id="atlas-model-pill-custom"
              type="text"
              className="atlas-model-pill__custom-input"
              placeholder="vendor/model — e.g. anthropic/claude-3.7-sonnet"
              value={customSlug}
              spellCheck={false}
              autoComplete="off"
              onChange={(event) => setCustomSlug(event.target.value)}
            />
            <button
              type="submit"
              className="atlas-model-pill__custom-add"
              disabled={!customSlug.trim()}
            >
              Add
            </button>
          </div>
        </form>
      ) : null}
    </>
  );

  return (
    <div className="atlas-model-pill__root">
      <button
        ref={buttonRef}
        type="button"
        className="atlas-model-pill"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={selected ? `Model: ${selected.name}` : "Select a model"}
        disabled={disabled}
        onClick={() =>
          setOpen((current) => {
            if (current) setAddKeyOpen(false);
            return !current;
          })
        }
        data-tooltip="Choose model"
        data-tooltip-placement="bottom"
      >
        <span className="atlas-model-pill__dot" aria-hidden="true" />
        <span className="atlas-model-pill__name">
          {selected?.name ?? "Model"}
        </span>
        <svg
          aria-hidden="true"
          className="atlas-model-pill__caret"
          viewBox="0 0 24 24"
          width="12"
          height="12"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <polyline points="9 6 15 12 9 18" />
        </svg>
      </button>
      <Menu
        open={open}
        onClose={closeMenu}
        anchorRef={buttonRef}
        side="up"
        align="left"
        className="atlas-model-pill__menu"
      >
        {addKeyOpen && providerKeysPort !== undefined ? (
          <div className="atlas-model-pill__addkey">
            <button
              type="button"
              className="atlas-model-pill__addkey-back"
              onClick={() => setAddKeyOpen(false)}
            >
              ← Back
            </button>
            <KeyForm
              port={providerKeysPort}
              providers={MODEL_PILL_KEY_PROVIDERS}
              formatCheck={modelPillFormatCheck}
              placeholder="sk-…  paste your API key"
              note="stored securely in your keychain — never uploaded"
              connectLabel="Connect"
              onCancel={() => setAddKeyOpen(false)}
              onConnected={(result) => {
                setAddKeyOpen(false);
                closeMenu();
                onProviderKeyAdded?.(result);
              }}
            />
          </div>
        ) : (
          <>{renderMenuBody()}</>
        )}
      </Menu>
    </div>
  );
}

import { Menu } from "@0x-copilot/design-system";
import type { ModelCatalogModel } from "@0x-copilot/api-types";
import { useMemo, useRef, useState, type ReactElement } from "react";

import { providerLabel } from "../settings/data/models";

export type ModelPillModel = ModelCatalogModel & { disabled?: boolean };

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
}: ModelPillProps): ReactElement {
  const [open, setOpen] = useState(false);
  const [customSlug, setCustomSlug] = useState("");
  const buttonRef = useRef<HTMLButtonElement>(null);

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
    setOpen(false);
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

  const hasFooter =
    onAddProviderKey !== undefined || onGetLocalModels !== undefined;

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
        onClick={() => setOpen((current) => !current)}
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
        onClose={() => setOpen(false)}
        anchorRef={buttonRef}
        side="up"
        align="left"
        className="atlas-model-pill__menu"
      >
        {visible.length === 0 ? (
          <div className="atlas-model-pill__empty">No models available.</div>
        ) : (
          <>
            {cloud.length > 0 ? (
              <div className="atlas-model-pill__group">
                <div
                  className="atlas-model-pill__group-head"
                  aria-hidden="true"
                >
                  Your keys
                </div>
                {cloud.map(renderRow)}
              </div>
            ) : null}
            {local.length > 0 ? (
              <div className="atlas-model-pill__group">
                <div
                  className="atlas-model-pill__group-head"
                  aria-hidden="true"
                >
                  Local · on-device
                </div>
                {local.map(renderRow)}
              </div>
            ) : null}
          </>
        )}
        {hasFooter ? (
          <div className="atlas-model-pill__footer">
            {onAddProviderKey ? (
              <a
                className="atlas-model-pill__footer-link"
                role="button"
                tabIndex={0}
                onClick={() => {
                  setOpen(false);
                  onAddProviderKey();
                }}
              >
                Add a provider key →
              </a>
            ) : null}
            {onAddProviderKey && onGetLocalModels ? (
              <span className="atlas-model-pill__footer-sp" />
            ) : null}
            {onGetLocalModels ? (
              <a
                className="atlas-model-pill__footer-link"
                role="button"
                tabIndex={0}
                onClick={() => {
                  setOpen(false);
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
              setOpen(false);
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
      </Menu>
    </div>
  );
}

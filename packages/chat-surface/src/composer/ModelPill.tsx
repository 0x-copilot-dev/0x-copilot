import { Menu } from "@0x-copilot/design-system";
import type { ModelCatalogModel } from "@0x-copilot/api-types";
import { useMemo, useRef, useState, type ReactElement } from "react";

import { Icon } from "../icons/Icon";
import { ProviderMark, providerBrandColor } from "../icons/providerMarks";
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
  /**
   * On-disk size of each installed LOCAL model, in bytes, so a local row can
   * read the design's "42 GB · never leaves this machine" instead of the
   * placeholder "local · …". Keyed by the model's Ollama tag / catalog id;
   * lookups try `model_name`, then `id`, then `name`.
   *
   * Deliberately a SIDE MAP rather than a field on `ModelCatalogModel`: sizes
   * come from `GET /v1/local-models` (`LocalModelSummary.size_bytes`), a
   * different endpoint from the model catalog, and `ModelCatalogModel` is a wire
   * contract shared with the backend. The host binder — which already reads both
   * endpoints — does the join by name and passes the result down here.
   */
  localModelSizes?: Readonly<Record<string, number>>;
}

const LOCAL_PROVIDERS = new Set(["ollama"]);

/** Dot hue when nothing is selected yet — the quiet neutral, not the accent. */
const PROVIDER_DOT_UNSELECTED = "var(--color-text-subtle)";

function isLocal(model: ModelPillModel): boolean {
  return LOCAL_PROVIDERS.has(model.provider);
}

/**
 * Human-readable on-disk size, in the design's idiom ("42 GB", "4.7 GB").
 * Decimal units (1 GB = 1e9 B) — the same convention Ollama/Hugging Face quote
 * model weights in, so the number matches what the download surface showed.
 * Returns `null` for anything that isn't a positive finite byte count, so a
 * missing/garbage size falls back to the generic sub-line instead of "0 GB".
 */
export function formatModelSize(bytes: number | undefined): string | null {
  if (bytes === undefined || !Number.isFinite(bytes) || bytes <= 0) return null;
  const gb = bytes / 1e9;
  if (gb >= 10) return `${Math.round(gb)} GB`;
  if (gb >= 1) return `${Math.round(gb * 10) / 10} GB`;
  return `${Math.round(bytes / 1e6)} MB`;
}

/** Byte size for a local model, looked up across the ids a host might key on. */
function localModelSize(
  model: ModelPillModel,
  sizes: Readonly<Record<string, number>> | undefined,
): number | undefined {
  if (sizes === undefined) return undefined;
  return sizes[model.model_name] ?? sizes[model.id] ?? sizes[model.name];
}

/** The mono sub-line under a row, in the v3 idiom. */
function subLine(
  model: ModelPillModel,
  sizes: Readonly<Record<string, number>> | undefined,
): string {
  if (isLocal(model)) {
    // Design: "42 GB · never leaves this machine". Without a joined size the
    // lead reverts to the honest generic "local" rather than inventing a number.
    const size = formatModelSize(localModelSize(model, sizes));
    return `${size ?? "local"} · never leaves this machine`;
  }
  const label = providerLabel(model.provider);
  return model.configured ? `${label} · your key` : `${label} · needs key`;
}

/**
 * Composer model picker (v3 design). A quiet, upward popover built entirely
 * from the shared `.ui-pop*` recipe: a `Model — this chat` header, ONE scroll
 * region (`.ui-pop__list`, capped at the design's 264px so a long catalog can
 * never grow the frame off-screen), mono group headings, and a pinned footer.
 * Grouped into "Your keys" (configured cloud models) and "Local · on-device",
 * with footer deep-links into Settings. Deliberately NOT searchable — the
 * composer shows the short curated (enabled) list; the full catalog + search +
 * toggles live in Settings → Models. Only enabled models are listed
 * (`enabled !== false`; undefined = legacy curated-in), with the current
 * selection always visible. Rows flagged `disabled` (no key) render but aren't
 * selectable.
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
  localModelSizes,
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

  // One catalog row, in the shared `.ui-pop-row` idiom (design `.pop-row`):
  // 24px provider badge · name + mono sub-line · selection radio. The legacy
  // `.atlas-model-pill__*` classes ride along because the WEB host forks these
  // rules into its own `styles.css` (composer.css is desktop-only) — dropping
  // them would silently restyle web, which is out of scope here.
  //
  // `data-on` drives the shared recipe's radio fill; `data-active` is kept for
  // the web fork's selector. Neither paints the ROW any more (design: selection
  // is the filled radio ONLY, so it can't be confused with hover).
  const renderRow = (model: ModelPillModel): ReactElement => {
    const active = model.id === value;
    return (
      <button
        key={model.id}
        type="button"
        role="menuitemradio"
        aria-checked={active}
        disabled={model.disabled}
        className="ui-pop-row atlas-model-pill__item"
        data-on={active || undefined}
        data-active={active || undefined}
        data-off={model.disabled || undefined}
        onClick={() => {
          if (model.disabled) return;
          commit(model.id);
        }}
      >
        <span
          className="ui-pop-row__lg atlas-model-pill__badge-lg"
          aria-hidden="true"
        >
          {/* Real bundled brand mark when we have one; two-letter initials when
              we don't. Local (Ollama) resolves to the design's chip glyph. */}
          <ProviderMark
            provider={model.provider}
            label={providerLabel(model.provider)}
            size={13}
          />
        </span>
        <span className="ui-pop-row__m atlas-model-pill__col">
          <span className="ui-pop-row__nm atlas-model-pill__row">
            <span className="ui-pop-row__txt atlas-model-pill__nm">
              {model.name}
            </span>
            {model.supports_reasoning ? (
              <span className="atlas-model-pill__badge" data-kind="reasoning">
                reasoning
              </span>
            ) : null}
          </span>
          <span className="ui-pop-row__sb atlas-model-pill__sub">
            {subLine(model, localModelSizes)}
          </span>
        </span>
        <span
          className="ui-pop-row__rad atlas-model-pill__rad"
          aria-hidden="true"
        >
          {active ? <Icon name="check" size={9} strokeWidth={3} /> : null}
        </span>
      </button>
    );
  };

  // The add-key affordance shows for either the deep-link callback OR an inline
  // port; the deep-link (navigate to Settings) takes precedence when both are set.
  const hasAddKey =
    providerKeysPort !== undefined || onAddProviderKey !== undefined;
  const hasFooter = hasAddKey || onGetLocalModels !== undefined;

  const handleAddKeyClick = (): void => {
    // Navigation wins: the "Add a provider key" footer routes to the one
    // Settings → Provider keys surface whenever the host supplies the deep-link,
    // rather than opening an inline form in the popover. The inline port stays a
    // fallback for hosts that don't wire navigation.
    if (onAddProviderKey !== undefined) {
      closeMenu();
      onAddProviderKey();
      return;
    }
    if (providerKeysPort !== undefined) {
      setAddKeyOpen(true);
    }
  };

  const renderMenuBody = (): ReactElement => (
    <>
      {/* Design `.pop__h` — the popover says what it is, and the meta says what
          the choice scopes to ("this chat", not a global default). */}
      <div className="ui-pop__h">
        Model <span className="ui-pop__h-meta">this chat</span>
      </div>
      {/* Design `.pop__list` — the ONLY scroll region. The frame is
          `overflow: hidden`, so a long catalog scrolls inside the list at
          max-height 264px instead of growing the popover off-screen. */}
      <div className="ui-pop__list">
        {visible.length === 0 ? (
          <div className="atlas-model-pill__empty">No models available.</div>
        ) : (
          <>
            {cloud.length > 0 ? (
              <div className="atlas-model-pill__group">
                <div className="ui-pop__grp" aria-hidden="true">
                  Your keys
                </div>
                {cloud.map(renderRow)}
              </div>
            ) : null}
            {local.length > 0 ? (
              <div className="atlas-model-pill__group">
                <div className="ui-pop__grp" aria-hidden="true">
                  Local · on-device
                </div>
                {local.map(renderRow)}
              </div>
            ) : null}
          </>
        )}
      </div>
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
            className="atlas-model-pill__custom-label ui-section-label"
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
      {/* Design `.pop__f` — the pinned footer is the LAST element in the frame
          (the live-only custom-slug form sits above it, not below). */}
      {hasFooter ? (
        <div className="ui-pop__f atlas-model-pill__footer">
          {hasAddKey ? (
            <a
              className="ui-pop__f-link atlas-model-pill__footer-link"
              role="button"
              tabIndex={0}
              onClick={handleAddKeyClick}
            >
              Add a provider key →
            </a>
          ) : null}
          {hasAddKey && onGetLocalModels ? (
            <span className="ui-pop__f-sp atlas-model-pill__footer-sp" />
          ) : null}
          {onGetLocalModels ? (
            <a
              className="ui-pop__f-link atlas-model-pill__footer-link"
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
        data-open={open || undefined}
        onClick={() =>
          setOpen((current) => {
            if (current) setAddKeyOpen(false);
            return !current;
          })
        }
        data-tooltip="Choose model"
        data-tooltip-placement="bottom"
      >
        {/* Design: a LOCAL selection shows the chip glyph where a cloud
            selection shows its provider's brand dot. The dot's hue is data
            (`providerBrandColor`), never the app accent — one accent stays the
            accent, and every provider used to render the same blue dot. */}
        {selected !== null && isLocal(selected) ? (
          <Icon name="chip" size={11} />
        ) : (
          <span
            className="ui-cpill__dot atlas-model-pill__dot"
            aria-hidden="true"
            style={{
              background:
                selected === null
                  ? PROVIDER_DOT_UNSELECTED
                  : providerBrandColor(selected.provider),
            }}
          />
        )}
        <span className="ui-cpill__lb atlas-model-pill__name">
          {selected?.name ?? "Model"}
        </span>
        <Icon
          name="chevronDown"
          size={11}
          className="atlas-model-pill__caret"
        />
      </button>
      {/* `.ui-pop` is the design's `.pop` frame; `.atlas-model-pill__menu` only
          corrects what the `Menu` primitive still leaks (see composer.css). */}
      <Menu
        open={open}
        onClose={closeMenu}
        anchorRef={buttonRef}
        side="up"
        align="left"
        className="ui-pop atlas-model-pill__menu"
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

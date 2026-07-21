import { Menu } from "@0x-copilot/design-system";
import type { ModelCatalogModel } from "@0x-copilot/api-types";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
} from "react";

import { filterModels, groupModelsByProvider } from "../settings/data/models";

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
}

/**
 * Topbar model picker. A cmdk-style searchable, keyboard-navigable menu:
 * type to filter, ↑/↓ to move, Enter to pick, Esc to close. Rows are grouped
 * by provider and show name + reasoning + description. Only enabled models are
 * listed (the Settings → Models curation drives `enabled`); the current
 * selection always stays visible even if filtered/curated out. Rows flagged
 * `disabled` (no key configured) render but aren't selectable.
 */
export function ModelPill({
  models,
  value,
  onChange,
  disabled,
  onAddCustom,
}: ModelPillProps): ReactElement {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [customSlug, setCustomSlug] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const selected =
    models.find((model) => model.id === value) ?? models[0] ?? null;

  // Only enabled models are offered (undefined `enabled` = legacy/curated-in,
  // so it shows); the current selection is always kept visible.
  const visible = useMemo(
    () => models.filter((m) => m.enabled !== false || m.id === value),
    [models, value],
  );
  const filtered = useMemo(
    () => filterModels(visible, query),
    [visible, query],
  );
  const groups = useMemo(() => groupModelsByProvider(filtered), [filtered]);
  // Flat selectable order for keyboard nav (skips disabled rows).
  const selectable = useMemo(
    () => filtered.filter((m) => !m.disabled),
    [filtered],
  );

  // Reset highlight to the current selection (or top) whenever the list or
  // open-state changes, and focus the search box on open.
  useEffect(() => {
    if (!open) {
      setQuery("");
      return;
    }
    const idx = selectable.findIndex((m) => m.id === value);
    setActiveIndex(idx >= 0 ? idx : 0);
    const raf = requestAnimationFrame(() => searchRef.current?.focus());
    return () => cancelAnimationFrame(raf);
  }, [open, value, selectable]);

  const commit = (modelId: string): void => {
    onChange(modelId);
    setOpen(false);
  };

  const onSearchKeyDown = (
    event: ReactKeyboardEvent<HTMLInputElement>,
  ): void => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, selectable.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const pick = selectable[activeIndex];
      if (pick) commit(pick.id);
    } else if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
    }
  };

  const activeId = selectable[activeIndex]?.id;

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
        <div className="atlas-model-pill__search">
          <input
            ref={searchRef}
            type="text"
            role="searchbox"
            className="atlas-model-pill__search-input"
            placeholder="Search models…"
            aria-label="Search models"
            value={query}
            spellCheck={false}
            autoComplete="off"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onSearchKeyDown}
          />
        </div>
        {filtered.length === 0 ? (
          <div className="atlas-model-pill__empty">No models match.</div>
        ) : (
          groups.map((group) => (
            <div key={group.provider} className="atlas-model-pill__group">
              <div className="atlas-model-pill__group-head" aria-hidden="true">
                {group.label}
              </div>
              {group.models.map((model) => {
                const active = model.id === value;
                const highlighted = model.id === activeId;
                return (
                  <button
                    key={model.id}
                    type="button"
                    role="menuitemradio"
                    aria-checked={active}
                    disabled={model.disabled}
                    className="atlas-model-pill__item"
                    data-active={active || undefined}
                    data-highlighted={highlighted || undefined}
                    onMouseEnter={() => {
                      const idx = selectable.findIndex(
                        (m) => m.id === model.id,
                      );
                      if (idx >= 0) setActiveIndex(idx);
                    }}
                    onClick={() => {
                      if (model.disabled) return;
                      commit(model.id);
                    }}
                  >
                    <span className="atlas-model-pill__col">
                      <span className="atlas-model-pill__row">
                        <strong>{model.name}</strong>
                        {model.supports_reasoning ? (
                          <span
                            className="atlas-model-pill__badge"
                            data-kind="reasoning"
                          >
                            reasoning
                          </span>
                        ) : null}
                      </span>
                      {model.description ? (
                        <span className="atlas-model-pill__sub">
                          {model.description}
                        </span>
                      ) : null}
                    </span>
                    <span className="atlas-model-pill__provider">
                      {model.provider}
                    </span>
                  </button>
                );
              })}
            </div>
          ))
        )}
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

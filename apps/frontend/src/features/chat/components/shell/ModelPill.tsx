import { Menu } from "@enterprise-search/design-system";
import type { ModelCatalogModel } from "@enterprise-search/api-types";
import { useRef, useState, type ReactElement } from "react";

export interface ModelPillProps {
  models: Array<ModelCatalogModel & { disabled?: boolean }>;
  value: string;
  onChange: (modelId: string) => void;
  disabled?: boolean;
}

/**
 * Topbar model picker. Replaces the native `<select>` with an anchored
 * Menu so each row can show description + reasoning + provider — same
 * fields the catalog already exposes.
 */
export function ModelPill({
  models,
  value,
  onChange,
  disabled,
}: ModelPillProps): ReactElement {
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const selected =
    models.find((model) => model.id === value) ?? models[0] ?? null;
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
        <div className="atlas-model-pill__head">Model</div>
        {models.map((model) => {
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
              onClick={() => {
                if (model.disabled) return;
                onChange(model.id);
                setOpen(false);
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
      </Menu>
    </div>
  );
}

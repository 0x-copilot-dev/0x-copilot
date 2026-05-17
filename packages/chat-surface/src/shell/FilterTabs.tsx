// <FilterTabs value onChange options /> — generic tablist primitive.
//
// Source: cross-audit.md §1.6 + destinations-master-prd §4.1. Every
// destination's "All / Mentions / Approvals" style row goes through this
// component; Inbox's bespoke TabBar is the migration target.
//
// Accessibility: rendered as a `role="tablist"` with `aria-selected`,
// `aria-controls`, and stable `id`s on each tab so a screen-reader
// navigates predictably. The companion tabpanel id pattern is
// `${idPrefix}-panel-${slug}` for hosts to bind their content.

import type { CSSProperties, ReactElement, ReactNode } from "react";

export interface FilterTabOption<TSlug extends string> {
  readonly slug: TSlug;
  readonly label: string;
  /** Optional right-aligned count chip (e.g. unread count). */
  readonly count?: number;
  /** Optional pre-rendered icon to the left of the label. */
  readonly icon?: ReactNode;
}

export interface FilterTabsProps<TSlug extends string> {
  readonly value: TSlug;
  readonly onChange: (slug: TSlug) => void;
  readonly options: ReadonlyArray<FilterTabOption<TSlug>>;
  /**
   * Accessible label for the tablist. Required so multiple FilterTabs
   * instances on a page can be distinguished by AT users.
   */
  readonly ariaLabel: string;
  /**
   * ID prefix used for the tab and matching tabpanel ids. The host
   * renders the panel as `id={`${idPrefix}-panel-${value}`}` and binds
   * `aria-labelledby={`${idPrefix}-tab-${value}`}`.
   */
  readonly idPrefix: string;
  readonly className?: string;
}

const wrapperStyle: CSSProperties = {
  display: "flex",
  gap: 4,
  borderBottom: "1px solid var(--color-border, #232325)",
};

function tabStyle(active: boolean): CSSProperties {
  return {
    height: 36,
    padding: "0 14px",
    borderRadius: 0,
    border: "none",
    background: "transparent",
    color: active
      ? "var(--color-text, #ededee)"
      : "var(--color-text-muted, #b4b4b8)",
    fontSize: "var(--font-size-sm, 13px)",
    fontWeight: active ? 600 : 500,
    cursor: "pointer",
    borderBottom: active
      ? "2px solid var(--color-accent, #d97757)"
      : "2px solid transparent",
    marginBottom: -1,
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
  };
}

function countStyle(active: boolean): CSSProperties {
  return {
    fontSize: "var(--font-size-2xs, 11px)",
    fontWeight: 600,
    color: active
      ? "var(--color-accent, #d97757)"
      : "var(--color-text-subtle, #7e7e84)",
    backgroundColor: "var(--color-surface-muted, #222224)",
    borderRadius: "var(--radius-full, 999px)",
    padding: "1px 8px",
    minWidth: 18,
    textAlign: "center",
  };
}

export function FilterTabs<TSlug extends string>({
  value,
  onChange,
  options,
  ariaLabel,
  idPrefix,
  className,
}: FilterTabsProps<TSlug>): ReactElement {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      style={wrapperStyle}
      className={className}
      data-testid="filter-tabs"
    >
      {options.map((option) => {
        const active = option.slug === value;
        return (
          <button
            key={option.slug}
            type="button"
            role="tab"
            aria-selected={active}
            aria-controls={`${idPrefix}-panel-${option.slug}`}
            id={`${idPrefix}-tab-${option.slug}`}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(option.slug)}
            style={tabStyle(active)}
            data-testid={`filter-tab-${option.slug}`}
            data-active={active}
          >
            {option.icon}
            {option.label}
            {option.count !== undefined ? (
              <span
                style={countStyle(active)}
                data-testid={`filter-tab-count-${option.slug}`}
              >
                {option.count}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

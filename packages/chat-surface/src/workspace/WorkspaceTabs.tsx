// PR 3.2 — minimal WAI-ARIA tablist for the workspace pane.
// PR-1.7 — hoisted into @0x-copilot/chat-surface with the pane it serves.
//
// Implements the [Tabs pattern](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/):
// roving tabindex, ArrowLeft/Right cycle, Home/End jump, Enter/Space
// (no-op since selecting via focus is handled by the click handler).
// Co-located with WorkspacePane because nothing else in the app uses
// tabs today; per packages/design-system/CLAUDE.md we promote when a
// second consumer needs it. Until then this stays small and local.

import { classNames } from "@0x-copilot/design-system";
import {
  useCallback,
  useId,
  useRef,
  type KeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";

export interface WorkspaceTabsItem<TabId extends string> {
  id: TabId;
  label: string;
  /** Tiny badge to the right of the label (e.g. "6", "2 live"). */
  badge?: ReactNode;
  /** Disabled tabs aren't focusable; arrow keys skip them. */
  disabled?: boolean;
}

export interface WorkspaceTabsProps<TabId extends string> {
  items: readonly WorkspaceTabsItem<TabId>[];
  active: TabId;
  onSelect: (id: TabId) => void;
  /** aria-label for the tablist (required for accessibility). */
  ariaLabel: string;
}

export function WorkspaceTabs<TabId extends string>({
  items,
  active,
  onSelect,
  ariaLabel,
}: WorkspaceTabsProps<TabId>): ReactElement {
  const reactId = useId();
  const tabRefs = useRef<Map<TabId, HTMLButtonElement | null>>(new Map());

  const focusableIndex = useCallback(
    (start: number, direction: 1 | -1): number => {
      const length = items.length;
      if (length === 0) {
        return -1;
      }
      let idx = start;
      for (let step = 0; step < length; step += 1) {
        idx = (idx + direction + length) % length;
        const candidate = items[idx];
        if (!candidate.disabled) {
          return idx;
        }
      }
      return -1;
    },
    [items],
  );

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLButtonElement>, currentIndex: number): void => {
      switch (event.key) {
        case "ArrowRight":
        case "ArrowDown": {
          event.preventDefault();
          const next = focusableIndex(currentIndex, 1);
          if (next >= 0) {
            const nextItem = items[next];
            tabRefs.current.get(nextItem.id)?.focus();
            onSelect(nextItem.id);
          }
          break;
        }
        case "ArrowLeft":
        case "ArrowUp": {
          event.preventDefault();
          const prev = focusableIndex(currentIndex, -1);
          if (prev >= 0) {
            const prevItem = items[prev];
            tabRefs.current.get(prevItem.id)?.focus();
            onSelect(prevItem.id);
          }
          break;
        }
        case "Home": {
          event.preventDefault();
          const first = items.findIndex((item) => !item.disabled);
          if (first >= 0) {
            const firstItem = items[first];
            tabRefs.current.get(firstItem.id)?.focus();
            onSelect(firstItem.id);
          }
          break;
        }
        case "End": {
          event.preventDefault();
          for (let idx = items.length - 1; idx >= 0; idx -= 1) {
            if (!items[idx].disabled) {
              const lastItem = items[idx];
              tabRefs.current.get(lastItem.id)?.focus();
              onSelect(lastItem.id);
              break;
            }
          }
          break;
        }
        default:
          break;
      }
    },
    [focusableIndex, items, onSelect],
  );

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      aria-orientation="horizontal"
      className="atlas-workspace-tabs"
      data-testid="workspace-tabs"
    >
      {items.map((item, index) => {
        const tabId = `${reactId}-${item.id}-tab`;
        const panelId = `${reactId}-${item.id}-panel`;
        const isActive = item.id === active;
        return (
          <button
            key={item.id}
            ref={(el) => {
              tabRefs.current.set(item.id, el);
            }}
            type="button"
            role="tab"
            id={tabId}
            aria-selected={isActive}
            aria-controls={panelId}
            tabIndex={isActive ? 0 : -1}
            disabled={item.disabled}
            onClick={() => onSelect(item.id)}
            onKeyDown={(event) => handleKeyDown(event, index)}
            className={classNames(
              "atlas-workspace-tabs__tab",
              isActive && "atlas-workspace-tabs__tab--active",
            )}
          >
            <span className="atlas-workspace-tabs__label">{item.label}</span>
            {item.badge !== undefined && item.badge !== null ? (
              <span className="atlas-workspace-tabs__badge">{item.badge}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

/**
 * Helper to compose the `id` attribute the active panel must carry so
 * `aria-controls` resolves correctly. Workspace pane uses this directly.
 */
export function workspaceTabPanelId<TabId extends string>(
  tablistId: string,
  tabId: TabId,
): string {
  return `${tablistId}-${tabId}-panel`;
}

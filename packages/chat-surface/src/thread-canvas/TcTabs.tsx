import type { KeyboardEvent, MouseEvent, ReactElement } from "react";

import { resolveSurfaceHue } from "../surfaces/surfaceHue";

export interface TcTab {
  readonly uri: string;
  readonly title: string;
  readonly pinned?: boolean;
  /**
   * An explicit source hue, when one was chosen rather than implied. This is
   * where a `publish_artifact` accent lands. Left unset, the hue is derived
   * from the URI's scheme, so every existing tab gains an identity colour with
   * no caller change — the choice is an override, never a requirement.
   */
  readonly hue?: string;
}

export interface TcTabsProps {
  readonly tabs: readonly TcTab[];
  readonly activeUri: string;
  readonly onActivate: (uri: string) => void;
  readonly onClose: (uri: string) => void;
}

/**
 * The canvas tab strip.
 *
 * Presentation lives in `surface-language.css`, not here. It used to be inline
 * styles over a private hardcoded palette (`#181a1c` / `#2a2d31`), which is why
 * the strip could not theme and every tab looked identical regardless of what
 * it opened. Each tab now carries `data-surface-hue`, and the stylesheet reads
 * `--surface-src` from it — the component resolves a NAME and paints nothing.
 */
export function TcTabs(props: TcTabsProps): ReactElement {
  const { tabs, activeUri, onActivate, onClose } = props;

  return (
    <div role="tablist" data-testid="tc-tabs" className="tc-tabs">
      {tabs.map((tab) => {
        const isActive = tab.uri === activeUri;
        const hue = resolveSurfaceHue({ uri: tab.uri, choice: tab.hue });
        const handleActivate = (): void => onActivate(tab.uri);
        const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onActivate(tab.uri);
          }
        };
        const handleClose = (event: MouseEvent<HTMLButtonElement>): void => {
          event.stopPropagation();
          onClose(tab.uri);
        };
        return (
          <div
            key={tab.uri}
            role="tab"
            tabIndex={0}
            aria-selected={isActive}
            aria-current={isActive ? "page" : undefined}
            data-uri={tab.uri}
            data-active={isActive ? "true" : "false"}
            data-pinned={tab.pinned ? "true" : "false"}
            data-surface-hue={hue}
            className="tc-tab"
            onClick={handleActivate}
            onKeyDown={handleKeyDown}
          >
            <span aria-hidden="true" className="tc-tab__dot" />
            <span className="tc-tab__title">{tab.title}</span>
            {tab.pinned ? null : (
              <button
                type="button"
                aria-label={`Close ${tab.title}`}
                data-testid={`tc-tabs-close-${tab.uri}`}
                onClick={handleClose}
                className="tc-tab__close"
              >
                ×
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

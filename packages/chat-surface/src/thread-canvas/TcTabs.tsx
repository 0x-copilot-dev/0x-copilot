import type { KeyboardEvent, MouseEvent, ReactElement } from "react";

import { resolveSurfaceHue } from "../surfaces/surfaceHue";

export interface TcTab {
  readonly uri: string;
  readonly title: string;
  /**
   * Auto-follow is paused ON this tab — the user opened it while the run kept
   * writing elsewhere. Renders the pin glyph in place of the close button
   * (clicking the glyph releases the pin), so the paused state is continuously
   * legible instead of being announced by a banner that a re-sort can silently
   * revoke.
   *
   * The host must NOT set this for the newest tab: pinning what you are already
   * following pauses nothing, and it used to swallow that tab's close button
   * with no visible cause.
   */
  readonly pinned?: boolean;
  /** The tab new work is landing in, while the run is still producing. */
  readonly live?: boolean;
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
  /**
   * Resume auto-follow. Supplied only while a pin is actually being held over a
   * live run; the "Follow live" chip renders at the end of the strip when it is
   * present, and not at all when it is not.
   *
   * This is deliberately IN the strip. It used to be a full-bleed accent banner
   * above the canvas (`RunFollowLiveBanner`), which restated the pinned tab's
   * title in caps a few pixels above the tab already showing it, and pushed the
   * whole canvas down ~33px on a plain tab click.
   */
  readonly onFollowLive?: () => void;
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
  const { tabs, activeUri, onActivate, onClose, onFollowLive } = props;

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
        const handleUnpin = (event: MouseEvent<HTMLButtonElement>): void => {
          event.stopPropagation();
          onFollowLive?.();
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
            data-live={tab.live ? "true" : "false"}
            data-surface-hue={hue}
            className="tc-tab"
            onClick={handleActivate}
            onKeyDown={handleKeyDown}
          >
            <span aria-hidden="true" className="tc-tab__dot" />
            <span className="tc-tab__title">{tab.title}</span>
            {tab.live ? (
              <span aria-hidden="true" className="tc-tab__live" />
            ) : null}
            {tab.pinned ? (
              // The pin glyph IS the release control. A non-interactive marker
              // would leave a pinned tab with neither a close button nor a way
              // out once the run goes terminal and the chip stops rendering.
              <button
                type="button"
                aria-label={`Unpin ${tab.title} and follow live`}
                data-testid={`tc-tabs-unpin-${tab.uri}`}
                onClick={handleUnpin}
                className="tc-tab__pin"
              >
                <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                  <path d="M9.8 1.2a1 1 0 0 0-1.5 1.3l.2.2-3 2.2-2.1-.3a1 1 0 0 0-.8 1.7l3 3-3.4 4.3a.5.5 0 0 0 .7.7l4.3-3.4 3 3a1 1 0 0 0 1.7-.8l-.3-2.1 2.2-3 .2.2a1 1 0 0 0 1.3-1.5l-5.5-5.5Z" />
                </svg>
              </button>
            ) : (
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
      {onFollowLive === undefined ? null : (
        <>
          <span aria-hidden="true" className="tc-tabs__spacer" />
          <button
            type="button"
            data-testid="tc-tabs-follow-live"
            onClick={onFollowLive}
            className="tc-follow-chip"
          >
            <span aria-hidden="true" className="tc-follow-chip__dot" />
            Follow live
          </button>
        </>
      )}
    </div>
  );
}

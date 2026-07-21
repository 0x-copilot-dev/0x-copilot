import { type CSSProperties, type ReactElement } from "react";

import { Icon } from "../icons/Icon";
import type { IconName } from "../icons/paths";
import { BrandMark } from "./BrandMark";
import {
  SHELL_DESTINATIONS,
  type ShellDestination,
  type ShellDestinationSlug,
} from "./destinations";

// Geometry constants — kept here, not stretched into a token, because they
// describe THIS component's box (not a colour or a font). Values track the v3
// "quiet" shell spec. One source of truth: `RAIL_WIDTH` feeds the rail's own
// style and is exported so ChatShell's grid template can mirror it.
const RAIL_WIDTH = 48; // Rail 48px
const BUTTON_SIZE = 34; // destination buttons 34×34
const BRAND_SIZE = 32; // brand mark 32×32
const AVATAR_SIZE = 26; // avatar (.rail-me) 26px circle
const ICON_SIZE = 17; // design .rail-item svg is 17×17

// The brand mark navigates here, and the shell's canonical front door.
const BRAND_DESTINATION: ShellDestinationSlug = "run";

// Every rail slug → its canonical glyph (PRD-C). Solo destinations use the v3
// design's icons: projects=folder, connectors("Tools")=plug, tools("Skills")=
// skill/sparkle. Icons live in the shared set (PRD-A) so there is one source.
const SLUG_ICON: Readonly<Record<ShellDestinationSlug, IconName>> = {
  run: "run",
  chats: "chats",
  projects: "folder",
  activity: "activity",
  connectors: "plug",
  tools: "skill",
  home: "home",
  agents: "agents",
  library: "library",
  inbox: "inbox",
  todos: "todos",
  team: "team",
  memory: "memory",
  routines: "routines",
  members: "members",
  billing: "billing",
};

// Rail-scoped CSS for interaction states inline styles cannot express: hover
// tint, the focus-visible ring, and a subtle colour brighten. Reduced-motion is
// inherited from the design-system global rule. Hover background is the design's
// `--panel2` (= --color-surface-muted), not the too-dark elevated step (PRD-C).
const RAIL_STYLE_RULES = `
[data-component="app-rail"] .rail-btn {
  transition: background-color 120ms ease, color 120ms ease;
}
[data-component="app-rail"] .rail-btn:hover {
  background: var(--color-surface-muted);
}
[data-component="app-rail"] .rail-btn:not(.rail-brand):hover {
  color: var(--color-text);
}
[data-component="app-rail"] .rail-btn:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
}
`;

export interface AppRailProps {
  /**
   * The destination the host considers active. The rail is controlled —
   * it never reads from a router itself. The host owns route↔destination
   * mapping (see `apps/frontend/src/app/App.tsx`); the rail just renders
   * a button per destination and reports clicks back.
   */
  readonly activeDestination: ShellDestinationSlug;
  /**
   * Click handler — the host translates the slug into whatever route
   * shape it owns and decides what to do with the navigation. Click on the
   * already-active destination is delivered too; the host can ignore or treat
   * as a deep-link reset.
   */
  readonly onNavigate: (slug: ShellDestinationSlug) => void;
  /**
   * Optional Settings click handler. When supplied, the rail's foot renders
   * a Settings gear + an account avatar. When absent (e.g. a bare test
   * harness), the whole foot is omitted.
   */
  readonly onOpenSettings?: () => void;
  /**
   * The destinations to render, in display order. Defaults to the legacy
   * 12-item `SHELL_DESTINATIONS` — the web host's frozen rail. The desktop host
   * passes the profile-derived list. The rail stays a pure controlled view.
   */
  readonly destinations?: readonly ShellDestination[];
  /**
   * Optional account identity. When present, the foot avatar renders the
   * user's initial (design `.rail-me`); when absent, a neutral user glyph.
   * The rail never fetches — the host supplies this (PRD-C / PRD-H).
   */
  readonly identity?: { readonly initial: string };
  /**
   * Optional per-destination badge counts (e.g. active runs on `run`). A badge
   * renders when the count is > 0 AND that destination is not the active one —
   * matching the design's Run badge shown only when off-workspace. Data is
   * host-supplied (PRD-H); absent = no badges.
   */
  readonly badges?: Partial<Record<ShellDestinationSlug, number>>;
}

function railButtonStyle(size: number, isActive: boolean): CSSProperties {
  return {
    position: "relative",
    width: size,
    height: size,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    // Active = the design's elevated panel (--panel2 = --color-surface-muted);
    // the sole decorative accent is the left-edge bar below, not a fill (PRD-C).
    background: isActive ? "var(--color-surface-muted)" : "transparent",
    border: "none",
    borderRadius: "var(--radius-md)",
    color: isActive ? "var(--color-text)" : "var(--color-text-subtle)",
    cursor: "pointer",
    padding: 0,
  };
}

// 2px accent bar in the rail gutter at the button's left edge — the active
// affordance (design `.rail-item[data-active]::before`: left:-8px, height 16px,
// radius 0 2 2 0). Rendered as an inset child so it stays a crisp vertical bar.
const activeBarStyle: CSSProperties = {
  position: "absolute",
  left: -8,
  top: "50%",
  transform: "translateY(-50%)",
  width: 2,
  height: 16,
  borderRadius: "0 2px 2px 0",
  background: "var(--color-accent)",
};

// Count badge (design `.rail-item .rbadge`): 13px accent pill, accent-ink text,
// mono, top-right of the button.
const badgeStyle: CSSProperties = {
  position: "absolute",
  top: 3,
  right: 3,
  minWidth: 13,
  height: 13,
  padding: "0 3px",
  borderRadius: 7,
  background: "var(--color-accent)",
  color: "var(--color-accent-contrast)",
  fontFamily: "var(--font-mono)",
  fontSize: 8.5,
  fontWeight: 700,
  lineHeight: "13px",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
};

export function AppRail({
  activeDestination,
  onNavigate,
  onOpenSettings,
  destinations = SHELL_DESTINATIONS,
  identity,
  badges,
}: AppRailProps): ReactElement {
  const railStyle: CSSProperties = {
    width: RAIL_WIDTH,
    minWidth: RAIL_WIDTH,
    height: "100%",
    // Design rail sits on --ink2 (= --color-bg-elevated), one step up from the
    // window bg — the previous --color-bg read a shade too dark (PRD-C).
    backgroundColor: "var(--color-bg-elevated)",
    borderRight: "1px solid var(--color-border)",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    paddingTop: 10,
    paddingBottom: 10,
    boxSizing: "border-box",
  };
  const itemsStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 2,
    flex: 1,
    marginTop: 10,
  };
  const footStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 6,
    paddingTop: 8,
    borderTop: "1px solid var(--color-border)",
    width: BUTTON_SIZE,
  };
  const avatarStyle: CSSProperties = {
    position: "relative",
    width: AVATAR_SIZE,
    height: AVATAR_SIZE,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    // Design .rail-me sits on --panel3 (= --color-surface-elevated), no border
    // (PRD-C — previously the too-dark elevated bg + a stray hairline).
    background: "var(--color-surface-elevated)",
    borderRadius: "var(--radius-full)",
    color: "var(--color-text-strong)",
    cursor: "pointer",
    padding: 0,
    border: "none",
    fontSize: 11,
    fontWeight: 600,
  };

  return (
    <nav
      aria-label="Copilot destinations"
      style={railStyle}
      data-component="app-rail"
    >
      <style>{RAIL_STYLE_RULES}</style>
      <button
        type="button"
        className="rail-btn rail-brand"
        aria-label="0xCopilot"
        title="0xCopilot — Run"
        data-rail-brand=""
        onClick={() => onNavigate(BRAND_DESTINATION)}
        style={{
          ...railButtonStyle(BRAND_SIZE, false),
          color: "var(--color-accent)",
        }}
      >
        <BrandMark size={22} />
      </button>
      <div style={itemsStyle}>
        {destinations.map((d) => {
          const isActive = d.slug === activeDestination;
          const count = badges?.[d.slug] ?? 0;
          const showBadge = count > 0 && !isActive;
          return (
            <button
              key={d.slug}
              type="button"
              className="rail-btn"
              aria-label={showBadge ? `${d.label} (${count})` : d.label}
              aria-current={isActive ? "page" : undefined}
              data-destination={d.slug}
              data-state={isActive ? "active" : "inactive"}
              onClick={() => onNavigate(d.slug)}
              style={railButtonStyle(BUTTON_SIZE, isActive)}
              title={d.label}
            >
              {isActive ? (
                <span
                  aria-hidden
                  data-rail-active-bar=""
                  style={activeBarStyle}
                />
              ) : null}
              <Icon name={SLUG_ICON[d.slug]} size={ICON_SIZE} />
              {showBadge ? (
                <span aria-hidden data-rail-badge="" style={badgeStyle}>
                  {count > 99 ? "99+" : count}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
      {onOpenSettings ? (
        <div style={footStyle}>
          <button
            type="button"
            className="rail-btn"
            aria-label="Settings"
            data-rail-action="settings"
            onClick={onOpenSettings}
            style={railButtonStyle(BUTTON_SIZE, false)}
            title="Settings"
          >
            <Icon name="gear" size={ICON_SIZE} />
          </button>
          <button
            type="button"
            className="rail-btn"
            aria-label="Account"
            data-rail-me=""
            onClick={onOpenSettings}
            style={avatarStyle}
            title="Account"
          >
            {identity && identity.initial ? (
              <span aria-hidden data-rail-initial="">
                {identity.initial.slice(0, 1).toUpperCase()}
              </span>
            ) : (
              <Icon name="user" size={14} />
            )}
          </button>
        </div>
      ) : null}
    </nav>
  );
}

export { RAIL_WIDTH as APP_RAIL_WIDTH };

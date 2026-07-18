import { type CSSProperties, type ReactElement } from "react";

import { BrandMark } from "./BrandMark";
import {
  SHELL_DESTINATIONS,
  type ShellDestination,
  type ShellDestinationSlug,
} from "./destinations";

// Geometry constants — kept here, not stretched into a token, because they
// describe THIS component's box (not a colour or a font). Values track the v2
// "quiet" shell spec (DESIGN-SPEC §0/§1). One source of truth: `RAIL_WIDTH`
// feeds the rail's own style and is exported so ChatShell's grid template can
// mirror it without redefining the literal.
const RAIL_WIDTH = 48; // §0: Rail 48px
const BUTTON_SIZE = 34; // §1: destination buttons 34×34
const BRAND_SIZE = 32; // §1: brand mark 32×32
const AVATAR_SIZE = 26; // §1: avatar (.rail-me) 26px circle

// The brand mark navigates here, and the shell's canonical front door.
const BRAND_DESTINATION: ShellDestinationSlug = "run";

// Rail-scoped CSS for interaction states that inline styles cannot express:
// hover lift, the focus-visible ring (FR-2.26), and a subtle colour brighten.
// Reduced-motion is inherited from the design-system global rule
// (`:root[data-reduce-motion] *` / `prefers-reduced-motion`), which zeroes
// these transitions — no local media query needed. Selectors are scoped to
// `[data-component="app-rail"]` so multiple rail instances share one rule set.
const RAIL_STYLE_RULES = `
[data-component="app-rail"] .rail-btn {
  transition: background-color 120ms ease, color 120ms ease;
}
[data-component="app-rail"] .rail-btn:hover {
  background: var(--color-bg-elevated);
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
   * shape it owns (chat / settings / share / admin-…) and decides what
   * to do with the navigation. Click on the already-active destination
   * is delivered too; the host can ignore or treat as a deep-link reset.
   */
  readonly onNavigate: (slug: ShellDestinationSlug) => void;
  /**
   * Optional Settings click handler. When supplied, the rail's foot renders
   * a Settings gear + an account avatar (mirrors os-shell.jsx `rail__foot`).
   * Settings isn't a destination — it lives outside the shell destination
   * union — so it gets its own foot slot rather than sitting in the list.
   * When absent (e.g. a bare test harness), the whole foot is omitted.
   */
  readonly onOpenSettings?: () => void;
  /**
   * The destinations to render, in display order. Defaults to the legacy
   * 12-item `SHELL_DESTINATIONS` — the web host's frozen rail, so omitting
   * this prop preserves that surface byte-for-byte. The desktop host passes
   * the profile-derived list from `destinationsForProfile(profile)`. The rail
   * stays a pure controlled view: it renders whatever list it is given and
   * never resolves the deployment profile itself (that single responsibility
   * lives in ChatShell / the host).
   */
  readonly destinations?: readonly ShellDestination[];
}

function Glyph({ slug }: { slug: ShellDestinationSlug }): ReactElement {
  const common = {
    "aria-hidden": true,
    focusable: false,
    fill: "none" as const,
    stroke: "currentColor",
    strokeWidth: 1.5,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    viewBox: "0 0 24 24",
    width: 18,
    height: 18,
  };
  switch (slug) {
    case "home":
      return (
        <svg {...common}>
          <path d="M3 11l9-8 9 8" />
          <path d="M5 10v10h14V10" />
        </svg>
      );
    case "chats":
      return (
        <svg {...common}>
          <path d="M4 5h16v11H8l-4 4z" />
        </svg>
      );
    case "agents":
      return (
        <svg {...common}>
          <circle cx="12" cy="8" r="4" />
          <path d="M4 21c0-4 4-7 8-7s8 3 8 7" />
        </svg>
      );
    case "library":
      return (
        <svg {...common}>
          <path d="M4 5h6v14H4z" />
          <path d="M14 5h6v14h-6z" />
          <path d="M7 8h0M7 11h0" />
        </svg>
      );
    case "inbox":
      return (
        <svg {...common}>
          <path d="M3 13h5l2 3h4l2-3h5" />
          <path d="M3 13l3-8h12l3 8v6H3z" />
        </svg>
      );
    case "tools":
      return (
        <svg {...common}>
          <path d="M14 6l4 4-10 10-4-4z" />
          <path d="M14 6l3-3 4 4-3 3" />
        </svg>
      );
    case "projects":
      return (
        <svg {...common}>
          <path d="M3 6h7l2 2h9v11H3z" />
        </svg>
      );
    case "todos":
      return (
        <svg {...common}>
          <path d="M4 6h16M4 12h16M4 18h10" />
          <path d="M19 17l2 2 3-3" transform="translate(-4 0)" />
        </svg>
      );
    case "connectors":
      return (
        <svg {...common}>
          <circle cx="6" cy="12" r="2.5" />
          <circle cx="18" cy="6" r="2.5" />
          <circle cx="18" cy="18" r="2.5" />
          <path d="M8.5 12L16 7M8.5 12L16 17" />
        </svg>
      );
    case "team":
      return (
        <svg {...common}>
          <circle cx="9" cy="9" r="3" />
          <circle cx="17" cy="10" r="2.5" />
          <path d="M3 20c0-3 3-5 6-5s6 2 6 5" />
          <path d="M14 20c0-2.2 1.5-4 3-4s3 1.8 3 4" />
        </svg>
      );
    case "memory":
      return (
        <svg {...common}>
          <path d="M9 5a4 4 0 0 0-4 4v6a4 4 0 0 0 4 4h6a4 4 0 0 0 4-4V9a4 4 0 0 0-4-4z" />
          <path d="M9 9h6M9 12h6M9 15h4" />
        </svg>
      );
    case "routines":
      // Clock face + recurrence sweep: Routines = scheduled / triggered runs.
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="8" />
          <path d="M12 8v4l3 2" />
        </svg>
      );
    case "run":
      // Rounded viewport + play head: the live agent run (DESIGN-SPEC §7 `run`).
      return (
        <svg {...common}>
          <rect x="3" y="3" width="18" height="18" rx="4" />
          <path d="M10 9l5 3-5 3z" />
        </svg>
      );
    case "activity":
      // Pulse line: the run-history feed (DESIGN-SPEC §7 `activity`).
      return (
        <svg {...common}>
          <path d="M3 12h4l2.5 7 5-14L17 12h4" />
        </svg>
      );
    case "members":
      // Two people: team member directory (team profile only).
      return (
        <svg {...common}>
          <circle cx="9" cy="8" r="3" />
          <circle cx="17" cy="9" r="2.5" />
          <path d="M3 20c0-3 2.7-5 6-5s6 2 6 5" />
          <path d="M15 20c0-2 1.3-3.5 3-3.5s3 1.5 3 3.5" />
        </svg>
      );
    case "billing":
      // Card: billing (team profile only).
      return (
        <svg {...common}>
          <rect x="3" y="6" width="18" height="12" rx="2" />
          <path d="M3 10h18" />
        </svg>
      );
  }
}

function SettingsGlyph(): ReactElement {
  return (
    <svg
      aria-hidden
      focusable={false}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      viewBox="0 0 24 24"
      width={18}
      height={18}
    >
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h.1a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v.1a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
    </svg>
  );
}

// Neutral account glyph inside the foot avatar (no identity data flows into
// this framework-agnostic view yet — the host supplies real avatar art later).
function AvatarGlyph(): ReactElement {
  return (
    <svg
      aria-hidden
      focusable={false}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      viewBox="0 0 24 24"
      width={14}
      height={14}
    >
      <circle cx="12" cy="9" r="3.2" />
      <path d="M5.5 19c.6-3.2 3.3-5 6.5-5s5.9 1.8 6.5 5" />
    </svg>
  );
}

function railButtonStyle(size: number, isActive: boolean): CSSProperties {
  return {
    position: "relative",
    width: size,
    height: size,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    // Active = elevated panel background (DESIGN-SPEC §1 panel2); the sole
    // decorative accent is the left-edge bar below, not a fill.
    background: isActive ? "var(--color-bg-elevated)" : "transparent",
    border: "none",
    borderRadius: "var(--radius-md)",
    color: isActive ? "var(--color-text)" : "var(--color-text-subtle)",
    cursor: "pointer",
    padding: 0,
  };
}

// 2px accent bar on the button's left edge — the active-state affordance
// (DESIGN-SPEC §1). Rendered as an inset child rather than a box-shadow so it
// stays a crisp vertical bar despite the button's rounded corners.
const activeBarStyle: CSSProperties = {
  position: "absolute",
  left: 0,
  top: 6,
  bottom: 6,
  width: 2,
  borderRadius: 1,
  background: "var(--color-accent)",
};

export function AppRail({
  activeDestination,
  onNavigate,
  onOpenSettings,
  destinations = SHELL_DESTINATIONS,
}: AppRailProps): ReactElement {
  const railStyle: CSSProperties = {
    width: RAIL_WIDTH,
    minWidth: RAIL_WIDTH,
    height: "100%",
    backgroundColor: "var(--color-bg)",
    borderRight: "1px solid var(--color-border)",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    paddingTop: 10,
    paddingBottom: 10,
    boxSizing: "border-box",
  };
  // The destinations list takes the available vertical space; the foot group
  // (Settings + avatar) hugs the bottom — mirrors os.css's
  // `.rail__items { flex: 1 }` + `.rail__foot { border-top }` pattern.
  const itemsStyle: CSSProperties = {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 4,
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
    background: "var(--color-bg-elevated)",
    border: "1px solid var(--color-border)",
    borderRadius: "var(--radius-full)",
    color: "var(--color-text-subtle)",
    cursor: "pointer",
    padding: 0,
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
        <BrandMark size={20} />
      </button>
      <div style={itemsStyle}>
        {destinations.map((d) => {
          const isActive = d.slug === activeDestination;
          return (
            <button
              key={d.slug}
              type="button"
              className="rail-btn"
              aria-label={d.label}
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
              <Glyph slug={d.slug} />
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
            <SettingsGlyph />
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
            <AvatarGlyph />
          </button>
        </div>
      ) : null}
    </nav>
  );
}

export { RAIL_WIDTH as APP_RAIL_WIDTH };

// Legacy Settings nav chrome — the top bar (back + crumb + user) and the
// left rail rows/glyphs. Extracted from `SettingsScreen.tsx` so the shell owns
// only state + section dispatch. Presentational: every value arrives as a prop.

import type { ReactElement } from "react";

import type { UserProfileState } from "../me/useUserProfile";
import type { RailEntry, RailIcon, SettingsSection } from "./settingsSections";

// Honest header identity (Issues 3 + 4): a wallet (SIWE) account has no real
// email, so label it by its truncated address instead of the undeliverable
// `@wallet.invalid` placeholder.
export function headerIdentityLabel(
  data: UserProfileState["data"],
): string | null {
  if (!data) return null;
  const isWallet =
    data.email_is_placeholder === true ||
    (data.wallet_address != null && data.wallet_address !== "");
  if (!isWallet) return data.email;
  const address = data.wallet_address ?? data.email.split("@")[0];
  return address.length > 12
    ? `${address.slice(0, 6)}…${address.slice(-4)}`
    : address;
}

export function SettingsTopChrome({
  workspaceName,
  userEmail,
  onBack,
  onJumpConnectors,
}: {
  workspaceName: string | null;
  userEmail: string | null;
  onBack: () => void;
  onJumpConnectors: () => void;
}): ReactElement {
  const initial = userEmail ? userEmail.charAt(0).toUpperCase() : "·";
  return (
    <header className="settings-chrome" role="banner">
      <button
        type="button"
        className="settings-chrome__back"
        onClick={onBack}
        title="Back to chat"
      >
        <RailGlyph name="back" />
        <span>Back to Copilot</span>
      </button>
      <div className="settings-chrome__crumb" aria-live="polite">
        Settings
        {workspaceName ? (
          <>
            <span className="settings-chrome__crumb-sep" aria-hidden="true">
              ·
            </span>
            <strong>{workspaceName}</strong>
          </>
        ) : null}
      </div>
      <div className="settings-chrome__right">
        <button
          type="button"
          className="settings-chrome__shortcut"
          onClick={onJumpConnectors}
          title="Manage MCP servers"
        >
          <RailGlyph name="link" />
          <span>Manage MCP servers</span>
        </button>
        <div className="settings-chrome__user" aria-label="Signed-in user">
          <span className="settings-chrome__avatar" aria-hidden="true">
            {initial}
          </span>
          <span className="settings-chrome__email">
            {userEmail ?? "Signed in"}
          </span>
        </div>
      </div>
    </header>
  );
}

export function RailRow({
  entry,
  active,
  count,
  onPick,
}: {
  entry: Extract<RailEntry, { kind: "section" }>;
  active: boolean;
  count: number | null;
  onPick: (id: SettingsSection) => void;
}): ReactElement {
  // Don't render zero-count badges for hooks still loading or empty
  // collections — only show the count chip when there's something to
  // count. Avoids "Connectors 0" before connectors hydrate.
  const badge =
    entry.badge ?? (count !== null && count > 0 ? String(count) : null);
  return (
    <button
      className={active ? "settings-nav__row is-active" : "settings-nav__row"}
      type="button"
      title={`Open ${entry.label} settings`}
      onClick={() => onPick(entry.id)}
    >
      <span className="settings-nav__icon" aria-hidden="true">
        <RailGlyph name={entry.icon} />
      </span>
      <span className="settings-nav__label">{entry.label}</span>
      {entry.adminPill ? (
        <span className="settings-nav__badge settings-nav__badge--admin">
          Admin
        </span>
      ) : badge ? (
        <span className="settings-nav__badge">{badge}</span>
      ) : null}
    </button>
  );
}

/**
 * Inline glyphs for the rail + chrome. The design system doesn't ship
 * an icon set today; rather than introduce one for a single surface we
 * inline the strokes here in the same style the design bundle used.
 * Stroke `currentColor` so the active-row colour change picks up
 * automatically.
 */
export function RailGlyph({
  name,
}: {
  name: RailIcon | "back";
}): ReactElement | null {
  const common = {
    width: 16,
    height: 16,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.6,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  switch (name) {
    case "back":
      return (
        <svg {...common}>
          <path d="M15 6l-6 6 6 6" />
        </svg>
      );
    case "user":
      return (
        <svg {...common}>
          <circle cx="12" cy="8" r="4" />
          <path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" />
        </svg>
      );
    case "sun":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5" />
        </svg>
      );
    case "command":
      return (
        <svg {...common}>
          <path d="M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z" />
        </svg>
      );
    case "key":
      return (
        <svg {...common}>
          <circle cx="8" cy="14" r="4" />
          <path d="M11 12l9-9 2 2-2 2 2 2-2 2-2-2-3 3" />
        </svg>
      );
    case "building":
      return (
        <svg {...common}>
          <path d="M4 21V5a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v16" />
          <path d="M15 9h3a2 2 0 0 1 2 2v10" />
          <path d="M8 7h2M8 11h2M8 15h2" />
        </svg>
      );
    case "users":
      return (
        <svg {...common}>
          <circle cx="9" cy="8" r="3.5" />
          <path d="M2 20c1-3.5 3.5-5 7-5s6 1.5 7 5" />
          <circle cx="17" cy="9" r="2.5" />
          <path d="M22 18c-.5-2-2-3-4-3" />
        </svg>
      );
    case "card":
      return (
        <svg {...common}>
          <rect x="3" y="6" width="18" height="13" rx="2" />
          <path d="M3 10h18" />
        </svg>
      );
    case "doc":
      return (
        <svg {...common}>
          <path d="M6 3h8l4 4v14H6z" />
          <path d="M14 3v4h4" />
          <path d="M9 13h6M9 17h6" />
        </svg>
      );
    case "spark":
      return (
        <svg {...common}>
          <path d="M12 3l1.7 4.3L18 9l-4.3 1.7L12 15l-1.7-4.3L6 9l4.3-1.7z" />
          <path d="M19 15l.6 1.4L21 17l-1.4.6L19 19l-.6-1.4L17 17l1.4-.6z" />
        </svg>
      );
    case "link":
      return (
        <svg {...common}>
          <path d="M10 13a4 4 0 0 0 5.7 0l3-3a4 4 0 1 0-5.7-5.7l-1 1" />
          <path d="M14 11a4 4 0 0 0-5.7 0l-3 3a4 4 0 1 0 5.7 5.7l1-1" />
        </svg>
      );
    case "book":
      return (
        <svg {...common}>
          <path d="M4 5a2 2 0 0 1 2-2h12v15H6a2 2 0 0 0-2 2z" />
          <path d="M4 18a2 2 0 0 1 2-2h12" />
          <path d="M8 7h7" />
        </svg>
      );
    case "shield":
      return (
        <svg {...common}>
          <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />
        </svg>
      );
    case "bell":
      return (
        <svg {...common}>
          <path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2H4.5z" />
          <path d="M10 21a2 2 0 0 0 4 0" />
        </svg>
      );
    default:
      return null;
  }
}

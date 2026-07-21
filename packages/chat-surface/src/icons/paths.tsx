// Canonical icon glyph set — the SINGLE source of truth for line iconography
// across the shell (rail, settings nav, ⌘K palette, destination rows).
//
// Ported byte-faithfully from the 0xCopilot v3 design `Icon` registry
// (`copilot-data.jsx`): all glyphs are authored on a 0 0 24 24 viewBox, stroke
// only (`fill: none`, `stroke: currentColor`), round caps/joins, stroke-width
// 1.7. Each entry lists ONLY the inner geometry; the wrapping <svg> (and every
// shared attribute) is supplied by <Icon> so a glyph can never disagree with the
// frame. Add a glyph here — never inline an <svg> in a surface again.
//
// PRD: docs/plan/frontend-parity-v3/PRD-A-icon-system.md (FR-A.2).

import type { ReactNode } from "react";

/** Every icon this package can render. Superset of `SettingsNavIcon`. */
export type IconName =
  // rail destinations
  | "run"
  | "chats"
  | "folder"
  | "activity"
  | "plug"
  | "skill"
  | "gear"
  // settings nav
  | "user"
  | "sun"
  | "cmd"
  | "key"
  | "chip"
  | "sliders"
  | "shield"
  | "bell"
  | "lock"
  | "bolt"
  | "coin"
  // palette / actions / rows
  | "search"
  | "plus"
  | "check"
  | "x"
  | "chevronRight"
  | "chevronDown"
  | "back"
  | "trash"
  | "download"
  | "external"
  | "warn"
  | "send"
  | "globe"
  | "eye"
  | "doc"
  | "clock"
  | "play"
  | "dots";

export const ICON_PATHS: Readonly<Record<IconName, ReactNode>> = {
  // ── rail destinations ───────────────────────────────────────────────────
  run: (
    <>
      <rect x="3" y="3" width="18" height="18" rx="4" />
      <path d="M10 9l5 3-5 3z" />
    </>
  ),
  chats: <path d="M4 5h16v10H9l-4 4z" />,
  folder: (
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
  ),
  activity: <path d="M3 12h4l2.5 7 5-14L17 12h4" />,
  plug: <path d="M9 3v6M15 3v6M6 9h12v3a6 6 0 0 1-12 0z M12 18v3" />,
  skill: <path d="M12 3l2.1 5.3L20 10l-5.9 1.7L12 17l-2.1-5.3L4 10l5.9-1.7z" />,
  gear: (
    <>
      <circle cx="12" cy="12" r="3.2" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1" />
    </>
  ),
  // ── settings nav ────────────────────────────────────────────────────────
  user: (
    <>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21c1.4-4 4.4-6 8-6s6.6 2 8 6" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.4 1.4M17.6 17.6L19 19M5 19l1.4-1.4M17.6 6.4L19 5" />
    </>
  ),
  cmd: (
    <path d="M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z" />
  ),
  key: (
    <>
      <circle cx="8" cy="14" r="4" />
      <path d="M11 12l9-9 2 2-2 2 2 2-2 2-2-2-3 3" />
    </>
  ),
  chip: (
    <>
      <rect x="6" y="6" width="12" height="12" rx="2" />
      <path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3" />
    </>
  ),
  sliders: (
    <>
      <path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M18 18h2" />
      <circle cx="15" cy="6" r="2" />
      <circle cx="9" cy="12" r="2" />
      <circle cx="15" cy="18" r="2" />
    </>
  ),
  shield: <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />,
  bell: (
    <>
      <path d="M6 16V11a6 6 0 1 1 12 0v5l1.6 2H4.4z" />
      <path d="M10 21a2 2 0 0 0 4 0" />
    </>
  ),
  lock: (
    <>
      <rect x="4" y="10" width="16" height="11" rx="2.5" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </>
  ),
  bolt: <path d="M13 2L4 14h7l-1 8 9-12h-7z" />,
  coin: (
    <>
      <ellipse cx="12" cy="6.5" rx="8" ry="3.2" />
      <path d="M4 6.5v11c0 1.8 3.6 3.2 8 3.2s8-1.4 8-3.2v-11M4 12c0 1.8 3.6 3.2 8 3.2s8-1.4 8-3.2" />
    </>
  ),
  // ── palette / actions / rows ────────────────────────────────────────────
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-3.5-3.5" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  check: <path d="M5 12l5 5L20 7" />,
  x: <path d="M6 6l12 12M18 6L6 18" />,
  chevronRight: <path d="M9 6l6 6-6 6" />,
  chevronDown: <path d="M6 9l6 6 6-6" />,
  back: <path d="M15 6l-6 6 6 6" />,
  trash: <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" />,
  download: <path d="M12 4v11M7 10l5 5 5-5M4 20h16" />,
  external: (
    <path d="M14 4h6v6M20 4l-9 9M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5" />
  ),
  warn: (
    <>
      <path d="M12 3l10 17H2z" />
      <path d="M12 9v5M12 17.5v.5" />
    </>
  ),
  send: <path d="M4 12l16-8-6 16-3.5-6.5z" />,
  globe: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18" />
    </>
  ),
  eye: (
    <>
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  doc: (
    <>
      <path d="M6 3h8l4 4v14H6z" />
      <path d="M14 3v4h4" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  play: <path d="M7 5l12 7-12 7z" />,
  dots: (
    <>
      <circle cx="5" cy="12" r="1.3" />
      <circle cx="12" cy="12" r="1.3" />
      <circle cx="19" cy="12" r="1.3" />
    </>
  ),
};

/** Frozen list of every valid icon name (stable order for table tests / pickers). */
export const ICON_NAMES: readonly IconName[] = Object.freeze(
  Object.keys(ICON_PATHS) as IconName[],
);

/** Runtime guard for data-driven call sites that map string keys to icons. */
export function hasIcon(name: string): name is IconName {
  return Object.prototype.hasOwnProperty.call(ICON_PATHS, name);
}

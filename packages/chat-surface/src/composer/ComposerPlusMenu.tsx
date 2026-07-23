// ComposerPlusMenu — the composer `+` popover (attach + MCP/skills drill-downs).
//
// Design parity, composer punch-list rows 44 + 46. This used to be a THIRD
// popover idiom (`.aui-plus-menu` / `.aui-trigger-popover__*`) living next to
// the model popover's `.ui-pop` and the tools popover's inline styles — three
// implementations of one design element. It now renders the SHARED `.ui-pop*`
// recipe from `@0x-copilot/design-system` (the design's `.pop` family in
// `tools/design-parity/design-kit/copilot-v3.css`), at the design's 296px attach
// width, with the design's structure:
//
//   .ui-pop__h        "Attach" + `drag & drop works too` meta (row 44 §2)
//   .ui-pop__list     24px-badge rows (`.ui-pop-row` + __lg/__m/__nm/__txt/__sb)
//   .ui-pop__div      divider between the attach actions and the drill-downs
//   .ui-pop__grp      mono/uppercase group heading
//   .ui-pop-row--pin  pinned action (the MCP view's connector suggestions)
//   .ui-pop__f        footer escapes (`← Back`, `Open … settings →`)
//
// ROW 46 — click-out scrim. The design puts a transparent `.pop-scrim` behind
// every popover (fixed, inset 0, z-index 70; the panel sits at 71) and dismisses
// on mousedown. That is a RENDERED element, not a `document` listener, which is
// exactly what this package needs (bare globals are eslint-banned here). It is
// opt-in via the `onDismiss` prop: today the HOST wrapper injected through
// `AssistantComposer`'s `renderPlusMenu` slot (web `AnchoredPlusMenu`, desktop
// `DesktopAnchoredPlusMenu`) owns outside-click dismissal with its own
// pointerdown listener, so the scrim stays unmounted unless a caller passes
// `onDismiss` — the design-system's "SCRIM **OR** Menu, never both" rule. Pass
// it (and the panel's `position: relative` below keeps `.ui-pop`'s z-index 71
// above the scrim's 70) to get the design's semantics: mousedown outside closes,
// Escape closes.
//
// Sub-line copy note: `.ui-pop-row__sb` truncates with an ellipsis by design
// (the design's sub-lines are short fragments — "any file up to 100 MB"). The
// old sentence-length descriptions would have rendered as "Upload PDF, DOCX,
// spreadsheets, slid…", so they are trimmed to design-length fragments here.
// Row TITLES are byte-unchanged (`Attach Image` / `Attach File` / `MCP Servers`
// / `Skills`) — the file-picker tests match the menuitem accessible name.

import type { McpServer, Skill } from "@0x-copilot/api-types";
import type {
  CSSProperties,
  KeyboardEvent,
  ReactElement,
  ReactNode,
} from "react";

import { Icon } from "../icons/Icon";
import { providerInitials } from "../icons/providerMarks";

export type ComposerMenuView = "root" | "mcp" | "skills";

/** The design's `.pop` width for the composer attach menu (copilot-composer2
 *  renders `<Pop>` at its 296 default). `maxWidth` is the only defensive
 *  addition — a fixed 296px would overflow a very narrow window. */
const panelStyle: CSSProperties = {
  width: 296,
  maxWidth: "calc(100vw - 2rem)",
  // Pairs with `.ui-pop`'s `z-index: 71` so the panel stays ABOVE the scrim
  // (70) instead of having its own clicks swallowed by it.
  position: "relative",
};

/* ── glyphs ──────────────────────────────────────────────────────────────
 * The two attach glyphs are the design's own paths (copilot-composer2.jsx
 * `CIcon.clip` / `CIcon.img`), copied verbatim. Everything else comes from the
 * icon SSOT (`<Icon>`). `.ui-pop-row__lg svg` sizes them to 13px; the width /
 * height attributes restate that value so the markup is honest without CSS. */
const glyphProps = {
  viewBox: "0 0 24 24",
  width: 13,
  height: 13,
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.7,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

const ClipGlyph = (): ReactElement => (
  <svg {...glyphProps}>
    <path d="M21 11.5l-8.5 8.5a5.4 5.4 0 0 1-7.6-7.6L13.5 3.8a3.6 3.6 0 0 1 5.1 5.1l-8.6 8.6a1.8 1.8 0 0 1-2.5-2.5l7.9-7.9" />
  </svg>
);

const ImageGlyph = (): ReactElement => (
  <svg {...glyphProps}>
    <rect x="3" y="5" width="18" height="14" rx="2" />
    <circle cx="8.5" cy="10" r="1.4" />
    <path d="M21 16l-4.5-4.5L7 21" />
  </svg>
);

/* ── row / note primitives (pure `.ui-pop*` composition) ────────────────── */

function MenuRow({
  badge,
  title,
  sub,
  onClick,
  hint,
  pinned,
}: {
  badge: ReactNode;
  title: string;
  sub: string;
  onClick: () => void;
  /** `title` attribute — the hover tooltip; unchanged from the pre-migration markup. */
  hint: string;
  pinned?: boolean;
}): ReactElement {
  return (
    <button
      className={pinned === true ? "ui-pop-row ui-pop-row--pin" : "ui-pop-row"}
      type="button"
      role="menuitem"
      title={hint}
      onClick={onClick}
    >
      <span className="ui-pop-row__lg">{badge}</span>
      <span className="ui-pop-row__m">
        <span className="ui-pop-row__nm">
          <span className="ui-pop-row__txt">{title}</span>
        </span>
        <span className="ui-pop-row__sb">{sub}</span>
      </span>
    </button>
  );
}

/** Loading / empty text inside a `.ui-pop__list` — the sub-line type, padded
 *  to the row rhythm. Not a `.ui-pop-row`: it must not take the hover fill. */
function MenuNote({ children }: { children: ReactNode }): ReactElement {
  return (
    <div className="ui-pop-row__sb" style={noteStyle}>
      {children}
    </div>
  );
}

const noteStyle: CSSProperties = { padding: "6px 9px", whiteSpace: "normal" };

/** Footer control. `.ui-pop__f-link` was authored for the design's `<a>`, so it
 *  does not reset native `<button>` chrome — `.aui-plus-menu__back` (composer.css)
 *  is the one surviving local rule and does exactly that, nothing else. */
function FooterButton({
  hint,
  onClick,
  children,
}: {
  hint: string;
  onClick: () => void;
  children: ReactNode;
}): ReactElement {
  return (
    <button
      className="ui-pop__f-link aui-plus-menu__back"
      type="button"
      title={hint}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

export function ComposerPlusMenu({
  view,
  connectors,
  skills,
  onBack,
  onAttachImage,
  onAttachFile,
  onOpenMcp,
  onOpenSkills,
  onOpenMcpSettings,
  onOpenSkillsSettings,
  onShowConnectors,
  onUseMcpServer,
  onUseSkill,
  onDismiss,
}: {
  view: ComposerMenuView;
  connectors: {
    servers: McpServer[];
    loading: boolean;
  };
  skills: {
    skills: Skill[];
    loading: boolean;
  };
  onBack: () => void;
  onAttachImage: () => void;
  onAttachFile: () => void;
  onOpenMcp: () => void;
  onOpenSkills: () => void;
  onOpenMcpSettings: () => void;
  onOpenSkillsSettings: () => void;
  onShowConnectors: () => void;
  onUseMcpServer: (server: McpServer) => void;
  onUseSkill: (skill: Skill) => void;
  /**
   * Close the whole menu. When supplied, the design's transparent click-out
   * scrim (`.ui-pop-scrim`) renders behind the panel and Escape dismisses —
   * the design's popover semantics. Omit it when the host wrapper already owns
   * outside-click dismissal (scrim OR Menu, never both).
   */
  onDismiss?: () => void;
}): ReactElement {
  const enabledSkills = skills.skills.filter((skill) => skill.enabled);

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (event.key === "Escape" && onDismiss !== undefined) {
      event.stopPropagation();
      onDismiss();
    }
  };

  // The design's `<Pop>`: scrim first, then the panel (siblings, never nested).
  const frame = (label: string, body: ReactNode): ReactElement => (
    <>
      {onDismiss !== undefined ? (
        <div className="ui-pop-scrim" onMouseDown={onDismiss} />
      ) : null}
      <div
        className="ui-pop"
        role="menu"
        aria-label={label}
        style={panelStyle}
        onKeyDown={handleKeyDown}
      >
        {body}
      </div>
    </>
  );

  if (view === "mcp") {
    return frame(
      "MCP server menu",
      <>
        <div className="ui-pop__h">
          MCP Servers
          <span className="ui-pop__h-meta">
            {connectors.loading
              ? "loading…"
              : `${connectors.servers.length} available`}
          </span>
        </div>
        <div className="ui-pop__list">
          {connectors.loading ? (
            <MenuNote>Loading servers...</MenuNote>
          ) : connectors.servers.length === 0 ? (
            <MenuNote>No MCP servers configured.</MenuNote>
          ) : (
            connectors.servers.map((server) => (
              <MenuRow
                key={server.server_id}
                badge={providerInitials(server.display_name)}
                title={server.display_name}
                sub={`${server.enabled ? "Enabled" : "Disabled"} · ${server.auth_state.replaceAll("_", " ")}`}
                hint={`Use ${server.display_name} MCP server`}
                onClick={() => onUseMcpServer(server)}
              />
            ))
          )}
        </div>
        <MenuRow
          pinned
          badge={<Icon name="plug" size={13} />}
          title="Show connector suggestions"
          sub="Review servers that need authentication."
          hint="Show connector suggestions"
          onClick={onShowConnectors}
        />
        <div className="ui-pop__f">
          <FooterButton
            hint="Back to attachment and tools menu"
            onClick={onBack}
          >
            ← Back
          </FooterButton>
          <span className="ui-pop__f-sp" />
          <FooterButton hint="Open MCP settings" onClick={onOpenMcpSettings}>
            Open MCP settings →
          </FooterButton>
        </div>
      </>,
    );
  }

  if (view === "skills") {
    return frame(
      "Skills menu",
      <>
        <div className="ui-pop__h">
          Skills
          <span className="ui-pop__h-meta">
            {skills.loading ? "loading…" : `${enabledSkills.length} enabled`}
          </span>
        </div>
        <div className="ui-pop__list">
          {skills.loading ? (
            <MenuNote>Loading skills...</MenuNote>
          ) : enabledSkills.length === 0 ? (
            <MenuNote>No enabled skills yet.</MenuNote>
          ) : (
            enabledSkills.map((skill) => (
              <MenuRow
                key={skill.skill_id}
                badge={<Icon name="skill" size={13} />}
                title={skill.display_name}
                sub={skill.description || skill.name}
                hint={`Use ${skill.display_name} skill`}
                onClick={() => onUseSkill(skill)}
              />
            ))
          )}
        </div>
        <div className="ui-pop__f">
          <FooterButton
            hint="Back to attachment and tools menu"
            onClick={onBack}
          >
            ← Back
          </FooterButton>
          <span className="ui-pop__f-sp" />
          <FooterButton
            hint="Open skill settings"
            onClick={onOpenSkillsSettings}
          >
            Open skill settings →
          </FooterButton>
        </div>
      </>,
    );
  }

  return frame(
    "Attachment and tools menu",
    <>
      <div className="ui-pop__h">
        Attach <span className="ui-pop__h-meta">drag &amp; drop works too</span>
      </div>
      <div className="ui-pop__list">
        <MenuRow
          badge={<ImageGlyph />}
          title="Attach Image"
          sub="PNG, JPG, GIF or WebP"
          hint="Attach an image"
          onClick={onAttachImage}
        />
        <MenuRow
          badge={<ClipGlyph />}
          title="Attach File"
          sub="PDF, DOCX, sheets, slides or text"
          hint="Attach a file"
          onClick={onAttachFile}
        />
        <div className="ui-pop__div" />
        <div className="ui-pop__grp">Tools &amp; skills</div>
        <MenuRow
          badge={<Icon name="plug" size={13} />}
          title="MCP Servers"
          sub="pick a server or open settings"
          hint="Open MCP server menu"
          onClick={onOpenMcp}
        />
        <MenuRow
          badge={<Icon name="skill" size={13} />}
          title="Skills"
          sub="pick an enabled skill or open settings"
          hint="Open skills menu"
          onClick={onOpenSkills}
        />
      </div>
    </>,
  );
}

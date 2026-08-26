// Per-tool presentation for transcript tool cards.
//
// WHY A REGISTRY — every tool used to render through one card whose body was
// `JSON.stringify(args)` and `JSON.stringify(result)` truncated at 600
// characters, so a file edit, a web search and a connector write were three
// visually identical rows of raw JSON. The projector had no tool-name branch at
// all. This module is that branch, and it is deliberately a lookup rather than
// a chain of conditionals inside the card: adding a view is one entry, and a
// tool with no entry keeps the generic card unchanged.
//
// WHAT A VIEW MAY READ — only `ToolCallEntry` fields the runtime actually
// supplies. The argument shapes below are transcribed from a real packaged
// run's `events.jsonl`:
//
//   edit_file   args {file_path, old_string, new_string}  output {content: prose}
//   write_file  args {file_path, content}                 output {content: prose}
//   read_file   args {file_path}                          output {content: numbered text}
//   glob        args {pattern, path}                      output {content: "['/a', '/b']"}
//
// Note `output.content` is always a STRING, and its format varies by tool
// (prose, a Python list repr, line-numbered text). Views parse defensively and
// fall back to the raw text rather than asserting a shape.

import type { CSSProperties, ReactElement, ReactNode } from "react";

import { Icon } from "../icons/Icon";
import type { ToolCallEntry } from "./eventProjector";
import { computeLineDiff } from "./lineDiff";
import { TcFileDiff } from "./TcFileDiff";

export type ToolViewKind = "edit" | "write" | "read" | "search" | "generic";

export interface ToolViewBodyProps {
  readonly toolCall: ToolCallEntry;
}

export interface ToolView {
  readonly kind: ToolViewKind;
  /** Tile glyph. `null` keeps the letter tile, which still distinguishes the
   *  long tail of connector tools the registry says nothing about. */
  readonly icon: ReactNode | null;
  /** Short identity line under the title — a filename, a glob pattern. */
  readonly subtitle: (toolCall: ToolCallEntry) => string | null;
  /** Specialised disclosure body. `null` falls through to the generic rows. */
  readonly Body: ((props: ToolViewBodyProps) => ReactElement | null) | null;
}

/* ── argument readers ──────────────────────────────────────────────────── */

function str(
  args: Record<string, unknown> | undefined,
  key: string,
): string | undefined {
  const value = args?.[key];
  return typeof value === "string" ? value : undefined;
}

function resultText(toolCall: ToolCallEntry): string | undefined {
  const content = toolCall.result?.["content"];
  return typeof content === "string" ? content : undefined;
}

/** The trailing path segment, for a card header that must stay on one line. */
export function basename(path: string): string {
  const trimmed = path.replace(/\/+$/, "");
  const cut = trimmed.lastIndexOf("/");
  return cut === -1 ? trimmed : trimmed.slice(cut + 1);
}

/* ── icons ─────────────────────────────────────────────────────────────── */

// Glyphs come from the package's canonical set (`icons/paths.tsx`) via <Icon>.
// Hand-drawing an <svg> in a surface is banned precisely so geometry and frame
// cannot drift apart; `pencil` and `docPlus` were added there for these cards.
const TOOL_ICON_SIZE = 13;

const EditIcon = <Icon name="pencil" size={TOOL_ICON_SIZE} />;
const WriteIcon = <Icon name="docPlus" size={TOOL_ICON_SIZE} />;
const ReadIcon = <Icon name="doc" size={TOOL_ICON_SIZE} />;
const SearchIcon = <Icon name="search" size={TOOL_ICON_SIZE} />;

/* ── bodies ────────────────────────────────────────────────────────────── */

/**
 * `edit_file` — a string replacement, rendered as a real diff.
 *
 * The runtime emits no structured diff (its result is one prose sentence), so
 * the before/after come from the call's own arguments. While the arguments are
 * still streaming they are absent, and the body renders nothing rather than a
 * half-diff.
 */
function EditBody({ toolCall }: ToolViewBodyProps): ReactElement | null {
  const filePath = str(toolCall.args, "file_path");
  const before = str(toolCall.args, "old_string");
  const after = str(toolCall.args, "new_string");
  if (before === undefined || after === undefined) return null;
  const diff = computeLineDiff(before, after);
  if (diff.hunks.length === 0) return null;
  return (
    <TcFileDiff diff={diff} filePath={filePath} testId="tc-tool-edit-diff" />
  );
}

/** `write_file` — the written content, as an all-additions diff. */
function WriteBody({ toolCall }: ToolViewBodyProps): ReactElement | null {
  const filePath = str(toolCall.args, "file_path");
  const content = str(toolCall.args, "content");
  if (content === undefined) return null;
  const diff = computeLineDiff("", content);
  if (diff.hunks.length === 0) return null;
  return (
    <TcFileDiff diff={diff} filePath={filePath} testId="tc-tool-write-diff" />
  );
}

/**
 * `read_file` — the file as the tool returned it.
 *
 * deepagents already prefixes each line with its number, so the text is shown
 * verbatim rather than re-numbered; re-numbering would double the gutter.
 */
function ReadBody({ toolCall }: ToolViewBodyProps): ReactElement | null {
  const text = resultText(toolCall);
  if (text === undefined || text.trim() === "") return null;
  const lines = text.split("\n");
  const shown = lines.slice(0, READ_MAX_LINES);
  const omitted = lines.length - shown.length;
  return (
    <div style={panelStyle} data-testid="tc-tool-read-preview">
      <pre style={preStyle}>{shown.join("\n")}</pre>
      {omitted > 0 ? (
        <p style={footNoteStyle}>
          {omitted} more {omitted === 1 ? "line" : "lines"} not shown
        </p>
      ) : null}
    </div>
  );
}

const READ_MAX_LINES = 120;

/**
 * `glob` / `grep` / `ls` — the matched paths as a list.
 *
 * The runtime hands these back as a Python list repr (`"['/a', '/b']"`), not
 * JSON, so the quoted segments are extracted rather than parsed. Anything that
 * yields no matches falls back to the raw text, which is the honest rendering
 * of an output shape this does not recognise.
 */
function SearchBody({ toolCall }: ToolViewBodyProps): ReactElement | null {
  const text = resultText(toolCall);
  if (text === undefined || text.trim() === "") return null;
  const matches = parsePathList(text);
  if (matches === null) {
    return (
      <div style={panelStyle} data-testid="tc-tool-search-raw">
        <pre style={preStyle}>{text}</pre>
      </div>
    );
  }
  const shown = matches.slice(0, SEARCH_MAX_ROWS);
  const omitted = matches.length - shown.length;
  return (
    <div style={panelStyle} data-testid="tc-tool-search-matches">
      <p style={matchCountStyle}>
        {matches.length} {matches.length === 1 ? "match" : "matches"}
      </p>
      <ul style={matchListStyle}>
        {shown.map((path) => (
          <li key={path} style={matchRowStyle} title={path}>
            {path}
          </li>
        ))}
      </ul>
      {omitted > 0 ? (
        <p style={footNoteStyle}>{omitted} more not shown</p>
      ) : null}
    </div>
  );
}

const SEARCH_MAX_ROWS = 40;

/**
 * Quoted segments of a Python list repr, or `null` when the text is not one.
 *
 * Deliberately not `JSON.parse`: `['/a', '/b']` uses single quotes and is not
 * valid JSON, and the empty list `[]` must read as "no matches" rather than as
 * an unrecognised shape.
 */
export function parsePathList(text: string): readonly string[] | null {
  const trimmed = text.trim();
  if (!trimmed.startsWith("[") || !trimmed.endsWith("]")) return null;
  if (trimmed === "[]") return [];
  const quoted = trimmed.matchAll(/'([^']*)'|"([^"]*)"/g);
  const out: string[] = [];
  for (const match of quoted) out.push(match[1] ?? match[2] ?? "");
  return out.length > 0 ? out : null;
}

/* ── the registry ──────────────────────────────────────────────────────── */

const VIEWS: Readonly<Record<string, ToolView>> = {
  edit_file: {
    kind: "edit",
    icon: EditIcon,
    subtitle: (t) => {
      const p = str(t.args, "file_path");
      return p === undefined ? null : basename(p);
    },
    Body: EditBody,
  },
  write_file: {
    kind: "write",
    icon: WriteIcon,
    subtitle: (t) => {
      const p = str(t.args, "file_path");
      return p === undefined ? null : basename(p);
    },
    Body: WriteBody,
  },
  read_file: {
    kind: "read",
    icon: ReadIcon,
    subtitle: (t) => {
      const p = str(t.args, "file_path");
      return p === undefined ? null : basename(p);
    },
    Body: ReadBody,
  },
  glob: {
    kind: "search",
    icon: SearchIcon,
    subtitle: (t) => str(t.args, "pattern") ?? null,
    Body: SearchBody,
  },
  grep: {
    kind: "search",
    icon: SearchIcon,
    subtitle: (t) => str(t.args, "pattern") ?? null,
    Body: SearchBody,
  },
  ls: {
    kind: "search",
    icon: SearchIcon,
    subtitle: (t) => {
      const p = str(t.args, "path");
      return p === undefined ? null : basename(p);
    },
    Body: SearchBody,
  },
};

const GENERIC_VIEW: ToolView = {
  kind: "generic",
  icon: null,
  subtitle: () => null,
  Body: null,
};

/** The view for `toolName`; the generic card for anything unregistered. */
export function toolViewFor(toolName: string): ToolView {
  return VIEWS[toolName] ?? GENERIC_VIEW;
}

/** Tool names with a specialised view — for tests and for the export surface. */
export const SPECIALISED_TOOL_NAMES: readonly string[] = Object.keys(VIEWS);

/* ── shared body chrome ────────────────────────────────────────────────── */

const panelStyle: CSSProperties = {
  background: "var(--color-surface)",
  border: "1px solid var(--color-border)",
  borderRadius: 8,
  overflow: "hidden",
};

const preStyle: CSSProperties = {
  color: "var(--color-text-strong)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-10)",
  lineHeight: "17px",
  margin: 0,
  maxHeight: 320,
  overflow: "auto",
  padding: "8px 10px",
  whiteSpace: "pre",
};

const matchCountStyle: CSSProperties = {
  borderBottom: "1px solid var(--color-border)",
  color: "var(--color-text-muted)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-9-5)",
  lineHeight: "14px",
  margin: 0,
  padding: "5px 10px",
};

const matchListStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  maxHeight: 260,
  overflow: "auto",
  padding: "4px 0",
};

const matchRowStyle: CSSProperties = {
  color: "var(--color-text-strong)",
  direction: "rtl",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-10)",
  lineHeight: "18px",
  overflow: "hidden",
  padding: "0 10px",
  textAlign: "left",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

const footNoteStyle: CSSProperties = {
  borderTop: "1px solid var(--color-border)",
  color: "var(--color-text-subtle)",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-9-5)",
  lineHeight: "14px",
  margin: 0,
  padding: "5px 10px",
};

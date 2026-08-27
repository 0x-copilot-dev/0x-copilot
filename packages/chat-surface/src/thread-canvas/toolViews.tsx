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
//
// ONE ENTRY HERE IS AHEAD OF ITS TOOL. `run_command` is registered from a
// contract (PRD-shell-execution §4.2/§4.3/§14.3), not from a captured run,
// because Phase 0 builds the seam before the tool exists: nothing in the
// runtime emits that name today, so the entry is unreachable and no card
// changes. Its readers are correspondingly stricter than the ones above —
// where a captured shape can be asserted, a contracted one is checked.

import type { CSSProperties, ReactElement, ReactNode } from "react";

import { Icon } from "../icons/Icon";
import type { ToolCallEntry } from "./eventProjector";
import { computeLineDiff } from "./lineDiff";
import { TcFileDiff } from "./TcFileDiff";

export type ToolViewKind =
  | "edit"
  | "write"
  | "read"
  | "search"
  | "command"
  | "generic";

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
  /**
   * Open the disclosure without a click.
   *
   * True only for the file-change views, and it is not a preference. A live run
   * showed the diff sitting inside a collapsed `<details>`: present in the DOM,
   * counted by a passing DOM assertion, and invisible to the reader. For an
   * agent that edits files the change IS the message, so burying it one click
   * deep makes the transcript read exactly as raw as it did before the card
   * existed. Reads and searches stay closed — a 120-line file preview expanded
   * by default would flood the transcript it is meant to clarify.
   */
  readonly defaultOpen: boolean;
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
    <TcFileDiff
      diff={diff}
      filePath={filePath}
      applied={toolCall.status === "complete"}
      testId="tc-tool-edit-diff"
    />
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
    <TcFileDiff
      diff={diff}
      filePath={filePath}
      applied={toolCall.status === "complete"}
      testId="tc-tool-write-diff"
    />
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
 * `run_command` — the §14.3 transcript block: the command, its output, and the
 * exit code.
 *
 * TWO SOURCES, AND THE LIFECYCLE PICKS. While the call is open there is no
 * result, only `outputPreview` — the rolling tail the projector holds from
 * `tool_call_delta` frames. Once it settles, the tool's own result is
 * authoritative: the runtime tail-keeps it to 64 KiB and puts its truncation
 * notice inside the string (§13), so it says more than the live tail ever can.
 * The preview stays as the fallback for the one case where the result is
 * missing — a run cancelled mid-command — rather than being preferred.
 *
 * THE EXIT CODE IS RENDERED, in three states not two. `status === "error"`
 * means the TOOL failed; a non-zero exit means the tool worked and the command
 * did not, and OpenCode showing neither is the defect §14.3 cites. Non-zero is
 * amber, never the destructive hue: `grep` exits 1 on no matches.
 */
function CommandBody({ toolCall }: ToolViewBodyProps): ReactElement | null {
  const command = commandText(toolCall);
  const settled = readCommandOutcome(toolCall);
  // The result, once we have one, is the WHOLE answer — including when it says
  // the command printed nothing. Only an absent (or unreadable) result falls
  // back to the live tail, so a settled "no output" is never contradicted by a
  // stale mid-run frame.
  const output = nonEmpty(
    settled === null ? toolCall.outputPreview : settled.output,
  );
  // Nothing known yet — arguments still streaming, no output, no result. An
  // empty bordered frame claims there is something to look at.
  if (command === null && output === null) return null;
  const exitCode = settled?.exitCode ?? null;

  return (
    <div style={panelStyle} data-testid="tc-tool-command">
      {command === null ? null : (
        <div style={commandRowStyle}>
          {/* Decorative. `aria-hidden` + `user-select: none` so a screen reader
              does not announce it and a copy yields the command alone. */}
          <span style={promptStyle} aria-hidden="true">
            $
          </span>
          <code style={commandTextStyle} data-testid="tc-tool-command-text">
            {command}
          </code>
          {exitCode === null ? null : (
            <span
              style={exitCode === 0 ? exitOkStyle : exitNonZeroStyle}
              data-testid="tc-tool-command-exit"
              // The state, stamped, so a journey asserts which of the three it
              // is without reading a colour or a word — the same discipline as
              // `data-tool-blocked` on the card root.
              data-exit={exitCode === 0 ? "ok" : "nonzero"}
            >
              exit {exitCode}
            </span>
          )}
        </div>
      )}
      {output === null ? null : <CommandOutput text={output} />}
    </div>
  );
}

/** Text worth a frame, or `null`. A command that printed only a newline has
 *  printed nothing a reader needs to see. */
function nonEmpty(text: string | undefined): string | null {
  return text === undefined || text.trim() === "" ? null : text;
}

/**
 * The output block — bounded in LINES, tail-kept, in its own scroller.
 *
 * ONE `<pre>`, not one element per line, so the mount cost of a command that
 * printed 50 000 lines is one node rather than 50 000. The line clip is what
 * bounds the remaining cost: a single text node still gives the browser one
 * line box per line, and `TcChat`'s render budget cannot help here — it folds
 * whole cards, so every row inside the newest card is mounted regardless.
 *
 * The last `COMMAND_MAX_LINES`, for the same reason the runtime tail-keeps the
 * bytes (§13): the error is at the end.
 *
 * ⚠️ NOT DONE HERE: §14.3 wants surviving ANSI rendered AS COLOUR. That is a
 * tokenizer plus a token→theme mapping, i.e. the colour work `syntaxTokens`
 * already does for code blocks, and it is deliberately not started — the
 * executor sets `TERM=dumb` + `NO_COLOR=1` (§13), so this stream should carry
 * no escapes at all. Quietly STRIPPING them instead would be the worst of the
 * three options: it makes the missing feature look shipped.
 */
function CommandOutput({ text }: { readonly text: string }): ReactElement {
  const lines = text.split("\n");
  const shown =
    lines.length > COMMAND_MAX_LINES
      ? lines.slice(lines.length - COMMAND_MAX_LINES)
      : lines;
  const omitted = lines.length - shown.length;
  return (
    <div style={commandOutputWrapStyle}>
      {omitted > 0 ? (
        <p style={commandClipNoteStyle} data-testid="tc-tool-command-clipped">
          {omitted} earlier {omitted === 1 ? "line" : "lines"} not shown
        </p>
      ) : null}
      <pre style={preStyle} data-testid="tc-tool-command-output">
        {shown.join("\n")}
      </pre>
    </div>
  );
}

/**
 * Lines one command card mounts. ~2.5 screens at 17px line-height, which is
 * enough to read a stack trace or a test summary without the transcript
 * becoming a terminal emulator. The bytes are already capped upstream
 * (`TOOL_OUTPUT_PREVIEW_CAP` live, 64 KiB settled); this caps LAYOUT, which
 * bytes do not — 8 KiB of two-character lines is 4000 line boxes.
 *
 * Deliberately larger than `READ_MAX_LINES` (120): a file preview is a lookup
 * the reader can repeat, a command's output is a one-time event.
 */
export const COMMAND_MAX_LINES = 200;

/** The command verbatim, or `null` while the arguments are still streaming. */
function commandText(toolCall: ToolCallEntry): string | null {
  const command = str(toolCall.args, "command");
  if (command === undefined || command.trim() === "") return null;
  return command;
}

/**
 * The command clamped to the card's one-line identity slot.
 *
 * The whitespace collapse is done in the DATA, not left to the renderer. The
 * row is `white-space: nowrap`, so HTML would already draw a newline as a
 * space — but the newline would survive in `textContent`, in a copy, and in an
 * accessible name, i.e. everywhere except the pixels. A `run_command` argument
 * is allowed to be multi-line (§4.2 permits `\t\n\r`), so this is the ordinary
 * case rather than the exotic one. The block below renders it unmangled.
 */
function commandSubtitle(toolCall: ToolCallEntry): string | null {
  const command = commandText(toolCall);
  return command === null ? null : command.replace(/\s+/g, " ").trim();
}

/**
 * What the card needs from a `RunCommandResult`.
 *
 * `output` is always a string — the empty one when the command printed nothing,
 * which is a fact and not an absence. `exitCode` is genuinely nullable: §4.3
 * defines it as `None` for every status other than `completed`, so a timeout
 * has no exit code to show.
 */
interface CommandOutcome {
  readonly output: string;
  readonly exitCode: number | null;
}

/**
 * The command's outcome, read from whichever shape the result arrived in.
 *
 * §4.3 specifies a `RunCommandResult` returned "as a JSON string, not prose",
 * and deepagents delivers a tool's return value inside `output.content` — so
 * the expected shape is a JSON document nested one layer deeper than the other
 * views' results, and both layers are checked rather than assumed. The direct
 * arm covers a runtime that lands the fields on `output` itself.
 *
 * Anything unrecognised falls back to the raw text, which is this file's
 * standing rule: showing the string is honest about a shape we do not know,
 * showing nothing is not.
 */
function readCommandOutcome(toolCall: ToolCallEntry): CommandOutcome | null {
  const result = toolCall.result;
  if (result === undefined) return null;
  const direct = readCommandResultRecord(result);
  if (direct !== null) return direct;
  const text = resultText(toolCall);
  if (text === undefined || text.trim() === "") return null;
  return parseCommandResult(text) ?? { output: text, exitCode: null };
}

/**
 * A `RunCommandResult` record, or `null` for anything else.
 *
 * Gated on `status` AND `output` — both required by the contract — because a
 * command whose own stdout happens to be JSON would otherwise be mistaken for
 * the envelope and rendered as its own metadata.
 */
function readCommandResultRecord(
  record: Record<string, unknown>,
): CommandOutcome | null {
  if (typeof record["status"] !== "string") return null;
  const output = record["output"];
  if (typeof output !== "string") return null;
  const exitCode = record["exit_code"];
  return {
    output,
    exitCode:
      typeof exitCode === "number" && Number.isInteger(exitCode)
        ? exitCode
        : null,
  };
}

/** The bound mirrors `eventProjector`'s `EMBEDDED_TOOL_RESULT_PARSE_CAP`, and
 *  for the same reason: parsing is attempted on an arbitrary tool string, so it
 *  is attempted on a bounded one. */
const COMMAND_RESULT_PARSE_CAP = 64 * 1024;

function parseCommandResult(text: string): CommandOutcome | null {
  const trimmed = text.trim();
  if (!trimmed.startsWith("{") || trimmed.length > COMMAND_RESULT_PARSE_CAP) {
    return null;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return null;
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return null;
  }
  return readCommandResultRecord(parsed as Record<string, unknown>);
}

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
    defaultOpen: true,
  },
  write_file: {
    kind: "write",
    icon: WriteIcon,
    subtitle: (t) => {
      const p = str(t.args, "file_path");
      return p === undefined ? null : basename(p);
    },
    Body: WriteBody,
    defaultOpen: true,
  },
  read_file: {
    kind: "read",
    icon: ReadIcon,
    subtitle: (t) => {
      const p = str(t.args, "file_path");
      return p === undefined ? null : basename(p);
    },
    Body: ReadBody,
    defaultOpen: false,
  },
  glob: {
    kind: "search",
    icon: SearchIcon,
    subtitle: (t) => str(t.args, "pattern") ?? null,
    Body: SearchBody,
    defaultOpen: false,
  },
  grep: {
    kind: "search",
    icon: SearchIcon,
    subtitle: (t) => str(t.args, "pattern") ?? null,
    Body: SearchBody,
    defaultOpen: false,
  },
  ls: {
    kind: "search",
    icon: SearchIcon,
    subtitle: (t) => {
      const p = str(t.args, "path");
      return p === undefined ? null : basename(p);
    },
    Body: SearchBody,
    defaultOpen: false,
  },
  // UNREACHABLE TODAY, ON PURPOSE. Phase 0 ships the seam dark: no `run_command`
  // tool is registered, so `toolViewFor` never returns this and no live card
  // changes. See the header note.
  run_command: {
    kind: "command",
    // No terminal glyph exists in the package's canonical set (`icons/paths.tsx`)
    // and hand-drawing an <svg> in a surface is banned, so this keeps the letter
    // tile rather than borrowing a glyph that means something else — `cmd` is the
    // ⌘ key and would read as a keyboard shortcut. Adding one is a Phase 1 item.
    icon: null,
    subtitle: (t) => commandSubtitle(t),
    Body: CommandBody,
    // §14.2. Command output is the case the `defaultOpen` comment above argues
    // hardest against: worse than a 120-line file preview, and unlike a diff it
    // is not the message — the model's next sentence usually is.
    //
    // ⚠️ §14.2 files ONE exception as open (OQ-6): open it when the command
    // ended non-zero, because then the failure IS the message. It is not
    // expressible here — `defaultOpen` is a constant on the view, not a
    // predicate over the call — so taking that option means changing this
    // registry's contract and `ToolCallCard`'s single read of it, not editing
    // this line. Deliberately left for whoever closes OQ-6.
    defaultOpen: false,
  },
};

const GENERIC_VIEW: ToolView = {
  kind: "generic",
  icon: null,
  subtitle: () => null,
  Body: null,
  defaultOpen: false,
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

/* ── command block (§14.3) ─────────────────────────────────────────────────
 *
 * Inline `CSSProperties`, like every other body in this file — which is the
 * strongest available answer to §14.3's requirement that this view's CSS ship
 * inside the package and no host stylesheet re-declare its class names. There
 * are no class names to re-declare: an inline style cannot be shadowed by a
 * host sheet at all. That is the failure mode PR #459 paid for (rules stranded
 * in `apps/frontend/src/styles.css`, so the surface was unstyled on desktop),
 * closed here by construction rather than by a guard test.
 */

const commandRowStyle: CSSProperties = {
  alignItems: "baseline",
  borderBottom: "1px solid var(--color-border)",
  display: "flex",
  gap: 7,
  padding: "7px 10px",
};

/** The prompt. Never selectable, never announced — copying the row must give
 *  the command and only the command. */
const promptStyle: CSSProperties = {
  color: "var(--color-text-subtle)",
  flex: "0 0 auto",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-10)",
  lineHeight: "17px",
  userSelect: "none",
};

/** Wraps rather than scrolls: the command is the thing being verified, so it is
 *  never half-visible. `anywhere` because a command is one unbroken token far
 *  more often than prose is. */
const commandTextStyle: CSSProperties = {
  color: "var(--color-text-strong)",
  flex: "1 1 auto",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-10)",
  lineHeight: "17px",
  minWidth: 0,
  overflowWrap: "anywhere",
  whiteSpace: "pre-wrap",
};

const exitChipStyle: CSSProperties = {
  borderRadius: 5,
  borderStyle: "solid",
  borderWidth: 1,
  flex: "0 0 auto",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--font-size-mono-9-5)",
  letterSpacing: "0.04em",
  lineHeight: "14px",
  padding: "1px 6px",
  whiteSpace: "nowrap",
};

const exitOkStyle: CSSProperties = {
  ...exitChipStyle,
  borderColor: "var(--color-success-line)",
  color: "var(--color-success)",
};

/**
 * WARNING, not danger, and this is a decision rather than a palette choice.
 * A non-zero exit is a third state: the tool worked, the command reported
 * something. `grep` exits 1 on no matches and `diff` on any difference —
 * painting those in the destructive hue would make the transcript's one
 * "something is wrong" colour mean "a command ran normally".
 */
const exitNonZeroStyle: CSSProperties = {
  ...exitChipStyle,
  borderColor: "var(--color-warning-line)",
  color: "var(--color-warning)",
};

const commandOutputWrapStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  minWidth: 0,
};

/** The clip notice sits ABOVE the output, unlike the read/search footnotes: the
 *  tail is what was kept, so the reader meets "there was more before this"
 *  before the text rather than after scrolling to the bottom of it. Hence the
 *  border flips sides — the command row above already draws one. */
const commandClipNoteStyle: CSSProperties = {
  ...footNoteStyle,
  borderBottom: "1px solid var(--color-border)",
  borderTop: 0,
};

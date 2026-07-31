// <ManageMcpModal /> — "Manage MCP": the whole MCP configuration as one
// editable JSON document.
//
// This replaces a single "Server URL" field that could express exactly one
// thing (a remote server with no credential and no headers) while the card
// launching it promised "paste a JSON config — stdio or remote". The document
// is the honest version of that promise: every server visible at once, added
// by pasting the block a project's README gives you, removed by deleting it.
//
// Substrate-agnostic (chat-surface boundary): no bare fetch / window /
// document / localStorage. The host binder supplies `document`, performs the
// save, and reports errors. This component owns editor state only.
//
// Secrets never round-trip. A stored credential arrives as its
// `${input:<id>}` placeholder and is edited as one; the plaintext travels
// only in `secrets`, which the host sends and the server never echoes back.
// That is what lets someone reformat this file without silently wiping a
// token they entered last week.

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import {
  Button,
  CodeEditor,
  Field,
  TextInput,
} from "@0x-copilot/design-system";

import { Modal } from "../../settings/Modal";

export interface ManageMcpSaveRequest {
  /** The parsed document, with any prompted values substituted in. */
  readonly document: unknown;
}

export interface ManageMcpModalProps {
  readonly open: boolean;
  readonly onClose: () => void;
  /** Current config document. `null` while the host is still loading it. */
  readonly document: unknown | null;
  readonly onSave: (request: ManageMcpSaveRequest) => void;
  /** True while the host is saving; disables the actions. */
  readonly pending?: boolean;
  /** Save failure from the host, rendered verbatim. */
  readonly error?: string | null;
  /** Outcome summary from the last successful save. */
  readonly result?: string | null;
}

const INDENT = 2;

// Characters of context V8 puts either side of a JSON syntax error when its
// message carries no explicit position. Used to recover the error's offset
// from the snippet; see `errorLine`.
const V8_CONTEXT = 10;

/** Pretty-print for display. Falls back to `{}` before the host has loaded. */
function formatDocument(value: unknown | null): string {
  if (value === null || value === undefined) return "{}";
  return JSON.stringify(value, null, INDENT);
}

/**
 * Where in `text` a `JSON.parse` failure happened, as a 1-based line number.
 *
 * A bare "Unexpected token" is useless in a 60-line config, and V8 states the
 * location in three different ways depending on the input, so all three are
 * read here rather than the one that happened to be in front of us:
 *
 *   1. `...(line 3 column 12)` — say it directly when offered.
 *   2. `...at position 42` — a character offset; count the newlines before it.
 *   3. `Unexpected token ',', ..."<snippet>" is not valid JSON` — no position
 *      at all, just a context snippet. Locating that snippet in the source
 *      recovers the line. This is the shape short documents produce, which is
 *      to say the shape a half-typed config produces — exactly when a line
 *      number is most wanted.
 *
 * Returns `null` when none of them apply, and the caller shows the raw message
 * rather than inventing a line.
 */
function errorLine(text: string, message: string): number | null {
  const direct = /line (\d+)/.exec(message)?.[1];
  if (direct !== undefined) return Number(direct);

  const position = /position (\d+)/.exec(message)?.[1];
  if (position !== undefined) {
    return text.slice(0, Number(position)).split("\n").length;
  }

  const context = /(\.\.\.)?"([\s\S]*?)"(?:\.\.\.)? is not valid JSON/.exec(
    message,
  );
  if (context === null) return null;
  const [, truncated, snippet] = context;
  const at = text.indexOf(snippet);
  if (at < 0) return null;
  // The snippet is context CENTRED on the error, not starting at it: V8 takes
  // up to `V8_CONTEXT` characters either side and marks a trimmed start with a
  // leading "...". So when the start was trimmed the error sits that far into
  // the snippet, and when it was not, the snippet begins at the document start
  // and the error is wherever it is — the snippet's own beginning is the best
  // available anchor. Anchoring on the snippet start in BOTH cases is what
  // reported the line above the real one.
  const offset = truncated === undefined ? at : at + V8_CONTEXT;
  return text.slice(0, offset).split("\n").length;
}

/** Parse the editor's text, reporting WHERE it broke. */
function parseDocument(text: string): { value: unknown } | { error: string } {
  try {
    return { value: JSON.parse(text) as unknown };
  } catch (err) {
    const message = err instanceof Error ? err.message : "Invalid JSON.";
    // Drop V8's inlined source snippet: the user is looking at that source
    // already, and repeating it turns a one-line status into a wall of text.
    const summary = message
      .replace(/,? \.\.\.?"[\s\S]*?"(?:\.\.\.)? is not valid JSON/, "")
      .replace(/ in JSON at position.*$/, "")
      .trim();
    const line = errorLine(text, message);
    return {
      error: line === null ? summary : `Line ${line}: ${summary}`,
    };
  }
}

/**
 * The `${input:<id>}` ids still awaiting a value anywhere in the document.
 *
 * These only ever arrive by paste: a config copied from a project's install
 * instructions carries them, and the server neither stores nor emits them.
 * Something has to ask, because the document is explicitly saying "this value
 * is not here" — and sending the literal `${input:github_mcp_pat}` as a bearer
 * token would earn a 401 nothing on screen could explain.
 */
function referencedInputs(value: unknown): string[] {
  const found = new Set<string>();
  const walk = (node: unknown): void => {
    if (typeof node === "string") {
      const match = /^\$\{input:([A-Za-z0-9][A-Za-z0-9._-]*)\}$/.exec(
        node.trim(),
      );
      if (match !== null) found.add(match[1]);
      return;
    }
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    if (node !== null && typeof node === "object") {
      Object.values(node as Record<string, unknown>).forEach(walk);
    }
  };
  walk(value);
  return [...found].sort();
}

/** Replace every `${input:<id>}` with the value the user supplied for it. */
function substituteInputs(
  value: unknown,
  secrets: Readonly<Record<string, string>>,
): unknown {
  if (typeof value === "string") {
    const match = /^\$\{input:([A-Za-z0-9][A-Za-z0-9._-]*)\}$/.exec(
      value.trim(),
    );
    const supplied = match === null ? undefined : secrets[match[1]]?.trim();
    return supplied !== undefined && supplied !== "" ? supplied : value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => substituteInputs(item, secrets));
  }
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, item]) => [
        key,
        substituteInputs(item, secrets),
      ]),
    );
  }
  return value;
}

export function ManageMcpModal({
  open,
  onClose,
  document: configDocument,
  onSave,
  pending = false,
  error = null,
  result = null,
}: ManageMcpModalProps): ReactElement {
  const [text, setText] = useState(() => formatDocument(configDocument));
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const editorRef = useRef<HTMLTextAreaElement | null>(null);

  // Re-seed from the host's document when the modal opens or the host
  // reloads it. Guarded on `open` so a background refetch cannot overwrite
  // text the user is in the middle of typing.
  useEffect(() => {
    if (!open) return;
    setText(formatDocument(configDocument));
    setSecrets({});
  }, [open, configDocument]);

  const parsed = useMemo(() => parseDocument(text), [text]);
  const parseError = "error" in parsed ? parsed.error : null;
  const referenced = useMemo(
    () => ("value" in parsed ? referencedInputs(parsed.value) : []),
    [parsed],
  );

  // Every reference still needs a value: the server never emits `${input:}`,
  // so anything the editor is showing arrived by paste and has nothing behind
  // it. There is no "already saved" case here — a stored credential shows as
  // the redaction marker, which needs no field at all.
  const missing = referenced.filter((id) => (secrets[id] ?? "").trim() === "");

  const handleFormat = useCallback(() => {
    if (!("value" in parsed)) return;
    setText(formatDocument(parsed.value));
  }, [parsed]);

  // Tab inserts indentation instead of leaving the editor. Without this, the
  // first Tab in a JSON editor moves focus to the Save button — which reads
  // as the editor being broken. Shift+Tab is left alone so keyboard users
  // retain a way out.
  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLTextAreaElement>): void => {
      if (event.key !== "Tab" || event.shiftKey) return;
      event.preventDefault();
      const field = event.currentTarget;
      const { selectionStart, selectionEnd, value } = field;
      const next =
        value.slice(0, selectionStart) +
        " ".repeat(INDENT) +
        value.slice(selectionEnd);
      setText(next);
      // Restore the caret after React re-renders with the new value.
      requestAnimationFrame(() => {
        field.selectionStart = field.selectionEnd = selectionStart + INDENT;
      });
    },
    [],
  );

  const handleSave = useCallback(() => {
    if (!("value" in parsed)) return;
    // Prompted values are substituted INTO the document rather than sent
    // beside it. One representation reaches the server, so there is no way for
    // an envelope and a document to disagree about a name that appears in
    // neither — and the server needs no notion of an "input" at all.
    onSave({ document: substituteInputs(parsed.value, secrets) });
  }, [onSave, parsed, secrets]);

  const blocked = parseError !== null || missing.length > 0 || pending;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Manage MCP"
      subtitle="servers the agent can reach"
      width={720}
      initialFocusRef={editorRef}
      footer={
        <div style={footerStyle}>
          <span style={statusStyle} role={parseError ? "alert" : undefined}>
            {parseError ??
              error ??
              (missing.length > 0
                ? `Needs a value for ${missing.join(", ")}`
                : (result ?? ""))}
          </span>
          <Button
            variant="ghost"
            onClick={handleFormat}
            disabled={parseError !== null || pending}
            type="button"
          >
            Format
          </Button>
          <Button onClick={handleSave} disabled={blocked} type="button">
            {pending ? "Saving…" : "Save"}
          </Button>
        </div>
      }
    >
      <div style={bodyStyle}>
        <CodeEditor
          ref={editorRef}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={handleKeyDown}
          aria-invalid={parseError !== null}
          aria-label="MCP configuration"
          data-testid="manage-mcp-editor"
        />

        {referenced.length > 0 && (
          <div style={secretsStyle}>
            {referenced.map((id) => (
              <Field
                key={id}
                label={id}
                hint="This config asks for a value that isn't in it yet."
              >
                <TextInput
                  type="password"
                  autoComplete="new-password"
                  value={secrets[id] ?? ""}
                  onChange={(event) =>
                    setSecrets((current) => ({
                      ...current,
                      [id]: event.target.value,
                    }))
                  }
                  placeholder="Paste the value"
                  data-testid={`manage-mcp-secret-${id}`}
                />
              </Field>
            ))}
          </div>
        )}
      </div>
    </Modal>
  );
}

const bodyStyle: CSSProperties = {
  display: "grid",
  gap: "var(--space-lg)",
};

const secretsStyle: CSSProperties = {
  display: "grid",
  gap: "var(--space-md)",
};

const footerStyle: CSSProperties = {
  alignItems: "center",
  display: "flex",
  gap: "var(--space-sm)",
  width: "100%",
};

// Takes the free space so the buttons sit at the tail, and stays quiet: this
// line carries a parse error, a save failure, or an outcome summary, and only
// the first of those is ever urgent.
const statusStyle: CSSProperties = {
  color: "var(--color-text-muted)",
  flex: 1,
  fontSize: "var(--font-size-xs)",
  minWidth: 0,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

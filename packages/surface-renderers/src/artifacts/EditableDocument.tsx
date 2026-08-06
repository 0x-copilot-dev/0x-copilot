// The document, edited where it renders.
//
// The complaint this exists to answer: a document artifact rendered its table
// beautifully and then asked the reader to change a cell by scrolling to a RAW
// MARKDOWN TEXTAREA underneath and finding it in pipe-delimited source. That
// textarea is deleted. The rendered block IS the control now — a cell opens an
// input in the cell, a paragraph opens a field where the paragraph is.
//
// Three properties hold this together, and none of them is decoration:
//
// 1. **Splice, never regenerate.** Save is `applyEdits(source, pending)`: every
//    byte outside an edited span is copied through untouched, so the prose
//    around the table, the links inside cells, and every construct the block
//    model declines to understand survive a round trip by construction. This
//    component never re-emits markdown; it only names spans.
//
// 2. **Nothing leaves on a keystroke.** Edits accumulate in local state and one
//    Save turns the batch into one new revision, through the SAME host call the
//    deleted textarea used. Discard drops the batch. A conflict keeps it — the
//    guarantee the old editor made and this one must keep.
//
// 3. **Editability is the HOST's to grant.** It arrives as `actions`, a live
//    function the host closed over its own transport. It is never on the wire,
//    never on a `SurfaceSpec`, and never inferred from content: a surface the
//    host did not deliberately open renders read-only.
//
// Every block that owns one text span is editable in place: a heading, a
// paragraph, and a `raw` block — the list, fence, quote, HTML, divider and
// setext heading the block model declines to model. A table is the one kind
// edited by its parts instead, because its spans are its cells. "In place" is
// the load-bearing half of that sentence: the field holds that block's own text
// at that block's own position, and never the document's source in a box
// underneath it. A checklist-only document is the case that made this
// non-optional — it is ONE raw block, so without it the toolbar invited a click
// that had no target anywhere on screen.
//
// What no block gets here is STRUCTURE. There is no way to add or delete a
// table row or column, add or delete a block, or reorder blocks — every one of
// those changes the document's SHAPE rather than a span in it, which is a
// different primitive from the one this file is built on.

import {
  useEffect,
  useMemo,
  useState,
  type ComponentPropsWithoutRef,
  type CSSProperties,
  type KeyboardEvent,
  type ReactElement,
  type ReactNode,
} from "react";
import {
  applyEdits,
  isExternalHref,
  MarkdownText,
  parseBlocks,
  type ColumnAlignment,
  type DocumentBlock,
  type HeadingBlock,
  type MarkdownTextProps,
  type ParagraphBlock,
  type RawBlock,
  type RawBlockReason,
  type TableBlock,
} from "@0x-copilot/chat-surface";

import {
  commitEdit,
  documentEditsFor,
  nextCellTarget,
  originalValue,
  revertEdit,
  targetKey,
  type EditTarget,
  type PendingEdit,
  type PendingEdits,
} from "./documentEdits";
import type { ArtifactEditorActions } from "./model";

const NO_PENDING_EDITS: PendingEdits = {};
const HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"] as const;

/**
 * What a raw block is CALLED, for its field's accessible name.
 *
 * The reason the scanner already recorded is the honest description of the
 * thing being edited — a screen reader should say "List", not "Block 3" — and
 * this map is the only place this component says anything at all about a
 * construct it deliberately does not model. It is total over every reason that
 * is editable, so a reason added upstream fails the typecheck here rather than
 * shipping a field with no name; `blank` is excluded because it renders no
 * field, and a label for it would be a string no one could ever hear.
 */
const RAW_LABEL: Record<Exclude<RawBlockReason, "blank">, string> = {
  blockquote: "Quote",
  "fenced-code": "Code block",
  html: "HTML block",
  "indented-code": "Code block",
  list: "List",
  "setext-heading": "Heading",
  "thematic-break": "Divider",
};

/**
 * A link inside an editable block stays a link.
 *
 * Clicking the anchor follows it; the click must NOT also bubble to the cell
 * and open an editor over the thing the reader just tried to visit. Stopping
 * propagation here is the whole mechanism — the alternative (inspecting the
 * event target's ancestors) would reach for the DOM globals this package bans.
 */
type AnchorSlotProps = ComponentPropsWithoutRef<"a"> & {
  /** The hast node streamdown passes alongside the DOM props. Never spread. */
  readonly node?: unknown;
};

/**
 * The same anchor policy the product's own markdown link applies — an absolute
 * http(s) href opens in a new tab and carries `rel="noreferrer"` — using the
 * product's own predicate rather than a second reading of "external". The one
 * thing added is the stopped click.
 */
function InlineAnchor({ node: _node, ...rest }: AnchorSlotProps): ReactElement {
  const external = isExternalHref(rest.href);
  return (
    <a
      {...rest}
      rel={external ? "noreferrer" : rest.rel}
      target={external ? "_blank" : rest.target}
      onClick={(event) => event.stopPropagation()}
    />
  );
}

function Unwrapped(props: { readonly children?: ReactNode }): ReactElement {
  return <>{props.children}</>;
}

// Streamdown's `Components` intersects a per-tag mapped type (whose `a` slot
// wants anchor props) with a catch-all index signature (whose slots want
// `Record<string, unknown>`), and no single props type is a supertype of both —
// so a slot cannot be written to satisfy the declared type, only to BE correct.
// The assertion is the boundary; the components above take exactly the props
// streamdown passes them.
const asComponents = (
  slots: Record<string, unknown>,
): MarkdownTextProps["components"] =>
  slots as unknown as MarkdownTextProps["components"];

/** Inline context (a cell, a heading): the paragraph box streamdown emits is dropped. */
const INLINE_COMPONENTS = asComponents({ p: Unwrapped, a: InlineAnchor });

/** Block context (a paragraph): the paragraph box is exactly what is wanted. */
const BLOCK_COMPONENTS = asComponents({ a: InlineAnchor });

const MARKDOWN_SIGNIFICANT = /[[\]`*_~<>\\!&]/;

/**
 * Renders a span's markdown, cheaply when there is none to render.
 *
 * A table of plain strings must not mount one markdown parser per cell, and the
 * overwhelming majority of cells are plain strings. Anything carrying a
 * markdown-significant character goes through the product's reviewed pipeline —
 * there is deliberately no second markdown renderer in this file.
 */
function InlineMarkdown(props: { readonly text: string }): ReactElement {
  if (!MARKDOWN_SIGNIFICANT.test(props.text)) return <>{props.text}</>;
  return (
    <MarkdownText
      type="text"
      text={props.text}
      status={{ type: "complete" }}
      components={INLINE_COMPONENTS}
    />
  );
}

interface EditSession {
  readonly target: EditTarget;
  /** What this target held before the session opened — what Escape restores. */
  readonly previous: PendingEdit | undefined;
}

export interface EditableDocumentProps {
  /** The artifact's source, verbatim. The only string Save ever splices into. */
  readonly source: string;
  /** The host's permission to write, and the call that writes. */
  readonly actions: ArtifactEditorActions;
  /**
   * `artifactId@revision`. A new revision is a new base document, so every
   * pending edit is dropped when it changes — including the revision this
   * component's own Save produced.
   */
  readonly documentKey: string;
  /** Scopes testids when two documents are mounted at once (the inline card). */
  readonly idPrefix?: string;
}

export function EditableDocument(props: EditableDocumentProps): ReactElement {
  const blocks = useMemo(() => parseBlocks(props.source), [props.source]);
  const [pending, setPending] = useState<PendingEdits>(NO_PENDING_EDITS);
  const [session, setSession] = useState<EditSession | null>(null);
  const [status, setStatus] = useState<
    "idle" | "saving" | "conflict" | "error"
  >("idle");
  const prefix = props.idPrefix ?? "doc";

  useEffect(() => {
    setPending(NO_PENDING_EDITS);
    setSession(null);
    setStatus("idle");
  }, [props.documentKey]);

  const saving = status === "saving";
  const editable = !props.actions.disabled && !saving;
  const dirtyCount = Object.keys(pending).length;

  const valueOf = (target: EditTarget): string =>
    pending[targetKey(target)]?.value ?? originalValue(blocks, target) ?? "";
  const isEditing = (target: EditTarget): boolean =>
    session !== null && targetKey(session.target) === targetKey(target);
  const isDirty = (target: EditTarget): boolean =>
    pending[targetKey(target)] !== undefined;

  const begin = (target: EditTarget): void => {
    // Re-entering an open session would re-snapshot `previous` as the value
    // just typed, and Escape would then "revert" to the edit it is meant to
    // undo. A click inside the open field bubbles here, so this is reachable.
    if (!editable || isEditing(target)) return;
    setSession({ target, previous: pending[targetKey(target)] });
  };
  // Per keystroke, and no further: this is local state, not a request. Keeping
  // the typed value in `pending` rather than in a separate draft is what makes
  // Save closure-safe — there is no half-typed value living somewhere Save
  // cannot see.
  const change = (target: EditTarget, value: string): void => {
    setPending((current) => commitEdit(current, blocks, target, value));
  };
  /**
   * Closes the session, but only if it is still this target's.
   *
   * Tab hands the session to the next cell and the outgoing input may then emit
   * a blur; an unguarded close would race that hand-off and shut the editor the
   * user just moved into.
   */
  const close = (target: EditTarget): void => {
    setSession((current) =>
      current !== null && targetKey(current.target) === targetKey(target)
        ? null
        : current,
    );
  };
  const cancel = (target: EditTarget, previous: PendingEdit | undefined) => {
    setPending((current) => revertEdit(current, target, previous));
    close(target);
  };
  const move = (target: EditTarget, step: 1 | -1): void => {
    const next = nextCellTarget(blocks, target, step);
    if (next === null) {
      close(target);
      return;
    }
    setSession({ target: next, previous: pending[targetKey(next)] });
  };
  const discard = (): void => {
    setPending(NO_PENDING_EDITS);
    setSession(null);
    setStatus("idle");
  };
  const save = (): void => {
    let next: string;
    try {
      next = applyEdits(props.source, documentEditsFor(blocks, pending));
    } catch {
      // A target that no longer resolves, or a batch that overlaps. Neither is
      // recoverable by guessing, and the edits stay on screen.
      setStatus("error");
      return;
    }
    setSession(null);
    setStatus("saving");
    void props.actions
      .saveRevision(next)
      .then((outcome) => setStatus(outcome === "saved" ? "idle" : outcome));
  };

  /**
   * What makes a rendered block clickable — and nothing when it is not
   * editable, so a read-only surface has no affordance to find, not merely one
   * that refuses.
   */
  const openProps = (
    target: EditTarget,
  ): {
    readonly tabIndex?: number;
    readonly onClick?: () => void;
    readonly onKeyDown?: (event: KeyboardEvent) => void;
  } =>
    !editable || isEditing(target)
      ? {}
      : {
          tabIndex: 0,
          onClick: () => begin(target),
          onKeyDown: (event) => {
            if (event.key !== "Enter") return;
            event.preventDefault();
            begin(target);
          },
        };

  const fieldProps = (
    target: EditTarget,
    label: string,
  ): Omit<FieldProps, "testId"> => ({
    "aria-label": label,
    value: valueOf(target),
    onChange: (value: string) => change(target, value),
    onCommit: () => close(target),
    onCancel: () => cancel(target, session?.previous),
    onMove: (step: 1 | -1) => move(target, step),
  });

  const renderSpan = (
    target: EditTarget,
    label: string,
    testId: string,
  ): ReactElement =>
    isEditing(target) ? (
      <CellField testId={`${testId}-input`} {...fieldProps(target, label)} />
    ) : (
      <span
        data-testid={testId}
        data-modified={isDirty(target) ? "true" : "false"}
        style={spanReadStyle}
      >
        <InlineMarkdown text={valueOf(target)} />
      </span>
    );

  const renderTable = (block: TableBlock, index: number): ReactElement => (
    <div style={tableWrapStyle} key={index}>
      <table style={tableStyle} data-testid={`${prefix}-table-${index}`}>
        <thead>
          <tr>
            {block.headerCells.map((_cell, column) => {
              const target: EditTarget = {
                kind: "header",
                block: index,
                column,
              };
              return (
                <th
                  key={column}
                  scope="col"
                  style={headerStyle(block.alignments[column] ?? null)}
                  {...openProps(target)}
                >
                  {renderSpan(
                    target,
                    `Column ${column + 1} name`,
                    `${prefix}-header-${index}-${column}`,
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {block.rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((_cell, column) => {
                const target: EditTarget = {
                  kind: "cell",
                  block: index,
                  row: rowIndex,
                  column,
                };
                const header = block.headers[column] || `Column ${column + 1}`;
                return (
                  <td
                    key={column}
                    style={cellStyle(block.alignments[column] ?? null)}
                    {...openProps(target)}
                  >
                    {renderSpan(
                      target,
                      `${header}, row ${rowIndex + 1}`,
                      `${prefix}-cell-${index}-${rowIndex}-${column}`,
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );

  const renderHeading = (block: HeadingBlock, index: number): ReactElement => {
    const target: EditTarget = { kind: "prose", block: index };
    const Tag = HEADING_TAGS[block.level - 1] ?? "h6";
    return (
      <Tag key={index} style={headingStyle} {...openProps(target)}>
        {renderSpan(
          target,
          `Heading, level ${block.level}`,
          `${prefix}-block-${index}`,
        )}
      </Tag>
    );
  };

  const renderParagraph = (
    block: ParagraphBlock,
    index: number,
  ): ReactElement => {
    const target: EditTarget = { kind: "prose", block: index };
    const testId = `${prefix}-block-${index}`;
    if (isEditing(target)) {
      return (
        <ProseField
          key={index}
          testId={`${testId}-input`}
          enter="commit-unless-shift"
          {...fieldProps(target, `Paragraph ${index + 1}`)}
        />
      );
    }
    return (
      <div
        key={index}
        data-testid={testId}
        data-modified={isDirty(target) ? "true" : "false"}
        style={proseReadStyle(editable)}
        {...openProps(target)}
      >
        <MarkdownText
          type="text"
          text={valueOf(target)}
          status={{ type: "complete" }}
          components={BLOCK_COMPONENTS}
        />
      </div>
    );
  };

  /**
   * A raw block — a list, a fence, a quote — edited IN PLACE, as its own span.
   *
   * Be precise about what this is, because a `textarea` was deleted from this
   * file and this one is not it. That one held THE WHOLE DOCUMENT and sat
   * UNDERNEATH the render, so ticking one checklist item meant finding it among
   * every other byte of markdown in the artifact. This holds ONE block, appears
   * where that block already is, shows no byte of any other block, and exists
   * only while that block is open.
   *
   * The block model refuses to model a list's items — so this offers the
   * block's own lines and nothing finer. That is the trade the `raw` catch-all
   * makes: the bytes of a construct nothing here understands stay exactly as
   * written, and the user still has a way in.
   */
  const renderRaw = (block: RawBlock, index: number): ReactNode => {
    // A blank run is the whitespace BETWEEN blocks: nothing to show, nothing to
    // edit, and an empty span sitting where the document paints no control.
    // `documentEditFor` refuses the same target from the other side, so this is
    // a missing affordance rather than a hidden one.
    if (block.reason === "blank") return null;
    const target: EditTarget = { kind: "prose", block: index };
    const testId = `${prefix}-raw-${index}`;
    const label = `${RAW_LABEL[block.reason]} ${index + 1}`;
    if (isEditing(target)) {
      return (
        <ProseField
          key={index}
          testId={`${testId}-input`}
          enter="newline"
          {...fieldProps(target, label)}
        />
      );
    }
    return (
      <div
        key={index}
        data-testid={testId}
        data-modified={isDirty(target) ? "true" : "false"}
        style={proseReadStyle(editable)}
        {...openProps(target)}
      >
        <MarkdownText
          type="text"
          text={valueOf(target)}
          status={{ type: "complete" }}
          components={BLOCK_COMPONENTS}
        />
      </div>
    );
  };

  const renderBlock = (block: DocumentBlock, index: number): ReactNode => {
    switch (block.kind) {
      case "table":
        return renderTable(block, index);
      case "heading":
        return renderHeading(block, index);
      case "paragraph":
        return renderParagraph(block, index);
      case "raw":
        return renderRaw(block, index);
    }
  };

  return (
    <section
      aria-label="Editable document"
      data-testid={`${prefix}-editor`}
      data-dirty={dirtyCount}
      style={rootStyle}
    >
      <div
        className="ui-toolbar"
        aria-label="Document revision actions"
        data-testid={`${prefix}-editor-actions`}
        style={actionBarStyle}
      >
        <span className="ui-caption" style={hintStyle}>
          Click any cell, paragraph or block to edit it in place. Changes stay
          local until you save a new revision.
        </span>
        <span className="ui-caption" data-testid={`${prefix}-editor-status`}>
          {dirtyCount === 0
            ? "No unsaved edits"
            : `${dirtyCount} unsaved ${dirtyCount === 1 ? "edit" : "edits"}`}
        </span>
        <button
          className="ui-button ui-button--ghost ui-button--sm"
          type="button"
          disabled={dirtyCount === 0 || saving}
          onClick={discard}
        >
          Discard
        </button>
        <button
          className="ui-button ui-button--primary ui-button--sm"
          type="button"
          disabled={!editable || dirtyCount === 0}
          onClick={save}
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
      {status === "conflict" ? (
        <p className="ui-caption" role="alert">
          A newer revision exists. Your local edits are preserved; review it and
          manually rebase before saving.
        </p>
      ) : null}
      {status === "error" ? (
        <p className="ui-caption" role="alert">
          Could not save this revision. Your local edits are still here.
        </p>
      ) : null}
      <div style={bodyStyle}>{blocks.map(renderBlock)}</div>
    </section>
  );
}

interface FieldProps {
  readonly "aria-label": string;
  readonly testId: string;
  readonly value: string;
  readonly onChange: (value: string) => void;
  readonly onCommit: () => void;
  readonly onCancel: () => void;
  readonly onMove: (step: 1 | -1) => void;
}

/** What Enter ALONE does in a field — the one thing the three shapes differ on. */
type EnterBehaviour = "commit" | "commit-unless-shift" | "newline";

/**
 * The keyboard contract, shared by every field shape.
 *
 * Escape reverts, Tab moves to the next cell (and closes when there is none),
 * and ⌘/Ctrl+Enter always commits. Tab is `preventDefault`ed because the move is
 * ours to make: letting the browser move focus would land on whatever DOM
 * happens to be next, not on the next cell.
 *
 * What Enter alone does depends on whether a newline is structure or damage in
 * the span being edited. In a cell or a heading it ends the construct, so Enter
 * commits; in a paragraph it is the rarer intent, so Enter commits and
 * Shift+Enter adds the line. In a RAW block the lines ARE the construct — "add
 * another checklist item" is the commonest edit that field exists to serve — so
 * Enter adds a line and ⌘Enter, Tab or a click away closes it.
 *
 * Nothing is at stake in that choice, which is why it can follow the content:
 * every keystroke has already landed in the pending batch, so closing a session
 * decides where the cursor goes, never whether the edit survives.
 */
function keyHandler(
  props: FieldProps,
  enter: EnterBehaviour,
): (event: KeyboardEvent) => void {
  return (event) => {
    if (event.key === "Enter") {
      const inserts =
        !(event.metaKey || event.ctrlKey) &&
        (enter === "newline" ||
          (enter === "commit-unless-shift" && event.shiftKey));
      if (inserts) return;
      event.preventDefault();
      props.onCommit();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      props.onCancel();
      return;
    }
    if (event.key === "Tab") {
      event.preventDefault();
      props.onMove(event.shiftKey ? -1 : 1);
    }
  };
}

/** One line, in the cell: a table cell's value, or a heading's text. */
function CellField(props: FieldProps): ReactElement {
  return (
    <input
      autoFocus
      aria-label={props["aria-label"]}
      data-testid={props.testId}
      style={fieldStyle}
      value={props.value}
      onChange={(event) => props.onChange(event.target.value)}
      onBlur={props.onCommit}
      onKeyDown={keyHandler(props, "commit")}
    />
  );
}

/**
 * One block's own text, where that block is.
 *
 * This is a `textarea`, so be precise about what was deleted: a whole-document
 * raw-markdown box mounted UNDERNEATH the render, in which a reader had to find
 * a cell among pipes. This holds ONE block, sits at that block's place in the
 * document, exists only while that block is being edited, and never shows a
 * byte of any other block. It serves a paragraph and a raw block alike — the
 * difference between them is `enter`, and nothing else.
 *
 * The narrow start is the design's (`EDITABLE-SURFACE-DESIGN.md`, open question
 * 2): plain text, spliced by span. A rich-text round trip back to markdown is
 * its own class of bug and is not what this phase buys.
 */
function ProseField(
  props: FieldProps & { readonly enter: EnterBehaviour },
): ReactElement {
  const lines = props.value.split("\n").length;
  return (
    <textarea
      autoFocus
      aria-label={props["aria-label"]}
      data-testid={props.testId}
      rows={Math.min(12, Math.max(2, lines))}
      style={proseFieldStyle}
      value={props.value}
      onChange={(event) => props.onChange(event.target.value)}
      onBlur={props.onCommit}
      onKeyDown={keyHandler(props, props.enter)}
    />
  );
}

// Presentation is inline rather than class-based on purpose. The design system
// owns no document-table classes, this package cannot add a stylesheet either
// host imports, and a package-owned class name re-declared in a host stylesheet
// is precisely how a shared surface comes to render differently on desktop.
// Tokens still come from the design system; only the layout is local.

const rootStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  minWidth: 0,
};

const actionBarStyle: CSSProperties = {
  alignItems: "center",
  display: "flex",
  flexWrap: "wrap",
  gap: 8,
};

const hintStyle: CSSProperties = { flex: "1 1 260px", minWidth: 0 };

const bodyStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 10,
  minWidth: 0,
};

const tableWrapStyle: CSSProperties = {
  maxWidth: "100%",
  minWidth: 0,
  overflowX: "auto",
};

const tableStyle: CSSProperties = {
  borderCollapse: "collapse",
  width: "100%",
};

function alignment(align: ColumnAlignment): CSSProperties["textAlign"] {
  return align ?? "left";
}

function headerStyle(align: ColumnAlignment): CSSProperties {
  return {
    borderBottom: "1px solid var(--color-border, #2a2d31)",
    color: "var(--color-text-subtle, #9aa0a6)",
    fontSize: "var(--font-size-xs, 12px)",
    fontWeight: 600,
    padding: "6px 8px",
    textAlign: alignment(align),
    verticalAlign: "top",
  };
}

function cellStyle(align: ColumnAlignment): CSSProperties {
  return {
    borderBottom: "1px solid var(--color-border, #2a2d31)",
    color: "var(--color-text, #f4f5f6)",
    fontSize: "var(--font-size-sm, 13px)",
    padding: "6px 8px",
    textAlign: alignment(align),
    verticalAlign: "top",
  };
}

const spanReadStyle: CSSProperties = {
  display: "block",
  minHeight: "1em",
  minWidth: 0,
};

function proseReadStyle(editable: boolean): CSSProperties {
  return { cursor: editable ? "text" : "default", minWidth: 0 };
}

const headingStyle: CSSProperties = {
  cursor: "text",
  margin: 0,
  minWidth: 0,
};

const fieldStyle: CSSProperties = {
  background: "var(--color-bg-elevated, transparent)",
  border: "1px solid var(--color-accent, #5fb2ec)",
  borderRadius: 4,
  color: "inherit",
  font: "inherit",
  padding: "2px 4px",
  width: "100%",
};

const proseFieldStyle: CSSProperties = {
  ...fieldStyle,
  display: "block",
  resize: "vertical",
};

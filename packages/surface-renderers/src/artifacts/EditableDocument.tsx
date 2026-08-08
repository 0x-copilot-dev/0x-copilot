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
// 1. **Splice, never regenerate.** Every change here is `applyEdits` over a
//    span of the document as it stands: every byte outside that span is copied
//    through untouched, so the prose around the table, the links inside cells,
//    and every construct the block model declines to understand survive a round
//    trip by construction. This component never re-emits markdown; it only
//    names spans, and for a structural change it does not even do that — it
//    asks the block package which span to write (`structuralEdits.ts`).
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
// STRUCTURE is the fourth property, and it is a different primitive from the
// other three. A row, a column and a block can be added, deleted and reordered
// here — but "delete row 2" is not a span replacement, it is a change to WHICH
// spans the document has, and its span CONTAINS the span of any cell edit still
// pending in that row. One batch holding both would hand `applyEdits` an
// overlapping pair and throw, in front of a user, over edits they can see on
// screen.
//
// So the two are STAGED rather than merged (`structuralEdits.ts`): a structural
// change is spliced immediately into a working document, and text edits stay
// pending against that working document. Both are unsaved until the same Save,
// both are dropped by the same Discard, and both count in "N unsaved edits" —
// what differs is only which string the pending map is addressed against.
//
// Deleting is therefore undoable exactly as long as everything else here is.
// Discard restores the artifact's own source, deleted rows and blocks included;
// after Save it is the host's revision history that holds the previous one. A
// control that can destroy a row of a user's data with no way back is a trap
// whatever it saves them in clicks, so this is the property to keep if any of
// the rest of the file changes.
//
// CHROME is the fifth property, and it is the one this file gets wrong most
// easily. Every operation above used to be drawn as its own always-visible
// button: five under each block, two more under each table, seventeen for a
// three-block document. The chrome outweighed the document it was chrome for.
//
// So the controls MOVED — none of them was removed:
//
//   * a left GUTTER holds the two per-block controls, revealed for the block
//     under the pointer or holding focus: `⋮⋮` drags to reorder, `+` opens the
//     one menu that holds everything else that block can do, Delete included;
//   * a ROW's and a COLUMN's controls sit AT that row and that column, revealed
//     the same way — a row's Delete belongs on the row, not in a strip under
//     the table.
//
// Three rules keep "revealed on hover" from being the accessibility regression
// it usually is:
//
// 1. **Focus reveals exactly as hover does.** Every control is a real
//    `<button>` in document order that is present, focusable and in the
//    accessibility tree at all times; `opacity` is the only thing hover
//    changes, so Tab reaches a control that then becomes visible. Nothing is
//    mounted on a pointer event, and nothing is `display: none`.
// 2. **A menu is a DISCLOSURE, not an ARIA menu.** The trigger carries
//    `aria-expanded`, the panel is a labelled group of ordinary buttons, and
//    Tab walks them. `role="menu"` would promise roving-tabindex arrow
//    navigation that this component does not implement, which is worse for a
//    screen-reader user than a plain group that behaves as it announces.
// 3. **Every control keeps the whole sentence as its accessible name** —
//    "Delete row 2 of Table 3" — and a glyph control repeats it in `title`, so
//    the tooltip explains the icon and the name still says what it acts on.
//
// Keyboard reorder survives the loss of the Move up / Move down buttons in two
// places: the arrow keys move the focused drag handle's block (and focus
// follows the block, not the position), and the same two operations remain as
// named items inside that block's `+` menu.

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentPropsWithoutRef,
  type CSSProperties,
  type DragEvent,
  type FocusEvent,
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
  addressableCells,
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
import {
  stageStructural,
  type StagedDocument,
  type StructuralOp,
} from "./structuralEdits";

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

/**
 * Everything unsaved, and the document it is unsaved against.
 *
 * `base` is the artifact's source with the STRUCTURAL changes already spliced
 * in, and `pending` holds the text edits addressed against `base` — the two
 * stages that let a deleted row and an edited cell be pending at once. Save
 * composes them (`applyEdits(base, pending)`); Discard drops the whole record
 * and goes back to `source`.
 *
 * It carries the identity it was taken against — `documentKey` AND the source
 * bytes — because a new revision is a new base document, and a draft addressed
 * at the old one cannot be adjusted onto the new one. It is dropped instead.
 */
interface Draft {
  readonly documentKey: string;
  readonly source: string;
  readonly base: string;
  /** Staged structural changes, counted so the unsaved-edit total includes them. */
  readonly structural: number;
  readonly pending: PendingEdits;
}

function freshDraft(documentKey: string, source: string): Draft {
  return {
    documentKey,
    source,
    base: source,
    structural: 0,
    pending: NO_PENDING_EDITS,
  };
}

/**
 * What a block is CALLED in a control's accessible name — "Move up: Table 3".
 *
 * The 1-based BLOCK index is the ordinal, matching the field labels above it
 * (`Paragraph 3` is the third block, not the third paragraph), so the two names
 * for one block never disagree.
 */
function describeBlock(block: DocumentBlock, index: number): string {
  switch (block.kind) {
    case "heading":
      return `Heading ${index + 1}`;
    case "paragraph":
      return `Paragraph ${index + 1}`;
    case "table":
      return `Table ${index + 1}`;
    case "raw":
      return block.reason === "blank"
        ? `Block ${index + 1}`
        : `${RAW_LABEL[block.reason]} ${index + 1}`;
  }
}

/**
 * A CONTENT-LESS block is not a neighbour to trade places with.
 *
 * The leading run of blank lines in a document is a block, and it is the one
 * `swapBlocksEdits` names as unable to hold the last slot: swapping content
 * into it leaves the other slot empty, and the document reparses with a
 * different number of blocks than the move assumed. It also renders nothing, so
 * a move "up" into it would look like a move that did nothing at all.
 */
function contentless(block: DocumentBlock): boolean {
  return block.kind === "raw" && block.reason === "blank";
}

export interface EditableDocumentProps {
  /**
   * The artifact's source, verbatim: the document every unsaved change is
   * measured from, and the one Discard goes back to. Save splices into it plus
   * whatever structure is already staged on top — see `Draft`.
   */
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
  const [stored, setStored] = useState<Draft>(() =>
    freshDraft(props.documentKey, props.source),
  );
  const [session, setSession] = useState<EditSession | null>(null);
  const [status, setStatus] = useState<
    "idle" | "saving" | "conflict" | "error" | "blocked"
  >("idle");
  // Which chrome is revealed. A block and a row/column are tracked separately
  // because hovering a row is also hovering its block: one slot would have the
  // row hide the gutter of the very block it lives in.
  const [activeBlock, setActiveBlock] = useState<number | null>(null);
  const [activeZone, setActiveZone] = useState<string | null>(null);
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const [dragging, setDragging] = useState<number | null>(null);
  const [dropTarget, setDropTarget] = useState<number | null>(null);
  // Focus follows the BLOCK a keyboard reorder moved, not the position it left
  // behind. Without this, a second ArrowUp moves whatever slid into the slot.
  const [focusHandle, setFocusHandle] = useState<number | null>(null);
  const handles = useRef(new Map<number, HTMLButtonElement>());
  const prefix = props.idPrefix ?? "doc";

  // Derived during render rather than reset by an effect: a draft addressed at
  // a document that is no longer on screen must never be the thing rendered,
  // not even for the one frame an effect would take to clear it.
  const draft =
    stored.documentKey === props.documentKey && stored.source === props.source
      ? stored
      : freshDraft(props.documentKey, props.source);
  const pending = draft.pending;
  // The blocks are the WORKING document's, not the artifact's: a staged row is
  // a real row with real spans, which is what makes it something to type into.
  const blocks = useMemo(() => parseBlocks(draft.base), [draft.base]);

  useEffect(() => {
    setSession(null);
    setStatus("idle");
    setOpenMenu(null);
    setDragging(null);
    setDropTarget(null);
  }, [props.documentKey, props.source]);

  useEffect(() => {
    if (focusHandle === null) return;
    handles.current.get(focusHandle)?.focus();
    setFocusHandle(null);
  }, [focusHandle]);

  const saving = status === "saving";
  const editable = !props.actions.disabled && !saving;
  const dirtyCount = draft.structural + Object.keys(pending).length;

  const updateDraft = (change: (current: Draft) => Draft): void => {
    setStored((current) =>
      change(
        current.documentKey === props.documentKey &&
          current.source === props.source
          ? current
          : freshDraft(props.documentKey, props.source),
      ),
    );
  };

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
    updateDraft((current) => ({
      ...current,
      pending: commitEdit(current.pending, blocks, target, value),
    }));
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
    updateDraft((current) => ({
      ...current,
      pending: revertEdit(current.pending, target, previous),
    }));
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
  /**
   * Splices structural changes into the working document, in order.
   *
   * Nothing is sent — this is the same batch a cell edit joins, and Save is
   * still the only thing that reaches the host. What it does do is move the
   * pending text edits onto the new document (`stageStructural`), because a
   * deleted row moves every row under it and an edit addressed at "row 3" would
   * otherwise land on what is now row 2. A change that cannot be carried across
   * is refused whole, with the edits left exactly where they are.
   *
   * It takes a LIST because dragging a block three places is three adjacent
   * swaps, and the three have to be one refusal: each is staged against the
   * document the previous one produced (its reparse included, since that is
   * what the next op's indices address), and `setStored` runs only if every one
   * of them landed. A partly applied drag would leave the document in a shape
   * nobody asked for and no single Discard step could explain.
   */
  const stageAll = (ops: readonly StructuralOp[]): boolean => {
    if (!editable || ops.length === 0) return false;
    setOpenMenu(null);
    let base = draft.base;
    let against: readonly DocumentBlock[] = blocks;
    let carried = pending;
    try {
      for (const op of ops) {
        const staged: StagedDocument = stageStructural(
          base,
          against,
          carried,
          op,
        );
        base = staged.source;
        carried = staged.pending;
        against = parseBlocks(base);
      }
    } catch {
      setStatus("blocked");
      return false;
    }
    // Written from `draft` rather than from whatever the setter is handed: the
    // staged document was computed against THAT record, and a base from one
    // draft with a pending map from another is a pair of offsets into two
    // different strings.
    setStored({
      ...draft,
      base,
      pending: carried,
      structural: draft.structural + ops.length,
    });
    // An open field's target may have moved with the change. Closing it loses
    // nothing: every keystroke is already in the batch, so a session decides
    // where the cursor is and never where an edit lives.
    setSession(null);
    setStatus("idle");
    return true;
  };
  const stage = (op: StructuralOp): void => {
    stageAll([op]);
  };

  /** A block that can hold the slot a swap puts content into. */
  const movable = (index: number): boolean =>
    index >= 0 && index < blocks.length && !contentless(blocks[index]);

  /**
   * Whether a block can travel from one index to another.
   *
   * A move of more than one place is a CHAIN of adjacent swaps, so every block
   * it passes through has to be able to hold content — a blank run in the way
   * is a wall, not a stop on the route. Asking here rather than discovering it
   * mid-chain is what lets a drag refuse the drop instead of accepting it and
   * then reporting that it could not be staged.
   */
  const canMove = (from: number, to: number): boolean => {
    if (from === to) return false;
    for (let at = Math.min(from, to); at <= Math.max(from, to); at += 1) {
      if (!movable(at)) return false;
    }
    return true;
  };

  const moveBlock = (from: number, to: number): void => {
    if (!canMove(from, to)) return;
    const step = to > from ? 1 : -1;
    const ops: StructuralOp[] = [];
    for (let at = from; at !== to; at += step) {
      ops.push({ kind: "swap-blocks", first: at, second: at + step });
    }
    if (stageAll(ops)) setFocusHandle(to);
  };
  const discard = (): void => {
    setStored(freshDraft(props.documentKey, props.source));
    setSession(null);
    setStatus("idle");
    setOpenMenu(null);
  };
  const save = (): void => {
    let next: string;
    try {
      next = applyEdits(draft.base, documentEditsFor(blocks, pending));
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

  /**
   * What reveals a row's or a column's controls.
   *
   * `onFocus` is not decoration next to `onMouseEnter` — it is the half that
   * makes the chrome reachable without a pointer. Both events bubble in React,
   * so entering or focusing any cell of a row reveals that row's controls, and
   * the leave/blur pair clears the zone only when it is still the one it set.
   */
  const zoneProps = (
    zone: string,
  ): {
    readonly onMouseEnter: () => void;
    readonly onMouseLeave: () => void;
    readonly onFocus: () => void;
    readonly onBlur: (event: FocusEvent<HTMLElement>) => void;
  } => {
    const clear = (): void =>
      setActiveZone((current) => (current === zone ? null : current));
    return {
      onMouseEnter: () => setActiveZone(zone),
      onMouseLeave: clear,
      onFocus: () => setActiveZone(zone),
      onBlur: (event) => {
        if (event.currentTarget.contains(event.relatedTarget)) return;
        clear();
      },
    };
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

  /**
   * A table whose structural controls sit AT the row and AT the column.
   *
   * What used to be here: a full row of Delete buttons above the header, an
   * Add row / Delete pair spelled out in words at the end of every row, and an
   * Add row strip in the footer — chrome that outweighed a three-row table.
   * What is here now is one glyph per column and two per row, invisible until
   * that column or that row is hovered or holds focus, and the appending
   * operations (Add row, Add column) moved into the block's own `+` menu where
   * they belong: they act on the TABLE, not on any one row or column.
   *
   * A row's controls stay in a cell of their OWN at the end of the row. That
   * cell is a `<td>`, never a `<th>` — a control is not a heading for anything
   * — and keeping it out of the content cells is what stops a click on Delete
   * from also opening the editor for the cell it sits beside.
   *
   * A column's control is the exception, and deliberately: it lives inside the
   * `<th>` because that IS the column, and there is nowhere else at the column
   * to put it that does not cost the table a row of chrome. It is a real button
   * inside a clickable header, so it stops its own click.
   *
   * The last column's Delete is disabled rather than hidden. A table with no
   * columns is not a table — there is no delimiter row that declares zero — so
   * emptying one is `Delete Table N`, which says what it does.
   *
   * A row is drawn through `addressableCells`, which is what keeps this table
   * and the document's own render agreeing about which cells there are: a row
   * carrying MORE cells than the header renders only the columns the header
   * declares, because that is all remark-gfm shows the reader. The rule is
   * stated once, in `documentEdits.ts`, and Tab reads the same one.
   */
  const renderTable = (block: TableBlock, index: number): ReactElement => {
    const name = describeBlock(block, index);
    const columns = block.headerCells.length;
    return (
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
                const zone = `${index}:column:${column}`;
                return (
                  <th
                    key={column}
                    scope="col"
                    style={headerStyle(block.alignments[column] ?? null)}
                    {...openProps(target)}
                    {...(editable ? zoneProps(zone) : {})}
                  >
                    <span style={cellRowStyle}>
                      <span style={cellSpanStyle}>
                        {renderSpan(
                          target,
                          `Column ${column + 1} name`,
                          `${prefix}-header-${index}-${column}`,
                        )}
                      </span>
                      {editable ? (
                        <span style={revealStyle(activeZone === zone)}>
                          <IconControl
                            testId={`${prefix}-delete-column-${index}-${column}`}
                            label={`Delete column ${
                              block.headers[column] || column + 1
                            } of ${name}`}
                            disabled={columns < 2}
                            onClick={() =>
                              stage({
                                kind: "delete-column",
                                block: index,
                                column,
                              })
                            }
                          >
                            ×
                          </IconControl>
                        </span>
                      ) : null}
                    </span>
                  </th>
                );
              })}
              {editable ? <td style={controlCellStyle} /> : null}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, rowIndex) => {
              const zone = `${index}:row:${rowIndex}`;
              return (
                <tr key={rowIndex} {...(editable ? zoneProps(zone) : {})}>
                  {addressableCells(block, row).map((_cell, column) => {
                    const target: EditTarget = {
                      kind: "cell",
                      block: index,
                      row: rowIndex,
                      column,
                    };
                    const header =
                      block.headers[column] || `Column ${column + 1}`;
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
                  {editable ? (
                    <td style={controlCellStyle}>
                      <span
                        style={{
                          ...rowControlsStyle,
                          ...revealStyle(activeZone === zone),
                        }}
                      >
                        <IconControl
                          testId={`${prefix}-insert-row-${index}-${rowIndex}`}
                          label={`Add row below row ${rowIndex + 1} of ${name}`}
                          onClick={() =>
                            stage({
                              kind: "insert-row",
                              block: index,
                              row: rowIndex,
                            })
                          }
                        >
                          +
                        </IconControl>
                        <IconControl
                          testId={`${prefix}-delete-row-${index}-${rowIndex}`}
                          label={`Delete row ${rowIndex + 1} of ${name}`}
                          onClick={() =>
                            stage({
                              kind: "delete-row",
                              block: index,
                              row: rowIndex,
                            })
                          }
                        >
                          ×
                        </IconControl>
                      </span>
                    </td>
                  ) : null}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  };

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

  const renderContent = (block: DocumentBlock, index: number): ReactNode => {
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

  /**
   * The two controls that add a block, at one BOUNDARY between blocks.
   *
   * Boundaries rather than blocks is what makes every position reachable: each
   * block's menu offers the boundary AFTER it, and the menu at the top of the
   * document offers boundary 0. That top menu is also the only affordance an
   * empty document has — deleting the last block leaves one, and it is the way
   * back.
   */
  const insertControls = (
    boundary: number,
    where: string,
  ): readonly ReactElement[] => [
    <ControlButton
      key="paragraph"
      testId={`${prefix}-insert-paragraph-${boundary}`}
      label={`Add paragraph ${where}`}
      onClick={() =>
        stage({ kind: "add-block", boundary, template: "paragraph" })
      }
    >
      Add paragraph
    </ControlButton>,
    <ControlButton
      key="table"
      testId={`${prefix}-insert-table-${boundary}`}
      label={`Add table ${where}`}
      onClick={() => stage({ kind: "add-block", boundary, template: "table" })}
    >
      Add table
    </ControlButton>,
  ];

  /**
   * The gutter for one block: drag to reorder, and the menu with the rest.
   *
   * Two controls where there were five, and the three that left are in the
   * menu rather than gone — Move up and Move down among them, because a drag
   * gesture is not an operation a keyboard can perform and this component owes
   * a keyboard every operation it owes a pointer.
   *
   * `opacity` is what hover changes, never `display` and never whether the
   * markup exists: an `opacity: 0` button is still focusable and still in the
   * accessibility tree, so Tab reaches it, `onFocus` reveals it, and a screen
   * reader announces it whether or not a pointer has ever been near this block.
   * `pointerEvents` is switched with the opacity so an invisible control cannot
   * swallow a click meant for the document underneath it.
   */
  const renderGutter = (block: DocumentBlock, index: number): ReactElement => {
    const name = describeBlock(block, index);
    const menu = `block:${index}`;
    const open = openMenu === menu;
    return (
      <div
        role="group"
        aria-label={`Actions for ${name}`}
        // NOT `-block-actions-`: `${prefix}-block-N` is a paragraph's own span,
        // and a testid under that prefix reads as one to anything scanning the
        // document for editable blocks.
        data-testid={`${prefix}-actions-${index}`}
        style={{
          ...gutterStyle,
          ...revealStyle(activeBlock === index || open),
        }}
      >
        <button
          aria-keyshortcuts="ArrowUp ArrowDown"
          aria-label={`Reorder ${name}`}
          className="ui-button ui-button--ghost ui-button--sm"
          data-testid={`${prefix}-reorder-${index}`}
          draggable
          ref={(node) => {
            if (node === null) handles.current.delete(index);
            else handles.current.set(index, node);
          }}
          style={iconButtonStyle}
          title={`Reorder ${name}. Drag it, or press the up and down arrow keys.`}
          type="button"
          onDragStart={(event: DragEvent<HTMLButtonElement>) => {
            // The index travels in React state, not in `dataTransfer`: the drop
            // handler needs a number this component can trust, and a payload a
            // page can write is not that. The transfer data is set anyway so a
            // browser draws a drag image at all.
            event.dataTransfer?.setData("text/plain", name);
            setDragging(index);
          }}
          onDragEnd={() => {
            setDragging(null);
            setDropTarget(null);
          }}
          onKeyDown={(event: KeyboardEvent) => {
            const step =
              event.key === "ArrowUp" ? -1 : event.key === "ArrowDown" ? 1 : 0;
            if (step === 0) return;
            event.preventDefault();
            moveBlock(index, index + step);
          }}
        >
          ⋮⋮
        </button>
        <ControlMenu
          testId={`${prefix}-block-menu-${index}`}
          label={`Insert or delete around ${name}`}
          panelLabel={`Change ${name}`}
          open={open}
          onToggle={() => setOpenMenu(open ? null : menu)}
          onClose={() =>
            setOpenMenu((current) => (current === menu ? null : current))
          }
        >
          {block.kind === "table" ? (
            <>
              <ControlButton
                testId={`${prefix}-add-row-${index}`}
                label={`Add row to ${name}`}
                onClick={() => stage({ kind: "add-row", block: index })}
              >
                Add row
              </ControlButton>
              <ControlButton
                testId={`${prefix}-add-column-${index}`}
                label={`Add column to ${name}`}
                onClick={() => stage({ kind: "add-column", block: index })}
              >
                Add column
              </ControlButton>
            </>
          ) : null}
          {insertControls(index + 1, `after ${name}`)}
          <ControlButton
            testId={`${prefix}-move-up-${index}`}
            label={`Move up: ${name}`}
            disabled={!canMove(index, index - 1)}
            onClick={() => moveBlock(index, index - 1)}
          >
            Move up
          </ControlButton>
          <ControlButton
            testId={`${prefix}-move-down-${index}`}
            label={`Move down: ${name}`}
            disabled={!canMove(index, index + 1)}
            onClick={() => moveBlock(index, index + 1)}
          >
            Move down
          </ControlButton>
          <ControlButton
            testId={`${prefix}-delete-block-${index}`}
            label={`Delete ${name}`}
            onClick={() => stage({ kind: "delete-block", block: index })}
          >
            Delete
          </ControlButton>
        </ControlMenu>
      </div>
    );
  };

  /**
   * One block and the gutter that acts on it, beside it.
   *
   * A block that renders nothing — the blank run between two blocks — gets no
   * gutter either. There is nothing on screen for the controls to be about, and
   * a handle floating in the whitespace of a document would be exactly the
   * affordance-with-no-target this file already refuses to draw.
   *
   * The wrapper is also the drop target, and it accepts a drop only for a move
   * `canMove` says is legal — `onDragOver` without `preventDefault` IS the
   * refusal, which is how a browser shows a "no" cursor rather than taking the
   * drop and reporting a failure afterwards.
   */
  const renderBlock = (block: DocumentBlock, index: number): ReactNode => {
    const content = renderContent(block, index);
    if (content === null) return null;
    const dragTo = dragging !== null && canMove(dragging, index);
    return (
      <div
        key={index}
        // NOT `-block-`: `${prefix}-block-N` is a paragraph's own span. This is
        // the slot the block occupies — what a drag drops ONTO.
        data-testid={`${prefix}-slot-${index}`}
        style={blockWrapStyle(dropTarget === index)}
        {...(editable
          ? {
              onMouseEnter: () => setActiveBlock(index),
              onMouseLeave: () =>
                setActiveBlock((current) =>
                  current === index ? null : current,
                ),
              onFocus: () => setActiveBlock(index),
              onBlur: (event: FocusEvent<HTMLDivElement>) => {
                if (event.currentTarget.contains(event.relatedTarget)) return;
                setActiveBlock((current) =>
                  current === index ? null : current,
                );
              },
              onDragOver: (event: DragEvent<HTMLDivElement>) => {
                if (!dragTo) return;
                event.preventDefault();
                setDropTarget(index);
              },
              onDragLeave: () =>
                setDropTarget((current) =>
                  current === index ? null : current,
                ),
              onDrop: (event: DragEvent<HTMLDivElement>) => {
                if (dragging === null) return;
                event.preventDefault();
                const from = dragging;
                setDragging(null);
                setDropTarget(null);
                moveBlock(from, index);
              },
            }
          : {})}
      >
        {editable ? renderGutter(block, index) : null}
        {content}
      </div>
    );
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
          Click any cell, paragraph or block to edit it in place. Hover or tab
          to a block for its drag handle and its insert menu. Changes stay local
          until you save a new revision.
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
      {status === "blocked" ? (
        <p className="ui-caption" role="alert">
          Could not add that change on top of your unsaved edits. Save or
          discard them first, then try it again.
        </p>
      ) : null}
      <div style={bodyStyle(editable)}>
        {editable ? (
          // The one control that is NOT revealed by hover, and the reason is
          // the empty document: deleting the last block leaves a page with
          // nothing to hover, so the way back has to be visible from the start.
          <div
            role="group"
            aria-label="Add a block at the top of the document"
            data-testid={`${prefix}-document-actions`}
            style={documentActionsStyle}
          >
            <ControlMenu
              testId={`${prefix}-document-menu`}
              label="Add a block at the top of the document"
              panelLabel="Add a block at the top of the document"
              open={openMenu === "document"}
              onToggle={() =>
                setOpenMenu(openMenu === "document" ? null : "document")
              }
              onClose={() =>
                setOpenMenu((current) =>
                  current === "document" ? null : current,
                )
              }
            >
              {insertControls(0, "at the top of the document")}
            </ControlMenu>
          </div>
        ) : null}
        {blocks.map(renderBlock)}
      </div>
    </section>
  );
}

interface ControlButtonProps {
  readonly testId: string;
  /** The whole sentence a screen reader needs: "Delete row 2 of Table 3". */
  readonly label: string;
  readonly disabled?: boolean;
  readonly onClick: () => void;
  readonly children: ReactNode;
}

/**
 * One item of a control menu: a real button, named for what it acts on.
 *
 * The visible text is the verb alone because the menu it sits in is already
 * labelled for the block it belongs to. The accessible name is the whole
 * sentence and CONTAINS that visible text, so the button a voice-control user
 * asks for by the words they can see is the button that gets pressed.
 *
 * The click stops here. A control inside a table cell or an open menu would
 * otherwise bubble into that cell's own click handler and open an editor over
 * the thing it just changed.
 */
function ControlButton(props: ControlButtonProps): ReactElement {
  return (
    <button
      aria-label={props.label}
      className="ui-button ui-button--ghost ui-button--sm"
      data-testid={props.testId}
      disabled={props.disabled ?? false}
      style={menuItemStyle}
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        props.onClick();
      }}
    >
      {props.children}
    </button>
  );
}

/**
 * The same control drawn as a glyph, for the places where a word does not fit:
 * a row's own end, a column's own header, the gutter.
 *
 * A glyph breaks the visible-text-inside-the-accessible-name rule that the
 * menu items keep, because `×` is not a word anybody can say. What replaces it
 * is `title`: the accessible name stays the whole sentence ("Delete row 2 of
 * Table 3") and the tooltip says the same sentence, so the icon is explained
 * on hover and announced on focus rather than left to be guessed at.
 */
function IconControl(props: ControlButtonProps): ReactElement {
  return (
    <button
      aria-label={props.label}
      className="ui-button ui-button--ghost ui-button--sm"
      data-testid={props.testId}
      disabled={props.disabled ?? false}
      style={iconButtonStyle}
      title={props.label}
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        props.onClick();
      }}
    >
      {props.children}
    </button>
  );
}

interface ControlMenuProps {
  readonly testId: string;
  /** The trigger's accessible name — what opening it is FOR. */
  readonly label: string;
  /** The panel's accessible name, so its buttons are announced in context. */
  readonly panelLabel: string;
  readonly open: boolean;
  readonly onToggle: () => void;
  readonly onClose: () => void;
  readonly children: ReactNode;
}

/**
 * A `+` that opens a panel of controls — a DISCLOSURE, not an ARIA menu.
 *
 * `role="menu"` is the obvious markup and the wrong one. It promises a screen
 * reader that arrow keys move between items under a roving tabindex, and this
 * component does not implement that; a widget that announces a keyboard
 * contract it does not honour is worse for the user than a labelled group of
 * ordinary buttons, which is what this is. `aria-expanded` + `aria-controls`
 * on the trigger say exactly what happens, Tab walks the items in order, and
 * Escape closes and hands focus back to the trigger it came from.
 *
 * Dismissal is `onBlur` with a containment check rather than a document-level
 * listener — this package cannot reach `document` (D28), and it does not need
 * to: clicking anywhere else moves focus out of this subtree, which is the
 * event that matters.
 */
function ControlMenu(props: ControlMenuProps): ReactElement {
  const trigger = useRef<HTMLButtonElement | null>(null);
  const panelId = `${props.testId}-panel`;
  const dismiss = (): void => {
    props.onClose();
    trigger.current?.focus();
  };
  return (
    <span
      style={menuAnchorStyle}
      onBlur={(event: FocusEvent<HTMLSpanElement>) => {
        if (event.currentTarget.contains(event.relatedTarget)) return;
        props.onClose();
      }}
      onKeyDown={(event: KeyboardEvent) => {
        if (event.key !== "Escape" || !props.open) return;
        event.preventDefault();
        event.stopPropagation();
        dismiss();
      }}
    >
      <button
        aria-controls={panelId}
        aria-expanded={props.open}
        aria-label={props.label}
        className="ui-button ui-button--ghost ui-button--sm"
        data-testid={props.testId}
        ref={trigger}
        style={iconButtonStyle}
        title={props.label}
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          props.onToggle();
        }}
      >
        +
      </button>
      {props.open ? (
        <span
          role="group"
          aria-label={props.panelLabel}
          data-testid={panelId}
          id={panelId}
          style={menuPanelStyle}
          // Focus never leaves the trigger for a POINTER interaction, and that
          // is what makes blur-to-dismiss safe. Chromium focuses a button on
          // mousedown; Safari and Firefox on macOS do not — there the item
          // would blur the trigger to nothing, `onClose` would unmount the item
          // mid-gesture, and the click would land on a control that no longer
          // exists. Keyboard reach is untouched: Tab is not a mousedown.
          onMouseDown={(event) => event.preventDefault()}
        >
          {props.children}
        </span>
      ) : null}
    </span>
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

/**
 * The gutter is drawn in the body's own left padding, so a control that
 * appears never reflows the document it appears beside.
 */
const GUTTER_WIDTH = 44;

function bodyStyle(editable: boolean): CSSProperties {
  return {
    display: "flex",
    flexDirection: "column",
    gap: 10,
    minWidth: 0,
    paddingLeft: editable ? GUTTER_WIDTH : 0,
  };
}

/**
 * A block and the gutter beside it. `position: relative` is what the gutter is
 * absolute against; the outline is the drop indicator, and it is an outline
 * rather than a border because an outline takes no space and so cannot make
 * the document twitch under a dragged block.
 */
function blockWrapStyle(dropping: boolean): CSSProperties {
  return {
    display: "flex",
    flexDirection: "column",
    minWidth: 0,
    outline: dropping
      ? "1px dashed var(--color-accent, #5fb2ec)"
      : "1px dashed transparent",
    outlineOffset: 3,
    position: "relative",
  };
}

/** The two per-block controls, in the padding to the left of the block. */
const gutterStyle: CSSProperties = {
  alignItems: "flex-start",
  display: "flex",
  gap: 2,
  left: -GUTTER_WIDTH,
  position: "absolute",
  top: 1,
};

/** The document-level `+`, in the same column the block gutters occupy. */
const documentActionsStyle: CSSProperties = {
  display: "flex",
  marginLeft: -GUTTER_WIDTH,
  minWidth: 0,
};

/**
 * What hover and focus change, and all they change.
 *
 * `opacity` keeps the control in the layout, in the tab order and in the
 * accessibility tree; `display: none` or an unmounted control would take it out
 * of all three and make "revealed on hover" mean "unreachable without a mouse".
 * `pointerEvents` rides along so an invisible button cannot eat a click.
 */
function revealStyle(revealed: boolean): CSSProperties {
  return {
    opacity: revealed ? 1 : 0,
    pointerEvents: revealed ? "auto" : "none",
    transition: "opacity 120ms ease",
  };
}

/** A cell that holds controls rather than content: no border, no cell padding. */
const controlCellStyle: CSSProperties = {
  padding: "2px 4px",
  textAlign: "left",
  verticalAlign: "middle",
  whiteSpace: "nowrap",
};

/** A header cell's content beside its own control, the control at the far end. */
const cellRowStyle: CSSProperties = {
  alignItems: "flex-start",
  display: "flex",
  gap: 4,
  justifyContent: "space-between",
  minWidth: 0,
};

const cellSpanStyle: CSSProperties = { flex: "1 1 auto", minWidth: 0 };

const rowControlsStyle: CSSProperties = { display: "inline-flex", gap: 2 };

const menuAnchorStyle: CSSProperties = {
  display: "inline-flex",
  position: "relative",
};

/**
 * The panel a `+` opens. It is positioned over the document rather than in
 * flow — the one thing in this file that is — because it exists only while it
 * is open, and reflowing the document around a transient panel would move the
 * block the user is about to act on.
 */
const menuPanelStyle: CSSProperties = {
  // `surface-elevated` and not `surface`: the panel opens OVER a card that is
  // already `surface`, and two identical fills separated by a hairline is a
  // panel a reader has to work out rather than see.
  background: "var(--color-surface-elevated, #1d1d23)",
  border: "1px solid var(--color-border-strong, rgba(255, 255, 255, 0.1))",
  borderRadius: "var(--radius-md, 8px)",
  boxShadow: "var(--shadow-md, 0 18px 50px -12px rgb(0 0 0 / 0.75))",
  display: "flex",
  flexDirection: "column",
  gap: 1,
  left: 0,
  minWidth: 168,
  padding: 4,
  position: "absolute",
  top: "calc(100% + 4px)",
  zIndex: 20,
};

const menuItemStyle: CSSProperties = {
  fontSize: "var(--font-size-xs, 12px)",
  justifyContent: "flex-start",
  lineHeight: 1.4,
  padding: "3px 8px",
  textAlign: "left",
  width: "100%",
};

/**
 * The 19px chip the mock draws (`mock-email-and-quiet-chrome.html`, `.gbtn`),
 * in the design system's own tokens — which carry the mock's values exactly:
 * `surface-elevated` IS its `--raised`, `border-strong` IS its `--border-2`,
 * `text-subtle` IS its `--subtle`. A ghost button paints no chrome at all, and
 * a bare glyph floating in the margin is hard to aim at even once it is drawn.
 */
const iconButtonStyle: CSSProperties = {
  alignItems: "center",
  background: "var(--color-surface-elevated, #1d1d23)",
  border: "1px solid var(--color-border-strong, rgba(255, 255, 255, 0.1))",
  borderRadius: 4,
  color: "var(--color-text-subtle, #64646d)",
  display: "inline-flex",
  fontSize: "var(--font-size-2xs, 11px)",
  height: 19,
  justifyContent: "center",
  lineHeight: 1,
  minHeight: 19,
  minWidth: 19,
  padding: 0,
  width: 19,
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

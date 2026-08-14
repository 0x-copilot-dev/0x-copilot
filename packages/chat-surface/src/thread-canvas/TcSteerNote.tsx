// TcSteerNote — the user's mid-run interjection, as a QUIET in-thread line. 🎨
//
// Like `TcCompactionDivider` this is deliberately not a card: there is nothing
// to decide, expand or act on. Unlike it, this row carries CONTENT — the user's
// own words — so it is not drawn as a bare rule either. It sits between the two
// registers on purpose, and the runtime asked for exactly that shape: it
// classifies `run_steered` as a `note` because prose "would render the user's
// words as something the agent said", while an `event` has "no place on the
// timeline, which is the one thing this event must have".
//
// So: a single hairline down the leading edge, the server's sentence above, the
// user's text below. The rule is what makes it read as an aside to the run
// rather than a turn in the conversation, and it is on the LEADING edge (not
// centred like the compaction rule) because this row is attributable — it came
// from one side of the conversation, and a centred rule would claim it was a
// property of the transcript itself.
//
// THE LABEL IS THE SERVER'S. `summary` is written at the emit site
// (`RunCoordinator.steer_run`), the same rule the rest of the cockpit follows
// for timeline labels. This component never re-words it and never derives one
// from the event name.
//
// THE TEXT IS THE USER'S AND IS NOT TRUNCATED. The server bounds a steer at
// `STEER_TEXT_MAX_LENGTH` (4000) before it is ever accepted, so there is no
// unbounded string to defend against here — and clipping the words someone sent
// into their own run, in the one place the record of having sent them exists,
// would defeat the row. It wraps.
//
// Kit-only styling; framework-agnostic (no window/document/fetch). Presentation
// lives in `review-surfaces.css` under package-owned `tc-steer*` class names,
// never inline: a host stylesheet re-declaring a package class is how a shared
// surface ends up right on web and wrong on desktop, and the names here exist
// nowhere else in the product.

import type { ReactElement } from "react";

export interface TcSteerNoteProps {
  /**
   * The server-written sentence ("You steered this run."). Required — the text
   * below it is a bare quotation without a line saying who said it and why it
   * is sitting mid-run.
   */
  readonly label: string;
  /**
   * The user's own words. Required and non-empty: `projectSteerNotes` drops a
   * note whose payload lost its text rather than render an empty aside.
   */
  readonly text: string;
  /** Overridden per-note by the transcript; the default is a standalone mount. */
  readonly testId?: string;
}

export function TcSteerNote({
  label,
  text,
  testId = "tc-steer",
}: TcSteerNoteProps): ReactElement {
  return (
    <div
      className="tc-steer"
      data-testid={testId}
      // A plain labelled group, for the reason the compaction sibling gives:
      // the accessible name is announced whole, so a reader who cannot see the
      // rule still gets "You steered this run." before the quotation.
      role="group"
      aria-label={label}
    >
      <span aria-hidden="true" className="tc-steer__rule" />
      <div className="tc-steer__body">
        <span className="tc-steer__label" data-testid={`${testId}-label`}>
          {label}
        </span>
        <span className="tc-steer__text" data-testid={`${testId}-text`}>
          {text}
        </span>
      </div>
    </div>
  );
}

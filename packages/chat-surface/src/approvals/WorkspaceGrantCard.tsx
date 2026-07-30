// The folder-grant card — the mid-run ask for real filesystem access.
//
// This is the surface that exists because of a specific defect: asked to read a
// folder it had no grant for, the agent got an empty listing back as SUCCESS and
// reported the folder empty. Nothing was refused, so nothing was shown, so the
// user was told a falsehood with a green tick next to it. A path outside granted
// scope has to become a QUESTION, and this is the question.
//
// It is deliberately the connector card's twin, not a new dialect: same `.cc`
// frame, same `data-state` attribute driving the same visual states, same
// "omit a clause you cannot back" rule (`grantAccessLabel` returns null rather
// than guess, and the Grant button withholds itself when the ask named no
// access). A user who has connected Linear already knows how to read this.
//
// Two things it does that the connector card does not:
//
//   1. It prints the PATH, in full, on its own wrapping line. Every other string
//      on a consent card is a name; this one is the subject of the decision, and
//      a truncated `/Users/ada/Doc…` is not something anyone can consent to. It
//      is also the only place a host-absolute path appears in this package — it
//      travels INTO the grant flow (see `ports/WorkspaceGrantPort`) and never
//      into a read.
//   2. It has a `failed` state that shows the host's message. A grant can fail
//      at the OS layer for reasons the app cannot fix (the user declined the
//      native dialog, the disk went away, the broker is down), and rendering
//      that as "nothing happened" is the same lie in a different costume.
//
// Presentational only (D28): every action is an injected callback. The host owns
// the `WorkspaceGrantPort` call, the OS dialog it triggers, and the decision POST
// that resumes the run.

import type { ReactElement, ReactNode } from "react";

import { Icon } from "../icons/Icon";
import { grantAccessLabel, type WorkspaceGrantRequest } from "./presentation";

/**
 * Where the ask has got to.
 *
 * `granting` covers the window where the host's native dialog is up — the
 * app cannot see that dialog, so this state exists to stop the card looking
 * inert while the user is being asked. `failed` is separate from `denied`
 * because "you said no" and "we couldn't" are different facts.
 */
export type WorkspaceGrantCardState =
  | "pending"
  | "granting"
  | "granted"
  | "denied"
  | "failed";

export interface WorkspaceGrantCardProps {
  /** The parsed ask — folder, access, and the model's reason. */
  readonly request: WorkspaceGrantRequest;
  readonly state: WorkspaceGrantCardState;
  /**
   * Why the grant failed, shown verbatim on `failed`. Absent on that state
   * degrades to a generic line rather than an empty card — but a host that has
   * a message must pass it: the message is the only thing that tells the user
   * whether to retry or to go change something.
   */
  readonly failureMessage?: string | null;
  /** Grant it — the host calls `WorkspaceGrantPort.requestGrant({ path, mode })`. */
  readonly onGrant?: () => void;
  /** Decline — the run continues without the folder. */
  readonly onDeny?: () => void;
  /** Abandon the ask while the host's native dialog is up. */
  readonly onCancel?: () => void;
  /** Reverse a decline, or retry after a failure. */
  readonly onReconsider?: () => void;
  /** False when no grant port is wired — the ask stays visible but inert. */
  readonly actionable?: boolean;
  readonly testId?: string;
  /** Host-supplied ids for the two decision controls. */
  readonly grantTestId?: string;
  readonly denyTestId?: string;
}

export function WorkspaceGrantCard({
  request,
  state,
  failureMessage = null,
  onGrant,
  onDeny,
  onCancel,
  onReconsider,
  actionable = true,
  testId,
  grantTestId = "wg-grant",
  denyTestId = "wg-deny",
}: WorkspaceGrantCardProps): ReactElement {
  const access = grantAccessLabel(request.mode);
  // An ask that named no access cannot be granted FROM HERE. The card still
  // renders — the user learns which folder wanted reading — but the button that
  // would hand over unknown access is withheld rather than labelled a guess.
  const grantable = actionable && access !== null;
  return (
    <div
      // Bare `.cc`, like the connector card: this variant needs no modifier
      // class because nothing about it is styled by variant — its two extra
      // states ride `data-state`, and its one extra element (`.cc__path`) is
      // grant-only by construction. A `cc--grant` hook that resolved to no rule
      // would just send the next reader looking for one.
      className="cc"
      data-state={state}
      data-testid={testId}
      role="group"
      aria-label={`Folder access: ${request.path}`}
    >
      <div className="cc__row">
        <span className="cc__mark" aria-hidden="true">
          <Icon name="folder" size={13} />
        </span>
        <span className="cc__title">{titleFor(state, request.folderName)}</span>
        {state === "granting" ? (
          <span className="cc__spinner" aria-hidden="true" />
        ) : null}
        {subtitleFor(state, request.reason)}
        <span className="cc__spacer" />
        <span className="cc__actions">
          {actionsFor(state, {
            grantable,
            onGrant,
            onDeny,
            onCancel,
            onReconsider,
            grantTestId,
            denyTestId,
          })}
        </span>
      </div>
      {/* The path, always — on every state. While pending it is what you are
          agreeing to; once granted it is what you would revoke. */}
      <p className="cc__path" data-testid="wg-path">
        {request.path}
      </p>
      {footFor(state, access)}
      {captionFor(state, access, failureMessage)}
    </div>
  );
}

function titleFor(state: WorkspaceGrantCardState, folderName: string): string {
  switch (state) {
    case "granting":
      return `Waiting for you to confirm ${folderName}…`;
    case "granted":
      return `${folderName} is available to this agent`;
    case "denied":
      return `${folderName} not shared`;
    case "failed":
      return `Couldn't get access to ${folderName}`;
    default:
      return `Let the agent read ${folderName}?`;
  }
}

function subtitleFor(
  state: WorkspaceGrantCardState,
  reason: string | null,
): ReactNode {
  if (state === "denied") {
    return <span className="cc__purpose">· the run continues without it</span>;
  }
  // The model's stated reason is narrative and only answers "why are you being
  // asked" — which stops mattering the moment the question is settled.
  if (state === "pending" && reason !== null) {
    return <span className="cc__purpose">{reason}</span>;
  }
  return null;
}

interface GrantActionHandlers {
  readonly grantable: boolean;
  readonly onGrant?: () => void;
  readonly onDeny?: () => void;
  readonly onCancel?: () => void;
  readonly onReconsider?: () => void;
  readonly grantTestId: string;
  readonly denyTestId: string;
}

function actionsFor(
  state: WorkspaceGrantCardState,
  handlers: GrantActionHandlers,
): ReactNode {
  if (state === "granted") {
    // Terminal. Revoking is a durable act on a durable grant, so it belongs
    // where the grant list lives (the composer's folder pills, Settings), not
    // on a transcript card that scrolls away.
    return null;
  }
  if (state === "granting") {
    return (
      <button
        type="button"
        className="apc-btn"
        data-testid="wg-cancel"
        onClick={handlers.onCancel}
      >
        Cancel
      </button>
    );
  }
  if (state === "denied" || state === "failed") {
    return (
      <button
        type="button"
        className="apc-btn"
        data-testid="wg-retry"
        disabled={!handlers.grantable}
        onClick={handlers.onReconsider}
      >
        {state === "failed" ? "Try again" : "Reconsider"}
      </button>
    );
  }
  return (
    <>
      <button
        type="button"
        className="apc-btn apc-btn--reject"
        data-testid={handlers.denyTestId}
        onClick={handlers.onDeny}
      >
        Deny
      </button>
      <button
        type="button"
        className="apc-btn apc-btn--primary"
        data-testid={handlers.grantTestId}
        disabled={!handlers.grantable}
        onClick={handlers.onGrant}
      >
        Grant access
      </button>
    </>
  );
}

/**
 * The trust line, on the pending ask only.
 *
 * "this folder only" is unconditional because it is a property of the mechanism
 * — a grant binds one root, and the broker refuses anything outside it — and so
 * is "revoke anytime". The access clause is conditional because it is a claim
 * about THIS ask, and an ask that named no access gets no clause.
 */
function footFor(
  state: WorkspaceGrantCardState,
  access: string | null,
): ReactNode {
  if (state !== "pending") {
    return null;
  }
  const clauses = [access, "this folder only", "revoke anytime"].filter(
    (clause): clause is string => clause !== null,
  );
  return (
    <div className="cc__foot">
      <span className="cc__trust">{clauses.join(" · ")}</span>
    </div>
  );
}

function captionFor(
  state: WorkspaceGrantCardState,
  access: string | null,
  failureMessage: string | null,
): ReactNode {
  if (state === "failed") {
    return (
      <p className="cc__caption" data-testid="wg-failure">
        {failureMessage ??
          "The folder was not shared, and the agent still cannot read it."}
      </p>
    );
  }
  if (state === "granting") {
    return (
      <p className="cc__caption">
        Your computer is asking you to confirm — approve there and the run
        continues.
      </p>
    );
  }
  if (state === "pending" && access === null) {
    // The withheld button, explained. Silence here would read as a broken card.
    return (
      <p className="cc__caption" data-testid="wg-unknown-access">
        This request didn&apos;t say what access it needs, so it can&apos;t be
        granted from here — add the folder in Settings instead.
      </p>
    );
  }
  return null;
}

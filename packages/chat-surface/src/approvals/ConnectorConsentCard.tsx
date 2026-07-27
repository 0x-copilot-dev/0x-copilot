// The connector consent card — the design's `connCard()` in copilot-workspace3.jsx.
//
// A connector ask has four states, and the previous implementation had one: it
// reused the generic approval frame, so connecting produced no waiting state, no
// confirmation, and no trace of a decline. The card simply vanished, which reads
// as "something happened" rather than "you connected Linear".
//
// Presentational only (D28): every action is an injected callback, and the trust
// clauses arrive already-derived from the server. This component never composes a
// claim of its own — if a clause is null it is omitted, because the alternative
// ("Read-only" over an unknown scope) is exactly the sentence a consent card must
// never show.

import type { ReactElement, ReactNode } from "react";
import { accessLabel, type ConnectorTrust } from "./presentation";

export type ConnectorConsentState =
  | "pending"
  | "connecting"
  | "connected"
  | "denied";

export interface ConnectorConsentCardProps {
  /** "Linear" — the connector's display name. */
  readonly displayName: string;
  /** Why the agent wants it, in the model's words ("to read LW-142's deps"). */
  readonly purpose: string | null;
  readonly state: ConnectorConsentState;
  readonly trust: ConnectorTrust;
  /** Tools the run gained; shown on `connected` only, when the host knows it. */
  readonly toolCount?: number | null;
  /** Stable key the vendor mark's hue is derived from (the `server_id`). */
  readonly brandKey?: string | null;
  readonly onConnect?: () => void;
  readonly onDeny?: () => void;
  readonly onCancel?: () => void;
  readonly onReconsider?: () => void;
  /** False when no auth port is wired — the gate stays visible but inert. */
  readonly actionable?: boolean;
  readonly testId?: string;
  /** Host-supplied ids for the two decision controls (see `ConsentCard`). */
  readonly connectTestId?: string;
  readonly denyTestId?: string;
}

export function ConnectorConsentCard({
  displayName,
  purpose,
  state,
  trust,
  toolCount = null,
  brandKey = null,
  onConnect,
  onDeny,
  onCancel,
  onReconsider,
  actionable = true,
  testId,
  connectTestId = "cc-connect",
  denyTestId = "cc-deny",
}: ConnectorConsentCardProps): ReactElement {
  return (
    <div
      className="cc"
      data-state={state}
      data-testid={testId}
      role="group"
      aria-label={`Connector: ${displayName}`}
      style={{
        ["--cc-mark-h" as string]: String(brandHue(brandKey ?? displayName)),
      }}
    >
      <div className="cc__row">
        <span className="cc__mark" aria-hidden="true">
          {monogram(displayName)}
        </span>
        <span className="cc__title">{titleFor(state, displayName)}</span>
        {state === "connecting" ? (
          <span className="cc__spinner" aria-hidden="true" />
        ) : null}
        {subtitleFor(state, purpose, toolCount)}
        <span className="cc__spacer" />
        <span className="cc__actions">
          {actionsFor(state, {
            actionable,
            onConnect,
            onDeny,
            onCancel,
            onReconsider,
            connectTestId,
            denyTestId,
          })}
        </span>
      </div>
      {footFor(state, trust)}
    </div>
  );
}

function titleFor(state: ConnectorConsentState, displayName: string): string {
  switch (state) {
    case "connecting":
      return `Waiting for ${displayName} sign-in…`;
    case "connected":
      return `${displayName} connected`;
    case "denied":
      return `${displayName} not connected`;
    default:
      return `Connect ${displayName}?`;
  }
}

function subtitleFor(
  state: ConnectorConsentState,
  purpose: string | null,
  toolCount: number | null,
): ReactNode {
  if (state === "connected") {
    // Only claim a tool count the host actually knows. "0 tools available"
    // would be worse than silence after a successful connect.
    if (toolCount === null || toolCount <= 0) {
      return null;
    }
    return (
      <span className="cc__purpose">
        · {toolCount} {toolCount === 1 ? "tool" : "tools"} available to this run
      </span>
    );
  }
  if (state === "denied") {
    return <span className="cc__purpose">· the run continues without it</span>;
  }
  if (state === "pending" && purpose !== null) {
    return <span className="cc__purpose">{purpose}</span>;
  }
  return null;
}

interface ActionHandlers {
  readonly actionable: boolean;
  readonly onConnect?: () => void;
  readonly onDeny?: () => void;
  readonly onCancel?: () => void;
  readonly onReconsider?: () => void;
  readonly connectTestId: string;
  readonly denyTestId: string;
}

function actionsFor(
  state: ConnectorConsentState,
  handlers: ActionHandlers,
): ReactNode {
  if (state === "connected") {
    // OAuth completion is terminal UI. The host reports the connection to the
    // runtime as a user turn; asking the user to click a second "Retry" action
    // duplicated that transition and hid failures behind an inert-looking CTA.
    return null;
  }
  if (state === "connecting") {
    return (
      <button
        type="button"
        className="apc-btn"
        data-testid="cc-cancel"
        onClick={handlers.onCancel}
      >
        Cancel
      </button>
    );
  }
  if (state === "denied") {
    return (
      <button
        type="button"
        className="apc-btn"
        data-testid="cc-reconsider"
        onClick={handlers.onReconsider}
      >
        Reconsider
      </button>
    );
  }
  return (
    <>
      {/* "Deny" not "Skip": the design offers to reverse this later, which only
          makes sense if it was a decision rather than a deferral. */}
      <button
        type="button"
        className="apc-btn apc-btn--reject"
        data-testid={handlers.denyTestId}
        disabled={!handlers.actionable}
        onClick={handlers.onDeny}
      >
        Deny
      </button>
      <button
        type="button"
        className="apc-btn apc-btn--primary"
        data-testid={handlers.connectTestId}
        disabled={!handlers.actionable}
        onClick={handlers.onConnect}
      >
        Connect
      </button>
    </>
  );
}

function footFor(
  state: ConnectorConsentState,
  trust: ConnectorTrust,
): ReactNode {
  if (state === "connecting") {
    return (
      <p className="cc__caption">
        {trust.authHost !== null
          ? `A browser tab opened at ${trust.authHost} — approve there and the run continues.`
          : "A browser tab opened — approve there and the run continues."}
      </p>
    );
  }
  if (state !== "pending") {
    return null;
  }
  const clauses = trustClauses(trust);
  if (clauses === null && trust.sourceTool === null) {
    return null;
  }
  return (
    <div className="cc__foot">
      {clauses !== null ? <span className="cc__trust">{clauses}</span> : null}
      {trust.sourceTool !== null ? (
        <span className="cc__provenance">{trust.sourceTool}</span>
      ) : null}
    </div>
  );
}

/**
 * Join the clauses the server could actually back, or null if there are none.
 *
 * "revoke anytime" is unconditional because it is a property of the product (a
 * connector can always be disconnected in Settings), not a claim about this
 * particular grant. Scope and host are conditional because they are.
 */
function trustClauses(trust: ConnectorTrust): string | null {
  const parts: string[] = [];
  const access = accessLabel(trust);
  if (access !== null) {
    parts.push(access);
  }
  if (trust.authHost !== null) {
    parts.push(`OAuth on ${trust.authHost}`);
  }
  if (parts.length === 0) {
    return null;
  }
  parts.push("revoke anytime");
  return parts.join(" · ");
}

/** First alphanumeric run of the name, uppercased — "Linear" → "L". */
function monogram(displayName: string): string {
  for (const char of displayName) {
    if (/[a-z0-9]/i.test(char)) {
      return char.toUpperCase();
    }
  }
  return "?";
}

/**
 * Deterministic hue per connector so two connectors are never the same colour
 * for the same user, and the same connector is never a different colour twice.
 *
 * Derived rather than fetched: a brand palette would have to come from the
 * catalog and be kept current for every connector, and the mark's job here is
 * distinguishability, not brand fidelity.
 */
function brandHue(key: string): number {
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) {
    hash = (hash * 31 + key.charCodeAt(i)) % 360;
  }
  return hash;
}

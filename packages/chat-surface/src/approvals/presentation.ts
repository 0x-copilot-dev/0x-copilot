// The consent card's SHAPE, the connector card's TRUST LINE, and the folder-grant
// card's ASK — all parsed off the run stream.
//
// Three separate concerns deliberately kept apart:
//
//   `ApprovalPresentation` is narrative — what is about to happen. The backend
//   projects it from the real tool-call arguments (`ApprovalPresentationProjector`),
//   so the preview text is the exact string the connector receives rather than a
//   description of it. Render every string here as PLAIN TEXT: it originates in
//   a model completion and may itself have come from tool output.
//
//   `ConnectorTrust` is a set of promises — the scope being granted and the host
//   the sign-in actually opens. The backend derives these from the connector row
//   and the issued auth URL, never from model text, and omits a clause it cannot
//   back. A missing field here means "we don't know", and the card must drop that
//   clause rather than substitute a plausible one.
//
//   `WorkspaceGrantRequest` is a REQUEST for a capability — one folder, and the
//   access being asked for. The path is the subject of the ask (see
//   `ports/WorkspaceGrantPort`), so it is the one string on that card the user
//   must be able to read in full and recognise.
//
// The first two parsers are total: anything unrecognised degrades to the neutral
// value so a malformed payload costs a decoration, never the card. The grant
// parser deliberately is NOT, and that asymmetry is the point — a decoration may
// degrade, a capability may not. No folder named → no card (null). No access mode
// named → the card renders but cannot be granted from there, because "Read-only"
// over an unknown mode is the same lie `accessLabel` refuses to tell.

import type { WorkspaceGrantMode } from "../ports/WorkspaceGrantPort";

/** Which of the design's three approval shapes to draw. */
export type ApprovalLayout = "params" | "rows" | "preview";

/** Per-line-item state inside a `rows` card. */
export type ApprovalRowStatus = "pending" | "approved" | "rejected" | "queued";

/** One decidable line item — a payee, a recipient, a transfer. */
export interface ApprovalRow {
  readonly label: string;
  readonly value: string;
  readonly note: string | null;
  readonly initials: string | null;
  /** Key a per-row decision is sent under; null → display-only. */
  readonly rowId: string | null;
  readonly status: ApprovalRowStatus;
  readonly decidable: boolean;
}

/** The literal content about to leave the workspace. */
export interface ApprovalPreview {
  readonly text: string;
  readonly meta: string | null;
}

export interface ApprovalPresentation {
  readonly layout: ApprovalLayout;
  /** "Approve & sign" / "Approve post"; null → the neutral "Approve". */
  readonly approveLabel: string | null;
  readonly rejectLabel: string | null;
  /** "Launch Week ops · Safe 3-of-5" — which run, against which account. */
  readonly provenance: string | null;
  readonly rows: readonly ApprovalRow[];
  readonly preview: ApprovalPreview | null;
}

/**
 * A mid-run ask for one host folder, parsed off an interrupt's
 * `payload.workspace_grant` block.
 *
 * Keyed on a DEDICATED block rather than a `path` field or an approval kind: a
 * kind would have to be added to the queue's union (which the hosts declare
 * their own copy of), and a bare `path` collides with the many tool payloads
 * that carry one in `arguments`. A backend raises this card by stamping this one
 * block on whatever interrupt it already emits — that is the whole contract.
 */
export interface WorkspaceGrantRequest {
  /** Host-absolute folder being asked for, verbatim — macOS or Windows. */
  readonly path: string;
  /** Last path segment ("Downloads") — the short name a sentence can use. */
  readonly folderName: string;
  /** Access asked for; null when the payload named none (see the header). */
  readonly mode: WorkspaceGrantMode | null;
  /** Why, in the model's words; null when it gave no reason. */
  readonly reason: string | null;
}

/** Server-derived clauses for the connector consent card's trust line. */
export interface ConnectorTrust {
  /** Scope being granted; null when the surface has no connector row to read. */
  readonly accessMode: "read" | "read_act" | null;
  /** Host the sign-in opens; null when no auth session was issued. */
  readonly authHost: string | null;
  /** Which surface raised the card — the card prints this as provenance. */
  readonly sourceTool: string | null;
}

/**
 * The payload block that turns any interrupt into a folder ask. Exported so the
 * one string the backend has to stamp is readable from the client contract, and
 * so a wiring test can assert against it rather than a literal.
 */
export const WORKSPACE_GRANT_PAYLOAD_KEY = "workspace_grant";

const LAYOUTS: readonly ApprovalLayout[] = ["params", "rows", "preview"];
const GRANT_MODES: readonly WorkspaceGrantMode[] = [
  "read_only",
  "read_write_no_delete",
  "read_write",
];
const ROW_STATUSES: readonly ApprovalRowStatus[] = [
  "pending",
  "approved",
  "rejected",
  "queued",
];

export const EMPTY_CONNECTOR_TRUST: ConnectorTrust = {
  accessMode: null,
  authHost: null,
  sourceTool: null,
};

/**
 * Parse `payload.presentation` into a card shape, or null to keep the params frame.
 *
 * A declared layout with nothing to draw falls back to `params` — the same
 * correction the server-side contract makes — so a mislabelled block renders the
 * key/value frame instead of an empty box.
 */
export function parseApprovalPresentation(
  value: unknown,
): ApprovalPresentation | null {
  if (!isRecord(value)) {
    return null;
  }
  const rows = parseRows(value.rows);
  const preview = parsePreview(value.preview);
  let layout = parseLayout(value.layout);
  if (layout === "rows" && rows.length === 0) {
    layout = "params";
  }
  if (layout === "preview" && preview === null) {
    layout = "params";
  }
  const approveLabel = text(value.approve_label);
  const rejectLabel = text(value.reject_label);
  const provenance = text(value.provenance);
  // Nothing worth carrying — let the caller keep its existing render path
  // rather than hand it an all-default object it has to re-check.
  if (
    layout === "params" &&
    approveLabel === null &&
    rejectLabel === null &&
    provenance === null
  ) {
    return null;
  }
  return { layout, approveLabel, rejectLabel, provenance, rows, preview };
}

/** Parse the connector card's trust clauses off an `mcp_auth_required` payload. */
export function parseConnectorTrust(payload: unknown): ConnectorTrust {
  if (!isRecord(payload)) {
    return EMPTY_CONNECTOR_TRUST;
  }
  const accessMode = text(payload.access_mode);
  return {
    accessMode:
      accessMode === "read" || accessMode === "read_act" ? accessMode : null,
    authHost: text(payload.auth_host),
    sourceTool: text(payload.source_tool),
  };
}

/**
 * The human sentence for a granted scope, or null when the scope is unknown.
 *
 * Null is the important case: the card drops the clause entirely. Defaulting an
 * unknown scope to "Read-only" would be the one lie this whole path exists to
 * prevent.
 */
export function accessLabel(trust: ConnectorTrust): string | null {
  if (trust.accessMode === "read") {
    return "Read-only";
  }
  if (trust.accessMode === "read_act") {
    return "Read & act";
  }
  return null;
}

/**
 * Parse the folder ask off an interrupt payload, or null when there is none.
 *
 * Null is the ONLY refusal: no `workspace_grant` block, or a block naming no
 * folder, means this interrupt is not a folder ask and the caller renders
 * whatever it rendered before. An unknown `mode` still yields a card — the user
 * gets told which folder is being asked for, and the card withholds the grant
 * button instead of guessing the access (see `grantAccessLabel`).
 */
export function parseWorkspaceGrantRequest(
  payload: unknown,
): WorkspaceGrantRequest | null {
  if (!isRecord(payload)) {
    return null;
  }
  const block = payload[WORKSPACE_GRANT_PAYLOAD_KEY];
  if (!isRecord(block)) {
    return null;
  }
  const path = text(block.path);
  if (path === null) {
    return null;
  }
  const mode = text(block.mode);
  return {
    path,
    folderName: folderNameOf(path),
    mode:
      mode !== null && (GRANT_MODES as readonly string[]).includes(mode)
        ? (mode as WorkspaceGrantMode)
        : null,
    reason: text(block.reason),
  };
}

/**
 * The human sentence for a requested access mode, or null when unknown.
 *
 * Same rule as {@link accessLabel}, for the same reason: a card that prints
 * "Read-only" over an access it cannot read is the lie this whole path exists to
 * prevent. `read_write_no_delete` keeps its "can't delete" clause rather than
 * flattening to "read & write" — the difference is the user's whole risk model.
 */
export function grantAccessLabel(
  mode: WorkspaceGrantMode | null,
): string | null {
  switch (mode) {
    case "read_only":
      return "Read-only";
    case "read_write_no_delete":
      return "Read & write, no deleting";
    case "read_write":
      return "Read & write";
    default:
      return null;
  }
}

/**
 * Last segment of a host-absolute path, on either OS.
 *
 * Splits on BOTH separators because the string arrives from whichever host the
 * user is on — `/Users/ada/Downloads` and `C:\Users\ada\Downloads` must both
 * yield `Downloads`. Falls back to the whole path (a drive root, `C:\`, has no
 * segment to take) so the caller always has something honest to print.
 */
function folderNameOf(path: string): string {
  const segments = path.split(/[/\\]+/).filter((part) => part.length > 0);
  return segments.length > 0 ? segments[segments.length - 1]! : path;
}

function parseLayout(value: unknown): ApprovalLayout {
  const raw = text(value);
  return raw !== null && (LAYOUTS as readonly string[]).includes(raw)
    ? (raw as ApprovalLayout)
    : "params";
}

function parseRows(value: unknown): readonly ApprovalRow[] {
  if (!Array.isArray(value)) {
    return [];
  }
  const rows: ApprovalRow[] = [];
  for (const entry of value) {
    if (!isRecord(entry)) {
      continue;
    }
    const label = text(entry.label);
    const rowValue = text(entry.value);
    if (label === null || rowValue === null) {
      continue;
    }
    const rowId = text(entry.row_id);
    rows.push({
      label,
      value: rowValue,
      note: text(entry.note),
      initials: text(entry.initials),
      rowId,
      status: parseRowStatus(entry.status),
      // Mirrors the server contract: no id, no per-row decision to send.
      decidable: entry.decidable === true && rowId !== null,
    });
  }
  return rows;
}

function parseRowStatus(value: unknown): ApprovalRowStatus {
  const raw = text(value);
  return raw !== null && (ROW_STATUSES as readonly string[]).includes(raw)
    ? (raw as ApprovalRowStatus)
    : "pending";
}

function parsePreview(value: unknown): ApprovalPreview | null {
  if (!isRecord(value)) {
    return null;
  }
  const body = text(value.text);
  if (body === null) {
    return null;
  }
  return { text: body, meta: text(value.meta) };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

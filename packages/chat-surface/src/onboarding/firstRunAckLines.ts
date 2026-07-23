// firstRunAckLines — derive the three acknowledgment echo lines (PRD-P3 §3.6)
// and the ack title state (PRD-P8 §7).
//
// Pure copy derivation, byte-verbatim vs SPEC §"Copy strings":
//   model   — {name}[ · downloading N%| · on-device]
//   tools   — {web search|none}[ · {connector}…]
//   privacy — local → "nothing leaves this machine"; key → "key in your OS keychain"
//
// Tolerant of `pct === undefined` (P2's download-annotated pct may not be
// wired): a local engine with no pct reads as "· on-device", never "· NaN%".
//
// PRD-P8 §7 adds the honesty axis: when the awaited local model demonstrably is
// NOT landing (runtime stopped / a terminal pull error), neither the model line
// nor the ack title may keep claiming the download is in flight.

import { FIRST_RUN_ACK_TITLES } from "./Acknowledgment";
import { FIRST_RUN_COPY } from "./firstRun";
import type { FirstRunLaunchPhase } from "./useFirstRunLaunch";

// Named `FirstRunAckEngine` (not `FirstRunEngine`) to avoid colliding with the
// surface's `FirstRunEngine` discriminated union in `firstRun.ts` — this is the
// flattened ack-copy view (kind + display name + optional pct).
export interface FirstRunAckEngine {
  readonly kind: "local" | "key";
  /** Display name — e.g. "Qwen 3 4B" or "Claude Sonnet 4.5". */
  readonly name: string;
  /** Local download progress 0–100 (P2). Undefined → treat as ready. */
  readonly pct?: number;
  /**
   * PRD-P8 §7 — the download demonstrably is not progressing (the hook's
   * `blocked !== null`, or `runtime === "stopped"`). Suppresses the
   * "· downloading N%" claim in favour of an honest paused suffix.
   */
  readonly blocked?: boolean;
}

export interface FirstRunToolsState {
  readonly webOn: boolean;
  readonly connectors: readonly string[];
}

export interface FirstRunAckLines {
  readonly modelLine: string;
  readonly toolsLine: string;
  readonly privacyLine: string;
}

/**
 * PRD-P8 §7 copy for the third ack state: the send was accepted, the model is
 * NOT arriving, and the user needs something true plus a way out.
 *
 * The strings themselves live in `FIRST_RUN_COPY.ack.stalled` with the rest of
 * the FTUE copy (PRD-P8 §5's "no inline literals" rule); this is the named
 * re-export the launch lane reads, so `starting`/`queued` (owned by
 * `FIRST_RUN_ACK_TITLES`) and `stalled` are reachable from one module without
 * either string gaining a second home.
 */
export const FIRST_RUN_ACK_STALLED = FIRST_RUN_COPY.ack.stalled;

/**
 * Which title the acknowledgment should carry. Mirrors `AcknowledgmentVariant`
 * plus P8's `stalled` — kept here (not in `Acknowledgment.tsx`) so the launch
 * lane has a single derivation both hosts share.
 */
export type FirstRunAckState = "starting" | "queued" | "stalled";

/**
 * Launch phase → ack state. `queued` still means "waiting on a model that IS
 * coming"; P8's `blocked` is the honest exit for one that is not. `error` keeps
 * today's mapping (the create error renders on the composer, not the title).
 */
export function firstRunAckStateForPhase(
  phase: FirstRunLaunchPhase,
): FirstRunAckState {
  if (phase === "queued") {
    return "queued";
  }
  if (phase === "blocked") {
    return "stalled";
  }
  return "starting";
}

/**
 * The title string for an ack state (the two shipped titles + P8's stalled).
 *
 * A single map lookup: `FIRST_RUN_ACK_TITLES.stalled` IS
 * `FIRST_RUN_COPY.ack.stalled.title` (a reference, not a copy), so the string
 * still has exactly one home and `Acknowledgment` — which renders the title from
 * the same map — cannot drift from this.
 */
export function firstRunAckTitle(state: FirstRunAckState): string {
  return FIRST_RUN_ACK_TITLES[state];
}

/**
 * The sub-line under the title, or `null` when the state needs none.
 *
 * Only `stalled` has one, and it is load-bearing rather than decorative: the
 * title says the model is not coming, and this says what to do about it. A
 * `starting`/`queued` ack deliberately stays silent — the three echo lines are
 * the whole body there.
 */
export function firstRunAckNote(state: FirstRunAckState): string | null {
  return state === "stalled" ? FIRST_RUN_ACK_STALLED.note : null;
}

/**
 * The label for the ack's action, or `null` when the state has none.
 *
 * `stalled` is the ONLY ack state with an action, and it must have one: it is
 * the terminus of a send that will not complete by itself, so without a control
 * the honest title would just be a nicer-worded dead end. The action is bound to
 * `FirstRunAckCtx.onBack`, which un-sends the surface and re-opens the composer
 * (`useFirstRunLaunch.launch` accepts a re-submit from `blocked` by design).
 *
 * Mirrors `FirstRunLocalCardProps`' omitted-means-no-button rule: a state with
 * no action returns `null`, so no caller can render a control that does nothing.
 */
export function firstRunAckAction(state: FirstRunAckState): string | null {
  return state === "stalled" ? FIRST_RUN_ACK_STALLED.action : null;
}

function modelSuffix(engine: FirstRunAckEngine): string {
  if (engine.kind !== "local") {
    return "";
  }
  // A landed model wins over a stale `blocked` flag — it is on-device now.
  if (engine.pct !== undefined && engine.pct >= 100) {
    return " · on-device";
  }
  if (engine.blocked === true) {
    return engine.pct !== undefined
      ? ` · download paused at ${engine.pct}%`
      : " · download paused";
  }
  if (engine.pct !== undefined) {
    return ` · downloading ${engine.pct}%`;
  }
  return " · on-device";
}

export function firstRunAckLines(
  engine: FirstRunAckEngine,
  tools: FirstRunToolsState,
): FirstRunAckLines {
  const modelLine = `model — ${engine.name}${modelSuffix(engine)}`;

  const toolsBase = tools.webOn ? "web search" : "none";
  const connectorSuffix =
    tools.connectors.length > 0 ? ` · ${tools.connectors[0]}…` : "";
  const toolsLine = `tools — ${toolsBase}${connectorSuffix}`;

  const privacyLine =
    engine.kind === "local"
      ? "nothing leaves this machine"
      : "key in your OS keychain";

  return { modelLine, toolsLine, privacyLine };
}

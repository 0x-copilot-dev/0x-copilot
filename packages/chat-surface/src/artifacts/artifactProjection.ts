import {
  ARTIFACT_EVENT_TYPES,
  type ArtifactKind,
  type RuntimeEventEnvelope,
} from "@0x-copilot/api-types";
import { artifactUri } from "./uri";

const [
  ARTIFACT_CREATED,
  ARTIFACT_REVISED,
  _ARTIFACT_PROMOTED,
  ARTIFACT_PRESENTATION_DECIDED,
] = ARTIFACT_EVENT_TYPES;

export interface ArtifactSurfaceTab {
  readonly artifactId: string;
  readonly kind: ArtifactKind;
  readonly revision: number;
  readonly uri: string;
  readonly title: string;
  readonly lastSeq: number;
}

const KINDS = new Set<ArtifactKind>(["code", "document", "dataset", "file"]);

/** A pure fold of B1 artifact events; only `canvas` decisions produce tabs. */
export function projectArtifactTabs(
  events: readonly RuntimeEventEnvelope[],
): readonly ArtifactSurfaceTab[] {
  const entries = new Map<string, ArtifactSurfaceTab>();
  const decisions = new Map<string, "canvas" | "hidden">();
  for (const event of events) {
    const payload = record(event.payload);
    if (payload === null) continue;
    const eventType = event.event_type as string;
    if (eventType === ARTIFACT_PRESENTATION_DECIDED) {
      const id = string(payload.artifact_id);
      if (id !== "")
        decisions.set(id, payload.decision === "canvas" ? "canvas" : "hidden");
      continue;
    }
    if (eventType !== ARTIFACT_CREATED && eventType !== ARTIFACT_REVISED)
      continue;
    const id = string(payload.artifact_id);
    const revision = number(payload.revision);
    if (id === "" || revision === null) continue;
    const prior = entries.get(id);
    const kind =
      (eventType === ARTIFACT_CREATED
        ? artifactKind(payload.kind)
        : undefined) ??
      prior?.kind ??
      // A revision whose `artifact.created` fell outside this event window
      // would otherwise be dropped and never become a tab. Now that a user can
      // revise an artifact from an earlier turn, that window miss is reachable,
      // so fall back to the payload's own kind before giving up.
      artifactKind(payload.kind);
    if (kind === undefined) continue;
    const sequence =
      typeof event.sequence_no === "number" ? event.sequence_no : 0;
    // Prefer the artifact's own name when the event carries one. Ledger
    // payloads do not today, so this normally falls through to the synthesized
    // label; the authoritative title reaches the Run cockpit's tabs from the
    // conversation-canvas record instead. Kept as a preference rather than
    // hardcoding the synthesis, so an event that gains a title is used.
    const name = string(payload.title) || `${kind} artifact`;
    entries.set(id, {
      artifactId: id,
      kind,
      revision,
      uri: artifactUri(kind, id, revision),
      title: `${name} · r${revision}`,
      lastSeq: sequence,
    });
  }
  return [...entries.values()]
    .filter((entry) => decisions.get(entry.artifactId) === "canvas")
    .sort((a, b) => b.lastSeq - a.lastSeq);
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
function string(value: unknown): string {
  return typeof value === "string" ? value : "";
}
function number(value: unknown): number | null {
  return Number.isSafeInteger(value) && Number(value) > 0
    ? Number(value)
    : null;
}
function artifactKind(value: unknown): ArtifactKind | undefined {
  return KINDS.has(value as ArtifactKind) ? (value as ArtifactKind) : undefined;
}

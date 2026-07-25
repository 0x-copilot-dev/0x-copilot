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
      eventType === ARTIFACT_CREATED ? artifactKind(payload.kind) : prior?.kind;
    if (kind === undefined) continue;
    const sequence =
      typeof event.sequence_no === "number" ? event.sequence_no : 0;
    entries.set(id, {
      artifactId: id,
      kind,
      revision,
      uri: artifactUri(kind, id, revision),
      title: `${kind} artifact · r${revision}`,
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

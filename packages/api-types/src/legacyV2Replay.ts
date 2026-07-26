/**
 * Versioned, read-only replay of historic Generative Surfaces v2 records.
 *
 * This is deliberately a client-safe reader, never a migration or an event
 * writer. Historic append-only events remain exactly as persisted. The result
 * preserves their factual subject ids and hands only persisted state/spec data
 * to pre-built renderers; it has no stage, gate, approval, queue, or connector
 * capability.
 */

import { GATE_V2_EVENT_TYPES } from "./ledger";

export type LegacyV2ReplayMode =
  | "empty"
  | "legacy_v2"
  | "mixed"
  | "canonical_v21";

export type LegacyV2QuarantineCode =
  | "malformed_surface_created"
  | "missing_surface_subject"
  | "malformed_surface_envelope"
  | "invalid_surface_uri"
  | "orphaned_surface_spec"
  | "unhydrated_payload_ref";

export interface LegacyV2ReplayQuarantine {
  readonly sequence_no: number | null;
  readonly event_type: string | null;
  readonly code: LegacyV2QuarantineCode;
  readonly subject_id: string | null;
}

export interface LegacyV2SurfaceProjection {
  /** Original persisted old-v2 subject. Never replaced by the renderer URI. */
  readonly subject_id: string;
  /** A stable URI used only to select an existing fixed renderer. */
  readonly uri: string;
  readonly kind: string | null;
  readonly title: string | null;
  readonly source_connector: string | null;
  readonly source_op: string | null;
  readonly payload_ref: string | null;
  readonly state: Readonly<Record<string, unknown>> | null;
  readonly last_sequence_no: number;
  readonly origin: "ledger" | "envelope";
  readonly read_only: true;
}

export interface LegacyV2ReplayProjection {
  readonly reader_version: 1;
  readonly mode: LegacyV2ReplayMode;
  readonly surfaces: readonly LegacyV2SurfaceProjection[];
  readonly quarantined: readonly LegacyV2ReplayQuarantine[];
}

/** Narrow structural input: both historic JSON and RuntimeEventEnvelope fit. */
export interface LegacyV2ReplayEvent {
  readonly event_id?: unknown;
  readonly event_type?: unknown;
  readonly sequence_no?: unknown;
  readonly payload?: unknown;
}

interface ReplayEvent {
  readonly eventType: string;
  readonly sequenceNo: number;
  readonly payload: Readonly<Record<string, unknown>>;
  readonly index: number;
  readonly eventId: string | null;
}

interface MutableSurface {
  subjectId: string;
  uri: string;
  kind: string | null;
  title: string | null;
  sourceConnector: string | null;
  sourceOp: string | null;
  payloadRef: string | null;
  state: Record<string, unknown> | null;
  lastSequenceNo: number;
  origin: "ledger" | "envelope";
}

interface SurfaceEnvelope {
  readonly uri: string | null;
  readonly archetype: string | null;
  readonly state: Readonly<Record<string, unknown>> | null;
  readonly hasState: boolean;
}

const SURFACE_MUTATION_TYPES = new Set<string>([
  "tool_result",
  "presentation_updated",
  "draft_updated",
]);
const LEGACY_PROOF_EVENT_TYPES = new Set<string>([
  "action.classified",
  "read.executed",
  "write.staged",
  "revision.added",
  "decision.recorded",
  "write.applied",
  "gate.opened",
  "gate.resolved",
  "receipt.emitted",
  "usage.recorded",
]);
const CANONICAL_V21_EVENT_TYPES = new Set<string>(GATE_V2_EVENT_TYPES);
const URI_PATTERN = /^[a-z][a-z0-9+.-]*:\/\/[^\s]+$/;

/**
 * Reconstruct only historic *presentation* facts. This function never mutates
 * input events, never performs I/O, and never exposes a writable v2.1 model.
 * Duplicate event ids are ignored after their first sequence-ordered replay,
 * matching SSE reconnect semantics.
 */
export function projectLegacyV2Replay(
  events: readonly LegacyV2ReplayEvent[],
): LegacyV2ReplayProjection {
  const ordered = orderedEvents(events);
  const quarantined: LegacyV2ReplayQuarantine[] = [];
  const seenEventIds = new Set<string>();
  const deduplicated: ReplayEvent[] = [];
  for (const event of ordered) {
    if (event.eventId !== null) {
      if (seenEventIds.has(event.eventId)) continue;
      seenEventIds.add(event.eventId);
    }
    deduplicated.push(event);
  }

  let legacySeen = false;
  let canonicalSeen = false;
  const ledgerSurfaces = new Map<string, MutableSurface>();
  const envelopeSurfaces = new Map<string, MutableSurface>();
  const legacyStreamProven = hasLegacyProof(deduplicated);

  for (const event of deduplicated) {
    if (isCanonicalV21(event.eventType)) canonicalSeen = true;
    if (LEGACY_PROOF_EVENT_TYPES.has(event.eventType)) legacySeen = true;

    if (event.eventType === "surface.created") {
      // `surface.created` deliberately spans the v2 → v2.1 boundary. Only
      // claim a proven historic row; an active `operation://` surface remains
      // owned by the canonical projector instead of becoming a read-only tab.
      if (isLegacySurfaceCreated(event, legacyStreamProven)) {
        legacySeen = true;
        applySurfaceCreated(event, ledgerSurfaces, quarantined);
      }
      continue;
    }

    if (SURFACE_MUTATION_TYPES.has(event.eventType)) {
      const envelope = readSurfaceEnvelope(event.payload);
      if (envelope !== null) {
        legacySeen = true;
        applySurfaceEnvelope(event, envelope, envelopeSurfaces, quarantined);
      } else if (Object.hasOwn(event.payload, "surface")) {
        legacySeen = true;
        quarantine(quarantined, event, "malformed_surface_envelope");
      }
      continue;
    }

    if (
      event.eventType === "surface_spec_generated" &&
      applySurfaceSpec(event, ledgerSurfaces, envelopeSurfaces, quarantined)
    ) {
      legacySeen = true;
    }
  }

  hydrateLedgerSurfaces(deduplicated, ledgerSurfaces, quarantined);
  const surfaces = [...ledgerSurfaces.values(), ...envelopeSurfaces.values()]
    .sort(
      (left, right) =>
        left.lastSequenceNo - right.lastSequenceNo ||
        compareText(left.uri, right.uri) ||
        compareText(left.subjectId, right.subjectId),
    )
    .map((surface) => freezeSurface(surface));

  return {
    reader_version: 1,
    mode: replayMode(legacySeen, canonicalSeen),
    surfaces,
    quarantined,
  };
}

function orderedEvents(events: readonly LegacyV2ReplayEvent[]): ReplayEvent[] {
  const normalized: ReplayEvent[] = [];
  events.forEach((raw, index) => {
    const eventType = text(raw.event_type);
    const sequenceNo = raw.sequence_no;
    if (
      eventType === null ||
      typeof sequenceNo !== "number" ||
      !Number.isSafeInteger(sequenceNo) ||
      sequenceNo < 0
    ) {
      return;
    }
    normalized.push({
      eventType,
      sequenceNo,
      payload: record(raw.payload) ?? {},
      index,
      eventId: text(raw.event_id),
    });
  });
  return normalized.sort(
    (left, right) =>
      left.sequenceNo - right.sequenceNo || left.index - right.index,
  );
}

function hasLegacyProof(events: readonly ReplayEvent[]): boolean {
  for (const event of events) {
    if (LEGACY_PROOF_EVENT_TYPES.has(event.eventType)) return true;
    if (event.eventType === "surface.created") {
      const subjectId = text(event.payload.surface_id);
      const payloadRef = text(event.payload.payload_ref);
      if (isConnectorSubject(subjectId) || isCallRef(payloadRef)) return true;
    }
    if (
      SURFACE_MUTATION_TYPES.has(event.eventType) &&
      (Object.hasOwn(event.payload, "surface") ||
        Object.hasOwn(event.payload, "surface_uri"))
    ) {
      return true;
    }
    if (
      event.eventType === "surface_spec_generated" &&
      (Object.hasOwn(event.payload, "surface_uri") ||
        Object.hasOwn(event.payload, "surface_id"))
    ) {
      return true;
    }
  }
  return false;
}

function isLegacySurfaceCreated(
  event: ReplayEvent,
  legacyStreamProven: boolean,
): boolean {
  const payloadRef = text(event.payload.payload_ref);
  // A terminal receipt is durable audit/export evidence, never a historic
  // presentation subject. `receipt.emitted` is intentionally a legacy-proof
  // event, so without this guard the compatibility reader retrofits every
  // modern receipt into a full Studio canvas tab.
  if (text(event.payload.kind) === "receipt") return false;
  if (payloadRef?.startsWith("operation://")) return false;
  const subjectId = text(event.payload.surface_id);
  return (
    legacyStreamProven || isConnectorSubject(subjectId) || isCallRef(payloadRef)
  );
}

function isConnectorSubject(value: string | null): boolean {
  return value?.startsWith("connector:") ?? false;
}

function isCallRef(value: string | null): boolean {
  return value?.startsWith("call:") ?? false;
}

function applySurfaceCreated(
  event: ReplayEvent,
  surfaces: Map<string, MutableSurface>,
  quarantined: LegacyV2ReplayQuarantine[],
): void {
  if (Object.keys(event.payload).length === 0) {
    quarantine(quarantined, event, "malformed_surface_created");
    return;
  }
  const subjectId = text(event.payload.surface_id);
  if (subjectId === null) {
    quarantine(quarantined, event, "missing_surface_subject");
    return;
  }
  const source = record(event.payload.source) ?? {};
  const prior = surfaces.get(subjectId);
  const kind = text(event.payload.kind);
  surfaces.set(subjectId, {
    subjectId,
    uri: ledgerUri(subjectId, kind),
    kind,
    title: text(event.payload.title),
    sourceConnector: text(source.connector),
    sourceOp: text(source.op),
    payloadRef: text(event.payload.payload_ref),
    state: prior?.state ?? null,
    lastSequenceNo: Math.max(
      prior?.lastSequenceNo ?? event.sequenceNo,
      event.sequenceNo,
    ),
    origin: "ledger",
  });
}

function readSurfaceEnvelope(
  payload: Readonly<Record<string, unknown>>,
): SurfaceEnvelope | null {
  const nested = record(payload.surface);
  if (nested !== null) {
    return {
      uri: text(nested.surface_uri),
      archetype: text(nested.archetype),
      state: record(nested.state),
      hasState: Object.hasOwn(nested, "state"),
    };
  }
  const uri = text(payload.surface_uri);
  if (uri === null) return null;
  return {
    uri,
    archetype: text(payload.archetype),
    state: record(payload.state),
    hasState: Object.hasOwn(payload, "state"),
  };
}

function applySurfaceEnvelope(
  event: ReplayEvent,
  envelope: SurfaceEnvelope,
  surfaces: Map<string, MutableSurface>,
  quarantined: LegacyV2ReplayQuarantine[],
): void {
  if (envelope.uri === null || !isUri(envelope.uri)) {
    quarantine(quarantined, event, "invalid_surface_uri");
    return;
  }
  const prior = surfaces.get(envelope.uri) ?? {
    subjectId: envelope.uri,
    uri: envelope.uri,
    kind: envelope.archetype,
    title: null,
    sourceConnector: null,
    sourceOp: null,
    payloadRef: null,
    state: null,
    lastSequenceNo: event.sequenceNo,
    origin: "envelope" as const,
  };
  if (envelope.hasState && envelope.state === null) {
    quarantine(quarantined, event, "malformed_surface_envelope", envelope.uri);
  }
  surfaces.set(envelope.uri, {
    ...prior,
    kind: envelope.archetype ?? prior.kind,
    state: mergeState(prior.state, envelope.state),
    lastSequenceNo: Math.max(prior.lastSequenceNo, event.sequenceNo),
    origin: "envelope",
  });
}

function applySurfaceSpec(
  event: ReplayEvent,
  ledgerSurfaces: Map<string, MutableSurface>,
  envelopeSurfaces: Map<string, MutableSurface>,
  quarantined: LegacyV2ReplayQuarantine[],
): boolean {
  const spec = record(event.payload.spec);
  const identity =
    text(event.payload.surface_uri) ?? text(event.payload.surface_id);
  if (identity === null && spec === null) return false;
  if (identity === null || spec === null) {
    quarantine(quarantined, event, "orphaned_surface_spec");
    return true;
  }
  const ledger = ledgerSurfaces.get(identity);
  if (ledger !== undefined) {
    ledger.state = mergeState(ledger.state, { spec });
    ledger.lastSequenceNo = Math.max(ledger.lastSequenceNo, event.sequenceNo);
    return true;
  }
  const envelope = envelopeSurfaces.get(identity);
  if (envelope !== undefined) {
    envelope.state = mergeState(envelope.state, { spec });
    envelope.lastSequenceNo = Math.max(
      envelope.lastSequenceNo,
      event.sequenceNo,
    );
    return true;
  }
  if (!isUri(identity)) {
    quarantine(quarantined, event, "orphaned_surface_spec", identity);
    return true;
  }
  envelopeSurfaces.set(identity, {
    subjectId: identity,
    uri: identity,
    kind: text(event.payload.archetype),
    title: null,
    sourceConnector: null,
    sourceOp: null,
    payloadRef: null,
    state: mergeState(null, { spec }),
    lastSequenceNo: event.sequenceNo,
    origin: "envelope",
  });
  return true;
}

function hydrateLedgerSurfaces(
  events: readonly ReplayEvent[],
  surfaces: ReadonlyMap<string, MutableSurface>,
  quarantined: LegacyV2ReplayQuarantine[],
): void {
  const refs = new Map<string, string>();
  for (const [subjectId, surface] of surfaces) {
    if (surface.payloadRef !== null) refs.set(subjectId, surface.payloadRef);
  }
  if (refs.size === 0) return;

  const outputByCall = new Map<string, unknown>();
  const specBySubject = new Map<string, Readonly<Record<string, unknown>>>();
  for (const event of events) {
    if (event.eventType === "tool_result") {
      const callId = text(event.payload.call_id);
      if (callId !== null && Object.hasOwn(event.payload, "output")) {
        outputByCall.set(callId, cloneValue(event.payload.output));
      }
      continue;
    }
    if (event.eventType !== "surface_spec_generated") continue;
    const subjectId =
      text(event.payload.surface_uri) ?? text(event.payload.surface_id);
    const spec = record(event.payload.spec);
    if (subjectId !== null && refs.has(subjectId) && spec !== null) {
      specBySubject.set(subjectId, cloneRecord(spec));
    }
  }

  for (const [subjectId, surface] of surfaces) {
    const hydrated: Record<string, unknown> = {};
    const callId = callIdFromRef(surface.payloadRef);
    if (callId !== null && outputByCall.has(callId)) {
      hydrated.data = cloneValue(outputByCall.get(callId));
    }
    const spec = specBySubject.get(subjectId);
    if (spec !== undefined) hydrated.spec = cloneRecord(spec);
    if (Object.keys(hydrated).length > 0) {
      surface.state = mergeState(surface.state, hydrated);
    } else if (surface.state === null && surface.payloadRef !== null) {
      quarantine(
        quarantined,
        {
          eventType: "surface.created",
          sequenceNo: surface.lastSequenceNo,
          payload: {},
          index: -1,
          eventId: null,
        },
        "unhydrated_payload_ref",
        subjectId,
      );
    }
  }
}

function freezeSurface(surface: MutableSurface): LegacyV2SurfaceProjection {
  return {
    subject_id: surface.subjectId,
    uri: surface.uri,
    kind: surface.kind,
    title: surface.title,
    source_connector: surface.sourceConnector,
    source_op: surface.sourceOp,
    payload_ref: surface.payloadRef,
    state: surface.state === null ? null : cloneRecord(surface.state),
    last_sequence_no: surface.lastSequenceNo,
    origin: surface.origin,
    read_only: true,
  };
}

function ledgerUri(subjectId: string, kind: string | null): string {
  const preferred = kind === "call" ? "record" : kind;
  const scheme =
    preferred === "record" ||
    preferred === "message" ||
    preferred === "table" ||
    preferred === "raw"
      ? preferred
      : "raw";
  return `${scheme}://legacy-v2/${encodeURIComponent(subjectId)}`;
}

function replayMode(
  legacySeen: boolean,
  canonicalSeen: boolean,
): LegacyV2ReplayMode {
  if (legacySeen && canonicalSeen) return "mixed";
  if (legacySeen) return "legacy_v2";
  if (canonicalSeen) return "canonical_v21";
  return "empty";
}

function isCanonicalV21(eventType: string): boolean {
  return (
    eventType.startsWith("operation.") ||
    eventType.startsWith("artifact.") ||
    eventType.startsWith("effect.") ||
    CANONICAL_V21_EVENT_TYPES.has(eventType)
  );
}

function isUri(value: string): boolean {
  return URI_PATTERN.test(value);
}

function callIdFromRef(value: string | null): string | null {
  if (value === null) return null;
  const separator = value.indexOf(":");
  if (separator < 0 || value.slice(0, separator) !== "call") return null;
  return text(value.slice(separator + 1));
}

function mergeState(
  current: Readonly<Record<string, unknown>> | null,
  incoming: Readonly<Record<string, unknown>> | null,
): Record<string, unknown> | null {
  if (current === null && incoming === null) return null;
  return {
    ...(current === null ? {} : cloneRecord(current)),
    ...(incoming === null ? {} : cloneRecord(incoming)),
  };
}

function quarantine(
  quarantined: LegacyV2ReplayQuarantine[],
  event: ReplayEvent,
  code: LegacyV2QuarantineCode,
  subjectId: string | null = null,
): void {
  const candidate: LegacyV2ReplayQuarantine = {
    sequence_no: event.sequenceNo,
    event_type: event.eventType,
    code,
    subject_id: subjectId,
  };
  if (
    !quarantined.some(
      (item) =>
        item.sequence_no === candidate.sequence_no &&
        item.event_type === candidate.event_type &&
        item.code === candidate.code &&
        item.subject_id === candidate.subject_id,
    )
  ) {
    quarantined.push(candidate);
  }
}

function record(value: unknown): Readonly<Record<string, unknown>> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
}

function cloneRecord(
  value: Readonly<Record<string, unknown>>,
): Record<string, unknown> {
  const cloned: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value))
    cloned[key] = cloneValue(item);
  return cloned;
}

function cloneValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map((item) => cloneValue(item));
  const object = record(value);
  return object === null ? value : cloneRecord(object);
}

function compareText(left: string, right: string): number {
  if (left === right) return 0;
  return left < right ? -1 : 1;
}

// Safe Sources v2 provenance projection.
//
// This is intentionally a pure selector over ledger-shaped events. It neither
// fetches nor opens a source, and opaque refs in its output are identifiers—not
// authorization. The existing `projectLedgerSources` read rail remains intact.

import {
  ARTIFACT_EVENT_TYPES,
  EFFECT_EVENT_TYPES,
  formatLedgerId,
  LEDGER_EVENT_TYPES,
  OPERATION_EVENT_TYPES,
  WorkspaceTargetRefCodec,
  type SourceFactKindV2,
  type SourceFactV2,
  type SourcesProjectionV2,
} from "@0x-copilot/api-types";

export interface SourcesProjectionEventLike {
  readonly event_type: unknown;
  readonly sequence_no: unknown;
  readonly payload: unknown;
}

type SourceFields = Pick<
  SourceFactV2,
  | "connector"
  | "tool"
  | "origin"
  | "artifact_id"
  | "artifact_revision"
  | "artifact_source_ref"
  | "workspace_grant_label"
  | "workspace_virtual_path_key"
  | "browser_origin"
  | "sandbox_operation"
  | "subagent_task"
  | "external_receipt_ref"
>;

const SOURCE_ID_PREFIX = "source:v2:";
const PHYSICAL_PATH =
  /(?:^|[\s(=:'"])(?:~[\\/]|[A-Za-z]:[\\/]|\\\\[^\\/\s]+[\\/]|\/(?:[^\s/]+(?:\/|$)))/i;
const SENSITIVE_TEXT =
  /(?:authorization|proxy-authorization|cookie|set-cookie|api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|token|secret|password|credential|private[_-]?key|client[_-]?secret|session)\s*[:=]|\bbearer\s+|(?:^|[^A-Za-z0-9_-])(?:sk-[A-Za-z0-9_-]+|xox[baprs]-[A-Za-z0-9-]+|gh[pousr]_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|AIza[A-Za-z0-9_-]{20,})(?![A-Za-z0-9_-])/i;
const OPAQUE_TOKEN = /^[A-Za-z0-9._~-]+$/;
const OPAQUE_REF =
  /^[A-Za-z][A-Za-z0-9+.-]*:\/\/[A-Za-z0-9._~-]+(?:\/[A-Za-z0-9._~-]+)*$/;
const SANDBOX_OPERATION = /^[A-Za-z][A-Za-z0-9._:-]{0,127}$/;

// The Work Ledger tuple order is append-only. Keep all canonical event values
// sourced from the API contract rather than redeclaring wire literals here.
const CANONICAL_EVENT = {
  actionClassified: LEDGER_EVENT_TYPES[2],
  readExecuted: LEDGER_EVENT_TYPES[3],
  surfaceCreated: LEDGER_EVENT_TYPES[4],
  writeApplied: LEDGER_EVENT_TYPES[12],
  operationRequested: OPERATION_EVENT_TYPES[0],
  artifactCreated: ARTIFACT_EVENT_TYPES[0],
  artifactRevised: ARTIFACT_EVENT_TYPES[1],
  artifactPromoted: ARTIFACT_EVENT_TYPES[2],
  effectStaged: EFFECT_EVENT_TYPES[0],
  effectApplied: EFFECT_EVENT_TYPES[5],
  effectReconciled: EFFECT_EVENT_TYPES[7],
} as const;

/**
 * Fold canonical and compatible ledger rows into a safe, deterministic Sources
 * v2 read model. Unknown/malformed rows are ignored; input is ordered by
 * sequence number with source order as the stable tie-breaker.
 */
export function projectSourcesV2(
  runId: string,
  events: readonly SourcesProjectionEventLike[],
): SourcesProjectionV2 {
  const ordered: Array<{
    sequenceNo: number;
    index: number;
    eventType: string;
    payload: Record<string, unknown>;
  }> = [];
  let latestSequenceNo = 0;

  events.forEach((event, index) => {
    const sequenceNo = positiveInt(event.sequence_no);
    if (sequenceNo !== null)
      latestSequenceNo = Math.max(latestSequenceNo, sequenceNo);
    const payload = asRecord(event.payload);
    if (
      sequenceNo === null ||
      typeof event.event_type !== "string" ||
      payload === null
    ) {
      return;
    }
    ordered.push({
      sequenceNo,
      index,
      eventType: event.event_type,
      payload,
    });
  });

  const facts: SourceFactV2[] = [];
  const emittedIds = new Set<string>();
  ordered
    .sort(
      (left, right) =>
        left.sequenceNo - right.sequenceNo || left.index - right.index,
    )
    .forEach(({ sequenceNo, eventType, payload }) => {
      factsForEvent(runId, sequenceNo, eventType, payload).forEach((fact) => {
        if (emittedIds.has(fact.source_id)) return;
        emittedIds.add(fact.source_id);
        facts.push(fact);
      });
    });

  return {
    v: 2,
    run_id: runId,
    latest_sequence_no: latestSequenceNo,
    facts,
  };
}

function factsForEvent(
  runId: string,
  sequenceNo: number,
  eventType: string,
  payload: Record<string, unknown>,
): SourceFactV2[] {
  const facts: SourceFactV2[] = [];
  const [connector, tool] = connectorAndTool(eventType, payload);
  const origin = isConnectorEvent(eventType)
    ? originFrom(payload, ["origin", "source_origin"])
    : null;
  const connectorFact = fact(runId, sequenceNo, "connector", {
    connector,
    tool,
    origin,
  });
  if (connectorFact !== null) facts.push(connectorFact);

  if (
    eventType === CANONICAL_EVENT.artifactCreated ||
    eventType === CANONICAL_EVENT.artifactRevised ||
    eventType === CANONICAL_EVENT.artifactPromoted
  ) {
    const artifactFact = fact(runId, sequenceNo, "artifact", {
      artifact_id: safeText(payload.artifact_id),
      artifact_revision: positiveInt(payload.revision),
      artifact_source_ref: firstSafeOpaqueRef(payload, [
        "source_ref",
        "content_ref",
      ]),
    });
    if (artifactFact !== null) facts.push(artifactFact);
  }

  const executor = safeText(payload.executor);
  const loweredExecutor = executor?.toLowerCase() ?? "";
  if (loweredExecutor === "workspace" || eventType.startsWith("workspace.")) {
    const workspace = workspaceFields(payload);
    if (workspace !== null) {
      const workspaceFact = fact(runId, sequenceNo, "workspace", {
        workspace_grant_label: workspace.grantLabel,
        workspace_virtual_path_key: workspace.virtualPathKey,
      });
      if (workspaceFact !== null) facts.push(workspaceFact);
    }
  }

  if (loweredExecutor === "browser" || eventType.startsWith("browser.")) {
    const browserOrigin = originFrom(payload, ["browser_origin", "origin"]);
    const browserFact = fact(runId, sequenceNo, "browser", {
      browser_origin: browserOrigin,
    });
    if (browserFact !== null) facts.push(browserFact);
  }

  const capability = safeText(payload.capability);
  if (
    loweredExecutor === "sandbox" ||
    capability === "sandbox" ||
    eventType.startsWith("sandbox.")
  ) {
    const sandboxFact = fact(runId, sequenceNo, "sandbox", {
      sandbox_operation: firstSafeSandboxOperation(payload, [
        "sandbox_operation",
        "operation",
        "op",
      ]),
    });
    if (sandboxFact !== null) facts.push(sandboxFact);
  }

  const producer = safeText(payload.producer);
  if (
    producer === "subagent" ||
    eventType.startsWith("subagent.") ||
    eventType.startsWith("subagent_")
  ) {
    // A tool name is not a task. Do not synthesize one if no explicit task text
    // is present on the compatible event.
    const subagentFact = fact(runId, sequenceNo, "subagent", {
      subagent_task: firstSafeText(payload, [
        "subagent_task",
        "task_summary",
        "objective_summary",
        "task",
      ]),
    });
    if (subagentFact !== null) facts.push(subagentFact);
  }

  if (
    eventType === CANONICAL_EVENT.writeApplied ||
    eventType === CANONICAL_EVENT.effectApplied ||
    eventType === CANONICAL_EVENT.effectReconciled
  ) {
    const receiptFact = fact(runId, sequenceNo, "external_receipt", {
      external_receipt_ref: firstSafeOpaqueRef(payload, [
        "connector_receipt_ref",
        "receipt_ref",
      ]),
    });
    if (receiptFact !== null) facts.push(receiptFact);
  }

  return facts;
}

function connectorAndTool(
  eventType: string,
  payload: Record<string, unknown>,
): [string | null, string | null] {
  if (
    eventType === CANONICAL_EVENT.readExecuted ||
    eventType === CANONICAL_EVENT.actionClassified
  ) {
    return [safeText(payload.connector), safeText(payload.op)];
  }
  if (eventType === CANONICAL_EVENT.surfaceCreated) {
    const source = asRecord(payload.source);
    return source === null
      ? [null, null]
      : [safeText(source.connector), safeText(source.op)];
  }
  if (
    eventType === CANONICAL_EVENT.operationRequested ||
    eventType === CANONICAL_EVENT.effectStaged
  ) {
    return [safeText(payload.capability), safeText(payload.op)];
  }
  if (eventType.startsWith("connector.") || eventType.startsWith("tool.")) {
    return [safeText(payload.connector), safeText(payload.op)];
  }
  return [null, null];
}

function isConnectorEvent(eventType: string): boolean {
  return (
    eventType === CANONICAL_EVENT.readExecuted ||
    eventType === CANONICAL_EVENT.actionClassified ||
    eventType === CANONICAL_EVENT.surfaceCreated ||
    eventType === CANONICAL_EVENT.operationRequested ||
    eventType === CANONICAL_EVENT.effectStaged ||
    eventType.startsWith("connector.") ||
    eventType.startsWith("tool.")
  );
}

function workspaceFields(payload: Record<string, unknown>): {
  grantLabel: string | null;
  virtualPathKey: string;
} | null {
  const grantLabel = firstSafeText(payload, [
    "workspace_grant_label",
    "grant_label",
    "display_target",
  ]);
  if (typeof payload.target_ref === "string") {
    try {
      const target = WorkspaceTargetRefCodec.parse(payload.target_ref);
      const grantId = safeOpaqueToken(target.grant_id);
      const pathToken = safeOpaqueToken(target.path_token);
      if (grantId !== null && pathToken !== null) {
        return {
          grantLabel,
          virtualPathKey: workspaceKey(grantId, pathToken),
        };
      }
    } catch {
      // Fall through to explicit compatible token fields.
    }
  }

  const grantId = firstOpaqueToken(payload, ["workspace_grant_id", "grant_id"]);
  const pathToken = firstOpaqueToken(payload, [
    "workspace_virtual_path_token",
    "virtual_path_token",
    "path_token",
  ]);
  return grantId === null || pathToken === null
    ? null
    : { grantLabel, virtualPathKey: workspaceKey(grantId, pathToken) };
}

function workspaceKey(grantId: string, pathToken: string): string {
  return `workspace:v2:${grantId}:${pathToken}`;
}

function fact(
  runId: string,
  sequenceNo: number,
  kind: SourceFactKindV2,
  fields: Partial<SourceFields>,
): SourceFactV2 | null {
  if (
    !Object.values(fields).some(
      (value) => value !== null && value !== undefined,
    )
  ) {
    return null;
  }
  return {
    source_id: `${SOURCE_ID_PREFIX}${String(sequenceNo).padStart(3, "0")}:${kind}`,
    kind,
    sequence_no: sequenceNo,
    ledger_id: safeLedgerId(runId, sequenceNo),
    connector: fields.connector ?? null,
    tool: fields.tool ?? null,
    origin: fields.origin ?? null,
    artifact_id: fields.artifact_id ?? null,
    artifact_revision: fields.artifact_revision ?? null,
    artifact_source_ref: fields.artifact_source_ref ?? null,
    workspace_grant_label: fields.workspace_grant_label ?? null,
    workspace_virtual_path_key: fields.workspace_virtual_path_key ?? null,
    browser_origin: fields.browser_origin ?? null,
    sandbox_operation: fields.sandbox_operation ?? null,
    subagent_task: fields.subagent_task ?? null,
    external_receipt_ref: fields.external_receipt_ref ?? null,
  };
}

function safeLedgerId(runId: string, sequenceNo: number): string | null {
  try {
    return formatLedgerId(runId, sequenceNo);
  } catch {
    return null;
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

function positiveInt(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 1
    ? value
    : null;
}

function firstSafeText(
  payload: Record<string, unknown>,
  keys: readonly string[],
): string | null {
  for (const key of keys) {
    const value = safeText(payload[key]);
    if (value !== null) return value;
  }
  return null;
}

function firstSafeOpaqueRef(
  payload: Record<string, unknown>,
  keys: readonly string[],
): string | null {
  for (const key of keys) {
    const value = safeOpaqueRef(payload[key]);
    if (value !== null) return value;
  }
  return null;
}

function firstSafeSandboxOperation(
  payload: Record<string, unknown>,
  keys: readonly string[],
): string | null {
  for (const key of keys) {
    const value = safeText(payload[key]);
    if (value !== null && SANDBOX_OPERATION.test(value)) return value;
  }
  return null;
}

function firstOpaqueToken(
  payload: Record<string, unknown>,
  keys: readonly string[],
): string | null {
  for (const key of keys) {
    const value = safeOpaqueToken(payload[key]);
    if (value !== null) return value;
  }
  return null;
}

function safeText(value: unknown): string | null {
  if (
    typeof value !== "string" ||
    value.trim().length === 0 ||
    value.length > 512
  ) {
    return null;
  }
  const lowered = value.toLowerCase();
  if (
    PHYSICAL_PATH.test(value) ||
    lowered.includes("file://") ||
    lowered.includes("filesystem://") ||
    SENSITIVE_TEXT.test(value)
  ) {
    return null;
  }
  // Keep untrusted labels as text. Rendering/escaping belongs to the host UI.
  return value;
}

function safeOpaqueRef(value: unknown): string | null {
  if (
    typeof value !== "string" ||
    value.length > 2048 ||
    safeText(value) === null
  ) {
    return null;
  }
  return OPAQUE_REF.test(value) || OPAQUE_TOKEN.test(value) ? value : null;
}

function safeOpaqueToken(value: unknown): string | null {
  if (typeof value !== "string" || safeText(value) === null) return null;
  return OPAQUE_TOKEN.test(value) ? value : null;
}

function originFrom(
  payload: Record<string, unknown>,
  keys: readonly string[],
): string | null {
  for (const key of keys) {
    const value = safeOrigin(payload[key]);
    if (value !== null) return value;
  }
  return null;
}

function safeOrigin(value: unknown): string | null {
  if (typeof value !== "string" || value.length > 2048) return null;
  try {
    const parsed = new URL(value);
    const scheme = parsed.protocol.slice(0, -1).toLowerCase();
    if (
      (scheme !== "http" && scheme !== "https") ||
      parsed.hostname.length === 0 ||
      parsed.username.length > 0 ||
      parsed.password.length > 0 ||
      (SENSITIVE_TEXT.test(value) && parsed.search.length === 0)
    ) {
      return null;
    }
    const host = parsed.hostname.toLowerCase();
    const port = parsed.port.length > 0 ? `:${parsed.port}` : "";
    return `${scheme}://${host}${port}`;
  } catch {
    return null;
  }
}

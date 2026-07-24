// @vitest-environment node
import { describe, expect, it } from "vitest";

import contract from "../../service-contracts/src/copilot_service_contracts/work_ledger.json";
import legacyGolden from "../../service-contracts/src/copilot_service_contracts/work_ledger_golden_events.json";
import journeysFixture from "../../service-contracts/src/copilot_service_contracts/work_ledger_v2_1_golden_journeys.json";
import vectors from "../../service-contracts/src/copilot_service_contracts/work_ledger_v2_1_vectors.json";
import {
  ArtifactContentRefCodec,
  ArtifactEffectFormatError,
  ArtifactIdCodec,
  CanonicalJsonError,
  EffectReceiptRefCodec,
  EffectStageIdCodec,
  OperationArgsRefCodec,
  OperationIdCodec,
  ProposalUriCodec,
  WorkspaceTargetRefCodec,
  canonicalJson,
  canonicalJsonSha256,
  compatibilityEventType,
  isLedgerPayloadForWrite,
  projectLegacyLedgerForRead,
  sha256Hex,
  type Artifact,
  type ArtifactIntent,
  type ArtifactRevision,
  type EffectDecision,
  type EffectExecutionRequest,
  type EffectExecutionResult,
  type EffectStage,
  type EffectTarget,
  type LedgerEventType,
  type OperationDescriptor,
  type OperationDisposition,
  type OperationRequest,
  type ProposalRef,
  type SurfaceSubject,
} from "./ledger";

type JsonObject = Record<string, any>;
interface JourneyEvent {
  event_type: LedgerEventType;
  run_id: string;
  sequence_no: number;
  created_at: string;
  payload: JsonObject;
}

const exactKeys =
  <T>() =>
  <K extends readonly (keyof T)[]>(
    ...keys: Exclude<keyof T, K[number]> extends never ? K : never
  ): K =>
    keys;

const ENTITY_KEYS = {
  OperationRequest: exactKeys<OperationRequest>()(
    "operation_id",
    "run_id",
    "producer",
    "capability",
    "op",
    "canonical_args_ref",
    "args_digest",
    "requested_at",
    "artifact_intent",
    "effect_hint",
    "parent_operation_id",
  ),
  OperationDescriptor: exactKeys<OperationDescriptor>()(
    "capability",
    "op",
    "executor",
    "effect_class",
    "result_kind",
    "supports_prepare",
    "supports_reconcile",
    "required_gate_kinds",
    "max_inline_result_bytes",
  ),
  OperationDisposition: exactKeys<OperationDisposition>()(
    "operation_id",
    "outcome",
    "artifact_ids",
    "stage_ids",
    "activity_ref",
    "agent_summary",
    "retryable",
  ),
  Artifact: exactKeys<Artifact>()(
    "artifact_id",
    "org_id",
    "user_id",
    "conversation_id",
    "run_id",
    "kind",
    "title",
    "media_type",
    "current_revision",
    "created_by",
    "created_at",
    "updated_at",
    "deleted_at",
  ),
  ArtifactRevision: exactKeys<ArtifactRevision>()(
    "artifact_id",
    "revision",
    "parent_revision",
    "content_ref",
    "content_digest",
    "byte_size",
    "author",
    "source_ref",
    "created_at",
  ),
  ArtifactIntent: exactKeys<ArtifactIntent>()(
    "kind",
    "title",
    "media_type",
    "suggested_filename",
    "presentation_preference",
  ),
  SurfaceSubject: exactKeys<SurfaceSubject>()("subject_type", "subject_id"),
  EffectTarget: exactKeys<EffectTarget>()(
    "executor",
    "capability",
    "op",
    "target_ref",
    "precondition_ref",
    "display_label",
  ),
  ProposalRef: exactKeys<ProposalRef>()(
    "proposal_ref",
    "proposal_digest",
    "media_type",
    "byte_size",
  ),
  EffectStage: exactKeys<EffectStage>()(
    "stage_id",
    "operation_id",
    "run_id",
    "executor",
    "target",
    "proposal",
    "revision",
    "status",
    "policy_snapshot_ref",
    "created_at",
    "updated_at",
  ),
  EffectDecision: exactKeys<EffectDecision>()(
    "stage_id",
    "revision",
    "decision",
    "actor",
    "proposal_digest",
    "target_digest",
    "decided_at",
    "ledger_id",
  ),
  EffectExecutionRequest: exactKeys<EffectExecutionRequest>()(
    "stage_id",
    "revision",
    "idempotency_key",
    "target_ref",
    "target_digest",
    "proposal_ref",
    "proposal_digest",
    "actor",
    "decision_ledger_id",
  ),
  EffectExecutionResult: exactKeys<EffectExecutionResult>()(
    "outcome",
    "receipt_ref",
    "result_digest",
    "retryable",
    "safe_message",
  ),
} as const;

function compileTimeImmutableEntityPin(
  operation: OperationRequest,
  artifact: Artifact,
  stage: EffectStage,
  result: EffectExecutionResult,
): void {
  // @ts-expect-error v2.1 wire entities are immutable
  operation.operation_id = "mutated";
  // @ts-expect-error v2.1 wire entities are immutable
  artifact.current_revision = 99;
  // @ts-expect-error v2.1 wire entities are immutable
  stage.status = "applied";
  // @ts-expect-error v2.1 wire entities are immutable
  result.retryable = true;
}
void compileTimeImmutableEntityPin;

function allPayloadSamples(): Map<string, JsonObject> {
  const samples = new Map<string, JsonObject>();
  for (const event of legacyGolden.events as JourneyEvent[]) {
    if (!samples.has(event.event_type)) {
      samples.set(event.event_type, structuredClone(event.payload));
    }
  }
  for (const journey of journeysFixture.journeys) {
    for (const event of journey.events as JourneyEvent[]) {
      if (!samples.has(event.event_type)) {
        samples.set(event.event_type, structuredClone(event.payload));
      }
    }
  }
  return samples;
}

describe("v2.1 strict writer contract", () => {
  it("pins every public entity key to the SSOT metadata", () => {
    expect(Object.keys(ENTITY_KEYS).sort()).toEqual(
      Object.keys(contract.entities).sort(),
    );
    for (const [name, keys] of Object.entries(ENTITY_KEYS)) {
      const metadata =
        contract.entities[name as keyof typeof contract.entities];
      expect([...keys].sort(), name).toEqual(
        [...metadata.required, ...metadata.optional].sort(),
      );
    }
  });

  it("accepts one sample for every event and rejects unknown/missing fields", () => {
    const samples = allPayloadSamples();
    const schemas = contract.events as Record<
      LedgerEventType,
      { required: string[]; optional?: string[] }
    >;
    expect([...samples.keys()].sort()).toEqual(Object.keys(schemas).sort());
    for (const [eventType, schema] of Object.entries(schemas) as Array<
      [LedgerEventType, { required: string[]; optional?: string[] }]
    >) {
      const sample = samples.get(eventType);
      expect(sample, eventType).toBeDefined();
      expect(isLedgerPayloadForWrite(eventType, sample), eventType).toBe(true);
      expect(
        isLedgerPayloadForWrite(eventType, {
          ...sample,
          unexpected_contract_field: true,
        }),
        eventType,
      ).toBe(false);
      for (const key of schema.required) {
        const missing = structuredClone(sample);
        delete missing[key];
        expect(
          isLedgerPayloadForWrite(eventType, missing),
          `${eventType}.${key}`,
        ).toBe(false);
      }
    }
  });

  it("rejects unknown closed-enum values", () => {
    const samples = allPayloadSamples();
    const schemas = contract.events as Record<
      LedgerEventType,
      { enum_fields?: Record<string, string> }
    >;
    for (const [eventType, schema] of Object.entries(schemas) as Array<
      [LedgerEventType, { enum_fields?: Record<string, string> }]
    >) {
      for (const field of Object.keys(schema.enum_fields ?? {})) {
        expect(
          isLedgerPayloadForWrite(eventType, {
            ...samples.get(eventType),
            [field]: "__future_unknown__",
          }),
          `${eventType}.${field}`,
        ).toBe(false);
      }
    }
  });

  it("rejects malformed ids, digests, refs, numbers, booleans, and cross-links", () => {
    const samples = allPayloadSamples();
    const operation = samples.get("operation.requested")!;
    const artifact = samples.get("artifact.created")!;
    const staged = samples.get("effect.staged")!;
    const reconciled = samples.get("effect.reconciled")!;
    const invalid: Array<[LedgerEventType, JsonObject]> = [
      ["operation.requested", { ...operation, operation_id: undefined }],
      ["operation.requested", { ...operation, capability: null }],
      ["operation.requested", { ...operation, args_digest: "not-a-digest" }],
      [
        "operation.classified",
        {
          ...samples.get("operation.classified"),
          confidence: Number.NaN,
        },
      ],
      [
        "operation.failed",
        {
          ...samples.get("operation.failed"),
          retryable: "false",
        },
      ],
      ["artifact.created", { ...artifact, revision: -1 }],
      [
        "artifact.created",
        {
          ...artifact,
          content_ref:
            "artifact://art_123e4567-e89b-42d3-a456-426614174000/revisions/1",
        },
      ],
      [
        "artifact.promoted",
        {
          ...samples.get("artifact.promoted"),
          source_ref: "/Users/alice/private.csv",
        },
      ],
      [
        "effect.staged",
        {
          ...staged,
          target_ref: "file:///Users/alice/private.csv",
        },
      ],
      [
        "effect.staged",
        {
          ...staged,
          proposal_ref:
            "proposal://stg_018f47a6-7b2c-7c10-8f21-123456789abc/revisions/1",
        },
      ],
      [
        "effect.claimed",
        {
          ...samples.get("effect.claimed"),
          claim_id: "claim..traversal",
        },
      ],
      [
        "effect.claimed",
        {
          ...samples.get("effect.claimed"),
          revision: Number.MAX_SAFE_INTEGER + 1,
        },
      ],
      [
        "effect.reconciled",
        {
          ...reconciled,
          receipt_ref: EffectReceiptRefCodec.format(
            reconciled.stage_id,
            "different_claim",
          ),
        },
      ],
      [
        "gate.opened.v2",
        {
          ...samples.get("gate.opened.v2"),
          reason: null,
        },
      ],
    ];
    for (const [eventType, payload] of invalid) {
      expect(isLedgerPayloadForWrite(eventType, payload), eventType).toBe(
        false,
      );
    }
  });

  it("exposes read-side compatibility mappings without treating gates as writes", () => {
    expect(compatibilityEventType("action.classified")).toBe(
      "operation.classified",
    );
    expect(compatibilityEventType("write.staged")).toBe("effect.staged");
    expect(compatibilityEventType("surface.created")).toBe("surface.created");
    expect(compatibilityEventType("gate.opened")).toBeNull();
    expect(contract.compatibility.read_side_only).toBe(true);
    expect(contract.compatibility.legacy_gate_write_input).toBe(false);
  });

  it("replays the complete legacy fixture through the shared read projector", () => {
    expect(projectLegacyLedgerForRead(legacyGolden.events)).toEqual(
      vectors.legacy_compatibility.expected,
    );
    for (let length = 0; length <= legacyGolden.events.length; length += 1) {
      expect(() =>
        projectLegacyLedgerForRead(legacyGolden.events.slice(0, length)),
      ).not.toThrow();
    }
    expect(
      projectLegacyLedgerForRead(legacyGolden.events).legacy_gates.every(
        (gate) => gate.valid_generalized_write_input === false,
      ),
    ).toBe(true);
  });
});

describe("v2.1 identifier and reference codecs", () => {
  it("round-trips every shared identifier vector", () => {
    const codecs = {
      operation_id: OperationIdCodec,
      artifact_id: ArtifactIdCodec,
      effect_stage_id: EffectStageIdCodec,
    };
    for (const vector of vectors.identifiers) {
      const codec = codecs[vector.kind as keyof typeof codecs];
      expect(codec.format(vector.uuid)).toBe(vector.formatted);
      expect(codec.parse(vector.formatted)).toBe(vector.uuid);
    }
  });

  it("round-trips every shared reference vector", () => {
    for (const vector of vectors.references) {
      const parts = vector.parts as JsonObject;
      switch (vector.kind) {
        case "artifact_content":
          expect(
            ArtifactContentRefCodec.format(parts.artifact_id, parts.revision),
          ).toBe(vector.formatted);
          expect(ArtifactContentRefCodec.parse(vector.formatted)).toEqual(
            parts,
          );
          break;
        case "operation_args":
          expect(OperationArgsRefCodec.format(parts.operation_id)).toBe(
            vector.formatted,
          );
          expect(OperationArgsRefCodec.parse(vector.formatted)).toEqual(parts);
          break;
        case "proposal":
          expect(ProposalUriCodec.format(parts.stage_id, parts.revision)).toBe(
            vector.formatted,
          );
          expect(ProposalUriCodec.parse(vector.formatted)).toEqual(parts);
          break;
        case "effect_receipt":
          expect(
            EffectReceiptRefCodec.format(parts.stage_id, parts.claim_id),
          ).toBe(vector.formatted);
          expect(EffectReceiptRefCodec.parse(vector.formatted)).toEqual(parts);
          break;
        case "workspace_target":
          expect(
            WorkspaceTargetRefCodec.format(parts.grant_id, parts.path_token),
          ).toBe(vector.formatted);
          expect(WorkspaceTargetRefCodec.parse(vector.formatted)).toEqual(
            parts,
          );
          break;
      }
    }
  });

  it("rejects bare/uppercase UUIDs, traversal, zero revisions, and extra segments", () => {
    const bad = [
      () => OperationIdCodec.parse("018f47a6-7b2c-7a10-8f21-123456789abc"),
      () => OperationIdCodec.parse("op_018F47A6-7B2C-7A10-8F21-123456789ABC"),
      () =>
        ArtifactContentRefCodec.parse(
          "artifact://art_123e4567-e89b-42d3-a456-426614174000/revisions/0",
        ),
      () =>
        ProposalUriCodec.parse(
          "proposal://stg_018f47a6-7b2c-7c10-8f21-123456789abc/revisions/1/extra",
        ),
      () => WorkspaceTargetRefCodec.parse("workspace-target://grant/../token"),
      () =>
        ArtifactContentRefCodec.format(
          "art_123e4567-e89b-42d3-a456-426614174000",
          Number.MAX_SAFE_INTEGER + 1,
        ),
      () =>
        ArtifactContentRefCodec.parse(
          "artifact://art_123e4567-e89b-42d3-a456-426614174000/revisions/9007199254740993",
        ),
    ];
    for (const invoke of bad) {
      expect(invoke).toThrow(ArtifactEffectFormatError);
    }
  });
});

describe("v2.1 canonical JSON and SHA-256", () => {
  it("matches every shared structured vector", async () => {
    for (const vector of vectors.canonical_json) {
      expect(canonicalJson(vector.value), vector.id).toBe(vector.canonical);
      expect(await canonicalJsonSha256(vector.value), vector.id).toBe(
        vector.sha256,
      );
    }
  });

  it("hashes bytes as bytes", async () => {
    for (const vector of vectors.byte_digests) {
      expect(await sha256Hex(new TextEncoder().encode(vector.utf8))).toBe(
        vector.sha256,
      );
    }
  });

  it("rejects every shared invalid canonical-JSON recipe", () => {
    const observed = new Set<string>();
    for (const vector of vectors.invalid_canonical_json) {
      observed.add(vector.id);
      expect(
        () =>
          canonicalJson(
            materializeInvalidCanonicalJson(
              vector.recipe as Record<string, string>,
            ),
          ),
        vector.id,
      ).toThrow(CanonicalJsonError);
    }
    expect(observed).toEqual(
      new Set([
        "nan",
        "positive_infinity",
        "negative_infinity",
        "unsafe_integer",
        "unsupported_value",
        "non_string_key",
        "cycle",
        "unpaired_surrogate",
      ]),
    );
  });

  it("also rejects sparse arrays and non-plain objects", () => {
    const sparse = new Array(2);
    sparse[1] = "value";
    const invalid = [undefined, 1n, new Date(), sparse];
    for (const value of invalid) {
      expect(() => canonicalJson(value)).toThrow(CanonicalJsonError);
    }
  });
});

function materializeInvalidCanonicalJson(
  recipe: Record<string, string>,
): unknown {
  switch (recipe.kind) {
    case "non_finite":
      return {
        nan: Number.NaN,
        positive: Number.POSITIVE_INFINITY,
        negative: Number.NEGATIVE_INFINITY,
      }[recipe.variant];
    case "unsafe_integer":
      return Number(recipe.decimal);
    case "unsupported_binary":
      return new Uint8Array([1, 2, 3]);
    case "non_string_key": {
      const value = { valid: true };
      Object.defineProperty(value, Symbol("invalid"), {
        enumerable: true,
        value: "bad",
      });
      return value;
    }
    case "cycle": {
      const value: unknown[] = [];
      value.push(value);
      return value;
    }
    case "unpaired_surrogate":
      return String.fromCharCode(Number.parseInt(recipe.code_unit, 16));
    default:
      throw new Error(`unknown invalid-vector recipe: ${recipe.kind}`);
  }
}

function emptyReceipt(): JsonObject {
  return {
    operations: {
      requested: 0,
      succeeded: 0,
      staged: 0,
      blocked: 0,
      cancelled: 0,
      failed: 0,
    },
    effects: {
      staged: 0,
      applied: 0,
      partial: 0,
      failed: 0,
      cancelled: 0,
      indeterminate: 0,
      already_applied: 0,
      precondition_drift: 0,
    },
    gates: { opened: 0, resolved: 0 },
  };
}

function applyOutcome(stage: JsonObject, outcome: string): void {
  stage.outcome = outcome;
  stage.status =
    {
      applied: "applied",
      already_applied: "applied",
      partial: "partial",
      failed: "failed",
      cancelled: "cancelled",
      indeterminate: "indeterminate",
      precondition_drift: "precondition_drift",
    }[outcome] ?? "failed";
}

function fold(events: JourneyEvent[]): JsonObject {
  const artifacts = new Map<string, JsonObject>();
  const stages = new Map<string, JsonObject>();
  const canvas: JsonObject[] = [];
  const openGates = new Map<string, JsonObject>();
  const receipt = emptyReceipt();

  for (const event of events) {
    const payload = event.payload;
    switch (event.event_type) {
      case "operation.requested":
        receipt.operations.requested += 1;
        break;
      case "operation.completed":
        receipt.operations[payload.outcome] += 1;
        break;
      case "operation.failed":
        receipt.operations.failed += 1;
        break;
      case "artifact.created":
        artifacts.set(payload.artifact_id, {
          artifact_id: payload.artifact_id,
          kind: payload.kind,
          revision: payload.revision,
          content_ref: payload.content_ref,
          content_digest: payload.content_digest,
          author: payload.author,
          presentation: null,
        });
        break;
      case "artifact.revised":
        Object.assign(artifacts.get(payload.artifact_id)!, {
          revision: payload.revision,
          content_ref: payload.content_ref,
          content_digest: payload.content_digest,
          author: payload.author,
        });
        break;
      case "artifact.presentation_decided": {
        const artifact = artifacts.get(payload.artifact_id)!;
        artifact.presentation = {
          decision: payload.decision,
          basis: payload.basis,
          surface_id: payload.surface_id ?? null,
        };
        for (let index = canvas.length - 1; index >= 0; index -= 1) {
          if (
            canvas[index].subject_type === "artifact" &&
            canvas[index].subject_id === payload.artifact_id
          ) {
            canvas.splice(index, 1);
          }
        }
        if (payload.decision === "canvas") {
          canvas.push({
            subject_type: "artifact",
            subject_id: payload.artifact_id,
            surface_id: payload.surface_id ?? null,
          });
        }
        break;
      }
      case "surface.created":
        canvas.push({
          subject_type: "record",
          subject_id: payload.surface_id,
          surface_id: payload.surface_id,
        });
        break;
      case "effect.staged":
        stages.set(payload.stage_id, {
          stage_id: payload.stage_id,
          operation_id: payload.operation_id,
          executor: payload.executor,
          target_ref: payload.target_ref,
          target_digest: payload.target_digest,
          proposal_ref: payload.proposal_ref,
          proposal_digest: payload.proposal_digest,
          revision: 1,
          status: "staged",
          policy: payload.policy,
          decision: null,
          claim_id: null,
          outcome: null,
        });
        canvas.push({
          subject_type: "stage",
          subject_id: payload.stage_id,
          surface_id: null,
        });
        receipt.effects.staged += 1;
        break;
      case "effect.revised":
        Object.assign(stages.get(payload.stage_id)!, {
          revision: payload.revision,
          proposal_ref: payload.proposal_ref,
          proposal_digest: payload.proposal_digest,
          status: "staged",
          decision: null,
          claim_id: null,
          outcome: null,
        });
        break;
      case "effect.decision_recorded": {
        const stage = stages.get(payload.stage_id)!;
        stage.decision = {
          decision: payload.decision,
          actor: payload.actor,
        };
        stage.status = {
          approve: "approved",
          reject: "rejected",
          restore: "staged",
          cancel: "cancelled",
        }[payload.decision];
        break;
      }
      case "effect.claimed": {
        const stage = stages.get(payload.stage_id)!;
        stage.status = "claimed";
        stage.claim_id = payload.claim_id;
        break;
      }
      case "effect.applied":
        applyOutcome(stages.get(payload.stage_id)!, payload.outcome);
        receipt.effects[payload.outcome] += 1;
        break;
      case "effect.indeterminate": {
        const stage = stages.get(payload.stage_id)!;
        stage.status = "indeterminate";
        stage.claim_id = payload.claim_id;
        stage.outcome = "indeterminate";
        receipt.effects.indeterminate += 1;
        break;
      }
      case "effect.reconciled":
        applyOutcome(stages.get(payload.stage_id)!, payload.outcome);
        receipt.effects[payload.outcome] += 1;
        break;
      case "gate.opened.v2":
        openGates.set(payload.gate_id, {
          kind: "gate",
          id: payload.gate_id,
        });
        receipt.gates.opened += 1;
        break;
      case "gate.resolved.v2":
        openGates.delete(payload.gate_id);
        receipt.gates.resolved += 1;
        break;
    }
  }

  const pendingStatuses = new Set([
    "staged",
    "approved",
    "claimed",
    "indeterminate",
    "precondition_drift",
  ]);
  const pending = [...openGates.values()];
  for (const [stageId, stage] of stages) {
    if (pendingStatuses.has(stage.status)) {
      pending.push({ kind: "effect", id: stageId });
    }
  }
  pending.sort((a, b) =>
    `${a.kind}:${a.id}`.localeCompare(`${b.kind}:${b.id}`),
  );
  return {
    artifacts: [...artifacts.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([, value]) => value),
    stages: [...stages.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([, value]) => value),
    canvas,
    receipt,
    pending_work: pending,
  };
}

describe("v2.1 golden journey referee fold", () => {
  it("folds every prefix without throwing", () => {
    expect(journeysFixture.journeys.length).toBeGreaterThanOrEqual(12);
    for (const journey of journeysFixture.journeys) {
      const events = journey.events as JourneyEvent[];
      for (let length = 0; length <= events.length; length += 1) {
        expect(() =>
          fold(structuredClone(events.slice(0, length))),
        ).not.toThrow();
      }
    }
  });

  it("matches every checked-in final snapshot", () => {
    for (const journey of journeysFixture.journeys) {
      expect(fold(journey.events as JourneyEvent[]), journey.id).toEqual(
        journey.expected,
      );
    }
  });

  it("keeps a destructive effect held despite allow_always posture", () => {
    const journey = journeysFixture.journeys.find(
      (candidate) => candidate.id === "destructive_effect_held",
    ) as unknown as {
      policy_context: Record<string, string>;
      events: JourneyEvent[];
      expected: JsonObject;
    };
    expect(journey.policy_context).toEqual({
      configured_write_policy: "allow_always",
      effect_class: "external_destructive",
      resolved_effect_policy: "require",
    });
    expect(
      journey.events.some((event) =>
        new Set([
          "effect.decision_recorded",
          "effect.claimed",
          "effect.applied",
        ]).has(event.event_type),
      ),
    ).toBe(false);
    const state = fold(structuredClone(journey.events));
    expect(state.stages[0].status).toBe("staged");
    expect(state.pending_work).toEqual([
      {
        kind: "effect",
        id: "stg_018f47a6-7b2c-7c10-8f21-12345678c012",
      },
    ]);
  });
});

// @vitest-environment node
import { describe, expect, it } from "vitest";

import corpusJson from "../../service-contracts/src/copilot_service_contracts/legacy_v2_replay_corpus.json";
import legacyGolden from "../../service-contracts/src/copilot_service_contracts/work_ledger_golden_events.json";

import {
  projectLegacyV2Replay,
  type LegacyV2ReplayEvent,
  type LegacyV2ReplayProjection,
} from "./legacyV2Replay";

interface ReplayCorpusCase {
  readonly id: string;
  readonly events: readonly LegacyV2ReplayEvent[];
  readonly expected: LegacyV2ReplayProjection;
}

const corpus = corpusJson as unknown as {
  readonly reader_version: number;
  readonly cases: readonly ReplayCorpusCase[];
};

function replayCase(id: string): ReplayCorpusCase {
  const result = corpus.cases.find((item) => item.id === id);
  if (result === undefined)
    throw new Error(`missing replay corpus case: ${id}`);
  return result;
}

describe("projectLegacyV2Replay", () => {
  it("matches every shared sanitized Python/TypeScript replay vector", () => {
    expect(corpus.reader_version).toBe(1);
    for (const item of corpus.cases) {
      const before = JSON.parse(JSON.stringify(item.events)) as unknown;
      expect(projectLegacyV2Replay(item.events)).toEqual(item.expected);
      // Input is the persisted source of truth: no reader may retrofit it.
      expect(item.events).toEqual(before);
      expect(
        projectLegacyV2Replay(item.events).surfaces.every(
          (surface) => surface.read_only === true,
        ),
      ).toBe(true);
    }
  });

  it("deduplicates reconnect frames after deterministic sequence ordering", () => {
    const item = replayCase("connector_subject_declared_reference_hydration");
    const replayed = JSON.parse(JSON.stringify(item.events)) as Array<
      Record<string, unknown>
    >;
    const duplicate = JSON.parse(JSON.stringify(replayed[0])) as Record<
      string,
      unknown
    >;
    duplicate.sequence_no = 99;
    replayed.push(duplicate);
    expect(projectLegacyV2Replay(replayed)).toEqual(
      projectLegacyV2Replay(item.events),
    );
  });

  it("replays every prefix of the checked-in old ledger fixture without creating a writable surface", () => {
    for (let length = 0; length <= legacyGolden.events.length; length += 1) {
      const projection = projectLegacyV2Replay(
        legacyGolden.events.slice(0, length),
      );
      expect(projection.surfaces.every((surface) => surface.read_only)).toBe(
        true,
      );
      expect(JSON.stringify(projection)).not.toContain('"stage_id"');
    }
  });

  it("classifies a canonical-only stream without inventing a legacy subject", () => {
    expect(
      projectLegacyV2Replay([
        {
          event_id: "op-1",
          event_type: "operation.requested",
          sequence_no: 1,
          payload: { operation_id: "op_00000000-0000-4000-8000-000000000001" },
        },
      ]),
    ).toEqual({
      reader_version: 1,
      mode: "canonical_v21",
      surfaces: [],
      quarantined: [],
    });
  });
});

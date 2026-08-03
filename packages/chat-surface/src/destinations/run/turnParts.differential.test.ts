// Differential: the TypeScript turn-parts fold vs the Python one, at EVERY
// replay prefix of a shared corpus.
//
// The fold has to exist in both runtimes — Python persists the turn at seal
// time so it reloads in the shape it rendered, TypeScript folds it live as the
// tokens arrive. That duplication is deliberate and bounded; this test is the
// thing that stops it drifting. Prefix-by-prefix is the point: an open/close
// part state machine can agree on the final answer while disagreeing halfway
// through, and halfway through is exactly what a streaming user looks at.
//
// Mirrors the `canvasLifecycle` differential (PRD-B3), including its runner
// contract and its cwd assumption.

import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import corpus from "../../../../service-contracts/src/copilot_service_contracts/turn_parts_corpus.json";

import { projectChatMessages } from "./chatProjection";

type CorpusEvent = {
  readonly sequence_no: number;
  readonly event_type: string;
  readonly payload?: Record<string, unknown>;
  readonly summary?: string;
  readonly subagent_id?: string;
};

type CorpusCase = {
  readonly id: string;
  readonly events: readonly CorpusEvent[];
};

// npm runs a workspace script with the package directory as cwd. Unlike
// `import.meta.url`, this remains a real filesystem path under Vitest's module
// virtualization.
const root = resolve(process.cwd(), "../..");
const pythonRunner = resolve(
  root,
  "services/ai-backend/tests/unit/agent_runtime/presentation/turn_parts_corpus_runner.py",
);
const workspacePython = resolve(root, "services/ai-backend/.venv/bin/python");

// Must match the Python runner's synthetic clock exactly — the corpus carries
// no timestamps, so a differential over derived time only means something when
// both sides derive it the same way.
const EPOCH_MS = 1_700_000_000_000;
const STEP_MS = 1_000;

function hydrate(events: readonly CorpusEvent[]): RuntimeEventEnvelope[] {
  return events.map(
    (entry, index) =>
      ({
        event_id: `evt-${entry.sequence_no}-${index}`,
        run_id: "run-1",
        conversation_id: "conv-1",
        sequence_no: entry.sequence_no,
        event_type: entry.event_type,
        activity_kind: "event",
        payload: entry.payload ?? {},
        ...(entry.summary !== undefined ? { summary: entry.summary } : {}),
        ...(entry.subagent_id !== undefined
          ? { subagent_id: entry.subagent_id }
          : {}),
        created_at: new Date(
          EPOCH_MS + entry.sequence_no * STEP_MS,
        ).toISOString(),
      }) as RuntimeEventEnvelope,
  );
}

/** The same snapshot shape the Python runner prints. */
function snapshot(events: readonly RuntimeEventEnvelope[]) {
  const parts = projectChatMessages(events)[0]?.parts ?? [];
  return parts.map((part) => ({
    type: part.type,
    text: part.text,
    seq: part.seq ?? null,
    status: part.status?.type ?? null,
    startedAtMs: part.startedAtMs ?? null,
    updatedAtMs: part.updatedAtMs ?? null,
  }));
}

function projectCorpus() {
  return {
    schema_version: corpus.schema_version,
    cases: (corpus.cases as readonly CorpusCase[]).map((caseItem) => {
      const events = hydrate(caseItem.events);
      return {
        id: caseItem.id,
        prefixes: Array.from({ length: events.length + 1 }, (_, end) =>
          snapshot(events.slice(0, end)),
        ),
      };
    }),
  };
}

function pythonCorpusProjection() {
  const python = process.env.PYTHON ?? workspacePython;
  if (!existsSync(python)) {
    throw new Error(
      `Turn parts differential test requires the ai-backend venv at ${python}`,
    );
  }
  return JSON.parse(
    execFileSync(python, [pythonRunner], { cwd: root, encoding: "utf8" }),
  ) as ReturnType<typeof projectCorpus>;
}

describe("assistant turn parts — TS/Python differential", () => {
  it("matches the Python fold for every shared corpus prefix", () => {
    expect(projectCorpus()).toEqual(pythonCorpusProjection());
  });

  // A corpus that stops covering the defect is worse than no corpus — it reads
  // as proof while asserting nothing. These pin the cases that matter.
  it("covers the interleaved-turn shapes the bucketed fold could not express", () => {
    const ids = (corpus.cases as readonly CorpusCase[]).map((c) => c.id);
    expect(ids).toContain("text-tools-text-single-turn");
    expect(ids).toContain("two-thinking-spans-across-two-tool-batches");
    expect(ids).toContain("reasoning-cap-must-not-hit-the-first-span");
  });

  // A differential proves the two folds AGREE; it cannot prove they are right.
  // The card-then-final case was wrong in both at once and passed cleanly. Value
  // assertions on the corpus are what close that gap.
  it("does not let the terminal text overwrite prose from before a card", () => {
    const target = (corpus.cases as readonly CorpusCase[]).find(
      (c) => c.id === "final-response-after-a-card-opens-its-own-part",
    );
    expect(target).toBeDefined();
    expect(snapshot(hydrate(target!.events)).map((p) => p.text)).toEqual([
      "Looking.",
      "Here is the answer.",
    ]);
  });

  it("keeps both halves of an interleaved turn in the final prefix", () => {
    const target = (corpus.cases as readonly CorpusCase[]).find(
      (c) => c.id === "text-tools-text-single-turn",
    );
    expect(target).toBeDefined();
    const parts = snapshot(hydrate(target!.events));
    expect(parts.map((p) => p.text)).toEqual([
      "Checking the deploy status.",
      "It shipped at 09:14.",
    ]);
  });
});

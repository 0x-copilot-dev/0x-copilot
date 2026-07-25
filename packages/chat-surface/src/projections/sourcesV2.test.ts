// Sources v2 cross-language fixture/safety pins.
//
// The event fixture and expected source-id prefix intentionally mirror
// services/ai-backend/tests/unit/agent_runtime/surfaces_v2/
// test_sources_projection_v2.py. Both implementations stay pure and have no
// runtime dependency on one another.

import { describe, expect, it } from "vitest";

import {
  ARTIFACT_EVENT_TYPES,
  EFFECT_EVENT_TYPES,
  LEDGER_EVENT_TYPES,
  OPERATION_EVENT_TYPES,
} from "@0x-copilot/api-types";

import { projectSourcesV2, type SourcesProjectionEventLike } from "./sourcesV2";

const RUN_ID = "run00000001abcdef";
const CANONICAL_EVENT = {
  readExecuted: LEDGER_EVENT_TYPES[3],
  writeApplied: LEDGER_EVENT_TYPES[12],
  operationRequested: OPERATION_EVENT_TYPES[0],
  artifactCreated: ARTIFACT_EVENT_TYPES[0],
  artifactPromoted: ARTIFACT_EVENT_TYPES[2],
  effectStaged: EFFECT_EVENT_TYPES[0],
  effectApplied: EFFECT_EVENT_TYPES[4],
} as const;

function event(
  eventType: string,
  sequenceNo: number,
  payload: Record<string, unknown>,
): SourcesProjectionEventLike {
  return { event_type: eventType, sequence_no: sequenceNo, payload };
}

describe("projectSourcesV2", () => {
  it("matches the Python fixture and source-id prefix", () => {
    // Deliberately supplied out of order: both projectors sort by sequence.
    const projection = projectSourcesV2(RUN_ID, [
      event(CANONICAL_EVENT.writeApplied, 7, {
        connector_receipt_ref: "receipt://connector/receipt_7",
      }),
      event(CANONICAL_EVENT.readExecuted, 1, {
        connector: "linear",
        op: "get_issue",
        origin: "https://linear.app/team/ENG-142?token=not-output",
      }),
      event(CANONICAL_EVENT.artifactCreated, 2, {
        artifact_id: "art_42",
        revision: 2,
        content_ref: "artifact://art_42/revisions/2",
      }),
      event(CANONICAL_EVENT.effectStaged, 3, {
        executor: "workspace",
        capability: "workspace",
        op: "replace",
        target_ref: "workspace-target://grant_finance/path_token_7",
        display_target: "Finance workspace change",
      }),
      event(CANONICAL_EVENT.effectStaged, 4, {
        executor: "browser",
        capability: "browser",
        op: "browser_submit",
        browser_origin: "https://portal.example.test/form?state=private",
      }),
      event(CANONICAL_EVENT.operationRequested, 5, {
        capability: "sandbox",
        op: "apply_patch",
      }),
      event("subagent.started", 6, {
        task_summary: "Compare the two implementation options.",
      }),
      event(CANONICAL_EVENT.effectApplied, 8, {
        receipt_ref: "receipt://effects/stage_8/claim_8",
      }),
    ]);

    expect(projection).toMatchObject({
      v: 2,
      run_id: RUN_ID,
      latest_sequence_no: 8,
    });
    expect(
      projection.facts.map(({ sequence_no, kind }) => [sequence_no, kind]),
    ).toEqual([
      [1, "connector"],
      [2, "artifact"],
      [3, "connector"],
      [3, "workspace"],
      [4, "connector"],
      [4, "browser"],
      [5, "connector"],
      [5, "sandbox"],
      [6, "subagent"],
      [7, "external_receipt"],
      [8, "external_receipt"],
    ]);
    expect(projection.facts.map((fact) => fact.source_id)).toEqual([
      "source:v2:001:connector",
      "source:v2:002:artifact",
      "source:v2:003:connector",
      "source:v2:003:workspace",
      "source:v2:004:connector",
      "source:v2:004:browser",
      "source:v2:005:connector",
      "source:v2:005:sandbox",
      "source:v2:006:subagent",
      "source:v2:007:external_receipt",
      "source:v2:008:external_receipt",
    ]);
    expect(
      projection.facts.every((fact) => fact.source_id.startsWith("source:v2:")),
    ).toBe(true);

    expect(projection.facts[0]).toMatchObject({
      connector: "linear",
      tool: "get_issue",
      origin: "https://linear.app",
    });
    expect(projection.facts[1]).toMatchObject({
      artifact_id: "art_42",
      artifact_revision: 2,
      artifact_source_ref: "artifact://art_42/revisions/2",
    });
    expect(projection.facts[3]).toMatchObject({
      workspace_grant_label: "Finance workspace change",
      workspace_virtual_path_key: "workspace:v2:grant_finance:path_token_7",
    });
    expect(projection.facts[5]?.browser_origin).toBe(
      "https://portal.example.test",
    );
    expect(projection.facts[7]?.sandbox_operation).toBe("apply_patch");
    expect(projection.facts[8]?.subagent_task).toBe(
      "Compare the two implementation options.",
    );
    expect(projection.facts[9]?.external_receipt_ref).toBe(
      "receipt://connector/receipt_7",
    );
  });

  it("redacts physical paths, credentials, raw arguments, and bodies", () => {
    const projection = projectSourcesV2(RUN_ID, [
      event(CANONICAL_EVENT.readExecuted, 1, {
        connector: "<img src=x onerror=alert(1)>",
        op: "</script>",
        arguments: { api_key: "never-copy" },
        body: "never-copy-this-full-body",
      }),
      event(CANONICAL_EVENT.effectStaged, 2, {
        executor: "workspace",
        target_ref: "workspace-target://grant_01/path_token_01",
        display_target: "/srv/alice/private/project.txt",
      }),
      event("browser.action", 3, {
        browser_origin: "https://cookie@example.test/?token=secret-value",
      }),
      event("sandbox.executed", 4, {
        operation: "echo $OPENAI_API_KEY",
        command: "never-copy-command",
      }),
      event("subagent.started", 5, { task: "cookie=session-secret" }),
      event(CANONICAL_EVENT.writeApplied, 6, {
        connector_receipt_ref: "receipt://provider?token=secret-value",
      }),
      event(CANONICAL_EVENT.artifactPromoted, 7, {
        artifact_id: "art_safe",
        revision: 1,
        source_ref: "file:///Users/alice/private.txt",
      }),
      event(CANONICAL_EVENT.artifactPromoted, 8, {
        artifact_id: "art_provider_token",
        revision: 1,
        source_ref: "artifact://sk-proj-abcdefghijklmnop/revisions/1",
      }),
      event("unknown.event", 9, {
        origin: "https://untrusted-origin.example.test/path",
      }),
    ]);

    expect(projection.facts[0]).toMatchObject({
      connector: "<img src=x onerror=alert(1)>",
      tool: "</script>",
    });
    const workspace = projection.facts.find(
      (fact) => fact.kind === "workspace",
    );
    expect(workspace).toMatchObject({
      workspace_grant_label: null,
      workspace_virtual_path_key: "workspace:v2:grant_01:path_token_01",
    });
    const artifact = projection.facts.find((fact) => fact.kind === "artifact");
    expect(artifact?.artifact_source_ref).toBeNull();

    const rendered = JSON.stringify(projection);
    [
      "/srv/alice/private/project.txt",
      "OPENAI_API_KEY",
      "secret-value",
      "never-copy",
      "never-copy-this-full-body",
      "never-copy-command",
      "file:///Users/alice/private.txt",
      "sk-proj-abcdefghijklmnop",
      "untrusted-origin.example.test",
    ].forEach((forbidden) => expect(rendered).not.toContain(forbidden));
    expect(
      projection.facts.some((fact) =>
        ["browser", "sandbox", "subagent", "external_receipt"].includes(
          fact.kind,
        ),
      ),
    ).toBe(false);
  });
});

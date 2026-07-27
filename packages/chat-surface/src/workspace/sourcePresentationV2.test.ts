import { describe, expect, it } from "vitest";

import type {
  SourceFactKindV2,
  SourceFactV2,
  SourcesProjectionV2,
} from "@0x-copilot/api-types";

import { presentSourcesV2 } from "./sourcePresentationV2";

function fact(
  sequence: number,
  kind: SourceFactKindV2,
  fields: Partial<SourceFactV2> = {},
): SourceFactV2 {
  return {
    source_id: `source:v2:${String(sequence).padStart(3, "0")}:${kind}`,
    kind,
    sequence_no: sequence,
    ledger_id: `rabc·${sequence}`,
    connector: null,
    tool: null,
    origin: null,
    artifact_id: null,
    artifact_revision: null,
    artifact_source_ref: null,
    workspace_grant_label: null,
    workspace_virtual_path_key: null,
    browser_origin: null,
    sandbox_operation: null,
    subagent_task: null,
    external_receipt_ref: null,
    ...fields,
  };
}

function projection(facts: readonly SourceFactV2[]): SourcesProjectionV2 {
  return {
    v: 2,
    run_id: "run_sources",
    latest_sequence_no: facts.at(-1)?.sequence_no ?? 0,
    facts,
  };
}

describe("presentSourcesV2", () => {
  it("groups connectors in first-seen order and humanizes every row", () => {
    const result = presentSourcesV2(
      projection([
        fact(1, "connector", {
          connector: "local_workspace",
          tool: "read_csv",
        }),
        fact(2, "connector", {
          connector: "local_workspace",
          tool: "publish_artifact",
        }),
        fact(3, "artifact", {
          artifact_id: "art_3",
          artifact_revision: 1,
        }),
      ]),
    );

    expect(result.total).toBe(3);
    expect(
      result.groups.map((group) => [group.label, group.rows.length]),
    ).toEqual([
      ["Local workspace", 2],
      ["Artifacts", 1],
    ]);
    expect(result.groups[0]?.rows.map((row) => row.title)).toEqual([
      "Read csv",
      "Publish artifact",
    ]);
    expect(result.groups[1]?.rows[0]).toMatchObject({
      title: "Generated Artifact",
      metadata: "Revision 1 · step 3",
      openable: true,
    });
  });

  it("covers every safe provenance kind with an intentional label", () => {
    const result = presentSourcesV2(
      projection([
        fact(1, "workspace", {
          workspace_grant_label: "Finance workspace",
          workspace_virtual_path_key: "workspace:v2:g:p",
        }),
        fact(2, "browser", { browser_origin: "https://example.test" }),
        fact(3, "sandbox", { sandbox_operation: "apply_patch" }),
        fact(4, "subagent", { subagent_task: "Check the generated file" }),
        fact(5, "external_receipt", {
          external_receipt_ref: "receipt://connector/r5",
        }),
      ]),
    );

    expect(result.groups.map((group) => group.label)).toEqual([
      "Workspace",
      "Browser",
      "Sandbox",
      "Subagents",
      "Receipts",
    ]);
    expect(
      result.groups.flatMap((group) => group.rows.map((row) => row.title)),
    ).toEqual([
      "Finance workspace",
      "Browser activity",
      "Apply patch",
      "Check the generated file",
      "External action receipt",
    ]);
  });

  it("never copies opaque refs, origins, ledger ids, or physical identifiers into presentation", () => {
    const result = presentSourcesV2(
      projection([
        fact(7, "artifact", {
          artifact_id: "art_private",
          artifact_revision: 2,
          artifact_source_ref: "artifact://art_private/revisions/2",
        }),
        fact(8, "connector", {
          connector: "linear",
          tool: "get_issue",
          origin: "https://linear.app",
          ledger_id: "rsecret·8",
        }),
      ]),
    );
    const rendered = JSON.stringify(result);

    for (const forbidden of [
      "art_private",
      "artifact://",
      "linear.app",
      "rsecret",
    ]) {
      expect(rendered).not.toContain(forbidden);
    }
  });
});

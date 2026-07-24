import { describe, expect, it } from "vitest";
import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";
import { projectArtifactTabs } from "./artifactProjection";

function event(
  event_type: string,
  sequence_no: number,
  payload: unknown,
): RuntimeEventEnvelope {
  return {
    event_id: `e${sequence_no}`,
    conversation_id: "conv_test",
    run_id: "run_test",
    sequence_no,
    event_type: event_type as RuntimeEventEnvelope["event_type"],
    activity_kind: "event",
    created_at: "2026-01-01T00:00:00Z",
    payload: payload as Record<string, unknown>,
  };
}

describe("projectArtifactTabs", () => {
  it("only exposes artifacts with an explicit canvas decision and advances revisions", () => {
    const tabs = projectArtifactTabs([
      event("artifact.created", 1, {
        artifact_id: "artifact_hidden",
        kind: "code",
        revision: 1,
      }),
      event("artifact.created", 2, {
        artifact_id: "artifact_csv",
        kind: "dataset",
        revision: 1,
      }),
      event("artifact.presentation_decided", 3, {
        artifact_id: "artifact_csv",
        decision: "canvas",
      }),
      event("artifact.revised", 4, {
        artifact_id: "artifact_csv",
        revision: 2,
      }),
      event("artifact.presentation_decided", 5, {
        artifact_id: "artifact_hidden",
        decision: "chat_card",
      }),
    ]);
    expect(tabs).toEqual([
      {
        artifactId: "artifact_csv",
        kind: "dataset",
        revision: 2,
        uri: "artifact-dataset://artifact_csv@2",
        title: "dataset artifact · r2",
        lastSeq: 4,
      },
    ]);
  });

  it("ignores malformed events rather than inventing a UI", () => {
    expect(
      projectArtifactTabs([
        event("artifact.created", 1, {
          artifact_id: "",
          kind: "code",
          revision: 0,
        }),
      ]),
    ).toEqual([]);
  });
});

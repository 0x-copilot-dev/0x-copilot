import { describe, expect, it } from "vitest";

import {
  isArtifactDetailResponse,
  isArtifactListResponse,
  isArtifactMutationResponse,
  isArtifactRevisionResponse,
} from "./artifacts";

const ARTIFACT_ID = "art_123e4567-e89b-42d3-a456-426614174000";

const revision = {
  artifact_id: ARTIFACT_ID,
  revision: 1,
  content_ref: `artifact://${ARTIFACT_ID}/revisions/1`,
  content_digest: "a".repeat(64),
  byte_size: 42,
  author: "user",
  created_at: "2026-07-24T00:00:00Z",
};

const detail = {
  artifact: {
    artifact_id: ARTIFACT_ID,
    org_id: "org_1",
    user_id: "user_1",
    conversation_id: "conv_1",
    run_id: "run_1",
    kind: "code",
    title: "demo.ts",
    media_type: "text/typescript",
    current_revision: 1,
    created_by: "user",
    created_at: "2026-07-24T00:00:00Z",
    updated_at: "2026-07-24T00:00:00Z",
  },
  current_revision: revision,
  suggested_filename: "demo.ts",
  range_supported: true,
};

describe("artifact HTTP guards", () => {
  it("accepts valid detail, revision, mutation, and list envelopes", () => {
    expect(
      isArtifactRevisionResponse({ revision, range_supported: true }),
    ).toBe(true);
    expect(isArtifactDetailResponse(detail)).toBe(true);
    expect(isArtifactMutationResponse({ ...detail, replayed: false })).toBe(
      true,
    );
    expect(
      isArtifactListResponse({
        artifacts: [detail],
        next_cursor: "cursor_2",
      }),
    ).toBe(true);
  });

  it("rejects internal storage fields even when every public field is valid", () => {
    expect(
      isArtifactDetailResponse({
        ...detail,
        blob_key: "a".repeat(64),
      }),
    ).toBe(false);
    expect(
      isArtifactDetailResponse({
        ...detail,
        current_revision: { ...revision, blob_key: "a".repeat(64) },
      }),
    ).toBe(false);
  });

  it("rejects mismatched revisions", () => {
    expect(
      isArtifactDetailResponse({
        ...detail,
        current_revision: { ...revision, revision: 2 },
      }),
    ).toBe(false);
  });

  it("rejects malformed ids, digests, and nullable optional wire fields", () => {
    expect(
      isArtifactDetailResponse({
        ...detail,
        artifact: { ...detail.artifact, artifact_id: "art_bad" },
      }),
    ).toBe(false);
    expect(
      isArtifactRevisionResponse({
        revision: { ...revision, content_digest: "not-a-digest" },
        range_supported: true,
      }),
    ).toBe(false);
    expect(isArtifactListResponse({ artifacts: [], next_cursor: null })).toBe(
      false,
    );
  });
});

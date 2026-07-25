// @vitest-environment node
import { describe, expect, it } from "vitest";

import { isSourceOpenResultV2 } from "./ledger";

const ARTIFACT_RESULT = {
  v: 2,
  source_id: "source:v2:004:artifact",
  kind: "artifact",
  disposition: "artifact",
  artifact_id: "art_safe_target",
  artifact_revision: 2,
  artifact_kind: "document",
};

describe("isSourceOpenResultV2", () => {
  it("accepts only a safe re-authorized artifact target", () => {
    expect(isSourceOpenResultV2(ARTIFACT_RESULT)).toBe(true);
    expect(
      isSourceOpenResultV2({
        v: 2,
        source_id: "source:v2:003:connector",
        kind: "connector",
        disposition: "unavailable",
        artifact_id: null,
        artifact_revision: null,
        artifact_kind: null,
      }),
    ).toBe(true);
  });

  it("fails closed for leaked, malformed, or inconsistent targets", () => {
    expect(
      isSourceOpenResultV2({
        ...ARTIFACT_RESULT,
        physical_path: "/Users/sarah/private.md",
      }),
    ).toBe(false);
    expect(
      isSourceOpenResultV2({
        ...ARTIFACT_RESULT,
        artifact_id: "file:///private/report.md",
      }),
    ).toBe(false);
    expect(
      isSourceOpenResultV2({
        ...ARTIFACT_RESULT,
        kind: "connector",
      }),
    ).toBe(false);
    expect(
      isSourceOpenResultV2({
        ...ARTIFACT_RESULT,
        disposition: "unavailable",
      }),
    ).toBe(false);
  });
});

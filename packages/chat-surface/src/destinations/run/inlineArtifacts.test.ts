import { describe, expect, it } from "vitest";

import type { ArtifactSurfaceTab } from "../../artifacts/artifactProjection";
import type { SurfaceHue } from "../../surfaces/surfaceHue";
import { artifactSubjectKey, buildInlineArtifacts } from "./inlineArtifacts";

function tab(over: Partial<ArtifactSurfaceTab> = {}): ArtifactSurfaceTab {
  return {
    artifactId: "art_1",
    kind: "dataset",
    revision: 1,
    uri: "artifact-dataset://art_1@1",
    title: "dataset artifact · r1",
    name: "dataset artifact",
    createdSeq: 10,
    lastSeq: 10,
    ...over,
  };
}

describe("buildInlineArtifacts", () => {
  // The Focus bug, in one assertion: the fold can only synthesize
  // "<kind> artifact" because `artifact.created` carries no title on the wire.
  it("prefers the canvas record's real title over the synthesized kind label", () => {
    const [entry] = buildInlineArtifacts(
      [tab()],
      new Map([["art_1", "bookings-forecast.csv"]]),
      new Map(),
    );
    expect(entry?.name).toBe("bookings-forecast.csv");
  });

  it("falls back to the fold's name when the record has no title", () => {
    const [entry] = buildInlineArtifacts([tab()], new Map(), new Map());
    expect(entry?.name).toBe("dataset artifact");
  });

  // The tab strip sorts newest-mutation-first. A transcript reads top-down, so
  // the two orders are opposites and the selector must not inherit one.
  it("orders by publication, oldest first — the opposite of the tab strip", () => {
    const entries = buildInlineArtifacts(
      [
        tab({ artifactId: "art_late", createdSeq: 90, lastSeq: 90 }),
        tab({ artifactId: "art_early", createdSeq: 12, lastSeq: 95 }),
      ],
      new Map(),
      new Map(),
    );
    expect(entries.map((e) => e.artifactId)).toEqual(["art_early", "art_late"]);
  });

  // A late revision must not move an artifact in the thread — `lastSeq` is
  // deliberately not consulted.
  it("anchors on createdSeq even when a later revision bumped lastSeq", () => {
    const [entry] = buildInlineArtifacts(
      [tab({ createdSeq: 10, lastSeq: 900 })],
      new Map(),
      new Map(),
    );
    expect(entry?.createdSeq).toBe(10);
  });

  it("carries the author's accent and omits the key when there is none", () => {
    const accent: SurfaceHue = "violet";
    const [withHue] = buildInlineArtifacts(
      [tab()],
      new Map(),
      new Map([["art_1", accent]]),
    );
    expect(withHue?.hue).toBe("violet");

    const [without] = buildInlineArtifacts([tab()], new Map(), new Map());
    expect(without && "hue" in without).toBe(false);
  });

  it("derives the subject key Studio opens the artifact by", () => {
    const [entry] = buildInlineArtifacts([tab()], new Map(), new Map());
    expect(entry?.subjectKey).toBe(artifactSubjectKey("art_1"));
  });
});

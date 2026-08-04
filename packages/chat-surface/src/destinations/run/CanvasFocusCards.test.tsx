import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { CanvasLifecycleProjection } from "./canvasLifecycle";
import { CanvasFocusCards } from "./CanvasFocusCards";

const terminalReceipt: CanvasLifecycleProjection = {
  lifecycle: "chat_only",
  tabs: [],
  activeSubjectKey: null,
  pendingSubjectKeys: [],
  terminalReceipt: {
    key: "receipt:run-1",
    kind: "receipt",
    subjectId: "run-1",
    title: "Run receipt",
    revision: null,
    lastSeq: 2,
    priority: 10,
    rendererHint: "receipt",
  },
  activityCount: 0,
  failure: null,
  hasFinalResponse: true,
  terminal: true,
  terminalStatus: "completed",
};

describe("CanvasFocusCards", () => {
  it("does not turn a terminal receipt into a Focus review card", () => {
    const { container } = render(
      <CanvasFocusCards projection={terminalReceipt} onOpenSubject={vi.fn()} />,
    );

    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByText("Run receipt")).toBeNull();
  });
});

describe("CanvasFocusCards — artifacts moved inline", () => {
  const withArtifact: CanvasLifecycleProjection = {
    ...terminalReceipt,
    lifecycle: "presenting",
    terminal: false,
    hasFinalResponse: false,
    terminalStatus: null,
    terminalReceipt: null,
    tabs: [
      {
        key: "artifact:art_1",
        kind: "artifact",
        subjectId: "art_1",
        title: "dataset artifact",
        revision: 1,
        lastSeq: 4,
        priority: 1,
        rendererHint: "dataset",
      },
      {
        key: "surface:surf_1",
        kind: "surface",
        subjectId: "surf_1",
        title: "Open issues",
        revision: null,
        lastSeq: 5,
        priority: 2,
        rendererHint: "table",
      },
    ],
  };

  // Artifacts render in the transcript now, expandable in place. A pinned card
  // could only say one existed and send the reader to Studio to find out what
  // was in it — and it named them by KIND, not by the filename the user chose.
  it("no longer renders a pinned card for an artifact", () => {
    render(
      <CanvasFocusCards projection={withArtifact} onOpenSubject={vi.fn()} />,
    );
    expect(screen.queryByText("dataset artifact")).toBeNull();
  });

  // Studio genuinely owns the other three kinds, so pointing at it is the
  // honest affordance for them rather than a detour.
  it("still renders the subject kinds Studio actually owns", () => {
    render(
      <CanvasFocusCards projection={withArtifact} onOpenSubject={vi.fn()} />,
    );
    expect(screen.getByText("Open issues")).toBeTruthy();
  });

  it("renders nothing when artifacts were the only subjects", () => {
    const { container } = render(
      <CanvasFocusCards
        projection={{ ...withArtifact, tabs: [withArtifact.tabs[0]!] }}
        onOpenSubject={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

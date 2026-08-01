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

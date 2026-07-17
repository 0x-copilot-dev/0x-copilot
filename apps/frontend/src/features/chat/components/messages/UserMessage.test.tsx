// PR 3.5 / G6 — minimal class/structure assertions for the user message.
//
// We don't snapshot the full rendered tree (parts walker output drifts
// across renders and would create brittle baselines). Instead we assert
// the structural class names that `apps/frontend/src/styles.css` keys
// off — that's the contract PR 2.3 laid down (right-aligned bubble,
// `aui-message--user`).

import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import type { ThreadMessageLike } from "../../runtime/types";

vi.mock("@0x-copilot/chat-surface", async () => ({
  ...(await vi.importActual<typeof import("@0x-copilot/chat-surface")>(
    "@0x-copilot/chat-surface",
  )),
  PlainText: () => <span data-testid="plain" />,
}));

vi.mock("../composer/AttachmentPill", () => ({
  AttachmentPill: () => null,
}));

import { UserMessage } from "./UserMessage";

const SAMPLE_MESSAGE: ThreadMessageLike = {
  role: "user",
  content: [{ type: "text", text: "hi there" }],
};

describe("UserMessage", () => {
  it("renders the right-aligned bubble class contract that styles.css keys off", () => {
    const { container } = render(<UserMessage message={SAMPLE_MESSAGE} />);
    const root = container.querySelector(".aui-message");
    // Both classes must be present: the generic `aui-message` (margins,
    // typography) and the user-specific `aui-message--user` (right-align,
    // soft surface bubble — see styles.css §"PR 2.3 thread polish").
    expect(root).not.toBeNull();
    expect(root!.className).toContain("aui-message--user");
  });

  it("renders message body slot for parts", () => {
    const { container } = render(<UserMessage message={SAMPLE_MESSAGE} />);
    const body = container.querySelector(".aui-message__body");
    expect(body).not.toBeNull();
  });
});

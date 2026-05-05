// PR 3.5 / G6 — minimal class/structure assertions for the user message.
//
// We don't snapshot the full rendered tree (assistant-ui's primitives
// render differently in different runtime contexts and would create
// brittle baselines). Instead we assert the structural class names that
// `apps/frontend/src/styles.css` keys off — that's the contract PR 2.3
// laid down (right-aligned bubble, `aui-message--user`).

import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import type { ComponentType, ReactNode } from "react";

vi.mock("@assistant-ui/react", () => ({
  MessagePrimitive: {
    Root: ({
      children,
      className,
    }: {
      children: ReactNode;
      className?: string;
    }) => (
      <div className={className} data-testid="message-root">
        {children}
      </div>
    ),
    Attachments: ({
      children,
    }: {
      children: (props: { attachment: unknown }) => ReactNode;
    }) => <>{children({ attachment: null })}</>,
    Parts: ({
      components: _components,
    }: {
      components: { Text: ComponentType };
    }) => <span data-testid="parts" />,
  },
}));

vi.mock("../markdown/PlainText", () => ({
  PlainText: () => <span data-testid="plain" />,
}));

vi.mock("../composer/AttachmentPill", () => ({
  AttachmentPill: () => null,
}));

import { UserMessage } from "./UserMessage";

describe("UserMessage", () => {
  it("renders the right-aligned bubble class contract that styles.css keys off", () => {
    const { getByTestId } = render(<UserMessage />);
    const root = getByTestId("message-root");
    // Both classes must be present: the generic `aui-message` (margins,
    // typography) and the user-specific `aui-message--user` (right-align,
    // soft surface bubble — see styles.css §"PR 2.3 thread polish").
    expect(root.className).toContain("aui-message");
    expect(root.className).toContain("aui-message--user");
  });

  it("renders message body slot for parts", () => {
    const { container } = render(<UserMessage />);
    const body = container.querySelector(".aui-message__body");
    expect(body).not.toBeNull();
  });
});

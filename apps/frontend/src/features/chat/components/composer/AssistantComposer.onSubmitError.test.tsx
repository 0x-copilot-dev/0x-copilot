// The web AssistantComposer adapter forwards the onSubmit error channel.
//
// The web adapter (this `AssistantComposer`) binds the substrate touchpoints
// (bridged attachment adapter, file picker, `+`-menu portal) and forwards the
// rest of the props to the shared `@0x-copilot/chat-surface` AssistantComposer
// via `{...rest}`. This test proves the `onSubmitError` channel survives that
// forwarding: a rejected async `onSubmit` reaches `onSubmitError` (with the
// error) through the whole web host stack, so a failed run-create is surfaced
// (ChatScreen turns it into a toast) instead of swallowed as an unhandled
// rejection.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AssistantComposer } from "./AssistantComposer";

function textarea(): HTMLTextAreaElement {
  return screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
}

function typeAndSend(text: string): void {
  fireEvent.change(textarea(), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: /Send message/i }));
}

describe("web AssistantComposer onSubmitError forwarding", () => {
  it("routes a rejected onSubmit to onSubmitError with the error", async () => {
    const boom = new Error("run-create failed");
    const onSubmit = vi.fn(() => Promise.reject(boom));
    const onSubmitError = vi.fn();
    render(
      <AssistantComposer
        connectors={{ servers: [], loading: false }}
        skills={{ skills: [], loading: false }}
        onOpenMcpSettings={vi.fn()}
        onOpenSkillsSettings={vi.fn()}
        onShowConnectors={vi.fn()}
        onSubmit={onSubmit}
        onSubmitError={onSubmitError}
      />,
    );

    typeAndSend("draft the launch note");

    await waitFor(() => expect(onSubmitError).toHaveBeenCalledTimes(1));
    expect(onSubmitError).toHaveBeenCalledWith(boom);
  });

  it("does not fire onSubmitError when onSubmit resolves", async () => {
    const onSubmit = vi.fn(() => Promise.resolve());
    const onSubmitError = vi.fn();
    render(
      <AssistantComposer
        connectors={{ servers: [], loading: false }}
        skills={{ skills: [], loading: false }}
        onOpenMcpSettings={vi.fn()}
        onOpenSkillsSettings={vi.fn()}
        onShowConnectors={vi.fn()}
        onSubmit={onSubmit}
        onSubmitError={onSubmitError}
      />,
    );

    typeAndSend("hello");

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(onSubmitError).not.toHaveBeenCalled();
  });
});

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRef, type ReactNode } from "react";

import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@enterprise-search/chat-transport";

import { TransportProvider } from "../providers/TransportProvider";
import { Composer, type ComposerHandle } from "./Composer";

function makeTransport(
  resolver: (req: TypedRequest) => Promise<unknown> = () =>
    Promise.resolve({ tools: [], candidates: [] }),
): Transport {
  return {
    request: <TRes,>(req: TypedRequest): Promise<TRes> =>
      resolver(req) as Promise<TRes>,
    subscribeServerSentEvents: (
      _opts: SseSubscribeOptions,
    ): SseSubscription => ({
      close: () => {},
    }),
    getSession: (): Session => ({ bearer: null }),
    capabilities: (): TransportCapabilities => ({
      substrate: "web",
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
  };
}

function withTransport(transport: Transport, children: ReactNode): ReactNode {
  return (
    <TransportProvider transport={transport}>{children}</TransportProvider>
  );
}

describe("Composer", () => {
  it("renders an empty textarea and a disabled Send button by default", () => {
    render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    expect(ta.value).toBe("");
    expect(screen.getByTestId("composer-send")).toBeDisabled();
  });

  it("enables Send once non-whitespace text is entered", () => {
    render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "hi" } });
    expect(screen.getByTestId("composer-send")).not.toBeDisabled();
  });

  it("calls onSend with trimmed text and clears the field on Enter", () => {
    const onSend = vi.fn();
    render(withTransport(makeTransport(), <Composer onSend={onSend} />));
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "  hello world  " } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("hello world");
    expect(ta.value).toBe("");
  });

  it("does not send on Shift+Enter", () => {
    const onSend = vi.fn();
    render(withTransport(makeTransport(), <Composer onSend={onSend} />));
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "line1" } });
    fireEvent.keyDown(ta, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("does not send when text is only whitespace", () => {
    const onSend = vi.fn();
    render(withTransport(makeTransport(), <Composer onSend={onSend} />));
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "   \n  " } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("does nothing when disabled", () => {
    const onSend = vi.fn();
    render(
      withTransport(
        makeTransport(),
        <Composer onSend={onSend} disabled={true} />,
      ),
    );
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    expect(ta).toBeDisabled();
    expect(screen.getByTestId("composer-send")).toBeDisabled();
  });

  it("toggles the ToolPicker open and closed", async () => {
    render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
    expect(screen.queryByTestId("tool-picker")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("composer-tools-toggle"));
    await waitFor(() => {
      expect(screen.getByTestId("tool-picker")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("composer-tools-toggle"));
    expect(screen.queryByTestId("tool-picker")).not.toBeInTheDocument();
  });

  it("toggles the ModelPicker open and closed", () => {
    render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
    fireEvent.click(screen.getByTestId("composer-model-toggle"));
    expect(screen.getByTestId("model-picker")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("composer-model-toggle"));
    expect(screen.queryByTestId("model-picker")).not.toBeInTheDocument();
  });

  it("updates the model toggle label when a model is selected", () => {
    render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
    fireEvent.click(screen.getByTestId("composer-model-toggle"));
    fireEvent.click(screen.getByTestId("model-picker-row-claude-haiku-4-5"));
    expect(screen.getByTestId("composer-model-toggle")).toHaveTextContent(
      "Haiku 4.5",
    );
  });

  it("opens the MentionPopover when '@' is typed at a word boundary", async () => {
    const transport = makeTransport(() =>
      Promise.resolve({
        candidates: [{ slug: "tim", label: "Tim", kind: "skill" }],
      }),
    );
    render(withTransport(transport, <Composer onSend={() => {}} />));
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "hello @t" } });
    await waitFor(() => {
      expect(screen.getByTestId("mention-popover")).toBeInTheDocument();
    });
  });

  it("does not open the MentionPopover for '@' inside a word (e.g. email)", () => {
    render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "name@example" } });
    expect(screen.queryByTestId("mention-popover")).not.toBeInTheDocument();
  });

  it("inserts the selected mention as @{slug} and closes the popover", async () => {
    const transport = makeTransport(() =>
      Promise.resolve({
        candidates: [{ slug: "tim", label: "Tim", kind: "skill" }],
      }),
    );
    render(withTransport(transport, <Composer onSend={() => {}} />));
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "hello @t" } });
    const row = await screen.findByTestId("mention-row-tim");
    fireEvent.click(row);
    expect(ta.value).toBe("hello @tim ");
    expect(screen.queryByTestId("mention-popover")).not.toBeInTheDocument();
  });

  it("closes pickers on Escape", () => {
    render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
    fireEvent.click(screen.getByTestId("composer-model-toggle"));
    expect(screen.getByTestId("model-picker")).toBeInTheDocument();
    const ta = screen.getByTestId("composer-textarea");
    fireEvent.keyDown(ta, { key: "Escape" });
    expect(screen.queryByTestId("model-picker")).not.toBeInTheDocument();
  });

  /* Locks in the apps/frontend/CLAUDE.md invariant: the kbd hint strip
   * is stateless info and must render whether or not a run is active.
   * Gating on `running` was a real shipped regression. */
  it("always renders the hint row, even while a run is active", () => {
    const { rerender } = render(
      withTransport(makeTransport(), <Composer onSend={() => {}} />),
    );
    expect(screen.getByTestId("composer-hint")).toBeInTheDocument();
    rerender(
      withTransport(
        makeTransport(),
        <Composer onSend={() => {}} running={true} />,
      ),
    );
    expect(screen.getByTestId("composer-hint")).toBeInTheDocument();
    expect(screen.getByTestId("composer-hint")).toHaveTextContent(/send/);
    expect(screen.getByTestId("composer-hint")).toHaveTextContent(/new line/);
    expect(screen.getByTestId("composer-hint")).toHaveTextContent(/skills/);
    expect(screen.getByTestId("composer-hint")).toHaveTextContent(
      /Sources cited inline/,
    );
  });

  it("opens only one popover at a time when Tools and Model are toggled", () => {
    render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
    fireEvent.click(screen.getByTestId("composer-tools-toggle"));
    expect(screen.getByTestId("tool-picker")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("composer-model-toggle"));
    expect(screen.queryByTestId("tool-picker")).not.toBeInTheDocument();
    expect(screen.getByTestId("model-picker")).toBeInTheDocument();
  });

  it("renders attach, mic, and send icon buttons in the thin action row", () => {
    render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
    expect(screen.getByTestId("composer-attach")).toBeInTheDocument();
    expect(screen.getByTestId("composer-mic")).toBeInTheDocument();
    expect(screen.getByTestId("composer-send")).toBeInTheDocument();
    expect(screen.getByTestId("composer-send")).toHaveAttribute(
      "aria-label",
      "Send",
    );
  });

  it("swaps Send for a Cancel button while a run is active and calls onCancel", () => {
    const onCancel = vi.fn();
    render(
      withTransport(
        makeTransport(),
        <Composer onSend={() => {}} running={true} onCancel={onCancel} />,
      ),
    );
    expect(screen.queryByTestId("composer-send")).not.toBeInTheDocument();
    const cancel = screen.getByTestId("composer-cancel");
    fireEvent.click(cancel);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("blocks Enter-send while running", () => {
    const onSend = vi.fn();
    render(
      withTransport(
        makeTransport(),
        <Composer onSend={onSend} running={true} />,
      ),
    );
    const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "hello" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("renders the Tools button without count by default and with count when tools selected", () => {
    render(
      withTransport(
        makeTransport(),
        <Composer
          onSend={() => {}}
          initialTools={["gmail.draft.create", "sheets.cell.set"]}
        />,
      ),
    );
    expect(screen.getByTestId("composer-tools-toggle")).toHaveTextContent(
      "Tools · 2",
    );
  });

  it("combines model + depth label on the model toggle", () => {
    render(
      withTransport(
        makeTransport(),
        <Composer onSend={() => {}} initialDepth="fast" />,
      ),
    );
    /* Substring match — the toggle reads "<Model> · <Depth>". */
    expect(screen.getByTestId("composer-model-toggle")).toHaveTextContent(
      /Opus 4.7/,
    );
    expect(screen.getByTestId("composer-model-toggle")).toHaveTextContent(
      /Fast/,
    );
  });

  it("updates the depth on the toggle when a depth chip is selected", () => {
    render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
    fireEvent.click(screen.getByTestId("composer-model-toggle"));
    fireEvent.click(screen.getByTestId("depth-picker-row-deep"));
    expect(screen.getByTestId("composer-model-toggle")).toHaveTextContent(
      /Deep/,
    );
  });

  /* --- Phase 1 P1-B extras --- */

  describe("forwardRef + ComposerHandle", () => {
    it("exposes setText / getText / clear / focus via the forwarded ref", () => {
      const ref = createRef<ComposerHandle>();
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={() => {}} ref={ref} />,
        ),
      );
      expect(ref.current).not.toBeNull();
      ref.current?.setText("hello world");
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      expect(ta.value).toBe("hello world");
      expect(ref.current?.getText()).toBe("hello world");
      ref.current?.clear();
      expect(ta.value).toBe("");
    });

    it("focus() moves keyboard focus to the textarea", () => {
      const ref = createRef<ComposerHandle>();
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={() => {}} ref={ref} />,
        ),
      );
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      expect(document.activeElement).not.toBe(ta);
      ref.current?.focus();
      expect(document.activeElement).toBe(ta);
    });
  });

  describe("topBarSlot + inlineActions", () => {
    it("renders host-supplied topBarSlot above the textarea", () => {
      render(
        withTransport(
          makeTransport(),
          <Composer
            onSend={() => {}}
            topBarSlot={<div data-testid="host-topbar">pills</div>}
          />,
        ),
      );
      expect(screen.getByTestId("composer-topbar-slot")).toBeInTheDocument();
      expect(screen.getByTestId("host-topbar")).toBeInTheDocument();
    });

    it("renders host-supplied inlineActions between attach and Tools", () => {
      render(
        withTransport(
          makeTransport(),
          <Composer
            onSend={() => {}}
            inlineActions={
              <button type="button" data-testid="host-connectors">
                connectors
              </button>
            }
          />,
        ),
      );
      expect(screen.getByTestId("composer-inline-actions")).toBeInTheDocument();
      expect(screen.getByTestId("host-connectors")).toBeInTheDocument();
    });

    it("does not render either slot when the prop is absent", () => {
      render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
      expect(
        screen.queryByTestId("composer-topbar-slot"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByTestId("composer-inline-actions"),
      ).not.toBeInTheDocument();
    });
  });

  describe('mode="edit"', () => {
    it("hides Tools / Model / attach / mic and shows Save + Cancel", () => {
      render(
        withTransport(
          makeTransport(),
          <Composer
            onSend={() => {}}
            mode="edit"
            initialText="prior"
            onSave={() => {}}
            onCancel={() => {}}
          />,
        ),
      );
      expect(
        screen.queryByTestId("composer-tools-toggle"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByTestId("composer-model-toggle"),
      ).not.toBeInTheDocument();
      expect(screen.queryByTestId("composer-attach")).not.toBeInTheDocument();
      expect(screen.queryByTestId("composer-mic")).not.toBeInTheDocument();
      expect(screen.getByTestId("composer-edit-save")).toBeInTheDocument();
      expect(screen.getByTestId("composer-edit-cancel")).toBeInTheDocument();
    });

    it("pre-fills the textarea with initialText", () => {
      render(
        withTransport(
          makeTransport(),
          <Composer
            onSend={() => {}}
            mode="edit"
            initialText="prior text"
            onSave={() => {}}
          />,
        ),
      );
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      expect(ta.value).toBe("prior text");
    });

    it("calls onSave when Save is clicked, not onSend", () => {
      const onSend = vi.fn();
      const onSave = vi.fn();
      render(
        withTransport(
          makeTransport(),
          <Composer
            onSend={onSend}
            mode="edit"
            initialText="hello"
            onSave={onSave}
            onCancel={() => {}}
          />,
        ),
      );
      fireEvent.click(screen.getByTestId("composer-edit-save"));
      expect(onSave).toHaveBeenCalledWith("hello");
      expect(onSend).not.toHaveBeenCalled();
    });

    it("calls onCancel when Cancel is clicked or Escape pressed", () => {
      const onCancel = vi.fn();
      render(
        withTransport(
          makeTransport(),
          <Composer
            onSend={() => {}}
            mode="edit"
            initialText="hi"
            onSave={() => {}}
            onCancel={onCancel}
          />,
        ),
      );
      fireEvent.click(screen.getByTestId("composer-edit-cancel"));
      expect(onCancel).toHaveBeenCalledTimes(1);
      fireEvent.keyDown(screen.getByTestId("composer-textarea"), {
        key: "Escape",
      });
      expect(onCancel).toHaveBeenCalledTimes(2);
    });

    it("retains the hint row in edit mode but flips the send label to save", () => {
      render(
        withTransport(
          makeTransport(),
          <Composer
            onSend={() => {}}
            mode="edit"
            initialText="x"
            onSave={() => {}}
          />,
        ),
      );
      const hint = screen.getByTestId("composer-hint");
      expect(hint).toBeInTheDocument();
      expect(hint).toHaveTextContent(/save/);
    });
  });

  describe("onSkillCommand (/ skills)", () => {
    it("emits onSkillCommand when input starts with /slug and Enter is pressed", () => {
      const onSkillCommand = vi.fn();
      const onSend = vi.fn();
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={onSend} onSkillCommand={onSkillCommand} />,
        ),
      );
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      fireEvent.change(ta, { target: { value: "/summarize today" } });
      fireEvent.keyDown(ta, { key: "Enter" });
      expect(onSkillCommand).toHaveBeenCalledWith("summarize", "today");
      expect(onSend).not.toHaveBeenCalled();
      expect(ta.value).toBe("");
    });

    it("passes empty args when only the slash + slug is typed", () => {
      const onSkillCommand = vi.fn();
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={() => {}} onSkillCommand={onSkillCommand} />,
        ),
      );
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      fireEvent.change(ta, { target: { value: "/clear" } });
      fireEvent.keyDown(ta, { key: "Enter" });
      expect(onSkillCommand).toHaveBeenCalledWith("clear", "");
    });

    it("does not treat '/foo' inside the middle of a message as a skill command", () => {
      const onSkillCommand = vi.fn();
      const onSend = vi.fn();
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={onSend} onSkillCommand={onSkillCommand} />,
        ),
      );
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      fireEvent.change(ta, {
        target: { value: "check https://example.com/path now" },
      });
      fireEvent.keyDown(ta, { key: "Enter" });
      expect(onSkillCommand).not.toHaveBeenCalled();
      expect(onSend).toHaveBeenCalledWith("check https://example.com/path now");
    });

    it("falls through to onSend when onSkillCommand is not wired", () => {
      const onSend = vi.fn();
      render(withTransport(makeTransport(), <Composer onSend={onSend} />));
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      fireEvent.change(ta, { target: { value: "/summarize today" } });
      fireEvent.keyDown(ta, { key: "Enter" });
      expect(onSend).toHaveBeenCalledWith("/summarize today");
    });
  });
});

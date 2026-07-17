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
} from "@0x-copilot/chat-transport";

import { TransportProvider } from "../providers/TransportProvider";
import {
  Composer,
  type AttachmentAdapter,
  type CompleteAttachment,
  type ComposerHandle,
  type PendingAttachment,
} from "./Composer";

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
      /* Member access on `globalThis.document` is the chat-surface
       * convention for honest substrate touchpoints — see eslint.config.js
       * BOUNDARY_MESSAGE_GLOBALS for the rule rationale. */
      expect(globalThis.document.activeElement).not.toBe(ta);
      ref.current?.focus();
      expect(globalThis.document.activeElement).toBe(ta);
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

  /* --- Phase 1 P1-B1 Delta 2: attachmentAdapter + drag-and-drop --- */

  describe("attachmentAdapter + drag-and-drop", () => {
    /** Build a fake File without depending on jsdom's File constructor
     * intricacies — use Blob + name shim that satisfies File-shape uses
     * (the Composer only reads .name, .size, .type via the adapter). */
    function makeFile(
      name: string,
      content: string,
      type: string = "text/plain",
    ): File {
      return new File([content], name, { type });
    }

    function makeAdapter(): {
      adapter: AttachmentAdapter;
      added: File[];
      removed: string[];
    } {
      const added: File[] = [];
      const removed: string[] = [];
      let n = 0;
      const adapter: AttachmentAdapter = {
        async add(file: File): Promise<CompleteAttachment> {
          added.push(file);
          n += 1;
          return {
            id: `att-${n}`,
            name: file.name,
            size: file.size,
            type: file.type,
          };
        },
        remove(id: string): void {
          removed.push(id);
        },
      };
      return { adapter, added, removed };
    }

    function dropFiles(target: Element, files: File[]): void {
      /* jsdom doesn't synthesize FileList; build a minimal duck-typed
       * stand-in that the Composer only ever reads via .length / .item(i). */
      const fileList: Record<string | number, unknown> = {
        length: files.length,
        item: (i: number): File | null => files[i] ?? null,
      };
      files.forEach((f, i) => {
        fileList[i] = f;
      });
      const dataTransfer = {
        files: fileList as unknown as FileList,
        types: ["Files"],
      };
      fireEvent.dragEnter(target, { dataTransfer });
      fireEvent.dragOver(target, { dataTransfer });
      fireEvent.drop(target, { dataTransfer });
    }

    it("renders a pill for each dropped file via the adapter", async () => {
      const { adapter, added } = makeAdapter();
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={() => {}} attachmentAdapter={adapter} />,
        ),
      );
      const composer = screen.getByTestId("composer");
      const file = makeFile("notes.txt", "hello attachments");
      dropFiles(composer, [file]);
      expect(added).toHaveLength(1);
      const pill = await screen.findByTestId("composer-attachment-att-1");
      expect(pill).toHaveTextContent(/notes\.txt/);
    });

    it("removing a pill removes it from the DOM and calls adapter.remove", async () => {
      const { adapter, removed } = makeAdapter();
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={() => {}} attachmentAdapter={adapter} />,
        ),
      );
      const composer = screen.getByTestId("composer");
      dropFiles(composer, [makeFile("a.txt", "x")]);
      const pill = await screen.findByTestId("composer-attachment-att-1");
      const removeBtn = screen.getByTestId("composer-attachment-remove-att-1");
      fireEvent.click(removeBtn);
      expect(pill).not.toBeInTheDocument();
      expect(removed).toEqual(["att-1"]);
    });

    it("is a no-op (and does not throw) when no adapter is wired", () => {
      render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
      const composer = screen.getByTestId("composer");
      /* Should not throw, and no attachment strip should appear. */
      dropFiles(composer, [makeFile("ignored.txt", "x")]);
      expect(
        screen.queryByTestId("composer-attachments"),
      ).not.toBeInTheDocument();
    });

    it("appends multiple drops cumulatively", async () => {
      const { adapter } = makeAdapter();
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={() => {}} attachmentAdapter={adapter} />,
        ),
      );
      const composer = screen.getByTestId("composer");
      dropFiles(composer, [makeFile("a.txt", "x")]);
      await screen.findByTestId("composer-attachment-att-1");
      dropFiles(composer, [makeFile("b.txt", "yy")]);
      await screen.findByTestId("composer-attachment-att-2");
      expect(
        screen.getByTestId("composer-attachment-att-1"),
      ).toBeInTheDocument();
      expect(
        screen.getByTestId("composer-attachment-att-2"),
      ).toBeInTheDocument();
    });

    it("does not render the attachment strip when there are no attachments", () => {
      const { adapter } = makeAdapter();
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={() => {}} attachmentAdapter={adapter} />,
        ),
      );
      expect(
        screen.queryByTestId("composer-attachments"),
      ).not.toBeInTheDocument();
    });
  });

  /* --- Phase 1 P1-B1 Delta 3: onSubmit (payload form) + onSend backward compat --- */

  describe("onSubmit (payload form) + onSend backward compat", () => {
    it("fires onSubmit with {text, attachments} when wired", async () => {
      const onSubmit = vi.fn();
      const adapter: AttachmentAdapter = {
        async add(file: File): Promise<CompleteAttachment> {
          return {
            id: "x1",
            name: file.name,
            size: file.size,
            type: file.type,
          };
        },
        remove(): void {},
      };
      render(
        withTransport(
          makeTransport(),
          <Composer onSubmit={onSubmit} attachmentAdapter={adapter} />,
        ),
      );
      const composer = screen.getByTestId("composer");
      const file = new File(["abc"], "draft.md", { type: "text/markdown" });
      fireEvent.dragEnter(composer, {
        dataTransfer: {
          files: {
            length: 1,
            item: (i: number) => (i === 0 ? file : null),
            0: file,
          },
          types: ["Files"],
        },
      });
      fireEvent.drop(composer, {
        dataTransfer: {
          files: {
            length: 1,
            item: (i: number) => (i === 0 ? file : null),
            0: file,
          },
          types: ["Files"],
        },
      });
      await screen.findByTestId("composer-attachment-x1");
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      fireEvent.change(ta, { target: { value: "ship it" } });
      fireEvent.keyDown(ta, { key: "Enter" });
      expect(onSubmit).toHaveBeenCalledTimes(1);
      const payload = onSubmit.mock.calls[0][0];
      expect(payload.text).toBe("ship it");
      expect(payload.attachments).toHaveLength(1);
      expect(payload.attachments[0].id).toBe("x1");
      expect(payload.attachments[0].name).toBe("draft.md");
    });

    it("fires onSubmit with empty attachments when no adapter is wired", () => {
      const onSubmit = vi.fn();
      render(withTransport(makeTransport(), <Composer onSubmit={onSubmit} />));
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      fireEvent.change(ta, { target: { value: "hi" } });
      fireEvent.keyDown(ta, { key: "Enter" });
      expect(onSubmit).toHaveBeenCalledWith({ text: "hi", attachments: [] });
    });

    it("falls back to onSend when only onSend is supplied (existing call sites)", () => {
      const onSend = vi.fn();
      render(withTransport(makeTransport(), <Composer onSend={onSend} />));
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      fireEvent.change(ta, { target: { value: "legacy path" } });
      fireEvent.keyDown(ta, { key: "Enter" });
      expect(onSend).toHaveBeenCalledWith("legacy path");
    });

    it("prefers onSubmit when both onSubmit and onSend are supplied", () => {
      const onSend = vi.fn();
      const onSubmit = vi.fn();
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={onSend} onSubmit={onSubmit} />,
        ),
      );
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      fireEvent.change(ta, { target: { value: "both" } });
      fireEvent.keyDown(ta, { key: "Enter" });
      expect(onSubmit).toHaveBeenCalledTimes(1);
      expect(onSend).not.toHaveBeenCalled();
    });

    it("clears attachments on submit", async () => {
      const onSubmit = vi.fn();
      const adapter: AttachmentAdapter = {
        async add(file: File): Promise<CompleteAttachment> {
          return {
            id: "z1",
            name: file.name,
            size: file.size,
            type: file.type,
          };
        },
        remove(): void {},
      };
      render(
        withTransport(
          makeTransport(),
          <Composer onSubmit={onSubmit} attachmentAdapter={adapter} />,
        ),
      );
      const composer = screen.getByTestId("composer");
      const file = new File(["x"], "f.txt", { type: "text/plain" });
      fireEvent.drop(composer, {
        dataTransfer: {
          files: {
            length: 1,
            item: (i: number) => (i === 0 ? file : null),
            0: file,
          },
          types: ["Files"],
        },
      });
      await screen.findByTestId("composer-attachment-z1");
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      fireEvent.change(ta, { target: { value: "send" } });
      fireEvent.keyDown(ta, { key: "Enter" });
      await waitFor(() =>
        expect(
          screen.queryByTestId("composer-attachment-z1"),
        ).not.toBeInTheDocument(),
      );
    });

    it("permits send with attachments only (no text) via onSubmit", async () => {
      const onSubmit = vi.fn();
      const adapter: AttachmentAdapter = {
        async add(file: File): Promise<CompleteAttachment> {
          return {
            id: "only-att",
            name: file.name,
            size: file.size,
            type: file.type,
          };
        },
        remove(): void {},
      };
      render(
        withTransport(
          makeTransport(),
          <Composer onSubmit={onSubmit} attachmentAdapter={adapter} />,
        ),
      );
      const composer = screen.getByTestId("composer");
      const file = new File(["x"], "only.txt", { type: "text/plain" });
      fireEvent.drop(composer, {
        dataTransfer: {
          files: {
            length: 1,
            item: (i: number) => (i === 0 ? file : null),
            0: file,
          },
          types: ["Files"],
        },
      });
      await screen.findByTestId("composer-attachment-only-att");
      /* Send button should be enabled because attachments are present. */
      const send = screen.getByTestId("composer-send");
      expect(send).not.toBeDisabled();
      fireEvent.click(send);
      expect(onSubmit).toHaveBeenCalledTimes(1);
      const payload = onSubmit.mock.calls[0][0];
      expect(payload.text).toBe("");
      expect(payload.attachments).toHaveLength(1);
    });
  });

  /* --- Phase 1.6 Gap 1: ComposerHandle.appendText / addAttachment / submit --- */

  describe("ComposerHandle extras (appendText / addAttachment / submit)", () => {
    it("appendText inserts at the caret when the textarea is focused", () => {
      const ref = createRef<ComposerHandle>();
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={() => {}} ref={ref} />,
        ),
      );
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      ref.current?.setText("hello world");
      ta.focus();
      ta.setSelectionRange(5, 5);
      ref.current?.appendText("!");
      /* flushSync makes the DOM value visible without awaiting a render. */
      expect(ta.value).toBe("hello! world");
    });

    it("appendText appends with a newline when the textarea is not focused", () => {
      const ref = createRef<ComposerHandle>();
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={() => {}} ref={ref} />,
        ),
      );
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      ref.current?.setText("first line");
      /* No focus → the runtime-composer behaviour of "append on a new
       * line" kicks in. */
      ref.current?.appendText("second line");
      expect(ta.value).toBe("first line\nsecond line");
    });

    it("appendText writes the input directly when the buffer is empty", () => {
      const ref = createRef<ComposerHandle>();
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={() => {}} ref={ref} />,
        ),
      );
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      ref.current?.appendText("kickoff");
      expect(ta.value).toBe("kickoff");
    });

    it("addAttachment routes through the adapter and renders a pill", async () => {
      const ref = createRef<ComposerHandle>();
      const added: File[] = [];
      const adapter: AttachmentAdapter = {
        async add(file: File): Promise<CompleteAttachment> {
          added.push(file);
          return {
            id: "h-1",
            name: file.name,
            size: file.size,
            type: file.type,
          };
        },
        remove(): void {},
      };
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={() => {}} ref={ref} attachmentAdapter={adapter} />,
        ),
      );
      const file = new File(["x"], "from-handle.txt", { type: "text/plain" });
      await ref.current?.addAttachment(file);
      expect(added).toHaveLength(1);
      expect(
        await screen.findByTestId("composer-attachment-h-1"),
      ).toBeInTheDocument();
    });

    it("addAttachment is a no-op when no adapter is wired", async () => {
      const ref = createRef<ComposerHandle>();
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={() => {}} ref={ref} />,
        ),
      );
      const file = new File(["x"], "ignored.txt", { type: "text/plain" });
      /* Should resolve cleanly and not render a pill. */
      await ref.current?.addAttachment(file);
      expect(
        screen.queryByTestId("composer-attachments"),
      ).not.toBeInTheDocument();
    });

    it("submit() drives the same path as Enter (onSubmit + clear)", () => {
      const ref = createRef<ComposerHandle>();
      const onSubmit = vi.fn();
      render(
        withTransport(
          makeTransport(),
          <Composer onSubmit={onSubmit} ref={ref} />,
        ),
      );
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      ref.current?.setText("ship it");
      ref.current?.submit();
      expect(onSubmit).toHaveBeenCalledTimes(1);
      expect(onSubmit.mock.calls[0][0].text).toBe("ship it");
      expect(ta.value).toBe("");
    });

    it("submit() honours disabled / running guards", () => {
      const ref = createRef<ComposerHandle>();
      const onSubmit = vi.fn();
      const { rerender } = render(
        withTransport(
          makeTransport(),
          <Composer onSubmit={onSubmit} ref={ref} disabled={true} />,
        ),
      );
      ref.current?.setText("blocked");
      ref.current?.submit();
      expect(onSubmit).not.toHaveBeenCalled();
      rerender(
        withTransport(
          makeTransport(),
          <Composer onSubmit={onSubmit} ref={ref} running={true} />,
        ),
      );
      ref.current?.submit();
      expect(onSubmit).not.toHaveBeenCalled();
    });

    it("submit() no-ops with no text and no attachments", () => {
      const ref = createRef<ComposerHandle>();
      const onSubmit = vi.fn();
      render(
        withTransport(
          makeTransport(),
          <Composer onSubmit={onSubmit} ref={ref} />,
        ),
      );
      ref.current?.submit();
      expect(onSubmit).not.toHaveBeenCalled();
    });
  });

  /* --- Phase 1.6 Gap 2: bottomBarRender / hintRender / onInputKeyDown --- */

  describe("bottomBarRender / hintRender / onInputKeyDown slots", () => {
    it("bottomBarRender REPLACES the built-in toolbar when provided", () => {
      render(
        withTransport(
          makeTransport(),
          <Composer
            onSend={() => {}}
            bottomBarRender={() => (
              <div data-testid="host-bottombar">host actions</div>
            )}
          />,
        ),
      );
      expect(screen.getByTestId("composer-bottombar-slot")).toBeInTheDocument();
      expect(screen.getByTestId("host-bottombar")).toBeInTheDocument();
      /* Built-in toolbar buttons must not render alongside the slot. */
      expect(
        screen.queryByTestId("composer-tools-toggle"),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByTestId("composer-model-toggle"),
      ).not.toBeInTheDocument();
      expect(screen.queryByTestId("composer-send")).not.toBeInTheDocument();
      expect(screen.queryByTestId("composer-mic")).not.toBeInTheDocument();
      expect(screen.queryByTestId("composer-attach")).not.toBeInTheDocument();
    });

    it("bottomBarRender receives a live ComposerSlotCtx", () => {
      const ctxSeen: {
        text: string;
        running: boolean;
        attachmentsCount: number;
      }[] = [];
      render(
        withTransport(
          makeTransport(),
          <Composer
            onSend={() => {}}
            running={true}
            bottomBarRender={(ctx) => {
              ctxSeen.push({
                text: ctx.text,
                running: ctx.running,
                attachmentsCount: ctx.attachmentsCount,
              });
              return <div data-testid="host-bottombar" />;
            }}
          />,
        ),
      );
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      fireEvent.change(ta, { target: { value: "hi" } });
      /* Last render's ctx should reflect the typed text + running flag. */
      const last = ctxSeen[ctxSeen.length - 1];
      expect(last.text).toBe("hi");
      expect(last.running).toBe(true);
      expect(last.attachmentsCount).toBe(0);
    });

    it("hintRender REPLACES the built-in hint row when provided", () => {
      render(
        withTransport(
          makeTransport(),
          <Composer
            onSend={() => {}}
            hintRender={() => <div data-testid="host-hint">custom hint</div>}
          />,
        ),
      );
      expect(screen.getByTestId("composer-hint-slot")).toBeInTheDocument();
      expect(screen.getByTestId("host-hint")).toBeInTheDocument();
      /* Built-in hint row must not render alongside. */
      expect(screen.queryByTestId("composer-hint")).not.toBeInTheDocument();
    });

    it("onInputKeyDown intercepts keys and prevents default handling when it returns true", () => {
      const onSend = vi.fn();
      const onInputKeyDown = vi.fn((e: KeyboardEvent) => {
        if (e.key === "Enter") return true;
        return false;
      });
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={onSend} onInputKeyDown={onInputKeyDown as never} />,
        ),
      );
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      fireEvent.change(ta, { target: { value: "hi" } });
      fireEvent.keyDown(ta, { key: "Enter" });
      expect(onInputKeyDown).toHaveBeenCalled();
      /* Composer's default send did not run because the interceptor
       * returned true. */
      expect(onSend).not.toHaveBeenCalled();
    });

    it("onInputKeyDown passes through when it returns undefined / false", () => {
      const onSend = vi.fn();
      const onInputKeyDown = vi.fn(() => false);
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={onSend} onInputKeyDown={onInputKeyDown as never} />,
        ),
      );
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      fireEvent.change(ta, { target: { value: "hi" } });
      fireEvent.keyDown(ta, { key: "Enter" });
      expect(onInputKeyDown).toHaveBeenCalled();
      expect(onSend).toHaveBeenCalledWith("hi");
    });
  });

  /* --- Phase 1.6 Gap 3: two-stage AttachmentAdapter --- */

  describe("AttachmentAdapter two-stage (add → pending, send → complete)", () => {
    it("renders a pill for a PendingAttachment returned by add()", async () => {
      const adapter: AttachmentAdapter = {
        async add(file: File): Promise<PendingAttachment> {
          return {
            id: "p-1",
            name: file.name,
            size: file.size,
            type: file.type,
            status: { type: "pending" },
          };
        },
        async send(pending) {
          return {
            id: pending.id,
            name: pending.name,
            size: pending.size,
            type: pending.type,
            content: [{ type: "file", filename: pending.name }],
          };
        },
        async remove(): Promise<void> {},
      };
      render(
        withTransport(
          makeTransport(),
          <Composer onSubmit={() => {}} attachmentAdapter={adapter} />,
        ),
      );
      const composer = screen.getByTestId("composer");
      const file = new File(["x"], "pending.txt", { type: "text/plain" });
      fireEvent.drop(composer, {
        dataTransfer: {
          files: {
            length: 1,
            item: (i: number) => (i === 0 ? file : null),
            0: file,
          },
          types: ["Files"],
        },
      });
      expect(
        await screen.findByTestId("composer-attachment-p-1"),
      ).toBeInTheDocument();
    });

    it("calls send() on submit to upgrade pending → complete and ships the content", async () => {
      const onSubmit = vi.fn();
      const sent: PendingAttachment[] = [];
      const adapter: AttachmentAdapter = {
        async add(file: File): Promise<PendingAttachment> {
          return {
            id: "p-2",
            name: file.name,
            size: file.size,
            type: file.type,
            status: { type: "pending" },
          };
        },
        async send(pending: PendingAttachment): Promise<CompleteAttachment> {
          sent.push(pending);
          return {
            id: pending.id,
            name: pending.name,
            size: pending.size,
            type: pending.type,
            content: [
              { type: "file", filename: pending.name, data: "blob:abc" },
            ],
          };
        },
        async remove(): Promise<void> {},
      };
      render(
        withTransport(
          makeTransport(),
          <Composer onSubmit={onSubmit} attachmentAdapter={adapter} />,
        ),
      );
      const composer = screen.getByTestId("composer");
      const file = new File(["x"], "doc.txt", { type: "text/plain" });
      fireEvent.drop(composer, {
        dataTransfer: {
          files: {
            length: 1,
            item: (i: number) => (i === 0 ? file : null),
            0: file,
          },
          types: ["Files"],
        },
      });
      await screen.findByTestId("composer-attachment-p-2");
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      fireEvent.change(ta, { target: { value: "send it" } });
      fireEvent.keyDown(ta, { key: "Enter" });
      await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
      expect(sent).toHaveLength(1);
      const payload = onSubmit.mock.calls[0][0];
      expect(payload.text).toBe("send it");
      expect(payload.attachments).toHaveLength(1);
      expect(payload.attachments[0].id).toBe("p-2");
      expect(payload.attachments[0].content).toEqual([
        { type: "file", filename: "doc.txt", data: "blob:abc" },
      ]);
    });

    it("accepts Promise<void> from remove() (runtime adapter shape)", async () => {
      const removed: string[] = [];
      const adapter: AttachmentAdapter = {
        async add(file: File): Promise<CompleteAttachment> {
          return {
            id: "rm-1",
            name: file.name,
            size: file.size,
            type: file.type,
          };
        },
        async remove(id: string): Promise<void> {
          removed.push(id);
        },
      };
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={() => {}} attachmentAdapter={adapter} />,
        ),
      );
      const composer = screen.getByTestId("composer");
      fireEvent.drop(composer, {
        dataTransfer: {
          files: {
            length: 1,
            item: () => new File(["x"], "rm.txt", { type: "text/plain" }),
            0: new File(["x"], "rm.txt", { type: "text/plain" }),
          },
          types: ["Files"],
        },
      });
      await screen.findByTestId("composer-attachment-rm-1");
      fireEvent.click(screen.getByTestId("composer-attachment-remove-rm-1"));
      expect(removed).toEqual(["rm-1"]);
    });
  });

  /* --- Phase 1.6 Gap 4: plus button + file picker wiring --- */

  describe("Plus button opens a file picker via the adapter", () => {
    it("clicking + opens the hidden file input and addFile routes through the adapter", async () => {
      const added: File[] = [];
      const adapter: AttachmentAdapter = {
        async add(file: File): Promise<CompleteAttachment> {
          added.push(file);
          return {
            id: "plus-1",
            name: file.name,
            size: file.size,
            type: file.type,
          };
        },
        remove(): void {},
      };
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={() => {}} attachmentAdapter={adapter} />,
        ),
      );
      /* The hidden file input is rendered only when an adapter is wired. */
      const input = screen.getByTestId(
        "composer-file-input",
      ) as HTMLInputElement;
      expect(input).toBeInTheDocument();
      /* jsdom doesn't open a real file dialog when input.click() is
       * invoked, but it does dispatch a click event. Use the onChange
       * surface to simulate the user picking files. */
      const file = new File(["x"], "picked.txt", { type: "text/plain" });
      Object.defineProperty(input, "files", {
        value: {
          length: 1,
          item: (i: number): File | null => (i === 0 ? file : null),
          0: file,
        },
        configurable: true,
      });
      fireEvent.change(input);
      expect(added).toHaveLength(1);
      expect(added[0].name).toBe("picked.txt");
      expect(
        await screen.findByTestId("composer-attachment-plus-1"),
      ).toBeInTheDocument();
    });

    it("file input is NOT rendered when no adapter is wired (back-compat)", () => {
      render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
      expect(
        screen.queryByTestId("composer-file-input"),
      ).not.toBeInTheDocument();
    });
  });

  /* --- Phase 1.6 Gap 5: minRows / maxRows --- */

  describe("minRows / maxRows control the textarea height envelope", () => {
    it("renders the textarea with the minRows row count by default (2)", () => {
      render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      expect(ta.rows).toBe(2);
    });

    it("respects a caller-supplied minRows", () => {
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={() => {}} minRows={1} />,
        ),
      );
      const ta = screen.getByTestId("composer-textarea") as HTMLTextAreaElement;
      expect(ta.rows).toBe(1);
    });

    it("applies a max-height that grows with maxRows", () => {
      const { rerender } = render(
        withTransport(
          makeTransport(),
          <Composer onSend={() => {}} maxRows={3} />,
        ),
      );
      const small = (
        screen.getByTestId("composer-textarea") as HTMLTextAreaElement
      ).style.maxHeight;
      rerender(
        withTransport(
          makeTransport(),
          <Composer onSend={() => {}} maxRows={12} />,
        ),
      );
      const large = (
        screen.getByTestId("composer-textarea") as HTMLTextAreaElement
      ).style.maxHeight;
      /* The exact pixel value is implementation detail; assert "bigger". */
      expect(parseInt(large, 10)).toBeGreaterThan(parseInt(small, 10));
    });
  });

  /* --- Phase 1.6 Gap 6: className + data-* attributes --- */

  describe("className + data-* attribute hooks for CSS parity", () => {
    it("passes through className to the root container", () => {
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={() => {}} className="aui-composer custom-x" />,
        ),
      );
      const root = screen.getByTestId("composer");
      expect(root).toHaveClass("aui-composer");
      expect(root).toHaveClass("custom-x");
    });

    it("flips data-running on the root when a run is active", () => {
      const { rerender } = render(
        withTransport(makeTransport(), <Composer onSend={() => {}} />),
      );
      expect(screen.getByTestId("composer")).not.toHaveAttribute(
        "data-running",
      );
      rerender(
        withTransport(
          makeTransport(),
          <Composer onSend={() => {}} running={true} />,
        ),
      );
      expect(screen.getByTestId("composer")).toHaveAttribute(
        "data-running",
        "true",
      );
    });

    it("flips data-focused when the textarea gains focus", () => {
      render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
      const root = screen.getByTestId("composer");
      const ta = screen.getByTestId("composer-textarea");
      expect(root).not.toHaveAttribute("data-focused");
      fireEvent.focus(ta);
      expect(root).toHaveAttribute("data-focused", "true");
    });

    it("flips data-has-topbar when topBarSlot is present", () => {
      render(
        withTransport(
          makeTransport(),
          <Composer
            onSend={() => {}}
            topBarSlot={<span data-testid="x">x</span>}
          />,
        ),
      );
      expect(screen.getByTestId("composer")).toHaveAttribute(
        "data-has-topbar",
        "true",
      );
    });

    it("flips data-has-topbar when hasTopBarContent is true (caller's other chips)", () => {
      render(
        withTransport(
          makeTransport(),
          <Composer onSend={() => {}} hasTopBarContent={true} />,
        ),
      );
      expect(screen.getByTestId("composer")).toHaveAttribute(
        "data-has-topbar",
        "true",
      );
    });

    it("data-has-topbar is absent when nothing is shown above the textarea", () => {
      render(withTransport(makeTransport(), <Composer onSend={() => {}} />));
      expect(screen.getByTestId("composer")).not.toHaveAttribute(
        "data-has-topbar",
      );
    });
  });
});

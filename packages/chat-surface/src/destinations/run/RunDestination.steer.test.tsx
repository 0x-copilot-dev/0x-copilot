// The mid-run steer, end to end through the seam a HOST actually mounts.
//
// WHY THIS FILE EXISTS SEPARATELY FROM THE THREE UNIT SUITES
// ----------------------------------------------------------
// `steerProjection.test.ts`, `TcSteerNote.test.tsx` and `TcChat.steer.test.tsx`
// all prove the DRAWING half: given a `run_steered` entry, a row appears at the
// right beat. Every one of them is handed the projection directly. None of them
// can see whether anything ever produces one — a suite that injects the thing
// under test cannot tell you the feature is reachable, which is exactly how a
// surface ships green over a seam nobody wired.
//
// So this file starts one layer out, at the only two facts that make the
// feature real:
//
//   1. ⏎ in the in-chat composer, while a run is live, reaches
//      `POST /v1/agent/runs/{id}/steer` — and does NOT start a second run;
//   2. the `run_steered` frame that comes back on the OPEN session stream draws
//      in the transcript, with no local echo.
//
// It mounts the REAL `AssistantComposer` in `renderComposer`, wired the way
// `apps/desktop/renderer/destinationBinders.tsx:1546` wires it (`running` and
// `onSubmit` both come from the cockpit's ctx), because the base `<Composer>`
// fallback inside TcChat is never handed `running` at all — asserting against
// that fallback would be asserting against a branch no host takes.

import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { type ReactElement, type ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import type { ConversationId } from "@0x-copilot/api-types";
import type {
  Session,
  SseSubscribeOptions,
  SseSubscription,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";

import {
  AssistantComposer,
  type AssistantComposerProps,
} from "../../composer/AssistantComposer";
import type { FilePickerPort } from "../../ports/FilePickerPort";
import { KeyValueStoreProvider } from "../../providers/KeyValueStoreProvider";
import { TransportProvider } from "../../providers/TransportProvider";
import type { KeyValueStore } from "../../storage/key-value-store";
import { RunDestination, type RunStartRequest } from "./RunDestination";

const CONV = "conv-1" as ConversationId;
const RUN = "run-1";

const CAPABILITIES: TransportCapabilities = {
  substrate: "web",
  nativeSecretStorage: false,
  fileSystemAccess: false,
  clipboardWrite: false,
  openExternal: false,
};

interface CapturedSub {
  readonly path: string;
  readonly eventName?: string;
  readonly onMessage?: (raw: string) => void;
  closed: boolean;
}

class FakeTransport implements Transport {
  requestHandler: (req: TypedRequest) => Promise<unknown> = async (req) =>
    req.path.includes("/messages")
      ? { messages: [] }
      : {
          latest_run_id: RUN,
          latest_run_id_any_status: RUN,
          runs: [
            { run_id: RUN, status: "running", goal: "Reconcile the batch" },
          ],
        };
  readonly requests: TypedRequest[] = [];
  readonly subs: CapturedSub[] = [];

  async request<TRes>(req: TypedRequest): Promise<TRes> {
    this.requests.push(req);
    return (await this.requestHandler(req)) as TRes;
  }

  subscribeServerSentEvents(opts: SseSubscribeOptions): SseSubscription {
    const sub: CapturedSub = {
      path: opts.path,
      eventName: opts.eventName,
      onMessage: opts.onMessage,
      closed: false,
    };
    this.subs.push(sub);
    return {
      close: () => {
        sub.closed = true;
      },
    };
  }

  getSession(): Session {
    return { bearer: null };
  }

  capabilities(): TransportCapabilities {
    return CAPABILITIES;
  }

  /** The `useRunSession` tail — the only sub tagged `runtime_event`. */
  get sessionSub(): CapturedSub | undefined {
    return [...this.subs]
      .reverse()
      .find((sub) => !sub.closed && sub.eventName === "runtime_event");
  }

  /** Every POST this mount made, by path — the reachability assertion. */
  postsTo(fragment: string): readonly TypedRequest[] {
    return this.requests.filter(
      (req) => req.method === "POST" && req.path.includes(fragment),
    );
  }
}

function makeStore(): KeyValueStore {
  const map = new Map<string, string>();
  return {
    get: (key) => map.get(key) ?? null,
    set: (key, value) => {
      if (value === null) map.delete(key);
      else map.set(key, value);
    },
    keys: (prefix) =>
      [...map.keys()].filter(
        (key) => prefix === undefined || key.startsWith(prefix),
      ),
  };
}

/**
 * The in-chat composer as a host mounts it: the REAL `AssistantComposer`, with
 * `running` and `onSubmit` both taken from the cockpit's injected ctx. This is
 * the whole point of the fixture — `running` is what used to make ⏎ a no-op,
 * and `dispatch` is what now has somewhere to send it.
 */
function hostRenderComposer(ctx: {
  readonly disabled: boolean;
  readonly placeholder: string;
  readonly dispatch: (request: RunStartRequest) => Promise<void>;
  readonly running: boolean;
  readonly onCancel: () => void;
}): ReactElement {
  const filePicker: FilePickerPort = { pick: async () => [] };
  const props: AssistantComposerProps = {
    connectors: { servers: [], loading: false },
    skills: { skills: [], loading: false },
    filePicker,
    renderPlusMenu: ({ open, children }): ReactNode =>
      open ? <div>{children}</div> : null,
    skillInstructionPrompt: (name) => `Use the ${name} skill for this request.`,
    mcpServerInstructionPrompt: (name) =>
      `Use the ${name} MCP server for this request.`,
    onOpenMcpSettings: () => {},
    onOpenSkillsSettings: () => {},
    onShowConnectors: () => {},
    disabled: ctx.disabled,
    placeholder: ctx.placeholder,
    running: ctx.running,
    onCancel: ctx.onCancel,
    onSubmit: ({ text }) => ctx.dispatch({ goal: text }),
  };
  return <AssistantComposer {...props} />;
}

function renderRun(transport: Transport, store: KeyValueStore) {
  const ui: ReactElement = (
    <TransportProvider transport={transport}>
      <KeyValueStoreProvider store={store}>
        <RunDestination
          conversationId={CONV}
          renderComposer={hostRenderComposer}
        />
      </KeyValueStoreProvider>
    </TransportProvider>
  );
  return render(ui);
}

/** A `run_steered` envelope shaped exactly as the coordinator appends it. */
function steerEvent(sequenceNo: number, eventId: string, text: string) {
  return {
    event_id: eventId,
    run_id: RUN,
    conversation_id: "conv-1",
    sequence_no: sequenceNo,
    event_type: "run_steered",
    activity_kind: "note",
    summary: "You steered this run.",
    payload: {
      steer: {
        steer_id: `steer-${eventId}`,
        text,
        requested_by_user_id: "user-1",
        created_at: "2026-08-14T10:00:00Z",
      },
    },
    created_at: "2026-08-14T10:00:00Z",
  };
}

/** Type into the mounted composer and press ⏎. */
async function sendFromComposer(text: string): Promise<void> {
  const textarea = await screen.findByTestId("composer-textarea");
  fireEvent.change(textarea, { target: { value: text } });
  await act(async () => {
    fireEvent.keyDown(textarea, { key: "Enter" });
  });
}

describe("RunDestination — steering a live run from the composer", () => {
  it("routes ⏎ mid-run to POST /steer instead of starting a second run", async () => {
    const transport = new FakeTransport();
    renderRun(transport, makeStore());

    // The run must actually be bound and live, or `running` is false and this
    // test would pass by testing nothing.
    await waitFor(() =>
      expect(screen.getByTestId("thread-canvas")).not.toBeNull(),
    );

    await sendFromComposer("no, use the other file");

    await waitFor(() => expect(transport.postsTo("/steer").length).toBe(1));
    const steer = transport.postsTo("/steer")[0];
    expect(steer.path).toBe(`/v1/agent/runs/${RUN}/steer`);
    // Text only. `requested_by_user_id` is stamped by the facade from the
    // verified session precisely so a body-supplied identity cannot put words
    // into someone else's run — sending one would be a field the server throws
    // away. See `backend_facade/app.py::steer_run`.
    expect(steer.body).toEqual({ text: "no, use the other file" });

    // The half that makes this a steer rather than a second agent on the same
    // thread: no run-create POST was made.
    expect(
      transport.postsTo("/runs").filter((r) => !r.path.endsWith("/steer")),
    ).toEqual([]);
  });

  it("draws the durable run_steered note from the stream, with no local echo", async () => {
    const transport = new FakeTransport();
    renderRun(transport, makeStore());
    await waitFor(() =>
      expect(screen.getByTestId("thread-canvas")).not.toBeNull(),
    );

    await sendFromComposer("stop reading and summarise");

    // Before the server's frame arrives there is deliberately NOTHING in the
    // transcript: the row is the durable record, not a guess. An optimistic
    // echo here would print the sentence twice once the frame landed.
    expect(screen.queryByTestId("tc-chat-steer-item-ev-1")).toBeNull();

    act(() => {
      transport.sessionSub?.onMessage?.(
        JSON.stringify(steerEvent(4, "ev-1", "stop reading and summarise")),
      );
    });

    await waitFor(() =>
      expect(screen.getByTestId("tc-chat-steer-item-ev-1")).not.toBeNull(),
    );
    expect(screen.getByTestId("tc-chat-steer-ev-1-label").textContent).toBe(
      "You steered this run.",
    );
    expect(screen.getByTestId("tc-chat-steer-ev-1-text").textContent).toBe(
      "stop reading and summarise",
    );
  });

  it("surfaces a rejected steer instead of dropping the sentence", async () => {
    const transport = new FakeTransport();
    const base = transport.requestHandler;
    transport.requestHandler = async (req) => {
      if (req.method === "POST" && req.path.endsWith("/steer")) {
        throw new Error("run already finished");
      }
      return base(req);
    };
    const onSubmitError = vi.fn();
    // Same host wiring, plus the composer's own error channel — the seam a
    // rejection is supposed to travel down.
    const ui: ReactElement = (
      <TransportProvider transport={transport}>
        <KeyValueStoreProvider store={makeStore()}>
          <RunDestination
            conversationId={CONV}
            renderComposer={(ctx) => {
              const filePicker: FilePickerPort = { pick: async () => [] };
              return (
                <AssistantComposer
                  connectors={{ servers: [], loading: false }}
                  skills={{ skills: [], loading: false }}
                  filePicker={filePicker}
                  renderPlusMenu={({ open, children }): ReactNode =>
                    open ? <div>{children}</div> : null
                  }
                  skillInstructionPrompt={(name) => `Use the ${name} skill.`}
                  mcpServerInstructionPrompt={(name) => `Use ${name}.`}
                  onOpenMcpSettings={() => {}}
                  onOpenSkillsSettings={() => {}}
                  onShowConnectors={() => {}}
                  disabled={ctx.disabled}
                  placeholder={ctx.placeholder}
                  running={ctx.running}
                  onCancel={ctx.onCancel}
                  onSubmit={({ text }) => ctx.dispatch({ goal: text })}
                  onSubmitError={onSubmitError}
                />
              );
            }}
          />
        </KeyValueStoreProvider>
      </TransportProvider>
    );
    render(ui);
    await waitFor(() =>
      expect(screen.getByTestId("thread-canvas")).not.toBeNull(),
    );

    await sendFromComposer("too late");

    // A steer that loses its race with the run ending has to SAY so. Silently
    // dropping it is the exact defect this whole change replaces, one layer
    // down — a user typed a sentence and nothing happened.
    await waitFor(() => expect(onSubmitError).toHaveBeenCalledTimes(1));
  });
});

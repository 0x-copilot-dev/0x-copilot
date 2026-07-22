// @vitest-environment jsdom
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { describe, expect, it } from "vitest";

import type {
  AgentRunStatus,
  RuntimeEventEnvelope,
} from "@0x-copilot/api-types";
import type {
  Session,
  Transport,
  TransportCapabilities,
  TypedRequest,
} from "@0x-copilot/chat-transport";

import { TransportProvider } from "../../providers/TransportProvider";
import { useRunTranscript } from "./useRunTranscript";

function ev(
  partial: Partial<RuntimeEventEnvelope> & {
    event_type: string;
    sequence_no: number;
  },
): RuntimeEventEnvelope {
  const { event_id, sequence_no, event_type, created_at, payload, ...rest } =
    partial;
  return {
    event_id: event_id ?? `e${sequence_no}`,
    sequence_no,
    event_type,
    created_at: created_at ?? new Date(1_700_000_000_000).toISOString(),
    payload: payload ?? {},
    ...rest,
  } as RuntimeEventEnvelope;
}

/** Transport whose /messages response is read live from `historyRef`. */
function makeTransport(historyRef: { current: unknown }): Transport {
  return {
    request: (async (req: TypedRequest) =>
      typeof req.path === "string" && req.path.endsWith("/messages")
        ? historyRef.current
        : {}) as Transport["request"],
    subscribeServerSentEvents: () => ({ close: () => undefined }),
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

function wrapper(transport: Transport) {
  return ({ children }: { children: ReactNode }) =>
    createElement(TransportProvider, { transport, children });
}

describe("useRunTranscript", () => {
  it("overlays the live streamed reply on history while the run streams", async () => {
    const historyRef = {
      current: {
        messages: [{ message_id: "u1", role: "user", content_text: "do it" }],
      },
    };
    const events = [
      ev({
        event_type: "model_delta",
        sequence_no: 1,
        payload: { text: "work" },
      }),
    ];
    const { result } = renderHook(
      () =>
        useRunTranscript({
          conversationId: "c",
          runId: "r1",
          runStatus: "running",
          events,
        }),
      { wrapper: wrapper(makeTransport(historyRef)) },
    );

    await waitFor(() => expect(result.current.messages).toHaveLength(2));
    expect(result.current.messages[0].role).toBe("user");
    expect(result.current.messages[1].role).toBe("assistant");
    expect(result.current.messages[1].parts[0]).toMatchObject({
      text: "work",
      status: { type: "running" },
    });
  });

  it("drops the live overlay once the run settles — no duplicate reply", async () => {
    const historyRef = {
      current: {
        messages: [{ message_id: "u1", role: "user", content_text: "do it" }],
      },
    };
    const transport = makeTransport(historyRef);
    const streaming = [
      ev({
        event_type: "model_delta",
        sequence_no: 1,
        payload: { text: "final answer" },
      }),
    ];
    const finished = [
      ...streaming,
      ev({
        event_type: "final_response",
        sequence_no: 2,
        payload: { text: "final answer" },
      }),
    ];

    const { result, rerender } = renderHook(
      (props: { runStatus: AgentRunStatus; events: RuntimeEventEnvelope[] }) =>
        useRunTranscript({
          conversationId: "c",
          runId: "r1",
          runStatus: props.runStatus,
          events: props.events,
        }),
      {
        wrapper: wrapper(transport),
        initialProps: {
          runStatus: "running" as AgentRunStatus,
          events: streaming,
        },
      },
    );
    await waitFor(() => expect(result.current.messages).toHaveLength(2));

    // Run completes: the reply is now persisted in history.
    historyRef.current = {
      messages: [
        { message_id: "u1", role: "user", content_text: "do it" },
        { message_id: "a1", role: "assistant", content_text: "final answer" },
      ],
    };
    rerender({ runStatus: "completed", events: finished });

    await waitFor(() => {
      expect(result.current.messages).toHaveLength(2);
      // The reply is history's persisted message, not the live overlay.
      expect(result.current.messages[1].message_id).toBe("a1");
    });
  });

  // WC-P4 (AD-9): optimistic user echo.
  it("echoes the pending user message at the tail before the re-seed absorbs it", async () => {
    const historyRef = {
      current: {
        messages: [
          { message_id: "u1", role: "user", content_text: "do it" },
          { message_id: "a1", role: "assistant", content_text: "done" },
        ],
      },
    };
    const { result } = renderHook(
      () =>
        useRunTranscript({
          conversationId: "c",
          // A run is bound (turn-N send) but the re-seed hasn't landed yet and no
          // reply has streamed — the echo bridges that beat at the tail.
          runId: "r-prev",
          runStatus: "running",
          events: [],
          pendingUserMessage: "follow up",
        }),
      { wrapper: wrapper(makeTransport(historyRef)) },
    );

    await waitFor(() => expect(result.current.messages).toHaveLength(3));
    const echo = result.current.messages[2];
    expect(echo.role).toBe("user");
    expect(echo.parts[0].text).toBe("follow up");
  });

  it("drops the echo once the re-seed carries the persisted user turn (no duplicate)", async () => {
    const historyRef = {
      current: {
        // The run-start re-seed already absorbed the user's turn.
        messages: [
          { message_id: "u1", role: "user", content_text: "do it" },
          { message_id: "u2", role: "user", content_text: "follow up" },
        ],
      },
    };
    const { result } = renderHook(
      () =>
        useRunTranscript({
          conversationId: "c",
          runId: "r2",
          runStatus: "running",
          events: [],
          pendingUserMessage: "follow up",
        }),
      { wrapper: wrapper(makeTransport(historyRef)) },
    );

    // History has 2 messages; the echo must NOT add a 3rd (deduped).
    await waitFor(() => expect(result.current.messages).toHaveLength(2));
    expect(
      result.current.messages.filter((m) => m.role === "user"),
    ).toHaveLength(2);
  });
});

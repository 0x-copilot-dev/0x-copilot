import type { SseSubscription } from "../types";

export interface SseRunnerOptions {
  readonly url: string;
  readonly headers: Record<string, string>;
  /** SSE `event:` field to dispatch; defaults to "message" if omitted. */
  readonly eventName: string;
  readonly onMessage: (raw: string) => void;
  readonly onOpen?: () => void;
  readonly onError?: (err: Error) => void;
  readonly fetchImpl: typeof fetch;
}

// Authenticated SSE reader.
//
// Why not EventSource: the browser's EventSource cannot send custom
// headers, so the bearer never reaches the facade and the stream 401s.
// Cookie sessions or `?token=…` URL params either invent a second auth
// scheme or write bearer-equivalents into proxy/access logs — both
// unacceptable under a bearer-only model. Streaming fetch carries the
// standard Authorization header on a normal request, no new server
// surface, no logged credentials.
//
// Reconnect semantics intentionally live with the caller — domain layers
// own the cursor (`?after_sequence=N`) based on the highest event they
// actually rendered. Closing the returned subscription aborts the
// in-flight fetch and the reader loop.
export function runSseStream(opts: SseRunnerOptions): SseSubscription {
  const controller = new AbortController();
  const subscription: SseSubscription = {
    close: () => controller.abort(),
  };

  void (async () => {
    let response: Response;
    try {
      response = await opts.fetchImpl(opts.url, {
        headers: { ...opts.headers, accept: "text/event-stream" },
        signal: controller.signal,
      });
    } catch (err) {
      if (controller.signal.aborted) {
        return;
      }
      opts.onError?.(toError(err));
      return;
    }
    if (!response.ok || response.body === null) {
      opts.onError?.(new Error(`stream returned ${response.status}`));
      return;
    }
    opts.onOpen?.();

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) {
          return;
        }
        buffer += decoder.decode(value, { stream: true });
        // SSE frames are separated by a blank line. Accumulate across
        // chunks, split on the frame boundary, dispatch each completed
        // frame. Within a frame, `event:` and `data:` lines are buffered
        // and emitted to onMessage when the event name matches.
        let boundary = buffer.indexOf("\n\n");
        while (boundary !== -1) {
          const frame = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          dispatchFrame(frame, opts.eventName, opts.onMessage);
          boundary = buffer.indexOf("\n\n");
        }
      }
    } catch (err) {
      if (controller.signal.aborted) {
        return;
      }
      opts.onError?.(toError(err));
    } finally {
      reader.releaseLock();
    }
  })();

  return subscription;
}

function dispatchFrame(
  frame: string,
  expectedEvent: string,
  onMessage: (data: string) => void,
): void {
  let event = "message";
  const dataLines: string[] = [];
  for (const rawLine of frame.split("\n")) {
    const line = rawLine.replace(/\r$/, "");
    if (line === "" || line.startsWith(":")) {
      continue;
    }
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).replace(/^ /, ""));
    }
    // Other SSE fields (id:, retry:) intentionally ignored — domain
    // callers own reconnect cursoring via their own sequence numbers.
  }
  if (event !== expectedEvent || dataLines.length === 0) {
    return;
  }
  onMessage(dataLines.join("\n"));
}

function toError(value: unknown): Error {
  if (value instanceof Error) {
    return value;
  }
  return new Error(typeof value === "string" ? value : "stream error");
}

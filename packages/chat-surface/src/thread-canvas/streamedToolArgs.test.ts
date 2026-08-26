import { describe, expect, it } from "vitest";

import {
  isStreamedArgsEnvelope,
  parseStreamedArgs,
  resolveToolArgs,
} from "./streamedToolArgs";

// Transcribed from a real packaged run's events.jsonl, not invented. Fixtures
// with a simpler shape than the wire are exactly how the original bug shipped
// green, so these keep the wire's shape verbatim — including the ending, which
// matters: the LAST frame carries `args` as the parsed object, not as an
// envelope. That is why a settled card was already correct and only a call in
// flight rendered escaped JSON.
const REAL_ARGS = {
  file_path: "/Users/dev/projects/kscope-benchmarks/random.csv",
  old_string: "5,orange,lynx,29,false",
  new_string: "5,orange,lynx,30,false",
} as const;

const REAL_EDIT_FILE_DELTAS = [
  { delta: "" },
  { delta: '{"file_pa' },
  { delta: '{"file_path": "/User' },
  { delta: JSON.stringify(REAL_ARGS) },
  // The real terminal delta: already parsed by the runtime.
  { ...REAL_ARGS },
] as const;

/** The frames that are still an envelope — everything before the terminal one. */
const STREAMING_FRAMES = REAL_EDIT_FILE_DELTAS.slice(0, 4);

describe("isStreamedArgsEnvelope", () => {
  it("recognises the runtime's one-key accumulator", () => {
    expect(isStreamedArgsEnvelope({ delta: '{"file_pa' })).toBe(true);
  });

  it("rejects a real argument record that happens to be one key", () => {
    expect(isStreamedArgsEnvelope({ file_path: "/tmp/a.txt" })).toBe(false);
  });

  it("rejects the empty record tool_call_started sends", () => {
    expect(isStreamedArgsEnvelope({})).toBe(false);
  });

  it("leaves a lone non-JSON `delta` string argument alone", () => {
    expect(isStreamedArgsEnvelope({ delta: "0.25" })).toBe(false);
  });

  it("rejects an accumulator-shaped key alongside real arguments", () => {
    expect(isStreamedArgsEnvelope({ delta: "{", file_path: "/tmp/a" })).toBe(
      false,
    );
  });
});

describe("parseStreamedArgs", () => {
  it("recovers the real arguments once the accumulator completes", () => {
    expect(parseStreamedArgs(STREAMING_FRAMES[3])).toEqual(REAL_ARGS);
  });

  it("returns undefined for every partial prefix", () => {
    for (const frame of STREAMING_FRAMES.slice(0, 3)) {
      expect(parseStreamedArgs(frame)).toBeUndefined();
    }
  });
});

describe("resolveToolArgs", () => {
  it("replays the real edit_file stream to complete arguments", () => {
    let args: Record<string, unknown> | undefined;
    // tool_call_started carries `{}`.
    args = resolveToolArgs({}, args);
    expect(args).toEqual({});
    for (const frame of REAL_EDIT_FILE_DELTAS) {
      args = resolveToolArgs(frame, args);
    }
    expect(args).toEqual(REAL_ARGS);
  });

  it("never leaves the escaped-JSON envelope as the stored arguments", () => {
    let args: Record<string, unknown> | undefined;
    for (const frame of REAL_EDIT_FILE_DELTAS) {
      args = resolveToolArgs(frame, args);
      expect(args === undefined || !("delta" in args)).toBe(true);
    }
  });

  it("agrees with the old behaviour on the terminal frame", () => {
    // The regression is bounded to the streaming window: on the frame the
    // runtime sends last, storing the snapshot verbatim and unwrapping it give
    // the same answer. This test is what stops the fix being described as
    // bigger than it is.
    const terminal = REAL_EDIT_FILE_DELTAS[REAL_EDIT_FILE_DELTAS.length - 1];
    expect(resolveToolArgs(terminal, undefined)).toEqual(terminal);
  });

  it("keeps the last good parse when a later frame is a partial prefix", () => {
    const good = resolveToolArgs({ delta: '{"a": 1}' }, undefined);
    expect(good).toEqual({ a: 1 });
    expect(resolveToolArgs({ delta: '{"a": 1, "b": ' }, good)).toEqual({
      a: 1,
    });
  });

  it("passes an ordinary argument record straight through", () => {
    // read_file in the same capture carried real arguments on the started
    // frame and emitted no deltas at all.
    expect(resolveToolArgs({ file_path: "/tmp/x.csv" }, undefined)).toEqual({
      file_path: "/tmp/x.csv",
    });
  });
});

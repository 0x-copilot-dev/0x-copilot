import { describe, expect, it } from "vitest";

import type { RuntimeEventEnvelope } from "@0x-copilot/api-types";

import { projectToolCalls } from "./eventProjector";

// Transcribed from a real packaged run: a `write_file` denied for want of a
// workspace grant. `error_message` and `safe_message` really were both absent,
// and the failure sentence really did sit in `output.content` as plain prose —
// which is why the card showed the backend's generic summary instead.
function frames(
  output: Record<string, unknown>,
  status: string,
): RuntimeEventEnvelope[] {
  const base = {
    run_id: "run-1",
    conversation_id: "conv-1",
    activity_kind: "event",
    created_at: "2026-08-26T12:00:00.000Z",
  };
  return [
    {
      ...base,
      event_id: "e1",
      sequence_no: 1,
      event_type: "tool_call_started",
      payload: { tool_name: "write_file", call_id: "c1", args: {} },
    },
    {
      ...base,
      event_id: "e2",
      sequence_no: 2,
      event_type: "tool_result",
      summary: "0xCopilot couldn't complete this step.",
      payload: { tool_name: "write_file", call_id: "c1", status, output },
    },
  ] as unknown as RuntimeEventEnvelope[];
}

const DENIED = { content: "Error: permission denied for write on /random.csv" };

describe("a tool that reports failure as plain text", () => {
  it("surfaces the real sentence instead of the generic summary", () => {
    const [call] = projectToolCalls(frames(DENIED, "failed"));
    expect(call.errorMessage).toBe(
      "permission denied for write on /random.csv",
    );
  });

  it("strips the tool's own Error: prefix, which the card already conveys", () => {
    const [call] = projectToolCalls(frames(DENIED, "failed"));
    expect(call.errorMessage?.startsWith("Error")).toBe(false);
  });

  it("never treats a SUCCESSFUL result's content as an error", () => {
    // A success `content` is the tool's ANSWER; printing it on the error line
    // would invent a failure that did not happen.
    const [call] = projectToolCalls(
      frames({ content: "Updated file /tmp/a.csv" }, "completed"),
    );
    expect(call.errorMessage).toBeUndefined();
  });

  it("lets a curated safe_message win over the raw text", () => {
    const events = frames(DENIED, "failed");
    (events[1].payload as Record<string, unknown>)["safe_message"] =
      "That folder isn't attached yet.";
    const [call] = projectToolCalls(events);
    expect(call.errorMessage).toBe("That folder isn't attached yet.");
  });

  it("keeps the header to one line and bounds its length", () => {
    const long = `Error: ${"x".repeat(400)}\nsecond line`;
    const [call] = projectToolCalls(frames({ content: long }, "failed"));
    expect(call.errorMessage).not.toContain("second line");
    expect((call.errorMessage ?? "").length).toBeLessThanOrEqual(201);
  });

  it("ignores a non-string content", () => {
    const [call] = projectToolCalls(frames({ content: { a: 1 } }, "failed"));
    expect(call.errorMessage).toBeUndefined();
  });
});

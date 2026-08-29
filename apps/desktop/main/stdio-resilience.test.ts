import { PassThrough } from "node:stream";
import { describe, expect, it } from "vitest";

import { installStdioResilience } from "./stdio-resilience";

/**
 * The bug these pin: the app is spawned `stdio: "inherit"` from the `copilot`
 * CLI, so closing the launching terminal leaves the GUI writing to a dead
 * pipe, and an unguarded `console.error` then crashes the main process.
 *
 * Note what makes this testable at all: the assertion is about whether the
 * stream THROWS, so each case emits a real `error` event rather than checking
 * that a listener was registered. "A listener exists" is exactly the shape of
 * assertion that passes over a listener which re-throws everything.
 */
describe("installStdioResilience", () => {
  const epipe = (): Error =>
    Object.assign(new Error("write EPIPE"), { code: "EPIPE" });

  it("swallows EPIPE so a dead pipe cannot crash the process", () => {
    const stream = new PassThrough();
    const dispose = installStdioResilience([stream]);

    expect(() => stream.emit("error", epipe())).not.toThrow();

    dispose();
  });

  it("re-throws anything that is not EPIPE", () => {
    const stream = new PassThrough();
    const dispose = installStdioResilience([stream]);
    const enospc = Object.assign(new Error("no space left"), {
      code: "ENOSPC",
    });

    // A full disk is a real failure. Swallowing every stream error to fix
    // EPIPE is how the next one goes unnoticed.
    expect(() => stream.emit("error", enospc)).toThrow("no space left");

    dispose();
  });

  it("guards every stream it is given, not just the first", () => {
    const out = new PassThrough();
    const err = new PassThrough();
    const dispose = installStdioResilience([out, err]);

    expect(() => out.emit("error", epipe())).not.toThrow();
    expect(() => err.emit("error", epipe())).not.toThrow();

    dispose();
  });

  it("removes only its own listeners when disposed", () => {
    const stream = new PassThrough();
    const before = stream.listenerCount("error");
    const dispose = installStdioResilience([stream]);
    expect(stream.listenerCount("error")).toBe(before + 1);

    dispose();

    expect(stream.listenerCount("error")).toBe(before);
  });

  it("leaves the stream unguarded again after disposal", () => {
    const stream = new PassThrough();
    installStdioResilience([stream])();

    // With no listener, Node's EventEmitter throws an emitted 'error'. This is
    // the control case: it proves the passing case above is the guard working
    // and not PassThrough being tolerant on its own.
    expect(() => stream.emit("error", epipe())).toThrow("write EPIPE");
  });
});

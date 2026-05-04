import { describe, expect, it, vi } from "vitest";
import {
  classifyError,
  installGlobalErrorHandlers,
} from "./globalErrorHandlers";

function withExtensionStack(message: string): Error {
  const error = new Error(message);
  error.stack = [
    `Error: ${message}`,
    "    at handler (chrome-extension://abcdef/content.js:42:13)",
    "    at MessagePort.<anonymous> (chrome-extension://abcdef/content.js:99:7)",
  ].join("\n");
  return error;
}

describe("classifyError", () => {
  it("flags errors whose stack includes a chrome-extension:// frame as extension", () => {
    const result = classifyError(withExtensionStack("anything goes here"));
    expect(result.category).toBe("extension");
    expect(result.reason).toBe("stack-extension-url");
  });

  it("flags errors whose stack includes a moz-extension:// frame as extension", () => {
    const error = new Error("ff content script failed");
    error.stack = "Error: x\n    at f (moz-extension://uuid/content.js:1:1)";
    expect(classifyError(error).category).toBe("extension");
  });

  it("flags the canonical message-channel pattern as extension even without a stack", () => {
    const message =
      "A listener indicated an asynchronous response by returning true, but the message channel closed before a response was received";
    const error = new Error(message);
    error.stack = undefined;
    const result = classifyError(error);
    expect(result.category).toBe("extension");
    expect(result.reason).toBe("message-pattern");
  });

  it("treats a TypeError from app code as an app error", () => {
    const error = new TypeError("Cannot read properties of undefined");
    error.stack =
      "TypeError: Cannot read properties of undefined\n    at ChatScreen (http://localhost:5173/src/features/chat/ChatScreen.tsx:42:13)";
    const result = classifyError(error);
    expect(result.category).toBe("app");
    expect(result.errorClass).toBe("TypeError");
  });

  it("treats an arbitrary thrown string as an app error of class 'string'", () => {
    expect(classifyError("oops")).toEqual({
      category: "app",
      errorClass: "string",
      reason: "default",
    });
  });

  it("treats null and undefined as app errors with stable class labels", () => {
    expect(classifyError(null).errorClass).toBe("null");
    expect(classifyError(undefined).errorClass).toBe("undefined");
  });
});

describe("installGlobalErrorHandlers", () => {
  it("logs [extension-noise] for the message-channel pattern and skips OTEL", () => {
    const target = new EventTarget();
    const logger = { warn: vi.fn(), error: vi.fn() };
    const { uninstall } = installGlobalErrorHandlers({
      target,
      logger,
      logNoise: true,
    });

    const event = new Event("unhandledrejection") as PromiseRejectionEvent;
    Object.defineProperty(event, "reason", {
      value: new Error(
        "A listener indicated an asynchronous response by returning true, but the message channel closed before a response was received",
      ),
    });
    target.dispatchEvent(event);

    expect(logger.warn).toHaveBeenCalledTimes(1);
    expect(logger.warn.mock.calls[0][0]).toMatch(/^\[extension-noise\]/);
    expect(logger.error).not.toHaveBeenCalled();
    uninstall();
  });

  it("logs [app-error] for an app-side TypeError", () => {
    const target = new EventTarget();
    const logger = { warn: vi.fn(), error: vi.fn() };
    const { uninstall } = installGlobalErrorHandlers({ target, logger });

    const error = new TypeError("nope");
    error.stack =
      "TypeError: nope\n    at f (http://localhost:5173/src/main.tsx:1:1)";
    const event = new Event("unhandledrejection") as PromiseRejectionEvent;
    Object.defineProperty(event, "reason", { value: error });
    target.dispatchEvent(event);

    expect(logger.error).toHaveBeenCalledTimes(1);
    expect(logger.error.mock.calls[0][0]).toMatch(/^\[app-error\]/);
    expect(logger.error.mock.calls[0][0]).toContain("class=TypeError");
    uninstall();
  });

  it("uninstall stops the listeners from firing", () => {
    const target = new EventTarget();
    const logger = { warn: vi.fn(), error: vi.fn() };
    const { uninstall } = installGlobalErrorHandlers({
      target,
      logger,
      logNoise: true,
    });
    uninstall();

    const event = new Event("unhandledrejection") as PromiseRejectionEvent;
    Object.defineProperty(event, "reason", {
      value: new Error(
        "A listener indicated an asynchronous response by returning true, but the message channel closed before a response was received",
      ),
    });
    target.dispatchEvent(event);

    expect(logger.warn).not.toHaveBeenCalled();
    expect(logger.error).not.toHaveBeenCalled();
  });

  it("classifies window 'error' events using the underlying Error", () => {
    const target = new EventTarget();
    const logger = { warn: vi.fn(), error: vi.fn() };
    const { uninstall } = installGlobalErrorHandlers({
      target,
      logger,
      logNoise: true,
    });

    const event = new Event("error") as ErrorEvent;
    Object.defineProperty(event, "error", {
      value: (() => {
        const err = new Error("connection ext failure");
        err.stack =
          "Error: connection ext failure\n    at h (chrome-extension://uuid/bg.js:1:1)";
        return err;
      })(),
    });
    target.dispatchEvent(event);

    expect(logger.warn).toHaveBeenCalledTimes(1);
    expect(logger.error).not.toHaveBeenCalled();
    uninstall();
  });
});

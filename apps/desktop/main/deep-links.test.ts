// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type AppListener = (...args: unknown[]) => void;

interface FakeApp {
  emit: (event: string, ...args: unknown[]) => void;
  on: (event: string, listener: AppListener) => FakeApp;
  off: (event: string, listener: AppListener) => FakeApp;
  setAsDefaultProtocolClient: (scheme: string) => boolean;
}

const fakeApp: FakeApp = (() => {
  const listeners = new Map<string, Set<AppListener>>();
  return {
    emit(event, ...args) {
      const set = listeners.get(event);
      if (!set) return;
      for (const fn of set) fn(...args);
    },
    on(event, listener) {
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event)!.add(listener);
      return fakeApp;
    },
    off(event, listener) {
      listeners.get(event)?.delete(listener);
      return fakeApp;
    },
    setAsDefaultProtocolClient: vi.fn(() => true),
  };
})();

vi.mock("electron", () => ({ app: fakeApp }));

type FakeEvent = { preventDefault: () => void };

function fakeOpenUrlEvent(): FakeEvent {
  return { preventDefault: vi.fn() };
}

let parseDeepLink: typeof import("./deep-links").parseDeepLink;
let registerDeepLinks: typeof import("./deep-links").registerDeepLinks;

beforeEach(async () => {
  vi.resetModules();
  const mod = await import("./deep-links");
  parseDeepLink = mod.parseDeepLink;
  registerDeepLinks = mod.registerDeepLinks;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("parseDeepLink", () => {
  it("parses an enterprise:// URL with query", () => {
    const parsed = parseDeepLink(
      "enterprise://oauth/callback?code=ABC&state=XYZ",
    );
    expect(parsed).not.toBeNull();
    expect(parsed?.searchParams).toEqual({ code: "ABC", state: "XYZ" });
  });

  it("rejects a non-enterprise URL", () => {
    expect(parseDeepLink("https://example.com")).toBeNull();
  });

  it("rejects gibberish", () => {
    expect(parseDeepLink("not a url at all")).toBeNull();
  });
});

describe("registerDeepLinks — OAuth callback dispatch", () => {
  it("invokes onOAuthCallback when open-url delivers oauth/callback with code+state", () => {
    const onOAuthCallback = vi.fn();
    const reg = registerDeepLinks({ onOAuthCallback });

    fakeApp.emit(
      "open-url",
      fakeOpenUrlEvent(),
      "enterprise://oauth/callback?code=CODE_A&state=STATE_A",
    );

    expect(onOAuthCallback).toHaveBeenCalledTimes(1);
    expect(onOAuthCallback).toHaveBeenCalledWith("CODE_A", "STATE_A");
    reg.unsubscribe();
  });

  it("routes to the connector router first, bypassing app-login, when it owns the state (AC9 demux)", () => {
    const onOAuthCallback = vi.fn();
    // The connector router claims only its own 256-bit state.
    const connectorCallbackRouter = vi.fn(
      (_code: string, state: string) => state === "CONNECTOR_STATE",
    );
    const reg = registerDeepLinks({ onOAuthCallback, connectorCallbackRouter });

    fakeApp.emit(
      "open-url",
      fakeOpenUrlEvent(),
      "enterprise://oauth/callback?code=CODE_C&state=CONNECTOR_STATE",
    );

    // Connector owned it → app-login is NOT invoked.
    expect(connectorCallbackRouter).toHaveBeenCalledWith(
      "CODE_C",
      "CONNECTOR_STATE",
    );
    expect(onOAuthCallback).not.toHaveBeenCalled();
    reg.unsubscribe();
  });

  it("falls through to app-login when the connector router does not own the state", () => {
    const onOAuthCallback = vi.fn();
    const connectorCallbackRouter = vi.fn(() => false);
    const reg = registerDeepLinks({ onOAuthCallback, connectorCallbackRouter });

    fakeApp.emit(
      "open-url",
      fakeOpenUrlEvent(),
      "enterprise://oauth/callback?code=CODE_L&state=LOGIN_STATE",
    );

    expect(connectorCallbackRouter).toHaveBeenCalledWith(
      "CODE_L",
      "LOGIN_STATE",
    );
    // Not a connector's state → app-login handles it.
    expect(onOAuthCallback).toHaveBeenCalledWith("CODE_L", "LOGIN_STATE");
    reg.unsubscribe();
  });

  it("invokes onOAuthCallback when second-instance argv carries oauth/callback", () => {
    const onOAuthCallback = vi.fn();
    const reg = registerDeepLinks({ onOAuthCallback });

    fakeApp.emit("second-instance", {} as FakeEvent, [
      "electron",
      "--some-flag",
      "enterprise://oauth/callback?code=CODE_B&state=STATE_B",
    ]);

    expect(onOAuthCallback).toHaveBeenCalledTimes(1);
    expect(onOAuthCallback).toHaveBeenCalledWith("CODE_B", "STATE_B");
    reg.unsubscribe();
  });

  it("does NOT invoke onOAuthCallback when state is missing (auth state machine never receives partial inputs)", () => {
    const onOAuthCallback = vi.fn();
    const logger = { info: vi.fn(), warn: vi.fn() };
    const reg = registerDeepLinks({ onOAuthCallback, logger });

    fakeApp.emit(
      "open-url",
      fakeOpenUrlEvent(),
      "enterprise://oauth/callback?code=CODE_ONLY",
    );

    expect(onOAuthCallback).not.toHaveBeenCalled();
    expect(logger.warn).toHaveBeenCalledWith(
      "oauth callback missing code/state or no handler",
      expect.objectContaining({ hasCode: true, hasState: false }),
    );
    reg.unsubscribe();
  });

  it("does NOT invoke onOAuthCallback for non-auth deep links", () => {
    const onOAuthCallback = vi.fn();
    const reg = registerDeepLinks({ onOAuthCallback });

    fakeApp.emit(
      "open-url",
      fakeOpenUrlEvent(),
      "enterprise://workspace/wsp_acme/chats",
    );

    expect(onOAuthCallback).not.toHaveBeenCalled();
    reg.unsubscribe();
  });

  it("ignores deep links that don't parse and logs a warning", () => {
    const onOAuthCallback = vi.fn();
    const logger = { info: vi.fn(), warn: vi.fn() };
    const reg = registerDeepLinks({ onOAuthCallback, logger });

    fakeApp.emit("open-url", fakeOpenUrlEvent(), "https://attacker.example/x");

    expect(onOAuthCallback).not.toHaveBeenCalled();
    expect(logger.warn).toHaveBeenCalledWith(
      "ignored non-enterprise url",
      expect.objectContaining({
        source: "open-url",
        rawUrl: "https://attacker.example/x",
      }),
    );
    reg.unsubscribe();
  });

  it("calls event.preventDefault on open-url so macOS doesn't try to open it", () => {
    const reg = registerDeepLinks({ onOAuthCallback: () => {} });
    const event = fakeOpenUrlEvent();

    fakeApp.emit(
      "open-url",
      event,
      "enterprise://oauth/callback?code=C&state=S",
    );

    expect(event.preventDefault).toHaveBeenCalled();
    reg.unsubscribe();
  });

  it("second-instance with no enterprise:// argv is silently ignored", () => {
    const onOAuthCallback = vi.fn();
    const logger = { info: vi.fn(), warn: vi.fn() };
    const reg = registerDeepLinks({ onOAuthCallback, logger });

    fakeApp.emit("second-instance", {} as FakeEvent, [
      "electron",
      "--some-flag",
    ]);

    expect(onOAuthCallback).not.toHaveBeenCalled();
    expect(logger.warn).not.toHaveBeenCalled();
    reg.unsubscribe();
  });

  it("unsubscribe removes both listeners (no further dispatches)", () => {
    const onOAuthCallback = vi.fn();
    const reg = registerDeepLinks({ onOAuthCallback });
    reg.unsubscribe();

    fakeApp.emit(
      "open-url",
      fakeOpenUrlEvent(),
      "enterprise://oauth/callback?code=C&state=S",
    );

    expect(onOAuthCallback).not.toHaveBeenCalled();
  });

  it("works without onOAuthCallback (Phase 5A wires the handler later)", () => {
    const logger = { info: vi.fn(), warn: vi.fn() };
    const reg = registerDeepLinks({ logger });

    fakeApp.emit(
      "open-url",
      fakeOpenUrlEvent(),
      "enterprise://oauth/callback?code=C&state=S",
    );

    expect(logger.warn).toHaveBeenCalledWith(
      "oauth callback missing code/state or no handler",
      expect.objectContaining({ hasLoginHandler: false }),
    );
    reg.unsubscribe();
  });
});

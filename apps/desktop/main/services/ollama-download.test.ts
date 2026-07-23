// @vitest-environment node

// PRD-P8 §8 — the "Get Ollama ↗" external-open channel.
//
// The security property under test is NOT "the handler validates the URL" but
// "there is no URL to validate": the channel takes no argument and can only
// ever reach `OLLAMA_DOWNLOAD_URL`. The hostile-payload cases below are the
// regression guard — if someone ever "helpfully" adds a `{ url }` parameter,
// they fail here.

import { describe, expect, it, vi } from "vitest";

import { FIRST_RUN_CHANNELS } from "./first-run-channels";
import {
  OLLAMA_DOWNLOAD_URL,
  registerOllamaDownloadIpc,
  type OpenOllamaDownloadResult,
} from "./ollama-download";

type Handler = (event: unknown, payload: unknown) => unknown;

function makeFakeIpcMain() {
  const handlers = new Map<string, Handler>();
  return {
    handle(channel: string, fn: Handler) {
      handlers.set(channel, fn);
    },
    removeHandler(channel: string) {
      handlers.delete(channel);
    },
    has(channel: string) {
      return handlers.has(channel);
    },
    async invoke(channel: string, payload?: unknown): Promise<unknown> {
      const fn = handlers.get(channel);
      if (!fn) throw new Error(`no handler for ${channel}`);
      return fn({ sender: { id: 1 } }, payload);
    },
  };
}

const quietLogger = { warn: () => undefined };

describe("registerOllamaDownloadIpc", () => {
  it("registers the first-run open-ollama-download channel", () => {
    const ipcMain = makeFakeIpcMain();
    registerOllamaDownloadIpc({
      ipcMain,
      openExternal: vi.fn(async () => undefined),
    });
    expect(ipcMain.has(FIRST_RUN_CHANNELS.openOllamaDownload)).toBe(true);
  });

  it("opens the constant download URL", async () => {
    const ipcMain = makeFakeIpcMain();
    const openExternal = vi.fn(async () => undefined);
    registerOllamaDownloadIpc({ ipcMain, openExternal });

    const result = (await ipcMain.invoke(
      FIRST_RUN_CHANNELS.openOllamaDownload,
    )) as OpenOllamaDownloadResult;

    expect(openExternal).toHaveBeenCalledTimes(1);
    expect(openExternal).toHaveBeenCalledWith(OLLAMA_DOWNLOAD_URL);
    expect(OLLAMA_DOWNLOAD_URL).toBe("https://ollama.com/download");
    expect(result).toEqual({ ok: true });
  });

  it("opens ONLY the constant — no renderer payload can influence the destination", async () => {
    const ipcMain = makeFakeIpcMain();
    const openExternal = vi.fn(async () => undefined);
    registerOllamaDownloadIpc({ ipcMain, openExternal });

    // Every shape a hostile renderer might try to smuggle a destination in.
    const hostilePayloads: readonly unknown[] = [
      { url: "https://evil.example/steal" },
      "https://evil.example/steal",
      ["https://evil.example/steal"],
      { url: "file:///etc/passwd" },
      { href: "javascript:alert(1)" },
      { url: OLLAMA_DOWNLOAD_URL.replace("ollama.com", "ollama.com.evil.io") },
      { 0: "https://evil.example" },
      null,
      undefined,
    ];

    for (const payload of hostilePayloads) {
      await ipcMain.invoke(FIRST_RUN_CHANNELS.openOllamaDownload, payload);
    }

    expect(openExternal).toHaveBeenCalledTimes(hostilePayloads.length);
    for (const call of openExternal.mock.calls) {
      expect(call).toEqual([OLLAMA_DOWNLOAD_URL]);
    }
  });

  it("resolves { ok:false } instead of rejecting when the shell open fails", async () => {
    const ipcMain = makeFakeIpcMain();
    registerOllamaDownloadIpc({
      ipcMain,
      openExternal: vi.fn(async () => {
        throw new Error("no handler for https");
      }),
      logger: quietLogger,
    });

    const result = (await ipcMain.invoke(
      FIRST_RUN_CHANNELS.openOllamaDownload,
    )) as OpenOllamaDownloadResult;

    expect(result.ok).toBe(false);
    expect(result.error).toBe("no handler for https");
  });

  it("teardown removes the handler", async () => {
    const ipcMain = makeFakeIpcMain();
    const teardown = registerOllamaDownloadIpc({
      ipcMain,
      openExternal: vi.fn(async () => undefined),
    });
    teardown();
    expect(ipcMain.has(FIRST_RUN_CHANNELS.openOllamaDownload)).toBe(false);
  });
});

describe("first-run channel allowlist", () => {
  it("carries the new channel, so the preload bridge admits it", async () => {
    const { isFirstRunChannel } = await import("./first-run-channels");
    expect(isFirstRunChannel(FIRST_RUN_CHANNELS.openOllamaDownload)).toBe(true);
    expect(isFirstRunChannel("first-run.open-anything")).toBe(false);
  });
});

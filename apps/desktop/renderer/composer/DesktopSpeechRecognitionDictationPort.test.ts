import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DesktopSpeechRecognitionDictationPort,
  desktopSpeechRecognitionSupported,
} from "./DesktopSpeechRecognitionDictationPort";

class FakeRecognition {
  static latest: FakeRecognition | null = null;

  lang = "";
  continuous = false;
  interimResults = false;
  start = vi.fn();
  stop = vi.fn();
  abort = vi.fn();
  private readonly listeners = new Map<
    string,
    Array<(event: unknown) => void>
  >();

  constructor() {
    FakeRecognition.latest = this;
  }

  addEventListener(type: string, listener: (event: unknown) => void): void {
    const existing = this.listeners.get(type) ?? [];
    existing.push(listener);
    this.listeners.set(type, existing);
  }

  emit(type: string, event: unknown = {}): void {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

const speechHost = window as unknown as Record<string, unknown>;
const originalSpeechRecognition = speechHost["SpeechRecognition"];
const originalWebkitSpeechRecognition = speechHost["webkitSpeechRecognition"];

beforeEach(() => {
  FakeRecognition.latest = null;
  speechHost["SpeechRecognition"] = FakeRecognition;
  delete speechHost["webkitSpeechRecognition"];
});

afterEach(() => {
  if (originalSpeechRecognition === undefined) {
    delete speechHost["SpeechRecognition"];
  } else {
    speechHost["SpeechRecognition"] = originalSpeechRecognition;
  }
  if (originalWebkitSpeechRecognition === undefined) {
    delete speechHost["webkitSpeechRecognition"];
  } else {
    speechHost["webkitSpeechRecognition"] = originalWebkitSpeechRecognition;
  }
});

function callbacks() {
  return {
    onStart: vi.fn(),
    onTranscript: vi.fn(),
    onEnd: vi.fn(),
    onError: vi.fn(),
  };
}

describe("DesktopSpeechRecognitionDictationPort", () => {
  it("reports support when Electron exposes SpeechRecognition", () => {
    expect(desktopSpeechRecognitionSupported()).toBe(true);
  });

  it("streams interim/final transcript and stops cleanly", () => {
    const cb = callbacks();
    const session = new DesktopSpeechRecognitionDictationPort().start(cb);
    const recognition = FakeRecognition.latest;
    expect(recognition).not.toBeNull();
    expect(recognition?.continuous).toBe(true);
    expect(recognition?.interimResults).toBe(true);
    expect(recognition?.start).toHaveBeenCalledTimes(1);

    recognition?.emit("start");
    expect(cb.onStart).toHaveBeenCalledTimes(1);

    const interim = Object.assign([{ transcript: "hello" }], {
      isFinal: false,
    });
    const final = Object.assign([{ transcript: "hello world" }], {
      isFinal: true,
    });
    recognition?.emit("result", { resultIndex: 0, results: [interim, final] });
    expect(cb.onTranscript).toHaveBeenNthCalledWith(1, {
      transcript: "hello",
      isFinal: false,
    });
    expect(cb.onTranscript).toHaveBeenNthCalledWith(2, {
      transcript: "hello world",
      isFinal: true,
    });

    session.stop();
    expect(recognition?.stop).toHaveBeenCalledTimes(1);
    recognition?.emit("end");
    expect(cb.onEnd).toHaveBeenCalledWith("stopped");
  });

  it("maps permission denial to safe user-facing copy", () => {
    const cb = callbacks();
    new DesktopSpeechRecognitionDictationPort().start(cb);
    FakeRecognition.latest?.emit("error", { error: "not-allowed" });
    FakeRecognition.latest?.emit("end");

    expect(cb.onError).toHaveBeenCalledWith(
      "Microphone access was denied. Enable it in System Settings and try again.",
    );
    expect(cb.onEnd).toHaveBeenCalledWith("error");
  });

  it("cancels without surfacing an aborted error", () => {
    const cb = callbacks();
    const session = new DesktopSpeechRecognitionDictationPort().start(cb);
    session.cancel();
    expect(FakeRecognition.latest?.abort).toHaveBeenCalledTimes(1);
    FakeRecognition.latest?.emit("error", { error: "aborted" });
    FakeRecognition.latest?.emit("end");

    expect(cb.onError).not.toHaveBeenCalled();
    expect(cb.onEnd).toHaveBeenCalledWith("cancelled");
  });
});

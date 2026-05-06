import type { DictationAdapter } from "../types";

/**
 * Atlas Web Speech dictation adapter. Wraps the browser's
 * `SpeechRecognition` API (webkit-prefixed in Chrome/Safari/Edge) into
 * the runtime's `DictationAdapter` shape so the composer mic button
 * can drive transcription without depending on `@assistant-ui/react`'s
 * adapter implementation.
 *
 * Static `isSupported()` lets the composer hide the mic when the
 * browser lacks the API entirely. Calling `listen()` on an unsupported
 * browser throws synchronously so the call site can render a fallback.
 */

// The Web Speech API's `SpeechRecognition` constructor. We avoid declaring
// global window augmentations here (they collide with the typings shipped
// alongside other libs) and instead read the constructor through an
// untyped indirection. Treating the recognition instance as a loose object
// with `addEventListener` is fine — we only ever cross the boundary once.
type RecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  abort(): void;
  addEventListener(type: string, listener: (event: unknown) => void): void;
};

type RecognitionCtor = new () => RecognitionLike;

function getSpeechRecognitionAPI(): RecognitionCtor | undefined {
  if (typeof window === "undefined") {
    return undefined;
  }
  const w = window as unknown as Record<string, unknown>;
  return (w.SpeechRecognition ?? w.webkitSpeechRecognition) as
    | RecognitionCtor
    | undefined;
}

export interface AtlasDictationOptions {
  language?: string;
  continuous?: boolean;
  interimResults?: boolean;
}

type SessionStatus =
  | { type: "starting" }
  | { type: "running" }
  | { type: "ended"; reason: "stopped" | "cancelled" | "error" };

export class AtlasWebSpeechDictationAdapter implements DictationAdapter {
  private readonly language: string;
  private readonly continuous: boolean;
  private readonly interimResults: boolean;

  constructor(options: AtlasDictationOptions = {}) {
    const fallbackLanguage =
      typeof navigator !== "undefined" && navigator.language
        ? navigator.language
        : "en-US";
    this.language = options.language ?? fallbackLanguage;
    this.continuous = options.continuous ?? true;
    this.interimResults = options.interimResults ?? true;
  }

  public static isSupported(): boolean {
    return getSpeechRecognitionAPI() !== undefined;
  }

  public listen(): ReturnType<DictationAdapter["listen"]> {
    const RecognitionCtorRef = getSpeechRecognitionAPI();
    if (!RecognitionCtorRef) {
      throw new Error(
        "SpeechRecognition is not supported in this browser. Try Chrome, Edge, or Safari.",
      );
    }
    const recognition = new RecognitionCtorRef();
    recognition.lang = this.language;
    recognition.continuous = this.continuous;
    recognition.interimResults = this.interimResults;

    const speechStartCallbacks = new Set<() => void>();
    const speechEndCallbacks = new Set<
      (payload: { transcript: string }) => void
    >();
    const speechCallbacks = new Set<
      (payload: { transcript: string; isFinal: boolean }) => void
    >();
    let finalTranscript = "";

    const session = {
      status: { type: "starting" } as SessionStatus,
      stop: async (): Promise<void> => {
        recognition.stop();
        await new Promise<void>((resolve) => {
          const tick = (): void => {
            if (session.status.type === "ended") {
              resolve();
              return;
            }
            window.setTimeout(tick, 50);
          };
          tick();
        });
      },
      cancel: (): void => {
        recognition.abort();
      },
      onSpeechStart: (callback: () => void) => {
        speechStartCallbacks.add(callback);
        return () => {
          speechStartCallbacks.delete(callback);
        };
      },
      onSpeechEnd: (callback: (payload: { transcript: string }) => void) => {
        speechEndCallbacks.add(callback);
        return () => {
          speechEndCallbacks.delete(callback);
        };
      },
      onSpeech: (
        callback: (payload: { transcript: string; isFinal: boolean }) => void,
      ) => {
        speechCallbacks.add(callback);
        return () => {
          speechCallbacks.delete(callback);
        };
      },
    };

    recognition.addEventListener("speechstart", () => {
      for (const cb of speechStartCallbacks) cb();
    });
    recognition.addEventListener("start", () => {
      session.status = { type: "running" };
    });
    recognition.addEventListener("result", (rawEvent) => {
      const event = rawEvent as {
        resultIndex: number;
        results: ArrayLike<{
          isFinal: boolean;
          length: number;
          [index: number]: { transcript: string };
        }>;
      };
      for (
        let index = event.resultIndex;
        index < event.results.length;
        index += 1
      ) {
        const result = event.results[index];
        if (!result) continue;
        const transcript = result[0]?.transcript ?? "";
        if (result.isFinal) {
          finalTranscript += transcript;
          for (const cb of speechCallbacks) cb({ transcript, isFinal: true });
        } else {
          for (const cb of speechCallbacks) cb({ transcript, isFinal: false });
        }
      }
    });
    recognition.addEventListener("speechend", () => {
      // 'end' handles final cleanup
    });
    recognition.addEventListener("end", () => {
      if (session.status.type !== "ended") {
        session.status = { type: "ended", reason: "stopped" };
      }
      if (finalTranscript) {
        for (const cb of speechEndCallbacks) {
          cb({ transcript: finalTranscript });
        }
        finalTranscript = "";
      }
    });
    recognition.addEventListener("error", (rawEvent) => {
      const errorEvent = rawEvent as { error: string; message?: string };
      if (errorEvent.error === "aborted") {
        session.status = { type: "ended", reason: "cancelled" };
      } else {
        session.status = { type: "ended", reason: "error" };
        if (typeof console !== "undefined") {
          console.error(
            "Dictation error:",
            errorEvent.error,
            errorEvent.message,
          );
        }
      }
    });

    try {
      recognition.start();
    } catch (error) {
      session.status = { type: "ended", reason: "error" };
      throw error;
    }

    return session as ReturnType<DictationAdapter["listen"]>;
  }
}

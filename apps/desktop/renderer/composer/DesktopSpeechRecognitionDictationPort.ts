import type {
  DictationCallbacks,
  DictationEndReason,
  DictationPort,
  DictationSession,
} from "@0x-copilot/chat-surface";

interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  abort(): void;
  addEventListener(type: string, listener: (event: unknown) => void): void;
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

function recognitionConstructor(): SpeechRecognitionConstructor | undefined {
  if (typeof window === "undefined") return undefined;
  const host = window as unknown as Record<string, unknown>;
  const ctor = host["SpeechRecognition"] ?? host["webkitSpeechRecognition"];
  return typeof ctor === "function"
    ? (ctor as SpeechRecognitionConstructor)
    : undefined;
}

export function desktopSpeechRecognitionSupported(): boolean {
  return recognitionConstructor() !== undefined;
}

function safeDictationError(error: string): string {
  switch (error) {
    case "not-allowed":
    case "service-not-allowed":
      return "Microphone access was denied. Enable it in System Settings and try again.";
    case "audio-capture":
      return "No microphone is available.";
    case "no-speech":
      return "No speech was detected. Try again.";
    case "network":
      return "Voice transcription is unavailable right now.";
    default:
      return "Voice input couldn't continue. Please try again.";
  }
}

export class DesktopSpeechRecognitionDictationPort implements DictationPort {
  public start(callbacks: DictationCallbacks): DictationSession {
    const Recognition = recognitionConstructor();
    if (Recognition === undefined) {
      throw new Error("Speech recognition is unavailable");
    }

    const recognition = new Recognition();
    recognition.lang = navigator.language || "en-US";
    recognition.continuous = true;
    recognition.interimResults = true;

    let endReason: DictationEndReason = "stopped";
    let ended = false;
    const finish = (): void => {
      if (ended) return;
      ended = true;
      callbacks.onEnd(endReason);
    };

    recognition.addEventListener("start", () => {
      callbacks.onStart();
    });
    recognition.addEventListener("result", (rawEvent) => {
      const event = rawEvent as {
        readonly resultIndex: number;
        readonly results: ArrayLike<{
          readonly isFinal: boolean;
          readonly length: number;
          readonly [index: number]: { readonly transcript: string };
        }>;
      };
      for (
        let index = event.resultIndex;
        index < event.results.length;
        index += 1
      ) {
        const result = event.results[index];
        if (result === undefined) continue;
        callbacks.onTranscript({
          transcript: result[0]?.transcript ?? "",
          isFinal: result.isFinal,
        });
      }
    });
    recognition.addEventListener("error", (rawEvent) => {
      const error = (rawEvent as { readonly error?: unknown }).error;
      if (error === "aborted") {
        endReason = "cancelled";
        return;
      }
      endReason = "error";
      callbacks.onError(
        safeDictationError(typeof error === "string" ? error : "unknown"),
      );
    });
    recognition.addEventListener("end", finish);

    recognition.start();

    return {
      stop: (): void => {
        endReason = "stopped";
        try {
          recognition.stop();
        } catch {
          endReason = "error";
          callbacks.onError("Voice input couldn't stop cleanly.");
          finish();
        }
      },
      cancel: (): void => {
        endReason = "cancelled";
        try {
          recognition.abort();
        } catch {
          finish();
        }
      },
    };
  }
}

/**
 * Renderer singleton: Electron 43 exposes SpeechRecognition in the isolated
 * renderer. Tests/non-browser consumers receive `undefined`, which keeps the
 * shared microphone honestly unavailable.
 */
export const desktopDictationPort: DictationPort | undefined =
  desktopSpeechRecognitionSupported()
    ? new DesktopSpeechRecognitionDictationPort()
    : undefined;

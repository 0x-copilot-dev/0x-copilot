/**
 * Host-owned speech-to-text capability for the shared composer.
 *
 * The chat surface owns text composition and the listening UI, but it must not
 * reach for browser/Electron globals. Hosts bind those substrate APIs behind
 * this narrow callback port instead.
 */
export interface DictationTranscript {
  readonly transcript: string;
  readonly isFinal: boolean;
}

export type DictationEndReason = "stopped" | "cancelled" | "error";

export interface DictationCallbacks {
  readonly onStart: () => void;
  readonly onTranscript: (payload: DictationTranscript) => void;
  readonly onEnd: (reason: DictationEndReason) => void;
  /**
   * A safe, user-facing message. Host adapters must not include raw device,
   * permission, or service payloads here.
   */
  readonly onError: (message: string) => void;
}

export interface DictationSession {
  /** Finish the current utterance and retain its transcript. */
  readonly stop: () => void | Promise<void>;
  /** Abort immediately, retaining whatever the composer already received. */
  readonly cancel: () => void;
}

export interface DictationPort {
  readonly start: (callbacks: DictationCallbacks) => DictationSession;
}

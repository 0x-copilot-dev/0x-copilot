// The single in-app notification surface for both substrates.
//
// Every failed action mutation (a rejected run-start, a connector connect 500, a
// profile save error) routes through ONE `useNotify()` API so users always get a
// visible signal instead of a silent no-op. It is an IN-PACKAGE provider — not a
// substrate `NotificationPort` — because in-app toasts are pure React state +
// timers with no host-specific primitive (contrast the native-OS `NotificationPort`,
// which genuinely differs per substrate). Both hosts mount it once and render one
// `<ToastStack/>`.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

export type NotifyTone = "error" | "success" | "info";

export interface NotifyAction {
  readonly label: string;
  readonly onClick: () => void;
}

export interface NotifyInput {
  readonly tone: NotifyTone;
  readonly title: string;
  readonly body?: string;
  /**
   * Auto-dismiss delay. `undefined` → tone default (errors sticky, others ~4s);
   * `null` → never auto-dismiss.
   */
  readonly durationMs?: number | null;
  readonly action?: NotifyAction;
}

export interface AppNotification extends NotifyInput {
  readonly id: string;
}

export interface NotificationCenter {
  readonly notifications: readonly AppNotification[];
  /** Enqueue a notification; returns its id. */
  notify(input: NotifyInput): string;
  dismiss(id: string): void;
}

const DEFAULT_AUTO_DISMISS_MS = 4000;

// Safe no-op default (mirrors useDeploymentProfile): components/tests without a
// provider never throw; production hosts mount the real one.
const NOOP: NotificationCenter = {
  notifications: [],
  notify: () => "",
  dismiss: () => undefined,
};

const NotificationCenterContext = createContext<NotificationCenter>(NOOP);

/** Default auto-dismiss per tone: errors stay until dismissed/actioned. */
function defaultDuration(tone: NotifyTone): number | null {
  return tone === "error" ? null : DEFAULT_AUTO_DISMISS_MS;
}

export function NotificationCenterProvider({
  children,
}: {
  readonly children: ReactNode;
}): ReactElement {
  const [notifications, setNotifications] = useState<
    readonly AppNotification[]
  >([]);
  const seq = useRef(0);
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: string): void => {
    const timer = timers.current.get(id);
    if (timer !== undefined) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
    setNotifications((prev) => prev.filter((n) => n.id !== id));
  }, []);

  const notify = useCallback(
    (input: NotifyInput): string => {
      seq.current += 1;
      const id = `ntf_${seq.current}`;
      setNotifications((prev) => [...prev, { ...input, id }]);
      const duration =
        input.durationMs === undefined
          ? defaultDuration(input.tone)
          : input.durationMs;
      if (duration !== null) {
        timers.current.set(
          id,
          setTimeout(() => dismiss(id), duration),
        );
      }
      return id;
    },
    [dismiss],
  );

  // Clear any outstanding timers when the provider unmounts.
  useEffect(() => {
    const map = timers.current;
    return () => {
      for (const timer of map.values()) clearTimeout(timer);
      map.clear();
    };
  }, []);

  const value = useMemo<NotificationCenter>(
    () => ({ notifications, notify, dismiss }),
    [notifications, notify, dismiss],
  );

  return (
    <NotificationCenterContext.Provider value={value}>
      {children}
    </NotificationCenterContext.Provider>
  );
}

/** Full center — used by `<ToastStack/>`. */
export function useNotificationCenter(): NotificationCenter {
  return useContext(NotificationCenterContext);
}

/** The common case: enqueue a notification. Safe no-op without a provider. */
export function useNotify(): (input: NotifyInput) => string {
  return useContext(NotificationCenterContext).notify;
}

/**
 * Best-effort human message from a rejected mutation, for a toast body. The
 * facade returns `{ detail: { safe_message, code, … } }` (or `{ detail: "…" }`),
 * and transport/IPC often wraps it as `"…: Error: {json}"` — dig out the
 * `safe_message` so the user sees the actionable line, not the raw envelope.
 */
export function messageFromError(err: unknown): string | undefined {
  const raw =
    err instanceof Error
      ? err.message
      : typeof err === "string"
        ? err
        : undefined;
  if (raw === undefined || raw === "") return raw;
  const start = raw.indexOf("{");
  const end = raw.lastIndexOf("}");
  if (start >= 0 && end > start) {
    try {
      const parsed: unknown = JSON.parse(raw.slice(start, end + 1));
      const detail =
        parsed !== null && typeof parsed === "object" && "detail" in parsed
          ? (parsed as { detail: unknown }).detail
          : parsed;
      if (typeof detail === "string" && detail !== "") return detail;
      if (detail !== null && typeof detail === "object") {
        const safe = (detail as { safe_message?: unknown }).safe_message;
        if (typeof safe === "string" && safe !== "") return safe;
      }
    } catch {
      /* not JSON — fall through to the raw message */
    }
  }
  return raw;
}

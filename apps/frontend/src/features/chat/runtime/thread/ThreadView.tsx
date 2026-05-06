import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import type { ThreadMessageLike } from "../types";

/**
 * Atlas thread root. Replaces `ThreadPrimitive.Root` from
 * `@assistant-ui/react`. Pure layout container — child components
 * (`ThreadViewport`, `ThreadEmpty`, `ThreadMessages`) handle their own
 * concerns.
 */
export function ThreadRoot({
  className,
  children,
}: {
  className?: string;
  children?: ReactNode;
}): ReactElement {
  return <div className={className}>{children}</div>;
}

/**
 * Scrollable thread viewport. Replaces `ThreadPrimitive.Viewport`.
 *
 * Owns the auto-scroll behaviour: when new content arrives we scroll
 * to bottom IFF the user is already near the bottom (within 64px).
 * That preserves the user's read position when they've scrolled up to
 * inspect earlier messages while a run is streaming.
 */
export function ThreadViewport({
  className,
  scrollKey,
  children,
  onAtBottomChange,
}: {
  className?: string;
  /**
   * A scalar that increments whenever the message list changes
   * meaningfully — usually `messages.length` plus a serialised tail
   * of the last message's content length so streaming deltas trigger
   * scroll.
   */
  scrollKey?: number | string;
  children?: ReactNode;
  /**
   * Fires whenever the user scrolls into / out of the bottom band.
   * The host uses it to show / hide the floating ScrollToBottom button.
   */
  onAtBottomChange?: (atBottom: boolean) => void;
}): ReactElement {
  const ref = useRef<HTMLDivElement | null>(null);
  const atBottomRef = useRef(true);

  const isNearBottom = useCallback((el: HTMLDivElement): boolean => {
    return el.scrollHeight - el.scrollTop - el.clientHeight < 64;
  }, []);

  // After every layout caused by `scrollKey` change, re-pin to bottom
  // if we were at bottom before the change. Use layout effect so we
  // measure pre-paint, avoiding flicker.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (atBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [scrollKey]);

  const handleScroll = useCallback((): void => {
    const el = ref.current;
    if (!el) return;
    const next = isNearBottom(el);
    if (next !== atBottomRef.current) {
      atBottomRef.current = next;
      onAtBottomChange?.(next);
    }
  }, [isNearBottom, onAtBottomChange]);

  return (
    <div
      ref={ref}
      className={className}
      onScroll={handleScroll}
      data-thread-viewport
    >
      {children}
    </div>
  );
}

/**
 * Renders `children` only when the thread is empty. Replaces
 * `ThreadPrimitive.Empty`. Caller passes `isEmpty` directly so this
 * has no implicit context coupling.
 */
export function ThreadEmpty({
  isEmpty,
  children,
}: {
  isEmpty: boolean;
  children?: ReactNode;
}): ReactElement | null {
  if (!isEmpty) return null;
  return <>{children}</>;
}

/**
 * Iterates the message list and calls the render-prop for each.
 * Replaces `ThreadPrimitive.Messages`. The shape of the value handed
 * to the render-prop is the message itself plus an `isEditing` flag —
 * matching the assistant-ui contract callers were already coding to.
 */
export interface ThreadMessageRenderValue {
  readonly message: ThreadMessageLike;
  readonly isEditing: boolean;
}

export function ThreadMessages({
  messages,
  editingMessageId,
  children,
}: {
  messages: readonly ThreadMessageLike[];
  /** Id of the user message currently being inline-edited, if any. */
  editingMessageId?: string | null;
  children: (value: ThreadMessageRenderValue) => ReactNode;
}): ReactElement {
  return (
    <>
      {messages.map((message, index) => {
        const isEditing =
          editingMessageId !== undefined &&
          editingMessageId !== null &&
          message.id === editingMessageId;
        return (
          <div
            key={message.id ?? `m-${index}`}
            data-message-id={message.id}
            data-message-role={message.role}
          >
            {children({ message, isEditing })}
          </div>
        );
      })}
    </>
  );
}

/**
 * Floating "scroll to bottom" button. Replaces
 * `ThreadPrimitive.ScrollToBottom`. Shows only when the viewport is
 * NOT at the bottom; click jumps the nearest viewport to the bottom.
 */
export function ThreadScrollToBottom({
  className,
  visible,
  title,
  children,
}: {
  className?: string;
  visible: boolean;
  title?: string;
  children?: ReactNode;
}): ReactElement | null {
  const ref = useRef<HTMLButtonElement | null>(null);
  const [, forceRender] = useState(0);
  // Re-render when `visible` changes so screen readers announce.
  useEffect(() => {
    forceRender((tick) => tick + 1);
  }, [visible]);
  if (!visible) return null;
  return (
    <button
      ref={ref}
      type="button"
      className={className}
      title={title}
      onClick={() => {
        const button = ref.current;
        if (!button) return;
        // Find the nearest ancestor scroll container (the ThreadViewport).
        let node: HTMLElement | null = button.parentElement;
        while (node && !node.matches("[data-thread-viewport]")) {
          node = node.parentElement;
        }
        if (node) {
          node.scrollTop = node.scrollHeight;
        }
      }}
    >
      {children}
    </button>
  );
}

import type { MessagePartStatus } from "./types";

// CSS class hooks that `apps/frontend/src/styles.css`'s
// `@media (prefers-reduced-motion: reduce)` rule keys off of.
//
// The base class styles the rendered markdown; the `--streaming` modifier
// means "this text is mid-stream and animating." Consumers always apply
// the base; the modifier is added/removed by `streamingCursorProps()`
// based on `MessagePartStatus`.
const STREAMING_MARKDOWN_CLASS = "assistant-markdown";
const STREAMING_MARKDOWN_ACTIVE_CLASS = "assistant-markdown--streaming";

/**
 * Streamdown-shaped props that encode the "this text is still being
 * generated" visual contract. Spread onto a `<Streamdown>` element by
 * the caller, which is then free to layer renderer-specific config
 * (`components`, `remarkPlugins`, …) on top.
 */
export interface StreamingCursorProps {
  readonly className: string;
  readonly mode: "streaming" | "static";
  readonly isAnimating: boolean;
  readonly animated:
    | false
    | {
        readonly animation: "fadeIn";
        readonly duration: number;
        readonly easing: "ease-out";
        readonly sep: "word";
      };
}

/**
 * Single source of truth for the streaming-cursor affordance.
 *
 * Encapsulates three otherwise-duplicable pieces of behavior:
 *
 *   1. The `assistant-markdown--streaming` CSS class that styles.css's
 *      reduced-motion rule hangs off. Adding it elsewhere by hand
 *      bypasses the rule.
 *   2. Streamdown's `mode`/`isAnimating` props that drive its internal
 *      fade-in cursor.
 *   3. The fade-in animation config (`fadeIn`, 120ms, ease-out, per word)
 *      — tuned for chat assistant text. A second consumer hard-coding
 *      different numbers would visibly disagree with the original.
 *
 * Any future streaming-text surface (tool output panel, subagent
 * transcript, …) opts in by importing this helper, not by re-deriving
 * the toggle.
 */
export function streamingCursorProps(
  status: MessagePartStatus,
): StreamingCursorProps {
  const streaming = status.type === "running";
  const className = streaming
    ? `${STREAMING_MARKDOWN_CLASS} ${STREAMING_MARKDOWN_ACTIVE_CLASS}`
    : STREAMING_MARKDOWN_CLASS;
  return {
    className,
    mode: streaming ? "streaming" : "static",
    isAnimating: streaming,
    animated: streaming
      ? {
          animation: "fadeIn",
          duration: 120,
          easing: "ease-out",
          sep: "word",
        }
      : false,
  };
}

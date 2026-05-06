import type { ReactElement, ReactNode } from "react";

/**
 * Plain ARIA-toolbar root. Replaces `ActionBarPrimitive.Root` from
 * `@assistant-ui/react`. No focus-roving for now — the four buttons
 * Atlas renders in a footer are reachable in tab order and that's
 * sufficient until we ship a richer action bar.
 */
export function ActionBar({
  className,
  children,
}: {
  className?: string;
  children?: ReactNode;
}): ReactElement {
  return (
    <div role="toolbar" className={className}>
      {children}
    </div>
  );
}

/**
 * Copy-to-clipboard button. Calls `getText()` lazily on click so the
 * caller doesn't need to materialise the message text on every render
 * (assistant messages stream — we read the latest snapshot at the
 * moment the user clicks).
 */
export function ActionBarCopy({
  className,
  getText,
  children,
  ...rest
}: {
  className?: string;
  getText: () => string;
  children?: ReactNode;
  "aria-label"?: string;
  "data-tooltip"?: string;
  title?: string;
}): ReactElement {
  return (
    <button
      type="button"
      className={className}
      onClick={() => {
        const text = getText();
        if (typeof navigator === "undefined" || !navigator.clipboard) {
          return;
        }
        void navigator.clipboard.writeText(text);
      }}
      {...rest}
    >
      {children}
    </button>
  );
}

/**
 * Reload (regenerate) button. Calls `onReload()` on click. The host
 * (`ChatScreen`) is responsible for resolving the parent message and
 * issuing a new run.
 */
export function ActionBarReload({
  className,
  onReload,
  children,
  disabled,
  ...rest
}: {
  className?: string;
  onReload: () => void;
  children?: ReactNode;
  disabled?: boolean;
  "aria-label"?: string;
  "data-tooltip"?: string;
  title?: string;
}): ReactElement {
  return (
    <button
      type="button"
      className={className}
      onClick={onReload}
      disabled={disabled}
      {...rest}
    >
      {children}
    </button>
  );
}

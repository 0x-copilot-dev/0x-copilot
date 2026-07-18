import { useMemo, type ComponentProps, type ReactElement } from "react";
import { Streamdown, defaultRemarkPlugins } from "streamdown";

import { createRemarkCitations } from "./citationRemarkPlugin";
import { streamingCursorProps } from "./streamingCursor";
import type { TextMessagePartProps } from "./types";

type StreamdownComponents = ComponentProps<typeof Streamdown>["components"];

export interface MarkdownTextProps extends TextMessagePartProps {
  /**
   * Anchor/chip renderers handed to Streamdown (its `components.a` slot
   * routes the citation-remark plugin's `#cite-ord:` / `#cite:` anchors to
   * the host's chip dispatcher). Injected — not imported — so this module
   * never pulls in the substrate's citation wrappers and stays
   * app-import-free (FR-1.2).
   */
  readonly components?: StreamdownComponents;
  /**
   * Citation-diagnostics sink forwarded to the remark plugin's `onMatch`.
   * The host binds its logger (web wires `citationDebug`); optional so the
   * core renders standalone without a diagnostics dependency.
   */
  readonly onMatch?: (matches: readonly string[]) => void;
}

export function MarkdownText({
  text,
  status,
  components,
  onMatch,
}: MarkdownTextProps): ReactElement {
  // Construct the citation plugin once per stable `onMatch` identity. The
  // host supplies a module-scope `onMatch`, so the plugin identity stays
  // constant across renders — matching the pre-hoist module-load
  // construction that kept Streamdown seeing a consistent plugin.
  //
  // Streamdown only auto-includes its default remark plugins (GFM among
  // them) when NO `remarkPlugins` prop is supplied; passing our own list
  // replaces that default outright. Without GFM, a conversational table
  // streams out as raw `| pipe |` text instead of a parsed `<table>`
  // (FR-3.19's forbidden half-parsed leak). Re-spread `defaultRemarkPlugins`
  // ahead of the citation plugin so GFM tables/strikethrough/autolinks keep
  // working while the citation rewrite still runs over the parsed tree.
  const remarkPlugins = useMemo(
    () => [
      ...Object.values(defaultRemarkPlugins),
      createRemarkCitations(onMatch ? { onMatch } : undefined),
    ],
    [onMatch],
  );
  // streamingCursorProps owns the single source of truth for the
  // `assistant-markdown[--streaming]` class + Streamdown's
  // mode/isAnimating/animated triple. Any future streaming-text surface
  // (tool output, subagent transcript, …) imports the same helper rather
  // than re-deriving the toggle.
  return (
    <Streamdown
      {...streamingCursorProps(status)}
      components={components}
      remarkPlugins={remarkPlugins}
    >
      {text}
    </Streamdown>
  );
}

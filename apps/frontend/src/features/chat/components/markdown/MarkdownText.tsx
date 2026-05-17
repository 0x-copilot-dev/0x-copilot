import {
  createRemarkCitations,
  streamingCursorProps,
} from "@enterprise-search/chat-surface";
import type { ReactElement } from "react";
import { Streamdown } from "streamdown";

import { citationDebug } from "../../chatModel/citationDebug";
import type { TextMessagePartProps } from "../../runtime/types";
import { MarkdownLink } from "./MarkdownLink";

const markdownComponents = {
  a: MarkdownLink,
};

// Construct the citation plugin once at module load; the closure over
// citationDebug is stable so Streamdown sees a consistent plugin
// identity across renders.
const remarkPlugins = [
  createRemarkCitations({
    onMatch: (matches) =>
      citationDebug(`plugin.match tokens=${matches.length}`, matches),
  }),
];

export function MarkdownText({
  text,
  status,
}: TextMessagePartProps): ReactElement {
  // streamingCursorProps owns the single source of truth for the
  // `assistant-markdown[--streaming]` class + Streamdown's
  // mode/isAnimating/animated triple. Any future streaming-text surface
  // (tool output, subagent transcript, …) imports the same helper rather
  // than re-deriving the toggle.
  return (
    <Streamdown
      {...streamingCursorProps(status)}
      components={markdownComponents}
      remarkPlugins={remarkPlugins}
    >
      {text}
    </Streamdown>
  );
}

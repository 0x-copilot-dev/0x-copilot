// Web host adapter for the streaming-markdown renderer.
//
// The renderer core now lives in @0x-copilot/chat-surface so web and
// desktop render assistant text identically (PR-1.1). This adapter binds
// the web-substrate-specific injections the core takes as props:
//
//   1. `components.a` — the citation-chip dispatcher (`MarkdownLink`),
//      which resolves chips against apps/frontend's CitationsProvider.
//   2. `onMatch` — the diagnostics sink (`citationDebug`, a console
//      logger). A desktop adapter would wire its own telemetry sink.
//
// Binding these here keeps the moved core free of `citationDebug` / the
// web `MarkdownLink` imports (FR-1.2), so it stays substrate-agnostic.

import { MarkdownText as SurfaceMarkdownText } from "@0x-copilot/chat-surface";
import type { ReactElement } from "react";

import { citationDebug } from "../../chatModel/citationDebug";
import type { TextMessagePartProps } from "../../runtime/types";
import { MarkdownLink } from "./MarkdownLink";

// Stable module-scope identities so the core's memoized plugin + component
// map never churn across renders (matches the pre-hoist construction).
const markdownComponents = { a: MarkdownLink };

function onCitationMatch(matches: readonly string[]): void {
  citationDebug(`plugin.match tokens=${matches.length}`, matches);
}

export function MarkdownText(props: TextMessagePartProps): ReactElement {
  return (
    <SurfaceMarkdownText
      {...props}
      components={markdownComponents}
      onMatch={onCitationMatch}
    />
  );
}

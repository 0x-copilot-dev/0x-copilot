// Web host adapter for the Sources slash-command (`/sources`) overlay.
//
// The presentational panel now lives in @0x-copilot/chat-surface (PR-1.4) so
// web and desktop render the sources list identically. This adapter binds the
// two web-substrate-specific bits the headless panel leaves to the host:
//
//   1. Ordering — `sourcesByCitationCount` (the host-owned `chatModel`
//      sources reducer): citation_count desc, then last_cited_at desc.
//   2. The preview-wired `SourceRow` wrapper (binds `useSourcePreviewTrigger`,
//      a `createPortal`/`window` adapter that must stay in the host).
//
// Keeping both here keeps the moved core free of `chatModel/*` and the browser
// preview portal (FR-1.13 / FR-1.14), so it stays substrate-agnostic. The
// public API (`sources: SourceEntryMap`, `onClose`) is unchanged, so
// `DetailsPanelHost` and the panel tests keep resolving `SourcesPanel` here.

import { SourcesPanel as SurfaceSourcesPanel } from "@0x-copilot/chat-surface";
import type { ReactElement } from "react";

import {
  sourcesByCitationCount,
  type SourceEntryMap,
} from "../../chatModel/sourcesReducer";
import { SourceRow } from "../citations/SourceRow";

export interface SourcesPanelProps {
  sources: SourceEntryMap;
  onClose: () => void;
}

export function SourcesPanel({
  sources,
  onClose,
}: SourcesPanelProps): ReactElement {
  return (
    <SurfaceSourcesPanel
      sources={sourcesByCitationCount(sources)}
      onClose={onClose}
      SourceRowComponent={SourceRow}
    />
  );
}

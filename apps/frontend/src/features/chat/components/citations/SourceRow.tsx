// Web wrapper around the substrate-agnostic SourceRow in
// @enterprise-search/chat-surface.
//
// The headless row is a pure renderer that takes `previewProps` (mouse/
// focus handlers, aria attributes) as a prop. This wrapper owns the
// web-substrate-specific bit: resolving the preview-portal wiring via
// `useSourcePreviewTrigger`. Both apps/frontend consumers — SourcesPanel
// and SourcesTab — render this wrapper unchanged.
//
// The desktop substrate will write a parallel wrapper that either omits
// the preview entirely or routes through its own portal mechanism. The
// chat-surface row itself does not change.

import type { SourceEntry } from "@enterprise-search/api-types";
import { SourceRow as HeadlessSourceRow } from "@enterprise-search/chat-surface";
import { forwardRef, type ReactElement, type Ref } from "react";

import { useSourcePreviewTrigger } from "./SourcePreview";

export interface SourceRowProps {
  source: SourceEntry;
  ordinal: number;
  focused?: boolean;
  onSelect?: (source: SourceEntry) => void;
  onJumpToChat?: (source: SourceEntry) => void;
}

export const SourceRow = forwardRef(function SourceRow(
  props: SourceRowProps,
  ref: Ref<HTMLLIElement>,
): ReactElement {
  const previewProps = useSourcePreviewTrigger(props.source);
  return <HeadlessSourceRow ref={ref} {...props} previewProps={previewProps} />;
});

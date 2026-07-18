// Web host adapter for the markdown anchor dispatcher.
//
// The dispatcher logic (route `#cite-ord:` → OrdinalCitationChip,
// `#cite:` → CitationChip, else a plain `<a>` with a compacted label) now
// lives in @0x-copilot/chat-surface (PR-1.1). This adapter binds it to
// apps/frontend's citation-resolving chip wrappers, which resolve chips
// against the host's CitationsProvider. `isExternalHref` is re-exported so
// existing import sites (and the colocated test) keep resolving here.

import { createMarkdownLink, isExternalHref } from "@0x-copilot/chat-surface";

import { CitationChip } from "../citations/CitationChip";
import { OrdinalCitationChip } from "../citations/OrdinalCitationChip";

export const MarkdownLink = createMarkdownLink({
  CitationChip,
  OrdinalCitationChip,
});

export { isExternalHref };

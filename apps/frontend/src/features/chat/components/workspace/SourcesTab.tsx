// Web host adapter for the Workspace pane Sources tab.
//
// The tab body now lives in @0x-copilot/chat-surface (PR-1.7). This adapter
// binds the web preview-wired `SourceRow` wrapper (which resolves
// `useSourcePreviewTrigger`, a `createPortal`/`window` adapter that must stay
// in the host) so hover-preview behaves exactly as before. Everything else is
// passed straight through. `WorkspacePane` renders the chat-surface tab
// directly with the same slot injected, so this adapter only matters to any
// direct importer of `SourcesTab`.

import {
  SourcesTab as SurfaceSourcesTab,
  type SourcesTabProps,
} from "@0x-copilot/chat-surface";
import type { ReactElement } from "react";

import { SourceRow } from "../citations/SourceRow";

export type { SourcesTabProps };

export function SourcesTab(props: SourcesTabProps): ReactElement {
  return (
    <SurfaceSourcesTab
      {...props}
      SourceRowComponent={props.SourceRowComponent ?? SourceRow}
    />
  );
}

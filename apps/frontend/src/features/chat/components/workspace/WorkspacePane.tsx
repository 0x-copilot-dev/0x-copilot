// Web host adapter for the right-rail Workspace pane.
//
// The pane + its five tab bodies now live in @0x-copilot/chat-surface (PR-1.7)
// so web and desktop render the right rail identically. This adapter binds the
// one web-substrate-specific bit the headless pane leaves to the host: the
// preview-wired `SourceRow` wrapper (which binds `useSourcePreviewTrigger`, a
// `createPortal`/`window` adapter that must stay in the host). Everything else
// — pane state, the data-binding hooks, the draft PATCH/SEND POSTs, the
// jump-to-approval focus — is passed in by `ChatScreen` as props, unchanged.
//
// The public API (`WorkspacePaneProps`) is re-exported from chat-surface, so
// `ChatScreen` and the pane test keep resolving `WorkspacePane` here.

import {
  WorkspacePane as SurfaceWorkspacePane,
  type WorkspacePaneProps,
} from "@0x-copilot/chat-surface";
import type { ReactElement } from "react";

import { SourceRow } from "../citations/SourceRow";

export type { WorkspacePaneProps };

export function WorkspacePane(props: WorkspacePaneProps): ReactElement | null {
  return (
    <SurfaceWorkspacePane
      {...props}
      SourceRowComponent={props.SourceRowComponent ?? SourceRow}
    />
  );
}

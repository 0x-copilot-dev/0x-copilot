// C3 web host declaration.
//
// Browsers may record the canonical effect-stage decision through the shared
// Transport inside RunDestination, but they never receive a local workspace
// capability. Artifact download remains the existing ArtifactDownloadPort
// fallback supplied by RunRoute.

import type { WorkspaceStageHost } from "@0x-copilot/chat-surface";

export const WEB_WORKSPACE_STAGE_HOST: WorkspaceStageHost = Object.freeze({
  kind: "web",
});

import {
  isArtifactRenderState,
  type ArtifactRenderState,
} from "@0x-copilot/api-types";

export type { ArtifactRenderState };

export function artifactStateFor(
  state: unknown,
  kind: ArtifactRenderState["kind"],
): ArtifactRenderState | null {
  return isArtifactRenderState(state) && state.kind === kind ? state : null;
}

export function previewNotice(state: ArtifactRenderState): string | null {
  if (state.preview === "ready") return null;
  return state.notice ?? "This artifact cannot be safely previewed here.";
}

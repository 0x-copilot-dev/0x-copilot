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

export type ArtifactRevisionSaveOutcome = "saved" | "conflict" | "error";

/**
 * The host's permission to write, and the one call that writes.
 *
 * EDITABILITY IS NOT DATA. It is never decoded from artifact content and never
 * carried on a `SurfaceSpec` — the model authors specs, so a spec able to carry
 * a handler would be a model-authored side effect. A renderer becomes editable
 * only because the HOST attached this object to the render state, which the
 * host does for one origin at a time (an artifact it can already revise).
 */
export interface ArtifactEditorActions {
  readonly disabled: boolean;
  readonly saveRevision: (
    source: string,
  ) => Promise<ArtifactRevisionSaveOutcome>;
  /**
   * Scopes the editor's testids and field labels when the host can mount two
   * artifacts at once. Studio shows one and leaves it unset; the inline
   * transcript card can have two expanded, where a shared name makes
   * `getByLabelText` ambiguous and gives a screen reader two "Status, row 1"
   * fields on one page. Host-owned because only the host knows how many of
   * itself it mounted.
   */
  readonly idPrefix?: string;
}

/**
 * Reads a host-attached editor capability off the render state, or `null`.
 *
 * Duck-typed on purpose: `ArtifactRenderState` is the api-types wire contract
 * and this field is emphatically NOT on the wire — it is a live function the
 * host closed over its own transport. `null` is the read-only answer, and it is
 * the answer for every surface the host did not deliberately open.
 */
export function hostEditorActions(
  state: ArtifactRenderState,
  field: string,
): ArtifactEditorActions | null {
  const candidate = (state as unknown as Record<string, unknown>)[field];
  if (typeof candidate !== "object" || candidate === null) return null;
  const value = candidate as Partial<ArtifactEditorActions>;
  return typeof value.disabled === "boolean" &&
    typeof value.saveRevision === "function" &&
    (value.idPrefix === undefined || typeof value.idPrefix === "string")
    ? (value as ArtifactEditorActions)
    : null;
}

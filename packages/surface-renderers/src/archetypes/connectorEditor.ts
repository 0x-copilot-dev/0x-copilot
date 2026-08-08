import {
  CONNECTOR_EDITOR_FIELD,
  type ConnectorSurfaceEditorActions,
} from "@0x-copilot/chat-surface";

export type { ConnectorSurfaceEditorActions };

/**
 * Reads a host-attached connector-write grant off the render state, or `null`.
 *
 * The sibling of `hostEditorActions` in `artifacts/model.ts`, and duck-typed for
 * the same reason: `SurfaceState` is the api-types WIRE contract and this field
 * is emphatically not on the wire — it is a live function the host closed over
 * its own Transport. It is not on the `SurfaceSpec` either; the model authors
 * specs, so a spec able to carry a handler would be a model-authored side
 * effect.
 *
 * `null` is the read-only answer, and it is the answer for every surface the
 * host did not deliberately open. The check is structural rather than an
 * `instanceof` because the grant crosses a package boundary as plain data.
 */
export function hostConnectorEditor(
  state: unknown,
): ConnectorSurfaceEditorActions | null {
  if (typeof state !== "object" || state === null) return null;
  const candidate = (state as Record<string, unknown>)[CONNECTOR_EDITOR_FIELD];
  if (typeof candidate !== "object" || candidate === null) return null;
  const value = candidate as Partial<ConnectorSurfaceEditorActions>;
  return typeof value.disabled === "boolean" &&
    typeof value.surfaceId === "string" &&
    value.surfaceId !== "" &&
    typeof value.saveEdits === "function"
    ? (value as ConnectorSurfaceEditorActions)
    : null;
}

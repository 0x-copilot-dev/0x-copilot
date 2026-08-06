import type { ReactElement } from "react";
import {
  registerAdapter,
  type SaaSRendererAdapter,
} from "@0x-copilot/chat-surface";
import { ArtifactRenderer } from "./ArtifactRenderer";
import { artifactStateFor } from "./model";

const SCHEMES = [
  "artifact-code",
  "artifact-document",
  "artifact-dataset",
  "artifact-file",
] as const;
const KINDS = ["code", "document", "dataset", "file"] as const;

function adapterFor(index: number): SaaSRendererAdapter {
  const scheme = SCHEMES[index]!;
  const kind = KINDS[index]!;
  const render = (state: unknown): ReactElement => {
    const artifact = artifactStateFor(state, kind);
    return artifact === null
      ? ArtifactRenderer({
          artifact: {
            artifactId: "",
            kind,
            title: "Artifact unavailable",
            mediaType: "application/octet-stream",
            filename: "artifact",
            revision: 1,
            digest: "",
            byteSize: 0,
            author: "system",
            createdAt: "",
            preview: "error",
            notice: "Artifact data is unavailable.",
          },
        })
      : ArtifactRenderer({ artifact });
  };
  return {
    scheme,
    matches: (uri) => new RegExp(`^${scheme}://[^@/]+@[1-9][0-9]*$`).test(uri),
    renderCurrent: render,
    renderDiff: render,
    metadata: { origin: "first-party", schemaVersion: 1 },
  };
}

export const ARTIFACT_ADAPTERS = KINDS.map((_, index) => adapterFor(index));

export function registerArtifactAdapters(): void {
  for (const adapter of ARTIFACT_ADAPTERS) registerAdapter(adapter);
}

export { ArtifactRenderer } from "./ArtifactRenderer";
export { CodeArtifactRenderer } from "./CodeArtifactRenderer";
export {
  DatasetArtifactRenderer,
  datasetGrid,
  parseCsv,
  parseLosslessDelimited,
  serializeDelimitedPatch,
  serializeFormulaSafeDelimitedPatch,
  type DatasetEditorActions,
  type DatasetGrid,
  type DatasetPatch,
  type LosslessDelimitedDataset,
} from "./DatasetArtifactRenderer";
// PRD-03 D4 — cell-level diff of a revised dataset. `DatasetRevisionChange` is
// the payload a host attaches to the render state; the renderer mounts the
// panel itself, so a host wires nothing beyond that field.
export {
  DatasetRevisionDiff,
  datasetRevisionChangeFor,
  diffDatasetGrids,
  type DatasetDiffCell,
  type DatasetDiffRow,
  type DatasetRevisionCellDiff,
  type DatasetRevisionChange,
} from "./DatasetRevisionDiff";
export { DocumentArtifactRenderer } from "./DocumentArtifactRenderer";
// Phase 1 of the editable surface: the document renders as editable BLOCKS
// whenever the host attached `documentEditor`, and the raw-markdown textarea
// that used to sit under it is gone. `EditableDocument` is exported for hosts
// and tests that mount it directly; the renderer mounts it itself, so wiring is
// the capability object and nothing else.
export {
  EditableDocument,
  type EditableDocumentProps,
} from "./EditableDocument";
export {
  commitEdit,
  documentEditFor,
  documentEditsFor,
  nextCellTarget,
  originalValue,
  revertEdit,
  targetKey,
  type EditTarget,
  type PendingEdit,
  type PendingEdits,
} from "./documentEdits";
export { FileArtifactRenderer } from "./FileArtifactRenderer";
export { RawArtifactFallback } from "./RawArtifactFallback";
export {
  hostEditorActions,
  type ArtifactEditorActions,
  type ArtifactRenderState,
  type ArtifactRevisionSaveOutcome,
} from "./model";

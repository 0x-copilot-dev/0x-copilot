export {
  addBlockEdits,
  addColumnEdits,
  addRowEdits,
  applyEdits,
  blockContentEnd,
  blockEdit,
  cellEdit,
  deleteBlockEdits,
  deleteColumnEdits,
  deleteRowEdits,
  escapeTableCellText,
  headerCellEdit,
  parseBlocks,
  spliceBlock,
  spliceCell,
  spliceHeaderCell,
  swapBlocksEdits,
  unescapeTableCellText,
  type ColumnAlignment,
  type DocumentBlock,
  type DocumentBlockKind,
  type DocumentEdit,
  type EditableBlock,
  type HeadingBlock,
  type ParagraphBlock,
  type RawBlock,
  type RawBlockReason,
  type TableBlock,
  type TableCell,
} from "./blocks";
export { ArtifactDownloadAction } from "./ArtifactDownloadAction";
// `ArtifactEditor` is deleted. It was a raw-markdown `<textarea>` mounted BELOW
// the rendered artifact — the thing that made "change one cell" mean "find it
// in pipe-delimited source". Editing now happens on the rendered blocks
// themselves, inside the surface (`surface-renderers`' `EditableDocument`),
// reached by the `documentEditor` grant `ArtifactSurface` attaches.
export { ArtifactFrame } from "./ArtifactFrame";
export { ArtifactRevisionHistory } from "./ArtifactRevisionHistory";
export {
  ArtifactRevisionCompare,
  compareArtifactText,
  type ArtifactTextComparison,
} from "./ArtifactRevisionCompare";
export {
  ArtifactRevisionReview,
  REVIEWED_ARTIFACT_AUTHORS,
  type ArtifactRevisionReviewState,
} from "./ArtifactRevisionReview";
export { ArtifactSurface } from "./ArtifactSurface";
export {
  projectArtifactTabs,
  type ArtifactSurfaceTab,
} from "./artifactProjection";
export { artifactUri, parseArtifactSurfaceUri } from "./uri";

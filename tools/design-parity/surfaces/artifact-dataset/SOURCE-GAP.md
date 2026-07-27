# Artifact dataset parity target — exact-state gap, shared v3 grammar covered

State: `editor`.

The requested path (`/Users/parthpahwa/Downloads/copilot-project-folder-copy/Chat+%26+Tool+Calls.dc.html`) is not present. The available adjacent source is `/Users/parthpahwa/Downloads/copilot-project-folder-copy/project/Chat & Tool Calls.dc.html`.

That file contains a compact `forecast_q1.csv` analysis result, but not an artifact table/editor: it reports row/column counts and summary statistics in chat. It has no grid, filtering/sorting, virtual window, editable cells, or immutable revision controls. Treating that card as an exact design equivalent would manufacture parity evidence.

The repository now also includes the supplied Generative Surfaces v3 bulk-review
design. It is not an honest exact-state equivalent for a standalone CSV
artifact editor, but it does define the shared sheet chrome and action language
that the editor must reuse.

- `lib/render-live-artifact-dataset.test.tsx` renders the real `ArtifactFrame`,
  `DatasetArtifactRenderer`, and revision history into `live/editor.html` using
  the shipping design-system stylesheet.
- `anchors-v3-shared.json` maps the roles genuinely shared with the v3 design:
  sheet header, title, action bar, safety copy, and primary action.
- `out/report-v3-shared.md` is the computed-style result for those shared roles.
  It must have zero missing, HIGH, and MEDIUM findings.
- `anchors.json` retains stable production anchors for dataset-only filter,
  editable-grid, virtual-window, and revision controls. Those remain
  source-unmapped; this repository does not fabricate a full 1:1 design claim
  for controls absent from the supplied mock.

Generate the deterministic live target from the repository root:

```bash
node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs lib/render-live-artifact-dataset.test.tsx
```

# Artifact dataset parity target — source gap

State: `editor`.

The requested path (`/Users/parthpahwa/Downloads/copilot-project-folder-copy/Chat+%26+Tool+Calls.dc.html`) is not present. The available adjacent source is `/Users/parthpahwa/Downloads/copilot-project-folder-copy/project/Chat & Tool Calls.dc.html`.

That file contains a compact `forecast_q1.csv` analysis result, but not an artifact table/editor: it reports row/column counts and summary statistics in chat. It has no grid, filtering/sorting, virtual window, editable cells, or immutable revision controls. Treating that card as an exact design equivalent would manufacture parity evidence.

This target therefore records an explicit scoped fallback:

- `lib/render-live-artifact-dataset.test.tsx` renders the real `DatasetArtifactRenderer` into `live/editor.html` using the shipping design-system stylesheet.
- `anchors.json` defines stable production anchors for the card, view controls, virtualized grid, and revision actions, each marked as source-unmapped.
- There is intentionally no design extraction, comparison report, or zero-drift claim until a B2 artifact table/editor design source is supplied.

Generate the deterministic live target from the repository root:

```bash
node_modules/.bin/vitest run --config tools/design-parity/vitest.config.mjs lib/render-live-artifact-dataset.test.tsx
```

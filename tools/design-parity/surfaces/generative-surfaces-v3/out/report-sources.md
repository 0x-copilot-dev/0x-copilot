# Design-parity report — generative-surfaces-v3 · `sources`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/generative-surfaces-v3/out/design-sources.json`
- Live: `surfaces/generative-surfaces-v3/out/live-sources.json`

**Summary:** 🔴 HIGH 0 · 🟠 MEDIUM 0 · 🟡 LOW 3 · ⚪ INFO 8

## 🟡 LOW (3)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `sources.panel` | Sources | height | 464.562px → 464.547px |
| `sources.list` | Group | tag | <div> → <ul> (semantic/default-style change) |
| `sources.row` | Row | tag | <button> → <div> (semantic/default-style change) |

## ⚪ INFO (8)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `sources.panel` | Sources | text | “” → “Everything the agent read or fetched this run — the receipts…” |
| `sources.group-header` | Group | text | “·” → “Artifacts · 1” |
| `sources.list` | Group | text | “” → “ArGenerated ArtifactRevision 1 · step 1” |
| `sources.row` | Row | text | “LiENG-142 · issue + PR linkread 11:39 · gv-01” → “ArGenerated ArtifactRevision 1 · step 1” |
| `sources.icon` | Row | text | “Li” → “Ar” |
| `sources.title` | Row | text | “ENG-142 · issue + PR link” → “Generated Artifact” |
| `sources.title` | Row | width | expected: intrinsic width follows dynamic runtime copy — 257px → 118.812px |
| `sources.sub` | Row | text | “read 11:39 · gv-01” → “Revision 1 · step 1” |

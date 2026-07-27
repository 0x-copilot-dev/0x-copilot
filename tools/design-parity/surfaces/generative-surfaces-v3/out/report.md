# Design-parity — Generative Surfaces v3 review states

Strict Playwright `getComputedStyle` comparison of the user-supplied v3 Design Compiler source against the real `TcStagedDraftSurface` and `TcStagedTableSurface`. No screenshot acceptance and no expected-divergence waivers.

**Aggregate findings:** 🔴 HIGH 0 · 🟠 MEDIUM 0 · 🟡 LOW 11 · ⚪ INFO 19.

| State | Design anchors | Live anchors | HIGH | MEDIUM | Report |
|---|---:|---:|---:|---:|---|
| `draft-held` | 9/9 | 9/9 | 0 | 0 | [report-draft-held.md](./report-draft-held.md) |
| `draft-edit` | 9/9 | 9/9 | 0 | 0 | [report-draft-edit.md](./report-draft-edit.md) |
| `bulk-review` | 9/9 | 9/9 | 0 | 0 | [report-bulk-review.md](./report-bulk-review.md) |
| `bulk-partial` | 7/7 | 7/7 | 0 | 0 | [report-bulk-partial.md](./report-bulk-partial.md) |

## Provenance

- Supplied design SHA-256: `8f72d0e0a2f8cc25ae3311083a451a0be7ac046bad4e497077451e65b64f5e86`.
- Support runtime SHA-256: `c60c49083997f51a592df118c0068475337afd20b8cfd8e1cd9d5eb0c7e254f6`.
- Design CSS SHA-256: `8fa1c5037da7ba965a3bb3782b0ecd9e482eb80242d01b6f006c4f2d18d70848`.
- Base design CSS SHA-256: `ec9299c38cfd092e946c919627fd19d6e2a11d4597066879d22a21d5037c107b`.
- `reference.dc.html`, `support.js`, `copilot-v3.css`, and `copilot.css` are byte-for-byte vendored copies.
- `index.html` changes only autoplay and initial walkthrough state.
- Live pages are serialized from shipping React components with the real design-system stylesheet.

## Reproduce

```bash
node tools/design-parity/lib/run-generative-surfaces-v3-parity.mjs
```

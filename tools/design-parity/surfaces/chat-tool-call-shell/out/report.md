# Design-parity — chat/tool-call shell

This is an authoritative computed-style parity baseline for the supplied **Chat & Tool Calls** Design Compiler walkthrough. It measures the real shipping `RunDestination` / `ThreadCanvas` composition using deterministic persisted-message and SSE fixtures. It does **not** use screenshot assertions.

**Aggregate measured findings:** 🔴 HIGH 11 · 🟠 MEDIUM 75 · 🟡 LOW 238 · ⚪ INFO 55.

## State coverage

| Walkthrough state | Design anchors | Live anchors | HIGH | MEDIUM | State report |
|---|---:|---:|---:|---:|---|
| `focus-thinking` | 10/10 | 9/9 | 2 | 8 | [report-focus-thinking.md](./report-focus-thinking.md) |
| `studio-third-party-read` | 10/10 | 10/10 | 1 | 11 | [report-studio-third-party-read.md](./report-studio-third-party-read.md) |
| `studio-web-chat-only` | 11/11 | 11/11 | 3 | 17 | [report-studio-web-chat-only.md](./report-studio-web-chat-only.md) |
| `studio-csv-chat-only` | 11/11 | 11/11 | 3 | 17 | [report-studio-csv-chat-only.md](./report-studio-csv-chat-only.md) |
| `studio-write-held` | 11/11 | 11/11 | 1 | 11 | [report-studio-write-held.md](./report-studio-write-held.md) |
| `studio-wrap-file` | 11/11 | 11/11 | 1 | 11 | [report-studio-wrap-file.md](./report-studio-wrap-file.md) |

The six states are fixture-backed and state-specific: Focus thinking; third-party Linear read; web chat-only read; local CSV chat-only read; held local-file write; and completed wrap-file result.

## Provenance and method

- Supplied design reference: `tools/design-parity/surfaces/chat-tool-call-shell/design/reference.dc.html`
- Reference SHA-256: `7701b4df85a3d8c45b0505e545c3d30031e9fb21de0b38442e8547f5820f7840`
- Vendor manifest: [design/PROVENANCE.json](../design/PROVENANCE.json) (source, support runtime, and CSS checksums).
- Repository commit measured: `ac475c7786343ced1e196f10680cf6a6f04480a7`; origin/main: `ac475c7786343ced1e196f10680cf6a6f04480a7`.
- Design capture: Design Compiler state selected at construction from `?state=…`; autoplay disabled; runtime-only `data-parity-anchor` attributes added after mount.
- Live capture: [render-live-chat-tool-call-shell.test.tsx](../../../lib/render-live-chat-tool-call-shell.test.tsx) mounts actual `RunDestination` with `surfacesV2`, real `ThreadCanvas`, real `Composer`, and its normal Transport/SSE projection path.
- Browser extraction: shared [extract-playwright.mjs](../../../lib/extract-playwright.mjs) + [extract-computed.js](../../../lib/extract-computed.js), viewport 1200×816.
- Comparator: shared [compare.mjs](../../../lib/compare.mjs); every anchor map is `strict: true` and declares **no** `expectDivergence` waiver.

## Measured design-baseline gaps (not waived)

- `focus-thinking` · `thinking.plan`: No live Focus plan component is mounted by RunDestination.
- `studio-web-chat-only` · `web.sources-card`: No matching inline web-source card is mounted in the shipping transcript.
- `studio-csv-chat-only` · `csv.summary-card`: No shipping inline CSV-summary component is mounted for a chat-only read.

These appear as missing-in-live HIGH rows in their state report. They are listed here so the harness cannot accidentally turn the absence into an expected divergence.

## Reproduce

```bash
node tools/design-parity/lib/run-chat-tool-call-shell-parity.mjs
```

The runner owns a short-lived loopback static server and regenerates ignored JSON profiles plus tracked Markdown reports. The live HTML itself remains under the harness-wide ignored `surfaces/*/live/` directory.

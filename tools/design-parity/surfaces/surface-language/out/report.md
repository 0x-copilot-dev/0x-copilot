# Design-parity — surface language (board lanes · no-spec view)

Computed-style comparison of the vendored `0xCopilot Surface Language` mock against the real `@0x-copilot/surface-renderers` archetypes, for the two renders `docs/plan/surface-language/` covers.

**Aggregate:** 🔴 HIGH 90 · 🟠 MEDIUM 120 · 🟡 LOW 163 · ⚪ INFO 65.

**Measured** 2026-07-29T14:47:08.774Z against `claude/surface-language-board-nospec` @ `5744106b` with **13 uncommitted change(s)** in the packages this reads:

```
M packages/surface-renderers/src/_shared/primitives.tsx
M packages/surface-renderers/src/archetypes/BoardRenderer.test.tsx
M packages/surface-renderers/src/archetypes/BoardRenderer.tsx
M packages/surface-renderers/src/archetypes/DocRenderer.test.tsx
M packages/surface-renderers/src/archetypes/DocRenderer.tsx
M packages/surface-renderers/src/archetypes/MessageRenderer.test.tsx
M packages/surface-renderers/src/archetypes/MessageRenderer.tsx
M packages/surface-renderers/src/archetypes/RecordRenderer.test.tsx
M packages/surface-renderers/src/archetypes/RecordRenderer.tsx
M packages/surface-renderers/src/archetypes/TableRenderer.test.tsx
M packages/surface-renderers/src/archetypes/TableRenderer.tsx
?? packages/surface-renderers/src/_shared/primitives.test.tsx
?? packages/surface-renderers/src/archetypes/noSpecTotality.test.tsx
```

Re-run before treating any row below as current.

| State           | PRD    | Design anchors | Live anchors | HIGH | MEDIUM | Report                                               |
| --------------- | ------ | -------------: | -----------: | ---: | -----: | ---------------------------------------------------- |
| `board`         | PRD-01 |          18/18 |        17/18 |   28 |     37 | [report-board.md](./report-board.md)                 |
| `board-changed` | PRD-01 |            9/9 |          9/9 |   12 |      8 | [report-board-changed.md](./report-board-changed.md) |
| `no-spec`       | PRD-02 |          18/18 |        14/17 |   20 |     28 | [report-no-spec.md](./report-no-spec.md)             |
| `no-spec-board` | PRD-02 |          18/18 |        14/17 |   20 |     28 | [report-no-spec-board.md](./report-no-spec-board.md) |
| `board-capped`  | PRD-01 |            3/3 |          3/3 |   10 |     19 | [report-board-capped.md](./report-board-capped.md)   |

A live anchor count below the design count is not a harness fault — it is the finding. Each unmatched label is listed in its own report as `present in design, ABSENT in live`.

## Provenance

- Design source: DesignSync project `73f810d9-7b77-4849-9087-f7f8e366c48a` (Copilot), page “0xCopilot Surface Language”, fetched 2026-07-29.
- `surface-lang.css` — SHA-256 `9d10c005ceada2b2147e6857892846b4576622b8b94b15f0e9a5f3d8442b453e`.
- `surface-kit.jsx` — SHA-256 `8346570c76b32294d7d942ce557988ed5c79fab37bf85ad332af0d1e29328664`.
- `surface-specs.jsx` — SHA-256 `7cf04ee0c331e01e3b59e3af3e8459ae66f41a883a6d1070c3902f85df99a8e4`.
- `surface-archetypes2.jsx` — SHA-256 `71d18341163adebf6a23c8f10466647338593b573ae2b3e00a92cc20a9f22e47`.
- SHA-256 of the vendored copies AS COMMITTED. Prettier reformats whitespace on commit (values unchanged), so these hashes are of the repo copy, not of the DesignSync response body. Re-vendor per design-kit/REFRESH.md.
- `index.html`, `_globals.js`, `_mount.jsx` and `copilot-v3.css` in `design/` are HARNESS files this repo wrote (mount shell + import shim), not design source.
- Live pages are serialized from the shipping renderers with the real `design-system/src/styles.css` + `chat-surface/src/thread-canvas/surface-language.css`.

## Reproduce

```bash
node tools/design-parity/lib/run-surface-language-parity.mjs
```

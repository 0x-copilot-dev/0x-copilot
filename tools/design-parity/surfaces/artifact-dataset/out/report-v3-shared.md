# Design-parity report — artifact-dataset · `editor-v3-shared`

Design baseline (source of truth) vs live app, by computed style.

- Design: `tools/design-parity/surfaces/artifact-dataset/out/design-v3-shared.json`
- Live: `tools/design-parity/surfaces/artifact-dataset/out/live-v3-shared.json`

**Summary:** 🔴 HIGH 0 · 🟠 MEDIUM 0 · 🟡 LOW 4 · ⚪ INFO 10

## 🟡 LOW (4)

| Element           | Group               | Property | Design → Live                                    |
| ----------------- | ------------------- | -------- | ------------------------------------------------ |
| `artifact.header` | Shared sheet chrome | height   | 42.75px → 44px                                   |
| `artifact.header` | Shared sheet chrome | tag      | <div> → <header> (semantic/default-style change) |
| `artifact.title`  | Shared sheet chrome | height   | 18.75px → 18.7188px                              |
| `artifact.title`  | Shared sheet chrome | tag      | <span> → <h2> (semantic/default-style change)    |

## ⚪ INFO (10)

| Element                  | Group               | Property | Design → Live                                                                                                                                                                                            |
| ------------------------ | ------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `artifact.header`        | Shared sheet chrome | text     | expected: The design names a staged Salesforce operation; the artifact header names the generated CSV and its immutable revision. — “” → “forecast.csvdataset artifactr7 · 125 bytes · aaaaaaaaaaaaDow…” |
| `artifact.header`        | Shared sheet chrome | width    | expected: The live fixture intentionally renders in a wider standalone artifact frame. — 751px → 914px                                                                                                   |
| `artifact.title`         | Shared sheet chrome | text     | expected: Dynamic artifact filename replaces the staged-operation title. — “8 opportunities → Closed-Lost” → “forecast.csv”                                                                              |
| `artifact.title`         | Shared sheet chrome | width    | expected: Intrinsic title width follows dynamic copy. — 186.828px → 75.3906px                                                                                                                            |
| `dataset.action-bar`     | Revision action     | text     | expected: The artifact bar describes immutable revision creation; the design bar describes applying approved connector writes. — “” → “New immutable revision; original stays unchanged.Save patche…”    |
| `dataset.action-bar`     | Revision action     | width    | expected: The live fixture intentionally renders in a wider standalone artifact frame. — 751px → 914px                                                                                                   |
| `dataset.action-copy`    | Revision action     | text     | expected: Artifact revision safety copy is domain-specific. — “1 row is stale — re-stage it before it can apply. Held rows …” → “New immutable revision; original stays unchanged.”                      |
| `dataset.action-copy`    | Revision action     | width    | expected: Intrinsic copy width follows dynamic text. — 413.844px → 286.188px                                                                                                                             |
| `dataset.primary-action` | Revision action     | text     | expected: Saving an immutable CSV revision is distinct from applying approved connector writes. — “Apply 5 changes →” → “Save patched revision”                                                          |
| `dataset.primary-action` | Revision action     | width    | expected: Intrinsic button width follows its action label. — 128.453px → 144.812px                                                                                                                       |

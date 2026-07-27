# Design-parity report — generative-surfaces-v3 · `bulk-partial`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/generative-surfaces-v3/out/design-bulk-partial.json`
- Live: `surfaces/generative-surfaces-v3/out/live-bulk-partial.json`

**Summary:** 🔴 HIGH 0 · 🟠 MEDIUM 0 · 🟡 LOW 4 · ⚪ INFO 7

## 🟡 LOW (4)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `bulk.root` | Bulk partial | height | 464.562px → 464.547px |
| `bulk.root` | Bulk partial | tag | <div> → <section> (semantic/default-style change) |
| `bulk.title` | Bulk partial | width | 186.828px → 186.578px |
| `bulk.title` | Bulk partial | height | 18.75px → 18.7188px |

## ⚪ INFO (7)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `bulk.root` | Bulk partial | text | “” → “8 opportunities → Closed-Lostpartial · retry available4 upda…” |
| `bulk.status` | Bulk partial | text | “staged, not applied” → “partial · retry available” |
| `bulk.status` | Bulk partial | width | expected: intrinsic width follows dynamic runtime copy — 137.703px → 175.5px |
| `bulk.row` | Row result | text | “” → “Meridian Health — renewalNegotiationstageClosed-LostWrite fa…” |
| `bulk.row` | Row result | width | expected: intrinsic width follows dynamic runtime copy — 751px → 760px |
| `bulk.partial-copy` | Recovery | text | “— , the successes stuck.” → “Some writes failed. Applied rows are safe — nothing lost.” |
| `bulk.partial-copy` | Recovery | width | expected: intrinsic width follows dynamic runtime copy — 467.203px → 320.094px |

# Design-parity report — generative-surfaces-v3 · `bulk-review`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/generative-surfaces-v3/out/design-bulk-review.json`
- Live: `surfaces/generative-surfaces-v3/out/live-bulk-review.json`

**Summary:** 🔴 HIGH 0 · 🟠 MEDIUM 0 · 🟡 LOW 3 · ⚪ INFO 4

## 🟡 LOW (3)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `bulk.root` | Bulk review | height | 464.562px → 464.547px |
| `bulk.title` | Bulk review | width | 186.828px → 186.578px |
| `bulk.title` | Bulk review | height | 18.75px → 18.7188px |

## ⚪ INFO (4)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `bulk.root` | Bulk review | text | “” → “8 opportunities → Closed-Loststaged, not applied5 approved ·…” |
| `bulk.row` | Row diff | text | “” → “Meridian Health — renewalNegotiationstageClosed-LostReady to…” |
| `bulk.held-reason` | Row decision | text | “— agent pre-held” → “Contact replied 12d ago — agent pre-held” |
| `bulk.held-reason` | Row decision | width | expected: intrinsic width follows dynamic runtime copy — 170.5px → 184.156px |

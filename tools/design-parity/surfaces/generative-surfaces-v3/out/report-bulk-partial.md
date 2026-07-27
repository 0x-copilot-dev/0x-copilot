# Design-parity report — generative-surfaces-v3 · `bulk-partial`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/generative-surfaces-v3/out/design-bulk-partial.json`
- Live: `surfaces/generative-surfaces-v3/out/live-bulk-partial.json`

**Summary:** 🔴 HIGH 8 · 🟠 MEDIUM 23 · 🟡 LOW 30 · ⚪ INFO 5

## 🔴 HIGH (8)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `bulk.root` | Bulk partial | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(17, 17, 20) (--panel) |
| `bulk.root` | Bulk partial | borderColor | rgb(236, 236, 241) (--tx) → rgba(255, 255, 255, 0.06) (--line) |
| `bulk.title` | Bulk partial | color | rgb(236, 236, 241) (--tx) → rgb(152, 152, 159) (--mut) |
| `bulk.row` | Row result | borderColor | rgb(236, 236, 241) rgb(236, 236, 241) rgba(255, 255, 255, 0.06) rgb(236, 236, 241) → rgba(255, 255, 255, 0.06) rgb(236, 236, 241) rgb(236, 236, 241) rgb(236, 236, 241) |
| `bulk.row-failed` | Row result | color | rgb(240, 118, 79) → rgb(232, 180, 94) |
| `bulk.row-failed` | Row result | borderColor | rgb(240, 118, 79) → rgba(232, 180, 94, 0.25) |
| `bulk.partial-copy` | Recovery | color | rgb(212, 212, 219) (--tx2) → rgb(152, 152, 159) (--mut) |
| `bulk.retry` | Recovery | missing-in-live | present in design, ABSENT in live |

## 🟠 MEDIUM (23)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `bulk.root` | Bulk partial | flexGrow | flex-grow 1 → 0 (affects vertical fill / button placement) |
| `bulk.root` | Bulk partial | boxShadow | none → rgba(0, 0, 0, 0.18) 0px 8px 32px 0px |
| `bulk.root` | Bulk partial | padding | 0px → 24px |
| `bulk.root` | Bulk partial | borderWidth | 0px → 1px |
| `bulk.root` | Bulk partial | borderRadius | 0px → 16px |
| `bulk.root` | Bulk partial | gap | normal → 8px |
| `bulk.title` | Bulk partial | fontSize | 12.5px → 11.2px (-1.3px) |
| `bulk.status` | Bulk partial | fontWeight | 400 → 500 |
| `bulk.status` | Bulk partial | padding | 2px 8px → 1px 8px |
| `bulk.row` | Row result | display | grid → flex |
| `bulk.row` | Row result | flexDirection | row → column |
| `bulk.row` | Row result | alignItems | center → normal |
| `bulk.row` | Row result | padding | 10px 16px → 8px 12px |
| `bulk.row` | Row result | borderWidth | 0px 0px 1px 0px → 1px 0px 0px 0px |
| `bulk.row` | Row result | gap | 10px → 4px |
| `bulk.row-failed` | Row result | fontSize | 9px → 10.5px (+1.5px) |
| `bulk.row-failed` | Row result | fontWeight | 400 → 500 |
| `bulk.row-failed` | Row result | display | inline-flex → flex |
| `bulk.row-failed` | Row result | padding | 0px → 1px 8px |
| `bulk.row-failed` | Row result | borderWidth | 0px → 1px |
| `bulk.row-failed` | Row result | borderRadius | 0px → 999px |
| `bulk.partial-copy` | Recovery | fontSize | 12px → 12.48px (+0.5px) |
| `bulk.partial-copy` | Recovery | fontWeight | 400 → 500 |

## 🟡 LOW (30)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `bulk.root` | Bulk partial | lineHeight | 19.5px → normal |
| `bulk.root` | Bulk partial | transition | all → none |
| `bulk.root` | Bulk partial | width | 751px → 736px |
| `bulk.root` | Bulk partial | height | 464.562px → 328px |
| `bulk.root` | Bulk partial | borderStyle | none → solid |
| `bulk.title` | Bulk partial | lineHeight | 18.75px → normal |
| `bulk.title` | Bulk partial | letterSpacing | normal → 0.56px |
| `bulk.title` | Bulk partial | textTransform | none → uppercase |
| `bulk.title` | Bulk partial | transition | all → none |
| `bulk.title` | Bulk partial | width | 186.828px → 80.4375px |
| `bulk.title` | Bulk partial | height | 18.75px → 13px |
| `bulk.status` | Bulk partial | transition | all → none |
| `bulk.status` | Bulk partial | width | 137.703px → 93.6094px |
| `bulk.status` | Bulk partial | height | 21.75px → 19.75px |
| `bulk.row` | Row result | lineHeight | 19.5px → normal |
| `bulk.row` | Row result | transition | all → none |
| `bulk.row` | Row result | width | 751px → 686px |
| `bulk.row` | Row result | height | 57px → 55.75px |
| `bulk.row` | Row result | borderStyle | none none solid none → solid none none none |
| `bulk.row-failed` | Row result | lineHeight | 13.5px → 15.75px |
| `bulk.row-failed` | Row result | textAlign | right → start |
| `bulk.row-failed` | Row result | transition | all → none |
| `bulk.row-failed` | Row result | width | 48.4062px → 55.8125px |
| `bulk.row-failed` | Row result | height | 13.5px → 19.75px |
| `bulk.row-failed` | Row result | borderStyle | none → solid |
| `bulk.partial-copy` | Recovery | lineHeight | 18px → normal |
| `bulk.partial-copy` | Recovery | letterSpacing | normal → 0.1248px |
| `bulk.partial-copy` | Recovery | transition | all → none |
| `bulk.partial-copy` | Recovery | width | 467.203px → 179.5px |
| `bulk.partial-copy` | Recovery | height | 18px → 15px |

## ⚪ INFO (5)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `bulk.root` | Bulk partial | text | “” → “Salesforce1 updated · 1 held, untouchedNorthstar renewalwill…” |
| `bulk.title` | Bulk partial | text | “8 opportunities → Closed-Lost” → “Salesforce” |
| `bulk.status` | Bulk partial | text | “staged, not applied” → “write · held” |
| `bulk.row` | Row result | text | “” → “Northstar renewalwill applyupdatedstage: Negotiation → Close…” |
| `bulk.partial-copy` | Recovery | text | “— , the successes stuck.” → “1 updated · 1 held, untouched” |

# Design-parity report — generative-surfaces-v3 · `bulk-review`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/generative-surfaces-v3/out/design-bulk-review.json`
- Live: `surfaces/generative-surfaces-v3/out/live-bulk-review.json`

**Summary:** 🔴 HIGH 15 · 🟠 MEDIUM 39 · 🟡 LOW 44 · ⚪ INFO 9

## 🔴 HIGH (15)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `bulk.root` | Bulk review | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(17, 17, 20) (--panel) |
| `bulk.root` | Bulk review | borderColor | rgb(236, 236, 241) (--tx) → rgba(255, 255, 255, 0.06) (--line) |
| `bulk.title` | Bulk review | color | rgb(236, 236, 241) (--tx) → rgb(152, 152, 159) (--mut) |
| `bulk.row` | Row diff | borderColor | rgb(236, 236, 241) rgb(236, 236, 241) rgba(255, 255, 255, 0.06) rgb(236, 236, 241) → rgba(255, 255, 255, 0.06) rgb(236, 236, 241) rgb(236, 236, 241) rgb(236, 236, 241) |
| `bulk.row-change` | Row diff | color | rgb(236, 236, 241) (--tx) → rgb(152, 152, 159) (--mut) |
| `bulk.row-change` | Row diff | backgroundColor | rgba(232, 180, 94, 0.09) → rgba(0, 0, 0, 0) (transparent) |
| `bulk.row-change` | Row diff | borderColor | rgba(232, 180, 94, 0.28) → rgb(152, 152, 159) (--mut) |
| `bulk.row-hold` | Row decision | color | rgb(100, 100, 109) (--mut2) → rgb(255, 255, 255) |
| `bulk.row-hold` | Row decision | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(107, 107, 107) |
| `bulk.row-hold` | Row decision | borderColor | rgba(255, 255, 255, 0.1) (--line2) → rgba(0, 0, 0, 0) (transparent) |
| `bulk.held-reason` | Row decision | fontSize | 10px → 12.48px (+2.5px) |
| `bulk.held-reason` | Row decision | color | rgb(232, 180, 94) → rgb(236, 236, 241) (--tx) |
| `bulk.review-copy` | Apply | fontFamily | typeface class changed (mono → sans) |
| `bulk.review-copy` | Apply | fontSize | 10px → 12.48px (+2.5px) |
| `bulk.action` | Apply | fontSize | 11.5px → 14px (+2.5px) |

## 🟠 MEDIUM (39)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `bulk.root` | Bulk review | flexGrow | flex-grow 1 → 0 (affects vertical fill / button placement) |
| `bulk.root` | Bulk review | boxShadow | none → rgba(0, 0, 0, 0.18) 0px 8px 32px 0px |
| `bulk.root` | Bulk review | padding | 0px → 24px |
| `bulk.root` | Bulk review | borderWidth | 0px → 1px |
| `bulk.root` | Bulk review | borderRadius | 0px → 16px |
| `bulk.root` | Bulk review | gap | normal → 8px |
| `bulk.title` | Bulk review | fontSize | 12.5px → 11.2px (-1.3px) |
| `bulk.status` | Bulk review | fontWeight | 400 → 500 |
| `bulk.status` | Bulk review | padding | 2px 8px → 1px 8px |
| `bulk.row` | Row diff | display | grid → flex |
| `bulk.row` | Row diff | flexDirection | row → column |
| `bulk.row` | Row diff | alignItems | center → normal |
| `bulk.row` | Row diff | padding | 10px 16px → 8px 12px |
| `bulk.row` | Row diff | borderWidth | 0px 0px 1px 0px → 1px 0px 0px 0px |
| `bulk.row` | Row diff | gap | 10px → 4px |
| `bulk.row-change` | Row diff | fontSize | 10.5px → 12.48px (+2.0px) |
| `bulk.row-change` | Row diff | fontWeight | 400 → 500 |
| `bulk.row-change` | Row diff | padding | 1px 7px → 0px |
| `bulk.row-change` | Row diff | borderWidth | 1px → 0px |
| `bulk.row-change` | Row diff | borderRadius | 5px → 0px |
| `bulk.row-hold` | Row decision | fontSize | 13.3333px → 14px (+0.7px) |
| `bulk.row-hold` | Row decision | fontWeight | 400 → 500 |
| `bulk.row-hold` | Row decision | display | grid → flex |
| `bulk.row-hold` | Row decision | justifyContent | normal → center |
| `bulk.row-hold` | Row decision | borderRadius | 6px → 8px |
| `bulk.row-hold` | Row decision | gap | normal → 8px |
| `bulk.held-reason` | Row decision | fontWeight | 400 → 500 |
| `bulk.held-reason` | Row decision | display | flex → block |
| `bulk.held-reason` | Row decision | alignItems | center → normal |
| `bulk.held-reason` | Row decision | margin | 3px 0px 0px 0px → 0px |
| `bulk.held-reason` | Row decision | gap | 5px → normal |
| `bulk.review-copy` | Apply | fontWeight | 400 → 500 |
| `bulk.review-copy` | Apply | flexGrow | flex-grow 0 → 1 (affects vertical fill / button placement) |
| `bulk.review-copy` | Apply | margin | 0px 0px 0px 200.469px → 0px |
| `bulk.action` | Apply | justifyContent | normal → center |
| `bulk.action` | Apply | padding | 4px 9px → 1px 6px |
| `bulk.action` | Apply | margin | 0px 0px 0px 139.703px → 0px |
| `bulk.action` | Apply | borderRadius | 6px → 8px |
| `bulk.action` | Apply | gap | 6px → 8px |

## 🟡 LOW (44)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `bulk.root` | Bulk review | lineHeight | 19.5px → normal |
| `bulk.root` | Bulk review | transition | all → none |
| `bulk.root` | Bulk review | width | 751px → 736px |
| `bulk.root` | Bulk review | height | 464.562px → 377.938px |
| `bulk.root` | Bulk review | borderStyle | none → solid |
| `bulk.title` | Bulk review | lineHeight | 18.75px → normal |
| `bulk.title` | Bulk review | letterSpacing | normal → 0.56px |
| `bulk.title` | Bulk review | textTransform | none → uppercase |
| `bulk.title` | Bulk review | transition | all → none |
| `bulk.title` | Bulk review | width | 186.828px → 80.4375px |
| `bulk.title` | Bulk review | height | 18.75px → 13px |
| `bulk.status` | Bulk review | transition | all → none |
| `bulk.status` | Bulk review | width | 137.703px → 93.6094px |
| `bulk.status` | Bulk review | height | 21.75px → 19.75px |
| `bulk.row` | Row diff | lineHeight | 19.5px → normal |
| `bulk.row` | Row diff | transition | all → none |
| `bulk.row` | Row diff | width | 751px → 686px |
| `bulk.row` | Row diff | height | 45px → 56.7969px |
| `bulk.row` | Row diff | borderStyle | none none solid none → solid none none none |
| `bulk.row-change` | Row diff | lineHeight | 15.75px → normal |
| `bulk.row-change` | Row diff | letterSpacing | normal → 0.1248px |
| `bulk.row-change` | Row diff | transition | all → none |
| `bulk.row-change` | Row diff | width | 77.4688px → 662px |
| `bulk.row-change` | Row diff | height | 19.75px → 15px |
| `bulk.row-change` | Row diff | borderStyle | solid → none |
| `bulk.row-change` | Row diff | tag | <span> → <p> (semantic/default-style change) |
| `bulk.row-hold` | Row decision | lineHeight | normal → 16.8px |
| `bulk.row-hold` | Row decision | transition | all → none |
| `bulk.row-hold` | Row decision | width | 26px → 44.9062px |
| `bulk.row-hold` | Row decision | height | 24px → 20.7969px |
| `bulk.held-reason` | Row decision | lineHeight | 15px → normal |
| `bulk.held-reason` | Row decision | letterSpacing | normal → 0.1248px |
| `bulk.held-reason` | Row decision | transition | all → none |
| `bulk.held-reason` | Row decision | width | 170.5px → 662px |
| `bulk.held-reason` | Row decision | height | 30px → 15px |
| `bulk.held-reason` | Row decision | tag | <div> → <span> (semantic/default-style change) |
| `bulk.review-copy` | Apply | lineHeight | 15px → normal |
| `bulk.review-copy` | Apply | letterSpacing | normal → 0.1248px |
| `bulk.review-copy` | Apply | transition | all → none |
| `bulk.review-copy` | Apply | width | 174px → 453.594px |
| `bulk.action` | Apply | lineHeight | normal → 16.8px |
| `bulk.action` | Apply | transition | background 0.12s, border-color 0.12s → none |
| `bulk.action` | Apply | width | 128.453px → 142.516px |
| `bulk.action` | Apply | height | 23px → 20.7969px |

## ⚪ INFO (9)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `bulk.root` | Bulk review | text | “” → “Salesforce2 will apply · 1 heldNorthstar renewalwill applyHo…” |
| `bulk.title` | Bulk review | text | “8 opportunities → Closed-Lost” → “Salesforce” |
| `bulk.status` | Bulk review | text | “staged, not applied” → “write · held” |
| `bulk.row` | Row diff | text | “” → “Northstar renewalwill applyHoldstage: Negotiation → Closed-L…” |
| `bulk.row-change` | Row diff | text | “Closed-Lost” → “stage: Negotiation → Closed-Lost” |
| `bulk.row-hold` | Row decision | text | “” → “Hold” |
| `bulk.held-reason` | Row decision | text | “— agent pre-held” → “recent activity — agent pre-held” |
| `bulk.review-copy` | Apply | text | “5 approved · 1 stale · 2 held” → “Writes apply only to rows you approve. Held rows stay untouc…” |
| `bulk.action` | Apply | text | “Apply 5 changes →” → “Apply 2 changes →” |

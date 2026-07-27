# Design-parity report — generative-surfaces-v3 · `draft-edit`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/generative-surfaces-v3/out/design-draft-edit.json`
- Live: `surfaces/generative-surfaces-v3/out/live-draft-edit.json`

**Summary:** 🔴 HIGH 16 · 🟠 MEDIUM 26 · 🟡 LOW 24 · ⚪ INFO 4

## 🔴 HIGH (16)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `draft.root` | Draft | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(17, 17, 20) (--panel) |
| `draft.root` | Draft | borderColor | rgb(236, 236, 241) (--tx) → rgba(255, 255, 255, 0.06) (--line) |
| `draft.revision` | Draft | color | rgb(100, 100, 109) (--mut2) → rgb(232, 180, 94) |
| `draft.revision` | Draft | borderColor | rgb(100, 100, 109) (--mut2) → rgba(232, 180, 94, 0.25) |
| `draft.editor` | Editor | borderColor | rgba(169, 139, 224, 0.5) → rgba(255, 255, 255, 0.1) (--line2) |
| `draft.save` | Editor | fontSize | 11.5px → 14px (+2.5px) |
| `draft.save` | Editor | color | rgb(236, 236, 241) (--tx) → rgb(8, 19, 29) (--accent-ink) |
| `draft.save` | Editor | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(95, 178, 236) (--accent/--sky) |
| `draft.save` | Editor | borderColor | rgba(255, 255, 255, 0.1) (--line2) → rgba(0, 0, 0, 0) (transparent) |
| `draft.cancel` | Editor | fontSize | 11.5px → 14px (+2.5px) |
| `draft.cancel` | Editor | color | rgb(152, 152, 159) (--mut) → rgb(255, 255, 255) |
| `draft.cancel` | Editor | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(107, 107, 107) |
| `draft.approval` | Approval | missing-in-live | present in design, ABSENT in live |
| `draft.approval-copy` | Approval | missing-in-live | present in design, ABSENT in live |
| `draft.approve` | Approval | missing-in-live | present in design, ABSENT in live |
| `draft.reject` | Approval | missing-in-live | present in design, ABSENT in live |

## 🟠 MEDIUM (26)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `draft.root` | Draft | flexGrow | flex-grow 1 → 0 (affects vertical fill / button placement) |
| `draft.root` | Draft | boxShadow | none → rgba(0, 0, 0, 0.18) 0px 8px 32px 0px |
| `draft.root` | Draft | padding | 0px → 24px |
| `draft.root` | Draft | borderWidth | 0px → 1px |
| `draft.root` | Draft | borderRadius | 0px → 16px |
| `draft.root` | Draft | gap | normal → 8px |
| `draft.revision` | Draft | fontSize | 9px → 10.5px (+1.5px) |
| `draft.revision` | Draft | fontWeight | 400 → 500 |
| `draft.revision` | Draft | display | block → flex |
| `draft.revision` | Draft | alignItems | normal → center |
| `draft.revision` | Draft | padding | 0px → 1px 8px |
| `draft.revision` | Draft | borderWidth | 0px → 1px |
| `draft.revision` | Draft | borderRadius | 0px → 999px |
| `draft.revision` | Draft | gap | normal → 5px |
| `draft.editor` | Editor | fontSize | 12px → 13px (+1.0px) |
| `draft.editor` | Editor | display | inline-block → block |
| `draft.editor` | Editor | margin | 0px → 0px 12px |
| `draft.save` | Editor | fontWeight | 500 → 600 |
| `draft.save` | Editor | justifyContent | normal → center |
| `draft.save` | Editor | padding | 4px 9px → 1px 6px |
| `draft.save` | Editor | borderRadius | 6px → 8px |
| `draft.save` | Editor | gap | 6px → 8px |
| `draft.cancel` | Editor | justifyContent | normal → center |
| `draft.cancel` | Editor | padding | 4px 9px → 1px 6px |
| `draft.cancel` | Editor | borderRadius | 6px → 8px |
| `draft.cancel` | Editor | gap | 6px → 8px |

## 🟡 LOW (24)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `draft.root` | Draft | lineHeight | 19.5px → normal |
| `draft.root` | Draft | transition | all → none |
| `draft.root` | Draft | width | 751px → 736px |
| `draft.root` | Draft | height | 464.562px → 279.297px |
| `draft.root` | Draft | borderStyle | none → solid |
| `draft.revision` | Draft | lineHeight | 13.5px → 15.75px |
| `draft.revision` | Draft | letterSpacing | 0.9px → normal |
| `draft.revision` | Draft | transition | all → none |
| `draft.revision` | Draft | width | 81.9062px → 49.5px |
| `draft.revision` | Draft | height | 13.5px → 19.75px |
| `draft.revision` | Draft | borderStyle | none → solid |
| `draft.editor` | Editor | lineHeight | 18.6px → normal |
| `draft.editor` | Editor | transition | border-color 0.12s → none |
| `draft.editor` | Editor | width | 606px → 686px |
| `draft.editor` | Editor | height | 64px → 120px |
| `draft.save` | Editor | lineHeight | normal → 16.8px |
| `draft.save` | Editor | opacity | 1 → 0.48 |
| `draft.save` | Editor | transition | background 0.12s, border-color 0.12s → none |
| `draft.save` | Editor | width | 90.7188px → 102.562px |
| `draft.save` | Editor | height | 23px → 20.7969px |
| `draft.cancel` | Editor | lineHeight | normal → 16.8px |
| `draft.cancel` | Editor | transition | background 0.12s, border-color 0.12s → none |
| `draft.cancel` | Editor | width | 58.2031px → 59.3594px |
| `draft.cancel` | Editor | height | 23px → 20.7969px |

## ⚪ INFO (4)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `draft.root` | Draft | text | “” → “Gmailrev 1Hi Priya — good news: the checkout fix (ENG-142) i…” |
| `draft.revision` | Draft | text | “DRAFT ·” → “rev 1” |
| `draft.editor` | Editor | text | “Dana's PR covers the session-refresh path, and we'll confirm…” → “Hi Priya — good news: the checkout fix (ENG-142) is in revie…” |
| `draft.save` | Editor | text | “Done editing” → “Save as rev 2” |

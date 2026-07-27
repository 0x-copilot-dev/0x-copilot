# Design-parity report — generative-surfaces-v3 · `draft-held`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/generative-surfaces-v3/out/design-draft-held.json`
- Live: `surfaces/generative-surfaces-v3/out/live-draft-held.json`

**Summary:** 🔴 HIGH 18 · 🟠 MEDIUM 37 · 🟡 LOW 42 · ⚪ INFO 7

## 🔴 HIGH (18)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `draft.root` | Draft | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(17, 17, 20) (--panel) |
| `draft.root` | Draft | borderColor | rgb(236, 236, 241) (--tx) → rgba(255, 255, 255, 0.06) (--line) |
| `draft.revision` | Draft | color | rgb(100, 100, 109) (--mut2) → rgb(232, 180, 94) |
| `draft.revision` | Draft | borderColor | rgb(100, 100, 109) (--mut2) → rgba(232, 180, 94, 0.25) |
| `draft.body` | Draft | color | rgb(212, 212, 219) (--tx2) → rgb(236, 236, 241) (--tx) |
| `draft.edit` | Draft actions | fontSize | 11.5px → 14px (+2.5px) |
| `draft.edit` | Draft actions | color | rgb(236, 236, 241) (--tx) → rgb(255, 255, 255) |
| `draft.edit` | Draft actions | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(107, 107, 107) |
| `draft.edit` | Draft actions | borderColor | rgba(255, 255, 255, 0.1) (--line2) → rgba(0, 0, 0, 0) (transparent) |
| `draft.approval` | Approval | backgroundColor | rgba(232, 180, 94, 0.05) → rgb(17, 17, 20) (--panel) |
| `draft.approval` | Approval | borderColor | rgba(232, 180, 94, 0.28) rgb(236, 236, 241) rgb(236, 236, 241) rgb(236, 236, 241) → rgba(255, 255, 255, 0.06) (--line) |
| `draft.approval-copy` | Approval | color | rgb(212, 212, 219) (--tx2) → rgb(236, 236, 241) (--tx) |
| `draft.approve` | Approval | fontSize | 11.5px → 14px (+2.5px) |
| `draft.reject` | Approval | fontSize | 11.5px → 14px (+2.5px) |
| `draft.reject` | Approval | color | rgb(240, 118, 79) → rgb(255, 255, 255) |
| `draft.reject` | Approval | backgroundColor | rgba(0, 0, 0, 0) (transparent) → rgb(107, 107, 107) |
| `draft.reject` | Approval | borderColor | rgba(240, 118, 79, 0.25) → rgba(0, 0, 0, 0) (transparent) |
| `draft.provenance` | Provenance | color | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut) |

## 🟠 MEDIUM (37)

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
| `draft.body` | Draft | fontSize | 12.5px → 13px (+0.5px) |
| `draft.body` | Draft | padding | 0px → 0px 12px |
| `draft.body` | Draft | margin | 0px 0px 12px 0px → 0px |
| `draft.edit` | Draft actions | justifyContent | normal → center |
| `draft.edit` | Draft actions | padding | 4px 9px → 1px 6px |
| `draft.edit` | Draft actions | borderRadius | 6px → 8px |
| `draft.edit` | Draft actions | gap | 6px → 8px |
| `draft.approval` | Approval | flexWrap | nowrap → wrap |
| `draft.approval` | Approval | boxShadow | none → rgba(0, 0, 0, 0.18) 0px 8px 32px 0px |
| `draft.approval` | Approval | padding | 10px 16px → 8px 12px |
| `draft.approval` | Approval | borderWidth | 1px 0px 0px 0px → 1px |
| `draft.approval` | Approval | borderRadius | 0px → 16px |
| `draft.approval` | Approval | gap | 11px → 8px |
| `draft.approval-copy` | Approval | fontSize | 12px → 13px (+1.0px) |
| `draft.approval-copy` | Approval | flexGrow | flex-grow 0 → 1 (affects vertical fill / button placement) |
| `draft.approve` | Approval | justifyContent | normal → center |
| `draft.approve` | Approval | padding | 4px 9px → 1px 6px |
| `draft.approve` | Approval | borderRadius | 6px → 8px |
| `draft.approve` | Approval | gap | 6px → 8px |
| `draft.reject` | Approval | justifyContent | normal → center |
| `draft.reject` | Approval | padding | 4px 9px → 1px 6px |
| `draft.reject` | Approval | borderRadius | 6px → 8px |
| `draft.reject` | Approval | gap | 6px → 8px |

## 🟡 LOW (42)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `draft.root` | Draft | lineHeight | 19.5px → normal |
| `draft.root` | Draft | transition | all → none |
| `draft.root` | Draft | width | 751px → 736px |
| `draft.root` | Draft | height | 464.562px → 274.344px |
| `draft.root` | Draft | borderStyle | none → solid |
| `draft.revision` | Draft | lineHeight | 13.5px → 15.75px |
| `draft.revision` | Draft | letterSpacing | 0.9px → normal |
| `draft.revision` | Draft | transition | all → none |
| `draft.revision` | Draft | width | 81.9062px → 49.5px |
| `draft.revision` | Draft | height | 13.5px → 19.75px |
| `draft.revision` | Draft | borderStyle | none → solid |
| `draft.body` | Draft | lineHeight | 22.5px → normal |
| `draft.body` | Draft | transition | all → none |
| `draft.body` | Draft | width | 606px → 686px |
| `draft.body` | Draft | height | 45px → 96px |
| `draft.edit` | Draft actions | lineHeight | normal → 16.8px |
| `draft.edit` | Draft actions | transition | background 0.12s, border-color 0.12s → none |
| `draft.edit` | Draft actions | width | 90.9844px → 39.5938px |
| `draft.edit` | Draft actions | height | 23px → 20.7969px |
| `draft.approval` | Approval | lineHeight | 19.5px → normal |
| `draft.approval` | Approval | transition | all → none |
| `draft.approval` | Approval | width | 751px → 686px |
| `draft.approval` | Approval | height | 44px → 38.7969px |
| `draft.approval` | Approval | borderStyle | solid none none none → solid |
| `draft.approval-copy` | Approval | lineHeight | 18px → normal |
| `draft.approval-copy` | Approval | transition | all → none |
| `draft.approval-copy` | Approval | width | 238.406px → 426.844px |
| `draft.approval-copy` | Approval | height | 18px → 16px |
| `draft.approve` | Approval | lineHeight | normal → 16.8px |
| `draft.approve` | Approval | transition | background 0.12s, border-color 0.12s → none |
| `draft.approve` | Approval | width | 123.703px → 105.734px |
| `draft.approve` | Approval | height | 23px → 20.7969px |
| `draft.reject` | Approval | lineHeight | normal → 16.8px |
| `draft.reject` | Approval | transition | background 0.12s, border-color 0.12s → none |
| `draft.reject` | Approval | width | 55.0625px → 55.5312px |
| `draft.reject` | Approval | height | 23px → 20.7969px |
| `draft.provenance` | Provenance | lineHeight | 14.25px → normal |
| `draft.provenance` | Provenance | letterSpacing | normal → 1.14px |
| `draft.provenance` | Provenance | textTransform | none → uppercase |
| `draft.provenance` | Provenance | transition | all → none |
| `draft.provenance` | Provenance | width | 404.734px → 47.8906px |
| `draft.provenance` | Provenance | height | 14.25px → 13px |

## ⚪ INFO (7)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `draft.root` | Draft | text | “” → “Gmailrev 1EditHi Priya — good news: the checkout fix (ENG-14…” |
| `draft.revision` | Draft | text | “DRAFT ·” → “rev 1” |
| `draft.edit` | Draft actions | text | “Edit draft” → “Edit” |
| `draft.approval` | Approval | text | “” → “Exactly this draft — rev 1 — is what sends.rv3·003Approve re…” |
| `draft.approval-copy` | Approval | text | “Exactly this draft — — is what sends.” → “Exactly this draft — rev 1 — is what sends.” |
| `draft.approve` | Approval | text | “Approve & send →” → “Approve rev 1” |
| `draft.provenance` | Provenance | text | “gmail.drafts.create → messages.send · · gv-02” → “rv3·003” |

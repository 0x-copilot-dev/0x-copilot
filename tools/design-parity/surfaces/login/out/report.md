# Design-parity report — `login (pick·connecting·sign·done)`

Design baseline (source of truth) vs live app, by computed style.

- Design: `surfaces/login/out/design-login.json`
- Live: `surfaces/login/out/live-login.json`

**Summary:** 🔴 HIGH 36 · 🟠 MEDIUM 31 · 🟡 LOW 17 · ⚪ INFO 0

## 🔴 HIGH (36)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `pick.card` | Pick | fontSize | 13px → 16px (+3.0px) |
| `pick.title` | Pick | fontSize | 18px → 22.4px (+4.4px) |
| `pick.opt.primary` | Pick | fontSize | 13px → 16px (+3.0px) |
| `pick.opt.primary.sub` | Pick | fontFamily | typeface class changed (mono → sans) |
| `pick.opt.primary.sub` | Pick | fontSize | 10px → 12.48px (+2.5px) |
| `pick.opt.primary.sub` | Pick | color | rgba(8, 19, 29, 0.6) → color(srgb 0.0313726 0.0745098 0.113725 / 0.78) |
| `pick.opt.primary.icon` | Pick | backgroundColor | rgba(8, 19, 29, 0.14) → rgba(0, 0, 0, 0) (transparent) |
| `pick.opt.google` | Pick | fontSize | 13px → 16px (+3.0px) |
| `pick.opt.google` | Pick | borderColor | rgba(255, 255, 255, 0.1) (--line2) → rgba(255, 255, 255, 0.06) (--line) |
| `pick.opt.local` | Pick | fontSize | 13px → 16px (+3.0px) |
| `pick.opt.local` | Pick | borderColor | rgba(255, 255, 255, 0.1) (--line2) → rgba(255, 255, 255, 0.06) (--line) |
| `pick.divider` | Pick | fontFamily | typeface class changed (mono → sans) |
| `pick.divider` | Pick | fontSize | 9px → 12.48px (+3.5px) |
| `pick.foot` | Pick | color | rgb(100, 100, 109) (--mut2) → rgb(152, 152, 159) (--mut) |
| `pick.ver` | Pick | fontSize | 9px → 11.2px (+2.2px) |
| `connecting.wrap` | Connecting (waiting) | fontSize | 13px → 16px (+3.0px) |
| `connecting.wrap` | Connecting (waiting) | color | rgb(152, 152, 159) (--mut) → rgb(236, 236, 241) (--tx) |
| `connecting.title` | Connecting (waiting) | fontSize | 13.5px → 22.4px (+8.9px) |
| `sign.title` | Sign | fontSize | 18px → 22.4px (+4.4px) |
| `sign.addr` | Sign | backgroundColor | rgb(17, 17, 20) (--panel) → rgb(22, 22, 26) (--panel2) |
| `sign.addr` | Sign | borderColor | rgba(255, 255, 255, 0.1) (--line2) → rgba(255, 255, 255, 0.06) (--line) |
| `sign.addr.meta` | Sign | fontFamily | typeface class changed (mono → sans) |
| `sign.addr.meta` | Sign | fontSize | 10px → 12.48px (+2.5px) |
| `sign.msg` | Sign | color | rgb(212, 212, 219) (--tx2) → rgb(152, 152, 159) (--mut) |
| `sign.msg` | Sign | backgroundColor | rgb(13, 13, 16) → rgb(22, 22, 26) (--panel2) |
| `sign.btn.cancel` | Sign | fontSize | 12px → 14px (+2.0px) |
| `sign.btn.cancel` | Sign | color | rgb(236, 236, 241) (--tx) → rgb(152, 152, 159) (--mut) |
| `sign.btn.primary` | Sign | fontSize | 12px → 14px (+2.0px) |
| `done.wrap` | Done | color | rgb(152, 152, 159) (--mut) → rgb(236, 236, 241) (--tx) |
| `done.title` | Done | fontSize | 13.5px → 22.4px (+8.9px) |
| `werr.wrap` | Wallet error (DESIGN-ONLY) | missing-in-live | present in design, ABSENT in live |
| `werr.title` | Wallet error (DESIGN-ONLY) | missing-in-live | present in design, ABSENT in live |
| `werr.body` | Wallet error (DESIGN-ONLY) | missing-in-live | present in design, ABSENT in live |
| `werr.btn.secondary` | Wallet error (DESIGN-ONLY) | missing-in-live | present in design, ABSENT in live |
| `werr.btn.primary` | Wallet error (DESIGN-ONLY) | missing-in-live | present in design, ABSENT in live |
| `werr.backlink` | Wallet error (DESIGN-ONLY) | missing-in-live | present in design, ABSENT in live |

## 🟠 MEDIUM (31)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `pick.sub` | Pick | fontSize | 12px → 13.6px (+1.6px) |
| `pick.opt.primary` | Pick | fontWeight | 500 → 400 |
| `pick.opt.primary` | Pick | padding | 10px 12px → 12px |
| `pick.opt.primary` | Pick | borderRadius | 9px → 8px |
| `pick.opt.primary` | Pick | gap | 11px → 12px |
| `pick.opt.primary.label` | Pick | fontSize | 13px → 13.6px (+0.6px) |
| `pick.opt.primary.label` | Pick | fontWeight | 500 → 600 |
| `pick.opt.primary.icon` | Pick | borderRadius | 7px → 0px |
| `pick.opt.google` | Pick | padding | 10px 12px → 12px |
| `pick.opt.google` | Pick | borderRadius | 9px → 8px |
| `pick.opt.google` | Pick | gap | 11px → 12px |
| `pick.opt.local` | Pick | padding | 10px 12px → 12px |
| `pick.opt.local` | Pick | borderRadius | 9px → 8px |
| `pick.foot` | Pick | fontSize | 10.5px → 12.48px (+2.0px) |
| `pick.ver` | Pick | gap | 14px → normal |
| `connecting.wrap` | Connecting (waiting) | display | block → flex |
| `connecting.wrap` | Connecting (waiting) | padding | 30px 0px → 12px 0px |
| `connecting.body` | Connecting (waiting) | fontSize | 12px → 13.6px (+1.6px) |
| `connecting.cancel` | Connecting (waiting) | display | inline-flex → flex |
| `connecting.cancel` | Connecting (waiting) | padding | 4px 9px → 4px 8.8px |
| `sign.sub` | Sign | fontSize | 12px → 13.6px (+1.6px) |
| `sign.addr` | Sign | padding | 10px 12px → 8px 12px |
| `sign.addr.hex` | Sign | fontSize | 12px → 13.6px (+1.6px) |
| `sign.msg` | Sign | padding | 11px 13px → 12px |
| `sign.btn.cancel` | Sign | fontWeight | 500 → 650 |
| `sign.btn.cancel` | Sign | borderRadius | 6px → 8px |
| `sign.btn.cancel` | Sign | padding | 9px 12px → 8.8px 14.4px |
| `sign.btn.primary` | Sign | fontWeight | 600 → 650 |
| `sign.btn.primary` | Sign | borderRadius | 6px → 8px |
| `sign.btn.primary` | Sign | padding | 9px 12px → 8.8px 14.4px |
| `done.body` | Done | fontSize | 12px → 13.6px (+1.6px) |

## 🟡 LOW (17)

| Element | Group | Property | Design → Live |
|---|---|---|---|
| `pick.card` | Pick | width | 372px → 416px |
| `pick.card` | Pick | tag | <div> → <section> (semantic/default-style change) |
| `pick.title` | Pick | lineHeight | 21.6px → 26.88px |
| `pick.sub` | Pick | lineHeight | 19.8px → 20.4px |
| `pick.opt.primary.sub` | Pick | tag | <small> → <span> (semantic/default-style change) |
| `pick.divider` | Pick | letterSpacing | 1.08px → normal |
| `pick.divider` | Pick | textTransform | uppercase → none |
| `pick.foot` | Pick | tag | <div> → <p> (semantic/default-style change) |
| `pick.ver` | Pick | tag | <div> → <p> (semantic/default-style change) |
| `connecting.title` | Connecting (waiting) | lineHeight | 16.2px → 26.88px |
| `connecting.title` | Connecting (waiting) | tag | <h3> → <h1> (semantic/default-style change) |
| `connecting.body` | Connecting (waiting) | lineHeight | 19.2px → 20.4px |
| `sign.title` | Sign | lineHeight | 21.6px → 26.88px |
| `sign.msg` | Sign | lineHeight | 18.7px → 17.36px |
| `sign.msg` | Sign | tag | <div> → <pre> (semantic/default-style change) |
| `done.title` | Done | lineHeight | 16.2px → 26.88px |
| `done.title` | Done | tag | <h3> → <h1> (semantic/default-style change) |

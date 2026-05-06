# PR 8.2 — Settings Phase 2 (Bio · Avatar · MFA · API key tabs)

## Context

PR 8.1 ([pr-8.1-settings-ia-overhaul.md](pr-8.1-settings-ia-overhaul.md)) restructured the Settings IA but explicitly deferred four items. Reassessed:

| Item                   | Backend ready?                                                                                                                                         | Verdict                                                                                                                                                         |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Bio field**          | No — needs one column.                                                                                                                                 | **Implement.** Smallest full-stack drop.                                                                                                                        |
| **Avatar upload**      | Field exists (`user_profiles.avatar_url TEXT`); no upload pipeline.                                                                                    | **Implement** as a `data:` URL stored in the existing column. No new table, no new endpoint, no S3. The column is already permissive — we cap size server-side. |
| **MFA toggle**         | Yes — `MfaService` + 7 internal routes already shipped under `/internal/v1/auth/mfa/*` (see [mfa.py](services/backend/src/backend_app/routes/mfa.py)). | **Implement.** Add public routes + facade proxy + UI; reuse the service layer. TOTP only in v1 (WebAuthn ceremony is its own PR).                               |
| **Workspace API keys** | No — admin-issued token model not designed.                                                                                                            | **IA prep only.** Split the existing `ApiKeys` section into `Personal` / `Workspace` tabs; Workspace renders an explained empty state. Real tokens land later.  |

## Design principles

- **No new infra.** Avatar uploads piggy-back on the existing `avatar_url TEXT` column as a `data:` URL. MFA reuses the existing `MfaService`. Workspace API key tab is an empty state, not a new model.
- **DRY.** New facade proxy routes use the existing `_proxy` helper pattern (no new HTTP machinery). FE reuses `Field`, `Card`, `Button`, `TextInput`, the existing `useUserProfile` shape.
- **Prebuilt where it counts.** [`qrcode.react`](https://www.npmjs.com/package/qrcode.react) (~7 KB, MIT) renders the TOTP QR. We don't reinvent QR generation.
- **Anti-patterns avoided.** No new "all the things" layer; we extend the patches we already use. No optimistic-then-rollback dance for sensitive flows (MFA enroll/disable goes through the form's normal save lifecycle). No silent error swallowing — server validation messages surface verbatim.

## Streaming + agent context (sanity check)

This PR is settings only, so it does **not** touch the run / event / SSE pipeline:

- The agent harness (`runtime_worker` / `runtime_api`) sees nothing new.
- No new `RuntimeEventEnvelope` variants. No new projection fields.
- Frontend's `eventReducer` and the SSE client are untouched.
- Database tables we own: `user_profiles` (one column added), `mfa_factors` / `totp_secrets` / `mfa_recovery_codes` (existing — no schema change).

## Scope

### A. Bio field (full stack)

**Database** — `services/backend/migrations/0026_user_profile_bio.sql`:

```sql
ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS bio TEXT;
```

Length cap server-side (`<= 600` chars); no migration constraint to keep the rollback trivial.

**Backend** — [me_store.py](services/backend/src/backend_app/identity/me_store.py): add `bio` to `UserProfileRecord`, `SELECT`, `UPSERT`. [me_profile.py](services/backend/src/backend_app/routes/me_profile.py): add `bio` to `UserProfileResponse`, `UpdateUserProfileRequest`, `_hydrate`, `_profile_diff_view`.

**api-types** — Add `bio: string | null` to `UserProfile`; `bio?: string | null` to `UpdateUserProfileRequest`.

**Frontend** — [Profile.tsx](apps/frontend/src/features/settings/sections/Profile.tsx): add `Bio` textarea below `Job title` in the Identity card.

### B. Avatar upload (FE-driven, existing column)

**Strategy.** Browser `<input type=file>` + drag-drop. Selected image goes through a `<canvas>` resize to 256×256 (cover) → re-encoded as JPEG 0.9 → base64 → stored in `user_profiles.avatar_url` as `data:image/jpeg;base64,…`. Size after resize is typically 30–60 KB; we hard-cap the column write at 200 KB.

**Why not a real upload pipeline?** Object storage isn't deployed and adding S3-isms now is premature. The `avatar_url` column already accepts arbitrary strings — `data:` URLs are valid URLs. We can swap to S3 later by changing what gets stored without changing the contract or the FE rendering.

**Server validation** — me_profile validator: when `avatar_url` starts with `data:`, require `image/(png|jpeg|webp)`, base64 encoding, and total length ≤ 200 KB. Otherwise treat as URL (existing behavior).

**Frontend** — Replace the URL paste field with:

- 64 px circle preview (existing).
- "Upload photo" button + drag-drop zone wrapping the avatar circle.
- "Remove" button clears the column.
- "Use a URL instead" disclosure reveals the URL input (kept for advanced users).

### C. MFA opt-in (TOTP)

**Backend public routes** — new `services/backend/src/backend_app/routes/me_mfa.py`:

- `GET    /v1/me/mfa/factors` — list factors for the caller.
- `POST   /v1/me/mfa/factors/totp/enroll` → `{factor_id, secret, otpauth_url}`.
- `POST   /v1/me/mfa/factors/totp/confirm` → enables the factor on first valid code.
- `DELETE /v1/me/mfa/factors/{factor_id}` — disable.
- `POST   /v1/me/mfa/recovery-codes/regenerate` → returns 8 one-time codes (shown once).

The handlers are 5–15 lines each because they delegate straight to `MfaService`. Identity comes from the verified session, not from caller-supplied org_id/user_id (the internal routes take those from the body; public callers can't be trusted to provide them). Audit: every state-changing call writes through the existing chain (`MfaService` already does this).

**Facade** — proxy each route through the existing `_proxy` helper with identity headers.

**api-types** — mirror `MfaFactorSummary`, `TotpEnrollResult`, `TotpConfirmRequest`, `RecoveryCodes`.

**Frontend** — new card in Profile's Sign-in & security section (existing card slot):

- "Two-step verification" panel.
- Empty state → "Add an authenticator app" button.
- Enroll flow: render QR via `qrcode.react`, show secret as fallback string, OTP input, confirm.
- Enrolled state: list factors with display name + last used + Disable button.
- Recovery codes accordion: "Regenerate" button (warns it invalidates the previous set), copies codes to clipboard.

**WebAuthn** — out of scope for this PR. The backend supports it; the FE ceremony (`navigator.credentials.create` + attestation parse) is its own PR.

### D. API keys: Personal | Workspace tabs

**Frontend only.** `ApiKeys.tsx` gets a tab strip at the top:

- **Personal** (existing UI, unchanged).
- **Workspace** (admin-only) — visible to admins, hidden for members. Renders an empty state explaining workspace-issued tokens are not yet available; links to the docs entry that describes the eventual model.

No new endpoints. The empty state is honest about the "later" timing and doesn't pretend a feature exists.

## Files touched

**New**

- `services/backend/migrations/0026_user_profile_bio.sql` (+ rollback)
- `services/backend/src/backend_app/routes/me_mfa.py`
- `apps/frontend/src/features/settings/sections/MfaPanel.tsx`
- `apps/frontend/src/api/mfaApi.ts`
- `apps/frontend/src/features/settings/sections/avatarPipeline.ts` (canvas resize helper, pure)

**Modified**

- `services/backend/src/backend_app/identity/me_store.py` — `bio` plumb.
- `services/backend/src/backend_app/routes/me_profile.py` — `bio` plumb + avatar `data:` validator.
- `services/backend/src/backend_app/app.py` — register `me_mfa` routes.
- `services/backend-facade/src/backend_facade/me_routes.py` — proxy MFA routes.
- `packages/api-types/src/index.ts` — `bio` field, MFA contracts.
- `apps/frontend/src/features/settings/sections/Profile.tsx` — Bio textarea + new avatar UI + `<MfaPanel/>` slot.
- `apps/frontend/src/features/settings/sections/ApiKeys.tsx` — tab strip.
- `apps/frontend/package.json` — `qrcode.react`.

## Verification

- Backend: `cd services/backend && PYTHONPATH=src:../../packages/service-contracts/src .venv/bin/python -m pytest tests/test_me_routes.py tests/test_me_profile_preferences.py -q`
- Backend MFA: existing `tests/test_mfa_*` continue to cover the service; new public routes get a small smoke test.
- api-types: `npm run typecheck --workspace @enterprise-search/api-types`
- Frontend: `npm run typecheck --workspace @enterprise-search/frontend && npm run build --workspace @enterprise-search/frontend`
- Vitest: settings + me feature tests pass.

## Out of scope (Phase 3+)

- WebAuthn UI ceremony (backend ready, FE only).
- Workspace-issued admin tokens (data model design).
- S3-style avatar pipeline (when object storage lands).
- Per-IdP MFA enforcement policy editor.

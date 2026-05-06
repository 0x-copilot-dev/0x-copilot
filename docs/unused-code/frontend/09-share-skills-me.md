# Cluster: Share, skills, and profile / preferences

**Paths:** `apps/frontend/src/features/share/`, `skills/`, `me/`  
**Last reviewed:** 2026-05-06

## Scope

- Share link UX: [`SharePopover.tsx`](../../../apps/frontend/src/features/share/SharePopover.tsx), [`useShareLinkText.ts`](../../../apps/frontend/src/features/share/useShareLinkText.ts).
- Skills catalog for composer/settings: [`useSkills.ts`](../../../apps/frontend/src/features/skills/useSkills.ts).
- Profile + theme/preferences hydration for shell: [`useUserProfile.ts`](../../../apps/frontend/src/features/me/useUserProfile.ts), [`useUserPreferences.ts`](../../../apps/frontend/src/features/me/useUserPreferences.ts), [`useThemeSync.ts`](../../../apps/frontend/src/features/me/useThemeSync.ts).

## Unused / ts-prune signals

| Symbol              | File               | Notes                                       |
| ------------------- | ------------------ | ------------------------------------------- |
| `SharePopoverProps` | `SharePopover.tsx` | Standard props typing — `(used in module)`. |

[`meApi.ts`](../../../apps/frontend/src/api/meApi.ts) is consumed from [`useUserProfile`](../../../apps/frontend/src/features/me/useUserProfile.ts), [`useUserPreferences`](../../../apps/frontend/src/features/me/useUserPreferences.ts), and sidebar [`WorkspacePicker.tsx`](../../../apps/frontend/src/features/chat/components/sidebar/WorkspacePicker.tsx).

## Smells

- **Facade-only contracts** — These features assume `/v1/*` routes via Vite proxy / ingress; dead code is less likely here than in experimental chat hooks.
- **Skills + settings duplication** — Skills appear in chat composer and settings; keep capability flags in sync when adding GTM restrictions.

## Confidence

**Low** for unused production modules in this cluster at this revision.

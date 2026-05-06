# Cluster: Settings and workspace members

**Paths:** `apps/frontend/src/features/settings/`, `apps/frontend/src/features/workspace/`  
**Last reviewed:** 2026-05-06

## Scope

- Settings surface: [`SettingsScreen.tsx`](../../../apps/frontend/src/features/settings/SettingsScreen.tsx), sections under `sections/`, [`Modal.tsx`](../../../apps/frontend/src/features/settings/Modal.tsx).
- Routing helpers: [`useSettingsSection.ts`](../../../apps/frontend/src/features/settings/useSettingsSection.ts), workspace hooks [`useWorkspace.ts`](../../../apps/frontend/src/features/settings/useWorkspace.ts), [`useWorkspaceDefaults.ts`](../../../apps/frontend/src/features/settings/useWorkspaceDefaults.ts).
- Workspace mention helper: [`MentionLabel.tsx`](../../../apps/frontend/src/features/workspace/MentionLabel.tsx), [`useWorkspaceMember.ts`](../../../apps/frontend/src/features/workspace/useWorkspaceMember.ts).

## Unused / ts-prune signals

| Symbol                                               | File                    | Notes                                                                                 |
| ---------------------------------------------------- | ----------------------- | ------------------------------------------------------------------------------------- |
| `SETTINGS_SECTIONS`, `SettingsSection`               | `useSettingsSection.ts` | `satisfies` / `readonly` tokens may appear as bogus ts-prune rows — parser artifact.  |
| `UseWorkspaceResult`, `UseWorkspaceMembersResult`, … | `useWorkspace.ts`       | Hook return types; often `(used in module)` unless consumers import types explicitly. |
| `ModalProps`                                         | `Modal.tsx`             | Standard prop typing export.                                                          |
| `WorkspaceMember`                                    | `useWorkspaceMember.ts` | Type export for consumers.                                                            |

No disconnected settings modules were found (all sections route from `SettingsScreen` / `App`).

## Smells

- **Duplicate settings slug lists** — [`SETTINGS_SECTIONS`](../../../apps/frontend/src/features/settings/useSettingsSection.ts) is canonical; [`App.tsx`](../../../apps/frontend/src/app/App.tsx) maintains a parallel `settingsSections` array with overlapping intent (“superset” comment). **Risk:** hash validation and UI labels drift if one array changes without the other.
- **Large settings union** — `SettingsSection` spans profile, workspace, billing, connectors, skills, etc.; removing or renaming a slug requires coordinated updates across hash routing, navigation links, and tests.

## Confidence

**Medium** on duplicate-array drift risk; **low** on unused files in this cluster.

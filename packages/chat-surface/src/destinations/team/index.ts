// Team destination — public surface + ItemRef resolver registration.
//
// Source: team-memory-cmdk-prd.md §7.1 + cross-audit §1.1 / §3.3.
// Each destination registers its `ItemKind` on package import; Team
// owns `kind: "person"` so every other destination's
// `<ItemLink kind="person" id=…>` resolves without forcing a circular
// dependency.

import type { UserId } from "@enterprise-search/api-types";

import {
  hasItemRefResolver,
  registerItemRefResolver,
} from "../../refs/registry";

import {
  OffboardingWizard,
  type OffboardingWizardProps,
  type OffboardingAsset,
} from "./OffboardingWizard";
import { PersonCard, type PersonCardProps } from "./PersonCard";
import {
  PersonDetailView,
  type PersonDetailTabId,
  type PersonDetailViewProps,
} from "./PersonDetailView";
import {
  TeamDestination,
  applyRoleFilter,
  applySearch,
  applySort,
  type TeamDestinationProps,
  type TeamFilterCounts,
  type TeamFilterSlug,
  type TeamSortSlug,
} from "./TeamDestination";
import {
  TeamInviteWizard,
  type TeamInviteWizardProps,
  type TeamInviteWizardResult,
} from "./TeamInviteWizard";
import {
  TeamPanel,
  type PresenceFilterCounts,
  type PresenceFilterSlug,
  type TeamPanelProps,
} from "./TeamPanel";

// ===========================================================================
// Re-exports
// ===========================================================================

export {
  TeamDestination,
  type TeamDestinationProps,
  type TeamFilterCounts,
  type TeamFilterSlug,
  type TeamSortSlug,
  applyRoleFilter,
  applySearch,
  applySort,
};

export {
  TeamPanel,
  type TeamPanelProps,
  type PresenceFilterSlug,
  type PresenceFilterCounts,
};

export { PersonCard, type PersonCardProps };

export { PersonDetailView, type PersonDetailViewProps, type PersonDetailTabId };

export {
  TeamInviteWizard,
  type TeamInviteWizardProps,
  type TeamInviteWizardResult,
};

export {
  OffboardingWizard,
  type OffboardingWizardProps,
  type OffboardingAsset,
};

// ===========================================================================
// ItemRef resolver registration (cross-audit §3.3)
// ===========================================================================
//
// A dedicated `{ kind: "person-detail", userId }` route variant will land
// alongside the data-binder phase. Until then, the workspace route is the
// stable fallback so `<ItemLink kind="person">` renders a real link
// rather than the deleted-chip — same pattern Tools / Projects use.

if (!hasItemRefResolver("person")) {
  registerItemRefResolver("person", async (id: UserId) => ({
    label: "Person",
    icon: null,
    route: { kind: "workspace", workspaceId: id as unknown as string },
    breadcrumb: "Team",
  }));
}

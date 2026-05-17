// Inbox destination — public surface + ItemRef resolver registration.
//
// Per cross-audit §1.1 + §3.3 (binding 2026-05-17), each destination
// registers its kind on package import. Inbox owns the resolver for
// kind `"inbox_item"` so every other destination's
// `<ItemLink kind="inbox_item" id=…>` resolves without forcing a
// circular dependency.
//
// Wire-type re-exports are forwarded from `_inbox-stub.ts`; the
// orchestrator rewires the stub to `@enterprise-search/api-types` at
// merge time.

import type { InboxItemId } from "@enterprise-search/api-types";

import {
  hasItemRefResolver,
  registerItemRefResolver,
} from "../../refs/registry";

import {
  InboxDestination,
  bucketInbox,
  type InboxDestinationProps,
  type RenderDetailSlot,
} from "./InboxDestination";
import {
  InboxPanel,
  type InboxPanelCounts,
  type InboxPanelFilterSlug,
  type InboxPanelProps,
} from "./InboxPanel";

// ===========================================================================
// Re-exports
// ===========================================================================

export {
  InboxDestination,
  bucketInbox,
  type InboxDestinationProps,
  type RenderDetailSlot,
};
export {
  InboxPanel,
  type InboxPanelCounts,
  type InboxPanelFilterSlug,
  type InboxPanelProps,
};

// Wire-type re-exports (forwarded from `_inbox-stub.ts`; the
// orchestrator rewires the stub to `@enterprise-search/api-types` at
// merge time — see `_inbox-stub.ts` header).
//
// TODO(merge): rewire to "@enterprise-search/api-types"
export type {
  InboxItem,
  InboxItemKind,
  InboxItemPriority,
  InboxItemStatus,
  InboxSectionKey,
  InboxSender,
  InboxSenderKind,
  InboxSystemOrigin,
} from "./InboxDestination";

// ===========================================================================
// ItemRef resolver registration (cross-audit §3.3)
// ===========================================================================
//
// Guarded with `hasItemRefResolver` to keep test environments — which
// may import the module in multiple realms / vitest workers — from
// throwing `ItemRefResolverAlreadyRegistered`. The host's richer
// resolver (with denormalized subject + read state) replaces this
// later with `{ replace: true }`.
//
// Phase-4 minimal resolver: route target is the workspace surface for
// the inbox-item id. P4-B2/P4-C own the route-extension work — adding
// an `{ kind: "inbox-detail", inboxItemId }` variant to ArtifactRoute
// and re-registering with `{ replace: true }`. Until then we route to
// the existing workspace destination so the link is at least
// clickable, not dead.

if (!hasItemRefResolver("inbox_item")) {
  registerItemRefResolver("inbox_item", async (id: InboxItemId) => ({
    label: "Inbox item",
    icon: null,
    // P4-B2 will introduce a dedicated `{ kind: "inbox-detail",
    // inboxItemId }` route variant and re-register with
    // `{ replace: true }`. Until then route to the workspace surface
    // keyed by the inbox item id — stable, non-null, clickable
    // fallback so `<ItemLink>` doesn't degrade to the deleted chip
    // path.
    route: { kind: "workspace", workspaceId: id as unknown as string },
    breadcrumb: "Inbox",
  }));
}

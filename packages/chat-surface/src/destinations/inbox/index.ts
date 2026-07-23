// Inbox destination — public surface + ItemRef resolver registration.
//
// Per cross-audit §1.1 + §3.3 (binding 2026-05-17), each destination
// registers its kind on package import. Inbox owns the resolver for
// kind `"inbox_item"` so every other destination's
// `<ItemLink kind="inbox_item" id=…>` resolves without forcing a
// circular dependency.
//
// Wire-type re-exports are forwarded from `_inbox-stub.ts`; the
// orchestrator rewires the stub to `@0x-copilot/api-types` at
// merge time.

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
// orchestrator rewires the stub to `@0x-copilot/api-types` at
// merge time — see `_inbox-stub.ts` header).
//
// TODO(merge): rewire to "@0x-copilot/api-types"
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
// Detail / reply / snooze re-exports (P4-B2)
// ===========================================================================

export {
  InboxDetail,
  type InboxDetailItem,
  type InboxDetailItemKind,
  type InboxDetailStatus,
  type InboxDetailPriority,
  type InboxDetailSender,
  type InboxDetailBodyState,
  type InboxDetailProps,
} from "./InboxDetail";
export {
  InboxReply,
  type InboxReplyPayload,
  type InboxReplyProps,
  type InboxReplyRouting,
} from "./inbox-reply";
export {
  SnoozePicker,
  type SnoozePickerProps,
  type SnoozePresetSlug,
} from "./snooze-picker";

// ===========================================================================
// Responsive layout (P4-B3) — re-exports
// ===========================================================================

export {
  useInboxLayout,
  INBOX_BREAKPOINT_PX,
  type InboxLayoutMode,
  type InboxLayoutState,
  type UseInboxLayoutOptions,
} from "./useInboxLayout";

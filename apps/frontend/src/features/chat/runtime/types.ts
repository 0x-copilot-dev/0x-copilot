/**
 * Atlas runtime — type barrel.
 *
 * Single point at which the rest of the app pulls runtime/message/composer
 * types. Today these still re-export from `@assistant-ui/react` so call sites
 * are decoupled from the underlying provider; when we own the runtime fully
 * (Phase 4 complete) we replace the bodies here without touching any
 * consumer.
 *
 * Frontend code MUST NOT `import type ... from "@assistant-ui/react"` directly.
 * Always go through this barrel.
 */
export type {
  AppendMessage,
  Attachment,
  AttachmentAdapter,
  AttachmentStatus,
  CompleteAttachment,
  DictationAdapter,
  ExternalStoreThreadData,
  ExternalStoreThreadListAdapter,
  MessagePartStatus,
  MessageStatus,
  MessageTiming,
  PendingAttachment,
  ReasoningGroupProps,
  ReasoningMessagePartProps,
  TextMessagePartProps,
  ThreadMessageLike,
  ToolCallMessagePartProps,
} from "@assistant-ui/react";

/**
 * Atlas runtime — public barrel.
 *
 * Frontend code consumes runtime types and adapters through this entry
 * point so that owning the runtime layer end-to-end (Phase 2+ of the
 * `@assistant-ui/react` migration) is a swap of internals here without
 * any churn at the call sites.
 */
export type * from "./types";

export {
  AtlasCompositeAttachmentAdapter,
  AtlasFileAttachmentAdapter,
  AtlasImageAttachmentAdapter,
  AtlasTextAttachmentAdapter,
  fileMatchesAccept,
  mimeTypeForFileName,
  type FileLike,
} from "./attachments";

export {
  AtlasWebSpeechDictationAdapter,
  type AtlasDictationOptions,
} from "./dictation";

export {
  ActionBar,
  ActionBarCopy,
  ActionBarReload,
  Message,
  MessageAttachments,
  MessageContext,
  MessageParts,
  useMessage,
  type MessageContextValue,
  type MessagePartsComponents,
  type MessageProps,
} from "./components";

export {
  ThreadEmpty,
  ThreadMessages,
  ThreadRoot,
  ThreadScrollToBottom,
  ThreadViewport,
  type ThreadMessageRenderValue,
} from "./thread";

import type { RuntimeEventEnvelope } from "@enterprise-search/api-types";
import type {
  MessageStatus as AssistantMessageStatus,
  ThreadMessageLike,
} from "@assistant-ui/react";

export type ThreadMessageContent = Exclude<
  ThreadMessageLike["content"],
  string
>;
export type ThreadMessageContentPart = ThreadMessageContent[number];
export type ThreadTextPart = Extract<
  ThreadMessageContentPart,
  { type: "text" }
>;
export type ThreadReasoningPart = Extract<
  ThreadMessageContentPart,
  { type: "reasoning" }
>;
export type ThreadToolCallPart = Extract<
  ThreadMessageContentPart,
  { type: "tool-call" }
>;
export type ThreadToolCallArgs = NonNullable<ThreadToolCallPart["args"]>;

export type ChatThreadMessage = ThreadMessageLike & {
  parentId?: string | null;
};

export type RuntimePartStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "waiting"
  | "unknown";

export type ChatItem =
  | {
      id: string;
      kind: "message";
      role: "user" | "assistant" | "system";
      content: ThreadMessageContent;
      parentId?: string | null;
      attachments?: ChatThreadMessage["attachments"];
      metadata?: ThreadMessageLike["metadata"];
      runId?: string;
      sourceMessageId?: string | null;
      branchId?: string | null;
      status?: AssistantMessageStatus;
    }
  | {
      id: string;
      kind: "status";
      title: string;
      text?: string;
    };

export type RuntimeEventsByRunId = ReadonlyMap<
  string,
  readonly RuntimeEventEnvelope[]
>;

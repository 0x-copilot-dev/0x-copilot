/**
 * Atlas runtime — type definitions.
 *
 * These are the application-owned shapes for messages, attachments,
 * adapters, and component props. Frontend code MUST go through this
 * barrel rather than importing types from any third-party runtime
 * library.
 */

import type { ReactNode } from "react";

// ─── Message status ────────────────────────────────────────────────────────

/** Status of a top-level message. */
export type MessageStatus =
  | { readonly type: "running" }
  | { readonly type: "complete"; readonly reason?: string }
  | {
      readonly type: "incomplete";
      readonly reason?: string;
      readonly error?: unknown;
    }
  | { readonly type: "requires-action"; readonly reason?: string };

/**
 * Status of an individual message part. Distinct from `MessageStatus`
 * because the part-level "requires-action" reason is constrained to
 * "interrupt" (the runtime only stalls a part for an interrupt; other
 * stop-reasons live at the message level).
 */
export type MessagePartStatus =
  | { readonly type: "running" }
  | { readonly type: "complete"; readonly reason?: string }
  | {
      readonly type: "incomplete";
      readonly reason?: string;
      readonly error?: unknown;
    }
  | { readonly type: "requires-action"; readonly reason?: "interrupt" };

/**
 * Per-message timing analytics. Schema is Atlas-internal; only
 * `runtime/components/AssistantMessageMetrics.tsx` reads the named
 * fields. Kept loose so the chatModel can tack on extra fields
 * (`streamStartTime`, `firstTokenTime`, `totalStreamTime`,
 * `tokenCount`, etc.) without re-shaping every consumer.
 */
export interface MessageTiming {
  readonly startedAt?: Date;
  readonly completedAt?: Date;
  readonly streamStartTime?: number;
  readonly firstTokenTime?: number;
  readonly totalStreamTime?: number;
  readonly tokenCount?: number;
  readonly tokensPerSecond?: number;
  readonly totalChunks?: number;
  readonly toolCallCount?: number;
  readonly [key: string]: unknown;
}

// ─── Message parts ─────────────────────────────────────────────────────────

export interface TextMessagePart {
  readonly type: "text";
  readonly text: string;
  readonly status?: MessagePartStatus;
}

export interface ReasoningMessagePart {
  readonly type: "reasoning";
  readonly text: string;
  readonly status?: MessagePartStatus;
}

export interface ToolCallMessagePart<
  TArgs = Record<string, unknown>,
  TResult = unknown,
> {
  readonly type: "tool-call";
  readonly toolCallId?: string;
  readonly toolName: string;
  readonly args?: TArgs;
  readonly argsText?: string;
  readonly artifact?: unknown;
  readonly result?: TResult;
  readonly isError?: boolean;
  readonly parentId?: string;
  readonly status?: MessagePartStatus;
}

export interface ImageContentPart {
  readonly type: "image";
  readonly image: string;
  readonly filename?: string;
}

export interface FileContentPart {
  readonly type: "file";
  readonly filename?: string;
  readonly data: string;
  readonly mimeType: string;
}

/**
 * Generic content-part shape used by the attachment adapters' `send`
 * step. Adapters return `content: AttachmentContentPart[]` whose
 * concrete shape is consumer-defined; the only contract is
 * `{ type: string }`. We keep it permissive because the message-send
 * pipeline forwards it to the run-create API as opaque payload.
 */
export interface OpaqueMessagePart {
  readonly type: string;
  readonly [key: string]: unknown;
}

/**
 * The three message-part variants the chatModel produces and the
 * Atlas renderers know how to render. Tightly typed so narrowing on
 * `part.type` works without intersecting OpaqueMessagePart.
 */
export type MessagePart =
  | TextMessagePart
  | ReasoningMessagePart
  | ToolCallMessagePart;

export type AttachmentContentPart =
  | TextMessagePart
  | ImageContentPart
  | FileContentPart
  | OpaqueMessagePart;

// ─── Attachments ───────────────────────────────────────────────────────────

export type AttachmentStatus =
  | { readonly type: "running" }
  | { readonly type: "complete" }
  | { readonly type: "requires-action"; readonly reason: "composer-send" };

export interface AttachmentBase {
  readonly id: string;
  readonly type: string;
  readonly name: string;
  readonly contentType?: string;
}

export interface PendingAttachment extends AttachmentBase {
  readonly file: File;
  readonly status:
    | { readonly type: "requires-action"; readonly reason: "composer-send" }
    | { readonly type: "running" };
}

export interface CompleteAttachment extends AttachmentBase {
  readonly file?: File;
  readonly status: { readonly type: "complete" };
  readonly content: readonly AttachmentContentPart[];
}

export type Attachment = PendingAttachment | CompleteAttachment;

export interface AttachmentAdapter {
  readonly accept: string;
  add(state: { file: File }): Promise<PendingAttachment>;
  send(attachment: PendingAttachment): Promise<CompleteAttachment>;
  remove(attachment: Attachment): Promise<void>;
}

// ─── Dictation ─────────────────────────────────────────────────────────────

export interface DictationSession {
  readonly status:
    | { readonly type: "starting" }
    | { readonly type: "running" }
    | {
        readonly type: "ended";
        readonly reason: "stopped" | "cancelled" | "error";
      };
  stop(): Promise<void>;
  cancel(): void;
  onSpeechStart(callback: () => void): () => void;
  onSpeechEnd(callback: (payload: { transcript: string }) => void): () => void;
  onSpeech(
    callback: (payload: { transcript: string; isFinal: boolean }) => void,
  ): () => void;
}

export interface DictationAdapter {
  listen(): DictationSession;
}

// ─── Thread message ────────────────────────────────────────────────────────

/**
 * Per-message metadata. Atlas-specific fields live under `custom`
 * (run_id, quote, citations, draft pointers); the named slots
 * (`timing`, `submittedFeedback`) are surface APIs that components
 * read directly.
 *
 * `custom` is loosely typed so the chatModel can tack on Atlas
 * fields (`run_id`, `quote`, `cited_sources`, `parent_run_id`, …)
 * without re-shaping every consumer; readers narrow on demand.
 */
export interface ThreadMessageMetadata {
  readonly unstable_state?: unknown;
  readonly unstable_annotations?: readonly unknown[];
  readonly unstable_data?: readonly unknown[];
  readonly steps?: readonly unknown[];
  readonly timing?: MessageTiming;
  readonly submittedFeedback?: { readonly type: "positive" | "negative" };
  readonly custom?: Record<string, unknown> & {
    readonly run_id?: string;
    readonly quote?: { readonly text?: string; readonly messageId?: string };
  };
}

export interface ThreadMessageLike {
  readonly role: "user" | "assistant" | "system";
  readonly content: string | readonly MessagePart[];
  readonly id?: string;
  readonly createdAt?: Date;
  readonly status?: MessageStatus;
  readonly attachments?: readonly CompleteAttachment[];
  readonly metadata?: ThreadMessageMetadata;
}

// ─── Append message ────────────────────────────────────────────────────────

/**
 * Shape that submission handlers receive. `parentId` and `sourceId`
 * describe where this message slots into the existing tree (used for
 * branching when editing). The historical assistant-ui contract
 * required these to be present even when null; we keep that
 * convention so existing call sites compile unchanged.
 */
export interface AppendMessage {
  readonly role: "user" | "assistant" | "system";
  readonly content: readonly MessagePart[];
  readonly attachments?: readonly CompleteAttachment[];
  readonly metadata?: ThreadMessageMetadata;
  readonly parentId: string | null;
  readonly sourceId: string | null;
  readonly runConfig?: unknown;
  readonly startRun?: boolean;
  readonly id?: string;
}

// ─── Component props ───────────────────────────────────────────────────────

/**
 * Per-part state the runtime synthesises before handing parts to a
 * renderer. `status` is required at this layer — the walker fills
 * it in (defaulting to "complete" or "running" depending on whether
 * a tool-call already has a result).
 */
export interface MessagePartState {
  readonly status: MessagePartStatus;
}

export type TextMessagePartProps = MessagePartState & TextMessagePart;
export type ReasoningMessagePartProps = MessagePartState & ReasoningMessagePart;

export interface ReasoningGroupProps {
  readonly startIndex: number;
  readonly endIndex: number;
  readonly children?: ReactNode;
}

/**
 * Tool renderers receive the part fields plus the runtime-supplied
 * `addResult` + `resume` callbacks. The runtime guarantees `args` /
 * `argsText` / `status` are present (defaulting `args` to `{}`,
 * `argsText` to `""`, and inferring `status` from `result`), so
 * renderers can destructure them without optional-chaining every field.
 */
export type ToolCallMessagePartProps<
  TArgs = Record<string, unknown>,
  TResult = unknown,
> = MessagePartState &
  Omit<ToolCallMessagePart<TArgs, TResult>, "args" | "argsText" | "status"> & {
    readonly args: TArgs;
    readonly argsText: string;
    addResult: (result: TResult) => void;
    resume: (payload: unknown) => void;
  };

// ─── Thread list adapter ──────────────────────────────────────────────────

/**
 * Persisted view of a conversation row used by the sidebar. Kept here
 * even though `useExternalStoreRuntime` is gone — `AssistantThreadList`
 * imports the type for its own thread-row schema.
 */
export interface ExternalStoreThreadData<
  S extends "regular" | "archived" = "regular",
> {
  readonly id: string;
  readonly remoteId: string;
  readonly title: string;
  readonly status?: S;
}

export interface ExternalStoreThreadListAdapter {
  readonly threadId?: string;
  readonly isLoading?: boolean;
  readonly threads: readonly ExternalStoreThreadData<"regular">[];
  readonly archivedThreads: readonly ExternalStoreThreadData<"archived">[];
  readonly onSwitchToNewThread: () => void;
  readonly onSwitchToThread: (threadId: string) => void;
}

// Host (web) adapter for the AssistantComposer shell.
//
// The composer shell itself now lives in @0x-copilot/chat-surface (PR-1.3)
// so web and desktop render it identically. This file is the thin web
// adapter that binds the shell's substrate touchpoints:
//
//   - `attachmentAdapter` — bridges the host runtime two-stage adapter
//     (`add({file})` / `remove(attachment)` / `send(pending)`) to the
//     chat-surface Composer's `add(file)` / `remove(id)` / `send(pending)`
//     shape. Kept here byte-for-byte (FR-1.10) because it depends on the
//     host's `runtime/*` attachment types.
//   - `filePicker` — the composer's hidden `<input type="file">` picker
//     (returns real `File`s the runtime adapters can read).
//   - `renderPlusMenu` — the `+` menu portal + outside-click, owned by the
//     host `AnchoredPlusMenu` (uses `createPortal` / `window` / `document`).
//   - `skillInstructionPrompt` / `mcpServerInstructionPrompt` — the host's
//     instruction-prompt builders.
//
// Existing import sites (`ThreadBody`) keep resolving `AssistantComposer`
// (and `DetailsPanelKind`) from this path unchanged.

import {
  AssistantComposer as ChatSurfaceAssistantComposer,
  type AssistantComposerProps as ChatSurfaceAssistantComposerProps,
  type AssistantComposerPlusMenuSlotArgs,
  type AttachmentAdapter as ChatSurfaceAttachmentAdapter,
  type ComposerHandle,
  type CompleteAttachment as ChatSurfaceCompleteAttachment,
  type PendingAttachment as ChatSurfacePendingAttachment,
} from "@0x-copilot/chat-surface";
import {
  forwardRef,
  useCallback,
  useMemo,
  useRef,
  type ReactElement,
} from "react";
import {
  mcpServerInstructionPrompt,
  skillInstructionPrompt,
} from "../../prompts";
import type {
  Attachment,
  AttachmentAdapter,
  CompleteAttachment,
  PendingAttachment,
} from "../../runtime/types";
import { AnchoredPlusMenu } from "./AnchoredPlusMenu";
import { ComposerFilePicker } from "./composerFilePicker";

export type { DetailsPanelKind } from "@0x-copilot/chat-surface";

// Substrate-bound singletons — one hidden-input picker per app, and the
// host-owned portal/outside-click slot renderer.
const composerFilePicker = new ComposerFilePicker();

/**
 * Public props for the web AssistantComposer. Identical to the shell's
 * prop surface except `attachmentAdapter` is the host's **runtime**
 * two-stage adapter (bridged here), and the shell-internal slots
 * (`filePicker` / `renderPlusMenu` / the prompt builders) are bound by
 * this adapter rather than supplied by callers.
 */
export type AssistantComposerProps = Omit<
  ChatSurfaceAssistantComposerProps,
  | "attachmentAdapter"
  | "filePicker"
  | "renderPlusMenu"
  | "skillInstructionPrompt"
  | "mcpServerInstructionPrompt"
> & {
  attachmentAdapter?: AttachmentAdapter;
};

export const AssistantComposer = forwardRef<
  ComposerHandle,
  AssistantComposerProps
>(function AssistantComposer(
  { attachmentAdapter, ...rest },
  ref,
): ReactElement {
  // Bridge the runtime two-stage AttachmentAdapter to the chat-surface
  // Composer's adapter shape. The runtime adapter family takes
  // `add({ file })` / `remove(attachment)` and never stamps a `size`
  // on the attachment; the chat-surface Composer expects `add(file)`,
  // `remove(id)`, and reads `size` for the pill render. We translate
  // here so the runtime adapters (image/text/file/composite) keep
  // working unchanged. An id→runtime-attachment registry keeps the
  // chat-surface remove call routable back to the right runtime
  // adapter (composite dispatches on the file's MIME type, so it
  // needs the original attachment).
  const adapterRegistryRef = useRef<Map<string, Attachment>>(new Map());
  const bridgedAttachmentAdapter = useMemo<
    ChatSurfaceAttachmentAdapter | undefined
  >(() => {
    if (!attachmentAdapter) return undefined;
    const runtime: AttachmentAdapter = attachmentAdapter;
    const registry = adapterRegistryRef.current;
    return {
      async add(file: File): Promise<ChatSurfacePendingAttachment> {
        const pending = await runtime.add({ file });
        registry.set(pending.id, pending);
        return {
          id: pending.id,
          name: pending.name,
          // `pending.contentType` is only populated by some adapters;
          // fall back to the file's MIME type so the chat-surface pill
          // still renders a sensible label.
          type: pending.contentType ?? file.type ?? pending.type,
          size: file.size,
          // The chat-surface "pending" status union is narrower than the
          // runtime's `requires-action | running`; both map to "pending"
          // for the chat-surface pill — the runtime adapter is the one
          // that knows how to finalise.
          status: { type: "pending" },
          handle: pending,
        };
      },
      async send(
        pendingShim: ChatSurfacePendingAttachment,
      ): Promise<ChatSurfaceCompleteAttachment> {
        const runtimePending = registry.get(pendingShim.id) as
          | PendingAttachment
          | undefined;
        if (!runtimePending) {
          throw new Error(
            `No runtime attachment registered for id ${pendingShim.id}`,
          );
        }
        const completed = await runtime.send(runtimePending);
        registry.set(completed.id, completed);
        // Forward the runtime CompleteAttachment verbatim plus the
        // chat-surface display fields. `onSubmit` downstream reads it
        // as a runtime CompleteAttachment (id/type/name/contentType/
        // content/file); we preserve every runtime field so the
        // run-create pipeline can build its `attachments[]` body.
        // chat-surface's CompleteAttachment is a structural superset
        // (adds size + optional handle); the only TS gap is the
        // `AttachmentContentPart` element shape (runtime: strict union;
        // chat-surface: `{type; [k]: unknown}`), so we widen via
        // `unknown` at the slot boundary.
        const bridged = {
          ...completed,
          size: pendingShim.size,
          handle: completed,
          status: { type: "complete" as const },
        };
        return bridged as unknown as ChatSurfaceCompleteAttachment;
      },
      async remove(id: string): Promise<void> {
        const attachment = registry.get(id);
        registry.delete(id);
        if (attachment) {
          await runtime.remove(attachment);
        }
      },
    };
  }, [attachmentAdapter]);

  const renderPlusMenu = useCallback(
    ({
      open,
      anchorRef,
      onDismiss,
      children,
    }: AssistantComposerPlusMenuSlotArgs): ReactElement => (
      <AnchoredPlusMenu open={open} anchorRef={anchorRef} onDismiss={onDismiss}>
        {children}
      </AnchoredPlusMenu>
    ),
    [],
  );

  return (
    <ChatSurfaceAssistantComposer
      ref={ref}
      {...rest}
      attachmentAdapter={bridgedAttachmentAdapter}
      filePicker={composerFilePicker}
      renderPlusMenu={renderPlusMenu}
      skillInstructionPrompt={skillInstructionPrompt}
      mcpServerInstructionPrompt={mcpServerInstructionPrompt}
    />
  );
});

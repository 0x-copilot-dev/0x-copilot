// Web substrate wiring for the shared `OnboardingComposer` (P3).
//
// `OnboardingComposer` mounts the shared `AssistantComposer` directly, so —
// unlike the ChatScreen path, which goes through the web `AssistantComposer`
// wrapper — the onboarding binder has to supply the chat-surface-shaped
// substrate touchpoints itself: an `AttachmentAdapter`, a `FilePickerPort`, the
// `+`-menu portal renderer, and the instruction-prompt builders. Every one is
// REUSED from the ChatScreen composer stack (no re-authoring):
//   • attachment adapter — bridges the host runtime two-stage onboarding
//     adapter (`createOnboardingAttachmentAdapter`, file-before-text so the CSV
//     chip lands as a `file` part) to the chat-surface `add(file)` /
//     `remove(id)` / `send(pending)` shape. The bridge is the same translation
//     the web `AssistantComposer` performs inline (FR-1.10) — kept here too
//     because `OnboardingComposer` bypasses that wrapper.
//   • file picker — the ChatScreen hidden-`<input>` `ComposerFilePicker`.
//   • `+` menu — the ChatScreen `AnchoredPlusMenu` portal + outside-click.
//   • prompts — the ChatScreen skill / MCP instruction-prompt builders.

import { type ReactElement } from "react";

import type {
  AssistantComposerPlusMenuSlotArgs,
  AttachmentAdapter as ChatSurfaceAttachmentAdapter,
  CompleteAttachment as ChatSurfaceCompleteAttachment,
  PendingAttachment as ChatSurfacePendingAttachment,
} from "@0x-copilot/chat-surface";

import { AnchoredPlusMenu } from "../chat/components/composer/AnchoredPlusMenu";
import { ComposerFilePicker } from "../chat/components/composer/composerFilePicker";
import { createOnboardingAttachmentAdapter } from "../chat/runtime/attachments/onboardingAdapter";
import type {
  Attachment,
  AttachmentAdapter,
  PendingAttachment,
} from "../chat/runtime/types";

export {
  mcpServerInstructionPrompt,
  skillInstructionPrompt,
} from "../chat/prompts";

// Substrate-bound singleton — one hidden-input file picker for the onboarding
// composer (mirrors the ChatScreen composer's module singleton; stateless).
export const onboardingFilePicker = new ComposerFilePicker();

/** Portal + outside-click `+`-menu renderer for the onboarding composer,
 *  reusing the ChatScreen `AnchoredPlusMenu` (owns `createPortal` / document). */
export function renderOnboardingPlusMenu({
  open,
  anchorRef,
  onDismiss,
  children,
}: AssistantComposerPlusMenuSlotArgs): ReactElement {
  return (
    <AnchoredPlusMenu open={open} anchorRef={anchorRef} onDismiss={onDismiss}>
      {children}
    </AnchoredPlusMenu>
  );
}

/**
 * Build a chat-surface `AttachmentAdapter` for the onboarding composer, bridging
 * the host runtime two-stage onboarding adapter. Each call constructs its own
 * runtime adapter + id→attachment registry (the composite dispatches `remove`
 * on the original attachment's MIME, so the chat-surface `remove(id)` call has
 * to be routable back to the runtime attachment).
 */
export function createOnboardingChatSurfaceAttachmentAdapter(): ChatSurfaceAttachmentAdapter {
  const runtime: AttachmentAdapter = createOnboardingAttachmentAdapter();
  const registry = new Map<string, Attachment>();
  return {
    async add(file: File): Promise<ChatSurfacePendingAttachment> {
      const pending = await runtime.add({ file });
      registry.set(pending.id, pending);
      return {
        id: pending.id,
        name: pending.name,
        // `pending.contentType` is only populated by some adapters; fall back
        // to the file's MIME type so the chat-surface pill still labels it.
        type: pending.contentType ?? file.type ?? pending.type,
        size: file.size,
        // The chat-surface "pending" status union is narrower than the
        // runtime's `requires-action | running`; both map to "pending" here.
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
      // Forward the runtime CompleteAttachment verbatim plus the chat-surface
      // display fields — `onSubmit` downstream (`toReadableRunAttachments`)
      // reads the runtime fields (id/type/name/contentType/content/file) to
      // build the run `attachments[]` body. chat-surface's CompleteAttachment
      // is a structural superset, so widen via `unknown` at the slot boundary.
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
}

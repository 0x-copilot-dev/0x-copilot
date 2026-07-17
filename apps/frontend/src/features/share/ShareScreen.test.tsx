/**
 * PR 6.1/6.2 — ShareScreen recipient view behavioural tests.
 *
 * Pins the contracts the rest of the system depends on:
 *
 *   - Loading → Preview gate → snapshot fetch happens in that order.
 *   - Blocked previews surface the right copy per ``reason`` value.
 *   - Sources-restricted shares render citation chips as the
 *     "restricted" tag, NEVER as live links.
 *   - Fork CTA round-trips through ``forkShare`` and notifies the
 *     parent with the new conversation id.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  RecipientPreview,
  SharedConversationView,
} from "@0x-copilot/api-types";

import { ShareScreen } from "./ShareScreen";

vi.mock("../../api/agentApi", async () => {
  const actual =
    await vi.importActual<typeof import("../../api/agentApi")>(
      "../../api/agentApi",
    );
  return {
    ...actual,
    previewSharedConversation: vi.fn(),
    getSharedConversation: vi.fn(),
    forkShare: vi.fn(),
  };
});

import {
  forkShare,
  getSharedConversation,
  previewSharedConversation,
} from "../../api/agentApi";

const IDENTITY = { orgId: "org_acme", userId: "usr_marcus" };

function makePreview(
  overrides: Partial<RecipientPreview> = {},
): RecipientPreview {
  return {
    can_view: true,
    reason: "ok",
    share: {
      share_id: "share_01",
      view_access: "workspace",
      sources_visible_to_viewer: true,
      snapshot_at: "2026-05-06T14:00:00Z",
      shared_by: { user_id: "usr_sarah", display_name: "Sarah Chen" },
    },
    ...overrides,
  };
}

function makeView(
  overrides: Partial<SharedConversationView> = {},
): SharedConversationView {
  return {
    share: makePreview().share,
    conversation: {
      conversation_id: "conv_01",
      org_id: "org_acme",
      user_id: "usr_sarah",
      assistant_id: null,
      title: "FY26 Q1 launch announcement draft",
      created_at: "2026-05-06T13:30:00Z",
      updated_at: "2026-05-06T13:55:00Z",
      status: "active",
      metadata: {},
    } as unknown as SharedConversationView["conversation"],
    messages: [
      {
        message_id: "msg_user",
        conversation_id: "conv_01",
        org_id: "org_acme",
        run_id: "run_01",
        role: "user",
        content_text: "Draft the launch announcement.",
        content_format: "text",
        parent_message_id: null,
        token_count: 6,
        trace_id: null,
        status: "created",
        created_at: "2026-05-06T13:31:00Z",
        edited_at: null,
        deleted_at: null,
      },
      {
        message_id: "msg_asst",
        conversation_id: "conv_01",
        org_id: "org_acme",
        run_id: "run_01",
        role: "assistant",
        content_text:
          "Per the approved positioning [c1] and the GTM plan [c2]…",
        content_format: "markdown",
        parent_message_id: "msg_user",
        token_count: 30,
        trace_id: null,
        status: "created",
        created_at: "2026-05-06T13:32:00Z",
        edited_at: null,
        deleted_at: null,
      },
    ],
    events_by_run_id: {},
    sources: [],
    drafts: [],
    subagents: [],
    ...overrides,
  };
}

describe("ShareScreen", () => {
  beforeEach(() => {
    vi.mocked(previewSharedConversation).mockReset();
    vi.mocked(getSharedConversation).mockReset();
    vi.mocked(forkShare).mockReset();
  });

  it("loads the preview then the snapshot when access is allowed", async () => {
    vi.mocked(previewSharedConversation).mockResolvedValue(makePreview());
    vi.mocked(getSharedConversation).mockResolvedValue(makeView());

    render(
      <ShareScreen
        token="tk_visible"
        identity={IDENTITY}
        onForked={vi.fn()}
        onBackToChat={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(
        screen.getByText("FY26 Q1 launch announcement draft"),
      ).toBeInTheDocument();
    });

    expect(previewSharedConversation).toHaveBeenCalledWith(
      "tk_visible",
      IDENTITY,
    );
    expect(getSharedConversation).toHaveBeenCalledWith("tk_visible", IDENTITY);
    // Recipient sees who shared.
    expect(screen.getByText(/Sarah Chen/)).toBeInTheDocument();
  });

  it("renders citation chips as restricted when sources are hidden", async () => {
    vi.mocked(previewSharedConversation).mockResolvedValue(
      makePreview({
        share: {
          ...makePreview().share,
          sources_visible_to_viewer: false,
        },
      }),
    );
    vi.mocked(getSharedConversation).mockResolvedValue(
      makeView({
        share: {
          ...makeView().share,
          sources_visible_to_viewer: false,
        },
      }),
    );

    render(
      <ShareScreen
        token="tk_restricted"
        identity={IDENTITY}
        onForked={vi.fn()}
        onBackToChat={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(
        screen.getByText("FY26 Q1 launch announcement draft"),
      ).toBeInTheDocument();
    });

    // Both [c1] and [c2] tokens render as the restricted tag.
    const restricted = screen.getAllByLabelText("Source restricted");
    expect(restricted).toHaveLength(2);
    expect(restricted[0]).toHaveTextContent("restricted");
    // The "Sources restricted" header badge is present too.
    expect(screen.getByText("Sources restricted")).toBeInTheDocument();
  });

  it("renders linkable citation chips when sources are visible", async () => {
    vi.mocked(previewSharedConversation).mockResolvedValue(makePreview());
    vi.mocked(getSharedConversation).mockResolvedValue(makeView());

    render(
      <ShareScreen
        token="tk_visible"
        identity={IDENTITY}
        onForked={vi.fn()}
        onBackToChat={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(
        screen.getByText("FY26 Q1 launch announcement draft"),
      ).toBeInTheDocument();
    });

    expect(screen.queryByLabelText("Source restricted")).toBeNull();
    // Two non-restricted chip elements (one for c1, one for c2). They
    // render the integer id; we assert by class so the text-only "1"
    // doesn't collide with timestamps.
    const chips = document.querySelectorAll(
      ".shared-message__chip:not(.shared-message__chip--restricted)",
    );
    expect(chips).toHaveLength(2);
  });

  it.each([
    ["revoked", "This share has been revoked."],
    ["expired", "This share has expired."],
    ["not_recipient", "You don't have access to this share."],
    ["foreign_org", "This share belongs to a different workspace."],
    ["share_not_found", "Share not found."],
  ] as const)(
    "shows the %s headline when access is blocked",
    async (reason, headline) => {
      vi.mocked(previewSharedConversation).mockResolvedValue(
        makePreview({ can_view: false, reason }),
      );

      render(
        <ShareScreen
          token={`tk_${reason}`}
          identity={IDENTITY}
          onForked={vi.fn()}
          onBackToChat={vi.fn()}
        />,
      );

      await waitFor(() => {
        expect(screen.getByText(headline)).toBeInTheDocument();
      });
      // Heavy snapshot fetch never happens for blocked previews — the
      // gate is the whole point.
      expect(getSharedConversation).not.toHaveBeenCalled();
    },
  );

  it("forks via the API and notifies the parent on success", async () => {
    vi.mocked(previewSharedConversation).mockResolvedValue(makePreview());
    vi.mocked(getSharedConversation).mockResolvedValue(makeView());
    vi.mocked(forkShare).mockResolvedValue({
      conversation_id: "conv_forked",
      parent_conversation_id: "conv_01",
      forked_from_share_id: "share_01",
      fork_message_count: 2,
      title: "FY26 Q1 launch announcement draft",
      folder: null,
      created_at: "2026-05-06T14:05:00Z",
      user_id: "usr_marcus",
    });
    const onForked = vi.fn();

    render(
      <ShareScreen
        token="tk_visible"
        identity={IDENTITY}
        onForked={onForked}
        onBackToChat={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(
        screen.getByText("FY26 Q1 launch announcement draft"),
      ).toBeInTheDocument();
    });

    await userEvent.click(
      screen.getByRole("button", { name: /Fork to my chat/ }),
    );

    await waitFor(() => {
      expect(forkShare).toHaveBeenCalledWith("tk_visible", {
        title: "FY26 Q1 launch announcement draft",
      });
    });
    expect(onForked).toHaveBeenCalledWith("conv_forked");
  });

  it("surfaces a fork error inline without losing the snapshot", async () => {
    vi.mocked(previewSharedConversation).mockResolvedValue(makePreview());
    vi.mocked(getSharedConversation).mockResolvedValue(makeView());
    vi.mocked(forkShare).mockRejectedValue(
      new Error("source conversation soft-deleted"),
    );
    const onForked = vi.fn();

    render(
      <ShareScreen
        token="tk_visible"
        identity={IDENTITY}
        onForked={onForked}
        onBackToChat={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(
        screen.getByText("FY26 Q1 launch announcement draft"),
      ).toBeInTheDocument();
    });

    await userEvent.click(
      screen.getByRole("button", { name: /Fork to my chat/ }),
    );

    await waitFor(() => {
      expect(
        screen.getByText("source conversation soft-deleted"),
      ).toBeInTheDocument();
    });
    // Snapshot remains rendered — we don't unmount on error.
    expect(
      screen.getByText("FY26 Q1 launch announcement draft"),
    ).toBeInTheDocument();
    expect(onForked).not.toHaveBeenCalled();
  });
});

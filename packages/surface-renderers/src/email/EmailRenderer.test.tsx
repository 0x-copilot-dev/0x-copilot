import { afterEach, describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { clearRegistry, resolveAdapter } from "@0x-copilot/chat-surface";

import { emailAdapter, type EmailDiff, type EmailState } from "./EmailRenderer";
import { registerEmailAdapter } from ".";

const CURRENT: EmailState = {
  to: "jordan.reyes@acme.com",
  cc: "s.park@yourco.com",
  subject: "Renewal terms — Q4 wrap and FY27 path",
  body: "Hi Jordan,\n\nThanks for the call this morning.\n\nBest,\nSam",
};

const PENDING_DIFF: EmailDiff = {
  base: CURRENT,
  pending: {
    provenance: "DRAFTED FROM SALESFORCE + Q4 SHEET",
    title: "Locked-price block sourced from MSA §3.2.",
    description: "Approve to send when ready.",
    bodyPrefix: "Hi Jordan,\n\n",
    streamingBody:
      "Confirming the locked-price block from MSA §3.2: per-seat rate stays at $84.",
    bodySuffix: "\n\nLet me know if you'd like to walk through the deltas.",
    progressPercent: 60,
    streaming: false,
  },
};

const STREAMING_DIFF: EmailDiff = {
  ...PENDING_DIFF,
  pending: {
    ...PENDING_DIFF.pending,
    streaming: true,
    streamingBody: "Confirming the locked-price block from MSA §3.2: ",
    progressPercent: 35,
  },
};

describe("emailAdapter contract", () => {
  it("registers scheme 'email' with first-party metadata", () => {
    expect(emailAdapter.scheme).toBe("email");
    expect(emailAdapter.metadata.origin).toBe("first-party");
    expect(emailAdapter.metadata.schemaVersion).toBe(1);
  });

  it("matches only email:// uris", () => {
    expect(emailAdapter.matches("email://draft-1")).toBe(true);
    expect(emailAdapter.matches("email://thread/abc")).toBe(true);
    expect(emailAdapter.matches("sf-opp://oppty-9")).toBe(false);
    expect(emailAdapter.matches("notanuri")).toBe(false);
    expect(emailAdapter.matches("")).toBe(false);
  });
});

describe("emailAdapter.renderCurrent", () => {
  it("renders the composer chrome with To / Cc / Subject populated", () => {
    render(emailAdapter.renderCurrent(CURRENT));
    expect(screen.getByTestId("email-renderer")).toBeInTheDocument();
    expect(screen.getByLabelText("To:")).toHaveValue(CURRENT.to);
    expect(screen.getByLabelText("Cc:")).toHaveValue(CURRENT.cc);
    expect(screen.getByLabelText("Subject:")).toHaveValue(CURRENT.subject);
  });

  it("renders the body text in a paragraph", () => {
    render(emailAdapter.renderCurrent(CURRENT));
    expect(
      screen.getByText(/Thanks for the call this morning\./),
    ).toBeVisible();
  });

  it("renders the Send and Schedule footer buttons", () => {
    render(emailAdapter.renderCurrent(CURRENT));
    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Schedule" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save draft" }),
    ).toBeInTheDocument();
  });

  it("renders the default Auto-saved indicator", () => {
    render(emailAdapter.renderCurrent(CURRENT));
    expect(screen.getByTestId("email-auto-saved")).toHaveTextContent(
      "Auto-saved · 2s ago",
    );
  });

  it("respects an overridden autoSavedLabel from state", () => {
    render(
      emailAdapter.renderCurrent({
        ...CURRENT,
        autoSavedLabel: "Auto-saved · 12s ago",
      }),
    );
    expect(screen.getByTestId("email-auto-saved")).toHaveTextContent(
      "Auto-saved · 12s ago",
    );
  });

  it("does not render Approve / Reject controls (host concern)", () => {
    render(emailAdapter.renderCurrent(CURRENT));
    expect(
      screen.queryByRole("button", { name: /approve/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /reject/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("pending-block")).not.toBeInTheDocument();
  });

  it("does not render a streaming cursor in the current view", () => {
    render(emailAdapter.renderCurrent(CURRENT));
    expect(screen.queryByTestId("streaming-cursor")).not.toBeInTheDocument();
    expect(screen.queryByTestId("drafting-pill")).not.toBeInTheDocument();
  });
});

describe("emailAdapter.renderDiff", () => {
  it("renders the same composer chrome populated from diff.base", () => {
    render(emailAdapter.renderDiff(PENDING_DIFF));
    expect(screen.getByTestId("email-renderer")).toBeInTheDocument();
    expect(screen.getByLabelText("To:")).toHaveValue(CURRENT.to);
    expect(screen.getByLabelText("Cc:")).toHaveValue(CURRENT.cc);
    expect(screen.getByLabelText("Subject:")).toHaveValue(CURRENT.subject);
  });

  it("renders the PENDING block with the provenance label", () => {
    render(emailAdapter.renderDiff(PENDING_DIFF));
    const block = screen.getByTestId("pending-block");
    expect(block).toBeInTheDocument();
    expect(block).toHaveAttribute("data-state", "pending");
    expect(within(block).getByTestId("pending-label")).toHaveTextContent(
      `PENDING · ${PENDING_DIFF.pending.provenance}`,
    );
  });

  it("renders the streaming body content inside the PENDING block", () => {
    render(emailAdapter.renderDiff(PENDING_DIFF));
    expect(screen.getByTestId("pending-body")).toHaveTextContent(
      PENDING_DIFF.pending.streamingBody,
    );
  });

  it("renders a provenance pill for the changed region", () => {
    render(emailAdapter.renderDiff(PENDING_DIFF));
    expect(screen.getByTestId("email-provenance-pill")).toHaveTextContent(
      PENDING_DIFF.pending.provenance,
    );
    expect(screen.getByTestId("pending-label")).toHaveTextContent(
      `PENDING · ${PENDING_DIFF.pending.provenance}`,
    );
  });

  it("renders the pending diff title and description without action buttons", () => {
    render(emailAdapter.renderDiff(PENDING_DIFF));
    const summary = screen.getByTestId("email-pending-summary");
    expect(summary).toHaveTextContent(PENDING_DIFF.pending.title);
    expect(summary).toHaveTextContent(PENDING_DIFF.pending.description!);
  });

  it("renders the body prefix and suffix around the PENDING block", () => {
    render(emailAdapter.renderDiff(PENDING_DIFF));
    expect(screen.getByText(/Hi Jordan,/)).toBeVisible();
    expect(screen.getByText(/walk through the deltas/)).toBeVisible();
  });

  it("does NOT render Approve / Reject buttons in the diff (host owns them)", () => {
    render(emailAdapter.renderDiff(PENDING_DIFF));
    expect(
      screen.queryByTestId("tc-inline-diff-approve"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("tc-inline-diff-reject"),
    ).not.toBeInTheDocument();
  });

  it("shows the streaming cursor and drafting pill when pending.streaming is true", () => {
    render(emailAdapter.renderDiff(STREAMING_DIFF));
    expect(screen.getByTestId("streaming-cursor")).toBeInTheDocument();
    expect(screen.getByTestId("drafting-pill")).toBeInTheDocument();
    expect(screen.getByTestId("pending-block")).toHaveAttribute(
      "data-state",
      "streaming",
    );
  });

  it("delegates to TcInlineDiff (streaming state) for streaming diffs and shows no buttons", () => {
    render(emailAdapter.renderDiff(STREAMING_DIFF));
    expect(screen.getByTestId("tc-inline-diff-pill")).toHaveTextContent(
      /STREAMING/,
    );
    expect(
      screen.queryByTestId("tc-inline-diff-approve"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("tc-inline-diff-reject"),
    ).not.toBeInTheDocument();
  });

  it("hides the streaming cursor in non-streaming pending diffs", () => {
    render(emailAdapter.renderDiff(PENDING_DIFF));
    expect(screen.queryByTestId("streaming-cursor")).not.toBeInTheDocument();
    expect(screen.queryByTestId("drafting-pill")).not.toBeInTheDocument();
  });
});

describe("emailAdapter accessibility", () => {
  it("exposes the composer as a labelled form", () => {
    render(emailAdapter.renderCurrent(CURRENT));
    expect(
      screen.getByRole("form", { name: "Email composer" }),
    ).toBeInTheDocument();
  });

  it("pairs every field with a semantic <label htmlFor=…>", () => {
    render(emailAdapter.renderCurrent(CURRENT));
    expect(screen.getByLabelText("To:")).toHaveAttribute("id", "email-to");
    expect(screen.getByLabelText("Cc:")).toHaveAttribute("id", "email-cc");
    expect(screen.getByLabelText("Subject:")).toHaveAttribute(
      "id",
      "email-subject",
    );
  });

  it("does not put Approve / Reject in the tab order (those live in host TcSurfaceMount)", () => {
    render(emailAdapter.renderCurrent(CURRENT));
    const buttons = screen.getAllByRole("button").map((b) => b.textContent);
    expect(buttons).toEqual(["Save draft", "Send", "Schedule"]);
  });

  it("marks the PENDING block with an aria-label so assistive tech can announce it", () => {
    render(emailAdapter.renderDiff(PENDING_DIFF));
    const block = screen.getByLabelText("Pending edit");
    expect(block).toBe(screen.getByTestId("pending-block"));
  });
});

describe("registerEmailAdapter", () => {
  afterEach(() => {
    clearRegistry();
  });

  it("registers under scheme 'email' so resolveAdapter finds it", () => {
    registerEmailAdapter();
    const resolved = resolveAdapter("email://draft-1");
    expect(resolved).not.toBeNull();
    expect(resolved?.scheme).toBe("email");
    expect(resolved?.metadata.schemaVersion).toBe(1);
  });

  it("does not match non-email schemes after registration", () => {
    registerEmailAdapter();
    expect(resolveAdapter("sf-opp://oppty-9")).toBeNull();
  });
});

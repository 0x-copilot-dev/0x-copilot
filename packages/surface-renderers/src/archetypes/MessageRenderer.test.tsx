import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { SurfaceState } from "../_shared/specTypes";
import {
  GMAIL_MESSAGE_DATA,
  GMAIL_MESSAGE_DIFF,
  GMAIL_MESSAGE_STATE,
} from "./fixtures";
import { messageAdapter } from "./MessageRenderer";

describe("messageAdapter contract", () => {
  it("registers scheme 'message' with first-party metadata", () => {
    expect(messageAdapter.scheme).toBe("message");
    expect(messageAdapter.metadata.origin).toBe("first-party");
    expect(messageAdapter.metadata.schemaVersion).toBe(1);
  });

  it("matches only message:// uris", () => {
    expect(messageAdapter.matches("message://gmail/abc")).toBe(true);
    expect(messageAdapter.matches("doc://x")).toBe(false);
  });
});

// AC1 — golden render for gmail_message.
describe("messageAdapter.renderCurrent (golden: gmail_message)", () => {
  it("renders the subject as the composer title and from as subtitle", () => {
    render(messageAdapter.renderCurrent(GMAIL_MESSAGE_STATE));
    expect(screen.getByTestId("message-renderer")).toHaveAttribute(
      "data-spec",
      "present",
    );
    expect(screen.getByTestId("surface-title")).toHaveTextContent(
      "Renewal terms — Q4 wrap and FY27 path",
    );
    expect(screen.getByTestId("surface-subtitle")).toHaveTextContent(
      "jordan.reyes@acme.com",
    );
  });

  it("renders the From / To / Snippet fields", () => {
    render(messageAdapter.renderCurrent(GMAIL_MESSAGE_STATE));
    expect(screen.getByTestId("field-message.from-value")).toHaveTextContent(
      "jordan.reyes@acme.com",
    );
    expect(screen.getByTestId("field-message.to-value")).toHaveTextContent(
      "sam.park@yourco.com",
    );
    expect(screen.getByTestId("field-message.snippet-value")).toHaveTextContent(
      "locked-price block",
    );
  });

  it("renders the spec link as a validated anchor", () => {
    render(messageAdapter.renderCurrent(GMAIL_MESSAGE_STATE));
    expect(screen.getByTestId("surface-link")).toHaveAttribute(
      "href",
      GMAIL_MESSAGE_DATA.message.url,
    );
  });
});

describe("messageAdapter.renderDiff", () => {
  it("renders a PENDING body block over the proposed change", () => {
    render(messageAdapter.renderDiff(GMAIL_MESSAGE_DIFF));
    const block = screen.getByTestId("message-pending-block");
    expect(block).toHaveAttribute("data-state", "pending");
    expect(screen.getByTestId("message-pending-label")).toHaveTextContent(
      "PENDING",
    );
    expect(screen.getByTestId("field-message.snippet-next")).toHaveTextContent(
      "Confirming the locked-price block from MSA §3.2.",
    );
  });
});

describe("messageAdapter spec-less fallback", () => {
  it("renders the Preparing hint without throwing", () => {
    const state: SurfaceState = { data: GMAIL_MESSAGE_DATA };
    expect(() => render(messageAdapter.renderCurrent(state))).not.toThrow();
    expect(screen.getByTestId("surface-preparing-hint")).toBeInTheDocument();
  });
});

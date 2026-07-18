// 4-zone consent card render contract (FR-1.20, FR-1.23). Hoisted with the
// component from apps/frontend; the same DOM the web app renders today.
//
// Guards: the header/params/actions/footer zones, the vendor·access pill,
// and the "empty params → no inset frame" branch stay byte-identical after
// the move.

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApprovalCard } from "./ApprovalCard";

describe("ApprovalCard", () => {
  it("renders all four zones with data-status=waiting", () => {
    const { container } = render(
      <ApprovalCard
        title="Search your Linear issues?"
        reason="Copilot is asking before it reads outside this chat."
        actions={<button type="button">Approve</button>}
        reassurance="You're always asked before Copilot writes outside this chat."
      />,
    );
    const card = container.querySelector(".atlas-approval-card");
    expect(card).not.toBeNull();
    expect(card?.getAttribute("data-status")).toBe("waiting");
    // Zone 1 — header.
    expect(
      container.querySelector(".atlas-approval-card__title")?.textContent,
    ).toBe("Search your Linear issues?");
    expect(
      container.querySelector(".atlas-approval-card__reason")?.textContent,
    ).toBe("Copilot is asking before it reads outside this chat.");
    // Zone 3 — actions.
    expect(
      container.querySelector(".atlas-approval-card__actions button")
        ?.textContent,
    ).toBe("Approve");
    // Zone 4 — footer reassurance.
    expect(
      container.querySelector(".atlas-approval-card__foot")?.textContent,
    ).toContain("You're always asked before Copilot writes outside this chat.");
  });

  it("renders the vendor·access pill with an aria-label", () => {
    const { container } = render(
      <ApprovalCard
        title="Allow Slack send_message?"
        reason="Writes outside your workspace."
        category={{ vendor: "SLACK", access: "WRITE" }}
        actions={<button type="button">Approve</button>}
        reassurance="rule"
      />,
    );
    const pill = container.querySelector(".atlas-approval-card__pill");
    expect(pill).not.toBeNull();
    expect(pill?.getAttribute("aria-label")).toBe("SLACK WRITE");
    expect(
      container.querySelector(".atlas-approval-card__pill-vendor")?.textContent,
    ).toBe("SLACK");
    expect(
      container.querySelector(".atlas-approval-card__pill-access")?.textContent,
    ).toBe("WRITE");
  });

  it("renders the inset params frame when params are present", () => {
    const { container } = render(
      <ApprovalCard
        title="t"
        reason="r"
        params={[
          { label: "Access", value: "Read-only" },
          { label: "Risk", value: "Low" },
        ]}
        actions={<button type="button">Approve</button>}
        reassurance="rule"
      />,
    );
    const frame = container.querySelector(".atlas-approval-card__params");
    expect(frame).not.toBeNull();
    const rows = container.querySelectorAll(".aui-activity-card__param");
    expect(rows).toHaveLength(2);
  });

  it("omits the params frame when params is empty", () => {
    const { container } = render(
      <ApprovalCard
        title="t"
        reason="r"
        params={[]}
        actions={<button type="button">Approve</button>}
        reassurance="rule"
      />,
    );
    expect(container.querySelector(".atlas-approval-card__params")).toBeNull();
  });

  it("renders the tool-details disclosure when details are provided", () => {
    const { container } = render(
      <ApprovalCard
        title="t"
        reason="r"
        actions={<button type="button">Approve</button>}
        reassurance="rule"
        details={<span>raw args</span>}
        detailsLabel="Tool details"
      />,
    );
    const details = container.querySelector(
      "details.aui-activity-card__details",
    );
    expect(details).not.toBeNull();
    expect(
      details?.querySelector(".aui-collapsible__trigger")?.textContent,
    ).toBe("Tool details");
  });
});

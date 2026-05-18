// OffboardingWizard — step machine + cascading vs non-cascading + submit.

import type {
  AgentId,
  ConnectorId,
  Person,
  ProjectId,
  TenantId,
  ToolId,
  UserId,
} from "@enterprise-search/api-types";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { OffboardingWizard, type OffboardingAsset } from "./OffboardingWizard";

function makePerson(over: Partial<Person> = {}): Person {
  return {
    id: "u_target" as UserId,
    tenant_id: "tnt_1" as TenantId,
    display_name: "Departing Dan",
    email: "dan@acme.test",
    role: "member",
    presence: "offline",
    last_seen_at: "2026-05-17T00:00:00.000Z",
    joined_at: "2024-01-01T00:00:00.000Z",
    agents_count: 1,
    projects_count: 1,
    is_self: false,
    ...over,
  };
}

function makePersonOption(over: Partial<Person> = {}): Person {
  return makePerson({
    id: "u_owner" as UserId,
    display_name: "New Owner",
    email: "owner@acme.test",
    ...over,
  });
}

const PROJECT_ASSET: OffboardingAsset = {
  ref: { kind: "project", id: "proj_1" as ProjectId },
  label: "Acme renewal",
};
const AGENT_ASSET: OffboardingAsset = {
  ref: { kind: "agent", id: "agent_1" as AgentId },
  label: "Acme bot",
};
const TOOL_ASSET: OffboardingAsset = {
  ref: { kind: "tool", id: "tool_1" as ToolId },
  label: "Acme tool",
};
const CONNECTOR_ASSET: OffboardingAsset = {
  ref: { kind: "connector", id: "conn_1" as ConnectorId },
  label: "Acme connector",
};

describe("OffboardingWizard", () => {
  it("renders step 1 (confirm) with target name + asset count", () => {
    render(
      <OffboardingWizard
        target={makePerson()}
        assets={[PROJECT_ASSET]}
        personOptions={[makePersonOption()]}
        onOffboard={vi.fn().mockResolvedValue(true)}
      />,
    );
    expect(screen.getByTestId("offboarding-confirm-step")).toBeInTheDocument();
    expect(screen.getByTestId("offboarding-target-name")).toHaveTextContent(
      "Departing Dan",
    );
  });

  it("step section has aria-labelledby pointing at step-N heading", () => {
    render(
      <OffboardingWizard
        target={makePerson()}
        assets={[PROJECT_ASSET]}
        personOptions={[makePersonOption()]}
        onOffboard={vi.fn().mockResolvedValue(true)}
      />,
    );
    const step = screen.getByTestId("offboarding-confirm-step");
    expect(step.tagName).toBe("SECTION");
    expect(step.getAttribute("aria-labelledby")).toBe("step-0");
    expect(document.getElementById("step-0")).not.toBeNull();
  });

  it("advances to step 2 (reassign) on Next", () => {
    render(
      <OffboardingWizard
        target={makePerson()}
        assets={[PROJECT_ASSET]}
        personOptions={[makePersonOption()]}
        onOffboard={vi.fn().mockResolvedValue(true)}
      />,
    );
    fireEvent.click(screen.getByTestId("offboarding-next"));
    expect(screen.getByTestId("offboarding-reassign-step")).toBeInTheDocument();
  });

  it("blocks advance from reassign until every cascading asset has an owner", () => {
    render(
      <OffboardingWizard
        target={makePerson()}
        assets={[PROJECT_ASSET]}
        personOptions={[makePersonOption()]}
        onOffboard={vi.fn().mockResolvedValue(true)}
      />,
    );
    fireEvent.click(screen.getByTestId("offboarding-next"));
    const next = screen.getByTestId("offboarding-next") as HTMLButtonElement;
    expect(next.disabled).toBe(true);
    fireEvent.change(
      screen.getByTestId("offboarding-picker-project:proj_1-select"),
      {
        target: { value: "u_owner" },
      },
    );
    expect(
      (screen.getByTestId("offboarding-next") as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it("surfaces non-cascading kinds with the 'Not supported in v1' notice", () => {
    render(
      <OffboardingWizard
        target={makePerson()}
        assets={[PROJECT_ASSET, AGENT_ASSET, TOOL_ASSET, CONNECTOR_ASSET]}
        personOptions={[makePersonOption()]}
        onOffboard={vi.fn().mockResolvedValue(true)}
      />,
    );
    fireEvent.click(screen.getByTestId("offboarding-next"));
    const block = screen.getByTestId("offboarding-non-cascading-block");
    expect(block).toHaveTextContent("Not supported in v1");
    const rows = screen.getAllByTestId("offboarding-non-cascading-row");
    expect(rows).toHaveLength(3);
  });

  it("steps back from reassign to confirm via Back", () => {
    render(
      <OffboardingWizard
        target={makePerson()}
        assets={[PROJECT_ASSET]}
        personOptions={[makePersonOption()]}
        onOffboard={vi.fn().mockResolvedValue(true)}
      />,
    );
    fireEvent.click(screen.getByTestId("offboarding-next"));
    fireEvent.click(screen.getByTestId("offboarding-back"));
    expect(screen.getByTestId("offboarding-confirm-step")).toBeInTheDocument();
  });

  it("submits OffboardingRequest with cascading reassignments only", async () => {
    const onOffboard = vi.fn().mockResolvedValue(true);
    render(
      <OffboardingWizard
        target={makePerson()}
        assets={[PROJECT_ASSET, AGENT_ASSET]}
        personOptions={[makePersonOption()]}
        onOffboard={onOffboard}
      />,
    );
    // Step 1 → 2
    fireEvent.click(screen.getByTestId("offboarding-next"));
    // Assign project owner.
    fireEvent.change(
      screen.getByTestId("offboarding-picker-project:proj_1-select"),
      {
        target: { value: "u_owner" },
      },
    );
    // Step 2 → 3 (review)
    fireEvent.click(screen.getByTestId("offboarding-next"));
    expect(screen.getByTestId("offboarding-review-step")).toBeInTheDocument();
    expect(
      screen.getByTestId("offboarding-review-non-cascading"),
    ).toHaveTextContent("Manual follow-up");
    fireEvent.click(screen.getByTestId("offboarding-submit"));
    await waitFor(() => expect(onOffboard).toHaveBeenCalledTimes(1));
    expect(onOffboard.mock.calls[0]![0]).toEqual({
      target_user_id: "u_target",
      reassignments: [
        {
          asset: { kind: "project", id: "proj_1" },
          new_owner_user_id: "u_owner",
        },
      ],
    });
  });

  it("renders Done after a successful submit and fires onDone", async () => {
    const onOffboard = vi.fn().mockResolvedValue(true);
    const onDone = vi.fn();
    render(
      <OffboardingWizard
        target={makePerson()}
        assets={[PROJECT_ASSET]}
        personOptions={[makePersonOption()]}
        onOffboard={onOffboard}
        onDone={onDone}
      />,
    );
    fireEvent.click(screen.getByTestId("offboarding-next"));
    fireEvent.change(
      screen.getByTestId("offboarding-picker-project:proj_1-select"),
      {
        target: { value: "u_owner" },
      },
    );
    fireEvent.click(screen.getByTestId("offboarding-next"));
    fireEvent.click(screen.getByTestId("offboarding-submit"));
    await waitFor(() =>
      expect(screen.getByTestId("offboarding-done")).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByTestId("offboarding-done"));
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("renders an error when the host returns false", async () => {
    const onOffboard = vi.fn().mockResolvedValue(false);
    render(
      <OffboardingWizard
        target={makePerson()}
        assets={[PROJECT_ASSET]}
        personOptions={[makePersonOption()]}
        onOffboard={onOffboard}
      />,
    );
    fireEvent.click(screen.getByTestId("offboarding-next"));
    fireEvent.change(
      screen.getByTestId("offboarding-picker-project:proj_1-select"),
      {
        target: { value: "u_owner" },
      },
    );
    fireEvent.click(screen.getByTestId("offboarding-next"));
    fireEvent.click(screen.getByTestId("offboarding-submit"));
    await waitFor(() =>
      expect(screen.getByTestId("offboarding-error")).toBeInTheDocument(),
    );
  });

  it("handles the no-cascading-assets case (advance directly through reassign)", () => {
    render(
      <OffboardingWizard
        target={makePerson()}
        assets={[AGENT_ASSET]}
        personOptions={[makePersonOption()]}
        onOffboard={vi.fn().mockResolvedValue(true)}
      />,
    );
    fireEvent.click(screen.getByTestId("offboarding-next"));
    // No cascading drafts → the canAdvance gate returns true immediately
    // because `every([])` is `true`.
    expect(
      (screen.getByTestId("offboarding-next") as HTMLButtonElement).disabled,
    ).toBe(false);
  });
});

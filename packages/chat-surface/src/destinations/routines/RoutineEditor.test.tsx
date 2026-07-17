// Tests for <RoutineEditor /> — every tab + the pure cron helpers.
//
// Covers:
//   - pure cron helpers (buildCronSpec / parseCronSpec / previewCron /
//     isValidCronSpec)
//   - Name tab: name + description binding, 80/200 maxLength
//   - Instructions tab: Composer mode="compose" reused
//   - Triggers tab: add schedule/webhook/event/manual + remove + cron
//     simple<->advanced toggle + weekly day picker
//   - Connectors tab: per-connector mode toggle (off→inherit→read_only)
//   - Behavior tab: missed_fire_policy radio (default fire_once) +
//     output target + agent_version_pin opt-in
//   - Permissions tab: manual_fire radio + project picker
//   - Save button calls onSave with the assembled Routine shape
//   - ARIA tabs: tablist + tab + tabpanel, arrow keys + Home/End cycle

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ConnectorId, ProjectId } from "@0x-copilot/api-types";

import {
  buildCronSpec,
  CronEditor,
  isValidCronSpec,
  parseCronSpec,
  previewCron,
  RoutineEditor,
  ROUTINE_EDITOR_DEFAULTS,
  type RoutineEditorValue,
} from "./RoutineEditor";

// ===========================================================================
// Pure helpers.
// ===========================================================================

describe("buildCronSpec", () => {
  it("hourly → minute * * * *", () => {
    expect(
      buildCronSpec({
        frequency: "hourly",
        hour: 9,
        minute: 30,
        weekdays: [],
        dayOfMonth: 1,
        raw: "",
      }),
    ).toBe("30 * * * *");
  });

  it("daily → m h * * *", () => {
    expect(
      buildCronSpec({
        frequency: "daily",
        hour: 18,
        minute: 0,
        weekdays: [],
        dayOfMonth: 1,
        raw: "",
      }),
    ).toBe("0 18 * * *");
  });

  it("weekdays → m h * * 1-5", () => {
    expect(
      buildCronSpec({
        frequency: "weekdays",
        hour: 9,
        minute: 0,
        weekdays: [],
        dayOfMonth: 1,
        raw: "",
      }),
    ).toBe("0 9 * * 1-5");
  });

  it("weekly with weekdays sorted Sun→Sat", () => {
    expect(
      buildCronSpec({
        frequency: "weekly",
        hour: 12,
        minute: 0,
        weekdays: [3, 1, 5],
        dayOfMonth: 1,
        raw: "",
      }),
    ).toBe("0 12 * * 1,3,5");
  });

  it("monthly → m h dom * *", () => {
    expect(
      buildCronSpec({
        frequency: "monthly",
        hour: 9,
        minute: 0,
        weekdays: [],
        dayOfMonth: 15,
        raw: "",
      }),
    ).toBe("0 9 15 * *");
  });

  it("custom returns the trimmed raw string", () => {
    expect(
      buildCronSpec({
        frequency: "custom",
        hour: 0,
        minute: 0,
        weekdays: [],
        dayOfMonth: 1,
        raw: "  5 4 * * 0 ",
      }),
    ).toBe("5 4 * * 0");
  });

  it("clamps out-of-range hour/minute/dayOfMonth", () => {
    expect(
      buildCronSpec({
        frequency: "daily",
        hour: 99,
        minute: 99,
        weekdays: [],
        dayOfMonth: 99,
        raw: "",
      }),
    ).toBe("59 23 * * *");
  });
});

describe("parseCronSpec", () => {
  it("round-trips through buildCronSpec — daily", () => {
    const spec = buildCronSpec({
      frequency: "daily",
      hour: 7,
      minute: 30,
      weekdays: [],
      dayOfMonth: 1,
      raw: "",
    });
    const parsed = parseCronSpec(spec);
    expect(parsed.frequency).toBe("daily");
    expect(parsed.hour).toBe(7);
    expect(parsed.minute).toBe(30);
  });

  it("round-trips through buildCronSpec — weekly", () => {
    const spec = buildCronSpec({
      frequency: "weekly",
      hour: 10,
      minute: 0,
      weekdays: [1, 3, 5],
      dayOfMonth: 1,
      raw: "",
    });
    const parsed = parseCronSpec(spec);
    expect(parsed.frequency).toBe("weekly");
    expect(parsed.weekdays).toEqual([1, 3, 5]);
  });

  it("falls back to custom on unknown shapes", () => {
    const parsed = parseCronSpec("*/15 8-18 * * 1-5");
    expect(parsed.frequency).toBe("custom");
    expect(parsed.raw).toBe("*/15 8-18 * * 1-5");
  });

  it("falls back to custom when the spec isn't 5 fields", () => {
    expect(parseCronSpec("0 9 * *").frequency).toBe("custom");
    expect(parseCronSpec("@reboot").frequency).toBe("custom");
  });
});

describe("previewCron", () => {
  it("renders daily preview with timezone", () => {
    expect(previewCron("0 18 * * *", "GMT+5:30")).toContain("18:00");
    expect(previewCron("0 18 * * *", "GMT+5:30")).toContain("GMT+5:30");
  });

  it("renders weekday names for weekly schedules", () => {
    expect(previewCron("0 9 * * 1,3,5", "UTC")).toContain("Mon");
    expect(previewCron("0 9 * * 1,3,5", "UTC")).toContain("Fri");
  });

  it("flags custom specs as Custom", () => {
    expect(previewCron("*/15 * * * *", "UTC")).toContain("Custom");
  });
});

describe("isValidCronSpec", () => {
  it("accepts 5-field specs", () => {
    expect(isValidCronSpec("0 9 * * *")).toBe(true);
    expect(isValidCronSpec("30 8 * * 1-5")).toBe(true);
  });

  it("rejects @reboot and other macros (1-minute granularity rule)", () => {
    expect(isValidCronSpec("@reboot")).toBe(false);
    expect(isValidCronSpec("@yearly")).toBe(false);
  });

  it("rejects 6-field per-second specs", () => {
    expect(isValidCronSpec("*/30 * * * * *")).toBe(false);
  });

  it("rejects empty / whitespace", () => {
    expect(isValidCronSpec("")).toBe(false);
    expect(isValidCronSpec("   ")).toBe(false);
  });
});

// ===========================================================================
// CronEditor (sub-component) — focused interaction tests.
// ===========================================================================

describe("<CronEditor />", () => {
  it("renders the parsed frequency in simple mode and updates on change", () => {
    const onChange = vi.fn();
    render(
      <CronEditor
        cron="0 9 * * *"
        tz="UTC"
        disabled={false}
        onChange={onChange}
      />,
    );
    const select = screen.getByTestId(
      "routine-editor-cron-frequency",
    ) as HTMLSelectElement;
    expect(select.value).toBe("daily");
    fireEvent.change(select, { target: { value: "weekly" } });
    expect(onChange).toHaveBeenCalled();
    // weekly with empty days defaults to Monday in buildCronSpec
    const [cronArg, tzArg] = onChange.mock.calls[0]!;
    expect(typeof cronArg).toBe("string");
    expect(tzArg).toBe("UTC");
  });

  it("switches to advanced mode and accepts a raw cron string", () => {
    const onChange = vi.fn();
    render(
      <CronEditor
        cron="0 9 * * *"
        tz="UTC"
        disabled={false}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByTestId("routine-editor-cron-mode-advanced"));
    const raw = screen.getByTestId(
      "routine-editor-cron-raw",
    ) as HTMLInputElement;
    fireEvent.change(raw, { target: { value: "*/15 8-18 * * 1-5" } });
    expect(onChange).toHaveBeenLastCalledWith("*/15 8-18 * * 1-5", "UTC");
  });

  it("flags invalid specs via aria-invalid + preview text", () => {
    const onChange = vi.fn();
    render(
      <CronEditor cron="" tz="UTC" disabled={false} onChange={onChange} />,
    );
    expect(screen.getByTestId("routine-editor-cron-preview")).toHaveTextContent(
      /invalid cron/i,
    );
  });

  it("renders a weekday picker only when frequency=weekly", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <CronEditor
        cron="0 9 * * *"
        tz="UTC"
        disabled={false}
        onChange={onChange}
      />,
    );
    expect(
      screen.queryByTestId("routine-editor-cron-weekday-1"),
    ).not.toBeInTheDocument();
    rerender(
      <CronEditor
        cron="0 9 * * 1"
        tz="UTC"
        disabled={false}
        onChange={onChange}
      />,
    );
    expect(
      screen.getByTestId("routine-editor-cron-weekday-1"),
    ).toBeInTheDocument();
  });

  it("toggles a weekday when clicked", () => {
    const onChange = vi.fn();
    render(
      <CronEditor
        cron="0 9 * * 1"
        tz="UTC"
        disabled={false}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByTestId("routine-editor-cron-weekday-3"));
    expect(onChange).toHaveBeenCalled();
    const [cronArg] = onChange.mock.calls.at(-1)!;
    expect(cronArg).toContain("1,3");
  });
});

// ===========================================================================
// <RoutineEditor /> — tabs + save callback.
// ===========================================================================

function renderEditor(
  overrides: Partial<React.ComponentProps<typeof RoutineEditor>> = {},
): {
  readonly onSave: ReturnType<typeof vi.fn>;
  readonly onCancel: ReturnType<typeof vi.fn>;
} {
  const onSave = vi.fn();
  const onCancel = vi.fn();
  render(<RoutineEditor onSave={onSave} onCancel={onCancel} {...overrides} />);
  return { onSave, onCancel };
}

describe("<RoutineEditor /> header + tabs", () => {
  it("renders all six tabs as an ARIA tablist", () => {
    renderEditor();
    const tablist = screen.getByRole("tablist", { name: "Routine editor" });
    const tabs = within(tablist).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual([
      "Name",
      "Instructions",
      "Triggers",
      "Connectors",
      "Behavior",
      "Permissions",
    ]);
  });

  it("defaults to the Name tab and switches on click", () => {
    renderEditor();
    expect(
      screen.getByTestId("routine-editor-tabpanel-name"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("routine-editor-tab-triggers"));
    expect(
      screen.getByTestId("routine-editor-tabpanel-triggers"),
    ).toBeInTheDocument();
  });

  it("cycles tabs with ArrowRight / ArrowLeft / Home / End", () => {
    renderEditor();
    const tablist = screen.getByRole("tablist", { name: "Routine editor" });
    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    expect(
      screen.getByTestId("routine-editor-tabpanel-instructions"),
    ).toBeInTheDocument();
    fireEvent.keyDown(tablist, { key: "End" });
    expect(
      screen.getByTestId("routine-editor-tabpanel-permissions"),
    ).toBeInTheDocument();
    fireEvent.keyDown(tablist, { key: "ArrowRight" }); // wraps
    expect(
      screen.getByTestId("routine-editor-tabpanel-name"),
    ).toBeInTheDocument();
    fireEvent.keyDown(tablist, { key: "ArrowLeft" }); // wraps backward
    expect(
      screen.getByTestId("routine-editor-tabpanel-permissions"),
    ).toBeInTheDocument();
    fireEvent.keyDown(tablist, { key: "Home" });
    expect(
      screen.getByTestId("routine-editor-tabpanel-name"),
    ).toBeInTheDocument();
  });

  it("shows the Draft status pill when name is empty / no triggers", () => {
    renderEditor();
    expect(screen.getByTestId("routine-editor")).toHaveAttribute(
      "data-status",
      "draft",
    );
  });

  it("flips to Ready once name + at least one trigger are present", () => {
    renderEditor({
      initialValue: {
        ...ROUTINE_EDITOR_DEFAULTS,
        name: "Weekly briefing",
        triggers: [{ kind: "manual" }],
      },
    });
    expect(screen.getByTestId("routine-editor")).toHaveAttribute(
      "data-status",
      "ready",
    );
  });

  it("Save button calls onSave with the assembled value", () => {
    const { onSave } = renderEditor({
      initialValue: {
        ...ROUTINE_EDITOR_DEFAULTS,
        name: "Daily briefing",
        triggers: [{ kind: "schedule", cron: "0 18 * * 1-5", tz: "UTC" }],
      },
    });
    fireEvent.click(screen.getByTestId("routine-editor-save"));
    expect(onSave).toHaveBeenCalledTimes(1);
    const sent = onSave.mock.calls[0]![0] as RoutineEditorValue;
    expect(sent.name).toBe("Daily briefing");
    expect(sent.triggers).toHaveLength(1);
    expect(sent.missed_fire_policy).toBe("fire_once");
    expect(sent.permissions.manual_fire).toBe("owner");
    expect(sent.agent_version_pin).toBeNull();
  });

  it("Cancel button calls onCancel", () => {
    const { onCancel } = renderEditor();
    fireEvent.click(screen.getByTestId("routine-editor-cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});

describe("Name tab", () => {
  it("binds name + description inputs (200/80 maxLength)", () => {
    renderEditor();
    const name = screen.getByTestId(
      "routine-editor-name-input",
    ) as HTMLInputElement;
    const desc = screen.getByTestId(
      "routine-editor-description-input",
    ) as HTMLTextAreaElement;
    fireEvent.change(name, { target: { value: "Daily briefing" } });
    fireEvent.change(desc, { target: { value: "Runs every weekday morning" } });
    expect(name.value).toBe("Daily briefing");
    expect(desc.value).toBe("Runs every weekday morning");
    expect(name.maxLength).toBe(80);
    expect(desc.maxLength).toBe(200);
  });

  it("the header name input mirrors the Name tab value", () => {
    renderEditor();
    const header = screen.getByTestId(
      "routine-editor-name-header",
    ) as HTMLInputElement;
    fireEvent.change(header, { target: { value: "From header" } });
    fireEvent.click(screen.getByTestId("routine-editor-tab-name"));
    expect(
      (screen.getByTestId("routine-editor-name-input") as HTMLInputElement)
        .value,
    ).toBe("From header");
  });
});

describe("Instructions tab", () => {
  it("renders the shared Composer in compose mode", () => {
    renderEditor();
    fireEvent.click(screen.getByTestId("routine-editor-tab-instructions"));
    const panel = screen.getByTestId("routine-editor-instructions");
    expect(panel).toBeInTheDocument();
    // The shared Composer renders a textarea inside this tabpanel. Tests
    // that the chat-surface Composer is the actual input — not a forked
    // one. (The header carries the name input — scope to the panel.)
    expect(within(panel).getByRole("textbox")).toBeInTheDocument();
  });
});

describe("Triggers tab", () => {
  function openTriggers(): void {
    fireEvent.click(screen.getByTestId("routine-editor-tab-triggers"));
  }

  it("starts empty with an empty-state hint", () => {
    renderEditor();
    openTriggers();
    expect(
      screen.getByTestId("routine-editor-triggers-empty"),
    ).toBeInTheDocument();
  });

  it("adds a schedule trigger with a default cron + CronEditor", () => {
    renderEditor();
    openTriggers();
    fireEvent.click(screen.getByTestId("routine-editor-add-schedule"));
    const card = screen.getByTestId("routine-editor-trigger-0");
    expect(card).toHaveAttribute("data-trigger-kind", "schedule");
    expect(within(card).getByTestId("routine-editor-cron")).toBeInTheDocument();
  });

  it("adds webhook + event + manual triggers", () => {
    renderEditor();
    openTriggers();
    fireEvent.click(screen.getByTestId("routine-editor-add-webhook"));
    fireEvent.click(screen.getByTestId("routine-editor-add-event"));
    fireEvent.click(screen.getByTestId("routine-editor-add-manual"));
    expect(screen.getByTestId("routine-editor-trigger-0")).toHaveAttribute(
      "data-trigger-kind",
      "webhook",
    );
    expect(screen.getByTestId("routine-editor-trigger-1")).toHaveAttribute(
      "data-trigger-kind",
      "event",
    );
    expect(screen.getByTestId("routine-editor-trigger-2")).toHaveAttribute(
      "data-trigger-kind",
      "manual",
    );
  });

  it("disables the manual button after one manual trigger is added", () => {
    renderEditor();
    openTriggers();
    fireEvent.click(screen.getByTestId("routine-editor-add-manual"));
    expect(screen.getByTestId("routine-editor-add-manual")).toBeDisabled();
  });

  it("removes a trigger when its remove button is clicked", () => {
    renderEditor();
    openTriggers();
    fireEvent.click(screen.getByTestId("routine-editor-add-schedule"));
    fireEvent.click(screen.getByTestId("routine-editor-trigger-0-remove"));
    expect(
      screen.queryByTestId("routine-editor-trigger-0"),
    ).not.toBeInTheDocument();
  });

  it("webhook editor binds the IP allowlist input", () => {
    renderEditor();
    openTriggers();
    fireEvent.click(screen.getByTestId("routine-editor-add-webhook"));
    const input = screen.getByTestId(
      "routine-editor-webhook-allowlist",
    ) as HTMLInputElement;
    fireEvent.change(input, {
      target: { value: "10.0.0.0/8, 192.168.1.0/24" },
    });
    expect(input.value).toBe("10.0.0.0/8, 192.168.1.0/24");
  });

  it("event editor offers a server-allowlisted event source list", () => {
    renderEditor();
    openTriggers();
    fireEvent.click(screen.getByTestId("routine-editor-add-event"));
    const sel = screen.getByTestId(
      "routine-editor-event-source",
    ) as HTMLSelectElement;
    expect(sel.value).toBe("inbox.item_created");
    fireEvent.change(sel, { target: { value: "library.page_created" } });
    expect(sel.value).toBe("library.page_created");
  });
});

describe("Connectors tab", () => {
  it("shows the empty state when no connectors are available", () => {
    renderEditor();
    fireEvent.click(screen.getByTestId("routine-editor-tab-connectors"));
    expect(
      screen.getByTestId("routine-editor-connectors-empty"),
    ).toBeInTheDocument();
  });

  it("renders one row per available connector and toggles its mode", () => {
    renderEditor({
      availableConnectors: [
        { connector_id: "salesforce" as ConnectorId, label: "Salesforce" },
        { connector_id: "gmail" as ConnectorId, label: "Gmail" },
      ],
    });
    fireEvent.click(screen.getByTestId("routine-editor-tab-connectors"));
    const sel = screen.getByTestId(
      "routine-editor-connector-salesforce-mode",
    ) as HTMLSelectElement;
    expect(sel.value).toBe("off");
    fireEvent.change(sel, { target: { value: "inherit" } });
    expect(sel.value).toBe("inherit");
    fireEvent.change(sel, { target: { value: "read_only" } });
    expect(sel.value).toBe("read_only");
    fireEvent.change(sel, { target: { value: "off" } });
    expect(sel.value).toBe("off");
  });
});

describe("Behavior tab", () => {
  function openBehavior(): void {
    fireEvent.click(screen.getByTestId("routine-editor-tab-behavior"));
  }

  it("defaults missed_fire_policy to fire_once (cross-audit §9.7 Q7)", () => {
    renderEditor();
    openBehavior();
    const radio = screen.getByTestId(
      "routine-editor-missed-fire-policy-fire_once",
    );
    expect(within(radio).getByRole("radio")).toBeChecked();
  });

  it("changes missed_fire_policy when another radio is clicked", () => {
    const { onSave } = renderEditor({
      initialValue: {
        ...ROUTINE_EDITOR_DEFAULTS,
        name: "X",
        triggers: [{ kind: "manual" }],
      },
    });
    openBehavior();
    fireEvent.click(
      within(
        screen.getByTestId("routine-editor-missed-fire-policy-fire_all"),
      ).getByRole("radio"),
    );
    fireEvent.click(screen.getByTestId("routine-editor-save"));
    expect(
      (onSave.mock.calls[0]![0] as RoutineEditorValue).missed_fire_policy,
    ).toBe("fire_all");
  });

  it("output target kind defaults to inbox and switches via select", () => {
    renderEditor();
    openBehavior();
    const sel = screen.getByTestId(
      "routine-editor-output-target-kind",
    ) as HTMLSelectElement;
    expect(sel.value).toBe("inbox");
    fireEvent.change(sel, { target: { value: "library_page" } });
    // No ref yet → falls back to inbox (host picks ref later); editor
    // stays consistent and the select reflects the inbox fallback.
    expect(sel.value).toBe("inbox");
  });

  it("agent_version_pin starts disabled (live re-resolve)", () => {
    renderEditor();
    openBehavior();
    const toggle = within(
      screen.getByTestId("routine-editor-agent-version-pin-toggle"),
    ).getByRole("checkbox");
    expect(toggle).not.toBeChecked();
    expect(
      screen.queryByTestId("routine-editor-agent-version-pin-input"),
    ).not.toBeInTheDocument();
  });

  it("agent_version_pin checkbox reveals the version input and saves it", () => {
    const { onSave } = renderEditor({
      initialValue: {
        ...ROUTINE_EDITOR_DEFAULTS,
        name: "X",
        triggers: [{ kind: "manual" }],
      },
    });
    openBehavior();
    const toggle = within(
      screen.getByTestId("routine-editor-agent-version-pin-toggle"),
    ).getByRole("checkbox");
    fireEvent.click(toggle);
    const versionInput = screen.getByTestId(
      "routine-editor-agent-version-pin-input",
    ) as HTMLInputElement;
    fireEvent.change(versionInput, { target: { value: "v1.4.2" } });
    fireEvent.click(screen.getByTestId("routine-editor-save"));
    expect(
      (onSave.mock.calls[0]![0] as RoutineEditorValue).agent_version_pin,
    ).toBe("v1.4.2");
  });
});

describe("Permissions tab", () => {
  function openPermissions(): void {
    fireEvent.click(screen.getByTestId("routine-editor-tab-permissions"));
  }

  it("defaults manual_fire to owner (routines-prd §3.11)", () => {
    renderEditor();
    openPermissions();
    expect(
      within(screen.getByTestId("routine-editor-manual-fire-owner")).getByRole(
        "radio",
      ),
    ).toBeChecked();
  });

  it("disables 'project_members' when no project is selected", () => {
    renderEditor();
    openPermissions();
    expect(
      within(
        screen.getByTestId("routine-editor-manual-fire-project_members"),
      ).getByRole("radio"),
    ).toBeDisabled();
  });

  it("enables 'project_members' once a project is picked, and saves the manual_fire override", () => {
    const { onSave } = renderEditor({
      availableProjects: [
        { project_id: "p1" as ProjectId, label: "Acme renewal" },
      ],
      initialValue: {
        ...ROUTINE_EDITOR_DEFAULTS,
        name: "X",
        triggers: [{ kind: "manual" }],
      },
    });
    openPermissions();
    const projectSel = screen.getByTestId(
      "routine-editor-project",
    ) as HTMLSelectElement;
    fireEvent.change(projectSel, { target: { value: "p1" } });
    const pmRadio = within(
      screen.getByTestId("routine-editor-manual-fire-project_members"),
    ).getByRole("radio");
    expect(pmRadio).not.toBeDisabled();
    fireEvent.click(pmRadio);
    fireEvent.click(screen.getByTestId("routine-editor-save"));
    const sent = onSave.mock.calls[0]![0] as RoutineEditorValue;
    expect(sent.project_id).toBe("p1");
    expect(sent.permissions.manual_fire).toBe("project_members");
  });
});

// The connector half of the editable surface, driven end to end.
//
// Every test here goes through the REAL grant — `createConnectorSurfaceEditor`
// closing over a fake Transport — rather than a hand-written `saveEdits` stub.
// That is deliberate: the property under test is not "the component calls a
// function", it is "the value the user typed reaches the write-back route
// unchanged", and a stub in the middle is exactly where a re-formatting step
// would hide.

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  attachConnectorEditor,
  createConnectorSurfaceEditor,
  type ConnectorSurfaceEditorConfig,
} from "@0x-copilot/chat-surface";

import type { SurfaceState } from "../_shared/specTypes";
import { GITHUB_TABLE_STATE } from "./fixtures";
import { tableAdapter } from "./TableRenderer";

const SURFACE = "table://github/list_issues/128";

/** The one request shape the write-back call builds. */
interface SentRequest {
  readonly method: string;
  readonly path: string;
  readonly body: {
    readonly run_id: string;
    readonly edits: readonly {
      readonly row_key: string;
      readonly title: string;
      readonly row: Record<string, unknown>;
      readonly changes: readonly {
        readonly field: string;
        readonly old: unknown;
        readonly new: unknown;
      }[];
    }[];
  };
}

// `surface-renderers` must not import `@0x-copilot/chat-transport` — the ban is
// the D28 rule that keeps a renderer from ever calling a transport itself, and
// eslint enforces it. So the fake is built structurally and handed to the config
// that WILL type it. Same for the HTTP error below: `isTransportHttpError`
// duck-types on `name` + a numeric `status` precisely because the error crosses
// a realm boundary on desktop, so a plain object is the honest stand-in.
type HostTransport = ConnectorSurfaceEditorConfig["transport"];

function transportWith(
  request: (item: SentRequest) => Promise<unknown>,
): HostTransport {
  return {
    request,
    subscribeServerSentEvents: () => ({ close: () => {} }),
    getSession: () => ({ bearer: null }),
    capabilities: () => ({
      substrate: "web",
      nativeSecretStorage: false,
      fileSystemAccess: false,
      clipboardWrite: false,
      openExternal: false,
    }),
  } as unknown as HostTransport;
}

function httpError(status: number, detail: string): Error {
  const error = new Error(detail);
  error.name = "TransportHttpError";
  return Object.assign(error, { status, detail });
}

function stagedResponse(rows: number, held = 0): unknown {
  return {
    stage_id: "stage-7",
    surface_id: SURFACE,
    run_id: "run-1",
    draft_id: "draft-1",
    target: { connector: "github", op: "update_issue" },
    latest_rev: 1,
    approved_rev: null,
    status: "staged",
    revisions: [],
    decisions: [],
    rows: [],
    row_counts: {
      total: rows,
      will_apply: rows - held,
      held,
      applied: 0,
      failed: 0,
    },
  };
}

/** The surface as the cockpit hands it to the adapter: hydrated state + grant. */
function editableState(
  request: (item: SentRequest) => Promise<unknown>,
  runId: string | null = "run-1",
): SurfaceState {
  return attachConnectorEditor(
    GITHUB_TABLE_STATE,
    createConnectorSurfaceEditor({
      transport: transportWith(request),
      runId,
      surfaceId: SURFACE,
    }),
  ) as SurfaceState;
}

/** A recording stand-in for the host Transport's one request method. */
function recorder(
  respond: () => unknown,
): ReturnType<typeof vi.fn<(item: SentRequest) => Promise<unknown>>> {
  return vi.fn(async (_item: SentRequest) => respond());
}

async function editCell(
  user: ReturnType<typeof userEvent.setup>,
  testId: string,
  value: string,
): Promise<void> {
  await user.click(screen.getByTestId(testId));
  const input = screen.getByTestId(`${testId}-input`);
  await user.clear(input);
  await user.type(input, value);
  await user.keyboard("{Enter}");
}

describe("a connector-origin table is read-only until the host opens it", () => {
  it("renders no editing affordance without a grant", () => {
    render(tableAdapter.renderCurrent(GITHUB_TABLE_STATE));
    expect(screen.getByTestId("table-renderer")).toHaveAttribute(
      "data-editable",
      "false",
    );
    expect(screen.queryByTestId("table-editor-save")).not.toBeInTheDocument();
    expect(screen.getByTestId("table-grid")).toBeInTheDocument();
  });

  // The grant is built from the run that owns the surface. No run, no grant —
  // and a Save button with nowhere to POST is worse than no Save button.
  it("renders no editing affordance when there is no run to write against", () => {
    render(tableAdapter.renderCurrent(editableState(async () => ({}), null)));
    expect(screen.queryByTestId("table-editor-save")).not.toBeInTheDocument();
  });

  it("renders editable cells once the host attaches the grant", () => {
    render(tableAdapter.renderCurrent(editableState(async () => ({}))));
    expect(screen.getByTestId("table-renderer")).toHaveAttribute(
      "data-editable",
      "true",
    );
    expect(screen.getByTestId("table-editable-grid")).toBeInTheDocument();
    expect(screen.getByTestId("table-cell-0-1")).toHaveAttribute(
      "data-editable",
      "true",
    );
  });

  // Becoming editable must not cost a surface an affordance it already had. The
  // way OUT to the vendor's own UI is the one a user reaches for exactly when an
  // edit here will not do.
  it("keeps the read view's outbound link", () => {
    render(tableAdapter.renderCurrent(editableState(async () => ({}))));
    expect(
      screen.getByRole("link", { name: /Open on GitHub/i }),
    ).toHaveAttribute("href", "https://github.com/acme/web/issues");
  });

  // A structured cell has no honest text form to round-trip, so it is rendered
  // exactly as the read view renders it, with no affordance at all.
  it("offers no editor for a cell whose value is not text", () => {
    const state = editableState(async () => ({}));
    const spec = {
      ...(state.spec as object),
      columns: [{ label: "Assignee", path: "assignee" }],
    };
    render(
      tableAdapter.renderCurrent({ ...state, spec } as unknown as SurfaceState),
    );
    expect(screen.getByTestId("table-cell-0-0")).toHaveAttribute(
      "data-editable",
      "false",
    );
  });
});

describe("edit two cells, Save, and the batch reaches the write-back route", () => {
  it("posts the user's EXACT values, with each row as it was read", async () => {
    const user = userEvent.setup();
    const request = recorder(() => stagedResponse(2));
    render(tableAdapter.renderCurrent(editableState(request)));

    // Two cells, in two different rows — the batching case the design names.
    await editCell(user, "table-cell-0-2", "in_progress");
    await editCell(user, "table-cell-1-3", "rlee");

    expect(screen.getByTestId("table-editor-actions")).toHaveTextContent(
      "2 unsaved edits",
    );
    // Nothing left on a keystroke.
    expect(request).not.toHaveBeenCalled();

    await user.click(screen.getByTestId("table-editor-save"));

    expect(request).toHaveBeenCalledTimes(1);
    const sent = request.mock.calls[0][0];
    expect(sent.method).toBe("POST");
    expect(sent.path).toBe(
      "/v1/agent/surfaces/table%3A%2F%2Fgithub%2Flist_issues%2F128/write-back",
    );
    expect(sent.body.run_id).toBe("run-1");
    expect(sent.body.edits).toHaveLength(2);

    const [first, second] = sent.body.edits;
    // Row identity comes from the row itself, not its screen position.
    expect(first.row_key).toBe("128");
    expect(first.title).toBe("128");
    expect(first.changes).toEqual([
      { field: "state", old: "open", new: "in_progress" },
    ]);
    // The row AS READ rides along whole: it is the provenance half that makes a
    // binding to an untouched field (the issue number) provable server-side.
    expect(first.row).toEqual({
      number: 128,
      title: "Composer drops focus on send",
      state: "open",
      assignee: { login: "jdoe" },
      updated_at: "2026-07-19T09:30:00Z",
    });

    expect(second.row_key).toBe("131");
    expect(second.changes).toEqual([
      { field: "assignee.login", old: "mkim", new: "rlee" },
    ]);
  });

  // The one property the whole design rests on: the model picks the TOOL and
  // maps field NAMES; it never retypes a value. If this client re-formatted a
  // value on its way out, no gate downstream could notice — the diff the user
  // approves is built from the same string.
  it("sends the typed text verbatim, never the display formatting", async () => {
    const user = userEvent.setup();
    const request = recorder(() => stagedResponse(1));
    render(tableAdapter.renderCurrent(editableState(request)));

    // A `datetime` column: the cell PRINTS a localised date, and the editor must
    // still carry what the user typed rather than a re-formatted copy of it.
    await editCell(user, "table-cell-0-4", "2026-08-01T00:00:00Z");
    await user.click(screen.getByTestId("table-editor-save"));

    const sent = request.mock.calls[0][0];
    expect(sent.body.edits[0].changes[0].new).toBe("2026-08-01T00:00:00Z");
  });

  // Opening the input must show the RAW value, not the printed one: `formatValue`
  // truncates long text and localises dates, so seeding the field from the screen
  // and sending it back would silently rewrite the datum.
  it("opens a cell on its raw value, not its rendered form", async () => {
    const user = userEvent.setup();
    render(tableAdapter.renderCurrent(editableState(async () => ({}))));
    await user.click(screen.getByTestId("table-cell-0-4"));
    expect(screen.getByTestId("table-cell-0-4-input")).toHaveValue(
      "2026-07-19T09:30:00Z",
    );
  });
});

describe("the result is a PROPOSAL, and the surface says so", () => {
  it("shows the edits as staged, not applied, and takes no decision itself", async () => {
    const user = userEvent.setup();
    render(
      tableAdapter.renderCurrent(
        editableState(async () => stagedResponse(2, 1)),
      ),
    );
    await editCell(user, "table-cell-0-2", "in_progress");
    await editCell(user, "table-cell-1-2", "open");
    await user.click(screen.getByTestId("table-editor-save"));

    const notice = await screen.findByTestId("table-editor-staged");
    expect(notice).toHaveAttribute("data-stage-id", "stage-7");
    expect(notice).toHaveTextContent("staged for approval");
    expect(notice).toHaveTextContent("Nothing has been sent");

    // The cells carry the PROPOSED value, marked as staged — never silently
    // reverted (which reads as a failed save) and never unmarked (which reads
    // as a write that happened).
    expect(screen.getByTestId("table-cell-0-2")).toHaveAttribute(
      "data-staged",
      "true",
    );
    expect(screen.getByTestId("table-cell-0-2")).toHaveTextContent(
      "in_progress",
    );
    expect(screen.getByTestId("table-editor-actions")).not.toHaveTextContent(
      "unsaved",
    );

    // The decision belongs to the write gate. There is no second approval UI
    // here, and this assertion is what keeps one from growing.
    expect(
      screen.queryByRole("button", { name: /approve|apply|send|confirm/i }),
    ).toBeNull();
  });

  it("freezes a staged cell — one cell cannot hold two answers at once", async () => {
    const user = userEvent.setup();
    render(
      tableAdapter.renderCurrent(editableState(async () => stagedResponse(1))),
    );
    await editCell(user, "table-cell-0-2", "in_progress");
    await user.click(screen.getByTestId("table-editor-save"));
    await screen.findByTestId("table-editor-staged");

    await user.click(screen.getByTestId("table-cell-0-2"));
    expect(
      screen.queryByTestId("table-cell-0-2-input"),
    ).not.toBeInTheDocument();
  });

  // A staged mark is a claim about an OUTSTANDING proposal. Once the connector
  // re-read brings a different value for that cell, the proposal is settled and
  // the fresh read is the truth — otherwise the surface would keep displaying an
  // approved-looking value that nothing on the server agrees with.
  it("drops the staged mark once the connector's own value moves", async () => {
    const user = userEvent.setup();
    const state = editableState(async () => stagedResponse(1));
    const { rerender } = render(tableAdapter.renderCurrent(state));
    await editCell(user, "table-cell-0-2", "in_progress");
    await user.click(screen.getByTestId("table-editor-save"));
    await screen.findByTestId("table-editor-staged");

    const applied = {
      ...state,
      data: {
        ...(state.data as { issues: readonly Record<string, unknown>[] }),
        issues: [
          {
            ...(state.data as { issues: Record<string, unknown>[] }).issues[0],
            state: "in_progress",
          },
          (state.data as { issues: Record<string, unknown>[] }).issues[1],
        ],
      },
    };
    rerender(tableAdapter.renderCurrent(applied));

    expect(screen.queryByTestId("table-editor-staged")).not.toBeInTheDocument();
    expect(screen.getByTestId("table-cell-0-2")).toHaveAttribute(
      "data-staged",
      "false",
    );
    expect(screen.getByTestId("table-cell-0-2")).toHaveTextContent(
      "in_progress",
    );
  });

  // `specFromState` narrows an UNTRUSTED boundary value on two fields only, so a
  // spec that reached a renderer is not a spec that satisfied its own schema.
  it("names the connector vaguely rather than throwing when the spec has no source", async () => {
    const user = userEvent.setup();
    const state = editableState(async () => stagedResponse(1));
    const spec = { ...(state.spec as object) } as Record<string, unknown>;
    delete spec.source;
    render(
      tableAdapter.renderCurrent({ ...state, spec } as unknown as SurfaceState),
    );
    await editCell(user, "table-cell-0-2", "in_progress");
    await user.click(screen.getByTestId("table-editor-save"));
    expect(await screen.findByTestId("table-editor-staged")).toHaveTextContent(
      "Nothing has been sent to the connector yet",
    );
  });
});

describe("a failed Save is loud, and keeps every keystroke", () => {
  it("reports the refusal in place and retains the batch", async () => {
    const user = userEvent.setup();
    const request = recorder(() => {
      throw httpError(
        422,
        "No write operation on this connector can carry these edits.",
      );
    });
    render(tableAdapter.renderCurrent(editableState(request)));
    await editCell(user, "table-cell-0-2", "in_progress");
    await editCell(user, "table-cell-1-2", "open");
    await user.click(screen.getByTestId("table-editor-save"));

    const alert = await screen.findByTestId("table-editor-error");
    expect(alert).toHaveAttribute("role", "alert");
    expect(alert).toHaveTextContent(
      "No write operation on this connector can carry these edits.",
    );
    expect(alert).toHaveTextContent("Your edits are still here.");

    // Nothing staged, nothing lost, and Save is live again.
    expect(screen.queryByTestId("table-editor-staged")).not.toBeInTheDocument();
    expect(screen.getByTestId("table-editor-actions")).toHaveTextContent(
      "2 unsaved edits",
    );
    expect(screen.getByTestId("table-cell-0-2")).toHaveTextContent(
      "in_progress",
    );
    expect(screen.getByTestId("table-editor-save")).toBeEnabled();

    await user.click(screen.getByTestId("table-editor-save"));
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("discards the batch only when the user asks", async () => {
    const user = userEvent.setup();
    render(tableAdapter.renderCurrent(editableState(async () => ({}))));
    await editCell(user, "table-cell-0-2", "in_progress");
    await user.click(screen.getByTestId("table-editor-discard"));
    expect(screen.getByTestId("table-cell-0-2")).toHaveTextContent("open");
    expect(screen.getByTestId("table-editor-actions")).not.toHaveTextContent(
      "unsaved",
    );
  });
});

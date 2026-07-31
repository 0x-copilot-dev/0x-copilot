// <ManageMcpModal /> — the config editor.
//
// The behaviours worth pinning are the ones that decide whether a user can
// trust the editor with a credential: a secret is never rendered, a
// placeholder with no stored value blocks the save rather than writing an
// empty one, and broken JSON says WHERE it broke instead of "invalid".

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ManageMcpModal } from "./ManageMcpModal";

const REDACTED = "\u2022".repeat(8);

/** What the server returns for a server whose credential it holds. */
const SAVED_DOCUMENT = {
  servers: {
    github: {
      type: "http",
      url: "https://api.githubcopilot.com/mcp/",
      headers: { Authorization: REDACTED },
    },
  },
};

/** What a config pasted from a README looks like: a value that is not here. */
const PASTED_DOCUMENT = {
  servers: {
    github: {
      type: "http",
      url: "https://api.githubcopilot.com/mcp/",
      headers: { Authorization: "${input:github_mcp_pat}" },
    },
  },
};

function editor(): HTMLTextAreaElement {
  return screen.getByTestId("manage-mcp-editor") as HTMLTextAreaElement;
}

function renderModal(
  overrides: Partial<Parameters<typeof ManageMcpModal>[0]> = {},
) {
  const onSave = vi.fn();
  render(
    <ManageMcpModal
      open
      onClose={vi.fn()}
      document={SAVED_DOCUMENT}
      onSave={onSave}
      {...overrides}
    />,
  );
  return { onSave };
}

describe("ManageMcpModal", () => {
  it("shows the config pretty-printed", () => {
    renderModal();

    // Formatted, not a single line: the indentation is what makes the
    // structure legible, and it is also what a user expects to edit.
    expect(editor().value).toContain('"github"');
    expect(editor().value).toContain("\n  ");
  });

  it("shows a stored credential as the redaction marker, never the token", () => {
    renderModal();

    expect(editor().value).toContain(REDACTED);
    expect(editor().value).not.toContain("ghp_");
  });

  it("needs no field at all for a credential the server already holds", () => {
    // The saved state is the common one, and it used to render a password
    // field that did nothing unless you wanted to rotate. The marker in the
    // document says everything that field said.
    renderModal();

    expect(screen.queryByTestId("manage-mcp-secret-github_mcp_pat")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Save" }).hasAttribute("disabled"),
    ).toBe(false);
  });

  it("saves an untouched document verbatim, marker included", () => {
    // Re-saving the marker is how the server is told to keep what it has.
    const { onSave } = renderModal();

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][0].document).toEqual(SAVED_DOCUMENT);
  });

  it("asks for a value a PASTED config references but does not carry", () => {
    renderModal({ document: PASTED_DOCUMENT });

    const field = screen.getByTestId("manage-mcp-secret-github_mcp_pat");
    // A password input — a pasted token must not sit in plain sight in a
    // screen-share or screenshot.
    expect(field.getAttribute("type")).toBe("password");
  });

  it("blocks the save until a referenced value is supplied", () => {
    const { onSave } = renderModal({ document: PASTED_DOCUMENT });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText(/Needs a value for github_mcp_pat/)).toBeTruthy();
  });

  it("substitutes a prompted value INTO the document", () => {
    // The whole point of dropping the side-channel: one representation goes to
    // the server, so an envelope and a document cannot disagree.
    const { onSave } = renderModal({ document: PASTED_DOCUMENT });

    fireEvent.change(screen.getByTestId("manage-mcp-secret-github_mcp_pat"), {
      target: { value: "Bearer ghp_real" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).toHaveBeenCalledTimes(1);
    const sent = onSave.mock.calls[0][0].document as typeof PASTED_DOCUMENT;
    expect(sent.servers.github.headers.Authorization).toBe("Bearer ghp_real");
    expect(JSON.stringify(sent)).not.toContain("${input:");
  });

  it("leaves a literal value alone when typed straight into the JSON", () => {
    // Typing the credential directly is the primary path now; the server seals
    // it on arrival and the next read shows the marker.
    const { onSave } = renderModal();

    fireEvent.change(editor(), {
      target: {
        value: JSON.stringify({
          servers: {
            github: {
              type: "http",
              url: "https://api.githubcopilot.com/mcp/",
              headers: { Authorization: "Bearer ghp_typed" },
            },
          },
        }),
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const sent = onSave.mock.calls[0][0].document as typeof PASTED_DOCUMENT;
    expect(sent.servers.github.headers.Authorization).toBe("Bearer ghp_typed");
  });

  it("reports the LINE a JSON error is on", () => {
    renderModal();

    fireEvent.change(editor(), {
      target: { value: '{\n  "servers": {\n    "a": ,\n  }\n}' },
    });

    expect(screen.getByRole("alert").textContent).toMatch(/^Line 3:/);
    expect(editor().getAttribute("aria-invalid")).toBe("true");
  });

  it("refuses to save invalid JSON", () => {
    const { onSave } = renderModal();

    fireEvent.change(editor(), { target: { value: "{ broken" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onSave).not.toHaveBeenCalled();
  });

  it("reformats on demand", () => {
    renderModal();

    fireEvent.change(editor(), {
      target: {
        value: '{"servers":{"a":{"type":"http","url":"https://e.com/"}}}',
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Format" }));

    expect(editor().value).toContain("\n  ");
    expect(editor().value).toContain('"servers"');
  });

  it("picks up an input added by editing the document", () => {
    // The prompt follows the text, not a pre-declared list, or pasting a new
    // server would save with nothing behind its placeholder.
    renderModal();

    fireEvent.change(editor(), {
      target: {
        value: JSON.stringify({
          servers: {
            other: {
              type: "http",
              url: "https://other.example.com/mcp",
              headers: { Authorization: "${input:other_token}" },
            },
          },
        }),
      },
    });

    expect(screen.getByTestId("manage-mcp-secret-other_token")).toBeTruthy();
  });

  it("surfaces a save failure from the host", () => {
    renderModal({ error: 'server "broken" is type stdio but has no command' });

    expect(screen.getByText(/is type stdio but has no command/)).toBeTruthy();
  });

  it("reports what the save changed", () => {
    renderModal({ result: "Added files · Removed github." });

    expect(screen.getByText("Added files · Removed github.")).toBeTruthy();
  });

  it("reports the line for a truncated-snippet parse error too", () => {
    // V8 omits the character position for short inputs and returns only a
    // context snippet; the line has to be recovered from that snippet or the
    // most common editing state reports no location at all.
    renderModal();

    fireEvent.change(editor(), {
      target: { value: '{\n  "a": 1,\n  "b": ,\n  "c": 3\n}' },
    });

    expect(screen.getByRole("alert").textContent).toMatch(/^Line 3:/);
  });

  it("disables the actions while saving", () => {
    renderModal({ pending: true });

    expect(
      screen.getByRole("button", { name: "Saving…" }).hasAttribute("disabled"),
    ).toBe(true);
  });

  it("indents with Tab instead of leaving the editor", () => {
    // Without this, the first Tab in a JSON editor moves focus to Save, which
    // reads as the editor being broken.
    renderModal();
    const field = editor();
    field.setSelectionRange(0, 0);

    fireEvent.keyDown(field, { key: "Tab" });

    expect(editor().value.startsWith("  ")).toBe(true);
  });
});

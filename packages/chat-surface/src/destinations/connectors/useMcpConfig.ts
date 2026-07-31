// The "Manage MCP" data hook — load the config document, save it back.
//
// Substrate-agnostic like the rest of chat-surface: it does no I/O itself and
// takes an injected port, so the web host talks to the facade over its
// transport and the desktop host over its own, without this file knowing
// either exists.
//
// The document is deliberately typed as `unknown` here. Its shape is the
// server's contract (`McpConfigDocument`), the editor treats it as text, and
// re-declaring the schema in a third place would only create somewhere for it
// to drift — a mismatch that would surface as a save the user cannot explain.

import { useCallback, useEffect, useState } from "react";

export interface McpConfigPort {
  /** GET the current document. Credentials arrive redacted. */
  readConfig(): Promise<McpConfigDocumentPayload>;
  /** PUT the document. Everything, including new credentials, is in it. */
  writeConfig(request: {
    readonly document: unknown;
  }): Promise<McpConfigWritePayload>;
}

export interface McpConfigDocumentPayload {
  readonly servers?: Readonly<Record<string, unknown>>;
}

export interface McpConfigWritePayload {
  readonly created?: ReadonlyArray<string>;
  readonly updated?: ReadonlyArray<string>;
  readonly deleted?: ReadonlyArray<string>;
  readonly unchanged?: ReadonlyArray<string>;
}

export interface UseMcpConfigOptions {
  readonly port: McpConfigPort;
  /** Called after a save that changed something, so the host can refetch. */
  readonly onSaved?: () => void;
}

export interface UseMcpConfigResult {
  readonly open: boolean;
  readonly openConfig: () => void;
  readonly closeConfig: () => void;
  readonly document: unknown | null;
  readonly pending: boolean;
  readonly error: string | null;
  readonly result: string | null;
  readonly save: (request: { readonly document: unknown }) => void;
}

function messageFrom(error: unknown): string {
  if (error instanceof Error && error.message.trim() !== "")
    return error.message;
  return "Could not save the MCP configuration.";
}

/**
 * Summarise a save for the user.
 *
 * A save can create, rewrite, and DELETE servers in one action, so "Saved" on
 * its own would be the least informative thing this could say — deletion is
 * the outcome most worth stating out loud, because it is the one the user may
 * not have intended when they removed a block.
 */
function summarise(result: McpConfigWritePayload): string {
  const parts: string[] = [];
  const push = (label: string, names?: ReadonlyArray<string>): void => {
    if (names !== undefined && names.length > 0) {
      parts.push(`${label} ${names.join(", ")}`);
    }
  };
  push("Added", result.created);
  push("Updated", result.updated);
  push("Removed", result.deleted);
  return parts.length === 0 ? "No changes." : `${parts.join(" · ")}.`;
}

export function useMcpConfig(options: UseMcpConfigOptions): UseMcpConfigResult {
  const { port, onSaved } = options;
  const [open, setOpen] = useState(false);
  const [document, setDocument] = useState<unknown | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  // Load on open, and only on open: the document is what the user is editing,
  // so refetching it underneath them would discard their work.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setError(null);
    setResult(null);
    port
      .readConfig()
      .then((payload) => {
        if (cancelled) return;
        setDocument({ servers: payload.servers ?? {} });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setDocument({ servers: {} });
        setError(messageFrom(err));
      });
    return () => {
      cancelled = true;
    };
  }, [open, port]);

  const save = useCallback(
    (request: { readonly document: unknown }): void => {
      setPending(true);
      setError(null);
      setResult(null);
      port
        .writeConfig(request)
        .then((payload) => {
          setPending(false);
          setResult(summarise(payload));
          // Re-seed from what was sent so the editor stops showing it as
          // unsaved. Any credential in it was just sealed server-side and will
          // come back redacted on the next open — which is why this does NOT
          // re-read: doing so would swap the user's own typing for `••••••••`
          // the instant they saved.
          setDocument(request.document);
          onSaved?.();
        })
        .catch((err: unknown) => {
          setPending(false);
          setError(messageFrom(err));
        });
    },
    [onSaved, port],
  );

  return {
    open,
    openConfig: useCallback(() => setOpen(true), []),
    closeConfig: useCallback(() => setOpen(false), []),
    document,
    pending,
    error,
    result,
    save,
  };
}

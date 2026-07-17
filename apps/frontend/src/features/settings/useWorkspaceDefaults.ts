// PR 3.5 (closes PR 1.6 G1) — data hook for workspace defaults.
//
// PR 1.6 shipped the wire (`GET/PUT /v1/agent/workspace/defaults`) and the
// types (`WorkspaceDefaultsResponse`, `UpdateWorkspaceDefaultsRequest`); the
// Settings → Workspace UI panel that consumes them lands in PR 4.2. This
// hook ships now so PR 4.2 can mount its panel without re-touching backend
// types or routes.
//
// Shape:
//   const { defaults, loading, error, save } = useWorkspaceDefaults(identity);
//
// `save()` does an optimistic update + rollback on 4xx. The hook follows the
// same single-fetch pattern `useArchivedSources` (PR 3.1) uses — no react-
// query / swr — because the data is read once per Settings open, not
// long-lived state. Adding a cache layer would be premature.

import { useCallback, useEffect, useState } from "react";
import type {
  UpdateWorkspaceDefaultsRequest,
  WorkspaceDefaultsResponse,
} from "@0x-copilot/api-types";
import { getWorkspaceDefaults, putWorkspaceDefaults } from "../../api/agentApi";
import type { RequestIdentity } from "../../api/config";
import { errorMessage } from "../../utils/errors";

export interface UseWorkspaceDefaultsResult {
  defaults: WorkspaceDefaultsResponse | null;
  loading: boolean;
  error: string | null;
  save: (next: UpdateWorkspaceDefaultsRequest) => Promise<void>;
}

export function useWorkspaceDefaults(
  identity: RequestIdentity,
): UseWorkspaceDefaultsResult {
  const [defaults, setDefaults] = useState<WorkspaceDefaultsResponse | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getWorkspaceDefaults(identity)
      .then((response) => {
        if (cancelled) {
          return;
        }
        setDefaults(response);
      })
      .catch((err: unknown) => {
        if (cancelled) {
          return;
        }
        setError(errorMessage(err, "Could not load workspace defaults"));
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [identity]);

  const save = useCallback(
    async (next: UpdateWorkspaceDefaultsRequest): Promise<void> => {
      const previous = defaults;
      // Optimistic update — render the panel as if the save succeeded so the
      // admin sees the new state immediately. Rollback on 4xx/5xx restores
      // the prior view; the error is propagated through `error` AND thrown
      // so callers can also chain (`save(...).catch(...)`).
      //
      // PR 4.3 — ``behavior_overrides`` is optional on the request but
      // always populated on the response. Carry the request's value when
      // present (the panel saved a new shape) and fall back to the prior
      // view otherwise so partial saves don't lose the field.
      setDefaults({
        default_model: next.default_model,
        default_connectors: next.default_connectors,
        retention_days: next.retention_days,
        behavior_overrides: next.behavior_overrides ??
          previous?.behavior_overrides ?? {
            training_data_opt_out: false,
          },
        updated_at: new Date().toISOString(),
        updated_by_user_id: previous?.updated_by_user_id ?? null,
      });
      setError(null);
      try {
        const updated = await putWorkspaceDefaults(next, identity);
        setDefaults(updated);
      } catch (err) {
        setDefaults(previous);
        setError(errorMessage(err, "Could not save workspace defaults"));
        throw err;
      }
    },
    [defaults, identity],
  );

  return { defaults, loading, error, save };
}

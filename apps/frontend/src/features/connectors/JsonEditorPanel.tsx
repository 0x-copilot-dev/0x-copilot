// JSON-config editor for MCP connectors. Mirrors what Cursor / Claude
// Desktop expose: a single textarea you edit as JSON. On Apply we diff
// against the current state, show a confirm dialog with the planned
// creates / updates / deletes, then drive the existing per-server
// endpoints sequentially. Not atomic — partial failures leave the
// applied subset in place; the panel re-pulls so the user sees what
// actually landed.

import { Button, Card, Field } from "@enterprise-search/design-system";
import { type ReactElement, useEffect, useMemo, useState } from "react";
import { ConfirmDialog } from "./ConfirmDialog";
import {
  type DiffPlan,
  JsonConfigError,
  diff,
  isNoOp,
  parseConfig,
  serializeServers,
} from "./jsonConfig";
import type { ConnectorState } from "./useConnectors";
import { errorMessage } from "../../utils/errors";

export function JsonEditorPanel({
  connectors,
}: {
  connectors: ConnectorState;
}): ReactElement {
  const initial = useMemo(
    () => serializeServers(connectors.servers),
    [connectors.servers],
  );
  const [text, setText] = useState(initial);
  const [parseError, setParseError] = useState<string | null>(null);
  const [pendingPlan, setPendingPlan] = useState<DiffPlan | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Re-seed the editor whenever the source-of-truth list changes (e.g.
  // user edits via the visual view and toggles back). Don't clobber
  // unsaved local edits — only reset when the editor is in sync with
  // the previous list.
  const [lastSyncedSnapshot, setLastSyncedSnapshot] = useState(initial);
  useEffect(() => {
    if (text === lastSyncedSnapshot) {
      setText(initial);
    }
    setLastSyncedSnapshot(initial);
  }, [initial]);

  function handlePreview(): void {
    try {
      const config = parseConfig(text);
      const plan = diff(connectors.servers, config);
      setParseError(null);
      if (isNoOp(plan)) {
        setApplyError("No changes to apply.");
        return;
      }
      setApplyError(null);
      setPendingPlan(plan);
    } catch (err) {
      setPendingPlan(null);
      setParseError(
        err instanceof JsonConfigError
          ? err.message
          : errorMessage(err, "Could not parse JSON."),
      );
    }
  }

  async function applyPlan(plan: DiffPlan): Promise<void> {
    setSubmitting(true);
    setApplyError(null);
    try {
      // Order: deletes first (frees up display-name conflicts), then
      // creates, then updates. Each call is independent — on failure
      // we surface the message and the panel re-pulls so the user sees
      // exactly what landed.
      for (const target of plan.deletes) {
        await connectors.removeServer(target.id);
      }
      for (const create of plan.creates) {
        await connectors.addServer(create.url);
        // ``addServer`` doesn't accept display_name today; if needed
        // a follow-up patch lands the requested name. Skip if equal
        // to the URL-derived default.
      }
      for (const update of plan.updates) {
        if (update.patch.enabled !== undefined) {
          await connectors.setEnabled(update.id, update.patch.enabled);
        }
        if (update.patch.display_name !== undefined) {
          await connectors.setDisplayName(update.id, update.patch.display_name);
        }
      }
      await connectors.refresh();
    } catch (err) {
      setApplyError(errorMessage(err, "Could not apply changes."));
      throw err;
    } finally {
      setSubmitting(false);
    }
  }

  function handleReset(): void {
    setText(initial);
    setParseError(null);
    setApplyError(null);
  }

  return (
    <Card className="json-editor">
      <Field
        label="Connectors as JSON"
        hint="Edit the list as text. Apply Changes diffs against the current state and confirms before writing."
      >
        <textarea
          className="json-editor__textarea"
          value={text}
          onChange={(event) => setText(event.target.value)}
          spellCheck={false}
          autoCorrect="off"
          autoCapitalize="off"
          aria-label="Connectors JSON config"
        />
      </Field>
      {parseError ? <p className="app-error">{parseError}</p> : null}
      {applyError ? <p className="app-error">{applyError}</p> : null}
      <div className="json-editor__actions">
        <Button
          type="button"
          variant="primary"
          onClick={handlePreview}
          disabled={submitting || text === initial}
        >
          Preview changes
        </Button>
        <Button
          type="button"
          variant="secondary"
          onClick={handleReset}
          disabled={submitting || text === initial}
        >
          Reset
        </Button>
      </div>

      <ConfirmDialog
        open={pendingPlan !== null}
        onClose={() => setPendingPlan(null)}
        onConfirm={() =>
          pendingPlan ? applyPlan(pendingPlan) : Promise.resolve()
        }
        title="Apply JSON changes?"
        description={pendingPlan ? <DiffSummary plan={pendingPlan} /> : null}
        confirmLabel={submitting ? "Applying..." : "Apply changes"}
        destructive={pendingPlan?.deletes.length ? true : false}
      />
    </Card>
  );
}

function DiffSummary({ plan }: { plan: DiffPlan }): ReactElement {
  return (
    <div className="json-editor__diff">
      {plan.creates.length > 0 ? (
        <DiffSection
          title={`Add (${plan.creates.length})`}
          items={plan.creates.map((entry) => `${entry.name} — ${entry.url}`)}
        />
      ) : null}
      {plan.updates.length > 0 ? (
        <DiffSection
          title={`Update (${plan.updates.length})`}
          items={plan.updates.map((u) => {
            const fields = Object.keys(u.patch).join(", ");
            return `${u.id} — ${fields}`;
          })}
        />
      ) : null}
      {plan.deletes.length > 0 ? (
        <DiffSection
          title={`Remove (${plan.deletes.length})`}
          items={plan.deletes.map((d) => `${d.name} (${d.id})`)}
          tone="danger"
        />
      ) : null}
    </div>
  );
}

function DiffSection({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone?: "danger";
}): ReactElement {
  return (
    <section className="json-editor__diff-section">
      <h4
        className={
          tone === "danger" ? "json-editor__diff-title--danger" : undefined
        }
      >
        {title}
      </h4>
      <ul>
        {items.map((item, index) => (
          <li key={`${title}-${index}`}>{item}</li>
        ))}
      </ul>
    </section>
  );
}

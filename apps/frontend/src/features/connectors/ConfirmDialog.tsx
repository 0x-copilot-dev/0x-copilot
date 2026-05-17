// Generic confirmation dialog for destructive / irreversible actions.
//
// Wraps the feature-local <Modal> so callers configure a single object
// instead of plumbing modal state, footer buttons, and pending state by
// hand. Used for "Remove connector" and "Skip auth" — both have real
// consequences (revoking OAuth tokens, allowing the agent to use a
// connector without auth) and need an explicit confirm.

import { Button } from "@enterprise-search/design-system";
import { type ReactElement, type ReactNode, useState } from "react";
import { Modal } from "../settings/Modal";
import { errorMessage } from "../../utils/errors";

export interface ConfirmDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => Promise<void> | void;
  title: string;
  description?: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  /** Render the confirm button as `danger` instead of `primary`. */
  destructive?: boolean;
}

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  description,
  confirmLabel,
  cancelLabel = "Cancel",
  destructive = false,
}: ConfirmDialogProps): ReactElement {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleConfirm(): Promise<void> {
    try {
      setSubmitting(true);
      setError(null);
      await onConfirm();
      onClose();
    } catch (err) {
      setError(errorMessage(err, "Action failed."));
    } finally {
      setSubmitting(false);
    }
  }

  function handleClose(): void {
    if (submitting) {
      return;
    }
    setError(null);
    onClose();
  }

  return (
    <Modal
      open={open}
      onClose={handleClose}
      title={title}
      footer={
        <>
          <Button
            type="button"
            variant="secondary"
            onClick={handleClose}
            disabled={submitting}
          >
            {cancelLabel}
          </Button>
          <Button
            type="button"
            variant={destructive ? "danger" : "primary"}
            onClick={() => void handleConfirm()}
            disabled={submitting}
          >
            {submitting ? "Working..." : confirmLabel}
          </Button>
        </>
      }
    >
      {description ? (
        <div className="confirm-dialog__body">{description}</div>
      ) : null}
      {error ? <p className="app-error">{error}</p> : null}
    </Modal>
  );
}

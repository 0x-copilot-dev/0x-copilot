// PR 4.2 — Settings → Billing panel.
//
// Read-only digest. v1 sources the plan tier and seat limit from the
// deployment env (the backend route returns ``managed_externally: true`` so
// the FE never claims to own the payment relationship). The Workspace
// usage chart embedding is PR 4.5's job — until then the panel surfaces
// the seats, plan card, and a placeholder for the chart slot.

import { Badge, Button, Card } from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import type { RequestIdentity } from "../../api/config";
import { useBilling } from "./useWorkspace";

export function BillingSettings({
  identity,
}: {
  identity: RequestIdentity;
}): ReactElement {
  const { digest, loading, error } = useBilling(identity);

  if (loading) {
    return (
      <div className="settings-section">
        <h2>Billing</h2>
        <Card>
          <p>Loading billing…</p>
        </Card>
      </div>
    );
  }
  if (error || !digest) {
    return (
      <div className="settings-section">
        <h2>Billing</h2>
        <Card>
          <p>{error ?? "Billing unavailable."}</p>
        </Card>
      </div>
    );
  }

  const seatPercent = digest.seats.limit
    ? Math.min(100, Math.round((digest.seats.used / digest.seats.limit) * 100))
    : 0;

  return (
    <div className="settings-section" data-section="billing">
      <header className="settings-section__header">
        <div>
          <h2>Billing</h2>
          <p className="settings-section__hint">
            Plan, seats, and usage at a glance.{" "}
            {digest.plan.managed_externally
              ? "Payment is managed externally."
              : null}
          </p>
        </div>
      </header>

      <Card data-section="plan">
        <div className="billing-card">
          <div className="billing-card__header">
            <span className="billing-card__eyebrow">Plan</span>
            <Badge tone="accent">{digest.plan.tier}</Badge>
          </div>
          <h3>{digest.plan.display_name}</h3>
          <p>
            {digest.plan.managed_externally
              ? "Managed externally."
              : "Managed in-app."}
          </p>
          {digest.plan.billing_contact ? (
            <p className="billing-card__contact">
              Billing contact:{" "}
              <a href={`mailto:${digest.plan.billing_contact}`}>
                {digest.plan.billing_contact}
              </a>
            </p>
          ) : null}
        </div>
      </Card>

      <Card data-section="seats">
        <h3>Seats</h3>
        <div className="billing-seats">
          <div className="billing-seats__numbers">
            <span className="billing-seats__used">{digest.seats.used}</span>
            <span className="billing-seats__limit">
              of {digest.seats.limit}
            </span>
          </div>
          <div
            className="billing-seats__bar"
            role="progressbar"
            aria-valuenow={digest.seats.used}
            aria-valuemax={digest.seats.limit}
            aria-valuemin={0}
          >
            <span style={{ width: `${seatPercent}%` }} />
          </div>
          {digest.seats.removed_in_period > 0 ? (
            <p className="billing-seats__removed">
              {digest.seats.removed_in_period} removed this period.
            </p>
          ) : null}
        </div>
      </Card>

      <Card data-section="usage-chart">
        <h3>Workspace usage</h3>
        <p>
          Stacked-area chart and per-user breakdown ship with PR 4.5. Until then
          admins can read totals via <code>GET /v1/usage/org?period=30d</code>.
        </p>
      </Card>

      <Card data-section="invoices">
        <h3>Invoices</h3>
        {digest.invoices.length === 0 ? (
          <p>No invoices on file. Payment is managed externally.</p>
        ) : (
          <ul className="billing-invoices">
            {digest.invoices.map((invoice, index) => (
              <li key={invoice.invoice_id ?? `invoice-${index}`}>
                <span>{invoice.invoice_id}</span>
                <span>
                  {invoice.period_start} – {invoice.period_end}
                </span>
                <span>{invoice.status}</span>
              </li>
            ))}
          </ul>
        )}
        <Button type="button" variant="ghost" disabled>
          Open billing portal
        </Button>
      </Card>
    </div>
  );
}

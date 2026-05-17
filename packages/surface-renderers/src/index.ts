import { registerEmailAdapter } from "./email";

export {
  emailAdapter,
  registerEmailAdapter,
  type EmailDiff,
  type EmailDiffPending,
  type EmailState,
} from "./email";

// === Phase 4-D salesforce ===
import { registerSalesforceAdapter } from "./salesforce";
export {
  OpportunityRenderer,
  OpportunityDiffRenderer,
  OpportunityFieldRow,
  opportunityAdapter,
  type SalesforceOpportunity,
  type SalesforceOpportunityCustomField,
  type SalesforceOpportunityDiff,
  type SalesforceOpportunityFieldChange,
  type OpportunityFieldRowProps,
} from "./salesforce";
// === end Phase 4-D ===

export function registerAll(): void {
  registerEmailAdapter();
  // === Phase 4-D salesforce ===
  registerSalesforceAdapter();
  // === end Phase 4-D ===
}

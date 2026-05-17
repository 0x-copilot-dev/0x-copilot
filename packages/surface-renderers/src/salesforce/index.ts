import {
  registerAdapter,
  type SaaSRendererAdapter,
} from "@enterprise-search/chat-surface";

import {
  opportunityAdapter,
  OpportunityRenderer,
  OpportunityDiffRenderer,
  type SalesforceOpportunity,
  type SalesforceOpportunityCustomField,
  type SalesforceOpportunityDiff,
} from "./OpportunityRenderer";

import {
  OpportunityFieldRow,
  type OpportunityFieldRowProps,
  type SalesforceOpportunityFieldChange,
} from "./OpportunityDiff";

export {
  OpportunityRenderer,
  OpportunityDiffRenderer,
  OpportunityFieldRow,
  opportunityAdapter,
};
export type {
  OpportunityFieldRowProps,
  SalesforceOpportunity,
  SalesforceOpportunityCustomField,
  SalesforceOpportunityDiff,
  SalesforceOpportunityFieldChange,
};

export function registerSalesforceAdapter(): void {
  registerAdapter(opportunityAdapter as SaaSRendererAdapter);
}

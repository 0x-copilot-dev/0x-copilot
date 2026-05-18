// Tools onboarding wizard family — barrel.
//
// Source: tools-prd §2 (user journeys U1-U3) + §7.1 (route map
// `/tools/onboard/<kind>`). Owned by P10-B3.
//
// Authority: this folder is the SINGLE home for the chat-surface tools
// onboarding step machine. Hosts (`apps/frontend/src/features/tools/*`)
// own transport: the wizards take callbacks for "fetch the OpenAPI doc",
// "test the call", "start OAuth", "continue to Library" — they do not
// import `fetch` or read URLs themselves.
//
// DRY: the step-machine pattern is local (`useStepMachine` in
// `./useStepMachine.ts`) so we don't drag a state-management library in.

export {
  OpenApiWizard,
  type OpenApiWizardProps,
  type OpenApiDoc,
  type OpenApiOperation,
  type OpenApiAuthKind,
  type OpenApiAuthChoice,
} from "./OpenApiWizard";
export {
  McpWizard,
  type McpWizardProps,
  type McpServerListEntry,
  type McpMethod,
} from "./McpWizard";
export {
  CodeWizard,
  type CodeWizardProps,
  type CodeWizardValue,
} from "./CodeWizard";
export { SkillWizard, type SkillWizardProps } from "./SkillWizard";
export {
  useStepMachine,
  type StepMachine,
  type UseStepMachineOptions,
} from "./useStepMachine";

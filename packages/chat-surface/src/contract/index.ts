// Host binding contract — public barrel (PRD-03).
//
// Both host binders (`apps/frontend`, `apps/desktop`) construct the binding
// types here and their conformance tests iterate the manifests here.

export type {
  ProjectsDetailBinding,
  ProjectsHostBinding,
  ShellHostBinding,
} from "./shellBinding";
export type { ExhaustiveBindingManifest } from "./manifest";
export { PROJECTS_BINDING_FIELDS, SHELL_BINDING_FIELDS } from "./manifest";

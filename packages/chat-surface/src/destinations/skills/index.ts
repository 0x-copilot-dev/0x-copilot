// Skills destination — public surface (PR-4.9).
//
// The redesigned Skills slug: a card grid of saved multi-step workflows
// (`/v1/skills`), presentational only. Host binding (fetch + Run/Edit/New
// wiring) lands in PR-4.10.

export {
  SkillsDestination,
  SKILLS_SUBTITLE_COPY,
  SKILLS_EMPTY_TITLE,
  type SkillsDestinationProps,
} from "./SkillsDestination";

export { SkillCard, runCountLabel, type SkillCardProps } from "./SkillCard";

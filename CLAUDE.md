# Working rules for every session in this repo

- The plan, phases and task tracking live in Linear, project "Rose Tattoo
  remaster" (team LMY). Read the "Remaster plan" document and your phase's
  milestone before starting work.
- Track your own work as Linear issues: move to In Progress when starting,
  comment with results (numbers, commit IDs, where results docs are), Done when
  verified. Create issues for new findings in the right milestone.
- Decisions needing the user: create an issue labelled "Needs approval",
  assigned to the user, stating the provisional default you proceed on. Never
  block on it. The runner still writes `review/queue.md`; mirror new pending
  items into Linear.
- Detailed results stay in repo docs (`docs/bakeoff-results.md` etc.); Linear
  links to them.
- Never upload game files, game-derived images or extracted text to Linear;
  describe and point to local paths instead. No secrets in Linear.
- Existing rules still apply: GitButler for version control, no push/PR unless
  asked, delegate implementation to OpenCode on local models via
  `uv run remaster delegate`, don't delete old pilot trees or model caches.

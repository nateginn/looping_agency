---
name: intake-project
description: Onboard a new client project into the workspace from templates/project-intake.md and templates/loops/*, gathering domain/repo/goals/caps/credential aliases from the human. Use for "/intake-project <slug>".
---

# /intake-project

Scaffolds `projects/<slug>/` from `templates/project-intake.md` and wires whichever loop templates the human wants enabled. This is a judgment-heavy, human-interview skill — there is no deterministic script for it, because the inputs (domain, repo, goals, credential aliases) only the human can supply.

## Steps

1. Ask the human (via `AskUserQuestion` where a decision is needed, plain questions otherwise):
   - project slug (lowercase, hyphenated)
   - domain
   - repo path, if any — and critically, **does that repo auto-deploy on push with no staging gate?** If yes, note in `project.md` that any push to it is Tier 2 (human-only), the same way RISK-REGISTER.md R6 treats the operator's own website. Never assume a repo is safe to push to without asking this.
   - goals (one line, plain language)
   - caps (ads budget ceilings if applicable — else leave null)
   - which loops to enable (seo first; content-social and ads are draft-only in Phase 1 and cannot actually run yet — say so if asked for them)
   - credential aliases per connector this project needs, and confirm each is (or will be) stored as an **opaque alias** in Windows Credential Manager — never ask the human to paste a raw secret into chat or into a file in this repo.
2. Create `projects/<slug>/project.md` from `templates/project-intake.md`, filled in with the answers above.
3. For each enabled loop, copy `templates/loops/<loop>/spec.md` and `instructions.md` into `projects/<slug>/loops/<loop>/`, fill in the placeholders from the interview, and create empty `memory.md`, `pending/`, `runs/`.
4. Validate the new spec immediately:
   ```
   python tools/spec_validate.py projects/<slug>/loops/<loop>/spec.md
   ```
   Fix and re-validate until it passes — do not leave an unvalidated spec in place.
5. Set `approval_mode: propose-only` for every newly onboarded loop, regardless of what the human asks for — per AgentColabPlan.md Phase 2, Tier-1 proposals are enabled only after human review of the first two reports. Say this explicitly if the human expects immediate `tier1-enabled`.
6. Do not run the loop as part of intake — that's a separate `/run-loop` step, on the human's schedule or via Task Scheduler, not automatically after onboarding.

# Phase 2 Readiness Checklist

Use this checklist to decide whether Loop Agency is ready to begin **Phase 2:
the first real project's live SEO loop**.

This is intentionally different from
`templates/loops/seo/intake-checklist.md`:

- `templates/loops/seo/intake-checklist.md` is about what a real project must
  supply at onboarding time.
- This file is about whether the framework itself is ready to move from Phase 1
  into a real, live, recommendations-only Phase 2 kickoff.

Status terms:

- `[x]` complete
- `[ ]` not complete
- `[!]` decision or action still required from the human operator

## 1. Phase 1 framework completion

- [x] Python runtime is the canonical implementation language throughout the repo
      (`AgentColabPlan.md`, `CLAUDE.md`, `HANDOFF.md`, skills, templates).
- [x] Phase 1 tooling exists in Python under `tools/`.
- [x] Local verification suite passes for the Phase 1 framework
      (`tools/tests/phase1_exit_criteria.py`, 32/32 checks).
- [x] Phase 1 work is committed and pushed to `origin/master`.
- [x] The plan's language-switch review reached a real Codex `APPROVED`
      outcome for the Python correction (`PLAN-REVIEW-LOG.md`, Round 4).

## 2. Safety and operating controls

- [x] Spec validation exists and rejects invalid loop specs before a run starts.
- [x] Per-loop locking exists, including stale-lock recovery.
- [x] Redaction exists for snapshot/log/report outputs.
- [x] Approval-state enforcement exists (`draft -> reviewed -> approved -> applied -> verified`).
- [x] Pause-on-breach behavior exists and blocks new proposals until human resolution.
- [x] Out-of-band watchdog exists for missed-run detection.
- [x] Tooling can run through the pinned workspace interpreter
      (`.venv/Scripts/python.exe`), not a bare `python`.

## 3. Real-project prerequisites

- [x] Human has explicitly authorized Phase 2 kickoff for a real project.
- [x] A real project has been chosen for onboarding: `art` (acceleratedrehabtherapy.com).
- [x] The project-specific intake checklist has been completed:
      `templates/loops/seo/intake-checklist.md`.
- [x] The real project's `project.md` and `loops/seo/spec.md` have been created
      (`projects/art/`), validated clean.
- [x] The real project's repo/deploy behavior has been reviewed for Tier 1 vs Tier 2 implications
      (`D:\Dev\artwebsite` auto-deploys, no staging gate — documented as Tier 2/human-only in
      `project.md`, per `RISK-REGISTER.md` R6).
- [x] The human reviewer for `/review-pending` has been identified: Nate (project owner).

## 4. Credential and connector readiness

- [x] `gsc.py` and `dataforseo.py` implement real API calls.
- [x] Both connectors still fail closed without an injected credential resolver.
- [x] Real credential aliases have been chosen for the first live project: `art-gsc-readonly`.
- [x] The corresponding secrets have been created by a human outside the repo
      (stored via `--store --from-file` in Nate's own terminal; verified with `--check`;
      source JSON file deleted afterward).
- [x] A real credential resolver has been wired into the run pathway
      (`tools/lib/credentials.py`, dispatched from `run_loop.py`).
- [x] `run_loop.py` has been updated so the first live project can use real connectors
      instead of `mock_metrics.py`.
- [x] The first project's GSC property string has been confirmed:
      `sc-domain:acceleratedrehabtherapy.com`.
      DataForSEO market defaults/target set: **N/A** — `art` starts `inputs: [gsc]` only;
      `dataforseo` is deferred until the first GSC-only reports are reviewed.

## 5. Required live verification before first real loop

- [ ] A connectors-only live smoke test has been run against the first project's
      lowest-risk credentials.
- [ ] The smoke test has verified authentication works.
- [ ] The smoke test has verified expected quota/headroom behavior.
- [ ] The smoke test has verified partial-failure handling and clean logging.
- [ ] The smoke test results have been reviewed by a human and accepted.

## 6. Phase 2 launch mode

- [x] The first real project starts in `approval_mode: propose-only`
      (`projects/art/loops/seo/spec.md`).
- [x] The initial run cadence has been chosen and documented: weekly, Monday mornings
      (`schedule: "0 6 * * 1"`).
- [ ] The first Phase 2 run is limited to recommendations-only behavior — guaranteed by
      `propose-only` mode, but not yet observed, since no run has happened (Step 6).
- [x] No Tier 1 external writes are enabled before human review of the first two reports
      (enforced by `approval_mode: propose-only`; `art`'s `allowed_actions` are additionally
      `manual_approval_only: true` since there is no automated apply path for this project at all).

## 7. Go / No-Go summary

You are ready to start a real Phase 2 kickoff only when:

- every item in sections 1 and 2 is `[x]`
- every item in sections 3, 4, 5, and 6 is either `[x]` or explicitly waived by the human
- no new High-severity blocker has been added to `RISK-REGISTER.md`

## Current objective assessment as of 2026-07-16

- `Sections 1, 2, 3, 4, and 6:` ready
- `Section 5:` not ready yet — this is the pending Step 6 action (live smoke
  test against `art`, not yet run)

So the current answer is:

- **Ready for Phase 2 planning and authorization:** yes
- **Ready to run the live smoke test and first real run (`art`):** yes, as
  soon as a human is present to review the smoke test output — see
  `HANDOFF.md` "Resume here" / Step 6.

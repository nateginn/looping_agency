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

- [!] Human has explicitly authorized Phase 2 kickoff for a real project.
- [ ] A real project has been chosen for onboarding.
- [ ] The project-specific intake checklist has been completed:
      `templates/loops/seo/intake-checklist.md`.
- [ ] The real project's `project.md` and `loops/seo/spec.md` have been created.
- [ ] The real project's repo/deploy behavior has been reviewed for Tier 1 vs Tier 2 implications.
- [ ] The human reviewer for `/review-pending` has been identified.

## 4. Credential and connector readiness

- [x] `gsc.py` and `dataforseo.py` implement real API calls.
- [x] Both connectors still fail closed without an injected credential resolver.
- [ ] Real credential aliases have been chosen for the first live project.
- [ ] The corresponding secrets have been created by a human outside the repo.
- [ ] A real credential resolver has been wired into the run pathway.
- [ ] `run_loop.py` has been updated so the first live project can use real connectors
      instead of `mock_metrics.py`.
- [ ] The first project's GSC property string, DataForSEO market defaults, and target set
      have been confirmed.

## 5. Required live verification before first real loop

- [ ] A connectors-only live smoke test has been run against the first project's
      lowest-risk credentials.
- [ ] The smoke test has verified authentication works.
- [ ] The smoke test has verified expected quota/headroom behavior.
- [ ] The smoke test has verified partial-failure handling and clean logging.
- [ ] The smoke test results have been reviewed by a human and accepted.

## 6. Phase 2 launch mode

- [ ] The first real project starts in `approval_mode: propose-only`.
- [ ] The initial run cadence has been chosen and documented.
- [ ] The first Phase 2 run is limited to recommendations-only behavior.
- [ ] No Tier 1 external writes are enabled before human review of the first two reports.

## 7. Go / No-Go summary

You are ready to start a real Phase 2 kickoff only when:

- every item in sections 1 and 2 is `[x]`
- every item in sections 3, 4, 5, and 6 is either `[x]` or explicitly waived by the human
- no new High-severity blocker has been added to `RISK-REGISTER.md`

## Current objective assessment as of 2026-07-16

- `Sections 1 and 2:` ready
- `Sections 3 through 6:` not ready yet

So the current answer is:

- **Ready for Phase 2 planning and authorization:** yes
- **Ready for a real live Phase 2 kickoff today:** no, not until real-project,
  credential, and live-connector steps are completed

---
name: run-loop
description: Run one cycle of a project's loop (e.g. SEO) through the deterministic run-contract engine, then summarize the resulting report for the human. Use for "/run-loop <project> <loop>".
---

# /run-loop

Runs `tools/run_loop.py`, which implements the run contract from the root `CLAUDE.md` mechanically: lock acquisition (with stale-lock recovery), spec validation, memory/pending load, metrics pull + immutable snapshot, prior-experiment evaluation (with cooldown and pause-on-breach), new proposal generation (skipped if paused-breach), structured `run.json` + `report.md`, and a `memory.md` append. Locking, redaction, and state transitions are handled by the script — do not reimplement or bypass them.

## Steps

1. Parse `<project>` and `<loop>` from the command args. If missing, ask the user which project/loop to run (list `projects/*/loops/*/spec.md` if unsure).
2. Run:
   ```
   python tools/run_loop.py <project> <loop>
   ```
3. Read the JSON result:
   - `status: "refused"` → a lock is already held. Report the reason to the human; do not retry automatically, do not delete the lock file.
   - `status: "invalid-spec"` → report the validation errors from `runs/<run-id>/validation-failure.json`; the loop did not run. Point at `python tools/spec_validate.py <spec path>` for details.
   - `status: "partial-failure"` → read `runs/<run-id>/report.md`; summarize the connector failure for the human. No proposals were generated; nothing else to do.
   - `status: "paused-breach"` → read `runs/<run-id>/report.md`; tell the human a guardrail breach halted the loop and that new proposals are blocked until they run `/review-pending` and resolve it.
   - `status: "ok"` → read `runs/<run-id>/report.md` and summarize: what won, what's still in its observation window, what new proposals were drafted (with tier and target), and any proposals flagged stale (undecided for >=2 cycles).
4. Never call `tools/apply.py` from this skill. Applying a proposal is always a separate, explicit `/review-pending` action gated on human approval.
5. Never point this skill at the operator's website repo or any real credential alias — Phase 1 stops at that boundary (see RISK-REGISTER.md R6). If asked to run a loop against a real project before Phase 2 connectors are wired, say so and stop.

---
version: 1
loop: ads
objective: <one line — e.g. "Improve cost-per-result / ROAS within hard spend ceilings">
primary_metric: cost_per_result
guardrail_metrics:
  - name: daily_budget_ceiling
    comparator: ">"
    threshold: 0
    consecutive_runs: 1
  - name: roas_floor
    comparator: "<"
    threshold: 1.0
    consecutive_runs: 3
failure_threshold:
  metric: daily_budget_ceiling
  comparator: ">"
  value: 0
inputs:
  - ads-platform-readonly
allowed_actions:
  - type: budget-change-proposal
    tier: 2
    manual_approval_only: true
    observation_window_days: 3
    min_sample_size: 1000
approval_mode: propose-only
max_run_duration_minutes: 20
schedule: "0 7 * * *"
stop_condition: <e.g. "human sets loops_enabled.ads to false in project.md">
memory: memory.md
credential_aliases:
  ads-platform-readonly: <alias>
---

# Ads loop spec — DRAFT (Phase 1: not wired)

**Status: draft only.** Per AgentColabPlan.md Sequencing Phase 4, the ads loop goes live last, read-only first, and stays **propose-only until daily ceilings are verified live**. No ads connector exists yet; `run-loop.mjs` has nothing registered for `ads-platform-readonly` and will refuse.

## What's still open before this can go live (Phase 4)

- `budget-change-proposal` is marked `tier: 2` + `manual_approval_only: true` deliberately — spend changes are always human-only per the side-effect tiers, never a loop's `applied` transition.
- Daily budget ceiling, per-campaign hard stop, and frequency/fatigue guardrails all need real numbers from the client before this spec is usable — the placeholders above (`threshold: 0`) are not meaningful defaults, just schema-valid placeholders.
- Fallback if Meta/Google Ads API onboarding stalls: this loop reads manual CSV exports instead (see AgentColabPlan.md Risks). That would change `inputs` to a CSV-reading connector, not `ads-platform-readonly`.

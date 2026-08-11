---
slug: _demo
domain: demo.invalid
repo: null
goals:
  - Prove the Phase 1 run contract end-to-end with mock data only — never a real project.
caps:
  ads_daily_budget_ceiling: null
  ads_monthly_cap: null
credential_aliases:
  mock: demo-gsc-readonly
loops_enabled: []   # 2026-08-10: emptied deliberately. _demo is an offline fixture, run by hand
                    # when someone wants to exercise the run contract - it is not on any schedule,
                    # so the watchdog was alerting on it every single day and drowning out real
                    # signal. `tools/watchdog.py` reads this field; `run_loop.py` does not, so
                    # `run_loop.py _demo seo` still works exactly as before.
---

# Project: _demo

Synthetic project used only for the Milestone-1 offline dry-run (AgentColabPlan.md Sequencing Phase 1a). No real domain, repo, or credential — `demo-gsc-readonly` is a fake alias resolved entirely inside `tools/mock_metrics.py`, which never makes a network call.

This project must never be pointed at a real repo or real credential alias.

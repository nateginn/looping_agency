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
loops_enabled:
  - seo
---

# Project: _demo

Synthetic project used only for the Milestone-1 offline dry-run (AgentColabPlan.md Sequencing Phase 1a). No real domain, repo, or credential — `demo-gsc-readonly` is a fake alias resolved entirely inside `tools/mock-metrics.mjs`, which never makes a network call.

This project must never be pointed at a real repo or real credential alias.

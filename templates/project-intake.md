---
slug: <project-slug>            # lowercase, hyphenated; becomes projects/<slug>/
domain: <example.com>
repo: <absolute or resolvable path to the project's own repo, if any — never this workspace's repo>
goals:
  - <primary business goal, one line>
caps:
  ads_daily_budget_ceiling: null   # required before any ads loop goes live; null = not applicable yet
  ads_monthly_cap: null
credential_aliases:               # opaque aliases only — never raw secrets. Resolve via Windows Credential Manager.
  gsc: <alias, e.g. acme-gsc-readonly>
  dataforseo: <alias, e.g. acme-dataforseo-read>
loops_enabled:
  - seo
---

# Project: <name>

## Domain & repo

- Website: <domain>
- Repo: <path> (declared here; the runner may only read/write inside this repo and `projects/<slug>/` — boundary checked via resolved canonical paths, symlinks/junctions included)
- **Deploy behavior:** does this repo auto-deploy on push with no staging gate? If yes, **every push is Tier 2 (public, human-only)** regardless of branch — record that explicitly here, the same way RISK-REGISTER.md R6 records it for the operator's own website.

## Goals

<what this project/business is trying to achieve, in plain language>

## Guardrails / caps

<any hard limits: budget ceilings, brand constraints, content review requirements>

## Credential aliases

List each connector this project uses and the **opaque alias** (never the raw secret) stored in Windows Credential Manager under that name. Document least-privilege scope per connector (e.g. GSC: restricted read-only user; GitHub: repo-scoped PAT for this one repo).

## Loops enabled

Which loops (seo, content-social, ads) are live for this project, and in what mode (propose-only vs tier1-enabled). New loops start propose-only.

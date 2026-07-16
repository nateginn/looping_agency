---
slug: art
domain: acceleratedrehabtherapy.com
repo: D:\artwebsite
goals:
  - Grow organic leads/calls by improving rankings and click-through on lead-intent pages (services, location pages)
caps:
  ads_daily_budget_ceiling: null
  ads_monthly_cap: null
credential_aliases:
  gsc: art-gsc-readonly
loops_enabled:
  - seo
---

# Project: Accelerated Rehab Therapy (art)

## Domain & repo

- Website: acceleratedrehabtherapy.com
- Repo: `D:\artwebsite` (declared here; the runner may only read/write inside this repo and `projects/art/` — boundary checked via resolved canonical paths, symlinks/junctions included)
- **Deploy behavior: this repo auto-deploys on push with no staging gate.** Per `RISK-REGISTER.md` R6, every push to `D:\artwebsite` is **Tier 2 (public, human-only)**, regardless of branch. No loop tooling in this workspace is ever authorized to push to it — Tier 1 `applied` proposals for this project have no repo to land in until a staging gate exists, so in practice this loop stays propose-only/manual-apply indefinitely unless that changes.

## Goals

More organic leads and phone calls for the clinic, by improving Search Console position and click-through on pages with lead intent (service pages, location pages) rather than chasing position on low-value/informational pages.

## Guardrails / caps

- Guardrail: a tracked page's position regresses by more than 5 (1 consecutive run) after an applied change → loop auto-pauses (`state.json: paused-breach`) until a human resolves it via `/review-pending --resolve-breach`.
- No ads caps — ads loop not in scope for this project.
- Brand-voice/content constraints: none specified yet; content-affecting proposals (title/meta rewrites) are human-reviewed before approval regardless.

## Credential aliases

- `gsc`: `art-gsc-readonly` — Google service-account JSON key, stored in Windows Credential Manager (chunked). Service account `loop-agency-gsc-reader@loop-agency-502604.iam.gserviceaccount.com` added to the `acceleratedrehabtherapy.com` Search Console property with **Restricted** permission (read-only). Property is verified as a **Domain** property; `site_url` for the connector is `sc-domain:acceleratedrehabtherapy.com`.
- `dataforseo`: not configured — this loop starts `inputs: [gsc]` only.

## Loops enabled

- `seo` — `propose-only`. Runs weekly (Monday mornings). Reviewer: Nate (project owner). Will move to `tier1-enabled` only after Nate reviews the first two reports — and even then, Tier-1 `applied` proposals have nowhere to land until `D:\artwebsite` gets a staging gate (see Deploy behavior above), so this is expected to stay a recommendations-only loop for the foreseeable future.

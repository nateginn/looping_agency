# SEO loop — Phase 2 intake checklist

What a human must supply before `/intake-project` can onboard a real project's
SEO loop and it can move past `propose-only`. This is a checklist to work
through with the client/operator, not a form the agent fills in alone —
several items require the operator's own judgment (deploy behavior, budget
ceilings, brand constraints).

Nothing on this list is wired up yet — filling it in does not start a live
loop. It only prepares `projects/<slug>/project.md` and
`projects/<slug>/loops/seo/spec.md` so that when a human explicitly
authorizes Phase 2 kickoff, onboarding is a known, bounded amount of work.

## 1. Domain & repo

- [ ] Verified domain (must match a Search Console property the operator controls)
- [ ] Repo path (absolute or resolvable; must be a real, separate repo from this workspace)
- [ ] **Deploy behavior**: does this repo auto-deploy on push with no staging gate?
      - If yes: every push is **Tier 2, human-only**, regardless of branch — this must be
        recorded explicitly in `project.md`, the same way `RISK-REGISTER.md` R6 records it
        for the operator's own site. Confirm no loop tooling will ever be allowed to push
        directly to this repo's default branch.
      - If no (staging gate exists): document the staging → production promotion path,
        since Tier 1 previews still need somewhere safe to land.

## 2. Goals & primary metric

- [ ] One-line business goal (what "winning" looks like, in the client's words)
- [ ] `primary_metric` for the spec (defaults to `gsc_position`; confirm this is what the
      client actually cares about, vs. e.g. clicks or a specific keyword set)
- [ ] The initial target keyword/page set (a starting list — the loop will propose
      changes to these first; it does not discover new keywords on its own in Phase 1/2)

## 3. Guardrails & caps

- [ ] Guardrail threshold for a position regression that should halt the loop
      (spec default: >5 position drop, 1 consecutive run — confirm or adjust)
- [ ] Any brand-voice or content constraints that content proposals must respect
      (e.g. no keyword stuffing, must match existing tone) — these become qualitative
      guardrails a human checks at review time, not automated checks (see
      `RISK-REGISTER.md` R5: qualitative rubrics are always human-approved)
- [ ] `ads_daily_budget_ceiling` / `ads_monthly_cap` — set to `null` unless the ads loop
      is also being scoped now (it isn't wired in Phase 1/2; SEO-only intake can leave
      these `null`)

## 4. Credential aliases (opaque names only — never raw secrets here)

- [ ] `gsc` alias — name only, e.g. `acme-gsc-readonly`. The actual OAuth token/refresh
      token is stored in Windows Credential Manager under this alias, never in this repo.
      - [ ] Confirm the GSC-verified property string this alias should read
            (`https://example.com/` vs `sc-domain:example.com` — `gsc.py`'s `site_url` param)
      - [ ] Confirm the GSC user/service account this credential belongs to has
            **read-only** `webmasters.readonly` scope — nothing broader
- [ ] `dataforseo` alias — name only, e.g. `acme-dataforseo-read`. Resolves to a
      `login:password` credential string (DataForSEO's Basic-auth model — see
      `dataforseo.py`), never stored raw in this repo.
      - [ ] Confirm the DataForSEO account used has read-only SERP/keyword-data access
      - [ ] Confirm location/language/device defaults (`location_code`, `language_code`,
            `device` params) match the client's actual target market

## 5. Loop mode

- [ ] Confirm the loop starts `approval_mode: propose-only` (the only safe default for
      a new project — never start a real project `tier1-enabled`)
- [ ] Confirm who the human reviewer is for `/review-pending` (name/role — this loop
      cannot self-approve anything, ever)
- [ ] Confirm `schedule` cadence (cron expression) and that it matches the client's
      expectation for how often the loop should run

## 6. What this checklist does NOT cover

- Actually creating the Windows Credential Manager entries — that happens out-of-band,
  by a human, at intake time, following the alias names agreed above.
- Wiring `gsc.py`/`dataforseo.py` into `run_loop.py`'s `fetch_metrics` — both
  connectors' real API calls are implemented (Phase 2 infra prep, 2026-07-15/16) but
  deliberately **not** called from any run pathway yet; only `mock_metrics.py` is wired.
  Wiring a real connector into the run path is itself a decision that needs explicit
  human sign-off, not something this checklist pre-authorizes.
- The actual `/intake-project <slug>` run — this checklist is preparation for that
  command, not a substitute for it.

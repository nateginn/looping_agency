# Handoff — read this first in a new session

Read this before touching anything else. It replaces needing to scroll a long prior chat transcript. Canonical docs are `AgentColabPlan.md` (design), `PLAN-REVIEW-LOG.md` (plan review history), `RISK-REGISTER.md` (findings + risk acceptances), `CLAUDE.md` (operating instructions), `PHASE2_READINESS_CHECKLIST.md` (Phase 2 go/no-go tracking) — read those next if you need depth.

## Status as of 2026-07-16 (end of the Phase-1-closeout session)

**Phase 1 is complete: implemented in Python, tested (32/32), committed, and pushed to `origin/master`** (`github.com/nateginn/looping_agency`, branch `master`). The Node→Python language correction is fully documented in `RISK-REGISTER.md` R8. **This project is Python-only — see `CLAUDE.md` "Implementation language" and the `language-choice-approval` memory: never introduce a non-Python language without the user's explicit prior approval.**

Since the last push, this happened (all **uncommitted** in the working tree — committing it is Step 0 below):

- **Codex adversarial re-review of `AgentColabPlan.md`, scoped to the language switch (Rounds 3–4 in `PLAN-REVIEW-LOG.md`): converged with a genuine `VERDICT: APPROVED`** — not a round-cap tie-break. Two findings fixed: interpreter/venv pinning added to the scheduling contract, and the imprecise TLS-fix note replaced with a pointer to R8. Files touched: `AgentColabPlan.md` (header + two sections), `PLAN-REVIEW-LOG.md` (Rounds 3–4 transcript).
- **Deleted `looping_agency_plan.md` and `LOOP_AGENCY_PLAN.md`** at the user's request (preliminary individual drafts, superseded by `AgentColabPlan.md`).
- **`PHASE2_READINESS_CHECKLIST.md` exists at repo root (untracked — add it to git in Step 0).** User-authored go/no-go tracker: sections 1–2 (framework + safety controls) all `[x]`; sections 3–6 (real project, credentials, smoke test, launch mode) open.

## THE USER HAS EXPLICITLY AUTHORIZED PHASE 2 KICKOFF (2026-07-16)

Decisions the user already made — do not re-ask:

- **First real project: the user's (Nate's) own website.** Propose-only mode. The loop reads GSC/DataForSEO and writes recommendations under `projects/<slug>/` only — it never touches his website repo (auto-deploys on push; Tier 2 human-only, `RISK-REGISTER.md` R6).
- **Credential resolver library: `keyring`** (pinned in `requirements.txt`).
- **`.env` fallback is acceptable to the user** — but only with the mandatory restrictive-ACL startup check the plan requires (Credential Manager via keyring is still checked first).
- **He already has a working GSC credential** (its exact form — access token vs refresh token vs service-account JSON — gets identified at the handoff step, and may require adding pinned `google-auth` for token minting).
- **Secrets never enter the chat transcript.** He stores them himself in his own terminal (`--store` CLI or editing `.env` directly). Never ask him to paste a raw secret into the conversation.

## Exact next steps — the accepted Phase 2 execution plan

### Step 0 — Commit the outstanding review work
One commit: Rounds 3–4 review edits (`AgentColabPlan.md`, `PLAN-REVIEW-LOG.md`), this file, the two deleted preliminary plans, and newly-tracked `PHASE2_READINESS_CHECKLIST.md`. Keeps Phase 2 work clean in history. (Push per the user's standing instruction — every commit goes to the public GitHub remote; if the harness safety classifier hard-blocks the push, hand the push to the user, never work around it.)

### Step 1 — Credential resolver: `tools/lib/credentials.py` (new)
- `resolve_credential(alias, project_dir=None) -> str`: (1) `keyring.get_password("loop-agency", alias)`; (2) fallback `projects/<slug>/.env`, key = alias uppercased with hyphens→underscores (e.g. `acme-gsc-readonly` → `ACME_GSC_READONLY`, matching `.env.example`) — **before reading, ACL-check the file via `icacls` (stdlib subprocess, no pywin32) and refuse with a clear error if any principal beyond current user + SYSTEM/Administrators has access** (Codex original-review R2 finding #3 requirement); (3) neither → raise naming the alias, never any value.
- CLI: `--store <alias>` (interactive `getpass` → `keyring.set_password`; this is how Nate hands over secrets), `--check <alias> [--project <slug>]` (reports which store resolved it, never prints values), `--verify` self-test (fake keyring backend + temp `.env` good/bad ACLs).
- Add pinned `keyring` to `requirements.txt`, install into `.venv`. Fix `.env.example`'s stale `redact.mjs` → `redact.py` reference.

### Step 2 — Wire real connectors into the run path
- Move `ConnectorError` to `tools/lib/errors.py`; re-export from `mock_metrics.py` for compat.
- Rewrite `run_loop.py:_fetch_metrics` (currently single-branch, ~line 111) to dispatch per entry of `spec["inputs"]`: `mock` → unchanged; `gsc` → `gsc.pull_metrics(...)` with the project's alias + real resolver + `site_url`/date-window from spec; `dataforseo` → `dataforseo.pull_metrics(...)` with `targets` + market params from spec. Wrap real-connector exceptions into `ConnectorError` (empty `raw_secrets` — connectors redact internally) so the existing partial-failure path handles them uniformly.
- Multiple inputs: GSC is the primary metrics source (clicks/impressions/sample_size); DataForSEO enriches position per (keyword, page). First live spec starts `inputs: [gsc]` only; enable `dataforseo` after the first reports are reviewed.
- Generalize `credential_alias_used` in run.json (currently hard-coded to the `"mock"` key, ~line 263).
- Extend `spec_validate.py` conditionally: `gsc` in inputs → require `site_url` + `metrics_window_days` (positive, default 28); `dataforseo` → require `targets` (non-empty `{keyword, page}` list) + optional `location_code`/`language_code`/`device`. Update `templates/loops/seo/spec.md` placeholders. `_demo`'s `inputs: [mock]` spec must stay valid untouched.
- Tests: existing 32 exit-criteria checks must still pass; add dispatcher checks (gsc-input spec dispatches with fake resolver + fake `http_post`, no network) and a real-connector-failure → clean `partial-failure` run.json check.

### Step 3 — Connectors-only smoke test: `tools/smoke_test.py` (new)
`python tools/smoke_test.py <project>`: resolves each alias (reports which store answered, never the value), calls each connector in `spec.inputs` live once, prints redacted summary (auth OK/failed, HTTP status, row count), includes a deliberate bad-alias partial-failure simulation proving clean error logging. Writes nothing into `runs/`. Human reviews and accepts (checklist §5).

### Step 4 — Nate-assisted credential handoff (interactive, HIS terminal)
1. Agree alias names (suggest `<slug>-gsc-readonly`, `<slug>-dataforseo-read`).
2. Identify the GSC credential's form: raw access token (expires ~1h, smoke-test-only) vs refresh token / service-account JSON (likely — then add pinned `google-auth` and a thin token-minting step for GSC, `webmasters.readonly` scope only, so scheduled runs work).
3. Nate stores each secret himself: `./.venv/Scripts/python.exe tools/lib/credentials.py --store <alias>` or editing `projects/<slug>/.env` in his editor (then verify the ACL check passes; tighten with `icacls` if needed).
4. Verify with `--check` (no values printed).

### Step 5 — Onboard the project: `/intake-project <slug>`
Follow the existing skill exactly. Collect: slug, domain + **exact GSC property string** (`sc-domain:` vs URL-prefix), goals one-liner, initial target keyword/page list, guardrail threshold (default >5 position drop), cadence cron, reviewer (Nate). Record his site repo's auto-deploy behavior in `project.md` as Tier-2/human-only per R6. `ads` caps stay `null`. Spec is forced `approval_mode: propose-only`.

### Step 6 — Live smoke test, then first run
1. `tools/smoke_test.py <slug>` → Nate reviews and accepts (checklist §5).
2. First real run: `./.venv/Scripts/python.exe tools/run_loop.py <slug> seo` — recommendations only; summarize `report.md` for him.
3. Update `PHASE2_READINESS_CHECKLIST.md` statuses; add a `RISK-REGISTER.md` note that Phase 2 kickoff was explicitly user-authorized 2026-07-16; update this file; commit.

### Out of scope for this plan
Content-social/ads loops; any Tier-1 apply against the real project (propose-only until Nate reviews the first two reports); any push to his website repo (Tier 2, always); Task Scheduler registration (after first manual runs look right).

### Verification bar
All module `--verify` self-tests + exit-criteria suite (32 existing + new checks) through `.venv/Scripts/python.exe`, no network. ACL check demonstrably refuses an over-broad `.env`. Live smoke test reviewed by Nate before the first real run; first run produces valid redacted `run.json`/`report.md` with zero writes outside `projects/<slug>/`.

## Hard boundaries (do not cross without the user present)

- **Never introduce a non-Python implementation language without the user's explicit prior approval** (R8 lesson; standing memory rule).
- **Never ask the user to paste a raw secret into chat**; never write one to any repo file. Aliases only.
- Never touch the user's website repo — auto-deploys on push, Tier 2/human-only, always (R6).
- The real project stays `propose-only` until the user has reviewed its first two reports.
- Don't attempt to work around a harness safety-classifier hard block — stop and hand it to the user.

## Fast sanity checks

```
./.venv/Scripts/python.exe tools/tests/phase1_exit_criteria.py   # all PASS, 32/32 (more once Step 2 lands)
git log --oneline                                                  # see what's committed
git status                                                          # dirty until Step 0's commit
git remote -v                                                       # origin -> github.com/nateginn/looping_agency
```

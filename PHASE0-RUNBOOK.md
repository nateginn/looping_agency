# Phase 0 runbook — manual GitHub protection & credential verification

Human-run only, like `PHASE3-SCHEDULING.md` — nothing here is auto-executed by any tool in this workspace, and none of it has been performed yet. This is the sequence to run, in order, before `tools/publish.py` or `tools/deploy_verify.py` ever touches a real repo. See `PLAN.md` Phase 0, `RISK-REGISTER.md` R6/R10/R11, and the Phase 0 readiness review in this session's history for the full reasoning.

## 1. Automation identity (do first — everything else depends on it)
Create a GitHub App installation (preferred) or dedicated machine user for `nateginn/artwebsite` — **never Nate's personal account/PAT**. Scope: `Contents: write`, `Pull requests: write`, `Administration: read` only (never write — a compromised session must not be able to weaken the control it's checking).

## 2. Branch protection on `main`
Configure (classic branch-protection settings, and/or an equivalent ruleset — `verify_branch_protection()` checks both): require a PR before merging; require **≥1 approving review**; dismiss stale approvals on new commits; block force pushes; block deletions; enable "do not allow bypassing the above settings" (`enforce_admins`); confirm the bypass list (classic `bypass_pull_request_allowances` and every active ruleset's `bypass_actors`) is empty. If the repo's plan supports it, also restrict who can push to `main` to Nate only.

**Open decision, not yet resolved:** requiring ≥1 approving review conflicts with "no second Nate action per proposal" (GitHub blocks self-approval, so the automation identity can never satisfy its own review requirement). The reviewed, recommended resolution is a second, narrowly-scoped Nate-owned PAT (`Pull requests: write` only) that auto-submits the approving review right after Loop Agency records approval — **not yet built**. Until it is, either accept a manual review click per proposal, or explicitly re-decide this tradeoff before activating anything.

## 3. Repository setting: Allow auto-merge
Settings → General → Pull Requests → enable "Allow auto-merge". Without this, `enable_auto_merge()`'s GraphQL call fails outright even with correct branch protection — confirmed not yet enabled; not previously documented in `PLAN.md`.

## 4. Store the automation token
`./.venv/Scripts/python.exe tools/lib/credentials.py --store <project>-github-token`, entered by Nate in his own terminal — never pasted in chat. Verify with `--check <project>-github-token --project <slug>`. This session stored nothing and used no credential.

## 5. Live fail-closed test matrix (disposable repo only, never `artwebsite`)
Run the matrix from this session's Phase 0 readiness review — direct push, force push, branch deletion, merge-without-approval, merge-after-approval, self-approval attempt, admin-scope-write attempt, a test ruleset with a bypass actor — against a throwaway repo with the real automation token. Confirms `verify_branch_protection()`'s assumed GitHub API response shapes (`/rules/branches/{branch}`, `/rulesets`, `/rulesets/{id}`) match reality; they have only been validated against hand-built fixtures so far.

## 6. Health-check config (Phase 6a, this session's work)
No credentials needed — `deploy_health.py`'s checks are unauthenticated public reads. Before first use: confirm `art`'s real `hostname`/`base_url`/representative lead-intent pages, and decide whether a commit-evidence extractor is worth adding (nothing on the live site is currently known to expose a build/commit marker — currently runs as `not_available`, non-blocking).

## 7. Redeploy integration point (still open)
`tools/deploy_verify.py`'s `redeploy_previous_sha` hook has no real implementation — it raises `DeploymentIntegrationError` and escalates, by design (redeploy is Tier 2, human-only, R6). Before rollback can complete automatically, wire it to either a human-run script Nate executes when paged, or an automated revert-PR path through `publish.py` once steps 1–5 above are live and proven.

## 8. Only after all of the above
Decide whether/when to flip `art`'s `spec.md` (`approval_mode: tier1-enabled`, remove `manual_approval_only` per action) — a separate, explicit human decision, never implied by any of the above and not performed by this runbook.

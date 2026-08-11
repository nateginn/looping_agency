# Plan: SEO change pipeline for `art` (draft → Codex-reviewed auto-approve → apply → **direct push** → verify/rollback → notify)

_Round 2 revision by Claude, after two rounds of Codex adversarial review. Supersedes the prior SEO-monitoring-expansion plan in this file's history (shipped 2026-07-23/24, commits `e282780` / `3626644`) — see git log. That work remains live and unaffected._

---

> **2026-08-11 — a separate, converged plan now sits upstream of this one:
> `PLAN-SEO-PROGRAM-INTEGRATION.md`** (8 rounds of Codex adversarial review,
> `PLAN-REVIEW-LOG-SEO-PROGRAM.md`). It does not change Path B, and it requires no R6
> amendment — but it found that `art`'s organic web-search channel produced **23 clicks in
> 28 days, 20 of them brand**, and three previously unrecorded defects (a DataForSEO
> domain-match bug returning competitors' ranks, discarded SERP-composition data, and a
> `min_sample_size` comparison that makes "verified winner" vacuous). It argues Path B
> should stay unbuilt until candidate selection, measurability and the local/GBP channel are
> addressed, and it recommends freezing `art`'s auto-implementation first. Read it before
> resuming any work below.

## SELECTED OPERATING MODEL — read this before anything below it

**Path B (direct push + post-deploy health verification + notification) is the selected model. Path A (side branch → PR → GitHub auto-merge, gated by branch protection) is NOT selected and is dormant by decision.** Direction chosen 2026-07-30, restated and expanded with explicit requirements 2026-08-10.

Most of this document below this section was written for Path A. It is retained as design history and as the record of six rounds of Codex adversarial review — **not** as the plan of record. Every Path-A-only section is marked `[HISTORICAL — PATH A]`. Where the two conflict, this section wins.

### What Nate actually wants (stated 2026-08-10)

1. **The loop adjusts the website on its own.** No per-change human approval. Nate is not the gate.
2. **He is notified when a change is made**, through the MMC daily briefing — not through a separate channel he has to remember to check.
3. **He gets positive confirmation that the change did not damage the site**, i.e. an explicit "deployed, verified healthy, no rollback" statement — silence is not confirmation.
4. **If a rollback did fire, he is told that too**, with enough detail to troubleshoot what went wrong.

### How much of that already exists (2026-08-10)

| Requirement | Status |
|---|---|
| Autonomous draft → adjudication → **local commit**, no human approval | **Built and active for `art`.** `/codex-seo-review` + `apply.py`; three spec gates all opted in (`tier1-enabled`, `auto_implementation_enabled: true`, `manual_approval_only: false` on `title-tag-rewrite`/`meta-description-rewrite`). See `PLAN-PHASE7-CODEX-REVIEW.md` and R14. |
| Post-deploy health checks + rollback orchestration | **Built, offline-tested, unwired.** `tools/deploy_verify.py`, `tools/lib/deploy_health.py`. Its default redeploy hook *refuses*; no project is wired to it. See R12. |
| MMC briefing section fed by the loop's event log | **Built.** `MMC/collector/sources/looping_events.py` + `advance_looping_cursor.py`, backed by `events.jsonl`. Cursor advances only after send-acceptance, so events are never lost or repeated. |
| **Push the local commit to the deployment branch** | **Not built.** This is the entire remaining gap. No code in this workspace pushes anything. |
| Deploy/health/rollback events reaching the MMC briefing | **Not built.** `deploy_verify.py` persists results but emits no `events.jsonl` entries, so nothing carries them into the briefing. |

So the missing work is genuinely narrow: **the push, the deploy/rollback event emission, and the wiring between them.** Everything on either side of that gap exists and is tested.

### What Path B requires before it can be switched on

This is the honest gate list. None of it is done, and none of it should be inferred as done.

- **R6 must be formally amended, not quietly contradicted.** R6/R10 currently classify *every* push to `D:\Dev\artwebsite` as Tier 2, human-only, **unconditionally** — because that repo auto-deploys on push with no staging gate. Path B moves that line. That is Nate's call to make, but it must be recorded as a dated amendment row with its reasoning, exactly as R10 amended R6 before it. Until that row exists, no push tooling may be built or run.
- **A push component with the same refusal discipline `apply.py` already has**: re-read the spec's gates fresh at push time, re-verify the commit's ancestry and single-file diff from the commit's own git objects, confirm the local ref hasn't moved, and refuse on any drift.
- **Bounded blast radius.** Only `title-tag-rewrite` and `meta-description-rewrite`, only on `priority_pages`, only one change in flight at a time. `internal-link-addition` remains excluded (no HTML-mutation engine exists, by design).
- **`deploy_verify.py` wired to a real redeploy hook**, replacing the default refusing stub — and that hook is itself a push, so it inherits every constraint above.
- **Event emission for `deployed`, `health_verified`, `rollback_started`, `rollback_succeeded`, `rollback_failed`**, so the MMC briefing can state requirements 3 and 4 rather than inferring them from absence.
- **An escalation path when rollback itself fails.** Health-check-failed-and-rollback-failed is the one state the automation cannot resolve, and it must reach Nate loudly, not as a line in a daily digest.

### Why the security argument changes shape under Path B

Path A's boundary was server-side: GitHub branch protection, which survives a fully compromised local agent. Path B has no server-side boundary — the loop pushes to production directly. Its safety properties are different in kind, and should be stated plainly rather than dressed up as equivalent:

- **Bounded change class.** A title tag or meta description is text in a template. It cannot execute, cannot alter routing, cannot touch data.
- **Independent adversarial review before the commit exists.** Two Codex rounds that must agree, bounded at one revision / two passes / four calls, any disagreement holds. That review happens *before* anything is committed, let alone pushed.
- **Detection and reversal after the fact.** Bounded public-site checks, automatic redeploy of the previous known-good SHA, and escalation if that fails.
- **A complete audit trail.** Append-only, redacted `events.jsonl` is the source of truth for the review subsystem, and the same log feeds the briefing.

What Path B does **not** have, and must not be claimed to have: any control that survives a compromised local agent. Anything with filesystem access to this repo could forge the review verdicts. This matches the project's existing, already-documented position on `publish.py`'s guards ("Where the security boundary actually is", below) — it is an accountability and recovery model, not a security boundary.

---

## Goal

Let Nate act on the SEO loop's findings — actually changing title tags and meta descriptions on acceleratedrehabtherapy.com — from either his desk or his phone. Nate's one required intervention is explicit approval of the proposed change and copy. After that approval, the pipeline should apply the change, open a PR, run automated checks, merge when those checks pass, confirm the live website is healthy, and automatically roll back/report if deployment damages the site. Today the loop cannot change anything from anywhere: proposals contain no copy, there is no remote approval path, and no tooling can push. The target is one accountable pipeline, invoked locally (`/review-pending` skill) or remotely (Telegram → Hermes/DAC → headless Claude), carrying a proposal from draft copy through deployment and post-deployment verification without requiring Nate to merge a PR manually.

## Where the security boundary actually is (revised after Round 1)

Round 0 treated `publish.py`'s guards and a `dual-agent-core` deny-list as the production gate. Codex correctly demolished that: the dispatched Claude session runs with `defaultMode: "dontAsk"`, `Bash(*)`, and write access to all of `D:/Dev/**`, so it can bypass any local tool via `git -C ... push origin HEAD:refs/heads/main`, a Python `subprocess`, or by editing the settings file itself. **Local deny-rules are not a security control against an agent that can run arbitrary commands.**

The revised model, in priority order:

1. **GitHub branch protection on `main` is the server-side change boundary** (new hard prerequisite, Phase 0). A protected `main` requiring a PR — with force-push and direct-push disabled for all actors including the token owner, and required automated status checks — means the normal pipeline cannot deploy an unvalidated direct push. The PR may be merged automatically after the proposal's human approval has already been recorded and all required checks pass.
2. **`publish.py`'s guards are correctness and ergonomics, not security.** They stop honest mistakes (wrong branch, stale base, unrelated diff) and produce a clean reviewable PR. They are defense-in-depth; they are explicitly *not* what stands between an agent and production.
3. **State the autonomy claim precisely.** The human approval is recorded in Loop Agency before the automation credential can publish/merge through the normal path. GitHub branch protection requires the PR and automated checks, but does not understand the local proposal approval. Therefore this is an accountable workflow boundary, not a cryptographic guarantee against a fully compromised local agent holding the automation credential. The automation identity must still be separate from Nate's identity, must not modify branch protection, and must be restricted from direct pushes to `main`. The remote instruction "do not wait for a manual merge" is an operating instruction; the enforceable controls are the proposal approval record, PR requirement, required checks, and deployment health/rollback logic.
4. **The Telegram allowlist authenticates the dispatcher, not the operation.** It proves a message came from Nate's Telegram account; it does not constrain what the resulting session does. Convenience, not a boundary.

**The real production control plane is wider than this pipeline** and must be documented as such (Codex #14): direct SSH to the prod host, re-running an old GitHub Actions job, edits via the GitHub web UI/API, changes to repository secrets consumed by the deploy step, and any future edit to `deploy.yml` itself. `publish.py` participates in exactly one of these paths. Claiming it is "the" safety boundary would be false.

## Context the reviewer needs (verified against code)

- **Four repos.** `D:\Dev\Looping _agency` (SEO loop, Python-only per R8); `D:\Dev\artwebsite` (Django, GitHub `nateginn/artwebsite`, HTTPS remote); `D:\Dev\MMC` (Tier-0 read-only, daily Telegram briefing); `D:\Dev\dual-agent-core` (Hermes/DAC dispatch).
- **Deploy trigger.** `artwebsite/.github/workflows/deploy.yml` is the only workflow; trigger is exactly `on: push: branches: [ main ]`. It runs `manage.py test`, then SSHes to prod: `git fetch && git reset --hard origin/main`, rewrites `.env` from GitHub Secrets, `migrate`, `collectstatic`, and `systemctl restart gunicorn`. It currently has no post-deploy health check or automatic rollback. Non-`main` branch pushes do not deploy **today** — see Phase 0 for why that is treated as a fact with an expiry date, not a permanent invariant.
- **State machine.** `draft → reviewed → approved → applied → verified`, plus `implemented` (local commit exists) and `implement-failed`. `review_pending.py` is fully non-interactive. `apply.py` re-checks tier/approval/`manual_approval_only` regardless of caller.
- **What apply does.** `artwebsite_seo.py::finalize_worktree()`: `git worktree add <tmp> -b seo/<id> main`, rewrite, `git add -A`, `git commit`, `git rev-parse HEAD`, `git worktree remove --force`, `delete_branch=False`. Verified: **no `git push`/`fetch`/`origin` anywhere under `tools/`.**
- **Blocking gap.** `apply_rewrite()` requires `proposal["implementation"]["new_value"]`; nothing writes it. No proposal can be applied today.
- **Two edit surfaces.** `resolve_edit_location()` handles `template-block` and `views-context`, raising on ambiguity.
- **Live detection.** `github_compare.py::compare_commit_to_main()` (unauthenticated GET, optional Bearer token) + `run_loop.py::_promote_live_implementations()` flips `implemented → applied` when GitHub reports `identical`/`ahead`.
- **Remote rail.** `watcher.py` dispatches `Project`/`Scope`/`Task-ID`/`Task` blocks to headless Claude, cwd pinned to `dual-agent-core`, Scope validated only as "a real directory under `D:/Dev`". Telegram sender allowlisted by numeric user ID.
- **`gh` CLI is NOT installed.**
- **Confirmed pre-existing bug (Codex #11, verified this session).** `run_loop.py::_proposal_row_for_page()` (L383-385) matches on page path **only** and returns the first matching keyword row, ignoring `proposal["target"]["keyword"]`. Worse, in `_evaluate_prior_experiments()` (L406-427), a missing row yields `metric_value=None` → `drift=None` → `breached=False` → the `else` branch marks the proposal **`verified` ("verified winner")** on absent data. The verify loop can currently declare a false win and can score the wrong query. This is independent of this plan but sits directly on the path it depends on.

## Approach

### Autonomy revision (user-approved direction, 2026-07-29)

The required human action is proposal approval, not PR merge. The older Phase 0 bullets below that require a human GitHub review or Nate-only merge are superseded by this revision. `main` must still require a PR, block direct/force pushes and deletions, and require automated checks; it must not require a human PR review in the normal path. After Loop Agency records Nate's approval, the separate automation identity may create the PR and enable GitHub auto-merge. The automation identity may not change branch protection.

- The approval record must bind proposal ID, target page/keyword, previous value, proposed value, implementation commit SHA, approver, and timestamp.
- The PR must contain that approval reference and the exact one-file diff. A changed or stale proposal must refuse auto-merge.
- Required checks must include application tests, diff validation, and deployment health. A green PR check is necessary but not sufficient: the production workflow must verify the live site after deployment.
- `deploy.yml` must record the previous known-good SHA, deploy the new SHA, run bounded HTTP/TLS/canonical/representative-page smoke tests, and automatically redeploy the previous SHA on failure. It must persist the failure, both SHAs, health results, and rollback result, notify the reporting path, and create an automated revert PR. If rollback or revert validation fails, the system stops and escalates to Nate.
- This is an accountable autonomous workflow, not a cryptographic guarantee against a fully compromised local agent holding the automation credential. The server-side controls protect `main`; the approval record, audit trail, health checks, and rollback make normal autonomous operation reviewable and recoverable.

**Phase 0 `[HISTORICAL — PATH A]` — Enforcement prerequisites + honest register amendment (human sign-off; blocks everything remote).**
_Not a prerequisite for the selected Path B: there is no PR, no auto-merge, and no automation identity to separate. Retained as the design record for what Path A would have required, and as the reason `publish.py` refuses to run today. R11 remains Open for `publish.py` specifically._
- **0a. Separate the automation identity from Nate's identity** (Codex R2 #1, the round's critical finding). "Require a pull request" alone does **not** require an approving review — a token with PR write can open and immediately merge its own PR, making the human gate decorative. But requiring approval while the token acts *as Nate* deadlocks, because GitHub forbids approving your own PR. So the automation credential must be a **distinct identity** — a GitHub App installation token (preferred) or a machine user — never Nate's personal PAT. Then "require 1 approving review" is satisfiable by Nate and unsatisfiable by the automation, by construction.
- **0b. Enable GitHub branch protection on `nateginn/artwebsite` `main`**: require a PR before merging; **require ≥1 approving review**; dismiss stale approvals on new commits; block force pushes; block deletions; **"do not allow bypassing the above settings"** (applies to admins); confirm no actor/app is on a bypass list. Load-bearing, and must exist *before* any push-capable token.
  - **Also set "Restrict who can push to matching branches" to Nate only** (Codex R3 #3). Merging writes to `main`, so excluding the automation identity from that list makes "automation cannot merge" a real, server-side property rather than a promise — and it does not impede `seo/*` pushes, since the restriction applies only to the matching pattern. **Dependency:** this control is free on public repos but requires a paid plan on private ones. If `artwebsite` is private on the free tier and this is unavailable, **the security claim must be weakened honestly** to: *"the automation cannot merge without Nate's approval; Nate's approval is the deployment gate"* — because `contents: write` is required for both branch push and merge and cannot be separated by token scope. The instruction "do not merge" in the remote template is an instruction, not a control.
- **0c. Prove it with the actual credential, not by inspection** (Codex R2 #3, #12). Fail-closed matrix: direct push to `main`; force push; opening a PR and attempting to merge with the automation token and **no** human approval; attempting to merge with the automation token **after** Nate approves (must also fail, if 0b's push restriction is in force); deleting `main`. One expected-to-succeed case: Nate merges after approving. Nominal PAT scope is not evidence — effective permissions are.
- **0c-bis. Design the protection-check permission deliberately** (Codex R3 #4). Reading branch protection needs an administration permission; granting *write* there would let a compromised session weaken the very control it is checking. So: use a **read-only** administration App permission, query **both** classic branch protection **and** rulesets (classic data alone does not prove absence of ruleset bypass actors), and verify as part of 0c that the publishing identity cannot modify protection settings.
- **0d. Amend RISK-REGISTER R6** with a dated amendment row (existing format `| ID | Source | Severity | Finding | Status | Resolution / Acceptance |`), not an in-place edit. New boundary: merge to `main` and any direct `main` push stay **Tier 2 human-only, enforced by branch protection + required review**; `seo/*` branch push, read-only `git fetch`, and PR *creation* become **Tier 1 tooling-permitted**. The row must state plainly that enforcement is server-side and that local guards are not a security control (Codex R1 #2, #3, #13). Mirror into `projects/art/project.md` and `CLAUDE.md`.
- **0e. Document the full production control plane** (Codex R1 #14) in the register/`project.md`: SSH, Actions re-run, web UI/API edits, secret changes, `deploy.yml` edits. State explicitly that this pipeline covers one path only. Since `deploy.yml` lives in the protected branch, workflow changes now inherit the same required-review gate (Codex R2 #11) — that, not the runtime shape check, is what actually governs them.

**Phase 1 — Fix the verification bug (new; promoted ahead of the pipeline).**
Codex R1 #11, confirmed in code. In `run_loop.py`:
- `_proposal_row_for_page()` → match on normalized page **and** normalized `proposal["target"]["keyword"]`.
- **Define the normalization policy explicitly** (Codex R2 #7): NFKC, casefold, strip, collapse internal whitespace — applied identically to the stored target keyword and the GSC row keyword. If more than one row matches after normalization, treat as **ambiguous** and refuse to evaluate rather than silently picking one.
- `_evaluate_prior_experiments()` → no matching row, an ambiguous match, **or a matching row whose `position` is `None`** (Codex R3 #5 — the current code treats a present-but-null position as non-breaching and therefore `verified`, the same false-win by a different route) produces a third outcome, `not-evaluable`: status stays `applied`, cooldown retained, reason recorded. Only an actual measured numeric position may yield `verified` or `breached`.
- **Integrate `not-evaluable` into the reported state model** (Codex R2 #8), not just a decision string: persist `evaluation_outcome` + `evaluation_reason` on the proposal, and add a dedicated report section. A proposal that is repeatedly not-evaluable is a real signal (wrong keyword recorded, page moved, GSC coverage gap) and must be visible, not buried — add an attention threshold for N consecutive not-evaluable runs.
- Regression tests: missing row → `not-evaluable`; **matching row with `position: None` → `not-evaluable`**; wrong-keyword row present → not used; two matching rows → ambiguous; correct row → evaluated. There is no reason to build a shipping pipeline on an evaluator that can report a false win.

**Phase 2 — Give proposals real content.**
- `artwebsite_seo.py`: add `read_current_value(repo_path, page, action_type)` reusing `resolve_edit_location()`.
- `tools/draft_copy.py` (new): deterministic storage + validation; Claude authors the words. Writes `implementation: {new_value, previous_value, drafted_at, drafted_by}` via `atomic_write_json`. Validates non-empty, single line, ≤60 chars (title) / ≤155 (meta), refuses `new_value == previous_value`. `--verify` self-test.
- **`apply_rewrite()` must verify `previous_value` still matches the on-disk content in the worktree and refuse on mismatch** (Codex #8) — otherwise approved copy silently overwrites an intervening unreviewed edit.
- `run_loop.py` stays deterministic and LLM-free.

**Phase 3 — Concurrency: one shared mutation lock (revised — Codex R2 #5, #6).**
Round 1 proposed compare-and-swap on proposal JSON. Codex correctly showed a naive `version` check would break normal operation: `run_loop.py` loads all proposals once, mutates them across several functions (`_promote_live_implementations`, `_evaluate_prior_experiments`, stale-counter bumps), writes fresh copies mid-run during promotion, then writes its original in-memory collection again at the end — so a version assertion would either reject ordinary runs or reintroduce the very stale-write race it was meant to close. CAS also protects only the JSON, not the git repo (#6).

Revised, simpler, and strictly stronger: **one lock, explicitly named and scoped** (Codex R3 #1, #2). Reuse the existing per-loop `run.lock` (`lib/lock.py`, which already has PID+age stale recovery) as the single mutation lock. This closes both the JSON clobber and the repo-level race — concurrent `fetch`/worktree/branch/PR operations — with one mechanism.

**The nesting trap, confirmed in code.** Today `run_loop.py:728` holds `run.lock` for the entire run, and `_promote_live_implementations()` then acquires `apply.lock` *nested inside it* at `run_loop.py:471`, while `apply.py:82` acquires `apply.lock` independently. Collapsing these naively would make `run_loop` deadlock against its own held lock. So the ownership rule must be explicit:
- `run.lock` is the one mutation lock. `apply.lock` is retired.
- `run_loop.py` acquires it once, as today.
- `_promote_live_implementations()` **drops its own acquisition** and documents that the caller must already hold the loop lock.
- `apply.py`, `publish.py`, **and `review_pending.py`** each acquire it. `review_pending.py` was omitted in Round 2 — Codex R3 #2 is right that approve/reject/resolve-breach can otherwise read stale state and overwrite a concurrent update, and that an unchanged-status check does not catch a stale write.

Cost: approve/apply/publish block while a run holds the lock (bounded by `max_run_duration_minutes`, 30). Correct trade — blocking briefly beats silently reverting a transition. The concrete bug fixed: `apply.py` transitions a proposal mid-run, then `run_loop.py`'s end-of-run write of stale in-memory state reverts it. No schema migration needed (dropping the `version` field avoids back-filling existing proposals).

**Phase 4 — Enable Tier-1 apply for `art`.**
`spec.md`: `approval_mode: propose-only` → `tier1-enabled`; drop `manual_approval_only: true` from `title-tag-rewrite` and `meta-description-rewrite` only. Keep it on `internal-link-addition` (no auto-implementer).

**Phase 5 — Harden `apply.py`'s baseline (Codex R1 #7, R2 #9, #10).**
Before `git worktree add`: `git fetch origin`, verify `refs/heads/main == refs/remotes/origin/main`, refuse with a "pull first" message on drift. Moving this to apply time (not publish time, as Round 0 had it) means copy is authored against current code, not merely published against it.
- **Persist the base SHA on the durable record** (Codex R2 #9, confirmed in code): `make_attempt()` records `base_commit` only inside `implement_attempt` (`artwebsite_seo.py:168`), and `apply.py:134` pops that object on success — destroying the base SHA at exactly the moment the proposal reaches `implemented`, the state `publish.py` needs it in. Write `implementation_base_sha` onto the proposal itself as part of the `implemented` record, and have `publish.py` require the commit's parent to equal it.
- **Use fully-qualified refs throughout** (Codex R2 #10): `refs/heads/main` and `refs/remotes/origin/main`, never bare `main` (current code uses `git rev-parse main` and `git worktree add ... main` at `artwebsite_seo.py:164,178`), so a tag or symbolic ref named `main` cannot resolve to something unintended.

**Phase 6 `[HISTORICAL — PATH A]` — `tools/publish.py` (new): push branch + open PR.**
_Built and offline-tested; dormant by decision. `publish.py` hard-refuses a `main`/`master` destination by construction (`FORBIDDEN_DESTINATION_BRANCHES`), so it structurally cannot perform Path B's push — Path B needs a different component, not a flag on this one. Do not delete: if the direction is ever revisited, this is a complete, tested implementation._
The only component permitted to touch a remote. Guards — reframed as correctness controls, with branch protection as the actual backstop:
- Proposal `status: implemented`, with `implemented_branch` and `implemented_commit_sha` recorded.
- **Strict identifier validation** (Codex #4): proposal ID must match `^prop-[0-9T:.Z-]+-[a-z0-9]+-\d+$`; refuse any `:`, `?`, `*`, `~`, `^`, whitespace, or `refs/` substring. (Practical severity is low — IDs are generated as `prop-{run_id}-{i}` from a timestamp plus random suffix, not attacker-supplied — but the check is nearly free.)
- **Push the validated SHA to a fully-qualified destination**, never a mutable local branch name (Codex #4, #5): `git push origin <implemented_commit_sha>:refs/heads/seo/<id>`. This is immutable by construction and closes the TOCTOU window between diff-validation and push. Additionally re-verify the local ref still points at that SHA immediately before pushing.
- **Validate the push destination** (Codex #6): `git remote get-url --push origin` must equal the expected `nateginn/artwebsite` URL; refuse on `pushurl`/`insteadOf` rewrites.
- **Validate the diff from the commit's own objects, not the working tree** (Codex #9): diff `<sha>` against its recorded parent/base SHA via `git diff-tree`, confirm exactly one changed path, and compare the blob content — do not call `resolve_edit_location()` against the mutable checkout as proof of what the commit contains.
- **Hard-refuse if branch protection is not verifiably in place** (Codex R2 #2 — resolving Round 1's open "warn or refuse?" question in favour of refuse). Before pushing, query the branch-protection API and confirm: PR required, ≥1 approving review required, force-push blocked, deletion blocked, no bypass actors. Any missing element, or any error querying it, fails closed. The control being load-bearing *and* unverified was the gap.
- **Refuse if `deploy.yml` at the base commit no longer has the expected trigger shape** (Codex R1 #1) — stale-baseline detection only. Stated precisely (Codex R2 #11): this checks the workflow *at the published commit* and cannot bind what happens between publication and merge. Workflow changes are governed by the required-review gate on the protected branch, not by this check.
- Hardcoded refusal if the resolved destination ref is `main`/`master`.
PR creation extends `github_compare.py` with `create_pull_request()` (same `urllib` + Bearer pattern; no `gh` dependency). Token via `credentials.py` alias `art-github-token`, stored by Nate himself, and per Phase 0a belonging to the **separate automation identity**. **Token caveat, stated plainly:** no PAT scope prevents pushing `main` — fine-grained "Contents: write" includes it, and `Pull requests: write` may permit merging. Identity separation plus required review, not token scoping, is what stops it. Records `pushed_branch`, `pr_url`, `pr_number`.

**Phase 7 — Local path.** Extend `.claude/skills/review-pending/SKILL.md`: list → draft copy → show **current vs proposed** → `AskUserQuestion` explicit decision → `--approve` → `apply.py` → offer `publish.py` → report PR link.

**Phase 8 `[HISTORICAL — PATH A]` — Remote path.** _The "do not merge" instruction it documents is meaningless under Path B, which has no PR to merge. A remote-dispatch document for Path B would be a different document._ No code change in `dual-agent-core`. Add `REMOTE-APPROVAL.md` documenting the dispatch block template (`Scope: D:/Dev/Looping _agency/`, steps = draft → approve → apply → publish → report PR URL → **do not merge**). Optional hardening only, explicitly *not* claimed as a control: add `main`-push denies to `dual-agent-core/.claude/settings.json` (Codex #3 is right that these are bypassable; they raise the bar against accident, not intent).

**Phase 9 — Briefing detail.** Extend `MMC/collector/sources/looping.py`'s existing pending-proposal loop to carry `action_type`, `target.page`, `target.keyword`, `rationale`, `baseline_position`, and when present `implementation.previous_value`/`new_value` and `pr_url`. Bounded fields, no raw blobs. MMC stays Tier 0 and still never writes `TASK_COORDINATION.md`.

**Sequencing.** 0a (branch protection) is a hard gate on everything remote. Then 1 (fix the evaluator) → 2 → 3 → 4 → 5 → 7, proving one real change locally end-to-end. Then 0b/0c → 6 → 8 → 9.

### Autonomous merge and deployment safety `[HISTORICAL — PATH A]` (supersedes manual-merge wording in Phases 6-8)

_Superseded in turn by "SELECTED OPERATING MODEL" at the top of this file. The deployment-safety half of this section — record the pre-deploy SHA, bounded smoke tests, automatic redeploy of the previous known-good SHA, persist both SHAs and all results, notify, escalate if rollback fails — carries over to Path B essentially unchanged and is what `deploy_verify.py`/`deploy_health.py` implement. The merge half does not carry over: there is no PR._

- After the recorded proposal approval, `publish.py` may push the validated immutable SHA to `seo/<id>`, open the PR, and request auto-merge. No second Nate action is required.
- The PR body must include the proposal ID, approval event, implementation SHA, exact changed path/value, validation results, and rollback target. Any mismatch, stale base, extra changed file, or missing approval refuses auto-merge.
- Add a required pull-request workflow for Django tests, the narrow diff/SEO validation, and merge-readiness checks. Do not require a human PR review in the normal path.
- Extend `deploy.yml` to record the pre-deployment SHA, deploy the new SHA, run bounded production smoke tests covering DNS/TLS, expected status, canonical/robots sanity, and representative lead-intent URLs, with retries and a stabilization window.
- On a failed smoke test, redeploy the previous known-good SHA, rerun the health check, persist both SHAs and all results, notify the reporting path, and open an automated revert PR. If rollback or revert validation fails, stop and escalate to Nate.
- The health check confirms availability and the configured contract; it does not prove SEO quality or business impact. Those remain the SEO loop's measured outcomes.

## Key decisions & tradeoffs

1. **Server-side branch protection is the gate; local guards are not.** Reversed from Round 0. Costs nothing, survives a fully-compromised local agent, and makes the Telegram path's permissive surface tolerable instead of load-bearing.
2. **Fix the evaluator before building on it.** The loop's entire value is measuring whether a change worked; shipping a pipeline that feeds a scorer which can report a false win would manufacture confident wrong conclusions.
3. **Push an immutable SHA to a fully-qualified ref**, not a branch name — closes refspec ambiguity and TOCTOU in one move.
4. **One shared mutation lock, not compare-and-swap.** Reversed in Round 2: CAS would have fought `run_loop.py`'s load-once/mutate-many/write-at-end pattern and wouldn't have protected the git repo at all. One lock covers both, with no schema migration.
5. **The automation must not be Nate.** Identity separation is what makes "required approving review" enforceable instead of self-defeating — the single most important structural fix of Round 2.
6. **Amend R6 honestly rather than quietly.** The amendment records that "only main deploys" is a fact with an expiry date, governed by required review on the protected branch (which now covers `deploy.yml` itself) and backstopped by a stale-baseline check.
7. **Rejected as over-engineered for a solo operator, but conditionally** (Codex R2 #4 sharpened this): cryptographic attestation of the reviewed workflow, and a capability-limited publish service with approvals bound to a proposal hash. **The rejection is valid only once Phase 0's controls are verified.** Without required human review and no-bypass, the remote session could call GitHub's merge API directly and the rejection would be indefensible. So Phase 0c's test matrix is not paperwork — it is the precondition that makes the rest of this plan's risk posture legitimate. Revisit for any third-party client.
7. **One Telegram instruction chains approve→apply→push→PR.** Every step is non-deploying and reversible; step-by-step confirmation is phone-hostile.

## Risks / open questions

- **Branch protection is load-bearing and lives outside version control.** If it is ever disabled, the model silently degrades. Resolved in Round 2: `publish.py` **hard-refuses** rather than warning. Residual risk: the check reads GitHub's own API, so a compromised token could in principle be pointed at a lying endpoint — out of scope for this threat model.
- **Repo-settings drift is invisible to git.** Nothing version-controls branch protection. Consider a periodic assertion (the existing watchdog is a natural home) so a disabled protection surfaces in the daily briefing rather than at the next publish attempt.
- **`apply.py` has never run against real `artwebsite`** — only with an injected fake `git_runner`. First live run is genuine risk; `artwebsite` also currently has an uncommitted `sitemap.xml` modification (worktree-from-`main` should isolate it, unexercised).
- **A `seo/*` branch accumulating on `origin`** — no cleanup path defined for rejected/abandoned PRs.
- **`_pick_new_actions` quality is unaddressed.** Candidates are positions 3–20 ranked by clicks, and most `art` candidates have 0 clicks, so ordering is near-arbitrary — it decides what gets proposed at all. Out of scope here, but it caps the pipeline's usefulness.
- **The observation window never starts until Nate merges.** Expected, but it means a forgotten PR silently stalls a proposal in `implemented` indefinitely (the existing "stuck implemented ≥3 cycles" flag partially covers this).

## Verification

The verification sequence below is read with the autonomy revision: the pipeline, not Nate, performs the PR merge after the recorded proposal approval. Verification must include auto-merge, deployment smoke testing, automatic rollback, incident reporting, and automated revert behavior.

Staged, because Codex R1 #12 correctly noted a test that stops at PR creation proves almost nothing about the loop closing.

1. **Phase 0 controls, with the real credential** (Codex R2 #3, #12) — the fail-closed matrix in 0c: direct push to `main`, force push, PR-merge-without-approval, branch deletion; plus the one success case, merge after Nate's approval. Effective permissions, not nominal scope.
2. **Unit/self-tests** — `--verify` on each new or changed module (`draft_copy`, `publish`, `artwebsite_seo`, `github_compare`), plus the full suite (`tools/tests/phase1_exit_criteria.py`, currently 73/73) and `spec_validate.py`.
3. **Evaluator regressions (Phase 1)** — missing row → `not-evaluable`; wrong-keyword row → not used; ambiguous duplicate → refused; correct row → evaluated; and a `not-evaluable` result actually appearing in `report.md`.
4. **Publish guards, offline** — using injected fake `git_runner`/`requester` (the pattern already used throughout): refuses on `main`/`master` destination; on a diff touching an extra file; on `new_value` mismatch; on drifted base; on absent branch protection; on a rewritten `remote.origin.pushurl`; on a `deploy.yml` trigger shape change.
5. **Concurrency** — `apply.py` transitions a proposal while a `run_loop.py` run holds the lock; confirm the run's end-of-run write does not revert it. Plus: confirm `run_loop.py` does not deadlock against itself now that `_promote_live_implementations()` no longer takes its own lock, and that `review_pending.py` blocks (rather than clobbers) during a run.
6. **One real change, end-to-end** — draft → approve → apply → publish on a single live proposal. Confirm: the `seo/*` branch exists on GitHub, the PR renders the expected one-line diff, **and the Actions tab shows no new workflow run** (the proof the gate holds).
7. **Then close the loop** — Nate merges the PR; confirm the next `run_loop.py art seo` promotes `implemented → applied` via `_promote_live_implementations`; then confirm at observation-window expiry that the proposal reaches `verified` or `breached` **on a real measured position**, and that a deliberately mismatched keyword yields `not-evaluable` rather than a false win.

## Out of scope

- `_pick_new_actions` candidate-selection logic and keyword/position heuristics.
- `internal-link-addition` auto-implementation.
- Task Scheduler registration (`PHASE3-SCHEDULING.md`, deliberately human-run).
- AEO connector; hyperlocal zip-radius rank.
- MMC's briefing *pipeline* (only its Looping collector's field set changes).
- Rewriting Hermes/DAC's dispatch or permission model beyond the optional deny-list addition.
- Hardening the non-pipeline production paths themselves (SSH, Actions re-run, secrets) — documented in 0c, not fixed here.

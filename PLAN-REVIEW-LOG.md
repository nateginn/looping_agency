# Plan Review Log: SEO monitoring & reporting expansion for `art`

Act 1 (grill) complete — plan locked with the user 2026-07-23 (local). MAX_ROUNDS=5, raised to 6 by Nate after Round 5 to allow one confirmatory pass on the Local Pack vs. organic rank fix. PLAN_FILE=PLAN.md.

Scope: geo/local rank tracking, backlink monitoring, and free technical/on-page health checks for the `art`/`seo` loop, reported through the loop's existing artifacts so MMC's briefing picks it up with no MMC-side changes. Full plan in `PLAN.md` (current top section).

## Round 1 — Codex (VERDICT: REVISE)

Reviewer: codex-cli 0.144.1, model gpt-5.4 (medium reasoning), read-only sandbox. Thread `019f8da1-cb09-79d3-8a3b-e696c7302cf6`.

1. **Blocker** — hyperlocal (zip/radius) rank tracking may be unsupported by the wired DataForSEO SERP endpoint; the plan treated this as an implementation detail rather than a gating unknown. Fix: capability spike first, city/state fallback if unsupported.
2. **Blocker** — "technical-only" runs weren't specified to skip `_evaluate_prior_experiments`/`_pick_new_actions`, which assume a GSC-shaped metrics payload; would break on a technical-only payload rather than just no-op. Fix: technical-only mode explicitly observe-only, skips both functions.
3. **High** — `spec_validate.py`'s `KNOWN_INPUTS` and `run_loop.py`'s `_fetch_metrics` dispatch are two separate hardcoded lists that can drift; adding to one without the other lets a spec validate and then fail at run time. Fix: single connector registry driving both.
4. **High** — `project.md`'s "Priority reference pages" is explicitly documentation-only prose today, not machine-readable; CWV/URL-inspection checks need real structured input. Fix: `priority_pages` moves into validated `spec.md` frontmatter.
5. **High** — monthly footer scrape auto-rewriting `project.md` directly introduces scrape-driven config drift, dirty-git side effects, and races with human edits to a file otherwise treated as source-of-truth. Fix: scrape writes a generated file + flags the diff in `report.md`; human confirms before `project.md` changes.
6. **Medium** — `run.json`'s `tool_calls` bloating with full connector payloads breaks the existing lightweight-metadata/detailed-`snapshot.json` split, risking oversized artifacts and noisier MMC summarization. Fix: `run.json` stays call-metadata-only; full data stays in `snapshot.json` only.
7. **Medium** — folding sitemaps/URL-inspection into `gsc.py`'s existing `pull_metrics` undersells it as a small additive change when it's really a different endpoint family/auth/quota regime. Fix: separate functions, separate tests.
8. **Medium** — "zero changes inside MMC" assumed MMC's summarizer reasons correctly about new sections/mixed freshness, unverified. Fix: explicit post-implementation check against a real artifact, still without modifying MMC.

### Claude's response

All 8 accepted, none rejected — every finding pointed at a real gap or an accurate technical claim about this codebase (verified `_fetch_metrics`'s hardcoded dispatch, `project.md`'s documentation-only priority-pages section, and `run.json`/`snapshot.json`'s existing lightweight/detailed split independently before agreeing). Revised `PLAN.md`: added Approach step 0 (capability spike + city/state fallback, gates the rest of the plan), step 2 now specifies a shared connector registry plus step 2a moving priority pages into `spec.md`, step 3 rewritten so the footer scrape writes a generated file and flags a diff rather than auto-editing `project.md`, step 4 now explicitly bypasses `_evaluate_prior_experiments`/`_pick_new_actions` in technical-only mode, step 6 corrected to keep `run.json` lightweight, step 1 splits new GSC functions out from `pull_metrics`, and a new step 8 verifies the MMC-delivery assumption post-implementation. Key decisions and Risks sections updated to match.

## Round 2 — Codex (VERDICT: REVISE)

Reviewer: same session (thread `019f8da1-cb09-79d3-8a3b-e696c7302cf6`), resumed read-only. Confirmed all 8 Round 1 findings addressed; 4 new findings:

1. **Blocker** — Round 1's fix for priority-pages prose wasn't applied consistently: `locations` was still specified as a `## Locations` markdown section in `project.md`, the exact same brittle-authoritative-prose problem just rejected for priority pages, and this repo has no parser for structured markdown-body sections. Fix: `locations` into validated `spec.md` frontmatter, same as `priority_pages`.
2. **High** — `additional_schedules[*].inputs` subsets weren't specified as individually schema-validated against the connector registry/required aliases; today's validator only checks top-level `spec.inputs` globally. Fix: validate each schedule entry as its own fully resolvable runnable mode.
3. **Medium** — `attention_thresholds` as specified (`>5` position drop, "any decrease" in referring domains) fires on routine single-run noise in inherently noisy data (backlink counts, local SERPs). Fix: require sustained/material deltas, reusing `guardrail_metrics`'s existing `consecutive_runs` field.
4. **Medium** — no versioned/sectioned schema for `snapshot.json`'s merged connector output; risk of ad hoc connector-specific branches growing over time. Fix: define `search_analytics`/`local_rank`/`backlinks`/`technical_health` top-level sections up front, each with its own `as_of`.

### Claude's response

All 4 accepted, none rejected. Revised `PLAN.md`: step 3 now puts `locations` in `spec.md` frontmatter (matching 2a's `priority_pages` treatment) with the monthly scrape writing to a generated file and flagging a diff, never auto-editing `spec.md` directly; step 4 adds explicit per-`additional_schedules`-entry validation against the connector registry; step 5's `attention_thresholds` now specify `consecutive_runs`-gated sustained deltas (CWV status-crossing kept as a same-run flag since it's a discrete state change, not a noisy metric); step 6 defines the sectioned `snapshot.json` schema (`search_analytics`/`local_rank`/`backlinks`/`technical_health`, each with its own `as_of`). Key decisions updated to match.

## Round 3 — Codex (VERDICT: REVISE)

Reviewer: same session, resumed read-only. Confirmed Round 2's 4 findings addressed; 1 new finding:

1. **Medium** — `attention_thresholds` claimed to "reuse the existing `guardrail_metrics` shape" while also defining a CWV rule that's categorical (not threshold-like) and a referring-domain rule with an unresolved `>= N` placeholder — a real schema mismatch, since `spec_validate.py`'s guardrail shape is strictly numeric comparator/threshold/consecutive-runs. Fix: either a separate schema with explicit rule types and concrete defaults, or fully specify the encoding into the existing structure.

### Claude's response

Accepted. Verified `_validate_guardrail_metric` directly (`tools/spec_validate.py:39-51`) — confirmed it's numeric-only, no room for a categorical transition. Revised `PLAN.md` step 5: `attention_thresholds` is now its own explicit schema (distinct from `guardrail_metrics`), each entry typed by `kind` — `numeric_delta` (rank position, referring domains, both with concrete defaults and `consecutive_runs`) and `enum_transition` (CWV status, no `consecutive_runs`). Named the exact per-`kind` validation rules `spec_validate.py` would need to enforce.

## Round 4 — Codex (VERDICT: APPROVED)

Reviewer: same session, resumed read-only. Confirmed Round 3's finding addressed (typed `attention_thresholds` schema, concrete referring-domain default). No new material blocker found — remaining unknowns (DataForSEO capability, MMC delivery) are explicitly scoped as implementation-time verification steps, not hidden assumptions. Converged after 4 rounds (MAX_ROUNDS=5).

## Post-approval update — new information, not a new review round's finding

Nate supplied DataForSEO's actual API catalog after Round 4 approval. This resolved the plan's single biggest named risk (step 0's hyperlocal-tracking unknown) with a concrete product name rather than changing the plan's structure: **Business Data API → Google My Business** is the named candidate for zip/radius rank tracking (vs. the already-wired SERP API's city/state-only `location_code`). Also surfaced: `OnPage API → Lighthouse` as a noted-but-not-adopted CWV alternative to free Google PSI, and `AI Optimization API → LLM Mentions` as a separate, out-of-scope discovery that contradicts a standing "no AEO data source exists" note elsewhere in the repo (`art/spec.md`, `HANDOFF.md`) — flagged in Out of Scope for a future conversation, not pulled into this plan. Revised `PLAN.md` step 0, step 1, the DataForSEO Risks bullet, and Out of Scope accordingly. Sending Round 5 to Codex as a confirmatory check since step 0 and step 1 both changed wording (though not structure).

## Round 5 — Codex (VERDICT: REVISE)

Reviewer: same session, resumed read-only. New finding on the post-approval update:

1. **Blocker** — the plan named Business Data API → Google My Business as the hyperlocal rank product without confirming it measures the same thing as the existing organic-page rank tracking. Google My Business rank is very likely Local Pack/Maps position (business-listing rank among nearby competitors, not tied to a URL) — a different metric family from organic 1-100 page rank. If step 0 confirms this, the plan needs to model it as a distinct schema/threshold, not silently reuse `rank_position`/`baseline_position`, or thresholds and reporting will compare unlike signals. Everything else in the update (capability-spike framing, PSI-vs-Lighthouse scoping, AEO correctly pushed out of scope) was confirmed sound.

### Claude's response

Accepted, no pushback — the distinction (Local Pack listing rank vs. organic page rank) is real and I hadn't considered it when first naming the product. Revised `PLAN.md`: step 0 gained an explicit (c) — step 0 must determine which metric Google My Business actually measures, not just whether it's on-plan; step 5's `numeric_delta` rank rule now names its metric as "resolved by step 0(c)," not a generic `rank_position`; step 6's `local_rank` snapshot section is now explicitly not assumed to share `search_analytics`'s shape (no `page` field if it's Local Pack rank) and gets its own `report.md` presentation, kept structurally distinct from organic position data.

**MAX_ROUNDS=5 reached.** Per the skill's hard rule, the loop would otherwise terminate here rather than sending a 6th round — this was not a disagreement-driven deadlock (Claude fully agreed with and incorporated Round 5's finding), just the round budget running out before a fresh Codex pass could re-confirm this specific fix. Flagged honestly to Nate rather than presenting it as freshly re-approved. Nate raised the cap to 6 for one confirmatory round.

## Round 6 — Codex (VERDICT: APPROVED)

Reviewer: same session, resumed read-only, cap raised to 6 by Nate. Confirmed the Local Pack vs. organic rank fix resolves the Round 5 finding: step 0's (c) properly gates implementation on determining which metric it is and requires separate modeling if it's Local Pack rank; step 5's threshold defers the concrete metric name to that outcome instead of hardcoding a shared `rank_position`; step 6's `local_rank` snapshot section is explicitly decoupled from `search_analytics`'s schema. No new material blocker. **Converged after 6 rounds.**

---

# Archived — prior review logs (Phase 1 combined plan, Phase 3 auto-implement plan)
_Kept below for reference. The Round 0-N content that follows predates this plan and reviewed different, now-shipped work._

# Plan Review Log: Loop Agency combined plan

Started 2026-07-15 (local). MAX_ROUNDS=2. PLAN_FILE=AgentColabPlan.md. Reviewer: Codex CLI 0.144.1, model gpt-5.4 (medium reasoning), read-only sandbox.

## Round 0 — pre-loop history

- Claude drafted `looping_agency_plan.md` (project-centric workspace, 3 loop templates, Claude Code runtime).
- Codex independently drafted `LOOP_AGENCY_PLAN.md` (loop-centric, SEO-first safety sequencing).
- Codex reviewed and critiqued (via user relay): too implementation-shaped too early; metrics too singular (wants primary + guardrail + failure threshold); separate loop definition model vs operating procedure vs runtime; define what Loop Agency is *for* first.
- Claude merged both plans + the critique into `AgentColabPlan.md` (Round 0 draft), adding the coder/reviewer role split requested by the user.

## Round 1 — Codex (VERDICT: REVISE)

Material problems remain in [AgentColabPlan.md](/D:/Dev/Looping%20_agency/AgentColabPlan.md).

1. `AgentColabPlan.md:16,40,50,56` treats “credential refs” and `--verify` as enough, but never defines secret storage, redaction, rotation, or least-privilege scopes; that is how API keys end up in `project.md`, logs, or process args. Fix: require OS keychain/secret manager storage, store only opaque secret IDs in repo files, redact all tool output, and document minimum scopes per connector.

2. `AgentColabPlan.md:17,45-47,55-58,85` has no concurrency control, so two scheduled runs of the same loop can both append `memory.md`, write competing snapshots, and generate contradictory proposals. Fix: add a per-project/per-loop run lock plus idempotent run IDs and refuse to start when another run is active.

3. `AgentColabPlan.md:17,56` says “evaluate last run’s experiments” and then make new actions, but never defines attribution windows or a cooldown, so the loop can mis-credit noisy weekly movement to its last edit and thrash. Fix: add per-action observation windows, minimum sample sizes, and a “no new change until prior change exits evaluation window” rule.

4. `AgentColabPlan.md:17,26,56` only “auto-flags revert proposal” for SEO losses; if a bad change ships, the plan has no mandatory halt condition before the next run piles on more edits. Fix: require automatic pause-on-breach and block new proposals until a human explicitly resolves the failed experiment.

5. `AgentColabPlan.md:28,58` uses a monthly spend cap as the main ads guardrail, which is too coarse to stop fast overspend or learning-phase blowups. Fix: add daily budget ceilings, per-campaign hard stops, and a “propose-only” mode until spend controls are verified live.

6. `AgentColabPlan.md:17,73` says humans approve anything public or paid, but the exact approval state machine is missing: who approves PR creation, who approves merge, who approves ad changes, and what happens on timeout. Fix: define explicit states `draft -> reviewed -> approved -> applied -> verified` with one required human action per irreversible transition.

7. `AgentColabPlan.md:17,46,72-73` assumes “pending proposals” are safe, but PR branches and draft ads are still side effects that may trigger CI, previews, notifications, or external syncs. Fix: classify branch creation, preview deployment, and ad-draft writes as side effects and gate them separately from fully local report generation.

8. `AgentColabPlan.md:77` says schema enforcement is mitigated by a spec-lint step, but linting after the runner loads the spec is backward and too weak for safety-critical fields. Fix: define a versioned machine-validated schema with required enums/ranges before any run begins.

9. `AgentColabPlan.md:44-47` uses mutable markdown files (`memory.md`, reports, pending) as the system of record, which is brittle for auditability and prone to merge conflicts. Fix: make each run write immutable timestamped artifacts and derive human-readable summaries from those append-only records.

10. `AgentColabPlan.md:17,45,47` has no observability contract beyond “human-readable report,” so debugging a failed or partial run will be guesswork. Fix: require structured run logs with run ID, start/end time, tool calls, credential alias used, decision rationale, and final status.

11. `AgentColabPlan.md:55-56` claims Phase 1 proves the framework with `_demo`, but mock metrics do not validate the riskiest parts: auth flows, API quotas, partial failures, and retries. Fix: split verification into offline dry-run plus a live “connectors-only” smoke test against a sandbox or lowest-risk real account.

12. `AgentColabPlan.md:18,85` treats local Task Scheduler as the default safe option, but scheduled CLI runs on a desktop inherit unstable user sessions, local env drift, and invisible failures. Fix: specify a dedicated service account, fixed working directory, captured stdout/stderr, retry policy, and healthcheck alerting for scheduled runs.

13. `AgentColabPlan.md:18,78` justifies Node by avoiding a machine-specific Python TLS issue, which is a brittle architectural reason and likely wrong once this moves machines. Fix: state connector/runtime selection criteria independent of one workstation and document the TLS workaround as an environment note, not a core design principle.

14. `AgentColabPlan.md:26-28,86` uses hand-wavy guardrails like “readability/intent check” and “brand-voice compliance,” which are not measurable enough to automate against. Fix: replace them with explicit review checklists or scored rubrics and require human approval whenever a guardrail is qualitative.

15. `AgentColabPlan.md:55,79` still over-scaffolds by creating all three loop templates in Phase 1 before one live loop is proven, which increases prompt/tool surface area without reducing risk materially. Fix: implement only the SEO template end-to-end first and extract shared abstractions after the second validated run.

16. `AgentColabPlan.md:67-72` gives Claude full authority to “rebut” Codex findings and caps plan review at two iterations, which biases the process toward shipping unresolved risks rather than closing them. Fix: require unresolved high-severity findings to block phase completion until the human explicitly accepts the risk in writing.

17. `AgentColabPlan.md:68` claims Codex persistent sessions improve review continuity, but that is an implementation assumption about the reviewer tooling, not a dependable control. Fix: make the review process rely on explicit artifacts (`PLAN-REVIEW-LOG.md`, diff summaries, risk register), not reviewer memory.

18. `AgentColabPlan.md:40,48` is multi-tenant in folder structure only; it does not specify tenant isolation for repos, credentials, reports, or accidental cross-project context leakage. Fix: require per-project secret namespaces, per-project working directories, and a hard check that no run can read or write outside its project root and declared repo.

19. `AgentColabPlan.md:17` mentions a rollback path, but only repo PRs have an obvious rollback; social drafts, ad proposals, and external state changes do not. Fix: define rollback semantics per action type and forbid any action type that cannot be cleanly reversed or superseded.

20. `AgentColabPlan.md:72` says Codex reviews “the diff,” but for loop software the dangerous failures are often in runtime behavior, not static code shape. Fix: require review inputs to include representative run artifacts, failing-path simulations, and scheduler/config examples, not just source diffs.

21. `AgentColabPlan.md:60` sets milestone success at “one loop runs end-to-end locally,” which is too weak to prove the plan is safe enough for a real client project. Fix: make milestone exit criteria include lock behavior, redaction tests, failed-connector handling, and explicit approval-gate enforcement.

VERDICT: REVISE
### Claude's response (Round 1)

Accepted and incorporated (18 of 21): #1 secrets policy (opaque aliases, .env/Credential Manager, least-privilege scopes, tool-owned redaction); #2 per-loop run lock + run IDs; #3 observation windows, sample sizes, cooldown rule; #4 pause-on-breach state blocking new proposals; #5 daily ceilings, per-campaign hard stops, propose-only until spend controls verified; #6 approval state machine (draft→reviewed→approved→applied→verified, stale-proposal surfacing); #7 side-effect tiers 0/1/2; #8 versioned schema validated before run; #9+#10 append-only runs/<run-id>/ as system of record with structured run.json, memory.md demoted to derived view; #11 two-stage verification (offline dry-run + connectors-only live smoke test); #14 qualitative guardrails get scored rubrics and always require human approval; #17 artifact-based review (PLAN-REVIEW-LOG.md + new RISK-REGISTER.md), session continuity demoted to convenience; #18 per-project namespaces and run confinement to project root + declared repo; #19 rollback semantics declared per action type, irreversible actions forbidden or manual-approval-only; #20 phase reviews include run artifacts and failing-path simulations, not just diffs; #21 milestone-1 exit criteria expanded (lock refusal, redaction test, partial-failure handling, gate enforcement).

Partially accepted with reasons:
- #12 (scheduler hardening): fixed working dir, captured output, retry policy, and staleness/health surfacing adopted. Dedicated service account documented as optional — this is a solo-operator desktop, and the compensating control is staleness detection, not enterprise service isolation.
- #13 (Node justification): reframed as workstation-independent selection criteria; TLS issue demoted to an environment note. Runtime choice (Node) unchanged.
- #15 (over-scaffolding): compromise — the user explicitly directed that all three loops be part of the build. Phase 1 wires only SEO's tooling end-to-end; content and ads exist as draft specs (markdown only, no tools), with shared abstractions extracted after SEO's second validated run. This honors both the user's directive and the surface-area concern.
- #16 (rebuttal authority / 2-round cap): the 2-iteration cap is user-mandated and retained; mitigations adopted — RISK-REGISTER.md, unresolved high-severity findings block phase completion absent written human risk acceptance, and deadlocks go to the human as tie-breaker rather than shipping silently.

Plan revised in place (AgentColabPlan.md, Round 1 header). Sent back to the same Codex session for Round 2.

## Round 2 — Codex (VERDICT: REVISE, 5 residual findings)

A few material problems remain in [AgentColabPlan.md](/D:/Dev/Looping%20_agency/AgentColabPlan.md).

1. [AgentColabPlan.md:24,31,99-100](file:///D:/Dev/Looping%20_agency/AgentColabPlan.md) defines a lockfile but no stale-lock recovery rule, so a crashed run or reboot can wedge a loop indefinitely or tempt unsafe manual lock deletion. Fix: define canonical stale-lock detection and recovery (`PID not alive` or `age > max_run_duration` -> mark stale, archive lock, start new run).

2. [AgentColabPlan.md:35-43](file:///D:/Dev/Looping%20_agency/AgentColabPlan.md) still leaves the apply boundary ambiguous for Tier-1 actions: a PR branch or draft ad is a real external write, but the state machine says only `/review-pending` performs `applied` while Tier-1 is “allowed” by spec opt-in. Fix: explicitly map each Tier-1 action to a state transition and say whether branch/draft creation is `draft-generation` or `applied`, with the required human gate before it happens.

3. [AgentColabPlan.md:59-61,99-100](file:///D:/Dev/Looping%20_agency/AgentColabPlan.md) still permits plaintext per-project `.env` files as a normal secret store on a multi-client desktop, with no filesystem ACL requirement or preference order; that is weaker than the rest of the plan’s tenant-isolation posture. Fix: make Windows Credential Manager the default, relegate `.env` to an explicit fallback, and require restricted file permissions plus a startup check that rejects overly-broad ACLs.

4. [AgentColabPlan.md:99-101,126](file:///D:/Dev/Looping%20_agency/AgentColabPlan.md) treats “staleness surfaced in every report” as a compensating control, but if scheduling breaks hard enough that no report is produced, that control disappears exactly when needed. Fix: add an out-of-band watchdog (separate scheduled healthcheck or external monitor) that alerts when an expected run artifact is missing.

5. [AgentColabPlan.md:62,75,99](file:///D:/Dev/Looping%20_agency/AgentColabPlan.md) says runs may access only the declared repo, but it never says the path check is canonicalized; symlinks/junctions or path tricks can defeat naive prefix checks on Windows. Fix: require resolved absolute canonical paths for both project root and declared repo before enforcing read/write boundaries.

VERDICT: REVISE
### Claude's response (Round 2)

All 5 residual findings accepted and folded into AgentColabPlan.md:
1. Stale-lock recovery rule added (dead PID or age > max_run_duration -> archive lock, allow new run).
2. Tier-1 ambiguity closed: spec opt-in only makes Tier-1 proposable; the external write itself is the `applied` transition and always requires human approval via /review-pending.
3. Windows Credential Manager promoted to default secret store; .env demoted to fallback with restricted-ACL requirement and a startup ACL check that refuses overly-broad permissions.
4. Out-of-band watchdog added: an independent scheduled task alerts when an expected run artifact is missing for the cadence window.
5. Path boundary checks now require resolved absolute canonical paths (symlinks/junctions resolved).

## Outcome

MAX_ROUNDS=2 reached without a formal APPROVED. Codex's trajectory: 21 findings -> 5, all 5 subsequently incorporated. No substantive disagreement remains between the models; the only unresolved item is that Codex has not re-verified the final edits. Per protocol, the human is the tie-breaker and final sign-off on AgentColabPlan.md (Round 2, final).

## Round 3 negotiation — language-switch scoped review (2026-07-16)

**Context:** After Round 2 (final) above, `RISK-REGISTER.md` R8 recorded that the plan's Node.js runtime choice was never actually authorized by the human — they had told an earlier session to use Python, and the switch to Node (justified by a since-debunked AVG TLS-interception workaround) was never re-confirmed with them. The entire `tools/` directory was rewritten Node→Python, tested (32/32 checks), committed, and pushed. Per the human's explicit instruction, this round runs the plan back through Codex for exactly two rounds, **scoped only to what needs to change in `AgentColabPlan.md` as a direct result of the language switch** — not a re-litigation of the whole plan. `looping_agency_plan.md` and `LOOP_AGENCY_PLAN.md` (the two preliminary individual plans referenced in Round 0 above, superseded by this document) were deleted from the repo at the human's request as part of this same instruction.

Reviewer: Codex CLI 0.144.1, model gpt-5.4 (medium reasoning), read-only sandbox, thread `019f68ee-0633-7322-9615-07c14e56d52f`.

### Round 3 — Codex (VERDICT: REVISE)

1. [AgentColabPlan.md:18](/D:/Dev/Looping%20_agency/AgentColabPlan.md) and [AgentColabPlan.md:99](/D:/Dev/Looping%20_agency/AgentColabPlan.md) switch the tool layer to Python, but the scheduling contract still pins only `claude` and never pins the Python interpreter/dependency environment, so a Task Scheduler run can succeed in launching Claude while silently using the wrong `python` or no repo deps at all. Fix: add one sentence requiring scheduled runs and skill/tool invocations to use a pinned workspace interpreter (e.g. `.venv/Scripts/python.exe`) or a checked wrapper that activates that venv before `claude -p`.
2. [AgentColabPlan.md:18](/D:/Dev/Looping%20_agency/AgentColabPlan.md) cites the old AVG/TLS issue as having a one-line Python fix of `pip install truststore`, but [RISK-REGISTER.md:14](/D:/Dev/Looping%20_agency/RISK-REGISTER.md) records the verified workaround as install *plus* `truststore.inject_into_ssl()`, so the historical note is technically imprecise. Fix: replace the parenthetical with the full verified workaround, or simply point to `RISK-REGISTER.md` R8.

VERDICT: REVISE

### Claude's response (Round 3)

Both findings accepted — both are concrete, in-scope (directly caused by the language switch, not a re-litigation of settled plan sections), and correct on inspection:

1. **Interpreter pinning.** Added a sentence to "Runtime/tooling layer" (dependencies pinned in `requirements.txt`, installed into `.venv/`, every invocation — scheduled or interactive — goes through `.venv/Scripts/python.exe`, never a bare `python`) and a matching sentence in "Scheduling & operations" making the same requirement explicit for the Task Scheduler action/wrapper, framing a silent interpreter fallback as a scheduling failure in its own right.
2. **TLS fix precision.** Verified against `RISK-REGISTER.md` R8's exact wording (`pip install truststore` + `truststore.inject_into_ssl()`) — the plan's parenthetical had dropped the `inject_into_ssl()` call, making it inaccurate. Replaced with a pointer to `RISK-REGISTER.md` R8 for the full note rather than restating (and risking re-drifting) the workaround inline.

### Round 4 — Codex (VERDICT: APPROVED)

Same session resumed (`019f68ee-0633-7322-9615-07c14e56d52f`), given the Round 3 revisions.

> No further material problems tied to the Node-to-Python switch. The Python runtime language is now internally consistent across runtime selection, dependency management, workspace structure, and scheduled execution, and the old TLS rationale has been demoted correctly to a risk-register reference instead of remaining as an architectural argument.

VERDICT: APPROVED

## Round 3–4 outcome

**Converged in 2 rounds, as scoped.** Both Codex findings (interpreter/venv pinning missing from the scheduling contract; imprecise TLS-fix parenthetical) were accepted and fixed; Codex re-reviewed the same session and returned a clean `APPROVED` with no further findings tied to the language switch. Unlike the original Round 1–2 negotiation (which hit the round cap without a formal approval and required the human as tie-breaker), this scoped negotiation reached a genuine mutual sign-off between Claude and Codex within the 2-round budget. `AgentColabPlan.md` now correctly and completely reflects Python as the implementation language throughout — runtime selection, dependency management (`.venv/` + `requirements.txt`), workspace structure, and scheduled execution are all internally consistent. Human final sign-off on this round is still the closing step, per the plan's own review protocol ("Human (you): approves the converged plan").

## Phase 2 Steps 1-3 code review (Codex, 2026-07-16)

**Context:** After Claude implemented Phase 2 Steps 0-3 (credential resolver, real-connector wiring, connectors-only smoke test; commits `f6ca0fd`..`37e44e1`), the human ran the work past Codex for an out-of-band code review before the Step 4 credential handoff. Four findings, all accepted and fixed the same session:

1. **GSC/DataForSEO page-shape mismatch.** GSC returns the `page` dimension as a full URL while the spec template collects DataForSEO `targets` as site paths, so the exact-tuple `(keyword, page)` merge would never attach `serp_position` once dataforseo was enabled. Fixed: `run_loop._page_path()` normalizes both sides of the merge key to the URL path component; the merge regression test now uses a full-URL GSC row against a path target.
2. **Unknown connector names passed validation.** A typo like `gscc` in `spec.inputs` survived `spec_validate.py` and only failed at run time as a connector error, below the "reject invalid specs before a run starts" bar. Fixed: `KNOWN_INPUTS` enum check (`mock`/`gsc`/`dataforseo` wired + the two draft-template placeholder names, which `run_loop.py` still refuses at run time); self-test covers the `gscc` case.
3. **GSC date window off by one.** GSC date ranges are endpoint-inclusive, so `start = end - window_days` queried 29 days for a 28-day window, in both `run_loop.py` and `smoke_test.py`. Fixed: `end - (window_days - 1)`; the dispatch test now asserts the request body spans exactly 28 inclusive days.
4. **ACL check matched principals by username suffix.** `audit_env_acl` treated any principal whose basename matched `%USERNAME%` as the current user, which would wrongly allow `OTHERDOMAIN\<same username>` on a domain-joined machine. Hardened rather than risk-accepted: comparison now uses the fully qualified account name (`whoami` output plus `USERDOMAIN\USERNAME`), unmatchable principals fall through to refusal, and the self-test asserts a same-username/other-domain principal is rejected.

Full suite after fixes: 49/49 exit-criteria checks + all module `--verify` self-tests green, no network.

## Project `art` intake review (Codex, 2026-07-16)

**Context:** After onboarding `projects/art/` (acceleratedrehabtherapy.com, Phase 2 Step 5), the user ran an independent Codex evaluation of the committed onboarding files against `HANDOFF.md`'s accepted plan. Three findings, all confirmed accurate and resolved the same session:

1. **Medium — missing initial target list.** `HANDOFF.md` Step 5 commits to collecting an "initial target keyword/page list" during intake, but `art`'s spec was onboarded with no seed list — a deliberate choice made during the intake interview (the user was offered discovery-mode as the recommended option and picked it). Risk: the first live report could surface branded/informational/low-value pages instead of the lead-intent pages `project.md`'s goals describe. Resolved by explicit user choice among three options (add a docs-only reference list, add code-enforced filtering, or document the deviation and keep pure discovery): the user chose the docs-only reference list and supplied the actual pages/queries. Added a `## Priority reference pages (first report)` section to `project.md`, explicitly labeled as a review aid for Nate — not a spec-level filter; `run_loop.py`'s proposal-picking logic is untouched and still ranks all GSC candidates by clicks.
2. **Medium — misleading rollback semantics.** `spec.md`'s three `allowed_actions` all declared `rollback: revert PR`, inherited unchanged from the generic template, despite `project.md` documenting that `D:\artwebsite` has no staging gate and no loop tooling may ever push to it — there is no PR-based apply path for this project at all. Fixed: rollback text rewritten to state plainly that no automated apply/PR path exists and any live change is made and reverted by Nate by hand; all three actions also marked `manual_approval_only: true` (a documentation/signal change — `tools/apply.py` doesn't read this field, and Tier-1 applies are already structurally blocked here by `approval_mode: propose-only`).
3. **Low — ambiguous repo-access wording.** `project.md`'s repo line still carried generic template language ("the runner may only read/write inside this repo and `projects/art/`") immediately next to a line saying tooling is never authorized to touch that repo. Reworded to state plainly that this loop's tooling does not currently read or write anything inside `D:\artwebsite` — it's declared only so the path-boundary check has a defined reference, and every artifact this loop produces lives under `projects/art/`.

Re-validated after the edits: `./.venv/Scripts/python.exe tools/spec_validate.py projects/art/loops/seo/spec.md` → `VALID`. No changes to `tools/*.py` or the exit-criteria suite — all three fixes are `art`-specific spec/doc edits.

## Phase 3 plan — auto-implement on approval + verification loop (codex-review skill, MAX_ROUNDS=3)

Started 2026-07-21 (session). Codex CLI 0.144.1, model gpt-5.4 (config default), reasoning effort medium. Plan under review: `PLAN.md` ("Phase 3 — auto-implement approved SEO proposals, close the verification loop").

## Round 1 — Codex

1. `applied_at` from GitHub commit timestamp treated as "went live" — wrong, commit time != push/deploy time; observation window starts too early, corrupting verified/breached results. Fix: use a true publish signal, not commit metadata.
2. `GET /commits/main` cannot prove ancestry of a specific commit, and explicitly fails on the squash/rebase flows the plan itself admits are likely. Fix: pick one concrete detection contract (exact-commit fast-forward via compare API, or content-diff matching) and reject unsupported merge styles explicitly.
3. New `implemented` status isn't threaded through the existing state machine (`review_pending.py` TRANSITIONS, `run_loop.py` cooldown/reporting) — will be invisible or mishandled by every consumer unless each is updated deliberately.
4. Cooldown only tracks `status == "applied"` targets — will keep generating new drafts against pages already `approved`/`implemented`, causing branch conflicts and contradictory recommendations. Fix: extend in-flight exclusion to `approved` and `implemented`.
5. No shared lock/atomic-write protocol between `apply.py`, `review_pending.py`, and `run_loop.py` on the same proposal JSON files — real race condition once a new concurrent writer (auto-implement) is added. Fix: require `tools/lib/lock.py`'s per-loop lock + atomic temp-file replace for every mutation path.
6. `manual_approval_only` semantics left unresolved despite being meaningful policy already (`spec_validate.py`, `art`'s spec uses it on every action) — a policy footgun if shipped ambiguous. Fix: make it a hard, unconditional apply-time refusal in `apply.py`.
7. Only title/meta have a concrete implementer design; `internal-link-addition` has no deterministic edit strategy or rollback story. Fix: scope Phase 3 auto-implement to title/meta only, keep internal-link-addition manual.
8. Repo-recon-based page→location mapping is unverified from Codex's sandbox (artwebsite is outside this workspace) and brittle by construction if wrong. Fix: implementer must validate the target file/block at runtime and fail closed if the page structure doesn't match expectations.

VERDICT: REVISE

### Claude's response

Accepted all 8 findings, no rejections. Revised `PLAN.md`:
- Approach step 2: added a hard, unconditional `manual_approval_only` refusal in `apply.py` (#6); narrowed auto-implement scope to `title-tag-rewrite`/`meta-description-rewrite` only, `internal-link-addition` stays permanently manual (#7); implementer now resolves page→edit-location at runtime with a fail-closed `implement-failed` status instead of trusting a static map (#8); added the shared-lock + atomic-write requirement across `apply.py`/`review_pending.py`/`run_loop.py` (#5).
- Approach step 3: replaced the vague "ancestor per the GitHub API" with a concrete contract — GitHub compare API (`compare/{sha}...main`, status `identical`/`ahead`), read-only (#2). `applied_at` now set from live-check detection time, never commit timestamp (#1). Documented the squash-merge/rebase blind spot explicitly as an accepted operational constraint (merge normally, don't squash) with a 3-cycle `stuck-implemented` surfacing fallback, rather than a silent gap.
- New Approach step 4 (new-status integration, #3): explicit per-consumer breakdown — `review_pending.py` TRANSITIONS unchanged (implemented not human-reachable), `run_loop.py` cooldown extended to `approved`+`implemented` targets (#4), verification gate stays `applied`-only, unchanged.
- Key decisions #2-#4, #6 firmed up from "leaning toward"/open to definite, matching the above.
- Risks trimmed to what's genuinely still open: the squash-merge blind spot (now an accepted, documented constraint, not unsolved), stale/conflicting branch reuse on implementer retry, and MMC collector readiness.

## Round 2 — Codex

1. `implement-failed` has no defined recovery path — `apply.py`'s gate only accepts `status == "approved"`, so a failed implementation becomes terminal without hand-editing JSON. Fix: explicit retry transition.
2. Git working-tree hygiene underspecified — writing commits directly into Nate's own `artwebsite` checkout risks capturing unrelated local changes or failing unpredictably if that tree is dirty/mid-merge/on another branch. Fix: clean-tree requirement or isolated worktree per branch.
3. The per-loop lock (`tools/lib/lock.py`) is tuned for scheduled-run mutexing, not for a longer apply operation touching a second repo — no defined recovery contract if apply crashes mid-implementation. Fix: record branch/base/partial-progress before editing, with a deterministic cleanup/resume rule.
4. `stuck-implemented` surfacing only at 3 run cycles means ~3 weeks of silence (weekly cadence) before a real detection failure becomes visible — too slow for an execution-state failure. Fix: immediate visibility on first missed detection, keep the 3-cycle threshold only for severity escalation.

VERDICT: REVISE

### Claude's response

Accepted all 4 findings, no rejections. Revised `PLAN.md`:
- Approach step 2: switched from "commit directly in D:\Dev\artwebsite" to an isolated `git worktree add ... -b seo/<id> main` per proposal (#2) — makes the working-tree-hygiene problem structurally impossible rather than just checked-for, never touches or requires anything of Nate's own checkout. Added `implement_attempt: {started_at, worktree_path, base_commit}` written via the locked atomic write *before* any git operation (#3), so a crash leaves a diagnosable trail and a deterministic stale-worktree cleanup + reset-to-`approved` rule. Added the missing recovery path (#1): `review_pending.py` gains `"retry": {"from": ["implement-failed"], "to": "approved"}`, human-gated re-queue after reading `implement_error`, not automatic.
- Approach step 3: `stuck-implemented` visibility split into two tiers per Round 2 (#4) — "Awaiting live confirmation" shown in `report.md` from cycle 1, distinctly-flagged `stuck-implemented` escalation still at cycle 3+ (severity, not first-visibility).
- Key decisions #7-10 added documenting the worktree choice, the write-before-git-op ordering, the human-gated retry, and the two-tier visibility timing.
- Risks trimmed: the stale-branch-reuse risk is resolved by the worktree approach + implement_attempt record, no longer open.

## Round 3 — Codex (final round, MAX_ROUNDS=3)

1. Crash window can destroy real work: if `apply.py` crashes after creating the commit but before writing `implemented_commit_sha`, the Round 2 recovery rule would treat it as stale and delete a valid implementation. Fix: inspect the branch for an actual commit before cleanup; promote to `implemented` if found instead of blindly resetting.
2. Reusing the run-cadence lock (`tools/lib/lock.py`, age-expiring against `max_run_duration_minutes`) for apply operations risks a scheduled `run_loop.py` "recovering" it as stale mid-implementation and mutating the same proposal set concurrently — reintroducing the race the lock was meant to prevent. Fix: dedicated apply-scoped lock, separate from the run lock.

VERDICT: REVISE

### Claude's response

Accepted both findings, no rejections. Revised `PLAN.md`:
- Approach step 2: crash recovery now inspects branch HEAD vs. `base_commit` before any cleanup — no advancement means safe to delete and reset to `approved`; any advancement means promote the existing commit to `implemented` instead of discarding it (#1).
- Concurrency/locking split into a dedicated apply-scoped lock (separate name/TTL from the run-cadence lock), acquired by `apply.py` for the full implement attempt and briefly by `run_loop.py`'s live-detection status writes — never the run lock itself (#2).
- Key decisions #11-12 added documenting both corrections.

## Round 1-3 outcome — MAX_ROUNDS reached, no formal APPROVED

**Not a deadlock in the disagreement sense** — every finding across all 3 rounds (8 + 4 + 2 = 14 total) was accepted by Claude, none disputed or rejected. The round cap was reached while Codex was still surfacing legitimate, decreasing-severity issues (broad architectural gaps in Round 1, narrower operational-recovery gaps in Round 2, subtle crash-ordering/lock-domain correctness in Round 3) — a trend toward convergence, not away from it, but not yet a clean `APPROVED`.

Per the skill's deadlock protocol: surfacing this state explicitly to the human rather than presenting it as converged. `PLAN.md` (Round 3) reflects all 14 accepted fixes. No code has been written — per the skill, code only follows human sign-off at this gate.

## Round 4 — Codex (extra round, human-approved extension past MAX_ROUNDS=3)

> No material flaws found in this revision. The two remaining accepted limitations are explicit rather than hidden: squash/rebase detection blindness is documented as an operational constraint, and MMC collector readiness is correctly treated as an external dependency rather than an implementation assumption. The state-machine recovery path, worktree isolation, crash promotion rule, and separate apply-scoped lock are now specified concretely enough to implement without inventing policy in code.
>
> Residual implementation risk is mostly test coverage, not plan soundness: the implementation should prove the new apply lock cannot race with the live-detection status write, and should include recovery tests for the "commit exists but JSON was not updated" path.

VERDICT: APPROVED

## Final outcome

**Converged in 4 rounds** (1 over the original MAX_ROUNDS=3 cap; human explicitly approved spending a 4th round rather than stopping at the Round 3 near-miss). 16 total findings across the argument, all accepted by Claude, zero disputed. `PLAN.md` is the final, mutually-approved Phase 3 plan. Codex flagged two items as test-coverage guidance for the build phase, not plan gaps: (1) a test proving the apply-scoped lock and the live-detection status write cannot race, (2) a recovery test for the "commit exists, JSON not yet updated" crash path. Human final sign-off on implementation approach (who builds it) is the next and last gate before any code is written.

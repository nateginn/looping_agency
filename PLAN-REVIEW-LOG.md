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

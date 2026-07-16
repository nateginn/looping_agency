# Agent Collab Plan: Loop Agency
_Round 2 (original scope) — revised by Claude after two Codex adversarial review rounds (21 findings → 5 residual, all incorporated; see PLAN-REVIEW-LOG.md). Codex's last formal verdict on that round was REVISE; the 5 residual fixes were folded in after the round cap — human sign-off was the tie-break._

_Round 3–4 (language-switch scoped, 2026-07-16) — the human corrected the runtime language from Node.js to Python (see RISK-REGISTER.md R8); this document was revised accordingly and re-run through 2 rounds of Codex adversarial review, scoped only to that change. **Codex's verdict on Round 4: APPROVED — a genuine mutual sign-off, not a round-cap tie-break.** Full transcript in PLAN-REVIEW-LOG.md._

## Goal

Loop Agency is a workspace for running durable business loops (build → verify against a real KPI → learn → repeat on a schedule) across **multiple client projects/businesses**. It is not a "run the whole company" super-agent: each loop has one narrow job, one primary KPI, guardrail metrics, a memory of prior experiments, and a human approval gate before anything public or paid happens. The first milestone is one loop (SEO) running end-to-end on one real project; the framework is designed so content/social and ads loops switch on next without restructuring.

## What Loop Agency is responsible for (and not)

**Responsible for:** operating scheduled improvement loops for onboarded projects — SEO first, then content/social, then ads; producing auditable proposals, experiment logs, and outcome reports; verifying its own past changes against metrics.

**Not responsible for (now):** product feedback loops (deferred), autonomous publishing or spending, building client products, or acting without a validated per-loop spec and approval mode.

## Three layers, decided separately

1. **Loop definition model** — a spec per loop instance: `objective`, `primary_metric`, `guardrail_metrics`, `failure_threshold`, `inputs`, `allowed_actions` (each with declared rollback semantics), `memory`, `schedule`, `stop_condition`, `approval_mode`. Format: markdown body + **versioned YAML frontmatter validated against a machine-checked schema (required enums/ranges) before any run starts** — a spec that fails validation refuses to run.
2. **Loop operating procedure** — the run contract (below) plus operating discipline: pre-run checklist, post-run summary, rollback path per action type, approval gates.
3. **Runtime/tooling layer** — Claude Code as the runner (skills, scheduling), thin Python scripts wrapping external APIs. Runtime selection criteria: single-language toolchain, no compile step, first-class on the schedulers we use, credentials injectable via env. **Python, per explicit user instruction — not a default to be silently overridden by environment quirks.** (An earlier revision of this document picked Node instead, citing this workstation's AVG TLS-interception issue as justification; that justification didn't hold up, and the choice was never actually authorized by the user. Corrected 2026-07-16 — see `RISK-REGISTER.md` R8 for the environment-specific TLS note and the full verified workaround.) Dependencies are pinned in `requirements.txt` and installed into a project-local virtual environment at `.venv/` — every tool invocation, scheduled or interactive, runs through `.venv/Scripts/python.exe`, never a bare `python` on PATH (see `CLAUDE.md` "Environment setup"). Specs and instructions stay plain markdown so the runtime is swappable.

## The run contract

Every run, identical for every loop:

1. **Acquire the per-loop run lock** (lockfile with run ID + PID + start time in the loop folder). If a lock is live, refuse to start and log the refusal. **Stale-lock recovery:** a lock whose PID is not alive or whose age exceeds the spec's `max_run_duration` is marked stale, archived into `runs/` for audit, and a new run may start — no manual lock deletion. Every run gets a unique timestamped run ID.
2. Validate spec against schema; load memory and the experiment register.
3. Pull current metrics via tools → write an **immutable, timestamped snapshot** under `runs/<run-id>/`.
4. **Evaluate prior experiments** — but only those whose observation window has elapsed and whose sample size is sufficient (both declared per action type in the spec). Keep winners; a guardrail/threshold breach **halts the loop** (state: `paused-breach`) and blocks all new proposals until a human resolves the failed experiment.
5. **Cooldown rule:** no new change to a page/campaign/asset that still has a change inside its evaluation window.
6. Pick 1–3 highest-leverage actions from `allowed_actions` only; write proposals to `pending/`.
7. Emit a structured run log (`runs/<run-id>/run.json`: run ID, start/end, tool calls, credential alias used, decisions + rationale, final status) **and** a human-readable report derived from it.
8. Append a summary line to `memory.md` (derived view — the append-only `runs/` artifacts are the system of record). Release the lock.

**Approval state machine** (one required human action per irreversible transition):

`draft → reviewed → approved → applied → verified`

- Loop may produce `draft`. Human review moves it to `reviewed/approved` (or `rejected`). Only `/review-pending` with explicit human approval performs `applied`. The *next* run's metrics evaluation moves it to `verified` (or breach → revert proposal).
- Proposals with no decision after 2 run cycles are surfaced as stale in every report; the loop never auto-applies them.

**Side-effect tiers** (gated separately):
- **Tier 0 — local only** (reports, snapshots, pending files): loop may do freely.
- **Tier 1 — visible side effects** (creating a PR branch — may trigger CI/previews/notifications; creating a draft ad in the platform): spec opt-in only makes these *proposable*. Executing a Tier-1 external write **is the `applied` transition** and always requires prior human approval via `/review-pending` — the loop itself never creates a branch or platform draft. Default is Tier 0 proposals only.
- **Tier 2 — public/paid** (merge, publish, spend change): human-only, always. **Any `git push` to a repo that auto-deploys on push (no staging gate) is Tier 2 regardless of branch or content** — e.g. the user's website repo (separate from this workspace, lives under `Dev/`) ships to production the instant anything lands, so it is never a loop's Tier-1 `applied` target and is out of scope entirely until/unless explicitly onboarded as a project (see RISK-REGISTER.md R6).

**Rollback semantics per action type:** every entry in `allowed_actions` declares how it reverts (repo change → revert PR; ad budget → restore prior value, recorded pre-change; social/content post → delete/supersede procedure). An action type with no clean reversal path is either forbidden or marked `manual-approval-only` with the residual risk stated in the spec.

## Metrics model

Every loop declares a primary metric, guardrail metric(s), and a failure threshold. **Qualitative guardrails are never auto-judged**: they get a scored checklist/rubric in the template, and any proposal touching them requires human approval regardless of tier.

| Loop | Primary metric | Guardrail metric(s) | Failure threshold (example) |
|------|---------------|---------------------|------------------------------|
| SEO | GSC position/clicks for target keywords | Scored content-quality rubric on diffs (human-approved); no drop in currently-ranking pages | Tracked keyword drops >5 positions post-change → pause-on-breach + revert proposal |
| Content/Social | Impressions/engagement per post | Brand-voice rubric (human-approved); cadence within plan | Engagement 50% below trailing average for 3 posts → pause & report |
| Ads | Cost-per-result / ROAS | **Daily** budget ceiling + monthly cap + per-campaign hard stop; frequency/fatigue limits | Any ceiling exceeded or ROAS < floor for N days → halt loop, alert. Loop stays **propose-only until spend controls are verified live**. |

## Credentials & tenant isolation

- **Windows Credential Manager is the default secret store**; per-project `.env` files outside git are an explicit fallback only, and when used must carry restricted ACLs (current user only) — the runner performs a startup ACL check and refuses to run against an `.env` readable by other accounts. **Repo files store only opaque credential aliases** (e.g. `gsc: acme-gsc-readonly`), never values.
- Least-privilege scopes documented per connector (GSC: restricted read-only user; DataForSEO: read; GitHub: repo-scoped PAT for the one blog repo; ads: read-only until Phase 4 gate).
- All tool output is redacted before it reaches logs/reports (tools own redaction, not the model).
- **Per-project namespace:** a run may only read/write inside `projects/<slug>/` and the repo declared in its `project.md`; boundary checks compare **resolved absolute canonical paths** (symlinks/junctions resolved) for both project root and declared repo — no naive prefix matching. The runner works from the project directory and loads no cross-project context.

## Workspace structure

```
Looping _agency/
├── CLAUDE.md                       # Run contract + operating discipline
├── AgentColabPlan.md               # This plan
├── RISK-REGISTER.md                # Open findings + human risk acceptances
├── templates/
│   ├── project-intake.md
│   └── loops/{seo,content-social,ads}/spec.md + instructions.md
├── projects/<slug>/
│   ├── project.md                  # Domain, repo, goals, caps, credential aliases
│   └── loops/<loop>/
│       ├── spec.md                 # Validated loop definition
│       ├── instructions.md
│       ├── memory.md               # Derived summary view
│       ├── runs/<run-id>/          # Append-only: snapshot, run.json, report.md
│       └── pending/                # Proposals + state (draft/reviewed/approved/...)
├── tools/                          # Python: gsc.py, dataforseo.py, snapshot.py, spec_validate.py (+ ads/social connectors in later phases); each has --verify
├── .claude/skills/                 # /intake-project, /run-loop, /review-pending
└── .env.example
```

## Sequencing

1. **Phase 1 — Framework + SEO, fully:** scaffold workspace; spec schema + validator; run contract; lock/run-ID/redaction plumbing; **SEO template wired end-to-end**. Content and ads exist as *draft specs only* (markdown, no tools wired) so their shape informs the shared abstractions — which get extracted after SEO's second validated run, not before.
   **Verification is two-stage:** (a) offline dry-run on `projects/_demo/` with mock metrics — two consecutive runs proving memory→verify→learn, lock refusal, pause-on-breach, and stale-proposal surfacing; (b) **connectors-only live smoke test** against the first real project's lowest-risk credentials (GSC read + DataForSEO read): auth, quota headroom, partial-failure and retry behavior.
2. **Phase 2 — First live SEO loop** via `/intake-project` on a real project. Recommendations-only mode first; Tier-1 PR proposals enabled only after human review of the first two reports.
3. **Phase 3 — Content/social loop live** after the SEO review workflow has proven itself over ≥2 run cycles; wire its tools then.
4. **Phase 4 — Ads loop live, last.** Read-only API access first, propose-only mode, daily ceilings verified live before any budget-change proposals are allowed. Fallback if Meta/Google API onboarding stalls: loop reads manual CSV exports.

**Milestone-1 exit criteria (all required):** one loop end-to-end locally; spec validation rejects a bad spec; lock refusal works; redaction test passes (a planted fake secret never appears in logs/reports); a simulated connector failure produces a clean partial-failure run log; pause-on-breach triggers and blocks; approval gates demonstrably prevent an unapproved apply.

## Scheduling & operations

- Default: **local Windows Task Scheduler** invoking `claude -p "/run-loop <project> <loop>"` with a fixed working directory, captured stdout/stderr to `runs/`, and a retry-once policy. **The scheduled action (or a checked wrapper script it calls) must invoke tools through the workspace's pinned virtual environment (`.venv/Scripts/python.exe`), never a bare `python` on PATH** — a scheduled run that silently falls back to a different or dependency-less interpreter is a scheduling failure even if Claude itself launches successfully. **Out-of-band watchdog:** a separate, independent scheduled task checks that each active loop's expected run artifact exists for the current cadence window and raises an alert (toast/email) when one is missing — so a hard scheduling failure is caught even when no report was produced. In-report staleness surfacing (`HEALTH.md`) remains as the secondary signal. Cloud `/schedule` routines only for projects whose repo + secrets are cloud-accessible.
- Hardening options documented but not required for a solo operator: dedicated Windows account for scheduled runs, always-on machine.
- Debugging never depends on model memory: `runs/<run-id>/run.json` is the observability contract.

## Who codes, who reviews

**Claude Code = primary implementer & orchestrator.** Resident in this workspace with the harness the runtime depends on (skills, scheduling, plan files, persistent memory); strong at multi-file scaffolding and long agentic runs. Weakness managed: builder's bias — countered by the cross-model reviewer.

**Codex = adversarial reviewer (plans and code), read-only.** Cross-model review catches shared-blind-spot errors; a read-only sandbox guarantees the reviewer can't contaminate what it judges. Weakness managed: it advises, not commands — Claude arbitrates, and every rejection is logged with a reason.

**Review protocol (artifact-based, not memory-based):**
- Canonical artifacts: `PLAN-REVIEW-LOG.md` (full argument transcript) and `RISK-REGISTER.md` (every open finding, its severity, and its resolution or written human risk-acceptance). Codex session continuity is a convenience, never a dependency.
- Plans: Codex reviews in a read-only session, max 2 iterations (user-set cap); on deadlock, unresolved points go to the human as tie-breaker — never silently shipped.
- Code: Claude implements each phase; **phase review inputs include the diff plus representative run artifacts** (a real `run.json`, a failing-path simulation, the scheduler config), not source alone. **Unresolved high-severity findings block phase completion** unless the human accepts the risk in writing in `RISK-REGISTER.md`.
- Human (you): approves the converged plan, each phase's completion, every risk acceptance, and every Tier-1/Tier-2 action in production. You are the only party that can publish or spend.

## Key decisions & tradeoffs

- **Markdown+YAML specs with pre-run schema validation** — human-auditable and diff-able; validation happens before load, not after.
- **Append-only `runs/` as system of record, `memory.md` as derived view** — auditability without giving up a readable narrative.
- **All three loop *specs* drafted in Phase 1, but only SEO's tooling wired** — the user wants all three loops; drafting specs is cheap and shapes shared abstractions, while deferring tool surface area until one loop is proven (Codex's concern, accommodated).
- **Atom Eve as prompt reference only** — its eve/Vercel runtime is skipped; we re-implement plumbing on infrastructure already paid for.

## Risks / open questions

- Meta/Google Ads API onboarding (app review, dev tokens) may stall Phase 4 → CSV-export fallback defined above.
- Qualitative guardrail rubrics need real content to calibrate — expect rubric revisions during Phase 2/3.
- Solo-operator scheduling on a desktop is inherently best-effort → staleness surfacing (HEALTH check) is the compensating control.

## Out of scope

Product feedback loop; auto-apply mode; Codex as runtime; multi-user access.

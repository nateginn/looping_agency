# Handoff — read this first in a new session

> **Everything below the "CURRENT STATE" section is historical.** It was accurate when
> written and is preserved as the record, but do not treat it as the present state of the
> project without cross-checking `git log` and `RISK-REGISTER.md`. This file had drifted
> badly enough by 2026-08-10 that it was flagged as untrustworthy; the section immediately
> below is the fix.

---

# CURRENT STATE — 2026-08-11 (overnight session, ~00:45–02:00 local, MDT / UTC-06:00)

**Read `PLAN-SEO-PROGRAM-INTEGRATION.md` first.** Nate dropped `SEO_PROGRAM.md` (untracked
reference material) into the repo and asked for a Codex-reviewed assessment of what in it
should change the Loop Agency process. Eight rounds of adversarial review later
(`PLAN-REVIEW-LOG-SEO-PROGRAM.md`, `VERDICT: APPROVED` at round 8 of a 10-round budget), the
answer reordered the roadmap. Documentation only — **no code was changed, no spec was
edited, no live API was called, and no boundary moved.**

What it found, all measured from `runs/2026-08-11T05-18-25-362Z-u5z1dd/snapshot.json`:

- `art`'s organic web-search channel produced **23 clicks in 28 days**; 20 are brand
  navigational. The three non-brand clicks are on services the loop does not track. Five
  commercial queries at ~position 1 with 400–710 impressions each take **zero** clicks.
- **D1 (High)** — `dataforseo.py:165/:261` match SERP results by path substring with no
  domain check, so competitors' URLs are reported as this site's rank. It also emerged that
  ART *does* rank ~33–50 for `physical therapy greeley`, which the defect was obscuring.
- **D2** — both call sites discard the SERP composition the loop already pays for.
- **D3 (High)** — `min_sample_size` is compared against the run's *total* sample, so
  "verified winner" currently means only "position didn't drop 5 places".
- **The freeze switch is `approval_mode: propose-only`**, not the other two gates:
  `auto_implementation_enabled` is only checked on the Codex auto path, and
  `manual_approval_only` is read from the proposal's cached copy, not the spec.

The plan recommends freezing `art`'s auto-implementation before any of it is built. **That
change was deliberately not made** — it alters the live operating state and is Nate's call.
Next scheduled proposal-generating run is **Mon 2026-08-17 06:00**; the Thursday and daily
jobs are `technical-only` and generate nothing.

## 2026-08-12 — reconciled with two more assessments; 10 Codex rounds total

Three independent assessments were merged into `PLAN-SEO-PROGRAM-INTEGRATION.md`: this
plan, MMC's three-track plan (from a manual 8/08 DataForSEO audit), and
`D:\Dev\artwebsite\SEO-ASSESSMENT-2026-08-12.md`. Still documentation only — no code
changed, suite 211/211.

**Start here tomorrow: the "executable slice" section of the plan.** Critical path for
*this* repo is `freeze → 3a → fresh verification → rebaseline → 3b`. Two traps are
called out explicitly and a cold operator must not skip them:

1. **3a is one atomic change-set, with the scheduled tasks disabled first.** Scheduled jobs
   run against this working tree, so a rank check firing mid-edit sees partial behaviour.
   Exact `Disable-ScheduledTask` commands and the hold window are in the plan.
2. **The rebaseline must be code, not a note.** `_evaluate_attention()` compares against
   prior snapshots regardless of documentation. Acceptance test: the first rank run after
   re-enabling emits **zero** historical `local_rank` delta findings.

**New since yesterday:**

- **D5 (High)** — `organic_rank_position` comes from `rank_absolute` (`dataforseo.py:273`),
  which counts local-pack and PAA blocks. With D1 and D4, `local_rank` has been wrong three
  independent ways at once. **All prior numeric local ranks are void** — host validation
  cannot repair a `rank_absolute`-derived number, so the earlier "keep host-validated rows"
  remedy is withdrawn; they are forensic reference only.
- **P1 is de-risked and buildable now.** DataForSEO's Maps endpoint
  (`/v3/serp/google/maps/live/advanced`, `$0.002`, verified live) replaces OAuth as the
  first path to GBP measurement. OAuth drops to a later enhancement for calls/directions.
- **GBP primary category is already `Chiropractor`** — this kills the category hypothesis
  raised 8/11. Greeley's pack absence is **prominence**: 55 reviews / 7 photos against
  incumbents at 225 / 434 / 508 / 656. Denver: 7 reviews, 5 photos, Thursday reads closed.
- **Ownership is three-way.** This repo owns only "rebuild the loop". GBP work is
  owner-executed; city pages belong to `artwebsite`.
- **Owner decision 8/11:** the Denver listing keeps "ART - Denver". The NAP fix inverts —
  the website carries the listing's name. Inconsistent listing names are an accepted,
  recorded trade-off. Do not re-litigate.
- **Higher business value than anything in this repo:** `artwebsite`'s contact form returns
  HTTP 500 on every submission. Leads are being lost now.

**Owner-executed, DONE 2026-08-12:** the BBB / Yelp / Zocdoc address inconsistency is
fixed. The site publishes `1823 65th Ave Suite 3` (correct); those three directories
carried two other addresses. **Treat 2026-08-12 as the baseline date for local-visibility
comparisons** — citation consistency is a primary local-pack input, so pre- and post-fix
data are not comparable. Directory edits can sit in moderation for days and can be
re-scraped from stale aggregators, so spot-check by eye in a week and again in a month;
automated checks of these three are unreliable (JS-rendered, fetch-hostile) and a null
result means nothing.

---

# CURRENT STATE — 2026-08-11 (earlier session, ended ~00:30 local)

**Git: everything is pushed. Nothing is waiting to be published.** `master` and
`phase6-ruleset-verification` both point at `d960c9d`, and both are level with `origin`.
`master` was fast-forwarded 8 commits (`f85dd5c → d960c9d`) on 2026-08-11 — before that it
predated Phase 7 activation entirely.

`SEO_PROGRAM.md` is untracked **by standing instruction** — do not read, move, commit, or
delete it. It is the operator's reference material for future work.

## What shipped this session

| Commit | What |
|---|---|
| `0c3a01a` | Four operational fixes (see `RISK-REGISTER.md` **R15**) |
| `9f244f2` | Path B reconciliation; Path A marked dormant (see **R16**) |
| `d960c9d` | Recovered art/seo cycle + its Codex review outcome |

Test suite: **211/211** (`./.venv/Scripts/python.exe tools/tests/phase1_exit_criteria.py`),
up from 191. Every module `--verify` passes except `codex_review_proposal.py`, which has
never had one.

## Operating model: Path B (direct push), decided — and NOT built

Read `PLAN.md`'s **SELECTED OPERATING MODEL** section first; most of the rest of that file
is Path A and is marked `[HISTORICAL — PATH A]`. Nate's requirements: no per-change
approval, notification via the MMC briefing, positive confirmation of no damage, and
explicit notification with troubleshooting detail if a rollback fired.

**Nothing in this workspace pushes, deploys, or rolls back.** The autonomous path is live
only up to a *local* commit (`/codex-seo-review` → `apply.py`). Before any Path B code is
written, **R6/R10 must be amended by a dated row of its own** — they currently classify
every push to `artwebsite` as Tier 2 human-only, unconditionally. R16 records the direction
but is explicitly *not* that amendment.

`tools/publish.py` and the branch-protection/PR/auto-merge block of `lib/github_compare.py`
implement Path A, carry DORMANT BY DECISION headers, and cannot be flag-flipped into Path B
(`publish.py` refuses a `main`/`master` destination by construction). Do not delete or build
on them. `compare_commit_to_main()` is exempt — `run_loop.py` calls it every run.

## Open state in `art/seo`

Three proposals from run `2026-08-11T05-18-25-362Z-u5z1dd`:

- `...u5z1dd-0` (title-tag, homepage) — **`review-revision-needed`**
- `...u5z1dd-1` (meta-desc, homepage) — **`review-revision-needed`**
- `...u5z1dd-2` (internal-link, /work-comp/) — still `draft`, `manual_approval_only`, can
  never auto-implement

Both homepage proposals held at 0.98 across two independent Codex rounds on *insufficient
evidence* (2 clicks/15 impressions; 0 clicks/1 impression). They were deliberately **not**
revised into a pass 2 — the blocking objection is not correctable by rewording, and forcing
it would game the control. They are non-terminal, so **cooldown blocks new homepage
proposals** until a human resolves them via `/review-pending`.

`artwebsite` is untouched: HEAD `e5a811c` on `main`, no `seo/*` branch created, no worktree.

## Known defects found but NOT fixed (highest value first)

1. **Candidate selection is the real blocker to autonomy.** `_pick_new_actions` ranks
   positions 3–20 by clicks, and nearly every `art` candidate has 0 clicks, so ordering is
   near-arbitrary. This session proved the consequence: a competent reviewer correctly
   refuses what the loop generates. Building the Path B push first would yield an
   autonomous pipeline that reliably ships nothing. Fix this before the push.
2. **The evidence packet misleads reviewers and biases them toward `hold`.** It passes
   `guardrail: {metric, comparator: ">", value: 5}` without stating it applies to *drift
   from baseline* (`_evaluate_prior_experiments` computes `metric_value - baseline`), and
   `min_sample_size: 100` without stating it is compared against the *total* GSC sample
   (10,308), not the keyword's impressions. Both reviewers misread both fields identically
   and raised false objections. In an agreement-required system, that manufactures spurious
   holds. Fix in `lib/review_protocol.py`.
3. **Hash echo is fragile.** Round 1C corrupted the 64-char `evidence_packet_hash` when
   echoing it (inserted `f9` → 66 chars); the tool correctly refused it. Verify a short
   prefix, or have the tool inject the hash rather than trusting an LLM to transcribe it.
4. **`.claude/skills/codex-seo-review/SKILL.md` contradicts itself** — its "never runs
   against `art`" bullet cites a Prerequisites section that says the opposite. Stale since
   the 2026-08-03 activation.
5. **`search_analytics` stamps freshness as `pulled_at`, not `as_of`** like the other three
   snapshot sections. Harmless today (`_section_history` is never called on it) but
   inconsistent for MMC's per-section freshness display.

## Cleanup inventory — identified, awaiting the operator's decision

Nothing here has been deleted. Zero-risk: `.agents/` (empty), `.claude/scheduled_tasks.lock`
(stale, dead PID). Doc consolidation: `PLAN-REVIEW-LOG-step6.md` (referenced by nothing),
`PHASE2_READINESS_CHECKLIST.md`. Data hygiene: the 9 terminal proposals still sitting in
`pending/` — archive rather than delete; their rationale is in `events.jsonl` and git.
`.review-artifacts/` is now tracked as of `d960c9d`.

## Scheduling (all four tasks re-registered 2026-08-10)

Every job runs through `tools/scheduled/task.cmd` → `logs/<name>.log` (gitignored, rotates
at 2 MB). Times are **local**; run IDs and `run.json` are **UTC** — a 6-hour offset, so a
06:00 local run appears as `12:00Z`.

| Task | Schedule | Next |
|---|---|---|
| `LoopAgency-Art-SEO` (full) | Mon 06:00 | 8/17 |
| `LoopAgency-Art-SEO-Technical` | Thu 06:00 | 8/13 |
| `LoopAgency-Art-SEO-DailyRank` | daily 07:00 (moved off a 06:00 lock collision) | 8/11 |
| `LoopAgency-Watchdog` | daily 07:30 | 8/11 |

Watchdog now exits 0 (`_demo` reports `not-enabled` via `loops_enabled`).

---

# HISTORICAL RECORD (pre-2026-08-11) — verify before relying on any of it

## Phase 7 activated for `art/seo` — 2026-08-03 (same day as shipping, per explicit user request)

`projects/art/loops/seo/spec.md` now sets all three gates: `approval_mode: tier1-enabled`, `auto_implementation_enabled: true`, and `manual_approval_only: false` on `title-tag-rewrite`/`meta-description-rewrite` only (`internal-link-addition` stays `manual_approval_only: true` and can never auto-implement regardless — no HTML-mutation engine exists, see Key Decision 3 in `PLAN-PHASE7-CODEX-REVIEW.md`). `.claude/skills/codex-seo-review/SKILL.md` no longer hard-blocks `art` — the three-gate check in `spec.md` is the only gate now, same as for any project.

**Scheduler decision (not deferred — actively decided this session):** this environment's available scheduling primitives were inspected specifically for whether they could run `/codex-seo-review` unattended. `RemoteTrigger` (cloud-hosted scheduled Claude Code routines) runs in a remote sandbox with no access to this local repo, local git, Windows Credential Manager, or this machine's local `codex login` session. `CronCreate` is explicitly session-only — in-memory, dies when the invoking conversation ends, capped at 7 days, only fires while a session is idle — not durable unattended infrastructure. Windows Task Scheduler → bare `python.exe` still cannot reach `codex exec` at all (unchanged finding from the original Phase 7 build). **Conclusion: no safe Claude-Code-capable scheduler exists in this environment for this purpose. `/codex-seo-review` remains invoked on demand only.** The plain-Python `run_loop.py` (weekly full + Thursday technical + daily rank-check) and `watchdog.py` (daily) jobs from `PHASE3-SCHEDULING.md` — which have no Codex dependency — were registered via `schtasks` this session; see below for exact task names/status.

**What this activation does NOT change:** push, merge, deploy, and rollback remain Tier 2/human-only, unconditionally — `publish.py` is still not wired to any project. The pre-existing 3 draft proposals in `pending/` (from the `2026-08-03T14-37-41-993Z-v3sydh` run, created before this activation session) were left untouched; only newly created proposals from a fresh collection were used to exercise the new path for the first time. See `RISK-REGISTER.md` R14 for the full activation record.

## Phase 7: Codex-reviewed low-risk SEO auto-implementation + MMC change-history briefing — dormant capability, shipped 2026-08-03

Built while Nate was away from keyboard, per his explicit prior authorization to use Codex adversarial plan review in place of questions that would otherwise go to him. Full design record: `PLAN-PHASE7-CODEX-REVIEW.md` (six rounds of Codex review — one deliberately past the session's own stated `MAX_ROUNDS=5`, logged explicitly as a judgment call; concluded without a formal `VERDICT: APPROVED` after the remaining findings became narrow completeness fixes rather than structural flaws — see `PLAN-REVIEW-LOG-PHASE7.md` for the full six-round transcript).

**What this is: a dormant capability, not an activated pipeline.** It lets a low-risk SEO proposal (`title-tag-rewrite`, `meta-description-rewrite`) reach a **local, unpushed git commit** — the pre-existing Tier-1 boundary, nothing new about what "implemented" means — without Nate individually approving it, once two independent rounds of Codex review agree AND a loop's spec explicitly opts into three separate gates. **`art`'s spec was deliberately left untouched** — none of this fires for `art` today. Activating it later is exactly three named `spec.md` edits (below).

**Why Codex review couldn't just be "automatic" in the fullest sense**: `codex exec` is a Bash-tool CLI invocation — it only exists inside a Claude Code session. Nothing in `tools/` (pure Python, run by hand or via Task Scheduler) can call it. So this session built the mechanism as a repo-scoped skill (`/codex-seo-review`, invoked on demand — exactly like `/run-loop`/`/review-pending` today), not a cron job. Wiring it to a true zero-touch schedule would mean registering a Claude-Code-capable scheduled agent (this environment's own cron/schedule mechanism, not Task Scheduler → bare `python.exe`, which can't reach Codex at all) — a separate, bigger activation decision this session deliberately did not make unilaterally while Nate was unreachable to correct a mistake.

**To activate for a real project** (not done for `art`): in that loop's `spec.md`, set all three, together:
1. `approval_mode: tier1-enabled` (pre-existing field)
2. `auto_implementation_enabled: true` (new)
3. Remove/set `manual_approval_only: false` on the specific `allowed_actions` entry (e.g. `title-tag-rewrite`)

All three are independently re-checked by `apply.py` at the moment of commit, not just at adjudication time — flipping any one back off after a proposal reaches `approved-for-implementation` genuinely disables the path for it.

**State machine**: adds `review-pending`, `review-revision-needed`, `review-approved`, `review-rejected`, `review-held`, `approved-for-implementation` alongside the unchanged `draft/reviewed/approved/implemented/applied/verified/breached/rejected/implement-failed`. A human can always rescue any new state into the existing `approved`/`rejected` via the unchanged `/review-pending` — no new human-facing states to learn. `internal-link-addition` gets full evidence/drafting/review support (source page, destination page, anchor text — `tools/draft_link.py`) but can **never** auto-implement: no code mutates a live Django template to insert a link, so it always lands at `review-held`/`review-approved` with `eligible: false` — a deliberate scope boundary, not a bug.

**Event history**: a new, durable, append-only, redacted `events.jsonl` per loop (`tools/lib/event_log.py`) is the source of truth for this new review subsystem's `status`/`review`/`implementation` — the event append IS the commit point, the proposal JSON file is a replayed projection. Authorization-adjacent reads (`--adjudicate`, `apply.py`'s auto-implement check) fail closed on any log corruption anywhere in the file. The pre-existing `draft/approved/implemented/...` machine is entirely untouched; events for it are best-effort observability layered on top (`event_log.py --reconcile`), a deliberately weaker guarantee for already-proven code.

**MMC**: gains a "Looping Agency changes since previous briefing" section (`D:\Dev\MMC\collector\sources\looping_events.py`, `collector\advance_looping_cursor.py`, `PROMPT.md`), reading that same event log with its own cursor (`data/looping_cursor.json`, keyed per `project/loop`). The cursor only advances after `notify.py` reports the Telegram send was *accepted* (never before — a failed briefing leaves the same events available for the next run; this is intentionally the same "send-acceptance" guarantee every other MMC data type already relies on, not a stronger "confirmed delivery" claim MMC has nowhere else), bound to a sha256 content stamp of the exact `inputs.json` written at collect time (`collect.py`) so the cursor can never advance over events that weren't actually in the sent briefing. `looping.py`'s known `pending_count`-counts-all-statuses bug was fixed in the same touch (now matches the already-correct `pending_undecided_count`).

**Verified**: `tools/tests/phase1_exit_criteria.py` 191/191 (153 pre-existing + 38 new, including a full offline end-to-end fixture: disagreement in pass 1 → revision → pass-2 agreement → adjudicate → real local git commit, plus missing-evidence/Tier-2/breach-pause/disable-after-adjudication/cooldown/reconcile coverage), every new/changed module's own `--verify` (`event_log.py` 26/26, `review_protocol.py` 22/22, `draft_link.py` 17/17, `artwebsite_seo.py` 31/31 including new `pre_commit_check` coverage, `spec_validate.py` +7, `draft_copy.py` +2), MMC's `looping_events.py` 12/12 and `advance_looping_cursor.py` 9/9, plus a direct read-only smoke call of `looping.collect()` against the real `art/seo` tree confirming no crash and the `pending_count` fix. No live SEO run, live GitHub call, live Telegram send, or touch to any existing pending proposal occurred during this work.

**Honest limitations, not silently glossed over**: reviewer-record provenance is local accountability, not cryptographic proof (matches this project's own established position on `publish.py`'s guards — not a claim this is a security boundary against an agent with filesystem/shell access); the legacy state machine's `--reconcile` only checks the latest milestone per on-disk field, not full per-run history (an already-argued-back, bounded trade-off — see the plan's Key Decisions); `apply.py`'s pre-commit spec re-read narrows but does not eliminate the TOCTOU window on `spec.md` (not lock-protected anywhere in this codebase, before or after this work).

## Phase 6a health/rollback orchestration — implemented and offline-verified (2026-07-30)

The credential-free Phase 6a foundation is now present in `tools/lib/deploy_health.py` and `tools/deploy_verify.py`. It records the deployed commit SHA and previous known-good SHA, waits through a non-blocking 15-minute stabilization window via a separate scheduled check, runs bounded DNS/TLS/HTTP/canonical/robots/representative-page checks, persists redacted results, and models healthy verification plus rollback/escalation outcomes. `PHASE0-RUNBOOK.md` records the still-human live GitHub controls and activation steps.

Verification completed: `deploy_health.py --verify` 31/31, `deploy_verify.py --verify` 42/42, full exit-criteria suite 153/153, all supported self-tests green, `compileall tools` clean, and `git diff --check` clean. No credentials, live GitHub calls, deployment, push, merge, or `D:\Dev\artwebsite` modification occurred.

Remaining integration seams are intentional: no project is wired to invoke deployment verification automatically; the default redeploy hook refuses and escalates rather than performing a production rollback; commit evidence is advisory until the site exposes a build marker. `art` remains `approval_mode: propose-only` with every action `manual_approval_only: true`.

Read this before touching anything else. It replaces needing to scroll a long prior chat transcript. Canonical docs are `AgentColabPlan.md` (design), `PLAN-REVIEW-LOG.md` (plan review history), `RISK-REGISTER.md` (findings + risk acceptances), `CLAUDE.md` (operating instructions), `PHASE2_READINESS_CHECKLIST.md` (Phase 2 go/no-go tracking) — read those next if you need depth.

## Resume here — updated 2026-07-24 (SEO monitoring expansion shipped + first full baseline run)

**Everything below this "Resume here" block that is dated 2026-07-16/18/21 is now historical.** The canonical design record for the current state is `PLAN.md` + `PLAN-REVIEW-LOG.md` (read the "Act 3 — Build" and "Post-build credential activation" sections of the log for the full story). There is also a standing memory note `seo-monitoring-expansion.md`.

**What is live now.** The `art`/`seo` loop is no longer GSC-only. As of 2026-07-23/24 it runs **five live, individually-verified connectors**: `gsc`, `pagespeed` (Core Web Vitals), `gsc-indexation` (sitemap + URL Inspection), `dataforseo-local-rank` (city/state SERP), `dataforseo-backlinks`. The expansion was grilled + Codex-reviewed (6 rounds), Codex-built under a hard no-scope-creep constraint, fixed during review, and shipped as commit `e282780`. The first real full baseline run through all five connectors completed 2026-07-24 (`run_id 2026-07-24T03-05-54-719Z-9noq9s`, all connectors `ok`).

**Still true / unchanged:** `art` stays `propose-only`, `manual_approval_only: true` on every action — nothing auto-applies. No Task Scheduler job is registered — `art seo` only runs when someone types the command by hand.

**Uncommitted right now:** the baseline run's artifacts (`runs/2026-07-24.../`, 3 new proposal files, 6 bumped proposal counters, `memory.md`). These were left uncommitted at Nate's stopping point — commit them when convenient (`git add projects/art/loops/seo && git commit`).

**Costs real money:** DataForSEO calls are paid/metered and `pagespeed`'s free keyless tier is exhausted (now needs the stored key). Do **not** casually re-run `run_loop.py art seo` for debugging — use `--verify` self-tests or targeted single-connector scripts. See the memory note.

---

## NEXT STEPS PLAN (2026-07-24) — move the SEO loop toward "live/operational"

The baseline run surfaced real, grounded findings. Two independent goals: **(A) act on the SEO findings** (the actual point of the loop), and **(B) make it operationally live** (automated, delivered, unattended). Do A1 first — it's the single highest-value finding and it's already fully diagnosed.

### Track A — Act on the baseline findings

**A1. Prune the ad landing pages out of `priority_pages`; chase only `/massage/`. (CORRECTED 2026-07-24 — the original "sitemap gap" reading was a false alarm.)**
`gsc-indexation` flagged 6 of 11 priority pages "URL is unknown to Google." Initial read was "sitemap gap, add them." **That was wrong** — verified this session that 5 of the 6 are deliberate **Meta ad landing pages**: `<meta name="robots" content="noindex, nofollow">`, Facebook pixel, lead-capture forms, no site nav — `/shockwave-therapy-denver/`, `/shockwave-therapy-greeley/`, `/shockwave-therapy-plantar-fasciitis/`, `/chronic-tendon-pain-treatment/`, `/non-surgical-pain-relief-denver/`. Being noindex, absent from the sitemap, and unknown to Google is their **correct, intended state** (they take paid Meta traffic, not organic). Do **not** add them to the sitemap or "fix" their indexing — that would fight the site's own noindex directives.
  - **Real action:** remove those 5 from `priority_pages` in `projects/art/loops/seo/spec.md`. They're paid-ad assets, not organic-SEO pages; leaving them in makes the Technical Health section report them "unknown to Google" every run forever — permanent false alarms. (Caveat: the current design ties both `pagespeed` and `gsc-indexation` to the same `priority_pages` list, so pruning them also drops their Lighthouse/CWV check. Landing-page speed, if wanted, belongs with the ads system — `D:\Dev\ART Marketing Agency` — not this organic loop.)
  - **The one genuine finding:** `/massage/` is a real organic page (`index, follow`, no pixel, real nav, and it IS in the sitemap) but came back "URL is unknown to Google." Legit, if minor: use GSC's "Request Indexing" (URL Inspection tool) on it and confirm it's internally linked from indexed pages. Watch a later run for it flipping to "Submitted and indexed."
  - **Lesson for the loop:** `priority_pages` was seeded from `project.md`'s reference list, which mixed organic service pages with paid-ad landing pages. An organic-SEO loop should only track organic-intent pages. Worth a quick audit of the whole `priority_pages` list against noindex status before the next run.

**A2. Diagnose the DataForSEO local-rank "no match" result.**
All 36 local-rank checks (3 locations × 12 keywords) returned no organic-SERP match. This is plausibly *accurate* — the site ranks strongly for **brand** terms ("accelerated rehab therapy" pos 1–2) but the 12 targets are competitive **generic commercial head terms** ("physical therapy greeley", "chiropractor denver") the site may genuinely not rank for in the top 100 yet. But confirm it's not a matching bug first: the connector matches by checking whether the target `page` path is a substring of a result URL, so if the site ranks with a *different* URL than expected, it reads as "no match." Quick check: pick one keyword (e.g. "greeley chiropractor" — GSC shows the homepage at avg pos ~3.4 for it), run one live DataForSEO SERP call, and see whether the domain appears at all under a different URL. Outcome decides whether to (a) accept it as a real ranking gap and a content/SEO target, or (b) adjust the target-page match logic / keyword list in `spec.md`.

**A3. Review the 9 pending proposals.** `/review-pending art seo` — 6 from 2026-07-21 + 3 from tonight's baseline. All Tier-1, all `manual_approval_only`. Approve/reject each. (Note: even approved, they won't auto-apply while `manual_approval_only: true` — applying is still a manual human action.)

### Track B — Make it operationally live (unattended)

**B1. Register the Task Scheduler jobs.** `PHASE3-SCHEDULING.md` has the ready-to-run `schtasks` commands (never auto-executed by tooling, by design): Monday 06:00 full run, Thursday 06:00 technical-only run, daily 06:15 watchdog. Registering these is what makes the loop actually "live" instead of hand-run. Human decision — run the commands when ready.

**B2. Verify the MMC briefing actually surfaces the richer report (PLAN.md step 8, never done live).** The whole delivery premise is that MMC's existing daily Telegram briefing picks up the new `report.md` sections (Local Rank / Backlinks / Technical Health) with **zero changes inside `D:\Dev\MMC`**. This has not been observed end-to-end yet. After B1 (or a manual run), wait for / trigger one MMC briefing cycle and confirm the new sections show up coherently in the Telegram output. If they don't, that's a real gap to diagnose — but still without editing MMC (the fix would be report formatting on this side). Separately: MMC's collector has a known stale `pending_count` bug (counts all proposal files regardless of status) already handed off as a separate fix.

### Track C — Optional / later

- **True hyperlocal rank** (zip / 20-mile radius) via DataForSEO's **Business Data API → Google My Business**, replacing the current city/state SERP fallback. Needs confirming the product is on Nate's plan. Only worth it if city/state granularity proves too coarse in practice.
- **AEO connector.** DataForSEO's `AI Optimization API` (LLM Mentions, AI Keyword Search Volume) now exists, which makes the long-standing "no AEO data source exists" note in `spec.md`/docs stale. A real AEO monitoring capability is a separate, unstarted effort — scope it as its own mini-plan if/when wanted.
- **Phase 3 auto-implement activation.** The built-but-dormant auto-implement-on-approval capability (commit `59f40f8`) is still not switched on for `art`. Flipping it is two deliberate `spec.md` edits (`approval_mode: tier1-enabled` + removing `manual_approval_only` per action) — a separate human decision, unrelated to this expansion.

### Baseline snapshot (the "where we stand right now" numbers, for later comparison)
From `run_id 2026-07-24T03-05-54-719Z-9noq9s`: GSC 2,652 keyword/page rows (strong brand + generic-local visibility — "chiropractor near me" ~pos 1.1, "auto injury chiropractor" pos 1). Backlinks: **86 referring domains, 130 backlinks**. Lighthouse perf scores 68–98 (weakest `/shockwave-therapy-denver/` at 68); CWV field status "unknown" on all pages (normal — needs more real-user Chrome traffic before Google publishes CrUX data). Indexation: 5 of the 6 "unknown to Google" pages are intentional noindex Meta ad landing pages (correct as-is); only `/massage/` is a genuine unindexed organic page (see A1). Local rank: no match on all 36 (see A2).

---

## Historical (pre-2026-07-24) — kept for reference

**Step 6 is done.** Steps 0–5 of the accepted Phase 2 execution plan were done, committed, and pushed to `origin/master` on 2026-07-16. Step 6 (live smoke test against `art`, then its first real run) completed 2026-07-18 — see "Step 6 — completed" below. Phase 3 (auto-implement + verify loop) shipped 2026-07-21 (`59f40f8`), still not activated for `art` (see Track C).

## Status as of 2026-07-16 (end of the Phase-2-onboarding session)

**Phase 1 is complete** (Python, 32/32 checks originally, since grown — see below). The Node→Python correction is documented in `RISK-REGISTER.md` R8. **This project is Python-only** — see `CLAUDE.md` "Implementation language" and the `language-choice-approval` memory: never introduce a non-Python language without the user's explicit prior approval.

**Phase 2 kickoff is underway.** The first real project, **`art`** (acceleratedrehabtherapy.com, Nate's own site), is fully onboarded: `projects/art/project.md` + `projects/art/loops/seo/spec.md` exist, validate clean, and are committed. Nate has a working, verified live GSC credential stored in Windows Credential Manager. Two independent Codex review rounds ran against this session's work (the Steps 1–3 connector build, and the `art` intake) — all findings from both were fixed. An AEO (Answer Engine Optimization) documentation scope addition also landed on top of the `art` onboarding.

### What happened since Step 0 (newest first)

- `db93fc7` — Documented AEO as a reviewer-guidance consideration for `art` (goals + priority-pages sections in `project.md`, a Notes bullet in `spec.md`). Docs only — no new metric/guardrail/connector; GSC/DataForSEO have no AI-answer-engine-citation signal to measure against.
- `acc0279` — Fixed 3 Codex findings from an independent review of the `art` intake: added a docs-only "Priority reference pages" section to `project.md` (review aid, not an enforced filter — the loop still proposes from all GSC data ranked by clicks); replaced misleading `rollback: revert PR` text in `spec.md` with an honest description (no PR path exists for this project) and marked all three `allowed_actions` `manual_approval_only: true`; tightened `project.md`'s repo-access wording (`D:\Dev\artwebsite` is never read/written by this loop's tooling).
- `92d7b64` — **Step 5**: onboarded `art` — `project.md`, `loops/seo/spec.md` (inputs `[gsc]` only, `site_url: sc-domain:acceleratedrehabtherapy.com`, weekly Monday cadence, default 5-position guardrail, no seed keyword list — discovers ranking pages from live GSC data), `memory.md`, `pending/`, `runs/` scaffolded. Validated clean.
- `0b7a54b` — **Step 4 prep**: Nate's GSC credential turned out to be a service-account JSON key (not a raw token), so added `tools/lib/gsc_auth.py` (mints short-lived access tokens from the JSON, `webmasters.readonly` scope only), transparent chunked storage in `tools/lib/credentials.py` (Windows Credential Manager caps a blob at ~1280 chars; the JSON is ~2.3k), `--store --from-file` CLI support, and `tools/lib/tls.py` (Windows-truststore HTTPS fix, R8). Pinned `google-auth`, `requests`, `truststore`.
- `87145bc` — Fixed 4 findings from an independent Codex review of the Step 1–3 connector build: (1) GSC returns `page` as a full URL while DataForSEO targets are paths — merge now normalizes both to the URL path component; (2) `spec_validate.py` now rejects unknown connector names in `inputs` pre-run (was previously only caught at run time); (3) GSC date ranges are endpoint-inclusive — fixed an off-by-one in the `metrics_window_days` window calculation; (4) hardened the `.env` ACL check to compare fully-qualified account names instead of a bare-username suffix match.
- `37e44e1` — **Step 3**: `tools/smoke_test.py` (connectors-only live check — resolves aliases, calls each connector once, redacted summary, bad-alias clean-failure simulation; writes nothing, takes no lock).
- `87138ad` — **Step 2**: wired `gsc.py`/`dataforseo.py` into `run_loop.py`'s `_fetch_metrics` dispatch; `spec_validate.py` gained conditional requirements per connector; GSC is the primary metrics source, DataForSEO only enriches matching rows with `serp_position`.
- `c768161` — **Step 1**: `tools/lib/credentials.py` — Windows Credential Manager (keyring) first, ACL-gated `.env` fallback second, `--store`/`--check`/`--verify`.
- `f6ca0fd` — **Step 0**: committed the outstanding Codex Rounds 3–4 review edits and `PHASE2_READINESS_CHECKLIST.md`.

**Current test suite: 49/49 exit-criteria checks, plus every module's `--verify` self-test, all green, no network calls.**

## THE USER HAS EXPLICITLY AUTHORIZED PHASE 2 KICKOFF (2026-07-16)

Decisions the user already made — do not re-ask, and note how each played out:

- **First real project: the user's (Nate's) own website.** → Onboarded as `art` (acceleratedrehabtherapy.com). Propose-only mode. Repo `D:\Dev\artwebsite` auto-deploys on push with no staging gate (Tier 2, human-only, R6) — this loop's tooling never reads or writes it.
- **Credential resolver library: `keyring`.** → Built (`tools/lib/credentials.py`), pinned in `requirements.txt`.
- **`.env` fallback is acceptable** with the mandatory restrictive-ACL check. → Built and hardened (fully-qualified-account-name comparison, not a suffix match).
- **He already has a working GSC credential.** → Turned out to be a **service-account JSON key**, not a raw token — `google-auth` added for token minting (see `0b7a54b` above).
- **Secrets never enter the chat transcript.** → Confirmed in practice: Nate stored the credential himself via `--store art-gsc-readonly --from-file <path>` in his own terminal, verified with `--check`, then deleted the source JSON file. No secret was ever pasted into the conversation.

## Exact next steps — the accepted Phase 2 execution plan

### Steps 0–5 — DONE (see commit list above)
Framework review closeout, credential resolver, real-connector wiring, connectors-only smoke test, service-account JSON support, and onboarding `art` are all committed and pushed. Do not re-plan or redo any of this.

### Step 6 — Live smoke test, then first run (COMPLETE — 2026-07-18)
1. `./.venv/Scripts/python.exe tools/smoke_test.py art` — first attempt 2026-07-16 returned HTTP 200 OK but 0 rows (Search Console still processing data for the newly-verified property); retried 2026-07-18 and returned 177 real rows, auth OK. Reviewed and accepted.
2. First real run: `./.venv/Scripts/python.exe tools/run_loop.py art seo` — also has two data points:
   - 2026-07-16T21:24:31.879Z (`run 2026-07-16T21-24-31-879Z-pc50bg`): `status: ok`, but `sample_size: 0`/0 keyword rows (GSC hadn't indexed data yet, same cause as the smoke test's 0-row result that day) → 0 proposals, a legitimate empty outcome, not a failure.
   - 2026-07-18T22:12:26.323Z (`run 2026-07-18T22-12-26-323Z-b8xtu9`): `status: ok`, `sample_size: 451`, ~187 keyword rows → **3 draft proposals created**: `title-tag-rewrite` and `meta-description-rewrite` on `http://acceleratedrehabtherapy.com/` (home page, two different low-traffic keywords), and `internal-link-addition` on `/auto-injury/`. All Tier 1, `manual_approval_only: true`, `pending/` review awaiting Nate.
   - Note for review: the first two proposals target the same page (home) via different action types — not a tooling conflict, but worth Nate's attention since GSC's http/https variants of the home page show up as separate rows and most candidate keywords in this window have 0 clicks (very early/sparse data), so the "top-clicks" ordering was effectively a tie-break among near-equal candidates this first cycle.
3. `PHASE2_READINESS_CHECKLIST.md` §5 flipped to `[x]`; `RISK-REGISTER.md` note added (Phase 2 kickoff explicitly user-authorized 2026-07-16; Step 6 completed 2026-07-18); this file updated; committed and pushed.

**Superseded — see "Phase 3" section below for current next-action.** (Historical note: the 3 proposals from the 2026-07-18 run were reviewed and rejected 2026-07-18 — see R9/keyword_exclusions in `RISK-REGISTER.md` and the `30cc05f` commit. They were built on "Accelerate Health" branded noise; a `keyword_exclusions` filter was added and set for `art` as a result.)

### Out of scope for this plan
Content-social/ads loops; Task Scheduler registration (commands documented in `PHASE3-SCHEDULING.md`, deliberately never executed by tooling); enabling `dataforseo` for `art` (deferred until the first GSC-only reports are reviewed); any AEO-specific connector/guardrail (deferred until a real AEO data source exists — today's AEO addition is reviewer guidance only). Auto-implement + push-to-live for `art` specifically is now a **capability** (Phase 3, see below) but not yet an **activated** one — that's a separate human decision, not implied by shipping the code.

## Phase 3 — auto-implement approved proposals, close the verify loop (COMPLETE, shipped 2026-07-21)

**What shipped:** once a human approves a Tier-1 title-tag-rewrite or meta-description-rewrite proposal (internal-link-addition stays manual — no safe automated edit strategy), `apply.py` now writes the change as a commit on an isolated local git branch inside `D:\Dev\artwebsite` (never pushes — `git worktree`, so Nate's own checkout is never touched). A new read-only GitHub compare-API check detects when Nate has pushed it live and promotes the proposal to `applied`, which unblocks `run_loop.py`'s previously-dead verification logic (`_evaluate_prior_experiments`) to actually measure before/after Google position. Full design history in `PLAN.md` + `PLAN-REVIEW-LOG.md` (4 rounds of adversarial Codex plan review, then a Codex build with 2 fix rounds, all independently re-verified — 73/73 tests, up from 54, plus 4 module `--verify` self-tests). Pushed as `59f40f8`.

**What did NOT change:** `art` is still `propose-only`, still `manual_approval_only: true` on every action. Nothing auto-implements for `art` today — the capability exists but two separate spec edits (flip `approval_mode: tier1-enabled`, remove `manual_approval_only` per action) are required to actually activate it, and that's explicitly a human decision, not bundled into this ship.

**Next action for Nate:**
1. `/review-pending art seo` — 6 undecided draft proposals are sitting in `pending/` (from the two 2026-07-21 runs), all still `draft`, none reviewed.
2. Separately: decide whether/when to activate Phase 3 auto-implement for `art` (the two spec edits above) — no rush, the capability isn't going anywhere.
3. Separately: decide whether to register the Task Scheduler jobs in `PHASE3-SCHEDULING.md` (weekly `art seo` run + daily watchdog) — currently nothing runs `art seo` except by hand.

### Verification bar
All module `--verify` self-tests + exit-criteria suite (49/49) through `.venv/Scripts/python.exe`, no network. Live smoke test reviewed by Nate before the first real run; first run produces valid redacted `run.json`/`report.md` with zero writes outside `projects/art/`.

## Hard boundaries (do not cross without the user present)

- **Never introduce a non-Python implementation language without the user's explicit prior approval** (R8 lesson; standing memory rule).
- **Never ask the user to paste a raw secret into chat**; never write one to any repo file. Aliases only.
- `D:\Dev\artwebsite`: **push, merge, deployment, and rollback are Tier 2/human-only, always (R6) — no exceptions, no tooling path exists for any of them.** Since Phase 3 (2026-07-21), tooling *may* write a local, unpushed commit/branch there via an isolated `git worktree` for an approved, non-`manual_approval_only` proposal — but only once a spec has explicitly opted in. Since Phase 5 (2026-07-29, `PLAN-REVIEW-LOG.md`/`RISK-REGISTER.md` R10), `apply.py` also runs a **read-only** `git fetch origin` immediately before that worktree is created, to confirm the local `refs/heads/main` still matches `refs/remotes/origin/main` (refuses on drift). That fetch only updates this checkout's own local remote-tracking ref cache — it never pushes, and is not a Tier-2 action. Never assume any of this is active for a project without checking its `spec.md`.
- `art` stays `propose-only` (every action `manual_approval_only: true`) until Nate explicitly changes that in `spec.md` — not implied by anything shipping in `tools/`.
- Don't attempt to work around a harness safety-classifier hard block — stop and hand it to the user.

## Fast sanity checks

```
./.venv/Scripts/python.exe tools/tests/phase1_exit_criteria.py   # all PASS, 73/73
./.venv/Scripts/python.exe tools/review_pending.py art seo --list  # 6 undecided draft proposals as of 2026-07-21
git log --oneline                                                  # see what's committed
git status                                                          # should be clean
git remote -v                                                       # origin -> github.com/nateginn/looping_agency
```

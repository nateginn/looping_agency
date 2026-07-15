# Loop Agency — Plan

## Context

You want to run parts of your businesses on **engineering loops** (per the podcast transcript): autonomous agents that build → verify against an objective metric → learn → iterate, on a schedule that runs for months. The video referenced **atomeve.dev**, an open-source registry of 28 loop agents (install-the-source, like shadcn). I reviewed it: each agent is a folder with `instructions.md`, tools, credentials setup, and a schedule — the SEO Improver is exactly the loop from the video (GSC + DataForSEO, weekly, opens PRs, review-before-apply).

**Your decisions:**
- **Runtime:** Claude Code as the runner (loop definitions kept as plain markdown so Codex could run them later too). Borrow atom-eve's proven prompts rather than reinventing.
- **Loops to build:** SEO, Content/Social, Ads. (Product feedback loop deferred.)
- **Projects:** Multiple, onboarded later via an intake command — the framework is the deliverable now.
- **Safety:** Review-before-apply. Every run produces a report + proposed changes you approve; nothing auto-publishes.
- **Sites:** Git repos → loops propose changes as branches/PRs.

## Architecture

Build the workspace in `d:\Dev\Looping _agency`:

```
Looping _agency/
├── CLAUDE.md                     # How any loop run works (the runner contract)
├── README.md                     # What this is, how to add projects/loops
├── templates/                    # Canonical loop definitions (adapted from atom-eve)
│   ├── project-intake.md         # Questionnaire → generates projects/<slug>/
│   └── loops/
│       ├── seo/instructions.md
│       ├── content-social/instructions.md
│       └── ads/instructions.md
├── projects/                     # One folder per business (created by intake)
│   └── <slug>/
│       ├── project.md            # Domain, repo path/URL, goals, KPI targets, credential refs
│       └── loops/<loop>/
│           ├── instructions.md   # Template + project-specific config block
│           ├── memory.md         # Run log: experiments tried, outcomes, wins/reverts
│           ├── metrics/          # Dated snapshots (rankings, impressions, spend) — the "verify" data
│           └── pending/          # Proposed changes awaiting your review
├── tools/                        # Thin Node scripts wrapping APIs (GSC, DataForSEO, social, ads)
├── .claude/skills/               # Slash commands (below)
└── .env.example                  # Credential names per loop (never committed real values)
```

**The loop contract (in CLAUDE.md):** every run = ① pull fresh metrics via tools → snapshot to `metrics/` → ② compare against last run's experiments in `memory.md` (did they work? keep winners, flag losers for revert) → ③ pick 1–3 highest-leverage next actions → ④ write proposals to `pending/` (and a PR branch on the project repo when it's a code change) → ⑤ append run summary to `memory.md` → ⑥ stop. Each run is shallow and cheap (~minutes); the loop is "infinite over time," not per-session.

**Slash commands (`.claude/skills/`):**
- `/intake-project` — interview → creates `projects/<slug>/` with chosen loops, walks through credential setup (GSC service account, DataForSEO, ad/social APIs) and verifies each connection.
- `/run-loop <project> <loop>` — executes one iteration per the contract.
- `/review-pending <project> <loop>` — walks you through pending proposals; approved ones get applied (PR merged / change pushed), decisions recorded in memory.

**Scheduling:** documented two ways, your choice at intake per loop — (a) Claude Code `/schedule` cloud routines (e.g. SEO weekly, content 2–3×/week, ads every 2–3 days), or (b) Windows Task Scheduler invoking `claude -p "/run-loop <project> <loop>"`. Runs end by writing a report you can read async (review-before-apply means nothing needs you mid-run).

## The three loop templates

Adapted from atom-eve agents (fetched from atomeve.dev during implementation):

1. **SEO loop** (from `seo-improver`) — Objective metric: Google rankings/clicks. Reads GSC API + DataForSEO; finds striking-distance keywords, weak titles/meta, cannibalization, decay; one high-leverage fix per opportunity; proposes PRs against the project's site repo; verifies last week's changes before making new ones. Cadence: weekly.
2. **Content/Social loop** (from `content-generator` + `postiz-social-scheduler` ideas) — Objective metric: impressions/engagement per post (and GSC clicks for blog content). Drafts blog posts / social posts to `pending/`, tracks how published pieces performed, doubles down on winning topics/hooks. Cadence: 2–3×/week.
3. **Ads loop** (from `ppc-assist`) — Objective metric: cost-per-result / ROAS from Meta & Google Ads APIs. Reviews variant performance, proposes copy variants, budget shifts, and kill-list for losers — all as pending proposals with projected impact. Includes a monthly spend guardrail stated in `project.md`. Cadence: every 2–3 days.

Each template has a `<!-- project-config -->` block (atom-eve's pattern) filled in by intake.

## Implementation steps

1. **Scaffold** the directory tree, `CLAUDE.md` (loop contract), `README.md`, `.env.example`, `git init` the workspace.
2. **Fetch & adapt atom-eve prompts** — pull the seo-improver, content-generator, and ppc-assist agent definitions from atomeve.dev; adapt into the three `templates/loops/*/instructions.md` (strip eve-runtime specifics, keep the loop logic, metrics definitions, and safety rules).
3. **Tools** — Node scripts in `tools/`: `gsc.mjs` (Search Console query), `dataforseo.mjs` (SERP/keyword data), `meta-ads.mjs`, `google-ads.mjs`, plus a shared `snapshot.mjs` that writes dated metric files. Node (not Python) to sidestep this machine's AVG TLS-interception issue. Each tool has a `--verify` mode for credential checks during intake.
4. **Skills** — write `/intake-project`, `/run-loop`, `/review-pending` as `.claude/skills/` markdown.
5. **Scheduling docs** — a `SCHEDULING.md` with both the `/schedule` routine setup and a ready-made Task Scheduler command per cadence.
6. **Dry-run verification** — create a `projects/_demo/` with a mock metrics snapshot and run `/run-loop _demo seo` end-to-end to prove the contract works (memory updated, pending proposal produced, report emitted) without any real credentials.

## Verification

- `node tools/gsc.mjs --verify` etc. run without crashing (graceful "no credentials" message pre-intake).
- Full dry run on `_demo` project: one loop iteration produces a metrics snapshot, a pending proposal, and a memory entry; a second iteration correctly reads the first run's memory and evaluates its "experiment."
- `/intake-project` walks through cleanly and generates a valid project folder.
- Real-world validation happens when you onboard your first real project: intake verifies live GSC/DataForSEO credentials before the first scheduled run.

## Out of scope (later)

- Product feedback loop (Google Reviews–driven) — the framework's structure already accommodates it as a fourth template.
- Codex as an alternate runner — instructions are plain markdown, so this is a docs exercise later.
- Auto-apply mode — everything stays review-before-apply until you loosen it per loop.

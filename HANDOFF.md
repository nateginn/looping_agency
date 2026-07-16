# Handoff — read this first in a new session

Read this before touching anything else. It replaces needing to scroll a long prior chat transcript. Canonical docs are `AgentColabPlan.md` (design), `PLAN-REVIEW-LOG.md` (plan review history), `RISK-REGISTER.md` (findings + risk acceptances), `CLAUDE.md` (operating instructions) — read those next if you need depth.

## Status as of 2026-07-16

**Phase 1 is implemented in Python, tested, committed (`8eef2bb`), and has a project-local venv.** The workspace was originally built in Node.js; that was a mistake — the user had told an earlier session to use Python, and the switch to Node was never actually re-confirmed with them (full account in `RISK-REGISTER.md` R8). The entire `tools/` directory was rewritten from `.mjs` to `.py` on 2026-07-16, same day the mismatch surfaced. **This project is Python-only from here forward — see `CLAUDE.md`'s "Implementation language" section and the `language-choice-approval` memory: never introduce a non-Python language without the user's explicit prior approval.**

### What's done

- Full port of every `tools/*.mjs` file to `tools/*.py`: `lib/paths.py`, `lib/redact.py`, `lib/lock.py`, `spec_validate.py`, `snapshot.py`, `mock_metrics.py`, `gsc.py`, `dataforseo.py`, `run_loop.py`, `review_pending.py`, `apply.py`, `watchdog.py`.
- `tools/tests/phase1_exit_criteria.py` — all 32 exit-criteria checks pass, plus every module's own `--verify` self-test passes.
- All `.mjs` files, `package.json`, `package-lock.json`, and the leftover `node_modules/` removed. Project is Node-free.
- `gsc.py`/`dataforseo.py` implement the real API calls (Search Analytics query, DataForSEO SERP) but keep the same safety gate as before — both refuse without an injected credential resolver, and `run_loop.py`'s `fetch_metrics` still only wires `mock_metrics.py`. Nothing in this codebase can reach a live API yet.
- `watchdog.py` — out-of-band scheduling watchdog (Tier 0, local-only, read-only), checks each active loop's expected run artifact exists for its cadence window. Has its own `--verify` self-test.
- `templates/loops/seo/intake-checklist.md` — Phase 2 intake checklist (what a human needs to supply before a real project's SEO loop can onboard). References updated to `.py` filenames.
- `_demo`'s run history was reset and regenerated end-to-end via the **Python** engine: two consecutive `run_loop.py _demo seo --scenario normal` runs with an approve+apply in between via `review_pending.py` / `apply.py`. Run 2 evaluates the applied proposal as a verified winner (position 6.1 → 6.1) and `applied/*.marker.json` exists correctly (not `pending/`).
- `CLAUDE.md`, all three skills (`.claude/skills/*/SKILL.md`), `AgentColabPlan.md`, and `templates/` had every Node reference (`node tools/*.mjs`) updated to the Python equivalent, invoked through the venv (`./.venv/Scripts/python.exe tools/*.py`).
- `RISK-REGISTER.md` R8 logs the full language-switch incident and its resolution. A `language-choice-approval` memory was saved to the operator's persistent memory system so this doesn't recur.
- **Commit `8eef2bb`** landed the full rewrite. Uncommitted since then: a project-local venv at `.venv/` (gitignored) with `requirements.txt` (`PyYAML==6.0.3` — the one dependency), plus the skill/template/CLAUDE.md updates pointing commands at `./.venv/Scripts/python.exe` instead of a bare `python`. All 32 exit-criteria checks re-verified passing through the venv interpreter.

### What's NOT done yet

1. **The venv (`.venv/` + `requirements.txt` + doc updates) is uncommitted.** The Python rewrite itself (`8eef2bb`) is already committed.
2. **Push to GitHub is blocked by the harness's own safety classifier** — a same-session `git remote add origin` + full-history push to a new public destination is a **hard block that no user consent can clear** (confirmed twice, including after explicit user approval). The user must run `git push -u origin master` themselves from their own terminal, or grant a standing Bash permission rule for `git push` if they want a future session able to do it. The remote is/was already configured: `origin` → `https://github.com/nateginn/looping_agency.git`. If a session before this one never got to `git remote add`, add it first.

## Exact next steps (in order)

1. `git status` — review the venv-related changes (`.gitignore`, `requirements.txt`, doc updates pointing commands at `.venv/Scripts/python.exe`) before committing.
2. Commit. Suggested scope: one commit for the venv addition.
3. Tell the user the push is ready and blocked on them (see above) — do not attempt to route around the classifier block.
4. Confirm the standing Python-only rule is being followed going forward — check `CLAUDE.md`'s "Implementation language" section and the `language-choice-approval` memory before writing any new tooling in this workspace.
5. Once Phase 1 is genuinely closed (committed + pushed): Phase 2 kickoff (a real project's live SEO loop) still requires the user's explicit go-ahead, per `CLAUDE.md`. The Phase 2 infra prep items (real connector implementations, intake checklist, watchdog) are already done as of this rewrite — no more infra prep authorized or needed beyond what's listed above unless the user asks for something new.
6. Update this file (`HANDOFF.md`) once the above is actually done, so it keeps reflecting reality rather than going stale.

## Hard boundaries (do not cross without the user present)

- **Never introduce a non-Python implementation language without the user's explicit prior approval** — this is the lesson of R8, and it's now a standing memory rule, not just a one-time fix.
- No real project intake (`/intake-project` against an actual client).
- No real credentials — nothing in Windows Credential Manager, nothing live-tested against GSC/DataForSEO.
- Never touch the user's website repo (separate from this workspace, lives under `Dev/`) — it auto-deploys on push with no staging gate, so any push there is Tier 2/human-only (see `RISK-REGISTER.md` R6).
- Phase 2's actual kickoff (a real project's live SEO loop) requires the user's explicit go-ahead, per `CLAUDE.md`.
- Don't attempt to work around a harness safety-classifier hard block (e.g. the git push block above) — stop and hand it to the user with a clear explanation.

## Fast sanity checks

```
./.venv/Scripts/python.exe tools/tests/phase1_exit_criteria.py   # should be all PASS, 32/32
git log --oneline                                                  # see what's committed
git status                                                          # should be clean once committed
git remote -v                                  # should show origin -> github.com/nateginn/looping_agency (after user pushes)
```

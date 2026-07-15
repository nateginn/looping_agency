# Loop Agency — operating instructions

This workspace runs durable business loops (build → verify against a real KPI → learn → repeat) for onboarded client projects. Full design and rationale: `AgentColabPlan.md`. Review history: `PLAN-REVIEW-LOG.md`. Open findings and risk acceptances: `RISK-REGISTER.md`. Read all three before making structural changes here.

## Phase status

**Phase 1 (framework + SEO, fully) is the current phase.** Content-social and ads loops exist only as draft specs (`templates/loops/{content-social,ads}/spec.md`) — no connectors are wired for them, and `run-loop.mjs` will refuse to run them. Do not begin Phase 2 (a real project's live SEO loop), touch the operator's website repo, or wire real credentials/live APIs without an explicit human instruction to do so.

## The run contract (every run, every loop)

1. Acquire the per-loop run lock (`tools/lib/lock.mjs`). A live lock refuses the run and logs the refusal to `lock-refusals.log`. A stale lock (dead PID or age > `max_run_duration_minutes`) is auto-recovered: archived under `runs/<old-run-id>/stale-lock.json`, never manually deleted.
2. Validate `spec.md`'s YAML frontmatter against the schema (`tools/spec-validate.mjs`). An invalid spec refuses to run before anything else happens.
3. Load `memory.md` and the pending proposals (the experiment register).
4. Pull metrics via the loop's connector; write an immutable, redacted, timestamped snapshot under `runs/<run-id>/snapshot.json` (`tools/snapshot.mjs` — read-only file mode).
5. Evaluate prior `applied` proposals whose observation window has elapsed and sample size is sufficient. A guardrail breach halts the loop (`state.json: paused-breach`) and blocks all new proposals until a human resolves it via `/review-pending --resolve-breach`.
6. Cooldown: no new proposal targets a page/asset that already has an `applied` proposal still inside its observation window.
7. Pick up to 3 new proposals from `allowed_actions` (skipped entirely if `paused-breach`); write them to `pending/` as `draft`.
8. Write `runs/<run-id>/run.json` (structured, redacted) + `report.md` (human-readable); append one summary line to `memory.md` (a derived view — `runs/` is the system of record). Release the lock.

**Approval state machine:** `draft → reviewed → approved → applied → verified` (or `rejected` / breach → revert proposal). Only `/review-pending` + explicit human approval can move a proposal to `approved`; only `tools/apply.mjs`, and only on an `approved` Tier ≤ 1 proposal, performs `applied`. Tier 2 proposals are never applied by tooling — human-only, always, including any `git push` to a repo that auto-deploys with no staging gate (see RISK-REGISTER.md R6).

## Tools (`tools/`)

- `spec-validate.mjs` — schema validator, `--verify` self-test.
- `lib/lock.mjs` — run lock + stale-lock recovery, `--verify` self-test.
- `lib/redact.mjs` — alias-based + pattern-based secret redaction, `--verify` self-test.
- `lib/paths.mjs` — canonical (symlink-resolved) path containment checks; every project/loop path is checked with `assertWithin` before use.
- `snapshot.mjs` — immutable redacted metrics snapshot writer, `--verify` self-test.
- `mock-metrics.mjs` — synthetic connector for `_demo` only; supports `normal`/`breach`/`fail` scenarios. Never calls a real API.
- `gsc.mjs`, `dataforseo.mjs` — real connector **contracts** only in Phase 1: they refuse to run without an injected credential resolver and are not wired to any live API yet (Phase 1(b) connectors-only smoke test is deferred).
- `run-loop.mjs` — the run contract engine described above.
- `review-pending.mjs` — approve/reject/list proposals, resolve a breach.
- `apply.mjs` — performs the `applied` transition; re-checks approval and tier itself regardless of caller.

## Skills (`.claude/skills/`)

`/intake-project`, `/run-loop`, `/review-pending` — see each `SKILL.md` for exact steps. None of them bypass the tools above; they orchestrate and summarize for the human.

## Secrets & tenant isolation

Windows Credential Manager is the default secret store; per-project `.env` is an explicit fallback only, requires restricted ACLs, and the runner must refuse to start against an over-broad `.env`. Repo files store only opaque aliases, never raw values. A run may only read/write inside `projects/<slug>/` and its declared repo, checked via resolved canonical paths (`tools/lib/paths.mjs`) — never naive prefix matching.

## Debugging

Never rely on model memory to reconstruct what a run did — `runs/<run-id>/run.json` is the observability contract. If something looks wrong, read that file (and `snapshot.json`, `report.md`) before speculating.

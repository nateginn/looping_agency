# Phase 3 scheduling commands

These are the ready-to-run human commands for (re-)registering the workspace's
scheduled jobs. The four tasks below are **registered and live** as of
2026-08-10; the commands are kept here so the exact registration is
reproducible and version-controlled.

Every task runs through `tools/scheduled/task.cmd`, which resolves the
workspace root from its own location, pins the `.venv` interpreter, and appends
stdout+stderr to `logs/<name>.log` (rotating at 2 MB). It exits with the
child's exit code so Task Scheduler's "Last Run Result" stays meaningful.

> **Why the wrapper, and what it fixed (2026-08-10).** The original
> registrations put `>> <log> 2>&1` directly inside `schtasks /TR`. Task
> Scheduler launches the executable directly rather than through a shell, so
> the redirection was never interpreted — those tokens were handed to
> `python.exe` as literal arguments and **no log file was ever created**. Weeks
> of scheduled runs produced no `scheduler.log`, no `watchdog.log`, and no
> captured stderr. `run_loop.py` still wrote its own `runs/<id>/run.json`, so
> loop runs were observable anyway; the watchdog, which only ever writes to
> stdout, was reporting into nothing at all.

## Registered tasks

`tools/run_loop.py art seo` — weekly Monday 06:00 local:

```powershell
$wrapper = "D:\Dev\Looping _agency\tools\scheduled\task.cmd"
schtasks /Create /TN "LoopAgency-Art-SEO" /SC WEEKLY /D MON /ST 06:00 /F /TR "\"$wrapper\" art-seo-full tools\run_loop.py art seo"
```

`tools/run_loop.py art seo --run-name technical-thursday` — weekly Thursday 06:00 local:

```powershell
schtasks /Create /TN "LoopAgency-Art-SEO-Technical" /SC WEEKLY /D THU /ST 06:00 /F /TR "\"$wrapper\" art-seo-technical tools\run_loop.py art seo --run-name technical-thursday"
```

`tools/run_loop.py art seo --run-name daily-rank-check` — daily **07:00** local
(DataForSEO local-rank only, no proposal generation):

```powershell
schtasks /Create /TN "LoopAgency-Art-SEO-DailyRank" /SC DAILY /ST 07:00 /F /TR "\"$wrapper\" art-seo-daily-rank tools\run_loop.py art seo --run-name daily-rank-check"
```

> **Why 07:00 and not 06:00 (2026-08-10).** The daily job originally fired at
> 06:00, the same minute as the Monday full run and the Thursday technical run.
> All three contend for the same per-loop `run.lock`, so on Mondays and
> Thursdays the daily rank check lost the race and was refused outright —
> visible only as a line in `lock-refusals.log` (confirmed on 2026-08-06 and
> 2026-08-10). That silently dropped 2 of 7 daily rank checks every week.
> One hour is ample headroom: observed full runs complete in 2–4 minutes, and
> the lock's own TTL ceiling is `max_run_duration_minutes: 30`.

`tools/watchdog.py` — daily **07:30** local:

```powershell
schtasks /Create /TN "LoopAgency-Watchdog" /SC DAILY /ST 07:30 /F /TR "\"$wrapper\" watchdog tools\watchdog.py"
```

> **Why 07:30.** It was 06:15, which put it *before* the day's loop run, so it
> could only ever assess yesterday. Running it after the last scheduled job
> means it checks the day that just happened.

## Cost note

Each of the 12 targets is pinned to its matching city (`location:` field in
`spec.md`, added 2026-07-26), so the daily job is 12 SERP calls/day, not the
original 3-location × 12-keyword cross-product (36). At DataForSEO Live pricing
($0.002 base × depth/10 = $0.02/call at depth=100, confirmed against
DataForSEO's own pricing docs), that's ~$0.24/day, ~$7.20/month. Confirmed with
Nate 2026-07-25/26 as worth it for daily rank-freshness confirmation.

## Notes

- The wrapper preserves the pinned-interpreter requirement from
  `AgentColabPlan.md` by invoking `.venv\Scripts\python.exe` directly rather
  than a bare `python`.
- `logs/` is gitignored — it holds scheduler transcripts, not run artifacts.
  `runs/<run-id>/run.json` remains the observability contract for what a run
  actually did (see `CLAUDE.md` "Debugging"); these logs exist to explain a run
  that never got far enough to write one.
- To inspect the live definitions:
  `Get-ScheduledTask -TaskName 'LoopAgency-*' | ForEach-Object { $_.TaskName; $_.Actions }`

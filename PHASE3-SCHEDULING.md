# Phase 3 scheduling commands

Do not run these automatically from the repo. They are the ready-to-run human commands for later manual registration.

`tools/run_loop.py art seo` weekly Monday 06:00 local:

```powershell
schtasks /Create /TN "LoopAgency-Art-SEO" /SC WEEKLY /D MON /ST 06:00 /TR "\"D:\Dev\Looping _agency\.venv\Scripts\python.exe\" \"D:\Dev\Looping _agency\tools\run_loop.py\" art seo >> \"D:\Dev\Looping _agency\projects\art\loops\seo\runs\scheduler.log\" 2>&1" /F
```

`tools/run_loop.py art seo --run-name technical-thursday` weekly Thursday 06:00 local:

```powershell
schtasks /Create /TN "LoopAgency-Art-SEO-Technical" /SC WEEKLY /D THU /ST 06:00 /TR "\"D:\Dev\Looping _agency\.venv\Scripts\python.exe\" \"D:\Dev\Looping _agency\tools\run_loop.py\" art seo --run-name technical-thursday >> \"D:\Dev\Looping _agency\projects\art\loops\seo\runs\scheduler-technical.log\" 2>&1" /F
```

`tools/run_loop.py art seo --run-name daily-rank-check` daily 06:00 local (DataForSEO local-rank only, no proposal generation, no watchdog implications):

```powershell
schtasks /Create /TN "LoopAgency-Art-SEO-DailyRank" /SC DAILY /ST 06:00 /TR "\"D:\Dev\Looping _agency\.venv\Scripts\python.exe\" \"D:\Dev\Looping _agency\tools\run_loop.py\" art seo --run-name daily-rank-check >> \"D:\Dev\Looping _agency\projects\art\loops\seo\runs\scheduler-daily-rank.log\" 2>&1" /F
```

Cost note: each of the 12 targets is now pinned to its matching city (`location:` field in `spec.md`, added 2026-07-26), so this is 12 SERP calls/day, not the original 3-location x 12-keyword cross-product (36). At DataForSEO Live pricing ($0.002 base x depth/10 = $0.02/call at depth=100, confirmed against DataForSEO's own pricing docs), that's ~$0.24/day, ~$7.20/month. Confirmed with Nate 2026-07-25/26 as worth it for daily rank-freshness confirmation.

`tools/watchdog.py` daily 06:15 local:

```powershell
schtasks /Create /TN "LoopAgency-Watchdog" /SC DAILY /ST 06:15 /TR "\"D:\Dev\Looping _agency\.venv\Scripts\python.exe\" \"D:\Dev\Looping _agency\tools\watchdog.py\" >> \"D:\Dev\Looping _agency\projects\art\loops\seo\runs\watchdog.log\" 2>&1" /F
```

These commands preserve the pinned workspace interpreter requirement from `AgentColabPlan.md` by invoking `.venv\Scripts\python.exe` directly rather than a bare `python`.

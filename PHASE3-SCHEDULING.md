# Phase 3 scheduling commands

Do not run these automatically from the repo. They are the ready-to-run human commands for later manual registration.

`tools/run_loop.py art seo` weekly Monday 06:00 local:

```powershell
schtasks /Create /TN "LoopAgency-Art-SEO" /SC WEEKLY /D MON /ST 06:00 /TR "\"D:\Dev\Looping _agency\.venv\Scripts\python.exe\" \"D:\Dev\Looping _agency\tools\run_loop.py\" art seo >> \"D:\Dev\Looping _agency\projects\art\loops\seo\runs\scheduler.log\" 2>&1" /F
```

`tools/watchdog.py` daily 06:15 local:

```powershell
schtasks /Create /TN "LoopAgency-Watchdog" /SC DAILY /ST 06:15 /TR "\"D:\Dev\Looping _agency\.venv\Scripts\python.exe\" \"D:\Dev\Looping _agency\tools\watchdog.py\" >> \"D:\Dev\Looping _agency\projects\art\loops\seo\runs\watchdog.log\" 2>&1" /F
```

These commands preserve the pinned workspace interpreter requirement from `AgentColabPlan.md` by invoking `.venv\Scripts\python.exe` directly rather than a bare `python`.

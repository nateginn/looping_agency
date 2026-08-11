@echo off
REM Wrapper for every Windows Task Scheduler job in this workspace.
REM
REM Why this file exists: the original registrations put `>> log 2>&1` directly
REM into `schtasks /TR`. Task Scheduler launches the program directly rather
REM than through a shell, so the redirection was never interpreted - it was
REM passed to python.exe as literal arguments and no log file was ever created
REM (confirmed 2026-08-10: scheduler.log/watchdog.log did not exist after weeks
REM of runs). The watchdog writes nothing but stdout, so its output went
REM nowhere at all. Redirection needs a shell; this .cmd is that shell, and it
REM lives in the repo so the exact scheduled invocation is version-controlled
REM instead of buried in a task definition.
REM
REM Usage:  task.cmd <log-basename> <script-relative-to-workspace-root> [args...]
REM   task.cmd art-seo-full        tools\run_loop.py art seo
REM   task.cmd art-seo-daily-rank  tools\run_loop.py art seo --run-name daily-rank-check
REM   task.cmd watchdog            tools\watchdog.py
REM
REM Exits with the child's exit code so Task Scheduler's "Last Run Result"
REM stays meaningful (0 = clean, non-zero = the job itself reported a problem).

setlocal EnableExtensions

set "ROOT=%~dp0..\.."

set "LOGNAME=%~1"
if "%LOGNAME%"=="" (
    echo task.cmd: a log basename is required as the first argument 1>&2
    exit /b 2
)
shift

set "SCRIPT=%~1"
if "%SCRIPT%"=="" (
    echo task.cmd: a script path is required as the second argument 1>&2
    exit /b 2
)
shift

REM `shift` renumbers %1..%9 but deliberately does NOT rewrite %*, so the
REM remaining arguments have to be re-collected by hand. Unquoted %1 is used so
REM an argument that was quoted by the caller stays quoted.
set "ARGS="
:collect_args
if "%~1"=="" goto args_collected
set "ARGS=%ARGS% %1"
shift
goto collect_args
:args_collected

set "PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo task.cmd: venv interpreter not found at "%PY%" - run pip install -r requirements.txt 1>&2
    exit /b 2
)

if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"
set "LOG=%ROOT%\logs\%LOGNAME%.log"

REM Rotate at ~2 MB so an unattended job can't fill the disk over months.
REM One generation back is enough to debug the run that just failed.
if exist "%LOG%" for %%F in ("%LOG%") do if %%~zF GTR 2097152 move /y "%LOG%" "%LOG%.1" >nul

echo.>> "%LOG%"
echo ==== %DATE% %TIME% : %SCRIPT%%ARGS% >> "%LOG%"
"%PY%" "%ROOT%\%SCRIPT%"%ARGS% >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo ==== exit %RC% >> "%LOG%"

exit /b %RC%

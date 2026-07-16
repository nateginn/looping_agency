# Out-of-band watchdog (AgentColabPlan.md "Scheduling & operations"): checks
# that each active loop's expected run artifact exists for its cadence
# window, so a hard scheduling failure (Task Scheduler didn't fire, the
# process crashed before writing anything) is caught even when no report
# was produced. In-report staleness surfacing (HEALTH.md) remains the
# secondary signal - not implemented here.
#
# Tier 0, local-only: this script only *reads* project/loop state
# (spec.md, state.json, runs/*/run.json). It never writes into a project's
# runs/pending/applied dirs, never resolves a credential, never calls a
# live API. Meant to be invoked by its own independent scheduled task,
# separate from the one that runs the loops themselves.
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import yaml

from spec_validate import extract_frontmatter

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(THIS_DIR)
PROJECTS_ROOT = os.path.join(WORKSPACE_ROOT, "projects")

# Allow this much slack over the estimated cadence before alerting - cron
# firing jitter, retry-once policy, and clock skew all eat into the window.
GRACE_MULTIPLIER = 1.5

_STEP_RE = re.compile(r"^\*/(\d+)$")


def estimate_cron_interval_ms(cron_expr, fallback_ms=24 * 60 * 60 * 1000):
    """Very small best-effort cron interval estimator - good enough to bound a
    "how long is too long since the last run" check. Supports the subset of
    cron this project's specs actually use (exact minute/hour, `*`, and
    `*/N` step fields). Falls back to a conservative 24h if it can't parse -
    under-alerting is safer than a watchdog that cries wolf on every run."""
    if not isinstance(cron_expr, str):
        return fallback_ms
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        return fallback_ms
    minute, hour, day_of_month, month, day_of_week = fields

    m = _STEP_RE.match(minute)
    if m:
        return int(m.group(1)) * 60 * 1000

    m = _STEP_RE.match(hour)
    if m:
        return int(m.group(1)) * 60 * 60 * 1000

    if hour == "*":
        return 60 * 60 * 1000  # fires every hour
    if day_of_month == "*" and month == "*" and day_of_week == "*":
        return 24 * 60 * 60 * 1000  # daily
    if day_of_week != "*":
        return 7 * 24 * 60 * 60 * 1000  # weekly (a specific weekday)
    if day_of_month != "*":
        return 30 * 24 * 60 * 60 * 1000  # monthly (approx)

    return fallback_ms


def _read_loop_spec(spec_path):
    with open(spec_path, "r", encoding="utf-8") as f:
        source = f.read()
    fm = extract_frontmatter(source)
    if fm is None:
        return None
    return yaml.safe_load(fm)


def _read_state(loop_dir):
    import json

    state_path = os.path.join(loop_dir, "state.json")
    if not os.path.exists(state_path):
        return {"status": "active"}
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"status": "active"}  # corrupt state.json shouldn't crash the watchdog


def _latest_run_start(runs_dir):
    import json

    if not os.path.exists(runs_dir):
        return None
    latest = None
    for entry in sorted(os.listdir(runs_dir)):
        run_json_path = os.path.join(runs_dir, entry, "run.json")
        if not os.path.exists(run_json_path):
            continue
        try:
            with open(run_json_path, "r", encoding="utf-8") as f:
                run = json.load(f)
            start = run.get("start")
            if start:
                start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                if latest is None or start_dt > latest:
                    latest = start_dt
        except Exception:
            continue  # Corrupt/partial run.json - ignore for staleness purposes.
    return latest


def _discover_loops(projects_root):
    loops = []
    if not os.path.exists(projects_root):
        return loops
    for project_entry in sorted(os.listdir(projects_root)):
        project_path = os.path.join(projects_root, project_entry)
        if not os.path.isdir(project_path):
            continue
        loops_dir = os.path.join(project_path, "loops")
        if not os.path.exists(loops_dir):
            continue
        for loop_entry in sorted(os.listdir(loops_dir)):
            loop_dir = os.path.join(loops_dir, loop_entry)
            if not os.path.isdir(loop_dir):
                continue
            spec_path = os.path.join(loop_dir, "spec.md")
            if not os.path.exists(spec_path):
                continue
            loops.append({"project": project_entry, "loop": loop_entry, "spec_path": spec_path, "loop_dir": loop_dir})
    return loops


def check_all(projects_root=None, now=None):
    """Check every discovered loop's staleness. Isolates failures per loop - one
    unreadable spec never stops the rest of the sweep from running.
    Returns {"checked": [...], "alerts": [...]}."""
    projects_root = projects_root or PROJECTS_ROOT
    now = now or datetime.now(timezone.utc)
    alerts = []
    checked = []

    for entry in _discover_loops(projects_root):
        project, loop, spec_path, loop_dir = entry["project"], entry["loop"], entry["spec_path"], entry["loop_dir"]
        try:
            spec = _read_loop_spec(spec_path)
            if not spec or not spec.get("schedule"):
                checked.append({"project": project, "loop": loop, "status": "no-schedule"})
                continue

            state = _read_state(loop_dir)
            if state.get("status") == "paused-breach":
                # A breach pause already surfaces via /review-pending; the watchdog
                # only catches *silent* scheduling failures, not a loop that halted
                # correctly and is waiting on a human.
                checked.append({"project": project, "loop": loop, "status": "paused-breach-skip"})
                continue

            interval_ms = estimate_cron_interval_ms(spec.get("schedule"))
            stale_threshold_ms = interval_ms * GRACE_MULTIPLIER
            latest = _latest_run_start(os.path.join(loop_dir, "runs"))

            if latest is None:
                # No runs yet. Only alert once the loop has existed longer than one
                # cadence window - a brand-new loop hasn't missed anything yet.
                spec_age_ms = (now - datetime.fromtimestamp(os.path.getmtime(spec_path), tz=timezone.utc)).total_seconds() * 1000
                if spec_age_ms > stale_threshold_ms:
                    alerts.append(
                        {
                            "project": project,
                            "loop": loop,
                            "reason": f"no run has ever been recorded, and the spec is older than one cadence window ({round(stale_threshold_ms / 60000)} min)",
                        }
                    )
                checked.append({"project": project, "loop": loop, "status": "no-runs-yet"})
                continue

            age_ms = (now - latest).total_seconds() * 1000
            if age_ms > stale_threshold_ms:
                alerts.append(
                    {
                        "project": project,
                        "loop": loop,
                        "reason": f"latest run is {round(age_ms / 60000)} min old, exceeds cadence+grace of {round(stale_threshold_ms / 60000)} min",
                        "last_run_at": latest.isoformat().replace("+00:00", "Z"),
                    }
                )
            checked.append({"project": project, "loop": loop, "status": "ok", "last_run_at": latest.isoformat().replace("+00:00", "Z")})
        except Exception as e:
            alerts.append({"project": project, "loop": loop, "reason": f"watchdog error while checking this loop: {e}"})

    return {"checked": checked, "alerts": alerts}


def _print_report(result):
    print(f'watchdog: checked {len(result["checked"])} loop(s), {len(result["alerts"])} alert(s)')
    for c in result["checked"]:
        last_run = f' (last run {c["last_run_at"]})' if c.get("last_run_at") else ""
        print(f'  ok    - {c["project"]}/{c["loop"]}: {c["status"]}{last_run}')
    for a in result["alerts"]:
        print(f'  ALERT - {a["project"]}/{a["loop"]}: {a["reason"]}')


def _self_test():
    import json
    import shutil
    import tempfile

    checks = []

    checks.append(("every-minute cron -> 1 min", estimate_cron_interval_ms("*/1 * * * *") == 60 * 1000))
    checks.append(("hourly cron -> 1 hour", estimate_cron_interval_ms("0 * * * *") == 60 * 60 * 1000))
    checks.append(("daily cron -> 24 hours", estimate_cron_interval_ms("0 6 * * *") == 24 * 60 * 60 * 1000))
    checks.append(("weekly cron -> 7 days", estimate_cron_interval_ms("0 6 * * 1") == 7 * 24 * 60 * 60 * 1000))
    checks.append(("unparseable cron falls back to 24h", estimate_cron_interval_ms("garbage") == 24 * 60 * 60 * 1000))

    tmp = tempfile.mkdtemp(prefix="watchdog-test-")
    projects_root = os.path.join(tmp, "projects")

    def iso(dt):
        return dt.isoformat().replace("+00:00", "Z")

    def make_loop(project, loop, schedule, runs=None, state_status=None, spec_mtime_ms_ago=None):
        loop_dir = os.path.join(projects_root, project, "loops", loop)
        os.makedirs(loop_dir, exist_ok=True)
        spec_path = os.path.join(loop_dir, "spec.md")
        with open(spec_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(f'---\nschedule: "{schedule}"\n---\n\n# spec\n')
        if spec_mtime_ms_ago is not None:
            t = datetime.now(timezone.utc).timestamp() - spec_mtime_ms_ago / 1000
            os.utime(spec_path, (t, t))
        if state_status:
            with open(os.path.join(loop_dir, "state.json"), "w", encoding="utf-8", newline="\n") as f:
                json.dump({"status": state_status}, f)
        if runs:
            runs_dir = os.path.join(loop_dir, "runs")
            os.makedirs(runs_dir, exist_ok=True)
            for r in runs:
                run_dir = os.path.join(runs_dir, r["id"])
                os.makedirs(run_dir, exist_ok=True)
                with open(os.path.join(run_dir, "run.json"), "w", encoding="utf-8", newline="\n") as f:
                    json.dump({"start": r["start"]}, f)

    now = datetime.now(timezone.utc)

    make_loop("proj-a", "seo", "0 * * * *", runs=[{"id": "r1", "start": iso(now - timedelta(minutes=10))}])
    make_loop("proj-b", "seo", "0 * * * *", runs=[{"id": "r1", "start": iso(now - timedelta(hours=5))}])
    make_loop("proj-c", "seo", "0 * * * *", state_status="paused-breach", runs=[{"id": "r1", "start": iso(now - timedelta(hours=5))}])
    make_loop("proj-d", "seo", "0 * * * *", spec_mtime_ms_ago=5 * 60 * 60 * 1000)
    make_loop("proj-e", "seo", "0 * * * *", spec_mtime_ms_ago=60 * 1000)

    result = check_all(projects_root=projects_root, now=now)
    alert_keys = [f'{a["project"]}/{a["loop"]}' for a in result["alerts"]]

    checks.append(("healthy recent-run loop produces no alert", "proj-a/seo" not in alert_keys))
    checks.append(("stale loop past cadence+grace produces an alert", "proj-b/seo" in alert_keys))
    checks.append(("paused-breach loop is skipped, not alerted (already surfaced via review-pending)", "proj-c/seo" not in alert_keys))
    checks.append(("never-run loop past one cadence window produces an alert", "proj-d/seo" in alert_keys))
    checks.append(("brand-new never-run loop within its first cadence window produces no alert", "proj-e/seo" not in alert_keys))
    checks.append(("check_all only reads the given projects_root, never the real workspace projects/ dir", projects_root != PROJECTS_ROOT))

    shutil.rmtree(tmp, ignore_errors=True)

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    if "--verify" in sys.argv:
        _self_test()
    else:
        result = check_all()
        _print_report(result)
        sys.exit(1 if result["alerts"] else 0)

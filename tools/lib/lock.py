# Per-loop run lock: refuse to start if a live lock exists; recover
# automatically from stale locks (dead PID or age > max_run_duration).
import errno
import json
import os
import random
import string
import sys
import time
from datetime import datetime, timezone


def _is_alive(pid):
    """Cross-platform liveness check. Never sends a real signal on Windows -
    os.kill(pid, 0) on win32 actually terminates the process, so this uses
    OpenProcess via ctypes there instead."""
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but we lack permission -> treat as alive
    return True


def lock_path_for(loop_dir):
    return os.path.join(loop_dir, "run.lock")


def make_run_id(now=None):
    now = now or datetime.now(timezone.utc)
    iso = now.strftime("%Y-%m-%dT%H-%M-%S-%f")[:-3] + "Z"
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{iso}-{rand}"


def acquire_lock(loop_dir, max_run_duration_minutes=None, runs_dir=None, now=None):
    """Returns {"acquired": True, "run_id": ..., "stale_recovered": {...}|None}
    or {"acquired": False, "reason": ..., "held_by": {...}}."""
    now = now or datetime.now(timezone.utc)
    lock_path = lock_path_for(loop_dir)
    stale_recovered = None

    if os.path.exists(lock_path):
        with open(lock_path, "r", encoding="utf-8") as f:
            held = json.load(f)
        start_time = datetime.fromisoformat(held["startTime"].replace("Z", "+00:00"))
        age_ms = (now - start_time).total_seconds() * 1000
        max_ms = (max_run_duration_minutes if max_run_duration_minutes is not None else 60) * 60 * 1000
        alive = _is_alive(held["pid"])
        stale = (not alive) or age_ms > max_ms

        if not stale:
            return {
                "acquired": False,
                "reason": f'active lock held by pid {held["pid"]} (run {held["runId"]}) since {held["startTime"]}',
                "held_by": held,
            }

        # Stale: archive the old lock for audit, then proceed to acquire fresh.
        archive_dir = os.path.join(runs_dir or os.path.join(loop_dir, "runs"), held.get("runId", "unknown-run"))
        os.makedirs(archive_dir, exist_ok=True)
        archived = dict(held)
        archived["staleReason"] = "pid-not-alive" if not alive else "age-exceeded-max-run-duration"
        archived["recoveredAt"] = now.isoformat().replace("+00:00", "Z")
        with open(os.path.join(archive_dir, "stale-lock.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(archived, f, indent=2)
        os.remove(lock_path)
        stale_recovered = held

    run_id = make_run_id(now)
    record = {"runId": run_id, "pid": os.getpid(), "startTime": now.isoformat().replace("+00:00", "Z")}
    # Exclusive create - races with another process are still refused, not clobbered.
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        # Lost a race with a concurrent acquirer between the exists-check above and here.
        with open(lock_path, "r", encoding="utf-8") as f:
            held = json.load(f)
        return {
            "acquired": False,
            "reason": f'active lock held by pid {held["pid"]} (run {held["runId"]}) since {held["startTime"]}',
            "held_by": held,
        }
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(record, f, indent=2)
    return {"acquired": True, "run_id": run_id, "stale_recovered": stale_recovered}


def release_lock(loop_dir, run_id):
    lock_path = lock_path_for(loop_dir)
    if not os.path.exists(lock_path):
        return
    with open(lock_path, "r", encoding="utf-8") as f:
        held = json.load(f)
    if held.get("runId") == run_id:
        os.remove(lock_path)


def log_refusal(loop_dir, reason):
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    line = f"{now} REFUSED: {reason}\n"
    with open(os.path.join(loop_dir, "lock-refusals.log"), "a", encoding="utf-8", newline="\n") as f:
        f.write(line)


def _self_test():
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="lock-test-")
    results = []

    # 1. Fresh acquire succeeds.
    a = acquire_lock(tmp, max_run_duration_minutes=60)
    results.append(("fresh lock acquires", a["acquired"] is True))

    # 2. Second acquire while held is refused.
    b = acquire_lock(tmp, max_run_duration_minutes=60)
    results.append(("concurrent acquire refused", b["acquired"] is False))

    release_lock(tmp, a["run_id"])
    results.append(("release removes lockfile", not os.path.exists(lock_path_for(tmp))))

    # 3. Stale lock (dead pid) is recovered automatically.
    with open(lock_path_for(tmp), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"runId": "dead-run", "pid": 999999, "startTime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}, f)
    c = acquire_lock(tmp, max_run_duration_minutes=60, runs_dir=os.path.join(tmp, "runs"))
    results.append(("dead-pid lock recovered as stale", c["acquired"] is True and c["stale_recovered"] is not None))
    results.append(("stale lock archived for audit", os.path.exists(os.path.join(tmp, "runs", "dead-run", "stale-lock.json"))))
    release_lock(tmp, c["run_id"])

    # 4. Stale lock (own pid, but age exceeded) is recovered.
    old_start = datetime.now(timezone.utc).timestamp() - 999 * 60
    old_start_iso = datetime.fromtimestamp(old_start, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    with open(lock_path_for(tmp), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"runId": "old-run", "pid": os.getpid(), "startTime": old_start_iso}, f)
    d = acquire_lock(tmp, max_run_duration_minutes=30, runs_dir=os.path.join(tmp, "runs"))
    results.append(("aged-out lock recovered as stale", d["acquired"] is True and d["stale_recovered"] is not None))
    release_lock(tmp, d["run_id"])

    shutil.rmtree(tmp, ignore_errors=True)

    failed = 0
    for name, ok in results:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__" and "--verify" in sys.argv:
    _self_test()

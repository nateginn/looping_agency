# Writes an immutable, timestamped metrics snapshot under runs/<run-id>/.
# Immutability is enforced with a read-only file mode after write - the
# run engine never edits a snapshot once written, only reads it back.
import json
import os
import stat
import sys

try:
    from .lib.redact import redact_deep
except ImportError:
    from lib.redact import redact_deep


def write_snapshot(run_dir, metrics, secret_map=None):
    secret_map = secret_map or {}
    os.makedirs(run_dir, exist_ok=True)
    snapshot_path = os.path.join(run_dir, "snapshot.json")
    redacted = redact_deep(metrics, secret_map)
    with open(snapshot_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(redacted, f, indent=2)
    os.chmod(snapshot_path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    return snapshot_path


def _self_test():
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="snapshot-test-")
    secret_map = {"alias1": "super-secret-value"}
    metrics = {"note": "token super-secret-value here", "nested": {"again": "super-secret-value"}}

    p = write_snapshot(os.path.join(tmp, "runs", "run-1"), metrics, secret_map)
    with open(p, "r", encoding="utf-8") as f:
        written = f.read()
    mode = os.stat(p).st_mode

    checks = [
        ("snapshot file created", os.path.exists(p)),
        ("snapshot redacted secret before write", "super-secret-value" not in written),
        ("snapshot is read-only (immutable)", not (mode & stat.S_IWRITE)),
    ]

    os.chmod(p, stat.S_IWRITE | stat.S_IREAD)  # allow cleanup
    shutil.rmtree(tmp, ignore_errors=True)

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__" and "--verify" in sys.argv:
    _self_test()

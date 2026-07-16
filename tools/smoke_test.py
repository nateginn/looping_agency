# Connectors-only smoke test (Phase 2 checklist section 5). For each loop of a
# project: resolve every credential alias in spec.inputs (reporting which store
# answered - never the value), call each connector live exactly once, and print
# a redacted summary (auth OK/failed, HTTP status, row count). Also runs a
# deliberate bad-alias simulation proving a missing credential fails cleanly.
#
# This tool is NOT a run: it writes nothing under runs/ (or anywhere else),
# takes no lock, and creates no proposals. A human reviews its output before
# the loop's first real run.
import json
import os
import sys

try:
    from . import dataforseo, gsc
    from .lib.credentials import CredentialError, resolve_with_source
    from .lib.paths import assert_within
    from .lib.redact import redact_text
    from .mock_metrics import pull_metrics as pull_mock_metrics
    from .spec_validate import validate_spec_file, extract_frontmatter
except ImportError:
    import dataforseo
    import gsc
    from lib.credentials import CredentialError, resolve_with_source
    from lib.paths import assert_within
    from lib.redact import redact_text
    from mock_metrics import pull_metrics as pull_mock_metrics
    from spec_validate import validate_spec_file, extract_frontmatter

from datetime import datetime, timedelta, timezone

import yaml

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(THIS_DIR)
PROJECTS_ROOT = os.path.join(WORKSPACE_ROOT, "projects")

BOGUS_ALIAS = "smoke-test-bogus-alias-do-not-create"


def _capture(http_post):
    """Wrap an http_post callable so the last HTTP status is observable even
    when the connector raises on it."""
    record = {}

    def wrapped(url, headers, body_bytes):
        status, reason, raw = http_post(url, headers, body_bytes)
        record["status"] = status
        record["reason"] = reason
        return status, reason, raw

    return wrapped, record


def _status_note(record):
    if "status" not in record:
        return ""
    reason = record.get("reason") or ""
    return f" (HTTP {record['status']}{' ' + reason if reason else ''})"


def _check_connector(input_name, spec, project_dir, resolve_fn, http_post, lines):
    """Resolve + call one connector. Appends redacted summary lines; returns True on success."""
    if input_name == "mock":
        metrics = pull_mock_metrics(scenario="normal", credential_alias=(spec.get("credential_aliases") or {}).get("mock", "demo-gsc-readonly"))
        lines.append(f"  mock: OK (local synthetic connector, no API), {len(metrics['keywords'])} row(s)")
        return True

    alias = (spec.get("credential_aliases") or {}).get(input_name)
    try:
        value, source = resolve_fn(alias, project_dir)
    except CredentialError as e:
        lines.append(f'  {input_name}: FAIL - credential not resolved: {e}')
        return False
    secret_map = {alias: value}
    lines.append(f'  {input_name}: alias "{alias}" resolved via {source} (value not shown)')

    try:
        if input_name == "gsc":
            wrapped, record = _capture(http_post or gsc._default_http_post)
            window_days = spec.get("metrics_window_days") or 28
            end_date = datetime.now(timezone.utc).date()
            metrics = gsc.pull_metrics(
                credential_alias=alias,
                resolve_credential=lambda _a: value,
                site_url=spec.get("site_url"),
                # GSC date ranges are inclusive of both endpoints: a 28-day window is end - 27.
                start_date=(end_date - timedelta(days=window_days - 1)).isoformat(),
                end_date=end_date.isoformat(),
                http_post=wrapped,
            )
        elif input_name == "dataforseo":
            wrapped, record = _capture(http_post or dataforseo._default_http_post)
            metrics = dataforseo.pull_metrics(
                credential_alias=alias,
                resolve_credential=lambda _a: value,
                targets=spec.get("targets"),
                location_code=spec.get("location_code") or 2840,
                language_code=spec.get("language_code") or "en",
                device=spec.get("device") or "desktop",
                http_post=wrapped,
            )
        else:
            lines.append(f'  {input_name}: FAIL - no connector wired for this input (known: mock, gsc, dataforseo)')
            return False
    except Exception as e:
        # Connectors redact their own messages; redact again here as belt-and-braces.
        lines.append(f"  {input_name}: FAIL - auth/call failed{_status_note(record)}: {redact_text(str(e), secret_map)}")
        return False

    rows = len(metrics.get("keywords") or [])
    lines.append(f"  {input_name}: auth OK{_status_note(record)}, {rows} row(s)")
    return True


def _bad_alias_simulation(project_dir, resolve_fn, lines):
    """Deliberately resolve a nonexistent alias to prove a missing/bad
    credential produces a clean, named, value-free error - not a crash."""
    try:
        resolve_fn(BOGUS_ALIAS, project_dir)
    except CredentialError as e:
        if BOGUS_ALIAS in str(e):
            lines.append(f"  bad-alias simulation: OK - clean refusal naming the alias: {e}")
            return True
        lines.append(f"  bad-alias simulation: FAIL - refusal does not name the alias: {e}")
        return False
    except Exception as e:
        lines.append(f"  bad-alias simulation: FAIL - unexpected {type(e).__name__} instead of CredentialError")
        return False
    lines.append(f'  bad-alias simulation: FAIL - bogus alias "{BOGUS_ALIAS}" unexpectedly resolved')
    return False


def smoke_test(project_slug, projects_root=None, resolve_fn=None, http_post=None):
    """Returns {"ok": bool, "lines": [str, ...]}. resolve_fn/http_post are
    injected by --verify only; live runs use the real credential resolver and
    each connector's real HTTP client."""
    projects_root = projects_root or PROJECTS_ROOT
    resolve_fn = resolve_fn or (lambda alias, project_dir: resolve_with_source(alias, project_dir=project_dir))
    project_dir = os.path.join(projects_root, project_slug)
    assert_within(projects_root, project_dir, "project directory")

    lines = [f"# Smoke test: {project_slug} (connectors only - no run, no writes)"]
    loops_root = os.path.join(project_dir, "loops")
    loop_names = sorted(d for d in (os.listdir(loops_root) if os.path.isdir(loops_root) else []) if os.path.exists(os.path.join(loops_root, d, "spec.md")))
    if not loop_names:
        lines.append(f"FAIL - no loops with a spec.md found under {loops_root}")
        return {"ok": False, "lines": lines}

    all_ok = True
    for loop_name in loop_names:
        spec_path = os.path.join(loops_root, loop_name, "spec.md")
        lines.append(f"loop: {loop_name}")
        validation = validate_spec_file(spec_path)
        if not validation["valid"]:
            lines.append(f"  FAIL - spec.md is invalid, refusing to smoke-test this loop: {'; '.join(validation['errors'])}")
            all_ok = False
            continue
        with open(spec_path, "r", encoding="utf-8") as f:
            spec = yaml.safe_load(extract_frontmatter(f.read()))
        for input_name in spec.get("inputs") or []:
            if not _check_connector(input_name, spec, project_dir, resolve_fn, http_post, lines):
                all_ok = False

    if not _bad_alias_simulation(project_dir, resolve_fn, lines):
        all_ok = False

    lines.append("RESULT: " + ("all connectors OK" if all_ok else "FAILURES above - fix before the first real run"))
    return {"ok": all_ok, "lines": lines}


def _self_test():
    import shutil
    import tempfile

    checks = []
    tmp = tempfile.mkdtemp(prefix="smoke-test-")
    project_dir = os.path.join(tmp, "acme")
    loop_dir = os.path.join(project_dir, "loops", "seo")
    os.makedirs(loop_dir)

    spec = """---
version: 1
loop: seo
objective: smoke-test fixture
primary_metric: gsc_position
guardrail_metrics:
  - name: ranking_pages_position
    comparator: ">"
    threshold: 5
failure_threshold:
  metric: ranking_pages_position
  comparator: ">"
  value: 5
inputs:
  - gsc
  - dataforseo
site_url: "sc-domain:example.com"
metrics_window_days: 28
targets:
  - keyword: best loop agency
    page: /blog/loop-agency
allowed_actions:
  - type: title-tag-rewrite
    tier: 1
    rollback: revert PR
    observation_window_days: 14
    min_sample_size: 100
approval_mode: propose-only
max_run_duration_minutes: 30
schedule: "manual"
stop_condition: fixture teardown
memory: memory.md
credential_aliases:
  gsc: acme-gsc-readonly
  dataforseo: acme-dataforseo-read
---
# fixture
"""
    with open(os.path.join(loop_dir, "spec.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(spec)

    fake_secret = "sk-test-fake-smoke-token-XYZ789"

    def fake_resolve(alias, project_dir):
        if alias in ("acme-gsc-readonly", "acme-dataforseo-read"):
            return fake_secret, "credential-manager"
        raise CredentialError(f'credential alias "{alias}" not found (fake store)')

    def fake_http_ok(url, headers, body_bytes):
        if "dataforseo" in url:
            payload = {"tasks": [{"result": [{"items": [{"type": "organic", "rank_absolute": 6, "url": "https://example.com/blog/loop-agency"}]}]}]}
        else:
            payload = {"rows": [{"keys": ["best loop agency", "/blog/loop-agency"], "clicks": 3, "impressions": 100, "position": 5.2}]}
        return 200, "OK", json.dumps(payload).encode("utf-8")

    result = smoke_test("acme", projects_root=tmp, resolve_fn=fake_resolve, http_post=fake_http_ok)
    output = "\n".join(result["lines"])
    checks.append(("healthy connectors: overall OK", result["ok"] is True))
    checks.append(("reports which store resolved each alias", output.count("resolved via credential-manager") == 2))
    checks.append(("reports HTTP status and row count per connector", "HTTP 200" in output and "1 row(s)" in output))
    checks.append(("bad-alias simulation reports a clean named refusal", "bad-alias simulation: OK" in output and BOGUS_ALIAS in output))
    checks.append(("output never contains the secret value", fake_secret not in output))

    def fake_http_401(url, headers, body_bytes):
        return 401, "Unauthorized", f"invalid token: {fake_secret}".encode("utf-8")

    failed_result = smoke_test("acme", projects_root=tmp, resolve_fn=fake_resolve, http_post=fake_http_401)
    failed_output = "\n".join(failed_result["lines"])
    checks.append(("auth failure: overall FAIL", failed_result["ok"] is False))
    checks.append(("auth failure reports the HTTP status cleanly", "HTTP 401" in failed_output))
    checks.append(("auth failure output never contains the secret value", fake_secret not in failed_output))

    def no_store(alias, project_dir):
        raise CredentialError(f'credential alias "{alias}" not found (fake empty store)')

    unresolved = smoke_test("acme", projects_root=tmp, resolve_fn=no_store, http_post=fake_http_ok)
    unresolved_output = "\n".join(unresolved["lines"])
    checks.append(("unresolvable alias: overall FAIL with a named clean error", unresolved["ok"] is False and "acme-gsc-readonly" in unresolved_output))

    checks.append(("smoke test writes nothing under runs/", not os.path.exists(os.path.join(loop_dir, "runs"))))
    checks.append(("smoke test takes no lock", not os.path.exists(os.path.join(loop_dir, "run.lock"))))

    shutil.rmtree(tmp, ignore_errors=True)

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--verify" in args:
        _self_test()
    else:
        positional = [a for a in args if not a.startswith("--")]
        if not positional:
            print("usage: python tools/smoke_test.py <project> | --verify", file=sys.stderr)
            sys.exit(2)
        result = smoke_test(positional[0])
        for line in result["lines"]:
            print(line)
        sys.exit(0 if result["ok"] else 1)

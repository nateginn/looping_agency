# Integration tests for AgentColabPlan.md Milestone-1 exit criteria.
# Operates on a disposable projects/_phase1-test-tmp/ fixture (created and
# torn down here) so it never touches the real projects/_demo run history
# used for the separate "two consecutive dry runs" proof.
import json
import os
import shutil
import stat
import sys
from datetime import datetime, timedelta, timezone

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.dirname(THIS_DIR)
WORKSPACE_ROOT = os.path.dirname(TOOLS_DIR)
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

try:
    from tools.run_loop import run_loop  # noqa: E402
    from tools.review_pending import decide, list_proposals, resolve_breach  # noqa: E402
    from tools.apply import apply_proposal  # noqa: E402
except ImportError:
    if TOOLS_DIR not in sys.path:
        sys.path.insert(0, TOOLS_DIR)
    from run_loop import run_loop  # noqa: E402
    from review_pending import decide, list_proposals, resolve_breach  # noqa: E402
    from apply import apply_proposal  # noqa: E402

PROJECTS_ROOT = os.path.join(WORKSPACE_ROOT, "projects")
PROJECT = "_phase1-test-tmp"
LOOP = "seo"
project_dir = os.path.join(PROJECTS_ROOT, PROJECT)
loop_dir = os.path.join(project_dir, "loops", LOOP)

results = []


def check(name, ok):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'} - {name}")


GOOD_SPEC = """---
version: 1
loop: seo
objective: Phase 1 exit-criteria test fixture
primary_metric: gsc_position
guardrail_metrics:
  - name: ranking_pages_position
    comparator: ">"
    threshold: 5
    consecutive_runs: 1
failure_threshold:
  metric: ranking_pages_position
  comparator: ">"
  value: 5
inputs:
  - mock
allowed_actions:
  - type: title-tag-rewrite
    tier: 1
    rollback: revert PR
    observation_window_days: 0.0001
    min_sample_size: 100
approval_mode: tier1-enabled
max_run_duration_minutes: 5
schedule: "manual"
stop_condition: test fixture teardown
memory: memory.md
credential_aliases:
  mock: fixture-alias
---
# fixture
"""

BAD_SPEC = """---
version: 1
loop: seo
objective: missing required fields
approval_mode: yolo-mode
---
# broken
"""

# Phase 2 fixture: a spec whose inputs dispatch to the real GSC connector.
# Tests inject a fake resolver + fake http_post, so nothing touches the
# network or the real credential stores.
GSC_SPEC = """---
version: 1
loop: seo
objective: Phase 2 real-connector dispatch fixture
primary_metric: gsc_position
guardrail_metrics:
  - name: ranking_pages_position
    comparator: ">"
    threshold: 5
    consecutive_runs: 1
failure_threshold:
  metric: ranking_pages_position
  comparator: ">"
  value: 5
inputs:
  - gsc
site_url: "sc-domain:example.com"
metrics_window_days: 28
allowed_actions:
  - type: title-tag-rewrite
    tier: 1
    rollback: revert PR
    observation_window_days: 0.0001
    min_sample_size: 100
approval_mode: propose-only
max_run_duration_minutes: 5
schedule: "manual"
stop_condition: test fixture teardown
memory: memory.md
credential_aliases:
  gsc: fixture-gsc-alias
---
# fixture
"""

GSC_DFS_SPEC = (
    GSC_SPEC.replace(
        "inputs:\n  - gsc\n",
        "inputs:\n  - gsc\n  - dataforseo\n",
    )
    .replace(
        "metrics_window_days: 28\n",
        "metrics_window_days: 28\ntargets:\n  - keyword: best loop agency\n    page: /blog/loop-agency\n",
    )
    .replace(
        "credential_aliases:\n  gsc: fixture-gsc-alias\n",
        "credential_aliases:\n  gsc: fixture-gsc-alias\n  dataforseo: fixture-dfs-alias\n",
    )
)

FAKE_LIVE_TOKEN = "sk-test-fake-live-token-ABC123"


def _force_rmtree(path):
    """shutil.rmtree can't delete read-only files on Windows (unlike Node's
    fs.rmSync) without clearing the read-only attribute first - snapshot.json
    is deliberately written read-only (see snapshot.py), so plain rmtree
    leaves stray fixture directories behind."""
    if not os.path.exists(path):
        return
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                os.chmod(os.path.join(root, name), stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


def reset_fixture():
    _force_rmtree(project_dir)
    os.makedirs(os.path.join(loop_dir, "pending"), exist_ok=True)
    os.makedirs(os.path.join(loop_dir, "runs"), exist_ok=True)
    with open(os.path.join(loop_dir, "spec.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(GOOD_SPEC)
    with open(os.path.join(loop_dir, "memory.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("# Memory - fixture\n")


def test_spec_validation_rejects_bad_spec():
    reset_fixture()
    with open(os.path.join(loop_dir, "spec.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(BAD_SPEC)
    result = run_loop(PROJECT, LOOP, scenario="normal")
    check("bad spec: run-loop refuses with status invalid-spec", result["status"] == "invalid-spec")
    bad_spec_run_dir = os.path.join(loop_dir, "runs", result["run_id"])
    check(
        "bad spec: no run.json written (only validation-failure.json)",
        os.path.exists(os.path.join(bad_spec_run_dir, "validation-failure.json")) and not os.path.exists(os.path.join(bad_spec_run_dir, "run.json")),
    )
    check("bad spec: lock released after refusal", not os.path.exists(os.path.join(loop_dir, "run.lock")))


def test_lock_refusal_and_stale_recovery():
    reset_fixture()
    # Live lock (this process's own pid, fresh) -> refused.
    with open(os.path.join(loop_dir, "run.lock"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"runId": "live-run", "pid": os.getpid(), "startTime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}, f)
    refused = run_loop(PROJECT, LOOP, scenario="normal")
    check("live lock: concurrent run refused", refused["status"] == "refused")
    with open(os.path.join(loop_dir, "lock-refusals.log"), "r", encoding="utf-8") as f:
        check("live lock: refusal logged", "live-run" in f.read())

    # Stale lock (dead pid) -> auto-recovered, run proceeds.
    with open(os.path.join(loop_dir, "run.lock"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"runId": "dead-run", "pid": 999999, "startTime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}, f)
    recovered = run_loop(PROJECT, LOOP, scenario="normal")
    check("stale lock (dead pid): run proceeds", recovered["status"] == "ok")
    check("stale lock: archived for audit", os.path.exists(os.path.join(loop_dir, "runs", "dead-run", "stale-lock.json")))
    check("lock released after successful run", not os.path.exists(os.path.join(loop_dir, "run.lock")))


def test_redaction_never_leaks_secret():
    reset_fixture()
    FAKE_SECRET = "sk-demo-FAKE1234567890ABCDEFDONOTUSE"  # must match tools/mock_metrics.py
    result = run_loop(PROJECT, LOOP, scenario="normal")
    run_dir = os.path.join(loop_dir, "runs", result["run_id"])
    with open(os.path.join(run_dir, "run.json"), "r", encoding="utf-8") as f:
        run_json_text = f.read()
    with open(os.path.join(run_dir, "report.md"), "r", encoding="utf-8") as f:
        report_text = f.read()
    with open(os.path.join(run_dir, "snapshot.json"), "r", encoding="utf-8") as f:
        snapshot_text = f.read()
    check("run.json never contains the planted fake secret", FAKE_SECRET not in run_json_text)
    check("report.md never contains the planted fake secret", FAKE_SECRET not in report_text)
    check("snapshot.json never contains the planted fake secret", FAKE_SECRET not in snapshot_text)


def test_partial_failure_clean_log():
    reset_fixture()
    result = run_loop(PROJECT, LOOP, scenario="fail")
    check("connector failure: status partial-failure", result["status"] == "partial-failure")
    run_dir = os.path.join(loop_dir, "runs", result["run_id"])
    with open(os.path.join(run_dir, "run.json"), "r", encoding="utf-8") as f:
        run_json = json.load(f)
    check("partial-failure run.json has final_status partial-failure", run_json["final_status"] == "partial-failure")
    check("partial-failure run.json records the failed tool call", run_json["tool_calls"][0]["ok"] is False)
    check("partial-failure run.json error message is redacted", "sk-demo-FAKE" not in json.dumps(run_json))
    check("lock released after partial failure", not os.path.exists(os.path.join(loop_dir, "run.lock")))


def test_pause_on_breach_blocks_new_proposals():
    reset_fixture()
    # Seed an "applied" proposal targeting the page mock_metrics regresses in 'breach' scenario.
    applied_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    applied_proposal = {
        "id": "prop-seed-0",
        "loop": LOOP,
        "action_type": "title-tag-rewrite",
        "tier": 1,
        "target": {"page": "/blog/seo-automation", "keyword": "seo automation tool"},
        "baseline_position": 11.5,
        "rationale": "seed",
        "rollback": "revert PR",
        "manual_approval_only": False,
        "status": "applied",
        "created_run_id": "seed",
        "created_at": applied_at,
        "run_cycles_seen": 0,
        "decision": {"action": "approve", "by": "test", "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")},
        "applied_at": applied_at,  # 1 min ago, window is ~9s so already elapsed
        "observation_window_days": 0.0001,
        "min_sample_size": 100,
    }
    with open(os.path.join(loop_dir, "pending", f'{applied_proposal["id"]}.json'), "w", encoding="utf-8", newline="\n") as f:
        json.dump(applied_proposal, f, indent=2)

    breach_run = run_loop(PROJECT, LOOP, scenario="breach")
    check("breach run: status paused-breach", breach_run["status"] == "paused-breach")
    with open(os.path.join(loop_dir, "state.json"), "r", encoding="utf-8") as f:
        state = json.load(f)
    check("state.json shows paused-breach", state["status"] == "paused-breach")
    check("breach run created 0 new proposals", len(breach_run["run_json"]["proposals_created"]) == 0)

    blocked_run = run_loop(PROJECT, LOOP, scenario="normal")
    check("subsequent run while paused-breach also creates 0 new proposals", len(blocked_run["run_json"]["proposals_created"]) == 0)
    check("subsequent run reports the block explicitly", any("BLOCKED" in d for d in blocked_run["run_json"]["decisions"]))

    revert_id = f'revert-{applied_proposal["id"]}'
    check("auto-generated revert proposal exists", any(p["id"] == revert_id for p in list_proposals(PROJECT, LOOP)))

    resolved = resolve_breach(PROJECT, LOOP, note="test resolves breach")
    check("resolveBreach clears paused-breach", resolved["status"] == "active")
    after_resolve = run_loop(PROJECT, LOOP, scenario="normal")
    check("after resolving breach, new proposals can be created again", len(after_resolve["run_json"]["proposals_created"]) > 0)


def test_approval_gates_prevent_unapproved_apply():
    reset_fixture()
    draft = {
        "id": "prop-gate-test",
        "loop": LOOP,
        "action_type": "title-tag-rewrite",
        "tier": 1,
        "target": {"page": "/blog/x", "keyword": "x"},
        "rationale": "test",
        "rollback": "revert PR",
        "manual_approval_only": False,
        "status": "draft",
        "created_run_id": "seed",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "run_cycles_seen": 0,
        "decision": None,
        "applied_at": None,
        "observation_window_days": 0.0001,
        "min_sample_size": 100,
    }
    with open(os.path.join(loop_dir, "pending", f'{draft["id"]}.json'), "w", encoding="utf-8", newline="\n") as f:
        json.dump(draft, f, indent=2)

    refused_unapproved = False
    try:
        apply_proposal(PROJECT, LOOP, draft["id"])
    except Exception as e:
        refused_unapproved = "approval gate blocks apply" in str(e)
    check("apply.py refuses an unapproved (draft) proposal", refused_unapproved)

    decide(PROJECT, LOOP, draft["id"], "approve", by="test")
    applied = apply_proposal(PROJECT, LOOP, draft["id"])
    check("apply.py succeeds once approved (spec is tier1-enabled)", applied["status"] == "applied")
    check(
        "applied marker written outside pending/ (never mistaken for proposal state)",
        os.path.exists(os.path.join(loop_dir, "applied", f'{draft["id"]}.marker.json'))
        and not os.path.exists(os.path.join(loop_dir, "pending", f'{draft["id"]}.applied-marker.json')),
    )
    check(
        "marker file is not picked up by list_proposals (no id/status/tier pollution)",
        not any(p.get("id") is None for p in list_proposals(PROJECT, LOOP)),
    )

    # Tier 2 must always refuse regardless of approval status.
    tier2 = {**draft, "id": "prop-tier2-test", "tier": 2, "status": "draft"}
    with open(os.path.join(loop_dir, "pending", f'{tier2["id"]}.json'), "w", encoding="utf-8", newline="\n") as f:
        json.dump(tier2, f, indent=2)
    decide(PROJECT, LOOP, tier2["id"], "approve", by="test")
    refused_tier2 = False
    try:
        apply_proposal(PROJECT, LOOP, tier2["id"])
    except Exception as e:
        refused_tier2 = "Tier 2" in str(e)
    check("apply.py refuses an approved Tier 2 proposal (human-only, always)", refused_tier2)


def test_propose_only_refuses_tier1_apply():
    reset_fixture()
    propose_only_spec = GOOD_SPEC.replace("approval_mode: tier1-enabled", "approval_mode: propose-only")
    with open(os.path.join(loop_dir, "spec.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(propose_only_spec)

    draft = {
        "id": "prop-propose-only-test",
        "loop": LOOP,
        "action_type": "title-tag-rewrite",
        "tier": 1,
        "target": {"page": "/blog/x", "keyword": "x"},
        "rationale": "test",
        "rollback": "revert PR",
        "manual_approval_only": False,
        "status": "draft",
        "created_run_id": "seed",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "run_cycles_seen": 0,
        "decision": None,
        "applied_at": None,
        "observation_window_days": 0.0001,
        "min_sample_size": 100,
    }
    with open(os.path.join(loop_dir, "pending", f'{draft["id"]}.json'), "w", encoding="utf-8", newline="\n") as f:
        json.dump(draft, f, indent=2)
    decide(PROJECT, LOOP, draft["id"], "approve", by="test")

    refused = False
    try:
        apply_proposal(PROJECT, LOOP, draft["id"])
    except Exception as e:
        refused = 'approval_mode is "propose-only"' in str(e)
    check("apply.py refuses an approved Tier-1 proposal when spec.approval_mode is propose-only", refused)


def test_stale_lock_ttl_comes_from_spec():
    reset_fixture()
    # GOOD_SPEC declares max_run_duration_minutes: 5. A live (own-pid) lock aged
    # 6 minutes should be recovered as stale using the SPEC's TTL, not a hardcoded default.
    aged_start = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat().replace("+00:00", "Z")
    with open(os.path.join(loop_dir, "run.lock"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"runId": "aged-per-spec-ttl", "pid": os.getpid(), "startTime": aged_start}, f)
    result = run_loop(PROJECT, LOOP, scenario="normal")
    check("lock older than spec.max_run_duration_minutes (5) is recovered as stale, not the 60min default", result["status"] == "ok")
    check("stale lock archived under the run it belonged to", os.path.exists(os.path.join(loop_dir, "runs", "aged-per-spec-ttl", "stale-lock.json")))


def _write_spec(text):
    with open(os.path.join(loop_dir, "spec.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def test_gsc_dispatch_offline():
    reset_fixture()
    _write_spec(GSC_SPEC)
    calls = []

    def fake_http(url, headers, body_bytes):
        calls.append(url)
        payload = {
            "rows": [
                {"keys": ["best loop agency", "/blog/loop-agency"], "clicks": 42, "impressions": 900, "position": 8.2},
                {"keys": ["seo automation tool", "/blog/seo-automation"], "clicks": 18, "impressions": 640, "position": 11.5},
            ]
        }
        return 200, "OK", json.dumps(payload).encode("utf-8")

    result = run_loop(PROJECT, LOOP, _resolve_credential=lambda alias: FAKE_LIVE_TOKEN, _http_post=fake_http)
    check("gsc dispatch: run succeeds offline (fake resolver + fake http_post)", result["status"] == "ok")
    check(
        "gsc dispatch: exactly one call, to the GSC endpoint for the spec's site_url",
        len(calls) == 1 and "webmasters/v3/sites/sc-domain%3Aexample.com" in calls[0],
    )
    run_json = result["run_json"]
    check("gsc dispatch: tool_calls records the gsc connector", [t["tool"] for t in run_json["tool_calls"]] == ["gsc"])
    check("gsc dispatch: credential_alias_used generalized per input", run_json["credential_alias_used"] == {"gsc": "fixture-gsc-alias"})
    check("gsc dispatch: proposals generated from connector-shaped data", len(run_json["proposals_created"]) > 0)
    run_dir = os.path.join(loop_dir, "runs", result["run_id"])
    on_disk = ""
    for name in ("run.json", "report.md", "snapshot.json"):
        with open(os.path.join(run_dir, name), "r", encoding="utf-8") as f:
            on_disk += f.read()
    check("gsc dispatch: resolved token never reaches disk", FAKE_LIVE_TOKEN not in on_disk)


def test_gsc_dataforseo_merge():
    reset_fixture()
    _write_spec(GSC_DFS_SPEC)

    def fake_http(url, headers, body_bytes):
        if "dataforseo" in url:
            payload = {"tasks": [{"result": [{"items": [{"type": "organic", "rank_absolute": 6, "url": "https://example.com/blog/loop-agency"}]}]}]}
        else:
            payload = {"rows": [{"keys": ["best loop agency", "/blog/loop-agency"], "clicks": 42, "impressions": 900, "position": 8.2}]}
        return 200, "OK", json.dumps(payload).encode("utf-8")

    result = run_loop(PROJECT, LOOP, _resolve_credential=lambda alias: FAKE_LIVE_TOKEN, _http_post=fake_http)
    check("merge: gsc+dataforseo run succeeds offline", result["status"] == "ok")
    check("merge: both connectors recorded in tool_calls", [t["tool"] for t in result["run_json"]["tool_calls"]] == ["gsc", "dataforseo"])
    with open(os.path.join(loop_dir, "runs", result["run_id"], "snapshot.json"), "r", encoding="utf-8") as f:
        snapshot = json.load(f)
    row = snapshot["keywords"][0]
    check("merge: gsc stays the primary source (clicks/position preserved)", row["clicks"] == 42 and row["position"] == 8.2)
    check("merge: dataforseo enriches the matching row with serp_position", row.get("serp_position") == 6)


def test_gsc_connector_failure_clean_partial():
    reset_fixture()
    _write_spec(GSC_SPEC)

    def fake_http_401(url, headers, body_bytes):
        return 401, "Unauthorized", f"invalid token: {FAKE_LIVE_TOKEN}".encode("utf-8")

    result = run_loop(PROJECT, LOOP, _resolve_credential=lambda alias: FAKE_LIVE_TOKEN, _http_post=fake_http_401)
    check("gsc failure: status partial-failure", result["status"] == "partial-failure")
    with open(os.path.join(loop_dir, "runs", result["run_id"], "run.json"), "r", encoding="utf-8") as f:
        run_json = json.load(f)
    check("gsc failure: final_status partial-failure", run_json["final_status"] == "partial-failure")
    check("gsc failure: failed tool_call names the gsc connector", run_json["tool_calls"][0]["tool"] == "gsc" and run_json["tool_calls"][0]["ok"] is False)
    check("gsc failure: error surfaces the HTTP status", "401" in run_json["tool_calls"][0]["error"])
    check("gsc failure: token echoed in the API error never reaches run.json", FAKE_LIVE_TOKEN not in json.dumps(run_json))
    check("gsc failure: lock released", not os.path.exists(os.path.join(loop_dir, "run.lock")))


def main():
    test_spec_validation_rejects_bad_spec()
    test_lock_refusal_and_stale_recovery()
    test_redaction_never_leaks_secret()
    test_partial_failure_clean_log()
    test_pause_on_breach_blocks_new_proposals()
    test_approval_gates_prevent_unapproved_apply()
    test_propose_only_refuses_tier1_apply()
    test_stale_lock_ttl_comes_from_spec()
    test_gsc_dispatch_offline()
    test_gsc_dataforseo_merge()
    test_gsc_connector_failure_clean_partial()

    _force_rmtree(project_dir)

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

# Integration tests for AgentColabPlan.md Milestone-1 exit criteria.
# Operates on a disposable projects/_phase1-test-tmp/ fixture (created and
# torn down here) so it never touches the real projects/_demo run history
# used for the separate "two consecutive dry runs" proof.
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.dirname(THIS_DIR)
WORKSPACE_ROOT = os.path.dirname(TOOLS_DIR)
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

try:
    from tools.apply import apply_proposal  # noqa: E402
    from tools.draft_copy import draft_copy  # noqa: E402
    from tools.review_pending import decide, list_proposals, resolve_breach  # noqa: E402
    from tools.run_loop import run_loop, _promote_live_implementations  # noqa: E402
    from tools.lib.artwebsite_seo import create_worktree, make_attempt, proposal_branch_name  # noqa: E402
except ImportError:
    if TOOLS_DIR not in sys.path:
        sys.path.insert(0, TOOLS_DIR)
    from apply import apply_proposal  # noqa: E402
    from draft_copy import draft_copy  # noqa: E402
    from review_pending import decide, list_proposals, resolve_breach  # noqa: E402
    from run_loop import run_loop, _promote_live_implementations  # noqa: E402
    from lib.artwebsite_seo import create_worktree, make_attempt, proposal_branch_name  # noqa: E402

PROJECTS_ROOT = os.path.join(WORKSPACE_ROOT, "projects")
PROJECT = "_phase1-test-tmp"
LOOP = "seo"
project_dir = os.path.join(PROJECTS_ROOT, PROJECT)
loop_dir = os.path.join(project_dir, "loops", LOOP)
pending_dir = os.path.join(loop_dir, "pending")

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
    if not os.path.exists(path):
        return
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                os.chmod(os.path.join(root, name), stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


def _write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _git(repo_path, *args):
    completed = subprocess.run(["git", *args], cwd=repo_path, capture_output=True, text=True, check=True)
    return completed.stdout.strip()


def _make_temp_git_site():
    repo_path = tempfile.mkdtemp(prefix="artwebsite-fixture-")
    os.makedirs(os.path.join(repo_path, "templates"), exist_ok=True)
    os.makedirs(os.path.join(repo_path, "app"), exist_ok=True)
    _write_text(
        os.path.join(repo_path, "templates", "services.html"),
        "{% block title %}Old service title{% endblock %}\n{% block meta_description %}Old service meta{% endblock %}\n",
    )
    _write_text(
        os.path.join(repo_path, "app", "views.py"),
        "from django.shortcuts import render\n\n"
        "def home(request):\n"
        "    return render(request, 'home.html', {\n"
        "        'title': 'Old home title',\n"
        "        'meta_description': 'Old home meta',\n"
        "    })\n\n"
        "def auto_injury(request):\n"
        "    return render(request, 'auto-injury.html', {\n"
        "        'title': 'Old auto injury title',\n"
        "        'meta_description': 'Old auto injury meta',\n"
        "    })\n",
    )
    _git(repo_path, "init", "-b", "main")
    _git(repo_path, "config", "user.email", "test@example.com")
    _git(repo_path, "config", "user.name", "Loop Agency Test")
    _git(repo_path, "add", "-A")
    _git(repo_path, "commit", "-m", "fixture init")
    return repo_path


def _seed_proposal(proposal):
    _write_json(os.path.join(pending_dir, f'{proposal["id"]}.json'), proposal)


def _proposal(
    proposal_id,
    *,
    status="draft",
    action_type="title-tag-rewrite",
    page="/services/",
    keyword="service keyword",
    tier=1,
    manual=False,
    implementation_value="New title for services",
):
    return {
        "id": proposal_id,
        "loop": LOOP,
        "action_type": action_type,
        "tier": tier,
        "target": {"page": page, "keyword": keyword},
        "baseline_position": 8.2,
        "rationale": "test",
        "rollback": "revert PR",
        "manual_approval_only": manual,
        "status": status,
        "created_run_id": "seed",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "run_cycles_seen": 0,
        "decision": None,
        "applied_at": None,
        "observation_window_days": 0.0001,
        "min_sample_size": 100,
        "implementation": {"new_value": implementation_value},
    }


def _compare_requester(compare_status):
    def requester(url, headers):
        payload = {"status": compare_status}
        return 200, "OK", json.dumps(payload).encode("utf-8")

    return requester


def reset_fixture():
    _force_rmtree(project_dir)
    os.makedirs(pending_dir, exist_ok=True)
    os.makedirs(os.path.join(loop_dir, "runs"), exist_ok=True)
    _write_text(os.path.join(loop_dir, "spec.md"), GOOD_SPEC)
    _write_text(os.path.join(loop_dir, "memory.md"), "# Memory - fixture\n")
    _write_text(
        os.path.join(project_dir, "project.md"),
        "---\nslug: _phase1-test-tmp\nrepo: D:\\Temp\\unused\n---\n# fixture\n",
    )


def test_spec_validation_rejects_bad_spec():
    reset_fixture()
    _write_text(os.path.join(loop_dir, "spec.md"), BAD_SPEC)
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
    _write_json(
        os.path.join(loop_dir, "run.lock"),
        {"runId": "live-run", "pid": os.getpid(), "startTime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")},
    )
    refused = run_loop(PROJECT, LOOP, scenario="normal")
    check("live lock: concurrent run refused", refused["status"] == "refused")
    with open(os.path.join(loop_dir, "lock-refusals.log"), "r", encoding="utf-8") as f:
        check("live lock: refusal logged", "live-run" in f.read())

    _write_json(
        os.path.join(loop_dir, "run.lock"),
        {"runId": "dead-run", "pid": 999999, "startTime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")},
    )
    recovered = run_loop(PROJECT, LOOP, scenario="normal")
    check("stale lock (dead pid): run proceeds", recovered["status"] == "ok")
    check("stale lock: archived for audit", os.path.exists(os.path.join(loop_dir, "runs", "dead-run", "stale-lock.json")))
    check("lock released after successful run", not os.path.exists(os.path.join(loop_dir, "run.lock")))


def test_redaction_never_leaks_secret():
    reset_fixture()
    fake_secret = "sk-demo-FAKE1234567890ABCDEFDONOTUSE"
    result = run_loop(PROJECT, LOOP, scenario="normal")
    run_dir = os.path.join(loop_dir, "runs", result["run_id"])
    on_disk = ""
    for name in ("run.json", "report.md", "snapshot.json"):
        with open(os.path.join(run_dir, name), "r", encoding="utf-8") as f:
            on_disk += f.read()
    check("run.json/report.md/snapshot.json never contain the planted fake secret", fake_secret not in on_disk)


def test_partial_failure_clean_log():
    reset_fixture()
    result = run_loop(PROJECT, LOOP, scenario="fail")
    check("connector failure: status partial-failure", result["status"] == "partial-failure")
    run_dir = os.path.join(loop_dir, "runs", result["run_id"])
    run_json = _read_json(os.path.join(run_dir, "run.json"))
    check("partial-failure run.json has final_status partial-failure", run_json["final_status"] == "partial-failure")
    check("partial-failure run.json records the failed tool call", run_json["tool_calls"][0]["ok"] is False)
    check("partial-failure run.json error message is redacted", "sk-demo-FAKE" not in json.dumps(run_json))
    check("lock released after partial failure", not os.path.exists(os.path.join(loop_dir, "run.lock")))


def test_pause_on_breach_blocks_new_proposals():
    reset_fixture()
    applied_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    applied_proposal = _proposal(
        "prop-seed-0",
        status="applied",
        page="/blog/seo-automation",
        keyword="seo automation tool",
        implementation_value="Not used",
    )
    applied_proposal["baseline_position"] = 11.5
    applied_proposal["decision"] = {"action": "approve", "by": "test", "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    applied_proposal["created_at"] = applied_at
    applied_proposal["applied_at"] = applied_at
    _seed_proposal(applied_proposal)

    breach_run = run_loop(PROJECT, LOOP, scenario="breach")
    check("breach run: status paused-breach", breach_run["status"] == "paused-breach")
    state = _read_json(os.path.join(loop_dir, "state.json"))
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


def test_apply_success_and_tier2_refusal():
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        draft = _proposal("prop-apply-success", status="draft")
        _seed_proposal(draft)
        decide(PROJECT, LOOP, draft["id"], "approve", by="test")
        implemented = apply_proposal(PROJECT, LOOP, draft["id"], repo_path=repo_path)
        stored = _read_json(os.path.join(pending_dir, f'{draft["id"]}.json'))
        branch_head = _git(repo_path, "rev-parse", proposal_branch_name(draft["id"]))
        with open(os.path.join(repo_path, "templates", "services.html"), "r", encoding="utf-8") as f:
            root_template_unchanged = "Old service title" in f.read()
        check("apply.py succeeds once approved and returns implemented status", implemented["status"] == "implemented")
        check("implemented proposal stores branch + commit sha", stored["implemented_branch"] == proposal_branch_name(draft["id"]) and stored["implemented_commit_sha"] == branch_head)
        check("worktree isolation leaves the source repo working tree file unchanged", root_template_unchanged)

        tier2 = _proposal("prop-tier2-test", status="approved", tier=2)
        _seed_proposal(tier2)
        refused_tier2 = False
        try:
            apply_proposal(PROJECT, LOOP, tier2["id"], repo_path=repo_path)
        except Exception as e:
            refused_tier2 = "Tier 2" in str(e)
        check("apply.py refuses an approved Tier 2 proposal (human-only, always)", refused_tier2)
    finally:
        _force_rmtree(repo_path)


def test_draft_copy_succeeds_on_draft_status():
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        proposal = _proposal("prop-draft-status-ok", status="draft", page="/services/")
        del proposal["implementation"]
        _seed_proposal(proposal)

        drafted = draft_copy(PROJECT, LOOP, proposal["id"], "New Draft Status Title", "claude-test", repo_path=repo_path)
        check("draft_copy: a fresh draft proposal accepts new implementation copy", drafted["implementation"]["new_value"] == "New Draft Status Title")
        check("draft_copy: proposal status stays draft after drafting copy", drafted["status"] == "draft")
    finally:
        _force_rmtree(repo_path)


def test_draft_copy_refuses_past_draft_status():
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        proposal = _proposal("prop-redraft-approved", status="draft", page="/services/")
        del proposal["implementation"]
        _seed_proposal(proposal)

        draft_copy(PROJECT, LOOP, proposal["id"], "Original Approved Title", "claude-test", repo_path=repo_path)
        decide(PROJECT, LOOP, proposal["id"], "approve", by="test")
        before = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
        check(
            "setup: proposal is approved with drafted copy before the refused redraft attempt",
            before["status"] == "approved" and before["implementation"]["new_value"] == "Original Approved Title",
        )

        refused = False
        try:
            draft_copy(PROJECT, LOOP, proposal["id"], "Sneaky Overwrite Title", "claude-test", repo_path=repo_path)
        except ValueError as e:
            refused = 'not "draft"' in str(e)
        check("draft_copy: drafting an already-approved proposal is refused", refused)

        after = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
        check("draft_copy: approved proposal's status is unchanged after the refused redraft", after["status"] == "approved")
        check(
            "draft_copy: approved proposal's existing implementation is unchanged after the refused redraft",
            after["implementation"] == before["implementation"],
        )
    finally:
        _force_rmtree(repo_path)


def test_draft_copy_end_to_end_apply():
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        proposal = _proposal("prop-draft-e2e", status="draft", page="/services/")
        del proposal["implementation"]
        _seed_proposal(proposal)

        drafted = draft_copy(PROJECT, LOOP, proposal["id"], "New Services Title For SEO", "claude-test", repo_path=repo_path)
        check("draft_copy: previous_value is read from the live template, not guessed", drafted["implementation"]["previous_value"] == "Old service title")
        check("draft_copy: new_value stored verbatim", drafted["implementation"]["new_value"] == "New Services Title For SEO")
        check("draft_copy: drafted_at/drafted_by stamped on the proposal", bool(drafted["implementation"]["drafted_at"]) and drafted["implementation"]["drafted_by"] == "claude-test")

        decide(PROJECT, LOOP, proposal["id"], "approve", by="test")
        implemented = apply_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)
        check("draft_copy end-to-end: apply succeeds using the drafted copy", implemented["status"] == "implemented")

        branch_head = _git(repo_path, "rev-parse", proposal_branch_name(proposal["id"]))
        committed = subprocess.run(
            ["git", "show", f"{branch_head}:templates/services.html"], cwd=repo_path, capture_output=True, text=True, check=True
        ).stdout
        check("draft_copy end-to-end: committed content contains the drafted new_value", "New Services Title For SEO" in committed)
    finally:
        _force_rmtree(repo_path)


def test_apply_refuses_stale_previous_value():
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        proposal = _proposal("prop-stale-apply", status="draft", page="/services/")
        del proposal["implementation"]
        _seed_proposal(proposal)

        draft_copy(PROJECT, LOOP, proposal["id"], "New Services Title", "claude-test", repo_path=repo_path)
        decide(PROJECT, LOOP, proposal["id"], "approve", by="test")

        # Simulate someone else editing the live page directly (a commit
        # landing on `main`) after this proposal was drafted but before it
        # was applied - the approved previous_value is now stale.
        _write_text(
            os.path.join(repo_path, "templates", "services.html"),
            "{% block title %}Title changed by someone else{% endblock %}\n{% block meta_description %}Old service meta{% endblock %}\n",
        )
        _git(repo_path, "add", "-A")
        _git(repo_path, "commit", "-m", "someone else's edit")

        result = apply_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)
        stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
        check("stale previous_value: apply refuses and marks implement-failed, not implemented", result["status"] == "implement-failed" and stored["status"] == "implement-failed")
        check("stale previous_value: implement_error explains the drift", "no longer matches the approved previous_value" in stored.get("implement_error", ""))
        with open(os.path.join(repo_path, "templates", "services.html"), "r", encoding="utf-8") as f:
            check("stale previous_value: the other person's edit on the live page is left untouched", "Title changed by someone else" in f.read())
    finally:
        _force_rmtree(repo_path)


def test_manual_approval_only_hard_refusal():
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        proposal = _proposal("prop-manual-only", status="approved", manual=True)
        _seed_proposal(proposal)
        refused = False
        try:
            apply_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)
        except Exception as e:
            refused = "manual_approval_only" in str(e)
        stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
        check("manual_approval_only approved proposal is refused by apply.py", refused)
        check("manual_approval_only refusal leaves proposal approved and untouched", stored["status"] == "approved" and "implement_attempt" not in stored)
    finally:
        _force_rmtree(repo_path)


def test_propose_only_refuses_tier1_apply():
    reset_fixture()
    _write_text(os.path.join(loop_dir, "spec.md"), GOOD_SPEC.replace("approval_mode: tier1-enabled", "approval_mode: propose-only"))
    repo_path = _make_temp_git_site()
    try:
        proposal = _proposal("prop-propose-only-test", status="approved")
        _seed_proposal(proposal)
        refused = False
        try:
            apply_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)
        except Exception as e:
            refused = 'approval_mode is "propose-only"' in str(e)
        check("apply.py refuses an approved Tier-1 proposal when spec.approval_mode is propose-only", refused)
    finally:
        _force_rmtree(repo_path)


def test_apply_and_review_pending_block_on_a_concurrent_run_lock_holder():
    # Phase 3: apply.py/review_pending.py/draft_copy.py now share run_loop.py's
    # single run.lock (the old, separately-named apply.lock let apply.py run
    # concurrently with an active run - exactly the stale-write race this
    # phase closes). A live held lock must refuse all of them, and each must
    # leave the proposal completely untouched.
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        proposal = _proposal("prop-approve-vs-runlock", status="draft")
        _seed_proposal(proposal)

        _write_json(
            os.path.join(loop_dir, "run.lock"),
            {"runId": "held-run", "pid": os.getpid(), "startTime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")},
        )

        approve_refused = False
        try:
            decide(PROJECT, LOOP, proposal["id"], "approve", by="test")
        except ValueError as e:
            approve_refused = "run lock active" in str(e)
        check("review_pending.decide refuses while a concurrent run holds the lock", approve_refused)
        stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
        check("review_pending.decide: proposal status untouched while refused", stored["status"] == "draft" and stored.get("decision") is None)

        apply_refused = False
        try:
            apply_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)
        except ValueError as e:
            apply_refused = "run lock active" in str(e)
        check("apply.py refuses while a concurrent run holds the lock", apply_refused)
        stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
        check("apply.py: proposal status untouched while refused (still draft, no implement_attempt)", stored["status"] == "draft" and "implement_attempt" not in stored)
        check("run.lock is untouched by the refused calls (still held by the original runId)", _read_json(os.path.join(loop_dir, "run.lock"))["runId"] == "held-run")

        # Once the simulated run finishes and releases the lock, the same
        # calls succeed normally.
        os.remove(os.path.join(loop_dir, "run.lock"))
        decide(PROJECT, LOOP, proposal["id"], "approve", by="test")
        implemented = apply_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)
        check("apply.py succeeds normally once the concurrent run's lock is released", implemented["status"] == "implemented")
    finally:
        _force_rmtree(repo_path)


def test_draft_copy_blocks_on_a_concurrent_run_lock_holder():
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        proposal = _proposal("prop-draft-vs-runlock", status="draft", page="/services/")
        del proposal["implementation"]
        _seed_proposal(proposal)

        _write_json(
            os.path.join(loop_dir, "run.lock"),
            {"runId": "held-run-2", "pid": os.getpid(), "startTime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")},
        )

        refused = False
        try:
            draft_copy(PROJECT, LOOP, proposal["id"], "New Title While Run Active", "claude-test", repo_path=repo_path)
        except ValueError as e:
            refused = "run lock active" in str(e)
        check("draft_copy refuses to draft while a concurrent run holds the lock", refused)

        stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
        check("draft_copy: proposal has no implementation written while refused", "implementation" not in stored)

        os.remove(os.path.join(loop_dir, "run.lock"))
        drafted = draft_copy(PROJECT, LOOP, proposal["id"], "New Title While Run Active", "claude-test", repo_path=repo_path)
        check("draft_copy succeeds normally once the concurrent run's lock is released", drafted["implementation"]["new_value"] == "New Title While Run Active")
    finally:
        _force_rmtree(repo_path)


def test_promote_live_implementations_no_nested_lock_deadlock():
    # The pre-Phase-3 code had _promote_live_implementations() acquire its
    # own separate apply.lock nested inside run_loop()'s held run.lock - safe
    # only because the two locks had different names. Consolidating onto one
    # shared lock without also dropping this nested acquisition would make
    # run_loop() self-refuse against its own held lock every single run,
    # silently skipping every implemented->applied promotion forever. Prove
    # the fix: call the promotion function directly while run.lock is already
    # held (simulating the caller) and confirm it still promotes - it must
    # not attempt to acquire anything itself.
    reset_fixture()
    proposal = _proposal("prop-nested-lock", status="implemented", page="/blog/loop-agency", keyword="best loop agency")
    proposal["implemented_branch"] = "seo/prop-nested-lock"
    proposal["implemented_commit_sha"] = "abc123"
    proposal["implemented_at"] = "2026-07-20T12:00:00Z"
    _seed_proposal(proposal)

    _write_json(
        os.path.join(loop_dir, "run.lock"),
        {"runId": "outer-run", "pid": os.getpid(), "startTime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")},
    )

    proposals = [proposal]
    decisions, awaiting_ids, stuck_ids = _promote_live_implementations(
        PROJECT,
        pending_dir,
        proposals,
        requester=_compare_requester("ahead"),
        compare_target={"owner": "nateginn", "repo": "artwebsite"},
    )
    stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
    check("no nested-lock deadlock: promotion succeeds while the outer run.lock is already held", stored["status"] == "applied")
    check("no nested-lock deadlock: nothing skipped it as busy", proposal["id"] not in awaiting_ids and proposal["id"] not in stuck_ids)
    check("no nested-lock deadlock: the outer run.lock is untouched (still held by the outer runId)", _read_json(os.path.join(loop_dir, "run.lock"))["runId"] == "outer-run")
    os.remove(os.path.join(loop_dir, "run.lock"))


def test_run_loop_promotion_survives_end_of_run_write():
    # The bug this phase closes: apply.py transitions a proposal mid-run,
    # then run_loop.py's end-of-run write of its own (now stale) in-memory
    # proposal list reverts it. With one shared lock, a concurrent apply.py
    # write during a run_loop() run is now impossible (previous test proves
    # it's refused); this proves the promotion run_loop() makes *to itself*
    # mid-run (via _promote_live_implementations) is not clobbered by its own
    # end-of-run write of the in-memory `proposals` list.
    reset_fixture()
    proposal = _proposal("prop-no-stale-revert", status="implemented", page="/blog/loop-agency", keyword="best loop agency")
    proposal["implemented_branch"] = "seo/prop-no-stale-revert"
    proposal["implemented_commit_sha"] = "abc123"
    proposal["implemented_at"] = "2026-07-20T12:00:00Z"
    _seed_proposal(proposal)

    result = run_loop(
        PROJECT,
        LOOP,
        scenario="normal",
        _github_requester=_compare_requester("ahead"),
        _github_compare_target={"owner": "nateginn", "repo": "artwebsite"},
    )
    stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
    check(
        "no stale final write: run_loop's end-of-run write persists the mid-run implemented->applied promotion",
        stored["status"] == "applied" and result["run_json"]["status"] == "ok",
    )


def test_apply_recovers_from_a_stale_run_lock_left_by_a_crashed_apply():
    # A prior apply.py process that crashed mid-flight would have died while
    # holding the shared run.lock rather than releasing it. Prove the
    # existing stale-lock recovery (dead pid / age > max_run_duration) covers
    # this now that apply.py uses the same lock file run_loop.py does.
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        proposal = _proposal("prop-crashed-apply-lock", status="approved")
        _seed_proposal(proposal)

        _write_json(
            os.path.join(loop_dir, "run.lock"),
            {"runId": "crashed-apply-run", "pid": 999999, "startTime": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat().replace("+00:00", "Z")},
        )

        implemented = apply_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)
        check("apply.py recovers a stale run.lock left by a crashed prior apply (dead pid)", implemented["status"] == "implemented")
        check("apply.py: the stale lock is archived for audit, same mechanism as a crashed run_loop", os.path.exists(os.path.join(loop_dir, "runs", "crashed-apply-run", "stale-lock.json")))
        check("apply.py: the lock is released after recovery + successful apply", not os.path.exists(os.path.join(loop_dir, "run.lock")))
    finally:
        _force_rmtree(repo_path)


def test_crash_recovery_no_commit_path():
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        proposal = _proposal("prop-recover-no-commit", status="approved", manual=True)
        attempt = make_attempt(repo_path, proposal["id"])
        create_worktree(repo_path, attempt)
        proposal["implement_attempt"] = attempt
        _seed_proposal(proposal)

        refused = False
        try:
            apply_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)
        except Exception as e:
            refused = "manual_approval_only" in str(e)

        stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
        branch_missing = subprocess.run(["git", "rev-parse", "--verify", proposal_branch_name(proposal["id"])], cwd=repo_path, capture_output=True, text=True).returncode != 0
        check("crash recovery (no commit): stale attempt is inspected before refusal", refused)
        check("crash recovery (no commit): proposal reset to approved with no implement_attempt", stored["status"] == "approved" and "implement_attempt" not in stored)
        check("crash recovery (no commit): stale branch/worktree cleaned up", branch_missing)
    finally:
        _force_rmtree(repo_path)


def test_crash_recovery_commit_exists_path():
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        proposal = _proposal("prop-recover-commit-exists", status="approved")
        attempt = make_attempt(repo_path, proposal["id"])
        create_worktree(repo_path, attempt)
        _write_text(
            os.path.join(attempt["worktree_path"], "templates", "services.html"),
            "{% block title %}Recovered title{% endblock %}\n{% block meta_description %}Old service meta{% endblock %}\n",
        )
        _git(attempt["worktree_path"], "add", "-A")
        _git(attempt["worktree_path"], "commit", "-m", "Recovered commit")
        expected_head = _git(attempt["worktree_path"], "rev-parse", "HEAD")
        proposal["implement_attempt"] = attempt
        _seed_proposal(proposal)

        recovered = apply_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)
        stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
        branch_head = _git(repo_path, "rev-parse", proposal_branch_name(proposal["id"]))
        check("crash recovery (commit exists): proposal promoted to implemented", recovered["status"] == "implemented" and stored["status"] == "implemented")
        check("crash recovery (commit exists): existing commit sha preserved", stored["implemented_commit_sha"] == expected_head == branch_head)
        check("crash recovery (commit exists): implement_attempt cleared", "implement_attempt" not in stored)
    finally:
        _force_rmtree(repo_path)


def test_retry_transition():
    reset_fixture()
    proposal = _proposal("prop-retry", status="implement-failed")
    proposal["implement_error"] = "test failure"
    _seed_proposal(proposal)
    retried = decide(PROJECT, LOOP, proposal["id"], "retry", by="test", note="retry it")
    stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
    check("review_pending retry transitions implement-failed -> approved", retried["status"] == "approved" and stored["status"] == "approved")
    check("retry transition records the retry decision", stored["decision"]["action"] == "retry")


def test_live_detection_transitions_implemented_to_applied():
    reset_fixture()
    proposal = _proposal("prop-live", status="implemented", page="/blog/loop-agency", keyword="best loop agency")
    proposal["implemented_branch"] = "seo/prop-live"
    proposal["implemented_commit_sha"] = "abc123"
    proposal["implemented_at"] = "2026-07-20T12:00:00Z"
    _seed_proposal(proposal)

    result = run_loop(
        PROJECT,
        LOOP,
        scenario="normal",
        _github_requester=_compare_requester("ahead"),
        _github_compare_target={"owner": "nateginn", "repo": "artwebsite"},
    )
    stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
    check("live detection: implemented proposal transitions to applied", stored["status"] == "applied")
    check("live detection: applied_at is detection time, not commit time", stored["applied_at"] != proposal["implemented_at"])
    check("live detection: run.json records no awaiting entry after promotion", proposal["id"] not in result["run_json"]["awaiting_live_confirmation"])


def test_awaiting_live_confirmation_and_stuck_reporting():
    reset_fixture()
    proposal = _proposal("prop-awaiting", status="implemented", page="/blog/loop-agency", keyword="best loop agency")
    proposal["implemented_branch"] = "seo/prop-awaiting"
    proposal["implemented_commit_sha"] = "deadbeef"
    proposal["implemented_at"] = "2026-07-20T12:00:00Z"
    proposal["implemented_run_cycles_seen"] = 2
    _seed_proposal(proposal)

    result = run_loop(
        PROJECT,
        LOOP,
        scenario="normal",
        _github_requester=_compare_requester("behind"),
        _github_compare_target={"owner": "nateginn", "repo": "artwebsite"},
    )
    stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
    report_text = open(os.path.join(loop_dir, "runs", result["run_id"], "report.md"), "r", encoding="utf-8").read()
    check("awaiting live confirmation: implemented proposal stays implemented", stored["status"] == "implemented")
    check("awaiting live confirmation: proposal listed in awaiting run_json section", proposal["id"] in result["run_json"]["awaiting_live_confirmation"])
    check("stuck implemented: cycle 3+ escalates in run_json", proposal["id"] in result["run_json"]["stuck_implemented"] and stored["implemented_run_cycles_seen"] == 3)
    check("report.md includes awaiting live confirmation and stuck sections", "Awaiting live confirmation" in report_text and "Stuck implemented" in report_text)


def test_cooldown_extends_to_approved_implemented_and_failed():
    reset_fixture()
    approved = _proposal("prop-approved-cooldown", status="approved", page="/blog/ai-marketing", keyword="ai marketing loops")
    implemented = _proposal("prop-implemented-cooldown", status="implemented", page="/blog/loop-agency", keyword="best loop agency")
    implemented["implemented_branch"] = "seo/prop-implemented-cooldown"
    implemented["implemented_commit_sha"] = "abc123"
    implemented["implemented_at"] = "2026-07-20T12:00:00Z"
    failed = _proposal("prop-failed-cooldown", status="implement-failed", page="/blog/seo-automation", keyword="seo automation tool")
    failed["implement_error"] = "test failure"
    _seed_proposal(approved)
    _seed_proposal(implemented)
    _seed_proposal(failed)

    result = run_loop(
        PROJECT,
        LOOP,
        scenario="normal",
        _github_requester=_compare_requester("behind"),
        _github_compare_target={"owner": "nateginn", "repo": "artwebsite"},
    )
    check("cooldown extends to approved/implemented/implement-failed targets", len(result["run_json"]["proposals_created"]) == 0)


def test_verified_delta_surfaces_in_report():
    reset_fixture()
    applied_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    proposal = _proposal("prop-verified-report", status="applied", page="/blog/loop-agency", keyword="best loop agency")
    proposal["baseline_position"] = 8.2
    proposal["applied_at"] = applied_at
    proposal["created_at"] = applied_at
    _seed_proposal(proposal)

    result = run_loop(PROJECT, LOOP, scenario="normal")
    report_text = open(os.path.join(loop_dir, "runs", result["run_id"], "report.md"), "r", encoding="utf-8").read()
    check("verified report section includes proposal id", "prop-verified-report" in report_text)
    check("verified report section includes explicit position delta", "delta +0.0" in report_text)


def test_evaluator_missing_row_is_not_evaluable():
    reset_fixture()
    applied_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    proposal = _proposal("prop-missing-row", status="applied", page="/blog/does-not-exist", keyword="nothing tracks this")
    proposal["baseline_position"] = 12.0
    proposal["applied_at"] = applied_at
    proposal["created_at"] = applied_at
    _seed_proposal(proposal)

    result = run_loop(PROJECT, LOOP, scenario="normal")
    stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
    report_text = open(os.path.join(loop_dir, "runs", result["run_id"], "report.md"), "r", encoding="utf-8").read()
    check("missing row: proposal status stays applied, not falsely verified", stored["status"] == "applied")
    check("missing row: evaluation_outcome is not-evaluable", stored["evaluation_outcome"] == "not-evaluable")
    check("missing row: evaluation_reason recorded", "no metrics row matched" in (stored.get("evaluation_reason") or ""))
    check(
        "missing row: run_json lists it as not-evaluable, not as evaluated",
        proposal["id"] in result["run_json"]["proposals_not_evaluable"] and proposal["id"] not in result["run_json"]["proposals_evaluated"],
    )
    check("missing row: report.md carries a Not evaluable section entry", "Not evaluable this run" in report_text and proposal["id"] in report_text)


def test_evaluator_wrong_keyword_row_is_not_used():
    reset_fixture()
    applied_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    # /blog/loop-agency exists in the mock keyword set, but only under "best loop
    # agency" - the pre-fix bug matched on page alone and would have scored this
    # proposal against that unrelated row's position instead of refusing to evaluate.
    proposal = _proposal("prop-wrong-keyword", status="applied", page="/blog/loop-agency", keyword="totally unrelated keyword")
    proposal["baseline_position"] = 12.0
    proposal["applied_at"] = applied_at
    proposal["created_at"] = applied_at
    _seed_proposal(proposal)

    result = run_loop(PROJECT, LOOP, scenario="normal")
    stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
    check("wrong keyword: same-page-different-keyword row is not falsely verified", stored["status"] == "applied")
    check("wrong keyword: evaluation_outcome is not-evaluable, not verified", stored["evaluation_outcome"] == "not-evaluable")
    check("wrong keyword: mismatched row's id not in this run's evaluated list", proposal["id"] not in result["run_json"]["proposals_evaluated"])


def test_evaluator_ambiguous_duplicate_rows_refused():
    reset_fixture()
    _write_spec(GSC_SPEC)
    applied_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    proposal = _proposal("prop-ambiguous", status="applied", page="/blog/loop-agency", keyword="best loop agency")
    proposal["baseline_position"] = 8.2
    proposal["applied_at"] = applied_at
    proposal["created_at"] = applied_at
    _seed_proposal(proposal)

    def fake_http(url, headers, body_bytes):
        payload = {
            "rows": [
                {"keys": ["Best Loop Agency", "/blog/loop-agency"], "clicks": 20, "impressions": 400, "position": 5.0},
                {"keys": ["  best   loop agency ", "/blog/loop-agency"], "clicks": 20, "impressions": 400, "position": 9.0},
            ]
        }
        return 200, "OK", json.dumps(payload).encode("utf-8")

    result = run_loop(PROJECT, LOOP, _resolve_credential=lambda alias: FAKE_LIVE_TOKEN, _http_post=fake_http)
    stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
    check("ambiguous duplicate rows: refuses to guess, proposal stays applied", stored["status"] == "applied")
    check("ambiguous duplicate rows: evaluation_outcome is not-evaluable", stored["evaluation_outcome"] == "not-evaluable")
    check("ambiguous duplicate rows: reason names the ambiguity", "multiple metrics rows" in (stored.get("evaluation_reason") or ""))
    check("ambiguous duplicate rows: not counted as evaluated", proposal["id"] not in result["run_json"]["proposals_evaluated"])


def test_evaluator_null_position_is_not_evaluable():
    reset_fixture()
    _write_spec(GSC_SPEC)
    applied_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    proposal = _proposal("prop-null-position", status="applied", page="/blog/loop-agency", keyword="best loop agency")
    proposal["baseline_position"] = 8.2
    proposal["applied_at"] = applied_at
    proposal["created_at"] = applied_at
    _seed_proposal(proposal)

    def fake_http(url, headers, body_bytes):
        # GSC omits "position" on rows below its reporting threshold; gsc.py maps
        # that to position: None rather than a number.
        payload = {"rows": [{"keys": ["best loop agency", "/blog/loop-agency"], "clicks": 42, "impressions": 900}]}
        return 200, "OK", json.dumps(payload).encode("utf-8")

    run_loop(PROJECT, LOOP, _resolve_credential=lambda alias: FAKE_LIVE_TOKEN, _http_post=fake_http)
    stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
    check("null position: matched-but-null row is not falsely verified", stored["status"] == "applied")
    check("null position: evaluation_outcome is not-evaluable", stored["evaluation_outcome"] == "not-evaluable")
    check("null position: reason mentions the null position", "null position" in (stored.get("evaluation_reason") or ""))


def test_evaluator_normalizes_case_and_whitespace_for_valid_match():
    reset_fixture()
    applied_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    proposal = _proposal("prop-normalized-match", status="applied", page="/blog/loop-agency", keyword="  Best   LOOP Agency  ")
    proposal["baseline_position"] = 8.2
    proposal["applied_at"] = applied_at
    proposal["created_at"] = applied_at
    _seed_proposal(proposal)

    run_loop(PROJECT, LOOP, scenario="normal")
    stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
    check("normalization: differently-cased/spaced keyword still matches the real row", stored["status"] == "verified")
    check("normalization: evaluation_outcome is verified", stored["evaluation_outcome"] == "verified")


def test_not_evaluable_streak_surfaces_attention_after_three_runs():
    reset_fixture()
    applied_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    proposal = _proposal("prop-streak", status="applied", page="/blog/does-not-exist", keyword="ghost keyword")
    proposal["baseline_position"] = 12.0
    proposal["applied_at"] = applied_at
    proposal["created_at"] = applied_at
    _seed_proposal(proposal)

    last_result = None
    for _ in range(3):
        last_result = run_loop(PROJECT, LOOP, scenario="normal")
    stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
    check("not-evaluable streak: counter reaches 3 after three consecutive not-evaluable runs", stored.get("not_evaluable_streak") == 3)
    check(
        "not-evaluable streak: attention_flags surfaces it on the 3rd run",
        any(proposal["id"] in flag and "3 consecutive runs" in flag for flag in last_result["run_json"]["attention_flags"]),
    )


def test_stale_lock_ttl_comes_from_spec():
    reset_fixture()
    aged_start = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat().replace("+00:00", "Z")
    _write_json(os.path.join(loop_dir, "run.lock"), {"runId": "aged-per-spec-ttl", "pid": os.getpid(), "startTime": aged_start})
    result = run_loop(PROJECT, LOOP, scenario="normal")
    check("lock older than spec.max_run_duration_minutes (5) is recovered as stale, not the 60min default", result["status"] == "ok")
    check("stale lock archived under the run it belonged to", os.path.exists(os.path.join(loop_dir, "runs", "aged-per-spec-ttl", "stale-lock.json")))


def _write_spec(text):
    _write_text(os.path.join(loop_dir, "spec.md"), text)


def test_gsc_dispatch_offline():
    reset_fixture()
    _write_spec(GSC_SPEC)
    calls = []
    windows = []

    def fake_http(url, headers, body_bytes):
        calls.append(url)
        body = json.loads(body_bytes)
        windows.append((date.fromisoformat(body["endDate"]) - date.fromisoformat(body["startDate"])).days + 1)
        payload = {
            "rows": [
                {"keys": ["best loop agency", "/blog/loop-agency"], "clicks": 42, "impressions": 900, "position": 8.2},
                {"keys": ["seo automation tool", "/blog/seo-automation"], "clicks": 18, "impressions": 640, "position": 11.5},
            ]
        }
        return 200, "OK", json.dumps(payload).encode("utf-8")

    result = run_loop(PROJECT, LOOP, _resolve_credential=lambda alias: FAKE_LIVE_TOKEN, _http_post=fake_http)
    check("gsc dispatch: run succeeds offline (fake resolver + fake http_post)", result["status"] == "ok")
    check("gsc dispatch: exactly one call, to the GSC endpoint for the spec's site_url", len(calls) == 1 and "webmasters/v3/sites/sc-domain%3Aexample.com" in calls[0])
    check("gsc dispatch: metrics_window_days 28 queries exactly 28 inclusive days", windows == [28])
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
            payload = {"rows": [{"keys": ["best loop agency", "https://example.com/blog/loop-agency"], "clicks": 42, "impressions": 900, "position": 8.2}]}
        return 200, "OK", json.dumps(payload).encode("utf-8")

    result = run_loop(PROJECT, LOOP, _resolve_credential=lambda alias: FAKE_LIVE_TOKEN, _http_post=fake_http)
    check("merge: gsc+dataforseo run succeeds offline", result["status"] == "ok")
    check("merge: both connectors recorded in tool_calls", [t["tool"] for t in result["run_json"]["tool_calls"]] == ["gsc", "dataforseo"])
    snapshot = _read_json(os.path.join(loop_dir, "runs", result["run_id"], "snapshot.json"))
    row = snapshot["search_analytics"]["keywords"][0]
    check("merge: gsc stays the primary source (clicks/position preserved)", row["clicks"] == 42 and row["position"] == 8.2)
    check("merge: dataforseo path target enriches the full-URL gsc row with serp_position", row.get("serp_position") == 6)


def test_gsc_connector_failure_clean_partial():
    reset_fixture()
    _write_spec(GSC_SPEC)

    def fake_http_401(url, headers, body_bytes):
        return 401, "Unauthorized", f"invalid token: {FAKE_LIVE_TOKEN}".encode("utf-8")

    result = run_loop(PROJECT, LOOP, _resolve_credential=lambda alias: FAKE_LIVE_TOKEN, _http_post=fake_http_401)
    check("gsc failure: status partial-failure", result["status"] == "partial-failure")
    run_json = _read_json(os.path.join(loop_dir, "runs", result["run_id"], "run.json"))
    check("gsc failure: final_status partial-failure", run_json["final_status"] == "partial-failure")
    check("gsc failure: failed tool_call names the gsc connector", run_json["tool_calls"][0]["tool"] == "gsc" and run_json["tool_calls"][0]["ok"] is False)
    check("gsc failure: error surfaces the HTTP status", "401" in run_json["tool_calls"][0]["error"])
    check("gsc failure: token echoed in the API error never reaches run.json", FAKE_LIVE_TOKEN not in json.dumps(run_json))
    check("gsc failure: lock released", not os.path.exists(os.path.join(loop_dir, "run.lock")))


def test_keyword_exclusions_filters_candidates():
    reset_fixture()
    excluded_spec = GOOD_SPEC.replace("inputs:\n  - mock\n", 'inputs:\n  - mock\nkeyword_exclusions:\n  - "ai marketing"\n')
    _write_spec(excluded_spec)
    result = run_loop(PROJECT, LOOP, scenario="normal")
    created = result["run_json"]["proposals_created"]
    check("keyword_exclusions: run still succeeds", result["status"] == "ok")
    check("keyword_exclusions: still creates a proposal", len(created) == 1)
    proposal = _read_json(os.path.join(pending_dir, f"{created[0]}.json"))
    check("keyword_exclusions: excluded keyword not picked", "ai marketing" not in proposal["target"]["keyword"])
    check("keyword_exclusions: next-best candidate picked instead", proposal["target"]["keyword"] == "best loop agency")
    check("keyword_exclusions: exclusion count surfaced in decisions", any("keyword_exclusions filtered" in d for d in result["run_json"]["decisions"]))


def main():
    test_spec_validation_rejects_bad_spec()
    test_lock_refusal_and_stale_recovery()
    test_redaction_never_leaks_secret()
    test_partial_failure_clean_log()
    test_pause_on_breach_blocks_new_proposals()
    test_apply_success_and_tier2_refusal()
    test_draft_copy_succeeds_on_draft_status()
    test_draft_copy_refuses_past_draft_status()
    test_draft_copy_end_to_end_apply()
    test_apply_refuses_stale_previous_value()
    test_manual_approval_only_hard_refusal()
    test_propose_only_refuses_tier1_apply()
    test_apply_and_review_pending_block_on_a_concurrent_run_lock_holder()
    test_draft_copy_blocks_on_a_concurrent_run_lock_holder()
    test_promote_live_implementations_no_nested_lock_deadlock()
    test_run_loop_promotion_survives_end_of_run_write()
    test_apply_recovers_from_a_stale_run_lock_left_by_a_crashed_apply()
    test_crash_recovery_no_commit_path()
    test_crash_recovery_commit_exists_path()
    test_retry_transition()
    test_live_detection_transitions_implemented_to_applied()
    test_awaiting_live_confirmation_and_stuck_reporting()
    test_cooldown_extends_to_approved_implemented_and_failed()
    test_verified_delta_surfaces_in_report()
    test_evaluator_missing_row_is_not_evaluable()
    test_evaluator_wrong_keyword_row_is_not_used()
    test_evaluator_ambiguous_duplicate_rows_refused()
    test_evaluator_null_position_is_not_evaluable()
    test_evaluator_normalizes_case_and_whitespace_for_valid_match()
    test_not_evaluable_streak_surfaces_attention_after_three_runs()
    test_stale_lock_ttl_comes_from_spec()
    test_gsc_dispatch_offline()
    test_gsc_dataforseo_merge()
    test_gsc_connector_failure_clean_partial()
    test_keyword_exclusions_filters_candidates()

    _force_rmtree(project_dir)

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

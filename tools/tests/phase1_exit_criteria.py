# Integration tests for AgentColabPlan.md Milestone-1 exit criteria.
# Operates on a disposable projects/_phase1-test-tmp/ fixture (created and
# torn down here) so it never touches the real projects/_demo run history
# used for the separate "two consecutive dry runs" proof.
import glob
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
    from tools.codex_review_proposal import adjudicate_proposal, auto_implement, evidence_packet, record_round, start_review  # noqa: E402
    from tools.draft_copy import draft_copy  # noqa: E402
    from tools.draft_link import draft_link  # noqa: E402
    from tools.publish import publish_proposal  # noqa: E402
    from tools.review_pending import decide, list_proposals, resolve_breach  # noqa: E402
    from tools.run_loop import run_loop, _promote_live_implementations  # noqa: E402
    from tools.lib.artwebsite_seo import cleanup_worktree, create_worktree, make_attempt, proposal_branch_name  # noqa: E402
    from tools.lib.event_log import read_events, reconcile as event_log_reconcile  # noqa: E402
except ImportError:
    if TOOLS_DIR not in sys.path:
        sys.path.insert(0, TOOLS_DIR)
    from apply import apply_proposal  # noqa: E402
    from codex_review_proposal import adjudicate_proposal, auto_implement, evidence_packet, record_round, start_review  # noqa: E402
    from draft_copy import draft_copy  # noqa: E402
    from draft_link import draft_link  # noqa: E402
    from publish import publish_proposal  # noqa: E402
    from review_pending import decide, list_proposals, resolve_breach  # noqa: E402
    from run_loop import run_loop, _promote_live_implementations  # noqa: E402
    from lib.artwebsite_seo import cleanup_worktree, create_worktree, make_attempt, proposal_branch_name  # noqa: E402
    from lib.event_log import read_events, reconcile as event_log_reconcile  # noqa: E402

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

# Phase 7 (PLAN-PHASE7-CODEX-REVIEW.md): opts into the codex-review
# auto-implementation path for the fixture project only - two of the three
# independent gates (approval_mode: tier1-enabled already in GOOD_SPEC,
# auto_implementation_enabled: true here); the third (manual_approval_only)
# is left at its GOOD_SPEC default of absent/false on title-tag-rewrite.
AUTO_IMPL_SPEC = GOOD_SPEC.replace(
    "approval_mode: tier1-enabled\n",
    "approval_mode: tier1-enabled\nauto_implementation_enabled: true\n",
)

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
    # _make_temp_git_site() pairs every working repo with a bare "origin"
    # remote at this sibling path (needed so Phase 5's verify_main_baseline
    # has something real to fetch) - tear it down alongside the repo.
    origin_path = path + "-origin"
    if os.path.exists(origin_path):
        for root, _dirs, files in os.walk(origin_path):
            for name in files:
                try:
                    os.chmod(os.path.join(root, name), stat.S_IWRITE | stat.S_IREAD)
                except OSError:
                    pass
        shutil.rmtree(origin_path, ignore_errors=True)


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


GOOD_DEPLOY_WORKFLOW_YAML = "name: Deploy to Production\n\non:\n  push:\n    branches: [ main ]\n\njobs:\n  deploy:\n    runs-on: ubuntu-latest\n"


def _make_temp_git_site():
    repo_path = tempfile.mkdtemp(prefix="artwebsite-fixture-")
    os.makedirs(os.path.join(repo_path, "templates"), exist_ok=True)
    os.makedirs(os.path.join(repo_path, "app"), exist_ok=True)
    os.makedirs(os.path.join(repo_path, ".github", "workflows"), exist_ok=True)
    _write_text(
        os.path.join(repo_path, "templates", "services.html"),
        "{% block title %}Old service title{% endblock %}\n{% block meta_description %}Old service meta{% endblock %}\n",
    )
    _write_text(os.path.join(repo_path, ".github", "workflows", "deploy.yml"), GOOD_DEPLOY_WORKFLOW_YAML)
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

    # Phase 5's verify_main_baseline() fetches origin and compares
    # refs/heads/main to refs/remotes/origin/main, so the fixture needs a
    # real origin remote whose main starts in sync with the local one - a
    # bare repo at a sibling path (torn down by _force_rmtree alongside
    # repo_path) plays that role without touching the network.
    origin_path = repo_path + "-origin"
    os.makedirs(origin_path, exist_ok=True)
    _git(origin_path, "init", "--bare", "-b", "main")
    _git(repo_path, "remote", "add", "origin", origin_path)
    _git(repo_path, "push", "origin", "main")
    _git(repo_path, "fetch", "origin")
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
        # Push so refs/heads/main and refs/remotes/origin/main still agree -
        # this test is proving the previous_value content check catches the
        # drift, not Phase 5's separate baseline-drift check (covered by its
        # own test below).
        _git(repo_path, "push", "origin", "main")

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
        check(
            "crash recovery (commit exists): implementation_base_sha backfilled from the recovered attempt, surviving implement_attempt removal",
            stored.get("implementation_base_sha") == attempt["base_commit"],
        )
    finally:
        _force_rmtree(repo_path)


def test_crash_recovery_legacy_attempt_without_base_sha_backfill():
    # A proposal already fully `implemented` under pre-Phase-5 code has no
    # implementation_base_sha field at all (the field didn't exist yet).
    # apply_proposal must still treat it as an idempotent no-op - nothing may
    # require the new field to be present on old data.
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        proposal = _proposal("prop-legacy-implemented", status="implemented")
        proposal["implemented_branch"] = "seo/prop-legacy-implemented"
        proposal["implemented_commit_sha"] = "legacy1234567890"
        proposal["implemented_at"] = "2026-07-20T12:00:00Z"
        _seed_proposal(proposal)

        result = apply_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)
        check(
            "legacy implemented proposal with no implementation_base_sha: apply_proposal is an idempotent no-op, not a crash",
            result["status"] == "implemented" and result["implemented_commit_sha"] == "legacy1234567890",
        )
    finally:
        _force_rmtree(repo_path)


def test_apply_refuses_on_main_baseline_drift():
    # If origin/main has moved (someone pushed directly) since this
    # checkout's local main was last updated, apply.py must refuse rather
    # than branch its worktree off a stale or diverged base.
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        proposal = _proposal("prop-baseline-drift", status="approved")
        _seed_proposal(proposal)

        # Push a commit to origin's main from a throwaway branch, without
        # ever updating this checkout's local refs/heads/main - after a
        # fetch, refs/heads/main and refs/remotes/origin/main disagree.
        _git(repo_path, "checkout", "-b", "drift-temp")
        _write_text(os.path.join(repo_path, "templates", "services.html"), "{% block title %}Drifted upstream{% endblock %}\n{% block meta_description %}Old service meta{% endblock %}\n")
        _git(repo_path, "add", "-A")
        _git(repo_path, "commit", "-m", "pushed straight to origin/main")
        _git(repo_path, "push", "origin", "drift-temp:refs/heads/main")
        _git(repo_path, "checkout", "main")
        _git(repo_path, "branch", "-D", "drift-temp")

        leak_glob = os.path.join(tempfile.gettempdir(), "looping-artwebsite-*")
        before_leak_check = set(glob.glob(leak_glob))

        refused = False
        try:
            apply_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)
        except ValueError as e:
            refused = "baseline drift" in str(e) and "refs/heads/main" in str(e) and "refs/remotes/origin/main" in str(e)
        stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
        check("apply.py refuses on main baseline drift (origin ahead of local)", refused)
        check("baseline drift: proposal left approved, untouched (no implement_attempt written)", stored["status"] == "approved" and "implement_attempt" not in stored)
        branches = _git(repo_path, "worktree", "list")
        check("baseline drift: no worktree was created before the refusal", branches.count("\n") == 0 or len(branches.strip().splitlines()) == 1)
        after_leak_check = set(glob.glob(leak_glob))
        check("baseline drift: no temp worktree directory leaked by the refused apply", after_leak_check == before_leak_check)
    finally:
        _force_rmtree(repo_path)


def test_make_attempt_and_create_worktree_never_use_a_bare_main_ref():
    # Phase 5: every place that used to resolve the bare name `main` must use
    # a fully-qualified ref (refs/heads/main, refs/remotes/origin/main)
    # instead, so a stray tag/symref named `main` can't resolve to the wrong
    # thing. Prove it by recording every git argv make_attempt()/
    # create_worktree() issue against a real repo.
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        calls = []

        def recording_runner(args, cwd):
            calls.append(list(args))
            completed = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)
            return completed.stdout.strip()

        attempt = make_attempt(repo_path, "prop-fq-refs", git_runner=recording_runner)
        create_worktree(repo_path, attempt, git_runner=recording_runner)

        bare_main_used = any(arg == "main" for call in calls for arg in call)
        fq_heads_used = any(arg == "refs/heads/main" for call in calls for arg in call)
        fq_remote_used = any(arg == "refs/remotes/origin/main" for call in calls for arg in call)
        check("make_attempt/create_worktree never pass a bare 'main' git argument", not bare_main_used)
        check("make_attempt resolves refs/heads/main explicitly", fq_heads_used)
        check("make_attempt resolves refs/remotes/origin/main explicitly", fq_remote_used)

        cleanup_worktree(repo_path, attempt, git_runner=recording_runner, delete_branch=True)
    finally:
        _force_rmtree(repo_path)


def test_apply_persists_implementation_base_sha_surviving_attempt_removal():
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        proposal = _proposal("prop-base-sha-persist", status="approved")
        _seed_proposal(proposal)
        expected_base_sha = _git(repo_path, "rev-parse", "refs/heads/main")

        implemented = apply_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)
        stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
        check("apply.py persists implementation_base_sha on the proposal", implemented.get("implementation_base_sha") == expected_base_sha)
        check(
            "implementation_base_sha survives implement_attempt being popped on success",
            "implement_attempt" not in stored and stored.get("implementation_base_sha") == expected_base_sha,
        )
    finally:
        _force_rmtree(repo_path)


def test_apply_refuses_on_inconsistent_commit_ancestry():
    # Defense-in-depth: even though apply.py creates the implementation
    # commit itself, verify its parent really is the recorded base SHA
    # before marking it implemented - the same check a later publish step
    # needs to trust the commit it's about to push.
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        proposal = _proposal("prop-ancestry-mismatch", status="approved")
        _seed_proposal(proposal)

        def tampering_runner(args, cwd):
            if len(args) >= 3 and args[1] == "rev-parse" and args[2].endswith("^"):
                return "0" * 40
            completed = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)
            return completed.stdout.strip()

        result = apply_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path, git_runner=tampering_runner)
        stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
        check("ancestry mismatch: apply.py refuses and marks implement-failed, not implemented", result["status"] == "implement-failed" and stored["status"] == "implement-failed")
        check("ancestry mismatch: implement_error names the ancestry failure", "inconsistent commit ancestry" in stored.get("implement_error", ""))
    finally:
        _force_rmtree(repo_path)


def _approved_publishable_proposal(proposal_id, **overrides):
    proposal = _proposal(proposal_id, status="approved", implementation_value=overrides.pop("implementation_value", "New service title for publish e2e"))
    proposal["decision"] = {"action": "approve", "by": "nate", "note": "looks good", "at": "2026-07-29T12:00:00Z"}
    proposal.update(overrides)
    return proposal


def _good_branch_protection_requester(url, headers):
    # Phase 0 protection verification queries two mechanisms combined
    # (github_compare.verify_branch_protection(): effective rules + classic
    # protection + rulesets) - a compliant fake must serve all three GET
    # endpoints. An empty rulesets list is a legitimate "protection is
    # fully classic, no rulesets configured" configuration.
    if "/rules/branches/" in url:
        effective_rules = [
            {"type": "pull_request", "parameters": {"required_approving_review_count": 1}},
            {"type": "non_fast_forward", "parameters": {}},
            {"type": "deletion", "parameters": {}},
        ]
        return 200, "OK", json.dumps(effective_rules).encode("utf-8")
    if url.endswith("/rulesets"):
        return 200, "OK", b"[]"
    payload = {
        "required_pull_request_reviews": {"required_approving_review_count": 1, "bypass_pull_request_allowances": {}},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "enforce_admins": {"enabled": True},
    }
    return 200, "OK", json.dumps(payload).encode("utf-8")


def _unprotected_branch_requester(url, headers):
    # Genuinely no protection: the effective-rules endpoint (GitHub's own
    # merge of classic protection + rulesets) reports nothing enforced.
    if "/rules/branches/" in url:
        return 200, "OK", b"[]"
    if url.endswith("/rulesets"):
        return 200, "OK", b"[]"
    return 404, "Not Found", b"{}"


def _good_pr_and_automerge_poster(pr_number, commit_sha):
    def poster(url, headers, body):
        if url.endswith("/pulls"):
            payload = {
                "number": pr_number,
                "html_url": f"https://github.com/nateginn/artwebsite/pull/{pr_number}",
                "node_id": f"PR_e2e_{pr_number}",
                "head": {"sha": commit_sha},
            }
            return 201, "Created", json.dumps(payload).encode("utf-8")
        payload = {"data": {"enablePullRequestAutoMerge": {"pullRequest": {"autoMergeRequest": {"enabledAt": "2026-07-30T00:00:00Z"}}}}}
        return 200, "OK", json.dumps(payload).encode("utf-8")

    return poster


def _real_git_with_faked_origin_push_url(fake_url="https://github.com/nateginn/artwebsite.git"):
    """Real git for everything (diff-tree, show, rev-parse, push all run for
    real against the fixture's actual bare-origin remote) except the one
    value publish.py cannot get from a local-only fixture: origin's push
    URL, which a real local test repo points at a filesystem path, not
    github.com. `git push origin <sha>:refs/heads/<branch>` still resolves
    the "origin" remote from the real git config regardless of what this
    intercepted get-url call reports, so the push itself is exercised for
    real."""

    def runner(args, cwd):
        if args[:5] == ["git", "remote", "get-url", "--push", "origin"]:
            return fake_url
        completed = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)
        return completed.stdout.strip()

    return runner


def test_publish_end_to_end_real_git_push_pr_and_auto_merge():
    # The one true end-to-end proof: draft -> approve -> apply (real git
    # worktree/commit) -> publish (real git diff-tree/show/push against the
    # fixture's real bare origin, plus faked GitHub HTTP responses since no
    # network call is allowed in this suite).
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        proposal = _approved_publishable_proposal("prop-2026-07-24T03-05-54-719Z-e2epub-0")
        _seed_proposal(proposal)

        implemented = apply_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)
        check("publish e2e: apply succeeds first", implemented["status"] == "implemented")

        published = publish_proposal(
            PROJECT,
            LOOP,
            proposal["id"],
            repo_path=repo_path,
            github_owner="nateginn",
            github_repo="artwebsite",
            token="fake-token",
            git_runner=_real_git_with_faked_origin_push_url(),
            requester=_good_branch_protection_requester,
            poster=_good_pr_and_automerge_poster(99, implemented["implemented_commit_sha"]),
        )
        check("publish e2e: real push+PR+auto-merge succeeds", published.get("pr_number") == 99 and published.get("auto_merge_enabled") is True)
        check("publish e2e: pr_url recorded", published.get("pr_url") == "https://github.com/nateginn/artwebsite/pull/99")

        origin_path = repo_path + "-origin"
        landed_sha = _git(origin_path, "rev-parse", f'refs/heads/{proposal_branch_name(proposal["id"])}')
        check("publish e2e: the implementation commit actually landed on the real bare origin remote", landed_sha == implemented["implemented_commit_sha"])
    finally:
        _force_rmtree(repo_path)


def test_publish_refuses_on_multi_file_commit_using_real_git():
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        proposal = _approved_publishable_proposal("prop-2026-07-24T03-05-54-719Z-e2emulti-0")
        _seed_proposal(proposal)
        implemented = apply_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)

        # Tamper: amend the real implementation commit so it touches a second
        # file too - a real multi-file diff, not a simulated one.
        branch = proposal_branch_name(proposal["id"])
        worktree_parent = tempfile.mkdtemp(prefix="artwebsite-publish-tamper-")
        worktree_path = os.path.join(worktree_parent, "tamper")
        try:
            _git(repo_path, "worktree", "add", worktree_path, branch)
            _write_text(os.path.join(worktree_path, "app", "views.py"), "# tampered extra change\n")
            _git(worktree_path, "add", "-A")
            _git(worktree_path, "commit", "--amend", "--no-edit")
            new_head = _git(worktree_path, "rev-parse", "HEAD")
        finally:
            _git(repo_path, "worktree", "remove", worktree_path, "--force")
            shutil.rmtree(worktree_parent, ignore_errors=True)

        stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
        stored["implemented_commit_sha"] = new_head
        _write_json(os.path.join(pending_dir, f'{proposal["id"]}.json'), stored)

        refused = False
        try:
            publish_proposal(
                PROJECT, LOOP, proposal["id"],
                repo_path=repo_path, github_owner="nateginn", github_repo="artwebsite", token="fake-token",
                git_runner=_real_git_with_faked_origin_push_url(),
                requester=_good_branch_protection_requester, poster=_good_pr_and_automerge_poster(1, new_head),
            )
        except ValueError as e:
            refused = "changes 2 file(s)" in str(e)
        check("publish.py refuses a real 2-file commit via real git diff-tree, before any push", refused)

        origin_path = repo_path + "-origin"
        branch_on_origin = _git(origin_path, "branch", "--list", branch)
        check("publish.py's refusal on a multi-file diff never pushed the branch to origin", branch_on_origin == "")
    finally:
        _force_rmtree(repo_path)


def test_publish_refuses_when_deploy_workflow_trigger_shape_has_drifted():
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        # Drift the workflow's trigger shape on main (post-fixture-init, so
        # it becomes part of the base commit apply.py branches from) before
        # any proposal is implemented.
        _write_text(os.path.join(repo_path, ".github", "workflows", "deploy.yml"), "on:\n  push:\n    branches: [ main, staging ]\n")
        _git(repo_path, "add", "-A")
        _git(repo_path, "commit", "-m", "drift deploy trigger shape")
        _git(repo_path, "push", "origin", "main")

        proposal = _approved_publishable_proposal("prop-2026-07-24T03-05-54-719Z-e2eshape-0")
        _seed_proposal(proposal)
        implemented = apply_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)

        refused = False
        try:
            publish_proposal(
                PROJECT, LOOP, proposal["id"],
                repo_path=repo_path, github_owner="nateginn", github_repo="artwebsite", token="fake-token",
                git_runner=_real_git_with_faked_origin_push_url(),
                requester=_good_branch_protection_requester, poster=_good_pr_and_automerge_poster(2, implemented["implemented_commit_sha"]),
            )
        except ValueError as e:
            refused = "branches is" in str(e)
        check("publish.py refuses a real base commit whose deploy.yml trigger shape has drifted, before any push", refused)

        origin_path = repo_path + "-origin"
        branch = proposal_branch_name(proposal["id"])
        branch_on_origin = _git(origin_path, "branch", "--list", branch)
        check("publish.py's refusal on a drifted deploy.yml never pushed the branch to origin", branch_on_origin == "")
    finally:
        _force_rmtree(repo_path)


def test_publish_refuses_when_branch_protection_is_missing():
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        proposal = _approved_publishable_proposal("prop-2026-07-24T03-05-54-719Z-e2eunprot-0")
        _seed_proposal(proposal)
        implemented = apply_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)

        refused = False
        try:
            publish_proposal(
                PROJECT, LOOP, proposal["id"],
                repo_path=repo_path, github_owner="nateginn", github_repo="artwebsite", token="fake-token",
                git_runner=_real_git_with_faked_origin_push_url(),
                requester=_unprotected_branch_requester, poster=_good_pr_and_automerge_poster(3, implemented["implemented_commit_sha"]),
            )
        except ValueError as e:
            refused = "no effective pull_request rule found" in str(e)
        check("publish.py hard-refuses on real git when main has no branch protection, before any push", refused)

        origin_path = repo_path + "-origin"
        branch = proposal_branch_name(proposal["id"])
        branch_on_origin = _git(origin_path, "branch", "--list", branch)
        check("publish.py's refusal on missing branch protection never pushed the branch to origin", branch_on_origin == "")
    finally:
        _force_rmtree(repo_path)


def test_publish_refuses_on_real_non_github_remote_url():
    # Without the faked get-url override, the fixture's real bare-repo
    # remote is a filesystem path, not github.com - proving the push-
    # destination check runs against the real `git remote get-url --push`
    # output and correctly refuses a non-matching remote.
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        proposal = _approved_publishable_proposal("prop-2026-07-24T03-05-54-719Z-e2eremote-0")
        _seed_proposal(proposal)
        implemented = apply_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)

        refused = False
        try:
            publish_proposal(
                PROJECT, LOOP, proposal["id"],
                repo_path=repo_path, github_owner="nateginn", github_repo="artwebsite", token="fake-token",
                requester=_good_branch_protection_requester, poster=_good_pr_and_automerge_poster(4, implemented["implemented_commit_sha"]),
            )
        except ValueError as e:
            refused = "possible pushurl/insteadOf rewrite" in str(e)
        check("publish.py refuses the fixture's real non-github.com origin URL, before any push", refused)
    finally:
        _force_rmtree(repo_path)


def _ruleset_bypass_requester(url, headers):
    # Classic protection and the effective-rules endpoint both look fully
    # compliant on their own; the hole is a repository ruleset that also
    # targets main, is actively enforced, and lists a bypass actor -
    # PLAN.md Phase 0c-bis's exact scenario ("classic data alone does not
    # prove absence of a ruleset bypass actor").
    if "/rules/branches/" in url:
        effective_rules = [
            {"type": "pull_request", "parameters": {"required_approving_review_count": 1}},
            {"type": "non_fast_forward", "parameters": {}},
            {"type": "deletion", "parameters": {}},
        ]
        return 200, "OK", json.dumps(effective_rules).encode("utf-8")
    if url.endswith("/rulesets"):
        ruleset_summary = {"id": 900, "name": "main-bypass", "target": "branch", "enforcement": "active"}
        return 200, "OK", json.dumps([ruleset_summary]).encode("utf-8")
    if "/rulesets/" in url:
        ruleset_detail = {
            "id": 900, "name": "main-bypass", "target": "branch", "enforcement": "active",
            "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
            "rules": [], "bypass_actors": [{"actor_id": 1, "actor_type": "RepositoryRole"}],
        }
        return 200, "OK", json.dumps(ruleset_detail).encode("utf-8")
    payload = {
        "required_pull_request_reviews": {"required_approving_review_count": 1, "bypass_pull_request_allowances": {}},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "enforce_admins": {"enabled": True},
    }
    return 200, "OK", json.dumps(payload).encode("utf-8")


def test_publish_refuses_when_a_ruleset_bypass_actor_exists_on_real_main():
    reset_fixture()
    repo_path = _make_temp_git_site()
    try:
        proposal = _approved_publishable_proposal("prop-2026-07-24T03-05-54-719Z-e2erulesetbp-0")
        _seed_proposal(proposal)
        implemented = apply_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)

        refused = False
        try:
            publish_proposal(
                PROJECT, LOOP, proposal["id"],
                repo_path=repo_path, github_owner="nateginn", github_repo="artwebsite", token="fake-token",
                git_runner=_real_git_with_faked_origin_push_url(),
                requester=_ruleset_bypass_requester, poster=_good_pr_and_automerge_poster(5, implemented["implemented_commit_sha"]),
            )
        except ValueError as e:
            refused = "bypass actor" in str(e) and "900" in str(e)
        check(
            "publish.py refuses when classic protection looks compliant but an active ruleset targeting main has a bypass actor",
            refused,
        )

        origin_path = repo_path + "-origin"
        branch = proposal_branch_name(proposal["id"])
        branch_on_origin = _git(origin_path, "branch", "--list", branch)
        check("publish.py's refusal on a ruleset bypass actor never pushed the branch to origin", branch_on_origin == "")
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


def test_promote_live_implementations_invokes_on_promoted_callback():
    # Phase 6a integration point: _promote_live_implementations() accepts
    # an optional on_promoted hook (deploy_verify.record_deployment() is
    # the intended real caller, wired outside run_loop.py so this module
    # stays decoupled) - default None must remain a strict no-op (already
    # covered by every other live-detection test above using no callback),
    # and a supplied callback must fire exactly once per promotion with
    # the fresh applied proposal, and never crash/block the run if it
    # itself raises.
    reset_fixture()
    proposal = _proposal("prop-on-promoted", status="implemented", page="/blog/loop-agency", keyword="best loop agency")
    proposal["implemented_branch"] = "seo/prop-on-promoted"
    proposal["implemented_commit_sha"] = "newsha000"
    proposal["implementation_base_sha"] = "oldsha000"
    proposal["implemented_at"] = "2026-07-20T12:00:00Z"
    _seed_proposal(proposal)

    promoted_calls = []
    result = run_loop(
        PROJECT,
        LOOP,
        scenario="normal",
        _github_requester=_compare_requester("ahead"),
        _github_compare_target={"owner": "nateginn", "repo": "artwebsite"},
        _on_promoted=lambda p: promoted_calls.append(p),
    )
    check("on_promoted fires exactly once for the one promoted proposal", len(promoted_calls) == 1)
    check("on_promoted receives the fresh proposal with its new and previous SHAs", promoted_calls[0]["implemented_commit_sha"] == "newsha000" and promoted_calls[0]["implementation_base_sha"] == "oldsha000")
    check("on_promoted receives the already-applied status", promoted_calls[0]["status"] == "applied")
    stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
    check("on_promoted firing does not change the promotion's own outcome", stored["status"] == "applied")

    reset_fixture()
    proposal2 = _proposal("prop-on-promoted-raises", status="implemented", page="/blog/loop-agency", keyword="best loop agency")
    proposal2["implemented_branch"] = "seo/prop-on-promoted-raises"
    proposal2["implemented_commit_sha"] = "newsha111"
    proposal2["implementation_base_sha"] = "oldsha111"
    proposal2["implemented_at"] = "2026-07-20T12:00:00Z"
    _seed_proposal(proposal2)

    def _raising_hook(p):
        raise RuntimeError("deploy_verify.record_deployment blew up")

    result2 = run_loop(
        PROJECT,
        LOOP,
        scenario="normal",
        _github_requester=_compare_requester("ahead"),
        _github_compare_target={"owner": "nateginn", "repo": "artwebsite"},
        _on_promoted=_raising_hook,
    )
    stored2 = _read_json(os.path.join(pending_dir, f'{proposal2["id"]}.json'))
    check("a raising on_promoted hook never blocks or crashes the run", result2["status"] == "ok")
    check("a raising on_promoted hook never prevents the promotion itself", stored2["status"] == "applied")
    check("a raising on_promoted hook's failure is surfaced in decisions, not swallowed silently", any("on_promoted deploy-verification hook failed" in d for d in result2["run_json"]["decisions"]))


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


# ---------------------------------------------------------------------------
# Phase 7 (PLAN-PHASE7-CODEX-REVIEW.md): codex-review auto-implementation
# pipeline - offline end-to-end coverage. No live SEO run, live GitHub call,
# or live Telegram send anywhere below; verdicts are fabricated dicts (the
# .claude/skills/codex-seo-review skill is what actually invokes `codex
# exec` in a real Claude Code session).
# ---------------------------------------------------------------------------


def test_codex_review_end_to_end_with_revision_and_auto_implement():
    reset_fixture()
    _write_spec(AUTO_IMPL_SPEC)
    repo_path = _make_temp_git_site()
    try:
        proposal = _proposal("prop-codex-e2e", status="draft", page="/services/")
        del proposal["implementation"]
        _seed_proposal(proposal)

        draft_copy(PROJECT, LOOP, proposal["id"], "Pass One Services Title", "claude-test", repo_path=repo_path)
        started = start_review(PROJECT, LOOP, proposal["id"])
        check("start_review moves draft -> review-pending", started["status"] == "review-pending")

        threw_start_review_twice = False
        try:
            start_review(PROJECT, LOOP, proposal["id"])
        except ValueError as e:
            threw_start_review_twice = "not \"draft\"" in str(e)
        check("start_review refuses a proposal that already left draft", threw_start_review_twice)

        packet1 = evidence_packet(PROJECT, LOOP, proposal["id"])
        check("evidence_packet includes the proposed value", packet1["proposed_value"] == "Pass One Services Title")
        packet1_again = evidence_packet(PROJECT, LOOP, proposal["id"])
        check("re-calling evidence_packet for the same pass returns the identical persisted artifact", packet1_again["evidence_packet_hash"] == packet1["evidence_packet_hash"])

        threw_stale_hash = False
        try:
            record_round(PROJECT, LOOP, proposal["id"], 1, {"verdict": "approve", "confidence": 0.5, "objections": [], "required_corrections": [], "evidence_packet_hash": "not-the-real-hash"})
        except ValueError as e:
            threw_stale_hash = "does not match" in str(e)
        check("record_round refuses a verdict declaring the wrong evidence_packet_hash", threw_stale_hash)

        # Pass 1: round A holds with a correctable objection, round B approves - disagreement.
        record_round(PROJECT, LOOP, proposal["id"], 1, {"verdict": "hold", "confidence": 0.6, "objections": ["title could be more specific"], "required_corrections": ["mention 'Denver' explicitly"], "evidence_packet_hash": packet1["evidence_packet_hash"]})
        record_round(PROJECT, LOOP, proposal["id"], 2, {"verdict": "approve", "confidence": 0.8, "objections": [], "required_corrections": [], "evidence_packet_hash": packet1["evidence_packet_hash"]})

        threw_out_of_order = False
        try:
            record_round(PROJECT, LOOP, proposal["id"], 5, {"verdict": "approve", "confidence": 0.5, "objections": [], "required_corrections": [], "evidence_packet_hash": packet1["evidence_packet_hash"]})
        except ValueError as e:
            threw_out_of_order = "out of order" in str(e)
        check("record_round refuses an out-of-order round number", threw_out_of_order)

        after_pass1 = adjudicate_proposal(PROJECT, LOOP, proposal["id"])
        check("adjudicate on a pass-1 disagreement moves to review-revision-needed", after_pass1["status"] == "review-revision-needed")

        revised = draft_copy(PROJECT, LOOP, proposal["id"], "Pass One Services Title - Denver", "claude-test", repo_path=repo_path)
        check("revision produces review-pending for pass 2", revised["status"] == "review-pending")

        packet2 = evidence_packet(PROJECT, LOOP, proposal["id"])
        check("pass-2 evidence packet reflects the revised copy", packet2["proposed_value"] == "Pass One Services Title - Denver")
        check("pass-2 packet is a distinct artifact from pass-1's", packet2["evidence_packet_hash"] != packet1["evidence_packet_hash"])

        record_round(PROJECT, LOOP, proposal["id"], 3, {"verdict": "approve", "confidence": 0.9, "objections": [], "required_corrections": [], "evidence_packet_hash": packet2["evidence_packet_hash"]})
        record_round(PROJECT, LOOP, proposal["id"], 4, {"verdict": "approve", "confidence": 0.85, "objections": [], "required_corrections": [], "evidence_packet_hash": packet2["evidence_packet_hash"]})

        adjudicated = adjudicate_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)
        check("adjudicate on pass-2 agreement moves to approved-for-implementation", adjudicated["status"] == "approved-for-implementation")
        check("adjudicate correctly used pass-2 rounds, not pass-1's disagreement, to reach agreement", adjudicated["review"]["final_adjudication"] == "approve")
        check("auto_implementation.eligible is true and recorded as output-only", adjudicated["auto_implementation"]["eligible"] is True)

        implemented = auto_implement(PROJECT, LOOP, proposal["id"], repo_path=repo_path)
        check("auto_implement produces a real local commit", implemented["status"] == "implemented" and implemented["implemented_by"] == "codex-review-pipeline")

        branch_head = _git(repo_path, "rev-parse", proposal_branch_name(proposal["id"]))
        committed = subprocess.run(["git", "show", f"{branch_head}:templates/services.html"], cwd=repo_path, capture_output=True, text=True, check=True).stdout
        check("committed content contains the revised, pass-2-approved copy", "Pass One Services Title - Denver" in committed)
        check("committed content does not contain the pre-revision pass-1 copy", "Pass One Services Title" not in committed.replace("Pass One Services Title - Denver", ""))

        events = read_events(loop_dir, proposal_id=proposal["id"])
        event_types = [e["event_type"] for e in events]
        check("event log has exactly one review_started", event_types.count("review_started") == 1)
        check("event log has exactly 4 review_round_completed events", event_types.count("review_round_completed") == 4)
        check("event log has exactly one proposal_revised", event_types.count("proposal_revised") == 1)
        check("event log has exactly one proposal_auto_approved", event_types.count("proposal_auto_approved") == 1)
        check("event log has exactly one implementation_created", event_types.count("implementation_created") == 1)
        seqs = [e["seq"] for e in events]
        check("event seqs are unique and monotonically increasing", seqs == sorted(seqs) and len(seqs) == len(set(seqs)))

        # Idempotency: retrying an already-completed apply must not duplicate the event.
        retried = apply_proposal(PROJECT, LOOP, proposal["id"], by="codex-review-pipeline", repo_path=repo_path)
        check("retrying apply_proposal on an already-implemented proposal is a safe no-op", retried["status"] == "implemented")
        events_after_retry = read_events(loop_dir, proposal_id=proposal["id"])
        check("retrying an already-completed apply does not duplicate its event", sum(1 for e in events_after_retry if e["event_type"] == "implementation_created") == 1)
    finally:
        _force_rmtree(repo_path)


def test_codex_review_missing_evidence_holds():
    reset_fixture()
    _write_spec(AUTO_IMPL_SPEC.replace(
        "  - type: title-tag-rewrite\n    tier: 1\n    rollback: revert PR\n    observation_window_days: 0.0001\n    min_sample_size: 100\n",
        "  - type: title-tag-rewrite\n    tier: 1\n    rollback: revert PR\n    observation_window_days: 0.0001\n    min_sample_size: 100\n"
        "  - type: internal-link-addition\n    tier: 1\n    rollback: revert PR\n    observation_window_days: 0.0001\n    min_sample_size: 100\n",
    ))
    proposal = {
        "id": "prop-missing-evidence", "loop": LOOP, "action_type": "internal-link-addition", "tier": 1,
        "target": {"page": "/blog/post/", "keyword": "x"}, "baseline_position": 8.2, "rationale": "test",
        "rollback": "revert PR", "manual_approval_only": False, "status": "draft", "created_run_id": "seed",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "run_cycles_seen": 0,
        "decision": None, "applied_at": None, "observation_window_days": 0.0001, "min_sample_size": 100,
    }
    _seed_proposal(proposal)
    start_review(PROJECT, LOOP, proposal["id"])

    threw_missing = False
    try:
        evidence_packet(PROJECT, LOOP, proposal["id"])
    except ValueError as e:
        threw_missing = "missing required evidence" in str(e)
    check("evidence_packet refuses an internal-link proposal with no source/destination/anchor drafted", threw_missing)
    stored = _read_json(os.path.join(pending_dir, f'{proposal["id"]}.json'))
    check("a proposal missing required evidence is moved to review-held, never submitted for review", stored["status"] == "review-held")


def test_codex_review_disagreement_at_pass_2_holds_permanently():
    reset_fixture()
    _write_spec(AUTO_IMPL_SPEC)
    repo_path = _make_temp_git_site()
    try:
        proposal = _proposal("prop-codex-final-hold", status="draft", page="/services/")
        del proposal["implementation"]
        _seed_proposal(proposal)
        draft_copy(PROJECT, LOOP, proposal["id"], "Title A", "claude-test", repo_path=repo_path)
        start_review(PROJECT, LOOP, proposal["id"])
        packet1 = evidence_packet(PROJECT, LOOP, proposal["id"])
        # Both pass-1 rounds hold (e.g. simulated reviewer timeout/failure - the
        # orchestrating skill records that as verdict="hold" too, never approve).
        record_round(PROJECT, LOOP, proposal["id"], 1, {"verdict": "hold", "confidence": None, "objections": ["reviewer failure/timeout"], "required_corrections": [], "evidence_packet_hash": packet1["evidence_packet_hash"]})
        record_round(PROJECT, LOOP, proposal["id"], 2, {"verdict": "hold", "confidence": None, "objections": ["reviewer failure/timeout"], "required_corrections": [], "evidence_packet_hash": packet1["evidence_packet_hash"]})
        after_pass1 = adjudicate_proposal(PROJECT, LOOP, proposal["id"])
        check("both rounds holding in pass 1 still allows one revision", after_pass1["status"] == "review-revision-needed")

        draft_copy(PROJECT, LOOP, proposal["id"], "Title B", "claude-test", repo_path=repo_path)
        packet2 = evidence_packet(PROJECT, LOOP, proposal["id"])
        record_round(PROJECT, LOOP, proposal["id"], 3, {"verdict": "hold", "confidence": None, "objections": ["still unclear"], "required_corrections": [], "evidence_packet_hash": packet2["evidence_packet_hash"]})
        record_round(PROJECT, LOOP, proposal["id"], 4, {"verdict": "reject", "confidence": 0.7, "objections": ["not a good fit"], "required_corrections": [], "evidence_packet_hash": packet2["evidence_packet_hash"]})
        after_pass2 = adjudicate_proposal(PROJECT, LOOP, proposal["id"])
        check("disagreement at pass 2 is a final review-held, no further revision offered", after_pass2["status"] == "review-held")

        threw_no_pass3_artifact = False
        try:
            evidence_packet(PROJECT, LOOP, proposal["id"])
        except ValueError as e:
            threw_no_pass3_artifact = "bounded at 4 total" in str(e)
        check("no third pass can be started - bounded at 4 total Codex calls, no further revision is ever permitted", threw_no_pass3_artifact)

        rescued = decide(PROJECT, LOOP, proposal["id"], "reject", by="nate", note="agreeing with Codex's hold")
        check("a human can still rescue (here: reject) a review-held proposal via the existing /review-pending path", rescued["status"] == "rejected")
    finally:
        _force_rmtree(repo_path)


def test_codex_review_tier2_and_breach_never_auto_implement():
    reset_fixture()
    _write_spec(AUTO_IMPL_SPEC)
    repo_path = _make_temp_git_site()
    try:
        tier2 = _proposal("prop-tier2-forged", status="approved-for-implementation", page="/services/", tier=2)
        tier2["implementation"] = {"new_value": "New Title", "previous_value": "Old service title"}
        tier2["review"] = {"rounds": [], "final_adjudication": "approve"}
        tier2["auto_implementation"] = {"eligible": True, "authorized_by": "forged"}
        _seed_proposal(tier2)
        threw_tier2 = False
        try:
            apply_proposal(PROJECT, LOOP, tier2["id"], repo_path=repo_path)
        except ValueError as e:
            threw_tier2 = "Tier 2" in str(e)
        check("a Tier-2 proposal is always refused by apply.py, even forged into approved-for-implementation with auto_implementation.eligible=True", threw_tier2)

        state_path = os.path.join(loop_dir, "state.json")
        _write_json(state_path, {"status": "paused-breach", "paused_reason": "test breach"})
        proposal = _proposal("prop-codex-breach", status="draft", page="/services/")
        del proposal["implementation"]
        _seed_proposal(proposal)
        draft_copy(PROJECT, LOOP, proposal["id"], "New Services Title", "claude-test", repo_path=repo_path)
        start_review(PROJECT, LOOP, proposal["id"])
        packet = evidence_packet(PROJECT, LOOP, proposal["id"])
        record_round(PROJECT, LOOP, proposal["id"], 1, {"verdict": "approve", "confidence": 0.9, "objections": [], "required_corrections": [], "evidence_packet_hash": packet["evidence_packet_hash"]})
        record_round(PROJECT, LOOP, proposal["id"], 2, {"verdict": "approve", "confidence": 0.9, "objections": [], "required_corrections": [], "evidence_packet_hash": packet["evidence_packet_hash"]})
        adjudicated = adjudicate_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)
        check("adjudicate refuses eligibility while the loop is paused-breach", adjudicated["status"] == "review-approved" and adjudicated["auto_implementation"]["eligible"] is False)
        check("the breach reason is named in failed_reasons", any("paused-breach" in r for r in adjudicated["auto_implementation"]["failed_reasons"]))
    finally:
        _force_rmtree(repo_path)


def test_codex_review_disable_switches_stop_apply_after_adjudication():
    reset_fixture()
    _write_spec(AUTO_IMPL_SPEC)
    repo_path = _make_temp_git_site()
    try:
        proposal = _proposal("prop-codex-disable", status="draft", page="/services/")
        del proposal["implementation"]
        _seed_proposal(proposal)
        draft_copy(PROJECT, LOOP, proposal["id"], "New Services Title", "claude-test", repo_path=repo_path)
        start_review(PROJECT, LOOP, proposal["id"])
        packet = evidence_packet(PROJECT, LOOP, proposal["id"])
        record_round(PROJECT, LOOP, proposal["id"], 1, {"verdict": "approve", "confidence": 0.9, "objections": [], "required_corrections": [], "evidence_packet_hash": packet["evidence_packet_hash"]})
        record_round(PROJECT, LOOP, proposal["id"], 2, {"verdict": "approve", "confidence": 0.9, "objections": [], "required_corrections": [], "evidence_packet_hash": packet["evidence_packet_hash"]})
        adjudicated = adjudicate_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)
        check("setup: proposal reaches approved-for-implementation before the disable check", adjudicated["status"] == "approved-for-implementation")

        # Flip auto_implementation_enabled off AFTER adjudication - apply.py
        # must re-read spec.md fresh and refuse, not trust the adjudication-
        # time decision (Codex round 2/3 finding: "disable switch ineffective
        # after adjudication").
        _write_spec(GOOD_SPEC)  # GOOD_SPEC has no auto_implementation_enabled at all
        threw_disabled = False
        disabled_reason = ""
        try:
            apply_proposal(PROJECT, LOOP, proposal["id"], repo_path=repo_path)
        except ValueError as e:
            threw_disabled = True
            disabled_reason = str(e)
        check("flipping auto_implementation_enabled off after adjudication genuinely disables the auto path", threw_disabled)
        check("the refusal names auto_implementation_enabled", "auto_implementation_enabled" in disabled_reason)
    finally:
        _force_rmtree(repo_path)


def test_codex_review_cooldown_blocks_duplicate_proposal_on_same_page():
    reset_fixture()
    in_review = _proposal("prop-in-review-cooldown", status="review-pending", page="/blog/loop-agency", keyword="best loop agency")
    in_review["review"] = {"rounds": [], "final_adjudication": None}
    _seed_proposal(in_review)

    result = run_loop(
        PROJECT, LOOP, scenario="normal",
        _github_requester=_compare_requester("behind"),
        _github_compare_target={"owner": "nateginn", "repo": "artwebsite"},
    )
    created = [_read_json(os.path.join(pending_dir, f"{pid}.json")) for pid in result["run_json"]["proposals_created"]]
    check("cooldown blocks a new proposal for a page with a review-pending proposal already active", not any(p["target"]["page"] == "/blog/loop-agency" for p in created))


def test_event_log_reconcile_backfills_via_cli_lock():
    reset_fixture()
    proposal = _proposal("prop-reconcile-cli", status="implemented", page="/blog/loop-agency", keyword="best loop agency")
    proposal["implemented_commit_sha"] = "deadbeefcafe"
    _seed_proposal(proposal)
    backfilled = event_log_reconcile(loop_dir, PROJECT, LOOP, pending_dir)
    check("reconcile backfills a missing implementation_created event for an existing proposal with no event history", any(e["event_type"] == "implementation_created" and e["proposal_id"] == proposal["id"] for e in backfilled))


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
    test_crash_recovery_legacy_attempt_without_base_sha_backfill()
    test_apply_refuses_on_main_baseline_drift()
    test_make_attempt_and_create_worktree_never_use_a_bare_main_ref()
    test_apply_persists_implementation_base_sha_surviving_attempt_removal()
    test_apply_refuses_on_inconsistent_commit_ancestry()
    test_publish_end_to_end_real_git_push_pr_and_auto_merge()
    test_publish_refuses_on_multi_file_commit_using_real_git()
    test_publish_refuses_when_deploy_workflow_trigger_shape_has_drifted()
    test_publish_refuses_when_branch_protection_is_missing()
    test_publish_refuses_when_a_ruleset_bypass_actor_exists_on_real_main()
    test_publish_refuses_on_real_non_github_remote_url()
    test_retry_transition()
    test_live_detection_transitions_implemented_to_applied()
    test_promote_live_implementations_invokes_on_promoted_callback()
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
    test_codex_review_end_to_end_with_revision_and_auto_implement()
    test_codex_review_missing_evidence_holds()
    test_codex_review_disagreement_at_pass_2_holds_permanently()
    test_codex_review_tier2_and_breach_never_auto_implement()
    test_codex_review_disable_switches_stop_apply_after_adjudication()
    test_codex_review_cooldown_blocks_duplicate_proposal_on_same_page()
    test_event_log_reconcile_backfills_via_cli_lock()

    _force_rmtree(project_dir)

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

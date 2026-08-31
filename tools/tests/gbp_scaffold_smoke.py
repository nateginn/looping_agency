# Offline smoke test for the GBP Posts loop scaffold (Phase 2 of the plan in
# kb/handoffs/looping-gbp-posts.md, mirrored into projects/art/loops/gbp/spec.md's own Notes).
#
# Exercises tools/run_loop.py against the REAL projects/art/loops/gbp/spec.md end to end,
# entirely offline - fake resolve_credential/http_post/http_get/http fetch/github requester,
# never a real credential or network call. Scope, honestly stated: this proves the run
# contract completes cleanly for this spec and that zero proposals are generated - it does
# NOT exercise a selector, a payload-hash gate, or a publish state machine, none of which
# exist yet (Phases 3-6 of the plan). A Codex review of an earlier version of this file
# (2026-08-31) correctly flagged that overclaim, plus four real defects fixed below.
#
# The one property this test exists to guard, durably, across future refactors: this loop's
# spec deliberately configures no connector that populates `search_analytics` (see spec.md's
# "Deliberately NOT included" note). Since 2026-08-31, that is enforced structurally in
# run_loop.py itself (`spec.get("loop") == "seo"` gates both _evaluate_prior_experiments and
# _pick_new_actions) rather than being an incidental consequence of what's in `inputs` today -
# a Codex review found the original claim ("no such connector is configured, so it can never
# fire") false in general, because _build_snapshot carries a stale search_analytics section
# forward from any earlier run that did populate one. This test proves BOTH the current-run
# case and the stale-carry-forward case.
import json
import os
import shutil
import sys
import unittest.mock

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.dirname(THIS_DIR)
WORKSPACE_ROOT = os.path.dirname(TOOLS_DIR)
sys.path.insert(0, TOOLS_DIR)

import dataforseo  # noqa: E402
import gsc  # noqa: E402
import run_loop  # noqa: E402
from lib.proposals import list_proposals  # noqa: E402

GBP_LOOP_DIR = os.path.join(WORKSPACE_ROOT, "projects", "art", "loops", "gbp")
GBP_SPEC_PATH = os.path.join(GBP_LOOP_DIR, "spec.md")

# Files run_loop() can touch as a side effect of a real run. Snapshotted before the test and
# restored exactly afterward (bytes, or absence) - never a blind delete/overwrite, which an
# earlier version of this test did and which a Codex review correctly flagged as destructive:
# on a loop that has ever had a real run, that would erase real state/event/memory history.
_STATEFUL_FILES = ("state.json", "events.jsonl", "run.lock", "memory.md", "locations-detected.json")


def _snapshot_files():
    saved = {}
    for name in _STATEFUL_FILES:
        path = os.path.join(GBP_LOOP_DIR, name)
        if os.path.exists(path):
            with open(path, "rb") as f:
                saved[name] = f.read()
        else:
            saved[name] = None
    return saved


def _restore_files(saved):
    for name, content in saved.items():
        path = os.path.join(GBP_LOOP_DIR, name)
        if content is None:
            if os.path.exists(path):
                os.remove(path)
        else:
            with open(path, "wb") as f:
                f.write(content)


def _snapshot_dir(dir_path):
    """Every file's exact bytes, keyed by filename - not just "which names exist". A
    Codex review (2026-08-31) found the original name-set-only tracking for pending/ let
    run_loop.py's own in-place rewrites of a PRE-EXISTING proposal's counters (run_cycles_seen
    etc.) survive past the test, since the file's name never changed. `pending/` starts empty
    for this brand-new loop today, but this must not silently rot into a real state
    modification if a real proposal ever exists there when this test runs again."""
    if not os.path.isdir(dir_path):
        return None
    saved = {}
    for name in os.listdir(dir_path):
        with open(os.path.join(dir_path, name), "rb") as f:
            saved[name] = f.read()
    return saved


def _restore_dir(dir_path, saved):
    if saved is None:
        if os.path.isdir(dir_path) and not os.listdir(dir_path):
            os.rmdir(dir_path)
        return
    os.makedirs(dir_path, exist_ok=True)
    for name in os.listdir(dir_path):
        if name not in saved:
            os.remove(os.path.join(dir_path, name))
    for name, content in saved.items():
        with open(os.path.join(dir_path, name), "wb") as f:
            f.write(content)


def _fail_closed_requester(url, headers):
    raise AssertionError(f"offline smoke test: no real GitHub call is permitted, attempted {url}")


def _fail_closed_urlopen(*args, **kwargs):
    raise AssertionError("offline smoke test: no real network call is permitted (urllib.request.urlopen)")


def _fake_resolve_credential(alias, project_dir=None):
    return f"fake-credential-for-{alias}"


def _fake_local_rank_http_get(url, headers):
    payload = {
        "tasks": [{
            "result": [
                {"location_code": 100, "location_name": "Greeley,Colorado,United States"},
                {"location_code": 200, "location_name": "Denver,Colorado,United States"},
            ]
        }]
    }
    return 200, "OK", json.dumps(payload).encode("utf-8")


def _fake_local_rank_http_post(url, headers, body_bytes):
    body = json.loads(body_bytes)
    keyword = body[0]["keyword"]
    # A populated, unambiguous "absent" organic SERP - our domain simply isn't in it. Never a
    # competitor identity beyond a synthetic .invalid host (standing decision 4 applies to
    # fixtures too).
    items = [{"type": "organic", "rank_absolute": i, "url": f"https://competitor-{i}.invalid/"} for i in range(1, 4)]
    payload = {"tasks": [{"status_code": 20000, "data": {"keyword": keyword}, "result": [{"items": items}]}]}
    return 200, "OK", json.dumps(payload).encode("utf-8")


def _fake_gsc_indexation_http_get(url, headers):
    return 200, "OK", json.dumps({"sitemap": []}).encode("utf-8")


def _fake_gsc_indexation_http_post(url, headers, body_bytes):
    return 200, "OK", json.dumps({
        "inspectionResult": {
            "indexStatusResult": {"verdict": "PASS", "coverageState": "Submitted and indexed"},
        }
    }).encode("utf-8")


# Matched against the real connector modules' own endpoint constants, not a loose substring
# guess - a Codex review (2026-08-31) correctly noted substring matching ("dataforseo.com" and
# "serp") would still accept a malformed-but-similarly-named URL.
_GSC_SITEMAPS_PREFIX = gsc.SITEMAPS_ENDPOINT.split("{", 1)[0]


def _dispatching_http_get(url, headers):
    if url == dataforseo.SERP_LOCATIONS_ENDPOINT:
        return _fake_local_rank_http_get(url, headers)
    if url.startswith(_GSC_SITEMAPS_PREFIX):
        return _fake_gsc_indexation_http_get(url, headers)
    raise AssertionError(f"offline smoke test: unexpected GET to {url} - no fixture configured, refusing to guess")


def _dispatching_http_post(url, headers, body_bytes):
    if url == dataforseo.SERP_ENDPOINT:
        return _fake_local_rank_http_post(url, headers, body_bytes)
    if url == gsc.URL_INSPECTION_ENDPOINT:
        return _fake_gsc_indexation_http_post(url, headers, body_bytes)
    raise AssertionError(f"offline smoke test: unexpected POST to {url} - no fixture configured, refusing to guess")


def _run_loop_offline():
    # Patches urllib.request.urlopen for the duration of the call so
    # _fetch_footer_location_diff (which has no injectable HTTP client and fires whenever
    # run_mode is "full" and local_rank was just updated - true for every call this test
    # makes) can never reach the real network, regardless of which day this test happens to
    # run on. A Codex review (2026-08-31) found this call actually fired live against
    # acceleratedrehabtherapy.com in an earlier version of this test.
    with unittest.mock.patch("run_loop.urllib.request.urlopen", side_effect=_fail_closed_urlopen):
        return run_loop.run_loop(
            "art", "gbp", scenario="normal",
            _resolve_credential=_fake_resolve_credential,
            _http_post=_dispatching_http_post,
            _http_get=_dispatching_http_get,
            _github_requester=_fail_closed_requester,
        )


def run():
    checks = []
    assert os.path.exists(GBP_SPEC_PATH), f"expected {GBP_SPEC_PATH} to exist"

    runs_dir = os.path.join(GBP_LOOP_DIR, "runs")
    pending_dir = os.path.join(GBP_LOOP_DIR, "pending")
    pre_existing_runs = set(os.listdir(runs_dir)) if os.path.isdir(runs_dir) else set()
    saved_files = _snapshot_files()
    saved_pending = _snapshot_dir(pending_dir)
    pre_existing_pending = set(saved_pending) if saved_pending is not None else set()

    try:
        result = _run_loop_offline()
        checks.append(("run completes (not refused/invalid-spec)", result["status"] not in ("refused", "invalid-spec")))
        checks.append(("run status is ok (no critical connector configured -> nothing can abort it)", result["status"] == "ok"))

        run_json = result.get("run_json") or {}
        checks.append(("zero proposals created - no selector exists yet, and none should fire", run_json.get("proposals_created") == []))
        checks.append(("dataforseo-local-rank ran (tool_calls records it)", any(tc.get("tool") == "dataforseo-local-rank" for tc in run_json.get("tool_calls") or [])))
        checks.append(("gsc-indexation ran (tool_calls records it)", any(tc.get("tool") == "gsc-indexation" for tc in run_json.get("tool_calls") or [])))
        checks.append(("no connector degraded in this fully-fixture-backed run", run_json.get("degraded_connectors") == []))

        run_id = result["run_id"]
        snapshot_path = os.path.join(runs_dir, run_id, "snapshot.json")
        with open(snapshot_path, "r", encoding="utf-8") as f:
            snapshot = json.load(f)
        checks.append(("snapshot has no search_analytics section in the ordinary case", snapshot.get("search_analytics") is None))
        checks.append(("snapshot's local_rank section is populated from the fixture", isinstance(snapshot.get("local_rank"), dict) and bool(snapshot["local_rank"].get("rows"))))
        checks.append(("snapshot's maps_rank/local_pack sections are absent (not in this spec's inputs yet)", snapshot.get("maps_rank") is None and snapshot.get("local_pack") is None))

        pending_proposals = list_proposals(pending_dir)
        new_pending = [p for p in pending_proposals if p.get("_file") not in pre_existing_pending]
        checks.append(("no new proposal files were written by this run", new_pending == []))

        second_result = _run_loop_offline()
        checks.append(("a second consecutive run also completes cleanly (no lock/state corruption)", second_result["status"] == "ok"))
        checks.append(("the second run also creates zero proposals", (second_result.get("run_json") or {}).get("proposals_created") == []))

        # --- the actual regression this test exists for: a STALE, carried-forward
        # search_analytics section (as if some earlier run had, incorrectly, included a
        # search_analytics-populating connector) must still never produce a gbp-post-draft
        # proposal, because run_loop.py now gates on spec["loop"] == "seo" structurally,
        # not on "does this run's own fetch happen to populate the section". -------------
        third_result = _run_loop_offline()
        third_run_id = third_result["run_id"]
        third_snapshot_path = os.path.join(runs_dir, third_run_id, "snapshot.json")
        with open(third_snapshot_path, "r", encoding="utf-8") as f:
            third_snapshot = json.load(f)
        third_snapshot["search_analytics"] = {
            "source": "poisoned-fixture",
            "pulled_at": run_loop._now_iso(),
            "keywords": [
                {"page": "/physical-therapy/", "keyword": "physical therapy greeley", "position": 8, "clicks": 40, "impressions": 500},
            ],
        }
        # snapshot.json is written read-only by design (tools/snapshot.py) - this
        # simulation needs to overwrite it, so make it writable first. Harmless: the whole
        # run directory this belongs to is removed in the finally block below regardless.
        os.chmod(third_snapshot_path, 0o644)
        with open(third_snapshot_path, "w", encoding="utf-8") as f:
            json.dump(third_snapshot, f, indent=2)

        fourth_result = _run_loop_offline()
        fourth_run_json = fourth_result.get("run_json") or {}
        checks.append(("a stale, carried-forward search_analytics section still produces zero proposals for this loop", fourth_run_json.get("proposals_created") == []))
        fourth_pending = list_proposals(pending_dir)
        new_fourth_pending = [p for p in fourth_pending if p.get("_file") not in pre_existing_pending]
        checks.append(("...and no gbp-post-draft (or any) proposal file was written from the poisoned section", not any(p.get("action_type") == "gbp-post-draft" for p in new_fourth_pending)))
    finally:
        # Test artifacts only - never touch pre-existing content. Restores every stateful
        # file to its exact pre-test bytes (or absence), rather than blindly deleting/
        # overwriting, which would destroy real history on a loop that has ever run for real.
        for name in os.listdir(runs_dir) if os.path.isdir(runs_dir) else []:
            if name not in pre_existing_runs:
                shutil.rmtree(os.path.join(runs_dir, name), ignore_errors=True)
        # Restores every pre-existing proposal's exact bytes too, not just its filename's
        # continued existence - run_loop.py rewrites every pending proposal file in place
        # each run (bumping counters like run_cycles_seen), which a name-only restore would
        # have let leak past this test (Codex review, 2026-08-31 follow-up).
        _restore_dir(pending_dir, saved_pending)
        _restore_files(saved_files)

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    print(f"{len(checks) - failed}/{len(checks)} checks passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    run()

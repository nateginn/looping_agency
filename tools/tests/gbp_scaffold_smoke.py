# Offline smoke test for the GBP Posts loop scaffold (Phases 2-3 of the plan in
# kb/handoffs/looping-gbp-posts.md, mirrored into projects/art/loops/gbp/spec.md's own Notes).
#
# Exercises tools/run_loop.py against the REAL projects/art/loops/gbp/spec.md AND the real
# known-gaps.yaml end to end, entirely offline - fake resolve_credential/http_post/http_get/
# http fetch/github requester, never a real credential or network call. Scope, honestly
# stated: this proves the run contract completes cleanly for this spec, that the real
# known-gaps.yaml's one eligible content-gap entry produces exactly one proposal, and that a
# second run does not duplicate it - it does NOT exercise a payload-hash gate or a publish
# state machine, neither of which exists yet (Phases 4-6 of the plan). See
# tools/tests/gbp_selector_test.py for focused unit-level coverage of the selector itself
# (_pick_gbp_actions) against synthetic fixtures.
#
# Two properties this test exists to guard, durably, across future refactors:
# 1. This loop's spec deliberately configures no connector that populates `search_analytics`
#    (see spec.md's "Deliberately NOT included" note), and even a STALE, carried-forward
#    search_analytics section (from some hypothetical earlier run that did include such a
#    connector) must never make run_loop.py's SEO-shaped `_pick_new_actions` fire for this
#    loop - enforced structurally by `spec.get("loop") == "seo"` gating both that function and
#    `_evaluate_prior_experiments` (a Codex review of an earlier version of this test/spec's
#    claim, 2026-08-31, found the un-gated version false in general).
# 2. The real known-gaps.yaml's one content_gap entry (greeley-deep-tissue-massage-missing)
#    produces exactly one gbp-post-draft proposal, and a second consecutive run does not
#    create a duplicate (cooldown keyed on {location, topic}, not the SEO-shaped {page}).
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
        # The directory did not exist before this test ran at all, so everything in it now -
        # not just an empty leftover - is this test's own creation and must be removed in
        # full. An earlier version only removed it when already empty, which silently left a
        # crashed run's proposal file behind (caught when a real run crash during
        # development left exactly such a file in the real pending/ dir).
        if os.path.isdir(dir_path):
            shutil.rmtree(dir_path, ignore_errors=True)
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
        first_run_created = run_json.get("proposals_created") or []
        checks.append(("exactly one proposal is created from the real known-gaps.yaml's one eligible content-gap entry", len(first_run_created) == 1))
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
        checks.append(("exactly one new proposal file was written by this run", len(new_pending) == 1))
        checks.append(("the proposal is a gbp-post-draft for the Greeley deep-tissue-massage gap", bool(new_pending) and new_pending[0].get("action_type") == "gbp-post-draft" and new_pending[0].get("target", {}).get("topic") == "greeley-deep-tissue-massage-missing"))
        checks.append(("the proposal carries a content_gap_evidence object", bool(new_pending) and isinstance(new_pending[0].get("content_gap_evidence"), dict)))

        second_result = _run_loop_offline()
        checks.append(("a second consecutive run also completes cleanly (no lock/state corruption)", second_result["status"] == "ok"))
        checks.append(("the second run creates zero NEW proposals - cooldown blocks a duplicate for the same location/topic", (second_result.get("run_json") or {}).get("proposals_created") == []))

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
        fourth_pending = list_proposals(pending_dir)
        new_fourth_pending = [p for p in fourth_pending if p.get("_file") not in pre_existing_pending]
        # Asserted directly on shape, not just "proposals_created is empty" - the
        # known-gaps-driven proposal from run 1 is itself still cooling down by now (still
        # "draft"), which would ALSO make proposals_created empty on run 4 for an unrelated
        # reason. Checking for the absence of any {page, keyword}-shaped (SEO-shaped) target
        # unambiguously proves the seo-gate held, independent of that cooldown interaction.
        checks.append(("a stale, carried-forward search_analytics section produces no SEO-shaped ({page, keyword}) proposal for this loop", not any("page" in (p.get("target") or {}) for p in new_fourth_pending)))
        checks.append(("...and no proposal at all was written from the poisoned section itself", (fourth_result.get("run_json") or {}).get("proposals_created") == []))
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

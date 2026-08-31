# Unit tests for run_loop.py's GBP Posts selector (_pick_gbp_actions - plan Phase 3).
#
# Pure-function-level tests against a temporary known-gaps.yaml, entirely offline - no
# run_loop() invocation, no credentials, no network. Complements
# tools/tests/gbp_scaffold_smoke.py, which exercises the full run contract end to end
# against the real projects/art/loops/gbp/spec.md.
#
# The property every test here ultimately serves: a proposal can only ever be justified by a
# genuine, source-verifiable content-gap fact (known-gaps.yaml, routing: content_gap_evidence)
# - never by a rank/pack/technical-health signal alone, and never by a structural gap that
# belongs in templates/loops/seo/gbp-checklist.md instead (plan Phase 1/3's explicit rule).
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.dirname(THIS_DIR)
sys.path.insert(0, TOOLS_DIR)

import run_loop  # noqa: E402

SPEC = {
    "loop": "gbp",
    "allowed_actions": [
        {"type": "gbp-post-draft", "tier": 1, "manual_approval_only": True, "observation_window_days": 30, "min_sample_size": 1},
    ],
}


def _write_known_gaps(tmp_dir, gaps_yaml_text):
    with open(os.path.join(tmp_dir, "known-gaps.yaml"), "w", encoding="utf-8", newline="\n") as f:
        f.write(gaps_yaml_text)


def _iso_date(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).date().isoformat()


def run():
    checks = []
    tmp = tempfile.mkdtemp(prefix="gbp-selector-test-")
    now = datetime.now(timezone.utc)
    try:
        # --- a well-formed content_gap entry produces exactly one proposal, fully shaped ---
        # Summary deliberately mentions a GSC position/rank claim, mirroring the real
        # known-gaps.yaml entry - this is exactly the free text the rationale must NEVER
        # echo (Codex review, 2026-08-31: an earlier version of the selector copied a gap's
        # summary straight into the rationale, and the real entry's summary literally
        # contains "GSC position 14.3").
        _write_known_gaps(tmp, f"""
version: 1
gaps:
  - id: greeley-deep-tissue-massage-missing
    type: content_gap
    location: Greeley
    summary: "Deep Tissue Massage is not listed; ranks at GSC position 14.3 and posting will improve rank."
    routing: content_gap_evidence
    freshness_threshold_days: 60
    confirmed_date: "{_iso_date(10)}"
""")
        proposals, decisions = run_loop._pick_gbp_actions(tmp, SPEC, set(), "run-1", now)
        checks.append(("exactly one proposal is drafted from one eligible entry", len(proposals) == 1))
        p = proposals[0] if proposals else {}
        checks.append(("action_type is gbp-post-draft", p.get("action_type") == "gbp-post-draft"))
        checks.append(("target uses {location, topic}, never {page, keyword}", p.get("target") == {"location": "Greeley", "topic": "greeley-deep-tissue-massage-missing"}))
        checks.append(("opportunity_type is the closed enum value content_freshness", p.get("opportunity_type") == "content_freshness"))
        checks.append(("signal_reference names the known-gaps entry", (p.get("signal_reference") or {}).get("known_gaps_entry_id") == "greeley-deep-tissue-massage-missing"))
        checks.append(("intended_user_action defaults to learn_more when unset", p.get("intended_user_action") == "learn_more"))
        evidence = p.get("content_gap_evidence") or {}
        checks.append(("content_gap_evidence carries evidence_id", evidence.get("evidence_id") == "greeley-deep-tissue-massage-missing"))
        checks.append(("content_gap_evidence carries a source path", bool(evidence.get("source"))))
        checks.append(("content_gap_evidence carries a source_entry_hash", bool(evidence.get("source_entry_hash"))))
        checks.append(("content_gap_evidence records the gap's OWN declared freshness threshold, distinctly from the selector's confirmation-age check", evidence.get("gap_freshness_threshold_days") == 60))
        checks.append(("content_gap_evidence records the confirmation-age check the selector actually applied", evidence.get("confirmation_max_age_days") == run_loop.GBP_KNOWN_GAP_MAX_AGE_DAYS and isinstance(evidence.get("confirmation_age_days"), float)))
        checks.append(("content_gap_evidence carries an observed_at timestamp", bool(evidence.get("observed_at"))))
        checks.append(("rationale identifies the entry/location but never echoes the gap's free-text summary", "greeley-deep-tissue-massage-missing" in p["rationale"] and "Deep Tissue Massage" not in p["rationale"]))
        checks.append(("rationale never contains a rank/position claim, even though the source summary does", "position" not in p["rationale"].lower() and "rank" not in p["rationale"].lower()))
        checks.append(("manual_approval_only is always true for gbp-post-draft", p.get("manual_approval_only") is True))
        checks.append(("observation_window_days/min_sample_size come from the spec's allowed_actions entry", p.get("observation_window_days") == 30 and p.get("min_sample_size") == 1))

        # --- a structural gap (checklist_only) never produces a proposal ---
        _write_known_gaps(tmp, f"""
version: 1
gaps:
  - id: denver-primary-category-unconfirmed
    type: structural
    location: Denver
    summary: "Denver's primary category has not been independently re-confirmed."
    routing: checklist_only
    confirmed_date: "{_iso_date(5)}"
""")
        structural_proposals, structural_decisions = run_loop._pick_gbp_actions(tmp, SPEC, set(), "run-2", now)
        checks.append(("a structural gap (routing: checklist_only) never produces a proposal", structural_proposals == []))

        # --- a stale confirmed_date is skipped ---
        _write_known_gaps(tmp, f"""
version: 1
gaps:
  - id: stale-gap
    type: content_gap
    location: Greeley
    summary: "Some content gap."
    routing: content_gap_evidence
    confirmed_date: "{_iso_date(200)}"
""")
        stale_proposals, stale_decisions = run_loop._pick_gbp_actions(tmp, SPEC, set(), "run-3", now)
        checks.append(("a content_gap entry confirmed >180 days ago is skipped as stale", stale_proposals == []))
        checks.append(("the stale skip is explained in decisions", any("stale" in d for d in stale_decisions)))

        # --- a syntactically-valid-YAML-but-not-a-date confirmed_date value degrades cleanly ---
        _write_known_gaps(tmp, """
version: 1
gaps:
  - id: garbage-date-gap
    type: content_gap
    location: Greeley
    summary: "Some content gap with a nonsense confirmed_date."
    routing: content_gap_evidence
    confirmed_date: "not-a-date"
""")
        garbage_date_proposals, garbage_date_decisions = run_loop._pick_gbp_actions(tmp, SPEC, set(), "run-3b", now)
        checks.append(("a confirmed_date that isn't a valid date at all is skipped, not a crash", garbage_date_proposals == []))
        checks.append(("...and explains why", any("not a valid date" in d for d in garbage_date_decisions)))

        # --- a missing confirmed_date is treated as unconfirmed, not a default-eligible gap ---
        _write_known_gaps(tmp, """
version: 1
gaps:
  - id: unconfirmed-gap
    type: content_gap
    location: Greeley
    summary: "Some content gap with no confirmation date."
    routing: content_gap_evidence
""")
        unconfirmed_proposals, unconfirmed_decisions = run_loop._pick_gbp_actions(tmp, SPEC, set(), "run-4", now)
        checks.append(("a content_gap entry with no confirmed_date is never treated as eligible by default", unconfirmed_proposals == []))

        # --- cooldown: a non-terminal proposal already active for {location, topic} blocks a new one ---
        _write_known_gaps(tmp, f"""
version: 1
gaps:
  - id: greeley-deep-tissue-massage-missing
    type: content_gap
    location: Greeley
    summary: "Deep Tissue Massage is not listed on the Greeley profile."
    routing: content_gap_evidence
    confirmed_date: "{_iso_date(10)}"
""")
        cooldown_key = run_loop._cooldown_key_from_target({"location": "Greeley", "topic": "greeley-deep-tissue-massage-missing"})
        cooled_proposals, cooled_decisions = run_loop._pick_gbp_actions(tmp, SPEC, {cooldown_key}, "run-5", now)
        checks.append(("an active non-terminal proposal for the same location/topic blocks a duplicate", cooled_proposals == []))
        checks.append(("the cooldown skip is explained in decisions", any("cooldown" in d for d in cooled_decisions)))

        # --- a DIFFERENT topic at the same location is NOT blocked by another topic's cooldown key
        # (this is the exact bug class a page-shaped cooldown key would have caused - every GBP
        # proposal collapsing onto the same {"page": None} key regardless of topic). -------------
        _write_known_gaps(tmp, f"""
version: 1
gaps:
  - id: greeley-deep-tissue-massage-missing
    type: content_gap
    location: Greeley
    summary: "Deep Tissue Massage is not listed on the Greeley profile."
    routing: content_gap_evidence
    confirmed_date: "{_iso_date(10)}"
  - id: greeley-some-other-topic
    type: content_gap
    location: Greeley
    summary: "A different content gap at the same location."
    routing: content_gap_evidence
    confirmed_date: "{_iso_date(10)}"
""")
        other_topic_cooldown = run_loop._cooldown_key_from_target({"location": "Greeley", "topic": "greeley-deep-tissue-massage-missing"})
        mixed_proposals, _mixed_decisions = run_loop._pick_gbp_actions(tmp, SPEC, {other_topic_cooldown}, "run-6", now)
        checks.append(("a cooldown on one topic does not block a different topic at the same location", any(p["target"]["topic"] == "greeley-some-other-topic" for p in mixed_proposals)))
        checks.append(("...and the cooled-down topic itself is still excluded", not any(p["target"]["topic"] == "greeley-deep-tissue-massage-missing" for p in mixed_proposals)))

        # --- missing/malformed known-gaps.yaml degrades cleanly, never crashes ---
        empty_dir = tempfile.mkdtemp(prefix="gbp-selector-test-empty-")
        try:
            missing_proposals, missing_decisions = run_loop._pick_gbp_actions(empty_dir, SPEC, set(), "run-7", now)
            checks.append(("a missing known-gaps.yaml produces zero proposals, not a crash", missing_proposals == []))
            checks.append(("...and explains why", any("not found" in d for d in missing_decisions)))
        finally:
            shutil.rmtree(empty_dir, ignore_errors=True)

        _write_known_gaps(tmp, "not: valid: yaml: [[[")
        malformed_proposals, malformed_decisions = run_loop._pick_gbp_actions(tmp, SPEC, set(), "run-8", now)
        checks.append(("malformed YAML produces zero proposals, not a crash", malformed_proposals == []))
        checks.append(("...and explains why", any("failed to parse" in d for d in malformed_decisions)))

        # --- a syntactically valid YAML document that isn't a mapping at all (Codex review,
        # 2026-08-31: parsed.get("gaps") would raise AttributeError on a bare list) ---------
        _write_known_gaps(tmp, "- just\n- a\n- list\n")
        list_root_proposals, list_root_decisions = run_loop._pick_gbp_actions(tmp, SPEC, set(), "run-8b", now)
        checks.append(("a YAML root that is a list, not a mapping, degrades cleanly", list_root_proposals == []))
        checks.append(("...and explains why", any("mapping" in d for d in list_root_decisions)))

        _write_known_gaps(tmp, "just a scalar string\n")
        scalar_root_proposals, scalar_root_decisions = run_loop._pick_gbp_actions(tmp, SPEC, set(), "run-8c", now)
        checks.append(("a YAML root that is a bare scalar degrades cleanly", scalar_root_proposals == []))

        # --- a future confirmed_date is invalid, never treated as "very fresh" ---
        _write_known_gaps(tmp, f"""
version: 1
gaps:
  - id: future-dated-gap
    type: content_gap
    location: Greeley
    summary: "A gap with a nonsensical future confirmation date."
    routing: content_gap_evidence
    confirmed_date: "{_iso_date(-30)}"
""")
        future_proposals, future_decisions = run_loop._pick_gbp_actions(tmp, SPEC, set(), "run-8d", now)
        checks.append(("a future confirmed_date is rejected, never treated as extra-fresh", future_proposals == []))
        checks.append(("...and explains why", any("future" in d for d in future_decisions)))

        # --- a timezone-aware confirmed_date is converted to UTC, never blindly relabeled ---
        # (Codex review, 2026-08-31: .replace(tzinfo=utc) on an already-aware datetime would
        # silently discard a real, non-UTC offset and misdate the confirmation).
        aware_but_old = (now - timedelta(days=200)).astimezone(timezone(timedelta(hours=-5))).isoformat()
        _write_known_gaps(tmp, f"""
version: 1
gaps:
  - id: aware-old-gap
    type: content_gap
    location: Greeley
    summary: "A gap confirmed 200 real days ago, expressed in a non-UTC offset."
    routing: content_gap_evidence
    confirmed_date: "{aware_but_old}"
""")
        aware_old_proposals, aware_old_decisions = run_loop._pick_gbp_actions(tmp, SPEC, set(), "run-8e", now)
        checks.append(("a timezone-aware confirmed_date is converted to UTC (not relabeled) before the age check, so 200 real days ago is correctly stale", aware_old_proposals == [] and any("stale" in d for d in aware_old_decisions)))

        aware_but_fresh = (now - timedelta(days=5)).astimezone(timezone(timedelta(hours=9))).isoformat()
        _write_known_gaps(tmp, f"""
version: 1
gaps:
  - id: aware-fresh-gap
    type: content_gap
    location: Greeley
    summary: "A gap confirmed 5 real days ago, expressed in a non-UTC offset."
    routing: content_gap_evidence
    confirmed_date: "{aware_but_fresh}"
""")
        aware_fresh_proposals, _aware_fresh_decisions = run_loop._pick_gbp_actions(tmp, SPEC, set(), "run-8f", now)
        checks.append(("a timezone-aware confirmed_date that is genuinely recent is correctly accepted, not miscounted by a dropped offset", len(aware_fresh_proposals) == 1))

        # --- two entries sharing the same id in one file never produce two proposals in one run ---
        _write_known_gaps(tmp, f"""
version: 1
gaps:
  - id: duplicate-id-gap
    type: content_gap
    location: Greeley
    summary: "First occurrence."
    routing: content_gap_evidence
    confirmed_date: "{_iso_date(1)}"
  - id: duplicate-id-gap
    type: content_gap
    location: Denver
    summary: "Second occurrence, different location, same id."
    routing: content_gap_evidence
    confirmed_date: "{_iso_date(1)}"
""")
        dup_proposals, dup_decisions = run_loop._pick_gbp_actions(tmp, SPEC, set(), "run-8g", now)
        checks.append(("two entries sharing an id produce only one proposal in a single run", len(dup_proposals) == 1))
        checks.append(("...and the duplicate is explained in decisions", any("duplicated" in d for d in dup_decisions)))

        # --- a GBP cooldown key never collides with an SEO {page}-shaped cooldown key, even
        # when they could coincidentally serialize to look similar --------------------------
        gbp_key = run_loop._cooldown_key_from_target({"location": "Greeley", "topic": "some-topic"})
        seo_key = run_loop._cooldown_key_from_target({"page": "/some-topic/", "keyword": "some keyword"})
        checks.append(("a GBP {location, topic} cooldown key never collides with an SEO {page} cooldown key", gbp_key != seo_key))
        checks.append(("a GBP cooldown key computed from this selector matches _cooldown_key_from_target's own encoding exactly", run_loop._cooldown_key_from_target({"location": "Greeley", "topic": "greeley-deep-tissue-massage-missing"}) == json.dumps({"location": "Greeley", "topic": "greeley-deep-tissue-massage-missing"}, sort_keys=True)))

        # --- content_gap_evidence.source_entry_hash actually changes when the source entry changes ---
        _write_known_gaps(tmp, f"""
version: 1
gaps:
  - id: hash-test-gap
    type: content_gap
    location: Greeley
    summary: "Original summary."
    routing: content_gap_evidence
    confirmed_date: "{_iso_date(1)}"
""")
        hash_before, _ = run_loop._pick_gbp_actions(tmp, SPEC, set(), "run-9", now)
        _write_known_gaps(tmp, f"""
version: 1
gaps:
  - id: hash-test-gap
    type: content_gap
    location: Greeley
    summary: "A materially different summary."
    routing: content_gap_evidence
    confirmed_date: "{_iso_date(1)}"
""")
        hash_after, _ = run_loop._pick_gbp_actions(tmp, SPEC, set(), "run-10", now)
        checks.append((
            "source_entry_hash changes when the underlying gap entry's content changes",
            hash_before[0]["content_gap_evidence"]["source_entry_hash"] != hash_after[0]["content_gap_evidence"]["source_entry_hash"],
        ))

        # --- no allowed_actions entry for gbp-post-draft => zero proposals, never a crash on missing tier/fields ---
        no_action_spec = {"loop": "gbp", "allowed_actions": [{"type": "some-other-action", "tier": 1}]}
        no_action_proposals, no_action_decisions = run_loop._pick_gbp_actions(tmp, no_action_spec, set(), "run-11", now)
        checks.append(("no gbp-post-draft entry in allowed_actions produces zero proposals, not a crash", no_action_proposals == []))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    print(f"{len(checks) - failed}/{len(checks)} checks passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    run()

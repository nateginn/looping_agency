# Codex-review protocol for low-risk SEO proposals (Phase 7 -
# PLAN-PHASE7-CODEX-REVIEW.md). This module is deterministic, LLM-free
# bookkeeping only - it never calls Codex itself (that's the
# .claude/skills/codex-seo-review skill's job, since only a Claude-Code Bash
# context can shell out to `codex exec`). I/O gathering is split from pure
# policy evaluation throughout (Codex round 1 finding 8).
import hashlib
import json

try:
    from .action_types import IMPLEMENTABLE_ACTIONS
    from .artwebsite_seo import read_current_value
except ImportError:
    from action_types import IMPLEMENTABLE_ACTIONS
    from artwebsite_seo import read_current_value

# Fields governing spec_content_hash - every field that can change what a
# proposal is authorized to do. approval_mode is included deliberately
# (Codex round 6 finding 3): a propose-only -> tier1-enabled flip is itself
# an authorization-relevant change and must invalidate a prior approval.
SPEC_HASH_FIELDS = (
    "approval_mode",
    "auto_implementation_enabled",
    "allowed_actions",
    "keyword_exclusions",
    "priority_pages",
    "noindex_destination_pages",
    "objective",
)

LINK_FIELDS = ("source_page", "destination_page", "anchor_text")


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def _sha256(value):
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def hash_proposal_for_review(proposal):
    """Content hash of exactly what a review round is judging - action_type,
    target, and drafted implementation. Deliberately excludes bookkeeping
    fields (status, review, events, timestamps) so re-hashing a proposal after
    a purely-administrative change (e.g. run_cycles_seen bumping) does not
    falsely look like a content revision."""
    return _sha256({
        "action_type": proposal.get("action_type"),
        "target": proposal.get("target"),
        "implementation": proposal.get("implementation"),
    })


def compute_spec_content_hash(spec):
    return _sha256({key: spec.get(key) for key in SPEC_HASH_FIELDS})


def _find_allowed_action(spec, action_type):
    for action in spec.get("allowed_actions") or []:
        if action.get("type") == action_type:
            return action
    return None


def gather_eligibility_context(project, loop, proposal, repo_path, spec, state=None):
    """The one function in this module that touches disk (beyond the caller
    already having loaded `proposal`/`spec`). Pure I/O, no policy decisions -
    evaluate_auto_implementation_eligibility() decides what the facts mean."""
    state = state if state is not None else {}
    action_type = proposal.get("action_type")
    current_value = None
    current_value_error = None
    if action_type in IMPLEMENTABLE_ACTIONS:
        try:
            current_value = read_current_value(repo_path, proposal.get("target", {}).get("page"), action_type)
        except ValueError as err:
            current_value_error = str(err)
    return {
        "breach_status": state.get("status"),
        "current_value": current_value,
        "current_value_error": current_value_error,
        "spec_content_hash": compute_spec_content_hash(spec),
    }


def missing_evidence_fields(proposal):
    """Fields required for a proposal to even be reviewable, per the task's
    explicit rule: a proposal lacking the exact proposed value, or (for an
    internal link) lacking source/destination/anchor text, is not reviewable
    at all - it must go straight to review-held, never submitted to Codex."""
    action_type = proposal.get("action_type")
    implementation = proposal.get("implementation") or {}
    missing = []
    if action_type in ("title-tag-rewrite", "meta-description-rewrite"):
        if not implementation.get("new_value"):
            missing.append("implementation.new_value")
        if implementation.get("previous_value") is None:
            missing.append("implementation.previous_value")
    elif action_type == "internal-link-addition":
        for field in LINK_FIELDS:
            if not implementation.get(field):
                missing.append(f"implementation.{field}")
    else:
        missing.append(f"unsupported action_type for review: {action_type}")
    return missing


def _prior_changes_for_page(loop_dir, proposal, read_events_fn, limit=10):
    """Prior events touching the same target page from OTHER proposals only -
    never this proposal's own events, at any pass. This is a stronger, simpler
    guarantee than merely excluding the current in-flight pass (Codex round 4
    finding 5 / round 6 wording): a packet can never leak any of its own
    proposal's review history back into itself, by construction."""
    page = (proposal.get("target") or {}).get("page")
    proposal_id = proposal.get("id")
    if not page:
        return []
    events = read_events_fn(loop_dir)
    out = []
    for ev in events:
        if ev.get("proposal_id") == proposal_id:
            continue
        if ev.get("target_page") != page:
            continue
        if ev.get("event_type") not in ("implementation_created", "proposal_applied", "proposal_verified", "guardrail_breached"):
            continue
        out.append({
            "event_type": ev.get("event_type"),
            "at": ev.get("at"),
            "action_type": ev.get("action_type"),
            "previous_value": ev.get("previous_value"),
            "new_value": ev.get("new_value"),
        })
    return out[-limit:]


def _metrics_row_for_target(metrics_snapshot, proposal):
    target = proposal.get("target") or {}
    page = target.get("page")
    keyword = target.get("keyword")
    section = (metrics_snapshot or {}).get("search_analytics") or {}
    for row in section.get("keywords") or []:
        if row.get("page") == page and row.get("keyword") == keyword:
            return row
    return None


def _local_rank_for_target(metrics_snapshot, proposal):
    target = proposal.get("target") or {}
    page = target.get("page")
    keyword = target.get("keyword")
    section = (metrics_snapshot or {}).get("local_rank") or {}
    results = section.get("results") if isinstance(section, dict) else None
    if not results:
        return None
    for row in results:
        if row.get("page") == page and row.get("keyword") == keyword:
            return row
    return None


def _indexation_status_for_page(metrics_snapshot, page):
    section = (metrics_snapshot or {}).get("technical_health") or {}
    for row in section.get("indexation") or []:
        if row.get("page") == page:
            return row.get("status") or row.get("coverage_state")
    return None


def build_evidence_packet(project, loop, proposal, spec, metrics_snapshot, loop_dir, read_events_fn):
    """Returns (packet, missing_fields). missing_fields non-empty -> the
    proposal is not reviewable and must route straight to review-held without
    ever being submitted to Codex."""
    missing = missing_evidence_fields(proposal)
    action_type = proposal.get("action_type")
    target = proposal.get("target") or {}
    implementation = proposal.get("implementation") or {}
    metrics_row = _metrics_row_for_target(metrics_snapshot, proposal)

    packet = {
        "proposal_id": proposal.get("id"),
        "action_type": action_type,
        "target_page": target.get("page"),
        "target_keyword": target.get("keyword"),
        "gsc_date_window": (metrics_snapshot or {}).get("search_analytics", {}).get("pulled_at") if metrics_snapshot else None,
        "clicks": metrics_row.get("clicks") if metrics_row else None,
        "impressions": metrics_row.get("impressions") if metrics_row else None,
        "avg_position": metrics_row.get("position") if metrics_row else None,
        "ctr": (metrics_row["clicks"] / metrics_row["impressions"]) if metrics_row and metrics_row.get("impressions") else None,
        "local_rank_data": _local_rank_for_target(metrics_snapshot, proposal),
        "current_value": implementation.get("previous_value"),
        "proposed_value": implementation.get("new_value"),
        "source_page": implementation.get("source_page"),
        "destination_page": implementation.get("destination_page"),
        "anchor_text": implementation.get("anchor_text"),
        "destination_indexation_status": _indexation_status_for_page(metrics_snapshot, implementation.get("destination_page")) if action_type == "internal-link-addition" else None,
        "source_snippet": implementation.get("source_snippet"),
        "project_objective": spec.get("objective"),
        "keyword_exclusions": spec.get("keyword_exclusions"),
        "prior_changes_same_page": _prior_changes_for_page(loop_dir, proposal, read_events_fn),
        "observation_window_days": proposal.get("observation_window_days"),
        "min_sample_size": proposal.get("min_sample_size"),
        "guardrail": {
            "metric": (spec.get("failure_threshold") or {}).get("metric"),
            "comparator": (spec.get("failure_threshold") or {}).get("comparator"),
            "value": (spec.get("failure_threshold") or {}).get("value"),
        },
        "rollback_plan": proposal.get("rollback"),
        "baseline_position": proposal.get("baseline_position"),
        "rationale": proposal.get("rationale"),
        "spec_content_hash": compute_spec_content_hash(spec),
    }
    return packet, missing


def evidence_packet_hash(packet):
    return _sha256(packet)


def adjudicate(rounds):
    """rounds: the list produced by event_log.rebuild_review_rounds_from_events
    (each already carries a computed "superseded" flag, derived from
    pass_number - see event_log._rebuild_rounds). Selects the two rounds of
    the current (highest) pass_number, not by content hash (Codex round 5
    finding 9 - hash-based selection collides on a revision that returns to
    its exact original content). Agreement is required; anything else holds."""
    current_pass = [r for r in rounds if not r.get("superseded")]
    if len(current_pass) != 2:
        return "hold"
    verdicts = {r.get("verdict") for r in current_pass}
    if verdicts == {"approve"}:
        return "approve"
    if verdicts == {"reject"}:
        return "reject"
    return "hold"


def evaluate_auto_implementation_eligibility(proposal, spec, context, fresh_adjudication_result, adjudicated_spec_content_hash):
    """Pure function over already-gathered facts (context) and the freshly
    (re-)computed adjudication result - never reads proposal["review"]
    ["final_adjudication"] itself (Codex round 3 finding 7), and authorizes
    tier/manual_approval_only from the CURRENT spec's matching allowed_actions
    entry, never proposal["tier"]/proposal["manual_approval_only"] (Codex
    round 5 finding 5 - a real forgery vector otherwise)."""
    results = {}
    reasons = []

    action_type = proposal.get("action_type")
    action = _find_allowed_action(spec, action_type)
    results["action_in_spec"] = action is not None
    if action is None:
        reasons.append(f'action_type "{action_type}" is not in this loop\'s current allowed_actions')

    tier_ok = bool(action) and action.get("tier") == 1
    results["tier_is_1"] = tier_ok
    if not tier_ok:
        reasons.append("current spec's tier for this action_type is not 1")

    manual_ok = bool(action) and action.get("manual_approval_only") is not True
    results["manual_approval_only_is_false"] = manual_ok
    if not manual_ok:
        reasons.append("current spec's manual_approval_only for this action_type is true")

    results["adjudication_is_approve"] = fresh_adjudication_result == "approve"
    if fresh_adjudication_result != "approve":
        reasons.append(f'fresh adjudication result is "{fresh_adjudication_result}", not "approve"')

    implementable = action_type in IMPLEMENTABLE_ACTIONS
    results["has_auto_implementer"] = implementable
    if not implementable:
        reasons.append(f'action_type "{action_type}" has no mechanical auto-implementer (apply.py IMPLEMENTABLE_ACTIONS)')

    no_drift = True
    if implementable:
        implementation = proposal.get("implementation") or {}
        no_drift = context.get("current_value_error") is None and context.get("current_value") == implementation.get("previous_value")
    results["no_value_drift"] = no_drift
    if not no_drift:
        reasons.append("the live current value has drifted since this proposal was drafted/reviewed")

    not_breached = context.get("breach_status") != "paused-breach"
    results["not_paused_breach"] = not_breached
    if not not_breached:
        reasons.append("this loop is currently paused-breach")

    enabled = spec.get("auto_implementation_enabled") is True
    results["auto_implementation_enabled"] = enabled
    if not enabled:
        reasons.append("spec.auto_implementation_enabled is not true")

    spec_unchanged = adjudicated_spec_content_hash is not None and adjudicated_spec_content_hash == context.get("spec_content_hash")
    results["spec_policy_unchanged_since_review"] = spec_unchanged
    if not spec_unchanged:
        reasons.append("the governing spec policy has changed since this proposal was reviewed - re-review required")

    eligible = all(results.values())
    return eligible, results, reasons


def latest_spec_content_hash_from_rounds(rounds):
    """The spec_content_hash recorded on the current (non-superseded) pass's
    rounds - what evaluate_auto_implementation_eligibility compares the live
    spec against. None if the current pass isn't complete yet."""
    current_pass = [r for r in rounds if not r.get("superseded")]
    hashes = {r.get("spec_content_hash") for r in current_pass if r.get("spec_content_hash")}
    if len(hashes) == 1:
        return next(iter(hashes))
    return None


def _self_test():
    import sys

    checks = []

    spec_a = {"approval_mode": "tier1-enabled", "auto_implementation_enabled": True, "allowed_actions": [{"type": "title-tag-rewrite", "tier": 1, "manual_approval_only": False}]}
    spec_b = dict(spec_a, approval_mode="propose-only")
    checks.append(("spec_content_hash changes when approval_mode changes", compute_spec_content_hash(spec_a) != compute_spec_content_hash(spec_b)))
    checks.append(("spec_content_hash is stable for an unchanged spec", compute_spec_content_hash(spec_a) == compute_spec_content_hash(dict(spec_a))))

    p1 = {"action_type": "title-tag-rewrite", "target": {"page": "/x/"}, "implementation": {"new_value": "A"}}
    p2 = dict(p1, run_cycles_seen=5)  # bookkeeping-only change
    checks.append(("hash_proposal_for_review ignores bookkeeping fields", hash_proposal_for_review(p1) == hash_proposal_for_review(p2)))
    p3 = dict(p1, implementation={"new_value": "B"})
    checks.append(("hash_proposal_for_review changes when implementation changes", hash_proposal_for_review(p1) != hash_proposal_for_review(p3)))

    checks.append(("missing_evidence_fields flags a title proposal with no new_value", "implementation.new_value" in missing_evidence_fields({"action_type": "title-tag-rewrite", "implementation": {}})))
    checks.append(("missing_evidence_fields is empty for a complete title proposal", missing_evidence_fields({"action_type": "title-tag-rewrite", "implementation": {"new_value": "A", "previous_value": "B"}}) == []))
    checks.append(("missing_evidence_fields flags an internal-link proposal missing anchor_text", "implementation.anchor_text" in missing_evidence_fields({"action_type": "internal-link-addition", "implementation": {"source_page": "/a/", "destination_page": "/b/"}})))
    checks.append(("missing_evidence_fields is empty for a complete internal-link proposal", missing_evidence_fields({"action_type": "internal-link-addition", "implementation": {"source_page": "/a/", "destination_page": "/b/", "anchor_text": "learn more"}}) == []))

    approve_round = lambda pn, verdict="approve", superseded=False: {"pass_number": pn, "verdict": verdict, "superseded": superseded, "spec_content_hash": "sh1"}
    checks.append(("adjudicate approves when both current-pass rounds approve", adjudicate([approve_round(1), approve_round(1)]) == "approve"))
    checks.append(("adjudicate rejects when both current-pass rounds reject", adjudicate([approve_round(1, "reject"), approve_round(1, "reject")]) == "reject"))
    checks.append(("adjudicate holds on disagreement", adjudicate([approve_round(1, "approve"), approve_round(1, "reject")]) == "hold"))
    checks.append(("adjudicate holds with only one current-pass round", adjudicate([approve_round(1)]) == "hold"))
    checks.append(("adjudicate ignores superseded rounds from an earlier pass", adjudicate([
        {"pass_number": 1, "verdict": "hold", "superseded": True, "spec_content_hash": "sh1"},
        {"pass_number": 1, "verdict": "approve", "superseded": True, "spec_content_hash": "sh1"},
        {"pass_number": 2, "verdict": "approve", "superseded": False, "spec_content_hash": "sh1"},
        {"pass_number": 2, "verdict": "approve", "superseded": False, "spec_content_hash": "sh1"},
    ]) == "approve"))
    checks.append(("adjudicate holds on a reviewer-failure verdict mixed with an approve", adjudicate([approve_round(1, "approve"), approve_round(1, "hold")]) == "hold"))

    rounds = [{"pass_number": 2, "superseded": False, "spec_content_hash": "sh1"}, {"pass_number": 2, "superseded": False, "spec_content_hash": "sh1"}]
    checks.append(("latest_spec_content_hash_from_rounds reads the current pass's shared hash", latest_spec_content_hash_from_rounds(rounds) == "sh1"))

    base_spec = {"allowed_actions": [{"type": "title-tag-rewrite", "tier": 1, "manual_approval_only": False}], "auto_implementation_enabled": True}
    base_proposal = {"action_type": "title-tag-rewrite", "implementation": {"previous_value": "Old"}}
    good_context = {"breach_status": "active", "current_value": "Old", "current_value_error": None, "spec_content_hash": compute_spec_content_hash(base_spec)}
    eligible, results, reasons = evaluate_auto_implementation_eligibility(base_proposal, base_spec, good_context, "approve", good_context["spec_content_hash"])
    checks.append(("eligibility passes when every gate is satisfied", eligible is True and reasons == []))

    tier2_spec = {"allowed_actions": [{"type": "title-tag-rewrite", "tier": 2, "manual_approval_only": False}], "auto_implementation_enabled": True}
    forged_proposal = {"action_type": "title-tag-rewrite", "tier": 1, "implementation": {"previous_value": "Old"}}  # proposal claims tier 1
    tier2_context = {"breach_status": "active", "current_value": "Old", "current_value_error": None, "spec_content_hash": compute_spec_content_hash(tier2_spec)}
    eligible2, results2, reasons2 = evaluate_auto_implementation_eligibility(forged_proposal, tier2_spec, tier2_context, "approve", tier2_context["spec_content_hash"])
    checks.append(("eligibility refuses a tier forged on the proposal when the current spec's tier is 2", eligible2 is False and any("tier" in r for r in reasons2)))

    breach_context = dict(good_context, breach_status="paused-breach")
    eligible3, _r3, reasons3 = evaluate_auto_implementation_eligibility(base_proposal, base_spec, breach_context, "approve", good_context["spec_content_hash"])
    checks.append(("eligibility refuses while paused-breach", eligible3 is False and any("paused-breach" in r for r in reasons3)))

    drifted_context = dict(good_context, current_value="Something else now")
    eligible4, _r4, reasons4 = evaluate_auto_implementation_eligibility(base_proposal, base_spec, drifted_context, "approve", good_context["spec_content_hash"])
    checks.append(("eligibility refuses when the live value has drifted", eligible4 is False and any("drift" in r for r in reasons4)))

    eligible5, _r5, reasons5 = evaluate_auto_implementation_eligibility(base_proposal, base_spec, good_context, "approve", "a-stale-spec-hash-from-an-earlier-review")
    checks.append(("eligibility refuses when the spec has changed since review", eligible5 is False and any("changed since" in r for r in reasons5)))

    disabled_spec = dict(base_spec, auto_implementation_enabled=False)
    disabled_context = dict(good_context, spec_content_hash=compute_spec_content_hash(disabled_spec))
    eligible6, _r6, reasons6 = evaluate_auto_implementation_eligibility(base_proposal, disabled_spec, disabled_context, "approve", disabled_context["spec_content_hash"])
    checks.append(("eligibility refuses when auto_implementation_enabled is false", eligible6 is False and any("auto_implementation_enabled" in r for r in reasons6)))

    link_proposal = {"action_type": "internal-link-addition", "implementation": {"source_page": "/a/", "destination_page": "/b/", "anchor_text": "x"}}
    link_spec = {"allowed_actions": [{"type": "internal-link-addition", "tier": 1, "manual_approval_only": False}], "auto_implementation_enabled": True}
    link_context = {"breach_status": "active", "current_value": None, "current_value_error": None, "spec_content_hash": compute_spec_content_hash(link_spec)}
    eligible7, results7, reasons7 = evaluate_auto_implementation_eligibility(link_proposal, link_spec, link_context, "approve", link_context["spec_content_hash"])
    checks.append(("internal-link-addition is always held for lack of a mechanical auto-implementer, even when otherwise eligible", eligible7 is False and results7["has_auto_implementer"] is False))

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    print(f"{len(checks) - failed}/{len(checks)} checks passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    import sys as _sys
    if "--verify" in _sys.argv:
        _self_test()

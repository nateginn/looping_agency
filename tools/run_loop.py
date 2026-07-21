# Run contract engine (AgentColabPlan.md "The run contract"). One call
# = one run of one loop for one project. Deterministic and testable by
# design: the judgment step (picking actions) uses a simple heuristic
# here; a human-in-the-loop skill wraps this for real proposal quality.
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import yaml

try:
    from . import dataforseo, gsc
    from .lib.credentials import resolve_credential
    from .lib.errors import ConnectorError
    from .lib.github_compare import compare_commit_to_main
    from .lib.gsc_auth import bearer_for_secret
    from .lib.lock import acquire_lock, acquire_named_lock, release_lock, release_named_lock, log_refusal
    from .lib.paths import assert_within
    from .lib.proposals import atomic_write_json, list_proposals, load_json, proposal_path, write_proposal
    from .lib.redact import redact_deep
    from .spec_validate import validate_spec_file, extract_frontmatter
    from .snapshot import write_snapshot
    from .mock_metrics import pull_metrics as pull_mock_metrics
except ImportError:
    import dataforseo
    import gsc
    from lib.credentials import resolve_credential
    from lib.errors import ConnectorError
    from lib.github_compare import compare_commit_to_main
    from lib.gsc_auth import bearer_for_secret
    from lib.lock import acquire_lock, acquire_named_lock, release_lock, release_named_lock, log_refusal
    from lib.paths import assert_within
    from lib.proposals import atomic_write_json, list_proposals, load_json, proposal_path, write_proposal
    from lib.redact import redact_deep
    from spec_validate import validate_spec_file, extract_frontmatter
    from snapshot import write_snapshot
    from mock_metrics import pull_metrics as pull_mock_metrics

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(THIS_DIR)
PROJECTS_ROOT = os.path.join(WORKSPACE_ROOT, "projects")
DEFAULT_LOCK_TTL_MINUTES = 60  # fallback when the spec doesn't parse at all
MIN_LOCK_TTL_MINUTES = 1
MAX_LOCK_TTL_MINUTES = 24 * 60
APPLY_LOCK_NAME = "apply.lock"
APPLY_LOCK_TTL_MINUTES = 240
LIVE_COMPARE_TARGETS = {
    "art": {"owner": "nateginn", "repo": "artwebsite"},
}


def _now_iso(now=None):
    now = now or datetime.now(timezone.utc)
    return now.isoformat().replace("+00:00", "Z")


def _read_lock_ttl_minutes_unsafe(spec_path, fallback=DEFAULT_LOCK_TTL_MINUTES):
    """Read just `max_run_duration_minutes` from spec.md, clamped to a sane range.
    Runs *before* full schema validation (the lock must be acquired first, per
    the run contract), so this only trusts a single bounded number out of an
    otherwise-untrusted file - never the full spec - and falls back safely on
    any parse error."""
    try:
        with open(spec_path, "r", encoding="utf-8") as f:
            source = f.read()
        fm = extract_frontmatter(source)
        if fm is None:
            return fallback
        parsed = yaml.safe_load(fm)
        v = parsed.get("max_run_duration_minutes") if isinstance(parsed, dict) else None
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            return min(max(v, MIN_LOCK_TTL_MINUTES), MAX_LOCK_TTL_MINUTES)
    except Exception:
        pass  # Unreadable/malformed spec: fall through to the conservative fallback below.
    return fallback


def _load_spec(spec_path):
    with open(spec_path, "r", encoding="utf-8") as f:
        source = f.read()
    return yaml.safe_load(extract_frontmatter(source))


def _load_json_safe(path, fallback):
    if not os.path.exists(path):
        return fallback
    return load_json(path)


def _list_pending_proposals(pending_dir):
    return list_proposals(pending_dir)


def _write_proposal(pending_dir, proposal):
    write_proposal(pending_dir, proposal)


def _target_key(target):
    return json.dumps(target or {}, sort_keys=True)


def _cooldown_key_from_target(target):
    return _target_key({"page": (target or {}).get("page")})


def _compare(value, comparator, threshold):
    if comparator == "<":
        return value < threshold
    if comparator == ">":
        return value > threshold
    if comparator == "<=":
        return value <= threshold
    if comparator == ">=":
        return value >= threshold
    if comparator == "==":
        return value == threshold
    raise ValueError(f"unknown comparator {comparator}")


def _aliases_used(spec):
    """{input -> credential alias} for every connector the spec declares."""
    aliases = spec.get("credential_aliases") or {}
    out = {}
    for input_name in spec.get("inputs") or []:
        out[input_name] = aliases.get("mock", "demo-gsc-readonly") if input_name == "mock" else aliases.get(input_name)
    return out


def _page_path(page):
    """Canonical page key for cross-connector matching: GSC returns the page
    dimension as a full URL while spec targets are site paths - reduce both
    to the path component so (keyword, page) merges actually attach."""
    if isinstance(page, str) and (page.startswith("http://") or page.startswith("https://")):
        return urlsplit(page).path or "/"
    return page


def _merge_metrics(results):
    """Merge per-connector metrics into one metrics object. GSC is the primary
    source (positions/clicks/impressions/sample_size drive evaluation and
    proposal picking); DataForSEO only enriches matching (keyword, page) rows
    with an independent serp_position - it never adds rows, since SERP data
    has no click/impression signal to rank candidates by."""
    by_tool = {tool: metrics for tool, _alias, metrics in results}
    primary = by_tool.get("gsc") or by_tool.get("mock-metrics") or results[0][2]

    merged = dict(primary)
    secret_map = {}
    for _tool, _alias, metrics in results:
        secret_map.update(metrics.get("secretMap") or {})
    merged["secretMap"] = secret_map
    merged["sources"] = [tool for tool, _alias, _metrics in results]

    serp = by_tool.get("dataforseo")
    if serp is not None and serp is not primary:
        serp_by_target = {(k.get("keyword"), _page_path(k.get("page"))): k.get("position") for k in serp.get("keywords") or []}
        merged["keywords"] = [dict(k) for k in merged.get("keywords") or []]
        for k in merged["keywords"]:
            serp_position = serp_by_target.get((k.get("keyword"), _page_path(k.get("page"))))
            if serp_position is not None:
                k["serp_position"] = serp_position
    return merged


def _fetch_metrics(spec, scenario, project_dir=None, resolve_credential_fn=None, http_post=None):
    """Dispatch every entry of spec.inputs to its connector and merge the
    results. resolve_credential_fn / http_post are injected by tests only;
    the defaults are the real credential resolver (Credential Manager ->
    ACL-checked .env) and each connector's real HTTP client. Any connector
    failure raises ConnectorError so the run lands in the uniform
    partial-failure path - real connectors redact their own messages, so
    those wrappers carry an empty raw_secrets map."""
    aliases = _aliases_used(spec)
    resolver = resolve_credential_fn or (lambda alias: resolve_credential(alias, project_dir=project_dir))
    results = []
    tool_calls = []

    for input_name in spec.get("inputs") or []:
        if input_name == "mock":
            metrics = pull_mock_metrics(scenario=scenario, credential_alias=aliases["mock"])
            results.append(("mock-metrics", aliases["mock"], metrics))
            tool_calls.append({"tool": "mock-metrics", "args": {"scenario": scenario}, "at": _now_iso(), "ok": True})
        elif input_name == "gsc":
            window_days = spec.get("metrics_window_days") or 28
            end_date = datetime.now(timezone.utc).date()
            # GSC date ranges are inclusive of both endpoints: a 28-day window is end - 27.
            start_date = end_date - timedelta(days=window_days - 1)
            args = {"site_url": spec.get("site_url"), "start_date": start_date.isoformat(), "end_date": end_date.isoformat()}
            try:
                metrics = gsc.pull_metrics(
                    credential_alias=aliases["gsc"],
                    # A stored service-account JSON is exchanged for a short-lived
                    # access token here; a raw token passes through unchanged.
                    resolve_credential=lambda a: bearer_for_secret(resolver(a)),
                    dimensions=["query", "page"],
                    http_post=http_post or gsc._default_http_post,
                    **args,
                )
            except Exception as e:
                raise ConnectorError(f"gsc connector failed: {e}", raw_secrets={}, tool_name="gsc") from None
            results.append(("gsc", aliases["gsc"], metrics))
            tool_calls.append({"tool": "gsc", "args": args, "at": _now_iso(), "ok": True})
        elif input_name == "dataforseo":
            args = {
                "targets": spec.get("targets"),
                "location_code": spec.get("location_code") or 2840,
                "language_code": spec.get("language_code") or "en",
                "device": spec.get("device") or "desktop",
            }
            try:
                metrics = dataforseo.pull_metrics(
                    credential_alias=aliases["dataforseo"],
                    resolve_credential=resolver,
                    http_post=http_post or dataforseo._default_http_post,
                    **args,
                )
            except Exception as e:
                raise ConnectorError(f"dataforseo connector failed: {e}", raw_secrets={}, tool_name="dataforseo") from None
            results.append(("dataforseo", aliases["dataforseo"], metrics))
            tool_calls.append({"tool": "dataforseo", "args": {"targets": args["targets"]}, "at": _now_iso(), "ok": True})
        else:
            raise ConnectorError(
                f'run_loop.py: no connector wired for spec input "{input_name}" (known: mock, gsc, dataforseo)',
                raw_secrets={},
                tool_name=input_name,
            )

    if not results:
        raise ConnectorError("run_loop.py: spec.inputs is empty - nothing to fetch", raw_secrets={}, tool_name="metrics-connector")
    return {"metrics": _merge_metrics(results), "tool_calls": tool_calls}


def _proposal_row_for_page(metrics, proposal):
    target_page = _page_path(proposal["target"]["page"])
    return next((k for k in metrics["keywords"] if _page_path(k.get("page")) == target_page), None)


def _evaluate_prior_experiments(proposals, metrics, spec, run_id, now):
    """Evaluate applied proposals whose observation window has elapsed.
    Returns (decisions, breach_or_none, still_cooling_down_set)."""
    decisions = []
    breach = None
    still_cooling_down = set()

    for p in proposals:
        if p.get("status") != "applied":
            continue
        applied_at = datetime.fromisoformat(p["applied_at"].replace("Z", "+00:00"))
        age_days = (now - applied_at).total_seconds() / 86400
        window_elapsed = age_days >= p["observation_window_days"]
        sample_ok = metrics["sample_size"] >= p["min_sample_size"]

        if not window_elapsed or not sample_ok:
            still_cooling_down.add(_cooldown_key_from_target(p["target"]))
            decisions.append(
                f'proposal {p["id"]}: still in observation window '
                f'(age {age_days:.1f}d/{p["observation_window_days"]}d, sample {metrics["sample_size"]}/{p["min_sample_size"]})'
            )
            continue

        row = _proposal_row_for_page(metrics, p)
        ft = spec["failure_threshold"]
        metric_value = row["position"] if row else None
        # Guardrail is a *regression from baseline* (position drop post-change), not an
        # absolute position value - a keyword stably sitting at position 8 is not a breach.
        baseline = p.get("baseline_position")
        drift = (metric_value - baseline) if (row is not None and baseline is not None) else None
        breached = drift is not None and "position" in ft["metric"] and _compare(drift, ft["comparator"], ft["value"])

        p["evaluated_position"] = metric_value
        p["position_delta"] = None if (metric_value is None or baseline is None) else metric_value - baseline

        if breached:
            p["status"] = "breached"
            p["evaluated_run_id"] = run_id
            breach = {
                "proposal_id": p["id"],
                "reason": (
                    f'guardrail breach on {ft["metric"]} for {p["target"]["page"]}: position moved '
                    f'{baseline} -> {metric_value} (drift {drift:.1f} {ft["comparator"]} {ft["value"]})'
                ),
            }
            decisions.append(f'proposal {p["id"]}: BREACH — {breach["reason"]}')
        else:
            p["status"] = "verified"
            p["evaluated_run_id"] = run_id
            decisions.append(f'proposal {p["id"]}: verified winner (position {baseline} -> {metric_value}, within guardrail)')

    return decisions, breach, still_cooling_down


def _promote_live_implementations(project_slug, loop_dir, pending_dir, proposals, requester=None, now=None, compare_target=None):
    now_iso = _now_iso(now)
    decisions = []
    awaiting_ids = []
    stuck_ids = []
    compare_target = compare_target or LIVE_COMPARE_TARGETS.get(project_slug)
    if not compare_target:
        return decisions, awaiting_ids, stuck_ids

    promotions = []
    for proposal in proposals:
        if proposal.get("status") != "implemented" or not proposal.get("implemented_commit_sha"):
            continue
        try:
            compare = compare_commit_to_main(
                compare_target["owner"],
                compare_target["repo"],
                proposal["implemented_commit_sha"],
                requester=requester,
            )
        except Exception as err:
            proposal["implemented_run_cycles_seen"] = proposal.get("implemented_run_cycles_seen", 0) + 1
            proposal["live_check_error"] = str(err)
            awaiting_ids.append(proposal["id"])
            if proposal["implemented_run_cycles_seen"] >= 3:
                stuck_ids.append(proposal["id"])
            decisions.append(f'proposal {proposal["id"]}: awaiting live confirmation (compare check failed: {err})')
            continue

        proposal.pop("live_check_error", None)
        if compare["live"]:
            promotions.append(proposal["id"])
            continue

        proposal["implemented_run_cycles_seen"] = proposal.get("implemented_run_cycles_seen", 0) + 1
        proposal["live_compare_status"] = compare["status"]
        awaiting_ids.append(proposal["id"])
        if proposal["implemented_run_cycles_seen"] >= 3:
            stuck_ids.append(proposal["id"])
        decisions.append(
            f'proposal {proposal["id"]}: awaiting live confirmation '
            f'(compare status {compare["status"]}, cycle {proposal["implemented_run_cycles_seen"]})'
        )

    if not promotions:
        return decisions, awaiting_ids, stuck_ids

    lock = acquire_named_lock(loop_dir, APPLY_LOCK_NAME, max_run_duration_minutes=APPLY_LOCK_TTL_MINUTES, runs_dir=os.path.join(loop_dir, "runs"), now=now)
    if not lock["acquired"]:
        for proposal_id in promotions:
            decisions.append(f"proposal {proposal_id}: compare API says live, but apply lock is busy so applied transition will retry next run")
        return decisions, awaiting_ids, stuck_ids

    try:
        for proposal_id in promotions:
            fresh_path = proposal_path(pending_dir, proposal_id)
            fresh = load_json(fresh_path)
            if fresh.get("status") != "implemented":
                continue
            fresh["status"] = "applied"
            fresh["applied_at"] = now_iso
            fresh["applied_by"] = "github-compare-api"
            fresh.pop("live_check_error", None)
            fresh["implemented_run_cycles_seen"] = fresh.get("implemented_run_cycles_seen", 0)
            atomic_write_json(fresh_path, fresh)
            for proposal in proposals:
                if proposal["id"] == proposal_id:
                    proposal.clear()
                    proposal.update(fresh)
                    break
            decisions.append(f'proposal {proposal_id}: live on GitHub main - transitioned implemented -> applied at {now_iso}')
    finally:
        release_named_lock(loop_dir, lock["run_id"], APPLY_LOCK_NAME)

    return decisions, awaiting_ids, stuck_ids


def _pick_new_actions(spec, metrics, cooling_down, run_id, now, max_count=3):
    candidates = [k for k in metrics["keywords"] if _target_key({"page": k["page"]}) not in cooling_down]
    # Real connector rows can carry None fields (e.g. a SERP-only row has no clicks).
    candidates = [k for k in candidates if k["position"] is not None and 3 < k["position"] <= 20]

    # Project-configured relevance filter: drop candidates whose keyword text
    # matches a known irrelevant/noise term (e.g. an unrelated business's brand
    # name that shows up in this domain's GSC query data) before ranking.
    exclusions = [t.lower() for t in (spec.get("keyword_exclusions") or [])]
    excluded_count = 0
    if exclusions:
        before = len(candidates)
        candidates = [k for k in candidates if not any(term in (k.get("keyword") or "").lower() for term in exclusions)]
        excluded_count = before - len(candidates)

    candidates.sort(key=lambda k: k["clicks"] or 0, reverse=True)

    proposals = []
    allowed_actions = spec["allowed_actions"]
    n = min(max_count, len(candidates), len(allowed_actions))
    for i in range(n):
        action = allowed_actions[i % len(allowed_actions)]
        kw = candidates[i]
        proposals.append(
            {
                "id": f"prop-{run_id}-{i}",
                "loop": spec["loop"],
                "action_type": action["type"],
                "tier": action["tier"],
                "target": {"page": kw["page"], "keyword": kw["keyword"]},
                "baseline_position": kw["position"],
                "rationale": f'keyword "{kw["keyword"]}" at position {kw["position"]} on {kw["page"]} with {kw["clicks"]} clicks — candidate for {action["type"]}',
                "rollback": action.get("rollback"),
                "manual_approval_only": action.get("manual_approval_only") is True,
                "observation_window_days": action["observation_window_days"],
                "min_sample_size": action["min_sample_size"],
                "status": "draft",
                "created_run_id": run_id,
                "created_at": now.isoformat().replace("+00:00", "Z"),
                "run_cycles_seen": 0,
                "decision": None,
                "applied_at": None,
            }
        )
    return proposals, excluded_count


def _status_buckets(proposals):
    buckets = {}
    for proposal in proposals:
        buckets.setdefault(proposal.get("status") or "unknown", []).append(proposal["id"])
    return buckets


def _report_lines(run_id, project_slug, loop_name, status, decisions, new_proposals, stale_ids, proposals, awaiting_ids, stuck_ids, evaluated_now):
    status_buckets = _status_buckets(proposals)
    report_lines = [
        f"# Run {run_id} ({loop_name} / {project_slug})",
        "",
        f"**Status:** {status}",
        "",
        "## Decisions",
        *([f"- {d}" for d in decisions] if decisions else ["- none"]),
        "",
        "## Proposals by status",
    ]
    if status_buckets:
        for proposal_status in sorted(status_buckets):
            report_lines.append(f'- {proposal_status}: {", ".join(status_buckets[proposal_status])}')
    else:
        report_lines.append("- none")

    report_lines.extend(
        [
            "",
            f"## New proposals ({len(new_proposals)})",
            *([f'- {p["id"]}: {p["action_type"]} on {p["target"]["page"]} (tier {p["tier"]})' for p in new_proposals] if new_proposals else ["- none"]),
            "",
            "## Awaiting live confirmation",
            *([f"- {proposal_id}" for proposal_id in awaiting_ids] if awaiting_ids else ["- none"]),
            "",
            "## Stuck implemented (>=3 cycles)",
            *([f"- {proposal_id}" for proposal_id in stuck_ids] if stuck_ids else ["- none"]),
            "",
            "## Verified / breached this run",
        ]
    )
    if evaluated_now:
        for proposal in evaluated_now:
            delta = proposal.get("position_delta")
            delta_text = "unknown" if delta is None else f"{delta:+.1f}"
            report_lines.append(
                f'- {proposal["id"]}: {proposal["status"]} '
                f'(position {proposal.get("baseline_position")} -> {proposal.get("evaluated_position")}, delta {delta_text})'
            )
    else:
        report_lines.append("- none")

    report_lines.extend(
        [
            "",
            "## Stale proposals (>=3 cycles undecided)",
            *([f"- {sid}" for sid in stale_ids] if stale_ids else ["- none"]),
        ]
    )
    return report_lines


def run_loop(project_slug, loop_name, scenario="normal", _resolve_credential=None, _http_post=None, _github_requester=None, _github_compare_target=None):
    project_dir = os.path.join(PROJECTS_ROOT, project_slug)
    assert_within(PROJECTS_ROOT, project_dir, "project directory")
    loop_dir = os.path.join(project_dir, "loops", loop_name)
    assert_within(project_dir, loop_dir, "loop directory")

    spec_path = os.path.join(loop_dir, "spec.md")
    memory_path = os.path.join(loop_dir, "memory.md")
    pending_dir = os.path.join(loop_dir, "pending")
    runs_dir = os.path.join(loop_dir, "runs")
    state_path = os.path.join(loop_dir, "state.json")

    now = datetime.now(timezone.utc)

    # Step 1: acquire lock. TTL comes from the loop's own spec.max_run_duration_minutes
    # (read unsafely/clamped - full schema validation happens in step 2, after the lock).
    lock_ttl_minutes = _read_lock_ttl_minutes_unsafe(spec_path)
    lock = acquire_lock(loop_dir, max_run_duration_minutes=lock_ttl_minutes, runs_dir=runs_dir, now=now)
    if not lock["acquired"]:
        log_refusal(loop_dir, lock["reason"])
        return {"status": "refused", "reason": lock["reason"]}
    run_id = lock["run_id"]
    run_dir = os.path.join(runs_dir, run_id)

    try:
        # Step 2: validate spec.
        validation = validate_spec_file(spec_path)
        if not validation["valid"]:
            os.makedirs(run_dir, exist_ok=True)
            atomic_write_json(os.path.join(run_dir, "validation-failure.json"), validation)
            return {"status": "invalid-spec", "run_id": run_id, "errors": validation["errors"]}
        spec = _load_spec(spec_path)

        state = _load_json_safe(state_path, {"status": "active"})
        proposals = _list_pending_proposals(pending_dir)

        # Step 3+9: pull metrics, write immutable snapshot (partial-failure path handled here).
        metrics = None
        tool_calls = []
        try:
            pulled = _fetch_metrics(spec, scenario, project_dir=project_dir, resolve_credential_fn=_resolve_credential, http_post=_http_post)
            metrics = pulled["metrics"]
            tool_calls = pulled["tool_calls"]
        except ConnectorError as err:
            secret_map = err.raw_secrets or {}
            redacted_message = redact_deep(str(err), secret_map)
            failed_tool = getattr(err, "tool_name", None) or "metrics-connector"
            os.makedirs(run_dir, exist_ok=True)
            run_json = {
                "run_id": run_id,
                "project": project_slug,
                "loop": loop_name,
                "start": now.isoformat().replace("+00:00", "Z"),
                "end": _now_iso(),
                "status": "partial-failure",
                "tool_calls": [{"tool": failed_tool, "args": {"scenario": scenario}, "at": _now_iso(), "ok": False, "error": redacted_message}],
                "credential_alias_used": _aliases_used(spec),
                "decisions": ["connector failed; no proposals generated this run; prior state left untouched"],
                "proposals_created": [],
                "proposals_evaluated": [],
                "stale_proposals": [],
                "awaiting_live_confirmation": [],
                "stuck_implemented": [],
                "final_status": "partial-failure",
            }
            atomic_write_json(os.path.join(run_dir, "run.json"), run_json)
            with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8", newline="\n") as f:
                f.write(
                    f"# Run {run_id} ({loop_name} / {project_slug})\n\n"
                    f"**Status:** partial-failure\n\n"
                    f"Connector call failed:\n\n```\n{redacted_message}\n```\n\n"
                    f"No proposals were generated this run. State left untouched.\n"
                )
            with open(memory_path, "a", encoding="utf-8", newline="\n") as f:
                f.write(f"- {_now_iso()} run {run_id}: partial-failure (connector error, redacted)\n")
            return {"status": "partial-failure", "run_id": run_id}

        snapshot_path = write_snapshot(run_dir, metrics, metrics.get("secretMap") or {})

        live_decisions, awaiting_ids, stuck_ids = _promote_live_implementations(
            project_slug,
            loop_dir,
            pending_dir,
            proposals,
            requester=_github_requester,
            now=now,
            compare_target=_github_compare_target,
        )

        # Step 4: evaluate prior experiments (only past-window, sufficient-sample ones).
        eval_decisions, breach, still_cooling_down = _evaluate_prior_experiments(proposals, metrics, spec, run_id, now)
        eval_decisions = live_decisions + eval_decisions

        # Step 5: cooldown - also exclude anything still 'applied' and within window (already in still_cooling_down).
        for proposal in proposals:
            if proposal.get("status") in ("applied", "approved", "implemented", "implement-failed"):
                still_cooling_down.add(_cooldown_key_from_target(proposal["target"]))

        new_state = state
        if breach:
            new_state = {
                "status": "paused-breach",
                "paused_reason": breach["reason"],
                "paused_at": _now_iso(),
                "breach_run_id": run_id,
                "breach_proposal_id": breach["proposal_id"],
            }
            original = next(p for p in proposals if p["id"] == breach["proposal_id"])
            revert_proposal = {
                "id": f'revert-{breach["proposal_id"]}',
                "loop": loop_name,
                "action_type": f'revert:{original["action_type"]}',
                "tier": original["tier"],
                "target": original["target"],
                "rationale": f'auto-generated revert proposal after guardrail breach: {breach["reason"]}',
                "rollback": original.get("rollback"),
                "manual_approval_only": True,
                "status": "draft",
                "created_run_id": run_id,
                "created_at": _now_iso(),
                "run_cycles_seen": 0,
                "decision": None,
                "applied_at": None,
                "observation_window_days": original["observation_window_days"],
                "min_sample_size": original["min_sample_size"],
            }
            _write_proposal(pending_dir, revert_proposal)
            proposals.append(revert_proposal)

        # Step 6: stale-proposal surfacing - bump cycle counters on undecided proposals.
        stale_ids = []
        for proposal in proposals:
            if proposal.get("status") in ("draft", "reviewed"):
                proposal["run_cycles_seen"] = proposal.get("run_cycles_seen", 0) + 1
                if proposal["run_cycles_seen"] >= 3:
                    stale_ids.append(proposal["id"])

        # Step 7: pick new actions, unless paused-breach.
        new_proposals = []
        if new_state["status"] == "paused-breach":
            eval_decisions.append("BLOCKED: loop is paused-breach — no new proposals until a human resolves the failed experiment via /review-pending")
        else:
            new_proposals, excluded_count = _pick_new_actions(spec, metrics, still_cooling_down, run_id, now)
            if excluded_count:
                eval_decisions.append(f"keyword_exclusions filtered {excluded_count} candidate(s)")
            for proposal in new_proposals:
                _write_proposal(pending_dir, proposal)

        # Persist mutated proposal states (evaluated/breached/verified/stale counters).
        for proposal in proposals:
            proposal.pop("_file", None)
            atomic_write_json(proposal_path(pending_dir, proposal["id"]), proposal)
        atomic_write_json(state_path, new_state)

        # Step 8: run.json + report.md + memory.md append.
        secret_map = metrics.get("secretMap") or {}
        evaluated_now = [p for p in proposals if p.get("evaluated_run_id") == run_id]
        run_json = redact_deep(
            {
                "run_id": run_id,
                "project": project_slug,
                "loop": loop_name,
                "start": now.isoformat().replace("+00:00", "Z"),
                "end": _now_iso(),
                "status": "paused-breach" if new_state["status"] == "paused-breach" else "ok",
                "tool_calls": tool_calls,
                "credential_alias_used": _aliases_used(spec),
                "decisions": list(eval_decisions),
                "proposals_created": [p["id"] for p in new_proposals],
                "proposals_evaluated": [p["id"] for p in evaluated_now],
                "stale_proposals": stale_ids,
                "awaiting_live_confirmation": awaiting_ids,
                "stuck_implemented": stuck_ids,
                "snapshot": os.path.relpath(snapshot_path, loop_dir),
                "final_status": "paused-breach" if new_state["status"] == "paused-breach" else "ok",
            },
            secret_map,
        )
        atomic_write_json(os.path.join(run_dir, "run.json"), run_json)

        report_lines = _report_lines(
            run_id,
            project_slug,
            loop_name,
            run_json["status"],
            eval_decisions,
            new_proposals,
            stale_ids,
            proposals,
            awaiting_ids,
            stuck_ids,
            evaluated_now,
        )
        with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(report_lines) + "\n")

        with open(memory_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(
                f'- {_now_iso()} run {run_id}: {run_json["status"]}, {len(new_proposals)} new proposal(s), '
                f'{len(run_json["proposals_evaluated"])} evaluated, {len(stale_ids)} stale, '
                f'{len(awaiting_ids)} awaiting-live\n'
            )

        return {"status": run_json["status"], "run_id": run_id, "run_json": run_json}
    finally:
        release_lock(loop_dir, run_id)


if __name__ == "__main__":
    try:
        from .lib.tls import enable_system_truststore
    except ImportError:
        from lib.tls import enable_system_truststore
    enable_system_truststore()  # live HTTPS through the Windows cert store (R8)

    args = sys.argv[1:]
    positional = [a for a in args if not a.startswith("--")]
    project = positional[0] if len(positional) > 0 else None
    loop = positional[1] if len(positional) > 1 else None
    scenario = "normal"
    if "--scenario" in args:
        idx = args.index("--scenario")
        if idx + 1 < len(args):
            scenario = args[idx + 1]

    if not project or not loop:
        print("usage: python tools/run_loop.py <project> <loop> [--scenario normal|breach|fail]", file=sys.stderr)
        sys.exit(2)

    try:
        result = run_loop(project, loop, scenario=scenario)
        print(json.dumps(result, indent=2))
        sys.exit(1 if result["status"] in ("refused", "invalid-spec") else 0)
    except Exception as err:
        print(f"run-loop failed unexpectedly: {err}", file=sys.stderr)
        sys.exit(1)

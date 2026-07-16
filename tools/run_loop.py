# Run contract engine (AgentColabPlan.md "The run contract"). One call
# = one run of one loop for one project. Deterministic and testable by
# design: the judgment step (picking actions) uses a simple heuristic
# here; a human-in-the-loop skill wraps this for real proposal quality.
import json
import os
import sys
from datetime import datetime, timezone

import yaml

try:
    from .lib.lock import acquire_lock, release_lock, log_refusal
    from .lib.paths import assert_within
    from .lib.redact import redact_deep
    from .spec_validate import validate_spec_file, extract_frontmatter
    from .snapshot import write_snapshot
    from .mock_metrics import pull_metrics as pull_mock_metrics, ConnectorError
except ImportError:
    from lib.lock import acquire_lock, release_lock, log_refusal
    from lib.paths import assert_within
    from lib.redact import redact_deep
    from spec_validate import validate_spec_file, extract_frontmatter
    from snapshot import write_snapshot
    from mock_metrics import pull_metrics as pull_mock_metrics, ConnectorError

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(THIS_DIR)
PROJECTS_ROOT = os.path.join(WORKSPACE_ROOT, "projects")
DEFAULT_LOCK_TTL_MINUTES = 60  # fallback when the spec doesn't parse at all
MIN_LOCK_TTL_MINUTES = 1
MAX_LOCK_TTL_MINUTES = 24 * 60


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _load_json_safe(p, fallback):
    if not os.path.exists(p):
        return fallback
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _list_pending_proposals(pending_dir):
    if not os.path.exists(pending_dir):
        return []
    out = []
    for f in sorted(os.listdir(pending_dir)):
        if not f.endswith(".json"):
            continue
        with open(os.path.join(pending_dir, f), "r", encoding="utf-8") as fh:
            proposal = json.load(fh)
        proposal["_file"] = f
        out.append(proposal)
    return out


def _write_proposal(pending_dir, proposal):
    os.makedirs(pending_dir, exist_ok=True)
    with open(os.path.join(pending_dir, f'{proposal["id"]}.json'), "w", encoding="utf-8", newline="\n") as f:
        json.dump(proposal, f, indent=2)


def _target_key(target):
    return json.dumps(target or {}, sort_keys=True)


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


def _fetch_metrics(spec, scenario):
    alias = (spec.get("credential_aliases") or {}).get("mock", "demo-gsc-readonly")
    if "mock" in (spec.get("inputs") or []):
        return {"tool_name": "mock-metrics", "metrics": pull_mock_metrics(scenario=scenario, credential_alias=alias)}
    raise RuntimeError(
        f'run_loop.py: spec.inputs {spec.get("inputs")!r} has no mock connector wired for this dry run. '
        "Live connectors (gsc/dataforseo) are out of scope until the Phase 1(b) smoke test."
    )


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
            still_cooling_down.add(_target_key(p["target"]))
            decisions.append(
                f'proposal {p["id"]}: still in observation window '
                f'(age {age_days:.1f}d/{p["observation_window_days"]}d, sample {metrics["sample_size"]}/{p["min_sample_size"]})'
            )
            continue

        row = next((k for k in metrics["keywords"] if k["page"] == p["target"]["page"]), None)
        ft = spec["failure_threshold"]
        metric_value = row["position"] if row else None
        # Guardrail is a *regression from baseline* (position drop post-change), not an
        # absolute position value - a keyword stably sitting at position 8 is not a breach.
        baseline = p.get("baseline_position")
        drift = (metric_value - baseline) if (row is not None and baseline is not None) else None
        breached = drift is not None and "position" in ft["metric"] and _compare(drift, ft["comparator"], ft["value"])

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


def _pick_new_actions(spec, metrics, cooling_down, run_id, now, max_count=3):
    candidates = [k for k in metrics["keywords"] if _target_key({"page": k["page"]}) not in cooling_down]
    candidates = [k for k in candidates if 3 < k["position"] <= 20]
    candidates.sort(key=lambda k: k["clicks"], reverse=True)

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
    return proposals


def run_loop(project_slug, loop_name, scenario="normal"):
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
            with open(os.path.join(run_dir, "validation-failure.json"), "w", encoding="utf-8", newline="\n") as f:
                json.dump(validation, f, indent=2)
            return {"status": "invalid-spec", "run_id": run_id, "errors": validation["errors"]}
        spec = _load_spec(spec_path)

        state = _load_json_safe(state_path, {"status": "active"})
        proposals = _list_pending_proposals(pending_dir)

        # Step 3+9: pull metrics, write immutable snapshot (partial-failure path handled here).
        metrics = None
        tool_call_record = None
        try:
            pulled = _fetch_metrics(spec, scenario)
            metrics = pulled["metrics"]
            tool_call_record = {"tool": pulled["tool_name"], "args": {"scenario": scenario}, "at": _now_iso(), "ok": True}
        except ConnectorError as err:
            secret_map = err.raw_secrets or {}
            redacted_message = redact_deep(str(err), secret_map)
            os.makedirs(run_dir, exist_ok=True)
            run_json = {
                "run_id": run_id,
                "project": project_slug,
                "loop": loop_name,
                "start": now.isoformat().replace("+00:00", "Z"),
                "end": _now_iso(),
                "status": "partial-failure",
                "tool_calls": [{"tool": "mock-metrics", "args": {"scenario": scenario}, "at": _now_iso(), "ok": False, "error": redacted_message}],
                "credential_alias_used": (spec.get("credential_aliases") or {}).get("mock"),
                "decisions": ["connector failed; no proposals generated this run; prior state left untouched"],
                "proposals_created": [],
                "proposals_evaluated": [],
                "stale_proposals": [],
                "final_status": "partial-failure",
            }
            with open(os.path.join(run_dir, "run.json"), "w", encoding="utf-8", newline="\n") as f:
                json.dump(run_json, f, indent=2)
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

        # Step 4: evaluate prior experiments (only past-window, sufficient-sample ones).
        eval_decisions, breach, still_cooling_down = _evaluate_prior_experiments(proposals, metrics, spec, run_id, now)

        # Step 5: cooldown - also exclude anything still 'applied' and within window (already in still_cooling_down).
        for p in proposals:
            if p.get("status") == "applied":
                still_cooling_down.add(_target_key(p["target"]))

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
        for p in proposals:
            if p.get("status") in ("draft", "reviewed"):
                p["run_cycles_seen"] = p.get("run_cycles_seen", 0) + 1
                if p["run_cycles_seen"] >= 3:
                    stale_ids.append(p["id"])

        # Step 7: pick new actions, unless paused-breach.
        new_proposals = []
        if new_state["status"] == "paused-breach":
            eval_decisions.append("BLOCKED: loop is paused-breach — no new proposals until a human resolves the failed experiment via /review-pending")
        else:
            new_proposals = _pick_new_actions(spec, metrics, still_cooling_down, run_id, now)
            for p in new_proposals:
                _write_proposal(pending_dir, p)

        # Persist mutated proposal states (evaluated/breached/verified/stale counters).
        for p in proposals:
            file_name = p.pop("_file", None)
            with open(os.path.join(pending_dir, file_name or f'{p["id"]}.json'), "w", encoding="utf-8", newline="\n") as f:
                json.dump(p, f, indent=2)
        with open(state_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(new_state, f, indent=2)

        # Step 8: run.json + report.md + memory.md append.
        secret_map = metrics.get("secretMap") or {}
        evaluated_ids = [p["id"] for p in proposals if p.get("evaluated_run_id") == run_id]
        run_json = redact_deep(
            {
                "run_id": run_id,
                "project": project_slug,
                "loop": loop_name,
                "start": now.isoformat().replace("+00:00", "Z"),
                "end": _now_iso(),
                "status": "paused-breach" if new_state["status"] == "paused-breach" else "ok",
                "tool_calls": [tool_call_record],
                "credential_alias_used": (spec.get("credential_aliases") or {}).get("mock"),
                "decisions": list(eval_decisions),
                "proposals_created": [p["id"] for p in new_proposals],
                "proposals_evaluated": evaluated_ids,
                "stale_proposals": stale_ids,
                "snapshot": os.path.relpath(snapshot_path, loop_dir),
                "final_status": "paused-breach" if new_state["status"] == "paused-breach" else "ok",
            },
            secret_map,
        )
        with open(os.path.join(run_dir, "run.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(run_json, f, indent=2)

        report_lines = [
            f"# Run {run_id} ({loop_name} / {project_slug})",
            "",
            f'**Status:** {run_json["status"]}',
            "",
            "## Decisions",
            *[f"- {d}" for d in eval_decisions],
            "",
            f"## New proposals ({len(new_proposals)})",
            *[f'- {p["id"]}: {p["action_type"]} on {p["target"]["page"]} (tier {p["tier"]})' for p in new_proposals],
            "",
            "## Stale proposals (>=3 cycles undecided)",
            *([f"- {sid}" for sid in stale_ids] if stale_ids else ["- none"]),
        ]
        with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(report_lines) + "\n")

        with open(memory_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(
                f'- {_now_iso()} run {run_id}: {run_json["status"]}, {len(new_proposals)} new proposal(s), '
                f'{len(run_json["proposals_evaluated"])} evaluated, {len(stale_ids)} stale\n'
            )

        return {"status": run_json["status"], "run_id": run_id, "run_json": run_json}
    finally:
        release_lock(loop_dir, run_id)


if __name__ == "__main__":
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

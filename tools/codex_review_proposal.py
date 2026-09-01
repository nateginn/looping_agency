# Phase 7 (PLAN-PHASE7-CODEX-REVIEW.md) - the only tool a Claude session
# (via .claude/skills/codex-seo-review) shells out to for review-pipeline
# state mutation. This module never calls Codex itself - only a Claude-Code
# Bash context can shell out to `codex exec`; this module is deterministic
# bookkeeping around that, exactly like apply.py/review_pending.py/
# draft_copy.py are deterministic bookkeeping around a human's decision.
#
# Every subcommand takes <project> <loop> <id> and acquires the loop's
# shared run.lock for its duration.
import glob
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import yaml

try:
    from .lib.event_log import append_event, build_secret_map, has_corruption_marker, read_events, read_events_fail_closed, sync_proposal_projection
    from .lib.lock import acquire_lock, release_lock
    from .lib.proposals import atomic_write_json, load_json, loop_dir_for, pending_dir_for, proposal_path
    from .lib.review_protocol import (
        adjudicate,
        build_evidence_packet,
        compute_spec_content_hash,
        evidence_packet_hash,
        gather_eligibility_context,
        evaluate_auto_implementation_eligibility,
        hash_proposal_for_review,
        latest_spec_content_hash_from_rounds,
        missing_evidence_fields,
    )
    from .run_loop import _read_lock_ttl_minutes_unsafe
    from .spec_validate import extract_frontmatter
except ImportError:
    from lib.event_log import append_event, build_secret_map, has_corruption_marker, read_events, read_events_fail_closed, sync_proposal_projection
    from lib.lock import acquire_lock, release_lock
    from lib.proposals import atomic_write_json, load_json, loop_dir_for, pending_dir_for, proposal_path
    from lib.review_protocol import (
        adjudicate,
        build_evidence_packet,
        compute_spec_content_hash,
        evidence_packet_hash,
        gather_eligibility_context,
        evaluate_auto_implementation_eligibility,
        hash_proposal_for_review,
        latest_spec_content_hash_from_rounds,
        missing_evidence_fields,
    )
    from run_loop import _read_lock_ttl_minutes_unsafe
    from spec_validate import extract_frontmatter

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(THIS_DIR)
PROJECTS_ROOT = os.path.join(WORKSPACE_ROOT, "projects")


def _now_iso(now=None):
    now = now or datetime.now(timezone.utc)
    return now.isoformat().replace("+00:00", "Z")


def _load_spec(loop_dir):
    with open(os.path.join(loop_dir, "spec.md"), "r", encoding="utf-8") as f:
        return yaml.safe_load(extract_frontmatter(f.read())) or {}


def _load_repo_path(project):
    project_path = os.path.join(PROJECTS_ROOT, project, "project.md")
    if not os.path.exists(project_path):
        return None
    with open(project_path, "r", encoding="utf-8") as f:
        return (yaml.safe_load(extract_frontmatter(f.read())) or {}).get("repo")


def _load_state(loop_dir):
    state_path = os.path.join(loop_dir, "state.json")
    if not os.path.exists(state_path):
        return {}
    return load_json(state_path)


def _latest_snapshot(loop_dir):
    candidates = sorted(glob.glob(os.path.join(loop_dir, "runs", "*", "snapshot.json")), key=os.path.getmtime, reverse=True)
    if not candidates:
        return {}
    return load_json(candidates[0])


def _artifacts_dir(pending_dir):
    return os.path.join(pending_dir, ".review-artifacts")


def _artifact_path(pending_dir, proposal_id, pass_number):
    return os.path.join(_artifacts_dir(pending_dir), f"{proposal_id}-pass{pass_number}.json")


def _acquire(project, loop):
    loop_dir = loop_dir_for(project, loop)
    lock_ttl_minutes = _read_lock_ttl_minutes_unsafe(os.path.join(loop_dir, "spec.md"))
    lock = acquire_lock(loop_dir, max_run_duration_minutes=lock_ttl_minutes, runs_dir=os.path.join(loop_dir, "runs"))
    if not lock["acquired"]:
        raise ValueError(f'REFUSED: run lock active for {project}/{loop} - {lock["reason"]}')
    return loop_dir, lock


# A GBP Posts loop proposal is event-sourced-authoritative under its OWN, stricter model
# (tools/approve_gbp_post.py) - approval requires resolving and locking a live publish target,
# which this Phase 7 review pipeline knows nothing about. Every subcommand here calls
# _load_proposal first, so refusing here closes the path uniformly rather than name-by-name
# (Codex review, 2026-09-01, fifth round: this pipeline had no action_type check at all and
# would happily mutate a GBP proposal's event-sourced status from a stale cached read, exactly
# the same category of bug the GBP-specific tools were hardened against).
GBP_EVENT_SOURCED_ACTION_TYPES = {"gbp-post-draft"}


def _load_proposal(pending_dir, proposal_id):
    path = proposal_path(pending_dir, proposal_id)
    if not os.path.exists(path):
        raise ValueError(f"proposal {proposal_id} not found")
    proposal = load_json(path)
    if proposal.get("action_type") in GBP_EVENT_SOURCED_ACTION_TYPES:
        raise ValueError(
            f'REFUSED: proposal {proposal_id} has action_type "{proposal.get("action_type")}" - this Codex-review '
            "pipeline does not support GBP Post proposals; use tools/approve_gbp_post.py or tools/review_pending.py "
            "(--reject/--review) instead"
        )
    return proposal


def _sync_and_persist(loop_dir, pending_dir, proposal, fail_closed=True):
    projected = sync_proposal_projection(loop_dir, proposal["id"], fail_closed=fail_closed)
    if projected.get("status"):
        proposal["status"] = projected["status"]
    if projected.get("implementation") is not None:
        proposal["implementation"] = projected["implementation"]
    proposal["review"] = projected["review"]
    atomic_write_json(proposal_path(pending_dir, proposal["id"]), proposal)
    return proposal


def _secret_map(spec, repo_path):
    return build_secret_map(repo_path, spec.get("credential_aliases"))


def start_review(project, loop, proposal_id):
    loop_dir, lock = _acquire(project, loop)
    try:
        pending_dir = pending_dir_for(project, loop)
        proposal = _load_proposal(pending_dir, proposal_id)
        if proposal.get("status") != "draft":
            raise ValueError(f'REFUSED: proposal {proposal_id} has status "{proposal.get("status")}", not "draft" - --start-review only enters the review pipeline from a fresh draft')
        spec = _load_spec(loop_dir)
        secret_map = _secret_map(spec, _load_repo_path(project))
        append_event(
            loop_dir, "review_started", project=project, loop=loop, proposal_id=proposal_id,
            action_type=proposal.get("action_type"), target_page=(proposal.get("target") or {}).get("page"),
            keyword=(proposal.get("target") or {}).get("keyword"), resulting_proposal_status="review-pending",
            source_run_id=proposal.get("created_run_id"), secret_map=secret_map,
        )
        return _sync_and_persist(loop_dir, pending_dir, proposal, fail_closed=False)
    finally:
        release_lock(loop_dir, lock["run_id"])


def _current_pass_number(loop_dir, proposal_id):
    rounds = read_events(loop_dir, proposal_id=proposal_id)
    round_count = sum(1 for e in rounds if e.get("event_type") == "review_round_completed")
    return (round_count // 2) + 1


def evidence_packet(project, loop, proposal_id):
    loop_dir, lock = _acquire(project, loop)
    try:
        pending_dir = pending_dir_for(project, loop)
        proposal = _load_proposal(pending_dir, proposal_id)
        proposal = _sync_and_persist(loop_dir, pending_dir, proposal, fail_closed=False)

        missing = missing_evidence_fields(proposal)
        if missing:
            spec = _load_spec(loop_dir)
            secret_map = _secret_map(spec, _load_repo_path(project))
            append_event(
                loop_dir, "review_held", project=project, loop=loop, proposal_id=proposal_id,
                action_type=proposal.get("action_type"), target_page=(proposal.get("target") or {}).get("page"),
                resulting_proposal_status="review-held", final_adjudication="hold",
                objections=[f"not reviewable: missing {f}" for f in missing], secret_map=secret_map,
            )
            _sync_and_persist(loop_dir, pending_dir, proposal, fail_closed=False)
            raise ValueError(f"REFUSED: proposal {proposal_id} is missing required evidence and cannot be reviewed: {', '.join(missing)} - moved to review-held")

        pass_number = _current_pass_number(loop_dir, proposal_id)
        if pass_number > 2:
            raise ValueError(
                f"REFUSED: proposal {proposal_id} has already completed 2 review passes - bounded at 4 total "
                "Codex calls, no further revision/round is permitted; it must be resolved via /review-pending"
            )
        pending_dir_artifacts = _artifacts_dir(pending_dir)
        os.makedirs(pending_dir_artifacts, exist_ok=True)
        artifact_path = _artifact_path(pending_dir, proposal_id, pass_number)
        if os.path.exists(artifact_path):
            return load_json(artifact_path)

        spec = _load_spec(loop_dir)
        snapshot = _latest_snapshot(loop_dir)
        packet, _missing = build_evidence_packet(project, loop, proposal, spec, snapshot, loop_dir, lambda ld: read_events(ld))
        packet["pass_number"] = pass_number
        packet["evidence_packet_hash"] = evidence_packet_hash({k: v for k, v in packet.items() if k != "evidence_packet_hash"})
        atomic_write_json(artifact_path, packet)
        return packet
    finally:
        release_lock(loop_dir, lock["run_id"])


def record_round(project, loop, proposal_id, round_number, verdict_data):
    loop_dir, lock = _acquire(project, loop)
    try:
        pending_dir = pending_dir_for(project, loop)
        proposal = _load_proposal(pending_dir, proposal_id)
        proposal = _sync_and_persist(loop_dir, pending_dir, proposal, fail_closed=True)

        existing_rounds = [e for e in read_events_fail_closed(loop_dir, proposal_id) if e.get("event_type") == "review_round_completed"]
        expected_round = len(existing_rounds) + 1
        if round_number != expected_round:
            raise ValueError(f"REFUSED: round {round_number} is out of order for proposal {proposal_id} - expected round {expected_round}")

        pass_number = ((round_number - 1) // 2) + 1
        artifact_path = _artifact_path(pending_dir, proposal_id, pass_number)
        if not os.path.exists(artifact_path):
            raise ValueError(f"REFUSED: no persisted evidence-packet artifact for pass {pass_number} - run --evidence-packet first")
        artifact = load_json(artifact_path)

        declared_hash = verdict_data.get("evidence_packet_hash")
        if declared_hash != artifact.get("evidence_packet_hash"):
            raise ValueError(
                f"REFUSED: verdict's evidence_packet_hash does not match the persisted pass-{pass_number} artifact - "
                "the packet changed since this round reviewed it; rebuild and resubmit"
            )

        verdict = verdict_data.get("verdict")
        if verdict not in ("approve", "reject", "hold"):
            raise ValueError(f'REFUSED: verdict must be one of approve|reject|hold, got {verdict!r}')

        spec = _load_spec(loop_dir)
        secret_map = _secret_map(spec, _load_repo_path(project))
        event = append_event(
            loop_dir, "review_round_completed", project=project, loop=loop, proposal_id=proposal_id,
            action_type=proposal.get("action_type"), target_page=(proposal.get("target") or {}).get("page"),
            keyword=(proposal.get("target") or {}).get("keyword"), resulting_proposal_status="review-pending",
            round=round_number, pass_number=pass_number, review_verdict=verdict,
            confidence=verdict_data.get("confidence"), objections=verdict_data.get("objections") or [],
            required_corrections=verdict_data.get("required_corrections") or [],
            proposal_content_hash=hash_proposal_for_review(proposal), evidence_packet_hash=declared_hash,
            spec_content_hash=artifact.get("spec_content_hash"), secret_map=secret_map,
        )
        _sync_and_persist(loop_dir, pending_dir, proposal, fail_closed=True)
        return event
    finally:
        release_lock(loop_dir, lock["run_id"])


def adjudicate_proposal(project, loop, proposal_id, repo_path=None):
    loop_dir, lock = _acquire(project, loop)
    try:
        pending_dir = pending_dir_for(project, loop)
        proposal = _load_proposal(pending_dir, proposal_id)
        proposal = _sync_and_persist(loop_dir, pending_dir, proposal, fail_closed=True)

        rounds = (proposal.get("review") or {}).get("rounds") or []
        current_pass_rounds = [r for r in rounds if not r.get("superseded")]
        if len(current_pass_rounds) != 2:
            raise ValueError(f"REFUSED: proposal {proposal_id} does not have exactly two current-pass rounds recorded yet ({len(current_pass_rounds)} found) - not ready to adjudicate")

        result = adjudicate(rounds)
        pass_number = current_pass_rounds[0].get("pass_number")
        spec = _load_spec(loop_dir)
        repo_path = repo_path or _load_repo_path(project)
        secret_map = _secret_map(spec, repo_path)

        auto_implementation = None
        if result == "approve":
            state = _load_state(loop_dir)
            context = gather_eligibility_context(project, loop, proposal, repo_path, spec, state=state)
            adjudicated_hash = latest_spec_content_hash_from_rounds(rounds)
            eligible, policy_results, reasons = evaluate_auto_implementation_eligibility(proposal, spec, context, result, adjudicated_hash)
            auto_implementation = {
                "eligible": eligible, "checked_at": _now_iso(), "policy_results": policy_results,
                "failed_reasons": reasons, "authorized_by": "codex-review-pipeline",
            }
            resulting_status = "approved-for-implementation" if eligible else "review-approved"
            event_type = "proposal_auto_approved" if eligible else "review_approved"
        elif result == "reject":
            resulting_status = "review-rejected"
            event_type = "review_rejected"
        else:  # hold
            resulting_status = "review-revision-needed" if pass_number == 1 else "review-held"
            event_type = "review_held"

        append_event(
            loop_dir, event_type, project=project, loop=loop, proposal_id=proposal_id,
            action_type=proposal.get("action_type"), target_page=(proposal.get("target") or {}).get("page"),
            keyword=(proposal.get("target") or {}).get("keyword"), resulting_proposal_status=resulting_status,
            final_adjudication=result, secret_map=secret_map,
        )
        proposal = _sync_and_persist(loop_dir, pending_dir, proposal, fail_closed=True)
        if auto_implementation is not None:
            proposal["auto_implementation"] = auto_implementation
            atomic_write_json(proposal_path(pending_dir, proposal_id), proposal)
        return proposal
    finally:
        release_lock(loop_dir, lock["run_id"])


def auto_implement(project, loop, proposal_id, repo_path=None):
    try:
        from . import apply as apply_module
    except ImportError:
        import apply as apply_module
    return apply_module.apply_proposal(project, loop, proposal_id, by="codex-review-pipeline", repo_path=repo_path)


def _cli():
    args = sys.argv[1:]
    positional = []
    i = 0
    while i < len(args) and len(positional) < 3:
        if args[i].startswith("--"):
            break
        positional.append(args[i])
        i += 1
    if len(positional) < 3:
        print(
            "usage: python tools/codex_review_proposal.py <project> <loop> <id> "
            "--start-review | --evidence-packet | --record-round --round N --verdict-json <file> | --adjudicate | --auto-implement",
            file=sys.stderr,
        )
        sys.exit(2)
    project, loop, proposal_id = positional
    rest = args[len(positional):]

    try:
        if "--start-review" in rest:
            p = start_review(project, loop, proposal_id)
            print(f'{p["status"]} {p["id"]}')
        elif "--evidence-packet" in rest:
            packet = evidence_packet(project, loop, proposal_id)
            print(json.dumps(packet, indent=2))
        elif "--record-round" in rest:
            if "--round" not in rest or "--verdict-json" not in rest:
                print("--record-round requires --round N --verdict-json <file>", file=sys.stderr)
                sys.exit(2)
            round_number = int(rest[rest.index("--round") + 1])
            verdict_path = rest[rest.index("--verdict-json") + 1]
            with open(verdict_path, "r", encoding="utf-8") as f:
                verdict_data = json.load(f)
            event = record_round(project, loop, proposal_id, round_number, verdict_data)
            print(f'recorded round {event["round"]} ({event["review_verdict"]}) for {proposal_id}')
        elif "--adjudicate" in rest:
            p = adjudicate_proposal(project, loop, proposal_id)
            print(f'{p["status"]} {p["id"]}')
        elif "--auto-implement" in rest:
            p = auto_implement(project, loop, proposal_id)
            print(f'{p["status"]} {p["id"]}')
        else:
            print("no action specified", file=sys.stderr)
            sys.exit(2)
    except Exception as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()

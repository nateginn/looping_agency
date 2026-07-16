# Performs the `applied` transition for a single approved Tier-1 proposal.
# This is the approval gate: it re-checks status == 'approved' itself
# rather than trusting the caller, and it never touches Tier-2 actions -
# those are human-only, always, per AgentColabPlan.md side-effect tiers.
#
# Phase 1 boundary: the "action" performed here is a local simulated
# marker (e.g. what would become a PR-branch-creation call). No real
# repo, API, or credential is touched by this file in this phase.
import json
import os
import sys
from datetime import datetime, timezone

import yaml

try:
    from .lib.paths import assert_within
    from .spec_validate import extract_frontmatter
except ImportError:
    from lib.paths import assert_within
    from spec_validate import extract_frontmatter

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(THIS_DIR)
PROJECTS_ROOT = os.path.join(WORKSPACE_ROOT, "projects")


def _loop_dir_for(project, loop):
    project_dir = os.path.join(PROJECTS_ROOT, project)
    assert_within(PROJECTS_ROOT, project_dir, "project directory")
    loop_dir = os.path.join(project_dir, "loops", loop)
    assert_within(project_dir, loop_dir, "loop directory")
    return loop_dir


def _load_approval_mode(loop_dir):
    spec_path = os.path.join(loop_dir, "spec.md")
    with open(spec_path, "r", encoding="utf-8") as f:
        source = f.read()
    parsed = yaml.safe_load(extract_frontmatter(source))
    return (parsed or {}).get("approval_mode")


def apply_proposal(project, loop, proposal_id, by="human"):
    loop_dir = _loop_dir_for(project, loop)
    pending_dir = os.path.join(loop_dir, "pending")
    proposal_path = os.path.join(pending_dir, f"{proposal_id}.json")
    if not os.path.exists(proposal_path):
        raise ValueError(f"proposal {proposal_id} not found")
    with open(proposal_path, "r", encoding="utf-8") as f:
        proposal = json.load(f)

    if proposal.get("tier") == 2:
        raise ValueError(f"REFUSED: proposal {proposal_id} is Tier 2 (public/paid) — always human-only, never automated by apply.py")
    if proposal.get("tier") == 1:
        approval_mode = _load_approval_mode(loop_dir)
        if approval_mode != "tier1-enabled":
            raise ValueError(
                f'REFUSED: proposal {proposal_id} is Tier 1 but this loop\'s approval_mode is "{approval_mode}" — '
                "Tier-1 applies require approval_mode: tier1-enabled (see AgentColabPlan.md Phase 2: enabled only after human review of the first two reports)"
            )
    if proposal.get("status") != "approved":
        raise ValueError(f'REFUSED: proposal {proposal_id} has status "{proposal.get("status")}", not "approved" — approval gate blocks apply')

    applied_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    proposal["status"] = "applied"
    proposal["applied_at"] = applied_at
    proposal["applied_by"] = by
    with open(proposal_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(proposal, f, indent=2)

    # Markers live outside pending/ so they're never mistaken for proposal state
    # by list_pending_proposals()/list_proposals() (which glob every *.json there).
    applied_dir = os.path.join(loop_dir, "applied")
    os.makedirs(applied_dir, exist_ok=True)
    marker_path = os.path.join(applied_dir, f"{proposal_id}.marker.json")
    with open(marker_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(
            {
                "note": "Phase 1 simulated Tier-1 side effect — no real repo/API/credential touched.",
                "proposal_id": proposal_id,
                "action_type": proposal.get("action_type"),
                "target": proposal.get("target"),
                "applied_at": applied_at,
                "applied_by": by,
            },
            f,
            indent=2,
        )

    return proposal


def _cli():
    args = sys.argv[1:]
    if len(args) < 3:
        print("usage: python tools/apply.py <project> <loop> <proposal-id>", file=sys.stderr)
        sys.exit(2)
    project, loop, proposal_id = args[0], args[1], args[2]
    try:
        p = apply_proposal(project, loop, proposal_id)
        print(f'applied {p["id"]} at {p["applied_at"]}')
    except Exception as err:
        print(str(err), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()

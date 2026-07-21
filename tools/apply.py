import os
import sys
from datetime import datetime, timezone

import yaml

try:
    from .lib.artwebsite_seo import cleanup_worktree, create_worktree, finalize_worktree, inspect_attempt, make_attempt
    from .lib.lock import acquire_named_lock, release_named_lock
    from .lib.proposals import load_json, loop_dir_for, pending_dir_for, proposal_path, atomic_write_json
    from .spec_validate import extract_frontmatter
except ImportError:
    from lib.artwebsite_seo import cleanup_worktree, create_worktree, finalize_worktree, inspect_attempt, make_attempt
    from lib.lock import acquire_named_lock, release_named_lock
    from lib.proposals import load_json, loop_dir_for, pending_dir_for, proposal_path, atomic_write_json
    from spec_validate import extract_frontmatter

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(THIS_DIR)
PROJECTS_ROOT = os.path.join(WORKSPACE_ROOT, "projects")
APPLY_LOCK_NAME = "apply.lock"
APPLY_LOCK_TTL_MINUTES = 240
IMPLEMENTABLE_ACTIONS = {"title-tag-rewrite", "meta-description-rewrite"}


def _now_iso(now=None):
    now = now or datetime.now(timezone.utc)
    return now.isoformat().replace("+00:00", "Z")


def _load_frontmatter(path):
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    return yaml.safe_load(extract_frontmatter(source)) or {}


def _load_approval_mode(loop_dir):
    return _load_frontmatter(os.path.join(loop_dir, "spec.md")).get("approval_mode")


def _load_repo_path(project):
    project_path = os.path.join(PROJECTS_ROOT, project, "project.md")
    return _load_frontmatter(project_path).get("repo")


def _write_proposal_state(path, proposal):
    atomic_write_json(path, proposal)


def _recover_attempt_if_needed(proposal, proposal_pathname, repo_path, git_runner=None, now=None):
    attempt = proposal.get("implement_attempt")
    if not attempt or proposal.get("implemented_commit_sha"):
        return proposal

    inspection = inspect_attempt(repo_path, attempt, git_runner=git_runner)
    if inspection["state"] == "commit-exists":
        cleanup_worktree(repo_path, attempt, git_runner=git_runner, delete_branch=False)
        proposal["status"] = "implemented"
        proposal["implemented_branch"] = attempt["branch"]
        proposal["implemented_commit_sha"] = inspection["head"]
        proposal["implemented_at"] = _now_iso(now)
        proposal.pop("implement_attempt", None)
        proposal.pop("implement_error", None)
        _write_proposal_state(proposal_pathname, proposal)
        return proposal

    cleanup_worktree(repo_path, attempt, git_runner=git_runner, delete_branch=True)
    proposal["status"] = "approved"
    proposal.pop("implement_attempt", None)
    proposal.pop("implement_error", None)
    _write_proposal_state(proposal_pathname, proposal)
    return proposal


def apply_proposal(project, loop, proposal_id, by="human", repo_path=None, git_runner=None, now=None):
    loop_dir = loop_dir_for(project, loop)
    pending_dir = pending_dir_for(project, loop)
    proposal_pathname = proposal_path(pending_dir, proposal_id)
    if not os.path.exists(proposal_pathname):
        raise ValueError(f"proposal {proposal_id} not found")

    lock = acquire_named_lock(loop_dir, APPLY_LOCK_NAME, max_run_duration_minutes=APPLY_LOCK_TTL_MINUTES, runs_dir=os.path.join(loop_dir, "runs"), now=now)
    if not lock["acquired"]:
        raise ValueError(f'REFUSED: apply lock active for {project}/{loop} - {lock["reason"]}')

    repo_path = repo_path or _load_repo_path(project)

    try:
        proposal = load_json(proposal_pathname)
        proposal = _recover_attempt_if_needed(proposal, proposal_pathname, repo_path, git_runner=git_runner, now=now)
        if proposal.get("status") == "implemented":
            return proposal

        if proposal.get("tier") == 2:
            raise ValueError(f"REFUSED: proposal {proposal_id} is Tier 2 (public/paid) - always human-only, never automated by apply.py")
        if proposal.get("tier") == 1:
            approval_mode = _load_approval_mode(loop_dir)
            if approval_mode != "tier1-enabled":
                raise ValueError(
                    f'REFUSED: proposal {proposal_id} is Tier 1 but this loop\'s approval_mode is "{approval_mode}" - '
                    "Tier-1 applies require approval_mode: tier1-enabled (see AgentColabPlan.md Phase 2: enabled only after human review of the first two reports)"
                )
        if proposal.get("manual_approval_only") is True:
            raise ValueError(
                f"REFUSED: proposal {proposal_id} is marked manual_approval_only: true - apply.py will not auto-implement it until a spec author removes that flag for this action"
            )
        if proposal.get("status") != "approved":
            raise ValueError(f'REFUSED: proposal {proposal_id} has status "{proposal.get("status")}", not "approved" - approval gate blocks apply')
        if proposal.get("action_type") not in IMPLEMENTABLE_ACTIONS:
            raise ValueError(f'proposal {proposal_id} action_type "{proposal.get("action_type")}" has no auto-implementer')

        attempt = make_attempt(repo_path, proposal_id, git_runner=git_runner, started_at=_now_iso(now))
        proposal["implement_attempt"] = attempt
        proposal["implemented_by"] = by
        _write_proposal_state(proposal_pathname, proposal)

        try:
            create_worktree(repo_path, attempt, git_runner=git_runner)
            implementation = finalize_worktree(repo_path, proposal, attempt, git_runner=git_runner, now=now)
        except Exception as err:
            proposal = load_json(proposal_pathname)
            proposal["status"] = "implement-failed"
            proposal["implement_error"] = str(err)
            proposal["implemented_by"] = by
            _write_proposal_state(proposal_pathname, proposal)
            return proposal

        proposal = load_json(proposal_pathname)
        proposal["status"] = "implemented"
        proposal["implemented_branch"] = implementation["implemented_branch"]
        proposal["implemented_commit_sha"] = implementation["implemented_commit_sha"]
        proposal["implemented_at"] = implementation["implemented_at"]
        proposal["implemented_by"] = by
        proposal.pop("implement_attempt", None)
        proposal.pop("implement_error", None)
        _write_proposal_state(proposal_pathname, proposal)
        return proposal
    finally:
        release_named_lock(loop_dir, lock["run_id"], APPLY_LOCK_NAME)


def _cli():
    args = sys.argv[1:]
    if len(args) < 3:
        print("usage: python tools/apply.py <project> <loop> <proposal-id>", file=sys.stderr)
        sys.exit(2)
    project, loop, proposal_id = args[0], args[1], args[2]
    try:
        p = apply_proposal(project, loop, proposal_id)
        if p.get("status") == "implemented":
            print(f'implemented {p["id"]} at {p["implemented_at"]}')
        else:
            print(f'{p["status"]} {p["id"]}')
    except Exception as err:
        print(str(err), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()

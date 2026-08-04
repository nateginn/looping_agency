import os
import sys
from datetime import datetime, timezone

import yaml

try:
    from .lib.action_types import IMPLEMENTABLE_ACTIONS
    from .lib.artwebsite_seo import (
        cleanup_worktree,
        create_worktree,
        finalize_worktree,
        inspect_attempt,
        make_attempt,
        verify_implementation_ancestry,
    )
    from .lib.event_log import append_event, build_secret_map, sync_proposal_projection
    from .lib.lock import acquire_lock, release_lock
    from .lib.proposals import load_json, loop_dir_for, pending_dir_for, proposal_path, atomic_write_json
    from .lib.review_protocol import (
        adjudicate,
        evaluate_auto_implementation_eligibility,
        gather_eligibility_context,
        latest_spec_content_hash_from_rounds,
    )
    from .run_loop import _read_lock_ttl_minutes_unsafe
    from .spec_validate import extract_frontmatter
except ImportError:
    from lib.action_types import IMPLEMENTABLE_ACTIONS
    from lib.artwebsite_seo import (
        cleanup_worktree,
        create_worktree,
        finalize_worktree,
        inspect_attempt,
        make_attempt,
        verify_implementation_ancestry,
    )
    from lib.event_log import append_event, build_secret_map, sync_proposal_projection
    from lib.lock import acquire_lock, release_lock
    from lib.proposals import load_json, loop_dir_for, pending_dir_for, proposal_path, atomic_write_json
    from lib.review_protocol import (
        adjudicate,
        evaluate_auto_implementation_eligibility,
        gather_eligibility_context,
        latest_spec_content_hash_from_rounds,
    )
    from run_loop import _read_lock_ttl_minutes_unsafe
    from spec_validate import extract_frontmatter

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(THIS_DIR)
PROJECTS_ROOT = os.path.join(WORKSPACE_ROOT, "projects")


def _now_iso(now=None):
    now = now or datetime.now(timezone.utc)
    return now.isoformat().replace("+00:00", "Z")


def _load_frontmatter(path):
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    return yaml.safe_load(extract_frontmatter(source)) or {}


def _load_approval_mode(loop_dir):
    return _load_frontmatter(os.path.join(loop_dir, "spec.md")).get("approval_mode")


def _load_spec_dict(loop_dir):
    return _load_frontmatter(os.path.join(loop_dir, "spec.md"))


def _load_state(loop_dir):
    state_path = os.path.join(loop_dir, "state.json")
    if not os.path.exists(state_path):
        return {}
    return load_json(state_path)


def _load_repo_path(project):
    project_path = os.path.join(PROJECTS_ROOT, project, "project.md")
    return _load_frontmatter(project_path).get("repo")


def _write_proposal_state(path, proposal):
    atomic_write_json(path, proposal)


def _authorize_auto_implementation(project, loop, loop_dir, proposal, repo_path):
    """Re-derives eligibility entirely from current, live sources - never
    trusts proposal["auto_implementation"]["eligible"] (that field is
    output-only, written by codex_review_proposal.py --adjudicate for humans/
    MMC to see, and is never read here). Returns (eligible, reasons, spec,
    adjudicated_spec_content_hash) so the caller can reuse spec/hash for the
    pre-commit recheck without a third spec read."""
    spec = _load_spec_dict(loop_dir)
    state = _load_state(loop_dir)
    context = gather_eligibility_context(project, loop, proposal, repo_path, spec, state=state)
    rounds = (proposal.get("review") or {}).get("rounds") or []
    fresh_result = adjudicate(rounds)
    adjudicated_hash = latest_spec_content_hash_from_rounds(rounds)
    eligible, _results, reasons = evaluate_auto_implementation_eligibility(proposal, spec, context, fresh_result, adjudicated_hash)
    return eligible, reasons, spec, adjudicated_hash


def _recover_attempt_if_needed(proposal, proposal_pathname, repo_path, git_runner=None, now=None):
    attempt = proposal.get("implement_attempt")
    if not attempt or proposal.get("implemented_commit_sha"):
        return proposal

    inspection = inspect_attempt(repo_path, attempt, git_runner=git_runner)
    if inspection["state"] == "commit-exists":
        # A crashed apply left this attempt's base SHA only inside
        # implement_attempt (or, for an attempt created before this base-SHA
        # persistence existed, not recorded at proposal level at all).
        # Backfill implementation_base_sha now, before implement_attempt is
        # popped below, so recovering a real commit never loses it.
        base_sha = attempt.get("base_commit")
        if base_sha and not proposal.get("implementation_base_sha"):
            proposal["implementation_base_sha"] = base_sha
        if base_sha:
            try:
                verify_implementation_ancestry(repo_path, inspection["head"], base_sha, git_runner=git_runner)
            except ValueError as err:
                cleanup_worktree(repo_path, attempt, git_runner=git_runner, delete_branch=True)
                proposal["status"] = "implement-failed"
                proposal["implement_error"] = str(err)
                proposal.pop("implement_attempt", None)
                proposal.pop("implement_attempt_pre_status", None)
                _write_proposal_state(proposal_pathname, proposal)
                return proposal
        cleanup_worktree(repo_path, attempt, git_runner=git_runner, delete_branch=False)
        proposal["status"] = "implemented"
        proposal["implemented_branch"] = attempt["branch"]
        proposal["implemented_commit_sha"] = inspection["head"]
        proposal["implemented_at"] = _now_iso(now)
        proposal.pop("implement_attempt", None)
        proposal.pop("implement_error", None)
        proposal.pop("implement_attempt_pre_status", None)
        _write_proposal_state(proposal_pathname, proposal)
        return proposal

    cleanup_worktree(repo_path, attempt, git_runner=git_runner, delete_branch=True)
    # Revert to whatever status this proposal actually had before the crashed
    # attempt started - not hardcoded "approved". A proposal on the codex-
    # review auto-implementation path (approved-for-implementation) must not
    # silently become "approved" on recovery, which would let a retry bypass
    # apply_proposal()'s fresh eligibility re-check the next time around.
    proposal["status"] = proposal.pop("implement_attempt_pre_status", "approved")
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

    # Phase 3: one shared mutation lock across run_loop.py/apply.py/
    # review_pending.py/draft_copy.py, not a separate apply.lock - a
    # scheduled run_loop.py run and a human apply/approve/draft can no
    # longer race the same proposal JSON.
    lock_ttl_minutes = _read_lock_ttl_minutes_unsafe(os.path.join(loop_dir, "spec.md"))
    lock = acquire_lock(loop_dir, max_run_duration_minutes=lock_ttl_minutes, runs_dir=os.path.join(loop_dir, "runs"), now=now)
    if not lock["acquired"]:
        raise ValueError(f'REFUSED: run lock active for {project}/{loop} - {lock["reason"]}')

    repo_path = repo_path or _load_repo_path(project)
    secret_map = None  # resolved lazily, only if an event actually needs to be emitted

    def _emit(event_type, proposal_obj, extra=None):
        nonlocal secret_map
        if secret_map is None:
            spec_for_secrets = _load_spec_dict(loop_dir)
            secret_map = build_secret_map(repo_path, spec_for_secrets.get("credential_aliases"))
        fields = {
            "action_type": proposal_obj.get("action_type"),
            "target_page": (proposal_obj.get("target") or {}).get("page"),
            "keyword": (proposal_obj.get("target") or {}).get("keyword"),
            "resulting_proposal_status": proposal_obj.get("status"),
            "implementation_commit": proposal_obj.get("implemented_commit_sha"),
            "implementation_branch": proposal_obj.get("implemented_branch"),
            "source_run_id": proposal_obj.get("created_run_id"),
        }
        if extra:
            fields.update(extra)
        return append_event(loop_dir, event_type, project=project, loop=loop, proposal_id=proposal_id, secret_map=secret_map, now=now, **fields)

    try:
        proposal = load_json(proposal_pathname)
        proposal = _recover_attempt_if_needed(proposal, proposal_pathname, repo_path, git_runner=git_runner, now=now)
        if proposal.get("status") == "implemented":
            return proposal

        auto_path = proposal.get("status") == "approved-for-implementation"
        if auto_path or proposal.get("review"):
            # Event-sourced review subsystem: the on-disk proposal file is a
            # projection that may lag events.jsonl (e.g. a crash between an
            # event append and the proposal-file write) - catch it up before
            # trusting anything review-related. Fail-closed on log corruption.
            projected = sync_proposal_projection(loop_dir, proposal_id, fail_closed=True)
            if projected.get("status"):
                proposal["status"] = projected["status"]
            if projected.get("implementation") is not None:
                proposal["implementation"] = projected["implementation"]
            proposal["review"] = projected["review"]
            auto_path = proposal.get("status") == "approved-for-implementation"

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
        if proposal.get("status") not in ("approved", "approved-for-implementation"):
            raise ValueError(f'REFUSED: proposal {proposal_id} has status "{proposal.get("status")}", not "approved" or "approved-for-implementation" - approval gate blocks apply')
        if proposal.get("action_type") not in IMPLEMENTABLE_ACTIONS:
            raise ValueError(f'proposal {proposal_id} action_type "{proposal.get("action_type")}" has no auto-implementer')

        pre_commit_check = None
        if auto_path:
            # Phase 7: recomputes eligibility entirely fresh - never trusts
            # proposal["auto_implementation"]["eligible"] (output-only, never
            # read here). Makes auto_implementation_enabled/manual_approval_only
            # live gates re-checked at the moment of commit, not just at
            # adjudication time - so flipping either off after adjudication
            # genuinely disables the auto path.
            eligible, reasons, spec_at_entry, adjudicated_hash = _authorize_auto_implementation(project, loop, loop_dir, proposal, repo_path)
            if not eligible:
                raise ValueError(
                    f"REFUSED: proposal {proposal_id} is approved-for-implementation but fresh re-authorization failed: {'; '.join(reasons)}"
                )

            def pre_commit_check(worktree_path):  # noqa: F811 - intentional shadow, closure captures proposal/repo_path/etc.
                # Re-read spec.md a SECOND time immediately before the actual
                # git commit (after the fetch/worktree sequence, which can
                # take real wall-clock time while still holding run.lock) and
                # re-validate every gate uniformly - narrows, does not
                # eliminate, the TOCTOU window on spec.md (not lock-protected
                # anywhere in this codebase, before or after this change).
                eligible2, reasons2, _spec2, _hash2 = _authorize_auto_implementation(project, loop, loop_dir, proposal, repo_path)
                if not eligible2:
                    raise ValueError(
                        f"REFUSED: proposal {proposal_id}'s auto-implementation authorization no longer holds immediately before commit: {'; '.join(reasons2)}"
                    )

        proposal["implement_attempt_pre_status"] = proposal["status"]
        attempt = make_attempt(repo_path, proposal_id, git_runner=git_runner, started_at=_now_iso(now))
        proposal["implement_attempt"] = attempt
        # Persisted on the proposal itself (not just inside implement_attempt,
        # which finalize/recovery both eventually pop) so it survives to the
        # `implemented` state a later publish step needs it in.
        proposal["implementation_base_sha"] = attempt["base_commit"]
        proposal["implemented_by"] = by
        _write_proposal_state(proposal_pathname, proposal)

        try:
            create_worktree(repo_path, attempt, git_runner=git_runner)
            implementation = finalize_worktree(repo_path, proposal, attempt, git_runner=git_runner, now=now, pre_commit_check=pre_commit_check)
            verify_implementation_ancestry(repo_path, implementation["implemented_commit_sha"], attempt["base_commit"], git_runner=git_runner)
        except Exception as err:
            proposal = load_json(proposal_pathname)
            proposal["status"] = "implement-failed"
            proposal["implement_error"] = str(err)
            proposal["implemented_by"] = by
            proposal.pop("implement_attempt_pre_status", None)
            _write_proposal_state(proposal_pathname, proposal)
            _emit("implementation_failed", proposal, {"note": str(err)[:500]})
            return proposal

        proposal = load_json(proposal_pathname)
        proposal["status"] = "implemented"
        proposal["implemented_branch"] = implementation["implemented_branch"]
        proposal["implemented_commit_sha"] = implementation["implemented_commit_sha"]
        proposal["implemented_at"] = implementation["implemented_at"]
        proposal["implemented_by"] = by
        proposal.pop("implement_attempt", None)
        proposal.pop("implement_error", None)
        proposal.pop("implement_attempt_pre_status", None)
        _write_proposal_state(proposal_pathname, proposal)
        _emit("implementation_created", proposal)
        return proposal
    finally:
        release_lock(loop_dir, lock["run_id"])


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

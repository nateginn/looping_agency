# Deterministic, LLM-free storage + validation for SEO proposal copy
# (Phase 2 - AgentColabPlan.md / PLAN.md "Give proposals real content").
# Claude (or a human) authors the actual title/meta text externally; this
# tool only reads the live current value, validates the proposed text
# against it, and writes both onto the proposal via the existing atomic
# proposal-writing path. No network calls, no writes to the website repo.
# Phase 3: acquires the loop's shared run.lock for the duration of the read-
# validate-write sequence, the same lock run_loop.py/apply.py/
# review_pending.py use - a scheduled run can no longer race a draft.
import os
import sys
from datetime import datetime, timezone

import yaml

try:
    from .lib.artwebsite_seo import read_current_value
    from .lib.event_log import append_event, build_secret_map, sync_proposal_projection
    from .lib.lock import acquire_lock, release_lock
    from .lib.proposals import atomic_write_json, load_json, loop_dir_for, pending_dir_for, proposal_path
    from .run_loop import _read_lock_ttl_minutes_unsafe
    from .spec_validate import extract_frontmatter
except ImportError:
    from lib.artwebsite_seo import read_current_value
    from lib.event_log import append_event, build_secret_map, sync_proposal_projection
    from lib.lock import acquire_lock, release_lock
    from lib.proposals import atomic_write_json, load_json, loop_dir_for, pending_dir_for, proposal_path
    from run_loop import _read_lock_ttl_minutes_unsafe
    from spec_validate import extract_frontmatter

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(THIS_DIR)
PROJECTS_ROOT = os.path.join(WORKSPACE_ROOT, "projects")

# Matches apply.py's IMPLEMENTABLE_ACTIONS - only actions with a deterministic
# edit-location resolver can have their copy validated against a real page.
MAX_LENGTHS = {"title-tag-rewrite": 60, "meta-description-rewrite": 155}
DRAFTABLE_ACTIONS = set(MAX_LENGTHS)


def _now_iso(now=None):
    now = now or datetime.now(timezone.utc)
    return now.isoformat().replace("+00:00", "Z")


def _load_repo_path(project):
    project_path = os.path.join(PROJECTS_ROOT, project, "project.md")
    with open(project_path, "r", encoding="utf-8") as f:
        source = f.read()
    return (yaml.safe_load(extract_frontmatter(source)) or {}).get("repo")


def validate_copy(action_type, new_value, previous_value):
    if action_type not in DRAFTABLE_ACTIONS:
        raise ValueError(f'draft_copy.py: action_type "{action_type}" has no draftable copy (supported: {sorted(DRAFTABLE_ACTIONS)})')
    if not isinstance(new_value, str) or new_value.strip() == "":
        raise ValueError("draft_copy.py: new_value must be a non-empty string")
    if "\n" in new_value or "\r" in new_value:
        raise ValueError("draft_copy.py: new_value must be a single line (no line breaks)")
    max_len = MAX_LENGTHS[action_type]
    if len(new_value) > max_len:
        raise ValueError(f"draft_copy.py: new_value is {len(new_value)} characters, over the {max_len}-character limit for {action_type}")
    if new_value == previous_value:
        raise ValueError("draft_copy.py: new_value is identical to the current live value - nothing to propose")


def draft_copy(project, loop, proposal_id, new_value, drafted_by, repo_path=None, now=None):
    loop_dir = loop_dir_for(project, loop)
    pending_dir = pending_dir_for(project, loop)
    proposal_pathname = proposal_path(pending_dir, proposal_id)
    if not os.path.exists(proposal_pathname):
        raise ValueError(f"draft_copy.py: proposal {proposal_id} not found")

    lock_ttl_minutes = _read_lock_ttl_minutes_unsafe(os.path.join(loop_dir, "spec.md"))
    lock = acquire_lock(loop_dir, max_run_duration_minutes=lock_ttl_minutes, runs_dir=os.path.join(loop_dir, "runs"), now=now)
    if not lock["acquired"]:
        raise ValueError(f'draft_copy.py: REFUSED - run lock active for {project}/{loop} - {lock["reason"]}')

    try:
        proposal = load_json(proposal_pathname)
        is_revision = proposal.get("status") == "review-revision-needed"

        if proposal.get("status") not in ("draft", "review-revision-needed"):
            raise ValueError(
                f'draft_copy.py: proposal {proposal_id} has status "{proposal.get("status")}", not "draft" or '
                '"review-revision-needed" - only a fresh draft, or a proposal a Codex review round flagged for a '
                "correctable revision, may receive new implementation copy; drafting refuses to overwrite "
                "implementation data at any other point in the pipeline"
            )

        action_type = proposal.get("action_type")
        repo_path = repo_path or _load_repo_path(project)
        if not repo_path:
            raise ValueError(f"draft_copy.py: no repo path configured for project {project} (project.md frontmatter's `repo` field)")

        # Raises ValueError on a missing or ambiguous edit location -
        # propagated as-is, never silently guessed at.
        previous_value = read_current_value(repo_path, proposal["target"]["page"], action_type)

        validate_copy(action_type, new_value, previous_value)

        implementation_before = proposal.get("implementation")
        implementation_after = {
            "previous_value": previous_value,
            "new_value": new_value,
            "drafted_at": _now_iso(now),
            "drafted_by": drafted_by,
        }

        if not is_revision:
            proposal["implementation"] = implementation_after
            atomic_write_json(proposal_pathname, proposal)
            return proposal

        # Revision path (Phase 7 - PLAN-PHASE7-CODEX-REVIEW.md item 10):
        # event-sourced - the proposal_revised event IS the commit point,
        # carrying the full before/after implementation (not just a hash),
        # and the on-disk proposal is a projection synced from it afterward.
        spec = yaml.safe_load(extract_frontmatter(open(os.path.join(loop_dir, "spec.md"), "r", encoding="utf-8").read())) or {}
        secret_map = build_secret_map(repo_path, spec.get("credential_aliases"))
        append_event(
            loop_dir, "proposal_revised", project=project, loop=loop, proposal_id=proposal_id,
            action_type=action_type, target_page=proposal["target"]["page"], keyword=proposal.get("target", {}).get("keyword"),
            implementation_before=implementation_before, implementation_after=implementation_after,
            resulting_proposal_status="review-pending", secret_map=secret_map,
        )
        projected = sync_proposal_projection(loop_dir, proposal_id, fail_closed=False)
        if projected.get("status"):
            proposal["status"] = projected["status"]
        proposal["implementation"] = implementation_after
        proposal["review"] = projected["review"]
        atomic_write_json(proposal_pathname, proposal)
        return proposal
    finally:
        release_lock(loop_dir, lock["run_id"])


def _cli():
    args = sys.argv[1:]
    if len(args) < 4:
        print("usage: python tools/draft_copy.py <project> <loop> <proposal-id> <new_value> [--by <name>]", file=sys.stderr)
        sys.exit(2)
    project, loop, proposal_id, new_value = args[0], args[1], args[2], args[3]
    drafted_by = "claude"
    if "--by" in args:
        idx = args.index("--by")
        if idx + 1 < len(args):
            drafted_by = args[idx + 1]
    try:
        proposal = draft_copy(project, loop, proposal_id, new_value, drafted_by)
        implementation = proposal["implementation"]
        print(f'drafted {proposal["id"]}: "{implementation["previous_value"]}" -> "{implementation["new_value"]}"')
    except Exception as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)


def _self_test():
    import shutil
    import tempfile

    from lib.proposals import write_proposal

    # draft_copy() resolves proposal storage through pending_dir_for(project,
    # loop), which is always rooted at the real projects/ directory - so the
    # self-test uses a disposable project there (same convention as
    # tools/tests/phase1_exit_criteria.py's "_phase1-test-tmp"), not an
    # arbitrary temp dir.
    project = "_draft-copy-selftest-tmp"
    loop = "seo"
    project_dir = os.path.join(PROJECTS_ROOT, project)
    pending_dir = os.path.join(project_dir, "loops", loop, "pending")
    shutil.rmtree(project_dir, ignore_errors=True)
    os.makedirs(pending_dir, exist_ok=True)
    with open(os.path.join(project_dir, "loops", loop, "spec.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("---\nversion: 1\nloop: seo\n---\n")

    def _seed(proposal_id, action_type, page, status="draft"):
        write_proposal(pending_dir, {"id": proposal_id, "action_type": action_type, "target": {"page": page}, "status": status})

    repo_dir = tempfile.mkdtemp(prefix="draft-copy-repo-")
    checks = []
    try:
        os.makedirs(os.path.join(repo_dir, "templates"), exist_ok=True)
        with open(os.path.join(repo_dir, "templates", "services.html"), "w", encoding="utf-8", newline="\n") as f:
            f.write("{% block title %}Old service title{% endblock %}\n{% block meta_description %}Old service meta description{% endblock %}\n")
        os.makedirs(os.path.join(repo_dir, "templates", "region-a"), exist_ok=True)
        os.makedirs(os.path.join(repo_dir, "templates", "region-b"), exist_ok=True)
        with open(os.path.join(repo_dir, "templates", "region-a", "duplicate.html"), "w", encoding="utf-8", newline="\n") as f:
            f.write("{% block title %}Duplicate A{% endblock %}\n")
        with open(os.path.join(repo_dir, "templates", "region-b", "duplicate.html"), "w", encoding="utf-8", newline="\n") as f:
            f.write("{% block title %}Duplicate B{% endblock %}\n")

        _seed("prop-valid-title", "title-tag-rewrite", "/services/")
        result = draft_copy(project, loop, "prop-valid-title", "New Services Title", "claude-test", repo_path=repo_dir)
        checks.append(("valid title draft stores previous_value from the live template", result["implementation"]["previous_value"] == "Old service title"))
        checks.append(("valid title draft stores the new_value verbatim", result["implementation"]["new_value"] == "New Services Title"))
        checks.append(("valid title draft stamps drafted_at/drafted_by", bool(result["implementation"]["drafted_at"]) and result["implementation"]["drafted_by"] == "claude-test"))

        threw_too_long = False
        _seed("prop-too-long", "title-tag-rewrite", "/services/")
        try:
            draft_copy(project, loop, "prop-too-long", "x" * 61, "claude-test", repo_path=repo_dir)
        except ValueError as e:
            threw_too_long = "60-character limit" in str(e)
        checks.append(("title over 60 characters is rejected", threw_too_long))

        threw_meta_too_long = False
        _seed("prop-meta-too-long", "meta-description-rewrite", "/services/")
        try:
            draft_copy(project, loop, "prop-meta-too-long", "x" * 156, "claude-test", repo_path=repo_dir)
        except ValueError as e:
            threw_meta_too_long = "155-character limit" in str(e)
        checks.append(("meta description over 155 characters is rejected", threw_meta_too_long))

        threw_blank = False
        _seed("prop-blank", "title-tag-rewrite", "/services/")
        try:
            draft_copy(project, loop, "prop-blank", "   ", "claude-test", repo_path=repo_dir)
        except ValueError as e:
            threw_blank = "non-empty" in str(e)
        checks.append(("blank/whitespace-only new_value is rejected", threw_blank))

        threw_multiline = False
        _seed("prop-multiline", "title-tag-rewrite", "/services/")
        try:
            draft_copy(project, loop, "prop-multiline", "Line one\nLine two", "claude-test", repo_path=repo_dir)
        except ValueError as e:
            threw_multiline = "single line" in str(e)
        checks.append(("multiline new_value is rejected", threw_multiline))

        threw_unchanged = False
        _seed("prop-unchanged", "title-tag-rewrite", "/services/")
        try:
            draft_copy(project, loop, "prop-unchanged", "Old service title", "claude-test", repo_path=repo_dir)
        except ValueError as e:
            threw_unchanged = "identical" in str(e)
        checks.append(("new_value identical to previous_value is rejected", threw_unchanged))

        threw_ambiguous = False
        _seed("prop-ambiguous-location", "title-tag-rewrite", "/duplicate/")
        try:
            draft_copy(project, loop, "prop-ambiguous-location", "New Title", "claude-test", repo_path=repo_dir)
        except ValueError as e:
            threw_ambiguous = "multiple" in str(e)
        checks.append(("ambiguous edit location is rejected, not silently guessed", threw_ambiguous))

        threw_missing = False
        _seed("prop-missing-location", "title-tag-rewrite", "/no-such-page/")
        try:
            draft_copy(project, loop, "prop-missing-location", "New Title", "claude-test", repo_path=repo_dir)
        except ValueError as e:
            threw_missing = "no title edit location found" in str(e)
        checks.append(("unresolved edit location is rejected, not silently guessed", threw_missing))

        _seed("prop-revision", "title-tag-rewrite", "/services/", status="review-revision-needed")
        revised = draft_copy(project, loop, "prop-revision", "Revised Services Title", "claude-test", repo_path=repo_dir)
        checks.append(("a review-revision-needed proposal can be redrafted", revised["implementation"]["new_value"] == "Revised Services Title"))
        checks.append(("a redraft syncs status from the (empty, in this fixture) event log rather than crashing", "status" in revised))

        for status in ("reviewed", "approved", "implemented", "applied", "verified", "breached", "rejected", "implement-failed"):
            proposal_id = f"prop-past-draft-{status}"
            _seed(proposal_id, "title-tag-rewrite", "/services/", status=status)
            threw_past_draft = False
            try:
                draft_copy(project, loop, proposal_id, "New Title", "claude-test", repo_path=repo_dir)
            except ValueError as e:
                threw_past_draft = "not \"draft\"" in str(e)
            checks.append((f'drafting a proposal with status "{status}" is refused', threw_past_draft))
    finally:
        shutil.rmtree(repo_dir, ignore_errors=True)
        shutil.rmtree(project_dir, ignore_errors=True)

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    if "--verify" in sys.argv:
        _self_test()
    else:
        _cli()

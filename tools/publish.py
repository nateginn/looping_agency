# Phase 6 (PLAN.md) - the only component in this workspace permitted to
# touch a remote GitHub repository. Pushes a validated, already-implemented
# proposal's commit to a fully-qualified `seo/<id>` ref, opens a PR against
# `main`, and requests GitHub auto-merge so no second human action is
# required after the recorded approval. Never pushes/merges to main/master
# itself - branch protection plus GitHub's own required-review gate is the
# actual merge boundary (RISK-REGISTER.md R6/R10; PLAN.md "Where the
# security boundary actually is"). Every guard below is correctness/
# ergonomics defense-in-depth on top of that server-side gate, not a
# replacement for it - see PLAN.md Phase 6.
#
# Phase 6a (not built here, by design): deployment health checks and
# automatic rollback/revert-PR behavior after a merge lands.
import os
import re
import sys
from datetime import datetime, timezone

import yaml

try:
    from .lib.artwebsite_seo import (
        _default_git_runner,
        proposal_branch_name,
        verify_commit_blob_contains,
        verify_commit_single_file_diff,
        verify_deploy_workflow_trigger_shape,
        verify_implementation_ancestry,
    )
    from .lib.credentials import resolve_credential
    from .lib.github_compare import create_pull_request, enable_auto_merge, verify_branch_protection
    from .lib.lock import acquire_lock, release_lock
    from .lib.proposals import load_json, loop_dir_for, pending_dir_for, proposal_path, atomic_write_json
    from .run_loop import LIVE_COMPARE_TARGETS, _read_lock_ttl_minutes_unsafe
    from .spec_validate import extract_frontmatter
except ImportError:
    from lib.artwebsite_seo import (
        _default_git_runner,
        proposal_branch_name,
        verify_commit_blob_contains,
        verify_commit_single_file_diff,
        verify_deploy_workflow_trigger_shape,
        verify_implementation_ancestry,
    )
    from lib.credentials import resolve_credential
    from lib.github_compare import create_pull_request, enable_auto_merge, verify_branch_protection
    from lib.lock import acquire_lock, release_lock
    from lib.proposals import load_json, loop_dir_for, pending_dir_for, proposal_path, atomic_write_json
    from run_loop import LIVE_COMPARE_TARGETS, _read_lock_ttl_minutes_unsafe
    from spec_validate import extract_frontmatter

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(THIS_DIR)
PROJECTS_ROOT = os.path.join(WORKSPACE_ROOT, "projects")

# Codex #4: proposal IDs are generated as prop-{run_id}-{i}, timestamp +
# random suffix + index - never attacker-supplied - but the check is nearly
# free and closes the door on a malformed/tampered ID reaching a git ref.
PROPOSAL_ID_RE = re.compile(r"^prop-[0-9T:.Z-]+-[a-z0-9]+-\d+$")
FORBIDDEN_ID_SUBSTRINGS = (":", "?", "*", "~", "^", " ", "\t", "\n", "refs/")

FORBIDDEN_DESTINATION_BRANCHES = {"main", "master"}

APPROVAL_ACTIONS = {"approve", "retry"}  # review_pending.py transitions that record an explicit human approval


def _now_iso(now=None):
    now = now or datetime.now(timezone.utc)
    return now.isoformat().replace("+00:00", "Z")


def _load_frontmatter(path):
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    return yaml.safe_load(extract_frontmatter(source)) or {}


def _load_repo_path(project):
    project_path = os.path.join(PROJECTS_ROOT, project, "project.md")
    return _load_frontmatter(project_path).get("repo")


def _write_proposal_state(path, proposal):
    atomic_write_json(path, proposal)


def validate_proposal_id(proposal_id):
    if not isinstance(proposal_id, str) or not PROPOSAL_ID_RE.match(proposal_id):
        raise ValueError(f"REFUSED: proposal id {proposal_id!r} does not match the expected format")
    for bad in FORBIDDEN_ID_SUBSTRINGS:
        if bad in proposal_id:
            raise ValueError(f"REFUSED: proposal id {proposal_id!r} contains a disallowed substring {bad!r}")
    return True


def _extract_approval_record(proposal):
    """The approval record required by PLAN.md's autonomy revision - bound
    to proposal ID, target, previous/new value, implementation SHA,
    approver, and timestamp. review_pending.py already writes exactly this
    onto `decision` when a human runs --approve or --retry (a re-approval
    after a fixed implement-failure); nothing new is stored, this only
    validates what is already there and refuses to publish without it."""
    decision = proposal.get("decision") or {}
    if decision.get("action") not in APPROVAL_ACTIONS:
        raise ValueError(
            f'REFUSED: proposal {proposal.get("id")} has no recorded human approval '
            f'(decision.action={decision.get("action")!r}, expected one of {sorted(APPROVAL_ACTIONS)})'
        )
    approver = decision.get("by")
    approved_at = decision.get("at")
    implementation = proposal.get("implementation") or {}
    target = proposal.get("target") or {}
    record = {
        "proposal_id": proposal.get("id"),
        "target_page": target.get("page"),
        "target_keyword": target.get("keyword"),
        "previous_value": implementation.get("previous_value"),
        "new_value": implementation.get("new_value"),
        "implementation_commit_sha": proposal.get("implemented_commit_sha"),
        "approver": approver,
        "approved_at": approved_at,
    }
    missing = [k for k, v in record.items() if k != "target_keyword" and k != "previous_value" and not v]
    if missing:
        raise ValueError(f"REFUSED: proposal {proposal.get('id')}'s approval record is missing required field(s): {missing}")
    return record


def _normalize_remote_url(url):
    u = (url or "").strip()
    if u.endswith(".git"):
        u = u[:-4]
    if u.endswith("/"):
        u = u[:-1]
    return u.lower()


def _expected_remote_urls(owner, repo):
    base = f"{owner}/{repo}".lower()
    return {
        f"https://github.com/{base}",
        f"http://github.com/{base}",
        f"git@github.com:{base}",
        f"ssh://git@github.com/{base}",
    }


def _resolve_github_target(project, github_owner, github_repo):
    if github_owner and github_repo:
        return github_owner, github_repo
    default_target = LIVE_COMPARE_TARGETS.get(project)
    if default_target:
        return default_target["owner"], default_target["repo"]
    raise ValueError(
        f"REFUSED: no GitHub owner/repo configured for project {project} - pass github_owner/github_repo explicitly "
        "or add an entry to run_loop.py's LIVE_COMPARE_TARGETS"
    )


def _pr_title(proposal):
    return f'SEO: {proposal["action_type"]} on {proposal["target"]["page"]} (proposal {proposal["id"]})'


def _pr_body(proposal, approval_record, changed_path, base_sha):
    lines = [
        f'Proposal: {proposal["id"]}',
        f'Approved by: {approval_record["approver"]} at {approval_record["approved_at"]}',
        f'Implementation commit: {proposal["implemented_commit_sha"]}',
        f'Base / rollback target: {base_sha}',
        f'Changed file: {changed_path}',
        f'Previous value: {approval_record["previous_value"]!r}',
        f'New value: {approval_record["new_value"]!r}',
        "",
        "Validated by tools/publish.py before push: commit ancestry against the recorded base SHA, "
        "single-file diff read from the commit's own objects, blob content contains the approved new_value, "
        "branch protection on main (PR + >=1 review + no force-push/deletion + no bypass actors), "
        "deploy.yml trigger shape at the base commit, and the push destination remote URL.",
        "",
        "Opened automatically by Loop Agency after recorded human approval. Auto-merge has been requested - "
        "no manual merge action should be required unless a required check fails.",
    ]
    return "\n".join(lines)


def publish_proposal(
    project,
    loop,
    proposal_id,
    *,
    repo_path=None,
    github_owner=None,
    github_repo=None,
    github_token_alias=None,
    token=None,
    git_runner=None,
    requester=None,
    poster=None,
    merge_method="SQUASH",
    now=None,
):
    loop_dir = loop_dir_for(project, loop)
    pending_dir = pending_dir_for(project, loop)
    proposal_pathname = proposal_path(pending_dir, proposal_id)
    if not os.path.exists(proposal_pathname):
        raise ValueError(f"proposal {proposal_id} not found")

    validate_proposal_id(proposal_id)

    # Phase 3: the one shared mutation lock - same as run_loop.py/apply.py/
    # review_pending.py/draft_copy.py, so a scheduled run can no longer race
    # a publish, and a concurrent publish attempt on the same proposal
    # blocks rather than clobbers.
    lock_ttl_minutes = _read_lock_ttl_minutes_unsafe(os.path.join(loop_dir, "spec.md"))
    lock = acquire_lock(loop_dir, max_run_duration_minutes=lock_ttl_minutes, runs_dir=os.path.join(loop_dir, "runs"), now=now)
    if not lock["acquired"]:
        raise ValueError(f'REFUSED: run lock active for {project}/{loop} - {lock["reason"]}')

    git_runner = git_runner or _default_git_runner

    try:
        proposal = load_json(proposal_pathname)

        if proposal.get("id") != proposal_id:
            raise ValueError(f"REFUSED: proposal file id {proposal.get('id')!r} does not match requested id {proposal_id!r}")
        if proposal.get("tier") == 2:
            raise ValueError(f"REFUSED: proposal {proposal_id} is Tier 2 (public/paid) - always human-only, never automated by publish.py")
        if proposal.get("manual_approval_only") is True:
            raise ValueError(f"REFUSED: proposal {proposal_id} is marked manual_approval_only: true - publish.py will not push/PR it")
        if proposal.get("pr_number") is not None:
            raise ValueError(f'REFUSED: proposal {proposal_id} was already published (PR #{proposal["pr_number"]}, {proposal.get("pr_url")})')
        if proposal.get("status") != "implemented":
            raise ValueError(f'REFUSED: proposal {proposal_id} has status "{proposal.get("status")}", not "implemented" - apply it first')

        implemented_branch = proposal.get("implemented_branch")
        implemented_commit_sha = proposal.get("implemented_commit_sha")
        base_sha = proposal.get("implementation_base_sha")
        if not implemented_branch or not implemented_commit_sha or not base_sha:
            raise ValueError(
                f"REFUSED: proposal {proposal_id} is missing implemented_branch/implemented_commit_sha/implementation_base_sha - "
                "cannot publish an incompletely-recorded implementation"
            )

        # Checked ahead of the expected-branch-name comparison below so this
        # hardcoded refusal is independently reachable defense-in-depth,
        # not merely implied by proposal_branch_name() never returning
        # main/master today.
        if implemented_branch.strip().lower() in FORBIDDEN_DESTINATION_BRANCHES:
            raise ValueError(f"REFUSED: publish destination resolves to {implemented_branch!r} - refusing to push to main/master")

        expected_branch = proposal_branch_name(proposal_id)
        if implemented_branch != expected_branch:
            raise ValueError(
                f"REFUSED: proposal {proposal_id}'s implemented_branch {implemented_branch!r} does not match the "
                f"expected branch name {expected_branch!r}"
            )

        approval_record = _extract_approval_record(proposal)

        repo_path = repo_path or _load_repo_path(project)
        if not repo_path:
            raise ValueError(f"REFUSED: no repo path configured for project {project} (project.md frontmatter's `repo` field)")

        github_owner, github_repo = _resolve_github_target(project, github_owner, github_repo)

        # Re-verify ancestry even though apply.py already checked it at
        # implement time - time has passed, and this is the step that is
        # about to make the commit public.
        verify_implementation_ancestry(repo_path, implemented_commit_sha, base_sha, git_runner=git_runner)

        changed_path = verify_commit_single_file_diff(repo_path, implemented_commit_sha, base_sha, git_runner=git_runner)
        verify_commit_blob_contains(repo_path, implemented_commit_sha, changed_path, approval_record["new_value"], git_runner=git_runner)

        # TOCTOU close: re-read the local branch tip immediately before
        # pushing, rather than trusting the value recorded on the proposal.
        try:
            current_head = git_runner(["git", "rev-parse", f"refs/heads/{implemented_branch}"], cwd=repo_path)
        except Exception as err:
            raise ValueError(f"REFUSED: could not resolve refs/heads/{implemented_branch} locally - {err}")
        if current_head != implemented_commit_sha:
            raise ValueError(
                f"REFUSED: refs/heads/{implemented_branch} now points at {current_head}, not the recorded "
                f"implemented_commit_sha {implemented_commit_sha} - refusing to push a moved branch"
            )

        verify_deploy_workflow_trigger_shape(repo_path, base_sha, git_runner=git_runner)

        try:
            push_url = git_runner(["git", "remote", "get-url", "--push", "origin"], cwd=repo_path)
        except Exception as err:
            raise ValueError(f"REFUSED: could not resolve origin's push URL - {err}")
        if _normalize_remote_url(push_url) not in _expected_remote_urls(github_owner, github_repo):
            raise ValueError(
                f"REFUSED: origin's push URL ({push_url}) does not match the expected destination "
                f"github.com/{github_owner}/{github_repo} - refusing (possible pushurl/insteadOf rewrite)"
            )

        token = token or resolve_credential(
            github_token_alias or f"{project}-github-token",
            project_dir=os.path.join(PROJECTS_ROOT, project),
        )

        verify_branch_protection(github_owner, github_repo, "main", token, requester=requester)

        try:
            git_runner(["git", "push", "origin", f"{implemented_commit_sha}:refs/heads/{implemented_branch}"], cwd=repo_path)
        except Exception as err:
            raise ValueError(f"REFUSED: git push to origin failed - {err}")

        proposal["pushed_branch"] = implemented_branch
        proposal["pushed_at"] = _now_iso(now)
        _write_proposal_state(proposal_pathname, proposal)

        try:
            pr = create_pull_request(
                github_owner,
                github_repo,
                implemented_branch,
                "main",
                _pr_title(proposal),
                _pr_body(proposal, approval_record, changed_path, base_sha),
                token,
                poster=poster,
            )
        except Exception as err:
            proposal["publish_error"] = f"push succeeded but PR creation failed: {err}"
            _write_proposal_state(proposal_pathname, proposal)
            raise ValueError(proposal["publish_error"])

        proposal["pr_number"] = pr["number"]
        proposal["pr_url"] = pr["html_url"]
        proposal["pr_node_id"] = pr.get("node_id")
        proposal["pr_opened_at"] = _now_iso(now)
        proposal.pop("publish_error", None)
        _write_proposal_state(proposal_pathname, proposal)

        try:
            if not pr.get("node_id"):
                raise ValueError("PR response is missing node_id - cannot request auto-merge")
            enable_auto_merge(pr["node_id"], token, merge_method=merge_method, poster=poster)
            proposal["auto_merge_enabled"] = True
            proposal["auto_merge_requested_at"] = _now_iso(now)
            proposal.pop("auto_merge_error", None)
        except Exception as err:
            # Non-fatal: the branch is pushed and the PR is real and
            # reviewable even if requesting auto-merge failed - surfaced on
            # the proposal (never silently) rather than unwinding a
            # publish that has already partly succeeded.
            proposal["auto_merge_enabled"] = False
            proposal["auto_merge_error"] = str(err)
        _write_proposal_state(proposal_pathname, proposal)

        return proposal
    finally:
        release_lock(loop_dir, lock["run_id"])


def _cli():
    args = sys.argv[1:]
    if len(args) < 3:
        print("usage: python tools/publish.py <project> <loop> <proposal-id> [--owner O] [--repo R] [--token-alias A]", file=sys.stderr)
        sys.exit(2)
    project, loop, proposal_id = args[0], args[1], args[2]
    owner = repo = token_alias = None
    if "--owner" in args:
        owner = args[args.index("--owner") + 1]
    if "--repo" in args:
        repo = args[args.index("--repo") + 1]
    if "--token-alias" in args:
        token_alias = args[args.index("--token-alias") + 1]
    try:
        p = publish_proposal(project, loop, proposal_id, github_owner=owner, github_repo=repo, github_token_alias=token_alias)
        print(
            f'published {p["id"]}: PR #{p.get("pr_number")} {p.get("pr_url")} '
            f'(auto_merge_enabled={p.get("auto_merge_enabled")})'
        )
        if p.get("auto_merge_error"):
            print(f'WARNING: auto-merge request failed: {p["auto_merge_error"]}', file=sys.stderr)
    except Exception as err:
        print(str(err), file=sys.stderr)
        sys.exit(1)


def _self_test():
    import shutil

    from lib.proposals import write_proposal

    project = "_publish-selftest-tmp"
    loop = "seo"
    project_dir = os.path.join(PROJECTS_ROOT, project)
    pending_dir = os.path.join(project_dir, "loops", loop, "pending")
    shutil.rmtree(project_dir, ignore_errors=True)
    os.makedirs(pending_dir, exist_ok=True)
    with open(os.path.join(project_dir, "project.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("---\nslug: _publish-selftest-tmp\nrepo: D:\\Temp\\unused\n---\n# fixture\n")

    OWNER, REPO = "nateginn", "artwebsite"
    GOOD_DEPLOY_YAML = "on:\n  push:\n    branches: [ main ]\n"
    GOOD_PROTECTION_PAYLOAD = {
        "required_pull_request_reviews": {"required_approving_review_count": 1, "bypass_pull_request_allowances": {}},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "enforce_admins": {"enabled": True},
    }
    # Phase 0 protection verification is now two mechanisms combined (classic
    # protection + rulesets, github_compare.verify_branch_protection()) -
    # every fake requester used below must serve all three GET endpoints it
    # queries: /rules/branches/{branch} (effective rules), /rulesets (list),
    # and /branches/{branch}/protection (classic). An empty rulesets list is
    # a legitimate "protection is fully classic" configuration.
    GOOD_EFFECTIVE_RULES = [
        {"type": "pull_request", "parameters": {"required_approving_review_count": 1}},
        {"type": "non_fast_forward", "parameters": {}},
        {"type": "deletion", "parameters": {}},
    ]

    def _fake_runner(responses, calls=None):
        def runner(args, cwd):
            if calls is not None:
                calls.append(list(args))
            for matcher, result in responses:
                if matcher(args):
                    if isinstance(result, Exception):
                        raise result
                    return result
            raise AssertionError(f"unexpected git command in publish.py self-test fake runner: {args}")

        return runner

    def _no_git_allowed():
        return _fake_runner([])

    def _good_requester(url, headers):
        import json as _json

        if "/rules/branches/" in url:
            return 200, "OK", _json.dumps(GOOD_EFFECTIVE_RULES).encode("utf-8")
        if url.endswith("/rulesets"):
            return 200, "OK", b"[]"
        return 200, "OK", _json.dumps(GOOD_PROTECTION_PAYLOAD).encode("utf-8")

    def _good_poster(url, headers, body):
        import json as _json

        if url.endswith("/pulls"):
            return 201, "Created", _json.dumps(
                {"number": 7, "html_url": "https://github.com/nateginn/artwebsite/pull/7", "node_id": "PR_kwtest", "head": {"sha": "sha-child"}}
            ).encode("utf-8")
        if url.endswith("/graphql"):
            return 200, "OK", _json.dumps(
                {"data": {"enablePullRequestAutoMerge": {"pullRequest": {"autoMergeRequest": {"enabledAt": "2026-07-30T00:00:00Z"}}}}}
            ).encode("utf-8")
        raise AssertionError(f"unexpected poster URL in publish.py self-test: {url}")

    def _base_proposal(proposal_id="prop-2026-07-24T03-05-54-719Z-9noq9s-0", **overrides):
        branch = overrides.pop("implemented_branch", proposal_branch_name(proposal_id))
        p = {
            "id": proposal_id,
            "loop": loop,
            "action_type": "title-tag-rewrite",
            "tier": 1,
            "target": {"page": "/services/", "keyword": "service keyword"},
            "manual_approval_only": False,
            "status": "implemented",
            "implemented_branch": branch,
            "implemented_commit_sha": "sha-child",
            "implementation_base_sha": "sha-base",
            "implementation": {"previous_value": "Old Services Title", "new_value": "New Services Title"},
            "decision": {"action": "approve", "by": "nate", "at": "2026-07-29T12:00:00Z", "note": ""},
        }
        p.update(overrides)
        return p

    def _happy_path_runner(calls=None):
        branch = proposal_branch_name("prop-2026-07-24T03-05-54-719Z-9noq9s-0")
        return _fake_runner(
            [
                (lambda a: a == ["git", "rev-parse", "sha-child^"], "sha-base"),
                (lambda a: a[:2] == ["git", "diff-tree"], "templates/services.html\n"),
                (lambda a: a[:2] == ["git", "show"] and a[2] == "sha-child:templates/services.html", "{% block title %}New Services Title{% endblock %}\n"),
                (lambda a: a == ["git", "rev-parse", f"refs/heads/{branch}"], "sha-child"),
                (lambda a: a[:2] == ["git", "show"] and a[2] == "sha-base:.github/workflows/deploy.yml", GOOD_DEPLOY_YAML),
                (lambda a: a == ["git", "remote", "get-url", "--push", "origin"], "https://github.com/nateginn/artwebsite.git"),
                (lambda a: a[:2] == ["git", "push"], ""),
            ],
            calls=calls,
        )

    checks = []

    def _seed(proposal):
        write_proposal(pending_dir, proposal)

    # -- validators, tested directly --
    checks.append(("validate_proposal_id accepts a real generated id", validate_proposal_id("prop-2026-07-24T03-05-54-719Z-9noq9s-0") is True))
    for bad_id in ("prop-has space-0", "prop-colon:here-0", "prop-refs/heads/main-0", "not-even-prop-0", "prop-star*-0"):
        refused = False
        try:
            validate_proposal_id(bad_id)
        except ValueError:
            refused = True
        checks.append((f"validate_proposal_id rejects {bad_id!r}", refused))

    # -- happy path: push, PR, auto-merge all succeed --
    _seed(_base_proposal())
    result = publish_proposal(
        project, loop, "prop-2026-07-24T03-05-54-719Z-9noq9s-0",
        repo_path="/fake/repo", github_owner=OWNER, github_repo=REPO, token="fake-token",
        git_runner=_happy_path_runner(), requester=_good_requester, poster=_good_poster,
    )
    checks.append(("happy path returns pr_number/pr_url", result["pr_number"] == 7 and result["pr_url"] == "https://github.com/nateginn/artwebsite/pull/7"))
    checks.append(("happy path records pushed_branch", result["pushed_branch"] == proposal_branch_name("prop-2026-07-24T03-05-54-719Z-9noq9s-0")))
    checks.append(("happy path requests and confirms auto-merge", result["auto_merge_enabled"] is True and "auto_merge_error" not in result))
    stored = load_json(proposal_path(pending_dir, "prop-2026-07-24T03-05-54-719Z-9noq9s-0"))
    checks.append(("happy path persists pr_number/pr_url on disk", stored.get("pr_number") == 7))

    # -- already published --
    already_refused = False
    try:
        publish_proposal(
            project, loop, "prop-2026-07-24T03-05-54-719Z-9noq9s-0",
            repo_path="/fake/repo", github_owner=OWNER, github_repo=REPO, token="fake-token",
            git_runner=_no_git_allowed(), requester=_good_requester, poster=_good_poster,
        )
    except ValueError as e:
        already_refused = "already published" in str(e)
    checks.append(("republishing an already-published proposal is refused without touching git", already_refused))

    # -- tier 2 (never touched by tooling) --
    _seed(_base_proposal("prop-2026-07-24T03-05-54-719Z-tiertwo-1", tier=2))
    tier2_refused = False
    try:
        publish_proposal(project, loop, "prop-2026-07-24T03-05-54-719Z-tiertwo-1", repo_path="/fake/repo", github_owner=OWNER, github_repo=REPO, token="t", git_runner=_no_git_allowed())
    except ValueError as e:
        tier2_refused = "Tier 2" in str(e)
    checks.append(("Tier 2 proposal is refused without touching git", tier2_refused))

    # -- manual_approval_only --
    _seed(_base_proposal("prop-2026-07-24T03-05-54-719Z-manualx-2", manual_approval_only=True))
    manual_refused = False
    try:
        publish_proposal(project, loop, "prop-2026-07-24T03-05-54-719Z-manualx-2", repo_path="/fake/repo", github_owner=OWNER, github_repo=REPO, token="t", git_runner=_no_git_allowed())
    except ValueError as e:
        manual_refused = "manual_approval_only" in str(e)
    checks.append(("manual_approval_only proposal is refused without touching git", manual_refused))

    # -- wrong status --
    _seed(_base_proposal("prop-2026-07-24T03-05-54-719Z-statusx-3", status="approved"))
    status_refused = False
    try:
        publish_proposal(project, loop, "prop-2026-07-24T03-05-54-719Z-statusx-3", repo_path="/fake/repo", github_owner=OWNER, github_repo=REPO, token="t", git_runner=_no_git_allowed())
    except ValueError as e:
        status_refused = 'not "implemented"' in str(e)
    checks.append(('proposal not yet "implemented" is refused', status_refused))

    # -- missing implementation fields --
    _seed(_base_proposal("prop-2026-07-24T03-05-54-719Z-missingx-4", implementation_base_sha=None))
    missing_refused = False
    try:
        publish_proposal(project, loop, "prop-2026-07-24T03-05-54-719Z-missingx-4", repo_path="/fake/repo", github_owner=OWNER, github_repo=REPO, token="t", git_runner=_no_git_allowed())
    except ValueError as e:
        missing_refused = "incompletely-recorded" in str(e)
    checks.append(("missing implementation_base_sha is refused", missing_refused))

    # -- branch name mismatch --
    _seed(_base_proposal("prop-2026-07-24T03-05-54-719Z-branchmis-5", implemented_branch="seo/some-other-id"))
    branch_mismatch_refused = False
    try:
        publish_proposal(project, loop, "prop-2026-07-24T03-05-54-719Z-branchmis-5", repo_path="/fake/repo", github_owner=OWNER, github_repo=REPO, token="t", git_runner=_no_git_allowed())
    except ValueError as e:
        branch_mismatch_refused = "does not match the expected branch name" in str(e)
    checks.append(("implemented_branch not matching the proposal id is refused", branch_mismatch_refused))

    # -- forbidden destination branch --
    _seed(_base_proposal("prop-2026-07-24T03-05-54-719Z-maindest-6", implemented_branch="main"))
    main_dest_refused = False
    try:
        publish_proposal(project, loop, "prop-2026-07-24T03-05-54-719Z-maindest-6", repo_path="/fake/repo", github_owner=OWNER, github_repo=REPO, token="t", git_runner=_no_git_allowed())
    except ValueError as e:
        main_dest_refused = "refusing to push to main/master" in str(e)
    checks.append(("a resolved destination of main/master is hard-refused", main_dest_refused))

    # -- no approval record --
    _seed(_base_proposal("prop-2026-07-24T03-05-54-719Z-noapprove-7", decision=None))
    no_approval_refused = False
    try:
        publish_proposal(project, loop, "prop-2026-07-24T03-05-54-719Z-noapprove-7", repo_path="/fake/repo", github_owner=OWNER, github_repo=REPO, token="t", git_runner=_no_git_allowed())
    except ValueError as e:
        no_approval_refused = "no recorded human approval" in str(e)
    checks.append(("a proposal with no recorded approval decision is refused", no_approval_refused))

    rejected_decision = {"action": "reject", "by": "nate", "at": "2026-07-29T12:00:00Z"}
    _seed(_base_proposal("prop-2026-07-24T03-05-54-719Z-rejected-8", decision=rejected_decision))
    rejected_decision_refused = False
    try:
        publish_proposal(project, loop, "prop-2026-07-24T03-05-54-719Z-rejected-8", repo_path="/fake/repo", github_owner=OWNER, github_repo=REPO, token="t", git_runner=_no_git_allowed())
    except ValueError as e:
        rejected_decision_refused = "no recorded human approval" in str(e)
    checks.append(("a proposal whose last decision was reject (not approve/retry) is refused", rejected_decision_refused))

    # -- ancestry mismatch --
    _seed(_base_proposal("prop-2026-07-24T03-05-54-719Z-ancestry-9"))
    ancestry_runner = _fake_runner([(lambda a: a == ["git", "rev-parse", "sha-child^"], "sha-not-base")])
    ancestry_refused = False
    try:
        publish_proposal(project, loop, "prop-2026-07-24T03-05-54-719Z-ancestry-9", repo_path="/fake/repo", github_owner=OWNER, github_repo=REPO, token="t", git_runner=ancestry_runner)
    except ValueError as e:
        ancestry_refused = "inconsistent commit ancestry" in str(e)
    checks.append(("inconsistent commit ancestry is refused before any push", ancestry_refused))

    # -- multi-file diff --
    _seed(_base_proposal("prop-2026-07-24T03-05-54-719Z-multifile-10"))
    multifile_runner = _fake_runner(
        [
            (lambda a: a == ["git", "rev-parse", "sha-child^"], "sha-base"),
            (lambda a: a[:2] == ["git", "diff-tree"], "templates/services.html\napp/views.py\n"),
        ]
    )
    multifile_refused = False
    try:
        publish_proposal(project, loop, "prop-2026-07-24T03-05-54-719Z-multifile-10", repo_path="/fake/repo", github_owner=OWNER, github_repo=REPO, token="t", git_runner=multifile_runner)
    except ValueError as e:
        multifile_refused = "changes 2 file(s)" in str(e)
    checks.append(("a commit touching more than one file is refused before any push", multifile_refused))

    # -- blob does not contain approved new_value --
    _seed(_base_proposal("prop-2026-07-24T03-05-54-719Z-blobmis-11"))
    blob_mismatch_runner = _fake_runner(
        [
            (lambda a: a == ["git", "rev-parse", "sha-child^"], "sha-base"),
            (lambda a: a[:2] == ["git", "diff-tree"], "templates/services.html\n"),
            (lambda a: a[:2] == ["git", "show"] and a[2] == "sha-child:templates/services.html", "{% block title %}Something unrelated{% endblock %}\n"),
        ]
    )
    blob_mismatch_refused = False
    try:
        publish_proposal(project, loop, "prop-2026-07-24T03-05-54-719Z-blobmis-11", repo_path="/fake/repo", github_owner=OWNER, github_repo=REPO, token="t", git_runner=blob_mismatch_runner)
    except ValueError as e:
        blob_mismatch_refused = "does not contain the approved new_value" in str(e)
    checks.append(("a commit whose blob does not contain the approved new_value is refused before any push", blob_mismatch_refused))

    # -- branch moved since implement (TOCTOU) --
    _seed(_base_proposal("prop-2026-07-24T03-05-54-719Z-branchmv-12"))
    branch = proposal_branch_name("prop-2026-07-24T03-05-54-719Z-branchmv-12")
    branch_moved_runner = _fake_runner(
        [
            (lambda a: a == ["git", "rev-parse", "sha-child^"], "sha-base"),
            (lambda a: a[:2] == ["git", "diff-tree"], "templates/services.html\n"),
            (lambda a: a[:2] == ["git", "show"] and a[2] == "sha-child:templates/services.html", "{% block title %}New Services Title{% endblock %}\n"),
            (lambda a: a == ["git", "rev-parse", f"refs/heads/{branch}"], "sha-moved-elsewhere"),
        ]
    )
    branch_moved_refused = False
    try:
        publish_proposal(project, loop, "prop-2026-07-24T03-05-54-719Z-branchmv-12", repo_path="/fake/repo", github_owner=OWNER, github_repo=REPO, token="t", git_runner=branch_moved_runner)
    except ValueError as e:
        branch_moved_refused = "refusing to push a moved branch" in str(e)
    checks.append(("a branch that moved since implement is refused (TOCTOU close)", branch_moved_refused))

    # -- bad deploy.yml trigger shape --
    _seed(_base_proposal("prop-2026-07-24T03-05-54-719Z-deployshp-13"))
    branch2 = proposal_branch_name("prop-2026-07-24T03-05-54-719Z-deployshp-13")
    bad_deploy_runner = _fake_runner(
        [
            (lambda a: a == ["git", "rev-parse", "sha-child^"], "sha-base"),
            (lambda a: a[:2] == ["git", "diff-tree"], "templates/services.html\n"),
            (lambda a: a[:2] == ["git", "show"] and a[2] == "sha-child:templates/services.html", "{% block title %}New Services Title{% endblock %}\n"),
            (lambda a: a == ["git", "rev-parse", f"refs/heads/{branch2}"], "sha-child"),
            (lambda a: a[:2] == ["git", "show"] and a[2] == "sha-base:.github/workflows/deploy.yml", "on:\n  push:\n    branches: [ main, staging ]\n"),
        ]
    )
    bad_deploy_refused = False
    try:
        publish_proposal(project, loop, "prop-2026-07-24T03-05-54-719Z-deployshp-13", repo_path="/fake/repo", github_owner=OWNER, github_repo=REPO, token="t", git_runner=bad_deploy_runner)
    except ValueError as e:
        bad_deploy_refused = "branches is" in str(e)
    checks.append(("a deploy.yml with a non-main-only trigger shape is refused before any push", bad_deploy_refused))

    # -- remote URL mismatch (possible pushurl/insteadOf rewrite) --
    _seed(_base_proposal("prop-2026-07-24T03-05-54-719Z-remotemis-14"))
    branch3 = proposal_branch_name("prop-2026-07-24T03-05-54-719Z-remotemis-14")
    remote_mismatch_runner = _fake_runner(
        [
            (lambda a: a == ["git", "rev-parse", "sha-child^"], "sha-base"),
            (lambda a: a[:2] == ["git", "diff-tree"], "templates/services.html\n"),
            (lambda a: a[:2] == ["git", "show"] and a[2] == "sha-child:templates/services.html", "{% block title %}New Services Title{% endblock %}\n"),
            (lambda a: a == ["git", "rev-parse", f"refs/heads/{branch3}"], "sha-child"),
            (lambda a: a[:2] == ["git", "show"] and a[2] == "sha-base:.github/workflows/deploy.yml", GOOD_DEPLOY_YAML),
            (lambda a: a == ["git", "remote", "get-url", "--push", "origin"], "https://github.com/some-other-owner/some-other-repo.git"),
        ]
    )
    remote_mismatch_refused = False
    try:
        publish_proposal(project, loop, "prop-2026-07-24T03-05-54-719Z-remotemis-14", repo_path="/fake/repo", github_owner=OWNER, github_repo=REPO, token="t", git_runner=remote_mismatch_runner)
    except ValueError as e:
        remote_mismatch_refused = "possible pushurl/insteadOf rewrite" in str(e)
    checks.append(("a push destination remote not matching the expected owner/repo is refused before any push", remote_mismatch_refused))

    # -- branch protection missing/insufficient --
    _seed(_base_proposal("prop-2026-07-24T03-05-54-719Z-noprotect-15"))

    def _unprotected_requester(url, headers):
        # Genuinely no protection: the effective-rules endpoint (the merge
        # of classic + rulesets) reports nothing enforced at all - an empty
        # array, not the good fixture - so Gate A correctly refuses instead
        # of the earlier classic-only check's plain 404 path.
        if "/rules/branches/" in url:
            return 200, "OK", b"[]"
        if url.endswith("/rulesets"):
            return 200, "OK", b"[]"
        return 404, "Not Found", b"{}"

    no_protection_runner = _happy_path_runner()
    no_protection_refused = False
    try:
        publish_proposal(
            project, loop, "prop-2026-07-24T03-05-54-719Z-noprotect-15",
            repo_path="/fake/repo", github_owner=OWNER, github_repo=REPO, token="t",
            git_runner=_fake_runner(
                [
                    (lambda a: a == ["git", "rev-parse", "sha-child^"], "sha-base"),
                    (lambda a: a[:2] == ["git", "diff-tree"], "templates/services.html\n"),
                    (lambda a: a[:2] == ["git", "show"] and a[2] == "sha-child:templates/services.html", "{% block title %}New Services Title{% endblock %}\n"),
                    (lambda a: a == ["git", "rev-parse", f"refs/heads/{proposal_branch_name('prop-2026-07-24T03-05-54-719Z-noprotect-15')}"], "sha-child"),
                    (lambda a: a[:2] == ["git", "show"] and a[2] == "sha-base:.github/workflows/deploy.yml", GOOD_DEPLOY_YAML),
                    (lambda a: a == ["git", "remote", "get-url", "--push", "origin"], "https://github.com/nateginn/artwebsite.git"),
                ]
            ),
            requester=_unprotected_requester,
        )
    except ValueError as e:
        no_protection_refused = "no effective pull_request rule found" in str(e)
    checks.append(("an unprotected main branch is hard-refused before any push", no_protection_refused))

    # -- auto-merge request failing is non-fatal (push + PR already succeeded) --
    _seed(_base_proposal("prop-2026-07-24T03-05-54-719Z-automerge-16"))
    branch4 = proposal_branch_name("prop-2026-07-24T03-05-54-719Z-automerge-16")

    def _failing_automerge_poster(url, headers, body):
        import json as _json

        if url.endswith("/pulls"):
            return 201, "Created", _json.dumps(
                {"number": 8, "html_url": "https://github.com/nateginn/artwebsite/pull/8", "node_id": "PR_kwtest2", "head": {"sha": "sha-child"}}
            ).encode("utf-8")
        return 200, "OK", _json.dumps({"data": None, "errors": [{"message": "Auto-merge is not allowed for this repository"}]}).encode("utf-8")

    automerge_fail_runner = _fake_runner(
        [
            (lambda a: a == ["git", "rev-parse", "sha-child^"], "sha-base"),
            (lambda a: a[:2] == ["git", "diff-tree"], "templates/services.html\n"),
            (lambda a: a[:2] == ["git", "show"] and a[2] == "sha-child:templates/services.html", "{% block title %}New Services Title{% endblock %}\n"),
            (lambda a: a == ["git", "rev-parse", f"refs/heads/{branch4}"], "sha-child"),
            (lambda a: a[:2] == ["git", "show"] and a[2] == "sha-base:.github/workflows/deploy.yml", GOOD_DEPLOY_YAML),
            (lambda a: a == ["git", "remote", "get-url", "--push", "origin"], "https://github.com/nateginn/artwebsite.git"),
            (lambda a: a[:2] == ["git", "push"], ""),
        ]
    )
    automerge_result = publish_proposal(
        project, loop, "prop-2026-07-24T03-05-54-719Z-automerge-16",
        repo_path="/fake/repo", github_owner=OWNER, github_repo=REPO, token="t",
        git_runner=automerge_fail_runner, requester=_good_requester, poster=_failing_automerge_poster,
    )
    checks.append(("a PR is still opened even when the auto-merge request fails", automerge_result.get("pr_number") == 8))
    checks.append(("auto-merge failure is recorded, not silent", automerge_result.get("auto_merge_enabled") is False and "errors" in automerge_result.get("auto_merge_error", "")))
    checks.append(("publish_proposal does not raise when only the auto-merge request fails", True))  # reaching here proves it

    # -- PR creation failing after a successful push records partial state and refuses --
    _seed(_base_proposal("prop-2026-07-24T03-05-54-719Z-prfail-17"))
    branch5 = proposal_branch_name("prop-2026-07-24T03-05-54-719Z-prfail-17")

    def _failing_pr_poster(url, headers, body):
        import json as _json

        return 422, "Unprocessable Entity", _json.dumps({"message": "Validation failed"}).encode("utf-8")

    pr_fail_runner = _fake_runner(
        [
            (lambda a: a == ["git", "rev-parse", "sha-child^"], "sha-base"),
            (lambda a: a[:2] == ["git", "diff-tree"], "templates/services.html\n"),
            (lambda a: a[:2] == ["git", "show"] and a[2] == "sha-child:templates/services.html", "{% block title %}New Services Title{% endblock %}\n"),
            (lambda a: a == ["git", "rev-parse", f"refs/heads/{branch5}"], "sha-child"),
            (lambda a: a[:2] == ["git", "show"] and a[2] == "sha-base:.github/workflows/deploy.yml", GOOD_DEPLOY_YAML),
            (lambda a: a == ["git", "remote", "get-url", "--push", "origin"], "https://github.com/nateginn/artwebsite.git"),
            (lambda a: a[:2] == ["git", "push"], ""),
        ]
    )
    pr_fail_refused = False
    try:
        publish_proposal(
            project, loop, "prop-2026-07-24T03-05-54-719Z-prfail-17",
            repo_path="/fake/repo", github_owner=OWNER, github_repo=REPO, token="t",
            git_runner=pr_fail_runner, requester=_good_requester, poster=_failing_pr_poster,
        )
    except ValueError as e:
        pr_fail_refused = "push succeeded but PR creation failed" in str(e)
    stored_partial = load_json(proposal_path(pending_dir, "prop-2026-07-24T03-05-54-719Z-prfail-17"))
    checks.append(("a PR-creation failure after a successful push is surfaced, not swallowed", pr_fail_refused))
    checks.append(("a PR-creation failure still persists pushed_branch (partial, visible state)", stored_partial.get("pushed_branch") == branch5))
    checks.append(("a PR-creation failure records publish_error on the proposal", "PR creation failed" in stored_partial.get("publish_error", "")))

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

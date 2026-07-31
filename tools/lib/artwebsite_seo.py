import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlsplit

import yaml


TITLE_BLOCK_RE = re.compile(r"({%\s*block\s+title\s*%})(.*?)(({%\s*endblock\s*%}))", re.DOTALL)
META_BLOCK_RE = re.compile(r"({%\s*block\s+meta_description\s*%})(.*?)(({%\s*endblock\s*%}))", re.DOTALL)


def _now_iso(now=None):
    now = now or datetime.now(timezone.utc)
    return now.isoformat().replace("+00:00", "Z")


def _slug_from_page(page):
    path = urlsplit(page).path if isinstance(page, str) else page
    path = path or "/"
    normalized = path.strip("/")
    return "home" if normalized == "" else normalized.split("/")[-1]


def _candidate_names(page):
    slug = _slug_from_page(page)
    if slug == "home":
        return ["home", "index"]
    variants = {slug, slug.replace("-", "_"), slug.replace("-", "")}
    return sorted(variants)


def _walk_files(repo_path, suffix):
    for root, _dirs, files in os.walk(repo_path):
        for name in files:
            if name.endswith(suffix):
                yield os.path.join(root, name)


def _template_block_matches(repo_path, page, block_name):
    matches = []
    candidates = set(_candidate_names(page))
    block_re = TITLE_BLOCK_RE if block_name == "title" else META_BLOCK_RE
    for path in _walk_files(repo_path, ".html"):
        base = os.path.splitext(os.path.basename(path))[0]
        if base not in candidates:
            continue
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        if block_re.search(source):
            matches.append(path)
    return matches


def _render_calls(source):
    render_re = re.compile(r"render\(\s*request\s*,\s*['\"](?P<template>[^'\"]+)['\"]\s*,\s*\{(?P<context>.*?)\}\s*\)", re.DOTALL)
    return list(render_re.finditer(source))


def _views_matches(repo_path, page, key_name):
    matches = []
    candidates = set(_candidate_names(page))
    key_re = re.compile(rf"([\"']){re.escape(key_name)}\1\s*:\s*([\"'])(?P<value>.*?)\2", re.DOTALL)
    for path in _walk_files(repo_path, "views.py"):
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        for render_match in _render_calls(source):
            template_name = os.path.splitext(os.path.basename(render_match.group("template")))[0]
            if template_name not in candidates:
                continue
            context = render_match.group("context")
            value_match = key_re.search(context)
            if value_match:
                absolute_start = render_match.start("context") + value_match.start("value")
                absolute_end = render_match.start("context") + value_match.end("value")
                matches.append({"path": path, "start": absolute_start, "end": absolute_end})
    return matches


def resolve_edit_location(repo_path, page, action_type):
    if action_type == "title-tag-rewrite":
        template_matches = _template_block_matches(repo_path, page, "title")
        if len(template_matches) == 1:
            return {"kind": "template-block", "path": template_matches[0], "block": "title"}
        if len(template_matches) > 1:
            raise ValueError(f"multiple title block matches found for {page}")
        view_matches = _views_matches(repo_path, page, "title")
        if len(view_matches) == 1:
            return {"kind": "views-context", "path": view_matches[0]["path"], "start": view_matches[0]["start"], "end": view_matches[0]["end"]}
        if len(view_matches) > 1:
            raise ValueError(f"multiple views.py title matches found for {page}")
        raise ValueError(f"no title edit location found for {page}")

    if action_type == "meta-description-rewrite":
        template_matches = _template_block_matches(repo_path, page, "meta_description")
        if len(template_matches) == 1:
            return {"kind": "template-block", "path": template_matches[0], "block": "meta_description"}
        if len(template_matches) > 1:
            raise ValueError(f"multiple meta_description block matches found for {page}")
        view_matches = _views_matches(repo_path, page, "meta_description")
        if len(view_matches) == 1:
            return {"kind": "views-context", "path": view_matches[0]["path"], "start": view_matches[0]["start"], "end": view_matches[0]["end"]}
        if len(view_matches) > 1:
            raise ValueError(f"multiple views.py meta_description matches found for {page}")
        raise ValueError(f"no meta_description edit location found for {page}")

    raise ValueError(f"unsupported action_type for auto-implement: {action_type}")


def _read_block_value(path, block_name):
    block_re = TITLE_BLOCK_RE if block_name == "title" else META_BLOCK_RE
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    match = block_re.search(source)
    if not match:
        raise ValueError(f"expected a {block_name} block in {path}")
    return match.group(2).strip()


def _read_views_value(path, start, end):
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    return source[start:end]


def _read_value_at_location(location):
    if location["kind"] == "template-block":
        return _read_block_value(location["path"], location["block"])
    return _read_views_value(location["path"], location["start"], location["end"])


def read_current_value(repo_path, page, action_type):
    """Read the live title/meta text at a proposal's target, reusing the same
    edit-location resolution apply_rewrite() uses - raises ValueError on a
    missing or ambiguous location rather than guessing."""
    location = resolve_edit_location(repo_path, page, action_type)
    return _read_value_at_location(location)


def _rewrite_template_block(path, block_name, new_value):
    block_re = TITLE_BLOCK_RE if block_name == "title" else META_BLOCK_RE
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    rewritten, count = block_re.subn(rf"\1{new_value}\3", source, count=1)
    if count != 1:
        raise ValueError(f"expected exactly one {block_name} block in {path}")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(rewritten)


def _rewrite_views_context(path, start, end, new_value):
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    rewritten = source[:start] + new_value + source[end:]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(rewritten)


def apply_rewrite(repo_path, proposal):
    rewrite = proposal.get("implementation") or {}
    new_value = rewrite.get("new_value")
    if not isinstance(new_value, str) or new_value.strip() == "":
        raise ValueError(
            f'proposal {proposal["id"]} is approved for {proposal["action_type"]} but has no implementation.new_value text to write'
        )
    location = resolve_edit_location(repo_path, proposal["target"]["page"], proposal["action_type"])

    previous_value = rewrite.get("previous_value")
    if previous_value is not None:
        current_value = _read_value_at_location(location)
        if current_value != previous_value:
            raise ValueError(
                f'proposal {proposal["id"]} refuses to apply: the source content for {proposal["action_type"]} on '
                f'{proposal["target"]["page"]} no longer matches the approved previous_value '
                f'(expected {previous_value!r}, found {current_value!r}) - the page changed since this proposal was drafted'
            )

    if location["kind"] == "template-block":
        _rewrite_template_block(location["path"], location["block"], new_value)
    else:
        _rewrite_views_context(location["path"], location["start"], location["end"], new_value)
    return location


def _default_git_runner(args, cwd):
    completed = subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def proposal_branch_name(proposal_id):
    return f"seo/{proposal_id}"


def worktree_path_for(parent_dir, proposal_id):
    return os.path.join(parent_dir, f"seo-{proposal_id}")


def verify_main_baseline(repo_path, git_runner=None):
    """Fetch origin and confirm the local refs/heads/main matches
    refs/remotes/origin/main before anything branches off it - refuses on
    drift (a human pushed to origin, or pulled locally, since the last
    fetch) rather than silently implementing against a stale or diverged
    base. Always uses fully-qualified refs, never a bare `main`, so a stray
    tag or symbolic ref named `main` cannot resolve to something else."""
    git_runner = git_runner or _default_git_runner
    try:
        git_runner(["git", "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main"], cwd=repo_path)
    except Exception as err:
        raise ValueError(f"REFUSED: could not fetch origin to verify the main baseline - {err}")

    try:
        local_main = git_runner(["git", "rev-parse", "refs/heads/main"], cwd=repo_path)
    except Exception as err:
        raise ValueError(f"REFUSED: refs/heads/main does not resolve locally - {err}")

    try:
        remote_main = git_runner(["git", "rev-parse", "refs/remotes/origin/main"], cwd=repo_path)
    except Exception as err:
        raise ValueError(f"REFUSED: refs/remotes/origin/main does not resolve after fetch - {err}")

    if local_main != remote_main:
        raise ValueError(
            f"REFUSED: baseline drift - local refs/heads/main ({local_main}) does not match "
            f"refs/remotes/origin/main ({remote_main}) after fetch. Pull/fast-forward main before applying."
        )
    return local_main


def verify_implementation_ancestry(repo_path, commit_sha, expected_base_sha, git_runner=None):
    """Confirm an implementation commit's parent is exactly the recorded
    base SHA - the check a later publish step needs to prove the commit it
    is about to push is actually based on the source it was drafted/applied
    against, not a rebased or otherwise altered one."""
    git_runner = git_runner or _default_git_runner
    try:
        parent = git_runner(["git", "rev-parse", f"{commit_sha}^"], cwd=repo_path)
    except Exception as err:
        raise ValueError(f"REFUSED: could not resolve the parent of implementation commit {commit_sha} - {err}")

    if parent != expected_base_sha:
        raise ValueError(
            f"REFUSED: inconsistent commit ancestry - implementation commit {commit_sha} has parent {parent}, "
            f"which does not match the recorded implementation_base_sha ({expected_base_sha})"
        )
    return True


def verify_commit_single_file_diff(repo_path, commit_sha, base_sha, git_runner=None):
    """Phase 6 (Codex #9): validate the diff from the commit's own objects,
    not the working tree - a mutable checkout is not proof of what a commit
    actually contains. Uses an explicit two-tree `git diff-tree` (base_sha
    vs commit_sha) rather than relying on the commit's implicit first
    parent, and refuses unless exactly one file changed."""
    git_runner = git_runner or _default_git_runner
    try:
        output = git_runner(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", base_sha, commit_sha], cwd=repo_path)
    except Exception as err:
        raise ValueError(f"REFUSED: could not diff commit {commit_sha} against base {base_sha} - {err}")
    changed_paths = [line.strip() for line in output.splitlines() if line.strip()]
    if len(changed_paths) != 1:
        raise ValueError(
            f"REFUSED: commit {commit_sha} changes {len(changed_paths)} file(s) relative to base {base_sha} "
            f"(expected exactly 1): {changed_paths}"
        )
    return changed_paths[0]


def verify_commit_blob_contains(repo_path, commit_sha, path, expected_substring, git_runner=None):
    """Read the changed file's content as committed at commit_sha (via `git
    show <sha>:<path>`, a blob read - never the mutable working tree) and
    confirm the approved new_value text is actually present in it."""
    git_runner = git_runner or _default_git_runner
    try:
        content = git_runner(["git", "show", f"{commit_sha}:{path}"], cwd=repo_path)
    except Exception as err:
        raise ValueError(f"REFUSED: could not read blob {path} at commit {commit_sha} - {err}")
    if expected_substring not in content:
        raise ValueError(
            f"REFUSED: commit {commit_sha}'s blob {path} does not contain the approved new_value text - "
            "the commit does not appear to implement the approved proposal"
        )
    return True


def verify_deploy_workflow_trigger_shape(repo_path, base_sha, git_runner=None):
    """Phase 6 stale-baseline check (Codex R1 #1, scoped precisely per
    Codex R2 #11): confirm .github/workflows/deploy.yml, as committed at the
    proposal's base commit, still triggers on exactly `push` to exactly
    `main` - nothing else. This only proves the shape at the commit being
    published from; it cannot bind what happens to the workflow between
    publication and merge (that is governed by required review on the
    protected branch, not by this check). PyYAML's default (YAML 1.1)
    resolver reads a bare `on:` key as the boolean True, not the string
    "on" - both are checked."""
    git_runner = git_runner or _default_git_runner
    try:
        content = git_runner(["git", "show", f"{base_sha}:.github/workflows/deploy.yml"], cwd=repo_path)
    except Exception as err:
        raise ValueError(f"REFUSED: could not read .github/workflows/deploy.yml at base commit {base_sha} - {err}")
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as err:
        raise ValueError(f"REFUSED: .github/workflows/deploy.yml at base commit {base_sha} is not valid YAML - {err}")
    if not isinstance(parsed, dict):
        raise ValueError(f"REFUSED: .github/workflows/deploy.yml at base commit {base_sha} does not parse to a mapping")

    on_value = parsed.get("on", parsed.get(True))
    if not isinstance(on_value, dict) or set(on_value.keys()) != {"push"}:
        keys = sorted(str(k) for k in on_value.keys()) if isinstance(on_value, dict) else on_value
        raise ValueError(
            f"REFUSED: .github/workflows/deploy.yml at base commit {base_sha} has unexpected top-level triggers {keys!r} (expected only 'push')"
        )
    push_value = on_value.get("push")
    if not isinstance(push_value, dict) or set(push_value.keys()) != {"branches"}:
        raise ValueError(
            f"REFUSED: .github/workflows/deploy.yml at base commit {base_sha} push trigger has an unexpected shape {push_value!r} (expected only 'branches')"
        )
    branches = push_value.get("branches")
    if branches != ["main"]:
        raise ValueError(
            f"REFUSED: .github/workflows/deploy.yml at base commit {base_sha} push trigger branches is {branches!r}, expected ['main'] exactly"
        )
    return True


def make_attempt(repo_path, proposal_id, git_runner=None, started_at=None):
    git_runner = git_runner or _default_git_runner
    branch = proposal_branch_name(proposal_id)
    # Verify the baseline before creating anything on disk - a drifted
    # baseline or failed fetch must not leak a temp worktree directory that
    # nothing will ever clean up (nobody holds an attempt dict to pass to
    # cleanup_worktree() for one that never got returned).
    base_commit = verify_main_baseline(repo_path, git_runner=git_runner)
    parent_dir = tempfile.mkdtemp(prefix="looping-artwebsite-")
    worktree_path = worktree_path_for(parent_dir, proposal_id)
    return {
        "started_at": started_at or _now_iso(),
        "branch": branch,
        "base_commit": base_commit,
        "worktree_path": worktree_path,
        "worktree_parent": parent_dir,
    }


def create_worktree(repo_path, attempt, git_runner=None):
    git_runner = git_runner or _default_git_runner
    worktree_path = attempt["worktree_path"]
    branch = attempt["branch"]
    # Branch from the exact SHA verify_main_baseline() already confirmed
    # matches refs/remotes/origin/main, rather than re-resolving `main`
    # (bare or qualified) a second time - closes the TOCTOU gap between
    # verification and worktree creation.
    base_commit = attempt["base_commit"]
    git_runner(["git", "worktree", "add", worktree_path, "-b", branch, base_commit], cwd=repo_path)
    return attempt


def finalize_worktree(repo_path, proposal, attempt, git_runner=None, now=None):
    git_runner = git_runner or _default_git_runner
    apply_rewrite(attempt["worktree_path"], proposal)
    git_runner(["git", "add", "-A"], cwd=attempt["worktree_path"])
    git_runner(
        ["git", "commit", "-m", f'SEO auto-implement {proposal["id"]}: {proposal["action_type"]} {proposal["target"]["page"]}'],
        cwd=attempt["worktree_path"],
    )
    head = git_runner(["git", "rev-parse", "HEAD"], cwd=attempt["worktree_path"])
    cleanup_worktree(repo_path, attempt, git_runner=git_runner, delete_branch=False)
    return {
        "implemented_branch": attempt["branch"],
        "implemented_commit_sha": head,
        "implemented_at": _now_iso(now),
    }


def inspect_attempt(repo_path, attempt, git_runner=None):
    git_runner = git_runner or _default_git_runner
    branch = attempt["branch"]
    base_commit = attempt["base_commit"]
    try:
        head = git_runner(["git", "rev-parse", f"refs/heads/{branch}"], cwd=repo_path)
    except Exception:
        return {"state": "missing-branch"}
    if head == base_commit:
        return {"state": "no-commit", "head": head}
    return {"state": "commit-exists", "head": head}


def cleanup_worktree(repo_path, attempt, git_runner=None, delete_branch=True):
    git_runner = git_runner or _default_git_runner
    worktree_path = attempt.get("worktree_path")
    if worktree_path and os.path.exists(worktree_path):
        git_runner(["git", "worktree", "remove", worktree_path, "--force"], cwd=repo_path)
    branch = attempt.get("branch")
    if branch and delete_branch:
        try:
            git_runner(["git", "branch", "-D", branch], cwd=repo_path)
        except Exception:
            pass
    parent_dir = attempt.get("worktree_parent") or os.path.dirname(worktree_path or "")
    if parent_dir and os.path.isdir(parent_dir):
        shutil.rmtree(parent_dir, ignore_errors=True)


def _self_test():
    repo_dir = tempfile.mkdtemp(prefix="artwebsite-implementer-")
    try:
        os.makedirs(os.path.join(repo_dir, "templates"), exist_ok=True)
        os.makedirs(os.path.join(repo_dir, "app"), exist_ok=True)
        with open(os.path.join(repo_dir, "templates", "services.html"), "w", encoding="utf-8", newline="\n") as f:
            f.write("{% block title %}Old{% endblock %}\n{% block meta_description %}Old meta{% endblock %}\n")
        with open(os.path.join(repo_dir, "app", "views.py"), "w", encoding="utf-8", newline="\n") as f:
            f.write(
                "def home(request):\n"
                "    return render(request, 'home.html', {\n"
                "        'title': 'Old title',\n"
                "        'meta_description': 'Old desc',\n"
                "    })\n"
            )
        checks = [
            ("template title resolves", resolve_edit_location(repo_dir, "/services/", "title-tag-rewrite")["kind"] == "template-block"),
            ("views home meta resolves", resolve_edit_location(repo_dir, "/", "meta-description-rewrite")["kind"] == "views-context"),
            ("read_current_value reads a template block", read_current_value(repo_dir, "/services/", "title-tag-rewrite") == "Old"),
            ("read_current_value reads a views-context string", read_current_value(repo_dir, "/", "meta-description-rewrite") == "Old desc"),
        ]

        stale_proposal = {
            "id": "prop-stale-test",
            "action_type": "title-tag-rewrite",
            "target": {"page": "/services/"},
            "implementation": {"previous_value": "This is not the real current value", "new_value": "New title"},
        }
        stale_refused = False
        try:
            apply_rewrite(repo_dir, stale_proposal)
        except ValueError as e:
            stale_refused = "no longer matches the approved previous_value" in str(e)
        checks.append(("apply_rewrite refuses when previous_value no longer matches the source", stale_refused))

        fresh_proposal = {
            "id": "prop-fresh-test",
            "action_type": "title-tag-rewrite",
            "target": {"page": "/services/"},
            "implementation": {"previous_value": "Old", "new_value": "New title"},
        }
        apply_rewrite(repo_dir, fresh_proposal)
        checks.append(("apply_rewrite proceeds when previous_value matches the source", read_current_value(repo_dir, "/services/", "title-tag-rewrite") == "New title"))

        no_previous_value_proposal = {
            "id": "prop-no-previous-value-test",
            "action_type": "meta-description-rewrite",
            "target": {"page": "/"},
            "implementation": {"new_value": "New description"},
        }
        apply_rewrite(repo_dir, no_previous_value_proposal)
        checks.append(("apply_rewrite skips the staleness check for legacy proposals with no previous_value", read_current_value(repo_dir, "/", "meta-description-rewrite") == "New description"))
    finally:
        shutil.rmtree(repo_dir, ignore_errors=True)

    def _fake_runner(responses):
        def runner(args, cwd):
            for matcher, result in responses:
                if matcher(args):
                    if isinstance(result, Exception):
                        raise result
                    return result
            raise AssertionError(f"unexpected git command in fake runner: {args}")
        return runner

    matching_runner = _fake_runner(
        [
            (lambda a: a[:2] == ["git", "fetch"], ""),
            (lambda a: a == ["git", "rev-parse", "refs/heads/main"], "abc123"),
            (lambda a: a == ["git", "rev-parse", "refs/remotes/origin/main"], "abc123"),
        ]
    )
    checks.append(("verify_main_baseline returns the shared SHA when refs/heads/main matches refs/remotes/origin/main", verify_main_baseline("/fake/repo", git_runner=matching_runner) == "abc123"))

    drifted_runner = _fake_runner(
        [
            (lambda a: a[:2] == ["git", "fetch"], ""),
            (lambda a: a == ["git", "rev-parse", "refs/heads/main"], "local111"),
            (lambda a: a == ["git", "rev-parse", "refs/remotes/origin/main"], "remote222"),
        ]
    )
    drift_refused = False
    try:
        verify_main_baseline("/fake/repo", git_runner=drifted_runner)
    except ValueError as e:
        drift_refused = "baseline drift" in str(e) and "local111" in str(e) and "remote222" in str(e)
    checks.append(("verify_main_baseline refuses on baseline drift between refs/heads/main and refs/remotes/origin/main", drift_refused))

    fetch_failed_runner = _fake_runner([(lambda a: a[:2] == ["git", "fetch"], RuntimeError("network unreachable"))])
    fetch_refused = False
    try:
        verify_main_baseline("/fake/repo", git_runner=fetch_failed_runner)
    except ValueError as e:
        fetch_refused = "could not fetch origin" in str(e)
    checks.append(("verify_main_baseline refuses safely when fetching origin fails", fetch_refused))

    ancestry_matching_runner = _fake_runner([(lambda a: a[-1] == "sha-child^", "sha-base")])
    ancestry_ok = False
    try:
        ancestry_ok = verify_implementation_ancestry("/fake/repo", "sha-child", "sha-base", git_runner=ancestry_matching_runner) is True
    except ValueError:
        pass
    checks.append(("verify_implementation_ancestry accepts a commit whose parent matches the recorded base SHA", ancestry_ok))

    ancestry_mismatch_runner = _fake_runner([(lambda a: a[-1] == "sha-child^", "sha-unexpected-parent")])
    ancestry_refused = False
    try:
        verify_implementation_ancestry("/fake/repo", "sha-child", "sha-base", git_runner=ancestry_mismatch_runner)
    except ValueError as e:
        ancestry_refused = "inconsistent commit ancestry" in str(e)
    checks.append(("verify_implementation_ancestry refuses on inconsistent commit ancestry", ancestry_refused))

    # verify_commit_single_file_diff (Phase 6, Codex #9)
    single_file_runner = _fake_runner([(lambda a: a[:2] == ["git", "diff-tree"], "templates/services.html\n")])
    checks.append(
        (
            "verify_commit_single_file_diff returns the sole changed path",
            verify_commit_single_file_diff("/fake/repo", "sha-child", "sha-base", git_runner=single_file_runner) == "templates/services.html",
        )
    )

    multi_file_runner = _fake_runner([(lambda a: a[:2] == ["git", "diff-tree"], "templates/services.html\napp/views.py\n")])
    multi_file_refused = False
    try:
        verify_commit_single_file_diff("/fake/repo", "sha-child", "sha-base", git_runner=multi_file_runner)
    except ValueError as e:
        multi_file_refused = "changes 2 file(s)" in str(e)
    checks.append(("verify_commit_single_file_diff refuses when more than one file changed", multi_file_refused))

    zero_file_runner = _fake_runner([(lambda a: a[:2] == ["git", "diff-tree"], "")])
    zero_file_refused = False
    try:
        verify_commit_single_file_diff("/fake/repo", "sha-child", "sha-base", git_runner=zero_file_runner)
    except ValueError as e:
        zero_file_refused = "changes 0 file(s)" in str(e)
    checks.append(("verify_commit_single_file_diff refuses when no file changed", zero_file_refused))

    diff_tree_error_runner = _fake_runner([(lambda a: a[:2] == ["git", "diff-tree"], RuntimeError("bad object"))])
    diff_tree_error_refused = False
    try:
        verify_commit_single_file_diff("/fake/repo", "sha-child", "sha-base", git_runner=diff_tree_error_runner)
    except ValueError as e:
        diff_tree_error_refused = "could not diff commit" in str(e)
    checks.append(("verify_commit_single_file_diff refuses cleanly when the diff-tree command fails", diff_tree_error_refused))

    # verify_commit_blob_contains
    blob_runner = _fake_runner([(lambda a: a[:2] == ["git", "show"], "{% block title %}New Services Title{% endblock %}\n")])
    checks.append(
        (
            "verify_commit_blob_contains accepts a blob containing the approved new_value",
            verify_commit_blob_contains("/fake/repo", "sha-child", "templates/services.html", "New Services Title", git_runner=blob_runner) is True,
        )
    )

    blob_missing_runner = _fake_runner([(lambda a: a[:2] == ["git", "show"], "{% block title %}Something else entirely{% endblock %}\n")])
    blob_missing_refused = False
    try:
        verify_commit_blob_contains("/fake/repo", "sha-child", "templates/services.html", "New Services Title", git_runner=blob_missing_runner)
    except ValueError as e:
        blob_missing_refused = "does not contain the approved new_value" in str(e)
    checks.append(("verify_commit_blob_contains refuses when the blob does not contain the approved new_value", blob_missing_refused))

    blob_read_error_runner = _fake_runner([(lambda a: a[:2] == ["git", "show"], RuntimeError("path not in commit"))])
    blob_read_error_refused = False
    try:
        verify_commit_blob_contains("/fake/repo", "sha-child", "templates/services.html", "New Services Title", git_runner=blob_read_error_runner)
    except ValueError as e:
        blob_read_error_refused = "could not read blob" in str(e)
    checks.append(("verify_commit_blob_contains refuses cleanly when the blob cannot be read", blob_read_error_refused))

    # verify_deploy_workflow_trigger_shape
    good_deploy_yaml = "name: Deploy to Production\n\non:\n  push:\n    branches: [ main ]\n\njobs:\n  deploy:\n    runs-on: ubuntu-latest\n"
    good_deploy_runner = _fake_runner([(lambda a: a[:2] == ["git", "show"], good_deploy_yaml)])
    checks.append(
        (
            "verify_deploy_workflow_trigger_shape accepts the exact push-to-main-only shape (bare `on:` parses as YAML 1.1 boolean True)",
            verify_deploy_workflow_trigger_shape("/fake/repo", "sha-base", git_runner=good_deploy_runner) is True,
        )
    )

    extra_branch_yaml = "on:\n  push:\n    branches: [ main, staging ]\n"
    extra_branch_runner = _fake_runner([(lambda a: a[:2] == ["git", "show"], extra_branch_yaml)])
    extra_branch_refused = False
    try:
        verify_deploy_workflow_trigger_shape("/fake/repo", "sha-base", git_runner=extra_branch_runner)
    except ValueError as e:
        extra_branch_refused = "branches is" in str(e)
    checks.append(("verify_deploy_workflow_trigger_shape refuses when an extra branch is triggered", extra_branch_refused))

    extra_trigger_yaml = "on:\n  push:\n    branches: [ main ]\n  pull_request: {}\n"
    extra_trigger_runner = _fake_runner([(lambda a: a[:2] == ["git", "show"], extra_trigger_yaml)])
    extra_trigger_refused = False
    try:
        verify_deploy_workflow_trigger_shape("/fake/repo", "sha-base", git_runner=extra_trigger_runner)
    except ValueError as e:
        extra_trigger_refused = "unexpected top-level triggers" in str(e)
    checks.append(("verify_deploy_workflow_trigger_shape refuses when an extra trigger (e.g. pull_request) is present", extra_trigger_refused))

    bad_yaml_runner = _fake_runner([(lambda a: a[:2] == ["git", "show"], "on: [this is: not valid")])
    bad_yaml_refused = False
    try:
        verify_deploy_workflow_trigger_shape("/fake/repo", "sha-base", git_runner=bad_yaml_runner)
    except ValueError as e:
        bad_yaml_refused = "not valid YAML" in str(e)
    checks.append(("verify_deploy_workflow_trigger_shape refuses cleanly on unparsable YAML", bad_yaml_refused))

    missing_workflow_runner = _fake_runner([(lambda a: a[:2] == ["git", "show"], RuntimeError("path does not exist"))])
    missing_workflow_refused = False
    try:
        verify_deploy_workflow_trigger_shape("/fake/repo", "sha-base", git_runner=missing_workflow_runner)
    except ValueError as e:
        missing_workflow_refused = "could not read .github/workflows/deploy.yml" in str(e)
    checks.append(("verify_deploy_workflow_trigger_shape refuses cleanly when the workflow file is missing", missing_workflow_refused))

    leak_glob = os.path.join(tempfile.gettempdir(), "looping-artwebsite-*")
    before_leak_check = set(glob.glob(leak_glob))
    fetch_failed_for_attempt = _fake_runner([(lambda a: a[:2] == ["git", "fetch"], RuntimeError("network unreachable"))])
    attempt_refused = False
    try:
        make_attempt("/fake/repo", "prop-leak-test", git_runner=fetch_failed_for_attempt)
    except ValueError:
        attempt_refused = True
    after_leak_check = set(glob.glob(leak_glob))
    checks.append(("make_attempt refuses when verify_main_baseline fails", attempt_refused))
    checks.append(("make_attempt does not leak a temp worktree directory when verify_main_baseline fails", after_leak_check == before_leak_check))

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__" and "--verify" in sys.argv:
    _self_test()

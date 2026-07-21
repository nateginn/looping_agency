import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlsplit


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


def make_attempt(repo_path, proposal_id, git_runner=None, started_at=None):
    git_runner = git_runner or _default_git_runner
    branch = proposal_branch_name(proposal_id)
    parent_dir = tempfile.mkdtemp(prefix="looping-artwebsite-")
    worktree_path = worktree_path_for(parent_dir, proposal_id)
    base_commit = git_runner(["git", "rev-parse", "main"], cwd=repo_path)
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
    git_runner(["git", "worktree", "add", worktree_path, "-b", branch, "main"], cwd=repo_path)
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
        ]
    finally:
        shutil.rmtree(repo_dir, ignore_errors=True)

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__" and "--verify" in sys.argv:
    _self_test()

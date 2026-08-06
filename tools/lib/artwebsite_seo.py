import ast
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

# Directories that are never this project's own editable source, but which do
# contain .html/views.py files that collide with real template basenames.
# Without this, resolve_edit_location() for "/" (candidates home/index) matched
# Django's and jazzmin's admin_doc/index.html inside venv/ - two matches, so it
# raised "multiple title block matches" rather than editing one of them. That
# error was luck, not a guard: a single match would have resolved to a
# site-packages file and apply_rewrite() would have rewritten it. venv/ is
# untracked so it never reaches an apply worktree, but drafting reads the main
# checkout, so draft and apply resolved against differently-shaped trees.
# node_modules IS tracked here (~6k files, including @tailwindcss/forms/
# index.html) and does reach a worktree; staticfiles/ is collectstatic output,
# where an edit silently does nothing.
EXCLUDED_DIR_NAMES = frozenset(
    {".git", "venv", ".venv", "env", "node_modules", "site-packages", "__pycache__", "staticfiles", ".tox"}
)

# The context keys this Django app actually uses, in precedence order. base.html
# renders `{% block title %}{{ meta_title|default:"..." }}{% endblock %}`, so a
# view's title lives under `meta_title`, not `title` - looking only for "title"
# (as this module originally did) never matched anything in the real repo.
VIEW_CONTEXT_KEYS = {
    "title": ("meta_title", "title"),
    "meta_description": ("meta_description",),
}


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
    for root, dirs, files in os.walk(repo_path):
        # Prune in place so os.walk never descends - this is both the
        # correctness guard (see EXCLUDED_DIR_NAMES) and what keeps a resolve
        # from walking ~6k vendored files on every call.
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIR_NAMES]
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


# `render(request, "x.html", <arg>)` where <arg> is either an inline dict
# literal or a bare name bound to one earlier in the view.
RENDER_CALL_RE = re.compile(
    r"render\(\s*request\s*,\s*['\"](?P<template>[^'\"]+)['\"]\s*,\s*(?P<arg>\{|[A-Za-z_][A-Za-z0-9_]*)",
    re.DOTALL,
)


def _dict_body_span(source, brace_index):
    """Offsets of a dict literal's body, given the index of its opening brace.

    Brace-counting rather than a regex because the original
    `\\{(?P<context>.*?)\\}` stopped at the first `}` - fine for a flat dict,
    wrong for any nested one. String- and comment-aware so a brace inside a
    copy string cannot unbalance the count."""
    depth = 0
    i = brace_index
    n = len(source)
    while i < n:
        ch = source[i]
        if ch in "\"'":
            quote = next((q for q in ('"""', "'''") if source.startswith(q, i)), ch)
            i += len(quote)
            while i < n:
                if source[i] == "\\":
                    i += 2
                    continue
                if source.startswith(quote, i):
                    i += len(quote)
                    break
                i += 1
            continue
        if ch == "#":
            newline = source.find("\n", i)
            i = n if newline == -1 else newline
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return brace_index + 1, i
        i += 1
    raise ValueError("unbalanced braces in a render() context dict")


def _context_spans(source):
    """(template_name, body_start, body_end) for every render() call, resolving
    a context passed by name back to its assignment.

    Every view in this repo is written `context = {...}` / `ctx = {...}` then
    `render(request, "t.html", context)`, so the inline-dict-only match this
    module started with resolved nothing at all against the real source."""
    spans = []
    for call in RENDER_CALL_RE.finditer(source):
        template_name = os.path.splitext(os.path.basename(call.group("template")))[0]
        arg = call.group("arg")
        if arg == "{":
            brace_index = call.start("arg")
        else:
            # Nearest *preceding* `name = {` - i.e. the binding in this same
            # function. Views in this file all reuse the name `context`, so
            # scanning forward, or taking the first match, would pick up a
            # different view's dict entirely.
            assign_re = re.compile(rf"^[ \t]*{re.escape(arg)}\s*=\s*\{{", re.MULTILINE)
            prior = [m for m in assign_re.finditer(source) if m.end() <= call.start()]
            if not prior:
                continue
            brace_index = prior[-1].end() - 1
        try:
            body_start, body_end = _dict_body_span(source, brace_index)
        except ValueError:
            continue
        spans.append((template_name, body_start, body_end))
    return spans


def _string_value_re(key_name):
    # The value group deliberately understands backslash escapes instead of
    # `.*?`: a single-quoted literal containing \' would otherwise be cut short
    # at the escaped quote, handing back a truncated "current value".
    return re.compile(
        rf"(?P<kq>[\"']){re.escape(key_name)}(?P=kq)\s*:\s*(?P<q>[\"'])(?P<value>(?:\\.|(?!(?P=q))[^\\])*)(?P=q)",
        re.DOTALL,
    )


def _views_matches(repo_path, page, key_name):
    matches = []
    candidates = set(_candidate_names(page))
    key_names = VIEW_CONTEXT_KEYS.get(key_name, (key_name,))
    for path in _walk_files(repo_path, "views.py"):
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        for template_name, body_start, body_end in _context_spans(source):
            if template_name not in candidates:
                continue
            body = source[body_start:body_end]
            for candidate_key in key_names:
                value_match = _string_value_re(candidate_key).search(body)
                if not value_match:
                    continue
                matches.append(
                    {
                        "path": path,
                        "start": body_start + value_match.start("value"),
                        "end": body_start + value_match.end("value"),
                        "quote": value_match.group("q"),
                    }
                )
                break  # first key in precedence order wins
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
            return {
                "kind": "views-context",
                "path": view_matches[0]["path"],
                "start": view_matches[0]["start"],
                "end": view_matches[0]["end"],
                "quote": view_matches[0]["quote"],
            }
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
            return {
                "kind": "views-context",
                "path": view_matches[0]["path"],
                "start": view_matches[0]["start"],
                "end": view_matches[0]["end"],
                "quote": view_matches[0]["quote"],
            }
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


def _read_views_value(path, start, end, quote="'"):
    """Return the *decoded* Python string, not the raw source slice.

    The slice is an escaped literal body; handing that straight back would
    report a live value of "Greeley\\'s clinic" and then length-check and
    diff against the escaped form. ast.literal_eval parses, never executes."""
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    raw = source[start:end]
    try:
        return ast.literal_eval(f"{quote}{raw}{quote}")
    except (ValueError, SyntaxError) as err:
        raise ValueError(f"could not parse the string literal at {path}[{start}:{end}] - {err}")


def _encode_views_value(value, quote):
    """Escape a value for insertion into a `quote`-delimited Python literal.

    Without this, `_rewrite_views_context` spliced raw text between quotes, so
    an approved meta description containing an apostrophe would have written a
    syntactically invalid views.py - a Django app that fails to boot. The path
    was unreachable before (see _context_spans) so it never fired."""
    return value.replace("\\", "\\\\").replace(quote, "\\" + quote)


def _read_value_at_location(location):
    if location["kind"] == "template-block":
        return _read_block_value(location["path"], location["block"])
    return _read_views_value(location["path"], location["start"], location["end"], location.get("quote", "'"))


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


def _rewrite_views_context(path, start, end, new_value, quote="'"):
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    rewritten = source[:start] + _encode_views_value(new_value, quote) + source[end:]
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
        _rewrite_views_context(location["path"], location["start"], location["end"], new_value, location.get("quote", "'"))
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


def finalize_worktree(repo_path, proposal, attempt, git_runner=None, now=None, pre_commit_check=None):
    """pre_commit_check, if given, is called as pre_commit_check(worktree_path)
    after apply_rewrite() writes the file in the worktree but strictly before
    `git add`/`git commit` - raising from it aborts the commit (the caller's
    exception handler is responsible for worktree cleanup, same as any other
    finalize_worktree failure). Used by apply.py's codex-review auto-implement
    path to re-validate spec.md gates immediately before the commit, narrowing
    (not eliminating) the TOCTOU window between authorization and the actual
    write (Phase 7 - PLAN-PHASE7-CODEX-REVIEW.md item 17a)."""
    git_runner = git_runner or _default_git_runner
    apply_rewrite(attempt["worktree_path"], proposal)
    if pre_commit_check is not None:
        pre_commit_check(attempt["worktree_path"])
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


def _reapply_refused(repo_dir, proposal):
    """Re-running an already-applied rewrite must trip the previous_value
    staleness guard rather than silently writing again."""
    try:
        apply_rewrite(repo_dir, proposal)
    except ValueError as err:
        return "no longer matches the approved previous_value" in str(err)
    return False


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
                "\n"
                "def contact(request):\n"
                "    context = {\n"
                "        'nested': {'a': 1},\n"
                "        'meta_title': 'Old contact title',\n"
                # writes the source text: 'meta_description': 'Greeley\'s old contact desc',
                "        'meta_description': 'Greeley\\'s old contact desc',\n"
                "    }\n"
                "    return render(request, 'main/contact.html', context)\n"
                "\n"
                "def team(request):\n"
                "    context = {\n"
                "        'meta_description': \"A team desc with an ' apostrophe\",\n"
                "    }\n"
                "    return render(request, 'main/team.html', context)\n"
            )
        # Decoys that only a scoped walk excludes. Both are real collisions in
        # artwebsite: venv ships admin_doc/index.html with a title block, and
        # node_modules/@tailwindcss/forms/index.html is git-tracked, so it
        # reaches an apply worktree. "/" resolves candidates home + index.
        for decoy in (
            os.path.join(repo_dir, "venv", "Lib", "site-packages", "jazzmin", "templates", "admin_doc"),
            os.path.join(repo_dir, "node_modules", "@tailwindcss", "forms"),
            os.path.join(repo_dir, "staticfiles", "main"),
        ):
            os.makedirs(decoy, exist_ok=True)
            with open(os.path.join(decoy, "index.html"), "w", encoding="utf-8", newline="\n") as f:
                f.write("{% block title %}Decoy library title{% endblock %}\n")

        checks = [
            ("template title resolves", resolve_edit_location(repo_dir, "/services/", "title-tag-rewrite")["kind"] == "template-block"),
            ("views home meta resolves", resolve_edit_location(repo_dir, "/", "meta-description-rewrite")["kind"] == "views-context"),
            ("read_current_value reads a template block", read_current_value(repo_dir, "/services/", "title-tag-rewrite") == "Old"),
            ("read_current_value reads a views-context string", read_current_value(repo_dir, "/", "meta-description-rewrite") == "Old desc"),
        ]

        # --- scoped walk (EXCLUDED_DIR_NAMES) ---
        # Without pruning, "/" matches the venv/node_modules/staticfiles decoys
        # above and resolve_edit_location either raises "multiple title block
        # matches" or, with a single decoy, hands back a library file to edit.
        checks.append(
            (
                "resolve_edit_location ignores title blocks under venv/node_modules/staticfiles",
                resolve_edit_location(repo_dir, "/", "title-tag-rewrite")["kind"] == "views-context",
            )
        )
        checks.append(
            (
                "_walk_files yields no file inside an excluded directory",
                not [p for p in _walk_files(repo_dir, ".html") if any(part in EXCLUDED_DIR_NAMES for part in p.split(os.sep))],
            )
        )

        # --- context-as-variable + meta_title key ---
        contact_title_loc = resolve_edit_location(repo_dir, "/contact/", "title-tag-rewrite")
        checks.append(("a context passed by name (context = {...}) resolves, not just an inline dict", contact_title_loc["kind"] == "views-context"))
        checks.append(("title-tag-rewrite reads the meta_title context key", read_current_value(repo_dir, "/contact/", "title-tag-rewrite") == "Old contact title"))
        checks.append(
            (
                "a nested dict earlier in the context does not truncate the body scan",
                read_current_value(repo_dir, "/contact/", "meta-description-rewrite") == "Greeley's old contact desc",
            )
        )
        checks.append(
            (
                "an escaped quote is decoded, not returned in its raw escaped form",
                "\\" not in read_current_value(repo_dir, "/contact/", "meta-description-rewrite"),
            )
        )
        checks.append(
            (
                "a double-quoted literal containing an apostrophe reads back intact",
                read_current_value(repo_dir, "/team/", "meta-description-rewrite") == "A team desc with an ' apostrophe",
            )
        )

        # --- writing a value containing the delimiter quote ---
        # The bug this covers: _rewrite_views_context used to splice raw text
        # between quotes, so an approved description containing an apostrophe
        # produced a views.py that will not parse - a Django app that cannot boot.
        apostrophe_proposal = {
            "id": "prop-apostrophe-test",
            "action_type": "meta-description-rewrite",
            "target": {"page": "/contact/"},
            "implementation": {"previous_value": "Greeley's old contact desc", "new_value": "Denver's new contact desc"},
        }
        apply_rewrite(repo_dir, apostrophe_proposal)
        views_source = open(os.path.join(repo_dir, "app", "views.py"), "r", encoding="utf-8").read()
        parses = True
        try:
            ast.parse(views_source)
        except SyntaxError:
            parses = False
        checks.append(("writing a value containing the delimiter quote leaves views.py parseable", parses))
        checks.append(
            (
                "the round-tripped value with an apostrophe reads back exactly",
                read_current_value(repo_dir, "/contact/", "meta-description-rewrite") == "Denver's new contact desc",
            )
        )
        checks.append(
            (
                "the staleness check compares decoded values, so a re-apply of the same edit is refused",
                _reapply_refused(repo_dir, apostrophe_proposal),
            )
        )

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

        pre_commit_calls = []

        def _fake_finalize_runner(add_commit_responses):
            def runner(args, cwd):
                if args[:2] == ["git", "add"]:
                    return ""
                if args[:2] == ["git", "commit"]:
                    return ""
                if args == ["git", "rev-parse", "HEAD"]:
                    return "committed-sha"
                if args[:3] == ["git", "worktree", "remove"]:
                    return ""
                raise AssertionError(f"unexpected git command in finalize test: {args}")
            return runner

        pre_commit_proposal = {
            "id": "prop-pre-commit-test",
            "action_type": "title-tag-rewrite",
            "target": {"page": "/services/"},
            "implementation": {"previous_value": "New title", "new_value": "Newer title"},
        }
        # worktree_parent must be a real, disposable directory distinct from
        # repo_dir - cleanup_worktree() rmtree's it, and repo_dir is reused
        # across every check in this self-test (a None/derived-from-repo_dir
        # parent would make cleanup_worktree delete the shared fixture).
        pre_commit_parent = tempfile.mkdtemp(prefix="artwebsite-implementer-pretest-parent-")
        pre_commit_attempt = {"worktree_path": repo_dir, "branch": "seo/prop-pre-commit-test", "base_commit": "base-sha", "worktree_parent": pre_commit_parent}
        result = finalize_worktree(repo_dir, pre_commit_proposal, pre_commit_attempt, git_runner=_fake_finalize_runner({}), pre_commit_check=lambda wt: pre_commit_calls.append(wt))
        checks.append(("finalize_worktree calls pre_commit_check exactly once with the worktree path", pre_commit_calls == [repo_dir]))
        checks.append(("finalize_worktree still returns the expected implementation record when pre_commit_check passes", result["implemented_commit_sha"] == "committed-sha"))
        checks.append(("finalize_worktree's rewrite happened before the (passing) pre_commit_check", read_current_value(repo_dir, "/services/", "title-tag-rewrite") == "Newer title"))

        pre_commit_refused_proposal = {
            "id": "prop-pre-commit-refuse-test",
            "action_type": "title-tag-rewrite",
            "target": {"page": "/services/"},
            "implementation": {"previous_value": "Newer title", "new_value": "Should never commit"},
        }
        pre_commit_refused_attempt = {"worktree_path": repo_dir, "branch": "seo/prop-pre-commit-refuse-test", "base_commit": "base-sha", "worktree_parent": None}

        def _refusing_check(worktree_path):
            raise ValueError("pre_commit_check refused")

        def _commit_should_not_run(args, cwd):
            if args[:2] == ["git", "commit"]:
                raise AssertionError("git commit ran despite pre_commit_check refusing")
            return ""

        pre_commit_refused = False
        try:
            finalize_worktree(repo_dir, pre_commit_refused_proposal, pre_commit_refused_attempt, git_runner=_commit_should_not_run, pre_commit_check=_refusing_check)
        except ValueError as e:
            pre_commit_refused = "pre_commit_check refused" in str(e)
        checks.append(("finalize_worktree propagates a pre_commit_check refusal and never calls git commit", pre_commit_refused))
        checks.append(("the rewrite from a refused pre_commit_check is still on disk (finalize_worktree does not roll back the working-tree edit itself - the caller's exception handling/worktree cleanup owns that)", read_current_value(repo_dir, "/services/", "title-tag-rewrite") == "Should never commit"))

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

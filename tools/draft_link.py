# Deterministic, LLM-free storage + validation for SEO internal-link-addition
# proposals (Phase 7 - PLAN-PHASE7-CODEX-REVIEW.md item 20), structurally
# parallel to draft_copy.py. Claude (via the codex-seo-review skill) reads
# the actual source/destination page content and authors the source page,
# destination page, and anchor text externally; this tool only validates and
# stores them. It does NOT implement an HTML mutation - no code anywhere
# resolves an edit location for "internal-link-addition" (see
# lib/artwebsite_seo.py::resolve_edit_location, which still raises for this
# action_type), so a drafted internal-link proposal can be reviewed but can
# never reach automatic implementation - it is always held for lack of a
# mechanical auto-implementer (see lib/review_protocol.py). This is a
# deliberate scope boundary (Key Decision 3), not an oversight.
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit

import yaml

try:
    from .lib.event_log import append_event, build_secret_map, sync_proposal_projection
    from .lib.lock import acquire_lock, release_lock
    from .lib.proposals import atomic_write_json, load_json, loop_dir_for, pending_dir_for, proposal_path
    from .run_loop import _read_lock_ttl_minutes_unsafe
    from .spec_validate import extract_frontmatter
except ImportError:
    from lib.event_log import append_event, build_secret_map, sync_proposal_projection
    from lib.lock import acquire_lock, release_lock
    from lib.proposals import atomic_write_json, load_json, loop_dir_for, pending_dir_for, proposal_path
    from run_loop import _read_lock_ttl_minutes_unsafe
    from spec_validate import extract_frontmatter

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(THIS_DIR)
PROJECTS_ROOT = os.path.join(WORKSPACE_ROOT, "projects")
MAX_ANCHOR_LENGTH = 80
MAX_SNIPPET_LENGTH = 2000


def _now_iso(now=None):
    now = now or datetime.now(timezone.utc)
    return now.isoformat().replace("+00:00", "Z")


def _load_spec(loop_dir):
    with open(os.path.join(loop_dir, "spec.md"), "r", encoding="utf-8") as f:
        return yaml.safe_load(extract_frontmatter(f.read())) or {}


def _load_repo_path(project):
    project_path = os.path.join(PROJECTS_ROOT, project, "project.md")
    with open(project_path, "r", encoding="utf-8") as f:
        source = f.read()
    return (yaml.safe_load(extract_frontmatter(source)) or {}).get("repo")


def normalize_page_path(page):
    """Origin/path normalization, mirroring the existing GSC/DataForSEO
    page-normalization precedent (HANDOFF.md commit 87145bc) - GSC returns a
    full URL while proposal targets/spec fields mix absolute URLs and bare
    paths; comparing raw strings would falsely reject a valid match."""
    if not page:
        return "/"
    path = urlsplit(page).path if "://" in page else page
    if not path.startswith("/"):
        path = "/" + path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return path.lower()


def validate_link(source_page, destination_page, anchor_text, priority_pages, noindex_destination_pages):
    if not isinstance(source_page, str) or source_page.strip() == "":
        raise ValueError("draft_link.py: source_page must be a non-empty string")
    if not isinstance(destination_page, str) or destination_page.strip() == "":
        raise ValueError("draft_link.py: destination_page must be a non-empty string")
    if not isinstance(anchor_text, str) or anchor_text.strip() == "":
        raise ValueError("draft_link.py: anchor_text must be a non-empty string")
    if "\n" in anchor_text or "\r" in anchor_text:
        raise ValueError("draft_link.py: anchor_text must be a single line (no line breaks)")
    if len(anchor_text) > MAX_ANCHOR_LENGTH:
        raise ValueError(f"draft_link.py: anchor_text is {len(anchor_text)} characters, over the {MAX_ANCHOR_LENGTH}-character limit")

    if normalize_page_path(source_page) == normalize_page_path(destination_page):
        raise ValueError("draft_link.py: source_page and destination_page resolve to the same page - nothing to link")

    normalized_priority = {normalize_page_path(p) for p in (priority_pages or [])}
    if normalize_page_path(destination_page) not in normalized_priority:
        raise ValueError(f"draft_link.py: destination_page {destination_page!r} is not in this loop's priority_pages - refusing to propose a link to an untracked page")

    normalized_noindex = {normalize_page_path(p) for p in (noindex_destination_pages or [])}
    if normalize_page_path(destination_page) in normalized_noindex:
        raise ValueError(f"draft_link.py: destination_page {destination_page!r} is listed in noindex_destination_pages - refusing to propose a link to a deliberately noindexed page")


def draft_link(project, loop, proposal_id, source_page, destination_page, anchor_text, drafted_by, source_snippet=None, repo_path=None, now=None):
    loop_dir = loop_dir_for(project, loop)
    pending_dir = pending_dir_for(project, loop)
    proposal_pathname = proposal_path(pending_dir, proposal_id)
    if not os.path.exists(proposal_pathname):
        raise ValueError(f"draft_link.py: proposal {proposal_id} not found")

    lock_ttl_minutes = _read_lock_ttl_minutes_unsafe(os.path.join(loop_dir, "spec.md"))
    lock = acquire_lock(loop_dir, max_run_duration_minutes=lock_ttl_minutes, runs_dir=os.path.join(loop_dir, "runs"), now=now)
    if not lock["acquired"]:
        raise ValueError(f'draft_link.py: REFUSED - run lock active for {project}/{loop} - {lock["reason"]}')

    try:
        proposal = load_json(proposal_pathname)
        is_revision = proposal.get("status") == "review-revision-needed"
        if proposal.get("status") not in ("draft", "review-revision-needed"):
            raise ValueError(
                f'draft_link.py: proposal {proposal_id} has status "{proposal.get("status")}", not "draft" or '
                '"review-revision-needed"'
            )
        if proposal.get("action_type") != "internal-link-addition":
            raise ValueError(f'draft_link.py: proposal {proposal_id} action_type is "{proposal.get("action_type")}", not "internal-link-addition"')

        spec = _load_spec(loop_dir)
        validate_link(source_page, destination_page, anchor_text, spec.get("priority_pages"), spec.get("noindex_destination_pages"))

        if source_snippet is not None and len(source_snippet) > MAX_SNIPPET_LENGTH:
            source_snippet = source_snippet[:MAX_SNIPPET_LENGTH]

        implementation_before = proposal.get("implementation")
        implementation_after = {
            "source_page": source_page,
            "destination_page": destination_page,
            "anchor_text": anchor_text,
            "source_snippet": source_snippet,
            "drafted_at": _now_iso(now),
            "drafted_by": drafted_by,
        }

        if not is_revision:
            proposal["implementation"] = implementation_after
            atomic_write_json(proposal_pathname, proposal)
            return proposal

        repo_path = repo_path or _load_repo_path(project)
        secret_map = build_secret_map(repo_path, spec.get("credential_aliases"))
        append_event(
            loop_dir, "proposal_revised", project=project, loop=loop, proposal_id=proposal_id,
            action_type="internal-link-addition", target_page=(proposal.get("target") or {}).get("page"),
            keyword=(proposal.get("target") or {}).get("keyword"),
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
    if len(args) < 6:
        print("usage: python tools/draft_link.py <project> <loop> <proposal-id> <source_page> <destination_page> <anchor_text> [--by <name>]", file=sys.stderr)
        sys.exit(2)
    project, loop, proposal_id, source_page, destination_page, anchor_text = args[0], args[1], args[2], args[3], args[4], args[5]
    drafted_by = "claude"
    if "--by" in args:
        idx = args.index("--by")
        if idx + 1 < len(args):
            drafted_by = args[idx + 1]
    try:
        proposal = draft_link(project, loop, proposal_id, source_page, destination_page, anchor_text, drafted_by)
        implementation = proposal["implementation"]
        print(f'drafted {proposal["id"]}: {implementation["source_page"]} -> {implementation["destination_page"]} ("{implementation["anchor_text"]}")')
    except Exception as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)


def _self_test():
    import shutil
    import tempfile

    from lib.proposals import write_proposal

    project = "_draft-link-selftest-tmp"
    loop = "seo"
    project_dir = os.path.join(PROJECTS_ROOT, project)
    pending_dir = os.path.join(project_dir, "loops", loop, "pending")
    shutil.rmtree(project_dir, ignore_errors=True)
    os.makedirs(pending_dir, exist_ok=True)
    with open(os.path.join(project_dir, "loops", loop, "spec.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(
            "---\nversion: 1\nloop: seo\n"
            "priority_pages:\n  - https://example.com/services/\n  - https://example.com/contact/\n  - https://example.com/ads/landing/\n"
            "noindex_destination_pages:\n  - https://example.com/ads/landing/\n"
            "---\n"
        )
    with open(os.path.join(project_dir, "project.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("---\nrepo: D:\\fake\\repo\n---\n")

    def _seed(proposal_id, status="draft"):
        write_proposal(pending_dir, {"id": proposal_id, "action_type": "internal-link-addition", "target": {"page": "/blog/post/"}, "status": status})

    checks = []
    try:
        checks.append((
            "normalize_page_path treats an absolute URL and a bare path the same",
            normalize_page_path("https://example.com/services/") == normalize_page_path("/services/"),
        ))
        checks.append((
            "normalize_page_path is tolerant of a missing trailing slash",
            normalize_page_path("/services") == normalize_page_path("/services/"),
        ))

        _seed("prop-valid-link")
        result = draft_link(project, loop, "prop-valid-link", "/blog/post/", "https://example.com/services/", "our services", "claude-test")
        checks.append(("valid link draft stores source/destination/anchor", result["implementation"]["destination_page"] == "https://example.com/services/" and result["implementation"]["anchor_text"] == "our services"))

        threw_same_page = False
        _seed("prop-same-page")
        try:
            draft_link(project, loop, "prop-same-page", "/services/", "https://example.com/services/", "x", "claude-test")
        except ValueError as e:
            threw_same_page = "same page" in str(e)
        checks.append(("source and destination resolving to the same page is rejected", threw_same_page))

        threw_not_priority = False
        _seed("prop-not-priority")
        try:
            draft_link(project, loop, "prop-not-priority", "/blog/post/", "/untracked-page/", "x", "claude-test")
        except ValueError as e:
            threw_not_priority = "not in this loop's priority_pages" in str(e)
        checks.append(("a destination outside priority_pages is rejected", threw_not_priority))

        threw_noindex = False
        _seed("prop-noindex")
        try:
            draft_link(project, loop, "prop-noindex", "/blog/post/", "https://example.com/ads/landing/", "x", "claude-test")
        except ValueError as e:
            threw_noindex = "noindex_destination_pages" in str(e)
        checks.append(("a destination in noindex_destination_pages is rejected even if hypothetically in priority_pages", threw_noindex))

        threw_blank_anchor = False
        _seed("prop-blank-anchor")
        try:
            draft_link(project, loop, "prop-blank-anchor", "/blog/post/", "https://example.com/services/", "   ", "claude-test")
        except ValueError as e:
            threw_blank_anchor = "non-empty" in str(e)
        checks.append(("blank anchor text is rejected", threw_blank_anchor))

        threw_long_anchor = False
        _seed("prop-long-anchor")
        try:
            draft_link(project, loop, "prop-long-anchor", "/blog/post/", "https://example.com/services/", "x" * 81, "claude-test")
        except ValueError as e:
            threw_long_anchor = "80-character limit" in str(e)
        checks.append(("anchor text over 80 characters is rejected", threw_long_anchor))

        _seed("prop-revision", status="review-revision-needed")
        revised = draft_link(project, loop, "prop-revision", "/blog/post/", "https://example.com/contact/", "contact us", "claude-test")
        checks.append(("a review-revision-needed link proposal can be redrafted", revised["implementation"]["destination_page"] == "https://example.com/contact/"))

        for status in ("reviewed", "approved", "implemented", "applied", "verified", "breached", "rejected", "implement-failed"):
            proposal_id = f"prop-past-draft-{status}"
            _seed(proposal_id, status=status)
            threw_past_draft = False
            try:
                draft_link(project, loop, proposal_id, "/blog/post/", "https://example.com/services/", "x", "claude-test")
            except ValueError as e:
                threw_past_draft = "not \"draft\" or" in str(e)
            checks.append((f'drafting a link for a proposal with status "{status}" is refused', threw_past_draft))
    finally:
        shutil.rmtree(project_dir, ignore_errors=True)

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    print(f"{len(checks) - failed}/{len(checks)} checks passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    if "--verify" in sys.argv:
        _self_test()
    else:
        _cli()

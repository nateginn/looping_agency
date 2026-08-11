import fnmatch
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

MERGE_METHODS = ("MERGE", "SQUASH", "REBASE")

# Rule types (from GitHub's merged "effective rules for a branch" endpoint)
# that must be present, beyond pull_request (checked separately for its
# required-review-count parameter). Phase 0c-bis / PLAN-REVIEW-LOG.md Round
# 3 #4: classic branch-protection data alone cannot prove the absence of a
# ruleset bypass actor, so verify_branch_protection() below checks both
# mechanisms rather than classic protection only.
REQUIRED_EFFECTIVE_RULE_TYPES = {"non_fast_forward", "deletion"}


def _default_requester(url, headers):
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, "OK", response.read()
    except urllib.error.HTTPError as err:
        return err.code, err.reason, err.read()


def _default_poster(url, headers, body):
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, "OK", response.read()
    except urllib.error.HTTPError as err:
        return err.code, err.reason, err.read()


def compare_commit_to_main(owner, repo, commit_sha, requester=None, token=None):
    requester = requester or _default_requester
    url = f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/compare/{urllib.parse.quote(commit_sha)}...main"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "looping-agency-phase3",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    status_code, reason, body = requester(url, headers)
    if status_code != 200:
        text = body.decode("utf-8", errors="replace")
        raise ValueError(f"GitHub compare API failed ({status_code} {reason}): {text}")
    payload = json.loads(body.decode("utf-8"))
    status = payload.get("status")
    return {
        "url": url,
        "status": status,
        "live": status in ("identical", "ahead"),
        "payload": payload,
    }


# ---------------------------------------------------------------------------
# DORMANT BY DECISION - everything from here to the end of enable_auto_merge()
# below serves Path A (side branch -> PR -> branch-protection-gated auto-merge),
# which PLAN.md's "SELECTED OPERATING MODEL" section did NOT select. It is
# reached only from tools/publish.py, itself dormant; see that file's header.
#
# compare_commit_to_main() ABOVE is NOT dormant - run_loop.py calls it every
# run to promote implemented -> applied once a commit is live on `main`, and it
# stays load-bearing under Path B. Do not remove it while pruning this block.
# ---------------------------------------------------------------------------


def get_branch_protection(owner, repo, branch, token, requester=None):
    """Read-only GET of GitHub's classic branch-protection API. Raises
    ValueError on any non-200 response (including 404 - unprotected), never
    returns a partial/best-guess result - a query failure must read as
    "protection could not be verified", not "protection is fine"."""
    requester = requester or _default_requester
    url = f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/branches/{urllib.parse.quote(branch)}/protection"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "looping-agency-phase6",
        "Authorization": f"Bearer {token}",
    }
    status_code, reason, body = requester(url, headers)
    if status_code == 404:
        raise ValueError(f"REFUSED: branch protection is not enabled on {owner}/{repo}@{branch} (404 from GitHub)")
    if status_code != 200:
        text = body.decode("utf-8", errors="replace")
        raise ValueError(f"REFUSED: branch protection check failed for {owner}/{repo}@{branch} ({status_code} {reason}): {text}")
    return json.loads(body.decode("utf-8"))


def get_effective_rules_for_branch(owner, repo, branch, token, requester=None):
    """GitHub's merged-rules endpoint: every rule actually enforced on
    `branch` right now, from BOTH classic branch protection and any
    repository ruleset that targets it. A rule sourced from a disabled or
    "evaluate"-mode ruleset never appears here - by construction, an empty
    or incomplete result is a true statement about current enforcement, not
    an artifact of enforcement mode. This is the primary source of truth
    for "is a PR with reviews actually required / are force-push and
    deletion actually blocked", regardless of which mechanism supplies it."""
    requester = requester or _default_requester
    url = f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/rules/branches/{urllib.parse.quote(branch)}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "looping-agency-phase6",
        "Authorization": f"Bearer {token}",
    }
    status_code, reason, body = requester(url, headers)
    if status_code != 200:
        text = body.decode("utf-8", errors="replace")
        raise ValueError(f"REFUSED: could not read effective rules for {owner}/{repo}@{branch} ({status_code} {reason}): {text}")
    try:
        rules = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        raise ValueError(f"REFUSED: effective rules response for {owner}/{repo}@{branch} is not valid JSON - {err}")
    if not isinstance(rules, list):
        raise ValueError(f"REFUSED: effective rules response for {owner}/{repo}@{branch} is not a JSON array (got {type(rules).__name__})")
    return rules


def get_repository_rulesets(owner, repo, token, requester=None):
    """List every ruleset defined on the repo (summaries only - id, target,
    enforcement; no bypass_actors, that needs get_ruleset_detail per-id)."""
    requester = requester or _default_requester
    url = f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/rulesets"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "looping-agency-phase6",
        "Authorization": f"Bearer {token}",
    }
    status_code, reason, body = requester(url, headers)
    if status_code != 200:
        text = body.decode("utf-8", errors="replace")
        raise ValueError(f"REFUSED: could not list rulesets for {owner}/{repo} ({status_code} {reason}): {text}")
    try:
        rulesets = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        raise ValueError(f"REFUSED: rulesets list response for {owner}/{repo} is not valid JSON - {err}")
    if not isinstance(rulesets, list):
        raise ValueError(f"REFUSED: rulesets list response for {owner}/{repo} is not a JSON array (got {type(rulesets).__name__})")
    return rulesets


def get_ruleset_detail(owner, repo, ruleset_id, token, requester=None):
    """Full detail for one ruleset, including bypass_actors and the
    ref_name conditions needed to decide whether it targets a given
    branch - neither is present in the list/summary endpoint."""
    requester = requester or _default_requester
    url = f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/rulesets/{urllib.parse.quote(str(ruleset_id))}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "looping-agency-phase6",
        "Authorization": f"Bearer {token}",
    }
    status_code, reason, body = requester(url, headers)
    if status_code != 200:
        text = body.decode("utf-8", errors="replace")
        raise ValueError(f"REFUSED: could not read ruleset {ruleset_id} for {owner}/{repo} ({status_code} {reason}): {text}")
    try:
        detail = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        raise ValueError(f"REFUSED: ruleset {ruleset_id} detail for {owner}/{repo} is not valid JSON - {err}")
    if not isinstance(detail, dict):
        raise ValueError(f"REFUSED: ruleset {ruleset_id} detail for {owner}/{repo} is not a JSON object (got {type(detail).__name__})")
    return detail


def _ref_pattern_matches(pattern, ref, default_branch_ref):
    if not isinstance(pattern, str) or not pattern:
        return False
    if pattern == "~ALL":
        return True
    if pattern == "~DEFAULT_BRANCH":
        # Simplifying assumption, not independently verified live: this
        # workspace's whole publish pipeline assumes `main` is the default
        # branch throughout (apply.py, publish.py's forbidden-destination
        # check, etc.) - so ~DEFAULT_BRANCH is treated as matching exactly
        # when the branch under test is that same assumed default.
        return ref == default_branch_ref
    return fnmatch.fnmatchcase(ref, pattern)


def _ruleset_applies_to_branch(ruleset, branch):
    """True if `ruleset` (a full detail object) targets `branch` via its
    ref_name include/exclude conditions. Raises ValueError - never returns
    False - when a branch-targeted ruleset's conditions are missing or
    unparseable: treating "can't tell" as "doesn't apply" would silently
    skip the bypass-actor audit for a ruleset that might well apply,
    exactly the failure mode this whole check exists to close."""
    if not isinstance(ruleset, dict):
        raise ValueError("REFUSED: malformed ruleset entry (not an object) while checking branch applicability")
    if ruleset.get("target") != "branch":
        return False
    conditions = ruleset.get("conditions")
    if not isinstance(conditions, dict):
        raise ValueError(f"REFUSED: ruleset {ruleset.get('id')!r} targets branches but has no parseable conditions")
    ref_name = conditions.get("ref_name")
    if not isinstance(ref_name, dict):
        raise ValueError(f"REFUSED: ruleset {ruleset.get('id')!r} targets branches but has no parseable ref_name condition")
    includes = ref_name.get("include")
    if not isinstance(includes, list) or not includes or not all(isinstance(p, str) for p in includes):
        raise ValueError(f"REFUSED: ruleset {ruleset.get('id')!r} has a malformed or empty ref_name.include list")
    excludes = ref_name.get("exclude")
    if excludes is not None and (not isinstance(excludes, list) or not all(isinstance(p, str) for p in excludes)):
        raise ValueError(f"REFUSED: ruleset {ruleset.get('id')!r} has a malformed ref_name.exclude list")

    ref = f"refs/heads/{branch}"
    default_branch_ref = "refs/heads/main"
    if any(_ref_pattern_matches(p, ref, default_branch_ref) for p in (excludes or [])):
        return False
    return any(_ref_pattern_matches(p, ref, default_branch_ref) for p in includes)


def verify_branch_protection(owner, repo, branch, token, requester=None):
    """Hard-refuse (Codex R2 #2, R3 #4 / PLAN.md Phase 0c-bis) unless
    `branch` is verifiably protected through BOTH mechanisms GitHub
    offers - classic branch protection and repository rulesets - since
    classic data alone cannot prove the absence of a ruleset bypass actor.

    Two independent gates, both must pass, or this function raises:

    Gate A (rule presence, effective/merged). Queries
    get_effective_rules_for_branch() - GitHub's own merge of classic
    protection and every active ruleset - and requires: a `pull_request`
    rule with required_approving_review_count >= 1; a `non_fast_forward`
    rule (blocks force pushes); a `deletion` rule (blocks branch deletion).

    Gate B (no bypass actor capable of weakening the above). Classic
    protection, if present, must have enforce_admins enabled and an empty
    bypass_pull_request_allowances. Separately, every repository ruleset
    whose conditions target `branch` AND whose enforcement is "active" must
    have an empty bypass_actors list. A bypass actor on ANY actively-
    enforced ruleset targeting the branch refuses the whole check, even if
    classic protection would independently still block that specific
    action - conservative by design, chosen for auditability over a full
    per-mechanism reachability analysis.

    Any missing element, any unparseable/malformed API response, or any
    error querying either mechanism fails closed - this function must never
    return normally unless both gates are provably satisfied."""
    effective_rules = get_effective_rules_for_branch(owner, repo, branch, token, requester=requester)

    pull_request_rules = [r for r in effective_rules if isinstance(r, dict) and r.get("type") == "pull_request"]
    if not pull_request_rules:
        raise ValueError(f"REFUSED: no effective pull_request rule found for {owner}/{repo}@{branch} - a PR is not actually required to merge")
    review_counts = []
    for rule in pull_request_rules:
        params = rule.get("parameters")
        count = params.get("required_approving_review_count") if isinstance(params, dict) else None
        if isinstance(count, int) and not isinstance(count, bool):
            review_counts.append(count)
    if not review_counts or max(review_counts) < 1:
        raise ValueError(f"REFUSED: effective pull_request rule(s) for {owner}/{repo}@{branch} do not require any approving review")

    effective_types = {r.get("type") for r in effective_rules if isinstance(r, dict)}
    missing_types = REQUIRED_EFFECTIVE_RULE_TYPES - effective_types
    if missing_types:
        raise ValueError(
            f"REFUSED: {owner}/{repo}@{branch} is missing effective rule(s) {sorted(missing_types)} "
            "(checked both classic branch protection and rulesets)"
        )

    # Gate B, part 1 - classic protection's own admin/reviewer bypass
    # surface, if classic protection exists at all (its absence is no
    # longer automatically fatal now that a ruleset can supply Gate A
    # instead - inlined rather than calling get_branch_protection() so a
    # 404 here is distinguished from a real query error without parsing
    # error-message text).
    classic_url = f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/branches/{urllib.parse.quote(branch)}/protection"
    classic_headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "looping-agency-phase6",
        "Authorization": f"Bearer {token}",
    }
    _requester = requester or _default_requester
    status_code, reason, body = _requester(classic_url, classic_headers)
    if status_code == 404:
        classic = None
    elif status_code != 200:
        text = body.decode("utf-8", errors="replace")
        raise ValueError(f"REFUSED: classic branch protection check failed for {owner}/{repo}@{branch} ({status_code} {reason}): {text}")
    else:
        try:
            classic = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            raise ValueError(f"REFUSED: classic branch protection response for {owner}/{repo}@{branch} is not valid JSON - {err}")
        if not isinstance(classic, dict):
            raise ValueError(f"REFUSED: classic branch protection response for {owner}/{repo}@{branch} is not a JSON object")

    if classic is not None:
        reviews = classic.get("required_pull_request_reviews") or {}
        admins_enforced = (classic.get("enforce_admins") or {}).get("enabled")
        bypass = reviews.get("bypass_pull_request_allowances") or {}
        classic_bypass_actors = list(bypass.get("users") or []) + list(bypass.get("teams") or []) + list(bypass.get("apps") or [])
        if admins_enforced is not True:
            raise ValueError(f"REFUSED: classic branch protection on {owner}/{repo}@{branch} does not enforce_admins (admins can bypass)")
        if classic_bypass_actors:
            raise ValueError(f"REFUSED: classic branch protection on {owner}/{repo}@{branch} lists {len(classic_bypass_actors)} bypass actor(s) on required reviews")

    # Gate B, part 2 - every actively-enforced ruleset targeting this
    # branch must itself have no bypass actors. A disabled/"evaluate"-mode
    # ruleset is skipped: it enforces nothing right now, so its bypass
    # list is moot (and if it were the repo's only source of Gate A's
    # rules, Gate A above would already have refused).
    rulesets = get_repository_rulesets(owner, repo, token, requester=requester)
    checked_ruleset_ids = []
    for summary in rulesets:
        if not isinstance(summary, dict):
            raise ValueError(f"REFUSED: malformed entry in rulesets list for {owner}/{repo}")
        if summary.get("enforcement") != "active":
            continue
        ruleset_id = summary.get("id")
        if ruleset_id is None:
            raise ValueError(f"REFUSED: an active ruleset for {owner}/{repo} has no id - cannot audit its bypass actors")
        detail = get_ruleset_detail(owner, repo, ruleset_id, token, requester=requester)
        if not _ruleset_applies_to_branch(detail, branch):
            continue
        checked_ruleset_ids.append(ruleset_id)
        ruleset_bypass_actors = detail.get("bypass_actors")
        if not isinstance(ruleset_bypass_actors, list):
            raise ValueError(f"REFUSED: ruleset {ruleset_id} for {owner}/{repo} has a missing or malformed bypass_actors field")
        if ruleset_bypass_actors:
            raise ValueError(
                f"REFUSED: ruleset {ruleset_id} ({summary.get('name')!r}) for {owner}/{repo}@{branch} "
                f"has {len(ruleset_bypass_actors)} bypass actor(s)"
            )

    return {
        "effective_rules": effective_rules,
        "classic_protection": classic,
        "rulesets_checked": checked_ruleset_ids,
    }


def create_pull_request(owner, repo, head, base, title, body_text, token, poster=None):
    """Open a PR via the REST API (urllib, no `gh` CLI dependency - gh is
    not installed on this workstation). Returns the fields publish.py needs
    to persist and to request auto-merge: number, html_url, node_id (the
    GraphQL global ID enablePullRequestAutoMerge requires)."""
    poster = poster or _default_poster
    url = f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/pulls"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "looping-agency-phase6",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = json.dumps(
        {"title": title, "head": head, "base": base, "body": body_text, "maintainer_can_modify": True}
    ).encode("utf-8")
    status_code, reason, response_body = poster(url, headers, payload)
    if status_code not in (200, 201):
        text = response_body.decode("utf-8", errors="replace")
        raise ValueError(f"GitHub create-PR API failed ({status_code} {reason}): {text}")
    data = json.loads(response_body.decode("utf-8"))
    return {
        "number": data.get("number"),
        "html_url": data.get("html_url"),
        "node_id": data.get("node_id"),
        "head_sha": (data.get("head") or {}).get("sha"),
    }


def enable_auto_merge(pull_request_node_id, token, merge_method="SQUASH", poster=None):
    """Request GitHub auto-merge via the enablePullRequestAutoMerge GraphQL
    mutation - the REST API has no equivalent endpoint. Auto-merge then
    completes on its own once required checks pass, with no second human
    action needed."""
    if merge_method not in MERGE_METHODS:
        raise ValueError(f"merge_method must be one of {MERGE_METHODS} (got {merge_method!r})")
    poster = poster or _default_poster
    url = "https://api.github.com/graphql"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "looping-agency-phase6",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    query = (
        "mutation($pullRequestId: ID!, $mergeMethod: PullRequestMergeMethod!) {"
        " enablePullRequestAutoMerge(input: {pullRequestId: $pullRequestId, mergeMethod: $mergeMethod}) {"
        " pullRequest { autoMergeRequest { enabledAt } } } }"
    )
    payload = json.dumps(
        {"query": query, "variables": {"pullRequestId": pull_request_node_id, "mergeMethod": merge_method}}
    ).encode("utf-8")
    status_code, reason, response_body = poster(url, headers, payload)
    if status_code != 200:
        text = response_body.decode("utf-8", errors="replace")
        raise ValueError(f"GitHub auto-merge GraphQL request failed ({status_code} {reason}): {text}")
    data = json.loads(response_body.decode("utf-8"))
    if data.get("errors"):
        raise ValueError(f"GitHub auto-merge GraphQL request returned errors: {data['errors']}")
    enabled_at = (
        ((data.get("data") or {}).get("enablePullRequestAutoMerge") or {}).get("pullRequest") or {}
    ).get("autoMergeRequest", {})
    enabled_at = enabled_at.get("enabledAt") if isinstance(enabled_at, dict) else None
    if not enabled_at:
        raise ValueError(f"GitHub auto-merge GraphQL request did not report an enabledAt timestamp: {data}")
    return {"enabled": True, "enabled_at": enabled_at, "raw": data}


def _self_test():
    calls = []

    def fake_requester(url, headers):
        calls.append((url, headers))
        return 200, "OK", json.dumps({"status": "ahead"}).encode("utf-8")

    result = compare_commit_to_main("nateginn", "artwebsite", "abc123", requester=fake_requester)
    checks = [
        ("compare requester called exactly once", len(calls) == 1),
        ("compare API URL targets main ancestry", calls[0][0].endswith("/compare/abc123...main")),
        ("ahead counts as live", result["live"] is True and result["status"] == "ahead"),
    ]

    # ---- get_branch_protection (classic, standalone) ----
    good_classic_payload = {
        "required_pull_request_reviews": {"required_approving_review_count": 1, "bypass_pull_request_allowances": {"users": [], "teams": [], "apps": []}},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "enforce_admins": {"enabled": True},
    }

    def _fixed_requester(status, body_obj):
        def requester(url, headers):
            checks.append((f"request to {url.rsplit('/', 1)[-1] or url} carries a Bearer token", headers.get("Authorization") == "Bearer fake-token"))
            body = body_obj if isinstance(body_obj, bytes) else json.dumps(body_obj).encode("utf-8")
            return status, "OK", body

        return requester

    classic_direct = get_branch_protection("nateginn", "artwebsite", "main", "fake-token", requester=_fixed_requester(200, good_classic_payload))
    checks.append(("get_branch_protection returns the parsed classic payload", classic_direct == good_classic_payload))

    classic_404_refused = False
    try:
        get_branch_protection("nateginn", "artwebsite", "main", "fake-token", requester=_fixed_requester(404, {}))
    except ValueError as e:
        classic_404_refused = "not enabled" in str(e)
    checks.append(("get_branch_protection refuses on 404 (unprotected)", classic_404_refused))

    # ---- verify_branch_protection (combined: effective rules + rulesets) ----
    GOOD_EFFECTIVE_RULES = [
        {"type": "pull_request", "ruleset_source_type": "Repository", "ruleset_source": "artwebsite", "parameters": {"required_approving_review_count": 1}},
        {"type": "non_fast_forward", "ruleset_source_type": "Repository", "ruleset_source": "artwebsite", "parameters": {}},
        {"type": "deletion", "ruleset_source_type": "Repository", "ruleset_source": "artwebsite", "parameters": {}},
    ]
    ACTIVE_RULESET_TARGETING_MAIN_NO_BYPASS = {
        "id": 555, "name": "main-lockdown", "target": "branch", "source_type": "Repository", "source": "nateginn/artwebsite", "enforcement": "active",
    }
    ACTIVE_RULESET_DETAIL_NO_BYPASS = {
        "id": 555, "name": "main-lockdown", "target": "branch", "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
        "rules": [], "bypass_actors": [],
    }

    def _endpoint_dispatch_requester(handlers):
        """handlers: list of (predicate(url) -> bool, status, body_obj). First match wins;
        an unmatched URL is a test-authoring bug, not a thing to silently ignore."""

        def requester(url, headers):
            checks.append((f"request to {url} carries a Bearer token", headers.get("Authorization") == "Bearer fake-token"))
            for predicate, status, body_obj in handlers:
                if predicate(url):
                    body = body_obj if isinstance(body_obj, bytes) else json.dumps(body_obj).encode("utf-8")
                    return status, "OK" if status < 400 else "ERROR", body
            raise AssertionError(f"unexpected URL in verify_branch_protection self-test dispatch requester: {url}")

        return requester

    def _is_effective_rules(url):
        return "/rules/branches/" in url

    def _is_classic_protection(url):
        return url.endswith("/protection")

    def _is_rulesets_list(url):
        return url.endswith("/rulesets")

    def _is_ruleset_detail(url):
        return "/rulesets/" in url

    def _compliant_requester(effective=None, classic=None, classic_status=200, rulesets=None):
        return _endpoint_dispatch_requester(
            [
                (_is_effective_rules, 200, effective if effective is not None else GOOD_EFFECTIVE_RULES),
                (_is_ruleset_detail, 200, ACTIVE_RULESET_DETAIL_NO_BYPASS),
                (_is_rulesets_list, 200, rulesets if rulesets is not None else []),
                (_is_classic_protection, classic_status, classic if classic is not None else good_classic_payload),
            ]
        )

    # -- compliant: classic protection alone, no rulesets configured at all --
    result = verify_branch_protection("nateginn", "artwebsite", "main", "fake-token", requester=_compliant_requester(rulesets=[]))
    checks.append(("verify_branch_protection accepts classic-protection-only compliance (missing rulesets is fine)", result["rulesets_checked"] == []))
    checks.append(("verify_branch_protection's success result carries the effective rules it checked", result["effective_rules"] == GOOD_EFFECTIVE_RULES))

    # -- compliant: NO classic protection at all (404), fully covered by an active ruleset --
    result_ruleset_only = verify_branch_protection(
        "nateginn", "artwebsite", "main", "fake-token",
        requester=_compliant_requester(classic_status=404, rulesets=[ACTIVE_RULESET_TARGETING_MAIN_NO_BYPASS]),
    )
    checks.append(("verify_branch_protection accepts ruleset-only compliance with classic protection entirely absent", result_ruleset_only["classic_protection"] is None))
    checks.append(("verify_branch_protection audits the active ruleset targeting main", result_ruleset_only["rulesets_checked"] == [555]))

    # -- compliant: an active ruleset exists but does NOT target this branch -> skipped, not audited --
    other_branch_ruleset_detail = dict(ACTIVE_RULESET_DETAIL_NO_BYPASS, conditions={"ref_name": {"include": ["refs/heads/staging"], "exclude": []}}, bypass_actors=[{"actor_type": "Team"}])
    result_other_branch = verify_branch_protection(
        "nateginn", "artwebsite", "main", "fake-token",
        requester=_endpoint_dispatch_requester(
            [
                (_is_effective_rules, 200, GOOD_EFFECTIVE_RULES),
                (_is_ruleset_detail, 200, other_branch_ruleset_detail),
                (_is_rulesets_list, 200, [ACTIVE_RULESET_TARGETING_MAIN_NO_BYPASS]),
                (_is_classic_protection, 200, good_classic_payload),
            ]
        ),
    )
    checks.append(("verify_branch_protection skips (does not flag) a bypass actor on a ruleset targeting a different branch", result_other_branch["rulesets_checked"] == []))

    # -- compliant: a ruleset with a bypass actor exists but is disabled/evaluate-mode -> skipped, moot --
    for inert_enforcement in ("disabled", "evaluate"):
        inert_ruleset_summary = dict(ACTIVE_RULESET_TARGETING_MAIN_NO_BYPASS, enforcement=inert_enforcement)
        result_inert = verify_branch_protection(
            "nateginn", "artwebsite", "main", "fake-token",
            requester=_compliant_requester(rulesets=[inert_ruleset_summary]),
        )
        checks.append((f"verify_branch_protection does not audit a {inert_enforcement}-enforcement ruleset", result_inert["rulesets_checked"] == []))

    # ---- bucket: bypass actors ----
    bypassable_ruleset_detail = dict(ACTIVE_RULESET_DETAIL_NO_BYPASS, bypass_actors=[{"actor_id": 1, "actor_type": "RepositoryRole"}])
    refused_ruleset_bypass = False
    try:
        verify_branch_protection(
            "nateginn", "artwebsite", "main", "fake-token",
            requester=_endpoint_dispatch_requester(
                [
                    (_is_effective_rules, 200, GOOD_EFFECTIVE_RULES),
                    (_is_ruleset_detail, 200, bypassable_ruleset_detail),
                    (_is_rulesets_list, 200, [ACTIVE_RULESET_TARGETING_MAIN_NO_BYPASS]),
                    (_is_classic_protection, 200, good_classic_payload),
                ]
            ),
        )
    except ValueError as e:
        refused_ruleset_bypass = "bypass actor" in str(e) and "555" in str(e)
    checks.append(("verify_branch_protection refuses when an active ruleset targeting the branch has a bypass actor", refused_ruleset_bypass))

    classic_bypass_payload = dict(good_classic_payload, required_pull_request_reviews={"required_approving_review_count": 1, "bypass_pull_request_allowances": {"users": [{"login": "nateginn"}], "teams": [], "apps": []}})
    refused_classic_bypass = False
    try:
        verify_branch_protection("nateginn", "artwebsite", "main", "fake-token", requester=_compliant_requester(classic=classic_bypass_payload, rulesets=[]))
    except ValueError as e:
        refused_classic_bypass = "classic branch protection" in str(e) and "bypass actor" in str(e)
    checks.append(("verify_branch_protection refuses when classic protection lists a bypass actor", refused_classic_bypass))

    classic_no_admin_enforce = dict(good_classic_payload, enforce_admins={"enabled": False})
    refused_admin_bypass = False
    try:
        verify_branch_protection("nateginn", "artwebsite", "main", "fake-token", requester=_compliant_requester(classic=classic_no_admin_enforce, rulesets=[]))
    except ValueError as e:
        refused_admin_bypass = "enforce_admins" in str(e)
    checks.append(("verify_branch_protection refuses when admins can bypass classic protection", refused_admin_bypass))

    # ---- bucket: permissive rules (effective rules don't cover everything required) ----
    for missing_type, bad_rules in (
        ("non_fast_forward", [r for r in GOOD_EFFECTIVE_RULES if r["type"] != "non_fast_forward"]),
        ("deletion", [r for r in GOOD_EFFECTIVE_RULES if r["type"] != "deletion"]),
    ):
        refused_permissive = False
        try:
            verify_branch_protection("nateginn", "artwebsite", "main", "fake-token", requester=_compliant_requester(effective=bad_rules, rulesets=[]))
        except ValueError as e:
            refused_permissive = missing_type in str(e)
        checks.append((f"verify_branch_protection refuses when the effective rules are missing {missing_type}", refused_permissive))

    no_pr_rule = [r for r in GOOD_EFFECTIVE_RULES if r["type"] != "pull_request"]
    refused_no_pr_rule = False
    try:
        verify_branch_protection("nateginn", "artwebsite", "main", "fake-token", requester=_compliant_requester(effective=no_pr_rule, rulesets=[]))
    except ValueError as e:
        refused_no_pr_rule = "no effective pull_request rule" in str(e)
    checks.append(("verify_branch_protection refuses when no effective pull_request rule exists at all", refused_no_pr_rule))

    zero_review_rules = [dict(r, parameters={"required_approving_review_count": 0}) if r["type"] == "pull_request" else r for r in GOOD_EFFECTIVE_RULES]
    refused_zero_review = False
    try:
        verify_branch_protection("nateginn", "artwebsite", "main", "fake-token", requester=_compliant_requester(effective=zero_review_rules, rulesets=[]))
    except ValueError as e:
        refused_zero_review = "do not require any approving review" in str(e)
    checks.append(("verify_branch_protection refuses when the effective pull_request rule requires 0 reviews", refused_zero_review))

    # ---- bucket: missing rulesets (already exercised above as a compliant case; also confirm a query error on the rulesets list itself fails closed) ----
    refused_rulesets_query_error = False
    try:
        verify_branch_protection(
            "nateginn", "artwebsite", "main", "fake-token",
            requester=_endpoint_dispatch_requester(
                [
                    (_is_effective_rules, 200, GOOD_EFFECTIVE_RULES),
                    (_is_rulesets_list, 500, b"boom"),
                    (_is_classic_protection, 200, good_classic_payload),
                ]
            ),
        )
    except ValueError as e:
        refused_rulesets_query_error = "could not list rulesets" in str(e)
    checks.append(("verify_branch_protection fails closed when the rulesets list query errors", refused_rulesets_query_error))

    # ---- bucket: API failures ----
    refused_effective_rules_error = False
    try:
        verify_branch_protection("nateginn", "artwebsite", "main", "fake-token", requester=_endpoint_dispatch_requester([(_is_effective_rules, 500, b"boom")]))
    except ValueError as e:
        refused_effective_rules_error = "could not read effective rules" in str(e)
    checks.append(("verify_branch_protection fails closed when the effective-rules query errors", refused_effective_rules_error))

    refused_classic_query_error = False
    try:
        verify_branch_protection(
            "nateginn", "artwebsite", "main", "fake-token",
            requester=_endpoint_dispatch_requester([(_is_effective_rules, 200, GOOD_EFFECTIVE_RULES), (_is_classic_protection, 500, b"boom")]),
        )
    except ValueError as e:
        refused_classic_query_error = "classic branch protection check failed" in str(e)
    checks.append(("verify_branch_protection fails closed when the classic-protection query errors (not 404)", refused_classic_query_error))

    refused_ruleset_detail_error = False
    try:
        verify_branch_protection(
            "nateginn", "artwebsite", "main", "fake-token",
            requester=_endpoint_dispatch_requester(
                [
                    (_is_effective_rules, 200, GOOD_EFFECTIVE_RULES),
                    (_is_rulesets_list, 200, [ACTIVE_RULESET_TARGETING_MAIN_NO_BYPASS]),
                    (_is_ruleset_detail, 500, b"boom"),
                    (_is_classic_protection, 200, good_classic_payload),
                ]
            ),
        )
    except ValueError as e:
        refused_ruleset_detail_error = "could not read ruleset 555" in str(e)
    checks.append(("verify_branch_protection fails closed when a ruleset-detail query errors", refused_ruleset_detail_error))

    # ---- bucket: malformed responses ----
    refused_malformed_effective = False
    try:
        verify_branch_protection("nateginn", "artwebsite", "main", "fake-token", requester=_endpoint_dispatch_requester([(_is_effective_rules, 200, {"not": "a list"})]))
    except ValueError as e:
        refused_malformed_effective = "not a JSON array" in str(e)
    checks.append(("verify_branch_protection refuses a non-array effective-rules response", refused_malformed_effective))

    refused_malformed_json = False
    try:
        verify_branch_protection("nateginn", "artwebsite", "main", "fake-token", requester=_endpoint_dispatch_requester([(_is_effective_rules, 200, b"{not valid json")]))
    except ValueError as e:
        refused_malformed_json = "not valid JSON" in str(e)
    checks.append(("verify_branch_protection refuses unparsable JSON from the effective-rules endpoint", refused_malformed_json))

    refused_malformed_rulesets_list = False
    try:
        verify_branch_protection(
            "nateginn", "artwebsite", "main", "fake-token",
            requester=_endpoint_dispatch_requester([(_is_effective_rules, 200, GOOD_EFFECTIVE_RULES), (_is_rulesets_list, 200, {"not": "a list"}), (_is_classic_protection, 200, good_classic_payload)]),
        )
    except ValueError as e:
        refused_malformed_rulesets_list = "not a JSON array" in str(e)
    checks.append(("verify_branch_protection refuses a non-array rulesets-list response", refused_malformed_rulesets_list))

    ruleset_no_id = dict(ACTIVE_RULESET_TARGETING_MAIN_NO_BYPASS)
    ruleset_no_id.pop("id")
    refused_ruleset_no_id = False
    try:
        verify_branch_protection(
            "nateginn", "artwebsite", "main", "fake-token",
            requester=_endpoint_dispatch_requester([(_is_effective_rules, 200, GOOD_EFFECTIVE_RULES), (_is_rulesets_list, 200, [ruleset_no_id]), (_is_classic_protection, 200, good_classic_payload)]),
        )
    except ValueError as e:
        refused_ruleset_no_id = "no id" in str(e)
    checks.append(("verify_branch_protection refuses an active ruleset summary with no id", refused_ruleset_no_id))

    malformed_bypass_detail = dict(ACTIVE_RULESET_DETAIL_NO_BYPASS, bypass_actors="not-a-list")
    refused_malformed_bypass = False
    try:
        verify_branch_protection(
            "nateginn", "artwebsite", "main", "fake-token",
            requester=_endpoint_dispatch_requester(
                [
                    (_is_effective_rules, 200, GOOD_EFFECTIVE_RULES),
                    (_is_rulesets_list, 200, [ACTIVE_RULESET_TARGETING_MAIN_NO_BYPASS]),
                    (_is_ruleset_detail, 200, malformed_bypass_detail),
                    (_is_classic_protection, 200, good_classic_payload),
                ]
            ),
        )
    except ValueError as e:
        refused_malformed_bypass = "malformed bypass_actors" in str(e)
    checks.append(("verify_branch_protection refuses a ruleset with a non-list bypass_actors field", refused_malformed_bypass))

    malformed_conditions_detail = dict(ACTIVE_RULESET_DETAIL_NO_BYPASS, conditions="not-an-object")
    refused_malformed_conditions = False
    try:
        verify_branch_protection(
            "nateginn", "artwebsite", "main", "fake-token",
            requester=_endpoint_dispatch_requester(
                [
                    (_is_effective_rules, 200, GOOD_EFFECTIVE_RULES),
                    (_is_rulesets_list, 200, [ACTIVE_RULESET_TARGETING_MAIN_NO_BYPASS]),
                    (_is_ruleset_detail, 200, malformed_conditions_detail),
                    (_is_classic_protection, 200, good_classic_payload),
                ]
            ),
        )
    except ValueError as e:
        refused_malformed_conditions = "no parseable conditions" in str(e)
    checks.append(("verify_branch_protection refuses a branch-targeted ruleset with malformed conditions, rather than silently skipping it", refused_malformed_conditions))

    # ---- _ruleset_applies_to_branch, tested directly ----
    checks.append(("_ruleset_applies_to_branch: exact ref match", _ruleset_applies_to_branch({"target": "branch", "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}}}, "main") is True))
    checks.append(("_ruleset_applies_to_branch: ~ALL matches any branch", _ruleset_applies_to_branch({"target": "branch", "conditions": {"ref_name": {"include": ["~ALL"], "exclude": []}}}, "seo/prop-1") is True))
    checks.append(("_ruleset_applies_to_branch: ~DEFAULT_BRANCH matches the assumed default (main)", _ruleset_applies_to_branch({"target": "branch", "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}}}, "main") is True))
    checks.append(("_ruleset_applies_to_branch: exclude overrides a matching include", _ruleset_applies_to_branch({"target": "branch", "conditions": {"ref_name": {"include": ["~ALL"], "exclude": ["refs/heads/main"]}}}, "main") is False))
    checks.append(("_ruleset_applies_to_branch: non-branch target is always False", _ruleset_applies_to_branch({"target": "tag", "conditions": {}}, "main") is False))
    conditions_missing_refused = False
    try:
        _ruleset_applies_to_branch({"target": "branch"}, "main")
    except ValueError as e:
        conditions_missing_refused = "no parseable conditions" in str(e)
    checks.append(("_ruleset_applies_to_branch raises (never silently False) on missing conditions for a branch target", conditions_missing_refused))

    # create_pull_request
    pr_calls = []

    def pr_poster(url, headers, body):
        pr_calls.append((url, headers, json.loads(body.decode("utf-8"))))
        return 201, "Created", json.dumps({"number": 42, "html_url": "https://github.com/nateginn/artwebsite/pull/42", "node_id": "PR_kwAaaa", "head": {"sha": "deadbeef"}}).encode("utf-8")

    pr = create_pull_request("nateginn", "artwebsite", "seo/prop-1", "main", "title", "body", "fake-token", poster=pr_poster)
    checks.append(("create_pull_request posts to the repo's pulls endpoint", pr_calls[0][0].endswith("/repos/nateginn/artwebsite/pulls")))
    checks.append(("create_pull_request sends head/base in the JSON body", pr_calls[0][2]["head"] == "seo/prop-1" and pr_calls[0][2]["base"] == "main"))
    checks.append(("create_pull_request returns number/html_url/node_id", pr == {"number": 42, "html_url": "https://github.com/nateginn/artwebsite/pull/42", "node_id": "PR_kwAaaa", "head_sha": "deadbeef"}))

    def pr_failure_poster(url, headers, body):
        return 422, "Unprocessable Entity", json.dumps({"message": "A pull request already exists"}).encode("utf-8")

    pr_refused = False
    try:
        create_pull_request("nateginn", "artwebsite", "seo/prop-1", "main", "title", "body", "fake-token", poster=pr_failure_poster)
    except ValueError as e:
        pr_refused = "already exists" in str(e)
    checks.append(("create_pull_request surfaces a 422 (e.g. duplicate PR) as a clean ValueError", pr_refused))

    # enable_auto_merge
    def auto_merge_poster(url, headers, body):
        parsed = json.loads(body.decode("utf-8"))
        checks.append(("enable_auto_merge posts to the GraphQL endpoint", url == "https://api.github.com/graphql"))
        checks.append(("enable_auto_merge passes the pull request node id as a variable", parsed["variables"]["pullRequestId"] == "PR_kwAaaa"))
        return 200, "OK", json.dumps({"data": {"enablePullRequestAutoMerge": {"pullRequest": {"autoMergeRequest": {"enabledAt": "2026-07-30T00:00:00Z"}}}}}).encode("utf-8")

    auto_merge = enable_auto_merge("PR_kwAaaa", "fake-token", poster=auto_merge_poster)
    checks.append(("enable_auto_merge reports enabled=True with a timestamp", auto_merge["enabled"] is True and auto_merge["enabled_at"] == "2026-07-30T00:00:00Z"))

    def graphql_error_poster(url, headers, body):
        return 200, "OK", json.dumps({"data": None, "errors": [{"message": "Pull request Auto merge is not allowed for this repository"}]}).encode("utf-8")

    auto_merge_refused = False
    try:
        enable_auto_merge("PR_kwAaaa", "fake-token", poster=graphql_error_poster)
    except ValueError as e:
        auto_merge_refused = "GraphQL request returned errors" in str(e)
    checks.append(("enable_auto_merge surfaces a GraphQL-level error cleanly", auto_merge_refused))

    bad_merge_method_refused = False
    try:
        enable_auto_merge("PR_kwAaaa", "fake-token", merge_method="NUKE")
    except ValueError as e:
        bad_merge_method_refused = "merge_method" in str(e)
    checks.append(("enable_auto_merge rejects an unknown merge_method before making any request", bad_merge_method_refused))

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__" and "--verify" in sys.argv:
    _self_test()

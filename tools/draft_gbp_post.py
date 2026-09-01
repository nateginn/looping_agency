# GBP Posts plan Phase 4 - draft_copy.py's counterpart for gbp-post-draft proposals.
#
# Same authorship model as draft_copy.py/draft_link.py: a human-supervised Claude Code
# session drafts the actual Post text externally; this tool only validates and persists it.
# No network calls, no calls to Google's API (that is publish_gbp_post.py, Phase 6, unbuilt),
# no writes outside this proposal's own file.
#
# The API-contract constants below (character limit, CTA enum, topic-type enum) are sourced
# from Google's public Business Profile API documentation as read 2026-08-31 - NOT verified
# against a live API response, because no posting-capable credential exists yet (Phase 0).
# The plan is explicit that these must be re-confirmed against the live API reference before
# this tool is ever trusted for a real --live publish - see MAX_SUMMARY_CHARS's own comment.
# This tool refuses any post_type other than STANDARD outright, rather than guess at EVENT's/
# OFFER's exact required-field shape from documentation alone.
import os
import re
import sys
from datetime import datetime, timezone

import yaml

try:
    from .lib.event_log import append_event, build_secret_map, sync_proposal_projection
    from .lib.gbp_constraints import (
        assert_location_publishable,
        build_source_manifest,
        find_contradictions,
        manifest_hash,
        offered_and_promotable,
        parse_art_yaml,
        parse_gbp_profile_confirmations,
    )
    from .lib.lock import acquire_lock, release_lock
    from .lib.proposals import atomic_write_json, list_proposals, load_json, loop_dir_for, pending_dir_for, proposal_path
    from .run_loop import _read_lock_ttl_minutes_unsafe
    from .spec_validate import extract_frontmatter
except ImportError:
    from lib.event_log import append_event, build_secret_map, sync_proposal_projection
    from lib.gbp_constraints import (
        assert_location_publishable,
        build_source_manifest,
        find_contradictions,
        manifest_hash,
        offered_and_promotable,
        parse_art_yaml,
        parse_gbp_profile_confirmations,
    )
    from lib.lock import acquire_lock, release_lock
    from lib.proposals import atomic_write_json, list_proposals, load_json, loop_dir_for, pending_dir_for, proposal_path
    from run_loop import _read_lock_ttl_minutes_unsafe
    from spec_validate import extract_frontmatter

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(THIS_DIR)
PROJECTS_ROOT = os.path.join(WORKSPACE_ROOT, "projects")
MMC_REGISTRY_ROOT = os.path.join(os.path.dirname(WORKSPACE_ROOT), "MMC", "registry", "clients")

DRAFTABLE_ACTIONS = {"gbp-post-draft"}
SUPPORTED_POST_TYPES = {"STANDARD"}  # EVENT/OFFER refused outright - see module docstring

# Sourced from Google's public Business Profile API docs, read 2026-08-31 - NOT independently
# verified against a live API response (no credential exists yet). Re-confirm before --live
# publishing is ever trusted (plan Phase 4/6). 1500 is the widely-documented figure for a
# Local Post summary; if the live API disagrees, this constant is wrong and must be corrected
# from the real API reference, not from this comment.
MAX_SUMMARY_CHARS = 1500

# GET_OFFER is documented as deprecated - deliberately excluded.
CTA_ENUM = {"BOOK", "ORDER", "SHOP", "LEARN_MORE", "SIGN_UP", "CALL"}
CTA_REQUIRES_URL = CTA_ENUM - {"CALL"}  # CALL uses the profile's own phone number, no URL

# Deliberately conservative, deterministic substring checks - not an NLP classifier. A false
# positive (a legitimate phrase incidentally matching) is refused and can be reworded and
# resubmitted; a false negative is the failure mode this exists to avoid, so the list errs
# toward catching more than a semantic model might. Regex, case-insensitive, \b word
# boundaries used wherever a short/ambiguous token could otherwise match inside an unrelated
# word (e.g. a bare "lac" substring inside "placebo"). A Codex review (2026-08-31) found the
# original plain-substring, narrow phrase lists trivially bypassed by minor rewordings
# ("clinically proven treatment relieves chronic pain", "today only", "licensed acupuncture
# specialist", "prenatal chiropractic care" with no literal "pediatric", "similar to ART") -
# every one of those is now covered below, plus their own regression tests.
# [\s-]+ rather than \s+ throughout multi-word patterns - a Codex review (2026-08-31 follow-
# up) demonstrated hyphenated rewordings ("clinically-proven", "limited-time", "today-only")
# evading whitespace-only regexes.
PHI_FORBIDDEN_PATTERNS = (
    r"\bcures?\b", r"\bcured\b", r"\bguarante\w*\b",  # guarantee/guarantees/guaranteed/guaranteeing
    r"100\s*%\s*effective", r"\bmiracle\b", r"pain[\s-]*free[\s-]+forever",
    r"eliminates?[\s-]+all\b", r"\beliminates?\b", r"no[\s-]+side[\s-]+effects", r"risk[\s-]*free",
    r"(clinically|medically|scientifically)[\s-]+(proven|validated)", r"proven[\s-]+(effective|to[\s-]+(relieve|cure|eliminate|treat|fix))",
    r"relieves?[\s-]+(all|chronic)\b", r"\bheals?[\s-]+(all|chronic)\b", r"permanent[\s-]+relief", r"instant[\s-]+relief",
    r"complete[\s-]+recovery", r"fully[\s-]+recovered",
)
URGENCY_FORBIDDEN_PATTERNS = (
    r"act[\s-]+now", r"limited[\s-]+time", r"\bhurry\b", r"don'?t[\s-]+wait", r"call[\s-]+now[\s-]+or",
    r"offer[\s-]+expires", r"today[\s-]+only", r"last[\s-]+chance", r"while[\s-]+supplies[\s-]+last",
    r"while[\s-]+it[\s-]+lasts", r"expires?[\s-]+soon", r"don'?t[\s-]+miss[\s-]+out", r"final[\s-]+hours",
    r"book[\s-]+now[\s-]+or", r"book[\s-]+immediately", r"filling[\s-]+(up[\s-]+)?fast",
    r"this[\s-]+(week|month|weekend)[\s-]+only",
)
BEFORE_AFTER_FORBIDDEN_PATTERNS = (r"before[\s-]+and[\s-]+after", r"before\s*/\s*after")

# Forbidden regardless of context - gbp-profiles.md's / MMC registry's explicit wording rules.
# [\s-]+ throughout - a Codex review (2026-08-31, third round) demonstrated hyphenated forms
# ("Active-Release Technique", "similar-to-ART", "licensed-acupuncturist") evading the
# whitespace-only versions of these same patterns.
TRADEMARK_FORBIDDEN_PATTERNS = (
    r"active[\s-]+release[\s-]+techniques?", r"a\.r\.t\.?\s*®", r"art\s*®", r"similar[\s-]+to[\s-]+art\b",
)
WRONG_ACUPUNCTURE_CREDENTIAL_PATTERNS = (
    r"l\.?\s*ac\.?\b", r"licensed[\s-]+acupuncturist", r"licensed[\s-]+acupuncture",
)
CLOSED_LOCATION_FORBIDDEN_NAMES = (r"\bthornton\b",)

# This finite, deterministic pattern list is a backstop, not the primary safety mechanism -
# it cannot be made exhaustive against arbitrary creative rewording (a genuinely adversarial
# rephrasing will eventually find a gap a regex list doesn't cover; three rounds of Codex
# review each found new ones). The plan's own authorship model is the actual first line of
# defense: "a human-supervised Claude Code session drafts the actual Post text" - the person
# or session writing the summary is expected to read gbp-profiles.md/art.yaml's constraints
# directly, not rely on this lint to catch every possible violation after the fact. Treat a
# clean lint result as "no known-bad pattern detected", never as "this summary is safe".

# Every service-name trigger a summary might use to reference the service, mapped to the
# art.yaml service_constraints name it should be checked against - deliberately broader than
# "the first word of the service's own name" (the original version only checked "pediatric",
# missing "prenatal chiropractic care" entirely).
SERVICE_MENTION_TRIGGERS = {
    "Pediatric and prenatal chiropractic": (r"\bpediatric\b", r"\bprenatal\b"),
    "Custom orthotics": (r"\borthotics?\b",),
}


def _regex_matches(text, patterns):
    import re
    return [p for p in patterns if re.search(p, text, re.IGNORECASE)]


def _mentions_shockwave(text):
    import re
    return re.search(r"shock\s*wave|eswt|acoustic[\s-]+wave", text, re.IGNORECASE) is not None


def _is_radial_shockwave_claim(text):
    """A compound check, not a simple pattern list: the clinic only ever runs FOCUSED
    (fESWT) shockwave, so "radial" appearing anywhere near a shockwave/ESWT mention is the
    violation - not the bare word "radial" alone (which could appear in an unrelated
    anatomical context, e.g. "radial nerve"), and not "shockwave" alone (which is the
    clinic's real, permitted service). Both sides broadened after a Codex review
    (2026-08-31 follow-up) demonstrated "radial acoustic wave therapy" (no literal "shock
    wave"/"eswt") and "rESWT" (no literal "radial") both evading the original narrower check."""
    import re
    has_radial = re.search(r"\bradial\b", text, re.IGNORECASE) is not None
    has_r_eswt_abbreviation = re.search(r"\br[\s-]?eswt\b", text, re.IGNORECASE) is not None
    return has_r_eswt_abbreviation or (has_radial and _mentions_shockwave(text))


def _now_iso(now=None):
    now = now or datetime.now(timezone.utc)
    return now.isoformat().replace("+00:00", "Z")


def _load_repo_path(project):
    project_path = os.path.join(PROJECTS_ROOT, project, "project.md")
    with open(project_path, "r", encoding="utf-8") as f:
        source = f.read()
    return (yaml.safe_load(extract_frontmatter(source)) or {}).get("repo")


def _load_project_domain(project):
    project_path = os.path.join(PROJECTS_ROOT, project, "project.md")
    with open(project_path, "r", encoding="utf-8") as f:
        source = f.read()
    return (yaml.safe_load(extract_frontmatter(source)) or {}).get("domain")


def lint_medical_and_urgency_claims(summary):
    """Deterministic, LLM-free PHI/medical-claims + manufactured-urgency lint - the same
    discipline MMC's registry/clients/art.yaml already establishes for shockwave copy
    (mechanism-only, no efficacy claims), extended here rather than reinvented. Returns a
    dict of category -> matched forbidden phrases (empty dict if clean)."""
    flags = {}
    for category, patterns in (
        ("medical_claim", PHI_FORBIDDEN_PATTERNS),
        ("manufactured_urgency", URGENCY_FORBIDDEN_PATTERNS),
        ("before_after_claim", BEFORE_AFTER_FORBIDDEN_PATTERNS),
    ):
        hits = _regex_matches(summary, patterns)
        if hits:
            flags[category] = hits
    return flags


def lint_brand_and_wording_constraints(summary, location, art_facts, profile_facts):
    """Content checks against gbp-profiles.md's explicit wording rules and art.yaml's
    trademark/location/service constraints - a hard gate, not a soft rule, because it is a
    structural (regex) check independent of what the drafting session intended. Returns a
    dict of category -> details (empty dict if clean)."""
    flags = {}

    trademark_hits = _regex_matches(summary, TRADEMARK_FORBIDDEN_PATTERNS)
    if trademark_hits:
        flags["trademark_claim"] = trademark_hits

    wrong_credential_hits = _regex_matches(summary, WRONG_ACUPUNCTURE_CREDENTIAL_PATTERNS)
    if wrong_credential_hits:
        flags["wrong_acupuncture_credential"] = wrong_credential_hits

    if _is_radial_shockwave_claim(summary):
        flags["radial_shockwave_claim"] = True
    elif _mentions_shockwave(summary):
        # A TRUE claim ("focused"/"fESWT") still needs POSITIVE, currently-confirmed evidence
        # from BOTH sources before it may be asserted - the plan's "unconfirmed facts are
        # never safe to assert" rule applies even when the claim happens to be correct. A
        # Codex review (2026-08-31, two follow-up rounds) found several ways the original
        # single-sidecar-lookup check could still be satisfied wrongly:
        #  - a confirmed sidecar fact whose value is null/empty (satisfied "confirmed" alone);
        #  - relying on the sidecar ALONE with no cross-check against art.yaml's own
        #    service_constraints entry for this exact location;
        #  - the text naming a DIFFERENT location than the structured `location` target (a
        #    typo'd or mismatched claim would pass because only the target was ever checked).
        # Every one of these is now required to line up: the sidecar fact must be confirmed
        # AND carry a real value, art.yaml's "Shockwave therapy" constraint must independently
        # list this exact location with a device_type of its own, and the summary must not
        # name any OTHER configured location (which would make it ambiguous or simply wrong
        # about which clinic the claim describes).
        other_location_hits = [
            loc["name"] for loc in art_facts.get("locations") or []
            if loc["name"] != location and re.search(rf"\b{re.escape(loc['name'])}\b", summary, re.IGNORECASE)
        ]
        if other_location_hits:
            flags["shockwave_claim_names_other_location"] = {"target_location": location, "other_locations_mentioned": other_location_hits}
        else:
            sidecar_fact = next(
                (f for f in profile_facts.get("facts") or [] if f.get("kind") == "shockwave_device_type" and f.get("location") == location),
                None,
            )
            sidecar_ok = sidecar_fact is not None and sidecar_fact.get("confirmed") and sidecar_fact.get("value")
            # Reuses offered_and_promotable's normalized (case/whitespace-insensitive) lookup
            # rather than a raw exact-string search - a Codex review (2026-08-31, fourth
            # round) found the equivalent exact-match pattern in offered_and_promotable itself
            # would silently stop matching a service name that's a case/whitespace variant of
            # the canonical constant, with no error anywhere in the chain; the same class of
            # bug applied here too before this fix.
            _shockwave_offered, _shockwave_promotable, shockwave_constraint = offered_and_promotable(art_facts, "Shockwave therapy")
            art_yaml_ok = bool(
                shockwave_constraint
                and shockwave_constraint.get("device_type")
                and location in (shockwave_constraint.get("locations") or [])
            )
            if not (sidecar_ok and art_yaml_ok):
                flags["unconfirmed_shockwave_claim"] = {
                    "location": location,
                    "sidecar_confirmed_with_value": sidecar_ok,
                    "art_yaml_corroborates_this_location": art_yaml_ok,
                }

    closed_location_hits = _regex_matches(summary, CLOSED_LOCATION_FORBIDDEN_NAMES)
    if closed_location_hits:
        flags["closed_location_mentioned"] = closed_location_hits

    if _regex_matches(summary, (r"\bspinal\s+decompression\b",)):
        _offered, _promotable, constraint = offered_and_promotable(art_facts, "Spinal decompression")
        allowed_locations = (constraint or {}).get("locations") or []
        if location not in allowed_locations:
            flags["spinal_decompression_wrong_location"] = {"location": location, "allowed": allowed_locations}

    for service_name, triggers in SERVICE_MENTION_TRIGGERS.items():
        if _regex_matches(summary, triggers):
            offered, promotable, _ = offered_and_promotable(art_facts, service_name)
            # Two distinct violations, both hard failures: a service the clinic doesn't offer
            # AT ALL being claimed (offered is False - "Custom orthotics" in the real
            # art.yaml), and a service it offers but has decided not to promote (offered is
            # True, promotable is False - "Pediatric and prenatal chiropractic"). A Codex
            # review (2026-08-31 follow-up) found the original check only looked for the
            # second shape, so "Custom orthotics" (offered: false) was never flagged at all.
            if offered is False:
                flags.setdefault("service_not_offered", []).append(service_name)
            elif offered is True and promotable is False:
                flags.setdefault("not_promotable_service", []).append(service_name)

    return flags


def _is_own_canonical_url(url, domain):
    """Same-origin, structurally - not "is this string present in a list a human typed",
    which a Codex review (2026-08-31) correctly noted would trust an external URL if one
    were ever mistakenly added to spec.md's priority_pages. Requires https, an exact hostname
    match against this project's own domain (bare or www.), no embedded credentials, no
    non-default port, and no query string or fragment (a tracking-parameter or fragment-laden
    variant of an otherwise-legitimate URL is not "canonical")."""
    from urllib.parse import urlsplit

    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https":
        return False
    if "@" in (parts.netloc or ""):
        return False  # embedded userinfo (user:pass@host)
    if parts.port is not None:
        return False
    hostname = (parts.hostname or "").lower()
    domain = (domain or "").lower()
    if not domain or hostname not in (domain, f"www.{domain}"):
        return False
    if parts.query or parts.fragment:
        return False
    return True


def _allowed_urls(project, spec_priority_pages, domain):
    urls = {url for url in (spec_priority_pages or []) if _is_own_canonical_url(url, domain)}
    if domain:
        urls.add(f"https://{domain}/")
    return urls


def validate_gbp_post(implementation, location, art_facts, profile_facts, allowed_urls, existing_summaries, domain=None):
    """Raises ValueError on any violation - never returns a partial/best-guess result.
    `existing_summaries` is the LOCAL duplicate-content check (every other proposal's
    normalized summary in this loop's pending/) - an interim stand-in for the plan's real
    check, which requires fetching actual remote post history (Phase 6, unbuilt)."""
    post_type = implementation.get("post_type")
    if post_type not in SUPPORTED_POST_TYPES:
        raise ValueError(
            f'draft_gbp_post.py: post_type "{post_type}" is not supported - only STANDARD is '
            "implemented today; EVENT/OFFER require confirming their exact required-field shape "
            "against the live API reference before being trusted (plan Phase 4)"
        )

    summary = implementation.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("draft_gbp_post.py: summary must be a non-empty string")
    if len(summary) > MAX_SUMMARY_CHARS:
        raise ValueError(f"draft_gbp_post.py: summary is {len(summary)} characters, over the {MAX_SUMMARY_CHARS}-character limit (see MAX_SUMMARY_CHARS's own caveat)")

    normalized = " ".join(summary.split()).casefold()
    if normalized in existing_summaries:
        raise ValueError("draft_gbp_post.py: this summary text is identical to another proposal already in this loop's pending/ (local duplicate-content check - see the plan's Phase 6 note on the real remote check)")

    cta = implementation.get("call_to_action") or {}
    action_type = cta.get("action_type")
    if action_type not in CTA_ENUM:
        raise ValueError(f'draft_gbp_post.py: call_to_action.action_type "{action_type}" is not a supported CTA (expected one of {sorted(CTA_ENUM)})')
    if action_type in CTA_REQUIRES_URL:
        url = cta.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ValueError(f'draft_gbp_post.py: call_to_action.url is required and must be absolute for action_type "{action_type}"')
        if url not in allowed_urls:
            raise ValueError(f'draft_gbp_post.py: call_to_action.url "{url}" is not in this project\'s allowlist of canonical URLs - never an arbitrary domain')
        # Defense in depth: re-verify same-origin structurally at the point of use too, not
        # only via set membership in a precomputed allowlist that some other code path built
        # (Codex review, 2026-08-31) - a URL that is in allowed_urls but fails this check
        # would indicate the allowlist itself was built incorrectly, which this refuses to
        # trust silently.
        if domain and not _is_own_canonical_url(url, domain):
            raise ValueError(f'draft_gbp_post.py: call_to_action.url "{url}" passed the allowlist but failed same-origin re-verification - refusing out of caution')
    elif cta.get("url"):
        raise ValueError(f'draft_gbp_post.py: call_to_action.url must not be set for action_type "{action_type}" (uses the profile\'s own phone number)')

    assert_location_publishable(art_facts, location)

    phi_flags = lint_medical_and_urgency_claims(summary)
    if phi_flags:
        raise ValueError(f"draft_gbp_post.py: summary fails the medical-claims/urgency lint: {phi_flags}")

    brand_flags = lint_brand_and_wording_constraints(summary, location, art_facts, profile_facts)
    if brand_flags:
        raise ValueError(f"draft_gbp_post.py: summary fails the brand/wording constraint check: {brand_flags}")


def draft_gbp_post(project, loop, proposal_id, implementation, drafted_by, repo_path=None, art_yaml_path=None, now=None):
    loop_dir = loop_dir_for(project, loop)
    pending_dir = pending_dir_for(project, loop)
    proposal_pathname = proposal_path(pending_dir, proposal_id)
    if not os.path.exists(proposal_pathname):
        raise ValueError(f"draft_gbp_post.py: proposal {proposal_id} not found")

    lock_ttl_minutes = _read_lock_ttl_minutes_unsafe(os.path.join(loop_dir, "spec.md"))
    lock = acquire_lock(loop_dir, max_run_duration_minutes=lock_ttl_minutes, runs_dir=os.path.join(loop_dir, "runs"), now=now)
    if not lock["acquired"]:
        raise ValueError(f'draft_gbp_post.py: REFUSED - run lock active for {project}/{loop} - {lock["reason"]}')

    try:
        proposal = load_json(proposal_pathname)
        # Event-sourced current status, not the cached JSON's own `status` field (Codex
        # review, 2026-09-01: a crash between approve_gbp_post.py's gbp_proposal_approved
        # event append and its own proposal-cache rewrite would otherwise leave this cache
        # reading stale "draft" while the event log already says "approved" - drafting new
        # copy against that stale cache would silently diverge the approved lock's hash from
        # the proposal's actual current implementation, with no error anywhere).
        current_status = sync_proposal_projection(loop_dir, proposal_id, fail_closed=True)["status"]
        is_revision = current_status == "review-revision-needed"
        if current_status not in ("draft", "review-revision-needed"):
            raise ValueError(
                f'draft_gbp_post.py: proposal {proposal_id} has event-sourced status "{current_status}", not "draft" '
                'or "review-revision-needed" - only a fresh draft, or a proposal flagged for a correctable revision, '
                "may receive new implementation copy"
            )
        if proposal.get("action_type") not in DRAFTABLE_ACTIONS:
            raise ValueError(f'draft_gbp_post.py: action_type "{proposal.get("action_type")}" has no draftable GBP Post copy')

        target = proposal.get("target") or {}
        location = target.get("location")
        if not location:
            raise ValueError(f"draft_gbp_post.py: proposal {proposal_id} has no target.location")

        repo_path = repo_path or _load_repo_path(project)
        art_yaml_path = art_yaml_path or os.path.join(MMC_REGISTRY_ROOT, f"{project}.yaml")
        confirmations_path = os.path.join(loop_dir, "gbp-profile-confirmations.yaml")
        gbp_profiles_md_path = os.path.join(PROJECTS_ROOT, project, "gbp-profiles.md")

        # All sources re-read and re-hashed fresh from disk right now - never a cached
        # projection from an earlier call in this process (plan Phase 4). Deliberately no
        # os.path.exists guard around gbp_profiles_md_path: a missing file must raise
        # (fail closed) here, not silently disable the sidecar-vs-prose verification the way
        # passing None used to (Codex review, 2026-08-31 follow-up).
        manifest, read_at, art_facts, profile_facts = build_source_manifest(
            os.path.join(PROJECTS_ROOT, project), art_yaml_path, confirmations_path,
            gbp_profiles_md_path, now=now,
        )
        contradictions = find_contradictions(art_facts, profile_facts)
        if contradictions:
            raise ValueError(
                f"draft_gbp_post.py: REFUSED - gbp-profiles.md and {os.path.basename(art_yaml_path)} disagree on a "
                f"confirmed fact, and this tool never tie-breaks between them: {contradictions}"
            )

        domain = _load_project_domain(project)
        spec_path = os.path.join(loop_dir, "spec.md")
        spec = yaml.safe_load(extract_frontmatter(open(spec_path, "r", encoding="utf-8").read())) or {}
        allowed_urls = _allowed_urls(project, spec.get("priority_pages"), domain)

        existing_summaries = set()
        for other in list_proposals(pending_dir):
            if other.get("id") == proposal_id:
                continue
            other_summary = (other.get("implementation") or {}).get("summary")
            if isinstance(other_summary, str):
                existing_summaries.add(" ".join(other_summary.split()).casefold())

        validate_gbp_post(implementation, location, art_facts, profile_facts, allowed_urls, existing_summaries, domain=domain)

        implementation_before = proposal.get("implementation")
        implementation_after = dict(implementation)
        implementation_after.update({
            "drafted_at": _now_iso(now),
            "drafted_by": drafted_by,
            "constraint_manifest": manifest,
            "constraint_manifest_hash": manifest_hash(manifest),
            "constraint_manifest_read_at": read_at,
        })

        if not is_revision:
            proposal["implementation"] = implementation_after
            atomic_write_json(proposal_pathname, proposal)
            return proposal

        secret_map = build_secret_map(repo_path, spec.get("credential_aliases"))
        append_event(
            loop_dir, "proposal_revised", project=project, loop=loop, proposal_id=proposal_id,
            action_type=proposal.get("action_type"), target_page=None, keyword=None,
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
    import json

    args = sys.argv[1:]
    if len(args) < 4:
        print("usage: python tools/draft_gbp_post.py <project> <loop> <proposal-id> <implementation-json> [--by <name>]", file=sys.stderr)
        sys.exit(2)
    project, loop, proposal_id, implementation_json = args[0], args[1], args[2], args[3]
    drafted_by = "claude"
    if "--by" in args:
        idx = args.index("--by")
        if idx + 1 < len(args):
            drafted_by = args[idx + 1]
    try:
        implementation = json.loads(implementation_json)
        proposal = draft_gbp_post(project, loop, proposal_id, implementation, drafted_by)
        print(f'drafted {proposal["id"]}: {proposal["implementation"].get("summary")!r}')
    except Exception as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)


def _self_test():
    import json
    import shutil
    import tempfile

    from lib.proposals import write_proposal

    project = "_draft-gbp-post-selftest-tmp"
    loop = "gbp"
    project_dir = os.path.join(PROJECTS_ROOT, project)
    loop_dir = os.path.join(project_dir, "loops", loop)
    pending_dir = os.path.join(loop_dir, "pending")
    shutil.rmtree(project_dir, ignore_errors=True)
    os.makedirs(pending_dir, exist_ok=True)

    with open(os.path.join(project_dir, "project.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("---\nslug: _draft-gbp-post-selftest-tmp\ndomain: example.invalid\nrepo: null\n---\n")
    with open(os.path.join(loop_dir, "spec.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("---\nversion: 1\nloop: gbp\npriority_pages:\n  - https://example.invalid/massage/\n---\n")

    tmp = tempfile.mkdtemp(prefix="draft-gbp-post-selftest-")
    art_yaml_path = os.path.join(tmp, "example.yaml")
    checks = []
    art_yaml_content = """
services_and_locations:
  locations:
    - name: Greeley
      status: active
      street: "1 Test St"
      street_confirmed: "2026-08-30"
      hours: "Mon-Fri 08:00-18:00"
      hours_confirmed: "2026-08-30"
    - name: Denver
      status: active
      street: "2 Test Ave"
      street_confirmed: "2026-08-30"
      hours: "Mon-Fri 08:00-17:00"
      hours_confirmed: "2026-08-30"
    - name: UNC Campus
      status: seasonal_inactive
      street: "3 Test Way"
      street_confirmed: "2026-08-30"
  former_locations:
    - name: Thornton
  service_constraints:
  - service: "Active Release Technique (ART)"
    offered: false
  - service: "Spinal decompression"
    offered: true
    locations: ["Denver"]
  - service: "Pediatric and prenatal chiropractic"
    offered: true
    promote: false
  - service: "Shockwave therapy"
    offered: true
    locations: ["Greeley", "Denver"]
    device_type: "focused (fESWT)"
  - service: "Custom orthotics"
    offered: false
"""
    # A variant with Greeley removed from the Shockwave therapy constraint's locations -
    # simulates a sidecar-confirmed claim with no independent art.yaml corroboration for the
    # exact location being claimed.
    art_yaml_content_no_shockwave_location = art_yaml_content.replace('locations: ["Greeley", "Denver"]\n    device_type', 'locations: ["Denver"]\n    device_type')
    try:
        with open(art_yaml_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(art_yaml_content)
        profiles_md_path = os.path.join(project_dir, "gbp-profiles.md")
        with open(profiles_md_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("# gbp-profiles.md fixture\n\nShockwave devices are FOCUSED (fESWT), never radial.\n")
        import hashlib
        prose_hash = hashlib.sha256(open(profiles_md_path, encoding="utf-8").read().encode("utf-8")).hexdigest()
        with open(os.path.join(loop_dir, "gbp-profile-confirmations.yaml"), "w", encoding="utf-8", newline="\n") as f:
            f.write(f"""
source_content_hash: "{prose_hash}"
facts:
  - id: greeley-shockwave
    kind: shockwave_device_type
    location: Greeley
    value: "focused (fESWT)"
    confirmed_date: "2026-08-30"
""")

        def _seed(proposal_id, location="Greeley", status="draft"):
            write_proposal(pending_dir, {
                "id": proposal_id, "action_type": "gbp-post-draft", "status": status,
                "target": {"location": location, "topic": "some-topic"},
            })
            # draft_gbp_post() now reads its status gate from the event-sourced projection,
            # not this cached JSON's own `status` field - a seeded proposal needs matching
            # event history or every call below would see status=None and refuse regardless
            # of what the JSON says.
            append_event(loop_dir, "proposal_created", project=project, loop=loop, proposal_id=proposal_id, action_type="gbp-post-draft", resulting_proposal_status=status)

        good_impl = {
            "post_type": "STANDARD",
            "summary": "Now offering deep tissue massage at our Greeley clinic - book online today.",
            "call_to_action": {"action_type": "LEARN_MORE", "url": "https://example.invalid/massage/"},
        }
        _seed("prop-good")
        result = draft_gbp_post(project, loop, "prop-good", good_impl, "claude-test", art_yaml_path=art_yaml_path)
        checks.append(("a valid post drafts successfully", result["implementation"]["summary"] == good_impl["summary"]))
        checks.append(("constraint_manifest is recorded", "constraint_manifest_hash" in result["implementation"]))
        checks.append(("constraint_manifest_read_at is tracked separately from the hash", "constraint_manifest_read_at" in result["implementation"]))

        threw_bad_post_type = False
        _seed("prop-bad-type")
        try:
            draft_gbp_post(project, loop, "prop-bad-type", {**good_impl, "post_type": "EVENT"}, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_bad_post_type = "not supported" in str(e)
        checks.append(("EVENT/OFFER post types are refused outright", threw_bad_post_type))

        threw_too_long = False
        _seed("prop-too-long")
        try:
            draft_gbp_post(project, loop, "prop-too-long", {**good_impl, "summary": "x" * 1501}, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_too_long = "character limit" in str(e)
        checks.append(("a summary over the character limit is rejected", threw_too_long))

        threw_bad_cta = False
        _seed("prop-bad-cta")
        try:
            draft_gbp_post(project, loop, "prop-bad-cta", {**good_impl, "summary": "Unique summary for the bad-CTA test.", "call_to_action": {"action_type": "GET_OFFER", "url": "https://example.invalid/"}}, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_bad_cta = "not a supported CTA" in str(e)
        checks.append(("the deprecated GET_OFFER CTA is rejected", threw_bad_cta))

        threw_bad_url = False
        _seed("prop-bad-url")
        try:
            draft_gbp_post(project, loop, "prop-bad-url", {**good_impl, "summary": "Unique summary for the bad-URL test.", "call_to_action": {"action_type": "LEARN_MORE", "url": "https://competitor.invalid/"}}, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_bad_url = "not in this project's allowlist" in str(e)
        checks.append(("a CTA URL outside the allowlist is rejected", threw_bad_url))

        threw_call_with_url = False
        _seed("prop-call-with-url")
        try:
            draft_gbp_post(project, loop, "prop-call-with-url", {**good_impl, "summary": "Unique summary for the CALL-with-URL test.", "call_to_action": {"action_type": "CALL", "url": "https://example.invalid/"}}, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_call_with_url = "must not be set" in str(e)
        checks.append(("a CALL CTA carrying a URL is rejected", threw_call_with_url))

        threw_closed_location = False
        _seed("prop-thornton", location="Thornton")
        try:
            draft_gbp_post(project, loop, "prop-thornton", {**good_impl, "summary": "Unique summary for the closed-location test."}, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_closed_location = "former/closed location" in str(e)
        checks.append(("a proposal targeting a closed location (Thornton) is refused", threw_closed_location))

        threw_trademark = False
        _seed("prop-trademark")
        try:
            draft_gbp_post(project, loop, "prop-trademark", {**good_impl, "summary": "We proudly offer Active Release Technique for faster recovery."}, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_trademark = "trademark_claim" in str(e)
        checks.append(("claiming the trademarked modality is rejected", threw_trademark))

        threw_wrong_credential = False
        _seed("prop-wrong-credential")
        try:
            draft_gbp_post(project, loop, "prop-wrong-credential", {**good_impl, "summary": "Acupuncture provided by our L.Ac. on staff."}, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_wrong_credential = "wrong_acupuncture_credential" in str(e)
        checks.append(("the wrong acupuncture credential wording is rejected", threw_wrong_credential))

        threw_radial = False
        _seed("prop-radial")
        try:
            draft_gbp_post(project, loop, "prop-radial", {**good_impl, "summary": "Try our radial shockwave therapy today."}, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_radial = "radial_shockwave_claim" in str(e)
        checks.append(("claiming radial shockwave (the clinic only runs focused/fESWT) is rejected", threw_radial))

        threw_wrong_location_service = False
        _seed("prop-spinal-greeley")
        try:
            draft_gbp_post(project, loop, "prop-spinal-greeley", {**good_impl, "summary": "Now offering spinal decompression at our Greeley location."}, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_wrong_location_service = "spinal_decompression_wrong_location" in str(e)
        checks.append(("spinal decompression claimed for Greeley (Denver-only) is rejected", threw_wrong_location_service))

        threw_not_promotable = False
        _seed("prop-pediatric")
        try:
            draft_gbp_post(project, loop, "prop-pediatric", {**good_impl, "summary": "We now offer pediatric chiropractic care for kids of all ages."}, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_not_promotable = "not_promotable_service" in str(e)
        checks.append(("promoting a not-to-be-promoted service (pediatric) is rejected", threw_not_promotable))

        threw_not_offered = False
        _seed("prop-orthotics")
        try:
            draft_gbp_post(project, loop, "prop-orthotics", {**good_impl, "summary": "We now offer custom orthotics fitted in-office."}, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_not_offered = "service_not_offered" in str(e)
        checks.append(("claiming a service the clinic does not offer at all (custom orthotics, offered: false) is rejected", threw_not_offered))

        threw_unconfirmed_shockwave = False
        with open(os.path.join(loop_dir, "gbp-profile-confirmations.yaml"), "w", encoding="utf-8", newline="\n") as f:
            f.write(f'source_content_hash: "wrong-hash-simulating-prose-drift"\nfacts:\n  - id: greeley-shockwave\n    kind: shockwave_device_type\n    location: Greeley\n    value: "focused (fESWT)"\n    confirmed_date: "2026-08-30"\n')
        _seed("prop-drifted-shockwave")
        try:
            draft_gbp_post(project, loop, "prop-drifted-shockwave", {**good_impl, "summary": "Now offering focused shockwave therapy (fESWT) at our Greeley clinic."}, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_unconfirmed_shockwave = "unconfirmed_shockwave_claim" in str(e)
        checks.append(("a TRUE shockwave claim is still rejected once the sidecar backing it is unconfirmed (source-hash drift)", threw_unconfirmed_shockwave))
        # restore a valid, verified sidecar for the remaining tests
        with open(os.path.join(loop_dir, "gbp-profile-confirmations.yaml"), "w", encoding="utf-8", newline="\n") as f:
            f.write(f'source_content_hash: "{prose_hash}"\nfacts:\n  - id: greeley-shockwave\n    kind: shockwave_device_type\n    location: Greeley\n    value: "focused (fESWT)"\n    confirmed_date: "2026-08-30"\n')

        threw_confirmed_shockwave = False
        _seed("prop-confirmed-shockwave")
        try:
            draft_gbp_post(project, loop, "prop-confirmed-shockwave", {**good_impl, "summary": "Now offering focused shockwave therapy (fESWT) at our Greeley clinic."}, "claude-test", art_yaml_path=art_yaml_path)
            threw_confirmed_shockwave = True  # should NOT raise
        except ValueError:
            threw_confirmed_shockwave = False
        checks.append(("a TRUE shockwave claim backed by a confirmed sidecar fact is accepted", threw_confirmed_shockwave))

        threw_wrong_location_mention = False
        _seed("prop-shockwave-wrong-location-text")
        try:
            draft_gbp_post(project, loop, "prop-shockwave-wrong-location-text", {**good_impl, "summary": "Now offering focused shockwave therapy (fESWT) at our Denver clinic."}, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_wrong_location_mention = "shockwave_claim_names_other_location" in str(e)
        checks.append(("a shockwave claim naming a DIFFERENT location than the proposal's actual target is rejected", threw_wrong_location_mention))

        threw_null_value_sidecar = False
        with open(os.path.join(loop_dir, "gbp-profile-confirmations.yaml"), "w", encoding="utf-8", newline="\n") as f:
            f.write(f'source_content_hash: "{prose_hash}"\nfacts:\n  - id: greeley-shockwave\n    kind: shockwave_device_type\n    location: Greeley\n    value: null\n    confirmed_date: "2026-08-30"\n')
        _seed("prop-shockwave-null-value")
        try:
            draft_gbp_post(project, loop, "prop-shockwave-null-value", {**good_impl, "summary": "We now provide focused shockwave (fESWT) treatment at Greeley."}, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_null_value_sidecar = "unconfirmed_shockwave_claim" in str(e)
        checks.append(("a 'confirmed' sidecar fact whose value is null still cannot back a shockwave claim", threw_null_value_sidecar))

        threw_no_art_yaml_corroboration = False
        with open(os.path.join(loop_dir, "gbp-profile-confirmations.yaml"), "w", encoding="utf-8", newline="\n") as f:
            f.write(f'source_content_hash: "{prose_hash}"\nfacts:\n  - id: greeley-shockwave\n    kind: shockwave_device_type\n    location: Greeley\n    value: "focused (fESWT)"\n    confirmed_date: "2026-08-30"\n')
        with open(art_yaml_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(art_yaml_content_no_shockwave_location)
        _seed("prop-shockwave-no-art-corroboration")
        try:
            draft_gbp_post(project, loop, "prop-shockwave-no-art-corroboration", {**good_impl, "summary": "Our Greeley clinic proudly provides focused (fESWT) shockwave care."}, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_no_art_yaml_corroboration = "unconfirmed_shockwave_claim" in str(e)
        checks.append(("a sidecar-only shockwave claim with no art.yaml corroboration for this location is still rejected", threw_no_art_yaml_corroboration))
        # restore the full, valid art.yaml for the remaining tests
        with open(art_yaml_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(art_yaml_content)

        threw_medical_claim = False
        _seed("prop-medical-claim")
        try:
            draft_gbp_post(project, loop, "prop-medical-claim", {**good_impl, "summary": "Our treatment is guaranteed to cure your chronic pain."}, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_medical_claim = "medical_claim" in str(e)
        checks.append(("a cure/guarantee medical claim is rejected", threw_medical_claim))

        threw_urgency = False
        _seed("prop-urgency")
        try:
            draft_gbp_post(project, loop, "prop-urgency", {**good_impl, "summary": "Act now - limited time offer on massage therapy!"}, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_urgency = "manufactured_urgency" in str(e)
        checks.append(("manufactured urgency language is rejected", threw_urgency))

        # --- specific bypass variants a Codex review (2026-08-31) demonstrated against the
        # original plain-substring lint - each must now be caught -----------------------------
        bypass_cases = [
            ("prop-bypass-prenatal", "Now offering prenatal chiropractic care for expecting mothers.", "not_promotable_service"),
            ("prop-bypass-radial-eswt", "Try our radial extracorporeal shock wave therapy today.", "radial_shockwave_claim"),
            ("prop-bypass-clinically-proven", "Our clinically proven treatment relieves chronic pain for good.", "medical_claim"),
            ("prop-bypass-today-only", "Today only - book your massage before it's gone!", "manufactured_urgency"),
            ("prop-bypass-lac", "Acupuncture provided by our LAc on staff.", "wrong_acupuncture_credential"),
            ("prop-bypass-licensed-acu-specialist", "See our licensed acupuncture specialist for pain relief.", "wrong_acupuncture_credential"),
            ("prop-bypass-similar-to-art", "Our modality is similar to ART but even better.", "trademark_claim"),
            ("prop-bypass-hyphen-proven", "Our clinically-proven treatment works wonders.", "medical_claim"),
            ("prop-bypass-hyphen-limited-time", "Limited-time offer on massage therapy!", "manufactured_urgency"),
            ("prop-bypass-hyphen-today-only", "Today-only special on chiropractic care.", "manufactured_urgency"),
            ("prop-bypass-reswt", "Ask about our rESWT treatment option.", "radial_shockwave_claim"),
            ("prop-bypass-acoustic-wave", "Try our radial acoustic wave therapy today.", "radial_shockwave_claim"),
            ("prop-bypass-heals-chronic", "Our treatment heals chronic pain.", "medical_claim"),
            ("prop-bypass-guaranteeing", "Our treatment is guaranteeing lasting relief.", "medical_claim"),
            ("prop-bypass-clinically-validated", "Our clinically validated treatment is proven effective.", "medical_claim"),
            ("prop-bypass-filling-fast", "Appointments are filling fast, book immediately.", "manufactured_urgency"),
            ("prop-bypass-this-week-only", "This week only, reserve your visit.", "manufactured_urgency"),
            ("prop-bypass-hyphen-trademark", "We use Active-Release Technique for faster recovery.", "trademark_claim"),
            ("prop-bypass-hyphen-similar-art", "Our modality is similar-to-ART but different.", "trademark_claim"),
            ("prop-bypass-hyphen-credential", "See our licensed-acupuncturist for treatment.", "wrong_acupuncture_credential"),
        ]
        for proposal_id, summary, expected_flag in bypass_cases:
            _seed(proposal_id)
            threw = False
            try:
                draft_gbp_post(project, loop, proposal_id, {**good_impl, "summary": summary}, "claude-test", art_yaml_path=art_yaml_path)
            except ValueError as e:
                threw = expected_flag in str(e)
            checks.append((f'bypass variant "{summary}" is still caught ({expected_flag})', threw))

        threw_seasonal_location = False
        _seed("prop-unc", location="UNC Campus")
        try:
            draft_gbp_post(project, loop, "prop-unc", {**good_impl, "summary": "Unique summary for the UNC Campus test."}, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_seasonal_location = "not currently active" in str(e)
        checks.append(("a proposal targeting a seasonally-inactive but otherwise-known location (UNC Campus) is refused", threw_seasonal_location))

        threw_external_url = False
        _seed("prop-external-url")
        try:
            draft_gbp_post(project, loop, "prop-external-url", {**good_impl, "summary": "Unique summary for the external-URL allowlist test.", "call_to_action": {"action_type": "LEARN_MORE", "url": "https://example.invalid.attacker.com/massage/"}}, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_external_url = "not in this project's allowlist" in str(e)
        checks.append(("a URL on a different (lookalike) hostname is rejected, never treated as same-site", threw_external_url))

        threw_duplicate = False
        _seed("prop-duplicate", status="draft")
        try:
            draft_gbp_post(project, loop, "prop-duplicate", good_impl, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_duplicate = "identical to another proposal" in str(e)
        checks.append(("a summary identical to another pending proposal's is rejected (local duplicate check)", threw_duplicate))

        # The crash-window scenario this event-sourced check exists to close: the cached JSON
        # still says "draft" (as if approve_gbp_post.py crashed after appending its
        # gbp_proposal_approved event but before rewriting the proposal cache), while the event
        # log already says "approved". Drafting against the stale cache would silently diverge
        # the locked hash from the proposal's actual content (Codex review, 2026-09-01, third
        # round) - this must refuse using the EVENT-sourced status, not the JSON's.
        write_proposal(pending_dir, {
            "id": "prop-cache-drift", "action_type": "gbp-post-draft", "status": "draft",
            "target": {"location": "Greeley", "topic": "some-topic"},
        })
        append_event(loop_dir, "proposal_created", project=project, loop=loop, proposal_id="prop-cache-drift", action_type="gbp-post-draft", resulting_proposal_status="draft")
        append_event(loop_dir, "gbp_proposal_approved", project=project, loop=loop, proposal_id="prop-cache-drift", action_type="gbp-post-draft", resulting_proposal_status="approved")
        threw_cache_drift = False
        try:
            draft_gbp_post(project, loop, "prop-cache-drift", {**good_impl, "summary": "Unique summary for the cache-drift test."}, "claude-test", art_yaml_path=art_yaml_path)
        except ValueError as e:
            threw_cache_drift = "event-sourced status" in str(e) and "approved" in str(e)
        checks.append(("a proposal whose cached JSON says draft but whose event log says approved is refused (event log is authoritative, not the cache)", threw_cache_drift))

        for status in ("reviewed", "approved", "review-pending", "review-approved"):
            proposal_id = f"prop-past-draft-{status}"
            _seed(proposal_id, status=status)
            threw_past_draft = False
            try:
                draft_gbp_post(project, loop, proposal_id, {**good_impl, "summary": f"Unique summary for {status} test."}, "claude-test", art_yaml_path=art_yaml_path)
            except ValueError as e:
                threw_past_draft = 'not "draft"' in str(e)
            checks.append((f'drafting a proposal with status "{status}" is refused', threw_past_draft))

        _seed("prop-revision", status="review-revision-needed")
        revised = draft_gbp_post(project, loop, "prop-revision", {**good_impl, "summary": "Revised unique summary for the revision test."}, "claude-test", art_yaml_path=art_yaml_path)
        checks.append(("a review-revision-needed proposal can be redrafted", revised["implementation"]["summary"] == "Revised unique summary for the revision test."))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
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

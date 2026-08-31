# GBP Posts plan Phase 4: the constraint-source hard gate.
#
# Two sources govern what a GBP Post is allowed to say about this client:
#   - projects/<slug>/gbp-profiles.md - GBP-specific wording rules (credentials, device
#     types). Prose, no per-fact confirmation metadata of its own.
#   - D:\Dev\MMC\registry\clients\<slug>.yaml - the richer, cross-system source (trademark
#     constraints, service-area limits, current hours/address/phone, closed locations).
#
# The plan's explicit requirement: hashing must prove facts didn't change since approval, but
# that alone doesn't prove they were CORRECT to begin with. So, independently of hashing:
#   1. Any field the source marks unconfirmed (art.yaml's `*_confirmed: null`) is never safe
#      to assert - omitted, not guessed at.
#   2. A field confirmed longer ago than CONFIRMATION_MAX_AGE_DAYS is treated as unconfirmed
#      until refreshed.
#   3. Where the two sources overlap on the same fact (hours, address), they are diffed
#      against each other - a contradiction is a hard failure, not a tie-break.
#
# gbp-profiles.md has no per-fact confirmation metadata the way art.yaml does (it is prose,
# with one blanket "As of <date>" header). Rather than inventing per-fact dates that were never
# actually confirmed at those granularities, this module reads a small structured CONFIRMATION
# SIDECAR file (projects/<slug>/loops/gbp/gbp-profile-confirmations.yaml) - the plan's explicitly
# offered alternative to editing gbp-profiles.md's prose directly. Today that sidecar carries
# ONE confirmed_date per fact, seeded from gbp-profiles.md's own blanket "As of" header (the
# only evidence that exists) - a coarser confirmation than art.yaml's per-fact dates, and
# documented as such; refining it to true per-fact confirmation requires a human to actually
# reconfirm each fact individually.
#
# A deterministic PARSED-FACTS projection, not a raw-file hash: hashing "the parsed facts
# actually used" only means something if two different sessions parsing the same prose extract
# the same thing. PARSER_VERSION is bumped whenever the extraction logic changes, and the
# manifest records it alongside the projection hash - a parser change invalidates every prior
# approval built on the old projection, by design.
import json
import os
from datetime import datetime, timezone

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a pinned dependency of this workspace
    raise

# Bumped from 1 to 2 on 2026-08-31 (Codex review, third round): parsing/validation semantics
# changed materially since v1 (required-section enforcement, normalized duplicate detection,
# a stricter shockwave-confirmation requirement) - a manifest hash produced under v1 must not
# be indistinguishable from one produced under the new, stricter logic. Any caller comparing
# manifests across this boundary should treat a v1-tagged manifest as needing re-verification,
# never as still current.
PARSER_VERSION = 2
CONFIRMATION_MAX_AGE_DAYS = 180

# The only fields Phase 4's validator actually consumes - deliberately narrow (the plan calls
# for "a small, versioned structured projection", not a full mirror of either source).
# Fields art.yaml actually dates with a `<field>_confirmed` sibling. `status` deliberately
# excluded: the real file (services_and_locations.locations[].status) asserts it directly with
# no confirmation-date convention of its own - treating it as "unconfirmed" the way a dateless
# street/hours field would be treated would have permanently blocked Greeley/Denver (both
# genuinely active, neither carrying a status_confirmed field) rather than only blocking what
# it should (a seasonal/inactive location). `status` is read as a plain fact instead - see
# ACTIVE_STATUS_VALUES.
ART_YAML_LOCATION_FIELDS = ("street", "zip", "phone", "hours")
ART_YAML_CONSTRAINT_FIELDS = ("service", "offered", "promote", "locations", "device_type")
ACTIVE_STATUS_VALUES = {"active"}

# Sidecar fact `kind` values for which consuming code looks up a single fact per (kind,
# location) and would silently pick or ignore one of several if more than one existed.
# "credential_wording" is deliberately excluded - the real sidecar legitimately carries more
# than one per location (different services, different wording rules).
SINGLE_VALUED_FACT_KINDS = {"shockwave_device_type", "hours", "address"}


def _now_iso(now=None):
    now = now or datetime.now(timezone.utc)
    return now.isoformat().replace("+00:00", "Z")


def _sha256_json(value):
    import hashlib
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _sha256_text(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_name(value):
    """Case/whitespace-insensitive identity for duplicate detection - "Greeley" and " greeley "
    (or "Pediatric and prenatal chiropractic" vs "pediatric  and prenatal chiropractic") must
    collide as duplicates, not be treated as two distinct entries (Codex review, 2026-08-31,
    third round: exact-string-only duplicate checks let case/whitespace variants slip through)."""
    import unicodedata
    if not isinstance(value, str):
        return value
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _parse_confirmed_date(value):
    """Returns a UTC-aware datetime, or None if value is missing/null/unparseable -
    treated identically to "unconfirmed" by the caller, never guessed at."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _is_stale(confirmed_dt, now, max_age_days=CONFIRMATION_MAX_AGE_DAYS):
    if confirmed_dt is None:
        return True
    age_days = (now - confirmed_dt).total_seconds() / 86400
    return age_days < 0 or age_days > max_age_days


def parse_art_yaml(art_yaml_path, now=None):
    """Extracts the small, versioned projection Phase 4 needs from MMC's registry.
    Every location/constraint fact is tagged confirmed=True only if it has a parseable,
    non-stale confirmation date on the SAME field - a location's `status` isn't "confirmed"
    just because its `street_confirmed` is fresh; each field is judged on its own dated
    sibling, never inherited from a neighboring field.

    Raises FileNotFoundError if art_yaml_path doesn't exist - a missing cross-repo source is a
    hard failure for the caller (fail closed on a missing input), never silently skipped.
    """
    now = now or datetime.now(timezone.utc)
    with open(art_yaml_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f.read())
    if not isinstance(raw, dict):
        raise ValueError(f"gbp_constraints: {art_yaml_path} does not contain a mapping at its root")

    # Required, not optional: a real art.yaml always has this section, and treating its total
    # absence the same as "present but empty" would let a caller accidentally pointed at the
    # wrong file (or a wildly outdated one) silently produce zero locations/constraints rather
    # than an error (Codex review, 2026-08-31 follow-up - a genuinely required section
    # producing empty-but-valid facts is indistinguishable from "this client has no locations
    # at all", which is never true).
    if "services_and_locations" not in raw:
        raise ValueError(f"gbp_constraints: {art_yaml_path} has no services_and_locations section")
    services_and_locations = raw.get("services_and_locations")
    if not isinstance(services_and_locations, dict):
        raise ValueError(f"gbp_constraints: {art_yaml_path}'s services_and_locations is not a mapping")

    # `service_constraints` lives NESTED under services_and_locations in the real file, not at
    # the document root - a Codex review (2026-08-31) caught this exact mismatch: the original
    # version read raw.get("service_constraints") (always [] against the real art.yaml), which
    # silently disabled every trademark/location/promotability check this module exists for,
    # undetected by this module's own self-test because that test's synthetic fixture matched
    # the WRONG (self-consistent but incorrect) schema rather than the real one.
    #
    # `locations` and `service_constraints` are REQUIRED keys, not merely type-checked when
    # present (Codex review, third round: `services_and_locations: {}` or a section with
    # locations but no service_constraints at all previously parsed to empty-but-valid facts,
    # which silently disabled every service check the same way the original schema bug did -
    # indistinguishable from "this client has no service constraints", which is never true for
    # a real file). `former_locations` remains optional - a client with no former locations at
    # all is a real, valid state.
    for required_key in ("locations", "service_constraints"):
        if required_key not in services_and_locations:
            raise ValueError(f"gbp_constraints: {art_yaml_path}'s services_and_locations has no {required_key} key")
    raw_locations = services_and_locations.get("locations")
    if not isinstance(raw_locations, list):
        raise ValueError(f"gbp_constraints: {art_yaml_path}'s services_and_locations.locations is not a list")
    raw_former = services_and_locations.get("former_locations")
    if raw_former is not None and not isinstance(raw_former, list):
        raise ValueError(f"gbp_constraints: {art_yaml_path}'s services_and_locations.former_locations is not a list")
    raw_constraints = services_and_locations.get("service_constraints")
    if not isinstance(raw_constraints, list):
        raise ValueError(f"gbp_constraints: {art_yaml_path}'s services_and_locations.service_constraints is not a list")

    locations = []
    seen_location_keys = set()
    for loc in raw_locations or []:
        if not isinstance(loc, dict):
            raise ValueError(f"gbp_constraints: {art_yaml_path} contains a non-mapping location entry")
        if not loc.get("name"):
            raise ValueError(f"gbp_constraints: {art_yaml_path} contains a location entry with no name")
        name_key = _normalize_name(loc["name"])
        if name_key in seen_location_keys:
            raise ValueError(f'gbp_constraints: {art_yaml_path} contains a duplicate location "{loc["name"]}" (case/whitespace-insensitive) - ambiguous, refusing to guess which is authoritative')
        seen_location_keys.add(name_key)
        entry = {
            "name": loc.get("name"),
            # Read directly, never dated (see ACTIVE_STATUS_VALUES) - and never silently
            # defaulted to "active": a location with no status field at all is exactly the
            # kind of malformed/incomplete source this refuses to guess about.
            "status": loc.get("status"),
        }
        for field in ART_YAML_LOCATION_FIELDS:
            confirmed_field = f"{field}_confirmed"
            confirmed_dt = _parse_confirmed_date(loc.get(confirmed_field))
            stale = _is_stale(confirmed_dt, now)
            entry[field] = {
                "value": loc.get(field) if not stale else None,
                "raw_value": loc.get(field),
                "confirmed": not stale,
                "confirmed_date": loc.get(confirmed_field),
            }
        locations.append(entry)

    former_location_names = []
    for loc in raw_former or []:
        if not isinstance(loc, dict) or not loc.get("name"):
            raise ValueError(f"gbp_constraints: {art_yaml_path} contains a malformed former_locations entry")
        # Checked against the SAME namespace as active locations, not a separate one - a name
        # duplicated across active/former (or a case/whitespace variant of an active name
        # accidentally re-listed as former) is exactly as ambiguous as a duplicate within one
        # list (Codex review, third round).
        name_key = _normalize_name(loc["name"])
        if name_key in seen_location_keys:
            raise ValueError(f'gbp_constraints: {art_yaml_path} contains "{loc["name"]}" in both locations and former_locations (or duplicated within former_locations) - ambiguous')
        seen_location_keys.add(name_key)
        former_location_names.append(loc["name"])

    constraints = []
    seen_service_keys = set()
    for c in raw_constraints or []:
        if not isinstance(c, dict) or not c.get("service"):
            raise ValueError(f"gbp_constraints: {art_yaml_path} contains a malformed service_constraints entry (missing service name)")
        service_key = _normalize_name(c["service"])
        if service_key in seen_service_keys:
            raise ValueError(f'gbp_constraints: {art_yaml_path} contains a duplicate service_constraints entry "{c["service"]}" (case/whitespace-insensitive) - ambiguous, refusing to guess which is authoritative (a promotable entry followed by a non-promotable variant would otherwise resolve to whichever happens to be listed first)')
        seen_service_keys.add(service_key)
        constraints.append({field: c.get(field) for field in ART_YAML_CONSTRAINT_FIELDS})

    return {
        "locations": locations,
        "former_location_names": former_location_names,
        "service_constraints": constraints,
    }


def parse_gbp_profile_confirmations(confirmations_path, gbp_profiles_content=None, now=None):
    """Reads the confirmation sidecar (projects/<slug>/loops/gbp/gbp-profile-confirmations.yaml)
    - the structured stand-in for per-fact confirmation metadata gbp-profiles.md's prose does
    not carry. Same confirmed/stale semantics as parse_art_yaml. Raises FileNotFoundError if
    the sidecar is missing - fail closed, same as the art.yaml source.

    Takes gbp-profiles.md's already-read CONTENT (a string), never a path to re-read here -
    the sole production caller (build_source_manifest) reads that file exactly once and passes
    the content to both this function and its own hash computation, closing a TOCTOU gap a
    Codex review (2026-08-31) flagged in an earlier two-separate-reads version.

    The sidecar's own declared `source_content_hash` is checked against a fresh hash of
    `gbp_profiles_content` (someone could edit gbp-profiles.md's wording and the sidecar would
    otherwise keep asserting the old, now-unverified value with no detection). On a mismatch,
    a missing `source_content_hash`, or `gbp_profiles_content` not being provided at all, EVERY
    fact in the sidecar is treated as unconfirmed, regardless of its own confirmed_date - the
    sidecar's relationship to its source is either broken or was never checked, so nothing it
    asserts can be trusted until that's resolved. There is deliberately no "skip the check"
    path: a caller that cannot supply the prose content gets the fail-closed (unconfirmed)
    outcome, never a silent pass-through (Codex review, 2026-08-31 follow-up - the previous
    optional gbp_profiles_md_path parameter let a caller pass None when the file was merely
    missing, bypassing verification entirely instead of failing closed)."""
    now = now or datetime.now(timezone.utc)
    with open(confirmations_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f.read())
    if not isinstance(raw, dict):
        raise ValueError(f"gbp_constraints: {confirmations_path} does not contain a mapping at its root")
    raw_facts = raw.get("facts")
    if raw_facts is not None and not isinstance(raw_facts, list):
        raise ValueError(f"gbp_constraints: {confirmations_path}'s facts field is not a list")

    if gbp_profiles_content is None:
        sidecar_verified = False
    else:
        current_prose_hash = _sha256_text(gbp_profiles_content)
        declared_hash = raw.get("source_content_hash")
        sidecar_verified = declared_hash is not None and declared_hash == current_prose_hash

    facts = []
    seen_ids = set()
    seen_kind_locations = set()
    for fact in raw_facts or []:
        if not isinstance(fact, dict) or not fact.get("id") or not fact.get("kind"):
            raise ValueError(f"gbp_constraints: {confirmations_path} contains a malformed fact entry (missing id/kind)")
        id_key = _normalize_name(fact["id"])
        if id_key in seen_ids:
            raise ValueError(f'gbp_constraints: {confirmations_path} contains a duplicate fact id "{fact["id"]}" (case/whitespace-insensitive) - ambiguous, refusing to guess which is authoritative')
        seen_ids.add(id_key)
        # A duplicate (kind, location) pair is exactly as ambiguous as a duplicate id for a
        # kind that consuming code looks up expecting a single answer (shockwave_device_type,
        # via draft_gbp_post.py's next(...) lookup) - a second entry would otherwise be picked
        # or ignored depending on list order (Codex review, third round). Scoped to ONLY that
        # kind, not applied blanket to every kind: the real sidecar legitimately carries two
        # DIFFERENT credential_wording facts for location "both" (acupuncture's and massage's
        # separate wording rules) - collapsing those into one namespace would reject genuinely
        # valid data. A kind is added here only when real code actually relies on (kind,
        # location) uniqueness for it.
        if fact.get("kind") in SINGLE_VALUED_FACT_KINDS:
            kind_location_key = (_normalize_name(fact.get("kind")), _normalize_name(fact.get("location")))
            if kind_location_key in seen_kind_locations:
                raise ValueError(f'gbp_constraints: {confirmations_path} contains two facts for the same (kind, location) pair {kind_location_key} - ambiguous, refusing to guess which is authoritative')
            seen_kind_locations.add(kind_location_key)
        confirmed_dt = _parse_confirmed_date(fact.get("confirmed_date"))
        stale = _is_stale(confirmed_dt, now) or not sidecar_verified
        facts.append({
            "id": fact.get("id"),
            "kind": fact.get("kind"),
            "location": fact.get("location"),
            "value": fact.get("value") if not stale else None,
            "raw_value": fact.get("value"),
            "confirmed": not stale,
            "confirmed_date": fact.get("confirmed_date"),
        })
    return {"facts": facts, "sidecar_verified_against_source": sidecar_verified}


def build_source_manifest(project_dir, art_yaml_path, confirmations_path, gbp_profiles_md_path, now=None):
    """The semantic hash-chain entry per source: path, projection hash, parser version.
    Deliberately excludes the read timestamp from what gets hashed into an approval (a fresh
    read at publish time always produces a new timestamp, which would invalidate every
    approval on re-read) - the timestamp is returned separately for audit/staleness display
    only, per the plan's explicit distinction.

    `gbp_profiles_md_path` is REQUIRED, not optional - a caller that cannot supply it should
    let this raise (FileNotFoundError) rather than pass None to silently skip the sidecar-vs-
    prose verification (Codex review, 2026-08-31 follow-up: an earlier optional version let
    draft_gbp_post.py pass None whenever gbp-profiles.md happened to be missing, bypassing the
    check entirely instead of failing closed). Reads gbp-profiles.md's content exactly ONCE
    and threads it to both parse_gbp_profile_confirmations and this function's own hash
    computation - two separate reads of the same file (the previous shape) is a TOCTOU gap a
    concurrent edit could exploit, however narrow that window is on a single-operator system."""
    now = now or datetime.now(timezone.utc)
    art_facts = parse_art_yaml(art_yaml_path, now=now)
    with open(gbp_profiles_md_path, "r", encoding="utf-8") as f:
        gbp_profiles_content = f.read()
    profile_facts = parse_gbp_profile_confirmations(confirmations_path, gbp_profiles_content=gbp_profiles_content, now=now)
    prose_hash = _sha256_text(gbp_profiles_content)

    manifest = {
        "parser_version": PARSER_VERSION,
        "sources": [
            {
                "path": os.path.relpath(art_yaml_path, project_dir).replace("\\", "/") if _same_drive(art_yaml_path, project_dir) else art_yaml_path,
                "projection_hash": _sha256_json(art_facts),
                "parser_version": PARSER_VERSION,
            },
            {
                "path": os.path.relpath(confirmations_path, project_dir).replace("\\", "/") if _same_drive(confirmations_path, project_dir) else confirmations_path,
                "projection_hash": _sha256_json(profile_facts),
                "parser_version": PARSER_VERSION,
            },
            {
                "path": os.path.relpath(gbp_profiles_md_path, project_dir).replace("\\", "/") if _same_drive(gbp_profiles_md_path, project_dir) else gbp_profiles_md_path,
                # Raw content hash, not a parsed projection - gbp-profiles.md is prose that this
                # module does not parse directly; its content hash exists so a change to it
                # that the sidecar hasn't caught up with is visible in the manifest (and, per
                # parse_gbp_profile_confirmations, already made every sidecar fact unconfirmed).
                "content_hash": prose_hash,
                "parser_version": None,
            },
        ],
    }
    # Read timestamps kept alongside, not inside, the hashed manifest (see docstring).
    read_at = {"art_yaml": _now_iso(now), "gbp_profile_confirmations": _now_iso(now), "gbp_profiles_md": _now_iso(now)}
    return manifest, read_at, art_facts, profile_facts


def _same_drive(a, b):
    return os.path.splitdrive(os.path.abspath(a))[0].lower() == os.path.splitdrive(os.path.abspath(b))[0].lower()


def manifest_hash(manifest):
    """What actually goes into a proposal's approved_payload_hash chain (plan Phase 5) -
    only the semantic fields (path, projection_hash, parser_version), never a read timestamp."""
    return _sha256_json(manifest)


def find_contradictions(art_facts, profile_facts):
    """Cross-checks the two sources on overlapping facts - a disagreement is a hard failure
    per the plan, never a tie-break toward either source. Only compares facts BOTH sources
    currently consider confirmed; an unconfirmed fact on either side is excluded here, not
    silently compared - see check_unconfirmed_facts for what happens to those instead.

    Two kinds of overlap today:
      - "hours"/"address": gbp-profiles.md carries no location hours/address facts as of
        this writing, so this branch is structurally present but will not fire against real
        data until the sidecar gains such a fact - kept general rather than hardcoded to
        "there is currently nothing to compare".
      - "shockwave_device_type": a genuine, real overlap - gbp-profiles.md's credentials
        section and art.yaml's service_constraints both independently assert the clinic runs
        FOCUSED (fESWT) shockwave, never radial. This is exactly the kind of fact this
        mechanism exists to protect: two sources agreeing today does not mean a future edit
        to either one silently drifting past the other should go unnoticed.
    """
    contradictions = []
    art_by_location = {loc["name"]: loc for loc in art_facts["locations"]}
    for fact in profile_facts["facts"]:
        if fact["kind"] in ("hours", "address"):
            art_loc = art_by_location.get(fact.get("location"))
            if not art_loc:
                continue
            art_field = "street" if fact["kind"] == "address" else "hours"
            art_entry = art_loc.get(art_field) or {}
            if not art_entry.get("confirmed") or not fact.get("confirmed"):
                continue  # an unconfirmed side can't contradict anything - it asserts nothing
            if art_entry.get("value") and fact.get("value") and art_entry["value"] != fact["value"]:
                contradictions.append({
                    "location": fact.get("location"),
                    "kind": fact["kind"],
                    "art_yaml_value": art_entry["value"],
                    "gbp_profile_value": fact["value"],
                })
        elif fact["kind"] == "shockwave_device_type":
            if not fact.get("confirmed"):
                continue
            # Normalized lookup, not exact-match (Codex review, fourth round - see
            # offered_and_promotable's docstring for why exact-match silently stops matching a
            # case/whitespace variant of the canonical service name).
            _offered, _promotable, shockwave_constraint = offered_and_promotable(art_facts, "Shockwave therapy")
            if not shockwave_constraint or not shockwave_constraint.get("device_type"):
                continue  # art.yaml has nothing confirmed to compare against here
            locations_covered = shockwave_constraint.get("locations") or []
            covered_keys = {_normalize_name(loc) for loc in locations_covered}
            if fact.get("location") and _normalize_name(fact["location"]) not in covered_keys:
                continue
            if fact.get("value") and shockwave_constraint["device_type"] != fact["value"]:
                contradictions.append({
                    "location": fact.get("location"),
                    "kind": "shockwave_device_type",
                    "art_yaml_value": shockwave_constraint["device_type"],
                    "gbp_profile_value": fact["value"],
                })
    return contradictions


def assert_location_publishable(art_facts, location_name):
    """Refuses a location that is structurally absent from what this client actually
    operates - a closed former location (Thornton), an unknown one, or one that IS a known
    entry but is not currently active (e.g. UNC Campus, status: seasonal_inactive - a Codex
    review, 2026-08-31, found the original version accepted this: it checked former-location
    membership and "is this name known at all", but never the location's own status field, so
    a seasonally-closed profile passed as freely as an open one). Raises ValueError; never
    returns a partial/best-guess result."""
    # Normalized matching throughout (Codex review, fourth round - the same exact-match class
    # of bug found in offered_and_promotable applies here too).
    target_key = _normalize_name(location_name)
    if target_key in {_normalize_name(n) for n in (art_facts.get("former_location_names") or [])}:
        raise ValueError(f'gbp_constraints: "{location_name}" is a former/closed location per art.yaml - never a valid GBP Post target')
    matching = next((loc for loc in art_facts.get("locations") or [] if _normalize_name(loc["name"]) == target_key), None)
    if matching is None:
        raise ValueError(f'gbp_constraints: "{location_name}" is not a known location in art.yaml - refusing to assume it is publishable')
    if matching.get("status") not in ACTIVE_STATUS_VALUES:
        raise ValueError(f'gbp_constraints: "{location_name}" is not currently active per art.yaml (status: {matching.get("status")!r}) - never a valid GBP Post target while inactive')


def offered_and_promotable(art_facts, service_name):
    """Returns (offered, promotable, constraint_or_None) for a named service, per art.yaml's
    service_constraints - e.g. "Pediatric and prenatal chiropractic" is offered but
    promote: false, and must never be the subject of a Post.

    Matches by _normalize_name (case/whitespace-insensitive), not exact string equality - a
    Codex review (2026-08-31, fourth round) found that parse_art_yaml's duplicate detection
    already normalizes names, but this lookup didn't: a case or double-space variant of a
    canonical service name (e.g. art.yaml recording "Custom  orthotics" while callers pass the
    constant "Custom orthotics") would parse successfully as a single, non-duplicate entry,
    then silently fail to match here - returning (None, None, None), which no caller treats as
    a violation, defeating the entire not-offered/not-promotable check for that service with
    no error anywhere in the chain."""
    target_key = _normalize_name(service_name)
    for c in art_facts.get("service_constraints") or []:
        if _normalize_name(c.get("service")) == target_key:
            offered = c.get("offered") is True
            promotable = offered and c.get("promote") is not False
            return offered, promotable, c
    return None, None, None


def _self_test():
    import shutil
    import sys
    import tempfile

    checks = []
    tmp = tempfile.mkdtemp(prefix="gbp-constraints-test-")
    now = datetime.now(timezone.utc)

    from datetime import timedelta

    def _iso(days_ago):
        return (now - timedelta(days=days_ago)).date().isoformat()

    art_yaml_path = os.path.join(tmp, "art.yaml")
    confirmations_path = os.path.join(tmp, "gbp-profile-confirmations.yaml")
    profiles_md_path = os.path.join(tmp, "gbp-profiles.md")

    # Schema matches the REAL art.yaml exactly (service_constraints NESTED under
    # services_and_locations, not top-level) - a Codex review (2026-08-31) found the original
    # fixture used a self-consistent but WRONG schema, so this test suite never noticed that
    # the parser itself read the wrong path and always saw an empty service_constraints list
    # against the real file. Also adds UNC Campus (status: seasonal_inactive) - the real
    # third location this module must actually refuse.
    def _write_art_yaml(denver_street_confirmed="null", denver_hours_confirmed_days_ago=300, device_type="focused (fESWT)"):
        with open(art_yaml_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"""
services_and_locations:
  locations:
    - name: Greeley
      status: active
      street: "1823 65th Ave Suite 3"
      zip: "80634"
      phone: "970-324-1750"
      street_confirmed: "{_iso(10)}"
      hours: "Mon-Thu 08:00-18:00"
      hours_confirmed: "{_iso(10)}"
    - name: Denver
      status: active
      street: "2480 W 26th Ave #90B"
      zip: "80211"
      phone: "720-604-2792"
      street_confirmed: {denver_street_confirmed}
      hours: "Mon-Thu 08:00-17:00, Fri 08:00-15:00"
      hours_confirmed: "{_iso(denver_hours_confirmed_days_ago)}"
    - name: UNC Campus
      status: seasonal_inactive
      street: "1901 10th Ave, Cassidy Hall"
      street_confirmed: "{_iso(10)}"
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
      device_type: "{device_type}"
""")

    def _write_confirmations(source_hash, greeley_confirmed_days_ago=10):
        with open(confirmations_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"""
source_content_hash: "{source_hash}"
facts:
  - id: greeley-shockwave-device-type
    kind: shockwave_device_type
    location: Greeley
    value: "focused (fESWT)"
    confirmed_date: "{_iso(greeley_confirmed_days_ago)}"
""")

    try:
        with open(profiles_md_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("# gbp-profiles.md fixture\n\nShockwave devices are FOCUSED (fESWT), never radial.\n")
        prose_hash = _sha256_text(open(profiles_md_path, encoding="utf-8").read())

        _write_art_yaml()
        _write_confirmations(prose_hash)

        art_facts = parse_art_yaml(art_yaml_path, now=now)
        greeley = next(loc for loc in art_facts["locations"] if loc["name"] == "Greeley")
        denver = next(loc for loc in art_facts["locations"] if loc["name"] == "Denver")
        checks.append(("a recently-confirmed field is usable", greeley["street"]["confirmed"] is True and greeley["street"]["value"] == "1823 65th Ave Suite 3"))
        checks.append(("a null-confirmed field (Denver street) is never asserted as safe", denver["street"]["confirmed"] is False and denver["street"]["value"] is None))
        checks.append(("a field confirmed >180 days ago is treated as unconfirmed (Denver hours)", denver["hours"]["confirmed"] is False and denver["hours"]["value"] is None))
        checks.append(("Thornton is recorded as a former location", "Thornton" in art_facts["former_location_names"]))
        checks.append(("service_constraints are actually parsed from the real (nested) schema location", len(art_facts["service_constraints"]) == 4))

        threw_thornton = False
        try:
            assert_location_publishable(art_facts, "Thornton")
        except ValueError as e:
            threw_thornton = "former/closed location" in str(e)
        checks.append(("Thornton is refused as a Post target", threw_thornton))

        threw_seasonal = False
        try:
            assert_location_publishable(art_facts, "UNC Campus")
        except ValueError as e:
            threw_seasonal = "not currently active" in str(e)
        checks.append(("a known but seasonally-inactive location (UNC Campus) is refused, not assumed publishable", threw_seasonal))

        threw_unknown = False
        try:
            assert_location_publishable(art_facts, "Nowhere")
        except ValueError as e:
            threw_unknown = "not a known location" in str(e)
        checks.append(("a wholly unknown location name is refused", threw_unknown))

        assert_location_publishable(art_facts, "Greeley")  # should not raise
        checks.append(("a real, active location is accepted", True))

        art_offered, art_promotable, _ = offered_and_promotable(art_facts, "Active Release Technique (ART)")
        checks.append(("a trademark-constrained service is offered=False", art_offered is False))
        ped_offered, ped_promotable, _ = offered_and_promotable(art_facts, "Pediatric and prenatal chiropractic")
        checks.append(("an offered-but-not-promotable service is flagged distinctly", ped_offered is True and ped_promotable is False))
        spinal_offered, spinal_promotable, spinal_c = offered_and_promotable(art_facts, "Spinal decompression")
        checks.append(("a location-limited service records its allowed locations", spinal_c is not None and spinal_c["locations"] == ["Denver"]))
        unknown_offered, _, _ = offered_and_promotable(art_facts, "Not a real service")
        checks.append(("an unlisted service returns None, not a guess", unknown_offered is None))

        # A case/whitespace variant of a real service name must still match (Codex review,
        # fourth round: an exact-string lookup would silently stop protecting a service the
        # moment art.yaml recorded its name with different casing/spacing than the caller's
        # canonical constant, with no error anywhere in the chain).
        variant_offered, variant_promotable, _ = offered_and_promotable(art_facts, "  active RELEASE   Technique (ART)")
        checks.append(("offered_and_promotable matches a case/whitespace variant of a real service name", variant_offered is False))
        variant_pub_ok = True
        try:
            assert_location_publishable(art_facts, "  GREELEY ")
        except ValueError:
            variant_pub_ok = False
        checks.append(("assert_location_publishable matches a case/whitespace variant of a real location name", variant_pub_ok))

        profile_facts = parse_gbp_profile_confirmations(confirmations_path, gbp_profiles_content=open(profiles_md_path, encoding='utf-8').read(), now=now)
        checks.append(("a sidecar whose source_content_hash matches the current prose is verified", profile_facts["sidecar_verified_against_source"] is True))
        contradictions = find_contradictions(art_facts, profile_facts)
        checks.append(("agreeing shockwave device types produce no contradiction", not any(c["kind"] == "shockwave_device_type" for c in contradictions)))

        # --- the sidecar-vs-prose drift check (Codex review, 2026-08-31): editing
        # gbp-profiles.md WITHOUT updating the sidecar's source_content_hash must make every
        # sidecar fact unconfirmed, not silently keep asserting the old value ------------------
        with open(profiles_md_path, "a", encoding="utf-8", newline="\n") as f:
            f.write("\nA new sentence changes the prose content.\n")
        drifted_profile_facts = parse_gbp_profile_confirmations(confirmations_path, gbp_profiles_content=open(profiles_md_path, encoding='utf-8').read(), now=now)
        checks.append(("editing gbp-profiles.md without updating the sidecar's hash is detected", drifted_profile_facts["sidecar_verified_against_source"] is False))
        checks.append(("...and every sidecar fact becomes unconfirmed as a result", all(not f["confirmed"] and f["value"] is None for f in drifted_profile_facts["facts"])))
        drifted_contradictions = find_contradictions(art_facts, drifted_profile_facts)
        checks.append(("an unconfirmed (drifted) sidecar fact can no longer even agree - it asserts nothing, so it also can't contradict", not any(c["kind"] == "shockwave_device_type" for c in drifted_contradictions)))

        # revert profiles_md_path back to the hash the sidecar declares, for the rest of the test
        with open(profiles_md_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("# gbp-profiles.md fixture\n\nShockwave devices are FOCUSED (fESWT), never radial.\n")

        manifest, read_at, _af, _pf = build_source_manifest(tmp, art_yaml_path, confirmations_path, gbp_profiles_md_path=profiles_md_path, now=now)
        checks.append(("manifest carries parser_version", manifest["parser_version"] == PARSER_VERSION))
        checks.append(("manifest carries three source entries (art.yaml, sidecar, gbp-profiles.md)", len(manifest["sources"]) == 3))
        h1 = manifest_hash(manifest)
        import time as _time
        _time.sleep(0.01)
        manifest2, read_at2, _af2, _pf2 = build_source_manifest(tmp, art_yaml_path, confirmations_path, gbp_profiles_md_path=profiles_md_path, now=None)
        h2 = manifest_hash(manifest2)
        checks.append(("manifest_hash is stable across independent reads with different real timestamps (timestamp excluded from the hash)", h1 == h2 and read_at["art_yaml"] != read_at2["art_yaml"]))
        checks.append(("read_at is tracked separately from the hashed manifest", "art_yaml" in read_at and "art_yaml" not in json.dumps(manifest)))

        # Mutate art.yaml and confirm the hash changes - proves the hash is sensitive to the
        # actual source content, not just present for show.
        with open(art_yaml_path, "a", encoding="utf-8", newline="\n") as f:
            f.write("\n# a trailing comment does not change parsed facts\n")
        manifest3, _r3, _a3, _p3 = build_source_manifest(tmp, art_yaml_path, confirmations_path, gbp_profiles_md_path=profiles_md_path, now=now)
        checks.append(("a comment-only edit does not change the projection hash (parses to the same facts)", manifest_hash(manifest3) == h1))

        _write_art_yaml(device_type="radial")
        manifest4, _r4, art_facts4, _p4 = build_source_manifest(tmp, art_yaml_path, confirmations_path, gbp_profiles_md_path=profiles_md_path, now=now)
        checks.append(("a real content change DOES change the projection hash", manifest_hash(manifest4) != h1))

        contradictions4 = find_contradictions(art_facts4, profile_facts)
        checks.append(("a genuine disagreement (radial vs focused) is caught as a contradiction", any(c["kind"] == "shockwave_device_type" and c["art_yaml_value"] == "radial" for c in contradictions4)))

        threw_missing_art = False
        try:
            parse_art_yaml(os.path.join(tmp, "does-not-exist.yaml"), now=now)
        except FileNotFoundError:
            threw_missing_art = True
        checks.append(("a missing art.yaml fails closed (raises), never silently skipped", threw_missing_art))

        # --- malformed-input hardening (Codex review, 2026-08-31) ---------------------------
        with open(art_yaml_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("- just\n- a\n- list\n")
        threw_list_root = False
        try:
            parse_art_yaml(art_yaml_path, now=now)
        except ValueError as e:
            threw_list_root = "does not contain a mapping" in str(e)
        checks.append(("a non-mapping YAML root in art.yaml is a clear ValueError, not a crash or silent empty result", threw_list_root))

        with open(art_yaml_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("services_and_locations:\n  locations:\n    - status: active\n  service_constraints: []\n")  # missing name
        threw_no_name = False
        try:
            parse_art_yaml(art_yaml_path, now=now)
        except ValueError as e:
            threw_no_name = "no name" in str(e)
        checks.append(("a location entry with no name is a clear ValueError, not silently dropped", threw_no_name))

        with open(confirmations_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("not: valid: yaml: [[[")
        threw_malformed_sidecar = False
        try:
            parse_gbp_profile_confirmations(confirmations_path, gbp_profiles_content=open(profiles_md_path, encoding='utf-8').read(), now=now)
        except yaml.YAMLError:
            threw_malformed_sidecar = True
        checks.append(("a malformed sidecar YAML file raises rather than returning silently-empty/wrong facts", threw_malformed_sidecar))

        with open(confirmations_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(f'source_content_hash: "{prose_hash}"\nfacts:\n  - kind: shockwave_device_type\n    value: "focused (fESWT)"\n')  # missing id
        threw_no_id = False
        try:
            parse_gbp_profile_confirmations(confirmations_path, gbp_profiles_content=open(profiles_md_path, encoding='utf-8').read(), now=now)
        except ValueError as e:
            threw_no_id = "missing id/kind" in str(e)
        checks.append(("a sidecar fact with no id is a clear ValueError, not silently dropped", threw_no_id))

        with open(art_yaml_path, "w", encoding="utf-8", newline="\n") as f:
            f.write('services_and_locations:\n  locations:\n    - name: Greeley\n      status: active\n    - name: "  greeley  "\n      status: active\n  service_constraints: []\n')
        threw_dup_location = False
        try:
            parse_art_yaml(art_yaml_path, now=now)
        except ValueError as e:
            threw_dup_location = "duplicate location" in str(e)
        checks.append(("a duplicate location name in art.yaml is a clear ValueError, not silently ambiguous", threw_dup_location))

        with open(art_yaml_path, "w", encoding="utf-8", newline="\n") as f:
            f.write('services_and_locations:\n  locations: []\n  service_constraints:\n    - service: "X"\n      offered: true\n    - service: "X"\n      offered: false\n')
        threw_dup_service = False
        try:
            parse_art_yaml(art_yaml_path, now=now)
        except ValueError as e:
            threw_dup_service = "duplicate service_constraints" in str(e)
        checks.append(("a duplicate service name in service_constraints is a clear ValueError", threw_dup_service))

        with open(art_yaml_path, "w", encoding="utf-8", newline="\n") as f:
            f.write("no_services_here: true\n")
        threw_missing_section = False
        try:
            parse_art_yaml(art_yaml_path, now=now)
        except ValueError as e:
            threw_missing_section = "no services_and_locations section" in str(e)
        checks.append(("a file entirely missing services_and_locations is a clear ValueError, not a silent empty result", threw_missing_section))

        with open(confirmations_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(f'source_content_hash: "{prose_hash}"\nfacts:\n  - id: dup\n    kind: credential_wording\n    value: "a"\n  - id: dup\n    kind: credential_wording\n    value: "b"\n')
        threw_dup_fact = False
        try:
            parse_gbp_profile_confirmations(confirmations_path, gbp_profiles_content=open(profiles_md_path, encoding="utf-8").read(), now=now)
        except ValueError as e:
            threw_dup_fact = "duplicate fact id" in str(e)
        checks.append(("a duplicate fact id in the sidecar is a clear ValueError, not silently ambiguous", threw_dup_fact))

        # --- fail-closed sidecar verification when gbp_profiles_content is unavailable
        # (Codex review, 2026-08-31 follow-up: the previous optional gbp_profiles_md_path
        # parameter let a caller silently skip verification by passing None) -----------------
        with open(confirmations_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(f'source_content_hash: "{prose_hash}"\nfacts:\n  - id: x\n    kind: credential_wording\n    value: "a"\n    confirmed_date: "{_iso(1)}"\n')
        no_content_facts = parse_gbp_profile_confirmations(confirmations_path, gbp_profiles_content=None, now=now)
        checks.append(("omitting gbp_profiles_content fails closed (unverified), never silently skips verification", no_content_facts["sidecar_verified_against_source"] is False and not no_content_facts["facts"][0]["confirmed"]))

        _write_art_yaml()  # restore a valid art.yaml - the prior test deliberately broke it
        threw_missing_profiles_md = False
        try:
            build_source_manifest(tmp, art_yaml_path, confirmations_path, os.path.join(tmp, "does-not-exist.md"), now=now)
        except FileNotFoundError:
            threw_missing_profiles_md = True
        checks.append(("build_source_manifest with a missing gbp-profiles.md path raises (fails closed), never silently proceeds unverified", threw_missing_profiles_md))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    print(f"{len(checks) - failed}/{len(checks)} checks passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    import sys as _sys
    if "--verify" in _sys.argv:
        _self_test()

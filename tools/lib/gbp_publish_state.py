# GBP Posts plan Phase 5 - approval-time target resolution and payload-hash locking.
#
# "Resolve and lock the concrete target at approval time, not publish time" (the plan's own
# wording): the accountId/locationId/Place ID for a proposal's location must be resolved from
# `locations.json` (Phase 6's mapping) and recorded ONTO THE APPROVAL EVENT when a human
# approves a GBP post proposal - never re-resolved fresh at publish time from a mapping that
# could have been edited in between, which would let an edited mapping silently redirect an
# already-approved post to a different (still-allowed) location without invalidating the
# approval. publish_gbp_post.py (Phase 6, unbuilt - needs live Google API access this
# workspace does not have) re-resolves LIVE against the real API and requires an exact match
# to these locked IDs; that live half cannot be built or tested without Phase 0.
#
# The event log is the sole commit point (plan Phase 5): the approval event, carrying the
# hash and locked IDs, IS the approval - the proposal JSON's status field is a disposable
# projection rebuilt from it, exactly like the pre-existing Codex-review subsystem's own
# projection (event_log.sync_proposal_projection). This module never trusts a cached JSON
# field for anything authorization-adjacent; it always replays events.jsonl fresh.
import json
import os
from datetime import datetime, timezone

try:
    from .event_log import read_events_fail_closed
    from .gbp_constraints import _normalize_name
except ImportError:
    from event_log import read_events_fail_closed
    from gbp_constraints import _normalize_name

APPROVAL_SCHEMA_VERSION = 1

# Structurally impossible to approve for, not merely absent from locations.json by
# convention (Codex review, 2026-09-01: a name typed into a verified locations.json entry
# would otherwise resolve happily regardless of what the file's own comments say). UNC
# Campus is footer-only, never a real GBP profile (gbp-profiles.md); Thornton is a closed
# former location (art.yaml). Checked unconditionally, even against a "verified": true entry.
#
# Compared via the SAME _normalize_name() (NFKC + casefold + whitespace-collapse) that
# gbp_constraints.py already uses for this exact class of problem - a second Codex round
# (2026-09-01) found the first version's exact-string check let "Thornton " / "UNC campus"
# slip through untouched.
FORBIDDEN_LOCATION_NAMES = {"UNC Campus", "Thornton"}
_FORBIDDEN_LOCATION_NAMES_NORMALIZED = {_normalize_name(name) for name in FORBIDDEN_LOCATION_NAMES}


def _is_forbidden_location_name(name):
    return isinstance(name, str) and _normalize_name(name) in _FORBIDDEN_LOCATION_NAMES_NORMALIZED

# The exact fields of a proposal's `implementation` that are semantically load-bearing for
# the hash - deliberately excludes bookkeeping (drafted_at/drafted_by/constraint_manifest
# itself, whose CONTENT is already summarized by constraint_manifest_hash below) so a purely
# administrative re-draft timestamp never falsely looks like a content change. An unknown/
# extra field present on `implementation` but not in this tuple is deliberately NOT hashed -
# this is a fixed allowlist of what Phase 6 will actually read to build a real STANDARD-post
# API payload, not an oversight; if a future post type or field genuinely becomes part of
# what gets published, it must be added here explicitly.
_HASHED_IMPLEMENTATION_FIELDS = ("post_type", "summary", "call_to_action", "constraint_manifest_hash")


def _now_iso(now=None):
    now = now or datetime.now(timezone.utc)
    return now.isoformat().replace("+00:00", "Z")


def _sha256_json(value):
    import hashlib
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def load_location_mapping(locations_json_path):
    """Returns (mapping_version, locations_by_name). Raises FileNotFoundError if the mapping
    is missing, ValueError if it's malformed - fail closed, matching every other GBP source
    parser in this workspace (gbp_constraints.py's art.yaml/sidecar parsers)."""
    with open(locations_json_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"gbp_publish_state: {locations_json_path} does not contain a mapping at its root")
    mapping_version = raw.get("version")
    if not isinstance(mapping_version, int) or isinstance(mapping_version, bool):
        raise ValueError(f"gbp_publish_state: {locations_json_path}'s version must be an integer")
    raw_locations = raw.get("locations")
    if not isinstance(raw_locations, list):
        raise ValueError(f"gbp_publish_state: {locations_json_path}'s locations field is not a list")

    locations_by_name = {}
    seen_normalized = {}
    for entry in raw_locations:
        if not isinstance(entry, dict) or not entry.get("name"):
            raise ValueError(f"gbp_publish_state: {locations_json_path} contains a malformed location entry (missing name)")
        name = entry["name"]
        if _is_forbidden_location_name(name):
            raise ValueError(f'gbp_publish_state: {locations_json_path} contains a structurally-forbidden location "{name}" (UNC Campus/Thornton must never appear in this file at all, in any spelling/casing)')
        if name in locations_by_name:
            raise ValueError(f'gbp_publish_state: {locations_json_path} contains a duplicate location "{name}" - ambiguous, refusing to guess which is authoritative')
        # A case/whitespace variant of an already-seen name (e.g. "Greeley" and " greeley ")
        # is just as ambiguous as an exact duplicate, but the check above alone would miss it
        # and silently create two independent map entries neither lookup could reliably
        # distinguish (Codex review, 2026-09-01, fifth round - MINOR). Only a same-target
        # duplicate check, never a change to resolve_locked_target's own lookup key, which
        # must stay an exact match against whatever name a proposal's target actually names.
        normalized = _normalize_name(name)
        if normalized in seen_normalized:
            raise ValueError(f'gbp_publish_state: {locations_json_path} contains "{name}", a case/whitespace variant of already-listed "{seen_normalized[normalized]}" - ambiguous, refusing to guess which is authoritative')
        seen_normalized[normalized] = name
        locations_by_name[name] = entry
    return mapping_version, locations_by_name


def resolve_locked_target(locations_by_name, location_name):
    """Returns {"account_id", "location_id", "place_id"} for a VERIFIED location. Raises
    ValueError - a real, human-actionable refusal - if the location is absent from the
    mapping entirely, present but not yet verified (the expected state for every location
    until a human completes Phase 0 and sets verified: true with real IDs), verified but
    missing any of the three identifiers, or one of the structurally-forbidden names.

    The forbidden-name check here is a second, independent line of defense on top of
    load_location_mapping's own check - this function must refuse on its own even if ever
    called with a `locations_by_name` dict assembled some other way (Codex review,
    2026-09-01: "unlisted by convention" is not the same as "impossible"). Both checks compare
    via _normalize_name() - a plain "in FORBIDDEN_LOCATION_NAMES" membership test let a
    differently-cased or whitespace-padded spelling of either name slip through untouched
    (Codex review, 2026-09-01, second round)."""
    if _is_forbidden_location_name(location_name):
        raise ValueError(f'gbp_publish_state: "{location_name}" is structurally forbidden as a GBP Post target (footer-only or closed location) - never valid regardless of what any mapping file says')
    entry = locations_by_name.get(location_name)
    if entry is None:
        raise ValueError(f'gbp_publish_state: "{location_name}" is not in locations.json at all - refusing to guess an identifier for it')
    if entry.get("verified") is not True:
        raise ValueError(
            f'gbp_publish_state: "{location_name}" is in locations.json but not yet verified (verified: {entry.get("verified")!r}) - '
            "a human must complete Phase 0 (Google Cloud/OAuth setup) and the one-time verified pairing between "
            "DataForSEO's and Google's identifiers before this location can be approved for publishing"
        )
    account_id = entry.get("account_id")
    location_id = entry.get("location_id")
    place_id = entry.get("place_id")
    if not isinstance(account_id, str) or not account_id or not isinstance(location_id, str) or not location_id or not isinstance(place_id, str) or not place_id:
        raise ValueError(f'gbp_publish_state: "{location_name}" is marked verified but is missing account_id/location_id/place_id (or one of them is not a string) - refusing to trust a partially-filled or malformed entry')
    return {"account_id": account_id, "location_id": location_id, "place_id": place_id}


def compute_approved_payload_hash(implementation, target, locked_target, mapping_version):
    """The hash that goes onto the approval event - sensitive to a content change in the
    drafted Post, a change in which physical location is locked, or a change in which
    mapping revision authorized that location, but NOT to purely administrative fields
    (drafted_at, an unrelated re-verification timestamp elsewhere)."""
    payload = {
        "implementation": {field: implementation.get(field) for field in _HASHED_IMPLEMENTATION_FIELDS},
        "target": {"location": target.get("location"), "topic": target.get("topic")},
        "locked_target": locked_target,
        "mapping_version": mapping_version,
        "approval_schema_version": APPROVAL_SCHEMA_VERSION,
    }
    return _sha256_json(payload)


def sync_gbp_approval_lock(loop_dir, proposal_id, fail_closed=True):
    """Replays events.jsonl for this proposal and returns the most recent approval-lock
    event's fields (payload_hash/locked_location_id/locked_account_id/locked_place_id/
    location_mapping_version), or None if the proposal has never been approved through this
    path. Fail-closed by default (raises on log corruption), matching every other
    authorization-adjacent read in this workspace (event_log.read_events_fail_closed).

    This is a HISTORICAL fact ("what was locked when this proposal was last approved"), not
    a claim about current status - a human can still reject an already-approved proposal
    afterward (`/review-pending --reject`, which never calls into this module at all), and
    this function would keep returning the old lock unchanged, correctly, since the lock
    itself never becomes untrue. Any caller (Phase 6's publish_gbp_post.py) MUST separately
    check the proposal's CURRENT status via event_log.sync_proposal_projection() before
    trusting this lock is still actionable - the two facts answer different questions and
    neither substitutes for the other (Codex review, 2026-09-01)."""
    events = read_events_fail_closed(loop_dir, proposal_id) if fail_closed else []
    if not fail_closed:
        try:
            from .event_log import read_events
        except ImportError:
            from event_log import read_events
        events = read_events(loop_dir, proposal_id=proposal_id)

    latest = None
    for ev in events:
        if ev.get("event_type") == "gbp_proposal_approved" and ev.get("payload_hash"):
            latest = ev
    if latest is None:
        return None
    return {
        "payload_hash": latest.get("payload_hash"),
        "locked_location_id": latest.get("locked_location_id"),
        "locked_account_id": latest.get("locked_account_id"),
        "locked_place_id": latest.get("locked_place_id"),
        "location_mapping_version": latest.get("location_mapping_version"),
        "approved_at": latest.get("at"),
    }


def _self_test():
    import shutil
    import sys
    import tempfile

    checks = []
    tmp = tempfile.mkdtemp(prefix="gbp-publish-state-test-")
    locations_path = os.path.join(tmp, "locations.json")
    try:
        with open(locations_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({
                "version": 3,
                "locations": [
                    {"name": "Greeley", "verified": True, "account_id": "acc-1", "location_id": "loc-1", "place_id": "place-1"},
                    {"name": "Denver", "verified": False, "account_id": None, "location_id": None, "place_id": None},
                ],
            }, f)

        mapping_version, by_name = load_location_mapping(locations_path)
        checks.append(("mapping_version is read as an int", mapping_version == 3))
        checks.append(("both locations are parsed", set(by_name) == {"Greeley", "Denver"}))

        locked = resolve_locked_target(by_name, "Greeley")
        checks.append(("a verified location resolves its real IDs", locked == {"account_id": "acc-1", "location_id": "loc-1", "place_id": "place-1"}))

        threw_unverified = False
        try:
            resolve_locked_target(by_name, "Denver")
        except ValueError as e:
            threw_unverified = "not yet verified" in str(e)
        checks.append(("an unverified location is refused, never resolved with null IDs", threw_unverified))

        threw_unknown = False
        try:
            resolve_locked_target(by_name, "Nowhere")
        except ValueError as e:
            threw_unknown = "not in locations.json at all" in str(e)
        checks.append(("an unlisted location is refused", threw_unknown))

        threw_forbidden = False
        try:
            resolve_locked_target(by_name, "UNC Campus")
        except ValueError as e:
            threw_forbidden = "structurally forbidden" in str(e)
        checks.append(("resolve_locked_target refuses UNC Campus even if not in the mapping at all (independent check)", threw_forbidden))

        threw_forbidden_thornton = False
        try:
            resolve_locked_target(by_name, "Thornton")
        except ValueError as e:
            threw_forbidden_thornton = "structurally forbidden" in str(e)
        checks.append(("resolve_locked_target refuses Thornton the same way", threw_forbidden_thornton))

        by_name_with_forbidden_verified = dict(by_name)
        by_name_with_forbidden_verified["UNC Campus"] = {"name": "UNC Campus", "verified": True, "account_id": "acc-x", "location_id": "loc-x", "place_id": "place-x"}
        threw_forbidden_even_verified = False
        try:
            resolve_locked_target(by_name_with_forbidden_verified, "UNC Campus")
        except ValueError as e:
            threw_forbidden_even_verified = "structurally forbidden" in str(e)
        checks.append(("resolve_locked_target refuses UNC Campus even if handed a verified entry directly", threw_forbidden_even_verified))

        threw_missing_place_id = False
        by_name_missing_place = {"Greeley": {"name": "Greeley", "verified": True, "account_id": "acc-1", "location_id": "loc-1", "place_id": None}}
        try:
            resolve_locked_target(by_name_missing_place, "Greeley")
        except ValueError as e:
            threw_missing_place_id = "missing account_id/location_id/place_id" in str(e)
        checks.append(("a verified entry missing place_id is refused, not silently resolved with place_id=None", threw_missing_place_id))

        threw_non_string_id = False
        by_name_non_string = {"Greeley": {"name": "Greeley", "verified": True, "account_id": 12345, "location_id": "loc-1", "place_id": "place-1"}}
        try:
            resolve_locked_target(by_name_non_string, "Greeley")
        except ValueError as e:
            threw_non_string_id = "missing account_id/location_id/place_id" in str(e)
        checks.append(("a verified entry with a non-string id (e.g. a bare number) is refused, not silently locked", threw_non_string_id))

        # Case/whitespace variants of a forbidden name must not slip through either check
        # (Codex review, 2026-09-01, second round: exact-string membership let "Thornton " /
        # "unc campus" through untouched).
        threw_forbidden_variant_resolve = False
        try:
            resolve_locked_target(by_name, "  unc CAMPUS  ")
        except ValueError as e:
            threw_forbidden_variant_resolve = "structurally forbidden" in str(e)
        checks.append(("resolve_locked_target refuses a case/whitespace variant of UNC Campus too", threw_forbidden_variant_resolve))

        # A zero-width space is invisible in an editor but NFKC+casefold+whitespace-collapse
        # alone does not touch it - Codex review, 2026-09-01, third round.
        threw_forbidden_zwsp = False
        try:
            resolve_locked_target(by_name, "UNC" + chr(0x200b) + "Campus")
        except ValueError as e:
            threw_forbidden_zwsp = "structurally forbidden" in str(e)
        checks.append(("resolve_locked_target refuses a zero-width-space variant of UNC Campus too", threw_forbidden_zwsp))

        threw_forbidden_variant_load = False
        with open(locations_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"version": 1, "locations": [{"name": "thornton", "verified": True, "account_id": "a", "location_id": "l", "place_id": "p"}]}, f)
        try:
            load_location_mapping(locations_path)
        except ValueError as e:
            threw_forbidden_variant_load = "structurally-forbidden" in str(e)
        checks.append(("load_location_mapping refuses a lowercase spelling of Thornton too", threw_forbidden_variant_load))

        threw_load_forbidden = False
        with open(locations_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"version": 1, "locations": [{"name": "UNC Campus", "verified": True, "account_id": "a", "location_id": "l", "place_id": "p"}]}, f)
        try:
            load_location_mapping(locations_path)
        except ValueError as e:
            threw_load_forbidden = "structurally-forbidden" in str(e)
        checks.append(("load_location_mapping refuses a file that contains UNC Campus/Thornton at all", threw_load_forbidden))

        # Restore a valid mapping for the rest of the checks below.
        with open(locations_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({
                "version": 3,
                "locations": [
                    {"name": "Greeley", "verified": True, "account_id": "acc-1", "location_id": "loc-1", "place_id": "place-1"},
                    {"name": "Denver", "verified": False, "account_id": None, "location_id": None, "place_id": None},
                ],
            }, f)
        mapping_version, by_name = load_location_mapping(locations_path)
        locked = resolve_locked_target(by_name, "Greeley")

        implementation = {"post_type": "STANDARD", "summary": "Test summary", "call_to_action": {"action_type": "LEARN_MORE", "url": "https://example.invalid/"}, "constraint_manifest_hash": "abc123", "drafted_at": "2026-09-01T00:00:00Z"}
        target = {"location": "Greeley", "topic": "some-gap"}
        h1 = compute_approved_payload_hash(implementation, target, locked, mapping_version)
        h2 = compute_approved_payload_hash(dict(implementation, drafted_at="2026-09-02T00:00:00Z"), target, locked, mapping_version)
        checks.append(("hash is stable across a purely administrative field change (drafted_at)", h1 == h2))
        h3 = compute_approved_payload_hash(dict(implementation, summary="A different summary"), target, locked, mapping_version)
        checks.append(("hash changes when the actual drafted content changes", h1 != h3))
        h4 = compute_approved_payload_hash(implementation, target, locked, mapping_version + 1)
        checks.append(("hash changes when the mapping version changes", h1 != h4))
        h5 = compute_approved_payload_hash(implementation, target, {**locked, "location_id": "loc-2"}, mapping_version)
        checks.append(("hash changes when the locked location_id changes", h1 != h5))

        threw_malformed_version = False
        with open(locations_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"version": "not-an-int", "locations": []}, f)
        try:
            load_location_mapping(locations_path)
        except ValueError as e:
            threw_malformed_version = "version must be an integer" in str(e)
        checks.append(("a non-integer version is a clear ValueError", threw_malformed_version))

        with open(locations_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"version": 1, "locations": [{"name": "X"}, {"name": "X"}]}, f)
        threw_dup = False
        try:
            load_location_mapping(locations_path)
        except ValueError as e:
            threw_dup = "duplicate location" in str(e)
        checks.append(("a duplicate location name is a clear ValueError", threw_dup))

        with open(locations_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"version": 1, "locations": [{"name": "Greeley"}, {"name": " greeley "}]}, f)
        threw_normalized_dup = False
        try:
            load_location_mapping(locations_path)
        except ValueError as e:
            threw_normalized_dup = "case/whitespace variant" in str(e)
        checks.append(("a case/whitespace variant of an already-listed location name is a clear ValueError too", threw_normalized_dup))

        threw_missing_file = False
        try:
            load_location_mapping(os.path.join(tmp, "does-not-exist.json"))
        except FileNotFoundError:
            threw_missing_file = True
        checks.append(("a missing locations.json fails closed (raises)", threw_missing_file))

        # --- event-sourced approval-lock projection ---
        try:
            from .event_log import append_event
        except ImportError:
            from event_log import append_event
        loop_dir = os.path.join(tmp, "loops", "gbp")
        os.makedirs(loop_dir, exist_ok=True)
        no_lock = sync_gbp_approval_lock(loop_dir, "prop-1")
        checks.append(("a never-approved proposal has no approval lock", no_lock is None))

        append_event(
            loop_dir, "gbp_proposal_approved", project="art", loop="gbp", proposal_id="prop-1",
            payload_hash=h1, locked_location_id="loc-1", locked_account_id="acc-1", locked_place_id="place-1", location_mapping_version=3,
            resulting_proposal_status="approved",
        )
        lock1 = sync_gbp_approval_lock(loop_dir, "prop-1")
        checks.append(("an approved proposal's lock is read back correctly", lock1["payload_hash"] == h1 and lock1["locked_location_id"] == "loc-1"))
        checks.append(("locked_place_id round-trips through the event-sourced lock", lock1["locked_place_id"] == "place-1"))

        # A second approval event (e.g. a re-approval after a revision) must be the one that wins.
        append_event(
            loop_dir, "gbp_proposal_approved", project="art", loop="gbp", proposal_id="prop-1",
            payload_hash=h3, locked_location_id="loc-1", locked_account_id="acc-1", location_mapping_version=3,
            resulting_proposal_status="approved",
        )
        lock2 = sync_gbp_approval_lock(loop_dir, "prop-1")
        checks.append(("the LATEST approval event wins, not the first", lock2["payload_hash"] == h3))

        other = sync_gbp_approval_lock(loop_dir, "prop-2")
        checks.append(("a different proposal_id is unaffected", other is None))
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

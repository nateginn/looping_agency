# DataForSEO local-pack connector - GBP Posts plan Phase 1 ("local_pack").
#
# Generalizes tools/serp_diagnostic.py's one-off local_pack detection (which
# runs off the organic SERP endpoint, google/organic/live/advanced - the SAME
# endpoint dataforseo.py's pull_metrics/pull_local_rank already call, but a
# DIFFERENT question: is our listing in the 3-pack shown above the organic
# results, not where our page ranks organically) into a real, scheduled,
# fixture-tested connector with its own registry entry, snapshot section and
# failure policy - see dataforseo_maps.py's header for why these two stay
# separate modules rather than merging back together.
#
# serp_diagnostic.py's original local_pack matcher used a name-substring
# heuristic ("accelerated rehab" in title.lower()) as a fallback when no URL
# was present - explicitly called out in the GBP plan as exactly the kind of
# matching this module must not do. This module matches ONLY by a verified
# place_id/cid (see locations[].place_id / locations[].cid in spec.md, the
# same human-verified identifiers dataforseo_maps.py uses) - never by name or
# domain, even a host-validated one (see _is_own_entry's docstring for why a
# domain fallback was removed after review). If a pack entry carries neither
# identifier, it cannot be attributed and is left unmatched rather than
# guessed at.
#
# Standing decision 4 (no competitor data stored) applies: only OUR entry's
# position and identifying fields are recorded. Competitor names, ratings, or
# URLs appearing in the same pack are counted but never persisted individually.
import json
import sys

try:
    from . import dataforseo
    from .lib.redact import redact_deep
except ImportError:
    import dataforseo
    from lib.redact import redact_deep

LOCAL_PACK_SCHEMA_VERSION = 1
LOCAL_PACK_ITEM_TYPES = ("local_pack", "map", "google_business_profile", "local_services")

ERR_TRANSPORT = dataforseo.ERR_TRANSPORT
ERR_HTTP = dataforseo.ERR_HTTP
ERR_MALFORMED = dataforseo.ERR_MALFORMED
ERR_NO_TASK = dataforseo.ERR_NO_TASK
ERR_KEYWORD_MISMATCH = dataforseo.ERR_KEYWORD_MISMATCH
ERR_TASK_STATUS = dataforseo.ERR_TASK_STATUS
ERR_EMPTY_RESULT = dataforseo.ERR_EMPTY_RESULT
ERR_LOCATION = dataforseo.ERR_LOCATION
ERR_NO_IDENTIFIER = "listing-has-no-identifier"


def _is_own_entry(entry, own_place_id, own_cid):
    """Identifier-only match - place_id or cid, never a name/domain heuristic.

    An earlier version of this function also accepted a host-validated
    domain/URL match as a fallback. A Codex adversarial review of this phase
    (2026-08-31) correctly flagged that as a real gap: it let a pack entry
    carrying a *different* business's place_id but a URL/domain match (e.g. a
    reseller page, a shared franchise domain, or simply an API quirk) get
    silently recorded as our own listing - exactly the class of matching this
    plan's Phase 1 section explicitly rules out ("never by domain-substring or
    business-name matching"). Identifier absence is now a configuration
    problem (see ERR_NO_IDENTIFIER below), not something a weaker fallback
    papers over."""
    if not isinstance(entry, dict):
        return False
    if own_place_id and entry.get("place_id") == own_place_id:
        return True
    if own_cid and entry.get("cid") == own_cid:
        return True
    return False


def analyze_local_pack(items, own_place_id, own_cid):
    """Pure function over one SERP's items - no I/O. Returns a dict with
    present/entry_count/site_in_pack/site_position/malformed, and never a
    competitor name, rating, or URL (standing decision 4).

    `malformed` is set whenever the API response shape does not match what
    this function knows how to interpret safely - a non-dict item, a
    local_pack-type item whose own `items` key is present but not a list, or
    a local_pack-type item that claims to be present but carries zero
    entries. The caller (pull_local_pack) treats a malformed observation as
    an explicit error, never as a trustworthy "not present" or "present with
    0 entries" reading - a Codex review of this phase (2026-08-31) found the
    original version returned `present: true, entry_count: 0` for an empty
    items list and raised an uncaught AttributeError for a non-dict item."""
    present = False
    entry_count = 0
    site_in_pack = False
    site_position = None
    malformed = False

    for item in items or []:
        if not isinstance(item, dict):
            malformed = True
            continue
        if item.get("type") not in LOCAL_PACK_ITEM_TYPES:
            continue
        raw_entries = item.get("items")
        if raw_entries is not None and not isinstance(raw_entries, list):
            malformed = True
            continue
        entries = raw_entries if isinstance(raw_entries, list) else [item]
        if not entries:
            malformed = True
            continue
        present = True
        for pos, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                malformed = True
                continue
            entry_count += 1
            if not site_in_pack and _is_own_entry(entry, own_place_id, own_cid):
                site_in_pack = True
                site_position = pos

    return {
        "present": present,
        "entry_count": entry_count,
        "site_in_pack": site_in_pack,
        "site_position": site_position,
        "malformed": malformed,
    }


def pull_local_pack(
    credential_alias=None,
    resolve_credential=None,
    targets=None,
    locations=None,
    language_code="en",
    device="desktop",
    depth=20,
    http_post=None,
    http_get=None,
):
    """One organic-SERP call per (location, keyword) target, checked for a
    local-pack block above the organic results. targets: list of
    {"keyword": str, "location": str}. locations: same shape as
    dataforseo_maps.py's (name/address/zip + place_id and/or cid).

    Requires a verified place_id and/or cid per referenced location - with
    neither, a populated pack can never be attributed and every row would be
    forced to "error", which is the correct outcome, not a silent no-op, so
    this refuses up front instead.
    """
    if not credential_alias:
        raise ValueError("dataforseo_local_pack.py: credential_alias is required")
    if not callable(resolve_credential):
        raise ValueError(
            f'dataforseo_local_pack.py: no credential resolver configured for alias "{credential_alias}". '
            "This connector refuses to run rather than guess."
        )
    if not targets:
        raise ValueError("dataforseo_local_pack.py: targets is required (non-empty list of {keyword, location})")
    if not locations:
        raise ValueError("dataforseo_local_pack.py: locations is required (non-empty list of location objects)")

    credentials = resolve_credential(credential_alias)
    secret_map = {credential_alias: credentials}
    headers = dataforseo._auth_headers(credentials)
    http_post = http_post or dataforseo._default_http_post
    http_get = http_get or dataforseo._default_http_get

    locations_by_name = {location.get("name"): location for location in locations}
    for target in targets:
        loc_name = target.get("location")
        if not loc_name or loc_name not in locations_by_name:
            raise ValueError(
                f'dataforseo_local_pack.py: target "{target.get("keyword")}" must reference a location name '
                f"present in locations (got {loc_name!r})"
            )

    rows = []
    request_count = 0
    location_code_cache = {}
    for target in targets:
        location = locations_by_name[target["location"]]
        own_place_id = location.get("place_id")
        own_cid = location.get("cid")

        if location["name"] not in location_code_cache:
            try:
                location_code_cache[location["name"]] = (dataforseo._resolve_location_code(credentials, location, http_get), None)
            except Exception:
                location_code_cache[location["name"]] = (None, ERR_LOCATION)
        (location_code, _resolved_name), location_error = (
            location_code_cache[location["name"]][0] or (None, None),
            location_code_cache[location["name"]][1],
        )

        if location_error is not None:
            rows.append({
                "location_name": location.get("name"),
                "keyword": target.get("keyword"),
                "status": "error",
                "error_reason": location_error,
                "present": None,
                "entry_count": None,
                "site_in_pack": None,
                "site_position": None,
            })
            continue

        if not own_place_id and not own_cid:
            rows.append({
                "location_name": location.get("name"),
                "keyword": target.get("keyword"),
                "status": "error",
                "error_reason": ERR_NO_IDENTIFIER,
                "present": None,
                "entry_count": None,
                "site_in_pack": None,
                "site_position": None,
            })
            continue

        request_count += 1
        items, error_reason, status_code = dataforseo._fetch_one_serp(
            headers, target["keyword"], location_code, language_code, device, depth, http_post
        )
        if error_reason is not None:
            rows.append({
                "location_name": location.get("name"),
                "keyword": target.get("keyword"),
                "status": "error",
                "error_reason": error_reason,
                "present": None,
                "entry_count": None,
                "site_in_pack": None,
                "site_position": None,
            })
            continue

        analysis = analyze_local_pack(items, own_place_id, own_cid)
        malformed = analysis.pop("malformed")
        if malformed:
            # An unrecognized response shape is missing/untrustworthy data,
            # never a legitimate "not present"/"present with 0 entries"
            # reading - see analyze_local_pack's docstring (Codex review,
            # 2026-08-31).
            rows.append({
                "location_name": location.get("name"),
                "keyword": target.get("keyword"),
                "status": "error",
                "error_reason": dataforseo.ERR_MALFORMED,
                "present": None,
                "entry_count": None,
                "site_in_pack": None,
                "site_position": None,
            })
            continue
        rows.append({
            "location_name": location.get("name"),
            "keyword": target.get("keyword"),
            "status": "ok",
            "error_reason": None,
            **analysis,
        })

    error_count = sum(1 for r in rows if r["status"] == "error")
    success_count = len(rows) - error_count
    if rows and error_count == len(rows):
        reasons = sorted({r["error_reason"] for r in rows if r.get("error_reason")})
        raise RuntimeError(
            f"dataforseo_local_pack.py: no usable local-pack read for any of "
            f'{len(rows)} targets ({", ".join(reasons)})'
        )

    out = {
        "source": "dataforseo-local-pack",
        "as_of": dataforseo._now_iso(),
        "schema_version": LOCAL_PACK_SCHEMA_VERSION,
        "request_count": request_count,
        "success_count": success_count,
        "error_count": error_count,
        "rows": rows,
        "secretMap": secret_map,
    }
    return redact_deep(out, secret_map)


def _self_test():
    checks = []
    D = "example.com"
    fake_secret = "sk-test-fake-local-pack-token"
    OWN_PLACE_ID = "ChIJ-own-place-id-invalid"

    a = analyze_local_pack(
        [
            {"type": "local_pack", "items": [
                {"title": "Competitor Clinic", "place_id": "ChIJ-comp", "rating": 4.2},
                {"title": "Our Clinic", "place_id": OWN_PLACE_ID, "rating": 4.9},
                {"title": "Other Competitor", "place_id": "ChIJ-comp-2"},
            ]},
            {"type": "organic", "url": "https://other.com/"},
        ],
        OWN_PLACE_ID, None,
    )
    checks.append(("finds our entry by place_id at the right position", a["present"] and a["site_in_pack"] and a["site_position"] == 2))
    checks.append(("counts every pack entry", a["entry_count"] == 3))
    checks.append(("well-formed data is never flagged malformed", not a["malformed"]))

    no_id = analyze_local_pack(
        [{"type": "local_pack", "items": [{"title": "Some Clinic", "url": f"https://www.{D}"}]}],
        None, None,
    )
    checks.append(("a URL/domain match is NOT accepted - identifier-only matching (Codex review finding)", no_id["present"] and not no_id["site_in_pack"]))

    conflicting_id = analyze_local_pack(
        [{"type": "local_pack", "items": [{"title": "Some Clinic", "url": f"https://www.{D}", "place_id": "ChIJ-some-other-listing"}]}],
        OWN_PLACE_ID, None,
    )
    checks.append(("a pack entry sharing our domain but a DIFFERENT place_id is never matched as us", not conflicting_id["site_in_pack"]))

    empty = analyze_local_pack([], OWN_PLACE_ID, None)
    checks.append(("no pack present degrades cleanly", not empty["present"] and not empty["site_in_pack"] and not empty["malformed"]))

    empty_items = analyze_local_pack([{"type": "local_pack", "items": []}], OWN_PLACE_ID, None)
    checks.append(("a local_pack block with zero entries is malformed, never 'present with 0 entries'", empty_items["malformed"] and not empty_items["present"]))

    non_list_items = analyze_local_pack([{"type": "local_pack", "items": "not-a-list"}], OWN_PLACE_ID, None)
    checks.append(("a local_pack block whose items key isn't a list is malformed, not a crash", non_list_items["malformed"]))

    non_dict_item = analyze_local_pack(["not-a-dict", {"type": "local_pack", "items": [{"place_id": OWN_PLACE_ID}]}], OWN_PLACE_ID, None)
    checks.append(("a non-dict item in the SERP list is malformed, not an AttributeError crash", non_dict_item["malformed"] and non_dict_item["site_in_pack"]))

    non_dict_entry = analyze_local_pack([{"type": "local_pack", "items": ["not-a-dict-entry", {"place_id": OWN_PLACE_ID}]}], OWN_PLACE_ID, None)
    checks.append(("a non-dict entry inside items is malformed but still finds a valid sibling entry", non_dict_entry["malformed"] and non_dict_entry["site_in_pack"]))

    LOCATIONS = [{"name": "Greeley", "address": "1823 65th Ave Suite 3 Greeley, CO 80634", "zip": "80634", "place_id": OWN_PLACE_ID}]
    TARGETS = [{"keyword": "physical therapy greeley", "location": "Greeley"}]

    def fake_locations(url, headers):
        payload = {"tasks": [{"result": [
            {"location_code": 100, "location_name": "Greeley,Colorado,United States"},
            {"location_code": 200, "location_name": "Denver,Colorado,United States"},
        ]}]}
        return 200, "OK", json.dumps(payload).encode("utf-8")

    sent = []

    def fake_serp_ok(url, headers, body_bytes):
        body = json.loads(body_bytes)
        sent.append(body)
        items = [
            {"type": "local_pack", "rank_absolute": 1, "items": [
                {"title": "Competitor", "place_id": "ChIJ-comp", "rating": 4.1},
                {"title": "Accelerated Rehab Therapy", "place_id": OWN_PLACE_ID},
            ]},
            {"type": "organic", "rank_absolute": 4, "url": f"https://{D}/physical-therapy/"},
        ]
        return 200, "OK", json.dumps({"tasks": [{"status_code": 20000, "data": {"keyword": body[0]["keyword"]}, "result": [{"items": items}]}]}).encode("utf-8")

    result = pull_local_pack(
        credential_alias="acme-dataforseo-read",
        resolve_credential=lambda alias: fake_secret,
        targets=TARGETS,
        locations=LOCATIONS,
        http_post=fake_serp_ok,
        http_get=fake_locations,
    )
    row = result["rows"][0]
    checks.append(("connector reports ok status with our pack position", row["status"] == "ok" and row["site_in_pack"] and row["site_position"] == 2))
    checks.append(("no competitor place_id or title leaks into the section", "ChIJ-comp" not in json.dumps(result) and "Competitor" not in json.dumps(result)))
    checks.append(("credential never leaks unredacted", fake_secret not in json.dumps(result)))
    checks.append(("one POST per target", len(sent) == 1))
    checks.append(("schema_version is announced", result["schema_version"] == LOCAL_PACK_SCHEMA_VERSION))

    no_id_locations = [
        {"name": "Greeley", "address": "1823 65th Ave Suite 3 Greeley, CO 80634", "zip": "80634"},
        {"name": "Verified", "address": "2480 W 26th Ave #90B, Denver, CO 80211", "zip": "80211", "place_id": OWN_PLACE_ID},
    ]
    no_id_result = pull_local_pack(
        credential_alias="acme-dataforseo-read",
        resolve_credential=lambda alias: fake_secret,
        targets=[{"keyword": "physical therapy greeley", "location": "Greeley"}, {"keyword": "physical therapy denver", "location": "Verified"}],
        locations=no_id_locations,
        http_post=fake_serp_ok,
        http_get=fake_locations,
    )
    no_id_row = next(r for r in no_id_result["rows"] if r["location_name"] == "Greeley")
    checks.append(("a location with no verified place_id/cid errors rather than guessing", no_id_row["status"] == "error" and no_id_row["error_reason"] == ERR_NO_IDENTIFIER))

    def fake_locations_greeley_only(url, headers):
        return 200, "OK", json.dumps({"tasks": [{"result": [{"location_code": 100, "location_name": "Greeley,Colorado,United States"}]}]}).encode("utf-8")

    mixed_locations = LOCATIONS + [{"name": "Denver", "address": "2480 W 26th Ave #90B, Denver, CO 80211", "zip": "80211", "place_id": OWN_PLACE_ID}]
    loc_fail_result = pull_local_pack(
        credential_alias="acme-dataforseo-read",
        resolve_credential=lambda alias: fake_secret,
        targets=TARGETS + [{"keyword": "physical therapy denver", "location": "Denver"}],
        locations=mixed_locations,
        http_post=fake_serp_ok,
        http_get=fake_locations_greeley_only,
    )
    denver_fail_row = next(r for r in loc_fail_result["rows"] if r["location_name"] == "Denver")
    checks.append(("an unresolvable location errors only its own row, not the others", denver_fail_row["status"] == "error" and denver_fail_row["error_reason"] == ERR_LOCATION))

    # --- failure classification, mirroring dataforseo.py's own coverage for
    # the shared _fetch_one_serp path (Codex review, 2026-08-31: this
    # connector's own suite should assert these explicitly rather than rely
    # on dataforseo.py's suite alone, since a future refactor could silently
    # stop routing through _fetch_one_serp here). --------------------------
    def _serp_with_mode(mode):
        def fake(url, headers, body_bytes):
            keyword = json.loads(body_bytes)[0]["keyword"]
            if keyword != "physical therapy greeley":
                return fake_serp_ok(url, headers, body_bytes)
            if mode == "rejected":
                return 200, "OK", json.dumps({"tasks": [{"status_code": 40000, "data": {"keyword": keyword}, "result": None}]}).encode("utf-8")
            if mode == "http-500":
                return 500, "Server Error", b"upstream exploded"
            if mode == "transport":
                raise OSError("connection reset")
            if mode == "wrong-keyword":
                return 200, "OK", json.dumps({"tasks": [{"status_code": 20000, "data": {"keyword": "some other keyword"}, "result": [{"items": []}]}]}).encode("utf-8")
            if mode == "empty-result":
                return 200, "OK", json.dumps({"tasks": [{"status_code": 20000, "data": {"keyword": keyword}, "result": None}]}).encode("utf-8")
            if mode == "malformed-pack":
                items = [{"type": "local_pack", "items": []}]
                return 200, "OK", json.dumps({"tasks": [{"status_code": 20000, "data": {"keyword": keyword}, "result": [{"items": items}]}]}).encode("utf-8")
            raise AssertionError(mode)
        return fake

    two_targets = TARGETS + [{"keyword": "physical therapy denver", "location": "Denver"}]

    def _greeley_row(mode):
        out = pull_local_pack(
            credential_alias="acme-dataforseo-read",
            resolve_credential=lambda alias: fake_secret,
            targets=two_targets,
            locations=mixed_locations,
            http_post=_serp_with_mode(mode),
            http_get=fake_locations,
        )
        return out, next(r for r in out["rows"] if r["location_name"] == "Greeley")

    rejected_out, rejected_row = _greeley_row("rejected")
    checks.append(("a rejected task is an error, never an absence", rejected_row["status"] == "error" and rejected_row["error_reason"] == ERR_TASK_STATUS))
    checks.append(("one failed target does not discard the other row", rejected_out["success_count"] == 1 and rejected_out["error_count"] == 1))
    _, http_row = _greeley_row("http-500")
    checks.append(("a non-2xx response is an error, not a crash", http_row["error_reason"] == ERR_HTTP))
    _, transport_row = _greeley_row("transport")
    checks.append(("a transport failure is an error, not an absence", transport_row["status"] == "error" and transport_row["error_reason"] == ERR_TRANSPORT))
    _, wrong_kw_row = _greeley_row("wrong-keyword")
    checks.append(("a task echoing a different keyword is an error, never attributed to this target", wrong_kw_row["status"] == "error" and wrong_kw_row["error_reason"] == ERR_KEYWORD_MISMATCH))
    _, empty_result_row = _greeley_row("empty-result")
    checks.append(("a 20000 task with no result is an error, never an absence", empty_result_row["status"] == "error" and empty_result_row["error_reason"] == ERR_EMPTY_RESULT))
    _, malformed_row = _greeley_row("malformed-pack")
    checks.append(("a malformed local_pack block (0 entries) surfaces as an explicit error, not a false 'present'", malformed_row["status"] == "error" and malformed_row["error_reason"] == dataforseo.ERR_MALFORMED))

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    print(f"{len(checks) - failed}/{len(checks)} checks passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__" and "--verify" in sys.argv:
    _self_test()

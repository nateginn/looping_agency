# DataForSEO Maps connector - GBP Posts plan Phase 1 ("maps_rank").
#
# Separate, deliberately, from the organic SERP endpoint dataforseo.py already
# wraps. DataForSEO's /v3/serp/google/maps/live/advanced returns maps_search
# result items - a different endpoint, a different item shape, and a different
# question ("where does our Maps listing rank for this keyword/location?")
# than organic rank or local-pack-within-organic-results (dataforseo_local_pack.py).
# Conflating the two was Round 1's first finding against the original GBP plan
# draft; this module and dataforseo_local_pack.py exist so they cannot drift
# back together.
#
# Matching is by a stable Google identifier (place_id or cid) ONLY - never by
# domain or business name. A listing with no website URL, or a competitor with
# a similar name, would otherwise produce a false absence or a false-positive
# match (the exact class of defect D1 already fixed once in dataforseo.py).
# The mapping from a verified place_id/cid to this loop's own listing is a
# one-time, human-verified configuration value (locations[].place_id in
# spec.md) - this module never infers or derives it.
#
# Standing decision 4 (no competitor data stored, ever) applies here exactly
# as it does in dataforseo.py: a non-matching maps_search item is discarded
# outright. No competitor place_id, title, rating, or URL is ever persisted.
import json
import sys

try:
    from . import dataforseo
    from .lib.redact import redact_deep, redact_text
except ImportError:
    import dataforseo
    from lib.redact import redact_deep, redact_text

MAPS_ENDPOINT = "https://api.dataforseo.com/v3/serp/google/maps/live/advanced"

# v1: brand new section, no legacy rows to be backward-compatible with.
MAPS_RANK_SCHEMA_VERSION = 1

ERR_TRANSPORT = dataforseo.ERR_TRANSPORT
ERR_HTTP = dataforseo.ERR_HTTP
ERR_MALFORMED = dataforseo.ERR_MALFORMED
ERR_NO_TASK = dataforseo.ERR_NO_TASK
ERR_KEYWORD_MISMATCH = dataforseo.ERR_KEYWORD_MISMATCH
ERR_TASK_STATUS = dataforseo.ERR_TASK_STATUS
ERR_EMPTY_RESULT = dataforseo.ERR_EMPTY_RESULT
ERR_LOCATION = dataforseo.ERR_LOCATION
ERR_NO_IDENTIFIER = "listing-has-no-identifier"


def _fetch_one_maps_task(headers, keyword, location_code, location_coordinate, language_code, device, depth, http_post):
    """One keyword, one POST - the same one-task-per-request contract as
    dataforseo.py's organic connector (D4). Returns (items, error_reason, status_code)."""
    task = {
        "keyword": keyword,
        "language_code": language_code,
        "device": device,
        "depth": depth,
    }
    # Coordinate-based targeting is materially more precise than a city/state
    # location_code for Maps ranking, which is sensitive to exact position -
    # supported when a location configures one; location_code remains the
    # fallback, matching this repo's existing "document the precision limit
    # rather than guess" convention (see art/loops/seo/spec.md's local-rank note).
    if location_coordinate:
        task["location_coordinate"] = location_coordinate
    else:
        task["location_code"] = location_code
    payload = [task]
    try:
        status, _reason, raw = http_post(MAPS_ENDPOINT, headers, json.dumps(payload).encode("utf-8"))
    except Exception:
        return None, ERR_TRANSPORT, None
    if status < 200 or status >= 300:
        return None, ERR_HTTP, status

    try:
        body = dataforseo._decode_json(raw)
        api_tasks = body.get("tasks") or []
    except Exception:
        return None, ERR_MALFORMED, status
    if not isinstance(api_tasks, list) or not api_tasks:
        return None, ERR_NO_TASK, status

    if len(api_tasks) == 1:
        task_obj = api_tasks[0]
        echoed = ((task_obj or {}).get("data") or {}).get("keyword")
        if echoed is not None and echoed != keyword:
            return None, ERR_KEYWORD_MISMATCH, status
    else:
        matching = [t for t in api_tasks if isinstance(t, dict) and ((t.get("data") or {}).get("keyword") == keyword)]
        if len(matching) != 1:
            return None, ERR_KEYWORD_MISMATCH, status
        task_obj = matching[0]

    if not isinstance(task_obj, dict):
        return None, ERR_MALFORMED, status
    task_status = task_obj.get("status_code")
    if task_status != 20000:
        return None, ERR_TASK_STATUS, task_status

    result = task_obj.get("result")
    if not isinstance(result, list) or not result or not isinstance(result[0], dict):
        return None, ERR_EMPTY_RESULT, task_status
    items = result[0].get("items")
    if not isinstance(items, list) or not items:
        return None, ERR_EMPTY_RESULT, task_status
    return items, None, task_status


def _own_identifier_match(item, own_place_id, own_cid):
    """True only on an exact match against a configured, human-verified
    identifier - never a name/domain heuristic. Checks place_id first (the
    more universally-populated field per DataForSEO's schema), then cid."""
    if not isinstance(item, dict):
        return False
    if own_place_id and item.get("place_id") == own_place_id:
        return True
    if own_cid and item.get("cid") == own_cid:
        return True
    return False


def _maps_row(target, own_place_id, own_cid, items, error_reason, status_code):
    if error_reason is not None:
        return {
            "status": "error",
            "error_reason": error_reason,
            "status_code": status_code,
            "rank_group": None,
            "rank_absolute": None,
            "results_seen": None,
        }
    if not own_place_id and not own_cid:
        # A misconfigured location with no verified identifier at all must
        # never silently read as "absent" - that would be indistinguishable
        # from a real absence and would misinform the Phase 3 selector.
        return {
            "status": "error",
            "error_reason": ERR_NO_IDENTIFIER,
            "status_code": status_code,
            "rank_group": None,
            "rank_absolute": None,
            "results_seen": None,
        }

    results_seen = len(items)
    for item in items:
        if _own_identifier_match(item, own_place_id, own_cid):
            return {
                "status": "ok",
                "error_reason": None,
                "status_code": status_code,
                "rank_group": item.get("rank_group"),
                "rank_absolute": item.get("rank_absolute"),
                "results_seen": results_seen,
            }
    return {
        "status": "absent",
        "error_reason": None,
        "status_code": status_code,
        "rank_group": None,
        "rank_absolute": None,
        "results_seen": results_seen,
    }


def pull_maps_rank(
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
    """One maps_search rank check per (location, keyword) target, one paid
    task each. targets: list of {"keyword": str, "location": str} - "location"
    must match a locations[].name. locations: list of {"name", "address",
    "zip", "place_id" and/or "cid", optional "coordinate": "lat,lng,zoomz"}.

    Every requested target yields exactly one row with an explicit status of
    ok / absent / error, matching dataforseo.py's pull_local_rank convention.
    critical: False at the connector_registry level - a single failing target
    never discards the others, and an all-failed pull raises (degrading, not
    aborting, the run - see connector_registry.py).
    """
    if not credential_alias:
        raise ValueError("dataforseo_maps.py: credential_alias is required")
    if not callable(resolve_credential):
        raise ValueError(
            f'dataforseo_maps.py: no credential resolver configured for alias "{credential_alias}". '
            "This connector refuses to run rather than guess."
        )
    if not targets:
        raise ValueError("dataforseo_maps.py: targets is required (non-empty list of {keyword, location})")
    if not locations:
        raise ValueError("dataforseo_maps.py: locations is required (non-empty list of location objects)")

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
                f'dataforseo_maps.py: target "{target.get("keyword")}" must reference a location name '
                f"present in locations (got {loc_name!r})"
            )

    rows = []
    request_count = 0
    location_code_cache = {}
    for target in targets:
        location = locations_by_name[target["location"]]
        coordinate = location.get("coordinate")
        location_code, location_error = None, None
        if not coordinate:
            if location["name"] not in location_code_cache:
                try:
                    resolved_code, _resolved_name = dataforseo._resolve_location_code(credentials, location, http_get)
                    location_code_cache[location["name"]] = (resolved_code, None)
                except Exception:
                    location_code_cache[location["name"]] = (None, ERR_LOCATION)
            location_code, location_error = location_code_cache[location["name"]]

        own_place_id = location.get("place_id")
        own_cid = location.get("cid")

        if location_error is not None:
            row_result = _maps_row(target, own_place_id, own_cid, None, location_error, None)
        else:
            request_count += 1
            items, error_reason, status_code = _fetch_one_maps_task(
                headers, target["keyword"], location_code, coordinate, language_code, device, depth, http_post
            )
            row_result = _maps_row(target, own_place_id, own_cid, items, error_reason, status_code)

        rows.append({
            "location_name": location.get("name"),
            "keyword": target.get("keyword"),
            "query_params": {
                "location_code": location_code,
                "location_coordinate": coordinate,
                "language_code": language_code,
                "device": device,
                "depth": depth,
            },
            **row_result,
        })

    error_count = sum(1 for r in rows if r["status"] == "error")
    success_count = len(rows) - error_count
    if rows and error_count == len(rows):
        reasons = sorted({r["error_reason"] for r in rows if r.get("error_reason")})
        raise RuntimeError(
            f"dataforseo_maps.py: Maps API returned no usable result for any of "
            f'{len(rows)} targets ({", ".join(reasons)})'
        )

    out = {
        "source": "dataforseo-maps-rank",
        "as_of": dataforseo._now_iso(),
        "schema_version": MAPS_RANK_SCHEMA_VERSION,
        "request_count": request_count,
        "success_count": success_count,
        "error_count": error_count,
        "rows": rows,
        "secretMap": secret_map,
    }
    return redact_deep(out, secret_map)


def _self_test():
    checks = []
    fake_secret = "sk-test-fake-maps-token"
    OWN_PLACE_ID = "ChIJ-own-place-id-invalid"

    LOCATIONS = [
        {"name": "Greeley", "address": "1823 65th Ave Suite 3 Greeley, CO 80634", "zip": "80634", "place_id": OWN_PLACE_ID},
        {"name": "Denver", "address": "2480 W 26th Ave #90B, Denver, CO 80211", "zip": "80211", "coordinate": "39.75,-105.02,14z", "cid": "1234567890"},
    ]
    TARGETS = [
        {"keyword": "physical therapy greeley", "location": "Greeley"},
        {"keyword": "physical therapy denver", "location": "Denver"},
    ]

    def fake_locations(url, headers):
        payload = {"tasks": [{"result": [
            {"location_code": 100, "location_name": "Greeley,Colorado,United States"},
            {"location_code": 200, "location_name": "Denver,Colorado,United States"},
        ]}]}
        return 200, "OK", json.dumps(payload).encode("utf-8")

    def _ok_body(keyword, items):
        return {"tasks": [{"status_code": 20000, "data": {"keyword": keyword}, "result": [{"items": items}]}]}

    sent = []

    def fake_post_ok(url, headers, body_bytes):
        body = json.loads(body_bytes)
        sent.append(body)
        keyword = body[0]["keyword"]
        if keyword == "physical therapy greeley":
            items = [
                {"rank_group": 1, "rank_absolute": 1, "place_id": "ChIJ-competitor-a", "title": "Competitor A"},
                {"rank_group": 2, "rank_absolute": 2, "place_id": OWN_PLACE_ID, "title": "Accelerated Rehab Therapy"},
                {"rank_group": 3, "rank_absolute": 3, "place_id": "ChIJ-competitor-b", "title": "Competitor B"},
            ]
        else:
            items = [
                {"rank_group": 1, "rank_absolute": 1, "cid": "1234567890", "title": "Accelerated Rehab Therapy Denver"},
            ]
        return 200, "OK", json.dumps(_ok_body(keyword, items)).encode("utf-8")

    result = pull_maps_rank(
        credential_alias="acme-dataforseo-read",
        resolve_credential=lambda alias: fake_secret,
        targets=TARGETS,
        locations=LOCATIONS,
        http_post=fake_post_ok,
        http_get=fake_locations,
    )
    greeley_row = next(r for r in result["rows"] if r["location_name"] == "Greeley")
    denver_row = next(r for r in result["rows"] if r["location_name"] == "Denver")
    checks.append(("matches own listing by place_id, not name", greeley_row["status"] == "ok" and greeley_row["rank_group"] == 2))
    checks.append(("matches own listing by cid when place_id absent", denver_row["status"] == "ok" and denver_row["rank_group"] == 1))
    checks.append(("no competitor place_id anywhere in the result (standing decision 4)", "competitor-a" not in json.dumps(result) and "Competitor A" not in json.dumps(result)))
    checks.append(("Denver used coordinate targeting, not location_code", sent[1][0].get("location_coordinate") == "39.75,-105.02,14z" and "location_code" not in sent[1][0]))
    checks.append(("Greeley used location_code (no coordinate configured)", sent[0][0].get("location_code") == 100))
    checks.append(("one POST per target (D4 lesson applied here too)", len(sent) == 2 and all(len(b) == 1 for b in sent)))
    checks.append(("credential never leaks unredacted", fake_secret not in json.dumps(result)))
    checks.append(("schema_version is announced", result["schema_version"] == MAPS_RANK_SCHEMA_VERSION))

    def fake_post_absent(url, headers, body_bytes):
        keyword = json.loads(body_bytes)[0]["keyword"]
        items = [{"rank_group": 1, "rank_absolute": 1, "place_id": "ChIJ-competitor-only", "title": "Only Competitor"}]
        return 200, "OK", json.dumps(_ok_body(keyword, items)).encode("utf-8")

    absent_result = pull_maps_rank(
        credential_alias="acme-dataforseo-read",
        resolve_credential=lambda alias: fake_secret,
        targets=TARGETS,
        locations=LOCATIONS,
        http_post=fake_post_absent,
        http_get=fake_locations,
    )
    checks.append(("a populated maps SERP without our listing is 'absent'", all(r["status"] == "absent" for r in absent_result["rows"])))
    checks.append(("absent rows record no competitor identifier", "competitor-only" not in json.dumps(absent_result) and "Only Competitor" not in json.dumps(absent_result)))

    no_id_locations = [
        {"name": "Greeley", "address": "1823 65th Ave Suite 3 Greeley, CO 80634", "zip": "80634"},
        {"name": "Denver", "address": "2480 W 26th Ave #90B, Denver, CO 80211", "zip": "80211", "cid": "1234567890"},
    ]
    no_id_result = pull_maps_rank(
        credential_alias="acme-dataforseo-read",
        resolve_credential=lambda alias: fake_secret,
        targets=[{"keyword": "physical therapy greeley", "location": "Greeley"}, {"keyword": "physical therapy denver", "location": "Denver"}],
        locations=no_id_locations,
        http_post=fake_post_absent,
        http_get=fake_locations,
    )
    no_id_row = next(r for r in no_id_result["rows"] if r["location_name"] == "Greeley")
    checks.append(("a location with no verified place_id/cid errors, never reads as absent", no_id_row["status"] == "error" and no_id_row["error_reason"] == ERR_NO_IDENTIFIER))

    def fake_post_rejected(url, headers, body_bytes):
        keyword = json.loads(body_bytes)[0]["keyword"]
        if keyword == "physical therapy greeley":
            return 200, "OK", json.dumps({"tasks": [{"status_code": 40000, "data": {"keyword": keyword}, "result": None}]}).encode("utf-8")
        return fake_post_ok(url, headers, body_bytes)

    rejected_result = pull_maps_rank(
        credential_alias="acme-dataforseo-read",
        resolve_credential=lambda alias: fake_secret,
        targets=TARGETS,
        locations=LOCATIONS,
        http_post=fake_post_rejected,
        http_get=fake_locations,
    )
    rejected_row = next(r for r in rejected_result["rows"] if r["location_name"] == "Greeley")
    checks.append(("a rejected task is an error, never absence", rejected_row["status"] == "error" and rejected_row["error_reason"] == ERR_TASK_STATUS))

    threw_missing_location_ref = False
    try:
        pull_maps_rank(
            credential_alias="acme-dataforseo-read",
            resolve_credential=lambda alias: fake_secret,
            targets=[{"keyword": "x", "location": "Nowhere"}],
            locations=LOCATIONS,
            http_post=fake_post_ok,
            http_get=fake_locations,
        )
    except ValueError as e:
        threw_missing_location_ref = "must reference a location name" in str(e)
    checks.append(("a target referencing an unknown location is rejected up front", threw_missing_location_ref))

    threw_all_failed = ""
    try:
        pull_maps_rank(
            credential_alias="acme-dataforseo-read",
            resolve_credential=lambda alias: fake_secret,
            targets=TARGETS,
            locations=LOCATIONS,
            http_post=lambda url, headers, body: (500, "Server Error", b"down"),
            http_get=fake_locations,
        )
    except RuntimeError as e:
        threw_all_failed = str(e)
    checks.append(("an all-targets-failed pull raises rather than emitting phantom absences", "no usable result for any" in threw_all_failed))

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    print(f"{len(checks) - failed}/{len(checks)} checks passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__" and "--verify" in sys.argv:
    _self_test()

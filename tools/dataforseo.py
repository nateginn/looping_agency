# DataForSEO connector - read-only scope only.
#
# This module keeps the existing organic SERP connector and adds:
# - location-aware organic rank checks (city/state fallback via location_code)
# - backlink summary/history pulls
#
# Step 0's Business Data API / Google My Business capability spike was not
# verified live in this workspace, so local rank here intentionally uses the
# documented fallback only: organic SERP rank with city/state location codes.
import base64
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    from .lib.redact import redact_deep, redact_text
    from .lib.serp_match import scan_organic
except ImportError:
    from lib.redact import redact_deep, redact_text
    from lib.serp_match import scan_organic

REQUIRED_SCOPE = "read-only (SERP + backlinks endpoints)"
SERP_ENDPOINT = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"
SERP_LOCATIONS_ENDPOINT = "https://api.dataforseo.com/v3/serp/google/locations/us"
BACKLINKS_SUMMARY_ENDPOINT = "https://api.dataforseo.com/v3/backlinks/summary/live"
BACKLINKS_HISTORY_ENDPOINT = "https://api.dataforseo.com/v3/backlinks/history/live"
# Matches the single word immediately before ", ST ZIP" at the end of an address,
# regardless of what punctuation precedes it - real addresses here have no comma
# between street and city ("...Ave Suite 3 Greeley, CO 80634"), so a pattern
# requiring a leading comma before the city (the original version) failed to
# match at all, and a permissive "everything between two commas" pattern
# mismatched multi-comma addresses ("...Ave, Cassidy Hall Greeley, CO 80639" ->
# wrongly captured "Cassidy Hall Greeley" as the city). Single-word-only: does
# not yet support multi-word city names (e.g. "Fort Collins", "Colorado Springs").
_CITY_STATE_RE = re.compile(r"([A-Za-z]+)\s*,\s*([A-Z]{2})\s+\d{5}(?:-\d{4})?\s*$")

# `local_rank` section schema. Version 2 is the Issue #1 rewrite: one task per request
# (D4), host+path matching against the client's own domain (D1), the true organic index
# rather than rank_absolute (D5), and non-matching results discarded entirely (standing
# decision 4). Version 1 rows are not comparable to version 2 rows in any direction, so
# `run_loop._section_history` refuses to hand a v1 section to the attention-threshold
# comparison. Bump this only when that statement becomes true again.
LOCAL_RANK_SCHEMA_VERSION = 2

# Enumerated failure reasons. Deliberately a closed vocabulary rather than the API's own
# `status_message`: the snapshot is a durable redacted artifact, and echoing arbitrary
# upstream text into it is how a keyword, a URL, or a credential fragment ends up there.
ERR_TRANSPORT = "transport-failure"
ERR_HTTP = "http-error"
ERR_MALFORMED = "malformed-response"
ERR_NO_TASK = "no-task-in-response"
ERR_KEYWORD_MISMATCH = "task-keyword-mismatch"
ERR_TASK_STATUS = "task-status-not-ok"
ERR_EMPTY_RESULT = "success-without-serp"
ERR_LOCATION = "location-resolution-failed"


def _default_http_post(url, headers, body_bytes):
    """Returns (status, reason, response_bytes). Raises on network failure."""
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.reason, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.reason, e.read()


def _default_http_get(url, headers):
    """Returns (status, reason, response_bytes). Raises on network failure."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.reason, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.reason, e.read()


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _auth_headers(credentials):
    basic_auth = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {basic_auth}", "Content-Type": "application/json"}


def _decode_json(raw):
    if isinstance(raw, (bytes, bytearray)):
        return json.loads(raw)
    return json.loads(raw.encode("utf-8"))


def _raise_api_error(prefix, status, reason, raw, secret_map):
    body_text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    raise RuntimeError(redact_text(f"{prefix} returned {status} {reason}: {body_text}", secret_map))


def _parse_city_state(address):
    if not isinstance(address, str) or not address.strip():
        raise ValueError("dataforseo.py: location.address is required")
    match = _CITY_STATE_RE.search(address.strip())
    if not match:
        raise ValueError(f'dataforseo.py: could not derive city/state from address "{address}"')
    return match.group(1).strip(), match.group(2).strip().upper()


def _resolve_location_code(credentials, location, http_get):
    city, state = _parse_city_state(location.get("address"))
    secret_map = {"dataforseo": credentials}
    try:
        status, reason, raw = http_get(SERP_LOCATIONS_ENDPOINT, _auth_headers(credentials))
    except Exception as e:
        raise RuntimeError(redact_text(f"dataforseo.py: request to SERP locations API failed: {e}", secret_map)) from None
    if status < 200 or status >= 300:
        _raise_api_error("dataforseo.py: SERP locations API", status, reason, raw, secret_map)

    body = _decode_json(raw)
    tasks = body.get("tasks") or []
    items = (tasks[0].get("result") if tasks else None) or []
    state_names = {state, _STATE_NAMES.get(state, state)}
    for item in items:
        item_city = (item.get("location_name") or "").split(",")[0].strip()
        item_name = item.get("location_name") or ""
        if item_city.lower() != city.lower():
            continue
        if not any(name.lower() in item_name.lower() for name in state_names):
            continue
        return item.get("location_code"), item_name
    raise RuntimeError(f'dataforseo.py: no SERP location_code found for "{city}, {state}"')


def _fetch_one_serp(headers, keyword, location_code, language_code, device, depth, http_post):
    """One keyword, one POST. Returns (items, error_reason, status_code).

    **One task per request is not an optimization, it is the contract.** DataForSEO's
    `live/advanced` endpoint executes exactly one task per POST; every additional task in
    the array comes back `status_code: 40000` ("You can set only one task at a time"),
    `result: null`, `cost: 0`. The previous implementation batched all 12 targets into one
    POST and then did `result = task.get("result") or []`, which turned that rejection
    into an empty item list, which turned into `organic_rank_position: null` - byte-identical
    to a genuine "not ranking". Ten of twelve targets were never queried for months and the
    system reported them as absent (defect D4).

    So every path that is not "a 20000 task carrying a populated SERP" returns an error
    reason, never an empty item list. In particular a 20000 with a missing or empty
    `result`/`items` is an error, not an absence: **claiming a page is absent requires
    positive evidence of a populated SERP to be absent from.**
    """
    payload = [{
        "keyword": keyword,
        "location_code": location_code,
        "language_code": language_code,
        "device": device,
        "depth": depth,
    }]
    try:
        status, _reason, raw = http_post(SERP_ENDPOINT, headers, json.dumps(payload).encode("utf-8"))
    except Exception:
        return None, ERR_TRANSPORT, None
    if status < 200 or status >= 300:
        return None, ERR_HTTP, status

    try:
        body = _decode_json(raw)
        api_tasks = body.get("tasks") or []
    except Exception:
        return None, ERR_MALFORMED, status
    if not isinstance(api_tasks, list) or not api_tasks:
        return None, ERR_NO_TASK, status

    # Never select a task by array index. A reordered, duplicated or partially-failed
    # response would otherwise attribute one keyword's SERP to another - silently, and
    # in the direction of a plausible-looking number.
    if len(api_tasks) == 1:
        task = api_tasks[0]
        echoed = ((task or {}).get("data") or {}).get("keyword")
        if echoed is not None and echoed != keyword:
            return None, ERR_KEYWORD_MISMATCH, status
    else:
        matching = [t for t in api_tasks if isinstance(t, dict) and ((t.get("data") or {}).get("keyword") == keyword)]
        if len(matching) != 1:
            return None, ERR_KEYWORD_MISMATCH, status
        task = matching[0]

    if not isinstance(task, dict):
        return None, ERR_MALFORMED, status
    task_status = task.get("status_code")
    if task_status != 20000:
        return None, ERR_TASK_STATUS, task_status

    result = task.get("result")
    if not isinstance(result, list) or not result or not isinstance(result[0], dict):
        return None, ERR_EMPTY_RESULT, task_status
    items = result[0].get("items")
    if not isinstance(items, list) or not items:
        return None, ERR_EMPTY_RESULT, task_status
    return items, None, task_status


def _rank_row(target, domain, items, error_reason, status_code):
    """The per-target result, in the one shape every caller agrees on.

    Exactly three statuses, and the distinction between the last two is the whole point
    of this module's rewrite:
      ok     - we found the configured page, at a validated organic index on our domain
      absent - we saw a populated SERP and our page was genuinely not in it
      error  - we did not get a usable answer; this is missing data, NOT an absence
    """
    if error_reason is not None:
        return {
            "status": "error",
            "error_reason": error_reason,
            "status_code": status_code,
            "organic_rank_position": None,
            "organic_rank_group": None,
            "matched_domain": None,
            "result_url": None,
            "domain_presence": [],
            "organic_results_seen": None,
        }

    scan = scan_organic(items, domain, target.get("page"))
    match = scan["target_match"]
    return {
        # `organic_rank_position` keeps its name through the semantic change on purpose:
        # MMC's collector (`collector/sources/looping.py:_summarize_local_rank`) parses
        # this key. Its *meaning* changed from rank_absolute to the organic index, which
        # is what `schema_version` exists to announce.
        "status": "ok" if match else "absent",
        "error_reason": None,
        "status_code": status_code,
        "organic_rank_position": match["organic_position"] if match else None,
        "organic_rank_group": match["organic_rank_group"] if match else None,
        "matched_domain": match["matched_domain"] if match else None,
        # Only ever our own URL. A non-matching result is discarded outright - no
        # competitor host, URL or title reaches the snapshot (standing decision 4).
        "result_url": match["url"] if match else None,
        "domain_presence": scan["domain_presence"],
        # Makes an "absent" verdict auditable: absent out of how many organic results.
        "organic_results_seen": scan["organic_seen"],
        "rank_group_agrees": scan["rank_group_agrees"],
    }


def pull_metrics(
    credential_alias=None,
    resolve_credential=None,
    targets=None,
    domain=None,
    location_code=2840,
    language_code="en",
    device="desktop",
    depth=100,
    http_post=_default_http_post,
):
    """
    resolve_credential: callable(alias) -> "login:password" credential string in a live run.
    targets: list of {"keyword": str, "page": str} pairs to rank-check.
    domain: the client's own domain. Required - a result is only ours if its host is this
    domain, its www. variant, or a subdomain of it. There is deliberately no "no domain"
    fallback to the old substring match, which is what recorded competitors' rankings as
    the client's (defect D1).
    location_code: DataForSEO location code, defaults to 2840 (United States).
    depth: SERP results to check per keyword. DataForSEO defaults to 10 (page 1 only)
    if omitted, which silently under-reports rank for anything past page 1 as "not
    found" rather than as an error - 100 matches standard top-100 rank tracking.

    **Fails closed, unlike `pull_local_rank`.** This connector is registered
    `critical: True` (`connector_registry.py`) because it merges into `search_analytics`,
    the section evaluations and proposals are computed from. A partially-enriched
    `search_analytics` is worse than none, so any target that does not produce a usable
    SERP aborts the whole pull. `pull_local_rank` is enrichment-only and records
    per-target failures instead; that asymmetry is deliberate.
    """
    if not credential_alias:
        raise ValueError("dataforseo.py: credential_alias is required")
    if not callable(resolve_credential):
        raise ValueError(
            f'dataforseo.py: no credential resolver configured for alias "{credential_alias}". '
            "Live DataForSEO access is out of scope until a real credential resolver is wired in "
            "(see AgentColabPlan.md Sequencing) - this connector refuses to run rather than guess."
        )
    if not targets:
        raise ValueError("dataforseo.py: targets is required (non-empty list of {keyword, page})")
    if not isinstance(domain, str) or not domain.strip():
        raise ValueError(
            "dataforseo.py: domain is required (the client's own domain). Without it a SERP "
            "result cannot be attributed, and matching by URL substring alone is defect D1."
        )

    credentials = resolve_credential(credential_alias)
    secret_map = {credential_alias: credentials}
    headers = _auth_headers(credentials)

    keywords = []
    request_count = 0
    for target in targets:
        request_count += 1
        items, error_reason, status_code = _fetch_one_serp(
            headers, target["keyword"], location_code, language_code, device, depth, http_post
        )
        if error_reason is not None:
            raise RuntimeError(
                redact_text(
                    f'dataforseo.py: SERP API gave no usable result for "{target.get("keyword")}" '
                    f"({error_reason}, status {status_code}) - refusing to report partial "
                    "search_analytics enrichment from a critical connector",
                    secret_map,
                )
            )
        row = _rank_row(target, domain, items, None, status_code)
        keywords.append(
            {
                "keyword": target.get("keyword"),
                "page": target.get("page"),
                # The organic index, not rank_absolute (defect D5). `_merge_metrics` in
                # run_loop.py reads this key to set `serp_position`.
                "position": row["organic_rank_position"],
                "organic_rank_group": row["organic_rank_group"],
                "matched_domain": row["matched_domain"],
                "clicks": None,
                "impressions": None,
            }
        )

    out = {
        "source": "dataforseo",
        "pulled_at": _now_iso(),
        "credential_alias": credential_alias,
        "domain": domain,
        "sample_size": len(keywords),
        "request_count": request_count,
        "keywords": keywords,
        "secretMap": secret_map,
    }
    return redact_deep(out, secret_map)


def pull_local_rank(
    credential_alias=None,
    resolve_credential=None,
    targets=None,
    locations=None,
    domain=None,
    language_code="en",
    device="desktop",
    depth=100,
    http_post=_default_http_post,
    http_get=_default_http_get,
):
    """Organic rank for each (location, keyword, page) target, one paid task each.

    Every requested target yields exactly one row, always, with an explicit `status` of
    ok / absent / error - see `_rank_row`. A single failing target does not discard the
    other eleven measurements; only an all-targets-failed pull raises, which degrades the
    run (this connector is registered `critical: False`).
    """
    if not credential_alias:
        raise ValueError("dataforseo.py: credential_alias is required")
    if not callable(resolve_credential):
        raise ValueError(
            f'dataforseo.py: no credential resolver configured for alias "{credential_alias}". '
            "Live DataForSEO access is out of scope until a real credential resolver is wired in "
            "(see AgentColabPlan.md Sequencing) - this connector refuses to run rather than guess."
        )
    if not targets:
        raise ValueError("dataforseo.py: targets is required (non-empty list of {keyword, page})")
    if not locations:
        raise ValueError("dataforseo.py: locations is required (non-empty list of {name, address, zip})")
    if not isinstance(domain, str) or not domain.strip():
        raise ValueError(
            "dataforseo.py: domain is required (the client's own domain). Without it a SERP "
            "result cannot be attributed, and matching by URL substring alone is defect D1."
        )

    credentials = resolve_credential(credential_alias)
    secret_map = {credential_alias: credentials}
    headers = _auth_headers(credentials)
    rows = []

    # Each target may opt into a single matched location via target["location"]
    # (a name matching locations[].name), avoiding a full location x keyword
    # cross-product where most pairings are irrelevant (e.g. checking a
    # Denver-intent keyword from the Greeley location). Targets without a
    # "location" key fall back to being checked from every configured location,
    # preserving the original cross-product behavior.
    locations_by_name = {location.get("name"): location for location in locations}
    targets_by_location_name = {}
    for target in targets:
        target_location_name = target.get("location")
        if target_location_name:
            if target_location_name not in locations_by_name:
                raise ValueError(
                    f'dataforseo.py: target "{target.get("keyword")}" references location '
                    f'"{target_location_name}" which is not in locations'
                )
            targets_by_location_name.setdefault(target_location_name, []).append(target)
        else:
            for location_name in locations_by_name:
                targets_by_location_name.setdefault(location_name, []).append(target)

    request_count = 0
    expected_keys = []
    for location_name, location_targets in targets_by_location_name.items():
        location = locations_by_name[location_name]
        # The locations lookup is a separate, unbilled GET. If it fails, that location's
        # targets are unanswerable - but the other location's are not, so this records
        # the failure per row rather than aborting the whole pull.
        location_code, resolved_location_name, location_error = None, None, None
        try:
            location_code, resolved_location_name = _resolve_location_code(credentials, location, http_get)
        except Exception:
            location_error = ERR_LOCATION

        for target in location_targets:
            expected_keys.append((location.get("name"), target.get("keyword"), target.get("page")))
            if location_error is not None:
                result_row = _rank_row(target, domain, None, location_error, None)
            else:
                request_count += 1
                items, error_reason, status_code = _fetch_one_serp(
                    headers, target["keyword"], location_code, language_code, device, depth, http_post
                )
                result_row = _rank_row(target, domain, items, error_reason, status_code)
            rows.append(
                {
                    "location_name": location.get("name"),
                    "location_address": location.get("address"),
                    "zip": location.get("zip"),
                    "location_code": location_code,
                    "location_target": resolved_location_name,
                    "keyword": target.get("keyword"),
                    "page": target.get("page"),
                    **result_row,
                }
            )

    # One row per requested target, no more and no fewer. The old code derived rows from
    # whatever the API happened to return, so a short response silently produced fewer
    # rows than targets and nothing noticed.
    actual_keys = [(r["location_name"], r["keyword"], r["page"]) for r in rows]
    if actual_keys != expected_keys:
        raise RuntimeError(
            f"dataforseo.py: local-rank produced {len(actual_keys)} rows for "
            f"{len(expected_keys)} requested targets - refusing to emit a mismatched section"
        )

    error_count = sum(1 for r in rows if r["status"] == "error")
    success_count = len(rows) - error_count
    if rows and error_count == len(rows):
        reasons = sorted({r["error_reason"] for r in rows if r.get("error_reason")})
        raise RuntimeError(
            f"dataforseo.py: local-rank SERP API returned no usable result for any of "
            f'{len(rows)} targets ({", ".join(reasons)})'
        )

    out = {
        "source": "dataforseo-local-rank",
        "as_of": _now_iso(),
        "schema_version": LOCAL_RANK_SCHEMA_VERSION,
        # v1 reported rank_absolute, which counts ads, local-pack entries and PAA blocks,
        # under this same "organic" label (defect D5). v2 reports the true organic index.
        "rank_metric": "organic_index",
        # `run.json` records one tool_call per connector, not one per HTTP request, so
        # these counters are what makes the D4 fix checkable from the artifact itself.
        "request_count": request_count,
        "success_count": success_count,
        "error_count": error_count,
        "rows": rows,
        "secretMap": secret_map,
    }
    return redact_deep(out, secret_map)


def pull_backlinks(
    credential_alias=None,
    resolve_credential=None,
    target=None,
    include_subdomains=True,
    date_from=None,
    date_to=None,
    http_post=_default_http_post,
):
    if not credential_alias:
        raise ValueError("dataforseo.py: credential_alias is required")
    if not callable(resolve_credential):
        raise ValueError(
            f'dataforseo.py: no credential resolver configured for alias "{credential_alias}". '
            "Live DataForSEO access is out of scope until a real credential resolver is wired in "
            "(see AgentColabPlan.md Sequencing) - this connector refuses to run rather than guess."
        )
    if not isinstance(target, str) or not target.strip():
        raise ValueError("dataforseo.py: target is required (domain, subdomain, or absolute URL)")

    credentials = resolve_credential(credential_alias)
    secret_map = {credential_alias: credentials}
    headers = _auth_headers(credentials)
    if not date_to:
        date_to = datetime.now(timezone.utc).date().isoformat()
    if not date_from:
        date_from = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()

    try:
        summary_status, summary_reason, summary_raw = http_post(
            BACKLINKS_SUMMARY_ENDPOINT,
            headers,
            json.dumps([{"target": target, "include_subdomains": include_subdomains}]).encode("utf-8"),
        )
    except Exception as e:
        raise RuntimeError(redact_text(f"dataforseo.py: request to Backlinks Summary API failed: {e}", secret_map)) from None
    if summary_status < 200 or summary_status >= 300:
        _raise_api_error("dataforseo.py: Backlinks Summary API", summary_status, summary_reason, summary_raw, secret_map)

    try:
        history_status, history_reason, history_raw = http_post(
            BACKLINKS_HISTORY_ENDPOINT,
            headers,
            json.dumps([{"target": target, "date_from": date_from, "date_to": date_to, "include_subdomains": include_subdomains}]).encode("utf-8"),
        )
    except Exception as e:
        raise RuntimeError(redact_text(f"dataforseo.py: request to Backlinks History API failed: {e}", secret_map)) from None
    if history_status < 200 or history_status >= 300:
        _raise_api_error("dataforseo.py: Backlinks History API", history_status, history_reason, history_raw, secret_map)

    summary_body = _decode_json(summary_raw)
    history_body = _decode_json(history_raw)
    summary_result = (((summary_body.get("tasks") or [{}])[0].get("result") or [{}])[0])
    history_result = (((history_body.get("tasks") or [{}])[0].get("result") or [{}])[0])

    out = {
        "source": "dataforseo-backlinks",
        "as_of": _now_iso(),
        "target": target,
        "date_from": date_from,
        "date_to": date_to,
        "summary": {
            "referring_domains": summary_result.get("referring_domains"),
            "backlinks": summary_result.get("backlinks"),
        },
        "history": {
            "new_backlinks": history_result.get("new_backlinks"),
            "lost_backlinks": history_result.get("lost_backlinks"),
            "new_referring_domains": history_result.get("new_referring_domains"),
            "lost_referring_domains": history_result.get("lost_referring_domains"),
        },
        "secretMap": secret_map,
    }
    return redact_deep(out, secret_map)


def _self_test():
    checks = []

    threw_no_resolver = False
    try:
        pull_metrics(credential_alias="acme-dataforseo-read", targets=[{"keyword": "x", "page": "/x"}])
    except ValueError as e:
        threw_no_resolver = "no credential resolver configured" in str(e)
    checks.append(("refuses to run without an injected resolver", threw_no_resolver))

    threw_no_alias = False
    try:
        pull_metrics()
    except ValueError:
        threw_no_alias = True
    checks.append(("refuses to run without a credential_alias", threw_no_alias))

    threw_no_targets = False
    try:
        pull_metrics(credential_alias="acme-dataforseo-read", resolve_credential=lambda alias: "login:pass")
    except ValueError as e:
        threw_no_targets = "targets is required" in str(e)
    checks.append(("refuses to run without targets", threw_no_targets))

    threw_no_domain = False
    try:
        pull_metrics(
            credential_alias="acme-dataforseo-read",
            resolve_credential=lambda alias: "login:pass",
            targets=[{"keyword": "x", "page": "/x"}],
        )
    except ValueError as e:
        threw_no_domain = "domain is required" in str(e)
    checks.append(("refuses to run without a domain (no substring fallback - defect D1)", threw_no_domain))

    fake_secret = "sk-test-fake-dataforseo-token"
    # Synthetic hosts throughout. Standing decision 4 ("no data about other businesses is
    # stored, at all") governs fixtures too, so the real competitor domains from P0.1's
    # regression list are represented by .invalid stand-ins.
    OWN = "ourclinic.invalid"
    targets = [{"keyword": "ai marketing loops", "page": "/blog/ai-marketing"}]

    # Real SERP composition, from the 2026-08-11 diagnostic: a three-entry local pack and
    # a People Also Ask block sit above the organic results, so rank_absolute runs 4 ahead
    # of the true organic index. A competitor carrying our exact path sits above us.
    def _serp_items():
        return [
            {"type": "local_pack", "rank_absolute": 1, "rank_group": 1},
            {"type": "local_pack", "rank_absolute": 2, "rank_group": 2},
            {"type": "local_pack", "rank_absolute": 3, "rank_group": 3},
            {"type": "organic", "rank_absolute": 4, "rank_group": 1, "url": "https://competitor-one.invalid/blog/ai-marketing"},
            {"type": "people_also_ask", "rank_absolute": 5, "rank_group": 1},
            {"type": "organic", "rank_absolute": 6, "rank_group": 2, "url": f"https://www.{OWN}/blog/ai-marketing"},
            {"type": "organic", "rank_absolute": 7, "rank_group": 3, "url": f"https://{OWN}/other-page"},
        ]

    def _ok_body(keyword, items=None):
        return {"tasks": [{"id": "t", "status_code": 20000, "status_message": "Ok.",
                           "data": {"keyword": keyword},
                           "result": [{"items": _serp_items() if items is None else items}]}]}

    sent_bodies = []

    def fake_serp_ok(url, headers, body_bytes):
        body = json.loads(body_bytes)
        sent_bodies.append(body)
        return 200, "OK", json.dumps(_ok_body(body[0]["keyword"])).encode("utf-8")

    result = pull_metrics(
        credential_alias="acme-dataforseo-read",
        resolve_credential=lambda alias: fake_secret,
        targets=targets,
        domain=OWN,
        http_post=fake_serp_ok,
    )
    checks.append(("D5: pull_metrics reports the organic index (2), not rank_absolute (6)", result["keywords"][0]["position"] == 2))
    checks.append(("D1: the competitor sharing our path is not reported as us", result["keywords"][0]["matched_domain"] == OWN))
    checks.append(("clicks/impressions are None (SERP has no click data)", result["keywords"][0]["clicks"] is None))
    checks.append(("resolved credential never appears unredacted in the returned object", fake_secret not in json.dumps(result)))
    checks.append(("pull_metrics requests depth=100 by default (API defaults to 10/page-1 if omitted)", sent_bodies[-1][0]["depth"] == 100))
    checks.append(("D4: pull_metrics sends exactly one task per request", all(len(b) == 1 for b in sent_bodies)))

    sent_bodies.clear()
    multi = pull_metrics(
        credential_alias="acme-dataforseo-read",
        resolve_credential=lambda alias: fake_secret,
        targets=[{"keyword": f"kw {i}", "page": "/blog/ai-marketing"} for i in range(5)],
        domain=OWN,
        http_post=fake_serp_ok,
    )
    checks.append(("D4: 5 targets issue 5 separate POSTs, not one batched array", len(sent_bodies) == 5 and multi["request_count"] == 5))

    def fake_serp_one_rejected(url, headers, body_bytes):
        keyword = json.loads(body_bytes)[0]["keyword"]
        if keyword == "kw 3":
            payload = {"tasks": [{"status_code": 40000, "status_message": "You can set only one task at a time.",
                                  "data": {"keyword": keyword}, "result": None}]}
            return 200, "OK", json.dumps(payload).encode("utf-8")
        return 200, "OK", json.dumps(_ok_body(keyword)).encode("utf-8")

    failed_closed = ""
    try:
        pull_metrics(
            credential_alias="acme-dataforseo-read",
            resolve_credential=lambda alias: fake_secret,
            targets=[{"keyword": f"kw {i}", "page": "/blog/ai-marketing"} for i in range(5)],
            domain=OWN,
            http_post=fake_serp_one_rejected,
        )
    except RuntimeError as e:
        failed_closed = str(e)
    checks.append(("pull_metrics fails closed on a single rejected task (it is a critical connector)", ERR_TASK_STATUS in failed_closed))
    checks.append(("...and the fail-closed message carries no raw API status_message text", "only one task at a time" not in failed_closed))

    def fake_locations(url, headers):
        payload = {"tasks": [{"result": [{"location_code": 123, "location_name": "Denver,Colorado,United States"}]}]}
        return 200, "OK", json.dumps(payload).encode("utf-8")

    sent_bodies.clear()
    local_rank = pull_local_rank(
        credential_alias="acme-dataforseo-read",
        resolve_credential=lambda alias: fake_secret,
        targets=[{"keyword": "ai marketing loops", "page": "/blog/ai-marketing"}],
        locations=[{"name": "Denver", "address": "2480 W 26th Ave #90B, Denver, CO 80211", "zip": "80211"}],
        domain=OWN,
        http_post=fake_serp_ok,
        http_get=fake_locations,
    )
    row = local_rank["rows"][0]
    checks.append(("D5: local rank records the organic index, not rank_absolute", row["organic_rank_position"] == 2))
    checks.append(("D5: organic_rank_group agrees with the counted organic index", row["organic_rank_group"] == 2 and row["rank_group_agrees"]))
    checks.append(("the row is marked ok and names the domain it matched", row["status"] == "ok" and row["matched_domain"] == OWN))
    checks.append(("decision 4: no competitor host appears anywhere in the section", "competitor-one" not in json.dumps(local_rank)))
    checks.append(("result_url is our own url, never the competitor sharing the path", row["result_url"] == f"https://www.{OWN}/blog/ai-marketing"))
    checks.append(("domain presence records our other ranking page too", [d["organic_position"] for d in row["domain_presence"]] == [2, 3]))
    checks.append(("an absent verdict is auditable against how many organic results were seen", row["organic_results_seen"] == 3))
    checks.append(("local rank stores resolved location metadata", row["location_code"] == 123))
    checks.append(("pull_local_rank requests depth=100 by default", sent_bodies[-1][0]["depth"] == 100))
    checks.append(("the section announces its schema version so history cannot be compared across the fix", local_rank["schema_version"] == LOCAL_RANK_SCHEMA_VERSION))
    checks.append(("the section renames its metric away from the old mixed-feature label", local_rank["rank_metric"] == "organic_index"))
    checks.append(("counters record what was actually sent", local_rank["request_count"] == 1 and local_rank["success_count"] == 1 and local_rank["error_count"] == 0))

    # --- D4, the headline case: 12 targets must issue 12 requests ------------------
    def fake_locations_multi(url, headers):
        payload = {
            "tasks": [
                {
                    "result": [
                        {"location_code": 100, "location_name": "Denver,Colorado,United States"},
                        {"location_code": 200, "location_name": "Greeley,Colorado,United States"},
                    ]
                }
            ]
        }
        return 200, "OK", json.dumps(payload).encode("utf-8")

    LOCATIONS = [
        {"name": "Denver", "address": "2480 W 26th Ave #90B, Denver, CO 80211", "zip": "80211"},
        {"name": "Greeley", "address": "1823 65th Ave Suite 3 Greeley, CO 80634", "zip": "80634"},
    ]
    twelve = [
        {"keyword": f"service {i} {city.lower()}", "page": f"/service-{i}/", "location": city}
        for city in ("Greeley", "Denver")
        for i in range(6)
    ]
    posts = []

    def fake_serp_counting(url, headers, body_bytes):
        body = json.loads(body_bytes)
        posts.append(body)
        return 200, "OK", json.dumps(_ok_body(body[0]["keyword"], items=_serp_items())).encode("utf-8")

    twelve_result = pull_local_rank(
        credential_alias="acme-dataforseo-read",
        resolve_credential=lambda alias: fake_secret,
        targets=twelve,
        locations=LOCATIONS,
        domain=OWN,
        http_post=fake_serp_counting,
        http_get=fake_locations_multi,
    )
    checks.append(("D4: 12 targets issue 12 separate POSTs (was 2 batched arrays, 10 targets never queried)", len(posts) == 12))
    checks.append(("D4: every POST carries exactly one task", all(len(b) == 1 for b in posts)))
    checks.append(("D4: request_count in the section equals the POSTs actually made", twelve_result["request_count"] == len(posts)))
    checks.append(("every requested target produces exactly one row", len(twelve_result["rows"]) == 12))
    checks.append(("every POST carries a distinct keyword (no target silently skipped)", len({b[0]["keyword"] for b in posts}) == 12))
    checks.append(("success_count + error_count accounts for every row", twelve_result["success_count"] + twelve_result["error_count"] == 12))

    # --- failure classification: an error is never an absence ----------------------
    def _failing_serp(mode):
        def fake(url, headers, body_bytes):
            keyword = json.loads(body_bytes)[0]["keyword"]
            if keyword != "service 0 greeley":
                return 200, "OK", json.dumps(_ok_body(keyword)).encode("utf-8")
            if mode == "rejected":
                return 200, "OK", json.dumps({"tasks": [{"status_code": 40000, "status_message": "You can set only one task at a time.", "data": {"keyword": keyword}, "result": None}]}).encode("utf-8")
            if mode == "empty-result":
                return 200, "OK", json.dumps({"tasks": [{"status_code": 20000, "status_message": "Ok.", "data": {"keyword": keyword}, "result": None}]}).encode("utf-8")
            if mode == "no-items":
                return 200, "OK", json.dumps({"tasks": [{"status_code": 20000, "status_message": "Ok.", "data": {"keyword": keyword}, "result": [{"items": None}]}]}).encode("utf-8")
            if mode == "wrong-keyword":
                return 200, "OK", json.dumps({"tasks": [{"status_code": 20000, "data": {"keyword": "some other keyword"}, "result": [{"items": _serp_items()}]}]}).encode("utf-8")
            if mode == "duplicate-tasks":
                one = {"status_code": 20000, "data": {"keyword": keyword}, "result": [{"items": _serp_items()}]}
                return 200, "OK", json.dumps({"tasks": [one, dict(one)]}).encode("utf-8")
            if mode == "no-tasks":
                return 200, "OK", json.dumps({"tasks": []}).encode("utf-8")
            if mode == "http-500":
                return 500, "Server Error", b"upstream exploded"
            if mode == "transport":
                raise OSError("connection reset")
            raise AssertionError(mode)
        return fake

    def _first_row(mode):
        out = pull_local_rank(
            credential_alias="acme-dataforseo-read",
            resolve_credential=lambda alias: fake_secret,
            targets=twelve,
            locations=LOCATIONS,
            domain=OWN,
            http_post=_failing_serp(mode),
            http_get=fake_locations_multi,
        )
        return out, next(r for r in out["rows"] if r["keyword"] == "service 0 greeley")

    rejected_out, rejected_row = _first_row("rejected")
    checks.append(("D4: a rejected task is an error, never a null position reading as 'not ranking'", rejected_row["status"] == "error" and rejected_row["organic_rank_position"] is None))
    checks.append(("the error reason is an enumerated code, not the API's own message text", rejected_row["error_reason"] == ERR_TASK_STATUS and "only one task" not in json.dumps(rejected_out)))
    checks.append(("one failed target does not discard the other eleven measurements", rejected_out["success_count"] == 11 and rejected_out["error_count"] == 1))

    _, empty_row = _first_row("empty-result")
    checks.append(("a 20000 with no result is an error, not an absence", empty_row["status"] == "error" and empty_row["error_reason"] == ERR_EMPTY_RESULT))
    _, no_items_row = _first_row("no-items")
    checks.append(("a 20000 with no items is an error - absence needs a populated SERP to be absent from", no_items_row["status"] == "error" and no_items_row["error_reason"] == ERR_EMPTY_RESULT))
    _, wrong_kw_row = _first_row("wrong-keyword")
    checks.append(("a task echoing a different keyword is an error, never attributed to this target", wrong_kw_row["status"] == "error" and wrong_kw_row["error_reason"] == ERR_KEYWORD_MISMATCH))
    _, dup_row = _first_row("duplicate-tasks")
    checks.append(("an ambiguous duplicate-task response is an error", dup_row["status"] == "error" and dup_row["error_reason"] == ERR_KEYWORD_MISMATCH))
    _, no_task_row = _first_row("no-tasks")
    checks.append(("an empty tasks array is an error", no_task_row["status"] == "error" and no_task_row["error_reason"] == ERR_NO_TASK))
    http_out, http_row = _first_row("http-500")
    checks.append(("a non-2xx response is an error and its body never reaches the snapshot", http_row["error_reason"] == ERR_HTTP and "exploded" not in json.dumps(http_out)))
    _, transport_row = _first_row("transport")
    checks.append(("a transport failure on one target is an error, not an absence", transport_row["status"] == "error" and transport_row["error_reason"] == ERR_TRANSPORT))

    # A genuine absence: a populated SERP that simply does not contain our page.
    def fake_serp_absent(url, headers, body_bytes):
        keyword = json.loads(body_bytes)[0]["keyword"]
        items = [{"type": "organic", "rank_absolute": i, "rank_group": i, "url": f"https://competitor-{i}.invalid/service-0/"} for i in range(1, 6)]
        return 200, "OK", json.dumps(_ok_body(keyword, items=items)).encode("utf-8")

    absent_out = pull_local_rank(
        credential_alias="acme-dataforseo-read",
        resolve_credential=lambda alias: fake_secret,
        targets=twelve,
        locations=LOCATIONS,
        domain=OWN,
        http_post=fake_serp_absent,
        http_get=fake_locations_multi,
    )
    absent_row = absent_out["rows"][0]
    checks.append(("a populated SERP without us is 'absent', distinct from 'error'", absent_row["status"] == "absent" and absent_row["organic_rank_position"] is None))
    checks.append(("decision 4: an absent row records no competitor url at all", absent_row["result_url"] is None and "competitor-1" not in json.dumps(absent_out)))
    checks.append(("an all-absent pull is a success, not a failure", absent_out["error_count"] == 0))

    threw_all_failed = ""
    try:
        pull_local_rank(
            credential_alias="acme-dataforseo-read",
            resolve_credential=lambda alias: fake_secret,
            targets=twelve,
            locations=LOCATIONS,
            domain=OWN,
            http_post=lambda url, headers, body: (500, "Server Error", b"down"),
            http_get=fake_locations_multi,
        )
    except RuntimeError as e:
        threw_all_failed = str(e)
    checks.append(("an all-targets-failed pull raises rather than emitting 12 phantom absences", "no usable result for any" in threw_all_failed))

    # A location whose code cannot be resolved must not take the other location with it.
    def fake_locations_greeley_only(url, headers):
        payload = {"tasks": [{"result": [{"location_code": 200, "location_name": "Greeley,Colorado,United States"}]}]}
        return 200, "OK", json.dumps(payload).encode("utf-8")

    partial_loc = pull_local_rank(
        credential_alias="acme-dataforseo-read",
        resolve_credential=lambda alias: fake_secret,
        targets=twelve,
        locations=LOCATIONS,
        domain=OWN,
        http_post=fake_serp_counting,
        http_get=fake_locations_greeley_only,
    )
    denver_rows = [r for r in partial_loc["rows"] if r["location_name"] == "Denver"]
    greeley_rows = [r for r in partial_loc["rows"] if r["location_name"] == "Greeley"]
    checks.append(("an unresolvable location errors only its own rows", all(r["error_reason"] == ERR_LOCATION for r in denver_rows)))
    checks.append(("...while the resolvable location is still measured", greeley_rows and all(r["status"] != "error" and r["organic_results_seen"] == 3 for r in greeley_rows)))
    checks.append(("every target still gets a row when a location fails", len(partial_loc["rows"]) == 12))

    # --- location pinning, unchanged from 2026-07-26 ------------------------------
    pin_posts = []

    def fake_serp_pin(url, headers, body_bytes):
        body = json.loads(body_bytes)
        pin_posts.append((body[0]["keyword"], body[0]["location_code"]))
        return 200, "OK", json.dumps(_ok_body(body[0]["keyword"])).encode("utf-8")

    matched_local_rank = pull_local_rank(
        credential_alias="acme-dataforseo-read",
        resolve_credential=lambda alias: fake_secret,
        targets=[
            {"keyword": "chiropractor denver", "page": "/chiropractor/", "location": "Denver"},
            {"keyword": "general keyword", "page": "/general/"},
        ],
        locations=LOCATIONS,
        domain=OWN,
        http_post=fake_serp_pin,
        http_get=fake_locations_multi,
    )
    checks.append(("a pinned target is checked from its own location only (3 checks, not 4)", len(pin_posts) == 3))
    greeley_rows = [r for r in matched_local_rank["rows"] if r["location_name"] == "Greeley"]
    checks.append(("a target pinned to Denver never appears under Greeley's rows", all(r["keyword"] != "chiropractor denver" for r in greeley_rows)))
    checks.append(("a target with no location still appears under every configured location (fallback preserved)", any(r["keyword"] == "general keyword" for r in greeley_rows)))

    def fake_backlinks(url, headers, body_bytes):
        if "summary" in url:
            payload = {"tasks": [{"result": [{"referring_domains": 40, "backlinks": 120}]}]}
        else:
            payload = {
                "tasks": [
                    {
                        "result": [
                            {
                                "new_backlinks": 7,
                                "lost_backlinks": 3,
                                "new_referring_domains": 2,
                                "lost_referring_domains": 1,
                            }
                        ]
                    }
                ]
            }
        return 200, "OK", json.dumps(payload).encode("utf-8")

    backlinks = pull_backlinks(
        credential_alias="acme-dataforseo-read",
        resolve_credential=lambda alias: fake_secret,
        target="example.com",
        http_post=fake_backlinks,
    )
    checks.append(("backlinks summary maps referring domain totals", backlinks["summary"]["referring_domains"] == 40))
    checks.append(("backlinks history maps new/lost counts", backlinks["history"]["lost_backlinks"] == 3))

    def fake_http_unauthorized(url, headers, body_bytes):
        return 401, "Unauthorized", f"invalid credentials: {fake_secret}".encode("utf-8")

    api_error_message = ""
    try:
        pull_metrics(
            credential_alias="acme-dataforseo-read",
            resolve_credential=lambda alias: fake_secret,
            targets=targets,
            domain=OWN,
            http_post=fake_http_unauthorized,
        )
    except RuntimeError as e:
        api_error_message = str(e)
    checks.append(("API error surfaces status", "401" in api_error_message))
    checks.append(("credential echoed back in an API error body is redacted before throwing", fake_secret not in api_error_message))
    checks.append(("an unauthorized response body is never echoed into the error", "invalid credentials" not in api_error_message))

    unauth_local = ""
    try:
        pull_local_rank(
            credential_alias="acme-dataforseo-read",
            resolve_credential=lambda alias: fake_secret,
            targets=twelve,
            locations=LOCATIONS,
            domain=OWN,
            http_post=fake_http_unauthorized,
            http_get=fake_locations_multi,
        )
    except RuntimeError as e:
        unauth_local = str(e)
    checks.append(("a wholly unauthorized local-rank pull raises and leaks no credential", "no usable result for any" in unauth_local and fake_secret not in unauth_local))

    redacted_sample = redact_deep({"token": fake_secret}, {"acme-dataforseo-read": fake_secret})
    checks.append(("redaction hook available and functional for this connector", fake_secret not in redacted_sample["token"]))

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    sys.exit(1 if failed else 0)


_STATE_NAMES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}


if __name__ == "__main__" and "--verify" in sys.argv:
    _self_test()

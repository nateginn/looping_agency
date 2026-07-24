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
except ImportError:
    from lib.redact import redact_deep, redact_text

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


def pull_metrics(
    credential_alias=None,
    resolve_credential=None,
    targets=None,
    location_code=2840,
    language_code="en",
    device="desktop",
    http_post=_default_http_post,
):
    """
    resolve_credential: callable(alias) -> "login:password" credential string in a live run.
    targets: list of {"keyword": str, "page": str} pairs to rank-check.
    location_code: DataForSEO location code, defaults to 2840 (United States).
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

    credentials = resolve_credential(credential_alias)
    secret_map = {credential_alias: credentials}
    headers = _auth_headers(credentials)
    tasks = [{"keyword": t["keyword"], "location_code": location_code, "language_code": language_code, "device": device} for t in targets]
    body_bytes = json.dumps(tasks).encode("utf-8")

    try:
        status, reason, raw = http_post(SERP_ENDPOINT, headers, body_bytes)
    except Exception as e:
        raise RuntimeError(redact_text(f"dataforseo.py: request to SERP API failed: {e}", secret_map)) from None

    if status < 200 or status >= 300:
        _raise_api_error("dataforseo.py: SERP API", status, reason, raw, secret_map)

    body = _decode_json(raw)
    api_tasks = body.get("tasks") or []

    keywords = []
    for i, task in enumerate(api_tasks):
        target = targets[i] if i < len(targets) else {}
        result = task.get("result") or []
        items = (result[0].get("items") if result else None) or []
        match = next(
            (item for item in items if item.get("type") == "organic" and target.get("page") and target["page"] in (item.get("url") or "")),
            None,
        )
        keywords.append(
            {
                "keyword": target.get("keyword"),
                "page": target.get("page"),
                "position": match.get("rank_absolute") if match else None,
                "clicks": None,
                "impressions": None,
            }
        )

    out = {
        "source": "dataforseo",
        "pulled_at": _now_iso(),
        "credential_alias": credential_alias,
        "sample_size": len(keywords),
        "keywords": keywords,
        "secretMap": secret_map,
    }
    return redact_deep(out, secret_map)


def pull_local_rank(
    credential_alias=None,
    resolve_credential=None,
    targets=None,
    locations=None,
    language_code="en",
    device="desktop",
    http_post=_default_http_post,
    http_get=_default_http_get,
):
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

    credentials = resolve_credential(credential_alias)
    secret_map = {credential_alias: credentials}
    headers = _auth_headers(credentials)
    rows = []

    for location in locations:
        location_code, location_name = _resolve_location_code(credentials, location, http_get)
        tasks = [{"keyword": t["keyword"], "location_code": location_code, "language_code": language_code, "device": device} for t in targets]
        body_bytes = json.dumps(tasks).encode("utf-8")
        try:
            status, reason, raw = http_post(SERP_ENDPOINT, headers, body_bytes)
        except Exception as e:
            raise RuntimeError(redact_text(f"dataforseo.py: request to local-rank SERP API failed: {e}", secret_map)) from None
        if status < 200 or status >= 300:
            _raise_api_error("dataforseo.py: local-rank SERP API", status, reason, raw, secret_map)

        body = _decode_json(raw)
        api_tasks = body.get("tasks") or []
        for i, task in enumerate(api_tasks):
            target = targets[i] if i < len(targets) else {}
            result = task.get("result") or []
            items = (result[0].get("items") if result else None) or []
            match = next(
                (item for item in items if item.get("type") == "organic" and target.get("page") and target["page"] in (item.get("url") or "")),
                None,
            )
            rows.append(
                {
                    "location_name": location.get("name"),
                    "location_address": location.get("address"),
                    "zip": location.get("zip"),
                    "location_code": location_code,
                    "location_target": location_name,
                    "keyword": target.get("keyword"),
                    "page": target.get("page"),
                    "organic_rank_position": match.get("rank_absolute") if match else None,
                    "result_url": match.get("url") if match else None,
                }
            )

    out = {
        "source": "dataforseo-local-rank",
        "as_of": _now_iso(),
        "rank_metric": "organic_rank_position",
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

    fake_secret = "sk-test-fake-dataforseo-token"
    targets = [{"keyword": "ai marketing loops", "page": "/blog/ai-marketing"}]

    def fake_serp_ok(url, headers, body_bytes):
        payload = {
            "tasks": [
                {
                    "result": [
                        {
                            "items": [
                                {"type": "organic", "rank_absolute": 6, "url": "https://example.com/blog/ai-marketing"},
                                {"type": "organic", "rank_absolute": 1, "url": "https://someoneelse.com/other-page"},
                            ]
                        }
                    ]
                }
            ]
        }
        return 200, "OK", json.dumps(payload).encode("utf-8")

    result = pull_metrics(
        credential_alias="acme-dataforseo-read",
        resolve_credential=lambda alias: fake_secret,
        targets=targets,
        http_post=fake_serp_ok,
    )
    checks.append(("live call path maps SERP items into keyword records", result["keywords"][0]["position"] == 6))
    checks.append(("clicks/impressions are None (SERP has no click data)", result["keywords"][0]["clicks"] is None))
    checks.append(("resolved credential never appears unredacted in the returned object", fake_secret not in json.dumps(result)))

    def fake_locations(url, headers):
        payload = {"tasks": [{"result": [{"location_code": 123, "location_name": "Denver,Colorado,United States"}]}]}
        return 200, "OK", json.dumps(payload).encode("utf-8")

    local_rank = pull_local_rank(
        credential_alias="acme-dataforseo-read",
        resolve_credential=lambda alias: fake_secret,
        targets=[{"keyword": "ai marketing loops", "page": "/blog/ai-marketing"}],
        locations=[{"name": "Denver", "address": "2480 W 26th Ave #90B, Denver, CO 80211", "zip": "80211"}],
        http_post=fake_serp_ok,
        http_get=fake_locations,
    )
    checks.append(("local rank uses fallback organic_rank_position field", local_rank["rows"][0]["organic_rank_position"] == 6))
    checks.append(("local rank stores resolved location metadata", local_rank["rows"][0]["location_code"] == 123))

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
            http_post=fake_http_unauthorized,
        )
    except RuntimeError as e:
        api_error_message = str(e)
    checks.append(("API error surfaces status", "401" in api_error_message))
    checks.append(("credential echoed back in an API error body is redacted before throwing", fake_secret not in api_error_message))

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

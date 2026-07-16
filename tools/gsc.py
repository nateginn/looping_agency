# Google Search Console connector - read-only scope only.
#
# Phase 1 boundary: this file implements the real Search Analytics API call,
# but keeps the same safety gate as before - pull_metrics() raises unless a
# credential resolver is explicitly injected. Nothing in this codebase
# wires a real resolver yet (run_loop.py's fetch_metrics only calls
# mock_metrics.py), so this cannot be reached from any run pathway until
# a live credential resolver (Windows Credential Manager) is built and
# deliberately wired in - that wiring is Phase 1(b)/Phase 2, not this file.
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote

from lib.redact import redact_deep, redact_text

REQUIRED_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"


def _default_http_post(url, headers, body_bytes):
    """Returns (status, reason, response_text_or_json_bytes). Raises on network failure."""
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.reason, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.reason, e.read()


def pull_metrics(
    credential_alias=None,
    resolve_credential=None,
    site_url=None,
    start_date=None,
    end_date=None,
    dimensions=None,
    row_limit=1000,
    http_post=_default_http_post,
):
    """
    resolve_credential: callable(alias) -> secret string. Injected for tests only;
        in a live run this would read Windows Credential Manager. Omitting it is the safe default.
    site_url: the GSC-verified property, e.g. "https://example.com/" or "sc-domain:example.com".
    start_date/end_date: "YYYY-MM-DD".
    http_post: callable(url, headers, body_bytes) -> (status, reason, response_bytes). Injected for tests only.
    """
    if not credential_alias:
        raise ValueError("gsc.py: credential_alias is required")
    if not callable(resolve_credential):
        raise ValueError(
            f'gsc.py: no credential resolver configured for alias "{credential_alias}". '
            "Live GSC access is out of scope until a real credential resolver is wired in "
            "(see AgentColabPlan.md Sequencing) - this connector refuses to run rather than guess."
        )
    if not site_url:
        raise ValueError("gsc.py: site_url is required (the GSC-verified property)")
    if not start_date or not end_date:
        raise ValueError("gsc.py: start_date and end_date are required (YYYY-MM-DD)")

    dimensions = dimensions or ["query", "page"]
    secret = resolve_credential(credential_alias)
    secret_map = {credential_alias: secret}

    endpoint = f"https://www.googleapis.com/webmasters/v3/sites/{quote(site_url, safe='')}/searchAnalytics/query"
    headers = {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}
    body_bytes = json.dumps({"startDate": start_date, "endDate": end_date, "dimensions": dimensions, "rowLimit": row_limit}).encode("utf-8")

    try:
        status, reason, raw = http_post(endpoint, headers, body_bytes)
    except Exception as e:  # network-level failure
        raise RuntimeError(redact_text(f"gsc.py: request to Search Analytics API failed: {e}", secret_map)) from None

    if status < 200 or status >= 300:
        body_text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        raise RuntimeError(redact_text(f"gsc.py: Search Analytics API returned {status} {reason}: {body_text}", secret_map))

    body = json.loads(raw)
    rows = body.get("rows") or []
    keywords = []
    for row in rows:
        keys = row.get("keys") or []
        keyword = keys[0] if len(keys) > 0 else None
        page = keys[1] if len(keys) > 1 else None
        keywords.append(
            {
                "keyword": keyword,
                "page": page,
                "position": row.get("position"),
                "clicks": row.get("clicks"),
                "impressions": row.get("impressions"),
            }
        )

    result = {
        "source": "gsc",
        "site_url": site_url,
        "pulled_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "credential_alias": credential_alias,
        "sample_size": sum(k["impressions"] or 0 for k in keywords),
        "keywords": keywords,
        "secretMap": secret_map,
    }
    return redact_deep(result, secret_map)


def _self_test():
    checks = []

    threw_no_resolver = False
    try:
        pull_metrics(credential_alias="acme-gsc-readonly", site_url="sc-domain:example.com", start_date="2026-01-01", end_date="2026-01-31")
    except ValueError as e:
        threw_no_resolver = "no credential resolver configured" in str(e)
    checks.append(("refuses to run without an injected resolver", threw_no_resolver))

    threw_no_alias = False
    try:
        pull_metrics()
    except ValueError:
        threw_no_alias = True
    checks.append(("refuses to run without a credential_alias", threw_no_alias))

    threw_no_site_url = False
    try:
        pull_metrics(
            credential_alias="acme-gsc-readonly",
            resolve_credential=lambda alias: "sk-test-fake-gsc-token",
            start_date="2026-01-01",
            end_date="2026-01-31",
        )
    except ValueError as e:
        threw_no_site_url = "site_url is required" in str(e)
    checks.append(("refuses to run without a site_url", threw_no_site_url))

    fake_secret = "sk-test-fake-gsc-token"

    def fake_http_ok(url, headers, body_bytes):
        payload = {"rows": [{"keys": ["test query", "/blog/test"], "clicks": 3, "impressions": 100, "position": 5.2}]}
        return 200, "OK", json.dumps(payload).encode("utf-8")

    result = pull_metrics(
        credential_alias="acme-gsc-readonly",
        resolve_credential=lambda alias: fake_secret,
        site_url="sc-domain:example.com",
        start_date="2026-01-01",
        end_date="2026-01-31",
        http_post=fake_http_ok,
    )
    checks.append(("live call path maps API rows into keyword records", result["keywords"][0]["keyword"] == "test query"))
    checks.append(("sample_size derived from impressions", result["sample_size"] == 100))
    checks.append(("resolved secret never appears unredacted in the returned object", fake_secret not in json.dumps(result)))

    def fake_http_unauthorized(url, headers, body_bytes):
        return 401, "Unauthorized", f"invalid token: {fake_secret}".encode("utf-8")

    api_error_message = ""
    try:
        pull_metrics(
            credential_alias="acme-gsc-readonly",
            resolve_credential=lambda alias: fake_secret,
            site_url="sc-domain:example.com",
            start_date="2026-01-01",
            end_date="2026-01-31",
            http_post=fake_http_unauthorized,
        )
    except RuntimeError as e:
        api_error_message = str(e)
    checks.append(("API error surfaces status", "401" in api_error_message))
    checks.append(("secret echoed back in an API error body is redacted before throwing", fake_secret not in api_error_message))

    redacted_sample = redact_deep({"token": fake_secret}, {"acme-gsc-readonly": fake_secret})
    checks.append(("redaction hook available and functional for this connector", fake_secret not in redacted_sample["token"]))

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__" and "--verify" in sys.argv:
    _self_test()

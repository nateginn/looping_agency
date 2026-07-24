# Google Search Console connector - read-only scope only.
#
# Implements:
# - Search Analytics pull
# - Sitemaps list
# - URL Inspection checks for priority pages
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote

try:
    from .lib.redact import redact_deep, redact_text
except ImportError:
    from lib.redact import redact_deep, redact_text

REQUIRED_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
SEARCH_ANALYTICS_ENDPOINT = "https://www.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
SITEMAPS_ENDPOINT = "https://www.googleapis.com/webmasters/v3/sites/{site}/sitemaps"
URL_INSPECTION_ENDPOINT = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"


def _default_http_post(url, headers, body_bytes):
    """Returns (status, reason, response_bytes). Raises on network failure."""
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.reason, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.reason, e.read()


def _default_http_get(url, headers):
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.reason, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.reason, e.read()


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_common(credential_alias, resolve_credential, site_url):
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


def _auth_headers(secret):
    return {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}


def _raise_api_error(prefix, status, reason, raw, secret_map):
    body_text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    raise RuntimeError(redact_text(f"{prefix} returned {status} {reason}: {body_text}", secret_map))


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
    _require_common(credential_alias, resolve_credential, site_url)
    if not start_date or not end_date:
        raise ValueError("gsc.py: start_date and end_date are required (YYYY-MM-DD)")

    dimensions = dimensions or ["query", "page"]
    secret = resolve_credential(credential_alias)
    secret_map = {credential_alias: secret}

    endpoint = SEARCH_ANALYTICS_ENDPOINT.format(site=quote(site_url, safe=""))
    headers = _auth_headers(secret)
    body_bytes = json.dumps({"startDate": start_date, "endDate": end_date, "dimensions": dimensions, "rowLimit": row_limit}).encode("utf-8")

    try:
        status, reason, raw = http_post(endpoint, headers, body_bytes)
    except Exception as e:  # network-level failure
        raise RuntimeError(redact_text(f"gsc.py: request to Search Analytics API failed: {e}", secret_map)) from None

    if status < 200 or status >= 300:
        _raise_api_error("gsc.py: Search Analytics API", status, reason, raw, secret_map)

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
        "pulled_at": _now_iso(),
        "credential_alias": credential_alias,
        "sample_size": sum(k["impressions"] or 0 for k in keywords),
        "keywords": keywords,
        "secretMap": secret_map,
    }
    return redact_deep(result, secret_map)


def pull_sitemaps(
    credential_alias=None,
    resolve_credential=None,
    site_url=None,
    http_get=_default_http_get,
):
    _require_common(credential_alias, resolve_credential, site_url)
    secret = resolve_credential(credential_alias)
    secret_map = {credential_alias: secret}
    endpoint = SITEMAPS_ENDPOINT.format(site=quote(site_url, safe=""))
    try:
        status, reason, raw = http_get(endpoint, _auth_headers(secret))
    except Exception as e:
        raise RuntimeError(redact_text(f"gsc.py: request to Sitemaps API failed: {e}", secret_map)) from None
    if status < 200 or status >= 300:
        _raise_api_error("gsc.py: Sitemaps API", status, reason, raw, secret_map)

    body = json.loads(raw)
    rows = []
    for sitemap in body.get("sitemap") or []:
        rows.append(
            {
                "path": sitemap.get("path"),
                "type": sitemap.get("type"),
                "last_submitted": sitemap.get("lastSubmitted"),
                "is_pending": sitemap.get("isPending"),
                "last_downloaded": sitemap.get("lastDownloaded"),
                "warnings": sitemap.get("warnings"),
                "errors": sitemap.get("errors"),
            }
        )

    return redact_deep(
        {
            "source": "gsc-sitemaps",
            "as_of": _now_iso(),
            "rows": rows,
            "secretMap": secret_map,
        },
        secret_map,
    )


def inspect_urls(
    credential_alias=None,
    resolve_credential=None,
    site_url=None,
    urls=None,
    language_code="en-US",
    http_post=_default_http_post,
):
    _require_common(credential_alias, resolve_credential, site_url)
    if not isinstance(urls, list) or len(urls) < 1:
        raise ValueError("gsc.py: urls is required (non-empty list of absolute URLs)")

    secret = resolve_credential(credential_alias)
    secret_map = {credential_alias: secret}
    headers = _auth_headers(secret)
    rows = []
    for url in urls:
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ValueError("gsc.py: each inspection URL must be absolute")
        body_bytes = json.dumps({"inspectionUrl": url, "siteUrl": site_url, "languageCode": language_code}).encode("utf-8")
        try:
            status, reason, raw = http_post(URL_INSPECTION_ENDPOINT, headers, body_bytes)
        except Exception as e:
            raise RuntimeError(redact_text(f"gsc.py: request to URL Inspection API failed: {e}", secret_map)) from None
        if status < 200 or status >= 300:
            _raise_api_error("gsc.py: URL Inspection API", status, reason, raw, secret_map)

        result = ((json.loads(raw).get("inspectionResult")) or {})
        index_result = result.get("indexStatusResult") or {}
        rows.append(
            {
                "page": url,
                "coverage_state": index_result.get("coverageState"),
                "verdict": index_result.get("verdict"),
                "robots_txt_state": index_result.get("robotsTxtState"),
                "indexing_state": index_result.get("indexingState"),
                "last_crawl_time": index_result.get("lastCrawlTime"),
                "page_fetch_state": index_result.get("pageFetchState"),
                "google_canonical": index_result.get("googleCanonical"),
                "user_canonical": index_result.get("userCanonical"),
            }
        )

    return redact_deep(
        {
            "source": "gsc-url-inspection",
            "as_of": _now_iso(),
            "rows": rows,
            "secretMap": secret_map,
        },
        secret_map,
    )


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

    def fake_sitemaps(url, headers):
        payload = {"sitemap": [{"path": "https://example.com/sitemap.xml", "type": "sitemap", "isPending": False, "warnings": "0", "errors": "0"}]}
        return 200, "OK", json.dumps(payload).encode("utf-8")

    sitemaps = pull_sitemaps(
        credential_alias="acme-gsc-readonly",
        resolve_credential=lambda alias: fake_secret,
        site_url="sc-domain:example.com",
        http_get=fake_sitemaps,
    )
    checks.append(("sitemaps list maps sitemap entries", sitemaps["rows"][0]["path"] == "https://example.com/sitemap.xml"))

    def fake_inspect(url, headers, body_bytes):
        payload = {
            "inspectionResult": {
                "indexStatusResult": {
                    "coverageState": "Submitted and indexed",
                    "verdict": "PASS",
                    "indexingState": "INDEXING_ALLOWED",
                    "robotsTxtState": "ALLOWED",
                }
            }
        }
        return 200, "OK", json.dumps(payload).encode("utf-8")

    inspections = inspect_urls(
        credential_alias="acme-gsc-readonly",
        resolve_credential=lambda alias: fake_secret,
        site_url="sc-domain:example.com",
        urls=["https://example.com/blog/test"],
        http_post=fake_inspect,
    )
    checks.append(("url inspection maps indexation fields", inspections["rows"][0]["coverage_state"] == "Submitted and indexed"))

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

# Google PageSpeed Insights connector - read-only scope only.
#
# Uses the free PSI API for per-page CWV/performance checks. A credential
# alias is optional because PSI can be called keyless at low volume, but an
# injected resolver is still supported for API-key usage.
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

try:
    from .lib.redact import redact_deep, redact_text
except ImportError:
    from lib.redact import redact_deep, redact_text

REQUIRED_SCOPE = "read-only (PageSpeed Insights API)"
PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

# PSI runs a live Lighthouse render per call and returns a 500
# ("Lighthouse returned error: Something went wrong") on its own transient
# internal failures - observed 2026-08-10, where a single 500 aborted the whole
# weekly art/seo run. The call is a read-only idempotent GET against a free
# API, so retrying is safe and costs nothing but wall-clock.
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (2, 5)


def _is_retryable_status(status):
    """5xx only. A 4xx is a real answer - 403 (bad/absent key), 429 (quota
    exhausted), 400 (bad URL) - and retrying it just burns quota and time."""
    return isinstance(status, int) and 500 <= status < 600


def _default_http_get(url, headers):
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.reason, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.reason, e.read()


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _status_for_lcp(value_ms):
    if value_ms is None:
        return "unknown"
    if value_ms <= 2500:
        return "good"
    if value_ms <= 4000:
        return "needs-improvement"
    return "poor"


def _status_for_inp(value_ms):
    if value_ms is None:
        return "unknown"
    if value_ms <= 200:
        return "good"
    if value_ms <= 500:
        return "needs-improvement"
    return "poor"


def _status_for_cls(value):
    if value is None:
        return "unknown"
    if value <= 0.1:
        return "good"
    if value <= 0.25:
        return "needs-improvement"
    return "poor"


def _rollup_status(statuses):
    if "poor" in statuses:
        return "poor"
    if "needs-improvement" in statuses:
        return "needs-improvement"
    if statuses and all(s == "good" for s in statuses):
        return "good"
    return "unknown"


def pull_pagespeed(
    credential_alias=None,
    resolve_credential=None,
    pages=None,
    strategy="MOBILE",
    category="performance",
    http_get=_default_http_get,
    max_attempts=MAX_ATTEMPTS,
    sleep=time.sleep,
):
    if pages is None or not isinstance(pages, list) or len(pages) < 1:
        raise ValueError("pagespeed.py: pages is required (non-empty list of absolute URLs)")
    if credential_alias and not callable(resolve_credential):
        raise ValueError(
            f'pagespeed.py: credential_alias "{credential_alias}" was provided but no credential resolver was injected'
        )

    api_key = resolve_credential(credential_alias) if credential_alias else None
    secret_map = {credential_alias: api_key} if credential_alias and api_key else {}
    rows = []
    for page in pages:
        if not isinstance(page, str) or not page.startswith(("http://", "https://")):
            raise ValueError("pagespeed.py: each page must be an absolute URL")
        query = {"url": page, "strategy": strategy, "category": category}
        if api_key:
            query["key"] = api_key
        url = f"{PSI_ENDPOINT}?{urllib.parse.urlencode(query)}"
        attempts = max(1, int(max_attempts or 1))
        raw = None
        for attempt in range(1, attempts + 1):
            last_attempt = attempt == attempts
            try:
                status, reason, raw = http_get(url, {})
            except Exception as e:
                # Transport-level failure (timeout, reset, DNS). Retryable for
                # the same reason a 5xx is: nothing was answered.
                if last_attempt:
                    raise RuntimeError(redact_text(f"pagespeed.py: request to PSI API failed after {attempt} attempt(s): {e}", secret_map)) from None
                sleep(RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])
                continue
            if 200 <= status < 300:
                break
            body_text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
            if _is_retryable_status(status) and not last_attempt:
                sleep(RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])
                continue
            suffix = f" after {attempt} attempt(s)" if attempt > 1 else ""
            raise RuntimeError(redact_text(f"pagespeed.py: PSI API returned {status} {reason}{suffix}: {body_text}", secret_map))

        body = json.loads(raw)
        metrics = (((body.get("loadingExperience") or {}).get("metrics")) or {})
        categories = ((((body.get("lighthouseResult") or {}).get("categories")) or {}))
        lcp_ms = (((metrics.get("LARGEST_CONTENTFUL_PAINT_MS") or {}).get("percentile")))
        inp_ms = (((metrics.get("INTERACTION_TO_NEXT_PAINT") or {}).get("percentile")))
        cls_value = (((metrics.get("CUMULATIVE_LAYOUT_SHIFT_SCORE") or {}).get("percentile")))
        if cls_value is not None:
            cls_value = cls_value / 100.0
        performance_score = categories.get("performance", {}).get("score")
        if performance_score is not None:
            performance_score = int(round(performance_score * 100))
        lcp_status = _status_for_lcp(lcp_ms)
        inp_status = _status_for_inp(inp_ms)
        cls_status = _status_for_cls(cls_value)
        rows.append(
            {
                "page": page,
                "strategy": strategy.lower(),
                "lcp_ms": lcp_ms,
                "inp_ms": inp_ms,
                "cls": cls_value,
                "performance_score": performance_score,
                "cwv_status": _rollup_status([lcp_status, inp_status, cls_status]),
                "metric_statuses": {
                    "lcp": lcp_status,
                    "inp": inp_status,
                    "cls": cls_status,
                },
            }
        )

    return redact_deep(
        {
            "source": "pagespeed",
            "as_of": _now_iso(),
            "rows": rows,
            "secretMap": secret_map,
        },
        secret_map,
    )


def _self_test():
    checks = []
    fake_key = "sk-test-psi-key"

    threw_no_pages = False
    try:
        pull_pagespeed()
    except ValueError as e:
        threw_no_pages = "pages is required" in str(e)
    checks.append(("refuses to run without pages", threw_no_pages))

    threw_missing_resolver = False
    try:
        pull_pagespeed(credential_alias="psi-key", pages=["https://example.com/"])
    except ValueError as e:
        threw_missing_resolver = "no credential resolver" in str(e)
    checks.append(("refuses alias-based auth without injected resolver", threw_missing_resolver))

    def fake_http_ok(url, headers):
        payload = {
            "loadingExperience": {
                "metrics": {
                    "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2400},
                    "INTERACTION_TO_NEXT_PAINT": {"percentile": 180},
                    "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 8},
                }
            },
            "lighthouseResult": {"categories": {"performance": {"score": 0.91}}},
        }
        return 200, "OK", json.dumps(payload).encode("utf-8")

    result = pull_pagespeed(
        credential_alias="psi-key",
        resolve_credential=lambda alias: fake_key,
        pages=["https://example.com/"],
        http_get=fake_http_ok,
    )
    row = result["rows"][0]
    checks.append(("PSI response maps LCP/INP/CLS", row["lcp_ms"] == 2400 and row["inp_ms"] == 180 and abs(row["cls"] - 0.08) < 0.0001))
    checks.append(("CWV rollup status is derived from per-metric statuses", row["cwv_status"] == "good"))
    checks.append(("performance score normalizes to 0-100", row["performance_score"] == 91))
    checks.append(("resolved key never appears unredacted in the returned object", fake_key not in json.dumps(result)))

    def fake_http_unauthorized(url, headers):
        return 403, "Forbidden", f"bad key {fake_key}".encode("utf-8")

    api_error_message = ""
    try:
        pull_pagespeed(
            credential_alias="psi-key",
            resolve_credential=lambda alias: fake_key,
            pages=["https://example.com/"],
            http_get=fake_http_unauthorized,
        )
    except RuntimeError as e:
        api_error_message = str(e)
    checks.append(("API error surfaces status", "403" in api_error_message))
    checks.append(("key echoed in PSI error body is redacted before throwing", fake_key not in api_error_message))

    # Retry behaviour - the 2026-08-10 failure mode: a transient Lighthouse 500
    # aborting the whole run. Sleeps are injected so the tests stay instant.
    slept = []
    calls = {"n": 0}

    def fake_http_500_then_ok(url, headers):
        calls["n"] += 1
        if calls["n"] < 3:
            return 500, "Internal Server Error", b'{"error":{"message":"Lighthouse returned error: Something went wrong."}}'
        return fake_http_ok(url, headers)

    recovered = pull_pagespeed(
        pages=["https://example.com/"],
        http_get=fake_http_500_then_ok,
        sleep=slept.append,
    )
    checks.append(("a transient PSI 500 is retried and the run recovers", recovered["rows"][0]["performance_score"] == 91))
    checks.append(("retries back off rather than hammering the API", slept == [2, 5]))

    calls_always_500 = {"n": 0}

    def fake_http_always_500(url, headers):
        calls_always_500["n"] += 1
        return 500, "Internal Server Error", b"persistent lighthouse failure"

    gave_up_message = ""
    try:
        pull_pagespeed(pages=["https://example.com/"], http_get=fake_http_always_500, sleep=lambda s: None)
    except RuntimeError as e:
        gave_up_message = str(e)
    checks.append(("a persistent 500 gives up after MAX_ATTEMPTS, it does not loop forever", calls_always_500["n"] == MAX_ATTEMPTS))
    checks.append(("the give-up error says how many attempts were made", "3 attempt(s)" in gave_up_message))

    calls_403 = {"n": 0}

    def fake_http_403_counting(url, headers):
        calls_403["n"] += 1
        return 403, "Forbidden", b"bad key"

    try:
        pull_pagespeed(pages=["https://example.com/"], http_get=fake_http_403_counting, sleep=lambda s: None)
    except RuntimeError:
        pass
    checks.append(("a 4xx is a real answer and is never retried (no quota burn on 403/429)", calls_403["n"] == 1))

    transport_calls = {"n": 0}

    def fake_http_transport_error(url, headers):
        transport_calls["n"] += 1
        raise OSError("connection reset")

    try:
        pull_pagespeed(pages=["https://example.com/"], http_get=fake_http_transport_error, sleep=lambda s: None)
    except RuntimeError:
        pass
    checks.append(("a transport failure is retried too, then reported", transport_calls["n"] == MAX_ATTEMPTS))

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__" and "--verify" in sys.argv:
    _self_test()

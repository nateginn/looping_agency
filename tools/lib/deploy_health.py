# Phase 6a (PLAN.md "Autonomous merge and deployment safety" / RISK-
# REGISTER.md R11-R12) - bounded, read-only, offline-testable post-
# deployment health checks against a live site. Every check is an
# unauthenticated public DNS/TLS/HTTP read (never SSH, never a deploy
# credential) with a fixed retry count and a per-request timeout - never
# an unbounded loop or an unbounded sleep. This module draws no SEO
# conclusion: it only answers "is the deployed site reachable and
# structurally sane" (PLAN.md: "The health check confirms availability and
# the configured contract; it does not prove SEO quality or business
# impact. Those remain the SEO loop's measured outcomes.").
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_RETRIES = 3
DEFAULT_RETRY_DELAY_SECONDS = 2

CANONICAL_RE = re.compile(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', re.IGNORECASE)
BLANKET_DISALLOW_RE = re.compile(r"user-agent:\s*\*\s*\n(?:\s*\n)*\s*disallow:\s*/\s*$", re.IGNORECASE | re.MULTILINE)


def _now_iso(now=None):
    now = now or datetime.now(timezone.utc)
    return now.isoformat().replace("+00:00", "Z")


def _strip_www(host):
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def _default_http_get(url, timeout):
    request = urllib.request.Request(url, headers={"User-Agent": "looping-agency-health-check"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers or {}), err.read()


def _default_dns_resolver(hostname):
    return socket.gethostbyname(hostname)


def _default_tls_connector(hostname, port, timeout):
    context = ssl.create_default_context()
    with socket.create_connection((hostname, port), timeout=timeout) as sock:
        with context.wrap_socket(sock, server_hostname=hostname) as tls_sock:
            return tls_sock.getpeercert()


def _with_retries(fn, retries, delay_seconds, sleep=None):
    """Run fn() up to `retries` times total (minimum 1), sleeping between
    attempts (never after the last) via the injectable `sleep` - offline
    tests pass a no-op so self-tests never actually block. Always bounded:
    `retries` is a fixed small int, never a while-True poll. Raises the
    last exception on exhaustion; returns (result, attempt_count) on
    success."""
    sleep = sleep or time.sleep
    retries = max(1, retries)
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            return fn(), attempt
        except Exception as err:
            last_err = err
            if attempt < retries:
                sleep(delay_seconds)
    raise RuntimeError(f"failed after {retries} attempt(s): {last_err}") from last_err


def check_dns(hostname, retries=DEFAULT_RETRIES, timeout=DEFAULT_TIMEOUT_SECONDS, resolver=None, sleep=None):
    resolver = resolver or _default_dns_resolver
    try:
        ip, attempts = _with_retries(lambda: resolver(hostname), retries, DEFAULT_RETRY_DELAY_SECONDS, sleep=sleep)
        return {"name": "dns", "ok": True, "detail": f"resolved {hostname} -> {ip}", "attempts": attempts}
    except Exception as err:
        return {"name": "dns", "ok": False, "detail": f"DNS resolution failed for {hostname}: {err}", "attempts": max(1, retries)}


def check_tls(hostname, port=443, retries=DEFAULT_RETRIES, timeout=DEFAULT_TIMEOUT_SECONDS, connector=None, sleep=None):
    connector = connector or _default_tls_connector
    try:
        _cert, attempts = _with_retries(lambda: connector(hostname, port, timeout), retries, DEFAULT_RETRY_DELAY_SECONDS, sleep=sleep)
        return {"name": "tls", "ok": True, "detail": f"TLS handshake succeeded for {hostname}:{port}", "attempts": attempts}
    except Exception as err:
        return {"name": "tls", "ok": False, "detail": f"TLS handshake failed for {hostname}:{port}: {err}", "attempts": max(1, retries)}


def check_http_status(url, expected_status=200, retries=DEFAULT_RETRIES, timeout=DEFAULT_TIMEOUT_SECONDS, http_get=None, sleep=None):
    http_get = http_get or _default_http_get
    try:
        (status, _headers, _body), attempts = _with_retries(lambda: http_get(url, timeout), retries, DEFAULT_RETRY_DELAY_SECONDS, sleep=sleep)
    except Exception as err:
        return {"name": "http_status", "ok": False, "detail": f"{url} request failed: {err}", "attempts": max(1, retries)}
    ok = status == expected_status
    return {"name": "http_status", "ok": ok, "detail": f"{url} -> {status} (expected {expected_status})", "attempts": attempts}


def check_canonical(url, expected_domain, retries=DEFAULT_RETRIES, timeout=DEFAULT_TIMEOUT_SECONDS, http_get=None, sleep=None):
    """Fetch `url` and confirm it declares a <link rel="canonical"> whose
    scheme is https and whose host matches expected_domain (www-agnostic).
    Sanity only, not full SEO validation - that stays the loop's own
    measured outcomes, never this health check."""
    http_get = http_get or _default_http_get
    try:
        (status, _headers, body), attempts = _with_retries(lambda: http_get(url, timeout), retries, DEFAULT_RETRY_DELAY_SECONDS, sleep=sleep)
    except Exception as err:
        return {"name": "canonical", "ok": False, "detail": f"{url} request failed: {err}", "attempts": max(1, retries)}
    if status != 200:
        return {"name": "canonical", "ok": False, "detail": f"{url} returned {status}, cannot check canonical", "attempts": attempts}
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
    match = CANONICAL_RE.search(text)
    if not match:
        return {"name": "canonical", "ok": False, "detail": f'{url} has no <link rel="canonical"> tag', "attempts": attempts}
    href = match.group(1)
    parsed = urllib.parse.urlsplit(href)
    ok = parsed.scheme == "https" and _strip_www(parsed.netloc) == _strip_www(expected_domain)
    return {"name": "canonical", "ok": ok, "detail": f"{url} canonical -> {href!r} (expected https://{expected_domain}/...)", "attempts": attempts}


def check_robots(base_url, retries=DEFAULT_RETRIES, timeout=DEFAULT_TIMEOUT_SECONDS, http_get=None, sleep=None):
    """Sanity only: robots.txt is reachable (200) and does not contain a
    blanket `Disallow: /` for `User-agent: *` (which would deindex the
    entire site) - not a full robots.txt parse or validation."""
    robots_url = base_url.rstrip("/") + "/robots.txt"
    http_get = http_get or _default_http_get
    try:
        (status, _headers, body), attempts = _with_retries(lambda: http_get(robots_url, timeout), retries, DEFAULT_RETRY_DELAY_SECONDS, sleep=sleep)
    except Exception as err:
        return {"name": "robots", "ok": False, "detail": f"{robots_url} request failed: {err}", "attempts": max(1, retries)}
    if status != 200:
        return {"name": "robots", "ok": False, "detail": f"{robots_url} returned {status}", "attempts": attempts}
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
    if BLANKET_DISALLOW_RE.search(text):
        return {"name": "robots", "ok": False, "detail": f"{robots_url} disallows all crawling for User-agent: * (Disallow: /)", "attempts": attempts}
    return {"name": "robots", "ok": True, "detail": f"{robots_url} reachable, no blanket disallow", "attempts": attempts}


def check_representative_pages(urls, retries=DEFAULT_RETRIES, timeout=DEFAULT_TIMEOUT_SECONDS, http_get=None, sleep=None):
    """One http_status-style check per representative lead-intent URL, so
    a single bad/missing page surfaces by name rather than as one opaque
    aggregate failure. An empty list is itself treated as not-ok - missing
    deployment metadata, not "nothing to check"."""
    if not urls:
        return {"name": "representative_pages", "ok": False, "detail": "no representative_pages configured - cannot verify lead-intent URLs", "attempts": 0, "pages": []}
    pages = [check_http_status(url, expected_status=200, retries=retries, timeout=timeout, http_get=http_get, sleep=sleep) for url in urls]
    ok_count = sum(1 for p in pages if p["ok"])
    return {
        "name": "representative_pages",
        "ok": ok_count == len(pages),
        "detail": f"{ok_count}/{len(pages)} representative pages OK",
        "attempts": max((p["attempts"] for p in pages), default=0),
        "pages": pages,
    }


def check_deployed_commit_evidence(url, expected_sha, extractor=None, retries=DEFAULT_RETRIES, timeout=DEFAULT_TIMEOUT_SECONDS, http_get=None, sleep=None):
    """Best-effort only, per PLAN.md's "evidence of the deployed commit
    where safely available" scoping - nothing on the real production site
    is currently known to expose a build/commit marker (documented open
    integration point, not assumed to exist). Without an `extractor`
    supplied this reports status "not_available" (a distinct, non-failing
    outcome) rather than fabricating a pass or blocking a deploy on a
    signal that was never configured. `extractor(status, headers, body) ->
    str|None` pulls a commit-ish string out of a real response once one is
    wired up (e.g. a response header, a static build-info asset, a meta
    tag)."""
    if extractor is None:
        return {"name": "commit_evidence", "ok": True, "status": "not_available", "detail": "no commit-evidence extractor configured - skipped, not a failure", "attempts": 0}
    http_get = http_get or _default_http_get
    try:
        (status, headers, body), attempts = _with_retries(lambda: http_get(url, timeout), retries, DEFAULT_RETRY_DELAY_SECONDS, sleep=sleep)
    except Exception as err:
        return {"name": "commit_evidence", "ok": False, "status": "error", "detail": f"{url} request failed: {err}", "attempts": max(1, retries)}
    try:
        found = extractor(status, headers, body)
    except Exception as err:
        return {"name": "commit_evidence", "ok": False, "status": "error", "detail": f"commit-evidence extractor raised: {err}", "attempts": attempts}
    if not found:
        return {"name": "commit_evidence", "ok": False, "status": "no_marker_found", "detail": f"{url} did not expose a recognizable commit marker", "attempts": attempts}
    expected_short = (expected_sha or "")[:7]
    ok = bool(expected_short) and (found.startswith(expected_short) or expected_short.startswith(found[:7]))
    return {"name": "commit_evidence", "ok": ok, "status": "checked", "detail": f"{url} marker={found!r} expected={expected_short!r}", "attempts": attempts}


def run_health_checks(config, http_get=None, resolver=None, tls_connector=None, sleep=None, commit_evidence_extractor=None, now=None):
    """Run every bounded check against `config` (a plain dict: hostname,
    base_url, expected_status, representative_pages, expected_sha,
    commit_evidence_url, retries, timeout) and return {"ok": bool,
    "checked_at": iso, "checks": [...]}. `commit_evidence_extractor` is a
    callable, never persisted on the record - passed here, not read from
    config, to keep the deploy JSON record purely serializable.

    `commit_evidence` is advisory/best-effort only (see its own docstring)
    and never blocks the overall result - every other check must pass."""
    if not isinstance(config, dict) or not config.get("hostname") or not config.get("base_url"):
        raise ValueError("run_health_checks: config must be an object with at least hostname and base_url")

    retries = config.get("retries", DEFAULT_RETRIES)
    timeout = config.get("timeout", DEFAULT_TIMEOUT_SECONDS)
    hostname = config["hostname"]
    base_url = config["base_url"]

    checks = [
        check_dns(hostname, retries=retries, timeout=timeout, resolver=resolver, sleep=sleep),
        check_tls(hostname, retries=retries, timeout=timeout, connector=tls_connector, sleep=sleep),
        check_http_status(base_url, expected_status=config.get("expected_status", 200), retries=retries, timeout=timeout, http_get=http_get, sleep=sleep),
        check_canonical(base_url, _strip_www(hostname), retries=retries, timeout=timeout, http_get=http_get, sleep=sleep),
        check_robots(base_url, retries=retries, timeout=timeout, http_get=http_get, sleep=sleep),
        check_representative_pages(config.get("representative_pages") or [], retries=retries, timeout=timeout, http_get=http_get, sleep=sleep),
        check_deployed_commit_evidence(
            config.get("commit_evidence_url", base_url),
            config.get("expected_sha", ""),
            extractor=commit_evidence_extractor,
            retries=retries,
            timeout=timeout,
            http_get=http_get,
            sleep=sleep,
        ),
    ]
    blocking = [c for c in checks if c["name"] != "commit_evidence"]
    ok = all(c["ok"] for c in blocking)
    return {"ok": ok, "checked_at": _now_iso(now), "checks": checks}


def _self_test():
    checks = []

    # ---- _with_retries: bounded, never unbounded ----
    calls = []

    def flaky_twice():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("transient")
        return "ok"

    sleeps = []
    result, attempts = _with_retries(flaky_twice, retries=5, delay_seconds=0.01, sleep=lambda s: sleeps.append(s))
    checks.append(("_with_retries succeeds after transient failures, within the bound", result == "ok" and attempts == 3))
    checks.append(("_with_retries sleeps between attempts but never after the last (2 sleeps for 3 attempts)", len(sleeps) == 2))

    exhausted_calls = []

    def always_fails():
        exhausted_calls.append(1)
        raise RuntimeError("permanent")

    exhausted = False
    try:
        _with_retries(always_fails, retries=3, delay_seconds=0.01, sleep=lambda s: None)
    except RuntimeError as e:
        exhausted = "failed after 3 attempt(s)" in str(e)
    checks.append(("_with_retries raises (not loops forever) once the fixed retry bound is exhausted", exhausted))
    checks.append(("_with_retries made exactly `retries` attempts, not more", len(exhausted_calls) == 3))

    # ---- check_dns / check_tls ----
    checks.append(("check_dns accepts a resolver that succeeds immediately", check_dns("example.com", resolver=lambda h: "93.184.216.34", sleep=lambda s: None)["ok"] is True))
    dns_fail = check_dns("example.com", retries=2, resolver=lambda h: (_ for _ in ()).throw(OSError("nxdomain")), sleep=lambda s: None)
    checks.append(("check_dns reports failure cleanly, bounded to `retries` attempts", dns_fail["ok"] is False and "nxdomain" in dns_fail["detail"]))

    checks.append(("check_tls accepts a connector that succeeds", check_tls("example.com", connector=lambda h, p, t: {"subject": "ok"}, sleep=lambda s: None)["ok"] is True))
    tls_fail = check_tls("example.com", retries=2, connector=lambda h, p, t: (_ for _ in ()).throw(ssl.SSLError("handshake failed")), sleep=lambda s: None)
    checks.append(("check_tls reports failure cleanly", tls_fail["ok"] is False and "handshake failed" in tls_fail["detail"]))

    # ---- check_http_status ----
    def fake_get_200(url, timeout):
        return 200, {}, b"ok"

    checks.append(("check_http_status accepts a matching status", check_http_status("https://example.com/", http_get=fake_get_200, sleep=lambda s: None)["ok"] is True))

    def fake_get_500(url, timeout):
        return 500, {}, b"error"

    status_fail = check_http_status("https://example.com/", http_get=fake_get_500, sleep=lambda s: None)
    checks.append(("check_http_status refuses a mismatched status", status_fail["ok"] is False and "500" in status_fail["detail"]))

    # ---- check_canonical ----
    good_canonical_html = b'<html><head><link rel="canonical" href="https://acceleratedrehabtherapy.com/services/"></head></html>'

    def fake_get_canonical_good(url, timeout):
        return 200, {}, good_canonical_html

    canonical_ok = check_canonical("https://acceleratedrehabtherapy.com/services/", "acceleratedrehabtherapy.com", http_get=fake_get_canonical_good, sleep=lambda s: None)
    checks.append(("check_canonical accepts a matching https canonical tag", canonical_ok["ok"] is True))

    wrong_domain_html = b'<html><head><link rel="canonical" href="https://some-other-site.com/services/"></head></html>'

    def fake_get_canonical_wrong(url, timeout):
        return 200, {}, wrong_domain_html

    canonical_wrong = check_canonical("https://acceleratedrehabtherapy.com/services/", "acceleratedrehabtherapy.com", http_get=fake_get_canonical_wrong, sleep=lambda s: None)
    checks.append(("check_canonical refuses a canonical pointing at a different domain", canonical_wrong["ok"] is False))

    checks.append(("check_canonical is www-agnostic", check_canonical("https://www.acceleratedrehabtherapy.com/", "acceleratedrehabtherapy.com", http_get=fake_get_canonical_good, sleep=lambda s: None)["ok"] is True))

    def fake_get_no_canonical(url, timeout):
        return 200, {}, b"<html><head></head></html>"

    canonical_missing = check_canonical("https://acceleratedrehabtherapy.com/", "acceleratedrehabtherapy.com", http_get=fake_get_no_canonical, sleep=lambda s: None)
    checks.append(("check_canonical refuses when no canonical tag is present at all", canonical_missing["ok"] is False and "no" in canonical_missing["detail"].lower()))

    # ---- check_robots ----
    def fake_get_robots_good(url, timeout):
        return 200, {}, b"User-agent: *\nDisallow: /wp-admin/\nSitemap: https://acceleratedrehabtherapy.com/sitemap.xml\n"

    checks.append(("check_robots accepts a normal robots.txt", check_robots("https://acceleratedrehabtherapy.com", http_get=fake_get_robots_good, sleep=lambda s: None)["ok"] is True))

    def fake_get_robots_blanket(url, timeout):
        return 200, {}, b"User-agent: *\nDisallow: /\n"

    robots_blanket = check_robots("https://acceleratedrehabtherapy.com", http_get=fake_get_robots_blanket, sleep=lambda s: None)
    checks.append(("check_robots refuses a blanket Disallow: / for User-agent: *", robots_blanket["ok"] is False and "disallows all crawling" in robots_blanket["detail"]))

    def fake_get_robots_404(url, timeout):
        return 404, {}, b"not found"

    robots_404 = check_robots("https://acceleratedrehabtherapy.com", http_get=fake_get_robots_404, sleep=lambda s: None)
    checks.append(("check_robots refuses when robots.txt itself 404s", robots_404["ok"] is False))

    # ---- check_representative_pages ----
    def fake_get_pages_all_ok(url, timeout):
        return 200, {}, b"ok"

    pages_ok = check_representative_pages(["https://acceleratedrehabtherapy.com/physical-therapy/", "https://acceleratedrehabtherapy.com/chiropractor/"], http_get=fake_get_pages_all_ok, sleep=lambda s: None)
    checks.append(("check_representative_pages accepts when every page is 200", pages_ok["ok"] is True and pages_ok["detail"] == "2/2 representative pages OK"))

    def fake_get_pages_one_bad(url, timeout):
        return (200, {}, b"ok") if "chiropractor" not in url else (404, {}, b"missing")

    pages_one_bad = check_representative_pages(["https://acceleratedrehabtherapy.com/physical-therapy/", "https://acceleratedrehabtherapy.com/chiropractor/"], http_get=fake_get_pages_one_bad, sleep=lambda s: None)
    checks.append(("check_representative_pages surfaces exactly which page failed, not just an opaque failure", pages_one_bad["ok"] is False and pages_one_bad["detail"] == "1/2 representative pages OK"))
    checks.append(("check_representative_pages names the failing page by URL", any("chiropractor" in p["detail"] and not p["ok"] for p in pages_one_bad["pages"])))

    pages_empty = check_representative_pages([], sleep=lambda s: None)
    checks.append(("check_representative_pages treats an empty/missing list as not-ok (missing metadata), not vacuously fine", pages_empty["ok"] is False))

    # ---- check_deployed_commit_evidence ----
    evidence_unconfigured = check_deployed_commit_evidence("https://acceleratedrehabtherapy.com/", "abc1234", extractor=None)
    checks.append(("check_deployed_commit_evidence with no extractor is a non-failing 'not_available', not a fabricated pass or a hard failure", evidence_unconfigured["ok"] is True and evidence_unconfigured["status"] == "not_available"))

    def fake_get_evidence(url, timeout):
        return 200, {"X-Build-SHA": "abc1234def"}, b""

    evidence_match = check_deployed_commit_evidence(
        "https://acceleratedrehabtherapy.com/", "abc1234def5678", extractor=lambda s, h, b: h.get("X-Build-SHA"), http_get=fake_get_evidence, sleep=lambda s: None
    )
    checks.append(("check_deployed_commit_evidence matches a real extractor against the expected SHA", evidence_match["ok"] is True and evidence_match["status"] == "checked"))

    evidence_mismatch = check_deployed_commit_evidence(
        "https://acceleratedrehabtherapy.com/", "zzz9999", extractor=lambda s, h, b: h.get("X-Build-SHA"), http_get=fake_get_evidence, sleep=lambda s: None
    )
    checks.append(("check_deployed_commit_evidence refuses when the extracted marker does not match the expected SHA", evidence_mismatch["ok"] is False and evidence_mismatch["status"] == "checked"))

    def fake_get_no_evidence(url, timeout):
        return 200, {}, b""

    evidence_no_marker = check_deployed_commit_evidence(
        "https://acceleratedrehabtherapy.com/", "abc1234", extractor=lambda s, h, b: h.get("X-Build-SHA"), http_get=fake_get_no_evidence, sleep=lambda s: None
    )
    checks.append(("check_deployed_commit_evidence reports no_marker_found distinctly when configured but absent", evidence_no_marker["ok"] is False and evidence_no_marker["status"] == "no_marker_found"))

    # ---- run_health_checks (orchestration) ----
    good_config = {
        "hostname": "acceleratedrehabtherapy.com",
        "base_url": "https://acceleratedrehabtherapy.com",
        "representative_pages": ["https://acceleratedrehabtherapy.com/physical-therapy/"],
        "expected_sha": "abc1234",
    }

    def fake_get_all_good(url, timeout):
        if url.rstrip("/").endswith("robots.txt"):
            return 200, {}, b"User-agent: *\nDisallow: /wp-admin/\n"
        if url == good_config["base_url"]:
            return 200, {}, good_canonical_html.replace(b"/services/", b"/")
        return 200, {}, b"ok"

    good_result = run_health_checks(
        good_config, http_get=fake_get_all_good, resolver=lambda h: "1.2.3.4", tls_connector=lambda h, p, t: {"ok": True}, sleep=lambda s: None
    )
    checks.append(("run_health_checks reports overall ok when every check passes", good_result["ok"] is True))
    checks.append(("run_health_checks includes every check type", {c["name"] for c in good_result["checks"]} == {"dns", "tls", "http_status", "canonical", "robots", "representative_pages", "commit_evidence"}))
    checks.append(("run_health_checks stamps checked_at", bool(good_result["checked_at"])))

    def fake_get_dns_ok_but_404_homepage(url, timeout):
        return 404, {}, b"not found"

    partial_fail = run_health_checks(
        good_config, http_get=fake_get_dns_ok_but_404_homepage, resolver=lambda h: "1.2.3.4", tls_connector=lambda h, p, t: {"ok": True}, sleep=lambda s: None
    )
    checks.append(("run_health_checks reports overall not-ok when any blocking check fails", partial_fail["ok"] is False))

    commit_evidence_only_config = dict(good_config)
    unavailable_evidence_still_ok = run_health_checks(
        commit_evidence_only_config, http_get=fake_get_all_good, resolver=lambda h: "1.2.3.4", tls_connector=lambda h, p, t: {"ok": True}, sleep=lambda s: None, commit_evidence_extractor=None
    )
    checks.append(("run_health_checks treats commit_evidence as advisory - unavailable never blocks overall ok", unavailable_evidence_still_ok["ok"] is True))

    missing_config_refused = False
    try:
        run_health_checks({"hostname": "example.com"})
    except ValueError as e:
        missing_config_refused = "must be an object with at least hostname and base_url" in str(e)
    checks.append(("run_health_checks refuses malformed/incomplete config rather than crashing with a KeyError", missing_config_refused))

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__" and "--verify" in sys.argv:
    _self_test()

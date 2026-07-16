# DataForSEO connector - read-only scope only (SERP rank tracking).
#
# Phase 1 boundary: this file implements the real SERP API call, but keeps
# the same safety gate as before - pull_metrics() raises unless a credential
# resolver is explicitly injected. Nothing in this codebase wires a real
# resolver yet (run_loop.py's fetch_metrics only calls mock_metrics.py), so
# this cannot be reached from any run pathway until a live credential
# resolver (Windows Credential Manager) is built and deliberately wired in
# - that wiring is Phase 1(b)/Phase 2, not this file.
#
# Unlike GSC, DataForSEO's SERP endpoint reports rank position only - it has
# no click/impression data, so those fields are always None here.
import base64
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

from lib.redact import redact_deep, redact_text

REQUIRED_SCOPE = "read-only (SERP + keyword data endpoints)"


def _default_http_post(url, headers, body_bytes):
    """Returns (status, reason, response_bytes). Raises on network failure."""
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.reason, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.reason, e.read()


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
    basic_auth = base64.b64encode(credentials.encode("utf-8")).decode("ascii")

    endpoint = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"
    tasks = [{"keyword": t["keyword"], "location_code": location_code, "language_code": language_code, "device": device} for t in targets]
    headers = {"Authorization": f"Basic {basic_auth}", "Content-Type": "application/json"}
    body_bytes = json.dumps(tasks).encode("utf-8")

    try:
        status, reason, raw = http_post(endpoint, headers, body_bytes)
    except Exception as e:
        raise RuntimeError(redact_text(f"dataforseo.py: request to SERP API failed: {e}", secret_map)) from None

    if status < 200 or status >= 300:
        body_text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        raise RuntimeError(redact_text(f"dataforseo.py: SERP API returned {status} {reason}: {body_text}", secret_map))

    body = json.loads(raw)
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
        "pulled_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "credential_alias": credential_alias,
        "sample_size": len(keywords),
        "keywords": keywords,
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

    def fake_http_ok(url, headers, body_bytes):
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
        http_post=fake_http_ok,
    )
    checks.append(("live call path maps SERP items into keyword records", result["keywords"][0]["position"] == 6))
    checks.append(("clicks/impressions are None (SERP has no click data)", result["keywords"][0]["clicks"] is None))
    checks.append(("resolved credential never appears unredacted in the returned object", fake_secret not in json.dumps(result)))

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


if __name__ == "__main__" and "--verify" in sys.argv:
    _self_test()

# Mock GSC-like connector for projects/_demo. Never calls a real API -
# used only for the offline dry-run and Phase-1 test harness. Real
# connectors (gsc.py, dataforseo.py) are separate and untouched here.
#
# Modes (via scenario):
#   normal   - stable/improving keyword positions
#   breach   - a tracked page drops >5 positions (guardrail breach)
#   fail     - throws, simulating a connector outage/auth failure
#
# Always returns a `_raw_auth_header` field carrying a fake secret value so
# the redaction pipeline has something real to catch (Milestone-1 redaction test).
import sys
from datetime import datetime, timezone

try:
    from .lib.errors import ConnectorError  # noqa: F401 - re-exported for compat
except ImportError:
    from lib.errors import ConnectorError  # noqa: F401

FAKE_SECRET = "sk-demo-FAKE1234567890ABCDEFDONOTUSE"


def _keyword_set(scenario):
    base = [
        {"keyword": "best loop agency", "page": "/blog/loop-agency", "position": 8.2, "clicks": 42, "impressions": 900},
        {"keyword": "seo automation tool", "page": "/blog/seo-automation", "position": 11.5, "clicks": 18, "impressions": 640},
        {"keyword": "ai marketing loops", "page": "/blog/ai-marketing", "position": 6.1, "clicks": 55, "impressions": 1100},
    ]
    if scenario == "breach":
        # /blog/seo-automation regressed from ~11.5 to 19 -> a >5 position drop.
        base[1] = {**base[1], "position": 19.0, "clicks": 4, "impressions": 640}
    return base


def pull_metrics(scenario="normal", credential_alias="demo-gsc-readonly"):
    if scenario == "fail":
        raise ConnectorError(
            f"connector auth failed for alias {credential_alias}: token {FAKE_SECRET} rejected (HTTP 401, simulated)",
            raw_secrets={credential_alias: FAKE_SECRET},
            tool_name="mock-metrics",
        )

    keywords = _keyword_set(scenario)
    return {
        "source": "mock-metrics",
        "scenario": scenario,
        "pulled_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "credential_alias": credential_alias,
        "sample_size": sum(k["impressions"] for k in keywords),
        "keywords": keywords,
        # Present in every real pull so the redactor has a concrete target - never written unredacted.
        "_rawAuthHeader": f"Bearer {FAKE_SECRET} (alias {credential_alias})",
        "secretMap": {credential_alias: FAKE_SECRET},
    }


def _self_test():
    checks = []
    normal = pull_metrics(scenario="normal")
    checks.append(("normal scenario returns 3 keywords", len(normal["keywords"]) == 3))
    checks.append(("normal scenario carries a fake secret to redact", "sk-demo-FAKE" in normal["_rawAuthHeader"]))

    breach = pull_metrics(scenario="breach")
    regressed = next(k for k in breach["keywords"] if k["page"] == "/blog/seo-automation")
    checks.append(("breach scenario regresses tracked page by >5 positions", regressed["position"] - 11.5 > 5))

    threw = False
    try:
        pull_metrics(scenario="fail")
    except ConnectorError:
        threw = True
    checks.append(("fail scenario throws", threw))

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__" and "--verify" in sys.argv:
    _self_test()

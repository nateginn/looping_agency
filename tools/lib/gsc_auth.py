# GSC service-account auth. The stored secret for a gsc alias may be either
# a raw OAuth bearer token (used as-is; expires ~1h, smoke-test-grade) or a
# Google service-account JSON key (the durable form): this module detects
# which, and mints a short-lived access token from the JSON when needed.
# Scope is webmasters.readonly only - the loop never gets a write-capable
# credential. The JSON itself never leaves memory here.
import json
import sys

GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"


def is_service_account_json(secret):
    if not isinstance(secret, str) or not secret.lstrip().startswith("{"):
        return False
    try:
        parsed = json.loads(secret)
    except ValueError:
        return False
    return isinstance(parsed, dict) and parsed.get("type") == "service_account"


def mint_access_token(sa_json_str):
    """Exchange a service-account JSON key for a short-lived access token
    (webmasters.readonly). Requires network; google-auth is imported lazily so
    offline callers (tests, passthrough tokens) never need it."""
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    info = json.loads(sa_json_str)
    credentials = service_account.Credentials.from_service_account_info(info, scopes=[GSC_SCOPE])
    credentials.refresh(Request())
    if not credentials.token:
        raise RuntimeError("gsc_auth: token refresh succeeded but returned no access token")
    return credentials.token


def bearer_for_secret(secret, mint=None):
    """Resolve a stored gsc secret to a bearer token: service-account JSON is
    exchanged for a fresh access token, anything else passes through as an
    already-usable token. mint is injected by tests only."""
    if is_service_account_json(secret):
        return (mint or mint_access_token)(secret)
    return secret


def _self_test():
    checks = []

    raw_token = "ya29.fake-raw-access-token-for-test"
    checks.append(("raw bearer token passes through unchanged", bearer_for_secret(raw_token) == raw_token))

    sa_json = json.dumps(
        {
            "type": "service_account",
            "client_email": "fake@fake-project.iam.gserviceaccount.com",
            "private_key": "-----BEGIN PRIVATE KEY-----\nFAKE\n-----END PRIVATE KEY-----\n",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )
    minted_with = []

    def fake_mint(s):
        minted_with.append(s)
        return "minted-access-token"

    checks.append(("service-account JSON is detected", is_service_account_json(sa_json) is True))
    checks.append(("service-account JSON routes through the minter", bearer_for_secret(sa_json, mint=fake_mint) == "minted-access-token"))
    checks.append(("minter receives the full JSON string", minted_with == [sa_json]))
    checks.append(("non-service-account JSON passes through", bearer_for_secret('{"type": "other"}') == '{"type": "other"}'))
    checks.append(("brace-shaped non-JSON passes through", bearer_for_secret("{not json") == "{not json"))
    checks.append(("google-auth is importable in this venv", __import__("google.oauth2.service_account") is not None))

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__" and "--verify" in sys.argv:
    _self_test()

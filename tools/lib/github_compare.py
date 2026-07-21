import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def _default_requester(url, headers):
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, "OK", response.read()
    except urllib.error.HTTPError as err:
        return err.code, err.reason, err.read()


def compare_commit_to_main(owner, repo, commit_sha, requester=None, token=None):
    requester = requester or _default_requester
    url = f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(repo)}/compare/{urllib.parse.quote(commit_sha)}...main"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "looping-agency-phase3",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    status_code, reason, body = requester(url, headers)
    if status_code != 200:
        text = body.decode("utf-8", errors="replace")
        raise ValueError(f"GitHub compare API failed ({status_code} {reason}): {text}")
    payload = json.loads(body.decode("utf-8"))
    status = payload.get("status")
    return {
        "url": url,
        "status": status,
        "live": status in ("identical", "ahead"),
        "payload": payload,
    }


def _self_test():
    calls = []

    def fake_requester(url, headers):
        calls.append((url, headers))
        return 200, "OK", json.dumps({"status": "ahead"}).encode("utf-8")

    result = compare_commit_to_main("nateginn", "artwebsite", "abc123", requester=fake_requester)
    checks = [
        ("compare requester called exactly once", len(calls) == 1),
        ("compare API URL targets main ancestry", calls[0][0].endswith("/compare/abc123...main")),
        ("ahead counts as live", result["live"] is True and result["status"] == "ahead"),
    ]

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__" and "--verify" in sys.argv:
    _self_test()

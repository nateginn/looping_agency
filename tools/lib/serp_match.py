"""Shared SERP result matching primitives - "is this result ours, and where?"

Extracted 2026-08-16 for Issue #1 (`CURRENT-WORK.md`; design record
`PLAN-SEO-PROGRAM-INTEGRATION.md` P0.1). Three call sites had to agree on this
question and only one of them got it right:

- `tools/dataforseo.py` matched `target["page"] in item["url"]` - a bare substring
  test with no domain check (defect D1), so a competitor URL carrying our path was
  recorded as our rank, and reported `rank_absolute`, which counts ads/local-pack/PAA
  blocks, under an "organic rank" label (defect D5).
- `tools/serp_diagnostic.py` got it right, but only inside a one-shot diagnostic.
- `tools/lib/timeseries.py` got it right, but only to *classify history after the
  fact*; it is the dashboard, and a connector must not import it.

So the primitives live here, below all three. `timeseries.py` imports
`registrable_host`/`path_matches` from this module rather than keeping its own copies -
its fixtures are the regression suite that proved them.

Pure functions over already-parsed data. No I/O, no network, no secrets.
"""
import sys
from urllib.parse import urlsplit


def registrable_host(url):
    """Lower-cased host with `www.` stripped, or None.

    Not a public-suffix parse. It is used to decide whether a result is the client's
    own site, so it must never be lenient - see `is_own_url`.
    """
    if not url:
        return None
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def path_matches(target_path, url):
    """Segment-boundary match: `/massage/` must not match `/massage-therapy-guide/`.

    Wrapping both sides in slashes makes a plain substring test exact at segment
    boundaries - `/massage-therapy-guide/` contains `/massage-`, never `/massage/`.
    """
    if not target_path or not url:
        return False
    path = urlsplit(url).path or "/"
    want = target_path if target_path.startswith("/") else "/" + target_path
    if not want.endswith("/"):
        want += "/"
    if not path.endswith("/"):
        path += "/"
    return want in path


def is_own_url(url, domain):
    """True only if `url`'s host is `domain`, its `www.` variant, or a subdomain.

    Deliberately strict, and deliberately has no "no domain configured" fallback: the
    callers require a domain (`connector_registry.py` lists it under `requires`) so that
    an unconfigured spec refuses to validate rather than silently reviving D1's
    substring matching behind a default.
    """
    host = registrable_host(url)
    want = (domain or "").strip().lower().lstrip(".")
    if want.startswith("www."):
        want = want[4:]
    if not host or not want:
        return False
    return host == want or host.endswith("." + want)


def scan_organic(items, domain, target_page=None):
    """One pass over a SERP's items, counting the true organic index.

    `rank_absolute` numbers every SERP element (ads, local pack, People Also Ask);
    `rank_group` restarts per element type. Verified against the real 2026-08-11
    diagnostic response: for `type == "organic"` items `rank_group` does equal the
    running organic index, so the two are cross-checked rather than one being trusted -
    `rank_group_agrees` is False if DataForSEO ever disagrees with the count, and the
    counted index always wins.

    Returns:
      organic_seen      - how many organic results the response actually contained
      target_match      - the best own-domain result whose path matches `target_page`
      domain_presence   - every own-domain organic result, any page (only ours is
                          recorded; competitors are discarded per standing decision 4)
      rank_group_agrees - whether every organic item's rank_group matched the count
    """
    organic_index = 0
    target_match = None
    domain_presence = []
    rank_group_agrees = True

    for item in items or []:
        if not isinstance(item, dict) or item.get("type") != "organic":
            continue
        organic_index += 1
        rank_group = item.get("rank_group")
        if isinstance(rank_group, int) and rank_group != organic_index:
            rank_group_agrees = False
        url = item.get("url") or ""
        if not is_own_url(url, domain):
            continue
        hit = {
            "organic_position": organic_index,
            "organic_rank_group": rank_group,
            "rank_absolute": item.get("rank_absolute"),
            "url": url,
            "matched_domain": registrable_host(url),
        }
        domain_presence.append({"organic_position": organic_index, "url": url})
        if target_match is None and path_matches(target_page, url):
            target_match = hit

    return {
        "organic_seen": organic_index,
        "target_match": target_match,
        "domain_presence": domain_presence,
        "rank_group_agrees": rank_group_agrees,
    }


def _self_test():
    checks = []
    # Synthetic hosts only. Standing decision 4 ("no data about other businesses is
    # stored, at all") governs test fixtures too, so the real competitor domains named
    # in P0.1's regression list are represented by .invalid stand-ins here.
    OWN = "ourclinic.invalid"

    checks.append(("registrable_host lower-cases and strips www.", registrable_host("https://WWW.Ourclinic.invalid/x") == "ourclinic.invalid"))
    checks.append(("registrable_host returns None for a non-url", registrable_host("") is None))
    checks.append(("registrable_host ignores port and path", registrable_host("https://ourclinic.invalid:8443/a/b") == "ourclinic.invalid"))

    checks.append(("own url: exact host matches", is_own_url("https://ourclinic.invalid/physical-therapy/", OWN)))
    checks.append(("own url: www. variant matches", is_own_url("https://www.ourclinic.invalid/physical-therapy/", OWN)))
    checks.append(("own url: subdomain matches", is_own_url("https://blog.ourclinic.invalid/x/", OWN)))
    checks.append(("own url: lookalike suffix is rejected", not is_own_url("https://notourclinic.invalid/", OWN)))
    checks.append(("own url: a configured www. domain still matches the bare host", is_own_url("https://ourclinic.invalid/x", "www.ourclinic.invalid")))
    checks.append(("own url: refuses when no domain is configured (no substring fallback)", not is_own_url("https://ourclinic.invalid/x", None)))
    checks.append(("own url: refuses an empty url", not is_own_url("", OWN)))

    # D1's regression cases, from real observed data, with synthetic hosts.
    checks.append(("D1: a competitor carrying our exact path is not ours", not is_own_url("https://competitor-one.invalid/physical-therapy/", OWN)))
    checks.append(("D1: a competitor nesting our path deeper is not ours", not is_own_url("https://competitor-two.invalid/care/rehab-and-therapy/physical-therapy/", OWN)))
    checks.append(("path match: exact segment matches", path_matches("/physical-therapy/", "https://ourclinic.invalid/physical-therapy/")))
    checks.append(("path match: /massage/ must not match /massage-therapy-guide/", not path_matches("/massage/", "https://ourclinic.invalid/massage-therapy-guide/")))
    checks.append(("path match: trailing slash is not required on either side", path_matches("massage", "https://ourclinic.invalid/massage")))
    checks.append(("path match: nested path still matches its segment", path_matches("/massage/", "https://ourclinic.invalid/services/massage/pricing")))
    checks.append(("path match: query string does not defeat the match", path_matches("/massage/", "https://ourclinic.invalid/massage/?utm_source=x")))
    checks.append(("path match: empty target never matches", not path_matches("", "https://ourclinic.invalid/massage/")))

    # D5's regression case: real composition from the 2026-08-11 diagnostic - a
    # three-entry local pack and a People Also Ask block sit above the organic results,
    # so rank_absolute is inflated by 4 relative to the true organic index.
    items = [
        {"type": "local_pack", "rank_absolute": 1, "rank_group": 1},
        {"type": "local_pack", "rank_absolute": 2, "rank_group": 2},
        {"type": "local_pack", "rank_absolute": 3, "rank_group": 3},
        {"type": "organic", "rank_absolute": 4, "rank_group": 1, "url": "https://competitor-one.invalid/physical-therapy/"},
        {"type": "people_also_ask", "rank_absolute": 5, "rank_group": 1},
        {"type": "organic", "rank_absolute": 6, "rank_group": 2, "url": "https://www.ourclinic.invalid/physical-therapy/"},
        {"type": "organic", "rank_absolute": 7, "rank_group": 3, "url": "https://competitor-two.invalid/care/physical-therapy/"},
        {"type": "organic", "rank_absolute": 8, "rank_group": 4, "url": "https://ourclinic.invalid/auto-injury/"},
    ]
    scan = scan_organic(items, OWN, "/physical-therapy/")
    checks.append(("D5: the reported position is the organic index, not rank_absolute", scan["target_match"]["organic_position"] == 2))
    checks.append(("D5: rank_absolute is kept alongside, not used as the rank", scan["target_match"]["rank_absolute"] == 6))
    checks.append(("D5: rank_group agrees with the counted organic index on real composition", scan["rank_group_agrees"] and scan["target_match"]["organic_rank_group"] == 2))
    checks.append(("non-organic blocks are not counted as organic results", scan["organic_seen"] == 4))
    checks.append(("the matched url is ours, never the competitor sharing the path", scan["target_match"]["url"] == "https://www.ourclinic.invalid/physical-therapy/"))
    checks.append(("matched_domain records the www.-normalized own host", scan["target_match"]["matched_domain"] == OWN))
    checks.append(("domain presence finds our other page too", [d["organic_position"] for d in scan["domain_presence"]] == [2, 4]))
    checks.append(("domain presence contains no competitor url", all("competitor" not in d["url"] for d in scan["domain_presence"])))

    absent = scan_organic(items, OWN, "/acupuncture/")
    checks.append(("a page of ours that does not rank yields no target match", absent["target_match"] is None))
    checks.append(("...but domain presence still records that the site ranks at all", len(absent["domain_presence"]) == 2))

    none_of_ours = scan_organic([{"type": "organic", "rank_absolute": 1, "rank_group": 1, "url": "https://competitor-one.invalid/physical-therapy/"}], OWN, "/physical-therapy/")
    checks.append(("a SERP with none of our results yields no match and no presence", none_of_ours["target_match"] is None and none_of_ours["domain_presence"] == []))

    empty = scan_organic([], OWN, "/x/")
    checks.append(("an empty SERP degrades cleanly", empty["organic_seen"] == 0 and empty["target_match"] is None))

    disagree = scan_organic([{"type": "organic", "rank_absolute": 1, "rank_group": 7, "url": "https://ourclinic.invalid/x/"}], OWN, "/x/")
    checks.append(("a rank_group that disagrees with the count is flagged, and the count wins", not disagree["rank_group_agrees"] and disagree["target_match"]["organic_position"] == 1))

    malformed = scan_organic([None, "not-a-dict", {"type": "organic", "rank_absolute": 1, "rank_group": 1, "url": "https://ourclinic.invalid/x/"}], OWN, "/x/")
    checks.append(("malformed items are skipped without breaking the organic count", malformed["target_match"]["organic_position"] == 1))

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__" and "--verify" in sys.argv:
    _self_test()

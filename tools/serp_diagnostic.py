"""One-shot SERP composition diagnostic (PLAN-SEO-PROGRAM-INTEGRATION.md P0-b).

Answers one question: for the queries where GSC reports a position ~1 but zero
clicks, what does the SERP actually contain, and does this site appear in it?

Deliberate design constraints, from the plan's P0-b section:
  - Inputs come from a standalone manifest file, NEVER from spec.md. Any field
    in the spec is a field a recurring connector may read, and this must run
    once, not daily. There is no connector_registry entry and no handler in
    run_loop.py's _dispatch_connector, so no scheduled job can reach this.
  - Writes only under projects/<slug>/diagnostics/. Never touches runs/,
    pending/, events.jsonl, state.json, or any proposal.
  - Scans EVERY organic result for the site's domain (the P0.1 fix applied in
    diagnostic form), rather than looking for one configured page path - which
    is exactly the defect that made the loop report competitors' URLs as ours.

Usage:
  python tools/serp_diagnostic.py <project> --manifest <path> [--dry-run]
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dataforseo
from lib.credentials import resolve_credential
from lib.paths import assert_within
from lib.redact import redact_deep
from lib.tls import enable_system_truststore

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _host(url):
    if not isinstance(url, str) or "://" not in url:
        return ""
    return url.split("://", 1)[1].split("/", 1)[0].lower()


def _is_site(url, domain):
    """Host-validated domain match - accepts a www. variant, rejects a
    competitor URL that merely contains our path (defect D1)."""
    h = _host(url)
    d = (domain or "").lower().lstrip(".")
    return bool(d) and (h == d or h == f"www.{d}" or h.endswith(f".{d}"))


def _walk_for_domain(node, domain, out, path="ai_overview"):
    """AI Overview reference shapes vary; recursively collect any url/domain
    field that resolves to our domain rather than assuming one schema."""
    if isinstance(node, dict):
        for key in ("url", "link", "source_url"):
            if isinstance(node.get(key), str) and _is_site(node[key], domain):
                out.append({"where": path, "url": node[key], "title": node.get("title")})
        if isinstance(node.get("domain"), str) and node["domain"].lower().lstrip("www.") == (domain or "").lower():
            out.append({"where": path, "url": node.get("url"), "title": node.get("title")})
        for k, v in node.items():
            _walk_for_domain(v, domain, out, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_for_domain(v, domain, out, f"{path}[{i}]")


def analyze_serp(items, domain):
    """Pure function over one SERP's items. No I/O."""
    composition = []
    organic_rank = 0
    site_organic = []
    top_competitors = []
    local_pack = {"present": False, "block_rank": None, "entries": 0, "site_in_pack": False, "site_entry": None, "names": []}
    ai_overview = {"present": False, "site_cited": False, "citations": []}

    for item in items or []:
        itype = item.get("type")
        composition.append({"type": itype, "rank_group": item.get("rank_group"), "rank_absolute": item.get("rank_absolute")})

        if itype == "organic":
            organic_rank += 1
            url = item.get("url") or ""
            if _is_site(url, domain):
                site_organic.append({"organic_position": organic_rank, "rank_absolute": item.get("rank_absolute"), "url": url, "title": item.get("title")})
            elif len(top_competitors) < 5:
                top_competitors.append({"organic_position": organic_rank, "host": _host(url), "url": url})

        elif itype in ("local_pack", "map", "google_business_profile", "local_services"):
            local_pack["present"] = True
            if local_pack["block_rank"] is None:
                local_pack["block_rank"] = item.get("rank_absolute")
            entries = item.get("items") if isinstance(item.get("items"), list) else [item]
            for pos, entry in enumerate(entries, start=1):
                local_pack["entries"] += 1
                title = entry.get("title") or entry.get("name")
                if title:
                    local_pack["names"].append(title)
                ours = _is_site(entry.get("url") or "", domain) or _is_site(entry.get("domain") or "", domain)
                if not ours and isinstance(title, str) and "accelerated rehab" in title.lower():
                    ours = True  # profile entries often carry no site URL at all
                if ours and not local_pack["site_in_pack"]:
                    local_pack["site_in_pack"] = True
                    local_pack["site_entry"] = {"pack_position": pos, "title": title, "rating": entry.get("rating"), "url": entry.get("url")}

        elif itype in ("ai_overview", "ai_overview_extended"):
            ai_overview["present"] = True
            found = []
            _walk_for_domain(item, domain, found)
            if found:
                ai_overview["site_cited"] = True
                ai_overview["citations"] = found[:5]

    return {
        "serp_composition": [c["type"] for c in composition],
        "composition_detail": composition[:40],
        "organic_results_seen": organic_rank,
        "domain_presence_top100": site_organic,
        "site_ranks_organically": bool(site_organic),
        "best_organic_position": site_organic[0]["organic_position"] if site_organic else None,
        "local_pack": local_pack,
        "ai_overview": ai_overview,
        "top_competitors": top_competitors,
    }


def run(project, manifest_path, dry_run=False, http_post=None, http_get=None):
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    domain = manifest["domain"]
    alias = manifest["credential_alias"]
    diagnostics = manifest["diagnostics"]
    locations = {loc["name"]: loc for loc in manifest["locations"]}

    tasks_planned = []
    for d in diagnostics:
        for loc_name in d["locations"]:
            if loc_name not in locations:
                raise ValueError(f'manifest: diagnostic "{d["query"]}" references unknown location "{loc_name}"')
            tasks_planned.append((d["query"], loc_name, d.get("device", "desktop")))

    print(f"SERP composition diagnostic - project {project}")
    print(f"  domain      : {domain}")
    print(f"  queries     : {len(diagnostics)}")
    print(f"  locations   : {', '.join(locations)}")
    print(f"  PAID TASKS  : {len(tasks_planned)}")
    for q, loc, dev in tasks_planned:
        print(f"     - {q!r} @ {loc} ({dev})")
    if dry_run:
        print("\n--dry-run: no API call made, nothing spent.")
        return None

    enable_system_truststore()  # live HTTPS through the Windows cert store (R8)
    project_dir = os.path.join(WORKSPACE, "projects", project)
    credentials = resolve_credential(alias, project_dir=project_dir)
    secret_map = {alias: credentials}
    headers = dataforseo._auth_headers(credentials)
    post = http_post or dataforseo._default_http_post
    get = http_get or dataforseo._default_http_get

    by_location = {}
    for q, loc_name, dev in tasks_planned:
        by_location.setdefault((loc_name, dev), []).append(q)

    results = []
    tasks_spent = 0
    for (loc_name, dev), queries in by_location.items():
        location_code, resolved_name = dataforseo._resolve_location_code(credentials, locations[loc_name], get)
        print(f"\n  {loc_name} -> location_code {location_code} ({resolved_name})")
        # ONE task per POST. DataForSEO's live/advanced endpoint rejects any
        # additional task in the same request with status 40000 "You can set
        # only one task at a time", returning result: None at zero cost -
        # which reads downstream as "no result found" rather than "never
        # checked". This is defect D4; the production connector still batches.
        tasks_returned = []
        for q in queries:
            payload = [{"keyword": q, "location_code": location_code, "language_code": manifest.get("language_code", "en"), "device": dev, "depth": 100}]
            status, reason, raw = post(dataforseo.SERP_ENDPOINT, headers, json.dumps(payload).encode("utf-8"))
            if status < 200 or status >= 300:
                dataforseo._raise_api_error("serp_diagnostic.py: SERP API", status, reason, raw, secret_map)
            body = dataforseo._decode_json(raw)
            for t in body.get("tasks") or []:
                tasks_returned.append(t)
                if t.get("status_code") == 20000:
                    tasks_spent += 1

        for task in tasks_returned:
            # Match by the keyword DataForSEO echoes back in task.data, never by
            # array index - a reordered or partially-failed response would
            # otherwise silently attribute one query's SERP to another.
            task_data = task.get("data") or {}
            query = task_data.get("keyword")
            status_code = task.get("status_code")
            status_message = task.get("status_message")
            result = task.get("result") or []
            items = (result[0].get("items") if result else None) or []

            # A failed or empty task is NOT evidence of absence. Reporting it as
            # "not ranking" would repeat defect D1's exact failure mode: an
            # absence of data presented as a finding.
            task_ok = status_code == 20000
            usable = task_ok and bool(result)
            if not usable:
                results.append({
                    "query": query,
                    "location_name": loc_name,
                    "device": dev,
                    "usable": False,
                    "status_code": status_code,
                    "status_message": status_message,
                    "result_blocks": len(result),
                    "total_serp_items": len(items),
                    "note": "TASK DID NOT RETURN A USABLE SERP - this is missing data, not evidence of absence",
                })
                print(f"     {query!r:<36} UNUSABLE (status {status_code}: {status_message}, result blocks {len(result)})")
                continue

            analysis = analyze_serp(items, domain)
            analysis.update({
                "query": query,
                "usable": True,
                "status_code": status_code,
                "status_message": status_message,
                "task_cost": task.get("cost"),
                "location_name": loc_name,
                "location_code": location_code,
                "resolved_location": resolved_name,
                "device": dev,
                "total_serp_items": len(items),
                "se_results_count": (result[0].get("se_results_count") if result else None),
            })
            results.append(analysis)
            lp = analysis["local_pack"]
            print(
                f"     {query!r:<36} organic={analysis['best_organic_position'] or '-'} "
                f"local_pack={'YES' if lp['present'] else 'no'}"
                f"{' (WE ARE IN IT #%s)' % lp['site_entry']['pack_position'] if lp['site_in_pack'] else ''} "
                f"ai_overview={'YES' if analysis['ai_overview']['present'] else 'no'}"
                f"{' (CITED)' if analysis['ai_overview']['site_cited'] else ''}"
            )

    out_dir = os.path.join(WORKSPACE, "projects", project, "diagnostics")
    assert_within(os.path.join(WORKSPACE, "projects", project), out_dir)
    os.makedirs(out_dir, exist_ok=True)
    stamp = _now_iso().replace(":", "-").replace(".", "-")
    out_path = os.path.join(out_dir, f"{stamp}-serp-composition.json")

    document = redact_deep(
        {
            "diagnostic": "serp-composition",
            "plan_reference": "PLAN-SEO-PROGRAM-INTEGRATION.md P0-b",
            "at": _now_iso(),
            "project": project,
            "domain": domain,
            "paid_tasks_spent": tasks_spent,
            "manifest_path": os.path.relpath(manifest_path, WORKSPACE),
            "results": results,
        },
        secret_map,
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(document, f, indent=2, sort_keys=True)
    print(f"\n  paid tasks spent: {tasks_spent}")
    print(f"  written: {os.path.relpath(out_path, WORKSPACE)}")
    print("  (no run artifact, no proposal, no event written - by design)")
    return document


def _self_test():
    checks = []
    D = "example.com"

    checks.append(("host match accepts exact domain", _is_site("https://example.com/x/", D)))
    checks.append(("host match accepts www variant", _is_site("https://www.example.com/x/", D)))
    checks.append(("host match REJECTS competitor sharing our path (defect D1)", not _is_site("https://www.mgmc.org/care/physical-therapy/", "acceleratedrehabtherapy.com")))
    checks.append(("host match rejects lookalike suffix", not _is_site("https://notexample.com/", D)))

    items = [
        {"type": "local_pack", "rank_absolute": 1, "items": [
            {"title": "Competitor Clinic", "url": "https://comp.com"},
            {"title": "Accelerated Rehab Therapy", "url": "https://example.com", "rating": {"value": 4.9}},
        ]},
        {"type": "organic", "rank_absolute": 5, "url": "https://other.com/physical-therapy/"},
        {"type": "organic", "rank_absolute": 6, "url": "https://example.com/physical-therapy/"},
        {"type": "ai_overview", "rank_absolute": 2, "items": [{"references": [{"url": "https://example.com/a", "title": "T"}]}]},
    ]
    a = analyze_serp(items, D)
    checks.append(("composition preserves SERP element order", a["serp_composition"] == ["local_pack", "organic", "organic", "ai_overview"]))
    checks.append(("finds our organic result anywhere in the list", a["best_organic_position"] == 2))
    checks.append(("competitor organic result is not attributed to us", a["domain_presence_top100"][0]["url"] == "https://example.com/physical-therapy/"))
    checks.append(("detects local pack and our entry within it", a["local_pack"]["present"] and a["local_pack"]["site_in_pack"] and a["local_pack"]["site_entry"]["pack_position"] == 2))
    checks.append(("detects ai_overview and our citation in it", a["ai_overview"]["present"] and a["ai_overview"]["site_cited"]))
    checks.append(("records top competitors separately", a["top_competitors"][0]["host"] == "other.com"))

    empty = analyze_serp([], D)
    checks.append(("empty SERP degrades cleanly", empty["best_organic_position"] is None and empty["local_pack"]["present"] is False))

    no_site = analyze_serp([{"type": "organic", "rank_absolute": 1, "url": "https://x.com/"}], D)
    checks.append(("absent site reports no organic presence", no_site["site_ranks_organically"] is False))

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    if "--verify" in sys.argv:
        _self_test()
    parser = argparse.ArgumentParser(description="One-shot SERP composition diagnostic (P0-b).")
    parser.add_argument("project")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(args.project, args.manifest, dry_run=args.dry_run)

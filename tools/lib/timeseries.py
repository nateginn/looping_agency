"""Derive a keyword-level SEO time series from the immutable run snapshots.

Design record: `PLAN-SEO-TIMESERIES.md` (7 drafts, 47 Codex findings; transcript in
`PLAN-REVIEW-LOG-SEO-TIMESERIES.md`). Read Part 1 before changing anything here - every
rule below exists because of a measured property of the real artifacts, and several of
them exist because an earlier draft of the plan got the measurement wrong.

Two rules dominate this module:

1. **Deduplicate by measurement, never by run.** `run_loop._build_snapshot()` carries
   every un-refreshed section forward verbatim, so 23 run directories hold only 9
   distinct GSC pulls. Keying anything by run id fabricates 13 observations that were
   never measured.
2. **Never emit a number the data cannot support.** The local-rank section is the
   governing case: batching (defect D4) means only the first target of each location was
   ever actually queried, so this module emits a *classification* and no rank at all.

Pure reads only - no writes, no network, no HTML.
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit

SECTION_KEYS = ("search_analytics", "local_rank", "backlinks", "technical_health")

# `gsc.py:77` sends this and never paginates; it is not recorded in run.json, so any
# truncation verdict derived from it is an assumption and is named as one.
ASSUMED_ROW_LIMIT = 1000
ASSUMED_ROW_LIMIT_SOURCE = "assumed-connector-default"

# Two pulls closer together than this are near-identical by construction, not a trend.
NEAR_DUPLICATE_HOURS = 24

DEFAULT_BRAND_TERMS = ("accelerated rehab", "accelerated rehab therapy")
DEFAULT_TOP_N = 25


# --------------------------------------------------------------------------- #
# normalisation
# --------------------------------------------------------------------------- #

def normalize_snapshot(doc):
    """Return a v2-shaped snapshot dict.

    Schema-v1 snapshots (2026-07-16 .. 07-21) store the bare `search_analytics` dict at
    the top level with no `schema_version` key. This mirrors `snapshot.write_snapshot`'s
    own fallback. It is load-bearing, not housekeeping: skipping it silently discards
    four real GSC pulls holding 0/177/245/245 rows - which is exactly the mistake Draft 1
    of the plan made (see F1).
    """
    if not isinstance(doc, dict):
        return {k: None for k in SECTION_KEYS}
    if "schema_version" in doc:
        return {k: doc.get(k) for k in SECTION_KEYS}
    out = {k: None for k in SECTION_KEYS}
    out["search_analytics"] = doc
    return out


def normalize_page(url):
    """Canonical page key: https scheme, lower-cased host, no `www.`, no port, no
    query/fragment, path preserved verbatim.

    Real case this exists for (F4): the top brand query is split across `http://` and
    `https://` variants of the same homepage (15 clicks and 3). Left unnormalised that
    is two series, one apparently losing three clicks to the other.
    """
    if not url:
        return url
    parts = urlsplit(url)
    if not parts.netloc:
        return url
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return f"https://{host}{parts.path}"


def registrable_host(url):
    """Lower-cased host with `www.` stripped. Not a public-suffix parse - it is used
    only to name a competitor in a panel, never to authorise anything."""
    if not url:
        return None
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _hours_between(later, earlier):
    a, b = _iso(later), _iso(earlier)
    if a is None or b is None:
        return None
    return (a - b).total_seconds() / 3600.0


# --------------------------------------------------------------------------- #
# local rank: batch reconstruction and classification
# --------------------------------------------------------------------------- #

def _local_rank_tool_args(run_json):
    for call in (run_json or {}).get("tool_calls") or []:
        if call.get("tool") == "dataforseo-local-rank":
            return call.get("args") or {}
    return None


def _replay_connector_grouping(args):
    """Reproduce `dataforseo.pull_local_rank`'s expansion, in its exact order.

    Not a flat comparison of the target list: legacy runs record targets with **no**
    `location` key plus a locations list, which the connector expands once per location
    (the 2026-07-24 run: 12 unpinned targets x 3 locations = 36 rows). Comparing the raw
    list would pass on a wrong reconstruction.

    Returns the expected [(location_name, keyword, page), ...] sequence, or None if the
    args cannot support a replay.
    """
    if not isinstance(args, dict):
        return None
    targets = args.get("targets")
    locations = args.get("locations")
    if not isinstance(targets, list) or not isinstance(locations, list):
        return None
    location_names = [loc.get("name") if isinstance(loc, dict) else loc for loc in locations]
    if any(not isinstance(n, str) for n in location_names):
        return None

    grouped = {}  # insertion-ordered, mirroring targets_by_location_name
    for target in targets:
        if not isinstance(target, dict):
            return None
        pinned = target.get("location")
        if pinned:
            if pinned not in location_names:
                return None
            grouped.setdefault(pinned, []).append(target)
        else:
            for name in location_names:
                grouped.setdefault(name, []).append(target)

    expected = []
    for name, group in grouped.items():
        for target in group:
            expected.append((name, target.get("keyword"), target.get("page")))
    return expected


def derive_batch_indexes(rows, run_json=None):
    """Per-row ordinal within its location's batch, or "unknown".

    The connector appends one location at a time, targets in order, so a row's ordinal
    within the contiguous run of rows sharing its `location_name` *is* its batch index.
    That inference drives a "never queried" verdict, so it is guarded twice: the
    `location_name` groups must be contiguous, and when the run's own `run.json` records
    the call, the replayed (location, keyword, page) sequence must match element by
    element. Any mismatch yields "unknown", and "unknown" is never "not-queried".
    """
    n = len(rows)
    if n == 0:
        return [], None

    indexes, seen_groups, current, ordinal = [], set(), object(), 0
    for row in rows:
        name = row.get("location_name")
        if name != current:
            if name in seen_groups:
                return ["unknown"] * n, "local_rank location groups are not contiguous"
            seen_groups.add(name)
            current, ordinal = name, 0
        indexes.append(ordinal)
        ordinal += 1

    expected = _replay_connector_grouping(_local_rank_tool_args(run_json))
    if expected is None:
        # No usable provenance. The contiguity-derived ordinal is still the connector's
        # real append order, so it is kept - but the caller is told it is unverified.
        return indexes, "no dataforseo-local-rank tool call in run.json to cross-validate batch order"
    actual = [(r.get("location_name"), r.get("keyword"), r.get("page")) for r in rows]
    if len(expected) != len(actual):
        return ["unknown"] * n, f"run.json replay expected {len(expected)} rows, snapshot has {len(actual)}"
    for i, (exp, act) in enumerate(zip(expected, actual)):
        if exp != act:
            return ["unknown"] * n, f"run.json replay disagrees with snapshot at row {i}: {exp!r} != {act!r}"
    return indexes, None


def _path_matches(target_path, url):
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


def classify_local_rank_row(row, domain, batch_index):
    """own | competitor | absent | not-queried | unknown.

    `own` requires host AND segment-boundary path match, mirroring the rule P0.1
    specifies for the connector fix. The historical rows carry `rank_absolute` matched by
    bare URL substring with no domain check (defects D1/D5), which is why no branch here
    ever returns a rank.
    """
    position = row.get("organic_rank_position")
    result_url = row.get("result_url")

    if batch_index == "unknown":
        return "unknown"
    if batch_index and batch_index > 0 and position is None:
        # D4: only the first task of each batched POST is executed by the live endpoint.
        return "not-queried"
    if position is None:
        return "absent"

    host = registrable_host(result_url)
    want_host = (domain or "").lower()
    if want_host.startswith("www."):
        want_host = want_host[4:]
    if host and want_host and host == want_host and _path_matches(row.get("page"), result_url):
        return "own"
    return "competitor"


# --------------------------------------------------------------------------- #
# GSC aggregation
# --------------------------------------------------------------------------- #

def _weighted_position(rows):
    num = den = 0.0
    for r in rows:
        pos, impr = r.get("position"), r.get("impressions") or 0
        if pos is not None and impr:
            num += pos * impr
            den += impr
    return (num / den) if den else None


def compute_totals(rows):
    """Site-wide headline numbers over ALL rows, unfiltered.

    Deliberately byte-identical arithmetic *and rounding* to MMC's
    `collector/sources/looping.py:_gsc_totals()` (ctr 4dp, avg_position 1dp), so the
    dashboard and the daily briefing can never disagree about a headline number. Check 13
    asserts the equality against the real 2026-08-11 snapshot.
    """
    clicks = sum(r.get("clicks") or 0 for r in rows)
    impressions = sum(r.get("impressions") or 0 for r in rows)
    pos = _weighted_position(rows)
    return {
        "clicks": clicks,
        "impressions": impressions,
        "ctr": round(clicks / impressions, 4) if impressions else None,
        "avg_position": round(pos, 1) if pos is not None else None,
    }


def is_brand(query, brand_terms):
    q = (query or "").lower()
    return any(term and term in q for term in brand_terms)


def merge_rows(raw_rows):
    """Collapse raw GSC rows onto the `(query, normalized_page)` entity.

    Clicks and impressions sum; position is impression-weighted. Returns rows sorted
    deterministically so the whole pipeline is reproducible.
    """
    buckets = {}
    for r in raw_rows or []:
        query = r.get("keyword")
        page = normalize_page(r.get("page"))
        buckets.setdefault((query, page), []).append(r)
    out = []
    for (query, page), group in buckets.items():
        pos = _weighted_position(group)
        out.append({
            "query": query,
            "page": page,
            "clicks": sum(g.get("clicks") or 0 for g in group),
            "impressions": sum(g.get("impressions") or 0 for g in group),
            "position": round(pos, 2) if pos is not None else None,
        })
    out.sort(key=lambda r: (-(r["impressions"] or 0), r["query"] or "", r["page"] or ""))
    return out


def rollup_by_query(merged_rows):
    """Query-level rollup across that query's pages, same arithmetic, plus `page_count`.

    A query-level position with `page_count > 1` is only meaningful when labelled "across
    N pages" - the renderer is responsible for saying so, this function for recording it.
    """
    buckets = {}
    for r in merged_rows:
        buckets.setdefault(r["query"], []).append(r)
    out = []
    for query, group in buckets.items():
        pos = _weighted_position(group)
        out.append({
            "query": query,
            "page_count": len(group),
            "pages": sorted(g["page"] for g in group if g["page"]),
            "clicks": sum(g["clicks"] for g in group),
            "impressions": sum(g["impressions"] for g in group),
            "position": round(pos, 2) if pos is not None else None,
        })
    out.sort(key=lambda r: (-(r["impressions"] or 0), r["query"] or ""))
    return out


def build_universe(gsc_observations, spec_targets, brand_terms, top_n=DEFAULT_TOP_N, rule="rolled-up"):
    """The charted keyword set: spec targets, union ever-clicked, union ever-top-N.

    `rule` selects the unit the click/top-N tests are evaluated over. "rolled-up" (the
    adopted rule) evaluates post-normalisation aggregated queries; "raw-row" evaluates
    raw `(query, page)` rows as they appear in the snapshot. The two differ by 11 members
    on the real data, which is why the rule is a parameter with both outputs asserted
    rather than an implicit choice.
    """
    members = set(spec_targets or [])
    for obs in gsc_observations:
        if rule == "raw-row":
            units = [{"query": r.get("keyword"), "clicks": r.get("clicks") or 0,
                      "impressions": r.get("impressions") or 0} for r in obs.get("raw_rows") or []]
        else:
            units = obs.get("rollup") or []
        for u in units:
            if (u.get("clicks") or 0) > 0:
                members.add(u["query"])
        for u in sorted(units, key=lambda x: -(x.get("impressions") or 0))[:top_n]:
            members.add(u["query"])
    members.discard(None)
    ordered = sorted(members)
    return {
        "rule": rule,
        "top_n": top_n,
        "members": ordered,
        "version": hashlib.sha256("\n".join(ordered).encode("utf-8")).hexdigest()[:12],
    }


# --------------------------------------------------------------------------- #
# extraction
# --------------------------------------------------------------------------- #

def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _dedupe_key(section, key):
    return (section or {}).get(key) if isinstance(section, dict) else None


def extract_observations(runs_dir, domain=None, brand_terms=None, spec_targets=None,
                         top_n=DEFAULT_TOP_N, priority_pages=None):
    """Walk every run directory once and emit five independently-keyed series.

    Run directories are *enumerated*, never counted from a constant - the numbers in the
    plan are dated measurements to assert against, not values the code depends on.
    """
    brand_terms = tuple(t.lower() for t in (brand_terms or DEFAULT_BRAND_TERMS))
    priority_pages = [normalize_page(p) for p in (priority_pages or [])]
    warnings = []
    series = {"gsc": [], "local_rank": [], "backlinks": [], "pagespeed": [], "indexation": []}
    seen = {k: set() for k in series}

    if not os.path.isdir(runs_dir):
        return {"series": series, "warnings": [f"runs directory not found: {runs_dir}"],
                "universe": build_universe([], spec_targets, brand_terms, top_n),
                "run_dirs": 0, "snapshot_bearing": 0}

    run_names = sorted(d for d in os.listdir(runs_dir) if os.path.isdir(os.path.join(runs_dir, d)))
    snapshot_bearing = 0

    for run_id in run_names:
        snap_path = os.path.join(runs_dir, run_id, "snapshot.json")
        if not os.path.exists(snap_path):
            # Normal: a partial-failure run writes no snapshot. Not a warning.
            continue
        snapshot_bearing += 1
        try:
            snapshot = normalize_snapshot(_load_json(snap_path))
        except (OSError, json.JSONDecodeError, ValueError) as e:
            warnings.append(f"{run_id}: unreadable snapshot.json ({type(e).__name__}) - skipped")
            continue

        run_json = None
        run_path = os.path.join(runs_dir, run_id, "run.json")
        if os.path.exists(run_path):
            try:
                run_json = _load_json(run_path)
            except (OSError, json.JSONDecodeError, ValueError) as e:
                warnings.append(f"{run_id}: unreadable run.json ({type(e).__name__}) - window unknown")

        _extract_gsc(series, seen, warnings, run_id, snapshot, run_json, brand_terms, priority_pages)
        _extract_local_rank(series, seen, warnings, run_id, snapshot, run_json, domain)
        _extract_backlinks(series, seen, run_id, snapshot)
        _extract_technical(series, seen, run_id, snapshot)

    for name in series:
        series[name].sort(key=lambda o: o["observed_at"])

    _annotate_near_duplicates(series["gsc"])

    return {
        "series": series,
        "warnings": warnings,
        "universe": build_universe(series["gsc"], spec_targets, brand_terms, top_n),
        "run_dirs": len(run_names),
        "snapshot_bearing": snapshot_bearing,
    }


def _gsc_window(run_json):
    """(start, end) from run.json's snake_case tool-call args - NOT the API's camelCase.

    Both a missing run.json and a run.json with no `gsc` call yield (None, None); the
    caller marks the point window-unknown rather than guessing a window.
    """
    for call in (run_json or {}).get("tool_calls") or []:
        if call.get("tool") == "gsc":
            args = call.get("args") or {}
            return args.get("start_date"), args.get("end_date")
    return None, None


def _extract_gsc(series, seen, warnings, run_id, snapshot, run_json, brand_terms, priority_pages):
    section = snapshot.get("search_analytics")
    pulled_at = _dedupe_key(section, "pulled_at")
    if not pulled_at or pulled_at in seen["gsc"]:
        return
    raw_rows = section.get("keywords") or []
    if not raw_rows:
        # The 2026-07-16 pull returned zero rows. A real observation of nothing is not a
        # data point; plotting it would draw a zero-click floor that never happened.
        seen["gsc"].add(pulled_at)
        return
    seen["gsc"].add(pulled_at)

    start, end = _gsc_window(run_json)
    if start is None and end is None:
        warnings.append(f"{run_id}: no gsc tool call in run.json - GSC window unknown for {pulled_at}")

    merged = merge_rows(raw_rows)
    rollup = rollup_by_query(merged)
    brand_rows = [r for r in merged if is_brand(r["query"], brand_terms)]
    nonbrand_rows = [r for r in merged if not is_brand(r["query"], brand_terms)]
    row_count = len(raw_rows)

    by_page = {}
    for r in merged:
        if r["page"] in priority_pages:
            by_page.setdefault(r["page"], []).append(r)

    series["gsc"].append({
        "observed_at": pulled_at,
        "first_seen_run_id": run_id,
        "window_start": start,
        "window_end": end,
        "window_known": bool(start and end),
        "row_count": row_count,
        "row_limit": ASSUMED_ROW_LIMIT,
        "row_limit_source": ASSUMED_ROW_LIMIT_SOURCE,
        "truncation_suspected": row_count >= ASSUMED_ROW_LIMIT,
        "totals": compute_totals(merged),
        "brand": compute_totals(brand_rows),
        "nonbrand": compute_totals(nonbrand_rows),
        "unique_queries": len(rollup),
        "multi_page_queries_raw": _multi_page_count(raw_rows, normalize=False),
        "multi_page_queries": _multi_page_count(raw_rows, normalize=True),
        "priority_pages": {p: compute_totals(rows) for p, rows in sorted(by_page.items())},
        "rollup": rollup,
        "rows": merged,
        "raw_rows": raw_rows,
    })


def _multi_page_count(raw_rows, normalize):
    pages = {}
    for r in raw_rows:
        page = normalize_page(r.get("page")) if normalize else r.get("page")
        pages.setdefault(r.get("keyword"), set()).add(page)
    return sum(1 for v in pages.values() if len(v) > 1)


def _extract_local_rank(series, seen, warnings, run_id, snapshot, run_json, domain):
    section = snapshot.get("local_rank")
    as_of = _dedupe_key(section, "as_of")
    if not as_of or as_of in seen["local_rank"]:
        return
    seen["local_rank"].add(as_of)
    rows = section.get("rows") or []
    indexes, note = derive_batch_indexes(rows, run_json)
    if note:
        warnings.append(f"{run_id}: {note}")

    out_rows, invariant_broken = [], False
    for row, idx in zip(rows, indexes):
        classification = classify_local_rank_row(row, domain, idx)
        if idx != "unknown" and idx > 0 and row.get("organic_rank_position") is not None:
            invariant_broken = True
        out_rows.append({
            "location": row.get("location_name"),
            "keyword": row.get("keyword"),
            "page": row.get("page"),
            "batch_index": idx,
            "classification": classification,
            "competitor_domain": registrable_host(row.get("result_url")) if classification == "competitor" else None,
            "result_url": row.get("result_url"),
            # Quarantined by name: rank_absolute counts every SERP feature and was matched
            # by bare URL substring. It is never rendered - see check 27c.
            "forensic": {"raw_rank_absolute_not_a_rank": row.get("organic_rank_position")},
        })
    if invariant_broken:
        warnings.append(
            f"{run_id}: a non-first batch position returned a result - the D4 batching invariant "
            "no longer holds, so 'not-queried' can no longer be inferred from batch order"
        )

    series["local_rank"].append({
        "observed_at": as_of,
        "first_seen_run_id": run_id,
        "row_count": len(out_rows),
        "batch_invariant_holds": not invariant_broken,
        "rows": out_rows,
    })


def _extract_backlinks(series, seen, run_id, snapshot):
    section = snapshot.get("backlinks")
    as_of = _dedupe_key(section, "as_of")
    if not as_of or as_of in seen["backlinks"]:
        return
    seen["backlinks"].add(as_of)
    summary = section.get("summary") or {}
    series["backlinks"].append({
        "observed_at": as_of,
        "first_seen_run_id": run_id,
        "referring_domains": summary.get("referring_domains"),
        "backlinks": summary.get("backlinks"),
    })


def _extract_technical(series, seen, run_id, snapshot):
    """pagespeed and indexation refresh independently and carry separate stamps, so they
    are separate series. Collapsing them into one `as_of` would present stale data as
    fresh."""
    section = snapshot.get("technical_health")
    if not isinstance(section, dict):
        return

    ps_as_of = section.get("pagespeed_as_of")
    if ps_as_of and ps_as_of not in seen["pagespeed"]:
        seen["pagespeed"].add(ps_as_of)
        series["pagespeed"].append({
            "observed_at": ps_as_of,
            "first_seen_run_id": run_id,
            "rows": [{"page": normalize_page(r.get("page")),
                      "performance_score": r.get("performance_score"),
                      "cwv_status": r.get("cwv_status")}
                     for r in section.get("pagespeed_rows") or []],
        })

    ix_as_of = section.get("indexation_as_of")
    if ix_as_of and ix_as_of not in seen["indexation"]:
        seen["indexation"].add(ix_as_of)
        series["indexation"].append({
            "observed_at": ix_as_of,
            "first_seen_run_id": run_id,
            "rows": [{"page": normalize_page(r.get("page")), "verdict": r.get("verdict")}
                     for r in section.get("indexation_rows") or []],
            "sitemaps": [{"path": s.get("path"), "warnings": s.get("warnings"),
                          "errors": s.get("errors"), "is_pending": s.get("is_pending")}
                         for s in section.get("sitemaps") or []],
        })


def _annotate_near_duplicates(gsc):
    """Two pulls under 24h apart are near-identical by construction (GSC's own window
    barely moved). They are labelled and excluded from delta arithmetic so they do not
    read as a flat stretch followed by a jump."""
    for i, obs in enumerate(gsc):
        if i == 0:
            obs["hours_since_previous"] = None
            obs["near_duplicate"] = False
            continue
        hours = _hours_between(obs["observed_at"], gsc[i - 1]["observed_at"])
        obs["hours_since_previous"] = round(hours, 2) if hours is not None else None
        obs["near_duplicate"] = hours is not None and hours < NEAR_DUPLICATE_HOURS


def previous_comparable(observations, index):
    """The most recent earlier observation that is not a near-duplicate re-pull.

    Deltas are computed against this, never against the immediately preceding element -
    two pulls nine minutes apart have no meaningful delta between them.
    """
    for j in range(index - 1, -1, -1):
        if not observations[j].get("near_duplicate"):
            return observations[j]
    return None


# --------------------------------------------------------------------------- #
# self-test (PLAN-SEO-TIMESERIES.md Part 5, checks 1-19)
# --------------------------------------------------------------------------- #

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REAL_RUNS = os.path.join(WORKSPACE_ROOT, "projects", "art", "loops", "seo", "runs")
REAL_DOMAIN = "acceleratedrehabtherapy.com"
REAL_TARGETS = [
    "physical therapy greeley", "physical therapy denver", "chiropractor greeley",
    "chiropractor denver", "auto injury treatment greeley", "auto injury treatment denver",
    "work comp injury care greeley", "work comp injury care denver",
    "massage therapy greeley", "massage therapy denver", "acupuncture greeley",
    "acupuncture denver",
]


def _write_run(root, run_id, snapshot, run_json=None):
    d = os.path.join(root, run_id)
    os.makedirs(d, exist_ok=True)
    if snapshot is not None:
        with open(os.path.join(d, "snapshot.json"), "w", encoding="utf-8") as fh:
            if isinstance(snapshot, str):
                fh.write(snapshot)
            else:
                json.dump(snapshot, fh)
    if run_json is not None:
        with open(os.path.join(d, "run.json"), "w", encoding="utf-8") as fh:
            json.dump(run_json, fh)
    return d


def _v2(pulled_at, rows, local_rank=None):
    return {
        "schema_version": 2,
        "search_analytics": {"pulled_at": pulled_at, "sample_size": sum(r.get("impressions") or 0 for r in rows), "keywords": rows},
        "local_rank": local_rank,
        "backlinks": None,
        "technical_health": None,
    }


def _row(kw, page, clicks=0, impressions=1, position=5.0):
    return {"keyword": kw, "page": page, "clicks": clicks, "impressions": impressions, "position": position}


def _gsc_run_json(start="2026-07-15", end="2026-08-11"):
    return {"tool_calls": [{"tool": "gsc", "args": {"site_url": "sc-domain:x", "start_date": start, "end_date": end}}]}


def _self_test():  # noqa: C901 - a flat checklist reads better than nested helpers here
    import shutil
    import sys
    import tempfile

    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    tmp = tempfile.mkdtemp(prefix="timeseries-test-")
    try:
        # --- 1: N runs sharing 9 distinct pulled_at -> exactly 9 observations ---------
        root = os.path.join(tmp, "c1")
        stamps = [f"2026-07-{d:02d}T00:00:00.000000Z" for d in range(1, 10)]
        run_n = 0
        for stamp in stamps:
            for _ in range(3):  # each pull is carried forward into two later runs
                run_n += 1
                _write_run(root, f"2026-07-{run_n:02d}T00-00-00-000Z-r{run_n:02d}",
                           _v2(stamp, [_row("k", "https://x/", impressions=10)]), _gsc_run_json())
        res = extract_observations(root)
        check("1  22+ runs sharing 9 distinct pulled_at -> 9 GSC observations",
              len(res["series"]["gsc"]) == 9, f"got {len(res['series']['gsc'])} from {run_n} runs")

        # --- 2: carried-forward never produces a second observation ------------------
        root = os.path.join(tmp, "c2")
        for i in range(10):
            _write_run(root, f"2026-07-{i + 1:02d}T00-00-00-000Z-x{i}",
                       _v2("2026-07-01T00:00:00.000000Z", [_row("k", "https://x/", impressions=10)]), _gsc_run_json())
        res = extract_observations(root)
        check("2  a carried-forward section across 10 runs -> 1 observation",
              len(res["series"]["gsc"]) == 1, f"got {len(res['series']['gsc'])}")

        # --- 3: v1 bare dict normalises AND is kept, with its rows intact -------------
        v1 = {"source": "gsc", "pulled_at": "2026-07-18T22:12:27.024357Z", "sample_size": 451,
              "keywords": [_row(f"q{i}", "https://x/", impressions=3) for i in range(177)]}
        norm = normalize_snapshot(v1)
        root = os.path.join(tmp, "c3")
        _write_run(root, "2026-07-18T22-12-26-323Z-b8xtu9", v1, _gsc_run_json())
        res = extract_observations(root)
        got_rows = res["series"]["gsc"][0]["row_count"] if res["series"]["gsc"] else 0
        check("3  schema-v1 bare-dict snapshot normalises and is KEPT with all 177 rows",
              norm["search_analytics"] is v1 and len(res["series"]["gsc"]) == 1 and got_rows == 177,
              f"observations={len(res['series']['gsc'])} row_count={got_rows}")

        # --- 4: zero-row observation dropped, non-empty kept -------------------------
        root = os.path.join(tmp, "c4")
        _write_run(root, "2026-07-16T21-24-31-879Z-pc50bg",
                   {"source": "gsc", "pulled_at": "2026-07-16T21:24:32.680890Z", "sample_size": 0, "keywords": []},
                   _gsc_run_json())
        _write_run(root, "2026-07-18T22-12-26-323Z-b8xtu9", v1, _gsc_run_json())
        res = extract_observations(root)
        check("4  a zero-row pull is dropped; a 177-row pull is not",
              len(res["series"]["gsc"]) == 1 and res["series"]["gsc"][0]["row_count"] == 177,
              f"got {len(res['series']['gsc'])}")

        # --- 5: missing snapshot.json is skipped, not fatal ---------------------------
        root = os.path.join(tmp, "c5")
        _write_run(root, "2026-08-10T12-00-01-321Z-2qbkux", None, {"status": "partial-failure"})
        _write_run(root, "2026-08-11T05-18-25-362Z-u5z1dd",
                   _v2("2026-08-11T05:18:26.348651Z", [_row("k", "https://x/", impressions=5)]), _gsc_run_json())
        res = extract_observations(root)
        check("5  a run directory with no snapshot.json is skipped without raising",
              len(res["series"]["gsc"]) == 1 and res["run_dirs"] == 2 and res["snapshot_bearing"] == 1,
              f"dirs={res['run_dirs']} snapshots={res['snapshot_bearing']}")

        # --- 6: corrupt snapshot skipped AND warned -----------------------------------
        root = os.path.join(tmp, "c6")
        _write_run(root, "2026-08-01T00-00-00-000Z-bad", "{not json at all", _gsc_run_json())
        res = extract_observations(root)
        check("6  a corrupt snapshot.json is skipped and recorded in warnings",
              not res["series"]["gsc"] and any("unreadable snapshot" in w for w in res["warnings"]),
              f"warnings={res['warnings']}")

        # --- 7 / 7b: window provenance -------------------------------------------------
        root = os.path.join(tmp, "c7")
        _write_run(root, "2026-08-01T00-00-00-000Z-nojson",
                   _v2("2026-08-01T00:00:00Z", [_row("k", "https://x/")]), None)
        res = extract_observations(root)
        o = res["series"]["gsc"][0]
        check("7  missing run.json -> window null, point kept, marked window-unknown",
              o["window_start"] is None and o["window_end"] is None and o["window_known"] is False)

        root = os.path.join(tmp, "c7b")
        _write_run(root, "2026-08-01T00-00-00-000Z-nogsc",
                   _v2("2026-08-01T00:00:00Z", [_row("k", "https://x/")]),
                   {"tool_calls": [{"tool": "pagespeed", "args": {}}]})
        res = extract_observations(root)
        o = res["series"]["gsc"][0]
        check("7b run.json present but no gsc tool call -> window null + warning",
              o["window_known"] is False and any("window unknown" in w for w in res["warnings"]),
              f"warnings={res['warnings']}")

        root = os.path.join(tmp, "c7c")
        _write_run(root, "2026-08-01T00-00-00-000Z-ok",
                   _v2("2026-08-01T00:00:00Z", [_row("k", "https://x/")]), _gsc_run_json("2026-07-15", "2026-08-11"))
        o = extract_observations(root)["series"]["gsc"][0]
        check("7c a present gsc call is read from snake_case start_date/end_date",
              o["window_start"] == "2026-07-15" and o["window_end"] == "2026-08-11",
              f"{o['window_start']}..{o['window_end']}")

        # --- 8 / 9: row_count provenance and truncation naming -------------------------
        root = os.path.join(tmp, "c9")
        rows = [_row(f"q{i}", f"https://x/{i}", impressions=2) for i in range(ASSUMED_ROW_LIMIT)]
        _write_run(root, "2026-08-01T00-00-00-000Z-full", _v2("2026-08-01T00:00:00Z", rows), _gsc_run_json())
        o = extract_observations(root)["series"]["gsc"][0]
        check("9  row_count >= row_limit -> truncation_suspected, with its source recorded",
              o["truncation_suspected"] is True and o["row_limit_source"] == ASSUMED_ROW_LIMIT_SOURCE
              and "truncated" not in o,
              f"keys={sorted(k for k in o if 'trunc' in k)}")

        # --- 10: page normalisation on the real http/https homepage case ---------------
        merged = merge_rows([
            {"keyword": "accelerated rehab therapy", "page": "https://acceleratedrehabtherapy.com/",
             "clicks": 15, "impressions": 47, "position": 1.0212765957446808},
            {"keyword": "accelerated rehab therapy", "page": "http://acceleratedrehabtherapy.com/",
             "clicks": 3, "impressions": 8, "position": 2},
        ])
        check("10 http:// and https:// variants of one page merge to 18 clicks / 55 impressions",
              len(merged) == 1 and merged[0]["clicks"] == 18 and merged[0]["impressions"] == 55
              and merged[0]["position"] == round((1.0212765957446808 * 47 + 2 * 8) / 55, 2),
              f"{merged}")

        # --- 12: an absent query is null, never zero -----------------------------------
        obs_rows = merge_rows([_row("present", "https://x/", clicks=1, impressions=2)])
        lookup = {r["query"]: r for r in obs_rows}
        check("12 a query absent from an observation yields no row (null), never a 0 row",
              "missing" not in lookup and lookup["present"]["clicks"] == 1)

        # --- 14: brand/non-brand split is exact ----------------------------------------
        sample = merge_rows([
            _row("accelerated rehab therapy", "https://x/", clicks=15, impressions=47, position=1.0),
            _row("chiropractor near me", "https://x/", clicks=0, impressions=476, position=1.2),
            _row("dry needling near me", "https://x/", clicks=1, impressions=4, position=1.5),
        ])
        total = compute_totals(sample)
        brand = compute_totals([r for r in sample if is_brand(r["query"], DEFAULT_BRAND_TERMS)])
        nonbrand = compute_totals([r for r in sample if not is_brand(r["query"], DEFAULT_BRAND_TERMS)])
        check("14 brand + non-brand sums back to the unsplit total exactly",
              brand["clicks"] + nonbrand["clicks"] == total["clicks"]
              and brand["impressions"] + nonbrand["impressions"] == total["impressions"],
              f"{brand['clicks']}+{nonbrand['clicks']} vs {total['clicks']}")

        # --- 15: classification fixtures (P0.1's own) ----------------------------------
        def cls(page, url, pos, idx=0):
            return classify_local_rank_row(
                {"page": page, "result_url": url, "organic_rank_position": pos}, REAL_DOMAIN, idx)

        fixtures = [
            ("occ-ortho.com competitor", cls("/physical-therapy/", "https://occ-ortho.com/physical-therapy/", 6), "competitor"),
            ("mgmc.org competitor", cls("/physical-therapy/", "https://www.mgmc.org/care/rehab-and-therapy/physical-therapy/", 33), "competitor"),
            ("own URL", cls("/physical-therapy/", "https://acceleratedrehabtherapy.com/physical-therapy/", 37), "own"),
            ("own www. variant", cls("/physical-therapy/", "https://www.acceleratedrehabtherapy.com/physical-therapy/", 37), "own"),
            ("/massage/ vs /massage-therapy-guide/", cls("/massage/", "https://acceleratedrehabtherapy.com/massage-therapy-guide/", 4), "competitor"),
            ("null position", cls("/chiropractor/", None, None), "absent"),
            ("batch_index > 0", cls("/chiropractor/", None, None, idx=3), "not-queried"),
            ("unknown batch index", cls("/chiropractor/", None, None, idx="unknown"), "unknown"),
        ]
        bad = [f"{n}: got {g}, want {w}" for n, g, w in fixtures if g != w]
        check("15 local-rank classification matches every P0.1 fixture", not bad, "; ".join(bad))

        # --- 16b: batch-index provenance guards ----------------------------------------
        noncontig = [
            {"location_name": "Greeley", "keyword": "a", "page": "/a/"},
            {"location_name": "Denver", "keyword": "b", "page": "/b/"},
            {"location_name": "Greeley", "keyword": "c", "page": "/c/"},
        ]
        idxs, note = derive_batch_indexes(noncontig, None)
        contiguity_ok = all(i == "unknown" for i in idxs) and "not contiguous" in (note or "")

        rows_ok = [{"location_name": "Greeley", "keyword": "a", "page": "/a/"},
                   {"location_name": "Greeley", "keyword": "b", "page": "/b/"}]
        disagree = {"tool_calls": [{"tool": "dataforseo-local-rank", "args": {
            "locations": ["Greeley"],
            "targets": [{"keyword": "b", "page": "/b/", "location": "Greeley"},
                        {"keyword": "a", "page": "/a/", "location": "Greeley"}]}}]}
        idxs2, note2 = derive_batch_indexes(rows_ok, disagree)
        order_ok = all(i == "unknown" for i in idxs2) and "disagrees" in (note2 or "")

        loc_disagree = {"tool_calls": [{"tool": "dataforseo-local-rank", "args": {
            "locations": ["Denver"],
            "targets": [{"keyword": "a", "page": "/a/", "location": "Denver"},
                        {"keyword": "b", "page": "/b/", "location": "Denver"}]}}]}
        idxs3, _ = derive_batch_indexes(rows_ok, loc_disagree)
        loc_ok = all(i == "unknown" for i in idxs3)

        unknown_never_notqueried = classify_local_rank_row(
            {"page": "/x/", "result_url": None, "organic_rank_position": None}, REAL_DOMAIN, "unknown") == "unknown"
        check("16b batch-index provenance: non-contiguity, target-order and location-order all -> unknown, "
              "and unknown is never not-queried",
              contiguity_ok and order_ok and loc_ok and unknown_never_notqueried,
              f"contig={contiguity_ok} order={order_ok} loc={loc_ok} unknown={unknown_never_notqueried}")

        # legacy unpinned expansion must be replayed, not compared flat
        legacy_args = {"locations": ["Greeley", "Denver"],
                       "targets": [{"keyword": "a", "page": "/a/"}, {"keyword": "b", "page": "/b/"}]}
        expected = _replay_connector_grouping(legacy_args)
        check("16c legacy unpinned targets are expanded once per location, in order",
              expected == [("Greeley", "a", "/a/"), ("Greeley", "b", "/b/"),
                           ("Denver", "a", "/a/"), ("Denver", "b", "/b/")], f"{expected}")

        # --- 18: differing local_rank row counts coexist -------------------------------
        root = os.path.join(tmp, "c18")
        lr36 = {"as_of": "2026-07-26T05:42:02.980004Z",
                "rows": [{"location_name": loc, "keyword": f"k{i}", "page": f"/p{i}/",
                          "organic_rank_position": None, "result_url": None}
                         for loc in ("Greeley", "Denver", "UNC Campus") for i in range(12)]}
        lr12 = {"as_of": "2026-07-26T05:58:58.751365Z",
                "rows": [{"location_name": loc, "keyword": f"k{i}", "page": f"/p{i}/",
                          "organic_rank_position": None, "result_url": None}
                         for loc in ("Greeley", "Denver") for i in range(6)]}
        _write_run(root, "2026-07-26T05-41-22-953Z-avoeea", _v2("2026-07-26T05:41:00Z", [_row("k", "https://x/")], lr36), _gsc_run_json())
        _write_run(root, "2026-07-26T05-58-26-541Z-veqfkb", _v2("2026-07-26T05:58:00Z", [_row("k", "https://x/")], lr12), _gsc_run_json())
        res = extract_observations(root, domain=REAL_DOMAIN)
        counts = [o["row_count"] for o in res["series"]["local_rank"]]
        check("18 a 36-row and a 12-row local_rank observation coexist without inventing 24 nulls",
              counts == [36, 12], f"{counts}")

        # --- 19 (synthetic half): universe versioning -----------------------------------
        base = [{"rollup": [{"query": "a", "clicks": 1, "impressions": 10},
                            {"query": "b", "clicks": 0, "impressions": 5}], "raw_rows": []}]
        u1 = build_universe(base, ["t1"], DEFAULT_BRAND_TERMS, top_n=1)
        u1b = build_universe(base, ["t1"], DEFAULT_BRAND_TERMS, top_n=1)
        grown = base + [{"rollup": [{"query": "c", "clicks": 3, "impressions": 1}], "raw_rows": []}]
        u2 = build_universe(grown, ["t1"], DEFAULT_BRAND_TERMS, top_n=1)
        check("19a universe is stable across a rebuild and its version changes when a member is added",
              u1["version"] == u1b["version"] and u2["version"] != u1["version"]
              and set(u2["members"]) - set(u1["members"]) == {"c"},
              f"{u1['version']} {u2['version']}")

        # ================= real-artifact checks ==========================================
        if not os.path.isdir(REAL_RUNS):
            check("REAL-DATA CHECKS", False, f"{REAL_RUNS} not found - checks 8/11/13/16/17/19b cannot run")
        else:
            real = extract_observations(REAL_RUNS, domain=REAL_DOMAIN, brand_terms=DEFAULT_BRAND_TERMS,
                                        spec_targets=REAL_TARGETS)
            gsc = real["series"]["gsc"]
            latest = gsc[-1]

            check("1b real artifacts: 23 run dirs / 22 snapshot-bearing -> 9 GSC observations, 8 usable",
                  real["run_dirs"] == 23 and real["snapshot_bearing"] == 22 and len(gsc) == 8,
                  f"dirs={real['run_dirs']} snaps={real['snapshot_bearing']} obs={len(gsc)} (1 zero-row pull dropped)")

            raw_snapshot = normalize_snapshot(_load_json(
                os.path.join(REAL_RUNS, "2026-08-11T05-18-25-362Z-u5z1dd", "snapshot.json")))
            sa = raw_snapshot["search_analytics"]
            check("8  row_count is len(keywords), and sample_size is total impressions (not row count)",
                  latest["row_count"] == len(sa["keywords"]) == 910
                  and sa["sample_size"] == latest["totals"]["impressions"] == 10308,
                  f"row_count={latest['row_count']} sample_size={sa['sample_size']}")

            conserved = (sum(r["clicks"] for r in latest["rows"]) == sum((r.get("clicks") or 0) for r in sa["keywords"])
                         and sum(r["impressions"] for r in latest["rows"]) == sum((r.get("impressions") or 0) for r in sa["keywords"]))
            check("11 multi-page queries: 129 pre-normalisation, 69 post; clicks/impressions conserved",
                  latest["multi_page_queries_raw"] == 129 and latest["multi_page_queries"] == 69 and conserved,
                  f"raw={latest['multi_page_queries_raw']} norm={latest['multi_page_queries']} conserved={conserved}")

            t = latest["totals"]
            check("13 totals match MMC's _gsc_totals() exactly, including its rounding",
                  (t["clicks"], t["impressions"], t["ctr"], t["avg_position"]) == (23, 10308, 0.0022, 24.2),
                  f"{t}")

            lr = real["series"]["local_rank"]
            nonnull = [(o["observed_at"], r) for o in lr for r in o["rows"]
                       if r["forensic"]["raw_rank_absolute_not_a_rank"] is not None]
            all_first = all(r["batch_index"] == 0 for _, r in nonnull)
            check("16 F6 invariant: every non-null local-rank result in all history is batch index 0",
                  len(lr) == 16 and len(nonnull) == 31 and all_first,
                  f"{len(nonnull)} non-null across {len(lr)} observations; all_index_0={all_first}. "
                  "IF THIS FAILS the connector changed and 'not-queried' can no longer be inferred from batch order")

            leaked = [k for o in lr for r in o["rows"] for k in r if k == "position"]
            outside = [r for o in lr for r in o["rows"]
                       if any(isinstance(v, (int, float)) and k != "batch_index" for k, v in r.items())]
            check("17 no 'position' field and no rank number outside forensic in the local_rank series",
                  not leaked and not outside, f"position_keys={len(leaked)} numeric_outside_forensic={len(outside)}")

            classifications = {}
            for o in lr:
                for r in o["rows"]:
                    classifications[r["classification"]] = classifications.get(r["classification"], 0) + 1
            check("16d the integrity panel's own numbers are derivable",
                  classifications.get("not-queried", 0) > 0 and classifications.get("competitor", 0) > 0,
                  f"{classifications}")

            u_rolled = build_universe(gsc, REAL_TARGETS, DEFAULT_BRAND_TERMS, rule="rolled-up")
            u_raw = build_universe(gsc, REAL_TARGETS, DEFAULT_BRAND_TERMS, rule="raw-row")
            check("19b universe on real data: 61 rolled-up, 50 raw-row, rolled-up a strict superset",
                  len(u_rolled["members"]) == 61 and len(u_raw["members"]) == 50
                  and set(u_raw["members"]) <= set(u_rolled["members"]),
                  f"rolled-up={len(u_rolled['members'])} raw-row={len(u_raw['members'])}")

            # The rule is asserted by its *property*, not by a hardcoded count: a
            # near-duplicate must be a repeat reading. On the real history this catches
            # three, not the two minute-apart pairs alone - 2026-08-04T03:05:49 sits
            # 12.5h after its predecessor and returns identical totals. That third one is
            # why the threshold is 24h and not "same minute".
            dupes = [o for o in gsc if o.get("near_duplicate")]
            def _t(o):
                return (o["totals"]["clicks"], o["totals"]["impressions"], o["totals"]["avg_position"])
            dup_repeats = all(_t(o) == _t(gsc[i - 1]) for i, o in enumerate(gsc) if o.get("near_duplicate"))
            nondup_moves = all(_t(o) != _t(gsc[i - 1])
                               for i, o in enumerate(gsc) if i > 0 and not o.get("near_duplicate"))
            check("F2 every near-duplicate pull (<24h) repeats its predecessor's totals, and no "
                  "non-duplicate does - 3 of 8 real observations are repeat readings",
                  len(dupes) == 3 and dup_repeats and nondup_moves,
                  f"flagged={[o['observed_at'][:19] for o in dupes]} repeats={dup_repeats} moves={nondup_moves}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = 0
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}" + (f"   [{detail}]" if detail and not ok else ""))
        if not ok:
            failed += 1
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    import sys
    if "--verify" in sys.argv:
        _self_test()

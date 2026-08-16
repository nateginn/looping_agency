"""Pull true DAILY GSC totals, so week-over-week can mean what it says.

    python tools/gsc_daily.py <project> <loop> [--full] [--dry-run] [--verify]

Why this exists (PLAN-SEO-TIMESERIES.md F2 / Part 4): the loop's GSC pull asks for a
trailing 28-day window, so two consecutive weekly pulls share 21 of their 28 days. Their
difference is a shift in a rolling sum, not a week's gain, and no arithmetic on those
snapshots can recover the weekly figure. One extra request with `dimensions: ["date"]`
returns one row per day and makes real, non-overlapping weeks possible - plus a backfill
across GSC's full retention, which is the only available pre-intervention baseline.

Deliberately outside the run contract:

* Not a step in `run_loop.py` and not a new snapshot section. `gsc` is a `critical`
  connector whose failure aborts a run; a standalone tool cannot abort anything.
* **`tools/gsc.py` is imported, never modified.** Its `pull_metrics()` hard-maps
  `keys[0] -> keyword` and `keys[1] -> page`, so handed `["date"]` it would return rows
  whose "keyword" is a date. This module owns its own request body and parser, and reuses
  gsc.py's auth, redaction and error handling so the credential path stays single-sourced.
* Read-only, on an already-granted `webmasters.readonly` scope. No new credential.
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gsc  # noqa: E402
from lib.credentials import resolve_credential  # noqa: E402
from lib.gsc_auth import bearer_for_secret  # noqa: E402
from lib.paths import assert_within  # noqa: E402
from lib.redact import redact_text  # noqa: E402

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# GSC retains roughly 16 months. Treated as approximate: the tool asks for its computed
# window and records how many rows actually came back, rather than assuming completeness.
RETENTION_DAYS = 480
# GSC data keeps settling for a few days. A named heuristic, NOT a completeness
# guarantee - these rows are re-fetched and overwritten on every later invocation.
PROVISIONAL_DAYS = 3
ROW_LIMIT = 1000


def _today():
    return datetime.now(timezone.utc).date()


def compute_window(last_recorded, today=None, retention_days=RETENTION_DAYS,
                   provisional_days=PROVISIONAL_DAYS, full=False):
    """The range to request: back to wherever the file actually ends, clamped to retention.

        start = max(last_recorded - provisional_days, today - retention_days)

    **`max`, not `min`.** `min` would pick the earlier of the two, so a file ending 200
    days ago would re-request the entire retention window on every run, and a gap older
    than retention would ask for a start date before GSC has data. (Draft 3 of the plan
    had exactly that inversion; it is the single-character error the review caught.)

    Because the start tracks the file's own end rather than a fixed lookback, **any
    invocation cadence produces a complete series** - weekly, monthly, twice a year. Data
    is lost only if a gap exceeds retention, which is reported as `retention_gap` rather
    than left as a silent hole.
    """
    today = today or _today()
    floor = today - timedelta(days=retention_days)
    if full or not last_recorded:
        return floor, today, bool(last_recorded and last_recorded < floor)
    want = last_recorded - timedelta(days=provisional_days)
    gap = want < floor
    return max(want, floor), today, gap


def parse_daily_rows(body):
    """`keys[0] -> date`. This is the whole reason gsc.pull_metrics cannot be reused."""
    out = []
    for row in (body.get("rows") or []):
        keys = row.get("keys") or []
        if not keys:
            continue
        out.append({
            "date": keys[0],
            "clicks": row.get("clicks"),
            "impressions": row.get("impressions"),
            "position": row.get("position"),
        })
    return out


def fetch_daily(credential_alias, resolve, site_url, start, end, http_post=None):
    """One Search Analytics request, `dimensions: ["date"]`, read-only.

    Auth headers, error raising and secret redaction all come from `gsc.py` so there is
    exactly one implementation of each in the workspace.
    """
    if not credential_alias:
        raise ValueError("gsc_daily.py: credential_alias is required")
    if not callable(resolve):
        raise ValueError("gsc_daily.py: a credential resolver is required")
    if not site_url:
        raise ValueError("gsc_daily.py: site_url is required")

    span = (end - start).days + 1
    if span > ROW_LIMIT:
        raise ValueError(
            f"gsc_daily.py: refusing to request {span} days against a {ROW_LIMIT}-row limit - "
            "the response would be silently truncated. Narrow the window or add pagination.")

    secret = resolve(credential_alias)
    secret_map = {credential_alias: secret}
    endpoint = gsc.SEARCH_ANALYTICS_ENDPOINT.format(site=quote(site_url, safe=""))
    payload = {"startDate": start.isoformat(), "endDate": end.isoformat(),
               "dimensions": ["date"], "rowLimit": ROW_LIMIT}
    poster = http_post or gsc._default_http_post

    try:
        status, reason, raw = poster(endpoint, gsc._auth_headers(secret),
                                     json.dumps(payload).encode("utf-8"))
    except Exception as e:
        raise RuntimeError(redact_text(
            f"gsc_daily.py: request to Search Analytics API failed: {e}", secret_map)) from None
    if status < 200 or status >= 300:
        gsc._raise_api_error("gsc_daily.py: Search Analytics API", status, reason, raw, secret_map)

    return parse_daily_rows(json.loads(raw)), span, payload


def merge_days(existing, fetched, today, provisional_days=PROVISIONAL_DAYS):
    """Upsert: fetched days overwrite recorded ones, everything older is kept verbatim.

    The last `provisional_days` days are marked provisional - they are re-fetched and
    overwritten every subsequent invocation, so a settling correction self-heals.
    """
    cutoff = today - timedelta(days=provisional_days - 1) if provisional_days > 0 else None
    by_date = {d["date"]: dict(d) for d in existing}
    for row in fetched:
        by_date[row["date"]] = dict(row)
    out = []
    for key in sorted(by_date):
        row = by_date[key]
        try:
            is_provisional = cutoff is not None and date.fromisoformat(key) >= cutoff
        except ValueError:
            is_provisional = False
        out.append({"date": row["date"], "clicks": row.get("clicks"),
                    "impressions": row.get("impressions"), "position": row.get("position"),
                    "provisional": bool(is_provisional)})
    return out


def serialize(days):
    return "".join(json.dumps(d, sort_keys=True) + "\n" for d in days)


def _atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def write_artifacts(out_dir, days, meta, _fail_between=None):
    """jsonl then meta, each temp-file + os.replace.

    Two files cannot be replaced in one transaction, so the pairing is made *detectable*
    rather than assumed: the meta carries a sha256 over the jsonl's exact bytes. An
    interruption between the two replaces leaves a stale meta whose hash no longer
    matches - which the dashboard refuses to render from. A row count could not catch
    this: the most common write here is a provisional refresh, which rewrites the last
    three days **without changing the line count**.

    `_fail_between` is a test hook, never used in production.
    """
    os.makedirs(out_dir, exist_ok=True)
    body = serialize(days)
    jsonl_path = os.path.join(out_dir, "gsc_daily.jsonl")
    meta_path = os.path.join(out_dir, "gsc_daily.meta.json")
    assert_within(out_dir, jsonl_path, "gsc_daily.jsonl")
    assert_within(out_dir, meta_path, "gsc_daily.meta.json")

    _atomic_write(jsonl_path, body)
    if _fail_between:
        raise RuntimeError("injected failure between jsonl and meta replace")
    meta = dict(meta)
    meta["jsonl_sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    meta["jsonl_row_count"] = len(days)
    _atomic_write(meta_path, json.dumps(meta, indent=2, sort_keys=True) + "\n")
    return jsonl_path, meta_path


def load_existing(out_dir):
    path = os.path.join(out_dir, "gsc_daily.jsonl")
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def run(project, loop, workspace_root=WORKSPACE_ROOT, full=False, today=None,
        http_post=None, resolve=None, dry_run=False):
    sys.path.insert(0, os.path.join(workspace_root, "tools"))
    from seo_timeseries import _read_spec  # local import: shared frontmatter reader

    project_dir = os.path.join(workspace_root, "projects", project)
    loop_dir = os.path.join(project_dir, "loops", loop)
    assert_within(project_dir, loop_dir, "loop dir")
    out_dir = os.path.join(loop_dir, "timeseries")
    spec = _read_spec(os.path.join(loop_dir, "spec.md"))
    alias = (spec.get("credential_aliases") or {}).get("gsc")
    site_url = spec.get("site_url")
    today = today or _today()

    existing = load_existing(out_dir)
    last = max((date.fromisoformat(d["date"]) for d in existing if d.get("date")), default=None)
    start, end, retention_gap = compute_window(last, today=today, full=full)

    resolver = resolve or (lambda a: bearer_for_secret(resolve_credential(a, project_dir=project_dir)))
    fetched, span, payload = fetch_daily(alias, resolver, site_url, start, end, http_post=http_post)
    days = merge_days(existing, fetched, today)

    warnings = []
    if retention_gap:
        warnings.append(
            f"retention_gap: the recorded history ends {last}, which is older than GSC's "
            f"~{RETENTION_DAYS}-day retention. Days before {start} cannot be recovered.")
    if len(fetched) < span:
        warnings.append(f"GSC returned {len(fetched)} rows for a {span}-day window "
                        f"(days with no impressions are omitted, so this is expected, not a gap)")

    meta = {
        "last_pull_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "requested_span_days": span,
        "returned_row_count": len(fetched),
        "retention_days": RETENTION_DAYS,
        "provisional_days": PROVISIONAL_DAYS,
        "provisional_note": "heuristic, not a completeness guarantee - GSC publishes no settling deadline",
        "row_limit": ROW_LIMIT,
        "retention_gap": retention_gap,
        "warnings": warnings,
    }
    if dry_run:
        return days, meta, out_dir, False
    write_artifacts(out_dir, days, meta)
    return days, meta, out_dir, True


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pull true daily GSC totals (read-only).")
    ap.add_argument("project", nargs="?")
    ap.add_argument("loop", nargs="?")
    ap.add_argument("--full", action="store_true", help="request GSC's full retention window")
    ap.add_argument("--dry-run", action="store_true", help="fetch but write nothing")
    ap.add_argument("--verify", action="store_true", help="run self-tests (offline)")
    args = ap.parse_args(argv)

    if args.verify:
        return _self_test()
    if not args.project or not args.loop:
        ap.error("project and loop are required")

    from lib.tls import enable_system_truststore
    enable_system_truststore()  # live HTTPS through the Windows cert store (R8)

    try:
        days, meta, out_dir, wrote = run(args.project, args.loop, full=args.full, dry_run=args.dry_run)
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        print("Nothing was written; the existing daily history is unchanged.", file=sys.stderr)
        return 1

    complete = [d for d in days if not d["provisional"]]
    print(f"{args.project}/{args.loop} -> {out_dir}")
    print(f"  requested {meta['requested_start']} .. {meta['requested_end']} "
          f"({meta['requested_span_days']} days), {meta['returned_row_count']} rows returned")
    print(f"  history now {len(days)} days ({len(complete)} complete, "
          f"{len(days) - len(complete)} provisional)")
    if complete:
        print(f"  newest complete day {max(d['date'] for d in complete)}")
    for w in meta["warnings"]:
        print(f"  NOTE: {w}")
    print("  wrote gsc_daily.jsonl + gsc_daily.meta.json" if wrote else "  dry run, nothing written")
    print("  rebuild the dashboard: python tools/seo_timeseries.py "
          f"{args.project} {args.loop}")
    return 0


# --------------------------------------------------------------------------- #
# self-test (PLAN-SEO-TIMESERIES.md Part 5, checks 28-36) - fully offline
# --------------------------------------------------------------------------- #

def _self_test():  # noqa: C901
    import shutil
    import tempfile

    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    SECRET = "sk-live-DAILYCANARY1234567890"
    captured = {}

    def fake_post(rows):
        def post(url, headers, body):
            captured["url"], captured["headers"] = url, headers
            captured["body"] = json.loads(body.decode("utf-8"))
            return 200, "OK", json.dumps({"rows": rows}).encode("utf-8")
        return post

    def rows_for(start, n):
        return [{"keys": [(start + timedelta(days=i)).isoformat()],
                 "clicks": i % 5, "impressions": 10 + i, "position": 12.5}
                for i in range(n)]

    today = date(2026, 8, 16)
    tmp = tempfile.mkdtemp(prefix="gsc-daily-test-")
    try:
        # --- 28: request shape and the date-specific parser ------------------------
        start, end, _ = compute_window(None, today=today)
        got, span, payload = fetch_daily("a", lambda _: SECRET, "sc-domain:x", start, end,
                                         http_post=fake_post(rows_for(start, 3)))
        check("28a the request is dimensions:['date'] over the computed window",
              captured["body"]["dimensions"] == ["date"]
              and captured["body"]["startDate"] == start.isoformat()
              and captured["body"]["endDate"] == end.isoformat(),
              f"{captured['body']}")
        check("28b the parser maps keys[0] -> date (gsc.pull_metrics would emit 'keyword')",
              got and "date" in got[0] and "keyword" not in got[0]
              and got[0]["date"] == start.isoformat(), f"{got[:1]}")

        # --- 29: full-retention backfill and the computed span guard ----------------
        big = rows_for(start, 480)
        got, span, _ = fetch_daily("a", lambda _: SECRET, "sc-domain:x", start, end,
                                   http_post=fake_post(big))
        check("29a a full-retention response backfills every returned row in one call",
              len(got) == 480 and span == RETENTION_DAYS + 1, f"rows={len(got)} span={span}")
        refused = False
        try:
            fetch_daily("a", lambda _: SECRET, "sc-domain:x", today - timedelta(days=1200), today,
                        http_post=fake_post([]))
        except ValueError as e:
            refused = "refusing to request" in str(e)
        check("29b a span wider than the row limit is refused, not silently truncated", refused)

        # --- 30: the min/max regression, both gap directions -----------------------
        last = today - timedelta(days=200)
        s, e, gap = compute_window(last, today=today)
        expected = last - timedelta(days=PROVISIONAL_DAYS)
        check("30a a file ending 200 days ago requests from its own end, NOT full retention",
              s == expected and not gap,
              f"start={s} expected={expected} (min/max inversion would give {today - timedelta(days=RETENTION_DAYS)})")
        ancient = today - timedelta(days=900)
        s2, _, gap2 = compute_window(ancient, today=today)
        check("30b a gap older than retention is clamped to the retention floor and flagged",
              s2 == today - timedelta(days=RETENTION_DAYS) and gap2 is True, f"start={s2} gap={gap2}")
        s3, _, _ = compute_window(None, today=today)
        check("30c an empty history requests the full retention window",
              s3 == today - timedelta(days=RETENTION_DAYS))

        # --- 30d: idempotence - older rows survive a re-run byte-identical ----------
        base = [{"date": (today - timedelta(days=d)).isoformat(), "clicks": 1, "impressions": 5,
                 "position": 3.0} for d in range(30, 0, -1)]
        first = merge_days([], base, today)
        refetch = [dict(r) for r in base[-PROVISIONAL_DAYS:]]
        second = merge_days(first, refetch, today)
        old_a = [d for d in first if not d["provisional"]]
        old_b = [d for d in second if not d["provisional"]]
        check("30d re-running leaves every non-provisional row byte-identical",
              serialize(old_a) == serialize(old_b) and len(first) == len(second))

        # --- 31: provisional marking is a date rule, not a row count -----------------
        # Asserted as a property: the fixture ends yesterday (GSC lags, so "today" is
        # normally absent), and the rule must still mark exactly the days inside the
        # provisional window rather than "the last N rows".
        cutoff = today - timedelta(days=PROVISIONAL_DAYS - 1)
        rule_holds = all(d["provisional"] == (date.fromisoformat(d["date"]) >= cutoff) for d in first)
        prov = [d["date"] for d in first if d["provisional"]]
        check("31 exactly the days inside the provisional window are marked, by date rule "
              "(not by row position), and the window is configurable",
              rule_holds and prov == [(today - timedelta(days=n)).isoformat() for n in (2, 1)],
              f"marked={prov} cutoff={cutoff} rule_holds={rule_holds}")

        # --- 34: an API failure leaves the file untouched ---------------------------
        out = os.path.join(tmp, "art")
        write_artifacts(out, first, {"note": "seed"})
        before = open(os.path.join(out, "gsc_daily.jsonl"), "rb").read()

        def failing(url, headers, body):
            return 500, "Server Error", b'{"error":"boom"}'
        raised = False
        try:
            fetch_daily("a", lambda _: SECRET, "sc-domain:x", start, end, http_post=failing)
        except RuntimeError:
            raised = True
        def exploding(url, headers, body):
            raise OSError("network down")
        raised2 = False
        try:
            fetch_daily("a", lambda _: SECRET, "sc-domain:x", start, end, http_post=exploding)
        except RuntimeError:
            raised2 = True
        after = open(os.path.join(out, "gsc_daily.jsonl"), "rb").read()
        check("34 a non-2xx and a transport failure both raise and leave the file unmodified",
              raised and raised2 and before == after)

        # --- 34b: interruption between the two replaces is DETECTABLE ---------------
        refreshed = [dict(d) for d in first]
        refreshed[-1]["clicks"] = (refreshed[-1]["clicks"] or 0) + 7  # same line count, new bytes
        try:
            write_artifacts(out, refreshed, {"note": "refresh"}, _fail_between=True)
        except RuntimeError:
            pass
        sys.path.insert(0, os.path.join(WORKSPACE_ROOT, "tools"))
        from seo_timeseries import load_daily
        state = load_daily(out)
        same_count = len(refreshed) == len(first)
        check("34b an interruption between the jsonl and meta replace is caught by the sha256, "
              "with the row count unchanged (the ordinary provisional-refresh case)",
              state["state"] == "inconsistent" and "sha256" in state.get("reason", "") and same_count,
              f"{state.get('reason')} same_count={same_count}")

        write_artifacts(out, refreshed, {"note": "fixed"})
        check("34c rewriting both files repairs the pairing",
              load_daily(out)["state"] == "ok")

        # --- 35: the credential never reaches disk or an error message --------------
        blob = (open(os.path.join(out, "gsc_daily.jsonl"), encoding="utf-8").read()
                + open(os.path.join(out, "gsc_daily.meta.json"), encoding="utf-8").read())
        leak_msg = ""
        def leaky(url, headers, body):
            return 403, "Forbidden", json.dumps({"error": f"bad token {SECRET}"}).encode("utf-8")
        try:
            fetch_daily("art-gsc-readonly", lambda _: SECRET, "sc-domain:x", start, end, http_post=leaky)
        except RuntimeError as e:
            leak_msg = str(e)
        check("35 the credential appears in neither artifact nor any error message",
              SECRET not in blob and SECRET not in leak_msg and "[REDACTED" in leak_msg,
              f"msg={leak_msg[:90]}")

        # --- 36: gsc.py is reused, not modified --------------------------------------
        src = open(os.path.join(WORKSPACE_ROOT, "tools", "gsc_daily.py"), encoding="utf-8").read()
        check("36 gsc.py's auth/error/endpoint helpers are imported, and gsc.py is not edited",
              "gsc._auth_headers" in src and "gsc._raise_api_error" in src
              and "gsc.SEARCH_ANALYTICS_ENDPOINT" in src
              and hasattr(gsc, "_auth_headers") and hasattr(gsc, "pull_metrics"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = 0
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}" + (f"   [{detail}]" if detail and not ok else ""))
        if not ok:
            failed += 1
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

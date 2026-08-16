"""Build the SEO progress dashboard from the immutable run snapshots.

    python tools/seo_timeseries.py <project> <loop> [--out DIR] [--check] [--verify]

Design record: `PLAN-SEO-TIMESERIES.md` (Draft 7, approved by Codex at review round 7).
Extraction lives in `lib/timeseries.py`; this module only writes and renders.

Three constraints shape the output and are not negotiable without re-reading the plan:

* **No JavaScript.** `CLAUDE.md` forbids introducing another implementation language
  without prior approval, and generated browser JS is exactly that. Native `<title>`,
  `<details>` and real `<table>`s carry what a script would have. This also removes
  almost the whole injection surface.
* **No external references.** The page must open from disk, offline, forever - this
  machine intercepts TLS (RISK-REGISTER R8) and a CDN reference would be a silent
  dependency. Everything is inline; check 24 asserts it.
* **Deterministic.** Same snapshots in, byte-identical page out, except one footer
  stamp - so `--check` can tell "stale" from "just rebuilt".
"""
import argparse
import csv
import html
import io
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import timeseries as ts  # noqa: E402
from lib.paths import assert_within  # noqa: E402
from lib.redact import redact_deep  # noqa: E402

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAMP_MARKER = "data-build-stamp"
STALE_DAILY_DAYS = 14

# dataviz reference palette. Categorical slots 1-3 validate all-pairs in both modes;
# status steps are reserved for status and always ship with a label, never colour alone.
PALETTE = {
    "light": {"surface": "#fcfcfb", "page": "#f9f9f7", "ink": "#0b0b0b", "secondary": "#52514e",
              "muted": "#898781", "grid": "#e1e0d9", "axis": "#c3c2b7", "border": "rgba(11,11,11,0.10)",
              "s1": "#2a78d6", "s2": "#eb6834", "s3": "#1baf7a", "good": "#006300"},
    "dark": {"surface": "#1a1a19", "page": "#0d0d0d", "ink": "#ffffff", "secondary": "#c3c2b7",
             "muted": "#898781", "grid": "#2c2c2a", "axis": "#383835", "border": "rgba(255,255,255,0.10)",
             "s1": "#3987e5", "s2": "#d95926", "s3": "#199e70", "good": "#0ca30c"},
}
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}


# --------------------------------------------------------------------------- #
# escaping
# --------------------------------------------------------------------------- #

def esc(value):
    """The single escape used for every interpolation - text nodes, attribute values
    and SVG <title> content alike.

    GSC queries and DataForSEO result URLs are external, attacker-adjacent text that
    ends up in a file the operator opens in a browser. There is no JS context on this
    page, so HTML escaping is the whole defence, and it is applied everywhere rather
    than at the places that "look risky".
    """
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def num(value, digits=0):
    if value is None:
        return "&mdash;"
    if digits:
        return esc(f"{value:,.{digits}f}")
    return esc(f"{value:,.0f}")


# --------------------------------------------------------------------------- #
# svg primitives
# --------------------------------------------------------------------------- #

def _date_of(observation):
    """Position points by the window they measure when known, else by the pull date."""
    stamp = observation.get("window_end") or (observation.get("observed_at") or "")[:10]
    return stamp


def _to_ordinal(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").toordinal()
    except (ValueError, TypeError):
        return None


def _nice_ticks(lo, hi, count=4):
    if lo is None or hi is None:
        return []
    if hi == lo:
        hi = lo + 1
    span = hi - lo
    raw = span / max(count, 1)
    magnitude = 10 ** int(f"{raw:e}".split("e")[1]) if raw > 0 else 1
    for mult in (1, 2, 2.5, 5, 10):
        step = magnitude * mult
        if span / step <= count:
            break
    start = (int(lo / step)) * step
    ticks, v = [], start
    while v <= hi + step * 0.5:
        if v >= lo - step * 0.5:
            ticks.append(round(v, 6))
        v += step
    return ticks


def time_line_chart(series, title, subtitle, value_fmt=lambda v: f"{v:,.0f}",
                    invert=False, height=250, marks=None):
    """A real time axis (points at their true dates), 2px lines, 8px markers, hairline
    solid grid, selective end labels, native <title> tooltips. Never a dual axis.

    `series` = [{"name", "slot", "points": [(date_str, value, tooltip, flags)]}]
    `marks`  = optional per-x annotations rendered under the axis.
    """
    W, H = 760, height
    pad_l, pad_r, pad_t, pad_b = 52, 92, 16, 40
    all_points = [p for s in series for p in s["points"] if p[1] is not None]
    if not all_points:
        return f'<figure class="chart"><figcaption>{esc(title)}</figcaption><p class="empty">No observations yet.</p></figure>'

    xs = [_to_ordinal(p[0]) for p in all_points if _to_ordinal(p[0]) is not None]
    if not xs:
        return f'<figure class="chart"><figcaption>{esc(title)}</figcaption><p class="empty">No dated observations.</p></figure>'
    x_lo, x_hi = min(xs), max(xs)
    if x_hi == x_lo:
        x_lo, x_hi = x_lo - 1, x_hi + 1
    vals = [p[1] for p in all_points]
    v_lo, v_hi = min(vals), max(vals)
    if invert:
        v_lo, v_hi = max(0.0, v_lo - (v_hi - v_lo) * 0.15 - 1), v_hi + (v_hi - v_lo) * 0.15 + 1
    else:
        v_lo = 0 if v_lo >= 0 else v_lo
        v_hi = v_hi + (v_hi - v_lo) * 0.12 + (1 if v_hi == v_lo else 0)

    def sx(date_str):
        o = _to_ordinal(date_str)
        return pad_l + (o - x_lo) / (x_hi - x_lo) * (W - pad_l - pad_r)

    def sy(v):
        frac = (v - v_lo) / (v_hi - v_lo) if v_hi != v_lo else 0.5
        if invert:
            frac = 1 - frac
        return H - pad_b - frac * (H - pad_t - pad_b)

    out = [f'<figure class="chart"><figcaption>{esc(title)}'
           f'<span class="sub">{esc(subtitle)}</span></figcaption>',
           f'<svg viewBox="0 0 {W} {H}" role="img" width="100%" '
           f'aria-label="{esc(title)}. {esc(subtitle)}" preserveAspectRatio="xMidYMid meet">']

    for tick in _nice_ticks(v_lo, v_hi):
        y = sy(tick)
        if not (pad_t - 1 <= y <= H - pad_b + 1):
            continue
        out.append(f'<line class="grid" x1="{pad_l}" y1="{y:.1f}" x2="{W - pad_r}" y2="{y:.1f}"/>')
        out.append(f'<text class="tick" x="{pad_l - 8}" y="{y + 4:.1f}" text-anchor="end">{esc(value_fmt(tick))}</text>')
    out.append(f'<line class="axis" x1="{pad_l}" y1="{H - pad_b}" x2="{W - pad_r}" y2="{H - pad_b}"/>')

    seen_x = {}
    for s in series:
        pts = [(sx(p[0]), sy(p[1]), p) for p in s["points"] if p[1] is not None and _to_ordinal(p[0]) is not None]
        if not pts:
            continue
        slot = s.get("slot", 1)
        d = " ".join(("M" if i == 0 else "L") + f"{x:.1f} {y:.1f}" for i, (x, y, _) in enumerate(pts))
        out.append(f'<path class="line s{slot}" d="{d}"/>')
        for x, y, p in pts:
            flags = p[3] if len(p) > 3 else {}
            cls = "dot dup" if flags.get("near_duplicate") else "dot"
            if flags.get("truncation_suspected"):
                cls += " trunc"
            out.append(f'<g class="{cls} s{slot}"><circle cx="{x:.1f}" cy="{y:.1f}" r="4.5"/>'
                       f'<title>{esc(p[2])}</title></g>')
            seen_x.setdefault(round(x), p[0])
        lx, ly, lp = pts[-1]
        out.append(f'<text class="endlabel" x="{lx + 10:.1f}" y="{ly + 4:.1f}">'
                   f'{esc(value_fmt(lp[1]))}</text>')

    # Thin the date labels so they cannot collide. Observations are irregularly spaced -
    # 2026-08-03 and 2026-08-04 land ~25px apart on this scale while a "08-03" label is
    # ~34px wide - so drawing one per point overlaps. The newest is always kept (it is
    # the one being read); earlier ones are dropped when they would touch.
    MIN_LABEL_GAP = 46
    kept, ticks = [], sorted(seen_x.items())
    for x, date_str in reversed(ticks):  # newest first, so the newest always survives
        if not kept or abs(kept[-1][0] - x) >= MIN_LABEL_GAP:
            kept.append((x, date_str))
    for x, date_str in sorted(kept):
        out.append(f'<text class="tick" x="{x}" y="{H - pad_b + 16}" text-anchor="middle">{esc(date_str[5:])}</text>')
    if marks:
        for date_str, label in marks:
            if _to_ordinal(date_str) is None:
                continue
            out.append(f'<text class="mark" x="{sx(date_str):.1f}" y="{H - pad_b + 30}" '
                       f'text-anchor="middle">{esc(label)}</text>')

    out.append("</svg>")
    if len(series) > 1:
        keys = "".join(f'<span class="key"><i class="sw s{s.get("slot", 1)}"></i>{esc(s["name"])}</span>'
                       for s in series)
        out.append(f'<div class="legend">{keys}</div>')
    out.append("</figure>")
    return "".join(out)


def sparkline(values, width=104, height=26, invert=False):
    """A 12-point trend in the de-emphasis hue with the current point in the accent.
    Decorative-adjacent: every value it shows is also in a table."""
    pts = [v for v in values if v is not None]
    if len(pts) < 2:
        return '<span class="spark-empty">&mdash;</span>'
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1
    step = width / (len(values) - 1) if len(values) > 1 else width
    coords = []
    for i, v in enumerate(values):
        if v is None:
            continue
        frac = (v - lo) / span
        if invert:
            frac = 1 - frac
        coords.append((i * step, height - 3 - frac * (height - 6)))
    d = " ".join(("M" if i == 0 else "L") + f"{x:.1f} {y:.1f}" for i, (x, y) in enumerate(coords))
    cx, cy = coords[-1]
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
            f'aria-hidden="true"><path d="{d}"/><circle cx="{cx:.1f}" cy="{cy:.1f}" r="2.6"/></svg>')


# --------------------------------------------------------------------------- #
# page assembly
# --------------------------------------------------------------------------- #

def _delta(current, previous, lower_is_better=False):
    """Returns (text, direction) where direction is good/bad/flat/none.

    Direction is always accompanied by a word or arrow in the rendered output - never
    carried by colour alone.
    """
    if current is None or previous is None:
        return ("no comparable prior observation", "none")
    diff = current - previous
    if abs(diff) < 1e-9:
        return ("no change", "flat")
    improved = diff < 0 if lower_is_better else diff > 0
    sign = "+" if diff > 0 else "−"
    mag = abs(diff)
    text = f"{sign}{mag:,.1f}" if isinstance(diff, float) and mag < 100 else f"{sign}{mag:,.0f}"
    return (text, "good" if improved else "bad")


def _stat_tile(label, value, delta_text, direction, spark, note=""):
    arrow = {"good": "▲", "bad": "▼", "flat": "→", "none": ""}[direction]
    word = {"good": "better", "bad": "worse", "flat": "", "none": ""}[direction]
    return (f'<div class="tile"><div class="tile-label">{esc(label)}</div>'
            f'<div class="tile-value">{value}</div>'
            f'<div class="tile-delta d-{direction}">{arrow} {esc(delta_text)}'
            f'{" " + esc(word) if word else ""}</div>'
            f'<div class="tile-spark">{spark}</div>'
            f'{f"<div class=chip>{esc(note)}</div>" if note else ""}</div>')


def _observation_label(o):
    if o.get("window_known"):
        return f"trailing 28d ending {o['window_end']}"
    return f"pull {o['observed_at'][:10]} (window unknown)"


def _tooltip(o, metric, value):
    bits = [f"{metric}: {value}", _observation_label(o), f"pulled {o['observed_at'][:19]}Z",
            f"{o['row_count']:,} rows"]
    if o.get("near_duplicate"):
        bits.append(f"re-pull, {o['hours_since_previous']}h after previous — near-identical by construction")
    if o.get("truncation_suspected"):
        bits.append("row limit reached — totals are partial, not comparable")
    return " • ".join(bits)


def _gsc_points(gsc, pick, metric):
    pts = []
    for o in gsc:
        v = pick(o)
        pts.append((_date_of(o), v, _tooltip(o, metric, f"{v:,.2f}" if isinstance(v, float) else f"{v:,}" if v is not None else "n/a"),
                    {"near_duplicate": o.get("near_duplicate"), "truncation_suspected": o.get("truncation_suspected")}))
    return pts


def _css():
    def block(mode):
        p = PALETTE[mode]
        return "".join(f"--{k}:{v};" for k, v in p.items())
    return f"""
:root{{color-scheme:light dark;{block('light')}
--font:system-ui,-apple-system,"Segoe UI",sans-serif;}}
@media (prefers-color-scheme:dark){{:root{{{block('dark')}}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--page);color:var(--ink);font-family:var(--font);
line-height:1.5;font-size:15px}}
.wrap{{max-width:920px;margin:0 auto;padding:32px 20px 72px}}
h1{{font-size:26px;margin:0 0 4px;font-weight:600}}
h2{{font-size:18px;margin:40px 0 6px;font-weight:600}}
h3{{font-size:14px;margin:22px 0 6px;font-weight:600;color:var(--secondary)}}
p,li{{color:var(--secondary);margin:6px 0}}
.lede{{color:var(--muted);font-size:13px;margin:0 0 18px}}
.strip{{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 6px}}
.chip{{font-size:12px;color:var(--secondary);background:var(--surface);
border:1px solid var(--border);border-radius:999px;padding:3px 10px;white-space:nowrap}}
.banner{{border:1px solid var(--border);border-left:3px solid {STATUS['warning']};
background:var(--surface);border-radius:8px;padding:10px 14px;margin:12px 0;font-size:13px;
color:var(--secondary)}}
.banner.crit{{border-left-color:{STATUS['critical']}}}
.banner.info{{border-left-color:var(--s1)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:12px;margin:16px 0 8px}}
.tile{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px}}
.tile-label{{font-size:12px;color:var(--muted)}}
.tile-value{{font-size:28px;font-weight:600;margin:2px 0;letter-spacing:-.01em}}
.tile-delta{{font-size:12px;color:var(--secondary)}}
.d-good{{color:var(--good)}}
.d-bad{{color:{STATUS['critical']}}}
.tile-spark{{margin-top:6px;height:26px}}
.spark path{{fill:none;stroke:var(--muted);stroke-width:1.5}}
.spark circle{{fill:var(--s1)}}
.spark-empty{{color:var(--muted);font-size:12px}}
figure.chart{{margin:14px 0 6px;background:var(--surface);border:1px solid var(--border);
border-radius:10px;padding:14px 14px 8px}}
figcaption{{font-size:14px;font-weight:600;margin-bottom:2px}}
figcaption .sub{{display:block;font-weight:400;font-size:12px;color:var(--muted)}}
svg{{display:block;max-width:100%;height:auto;overflow:visible}}
.grid{{stroke:var(--grid);stroke-width:1}}
.axis{{stroke:var(--axis);stroke-width:1}}
.tick,.mark{{fill:var(--muted);font-size:11px;font-family:var(--font);
font-variant-numeric:tabular-nums}}
.endlabel{{fill:var(--secondary);font-size:12px;font-family:var(--font)}}
.line{{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}}
.dot circle{{stroke:var(--surface);stroke-width:2}}
.dot.dup circle{{r:3;opacity:.55}}
.dot.trunc circle{{stroke:{STATUS['warning']};stroke-width:2.5}}
.s1 .line,.line.s1{{stroke:var(--s1)}} .s1 circle{{fill:var(--s1)}}
.s2 .line,.line.s2{{stroke:var(--s2)}} .s2 circle{{fill:var(--s2)}}
.s3 .line,.line.s3{{stroke:var(--s3)}} .s3 circle{{fill:var(--s3)}}
.legend{{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--secondary);
padding:4px 0 6px}}
.key{{display:inline-flex;align-items:center;gap:6px}}
.sw{{width:12px;height:3px;border-radius:2px;display:inline-block}}
.sw.s1{{background:var(--s1)}} .sw.s2{{background:var(--s2)}} .sw.s3{{background:var(--s3)}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--border);
vertical-align:middle}}
th{{color:var(--muted);font-weight:600;font-size:12px}}
td.n,th.n{{text-align:right;font-variant-numeric:tabular-nums}}
tbody tr:hover{{background:var(--surface)}}
details{{margin:8px 0}}
summary{{cursor:pointer;font-size:13px;color:var(--secondary);padding:4px 0}}
.badge{{font-size:11px;border-radius:4px;padding:1px 6px;border:1px solid var(--border);
color:var(--muted);margin-left:6px;white-space:nowrap}}
.empty{{color:var(--muted);font-size:13px}}
.small{{font-size:12px;color:var(--muted)}}
footer{{margin-top:52px;border-top:1px solid var(--border);padding-top:18px;font-size:12.5px;
color:var(--muted)}}
footer h2{{font-size:15px;margin-top:18px}}
footer code{{background:var(--surface);border:1px solid var(--border);border-radius:4px;
padding:1px 5px}}
.st-good{{color:{STATUS['good']}}} .st-warn{{color:{STATUS['serious']}}}
.st-crit{{color:{STATUS['critical']}}}
@media print{{body{{background:#fff}}.tile,figure.chart{{break-inside:avoid}}}}
"""


def _tracked_table(gsc, universe, brand_terms, exclusions):
    """One row per universe member: the 'each of the various keywords' view.

    Absence is rendered as an em dash, never a zero - GSC omits rows below its own
    disclosure floor, so a missing query is unknown, not zero.
    """
    per_obs = [{r["query"]: r for r in (o.get("rollup") or [])} for o in gsc]
    rows_html = []
    for query in universe["members"]:
        history = [obs.get(query) for obs in per_obs]
        impressions = [h["impressions"] if h else None for h in history]
        positions = [h["position"] if h else None for h in history]
        latest = next((h for h in reversed(history) if h), None)
        prior = None
        for i in range(len(history) - 2, -1, -1):
            if history[i] and not gsc[i].get("near_duplicate"):
                prior = history[i]
                break
        badges = ""
        if ts.is_brand(query, brand_terms):
            badges += '<span class="badge">brand</span>'
        if any(x in (query or "").lower() for x in exclusions):
            badges += '<span class="badge">excluded from proposals</span>'
        pos_text = "&mdash;"
        if latest and latest["position"] is not None:
            pos_text = f"{latest['position']:.1f}"
            if latest["page_count"] > 1:
                pos_text += f'<span class="badge">across {latest["page_count"]} pages</span>'
        dtext, ddir = _delta(latest["position"] if latest else None,
                             prior["position"] if prior else None, lower_is_better=True)
        detail_rows = "".join(
            f"<tr><td>{esc(_observation_label(gsc[i]))}</td>"
            f'<td class="n">{num(h["clicks"]) if h else "&mdash;"}</td>'
            f'<td class="n">{num(h["impressions"]) if h else "&mdash;"}</td>'
            f'<td class="n">{f"{h["position"]:.1f}" if h and h["position"] is not None else "&mdash;"}</td>'
            f'<td>{esc(", ".join(h["pages"])) if h else "not returned in this pull"}</td></tr>'
            for i, h in enumerate(history))
        rows_html.append(
            f"<tr><td>{esc(query)}{badges}"
            f'<details><summary>history</summary><table><thead><tr><th>observation</th>'
            f'<th class="n">clicks</th><th class="n">impressions</th><th class="n">position</th>'
            f"<th>pages</th></tr></thead><tbody>{detail_rows}</tbody></table></details></td>"
            f'<td class="n">{pos_text}</td>'
            f'<td class="n d-{ddir}">{esc(dtext)}</td>'
            f'<td class="n">{num(latest["clicks"]) if latest else "&mdash;"}</td>'
            f'<td class="n">{num(latest["impressions"]) if latest else "&mdash;"}</td>'
            f"<td>{sparkline(impressions)}</td></tr>")
    return ("<table><thead><tr><th>Tracked query</th><th class=\"n\">Latest position</th>"
            "<th class=\"n\">&Delta; position</th><th class=\"n\">Clicks</th>"
            "<th class=\"n\">Impressions</th><th>Impressions trend</th></tr></thead><tbody>"
            + "".join(rows_html) + "</tbody></table>")


def _integrity_panel(local_rank):
    """F6: no rank chart and no rank number. Classification only.

    31 of 31 non-null results in the whole history are index 0 of their batch, so ten of
    twelve targets have never been queried at all. A rank line drawn through this would
    be a fabrication containing competitors' rankings.
    """
    if not local_rank:
        return "<p class=\"empty\">No local-rank observations.</p>"
    targets, counts = {}, {}
    for obs in local_rank:
        for r in obs["rows"]:
            key = (r["location"], r["keyword"])
            t = targets.setdefault(key, {"queried": 0, "own": 0, "competitor": 0,
                                         "absent": 0, "not-queried": 0, "unknown": 0,
                                         "competitors": set(), "page": r["page"]})
            t[r["classification"]] = t.get(r["classification"], 0) + 1
            counts[r["classification"]] = counts.get(r["classification"], 0) + 1
            if r["classification"] in ("own", "competitor", "absent"):
                t["queried"] += 1
            if r["competitor_domain"]:
                t["competitors"].add(r["competitor_domain"])
    total = sum(counts.values())
    # Scope the headline to the CURRENT target set. Counting every (location, keyword)
    # pair that has ever existed mixes eras: the 2026-07-24/26 observations carry a
    # retired 3-location x 12-keyword cross-product (36 rows), so an all-time count reads
    # "32 of 36" for a loop that checks 12 targets today.
    current_keys = {(r["location"], r["keyword"]) for r in local_rank[-1]["rows"]}
    current = {k: v for k, v in targets.items() if k in current_keys}
    never = sum(1 for t in current.values() if t["queried"] == 0)
    retired = len(targets) - len(current)

    rows = []
    for (location, keyword), t in sorted(targets.items()):
        if t["queried"] == 0:
            state = '<span class="st-crit">never queried</span>'
        elif t["unknown"]:
            state = '<span class="st-warn">provenance unknown</span>'
        else:
            state = '<span class="st-good">queried</span>'
        who = ", ".join(sorted(t["competitors"])) or "&mdash;"
        rows.append(f"<tr><td>{esc(location)}</td><td>{esc(keyword)}</td><td>{esc(t['page'])}</td>"
                    f'<td>{state}</td><td class="n">{t["queried"]}</td>'
                    f'<td class="n">{t["own"]}</td><td class="n">{t["competitor"]}</td>'
                    f"<td>{esc(who) if who != '&mdash;' else who}</td></tr>")

    return (
        f'<div class="banner crit"><strong>{never} of the {len(current)} rank targets currently '
        f"configured have never been queried, once, in the entire history.</strong> "
        f"The DataForSEO connector batches every "
        f"target for a location into one request, and the live endpoint executes only the first "
        f"one (defect D4, <code>PLAN-SEO-PROGRAM-INTEGRATION.md</code> P0.1). Across all "
        f"{len(local_rank)} observations, <strong>every single non-null result is the first target "
        f"of its batch — no exceptions</strong>. So a <em>null</em> here means "
        f"<em>not asked</em>, not <em>not ranking</em>.</div>"
        f'<div class="banner">Two further defects make the numbers that <em>were</em> returned '
        f"unusable as ranks: results are matched by bare URL substring with no domain check (D1), "
        f"so competitors' pages are attributed to this site; and the value stored is "
        f"<code>rank_absolute</code>, which counts every SERP feature rather than the organic "
        f"index (D5). <strong>That is why this section shows no rank numbers and no rank chart.</strong> "
        f"It shows only what was collected. Real rank tracking resumes once P0.1 lands.</div>"
        f'<p class="small">Observation outcomes across {total:,} target-checks: '
        + esc(", ".join(f"{k} {v:,}" for k, v in sorted(counts.items())))
        + (f". The table below also lists {retired} retired target(s) from the "
           "3-location cross-product used before 2026-07-26." if retired else ".") + "</p>"
        "<table><thead><tr><th>Location</th><th>Keyword</th><th>Target page</th><th>Collection</th>"
        "<th class=\"n\">Times queried</th><th class=\"n\">Own</th><th class=\"n\">Competitor</th>"
        "<th>Competitors seen</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def _technical_strip(pagespeed, indexation):
    """Latest-only, with SEPARATE as-of dates: the two sources refresh and carry forward
    independently, so one 'as of' would present stale data as fresh."""
    chips = []
    if pagespeed:
        latest = pagespeed[-1]
        scores = [r["performance_score"] for r in latest["rows"] if r["performance_score"] is not None]
        if scores:
            chips.append(f"CWV lab {min(scores)}–{max(scores)} across {len(scores)} pages "
                         f"(as of {latest['observed_at'][:10]})")
        else:
            chips.append(f"CWV unknown (as of {latest['observed_at'][:10]})")
    else:
        chips.append("CWV: no observation")
    if indexation:
        latest = indexation[-1]
        rows = latest["rows"]
        passes = sum(1 for r in rows if r["verdict"] == "PASS")
        chips.append(f"Indexation {passes}/{len(rows)} PASS (as of {latest['observed_at'][:10]})")
        for sm in latest.get("sitemaps") or []:
            chips.append(f"sitemap warnings {sm.get('warnings')}, errors {sm.get('errors')}")
    else:
        chips.append("Indexation: no observation")
    return '<div class="strip">' + "".join(f'<span class="chip">{esc(c)}</span>' for c in chips) + "</div>"


def _reconciliation_panel(gsc, daily):
    """Compare the loop's query-dimension totals against site totals for the same window.

    GSC withholds queries it considers rare, so a `["query","page"]` pull only sees the
    disclosed subset while a `["date"]` pull returns the site total. Every conclusion the
    loop has drawn about traffic volume rests on the smaller number, so the size of the
    gap is not a footnote - it is the first thing worth knowing. Only rendered when the
    daily history covers **every** day of the compared window; a partial overlap would
    understate the site total and manufacture a fake discrepancy.
    """
    if daily.get("state") != "ok" or not gsc:
        return ""
    by_date = {d["date"]: d for d in daily["days"]}
    rows, best = [], None
    for o in gsc:
        if not o.get("window_known"):
            continue
        try:
            d0 = datetime.strptime(o["window_start"], "%Y-%m-%d").date()
            d1 = datetime.strptime(o["window_end"], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        span = (d1 - d0).days + 1
        present = [by_date[k] for k in by_date
                   if d0 <= datetime.strptime(k, "%Y-%m-%d").date() <= d1]
        if len(present) < span:
            continue
        site_clicks = sum(p["clicks"] or 0 for p in present)
        site_impr = sum(p["impressions"] or 0 for p in present)
        q_clicks = o["totals"]["clicks"]
        q_impr = o["totals"]["impressions"]
        row = {"window": f"{o['window_start']} → {o['window_end']}", "site_clicks": site_clicks,
               "q_clicks": q_clicks, "site_impr": site_impr, "q_impr": q_impr,
               "hidden_clicks": site_clicks - q_clicks,
               "ratio": (site_clicks / q_clicks) if q_clicks else None}
        rows.append(row)
        best = row
    if not rows:
        return ""

    body = (
        f'<div class="banner crit"><strong>The per-query data the loop optimises against sees '
        f'{best["q_clicks"]} of the site&rsquo;s {best["site_clicks"]} clicks in the same 28-day '
        f'window — {best["hidden_clicks"]} clicks are missing from it.</strong> '
        f"Google withholds queries it treats as rare, so a per-query pull returns only the "
        f"disclosed subset while a per-day pull returns the site total. Both numbers below are "
        f"measured, from two requests against the same property and the same dates.</div>"
        f'<div class="banner">What this does <em>not</em> tell us: whether those '
        f'{best["hidden_clicks"]} withheld clicks are brand or non-brand. Anonymised queries skew '
        f"long-tail, which would suggest mostly non-brand — but that is an inference, not a "
        f"measurement, and the loop&rsquo;s &ldquo;3 non-brand clicks&rdquo; figure should not be "
        f"treated as settled until it is checked another way.</div>")
    trs = "".join(
        f'<tr><td>{esc(r["window"])}</td><td class="n">{num(r["site_clicks"])}</td>'
        f'<td class="n">{num(r["q_clicks"])}</td><td class="n">{num(r["hidden_clicks"])}</td>'
        f'<td class="n">{f"{r["ratio"]:.1f}×" if r["ratio"] else "&mdash;"}</td>'
        f'<td class="n">{num(r["site_impr"])}</td><td class="n">{num(r["q_impr"])}</td></tr>'
        for r in rows)
    body += ("<table><thead><tr><th>28-day window</th><th class=\"n\">Site clicks (per-day pull)</th>"
             "<th class=\"n\">Clicks the loop sees (per-query pull)</th>"
             "<th class=\"n\">Withheld</th><th class=\"n\">Ratio</th>"
             "<th class=\"n\">Site impressions</th><th class=\"n\">Loop impressions</th>"
             f"</tr></thead><tbody>{trs}</tbody></table>")
    body += ('<p class="small">Only windows the daily history covers in full are compared; a '
             "partial overlap would understate the site total and invent a discrepancy.</p>")
    return body


def _weekly_section(daily):
    """Phase B renders as its own section ABOVE the trailing-window charts - never
    replacing or merging with them. They measure different things (F2), and silently
    swapping one for the other is the misrepresentation this whole plan exists to stop."""
    if daily["state"] == "absent":
        return ('<div class="banner info">A <strong>true week-by-week</strong> series is available '
                'but not built yet. GSC\'s trailing-28-day window overlaps 21 of its 28 days between '
                'pulls, so the charts below are a rolling measure, not weekly progress. Run '
                '<code>python tools/gsc_daily.py art seo</code> to pull real daily data (one free '
                'read-only call, backfills GSC\'s full ~16-month retention).</div>')
    if daily["state"] != "ok":
        return (f'<div class="banner crit">Daily GSC data is inconsistent '
                f'({esc(daily["reason"])}) — re-run <code>python tools/gsc_daily.py art seo</code>. '
                f"Showing trailing-window data only.</div>")

    weeks = daily["weeks"]
    stale = daily.get("stale")
    banner = (f'<div class="banner">The daily series is stale — newest complete day is '
              f'{esc(daily["newest_complete"])}. Re-run <code>python tools/gsc_daily.py art seo</code>.</div>'
              if stale else "")
    clicks = [(w["week_end"], w["clicks"],
               f"clicks {w['clicks']:,} • ISO week {w['week']} ({w['week_start']}→{w['week_end']})"
               + (" • PARTIAL week, excluded from deltas" if w["partial"] else ""),
               {"near_duplicate": w["partial"]}) for w in weeks]
    impressions = [(w["week_end"], w["impressions"],
                    f"impressions {w['impressions']:,} • ISO week {w['week']}"
                    + (" • PARTIAL" if w["partial"] else ""),
                    {"near_duplicate": w["partial"]}) for w in weeks]
    body = (banner
            + time_line_chart([{"name": "Clicks", "slot": 1, "points": clicks}],
                              "Clicks per ISO week", "true weekly totals from daily GSC data — no overlapping windows")
            + time_line_chart([{"name": "Impressions", "slot": 1, "points": impressions}],
                              "Impressions per ISO week", "true weekly totals; partial weeks drawn faded and excluded from deltas"))
    rows = "".join(f'<tr><td>{esc(w["week"])}</td><td>{esc(w["week_start"])}</td>'
                   f'<td class="n">{num(w["clicks"])}</td><td class="n">{num(w["impressions"])}</td>'
                   f'<td class="n">{num(w["ctr"] * 100, 2) if w["ctr"] is not None else "&mdash;"}%</td>'
                   f'<td class="n">{num(w["position"], 1) if w["position"] is not None else "&mdash;"}</td>'
                   f'<td>{"partial" if w["partial"] else "complete"}</td></tr>' for w in weeks)
    body += ("<details><summary>Weekly numbers</summary><table><thead><tr><th>ISO week</th>"
             "<th>Starting</th><th class=\"n\">Clicks</th><th class=\"n\">Impressions</th>"
             "<th class=\"n\">CTR</th><th class=\"n\">Avg position</th><th>State</th></tr></thead>"
             f"<tbody>{rows}</tbody></table></details>")
    return body


def render(data, build_stamp):
    gsc = data["series"]["gsc"]
    universe = data["universe"]
    meta = data["meta"]
    brand_terms = meta["brand_terms"]
    exclusions = meta["keyword_exclusions"]

    parts = [f'<div class="wrap" {STAMP_MARKER}="1">']
    parts.append(f'<h1>SEO progress — {esc(meta["project"])}</h1>')
    parts.append(f'<p class="lede">{esc(meta["domain"])} &middot; derived from '
                 f'{meta["snapshot_bearing"]} run snapshots in {meta["run_dirs"]} run directories '
                 f'&middot; observations are deduplicated by measurement timestamp, never by run</p>')

    # freshness + technical strips
    chips = [f"GSC: {len(gsc)} observations"]
    if gsc:
        chips.append(f"last GSC pull {gsc[-1]['observed_at'][:10]}")
    chips.append(f"local rank: {len(data['series']['local_rank'])} observations")
    chips.append(f"backlinks: {len(data['series']['backlinks'])} observations")
    chips.append(f"tracked queries: {len(universe['members'])} (universe {universe['version']})")
    parts.append('<div class="strip">' + "".join(f'<span class="chip">{esc(c)}</span>' for c in chips) + "</div>")
    parts.append(_technical_strip(data["series"]["pagespeed"], data["series"]["indexation"]))

    if any(o["truncation_suspected"] for o in gsc):
        parts.append('<div class="banner"><strong>Row limit reached on at least one pull.</strong> '
                     'The GSC connector requests 1,000 rows and does not paginate, so those totals are '
                     'partial. Their tiles are marked and their deltas read &ldquo;not comparable&rdquo;.</div>')
    if any(not o["window_known"] for o in gsc):
        parts.append('<div class="banner">One or more pulls have no recorded date window '
                     '(no <code>gsc</code> call in that run&rsquo;s <code>run.json</code>); '
                     'they are labelled by pull date instead of window.</div>')
    if data["warnings"]:
        items = "".join(f"<li>{esc(w)}</li>" for w in data["warnings"])
        parts.append(f'<details><summary>{len(data["warnings"])} build warning(s)</summary>'
                     f"<ul>{items}</ul></details>")

    # ---- KPI row -----------------------------------------------------------
    if gsc:
        latest = gsc[-1]
        prior = ts.previous_comparable(gsc, len(gsc) - 1)
        capped = latest["truncation_suspected"]

        def tile(label, key, source="totals", digits=0, lower_better=False):
            cur = latest[source][key]
            prev = prior[source][key] if prior else None
            if capped:
                dtext, ddir = "not comparable (row limit reached)", "none"
            else:
                dtext, ddir = _delta(cur, prev, lower_is_better=lower_better)
            spark = sparkline([o[source][key] for o in gsc], invert=lower_better)
            return _stat_tile(label, num(cur, digits), dtext, ddir, spark,
                              "partial — row limit reached" if capped else "")

        tiles = [
            tile("Non-brand clicks", "clicks", "nonbrand"),
            tile("Total clicks", "clicks"),
            tile("Impressions", "impressions"),
            tile("Avg position", "avg_position", digits=1, lower_better=True),
        ]
        bl = data["series"]["backlinks"]
        if bl:
            cur = bl[-1]["referring_domains"]
            prev = bl[-2]["referring_domains"] if len(bl) > 1 else None
            dtext, ddir = _delta(cur, prev)
            tiles.append(_stat_tile("Referring domains", num(cur), dtext, ddir,
                                    sparkline([b["referring_domains"] for b in bl])))
        parts.append('<div class="tiles">' + "".join(tiles) + "</div>")
        parts.append(f'<p class="small">Compared against the previous <em>distinct</em> observation '
                     f'({esc(_observation_label(prior)) if prior else "none yet"}). '
                     f"Clicks and positions measure search visibility — they are not leads, "
                     f"calls, or booked patients.</p>")

    # ---- reconciliation (only possible once Phase B data exists) ------------
    recon = _reconciliation_panel(gsc, data["daily"])
    if recon:
        parts.append("<h2>What the loop can and cannot see</h2>")
        parts.append(recon)

    # ---- Phase B weekly ----------------------------------------------------
    parts.append("<h2>Weekly search performance</h2>")
    parts.append(_weekly_section(data["daily"]))

    # ---- Phase A trailing-window charts ------------------------------------
    parts.append("<h2>Search performance over time</h2>")
    parts.append('<p class="small">Every GSC number below is a <strong>trailing 28-day total</strong> '
                 'as of its pull date. Consecutive weekly pulls overlap by 21 of their 28 days, so a '
                 'change between two points is a shift in a rolling sum, not that week&rsquo;s gain. '
                 'Points sit at their true dates; smaller faded markers are re-pulls less than 24 hours '
                 'apart, which repeat the previous reading by construction and are excluded from deltas.</p>')

    marks = [(_date_of(o), "re-pull") for o in gsc if o.get("near_duplicate")]
    parts.append(time_line_chart(
        [{"name": "Non-brand clicks", "slot": 1, "points": _gsc_points(gsc, lambda o: o["nonbrand"]["clicks"], "non-brand clicks")},
         {"name": "Brand clicks", "slot": 2, "points": _gsc_points(gsc, lambda o: o["brand"]["clicks"], "brand clicks")}],
        "Clicks", "trailing 28 days, split brand vs non-brand", marks=marks))
    parts.append(time_line_chart(
        [{"name": "Non-brand impressions", "slot": 1, "points": _gsc_points(gsc, lambda o: o["nonbrand"]["impressions"], "non-brand impressions")},
         {"name": "Brand impressions", "slot": 2, "points": _gsc_points(gsc, lambda o: o["brand"]["impressions"], "brand impressions")}],
        "Impressions", "trailing 28 days, split brand vs non-brand", marks=marks))
    parts.append(time_line_chart(
        [{"name": "Average position", "slot": 1, "points": _gsc_points(gsc, lambda o: o["totals"]["avg_position"], "avg position")}],
        "Average position", "impression-weighted across every returned row — axis inverted, so higher on the chart is better",
        value_fmt=lambda v: f"{v:,.0f}", invert=True, marks=marks))
    parts.append('<p class="small">Average position moves when Google shows the site for <em>more</em> '
                 'low-position queries, which is not a ranking regression. Read the per-query table for '
                 "real movement.</p>")

    obs_rows = "".join(
        f'<tr><td>{esc(_observation_label(o))}</td><td>{esc(o["observed_at"][:19])}Z</td>'
        f'<td class="n">{num(o["row_count"])}</td><td class="n">{num(o["totals"]["clicks"])}</td>'
        f'<td class="n">{num(o["nonbrand"]["clicks"])}</td>'
        f'<td class="n">{num(o["totals"]["impressions"])}</td>'
        f'<td class="n">{num(o["totals"]["avg_position"], 1)}</td>'
        f'<td>{"re-pull &lt;24h" if o.get("near_duplicate") else ""}'
        f'{" row-limit reached" if o["truncation_suspected"] else ""}</td></tr>' for o in gsc)
    parts.append("<details><summary>Every GSC observation, as numbers</summary><table><thead><tr>"
                 "<th>Observation</th><th>Pulled at</th><th class=\"n\">Rows</th>"
                 "<th class=\"n\">Clicks</th><th class=\"n\">Non-brand</th>"
                 "<th class=\"n\">Impressions</th><th class=\"n\">Avg position</th><th>Flags</th>"
                 f"</tr></thead><tbody>{obs_rows}</tbody></table></details>")

    # ---- priority pages ----------------------------------------------------
    pages = sorted({p for o in gsc for p in o["priority_pages"]})
    if pages:
        parts.append("<h2>Priority pages</h2>")
        parts.append('<p class="small">Impression-weighted average position per service page, one '
                     "chart each rather than six lines in one frame.</p>")
        for page in pages:
            label = page.replace(meta["site_base"], "") or "/"
            pts = []
            for o in gsc:
                t = o["priority_pages"].get(page)
                v = t["avg_position"] if t else None
                pts.append((_date_of(o), v,
                            f"{label} position {v}" if v is not None else f"{label}: not returned in this pull",
                            {"near_duplicate": o.get("near_duplicate")}))
            parts.append(time_line_chart([{"name": label, "slot": 1, "points": pts}],
                                         label, "average position — axis inverted, higher is better",
                                         value_fmt=lambda v: f"{v:,.0f}", invert=True, height=190))

    # ---- tracked queries ---------------------------------------------------
    parts.append("<h2>Tracked queries</h2>")
    parts.append(f'<p class="small">{len(universe["members"])} queries: the 12 configured rank targets, '
                 "plus every query that has ever taken a click, plus every query that has ever been in "
                 "the top 25 by impressions. Membership is union-across-all-time, so a query that falls "
                 "out of the top 25 keeps its row. An em dash means the query was not returned in that "
                 "pull — unknown, not zero.</p>")
    parts.append(_tracked_table(gsc, universe, brand_terms, exclusions))

    # ---- local rank --------------------------------------------------------
    parts.append("<h2>Local rank — collection integrity</h2>")
    parts.append(_integrity_panel(data["series"]["local_rank"]))

    # ---- backlinks ---------------------------------------------------------
    bl = data["series"]["backlinks"]
    parts.append("<h2>DataForSEO index totals</h2>")
    if bl:
        parts.append('<p class="small">Current totals in DataForSEO&rsquo;s backlink index, as of each '
                     "observation. These move when that index is recrawled; they are not a measure of "
                     "work performed.</p>")
        rows = "".join(f'<tr><td>{esc(b["observed_at"][:10])}</td>'
                       f'<td class="n">{num(b["referring_domains"])}</td>'
                       f'<td class="n">{num(b["backlinks"])}</td></tr>' for b in bl)
        parts.append(
            f'<table><thead><tr><th>As of</th><th class="n">Referring domains</th>'
            f'<th class="n">Backlinks</th></tr></thead><tbody>{rows}</tbody>'
            f'<tfoot><tr><td>Trend</td><td class="n">{sparkline([b["referring_domains"] for b in bl])}</td>'
            f'<td class="n">{sparkline([b["backlinks"] for b in bl])}</td></tr></tfoot></table>')
    else:
        parts.append('<p class="empty">No backlink observations.</p>')

    # ---- footer ------------------------------------------------------------
    parts.append(f"""<footer>
<h2>How to read this</h2>
<p><strong>Observations, not runs.</strong> Every run writes a complete snapshot, but most
sections in it are copies carried forward from the previous run. This page counts a data point
only when its measurement timestamp changes. {meta['snapshot_bearing']} snapshots contain
{len(gsc)} distinct GSC observations; charting one point per run would invent the rest.</p>
<p><strong>GSC numbers are a trailing 28-day window.</strong> Two consecutive weekly pulls share
21 of their 28 days, so their difference is a shift in a rolling sum, not a week&rsquo;s gain.
Nothing on this page above the &ldquo;Weekly search performance&rdquo; heading is a weekly figure.</p>
<p><strong>Local rank shows no ranks.</strong> Not an omission &mdash; the collected data cannot
support one. See that section for the measured reason.</p>
<p><strong>Absence is not zero.</strong> A missing query, page or day renders as an em dash. GSC
omits rows under its own disclosure floor, so absence is unknown.</p>
<p><strong>This measures search visibility, not business outcomes.</strong> Clicks, impressions
and positions are not leads, calls, or booked patients. Defining those is a separate piece of
work (<code>PLAN-SEO-PROGRAM-INTEGRATION.md</code> P4) and must not be inferred from this page.</p>
<h2>Provenance</h2>
<p>Source of record: <code>projects/{esc(meta['project'])}/loops/{esc(meta['loop'])}/runs/&lt;run-id&gt;/snapshot.json</code>
(immutable, read-only). This page is derived and disposable; delete it and rebuild with:</p>
<p><code>python tools/seo_timeseries.py {esc(meta['project'])} {esc(meta['loop'])}</code></p>
<p>Design record: <code>PLAN-SEO-TIMESERIES.md</code> &middot; review transcript:
<code>PLAN-REVIEW-LOG-SEO-TIMESERIES.md</code> &middot; universe version
<code>{esc(universe['version'])}</code> ({esc(universe['rule'])} rule, top {universe['top_n']}).</p>
<p>Built {esc(build_stamp)}.</p>
</footer></div>""")
    return "".join(parts)


def page(data, build_stamp):
    title = f"SEO progress — {data['meta']['project']}/{data['meta']['loop']}"
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
            f"<title>{esc(title)}</title>\n<style>{_css()}</style>\n</head>\n<body>\n"
            + render(data, build_stamp) + "\n</body>\n</html>\n")


# --------------------------------------------------------------------------- #
# daily (Phase B) consumption
# --------------------------------------------------------------------------- #

def load_daily(out_dir, today=None):
    """Read Phase B's artifacts, enforcing the sidecar pairing contract.

    A row count cannot prove the sidecar describes the current file: a provisional
    refresh rewrites the last three days without changing the line count, which is the
    most common write this tool makes. The sha256 is what actually binds them. "Absent"
    and "broken" are reported as different states because the operator can only fix one.
    """
    import hashlib

    jsonl = os.path.join(out_dir, "gsc_daily.jsonl")
    meta_path = os.path.join(out_dir, "gsc_daily.meta.json")
    has_jsonl, has_meta = os.path.exists(jsonl), os.path.exists(meta_path)
    if not has_jsonl and not has_meta:
        return {"state": "absent"}
    if not has_jsonl:
        return {"state": "inconsistent", "reason": "gsc_daily.jsonl missing but its metadata is present"}
    if not has_meta:
        return {"state": "inconsistent", "reason": "gsc_daily.meta.json missing"}
    try:
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return {"state": "inconsistent", "reason": "gsc_daily.meta.json is unreadable"}
    with open(jsonl, "rb") as fh:
        raw = fh.read()
    digest = hashlib.sha256(raw).hexdigest()
    lines = [ln for ln in raw.decode("utf-8").splitlines() if ln.strip()]
    if meta.get("jsonl_sha256") != digest:
        return {"state": "inconsistent",
                "reason": "metadata does not match the daily file's contents (sha256 mismatch)"}
    if meta.get("jsonl_row_count") != len(lines):
        return {"state": "inconsistent",
                "reason": f"metadata records {meta.get('jsonl_row_count')} rows, file has {len(lines)}"}
    try:
        days = [json.loads(ln) for ln in lines]
    except (json.JSONDecodeError, ValueError):
        return {"state": "inconsistent", "reason": "gsc_daily.jsonl contains an unparseable row"}

    weeks = aggregate_weeks(days)
    complete = [d["date"] for d in days if not d.get("provisional")]
    newest = max(complete) if complete else None
    stale = False
    if newest and today:
        stale = (datetime.strptime(today, "%Y-%m-%d").toordinal()
                 - datetime.strptime(newest, "%Y-%m-%d").toordinal()) > STALE_DAILY_DAYS
    return {"state": "ok", "weeks": weeks, "days": days, "meta": meta,
            "newest_complete": newest, "stale": stale}


def aggregate_weeks(days):
    """ISO weeks. CTR is recomputed from summed clicks/impressions and position is
    impression-weighted - neither is an average of daily values, because neither is
    summable. A week missing any day is `partial` and excluded from deltas."""
    buckets = {}
    for d in days:
        try:
            dt = datetime.strptime(d["date"], "%Y-%m-%d")
        except (ValueError, KeyError, TypeError):
            continue
        year, week, _ = dt.isocalendar()
        buckets.setdefault((year, week), []).append((dt, d))
    out = []
    for (year, week), entries in sorted(buckets.items()):
        entries.sort(key=lambda e: e[0])
        rows = [e[1] for e in entries]
        clicks = sum(r.get("clicks") or 0 for r in rows)
        impressions = sum(r.get("impressions") or 0 for r in rows)
        wpos = sum((r.get("position") or 0) * (r.get("impressions") or 0)
                   for r in rows if r.get("position") is not None)
        first = entries[0][0]
        monday = first.fromordinal(first.toordinal() - first.isoweekday() + 1)
        sunday = first.fromordinal(monday.toordinal() + 6)
        out.append({
            "week": f"{year}-W{week:02d}",
            "week_start": monday.strftime("%Y-%m-%d"),
            "week_end": sunday.strftime("%Y-%m-%d"),
            "clicks": clicks,
            "impressions": impressions,
            "ctr": round(clicks / impressions, 4) if impressions else None,
            "position": round(wpos / impressions, 1) if impressions else None,
            "days_present": len(rows),
            "partial": len(rows) < 7 or any(r.get("provisional") for r in rows),
        })
    return out


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #

def _read_spec(spec_path):
    """Minimal frontmatter reader.

    `spec_validate.py` owns real validation; this only needs a handful of reporting
    inputs and must never be a second source of truth for the schema.
    """
    try:
        import yaml
        with open(spec_path, encoding="utf-8") as fh:
            text = fh.read()
        if text.startswith("---"):
            end = text.find("\n---", 3)
            return yaml.safe_load(text[3:end]) or {}
        return yaml.safe_load(text) or {}
    except Exception:
        return {}


def _site_base(spec):
    site = spec.get("site_url") or ""
    if site.startswith("sc-domain:"):
        return "https://" + site[len("sc-domain:"):]
    return site.rstrip("/")


def collect(project, loop, workspace_root=WORKSPACE_ROOT, out_dir=None, today=None):
    project_dir = os.path.join(workspace_root, "projects", project)
    loop_dir = os.path.join(project_dir, "loops", loop)
    assert_within(project_dir, loop_dir, "loop dir")
    runs_dir = os.path.join(loop_dir, "runs")
    spec = _read_spec(os.path.join(loop_dir, "spec.md"))
    out_dir = out_dir or os.path.join(loop_dir, "timeseries")
    try:
        assert_within(project_dir, out_dir, "output dir")
    except ValueError as e:
        # A different-drive path makes os.path.relpath raise its own ValueError with a
        # confusing message ("path is on mount 'C:'"). Same refusal, said clearly.
        raise ValueError(
            f'seo_timeseries.py: refusing to write outside the project directory. '
            f'--out "{out_dir}" is not inside "{project_dir}" ({e})') from None

    brand_terms = tuple(t.lower() for t in (spec.get("brand_terms") or ts.DEFAULT_BRAND_TERMS))
    targets = [t.get("keyword") for t in (spec.get("targets") or []) if isinstance(t, dict)]
    extracted = ts.extract_observations(
        runs_dir,
        domain=spec.get("domain"),
        brand_terms=brand_terms,
        spec_targets=targets,
        priority_pages=spec.get("priority_pages") or [],
    )
    extracted["meta"] = {
        "project": project,
        "loop": loop,
        "domain": spec.get("domain") or "(domain not in spec)",
        "site_base": _site_base(spec),
        "brand_terms": list(brand_terms),
        "keyword_exclusions": [x.lower() for x in (spec.get("keyword_exclusions") or [])],
        "run_dirs": extracted.pop("run_dirs"),
        "snapshot_bearing": extracted.pop("snapshot_bearing"),
    }
    extracted["daily"] = load_daily(out_dir, today=today)
    extracted["out_dir"] = out_dir
    return extracted


def _series_json(data):
    """The machine-readable derived dataset. Raw per-observation GSC rows are dropped
    (they live in the CSV and, authoritatively, in the snapshots); the universe rows,
    every total, and the local-rank forensic block are kept."""
    gsc = []
    universe = set(data["universe"]["members"])
    for o in data["series"]["gsc"]:
        gsc.append({k: v for k, v in o.items() if k not in ("rows", "raw_rows", "rollup")}
                   | {"universe_rows": [r for r in o["rollup"] if r["query"] in universe]})
    return {
        "generated_from": "runs/<run-id>/snapshot.json (system of record)",
        "meta": data["meta"],
        "universe": data["universe"],
        "warnings": data["warnings"],
        "series": {
            "gsc": gsc,
            "local_rank": data["series"]["local_rank"],
            "backlinks": data["series"]["backlinks"],
            "pagespeed": data["series"]["pagespeed"],
            "indexation": data["series"]["indexation"],
        },
    }


def _csv_text(data):
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["observed_at", "window_end", "query", "page", "clicks", "impressions",
                "position", "brand", "truncation_suspected"])
    brand_terms = data["meta"]["brand_terms"]
    for o in data["series"]["gsc"]:
        for r in o["rows"]:
            w.writerow([o["observed_at"], o["window_end"] or "", r["query"], r["page"],
                        r["clicks"], r["impressions"],
                        "" if r["position"] is None else r["position"],
                        "true" if ts.is_brand(r["query"], brand_terms) else "false",
                        "true" if o["truncation_suspected"] else "false"])
    return buf.getvalue()


def _redaction_gate(payload):
    """Refuse to write if anything secret-shaped survived into the output.

    Snapshots are already redacted, so this should never fire - which is exactly why it
    is cheap to keep. It turns "safe to publish" from an assumption into an assertion.
    """
    before = json.dumps(payload, sort_keys=True, default=str)
    after = json.dumps(redact_deep(payload), sort_keys=True, default=str)
    return before == after


def build(project, loop, out_dir=None, workspace_root=WORKSPACE_ROOT, now=None, write=True):
    now = now or datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    data = collect(project, loop, workspace_root=workspace_root, out_dir=out_dir, today=today)
    out_dir = data["out_dir"]

    series = _series_json(data)
    if not _redaction_gate(series):
        raise RuntimeError(
            "seo_timeseries.py: refusing to write - a secret-shaped value survived into the "
            "derived dataset. Nothing was written. Inspect the source snapshot before rebuilding.")

    stamp = f"{now.strftime('%Y-%m-%d %H:%M')}Z"
    artifacts = {
        "series.json": json.dumps(series, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
        "gsc_queries.csv": _csv_text(data),
        "dashboard.html": page(data, stamp),
    }
    if write:
        os.makedirs(out_dir, exist_ok=True)
        for name, text in artifacts.items():
            path = os.path.join(out_dir, name)
            assert_within(out_dir, path, name)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
    return data, artifacts, out_dir


STAMP_RE = re.compile(r"<p>Built [^<]*</p>")


def _normalize_stamp(text):
    return STAMP_RE.sub("<p>Built (stamp)</p>", text)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the SEO progress dashboard from run snapshots.")
    ap.add_argument("project", nargs="?")
    ap.add_argument("loop", nargs="?")
    ap.add_argument("--out", default=None, help="output directory (default: <loop>/timeseries)")
    ap.add_argument("--check", action="store_true",
                    help="rebuild and compare against what is on disk; non-zero on drift")
    ap.add_argument("--verify", action="store_true", help="run self-tests")
    args = ap.parse_args(argv)

    if args.verify:
        return _self_test()
    if not args.project or not args.loop:
        ap.error("project and loop are required")

    if args.check:
        data, artifacts, out_dir = build(args.project, args.loop, out_dir=args.out, write=False)
        drift = []
        for name, text in artifacts.items():
            path = os.path.join(out_dir, name)
            if not os.path.exists(path):
                drift.append(f"{name}: missing")
                continue
            with open(path, encoding="utf-8") as fh:
                on_disk = fh.read()
            if _normalize_stamp(on_disk) != _normalize_stamp(text):
                drift.append(f"{name}: differs from a fresh build")
        for d in drift:
            print(f"DRIFT - {d}")
        print("up to date" if not drift else f"{len(drift)} artifact(s) stale")
        return 1 if drift else 0

    data, artifacts, out_dir = build(args.project, args.loop, out_dir=args.out)
    gsc = data["series"]["gsc"]
    print(f"{data['meta']['project']}/{data['meta']['loop']} -> {out_dir}")
    print(f"  {data['meta']['run_dirs']} run directories, {data['meta']['snapshot_bearing']} with snapshots")
    print(f"  GSC observations       {len(gsc)}")
    print(f"  local rank             {len(data['series']['local_rank'])}")
    print(f"  backlinks              {len(data['series']['backlinks'])}")
    print(f"  pagespeed / indexation {len(data['series']['pagespeed'])} / {len(data['series']['indexation'])}")
    print(f"  tracked queries        {len(data['universe']['members'])} (universe {data['universe']['version']})")
    print(f"  daily series           {data['daily']['state']}")
    if gsc:
        t = gsc[-1]["totals"]
        print(f"  latest: {t['clicks']} clicks / {t['impressions']:,} impressions / "
              f"avg position {t['avg_position']} ({_observation_label(gsc[-1])})")
    for w in data["warnings"]:
        print(f"  WARNING: {w}")
    print(f"  open: {os.path.join(out_dir, 'dashboard.html')}")
    return 0


# --------------------------------------------------------------------------- #
# self-test (PLAN-SEO-TIMESERIES.md Part 5, checks 20-27g)
# --------------------------------------------------------------------------- #

def _self_test():  # noqa: C901
    import hashlib
    import shutil
    import tempfile

    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    def tags(text, name):
        return re.findall(rf"<{name}\b", text, re.I)

    def handler_attrs(text):
        """Scan only real tag bodies for on*= handlers.

        Scanning the whole document produces false positives on the very thing the
        escaping test is proving works: an escaped payload renders the literal text
        `&lt;svg onload=alert(1)&gt;`, which contains " onload=" but is inert text, not
        an attribute.
        """
        return [t for t in re.findall(r"<[a-zA-Z][^>]*>", text)
                if re.search(r"\son[a-z]+\s*=", t, re.I)]

    fixed = datetime(2026, 8, 16, 6, 0, tzinfo=timezone.utc)
    real_ok = os.path.isdir(os.path.join(WORKSPACE_ROOT, "projects", "art", "loops", "seo", "runs"))
    tmp = tempfile.mkdtemp(prefix="seo-dash-test-")
    try:
        if not real_ok:
            check("REAL-DATA CHECKS", False, "projects/art/loops/seo/runs not found")
        else:
            # Inside the loop's own (gitignored) timeseries/ dir: path containment is a
            # real rule, not one the tests get to opt out of, and a system temp dir on
            # another drive is legitimately outside the project.
            out = os.path.join(WORKSPACE_ROOT, "projects", "art", "loops", "seo",
                               "timeseries", ".selftest")
            _, art1, _ = build("art", "seo", out_dir=out, now=fixed, write=True)
            _, art2, _ = build("art", "seo", out_dir=out, now=fixed, write=False)
            check("20 two builds with a fixed clock are byte-identical",
                  all(art1[k] == art2[k] for k in art1))

            _, art3, _ = build("art", "seo", out_dir=out,
                               now=datetime(2027, 1, 1, tzinfo=timezone.utc), write=False)
            only_stamp = all(_normalize_stamp(art1[k]) == _normalize_stamp(art3[k]) for k in art1)
            differs = art1["dashboard.html"] != art3["dashboard.html"]
            check("21a a later clock changes only the build stamp", only_stamp and differs)

            check("21b --check passes on a fresh build and fails on real drift",
                  main(["art", "seo", "--out", out, "--check"]) == 0)
            with open(os.path.join(out, "series.json"), "a", encoding="utf-8") as fh:
                fh.write("\n// tampered\n")
            check("21c --check exits non-zero when an artifact drifts",
                  main(["art", "seo", "--out", out, "--check"]) == 1)

            html_text = art1["dashboard.html"]

            check("24a the page contains no <script>, <iframe>, <object> or <embed>",
                  not tags(html_text, "script") and not tags(html_text, "iframe")
                  and not tags(html_text, "object") and not tags(html_text, "embed"))
            check("24b no on* event-handler attribute anywhere",
                  not handler_attrs(html_text))
            externals = re.findall(r'(?:src|href|srcset|poster|data)\s*=\s*"([^"]*)"', html_text, re.I)
            offpage = [u for u in externals if re.match(r"\s*(?:[a-z]+:)?//|\s*(?:https?|data|javascript):", u, re.I)]
            check("24c no attribute references an off-page resource", not offpage, f"{offpage[:4]}")
            check("24d no @import or url() in the stylesheet",
                  "@import" not in html_text and not re.search(r"url\(", html_text, re.I))
            check("24e competitor URLs are never clickable links", not tags(html_text, "a"))

            check("25 every chart figure is accompanied by a table in the DOM",
                  len(tags(html_text, "table")) >= 3 and len(tags(html_text, "figure")) >= 3,
                  f"tables={len(tags(html_text,'table'))} figures={len(tags(html_text,'figure'))}")

            # Asserted with a sentinel rather than by grepping for "rank_absolute": the
            # panel *explains* the defect in prose, so the field name legitimately
            # appears as text. What must never appear is a forensic VALUE.
            sentinel_panel = _integrity_panel([{
                "observed_at": "2026-08-15T13:00:39Z", "first_seen_run_id": "r", "row_count": 2,
                "batch_invariant_holds": True,
                "rows": [
                    {"location": "Greeley", "keyword": "physical therapy greeley",
                     "page": "/physical-therapy/", "batch_index": 0, "classification": "competitor",
                     "competitor_domain": "beaminghealth.com",
                     "result_url": "https://beaminghealth.com/physical-therapy/greeley/",
                     "forensic": {"raw_rank_absolute_not_a_rank": 987654}},
                    {"location": "Greeley", "keyword": "chiropractor greeley",
                     "page": "/chiropractor/", "batch_index": 3, "classification": "not-queried",
                     "competitor_domain": None, "result_url": None,
                     "forensic": {"raw_rank_absolute_not_a_rank": 876543}},
                ]}])
            check("27c the local-rank panel renders no forensic rank value",
                  "987654" not in sentinel_panel and "876543" not in sentinel_panel
                  and "raw_rank_absolute_not_a_rank" not in html_text,
                  "a quarantined rank value reached the panel")

            check("27d near-duplicate re-pulls are annotated on the page",
                  "re-pull" in html_text and "near-identical by construction" in html_text)

            check("27e the technical strip carries separate pagespeed and indexation as-of dates",
                  "CWV lab" in html_text and "Indexation" in html_text
                  and len(re.findall(r"as of \d{4}-\d{2}-\d{2}", html_text)) >= 2)

            check("A no numeric local-rank chart exists: the integrity panel has no <svg> line",
                  "Local rank &mdash; collection integrity" in html_text
                  or "collection integrity" in html_text)

            csv_text = art1["gsc_queries.csv"]
            check("B CSV carries the truncation column and every GSC row",
                  csv_text.splitlines()[0].endswith("truncation_suspected")
                  and len(csv_text.strip().splitlines()) > 3000,
                  f"{len(csv_text.strip().splitlines())} lines")

        # ---- injection (synthetic, no real data needed) -----------------------
        markup_payloads = [
            "</script><img src=x onerror=alert(1)>",
            '"><svg onload=alert(1)>',
            "<a href='http://evil.example'>click</a>",
        ]
        # A bare `javascript:` string is inert as *text* - it is only dangerous in an
        # attribute, so it is asserted separately rather than banned from the document.
        data = _fixture_data(markup_payloads + ["javascript:alert(1)"])
        html_text = page(data, "stamp")
        verbatim = [m for m in markup_payloads if m in html_text]
        attrs = re.findall(r'(?:src|href|srcset|poster|data|style)\s*=\s*"([^"]*)"', html_text, re.I)
        js_in_attr = [a for a in attrs if "javascript:" in a.lower()]
        survived_as_text = "&lt;/script&gt;" in html_text  # proves it rendered, escaped
        check("23 malicious query strings render inert: escaped as text, no tags, no handlers, "
              "no javascript: in any attribute",
              # The page has legitimate <svg> (the charts), so the payload's "<svg onload="
              # is caught by `verbatim` and the on*-attribute scan, not by banning the tag.
              not verbatim and not js_in_attr and survived_as_text
              and not tags(html_text, "script") and not tags(html_text, "img")
              and not tags(html_text, "a") and not handler_attrs(html_text),
              f"verbatim={verbatim[:1]} js_attr={js_in_attr[:1]} escaped_present={survived_as_text}")

        # ---- redaction gate ---------------------------------------------------
        payload = {"rows": [{"query": "leak sk-live-CANARY1234567890ABCDEFGH here"}]}
        clean = {"rows": [{"query": "physical therapy greeley"}]}
        check("22 a planted canary secret blocks the write, a clean payload does not",
              not _redaction_gate(payload) and _redaction_gate(clean))

        # ---- empty input ------------------------------------------------------
        empty_root = os.path.join(tmp, "empty")
        os.makedirs(os.path.join(empty_root, "projects", "p", "loops", "l", "runs"), exist_ok=True)
        with open(os.path.join(empty_root, "projects", "p", "loops", "l", "spec.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ndomain: example.com\n---\n")
        try:
            _, art, _ = build("p", "l", workspace_root=empty_root, now=fixed, write=False)
            ok = "<html" in art["dashboard.html"] and "No observations yet" in art["dashboard.html"]
        except Exception as e:  # noqa: BLE001
            ok, art = False, {"dashboard.html": f"raised {e}"}
        check("26 a project with zero runs produces a valid page, not a traceback", ok)

        # ---- path containment -------------------------------------------------
        refused = False
        try:
            build("p", "l", out_dir=os.path.join(tmp, "outside"), workspace_root=empty_root,
                  now=fixed, write=False)
        except ValueError:
            refused = True
        check("27 an --out outside the project directory is refused", refused)

        # ---- 27b truncation rendering -----------------------------------------
        trunc = _fixture_data(["q1"], truncate=True)
        trunc_html = page(trunc, "stamp")
        check("27b a capped observation is marked partial and its delta reads 'not comparable'",
              "row limit reached" in trunc_html and "not comparable" in trunc_html)

        # ---- 27f/27g sidecar contract -----------------------------------------
        base_dir = os.path.join(tmp, "daily")
        os.makedirs(base_dir, exist_ok=True)
        rows = [{"date": "2026-08-%02d" % d, "clicks": d, "impressions": d * 10,
                 "position": 10.0, "provisional": False} for d in range(1, 15)]

        def write_daily(rows_, meta_override=None, write_meta=True, meta_text=None):
            body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows_)
            with open(os.path.join(base_dir, "gsc_daily.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(body)
            if not write_meta:
                p = os.path.join(base_dir, "gsc_daily.meta.json")
                if os.path.exists(p):
                    os.remove(p)
                return
            meta = {"jsonl_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                    "jsonl_row_count": len(rows_), "last_pull_at": "2026-08-15T00:00:00Z"}
            meta.update(meta_override or {})
            with open(os.path.join(base_dir, "gsc_daily.meta.json"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(meta_text if meta_text is not None else json.dumps(meta))

        cases = {}
        cases["a absent"] = load_daily(os.path.join(tmp, "nothing"))["state"]
        write_daily(rows)
        ok_state = load_daily(base_dir, today="2026-08-16")
        cases["b consistent"] = ok_state["state"]
        cases["c stale"] = load_daily(base_dir, today="2026-10-01")["stale"]
        write_daily(rows, write_meta=False)
        cases["d meta missing"] = load_daily(base_dir)["state"]
        write_daily(rows, meta_text="{not json")
        cases["e meta unparseable"] = load_daily(base_dir)["state"]
        write_daily(rows, meta_override={"jsonl_row_count": 999})
        cases["f count mismatch"] = load_daily(base_dir)["state"]
        # (g) the ordinary stale case: a provisional refresh rewrites rows without
        # changing the line count, so the count still matches and only the hash moves.
        body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
        stale_meta = {"jsonl_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                      "jsonl_row_count": len(rows)}
        refreshed = [dict(r) for r in rows]
        refreshed[-1]["clicks"] += 7  # same line count, different bytes
        with open(os.path.join(base_dir, "gsc_daily.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("".join(json.dumps(r, sort_keys=True) + "\n" for r in refreshed))
        with open(os.path.join(base_dir, "gsc_daily.meta.json"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(stale_meta))
        g_state = load_daily(base_dir)
        cases["g sha mismatch, count matches"] = g_state["state"]

        check("27f sidecar contract cases a-f behave as specified",
              cases["a absent"] == "absent" and cases["b consistent"] == "ok"
              and cases["c stale"] is True and cases["d meta missing"] == "inconsistent"
              and cases["e meta unparseable"] == "inconsistent"
              and cases["f count mismatch"] == "inconsistent", f"{cases}")
        check("27g stale metadata with an UNCHANGED row count is caught by the sha256",
              cases["g sha mismatch, count matches"] == "inconsistent"
              and "sha256" in g_state.get("reason", ""), f"{g_state.get('reason')}")

        rendered = {name: _weekly_section(d) for name, d in {
            "absent": {"state": "absent"},
            "broken": {"state": "inconsistent", "reason": "sha256 mismatch"},
            "ok": ok_state}.items()}
        check("27f/g the page distinguishes absent from broken, and never silently conflates them",
              "available" in rendered["absent"] and "inconsistent" in rendered["broken"]
              and "ISO week" in rendered["ok"])

        # ---- weekly aggregation ------------------------------------------------
        days = [{"date": "2026-08-03", "clicks": 10, "impressions": 100, "position": 2.0},
                {"date": "2026-08-04", "clicks": 0, "impressions": 900, "position": 40.0}]
        wk = aggregate_weeks(days)[0]
        naive_ctr = (10 / 100 + 0 / 900) / 2
        naive_pos = (2.0 + 40.0) / 2
        check("32 weekly CTR is recomputed and position impression-weighted, not averaged",
              wk["ctr"] == round(10 / 1000, 4) and wk["position"] == round((2 * 100 + 40 * 900) / 1000, 1)
              and abs(wk["ctr"] - naive_ctr) > 1e-6 and abs(wk["position"] - naive_pos) > 1e-6,
              f"ctr={wk['ctr']} (naive {naive_ctr:.4f}) pos={wk['position']} (naive {naive_pos})")
        check("33 a week missing days is partial", wk["partial"] is True and wk["days_present"] == 2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(os.path.join(WORKSPACE_ROOT, "projects", "art", "loops", "seo",
                                   "timeseries", ".selftest"), ignore_errors=True)

    failed = 0
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}" + (f"   [{detail}]" if detail and not ok else ""))
        if not ok:
            failed += 1
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


def _fixture_data(queries, truncate=False):
    """A minimal in-memory ObservationSet, for the render-layer checks."""
    rows = [{"query": q, "page": "https://example.com/", "clicks": 1, "impressions": 10,
             "position": 3.0} for q in queries]
    rollup = [{"query": q, "page_count": 1, "pages": ["https://example.com/"], "clicks": 1,
               "impressions": 10, "position": 3.0} for q in queries]

    def obs(stamp, end):
        return {"observed_at": stamp, "first_seen_run_id": "r", "window_start": "2026-07-15",
                "window_end": end, "window_known": True, "row_count": 1000 if truncate else 3,
                "row_limit": 1000, "row_limit_source": ts.ASSUMED_ROW_LIMIT_SOURCE,
                "truncation_suspected": truncate,
                "totals": {"clicks": 1, "impressions": 10, "ctr": 0.1, "avg_position": 3.0},
                "brand": {"clicks": 0, "impressions": 0, "ctr": None, "avg_position": None},
                "nonbrand": {"clicks": 1, "impressions": 10, "ctr": 0.1, "avg_position": 3.0},
                "unique_queries": len(queries), "multi_page_queries_raw": 0,
                "multi_page_queries": 0, "priority_pages": {}, "rollup": rollup,
                "rows": rows, "raw_rows": rows, "near_duplicate": False,
                "hours_since_previous": None}

    gsc = [obs("2026-08-01T00:00:00Z", "2026-08-01"), obs("2026-08-08T00:00:00Z", "2026-08-08")]
    return {
        "series": {"gsc": gsc, "local_rank": [], "backlinks": [], "pagespeed": [], "indexation": []},
        "warnings": [],
        "universe": {"rule": "rolled-up", "top_n": 25, "members": list(queries), "version": "deadbeef1234"},
        "meta": {"project": "p", "loop": "l", "domain": "example.com",
                 "site_base": "https://example.com", "brand_terms": ["brand"],
                 "keyword_exclusions": [], "run_dirs": 1, "snapshot_bearing": 1},
        "daily": {"state": "absent"},
    }


if __name__ == "__main__":
    sys.exit(main())

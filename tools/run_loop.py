# Run contract engine (AgentColabPlan.md "The run contract"). One call
# = one run of one loop for one project. Deterministic and testable by
# design: the judgment step (picking actions) uses a simple heuristic
# here; a human-in-the-loop skill wraps this for real proposal quality.
import json
import os
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import yaml

try:
    from . import dataforseo, dataforseo_local_pack, dataforseo_maps, gsc, pagespeed
    from .connector_registry import get_connector, is_critical
    from .lib.credentials import resolve_credential
    from .lib.errors import ConnectorError
    from .lib.event_log import append_event
    from .lib.github_compare import compare_commit_to_main
    from .lib.gsc_auth import bearer_for_secret
    from .lib.lock import acquire_lock, release_lock, log_refusal
    from .lib.paths import assert_within
    from .lib.proposals import atomic_write_json, list_proposals, load_json, proposal_path, write_proposal
    from .lib.redact import redact_deep
    from .spec_validate import validate_spec_file, extract_frontmatter
    from .snapshot import write_snapshot
    from .mock_metrics import pull_metrics as pull_mock_metrics
except ImportError:
    import dataforseo
    import dataforseo_local_pack
    import dataforseo_maps
    import gsc
    import pagespeed
    from connector_registry import get_connector, is_critical
    from lib.credentials import resolve_credential
    from lib.errors import ConnectorError
    from lib.event_log import append_event
    from lib.github_compare import compare_commit_to_main
    from lib.gsc_auth import bearer_for_secret
    from lib.lock import acquire_lock, release_lock, log_refusal
    from lib.paths import assert_within
    from lib.proposals import atomic_write_json, list_proposals, load_json, proposal_path, write_proposal
    from lib.redact import redact_deep
    from spec_validate import validate_spec_file, extract_frontmatter
    from snapshot import write_snapshot
    from mock_metrics import pull_metrics as pull_mock_metrics

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(THIS_DIR)
PROJECTS_ROOT = os.path.join(WORKSPACE_ROOT, "projects")
DEFAULT_LOCK_TTL_MINUTES = 60
MIN_LOCK_TTL_MINUTES = 1
MAX_LOCK_TTL_MINUTES = 24 * 60
LIVE_COMPARE_TARGETS = {"art": {"owner": "nateginn", "repo": "artwebsite"}}
LOCATION_DIFF_FILENAME = "locations-detected.json"
# Phase 7 (PLAN-PHASE7-CODEX-REVIEW.md item 16): cooldown widened from a
# fixed status list to "everything except a genuine terminal outcome" - the
# new, longer-lived review-subsystem intermediate states (review-pending,
# review-revision-needed, review-approved, review-held, approved-for-
# implementation) must also block a duplicate proposal on the same page,
# same as draft/reviewed/approved/implemented/applied already do.
TERMINAL_PROPOSAL_STATUSES = {"rejected", "review-rejected", "verified", "breached"}


def _now_iso(now=None):
    now = now or datetime.now(timezone.utc)
    return now.isoformat().replace("+00:00", "Z")


def _read_lock_ttl_minutes_unsafe(spec_path, fallback=DEFAULT_LOCK_TTL_MINUTES):
    try:
        with open(spec_path, "r", encoding="utf-8") as f:
            source = f.read()
        fm = extract_frontmatter(source)
        if fm is None:
            return fallback
        parsed = yaml.safe_load(fm)
        v = parsed.get("max_run_duration_minutes") if isinstance(parsed, dict) else None
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            return min(max(v, MIN_LOCK_TTL_MINUTES), MAX_LOCK_TTL_MINUTES)
    except Exception:
        pass
    return fallback


def _load_spec(spec_path):
    with open(spec_path, "r", encoding="utf-8") as f:
        source = f.read()
    return yaml.safe_load(extract_frontmatter(source))


def _load_json_safe(path, fallback):
    if not os.path.exists(path):
        return fallback
    return load_json(path)


def _list_pending_proposals(pending_dir):
    return list_proposals(pending_dir)


def _write_proposal(pending_dir, proposal):
    write_proposal(pending_dir, proposal)


def _target_key(target):
    return json.dumps(target or {}, sort_keys=True)


def _describe_target(target):
    """Human-readable target description for report.md, shape-aware like
    _cooldown_key_from_target above - an SEO proposal's target has `page`, a GBP Post
    proposal's has `location`/`topic` and no `page` at all. report.md previously assumed
    `target["page"]` unconditionally and raised KeyError on the first real GBP proposal
    (caught by tools/tests/gbp_scaffold_smoke.py, 2026-08-31)."""
    target = target or {}
    if "location" in target or "topic" in target:
        return f'{target.get("location")} / {target.get("topic")}'
    return target.get("page")


def _cooldown_key_from_target(target):
    """Cooldown is keyed on whichever identity fields a target actually has - `page`
    for an SEO proposal, `{location, topic}` for a GBP Post proposal (plan Phase 3;
    a GBP proposal has no `page` at all). Dispatching on which keys are PRESENT on the
    target, rather than on the caller's loop name, means this stays correct even where
    `proposals` is read generically (this function is called for every proposal in a
    loop's pending/ regardless of type) - GBP's `{location, topic}` target has no `page`
    key, so falling through to the SEO shape's `{"page": None}` for every GBP proposal
    would have collapsed every distinct GBP topic into one shared, wrong cooldown key."""
    target = target or {}
    if "location" in target or "topic" in target:
        return _target_key({"location": target.get("location"), "topic": target.get("topic")})
    return _target_key({"page": target.get("page")})


def _compare(value, comparator, threshold):
    if comparator == "<":
        return value < threshold
    if comparator == ">":
        return value > threshold
    if comparator == "<=":
        return value <= threshold
    if comparator == ">=":
        return value >= threshold
    if comparator == "==":
        return value == threshold
    raise ValueError(f"unknown comparator {comparator}")


def _aliases_used(spec, inputs=None):
    aliases = spec.get("credential_aliases") or {}
    out = {}
    for input_name in inputs or (spec.get("inputs") or []):
        alias_key = (get_connector(input_name) or {}).get("credential_alias")
        out[input_name] = aliases.get(alias_key) if alias_key else None
    return out


def _page_path(page):
    if isinstance(page, str) and (page.startswith("http://") or page.startswith("https://")):
        return urlsplit(page).path or "/"
    return page


def _normalize_match_text(value):
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKC", value).strip().casefold()
    return re.sub(r"\s+", " ", text)


def _merge_metrics(results):
    by_tool = {tool: metrics for tool, _alias, metrics in results}
    primary = by_tool.get("gsc") or by_tool.get("mock-metrics") or results[0][2]

    merged = dict(primary)
    secret_map = {}
    for _tool, _alias, metrics in results:
        secret_map.update(metrics.get("secretMap") or {})
    merged["secretMap"] = secret_map
    merged["sources"] = [tool for tool, _alias, _metrics in results]

    serp = by_tool.get("dataforseo")
    if serp is not None and serp is not primary:
        serp_by_target = {(k.get("keyword"), _page_path(k.get("page"))): k.get("position") for k in serp.get("keywords") or []}
        merged["keywords"] = [dict(k) for k in merged.get("keywords") or []]
        for k in merged["keywords"]:
            serp_position = serp_by_target.get((k.get("keyword"), _page_path(k.get("page"))))
            if serp_position is not None:
                k["serp_position"] = serp_position
    return merged


def _resolve_run_mode(spec, run_name=None):
    if not run_name:
        return {"name": "full", "mode": "full", "inputs": list(spec.get("inputs") or [])}
    for schedule in spec.get("additional_schedules") or []:
        if schedule.get("name") == run_name:
            return {"name": run_name, "mode": schedule.get("mode") or "full", "inputs": list(schedule.get("inputs") or [])}
    raise ValueError(f'run_loop.py: unknown run name "{run_name}"')


def _latest_snapshot_sections(runs_dir, current_run_id=None):
    if not os.path.isdir(runs_dir):
        return None
    candidates = []
    for name in os.listdir(runs_dir):
        if current_run_id and name == current_run_id:
            continue
        snapshot_path = os.path.join(runs_dir, name, "snapshot.json")
        if os.path.exists(snapshot_path):
            candidates.append((name, snapshot_path))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return load_json(candidates[0][1])


# The history cutoff (Issue #1). A section whose meaning changed is not comparable to
# its older self, and `_evaluate_attention` compares snapshots on disk regardless of what
# any document says - so the cutoff has to be code, not prose.
#
# Keyed on the section's own `schema_version` rather than on a baseline run id, because
# `_build_snapshot` carries every un-refreshed section forward verbatim: a pre-fix
# local_rank section lives on inside run directories created long after the fix, and a
# run-id cutoff would hand it straight to the comparison. The version travels with the
# data it describes, so it cannot be outrun by a carry-forward.
MIN_SECTION_SCHEMA_VERSION = {
    # v1 rows reported rank_absolute (ads/local-pack/PAA inclusive) matched by bare URL
    # substring with no domain check, and only ever queried the first target of each
    # batched request. See dataforseo.LOCAL_RANK_SCHEMA_VERSION.
    "local_rank": 2,
}


def _section_history(runs_dir, section_name, current_run_id):
    history = []
    if not os.path.isdir(runs_dir):
        return history
    min_version = MIN_SECTION_SCHEMA_VERSION.get(section_name)
    run_names = sorted(os.listdir(runs_dir), reverse=True)
    seen_as_of = set()
    for run_name in run_names:
        if run_name == current_run_id:
            continue
        snapshot_path = os.path.join(runs_dir, run_name, "snapshot.json")
        if not os.path.exists(snapshot_path):
            continue
        snapshot = load_json(snapshot_path)
        section = snapshot.get(section_name)
        if not isinstance(section, dict):
            continue
        if min_version is not None:
            version = section.get("schema_version")
            # Absent (v1, which never wrote the key) or non-integer both fail closed.
            if not isinstance(version, int) or isinstance(version, bool) or version < min_version:
                continue
        as_of = section.get("as_of")
        if not as_of or as_of in seen_as_of:
            continue
        seen_as_of.add(as_of)
        history.append(section)
    return history


def _build_snapshot(previous_snapshot, fetched_sections):
    snapshot = {
        "schema_version": 2,
        "search_analytics": None,
        "local_rank": None,
        "backlinks": None,
        "technical_health": None,
        # GBP Posts plan Phase 1: own-listing-only Maps rank / local-pack
        # presence, carried forward exactly like every other enrichment
        # section - a degraded connector must never blank an existing
        # observation, only leave it visibly stale (its own `as_of`).
        "maps_rank": None,
        "local_pack": None,
    }
    if previous_snapshot:
        for key in ("search_analytics", "local_rank", "backlinks", "technical_health", "maps_rank", "local_pack"):
            snapshot[key] = previous_snapshot.get(key)
    for key, value in fetched_sections.items():
        if value is not None:
            snapshot[key] = value
    return snapshot


def _domain_from_site_url(site_url):
    if isinstance(site_url, str) and site_url.startswith("sc-domain:"):
        return "https://" + site_url[len("sc-domain:") :]
    return site_url.rstrip("/")


def _absolute_pages(spec):
    site_base = _domain_from_site_url(spec.get("site_url") or "").rstrip("/")
    out = []
    for page in spec.get("priority_pages") or []:
        out.append(page if page.startswith(("http://", "https://")) else f"{site_base}{page}")
    return out


def _local_stamp(iso_utc, short=False):
    """Render a UTC ISO-8601 stamp in this machine's local time, for humans.

    Storage stays UTC everywhere on purpose and must not be "fixed" to local:
    run IDs are used as lexicographic sort keys (`_latest_snapshot_sections`,
    `_section_history`) and lock staleness is age arithmetic. At the autumn DST
    rollback local time repeats an hour, so a local-stamped run ID would sort a
    later run before an earlier one - silently selecting the wrong previous
    snapshot - and an hour-shifted age could read a live lock as stale and let
    two runs proceed at once. Only the human-facing surfaces are localized."""
    if not iso_utc:
        return "n/a"
    try:
        dt = datetime.fromisoformat(str(iso_utc).replace("Z", "+00:00")).astimezone()
    except (ValueError, TypeError):
        return str(iso_utc)
    offset = dt.strftime("%z") or "+0000"
    pattern = "%a %Y-%m-%d %H:%M" if short else "%a %Y-%m-%d %H:%M:%S"
    return f"{dt.strftime(pattern)} local (UTC{offset[:3]}:{offset[3:]})"


def _previous_technical_health(previous_snapshot):
    """The last snapshot's technical_health, used to seed this run's section so
    that a *partial* refresh (pagespeed succeeded, indexation degraded, or vice
    versa) carries the other half forward instead of blanking it. Without this,
    degrading one of the two would silently replace real rows with []."""
    section = (previous_snapshot or {}).get("technical_health")
    return section if isinstance(section, dict) else {}


def _technical_health_section(current, pagespeed_as_of=None, pagespeed_rows=None, indexation_as_of=None, indexation_rows=None, sitemaps=None, secret_map=None):
    """Merge one half of technical_health onto whatever is already there.
    The section-level `as_of` is the *newest* of the two sub-stamps: it is what
    `_section_history` de-duplicates snapshots by, so pinning it to the older
    (possibly carried-forward) half would make successive refreshes look like
    the same reading and drop them from history."""
    pagespeed_as_of = pagespeed_as_of or current.get("pagespeed_as_of")
    indexation_as_of = indexation_as_of or current.get("indexation_as_of")
    stamps = [s for s in (pagespeed_as_of, indexation_as_of) if s]
    return {
        "as_of": max(stamps) if stamps else None,
        "pagespeed_as_of": pagespeed_as_of,
        "indexation_as_of": indexation_as_of,
        "pagespeed_rows": (pagespeed_rows if pagespeed_rows is not None else current.get("pagespeed_rows")) or [],
        "indexation_rows": (indexation_rows if indexation_rows is not None else current.get("indexation_rows")) or [],
        "sitemaps": (sitemaps if sitemaps is not None else current.get("sitemaps")) or [],
        "secretMap": {**(current.get("secretMap") or {}), **(secret_map or {})},
    }


def _dispatch_connector(input_name, handler, spec, aliases, resolver, http_get, http_post, now, scenario, previous_snapshot, fetched_sections, analytics_results, tool_calls):
    """Run one connector, appending its results to the shared accumulators.
    Raises ConnectorError on failure; the caller decides whether that aborts
    the run or merely degrades it (see CONNECTOR_REGISTRY's `critical`)."""
    if handler == "mock":
        metrics = pull_mock_metrics(scenario=scenario, credential_alias=aliases["mock"])
        analytics_results.append(("mock-metrics", aliases["mock"], metrics))
        tool_calls.append({"tool": "mock-metrics", "args": {"scenario": scenario}, "at": _now_iso(), "ok": True})
    elif handler == "gsc-search-analytics":
        window_days = spec.get("metrics_window_days") or 28
        end_date = now.date()
        start_date = end_date - timedelta(days=window_days - 1)
        args = {"site_url": spec.get("site_url"), "start_date": start_date.isoformat(), "end_date": end_date.isoformat()}
        try:
            metrics = gsc.pull_metrics(
                credential_alias=aliases[input_name],
                resolve_credential=lambda a: bearer_for_secret(resolver(a)),
                dimensions=["query", "page"],
                http_post=http_post or gsc._default_http_post,
                **args,
            )
        except Exception as e:
            raise ConnectorError(f"gsc connector failed: {e}", raw_secrets={}, tool_name="gsc") from None
        analytics_results.append(("gsc", aliases[input_name], metrics))
        tool_calls.append({"tool": "gsc", "args": args, "at": _now_iso(), "ok": True})
    elif handler == "dataforseo-serp":
        args = {
            "targets": spec.get("targets"),
            # Without the domain a SERP result cannot be attributed to this client, which
            # is defect D1 - the connector refuses rather than falling back to substring
            # matching, and connector_registry lists "domain" under this input's requires.
            "domain": spec.get("domain"),
            "location_code": spec.get("location_code") or 2840,
            "language_code": spec.get("language_code") or "en",
            "device": spec.get("device") or "desktop",
        }
        try:
            metrics = dataforseo.pull_metrics(
                credential_alias=aliases[input_name],
                resolve_credential=resolver,
                http_post=http_post or dataforseo._default_http_post,
                **args,
            )
        except Exception as e:
            raise ConnectorError(f"dataforseo connector failed: {e}", raw_secrets={}, tool_name="dataforseo") from None
        analytics_results.append(("dataforseo", aliases[input_name], metrics))
        tool_calls.append({"tool": "dataforseo", "args": {"targets": args["targets"]}, "at": _now_iso(), "ok": True})
    elif handler == "dataforseo-local-rank":
        try:
            fetched_sections["local_rank"] = dataforseo.pull_local_rank(
                credential_alias=aliases[input_name],
                resolve_credential=resolver,
                targets=spec.get("targets"),
                locations=spec.get("locations"),
                domain=spec.get("domain"),
                language_code=spec.get("language_code") or "en",
                device=spec.get("device") or "desktop",
                http_post=http_post or dataforseo._default_http_post,
                http_get=http_get or dataforseo._default_http_get,
            )
        except Exception as e:
            raise ConnectorError(f"dataforseo-local-rank connector failed: {e}", raw_secrets={}, tool_name="dataforseo-local-rank") from None
        tool_calls.append({"tool": "dataforseo-local-rank", "args": {"locations": [l.get("name") for l in spec.get("locations") or []], "targets": spec.get("targets")}, "at": _now_iso(), "ok": True})
    elif handler == "dataforseo-maps-rank":
        try:
            fetched_sections["maps_rank"] = dataforseo_maps.pull_maps_rank(
                credential_alias=aliases[input_name],
                resolve_credential=resolver,
                targets=spec.get("gbp_targets"),
                locations=spec.get("locations"),
                language_code=spec.get("language_code") or "en",
                device=spec.get("device") or "desktop",
                http_post=http_post or dataforseo._default_http_post,
                http_get=http_get or dataforseo._default_http_get,
            )
        except Exception as e:
            raise ConnectorError(f"dataforseo-maps-rank connector failed: {e}", raw_secrets={}, tool_name="dataforseo-maps-rank") from None
        tool_calls.append({"tool": "dataforseo-maps-rank", "args": {"locations": [l.get("name") for l in spec.get("locations") or []], "targets": spec.get("gbp_targets")}, "at": _now_iso(), "ok": True})
    elif handler == "dataforseo-local-pack":
        try:
            fetched_sections["local_pack"] = dataforseo_local_pack.pull_local_pack(
                credential_alias=aliases[input_name],
                resolve_credential=resolver,
                targets=spec.get("gbp_targets"),
                locations=spec.get("locations"),
                language_code=spec.get("language_code") or "en",
                device=spec.get("device") or "desktop",
                http_post=http_post or dataforseo._default_http_post,
                http_get=http_get or dataforseo._default_http_get,
            )
        except Exception as e:
            raise ConnectorError(f"dataforseo-local-pack connector failed: {e}", raw_secrets={}, tool_name="dataforseo-local-pack") from None
        tool_calls.append({"tool": "dataforseo-local-pack", "args": {"locations": [l.get("name") for l in spec.get("locations") or []], "targets": spec.get("gbp_targets")}, "at": _now_iso(), "ok": True})
    elif handler == "dataforseo-backlinks":
        previous_backlinks = (previous_snapshot or {}).get("backlinks") if previous_snapshot else None
        date_from = None
        if isinstance(previous_backlinks, dict) and previous_backlinks.get("as_of"):
            date_from = previous_backlinks["as_of"][:10]
        try:
            fetched_sections["backlinks"] = dataforseo.pull_backlinks(
                credential_alias=aliases[input_name],
                resolve_credential=resolver,
                target=spec.get("domain"),
                date_from=date_from,
                date_to=now.date().isoformat(),
                http_post=http_post or dataforseo._default_http_post,
            )
        except Exception as e:
            raise ConnectorError(f"dataforseo-backlinks connector failed: {e}", raw_secrets={}, tool_name="dataforseo-backlinks") from None
        tool_calls.append({"tool": "dataforseo-backlinks", "args": {"target": spec.get("domain"), "date_from": date_from}, "at": _now_iso(), "ok": True})
    elif handler == "pagespeed":
        try:
            fetched_pagespeed = pagespeed.pull_pagespeed(
                credential_alias=aliases[input_name],
                resolve_credential=resolver if aliases[input_name] else None,
                pages=_absolute_pages(spec),
                http_get=http_get or pagespeed._default_http_get,
            )
        except Exception as e:
            raise ConnectorError(f"pagespeed connector failed: {e}", raw_secrets={}, tool_name="pagespeed") from None
        fetched_sections["technical_health"] = _technical_health_section(
            fetched_sections.get("technical_health") or _previous_technical_health(previous_snapshot),
            pagespeed_as_of=fetched_pagespeed["as_of"],
            pagespeed_rows=fetched_pagespeed["rows"],
            secret_map=fetched_pagespeed.get("secretMap"),
        )
        tool_calls.append({"tool": "pagespeed", "args": {"pages": _absolute_pages(spec)}, "at": _now_iso(), "ok": True})
    elif handler == "gsc-indexation":
        try:
            sitemaps = gsc.pull_sitemaps(
                credential_alias=aliases[input_name],
                resolve_credential=lambda a: bearer_for_secret(resolver(a)),
                site_url=spec.get("site_url"),
                http_get=http_get or gsc._default_http_get,
            )
            inspections = gsc.inspect_urls(
                credential_alias=aliases[input_name],
                resolve_credential=lambda a: bearer_for_secret(resolver(a)),
                site_url=spec.get("site_url"),
                urls=_absolute_pages(spec),
                http_post=http_post or gsc._default_http_post,
            )
        except Exception as e:
            raise ConnectorError(f"gsc-indexation connector failed: {e}", raw_secrets={}, tool_name="gsc-indexation") from None
        fetched_sections["technical_health"] = _technical_health_section(
            fetched_sections.get("technical_health") or _previous_technical_health(previous_snapshot),
            indexation_as_of=inspections["as_of"],
            indexation_rows=inspections["rows"],
            sitemaps=sitemaps["rows"],
            secret_map={**(sitemaps.get("secretMap") or {}), **(inspections.get("secretMap") or {})},
        )
        tool_calls.append({"tool": "gsc-indexation", "args": {"pages": _absolute_pages(spec), "site_url": spec.get("site_url")}, "at": _now_iso(), "ok": True})
    else:
        raise ConnectorError(f'run_loop.py: no connector wired for spec input "{input_name}"', raw_secrets={}, tool_name=input_name)


def _fetch_metrics(spec, run_mode, scenario, previous_snapshot=None, project_dir=None, resolve_credential_fn=None, http_post=None, http_get=None):
    """Pull every connector this run mode declares.

    A *critical* connector's failure propagates as ConnectorError and aborts
    the run (the caller writes a partial-failure run and touches no state). A
    *degradable* connector's failure is recorded as a failed tool_call and the
    run continues on the sections that did come back - one flaky third-party
    enrichment API must not cost a whole cycle's evaluation and proposals.
    See CONNECTOR_REGISTRY for which is which and why."""
    aliases = _aliases_used(spec, run_mode["inputs"])
    resolver = resolve_credential_fn or (lambda alias: resolve_credential(alias, project_dir=project_dir))
    http_get = http_get or gsc._default_http_get
    now = datetime.now(timezone.utc)
    fetched_sections = {}
    tool_calls = []
    analytics_results = []
    degraded = []

    for input_name in run_mode["inputs"]:
        handler = (get_connector(input_name) or {}).get("handler")
        try:
            _dispatch_connector(
                input_name,
                handler,
                spec,
                aliases,
                resolver,
                http_get,
                http_post,
                now,
                scenario,
                previous_snapshot,
                fetched_sections,
                analytics_results,
                tool_calls,
            )
        except ConnectorError as err:
            if is_critical(input_name):
                raise
            # run.json is redacted wholesale against the run's secret_map
            # before it is written, so the message is safe to carry here.
            message = str(err)
            degraded.append({"tool": getattr(err, "tool_name", None) or input_name, "error": message})
            tool_calls.append({"tool": getattr(err, "tool_name", None) or input_name, "args": {"input": input_name}, "at": _now_iso(), "ok": False, "error": message})

    if analytics_results:
        fetched_sections["search_analytics"] = _merge_metrics(analytics_results)

    secret_map = {}
    for section in fetched_sections.values():
        if isinstance(section, dict):
            secret_map.update(section.get("secretMap") or {})
    return {"sections": fetched_sections, "tool_calls": tool_calls, "secretMap": secret_map, "degraded": degraded}


def _proposal_row_for_page(metrics, proposal):
    """Match a metrics row to a proposal's target by normalized page AND
    normalized keyword. Returns (row, reason) - reason is None on an
    unambiguous match, "missing" on zero matches, "ambiguous" on 2+."""
    target_page = _normalize_match_text(_page_path(proposal["target"]["page"]))
    target_keyword = _normalize_match_text(proposal["target"].get("keyword"))
    matches = [
        k
        for k in metrics["keywords"]
        if _normalize_match_text(_page_path(k.get("page"))) == target_page and _normalize_match_text(k.get("keyword")) == target_keyword
    ]
    if not matches:
        return None, "missing"
    if len(matches) > 1:
        return None, "ambiguous"
    return matches[0], None


def _mark_not_evaluable(p, run_id, reason):
    p["evaluation_outcome"] = "not-evaluable"
    p["evaluation_reason"] = reason
    p["evaluated_run_id"] = run_id
    p["evaluated_position"] = None
    p["position_delta"] = None
    p["not_evaluable_streak"] = p.get("not_evaluable_streak", 0) + 1
    return f'proposal {p["id"]}: not-evaluable - {reason}'


def _evaluate_prior_experiments(proposals, metrics, spec, run_id, now):
    decisions = []
    breach = None
    still_cooling_down = set()

    for p in proposals:
        if p.get("status") != "applied":
            continue
        applied_at = datetime.fromisoformat(p["applied_at"].replace("Z", "+00:00"))
        age_days = (now - applied_at).total_seconds() / 86400
        window_elapsed = age_days >= p["observation_window_days"]

        if not window_elapsed:
            still_cooling_down.add(_cooldown_key_from_target(p["target"]))
            decisions.append(f'proposal {p["id"]}: still in observation window (age {age_days:.1f}d/{p["observation_window_days"]}d)')
            continue

        row, match_reason = _proposal_row_for_page(metrics, p)
        target_desc = f'page "{p["target"]["page"]}" and keyword "{p["target"].get("keyword")}"'

        if match_reason == "missing":
            decisions.append(_mark_not_evaluable(p, run_id, f"no metrics row matched target {target_desc}"))
            continue
        if match_reason == "ambiguous":
            decisions.append(_mark_not_evaluable(p, run_id, f"multiple metrics rows matched target {target_desc} after normalization; refusing to guess"))
            continue

        # Sample sufficiency is judged against this target's own impressions, not
        # metrics["sample_size"] - that field sums every keyword row site-wide, so
        # it was almost always >= min_sample_size regardless of whether this one
        # page/keyword had enough traffic yet to draw a real conclusion from. The
        # row was already being fetched above; it just wasn't used for this check.
        row_impressions = row.get("impressions") or 0
        if row_impressions < p["min_sample_size"]:
            still_cooling_down.add(_cooldown_key_from_target(p["target"]))
            decisions.append(f'proposal {p["id"]}: still in observation window (age {age_days:.1f}d/{p["observation_window_days"]}d, sample {row_impressions}/{p["min_sample_size"]})')
            continue

        metric_value = row["position"]
        if metric_value is None:
            decisions.append(_mark_not_evaluable(p, run_id, f"matched metrics row for target {target_desc} has a null position"))
            continue

        ft = spec["failure_threshold"]
        baseline = p.get("baseline_position")
        drift = (metric_value - baseline) if baseline is not None else None
        breached = drift is not None and "position" in ft["metric"] and _compare(drift, ft["comparator"], ft["value"])

        p["evaluated_position"] = metric_value
        p["position_delta"] = None if baseline is None else metric_value - baseline
        p["not_evaluable_streak"] = 0

        if breached:
            p["status"] = "breached"
            p["evaluation_outcome"] = "breached"
            p["evaluated_run_id"] = run_id
            breach_reason = f'guardrail breach on {ft["metric"]} for {p["target"]["page"]}: position moved {baseline} -> {metric_value} (drift {drift:.1f} {ft["comparator"]} {ft["value"]})'
            p["evaluation_reason"] = breach_reason
            breach = {"proposal_id": p["id"], "reason": breach_reason}
            decisions.append(f'proposal {p["id"]}: BREACH - {breach_reason}')
        else:
            p["status"] = "verified"
            p["evaluation_outcome"] = "verified"
            p["evaluation_reason"] = None
            p["evaluated_run_id"] = run_id
            decisions.append(f'proposal {p["id"]}: verified winner (position {baseline} -> {metric_value}, within guardrail)')

    return decisions, breach, still_cooling_down


def _promote_live_implementations(project_slug, pending_dir, proposals, requester=None, now=None, compare_target=None, on_promoted=None):
    """Promotes implemented -> applied once GitHub confirms a proposal is live
    on main. Caller (run_loop()) must already hold the loop's single run.lock
    for the whole run - this function acquires no lock of its own (Phase 3:
    one shared mutation lock; a nested acquisition of that same lock here
    would self-refuse against the caller's own held lock and either deadlock
    the check or silently skip every promotion).

    `on_promoted`, if given, is called once per promoted proposal (the
    fresh, now-`applied` proposal dict - which already carries
    `implemented_commit_sha` as the new live SHA and
    `implementation_base_sha` as the previous known-good SHA) - this is
    the Phase 6a integration point for recording an automatic deploy-
    verification entry (tools/deploy_verify.py's record_deployment()),
    left as an explicit caller-supplied hook rather than a hardcoded
    import so run_loop.py stays decoupled from deploy_verify.py and every
    other caller/test is unaffected by default (None = no-op, unchanged
    behavior). A callback failure is caught and folded into `decisions`
    rather than ever crashing or blocking the core SEO loop run - deploy
    verification is a side effect of promotion, never a precondition for
    it."""
    now_iso = _now_iso(now)
    decisions = []
    awaiting_ids = []
    stuck_ids = []
    compare_target = compare_target or LIVE_COMPARE_TARGETS.get(project_slug)
    if not compare_target:
        return decisions, awaiting_ids, stuck_ids

    promotions = []
    for proposal in proposals:
        if proposal.get("status") != "implemented" or not proposal.get("implemented_commit_sha"):
            continue
        try:
            compare = compare_commit_to_main(compare_target["owner"], compare_target["repo"], proposal["implemented_commit_sha"], requester=requester)
        except Exception as err:
            proposal["implemented_run_cycles_seen"] = proposal.get("implemented_run_cycles_seen", 0) + 1
            proposal["live_check_error"] = str(err)
            awaiting_ids.append(proposal["id"])
            if proposal["implemented_run_cycles_seen"] >= 3:
                stuck_ids.append(proposal["id"])
            decisions.append(f'proposal {proposal["id"]}: awaiting live confirmation (compare check failed: {err})')
            continue

        proposal.pop("live_check_error", None)
        if compare["live"]:
            promotions.append(proposal["id"])
            continue

        proposal["implemented_run_cycles_seen"] = proposal.get("implemented_run_cycles_seen", 0) + 1
        proposal["live_compare_status"] = compare["status"]
        awaiting_ids.append(proposal["id"])
        if proposal["implemented_run_cycles_seen"] >= 3:
            stuck_ids.append(proposal["id"])
        decisions.append(f'proposal {proposal["id"]}: awaiting live confirmation (compare status {compare["status"]}, cycle {proposal["implemented_run_cycles_seen"]})')

    if not promotions:
        return decisions, awaiting_ids, stuck_ids

    for proposal_id in promotions:
        fresh_path = proposal_path(pending_dir, proposal_id)
        fresh = load_json(fresh_path)
        if fresh.get("status") != "implemented":
            continue
        fresh["status"] = "applied"
        fresh["applied_at"] = now_iso
        fresh["applied_by"] = "github-compare-api"
        fresh.pop("live_check_error", None)
        fresh["implemented_run_cycles_seen"] = fresh.get("implemented_run_cycles_seen", 0)
        atomic_write_json(fresh_path, fresh)
        try:
            loop_dir = os.path.dirname(pending_dir)
            loop_name = os.path.basename(loop_dir)
            deadline = None
            if fresh.get("applied_at") and fresh.get("observation_window_days"):
                applied_dt = datetime.fromisoformat(fresh["applied_at"].replace("Z", "+00:00"))
                deadline = (applied_dt + timedelta(days=fresh["observation_window_days"])).isoformat().replace("+00:00", "Z")
            append_event(
                loop_dir, "proposal_applied", project=project_slug, loop=loop_name, proposal_id=proposal_id,
                action_type=fresh.get("action_type"), target_page=(fresh.get("target") or {}).get("page"),
                keyword=(fresh.get("target") or {}).get("keyword"), resulting_proposal_status="applied",
                implementation_commit=fresh.get("implemented_commit_sha"), implementation_branch=fresh.get("implemented_branch"),
                observation_deadline=deadline, source_run_id=fresh.get("created_run_id"),
            )
        except Exception:
            pass  # observability, never blocks a live promotion that already succeeded
        for proposal in proposals:
            if proposal["id"] == proposal_id:
                proposal.clear()
                proposal.update(fresh)
                break
        decisions.append(f'proposal {proposal_id}: live on GitHub main - transitioned implemented -> applied at {now_iso}')
        if on_promoted is not None:
            try:
                on_promoted(fresh)
            except Exception as err:
                decisions.append(f'proposal {proposal_id}: on_promoted deploy-verification hook failed (non-fatal, promotion stands): {err}')

    return decisions, awaiting_ids, stuck_ids


def _pick_new_actions(spec, metrics, cooling_down, run_id, now, max_count=3):
    candidates = [k for k in metrics["keywords"] if _target_key({"page": k["page"]}) not in cooling_down]
    candidates = [k for k in candidates if k["position"] is not None and 3 < k["position"] <= 20]
    exclusions = [t.lower() for t in (spec.get("keyword_exclusions") or [])]
    excluded_count = 0
    if exclusions:
        before = len(candidates)
        candidates = [k for k in candidates if not any(term in (k.get("keyword") or "").lower() for term in exclusions)]
        excluded_count = before - len(candidates)
    candidates.sort(key=lambda k: k["clicks"] or 0, reverse=True)

    proposals = []
    allowed_actions = spec["allowed_actions"]
    # Capped only by max_count and candidate availability, never by how many
    # distinct action types are configured. The modulo below is what lets
    # action types repeat (round-robin) once there are more good candidates
    # than allowed_actions entries - folding len(allowed_actions) into this
    # bound made that branch unreachable (n could never exceed it) and
    # silently throttled a week's proposal volume to whichever was smaller,
    # with no round-robin ever actually occurring. spec_validate.py already
    # requires allowed_actions to be non-empty, so the modulo below is safe.
    n = min(max_count, len(candidates))
    for i in range(n):
        action = allowed_actions[i % len(allowed_actions)]
        kw = candidates[i]
        proposals.append(
            {
                "id": f"prop-{run_id}-{i}",
                "loop": spec["loop"],
                "action_type": action["type"],
                "tier": action["tier"],
                "target": {"page": kw["page"], "keyword": kw["keyword"]},
                "baseline_position": kw["position"],
                "rationale": f'keyword "{kw["keyword"]}" at position {kw["position"]} on {kw["page"]} with {kw["clicks"]} clicks - candidate for {action["type"]}',
                "rollback": action.get("rollback"),
                "manual_approval_only": action.get("manual_approval_only") is True,
                "observation_window_days": action["observation_window_days"],
                "min_sample_size": action["min_sample_size"],
                "status": "draft",
                "created_run_id": run_id,
                "created_at": now.isoformat().replace("+00:00", "Z"),
                "run_cycles_seen": 0,
                "decision": None,
                "applied_at": None,
            }
        )
    return proposals, excluded_count


# GBP Posts plan Phase 3. A known-gaps.yaml entry confirmed longer ago than this needs a
# human to re-confirm it before this selector treats it as live evidence - mirrors the
# staleness convention the plan's Phase 4 section establishes for cross-repo constraint facts
# (gbp-profiles.md/art.yaml), applied here at selection time as a lower-stakes analog. This is
# NOT the same mechanism as Phase 4's hard publish-time gate on those two files; it only
# governs whether this selector drafts a proposal at all.
GBP_KNOWN_GAP_MAX_AGE_DAYS = 180
CONTENT_GAP_EVIDENCE_SCHEMA_VERSION = 1


def _find_allowed_action(spec, action_type):
    for action in spec.get("allowed_actions") or []:
        if action.get("type") == action_type:
            return action
    return None


def _sha256_json(value):
    import hashlib
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _pick_gbp_actions(loop_dir, spec, cooling_down, run_id, now, max_count=3):
    """GBP Posts plan Phase 3 - deliberately NOT a reuse of _pick_new_actions, and
    deliberately does not read `search_analytics` at all (see the spec["loop"] == "seo" gate
    around this function's call site).

    The plan's hard rule: a visibility signal (local-rank absence, local-pack absence, a
    technical-health/indexation gap) is a trigger to LOOK for a content opportunity, never a
    content opportunity itself - "a page ranking poorly says nothing about whether there's
    anything fresh to post about that service." The only thing that can justify drafting a
    gbp-post-draft proposal today is a known-gaps.yaml entry explicitly routed
    `content_gap_evidence` (the alternative source the plan names - a "no post in N days"
    check against real remote post history - requires Phase 6's publish/reconciliation
    machinery, which is unbuilt; this selector does not fabricate that check in its absence).
    known-gaps.yaml is re-read and re-hashed fresh from disk every run, never from a cached
    copy, and `content_gap_evidence.source_entry_hash` is exactly what was actually read.

    `opportunity_type` is always "content_freshness" here (the closed-enum other value,
    "engagement", belongs to a future no-post-in-N-days trigger this selector cannot express
    yet) - this is a structural fact about what this function can produce, not a free-text
    choice, so a rank/visibility claim cannot be smuggled into it.
    """
    decisions = []
    known_gaps_path = os.path.join(loop_dir, "known-gaps.yaml")
    if not os.path.exists(known_gaps_path):
        return [], ["known-gaps.yaml not found - no content-gap evidence source available, zero proposals drafted"]

    with open(known_gaps_path, "r", encoding="utf-8") as f:
        raw_text = f.read()
    try:
        parsed = yaml.safe_load(raw_text)
    except yaml.YAMLError as err:
        return [], [f"known-gaps.yaml failed to parse ({err}) - zero proposals drafted this run"]
    if not isinstance(parsed, dict):
        # A syntactically valid YAML document that isn't a mapping at all (a bare list, a
        # scalar) - .get("gaps") on it would raise AttributeError rather than degrade
        # cleanly (Codex review, 2026-08-31).
        return [], ["known-gaps.yaml does not contain a mapping at its root - zero proposals drafted this run"]
    raw_gaps = parsed.get("gaps")
    if raw_gaps is None:
        raw_gaps = []
    elif not isinstance(raw_gaps, list):
        return [], ["known-gaps.yaml's gaps field is not a list - zero proposals drafted this run"]

    try:
        source_rel_path = os.path.relpath(known_gaps_path, WORKSPACE_ROOT).replace("\\", "/")
    except ValueError:
        # Cross-drive on Windows (e.g. a test fixture under a different drive than the
        # workspace) - relpath cannot express that as a relative path at all. Falls back to
        # the absolute path rather than raising; this is a display/reference field only, not
        # something the hash or the proposal's identity depends on.
        source_rel_path = known_gaps_path
    action = _find_allowed_action(spec, "gbp-post-draft")
    if action is None:
        return [], ['"gbp-post-draft" is not in this loop\'s allowed_actions - zero proposals drafted']

    candidates = []
    seen_ids_this_run = set()
    seen_cooldown_keys_this_run = set()
    for gap in raw_gaps:
        if not isinstance(gap, dict):
            decisions.append("known-gaps.yaml contains a non-object gap entry - skipped")
            continue
        gap_id = gap.get("id")
        # Structural gaps route to the human checklist (templates/loops/seo/gbp-checklist.md)
        # and must never become a proposal - a Post cannot fix a category/hours/photo-count
        # gap (spec.md's framing note).
        if gap.get("type") != "content_gap" or gap.get("routing") != "content_gap_evidence":
            continue
        location = gap.get("location")
        summary = gap.get("summary")
        confirmed_date = gap.get("confirmed_date")
        if not gap_id or not location or not summary:
            decisions.append(f'known-gaps.yaml entry "{gap_id}" is missing id/location/summary - skipped')
            continue
        if gap_id in seen_ids_this_run:
            # A malformed source with two entries sharing an id must not become two
            # proposals in the same run (Codex review, 2026-08-31) - source validation
            # belongs on known-gaps.yaml itself; this is the selector's own backstop.
            decisions.append(f'known-gaps.yaml entry "{gap_id}" is duplicated - only the first occurrence is considered')
            continue
        seen_ids_this_run.add(gap_id)
        if not confirmed_date:
            decisions.append(f'known-gaps.yaml entry "{gap_id}": no confirmed_date - treated as unconfirmed, skipped')
            continue
        try:
            confirmed_dt = datetime.fromisoformat(str(confirmed_date))
        except ValueError:
            decisions.append(f'known-gaps.yaml entry "{gap_id}": confirmed_date "{confirmed_date}" is not a valid date - skipped')
            continue
        # A naive value (a bare "YYYY-MM-DD" date, as every entry today is written) is
        # treated as UTC; an already-aware value is converted, never blindly relabeled -
        # `.replace(tzinfo=...)` on an aware datetime would have silently discarded its
        # real offset and misdated the confirmation (Codex review, 2026-08-31).
        confirmed_dt = confirmed_dt.replace(tzinfo=timezone.utc) if confirmed_dt.tzinfo is None else confirmed_dt.astimezone(timezone.utc)
        age_days = (now - confirmed_dt).total_seconds() / 86400
        if age_days < 0:
            decisions.append(f'known-gaps.yaml entry "{gap_id}": confirmed_date "{confirmed_date}" is in the future - invalid, skipped')
            continue
        if age_days > GBP_KNOWN_GAP_MAX_AGE_DAYS:
            decisions.append(f'known-gaps.yaml entry "{gap_id}": confirmed {age_days:.0f}d ago (> {GBP_KNOWN_GAP_MAX_AGE_DAYS}d) - stale, needs re-confirmation, skipped')
            continue
        cooldown_key = _cooldown_key_from_target({"location": location, "topic": gap_id})
        if cooldown_key in cooling_down or cooldown_key in seen_cooldown_keys_this_run:
            decisions.append(f'known-gaps.yaml entry "{gap_id}": a non-terminal proposal already exists for {location}/{gap_id} - cooldown')
            continue
        seen_cooldown_keys_this_run.add(cooldown_key)
        candidates.append((gap, age_days))

    new_proposals = []
    for i, (gap, age_days) in enumerate(candidates[:max_count]):
        gap_id = gap["id"]
        evidence = {
            "schema_version": CONTENT_GAP_EVIDENCE_SCHEMA_VERSION,
            "evidence_id": gap_id,
            "source": source_rel_path,
            "source_entry_hash": _sha256_json(gap),
            "observed_at": _now_iso(now),
            # Two distinct thresholds, recorded distinctly rather than conflated under one
            # name (Codex review, 2026-08-31 - the original single `freshness_threshold_days`
            # field claimed to have "qualified" this proposal but was never actually checked
            # against anything): `gap_freshness_threshold_days` is known-gaps.yaml's own
            # declared value, carried through for a future Phase 6 "no post about this in N
            # days" check against real remote post history (not buildable yet - Phase 6 is
            # unbuilt); `confirmation_max_age_days`/`confirmation_age_days` are what THIS
            # selector actually evaluated just now to decide the underlying fact is still
            # trustworthy.
            "gap_freshness_threshold_days": gap.get("freshness_threshold_days"),
            "gap_confirmed_date": gap.get("confirmed_date"),
            "confirmation_max_age_days": GBP_KNOWN_GAP_MAX_AGE_DAYS,
            "confirmation_age_days": round(age_days, 1),
        }
        proposals_new_entry = {
            "id": f"prop-{run_id}-{i}",
            "loop": spec["loop"],
            "action_type": "gbp-post-draft",
            "tier": action["tier"],
            "target": {"location": gap["location"], "topic": gap_id},
            "opportunity_type": "content_freshness",
            "signal_reference": {"known_gaps_entry_id": gap_id, "source": source_rel_path},
            "intended_user_action": gap.get("intended_user_action") or "learn_more",
            "content_gap_evidence": evidence,
            # Templated EXCLUSIVELY from closed/structured fields (gap_id, location,
            # confirmed_date) - deliberately never gap["summary"], which is human-authored
            # free text and could itself contain a rank/visibility claim (e.g. "will improve
            # rank") that this function would otherwise blindly echo into the proposal,
            # defeating the entire point of a templated-only rationale (Codex review,
            # 2026-08-31 - the real known-gaps.yaml entry's summary literally mentions a GSC
            # position, which the original version copied straight into the rationale). A
            # reviewer who wants the human-authored summary can read it directly via
            # signal_reference.known_gaps_entry_id/source.
            "rationale": (
                f'Content-freshness opportunity at {gap["location"]}, from known-gaps.yaml entry "{gap_id}" '
                f'(confirmed {gap.get("confirmed_date")}). See that entry\'s own summary field for the human-authored '
                "detail - deliberately not repeated here, to keep this field limited to structured data only."
            ),
            "rollback": "retract via tools/retract_gbp_post.py once published (plan Phase 6, unbuilt) - a draft/approval has nothing live to roll back",
            "manual_approval_only": action.get("manual_approval_only") is True,
            "observation_window_days": action["observation_window_days"],
            "min_sample_size": action["min_sample_size"],
            "status": "draft",
            "created_run_id": run_id,
            "created_at": _now_iso(now),
            "run_cycles_seen": 0,
            "decision": None,
            "applied_at": None,
        }
        new_proposals.append(proposals_new_entry)
        decisions.append(f'proposal drafted from known-gaps.yaml entry "{gap_id}" ({gap["location"]})')

    if not new_proposals:
        decisions.append("no eligible content-gap evidence found this run - zero proposals drafted")

    return new_proposals, decisions


def _status_buckets(proposals):
    buckets = {}
    for proposal in proposals:
        buckets.setdefault(proposal.get("status") or "unknown", []).append(proposal["id"])
    return buckets


def _numeric(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _rank_of(row):
    """A local-rank row's comparable position, or None if it has none.

    None means "do not compare this row", and the distinction is load-bearing. The
    previous code read `(_numeric(pos) or 0)` on both sides, so a row with no position
    was compared as rank 0 - the best rank there is. A target going from "no answer" to
    a real rank of 40 computed as a +40 delta and fired the attention threshold, and a
    genuine drop out of the results computed as a -40 improvement. Since Issue #1 a row
    can also be an explicit `error` (the API gave us nothing), which is missing data and
    must never be read as a position at all.
    """
    if not isinstance(row, dict):
        return None
    if row.get("status") == "error":
        return None
    return _numeric(row.get("organic_rank_position"))


def _evaluate_attention(spec, run_id, runs_dir, snapshot, updated_sections):
    findings = []
    history_local_rank = _section_history(runs_dir, "local_rank", run_id) if "local_rank" in updated_sections else []
    history_backlinks = _section_history(runs_dir, "backlinks", run_id) if "backlinks" in updated_sections else []
    history_tech = _section_history(runs_dir, "technical_health", run_id) if "technical_health" in updated_sections else []

    for threshold in spec.get("attention_thresholds") or []:
        metric = threshold.get("metric")
        if threshold["kind"] == "numeric_delta" and metric == "organic_rank_position" and "local_rank" in updated_sections:
            rows = (snapshot.get("local_rank") or {}).get("rows") or []
            previous_rows = {(r.get("location_name"), r.get("keyword"), r.get("page")): r for r in ((history_local_rank[0].get("rows") if history_local_rank else []) or [])}
            older_rows = {(r.get("location_name"), r.get("keyword"), r.get("page")): r for r in ((history_local_rank[1].get("rows") if len(history_local_rank) > 1 else []) or [])}
            for row in rows:
                key = (row.get("location_name"), row.get("keyword"), row.get("page"))
                prev = previous_rows.get(key)
                if not prev:
                    continue
                current_pos, prev_pos = _rank_of(row), _rank_of(prev)
                if current_pos is None or prev_pos is None:
                    continue
                delta = current_pos - prev_pos
                if not _compare(delta, threshold["comparator"], threshold["threshold"]):
                    continue
                ok = True
                for _ in range(1, int(threshold.get("consecutive_runs") or 1)):
                    older = older_rows.get(key)
                    older_pos = _rank_of(older) if older else None
                    if older_pos is None:
                        ok = False
                        break
                    prev_delta = prev_pos - older_pos
                    if not _compare(prev_delta, threshold["comparator"], threshold["threshold"]):
                        ok = False
                        break
                if ok:
                    findings.append(f'Local rank: {row.get("location_name")} / "{row.get("keyword")}" moved {prev.get("organic_rank_position")} -> {row.get("organic_rank_position")} on {row.get("page")}')
        elif threshold["kind"] == "numeric_delta" and metric == "referring_domains" and "backlinks" in updated_sections:
            current = ((snapshot.get("backlinks") or {}).get("summary") or {}).get("referring_domains")
            prev = (((history_backlinks[0].get("summary") if history_backlinks else {}) or {}).get("referring_domains"))
            older = (((history_backlinks[1].get("summary") if len(history_backlinks) > 1 else {}) or {}).get("referring_domains"))
            if current is not None and prev is not None:
                delta = current - prev
                if _compare(delta, threshold["comparator"], threshold["threshold"]):
                    ok = True
                    if int(threshold.get("consecutive_runs") or 1) > 1:
                        if older is None or not _compare(prev - older, threshold["comparator"], threshold["threshold"]):
                            ok = False
                    if ok:
                        findings.append(f"Backlinks: referring domains moved {prev} -> {current} ({delta:+})")
        elif threshold["kind"] == "enum_transition" and metric == "cwv_status" and "technical_health" in updated_sections:
            rows = (snapshot.get("technical_health") or {}).get("pagespeed_rows") or []
            prev_rows = {(r.get("page"), r.get("strategy")): r for r in ((history_tech[0].get("pagespeed_rows") if history_tech else []) or [])}
            for row in rows:
                prev = prev_rows.get((row.get("page"), row.get("strategy")))
                if prev and prev.get("cwv_status") != threshold["to"] and row.get("cwv_status") == threshold["to"]:
                    findings.append(f'CWV: {row.get("page")} transitioned {prev.get("cwv_status")} -> {row.get("cwv_status")}')
    return findings


def _fetch_footer_location_diff(loop_dir, spec, now):
    if now.weekday() != 0 or now.day > 7:
        return None
    domain = spec.get("domain")
    if not domain:
        return None
    url = f"https://{domain}/"
    try:
        with urllib.request.urlopen(url) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return {"checked_at": _now_iso(now), "ok": False, "error": str(e)}

    text = re.sub(r"<[^>]+>", " ", html)
    detected = sorted(set(m.group(0).strip() for m in re.finditer(r"\d{2,5}[^<>\n]{0,80}[A-Za-z]+,\s*CO\s+\d{5}", text)))
    expected = [location.get("address") for location in spec.get("locations") or []]
    payload = {
        "checked_at": _now_iso(now),
        "ok": True,
        "expected": expected,
        "detected": detected,
        "matches": sorted(expected) == sorted(detected),
    }
    atomic_write_json(os.path.join(loop_dir, LOCATION_DIFF_FILENAME), payload)
    return payload


def _report_lines(run_id, project_slug, loop_name, mode, status, decisions, new_proposals, stale_ids, proposals, awaiting_ids, stuck_ids, evaluated_now, snapshot, attention_findings, footer_check, not_evaluable_now=None, started_at=None):
    not_evaluable_now = not_evaluable_now or []
    status_buckets = _status_buckets(proposals)
    report_lines = [
        f"# Run {run_id} ({loop_name} / {project_slug})",
        "",
        f"**Status:** {status}",
        f"**Mode:** {mode}",
        # Run IDs are UTC by design (sort keys); this line is the local-time
        # reading of the same instant, so the report matches your wall clock.
        f"**Started:** {_local_stamp(started_at)}" if started_at else "",
        "",
        f"## Needs Attention ({len(attention_findings)})",
    ]
    report_lines.extend([f"- {item}" for item in attention_findings] if attention_findings else ["- none"])
    if footer_check and footer_check.get("ok") and not footer_check.get("matches"):
        report_lines.append(f'- Monthly footer location check mismatch ({footer_check.get("checked_at")}): detected footer addresses differ from spec.md; see {LOCATION_DIFF_FILENAME}')
    elif footer_check and not footer_check.get("ok"):
        report_lines.append(f'- Monthly footer location check failed ({footer_check.get("checked_at")}): {footer_check.get("error")}')

    local_rank = snapshot.get("local_rank") or {}
    report_lines.extend(["", f'## Local Rank By Location (as of {_local_stamp(local_rank.get("as_of"), short=True)})'])
    if local_rank.get("rows"):
        for row in local_rank["rows"]:
            # A row with no position is either "we looked and we are not there" or "we
            # never got an answer", and rendering both as a bare `None` is what let a
            # months-long collection failure read as a months-long ranking failure.
            status = row.get("status")
            if status == "error":
                outcome = f'NO ANSWER ({row.get("error_reason")}) - missing data, not an absence'
            elif status == "absent":
                seen = row.get("organic_results_seen")
                outcome = f"not in results (of {seen} organic results seen)" if seen else "not in results"
            elif status == "ok":
                outcome = f'#{row.get("organic_rank_position")} organic'
            else:
                outcome = str(row.get("organic_rank_position"))
            report_lines.append(f'- {row.get("location_name")}: "{row.get("keyword")}" -> {outcome} on {row.get("page")}')
        if local_rank.get("request_count") is not None:
            report_lines.append(
                f'- collection: {local_rank.get("request_count")} request(s) sent, '
                f'{local_rank.get("success_count")} answered, {local_rank.get("error_count")} failed'
            )
    else:
        report_lines.append("- none")

    maps_rank = snapshot.get("maps_rank") or {}
    report_lines.extend(["", f'## Maps Rank (as of {_local_stamp(maps_rank.get("as_of"), short=True)})'])
    if maps_rank.get("rows"):
        for row in maps_rank["rows"]:
            status = row.get("status")
            if status == "error":
                outcome = f'NO ANSWER ({row.get("error_reason")}) - missing data, not an absence'
            elif status == "absent":
                seen = row.get("results_seen")
                outcome = f"not in maps results (of {seen} seen)" if seen else "not in maps results"
            else:
                outcome = f'#{row.get("rank_group")}'
            report_lines.append(f'- {row.get("location_name")}: "{row.get("keyword")}" -> {outcome}')
    else:
        report_lines.append("- none")

    local_pack = snapshot.get("local_pack") or {}
    report_lines.extend(["", f'## Local Pack (as of {_local_stamp(local_pack.get("as_of"), short=True)})'])
    if local_pack.get("rows"):
        for row in local_pack["rows"]:
            status = row.get("status")
            if status == "error":
                outcome = f'NO ANSWER ({row.get("error_reason")})'
            elif not row.get("present"):
                outcome = "no pack shown"
            elif row.get("site_in_pack"):
                outcome = f'in pack at #{row.get("site_position")} (of {row.get("entry_count")})'
            else:
                outcome = f'pack shown, not in it ({row.get("entry_count")} entries)'
            report_lines.append(f'- {row.get("location_name")}: "{row.get("keyword")}" -> {outcome}')
    else:
        report_lines.append("- none")

    backlinks = snapshot.get("backlinks") or {}
    summary = backlinks.get("summary") or {}
    history = backlinks.get("history") or {}
    report_lines.extend(["", f'## Backlinks (as of {_local_stamp(backlinks.get("as_of"), short=True)})'])
    if backlinks:
        report_lines.append(f'- referring domains: {summary.get("referring_domains")}, backlinks: {summary.get("backlinks")}, new/lost backlinks: {history.get("new_backlinks")}/{history.get("lost_backlinks")}, new/lost referring domains: {history.get("new_referring_domains")}/{history.get("lost_referring_domains")}')
    else:
        report_lines.append("- none")

    tech = snapshot.get("technical_health") or {}
    report_lines.extend(["", f'## Technical Health (as of {_local_stamp(tech.get("as_of"), short=True)})'])
    if tech.get("pagespeed_rows"):
        for row in tech["pagespeed_rows"]:
            report_lines.append(f'- CWV {row.get("page")}: {row.get("cwv_status")} (LCP {row.get("lcp_ms")}, INP {row.get("inp_ms")}, CLS {row.get("cls")}, score {row.get("performance_score")})')
    else:
        report_lines.append("- CWV: none")
    if tech.get("indexation_rows"):
        for row in tech["indexation_rows"]:
            report_lines.append(f'- Indexation {row.get("page")}: {row.get("verdict")} / {row.get("coverage_state")}')
    else:
        report_lines.append("- Indexation: none")
    if tech.get("sitemaps"):
        for row in tech["sitemaps"]:
            report_lines.append(f'- Sitemap {row.get("path")}: pending={row.get("is_pending")} warnings={row.get("warnings")} errors={row.get("errors")}')
    else:
        report_lines.append("- Sitemaps: none")

    report_lines.extend(["", "## Decisions"])
    report_lines.extend([f"- {d}" for d in decisions] if decisions else ["- none"])
    report_lines.extend(["", "## Proposals by status"])
    if status_buckets:
        for proposal_status in sorted(status_buckets):
            report_lines.append(f'- {proposal_status}: {", ".join(status_buckets[proposal_status])}')
    else:
        report_lines.append("- none")

    report_lines.extend(["", f"## New proposals ({len(new_proposals)})"])
    report_lines.extend([f'- {p["id"]}: {p["action_type"]} on {_describe_target(p.get("target"))} (tier {p["tier"]})' for p in new_proposals] if new_proposals else ["- none"])
    report_lines.extend(["", "## Awaiting live confirmation"])
    report_lines.extend([f"- {proposal_id}" for proposal_id in awaiting_ids] if awaiting_ids else ["- none"])
    report_lines.extend(["", "## Stuck implemented (>=3 cycles)"])
    report_lines.extend([f"- {proposal_id}" for proposal_id in stuck_ids] if stuck_ids else ["- none"])
    report_lines.extend(["", "## Verified / breached this run"])
    if evaluated_now:
        for proposal in evaluated_now:
            delta = proposal.get("position_delta")
            delta_text = "unknown" if delta is None else f"{delta:+.1f}"
            report_lines.append(f'- {proposal["id"]}: {proposal["status"]} (position {proposal.get("baseline_position")} -> {proposal.get("evaluated_position")}, delta {delta_text})')
    else:
        report_lines.append("- none")
    report_lines.extend(["", "## Not evaluable this run"])
    if not_evaluable_now:
        for proposal in not_evaluable_now:
            report_lines.append(f'- {proposal["id"]}: {proposal.get("evaluation_reason")}')
    else:
        report_lines.append("- none")
    report_lines.extend(["", "## Stale proposals (>=3 cycles undecided)"])
    report_lines.extend([f"- {sid}" for sid in stale_ids] if stale_ids else ["- none"])
    return report_lines


def run_loop(project_slug, loop_name, scenario="normal", run_name=None, _resolve_credential=None, _http_post=None, _http_get=None, _github_requester=None, _github_compare_target=None, _on_promoted=None):
    project_dir = os.path.join(PROJECTS_ROOT, project_slug)
    assert_within(PROJECTS_ROOT, project_dir, "project directory")
    loop_dir = os.path.join(project_dir, "loops", loop_name)
    assert_within(project_dir, loop_dir, "loop directory")

    spec_path = os.path.join(loop_dir, "spec.md")
    memory_path = os.path.join(loop_dir, "memory.md")
    pending_dir = os.path.join(loop_dir, "pending")
    runs_dir = os.path.join(loop_dir, "runs")
    state_path = os.path.join(loop_dir, "state.json")
    now = datetime.now(timezone.utc)

    lock_ttl_minutes = _read_lock_ttl_minutes_unsafe(spec_path)
    lock = acquire_lock(loop_dir, max_run_duration_minutes=lock_ttl_minutes, runs_dir=runs_dir, now=now)
    if not lock["acquired"]:
        log_refusal(loop_dir, lock["reason"])
        return {"status": "refused", "reason": lock["reason"]}
    run_id = lock["run_id"]
    run_dir = os.path.join(runs_dir, run_id)

    try:
        validation = validate_spec_file(spec_path)
        if not validation["valid"]:
            os.makedirs(run_dir, exist_ok=True)
            atomic_write_json(os.path.join(run_dir, "validation-failure.json"), validation)
            return {"status": "invalid-spec", "run_id": run_id, "errors": validation["errors"]}
        spec = _load_spec(spec_path)
        run_mode = _resolve_run_mode(spec, run_name=run_name)

        state = _load_json_safe(state_path, {"status": "active"})
        proposals = _list_pending_proposals(pending_dir)
        previous_snapshot = _latest_snapshot_sections(runs_dir, current_run_id=run_id)

        try:
            pulled = _fetch_metrics(
                spec,
                run_mode,
                scenario,
                previous_snapshot=previous_snapshot,
                project_dir=project_dir,
                resolve_credential_fn=_resolve_credential,
                http_post=_http_post,
                http_get=_http_get,
            )
        except ConnectorError as err:
            secret_map = err.raw_secrets or {}
            redacted_message = redact_deep(str(err), secret_map)
            failed_tool = getattr(err, "tool_name", None) or "metrics-connector"
            os.makedirs(run_dir, exist_ok=True)
            run_json = {
                "run_id": run_id,
                "project": project_slug,
                "loop": loop_name,
                "mode": run_mode["mode"],
                "run_name": run_mode["name"],
                "start": _now_iso(now),
                "end": _now_iso(),
                "status": "partial-failure",
                "tool_calls": [{"tool": failed_tool, "args": {"run_name": run_mode["name"]}, "at": _now_iso(), "ok": False, "error": redacted_message}],
                "credential_alias_used": _aliases_used(spec, run_mode["inputs"]),
                "decisions": ["connector failed; no proposals generated this run; prior state left untouched"],
                "proposals_created": [],
                "proposals_evaluated": [],
                "stale_proposals": [],
                "awaiting_live_confirmation": [],
                "stuck_implemented": [],
                "final_status": "partial-failure",
            }
            atomic_write_json(os.path.join(run_dir, "run.json"), run_json)
            with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8", newline="\n") as f:
                f.write(f"# Run {run_id} ({loop_name} / {project_slug})\n\n**Status:** partial-failure\n\nConnector call failed:\n\n```\n{redacted_message}\n```\n\nNo proposals were generated this run. State left untouched.\n")
            with open(memory_path, "a", encoding="utf-8", newline="\n") as f:
                f.write(f"- {_local_stamp(_now_iso(), short=True)} / {_now_iso()} run {run_id}: partial-failure (connector error, redacted)\n")
            return {"status": "partial-failure", "run_id": run_id}

        degraded = pulled.get("degraded") or []
        snapshot = _build_snapshot(previous_snapshot, pulled["sections"])
        secret_map = dict(pulled["secretMap"])
        for section in snapshot.values():
            if isinstance(section, dict):
                secret_map.update(section.get("secretMap") or {})
        snapshot_path = write_snapshot(run_dir, snapshot, secret_map)

        live_decisions, awaiting_ids, stuck_ids = _promote_live_implementations(project_slug, pending_dir, proposals, requester=_github_requester, now=now, compare_target=_github_compare_target, on_promoted=_on_promoted)

        eval_decisions = list(live_decisions)
        breach = None
        still_cooling_down = set()
        search_metrics = snapshot.get("search_analytics")
        # `spec["loop"] == "seo"` is a real structural gate, not an artifact of what
        # happens to be in this run's `inputs` - _build_snapshot carries a stale
        # search_analytics section forward from any earlier run that DID populate it
        # (e.g. a since-removed `mock`/`gsc`/`dataforseo` input), so "no such connector
        # is configured today" is not itself a durable guarantee that this SEO-shaped
        # evaluator/selector can never fire for a non-seo loop (Codex review of the GBP
        # Posts plan Phase 2, 2026-08-31, correctly flagged this carry-forward loophole
        # against a claim in projects/art/loops/gbp/spec.md's Notes). Both
        # _evaluate_prior_experiments and _pick_new_actions assume SEO-shaped data
        # ({page, keyword} targets, allowed_actions typed as SEO rewrites) and must
        # never run for a loop like `gbp` whose own selector (a future phase) works
        # entirely differently.
        if spec.get("loop") == "seo" and run_mode["mode"] != "technical-only" and isinstance(search_metrics, dict) and search_metrics.get("keywords") is not None:
            extra_decisions, breach, still_cooling_down = _evaluate_prior_experiments(proposals, search_metrics, spec, run_id, now)
            eval_decisions.extend(extra_decisions)

        for proposal in proposals:
            if proposal.get("status") not in TERMINAL_PROPOSAL_STATUSES:
                still_cooling_down.add(_cooldown_key_from_target(proposal["target"]))

        new_state = state
        if breach:
            new_state = {"status": "paused-breach", "paused_reason": breach["reason"], "paused_at": _now_iso(), "breach_run_id": run_id, "breach_proposal_id": breach["proposal_id"]}
            original = next(p for p in proposals if p["id"] == breach["proposal_id"])
            revert_proposal = {
                "id": f'revert-{breach["proposal_id"]}',
                "loop": loop_name,
                "action_type": f'revert:{original["action_type"]}',
                "tier": original["tier"],
                "target": original["target"],
                "rationale": f'auto-generated revert proposal after guardrail breach: {breach["reason"]}',
                "rollback": original.get("rollback"),
                "manual_approval_only": True,
                "status": "draft",
                "created_run_id": run_id,
                "created_at": _now_iso(),
                "run_cycles_seen": 0,
                "decision": None,
                "applied_at": None,
                "observation_window_days": original["observation_window_days"],
                "min_sample_size": original["min_sample_size"],
            }
            _write_proposal(pending_dir, revert_proposal)
            proposals.append(revert_proposal)

        stale_ids = []
        for proposal in proposals:
            if proposal.get("status") in ("draft", "reviewed"):
                proposal["run_cycles_seen"] = proposal.get("run_cycles_seen", 0) + 1
                if proposal["run_cycles_seen"] >= 3:
                    stale_ids.append(proposal["id"])

        new_proposals = []
        if new_state["status"] == "paused-breach":
            eval_decisions.append("BLOCKED: loop is paused-breach - no new proposals until a human resolves the failed experiment via /review-pending")
        elif spec.get("loop") == "seo" and run_mode["mode"] != "technical-only" and isinstance(search_metrics, dict) and search_metrics.get("keywords") is not None:
            new_proposals, excluded_count = _pick_new_actions(spec, search_metrics, still_cooling_down, run_id, now)
            if excluded_count:
                eval_decisions.append(f"keyword_exclusions filtered {excluded_count} candidate(s)")
            for proposal in new_proposals:
                _write_proposal(pending_dir, proposal)
        elif spec.get("loop") == "gbp":
            new_proposals, gbp_decisions = _pick_gbp_actions(loop_dir, spec, still_cooling_down, run_id, now)
            eval_decisions.extend(gbp_decisions)
            for proposal in new_proposals:
                _write_proposal(pending_dir, proposal)

        for proposal in proposals:
            proposal.pop("_file", None)
            atomic_write_json(proposal_path(pending_dir, proposal["id"]), proposal)
        atomic_write_json(state_path, new_state)

        # Durable event history (Phase 7 - PLAN-PHASE7-CODEX-REVIEW.md item 6):
        # best-effort observability layered onto this already-shipped state
        # machine, emitted only after the corresponding disk write above.
        for proposal in new_proposals:
            try:
                append_event(
                    loop_dir, "proposal_created", project=project_slug, loop=loop_name, proposal_id=proposal["id"],
                    action_type=proposal.get("action_type"), target_page=(proposal.get("target") or {}).get("page"),
                    keyword=(proposal.get("target") or {}).get("keyword"), resulting_proposal_status="draft",
                    source_run_id=run_id,
                )
            except Exception:
                pass
        for proposal in proposals:
            if proposal.get("evaluated_run_id") != run_id or proposal.get("evaluation_outcome") not in ("verified", "breached"):
                continue
            try:
                append_event(
                    loop_dir, "proposal_verified" if proposal["evaluation_outcome"] == "verified" else "guardrail_breached",
                    project=project_slug, loop=loop_name, proposal_id=proposal["id"],
                    action_type=proposal.get("action_type"), target_page=(proposal.get("target") or {}).get("page"),
                    keyword=(proposal.get("target") or {}).get("keyword"), resulting_proposal_status=proposal["status"],
                    source_run_id=run_id, note=(proposal.get("evaluation_reason") or "")[:500] or None,
                )
            except Exception:
                pass

        updated_sections = set(pulled["sections"].keys())
        attention_findings = _evaluate_attention(spec, run_id, runs_dir, snapshot, updated_sections)
        footer_check = _fetch_footer_location_diff(loop_dir, spec, now) if run_mode["mode"] == "full" and "local_rank" in updated_sections else None
        evaluated_now = [p for p in proposals if p.get("evaluated_run_id") == run_id and p.get("evaluation_outcome") in ("verified", "breached")]
        not_evaluable_now = [p for p in proposals if p.get("evaluated_run_id") == run_id and p.get("evaluation_outcome") == "not-evaluable"]
        for proposal in not_evaluable_now:
            if proposal.get("not_evaluable_streak", 0) >= 3:
                attention_findings.append(f'proposal {proposal["id"]}: not-evaluable for {proposal["not_evaluable_streak"]} consecutive runs ({proposal.get("evaluation_reason")})')
        for failure in degraded:
            # A degraded connector must never be a silent omission: it means a
            # section of the snapshot is carried forward from an older run.
            attention_findings.append(f'connector degraded - {failure["tool"]}: {failure["error"]} (its snapshot section was carried forward, not refreshed)')
        # "degraded" is distinct from "partial-failure": this run produced a
        # snapshot, evaluated proposals and may have created new ones, it just
        # did so with one enrichment section stale.
        run_status = "paused-breach" if new_state["status"] == "paused-breach" else ("degraded" if degraded else "ok")
        run_json = redact_deep(
            {
                "run_id": run_id,
                "project": project_slug,
                "loop": loop_name,
                "mode": run_mode["mode"],
                "run_name": run_mode["name"],
                "start": _now_iso(now),
                "end": _now_iso(),
                "status": run_status,
                "tool_calls": pulled["tool_calls"],
                "degraded_connectors": [failure["tool"] for failure in degraded],
                "credential_alias_used": _aliases_used(spec, run_mode["inputs"]),
                "decisions": list(eval_decisions),
                "proposals_created": [p["id"] for p in new_proposals],
                "proposals_evaluated": [p["id"] for p in evaluated_now],
                "proposals_not_evaluable": [p["id"] for p in not_evaluable_now],
                "stale_proposals": stale_ids,
                "awaiting_live_confirmation": awaiting_ids,
                "stuck_implemented": stuck_ids,
                "attention_flags": attention_findings,
                "snapshot": os.path.relpath(snapshot_path, loop_dir),
                "final_status": run_status,
            },
            secret_map,
        )
        atomic_write_json(os.path.join(run_dir, "run.json"), run_json)

        report_lines = _report_lines(run_id, project_slug, loop_name, run_mode["mode"], run_json["status"], eval_decisions, new_proposals, stale_ids, proposals, awaiting_ids, stuck_ids, evaluated_now, snapshot, attention_findings, footer_check, not_evaluable_now=not_evaluable_now, started_at=_now_iso(now))
        with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(report_lines) + "\n")

        with open(memory_path, "a", encoding="utf-8", newline="\n") as f:
            degraded_note = f', {len(degraded)} connector(s) degraded ({", ".join(run_json["degraded_connectors"])})' if degraded else ""
            f.write(f'- {_local_stamp(_now_iso(), short=True)} / {_now_iso()} run {run_id}: {run_json["status"]}, mode {run_mode["mode"]}, {len(new_proposals)} new proposal(s), {len(run_json["proposals_evaluated"])} evaluated, {len(stale_ids)} stale, {len(awaiting_ids)} awaiting-live{degraded_note}\n')

        return {"status": run_json["status"], "run_id": run_id, "run_json": run_json}
    finally:
        release_lock(loop_dir, run_id)


if __name__ == "__main__":
    try:
        from .lib.tls import enable_system_truststore
    except ImportError:
        from lib.tls import enable_system_truststore
    enable_system_truststore()

    args = sys.argv[1:]
    positional = [a for a in args if not a.startswith("--")]
    project = positional[0] if len(positional) > 0 else None
    loop = positional[1] if len(positional) > 1 else None
    scenario = "normal"
    run_name = None
    if "--scenario" in args:
        idx = args.index("--scenario")
        if idx + 1 < len(args):
            scenario = args[idx + 1]
    if "--run-name" in args:
        idx = args.index("--run-name")
        if idx + 1 < len(args):
            run_name = args[idx + 1]

    if not project or not loop:
        print("usage: python tools/run_loop.py <project> <loop> [--scenario normal|breach|fail] [--run-name <name>]", file=sys.stderr)
        sys.exit(2)

    try:
        result = run_loop(project, loop, scenario=scenario, run_name=run_name)
        print(json.dumps(result, indent=2))
        sys.exit(1 if result["status"] in ("refused", "invalid-spec") else 0)
    except Exception as err:
        print(f"run-loop failed unexpectedly: {err}", file=sys.stderr)
        sys.exit(1)

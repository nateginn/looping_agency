# Run contract engine (AgentColabPlan.md "The run contract"). One call
# = one run of one loop for one project. Deterministic and testable by
# design: the judgment step (picking actions) uses a simple heuristic
# here; a human-in-the-loop skill wraps this for real proposal quality.
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import yaml

try:
    from . import dataforseo, gsc, pagespeed
    from .connector_registry import get_connector
    from .lib.credentials import resolve_credential
    from .lib.errors import ConnectorError
    from .lib.github_compare import compare_commit_to_main
    from .lib.gsc_auth import bearer_for_secret
    from .lib.lock import acquire_lock, acquire_named_lock, release_lock, release_named_lock, log_refusal
    from .lib.paths import assert_within
    from .lib.proposals import atomic_write_json, list_proposals, load_json, proposal_path, write_proposal
    from .lib.redact import redact_deep
    from .spec_validate import validate_spec_file, extract_frontmatter
    from .snapshot import write_snapshot
    from .mock_metrics import pull_metrics as pull_mock_metrics
except ImportError:
    import dataforseo
    import gsc
    import pagespeed
    from connector_registry import get_connector
    from lib.credentials import resolve_credential
    from lib.errors import ConnectorError
    from lib.github_compare import compare_commit_to_main
    from lib.gsc_auth import bearer_for_secret
    from lib.lock import acquire_lock, acquire_named_lock, release_lock, release_named_lock, log_refusal
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
APPLY_LOCK_NAME = "apply.lock"
APPLY_LOCK_TTL_MINUTES = 240
LIVE_COMPARE_TARGETS = {"art": {"owner": "nateginn", "repo": "artwebsite"}}
LOCATION_DIFF_FILENAME = "locations-detected.json"


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


def _cooldown_key_from_target(target):
    return _target_key({"page": (target or {}).get("page")})


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


def _section_history(runs_dir, section_name, current_run_id):
    history = []
    if not os.path.isdir(runs_dir):
        return history
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
    }
    if previous_snapshot:
        for key in ("search_analytics", "local_rank", "backlinks", "technical_health"):
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


def _fetch_metrics(spec, run_mode, scenario, previous_snapshot=None, project_dir=None, resolve_credential_fn=None, http_post=None, http_get=None):
    aliases = _aliases_used(spec, run_mode["inputs"])
    resolver = resolve_credential_fn or (lambda alias: resolve_credential(alias, project_dir=project_dir))
    http_get = http_get or gsc._default_http_get
    now = datetime.now(timezone.utc)
    fetched_sections = {}
    tool_calls = []
    analytics_results = []

    for input_name in run_mode["inputs"]:
        handler = (get_connector(input_name) or {}).get("handler")
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
                    language_code=spec.get("language_code") or "en",
                    device=spec.get("device") or "desktop",
                    http_post=http_post or dataforseo._default_http_post,
                    http_get=http_get or dataforseo._default_http_get,
                )
            except Exception as e:
                raise ConnectorError(f"dataforseo-local-rank connector failed: {e}", raw_secrets={}, tool_name="dataforseo-local-rank") from None
            tool_calls.append({"tool": "dataforseo-local-rank", "args": {"locations": [l.get("name") for l in spec.get("locations") or []], "targets": spec.get("targets")}, "at": _now_iso(), "ok": True})
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
            current = fetched_sections.get("technical_health") or {}
            fetched_sections["technical_health"] = {
                "as_of": fetched_pagespeed["as_of"],
                "pagespeed_as_of": fetched_pagespeed["as_of"],
                "indexation_as_of": current.get("indexation_as_of"),
                "pagespeed_rows": fetched_pagespeed["rows"],
                "indexation_rows": current.get("indexation_rows") or [],
                "sitemaps": current.get("sitemaps") or [],
                "secretMap": {**(current.get("secretMap") or {}), **(fetched_pagespeed.get("secretMap") or {})},
            }
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
            current = fetched_sections.get("technical_health") or {}
            as_of = inspections["as_of"]
            fetched_sections["technical_health"] = {
                "as_of": current.get("pagespeed_as_of") or as_of,
                "pagespeed_as_of": current.get("pagespeed_as_of"),
                "indexation_as_of": as_of,
                "pagespeed_rows": current.get("pagespeed_rows") or [],
                "indexation_rows": inspections["rows"],
                "sitemaps": sitemaps["rows"],
                "secretMap": {**(current.get("secretMap") or {}), **(sitemaps.get("secretMap") or {}), **(inspections.get("secretMap") or {})},
            }
            tool_calls.append({"tool": "gsc-indexation", "args": {"pages": _absolute_pages(spec), "site_url": spec.get("site_url")}, "at": _now_iso(), "ok": True})
        else:
            raise ConnectorError(f'run_loop.py: no connector wired for spec input "{input_name}"', raw_secrets={}, tool_name=input_name)

    if analytics_results:
        fetched_sections["search_analytics"] = _merge_metrics(analytics_results)

    secret_map = {}
    for section in fetched_sections.values():
        if isinstance(section, dict):
            secret_map.update(section.get("secretMap") or {})
    return {"sections": fetched_sections, "tool_calls": tool_calls, "secretMap": secret_map}


def _proposal_row_for_page(metrics, proposal):
    target_page = _page_path(proposal["target"]["page"])
    return next((k for k in metrics["keywords"] if _page_path(k.get("page")) == target_page), None)


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
        sample_ok = metrics["sample_size"] >= p["min_sample_size"]

        if not window_elapsed or not sample_ok:
            still_cooling_down.add(_cooldown_key_from_target(p["target"]))
            decisions.append(f'proposal {p["id"]}: still in observation window (age {age_days:.1f}d/{p["observation_window_days"]}d, sample {metrics["sample_size"]}/{p["min_sample_size"]})')
            continue

        row = _proposal_row_for_page(metrics, p)
        ft = spec["failure_threshold"]
        metric_value = row["position"] if row else None
        baseline = p.get("baseline_position")
        drift = (metric_value - baseline) if (row is not None and baseline is not None) else None
        breached = drift is not None and "position" in ft["metric"] and _compare(drift, ft["comparator"], ft["value"])

        p["evaluated_position"] = metric_value
        p["position_delta"] = None if (metric_value is None or baseline is None) else metric_value - baseline

        if breached:
            p["status"] = "breached"
            p["evaluated_run_id"] = run_id
            breach = {
                "proposal_id": p["id"],
                "reason": f'guardrail breach on {ft["metric"]} for {p["target"]["page"]}: position moved {baseline} -> {metric_value} (drift {drift:.1f} {ft["comparator"]} {ft["value"]})',
            }
            decisions.append(f'proposal {p["id"]}: BREACH - {breach["reason"]}')
        else:
            p["status"] = "verified"
            p["evaluated_run_id"] = run_id
            decisions.append(f'proposal {p["id"]}: verified winner (position {baseline} -> {metric_value}, within guardrail)')

    return decisions, breach, still_cooling_down


def _promote_live_implementations(project_slug, loop_dir, pending_dir, proposals, requester=None, now=None, compare_target=None):
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

    lock = acquire_named_lock(loop_dir, APPLY_LOCK_NAME, max_run_duration_minutes=APPLY_LOCK_TTL_MINUTES, runs_dir=os.path.join(loop_dir, "runs"), now=now)
    if not lock["acquired"]:
        for proposal_id in promotions:
            decisions.append(f"proposal {proposal_id}: compare API says live, but apply lock is busy so applied transition will retry next run")
        return decisions, awaiting_ids, stuck_ids

    try:
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
            for proposal in proposals:
                if proposal["id"] == proposal_id:
                    proposal.clear()
                    proposal.update(fresh)
                    break
            decisions.append(f'proposal {proposal_id}: live on GitHub main - transitioned implemented -> applied at {now_iso}')
    finally:
        release_named_lock(loop_dir, lock["run_id"], APPLY_LOCK_NAME)

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
    n = min(max_count, len(candidates), len(allowed_actions))
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


def _status_buckets(proposals):
    buckets = {}
    for proposal in proposals:
        buckets.setdefault(proposal.get("status") or "unknown", []).append(proposal["id"])
    return buckets


def _numeric(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


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
                delta = (_numeric(row.get("organic_rank_position")) or 0) - (_numeric(prev.get("organic_rank_position")) or 0)
                if not _compare(delta, threshold["comparator"], threshold["threshold"]):
                    continue
                ok = True
                for _ in range(1, int(threshold.get("consecutive_runs") or 1)):
                    older = older_rows.get(key)
                    if not older:
                        ok = False
                        break
                    prev_delta = (_numeric(prev.get("organic_rank_position")) or 0) - (_numeric(older.get("organic_rank_position")) or 0)
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


def _report_lines(run_id, project_slug, loop_name, mode, status, decisions, new_proposals, stale_ids, proposals, awaiting_ids, stuck_ids, evaluated_now, snapshot, attention_findings, footer_check):
    status_buckets = _status_buckets(proposals)
    report_lines = [
        f"# Run {run_id} ({loop_name} / {project_slug})",
        "",
        f"**Status:** {status}",
        f"**Mode:** {mode}",
        "",
        f"## Needs Attention ({len(attention_findings)})",
    ]
    report_lines.extend([f"- {item}" for item in attention_findings] if attention_findings else ["- none"])
    if footer_check and footer_check.get("ok") and not footer_check.get("matches"):
        report_lines.append(f'- Monthly footer location check mismatch ({footer_check.get("checked_at")}): detected footer addresses differ from spec.md; see {LOCATION_DIFF_FILENAME}')
    elif footer_check and not footer_check.get("ok"):
        report_lines.append(f'- Monthly footer location check failed ({footer_check.get("checked_at")}): {footer_check.get("error")}')

    local_rank = snapshot.get("local_rank") or {}
    report_lines.extend(["", f'## Local Rank By Location (as of {local_rank.get("as_of") or "n/a"})'])
    if local_rank.get("rows"):
        for row in local_rank["rows"]:
            report_lines.append(f'- {row.get("location_name")}: "{row.get("keyword")}" -> {row.get("organic_rank_position")} on {row.get("page")}')
    else:
        report_lines.append("- none")

    backlinks = snapshot.get("backlinks") or {}
    summary = backlinks.get("summary") or {}
    history = backlinks.get("history") or {}
    report_lines.extend(["", f'## Backlinks (as of {backlinks.get("as_of") or "n/a"})'])
    if backlinks:
        report_lines.append(f'- referring domains: {summary.get("referring_domains")}, backlinks: {summary.get("backlinks")}, new/lost backlinks: {history.get("new_backlinks")}/{history.get("lost_backlinks")}, new/lost referring domains: {history.get("new_referring_domains")}/{history.get("lost_referring_domains")}')
    else:
        report_lines.append("- none")

    tech = snapshot.get("technical_health") or {}
    report_lines.extend(["", f'## Technical Health (as of {tech.get("as_of") or "n/a"})'])
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
    report_lines.extend([f'- {p["id"]}: {p["action_type"]} on {p["target"]["page"]} (tier {p["tier"]})' for p in new_proposals] if new_proposals else ["- none"])
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
    report_lines.extend(["", "## Stale proposals (>=3 cycles undecided)"])
    report_lines.extend([f"- {sid}" for sid in stale_ids] if stale_ids else ["- none"])
    return report_lines


def run_loop(project_slug, loop_name, scenario="normal", run_name=None, _resolve_credential=None, _http_post=None, _http_get=None, _github_requester=None, _github_compare_target=None):
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
                f.write(f"- {_now_iso()} run {run_id}: partial-failure (connector error, redacted)\n")
            return {"status": "partial-failure", "run_id": run_id}

        snapshot = _build_snapshot(previous_snapshot, pulled["sections"])
        secret_map = dict(pulled["secretMap"])
        for section in snapshot.values():
            if isinstance(section, dict):
                secret_map.update(section.get("secretMap") or {})
        snapshot_path = write_snapshot(run_dir, snapshot, secret_map)

        live_decisions, awaiting_ids, stuck_ids = _promote_live_implementations(project_slug, loop_dir, pending_dir, proposals, requester=_github_requester, now=now, compare_target=_github_compare_target)

        eval_decisions = list(live_decisions)
        breach = None
        still_cooling_down = set()
        search_metrics = snapshot.get("search_analytics")
        if run_mode["mode"] != "technical-only" and isinstance(search_metrics, dict) and search_metrics.get("keywords") is not None:
            extra_decisions, breach, still_cooling_down = _evaluate_prior_experiments(proposals, search_metrics, spec, run_id, now)
            eval_decisions.extend(extra_decisions)

        for proposal in proposals:
            if proposal.get("status") in ("applied", "approved", "implemented", "implement-failed"):
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
        elif run_mode["mode"] != "technical-only" and isinstance(search_metrics, dict) and search_metrics.get("keywords") is not None:
            new_proposals, excluded_count = _pick_new_actions(spec, search_metrics, still_cooling_down, run_id, now)
            if excluded_count:
                eval_decisions.append(f"keyword_exclusions filtered {excluded_count} candidate(s)")
            for proposal in new_proposals:
                _write_proposal(pending_dir, proposal)

        for proposal in proposals:
            proposal.pop("_file", None)
            atomic_write_json(proposal_path(pending_dir, proposal["id"]), proposal)
        atomic_write_json(state_path, new_state)

        updated_sections = set(pulled["sections"].keys())
        attention_findings = _evaluate_attention(spec, run_id, runs_dir, snapshot, updated_sections)
        footer_check = _fetch_footer_location_diff(loop_dir, spec, now) if run_mode["mode"] == "full" and "local_rank" in updated_sections else None
        evaluated_now = [p for p in proposals if p.get("evaluated_run_id") == run_id]
        run_json = redact_deep(
            {
                "run_id": run_id,
                "project": project_slug,
                "loop": loop_name,
                "mode": run_mode["mode"],
                "run_name": run_mode["name"],
                "start": _now_iso(now),
                "end": _now_iso(),
                "status": "paused-breach" if new_state["status"] == "paused-breach" else "ok",
                "tool_calls": pulled["tool_calls"],
                "credential_alias_used": _aliases_used(spec, run_mode["inputs"]),
                "decisions": list(eval_decisions),
                "proposals_created": [p["id"] for p in new_proposals],
                "proposals_evaluated": [p["id"] for p in evaluated_now],
                "stale_proposals": stale_ids,
                "awaiting_live_confirmation": awaiting_ids,
                "stuck_implemented": stuck_ids,
                "attention_flags": attention_findings,
                "snapshot": os.path.relpath(snapshot_path, loop_dir),
                "final_status": "paused-breach" if new_state["status"] == "paused-breach" else "ok",
            },
            secret_map,
        )
        atomic_write_json(os.path.join(run_dir, "run.json"), run_json)

        report_lines = _report_lines(run_id, project_slug, loop_name, run_mode["mode"], run_json["status"], eval_decisions, new_proposals, stale_ids, proposals, awaiting_ids, stuck_ids, evaluated_now, snapshot, attention_findings, footer_check)
        with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(report_lines) + "\n")

        with open(memory_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(f'- {_now_iso()} run {run_id}: {run_json["status"]}, mode {run_mode["mode"]}, {len(new_proposals)} new proposal(s), {len(run_json["proposals_evaluated"])} evaluated, {len(stale_ids)} stale, {len(awaiting_ids)} awaiting-live\n')

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

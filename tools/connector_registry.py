"""Shared connector registry for spec validation and runtime dispatch."""
import sys

# `critical` answers one question: if this connector fails, is the run's
# *reasoning* still trustworthy? A critical connector feeds `search_analytics`,
# which is what `_evaluate_prior_experiments` scores guardrail breaches against
# and what `_pick_new_actions` proposes from. Losing it and carrying the
# previous snapshot's section forward would mean evaluating today's proposals
# against stale positions - the exact false-win failure mode PLAN.md Phase 1
# was written to close - so a critical failure still aborts the whole run.
#
# A non-critical connector only fills an enrichment section (local_rank,
# backlinks, technical_health). Those are reported and attention-checked but
# never drive an evaluation verdict, and each section carries its own `as_of`,
# so a stale one is visibly stale rather than silently wrong. Losing one
# degrades the run (status "degraded", the failure recorded in run.json's
# tool_calls and named in report.md) instead of discarding the GSC pull,
# the evaluation, and the week's proposals along with it.
CONNECTOR_REGISTRY = {
    "mock": {
        "handler": "mock",
        "credential_alias": None,
        "requires": [],
        "critical": True,
    },
    "gsc": {
        "handler": "gsc-search-analytics",
        "credential_alias": "gsc",
        "requires": ["site_url", "metrics_window_days"],
        "critical": True,
    },
    "gsc-indexation": {
        "handler": "gsc-indexation",
        "credential_alias": "gsc",
        "requires": ["site_url", "priority_pages"],
        "critical": False,
    },
    "dataforseo": {
        "handler": "dataforseo-serp",
        "credential_alias": "dataforseo",
        # "domain" added 2026-08-16 (Issue #1): a SERP result can only be attributed to
        # this client by host, so a spec without a domain must refuse to validate rather
        # than let the connector fall back to URL-substring matching - which is what
        # recorded competitors' rankings as the client's (defect D1).
        "requires": ["targets", "domain"],
        # Merges into search_analytics (serp_position enrichment on matching
        # rows), so it is a contributor to the evaluated metric set.
        "critical": True,
    },
    "dataforseo-local-rank": {
        "handler": "dataforseo-local-rank",
        "credential_alias": "dataforseo",
        "requires": ["targets", "locations", "domain"],
        "critical": False,
    },
    "dataforseo-backlinks": {
        "handler": "dataforseo-backlinks",
        "credential_alias": "dataforseo",
        "requires": ["domain"],
        "critical": False,
    },
    "dataforseo-maps-rank": {
        "handler": "dataforseo-maps-rank",
        "credential_alias": "dataforseo",
        # "gbp_targets"/"gbp_locations" (not "targets"/"locations" - those
        # require a "page" field that a Maps ranking check has no use for;
        # see spec_validate.py). Failure here degrades the run exactly like
        # dataforseo-local-rank, never aborts it - a Maps-endpoint outage
        # must not cost a week's GSC-driven proposal cycle for an unrelated
        # loop's data (GBP Posts plan Phase 1).
        "requires": ["gbp_targets", "gbp_locations_with_identifier"],
        "critical": False,
    },
    "dataforseo-local-pack": {
        "handler": "dataforseo-local-pack",
        "credential_alias": "dataforseo",
        "requires": ["gbp_targets", "gbp_locations_with_identifier"],
        "critical": False,
    },
    "pagespeed": {
        "handler": "pagespeed",
        # Real alias key so a configured credential_aliases.pagespeed is actually
        # looked up and passed through - PSI's free keyless quota is shared/global
        # and can hit zero with no warning (confirmed live 2026-07-23), so this
        # can't be "None means nobody will ever want a key" the way mock's is.
        # credential_required=False keeps it optional: validation doesn't demand
        # the alias be set, but dispatch uses one if the spec provides it.
        "credential_alias": "pagespeed",
        "credential_required": False,
        "requires": ["priority_pages"],
        "critical": False,
    },
}


def connector_names():
    return list(CONNECTOR_REGISTRY.keys())


def get_connector(name):
    return CONNECTOR_REGISTRY.get(name)


def is_critical(name):
    """True if a failure of this connector must abort the run. Unknown names
    are treated as critical - an unrecognized input is a spec/wiring error,
    not something to quietly degrade past."""
    entry = get_connector(name)
    if entry is None:
        return True
    return bool(entry.get("critical", True))


def _self_test():
    checks = []

    checks.append(("connector_names returns every registered connector", set(connector_names()) == set(CONNECTOR_REGISTRY.keys())))
    checks.append(("get_connector returns the entry for a known name", get_connector("gsc") is CONNECTOR_REGISTRY["gsc"]))
    checks.append(("get_connector returns None for an unknown name", get_connector("not-a-real-connector") is None))

    required_keys = {"handler", "credential_alias", "requires", "critical"}
    all_entries_well_shaped = all(required_keys.issubset(entry.keys()) for entry in CONNECTOR_REGISTRY.values())
    checks.append(("every registry entry has handler/credential_alias/requires/critical", all_entries_well_shaped))

    checks.append(("search-analytics contributors are critical (their loss would evaluate against stale positions)", all(is_critical(n) for n in ("mock", "gsc", "dataforseo"))))
    checks.append(("enrichment-only connectors are degradable, not run-fatal", not any(is_critical(n) for n in ("gsc-indexation", "dataforseo-local-rank", "dataforseo-backlinks", "pagespeed", "dataforseo-maps-rank", "dataforseo-local-pack"))))
    checks.append(("an unknown connector name is treated as critical, never silently degraded", is_critical("not-a-real-connector") is True))

    all_requires_are_lists = all(isinstance(entry["requires"], list) for entry in CONNECTOR_REGISTRY.values())
    checks.append(("every entry's requires field is a list", all_requires_are_lists))

    checks.append(("mock has no alias concept at all (keyless self-contained connector)", CONNECTOR_REGISTRY["mock"]["credential_alias"] is None))
    checks.append(("pagespeed has a real alias key so a configured credential is actually used", CONNECTOR_REGISTRY["pagespeed"]["credential_alias"] == "pagespeed"))
    checks.append(("pagespeed's credential is optional, not required (works keyless if unset)", CONNECTOR_REGISTRY["pagespeed"].get("credential_required") is False))

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__" and "--verify" in sys.argv:
    _self_test()

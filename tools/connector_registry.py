"""Shared connector registry for spec validation and runtime dispatch."""
import sys

CONNECTOR_REGISTRY = {
    "mock": {
        "handler": "mock",
        "credential_alias": None,
        "requires": [],
    },
    "gsc": {
        "handler": "gsc-search-analytics",
        "credential_alias": "gsc",
        "requires": ["site_url", "metrics_window_days"],
    },
    "gsc-indexation": {
        "handler": "gsc-indexation",
        "credential_alias": "gsc",
        "requires": ["site_url", "priority_pages"],
    },
    "dataforseo": {
        "handler": "dataforseo-serp",
        "credential_alias": "dataforseo",
        "requires": ["targets"],
    },
    "dataforseo-local-rank": {
        "handler": "dataforseo-local-rank",
        "credential_alias": "dataforseo",
        "requires": ["targets", "locations"],
    },
    "dataforseo-backlinks": {
        "handler": "dataforseo-backlinks",
        "credential_alias": "dataforseo",
        "requires": ["domain"],
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
    },
}


def connector_names():
    return list(CONNECTOR_REGISTRY.keys())


def get_connector(name):
    return CONNECTOR_REGISTRY.get(name)


def _self_test():
    checks = []

    checks.append(("connector_names returns every registered connector", set(connector_names()) == set(CONNECTOR_REGISTRY.keys())))
    checks.append(("get_connector returns the entry for a known name", get_connector("gsc") is CONNECTOR_REGISTRY["gsc"]))
    checks.append(("get_connector returns None for an unknown name", get_connector("not-a-real-connector") is None))

    required_keys = {"handler", "credential_alias", "requires"}
    all_entries_well_shaped = all(required_keys.issubset(entry.keys()) for entry in CONNECTOR_REGISTRY.values())
    checks.append(("every registry entry has handler/credential_alias/requires", all_entries_well_shaped))

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

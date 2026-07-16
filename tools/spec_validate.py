# Machine-checked schema for loop specs. Validation happens before a run
# is allowed to start - a spec that fails validation refuses to run.
import re
import sys

import yaml

SCHEMA_VERSION = 1
COMPARATORS = ["<", ">", "<=", ">=", "=="]
APPROVAL_MODES = ["propose-only", "tier1-enabled"]
TIERS = [0, 1, 2]
DEVICES = ["desktop", "mobile", "tablet"]
REAL_CONNECTORS = ["gsc", "dataforseo"]

_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)


def is_non_empty_string(v):
    return isinstance(v, str) and v.strip() != ""


def is_positive_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def extract_frontmatter(source):
    """Extract the `---\\n...\\n---` YAML frontmatter block from a spec.md body."""
    match = _FRONTMATTER_RE.match(source)
    if not match:
        return None
    return match.group(1)


def _validate_guardrail_metric(m, idx, errors):
    p = f"guardrail_metrics[{idx}]"
    if not isinstance(m, dict):
        errors.append(f"{p} must be an object")
        return
    if not is_non_empty_string(m.get("name")):
        errors.append(f"{p}.name is required (string)")
    if m.get("comparator") not in COMPARATORS:
        errors.append(f"{p}.comparator must be one of {', '.join(COMPARATORS)}")
    if not isinstance(m.get("threshold"), (int, float)) or isinstance(m.get("threshold"), bool):
        errors.append(f"{p}.threshold must be a number")
    if "consecutive_runs" in m and m["consecutive_runs"] is not None and not is_positive_number(m["consecutive_runs"]):
        errors.append(f"{p}.consecutive_runs must be a positive number if present")


def _validate_allowed_action(a, idx, errors):
    p = f"allowed_actions[{idx}]"
    if not isinstance(a, dict):
        errors.append(f"{p} must be an object")
        return
    if not is_non_empty_string(a.get("type")):
        errors.append(f"{p}.type is required (string)")
    if a.get("tier") not in TIERS:
        errors.append(f"{p}.tier must be one of {', '.join(str(t) for t in TIERS)}")
    has_rollback = is_non_empty_string(a.get("rollback"))
    manual_only = a.get("manual_approval_only") is True
    if not has_rollback and not manual_only:
        errors.append(f"{p} must declare a non-empty rollback, or be marked manual_approval_only: true")
    if not is_positive_number(a.get("observation_window_days")):
        errors.append(f"{p}.observation_window_days must be a positive number")
    if not is_positive_number(a.get("min_sample_size")):
        errors.append(f"{p}.min_sample_size must be a positive number")


def _validate_connector_requirements(spec, inputs, errors):
    """Conditional requirements for real connectors (run_loop.py dispatches on
    spec.inputs, so each real input needs its call parameters up front)."""
    if "gsc" in inputs:
        if not is_non_empty_string(spec.get("site_url")):
            errors.append('site_url is required when "gsc" is in inputs (the exact GSC property, e.g. "sc-domain:example.com")')
        if not is_positive_number(spec.get("metrics_window_days")):
            errors.append('metrics_window_days is required when "gsc" is in inputs (positive number of days, e.g. 28)')

    if "dataforseo" in inputs:
        targets = spec.get("targets")
        if not isinstance(targets, list) or len(targets) < 1:
            errors.append('targets must be a non-empty array of {keyword, page} objects when "dataforseo" is in inputs')
        else:
            for i, t in enumerate(targets):
                if not isinstance(t, dict) or not is_non_empty_string(t.get("keyword")) or not is_non_empty_string(t.get("page")):
                    errors.append(f"targets[{i}] must be an object with non-empty keyword and page strings")
        if spec.get("location_code") is not None and not is_positive_number(spec.get("location_code")):
            errors.append("location_code must be a positive number if present")
        if spec.get("language_code") is not None and not is_non_empty_string(spec.get("language_code")):
            errors.append("language_code must be a non-empty string if present")
        if spec.get("device") is not None and spec.get("device") not in DEVICES:
            errors.append(f"device must be one of {', '.join(DEVICES)} if present")

    for connector in REAL_CONNECTORS:
        if connector in inputs:
            alias = (spec.get("credential_aliases") or {}).get(connector)
            if not is_non_empty_string(alias):
                errors.append(f'credential_aliases.{connector} is required (opaque alias string) when "{connector}" is in inputs')


def validate_spec_object(spec):
    errors = []
    if not isinstance(spec, dict):
        return {"valid": False, "errors": ["spec frontmatter is empty or not a mapping"]}

    if spec.get("version") != SCHEMA_VERSION:
        errors.append(f"version must equal {SCHEMA_VERSION} (got {spec.get('version')!r})")
    if not is_non_empty_string(spec.get("loop")):
        errors.append("loop is required (string)")
    if not is_non_empty_string(spec.get("objective")):
        errors.append("objective is required (string)")
    if not is_non_empty_string(spec.get("primary_metric")):
        errors.append("primary_metric is required (string)")

    guardrails = spec.get("guardrail_metrics")
    if not isinstance(guardrails, list) or len(guardrails) < 1:
        errors.append("guardrail_metrics must be a non-empty array")
    else:
        for i, m in enumerate(guardrails):
            _validate_guardrail_metric(m, i, errors)

    ft = spec.get("failure_threshold")
    if not isinstance(ft, dict):
        errors.append("failure_threshold is required (object)")
    else:
        if not is_non_empty_string(ft.get("metric")):
            errors.append("failure_threshold.metric is required (string)")
        if ft.get("comparator") not in COMPARATORS:
            errors.append(f"failure_threshold.comparator must be one of {', '.join(COMPARATORS)}")
        if not isinstance(ft.get("value"), (int, float)) or isinstance(ft.get("value"), bool):
            errors.append("failure_threshold.value must be a number")

    inputs = spec.get("inputs")
    if not isinstance(inputs, list) or len(inputs) < 1 or not all(is_non_empty_string(i) for i in inputs):
        errors.append("inputs must be a non-empty array of connector alias strings")
    _validate_connector_requirements(spec, inputs if isinstance(inputs, list) else [], errors)

    actions = spec.get("allowed_actions")
    if not isinstance(actions, list) or len(actions) < 1:
        errors.append("allowed_actions must be a non-empty array")
    else:
        for i, a in enumerate(actions):
            _validate_allowed_action(a, i, errors)

    if spec.get("approval_mode") not in APPROVAL_MODES:
        errors.append(f"approval_mode must be one of {', '.join(APPROVAL_MODES)}")
    if not is_positive_number(spec.get("max_run_duration_minutes")):
        errors.append("max_run_duration_minutes must be a positive number")
    if not is_non_empty_string(spec.get("schedule")):
        errors.append("schedule is required (string, cron expression)")
    if not is_non_empty_string(spec.get("stop_condition")):
        errors.append("stop_condition is required (string)")
    if not is_non_empty_string(spec.get("memory")):
        errors.append("memory is required (string path)")

    if spec.get("credential_aliases") is not None:
        aliases = spec["credential_aliases"]
        if not isinstance(aliases, dict):
            errors.append("credential_aliases must be a mapping of connector -> opaque alias string")
        else:
            for k, v in aliases.items():
                if not is_non_empty_string(v):
                    errors.append(f"credential_aliases.{k} must be a non-empty opaque alias string")

    return {"valid": len(errors) == 0, "errors": errors}


def validate_spec_file(spec_path):
    import os

    if not os.path.exists(spec_path):
        return {"valid": False, "errors": [f"spec file not found: {spec_path}"]}
    with open(spec_path, "r", encoding="utf-8") as f:
        source = f.read()
    fm = extract_frontmatter(source)
    if fm is None:
        return {"valid": False, "errors": ["no YAML frontmatter block (--- ... ---) found at top of spec"]}
    try:
        parsed = yaml.safe_load(fm)
    except yaml.YAMLError as err:
        return {"valid": False, "errors": [f"YAML parse error: {err}"]}
    return validate_spec_object(parsed)


def _self_test():
    import os
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="spec-test-")

    good = """---
version: 1
loop: seo
objective: Improve rankings for target keywords
primary_metric: gsc_position
guardrail_metrics:
  - name: ranking_pages_position
    comparator: ">"
    threshold: 5
    consecutive_runs: 1
failure_threshold:
  metric: ranking_pages_position
  comparator: ">"
  value: 5
inputs:
  - gsc
  - dataforseo
site_url: "sc-domain:example.com"
metrics_window_days: 28
targets:
  - keyword: best loop agency
    page: /blog/loop-agency
allowed_actions:
  - type: title-tag-rewrite
    tier: 1
    rollback: revert PR
    observation_window_days: 14
    min_sample_size: 100
approval_mode: propose-only
max_run_duration_minutes: 30
schedule: "0 6 * * 1"
stop_condition: "manual stop via project.md"
memory: memory.md
credential_aliases:
  gsc: acme-gsc-readonly
  dataforseo: acme-dataforseo-read
---
# SEO loop spec
"""
    bad = """---
version: 1
loop: seo
objective: missing required fields
approval_mode: yolo-mode
schedule: "0 6 * * 1"
guardrail_metrics: []
---
# broken spec, missing required fields, bad enum, empty guardrails
"""

    with open(os.path.join(tmp, "good.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(good)
    with open(os.path.join(tmp, "bad.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(bad)

    good_result = validate_spec_file(os.path.join(tmp, "good.md"))
    bad_result = validate_spec_file(os.path.join(tmp, "bad.md"))

    # Conditional connector requirements (Phase 2 wiring).
    no_site_url = validate_spec_object(yaml.safe_load(extract_frontmatter(good.replace('site_url: "sc-domain:example.com"\n', ""))))
    no_window = validate_spec_object(yaml.safe_load(extract_frontmatter(good.replace("metrics_window_days: 28\n", ""))))
    no_targets = validate_spec_object(yaml.safe_load(extract_frontmatter(good.replace("targets:\n  - keyword: best loop agency\n    page: /blog/loop-agency\n", ""))))
    no_dfs_alias = validate_spec_object(yaml.safe_load(extract_frontmatter(good.replace("  dataforseo: acme-dataforseo-read\n", ""))))
    bad_device = validate_spec_object({**yaml.safe_load(extract_frontmatter(good)), "device": "toaster"})
    mock_only = yaml.safe_load(extract_frontmatter(good))
    mock_only["inputs"] = ["mock"]
    for key in ("site_url", "metrics_window_days", "targets"):
        mock_only.pop(key, None)
    mock_only_result = validate_spec_object(mock_only)

    checks = [
        ("valid spec is accepted", good_result["valid"] is True),
        ("valid spec has no errors", len(good_result["errors"]) == 0),
        ("invalid spec is rejected", bad_result["valid"] is False),
        ("invalid spec reports approval_mode enum error", any("approval_mode" in e for e in bad_result["errors"])),
        ("invalid spec reports empty guardrail_metrics", any("guardrail_metrics" in e for e in bad_result["errors"])),
        ("invalid spec reports missing allowed_actions", any("allowed_actions" in e for e in bad_result["errors"])),
        ("missing file is rejected", validate_spec_file(os.path.join(tmp, "missing.md"))["valid"] is False),
        ("gsc input without site_url is rejected", any("site_url" in e for e in no_site_url["errors"])),
        ("gsc input without metrics_window_days is rejected", any("metrics_window_days" in e for e in no_window["errors"])),
        ("dataforseo input without targets is rejected", any("targets" in e for e in no_targets["errors"])),
        ("dataforseo input without a credential alias is rejected", any("credential_aliases.dataforseo" in e for e in no_dfs_alias["errors"])),
        ("invalid device enum is rejected", any("device" in e for e in bad_device["errors"])),
        ("mock-only spec needs none of the connector fields", mock_only_result["valid"] is True),
    ]

    shutil.rmtree(tmp, ignore_errors=True)

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    if "--verify" in sys.argv:
        _self_test()
    else:
        target = sys.argv[1] if len(sys.argv) > 1 else None
        if not target:
            print("usage: python tools/spec_validate.py <spec.md> | --verify", file=sys.stderr)
            sys.exit(2)
        result = validate_spec_file(target)
        if result["valid"]:
            print(f"VALID: {target}")
            sys.exit(0)
        else:
            print(f"INVALID: {target}", file=sys.stderr)
            for e in result["errors"]:
                print(f"  - {e}", file=sys.stderr)
            sys.exit(1)

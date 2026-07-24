# Machine-checked schema for loop specs. Validation happens before a run
# is allowed to start - a spec that fails validation refuses to run.
import re
import sys

import yaml

try:
    from .connector_registry import CONNECTOR_REGISTRY, connector_names, get_connector
except ImportError:
    from connector_registry import CONNECTOR_REGISTRY, connector_names, get_connector

SCHEMA_VERSION = 1
COMPARATORS = ["<", ">", "<=", ">=", "=="]
APPROVAL_MODES = ["propose-only", "tier1-enabled"]
TIERS = [0, 1, 2]
DEVICES = ["desktop", "mobile", "tablet"]
RUN_MODES = ["full", "technical-only"]

_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
_ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")


def is_non_empty_string(v):
    return isinstance(v, str) and v.strip() != ""


def is_positive_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def is_absolute_url(v):
    return is_non_empty_string(v) and v.startswith(("http://", "https://"))


def extract_frontmatter(source):
    """Extract the `---\n...\n---` YAML frontmatter block from a spec.md body."""
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


def _validate_priority_pages(priority_pages, errors, path="priority_pages"):
    if priority_pages is None:
        return
    if not isinstance(priority_pages, list) or not priority_pages:
        errors.append(f"{path} must be a non-empty array of absolute URLs if present")
        return
    for i, page in enumerate(priority_pages):
        if not is_absolute_url(page):
            errors.append(f"{path}[{i}] must be an absolute URL")


def _validate_locations(locations, errors, path="locations"):
    if locations is None:
        return
    if not isinstance(locations, list) or not locations:
        errors.append(f"{path} must be a non-empty array of {{name, address, zip}} objects if present")
        return
    for i, location in enumerate(locations):
        p = f"{path}[{i}]"
        if not isinstance(location, dict):
            errors.append(f"{p} must be an object")
            continue
        if not is_non_empty_string(location.get("name")):
            errors.append(f"{p}.name is required (string)")
        if not is_non_empty_string(location.get("address")):
            errors.append(f"{p}.address is required (string)")
        if not is_non_empty_string(location.get("zip")) or not _ZIP_RE.match(location.get("zip")):
            errors.append(f"{p}.zip is required (5-digit ZIP string)")


def _validate_attention_thresholds(thresholds, errors):
    if thresholds is None:
        return
    if not isinstance(thresholds, list) or not thresholds:
        errors.append("attention_thresholds must be a non-empty array if present")
        return
    for i, threshold in enumerate(thresholds):
        p = f"attention_thresholds[{i}]"
        if not isinstance(threshold, dict):
            errors.append(f"{p} must be an object")
            continue
        kind = threshold.get("kind")
        if kind not in ("numeric_delta", "enum_transition"):
            errors.append(f'{p}.kind must be "numeric_delta" or "enum_transition"')
            continue
        if not is_non_empty_string(threshold.get("metric")):
            errors.append(f"{p}.metric is required (string)")
        if kind == "numeric_delta":
            if threshold.get("comparator") not in COMPARATORS:
                errors.append(f"{p}.comparator must be one of {', '.join(COMPARATORS)}")
            if not isinstance(threshold.get("threshold"), (int, float)) or isinstance(threshold.get("threshold"), bool):
                errors.append(f"{p}.threshold must be a number")
            if not is_positive_number(threshold.get("consecutive_runs")):
                errors.append(f"{p}.consecutive_runs must be a positive number")
            if "to" in threshold:
                errors.append(f"{p}.to is not allowed for numeric_delta")
        else:
            if not is_non_empty_string(threshold.get("to")):
                errors.append(f"{p}.to is required (string)")
            if "consecutive_runs" in threshold:
                errors.append(f"{p}.consecutive_runs is not allowed for enum_transition")
            for field in ("comparator", "threshold"):
                if field in threshold:
                    errors.append(f"{p}.{field} is not allowed for enum_transition")


def _validate_connector_requirements(spec, inputs, errors, path_prefix=""):
    aliases = spec.get("credential_aliases") or {}
    for i, connector_name in enumerate(inputs):
        entry = get_connector(connector_name)
        p = f"{path_prefix}inputs[{i}]"
        if entry is None:
            errors.append(f'{p} "{connector_name}" is not a known connector (known: {", ".join(connector_names())})')
            continue

        alias_key = entry.get("credential_alias")
        if alias_key and entry.get("credential_required", True):
            alias = aliases.get(alias_key)
            if not is_non_empty_string(alias):
                errors.append(f'{path_prefix}credential_aliases.{alias_key} is required (opaque alias string) when "{connector_name}" is in inputs')

        for requirement in entry.get("requires") or []:
            if requirement == "site_url" and not is_non_empty_string(spec.get("site_url")):
                errors.append(f'{path_prefix}site_url is required when "{connector_name}" is in inputs (the exact GSC property)')
            elif requirement == "metrics_window_days" and not is_positive_number(spec.get("metrics_window_days")):
                errors.append(f'{path_prefix}metrics_window_days is required when "{connector_name}" is in inputs (positive number of days)')
            elif requirement == "targets":
                targets = spec.get("targets")
                if not isinstance(targets, list) or len(targets) < 1:
                    errors.append(f'{path_prefix}targets must be a non-empty array of {{keyword, page}} objects when "{connector_name}" is in inputs')
                else:
                    for t_idx, t in enumerate(targets):
                        if not isinstance(t, dict) or not is_non_empty_string(t.get("keyword")) or not is_non_empty_string(t.get("page")):
                            errors.append(f"{path_prefix}targets[{t_idx}] must be an object with non-empty keyword and page strings")
            elif requirement == "locations":
                locations = spec.get("locations")
                if not isinstance(locations, list) or len(locations) < 1:
                    errors.append(f'{path_prefix}locations must be a non-empty array when "{connector_name}" is in inputs')
            elif requirement == "domain" and not is_non_empty_string(spec.get("domain")):
                errors.append(f'{path_prefix}domain is required when "{connector_name}" is in inputs')
            elif requirement == "priority_pages":
                priority_pages = spec.get("priority_pages")
                if not isinstance(priority_pages, list) or len(priority_pages) < 1:
                    errors.append(f'{path_prefix}priority_pages must be a non-empty array when "{connector_name}" is in inputs')

    if "dataforseo" in inputs or "dataforseo-local-rank" in inputs:
        if spec.get("language_code") is not None and not is_non_empty_string(spec.get("language_code")):
            errors.append(f"{path_prefix}language_code must be a non-empty string if present")
        if spec.get("device") is not None and spec.get("device") not in DEVICES:
            errors.append(f"{path_prefix}device must be one of {', '.join(DEVICES)} if present")


def _validate_schedule_entry(schedule, idx, spec_inputs, spec, errors):
    p = f"additional_schedules[{idx}]"
    if not isinstance(schedule, dict):
        errors.append(f"{p} must be an object")
        return
    if not is_non_empty_string(schedule.get("name")):
        errors.append(f"{p}.name is required (string)")
    if not is_non_empty_string(schedule.get("cron")):
        errors.append(f"{p}.cron is required (string)")
    if schedule.get("mode") is not None and schedule.get("mode") not in RUN_MODES:
        errors.append(f"{p}.mode must be one of {', '.join(RUN_MODES)} if present")
    inputs = schedule.get("inputs")
    if not isinstance(inputs, list) or not inputs or not all(is_non_empty_string(i) for i in inputs):
        errors.append(f"{p}.inputs must be a non-empty array of connector alias strings")
        return
    for name in inputs:
        if name not in spec_inputs:
            errors.append(f'{p}.inputs contains "{name}" which is not present in top-level inputs')
    _validate_connector_requirements(spec, inputs, errors, path_prefix=f"{p}.")


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
    else:
        for i, name in enumerate(inputs):
            if name not in CONNECTOR_REGISTRY:
                errors.append(f'inputs[{i}] "{name}" is not a known connector (known: {", ".join(connector_names())})')
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

    if spec.get("keyword_exclusions") is not None:
        exclusions = spec["keyword_exclusions"]
        if not isinstance(exclusions, list) or not all(is_non_empty_string(t) for t in exclusions):
            errors.append("keyword_exclusions must be an array of non-empty strings if present")

    _validate_priority_pages(spec.get("priority_pages"), errors)
    _validate_locations(spec.get("locations"), errors)
    _validate_attention_thresholds(spec.get("attention_thresholds"), errors)

    additional_schedules = spec.get("additional_schedules")
    if additional_schedules is not None:
        if not isinstance(additional_schedules, list):
            errors.append("additional_schedules must be an array if present")
        else:
            seen_names = set()
            for i, schedule in enumerate(additional_schedules):
                _validate_schedule_entry(schedule, i, inputs if isinstance(inputs, list) else [], spec, errors)
                name = schedule.get("name") if isinstance(schedule, dict) else None
                if is_non_empty_string(name):
                    if name in seen_names:
                        errors.append(f'additional_schedules[{i}].name "{name}" is duplicated')
                    seen_names.add(name)

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
  - dataforseo-local-rank
  - dataforseo-backlinks
  - pagespeed
  - gsc-indexation
site_url: "sc-domain:example.com"
domain: "example.com"
metrics_window_days: 28
targets:
  - keyword: best loop agency
    page: /blog/loop-agency
priority_pages:
  - https://example.com/blog/loop-agency
locations:
  - name: Denver
    address: "2480 W 26th Ave #90B, Denver, CO 80211"
    zip: "80211"
allowed_actions:
  - type: title-tag-rewrite
    tier: 1
    rollback: revert PR
    observation_window_days: 14
    min_sample_size: 100
approval_mode: propose-only
max_run_duration_minutes: 30
schedule: "0 6 * * 1"
additional_schedules:
  - name: technical-thursday
    cron: "0 6 * * 4"
    mode: technical-only
    inputs:
      - pagespeed
      - gsc-indexation
stop_condition: "manual stop via project.md"
memory: memory.md
credential_aliases:
  gsc: acme-gsc-readonly
  dataforseo: acme-dataforseo-read
attention_thresholds:
  - kind: numeric_delta
    metric: organic_rank_position
    comparator: ">"
    threshold: 5
    consecutive_runs: 2
  - kind: enum_transition
    metric: cwv_status
    to: poor
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

    no_site_url = validate_spec_object(yaml.safe_load(extract_frontmatter(good.replace('site_url: "sc-domain:example.com"\n', ""))))
    no_window = validate_spec_object(yaml.safe_load(extract_frontmatter(good.replace("metrics_window_days: 28\n", ""))))
    no_targets = validate_spec_object(yaml.safe_load(extract_frontmatter(good.replace("targets:\n  - keyword: best loop agency\n    page: /blog/loop-agency\n", ""))))
    no_locations = validate_spec_object(yaml.safe_load(extract_frontmatter(good.replace('locations:\n  - name: Denver\n    address: "2480 W 26th Ave #90B, Denver, CO 80211"\n    zip: "80211"\n', ""))))
    no_priority_pages = validate_spec_object(yaml.safe_load(extract_frontmatter(good.replace("priority_pages:\n  - https://example.com/blog/loop-agency\n", ""))))
    bad_schedule = validate_spec_object({**yaml.safe_load(extract_frontmatter(good)), "additional_schedules": [{"name": "bad", "cron": "0 6 * * 4", "inputs": ["mock"]}]})
    bad_attention = validate_spec_object({**yaml.safe_load(extract_frontmatter(good)), "attention_thresholds": [{"kind": "enum_transition", "metric": "cwv_status", "consecutive_runs": 2}]})
    bad_zip = validate_spec_object({**yaml.safe_load(extract_frontmatter(good)), "locations": [{"name": "Denver", "address": "x", "zip": "zip"}]})
    no_dfs_alias = validate_spec_object(yaml.safe_load(extract_frontmatter(good.replace("  dataforseo: acme-dataforseo-read\n", ""))))
    bad_device = validate_spec_object({**yaml.safe_load(extract_frontmatter(good)), "device": "toaster"})
    typo_input = validate_spec_object({**yaml.safe_load(extract_frontmatter(good)), "inputs": ["gscc", "dataforseo"]})
    good_exclusions = validate_spec_object({**yaml.safe_load(extract_frontmatter(good)), "keyword_exclusions": ["accelerate health"]})
    bad_exclusions = validate_spec_object({**yaml.safe_load(extract_frontmatter(good)), "keyword_exclusions": ["ok", ""]})
    mock_only = yaml.safe_load(extract_frontmatter(good))
    mock_only["inputs"] = ["mock"]
    for key in ("site_url", "metrics_window_days", "targets", "locations", "priority_pages", "domain", "additional_schedules"):
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
        ("dataforseo input without locations is rejected", any("locations" in e for e in no_locations["errors"])),
        ("pagespeed input without priority_pages is rejected", any("priority_pages" in e for e in no_priority_pages["errors"])),
        ("additional schedule inputs must be a top-level subset", any("not present in top-level inputs" in e for e in bad_schedule["errors"])),
        ("enum_transition rejects consecutive_runs", any("consecutive_runs is not allowed" in e for e in bad_attention["errors"])),
        ("location ZIPs are validated", any(".zip is required" in e for e in bad_zip["errors"])),
        ("dataforseo input without a credential alias is rejected", any("credential_aliases.dataforseo" in e for e in no_dfs_alias["errors"])),
        ("invalid device enum is rejected", any("device" in e for e in bad_device["errors"])),
        ("typo'd connector name in inputs is rejected pre-run", any('"gscc" is not a known connector' in e for e in typo_input["errors"])),
        ("mock-only spec needs none of the connector fields", mock_only_result["valid"] is True),
        ("valid keyword_exclusions list is accepted", good_exclusions["valid"] is True),
        ("keyword_exclusions with an empty string is rejected", any("keyword_exclusions" in e for e in bad_exclusions["errors"])),
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

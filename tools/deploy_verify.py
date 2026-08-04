# Phase 6a (PLAN.md "Autonomous merge and deployment safety" / RISK-
# REGISTER.md R11-R12) - automatic post-deployment health verification and
# rollback ORCHESTRATION.
#
# Health checks themselves are Tier 0 (read-only, unauthenticated public
# DNS/TLS/HTTP - lib/deploy_health.py). Actually triggering a redeploy of
# the previous known-good SHA is Tier 2 (push/deploy, human-only, always -
# CLAUDE.md, RISK-REGISTER.md R6/R10): this module's default
# redeploy_previous_sha hook does not perform one - see
# DeploymentIntegrationError below for the exact seam a human or a future
# verified-auto-merge path must close. This module never stores or
# resolves a credential of any kind.
#
# State machine over durable per-deploy JSON records (same
# atomic_write_json/redact_deep idioms as every other tool in this
# workspace), not a blocking sleep: record_deployment() is called once,
# immediately, when a deploy is detected (non-blocking, returns instantly).
# A separate, idempotent, schedulable run_pending_verifications() call
# (mirroring watchdog.py's existing out-of-band, human-registered-via-
# Task-Scheduler convention - see PHASE3-SCHEDULING.md) only acts on
# records whose stabilization window has actually elapsed. No process in
# this workspace ever sleeps for ~15 minutes to implement the delay.
#
# Rollback here means redeploying a previous, already-migrated commit -
# never a database migration reversal (a destructive, data-loss-prone
# operation this module does not perform and has no code path for). This
# is also *why* the actual redeploy trigger is a human/Tier-2 seam rather
# than something this tool does automatically: whether replaying an old
# commit against a forward-migrated database is safe is exactly the kind
# of judgment call that stays human, not automated.
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    from .lib.deploy_health import run_health_checks
    from .lib.proposals import atomic_write_json, load_json
    from .lib.redact import redact_deep
except ImportError:
    from lib.deploy_health import run_health_checks
    from lib.proposals import atomic_write_json, load_json
    from lib.redact import redact_deep

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(THIS_DIR)
PROJECTS_ROOT = os.path.join(WORKSPACE_ROOT, "projects")

DEFAULT_STABILIZATION_MINUTES = 15
REQUIRED_RECORD_FIELDS = ("id", "status", "deployed_at", "health_config", "previous_sha", "new_sha")


class DeploymentIntegrationError(Exception):
    """Raised by the default redeploy_previous_sha hook. Actually
    triggering a redeploy is Tier 2 (push/deploy, human-only, always -
    RISK-REGISTER.md R6/R10) - no tooling in this workspace performs one
    automatically, ever. This is the exact remaining integration point:
    wire a real `redeploy_previous_sha` callable to either (a) a human-run
    script Nate executes when paged, or (b) an automated revert-PR path
    through tools/publish.py once Phase 0's branch protection and a
    verified auto-merge path are live (RISK-REGISTER.md R11) - never a
    credential this tool stores or calls itself."""


def _default_redeploy_previous_sha(previous_sha, context):
    raise DeploymentIntegrationError(
        f"REFUSED: automatic redeploy of {previous_sha} is not implemented - redeploy/push/merge is Tier 2 "
        "(human-only, always; CLAUDE.md, RISK-REGISTER.md R6/R10). Escalating for manual action instead of "
        "performing one. Wire a real redeploy_previous_sha callable to close this integration point (see "
        "DeploymentIntegrationError's docstring)."
    )


def _now(now=None):
    return now or datetime.now(timezone.utc)


def _now_iso(now=None):
    return _now(now).isoformat().replace("+00:00", "Z")


def deploys_dir_for(project, loop):
    return os.path.join(PROJECTS_ROOT, project, "loops", loop, "deploys")


def _deploy_path(project, loop, deploy_id):
    return os.path.join(deploys_dir_for(project, loop), f"{deploy_id}.json")


def _write_record(project, loop, record):
    # Redact before every write - defense in depth. Health checks
    # themselves are unauthenticated, but an error string surfaced from a
    # future redeploy hook, extractor, or transport failure must never
    # leak a secret onto disk even so (same pattern as run.json/
    # snapshot.json elsewhere in this workspace).
    atomic_write_json(_deploy_path(project, loop, record["id"]), redact_deep(record))


def record_deployment(project, loop, new_sha, previous_sha, health_config, deploy_id=None, deployed_at=None, now=None):
    """Called once, immediately, when a deploy is detected (e.g. from
    run_loop.py's existing implemented->applied live-detection via an
    optional on_promoted callback) - never blocks, never sleeps. Persists
    an `awaiting-verification` record that a later, separate
    run_pending_verifications() call acts on once the stabilization window
    has elapsed.

    `health_config` must be JSON-serializable (hostname, base_url,
    expected_status, representative_pages, expected_sha,
    commit_evidence_url, retries, timeout) - never a callable. Extractors
    and injected transport fakes are passed to verify_one_deployment/
    run_pending_verifications directly, never stored on the record."""
    if not isinstance(project, str) or not project.strip():
        raise ValueError("record_deployment: project must be a non-empty string")
    if not isinstance(loop, str) or not loop.strip():
        raise ValueError("record_deployment: loop must be a non-empty string")
    if not isinstance(new_sha, str) or not new_sha.strip():
        raise ValueError("record_deployment: new_sha must be a non-empty string")
    if not isinstance(previous_sha, str) or not previous_sha.strip():
        raise ValueError("record_deployment: previous_sha must be a non-empty string")
    if not isinstance(health_config, dict) or not health_config.get("hostname") or not health_config.get("base_url"):
        raise ValueError("record_deployment: health_config must be an object with at least hostname and base_url")

    deploy_id = deploy_id or f"deploy-{new_sha[:12]}-{_now(now).strftime('%Y%m%dT%H%M%S')}"
    record = {
        "id": deploy_id,
        "project": project,
        "loop": loop,
        "new_sha": new_sha,
        "previous_sha": previous_sha,
        "health_config": health_config,
        "status": "awaiting-verification",
        "created_at": _now_iso(now),
        "deployed_at": _now_iso(deployed_at or now),
        "health_results": None,
        "rollback": None,
        "escalation": None,
    }
    _write_record(project, loop, record)
    return record


def list_deploys(project, loop):
    d = deploys_dir_for(project, loop)
    if not os.path.exists(d):
        return []
    out = []
    for name in sorted(os.listdir(d)):
        if name.endswith(".json"):
            out.append(load_json(os.path.join(d, name)))
    return out


def _validate_record_shape(record):
    if not isinstance(record, dict):
        raise ValueError("deploy record is not an object")
    missing = [f for f in REQUIRED_RECORD_FIELDS if not record.get(f)]
    if missing:
        raise ValueError(f"deploy record {record.get('id', '<unknown>')!r} is missing required field(s): {missing}")
    if not isinstance(record["health_config"], dict) or not record["health_config"].get("hostname") or not record["health_config"].get("base_url"):
        raise ValueError(f"deploy record {record['id']!r} has a malformed health_config (needs at least hostname and base_url)")


def _parse_iso(value, label):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as err:
        raise ValueError(f"deploy record has an unparseable {label} timestamp {value!r}: {err}") from None


def _due(record, now_dt, stabilization_minutes):
    deployed_at = _parse_iso(record["deployed_at"], "deployed_at")
    return now_dt >= deployed_at + timedelta(minutes=stabilization_minutes)


def _run_checks(health_config, http_get, resolver, tls_connector, sleep, commit_evidence_extractor, now):
    return run_health_checks(
        health_config, http_get=http_get, resolver=resolver, tls_connector=tls_connector, sleep=sleep, commit_evidence_extractor=commit_evidence_extractor, now=now
    )


def verify_one_deployment(
    project,
    loop,
    record,
    *,
    stabilization_minutes=DEFAULT_STABILIZATION_MINUTES,
    http_get=None,
    resolver=None,
    tls_connector=None,
    sleep=None,
    commit_evidence_extractor=None,
    redeploy_previous_sha=None,
    now=None,
):
    """Advances a single deploy record's state machine by exactly one
    step, if (and only if) it is currently due. Returns the (possibly
    unchanged) record; never raises on a legitimate health/rollback
    failure - those are recorded outcomes, not exceptions. Raises
    ValueError only for a malformed/unparseable record (missing or
    corrupted deployment metadata).

        awaiting-verification --(not yet due)--> unchanged
        awaiting-verification --(due, healthy)--> verified [terminal]
        awaiting-verification --(due, unhealthy)--> rollback-attempted
          --(redeploy hook raises)--> rollback-failed-escalated [terminal]
          --(redeploy succeeds, recheck healthy)--> rolled-back-verified [terminal]
          --(redeploy succeeds, recheck still unhealthy)--> rollback-verification-failed-escalated [terminal]
    """
    _validate_record_shape(record)
    redeploy_previous_sha = redeploy_previous_sha or _default_redeploy_previous_sha
    now_dt = _now(now)

    if record.get("status") != "awaiting-verification":
        return record
    if not _due(record, now_dt, stabilization_minutes):
        return record

    health_results = _run_checks(record["health_config"], http_get, resolver, tls_connector, sleep, commit_evidence_extractor, now)
    record["health_results"] = health_results
    record["verified_at"] = _now_iso(now)

    if health_results["ok"]:
        record["status"] = "verified"
        record["final_status"] = "verified"
        _write_record(project, loop, record)
        return record

    # Health failed - record the failure and attempt rollback.
    record["status"] = "rollback-attempted"
    record["rollback"] = {"attempted_at": _now_iso(now), "target_sha": record["previous_sha"], "succeeded": False}
    _write_record(project, loop, record)

    try:
        redeploy_result = redeploy_previous_sha(
            record["previous_sha"], {"project": project, "loop": loop, "deploy_id": record["id"], "failed_sha": record["new_sha"]}
        )
    except Exception as err:
        record["status"] = "rollback-failed-escalated"
        record["final_status"] = "rollback-failed-escalated"
        record["rollback"]["error"] = str(err)
        record["rollback"]["succeeded"] = False
        record["escalation"] = f"ESCALATE: rollback to {record['previous_sha']} failed - {err}. Manual action required (Tier 2, human-only)."
        _write_record(project, loop, record)
        return record

    record["rollback"]["succeeded"] = True
    record["rollback"]["redeployed_at"] = _now_iso(now)
    record["rollback"]["redeploy_result"] = redeploy_result if isinstance(redeploy_result, (dict, str, int, float, bool)) or redeploy_result is None else str(redeploy_result)

    rollback_health_config = dict(record["health_config"], expected_sha=record["previous_sha"])
    rollback_health = _run_checks(rollback_health_config, http_get, resolver, tls_connector, sleep, commit_evidence_extractor, now)
    record["rollback"]["health_results"] = rollback_health

    if rollback_health["ok"]:
        record["status"] = "rolled-back-verified"
        record["final_status"] = "rolled-back-verified"
    else:
        record["status"] = "rollback-verification-failed-escalated"
        record["final_status"] = "rollback-verification-failed-escalated"
        record["escalation"] = (
            f"ESCALATE: redeployed {record['previous_sha']} but post-rollback health checks still failed. "
            "Manual investigation required immediately - the site may be in a degraded or fully down state."
        )
    _write_record(project, loop, record)
    return record


def run_pending_verifications(project, loop, **kwargs):
    """Scans every deploy record for this project/loop and advances each
    one that is due. Safe to call repeatedly/idempotently (e.g. every few
    minutes via Task Scheduler, mirroring watchdog.py's existing
    convention - never auto-registered by tooling) - a record not yet due,
    or already in a terminal state, is returned unchanged, not re-acted
    on."""
    return [verify_one_deployment(project, loop, record, **kwargs) for record in list_deploys(project, loop)]


def render_report(record):
    lines = [
        f"# Deploy verification report — {record['id']}",
        "",
        f"- Project/loop: {record.get('project')}/{record.get('loop')}",
        f"- New SHA: {record.get('new_sha')}",
        f"- Previous (known-good) SHA: {record.get('previous_sha')}",
        f"- Deployed at: {record.get('deployed_at')}",
        f"- Status: **{record.get('final_status', record.get('status'))}**",
    ]
    if record.get("health_results"):
        lines += ["", "## Health checks"]
        lines += [f"- {'PASS' if c['ok'] else 'FAIL'} — {c['name']}: {c['detail']}" for c in record["health_results"]["checks"]]
    if record.get("rollback"):
        rb = record["rollback"]
        lines += ["", "## Rollback", f"- Attempted at: {rb.get('attempted_at')}", f"- Succeeded: {rb.get('succeeded')}"]
        if rb.get("error"):
            lines.append(f"- Error: {rb['error']}")
        if rb.get("health_results"):
            lines.append("- Post-rollback health checks:")
            lines += [f"  - {'PASS' if c['ok'] else 'FAIL'} — {c['name']}: {c['detail']}" for c in rb["health_results"]["checks"]]
    if record.get("escalation"):
        lines += ["", f"## ESCALATION\n{record['escalation']}"]
    lines += [
        "",
        "This report reflects deployment/site health only - it draws no conclusion about SEO performance. "
        "SEO impact remains a later, separate observation-window measurement.",
    ]
    return "\n".join(lines) + "\n"


def _cli():
    args = sys.argv[1:]
    if len(args) < 3:
        print(
            "usage: python tools/deploy_verify.py <project> <loop> --record --new-sha S --previous-sha S --hostname H --base-url U [--page URL]...\n"
            "       python tools/deploy_verify.py <project> <loop> --check",
            file=sys.stderr,
        )
        sys.exit(2)
    project, loop = args[0], args[1]
    rest = args[2:]

    def _opt(flag):
        return rest[rest.index(flag) + 1] if flag in rest and rest.index(flag) + 1 < len(rest) else None

    try:
        if "--record" in rest:
            new_sha, previous_sha, hostname, base_url = _opt("--new-sha"), _opt("--previous-sha"), _opt("--hostname"), _opt("--base-url")
            if not all([new_sha, previous_sha, hostname, base_url]):
                print("ERROR: --record requires --new-sha, --previous-sha, --hostname, --base-url", file=sys.stderr)
                sys.exit(2)
            pages = [rest[i + 1] for i, a in enumerate(rest) if a == "--page" and i + 1 < len(rest)]
            record = record_deployment(
                project, loop, new_sha, previous_sha, {"hostname": hostname, "base_url": base_url, "representative_pages": pages, "expected_sha": new_sha}
            )
            print(f'recorded {record["id"]} (status={record["status"]})')
        elif "--check" in rest:
            results = run_pending_verifications(project, loop)
            if not results:
                print("no deploy records found")
            for r in results:
                print(f'{r["id"]}: status={r["status"]}')
        else:
            print("no action specified (--record or --check)", file=sys.stderr)
            sys.exit(2)
    except Exception as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)


def _self_test():
    import shutil

    project = "_deploy-verify-selftest-tmp"
    loop = "seo"
    project_dir = os.path.join(PROJECTS_ROOT, project)
    shutil.rmtree(project_dir, ignore_errors=True)

    checks = []
    NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
    DEPLOYED_AT = NOW - timedelta(minutes=20)  # already past the 15-minute stabilization window
    NOT_DUE_YET = NOW - timedelta(minutes=5)  # not yet past it

    HEALTH_CONFIG = {
        "hostname": "example.com",
        "base_url": "https://example.com",
        "representative_pages": ["https://example.com/physical-therapy/"],
        "expected_sha": "newsha1234567",
    }

    def _all_ok_http_get(url, timeout):
        if url.rstrip("/").endswith("robots.txt"):
            return 200, {}, b"User-agent: *\nDisallow: /wp-admin/\n"
        return 200, {}, b'<html><head><link rel="canonical" href="https://example.com/"></head></html>'

    def _all_fail_http_get(url, timeout):
        return 503, {}, b"service unavailable"

    def _ok_resolver(h):
        return "1.2.3.4"

    def _ok_tls(h, p, t):
        return {"ok": True}

    def _no_sleep(s):
        return None

    def _reset():
        shutil.rmtree(project_dir, ignore_errors=True)

    # ---- record_deployment: malformed/missing metadata ----
    _reset()
    for bad_kwargs, expected_fragment in (
        (dict(new_sha="", previous_sha="old", health_config=HEALTH_CONFIG), "new_sha"),
        (dict(new_sha="new", previous_sha="", health_config=HEALTH_CONFIG), "previous_sha"),
        (dict(new_sha="new", previous_sha="old", health_config={}), "health_config"),
        (dict(new_sha="new", previous_sha="old", health_config={"hostname": "example.com"}), "health_config"),
    ):
        refused = False
        try:
            record_deployment(project, loop, **bad_kwargs)
        except ValueError as e:
            refused = expected_fragment in str(e)
        checks.append((f"record_deployment refuses malformed/missing metadata ({expected_fragment})", refused))

    # ---- healthy deployment: end to end, verified ----
    _reset()
    record = record_deployment(project, loop, "newsha1234567", "oldsha7654321", HEALTH_CONFIG, deploy_id="deploy-healthy", deployed_at=DEPLOYED_AT, now=NOW)
    checks.append(("record_deployment starts a record as awaiting-verification", record["status"] == "awaiting-verification"))
    checks.append(("record_deployment persists to disk", os.path.exists(_deploy_path(project, loop, "deploy-healthy"))))

    not_due = verify_one_deployment(project, loop, dict(record, deployed_at=_now_iso(NOT_DUE_YET)), http_get=_all_ok_http_get, resolver=_ok_resolver, tls_connector=_ok_tls, sleep=_no_sleep, now=NOW)
    checks.append(("verify_one_deployment leaves a not-yet-due record untouched", not_due["status"] == "awaiting-verification" and not_due.get("health_results") is None))

    verified = verify_one_deployment(project, loop, record, http_get=_all_ok_http_get, resolver=_ok_resolver, tls_connector=_ok_tls, sleep=_no_sleep, now=NOW)
    checks.append(("a healthy deployment (due, all checks pass) transitions to verified", verified["status"] == "verified" and verified["final_status"] == "verified"))
    checks.append(("verified record carries its health_results", verified["health_results"]["ok"] is True))
    stored_verified = load_json(_deploy_path(project, loop, "deploy-healthy"))
    checks.append(("verified status is persisted to disk", stored_verified["status"] == "verified"))

    already_terminal = verify_one_deployment(project, loop, verified, http_get=_all_fail_http_get, resolver=_ok_resolver, tls_connector=_ok_tls, sleep=_no_sleep, now=NOW)
    checks.append(("a terminal (verified) record is never re-acted on, even if called again", already_terminal["status"] == "verified"))

    # ---- transient health failure followed by success (bounded retries) ----
    _reset()
    transient_calls = {"n": 0}

    def _transient_then_ok_http_get(url, timeout):
        transient_calls["n"] += 1
        if transient_calls["n"] <= 2:
            raise TimeoutError("connection timed out")
        if url.rstrip("/").endswith("robots.txt"):
            return 200, {}, b"User-agent: *\nDisallow: /wp-admin/\n"
        return 200, {}, b'<html><head><link rel="canonical" href="https://example.com/"></head></html>'

    record2 = record_deployment(project, loop, "newsha2", "oldsha2", HEALTH_CONFIG, deploy_id="deploy-transient", deployed_at=DEPLOYED_AT, now=NOW)
    verified2 = verify_one_deployment(
        project, loop, record2, http_get=_transient_then_ok_http_get, resolver=_ok_resolver, tls_connector=_ok_tls, sleep=_no_sleep, now=NOW
    )
    checks.append(("a transient health failure that recovers within the retry bound still verifies", verified2["status"] == "verified"))
    checks.append(("the retry actually happened (more than one HTTP attempt was made)", transient_calls["n"] > 1))

    # ---- failed deployment -> successful rollback ----
    _reset()
    rollback_state = {"rolled_back": False}

    def _fails_until_rolled_back_http_get(url, timeout):
        if rollback_state["rolled_back"]:
            if url.rstrip("/").endswith("robots.txt"):
                return 200, {}, b"User-agent: *\nDisallow: /wp-admin/\n"
            return 200, {}, b'<html><head><link rel="canonical" href="https://example.com/"></head></html>'
        return 500, {}, b"internal server error"

    def _successful_redeploy(previous_sha, context):
        rollback_state["rolled_back"] = True
        return {"redeployed_sha": previous_sha}

    record3 = record_deployment(project, loop, "badsha3", "goodsha3", HEALTH_CONFIG, deploy_id="deploy-rollback-ok", deployed_at=DEPLOYED_AT, now=NOW)
    rolled_back = verify_one_deployment(
        project, loop, record3,
        http_get=_fails_until_rolled_back_http_get, resolver=_ok_resolver, tls_connector=_ok_tls, sleep=_no_sleep,
        redeploy_previous_sha=_successful_redeploy, now=NOW,
    )
    checks.append(("a failed deployment triggers a rollback attempt", rolled_back["rollback"] is not None))
    checks.append(("a successful rollback + healthy recheck transitions to rolled-back-verified", rolled_back["status"] == "rolled-back-verified"))
    checks.append(("rollback record notes target_sha == previous_sha", rolled_back["rollback"]["target_sha"] == "goodsha3"))
    checks.append(("rollback record notes succeeded=True", rolled_back["rollback"]["succeeded"] is True))
    checks.append(("post-rollback health_results are recorded on the rollback sub-record", rolled_back["rollback"]["health_results"]["ok"] is True))
    checks.append(("original (pre-rollback) health_results are still recorded on the top-level record", rolled_back["health_results"]["ok"] is False))
    stored_rollback = load_json(_deploy_path(project, loop, "deploy-rollback-ok"))
    checks.append(("rolled-back-verified status is persisted to disk", stored_rollback["status"] == "rolled-back-verified"))

    # ---- rollback failure and escalation (redeploy hook itself fails) ----
    _reset()
    record4 = record_deployment(project, loop, "badsha4", "goodsha4", HEALTH_CONFIG, deploy_id="deploy-rollback-fail", deployed_at=DEPLOYED_AT, now=NOW)

    def _failing_redeploy(previous_sha, context):
        raise RuntimeError("SSH connection refused")

    rollback_failed = verify_one_deployment(
        project, loop, record4, http_get=_all_fail_http_get, resolver=_ok_resolver, tls_connector=_ok_tls, sleep=_no_sleep, redeploy_previous_sha=_failing_redeploy, now=NOW
    )
    checks.append(("a redeploy hook failure transitions to rollback-failed-escalated", rollback_failed["status"] == "rollback-failed-escalated"))
    checks.append(("rollback failure records the underlying error", "SSH connection refused" in rollback_failed["rollback"]["error"]))
    checks.append(("rollback failure sets an explicit escalation message", rollback_failed["escalation"] is not None and "ESCALATE" in rollback_failed["escalation"]))
    checks.append(("rollback failure never claims succeeded=True", rollback_failed["rollback"]["succeeded"] is False))

    # ---- rollback failure and escalation (redeploy succeeds but recheck still fails) ----
    _reset()
    record5 = record_deployment(project, loop, "badsha5", "goodsha5", HEALTH_CONFIG, deploy_id="deploy-rollback-still-broken", deployed_at=DEPLOYED_AT, now=NOW)

    def _redeploy_but_site_stays_down(previous_sha, context):
        return "redeployed"

    rollback_still_broken = verify_one_deployment(
        project, loop, record5, http_get=_all_fail_http_get, resolver=_ok_resolver, tls_connector=_ok_tls, sleep=_no_sleep, redeploy_previous_sha=_redeploy_but_site_stays_down, now=NOW
    )
    checks.append(("redeploy succeeding but post-rollback health still failing escalates distinctly", rollback_still_broken["status"] == "rollback-verification-failed-escalated"))
    checks.append(("this distinct escalation still records rollback succeeded=True (the redeploy itself worked)", rollback_still_broken["rollback"]["succeeded"] is True))
    checks.append(("this escalation names post-rollback health failure specifically", "post-rollback" in rollback_still_broken["escalation"]))

    # ---- default redeploy hook is the documented Tier-2 integration seam ----
    _reset()
    record6 = record_deployment(project, loop, "badsha6", "goodsha6", HEALTH_CONFIG, deploy_id="deploy-default-hook", deployed_at=DEPLOYED_AT, now=NOW)
    default_hook_result = verify_one_deployment(project, loop, record6, http_get=_all_fail_http_get, resolver=_ok_resolver, tls_connector=_ok_tls, sleep=_no_sleep, now=NOW)
    checks.append(("with no redeploy hook supplied, the default seam escalates rather than silently doing nothing or fabricating success", default_hook_result["status"] == "rollback-failed-escalated"))
    checks.append(("the default seam's error names it as Tier 2 / human-only, not a bug", "Tier 2" in default_hook_result["rollback"]["error"]))

    # ---- secret redaction ----
    _reset()

    def _leaky_redeploy(previous_sha, context):
        raise RuntimeError("ssh auth failed with token sk-liveFAKESECRETABCDEFGHIJ1234567890")

    record7 = record_deployment(project, loop, "badsha7", "goodsha7", HEALTH_CONFIG, deploy_id="deploy-secret-leak", deployed_at=DEPLOYED_AT, now=NOW)
    verify_one_deployment(project, loop, record7, http_get=_all_fail_http_get, resolver=_ok_resolver, tls_connector=_ok_tls, sleep=_no_sleep, redeploy_previous_sha=_leaky_redeploy, now=NOW)
    with open(_deploy_path(project, loop, "deploy-secret-leak"), "r", encoding="utf-8") as f:
        raw_on_disk = f.read()
    checks.append(("a secret-looking string embedded in an error message is never written to disk unredacted", "sk-liveFAKESECRETABCDEFGHIJ1234567890" not in raw_on_disk))
    checks.append(("the redaction marker is present in its place", "[REDACTED:pattern-match]" in raw_on_disk))

    # ---- malformed/missing deployment metadata surfacing at verify time (corrupted record) ----
    corrupt_missing_field = {"id": "corrupt-1", "status": "awaiting-verification", "deployed_at": _now_iso(DEPLOYED_AT), "health_config": HEALTH_CONFIG, "previous_sha": "x"}
    refused_missing_field = False
    try:
        verify_one_deployment(project, loop, corrupt_missing_field, now=NOW)
    except ValueError as e:
        refused_missing_field = "new_sha" in str(e)
    checks.append(("verify_one_deployment refuses a record missing a required field, rather than crashing raw", refused_missing_field))

    corrupt_bad_health_config = {"id": "corrupt-2", "status": "awaiting-verification", "deployed_at": _now_iso(DEPLOYED_AT), "health_config": {"hostname": "example.com"}, "previous_sha": "x", "new_sha": "y"}
    refused_bad_health_config = False
    try:
        verify_one_deployment(project, loop, corrupt_bad_health_config, now=NOW)
    except ValueError as e:
        refused_bad_health_config = "malformed health_config" in str(e)
    checks.append(("verify_one_deployment refuses a record with a malformed health_config", refused_bad_health_config))

    fresh_for_timestamp_test = record_deployment(project, loop, "sha-ts", "sha-ts-prev", HEALTH_CONFIG, deploy_id="deploy-bad-timestamp", deployed_at=DEPLOYED_AT, now=NOW)
    corrupt_bad_timestamp = dict(fresh_for_timestamp_test, deployed_at="not-a-real-timestamp")
    refused_bad_timestamp = False
    try:
        verify_one_deployment(project, loop, corrupt_bad_timestamp, now=NOW)
    except ValueError as e:
        refused_bad_timestamp = "unparseable" in str(e)
    checks.append(("verify_one_deployment refuses a record with an unparseable deployed_at timestamp", refused_bad_timestamp))

    not_an_object_refused = False
    try:
        verify_one_deployment(project, loop, "not-a-record", now=NOW)
    except ValueError as e:
        not_an_object_refused = "not an object" in str(e)
    checks.append(("verify_one_deployment refuses a non-object record outright", not_an_object_refused))

    # ---- list_deploys / run_pending_verifications / render_report ----
    _reset()
    record_deployment(project, loop, "sha-a", "sha-a-prev", HEALTH_CONFIG, deploy_id="deploy-a", deployed_at=DEPLOYED_AT, now=NOW)
    record_deployment(project, loop, "sha-b", "sha-b-prev", HEALTH_CONFIG, deploy_id="deploy-b", deployed_at=NOT_DUE_YET, now=NOW)
    listed = list_deploys(project, loop)
    checks.append(("list_deploys returns every recorded deploy", len(listed) == 2))

    batch = run_pending_verifications(project, loop, http_get=_all_ok_http_get, resolver=_ok_resolver, tls_connector=_ok_tls, sleep=_no_sleep, now=NOW)
    by_id = {r["id"]: r for r in batch}
    checks.append(("run_pending_verifications advances the due record", by_id["deploy-a"]["status"] == "verified"))
    checks.append(("run_pending_verifications leaves the not-yet-due record alone", by_id["deploy-b"]["status"] == "awaiting-verification"))

    report_text = render_report(rolled_back)
    checks.append(("render_report includes both SHAs", "goodsha3" in report_text and "badsha3" in report_text))
    checks.append(("render_report includes the final status", "rolled-back-verified" in report_text))
    checks.append(("render_report explicitly disclaims any SEO-performance conclusion", "no conclusion about SEO performance" in report_text))

    escalation_report = render_report(rollback_failed)
    checks.append(("render_report surfaces an escalation prominently when present", "ESCALATION" in escalation_report))

    _reset()

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
        _cli()

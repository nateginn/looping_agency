# Durable, append-only event history for a loop (Phase 7 - PLAN-PHASE7-CODEX-REVIEW.md).
#
# One JSONL file per loop: <loop_dir>/events.jsonl. Every line is one bounded,
# redacted, machine-readable event. This is the source of truth for the new
# Codex-review subsystem's proposal `status`/`review`/`implementation`
# projection (see sync_proposal_projection) - the event append IS the commit
# point, and the proposal JSON file is a rebuildable projection of it. For the
# pre-existing draft/approved/implemented/applied/verified/breached machine,
# events are best-effort observability layered on top of that already-shipped,
# already-tested state machine (unchanged by this file) - see --reconcile.
#
# Caller must already hold the loop's shared run.lock before calling
# append_event() or reconcile(), exactly like every other tool in tools/.
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

try:
    from .redact import redact_deep
except ImportError:
    from redact import redact_deep

REQUIRED_FIELDS = {"event_id", "seq", "at", "project", "loop", "proposal_id", "event_type"}
_STRING_REQUIRED = ("event_id", "at", "project", "loop", "proposal_id", "event_type")

# Bounded set of optional fields an event may carry. Anything outside this set
# passed to append_event() is dropped, not silently included - keeps every
# event's shape predictable for MMC's reader and for tests.
#
# `target_location`/`topic`/`opportunity_type`/`content_gap_evidence_id` added 2026-08-31
# (GBP Posts plan Phase 3/5): a GBP proposal's target is `{location, topic}`, not `{page,
# keyword}` - without these, a GBP proposal_created event silently lost its own target
# entirely (target_page/keyword are always None for a GBP proposal). The remaining new fields
# (remote_post_id through retracted_by) are reserved for Phase 6's publish/retract events -
# added now, ahead of that code, so a future publish event's field names are fixed before any
# event actually writes them, matching how CONTENT_GAP_EVIDENCE_SCHEMA_VERSION and the other
# GBP schema constants were also fixed ahead of their consuming code in earlier phases. Unused
# until Phase 6 exists to emit them.
ALLOWED_EXTRA_FIELDS = {
    "action_type", "target_page", "keyword", "previous_value", "new_value",
    "implementation_commit", "implementation_branch", "pr_url",
    "review_verdict", "resulting_proposal_status", "observation_deadline",
    "rollback_reference", "source_run_id", "round", "pass_number",
    "proposal_content_hash", "evidence_packet_hash", "spec_content_hash",
    "confidence", "objections", "required_corrections", "final_adjudication",
    "implementation_before", "implementation_after", "reconciled", "note",
    "target_location", "topic", "opportunity_type", "content_gap_evidence_id",
    "remote_post_id", "google_state", "internal_state", "payload_hash",
    "locked_location_id", "locked_account_id", "location_mapping_version",
    "http_status", "retraction_reason", "retracted_by", "metrics_pull_freshness",
}

REVIEW_SUBSYSTEM_EVENT_TYPES = {
    "review_started", "review_round_completed", "proposal_revised",
    "review_approved", "review_rejected", "review_held", "proposal_auto_approved",
}


def _now_iso(now=None):
    now = now or datetime.now(timezone.utc)
    return now.isoformat().replace("+00:00", "Z")


def _events_path(loop_dir):
    return os.path.join(loop_dir, "events.jsonl")


def _quarantine_path(loop_dir):
    return os.path.join(loop_dir, "events.jsonl.quarantine")


def _corrupt_marker_path(loop_dir):
    return os.path.join(loop_dir, "events.jsonl.corrupt_marker")


def _loop_slug(loop_dir):
    return os.path.basename(os.path.normpath(loop_dir)) or "loop"


def _schema_ok(obj):
    if not isinstance(obj, dict):
        return False
    if not REQUIRED_FIELDS.issubset(obj.keys()):
        return False
    if not isinstance(obj.get("seq"), int):
        return False
    for key in _STRING_REQUIRED:
        value = obj.get(key)
        if not isinstance(value, str) or value == "":
            return False
    return True


def _try_parse(line):
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return False, None
    if not _schema_ok(obj):
        return False, None
    return True, obj


def has_corruption_marker(loop_dir):
    return os.path.exists(_corrupt_marker_path(loop_dir))


def _write_corruption_marker(loop_dir, reason, now=None):
    path = _corrupt_marker_path(loop_dir)
    if os.path.exists(path):
        return
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"detected_at": _now_iso(now), "reason": reason}, f, indent=2)


def clear_corruption_marker(loop_dir):
    """Explicit maintenance action after a human/operator has investigated and
    repaired or accepted the underlying corruption - never called automatically."""
    path = _corrupt_marker_path(loop_dir)
    if os.path.exists(path):
        os.remove(path)


def _line_boundaries(text):
    """Yield (byte_start, byte_end_incl_newline, line_text_without_newline) for
    each line in text, using UTF-8 byte offsets (what file.truncate() needs)."""
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        start = offset
        offset += len(raw_line.encode("utf-8"))
        yield start, offset, raw_line.rstrip("\n")


def _scan(events_path):
    """Full-file scan. Returns (max_seq, tail_malformed, any_malformed)."""
    if not os.path.exists(events_path):
        return -1, False, False
    with open(events_path, "r", encoding="utf-8") as f:
        text = f.read()
    max_seq = -1
    any_malformed = False
    tail_malformed = False
    lines = [line for line in text.splitlines() if line != ""]
    for i, raw in enumerate(lines):
        ok, obj = _try_parse(raw)
        if not ok:
            any_malformed = True
            if i == len(lines) - 1:
                tail_malformed = True
            continue
        if isinstance(obj.get("seq"), int):
            max_seq = max(max_seq, obj["seq"])
    return max_seq, tail_malformed, any_malformed


def _quarantine_and_truncate(events_path, loop_dir, now=None):
    """Repair a malformed tail: copy everything from the first unparseable line
    to EOF into events.jsonl.quarantine (flushed+fsynced) BEFORE truncating the
    main log (also flushed+fsynced). If the quarantine write/fsync fails, the
    main log is never touched - the append that triggered this aborts untouched."""
    with open(events_path, "r", encoding="utf-8") as f:
        text = f.read()

    last_good_end = 0
    corrupt_start = None
    for start, end, line in _line_boundaries(text):
        if line == "":
            last_good_end = end
            continue
        ok, _obj = _try_parse(line)
        if ok:
            last_good_end = end
        else:
            corrupt_start = start
            break

    if corrupt_start is None:
        return  # nothing to repair

    quarantined_bytes = text.encode("utf-8")[corrupt_start:]

    quarantine_path = _quarantine_path(loop_dir)
    with open(quarantine_path, "ab") as qf:
        header = f"--- quarantined at {_now_iso(now)} ---\n".encode("utf-8")
        qf.write(header)
        qf.write(quarantined_bytes)
        if not quarantined_bytes.endswith(b"\n"):
            qf.write(b"\n")
        qf.flush()
        os.fsync(qf.fileno())

    with open(events_path, "r+b") as f:
        f.truncate(last_good_end)
        f.flush()
        os.fsync(f.fileno())


def _prepare_for_append(events_path, loop_dir, now=None):
    max_seq, tail_malformed, any_malformed = _scan(events_path)
    if tail_malformed:
        _quarantine_and_truncate(events_path, loop_dir, now=now)
        max_seq, _tail2, any_malformed2 = _scan(events_path)
        any_malformed = any_malformed or any_malformed2
    if any_malformed:
        _write_corruption_marker(loop_dir, "malformed or schema-invalid line detected in events.jsonl", now=now)
    return max_seq + 1


def build_secret_map(project_dir, credential_aliases, resolver=None):
    """Best-effort real alias-based secret_map for redact_deep(), resolving
    every configured credential alias exactly the way run_loop.py's
    connectors already do for snapshot.py. Preflight, non-blocking (Codex
    round 5 finding 8): a single alias failing to resolve is caught and
    simply excluded from the map - never aborts or partially mutates the
    caller's operation. Pattern-based redaction in append_event() remains the
    unconditional floor regardless of what this returns."""
    if resolver is None:
        try:
            from .credentials import resolve_credential as resolver
        except ImportError:
            from credentials import resolve_credential as resolver
    secret_map = {}
    for alias in (credential_aliases or {}).values():
        if not alias:
            continue
        try:
            secret_map[alias] = resolver(alias, project_dir=project_dir)
        except Exception:
            continue
    return secret_map


def append_event(loop_dir, event_type, project, loop, proposal_id, secret_map=None, now=None, **fields):
    """Caller must already hold the loop's run.lock. Returns the written event dict."""
    events_path = _events_path(loop_dir)
    os.makedirs(loop_dir, exist_ok=True)
    seq = _prepare_for_append(events_path, loop_dir, now=now)

    event = {
        "event_id": f"evt-{_loop_slug(loop_dir)}-{seq:012d}",
        "seq": seq,
        "at": _now_iso(now),
        "project": project,
        "loop": loop,
        "proposal_id": proposal_id,
        "event_type": event_type,
    }
    for key in ALLOWED_EXTRA_FIELDS:
        if key in fields and fields[key] is not None:
            event[key] = fields[key]

    secret_map = secret_map or {}
    event = redact_deep(event, secret_map)

    line = json.dumps(event, sort_keys=True) + "\n"
    with open(events_path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
    return event


def read_events(loop_dir, since_event_id=None, limit=None, proposal_id=None):
    """Best-effort read: skips (never repairs) a malformed/schema-invalid line.
    For general observability only - authorization-adjacent code must use
    read_events_fail_closed instead."""
    path = _events_path(loop_dir)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.rstrip("\n")
            if raw == "":
                continue
            ok, obj = _try_parse(raw)
            if not ok:
                continue
            if proposal_id is not None and obj.get("proposal_id") != proposal_id:
                continue
            if since_event_id is not None and obj["event_id"] <= since_event_id:
                continue
            out.append(obj)
    if limit is not None:
        out = out[:limit]
    return out


def read_events_fail_closed(loop_dir, proposal_id):
    """Used by --adjudicate and apply.py's auto-implement authorization path.
    Refuses (raises ValueError) if the global corruption marker is set, or if
    ANY line in the whole log is malformed/schema-invalid - not just lines
    belonging to this proposal - since a corrupt line elsewhere in the file
    might not even parse far enough to expose which proposal_id it belonged
    to, and a silently-incomplete history must never look authoritative for
    an authorization decision."""
    if has_corruption_marker(loop_dir):
        raise ValueError(
            "REFUSED: events.jsonl has an unresolved corruption marker - "
            "authorization reads refuse until it is investigated and cleared"
        )
    path = _events_path(loop_dir)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.rstrip("\n")
            if raw == "":
                continue
            ok, obj = _try_parse(raw)
            if not ok:
                _write_corruption_marker(loop_dir, "malformed line encountered during a fail-closed authorization read")
                raise ValueError(
                    "REFUSED: events.jsonl contains a malformed/schema-invalid line - "
                    "authorization reads refuse until it is investigated and cleared"
                )
            if obj.get("proposal_id") == proposal_id:
                out.append(obj)
    return out


def _rebuild_rounds(events):
    rounds = []
    for ev in events:
        if ev.get("event_type") != "review_round_completed":
            continue
        rounds.append({
            "reviewer": ev.get("action_type") and "codex" or "codex",
            "round": ev.get("round"),
            "pass_number": ev.get("pass_number"),
            "at": ev.get("at"),
            "proposal_content_hash": ev.get("proposal_content_hash"),
            "evidence_packet_hash": ev.get("evidence_packet_hash"),
            "spec_content_hash": ev.get("spec_content_hash"),
            "verdict": ev.get("review_verdict"),
            "confidence": ev.get("confidence"),
            "objections": ev.get("objections") or [],
            "required_corrections": ev.get("required_corrections") or [],
        })
    pass_numbers = [r["pass_number"] for r in rounds if r.get("pass_number") is not None]
    max_pass = max(pass_numbers) if pass_numbers else None
    for r in rounds:
        r["superseded"] = max_pass is not None and r.get("pass_number") != max_pass
    return rounds


def rebuild_review_rounds_from_events(loop_dir, proposal_id, fail_closed=True):
    events = read_events_fail_closed(loop_dir, proposal_id) if fail_closed else read_events(loop_dir, proposal_id=proposal_id)
    return _rebuild_rounds(events)


def sync_proposal_projection(loop_dir, proposal_id, fail_closed=True):
    """Replay the complete (not bounded/tail) history for proposal_id and
    reconstruct {"status", "implementation", "review": {"rounds", "final_adjudication"}}
    from it. Used by every tool that touches a proposal in the review
    subsystem, before doing anything else, so a crash between an event append
    and a proposal-file write is caught up rather than silently ignored."""
    events = read_events_fail_closed(loop_dir, proposal_id) if fail_closed else read_events(loop_dir, proposal_id=proposal_id)
    status = None
    implementation = None
    final_adjudication = None
    for ev in events:
        if ev.get("resulting_proposal_status"):
            status = ev["resulting_proposal_status"]
        if ev.get("event_type") == "proposal_revised" and "implementation_after" in ev:
            implementation = ev["implementation_after"]
        if ev.get("final_adjudication"):
            final_adjudication = ev["final_adjudication"]
    return {
        "status": status,
        "implementation": implementation,
        "review": {"rounds": _rebuild_rounds(events), "final_adjudication": final_adjudication},
    }


_LEGACY_MILESTONE_CHECKS = (
    ("created_run_id", "proposal_created"),
    ("implement_error", "implementation_failed"),
    ("implemented_commit_sha", "implemented_branch", "implementation_created"),
    ("applied_at", "proposal_applied"),
)


def reconcile(loop_dir, project, loop, pending_dir, now=None):
    """For the pre-existing (non-event-sourced) state machine only: backfill a
    `reconciled: true` event for any durable, never-cleared-except-implement_error
    on-disk milestone field that has no matching event yet. Checks each
    milestone independently via the field that records it, not just the
    proposal's current `status` - see PLAN-PHASE7-CODEX-REVIEW.md item 7 for
    why current-status-only reconciliation is insufficient. Caller must hold
    the loop's run.lock. Returns the list of backfilled events.

    Reads pending_dir directly rather than importing lib.proposals.list_proposals,
    since tools/lib/*.py modules are not guaranteed importable from each other
    when a lib module is itself invoked directly as a script (sys.path[0] is
    then the lib/ directory, not tools/ - proposals.py's own dual-fallback
    import assumes the latter)."""
    proposals = []
    if os.path.isdir(pending_dir):
        for name in sorted(os.listdir(pending_dir)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(pending_dir, name), "r", encoding="utf-8") as f:
                proposals.append(json.load(f))

    backfilled = []
    for proposal in proposals:
        pid = proposal.get("id")
        if not pid:
            continue
        existing_types = {e.get("event_type") for e in read_events(loop_dir, proposal_id=pid)}

        if proposal.get("created_run_id") and "proposal_created" not in existing_types:
            backfilled.append(_backfill(loop_dir, project, loop, pid, "proposal_created", proposal, now))
        if proposal.get("implement_error") and "implementation_failed" not in existing_types:
            backfilled.append(_backfill(loop_dir, project, loop, pid, "implementation_failed", proposal, now))
        if proposal.get("implemented_commit_sha") and "implementation_created" not in existing_types:
            backfilled.append(_backfill(loop_dir, project, loop, pid, "implementation_created", proposal, now))
        if proposal.get("applied_at") and "proposal_applied" not in existing_types:
            backfilled.append(_backfill(loop_dir, project, loop, pid, "proposal_applied", proposal, now))
        if proposal.get("evaluated_run_id"):
            outcome = proposal.get("evaluation_outcome")
            expected = "guardrail_breached" if outcome == "breached" else "proposal_verified"
            if expected not in existing_types:
                backfilled.append(_backfill(loop_dir, project, loop, pid, expected, proposal, now))
    return backfilled


def _backfill(loop_dir, project, loop, proposal_id, event_type, proposal, now):
    return append_event(
        loop_dir, event_type, project=project, loop=loop, proposal_id=proposal_id,
        action_type=proposal.get("action_type"),
        resulting_proposal_status=proposal.get("status"),
        source_run_id=proposal.get("created_run_id"),
        reconciled=True, now=now,
    )


def _cli():
    args = sys.argv[1:]
    if "--reconcile" in args:
        positional = [a for a in args if a != "--reconcile"]
        if len(positional) < 2:
            print("usage: python tools/lib/event_log.py --reconcile <project> <loop>", file=sys.stderr)
            sys.exit(2)
        project, loop = positional[0], positional[1]
        try:
            from .lock import acquire_lock, release_lock
            from .paths import assert_within
        except ImportError:
            from lock import acquire_lock, release_lock
            from paths import assert_within
        workspace_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        projects_root = os.path.join(workspace_root, "projects")
        project_dir = os.path.join(projects_root, project)
        assert_within(projects_root, project_dir, "project directory")
        loop_dir = os.path.join(project_dir, "loops", loop)
        assert_within(project_dir, loop_dir, "loop directory")
        pending_dir = os.path.join(loop_dir, "pending")
        lock = acquire_lock(loop_dir, runs_dir=os.path.join(loop_dir, "runs"))
        if not lock["acquired"]:
            print(f'REFUSED: run lock active - {lock["reason"]}', file=sys.stderr)
            sys.exit(1)
        try:
            backfilled = reconcile(loop_dir, project, loop, pending_dir)
            for ev in backfilled:
                print(f'backfilled {ev["event_type"]} for {ev["proposal_id"]}')
            print(f"{len(backfilled)} event(s) backfilled")
        finally:
            release_lock(loop_dir, lock["run_id"])
        return
    print("usage: python tools/lib/event_log.py --reconcile <project> <loop>", file=sys.stderr)
    sys.exit(2)


def _self_test():
    import shutil

    tmp = tempfile.mkdtemp(prefix="event-log-test-")
    checks = []
    try:
        loop_dir = os.path.join(tmp, "loops", "seo")
        os.makedirs(loop_dir, exist_ok=True)

        e1 = append_event(loop_dir, "proposal_created", project="p", loop="seo", proposal_id="prop-1", action_type="title-tag-rewrite")
        checks.append(("first event gets seq 0", e1["seq"] == 0))
        checks.append(("event_id is zero-padded and loop-scoped", e1["event_id"] == "evt-seo-000000000000"))

        # GBP-shaped fields (2026-08-31, Phase 3/5) - a GBP proposal's target is
        # {location, topic}, not {page, keyword}, and carries no page/keyword at all. Uses its
        # own loop_dir so it doesn't disturb the seq-numbered assertions below.
        gbp_loop_dir = os.path.join(tmp, "loops", "gbp")
        os.makedirs(gbp_loop_dir, exist_ok=True)
        e1b = append_event(
            gbp_loop_dir, "proposal_created", project="p", loop="gbp", proposal_id="gbp-prop-1",
            action_type="gbp-post-draft", target_location="Greeley", topic="some-gap-id",
            opportunity_type="content_freshness", content_gap_evidence_id="some-gap-id",
        )
        checks.append(("GBP-shaped fields (target_location/topic/opportunity_type/content_gap_evidence_id) round-trip", e1b.get("target_location") == "Greeley" and e1b.get("topic") == "some-gap-id" and e1b.get("opportunity_type") == "content_freshness" and e1b.get("content_gap_evidence_id") == "some-gap-id"))

        e2 = append_event(loop_dir, "review_started", project="p", loop="seo", proposal_id="prop-1", resulting_proposal_status="review-pending")
        checks.append(("second event increments seq", e2["seq"] == 1))

        events = read_events(loop_dir)
        checks.append(("read_events returns both events in order", [e["event_type"] for e in events] == ["proposal_created", "review_started"]))

        filtered = read_events(loop_dir, proposal_id="prop-1")
        checks.append(("proposal_id filter works", len(filtered) == 2))

        since = read_events(loop_dir, since_event_id=e1["event_id"])
        checks.append(("since_event_id excludes the boundary event", len(since) == 1 and since[0]["event_type"] == "review_started"))

        # Redaction: a secret_map should scrub a raw value out of a free-text field.
        e3 = append_event(
            loop_dir, "proposal_revised", project="p", loop="seo", proposal_id="prop-1",
            note="rationale mentions sk-super-secret-raw-value-1234567890",
            secret_map={"demo-alias": "sk-super-secret-raw-value-1234567890"},
        )
        checks.append(("alias-based redaction scrubs a known secret from an event field", "sk-super-secret-raw-value-1234567890" not in json.dumps(e3)))
        checks.append(("redaction marker present", "[REDACTED:demo-alias]" in json.dumps(e3)))

        # Pattern-based redaction fires even with no secret_map.
        e4 = append_event(loop_dir, "proposal_revised", project="p", loop="seo", proposal_id="prop-1", note="oauth-ish sk-abcdefghijklmnop1234567890")
        checks.append(("pattern-based redaction fires with an empty secret_map", "sk-abcdefghijklmnop1234567890" not in json.dumps(e4)))

        # Projection replay: status/implementation reconstructed from events, including a revision.
        append_event(loop_dir, "review_round_completed", project="p", loop="seo", proposal_id="prop-2",
                     round=1, pass_number=1, review_verdict="hold", proposal_content_hash="h1", evidence_packet_hash="ph1", spec_content_hash="sh1",
                     resulting_proposal_status="review-revision-needed")
        append_event(loop_dir, "proposal_revised", project="p", loop="seo", proposal_id="prop-2",
                     implementation_before={"new_value": "Old"}, implementation_after={"new_value": "New"},
                     resulting_proposal_status="review-pending")
        append_event(loop_dir, "review_round_completed", project="p", loop="seo", proposal_id="prop-2",
                     round=3, pass_number=2, review_verdict="approve", proposal_content_hash="h2", evidence_packet_hash="ph2", spec_content_hash="sh1",
                     resulting_proposal_status="review-pending")
        append_event(loop_dir, "review_round_completed", project="p", loop="seo", proposal_id="prop-2",
                     round=4, pass_number=2, review_verdict="approve", proposal_content_hash="h2", evidence_packet_hash="ph3", spec_content_hash="sh1",
                     resulting_proposal_status="review-approved")
        append_event(loop_dir, "review_approved", project="p", loop="seo", proposal_id="prop-2",
                     final_adjudication="approve", resulting_proposal_status="review-approved")

        projection = sync_proposal_projection(loop_dir, "prop-2")
        checks.append(("projection reconstructs latest status", projection["status"] == "review-approved"))
        checks.append(("projection reconstructs latest implementation from proposal_revised", projection["implementation"] == {"new_value": "New"}))
        checks.append(("projection reconstructs final_adjudication", projection["review"]["final_adjudication"] == "approve"))
        rounds = projection["review"]["rounds"]
        checks.append(("projection has all 3 rounds", len(rounds) == 3))
        superseded_map = {r["round"]: r["superseded"] for r in rounds}
        checks.append(("pass-1 round is superseded after a pass-2 exists", superseded_map[1] is True))
        checks.append(("pass-2 rounds are not superseded", superseded_map[3] is False and superseded_map[4] is False))

        # Tail corruption: truncated last line is quarantined, not silently dropped, and doesn't collide seq.
        events_path = _events_path(loop_dir)
        with open(events_path, "a", encoding="utf-8", newline="\n") as f:
            f.write('{"event_id": "evt-seo-000000099", "seq": 99, "at": "2026-01-01T00:00:00Z", "project": "p", "loop": "seo", "proposal_id": "prop-3", "event_type": "trunc')  # no closing, no newline
        before_seq = _scan(events_path)[0]
        e5 = append_event(loop_dir, "proposal_created", project="p", loop="seo", proposal_id="prop-3")
        checks.append(("append after tail corruption succeeds", e5["seq"] == before_seq + 1))
        checks.append(("quarantine file created", os.path.exists(_quarantine_path(loop_dir))))
        with open(_quarantine_path(loop_dir), "r", encoding="utf-8") as f:
            quarantine_content = f.read()
        checks.append(("quarantined bytes preserved, not deleted", "trunc" in quarantine_content))
        checks.append(("corruption marker set after a repair", has_corruption_marker(loop_dir)))

        threw_fail_closed = False
        try:
            read_events_fail_closed(loop_dir, "prop-1")
        except ValueError as e:
            threw_fail_closed = "corruption marker" in str(e)
        checks.append(("fail-closed read refuses while corruption marker is set", threw_fail_closed))

        clear_corruption_marker(loop_dir)
        checks.append(("clear_corruption_marker removes the marker", not has_corruption_marker(loop_dir)))
        read_events_fail_closed(loop_dir, "prop-1")  # should not raise now
        checks.append(("fail-closed read succeeds once the marker is cleared", True))

        # build_secret_map: one unresolvable alias among several never blocks.
        def _flaky_resolver(alias, project_dir=None):
            if alias == "bad-alias":
                raise RuntimeError("credential not found")
            return f"raw-value-for-{alias}"
        secret_map = build_secret_map("/fake/project", {"gsc": "good-alias", "dataforseo": "bad-alias"}, resolver=_flaky_resolver)
        checks.append(("build_secret_map resolves a working alias", secret_map.get("good-alias") == "raw-value-for-good-alias"))
        checks.append(("build_secret_map silently excludes an alias that fails to resolve, not raising", "bad-alias" not in secret_map))

        # Reconcile: proposal.json shows implemented_commit_sha with no matching event -> backfilled.
        # (Written directly, not via lib.proposals.write_proposal, to avoid this
        # self-test's import path depending on whether it's run as a package
        # member or a bare script - see the module-level import fallback note.)
        pending_dir = os.path.join(tmp, "pending")
        os.makedirs(pending_dir, exist_ok=True)
        with open(os.path.join(pending_dir, "prop-4.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump({"id": "prop-4", "status": "implemented", "implemented_commit_sha": "deadbeef", "action_type": "title-tag-rewrite"}, f)
        backfilled = reconcile(loop_dir, "p", "seo", pending_dir)
        checks.append(("reconcile backfills a missing implementation_created event", any(e["event_type"] == "implementation_created" and e["proposal_id"] == "prop-4" for e in backfilled)))
        backfilled_again = reconcile(loop_dir, "p", "seo", pending_dir)
        checks.append(("reconcile does not duplicate an already-backfilled event", not any(e["proposal_id"] == "prop-4" for e in backfilled_again)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    print(f"{len(checks) - failed}/{len(checks)} checks passed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    if "--verify" in sys.argv:
        _self_test()
    else:
        _cli()

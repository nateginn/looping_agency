# GBP Posts plan Phase 5 - resolves and locks the concrete publish target (accountId/
# locationId/place_id) at approval time and commits a payload hash tying that lock to this
# exact drafted content.
#
# Codex review, 2026-09-01: the first version of this file called review_pending.decide() as
# a black box (its own lock acquire/release) and only THEN acquired a second, separate lock to
# append the gbp_proposal_approved event. That created a real race window - a concurrent draft
# could change `implementation` between the two lock cycles, a concurrent reject could land in
# the gap, and a crash between the two cycles could leave a proposal "approved" with no
# corresponding lock event, the exact state this whole mechanism exists to prevent. This
# version performs the ENTIRE approval - reading the proposal fresh, validating its CURRENT
# status via the event-sourced projection (never the cached JSON review_pending.decide() would
# otherwise trust), resolving the locked target, computing the payload hash, and writing both
# the status transition and both events - inside a SINGLE lock acquisition. No other tool in
# this workspace may mutate a GBP proposal's `implementation` or `status` without holding this
# same loop lock (draft_gbp_post.py, review_pending.py, run_loop.py all do), so holding it for
# the whole operation genuinely serializes against every one of those.
#
# The event log is the sole commit point (plan's own hard requirement): a human_decision_
# recorded event (matching what review_pending._emit_human_decision would have written, kept
# for MMC/observability compatibility) and a gbp_proposal_approved event (carrying the hash and
# all three locked IDs, including place_id) are both appended before the proposal JSON is
# rewritten at all - the JSON is a disposable, rebuildable cache exactly like everywhere else
# in this event-sourced subsystem. publish_gbp_post.py (Phase 6, unbuilt - needs live Google
# API access this workspace does not have) must replay events.jsonl via
# gbp_publish_state.sync_gbp_approval_lock() before ever trusting these fields, never read them
# from the proposal JSON's cache alone.
import os
import sys
from datetime import datetime, timezone

try:
    from .lib.event_log import append_event, build_secret_map, sync_proposal_projection
    from .lib.gbp_publish_state import compute_approved_payload_hash, load_location_mapping, resolve_locked_target
    from .lib.lock import acquire_lock, release_lock
    from .lib.proposals import atomic_write_json, load_json, loop_dir_for, pending_dir_for, proposal_path
    from .review_pending import TRANSITIONS
    from .run_loop import _read_lock_ttl_minutes_unsafe
    from .spec_validate import extract_frontmatter
except ImportError:
    from lib.event_log import append_event, build_secret_map, sync_proposal_projection
    from lib.gbp_publish_state import compute_approved_payload_hash, load_location_mapping, resolve_locked_target
    from lib.lock import acquire_lock, release_lock
    from lib.proposals import atomic_write_json, load_json, loop_dir_for, pending_dir_for, proposal_path
    from review_pending import TRANSITIONS
    from run_loop import _read_lock_ttl_minutes_unsafe
    from spec_validate import extract_frontmatter

import yaml

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(THIS_DIR)
PROJECTS_ROOT = os.path.join(WORKSPACE_ROOT, "projects")

APPROVABLE_ACTION_TYPES = {"gbp-post-draft"}
# The full set review_pending.TRANSITIONS["approve"] itself allows approving FROM - not
# narrowed to {"draft", "reviewed"} the way an earlier version of this file did. A GBP
# proposal never actually enters the Codex-review-subsystem states today (nothing wires that
# subsystem to this loop), but narrowing this set anyway created a genuine dead end a Codex
# review caught (2026-09-01, second round): review_pending.py already refuses to approve a
# gbp-post-draft proposal unconditionally (see GBP_LOCKED_APPROVAL_ACTION_TYPES there), so if
# one ever DID reach review-approved by some future path, excluding it here too would leave it
# permanently unapprovable by any tool. Keeping the full set costs nothing today and removes
# that trap.
APPROVE_FROM_STATUSES = set(TRANSITIONS["approve"]["from"])


def _now_iso(now=None):
    now = now or datetime.now(timezone.utc)
    return now.isoformat().replace("+00:00", "Z")


def _load_repo_path(project, projects_root):
    project_path = os.path.join(projects_root, project, "project.md")
    with open(project_path, "r", encoding="utf-8") as f:
        source = f.read()
    return (yaml.safe_load(extract_frontmatter(source)) or {}).get("repo")


def approve_gbp_post(project, loop, proposal_id, by="human", note="", now=None):
    loop_dir = loop_dir_for(project, loop)
    pending_dir = pending_dir_for(project, loop)
    proposal_pathname = proposal_path(pending_dir, proposal_id)
    if not os.path.exists(proposal_pathname):
        raise ValueError(f"approve_gbp_post.py: proposal {proposal_id} not found")

    lock_ttl_minutes = _read_lock_ttl_minutes_unsafe(os.path.join(loop_dir, "spec.md"))
    lock = acquire_lock(loop_dir, max_run_duration_minutes=lock_ttl_minutes, runs_dir=os.path.join(loop_dir, "runs"), now=now)
    if not lock["acquired"]:
        raise ValueError(f'approve_gbp_post.py: REFUSED - run lock active for {project}/{loop} - {lock["reason"]}')
    try:
        # Read fresh, inside the lock - never a value read before acquisition, which could
        # already be stale by the time this holder gets the lock.
        proposal = load_json(proposal_pathname)
        if proposal.get("action_type") not in APPROVABLE_ACTION_TYPES:
            raise ValueError(f'approve_gbp_post.py: proposal {proposal_id} has action_type "{proposal.get("action_type")}", not a GBP post proposal - use review_pending.py for other action types')

        # Event-sourced current status AND implementation - the authoritative facts for this
        # event-sourced subsystem, not the cached JSON's own fields. A proposal whose creation
        # event never made it into events.jsonl (the append is best-effort) has no
        # event-sourced status at all and is refused here, fail-closed; the fix is
        # `event_log.py --reconcile`, never assuming the cache is right.
        #
        # implementation specifically: draft_gbp_post.py's REVISION path appends a
        # proposal_revised event (carrying implementation_after) and ONLY THEN rewrites the
        # proposal cache - two separate steps. A crash between them leaves the event log
        # correctly showing the revised content while the cache still holds the pre-revision
        # copy. Now that "review-pending"/"review-revision-needed" are back in
        # APPROVE_FROM_STATUSES (below), such a proposal IS approvable, so hashing the stale
        # cached implementation instead of the event-sourced one would lock the WRONG content
        # (Codex review, 2026-09-01, fourth round). sync_proposal_projection's own
        # "implementation" field is None until at least one proposal_revised event exists (the
        # very first draft is a single atomic cache write with no preceding event, so there is
        # no crash window to protect against there) - fall back to the cache only in that case.
        projection = sync_proposal_projection(loop_dir, proposal_id, fail_closed=True)
        # Explicit is-not-None check, not a truthy `or` - a degenerate-but-technically-valid
        # event-sourced implementation of {} (falsy) must still win over the cache; `or` would
        # silently fall back to stale cache content for that value (Codex review, 2026-09-01,
        # sixth round).
        projected_implementation = projection.get("implementation")
        implementation = projected_implementation if projected_implementation is not None else proposal.get("implementation")
        if not implementation or not implementation.get("summary"):
            raise ValueError(f"approve_gbp_post.py: proposal {proposal_id} has not been drafted yet (no implementation.summary) - run draft_gbp_post.py first")
        target = proposal.get("target") or {}
        location_name = target.get("location")
        if not location_name:
            raise ValueError(f"approve_gbp_post.py: proposal {proposal_id} has no target.location")

        current_status = projection["status"]
        if current_status not in APPROVE_FROM_STATUSES:
            raise ValueError(
                f"approve_gbp_post.py: cannot approve proposal {proposal_id}: event-sourced current status is "
                f"{current_status!r}, expected one of {sorted(APPROVE_FROM_STATUSES)} "
                f"(if this proposal was legitimately created but has no event history yet, run "
                f"`python tools/lib/event_log.py --reconcile {project} {loop}` first)"
            )

        # Resolved fresh, inside this SAME lock hold, immediately before the hash is computed
        # from the SAME in-lock read of `implementation` above - closes the race where a
        # concurrent draft_gbp_post.py revision (which requires this same lock) could otherwise
        # land between resolution and commit.
        locations_json_path = os.path.join(loop_dir, "locations.json")
        mapping_version, locations_by_name = load_location_mapping(locations_json_path)
        locked_target = resolve_locked_target(locations_by_name, location_name)
        approved_payload_hash = compute_approved_payload_hash(implementation, target, locked_target, mapping_version)

        repo_path = _load_repo_path(project, PROJECTS_ROOT)
        spec = yaml.safe_load(extract_frontmatter(open(os.path.join(loop_dir, "spec.md"), "r", encoding="utf-8").read())) or {}
        secret_map = build_secret_map(repo_path, spec.get("credential_aliases"))

        # gbp_proposal_approved is the ONE event that must durably land for this operation to
        # be considered committed - it already carries resulting_proposal_status="approved",
        # so a single successful append makes "approved" and "locked" true together, in one
        # atomic write; there is no window where the projection says "approved" but no lock
        # exists (Codex review, 2026-09-01, second round: the original two-required-events
        # design left exactly that split-state possible if the second append ever failed).
        # If THIS append raises, the whole function raises too, and the proposal JSON below is
        # never touched - nothing changed.
        append_event(
            loop_dir, "gbp_proposal_approved", project=project, loop=loop, proposal_id=proposal_id,
            action_type=proposal.get("action_type"), target_location=location_name, topic=target.get("topic"),
            payload_hash=approved_payload_hash, locked_location_id=locked_target["location_id"],
            locked_account_id=locked_target["account_id"], locked_place_id=locked_target["place_id"],
            location_mapping_version=mapping_version, resulting_proposal_status="approved",
            secret_map=secret_map, now=now,
        )
        try:
            # Best-effort only, exactly like review_pending._emit_human_decision elsewhere in
            # this workspace: MMC/observability compatibility, never authorization. The
            # approval above already stands regardless of whether this succeeds.
            append_event(
                loop_dir, "human_decision_recorded", project=project, loop=loop, proposal_id=proposal_id,
                action_type=proposal.get("action_type"), target_location=location_name, topic=target.get("topic"),
                review_verdict="approve", resulting_proposal_status="approved",
                note=note[:500] if note else None, secret_map=secret_map, now=now,
            )
        except Exception:
            pass

        proposal["status"] = "approved"
        proposal["decision"] = {"action": "approve", "by": by, "note": note, "at": _now_iso(now)}
        # The event-sourced implementation resolved above (which may be the NEW, post-revision
        # content when a crash left the cache stale - see the comment above) must also become
        # the cache's own value - otherwise a successful approval would still leave the OLD
        # implementation visible in pending/*.json even though the correct one was hashed and
        # locked (Codex review, 2026-09-01, fifth round: this write was missing entirely).
        proposal["implementation"] = implementation
        proposal["approved_payload_hash"] = approved_payload_hash
        proposal["locked_location_id"] = locked_target["location_id"]
        proposal["locked_account_id"] = locked_target["account_id"]
        proposal["locked_place_id"] = locked_target["place_id"]
        proposal["location_mapping_version"] = mapping_version
        atomic_write_json(proposal_pathname, proposal)
        return proposal
    finally:
        release_lock(loop_dir, lock["run_id"])


def _cli():
    args = sys.argv[1:]
    if len(args) < 3:
        print("usage: python tools/approve_gbp_post.py <project> <loop> <proposal-id> [--by <name>] [--reason <text>]", file=sys.stderr)
        sys.exit(2)
    project, loop, proposal_id = args[0], args[1], args[2]
    by = "human"
    if "--by" in args:
        idx = args.index("--by")
        if idx + 1 < len(args):
            by = args[idx + 1]
    note = ""
    if "--reason" in args:
        idx = args.index("--reason")
        if idx + 1 < len(args):
            note = args[idx + 1]
    try:
        proposal = approve_gbp_post(project, loop, proposal_id, by=by, note=note)
        print(f'approved and locked {proposal["id"]} -> location_id {proposal["locked_location_id"]} / place_id {proposal["locked_place_id"]} (mapping v{proposal["location_mapping_version"]})')
    except Exception as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)


def _self_test():
    import json
    import shutil

    import codex_review_proposal
    from lib.event_log import append_event as _append_event_raw
    from lib.event_log import read_events
    from lib.gbp_publish_state import sync_gbp_approval_lock
    from lib.proposals import write_proposal
    from review_pending import decide as _review_decide

    project = "_approve-gbp-post-selftest-tmp"
    loop = "gbp"
    project_dir = os.path.join(PROJECTS_ROOT, project)
    loop_dir = os.path.join(project_dir, "loops", loop)
    pending_dir = os.path.join(loop_dir, "pending")
    shutil.rmtree(project_dir, ignore_errors=True)
    os.makedirs(pending_dir, exist_ok=True)

    with open(os.path.join(project_dir, "project.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("---\nslug: _approve-gbp-post-selftest-tmp\ndomain: example.invalid\nrepo: null\n---\n")
    with open(os.path.join(loop_dir, "spec.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("---\nversion: 1\nloop: gbp\n---\n")

    def _write_locations(entries, version=5):
        with open(os.path.join(loop_dir, "locations.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump({"version": version, "locations": entries}, f)

    _write_locations([
        {"name": "Greeley", "verified": True, "account_id": "acc-greeley", "location_id": "loc-greeley", "place_id": "place-greeley"},
        {"name": "Denver", "verified": False, "account_id": None, "location_id": None, "place_id": None},
        {"name": "Missing-Place-Id", "verified": True, "account_id": "acc-x", "location_id": "loc-x", "place_id": None},
    ])

    def _seed(proposal_id, location="Greeley", status="draft", drafted=True, emit_created_event=True):
        implementation = {"post_type": "STANDARD", "summary": "Test summary", "call_to_action": {"action_type": "LEARN_MORE", "url": "https://example.invalid/"}, "constraint_manifest_hash": "abc123"} if drafted else None
        write_proposal(pending_dir, {
            "id": proposal_id, "action_type": "gbp-post-draft", "status": status,
            "target": {"location": location, "topic": "some-topic"}, "implementation": implementation,
        })
        # sync_proposal_projection is now the authority approve_gbp_post reads status from -
        # a seeded proposal needs the SAME event history a real _pick_gbp_actions() run would
        # have produced (proposal_created carrying resulting_proposal_status), not just a JSON
        # file with the matching status field, or every approval attempt below would refuse
        # with "event-sourced current status is None" regardless of what the JSON says.
        if emit_created_event:
            _append_event_raw(loop_dir, "proposal_created", project=project, loop=loop, proposal_id=proposal_id, action_type="gbp-post-draft", resulting_proposal_status=status)

    checks = []
    try:
        _seed("prop-good")
        result = approve_gbp_post(project, loop, "prop-good", by="test-human", note="looks good")
        checks.append(("a valid approval transitions status to approved", result["status"] == "approved"))
        checks.append(("the locked location_id is recorded on the proposal", result["locked_location_id"] == "loc-greeley"))
        checks.append(("the locked place_id is recorded on the proposal", result["locked_place_id"] == "place-greeley"))
        checks.append(("the location_mapping_version is recorded", result["location_mapping_version"] == 5))
        lock = sync_gbp_approval_lock(loop_dir, "prop-good")
        checks.append(("the approval-lock event is readable via event-sourced replay", lock is not None and lock["locked_location_id"] == "loc-greeley" and lock["payload_hash"] == result["approved_payload_hash"]))
        checks.append(("the approval-lock event carries locked_place_id too", lock["locked_place_id"] == "place-greeley"))
        events_for_good = read_events(loop_dir, proposal_id="prop-good")
        checks.append(("a human_decision_recorded event was also emitted (MMC/observability compatibility)", any(e["event_type"] == "human_decision_recorded" and e.get("resulting_proposal_status") == "approved" for e in events_for_good)))

        threw_unverified = False
        _seed("prop-unverified-location", location="Denver")
        try:
            approve_gbp_post(project, loop, "prop-unverified-location")
        except ValueError as e:
            threw_unverified = "not yet verified" in str(e)
        checks.append(("an unverified location refuses the WHOLE approval, not just the lock", threw_unverified))
        unverified_after = json.load(open(proposal_path(pending_dir, "prop-unverified-location"), encoding="utf-8"))
        checks.append(("...and the proposal's status never changed (never left half-approved)", unverified_after["status"] == "draft"))
        checks.append(("...and no gbp_proposal_approved event was written either (all-or-nothing)", not any(e["event_type"] == "gbp_proposal_approved" for e in read_events(loop_dir, proposal_id="prop-unverified-location"))))

        threw_unlisted = False
        _seed("prop-unlisted-location", location="Nowhere")
        try:
            approve_gbp_post(project, loop, "prop-unlisted-location")
        except ValueError as e:
            threw_unlisted = "not in locations.json at all" in str(e)
        checks.append(("an unlisted location is refused", threw_unlisted))

        threw_missing_place_id = False
        _seed("prop-missing-place-id", location="Missing-Place-Id")
        try:
            approve_gbp_post(project, loop, "prop-missing-place-id")
        except ValueError as e:
            threw_missing_place_id = "missing account_id/location_id/place_id" in str(e)
        checks.append(("a verified location missing place_id is refused", threw_missing_place_id))

        threw_forbidden = False
        _write_locations([
            {"name": "Greeley", "verified": True, "account_id": "acc-greeley", "location_id": "loc-greeley", "place_id": "place-greeley"},
            {"name": "UNC Campus", "verified": True, "account_id": "acc-unc", "location_id": "loc-unc", "place_id": "place-unc"},
        ], version=6)
        _seed("prop-forbidden-location", location="UNC Campus")
        try:
            approve_gbp_post(project, loop, "prop-forbidden-location")
        except ValueError as e:
            threw_forbidden = "structurally-forbidden" in str(e) or "structurally forbidden" in str(e)
        checks.append(("a locations.json entry for UNC Campus/Thornton is refused even if present and verified", threw_forbidden))
        # Restore a normal mapping for the remaining checks.
        _write_locations([
            {"name": "Greeley", "verified": True, "account_id": "acc-greeley", "location_id": "loc-greeley", "place_id": "place-greeley"},
            {"name": "Denver", "verified": False, "account_id": None, "location_id": None, "place_id": None},
        ], version=5)

        # review_pending.decide() must not offer a second, unlocked "approve" path for a
        # gbp-post-draft proposal - only this tool's atomic path may approve one.
        threw_alternate_path = False
        _seed("prop-alternate-path")
        try:
            _review_decide(project, loop, "prop-alternate-path", "approve", by="test-human")
        except ValueError as e:
            threw_alternate_path = "use tools/approve_gbp_post.py instead" in str(e)
        checks.append(("review_pending.decide() refuses to approve a gbp-post-draft proposal directly", threw_alternate_path))
        alternate_path_after = json.load(open(proposal_path(pending_dir, "prop-alternate-path"), encoding="utf-8"))
        checks.append(("...and its status never changed (no unlocked approval slipped through)", alternate_path_after["status"] == "draft"))

        # "retry" also lands on "approved" (from "implement-failed") - the guard must catch
        # this transition too, not just "approve" (Codex review, 2026-09-01, third round).
        threw_alternate_retry = False
        _seed("prop-alternate-retry")
        try:
            _review_decide(project, loop, "prop-alternate-retry", "retry", by="test-human")
        except ValueError as e:
            threw_alternate_retry = "use tools/approve_gbp_post.py instead" in str(e)
        checks.append(("review_pending.decide() also refuses to 'retry'-approve a gbp-post-draft proposal", threw_alternate_retry))

        # A GBP proposal that somehow reached a Codex-review-subsystem state (never wired up
        # for this loop today, but not structurally impossible) must still be approvable here -
        # excluding these states created a dead end review_pending.py's own refusal above would
        # otherwise leave unrescuable (Codex review, 2026-09-01, second round).
        _seed("prop-review-approved-state", status="review-approved")
        result_from_review_state = approve_gbp_post(project, loop, "prop-review-approved-state")
        checks.append(("a proposal in a Codex-review-subsystem state (review-approved) can still be approved and locked here", result_from_review_state["status"] == "approved" and result_from_review_state["locked_place_id"] == "place-greeley"))

        # The crash window this closes: draft_gbp_post.py's revision path appends a
        # proposal_revised event (implementation_after) and ONLY THEN rewrites the proposal
        # cache. Simulate a crash right between those two steps - event log has the NEW
        # content, cache still has the OLD - and confirm approval locks the event-sourced
        # (new) content, not the stale cached (old) one (Codex review, 2026-09-01, fourth
        # round: widening APPROVE_FROM_STATUSES to include "review-pending" made this
        # reachable, since a stuck-mid-revision proposal is now approvable at all).
        old_impl = {"post_type": "STANDARD", "summary": "OLD stale summary still in the cache.", "call_to_action": {"action_type": "LEARN_MORE", "url": "https://example.invalid/"}, "constraint_manifest_hash": "old-hash"}
        new_impl = {"post_type": "STANDARD", "summary": "NEW summary only the event log knows about.", "call_to_action": {"action_type": "LEARN_MORE", "url": "https://example.invalid/"}, "constraint_manifest_hash": "new-hash"}
        write_proposal(pending_dir, {
            "id": "prop-cache-drift", "action_type": "gbp-post-draft", "status": "review-revision-needed",
            "target": {"location": "Greeley", "topic": "some-topic"}, "implementation": old_impl,
        })
        _append_event_raw(loop_dir, "proposal_created", project=project, loop=loop, proposal_id="prop-cache-drift", action_type="gbp-post-draft", resulting_proposal_status="review-revision-needed")
        _append_event_raw(loop_dir, "proposal_revised", project=project, loop=loop, proposal_id="prop-cache-drift", action_type="gbp-post-draft", implementation_before=old_impl, implementation_after=new_impl, resulting_proposal_status="review-pending")
        drift_result = approve_gbp_post(project, loop, "prop-cache-drift")
        expected_drift_hash = compute_approved_payload_hash(new_impl, {"location": "Greeley", "topic": "some-topic"}, {"account_id": "acc-greeley", "location_id": "loc-greeley", "place_id": "place-greeley"}, 5)
        checks.append(("approval locks the event-sourced (new) implementation, not the stale cached (old) one", drift_result["approved_payload_hash"] == expected_drift_hash))
        checks.append(("the returned/cached proposal's implementation is also the NEW content, not left stale", drift_result["implementation"]["summary"] == new_impl["summary"]))
        drift_after_reload = json.load(open(proposal_path(pending_dir, "prop-cache-drift"), encoding="utf-8"))
        checks.append(("...and this is what's actually persisted to disk, not just the in-memory return value", drift_after_reload["implementation"]["summary"] == new_impl["summary"]))

        # A degenerate-but-technically-present event-sourced implementation of {} (falsy) must
        # still be preferred over a non-empty CACHED implementation - an `or` fallback would
        # silently and wrongly prefer the cache here (Codex review, 2026-09-01, sixth round).
        write_proposal(pending_dir, {
            "id": "prop-empty-projected-impl", "action_type": "gbp-post-draft", "status": "review-revision-needed",
            "target": {"location": "Greeley", "topic": "some-topic"}, "implementation": old_impl,
        })
        _append_event_raw(loop_dir, "proposal_created", project=project, loop=loop, proposal_id="prop-empty-projected-impl", action_type="gbp-post-draft", resulting_proposal_status="review-revision-needed")
        _append_event_raw(loop_dir, "proposal_revised", project=project, loop=loop, proposal_id="prop-empty-projected-impl", action_type="gbp-post-draft", implementation_before=old_impl, implementation_after={}, resulting_proposal_status="review-pending")
        threw_on_empty_projected_impl = False
        try:
            approve_gbp_post(project, loop, "prop-empty-projected-impl")
        except ValueError as e:
            threw_on_empty_projected_impl = "has not been drafted yet" in str(e)
        checks.append(("an empty ({}) event-sourced implementation is used as-is (refused for having no summary), never silently replaced by a non-empty stale cache", threw_on_empty_projected_impl))

        # review_pending.decide() reject/review for a GBP proposal must emit the event FIRST,
        # required (not best-effort) - a failure there must abort the whole decide() call
        # BEFORE the cache is touched, never leave the cache saying "rejected" while the event
        # log a future publisher must trust still says something else (Codex review,
        # 2026-09-01, fifth round).
        _seed("prop-reject-normal")
        rejected = _review_decide(project, loop, "prop-reject-normal", "reject", by="test-human", note="not needed after all")
        checks.append(("review_pending.decide() can still reject a GBP proposal normally", rejected["status"] == "rejected"))
        reject_events = read_events(loop_dir, proposal_id="prop-reject-normal")
        checks.append(("...and the human_decision_recorded event carries the location/topic target and the rejection", any(e["event_type"] == "human_decision_recorded" and e.get("resulting_proposal_status") == "rejected" and e.get("target_location") == "Greeley" for e in reject_events)))

        _seed("prop-reject-event-failure")
        spec_path = os.path.join(loop_dir, "spec.md")
        with open(spec_path, "r", encoding="utf-8") as f:
            spec_backup = f.read()
        os.remove(spec_path)
        threw_on_event_failure = False
        try:
            _review_decide(project, loop, "prop-reject-event-failure", "reject", by="test-human")
        except Exception:
            threw_on_event_failure = True
        finally:
            with open(spec_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(spec_backup)
        checks.append(("if the required event append fails for a GBP proposal, decide() raises rather than swallowing it", threw_on_event_failure))
        after_event_failure = json.load(open(proposal_path(pending_dir, "prop-reject-event-failure"), encoding="utf-8"))
        checks.append(("...and the cache was never touched - never left saying 'rejected' with no matching event", after_event_failure["status"] == "draft"))

        # The Phase 7 Codex-review pipeline has no action_type check of its own and would
        # otherwise happily mutate a GBP proposal's status from a stale cached read, bypassing
        # this loop's event-sourced-authority model entirely (Codex review, 2026-09-01, fifth
        # round). Every one of its subcommands calls _load_proposal first, so this one refusal
        # covers --start-review/--evidence-packet/--record-round/--adjudicate uniformly.
        _seed("prop-codex-review-excluded")
        threw_codex_review_excluded = False
        try:
            codex_review_proposal.start_review(project, loop, "prop-codex-review-excluded")
        except ValueError as e:
            threw_codex_review_excluded = "does not support GBP Post proposals" in str(e)
        checks.append(("the Phase 7 Codex-review pipeline (start_review) refuses a gbp-post-draft proposal outright", threw_codex_review_excluded))

        threw_wrong_action = False
        write_proposal(pending_dir, {"id": "prop-wrong-action", "action_type": "title-tag-rewrite", "status": "draft", "target": {"page": "/x/"}})
        try:
            approve_gbp_post(project, loop, "prop-wrong-action")
        except ValueError as e:
            threw_wrong_action = "not a GBP post proposal" in str(e)
        checks.append(("a non-GBP action_type is refused by this tool", threw_wrong_action))

        threw_undrafted = False
        _seed("prop-undrafted", drafted=False)
        try:
            approve_gbp_post(project, loop, "prop-undrafted")
        except ValueError as e:
            threw_undrafted = "has not been drafted yet" in str(e)
        checks.append(("an undrafted proposal (no implementation) is refused", threw_undrafted))

        threw_no_event_history = False
        _seed("prop-no-events", emit_created_event=False)
        try:
            approve_gbp_post(project, loop, "prop-no-events")
        except ValueError as e:
            threw_no_event_history = "event-sourced current status is None" in str(e)
        checks.append(("a proposal with no event history at all is refused (event log, not the cached JSON, is authoritative)", threw_no_event_history))

        threw_double_approve = False
        try:
            approve_gbp_post(project, loop, "prop-good")
        except ValueError as e:
            threw_double_approve = "cannot approve" in str(e) and "current status is 'approved'" in str(e)
        checks.append(("re-approving an already-approved proposal is refused (checked via the event-sourced projection, not decide()'s cache)", threw_double_approve))

        threw_approve_after_reject = False
        _seed("prop-rejected")
        _review_decide(project, loop, "prop-rejected", "reject", by="test-human", note="not needed")
        try:
            approve_gbp_post(project, loop, "prop-rejected")
        except ValueError as e:
            threw_approve_after_reject = "current status is 'rejected'" in str(e)
        checks.append(("a rejected proposal cannot be approved - reject (via review_pending.decide, same lock convention) is visible through the same event-sourced read this tool uses", threw_approve_after_reject))

        threw_missing_mapping = False
        os.remove(os.path.join(loop_dir, "locations.json"))
        _seed("prop-no-mapping-file")
        try:
            approve_gbp_post(project, loop, "prop-no-mapping-file")
        except FileNotFoundError:
            threw_missing_mapping = True
        checks.append(("a missing locations.json fails closed", threw_missing_mapping))
    finally:
        shutil.rmtree(project_dir, ignore_errors=True)

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

# Human review CLI for pending proposals. Advances the approval state
# machine: draft -> reviewed/rejected -> approved. Never performs the
# `applied` transition itself - that is tools/apply.py's job, and it
# re-checks approval before doing anything.
import json
import os
import sys
from datetime import datetime, timezone

try:
    from .lib.proposals import atomic_write_json, load_json, pending_dir_for, proposal_path
except ImportError:
    from lib.proposals import atomic_write_json, load_json, pending_dir_for, proposal_path

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(THIS_DIR)
PROJECTS_ROOT = os.path.join(WORKSPACE_ROOT, "projects")

TRANSITIONS = {
    "approve": {"from": ["draft", "reviewed"], "to": "approved"},
    "reject": {"from": ["draft", "reviewed", "approved"], "to": "rejected"},
    "review": {"from": ["draft"], "to": "reviewed"},
    "retry": {"from": ["implement-failed"], "to": "approved"},
}


def _pending_dir_for(project, loop):
    return pending_dir_for(project, loop)


def list_proposals(project, loop):
    d = _pending_dir_for(project, loop)
    if not os.path.exists(d):
        return []
    out = []
    for f in sorted(os.listdir(d)):
        if not f.endswith(".json"):
            continue
        out.append(load_json(os.path.join(d, f)))
    return out


def _write_proposal(project, loop, proposal):
    d = _pending_dir_for(project, loop)
    atomic_write_json(proposal_path(d, proposal["id"]), proposal)


def decide(project, loop, proposal_id, action, by="human", note=""):
    t = TRANSITIONS.get(action)
    if not t:
        raise ValueError(f'unknown action "{action}" (expected approve|reject|review)')
    proposals = list_proposals(project, loop)
    p = next((x for x in proposals if x["id"] == proposal_id), None)
    if p is None:
        raise ValueError(f"proposal {proposal_id} not found")
    if p["status"] not in t["from"]:
        raise ValueError(f'cannot {action} proposal {proposal_id}: current status is "{p["status"]}", expected one of {", ".join(t["from"])}')
    p["status"] = t["to"]
    p["decision"] = {"action": action, "by": by, "note": note, "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    _write_proposal(project, loop, p)
    return p


def resolve_breach(project, loop, by="human", note=""):
    project_dir = os.path.join(PROJECTS_ROOT, project)
    loop_dir = os.path.join(project_dir, "loops", loop)
    state_path = os.path.join(loop_dir, "state.json")
    state = load_json(state_path)
    if state.get("status") != "paused-breach":
        raise ValueError(f'loop {loop} is not paused-breach (current status: {state.get("status")})')
    resolved = {
        "status": "active",
        "resolved_from": state,
        "resolved_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "resolved_by": by,
        "resolution_note": note,
    }
    atomic_write_json(state_path, resolved)
    return resolved


def _cli():
    args = sys.argv[1:]
    # First two non-flag tokens are project, loop.
    positional = []
    i = 0
    while i < len(args) and len(positional) < 2:
        if args[i].startswith("--"):
            break
        positional.append(args[i])
        i += 1
    project = positional[0] if len(positional) > 0 else None
    loop = positional[1] if len(positional) > 1 else None
    rest = args[len(positional):]

    if not project or not loop:
        print(
            "usage: python tools/review_pending.py <project> <loop> --list | --review <id> | --approve <id> | --reject <id> | --retry <id> [--reason r] | --resolve-breach [--reason r]",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        if "--list" in rest:
            for p in list_proposals(project, loop):
                stale_flag = " [STALE]" if p.get("run_cycles_seen", 0) >= 3 and p["status"] in ("draft", "reviewed") else ""
                print(f'{p["id"]}\tstatus={p["status"]}\ttier={p["tier"]}\ttype={p["action_type"]}\ttarget={json.dumps(p["target"])}{stale_flag}')
            return

        reason = ""
        if "--reason" in rest:
            idx = rest.index("--reason")
            if idx + 1 < len(rest):
                reason = rest[idx + 1]

        if "--review" in rest:
            idx = rest.index("--review")
            pid = rest[idx + 1]
            p = decide(project, loop, pid, "review", note=reason)
            print(f'reviewed {p["id"]}')
        elif "--approve" in rest:
            idx = rest.index("--approve")
            pid = rest[idx + 1]
            p = decide(project, loop, pid, "approve", note=reason)
            print(f'approved {p["id"]}')
        elif "--reject" in rest:
            idx = rest.index("--reject")
            pid = rest[idx + 1]
            p = decide(project, loop, pid, "reject", note=reason)
            print(f'rejected {p["id"]}')
        elif "--retry" in rest:
            idx = rest.index("--retry")
            pid = rest[idx + 1]
            p = decide(project, loop, pid, "retry", note=reason)
            print(f'retried {p["id"]}')
        elif "--resolve-breach" in rest:
            state = resolve_breach(project, loop, note=reason)
            print(f'resolved breach, loop status now: {state["status"]}')
        else:
            print("no action specified", file=sys.stderr)
            sys.exit(2)
    except Exception as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()

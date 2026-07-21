import json
import os
import tempfile

try:
    from .paths import assert_within
except ImportError:
    from lib.paths import assert_within

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.dirname(THIS_DIR)
WORKSPACE_ROOT = os.path.dirname(TOOLS_DIR)
PROJECTS_ROOT = os.path.join(WORKSPACE_ROOT, "projects")


def loop_dir_for(project, loop):
    project_dir = os.path.join(PROJECTS_ROOT, project)
    assert_within(PROJECTS_ROOT, project_dir, "project directory")
    loop_dir = os.path.join(project_dir, "loops", loop)
    assert_within(project_dir, loop_dir, "loop directory")
    return loop_dir


def pending_dir_for(project, loop):
    return os.path.join(loop_dir_for(project, loop), "pending")


def proposal_path(pending_dir, proposal_id):
    path = os.path.join(pending_dir, f"{proposal_id}.json")
    assert_within(pending_dir, path, "proposal file")
    return path


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def atomic_write_json(path, data):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def list_proposals(pending_dir):
    if not os.path.exists(pending_dir):
        return []
    out = []
    for name in sorted(os.listdir(pending_dir)):
        if not name.endswith(".json"):
            continue
        proposal = load_json(os.path.join(pending_dir, name))
        proposal["_file"] = name
        out.append(proposal)
    return out


def write_proposal(pending_dir, proposal):
    atomic_write_json(proposal_path(pending_dir, proposal["id"]), proposal)

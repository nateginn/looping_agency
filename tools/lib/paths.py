# Canonical path helpers - boundary checks must use resolved absolute
# canonical paths (symlinks/junctions resolved), never naive prefix matching.
import os


def canonical(p):
    abs_p = os.path.abspath(p)
    try:
        return os.path.realpath(abs_p, strict=True)
    except OSError:
        # Path may not exist yet (e.g. a dir we're about to create) - resolve
        # the nearest existing ancestor and rebuild the tail on top of it.
        parent = os.path.dirname(abs_p)
        if parent == abs_p:
            return abs_p
        return os.path.join(canonical(parent), os.path.basename(abs_p))


def is_within(root, child):
    """True iff `child` resolves to a path inside (or equal to) `root`."""
    root_c = canonical(root)
    child_c = canonical(child)
    rel = os.path.relpath(child_c, root_c)
    return rel == os.curdir or (not rel.startswith(os.pardir) and not os.path.isabs(rel))


def assert_within(root, child, label="path"):
    if not is_within(root, child):
        raise ValueError(f'Boundary violation: {label} "{child}" resolves outside allowed root "{root}"')

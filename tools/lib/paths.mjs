// Canonical path helpers — boundary checks must use resolved absolute
// canonical paths (symlinks/junctions resolved), never naive prefix matching.
import fs from 'node:fs';
import path from 'node:path';

export function canonical(p) {
  const abs = path.resolve(p);
  try {
    return fs.realpathSync.native ? fs.realpathSync.native(abs) : fs.realpathSync(abs);
  } catch {
    // Path may not exist yet (e.g. a dir we're about to create) — resolve
    // the nearest existing ancestor and rebuild the tail on top of it.
    const parent = path.dirname(abs);
    if (parent === abs) return abs;
    return path.join(canonical(parent), path.basename(abs));
  }
}

/** True iff `child` resolves to a path inside (or equal to) `root`. */
export function isWithin(root, child) {
  const rootC = canonical(root);
  const childC = canonical(child);
  const rel = path.relative(rootC, childC);
  return rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel));
}

export function assertWithin(root, child, label = 'path') {
  if (!isWithin(root, child)) {
    throw new Error(
      `Boundary violation: ${label} "${child}" resolves outside allowed root "${root}"`
    );
  }
}

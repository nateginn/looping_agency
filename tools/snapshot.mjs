// Writes an immutable, timestamped metrics snapshot under runs/<run-id>/.
// Immutability is enforced with a read-only file mode after write — the
// run engine never edits a snapshot once written, only reads it back.
import fs from 'node:fs';
import path from 'node:path';
import { redactDeep } from './lib/redact.mjs';

export function writeSnapshot(runDir, metrics, secretMap = {}) {
  fs.mkdirSync(runDir, { recursive: true });
  const snapshotPath = path.join(runDir, 'snapshot.json');
  const redacted = redactDeep(metrics, secretMap);
  fs.writeFileSync(snapshotPath, JSON.stringify(redacted, null, 2));
  fs.chmodSync(snapshotPath, 0o444);
  return snapshotPath;
}

async function selfTest() {
  const os = await import('node:os');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'snapshot-test-'));
  const secretMap = { alias1: 'super-secret-value' };
  const metrics = { note: 'token super-secret-value here', nested: { again: 'super-secret-value' } };

  const p = writeSnapshot(path.join(tmp, 'runs/run-1'), metrics, secretMap);
  const written = fs.readFileSync(p, 'utf8');
  const stats = fs.statSync(p);

  const checks = [
    ['snapshot file created', fs.existsSync(p)],
    ['snapshot redacted secret before write', !written.includes('super-secret-value')],
    ['snapshot is read-only (immutable)', (stats.mode & 0o200) === 0],
  ];

  fs.chmodSync(p, 0o666); // allow cleanup on Windows
  fs.rmSync(tmp, { recursive: true, force: true });

  let failed = 0;
  for (const [name, ok] of checks) {
    console.log(`${ok ? 'PASS' : 'FAIL'} - ${name}`);
    if (!ok) failed++;
  }
  process.exit(failed ? 1 : 0);
}

const isMain = process.argv[1] && process.argv[1].replace(/\\/g, '/').endsWith('tools/snapshot.mjs');
if (isMain && process.argv.includes('--verify')) selfTest();

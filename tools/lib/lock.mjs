// Per-loop run lock: refuse to start if a live lock exists; recover
// automatically from stale locks (dead PID or age > max_run_duration).
import fs from 'node:fs';
import path from 'node:path';

function isAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return err.code === 'EPERM'; // exists but we lack permission -> treat as alive
  }
}

export function lockPathFor(loopDir) {
  return path.join(loopDir, 'run.lock');
}

export function makeRunId() {
  const iso = new Date().toISOString().replace(/[:.]/g, '-');
  const rand = Math.random().toString(36).slice(2, 8);
  return `${iso}-${rand}`;
}

/**
 * @returns {{acquired:true, runId:string, staleRecovered?:object}|{acquired:false, reason:string, heldBy:object}}
 */
export function acquireLock(loopDir, { maxRunDurationMinutes, runsDir, now = new Date() } = {}) {
  const lockPath = lockPathFor(loopDir);
  let staleRecovered;

  if (fs.existsSync(lockPath)) {
    const held = JSON.parse(fs.readFileSync(lockPath, 'utf8'));
    const ageMs = now.getTime() - new Date(held.startTime).getTime();
    const maxMs = (maxRunDurationMinutes ?? 60) * 60 * 1000;
    const alive = isAlive(held.pid);
    const stale = !alive || ageMs > maxMs;

    if (!stale) {
      return {
        acquired: false,
        reason: `active lock held by pid ${held.pid} (run ${held.runId}) since ${held.startTime}`,
        heldBy: held,
      };
    }

    // Stale: archive the old lock for audit, then proceed to acquire fresh.
    const archiveDir = path.join(runsDir ?? path.join(loopDir, 'runs'), held.runId ?? 'unknown-run');
    fs.mkdirSync(archiveDir, { recursive: true });
    fs.writeFileSync(
      path.join(archiveDir, 'stale-lock.json'),
      JSON.stringify(
        { ...held, staleReason: !alive ? 'pid-not-alive' : 'age-exceeded-max-run-duration', recoveredAt: now.toISOString() },
        null,
        2
      )
    );
    fs.rmSync(lockPath, { force: true });
    staleRecovered = held;
  }

  const runId = makeRunId();
  const record = { runId, pid: process.pid, startTime: now.toISOString() };
  // Exclusive create — races with another process are still refused, not clobbered.
  const fd = fs.openSync(lockPath, 'wx');
  fs.writeFileSync(fd, JSON.stringify(record, null, 2));
  fs.closeSync(fd);
  return { acquired: true, runId, staleRecovered };
}

export function releaseLock(loopDir, runId) {
  const lockPath = lockPathFor(loopDir);
  if (!fs.existsSync(lockPath)) return;
  const held = JSON.parse(fs.readFileSync(lockPath, 'utf8'));
  if (held.runId === runId) fs.rmSync(lockPath, { force: true });
}

export function logRefusal(loopDir, reason) {
  const line = `${new Date().toISOString()} REFUSED: ${reason}\n`;
  fs.appendFileSync(path.join(loopDir, 'lock-refusals.log'), line);
}

async function selfTest() {
  const os = await import('node:os');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'lock-test-'));
  const results = [];

  // 1. Fresh acquire succeeds.
  const a = acquireLock(tmp, { maxRunDurationMinutes: 60 });
  results.push(['fresh lock acquires', a.acquired === true]);

  // 2. Second acquire while held is refused.
  const b = acquireLock(tmp, { maxRunDurationMinutes: 60 });
  results.push(['concurrent acquire refused', b.acquired === false]);

  releaseLock(tmp, a.runId);
  results.push(['release removes lockfile', !fs.existsSync(lockPathFor(tmp))]);

  // 3. Stale lock (dead pid) is recovered automatically.
  fs.writeFileSync(
    lockPathFor(tmp),
    JSON.stringify({ runId: 'dead-run', pid: 999999, startTime: new Date().toISOString() })
  );
  const c = acquireLock(tmp, { maxRunDurationMinutes: 60, runsDir: path.join(tmp, 'runs') });
  results.push(['dead-pid lock recovered as stale', c.acquired === true && !!c.staleRecovered]);
  results.push([
    'stale lock archived for audit',
    fs.existsSync(path.join(tmp, 'runs', 'dead-run', 'stale-lock.json')),
  ]);
  releaseLock(tmp, c.runId);

  // 4. Stale lock (own pid, but age exceeded) is recovered.
  fs.writeFileSync(
    lockPathFor(tmp),
    JSON.stringify({ runId: 'old-run', pid: process.pid, startTime: new Date(Date.now() - 999 * 60 * 1000).toISOString() })
  );
  const d = acquireLock(tmp, { maxRunDurationMinutes: 30, runsDir: path.join(tmp, 'runs') });
  results.push(['aged-out lock recovered as stale', d.acquired === true && !!d.staleRecovered]);
  releaseLock(tmp, d.runId);

  fs.rmSync(tmp, { recursive: true, force: true });

  let failed = 0;
  for (const [name, ok] of results) {
    console.log(`${ok ? 'PASS' : 'FAIL'} - ${name}`);
    if (!ok) failed++;
  }
  process.exit(failed ? 1 : 0);
}

if (process.argv[1] && process.argv[1].endsWith('lock.mjs') && process.argv.includes('--verify')) {
  selfTest();
}

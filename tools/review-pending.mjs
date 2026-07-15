// Human review CLI for pending proposals. Advances the approval state
// machine: draft -> reviewed/rejected -> approved. Never performs the
// `applied` transition itself — that is tools/apply.mjs's job, and it
// re-checks approval before doing anything.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { assertWithin } from './lib/paths.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WORKSPACE_ROOT = path.resolve(__dirname, '..');
const PROJECTS_ROOT = path.join(WORKSPACE_ROOT, 'projects');

function pendingDirFor(project, loop) {
  const projectDir = path.join(PROJECTS_ROOT, project);
  assertWithin(PROJECTS_ROOT, projectDir, 'project directory');
  const loopDir = path.join(projectDir, 'loops', loop);
  assertWithin(projectDir, loopDir, 'loop directory');
  return path.join(loopDir, 'pending');
}

export function listProposals(project, loop) {
  const dir = pendingDirFor(project, loop);
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith('.json'))
    .map((f) => JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8')));
}

function writeProposal(project, loop, proposal) {
  const dir = pendingDirFor(project, loop);
  fs.writeFileSync(path.join(dir, `${proposal.id}.json`), JSON.stringify(proposal, null, 2));
}

const TRANSITIONS = {
  approve: { from: ['draft', 'reviewed'], to: 'approved' },
  reject: { from: ['draft', 'reviewed', 'approved'], to: 'rejected' },
  review: { from: ['draft'], to: 'reviewed' },
};

export function decide(project, loop, proposalId, action, { by = 'human', note = '' } = {}) {
  const t = TRANSITIONS[action];
  if (!t) throw new Error(`unknown action "${action}" (expected approve|reject|review)`);
  const proposals = listProposals(project, loop);
  const p = proposals.find((x) => x.id === proposalId);
  if (!p) throw new Error(`proposal ${proposalId} not found`);
  if (!t.from.includes(p.status)) {
    throw new Error(`cannot ${action} proposal ${proposalId}: current status is "${p.status}", expected one of ${t.from.join(', ')}`);
  }
  p.status = t.to;
  p.decision = { action, by, note, at: new Date().toISOString() };
  writeProposal(project, loop, p);
  return p;
}

export function resolveBreach(project, loop, { by = 'human', note = '' } = {}) {
  const projectDir = path.join(PROJECTS_ROOT, project);
  const loopDir = path.join(projectDir, 'loops', loop);
  const statePath = path.join(loopDir, 'state.json');
  const state = JSON.parse(fs.readFileSync(statePath, 'utf8'));
  if (state.status !== 'paused-breach') {
    throw new Error(`loop ${loop} is not paused-breach (current status: ${state.status})`);
  }
  const resolved = {
    status: 'active',
    resolved_from: state,
    resolved_at: new Date().toISOString(),
    resolved_by: by,
    resolution_note: note,
  };
  fs.writeFileSync(statePath, JSON.stringify(resolved, null, 2));
  return resolved;
}

function cli() {
  const [, , project, loop, ...rest] = process.argv;
  if (!project || !loop) {
    console.error('usage: node tools/review-pending.mjs <project> <loop> --list | --approve <id> | --reject <id> [--reason r] | --resolve-breach [--reason r]');
    process.exit(2);
  }
  try {
    if (rest.includes('--list')) {
      const proposals = listProposals(project, loop);
      for (const p of proposals) {
        const staleFlag = p.run_cycles_seen >= 3 && ['draft', 'reviewed'].includes(p.status) ? ' [STALE]' : '';
        console.log(`${p.id}\tstatus=${p.status}\ttier=${p.tier}\ttype=${p.action_type}\ttarget=${JSON.stringify(p.target)}${staleFlag}`);
      }
      return;
    }
    const reasonIdx = rest.indexOf('--reason');
    const reason = reasonIdx >= 0 ? rest[reasonIdx + 1] : '';

    if (rest.includes('--approve')) {
      const id = rest[rest.indexOf('--approve') + 1];
      const p = decide(project, loop, id, 'approve', { note: reason });
      console.log(`approved ${p.id}`);
    } else if (rest.includes('--reject')) {
      const id = rest[rest.indexOf('--reject') + 1];
      const p = decide(project, loop, id, 'reject', { note: reason });
      console.log(`rejected ${p.id}`);
    } else if (rest.includes('--resolve-breach')) {
      const state = resolveBreach(project, loop, { note: reason });
      console.log(`resolved breach, loop status now: ${state.status}`);
    } else {
      console.error('no action specified');
      process.exit(2);
    }
  } catch (err) {
    console.error(`ERROR: ${err.message}`);
    process.exit(1);
  }
}

const isMain = process.argv[1] && process.argv[1].replace(/\\/g, '/').endsWith('tools/review-pending.mjs');
if (isMain) cli();

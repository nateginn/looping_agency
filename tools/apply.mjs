// Performs the `applied` transition for a single approved Tier-1 proposal.
// This is the approval gate: it re-checks status === 'approved' itself
// rather than trusting the caller, and it never touches Tier-2 actions —
// those are human-only, always, per AgentColabPlan.md side-effect tiers.
//
// Phase 1 boundary: the "action" performed here is a local simulated
// marker (e.g. what would become a PR-branch-creation call). No real
// repo, API, or credential is touched by this file in this phase.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import yaml from 'js-yaml';
import { assertWithin } from './lib/paths.mjs';
import { extractFrontmatter } from './spec-validate.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WORKSPACE_ROOT = path.resolve(__dirname, '..');
const PROJECTS_ROOT = path.join(WORKSPACE_ROOT, 'projects');

function loopDirFor(project, loop) {
  const projectDir = path.join(PROJECTS_ROOT, project);
  assertWithin(PROJECTS_ROOT, projectDir, 'project directory');
  const loopDir = path.join(projectDir, 'loops', loop);
  assertWithin(projectDir, loopDir, 'loop directory');
  return loopDir;
}

function loadApprovalMode(loopDir) {
  const specPath = path.join(loopDir, 'spec.md');
  const source = fs.readFileSync(specPath, 'utf8');
  const parsed = yaml.load(extractFrontmatter(source));
  return parsed?.approval_mode;
}

export function applyProposal(project, loop, proposalId, { by = 'human' } = {}) {
  const loopDir = loopDirFor(project, loop);
  const pendingDir = path.join(loopDir, 'pending');
  const proposalPath = path.join(pendingDir, `${proposalId}.json`);
  if (!fs.existsSync(proposalPath)) throw new Error(`proposal ${proposalId} not found`);
  const proposal = JSON.parse(fs.readFileSync(proposalPath, 'utf8'));

  if (proposal.tier === 2) {
    throw new Error(`REFUSED: proposal ${proposalId} is Tier 2 (public/paid) — always human-only, never automated by apply.mjs`);
  }
  if (proposal.tier === 1) {
    const approvalMode = loadApprovalMode(loopDir);
    if (approvalMode !== 'tier1-enabled') {
      throw new Error(
        `REFUSED: proposal ${proposalId} is Tier 1 but this loop's approval_mode is "${approvalMode}" — ` +
          'Tier-1 applies require approval_mode: tier1-enabled (see AgentColabPlan.md Phase 2: enabled only after human review of the first two reports)'
      );
    }
  }
  if (proposal.status !== 'approved') {
    throw new Error(`REFUSED: proposal ${proposalId} has status "${proposal.status}", not "approved" — approval gate blocks apply`);
  }

  const appliedAt = new Date().toISOString();
  proposal.status = 'applied';
  proposal.applied_at = appliedAt;
  proposal.applied_by = by;
  fs.writeFileSync(proposalPath, JSON.stringify(proposal, null, 2));

  // Markers live outside pending/ so they're never mistaken for proposal state
  // by listPendingProposals()/listProposals() (which glob every *.json there).
  const appliedDir = path.join(loopDir, 'applied');
  fs.mkdirSync(appliedDir, { recursive: true });
  const markerPath = path.join(appliedDir, `${proposalId}.marker.json`);
  fs.writeFileSync(
    markerPath,
    JSON.stringify(
      {
        note: 'Phase 1 simulated Tier-1 side effect — no real repo/API/credential touched.',
        proposal_id: proposalId,
        action_type: proposal.action_type,
        target: proposal.target,
        applied_at: appliedAt,
        applied_by: by,
      },
      null,
      2
    )
  );

  return proposal;
}

function cli() {
  const [, , project, loop, proposalId] = process.argv;
  if (!project || !loop || !proposalId) {
    console.error('usage: node tools/apply.mjs <project> <loop> <proposal-id>');
    process.exit(2);
  }
  try {
    const p = applyProposal(project, loop, proposalId);
    console.log(`applied ${p.id} at ${p.applied_at}`);
  } catch (err) {
    console.error(err.message);
    process.exit(1);
  }
}

const isMain = process.argv[1] && process.argv[1].replace(/\\/g, '/').endsWith('tools/apply.mjs');
if (isMain) cli();

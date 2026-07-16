// Integration tests for AgentColabPlan.md Milestone-1 exit criteria.
// Operates on a disposable projects/_phase1-test-tmp/ fixture (created and
// torn down here) so it never touches the real projects/_demo run history
// used for the separate "two consecutive dry runs" proof.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { runLoop } from '../run-loop.mjs';
import { decide, listProposals } from '../review-pending.mjs';
import { applyProposal } from '../apply.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WORKSPACE_ROOT = path.resolve(__dirname, '../..');
const PROJECTS_ROOT = path.join(WORKSPACE_ROOT, 'projects');
const PROJECT = '_phase1-test-tmp';
const LOOP = 'seo';
const projectDir = path.join(PROJECTS_ROOT, PROJECT);
const loopDir = path.join(projectDir, 'loops', LOOP);

const results = [];
function check(name, ok) {
  results.push([name, ok]);
  console.log(`${ok ? 'PASS' : 'FAIL'} - ${name}`);
}

const GOOD_SPEC = `---
version: 1
loop: seo
objective: Phase 1 exit-criteria test fixture
primary_metric: gsc_position
guardrail_metrics:
  - name: ranking_pages_position
    comparator: ">"
    threshold: 5
    consecutive_runs: 1
failure_threshold:
  metric: ranking_pages_position
  comparator: ">"
  value: 5
inputs:
  - mock
allowed_actions:
  - type: title-tag-rewrite
    tier: 1
    rollback: revert PR
    observation_window_days: 0.0001
    min_sample_size: 100
approval_mode: tier1-enabled
max_run_duration_minutes: 5
schedule: "manual"
stop_condition: test fixture teardown
memory: memory.md
credential_aliases:
  mock: fixture-alias
---
# fixture
`;

const BAD_SPEC = `---
version: 1
loop: seo
objective: missing required fields
approval_mode: yolo-mode
---
# broken
`;

function resetFixture() {
  fs.rmSync(projectDir, { recursive: true, force: true });
  fs.mkdirSync(path.join(loopDir, 'pending'), { recursive: true });
  fs.mkdirSync(path.join(loopDir, 'runs'), { recursive: true });
  fs.writeFileSync(path.join(loopDir, 'spec.md'), GOOD_SPEC);
  fs.writeFileSync(path.join(loopDir, 'memory.md'), '# Memory — fixture\n');
}

async function testSpecValidationRejectsBadSpec() {
  resetFixture();
  fs.writeFileSync(path.join(loopDir, 'spec.md'), BAD_SPEC);
  const result = await runLoop(PROJECT, LOOP, { scenario: 'normal' });
  check('bad spec: run-loop refuses with status invalid-spec', result.status === 'invalid-spec');
  const badSpecRunDir = path.join(loopDir, 'runs', result.runId);
  check(
    'bad spec: no run.json written (only validation-failure.json)',
    fs.existsSync(path.join(badSpecRunDir, 'validation-failure.json')) && !fs.existsSync(path.join(badSpecRunDir, 'run.json'))
  );
  check('bad spec: lock released after refusal', !fs.existsSync(path.join(loopDir, 'run.lock')));
}

async function testLockRefusalAndStaleRecovery() {
  resetFixture();
  // Live lock (this process's own pid, fresh) -> refused.
  fs.writeFileSync(
    path.join(loopDir, 'run.lock'),
    JSON.stringify({ runId: 'live-run', pid: process.pid, startTime: new Date().toISOString() })
  );
  const refused = await runLoop(PROJECT, LOOP, { scenario: 'normal' });
  check('live lock: concurrent run refused', refused.status === 'refused');
  check('live lock: refusal logged', fs.readFileSync(path.join(loopDir, 'lock-refusals.log'), 'utf8').includes('live-run'));

  // Stale lock (dead pid) -> auto-recovered, run proceeds.
  fs.writeFileSync(
    path.join(loopDir, 'run.lock'),
    JSON.stringify({ runId: 'dead-run', pid: 999999, startTime: new Date().toISOString() })
  );
  const recovered = await runLoop(PROJECT, LOOP, { scenario: 'normal' });
  check('stale lock (dead pid): run proceeds', recovered.status === 'ok');
  check(
    'stale lock: archived for audit',
    fs.existsSync(path.join(loopDir, 'runs', 'dead-run', 'stale-lock.json'))
  );
  check('lock released after successful run', !fs.existsSync(path.join(loopDir, 'run.lock')));
}

async function testRedactionNeverLeaksSecret() {
  resetFixture();
  const FAKE_SECRET = 'sk-demo-FAKE1234567890ABCDEFDONOTUSE'; // must match tools/mock-metrics.mjs
  const result = await runLoop(PROJECT, LOOP, { scenario: 'normal' });
  const runDir = path.join(loopDir, 'runs', result.runId);
  const runJsonText = fs.readFileSync(path.join(runDir, 'run.json'), 'utf8');
  const reportText = fs.readFileSync(path.join(runDir, 'report.md'), 'utf8');
  const snapshotText = fs.readFileSync(path.join(runDir, 'snapshot.json'), 'utf8');
  check('run.json never contains the planted fake secret', !runJsonText.includes(FAKE_SECRET));
  check('report.md never contains the planted fake secret', !reportText.includes(FAKE_SECRET));
  check('snapshot.json never contains the planted fake secret', !snapshotText.includes(FAKE_SECRET));
}

async function testPartialFailureCleanLog() {
  resetFixture();
  const result = await runLoop(PROJECT, LOOP, { scenario: 'fail' });
  check('connector failure: status partial-failure', result.status === 'partial-failure');
  const runDir = path.join(loopDir, 'runs', result.runId);
  const runJson = JSON.parse(fs.readFileSync(path.join(runDir, 'run.json'), 'utf8'));
  check('partial-failure run.json has final_status partial-failure', runJson.final_status === 'partial-failure');
  check('partial-failure run.json records the failed tool call', runJson.tool_calls[0].ok === false);
  check('partial-failure run.json error message is redacted', !JSON.stringify(runJson).includes('sk-demo-FAKE'));
  check('lock released after partial failure', !fs.existsSync(path.join(loopDir, 'run.lock')));
}

async function testPauseOnBreachBlocksNewProposals() {
  resetFixture();
  // Seed an "applied" proposal targeting the page mock-metrics regresses in 'breach' scenario.
  const appliedProposal = {
    id: 'prop-seed-0',
    loop: LOOP,
    action_type: 'title-tag-rewrite',
    tier: 1,
    target: { page: '/blog/seo-automation', keyword: 'seo automation tool' },
    baseline_position: 11.5,
    rationale: 'seed',
    rollback: 'revert PR',
    manual_approval_only: false,
    status: 'applied',
    created_run_id: 'seed',
    created_at: new Date(Date.now() - 60000).toISOString(),
    run_cycles_seen: 0,
    decision: { action: 'approve', by: 'test', at: new Date().toISOString() },
    applied_at: new Date(Date.now() - 60000).toISOString(), // 1 min ago, window is ~9s so already elapsed
    observation_window_days: 0.0001,
    min_sample_size: 100,
  };
  fs.writeFileSync(path.join(loopDir, 'pending', `${appliedProposal.id}.json`), JSON.stringify(appliedProposal, null, 2));

  const breachRun = await runLoop(PROJECT, LOOP, { scenario: 'breach' });
  check('breach run: status paused-breach', breachRun.status === 'paused-breach');
  const state = JSON.parse(fs.readFileSync(path.join(loopDir, 'state.json'), 'utf8'));
  check('state.json shows paused-breach', state.status === 'paused-breach');
  check('breach run created 0 new proposals', breachRun.runJson.proposals_created.length === 0);

  const blockedRun = await runLoop(PROJECT, LOOP, { scenario: 'normal' });
  check('subsequent run while paused-breach also creates 0 new proposals', blockedRun.runJson.proposals_created.length === 0);
  check('subsequent run reports the block explicitly', blockedRun.runJson.decisions.some((d) => d.includes('BLOCKED')));

  const revertId = `revert-${appliedProposal.id}`;
  check('auto-generated revert proposal exists', listProposals(PROJECT, LOOP).some((p) => p.id === revertId));

  const resolved = (await import('../review-pending.mjs')).resolveBreach(PROJECT, LOOP, { note: 'test resolves breach' });
  check('resolveBreach clears paused-breach', resolved.status === 'active');
  const afterResolve = await runLoop(PROJECT, LOOP, { scenario: 'normal' });
  check('after resolving breach, new proposals can be created again', afterResolve.runJson.proposals_created.length > 0);
}

async function testApprovalGatesPreventUnapprovedApply() {
  resetFixture();
  const draft = {
    id: 'prop-gate-test',
    loop: LOOP,
    action_type: 'title-tag-rewrite',
    tier: 1,
    target: { page: '/blog/x', keyword: 'x' },
    rationale: 'test',
    rollback: 'revert PR',
    manual_approval_only: false,
    status: 'draft',
    created_run_id: 'seed',
    created_at: new Date().toISOString(),
    run_cycles_seen: 0,
    decision: null,
    applied_at: null,
    observation_window_days: 0.0001,
    min_sample_size: 100,
  };
  fs.writeFileSync(path.join(loopDir, 'pending', `${draft.id}.json`), JSON.stringify(draft, null, 2));

  let refusedUnapproved = false;
  try {
    applyProposal(PROJECT, LOOP, draft.id);
  } catch (e) {
    refusedUnapproved = /approval gate blocks apply/.test(e.message);
  }
  check('apply.mjs refuses an unapproved (draft) proposal', refusedUnapproved);

  decide(PROJECT, LOOP, draft.id, 'approve', { by: 'test' });
  const applied = applyProposal(PROJECT, LOOP, draft.id);
  check('apply.mjs succeeds once approved (spec is tier1-enabled)', applied.status === 'applied');
  check(
    'applied marker written outside pending/ (never mistaken for proposal state)',
    fs.existsSync(path.join(loopDir, 'applied', `${draft.id}.marker.json`)) &&
      !fs.existsSync(path.join(loopDir, 'pending', `${draft.id}.applied-marker.json`))
  );
  check(
    'marker file is not picked up by listProposals (no id/status/tier pollution)',
    !listProposals(PROJECT, LOOP).some((p) => p.id === undefined)
  );

  // Tier 2 must always refuse regardless of approval status.
  const tier2 = { ...draft, id: 'prop-tier2-test', tier: 2, status: 'draft' };
  fs.writeFileSync(path.join(loopDir, 'pending', `${tier2.id}.json`), JSON.stringify(tier2, null, 2));
  decide(PROJECT, LOOP, tier2.id, 'approve', { by: 'test' });
  let refusedTier2 = false;
  try {
    applyProposal(PROJECT, LOOP, tier2.id);
  } catch (e) {
    refusedTier2 = /Tier 2/.test(e.message);
  }
  check('apply.mjs refuses an approved Tier 2 proposal (human-only, always)', refusedTier2);
}

async function testProposeOnlyRefusesTier1Apply() {
  resetFixture();
  const proposeOnlySpec = GOOD_SPEC.replace('approval_mode: tier1-enabled', 'approval_mode: propose-only');
  fs.writeFileSync(path.join(loopDir, 'spec.md'), proposeOnlySpec);

  const draft = {
    id: 'prop-propose-only-test',
    loop: LOOP,
    action_type: 'title-tag-rewrite',
    tier: 1,
    target: { page: '/blog/x', keyword: 'x' },
    rationale: 'test',
    rollback: 'revert PR',
    manual_approval_only: false,
    status: 'draft',
    created_run_id: 'seed',
    created_at: new Date().toISOString(),
    run_cycles_seen: 0,
    decision: null,
    applied_at: null,
    observation_window_days: 0.0001,
    min_sample_size: 100,
  };
  fs.writeFileSync(path.join(loopDir, 'pending', `${draft.id}.json`), JSON.stringify(draft, null, 2));
  decide(PROJECT, LOOP, draft.id, 'approve', { by: 'test' });

  let refused = false;
  try {
    applyProposal(PROJECT, LOOP, draft.id);
  } catch (e) {
    refused = /approval_mode is "propose-only"/.test(e.message);
  }
  check('apply.mjs refuses an approved Tier-1 proposal when spec.approval_mode is propose-only', refused);
}

async function testStaleLockTtlComesFromSpec() {
  resetFixture();
  // GOOD_SPEC declares max_run_duration_minutes: 5. A live (own-pid) lock aged
  // 6 minutes should be recovered as stale using the SPEC's TTL, not a hardcoded default.
  fs.writeFileSync(
    path.join(loopDir, 'run.lock'),
    JSON.stringify({ runId: 'aged-per-spec-ttl', pid: process.pid, startTime: new Date(Date.now() - 6 * 60 * 1000).toISOString() })
  );
  const result = await runLoop(PROJECT, LOOP, { scenario: 'normal' });
  check('lock older than spec.max_run_duration_minutes (5) is recovered as stale, not the 60min default', result.status === 'ok');
  check(
    'stale lock archived under the run it belonged to',
    fs.existsSync(path.join(loopDir, 'runs', 'aged-per-spec-ttl', 'stale-lock.json'))
  );
}

async function main() {
  await testSpecValidationRejectsBadSpec();
  await testLockRefusalAndStaleRecovery();
  await testRedactionNeverLeaksSecret();
  await testPartialFailureCleanLog();
  await testPauseOnBreachBlocksNewProposals();
  await testApprovalGatesPreventUnapprovedApply();
  await testProposeOnlyRefusesTier1Apply();
  await testStaleLockTtlComesFromSpec();

  fs.rmSync(projectDir, { recursive: true, force: true });

  const failed = results.filter(([, ok]) => !ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed.`);
  process.exit(failed.length ? 1 : 0);
}

main();

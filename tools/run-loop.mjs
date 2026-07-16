// Run contract engine (AgentColabPlan.md "The run contract"). One call
// = one run of one loop for one project. Deterministic and testable by
// design: the judgment step (picking actions) uses a simple heuristic
// here; a human-in-the-loop skill wraps this for real proposal quality.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { acquireLock, releaseLock, logRefusal } from './lib/lock.mjs';
import { assertWithin, isWithin } from './lib/paths.mjs';
import { redactDeep } from './lib/redact.mjs';
import { validateSpecFile, extractFrontmatter } from './spec-validate.mjs';
import { writeSnapshot } from './snapshot.mjs';
import { pullMetrics as pullMockMetrics } from './mock-metrics.mjs';
import yaml from 'js-yaml';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WORKSPACE_ROOT = path.resolve(__dirname, '..');
const PROJECTS_ROOT = path.join(WORKSPACE_ROOT, 'projects');
const DEFAULT_LOCK_TTL_MINUTES = 60; // fallback when the spec doesn't parse at all
const MIN_LOCK_TTL_MINUTES = 1;
const MAX_LOCK_TTL_MINUTES = 24 * 60;

function nowIso() {
  return new Date().toISOString();
}

/**
 * Read just `max_run_duration_minutes` from spec.md, clamped to a sane range.
 * Runs *before* full schema validation (the lock must be acquired first, per
 * the run contract), so this only trusts a single bounded number out of an
 * otherwise-untrusted file — never the full spec — and falls back safely on
 * any parse error.
 */
function readLockTtlMinutesUnsafe(specPath, fallback = DEFAULT_LOCK_TTL_MINUTES) {
  try {
    const source = fs.readFileSync(specPath, 'utf8');
    const fm = extractFrontmatter(source);
    if (!fm) return fallback;
    const parsed = yaml.load(fm);
    const v = parsed?.max_run_duration_minutes;
    if (typeof v === 'number' && Number.isFinite(v) && v > 0) {
      return Math.min(Math.max(v, MIN_LOCK_TTL_MINUTES), MAX_LOCK_TTL_MINUTES);
    }
  } catch {
    // Unreadable/malformed spec: fall through to the conservative fallback below.
  }
  return fallback;
}

function loadSpec(specPath) {
  const source = fs.readFileSync(specPath, 'utf8');
  return yaml.load(extractFrontmatter(source));
}

function loadJsonSafe(p, fallback) {
  if (!fs.existsSync(p)) return fallback;
  return JSON.parse(fs.readFileSync(p, 'utf8'));
}

function listPendingProposals(pendingDir) {
  if (!fs.existsSync(pendingDir)) return [];
  return fs
    .readdirSync(pendingDir)
    .filter((f) => f.endsWith('.json'))
    .map((f) => ({ file: f, ...JSON.parse(fs.readFileSync(path.join(pendingDir, f), 'utf8')) }));
}

function writeProposal(pendingDir, proposal) {
  fs.mkdirSync(pendingDir, { recursive: true });
  fs.writeFileSync(path.join(pendingDir, `${proposal.id}.json`), JSON.stringify(proposal, null, 2));
}

function targetKey(target) {
  return JSON.stringify(target ?? {});
}

function compare(value, comparator, threshold) {
  switch (comparator) {
    case '<': return value < threshold;
    case '>': return value > threshold;
    case '<=': return value <= threshold;
    case '>=': return value >= threshold;
    case '==': return value === threshold;
    default: throw new Error(`unknown comparator ${comparator}`);
  }
}

async function fetchMetrics(spec, { scenario }) {
  const alias = spec.credential_aliases?.mock ?? 'demo-gsc-readonly';
  if (spec.inputs.includes('mock')) {
    return { toolName: 'mock-metrics', metrics: await pullMockMetrics({ scenario, credentialAlias: alias }) };
  }
  throw new Error(
    `run-loop.mjs: spec.inputs ${JSON.stringify(spec.inputs)} has no mock connector wired for this dry run. ` +
      'Live connectors (gsc/dataforseo) are out of scope until the Phase 1(b) smoke test.'
  );
}

/** Evaluate applied proposals whose observation window has elapsed. Returns { report lines, breach? } */
function evaluatePriorExperiments({ proposals, metrics, spec, runId, now }) {
  const decisions = [];
  let breach = null;
  const stillCoolingDown = new Set();

  for (const p of proposals) {
    if (p.status !== 'applied') continue;
    const ageDays = (now.getTime() - new Date(p.applied_at).getTime()) / 86400000;
    const windowElapsed = ageDays >= p.observation_window_days;
    const sampleOk = metrics.sample_size >= p.min_sample_size;

    if (!windowElapsed || !sampleOk) {
      stillCoolingDown.add(targetKey(p.target));
      decisions.push(`proposal ${p.id}: still in observation window (age ${ageDays.toFixed(1)}d/${p.observation_window_days}d, sample ${metrics.sample_size}/${p.min_sample_size})`);
      continue;
    }

    const row = metrics.keywords.find((k) => k.page === p.target.page);
    const ft = spec.failure_threshold;
    const metricValue = row ? row.position : undefined;
    // Guardrail is a *regression from baseline* (position drop post-change), not an
    // absolute position value — a keyword stably sitting at position 8 is not a breach.
    const baseline = p.baseline_position;
    const drift = row !== undefined && baseline !== undefined ? metricValue - baseline : undefined;
    const breached = drift !== undefined && ft.metric.includes('position') && compare(drift, ft.comparator, ft.value);

    if (breached) {
      p.status = 'breached';
      p.evaluated_run_id = runId;
      breach = {
        proposalId: p.id,
        reason: `guardrail breach on ${ft.metric} for ${p.target.page}: position moved ${baseline} -> ${metricValue} (drift ${drift.toFixed(1)} ${ft.comparator} ${ft.value})`,
      };
      decisions.push(`proposal ${p.id}: BREACH — ${breach.reason}`);
    } else {
      p.status = 'verified';
      p.evaluated_run_id = runId;
      decisions.push(`proposal ${p.id}: verified winner (position ${baseline} -> ${metricValue}, within guardrail)`);
    }
  }

  return { decisions, breach, stillCoolingDown };
}

function pickNewActions({ spec, metrics, coolingDown, runId, now, maxCount = 3 }) {
  const candidates = metrics.keywords
    .filter((k) => !coolingDown.has(targetKey({ page: k.page })))
    .filter((k) => k.position > 3 && k.position <= 20)
    .sort((a, b) => b.clicks - a.clicks);

  const proposals = [];
  for (let i = 0; i < Math.min(maxCount, candidates.length, spec.allowed_actions.length); i++) {
    const action = spec.allowed_actions[i % spec.allowed_actions.length];
    const kw = candidates[i];
    proposals.push({
      id: `prop-${runId}-${i}`,
      loop: spec.loop,
      action_type: action.type,
      tier: action.tier,
      target: { page: kw.page, keyword: kw.keyword },
      baseline_position: kw.position,
      rationale: `keyword "${kw.keyword}" at position ${kw.position} on ${kw.page} with ${kw.clicks} clicks — candidate for ${action.type}`,
      rollback: action.rollback ?? null,
      manual_approval_only: action.manual_approval_only === true,
      observation_window_days: action.observation_window_days,
      min_sample_size: action.min_sample_size,
      status: 'draft',
      created_run_id: runId,
      created_at: now.toISOString(),
      run_cycles_seen: 0,
      decision: null,
      applied_at: null,
    });
  }
  return proposals;
}

export async function runLoop(projectSlug, loopName, { scenario = 'normal' } = {}) {
  const projectDir = path.join(PROJECTS_ROOT, projectSlug);
  assertWithin(PROJECTS_ROOT, projectDir, 'project directory');
  const loopDir = path.join(projectDir, 'loops', loopName);
  assertWithin(projectDir, loopDir, 'loop directory');

  const specPath = path.join(loopDir, 'spec.md');
  const memoryPath = path.join(loopDir, 'memory.md');
  const pendingDir = path.join(loopDir, 'pending');
  const runsDir = path.join(loopDir, 'runs');
  const statePath = path.join(loopDir, 'state.json');

  const now = new Date();

  // Step 1: acquire lock. TTL comes from the loop's own spec.max_run_duration_minutes
  // (read unsafely/clamped — full schema validation happens in step 2, after the lock).
  const lockTtlMinutes = readLockTtlMinutesUnsafe(specPath);
  const lock = acquireLock(loopDir, { maxRunDurationMinutes: lockTtlMinutes, runsDir, now });
  if (!lock.acquired) {
    logRefusal(loopDir, lock.reason);
    return { status: 'refused', reason: lock.reason };
  }
  const runId = lock.runId;
  const runDir = path.join(runsDir, runId);

  try {
    // Step 2: validate spec.
    const validation = validateSpecFile(specPath);
    if (!validation.valid) {
      fs.mkdirSync(runDir, { recursive: true });
      fs.writeFileSync(path.join(runDir, 'validation-failure.json'), JSON.stringify(validation, null, 2));
      return { status: 'invalid-spec', runId, errors: validation.errors };
    }
    const spec = loadSpec(specPath);

    const state = loadJsonSafe(statePath, { status: 'active' });
    const proposals = listPendingProposals(pendingDir);

    // Step 3+9: pull metrics, write immutable snapshot (partial-failure path handled here).
    let metrics;
    let toolCallRecord;
    try {
      const pulled = await fetchMetrics(spec, { scenario });
      metrics = pulled.metrics;
      toolCallRecord = { tool: pulled.toolName, args: { scenario }, at: nowIso(), ok: true };
    } catch (err) {
      const secretMap = err.rawSecrets ?? {};
      const redactedMessage = redactDeep(err.message, secretMap);
      fs.mkdirSync(runDir, { recursive: true });
      const runJson = {
        run_id: runId,
        project: projectSlug,
        loop: loopName,
        start: now.toISOString(),
        end: nowIso(),
        status: 'partial-failure',
        tool_calls: [{ tool: 'mock-metrics', args: { scenario }, at: nowIso(), ok: false, error: redactedMessage }],
        credential_alias_used: spec.credential_aliases?.mock ?? null,
        decisions: ['connector failed; no proposals generated this run; prior state left untouched'],
        proposals_created: [],
        proposals_evaluated: [],
        stale_proposals: [],
        final_status: 'partial-failure',
      };
      fs.writeFileSync(path.join(runDir, 'run.json'), JSON.stringify(runJson, null, 2));
      fs.writeFileSync(
        path.join(runDir, 'report.md'),
        `# Run ${runId} (${loopName} / ${projectSlug})\n\n**Status:** partial-failure\n\nConnector call failed:\n\n\`\`\`\n${redactedMessage}\n\`\`\`\n\nNo proposals were generated this run. State left untouched.\n`
      );
      fs.appendFileSync(memoryPath, `- ${nowIso()} run ${runId}: partial-failure (connector error, redacted)\n`);
      return { status: 'partial-failure', runId };
    }

    const snapshotPath = writeSnapshot(runDir, metrics, metrics.secretMap ?? {});

    // Step 4: evaluate prior experiments (only past-window, sufficient-sample ones).
    const { decisions: evalDecisions, breach, stillCoolingDown } = evaluatePriorExperiments({
      proposals,
      metrics,
      spec,
      runId,
      now,
    });

    // Step 5: cooldown — also exclude anything still 'applied' and within window (already in stillCoolingDown).
    for (const p of proposals) {
      if (p.status === 'applied') stillCoolingDown.add(targetKey(p.target));
    }

    let newState = state;
    if (breach) {
      newState = {
        status: 'paused-breach',
        paused_reason: breach.reason,
        paused_at: nowIso(),
        breach_run_id: runId,
        breach_proposal_id: breach.proposalId,
      };
      const original = proposals.find((p) => p.id === breach.proposalId);
      const revertProposal = {
        id: `revert-${breach.proposalId}`,
        loop: loopName,
        action_type: `revert:${original.action_type}`,
        tier: original.tier,
        target: original.target,
        rationale: `auto-generated revert proposal after guardrail breach: ${breach.reason}`,
        rollback: original.rollback,
        manual_approval_only: true,
        status: 'draft',
        created_run_id: runId,
        created_at: nowIso(),
        run_cycles_seen: 0,
        decision: null,
        applied_at: null,
        observation_window_days: original.observation_window_days,
        min_sample_size: original.min_sample_size,
      };
      writeProposal(pendingDir, revertProposal);
      proposals.push(revertProposal);
    }

    // Step 6: stale-proposal surfacing — bump cycle counters on undecided proposals.
    const staleIds = [];
    for (const p of proposals) {
      if (p.status === 'draft' || p.status === 'reviewed') {
        p.run_cycles_seen = (p.run_cycles_seen ?? 0) + 1;
        if (p.run_cycles_seen >= 3) staleIds.push(p.id);
      }
    }

    // Step 7: pick new actions, unless paused-breach.
    let newProposals = [];
    if (newState.status === 'paused-breach') {
      evalDecisions.push('BLOCKED: loop is paused-breach — no new proposals until a human resolves the failed experiment via /review-pending');
    } else {
      newProposals = pickNewActions({ spec, metrics, coolingDown: stillCoolingDown, runId, now });
      for (const p of newProposals) writeProposal(pendingDir, p);
    }

    // Persist mutated proposal states (evaluated/breached/verified/stale counters).
    for (const p of proposals) {
      const { file, ...rest } = p;
      fs.writeFileSync(path.join(pendingDir, file ?? `${p.id}.json`), JSON.stringify(rest, null, 2));
    }
    fs.writeFileSync(statePath, JSON.stringify(newState, null, 2));

    // Step 8: run.json + report.md + memory.md append.
    const secretMap = metrics.secretMap ?? {};
    const runJson = redactDeep(
      {
        run_id: runId,
        project: projectSlug,
        loop: loopName,
        start: now.toISOString(),
        end: nowIso(),
        status: newState.status === 'paused-breach' ? 'paused-breach' : 'ok',
        tool_calls: [toolCallRecord],
        credential_alias_used: spec.credential_aliases?.mock ?? null,
        decisions: [...evalDecisions],
        proposals_created: newProposals.map((p) => p.id),
        proposals_evaluated: proposals.filter((p) => p.evaluated_run_id === runId).map((p) => p.id),
        stale_proposals: staleIds,
        snapshot: path.relative(loopDir, snapshotPath),
        final_status: newState.status === 'paused-breach' ? 'paused-breach' : 'ok',
      },
      secretMap
    );
    fs.writeFileSync(path.join(runDir, 'run.json'), JSON.stringify(runJson, null, 2));

    const reportLines = [
      `# Run ${runId} (${loopName} / ${projectSlug})`,
      '',
      `**Status:** ${runJson.status}`,
      '',
      '## Decisions',
      ...evalDecisions.map((d) => `- ${d}`),
      '',
      `## New proposals (${newProposals.length})`,
      ...newProposals.map((p) => `- ${p.id}: ${p.action_type} on ${p.target.page} (tier ${p.tier})`),
      '',
      `## Stale proposals (>=3 cycles undecided)`,
      ...(staleIds.length ? staleIds.map((id) => `- ${id}`) : ['- none']),
    ];
    fs.writeFileSync(path.join(runDir, 'report.md'), reportLines.join('\n') + '\n');

    fs.appendFileSync(
      memoryPath,
      `- ${nowIso()} run ${runId}: ${runJson.status}, ${newProposals.length} new proposal(s), ${runJson.proposals_evaluated.length} evaluated, ${staleIds.length} stale\n`
    );

    return { status: runJson.status, runId, runJson };
  } finally {
    releaseLock(loopDir, runId);
  }
}

const isMain = process.argv[1] && process.argv[1].replace(/\\/g, '/').endsWith('tools/run-loop.mjs');
if (isMain) {
  const [, , project, loop, ...rest] = process.argv;
  const scenarioIdx = rest.indexOf('--scenario');
  const scenario = scenarioIdx >= 0 ? rest[scenarioIdx + 1] : 'normal';
  if (!project || !loop) {
    console.error('usage: node tools/run-loop.mjs <project> <loop> [--scenario normal|breach|fail]');
    process.exit(2);
  }
  runLoop(project, loop, { scenario })
    .then((result) => {
      console.log(JSON.stringify(result, null, 2));
      process.exit(result.status === 'refused' || result.status === 'invalid-spec' ? 1 : 0);
    })
    .catch((err) => {
      console.error('run-loop failed unexpectedly:', err);
      process.exit(1);
    });
}

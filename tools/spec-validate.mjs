// Machine-checked schema for loop specs. Validation happens before a run
// is allowed to start — a spec that fails validation refuses to run.
import fs from 'node:fs';
import yaml from 'js-yaml';

export const SCHEMA_VERSION = 1;
const COMPARATORS = ['<', '>', '<=', '>=', '=='];
const APPROVAL_MODES = ['propose-only', 'tier1-enabled'];
const TIERS = [0, 1, 2];

function isNonEmptyString(v) {
  return typeof v === 'string' && v.trim().length > 0;
}
function isPositiveNumber(v) {
  return typeof v === 'number' && Number.isFinite(v) && v > 0;
}

/** Extract the `---\n...\n---` YAML frontmatter block from a spec.md body. */
export function extractFrontmatter(source) {
  const match = source.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
  if (!match) return null;
  return match[1];
}

function validateGuardrailMetric(m, idx, errors) {
  const p = `guardrail_metrics[${idx}]`;
  if (!m || typeof m !== 'object') return errors.push(`${p} must be an object`);
  if (!isNonEmptyString(m.name)) errors.push(`${p}.name is required (string)`);
  if (!COMPARATORS.includes(m.comparator)) errors.push(`${p}.comparator must be one of ${COMPARATORS.join(', ')}`);
  if (typeof m.threshold !== 'number') errors.push(`${p}.threshold must be a number`);
  if (m.consecutive_runs !== undefined && !isPositiveNumber(m.consecutive_runs)) {
    errors.push(`${p}.consecutive_runs must be a positive number if present`);
  }
}

function validateAllowedAction(a, idx, errors) {
  const p = `allowed_actions[${idx}]`;
  if (!a || typeof a !== 'object') return errors.push(`${p} must be an object`);
  if (!isNonEmptyString(a.type)) errors.push(`${p}.type is required (string)`);
  if (!TIERS.includes(a.tier)) errors.push(`${p}.tier must be one of ${TIERS.join(', ')}`);
  const hasRollback = isNonEmptyString(a.rollback);
  const manualOnly = a.manual_approval_only === true;
  if (!hasRollback && !manualOnly) {
    errors.push(`${p} must declare a non-empty rollback, or be marked manual_approval_only: true`);
  }
  if (!isPositiveNumber(a.observation_window_days)) errors.push(`${p}.observation_window_days must be a positive number`);
  if (!isPositiveNumber(a.min_sample_size)) errors.push(`${p}.min_sample_size must be a positive number`);
}

export function validateSpecObject(spec) {
  const errors = [];
  if (!spec || typeof spec !== 'object') return { valid: false, errors: ['spec frontmatter is empty or not a mapping'] };

  if (spec.version !== SCHEMA_VERSION) errors.push(`version must equal ${SCHEMA_VERSION} (got ${JSON.stringify(spec.version)})`);
  if (!isNonEmptyString(spec.loop)) errors.push('loop is required (string)');
  if (!isNonEmptyString(spec.objective)) errors.push('objective is required (string)');
  if (!isNonEmptyString(spec.primary_metric)) errors.push('primary_metric is required (string)');

  if (!Array.isArray(spec.guardrail_metrics) || spec.guardrail_metrics.length < 1) {
    errors.push('guardrail_metrics must be a non-empty array');
  } else {
    spec.guardrail_metrics.forEach((m, i) => validateGuardrailMetric(m, i, errors));
  }

  if (!spec.failure_threshold || typeof spec.failure_threshold !== 'object') {
    errors.push('failure_threshold is required (object)');
  } else {
    if (!isNonEmptyString(spec.failure_threshold.metric)) errors.push('failure_threshold.metric is required (string)');
    if (!COMPARATORS.includes(spec.failure_threshold.comparator)) {
      errors.push(`failure_threshold.comparator must be one of ${COMPARATORS.join(', ')}`);
    }
    if (typeof spec.failure_threshold.value !== 'number') errors.push('failure_threshold.value must be a number');
  }

  if (!Array.isArray(spec.inputs) || spec.inputs.length < 1 || !spec.inputs.every(isNonEmptyString)) {
    errors.push('inputs must be a non-empty array of connector alias strings');
  }

  if (!Array.isArray(spec.allowed_actions) || spec.allowed_actions.length < 1) {
    errors.push('allowed_actions must be a non-empty array');
  } else {
    spec.allowed_actions.forEach((a, i) => validateAllowedAction(a, i, errors));
  }

  if (!APPROVAL_MODES.includes(spec.approval_mode)) {
    errors.push(`approval_mode must be one of ${APPROVAL_MODES.join(', ')}`);
  }
  if (!isPositiveNumber(spec.max_run_duration_minutes)) errors.push('max_run_duration_minutes must be a positive number');
  if (!isNonEmptyString(spec.schedule)) errors.push('schedule is required (string, cron expression)');
  if (!isNonEmptyString(spec.stop_condition)) errors.push('stop_condition is required (string)');
  if (!isNonEmptyString(spec.memory)) errors.push('memory is required (string path)');

  if (spec.credential_aliases !== undefined) {
    if (typeof spec.credential_aliases !== 'object' || Array.isArray(spec.credential_aliases)) {
      errors.push('credential_aliases must be a mapping of connector -> opaque alias string');
    } else {
      for (const [k, v] of Object.entries(spec.credential_aliases)) {
        if (!isNonEmptyString(v)) errors.push(`credential_aliases.${k} must be a non-empty opaque alias string`);
      }
    }
  }

  return { valid: errors.length === 0, errors };
}

export function validateSpecFile(specPath) {
  if (!fs.existsSync(specPath)) return { valid: false, errors: [`spec file not found: ${specPath}`] };
  const source = fs.readFileSync(specPath, 'utf8');
  const fm = extractFrontmatter(source);
  if (fm === null) return { valid: false, errors: ['no YAML frontmatter block (--- ... ---) found at top of spec'] };
  let parsed;
  try {
    parsed = yaml.load(fm);
  } catch (err) {
    return { valid: false, errors: [`YAML parse error: ${err.message}`] };
  }
  return validateSpecObject(parsed);
}

async function selfTest() {
  const os = await import('node:os');
  const path = await import('node:path');
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'spec-test-'));

  const good = `---
version: 1
loop: seo
objective: Improve rankings for target keywords
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
  - gsc
  - dataforseo
allowed_actions:
  - type: title-tag-rewrite
    tier: 1
    rollback: revert PR
    observation_window_days: 14
    min_sample_size: 100
approval_mode: propose-only
max_run_duration_minutes: 30
schedule: "0 6 * * 1"
stop_condition: "manual stop via project.md"
memory: memory.md
credential_aliases:
  gsc: acme-gsc-readonly
---
# SEO loop spec
`;
  const bad = `---
version: 1
loop: seo
objective: Improve rankings
primary_metric: gsc_position
guardrail_metrics: []
approval_mode: yolo-mode
schedule: "0 6 * * 1"
---
# broken spec, missing required fields, bad enum, empty guardrails
`;

  fs.writeFileSync(path.join(tmp, 'good.md'), good);
  fs.writeFileSync(path.join(tmp, 'bad.md'), bad);

  const goodResult = validateSpecFile(path.join(tmp, 'good.md'));
  const badResult = validateSpecFile(path.join(tmp, 'bad.md'));

  const checks = [
    ['valid spec is accepted', goodResult.valid === true],
    ['valid spec has no errors', goodResult.errors.length === 0],
    ['invalid spec is rejected', badResult.valid === false],
    ['invalid spec reports approval_mode enum error', badResult.errors.some((e) => e.includes('approval_mode'))],
    ['invalid spec reports empty guardrail_metrics', badResult.errors.some((e) => e.includes('guardrail_metrics'))],
    ['invalid spec reports missing allowed_actions', badResult.errors.some((e) => e.includes('allowed_actions'))],
    ['missing file is rejected', validateSpecFile(path.join(tmp, 'missing.md')).valid === false],
  ];

  fs.rmSync(tmp, { recursive: true, force: true });

  let failed = 0;
  for (const [name, ok] of checks) {
    console.log(`${ok ? 'PASS' : 'FAIL'} - ${name}`);
    if (!ok) failed++;
  }
  process.exit(failed ? 1 : 0);
}

const isMain = process.argv[1] && process.argv[1].replace(/\\/g, '/').endsWith('tools/spec-validate.mjs');
if (isMain && process.argv.includes('--verify')) {
  selfTest();
} else if (isMain) {
  const target = process.argv[2];
  if (!target) {
    console.error('usage: node tools/spec-validate.mjs <spec.md> | --verify');
    process.exit(2);
  }
  const result = validateSpecFile(target);
  if (result.valid) {
    console.log(`VALID: ${target}`);
    process.exit(0);
  } else {
    console.error(`INVALID: ${target}`);
    for (const e of result.errors) console.error(`  - ${e}`);
    process.exit(1);
  }
}

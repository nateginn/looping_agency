// DataForSEO connector — read-only scope only.
//
// Phase 1 boundary: connector contract only, same rationale as gsc.mjs.
// No live API call, no real credentials this session.
import { redactDeep } from './lib/redact.mjs';

export const REQUIRED_SCOPE = 'read-only (SERP + keyword data endpoints)';

/**
 * @param {object} opts
 * @param {string} opts.credentialAlias - opaque alias, never a raw secret.
 * @param {(alias:string)=>Promise<string>} [opts.resolveCredential] - injected for tests only.
 */
export async function pullMetrics({ credentialAlias, resolveCredential } = {}) {
  if (!credentialAlias) throw new Error('dataforseo.mjs: credentialAlias is required');
  if (typeof resolveCredential !== 'function') {
    throw new Error(
      `dataforseo.mjs: no credential resolver configured for alias "${credentialAlias}". ` +
        'Live DataForSEO access is out of scope until the Phase 1(b) connectors-only smoke test ' +
        '(see AgentColabPlan.md Sequencing) — this connector refuses to run rather than guess.'
    );
  }
  await resolveCredential(credentialAlias);
  // Real implementation would call the DataForSEO SERP/keyword-data API here.
  // Not implemented in Phase 1.
  throw new Error('dataforseo.mjs: live API call not implemented in Phase 1 (connector contract only)');
}

async function selfTest() {
  const checks = [];

  let threwNoResolver = false;
  try {
    await pullMetrics({ credentialAlias: 'acme-dataforseo-read' });
  } catch (e) {
    threwNoResolver = /no credential resolver configured/.test(e.message);
  }
  checks.push(['refuses to run without an injected resolver', threwNoResolver]);

  let threwNoAlias = false;
  try {
    await pullMetrics({});
  } catch {
    threwNoAlias = true;
  }
  checks.push(['refuses to run without a credentialAlias', threwNoAlias]);

  const fakeSecret = 'sk-test-fake-dataforseo-token';
  let threwNotImplemented = false;
  let leaked = false;
  try {
    await pullMetrics({ credentialAlias: 'acme-dataforseo-read', resolveCredential: async () => fakeSecret });
  } catch (e) {
    threwNotImplemented = /not implemented/.test(e.message);
    leaked = e.message.includes(fakeSecret);
  }
  checks.push(['live call path is not implemented (no real API touched)', threwNotImplemented]);
  checks.push(['resolved secret never appears unredacted in a thrown error', !leaked]);

  const redactedSample = redactDeep({ token: fakeSecret }, { 'acme-dataforseo-read': fakeSecret });
  checks.push(['redaction hook available and functional for this connector', !redactedSample.token.includes(fakeSecret)]);

  let failed = 0;
  for (const [name, ok] of checks) {
    console.log(`${ok ? 'PASS' : 'FAIL'} - ${name}`);
    if (!ok) failed++;
  }
  process.exit(failed ? 1 : 0);
}

const isMain = process.argv[1] && process.argv[1].replace(/\\/g, '/').endsWith('tools/dataforseo.mjs');
if (isMain && process.argv.includes('--verify')) selfTest();

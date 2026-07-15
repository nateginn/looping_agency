// Google Search Console connector — read-only scope only.
//
// Phase 1 boundary: this file defines the connector *contract* only. It
// deliberately does not call the real GSC API and does not read real
// credentials in this session — wiring a live credential alias and doing
// the connectors-only smoke test (auth, quota, retry) is Sequencing
// Phase 1(b), explicitly deferred. Calling pullMetrics() without an
// injected resolver throws rather than silently no-op'ing.
import { redactDeep } from './lib/redact.mjs';

export const REQUIRED_SCOPE = 'https://www.googleapis.com/auth/webmasters.readonly';

/**
 * @param {object} opts
 * @param {string} opts.credentialAlias - opaque alias from project.md/spec, never a raw secret.
 * @param {(alias:string)=>Promise<string>} [opts.resolveCredential] - injected for tests only;
 *   in a live run this would read Windows Credential Manager. Omitting it is the safe default.
 */
export async function pullMetrics({ credentialAlias, resolveCredential } = {}) {
  if (!credentialAlias) throw new Error('gsc.mjs: credentialAlias is required');
  if (typeof resolveCredential !== 'function') {
    throw new Error(
      `gsc.mjs: no credential resolver configured for alias "${credentialAlias}". ` +
        'Live GSC access is out of scope until the Phase 1(b) connectors-only smoke test ' +
        '(see AgentColabPlan.md Sequencing) — this connector refuses to run rather than guess.'
    );
  }
  const secret = await resolveCredential(credentialAlias);
  // Real implementation would call the GSC Search Analytics API here with `secret`
  // as the bearer token, scoped to REQUIRED_SCOPE, and return position/click/impression
  // rows per (keyword, page). Not implemented in Phase 1.
  throw new Error('gsc.mjs: live API call not implemented in Phase 1 (connector contract only)');
}

async function selfTest() {
  const checks = [];

  let threwNoResolver = false;
  try {
    await pullMetrics({ credentialAlias: 'acme-gsc-readonly' });
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

  const fakeSecret = 'sk-test-fake-gsc-token';
  let threwNotImplemented = false;
  let leaked = false;
  try {
    await pullMetrics({ credentialAlias: 'acme-gsc-readonly', resolveCredential: async () => fakeSecret });
  } catch (e) {
    threwNotImplemented = /not implemented/.test(e.message);
    leaked = e.message.includes(fakeSecret);
  }
  checks.push(['live call path is not implemented (no real API touched)', threwNotImplemented]);
  checks.push(['resolved secret never appears unredacted in a thrown error', !leaked]);

  const redactedSample = redactDeep({ token: fakeSecret }, { 'acme-gsc-readonly': fakeSecret });
  checks.push(['redaction hook available and functional for this connector', !redactedSample.token.includes(fakeSecret)]);

  let failed = 0;
  for (const [name, ok] of checks) {
    console.log(`${ok ? 'PASS' : 'FAIL'} - ${name}`);
    if (!ok) failed++;
  }
  process.exit(failed ? 1 : 0);
}

const isMain = process.argv[1] && process.argv[1].replace(/\\/g, '/').endsWith('tools/gsc.mjs');
if (isMain && process.argv.includes('--verify')) selfTest();

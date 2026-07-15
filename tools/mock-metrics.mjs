// Mock GSC-like connector for projects/_demo. Never calls a real API —
// used only for the offline dry-run and Phase-1 test harness. Real
// connectors (gsc.mjs, dataforseo.mjs) are separate and untouched here.
//
// Modes (via --scenario):
//   normal   - stable/improving keyword positions
//   breach   - a tracked page drops >5 positions (guardrail breach)
//   fail     - throws, simulating a connector outage/auth failure
//
// Always returns a `_rawAuthHeader` field carrying a fake secret value so
// the redaction pipeline has something real to catch (Milestone-1 redaction test).

const FAKE_SECRET = 'sk-demo-FAKE1234567890ABCDEFDONOTUSE';

function keywordSet(scenario) {
  const base = [
    { keyword: 'best loop agency', page: '/blog/loop-agency', position: 8.2, clicks: 42, impressions: 900 },
    { keyword: 'seo automation tool', page: '/blog/seo-automation', position: 11.5, clicks: 18, impressions: 640 },
    { keyword: 'ai marketing loops', page: '/blog/ai-marketing', position: 6.1, clicks: 55, impressions: 1100 },
  ];
  if (scenario === 'breach') {
    // /blog/seo-automation regressed from ~11.5 to 19 -> a >5 position drop.
    base[1] = { ...base[1], position: 19.0, clicks: 4, impressions: 640 };
  }
  return base;
}

export async function pullMetrics({ scenario = 'normal', credentialAlias = 'demo-gsc-readonly' } = {}) {
  if (scenario === 'fail') {
    const err = new Error(`connector auth failed for alias ${credentialAlias}: token ${FAKE_SECRET} rejected (HTTP 401, simulated)`);
    err.rawSecrets = { [credentialAlias]: FAKE_SECRET };
    throw err;
  }

  const keywords = keywordSet(scenario);
  return {
    source: 'mock-metrics',
    scenario,
    pulled_at: new Date().toISOString(),
    credential_alias: credentialAlias,
    sample_size: keywords.reduce((sum, k) => sum + k.impressions, 0),
    keywords,
    // Present in every real pull so the redactor has a concrete target — never written unredacted.
    _rawAuthHeader: `Bearer ${FAKE_SECRET} (alias ${credentialAlias})`,
    secretMap: { [credentialAlias]: FAKE_SECRET },
  };
}

async function selfTest() {
  const checks = [];
  const normal = await pullMetrics({ scenario: 'normal' });
  checks.push(['normal scenario returns 3 keywords', normal.keywords.length === 3]);
  checks.push(['normal scenario carries a fake secret to redact', normal._rawAuthHeader.includes('sk-demo-FAKE')]);

  const breach = await pullMetrics({ scenario: 'breach' });
  const regressed = breach.keywords.find((k) => k.page === '/blog/seo-automation');
  checks.push(['breach scenario regresses tracked page by >5 positions', regressed.position - 11.5 > 5]);

  let threw = false;
  try {
    await pullMetrics({ scenario: 'fail' });
  } catch {
    threw = true;
  }
  checks.push(['fail scenario throws', threw]);

  let failed = 0;
  for (const [name, ok] of checks) {
    console.log(`${ok ? 'PASS' : 'FAIL'} - ${name}`);
    if (!ok) failed++;
  }
  process.exit(failed ? 1 : 0);
}

const isMain = process.argv[1] && process.argv[1].replace(/\\/g, '/').endsWith('tools/mock-metrics.mjs');
if (isMain && process.argv.includes('--verify')) selfTest();

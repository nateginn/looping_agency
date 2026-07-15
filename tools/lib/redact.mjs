// Redaction is owned by tools, not the model: every string that reaches
// disk (snapshot, run.json, report.md) must pass through here first.
// Secret map is { alias: rawValue }. Callers should hold rawValue only in
// memory for the duration of a connector call, never write it unredacted.

const GENERIC_PATTERNS = [
  /\bsk-[A-Za-z0-9_-]{10,}\b/g, // OpenAI-style API keys
  /\b[A-Za-z0-9_-]{32,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,}\b/g, // JWT-ish
];

export function redactText(text, secretMap = {}) {
  if (typeof text !== 'string') return text;
  let out = text;
  for (const [alias, value] of Object.entries(secretMap)) {
    if (!value) continue;
    out = out.split(value).join(`[REDACTED:${alias}]`);
  }
  for (const pattern of GENERIC_PATTERNS) {
    out = out.replace(pattern, '[REDACTED:pattern-match]');
  }
  return out;
}

/** Deep-redact every string value in an object/array in place (returns a new structure). */
export function redactDeep(value, secretMap = {}) {
  if (typeof value === 'string') return redactText(value, secretMap);
  if (Array.isArray(value)) return value.map((v) => redactDeep(v, secretMap));
  if (value && typeof value === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = redactDeep(v, secretMap);
    return out;
  }
  return value;
}

async function selfTest() {
  const secretMap = { 'demo-fake-secret': 'sk-live-DEMOFAKE1234567890ABCDEF' };
  const raw = `connector auth header: Bearer sk-live-DEMOFAKE1234567890ABCDEF (alias demo-fake-secret)`;
  const redacted = redactText(raw, secretMap);
  const deep = redactDeep(
    { note: raw, nested: { again: raw }, list: [raw] },
    secretMap
  );
  const checks = [
    ['flat string no longer contains raw secret', !redacted.includes('sk-live-DEMOFAKE1234567890ABCDEF')],
    ['flat string contains redaction marker', redacted.includes('[REDACTED:demo-fake-secret]')],
    ['deep.note redacted', !deep.note.includes('sk-live-DEMOFAKE1234567890ABCDEF')],
    ['deep.nested.again redacted', !deep.nested.again.includes('sk-live-DEMOFAKE1234567890ABCDEF')],
    ['deep.list[0] redacted', !deep.list[0].includes('sk-live-DEMOFAKE1234567890ABCDEF')],
  ];
  let failed = 0;
  for (const [name, ok] of checks) {
    console.log(`${ok ? 'PASS' : 'FAIL'} - ${name}`);
    if (!ok) failed++;
  }
  process.exit(failed ? 1 : 0);
}

if (process.argv[1] && process.argv[1].endsWith('redact.mjs') && process.argv.includes('--verify')) {
  selfTest();
}

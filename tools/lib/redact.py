# Redaction is owned by tools, not the model: every string that reaches
# disk (snapshot, run.json, report.md) must pass through here first.
# Secret map is { alias: raw_value }. Callers should hold raw_value only in
# memory for the duration of a connector call, never write it unredacted.
import re
import sys

GENERIC_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{10,}\b"),  # OpenAI-style API keys
    re.compile(r"\b[A-Za-z0-9_-]{32,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,}\b"),  # JWT-ish
]


def redact_text(text, secret_map=None):
    if not isinstance(text, str):
        return text
    secret_map = secret_map or {}
    out = text
    for alias, value in secret_map.items():
        if not value:
            continue
        out = out.replace(value, f"[REDACTED:{alias}]")
    for pattern in GENERIC_PATTERNS:
        out = pattern.sub("[REDACTED:pattern-match]", out)
    return out


def redact_deep(value, secret_map=None):
    """Deep-redact every string value in a dict/list in place (returns a new structure)."""
    secret_map = secret_map or {}
    if isinstance(value, str):
        return redact_text(value, secret_map)
    if isinstance(value, list):
        return [redact_deep(v, secret_map) for v in value]
    if isinstance(value, dict):
        return {k: redact_deep(v, secret_map) for k, v in value.items()}
    return value


def _self_test():
    secret_map = {"demo-fake-secret": "sk-live-DEMOFAKE1234567890ABCDEF"}
    raw = "connector auth header: Bearer sk-live-DEMOFAKE1234567890ABCDEF (alias demo-fake-secret)"
    redacted = redact_text(raw, secret_map)
    deep = redact_deep({"note": raw, "nested": {"again": raw}, "list": [raw]}, secret_map)

    checks = [
        ("flat string no longer contains raw secret", "sk-live-DEMOFAKE1234567890ABCDEF" not in redacted),
        ("flat string contains redaction marker", "[REDACTED:demo-fake-secret]" in redacted),
        ("deep.note redacted", "sk-live-DEMOFAKE1234567890ABCDEF" not in deep["note"]),
        ("deep.nested.again redacted", "sk-live-DEMOFAKE1234567890ABCDEF" not in deep["nested"]["again"]),
        ("deep.list[0] redacted", "sk-live-DEMOFAKE1234567890ABCDEF" not in deep["list"][0]),
    ]
    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__" and "--verify" in sys.argv:
    _self_test()

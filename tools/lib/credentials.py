# Credential resolver (AgentColabPlan.md "Credentials & tenant isolation").
# Windows Credential Manager (via keyring) is the default store; a per-project
# .env is an explicit fallback only, and reading it is gated behind a
# restrictive-ACL check - the resolver refuses an .env readable by any
# principal beyond the current user + SYSTEM/Administrators (Codex R2 #3).
# Repo files store opaque aliases only; a raw value exists in memory just
# long enough to hand to a connector, and never appears in any error message.
import getpass
import os
import re
import subprocess
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(THIS_DIR))
PROJECTS_ROOT = os.path.join(WORKSPACE_ROOT, "projects")

SERVICE_NAME = "loop-agency"

# Principals allowed on a fallback .env besides the current user. Compared
# case-insensitively against icacls output (English principal names; this
# workspace is single-machine - revisit if it ever runs on a localized OS).
ALLOWED_PRINCIPALS = {"nt authority\\system", "builtin\\administrators"}


class CredentialError(Exception):
    """Alias could not be resolved (or the fallback store was refused).
    Never carries a secret value."""


def env_key_for_alias(alias):
    """Alias -> .env key, e.g. "acme-gsc-readonly" -> "ACME_GSC_READONLY"
    (matches .env.example)."""
    return alias.upper().replace("-", "_")


def _icacls_principals(path):
    """List every principal named in the file's ACL, via icacls (stdlib
    subprocess only - no pywin32)."""
    abs_path = os.path.abspath(path)
    proc = subprocess.run(["icacls", abs_path], capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout:
        raise CredentialError(f"ACL check failed: icacls exited {proc.returncode} for {abs_path}")
    principals = []
    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        # First line is "<path> FIRST\PRINCIPAL:(perms)" - drop the path prefix.
        if line.lower().startswith(abs_path.lower()):
            line = line[len(abs_path):].strip()
        m = re.match(r"^(.+?):\(", line)
        if m:
            principals.append(m.group(1).strip())
    return principals


def _current_user_identities():
    """Fully qualified DOMAIN\\user forms of the current account, lowercased.
    ACL comparison must use the qualified name, never a bare-username suffix
    match - OTHERDOMAIN\\<same username> is a different account (Codex
    Steps-1-3 review, finding #4)."""
    identities = set()
    try:
        proc = subprocess.run(["whoami"], capture_output=True, text=True)
        if proc.returncode == 0 and proc.stdout.strip():
            identities.add(proc.stdout.strip().lower())
    except OSError:
        pass
    domain = os.environ.get("USERDOMAIN")
    user = os.environ.get("USERNAME") or getpass.getuser()
    if domain and user:
        identities.add(f"{domain}\\{user}".lower())
    return identities


def _audit_principals(principals, allowed_users=None):
    """Return the principals that should NOT have access (empty list = ACL ok).
    Unknown/unmatchable principals land in the disallowed list - the failure
    direction is always refusal, never a silent allow."""
    allowed_users = _current_user_identities() if allowed_users is None else allowed_users
    disallowed = []
    for principal in principals:
        p = principal.lower()
        if p in ALLOWED_PRINCIPALS:
            continue
        if p in allowed_users:
            continue
        disallowed.append(principal)
    return disallowed


def audit_env_acl(env_path):
    """Return the principals on the file's ACL that should NOT have access."""
    return _audit_principals(_icacls_principals(env_path))


def _read_env_value(env_path, key):
    with open(env_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                v = v.strip().strip('"').strip("'")
                return v or None
    return None


def _load_keyring():
    try:
        import keyring
    except ImportError:
        return None
    return keyring


def resolve_with_source(alias, project_dir=None, keyring_module=None):
    """Resolve an alias to (value, source) where source is "credential-manager"
    or ".env". keyring_module is injected by tests only; the default is the
    real keyring package (Windows Credential Manager backend).

    Order: Credential Manager first; then projects/<slug>/.env if project_dir
    is given - but an .env with an over-broad ACL is refused outright, never
    silently skipped."""
    if not isinstance(alias, str) or not alias.strip():
        raise CredentialError("credential alias must be a non-empty string")

    kr = keyring_module if keyring_module is not None else _load_keyring()
    if kr is not None:
        try:
            value = kr.get_password(SERVICE_NAME, alias)
        except Exception as e:
            raise CredentialError(f'keyring lookup failed for alias "{alias}": {e.__class__.__name__}') from None
        if value:
            return value, "credential-manager"

    env_path = os.path.join(project_dir, ".env") if project_dir else None
    if env_path and os.path.exists(env_path):
        disallowed = audit_env_acl(env_path)
        if disallowed:
            raise CredentialError(
                f"refusing to read {env_path}: ACL grants access beyond the current user "
                f"+ SYSTEM/Administrators ({', '.join(sorted(disallowed))}). Tighten it with e.g.: "
                f'icacls "{env_path}" /inheritance:r /grant:r "%USERNAME%:F"'
            )
        value = _read_env_value(env_path, env_key_for_alias(alias))
        if value:
            return value, ".env"

    looked_in = f'Windows Credential Manager (service "{SERVICE_NAME}")'
    if env_path:
        looked_in += f" or {env_path}"
    raise CredentialError(
        f'credential alias "{alias}" not found in {looked_in}. '
        f"Store it with: python tools/lib/credentials.py --store {alias}"
    )


def resolve_credential(alias, project_dir=None, keyring_module=None):
    """Resolve an alias to its raw secret value. See resolve_with_source."""
    return resolve_with_source(alias, project_dir=project_dir, keyring_module=keyring_module)[0]


def _cli_store(alias):
    kr = _load_keyring()
    if kr is None:
        print("keyring is not installed - run: ./.venv/Scripts/python.exe -m pip install -r requirements.txt", file=sys.stderr)
        return 1
    secret = getpass.getpass(f'Secret for alias "{alias}" (input hidden, stored in Windows Credential Manager): ')
    if not secret:
        print("empty input - nothing stored", file=sys.stderr)
        return 1
    kr.set_password(SERVICE_NAME, alias, secret)
    round_trip_ok = kr.get_password(SERVICE_NAME, alias) == secret
    if round_trip_ok:
        print(f'stored alias "{alias}" (service "{SERVICE_NAME}") - round-trip read verified, value not shown')
        return 0
    print(f'stored alias "{alias}" but round-trip read did not match - check the keyring backend', file=sys.stderr)
    return 1


def _cli_check(alias, project_slug=None):
    project_dir = os.path.join(PROJECTS_ROOT, project_slug) if project_slug else None
    try:
        value, source = resolve_with_source(alias, project_dir=project_dir)
    except CredentialError as e:
        print(f"NOT RESOLVED: {e}", file=sys.stderr)
        return 1
    print(f'alias "{alias}" resolved via {source} ({len(value)} chars, value not shown)')
    return 0


def _self_test():
    import shutil
    import tempfile

    class FakeKeyring:
        """In-memory stand-in so --verify never touches the real Credential Manager."""

        def __init__(self, store=None):
            self.store = dict(store or {})

        def get_password(self, service, alias):
            return self.store.get((service, alias))

        def set_password(self, service, alias, value):
            self.store[(service, alias)] = value

    checks = []
    tmp = tempfile.mkdtemp(prefix="cred-test-")
    current_user = os.environ.get("USERNAME") or getpass.getuser()

    checks.append(("alias -> .env key mapping matches .env.example convention", env_key_for_alias("acme-gsc-readonly") == "ACME_GSC_READONLY"))

    # 1) Credential Manager (fake backend) hit.
    kr = FakeKeyring({(SERVICE_NAME, "acme-gsc-readonly"): "keyring-secret-value"})
    value, source = resolve_with_source("acme-gsc-readonly", keyring_module=kr)
    checks.append(("keyring hit resolves with source credential-manager", value == "keyring-secret-value" and source == "credential-manager"))

    # 2) Keyring miss -> .env fallback with a restrictive ACL.
    good_dir = os.path.join(tmp, "good-project")
    os.makedirs(good_dir)
    good_env = os.path.join(good_dir, ".env")
    with open(good_env, "w", encoding="utf-8", newline="\n") as f:
        f.write("# comment line\nACME_GSC_READONLY=env-secret-value\n")
    locked = subprocess.run(
        ["icacls", good_env, "/inheritance:r", "/grant:r", f"{current_user}:F"],
        capture_output=True,
        text=True,
    )
    checks.append(("fixture: icacls can restrict the good .env to the current user", locked.returncode == 0))
    value, source = resolve_with_source("acme-gsc-readonly", project_dir=good_dir, keyring_module=FakeKeyring())
    checks.append(("keyring miss falls back to a restrictive-ACL .env", value == "env-secret-value" and source == ".env"))
    checks.append(("good .env passes the ACL audit", audit_env_acl(good_env) == []))

    # 3) Keyring value wins over .env when both exist.
    both = FakeKeyring({(SERVICE_NAME, "acme-gsc-readonly"): "keyring-secret-value"})
    value, source = resolve_with_source("acme-gsc-readonly", project_dir=good_dir, keyring_module=both)
    checks.append(("Credential Manager is checked before .env", source == "credential-manager"))

    # 4) Over-broad .env (Everyone granted read, SID *S-1-1-0) is refused, even
    #    though the key exists in the file - the ACL check runs before any read.
    bad_dir = os.path.join(tmp, "bad-project")
    os.makedirs(bad_dir)
    bad_env = os.path.join(bad_dir, ".env")
    with open(bad_env, "w", encoding="utf-8", newline="\n") as f:
        f.write("ACME_GSC_READONLY=env-secret-value\n")
    widened = subprocess.run(
        ["icacls", bad_env, "/inheritance:r", "/grant:r", f"{current_user}:F", "/grant", "*S-1-1-0:R"],
        capture_output=True,
        text=True,
    )
    checks.append(("fixture: icacls can widen the bad .env to Everyone", widened.returncode == 0))
    refused_message = ""
    try:
        resolve_with_source("acme-gsc-readonly", project_dir=bad_dir, keyring_module=FakeKeyring())
    except CredentialError as e:
        refused_message = str(e)
    checks.append(("over-broad .env is refused with an ACL error", "ACL grants access beyond" in refused_message))
    checks.append(("ACL refusal names the offending principal", "everyone" in refused_message.lower()))
    checks.append(("ACL refusal never contains the secret value", "env-secret-value" not in refused_message))

    # 4b) A same-username principal from a different domain/machine is NOT the
    #     current user - qualified-name comparison, never a basename match.
    same_basename_other_domain = _audit_principals([f"OTHERDOMAIN\\{current_user}"])
    checks.append(("same username under a different domain is rejected", same_basename_other_domain == [f"OTHERDOMAIN\\{current_user}"]))
    checks.append(("the current user's own qualified identity is known", len(_current_user_identities()) > 0))

    # 5) Not found anywhere -> error names the alias, never a value.
    missing_message = ""
    try:
        resolve_with_source("no-such-alias", project_dir=good_dir, keyring_module=FakeKeyring())
    except CredentialError as e:
        missing_message = str(e)
    checks.append(("unresolvable alias raises an error naming the alias", '"no-such-alias"' in missing_message))

    # 6) Blank alias is rejected.
    blank_rejected = False
    try:
        resolve_with_source("  ", keyring_module=FakeKeyring())
    except CredentialError:
        blank_rejected = True
    checks.append(("blank alias is rejected", blank_rejected))

    # Teardown: restore delete rights before rmtree (ACLs were restricted above).
    for env_file in (good_env, bad_env):
        subprocess.run(["icacls", env_file, "/grant", f"{current_user}:F"], capture_output=True, text=True)
    shutil.rmtree(tmp, ignore_errors=True)

    failed = 0
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} - {name}")
        if not ok:
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--verify" in args:
        _self_test()
    elif "--store" in args:
        idx = args.index("--store")
        alias = args[idx + 1] if idx + 1 < len(args) else None
        if not alias:
            print("usage: python tools/lib/credentials.py --store <alias>", file=sys.stderr)
            sys.exit(2)
        sys.exit(_cli_store(alias))
    elif "--check" in args:
        idx = args.index("--check")
        alias = args[idx + 1] if idx + 1 < len(args) else None
        project = None
        if "--project" in args:
            pidx = args.index("--project")
            project = args[pidx + 1] if pidx + 1 < len(args) else None
        if not alias:
            print("usage: python tools/lib/credentials.py --check <alias> [--project <slug>]", file=sys.stderr)
            sys.exit(2)
        sys.exit(_cli_check(alias, project))
    else:
        print(
            "usage: python tools/lib/credentials.py --store <alias> | --check <alias> [--project <slug>] | --verify",
            file=sys.stderr,
        )
        sys.exit(2)

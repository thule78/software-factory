#!/usr/bin/env python3
"""PreToolUse(Bash): block `git add` / `git commit` that would stage secret files.

Checks, in the hook's cwd:
- each `git add ...` segment, via `git add --dry-run <same args>`
- `git commit`: files already staged; with -a/--all also tracked modified files

Limitation: a `cd other/dir && git add .` chain is checked against the session cwd,
not other/dir. Blocks the whole command with exit 2 and a reason on stderr.
"""

import fnmatch
import os
import re
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from factory_config import read_hook_input  # noqa: E402

SECRET_PATTERNS = [
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore", "*.jks",
    "id_rsa*", "id_ed25519*", "secrets.json", "secrets.y*ml", "credentials.json",
    "service-account*.json", "*.tfstate", ".npmrc", ".pypirc", "creds.md",
]
ALLOWED = [".env.example", ".env.sample", ".env.template", "*.pub"]


def is_secret(path):
    name = os.path.basename(path.strip().strip('"'))
    if any(fnmatch.fnmatch(name, a) for a in ALLOWED):
        return False
    return any(fnmatch.fnmatch(name, p) for p in SECRET_PATTERNS)


def git(args, cwd):
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return out.stdout.splitlines() if out.returncode == 0 else []


def segments(command):
    """Split a shell command line on && || ; | and newlines; tokenise each piece."""
    for piece in re.split(r"&&|\|\||;|\||\n", command):
        try:
            tokens = shlex.split(piece)
        except ValueError:
            continue
        # skip leading env assignments like FOO=1 git ...
        while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
            tokens.pop(0)
        if len(tokens) >= 2 and tokens[0] == "git":
            yield tokens


data = read_hook_input()
command = (data.get("tool_input") or {}).get("command") or ""
if "git" not in command:
    sys.exit(0)
cwd = data.get("cwd") or os.getcwd()

candidates = set()
for tokens in segments(command):
    # drop global options such as `git -C dir` / `git -c k=v`
    i = 1
    while i < len(tokens) and tokens[i].startswith("-"):
        i += 2 if tokens[i] in ("-C", "-c") else 1
    if i >= len(tokens):
        continue
    sub, args = tokens[i], tokens[i + 1:]
    if sub == "add":
        for line in git(["add", "--dry-run", *args], cwd):
            m = re.match(r"^add '(.+)'$", line)
            if m:
                candidates.add(m.group(1))
    elif sub == "commit":
        candidates.update(git(["diff", "--cached", "--name-only"], cwd))
        if any(a in ("-a", "--all") or (a.startswith("-") and not a.startswith("--") and "a" in a)
               for a in args):
            candidates.update(git(["diff", "--name-only"], cwd))

hits = sorted(p for p in candidates if is_secret(p))
if hits:
    print(
        "BLOCKED by software-factory: this would stage or commit secret files:\n  "
        + "\n  ".join(hits)
        + "\nUnstage them (git restore --staged <file>) and add them to .gitignore. "
          "Do not rename or move the files to get around this check.",
        file=sys.stderr,
    )
    sys.exit(2)
sys.exit(0)

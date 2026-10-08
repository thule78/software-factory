#!/usr/bin/env python3
"""PreToolUse(Bash): keep factory agents' shell commands inside their lane.

path_scope.py only sees Edit/Write; this hook closes the Bash side door.

All factory agents (backend-builder, frontend-builder, validator):
- no `sudo`, no `curl|wget ... | sh`, no `bash -c` / `sh -c` / `eval` (unparseable)
- no DROP TABLE / DROP DATABASE / DROP SCHEMA / TRUNCATE TABLE
- git: no push, merge, rebase, switch, checkout, reset --hard, clean, worktree,
  branch delete/rename, tag

Builders: every path a command writes (redirections, tee, sed -i, cp, mv, rm, touch,
mkdir, ...) must be inside the layer's `paths`, `.factory/tickets/`, or the temp dir.
Validator: read-only. Any file-writing command, git write, or package install is denied.

Any other agent or the main session: not affected.
Limitation: writes made by an interpreter (`python -c`, `node -e`, scripts) are not seen.
"""

import os
import re
import shlex
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from factory_config import (  # noqa: E402
    BUILDER_ROLES, agent_role, deny_pretool, git_toplevel, load_layers, read_hook_input,
)

SEPARATORS = {"&&", "||", ";", "|", "&", "(", ")", ";;"}
GLOB_CHARS = set("*?[")

# git subcommands no factory agent may run (the orchestrator does branches and merges)
GIT_DENY = {"push", "merge", "rebase", "switch", "checkout", "clean", "worktree", "tag",
            "filter-branch", "update-ref", "reflog"}
# validator may only run these
GIT_READ = {"status", "diff", "log", "show", "blame", "rev-parse", "ls-files", "grep",
            "cat-file", "merge-base", "describe", "shortlog", "branch", "rev-list", "ls-tree"}

# command -> how to find the paths it writes
WRITE_ALL_ARGS = {"rm", "rmdir", "touch", "mkdir", "truncate", "unlink", "shred"}
WRITE_SKIP_FIRST = {"chmod", "chown", "chgrp"}
WRITE_LAST_ARG = {"cp", "install", "ln", "rsync"}
WRAPPERS = {"env", "command", "nohup", "nice", "exec", "time"}
PACKAGE_INSTALL = re.compile(
    r"^(npm|pnpm|yarn|bun)\s+(i|install|add|remove|rm|uninstall|ci)\b"
    r"|^(pip3?|uv\s+pip)\s+(install|uninstall)\b|^(uv|poetry)\s+(add|remove|sync)\b"
)
PIPE_TO_SHELL = re.compile(r"\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b")
DESTRUCTIVE_SQL = re.compile(r"\b(drop\s+(table|database|schema)|truncate\s+table)\b", re.I)


def tokenize(command):
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:
        return None


def split_segments(tokens):
    seg = []
    for tok in tokens:
        if tok in SEPARATORS:
            if seg:
                yield seg
            seg = []
        else:
            seg.append(tok)
    if seg:
        yield seg


def strip_redirections(seg):
    """Return (args, redirect_targets). `2>&1` style fd duplication is not a target."""
    args, targets, i = [], [], 0
    while i < len(seg):
        tok = seg[i]
        if set(tok) <= set("<>&|") and ">" in tok:
            if args and args[-1].isdigit():
                args.pop()
            if i + 1 < len(seg):
                target = seg[i + 1]
                if not (target.isdigit() or target == "-"):
                    targets.append(target)
            i += 2
            continue
        if set(tok) <= set("<") and tok:
            i += 2
            continue
        args.append(tok)
        i += 1
    return args, targets


def strip_wrappers(args):
    """Drop leading `FOO=1`, `env`, `command`, `nohup`, `timeout 60` ... to reach the real command."""
    while args:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", args[0]) or args[0] in WRAPPERS:
            args = args[1:]
        elif args[0] == "timeout":
            args = args[2:]
        else:
            break
    return args


def strip_heredocs(command):
    """Remove heredoc bodies so their text is not parsed as commands."""
    out, end = [], None
    for line in command.splitlines():
        if end is not None:
            if line.strip() == end:
                end = None
            continue
        out.append(line)
        m = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", line)
        if m:
            end = m.group(1)
    return "\n".join(out)


def positional(args):
    return [a for a in args if a and not a.startswith("-")]


def in_place_targets(rest):
    """Files edited by `sed -i` / `perl -pi`: positionals minus the script."""
    files, script_given, skip = [], False, False
    for a in rest:
        if skip:
            skip = False
            continue
        if a in ("-e", "-f", "--expression", "--file"):
            script_given = skip = True
        elif a and not a.startswith("-"):
            files.append(a)
    return files if script_given else files[1:]


def written_paths(args):
    """Paths a single (redirection-free) command writes, or None if it writes nothing we track."""
    cmd, rest = os.path.basename(args[0]), args[1:]
    pos = positional(rest)
    if cmd in WRITE_ALL_ARGS or cmd == "mv":
        return pos
    if cmd in WRITE_SKIP_FIRST:
        return pos[1:]
    if cmd in WRITE_LAST_ARG:
        return pos[-1:]
    if cmd == "tee":
        return pos
    if cmd == "dd":
        return [a[3:] for a in rest if a.startswith("of=")]
    if cmd in ("sed", "perl") and any(re.match(r"^(-[a-zA-Z]*i|--in-place)", a) for a in rest):
        return in_place_targets(rest)
    if cmd == "find" and any(a in ("-delete", "-exec", "-execdir") for a in rest):
        roots = []
        for a in rest:
            if a.startswith("-") or a in ("(", "!"):
                break
            roots.append(a)
        return roots or ["."]
    return None


class Guard:
    def __init__(self, role, cwd):
        self.role = role
        self.layer = BUILDER_ROLES.get(role)
        self.cwd = cwd
        self.tmp = {os.path.realpath(p) for p in (tempfile.gettempdir(), "/tmp", "/var/tmp")}

    def deny(self, reason):
        deny_pretool(f"{self.role}: {reason}")

    def resolve(self, path):
        path = os.path.expandvars(os.path.expanduser(path))
        if "$" in path or "`" in path:
            self.deny(f"cannot check write target {path!r}. Use a literal path.")
        parts = path.split(os.sep)
        for n, part in enumerate(parts):
            if GLOB_CHARS & set(part):
                path = os.sep.join(parts[:n]) or "."
                break
        if not os.path.isabs(path):
            path = os.path.join(self.cwd, path)
        return os.path.realpath(path)

    def check_write(self, raw):
        if raw.startswith("/dev/"):
            return
        target = self.resolve(raw)
        root = git_toplevel(target)
        if not root:
            if any(target == t or target.startswith(t + os.sep) for t in self.tmp):
                return
            self.deny(f"{raw} is outside the repo. Only the temp dir and your layer paths are writable.")
        if self.role == "validator":
            self.deny(f"read-only: this command writes {raw}. Report the finding instead of fixing it.")
        if f"{os.sep}.factory{os.sep}tickets{os.sep}" in target + os.sep:
            return
        root = os.path.realpath(root)
        cfg = load_layers(root).get(self.layer)
        if not cfg:
            self.deny(f"the {self.layer} layer is not configured in {root}/CLAUDE.md. "
                      "Stop and report STATUS: BLOCKED.")
        rel = os.path.relpath(target, root)
        if any(rel == p or rel.startswith(p + os.sep) for p in cfg["paths"]):
            return
        self.deny(f"{rel} is outside the {self.layer} paths ({', '.join(cfg['paths'])}). "
                  f"Do not work around this. If the ticket needs it, stop and report OUT_OF_SCOPE: {rel}.")

    def check_git(self, args):
        i = 1
        while i < len(args) and args[i].startswith("-"):
            i += 2 if args[i] in ("-C", "-c") else 1
        if i >= len(args):
            return
        sub, rest = args[i], args[i + 1:]
        if self.role == "validator":
            if sub not in GIT_READ or (sub == "branch" and positional(rest)) \
                    or (sub == "branch" and any(a in ("-d", "-D", "-m", "-M", "-c", "-C") for a in rest)):
                self.deny(f"read-only: `git {sub}` changes the repo. Use status/diff/log/show only.")
            return
        if sub in GIT_DENY:
            self.deny(f"`git {sub}` is the orchestrator's job. Commit on your branch and report back.")
        if sub == "reset" and "--hard" in rest:
            self.deny("`git reset --hard` discards work. Fix forward or report STATUS: BLOCKED.")
        if sub == "branch" and any(a in ("-d", "-D", "-m", "-M", "--delete", "--move") for a in rest):
            self.deny("deleting or renaming branches is the orchestrator's job.")

    def check_segment(self, seg):
        args, redirects = strip_redirections(seg)
        args = strip_wrappers(args)
        for target in redirects:
            self.check_write(target)
        if not args:
            return
        cmd = os.path.basename(args[0])

        if cmd in ("cd", "pushd"):
            dest = args[1] if len(args) > 1 else os.path.expanduser("~")
            self.cwd = os.path.realpath(os.path.join(self.cwd, os.path.expanduser(dest)))
            return
        if cmd == "sudo":
            self.deny("sudo is not allowed.")
        if cmd == "eval" or (cmd in ("sh", "bash", "zsh", "dash") and "-c" in args):
            self.deny(f"`{cmd} -c` / eval hides the real command. Run it directly.")
        if cmd == "xargs" and any(os.path.basename(a) in WRITE_ALL_ARGS | {"mv", "cp"} for a in args[1:]):
            self.deny("`xargs` with a file-writing command can't be checked. Use explicit paths.")
        if cmd == "git":
            self.check_git(args)
            return
        if DESTRUCTIVE_SQL.search(" ".join(args)):
            self.deny("destructive SQL (DROP/TRUNCATE) is not allowed from the shell. "
                      "Put schema changes in a migration file.")
        if self.role == "validator" and PACKAGE_INSTALL.match(" ".join(args)):
            self.deny("read-only: installing or removing packages changes the worktree.")

        for p in written_paths(args) or []:
            self.check_write(p)

    def check(self, command):
        if PIPE_TO_SHELL.search(command):
            self.deny("piping a download into a shell is not allowed.")
        text = strip_heredocs(command).replace("\\\n", " ").replace("\n", " ; ")
        tokens = tokenize(text)
        if tokens is None:
            self.deny("could not parse this command (unbalanced quotes?). Simplify it.")
        for seg in split_segments(tokens):
            self.check_segment(seg)


data = read_hook_input()
role = agent_role(data)
if role != "validator" and role not in BUILDER_ROLES:
    sys.exit(0)

command = (data.get("tool_input") or {}).get("command") or ""
Guard(role, os.path.realpath(data.get("cwd") or os.getcwd())).check(command)
sys.exit(0)

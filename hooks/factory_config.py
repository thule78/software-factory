"""Shared helpers for software-factory hooks.

Reads the `## Backend` / `## Frontend` layer sections from a repo's CLAUDE.md:

    ## Backend
    - paths: src/api, tests/backend
    - test: pytest -q
    - typecheck: mypy src

A missing section, or an empty `paths:`, means the layer is off.
Values still holding a template placeholder (`<...>`) count as empty.
"""

import json
import os
import re
import subprocess
import sys
import time

LAYERS = ("backend", "frontend")
BUILDER_ROLES = {"backend-builder": "backend", "frontend-builder": "frontend"}
FACTORY_ROLES = {"validator", *BUILDER_ROLES}
AUDIT_SUMMARY_CHARS = 300

_hook_input = {}


def read_hook_input():
    global _hook_input
    try:
        _hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        _hook_input = {}
    return _hook_input


def agent_role(hook_input):
    """Plugin agents arrive as `software-factory:backend-builder`; take the last segment."""
    agent_type = hook_input.get("agent_type") or ""
    return agent_type.split(":")[-1] or None


def _clean(value):
    value = value.strip()
    return "" if (not value or "<" in value) else value


def load_layers(repo_root):
    """Return {"backend": {"paths": [...], "test": str, "typecheck": str}, ...}; absent layers omitted."""
    path = os.path.join(repo_root, "CLAUDE.md")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return {}
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)

    layers = {}
    current = None
    for line in text.splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            name = heading.group(1).strip().lower()
            current = name if name in LAYERS else None
            if current:
                layers[current] = {"paths": [], "test": "", "typecheck": ""}
            continue
        if not current:
            continue
        item = re.match(r"^\s*-\s*(paths|test|typecheck)\s*:\s*(.*)$", line)
        if not item:
            continue
        key, value = item.group(1), item.group(2)
        if key == "paths":
            layers[current]["paths"] = [
                p.strip().strip("/") for p in value.split(",") if _clean(p)
            ]
        else:
            layers[current][key] = _clean(value)

    return {name: cfg for name, cfg in layers.items() if cfg["paths"]}


def git_toplevel(start):
    """Repo (or worktree) root containing `start`, walking up to an existing directory first."""
    d = start if os.path.isdir(start) else os.path.dirname(start)
    while d and not os.path.isdir(d):
        d = os.path.dirname(d)
    if not d:
        return None
    try:
        out = subprocess.run(
            ["git", "-C", d, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def _summary(tool_input):
    for key in ("command", "file_path", "notebook_path", "pattern", "url", "prompt"):
        if tool_input.get(key):
            text = str(tool_input[key])
            if key == "pattern" and tool_input.get("path"):
                text += f"  in {tool_input['path']}"
            break
    else:
        text = json.dumps(tool_input, sort_keys=True)
    text = " ".join(text.split())
    return text if len(text) <= AUDIT_SUMMARY_CHARS else text[:AUDIT_SUMMARY_CHARS] + "..."


def _audit_target(hook_input):
    """(repo_root, ticket) for a factory agent's call, or None. Ticket comes from a worktree path."""
    tool_input = hook_input.get("tool_input") or {}
    blob = " ".join(str(v) for v in (
        hook_input.get("cwd"), tool_input.get("file_path"), tool_input.get("notebook_path"),
        tool_input.get("path"), tool_input.get("command"),
    ) if v)
    m = re.search(r"(/\S*?)/\.factory/worktrees/([A-Za-z0-9_-]+)", blob)
    if m:
        return m.group(1), m.group(2).lstrip("_")
    cwd = hook_input.get("cwd")
    if not cwd:
        return None
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return os.path.dirname(out.stdout.strip()), "unknown"


def audit(hook_input, decision, reason=""):
    """Append one JSON line per factory-agent tool call to <repo>/.factory/audit/<ticket>.jsonl."""
    role = agent_role(hook_input)
    if role not in FACTORY_ROLES:
        return
    try:
        target = _audit_target(hook_input)
        if not target:
            return
        repo, ticket = target
        folder = os.path.join(repo, ".factory", "audit")
        if not os.path.isdir(os.path.join(repo, ".factory")):
            return
        os.makedirs(folder, exist_ok=True)
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "ticket": ticket,
            "role": role,
            "agent_id": hook_input.get("agent_id"),
            "tool": hook_input.get("tool_name"),
            "decision": decision,
            "input": _summary(hook_input.get("tool_input") or {}),
        }
        if reason:
            entry["reason"] = " ".join(reason.split())[:AUDIT_SUMMARY_CHARS]
        with open(os.path.join(folder, f"{ticket}.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def deny_pretool(reason):
    audit(_hook_input, "deny", reason)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)

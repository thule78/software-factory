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

LAYERS = ("backend", "frontend")
BUILDER_ROLES = {"backend-builder": "backend", "frontend-builder": "frontend"}


def read_hook_input():
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return {}


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


def deny_pretool(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)

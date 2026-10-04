#!/usr/bin/env python3
"""PreToolUse(Edit|Write|MultiEdit|NotebookEdit): keep each factory agent inside its lane.

- backend-builder / frontend-builder: may only write inside their layer's `paths`
  from CLAUDE.md, plus the factory's own ticket folder (`.factory/tickets/`).
- validator: may not write files at all.
- Any other agent or the main session: not affected.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from factory_config import (  # noqa: E402
    BUILDER_ROLES, agent_role, deny_pretool, git_toplevel, load_layers, read_hook_input,
)

data = read_hook_input()
role = agent_role(data)

if role == "validator":
    deny_pretool("validator is read-only. Report the finding instead of fixing it.")

layer = BUILDER_ROLES.get(role)
if not layer:
    sys.exit(0)

tool_input = data.get("tool_input") or {}
target = tool_input.get("file_path") or tool_input.get("notebook_path")
if not target:
    sys.exit(0)
if not os.path.isabs(target):
    target = os.path.join(data.get("cwd") or os.getcwd(), target)
target = os.path.realpath(target)

if f"{os.sep}.factory{os.sep}tickets{os.sep}" in target:
    sys.exit(0)

root = git_toplevel(target)
if not root:
    deny_pretool(f"{role}: {target} is not inside a git repo or worktree.")
root = os.path.realpath(root)

layers = load_layers(root)
cfg = layers.get(layer)
if not cfg:
    deny_pretool(
        f"{role}: the {layer} layer is not configured in {root}/CLAUDE.md "
        f"(no '## {layer.capitalize()}' section with '- paths:'). Stop and report STATUS: BLOCKED."
    )

rel = os.path.relpath(target, root)
for allowed in cfg["paths"]:
    if rel == allowed or rel.startswith(allowed + os.sep):
        sys.exit(0)

deny_pretool(
    f"{role}: {rel} is outside the {layer} paths ({', '.join(cfg['paths'])}). "
    f"Do not work around this. If the ticket needs it, stop and report OUT_OF_SCOPE: {rel}."
)

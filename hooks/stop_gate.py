#!/usr/bin/env python3
"""SubagentStop: a builder may not finish while its layer's tests or typecheck fail.

- Runs only for backend-builder / frontend-builder.
- Runs in the worktree named by a `WORKTREE: <path>` line in the builder's last
  message, falling back to the hook's cwd.
- Skips the gate when the builder is deliberately stopping early
  (STATUS: BLOCKED, CONTRACT_GAP, OUT_OF_SCOPE).
- Blocks at most MAX_BLOCKS times per agent, then lets it stop so a stuck builder
  cannot loop forever; the validator re-runs tests anyway.
"""

import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from factory_config import BUILDER_ROLES, agent_role, git_toplevel, load_layers, read_hook_input  # noqa: E402

MAX_BLOCKS = 3
TIMEOUT_S = 600
TAIL_CHARS = 3000

data = read_hook_input()
role = agent_role(data)
layer = BUILDER_ROLES.get(role)
if not layer:
    sys.exit(0)

message = data.get("last_assistant_message") or ""
if re.search(r"STATUS:\s*BLOCKED|CONTRACT_GAP|OUT_OF_SCOPE", message):
    sys.exit(0)

m = re.search(r"^\s*WORKTREE:\s*(\S+)\s*$", message, flags=re.MULTILINE)
start = m.group(1) if m else (data.get("cwd") or os.getcwd())
root = git_toplevel(start)
if not root:
    sys.exit(0)

cfg = load_layers(root).get(layer)
if not cfg:
    sys.exit(0)

failures = []
for kind in ("typecheck", "test"):
    cmd = cfg.get(kind)
    if not cmd:
        continue
    try:
        run = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True, timeout=TIMEOUT_S)
        if run.returncode != 0:
            failures.append(f"$ {cmd}  (exit {run.returncode})\n{(run.stdout + run.stderr)[-TAIL_CHARS:]}")
    except subprocess.TimeoutExpired:
        failures.append(f"$ {cmd}  timed out after {TIMEOUT_S}s")

if not failures:
    sys.exit(0)

agent_id = re.sub(r"[^A-Za-z0-9_-]", "_", data.get("agent_id") or "unknown")
counter = os.path.join(tempfile.gettempdir(), f"software-factory-stopgate-{agent_id}")
try:
    with open(counter, encoding="utf-8") as f:
        blocks = int(f.read().strip() or 0)
except (OSError, ValueError):
    blocks = 0

if blocks >= MAX_BLOCKS:
    sys.exit(0)
with open(counter, "w", encoding="utf-8") as f:
    f.write(str(blocks + 1))

print(json.dumps({
    "decision": "block",
    "reason": (
        f"software-factory stop gate ({blocks + 1}/{MAX_BLOCKS}): {layer} checks fail in {root}. "
        "Fix them before finishing, or report STATUS: BLOCKED with the reason.\n\n"
        + "\n\n".join(failures)
    ),
}))
sys.exit(0)

#!/usr/bin/env python3
"""PreToolUse(*): stop a factory agent that is going in circles, before it burns the budget.

Per agent (backend-builder, frontend-builder, validator), keyed by agent_id:
- no progress: the same call (tool + input) made REPEAT_LIMIT times with no new edit or
  new shell command in between is denied the next time.
- flip-flop: the same Edit/Write applied FLIPFLOP_LIMIT times is denied the next time.
- budget: after MAX_CALLS tool calls every further call is denied.

A denial tells the agent to stop and report STATUS: BLOCKED, which also skips stop_gate.
Any other agent or the main session: not affected.
"""

import hashlib
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from factory_config import BUILDER_ROLES, agent_role, deny_pretool, read_hook_input  # noqa: E402

REPEAT_LIMIT = 3
FLIPFLOP_LIMIT = 2
MAX_CALLS = int(os.environ.get("FACTORY_MAX_TOOL_CALLS", "200"))
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# a new call of one of these may have changed something, so earlier repeats are not "stuck"
PROGRESS_TOOLS = EDIT_TOOLS | {"Bash"}


def state_path(data):
    key = data.get("agent_id") or f"{data.get('session_id', 'unknown')}-{data.get('agent_type', '')}"
    key = re.sub(r"[^A-Za-z0-9_-]", "_", key)
    return os.path.join(tempfile.gettempdir(), f"software-factory-loop-{key}.json")


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"calls": 0, "since_progress": {}, "edits": {}}


def save(path, state):
    tmp = f"{path}.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def stuck(role, why):
    deny_pretool(
        f"{role}: STUCK, {why} Stop now and report STATUS: BLOCKED with what you tried "
        "and what is still failing. The orchestrator will decide the next step."
    )


data = read_hook_input()
role = agent_role(data)
if role != "validator" and role not in BUILDER_ROLES:
    sys.exit(0)

tool = data.get("tool_name") or ""
call = hashlib.sha256(
    json.dumps([tool, data.get("tool_input") or {}], sort_keys=True).encode()
).hexdigest()[:16]

path = state_path(data)
state = load(path)
state["calls"] += 1
seen = state["since_progress"]

if state["calls"] > MAX_CALLS:
    save(path, state)
    stuck(role, f"tool-call budget of {MAX_CALLS} used up.")

if tool in EDIT_TOOLS:
    count = state["edits"].get(call, 0)
    if count >= FLIPFLOP_LIMIT:
        save(path, state)
        stuck(role, f"this exact {tool} was already applied {count} times (edit/revert loop).")
    state["edits"][call] = count + 1

if tool in PROGRESS_TOOLS and call not in seen:
    seen.clear()
count = seen.get(call, 0)
if count >= REPEAT_LIMIT:
    save(path, state)
    stuck(role, f"identical {tool} call repeated {count} times with nothing changed in between.")
seen[call] = count + 1

save(path, state)
sys.exit(0)

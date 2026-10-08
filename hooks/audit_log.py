#!/usr/bin/env python3
"""PostToolUse(*): log every factory-agent tool call to <repo>/.factory/audit/<ticket>.jsonl.

One JSON line per call: ts, ticket, role, agent_id, tool, decision (ok / error / deny),
input summary. Denied calls never reach PostToolUse; the deny helpers in factory_config
log those. Only written when <repo>/.factory/ exists, i.e. during a factory run.
Any other agent or the main session: not logged.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from factory_config import audit, read_hook_input  # noqa: E402

data = read_hook_input()
response = data.get("tool_response")
failed = isinstance(response, dict) and bool(response.get("is_error") or response.get("error"))
audit(data, "error" if failed else "ok")
sys.exit(0)

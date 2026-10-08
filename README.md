# software-factory

A Claude Code plugin that runs a feature from idea to PR:

```
/factory "idea"
  0 preflight   repo has issue tracker config + CLAUDE.md layer sections
  1 grill       grilling + domain-modeling (= /grill-with-docs)   → GATE
  2 PRD         to-prd                                            → GATE
  3 tickets     to-issues, each tagged backend / frontend / both  → GATE
  4 build loop  per ticket, max 2 in parallel:
                  backend-builder → contract.md → frontend-builder → validator
                  FAIL → fix round (max 2) → stuck → you decide
                  PASS → merge into factory/<slug> → full tests
  5 review      code-review on the whole branch                   → GATE → PR
```

Planning skills (`grilling`, `domain-modeling`, `to-prd`, `to-issues`, `tdd`, `code-review`) are **called, not copied**. They live in `~/.claude/skills/`; improve them there and the factory picks it up.

## What's inside

| Path | What |
|---|---|
| `skills/factory/SKILL.md` | Orchestrator: stages, gates, state file |
| `skills/factory/build-loop.md` | Worktrees, builder → validator chain, retries, merge |
| `agents/backend-builder.md` | Sonnet. Non-UI code, test-first, writes the contract |
| `agents/frontend-builder.md` | Sonnet. UI code, test-first, uses only the contract |
| `agents/validator.md` | Opus. Read-only check against acceptance criteria + contract |
| `hooks/block_secrets.py` | Blocks `git add`/`git commit` of `.env`, keys, credentials (exit 2) |
| `hooks/path_scope.py` | Builders can only write inside their layer's paths; validator can't write |
| `hooks/guard_bash.py` | Same lanes for shell commands (redirects, `sed -i`, `cp`/`mv`/`rm`...), plus no push/merge/reset --hard/sudo/`curl \| sh`/DROP TABLE for factory agents |
| `hooks/loop_breaker.py` | Stops a factory agent going in circles: same call 4× with no change between, same edit 3×, or over 200 tool calls (`FACTORY_MAX_TOOL_CALLS`) → told to report BLOCKED |
| `hooks/stop_gate.py` | A builder can't finish while its layer's tests/typecheck fail (max 3 bounces) |
| `templates/CLAUDE.md.template` | Project knowledge + the `## Backend` / `## Frontend` layer sections |
| `templates/contract.md.template` | Backend → frontend API contract |

## Per-project setup

1. `/setup-matt-pocock-skills`: issue tracker (GitHub or local `.scratch/`), labels, domain docs.
2. Add the layer sections to the project's `CLAUDE.md` (stage 0 offers to do it with you):
   ```markdown
   ## Backend
   - paths: src/api, src/services, tests/backend
   - test: pytest -q
   - typecheck: mypy src

   ## Frontend
   - paths: src/app, src/components, tests/frontend
   - test: npm test -- --run
   - typecheck: npx tsc --noEmit
   ```
   No frontend? Delete that section. backend-builder then covers all the code and frontend-builder never runs.
3. Start: `/factory <idea>`. Resume an interrupted run: `/factory`.

Run state lives in `.factory/` (gitignored): `state.md`, per-ticket contracts, and worktrees.

## Install (local, no download)

```bash
claude plugin marketplace add ~/Code/software-factory
claude plugin install software-factory@software-factory --scope project   # run inside the target repo
```

Use `--scope user` to have it in every repo. Note that the hooks then apply everywhere too: `block_secrets` guards every session, while `path_scope` and `stop_gate` only act on the factory's own agents.

To try it without installing: `claude --plugin-dir ~/Code/software-factory`.

## Tests

```bash
python3 -m unittest discover -s tests -v
claude plugin validate .
```

## Known limits

- `block_secrets` checks `git add`/`commit` against the session's cwd; `cd other && git add .` inside one command is checked against the wrong directory.
- `guard_bash` parses the shell command line. Writes made inside an interpreter (`python -c`, `node -e`, a script) or by tools like `npm install` are not seen.
- The stop gate runs your full layer test command on every builder stop. Keep it fast, or point `test:` at a quicker subset.

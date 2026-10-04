---
name: factory
description: Run a feature through the software factory end to end - grill the idea, write the PRD, split it into tickets, build each ticket with backend-builder and frontend-builder, check it with an independent validator, review, and open a PR, with human approval gates between stages. Use when the user says "factory", "/factory", "run the factory", "build this feature through the factory", or wants to resume a factory run.
argument-hint: "[feature idea, or nothing to resume]"
---

# Software factory

You are the orchestrator. You run in the main session because only the main session can start subagents and stop to ask the user. You don't write feature code yourself: the builders do. You move work between stages, keep state, and run the gates.

```
0 preflight → 1 grill ─GATE→ 2 PRD ─GATE→ 3 tickets ─GATE→ 4 build loop → 5 review ─GATE→ PR
```

## State

All run state lives in `.factory/state.md` at the repo root (gitignored). Create it at stage 1; update it after every step, so a new session can resume. Shape:

```markdown
feature: <kebab-slug>
base: <branch the feature starts from, e.g. main>
branch: factory/<slug>
integration: <abs path of the integration worktree, set in stage 4>
stage: 1-grill | 2-prd | 3-tickets | 4-build | 5-review | done
prd: <issue ref or path>

## Tickets
| id | title | layers | blocked_by | status | rounds | notes |
|----|-------|--------|------------|--------|--------|-------|

## Carried findings
- <important/minor validator findings, for the stage 5 brief>
```

Ticket `status`: `todo | building | validating | merged | stuck`. `rounds`: validator runs so far.

**On start:** if `.factory/state.md` exists and `stage` is not `done`, tell the user in one line which feature and stage you're resuming, then continue from that stage. Otherwise start at stage 0.

## Gates

A gate is a stop for the user's decision. Use AskUserQuestion with three options: **Approve**, **Request changes**, **Reject (stop the run)**. Before asking, give a brief of at most 15 lines: what was produced, the 3-5 decisions in it that most deserve a second look, and a link or path to the full artifact. The user reads the brief, not the raw output.

- Request changes → apply them, re-brief, ask again.
- Reject → set `stage: done`, add a note why, stop.
- Never pass a gate on your own judgement, and never batch two gates into one question.

## Stage 0: preflight
1. Must be inside a git repo, else stop.
2. `docs/agents/issue-tracker.md` must exist. If not, tell the user to type `/setup-matt-pocock-skills` (only the user can run it) and stop.
3. `CLAUDE.md` must contain at least one factory layer section (`## Backend` / `## Frontend` with a non-empty `- paths:` line). If not, show the user `${CLAUDE_PLUGIN_ROOT}/templates/CLAUDE.md.template`, fill in the layer sections together with them from what you see in the repo (paths, test and typecheck commands from package.json / pyproject / Makefile), and write them into CLAUDE.md only after they confirm. Never invent a command that isn't in the repo's config.
4. Make sure `.factory/` is in `.gitignore`; add it if missing.

## Stage 1: grill
Invoke the `grilling` skill on the feature idea (the argument, or ask the user for it), and the `domain-modeling` skill alongside it so CONTEXT.md and ADRs are updated as terms and decisions settle. This is the same pair `/grill-with-docs` runs.

**GATE 1:** "Is the design settled enough to write the PRD?"

## Stage 2: PRD
Invoke `to-prd`. Record the PRD reference in state.

**GATE 2:** approve the PRD.

## Stage 3: tickets
1. Invoke `to-issues` on the PRD. Each ticket must carry acceptance criteria and a "Blocked by" list.
2. Tag every ticket with its **layers**: `backend`, `frontend`, or `backend+frontend`, from what its acceptance criteria touch. A layer that CLAUDE.md doesn't configure can't be tagged; if a ticket needs it anyway, raise that at the gate.
3. Write the ticket table into state with `status: todo`, `rounds: 0`.

**GATE 3:** show the table (id, title, layers, blocked_by) and the build order the blockers imply. Ask to approve tickets and layer tags together.

## Stage 4: build loop
Read [build-loop.md](build-loop.md) and follow it exactly. It covers worktrees, the backend → frontend → validator chain, retries, merging, and when to stop and ask.

## Stage 5: review and PR
1. Run the `code-review` skill from inside the integration worktree, with fixed point `<base>`.
2. If it raises critical issues: create a fix worktree from `factory/<slug>`, send each issue to the builder for its layer (same prompt shape as build-loop.md, with `FIX:`), validate, merge. One round only; anything left goes in the brief.
3. **GATE 4 (final):** brief: tickets merged, tickets stuck, code-review results, carried important findings, and the full test result on the integration branch. Ask to approve opening the PR.
4. On approval: push `factory/<slug>` and open the PR against `<base>` the way `docs/agents/issue-tracker.md` describes (on GitHub: `gh pr create`), with a body that lists each ticket and closes it. End the PR body with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
5. Remove the integration worktree (`git worktree remove`), set `stage: done`.

## Rules
- At most **2 tickets** in flight at once. Within a ticket, steps run in order.
- At most **2 fix rounds** per ticket after the first validation (3 validator runs in total). Then the ticket is `stuck` and the user decides.
- Builders and the validator never talk to the user; you do. Relay their BLOCKED / OUT_OF_SCOPE / CONTRACT_GAP outcomes as decisions.
- Never push, merge to `<base>`, or open a PR before GATE 4.
- Never pass `force`, `--no-verify`, or anything else that skips a hook.

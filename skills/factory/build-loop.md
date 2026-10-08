# Stage 4: build loop

Notation: `<repo>` is the main checkout's root, `<slug>` the feature slug, `<base>` the base branch, all from `.factory/state.md`.

## Setup (once per run)

The user's main checkout is never switched or touched. All merging happens in an **integration worktree** on the feature branch.

```bash
git -C <repo> worktree add <repo>/.factory/worktrees/_integration -b factory/<slug> <base>
```

If the branch already exists (resumed run), add the worktree without `-b`. Save the absolute path as `integration:` in state. Create `<repo>/.factory/tickets/`.

## The loop

Repeat until no ticket is `todo`:

1. **Frontier:** tickets with `status: todo` whose every `blocked_by` ticket is `merged`. If the frontier is empty but `todo` tickets remain, they are blocked by `stuck` tickets: go to **Stuck** below.
2. **Wave:** take up to 2 frontier tickets, in ticket order. Run steps A-E for each. The two tickets run in parallel: start their subagents in the background and handle each as its notification arrives.
3. After the wave, update state, then loop.

### A. Ticket worktree
```bash
git -C <integration> worktree add <repo>/.factory/worktrees/<id> -b factory-t/<slug>-<id> factory/<slug>
mkdir -p <repo>/.factory/tickets/<id>
```
Set `status: building`.

### B. Build
Start each builder with the Agent tool, `subagent_type: "software-factory:backend-builder"` or `"software-factory:frontend-builder"`, `run_in_background: true`. Prompts are short context pointers, not copies:

```
WORKTREE: <repo>/.factory/worktrees/<id>
TICKET: <ticket ref or path>
PRD: <prd ref>
CONTRACT: <repo>/.factory/tickets/<id>/contract.md   (backend: "none" if layers has no frontend)
FIX: <validator findings for this layer, verbatim, only on fix rounds>
```

Order by layers:
- `backend` → backend-builder only, `CONTRACT: none`.
- `frontend` → frontend-builder only, `CONTRACT: none`.
- `backend+frontend` → backend-builder, wait for DONE and the contract file to exist, then frontend-builder.

Read each builder report:
- `STATUS: DONE` → next step.
- `CONTRACT_GAP` from frontend → run backend-builder with `FIX: CONTRACT_GAP: <what is missing>`, then frontend-builder again. At most 2 contract-gap loops per ticket; a third gap marks it `stuck`.
- `OUT_OF_SCOPE` or `STATUS: BLOCKED` → mark the ticket `stuck`, note the reason, leave its worktree in place. Don't block the other ticket in the wave.

### C. Validate
Set `status: validating`. Start `subagent_type: "software-factory:validator"` with:
```
WORKTREE: <repo>/.factory/worktrees/<id>
BASE: factory/<slug>
TICKET: <ticket ref>
LAYERS: <layers>
CONTRACT: <contract path or none>
BUILDER REPORTS: <the builders' final reports, verbatim>
```
Increment `rounds`.

### D. Route the verdict
- `VERDICT: PASS` → go to E. Copy its important/minor findings into **Carried findings** in state.
- `VERDICT: FAIL` and `rounds` ≤ 2 → send the `critical [backend]` findings to backend-builder and the `critical [frontend]` findings to frontend-builder as `FIX:` (backend first if both), then validate again (back to C).
- `VERDICT: FAIL` and `rounds` = 3 → mark `stuck` with the remaining critical findings in notes.

### E. Merge
```bash
cd <integration> && git merge --no-ff factory-t/<slug>-<id> -m "Merge ticket <id>: <title>"
```
- On conflict: `git merge --abort`, mark `stuck` with the conflicting files. Don't resolve conflicts here; the user decides at the Stuck gate (they may ask you to use the `resolving-merge-conflicts` skill).
- After a clean merge, run every configured layer's test and typecheck commands in `<integration>`. If they fail, the merge broke something across tickets: `git reset --hard ORIG_HEAD` inside `<integration>` only, mark `stuck` with the failure.
- If green: `git -C <repo> worktree remove <repo>/.factory/worktrees/<id>`, `git -C <repo> branch -d factory-t/<slug>-<id>`, set `status: merged`, and mark the ticket done in the issue tracker the way `docs/agents/issue-tracker.md` describes.

## Stuck

When a wave ends with any `stuck` ticket, or the frontier is empty with `todo` tickets left, stop and ask the user (AskUserQuestion), one stuck ticket at a time. Brief: ticket, rounds used, last critical findings or blocker, worktree path, and what the agents actually did: read `<repo>/.factory/audit/<id>.jsonl` and summarise its `deny` and `error` lines and any `STUCK` reason in two or three bullets. Options:
- **Retry with guidance**: the user's note goes to the right builder as `FIX:`; `rounds` resets to 0 for this ticket.
- **I'll fix it by hand**: wait; when they say done, validate (C) and continue.
- **Drop the ticket**: remove its worktree and branch, mark `stuck (dropped)`, and tell the user which tickets it was blocking.
- **Stop the run**: save state and stop. `/factory` resumes later.

## Done

When every ticket is `merged` or `stuck (dropped)`, set `stage: 5-review` and return to SKILL.md stage 5.

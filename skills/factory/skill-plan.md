# Stage 3b: skill plan

Decide which skills this run needs before any code is built, so builders follow one procedure instead of improvising per ticket, and product skills are built to the standard. Read [skill-standard.md](skill-standard.md) first; every rule below refers to it.

## 1. Find candidates

**Helper** (used by builders on this repo). Read the approved tickets and the repo. A candidate is a procedure that:
- repeats in 2+ tickets of this run, or will clearly repeat in future work on this repo (add an endpoint, add a migration, add a screen, wire a job, write an e2e test), and
- has a "done" you can state as yes/no lines.

**Product** (shipped as part of the product). Read the PRD. A candidate is a feature whose behaviour is "the product's agent does job X": an agent procedure, not ordinary code. Code the skill's scripts call stays in normal backend tickets.

## 2. Filter

1. Apply "When NOT to write a skill" from the standard to each candidate. Drop failures, keeping a one-line reason.
2. Look for existing skills that cover it: the repo's helper and product paths, `~/.claude/skills/`, and the skills listed in this session. A match becomes **extend <existing>** (only if it lives in this repo) or **use <existing>** (anything else), not a new skill.
3. Check collisions between the remaining candidates: two with overlapping triggers are merged, or each gets a "Not for X; use Y" exclusion.
4. Cap new skills at **3 per run** (the standard: start with 3, not 20). Rank by how many tickets use it; the rest go to "later" in the brief.

## 3. Draft the plan

For each kept skill, write only its label, not its body:

| id | name | kind | action | description (draft) | used_by | blocked_by |
|----|------|------|--------|---------------------|---------|------------|
| S1 | add-api-endpoint | helper | new | Adds one REST endpoint ... Not for ... | T2, T4, T5 | - |
| S2 | audit-spending | product | new | Reads a bank CSV ... Not for ... | - | T3 |
| S3 | write-e2e-test | helper | use (~/.claude/skills) | - | T6 | - |

- `kind`: `helper` or `product`. `action`: `new`, `extend`, or `use`.
- `description`: the full draft, to the standard's four-part shape. It is the most important line of the plan.
- `used_by`: code tickets whose builders must follow this helper skill.
- `blocked_by`: tickets that must merge first (a product skill whose scripts call code from a backend ticket).

**No candidates survive?** Say so in one line at the gate and move on; skipping is a valid outcome.

## 4. Config check

New or extended skills need the `## Skills` section in CLAUDE.md:

```markdown
## Skills
- helper: .claude/skills
- product: <where the product loads its skills from, or leave empty>
- test: <optional command that runs the skills' script tests>
```

If it is missing, or a kind in the plan has no path, propose the lines and write them only after the user confirms at the gate. Never guess a product path: ask.

## GATE 3b

Brief (≤ 15 lines): the plan table, the dropped candidates with one-line reasons, "later" candidates, and any CLAUDE.md change. Options as usual. Request changes → edit the plan, re-brief.

## 5. On approval: write the plan into state

1. Add each `new` / `extend` skill as a ticket row in state: `id` S1.., `layers: skill`, `blocked_by` from the plan, `status: todo`, `rounds: 0`. Create the issue in the tracker like any ticket, with the description draft, kind, target folder and "Done means" from the standard's production checklist as acceptance criteria.
2. For every code ticket in a helper skill's `used_by`, add that skill's ticket to its `blocked_by`, so the skill is merged into `factory/<slug>` before that ticket's worktree is cut. A `use` skill blocks nothing.
3. Fill the `## Skills` table in state (see SKILL.md).

Then go to stage 4.

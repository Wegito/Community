# Working with a team of agents

How this repository is meant to be built by several AI agents of different strength: cheap agents for
easy work, strong agents for hard or risky work, every result checked, and a failed task handed to the
next stronger agent. The method is general; the last section shows how to reuse it in any project.

## 1. The loop

```
                ┌──────────────────────── docs/TASKS.md (cards with tier + acceptance criteria)
                ▼
        Orchestrator (strong model; plans, routes, never writes code itself)
                │  picks the next ready card, chooses the agent by tier
                ▼
        Implementer  T1 light ─► T2 standard ─► T3 expert ─► person (T4)
                │                     ▲ escalate after a second FAIL
                ▼                     │
        Gate 1: tools/check.sh  (lint, format, CSS, migrations, all tests; objective, cannot be argued with)
                │ pass
                ▼
        Gate 2: reviewer (strong model, read-only): every acceptance criterion, project rules, attack the change
                │ PASS                           │ FAIL
                ▼                                ▼
        commit "<id>: <what>", card done   retry once at the same tier with the reviewer's notes,
                                           then escalate one tier
```

Why it works:
- **Small cards with checkable "done when"** let a cheaper model succeed: it does not have to understand
  the whole system, only follow an example and satisfy tests.
- **Objective gates first.** Tests, lint and migrations catch most failures for free. The reviewer only
  looks at what passed them.
- **Nobody grades their own work.** The reviewer is a different agent, at least as strong as the risk
  of the card, and it may not edit files.
- **Escalation instead of perfection.** A cheap agent may fail; the cost of a failed T1 attempt is small
  compared with always using the strongest model.
- **People decide what only people can decide** (T4): money rules, legal questions, accounts, servers.

## 2. Ranking a task

Score each question 0, 1 or 2 and add up:

| Question | 0 | 1 | 2 |
|---|---|---|---|
| Is there an example in the code to copy? | yes, almost identical | similar | no, new design |
| How many files and modules? | 1–3 files, one module | one module | several modules |
| Does anything need a decision? | no | small, documented | design or product decision |
| What breaks if it is wrong? | a label | one feature | money, permissions, security, existing data |
| Concurrency, idempotency, migrations of existing data? | no | one of them | several |

**0–2 → T1, 3–5 → T2, 6+ → T3.** A "2" in *what breaks* always means a T3 **review**, even if the
implementation is T2. Anything that needs the owner (a decision, an account, a file with real data,
legal advice) is **T4**: an agent may prepare options, a person answers.

If a card scores T3 only because it is big, split it. A good card is one purpose, one commit, usually
under ~300 changed lines, with tests that would fail without the change.

## 3. Escalation and stopping rules

1. Implementer at the card's tier.
2. `tools/check.sh` fails → the same implementer gets the output and fixes it (counts as part of the
   attempt).
3. Reviewer FAIL → one retry at the same tier with the reviewer's notes.
4. Second FAIL → the next tier starts fresh from the card plus both reviews (not from the broken diff,
   unless the reviewer says it is close).
5. T3 fails twice → stop and ask a person; the card is probably unclear or needs a decision.
6. An implementer that says "the card needs a decision" stops the loop for that card → T4.

Write each outcome down (card, tier tried, PASS/FAIL, retries, what failed). After ten or twenty cards
you will see which kinds of cards a tier handles, and can move the thresholds in §2.

## 4. In this repository

Files:
- `CLAUDE.md`: the rules every agent reads first (layout, commands, conventions).
- `docs/TASKS.md`: the cards, their tier and review tier, order and dependencies.
- `tools/check.sh`: Gate 1, the same checks as CI.
- `.claude/agents/`: the agent definitions. Each sets its model in its frontmatter:

| Agent | Model | Use |
|---|---|---|
| `planner` | opus | split a milestone into cards |
| `implementer-light` | haiku | T1 cards |
| `implementer` | sonnet | T2 cards |
| `implementer-expert` | opus | T3 cards, and escalations |
| `reviewer` | opus | Gate 2 for every card; read-only |

Running it in Claude Code: start a session on a strong model as the orchestrator and give it an
instruction like:

> Work through Wave 1 of docs/TASKS.md. For each ready card: start the agent of its tier with the card
> id, then run tools/check.sh, then the reviewer agent. On PASS commit as "<id>: <title>". On FAIL
> follow the escalation rules in docs/AGENTS.md §3. Cards on the same order line may run in parallel,
> each in its own git worktree. Stop at T4 cards and at a second T3 failure and tell me why. Keep a log
> of every attempt in docs/agent-log.md.

Notes:
- Claude Code reads the agent files from `.claude/agents/` and starts them with the model named there.
  The fields used here (`name`, `description`, `tools`, `model`) are the documented ones as of
  2026-10; check the current Claude Code documentation if something does not load.
- For a fixed, repeatable pipeline instead of a free-form orchestrator, Claude Code can also run a
  workflow script that calls the agents in this order; ask the session to "use a workflow".
- Parallel cards need separate git worktrees, or they overwrite each other's files.
- Which model is strongest changes over time. The table above uses the Haiku / Sonnet / Opus order;
  re-check the tiers when new models arrive, and measure (§3) instead of trusting names.

## 5. Reusing the method in any project

1. **Write the rules file** (`CLAUDE.md`): what the project is, commands, layout, the rules a reviewer
   will check. Cheap models need this most.
2. **Make one command that says "good" or "bad"** (`tools/check.sh`): lint, types, tests, build. Without
   an objective gate, every check becomes an opinion.
3. **Plan in milestones, work in cards.** A strong model (or you) splits the next milestone into cards
   with tier, dependencies, files, an example to follow, steps and "done when" criteria.
4. **Rank** with the five questions in §2. Mark the cards that need a person.
5. **Define the agents**: one implementer per tier, a read-only reviewer, a planner. Give each a short,
   strict instruction: read the rules, stay in scope, run the gate, do not weaken tests, report and stop
   when unsure.
6. **Run the loop** in §1 with the stopping rules in §3. Commit one card at a time so a bad card is
   easy to revert.
7. **Measure and adjust**: log every attempt; move card types up or down a tier based on results; split
   cards that keep failing.

What it does not solve: unclear goals (those come back as T4), missing tests (write the tests first, at
a higher tier), and decisions about money, law or people. Cost goes down only if most cards pass at the
low tiers; if many escalate, the cards are too big or too vague.

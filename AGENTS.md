# foundry-sandbox: agent rules

| Field | Value |
| --- | --- |
| Status | draft |
| Version | 0.1 |
| Owner | Bassem-Zohdy |
| Approver | Bassem-Zohdy |
| Date approved | not yet approved |

Base branch: `main`. Generated for Foundry 0.5.0.

These rules apply to every coding agent and every developer in this repo.

## Hard rules

1. One branch per change, cut from `main`. Never commit to `main` directly.
2. Every change ships with tests. Business logic is written test-first; a bug fix starts with a failing test that reproduces it.
3. No secrets in code, prompts, docs or commit history. Use environment variables or the secret store.
4. Attribution is human only. Commits and PRs are authored by the person running the session, with no AI co-author or "generated with" lines.
5. Approvals go through GitHub, never through chat:
   - a story plan is approved when the tech lead (`Bassem-Zohdy`) comments `/approve-plan <sha>` on the PR, where `<sha>` is the PR head commit; a bare `/approve-plan` does not count;
   - a gate is approved by a tech-lead comment `/approve-gate <G> <sha>` (for example `/approve-gate G2 <sha>`), or by an approving review on the PR head;
   - a plan approval covers the story file from `## Story` down to `## Ledger` (story, acceptance criteria, requirement IDs and Plan): code pushes and Ledger or Test evidence notes keep it, and any change above `## Ledger` resets it. A gate approval is for one head sha: any new commit resets it;
   - story files are plain Markdown: no raw HTML blocks (no `<!-- -->`, `<details>` or other line-starting tags), code fences only at column 0 (never inside a list item or quote), before `## Story` only one `# ` title and one header table, with no HTML, and below `## Ledger` no heading except `## Test evidence`, no `---` or `===` lines, no inline HTML (even in backticks) and no lines starting a link or footnote definition. The plan check fails otherwise, because such Markdown can render differently from what the check reads;
   - agents never post approvals: no `/approve-plan <sha>` or `/approve-gate <G> <sha>` comment and no approving review, even when asked and even under the tech lead's login. Only the human tech lead posts them.
6. Documents change in the same PR as the code they describe. Every user-facing string exists in Arabic and English.

## Rules for using AI

- Never paste client production data or real personal data into any AI tool. Use synthetic data.
- Never put secrets in prompts, `CLAUDE.md`, `AGENTS.md` or skills.
- Read every diff before opening a PR. The agent writes code; the developer owns it.
- AI-drafted documents stay in `draft` status until a human approves them.
- If an agent misbehaves (wrong skill, skipped tests, loops), stop the session, diagnose it and raise it at the retro.

## Phased workflow

The phased workflow (design, plan, build, review, release, with gates) needs the Foundry plugin installed. Without it these rules still apply, but gate tracking and the `/foundry` commands are unavailable.

## Build flow and limits

After the plan is approved on GitHub: build test-first (`foundry:build-inline`, or `foundry:build-subagents` for 4 or more tasks on disjoint files), run `/foundry verify`, then run `foundry:review`. The PR is ready for the tech lead only when `foundry review gate` passes. Verify and review run on every PR, whatever its size.

Stack rules: read every file in `docs/foundry/stacks/` before writing code.

Code limits for new and changed code: files 300 lines (soft) and 400 (hard); functions about 40 lines; cyclomatic complexity 10; nesting depth 3; 4 parameters. A change deletes the code it replaced.

## Review

See `REVIEW.md`. Risky paths are listed in `foundry.json` and owned by the tech lead in `CODEOWNERS`.

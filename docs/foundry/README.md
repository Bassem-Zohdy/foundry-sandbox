# Foundry workflow records

| Field | Value |
| --- | --- |
| Status | draft |
| Version | 0.1 |
| Owner | Bassem-Zohdy |
| Approver | Bassem-Zohdy |
| Date approved | not yet approved |

Foundry keeps its per-module state and gate records here. Do not edit these by hand: they are changed by `/foundry` commands and approved on GitHub. Generated for Foundry 0.5.0; base branch `main`.

## Governance CI

`.github/workflows/foundry-governance.yml` runs three required checks on every pull request: `foundry / attribution`, `foundry / plan-approved` and `foundry / gate-approved`. It runs on pull request events only. A `/approve-plan <sha>` or `/approve-gate <G> <sha>` comment, or a review, triggers `.github/workflows/foundry-approval-rerun.yml`, which re-runs those checks. The re-run lives in its own workflow because a review-triggered run attaches to the pull request's latest commit, and GitHub counts a skipped required check as passing, so the required check names must never appear in it. On pull requests from forks, a review cannot trigger the re-run (GitHub gives that run a read-only token); approve with the `/approve-*` comment instead. They run a vendored copy of the Foundry CLI in `scripts/foundry-ci/`, checked out from the base branch, so a pull request cannot change the checker that judges it.

- `foundry / plan-approved` applies to every pull request into the base branch, whatever its branch name. A pull request with no story file in `docs/stories/` fails. A dependency-bot pull request needs a story file too, or a decision by the tech lead. The branch pattern in `foundry.json` is a naming convention only. The exception is a gate pull request: with a `gate:*` label and only tech-lead document files (the planning documents in `CODEOWNERS`, including `docs/foundry/`, never `docs/stories/`), `plan-approved` passes as not applicable and `foundry / gate-approved` decides it.
- `foundry / traceability` runs on every pull request too. It only warns, on every pull request, including in a repo still in Discover where the documents are not written yet; that is by design. It never fails and is not a required check.
- A plan approval covers the story file from `## Story` down to `## Ledger` (story, acceptance criteria, requirement IDs and Plan). Code pushes and Ledger or Test evidence notes keep it; any change above `## Ledger` resets it.
- Story files must be plain Markdown, or `foundry / plan-approved` fails at the head and at the approved commit: no raw HTML blocks, code fences only at column 0 (not in a list item or quote), before `## Story` only one `# ` title and one header table, with no HTML, and below `## Ledger` no heading except `## Test evidence`, no `---` or `===` lines, no inline HTML (even in backticks) and no lines starting a link or footnote definition. These rules make sure text added below `## Ledger` after approval can never render inside the Plan.
- A gate approval is for one head sha: any new commit resets it. `foundry / gate-approved` passes as "not applicable" when the pull request has no `gate:*` label.
- Approvals are posted by the human tech lead only. Agents never post `/approve-plan <sha>` or `/approve-gate <G> <sha>` comments or approving reviews.

Known limit: a pull request can still edit the workflow file itself and weaken or remove a check. `foundry.json`, `.github/` (CODEOWNERS and the workflows), `.claude/`, `docs/foundry/` and `scripts/foundry-ci/` are therefore owned by the tech lead in CODEOWNERS. With a stack pack, so are the files that define its quality gate (the pack's `guarded` paths, for example `quality/`, `scripts/foundry-quality/` and the lint and Semgrep configs): the stack workflow restores `foundry.json` and every one of them from the base branch (`scripts/foundry-quality/trusted-gate.sh`) before each gate step, so a change to them takes effect after it merges. `quality/baseline/` is the exception: the pull request's counts stay, and `quality raised` judges them against the base's. Code-owner review is required in team mode only.

Residual limit of the trusted stack checker: the tool binaries (ESLint, knip, dependency-cruiser, jscpd, ruff, vulture, pip-audit) come from the pull request's own dependency manifests and lockfiles, and the install scripts and tests run PR code. The gate files are restored again after setup and after the tests, but a pull request can still swap a tool for a weaker one. The workflow files and the `foundry-setup` action are the pull request's own copies too. For those, CODEOWNERS with code-owner review is the only protection.

A review (approval, changes requested or dismissal) and a `/approve-*` comment from an owner, member or collaborator both trigger the same re-run.

### Limits of the required checks

- A pull request can add a second workflow file whose jobs reuse the required names (`foundry / attribution` and so on) and always pass. Pinning the required checks to the GitHub Actions app does not stop this, because that workflow also runs as GitHub Actions. Only CODEOWNERS on `.github/` with code-owner review (team mode) does.
- Another GitHub App or a personal token could post a check run with the same name. The branch ruleset that `foundry github apply` sets pins each required check to the GitHub Actions app to prevent that.
- Images are not restricted in story files. An image in the title, header table or Ledger could show a picture of a fake plan. It cannot hide or change the approved text, so read the Plan as text, not pictures, before approving.
- Removing a `gate:*` label makes `foundry / gate-approved` pass as "not applicable". Whoever can edit labels can drop a gate.
- GitHub cannot tell the tech lead from an agent session that holds the tech lead's token. The agent hooks refuse approval comments and approving reviews, but they are client-side guardrails: anyone holding the token can post an approval outside them.
- The governance checks fail on the setup pull request itself, because the base branch has no `scripts/foundry-ci/` until it merges.

### Troubleshooting

- GitHub will not re-run a workflow run older than 30 days: the re-run job fails. Push a commit to the PR instead.
- A run waiting in `action_required` (a first-time contributor from a fork) makes the re-run job wait until it times out. Approve the pending run in the Actions tab first.
- Reviews on PRs from forks run with a read-only token, so the re-run job cannot re-run there; post a `/approve-*` comment or push a commit.

## Build loop and review gate

After a plan is approved, the agent builds test-first, runs `/foundry verify` (a PASS/FAIL report, written under `<git-common-dir>/foundry/verify/`), then `/foundry review`. Each reviewer writes a result file under `<git-common-dir>/foundry/review/<branch>/`, outside the work tree, so nothing is added to `.gitignore`. `foundry review gate` fails on a missing or stale result and on any open Blocking finding. The gate runs locally and is the precondition for marking a PR ready; it is not a required CI check, because CI holds no model key. The tech lead's GitHub approvals remain the server-side gates. After a pass, the review summary goes in a PR comment, which is the record; nothing is added to the Ledger for it. Any new commit on a ready PR sends it back to draft (`gh pr ready --undo`) and `/foundry review` runs again before the next human review.

## Solo mode limits

In solo mode the base-branch ruleset requires no human review: 0 approving reviews and no code-owner review. That means:

- CODEOWNERS is not enforced, so a pull request can edit or add workflows, `foundry.json` or CODEOWNERS without review, and, with a stack pack, can change the quality gate (baselines, lint and Semgrep configs, `scripts/foundry-quality/`) that checks it;
- G3 has no second reviewer;
- the only approvals are the tech lead's `/approve-*` comments, and an agent session under the tech lead's login could post them past the hooks (see above);
- the required checks still apply to everyone, admins included: the ruleset has no bypass.

# CHORE: Update the Foundry files to Foundry 56f06d1

| Field | Value |
| --- | --- |
| Status | draft |
| Version | 0.1 |
| Owner | Bassem-Zohdy |
| Approver | Bassem-Zohdy |
| Date approved | not yet approved |

## Story

As the tech lead, I want the sandbox's Foundry files to match Foundry 56f06d1, so that one approval re-runs every governance run on a PR and `/approve-deps` accepts only commits that GitHub signed for Dependabot.

## Acceptance criteria

- [ ] Given a PR with several governance runs on its head, when the tech lead posts an approval, then every one of those runs is re-run and the PR is not left blocked by an older failing run.
- [ ] Given a Dependabot PR whose commits are committed by `web-flow` with a verified signature, when the tech lead posts `/approve-deps <head sha>`, then `foundry / plan-approved` passes.

## Requirement IDs

None: a tooling chore with no product requirement.

## Plan

Files rendered from the Foundry template at 56f06d1 and copied over the sandbox's copies (rulings R36 and R37):

1. `.github/workflows/foundry-approval-rerun.yml`: re-run every `foundry-governance` pull_request run for the head, not only the newest.
2. `scripts/foundry-ci/foundry_cli/dep_update.py` and `gh.py`: require `web-flow` as committer and a verified signature on every Dependabot commit.
3. `docs/foundry/README.md`: the matching wording.

No tests are added in this repo: the checker's tests live in Foundry. Verification here: this PR's CI, then `/approve-deps` on the open Dependabot PRs.

## Ledger

## Test evidence

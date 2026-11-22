# CHORE: Update the Foundry files to Foundry 2f2b183

| Field | Value |
| --- | --- |
| Status | draft |
| Version | 0.1 |
| Owner | Bassem-Zohdy |
| Approver | Bassem-Zohdy |
| Date approved | not yet approved |

## Story

As the tech lead, I want the sandbox's Foundry files to match Foundry 2f2b183, so that Dependabot stops proposing major and base-image minor upgrades and a dependency-only Dependabot PR can be approved with `/approve-deps <sha>` instead of a story.

## Acceptance criteria

- [ ] Given this PR is merged, when Dependabot next runs, then it opens no major-version PRs, and docker image PRs are patch updates only.
- [ ] Given a dependency-only Dependabot PR, when the tech lead posts `/approve-deps <head sha>`, then `foundry / plan-approved` passes without a story file.
- [ ] Given CI on this PR, then the dashboard image builds on `node:24.22.2-bookworm-slim` and the setup action uses Node 24.22.2.

## Requirement IDs

None: a tooling chore with no product requirement.

## Plan

Files rendered from the Foundry template at 2f2b183 and copied over the sandbox's copies. `docs/brd.md` and `docs/prd.md` are left as they are, because they hold the real G1 content.

1. `.github/dependabot.yml`: ignore semver-major for npm, pip and actions; ignore semver-major and semver-minor for docker; the docker group becomes `docker-patch`.
2. `scripts/foundry-ci/`: the vendored checker with the `deps` approval route (`dep_update.py`, `dep_ci_files.py`, `dep_manifests.py`) and the gate-label setting; `vendor.txt` records the new source commit.
3. Node 24.22.2 in `.github/actions/foundry-setup/action.yml` and `dashboard/Dockerfile.web`; comment and doc updates in the workflows and `docs/foundry/`.
4. `scripts/foundry-quality/lib/typescript.sh`: the FORCE_COLOR fix.

No tests are added in this repo: the checker's tests live in Foundry, where CI passed on 2f2b183. Verification here is this PR's own CI. After merge: run `foundry github apply` for the `gate:*` labels, close the old Dependabot PRs and let Dependabot reopen them under the new rules.

## Ledger

## Test evidence

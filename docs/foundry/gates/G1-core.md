# G1 findings: core

| ID | Item | Evidence |
| --- | --- | --- |
| G1-01 | BRD goals are measurable: each BG has a measure and a target | docs/brd.md:17 (BG-01: about 30 to under 5 a week by 2027-03), docs/brd.md:18 (BG-02: under 1 minute) |
| G1-02 | Every story traces to a goal (trace --gate G1) | trace: "BG-01 …: ST-001, ST-003", "BG-02 …: ST-002, ST-003"; `foundry trace --gate G1` exit 0 |
| G1-03 | Scope and out of scope are stated (BRD '## Scope and out of scope', PRD '## Out of scope') | docs/brd.md:24-28 (in scope and out of scope), docs/prd.md:39-41 |
| G1-04 | PRD validation was run (docs/foundry/gates/prd-validation.md exists) | docs/foundry/gates/prd-validation.md |

## Traceability

`trace --gate G1` output:

```
# Traceability report

## Goals to stories
- BG-01 Support staff check API health themselves instead of asking an engineer: ST-001, ST-003
- BG-02 Support staff see an outage quickly: ST-002, ST-003

## Stories to functional requirements
- ST-001 As a support staff member, I open the dashboard and see whether the API is up: (none)
- ST-002 As a support staff member, I see when the API is down: (none)
- ST-003 As an on-call engineer, I can call a health endpoint from scripts: (none)

## Requirements to tests

## Orphans
- module core: has no functional requirement

## Dangling references
- FR-CORE-001: referenced in docs/test-plan.md:23 but not defined

## Not written yet
- FR-CORE-001
- NFR-001
- TC-001

no failures from trace --gate G1
```

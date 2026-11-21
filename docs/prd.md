# foundry-sandbox: Product Requirements (PRD)

| Field | Value |
| --- | --- |
| Status | draft |
| Version | 0.1 |
| Owner | Bassem-Zohdy |
| Approver | Bassem-Zohdy |
| Date approved | not yet approved |

What we are building and for whom. Each story lists the business goals it serves.

## Users and personas

Support staff member; on-call engineer.

## Epics

One `## Epic: <bounded context>` heading per epic, with its stories as `ST-###` rows in the table below.

## Epic: core

ST-001, ST-002, ST-003

## Stories

| ID | Story | Business goals | Acceptance criteria |
| --- | --- | --- | --- |
| ST-001 | As a support staff member, I open the dashboard and see whether the API is up | BG-01 | Given the API answers its health check, when I open the dashboard, then it shows "API: ok" |
| ST-002 | As a support staff member, I see when the API is down | BG-02 | Given the API does not answer, when I open the dashboard, then it shows "API: unavailable" within 1 minute |
| ST-003 | As an on-call engineer, I can call a health endpoint from scripts | BG-01, BG-02 | Given the service runs, when I GET /healthz, then it returns 200 with {"status": "ok"} |

## User journeys

## Success metrics

See BG-01 and BG-02 in the BRD.

## Out of scope

Alerting, history, other services, public status pages.

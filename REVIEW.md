# Review policy

| Field | Value |
| --- | --- |
| Status | draft |
| Version | 0.1 |
| Owner | Bassem-Zohdy |
| Approver | Bassem-Zohdy |
| Date approved | not yet approved |

Every PR gets an automated review and a human review. Nobody reviews their own code. Reviews are done within one working day; risky PRs in the next review window.

## Labelling comments

| Label | Meaning | Author must |
| --- | --- | --- |
| blocking | Must change before merge | Fix it, then re-request review |
| suggestion | Optional improvement | Take it or reply with a reason |
| triage required | Automated review finding | Fix it or answer with a reason; never ignore it silently |

## What reviewers check

- It does what the story and requirement IDs say, and nothing extra.
- The tests are real (they would fail if the code broke) and cover the acceptance criteria.
- Tenant data is isolated, input is validated, personal data is handled as the assessment says.
- It is readable and follows the rules in `AGENTS.md`.
- Docs are updated in the same PR.

## Risky PRs

PRs that `foundry risky` reports reasons for (built-in path and content rules, plus `riskyPaths` in `foundry.json`) carry the risky label. They need the tech lead's review and are merged by the tech lead.

## Design principles

Reviewers apply each item to the changed code only. CI already enforces the limits a tool can measure; these items need judgement.

### Single responsibility
**Ask:** Does this file or class change for more than one reason?
**Signals:** Unrelated groups of methods; names such as `Manager` or `...And...`; imports from both the domain layer and the infrastructure layer.
**Evidence:** Quote the line ranges for each separate reason to change.

### Open/closed
**Ask:** Does adding the next variant of this behaviour mean editing existing code instead of adding new code?
**Signals:** A growing `if/else` or `switch` on a type or kind; the same type check repeated in several places; flags that select behaviour.
**Evidence:** Quote the branch chain and the other places that must change with it.

### Liskov substitution
**Ask:** Can every subtype stand in for its base type without the caller knowing which one it has?
**Signals:** An override that throws "not supported" or does nothing; a type check on a subtype before a call; a subtype that narrows inputs or widens outputs.
**Evidence:** Quote the base contract and the override that breaks it.

### DDD boundaries
**Ask:** Does a context import another only through its public interface, and does domain code import no adapters?
**Signals:** Imports of another context's internal modules or tables; domain code importing a framework, the ORM or infrastructure; names outside the glossary.
**Evidence:** Quote the import line and the two paths it joins.

### Code limits
**Ask:** Does the change keep every file, function and signature inside the limits?
**Signals:** A file over 300 lines (soft) or 400 (hard); a function over about 40 lines; cyclomatic complexity over 10; nesting deeper than 3; more than 4 parameters.
**Evidence:** Quote the file or function with its measured size, complexity, depth or parameter count.

## Code smells

### God class
**Ask:** Does one class hold state and behaviour for many unrelated jobs?
**Signals:** Many fields used by disjoint method groups; a class others reach into for everything; a name that is only "Service", "Util" or "Helper".
**Evidence:** Quote the field-to-method groups that never touch each other.

### Feature envy
**Ask:** Does a method use another object's data more than its own?
**Signals:** A chain of getters on one other object; a method that reads several fields of an argument and none of its own.
**Evidence:** Quote the method and the other object's members it uses.

### Shotgun surgery
**Ask:** Would one small change to this behaviour touch many files?
**Signals:** The same constant, rule or field edited in several places in this diff; a new option threaded through many layers.
**Evidence:** Quote the repeated edits, one line per file.

### Primitive obsession
**Ask:** Is a domain concept carried as a bare string, number or dict?
**Signals:** Money, ids, emails or statuses passed as raw values; the same validation repeated for one value in several places.
**Evidence:** Quote the primitive and the places it is checked or interpreted.

### Long parameter list
**Ask:** Does a signature take more than 4 parameters?
**Signals:** Several parameters that always travel together; boolean flags; calls with many positional arguments.
**Evidence:** Quote the signature and one call site.

### Duplicated logic
**Ask:** Does the change copy logic that already exists, or write it twice?
**Signals:** Near-identical blocks; a copied helper with a small tweak; the same rule in code and in a test fixture.
**Evidence:** Quote both blocks with their file and line ranges.

### Leaky context boundary
**Ask:** Do internals of one context show through its public interface?
**Signals:** Public functions returning another context's internal types or persistence rows; callers needing to know a table layout or a private status value.
**Evidence:** Quote the public signature and the internal type it exposes.

### Dead or stale code left by the change
**Ask:** Does the change leave code, config, flags, docs or tests that nothing uses any more?
**Signals:** Functions with no callers after the diff; commented-out code; comments or docs that describe the old behaviour; unused imports or flags.
**Evidence:** Quote the orphaned item. The fix is to delete it in this change, or to name the follow-up issue that will.

## Security

The security reviewer runs every item on risky PRs; any reviewer may raise one on other PRs.

### Schema validation at the boundary
**Ask:** Is all untrusted input validated against a schema where it enters, before any use?
**Signals:** Request bodies, query values, webhooks, queue messages, files or AI output used straight from the raw payload; hand-rolled checks; no size limit.
**Evidence:** Quote the entry point and the first use of the unvalidated value.

### Output encoding for the sink
**Ask:** Is output encoded for where it goes (HTML, shell, logs, URLs)?
**Signals:** Raw HTML injection; user text in a shell command or a log line without neutralising newlines; string-built URLs.
**Evidence:** Quote the sink and the value reaching it.

### Parameterised queries
**Ask:** Are all queries built with parameters only?
**Signals:** String concatenation or formatting inside SQL or another query language; an identifier taken from input.
**Evidence:** Quote the query-building line and the input that reaches it.

### Authentication and authorisation on every new route
**Ask:** Does every new route check who the caller is and what they may do, matching the identity model recorded in `docs/architecture.md`?
**Signals:** A new route without the shared auth guard; a check by role name inline; tenant or owner id taken from the request instead of the session.
**Evidence:** Quote the route definition and the guard it uses, or the missing one.

### Cookie and token handling
**Ask:** Are cookies and tokens handled as the identity model says?
**Signals:** Cookies without `HttpOnly`, `Secure` or `SameSite`; no CSRF protection on cookie auth; tokens in local storage or URLs; no expiry, rotation or revocation; JWT algorithm not pinned.
**Evidence:** Quote the cookie or token code and the setting that is missing.

### CORS from configuration
**Ask:** Do allowed origins come from configuration, never from code or the request?
**Signals:** A wildcard origin; credentials allowed with a broad origin; the `Origin` header echoed back; origins written as literals.
**Evidence:** Quote the CORS setup and where its origins come from.

### Rate limits on new public or expensive endpoints
**Ask:** Does each new public or costly endpoint have a rate limit?
**Signals:** Login, signup, password reset, search, export, upload or AI-call routes without a limiter; unbounded loops over request-sized data.
**Evidence:** Quote the route and the limiter, or its absence.

### Secrets from the environment or the store only
**Ask:** Do secrets come only from the environment or the secret store?
**Signals:** Keys, passwords or tokens in code, fixtures, config files or logs; a `.env` file added to the diff; real values in examples.
**Evidence:** Quote the line (redact the value) and its file.

### Errors that leak no internals
**Ask:** Do error responses and logs avoid stack traces, queries, paths and other internals?
**Signals:** A raw exception message returned to the client; debug output on in production config; different errors that reveal whether an account exists.
**Evidence:** Quote the handler and the detail it returns.

## Blocking rule

From the design: "A finding that names the principle or smell, quotes the evidence and proposes a concrete fix is **Blocking**. One without evidence is a suggestion." The same rule applies to the security items. A reviewer who cannot fill in all three of the named principle or item, the quoted evidence and the concrete fix raises a suggestion, never a blocking finding.

## Bug severity

| Severity | Meaning | Fix target |
| --- | --- | --- |
| Critical | Data loss, security breach, tenant data exposed, or product down | Same day, hotfix allowed |
| High | A core flow is broken with no workaround | Within the current sprint |
| Medium | A flow is broken but has a workaround | Next sprint |
| Low | Cosmetic or minor | Backlog |

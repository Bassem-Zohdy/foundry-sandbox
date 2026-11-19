# Python rules

These rules apply to every Python file in the repo, whether it is a FastAPI API, a Celery worker or a
shared library. Read this file first, then the file for each framework the repo uses (`fastapi.md`,
`celery.md`).

## Skills that apply

Use these through the Skill tool when the work matches:

- `foundry:python-patterns` for idioms, typing and structure.
- `foundry:python-testing` for pytest fixtures, mocks and parametrization; tests come first.
- `foundry:hexagonal-architecture` when you add a module, a port or an adapter.
- `foundry:postgres-patterns` and `foundry:database-migrations` for schema, query and migration work.
- `foundry:redis-patterns` for caches, locks and rate limits.
- `foundry:security-review` before you open a PR that touches input handling, auth, secrets or uploads.
- `foundry:docker-patterns` and `foundry:deployment-patterns` for Dockerfiles and release work.

## Code limits

Foundry's own checker reports these (ruff has no file or function length rule). Hard limits are errors, the soft
file limit is a warning that is listed and not counted.

| Limit | Value | Level |
|---|---|---|
| File length | 300 lines | warning |
| File length | 400 lines | error |
| Function length | 40 lines | error |
| Nesting depth (`if`, `for`, `while`, `with`, `try`, `match`) | 3 | error |
| Parameters | 4 (`self`, `cls` and FastAPI-injected parameters are not counted: a `Depends(...)` default or an `Annotated[..., Depends(...)]` annotation) | error |
| Cyclomatic complexity | 10 | error |
| Modules or packages named `utils`, `helpers` or `misc` | none | error |

ruff runs with Foundry's `ruff-foundry.toml` (`--config`), so a project's own ruff configuration cannot weaken the gate.
It runs with `--ignore-noqa`, and vulture reads copies of the files with `noqa` comments removed, so an inline
`# noqa` hides nothing. Its rules are `E4`, `E7`, `E9`, `F`, `B`, `C90` (complexity 10), `PLR0911`, `PLR0912` (10 branches) and `PLR0915`
(40 statements). Bandit `S` rules are advisory: they are listed and not counted (`S101`, `assert`, is off in tests).

Dead code comes from vulture: every result it reports (confidence 60 or more) is an error. Functions registered with a route or
task decorator (`@router.get`, `@app.post`, `@celery_app.task`, `@shared_task`, `@app.middleware` and the like) are not
reported. A name that is used where vulture cannot see it goes in `vulture_whitelist.py` with the reason. Duplicated
code comes from jscpd (`--format python`, 70 tokens; test files are not checked for duplication). jscpd reads the
same copies as vulture, with `jscpd:ignore-start` / `-end` comments neutralised, so they hide nothing.

The checks read the `.py` files under the pack root that git tracks or would track, less `quality.exclude`, another pack's
root inside this one, and the files Foundry vendors (`.claude/`, `scripts/foundry-ci/`, `scripts/foundry-quality/`).
A run that reads no file while the root has `.py` files fails, as does a file with a syntax error.

## Folders and boundaries

The package named by `python.package` in `foundry.json` holds one folder per module from `modules`:

```
<pkg>/<module>/
  domain/        pure business rules: no framework, database or network imports
  application/   use cases; talks to adapters only through ports
  adapters/      database, HTTP, queue and cache code
  public/        what other modules may import
```

The architecture rules, checked by `py_arch.py`:

- `context-public-interface`: outside a module, import only the module itself (its `__init__`) or `<pkg>.<module>.public.*`.
- `domain-is-pure`: `domain` imports no `application`, `adapters`, `fastapi`, `starlette`, `sqlalchemy`, `celery`, `redis` or `httpx`.
- `application-not-adapters`: `application` never imports `adapters`.
- `no-circular`: no import cycles between files of the package (an import under `if TYPE_CHECKING:` does not count).

Only files inside the package are checked. Without `python.package` and `modules` the architecture rules are off,
and the run says so.

## Security rules (Semgrep)

`.semgrep/foundry-py.yml` holds seven rules in total; two of them concern every Python repo. Each is an error, so
each finding counts against the quality baseline like a limit does. `sh scripts/foundry-quality/run.sh check` runs
them. A failure line reads `semgrep foundry.py-<rule> <file>`. The other five are in `fastapi.md` and `celery.md`.

| Rule | Fails on | Fix |
|---|---|---|
| `foundry.py-sql-string-build` | `text(`, `.execute(`, `.executemany(`, `.raw(` or `.exec_driver_sql(` called with an f-string, a `%` format, `.format(` or a `+` concatenation (unless both sides are string literals), or with a variable assigned such a string earlier in the same block. | Bind the values: `text("... :id")` with a dict, or `%s` with a tuple. |
| `foundry.py-tls-verification-disabled` | `verify=False` in any call (`httpx`, `requests`, a client or a session), `<x>.verify = False`, `ssl._create_unverified_context`, `ssl.CERT_NONE` or `<x>.check_hostname = False`. | Trust the right CA: `verify="<ca bundle path>"` or `ssl.create_default_context(cafile=...)`. |

The rules read code text and cannot follow data. They miss SQL built by `+=` or in a helper, other raw-SQL entry
points, and TLS checks turned off through a library's own option. Review those by hand.

A false positive is not silenced in the code: `# nosemgrep` comments are ignored by the runner. It is handled by a
reasoned baseline raise: `foundry quality baseline --raise "<why>"` with the `Quality-Baseline-Raise:` trailer on
the commit, reviewed by the tech lead. A project may add its own rules under `.semgrep/`; the runner loads the whole
folder.

To test the rules: `semgrep --test --config tests/fixtures/semgrep/py tests/fixtures/semgrep/py` in the Foundry
plugin repo, which keeps one annotated file per rule.

## Dependencies

Runtime packages are pinned in the project's `requirements*.txt` (or `pyproject.toml`) as `==` versions.
`foundry pack check` lists what is missing, with an install line per package (`uv add <pkg>==<v>` when `uv.lock`
exists). The quality tools are not project dependencies: they are pinned in
`scripts/foundry-quality/packs/python/requirements-tools.txt` (ruff, vulture, pip-audit, pytest-cov) and in `setup.sh`
(jscpd, from npm), and `setup.sh` installs them into the Foundry scratch area
(`<git-common-dir>/foundry/tools/python-venv`). It needs `python3` 3.10+ and `npm`. The quality hooks stop with the
setup command when the tools are missing.

## CI

Every job of `foundry-stack.yml` starts with the `foundry-setup` action, which runs `setup.sh <root> --project`: besides
the tools venv, it installs the project's `requirements*.txt` (or the `pyproject.toml` package) and
`requirements-tools.txt` into the CI Python, where the tests run.

- `foundry / coverage` runs `coverage.sh <root> <out-dir>`: `python -m pytest --cov=<python.package>
  --cov-report=lcov:<out-dir>/python.lcov` in the pack root, with the paths made repo-relative (`api/app/x.py` for the
  root `api`). The Python on PATH runs it; without `pytest-cov` it stops (exit 2) with the install line, and without
  `python.package` it stops naming that key. The job then requires every changed source file in `paths.source` (tests
  and `quality.exclude` aside) to be in a report and 80 percent of the changed lines covered, so `paths.source` must
  list the package (for example `app/**`).
- `foundry / security` runs `audit.sh <root>`: pinned `pip-audit` on the registry packages of `uv.lock` when it exists,
  else on every `requirements*.txt`, else on `pyproject.toml`. A known vulnerability with a fixed release fails; one
  with no fix yet, or a package pip-audit cannot audit, is a warning. To skip an advisory, list its ID in the
  repository variable `PIP_AUDIT_IGNORE` (spaces or commas) and add a line `<ID>  # <reason>` to
  `quality/audit-ignore.txt`; an ID without its reason line stops the audit (exit 2). Reading `uv.lock` needs Python
  3.11+.
- `foundry / image (api)` and `foundry / image (worker)` build `Dockerfile.api` and `Dockerfile.worker` with the build
  args in `foundry.json` `images` (see `fastapi.md` and `celery.md`); `foundry image args <name>` prints them. The npm
  lockfile check applies only to a Dockerfile whose folder has a `package.json`.
- The Python images are built from the repo root (or the python pack root), so in a mixed repo the build context
  holds the other pack roots too. Their `node_modules` stay out (`**/node_modules` in `Dockerfile.api.dockerignore`
  and `Dockerfile.worker.dockerignore`, and the Dockerfiles delete them for the classic builder); add the other pack
  roots, such as `dashboard/`, to that `.dockerignore` yourself so the API image does not carry the web app.

## Commands

| Command | Does |
|---|---|
| `sh scripts/foundry-quality/packs/python/setup.sh .` | installs the quality tools (pass the pack root; with none it uses the python root from `foundry.json`; `--project`, for CI, also installs the project's requirements and the tools into `python3` on PATH) |
| `sh scripts/foundry-quality/packs/python/coverage.sh . <out-dir>` | the tests with coverage, as `<out-dir>/python.lcov` |
| `sh scripts/foundry-quality/packs/python/audit.sh .` | the dependency audit |
| `foundry image args <name>` | the `--build-arg` lines `foundry.json` `images` gives `Dockerfile.<name>` |
| `sh scripts/foundry-quality/run.sh check` | ruff, the limits and architecture checkers, vulture, jscpd and Semgrep against the baseline |
| `sh scripts/foundry-quality/run.sh baseline` | records the current findings as the baseline (tech lead only) |
| `sh scripts/foundry-quality/staged.sh` | the pre-commit check: ruff and the limits on the staged `.py` files |
| `foundry pack check` | verifies the pinned runtime packages |
| `foundry pack scaffold python <component>` | copies the platform wiring into `<pkg>/platform/foundry` |

## Guarded files

`/scripts/foundry-quality/`, `/quality/`, `/.semgrep/`, `ruff-foundry.toml` and `vulture_whitelist.py` define the gate.
CODEOWNERS gives them to the tech lead; an agent proposes changes to them and does not make them.

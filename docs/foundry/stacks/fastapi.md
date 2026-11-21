# FastAPI rules

Read `python.md` first. This file adds what applies to a FastAPI API.

**FastAPI 0.115+ and Pydantic 2 required.** The scaffold and its tests target them; `foundry pack show python` says
so too.

## Skills that apply

- `foundry:fastapi-patterns` for routers, Pydantic schemas, dependencies, auth and async handlers.
- `foundry:hexagonal-architecture` for ports and adapters inside a module.
- `foundry:postgres-patterns` and `foundry:database-migrations` for the database.
- `foundry:redis-patterns` for cache, locks and rate-limit storage.
- `foundry:security-review` before any PR that adds a route, a dependency that authorises, or a secret.
- `foundry:docker-patterns` and `foundry:deployment-patterns` for the image and release.

## Layers

Routers live in `<pkg>/<module>/adapters` (HTTP is an adapter) and call `application` use cases. `domain` has no
FastAPI, Starlette or Pydantic-settings imports. Another module reaches this one only through its `public` folder.

## Scaffold wiring

`foundry pack scaffold python fastapi --repo .` shows what it would copy into `<pkg>/platform/foundry` (under the
pack's `packRoots` entry); add `--apply` to write it, or `--to <dir>` (repo-relative) for another folder. The folder
above it must be a package: create `<pkg>/platform/__init__.py` if it is missing. Each module has a `test_<module>.py`
beside it; run them with `python -m pytest <pkg>/platform/foundry`. They need `pytest`, `httpx` (for `TestClient`),
`fakeredis` and `lupa` (fakeredis runs the rate-limit Lua script with it) as dev dependencies, with exact pins.

| Module | Gives |
|---|---|
| `health.py` | `health_router(checks)`: `GET /health/live` (200 while the process is up, touches nothing) and `GET /health/ready` (503 `{"status": "unavailable"}` when a check returns false, raises or takes over 2 s, or once shutdown has started; the reason is logged, never sent) |
| `shutdown.py` | `draining_lifespan(app)` and `is_draining(app)`: readiness turns 503 as soon as `SIGTERM` or `SIGINT` arrives |
| `errors.py` | `install_error_handlers(app)`: the 422 body is `{"detail": [{"loc", "msg", "type"}]}` with no `input` and no `ctx`; any unhandled exception is 500 `{"detail": "internal error"}` and is logged |
| `body.py` | `await parse_body(request, Model, max_bytes)`: 415 unless the content type is JSON (`application/json` or `application/*+json`), 413 over `max_bytes` (counted while reading, so a chunked body or a false `Content-Length` cannot pass), 400 on malformed JSON, 422 through the safe handler |
| `body_limit.py` | `BodyLimitMiddleware` (pure ASGI): 413 for any request whose body is over `MAX_BODY_BYTES`, by `Content-Length` before the app runs and counted while the app reads it (a chunked body cannot pass); a handler that declares a Pydantic model is covered by it |
| `security_headers.py` | `SecurityHeadersMiddleware` (pure ASGI): `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`, `Cross-Origin-Resource-Policy: same-origin` and HSTS |
| `cors.py` | `cors_from_env(env, credentials=True)`: the keyword arguments for `CORSMiddleware` |
| `trust_proxy.py` | `client_ip(scope_or_request, trust_proxy)`: the client address, reading `X-Forwarded-For` only as far as `TRUST_PROXY` allows |
| `rate_limit.py` | `rate_limit(limit, window_s)`, `public`, `is_public(fn)`, `redis_client(app)` and `close_redis(app)` |

Wire it in `<pkg>/main.py`:

```python
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.platform.foundry import (
    BodyLimitMiddleware, SecurityHeadersMiddleware, close_redis, cors_from_env, draining_lifespan,
    health_router, install_error_handlers, redis_client,
)


@asynccontextmanager
async def lifespan(api: FastAPI):
    async with draining_lifespan(api):
        yield
    await close_redis(api)


async def redis_ready() -> bool:
    return bool(await redis_client(app).ping())


app = FastAPI(lifespan=lifespan)
install_error_handlers(app)
app.add_middleware(BodyLimitMiddleware)  # first: innermost, so its 413 gets the headers
app.add_middleware(CORSMiddleware, **cors_from_env(os.environ))
app.add_middleware(SecurityHeadersMiddleware)
app.include_router(health_router({"redis": redis_ready}))
```

A public route takes the limiter in either form; `@public` goes under the route decorator. Each `rate_limit(...)`
call checks `TRUST_PROXY` there and then, so a bad value fails the import of the router module:

```python
@router.post("/login", dependencies=[rate_limit(5, 60)])
@public
async def login(body: Login) -> Session: ...


@router.get("/search")
async def search(q: str, _: None = Depends(rate_limit(30, 60))) -> Results: ...
```

How the pieces behave:

- Shutdown: uvicorn, on `SIGTERM` or `SIGINT`, closes the listener, lets in-flight requests finish for up to
  `--timeout-graceful-shutdown` seconds, then runs the lifespan shutdown. Start it with
  `uvicorn app.main:app --timeout-graceful-shutdown $SHUTDOWN_TIMEOUT_S` (`Dockerfile.api` does). Release database
  and Redis connections after `draining_lifespan` exits, as above, never before the drain. Give the platform a stop
  timeout above `SHUTDOWN_TIMEOUT_S`.
- Errors: Pydantic's own messages hold no input, but a custom validator's `ValueError` message is sent as `msg`: never
  put the value in it.
- Rate limits: one counter per route and client, `rl:{<METHOD> <route path>:<ip>}`, in one atomic Lua script through
  `redis.asyncio`; the counter expires `window_s` after its first hit, and past `limit` the answer is 429 with
  `Retry-After`. A fixed window lets a caller make up to 2 x `limit` requests across a window boundary. The hash tag
  spans the whole key (the script touches one key; braces in a route template such as `/u/{id}` become parentheses,
  since Redis Cluster hashes up to the first `}`), so a busy route's counters spread over the cluster slots. The limiter fails open, with one warning a minute, only on a
  connection error, a timeout, or a failover reply (`READONLY`, `LOADING`, `CLUSTERDOWN`, `TRYAGAIN`,
  `MASTERDOWN`). Any other Redis error (a wrong password, `NOPERM`, a script error) and an unset `REDIS_URL` are 500s:
  they are bugs, not outages. `public` only marks a function (`is_public(fn)` reads it, for an auth dependency that
  lets public routes through); it changes nothing by itself.
- Client IPs: `TRUST_PROXY` unset or `0` trusts nobody, the socket peer is the client. A number N trusts N proxy hops
  (`1` takes the last `X-Forwarded-For` address). Otherwise it is a comma-separated list of addresses, CIDR ranges,
  `loopback`, `linklocal` or `uniquelocal`, and hops are skipped from the right while they are in it. "Trust all" (or a
  `/0` range such as `0.0.0.0/0`) is refused: it would let a caller pick its own rate-limit bucket. `rate_limit(...)` reads and checks `TRUST_PROXY`
  when the route is declared, so an invalid value raises `ValueError` at import and the app does not start; set it
  in the environment before `main.py` is imported, and changing it needs a restart. Run uvicorn with `--no-proxy-headers` (as
  `Dockerfile.api` does) so it does not rewrite the peer address before `client_ip` reads it.
- Headers: the strict CSP stops the Swagger UI at `/docs` from loading its scripts. Turn the docs off in production
  (`FastAPI(docs_url=None, redoc_url=None)`) or serve them from a separate app. A 500 answered by Starlette's
  outermost error middleware does not pass through `SecurityHeadersMiddleware`; its body is the fixed
  `internal error`.

## Environment

| Variable | Read by | Default and values |
|---|---|---|
| `CORS_ORIGINS` | `cors_from_env` | Unset: no origin is allowed (and the app refuses to start when `APP_ENV=production`). Comma-separated `scheme://host[:port]` entries; `*` raises while credentials are allowed |
| `APP_ENV` | `cors_from_env` | `production` makes `CORS_ORIGINS` required |
| `REDIS_URL` | `redis_client` | Required by `rate_limit` and the readiness check, e.g. `redis://redis:6379/0` |
| `TRUST_PROXY` | `rate_limit` (checked when a route declares it) through `client_ip` | Unset (trust nobody), a hop count, or a list of addresses, CIDR ranges and keywords; an invalid value stops the app at startup |
| `MAX_BODY_BYTES` | `BodyLimitMiddleware` | `1048576` (1 MiB); a whole number of bytes, 1 or more, else the app does not start. `parse_body` can only lower it for one handler |
| `HSTS_MAX_AGE` | `SecurityHeadersMiddleware` | `31536000` (one year), with `includeSubDomains`; `0` tells browsers to forget the host |
| `SHUTDOWN_TIMEOUT_S` | `Dockerfile.api` (uvicorn `--timeout-graceful-shutdown`) | `20` |

## Image

`Dockerfile.api` builds a two-stage image on `python:3.12.16-slim-bookworm`:
`docker build -f Dockerfile.api --build-arg APP_MODULE=app.main:app -t api .` (the build fails when `APP_MODULE` is
empty). Dependencies come from `uv.lock` (`uv sync --frozen --no-dev`, uv pinned by `UV_VERSION`) or else from
`requirements.txt`; with neither the build stops with a message. Keep test tools out of `requirements.txt` (put them
in `requirements-dev.txt`), or they ship in the image. The app runs as uid 10001 and cannot write its own files.
`HEALTHCHECK` calls `/health/live` with `python -c` and `urllib`, and the container listens on `PORT` (8000).
`Dockerfile.api.dockerignore` keeps `.env*`, `.git`, `tests` and local venvs out of the build context; the classic
builder ignores that file, so the Dockerfile deletes them from the context copy as well.

In CI, `foundry / image (api)` builds it with the build args from `foundry.json`, which `foundry detect` proposes when
`<pkg>/main.py` exists (otherwise set it by hand):

```json
"images": {"api": {"buildArgs": {"APP_MODULE": "app.main:app"}}}
```

`foundry image args api` prints them as `--build-arg=APP_MODULE=app.main:app`. Names are upper-case
(`[A-Z][A-Z0-9_]*`), values non-empty strings without newlines. Without `APP_MODULE` the CI build fails like a local one.

## Sentry (optional)

Error tracking is recommended, not required. To add it:

1. Add `sentry-sdk[fastapi]` to the requirements with an exact pin.
2. Call `sentry_sdk.init(dsn=os.environ.get("SENTRY_DSN"), environment=os.environ.get("APP_ENV"),
   send_default_pii=False)` in `main.py` before `FastAPI(...)`. With no `SENTRY_DSN` Sentry stays off.
3. Keep request bodies out of events (`max_request_body_size="never"`): a login body holds a password.

Check the current setup steps in the Sentry FastAPI guide before you copy this; the package changes often.

## Security rules (Semgrep)

Four rules of `.semgrep/foundry-py.yml` concern a FastAPI API (the others are in `python.md` and `celery.md`).
Each is an error; a failure line reads `semgrep foundry.py-<rule> <file>`.

| Rule | Fails on | Fix |
|---|---|---|
| `foundry.py-cors-wildcard-credentials` | A call with `allow_origins=["*"]` or `allow_origin_regex=".*"` and `allow_credentials=True` (`add_middleware(CORSMiddleware, ...)` or `CORSMiddleware(...)`). | List the allowed origins from configuration. |
| `foundry.py-body-without-validation` | A route handler (an `async def` under `@<router>.<verb>(...)`) that calls `await <request>.json()`, `.body()` or `.form()` and has no `<Model>.model_validate(`, `<Model>.model_validate_json(`, `TypeAdapter(...).validate_python(`, `TypeAdapter(...).validate_json(` or `parse_body(` call in the same function. | Declare a Pydantic model as the parameter, or validate the body in the handler. |
| `foundry.py-cookie-flags` | `<response>.set_cookie(...)` unless the call gives `httponly=True`, a `samesite` keyword and a `secure` flag that is `True`, a boolean name (`secure=is_prod`, `secure=settings.cookie_secure`) or a comparison with a string: `<expr> == "production"`, `"production" == <expr>`, `<expr> != "development"` or `"development" != <expr>`. `secure=False`, `None`, a missing `secure` and any other expression fail. | Write the three flags inline in the call; `**options` also fails. |
| `foundry.py-public-route-no-rate-limit` | A function decorated `@public` with no `rate_limit(` dependency in the route decorator's `dependencies=[...]` or in its parameters (`= Depends(rate_limit(...))` or `Annotated[..., Depends(rate_limit(...))]`). | Add the dependency, or the exemption comment below. |

An exempt public route (a health probe, say) carries this comment on the line above its decorators (other `#` lines
or decorators may sit between the comment and `@public`):

```python
# foundry: rate-limit exempt — health probe, does no work
@public
@router.get("/health")
def health() -> dict: ...
```

The dash is an em dash and the reason must not be empty. A limiter set on the router
(`APIRouter(dependencies=[...])`) or built in another variable is not visible to the rule: give the route its own
`rate_limit(...)` or write the exemption.

The rules miss: CORS options spread from another object or set through a callback; a body read through
`await request.stream()` or a sync handler; cookies set through `headers` or `**options`. Review those by hand.

## Rules to keep

- Declare every response and request as a Pydantic model; no untyped `dict` bodies.
- Inject through `Depends`; a handler does not build its own database session or Redis client.
- No blocking calls inside `async def`; use the sync form of the handler or run the call in a thread.

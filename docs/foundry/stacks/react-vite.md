# React and Vite rules

This file covers a React single-page app built with Vite and served as static files by nginx.
In a repo that also has the `ts-nest-next` pack, `typescript.md` describes the same shared
TypeScript tooling in more depth; everything a Vite app needs is here.

## Skills that apply

- `foundry:vite-patterns` for `vite.config`, env variables, the dev proxy and build output.
- `foundry:react-patterns` for hooks, state, data fetching and accessible components.
- `foundry:react-testing` for component and hook tests with Vitest.
- `foundry:e2e-testing` for Playwright journeys.
- `foundry:security-review` before any PR that handles tokens, user input or a new API call.
- `foundry:docker-patterns` and `foundry:deployment-patterns` for `Dockerfile.web` and release.

## Code limits

Every limit is an ESLint error and counts against the quality baseline: a file of 400 lines, a
function of 40 lines, complexity 10, nesting depth 3, 4 parameters and cognitive complexity 15.
A file over 300 lines is a warning: it is listed and not counted. Modules named `utils`,
`helpers` or `misc` cannot be imported; name a module after what it does. jscpd reports a
duplicated block of 70 tokens and 5 lines, knip an unused file, export or dependency. A
`jscpd:ignore-start` or `jscpd:ignore-end` comment is itself a finding (`foundry/jscpd-ignore`).

ESLint's nesting limit counts code blocks, not JSX elements, so deep markup passes (a test pins
this). Split a component when it grows past the function limit.

## JavaScript, TypeScript and `tsconfig.json`

The app may be JavaScript and JSX, TypeScript, or both. The type-checked typescript-eslint rules
run only on TypeScript files, and only when the pack root has a `tsconfig.json`; without one,
TypeScript files get every other rule. With a `tsconfig.json`, add `e2e/**/*.ts`,
`playwright.config.ts` and `src/foundry/**/*.ts` to its `include` so typed lint sees them.

## Components and hooks

The Vite rules apply to `.jsx` and `.tsx` files, and the two rules of hooks to custom hooks in
`src/**/*.{js,ts}` too:

- `react-hooks` recommended: `rules-of-hooks` and the React Compiler checks are errors;
  `exhaustive-deps` is a warning (listed, not counted). A hook called in a condition fails, in a
  component or in a custom hook such as `src/hooks/use-settings.js`.
- `react-refresh/only-export-components` is an error: a component file exports components, so
  fast refresh keeps state. Move constants and helpers that other files import to their own file.
- `jsx-a11y` recommended: interactive elements are real buttons and links with labels.

Data comes in through hooks. Files under `src/components/` and `src/pages/` never import
`api-client/`, `adapters/` or `db/` folders; `ui-not-data-clients` fails it. Put the calls in
`src/api-client/`, wrap them in a hook (`src/hooks/use-settings.js`), and pass plain data down.
Import cycles fail `no-circular`.

## Security baseline

| Item | In a Vite app |
| --- | --- |
| Security headers | nginx sends them on every response (`nginx.conf`, below) |
| Content-Security-Policy | `script-src 'self'` with no hash or nonce: the build has no inline script |
| Secrets | none in the browser: every `VITE_` variable is public (`foundry.vite-public-secret`) |
| API responses | validated at the trust boundary with `parseResponse(schema, response)` |
| Settings | validated once at startup with `readEnv(schema)` |
| CORS | not applicable: static. The API decides which origins may call it |
| Rate limiting | not applicable: static. The API limits its own routes |
| Request validation | not applicable: static. The API validates every body it receives |

### Content-Security-Policy and Vite

`nginx.conf` sends:

```text
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;
connect-src 'self' ${API_ORIGIN}; frame-ancestors 'none'; base-uri 'self'; form-action 'self';
object-src 'none'
```

- `vite build` writes the entry as `<script type="module" src="/assets/...">`, so
  `script-src 'self'` needs no hash. Keep it that way: set `build.modulePreload.polyfill: false`
  in `vite.config` (Vite would otherwise add a polyfill script), and add no inline `<script>` to
  `index.html`. A test checks the built `index.html`.
- `style-src` allows `'unsafe-inline'` because React's `style={...}` props are inline styles.
- `img-src` allows `data:` because Vite inlines small images. It also inlines fonts under 4 KiB,
  which `default-src` blocks: set `build.assetsInlineLimit` to keep fonts as files.
- `connect-src` allows this host and `API_ORIGIN`. A third-party script or API (analytics,
  maps, error tracking) needs its origin added to `nginx.conf`, reviewed by the tech lead.

The Vite dev server sends no CSP; run the image (or the e2e job) to see the real policy.

## Security rules (Semgrep)

`foundry.vite-public-secret` (`.semgrep/foundry-vite.yml`) fails any `VITE_*` name that
contains `SECRET`, `PRIVATE`, `PASSWORD` or `SERVICE_ROLE`, wherever it appears in the source
(`import.meta.env.X`, destructuring, `?.`, a cast, a `readEnv` schema key), in a `.env` file, in a
Dockerfile `ARG` or in `foundry.json` `images.<name>.buildArgs`. Vite writes every `VITE_` variable into the JavaScript it ships,
so such a value reaches every browser. Keep it in the API and call the API. A publishable key
(`VITE_STRIPE_PUBLISHABLE_KEY`, a Supabase anon key) is meant to be public and passes.

## Scaffold

`foundry pack scaffold react-vite web --repo . --apply` copies the baseline into `src/foundry`
(`--to <dir>` picks another folder). Take the quality baseline first, then scaffold: the
scaffold passes every check on its own. Nothing is wired for you.

| File | What it is |
| --- | --- |
| `api.ts` | `parseResponse(schema, response)`: the parsed body, or `ApiStatusError` / `ApiShapeError` |
| `env.ts` | `readEnv(schema)`: validates `import.meta.env` once, or throws `EnvError` |

Each has a `*.spec.ts` for Vitest. `ApiShapeError` names the paths that differ
(`items.2.price`) and `EnvError` the settings, never a value: messages end up in logs and error
trackers. Neither puts the URL's query string in a message.

```ts
// src/config.ts
import { z } from 'zod';
import { readEnv } from './foundry/env';

export const env = readEnv(z.object({ VITE_API_URL: z.url() }));

// src/api-client/me.ts
import { z } from 'zod';
import { parseResponse } from '../foundry/api';
import { env } from '../config';

const me = z.object({ id: z.string(), name: z.string() });
export const fetchMe = async () => parseResponse(me, await fetch(`${env.VITE_API_URL}/me`));
```

In JavaScript, import the same functions from the `.ts` files: Vite and Vitest compile them.

## Commands

```sh
npm run dev                            # dev server
npm test                               # unit tests (vitest run)
npm run test:coverage                  # tests plus coverage/lcov.info, run by CI
npm run build && npx playwright test   # end-to-end journeys against vite preview
sh scripts/foundry-quality/run.sh check   # quality gate against the baseline
```

Vitest runs every `*.spec.*` and `*.test.*` file by default, including the Playwright journeys in
`e2e/`. Keep it to `src/` in `vite.config`:
`test: { include: ['src/**/*.{test,spec}.{js,jsx,ts,tsx}'] }`.

## Serving: `nginx.conf` and `Dockerfile.web`

`Dockerfile.web` runs `npm ci` and `npm run build` on Node 22, then copies `dist/` into
`nginxinc/nginx-unprivileged`, which runs as user 101 on port 8080. `nginx.conf` becomes the
image's envsubst template: at start, `${API_ORIGIN}` is replaced from the container's
environment (`docker run -e API_ORIGIN=https://api.example.com`); empty means only this host.
It is written into the header as given, so set it to space-separated origins only.

- Every path that is not a file gets `index.html`, so a deep link such as `/settings` loads the
  app. A missing `/assets/` file is a 404.
- `/assets/` files are hashed and cached for a year (`immutable`); `index.html` is `no-cache`.
  An error response gets no Cache-Control, so the 404 for a new hash that reaches an old server
  during a rolling deploy is not cached.
- `/healthz` answers 200 `ok` and is the image's `HEALTHCHECK`.
- HSTS is `max-age=63072000; includeSubDomains`. Serve the app over HTTPS only, and remove
  `includeSubDomains` in `nginx.conf` if a subdomain still serves plain HTTP.
- Env files never reach the build. Build-time values (`VITE_API_URL`) come in as build args: add
  `ARG VITE_API_URL` before `npm run build` in `Dockerfile.web`, and list it under
  `images.web.buildArgs` in `foundry.json` for `foundry / image (web)`.
- Every response carries the headers: they are set at server level, because an `add_header` in
  a `location` drops the others there.

`nginx.conf` and `Dockerfile.web` are code-owned by the tech lead.

## CI workflows

- `foundry-stack.yml` (`foundry / verify`, `foundry / coverage`, `foundry / quality`,
  `foundry / security`) runs this pack's hooks under `scripts/foundry-quality/packs/react-vite/`.
- `foundry / coverage` runs `npm run test:coverage`, which must write `coverage/lcov.info` in the
  pack root, for example `"test:coverage": "vitest run --coverage --coverage.reporter=lcov"` with
  `@vitest/coverage-v8` (pinned by the pack to the Vitest version; `foundry pack check` prints the
  install line). The hook copies it to `coverage/react-vite.lcov` with repo paths
  (`dashboard/src/...` when the pack sits in `dashboard/`). Add `coverage/` to `.gitignore`.
  Every changed file under `paths.source` (`detect` proposes `<root>/src/**` for a pack in a
  folder) must appear in it, and the changed lines need 80% coverage.
- `foundry / security` runs `npm audit --audit-level=high` in the pack root.
- `foundry-e2e-vite.yml` (`foundry / e2e (vite)`) builds `Dockerfile.web`, runs it and points
  Playwright at it with `E2E_BASE_URL`, so the journeys see the real headers. Add one spec per
  critical journey in `e2e/`. Without `E2E_BASE_URL`, `npx playwright test` serves the last
  build with `vite preview` (which sends no security headers).
- `foundry-image.yml` builds and scans the image as `foundry / image (web)`.

Every job runs in the pack root from `foundry.json` `packRoots` (for example `dashboard`).

### Your own ESLint config

`eslint.config.mjs` loads Foundry's rules. A repo that keeps its own flat config spreads
Foundry's after its own entries, so Foundry's limits win; the gate fails when the active config
does not carry them:

```js
import { foundryConfig } from './foundry/lint/project.mjs';

const mine = [/* your entries */];

export default [...mine, ...(await foundryConfig())];
```

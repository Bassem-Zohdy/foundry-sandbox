# TypeScript tooling for the TypeScript packs' hooks. Source it after lib.sh.
#
# ts_tools   ESLint (after checking the config carries Foundry's limits), knip, jscpd (and its
#            ignore markers) and dependency-cruiser in the pack root, each written as
#            <q>/<pack>-<tool>.sarif;
# ts_staged  ESLint on the staged files under the pack root;
#            both run once per root: the first TypeScript pack there by name runs them;
# ts_setup, ts_coverage, ts_audit   the install, coverage and audit steps CI runs.
#
# Each entry point changes into the pack root and puts its node_modules/.bin first on PATH. A
# hook sets `ts_doc` to its stack doc (default typescript.md) for the messages that point at it.
# Tool output that is not SARIF goes to <q>/tmp/<pack>/. Another pack's root inside this one
# (FOUNDRY_OTHER_ROOTS) is left to that pack.

ts_enter() { # pack q root
  pack=$1 q=$2 root=$3
  work="$q/tmp/$pack"
  mkdir -p "$work"
  cd "$repo/$root" || fail "pack $pack: its root $root is not a folder"
  PATH="$PWD/node_modules/.bin:$PATH"
}

# Two TypeScript packs at one root share eslint.config.mjs, knip.json, .jscpd.json and
# .dependency-cruiser.cjs, so running the tools for both would count every file twice. The first
# of them by name runs the tools; for the others this fails and sets `owner` to that pack. A
# TypeScript pack is one whose tools.sh calls ts_tools.
ts_runs_shared_tools() {
  (cd "$repo" && foundry pack roots) >"$work/roots.tsv" ||
    fail "the pack roots could not be read from foundry.json"
  owner=$(tab=$(printf '\t')
    while IFS="$tab" read -r other other_root; do
      if [ "$other_root" = "$root" ] &&
        grep -q '^ts_tools ' "$repo/scripts/foundry-quality/packs/$other/tools.sh" 2>/dev/null; then
        printf '%s\n' "$other"
      fi
    done <"$work/roots.tsv" | LC_ALL=C sort | head -n 1)
  [ -z "$owner" ] || [ "$owner" = "$pack" ]
}

ts_covered_by_owner() {
  echo "foundry-quality: $pack: the TypeScript tools in $root are covered by $owner (same root)"
}

# The repo has TypeScript or JavaScript sources here, so a tool that looked at none is broken.
sources_exist() {
  # shellcheck disable=SC2046 # pack roots hold no spaces or glob characters
  [ -n "$(git ls-files -- '*.ts' '*.tsx' '*.js' '*.jsx' '*.mjs' '*.cjs' $(nested_pathspecs) | head -n 1)" ]
}

# A repo's own eslint.config.mjs (a takeover keeps it: template apply never overwrites) may not
# load Foundry's rules; the gate would then pass on code that breaks every limit.
ts_require_limits() {
  # shellcheck disable=SC2046
  git ls-files -z -- '*.ts' '*.tsx' '*.mts' '*.cts' '*.js' '*.jsx' '*.mjs' '*.cjs' $(nested_pathspecs) \
    >"$work/lint-files.list" || fail "git could not list the source files"
  status=0
  node "$repo/scripts/foundry-quality/lib/eslint-limits.mjs" "$work/lint-files.list" \
    2>"$work/eslint-limits.err" || status=$?
  [ "$status" -eq 0 ] && return 0
  cat "$work/eslint-limits.err" >&2
  [ "$status" -eq 1 ] || fail "the ESLint config could not be read"
  fail "the active ESLint config does not carry Foundry's limits; spread foundryConfig() from foundry/lint/project.mjs into eslint.config.mjs (docs/foundry/stacks/${ts_doc:-typescript.md}, \"Your own ESLint config\")"
}

ts_eslint() {
  ts_require_limits
  set --
  for nested in $(nested_roots); do set -- "$@" --ignore-pattern "$nested/**"; done
  status=0
  eslint . --no-warn-ignored "$@" -f @microsoft/eslint-formatter-sarif \
    -o "$q/$pack-eslint.sarif" 2>"$work/eslint.err" || status=$?
  finished eslint "$status" 1 "$q/$pack-eslint.sarif"
  # ESLint writes absolute file URIs, which `foundry quality` makes repo-relative: no prefix.
  require_inspected eslint "$q/$pack-eslint.sarif" \
    'sum(len(r.get("artifacts") or []) for r in d.get("runs") or [])'
}

# knip.json plus the nested pack roots, left out of `entry` and `project` (so knip does not
# analyse them) and of `ignore`. knip resolves the globs against the pack root (the cwd), not the
# config's folder, so the merged config can live in the scratch area. An unreadable knip.json is
# copied as it is, so knip fails on it with its own message.
ts_knip_config() {
  node - "$work/knip-config.json" <<'JS' 2>"$work/knip-config.err" ||
const fs = require('fs');
const text = fs.readFileSync('knip.json', 'utf8');
let config;
try {
  config = JSON.parse(text);
} catch {
  fs.writeFileSync(process.argv[2], text);
  process.exit(0);
}
const nested = (process.env.FOUNDRY_OTHER_ROOTS || '').split('\n').filter(Boolean).map((r) => `${r}/**`);
const add = (key, globs) => [].concat(config[key] ?? [], globs);
if (nested.length > 0) {
  config.entry = add('entry', nested.map((g) => `!${g}`));
  config.project = add('project', nested.map((g) => `!${g}`));
  config.ignore = add('ignore', nested);
}
fs.writeFileSync(process.argv[2], JSON.stringify(config));
JS
    { cat "$work/knip-config.err" >&2; fail "knip config could not be built (knip.json unreadable)"; }
}

# knip reports nothing, not an error, when its `project` globs match no file (sources moved).
ts_require_knip_project() {
  # shellcheck disable=SC2046
  git ls-files -z -- $(nested_pathspecs) >"$work/tracked.list" ||
    fail "git could not list the tracked files"
  n=$(node - "$work/tracked.list" "$work/knip-config.json" <<'JS'
const fs = require('fs');
let globs;
try {
  globs = [].concat(JSON.parse(fs.readFileSync(process.argv[3], 'utf8')).project ?? '**/*.{js,cjs,mjs,jsx,ts,cts,mts,tsx}');
} catch {
  console.log(-1); // unreadable: knip itself crashes on it, with its own message
  process.exit(0);
}
const toRe = (glob) =>
  new RegExp(
    '^' +
      glob
        .replace(/[.+^$()|[\]\\]/g, '\\$&')
        .replace(/\{([^}]*)\}/g, (_, alts) => '(' + alts.split(',').join('|') + ')')
        .replace(/\*\*\//g, '\0')
        .replace(/\*\*/g, '.*')
        .replace(/\*/g, '[^/]*')
        .replace(/\?/g, '[^/]')
        .replace(/\0/g, '(.*/)?') +
      '$',
  );
const take = globs.filter((g) => !g.startsWith('!')).map(toRe);
const skip = globs.filter((g) => g.startsWith('!')).map((g) => toRe(g.slice(1)));
const files = fs.readFileSync(process.argv[2], 'utf8').split('\0').filter(Boolean);
console.log(files.filter((f) => take.some((r) => r.test(f)) && !skip.some((r) => r.test(f))).length);
JS
  ) || fail "knip.json project globs could not be checked"
  if [ "$n" -eq 0 ] && sources_exist; then
    fail "knip looked at no files: no tracked file matches the \"project\" globs in knip.json"
  fi
}

ts_knip() {
  ts_knip_config
  ts_require_knip_project
  status=0
  knip -c "$work/knip-config.json" --reporter json >"$work/knip.json" 2>"$work/knip.err" || status=$?
  finished knip "$status" 1 "$work/knip.json"
  to_sarif knip "$work/knip.json" "$q/$pack-knip.sarif" "$root"
}

jscpd_run() { # config
  jscpd --config "$1" --silent .
}

ts_jscpd() {
  # jscpd cannot read foundry.json, so the exclude globs (made relative to this root by
  # foundry/arch/config.cjs) and the nested pack roots are merged into its config here.
  node - "$work/jscpd" <<'JS' >"$work/jscpd-config.json" 2>"$work/jscpd.err" || true
const fs = require('fs');
const path = require('path');
const read = (file) => {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (error) {
    if (error.code === 'ENOENT') return {};
    throw error;
  }
};
const config = read('.jscpd.json');
const { exclude } = require(path.resolve('foundry/arch/config.cjs')).loadFoundry();
const nested = (process.env.FOUNDRY_OTHER_ROOTS || '').split('\n').filter(Boolean).map((r) => `${r}/**`);
config.ignore = [...(config.ignore || []), ...exclude, ...nested];
config.output = process.argv[2];
process.stdout.write(JSON.stringify(config));
JS
  [ -s "$work/jscpd-config.json" ] ||
    fail "jscpd config could not be built (.jscpd.json, foundry.json or foundry/arch/config.cjs unreadable)"
  status=0
  jscpd_run "$work/jscpd-config.json" >"$work/jscpd.log" 2>"$work/jscpd.err" || status=$?
  finished jscpd "$status" 0 "$work/jscpd/jscpd-report.json"
  require_jscpd_inspected "$work/jscpd/jscpd-report.json" "$work/jscpd-config.json"
  to_sarif jscpd "$work/jscpd/jscpd-report.json" "$q/$pack-jscpd.sarif" "$root"
}

# jscpd skips the code between `jscpd:ignore-start` and `-end` comments and has no switch to turn
# that off, so each marker in the root's sources (tracked, or untracked and not ignored) is an
# error of its own (rule foundry/jscpd-ignore, counted under jscpd); `foundry quality` drops the
# quality.exclude ones.
ts_jscpd_markers() {
  # shellcheck disable=SC2046
  git ls-files -z -co --exclude-standard -- '*.ts' '*.tsx' '*.mts' '*.cts' '*.js' '*.jsx' '*.mjs' \
    '*.cjs' $(nested_pathspecs) >"$work/marker-files.list" || fail "git could not list the source files"
  node - "$work/marker-files.list" "$root" >"$q/$pack-jscpd-ignore.sarif" \
    2>"$work/jscpd-ignore.err" <<'JS' ||
const fs = require('fs');
const marker = /jscpd\s*:\s*ignore-(start|end)/gi;
const lead = process.argv[3] === '.' ? '' : `${process.argv[3]}/`;
const results = [];
for (const file of fs.readFileSync(process.argv[2], 'utf8').split('\0').filter(Boolean)) {
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (error) {
    if (error.code === 'ENOENT') continue; // deleted in the working tree
    throw error;
  }
  text.split('\n').forEach((line, i) => {
    for (const m of line.matchAll(marker)) {
      results.push({
        ruleId: 'foundry/jscpd-ignore',
        level: 'error',
        message: { text: `${m[0]} hides code from the duplication check; remove it` },
        locations: [{ physicalLocation: { artifactLocation: { uri: lead + file },
          region: { startLine: i + 1, startColumn: m.index + 1 } } }],
      });
    }
  });
}
const rule = { id: 'foundry/jscpd-ignore', defaultConfiguration: { level: 'error' } };
process.stdout.write(JSON.stringify({ version: '2.1.0',
  runs: [{ tool: { driver: { name: 'jscpd', rules: [rule] } }, results }] }));
JS
    { cat "$work/jscpd-ignore.err" >&2; fail "the jscpd ignore markers could not be checked"; }
}

# The nested pack roots reach .dependency-cruiser.cjs through FOUNDRY_OTHER_ROOTS: a CLI
# --exclude would replace the config's own exclude (quality.exclude).
ts_depcruise() {
  [ -f .dependency-cruiser.cjs ] || return 0
  dirs=""
  for d in src app; do [ -d "$d" ] && dirs="$dirs $d"; done
  # The exit code is the number of violations, so it cannot tell a crash. A crash writes no
  # JSON, or JSON without a `summary`; both exit 2.
  # shellcheck disable=SC2086
  depcruise $dirs --config .dependency-cruiser.cjs --output-type json \
    >"$work/depcruise.json" 2>"$work/depcruise.err" || true
  if [ ! -s "$work/depcruise.json" ]; then
    if [ -f "$work/depcruise.err" ]; then cat "$work/depcruise.err" >&2; fi
    fail "depcruise crashed or wrote no output"
  fi
  has_summary=$(count "$work/depcruise.json" '1 if isinstance(d.get("summary"), dict) else 0') ||
    fail "depcruise output cannot be read"
  [ "$has_summary" -eq 1 ] || fail "depcruise crashed or wrote output without a summary"
  require_inspected depcruise "$work/depcruise.json" '(d.get("summary") or {}).get("totalCruised")'
  to_sarif depcruise "$work/depcruise.json" "$q/$pack-depcruise.sarif" "$root"
}

ts_tools() { # pack q root
  ts_enter "$1" "$2" "$3"
  ts_runs_shared_tools || { ts_covered_by_owner; return 0; }
  ts_eslint
  ts_knip
  ts_jscpd
  ts_jscpd_markers
  ts_depcruise
}

# The staged files arrive repo-relative; ESLint runs in the root on the TypeScript and
# JavaScript ones. With none of them staged it writes nothing and passes.
ts_staged() { # pack q root file...
  ts_enter "$1" "$2" "$3"
  shift 3
  ts_runs_shared_tools || { ts_covered_by_owner; return 0; }
  lead=""
  if [ "$root" != . ]; then lead="$root/"; fi
  for file do
    shift
    case $file in
      *.ts | *.tsx | *.js | *.jsx | *.mjs | *.cjs) set -- "$@" "${file#"$lead"}" ;;
    esac
  done
  [ "$#" -gt 0 ] || return 0
  status=0
  eslint --no-warn-ignored -f @microsoft/eslint-formatter-sarif \
    -o "$q/$pack-staged-eslint.sarif" -- "$@" 2>"$work/eslint-staged.err" || status=$?
  if [ "$status" -gt 1 ] || [ ! -s "$q/$pack-staged-eslint.sarif" ]; then
    cat "$work/eslint-staged.err" >&2
    fail "eslint crashed or wrote no output"
  fi
}

# Sets ts_pm to npm or pnpm from the root's single lockfile, or fails the CI step.
ts_package_manager() {
  if [ -f yarn.lock ] || { [ -f package-lock.json ] && [ -f pnpm-lock.yaml ]; } \
    || { [ ! -f package-lock.json ] && [ ! -f pnpm-lock.yaml ]; }; then
    echo "::error::$PWD: Need exactly one of package-lock.json or pnpm-lock.yaml (yarn is not supported)"
    exit 1
  fi
  if [ -f package-lock.json ]; then ts_pm=npm; else ts_pm=pnpm; fi
  if [ "$ts_pm" = pnpm ]; then corepack enable; fi
}

ts_setup() { # root
  cd "$repo/$1" || fail "pack root $1 is not a folder"
  ts_package_manager
  if [ "$ts_pm" = npm ]; then npm ci; else pnpm install --frozen-lockfile; fi
}

ts_audit() { # root
  cd "$repo/$1" || fail "pack root $1 is not a folder"
  ts_package_manager
  "$ts_pm" audit --audit-level=high
}

# The `test:coverage` script must write coverage/lcov.info in the root; its paths are copied
# to <out>/<pack>.lcov relative to the repo root.
ts_coverage() { # pack root out
  mkdir -p "$3"
  out=$(cd "$3" && pwd)
  cd "$repo/$2" || fail "pack root $2 is not a folder"
  ts_package_manager
  if [ "$(node -p "Boolean(require('./package.json').scripts?.['test:coverage'])")" != "true" ]; then
    echo "::error::$2/package.json has no \"test:coverage\" script; it must run the tests and write coverage/lcov.info (see docs/foundry/stacks/${ts_doc:-typescript.md})"
    exit 1
  fi
  rm -rf coverage
  "$ts_pm" run test:coverage
  if ! grep -q '^SF:' coverage/lcov.info 2>/dev/null; then
    echo "::error::test:coverage wrote no $2/coverage/lcov.info with source files; set the coverage reporter to lcov"
    exit 1
  fi
  lead=""
  if [ "$2" != . ]; then lead="$2/"; fi
  sed -e 's|^SF:\./|SF:|' -e "s|^SF:\([^/]\)|SF:$lead\1|" coverage/lcov.info >"$out/$1.lcov"
}

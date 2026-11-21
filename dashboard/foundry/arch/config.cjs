// Reads foundry.json each time ESLint or dependency-cruiser runs, so editing it changes the rules
// with no re-render. foundry/lint/config.mjs loads this file too, so both read it the same way.
const { existsSync, readFileSync } = require('node:fs');
const { dirname, relative, resolve, sep } = require('node:path');

/** The pack root (tsconfig.json, package.json): foundry/arch/ sits two levels below it. */
const root = resolve(__dirname, '..', '..');

/**
 * foundry.json: in the pack root, or above it when the pack sits in a folder of the repo. The
 * search stops at the repo root (the folder holding .git).
 */
function findFoundryJson(start) {
  for (let dir = start; ; dir = dirname(dir)) {
    const path = resolve(dir, 'foundry.json');
    // eslint-disable-next-line security/detect-non-literal-fs-filename -- folders above this file
    if (existsSync(path)) return path;
    // eslint-disable-next-line security/detect-non-literal-fs-filename -- folders above this file
    if (existsSync(resolve(dir, '.git')) || dirname(dir) === dir) {
      throw new Error(`foundry.json not found in ${start} or above it; the rules are read from it`);
    }
  }
}

function readJson(path) {
  try {
    // eslint-disable-next-line security/detect-non-literal-fs-filename -- found by findFoundryJson
    return JSON.parse(readFileSync(path, 'utf8'));
  } catch (error) {
    throw new Error(`foundry.json is not readable JSON: ${error.message}`);
  }
}

function strings(value) {
  return Array.isArray(value) ? value.filter((item) => typeof item === 'string') : [];
}

/**
 * quality.exclude globs are repo-relative, but the tools here see paths relative to the pack
 * root. A glob under the pack's folder loses that prefix, a glob starting with `**` applies
 * anywhere, and a glob for another folder does not apply here.
 */
function excludeForRoot(globs, prefix) {
  if (prefix === '') return globs;
  return globs.flatMap((glob) => {
    if (glob.startsWith(`${prefix}/`)) return [glob.slice(prefix.length + 1)];
    return glob.startsWith('**') ? [glob] : [];
  });
}

// The stacks each TypeScript pack lints. In a repo with two of them (a Nest API at the root and a
// Vite app in dashboard/), each root loads only the rules of the packs placed there, so the root
// never imports the Vite plugins that only dashboard/ installs.
const PACK_STACKS = new Map([
  ['ts-nest-next', ['ts-nest', 'ts-next']],
  ['react-vite', ['react-vite']],
]);

/** `./web/` -> `web`, `.` and `./` -> `.` (the same cleaning as foundry.json validation). */
const cleanRoot = (dir) => dir.split('/').filter((part) => part !== '' && part !== '.').join('/') || '.';

/** The listed stacks that belong to a TypeScript pack at this root; all of them when none is. */
function stacksForRoot(data, prefix) {
  const stacks = strings(data.stacks);
  const roots = new Map(Object.entries(data.packRoots ?? {}));
  const here = strings(data.packs).filter(
    (pack) => PACK_STACKS.has(pack) && cleanRoot(String(roots.get(pack) ?? '.')) === (prefix || '.'),
  );
  if (here.length === 0) return stacks;
  const owned = new Set(here.flatMap((pack) => PACK_STACKS.get(pack)));
  return stacks.filter((stack) => owned.has(stack));
}

/** `stacks` (of this root's packs), `modules` and `quality.exclude` from foundry.json. */
function loadFoundry() {
  const path = findFoundryJson(root);
  const data = readJson(path);
  const prefix = relative(dirname(path), root).split(sep).join('/');
  return {
    stacks: stacksForRoot(data, prefix),
    modules: strings(data.modules),
    exclude: excludeForRoot(strings(data.quality?.exclude), prefix),
  };
}

const escapeRegex = (text) => text.replace(/[.+^${}()|[\]\\]/g, '\\$&');

/** A quality.exclude glob (`**`, `*`, `?`) as an anchored regular expression string. */
function globToRegex(glob) {
  const body = escapeRegex(glob)
    .replace(/\*\*\//g, '<any-dirs>')
    .replace(/\*\*/g, '<anything>')
    .replace(/\*/g, '[^/]*')
    .replace(/\?/g, '[^/]')
    .replace(/<any-dirs>/g, '(.*/)?')
    .replace(/<anything>/g, '.*');
  return `^${body}$`;
}

module.exports = { root, findFoundryJson, loadFoundry, globToRegex, escapeRegex };

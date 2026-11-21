// Fails when the active ESLint config does not carry Foundry's code limits, as when a takeover
// repo keeps its own eslint.config.mjs. Asks ESLint for the config of the first linted file.
// Run it in the pack root: ESLint, its config and the listed paths are taken from there.
// Usage: node <repo>/scripts/foundry-quality/lib/eslint-limits.mjs <file holding NUL-separated paths>
// Exit 0 ok (or no file is linted), 1 limits missing, 2 ESLint or the list could not be read.
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

// The pack root's ESLint, not one next to this script: a pack in a folder installs its own.
async function loadEslint() {
  const fromRoot = createRequire(join(process.cwd(), 'package.json'));
  return (await import(pathToFileURL(fromRoot.resolve('eslint')).href)).ESLint;
}

const EXPECTED = [
  ['max-lines', 2, 400],
  ['foundry-soft/max-lines', 1, 300],
  ['max-lines-per-function', 2, 40],
  ['complexity', 2, 10],
  ['max-depth', 2, 3],
  ['max-params', 2, 4],
  ['sonarjs/cognitive-complexity', 2, 15],
];
const LEVELS = new Map([
  ['off', 0],
  ['warn', 1],
  ['error', 2],
]);

function level(entry) {
  const value = Array.isArray(entry) ? entry[0] : entry;
  return typeof value === 'string' ? LEVELS.get(value) : value;
}

function limit(entry) {
  const option = Array.isArray(entry) ? entry[1] : undefined;
  return typeof option === 'object' && option !== null ? option.max : option;
}

function gaps(rules) {
  const active = new Map(Object.entries(rules));
  return EXPECTED.filter(([name, sev, max]) => level(active.get(name)) !== sev || limit(active.get(name)) !== max).map(
    ([name, sev, max]) => `${name} ${max} (${sev === 2 ? 'error' : 'warning'})`,
  );
}

async function firstLinted(eslint, files) {
  for (const file of files) {
    if (await eslint.isPathIgnored(file)) continue;
    const config = await eslint.calculateConfigForFile(file);
    if (config) return { file, rules: config.rules ?? {} };
  }
  return null;
}

async function main() {
  const files = readFileSync(process.argv[2], 'utf8').split('\0').filter(Boolean);
  const ESLint = await loadEslint();
  const found = await firstLinted(new ESLint(), files);
  if (!found) return 0;
  const missing = gaps(found.rules);
  if (missing.length === 0) return 0;
  console.error(`${found.file}: missing ${missing.join(', ')}`);
  return 1;
}

main().then(
  (code) => {
    process.exitCode = code;
  },
  (error) => {
    console.error(String(error));
    process.exitCode = 2;
  },
);

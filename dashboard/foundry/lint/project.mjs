import { base } from './base.mjs';
import { loadFoundry } from './config.mjs';

const generated = [
  'dist/**',
  'build/**',
  'coverage/**',
  '.next/**',
  'next-env.d.ts',
  'playwright-report/**',
  'test-results/**',
];

// A stack's rules load only when foundry.json lists the stack, so a Nest-only repo never
// imports the Next or Vite plugins.
async function stackRules(stacks) {
  const out = [];
  if (stacks.includes('ts-nest')) out.push(...(await import('./nest.mjs')).nest);
  if (stacks.includes('ts-next')) out.push(...(await import('./next.mjs')).next);
  if (stacks.includes('react-vite')) out.push(...(await import('./vite.mjs')).vite);
  return out;
}

/**
 * Foundry's whole ESLint config, read from foundry.json each run. A repo with its own flat
 * config spreads it after its own entries, so Foundry's limits win (see typescript.md).
 */
export async function foundryConfig() {
  const { stacks, exclude } = loadFoundry();
  // next.mjs and vite.mjs import plugins that a repo without that stack does not install.
  const unused = [
    ...(stacks.includes('ts-next') ? [] : ['foundry/lint/next.mjs']),
    ...(stacks.includes('react-vite') ? [] : ['foundry/lint/vite.mjs']),
  ];
  return [{ ignores: [...generated, ...unused, ...exclude] }, ...base, ...(await stackRules(stacks))];
}

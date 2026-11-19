// Architecture rules. The logic lives in foundry/arch/; it reads foundry.json when this runs.
const { existsSync } = require('node:fs');
const { resolve } = require('node:path');
const { root, loadFoundry, globToRegex, escapeRegex } = require('./foundry/arch/config.cjs');
const { buildRules } = require('./foundry/arch/rules.cjs');

const foundry = loadFoundry();
// Another pack's root inside this one belongs to that pack (the quality hook sets this).
const nested = (process.env.FOUNDRY_OTHER_ROOTS || '').split('\n').filter(Boolean);
const exclude = [...foundry.exclude.map(globToRegex), ...nested.map((dir) => `^${escapeRegex(dir)}/`)];
// eslint-disable-next-line security/detect-non-literal-fs-filename -- a fixed path in this repo
const tsconfig = existsSync(resolve(root, 'tsconfig.json'));

module.exports = {
  forbidden: buildRules(foundry),
  options: {
    doNotFollow: { path: 'node_modules' },
    ...(exclude.length > 0 && { exclude: { path: exclude } }),
    ...(tsconfig && { tsConfig: { fileName: 'tsconfig.json' } }),
    tsPreCompilationDeps: true,
    enhancedResolveOptions: {
      exportsFields: ['exports'],
      conditionNames: ['import', 'require', 'node', 'default', 'types'],
      mainFields: ['module', 'main', 'types', 'typings'],
    },
  },
};

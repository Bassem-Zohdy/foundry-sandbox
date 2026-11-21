import { existsSync } from 'node:fs';
import { join } from 'node:path';
import js from '@eslint/js';
import nodePlugin from 'eslint-plugin-n';
import security from 'eslint-plugin-security';
import sonarjs from 'eslint-plugin-sonarjs';
import globals from 'globals';
import tseslint from 'typescript-eslint';
import { root } from './config.mjs';
import { limits, noGrabBags } from './limits.mjs';
import { softMaxLines } from './soft-max-lines.mjs';

const scripts = ['**/*.{js,jsx,mjs,cjs}'];
const typescript = ['**/*.{ts,tsx,mts,cts}'];
const nodeFiles = ['**/*.{ts,mts,cts,js,mjs,cjs}'];

// The TypeScript compiler already checks imports, so the plugin's resolver rules would only
// report false positives on TypeScript paths.
const typescriptOwnsImports = {
  files: typescript,
  rules: {
    'n/no-missing-import': 'off',
    'n/no-missing-require': 'off',
    'n/no-unpublished-import': 'off',
    'n/no-extraneous-import': 'off',
    'n/no-unsupported-features/es-syntax': 'off',
  },
};

// The type-checked rules need TypeScript's project: they run on TypeScript files of a root with a
// tsconfig.json. JavaScript (a Vite dashboard in .jsx, say) and a root without one are linted
// with every other rule, so ESLint never stops on a file no project includes.
// eslint-disable-next-line security/detect-non-literal-fs-filename -- the pack root's tsconfig
const hasTsconfig = existsSync(join(root, 'tsconfig.json'));

const typed = {
  files: typescript,
  languageOptions: {
    parserOptions: { projectService: true, tsconfigRootDir: root },
  },
};

const untypedFiles = hasTsconfig ? scripts : [...scripts, ...typescript];
const untyped = { files: untypedFiles, ...tseslint.configs.disableTypeChecked };

// A .cjs file is CommonJS by definition (the dependency-cruiser config must be one), so
// `require` is how it imports.
const commonJs = { files: ['**/*.cjs'], rules: { '@typescript-eslint/no-require-imports': 'off' } };

const nodeGlobals = { languageOptions: { globals: globals.node } };

function onlyFor(files, config) {
  return { ...config, files };
}

export const base = [
  js.configs.recommended,
  ...tseslint.configs.recommendedTypeChecked,
  ...(hasTsconfig ? [typed] : []),
  untyped,
  sonarjs.configs.recommended,
  onlyFor(nodeFiles, nodePlugin.configs['flat/recommended']),
  onlyFor(nodeFiles, security.configs.recommended),
  nodeGlobals,
  commonJs,
  typescriptOwnsImports,
  limits,
  softMaxLines,
  noGrabBags,
];

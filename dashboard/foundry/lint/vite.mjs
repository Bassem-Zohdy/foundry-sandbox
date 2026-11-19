import jsxA11y from 'eslint-plugin-jsx-a11y';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import globals from 'globals';

const files = ['**/*.{jsx,tsx}'];

// Custom hooks often live in plain .js or .ts files (src/hooks/use-x.js): the two rules of hooks
// cover them too. This file loads only at a react-vite root, so a Nest API never gets them.
const hooksInScripts = {
  files: ['src/**/*.{js,ts}'],
  plugins: { 'react-hooks': reactHooks },
  rules: {
    'react-hooks/rules-of-hooks': 'error',
    'react-hooks/exhaustive-deps': 'warn',
  },
};

// Rules for a React app built with Vite, on the component files. ESLint's max-depth counts
// nested code blocks, not JSX elements, so deep markup passes; split a component when it
// outgrows the function limit instead. The app's sources run in the browser.
export const vite = [
  { files, ...reactHooks.configs.flat.recommended },
  hooksInScripts,
  { files, ...reactRefresh.configs.vite },
  { files, ...jsxA11y.flatConfigs.recommended },
  { files: ['src/**/*.{js,jsx,ts,tsx}'], languageOptions: { globals: globals.browser } },
];

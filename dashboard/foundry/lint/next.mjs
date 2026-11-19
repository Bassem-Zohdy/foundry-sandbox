import nextPlugin from '@next/eslint-plugin-next';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';

const files = ['**/*.{js,jsx,ts,tsx}'];

// ESLint's max-depth counts nested code blocks. JSX elements are not blocks, so deep markup
// passes; split a component when it outgrows the function limit instead.
export const next = [
  { files, ...nextPlugin.configs['core-web-vitals'] },
  { files, ...reactHooks.configs.flat.recommended },
  { files, ...jsxA11y.flatConfigs.recommended },
  { files, languageOptions: { globals: { ...globals.browser, ...globals.node } } },
];

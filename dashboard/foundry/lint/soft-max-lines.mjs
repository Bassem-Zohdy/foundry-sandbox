// The soft file-length limit: ESLint's own max-lines, registered a second time so that it can
// run at `warn` beside the hard `max-lines` error. Warnings are listed and not counted.
import { builtinRules } from 'eslint/use-at-your-own-risk';

const softPlugin = {
  rules: { 'max-lines': builtinRules.get('max-lines') },
};

export const softMaxLines = {
  plugins: { 'foundry-soft': softPlugin },
  rules: {
    'foundry-soft/max-lines': ['warn', { max: 300, skipBlankLines: false, skipComments: false }],
  },
};

// The code limits of the engineering spec (section 5.8). Every one is an error except the soft
// file length in soft-max-lines.mjs.
export const limits = {
  rules: {
    'max-lines': ['error', { max: 400, skipBlankLines: false, skipComments: false }],
    'max-lines-per-function': [
      'error',
      { max: 40, skipBlankLines: false, skipComments: false },
    ],
    complexity: ['error', 10],
    'max-depth': ['error', 3],
    'max-params': ['error', 4],
    'sonarjs/cognitive-complexity': ['error', 15],
  },
};

const grabBags = ['utils', 'helpers', 'misc'];
const banned = grabBags.flatMap((name) => [
  `**/${name}`,
  `**/${name}.*`,
  `**/${name}/**`,
  `${name}`,
  `${name}.*`,
]);

export const noGrabBags = {
  rules: {
    'no-restricted-imports': [
      'error',
      {
        patterns: [{ group: banned, message: 'name the module after what it does' }],
      },
    ],
  },
};

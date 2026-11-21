// Rules for a Nest API. A controller must not import a repository; that is an architecture
// rule and lives in .dependency-cruiser.cjs. The scaffold's shutdown file may call process.exit.
export const nest = [
  {
    files: ['**/*.ts'],
    rules: {
      '@typescript-eslint/no-floating-promises': 'error',
      '@typescript-eslint/no-misused-promises': 'error',
      '@typescript-eslint/no-explicit-any': 'error',
    },
  },
  {
    // Graceful shutdown must leave with a failure code when the drain times out.
    files: ['**/platform/foundry/shutdown/graceful-shutdown.ts'],
    rules: { 'n/no-process-exit': 'off' },
  },
];

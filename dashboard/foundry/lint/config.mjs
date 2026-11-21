// Reads foundry.json each time ESLint runs, so editing it changes the rules with no re-render.
// The search for foundry.json and the reading of it live in foundry/arch/config.cjs, shared with
// dependency-cruiser.
import { createRequire } from 'node:module';

const shared = createRequire(import.meta.url)('../arch/config.cjs');

/** The pack root: tsconfig.json and package.json are here. */
export const root = shared.root;

/** `stacks`, `modules` and `quality.exclude` (relative to the pack root) from foundry.json. */
export function loadFoundry() {
  return shared.loadFoundry();
}

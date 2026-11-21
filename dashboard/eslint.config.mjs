import { foundryConfig } from './foundry/lint/project.mjs';

// Foundry's rules, read from foundry.json each run. To keep a config of your own, see the stack
// doc (docs/foundry/stacks/typescript.md or react-vite.md), "Your own ESLint config".
export default await foundryConfig();

// The architecture rules. Every rule is an error; a rule whose folders do not exist reports
// nothing. Folder conventions are in docs/foundry/stacks/typescript.md.
const { escapeRegex } = require('./config.cjs');

const ctx = '^src/[^/]+';
const NPM = '(^|/)node_modules/(@types/)?';
const DOMAIN_PACKAGES = '(@nestjs|typeorm|@prisma|prisma|mongoose|next|react)';

const error = (name, from, to, comment) => ({
  name,
  comment,
  severity: 'error',
  from,
  to,
});

/** One rule per module: only index.ts, <m>.module.ts and public/ may be imported from outside. */
function contextRules(modules) {
  return modules.map((m) => {
    const name = escapeRegex(m);
    return error(
      'context-public-interface',
      { pathNot: `^src/${name}/` },
      {
        path: `^src/${name}/`,
        pathNot: [`^src/${name}/(index|${name}\\.module)\\.ts$`, `^src/${name}/public/`],
      },
      `Import ${m} only through its index.ts, ${m}.module.ts or public/.`,
    );
  });
}

const layerRules = () => [
  error('no-circular', {}, { circular: true }, 'No import cycles.'),
  error(
    'domain-is-pure',
    { path: `${ctx}/domain/` },
    { path: [`${ctx}/(application|adapters)/`, `${NPM}${DOMAIN_PACKAGES}`] },
    'Domain code imports no application, adapter or framework code.',
  ),
  error(
    'application-not-adapters',
    { path: `${ctx}/application/` },
    { path: `${ctx}/adapters/` },
    'Use cases depend on ports, not on adapters.',
  ),
];

function nestRules(modules) {
  const rules = [
    error(
      'controller-not-repository',
      { path: '\\.controller\\.ts$' },
      { path: '\\.repository\\.ts$' },
      'A controller calls a service, never a repository.',
    ),
  ];
  if (modules.length > 0) {
    const names = modules.map(escapeRegex).join('|');
    rules.push(
      error(
        'shared-not-context',
        { path: '^src/(common|platform)/' },
        { path: `^src/(${names})/` },
        'Shared code imports no module.',
      ),
    );
  }
  return rules;
}

const nextRules = () => [
  error(
    'client-not-server',
    { path: ['(^|/)components/', '\\.client\\.tsx$'] },
    { path: ['(^|/)server/', '(^|/)server-only(/|$)'], pathNot: '(^|/)node_modules/(?!server-only)' },
    'Client code never imports server-only code.',
  ),
  error(
    'ui-not-data-clients',
    { path: '(^|/)components/' },
    {
      path: ['(^|/)(adapters|db|api-client)/', '(^|/)node_modules/@prisma/client(/|$)'],
      pathNot: '(^|/)node_modules/(?!@prisma/client)',
    },
    'Components get data as props, not from a data client.',
  ),
];

// A Vite app has no server code; its components and pages get data through props or hooks.
const viteRules = () => [
  error(
    'ui-not-data-clients',
    { path: ['^src/components/', '^src/pages/'] },
    { path: ['(^|/)(adapters|db|api-client)/'] },
    'Components and pages get data as props or from a hook, not from a data client.',
  ),
];

/** The forbidden rules for the stacks and modules in foundry.json. */
function buildRules({ stacks, modules }) {
  return [
    ...layerRules(),
    ...contextRules(modules),
    ...(stacks.includes('ts-nest') ? nestRules(modules) : []),
    ...(stacks.includes('ts-next') ? nextRules() : []),
    ...(stacks.includes('react-vite') ? viteRules() : []),
  ];
}

module.exports = { buildRules };

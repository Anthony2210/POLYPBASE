import { moduleLoader } from './sharing-test-harness.mjs';

const load = moduleLoader();

// Follow the production imports, not the static section inventory. These paths
// must stay independent so a section missing from the aggregate is detectable.
export function loadI18n(globals) {
  return (globals ? moduleLoader({}, globals) : load)('src/i18n/index.ts');
}

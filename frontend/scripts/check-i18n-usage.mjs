import { checkRepository } from './i18n-usage.mjs';

try {
  const result = checkRepository();
  console.log(`i18n usage: FR ${result.counts.fr}, EN ${result.counts.en}; ${result.files.length} non-test source files; ${result.references.size} referenced keys.`);
  console.log(`Source-unreferenced keys (${result.orphans.length}): ${result.orphans.join(', ') || '(none)'}`);
  if (result.errors.length) {
    console.error(result.errors.join('\n'));
    process.exitCode = 1;
  } else {
    console.log('No unreviewed orphans or stale exact-key exceptions. This is conservative source evidence, not runtime reachability.');
  }
} catch (error) {
  console.error(`i18n usage check failed: ${error.message}`);
  process.exitCode = 1;
}

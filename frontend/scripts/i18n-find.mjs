import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { isStaleReview, readSections } from './i18n-sections.mjs';

export function findTranslations(inventory, query) {
  const needle = query.toLowerCase();
  return [...inventory.entries.values()].filter(({ key, values }) =>
    [key, ...Object.values(values)].some((value) =>
      value.toLowerCase().includes(needle),
    ),
  );
}

export function formatTranslation(entry) {
  const { key, path, line, values, statuses } = entry;
  const lines = [`${key} ${path}:${line}`];
  for (const language of ['fr', 'en', 'ja']) {
    const value = Object.hasOwn(values, language)
      ? JSON.stringify(values[language])
      : '(missing; falls back to en)';
    const review = statuses[language];
    const status = review
      ? ` [${review.status}${review.reviewer ? `: ${review.reviewer}` : ''}]`
      : '';
    const stale =
      review && isStaleReview(entry, language) ? ' (stale source)' : '';
    lines.push(`  ${language}: ${value}${status}${stale}`);
  }
  return lines.join('\n');
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  try {
    const query = process.argv.slice(2).join(' ');
    if (!query.trim())
      throw new Error('Usage: npm run i18n:find -- <key or text>');
    const inventory = readSections();
    const matches = findTranslations(inventory, query);
    if (!matches.length)
      throw new Error(`No translation matches ${JSON.stringify(query)}`);
    console.log(matches.map(formatTranslation).join('\n\n'));
  } catch (error) {
    console.error(`i18n find failed: ${error.message}`);
    process.exitCode = 1;
  }
}

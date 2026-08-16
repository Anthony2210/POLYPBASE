import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const stylesRoot = path.join(frontendRoot, 'src', 'styles');

async function listCssFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map(async (entry) => {
    const absolutePath = path.join(directory, entry.name);
    return entry.isDirectory() ? listCssFiles(absolutePath) : [absolutePath];
  }));
  return nested.flat().filter((file) => file.endsWith('.css'));
}

const cssFiles = await listCssFiles(stylesRoot);
const indexPath = path.join(stylesRoot, 'index.css');
const indexSource = await readFile(indexPath, 'utf8');
const mainSource = await readFile(path.join(frontendRoot, 'src', 'main.tsx'), 'utf8');
const relative = (file) => path.relative(stylesRoot, file).replaceAll('\\', '/');
const importedFiles = [...indexSource.matchAll(/@import\s+['"](.+?\.css)['"]/g)]
  .map((match) => match[1].replace(/^\.\//, ''));
const expectedFiles = cssFiles.map(relative).filter((file) => file !== 'index.css');
const errors = [];

for (const file of expectedFiles) {
  if (!importedFiles.includes(file)) errors.push(`${file} is not imported by index.css.`);
}

for (const file of importedFiles) {
  if (!expectedFiles.includes(file)) errors.push(`${file} is imported but does not exist.`);
}

for (const file of new Set(importedFiles)) {
  if (importedFiles.filter((candidate) => candidate === file).length > 1) {
    errors.push(`${file} is imported more than once.`);
  }
}

if (!mainSource.includes("import './styles/index.css';")) {
  errors.push('main.tsx must import styles/index.css.');
}

if (/styles\/(?!index\.css)[^'"\n]+\.css/.test(mainSource)) {
  errors.push('main.tsx must not import a page or component stylesheet directly.');
}

for (const legacyFile of ['app.css', 'phone.css']) {
  if (expectedFiles.includes(legacyFile)) errors.push(`${legacyFile} is a retired legacy stylesheet.`);
}

const importantAllowList = new Set(['base.css', 'components/primitives.css', 'responsive/print.css']);

for (const filePath of cssFiles) {
  const file = relative(filePath);
  const source = await readFile(filePath, 'utf8');
  if (file !== 'index.css' && !source.trim()) errors.push(`${file} is empty.`);
  if (/font-size\s*:\s*clamp\(/i.test(source)) errors.push(`${file} scales text with the viewport.`);
  if (/letter-spacing\s*:\s*-/i.test(source)) errors.push(`${file} uses negative letter spacing.`);
  if (source.includes('!important') && !importantAllowList.has(file)) {
    errors.push(`${file} uses !important outside the accessibility or print allow-list.`);
  }
  if (file.startsWith('pages/') && /(^|\n)\s*(?::root|html|body)\b/.test(source)) {
    errors.push(`${file} defines a global document selector inside a page layer.`);
  }
}

if (errors.length) {
  console.error(`CSS architecture check failed:\n- ${errors.join('\n- ')}`);
  process.exit(1);
}

const lineCount = (await Promise.all(cssFiles.map(async (file) => {
  const source = await readFile(file, 'utf8');
  return source.split(/\r?\n/).filter((line) => line.trim()).length;
}))).reduce((total, count) => total + count, 0);

console.log(`CSS architecture OK: ${cssFiles.length} files, ${lineCount} non-empty source lines.`);

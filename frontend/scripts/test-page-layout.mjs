import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readSource = (relativePath) => readFileSync(path.join(frontendRoot, relativePath), 'utf8');
const appSource = readSource('src/App.tsx');
const tokensSource = readSource('src/styles/tokens.css');
const layoutSource = readSource('src/styles/layout.css');
const zonesSource = readSource('src/styles/pages/zones.css');
const exportsLabelsSource = readSource('src/styles/pages/exports-labels.css');
const adminViewSource = readSource('src/components/AdminView.tsx');
const exportsViewSource = readSource('src/components/ExportsView.tsx');
const labelsViewSource = readSource('src/components/LabelsView.tsx');
const overviewViewSource = readSource('src/components/OverviewView.tsx');
const profileViewSource = readSource('src/components/ProfileView.tsx');
const zonesViewSource = readSource('src/components/ZonesView.tsx');

// Actual outer roots rendered directly by workspace views, plus the immediate
// Administration and Emplacements wrappers that could otherwise re-own their
// page frame. Internal cards, forms, tables, previews and modals are excluded.
const PAGE_ROOT_CLASSES = [
  'admin-panel',
  'admin-workspace',
  'box-page',
  'export-page',
  'labels-page',
  'overview-page',
  'pilotage-flow',
  'profile-page',
  'zone-overview',
  'zone-overview-shell',
  'zone-page',
];
const PAGE_ROOT_SOURCE_BY_CLASS = new Map([
  ['admin-panel', adminViewSource],
  ['admin-workspace', adminViewSource],
  ['box-page', appSource],
  ['export-page', exportsViewSource],
  ['labels-page', labelsViewSource],
  ['overview-page', overviewViewSource],
  ['pilotage-flow', appSource],
  ['profile-page', `${profileViewSource}\n${labelsViewSource}`],
  ['zone-overview', zonesViewSource],
  ['zone-overview-shell', zonesViewSource],
  ['zone-page', zonesViewSource],
]);
const SHELL_CLASSES = ['app-shell', 'page-heading', 'workspace', 'workspace-page'];
const OUTER_FRAME_OWNERSHIP = /(?:^|;)\s*(?:width|max-width|margin|margin-inline)\s*:/;

function listCssFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const entryPath = path.join(directory, entry.name);
    return entry.isDirectory() ? listCssFiles(entryPath) : entryPath.endsWith('.css') ? [entryPath] : [];
  });
}

const stylesRoot = path.join(frontendRoot, 'src', 'styles');
const styleSheets = listCssFiles(stylesRoot).map((file) => ({
  file: path.relative(frontendRoot, file).replaceAll('\\', '/'),
  source: readFileSync(file, 'utf8'),
}));
const allCss = styleSheets.map(({ source }) => source).join('\n');

// The project does not use CSS nesting. Removing comments prevents a comment
// before a selector from becoming part of that selector in this lightweight
// rule reader; container and media wrappers are naturally skipped because
// their bodies contain nested braces.
function cssRules(source) {
  const withoutComments = source.replace(/\/\*[\s\S]*?\*\//g, '');
  return [...withoutComments.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(([, selector, declarations]) => ({
    selector: selector.trim(),
    declarations,
  }));
}

function splitTopLevel(value, delimiter = ',') {
  const parts = [];
  let start = 0;
  let parentheses = 0;
  let brackets = 0;
  let quote = null;
  let escaped = false;

  for (let index = 0; index < value.length; index += 1) {
    const character = value[index];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (character === '\\') {
      escaped = true;
      continue;
    }
    if (quote) {
      if (character === quote) quote = null;
      continue;
    }
    if (character === '"' || character === "'") {
      quote = character;
      continue;
    }
    if (character === '(') parentheses += 1;
    else if (character === ')') parentheses -= 1;
    else if (character === '[') brackets += 1;
    else if (character === ']') brackets -= 1;
    else if (character === delimiter && parentheses === 0 && brackets === 0) {
      parts.push(value.slice(start, index).trim());
      start = index + 1;
    }
  }

  parts.push(value.slice(start).trim());
  return parts.filter(Boolean);
}

function finalCompound(selector) {
  let start = 0;
  let parentheses = 0;
  let brackets = 0;
  let quote = null;
  let escaped = false;

  for (let index = 0; index < selector.length; index += 1) {
    const character = selector[index];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (character === '\\') {
      escaped = true;
      continue;
    }
    if (quote) {
      if (character === quote) quote = null;
      continue;
    }
    if (character === '"' || character === "'") {
      quote = character;
      continue;
    }
    if (character === '(') parentheses += 1;
    else if (character === ')') parentheses -= 1;
    else if (character === '[') brackets += 1;
    else if (character === ']') brackets -= 1;
    else if (parentheses === 0 && brackets === 0 && (/[>+~]/.test(character) || /\s/.test(character))) {
      start = index + 1;
    }
  }

  return selector.slice(start).trim();
}

function findClosingParenthesis(value, openingIndex) {
  let depth = 0;
  let quote = null;
  let escaped = false;

  for (let index = openingIndex; index < value.length; index += 1) {
    const character = value[index];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (character === '\\') {
      escaped = true;
      continue;
    }
    if (quote) {
      if (character === quote) quote = null;
      continue;
    }
    if (character === '"' || character === "'") {
      quote = character;
      continue;
    }
    if (character === '(') depth += 1;
    if (character === ')' && (depth -= 1) === 0) return index;
  }

  return -1;
}

function subjectClasses(selectorText) {
  const classes = new Set();

  for (const selector of splitTopLevel(selectorText)) {
    collectCompoundClasses(finalCompound(selector), classes);
  }

  return classes;
}

function collectCompoundClasses(compound, classes) {
  let parentheses = 0;
  let brackets = 0;
  let quote = null;
  let escaped = false;

  for (let index = 0; index < compound.length; index += 1) {
    const character = compound[index];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (character === '\\') {
      escaped = true;
      continue;
    }
    if (quote) {
      if (character === quote) quote = null;
      continue;
    }
    if (character === '"' || character === "'") {
      quote = character;
      continue;
    }
    if (character === '[') {
      brackets += 1;
      continue;
    }
    if (character === ']') {
      brackets -= 1;
      continue;
    }
    if (brackets > 0) continue;

    if (parentheses === 0 && character === '.') {
      const match = compound.slice(index + 1).match(/^[A-Za-z_-][\w-]*/);
      if (match) {
        classes.add(match[0]);
        index += match[0].length;
      }
      continue;
    }

    if (parentheses === 0 && character === ':') {
      const functionalMatch = compound.slice(index).match(/^:(is|where)\(/);
      if (functionalMatch) {
        const openingIndex = index + functionalMatch[0].length - 1;
        const closingIndex = findClosingParenthesis(compound, openingIndex);
        assert.notEqual(closingIndex, -1, `unclosed :${functionalMatch[1]}() in ${compound}`);
        const argumentsText = compound.slice(openingIndex + 1, closingIndex);
        for (const className of subjectClasses(argumentsText)) classes.add(className);
        index = closingIndex;
        continue;
      }
    }

    if (character === '(') parentheses += 1;
    else if (character === ')') parentheses -= 1;
  }
}

function cssRule(source, selector) {
  const escapedSelector = selector
    .trim()
    .split(/\s+/)
    .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .join('\\s+');
  const match = source.match(new RegExp(`${escapedSelector}\\s*\\{([^}]*)\\}`));
  assert.ok(match, `missing CSS rule for ${selector}`);
  return match[1];
}

test('selector parsing preserves functional selector groups', () => {
  assert.deepEqual(
    [...subjectClasses(':where(.profile-page, .export-page), .wrapper > :is(.labels-page, .zone-page)')].sort(),
    ['export-page', 'labels-page', 'profile-page', 'zone-page'],
  );
  assert.deepEqual(
    [...subjectClasses('.profile-page h2, .wrapper > .overview-page')].sort(),
    ['overview-page'],
    'ancestor classes must not be mistaken for selector subjects',
  );
});

test('one shared outer page-width token defines the desktop frame', () => {
  assert.match(tokensSource, /--page-width:\s*1480px/);
  assert.doesNotMatch(tokensSource, /--content-max/);

  const definitions = allCss.match(/--page-width\s*:/g) ?? [];
  assert.equal(definitions.length, 1, '--page-width must be defined exactly once');
  assert.doesNotMatch(allCss, /--page-width-(?:wide|standard|narrow)/);
  assert.doesNotMatch(allCss, /1480px.*1480px/s, 'the desktop frame must not be duplicated');
});

test('the page title and the page content consume the same shared outer frame', () => {
  const sharedRule = cssRule(layoutSource, '.page-heading,\n.workspace-page');
  assert.match(sharedRule, /width:\s*min\(var\(--page-width\),\s*100%\)/);
  assert.match(sharedRule, /margin-inline:\s*auto/);

  const frameDeclarations = allCss.match(/width:\s*min\(var\(--page-width\),\s*100%\)/g) ?? [];
  assert.equal(frameDeclarations.length, 1, 'the frame must be declared once, for both selectors');
  assert.doesNotMatch(allCss, /--page-frame-width/);
});

test('no route-dependent page width classification remains', () => {
  assert.doesNotMatch(appSource, /PageWidthCategory|PAGE_WIDTH_BY_TAB|getPageWidthCategory/);
  assert.match(appSource, /<section className="workspace">/);
  assert.doesNotMatch(appSource, /page-width-/);
});

test('no page-width category shell class remains', () => {
  assert.doesNotMatch(allCss, /\.workspace\.page-width-/);
  assert.doesNotMatch(allCss, /page-width-(?:wide|standard|narrow)/);
});

test('the Labels local width hack remains absent', () => {
  assert.doesNotMatch(exportsLabelsSource, /\.workspace-page:has\(/);
  assert.doesNotMatch(exportsLabelsSource, /:where\(\.export-page, \.labels-page\)\s*\{[^}]*max-width/s);
  assert.doesNotMatch(exportsLabelsSource, /\.labels-page\s*\{[^}]*max-width/s);
});

test('the Emplacements whole-page cap remains absent', () => {
  assert.doesNotMatch(zonesSource, /max-width:\s*1120px/);
  assert.doesNotMatch(zonesSource, /:is\(\.zone-overview, \.zone-page\)|:where\(\.zone-overview, \.zone-page\)/);

  const rule = cssRule(zonesSource, '.zone-page,\n.zone-overview');
  assert.doesNotMatch(rule, OUTER_FRAME_OWNERSHIP, 'the Emplacements roots must not own the page frame');
});

test('all actual page roots reject independent outer-frame ownership', () => {
  assert.deepEqual(
    [...PAGE_ROOT_SOURCE_BY_CLASS.keys()].sort(),
    [...PAGE_ROOT_CLASSES].sort(),
    'every configured page root must have a JSX source assertion',
  );
  for (const [className, source] of PAGE_ROOT_SOURCE_BY_CLASS) {
    assert.match(source, new RegExp(`className=(?:\\{[^}]*\\}|["'][^"']*)\\b${className}\\b`), `${className} must be a real JSX root`);
  }

  const matchedRootClasses = new Set();
  let matchedRootRules = 0;

  for (const { file, source } of styleSheets) {
    for (const { selector, declarations } of cssRules(source)) {
      const matchedClasses = [...subjectClasses(selector)].filter((className) => PAGE_ROOT_CLASSES.includes(className));
      if (!matchedClasses.length) continue;

      matchedRootRules += 1;
      matchedClasses.forEach((className) => matchedRootClasses.add(className));
      assert.doesNotMatch(
        declarations,
        OUTER_FRAME_OWNERSHIP,
        `${file}: ${selector} is a page root and must not set width, max-width, margin, or margin-inline`,
      );
    }
  }

  assert.ok(matchedRootRules > 0, 'the page-root scan must inspect real rules');
  assert.ok(matchedRootClasses.size > 0, 'the page-root scan must identify real root classes');
});

test('all screen shell rules reject viewport-width workarounds', () => {
  const matchedShellClasses = new Set();
  let matchedShellRules = 0;

  for (const { file, source } of styleSheets) {
    if (file === 'src/styles/responsive/print.css') {
      assert.match(source.trim(), /^@media\s+print\s*\{[\s\S]*\}$/);
      continue;
    }

    for (const { selector, declarations } of cssRules(source)) {
      const matchedClasses = [...subjectClasses(selector)].filter((className) => SHELL_CLASSES.includes(className));
      if (!matchedClasses.length) continue;

      matchedShellRules += 1;
      matchedClasses.forEach((className) => matchedShellClasses.add(className));
      assert.doesNotMatch(declarations, /100vw/, `${file}: ${selector} must not use 100vw`);
    }
  }

  assert.ok(matchedShellRules > 0, 'the screen shell scan must inspect real rules');
  assert.deepEqual(
    [...matchedShellClasses].sort(),
    [...SHELL_CLASSES].sort(),
    'the screen shell scan must cover every shell class',
  );
});

test('the Administration desktop-only redirect and render guards remain in place', () => {
  assert.match(
    appSource,
    /if \(activeTab === 'admin' && !isDesktopApp\) \{\s*replaceRoute\(\{ tab: 'pilotage', boxCode: null, boxId: null \}, '\/'\);\s*return;\s*\}/,
  );
  assert.match(appSource, /if \(activeTab === 'admin' && !isDesktopApp\) return null;/);
  assert.match(appSource, /\{activeTab === 'admin' && isDesktopApp && \(/);
});

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
    assert.match(source, new RegExp(`className=(?:\\{[^}]*\\b${className}\\b[^}]*\\}|["'][^"']*\\b${className}\\b)`), `${className} must be a real JSX root`);
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

test('Profile shows the institution selector only when there is a real choice', () => {
  assert.match(profileViewSource, /const organizations = getSelectableOrganizations\(profile\);/);
  assert.match(
    profileViewSource,
    /\{organizations\.length > 1 \? \(\s*<section className="profile-block profile-organization-context">/s,
  );
  assert.doesNotMatch(profileViewSource, /organizations\.length > 0/);

  // Selecting an institution keeps its existing behaviour.
  assert.match(profileViewSource, /onClick=\{\(\) => onSelectOrganization\(organization\.id\)\}/);
  assert.match(profileViewSource, /aria-pressed=\{isActive\}/);
  assert.match(profileViewSource, /className=\{isActive \? 'is-active' : ''\}/);
});

test('Profile carries the Administration action inside the account card', () => {
  const profileCssSource = readSource('src/styles/pages/profile.css');
  const cardStart = profileViewSource.indexOf('<header className="profile-identity-card">');
  const cardSource = profileViewSource.slice(
    cardStart,
    profileViewSource.indexOf('</header>', cardStart),
  );

  assert.notEqual(cardStart, -1);
  assert.match(
    cardSource,
    /\{canOpenAdmin \? \(\s*<button\s*className="secondary-button button-icon-label profile-admin-action"/s,
  );
  assert.match(cardSource, /onClick=\{onOpenAdmin\}/);
  assert.match(cardSource, /<PolypbaseIcon name="settings" size=\{17\} \/>/);

  // Logout is no longer inside the account card.
  assert.doesNotMatch(cardSource, /profile-sign-out|profile-session-actions/);
  assert.doesNotMatch(profileCssSource, /profile-session-actions/);

  // The separate "Administration area" section is fully removed.
  assert.doesNotMatch(profileViewSource, /profile-admin-entry|profile-admin-button|profile-link-arrow/);
  assert.doesNotMatch(profileCssSource, /profile-admin-entry|profile-admin-button|profile-link-arrow/);

  // One single capability condition, already desktop-only, drives the entry.
  assert.match(appSource, /canOpenAdmin=\{canUseAdmin && isDesktopApp\}/);
  assert.match(appSource, /const canUseAdmin = hasAdminRole;/);
});

test('Profile orders sections and places the full-width logout last', () => {
  const profileCssSource = readSource('src/styles/pages/profile.css');
  const preferencesIndex = profileViewSource.indexOf('{labels.profilePreferences}');
  const actionsIndex = profileViewSource.indexOf('<ProfileActionsSection');
  const logoutIndex = profileViewSource.indexOf('className="profile-logout-row"');

  assert.notEqual(preferencesIndex, -1);
  assert.notEqual(actionsIndex, -1);
  assert.notEqual(logoutIndex, -1);
  // Preferences comes before Mes actions.
  assert.ok(preferencesIndex < actionsIndex, 'Préférences must precede Mes actions');
  // Logout is the final Profile action.
  assert.ok(logoutIndex > actionsIndex, 'logout must come after Mes actions');
  assert.ok(logoutIndex > preferencesIndex, 'logout must come after Préférences');

  // Logout is full width and separated at the bottom, still on the same handler.
  assert.match(
    profileCssSource,
    /\.profile-logout-row \{\s*display: grid;\s*gap: var\(--space-2\);\s*padding-top: var\(--space-4\);\s*border-top: 1px solid var\(--color-line-soft\);\s*\}/s,
  );
  assert.match(profileCssSource, /\.profile-logout-row \.profile-sign-out \{ width: 100%; \}/);
  assert.match(profileViewSource, /className="profile-sign-out"[\s\S]*?onClick=\{handleLogout\}/);
});

function containerBlock(source, name, minWideWidth) {
  const marker = `@container ${name} (width < ${minWideWidth}px)`;
  const start = source.indexOf(marker);
  assert.notEqual(start, -1, `missing ${marker}`);
  const opening = source.indexOf('{', start);
  let depth = 1;
  for (let index = opening + 1; index < source.length; index += 1) {
    if (source[index] === '{') depth += 1;
    if (source[index] === '}') depth -= 1;
    if (depth === 0) return source.slice(opening + 1, index);
  }
  assert.fail(`unclosed ${marker}`);
}

const boxDetailCss = readSource('src/styles/pages/box-detail.css');
const overviewCss = readSource('src/styles/pages/overview.css');
const tabletCss = readSource('src/styles/responsive/tablet.css');
const phoneCss = readSource('src/styles/responsive/phone.css');

test('Box header adapts to available page width in the responsive layer', () => {
  assert.match(cssRule(boxDetailCss, '.box-page'), /container:\s*box-detail\s*\/\s*inline-size/);
  // 878px of tracks + 60px gaps + 48px padding + 5px borders = 991px.
  const compact = containerBlock(tabletCss, 'box-detail', 991);
  const header = cssRule(compact, '.entity-header--box');
  assert.match(header, /--entity-columns:\s*minmax\(0,\s*1fr\)\s+92px/);
  assert.match(header, /--entity-areas:\s*"identity tools"\s*"summary summary"\s*"actions actions"/);
  assert.match(cssRule(compact, '.box-action-stack'), /repeat\(auto-fit,\s*minmax\(min\(100%,\s*156px\),\s*1fr\)\)/);
  assert.doesNotMatch(compact, /display:\s*none|overflow:\s*hidden/);
  assert.match(cssRule(phoneCss, '.entity-header--box'), /--entity-columns:\s*minmax\(0,\s*1fr\)\s+48px/);
  const imports = readSource('src/styles/index.css');
  assert.ok(imports.indexOf("'./responsive/tablet.css'") < imports.indexOf("'./responsive/phone.css'"));
  assert.match(imports, /responsive\/tablet\.css'\s+layer\(responsive\)/);
});

test('compact Box header wraps identity and facts instead of clipping them', () => {
  const compact = containerBlock(tabletCss, 'box-detail', 991);
  assert.match(cssRule(compact, '.box-code-line h2'), /white-space:\s*normal/);
  assert.match(cssRule(compact, '.box-code-line h2'), /overflow:\s*visible/);
  assert.match(cssRule(compact, '.box-species-name'), /overflow-wrap:\s*anywhere/);
  assert.match(cssRule(compact, '.box-zone-summary .info-pill'), /overflow-wrap:\s*anywhere/);
  assert.match(cssRule(compact, '.box-zone-summary'), /border-inline:\s*0/);
  assert.match(cssRule(compact, '.box-action-stack > button'), /overflow-wrap:\s*anywhere/);
  assert.match(readSource('src/styles/components/buttons.css'), /\.profile-sign-out\s*\{\s*min-height:\s*44px/);
});

test('Overview filters stack before their tracks exceed the container', () => {
  assert.match(cssRule(overviewCss, '.overview-filters'), /container:\s*overview-filters\s*\/\s*inline-size/);
  // 730px of tracks + 24px gaps + 24px padding = 778px.
  const compact = containerBlock(overviewCss, 'overview-filters', 778);
  assert.match(cssRule(compact, '.overview-filter-fields'), /grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  const trackRules = cssRules(overviewCss).filter(({ selector }) => selector === '.overview-filter-fields');
  assert.equal(trackRules.length, 2, 'no viewport override may restore overflowing filter tracks');
  assert.match(cssRule(overviewCss, '.overview-filters-header'), /flex-wrap:\s*wrap/);
  assert.match(cssRule(overviewCss, '.overview-filters-header > div'), /flex-wrap:\s*wrap/);
  assert.match(cssRule(overviewCss, '.overview-sort-buttons button'), /overflow-wrap:\s*anywhere/);
  assert.equal((overviewViewSource.match(/className="overview-sort-buttons"/g) ?? []).length, 1);
});

test('narrow Overview result headers keep identity and location controls in flow', () => {
  const resultRule = cssRules(overviewCss).find(({ selector }) => selector === '.overview-box-summary');
  assert.ok(resultRule);
  assert.match(resultRule.declarations, /container:\s*overview-result\s*\/\s*inline-size/);
  // Header tracks need 354px; its negative margins add 28px to the content box.
  const compact = containerBlock(overviewCss, 'overview-result', 326);
  assert.match(cssRule(compact, '.overview-box-summary > header'), /grid-template-columns:\s*auto\s+minmax\(0,\s*1fr\)/);
  assert.match(cssRule(compact, '.overview-zone-context'), /grid-column:\s*1\s*\/\s*-1/);
  assert.match(cssRule(compact, '.overview-zone-context'), /padding-inline:\s*var\(--space-3\)/);
  assert.doesNotMatch(compact, /display:\s*none|overflow:\s*hidden|position:\s*absolute/);
  assert.match(cssRule(overviewCss, '.overview-zone-button'), /overflow-wrap:\s*anywhere/);
  assert.match(cssRule(overviewCss, '.overview-box-identity strong'), /overflow-wrap:\s*anywhere/);
  assert.match(overviewViewSource, /className="overview-box-identity"[\s\S]*?onClick=\{\(\) => onSelectBox\(entry\.box\.id\)\}/);
  assert.match(overviewViewSource, /className="overview-zone-button"[\s\S]*?onClick=\{\(\) => onOpenZone/);
});

test('all Zone hero variants reserve only the identity track at every CSS layer', () => {
  for (const source of [zonesSource, tabletCss, phoneCss]) {
    const rules = cssRules(source).filter(({ selector }) => /\.(?:zone-sheet-hero|zone-directory-hero)$/.test(selector));
    assert.ok(rules.length > 0);
    for (const { selector, declarations } of rules) {
      assert.match(declarations, /--entity-columns:\s*minmax\(0,\s*1fr\)/, selector);
      assert.match(declarations, /--entity-areas:\s*"identity"\s*;/, selector);
    }
  }
  for (const source of [zonesViewSource, readSource('src/components/ZoneMovementHistory.tsx')]) {
    const heroes = [...source.matchAll(/<header className="entity-header entity-header--zone[^"\n]*">([\s\S]*?)<\/header>/g)];
    assert.ok(heroes.length > 0);
    for (const [, hero] of heroes) {
      assert.match(hero, /entity-header__identity/);
      assert.doesNotMatch(hero, /entity-header__(?:summary|actions)|zone-hero-summary|zone-hero-actions/);
    }
  }
});

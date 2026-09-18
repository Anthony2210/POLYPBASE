import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';

const readSource = (relativePath) => readFileSync(new URL(relativePath, import.meta.url), 'utf8');
const appSource = readSource('../src/App.tsx');
const searchFieldSource = readSource('../src/components/SearchField.tsx');
const pilotageCss = readSource('../src/styles/pages/pilotage.css');
const layoutCss = readSource('../src/styles/layout.css');

function loadTypeScript(relativePath) {
  const source = readSource(relativePath);
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  vm.runInNewContext(outputText, { exports });
  return exports;
}

const { isRouteRequestCurrent } = loadTypeScript('../src/utils/routeSafety.ts');

test('route request guard rejects stale request and navigation generations', () => {
  assert.equal(isRouteRequestCurrent(2, 2, 5, 5), true);
  assert.equal(isRouteRequestCurrent(1, 2, 5, 5), false);
  assert.equal(isRouteRequestCurrent(2, 2, 4, 5), false);
});

test('idle and active search states switch recent content correctly', () => {
  assert.match(appSource, /const hasSearch = Boolean\(search\.trim\(\)\)/);
  assert.match(appSource, /\{\(!hasSearch \|\| isPhoneLayout\) \? \(\s*<RecentAccessList/s);
  assert.match(appSource, /\{hasSearch \? \(\s*<SuggestionList/s);
});

test('zero results render explicit feedback and a clear action', () => {
  assert.match(appSource, /<div className="search-empty-state" id=\{listId\} role="status">/);
  assert.match(appSource, /t\('searchNoResults'\)/);
  assert.match(appSource, /t\('searchClear'\)/);
});

test('desktop and tablet show total results while rendering at most fifteen', () => {
  assert.match(appSource, /const PILOTAGE_RESULT_LIMIT = 15/);
  assert.match(appSource, /searchResults\.slice\(0, isPhoneLayout \? PHONE_RESULT_LIMIT : PILOTAGE_RESULT_LIMIT\)/);
  assert.match(appSource, /totalCount=\{searchResults\.length\}/);
  assert.doesNotMatch(appSource, /searchMoreResults|search-results-overflow/);
});

test('returning to idle Pilotage clears search without box-code injection', () => {
  assert.match(appSource, /if \(activeTab === 'pilotage' && !isBoxRoute\) setSearch\(''\)/);
  assert.match(appSource, /if \(tab === 'pilotage'\) setSearch\(''\)/);
  assert.match(appSource, /function closeBoxPage\(\) \{\s*setSearch\(''\)/);
  const openBoxSource = appSource.slice(
    appSource.indexOf('function openBox('),
    appSource.indexOf('\n  function openZone(', appSource.indexOf('function openBox(')),
  );
  assert.doesNotMatch(openBoxSource, /setSearch\(/);
});

test('late box fallback responses are guarded against route changes', () => {
  assert.match(appSource, /if \(box\) \{\s*setIsBoxLoading\(false\)/);
  assert.match(appSource, /const navigationGeneration = navigationGenerationRef\.current/);
  assert.match(appSource, /navigationGenerationRef\.current \+= 1;\s*window\.history\.pushState/);
  assert.match(appSource, /navigationGenerationRef\.current \+= 1;\s*setIsTabletScannerOpen\(false\)/);
  assert.ok((appSource.match(/isRouteRequestCurrent\(/g) ?? []).length >= 3);
});

test('desktop and tablet use one in-flow semantic result surface', () => {
  assert.equal((appSource.match(/'box-search-results'/g) ?? []).length, 1);
  assert.doesNotMatch(appSource, /desktop-suggestion-slot|tablet-search-panel/);
  assert.match(appSource, /className="pilotage-search-surface"/);
  assert.match(pilotageCss, /\.pilotage-search-surface\s*\{[^}]*display:\s*grid[^}]*gap:\s*0[^}]*border:\s*1px solid var\(--color-line-strong\)/s);
  assert.match(pilotageCss, /\.pilotage-search-surface \.suggestion-panel\s*\{[^}]*border-top:\s*1px solid var\(--color-line\)/s);
  assert.match(pilotageCss, /\.suggestion-list\s*\{[^}]*overflow-y:\s*auto/s);
  assert.match(appSource, /clearLabel=\{t\('searchClear'\)\}[\s\S]*?variant="control-deck"/);
  assert.match(searchFieldSource, /variant === 'control-deck' && value && clearLabel/);
  assert.match(searchFieldSource, /className="search-field-clear"[\s\S]*?onChange\(''\);[\s\S]*?inputRef\.current\?\.focus\(\)/);
  assert.match(appSource, /<PolypbaseIcon name="chevron-right" size=\{17\}/);
  assert.doesNotMatch(appSource, /<span className="suggestion-chevron"[^>]*>›<\/span>/);
  assert.doesNotMatch(pilotageCss, /\.desktop-suggestion-slot/);
});

test('desktop and tablet create boxes from the shared page header and reused modal form', () => {
  assert.match(appSource, /<header className=\{activeTab === 'pilotage' \? 'page-heading pilotage-page-heading'/);
  assert.match(appSource, /className="secondary-button button-icon-label pilotage-create-action"/);
  assert.match(appSource, /onClick=\{\(\) => setIsCreateBoxOpen\(true\)\}/);
  assert.match(appSource, /presentation=\{isPhoneLayout \? 'inline' : 'modal'\}/);
  assert.match(appSource, /className="create-box-modal"[\s\S]*?role="dialog"[\s\S]*?aria-modal="true"/);
  assert.equal((appSource.match(/<form className="create-box-form"/g) ?? []).length, 1);
  assert.match(appSource, /className="modal-close-button" type="button"[^>]*onClick=\{\(\) => setOpen\(false\)\}/);
  assert.match(appSource, /onMouseDown=\{\(\) => setOpen\(false\)\}/);
  assert.match(appSource, /isConfirmationOpenRef\.current = true;[\s\S]*?confirmed = await confirmAction[\s\S]*?finally \{\s*isConfirmationOpenRef\.current = false;/);
  assert.match(appSource, /if \(isQuickStrainOpenRef\.current \|\| isConfirmationOpenRef\.current\) return;/);
  assert.match(appSource, /onSelectBox\(created\.id\);\s*if \(presentation === 'modal'\) \{[\s\S]*?requestAnimationFrame[\s\S]*?\[data-box-page-focus-target\]/);
  assert.match(appSource, /<h2 data-box-page-focus-target tabIndex=\{-1\}>\{box\.global_code\}<\/h2>/);
});

test('landscape tablet result rows retain room for every operational column', () => {
  assert.match(
    pilotageCss,
    /\.pilotage-search-surface \.suggestion-row\s*\{\s*grid-template-columns:\s*minmax\(140px, 1\.1fr\) minmax\(150px, 1\.2fr\) minmax\(140px, 1fr\) minmax\(95px, \.7fr\) 20px;/,
  );
});

test('Pilotage title and recent box codes keep the shared visual hierarchy', () => {
  assert.doesNotMatch(pilotageCss, /\.pilotage-page-heading h1/);
  assert.match(pilotageCss, /\.pilotage-page-heading\s*\{[^}]*align-items:\s*baseline[^}]*justify-content:\s*space-between/s);
  assert.match(pilotageCss, /\.recent-box-heading strong\s*\{[^}]*overflow:\s*visible[^}]*overflow-wrap:\s*anywhere[^}]*text-overflow:\s*clip[^}]*white-space:\s*normal/s);
});

test('Pilotage heading reuses the shared page frame without a page-specific offset', () => {
  assert.doesNotMatch(pilotageCss, /\.workspace:has\(\.pilotage-flow\)/);
  assert.doesNotMatch(pilotageCss, /\.pilotage-page-heading\s*\{[^}]*(padding|margin)/s);
  assert.match(layoutCss, /\.workspace\s*\{[^}]*padding:\s*var\(--space-8\)/s);
  assert.match(layoutCss, /\.page-heading,\s*\.workspace-page\s*\{[^}]*width:\s*min\(var\(--page-width\), 100%\)[^}]*margin-inline:\s*auto/s);
});

test('desktop and tablet result rows show only the canonical box code and species', () => {
  const contextIndex = appSource.indexOf('<span className="suggestion-context">');
  const identityIndex = appSource.lastIndexOf('<span className="suggestion-identity">', contextIndex);
  const desktopBranch = appSource.slice(
    identityIndex,
    appSource.indexOf('<span className="suggestion-reading">', contextIndex),
  );
  assert.doesNotMatch(desktopBranch, /box\.local_code|box\.box_number|strain\.code/);
  assert.match(desktopBranch, /<strong>\{box\.global_code\}<\/strong>/);
  assert.match(desktopBranch, /<strong>\{box\.species\.scientific_name\}<\/strong>/);
  assert.doesNotMatch(pilotageCss, /\.suggestion-identity, \.suggestion-context\) small/);
});

test('Pilotage retains the shared page frame and the phone workflow branch', () => {
  assert.match(layoutCss, /\.page-heading,\s*\.workspace-page\s*\{[^}]*width:\s*min\(var\(--page-width\), 100%\)/s);
  assert.match(appSource, /className=\{`phone-lookup-panel is-\$\{tabletLookupMode\}-mode`\}/);
  assert.match(appSource, /<TabletQrScanner[\s\S]*?onSelectBox=\{onSelectBox\}/);
  assert.match(pilotageCss, /@media \(max-width: 759px\), \(max-width: 900px\) and \(orientation: portrait\)/);
  assert.match(appSource, /presentation === 'inline' \? \(\s*<button\s*className="create-box-toggle"/s);
  assert.match(pilotageCss, /\.create-box-panel:has\(\.create-box-form\)\s*\{[^}]*position:\s*fixed/s);
  assert.match(pilotageCss, /\.create-box-toggle > span\s*\{[^}]*border-radius:\s*50%/s);
});

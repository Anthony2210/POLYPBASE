import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';
import { appHarness, ast, deferred, functionNode, tick } from './app-operation-test-harness.mjs';
import { pilotageHarness } from './pilotage-test-harness.mjs';

const readSource = (relativePath) => readFileSync(new URL(relativePath, import.meta.url), 'utf8');
const appSource = readSource('../src/App.tsx');
const pilotageSource = readSource('../src/components/PilotageView.tsx');
const suggestionSource = readSource('../src/components/SuggestionList.tsx');
const createBoxSource = readSource('../src/components/CreateBoxPanel.tsx');
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
  assert.match(pilotageSource, /const hasSearch = Boolean\(search\.trim\(\)\)/);
  assert.match(pilotageSource, /\{\(!hasSearch \|\| isPhoneLayout\) \? \(\s*<RecentAccessList/s);
  assert.match(pilotageSource, /\{hasSearch && !isBoxCollectionReady \? \(\s*<BoxSearchStatus[^>]*\/>\s*\) : hasSearch \? \(\s*<SuggestionList/s);
});

test('zero results render explicit feedback and a clear action', () => {
  assert.match(suggestionSource, /<div className="search-empty-state" id=\{listId\} role="status">/);
  assert.match(suggestionSource, /t\('searchNoResults'\)/);
  assert.match(suggestionSource, /t\('searchClear'\)/);
});

test('desktop and tablet show total results while rendering at most fifteen', () => {
  assert.match(pilotageSource, /const PILOTAGE_RESULT_LIMIT = 15/);
  assert.match(pilotageSource, /searchResults\.slice\(0, isPhoneLayout \? PHONE_RESULT_LIMIT : PILOTAGE_RESULT_LIMIT\)/);
  assert.match(pilotageSource, /totalCount=\{searchResults\.length\}/);
  assert.doesNotMatch(pilotageSource + suggestionSource, /searchMoreResults|search-results-overflow/);
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

test('numeric box resolution is server-authoritative and guarded against route changes', () => {
  const openBoxSource = functionNode('openBox').getText(ast);
  assert.doesNotMatch(openBoxSource, /data\.boxes\.find/);
  assert.match(openBoxSource, /apiGet<BoxDetail>\(`\/api\/boxes\/\$\{boxId\}\//);
  assert.match(openBoxSource, /boxId: detail\.id/);
  assert.match(appSource, /const navigationGeneration = navigationGenerationRef\.current/);
  const navigateSource = functionNode('navigateTo').getText(ast);
  assert.match(navigateSource, /getInAppHistory\(\)\.push/);
  assert.match(navigateSource, /if \(result === 'pending'\) return;\s*navigationGenerationRef\.current \+= 1;/);
  const syncSource = functionNode('syncRoute').getText(ast);
  assert.match(syncSource, /getInAppHistory\(\)\.sync/);
  assert.match(syncSource, /navigationGenerationRef\.current \+= 1;/);
  assert.match(syncSource, /setIsTabletScannerOpen\(false\)/);
  assert.ok((appSource.match(/isRouteRequestCurrent\(/g) ?? []).length >= 3);
});

for (const traversal of ['push', 'popstate']) {
  test(`real routing invalidates a late box fallback after ${traversal}`, async () => {
    const h = appHarness();
    h.context.data = h.state.data;
    h.context.setIsBoxLoading = () => {};
    h.evaluate(functionNode('openBox').getText(ast));
    h.context.openZone(3);
    h.context.openBox(42, 'BOX-42');
    const generation = h.context.navigationGenerationRef.current;
    if (traversal === 'push') {
      h.context.navigateTo({ tab: 'zones', boxCode: null, boxId: null }, '/zones');
    } else {
      h.browser.travel(-1, h.context.syncRoute);
    }
    assert.ok(h.context.navigationGenerationRef.current > generation);
    const route = h.state.route;
    const data = h.state.data;
    h.requests[0].resolve({ id: 42, global_code: 'BOX-42' });
    await tick();
    assert.equal(h.state.route, route);
    assert.equal(h.state.data, data);
    assert.equal(h.browser.path, traversal === 'push' ? '/zones' : '/');
  });
}

test('desktop and tablet use one in-flow semantic result surface', () => {
  assert.equal((pilotageSource.match(/'box-search-results'/g) ?? []).length, 1);
  assert.doesNotMatch(pilotageSource, /desktop-suggestion-slot|tablet-search-panel/);
  assert.match(pilotageSource, /className="pilotage-search-surface"/);
  assert.match(pilotageCss, /\.pilotage-search-surface\s*\{[^}]*display:\s*grid[^}]*gap:\s*0[^}]*border:\s*1px solid var\(--color-line-strong\)/s);
  assert.match(pilotageCss, /\.pilotage-search-surface \.suggestion-panel\s*\{[^}]*border-top:\s*1px solid var\(--color-line\)/s);
  assert.match(pilotageCss, /\.suggestion-list\s*\{[^}]*overflow-y:\s*auto/s);
  assert.match(pilotageSource, /clearLabel=\{t\('searchClear'\)\}[\s\S]*?variant="control-deck"/);
  assert.match(searchFieldSource, /variant === 'control-deck' && value && clearLabel/);
  assert.match(searchFieldSource, /className="search-field-clear"[\s\S]*?onChange\(''\);[\s\S]*?inputRef\.current\?\.focus\(\)/);
  assert.match(suggestionSource, /<PolypbaseIcon name="chevron-right" size=\{17\}/);
  assert.doesNotMatch(suggestionSource, /<span className="suggestion-chevron"[^>]*>›<\/span>/);
  assert.doesNotMatch(pilotageCss, /\.desktop-suggestion-slot/);
});

test('desktop and tablet create boxes from the shared page header and reused modal form', () => {
  assert.match(appSource, /<header className=\{activeTab === 'pilotage' \? 'page-heading pilotage-page-heading'/);
  assert.match(appSource, /className="secondary-button button-icon-label pilotage-create-action"/);
  assert.match(appSource, /onClick=\{\(\) => setIsCreateBoxOpen\(true\)\}/);
  assert.match(pilotageSource, /presentation=\{isPhoneLayout \? 'inline' : 'modal'\}/);
  assert.match(createBoxSource, /className="create-box-modal"[\s\S]*?role="dialog"[\s\S]*?aria-modal="true"/);
  assert.equal((createBoxSource.match(/<form className="create-box-form"/g) ?? []).length, 1);
  assert.match(createBoxSource, /className="modal-close-button" type="button"[^>]*onClick=\{\(\) => setOpen\(false\)\}/);
  assert.match(createBoxSource, /onMouseDown=\{\(\) => setOpen\(false\)\}/);
  assert.match(createBoxSource, /isConfirmationOpenRef\.current = !intent;[\s\S]*?confirmed = await confirmAction[\s\S]*?finally \{\s*isConfirmationOpenRef\.current = false;/);
  assert.match(createBoxSource, /if \(isQuickStrainOpenRef\.current \|\| isConfirmationOpenRef\.current\) return;/);
  assert.match(createBoxSource, /onSelectBox\(createdBox\.id\);\s*if \(presentation === 'modal'\) \{[\s\S]*?requestAnimationFrame[\s\S]*?\[data-box-page-focus-target\]/);
  assert.match(readSource('../src/components/BoxPage.tsx'), /<h2 data-box-page-focus-target tabIndex=\{-1\}>\{box\.global_code\}<\/h2>/);
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
  const contextIndex = suggestionSource.indexOf('<span className="suggestion-context">');
  const identityIndex = suggestionSource.lastIndexOf('<span className="suggestion-identity">', contextIndex);
  assert.ok(contextIndex > identityIndex && identityIndex >= 0);
  const desktopBranch = suggestionSource.slice(
    identityIndex,
    suggestionSource.indexOf('<span className="suggestion-reading">', contextIndex),
  );
  assert.doesNotMatch(desktopBranch, /box\.local_code|box\.box_number|strain\.code/);
  assert.match(desktopBranch, /<strong>\{box\.global_code\}<\/strong>/);
  assert.match(desktopBranch, /<strong>\{box\.species\.scientific_name\}<\/strong>/);
  assert.doesNotMatch(pilotageCss, /\.suggestion-identity, \.suggestion-context\) small/);
});

test('Pilotage retains the shared page frame and the phone workflow branch', () => {
  assert.match(layoutCss, /\.page-heading,\s*\.workspace-page\s*\{[^}]*width:\s*min\(var\(--page-width\), 100%\)/s);
  assert.match(pilotageSource, /className=\{`phone-lookup-panel is-\$\{tabletLookupMode\}-mode`\}/);
  assert.match(pilotageSource, /<TabletQrScanner[\s\S]*?onSelectBox=\{onSelectBox\}/);
  assert.match(pilotageCss, /@media \(max-width: 759px\), \(max-width: 900px\) and \(orientation: portrait\)/);
  assert.match(createBoxSource, /presentation === 'inline' \? \(\s*<button\s*className="create-box-toggle"/s);
  assert.match(pilotageCss, /\.create-box-panel:has\(\.create-box-form\)\s*\{[^}]*position:\s*fixed/s);
  assert.match(pilotageCss, /\.create-box-toggle > span\s*\{[^}]*border-radius:\s*50%/s);
});

for (const isPhoneLayout of [false, true]) {
  const device = isPhoneLayout ? 'phone' : 'desktop/tablet';
  test(`actual ${device} module limits results and preserves total/count and recent semantics`, () => {
    const boxes = Array.from({ length: 20 }, (_, i) => ({ id: i + 1 }));
    const recentBoxes = [{ id: 99 }];
    const h = pilotageHarness({ isPhoneLayout, search: 'box', searchResults: boxes, recentBoxes });
    h.manual();
    const results = h.node('SuggestionList').props;
    assert.deepEqual(Array.from(results.boxes, box => box.id), boxes.slice(0, isPhoneLayout ? 5 : 15).map(box => box.id));
    assert.equal(results.totalCount, isPhoneLayout ? 5 : 20);
    assert.equal(results.isPhoneLayout, isPhoneLayout);
    assert.equal(Boolean(h.node('RecentAccessList')), isPhoneLayout);
    h.view.setProps({ search: '   ' });
    assert.equal(h.node('SuggestionList'), undefined);
    assert.equal(h.node('RecentAccessList').props.boxes, recentBoxes);
    assert.equal(h.node('SearchField').props.expanded, false);
    h.node('RecentAccessList').props.onSelectBox(99);
    assert.deepEqual(h.selected, [99]);
  });

  test(`actual ${device} keyboard wraps, resets, clears and scrolls semantic row ids`, () => {
    const h = pilotageHarness({ isPhoneLayout, search: 'box', searchResults: [{ id: 1 }, { id: 2 }, { id: 3 }] });
    h.manual();
    const prefix = isPhoneLayout ? 'box-suggestion' : 'box-search-result';
    const focus = h.env.document.activeElement;
    assert.equal(h.node('SearchField').props.controls, isPhoneLayout ? 'box-suggestions' : 'box-search-results');
    assert.equal(h.key('ArrowUp').defaultPrevented, true);
    assert.equal(h.node('SearchField').props.activeDescendant, `${prefix}-3`);
    assert.equal(h.key('ArrowDown').defaultPrevented, true);
    assert.equal(h.node('SuggestionList').props.selectedBoxId, 1);
    h.key('ArrowDown');
    assert.equal(h.node('SuggestionList').props.selectedBoxId, 2);
    assert.deepEqual(h.scrolled.map(row => row.id), [`${prefix}-3`, `${prefix}-1`, `${prefix}-2`]);
    for (const row of h.scrolled) {
      assert.equal(row.options.block, 'nearest');
      assert.equal(row.options.inline, 'nearest');
    }
    assert.equal(h.env.document.activeElement, focus);
    assert.deepEqual(h.selected, []);
    h.node('SearchField').props.onSubmit();
    assert.deepEqual(h.selected, [2]);
    h.node('SearchField').props.onChange('new');
    h.view.flush();
    assert.deepEqual(h.searched, ['new']);
    assert.equal(h.node('SuggestionList').props.selectedBoxId, 1);
    h.key('ArrowDown');
    assert.equal(h.key('Escape').defaultPrevented, true);
    assert.deepEqual(h.searched, ['new', '']);
    assert.equal(h.node('SuggestionList').props.selectedBoxId, 1);
    h.view.setProps({ search: '', searchResults: [] });
    assert.equal(h.key('ArrowDown').defaultPrevented, false);
    assert.equal(h.key('ArrowUp').defaultPrevented, false);
    assert.equal(h.node('SearchField').props.activeDescendant, undefined);
  });

  test(`actual ${device} module retains loader, collection status and retry callbacks`, () => {
    const h = pilotageHarness({ isPhoneLayout, isLoading: true, search: 'box' });
    assert.equal(h.node('PageLoader').props.variant, 'pilotage');
    assert.equal(h.node('PageLoader').props.label, 'pilotageTitle');
    assert.equal(h.node('SearchField'), undefined);
    assert.equal(h.node('RecentAccessList'), undefined);
    h.view.setProps({ isLoading: false });
    h.manual();
    for (const status of ['idle', 'loading', 'error']) {
      h.view.setProps({ boxCollectionStatus: status });
      assert.equal(h.node('SuggestionList'), undefined);
      assert.equal(h.node('BoxSearchStatus').props.status, status);
      h.node('BoxSearchStatus').props.onRetry();
    }
    assert.equal(h.retries, 3);
    h.view.setProps({ boxCollectionStatus: 'ready' });
    assert.equal(h.node('BoxSearchStatus'), undefined);
    assert.equal(h.node('SuggestionList').props.boxes.length, 0);
    h.node('SuggestionList').props.onClear();
    assert.deepEqual(h.searched, ['']);
  });
}

for (const outcome of ['success', 'missing', 'error']) {
  test(`actual module exact-code fallback handles async ${outcome}`, async () => {
    const pending = deferred();
    const resolved = [];
    const h = pilotageHarness({ search: 'AUR-042', boxCollectionStatus: 'loading',
      onResolveBoxCode(code) { resolved.push(code); return pending.promise; } });
    h.node('SearchField').props.onSubmit();
    assert.deepEqual(resolved, ['AUR-042']);
    assert.deepEqual(h.selected, []);
    if (outcome === 'error') pending.reject(new Error('lookup failed'));
    else pending.resolve(outcome === 'success' ? 42 : null);
    await tick();
    assert.deepEqual(h.selected, outcome === 'success' ? [42] : []);
    assert.equal(h.node('BoxSearchStatus').props.status, 'loading');
    h.view.setProps({ boxCollectionStatus: 'ready' });
    h.node('SearchField').props.onSubmit();
    await tick();
    assert.deepEqual(resolved, ['AUR-042'], 'complete empty results never start a fallback');
  });
}

test('actual phone module switches QR/manual mode without losing recent or resolver callbacks', () => {
  const boxes = [{ id: 1 }];
  const h = pilotageHarness({ isPhoneLayout: true, search: 'box', boxes, searchResults: boxes });
  const scanner = h.node('TabletQrScanner');
  assert.ok(scanner);
  assert.equal(scanner.props.boxes, boxes);
  assert.equal(scanner.props.onResolveBoxCode, h.props.onResolveBoxCode);
  assert.equal(scanner.props.onSelectBox, h.props.onSelectBox);
  assert.equal(h.node('SearchField'), undefined);
  assert.equal(h.node('SuggestionList'), undefined);
  assert.ok(h.node('RecentAccessList'));
  scanner.props.onSelectBox(1);
  assert.deepEqual(h.selected, [1]);
  h.manual();
  assert.equal(h.node('TabletQrScanner'), undefined);
  assert.ok(h.node('SearchField'));
  assert.ok(h.node('SuggestionList'));
  assert.ok(h.node('RecentAccessList'));
  h.view.click(h.nodes.find(node => node.props.role === 'tab' && node.props.children === 'qrCode'));
  assert.ok(h.node('TabletQrScanner'));
  assert.equal(h.node('SearchField'), undefined);
});

test('actual create permission retains active-membership and superuser semantics', () => {
  const profile = { is_superuser: false, active_organization: { id: 1 }, memberships: [
    { organization: { id: 2 }, role: 'admin' },
  ] };
  const cases = [
    [null, false],
    [profile, false],
    [{ ...profile, active_organization: null }, false],
    [{ ...profile, is_superuser: true, active_organization: null }, true],
    ...['admin', 'lab_technician', 'viewer'].map(role => [
      { ...profile, memberships: [...profile.memberships, { organization: { id: 1 }, role }] },
      role !== 'viewer',
    ]),
  ];
  for (const [candidate, expected] of cases) {
    const h = pilotageHarness({ profile: candidate });
    const { userCanCreateBoxes } = h.env.load('../src/utils/profilePermissions.ts');
    assert.equal(userCanCreateBoxes(candidate), expected);
    const panel = h.nodes.find(node => node.type === h.env.load('../src/components/CreateBoxPanel.tsx').default);
    assert.equal(Boolean(panel), expected);
  }
});

for (const isPhoneLayout of [false, true]) test(`actual create panel forwards readiness and callbacks for phone=${isPhoneLayout}`, () => {
  const h = pilotageHarness({ isPhoneLayout, activeOrganizationId: 7, isCreateBoxOpen: true,
    isOptionsLoading: true, boxCollectionStatus: 'loading', profile: { is_superuser: true },
    onManageSpeciesCodes() {},
  });
  const panel = () => h.nodes.find(node => node.type === h.env.load('../src/components/CreateBoxPanel.tsx').default);
  assert.equal(panel().key, 7);
  assert.equal(panel().props.presentation, isPhoneLayout ? 'inline' : 'modal');
  assert.equal(panel().props.isOpen, isPhoneLayout ? undefined : true);
  assert.equal(panel().props.onOpenChange, isPhoneLayout ? undefined : h.props.onCreateBoxOpenChange);
  assert.equal(panel().props.isBoxCollectionReady, undefined);
  assert.equal(panel().props.onRequestBoxCollection, undefined);
  assert.equal(panel().props.boxes, undefined);
  for (const prop of ['exportOptions', 'isOptionsLoading', 'profile', 't', 'onCreateBox', 'onResolveBoxCreation',
    'isOperationCurrent', 'onManageSpeciesCodes', 'onRequestOptions', 'confirmAction', 'onSelectBox']) {
    assert.equal(panel().props[prop], h.props[prop], prop);
  }
  h.view.setProps({ boxCollectionStatus: 'ready' });
  assert.equal(panel().props.isBoxCollectionReady, undefined);
});

test('App imports the actual default module once and wires all 24 Pilotage props', () => {
  const imports = ast.statements.filter(node => ts.isImportDeclaration(node)
    && node.moduleSpecifier.text === './components/PilotageView');
  assert.equal(imports.length, 1);
  assert.equal(imports[0].importClause.name.text, 'PilotageView');
  assert.doesNotMatch(appSource, /function PilotageView\(|function userCanCreateBoxes\(|const (?:PHONE_RESULT_LIMIT|PILOTAGE_RESULT_LIMIT)\s*=/);
  const elements = [];
  function visit(node) {
    if (ts.isJsxSelfClosingElement(node) && node.tagName.getText(ast) === 'PilotageView') elements.push(node);
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.equal(elements.length, 1);
  assert.equal(elements[0].attributes.properties.length, 24);
  const h = pilotageHarness();
  const PilotageView = h.env.load('../src/components/PilotageView.tsx').default;
  const names = ['activeOrganizationId', 'requestBoxCollection', 'requestBoxCollectionRetry', 'findBoxIdByCode',
    'isLoading', 'isCreateBoxOpen', 'isPhoneLayout', 'isExportOptionsLoading', 'search', 'filteredBoxes',
    'recentBoxes', 'createBox', 'resolveBoxCreation', 'setIsCreateBoxOpen', 'confirmAction', 'setSearch', 'openBox', 't'];
  const bindings = Object.fromEntries(names.map(name => [name, { name }]));
  const calls = [];
  Object.assign(bindings, { PilotageView, data: { boxes: [], exportOptions: {}, profile: {} },
    boxCollection: { status: 'ready' }, operationGeneration: 7, organizationRequestGenerationRef: { current: 7 },
    isDesktopApp: true, setIsCreateBoxOpen: value => calls.push(['open', value]),
    openAdminSection: value => calls.push(['admin', value]), setExportOptionsRequested: value => calls.push(['options', value]),
    React: { createElement: (type, props) => ({ type, props }) } });
  const context = vm.createContext(bindings);
  vm.runInContext(ts.transpileModule(`const result = (${elements[0].getText(ast)});`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
  }).outputText, context);
  const result = vm.runInContext('result', context);
  assert.equal(result.type, PilotageView);
  const mapping = { activeOrganizationId: 'activeOrganizationId',
    onRetryBoxCollection: 'requestBoxCollectionRetry', onResolveBoxCode: 'findBoxIdByCode', isLoading: 'isLoading',
    isCreateBoxOpen: 'isCreateBoxOpen', isPhoneLayout: 'isPhoneLayout', isOptionsLoading: 'isExportOptionsLoading',
    search: 'search', searchResults: 'filteredBoxes', recentBoxes: 'recentBoxes', onCreateBox: 'createBox', onResolveBoxCreation: 'resolveBoxCreation',
    onCreateBoxOpenChange: 'setIsCreateBoxOpen', confirmAction: 'confirmAction', onSearch: 'setSearch', onSelectBox: 'openBox', t: 't' };
  for (const [prop, binding] of Object.entries(mapping)) assert.equal(result.props[prop], bindings[binding], prop);
  for (const prop of ['boxes', 'exportOptions', 'profile']) assert.equal(result.props[prop], bindings.data[prop], prop);
  assert.equal(result.props.boxCollectionStatus, 'ready');
  assert.equal(result.props.isOperationCurrent(), true);
  bindings.organizationRequestGenerationRef.current++;
  assert.equal(result.props.isOperationCurrent(), false);
  result.props.onManageSpeciesCodes();
  result.props.onRequestOptions();
  assert.deepEqual(calls, [['open', false], ['admin', 'references'], ['options', true]]);
  context.isDesktopApp = false;
  vm.runInContext(ts.transpileModule(`const phoneResult = (${elements[0].getText(ast)});`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
  }).outputText, context);
  assert.equal(vm.runInContext('phoneResult.props.onManageSpeciesCodes', context), undefined);
});

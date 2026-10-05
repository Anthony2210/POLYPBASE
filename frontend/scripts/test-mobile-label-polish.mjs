import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import * as jsxRuntime from 'react/jsx-runtime';
import { CirclePause, CirclePlay, GitFork, Route } from 'lucide-react';
import ts from 'typescript';
import { ast as appAst, functionNode } from './app-operation-test-harness.mjs';


const read = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
const compilerOptions = {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
};

function evaluate(source, imports = {}, globals = {}) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions }).outputText, {
    exports,
    require(name) {
      if (/^\.{1,2}\/.*\.css$/.test(name)) return {};
      assert.ok(Object.hasOwn(imports, name), `Unexpected test dependency: ${name}`);
      return imports[name];
    },
    ...globals,
  });
  return exports;
}

function appFunction(name, globals = {}) {
  return evaluate(`${functionNode(name).getText(appAst)}\nexports.result = ${name};`, {
    'react/jsx-runtime': jsxRuntime,
  }, globals).result;
}

function nodes(tree) {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  if (!React.isValidElement(tree)) return [];
  return [tree, ...nodes(tree.props.children)];
}

const hasClass = (node, name) => node.props.className?.split(/\s+/).includes(name) ?? false;
function byClass(tree, name) {
  const found = nodes(tree).filter((node) => hasClass(node, name));
  assert.equal(found.length, 1, `Expected one ${name}`);
  return found[0];
}
function text(tree) {
  if (Array.isArray(tree)) return tree.map(text).join('');
  if (React.isValidElement(tree)) return text(tree.props.children);
  return tree == null || typeof tree === 'boolean' ? '' : String(tree);
}

const catalogs = {
  fr: evaluate(read('i18n/fr.ts')).fr,
  en: evaluate(read('i18n/en.ts')).en,
};
function translator(language) {
  return (key) => {
    assert.equal(typeof catalogs[language][key], 'string', `Missing ${language} translation: ${key}`);
    return catalogs[language][key];
  };
}
const getLabelsViewLabels = appFunction('getLabelsViewLabels');
const origin = 'https://polypbase.test';
const contextSignal = new AbortController().signal;
const client = {
  ApiError: class ApiError extends Error {},
  ApiResourceCancelledError: class ApiResourceCancelledError extends Error {},
  getOrganizationResourceSignal: () => contextSignal,
  apiGetResource() { throw new Error('Render tests must not request API resources'); },
};
const qrLabels = evaluate(read('utils/qrLabels.ts'), { '../api/client': client }, {
  URL, window: { location: { origin } },
});
const PolypbaseIcon = evaluate(read('components/PolypbaseIcon.tsx'), {
  'react/jsx-runtime': jsxRuntime,
}).default;
const QrLabel = evaluate(read('components/QrLabel.tsx'), {
  react: React, 'react/jsx-runtime': jsxRuntime, '../api/client': client,
}).default;

const labelsSource = read('components/LabelsView.tsx');
const labelsAst = ts.createSourceFile('LabelsView.tsx', labelsSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const selectionHelper = labelsAst.statements.find((node) =>
  ts.isFunctionDeclaration(node) && node.name?.text === 'getSpeciesSelectionState');
assert.ok(selectionHelper, 'Missing real species selection helper');
const getSpeciesSelectionState = evaluate(`${selectionHelper.getText(labelsAst)}\nexports.result = getSpeciesSelectionState;`).result;
const labelsImports = (hooks, api) => ({
  react: hooks, 'react/jsx-runtime': jsxRuntime,
  'lucide-react': { ChevronDown: () => null, ChevronRight: () => null, Printer: () => null },
  '../utils/qrLabels': api,
  './BoxTrackingPreview': { default: ({ code }) => React.createElement('a', { href: `/boxes/${code}` }, code) },
  './PageLoader': { default: () => null },
});
const LabelsView = evaluate(labelsSource, labelsImports(React, qrLabels)).default;
const boxes = Array.from({ length: 5 }, (_, index) => ({
  id: index + 1, global_code: `ATL-AAU-1.00${index + 1}`, local_code: `AAU-1.00${index + 1}`,
  status: 'active', organization: { id: 1 }, strain: { code: 'AAU-1' },
  species: index < 3 ? { id: 1, scientific_name: 'Aurelia aurita' } : { id: 2, scientific_name: 'Chrysaora pacifica' },
  thermal_zone: { id: 2, name: 'Zone 15' },
  latest_measurement: { measured_on: new Date().toISOString().slice(0, 10), polyp_count: 0 },
}));
function labelsProps(language, selectedCount) {
  const selectedBoxes = boxes.slice(0, selectedCount);
  // A second species makes the global count differ from the first species count.
  if (selectedCount > 0) selectedBoxes.push(boxes[3]);
  return {
    boxes, language, labels: getLabelsViewLabels(translator(language)), t: translator(language),
    profile: { is_superuser: true, memberships: [] }, isLoading: false,
    qrLabelSelection: selectedBoxes.map((box) => qrLabels.buildQrLabelItem(box)),
    onAddQrLabel() {}, onRemoveQrLabel() {}, onClearQrLabelSelection() {}, onOpenBox() {},
  };
}

// Keep real React elements and component code, but expose handlers with the same
// isolated hook-slot pattern as the existing QR preparation tests.
function labelsHarness(api = qrLabels) {
  const slots = [];
  let cursor = 0;
  const hooks = {
    ...React,
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
      return [slots[index], (next) => { slots[index] = typeof next === 'function' ? next(slots[index]) : next; }];
    },
    useRef(initial) { return slots[cursor++] ??= { current: initial }; },
    useMemo(factory) { return factory(); },
  };
  const View = evaluate(labelsSource, labelsImports(hooks, api)).default;
  return { render(props) { cursor = 0; return View(props); } };
}

for (const language of ['fr', 'en']) {
  for (const count of [0, 1, 2, 3]) {
    test(`real LabelsView ${language} metadata shares the helper count with ${count} selected boxes`, () => {
      const props = labelsProps(language, count);
      const tree = labelsHarness().render(props);
      const html = renderToStaticMarkup(React.createElement(LabelsView, props));
      const selectedIds = new Set(props.qrLabelSelection.map((label) => label.id));
      const groups = nodes(tree).filter((node) => hasClass(node, 'label-species-group'));
      assert.equal(groups.length, 2);
      for (const [index, group] of groups.entries()) {
        const groupBoxes = boxes.filter((box) => box.species.id === index + 1);
        const state = getSpeciesSelectionState(groupBoxes, selectedIds);
        if (index === 0) assert.equal(state.selectedCount, count);
        const meta = byClass(group, 'label-species-meta');
        assert.equal(hasClass(meta, 'has-selection'), state.selectedCount > 0);
        assert.equal(text(byClass(meta, 'label-species-total')), props.labels.qrLabelSpeciesCount(groupBoxes.length));
        assert.ok(html.includes(renderToStaticMarkup(meta)), 'Real SSR must retain the same metadata contract');
        const full = nodes(meta).filter((node) => hasClass(node, 'label-species-selected-full'));
        const compact = nodes(meta).filter((node) => hasClass(node, 'label-species-selected-compact'));
        assert.equal(full.length, state.selectedCount > 0 ? 1 : 0);
        assert.equal(compact.length, full.length);
        if (state.selectedCount > 0) {
          const fullText = props.labels.qrLabelSpeciesSelected(state.selectedCount);
          const compactText = props.labels.qrLabelSpeciesSelectedCompact(state.selectedCount);
          assert.equal(text(full[0]), fullText);
          assert.equal(text(compact[0]), compactText);
          assert.equal(compact[0].props['aria-label'], fullText);
          assert.equal([true, 'true'].includes(compact[0].props['aria-hidden']), false);
          assert.ok(compactText.length < fullText.length, 'Phone copy must actually be compact');
          assert.ok(compactText.includes(String(state.selectedCount)));
        }
        const toggle = nodes(group).find((node) => node.type === 'input' && node.props['aria-checked'] !== undefined);
        assert.ok(toggle);
        assert.equal(toggle.props.checked, state.allSelected);
        assert.equal(toggle.props['aria-checked'], state.selectedCount && !state.allSelected ? 'mixed' : state.allSelected);
      }
      assert.equal(nodes(tree).some((node) => hasClass(node, 'label-selection-bar')), count > 0);
    });
  }

  test(`LabelsView ${language} clear, species selection, and print handlers survive compact presentation`, async () => {
    let completePrint;
    const prints = [], cleared = [], added = [], removed = [];
    const harness = labelsHarness({
      ...qrLabels,
      printQrLabels(items, settings) {
        prints.push({ items, settings });
        return new Promise((resolve) => { completePrint = resolve; });
      },
    });
    const props = {
      ...labelsProps(language, 2),
      onClearQrLabelSelection: () => cleared.push(true),
      onAddQrLabel: (item) => added.push(item.id),
      onRemoveQrLabel: (id) => removed.push(id),
    };
    let tree = harness.render(props);
    const clear = byClass(tree, 'label-selection-clear');
    const print = byClass(tree, 'label-selection-print');
    assert.equal(clear.type, 'button');
    assert.equal(clear.props.type, 'button');
    assert.equal(clear.props['aria-label'], props.labels.qrLabelClearSelection);
    clear.props.onClick();
    assert.equal(cleared.length, 1);
    assert.equal(print.props.type, 'button');
    assert.equal(print.props.disabled, false);
    assert.equal(text(print), props.labels.qrLabelPrintCount(props.qrLabelSelection.length));
    print.props.onClick();
    print.props.onClick();
    assert.equal(prints.length, 1, 'The preparation ref must guard repeat clicks');
    assert.deepEqual(Array.from(prints[0].items, (item) => item.id), props.qrLabelSelection.map((item) => item.id));
    assert.equal(prints[0].settings, qrLabels.DEFAULT_QR_LABEL_PRINT_SETTINGS);
    tree = harness.render(props);
    assert.equal(byClass(tree, 'label-selection-print').props.disabled, true);
    assert.ok(nodes(tree).some((node) => node.props.role === 'status' && text(node) === props.labels.qrLabelPreparing));
    completePrint({ status: 'prepared' });
    await new Promise(setImmediate);
    tree = harness.render(props);
    assert.equal(byClass(tree, 'label-selection-print').props.disabled, false);

    const firstGroup = nodes(tree).find((node) => hasClass(node, 'label-species-group'));
    byClass(firstGroup, 'label-species-toggle').props.children.props.onChange();
    assert.deepEqual(added, [3], 'Bulk selection adds only unselected matching boxes');
    const rowToggles = nodes(firstGroup).filter((node) => hasClass(node, 'label-box-toggle'));
    rowToggles[0].props.children.props.onChange();
    assert.deepEqual(removed, [1]);
    const allProps = { ...props, qrLabelSelection: labelsProps(language, 3).qrLabelSelection };
    const selectedGroup = nodes(harness.render(allProps)).find((node) => hasClass(node, 'label-species-group'));
    const speciesToggle = byClass(selectedGroup, 'label-species-toggle').props.children;
    assert.equal(speciesToggle.props.checked, true);
    speciesToggle.props.onChange();
    assert.deepEqual(removed, [1, 1, 2, 3]);
    tree = harness.render({ ...props, qrLabelSelection: [] });
    assert.equal(nodes(tree).some((node) => hasClass(node, 'label-selection-print')), false);
    assert.equal(nodes(tree).some((node) => hasClass(node, 'label-selection-clear')), false);
  });
}

const { PHONE_NAVIGATION_ITEMS } = evaluate(read('utils/phoneNavigation.ts'));
const PhoneBottomNavigation = appFunction('PhoneBottomNavigation', { PHONE_NAVIGATION_ITEMS, PolypbaseIcon });
const destinations = [
  ['overview', 'overview'], ['zones', 'phoneLocations'], ['labels', 'labels'], ['profile', 'profile'],
];
for (const language of ['fr', 'en']) {
  test(`real App phone navigation ${language} retains four destinations and an independent QR action`, () => {
    const t = translator(language);
    for (const activeTab of destinations.map(([tab]) => tab).concat('box')) {
      const selected = [];
      let scans = 0;
      const tree = PhoneBottomNavigation({ activeTab, t, onSelectTab: (tab) => selected.push(tab), onOpenQr: () => scans++ });
      assert.equal(tree.type, 'nav');
      assert.equal(tree.props['aria-label'], t('mainNavigation'));
      const buttons = nodes(tree).filter((node) => node.type === 'button');
      assert.equal(buttons.length, 5);
      assert.deepEqual(buttons.map((node) => node.key), ['overview', 'zones', 'qr', 'labels', 'profile']);
      for (const [tab, labelKey] of destinations) {
        const button = buttons.find((node) => node.key === tab);
        assert.equal(button.props.type, 'button');
        assert.equal(button.props['aria-label'], t(labelKey));
        assert.equal(button.props['aria-current'], tab === activeTab ? 'page' : undefined);
        assert.equal(hasClass(button, 'is-active'), tab === activeTab);
        assert.ok([true, 'true'].includes(byClass(button, 'phone-nav-icon').props['aria-hidden']));
        button.props.onClick();
      }
      assert.deepEqual(selected, destinations.map(([tab]) => tab));
      const scan = byClass(tree, 'phone-nav-qr');
      assert.equal(scan.props.type, 'button');
      assert.equal(scan.props['aria-label'], t('searchOrScan'));
      assert.equal(scan.props['aria-current'], undefined);
      scan.props.onClick();
      assert.equal(scans, 1);
      assert.equal(selected.length, 4, 'Scan must not select a destination');
      const html = renderToStaticMarkup(tree);
      assert.equal((html.match(/<button\b/g) ?? []).length, 5);
      assert.equal((html.match(/aria-current="page"/g) ?? []).length, activeTab === 'box' ? 0 : 1);
    }
  });
}

// Extract the real tools JSX and shared dispatcher so each layout exercises
// the current permission/resource gates and modal callbacks, not a lookalike.
let boxTools;
function findQrTrigger(node) {
  if (ts.isJsxElement(node) && node.openingElement.tagName.getText(appAst) === 'div'
    && node.openingElement.attributes.properties.some((attribute) =>
      ts.isJsxAttribute(attribute) && attribute.name.text === 'className'
      && attribute.initializer && ts.isStringLiteral(attribute.initializer) && attribute.initializer.text === 'box-header-tools')) {
    assert.equal(boxTools, undefined, 'Expected a single box tools block');
    boxTools = node;
  }
  ts.forEachChild(node, findQrTrigger);
}
findQrTrigger(functionNode('BoxPage'));
assert.ok(boxTools, 'Missing box tools JSX');
const boxBody = functionNode('BoxPage').getText(appAst);
const boxActionSource = boxBody.slice(boxBody.indexOf('type BoxAction'), boxBody.indexOf('async function saveMeasurement'));
const RowActionMenu = ({ ariaLabel }) => React.createElement('button', { className: 'row-action-menu-trigger', 'aria-label': ariaLabel });
function boxQrTree(globals) {
  return evaluate(`${boxActionSource}\nexports.result = (${boxTools.getText(appAst)});`, {
    'react/jsx-runtime': jsxRuntime,
  }, {
    QrLabel, PolypbaseIcon, RowActionMenu, CirclePause, CirclePlay, GitFork, Route,
    canShowStatusButton: false, isBoxActive: true, isChangingBoxStatus: false,
    ...globals,
  }).result;
}
for (const language of ['fr', 'en']) {
  for (const layout of ['desktop', 'tablet', 'phone']) {
    const isDesktopApp = layout === 'desktop';
    const isPhoneLayout = layout === 'phone';
    test(`real box tools JSX ${language} ${layout} preserves gates, dispatcher, and label identity`, () => {
      const t = translator(language);
      const box = boxes[0];
      for (const qr of [null, { scanUrl: qrLabels.getBoxScanUrl(box), imageUrl: '/boites/1/qr.svg' }]) {
        for (const canWriteLabData of [false, true]) {
          const opened = [], built = [];
          const tree = boxQrTree({
            box, qr, canWriteLabData, isDesktopApp, isPhoneLayout, isTabletLayout: layout === 'tablet', t,
            setIsQrLabelOpen: (value) => opened.push(['qr', value]),
            setIsMoveOpen: (value) => opened.push(['move', value]),
            setSubcultureError: (value) => opened.push(['subculture-error', value]),
            setIsSubcultureOpen: (value) => opened.push(['subculture', value]),
            buildQrLabelItem(item, imageUrl) {
              built.push([item, imageUrl]);
              return qrLabels.buildQrLabelItem(item, imageUrl);
            },
          });
          const qrButtons = nodes(tree).filter((node) => hasClass(node, 'box-hero-qr'));
          const menus = nodes(tree).filter((node) => node.type === RowActionMenu);
          assert.equal(qrButtons.length, !isPhoneLayout && qr && canWriteLabData ? 1 : 0);
          assert.equal(menus.length, isPhoneLayout && canWriteLabData ? 1 : 0);
          const html = renderToStaticMarkup(tree);
          assert.equal(/<img\b/.test(html), !isPhoneLayout && Boolean(qr) && canWriteLabData);
          if (isPhoneLayout) {
            assert.equal(built.length, 0, 'Phone opens the QR modal through the menu, without an inline image');
            assert.equal(nodes(tree).some((node) => hasClass(node, 'box-compact-action')), false);
            if (canWriteLabData) {
              const menu = menus[0];
              assert.equal(menu.props.ariaLabel, `${t('boxInventoryActions')} ${box.global_code}`);
              assert.deepEqual(Array.from(menu.props.actions, item => item.action), qr ? ['qr', 'move', 'subculture'] : ['move', 'subculture']);
              menu.props.onAction('qr');
              assert.deepEqual(opened, qr ? [['qr', true]] : []);
              menu.props.onAction('move');
              menu.props.onAction('subculture');
              assert.deepEqual(opened.slice(qr ? 1 : 0), [['move', true], ['subculture-error', null], ['subculture', true]]);
            }
          } else if (qr && canWriteLabData) {
            const button = qrButtons[0];
            assert.equal(button.type, 'button');
            assert.equal(button.props.type, 'button');
            assert.equal(button.props['aria-label'], `${t('qrLabelTitle')} ${box.global_code}`);
            assert.equal(button.props.title, qr.scanUrl);
            button.props.onClick();
            assert.deepEqual(opened, [['qr', true]]);
            const label = nodes(button).find((node) => node.type === QrLabel);
            assert.ok(label, 'Desktop and tablet must both retain the actual shared QrLabel');
            assert.equal(label.props.variant, 'trigger');
            assert.equal(label.props.showMetadata, false);
            assert.equal(label.props.altLabel, t('qrCode'));
            assert.equal(built.length, 1);
            assert.equal(built[0][0], box);
            assert.equal(built[0][1], qr.imageUrl);
            assert.deepEqual(label.props.item, qrLabels.buildQrLabelItem(box, qr.imageUrl));
            assert.ok(html.includes(box.global_code));
          } else {
            assert.equal(built.length, 0);
            assert.equal(opened.length, 0);
          }
          if (layout === 'tablet' && canWriteLabData) {
            const buttons = nodes(tree).filter((node) => hasClass(node, 'box-compact-action'));
            assert.deepEqual(buttons.map(button => button.props['aria-label']), [t('moveAction'), t('subcultureAction')]);
            const moveGlyphs = nodes(buttons[0]).filter(node => node.type === Route);
            const subcultureGlyphs = nodes(buttons[1]).filter(node => node.type === GitFork);
            assert.equal(moveGlyphs.length, 1);
            assert.match(html, /<svg[^>]*width="20"[^>]*height="20"[^>]*class="lucide lucide-route"[^>]*aria-hidden="true"/);
            assert.equal(subcultureGlyphs.length, 1);
            for (const glyph of [...moveGlyphs, ...subcultureGlyphs]) {
              assert.equal(glyph.props.size, 20);
              assert.equal(glyph.props['aria-hidden'], 'true');
            }
            assert.equal(subcultureGlyphs[0].props.className, 'box-subculture-glyph');
            for (const button of buttons) {
              assert.equal(hasClass(button, 'box-compact-action--labeled'), true);
              assert.equal(text(nodes(button).find(node => node.type === 'span')), button.props['aria-label']);
            }
            assert.doesNotMatch(html, /box-move-glyph|lucide-arrow-right|lucide-share2/);
            buttons.forEach(button => button.props.onClick());
            assert.deepEqual(opened.slice(qr ? 1 : 0), [['move', true], ['subculture-error', null], ['subculture', true]]);
            if (qr) assert.ok(nodes(tree).indexOf(qrButtons[0]) < nodes(tree).indexOf(buttons[0]), 'Actual QR stays left of tablet actions');
          }
          assert.equal(nodes(tree).some(node => node.type === PolypbaseIcon), false, 'No obsolete icon-only QR trigger');
        }
      }
    });
  }
}

// Source-level CSS contracts: preserve nested media/container scopes rather than
// flattening rules with a regex that could accidentally accept desktop hiding.
function cssRules(source, conditions = []) {
  source = source.replace(/\/\*[\s\S]*?\*\//g, '');
  const result = [];
  let offset = 0;
  while (offset < source.length) {
    const start = source.indexOf('{', offset);
    if (start < 0) break;
    const header = source.slice(offset, start).trim();
    let end = start + 1, depth = 1, quote = '';
    for (; end < source.length && depth; end++) {
      const char = source[end];
      if (quote) {
        if (char === quote && source[end - 1] !== '\\') quote = '';
      } else if (char === '"' || char === "'") quote = char;
      else if (char === '{') depth++;
      else if (char === '}') depth--;
    }
    assert.equal(depth, 0, `Unbalanced CSS block: ${header}`);
    const body = source.slice(start + 1, end - 1);
    if (header.startsWith('@')) result.push(...cssRules(body, [...conditions, header]));
    else result.push({
      selectors: header.split(',').map((selector) => selector.trim()), conditions,
      declarations: Object.fromEntries(body.split(';').map((declaration) => {
        const colon = declaration.indexOf(':');
        return colon < 0 ? null : [declaration.slice(0, colon).trim(), declaration.slice(colon + 1).trim()];
      }).filter(Boolean)),
    });
    offset = end;
  }
  return result;
}
const stylePaths = [...read('styles/index.css').matchAll(/@import\s+['"]\.\/(.+?\.css)['"]/g)].map((match) => match[1]);
const styles = Object.fromEntries(stylePaths.map((path) => [path, cssRules(read(`styles/${path}`))]));
const allRules = Object.values(styles).flat();
const phone = { width: 390, orientation: 'portrait', pointer: 'coarse', hover: 'none' };
const portrait = { ...phone, width: 820 };
const tablet = { width: 960, orientation: 'landscape', pointer: 'coarse', hover: 'none' };
const desktop = { width: 1440, orientation: 'landscape', pointer: 'fine', hover: 'hover' };
function matchesMedia(query, viewport) {
  return query.split(',').some((branch) => {
    let clause = branch.trim();
    const negate = clause.startsWith('not ');
    if (negate) clause = clause.slice(4);
    const match = clause.split(/\s+and\s+/).every((condition) => {
      if (condition === 'screen' || condition === 'all') return true;
      if (condition === 'print') return false;
      const feature = condition.match(/^\((min-width|max-width|orientation|pointer|hover):\s*([^()]+)\)$/);
      assert.ok(feature, `Unsupported scoped media contract: ${condition}`);
      const [, name, value] = feature;
      if (name === 'min-width') return viewport.width >= parseFloat(value);
      if (name === 'max-width') return viewport.width <= parseFloat(value);
      return viewport[name] === value;
    });
    return negate ? !match : match;
  });
}
function mediaApplies(rule, viewport) {
  return rule.conditions.filter((condition) => condition.startsWith('@media '))
    .every((condition) => matchesMedia(condition.slice(7), viewport));
}
function declarations(rules, selector, viewport, predicate = () => true) {
  return Object.assign({}, ...rules.filter((rule) => rule.selectors.includes(selector)
    && mediaApplies(rule, viewport) && predicate(rule)).map((rule) => rule.declarations));
}
function pixels(value, viewport, seen = new Set()) {
  const variable = value?.match(/^var\((--[\w-]+)\)$/)?.[1];
  if (variable) {
    assert.equal(seen.has(variable), false, 'CSS token cycle');
    return pixels(declarations(allRules, ':root', viewport)[variable], viewport, new Set([...seen, variable]));
  }
  assert.match(value ?? '', /^\d+(?:\.\d+)?px$/, `Expected a pixel size or token, got ${value}`);
  return parseFloat(value);
}

const labelsCss = styles['pages/exports-labels.css'];
test('species metadata hides totals only with selection, and swaps full/compact copy only on phones', () => {
  const totalHideRules = allRules.filter((rule) => rule.declarations.display === 'none'
    && rule.selectors.some((selector) => selector.includes('.label-species-total')));
  assert.ok(totalHideRules.length > 0);
  for (const rule of totalHideRules) {
    for (const selector of rule.selectors.filter((value) => value.includes('.label-species-total'))) {
      assert.match(selector, /\.label-species-meta\.has-selection\s+\.label-species-total$/);
    }
    for (const viewport of [tablet, desktop]) assert.equal(mediaApplies(rule, viewport), false);
  }
  for (const rule of allRules.filter((entry) => entry.declarations.display === 'none'
    && entry.selectors.some((selector) => selector.includes('.label-species-selected-full')))) {
    for (const viewport of [tablet, desktop]) assert.equal(mediaApplies(rule, viewport), false, 'Full selection text must stay visible off-phone');
  }
  for (const rule of allRules.filter((entry) => entry.declarations.display && entry.declarations.display !== 'none'
    && entry.selectors.some((selector) => selector.includes('.label-species-selected-compact')))) {
    for (const viewport of [tablet, desktop]) assert.equal(mediaApplies(rule, viewport), false, 'Compact text must stay phone-only');
  }
  for (const viewport of [phone, portrait, { ...phone, width: 759, orientation: 'landscape' }, { ...portrait, width: 900 }]) {
    assert.equal(declarations(labelsCss, '.labels-page .label-species-meta.has-selection .label-species-total', viewport).display, 'none');
    assert.equal(declarations(labelsCss, '.labels-page .label-species-selected-full', viewport).display, 'none');
    const compact = declarations(labelsCss, '.labels-page .label-species-selected-compact', viewport);
    assert.ok(compact.display && compact.display !== 'none');
    assert.equal(compact['white-space'], 'nowrap');
  }
  for (const viewport of [tablet, desktop, { ...tablet, width: 760 }, { ...portrait, width: 901 }]) {
    assert.notEqual(declarations(labelsCss, '.labels-page .label-species-meta.has-selection .label-species-total', viewport).display, 'none');
    assert.notEqual(declarations(labelsCss, '.labels-page .label-species-selected-full', viewport).display, 'none');
    assert.equal(declarations(labelsCss, '.labels-page .label-species-selected-compact', viewport).display, 'none');
  }
});

test('phone navigation preserves 48px destinations and 76px scan target around a smaller 56px visual square', () => {
  const css = styles['responsive/phone.css'];
  const item = declarations(css, '.phone-nav-item', phone);
  const icon = declarations(css, '.phone-nav-icon', phone);
  const scan = declarations(css, '.phone-nav-qr', phone);
  const scanIcon = declarations(css, '.phone-nav-qr .phone-nav-icon', phone);
  assert.equal(pixels(item['min-height'], phone), 48);
  assert.equal(pixels(icon.width, phone), 48);
  assert.equal(pixels(icon.height, phone), 48);
  assert.equal(pixels(scan.width, phone), 76);
  assert.equal(pixels(scan.height, phone), 76);
  assert.equal(pixels(scanIcon.width, phone), 56);
  assert.equal(pixels(scanIcon.height, phone), 56);
  assert.ok(pixels(scanIcon['border-radius'], phone) < pixels(scanIcon.width, phone) / 2, 'Visual scan surface must be a square, not a circle');
  assert.match(declarations(css, '.phone-bottom-nav', phone)['grid-template-columns'], /var\(--phone-qr-size\)/);
});

test('active phone destination has a visible non-color marker without changing its target', () => {
  const css = styles['responsive/phone.css'];
  const marker = declarations(css, '.phone-nav-item.is-active .phone-nav-icon::after', phone);
  assert.ok(marker.content !== undefined && marker.content !== 'none');
  assert.notEqual(marker.display, 'none');
  assert.equal(marker.position, 'absolute');
  assert.ok(pixels(marker.width, phone) > 0);
  assert.ok(pixels(marker.height, phone) > 0);
  assert.ok(marker.background || marker['background-color']);
  assert.equal(declarations(css, '.phone-nav-icon', phone).position, 'relative');
});

test('phone print ink is inset inside the unchanged touch-target button and cannot intercept clicks', () => {
  const target = declarations(labelsCss, '.labels-page .label-selection-actions button', phone);
  const print = declarations(labelsCss, '.labels-page .label-selection-actions .label-selection-print', phone);
  const ink = declarations(labelsCss, '.labels-page .label-selection-print::before', phone);
  assert.equal(target['min-height'], 'var(--touch-target)');
  assert.ok(pixels(target['min-height'], phone) >= 48);
  assert.equal(print.position, 'relative');
  assert.equal(print.background, 'transparent');
  assert.equal(print['border-color'], 'transparent');
  assert.equal(ink.position, 'absolute');
  assert.ok(ink.content !== undefined && ink.content !== 'none');
  assert.equal(ink['pointer-events'], 'none');
  const insets = ink.inset?.match(/var\(--[\w-]+\)|\d+(?:\.\d+)?px/g);
  assert.ok(insets?.length, 'Pseudo-element must inset its ink surface');
  assert.ok(insets.every((value) => pixels(value, phone) > 0));
  assert.ok(ink.background || ink['background-color']);
});

function areaRows(value) {
  const rows = [...(value ?? '').matchAll(/["']([^"']+)["']/g)].map((match) => match[1].trim().split(/\s+/));
  assert.ok(rows.length, 'Missing named header areas');
  return rows;
}
test('Box keeps matching 64px tablet QR/action surfaces, rotated fork, 48px phone menu, and narrow fallback', () => {
  for (const rule of allRules) {
    if (rule.selectors.some((selector) => /\.box-(?:hero-qr|header-tools)\b/.test(selector))) {
      assert.notEqual(rule.declarations.display, 'none', `Hidden box QR/tools: ${rule.selectors.join(', ')}`);
    }
  }
  const tabletRules = styles['responsive/tablet.css'];
  const compactQr = declarations(tabletRules, '.is-tablet .box-hero-qr', tablet);
  for (const property of ['width', 'min-width']) assert.equal(pixels(compactQr[property], tablet), 64);
  const qrLabel = declarations(tabletRules, '.is-tablet .box-hero-qr .qr-label', tablet);
  assert.equal(pixels(qrLabel['--qr-label-image-size'], tablet), 54);
  assert.equal(pixels(qrLabel['--qr-label-padding'], tablet), 4);
  assert.equal(pixels(qrLabel['min-height'], tablet), 64);
  assert.equal(qrLabel.background, '#fff');
  const square = declarations(tabletRules, '.is-tablet .box-compact-action', tablet);
  for (const property of ['width', 'min-width', 'height', 'min-height']) assert.equal(pixels(square[property], tablet), 64);
  assert.equal(square['border-radius'], 'var(--radius-md)');
  assert.equal(pixels(square.width, tablet), pixels(qrLabel.width, tablet));
  assert.equal(pixels(square.height, tablet), pixels(qrLabel['min-height'], tablet));
  assert.equal(declarations(tabletRules, '.is-tablet .box-subculture-glyph', tablet).transform, 'rotate(90deg)');
  const phoneRules = styles['responsive/phone.css'];
  const menu = declarations(phoneRules, '.is-phone .box-header-tools .row-action-menu-trigger', phone);
  for (const property of ['width', 'min-width', 'height', 'min-height']) assert.equal(pixels(menu[property], phone), 48);
  const phoneHeader = declarations(phoneRules, '.entity-header--box.is-phone', phone);
  const phoneRows = areaRows(phoneHeader['--entity-areas']);
  assert.equal(phoneRows[0].at(-1), 'tools');
  assert.ok(phoneRows[0].includes('identity'));
  assert.match(phoneHeader['--entity-columns'], /\b48px\s*$/);
  assert.ok(phoneRows.flat().every(area => area !== 'actions'));
  const tabletHeader = declarations(tabletRules, '.entity-header--box.is-tablet', tablet,
    (rule) => !rule.conditions.some((condition) => condition.startsWith('@container box-detail (width <')));
  const tabletRows = areaRows(tabletHeader['--entity-areas']);
  assert.equal(tabletRows[0].at(-1), 'tools');
  assert.ok(tabletRows[0].includes('identity'));
  assert.ok(tabletRows.flat().every(area => area !== 'actions'), 'Tablet actions share the QR tools group');
  assert.ok(tabletRows.slice(1).some((row) => row.every((area) => area === 'summary')));
  assert.match(tabletHeader['--entity-columns'], /max-content\s*$/);
  const tools = declarations(tabletRules, '.is-tablet .box-header-tools', tablet,
    rule => !rule.conditions.some(condition => condition.startsWith('@container box-detail (width <')));
  assert.equal(tools.display, 'flex');
  assert.equal(tools['align-self'], 'start');
  assert.equal(tools['align-items'], 'center');
  assert.equal(declarations(phoneRules, '.is-phone .box-header-tools', phone)['justify-self'], 'end');
  const narrow = declarations(tabletRules, '.entity-header--box.is-tablet', tablet,
    rule => rule.conditions.includes('@container box-detail (width < 680px)'));
  assert.deepEqual(areaRows(narrow['--entity-areas']), [['identity'], ['tools'], ['summary']]);
  const desktopQr = declarations(styles['components/qr-label.css'], '.qr-label--trigger', desktop);
  assert.ok(pixels(qrLabel.width, tablet) < pixels(desktopQr.width, desktop), 'Tablet QR is slightly smaller than desktop');
});

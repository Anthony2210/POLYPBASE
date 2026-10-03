import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const read = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
function load(path, imports = {}, globals = {}) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(read(path), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, {
    exports, ...globals,
    require(name) {
      assert.ok(Object.hasOwn(imports, name), `Unexpected render dependency: ${name}`);
      return imports[name];
    },
  });
  return exports;
}

// Static initial renders only. Mutation locks, zero/empty payloads, focus and
// confirmation nesting live in test-mutation-dialogs and test-qr-preparation.
const hooks = {
  useMemo: factory => factory(),
  useState: initial => [typeof initial === 'function' ? initial() : initial, () => {}],
  useRef: initial => ({ current: initial }),
  useId: () => ':box-dialog-test:',
  useEffect() {}, useLayoutEffect() {},
};
const contextSignal = new AbortController().signal;
const client = {
  getOrganizationResourceSignal: () => contextSignal,
  apiGetResource() { throw new Error('Static dialog tests must not fetch QR resources'); },
};
const qrLabels = load('utils/qrLabels.ts', { '../api/client': client }, {
  URL, window: { location: { origin: 'https://polypbase.test' } },
});
const QrLabel = load('components/QrLabel.tsx', {
  react: React, 'react/jsx-runtime': jsxRuntime, '../api/client': client,
}).default;
const PolypbaseIcon = load('components/PolypbaseIcon.tsx', { 'react/jsx-runtime': jsxRuntime }).default;
const common = {
  react: hooks, 'react/jsx-runtime': jsxRuntime,
  './ModalPortal': { default: ({ children }) => children },
  './PolypbaseIcon': { default: PolypbaseIcon },
  '../hooks/useMutationDialog': { default: (isBusy, onClose) => ({
    dialogRef: { current: null }, initialFocusRef: { current: null },
    isBusy, close: onClose, submit: callback => callback(),
  }) },
};
const components = {
  qr: load('components/QrLabelModal.tsx', {
    ...common, '../utils/qrLabels': qrLabels, './QrLabel': { default: QrLabel },
  }).default,
  move: load('components/MoveBoxModal.tsx', common).default,
  subculture: load('components/SubcultureModal.tsx', {
    ...common, '../utils/subculture': load('utils/subculture.ts'),
  }).default,
};
const catalogues = Object.fromEntries(['fr', 'en'].map(language => [language, load(`i18n/${language}.ts`)[language]]));
const labelKeys = {
  title: 'qrLabelTitle', close: 'close', download: 'qrLabelDownload', print: 'print', qrCode: 'qrCode',
  addToSelection: 'qrLabelAddToSelection', alreadySelected: 'qrLabelAlreadySelected',
  selectionCount: 'qrLabelSelectionCount', viewSelection: 'qrLabelViewSelection',
  ...Object.fromEntries(['qrLabelPreparing', 'qrLabelPopupBlocked', 'qrLabelQrUnavailable',
    'qrLabelResourceUnavailable', 'qrLabelImagePreparationFailed', 'qrLabelPreparationFailed',
    'qrLabelRetry'].map(key => [key, key])),
};
const box = {
  id: 17, global_code: 'ATL-AAU-1.001', species: { scientific_name: 'Aurelia aurita' },
  strain: { code: 'ATL-AAU-1' }, organization: { id: 1, name: 'Test institution' },
  thermal_zone: { id: 2, name: 'Current zone' },
};
const titles = {
  move: { fr: 'Déplacer la boîte', en: 'Move box' },
  subculture: { fr: 'Repiquer la boîte', en: 'Create a subculture' },
};
const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes)
  : React.isValidElement(tree) ? [tree, ...nodes(tree.props.children)] : [];
const children = tree => [tree.props.children].flat(Infinity).filter(React.isValidElement);
const text = tree => Array.isArray(tree) ? tree.map(text).join('')
  : React.isValidElement(tree) ? text(tree.props.children) : String(tree ?? '');
const hasClass = (node, name) => node.props.className?.split(/\s+/).includes(name) ?? false;
function one(tree, name) {
  const found = nodes(tree).filter(node => hasClass(node, name));
  assert.equal(found.length, 1, `Expected one .${name}`);
  return found[0];
}
function render(kind, language, sourceBox = box, overrides = {}) {
  const labels = Object.fromEntries(Object.entries(labelKeys).map(([prop, key]) => {
    assert.equal(typeof catalogues[language][key], 'string', `Missing ${language}.${key}`);
    return [prop, catalogues[language][key]];
  }));
  return components[kind]({
    box: sourceBox, language, labels, existingBoxes: [sourceBox],
    zones: [2, 3].map(id => ({ id, name: `Zone ${id}`, organization: sourceBox.organization, is_active: true })),
    isSaving: false, error: null, selectedLabels: [], qrImageUrl: '/boites/17/qr.svg',
    onClose() {}, onSubmit: async () => {}, onAddToSelection() {}, onViewSelection() {},
    ...overrides,
  });
}

for (const kind of Object.keys(components)) {
  for (const language of ['fr', 'en']) {
    test(`${kind} ${language}: real render shares the labelled shell, icon close, single body and sibling footer`, () => {
      const tree = render(kind, language);
      const dialog = one(tree, 'box-dialog');
      const backdrop = one(tree, 'box-dialog-backdrop');
      const heading = one(tree, 'box-dialog-heading');
      const close = one(tree, 'box-dialog-close');
      const body = one(tree, 'box-dialog-body');
      const footer = one(tree, 'box-dialog-actions');
      assert.ok(hasClass(dialog, `box-dialog--${kind}`));
      assert.ok(hasClass(backdrop, 'modal-backdrop'));
      assert.equal(backdrop.props.role, 'presentation');
      assert.equal(dialog.props.role, 'dialog');
      assert.equal(dialog.props['aria-modal'], 'true');
      assert.equal(dialog.props.tabIndex, -1);
      assert.equal(heading.type, 'header');
      const title = nodes(heading).find(node => node.props.id === dialog.props['aria-labelledby']);
      assert.ok(title, 'aria-labelledby must resolve inside the heading');
      assert.equal(title.type, 'h2');
      assert.equal(text(title), kind === 'qr' ? catalogues[language].qrLabelTitle : titles[kind][language]);
      assert.equal(nodes(tree).filter(node => node.props.id === title.props.id).length, 1);
      assert.equal(close.type, 'button');
      assert.ok(hasClass(close, 'icon-button'));
      assert.equal(close.props.type, 'button');
      assert.equal(close.props['aria-label'], kind === 'qr' ? catalogues[language].close : language === 'fr' ? 'Annuler' : 'Cancel');
      assert.equal(close.props.title, close.props['aria-label']);
      assert.equal(close.props.disabled, false);
      assert.ok(children(heading).includes(close));
      assert.equal(close.props.children.type, PolypbaseIcon);
      assert.equal(close.props.children.props.name, 'close');
      assert.equal(close.props.children.props['aria-hidden'], 'true');
      assert.equal(text(close), '', 'The close control must not contain a literal x');
      assert.equal(footer.type, 'footer');
      assert.equal(nodes(body).includes(footer), false);
      const parent = kind === 'qr' ? dialog : one(tree, 'box-dialog-form');
      assert.deepEqual(children(parent), kind === 'qr' ? [heading, body, footer] : [body, footer]);
      if (kind !== 'qr') {
        assert.equal(parent.type, 'form');
        assert.deepEqual(children(dialog), [heading, parent]);
        assert.ok(nodes(body).some(node => ['input', 'select', 'textarea'].includes(node.type)));
        assert.ok(nodes(footer).some(node => node.type === 'button' && node.props.type === 'submit'));
      }
      assert.equal(nodes(footer).filter(node => node.type === 'button').length, 2);
      const html = renderToStaticMarkup(tree);
      assert.match(html, /class="icon-button box-dialog-close"/);
      assert.match(renderToStaticMarkup(close), /<svg[^>]*aria-hidden="true"/);
      assert.ok(html.includes(box.global_code));
      assert.doesNotMatch(html, /undefined|aria-describedby=/);
    });

    test(`${kind} ${language}: long source identity stays separate from the action and within the wrapping shell`, () => {
      const longBox = {
        ...box, global_code: `${'LONG-SOURCE-IDENTITY-'.repeat(12)}.001`,
        species: { scientific_name: `Aurelia ${'longscientificname'.repeat(15)}` },
        thermal_zone: { ...box.thermal_zone, name: 'Long thermal zone '.repeat(20) },
        organization: { ...box.organization, name: 'Long institution '.repeat(20) },
      };
      const tree = render(kind, language, longBox);
      const heading = one(tree, 'box-dialog-heading');
      const body = one(tree, 'box-dialog-body');
      const context = one(tree, 'box-dialog-context');
      assert.ok(text(context).includes(longBox.global_code));
      assert.equal(nodes(kind === 'subculture' ? body : heading).includes(context), true);
      assert.equal(nodes(heading).find(node => node.type === 'h2').props.children,
        kind === 'qr' ? catalogues[language].qrLabelTitle : titles[kind][language]);
      if (kind !== 'move') assert.ok(text(body).includes(longBox.species.scientific_name));
      else {
        assert.ok(text(body).includes(longBox.thermal_zone.name));
        assert.ok(text(body).includes(longBox.organization.name));
      }
      const html = renderToStaticMarkup(tree);
      assert.ok(html.includes(longBox.global_code), 'Source identity must not be truncated in JSX');
      if (kind !== 'move') assert.ok(html.includes(longBox.species.scientific_name));
    });
  }
}

test('mutation errors stay in the scroll body, not between the body and its action footer', () => {
  for (const kind of ['move', 'subculture']) {
    const tree = render(kind, 'en', box, { error: 'Long validation error '.repeat(30) });
    const alerts = nodes(tree).filter(node => node.props.role === 'alert');
    assert.equal(alerts.length, 1);
    assert.ok(nodes(one(tree, 'box-dialog-body')).includes(alerts[0]));
    assert.equal(nodes(one(tree, 'box-dialog-actions')).includes(alerts[0]), false);
  }
});

// This parser inspects source contracts, not computed browser styles. It keeps
// media ancestry so a phone-only declaration cannot satisfy a desktop check.
function cssRules(source, media = []) {
  source = source.replace(/\/\*[\s\S]*?\*\//g, '');
  const result = [];
  let cursor = 0;
  while (cursor < source.length) {
    const opening = source.indexOf('{', cursor);
    if (opening < 0) break;
    const selector = source.slice(cursor, opening).trim().replace(/\s+/g, ' ');
    let depth = 1, end = opening + 1;
    for (; depth && end < source.length; end++) {
      if (source[end] === '{') depth++;
      if (source[end] === '}') depth--;
    }
    assert.equal(depth, 0, `Unbalanced CSS block: ${selector}`);
    const body = source.slice(opening + 1, end - 1);
    if (selector.startsWith('@media ')) result.push(...cssRules(body, [...media, selector.slice(7)]));
    else if (selector.startsWith('@')) result.push(...cssRules(body, media));
    else result.push({ selector, media, declarations: Object.fromEntries(body.split(';').filter(part => part.includes(':')).map(part => {
      const colon = part.indexOf(':');
      return [part.slice(0, colon).trim(), part.slice(colon + 1).trim().replace(/\s+/g, ' ')];
    })) });
    cursor = end;
  }
  return result;
}
function matchesMedia(query, { width, height }) {
  return query.split(',').some(branch => branch.split(/\s+and\s+/).every(condition => {
    condition = condition.trim();
    if (condition === 'screen' || condition === 'all') return true;
    if (condition === 'print') return false;
    const size = condition.match(/^\((min|max)-(width|height): (\d+)px\)$/);
    if (size) {
      const actual = size[2] === 'width' ? width : height;
      return size[1] === 'min' ? actual >= Number(size[3]) : actual <= Number(size[3]);
    }
    const orientation = condition.match(/^\(orientation: (portrait|landscape)\)$/);
    if (orientation) return orientation[1] === (height >= width ? 'portrait' : 'landscape');
    assert.fail(`Unsupported source media condition: ${condition}`);
  }));
}
const labRules = cssRules(read('styles/components/lab-dialogs.css'));
function rule(selector, viewport = { width: 1440, height: 1000 }, rules = labRules) {
  const found = rules.filter(entry => entry.selector === selector && entry.media.every(query => matchesMedia(query, viewport)));
  assert.ok(found.length, `Missing active ${selector} at ${viewport.width}x${viewport.height}`);
  return Object.assign({}, ...found.map(entry => entry.declarations));
}
const tokens = rule(':root', undefined, cssRules(read('styles/tokens.css')));
function pixels(value) {
  const token = value.match(/^var\((--[\w-]+)\)$/);
  if (token) return pixels(tokens[token[1]]);
  assert.match(value, /^\d+(?:\.\d+)?px$/);
  return Number.parseFloat(value);
}
const widths = { qr: 480, move: 640, subculture: 760 };
const viewports = [
  { width: 320, height: 568, edge: 8, inset: 16, columns: 1 },
  { width: 390, height: 844, edge: 8, inset: 16, columns: 1 },
  { width: 800, height: 1280, edge: 8, inset: 16, columns: 1 },
  { width: 960, height: 600, edge: 8, inset: 20, columns: 2 },
  { width: 1280, height: 800, edge: 20, inset: 20, columns: 2 },
  { width: 1440, height: 1000, edge: 20, inset: 20, columns: 2 },
  { width: 960, height: 480, edge: 8, inset: 20, columns: 2 },
];
for (const viewport of viewports) {
  test(`CSS source contract ${viewport.width}x${viewport.height}: bounded content widths, safe areas and reachable action shell`, () => {
    const shell = rule('.box-dialog', viewport);
    const backdrop = rule('.box-dialog-backdrop', viewport);
    const heading = rule('.box-dialog .box-dialog-heading', viewport);
    const footer = rule('.box-dialog .box-dialog-actions', viewport);
    const edge = pixels(backdrop['--box-dialog-edge']);
    const inset = pixels(shell['--box-dialog-inset']);
    assert.equal(edge, viewport.edge);
    assert.equal(inset, viewport.inset);
    assert.equal(shell.width, 'min(100%, var(--box-dialog-width))');
    assert.equal(shell['max-height'], 'calc(100dvh - 2 * var(--box-dialog-edge) - env(safe-area-inset-top) - env(safe-area-inset-bottom))');
    assert.equal(backdrop.padding, 'calc(var(--box-dialog-edge) + env(safe-area-inset-top)) calc(var(--box-dialog-edge) + env(safe-area-inset-right)) calc(var(--box-dialog-edge) + env(safe-area-inset-bottom)) calc(var(--box-dialog-edge) + env(safe-area-inset-left))');
    for (const [kind, cap] of Object.entries(widths)) {
      const width = pixels(kind === 'move' ? shell['--box-dialog-width'] : rule(`.box-dialog--${kind}`, viewport)['--box-dialog-width']);
      assert.equal(width, cap);
      for (const safeArea of [{ top: 0, right: 0, bottom: 0, left: 0 }, { top: 24, right: 12, bottom: 34, left: 12 }]) {
        const available = viewport.width - 2 * edge - safeArea.left - safeArea.right;
        const outer = Math.min(available, width);
        const content = outer - 2 * inset - 2;
        assert.ok(outer <= available && outer <= cap);
        assert.ok(content >= 240, `${kind} should retain usable content width`);
        if (viewport.width >= 800) assert.equal(outer, cap, `${kind} must use its content-specific cap, not viewport width`);
        assert.ok(viewport.height - 2 * edge - safeArea.top - safeArea.bottom > 0);
      }
    }
    for (const selector of ['.box-dialog-location-flow', '.box-dialog .subculture-child']) {
      const combined = '.box-dialog-location-flow, .box-dialog .subculture-child';
      const columns = viewport.columns === 1 ? rule(combined, viewport)['grid-template-columns'] : rule(selector, viewport)['grid-template-columns'];
      assert.equal(columns, viewport.columns === 1 ? 'minmax(0, 1fr)' : 'repeat(2, minmax(0, 1fr))');
    }
    assert.equal(heading.flex, '0 0 auto');
    assert.equal(footer.flex, '0 0 auto');
    assert.equal(footer['flex-wrap'], 'wrap');
    assert.equal(footer.position, undefined, 'Footer must participate in layout rather than overlay fields');
    assert.equal(footer['max-height'], undefined);
    if (viewport.height <= 600) assert.equal(pixels(heading['padding-block']), 8);
    else assert.equal(heading.padding, 'var(--space-4) var(--box-dialog-inset)');
    if (viewport.columns === 1) assert.equal(rule('.box-dialog .box-dialog-actions button', viewport).flex, '1 1 0');
  });
}

test('shared body owns scrolling and flex ancestors override the legacy grid/form scroll', () => {
  const shell = rule('.box-dialog');
  const form = rule('.box-dialog .box-dialog-form');
  const body = rule('.box-dialog-body');
  for (const ancestor of [shell, form]) {
    assert.equal(ancestor.display, 'flex');
    assert.equal(ancestor['flex-direction'], 'column');
    assert.equal(ancestor.overflow, 'hidden');
    assert.equal(ancestor.padding, '0');
    assert.equal(ancestor.gap, '0');
  }
  assert.equal(form['min-height'], '0');
  assert.equal(form['max-height'], 'none');
  assert.equal(form.flex, '1 1 auto');
  assert.equal(body.overflow, 'auto');
  assert.equal(body['min-height'], '0');
  assert.equal(body['min-width'], '0');
  assert.equal(body.flex, '1 1 auto');
  assert.equal(body['overscroll-behavior'], 'contain');
  assert.equal(body['scroll-padding-block'], 'var(--space-4)');
  const sharedScrollers = labRules.filter(entry => entry.selector.includes('.box-dialog')
    && Object.entries(entry.declarations).some(([property, value]) => /^overflow(?:-[xy])?$/.test(property) && /^(auto|scroll)$/.test(value)));
  assert.deepEqual(sharedScrollers.map(entry => entry.selector), ['.box-dialog-body']);
  for (const selector of ['.location-history', '.location-row', '.subculture-children', '.subculture-child']) {
    assert.equal(rule(selector).overflow, undefined, `${selector} must not create a nested scroll container`);
  }
});

test('close stays 44px and long titles/context, fields and translated footer labels can wrap', () => {
  const close = rule('.box-dialog .box-dialog-close');
  for (const property of ['width', 'min-width', 'height']) assert.equal(pixels(close[property]), 44);
  assert.equal(close.flex, '0 0 auto');
  assert.equal(close.padding, '0');
  assert.equal(rule('.box-dialog-heading > div')['min-width'], '0');
  for (const selector of ['.box-dialog .box-dialog-heading h2', '.box-dialog .box-dialog-context', '.box-dialog-body :is(p, strong, small, label)', '.box-dialog-actions button']) {
    const declarations = rule(selector);
    assert.equal(declarations['overflow-wrap'], 'anywhere');
    assert.notEqual(declarations['white-space'], 'nowrap');
    assert.notEqual(declarations['text-overflow'], 'ellipsis');
  }
  const buttons = rule('.box-dialog-actions button');
  assert.equal(buttons['white-space'], 'normal');
  assert.equal(pixels(buttons['min-height']), 44);
  assert.equal(rule('.box-dialog-body > *, .box-dialog-body :is(label, input, select, textarea)')['min-width'], '0');
  assert.equal(rule('.box-dialog-body :is(input, select, textarea)')['max-width'], '100%');
  assert.equal(rule('.qr-label-modal-species', undefined, cssRules(read('styles/pages/exports-labels.css')))['overflow-wrap'], 'anywhere');
});

test('shared geometry is imported and later page/responsive layers do not reinstate legacy shell sizing or nested scrolling', () => {
  const index = read('styles/index.css');
  assert.match(index, /@layer tokens, base, layout, components, pages, responsive, print;/);
  assert.match(index, /@import '\.\/components\/lab-dialogs\.css' layer\(components\);/);
  assert.equal(rule('*, *::before, *::after', undefined, cssRules(read('styles/base.css')).filter(entry => entry.media.length === 0))['box-sizing'], 'border-box');
  const shellClass = /\.(?:box-dialog(?:--(?:qr|move|subculture)|-backdrop|-form|-body)?|qr-label-modal|move-modal|subculture-modal|move-form|subculture-form)(?![\w-])/;
  for (const entry of index.matchAll(/@import '\.\/([^']+)' layer\((pages|responsive)\);/g)) {
    for (const css of cssRules(read(`styles/${entry[1]}`))) {
      if (!shellClass.test(css.selector)) continue;
      for (const property of ['width', 'min-width', 'height', 'min-height', 'max-height', 'overflow', 'overflow-x', 'overflow-y']) {
        assert.equal(css.declarations[property], undefined, `${entry[1]} ${css.selector} must not override shared ${property}`);
      }
    }
  }
});

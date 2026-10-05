import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
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
      if (name.endsWith('.css')) return {};
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
const catalogues = Object.fromEntries(['fr', 'en'].map(language => [language, load(`i18n/${language}.ts`)[language]]));
const i18n = load('i18n/index.ts', Object.fromEntries(['fr', 'en'].map(language => [`./${language}`, { [language]: catalogues[language] }])));
const components = {
  qr: load('components/QrLabelModal.tsx', {
    ...common, '../utils/qrLabels': qrLabels, './QrLabel': { default: QrLabel },
  }).default,
  move: load('components/MoveBoxModal.tsx', { ...common, '../i18n': i18n }).default,
  subculture: load('components/SubcultureModal.tsx', {
    ...common, '../utils/subculture': load('utils/subculture.ts'), '../i18n': i18n,
        '../hooks/useSubcultureCodePreview': { default: () => [] },
  }).default,
};
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
  current_polyp_state: { polyp_count: 12, revision: 'opaque-current-revision', source: { kind: 'subculture', id: 8, timestamp: '2026-09-21T12:00:00Z' } },
  latest_measurement: { polyp_count: 999 },
};
const titles = {
  move: { fr: 'Déplacer la boîte', en: 'Move box' },
  subculture: { fr: catalogues.fr.subcultureTitle, en: catalogues.en.subcultureTitle },
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
      assert.doesNotMatch(html, /undefined/);
      if (kind === 'subculture') {
        const allocation = nodes(body).find(node => node.props.name === 'children.0.allocated_polyps');
        assert.equal(allocation.props.required, undefined);
        assert.equal(allocation.props.value, '');
        assert.equal(allocation.props['aria-describedby'], undefined);
        assert.equal(nodes(body).find(node => node.props.id === 'subculture-validation'), undefined);
        assert.equal(nodes(footer).find(node => node.props.type === 'submit').props.disabled, false);
      } else assert.doesNotMatch(html, /aria-describedby=/);
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
      const context = one(tree, kind === 'subculture' ? 'quantitative-subculture-parent' : 'utility-dialog-identity');
      assert.ok(text(context).includes(longBox.global_code));
      assert.ok(text(context).includes(longBox.species.scientific_name));
      assert.equal(nodes(body).includes(context), true);
      assert.equal(nodes(heading).includes(context), false);
      assert.equal(nodes(heading).find(node => node.type === 'h2').props.children,
        kind === 'qr' ? catalogues[language].qrLabelTitle : titles[kind][language]);
      if (kind === 'move') {
        assert.ok(text(body).includes(longBox.thermal_zone.name));
        assert.equal(text(body).includes(longBox.organization.name), false);
      }
      const html = renderToStaticMarkup(tree);
      assert.ok(html.includes(longBox.global_code), 'Source identity must not be truncated in JSX');
      if (kind !== 'move') assert.ok(html.includes(longBox.species.scientific_name));
    });
  }
}

for (const language of ['fr', 'en']) {
  test(`qr ${language}: image-only screen preview stays separate from the physical print label`, () => {
    const tree = render('qr', language);
    const body = one(tree, 'box-dialog-body');
    const scan = one(tree, 'utility-qr-scan');
    const physical = one(tree, 'utility-qr-physical');
    assert.ok(nodes(body).includes(scan));
    assert.ok(nodes(body).includes(physical));
    assert.equal(nodes(scan).includes(physical), false);
    assert.equal(physical.props['aria-hidden'], 'true');
    const preview = children(scan)[0];
    const label = children(physical)[0];
    assert.equal(preview.type, QrLabel);
    assert.equal(preview.props.variant, 'preview');
    assert.equal(preview.props.showMetadata, false);
    assert.equal(label.type, QrLabel);
    assert.equal(label.props.variant, 'label');
    assert.equal(label.props.className, 'qr-label-print-sheet');
    assert.deepEqual(preview.props.item, label.props.item);
    assert.equal(preview.props.altLabel, catalogues[language].qrCode);
    assert.equal(nodes(one(tree, 'box-dialog-actions')).filter(node => node.type === 'button').map(text).join('|'),
      `${catalogues[language].qrLabelDownload}|${catalogues[language].print}`);
  });

  test(`subculture ${language}: explicit allocation is first focus, with live current state and add after expanded children`, () => {
    const tree = render('subculture', language);
    const body = one(tree, 'box-dialog-body');
    const fields = nodes(body).filter(node => ['input', 'select', 'textarea'].includes(node.type));
    assert.deepEqual(fields.map(field => field.props.name), ['children.0.thermal_zone_id', 'children.0.allocated_polyps', 'notes']);
    const allocationField = fields.find(field => field.props.name === 'children.0.allocated_polyps');
    assert.equal(allocationField.props.type, 'number');
    assert.ok(allocationField.ref);
    assert.equal(allocationField.props.required, undefined);
    assert.equal(allocationField.props.value, '');
    assert.equal(allocationField.props.min, '0');
    assert.equal(allocationField.props.step, '1');
    assert.equal(fields.some(node => ['date', 'datetime-local'].includes(node.props.type) || node.props.readOnly || /code/.test(node.props.name ?? '')), false);
    const summary = one(tree, 'quantitative-subculture-summary');
    assert.equal(summary.props.role, 'status');
    assert.equal(summary.props['aria-live'], 'polite');
    assert.equal(summary.props['aria-atomic'], 'true');
    assert.deepEqual(nodes(summary).filter(node => node.type === 'dd').map(text), [`12 ${catalogues[language].polyps}`]);
        assert.deepEqual(nodes(summary).filter(node => node.type === 'dt').map(text), [catalogues[language].subcultureAvailable]);
    const list = one(tree, 'quantitative-subculture-rows');
    const row = one(tree, 'quantitative-subculture-row');
    assert.equal(row.type, 'section');
    assert.ok(nodes(row).find(node => node.props.id === row.props['aria-labelledby']));
    assert.equal(nodes(row).some(node => node.type === 'details' || node.props.hidden), false);
    assert.equal(text(nodes(row).find(node => node.type === 'h4')), `${catalogues[language].subcultureChild} 1 : —`);
        assert.equal(nodes(row).some(node => node.type === 'small'), false);
        assert.equal(nodes(tree).some(node => node.type === 'h3'), false);
        assert.equal(text(one(tree, 'quantitative-subculture-parent')), box.global_code + box.species.scientific_name + catalogues[language].subcultureAvailable + `12 ${catalogues[language].polyps}`);
        assert.ok(nodes(one(tree, 'quantitative-subculture-parent')).includes(summary));
        assert.equal(nodes(tree).some(node => hasClass(node, 'quantitative-subculture-creation-count')), false);
        const note = fields.find(field => field.props.name === 'notes');
        assert.equal(note.type, 'textarea');
        assert.equal(note.props.required, undefined);
        assert.equal(text(one(tree, 'quantitative-subculture-note')), catalogues[language].subcultureNote);
    const add = one(tree, 'quantitative-subculture-add');
    const addButton = children(add)[0];
    assert.ok(hasClass(addButton, 'secondary-button'));
    assert.equal(text(addButton), `+${catalogues[language].subcultureAddChild}`);
    assert.equal(children(addButton)[0].props['aria-hidden'], 'true');
    assert.deepEqual(children(one(tree, 'quantitative-subculture-children')), [list, add]);
    assert.equal(nodes(one(tree, 'box-dialog-actions')).filter(node => node.type === 'button' && node.props.type === 'submit')[0].props.disabled, false);
  });
}

// Lifecycle baseline changes only by the explicitly requested kicker span removal.
// QR remains unchanged. These dialogs are otherwise frozen for this presentation pass.
// Keep exact source preservation in addition to the behavioral QR/lifecycle tests.
for (const [component, digest] of [
  ['QrLabelModal', 'd5d94aeaedad258a3d4d8c5415ebada00d3210c60958fcbd62eae1a960ce7db9'],
  ['MoveBoxModal', 'c28c4df5a40824b86950af1f016f9435b4287fccf8483a2866debf1c869a85b5'],
  ['BoxLifecycleModal', 'b7aae51037ba180a93838a9379f1868e9afbb08cb827fe2cb8a8b485d20ef4e8'],
]) {
  test(`${component}: frozen utility/deactivation implementation remains exactly preserved`, () => {
    const source = read(`components/${component}.tsx`).replace(/\r\n/g, '\n');
    assert.equal(createHash('sha256').update(source).digest('hex'), digest);
  });
}

for (const [path, digest] of [
  ['components/box-utility-dialogs.css', 'b885eb6ab879543d710d2912847d6330152f4b8595d2fe5d74a1a6f40ddf83ae'],
  ['styles/responsive/tablet.css', '469ffa202a35d8a1d14e7602bbe0862879d6a668b35179ce4f0d8e5ffe5bdd83'],
]) {
  test(`${path}: approved utility/tablet styles remain exactly preserved`, () => {
    assert.equal(createHash('sha256').update(read(path).replace(/\r\n/g, '\n')).digest('hex'), digest);
  });
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
function matchesMedia(query, { width, height, pointer = 'fine' }) {
  return query.split(',').some(branch => branch.split(/\s+and\s+/).every(condition => {
    condition = condition.trim();
    if (condition === 'screen' || condition === 'all') return true;
    if (condition === 'print') return false;
    const size = condition.match(/^\((min|max)-(width|height): (\d+)px\)$/);
    if (size) {
      const actual = size[2] === 'width' ? width : height;
      return size[1] === 'min' ? actual >= Number(size[3]) : actual <= Number(size[3]);
    }
    const pointerCondition = condition.match(/^\(pointer: (coarse|fine)\)$/);
    if (pointerCondition) return pointerCondition[1] === pointer;
    const orientation = condition.match(/^\(orientation: (portrait|landscape)\)$/);
    if (orientation) return orientation[1] === (height >= width ? 'portrait' : 'landscape');
    assert.fail(`Unsupported source media condition: ${condition}`);
  }));
}
const labRules = cssRules(read('styles/components/lab-dialogs.css'));
const utilityRules = cssRules(read('components/box-utility-dialogs.css'));
const quantitativeRules = cssRules(read('components/quantitative-subculture.css'));
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
  test(`CSS source contract ${viewport.width}x${viewport.height}: shared baseline widths, safe areas and action shell before local overrides`, () => {
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
      // These are shared defaults, not computed layout: local component CSS can override them.
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

for (const viewport of [...viewports, { width: 1280, height: 800, pointer: 'coarse' }]) {
  test(`local CSS source contract ${viewport.width}x${viewport.height} ${viewport.pointer ?? 'fine'}: implemented utility and quantitative overrides`, () => {
    const compact = viewport.width <= 1023 || viewport.height <= 600 || viewport.pointer === 'coarse';
    assert.equal(rule('.box-dialog.quantitative-subculture', viewport, quantitativeRules)['--box-dialog-width'], '620px');
    assert.equal(rule('.box-dialog-backdrop.quantitative-subculture-backdrop', viewport, quantitativeRules).overflow, 'hidden');
    assert.equal(rule('.box-dialog.quantitative-subculture .box-dialog-form', viewport, quantitativeRules).overflow, 'hidden');
    assert.equal(rule('.box-dialog.quantitative-subculture .quantitative-subculture-body', viewport, quantitativeRules).overflow, 'auto');
    const panel = rule('.quantitative-subculture .quantitative-subculture-parent', viewport, quantitativeRules);
        assert.equal(panel.background, 'var(--color-surface-info)');
        assert.equal(panel['border-left'], '3px solid var(--color-primary)');
        assert.equal(panel.padding, 'var(--space-3)');
        assert.equal(rule('.box-dialog.quantitative-subculture .box-dialog-heading h2', viewport, quantitativeRules)['font-style'], 'italic');
        assert.equal(rule('.quantitative-subculture .box-dialog-close', viewport, quantitativeRules)['border-radius'], '50%');
        const addButton = rule('.quantitative-subculture .quantitative-subculture-add-button', viewport, quantitativeRules);
        assert.equal(addButton.width, '100%');
        assert.equal(addButton.border, '1px solid var(--color-line-strong)');
        assert.equal(addButton['min-height'], '44px');
        assert.equal(rule('.quantitative-subculture :is(button, input, select, textarea):focus-visible', viewport, quantitativeRules).outline, '2px solid var(--color-primary)');
        assert.equal(rule('.quantitative-subculture :is(button, input, select, textarea):disabled', viewport, quantitativeRules).cursor, 'not-allowed');
        if (viewport.width <= 639) {
          assert.equal(rule('.quantitative-subculture :is(input, select, textarea)', viewport, quantitativeRules)['font-size'], '16px');
          assert.equal(panel['flex-wrap'], 'wrap');
        }
        const row = rule('.quantitative-subculture .quantitative-subculture-row', viewport, quantitativeRules);
    assert.equal(row['grid-template-columns'], viewport.width <= 639 ? 'minmax(0, 1fr)' : 'minmax(0, 1fr) minmax(0, 1fr)');
    assert.equal(row['grid-template-areas'], viewport.width <= 639 ? '"identity" "zone" "count"' : '"identity identity" "zone count"');
    assert.equal(row.gap, 'var(--space-2) var(--space-3)');
    for (const area of ['zone', 'count']) assert.equal(rule(`.quantitative-subculture .quantitative-subculture-${area}`, viewport, quantitativeRules)['grid-area'], area);
    if (compact) {
      assert.equal(rule('.quantitative-subculture .quantitative-subculture-summary dl', viewport, quantitativeRules).display, 'flex');
      assert.equal(rule('.quantitative-subculture .box-dialog-actions button', viewport, quantitativeRules).flex, '0 1 auto');
      assert.equal(rule('.quantitative-subculture .quantitative-subculture-add-button', viewport, quantitativeRules)['min-height'], '44px');
      assert.equal(rule(':is(.box-dialog--move, .box-dialog--qr) .box-dialog-actions button', viewport, utilityRules).flex, '0 1 auto');
      assert.equal(rule('.box-dialog--move textarea', viewport, utilityRules)['min-height'], '72px');
      assert.equal(rule('.box-dialog--qr .qr-label-selection-actions button', viewport, utilityRules).background, 'transparent');
      assert.equal(rule('.box-dialog--qr .qr-label-modal-actions button', viewport, utilityRules)['min-height'], '44px');
    }
    if (viewport.width <= 639) assert.equal(rule('.box-dialog.quantitative-subculture', viewport, quantitativeRules)['--box-dialog-inset'], 'var(--space-3)');
    assert.equal(rule('.box-dialog--move .box-dialog-location-flow', viewport, utilityRules)['grid-template-columns'],
      'minmax(0, 1fr)');
    assert.equal(rule('.box-dialog--move .move-subject-panel', viewport, utilityRules).display, 'flex');
    if (viewport.width <= 639) assert.equal(rule('.box-dialog--move .move-subject-panel', viewport, utilityRules)['flex-wrap'], 'wrap');
    assert.equal(rule('.utility-qr-scan', viewport, utilityRules).width,
      viewport.height <= 600 ? viewport.width <= 479 ? 'min(100%, 160px)' : 'min(100%, 144px)'
        : viewport.width <= 639 ? 'min(100%, 192px)'
          : compact ? 'min(100%, 176px)' : 'min(100%, 224px)');
    assert.equal(rule('.utility-qr-physical', viewport, utilityRules).display, 'none');
    assert.equal(rule('.utility-dialog-identity', viewport, utilityRules)['overflow-wrap'], 'anywhere');
    assert.equal(rule('.quantitative-subculture .quantitative-subculture-parent', viewport, quantitativeRules)['overflow-wrap'], 'anywhere');
  });
}

test('local utility screen styling does not override physical print geometry or introduce nested scroll containers', () => {
  for (const component of ['MoveBoxModal', 'QrLabelModal']) assert.match(read(`components/${component}.tsx`), /import '\.\/box-utility-dialogs\.css';/);
  assert.match(read('components/SubcultureModal.tsx'), /import '\.\/quantitative-subculture\.css';/);
  assert.ok(utilityRules.every(entry => entry.media.includes('screen')), 'Utility overrides are screen-only');
  for (const rules of [utilityRules, quantitativeRules]) {
    const scrolls = rules.filter(entry => Object.entries(entry.declarations).some(([property, value]) => /^overflow(?:-[xy])?$/.test(property) && /^(auto|scroll)$/.test(value)));
    assert.ok(scrolls.every(entry => entry.selector.includes('subculture-body')), 'Only the existing body may scroll');
  }
});

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

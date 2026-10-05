import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync, readdirSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';
import { ast, functionNode } from './app-operation-test-harness.mjs';

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const repository = new URL('../../', import.meta.url);
const head = path => execFileSync('git', ['--no-pager', 'show', `HEAD:${path}`], { cwd: repository, encoding: 'utf8' });
function load(source, imports = {}, globals = {}) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, {
    exports, ...globals,
    require(name) {
      if (/^\.{1,2}\/.*\.css$/.test(name)) return {};
      assert.ok(Object.hasOwn(imports, name), `Unexpected presentation dependency: ${name}`);
      return imports[name];
    },
  });
  return exports;
}
const catalogues = Object.fromEntries(['fr', 'en'].map(language => [language, load(read(`../src/i18n/${language}.ts`))[language]]));
const i18n = load(read('../src/i18n/index.ts'), Object.fromEntries(['fr', 'en'].map(language => [`./${language}`, { [language]: catalogues[language] }])));
const translator = language => key => {
  assert.equal(typeof catalogues[language][key], 'string', `Missing ${language} key ${key}`);
  return catalogues[language][key];
};
const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes)
  : React.isValidElement(tree) ? [tree, ...nodes(tree.props.children)] : [];
const hooks = {
  useMemo: factory => factory(),
  useState: initial => [typeof initial === 'function' ? initial() : initial, () => {}],
  useRef: initial => ({ current: initial }), useEffect() {}, useLayoutEffect() {},
};
const ModalPortal = ({ children }) => children;
const common = { react: hooks, 'react/jsx-runtime': jsxRuntime, './ModalPortal': { default: ModalPortal } };
const box = { id: 7, global_code: 'BOX-7', species: { scientific_name: 'Aurelia aurita' },
  organization: { id: 1, name: 'Local lab' }, thermal_zone: { id: 1, name: 'Current zone' } };
const zones = [
  { id: 1, name: 'Current zone', organization: { id: 1 }, is_active: true },
  { id: 2, name: 'Target zone', organization: { id: 1 }, is_active: true },
  { id: 3, name: 'Foreign zone', organization: { id: 2 }, is_active: true },
];

const localKeys = ['moveAction', 'confirmMoveTitle', 'confirmMoveAction', 'moveForbidden', 'moveLocationChanged', 'movementEvent', 'movedTo'];
const localExpected = {
  fr: ['Déplacer', 'Déplacer cette boîte', 'Déplacer', 'Ce compte ne peut pas déplacer de boîte.',
    'L’emplacement de cette boîte a changé. Les informations ont été actualisées : vérifiez-les avant de relancer le déplacement.', 'Déplacement', 'Déplacée vers'],
  en: ['Move', 'Move this box', 'Move', 'This account cannot move boxes.',
    'This box location has changed. Its information has been refreshed: review it before moving the box again.', 'Move', 'Moved to'],
};

for (const language of ['fr', 'en']) {
  test(`local Box movement catalogue uses unambiguous ${language} wording`, () => {
    assert.deepEqual(localKeys.map(key => catalogues[language][key]), localExpected[language]);
    if (language === 'fr') assert.doesNotMatch(localKeys.map(key => catalogues.fr[key]).join(' '), /transf[ée]r/i);
    const getLabels = load(`${functionNode('getBoxInsightsLabels').getText(ast)}\nexports.labels = getBoxInsightsLabels;`).labels;
    assert.equal(getLabels(translator(language)).historyButton, language === 'fr' ? 'Voir détails' : 'View details');
  });

  test(`real MoveBoxModal renders local ${language} title/save without date and retains payload and close callbacks`, async () => {
    const submissions = [], closed = [];
    const MoveBoxModal = load(read('../src/components/MoveBoxModal.tsx'), {
      ...common, '../i18n': i18n,
      './PolypbaseIcon': { default: load(read('../src/components/PolypbaseIcon.tsx'), { 'react/jsx-runtime': jsxRuntime }).default },
      '../hooks/useMutationDialog': { default: (busy, onClose) => ({
        dialogRef: { current: null }, initialFocusRef: { current: null }, isBusy: busy,
        close: onClose, submit: callback => callback(),
      }) },
    }).default;
    const props = { box, zones, language, isSaving: false, error: null,
      onClose: () => closed.push(true), onSubmit: async payload => submissions.push(payload) };
    const tree = MoveBoxModal(props);
    const markup = renderToStaticMarkup(tree);
    const expected = language === 'fr'
      ? ['Déplacer la boîte', 'Déplacer']
      : ['Move box', 'Move'];
    for (const label of expected) assert.ok(markup.includes(label));
    const primary = nodes(tree).find(node => node.type === 'button' && node.props.type === 'submit');
    assert.equal(primary.props.children, expected[1]);
    assert.doesNotMatch(markup, /datetime-local|name="moved_at"|Date du déplacement|Movement date/);
    if (language === 'fr') assert.doesNotMatch(markup, /transf[ée]r/i);
    assert.match(markup, /role="dialog"[^>]*aria-modal="true"[^>]*aria-labelledby="move-title"/);
    assert.ok(markup.includes('BOX-7'));
    assert.ok(markup.includes('Aurelia aurita'));
    assert.ok(markup.includes('Target zone'));
    assert.equal(markup.includes('Foreign zone'), false);
    await nodes(tree).find(node => node.type === 'form').props.onSubmit({ preventDefault() {} });
    assert.equal(submissions.length, 1);
    assert.deepEqual(JSON.parse(JSON.stringify(submissions[0])), {
      expected_thermal_zone_id: 1, thermal_zone_id: 2, notes: '',
    });
    assert.equal(Object.hasOwn(submissions[0], 'moved_at'), false);
    nodes(tree).find(node => node.type === 'button' && node.props['aria-label']).props.onClick();
    assert.deepEqual(closed, [true]);
    const busy = MoveBoxModal({ ...props, isSaving: true });
    assert.ok(nodes(busy).filter(node => node.type === 'button').every(node => node.props.disabled));
    await nodes(busy).find(node => node.type === 'form').props.onSubmit({ preventDefault() {} });
    assert.equal(submissions.length, 1);
  });

  test(`Box handleMove supplies rendered ${language} confirmation labels and preserves acceptance/cancellation`, async () => {
    const confirmations = [], calls = [];
    let accept = false;
    const context = {
      box, zones, currentZone: box.thermal_zone, isSavingMove: false, t: translator(language),
      operationLifetimeRef: { current: true }, isOperationCurrent: () => true,
      confirmAction: async options => { confirmations.push(options); return accept; },
      setIsSavingMove: value => calls.push(['saving', value]), setMoveError: value => calls.push(['error', value]),
      onMoveBox: async (id, payload) => calls.push(['move', id, payload]), setIsMoveOpen: value => calls.push(['open', value]),
    };
    const handleMove = load(`${functionNode('handleMove').getText(ast)}\nexports.handleMove = handleMove;`, {}, context).handleMove;
    const payload = { expected_thermal_zone_id: 1, thermal_zone_id: 2, notes: '' };
    await handleMove(payload);
    assert.deepEqual(calls, []);
    const action = confirmations[0];
    assert.equal(action.title, localExpected[language][1]);
    assert.equal(action.confirmLabel, localExpected[language][2]);
    const ConfirmActionModal = load(`${read('../src/components/ConfirmActionModal.tsx')}\nexports.Modal = ConfirmActionModal;`, {
      ...common, '../utils/confirmActionResolver': { createPendingResolver() {} },
    }).Modal;
    const confirmed = [], cancelled = [];
    const tree = ConfirmActionModal({ action: { ...action, returnFocus: null }, onConfirm: () => confirmed.push(true), onCancel: () => cancelled.push(true) });
    const markup = renderToStaticMarkup(tree);
    assert.ok(markup.includes(action.title));
    assert.ok(markup.includes(action.confirmLabel));
    assert.ok(markup.includes('BOX-7'));
    assert.ok(markup.includes('Current zone'));
    assert.ok(markup.includes('Target zone'));
    for (const [className, output] of [['confirm-action-submit', confirmed], ['confirm-action-cancel', cancelled]]) {
      nodes(tree).find(node => node.type === 'button' && node.props.className?.includes(className)).props.onClick();
      assert.deepEqual(output, [true]);
    }
    accept = true;
    await handleMove(payload);
    assert.deepEqual(calls, [['saving', true], ['error', null], ['move', 7, payload], ['open', false], ['saving', false]]);
  });
}

const transferKeys = [
  'adminTransferTitle', 'adminTransferText', 'adminTransferOutgoingTitle', 'adminTransferIncomingTitle',
  'adminTransferBox', 'adminTransferTarget', 'adminTransferPolyps', 'adminPrepareTransfer',
  'adminTransferImportTitle', 'adminTransferImportAction', 'adminTransferImportConfirmTitle', 'adminTransferImportConfirmMessage',
  'auditActionTransfer', 'auditDescriptionTransferPrepared', 'auditDescriptionTransferImported',
  'auditInlineTransferOut', 'auditInlineTransferImport',
];
function selectedLiteralKeys(source, keys) {
  const file = ts.createSourceFile('catalogue.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const result = {};
  function visit(node) {
    if (ts.isPropertyAssignment(node) && keys.includes(node.name.getText(file))) {
      assert.ok(ts.isStringLiteral(node.initializer));
      result[node.name.getText(file)] = node.initializer.text;
    }
    ts.forEachChild(node, visit);
  }
  visit(file);
  return result;
}
for (const language of ['fr', 'en']) {
  test(`specific inter-institution ${language} transfer keys are unchanged from HEAD`, () => {
    const original = selectedLiteralKeys(head(`frontend/src/i18n/${language}.ts`), transferKeys);
    for (const key of transferKeys) {
      assert.equal(typeof original[key], 'string', `Missing HEAD key ${key}`);
      assert.equal(catalogues[language][key], original[key], `Inter-institution wording changed: ${language}.${key}`);
    }
  });
}

const scannerModalSource = read('../src/components/TabletQrScannerModal.tsx');
let scannerCall;
function findScannerCall(node) {
  if (ts.isJsxSelfClosingElement(node) && node.tagName.getText(ast) === 'TabletQrScannerModal') scannerCall = node;
  ts.forEachChild(node, findScannerCall);
}
findScannerCall(ast);
assert.ok(scannerCall);
for (const language of ['fr', 'en']) {
  test(`real tablet scanner modal ${language} has a title-only accessible heading and forwards autoStart/callbacks`, () => {
    let scannerProps;
    const TabletQrScanner = props => { scannerProps = props; return React.createElement('div', { 'data-scanner-engine': 'unchanged' }); };
    const PolypbaseIcon = load(read('../src/components/PolypbaseIcon.tsx'), { 'react/jsx-runtime': jsxRuntime }).default;
    const Modal = load(scannerModalSource, {
      ...common, './PolypbaseIcon': { default: PolypbaseIcon }, './TabletQrScanner': { default: TabletQrScanner },
    }).default;
    const selected = [], closed = [];
    const appTree = load(`exports.tree = (${scannerCall.getText(ast)});`, { 'react/jsx-runtime': jsxRuntime }, {
      TabletQrScannerModal: Modal, data: { boxes: [box] }, t: translator(language),
      setIsTabletScannerOpen: value => closed.push(value), openScannedBox: id => selected.push(id),
    }).tree;
    assert.equal(Object.hasOwn(appTree.props.labels, 'description'), false);
    const tree = Modal(appTree.props);
    const markup = renderToStaticMarkup(tree);
    const dialog = nodes(tree).find(node => node.props.role === 'dialog');
    const title = nodes(tree).find(node => node.type === 'h2');
    assert.equal(dialog.props['aria-modal'], 'true');
    assert.equal(dialog.props['aria-labelledby'], title.props.id);
    assert.equal(title.props.children, catalogues[language].qrScannerTitle);
    assert.equal(dialog.props['aria-describedby'], undefined);
    const heading = nodes(tree).find(node => node.type === 'header');
    assert.equal(nodes(heading).some(node => node.type === 'p'), false);
    assert.doesNotMatch(markup, /aria-describedby|tablet-scanner-description/);
    assert.ok(markup.includes(catalogues[language].qrScannerTitle));
    const close = nodes(tree).find(node => node.type === 'button');
    assert.equal(close.props['aria-label'], catalogues[language].close);
    close.props.onClick();
    assert.deepEqual(closed, [false]);
    assert.equal(scannerProps.autoStart, true);
    assert.equal(scannerProps.boxes, appTree.props.boxes);
    assert.equal(scannerProps.labels, appTree.props.labels);
    assert.equal(scannerProps.onSelectBox, appTree.props.onSelectBox);
    scannerProps.onSelectBox(7);
    assert.deepEqual(selected, [7]);
  });
}

test('removed scanner description has no catalogue key or source consumer and no CSS subtitle reservation', () => {
  for (const catalogue of Object.values(catalogues)) assert.equal(Object.hasOwn(catalogue, 'qrScannerText'), false);
  function inspect(directory) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = new URL(entry.name + (entry.isDirectory() ? '/' : ''), directory);
      if (entry.isDirectory()) inspect(path);
      else if (/\.tsx?$/.test(entry.name)) assert.doesNotMatch(readFileSync(path, 'utf8'), /\bqrScannerText\b/, path.pathname);
    }
  }
  inspect(new URL('../src/', import.meta.url));
  assert.doesNotMatch(scannerModalSource, /\bdescription\b|aria-describedby/);
  const css = read('../src/styles/components/modals.css');
  assert.match(css, /\.tablet-scanner-modal-heading \{[^}]*min-height: 0;/);
  assert.doesNotMatch(css, /\.tablet-scanner-modal-heading (?:p|:is\(h2, p\))/);
});

test('scanner camera component and QR parser remain identical to HEAD', () => {
  for (const path of ['src/components/TabletQrScanner.tsx', 'src/utils/qrScanner.ts']) {
    assert.equal(read(`../${path}`).replace(/\r\n/g, '\n'), head(`frontend/${path}`).replace(/\r\n/g, '\n'), path);
  }
});

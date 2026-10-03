import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';
import { ast, functionNode } from './app-operation-test-harness.mjs';

function loadTypeScript(relativePath) {
  const source = readFileSync(new URL(relativePath, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  vm.runInNewContext(outputText, { exports });
  return exports;
}

const { PHONE_NAVIGATION_ITEMS } = loadTypeScript('../src/utils/phoneNavigation.ts');
const { filterBoxes } = loadTypeScript('../src/utils/boxLookup.ts');
const { getBoxIdFromQrValue } = loadTypeScript('../src/utils/qrScanner.ts');

const boxes = [
  {
    id: 17,
    global_code: '1-ATL.017',
    local_code: 'ATL-17',
    box_number: '17',
    species: { scientific_name: 'Aurelia aurita' },
    strain: { code: 'ATL' },
    thermal_zone: { name: 'Nursery' },
  },
  {
    id: 23,
    global_code: '1-PAC.023',
    local_code: 'PAC-23',
    box_number: '23',
    species: { scientific_name: 'Chrysaora pacifica' },
    strain: { code: 'PAC' },
    thermal_zone: null,
  },
];

test('Move and Subculture remain available through the phone menu and shared dispatcher', () => {
  for (const path of ['pages/box-detail.css', 'responsive/phone.css', 'responsive/tablet.css']) {
    const css = readFileSync(new URL(`../src/styles/${path}`, import.meta.url), 'utf8');
    assert.doesNotMatch(css, /[^{}]*(?:move-trigger|subculture-trigger|box-header-tools|row-action-menu-trigger)[^{}]*\{[^}]*display:\s*none/, path);
  }
  const boxPage = functionNode('BoxPage');
  const body = boxPage.getText(ast);
  let menuExpression;
  function visit(node) {
    if (ts.isJsxSelfClosingElement(node) && node.tagName.getText(ast) === 'RowActionMenu') {
      assert.equal(menuExpression, undefined, 'Expected one Box phone menu');
      let enclosing = node.parent;
      while (enclosing && !ts.isJsxExpression(enclosing)) enclosing = enclosing.parent;
      assert.ok(enclosing?.expression, 'Missing layout-gated menu expression');
      menuExpression = enclosing.expression;
    }
    ts.forEachChild(node, visit);
  }
  visit(boxPage);
  assert.ok(menuExpression, 'Box phone menu must be rendered by the real JSX');
  const actions = body.slice(body.indexOf('type BoxAction'), body.indexOf('async function saveMeasurement'));
  for (const write of [false, true]) {
    const calls = [];
    const context = {
      React: { createElement: (type, props) => ({ type, props }) }, RowActionMenu: 'menu',
      isPhoneLayout: true, canWriteLabData: write, canShowStatusButton: false,
      qr: null, box: { global_code: 'BOX-17' }, t: key => `translated:${key}`,
      setIsMoveOpen: value => calls.push(['move', value]),
      setIsSubcultureOpen: value => calls.push(['subculture', value]),
    };
    const code = `${actions}\nglobalThis.menu = (${menuExpression.getText(ast)});`;
    vm.runInNewContext(ts.transpileModule(code, {
      compilerOptions: { target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
    }).outputText, context);
    if (write) {
      assert.equal(context.menu.type, 'menu');
      assert.equal(context.menu.props.ariaLabel, 'translated:boxInventoryActions BOX-17');
      assert.deepEqual(Array.from(context.menu.props.actions, item => [item.action, item.label]), [
        ['move', 'translated:moveAction'], ['subculture', 'translated:subcultureAction'],
      ]);
      for (const item of context.menu.props.actions) context.menu.props.onAction(item.action);
      assert.deepEqual(calls, [['move', true], ['subculture', true]]);
    } else {
      assert.equal(context.menu, null, 'No mutation menu for a read-only account');
      context.dispatchBoxAction('move');
      context.dispatchBoxAction('subculture');
      assert.deepEqual(calls, [], 'Dispatcher also enforces action availability');
    }
  }
});

test('phone navigation keeps four destinations around a non-route QR action', () => {
  assert.equal(
    PHONE_NAVIGATION_ITEMS.map((item) => item.kind === 'destination' ? item.tab : item.action).join(','),
    'overview,zones,qr,labels,profile',
  );
  const qrItem = PHONE_NAVIGATION_ITEMS[2];
  assert.equal(qrItem.kind, 'action');
  assert.equal('tab' in qrItem, false);
  assert.equal('path' in qrItem, false);
});

test('manual lookup retains the existing box fields and ordering', () => {
  assert.deepEqual(filterBoxes(boxes, 'aurelia').map((box) => box.id), [17]);
  assert.deepEqual(filterBoxes(boxes, 'PAC-23').map((box) => box.id), [23]);
  assert.deepEqual(filterBoxes(boxes, 'nursery').map((box) => box.id), [17]);
  assert.deepEqual(filterBoxes(boxes, '').map((box) => box.id), [17, 23]);
});

test('QR lookup accepts only the currently supported box values', () => {
  assert.equal(getBoxIdFromQrValue('https://example.test/bac/17/', boxes), 17);
  assert.equal(getBoxIdFromQrValue('/boxes/1-ATL.017', boxes), 17);
  assert.equal(getBoxIdFromQrValue('ATL-17', boxes), 17);
  assert.equal(getBoxIdFromQrValue('unknown-format:17', boxes), null);
});

test('profile no longer renders the phone-only labels shortcut', () => {
  const profileSource = readFileSync(new URL('../src/components/ProfileView.tsx', import.meta.url), 'utf8');
  assert.equal(profileSource.includes('profile-mobile-labels-link'), false);
  assert.equal(profileSource.includes('onOpenLabels'), false);
});

test('profile no longer renders the removed explanatory copy', () => {
  const profileSource = readFileSync(new URL('../src/components/ProfileView.tsx', import.meta.url), 'utf8');
  const fr = readFileSync(new URL('../src/i18n/fr.ts', import.meta.url), 'utf8');
  const en = readFileSync(new URL('../src/i18n/en.ts', import.meta.url), 'utf8');

  assert.doesNotMatch(profileSource, /profileAdminText|profileActiveOrganizationHelp/);
  assert.doesNotMatch(fr, /Elle définit les données visibles et vos droits/);
  assert.doesNotMatch(fr, /Gérer les comptes, les emplacements, les sondes/);
  assert.doesNotMatch(en, /It defines the visible data and your permissions/);
  assert.doesNotMatch(en, /Manage accounts, locations, probes and exchanges/);

  // The separate Administration section and its heading are gone too.
  assert.doesNotMatch(profileSource, /profileAdminTitle|profile-admin-entry/);
  assert.doesNotMatch(fr, /Espace administrateur/);
  assert.doesNotMatch(en, /Administration area/);

  // The account identity, the logout action and the Administration action stay.
  assert.match(profileSource, /<h2>\{fullName\}<\/h2>/);
  assert.match(profileSource, /\{profile\.email \|\| labels\.profileNoEmail\}/);
  assert.match(profileSource, /className="profile-sign-out"/);
  assert.match(profileSource, /\{labels\.profileAdminAction\}/);
});

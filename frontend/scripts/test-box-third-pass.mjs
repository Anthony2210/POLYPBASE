import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { ast, functionNode } from './app-operation-test-harness.mjs';

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const body = functionNode('BoxPage').getText(ast);
const React = { createElement: (type, props, ...children) => ({ type, props: props ?? {}, children: children.flat(Infinity) }) };
const nodes = tree => tree && typeof tree === 'object' ? [tree, ...(tree.children ?? []).flatMap(nodes)] : [];
function evaluate(code, context) {
  vm.runInNewContext(ts.transpileModule(code, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
  }).outputText, context);
}
let header;
function visit(node) {
  if (ts.isJsxElement(node) && node.openingElement.tagName.getText(ast) === 'header'
    && node.getText(ast).includes('box-sheet-hero')) header = node;
  ts.forEachChild(node, visit);
}
visit(functionNode('BoxPage'));
assert.ok(header);
function render({ desktop = false, phone = false, write = true, status = true, active = true, busy = false, qr = true } = {}) {
  const calls = [];
  const context = { React, isDesktopApp: desktop, isPhoneLayout: phone, isTabletLayout: !desktop && !phone,
    canWriteLabData: write, canShowStatusButton: status, isBoxActive: active, isChangingBoxStatus: busy,
    qr: qr ? { imageUrl: '/same-qr.svg', scanUrl: '/bac/7' } : null,
    box: { global_code: 'LONG-BOX-7', species: { scientific_name: 'Aurelia aurita' }, thermal_zone: null },
    statusPresentation: { tone: 'active' }, displayDate: { labelKey: 'created', date: null }, currentZone: null,
    t: key => key, formatSalinity: value => value ?? '-', formatTemperature: value => value ?? '-',
    formatDisplayDate: value => value, buildQrLabelItem: (box, imageUrl) => ({ globalCode: box.global_code, qrImageUrl: imageUrl }),
    setIsQrLabelOpen: value => calls.push(['qr', value]), setIsMoveOpen: value => calls.push(['move', value]),
    setIsSubcultureOpen: value => calls.push(['subculture', value]), setStatusError: value => calls.push(['error', value]),
    setLifecycleAction: value => calls.push(['tracking', value]),
  };
  for (const component of ['QrLabel', 'RowActionMenu', 'InfoPill', 'PolypbaseIcon', 'Route', 'GitFork', 'CirclePause', 'CirclePlay']) context[component] = component;
  evaluate(`${body.slice(body.indexOf('type BoxAction'), body.indexOf('async function saveMeasurement'))}\n globalThis.tree = ${header.getText(ast)};`, context);
  return { elements: nodes(context.tree), calls, context };
}

for (const active of [true, false]) {
  test(`tablet shows real QR left of location, subculture and ${active ? 'pause' : 'play'} tracking icons`, () => {
    const { elements, calls } = render({ active });
    const tools = elements.find(node => node.props.className === 'box-header-tools');
    assert.equal(tools.children[0].props.className, 'box-hero-qr');
    assert.equal(tools.children[1].props.className, 'box-tablet-actions');
    const label = elements.find(node => node.type === 'QrLabel');
    assert.equal(label.props.item.qrImageUrl, '/same-qr.svg');
    assert.equal(label.props.showMetadata, false);
    assert.equal(elements.some(node => node.type === 'RowActionMenu'), false);
    assert.equal(elements.some(node => node.props.className === 'entity-header__actions box-action-stack'), false);
    const buttons = elements.filter(node => node.props.className === 'icon-button box-compact-action');
    assert.deepEqual(buttons.map(node => node.children[0].type), ['Route', 'GitFork', active ? 'CirclePause' : 'CirclePlay']);
    for (const button of buttons) {
      assert.equal(button.children[0].props.size, 36);
      assert.equal(button.children[0].props['aria-hidden'], 'true');
    }
    assert.equal(buttons[1].children[0].props.className, 'box-subculture-glyph');
    for (const button of buttons) { assert.ok(button.props['aria-label']); button.props.onClick(); }
    assert.deepEqual(calls, [['move', true], ['subculture', true], ['error', null], ['tracking', active ? 'deactivate' : 'reactivate']]);
    tools.children[0].props.onClick();
    assert.deepEqual(calls.at(-1), ['qr', true]);
  });
}

test('phone has only the contextual menu and dispatches the same operations', () => {
  const { elements, calls } = render({ phone: true });
  const menu = elements.find(node => node.type === 'RowActionMenu');
  assert.deepEqual(Array.from(menu.props.actions, item => item.action), ['qr', 'move', 'subculture', 'tracking']);
  assert.equal(elements.some(node => ['QrLabel', 'CirclePause', 'CirclePlay', 'Route', 'GitFork'].includes(node.type)), false);
  assert.equal(elements.some(node => node.props.className === 'entity-header__actions box-action-stack'), false);
  for (const item of menu.props.actions) menu.props.onAction(item.action);
  assert.deepEqual(calls, [['qr', true], ['move', true], ['subculture', true], ['error', null], ['tracking', 'deactivate']]);
});

test('busy tracking remains visible but disabled without disabling QR or move', () => {
  for (const phone of [true, false]) {
    const { elements, context, calls } = render({ phone, busy: true });
    const tracking = phone ? elements.find(node => node.type === 'RowActionMenu').props.actions.at(-1)
      : elements.find(node => node.props['aria-label'] === 'saving');
    assert.equal(phone ? tracking.disabled : tracking.props.disabled, true);
    context.dispatchBoxAction('tracking');
    assert.deepEqual(calls, []);
    context.dispatchBoxAction('move'); context.dispatchBoxAction('qr');
    assert.deepEqual(calls, [['move', true], ['qr', true]]);
  }
});

test('read-only and status permissions independently gate every action', () => {
  const { elements, context, calls } = render({ phone: true, write: false, status: false });
  assert.equal(elements.some(node => node.type === 'RowActionMenu' || node.type === 'QrLabel'), false);
  for (const action of ['qr', 'move', 'subculture', 'tracking']) context.dispatchBoxAction(action);
  assert.deepEqual(calls, []);
  const statusOnly = render({ phone: true, write: false, status: true });
  assert.deepEqual(Array.from(statusOnly.elements.find(node => node.type === 'RowActionMenu').props.actions, item => item.action), ['tracking']);
  const noStatus = render({ phone: true, status: false, qr: false });
  assert.deepEqual(Array.from(noStatus.elements.find(node => node.type === 'RowActionMenu').props.actions, item => item.action), ['move', 'subculture']);
});

test('desktop retains full labeled actions, status disabled state and actual QR', () => {
  const { elements, calls } = render({ desktop: true, busy: true });
  assert.ok(elements.find(node => node.type === 'QrLabel'));
  assert.equal(elements.some(node => node.type === 'RowActionMenu' || node.props.className === 'box-tablet-actions'), false);
  const actions = elements.find(node => node.props.className === 'entity-header__actions box-action-stack');
  const buttons = nodes(actions).filter(node => node.type === 'button');
  assert.equal(buttons.length, 3);
  assert.equal(buttons.at(-1).props.disabled, true);
  buttons[0].props.onClick(); buttons[1].props.onClick();
  assert.deepEqual(calls, [['move', true], ['subculture', true]]);
});

test('Box preserves capability expressions, shared back control and QR modal resource', () => {
  assert.match(body, /const isTabletLayout = !isDesktopApp && !isPhoneLayout/);
  assert.match(body, /userCanWriteLabData\(profile, box.organization.id\)/);
  assert.match(body, /const canShowStatusButton = canChangeBoxStatus && \['active', 'inactive'\].includes\(box.status\)/);
  assert.equal((body.match(/<DetailBackButton label=\{t\('back'\)\} onBack=\{onBack\} \/>/g) ?? []).length, 2);
  assert.match(body, /<QrLabelModal[\s\S]*box=\{box\}[\s\S]*qrImageUrl=\{qr.imageUrl\}/);
  const tablet = read('../src/styles/responsive/tablet.css');
  assert.match(tablet, /--qr-label-image-size: 54px/);
  assert.match(tablet, /--qr-label-padding: 4px/);
  assert.match(tablet, /box-compact-action \{[^}]*width: 64px;[^}]*min-width: 64px;[^}]*height: 64px;[^}]*min-height: 64px/);
  assert.match(tablet, /\.is-tablet \.box-hero-qr \{ width: 64px; min-width: 64px/);
  assert.match(tablet, /\.is-tablet \.box-subculture-glyph \{ transform: rotate\(90deg\); \}/);
  assert.match(tablet, /box-compact-action \{[^}]*border-radius: var\(--radius-md\)/);
  assert.match(tablet, /"identity tools" "summary summary"/);
  assert.match(tablet, /grid-template-columns: repeat\(5, minmax\(0, 1fr\)\)/);
  assert.match(read('../src/styles/responsive/phone.css'), /\.is-phone \.box-species-name \{ overflow-wrap: anywhere; \}/);
});

test('RowActionMenu skips disabled items for initial and arrow/Home/End focus and blocks their callback', () => {
  const selectors = [], focused = [], calls = [], effects = [];
  const enabled = ['qr', 'move'].map(name => ({ focus: () => focused.push(name) }));
  const panelRef = { current: {
    querySelector(selector) { selectors.push(selector); return enabled[0]; },
    querySelectorAll(selector) { selectors.push(selector); return enabled; },
  } };
  const context = { React, MoreVertical: 'MoreVertical', document: { body: {}, activeElement: enabled[0] },
    useState: () => [true, () => {}], useCallback: callback => callback, useLayoutEffect: callback => effects.push(callback),
    useAnchoredPopover: () => ({ anchorRef: { current: { focus() {} } }, panelRef, position: { visibility: 'visible' }, id: 'actions' }),
    createPortal: tree => tree,
  };
  evaluate(read('../src/components/RowActionMenu.tsx').replace(/^import .*;\r?\n/gm, '').replace(/export /g, ''), context);
  const tree = context.RowActionMenu({ actions: [{ action: 'qr', label: 'QR' }, { action: 'tracking', label: 'Saving', disabled: true }], onAction: action => calls.push(action), ariaLabel: 'Actions' });
  effects.forEach(effect => effect());
  const menu = nodes(tree).find(node => node.props.role === 'menu');
  for (const key of ['ArrowDown', 'End', 'Home', 'ArrowUp']) menu.props.onKeyDown({ key, preventDefault() {} });
  assert.deepEqual(focused, ['qr', 'move', 'move', 'qr', 'move']);
  assert.ok(selectors.every(selector => selector.includes(':not(:disabled)')));
  const buttons = nodes(menu).filter(node => node.props.role === 'menuitem');
  assert.equal(buttons[1].props.disabled, true);
  buttons[1].props.onClick(); assert.deepEqual(calls, []);
  buttons[0].props.onClick(); assert.deepEqual(calls, ['qr']);
  enabled.length = 0;
  menu.props.onKeyDown({ key: 'ArrowDown', preventDefault() {} });
});

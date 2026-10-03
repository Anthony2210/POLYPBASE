import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { ast, source } from './app-operation-test-harness.mjs';

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const exports = {};
vm.runInNewContext(ts.transpileModule(read('../src/utils/biologicalSalinity.ts'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports });
const { stepBiologicalSalinity } = exports;
const React = { createElement: (type, props, ...children) => ({ type, props: props ?? {}, children: children.flat() }) };
function evaluate(code, context) {
  vm.runInNewContext(ts.transpileModule(code, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
  }).outputText, context);
}
function descendants(tree) {
  return tree && typeof tree === 'object' ? [tree, ...tree.children.flatMap(descendants)] : [];
}
let field;
function visit(node) {
  if (ts.isJsxElement(node) && node.openingElement.attributes.properties.some(attribute =>
    attribute.name?.text === 'className' && attribute.initializer?.text === 'measurement-salinity-field')) field = node;
  ts.forEachChild(node, visit);
}
visit(ast);
assert.ok(field);
function salinityControls(value, language = 'en') {
  const context = { React, StepperButton: 'step', QuickCountButtons: 'quick', PolypbaseIcon: 'icon',
    SALINITY_STEP: 0.1, language, t: () => language === 'fr' ? 'Salinité' : 'Salinity',
    stepBiologicalSalinity, form: { salinity: value },
    setForm: update => { context.form = update(context.form); } };
  evaluate(`const tree = ${field.getText(ast)}; globalThis.tree = tree;`, context);
  return { context, elements: descendants(context.tree) };
}
for (const [value, increment, expected] of [
  ['0', 1, '1'], ['0', 5, '5'], ['0.00', 5, '5'], ['32.15', 1, '33.15'], ['32.15', 5, '37.15'],
  ['', 1, '1'], ['', 5, '5'], ['invalid', 1, 'invalid'], ['32.155', 5, '32.155'],
]) {
  test(`biological quick increment ${increment} maps ${JSON.stringify(value)} to ${expected}`, () => {
    const { context, elements } = salinityControls(value);
    const quick = elements.find(element => element.type === 'quick');
    assert.deepEqual(Array.from(quick.props.values), [1, 5]);
    quick.props.onAdd(increment);
    assert.equal(context.form.salinity, expected);
    assert.equal(quick.props.getAccessibleLabel(increment), `Salinity +${increment} PSU`);
  });
}
test('existing tenth handlers and mixed quick increments remain exact, including zero', () => {
  const { context, elements } = salinityControls('32.15', 'fr');
  const steps = elements.filter(element => element.type === 'step');
  const quick = elements.find(element => element.type === 'quick');
  assert.equal(steps[0].props['aria-label'], 'Salinité -0,1');
  assert.equal(steps[1].props['aria-label'], 'Salinité +0,1');
  for (let index = 0; index < 100; index++) steps[1].props.onStep();
  assert.equal(context.form.salinity, '42.15');
  for (let index = 0; index < 100; index++) steps[0].props.onStep();
  assert.equal(context.form.salinity, '32.15');
  quick.props.onAdd(1); quick.props.onAdd(5); steps[0].props.onStep();
  assert.equal(context.form.salinity, '38.05');
  context.form.salinity = '0.1'; steps[0].props.onStep();
  assert.equal(context.form.salinity, '0');
  steps[0].props.onStep(); assert.equal(context.form.salinity, '0');
  steps[1].props.onStep(); assert.equal(context.form.salinity, '0.1');
});
test('quick buttons expose the salinity accessible label and never submit the form', () => {
  const quickSource = read('../src/components/QuickCountButtons.tsx')
    .replace(/^import .*;\r?\n/gm, '').replace('export default function', 'function');
  const context = { React, useState: initial => [initial, () => {}], useRef: () => ({ current: null }),
    useEffect() {}, triggerHaptic() {}, window: { setTimeout() { return 1; }, clearTimeout() {} } };
  evaluate(quickSource, context);
  const additions = [];
  const tree = context.QuickCountButtons({ values: [1, 5], getAccessibleLabel: value => `Salinité +${value} PSU`, onAdd: value => additions.push(value) });
  for (const button of tree.children) {
    assert.equal(button.props.type, 'button');
    assert.match(button.props['aria-label'], /^Salinité \+[15] PSU$/);
    button.props.onClick();
  }
  assert.deepEqual(additions, [1, 5]);
});
test('tablet composition is non-desktop, container-bounded, and retains phone and narrow fallbacks', () => {
  const tablet = read('../src/styles/responsive/tablet.css');
  const phone = read('../src/styles/responsive/phone.css');
  const compact = tablet.slice(tablet.indexOf('/* Keep the narrow-page fallback'));
  assert.match(compact, /@media not all and \(min-width: 1024px\) and \(hover: hover\) and \(pointer: fine\)/);
  assert.match(compact, /@media \(min-width: 760px\) and \(orientation: landscape\), \(min-width: 901px\)/);
  assert.match(compact, /@container box-detail \(width >= 680px\)/);
  assert.match(compact, /\.entity-header--box\.is-tablet \{[\s\S]*?"identity tools" "summary summary"/);
  assert.match(compact, /@container box-detail \(width < 680px\)[\s\S]*?"identity" "tools" "summary"/);
  assert.match(compact, /\.is-tablet \.box-header-tools \{[\s\S]*?display: flex;/);
  assert.match(compact, /\.is-tablet \.box-hero-qr \{ width: 64px; min-width: 64px;/);
  assert.match(compact, /--qr-label-image-size: 54px;/);
  assert.match(compact, /--qr-label-padding: 4px;/);
  assert.match(compact, /\.is-tablet \.box-compact-action \{[^}]*width: 64px;[^}]*min-width: 64px;[^}]*height: 64px;[^}]*min-height: 64px;[^}]*border-radius: var\(--radius-md\)/);
  assert.match(compact, /\.is-tablet \.box-subculture-glyph \{ transform: rotate\(90deg\); \}/);
  assert.match(compact, /grid-template-columns: repeat\(5, minmax\(0, 1fr\)\)/);
  assert.match(compact, /\.box-summary-metadata\.is-compact \{ display: contents; \}/);
  assert.doesNotMatch(compact, /display: none|overflow: hidden/);
  assert.match(tablet, /@container box-detail \(width < 991px\)/);
  assert.match(phone, /\.entity-header--box\.is-phone \{[^}]*--entity-areas: "identity tools" "summary summary"/);
  assert.match(phone, /\.is-phone \.box-header-tools \.row-action-menu-trigger \{[^}]*width: 48px;[^}]*height: 48px;/);
  const boxCss = read('../src/styles/pages/box-detail.css');
  assert.match(boxCss, /quick-counts button\) \{ min-height: 52px/);
  assert.match(boxCss, /quick-counts button\) \{ min-height: 56px/);
  assert.match(boxCss, /\.measurement-form-section \.quick-counts \{\s*display: none/);
  assert.equal((source.match(/values=\{\[1, 5\]\}/g) ?? []).length, 1);
});

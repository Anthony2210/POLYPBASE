import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { appHarness, ast, functionNode } from './app-operation-test-harness.mjs';

const read = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
const app = read('App.tsx');
const boxCss = read('styles/pages/box-detail.css');
const phoneCss = read('styles/responsive/phone.css');
function nodes(root, predicate) {
  const found = [];
  function visit(node) {
    if (predicate(node)) found.push(node);
    ts.forEachChild(node, visit);
  }
  visit(root);
  return found;
}
function attribute(element, name) {
  return element.attributes.properties.find(attr => ts.isJsxAttribute(attr) && attr.name.text === name)?.initializer;
}

for (const [field, id] of [['polyp', 'polyps'], ['ephyrae', 'ephyrae']]) {
  test(`${field} controls are outside labels explicitly associated with the number input`, () => {
    const wrapper = nodes(ast, node => ts.isJsxElement(node)
      && attribute(node.openingElement, 'className')?.text === `measurement-count-field measurement-${field}-field`)[0];
    assert.ok(wrapper);
    assert.equal(wrapper.openingElement.tagName.getText(ast), 'div');
    const labels = nodes(wrapper, ts.isJsxElement).filter(node => node.openingElement.tagName.getText(ast) === 'label');
    assert.equal(labels.length, 1);
    assert.equal(attribute(labels[0].openingElement, 'htmlFor').text, `measurement-${id}`);
    assert.equal(nodes(labels[0], node => (ts.isJsxElement(node) && ['button', 'StepperButton', 'QuickCountButtons'].includes(node.openingElement.tagName.getText(ast)))
      || (ts.isJsxSelfClosingElement(node) && ['button', 'StepperButton', 'QuickCountButtons'].includes(node.tagName.getText(ast)))).length, 0);
    const input = nodes(wrapper, ts.isJsxSelfClosingElement).find(node => node.tagName.getText(ast) === 'input');
    assert.equal(attribute(input, 'id').text, `measurement-${id}`);
  });
}

function stepperHarness() {
  let instance, index, focused;
  const runtime = { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
  const context = {
    exports: {},
    require: () => runtime,
    useRef(value) {
      const slot = index++;
      return instance.slots[slot] ??= { current: value };
    },
    useState(value) {
      const owner = instance, slot = index++;
      if (!(slot in owner.slots)) owner.slots[slot] = value;
      return [owner.slots[slot], next => { owner.slots[slot] = next; }];
    },
    useEffect() {},
    window: { setTimeout: () => 1, clearTimeout() {}, setInterval: () => 2, clearInterval() {} },
  };
  vm.createContext(context);
  const { outputText } = ts.transpileModule(functionNode('StepperButton').getText(ast), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
  vm.runInContext(outputText, context);
  const controls = new Map();
  function render(name, onStep) {
    instance = controls.get(name) ?? { slots: [] };
    controls.set(name, instance);
    index = 0;
    instance.node = context.StepperButton({ 'aria-label': name, onStep, children: name });
    return instance.node.props;
  }
  function pointer(name, onStep) {
    const props = render(name, onStep);
    props.onPointerDown({ preventDefault() {}, currentTarget: { focus(options) {
      assert.equal(options.preventScroll, true);
      if (focused && focused !== name) controls.get(focused)?.node.props.onBlur();
      focused = name;
    } } });
    return render(name, onStep);
  }
  return { render, pointer, focus: name => { focused = name; }, getFocused: () => focused };
}
for (const field of ['polyps', 'ephyrae']) {
  test(`${field}: positive pointer step moves focus off minus and pressed state remains per-button`, () => {
    const h = stepperHarness();
    let value = '0';
    const counts = appHarness().context;
    const down = () => { value = counts.decrementCountValue(value); };
    const up = () => { value = counts.incrementCountValue(value, 1); };
    h.pointer(`${field}-minus`, down);
    assert.match(h.render(`${field}-minus`, down).className, /is-pressed/);
    const plus = h.pointer(`${field}-plus`, up);
    assert.equal(h.getFocused(), `${field}-plus`);
    assert.match(plus.className, /is-pressed/);
    assert.doesNotMatch(h.render(`${field}-minus`, down).className, /is-pressed/);
    assert.equal(value, '1');
    plus.onPointerUp();
    assert.doesNotMatch(h.render(`${field}-plus`, up).className, /is-pressed/);
    let prevented = false;
    h.render(`${field}-plus`, up).onKeyDown({ key: 'Enter', repeat: false, preventDefault() { prevented = true; } });
    assert.ok(prevented);
    assert.equal(value, '2');
    h.render(`${field}-minus`, down).onKeyDown({ key: ' ', repeat: false, preventDefault() {} });
    assert.equal(value, '1');
  });
}
for (const increment of [1, 10, 25, 50, 100]) {
  test(`count positive increment ${increment} remains exact and zero decrements stay zero`, () => {
    const { context } = appHarness();
    assert.equal(context.incrementCountValue('0', increment), String(increment));
    assert.equal(context.decrementCountValue('0'), '0');
  });
}
test('stepper keyboard focus styling is retained rather than globally removed', () => {
  assert.match(read('styles/base.css'), /button:focus-visible,[\s\S]*?box-shadow: var\(--shadow-focus\)/);
  assert.match(boxCss, /\.count-stepper button\.is-pressed\s*\{/);
  assert.doesNotMatch(boxCss, /(?:count-stepper|quick-counts)[^{}]*:focus-visible[^{}]*\{[^}]*box-shadow:\s*none/);
});

function salinityUtility() {
  const exports = {};
  const { outputText } = ts.transpileModule(read('utils/biologicalSalinity.ts'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  vm.runInNewContext(outputText, { exports });
  return exports;
}
for (const [value, step, expected] of [
  ['32.15', 0.1, '32.25'], ['32.15', -0.1, '32.05'], ['0', 0.1, '0.1'],
  ['0.00', -0.1, '0'], ['', 0.1, '0.1'], ['', -0.1, '0'],
]) {
  test(`biological salinity ${JSON.stringify(value)} ${step > 0 ? '+' : ''}${step} = ${expected}`, () => {
    assert.equal(salinityUtility().stepBiologicalSalinity(value, step), expected);
  });
}
test('salinity repeated tenth steps never display binary drift or mutate absence passively', () => {
  const utility = salinityUtility();
  let value = '32.15';
  for (let index = 0; index < 100; index++) value = utility.stepBiologicalSalinity(value, 0.1);
  assert.equal(value, '42.15');
  for (let index = 0; index < 100; index++) value = utility.stepBiologicalSalinity(value, -0.1);
  assert.equal(value, '32.15');
  assert.equal(utility.formatBiologicalSalinity(''), '');
  assert.match(app, /const SALINITY_STEP = 0\.1;/);
  assert.match(app, /SALINITY_STEP\.toLocaleString\(language\)/);
});
test('metadata shares a responsive grid while desktop keeps the original date location', () => {
  assert.match(app, /\{isDesktopApp \? \(\s*<div className="box-small-facts"/);
  assert.match(app, /box-summary-metadata is-compact/);
  assert.match(boxCss, /\.box-summary-metadata \{ display: contents; \}/);
  assert.match(boxCss, /\.box-summary-metadata\.is-compact\s*\{[^}]*repeat\(auto-fit, minmax\(min\(100%, 128px\), 1fr\)\)/);
  assert.match(boxCss, /\.box-zone-summary \.box-summary-metadata\.is-compact \.info-pill\s*\{[^}]*overflow-wrap: anywhere;[^}]*white-space: normal;/);
});
test('phone scan uses a reserved larger button track and shared navigation clearance', () => {
  assert.match(phoneCss, /--phone-qr-size: 76px;/);
  assert.match(phoneCss, /grid-template-columns: repeat\(2, minmax\(0, 1fr\)\) var\(--phone-qr-size\) repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(phoneCss, /\.phone-nav-qr\s*\{[^}]*width: var\(--phone-qr-size\);[^}]*height: var\(--phone-qr-size\)/);
  assert.match(phoneCss, /padding: var\(--space-4\) 10px var\(--phone-nav-clearance\)/);
  for (const width of [320, 360, 390, 430]) {
    const nav = Math.min(width - 16, 400), scan = 76, gap = 2;
    const slot = (nav - scan - gap * 4) / 4;
    assert.ok(slot >= 48);
    const centers = (slot + scan) / 2 + gap;
    assert.ok(centers > (48 + scan) / 2, 'neighboring touch targets must not overlap');
  }
});

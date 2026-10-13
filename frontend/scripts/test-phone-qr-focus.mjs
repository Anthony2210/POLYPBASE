import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

import { loadComponent } from './ui-primitives-test-harness.mjs';

const compile = source => ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const lookupExports = {};
vm.runInNewContext(compile(readFileSync(new URL('../src/utils/boxLookup.ts', import.meta.url), 'utf8')), { exports: lookupExports });
const searchCode = compile(readFileSync(new URL('../src/components/SearchField.tsx', import.meta.url), 'utf8'));

// Follow the existing Node/TypeScript/VM dialog harness convention. Reconcile
// persistent DOM controls and run dependency-aware effect cleanup after refs attach.
function harness() {
  const slots = [], cleanups = [], setups = [], layout = [], passive = [], listeners = new Set(), focusCalls = [];
  let index = 0, controls = [], tree, props, requests = 0;
  const document = { activeElement: null };
  class Element {
    constructor(name) { this.name = name; this.isConnected = true; }
    focus(options) { focusCalls.push({ element: this, options }); document.activeElement = this; }
  }
  document.body = new Element('body');
  const opener = new Element('opener'), close = new Element('close'), input = new Element('input');
  const dialog = new Element('dialog');
  dialog.contains = node => node === dialog || controls.includes(node);
  dialog.querySelector = () => close;
  dialog.querySelectorAll = () => controls.filter(node => !node.props.disabled);
  opener.focus();
  function effect(queue, fn, deps) {
    const i = index++;
    if (!(i in slots) || !deps || deps.some((value, j) => !Object.is(value, slots[i][j]))) {
      slots[i] = deps;
      setups[i] = { fn, isLayout: queue === layout };
      queue.push(() => { cleanups[i]?.(); cleanups[i] = fn(); });
    }
  }
  const hooks = {
    useRef(initial) { return slots[index++] ??= { current: initial }; },
    useState(initial) {
      const i = index++;
      if (!(i in slots)) slots[i] = initial;
      return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }];
    },
    useMemo(fn) { index++; return fn(); },
    useLayoutEffect(fn, deps) { effect(layout, fn, deps); },
    useEffect(fn, deps) { effect(passive, fn, deps); },
  };
  const jsx = (type, props) => ({ type, props });
  const runtime = { jsx, jsxs: jsx };
  const searchExports = {};
  vm.runInNewContext(searchCode, {
    exports: searchExports,
    require(name) {
      if (name === 'react') return { useRef: () => ({ current: input }) };
      if (name === 'react/jsx-runtime') return runtime;
      if (name === './PolypbaseIcon') return { default: 'icon' };
      throw new Error(`Unexpected import: ${name}`);
    },
  });
  const Modal = loadComponent('QrSearchModal', {
    react: hooks,
    'react/jsx-runtime': runtime,
    './ModalPortal': { default: 'portal' },
    './PolypbaseIcon': { default: 'icon' },
    './TabletQrScanner': { default: 'scanner' },
    './SearchField': searchExports,
    './BoxSearchStatus': { default: loadComponent('BoxSearchStatus', { 'react/jsx-runtime': runtime }) },
    '../utils/boxLookup': lookupExports,
  }, {
    document, HTMLElement: Element, queueMicrotask,
    window: {
      addEventListener(name, fn) { assert.equal(name, 'keydown'); listeners.add(fn); },
      removeEventListener(name, fn) { assert.equal(name, 'keydown'); assert.ok(listeners.delete(fn)); },
    },
  });
  props = {
    boxes: [], boxCollectionStatus: 'idle', t: key => key,
    onClose() {}, onSelectBox() {}, onResolveBoxCode: async () => null,
    onRequestBoxCollection() { requests++; }, onRetryBoxCollection() {},
  };
  function attach(node) {
    if (!node || typeof node !== 'object') return;
    if (typeof node.type === 'function') { attach(node.type(node.props)); return; }
    if (node.props?.role === 'dialog') node.props.ref.current = dialog;
    if (['button', 'input'].includes(node.type)) {
      const element = node.type === 'input' ? input : node.props.className === 'modal-close-button' ? close : new Element(node.props.id ?? 'button');
      element.props = node.props;
      controls.push(element);
      if (node.props.ref) node.props.ref.current = element;
    }
    [node.props?.children].flat(Infinity).forEach(attach);
  }
  function render(updates = {}) {
    props = { ...props, ...updates };
    index = 0;
    tree = Modal(props);
    controls = [];
    attach(tree);
    while (layout.length) layout.shift()();
    while (passive.length) passive.shift()();
  }
  return {
    render, document, opener, close, input, focusCalls, listeners,
    focusNewDialog() { const next = new Element('next-dialog'); next.focus(); return next; },
    get controls() { return controls; }, get requests() { return requests; },
    type(value) { input.props.onChange({ target: { value } }); render(); },
    key(key, shiftKey = false) {
      let prevented = false;
      for (const fn of [...listeners]) fn({ key, shiftKey, preventDefault() { prevented = true; } });
      return prevented;
    },
    replayEffects() {
      cleanups.forEach(fn => fn?.());
      for (const isLayout of [true, false]) {
        setups.forEach((setup, i) => {
          if (setup.isLayout === isLayout) cleanups[i] = setup.fn();
        });
      }
    },
    async unmount() {
      cleanups.forEach(fn => fn?.());
      if (dialog.contains(document.activeElement)) document.activeElement = document.body;
      dialog.isConnected = false;
      controls.forEach(node => { node.isConnected = false; });
      await Promise.resolve();
    },
  };
}

const box = {
  id: 1, global_code: 'BOX-1', local_code: 'LOCAL-1', box_number: '1',
  species: { scientific_name: 'Aurelia aurita' }, strain: { code: 'AA' }, thermal_zone: null,
};

for (const disconnected of [false, true]) {
  test(`StrictMode effect replay preserves the external opener and cancels synthetic restoration (disconnected=${disconnected})`, async () => {
    const h = harness();
    let oldCalls = 0, latestCalls = 0;
    h.render({ onClose: () => oldCalls++ });
    assert.equal(h.document.activeElement, h.close);
    const replayStart = h.focusCalls.length;
    h.replayEffects();
    assert.ok(h.focusCalls.slice(replayStart).every(call => call.element === h.close), 'Synthetic cleanup must never focus the opener');
    await Promise.resolve();
    assert.equal(h.document.activeElement, h.close);
    assert.equal(h.focusCalls.filter(call => call.element === h.opener).length, 1);
    assert.equal(h.listeners.size, 1);
    h.input.focus();
    h.type('BOX');
    const focusCount = h.focusCalls.length;
    h.render({ onClose: () => oldCalls++, boxCollectionStatus: 'loading' });
    h.render({ onClose: () => latestCalls++, boxCollectionStatus: 'ready', boxes: [box] });
    assert.equal(h.document.activeElement, h.input);
    assert.equal(h.focusCalls.length, focusCount);
    assert.equal(h.input.props.value, 'BOX');
    assert.equal(h.key('Escape'), true);
    assert.equal(oldCalls, 0);
    assert.equal(latestCalls, 1);
    h.opener.isConnected = !disconnected;
    await h.unmount();
    assert.equal(h.listeners.size, 0);
    assert.equal(h.focusCalls.length, focusCount + (disconnected ? 0 : 1));
    if (!disconnected) assert.equal(h.document.activeElement, h.opener, 'Genuine unmount restores the original external opener, not the close button');
  });
}

test('replay immediately followed by genuine unmount restores exactly once', async () => {
  const h = harness();
  h.render();
  h.replayEffects();
  const focusCount = h.focusCalls.length;
  const removal = h.unmount();
  assert.equal(h.listeners.size, 0);
  assert.equal(h.focusCalls.length, focusCount, 'Restoration waits for the microtask');
  await removal;
  assert.equal(h.focusCalls.length, focusCount + 1);
  assert.equal(h.document.activeElement, h.opener);
  await Promise.resolve();
  assert.equal(h.focusCalls.length, focusCount + 1, 'Cancelled replay work cannot restore again');
});

test('pending restoration cannot steal focus from a later dialog', async () => {
  const h = harness();
  h.render();
  const removal = h.unmount();
  const nextDialog = h.focusNewDialog();
  const focusCount = h.focusCalls.length;
  await removal;
  assert.equal(h.document.activeElement, nextDialog);
  assert.equal(h.focusCalls.length, focusCount);
  assert.equal(h.listeners.size, 0);
});

test('phone QR search retains input focus through fresh parent callbacks and collection states without intermediate focus', async () => {
  const h = harness();
  h.render();
  assert.equal(h.document.activeElement, h.close);
  h.input.focus();
  h.type('BOX');
  assert.equal(h.input.props.value, 'BOX');
  assert.equal(h.requests, 1);
  const focusCount = h.focusCalls.length;
  for (const updates of [
    {}, { boxCollectionStatus: 'loading' }, { boxCollectionStatus: 'ready', boxes: [box] },
    { boxCollectionStatus: 'error', boxes: [] }, { boxCollectionStatus: 'loading' },
    { boxCollectionStatus: 'ready', boxes: [] },
  ]) {
    h.render({ ...updates, onClose: () => {}, onRequestBoxCollection: () => {} });
    assert.equal(h.document.activeElement, h.input);
    assert.equal(h.input.props.value, 'BOX');
    assert.equal(h.focusCalls.length, focusCount, 'No transient opener or close-button focus');
    assert.equal(h.listeners.size, 1);
  }
  await h.unmount();
  assert.equal(h.document.activeElement, h.opener);
  assert.equal(h.focusCalls.length, focusCount + 1);
  assert.equal(h.focusCalls.at(-1).options.preventScroll, true);
  assert.equal(h.listeners.size, 0);
});

test('Escape uses the latest callback exactly once and all listeners are removed on close', async () => {
  const h = harness();
  let oldCalls = 0, latestCalls = 0;
  h.render({ onClose: () => oldCalls++ });
  h.input.focus();
  h.render({ onClose: () => latestCalls++ });
  assert.equal(h.document.activeElement, h.input);
  assert.equal(h.key('Escape'), true);
  assert.equal(oldCalls, 0);
  assert.equal(latestCalls, 1);
  await h.unmount();
  h.key('Escape');
  assert.equal(latestCalls, 1);
});

test('forward and reverse Tab containment follow current controls after collection updates', async () => {
  const h = harness();
  h.render();
  h.type('BOX');
  for (const updates of [{ boxCollectionStatus: 'loading' }, { boxCollectionStatus: 'ready', boxes: [box] }, { boxCollectionStatus: 'error' }]) {
    h.render({ ...updates, onClose: () => {} });
    const last = h.controls.at(-1);
    h.close.focus();
    assert.equal(h.key('Tab', true), true);
    assert.equal(h.document.activeElement, last);
    assert.equal(h.key('Tab'), true);
    assert.equal(h.document.activeElement, h.close);
    h.opener.focus();
    assert.equal(h.key('Tab', true), true);
    assert.equal(h.document.activeElement, last);
    h.opener.focus();
    assert.equal(h.key('Tab'), true);
    assert.equal(h.document.activeElement, h.close);
    h.close.focus();
    assert.equal(h.key('Tab'), false, 'Interior native Tab navigation is not intercepted');
  }
  await h.unmount();
});

test('actual unmount does not restore a disconnected opener', async () => {
  const h = harness();
  h.render();
  h.input.focus();
  h.opener.isConnected = false;
  const focusCount = h.focusCalls.length;
  await h.unmount();
  assert.equal(h.focusCalls.length, focusCount);
  assert.equal(h.listeners.size, 0);
});

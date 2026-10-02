import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const origin = 'https://polypbase.test';
const label = { id: 17, globalCode: 'ATL-AAU-1.001', speciesName: 'Aurelia aurita', zoneName: 'Zone 15', qrImageUrl: '/boites/17/qr.svg' };
const source = readFileSync(new URL('../src/utils/qrLabels.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const feedback = {
  qrLabelPreparing: 'Preparing labels…', qrLabelPopupBlocked: 'Popup blocked', qrLabelQrUnavailable: 'QR unavailable',
  qrLabelResourceUnavailable: 'Fallback unavailable', qrLabelImagePreparationFailed: 'Image unavailable',
  qrLabelPreparationFailed: 'Preparation unavailable', qrLabelRetry: 'Retry',
};

function resourceImage({ complete = true, valid = true } = {}) {
  const listeners = new Map();
  return {
    complete, naturalWidth: valid ? 100 : 0, src: 'data:image/svg+xml;base64,AAAA',
    addEventListener(name, listener) { listeners.set(name, listener); },
    removeEventListener(name) { listeners.delete(name); },
    emit(name) { listeners.get(name)?.(); }, listeners,
    removeAttribute(name) { if (name === 'src') this.src = ''; },
  };
}

function preparationHarness(options = {}) {
  const calls = [];
  const timers = new Map();
  const timerDelays = new Map();
  const resources = [];
  let timerId = 0;
  const images = options.images ?? [resourceImage()];
  const popup = {
    closed: false,
    document: { images, write(html) { calls.push(['write', html]); }, close() {} },
    focus() { calls.push(['focus']); }, print() { calls.push(['print']); }, close() { this.closed = true; calls.push(['close']); },
  };
  const url = class extends URL {};
  url.createObjectURL = (blob) => { calls.push(['blob', blob]); return 'blob:test'; };
  url.revokeObjectURL = () => calls.push(['revoke']);
  const exports = {};
  vm.runInNewContext(compiled, {
    exports, URL: url, Blob, AbortController, unescape, encodeURIComponent,
    window: {
      location: { origin }, btoa: (text) => Buffer.from(text, 'binary').toString('base64'),
      open() { calls.push(['open']); if (options.openThrows) throw new Error('open'); return options.blocked ? null : popup; },
      setTimeout(callback, delay) {
        const id = ++timerId;
        timers.set(id, () => { timers.delete(id); timerDelays.delete(id); callback(); });
        timerDelays.set(id, delay);
        return id;
      },
      clearTimeout(id) { timers.delete(id); timerDelays.delete(id); },
    },
    fetch: options.fetch ?? (async () => { calls.push(['fetch']); return { ok: true, text: async () => '<svg xmlns="http://www.w3.org/2000/svg" width="25" height="25"/>' }; }),
    Image: class {
      constructor() { const { src: _src, ...image } = resourceImage({ complete: false, valid: options.resourceValid !== false }); Object.assign(this, image); this.src = ''; resources.push(this); }
      set src(value) { this.value = value; if (value && !options.resourcePending) this.emit(options.resourceValid === false ? 'error' : 'load'); }
      get src() { return this.value; }
    },
    document: {
      body: { appendChild() {} },
      createElement: () => ({ click() { calls.push(['download']); if (options.downloadThrows) throw new Error('download'); }, remove() { calls.push(['remove']); } }),
    },
  });
  const runTimers = (delay) => {
    const pending = [...timers.entries()].filter(([id]) => timerDelays.get(id) === delay);
    pending.forEach(([, callback]) => callback());
  };
  return { api: exports, calls, popup, images, timers, timerDelays, resources, runTimers };
}

function expectStatus(result, status, reason) {
  assert.equal(result.status, status);
  if (reason) assert.equal(result.reason, reason);
}
const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

test('empty selection and blocked popup return explicit outcomes without fetching', async () => {
  const h = preparationHarness({ blocked: true });
  expectStatus(await h.api.printQrLabels([]), 'empty');
  assert.equal(h.calls.length, 0);
  expectStatus(await h.api.printQrLabels([label]), 'failed', 'popup-blocked');
  assert.deepEqual(h.calls.map(([name]) => name), ['open']);
});

test('popup opens synchronously and print waits for every image', async () => {
  const images = [resourceImage({ complete: false }), resourceImage({ complete: false })];
  const h = preparationHarness({ images });
  const pending = h.api.printQrLabels([label, { ...label, id: 18 }]);
  assert.equal(h.calls[0][0], 'open');
  await tick();
  assert.equal(h.calls.some(([name]) => name === 'print'), false);
  images[0].emit('load');
  await tick();
  assert.equal(h.calls.some(([name]) => name === 'print'), false);
  images[1].emit('load');
  expectStatus(await pending, 'prepared');
  assert.equal(h.calls.filter(([name]) => name === 'print').length, 1);
  assert.equal(h.timers.size, 0);
  assert.equal(images[0].listeners.size + images[1].listeners.size, 0);
  assert.match(h.calls.find(([name]) => name === 'write')[1], /data:image\/svg\+xml;base64,/);
});

test('HTTP retrieval failure closes partial popup and never invokes print', async () => {
  const h = preparationHarness({ fetch: async () => ({ ok: false }) });
  expectStatus(await h.api.printQrLabels([label]), 'failed', 'qr-retrieval');
  assert.equal(h.popup.closed, true);
  assert.equal(h.calls.some(([name]) => name === 'print' || name === 'write'), false);
});

test('network fallback must load successfully before it can be printed', async () => {
  const fetch = async () => { throw new Error('network'); };
  const good = preparationHarness({ fetch });
  expectStatus(await good.api.printQrLabels([label]), 'prepared');
  assert.match(good.calls.find(([name]) => name === 'write')[1], /https:\/\/polypbase\.test\/boites\/17\/qr.svg/);
  const bad = preparationHarness({ fetch, resourceValid: false });
  expectStatus(await bad.api.printQrLabels([label]), 'failed', 'fallback-resource');
  assert.equal(bad.popup.closed, true);
  assert.equal(bad.calls.some(([name]) => name === 'print'), false);
});

test('downloads never substitute a session-dependent remote resource', async () => {
  const h = preparationHarness({ fetch: async () => { throw new Error('network'); } });
  expectStatus(await h.api.downloadQrLabel(label), 'failed', 'qr-retrieval');
  assert.equal(h.calls.some(([name]) => name === 'download'), false);
});

test('cached broken, event-error, missing and timed-out print images fail preparation', async () => {
  for (const mode of ['cached', 'event', 'missing', 'timeout']) {
    const image = resourceImage({ complete: mode === 'cached', valid: false });
    const h = preparationHarness({ images: mode === 'missing' ? [] : [image] });
    const pending = h.api.printQrLabels([label]);
    await tick();
    if (mode === 'event') image.emit('error');
    if (mode === 'timeout') h.runTimers(15000);
    expectStatus(await pending, 'failed', 'image-preparation');
    assert.equal(h.calls.some(([name]) => name === 'print'), false, mode);
    assert.equal(h.popup.closed, true, mode);
    assert.equal(h.timers.size, 0, mode);
    assert.equal(image.listeners.size, 0, mode);
  }
});

test('fetch timeout settles and does not leave a download pending', async () => {
  const h = preparationHarness({ fetch: (_url, { signal }) => new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error('abort')))) });
  const pending = h.api.downloadQrLabel(label);
  h.runTimers(15000);
  expectStatus(await pending, 'failed', 'qr-retrieval');
  assert.equal(h.timers.size, 0);
});

test('closing the preparation window does not report printer failure', async () => {
  const h = preparationHarness();
  const pending = h.api.printQrLabels([label]);
  h.popup.closed = true;
  expectStatus(await pending, 'cancelled');
  assert.equal(h.calls.some(([name]) => name === 'print'), false);
});

for (const outcome of ['error', 'timeout']) {
  test(`popup closed while image pending wins over its later ${outcome}`, async () => {
    const image = resourceImage({ complete: false });
    const h = preparationHarness({ images: [image] });
    const pending = h.api.printQrLabels([label]);
    await tick();
    assert.equal(image.listeners.size, 2);
    h.popup.closed = true;
    // Exercise the race before the 100 ms closed-window monitor runs.
    if (outcome === 'error') image.emit('error');
    else h.runTimers(15000);
    expectStatus(await pending, 'cancelled');
    assert.equal(h.calls.some(([name]) => name === 'print'), false);
    assert.equal(image.listeners.size, 0);
    assert.equal(h.timers.size, 0);
  });
}

for (const outcome of ['error', 'timeout']) {
  test(`popup close settles promptly and disposes images before a late ${outcome}`, async () => {
    const image = resourceImage({ complete: false });
    const h = preparationHarness({ images: [image] });
    const pending = h.api.printQrLabels([label]);
    await tick();
    const staleTimeout = [...h.timers.entries()].find(([id]) => h.timerDelays.get(id) === 15000)[1];
    h.popup.closed = true;
    h.runTimers(100);
    expectStatus(await pending, 'cancelled');
    assert.equal(image.listeners.size, 0);
    assert.equal(image.src, '');
    assert.equal(h.timers.size, 0);
    if (outcome === 'error') image.emit('error');
    else staleTimeout();
    await tick();
    assert.equal(h.calls.some(([name]) => name === 'print'), false);
  });
}

test('one failed batch image disposes siblings before failure resolves and retry starts cleanly', async () => {
  const images = [resourceImage({ complete: false }), resourceImage({ complete: false })];
  const h = preparationHarness({ images });
  const pending = h.api.printQrLabels([label, { ...label, id: 18 }]);
  await tick();
  const oldError = images[1].listeners.get('error');
  const oldTimeouts = [...h.timers.values()];
  images[0].emit('error');
  expectStatus(await pending, 'failed', 'image-preparation');
  assert.equal(h.popup.closed, true);
  assert.equal(h.timers.size, 0);
  assert.equal(images[0].listeners.size + images[1].listeners.size, 0);
  assert.equal(images[1].src, '');
  assert.equal(h.calls.some(([name]) => name === 'print'), false);

  h.popup.closed = false;
  h.popup.document.images = [resourceImage({ complete: false }), resourceImage({ complete: false })];
  const retry = h.api.printQrLabels([label, { ...label, id: 18 }]);
  await tick();
  oldError();
  oldTimeouts.forEach((callback) => callback());
  await tick();
  assert.equal(h.popup.closed, false);
  h.popup.document.images.forEach((image) => image.emit('load'));
  expectStatus(await retry, 'prepared');
  assert.equal(h.calls.filter(([name]) => name === 'print').length, 1);
  assert.equal(h.timers.size, 0);
});

test('failed retrieval aborts a sibling fetch without invoking fallback', async () => {
  let siblingSignal;
  const h = preparationHarness({ fetch: (url, { signal }) => {
    if (url === label.qrImageUrl) return Promise.resolve({ ok: false });
    siblingSignal = signal;
    return new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(signal.reason), { once: true }));
  } });
  expectStatus(await h.api.printQrLabels([label, { ...label, id: 18, qrImageUrl: '/boites/18/qr.svg' }]), 'failed', 'qr-retrieval');
  await tick();
  assert.equal(siblingSignal.aborted, true);
  assert.equal(h.resources.length, 0, 'Attempt abort must not become image fallback');
  assert.equal(h.timers.size, 0);
  assert.equal(h.popup.closed, true);
  assert.equal(h.calls.some(([name]) => name === 'print'), false);
});

test('failed fallback image aborts sibling fetch and clears its resource listeners', async () => {
  let siblingSignal;
  const h = preparationHarness({ resourcePending: true, fetch: (url, { signal }) => {
    if (url === label.qrImageUrl) return Promise.reject(new Error('network'));
    siblingSignal = signal;
    return new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(signal.reason), { once: true }));
  } });
  const pending = h.api.printQrLabels([label, { ...label, id: 18, qrImageUrl: '/boites/18/qr.svg' }]);
  await tick();
  assert.equal(h.resources.length, 1);
  h.resources[0].emit('error');
  expectStatus(await pending, 'failed', 'fallback-resource');
  await tick();
  assert.equal(siblingSignal.aborted, true);
  assert.equal(h.resources.length, 1, 'Aborted sibling must not create another fallback');
  assert.equal(h.resources[0].listeners.size, 0);
  assert.equal(h.timers.size, 0);
  assert.equal(h.popup.closed, true);
  assert.equal(h.calls.some(([name]) => name === 'print'), false);
});

test('failed retrieval disposes a pending sibling fallback image', async () => {
  let failFirst;
  const h = preparationHarness({ resourcePending: true, fetch: (url) => {
    if (url === label.qrImageUrl) return new Promise((resolve) => { failFirst = resolve; });
    return Promise.reject(new Error('network'));
  } });
  const pending = h.api.printQrLabels([label, { ...label, id: 18, qrImageUrl: '/boites/18/qr.svg' }]);
  await tick();
  assert.equal(h.resources[0].listeners.size, 2);
  failFirst({ ok: false });
  expectStatus(await pending, 'failed', 'qr-retrieval');
  assert.equal(h.resources[0].listeners.size, 0);
  assert.equal(h.resources[0].src, '');
  assert.equal(h.timers.size, 0);
  assert.equal(h.popup.closed, true);
  assert.equal(h.calls.some(([name]) => name === 'print'), false);
});

test('popup close aborts pending fetch promptly even if transport ignores abort', async () => {
  let resolveFetch, signal;
  const h = preparationHarness({ fetch: (_url, config) => {
    signal = config.signal;
    return new Promise((resolve) => { resolveFetch = resolve; });
  } });
  const pending = h.api.printQrLabels([label]);
  await tick();
  h.popup.closed = true;
  h.runTimers(100);
  expectStatus(await pending, 'cancelled');
  assert.equal(signal.aborted, true);
  assert.equal(h.timers.size, 0);
  resolveFetch({ ok: true, text: async () => '<svg/>' });
  await tick();
  assert.equal(h.resources.length, 0);
  assert.equal(h.calls.some(([name]) => name === 'write' || name === 'print'), false);
});

test('download validates its image and preserves self-contained vector geometry and identity', async () => {
  const bad = preparationHarness({ resourceValid: false });
  expectStatus(await bad.api.downloadQrLabel(label), 'failed', 'image-preparation');
  assert.equal(bad.calls.some(([name]) => name === 'download'), false);
  const h = preparationHarness();
  expectStatus(await h.api.downloadQrLabel(label), 'prepared');
  const svg = await h.calls.find(([name]) => name === 'blob')[1].text();
  assert.match(svg, /width="41mm" height="28mm"/);
  assert.match(svg, /<image href="data:image\/svg\+xml;base64,[^"]+"[^>]*width="25" height="25"/);
  assert.match(svg, /ATL-AAU-1.001/);
  assert.match(svg, /Aurelia aurita/);
  assert.doesNotMatch(svg, /<canvas|image\/png/);
});

test('unexpected preparation exceptions settle and clean up resources', async () => {
  const popup = preparationHarness({ openThrows: true });
  expectStatus(await popup.api.printQrLabels([label]), 'failed', 'preparation');
  const download = preparationHarness({ downloadThrows: true });
  expectStatus(await download.api.downloadQrLabel(label), 'failed', 'preparation');
  assert.ok(download.calls.some(([name]) => name === 'remove'));
  assert.ok(download.calls.some(([name]) => name === 'revoke'));
});

test('every failure maps to a translated, factual preparation message', () => {
  const { api } = preparationHarness();
  for (const [reason, key] of Object.entries({ 'popup-blocked': 'qrLabelPopupBlocked', 'qr-retrieval': 'qrLabelQrUnavailable', 'fallback-resource': 'qrLabelResourceUnavailable', 'image-preparation': 'qrLabelImagePreparationFailed', preparation: 'qrLabelPreparationFailed' })) {
    assert.equal(api.getQrLabelPreparationMessage(reason, feedback), feedback[key]);
  }
});

// Execute component handlers and effects without a DOM dependency. Browser and
// screen-reader rendering are separate checks; these tests exercise real hooks' code.
function componentHarness(file, api) {
  const code = ts.transpileModule(readFileSync(new URL(`../src/components/${file}.tsx`, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const slots = [], effects = [], cleanups = [];
  let index = 0, tree, controls = [];
  const document = { activeElement: null };
  const listeners = new Map();
  const opener = { isConnected: true, focus() { document.activeElement = this; } };
  opener.focus();
  const dialog = { focus() { document.activeElement = this; }, contains(node) { return node === this || controls.includes(node); }, querySelectorAll() { return controls.filter((node) => !node.props.disabled); } };
  const hooks = {
    useState(initial) { const i = index++; if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial; return [slots[i], (value) => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }]; },
    useRef(initial) { const i = index++; return slots[i] ??= { current: initial }; },
    useId() { const i = index++; return `qr-test-${i}`; },
    useMemo(fn) { return fn(); },
    useEffect: effect, useLayoutEffect: effect,
  };
  function effect(fn, deps) {
    const i = index++;
    if (!slots[i] || deps.some((dep, j) => dep !== slots[i][j])) { slots[i] = deps; effects.push(() => { cleanups[i]?.(); cleanups[i] = fn(); }); }
  }
  const jsx = (type, props) => ({ type, props });
  const modules = {
    react: hooks, 'react/jsx-runtime': { jsx, jsxs: jsx },
    '../utils/qrLabels': api, './ModalPortal': () => null, './PolypbaseIcon': () => null, './QrLabel': () => null,
    'lucide-react': { ChevronDown() {}, ChevronRight() {}, Printer() {} }, './BoxTrackingPreview': () => null, './PageLoader': () => null,
  };
  const exports = {};
  vm.runInNewContext(code, { exports, document, HTMLElement: Object, window: { addEventListener(name, fn) { listeners.set(name, fn); }, removeEventListener(name) { listeners.delete(name); } }, require: (name) => { assert.ok(name in modules, name); return modules[name]; } });
  function nodes(node = tree) {
    if (!node || typeof node !== 'object') return [];
    return [node, ...[node.props?.children].flat(2).filter(Boolean).flatMap((child) => nodes(child))];
  }
  function render(props) {
    index = 0;
    tree = exports.default(props);
    controls = nodes().filter((node) => ['button', 'input', 'select'].includes(node.type));
    for (const node of nodes()) {
      if (node.props?.role === 'dialog') node.props.ref.current = dialog;
      if (controls.includes(node)) { node.focus = () => { document.activeElement = node; }; if (node.props.ref) node.props.ref.current = node; }
    }
    while (effects.length) effects.shift()();
    return tree;
  }
  function key(key, shiftKey = false) { let prevented = false; listeners.get('keydown')?.({ key, shiftKey, preventDefault() { prevented = true; } }); return prevented; }
  return { render, nodes, key, document, opener, dialog, unmount() { cleanups.forEach((cleanup) => cleanup?.()); }, listeners };
}

function modalProps() {
  return {
    box: { id: 17, global_code: label.globalCode, species: { scientific_name: label.speciesName } }, qrImageUrl: label.qrImageUrl,
    labels: { ...feedback, title: 'Label', help: 'Preview', close: 'Close', download: 'Download', print: 'Print', qrCode: 'QR code', selectionCount: 'Selected', addToSelection: 'Add', alreadySelected: 'Already selected', viewSelection: 'View selection' },
    selectedLabels: [], onAddToSelection() {}, onClose() {}, onViewSelection() {},
  };
}
const componentApi = { ...preparationHarness().api, buildQrLabelItem: () => label };
const button = (h, text) => h.nodes().find((node) => node.type === 'button' && (node.props.children === text || JSON.stringify(node.props.children)?.includes(`"${text}"`)));

test('modal is labeled, focuses close, contains forward/reverse Tab and restores connected opener', () => {
  const h = componentHarness('QrLabelModal', componentApi);
  const props = modalProps(); let closed = 0; props.onClose = () => closed++;
  h.render(props);
  const dialogNode = h.nodes().find((node) => node.props?.role === 'dialog');
  assert.equal(dialogNode.props['aria-modal'], 'true');
  assert.ok(h.nodes().some((node) => node.type === 'h2' && node.props.id === dialogNode.props['aria-labelledby']));
  assert.ok(h.nodes().some((node) => node.props?.id === dialogNode.props['aria-describedby']));
  assert.equal(h.document.activeElement, button(h, 'Close') ?? h.nodes().find((node) => node.props?.['aria-label'] === 'Close'));
  const first = h.document.activeElement, last = button(h, 'Print');
  assert.equal(h.key('Tab', true), true); assert.equal(h.document.activeElement, last);
  assert.equal(h.key('Tab'), true); assert.equal(h.document.activeElement, first);
  h.opener.focus(); h.key('Tab'); assert.equal(h.document.activeElement, first);
  h.key('Escape'); assert.equal(closed, 1);
  h.unmount(); assert.equal(h.document.activeElement, h.opener); assert.equal(h.listeners.size, 0);
});

test('modal does not restore focus to a disconnected trigger', () => {
  const h = componentHarness('QrLabelModal', componentApi);
  h.render(modalProps()); h.opener.isConnected = false; h.unmount();
  assert.notEqual(h.document.activeElement, h.opener);
});

test('modal loading prevents duplicate actions, dismissal and navigation; retry retains action and context', async () => {
  for (const action of ['Print', 'Download']) {
    let resolve, calls = 0, otherCalls = 0, closed = 0;
    const pending = () => { calls++; return new Promise((done) => { resolve = done; }); };
    const api = { ...componentApi, printQrLabels: action === 'Print' ? pending : () => otherCalls++, downloadQrLabel: action === 'Download' ? pending : () => otherCalls++ };
    const h = componentHarness('QrLabelModal', api), props = modalProps(); props.onClose = () => closed++;
    h.render(props);
    const click = button(h, action).props.onClick;
    click(); click(); assert.equal(calls, 1);
    h.render(props);
    assert.equal(h.document.activeElement, h.dialog);
    assert.ok(h.nodes().filter((node) => node.type === 'button').every((node) => node.props.disabled));
    assert.ok(h.nodes().some((node) => node.props?.role === 'status' && node.props.children === feedback.qrLabelPreparing));
    h.key('Escape'); h.nodes().find((node) => node.props?.className === 'modal-backdrop qr-print-backdrop').props.onClick();
    assert.equal(closed, 0); assert.equal(h.key('Tab'), true);
    resolve({ status: 'failed', reason: 'image-preparation' }); await tick(); h.render(props);
    assert.ok(h.nodes().some((node) => node.props?.role === 'alert' && node.props.children === feedback.qrLabelImagePreparationFailed));
    button(h, 'Retry').props.onClick(); assert.equal(calls, 2); assert.equal(otherCalls, 0);
    resolve({ status: 'prepared' }); await tick(); h.render(props);
    assert.equal(h.nodes().some((node) => node.props?.role === 'alert'), false);
    assert.equal(props.selectedLabels.length, 0); assert.equal(props.box.id, 17);
    h.unmount();
  }
});

test('modal popup cancellation releases preparing state without failure or retry feedback', async () => {
  const image = resourceImage({ complete: false });
  const resources = preparationHarness({ images: [image] });
  let pending;
  const h = componentHarness('QrLabelModal', {
    ...componentApi,
    printQrLabels: (items) => (pending = resources.api.printQrLabels(items)),
  });
  const props = modalProps();
  h.render(props);
  button(h, 'Print').props.onClick();
  await tick(); h.render(props);
  assert.equal(button(h, 'Print').props.disabled, true);
  resources.popup.closed = true;
  resources.runTimers(100);
  expectStatus(await pending, 'cancelled');
  await tick(); h.render(props);
  assert.equal(button(h, 'Print').props.disabled, false);
  assert.equal(h.nodes().some((node) => node.props?.role === 'alert'), false);
  assert.equal(button(h, 'Retry'), undefined);
  assert.equal(resources.timers.size, 0);
  image.emit('error');
  assert.equal(resources.calls.some(([name]) => name === 'print'), false);
  h.unmount();
});

test('selection availability remains independent of download and print', () => {
  const h = componentHarness('QrLabelModal', componentApi), props = modalProps();
  props.selectedLabels = [label]; h.render(props);
  assert.equal(button(h, 'Already selected').props.disabled, true);
  assert.equal(button(h, 'View selection').props.disabled, false);
  assert.equal(button(h, 'Print').props.disabled, false);
  assert.equal(button(h, 'Download').props.disabled, false);
  h.unmount();
});

test('Labels popup cancellation unlocks print and keeps selection without failure feedback', async () => {
  const image = resourceImage({ complete: false });
  const resources = preparationHarness({ images: [image] });
  let pending;
  const h = componentHarness('LabelsView', {
    ...componentApi,
    printQrLabels: (items) => (pending = resources.api.printQrLabels(items)),
  });
  const props = {
    boxes: [{ id: 17, global_code: label.globalCode, species: { id: 1, scientific_name: label.speciesName }, organization: { id: 1 }, thermal_zone: { id: 2, name: 'Zone 15' }, status: 'active', latest_measurement: { measured_on: '2099-01-01' }, strain: { code: 'AAU' } }],
    profile: { is_superuser: true }, qrLabelSelection: [label], isLoading: false,
    labels: { ...feedback, noZone: 'No zone', qrLabelPrintCount: () => 'Print', qrLabelAddResults: () => 'Add results', qrLabelAddResultsCompact: () => 'Add', qrLabelSpeciesCount: () => '1 box', qrLabelSpeciesSelected: () => '1 selected', qrLabelDeselectSpecies: () => 'Deselect', qrLabelSelectSpecies: () => 'Select' },
    onAddQrLabel() { assert.fail('selection changed'); }, onRemoveQrLabel() { assert.fail('selection changed'); }, onClearQrLabelSelection() { assert.fail('selection cleared'); },
  };
  h.render(props);
  button(h, 'Print').props.onClick();
  await tick(); h.render(props);
  assert.equal(button(h, 'Print').props.disabled, true);
  const staleTimeouts = [...resources.timers.values()];
  resources.popup.closed = true;
  resources.runTimers(100);
  expectStatus(await pending, 'cancelled');
  await tick(); h.render(props);
  assert.equal(button(h, 'Print').props.disabled, false);
  assert.equal(button(h, 'Retry'), undefined);
  assert.equal(h.nodes().some((node) => node.props?.role === 'alert'), false);
  assert.deepEqual(props.qrLabelSelection, [label]);

  resources.popup.closed = false;
  resources.popup.document.images = [resourceImage()];
  button(h, 'Print').props.onClick();
  staleTimeouts.forEach((callback) => callback());
  expectStatus(await pending, 'prepared');
  await tick(); h.render(props);
  assert.equal(button(h, 'Print').props.disabled, false);
  assert.equal(resources.calls.filter(([name]) => name === 'print').length, 1);
  assert.equal(resources.timers.size, 0);
  assert.deepEqual(props.qrLabelSelection, [label]);
});

test('Labels failure and retry preserve selection, search and zone state', async () => {
  let resolve, calls = 0;
  const h = componentHarness('LabelsView', { ...componentApi, printQrLabels: () => { calls++; return new Promise((done) => { resolve = done; }); } });
  const props = {
    boxes: [{ id: 17, global_code: label.globalCode, species: { id: 1, scientific_name: label.speciesName }, organization: { id: 1 }, thermal_zone: { id: 2, name: 'Zone 15' }, status: 'active', latest_measurement: { measured_on: '2099-01-01' }, strain: { code: 'AAU' } }],
    profile: { is_superuser: true }, qrLabelSelection: [label], isLoading: false,
    labels: { ...feedback, noZone: 'No zone', qrLabelPrintCount: () => 'Print', qrLabelAddResults: () => 'Add results', qrLabelAddResultsCompact: () => 'Add', qrLabelSpeciesCount: () => '1 box', qrLabelSpeciesSelected: () => '1 selected', qrLabelDeselectSpecies: () => 'Deselect', qrLabelSelectSpecies: () => 'Select' },
    onAddQrLabel() { assert.fail('selection changed'); }, onRemoveQrLabel() { assert.fail('selection changed'); }, onClearQrLabelSelection() { assert.fail('selection cleared'); },
  };
  h.render(props);
  h.nodes().find((node) => node.props?.type === 'search').props.onChange({ target: { value: 'ATL' } });
  h.nodes().find((node) => node.type === 'select').props.onChange({ target: { value: 'zone-2' } });
  h.render(props); const click = button(h, 'Print').props.onClick; click(); click(); assert.equal(calls, 1);
  h.render(props); assert.equal(button(h, 'Print').props.disabled, true);
  resolve({ status: 'failed', reason: 'popup-blocked' }); await tick(); h.render(props);
  assert.ok(h.nodes().some((node) => node.props?.role === 'alert' && node.props.children === feedback.qrLabelPopupBlocked));
  assert.equal(h.nodes().find((node) => node.props?.type === 'search').props.value, 'ATL');
  assert.equal(h.nodes().find((node) => node.type === 'select').props.value, 'zone-2');
  button(h, 'Retry').props.onClick(); assert.equal(calls, 2);
  resolve({ status: 'prepared' }); await tick(); h.render(props);
  assert.equal(h.nodes().some((node) => node.props?.role === 'alert'), false);
  assert.deepEqual(props.qrLabelSelection, [label]);
});

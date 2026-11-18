import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function load(path, modules = {}, globals = {}) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  vm.runInNewContext(outputText, { exports, ...globals, require(id) {
    assert.ok(Object.hasOwn(modules, id), `Unexpected dependency: ${id}`);
    return modules[id];
  } });
  return exports;
}
const placementModule = load('../src/utils/anchoredPopoverPlacement.ts');
const place = placementModule.getAnchoredPopoverPlacement;
const viewport = { width: 1280, height: 800 };
const anchorAt = (top, left = 200, height = 24) => ({ top, bottom: top + height, left, right: left + 100 });

function assertSeparate(anchor, panel, position, viewport) {
  const bounds = {
    left: position.left, right: position.left + panel.width,
    top: position.top, bottom: position.top + Math.min(panel.height, position.maxHeight),
  };
  assert.ok(bounds.bottom <= anchor.top - 6 || bounds.top >= anchor.bottom + 6,
    `Panel ${JSON.stringify(bounds)} overlaps anchor ${JSON.stringify(anchor)}`);
  assert.ok(position.maxHeight >= 0);
  assert.ok(bounds.top >= 12);
  assert.ok(bounds.bottom <= viewport.height - 12);
  assert.ok(bounds.left >= 12);
  assert.ok(bounds.right <= viewport.width - 12);
  return bounds;
}

for (const [name, top, height, side, expectedMax] of [
  ['small menu prefers below when both sides fit', 350, 120, 'below', 408],
  ['panel fits above but not below', 600, 300, 'above', 582],
  ['oversized chart uses the larger lower side', 250, 1200, 'below', 508],
  ['oversized chart uses the larger upper side', 500, 1200, 'above', 482],
  ['equal available space prefers below', 388, 1200, 'below', 370],
  ['anchor at the top edge', 0, 1200, 'below', 758],
  ['anchor at the bottom edge', 776, 1200, 'above', 758],
  ['partially visible top anchor', -12, 1200, 'below', 770],
  ['partially visible bottom anchor', 788, 1200, 'above', 770],
]) {
  test(name, () => {
    const anchor = anchorAt(top);
    const panel = { width: 720, height };
    const position = place(anchor, panel, viewport);
    const bounds = assertSeparate(anchor, panel, position, viewport);
    assert.equal(position.maxHeight, expectedMax);
    if (side === 'above') assert.equal(bounds.bottom, anchor.top - 6);
    else assert.equal(bounds.top, anchor.bottom + 6);
  });
}

test('horizontal start/end menu alignment and viewport edge clamping are preserved', () => {
  const panel = { width: 192, height: 120 };
  assert.equal(place(anchorAt(350, 600), panel, viewport, 'start').left, 600);
  assert.equal(place(anchorAt(350, 600), panel, viewport, 'end').left, 508);
  for (const align of ['start', 'end']) {
    assert.equal(place(anchorAt(350, 0), panel, viewport, align).left, 12);
    assert.equal(place(anchorAt(350, 1250), panel, viewport, align).left, 1076);
  }
});

test('bounding rectangles never overlap across viewport sizes, edges and chart growth', () => {
  for (const viewport of [{ width: 1280, height: 800 }, { width: 1440, height: 1000 },
    { width: 960, height: 600 }, { width: 320, height: 200 }]) {
    for (const top of [0, 12, viewport.height / 3, viewport.height / 2, viewport.height - 24]) {
      for (const height of [40, 220, 600, 1600]) {
        for (const align of ['start', 'end']) {
          const anchor = anchorAt(top, viewport.width - 100);
          const panel = { width: Math.min(720, viewport.width - 24), height };
          assertSeparate(anchor, panel, place(anchor, panel, viewport, align), viewport);
        }
      }
    }
  }
});

// Execute the real hook with controlled DOM measurements and explicit observer delivery.
function hookHarness({ top = 500, height = 220, align = 'start' } = {}) {
  // Node's EventTarget needs object options to remove captured listeners reliably.
  class BrowserEvents extends EventTarget {
    addEventListener(type, listener, options) {
      super.addEventListener(type, listener, typeof options === 'boolean' ? { capture: options } : options);
    }
    removeEventListener(type, listener, options) {
      super.removeEventListener(type, listener, typeof options === 'boolean' ? { capture: options } : options);
    }
  }
  const document = new BrowserEvents();
  const window = Object.assign(new BrowserEvents(), { innerWidth: 1280, innerHeight: 800 });
  class Node {}
  class CustomEvent extends Event {
    constructor(type, { detail }) { super(type); this.detail = detail; }
  }
  let anchorRect = anchorAt(top);
  let contentHeight = height;
  let position = { visibility: 'hidden' };
  let setup, cleanup, observer, updates = 0;
  const closed = [];
  const insideAnchor = new Node(), insidePanel = new Node();
  const anchor = {
    getBoundingClientRect: () => anchorRect,
    contains: target => target === insideAnchor,
  };
  const renderedHeight = () => Math.min(contentHeight, position.maxHeight ?? 776);
  const panel = {
    getBoundingClientRect: () => ({ width: 720, height: renderedHeight() }),
    get scrollHeight() { return contentHeight - 2; },
    get offsetHeight() { return renderedHeight(); },
    get clientHeight() { return renderedHeight() - 2; },
    contains: target => target === insidePanel,
  };
  const refs = [{ current: anchor }, { current: panel }];
  let refIndex = 0;
  const hooks = {
    useId: () => 'test-popover',
    useRef: () => refs[refIndex++],
    useState: () => [position, next => {
      const value = typeof next === 'function' ? next(position) : next;
      if (value !== position) updates++;
      position = value;
    }],
    useLayoutEffect: effect => { setup = effect; },
  };
  class ResizeObserver {
    constructor(callback) { this.callback = callback; observer = this; }
    observe(node) { assert.equal(node, panel); }
    disconnect() { this.disconnected = true; }
  }
  const { useAnchoredPopover } = load('../src/hooks/useAnchoredPopover.ts', {
    react: hooks, '../utils/anchoredPopoverPlacement': placementModule,
  }, { document, window, Node, CustomEvent, ResizeObserver });
  useAnchoredPopover(true, restoreFocus => closed.push(restoreFocus), align);
  cleanup = setup();
  return {
    document, window, closed, insideAnchor, insidePanel, observer,
    get position() { return position; },
    get updates() { return updates; },
    resizePanel(height) { contentHeight = height; observer.callback(); },
    moveAnchor(top) { anchorRect = anchorAt(top); window.dispatchEvent(new Event('scroll')); },
    resizeViewport(height) { window.innerHeight = height; window.dispatchEvent(new Event('resize')); },
    assertSeparate() {
      return assertSeparate(anchorRect, { width: 720, height: contentHeight }, position,
        { width: window.innerWidth, height: window.innerHeight });
    },
    dispatch(type, properties) {
      const event = new Event(type, { cancelable: true });
      for (const [key, value] of Object.entries(properties)) Object.defineProperty(event, key, { value });
      document.dispatchEvent(event);
      return event;
    },
    cleanup: () => cleanup(),
  };
}

test('loading preview grows above without covering its link or oscillating after constraint', () => {
  const h = hookHarness();
  assert.equal(h.position.top, 530, 'loading state fits below');
  h.assertSeparate();
  h.resizePanel(1200);
  assert.equal(h.position.top, 12);
  assert.equal(h.position.maxHeight, 482);
  h.assertSeparate();
  const settled = h.position, updates = h.updates;
  for (let i = 0; i < 10; i++) h.observer.callback();
  assert.equal(h.position, settled, 'observer measurements retain the same placement object');
  assert.equal(h.updates, updates, 'no redundant state updates or size feedback loop');
  h.resizePanel(180);
  assert.equal(h.position.top, 530, 'shrinking content may fit below again');
  h.assertSeparate();
  h.cleanup();
});

test('chart growth on the lower side stays below and uses its own scroll area', () => {
  const h = hookHarness({ top: 250 });
  h.resizePanel(1600);
  assert.equal(h.position.top, 280);
  assert.equal(h.position.maxHeight, 508);
  h.assertSeparate();
  for (let i = 0; i < 10; i++) h.observer.callback();
  assert.equal(h.position.top, 280);
  h.assertSeparate();
  h.cleanup();
});

test('viewport resize and captured scroll recalculate room; offscreen anchors dismiss', () => {
  const h = hookHarness({ height: 1200 });
  h.resizeViewport(1000);
  assert.equal(h.position.maxHeight, 482);
  h.assertSeparate();
  h.moveAnchor(100);
  assert.equal(h.position.top, 130);
  assert.equal(h.position.maxHeight, 858);
  h.assertSeparate();
  h.resizeViewport(600);
  assert.equal(h.position.maxHeight, 458);
  h.assertSeparate();
  h.moveAnchor(-30);
  assert.deepEqual(h.closed, [undefined]);
  h.moveAnchor(601);
  assert.deepEqual(h.closed, [undefined, undefined]);
  h.cleanup();
});

test('outside click, Escape focus request, other-popover dismissal and cleanup are preserved', () => {
  const h = hookHarness({ align: 'end' });
  h.dispatch('pointerdown', { target: h.insideAnchor });
  h.dispatch('pointerdown', { target: h.insidePanel });
  h.dispatch('keydown', { key: 'ArrowDown' });
  h.dispatch('polypbase:popover-open', { detail: 'test-popover' });
  assert.deepEqual(h.closed, []);
  h.dispatch('pointerdown', { target: new (class {})() });
  assert.deepEqual(h.closed, [], 'non-Node targets are ignored');
  h.dispatch('pointerdown', { target: Object.create(Object.getPrototypeOf(h.insideAnchor)) });
  assert.deepEqual(h.closed, [undefined]);
  assert.equal(h.dispatch('keydown', { key: 'Escape' }).defaultPrevented, true);
  h.dispatch('polypbase:popover-open', { detail: 'another-popover' });
  assert.deepEqual(h.closed, [undefined, true, undefined]);
  h.cleanup();
  assert.equal(h.observer.disconnected, true);
  const position = h.position;
  h.resizeViewport(600);
  h.moveAnchor(100);
  h.dispatch('keydown', { key: 'Escape' });
  h.dispatch('polypbase:popover-open', { detail: 'another-popover' });
  assert.equal(h.position, position);
  assert.deepEqual(h.closed, [undefined, true, undefined]);
});

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/hooks/useTemperatureChartActivation.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});

function harness({ intersectionObserver = true, aborted = false, mountRef = true } = {}) {
  const slots = [];
  const observers = [];
  const element = { synthetic: 'chart-root' };
  let organization = new AbortController();
  if (aborted) organization.abort();
  let boxId = 101;
  let cursor = 0;
  let pending = [];
  let dirty = false;
  let mounted = true;
  let result;
  const publications = [];
  const same = (a, b) => a && b && a.length === b.length && a.every((value, index) => Object.is(value, b[index]));
  const react = {
    useMemo(factory, deps) {
      const index = cursor++;
      if (!same(slots[index]?.deps, deps)) slots[index] = { deps, value: factory() };
      return slots[index].value;
    },
    useRef(initial) {
      const index = cursor++;
      slots[index] ??= { value: { current: initial } };
      return slots[index].value;
    },
    useState(initial) {
      const index = cursor++;
      slots[index] ??= { value: initial };
      return [slots[index].value, next => {
        publications.push(next);
        if (!Object.is(slots[index].value, next)) { slots[index].value = next; dirty = true; }
      }];
    },
    useEffect(effect, deps) {
      const index = cursor++;
      if (!same(slots[index]?.deps, deps)) pending.push({ index, effect, deps });
    },
  };
  class FakeIntersectionObserver {
    constructor(callback, options) {
      this.callback = callback;
      this.options = options;
      this.targets = [];
      this.disconnected = false;
      observers.push(this);
    }
    observe(target) { this.targets.push(target); }
    disconnect() { this.disconnected = true; }
    // Deliberately deliver even after disconnect to exercise queued callback guards.
    emit(isIntersecting) { this.callback([{ target: element, isIntersecting }]); }
  }
  const exports = {};
  vm.runInNewContext(outputText, {
    exports,
    IntersectionObserver: intersectionObserver ? FakeIntersectionObserver : undefined,
    require(name) {
      if (name === 'react') return react;
      if (name === '../api/client') return { getOrganizationResourceSignal: () => organization.signal };
      assert.fail(`Unexpected activation dependency: ${name}`);
    },
  });
  function render() {
    if (!mounted) return;
    let iterations = 0;
    do {
      assert.ok(++iterations < 20, 'Activation effects must settle');
      cursor = 0; pending = []; dirty = false;
      result = exports.useTemperatureChartActivation(boxId);
      if (mountRef) result.ref.current = element;
      for (const { index, effect, deps } of pending) {
        slots[index]?.cleanup?.();
        slots[index] = { deps, cleanup: effect() };
      }
    } while (dirty);
  }
  render();
  return {
    observers, element, publications, render,
    get active() { return result.active; },
    get ref() { return result.ref; },
    intersect(value = true, observer = observers.at(-1)) { observer.emit(value); render(); },
    changeBox(id) { boxId = id; render(); },
    changeOrganization() { organization.abort(); organization = new AbortController(); render(); },
    abort() { organization.abort(); render(); },
    unmount() {
      if (!mounted) return;
      mounted = false;
      slots.forEach(slot => slot?.cleanup?.());
      result.ref.current = null;
    },
  };
}

test('activation waits for actual viewport intersection and stays active after exit', () => {
  const view = harness();
  try {
    assert.equal(view.active, false);
    assert.equal(view.observers.length, 1);
    const observer = view.observers[0];
    assert.deepEqual(observer.targets, [view.element]);
    assert.equal(observer.options.root ?? null, null, 'Use the actual viewport, not a scroll ancestor');
    assert.equal(observer.options.rootMargin, '0px');
    assert.equal(observer.options.threshold, 0);
    view.intersect(false);
    assert.equal(view.active, false);
    view.intersect(true);
    assert.equal(view.active, true);
    assert.equal(observer.disconnected, true);
    const count = view.publications.length;
    view.intersect(false, observer);
    view.intersect(true, observer);
    view.render();
    assert.equal(view.active, true);
    assert.equal(view.publications.length, count, 'Disconnected queued callbacks cannot activate again');
    assert.equal(view.observers.length, 1, 'No observer is recreated after activation');
  } finally { view.unmount(); }
});

test('Box and organization replacements reset activation and reject old callbacks', () => {
  const view = harness();
  try {
    const ref = view.ref;
    view.intersect();
    const first = view.observers[0];
    view.changeBox(102);
    assert.equal(view.active, false);
    assert.equal(view.ref, ref);
    view.intersect(true, first);
    assert.equal(view.active, false);
    view.intersect();
    assert.equal(view.active, true);
    const second = view.observers.at(-1);
    view.changeOrganization();
    assert.equal(view.active, false);
    assert.equal(second.disconnected, true);
    view.intersect(true, second);
    assert.equal(view.active, false);
    view.intersect();
    assert.equal(view.active, true);
    view.changeBox(101);
    assert.equal(view.active, false, 'Returning to a previous Box still requires a fresh intersection');
  } finally { view.unmount(); }
});

test('organization abort and unmount disconnect observers and guard queued callbacks', () => {
  for (const action of ['abort', 'unmount']) {
    const view = harness();
    const observer = view.observers[0];
    view[action]();
    assert.equal(observer.disconnected, true);
    const count = view.publications.length;
    observer.emit(true);
    assert.equal(view.publications.length, count);
    view.unmount();
  }
  const active = harness();
  active.intersect();
  active.abort();
  assert.equal(active.active, false, 'Aborted organization hides even a previously active chart');
  active.unmount();
});

test('missing IntersectionObserver activates mounted charts but never an aborted context', () => {
  const view = harness({ intersectionObserver: false });
  try {
    assert.equal(view.active, true);
    assert.equal(view.observers.length, 0);
    view.changeBox(102);
    assert.equal(view.active, true);
    view.changeOrganization();
    assert.equal(view.active, true);
  } finally { view.unmount(); }
  for (const options of [{ aborted: true }, { intersectionObserver: false, aborted: true }, { mountRef: false }]) {
    const inactive = harness(options);
    assert.equal(inactive.active, false);
    assert.equal(inactive.observers.length, 0);
    inactive.unmount();
  }
});

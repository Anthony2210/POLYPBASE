import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

export const readComponent = name => readFileSync(new URL(`../src/components/${name}.tsx`, import.meta.url), 'utf8');

// Execute the complete module, including its imports and default export.
export function loadComponent(name, modules, globals = {}) {
  const { outputText } = ts.transpileModule(readComponent(name), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
  const context = vm.createContext({ ...globals, exports: {}, require(id) {
    assert.ok(Object.hasOwn(modules, id), `Unexpected component dependency: ${id}`);
    return modules[id];
  } });
  vm.runInContext(outputText, context, { filename: `${name}.tsx` });
  assert.equal(typeof context.exports.default, 'function', `${name} must export its component as default`);
  return context.exports.default;
}

export function fakeClock() {
  let now = 0, nextId = 1;
  const timers = new Map();
  function schedule(callback, delay, interval) {
    assert.ok(delay > 0);
    const id = nextId++;
    timers.set(id, { callback, due: now + delay, interval });
    return id;
  }
  const window = {
    setTimeout: (callback, delay) => schedule(callback, delay, null),
    setInterval: (callback, delay) => schedule(callback, delay, delay),
    clearTimeout: id => timers.delete(id),
    clearInterval: id => timers.delete(id),
  };
  return {
    window,
    get now() { return now; },
    get pending() { return timers.size; },
    advance(milliseconds) {
      assert.ok(milliseconds >= 0);
      const end = now + milliseconds;
      while (true) {
        const next = [...timers.entries()].filter(([, timer]) => timer.due <= end)
          .sort((a, b) => a[1].due - b[1].due || a[0] - b[0])[0];
        if (!next) break;
        const [id, timer] = next;
        now = timer.due;
        if (timer.interval === null) timers.delete(id);
        else timer.due += timer.interval;
        timer.callback();
      }
      now = end;
    },
  };
}

// A deliberately small hook renderer: slots belong to instances, updates apply
// at the next explicit render, and passive effects run only after rendering.
export function stepperHarness({ strictEffects = false } = {}) {
  const clock = fakeClock();
  const controls = new Map();
  let instance, index, focused;
  const runtime = { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) };
  const hooks = {
    useRef(value) {
      const slot = index++;
      return instance.slots[slot] ??= { current: value };
    },
    useState(value) {
      const owner = instance, slot = index++;
      if (!(slot in owner.slots)) owner.slots[slot] = typeof value === 'function' ? value() : value;
      return [owner.slots[slot], next => {
        if (!owner.unmounted) owner.slots[slot] = typeof next === 'function' ? next(owner.slots[slot]) : next;
      }];
    },
    useEffect(setup, dependencies) {
      const slot = index++;
      const previous = instance.effects.get(slot);
      const changed = !previous || !dependencies || !previous.dependencies
        || dependencies.length !== previous.dependencies.length
        || dependencies.some((value, i) => !Object.is(value, previous.dependencies[i]));
      if (changed) instance.pendingEffects.set(slot, { setup, dependencies, cleanup: previous?.cleanup });
    },
  };
  const Component = loadComponent('StepperButton', { react: hooks, 'react/jsx-runtime': runtime }, { window: clock.window });
  function flushEffects(name) {
    const owner = controls.get(name);
    assert.ok(owner && !owner.unmounted);
    for (const [slot, effect] of owner.pendingEffects) {
      effect.cleanup?.();
      effect.cleanup = effect.setup();
      owner.effects.set(slot, effect);
      owner.effectSetups++;
    }
    owner.pendingEffects.clear();
    if (strictEffects && !owner.committed) {
      for (const effect of owner.effects.values()) {
        effect.cleanup?.();
        owner.effectCleanups++;
        effect.cleanup = effect.setup();
        owner.effectSetups++;
      }
    }
    owner.committed = true;
  }
  function render(name, onStep, { flush = true, children = name } = {}) {
    instance = controls.get(name);
    if (!instance) {
      instance = { slots: [], effects: new Map(), pendingEffects: new Map(), effectSetups: 0, effectCleanups: 0 };
      controls.set(name, instance);
    }
    assert.ok(!instance.unmounted, 'Cannot render an unmounted instance');
    index = 0;
    instance.node = Component({ 'aria-label': name, onStep, children });
    instance = undefined;
    if (flush) flushEffects(name);
    return controls.get(name).node.props;
  }
  function pointer(name, onStep) {
    render(name, onStep).onPointerDown({ preventDefault() {}, currentTarget: { focus(options) {
      assert.equal(options.preventScroll, true);
      if (focused && focused !== name) controls.get(focused)?.node.props.onBlur();
      focused = name;
    } } });
    return render(name, onStep);
  }
  function unmount(name) {
    const owner = controls.get(name);
    assert.ok(owner && !owner.unmounted);
    owner.unmounted = true;
    for (const effect of owner.effects.values()) {
      effect.cleanup?.();
      owner.effectCleanups++;
    }
    owner.pendingEffects.clear();
    if (focused === name) focused = undefined;
  }
  return { render, pointer, unmount, flushEffects, clock, controls,
    focus: name => { focused = name; }, getFocused: () => focused };
}

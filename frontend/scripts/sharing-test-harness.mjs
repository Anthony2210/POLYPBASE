import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const read = file => readFileSync(path.join(root, file), 'utf8');
export const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
export const tick = () => new Promise(resolve => setImmediate(resolve));

export function moduleLoader(overrides = {}, globals = {}) {
  const cache = new Map();
  function load(file) {
    const full = path.join(root, file);
    if (Object.hasOwn(overrides, file)) return overrides[file];
    if (cache.has(full)) return cache.get(full);
    const exports = {};
    cache.set(full, exports);
    const code = ts.transpileModule(read(file), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
    vm.runInNewContext(code, {
      exports, Headers, URL, URLSearchParams, AbortController, Date, Intl, console,
      setTimeout, clearTimeout, ...globals,
      require(name) {
        if (!name.startsWith('.')) return Object.hasOwn(overrides, name) ? overrides[name] : require(name);
        const relative = path.relative(root, path.resolve(path.dirname(full), name)).replaceAll('\\', '/');
        for (const suffix of ['.ts', '.tsx', '/index.ts']) {
          const candidate = relative + suffix;
          if (Object.hasOwn(overrides, candidate)) return overrides[candidate];
          try { read(candidate); return load(candidate); } catch (error) { if (error.code !== 'ENOENT') throw error; }
        }
        throw new Error(`Unresolved sharing test dependency: ${relative}`);
      },
    }, { filename: file });
    return exports;
  }
  return load;
}

// Execute real hook/component logic with controllable effects and publication.
// JSX stays a React element tree; DOM semantics are covered separately in Chromium.
export function hookRuntime() {
  const slots = [];
  let cursor = 0;
  let effects = [];
  let queued = [];
  let deferUpdates = false;
  const same = (a, b) => a && b && a.length === b.length && a.every((value, index) => Object.is(value, b[index]));
  const hooks = {
    useState(initial) {
      const index = cursor++;
      slots[index] ??= { value: typeof initial === 'function' ? initial() : initial };
      return [slots[index].value, value => {
        const apply = () => { slots[index].value = typeof value === 'function' ? value(slots[index].value) : value; };
        if (deferUpdates) queued.push(apply); else apply();
      }];
    },
    useRef(initial) { const index = cursor++; slots[index] ??= { current: initial }; return slots[index]; },
    useMemo(factory, dependencies) {
      const index = cursor++;
      if (!slots[index] || !same(slots[index].dependencies, dependencies)) slots[index] = { value: factory(), dependencies };
      return slots[index].value;
    },
    useCallback(callback, dependencies) { return hooks.useMemo(() => callback, dependencies); },
    useEffect(effect, dependencies) {
      const index = cursor++;
      if (!slots[index] || !same(slots[index].dependencies, dependencies)) {
        effects.push(() => { slots[index]?.cleanup?.(); slots[index] = { dependencies, cleanup: effect() }; });
      }
    },
  };
  hooks.useLayoutEffect = hooks.useEffect;
  return {
    hooks,
    render(fn) { cursor = 0; return fn(); },
    commit() { const pending = effects; effects = []; for (const effect of pending) effect(); },
    unmount() { for (const slot of slots) slot?.cleanup?.(); },
    defer(value) { deferUpdates = value; },
    publish() { const pending = queued; queued = []; for (const update of pending) update(); },
  };
}
export function elements(tree, predicate) {
  const result = [];
  function visit(node) {
    if (Array.isArray(node)) { node.forEach(visit); return; }
    if (!node || typeof node !== 'object') return;
    if (predicate(node)) result.push(node);
    visit(node.props?.children);
  }
  visit(tree);
  return result;
}
export const one = (tree, predicate) => {
  const result = elements(tree, predicate);
  assert.equal(result.length, 1, 'one matching sharing element');
  return result[0];
};

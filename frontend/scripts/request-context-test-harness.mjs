import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

// Execute the actual component request/effect code without a DOM or new test dependencies.
// Child components remain JSX nodes; this is not a browser or React renderer substitute.
export function createRequestContextHarness(componentName, initialProps) {
  const slots = [];
  const timers = new Map();
  const requests = [];
  const downloads = [];
  let cursor = 0;
  let dirty = true;
  let tree;
  let props = initialProps;
  let pendingEffects = [];
  let timerId = 0;
  let holdDeferred = false;
  const sameDeps = (first, second) => first && second
    && first.length === second.length && first.every((value, index) => Object.is(value, second[index]));

  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!slots[index]) {
        const slot = { value: typeof initial === 'function' ? initial() : initial };
        slot.set = (next) => {
          const value = typeof next === 'function' ? next(slot.value) : next;
          if (!Object.is(value, slot.value)) {
            slot.value = value;
            dirty = true;
          }
        };
        slots[index] = slot;
      }
      return [slots[index].value, slots[index].set];
    },
    useMemo(factory, deps) {
      const index = cursor++;
      if (!sameDeps(slots[index]?.deps, deps)) slots[index] = { value: factory(), deps };
      return slots[index].value;
    },
    useRef(initial) {
      const index = cursor++;
      slots[index] ??= { current: initial };
      return slots[index];
    },
    useDeferredValue(value) {
      const index = cursor++;
      slots[index] ??= { value };
      if (!holdDeferred) slots[index].value = value;
      return slots[index].value;
    },
    useEffect(effect, deps) {
      const index = cursor++;
      if (!sameDeps(slots[index]?.deps, deps)) pendingEffects.push({ index, effect, deps });
    },
  };
  hooks.useLayoutEffect = hooks.useEffect;

  function deferredRequest(url, config) {
    let resolve;
    let reject;
    const promise = new Promise((accept, decline) => { resolve = accept; reject = decline; });
    return { url, config, promise, resolve, reject };
  }
  class ApiError extends Error {}
  const api = {
    ApiError,
    apiGet(url, config) {
      const request = deferredRequest(url, config);
      requests.push(request);
      return request.promise;
    },
    apiDownload(url) {
      const request = deferredRequest(url);
      downloads.push(request);
      return request.promise;
    },
  };
  const modules = new Map();
  const window = {
    setTimeout(callback) { const id = ++timerId; timers.set(id, callback); return id; },
    clearTimeout(id) { timers.delete(id); },
    requestAnimationFrame() { return ++timerId; },
    cancelAnimationFrame() {},
    scrollTo() {},
    scrollY: 0,
  };
  const translations = {
    en: { loading: 'Loading...', reloadAction: 'Reload', close: 'Close' },
    fr: { loading: 'Chargement...', reloadAction: 'Recharger', close: 'Fermer' },
  };
  function load(url) {
    if (modules.has(url.href)) return modules.get(url.href);
    const source = readFileSync(url, 'utf8');
    const { outputText } = ts.transpileModule(source, {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
        jsx: ts.JsxEmit.ReactJSX,
      },
    });
    const exports = {};
    modules.set(url.href, exports);
    const require = (name) => {
      if (name === 'react') return hooks;
      if (name === 'react/jsx-runtime') {
        const jsx = (type, props) => ({ type, props });
        return { jsx, jsxs: jsx, Fragment: 'Fragment' };
      }
      if (name === '../api/client' || name === './client') return api;
      if (name === '../i18n') return { translations };
      if (name === '../boxStatus') {
        return { getBoxStatusPresentation: (status) => ({ tone: status, label: status }) };
      }
      if (name.startsWith('../utils/') || name === '../api/boxInventory') {
        return load(new URL(`${name}.ts`, url));
      }
      if (name.startsWith('./')) return { default: name.slice(2) };
      if (name === 'lucide-react') return new Proxy({}, { get: (_, name) => name });
      throw new Error(`Unexpected import: ${name}`);
    };
    vm.runInNewContext(outputText, {
      exports, require, window, URLSearchParams, AbortController, DOMException, Error,
      document: { documentElement: { lang: 'en' } },
    }, { filename: url.pathname });
    return exports;
  }
  const component = load(new URL(`../src/components/${componentName}.tsx`, import.meta.url)).default;

  function render(runEffects = true) {
    cursor = 0;
    dirty = false;
    pendingEffects = [];
    tree = component(props);
    if (runEffects) commit();
    return tree;
  }
  function commit() {
    const effects = pendingEffects;
    pendingEffects = [];
    for (const { index, effect, deps } of effects) {
      slots[index]?.cleanup?.();
      slots[index] = { deps, cleanup: effect() };
    }
  }
  function flush() {
    commit();
    let iterations = 0;
    while (dirty) {
      if (++iterations > 50) throw new Error('Component failed to settle');
      render();
    }
    return tree;
  }
  async function settle() {
    for (let index = 0; index < 12; index += 1) {
      await Promise.resolve();
      flush();
    }
    return tree;
  }
  function runTimers() {
    const callbacks = [...timers.values()];
    timers.clear();
    callbacks.forEach((callback) => callback());
  }
  return {
    requests, downloads, ApiError, render, commit, flush, settle, runTimers,
    get tree() { return tree; },
    setProps(next) { props = { ...props, ...next }; dirty = true; },
    holdDeferred(value) { holdDeferred = value; dirty = true; },
    unmount() { slots.forEach((slot) => slot?.cleanup?.()); },
  };
}

export function nodes(tree, predicate) {
  if (Array.isArray(tree)) return tree.flatMap((child) => nodes(child, predicate));
  if (!tree || typeof tree !== 'object') return [];
  return [...(predicate(tree) ? [tree] : []), ...nodes(tree.props?.children, predicate)];
}

export function text(tree) {
  if (Array.isArray(tree)) return tree.map(text).join(' ').replace(/\s+/g, ' ');
  if (tree == null || typeof tree === 'boolean') return '';
  if (typeof tree !== 'object') return String(tree);
  return text(tree.props?.children);
}

export function named(tree, name) {
  return nodes(tree, (node) => node.type === name || node.type?.name === name);
}

export function button(tree, label) {
  const match = nodes(tree, (node) => node.type === 'button'
    && (text(node).trim() === label || node.props['aria-label'] === label))[0];
  if (!match) throw new Error(`Button not found: ${label}`);
  return match;
}

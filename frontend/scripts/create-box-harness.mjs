import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

// Handler/effect regression harness, not a browser/layout implementation. Only
// transport and leaf UI boundaries are replaced; component and utilities are real.
const compiled = new Map();
export const FOCUSABLE_SELECTOR = 'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';
export const plain = value => JSON.parse(JSON.stringify(value));
export function deferred() {
  let resolve, reject;
  const promise = new Promise((accept, decline) => { resolve = accept; reject = decline; });
  return { promise, resolve, reject };
}
export async function tick() {
  for (let i = 0; i < 30; i++) await Promise.resolve();
}
export function text(node) {
  if (Array.isArray(node)) return node.map(text).join('');
  if (node == null || typeof node === 'boolean') return '';
  return typeof node === 'object' ? text(node.props?.children) : String(node);
}
export function submitEvent() {
  return { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
}
const sameDeps = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));

export function createBoxEnvironment() {
  const roots = [], listeners = new Map(), frames = new Map(), requests = [];
  let nextFrame = 0, current;
  const document = {
    activeElement: null,
    querySelectorAll(selector) {
      return roots.flatMap(root => [...(root.matches(selector) ? [root] : []), ...root.querySelectorAll(selector)]);
    },
    querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; },
  };
  class Element {
    constructor(type, props = {}) {
      Object.assign(this, { type, props, children: [], parent: null, isConnected: true, focusCalls: [] });
    }
    get disabled() { return Boolean(this.props.disabled); }
    focus(options) {
      this.focusCalls.push(options);
      if (this.isConnected && !this.disabled) document.activeElement = this;
    }
    contains(node) { return this === node || this.children.some(child => child.contains(node)); }
    matches(selector) {
      if (selector === FOCUSABLE_SELECTOR) return (!this.disabled && ['button', 'input', 'select', 'textarea'].includes(this.type))
        || (this.type === 'a' && Boolean(this.props.href)) || (this.props.tabIndex != null && this.props.tabIndex !== -1);
      if (selector.startsWith('.')) return (this.props.className ?? '').split(/\s+/).includes(selector.slice(1));
      if (selector === '[data-box-page-focus-target]') return 'data-box-page-focus-target' in this.props;
      throw new Error(`Unsupported DOM selector: ${selector}`);
    }
    querySelectorAll(selector) {
      return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; }
  }
  document.body = new Element('body');
  function external(type, props = {}) {
    const element = new Element(type, props);
    roots.push(element);
    return element;
  }
  const opener = external('button', { children: 'Open' });
  opener.focus();
  const session = new Map();
  const window = {
    crypto: { randomUUID: () => crypto.randomUUID() },
    sessionStorage: {
      getItem: key => session.get(key) ?? null,
      setItem: (key, value) => session.set(key, value),
      removeItem: key => session.delete(key),
    },
    location: { origin: 'https://polypbase.test' },
    addEventListener(name, fn) {
      if (!listeners.has(name)) listeners.set(name, new Set());
      listeners.get(name).add(fn);
    },
    removeEventListener(name, fn) { listeners.get(name)?.delete(fn); },
    requestAnimationFrame(fn) { const id = ++nextFrame; frames.set(id, fn); return id; },
    cancelAnimationFrame(id) { frames.delete(id); },
  };
  function key(key, shiftKey = false) {
    const event = { key, shiftKey, ...submitEvent() };
    for (const listener of [...(listeners.get('keydown') ?? [])]) listener(event);
    if (key === 'Tab' && !event.defaultPrevented) {
      const controls = document.querySelectorAll(FOCUSABLE_SELECTOR);
      const index = controls.indexOf(document.activeElement);
      controls[(index + (shiftKey ? -1 : 1) + controls.length) % controls.length]?.focus();
    }
    return event;
  }
  const hooks = {
    useState(initial) {
      const owner = current, index = owner.cursor++;
      if (!owner.slots[index]) {
        const slot = { value: typeof initial === 'function' ? initial() : initial };
        slot.set = next => {
          if (!owner.mounted) { owner.updatesAfterUnmount++; return; }
          const value = typeof next === 'function' ? next(slot.value) : next;
          if (!Object.is(value, slot.value)) { slot.value = value; owner.dirty = true; }
        };
        owner.slots[index] = slot;
      }
      return [owner.slots[index].value, owner.slots[index].set];
    },
    useRef(initial) { const index = current.cursor++; return current.slots[index] ??= { current: initial }; },
    useMemo(fn, deps) {
      const index = current.cursor++;
      if (!sameDeps(current.slots[index]?.deps, deps)) current.slots[index] = { value: fn(), deps };
      return current.slots[index].value;
    },
    useCallback(fn, deps) { return hooks.useMemo(() => fn, deps); },
    useLayoutEffect(fn, deps) { effect('layout', fn, deps); },
    useEffect(fn, deps) { effect('passive', fn, deps); },
  };
  function effect(phase, fn, deps) {
    const index = current.cursor++;
    if (!sameDeps(current.slots[index]?.deps, deps)) current.effects[phase].push({ index, fn, deps });
  }
  const modules = new Map();
  function load(path) {
    const url = new URL(path, import.meta.url);
    if (modules.has(url.href)) return modules.get(url.href);
    if (!compiled.has(url.href)) compiled.set(url.href, ts.transpileModule(readFileSync(url, 'utf8'), {
      fileName: url.pathname,
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    }).outputText);
    const exports = {};
    modules.set(url.href, exports);
    const require = name => {
      if (name === 'react') return hooks;
      if (name === 'react/jsx-runtime') {
        const jsx = (type, props, key) => ({ type, props, key });
        return { jsx, jsxs: jsx, Fragment: 'test-fragment' };
      }
      // Inert leaves retain their props for nested-dialog callback tests.
      if (['./ModalPortal', './PolypbaseIcon', './QuickStrainCreator', './SearchField', './RecentAccessList', './TabletQrScanner', './PageLoader', './BoxSearchStatus', './SuggestionList'].includes(name)) return { default: name.slice(2) };
      if (name.startsWith('.')) {
        const candidates = /\.(ts|tsx)$/.test(name) ? [name] : [`${name}.ts`, `${name}.tsx`, `${name}/index.ts`];
        const resolved = candidates.map(path => new URL(path, url)).find(candidate => existsSync(candidate));
        assert.ok(resolved, `Missing module ${name} imported by ${url.pathname}`);
        return load(resolved.href);
      }
      throw new Error(`Unexpected module import: ${name}`);
    };
    vm.runInNewContext(compiled.get(url.href), {
      exports, require, document, window, HTMLElement: Element, Error, URL, AbortController,
      __API_PROXY_ORIGIN__: null,
      fetch() { throw new Error('Network is forbidden in the create-box harness'); },
    }, { filename: url.pathname });
    if (url.pathname.endsWith('/api/client.ts')) exports.apiGet = path => {
      const request = { ...deferred(), path };
      requests.push(request);
      return request.promise;
    };
    return exports;
  }
  function instance(component, initialProps, { strictMode = false } = {}) {
    const owner = { slots: [], cursor: 0, dirty: true, mounted: true, updatesAfterUnmount: 0, effects: { layout: [], passive: [] } };
    const container = external('test-root');
    let props = initialProps, elements = new Map(), value;
    function reconcile(tree) {
      const next = new Map();
      function build(node, parent, path) {
        if (Array.isArray(node)) { node.forEach((child, i) => build(child, parent, `${path}/${child?.key ?? i}`)); return; }
        if (!node || typeof node !== 'object') return;
        const element = elements.get(path)?.type === node.type ? elements.get(path) : new Element(node.type);
        Object.assign(element, { props: node.props, key: node.key, children: [], parent, isConnected: true });
        parent.children.push(element);
        next.set(path, element);
        if (node.props.ref) node.props.ref.current = element;
        build(node.props.children, element, `${path}/children`);
      }
      container.children = [];
      build(tree, container, 'root');
      for (const [path, element] of elements) if (next.get(path) !== element) {
        element.isConnected = false;
        if (element.props.ref && element.props.ref.current === element) element.props.ref.current = null;
      }
      elements = next;
      if (document.activeElement?.disabled || document.activeElement?.isConnected === false) document.activeElement = document.body;
    }
    function render() {
      assert.ok(owner.mounted, 'Cannot render after unmount');
      owner.cursor = 0;
      owner.dirty = false;
      current = owner;
      try { value = component(props); } finally { current = null; }
      reconcile(value);
      for (const phase of ['layout', 'passive']) {
        const pending = owner.effects[phase];
        owner.effects[phase] = [];
        for (const { index, fn, deps } of pending) {
          owner.slots[index]?.cleanup?.();
          owner.slots[index] = { deps, phase, fn, cleanup: fn() };
        }
      }
      return value;
    }
    function flush() {
      for (let count = 0; owner.dirty; count++) {
        assert.ok(count < 40, 'Hook state did not settle');
        render();
      }
      return value;
    }
    function replayEffects() {
      // React's development setup -> cleanup -> setup, preserving state/refs.
      for (const phase of ['layout', 'passive']) for (const slot of owner.slots) if (slot?.phase === phase) slot.cleanup?.();
      for (const phase of ['layout', 'passive']) for (const slot of owner.slots) if (slot?.phase === phase) slot.cleanup = slot.fn();
      flush();
    }
    const api = {
      render, flush, replayEffects,
      get value() { return value; },
      get nodes() { return [...elements.values()]; },
      get form() { return api.nodes.find(node => node.type === 'form'); },
      get dialog() { return api.nodes.find(node => node.props.role === 'dialog'); },
      get controls() { return api.nodes.filter(node => node.matches(FOCUSABLE_SELECTOR)); },
      get updatesAfterUnmount() { return owner.updatesAfterUnmount; },
      byClass(name) { return api.nodes.find(node => node.matches(`.${name}`)); },
      field(name) { const label = api.byClass(name); return api.nodes.find(node => label?.contains(node) && ['input', 'select', 'textarea'].includes(node.type)); },
      setProps(next) { props = { ...props, ...next }; owner.dirty = true; return flush(); },
      change(node, value) { assert.ok(node, 'Missing field'); node.props.onChange({ target: { value } }); flush(); },
      click(node) { assert.ok(node, 'Missing button'); node.props.onClick(); flush(); },
      submit(event = submitEvent()) { assert.ok(api.form, 'Missing form'); return api.form.props.onSubmit(event); },
      async settle() { await tick(); if (owner.mounted) flush(); },
      unmount() {
        owner.mounted = false;
        for (const element of elements.values()) { element.isConnected = false; if (element.props.ref) element.props.ref.current = null; }
        if (container.contains(document.activeElement)) document.activeElement = document.body;
        roots.splice(roots.indexOf(container), 1);
        for (const phase of ['layout', 'passive']) for (const slot of owner.slots) if (slot?.phase === phase) slot.cleanup?.();
      },
    };
    render();
    if (strictMode) replayEffects();
    flush();
    return api;
  }

  function keyedChild(getNode) {
    let child = null, previousType, previousKey;
    return {
      get current() { return child; },
      sync() {
        const node = getNode();
        if (!node || node.type !== previousType || node.key !== previousKey) {
          child?.unmount();
          child = node ? instance(node.type, node.props) : null;
        } else child.setProps(node.props);
        previousType = node?.type;
        previousKey = node?.key;
        return child;
      },
      unmount() { child?.unmount(); child = null; },
    };
  }
  function confirmationHost() {
    const { useConfirmAction } = load('../src/components/ConfirmActionModal.tsx');
    let result;
    const owner = instance(() => { result = useConfirmAction(); return null; }, {});
    const child = keyedChild(() => result.confirmActionModal);
    function flush() { owner.flush(); child.sync(); }
    return {
      confirmAction(options) { const pending = result.confirmAction(options); flush(); return pending; },
      flush,
      get modal() { return child.current; },
      unmount() { child.unmount(); owner.unmount(); },
    };
  }
  return {
    document, window, opener, requests, key, load, instance, external, keyedChild, confirmationHost,
    listenerCount: () => listeners.get('keydown')?.size ?? 0,
    get frameCount() { return frames.size; },
    flushFrames() { const pending = [...frames.values()]; frames.clear(); pending.forEach(fn => fn(0)); },
  };
}

export function createBoxHarness(initialProps = {}, options = {}) {
  const env = options.environment ?? createBoxEnvironment();
  const confirmationHost = options.realConfirmation ? env.confirmationHost() : null;
  const confirmations = [], mutations = [], resolutions = [], navigations = [], openChanges = [];
  let generation = 0, boxCollectionRequests = 0, optionsRequests = 0;
  const profile = {
    id: 7, active_organization: { id: 1, name: 'Laboratory A' }, is_superuser: false,
    memberships: [{ organization: { id: 1 }, role: 'admin' }],
  };
  const props = {
    boxes: [], isBoxCollectionReady: true, exportOptions: { zones: [{ id: 10, organization_id: 1, name: 'Zone A' }] },
    presentation: 'modal', isOpen: true, isOptionsLoading: false, profile,
    t: key => key,
    onRequestBoxCollection() { boxCollectionRequests++; },
    onRequestOptions() { optionsRequests++; },
    onOpenChange(value) { openChanges.push(value); },
    isOperationCurrent: () => generation === 0,
    confirmAction(details) {
      if (confirmationHost) return confirmationHost.confirmAction(details);
      const request = { ...deferred(), details };
      confirmations.push(request);
      return request.promise;
    },
    onCreateBox(payload) { const request = { ...deferred(), payload }; mutations.push(request); return request.promise; },
    onResolveBoxCreation(requestId) { const request = { ...deferred(), requestId }; resolutions.push(request); return request.promise; },
    onSelectBox(id) { navigations.push(id); },
    ...initialProps,
  };
  const component = env.load('../src/components/CreateBoxPanel.tsx').default;
  let panel, parent = null, children = null;
  if (options.pilotage) {
    const PilotageView = env.load('../src/components/PilotageView.tsx').default;
    parent = env.instance(PilotageView, {
      ...props, activeOrganizationId: props.profile?.active_organization?.id ?? null,
      boxCollectionStatus: props.isBoxCollectionReady ? 'ready' : 'idle',
      isCreateBoxOpen: props.isOpen, isPhoneLayout: props.presentation === 'inline', isLoading: false,
      recentBoxes: [], search: '', searchResults: [], onCreateBoxOpenChange: props.onOpenChange,
    });
    // Reconcile only the relevant child by the actual parent-emitted type/key;
    // other Pilotage leaves remain inert. No copied key or reset policy.
    children = env.keyedChild(() => parent.nodes.find(node => node.type === component));
    panel = children.sync();
  } else panel = env.instance(component, props, options);
  return {
    env, get panel() { return panel; }, parent, props, confirmations, mutations, resolutions, navigations, openChanges, confirmationHost,
    setParentProps(next) {
      assert.ok(parent, 'This harness was not mounted through PilotageView');
      parent.setProps(next);
      panel = children.sync();
    },
    unmount() {
      if (children) children.unmount();
      else panel?.unmount();
      parent?.unmount();
      confirmationHost?.unmount();
    },
    api: env.load('../src/api/client.ts'),
    invalidateOperation() { generation++; },
    get boxCollectionRequests() { return boxCollectionRequests; },
    get optionsRequests() { return optionsRequests; },
  };
}

export function createPilotageHarness(initialProps = {}, options = {}) {
  return createBoxHarness(initialProps, { ...options, pilotage: true });
}

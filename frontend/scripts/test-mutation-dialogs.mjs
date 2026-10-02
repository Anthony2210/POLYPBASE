import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

// Execute real components/hooks with deferred promises and a small DOM model.
// This covers handler/effect contracts, not browser layout or screen-reader output.
const compiled = new Map();
const focusableSelector = 'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';
const dialogSelector = '[role="dialog"][aria-modal="true"]';
const controlTypes = ['button', 'input', 'select', 'textarea'];
const sameDeps = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
const plain = (value) => JSON.parse(JSON.stringify(value));
function deferred() {
  let resolve, reject;
  const promise = new Promise((accept, decline) => { resolve = accept; reject = decline; });
  return { promise, resolve, reject };
}
function text(node) {
  if (Array.isArray(node)) return node.map(text).join('');
  if (node == null || typeof node === 'boolean') return '';
  return typeof node === 'object' ? text(node.props?.children) : String(node);
}

function environment() {
  const roots = [], listeners = new Map();
  const document = {
    activeElement: null,
    querySelectorAll(selector) { return roots.flatMap((root) => root.matches(selector) ? [root, ...root.querySelectorAll(selector)] : root.querySelectorAll(selector)); },
  };
  class Element {
    constructor(type, props = {}) {
      this.type = type;
      this.props = props;
      this.children = [];
      this.parent = null;
      this.isConnected = true;
      this.focusCalls = 0;
    }
    get disabled() { return Boolean(this.props.disabled); }
    focus() {
      this.focusCalls++;
      if (this.isConnected && !this.disabled) document.activeElement = this;
    }
    contains(node) { return node === this || this.children.some((child) => child.contains(node)); }
    matches(selector) {
      if (selector === dialogSelector) return this.props.role === 'dialog' && this.props['aria-modal'] === 'true';
      assert.equal(selector, focusableSelector, `Unexpected DOM selector: ${selector}`);
      return (controlTypes.includes(this.type) && !this.disabled)
        || (this.type === 'a' && Boolean(this.props.href))
        || (this.props.tabIndex != null && this.props.tabIndex !== -1);
    }
    querySelectorAll(selector) {
      return this.children.flatMap((child) => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; }
  }
  document.body = new Element('body');
  const opener = new Element('button', { children: 'Open dialog' });
  roots.push(opener);
  opener.focus();
  const window = {
    addEventListener(name, fn) {
      if (!listeners.has(name)) listeners.set(name, new Set());
      listeners.get(name).add(fn);
    },
    removeEventListener(name, fn) { listeners.get(name)?.delete(fn); },
  };
  function key(key, shiftKey = false) {
    const event = { key, shiftKey, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    // Keep every listener, in registration order, to detect underlying-dialog interference.
    for (const listener of [...(listeners.get('keydown') ?? [])]) listener(event);
    if (key === 'Tab' && !event.defaultPrevented) {
      const controls = document.querySelectorAll(focusableSelector);
      const index = controls.indexOf(document.activeElement);
      controls[(index + (shiftKey ? -1 : 1) + controls.length) % controls.length]?.focus();
    }
    return event;
  }
  let current;
  const hooks = {
    useState(initial) {
      const owner = current, index = owner.cursor++;
      if (!owner.slots[index]) {
        const slot = { value: typeof initial === 'function' ? initial() : initial };
        slot.set = (next) => {
          const value = typeof next === 'function' ? next(slot.value) : next;
          if (!Object.is(value, slot.value)) { slot.value = value; owner.dirty = true; }
        };
        owner.slots[index] = slot;
      }
      return [owner.slots[index].value, owner.slots[index].set];
    },
    useRef(initial) { const index = current.cursor++; return current.slots[index] ??= { current: initial }; },
    useMemo(factory, deps) {
      const index = current.cursor++;
      if (!sameDeps(current.slots[index]?.deps, deps)) current.slots[index] = { value: factory(), deps };
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
    if (!compiled.has(url.href)) {
      compiled.set(url.href, ts.transpileModule(readFileSync(url, 'utf8'), {
        compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
      }).outputText);
    }
    const exports = {};
    modules.set(url.href, exports);
    const require = (name) => {
      if (name === 'react') return hooks;
      if (name === 'react/jsx-runtime') {
        const jsx = (type, props, key) => ({ type, props, key });
        return { jsx, jsxs: jsx };
      }
      // Portals are represented in document order; icons have no interactive DOM.
      if (name === './ModalPortal' || name === './PolypbaseIcon') return { default: name };
      if (name.startsWith('../hooks/') || name.startsWith('../utils/')) return load(new URL(`${name}.ts`, url).href);
      throw new Error(`Unexpected import: ${name}`);
    };
    vm.runInNewContext(compiled.get(url.href), { exports, require, document, window, HTMLElement: Element }, { filename: url.pathname });
    return exports;
  }
  function instance(component, initialProps, attach = true) {
    const owner = { slots: [], cursor: 0, dirty: true, effects: { layout: [], passive: [] } };
    const container = new Element('test-root');
    if (attach) roots.push(container);
    let props = initialProps, elements = new Map(), value, mounted = true;
    function reconcile(tree) {
      const next = new Map();
      function build(node, parent, path) {
        if (Array.isArray(node)) { node.forEach((child, i) => build(child, parent, `${path}/${child?.key ?? i}`)); return; }
        if (!node || typeof node !== 'object') return;
        const element = elements.get(path)?.type === node.type ? elements.get(path) : new Element(node.type);
        element.props = node.props;
        element.children = [];
        element.parent = parent;
        element.isConnected = true;
        parent.children.push(element);
        next.set(path, element);
        if (node.props.ref) node.props.ref.current = element;
        build(node.props.children, element, `${path}/children`);
      }
      container.children = [];
      build(tree, container, 'root');
      for (const [path, element] of elements) if (!next.has(path)) element.isConnected = false;
      elements = next;
      if (document.activeElement?.disabled || document.activeElement?.isConnected === false) document.activeElement = document.body;
    }
    function render() {
      assert.ok(mounted, 'Cannot render an unmounted instance');
      owner.cursor = 0;
      owner.dirty = false;
      current = owner;
      try { value = component(props); } finally { current = null; }
      if (attach) reconcile(value);
      for (const phase of ['layout', 'passive']) {
        const effects = owner.effects[phase];
        owner.effects[phase] = [];
        for (const { index, fn, deps } of effects) {
          owner.slots[index]?.cleanup?.();
          owner.slots[index] = { deps, phase, cleanup: fn() };
        }
      }
      return value;
    }
    function flush() {
      for (let count = 0; owner.dirty; count++) {
        assert.ok(count < 20, 'Hook state did not settle');
        render();
      }
      return value;
    }
    const api = {
      render, flush,
      get value() { return value; },
      get nodes() { return [...elements.values()]; },
      get dialog() { return api.nodes.find((node) => node.props.role === 'dialog'); },
      get fields() { return api.nodes.filter((node) => ['input', 'select', 'textarea'].includes(node.type)); },
      get controls() { return api.nodes.filter((node) => controlTypes.includes(node.type)); },
      get form() { return api.nodes.find((node) => node.type === 'form'); },
      get backdrop() { return api.nodes.find((node) => node.props.role === 'presentation'); },
      setProps(next) { props = { ...props, ...next }; owner.dirty = true; return flush(); },
      unmount() {
        if (container.contains(document.activeElement)) document.activeElement = document.body;
        for (const element of elements.values()) { element.isConnected = false; if (element.props.ref) element.props.ref.current = null; }
        if (attach) roots.splice(roots.indexOf(container), 1);
        for (const phase of ['layout', 'passive']) for (const slot of owner.slots) if (slot?.phase === phase) slot.cleanup?.();
        mounted = false;
      },
    };
    render();
    return api;
  }
  return { document, opener, key, load, instance, listenerCount: () => listeners.get('keydown')?.size ?? 0 };
}

const cases = [
  { name: 'MoveBoxModal', titles: { en: 'Move box', fr: 'Transférer la boîte' }, initialType: 'select', saves: { en: 'Save movement', fr: 'Enregistrer le transfert' }, saving: { en: 'Saving...', fr: 'Enregistrement...' } },
  { name: 'SubcultureModal', titles: { en: 'Create a subculture', fr: 'Repiquer la boîte' }, initialType: 'input', saves: { en: 'Create subculture', fr: 'Créer le repiquage' }, saving: { en: 'Creating...', fr: 'Création...' } },
];
function fixture(spec, overrides = {}, env = environment()) {
  let closed = 0;
  const box = { id: 17, global_code: '1-ATL.001', strain: { code: '1-ATL' }, organization: { id: 1, name: 'Test institution' }, thermal_zone: { id: 2, name: 'Current zone' } };
  const props = {
    box, existingBoxes: [box], zones: [2, 3, 4].map((id) => ({ id, name: `Zone ${id}`, organization: box.organization, is_active: true })),
    language: 'en', isSaving: false, error: null, onClose() { closed++; }, onSubmit: async () => {}, ...overrides,
  };
  const modal = env.instance(env.load(`../src/components/${spec.name}.tsx`).default, props);
  return { env, modal, get closed() { return closed; } };
}
function submitButton(modal) { return modal.controls.find((node) => node.props.type === 'submit'); }
function closeButtons(modal) {
  return modal.controls.filter((node) => node.type === 'button' && (node.props['aria-label'] === 'Cancel' || text(node) === 'Cancel' || node.props['aria-label'] === 'Annuler' || text(node) === 'Annuler'));
}
function change(node, value) { node.props.onChange({ target: { value } }); }
function submitEvent() { return { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } }; }
function addSecondChild(spec, modal) {
  if (spec.name === 'SubcultureModal') {
    modal.controls.find((node) => text(node).includes('Add a box')).props.onClick();
    modal.flush();
    assert.equal(modal.controls.filter((node) => node.props['aria-label'] === 'Remove this box').length, 2);
  }
}
function editDraft(spec, modal) {
  if (spec.name === 'MoveBoxModal') {
    change(modal.fields.find((node) => node.type === 'select'), '4');
    change(modal.fields.find((node) => node.props.type === 'datetime-local'), '2026-09-21T12:34');
    change(modal.fields.find((node) => node.type === 'textarea'), '  Keep this movement note  ');
  } else {
    addSecondChild(spec, modal);
    change(modal.fields.find((node) => node.props.type === 'date'), '2026-09-21');
    change(modal.fields.find((node) => node.props.maxLength === 180), '  Keep this reason  ');
    change(modal.fields.find((node) => node.props.type === 'number'), '0');
    change(modal.fields.find((node) => node.props.placeholder === 'Optional'), '  Keep this child note  ');
  }
  modal.flush();
}
function draftValues(modal) { return modal.fields.map((node) => node.props.value); }
function assertBusy(h, checkInitialFocus = true) {
  const { env, modal } = h;
  assert.equal(modal.dialog.props['aria-busy'], true);
  assert.ok(modal.fields.length > 0);
  assert.ok(modal.controls.every((node) => node.disabled), 'Every field, close, add/remove and submit control must be disabled');
  assert.equal(closeButtons(modal).length, 2, 'Both header and footer close buttons are covered');
  if (checkInitialFocus) assert.ok(env.document.activeElement === modal.dialog, 'Busy dialog must contain focus');
  for (const reverse of [false, true]) {
    assert.equal(env.key('Tab', reverse).defaultPrevented, true);
    assert.equal(env.document.activeElement, modal.dialog);
  }
  modal.backdrop.props.onClick();
  closeButtons(modal).forEach((node) => node.props.onClick());
  assert.equal(env.key('Escape').defaultPrevented, true);
  assert.equal(h.closed, 0);
}

for (const spec of cases) {
  for (const language of ['en', 'fr']) {
    test(`${spec.name}: ${language} label, initial focus, forward/reverse Tab, idle Escape and opener restore`, () => {
      const h = fixture(spec, { language }), { env, modal } = h;
      assert.equal(modal.dialog.props['aria-modal'], 'true');
      assert.equal(modal.dialog.props.tabIndex, -1);
      assert.equal(modal.dialog.props['aria-busy'], false);
      const title = modal.nodes.find((node) => node.props.id === modal.dialog.props['aria-labelledby']);
      assert.ok(title);
      assert.equal(text(title), spec.titles[language]);
      const initial = modal.fields.find((node) => node.props.ref);
      assert.equal(initial.type, spec.initialType);
      assert.equal(env.document.activeElement, initial);
      for (const field of modal.fields) {
        assert.equal(field.parent.type, 'label');
        assert.ok(text(field.parent).trim(), 'Every field has a nonempty wrapping label');
      }
      const controls = modal.dialog.querySelectorAll(focusableSelector), first = controls[0], last = controls.at(-1);
      initial.focus();
      assert.equal(env.key('Tab').defaultPrevented, false, 'Interior Tab uses native order');
      assert.equal(env.document.activeElement, controls[controls.indexOf(initial) + 1]);
      assert.equal(env.key('Tab', true).defaultPrevented, false);
      assert.equal(env.document.activeElement, initial);
      first.focus();
      assert.equal(env.key('Tab', true).defaultPrevented, true);
      assert.equal(env.document.activeElement, last);
      assert.equal(env.key('Tab').defaultPrevented, true);
      assert.equal(env.document.activeElement, first);
      for (const reverse of [false, true]) {
        env.opener.focus();
        assert.equal(env.key('Tab', reverse).defaultPrevented, true);
        assert.equal(env.document.activeElement, reverse ? last : first);
        modal.dialog.focus();
        env.key('Tab', reverse);
        assert.equal(env.document.activeElement, reverse ? last : first);
      }
      env.key('Enter');
      assert.equal(h.closed, 0);
      assert.equal(env.key('Escape').defaultPrevented, true);
      assert.equal(h.closed, 1);
      modal.unmount();
      assert.equal(env.document.activeElement, env.opener);
      assert.equal(env.listenerCount(), 0);
    });
  }

  test(`${spec.name}: disconnected opener is never focused during cleanup`, () => {
    const { env, modal } = fixture(spec);
    env.opener.isConnected = false;
    const calls = env.opener.focusCalls;
    modal.unmount();
    assert.equal(env.opener.focusCalls, calls);
    assert.notEqual(env.document.activeElement, env.opener);
    assert.equal(env.listenerCount(), 0);
  });

  test(`${spec.name}: idle backdrop and both close buttons dismiss; dialog click stays inside`, () => {
    const h = fixture(spec);
    let stopped = false;
    h.modal.dialog.props.onClick({ stopPropagation() { stopped = true; } });
    assert.equal(stopped, true);
    assert.equal(h.closed, 0);
    h.modal.backdrop.props.onClick();
    closeButtons(h.modal).forEach((node) => node.props.onClick());
    assert.equal(h.closed, 3);
    h.modal.unmount();
  });

  test(`${spec.name}: pending promise locks same-tick duplicates/dismissal and restores edit focus`, async () => {
    const operation = deferred(), payloads = [];
    const h = fixture(spec, { onSubmit(payload) { payloads.push(plain(payload)); return operation.promise; } });
    editDraft(spec, h.modal);
    const handler = h.modal.form.props.onSubmit, event = submitEvent();
    const pending = handler(event);
    const duplicate = handler(submitEvent());
    assert.equal(event.defaultPrevented, true);
    assert.equal(payloads.length, 1);
    // These still reference the idle render: the synchronous ref must guard them.
    h.modal.backdrop.props.onClick();
    closeButtons(h.modal).forEach((node) => node.props.onClick());
    h.env.key('Escape');
    assert.equal(h.closed, 0);
    h.modal.flush();
    assertBusy(h);
    assert.equal(text(submitButton(h.modal)), spec.saves.en, 'Confirmation-pending is not an API save');
    await h.modal.form.props.onSubmit(submitEvent());
    assert.equal(payloads.length, 1);
    operation.resolve();
    await Promise.all([pending, duplicate]);
    h.modal.flush();
    assert.equal(h.modal.dialog.props['aria-busy'], false);
    assert.ok(h.modal.controls.every((node) => !node.disabled));
    assert.ok(h.env.document.activeElement === h.modal.fields.find((node) => node.props.ref), 'Settled submission must restore initial-field focus after the field is enabled');
    h.modal.unmount();
  });

  for (const language of ['en', 'fr']) {
    test(`${spec.name}: ${language} externally saving locks fields/close controls and uses actual save label`, async () => {
      let calls = 0;
      const h = fixture(spec, { language, onSubmit: async () => { calls++; } });
      if (language === 'en') addSecondChild(spec, h.modal);
      const idleHandler = h.modal.form.props.onSubmit;
      h.modal.setProps({ isSaving: true });
      assertBusy(h);
      assert.equal(text(submitButton(h.modal)), spec.saving[language]);
      await idleHandler(submitEvent());
      await h.modal.form.props.onSubmit(submitEvent());
      assert.equal(calls, 0, 'Saving ref also rejects a stale idle submit handler');
      h.modal.setProps({ isSaving: false });
      assert.equal(h.modal.dialog.props['aria-busy'], false);
      assert.ok(h.modal.controls.every((node) => !node.disabled));
      h.env.key('Escape');
      assert.equal(h.closed, 1);
      h.modal.unmount();
    });
  }

  test(`${spec.name}: rejected mutation retains edited form, announces error and permits identical retry`, async () => {
    const attempts = [], payloads = [];
    const h = fixture(spec, { onSubmit(payload) { payloads.push(plain(payload)); const attempt = deferred(); attempts.push(attempt); return attempt.promise; } });
    editDraft(spec, h.modal);
    const before = draftValues(h.modal);
    const pending = h.modal.form.props.onSubmit(submitEvent());
    const failure = new Error('Test mutation failed');
    const rejected = assert.rejects(pending, (error) => error === failure);
    h.modal.setProps({ isSaving: true });
    assertBusy(h);
    attempts[0].reject(failure);
    await rejected;
    h.modal.setProps({ isSaving: false, error: failure.message });
    const alert = h.modal.nodes.find((node) => node.props.role === 'alert');
    assert.ok(alert);
    assert.equal(text(alert), failure.message);
    assert.deepEqual(draftValues(h.modal), before);
    assert.equal(h.closed, 0);
    assert.ok(h.modal.controls.every((node) => !node.disabled));
    const retry = h.modal.form.props.onSubmit(submitEvent());
    h.modal.flush();
    assertBusy(h);
    assert.equal(payloads.length, 2);
    assert.deepEqual(payloads[1], payloads[0]);
    if (spec.name === 'SubcultureModal') {
      assert.equal(payloads[1].children.length, 2);
      assert.equal(payloads[1].children[0].initial_polyp_count, 0);
      assert.equal(payloads[1].reason, 'Keep this reason');
      assert.equal(payloads[1].children[0].notes, 'Keep this child note');
    } else {
      assert.equal(payloads[1].thermal_zone_id, 4);
      assert.equal(payloads[1].notes, 'Keep this movement note');
    }
    attempts[1].resolve();
    await retry;
    h.modal.setProps({ error: null });
    assert.deepEqual(draftValues(h.modal), before);
    assert.equal(h.modal.nodes.some((node) => node.props.role === 'alert'), false);
    h.modal.unmount();
  });

  test(`${spec.name}: nested real ConfirmActionModal owns Tab/Escape, cancellation unlocks, acceptance stays locked through save`, async () => {
    const env = environment();
    const confirm = env.load('../src/components/ConfirmActionModal.tsx');
    const host = env.instance(() => confirm.useConfirmAction(), {}, false);
    const mutation = deferred();
    let requests = 0, mutations = 0;
    const h = fixture(spec, { async onSubmit() {
      requests++;
      const accepted = await host.value.confirmAction({ title: 'Confirm test operation', confirmLabel: 'Confirm', cancelLabel: 'Cancel' });
      if (!accepted) return;
      mutations++;
      h.modal.setProps({ isSaving: true });
      await mutation.promise;
      h.modal.setProps({ isSaving: false });
    } }, env);
    editDraft(spec, h.modal);
    const before = draftValues(h.modal);
    function mountConfirmation() {
      host.flush();
      const node = host.value.confirmActionModal;
      assert.ok(node);
      return env.instance(node.type, node.props);
    }
    submitButton(h.modal).focus();
    const cancelled = h.modal.form.props.onSubmit(submitEvent());
    // Mount top dialog before the underlying busy layout effect runs, so it
    // must not steal the confirmation's initial focus during the same commit.
    const nested = mountConfirmation();
    const first = nested.controls[0], last = nested.controls.at(-1);
    assert.equal(env.document.activeElement, first);
    h.modal.flush();
    assert.equal(env.document.activeElement, first);
    assert.equal(env.listenerCount(), 2);
    assert.ok(h.modal.controls.every((node) => node.disabled));
    assert.equal(text(submitButton(h.modal)), spec.saves.en);
    const underlyingFocusCalls = h.modal.dialog.focusCalls;
    assert.equal(env.key('Tab', true).defaultPrevented, true);
    assert.equal(env.document.activeElement, last);
    assert.equal(env.key('Tab').defaultPrevented, true);
    assert.equal(env.document.activeElement, first);
    assert.equal(h.modal.dialog.focusCalls, underlyingFocusCalls);
    // With focus outside both dialogs, only the top dialog may recover it.
    env.opener.focus();
    env.key('Tab');
    assert.equal(env.document.activeElement, first);
    assert.equal(h.modal.dialog.focusCalls, underlyingFocusCalls);
    // Cancellation changes the real confirmation owner's state, never onClose.
    assert.equal(env.key('Escape').defaultPrevented, false, 'Underlying hook must not consume the confirmation Escape');
    assert.equal(h.closed, 0);
    host.flush();
    assert.equal(host.value.confirmActionModal, null);
    nested.unmount();
    await cancelled;
    h.modal.flush();
    assert.equal(mutations, 0);
    assert.equal(h.modal.dialog.props['aria-busy'], false);
    assert.ok(h.modal.controls.every((node) => !node.disabled));
    assert.deepEqual(draftValues(h.modal), before);
    assert.equal(env.listenerCount(), 1);

    const accepted = h.modal.form.props.onSubmit(submitEvent());
    const confirmation = mountConfirmation();
    h.modal.flush();
    confirmation.controls.at(-1).props.onClick();
    host.flush();
    assert.equal(host.value.confirmActionModal, null);
    confirmation.unmount();
    // Drain cross-VM promise assimilation without resolving the API mutation.
    for (let tick = 0; tick < 12; tick++) await Promise.resolve();
    h.modal.flush();
    assert.equal(requests, 2);
    assert.equal(mutations, 1);
    const regainedBusyFocus = env.document.activeElement === h.modal.dialog;
    // Finish checking guards and settlement even if automatic focus recovery
    // fails; the explicit regression assertion below still fails the test.
    assertBusy(h, false);
    assert.equal(text(submitButton(h.modal)), spec.saving.en);
    await h.modal.form.props.onSubmit(submitEvent());
    assert.equal(requests, 2);
    mutation.resolve();
    await accepted;
    h.modal.flush();
    assert.equal(h.modal.dialog.props['aria-busy'], false);
    assert.deepEqual(draftValues(h.modal), before);
    assert.equal(h.closed, 0);
    host.unmount();
    h.modal.unmount();
    assert.equal(env.document.activeElement, env.opener);
    assert.equal(env.listenerCount(), 0);
    assert.ok(regainedBusyFocus, 'Busy dialog must recover focus after its nested confirmation closes');
  });
}

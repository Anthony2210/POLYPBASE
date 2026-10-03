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
      if (name === 'lucide-react') return Object.fromEntries(['AlertTriangle', 'CheckCircle2', 'X', 'XCircle'].map((icon) => [icon, `icon-${icon}`]));
      if (name === 'react/jsx-runtime') {
        const jsx = (type, props, key) => ({ type, props, key });
        return { jsx, jsxs: jsx, Fragment: 'test-fragment' };
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
  { name: 'MoveBoxModal', titles: { en: 'Move box', fr: 'Déplacer la boîte' }, initialType: 'select', saves: { en: 'Move', fr: 'Déplacer' }, saving: { en: 'Saving...', fr: 'Enregistrement...' } },
  { name: 'SubcultureModal', titles: { en: 'Create a subculture', fr: 'Repiquer la boîte' }, initialType: 'input', saves: { en: 'Subculture', fr: 'Repiquer' }, saving: { en: 'Creating...', fr: 'Création...' } },
];
function fixture(spec, overrides = {}, env = environment()) {
  let closed = 0;
  const box = { id: 17, global_code: '1-ATL.001', species: { scientific_name: 'Aurelia aurita' }, strain: { code: '1-ATL' }, organization: { id: 1, name: 'Test institution' }, thermal_zone: { id: 2, name: 'Current zone' } };
  const props = {
    box, existingBoxes: [box], zones: [2, 3, 4].map((id) => ({ id, name: `Zone ${id}`, organization: box.organization, is_active: true })),
    language: 'en', isSaving: false, error: null, onClose() { closed++; }, onSubmit: async () => {}, ...overrides,
  };
  const modal = env.instance(env.load(`../src/components/${spec.name}.tsx`).default, props);
  return { env, modal, get closed() { return closed; } };
}
function hasClass(node, name) { return node.props.className?.split(/\s+/).includes(name) ?? false; }
function byClass(modal, name) { return modal.nodes.find((node) => hasClass(node, name)); }
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
    test(`${spec.name}: ${language} shared layout keeps labelled fields/errors in one body and actions outside it`, () => {
      const { env, modal } = fixture(spec, { language, error: 'Test validation error' });
      const isMove = spec.name === 'MoveBoxModal';
      const body = byClass(modal, 'box-dialog-body');
      const footer = byClass(modal, 'box-dialog-actions');
      const heading = byClass(modal, 'box-dialog-heading');
      const context = byClass(modal, 'box-dialog-context');
      const close = byClass(modal, 'box-dialog-close');
      const title = modal.nodes.find((node) => node.props.id === modal.dialog.props['aria-labelledby']);
      assert.equal(modal.dialog.props.className, isMove ? 'move-modal box-dialog box-dialog--move' : 'subculture-modal box-dialog box-dialog--subculture');
      assert.equal(modal.backdrop.props.className, 'modal-backdrop box-dialog-backdrop');
      assert.equal(heading.props.className, 'subculture-heading box-dialog-heading');
      assert.equal(modal.form.props.className, isMove ? 'move-form box-dialog-form' : 'subculture-form box-dialog-form');
      assert.equal(footer.props.className, 'subculture-actions box-dialog-actions');
      assert.equal(modal.nodes.filter((node) => hasClass(node, 'box-dialog-body')).length, 1);
      assert.deepEqual(modal.form.children, [body, footer], 'Body and footer are direct form siblings for parent-owned scrolling');
      assert.deepEqual(modal.dialog.children, [heading, modal.form]);
      assert.equal(body.type, 'div');
      assert.equal(footer.type, 'footer');
      assert.equal(title.type, 'h2');
      assert.equal(text(title), spec.titles[language]);
      assert.ok(heading.contains(title));
      assert.equal(close.parent, heading);
      assert.equal(close.props.className, 'icon-button box-dialog-close');
      assert.equal(close.props.type, 'button');
      assert.equal(close.props['aria-label'], language === 'fr' ? 'Annuler' : 'Cancel');
      assert.equal(close.props.title, close.props['aria-label']);
      assert.equal(close.disabled, false);
      assert.equal(close.children.length, 1);
      const icon = close.children[0];
      assert.equal(icon.type, './PolypbaseIcon');
      assert.equal(icon.props.name, 'close');
      assert.equal(icon.props.size, 19);
      assert.equal(icon.props['aria-hidden'], 'true');
      assert.equal(text(close), '', 'Close is an accessible icon, not a literal x');
      assert.ok(modal.fields.every((field) => body.contains(field)));
      assert.ok(modal.fields.every((field) => field.parent.type === 'label' && text(field.parent).trim()));
      assert.ok(body.contains(modal.nodes.find((node) => node.props.role === 'alert')));
      assert.ok(!body.contains(footer));
      assert.equal(footer.children.length, 2);
      assert.ok(footer.contains(submitButton(modal)));
      assert.equal(text(submitButton(modal)), spec.saves[language]);
      assert.equal(submitButton(modal).props.type, 'submit');
      const initial = modal.fields.find((field) => field.props.ref);
      assert.equal(env.document.activeElement, initial);
      assert.equal(initial.props.required, true);
      assert.equal(initial.type, spec.initialType);
      assert.ok(text(context).includes('1-ATL.001'));
      if (isMove) {
        const flow = byClass(modal, 'box-dialog-location-flow');
        const current = byClass(modal, 'current-zone-card');
        assert.equal(current.parent, flow);
        assert.equal(initial.parent.parent, flow);
        assert.deepEqual(flow.children, [current, initial.parent]);
        assert.ok(body.contains(flow));
        assert.ok(body.contains(byClass(modal, 'location-history')));
        assert.ok(heading.contains(context));
      } else {
        assert.ok(body.contains(context));
        assert.ok(text(context).includes('Aurelia aurita'));
        assert.equal(heading.contains(context), false, 'Source identity stays distinct from the action title');
        assert.ok(body.contains(byClass(modal, 'subculture-event-fields')));
        assert.ok(body.contains(byClass(modal, 'subculture-children-heading')));
        assert.ok(body.contains(byClass(modal, 'subculture-children')));
      }
      modal.unmount();
    });

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

for (const currentZone of [{ id: 2, name: 'Current zone' }, null]) {
  test(`MoveBoxModal: location flow preserves exact payload with ${currentZone ? 'known' : 'null'} current zone and historical display`, async () => {
    const organization = { id: 1, name: 'Test institution' };
    const locations = Array.from({ length: 7 }, (_, index) => ({
      id: index + 1, thermal_zone: { id: 10 + index, name: `History zone ${index + 1}` },
      starts_at: '2026-09-20T12:00:00Z', ends_at: index === 1 ? null : '2026-09-21T12:00:00Z',
      end_date_unknown: index === 0, notes: index === 0 ? 'Historical note' : '',
    }));
    const payloads = [];
    const h = fixture(cases[0], {
      box: { id: 17, global_code: '1-ATL.001', organization, thermal_zone: currentZone, locations },
      zones: [
        { id: 2, name: 'Current zone', organization, is_active: true },
        { id: 3, name: 'Destination', organization, is_active: true },
        { id: 4, name: 'Inactive', organization, is_active: false },
        { id: 5, name: 'Other institution', organization: { id: 9 }, is_active: true },
      ],
      onSubmit: async (payload) => { payloads.push(plain(payload)); },
    });
    const destination = h.modal.fields.find((field) => field.type === 'select');
    assert.deepEqual(destination.children.map((option) => option.props.value), currentZone ? [3] : [2, 3]);
    assert.ok(text(byClass(h.modal, 'current-zone-card')).includes(currentZone ? 'Current zone' : 'No zone'));
    assert.equal(h.modal.nodes.filter((node) => hasClass(node, 'location-row')).length, 6);
    const history = text(byClass(h.modal, 'location-history'));
    assert.ok(history.includes('end date unknown'));
    assert.ok(history.includes('current'));
    assert.ok(history.includes('Historical note'));
    assert.equal(history.includes('History zone 7'), false);
    change(destination, '3');
    change(h.modal.fields.find((field) => field.props.type === 'datetime-local'), '2026-09-21T12:34');
    change(h.modal.fields.find((field) => field.type === 'textarea'), '  Movement note  ');
    h.modal.flush();
    const event = submitEvent();
    await h.modal.form.props.onSubmit(event);
    assert.equal(event.defaultPrevented, true);
    assert.deepEqual(payloads, [{
      expected_thermal_zone_id: currentZone?.id ?? null, thermal_zone_id: 3,
      moved_at: new Date('2026-09-21T12:34').toISOString(), notes: 'Movement note',
    }]);
    h.modal.flush();
    h.modal.unmount();
  });
}

test('SubcultureModal: grouped children preserve generated identities, zero versus empty, trimming and payload after removal', async () => {
  const payloads = [];
  const h = fixture(cases[1], { onSubmit: async (payload) => { payloads.push(plain(payload)); } });
  addSecondChild(cases[1], h.modal);
  h.modal.controls.find((node) => text(node).includes('Add a box')).props.onClick();
  h.modal.flush();
  const counts = h.modal.fields.filter((field) => field.props.type === 'number');
  assert.equal(counts.length, 3);
  for (const count of counts) {
    assert.equal(count.props.min, '0');
    assert.equal(count.props.step, '1');
    assert.equal(count.props.value, '');
  }
  for (const code of h.modal.fields.filter((field) => field.props.readOnly)) {
    assert.equal(code.props.required, true);
    assert.equal(code.props.readOnly, true);
  }
  change(h.modal.fields.find((field) => field.props.type === 'date'), '2026-09-21');
  change(h.modal.fields.find((field) => field.props.maxLength === 180), '  Dense culture  ');
  change(counts[0], '0');
  change(counts[1], '9');
  h.modal.flush();
  change(h.modal.fields.filter((field) => field.props.type === 'number')[1], '');
  change(h.modal.fields.filter((field) => field.props.type === 'number')[2], '12');
  change(h.modal.fields.find((field) => field.props.placeholder === 'Optional'), '  Child note  ');
  h.modal.flush();
  assert.deepEqual(h.modal.fields.filter((field) => field.props.type === 'number').map((field) => field.props.value), [0, '', 12]);
  const children = [0, null, 12].map((count, index) => ({
    global_code: `1-ATL.00${index + 2}`, local_code: '', box_number: `00${index + 2}`,
    thermal_zone_id: 2, copy_origin: true, initial_polyp_count: count, notes: index === 0 ? 'Child note' : '',
  }));
  await h.modal.form.props.onSubmit(submitEvent());
  h.modal.flush();
  assert.deepEqual(payloads[0], { event_date: '2026-09-21', reason: 'Dense culture', notes: '', children });
  h.modal.controls.filter((node) => node.props['aria-label'] === 'Remove this box')[2].props.onClick();
  h.modal.flush();
  assert.equal(h.modal.nodes.filter((node) => hasClass(node, 'subculture-child')).length, 2);
  assert.equal(byClass(h.modal, 'box-dialog-body').contains(byClass(h.modal, 'box-dialog-actions')), false);
  await h.modal.form.props.onSubmit(submitEvent());
  h.modal.flush();
  assert.deepEqual(payloads[1], { ...payloads[0], children: children.slice(0, 2) });
  h.modal.unmount();
});

for (const spec of cases) {
  test(`${spec.name}: no allowed zones keeps primary disabled, fields labelled and cancel reachable outside body`, () => {
    const h = fixture(spec, { zones: [] });
    const primary = submitButton(h.modal);
    const body = byClass(h.modal, 'box-dialog-body');
    const footer = byClass(h.modal, 'box-dialog-actions');
    assert.equal(primary.disabled, true);
    assert.equal(closeButtons(h.modal).length, 2);
    assert.ok(closeButtons(h.modal).every((button) => !button.disabled));
    assert.ok(footer.contains(closeButtons(h.modal)[1]));
    assert.equal(body.contains(footer), false);
    assert.ok(h.modal.fields.every((field) => field.parent.type === 'label' && text(field.parent).trim()));
    if (spec.name === 'MoveBoxModal') assert.ok(text(byClass(h.modal, 'location-history')).includes('No location history for this box.'));
    h.modal.unmount();
  });
}

const operationalCases = [
  ...['qualify', 'deactivate', 'reactivate', 'assign'].map((action) => ({ name: 'BoxLifecycleModal', action })),
  ...['active', 'inactive'].map((action) => ({ name: 'BoxInventoryBatchModal', action })),
];
function operationalFixture(spec, overrides = {}) {
  const env = environment();
  let closed = 0;
  const box = { id: 17, global_code: '1-ATL.001', status: 'pending_review', species: { scientific_name: 'Test species' }, thermal_zone: null };
  const props = {
    action: spec.action, box, zones: [{ id: 3, name: 'Zone 3', is_active: true }],
    selectedBoxes: [{ id: box.id, global_code: box.global_code, species_name: 'Test species', has_location: false }],
    result: null, error: null, isSaving: false, t: (key) => key,
    onClose() { closed++; }, onSubmit: async () => {}, onConfirm: async () => {}, ...overrides,
  };
  const modal = env.instance(env.load(`../src/components/${spec.name}.tsx`).default, props);
  const start = () => spec.name === 'BoxLifecycleModal'
    ? modal.form.props.onSubmit(submitEvent())
    : modal.controls.find((node) => text(node) === 'boxInventoryBatchConfirm').props.onClick();
  if (spec.action === 'deactivate') change(modal.fields.find((node) => node.type === 'textarea'), '  Keep this reason  ');
  if (spec.action === 'reactivate' || spec.action === 'assign') change(modal.fields.find((node) => node.type === 'select'), '3');
  modal.flush();
  return { env, modal, start, get closed() { return closed; } };
}
function operationalCloseButtons(modal) {
  return modal.controls.filter((node) => node.props['aria-label'] === 'close' || ['confirmCancel', 'boxInventoryBatchCloseReport'].includes(text(node)));
}
function assertOperationalContainment(h) {
  const { env, modal } = h;
  const controls = modal.dialog.querySelectorAll(focusableSelector);
  if (controls.length) {
    controls.at(-1).focus();
    assert.equal(env.key('Tab').defaultPrevented, true);
    assert.equal(env.document.activeElement, controls[0]);
    assert.equal(env.key('Tab', true).defaultPrevented, true);
    assert.equal(env.document.activeElement, controls.at(-1));
    for (let step = 0; step < controls.length * 2; step++) {
      env.key('Tab');
      assert.ok(modal.dialog.contains(env.document.activeElement), 'Background must never enter the modal tab cycle');
    }
  } else {
    for (const reverse of [false, true]) {
      assert.equal(env.key('Tab', reverse).defaultPrevented, true);
      assert.equal(env.document.activeElement, modal.dialog);
    }
  }
  for (const reverse of [false, true]) {
    env.opener.focus();
    assert.equal(env.key('Tab', reverse).defaultPrevented, true);
    assert.ok(modal.dialog.contains(env.document.activeElement));
  }
}
async function settleOperational() {
  for (let tick = 0; tick < 12; tick++) await Promise.resolve();
}
for (const spec of operationalCases) {
  const label = `${spec.name} (${spec.action})`;
  test(`${label}: labelled initial focus, Tab confinement, idle Escape and connected opener restoration`, () => {
    const h = operationalFixture(spec);
    const { env, modal } = h;
    const title = modal.nodes.find((node) => node.props.id === modal.dialog.props['aria-labelledby']);
    assert.ok(title && text(title));
    assert.equal(modal.dialog.props['aria-modal'], 'true');
    assert.equal(env.document.activeElement, operationalCloseButtons(modal)[0]);
    assertOperationalContainment(h);
    assert.equal(env.key('Escape').defaultPrevented, true);
    assert.equal(h.closed, 1);
    modal.unmount();
    assert.equal(env.document.activeElement, env.opener);
    assert.equal(env.listenerCount(), 0);
  });

  test(`${label}: idle close/backdrop dismiss and inside mousedown does not dismiss`, () => {
    const h = operationalFixture(spec);
    let stopped = false;
    h.modal.dialog.props.onMouseDown({ stopPropagation() { stopped = true; } });
    assert.ok(stopped);
    assert.equal(h.closed, 0);
    h.modal.backdrop.props.onMouseDown();
    operationalCloseButtons(h.modal).forEach((node) => node.props.onClick());
    assert.equal(h.closed, 3);
    h.modal.unmount();
  });

  test(`${label}: detached opener is not focused and removed modal never keeps focus`, () => {
    const { env, modal } = operationalFixture(spec);
    env.opener.isConnected = false;
    const calls = env.opener.focusCalls;
    modal.unmount();
    assert.equal(env.opener.focusCalls, calls);
    assert.equal(env.document.activeElement, env.document.body);
    assert.equal(env.listenerCount(), 0);
  });

  test(`${label}: externally saving blocks Escape, backdrop and close controls`, () => {
    const h = operationalFixture(spec, { isSaving: true });
    assert.equal(h.modal.dialog.props['aria-busy'], true);
    assert.equal(h.env.document.activeElement, h.modal.dialog);
    assert.ok(operationalCloseButtons(h.modal).every((node) => node.disabled));
    if (spec.name === 'BoxLifecycleModal') assert.ok(h.modal.controls.every((node) => node.disabled), 'Saving locks all lifecycle draft controls');
    h.modal.backdrop.props.onMouseDown();
    operationalCloseButtons(h.modal).forEach((node) => node.props.onClick());
    assert.equal(h.env.key('Escape').defaultPrevented, true);
    assert.equal(h.closed, 0);
    assertOperationalContainment(h);
    h.modal.setProps({ isSaving: false });
    assert.ok(h.modal.dialog.contains(h.env.document.activeElement), 'Focus stays inside when saving ends');
    assert.ok(operationalCloseButtons(h.modal).every((node) => !node.disabled));
    h.env.key('Escape');
    assert.equal(h.closed, 1);
    h.modal.unmount();
  });

  test(`${label}: pending mutation blocks stale dismissal/duplicates; failure retains usable draft and permits retry`, async () => {
    const attempts = [], payloads = [];
    let h;
    const operation = async (payload) => {
      payloads.push(payload == null ? null : plain(payload));
      const attempt = deferred();
      attempts.push(attempt);
      try { await attempt.promise; }
      catch (error) { h.modal.setProps({ error: error.message }); }
    };
    h = operationalFixture(spec, { onSubmit: operation, onConfirm: operation });
    const before = draftValues(h.modal);
    const staleClose = operationalCloseButtons(h.modal).map((node) => node.props.onClick);
    const staleBackdrop = h.modal.backdrop.props.onMouseDown;
    const start = h.start;
    const pending = start();
    start();
    staleClose.forEach((close) => close());
    staleBackdrop();
    h.env.key('Escape');
    assert.equal(h.closed, 0);
    assert.equal(attempts.length, 1);
    h.modal.flush();
    assert.equal(h.modal.dialog.props['aria-busy'], true);
    assert.equal(h.env.document.activeElement, h.modal.dialog);
    assertOperationalContainment(h);
    attempts[0].reject(new Error('Test mutation failed'));
    await pending;
    await settleOperational();
    h.modal.flush();
    assert.equal(h.modal.dialog.props['aria-busy'], false);
    assert.deepEqual(draftValues(h.modal), before);
    assert.ok(h.modal.nodes.some((node) => text(node) === 'Test mutation failed'));
    assert.equal(h.env.document.activeElement, operationalCloseButtons(h.modal)[0]);
    assertOperationalContainment(h);
    const retry = h.start();
    assert.equal(attempts.length, 2);
    assert.deepEqual(payloads[1], payloads[0]);
    attempts[1].resolve();
    await retry;
    await settleOperational();
    h.modal.flush();
    assert.equal(h.closed, 0);
    h.env.key('Escape');
    assert.equal(h.closed, 1);
    h.modal.unmount();
    assert.equal(h.env.document.activeElement, h.env.opener);
  });

  test(`${label}: successful mutation unmount restores connected focus before promise settlement`, async () => {
    const attempt = deferred();
    let h;
    const operation = async () => { await attempt.promise; h.modal.unmount(); };
    h = operationalFixture(spec, { onSubmit: operation, onConfirm: operation });
    const pending = h.start();
    h.modal.flush();
    attempt.resolve();
    await pending;
    await settleOperational();
    assert.equal(h.env.document.activeElement, h.env.opener);
    assert.equal(h.env.document.activeElement.isConnected, true);
    assert.equal(h.env.listenerCount(), 0);
  });
}

test('BoxInventoryBatchModal: success report retains focus, traps Tab and restores opener when closed', async () => {
  const h = operationalFixture({ name: 'BoxInventoryBatchModal', action: 'active' });
  const attempt = deferred();
  h.modal.setProps({ onConfirm: () => attempt.promise });
  h.start();
  h.modal.flush();
  h.modal.setProps({
    result: { success_count: 1, failure_count: 0, active_with_location_count: 0, active_without_location_count: 1, successes: [{ box_id: 17, global_code: '1-ATL.001' }], failures: [] },
    isSaving: true,
  });
  assert.ok(operationalCloseButtons(h.modal).every((node) => node.disabled));
  operationalCloseButtons(h.modal).forEach((node) => node.props.onClick());
  h.modal.backdrop.props.onMouseDown();
  h.env.key('Escape');
  assert.equal(h.closed, 0);
  assertOperationalContainment(h);
  attempt.resolve();
  await settleOperational();
  h.modal.setProps({ isSaving: false });
  assert.equal(h.env.document.activeElement, operationalCloseButtons(h.modal)[0]);
  assertOperationalContainment(h);
  operationalCloseButtons(h.modal).at(-1).props.onClick();
  assert.equal(h.closed, 1);
  h.modal.unmount();
  assert.equal(h.env.document.activeElement, h.env.opener);
});

// Model native user interaction: disabled controls do not dispatch changes/clicks.
// Direct handler calls elsewhere intentionally test stale dismissal guards instead.
function interactLifecycle(node, value) {
  if (node.disabled) return false;
  if (node.props.onChange) node.props.onChange({ target: { value, checked: value } });
  else node.props.onClick();
  return true;
}
function lifecycleDraftControls(modal) {
  return [...modal.fields, ...modal.controls.filter((node) => text(node) === 'boxLifecycleReuseLastLocation')];
}
function lifecycleDraft(modal) {
  return modal.fields.map((node) => ({ type: node.props.type ?? node.type, value: node.props.value, checked: node.props.checked }));
}
function editLifecycle(h, type, value) {
  const node = h.modal.fields.find((field) => (field.props.type ?? field.type) === type);
  assert.ok(node, `Missing lifecycle ${type}`);
  assert.equal(interactLifecycle(node, value), true, `${type} must be editable`);
  h.modal.flush();
}
const lifecycleDraftCases = [
  { action: 'qualify', variant: 'active', setup(h) { editLifecycle(h, 'select', '3'); }, edit(h) { editLifecycle(h, 'select', '4'); }, payload: { target_status: 'active', thermal_zone_id: 3 }, editedPayload: { target_status: 'active', thermal_zone_id: 4 } },
  { action: 'qualify', variant: 'inactive reason', setup(h) { editLifecycle(h, 'textarea', '  Draft A reason  '); }, edit(h) { editLifecycle(h, 'checkbox', true); }, payload: { target_status: 'inactive', reason: 'Draft A reason', reason_missing_from_history: false }, editedPayload: { target_status: 'inactive', reason: '', reason_missing_from_history: true } },
  { action: 'qualify', variant: 'inactive historical reason', setup(h) { editLifecycle(h, 'checkbox', true); }, edit(h) { editLifecycle(h, 'checkbox', false); editLifecycle(h, 'textarea', 'Draft B reason'); }, payload: { target_status: 'inactive', reason: '', reason_missing_from_history: true }, editedPayload: { target_status: 'inactive', reason: 'Draft B reason', reason_missing_from_history: false } },
  { action: 'qualify', variant: 'status change', setup(h) { editLifecycle(h, 'select', '3'); }, edit(h) { assert.equal(interactLifecycle(h.modal.fields.find((node) => node.props.value === 'inactive'), true), true); h.modal.flush(); editLifecycle(h, 'textarea', 'Draft B reason'); }, payload: { target_status: 'active', thermal_zone_id: 3 }, editedPayload: { target_status: 'inactive', reason: 'Draft B reason', reason_missing_from_history: false } },
  { action: 'deactivate', variant: 'reason', setup(h) { editLifecycle(h, 'textarea', '  Draft A reason  '); }, edit(h) { editLifecycle(h, 'textarea', 'Draft B reason'); }, payload: { reason: 'Draft A reason' }, editedPayload: { reason: 'Draft B reason' } },
  ...['reactivate', 'assign'].map((action) => ({ action, variant: 'zone and notes', setup(h) { editLifecycle(h, 'textarea', '  Draft A notes  '); }, edit(h) { editLifecycle(h, 'select', '4'); editLifecycle(h, 'textarea', 'Draft B notes'); }, payload: { thermal_zone_id: 3, notes: 'Draft A notes' }, editedPayload: { thermal_zone_id: 4, notes: 'Draft B notes' } })),
  { action: 'reactivate', variant: 'reuse last zone', setup(h) { editLifecycle(h, 'textarea', '  Draft A notes  '); }, edit(h) { assert.equal(interactLifecycle(lifecycleDraftControls(h.modal).find((node) => text(node) === 'boxLifecycleReuseLastLocation')), true); h.modal.flush(); }, payload: { thermal_zone_id: 3, notes: 'Draft A notes' }, editedPayload: { thermal_zone_id: 4, notes: 'Draft A notes' } },
];
for (const spec of lifecycleDraftCases) {
  test(`BoxLifecycleModal (${spec.action}, ${spec.variant}): freezes A in flight, retries A after failure, allows deliberate B and restores success focus`, async () => {
    const attempts = [], payloads = [];
    let h;
    const operation = async (submission) => {
      payloads.push(plain(submission));
      const attempt = deferred();
      attempts.push(attempt);
      try {
        await attempt.promise;
        h.modal.unmount();
      } catch (error) {
        h.modal.setProps({ isSaving: false, error: error.message });
      }
    };
    h = operationalFixture({ name: 'BoxLifecycleModal', action: spec.action }, {
      initialTargetStatus: spec.variant.startsWith('inactive') ? 'inactive' : 'active',
      zones: [3, 4].map((id) => ({ id, name: `Zone ${id}`, is_active: true })),
      box: { id: 17, global_code: '1-ATL.001', status: 'pending_review', species: { scientific_name: 'Test species' }, thermal_zone: null, last_location: { thermal_zone: { id: 4, name: 'Zone 4' }, starts_at: '2026-01-01' } },
      onSubmit: operation,
    });
    spec.setup(h);
    const draftA = lifecycleDraft(h.modal);
    const disabledBefore = lifecycleDraftControls(h.modal).map((node) => node.disabled);
    const focusedField = h.modal.fields.find((node) => !node.disabled);
    focusedField.focus();
    assert.equal(h.env.document.activeElement, focusedField);
    const pending = h.start();
    assert.deepEqual(payloads[0], { action: spec.action, payload: spec.payload });
    h.modal.flush();
    assert.equal(h.modal.dialog.props['aria-busy'], true, 'Hook locks draft before parent isSaving changes');
    assert.equal(h.env.document.activeElement, h.modal.dialog, 'Disabling the focused field transfers focus to the dialog');
    assert.ok(h.modal.controls.every((node) => node.disabled));
    for (const node of lifecycleDraftControls(h.modal)) {
      assert.equal(interactLifecycle(node, node.type === 'select' ? '4' : node.props.type === 'checkbox' || node.props.type === 'radio' ? !node.props.checked : 'Draft B'), false);
    }
    h.modal.flush();
    assert.deepEqual(lifecycleDraft(h.modal), draftA);
    assert.deepEqual(payloads[0], { action: spec.action, payload: spec.payload });
    await h.start();
    assert.equal(attempts.length, 1);
    h.modal.setProps({ isSaving: true });
    assert.ok(h.modal.controls.every((node) => node.disabled));
    h.modal.backdrop.props.onMouseDown();
    operationalCloseButtons(h.modal).forEach((node) => node.props.onClick());
    h.env.key('Escape');
    assert.equal(h.closed, 0);
    assertOperationalContainment(h);
    attempts[0].reject(new Error('Request A failed'));
    await pending;
    h.modal.flush();
    assert.deepEqual(lifecycleDraft(h.modal), draftA);
    assert.deepEqual(lifecycleDraftControls(h.modal).map((node) => node.disabled), disabledBefore);
    assert.ok(h.modal.nodes.some((node) => text(node) === 'Request A failed'));
    assert.equal(h.env.document.activeElement, operationalCloseButtons(h.modal)[0]);
    const retry = h.start();
    assert.deepEqual(payloads[1], payloads[0]);
    h.modal.flush();
    attempts[1].reject(new Error('Retry A failed'));
    await retry;
    h.modal.flush();
    assert.deepEqual(lifecycleDraft(h.modal), draftA);
    spec.edit(h);
    assert.notDeepEqual(lifecycleDraft(h.modal), draftA);
    const editedSubmission = h.start();
    assert.deepEqual(payloads[2], { action: spec.action, payload: spec.editedPayload });
    h.modal.flush();
    attempts[2].resolve();
    await editedSubmission;
    assert.equal(h.env.document.activeElement, h.env.opener);
    assert.equal(h.env.document.activeElement.isConnected, true);
    assert.equal(h.env.listenerCount(), 0);
  });
}

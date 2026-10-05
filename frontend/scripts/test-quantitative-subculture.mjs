import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const read = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
const plain = (value) => JSON.parse(JSON.stringify(value));
const text = (node) => Array.isArray(node) ? node.map(text).join('')
  : node == null || typeof node === 'boolean' ? '' : typeof node === 'object' ? text(node.props?.children) : String(node);
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const hasClass = (node, name) => node.props.className?.split(/\s+/).includes(name);
const organization = { id: 1, name: 'Isolated laboratory' };
const box = {
  id: 17, global_code: 'LEGACY-PARENT', species: { scientific_name: 'Aurelia aurita' },
  strain: { code: '1-ATL' }, organization, thermal_zone: { id: 2, name: 'Parent zone' },
  current_polyp_state: { polyp_count: 12, revision: 'opaque-revision:not-a-code', source: { kind: 'subculture', id: 8 } },
  latest_measurement: { polyp_count: 999 },
};

// Local VM/DOM harness adapted from the existing mutation-dialog tests. Runs the
// actual hook and modal handlers; it does not simulate browser layout or number input sanitization.
function harness(overrides = {}, previewCodes = []) {
  const slots = [], effects = [], listeners = new Set();
  let cursor = 0, dirty = false, nodes = [], elements = new Map(), mounted = true;
  const document = { activeElement: null, querySelectorAll: () => nodes.filter((node) => node.props.role === 'dialog') };
  class Element {
    constructor(type) { this.type = type; this.isConnected = true; }
    focus() { if (this.isConnected && !this.props.disabled) document.activeElement = this; }
    contains(other) { while (other) { if (other === this) return true; other = other.parent; } return false; }
    querySelectorAll() {
      return nodes.filter((node) => node !== this && this.contains(node)
        && ((!node.props.disabled && ['button', 'input', 'select', 'textarea'].includes(node.type)) || node.props.tabIndex === 0));
    }
    querySelector(selector) {
      const key = selector.match(/^\[data-allocation-key="(\d+)"\]$/)?.[1];
      assert.ok(key, `Unexpected selector: ${selector}`);
      return nodes.find((node) => this.contains(node) && String(node.props['data-allocation-key']) === key);
    }
  }
  const opener = new Element('button'); opener.props = {}; opener.focus();
  const hooks = {
    useState(initial) {
      const i = cursor++;
      if (!(i in slots)) slots[i] = { value: typeof initial === 'function' ? initial() : initial };
      return [slots[i].value, (next) => {
        const value = typeof next === 'function' ? next(slots[i].value) : next;
        if (!Object.is(value, slots[i].value)) { slots[i].value = value; dirty = true; }
      }];
    },
    useRef(initial) { const i = cursor++; return slots[i] ??= { current: initial }; },
    useMemo(fn, deps) {
      const i = cursor++;
      if (!slots[i] || deps.some((value, j) => !Object.is(value, slots[i].deps[j]))) slots[i] = { value: fn(), deps };
      return slots[i].value;
    },
    useEffect: effect, useLayoutEffect: effect,
  };
  function effect(fn, deps) {
    const i = cursor++;
    if (!deps || !slots[i] || deps.some((value, j) => !Object.is(value, slots[i].deps[j]))) {
      effects.push(() => { slots[i]?.cleanup?.(); slots[i] = { deps, cleanup: fn() }; });
    }
  }
  const modules = new Map();
  function load(path) {
    if (modules.has(path)) return modules.get(path);
    const exports = {}; modules.set(path, exports);
    const code = ts.transpileModule(read(path), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
    } }).outputText;
    const jsx = (type, props, key) => ({ type, props, key });
    vm.runInNewContext(code, {
      exports, document, HTMLElement: Element,
      window: { addEventListener(name, fn) { assert.equal(name, 'keydown'); listeners.add(fn); }, removeEventListener(name, fn) { listeners.delete(fn); } },
      require(name) {
        if (name === 'react') return hooks;
        if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
        if (name === '../hooks/useMutationDialog') return load('hooks/useMutationDialog.ts');
                if (name === '../hooks/useSubcultureCodePreview') return { default: () => previewCodes };
        if (name === '../utils/subculture') return load('utils/subculture.ts');
        if (name === '../i18n') return load('i18n/index.ts');
        if (name === './fr' || name === './en') return load(`i18n/${name.slice(2)}.ts`);
        if (name === './ModalPortal' || name === './PolypbaseIcon') return { default: name };
        if (name === './quantitative-subculture.css') return {};
        throw new Error(`Unexpected import: ${name}`);
      },
    });
    return exports;
  }
  const Modal = load('components/SubcultureModal.tsx').default;
  let props = {
    box, zones: [2, 3].map((id) => ({ id, name: `Zone ${id}`, organization, is_active: true })),
    language: 'en', isSaving: false, error: null, onClose() {}, onSubmit: async () => {}, ...overrides,
  };
  function render() {
    assert.ok(mounted); cursor = 0; dirty = false;
    const tree = Modal(props), next = new Map(); nodes = [];
    function visit(node, parent = null, path = 'root') {
      if (Array.isArray(node)) return node.forEach((child, i) => visit(child, parent, `${path}/${child?.key ?? i}`));
      if (!node || typeof node !== 'object') return;
      const element = elements.get(path)?.type === node.type ? elements.get(path) : new Element(node.type);
      element.props = node.props; element.parent = parent; element.isConnected = true;
      nodes.push(element); next.set(path, element);
      if (node.props.ref) node.props.ref.current = element;
      visit(node.props.children, element, `${path}/children`);
    }
    visit(tree);
    for (const [path, node] of elements) if (!next.has(path)) node.isConnected = false;
    elements = next;
    while (effects.length) effects.shift()();
  }
  function flush() { for (let i = 0; dirty; i++) { assert.ok(i < 20); render(); } }
  const api = {
    document, opener, get nodes() { return nodes; }, get props() { return props; },
    get rows() { return nodes.filter((node) => hasClass(node, 'quantitative-subculture-row')); },
    get submitButton() { return nodes.find((node) => node.type === 'button' && node.props.type === 'submit'); },
    byClass: (name) => nodes.find((node) => hasClass(node, name)),
    field: (name) => nodes.find((node) => node.props.name === name),
    change(name, value) { this.field(name).props.onChange({ target: { value } }); flush(); },
    add() { this.byClass('quantitative-subculture-add').props.children[0].props.onClick(); flush(); },
    remove(index) {
      nodes.find((node) => node.type === 'button' && this.rows[index].contains(node)).props.onClick(); flush();
    },
    submit() { const pending = nodes.find((node) => node.type === 'form').props.onSubmit({ preventDefault() {} }); flush(); return pending; },
    setProps(next) { props = { ...props, ...next }; render(); flush(); }, flush,
    key(key, shiftKey = false) {
      const event = { key, shiftKey, prevented: false, preventDefault() { this.prevented = true; } };
      listeners.forEach((fn) => fn(event)); return event;
    },
    unmount() { slots.forEach((slot) => slot?.cleanup?.()); mounted = false; assert.equal(listeners.size, 0); },
  };
  render(); flush(); return api;
}

for (const language of ['fr', 'en']) {
  test(`${language}: compact parent identity and honest advisory child identity`, () => {
    const h = harness({ language });
    assert.equal(text(h.nodes.find((node) => node.type === 'h2')), language === 'fr' ? 'Repiquer' : 'Subculture');
    const identity = text(h.byClass('quantitative-subculture-parent'));
    assert.equal(identity, `LEGACY-PARENTAurelia aurita${language === 'fr' ? 'Disponible12 Polypes' : 'Available12 Polyps'}`);
        assert.equal(h.byClass('quantitative-subculture-parent').parent, h.byClass('quantitative-subculture-body'));
        assert.ok(h.byClass('quantitative-subculture-parent').contains(h.byClass('quantitative-subculture-summary')));
    assert.doesNotMatch(identity, /1-ATL/);
    assert.equal(h.nodes.filter((node) => node.type === 'strong' && text(node) === box.global_code).length, 1);
    assert.equal(text(h.nodes.find((node) => node.type === 'h4')), language === 'fr' ? 'Boîte 1 : —' : 'Box 1 : —');
    assert.equal(h.nodes.filter((node) => node.type === 'h3').length, 0);
    assert.equal(h.nodes.filter((node) => hasClass(node, 'quantitative-subculture-help')).length, 0);
    assert.doesNotMatch(text(h.rows[0]), /Code attribué à la création|Code assigned on creation/);
    assert.equal(h.byClass('quantitative-subculture-creation-count'), undefined);
    assert.equal(h.byClass('box-dialog-context'), undefined);
    assert.ok(h.nodes.filter((node) => node.type === 'input').every((node) => !['date', 'datetime-local'].includes(node.props.type)));
    assert.ok(h.nodes.filter((node) => ['input', 'select'].includes(node.type)).every((node) => !/global_code|box_number|local_code/.test(node.props.name)));
    assert.equal(h.nodes.filter((node) => node.type === 'details').length, 0);
    assert.equal(h.byClass('quantitative-subculture-add').parent, h.byClass('quantitative-subculture-children'));
    assert.equal(h.field('children.0.allocated_polyps').props.required, undefined);
        assert.equal(text(h.field('children.0.allocated_polyps').parent.props.children[0]), language === 'fr' ? 'Polypes à allouer (facultatif)' : 'Polyps to allocate (optional)');
    assert.equal(h.field('reason'), undefined);
        assert.equal(h.field('children.0.notes'), undefined);
        assert.equal(h.nodes.filter(node => node.type === 'textarea').length, 1);
        assert.equal(h.field('notes').props.required, undefined);
        assert.equal(text(h.field('notes').parent.props.children[0]), language === 'fr' ? 'Note du repiquage (facultatif)' : 'Subculture note (optional)');
    h.unmount();
  });
}

for (const language of ['fr', 'en']) {
  test(`${language}: server preview is display-only and footer contains no creation count`, async () => {
    const payloads = [];
    const codes = ['SERVER-PREVIEW-A', 'SERVER-PREVIEW-B'];
    const h = harness({ language, onSubmit: async payload => payloads.push(plain(payload)) }, codes);
    const label = language === 'fr' ? 'Boîte' : 'Box';
    assert.equal(text(h.nodes.find(node => node.type === 'h4')), `${label} 1 : SERVER-PREVIEW-A`);
    h.change('children.0.allocated_polyps', '0'); h.add();
    assert.deepEqual(h.nodes.filter(node => node.type === 'h4').map(text), [`${label} 1 : SERVER-PREVIEW-A`, `${label} 2 : SERVER-PREVIEW-B`]);
    assert.equal(h.byClass('quantitative-subculture-creation-count'), undefined);
    assert.equal(h.field('children.1.allocated_polyps').props.required, undefined);
    assert.equal(h.submitButton.props.disabled, false);
    await h.submit(); h.flush();
    assert.deepEqual(payloads, [{ expected_current_state_revision: 'opaque-revision:not-a-code', reason: '', notes: '', children: [
      { thermal_zone_id: 2, allocated_polyps: 0, copy_origin: true, notes: '' },
      { thermal_zone_id: 2, allocated_polyps: null, copy_origin: true, notes: '' },
    ] }]);
    assert.doesNotMatch(JSON.stringify(payloads), /SERVER-PREVIEW|global_code|box_number|local_code/);
    h.unmount();
  });
}

for (const counts of [[''], ['', ''], ['0', ''], ['30', '']]) {
  test(`optional allocations ${JSON.stringify(counts)} submit null without fabricating totals`, async () => {
    const payloads = [];
    const h = harness({ box: { ...box, current_polyp_state: { ...box.current_polyp_state, polyp_count: 50 } }, onSubmit: async payload => payloads.push(plain(payload)) });
    counts.forEach((count, index) => {
      if (index) h.add();
      h.change(`children.${index}.allocated_polyps`, count);
    });
    assert.equal(h.submitButton.props.disabled, false);
    assert.deepEqual(h.nodes.filter(node => node.type === 'dd').map(text), ['50 Polyps']);
    await h.submit(); h.flush();
    assert.deepEqual(payloads[0].children.map(child => child.allocated_polyps), counts.map(count => count === '' ? null : Number(count)));
    assert.equal(h.nodes.some(node => node.type === 'p'), false);
    h.unmount();
  });
}

test('known zero submits an explicit zero with the exact backend payload', async () => {
  const payloads = [];
  const h = harness({ box: { ...box, current_polyp_state: { ...box.current_polyp_state, polyp_count: 0 } }, onSubmit: async (payload) => payloads.push(plain(payload)) });
  h.change('children.0.allocated_polyps', '0');
  h.change('notes', '  Global note  ');
  assert.deepEqual(h.nodes.filter((node) => node.type === 'dd').map(text), ['0 Polyps', '0', '0']);
  assert.equal(h.submitButton.props.disabled, false);
  await h.submit(); h.flush();
  assert.deepEqual(payloads, [{ expected_current_state_revision: 'opaque-revision:not-a-code', reason: '', notes: 'Global note', children: [{ thermal_zone_id: 2, allocated_polyps: 0, copy_origin: true, notes: '' }] }]);
  h.unmount();
});

for (const current of [null, { ...box.current_polyp_state, polyp_count: null }, undefined]) {
  test(`unknown/missing current state (${JSON.stringify(current)}) never falls back to latest measurement`, async () => {
    let calls = 0;
    const h = harness({ box: { ...box, current_polyp_state: current }, onSubmit: async () => calls++ });
    h.change('children.0.allocated_polyps', '0');
    assert.deepEqual(h.nodes.filter((node) => node.type === 'dd').map(text), ['Unknown']);
    assert.equal(h.submitButton.props.disabled, true);
    await h.submit(); assert.equal(calls, 0); h.unmount();
  });
}

for (const value of ['-1', '0.5', '1e1', '2147483648', 'NaN', ' ']) {
  test(`invalid draft ${JSON.stringify(value)} is blocked by the submit handler as well as UI`, async () => {
    let calls = 0;
    const h = harness({ onSubmit: async () => calls++ });
    h.change('children.0.allocated_polyps', value);
    assert.equal(h.submitButton.props.disabled, true);
    assert.equal(h.field('children.0.allocated_polyps').props['aria-invalid'], true);
    await h.submit(); assert.equal(calls, 0); h.unmount();
  });
}

test('exact allocation totals block over-allocation and allow correction or removal', async () => {
  const payloads = [];
  const h = harness({ onSubmit: async (payload) => payloads.push(plain(payload)) });
  h.change('children.0.allocated_polyps', '8'); h.add(); h.change('children.1.allocated_polyps', '5');
  assert.deepEqual(h.nodes.filter((node) => node.type === 'dd').map(text), ['12 Polyps', '13', '-1']);
  assert.equal(h.submitButton.props.disabled, true);
  await h.submit(); assert.equal(payloads.length, 0);
  h.change('children.1.allocated_polyps', '4');
  assert.deepEqual(h.nodes.filter((node) => node.type === 'dd').map(text), ['12 Polyps', '12', '0']);
  assert.equal(h.submitButton.props.disabled, false);
  h.remove(0);
  assert.deepEqual(h.nodes.filter((node) => node.type === 'dd').map(text), ['12 Polyps', '4', '8']);
  await h.submit(); h.flush(); assert.equal(payloads[0].children[0].allocated_polyps, 4); h.unmount();
});

test('known partial allocations exceeding available stock are rejected even with blanks', async () => {
  let calls = 0;
  const h = harness({ onSubmit: async () => calls++ });
  h.change('children.0.allocated_polyps', '13'); h.add();
  assert.deepEqual(h.nodes.filter(node => node.type === 'dd').map(text), ['12 Polyps']);
  assert.equal(h.submitButton.props.disabled, true);
  await h.submit(); assert.equal(calls, 0);
  h.change('children.0.allocated_polyps', '12');
  assert.equal(h.submitButton.props.disabled, false);
  await h.submit(); assert.equal(calls, 1);
  h.unmount();
});

test('all children remain expanded, allocations are ordered and the optional global note is not copied', async () => {
  const payloads = [];
  const h = harness({ onSubmit: async (payload) => payloads.push(plain(payload)) });
  h.change('children.0.allocated_polyps', '1'); h.add(); h.change('children.1.allocated_polyps', '2');
  h.change('children.1.thermal_zone_id', '3'); h.change('notes', '  Global note  ');
  assert.equal(h.rows.length, 2);
  assert.equal(h.nodes.filter((node) => node.type === 'input' && node.props.type === 'number').length, 2);
  await h.submit(); h.flush();
  assert.deepEqual(payloads[0].children, [
    { thermal_zone_id: 2, allocated_polyps: 1, copy_origin: true, notes: '' },
    { thermal_zone_id: 3, allocated_polyps: 2, copy_origin: true, notes: '' },
  ]);
  assert.equal(payloads[0].reason, ''); assert.equal(payloads[0].notes, 'Global note'); h.unmount();
});

test('add/remove transfer focus to a surviving allocation input and preserve row drafts', () => {
  const h = harness();
  h.change('children.0.allocated_polyps', '1'); h.add();
  assert.equal(h.document.activeElement, h.field('children.1.allocated_polyps'));
  h.change('children.1.allocated_polyps', '2'); h.change('notes', 'Keep global note'); h.add();
  assert.equal(h.document.activeElement, h.field('children.2.allocated_polyps'));
  h.change('children.2.allocated_polyps', '3');
  h.remove(1);
  assert.equal(h.document.activeElement, h.field('children.1.allocated_polyps'));
  assert.equal(h.field('children.1.allocated_polyps').props.value, '3');
  assert.equal(h.field('children.0.allocated_polyps').props.value, '1');
    assert.equal(h.field('notes').props.value, 'Keep global note');
  h.remove(1);
  assert.equal(h.document.activeElement, h.field('children.0.allocated_polyps'));
  assert.equal(h.nodes.filter((node) => node.type === 'button' && h.rows[0].contains(node)).length, 0);
  h.unmount(); assert.equal(h.document.activeElement, h.opener);
});

test('20 child limit guards the handler; removal permits another child with a unique key', () => {
  const h = harness();
  for (let i = 1; i < 20; i++) h.add();
  const keys = h.nodes.filter((node) => node.props['data-allocation-key']).map((node) => node.props['data-allocation-key']);
  assert.equal(h.rows.length, 20); assert.equal(new Set(keys).size, 20);
  const add = h.nodes.find((node) => node.type === 'button' && h.byClass('quantitative-subculture-add').contains(node));
  assert.equal(add.props.disabled, true);
  h.add(); assert.equal(h.rows.length, 20);
  h.remove(10); h.add(); assert.equal(h.rows.length, 20);
  assert.equal(h.field('children.19.allocated_polyps').props['data-allocation-key'], 21);
  h.unmount();
});

test('zone choices include only active zones in the parent institution, and stale choices cannot submit', async () => {
  let calls = 0;
  const allowed = { id: 3, name: 'Allowed', organization, is_active: true };
  const h = harness({ zones: [allowed,
    { id: 2, name: 'Inactive', organization, is_active: false },
    { id: 4, name: 'Foreign', organization: { id: 2 }, is_active: true },
  ], onSubmit: async () => calls++ });
  assert.deepEqual(h.nodes.filter((node) => node.type === 'option').map(text), ['—', 'Allowed']);
  assert.equal(h.field('children.0.thermal_zone_id').props.value, 3);
  h.change('children.0.allocated_polyps', '0');
  h.change('children.0.thermal_zone_id', '4');
  assert.equal(h.submitButton.props.disabled, true); await h.submit(); assert.equal(calls, 0);
  h.change('children.0.thermal_zone_id', '3');
  h.setProps({ zones: [{ ...allowed, id: 5 }] });
  assert.equal(h.field('children.0.thermal_zone_id').props.value, '');
  await h.submit(); assert.equal(calls, 0); h.unmount();
});

test('no active zone prevents submission without disabling cancellation', async () => {
  let calls = 0;
  const h = harness({ zones: [], onSubmit: async () => calls++ });
  h.change('children.0.allocated_polyps', '0');
  assert.equal(h.submitButton.props.disabled, true); await h.submit(); assert.equal(calls, 0);
  assert.equal(h.nodes.find((node) => hasClass(node, 'box-dialog-close')).props.disabled, false);
  assert.match(text(h.byClass('quantitative-subculture-help')), /No active thermal zone/); h.unmount();
});

test('refresh never silently changes the intent revision or automatically retries a stale draft', async () => {
  let calls = 0;
  const h = harness({ onSubmit: async () => calls++ });
  h.change('children.0.allocated_polyps', '2'); h.change('notes', 'Preserve draft');
  h.setProps({ box: { ...box, current_polyp_state: { ...box.current_polyp_state, revision: 'new-opaque-token', polyp_count: 10 } } });
  assert.deepEqual(h.nodes.filter((node) => node.type === 'dd').map(text), ['10 Polyps', '2', '8']);
  assert.equal(h.submitButton.props.disabled, true);
  assert.equal(h.field('notes').props.value, 'Preserve draft');
  assert.match(text(h.nodes.find((node) => node.props.id === 'subculture-validation')), /Review the refreshed quantities/);
  await h.submit(); assert.equal(calls, 0);
  const review = h.nodes.find((node) => node.type === 'button' && text(node) === 'Use this refreshed state');
  assert.ok(review);
  review.props.onClick(); h.flush();
  assert.equal(h.submitButton.props.disabled, false);
  assert.equal(h.field('notes').props.value, 'Preserve draft');
  assert.equal(h.field('children.0.allocated_polyps').props.value, '2');
  assert.equal(calls, 0, 'explicit review never submits automatically');
  await h.submit(); assert.equal(calls, 1); h.unmount();
});

test('pending confirmation blocks duplicates, editing, dismissal and add/remove; rejection preserves the draft', async () => {
  const attempt = deferred(), payloads = []; let closed = 0;
  const h = harness({ onClose: () => closed++, onSubmit: (payload) => { payloads.push(plain(payload)); return attempt.promise; } });
  h.change('children.0.allocated_polyps', '0'); h.change('notes', 'Draft global note');
    h.field('notes').focus();
    assert.equal(h.document.activeElement, h.field('notes'));
  const staleClose = h.nodes.find((node) => hasClass(node, 'box-dialog-close')).props.onClick;
  const staleBackdrop = h.byClass('quantitative-subculture-backdrop').props.onClick;
  const pending = h.submit(); await h.submit();
  assert.equal(payloads.length, 1);
  assert.equal(h.nodes.find((node) => node.props.role === 'dialog').props['aria-busy'], true);
  assert.ok(h.nodes.filter((node) => ['button', 'input', 'select', 'textarea'].includes(node.type)).every((node) => node.props.disabled));
  h.add(); assert.equal(h.rows.length, 1);
  staleClose(); staleBackdrop(); h.key('Escape'); assert.equal(closed, 0);
  attempt.reject(new Error('Backend rejected')); await assert.rejects(pending, /Backend rejected/); h.flush();
  h.setProps({ error: 'Conflict: reload parent' });
  assert.equal(h.field('notes').props.value, 'Draft global note');
  assert.equal(h.field('children.0.allocated_polyps').props.value, '0');
  assert.equal(h.document.activeElement, h.field('children.0.allocated_polyps'));
  assert.equal(payloads.length, 1, 'no automatic retry');
  assert.ok(h.byClass('quantitative-subculture-body').contains(h.nodes.find((node) => node.props.role === 'alert')));
  h.setProps({ onSubmit: async (payload) => payloads.push(plain(payload)) });
  await h.submit(); h.flush(); assert.deepEqual(payloads[1], payloads[0]);
  h.key('Escape'); assert.equal(closed, 1);
  h.unmount(); assert.equal(h.document.activeElement, h.opener);
});

test('saving prop freezes the form and keyboard focus wraps inside the modal', () => {
  let closed = 0;
  const h = harness({ onClose: () => closed++ });
  const first = h.nodes.find((node) => node.type === 'button');
  const last = h.nodes.filter((node) => node.type === 'button' && !node.props.disabled).at(-1);
  first.focus(); assert.equal(h.key('Tab', true).prevented, true); assert.equal(h.document.activeElement, last);
  assert.equal(h.key('Tab').prevented, true); assert.equal(h.document.activeElement, first);
  h.setProps({ isSaving: true });
  assert.ok(h.nodes.filter((node) => ['button', 'input', 'select', 'textarea'].includes(node.type)).every((node) => node.props.disabled));
  h.key('Escape'); assert.equal(closed, 0);
  h.setProps({ isSaving: false }); h.key('Escape'); assert.equal(closed, 1); h.unmount();
});

test('local stylesheet is scoped, keeps one body scroller and uses standard controls without card rows', () => {
  const source = read('components/SubcultureModal.tsx');
  const css = read('components/quantitative-subculture.css');
  assert.match(source, /import '\.\/quantitative-subculture\.css'/);
  assert.match(css, /\.box-dialog\.quantitative-subculture \.box-dialog-heading \{\s*align-items: center;/);
  assert.match(source, /className="primary-button"/);
  assert.match(source, /className="secondary-button(?: [^"]+)?"/);
  assert.doesNotMatch(source, /suggestChildIdentity|latest_measurement|event_date|occurred_at|initial_polyp_count|global_code:/);
  assert.equal((css.match(/overflow: auto/g) ?? []).length, 1);
  assert.match(css, /\.quantitative-subculture-body \{[^}]*overflow: auto/);
  assert.match(css, /\.quantitative-subculture-row \{[^}]*grid-template-columns: minmax\(0, 1fr\) minmax\(0, 1fr\);[^}]*grid-template-areas: "identity identity" "zone count"/);
    assert.match(css, /\.quantitative-subculture-row \+ \.quantitative-subculture-row \{[^}]*border-top: 1px solid var\(--color-line-soft\)/);
    for (const area of ['zone', 'count']) assert.match(css, new RegExp(`\\.quantitative-subculture-${area} \\{ grid-area: ${area};`));
  assert.doesNotMatch(css.match(/\.quantitative-subculture-row \{[^}]*\}/)[0], /border-radius|background:|border:/);
  assert.match(css, /:focus-visible \{\s*outline: 2px solid var\(--color-primary\)/);
  assert.match(css, /\.quantitative-subculture-summary dl \{[^}]*display: flex;[^}]*flex-wrap: wrap;[^}]*gap: var\(--space-2\) var\(--space-3\)/);
  assert.doesNotMatch(css.match(/\.quantitative-subculture-summary dl \{[^}]*\}/)[0], /background|border|padding/);
  assert.match(css, /\.quantitative-subculture :is\(input, select, textarea\) \{ font-size: 16px;/);
  assert.doesNotMatch(css, /!important|position: sticky|font-size: clamp|letter-spacing: -/);
  for (const line of css.split('\n').filter((line) => line.trim() && !line.startsWith(' ') && !line.startsWith('}') && !line.startsWith('@'))) {
    assert.match(line, /quantitative-subculture/, `Unscoped local selector: ${line}`);
  }
});

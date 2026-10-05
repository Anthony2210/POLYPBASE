import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { appHarness, ast, functionNode, tick } from './app-operation-test-harness.mjs';

const read = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
const text = (node) => Array.isArray(node) ? node.map(text).join('')
  : node == null || typeof node === 'boolean' ? '' : typeof node === 'object' ? text(node.props?.children) : String(node);
const plain = (value) => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

// A local hook harness exercises the real safe modal hook without editing shared tests.
function harness(overrides = {}) {
  const slots = [], effects = [], cleanups = [], listeners = new Set();
  let cursor = 0, dirty = false, tree, nodes = [], props, mounted = true;
  const document = { activeElement: null, querySelectorAll: () => nodes.filter((node) => node.props.role === 'dialog') };
  const opener = { isConnected: true, focus() { document.activeElement = this; } };
  opener.focus();
  const hooks = {
    useState(initial) {
      const i = cursor++;
      if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
      return [slots[i], (next) => { slots[i] = typeof next === 'function' ? next(slots[i]) : next; dirty = true; }];
    },
    useRef(initial) { const i = cursor++; return slots[i] ??= { current: initial }; },
    useMemo(fn) { return fn(); },
    useEffect: effect, useLayoutEffect: effect,
  };
  function effect(fn, deps) {
    const i = cursor++;
    if (!deps || !slots[i] || deps.some((dep, j) => dep !== slots[i][j])) {
      slots[i] = deps;
      effects.push(() => { cleanups[i]?.(); cleanups[i] = fn(); });
    }
  }
  const jsx = (type, props) => ({ type, props });
  function load(path, imports = {}) {
    const exports = {};
    const code = ts.transpileModule(read(path), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
    } }).outputText;
    vm.runInNewContext(code, {
      exports, document, HTMLElement: Object,
      window: { addEventListener(name, fn) { listeners.add(fn); }, removeEventListener(name, fn) { listeners.delete(fn); } },
      require(name) {
        if (name === 'react') return hooks;
        if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx };
        assert.ok(name in imports, `Unexpected import ${name}`);
        return imports[name];
      },
    });
    return exports;
  }
  const catalogues = Object.fromEntries(['fr', 'en'].map(language => [language, load(`i18n/${language}.ts`)[language]]));
  const i18n = load('i18n/index.ts', Object.fromEntries(['fr', 'en'].map(language => [`./${language}`, { [language]: catalogues[language] }])));
  const useMutationDialog = load('hooks/useMutationDialog.ts').default;
  const Modal = load('components/MoveBoxModal.tsx', {
    '../i18n': i18n,
    '../hooks/useMutationDialog': { default: useMutationDialog },
    './ModalPortal': { default: 'portal' }, './PolypbaseIcon': { default: 'icon' }, './box-utility-dialogs.css': {},
  }).default;
  const organization = { id: 1, name: 'Lab' };
  props = {
    box: { id: 17, global_code: 'BOX-17', species: { scientific_name: 'Aurelia aurita' }, organization, thermal_zone: { id: 1, name: 'Current' }, locations: [] },
    zones: [{ id: 2, name: 'Destination', organization, is_active: true }],
    language: 'en', isSaving: false, error: null, onClose() {}, onSubmit: async () => {}, ...overrides,
  };
  function render() {
    assert.ok(mounted);
    cursor = 0; dirty = false; tree = Modal(props); nodes = [];
    function visit(node, parent = null) {
      if (Array.isArray(node)) return node.forEach((child) => visit(child, parent));
      if (!node || typeof node !== 'object') return;
      node.parent = parent; node.isConnected = true;
      node.focus = () => { document.activeElement = node; };
      node.contains = (other) => { while (other) { if (other === node) return true; other = other.parent; } return false; };
      node.querySelectorAll = () => nodes.filter((other) => other !== node && node.contains(other)
        && ((!other.props.disabled && ['button', 'select', 'input', 'textarea'].includes(other.type)) || other.props.tabIndex === 0));
      nodes.push(node);
      if (node.props.ref) node.props.ref.current = node;
      visit(node.props.children, node);
    }
    visit(tree);
    while (effects.length) effects.shift()();
  }
  function flush() { if (dirty) render(); }
  render();
  return {
    document, opener, t: i18n.createTranslator(props.language), get nodes() { return nodes; }, get props() { return props; },
    find: (predicate) => nodes.find(predicate), byClass: (name) => nodes.find((node) => node.props.className?.split(/\s+/).includes(name)),
    field: (name) => nodes.find((node) => node.props.name === name),
    change(name, value) { this.field(name).props.onChange({ target: { value } }); flush(); },
    submit() { const pending = nodes.find((node) => node.type === 'form').props.onSubmit({ preventDefault() {} }); flush(); return pending; },
    setProps(next) { props = { ...props, ...next }; render(); }, flush,
    key(key, shiftKey = false) { const event = { key, shiftKey, prevented: false, preventDefault() { this.prevented = true; } }; listeners.forEach((fn) => fn(event)); return event; },
    unmount() { cleanups.forEach((cleanup) => cleanup?.()); mounted = false; },
  };
}

const labelKeys = ['moveDialogTitle', 'moveDialogCurrentZone', 'moveDialogNewZone', 'moveDialogMovedAt',
  'moveDialogNote', 'moveDialogNotePlaceholder', 'movementHistoryTitle', 'moveDialogCurrent',
  'moveDialogUnknownEnd', 'moveDialogNoHistory', 'cancel', 'moveAction', 'saving', 'noZone'];
const originalLabels = {
  fr: ['Déplacer la boîte', 'Emplacement actuel', 'Nouvel emplacement', 'Date du déplacement', 'Note',
    'Ex. changement de température, rangement, manipulation', 'Historique des emplacements', 'actuel',
    'date de fin inconnue', 'Aucun historique d’emplacement pour cette boîte.', 'Annuler', 'Déplacer',
    'Enregistrement...', 'Sans emplacement'],
  en: ['Move box', 'Current zone', 'New zone', 'Movement date', 'Note',
    'For example, temperature change, storage, handling', 'Location history', 'current',
    'end date unknown', 'No location history for this box.', 'Cancel', 'Move', 'Saving...', 'No zone'],
};

for (const language of ['fr', 'en']) {
  test(`Move ${language}: real central translator preserves every original label and rendered state`, () => {
    const h = harness({ language, zones: [] });
    const expected = originalLabels[language];
    assert.deepEqual(labelKeys.map(key => h.t(key)), expected);
    assert.equal(text(h.find(node => node.type === 'h2')), expected[0]);
    assert.equal(text(h.byClass('move-current-zone').props.children[0]), expected[1]);
    assert.equal(text(h.field('thermal_zone_id').parent.props.children[0]), expected[2]);
    assert.equal(h.field('moved_at'), undefined);
    assert.equal(h.nodes.some(node => ['date', 'datetime-local'].includes(node.props.type)), false);
    assert.equal(text(h.find(node => node.type === 'form')).includes(expected[3]), false);
    assert.equal(text(h.field('notes').parent.props.children[0]), expected[4]);
    assert.equal(h.field('notes').props.placeholder, expected[5]);
    assert.equal(text(h.find(node => node.type === 'summary').props.children[0]), expected[6]);
    assert.ok(text(h.byClass('move-location-history')).includes(expected[9]));
    assert.equal(h.byClass('box-dialog-close').props['aria-label'], expected[10]);
    assert.equal(h.byClass('box-dialog-close').props.title, expected[10]);
    assert.equal(text(h.find(node => node.props.type === 'submit')), expected[11]);
    assert.equal(text(h.find(node => node.type === 'option')), expected[13]);
    h.setProps({ box: { ...h.props.box, thermal_zone: null, locations: [
      { id: 1, thermal_zone: { name: 'Past' }, starts_at: '2026-01-01T10:00:00Z', ends_at: null, end_date_unknown: true },
      { id: 2, thermal_zone: { name: 'Current' }, starts_at: '2026-01-02T10:00:00Z', ends_at: null, end_date_unknown: false },
    ] } });
    assert.equal(text(h.byClass('move-current-zone').props.children[1]), expected[13]);
    const history = text(h.byClass('move-location-history'));
    const rows = h.nodes.filter(node => node.props.className === 'move-location-row');
    assert.equal(text(rows[1]).includes(h.t('moveHistoryDeparture')), false);
    assert.equal(rows[1].props.children[1].props.children.filter(Boolean).length, 1);
    assert.ok(text(rows[0]).includes(h.t('moveHistoryDeparture')));
    assert.ok(history.includes(expected[8]));
    assert.equal(history.includes('→'), false);
    assert.ok(history.includes(h.t('moveHistoryArrival')));
    assert.ok(history.includes(h.t('moveHistoryDeparture')));
    assert.equal(text(h.byClass('move-subject-panel')).includes('Lab'), false);
    assert.ok(h.nodes.filter(node => node.type === 'time').every(node => node.props.dateTime));
    h.setProps({ isSaving: true });
    assert.equal(text(h.find(node => node.props.type === 'submit')), expected[12]);
    h.unmount();
  });

  test(`Move ${language}: body identity, arrow-free native select and collapsed lightweight history`, () => {
    const h = harness({ language, error: 'Retry the move' });
    const identity = h.byClass('utility-dialog-identity');
    assert.equal(text(identity), 'BOX-17Aurelia aurita');
    assert.equal(h.find((node) => node.type === 'header').contains(identity), false);
    assert.ok(h.byClass('box-dialog-body').contains(identity));
        assert.equal(h.field('notes').type, 'textarea');
        assert.equal(h.field('notes').props.rows, 2);
    assert.equal(h.nodes.filter((node) => node.type === 'strong' && text(node) === 'BOX-17').length, 1);
    const flow = h.byClass('box-dialog-location-flow');
    assert.equal(h.field('thermal_zone_id').type, 'select');
    assert.equal(h.field('thermal_zone_id').parent.parent, flow);
        const current = h.byClass('move-current-zone');
        assert.equal(current.parent, h.byClass('move-subject-panel'));
                assert.ok(current.parent.contains(identity));
                assert.equal(flow.contains(current), false);
        assert.equal(text(current), `${h.t('moveDialogCurrentZone')}Current`);
        assert.equal(h.nodes.some((node) => current.contains(node) && ['input', 'select', 'textarea'].includes(node.type)), false);
    assert.equal(h.byClass('move-direction'), undefined);
    assert.equal(h.byClass('current-zone-card'), undefined);
    const history = h.byClass('move-location-history');
    assert.equal(history.type, 'details');
    assert.equal(history.props.open, undefined);
    assert.equal(h.find((node) => node.type === 'summary').props.tabIndex, 0);
    const body = h.byClass('box-dialog-body');
    assert.ok(body.contains(h.find((node) => node.props.role === 'alert')));
    const children = body.props.children.filter(Boolean);
    assert.ok(children.indexOf(h.find((node) => node.props.role === 'alert')) < children.indexOf(history));
    h.unmount();
  });
}

test('Move filters current, inactive and foreign zones, preserves all stored history and exact payload', async () => {
  const organization = { id: 1, name: 'Lab' }, submitted = [];
  const locations = Array.from({ length: 8 }, (_, id) => ({ id, thermal_zone: { name: `Historic ${id}` }, starts_at: '2026-01-01T10:00:00Z', ends_at: null, end_date_unknown: id === 0, notes: `Note ${id}` }));
  const h = harness({
    box: { id: 17, global_code: 'BOX-17', species: { scientific_name: 'Aurelia' }, organization, thermal_zone: null, locations },
    zones: [
      { id: 2, name: 'Inactive', organization, is_active: false },
      { id: 3, name: 'Foreign', organization: { id: 2 }, is_active: true },
      { id: 4, name: 'Allowed A', organization, is_active: true },
      { id: 5, name: 'Allowed B', organization, is_active: true },
    ], onSubmit: async (payload) => submitted.push(plain(payload)),
  });
  assert.deepEqual(h.nodes.filter((node) => node.type === 'option').map(text), ['Allowed A', 'Allowed B']);
  assert.equal(h.nodes.filter((node) => node.props.className === 'move-location-row').length, 8);
  assert.match(text(h.byClass('move-location-history')), /end date unknown/);
  h.change('thermal_zone_id', '5'); h.change('notes', '  Kept note  ');
  await h.submit(); h.flush();
  assert.deepEqual(submitted, [{ expected_thermal_zone_id: null, thermal_zone_id: 5, notes: 'Kept note' }]);
  assert.equal(Object.hasOwn(submitted[0], 'moved_at'), false);
  h.setProps({ zones: [{ id: 4, name: 'Allowed A', organization, is_active: true }] });
  assert.equal(h.field('thermal_zone_id').props.value, '4', 'a stale draft cannot submit an unavailable destination');
  h.unmount();
});

test('Move retains draft through pending confirmation, blocks duplicate/dismissal, restores keyboard focus and retry', async () => {
  const attempt = deferred(), payloads = []; let closed = 0;
  const h = harness({ onClose: () => closed++, onSubmit: (payload) => { payloads.push(plain(payload)); return attempt.promise; } });
  assert.equal(h.document.activeElement, h.field('thermal_zone_id'));
  const first = h.nodes.find((node) => node.type === 'button');
  const last = h.nodes.filter((node) => node.type === 'button').at(-1);
  first.focus(); assert.equal(h.key('Tab', true).prevented, true); assert.equal(h.document.activeElement, last);
  assert.equal(h.key('Tab').prevented, true); assert.equal(h.document.activeElement, first);
  h.change('notes', 'Draft');
  const staleClose = first.props.onClick, staleBackdrop = h.byClass('box-dialog-backdrop').props.onClick;
  const pending = h.submit(); await h.submit();
  assert.deepEqual(payloads, [{ expected_thermal_zone_id: 1, thermal_zone_id: 2, notes: 'Draft' }]);
  assert.equal(h.find((node) => node.props.role === 'dialog').props['aria-busy'], true);
  assert.ok(h.nodes.filter((node) => ['button', 'select', 'input', 'textarea'].includes(node.type)).every((node) => node.props.disabled));
  staleClose(); staleBackdrop(); h.key('Escape'); assert.equal(closed, 0);
  attempt.reject(new Error('Rejected')); await assert.rejects(pending, /Rejected/); h.flush();
  h.setProps({ error: 'Retry the move' });
  assert.equal(h.field('notes').props.value, 'Draft');
  assert.equal(h.field('moved_at'), undefined);
  assert.equal(h.document.activeElement, h.field('thermal_zone_id'));
  h.setProps({ onSubmit: async (payload) => payloads.push(plain(payload)) });
  await h.submit(); h.flush();
  assert.deepEqual(payloads[1], payloads[0], 'retry preserves the full draft payload');
  h.key('Escape'); assert.equal(closed, 1);
  h.unmount(); assert.equal(h.document.activeElement, h.opener);
});

test('Move excludes its current zone from destination choices', () => {
  const organization = { id: 1, name: 'Lab' };
  const h = harness({ zones: [
    { id: 1, name: 'Current', organization, is_active: true },
    { id: 2, name: 'Destination', organization, is_active: true },
  ] });
  assert.deepEqual(h.nodes.filter((node) => node.type === 'option').map(text), ['Destination']);
  h.unmount();
});

test('Move with no allowed zone cannot submit and keeps cancel available', async () => {
  let calls = 0;
  const h = harness({ zones: [], onSubmit: async () => calls++ });
  assert.equal(h.nodes.find((node) => node.props.type === 'submit').props.disabled, true);
  await h.submit(); assert.equal(calls, 0);
  assert.equal(h.nodes.find((node) => node.props['aria-label']).props.disabled, false);
  h.unmount();
});

test('Move sends the exact date-free modal payload to the API only after confirmation', async () => {
  const app = appHarness();
  const confirmation = deferred();
  const h = harness();
  Object.assign(app.context, {
    box: h.props.box, zones: h.props.zones, currentZone: h.props.box.thermal_zone,
    isSavingMove: false, t: h.t,
    operationLifetimeRef: { current: true }, isOperationCurrent: () => true,
    confirmAction: () => confirmation.promise,
    setIsSavingMove() {}, setMoveError() {}, setIsMoveOpen() {},
    onMoveBox: app.context.moveBox,
  });
  app.evaluate(functionNode('handleMove').getText(ast));
  h.setProps({ onSubmit: app.context.handleMove });
  h.change('notes', '  Confirmed note  ');
  const pending = h.submit();
  await tick();
  assert.equal(app.requests.length, 0, 'waiting for confirmation must not write');
  confirmation.resolve(true);
  await tick();
  const request = app.requests[0];
  assert.equal(request.method, 'apiPost');
  assert.equal(request.url, '/api/boxes/17/move/');
  assert.deepEqual(plain(request.payload), {
    expected_thermal_zone_id: 1, thermal_zone_id: 2, notes: 'Confirmed note',
  });
  assert.equal(Object.hasOwn(request.payload, 'moved_at'), false);
  request.resolve({ ...h.props.box, thermal_zone: { id: 2, name: 'Destination' } });
  await tick();
  assert.equal(app.requests[1].url, '/api/thermal-zones/?limit=80');
  app.requests[1].resolve({ results: h.props.zones, next: null });
  await pending;
  h.unmount();
});

test('local Move CSS gives phone fields 16px, visible focus and no nested history scroller', () => {
  const css = read('components/box-utility-dialogs.css');
  assert.match(read('components/MoveBoxModal.tsx'), /import '\.\/box-utility-dialogs\.css'/);
  assert.match(css, /\.box-dialog--move :is\(input, select, textarea\) \{ font-size: 16px;/);
  assert.match(css, /:focus-visible \{\s*outline: 2px solid var\(--color-primary\)/);
  assert.doesNotMatch(css, /overflow: auto|overflow-y: auto|position: sticky|!important/);
  assert.match(css, /\.move-location-row p[^}]*white-space: pre-line/);
  assert.doesNotMatch(css, /summary::before|summary::-webkit-details-marker|list-style: none/);
  const fieldLayouts = [...css.matchAll(/\.box-dialog--move \.move-fields \{([^}]+)\}/g)];
  assert.equal(fieldLayouts.length, 3);
  for (const [, declarations] of fieldLayouts) {
    assert.match(declarations, /grid-template-columns: minmax\(0, 1fr\);/);
  }
  assert.doesNotMatch(read('components/MoveBoxModal.tsx'), /movedAt|setMovedAt|getCurrentDateTimeValue|datetime-local|moved_at/);
    assert.match(css, /@media \(max-width: 639px\)[\s\S]*\.box-dialog--move \.box-dialog-location-flow \{ grid-template-columns: minmax\(0, 1fr\)/);
    assert.doesNotMatch(css, /move-direction/);
    assert.match(css, /\.box-dialog--move \{ --box-dialog-width: 620px;/);
    assert.match(css, /\.box-dialog--move \.box-dialog-heading \{ align-items: center;/);
    assert.match(css, /\.box-dialog--move \.box-dialog-close \{ border-radius: 50%;/);
    assert.match(css, /\.box-dialog--move \.move-subject-panel \{[^}]*background: var\(--color-surface-info\);[^}]*border-left: 3px solid var\(--color-primary\);/);
    assert.match(css, /\.box-dialog--move \.box-dialog-location-flow \{\s*grid-template-columns: minmax\(0, 1fr\);/);
    assert.doesNotMatch(css.match(/\.move-location-history \{[^}]*\}/)[0], /border/);
    assert.doesNotMatch(css.match(/\.move-location-history summary \{[^}]*\}/)[0], /border/);
});

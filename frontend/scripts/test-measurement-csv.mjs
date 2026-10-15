import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { resourceClient } from './resource-test-harness.mjs';
import { loadComponent } from './ui-primitives-test-harness.mjs';

function moduleAt(path, modules = {}) {
  const exports = {};
  const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  vm.runInNewContext(outputText, { exports, FormData, require: name => {
    assert.ok(Object.hasOwn(modules, name), name);
    return modules[name];
  } });
  return exports;
}
const fr = moduleAt('../src/i18n/fr.ts').fr;
const en = moduleAt('../src/i18n/en.ts').en;
const inspection = { headers: ['box', 'count', 'ignored'], fields: ['box_reference', 'polyp_count'], issues: [], application_available: false };
const file = new File(['box,count,ignored\nB,0,x'], 'test.csv', { type: 'text/csv' });
// Response.json() drains a stream across event-loop turns, not only microtasks.
const tick = () => new Promise(resolve => setImmediate(resolve));
const response = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
function setup() {
  const calls = [];
  const api = resourceClient({ document: { cookie: 'csrftoken=csrf-test' }, fetch: (path, options) => new Promise((resolve, reject) => calls.push({ path, options, resolve, reject })) });
  const logic = moduleAt('../src/api/measurementImport.ts', { './client': api });
  return { api, logic, calls };
}

function harness() {
  const env = setup();
  const slots = [], effects = new Map();
  let cursor = 0, pending = [], tree, unmounted = false, updatesAfterUnmount = 0;
  let operationCurrent = true;
  const hooks = {
    useState(initial) {
      const i = cursor++;
      if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
      return [slots[i], next => {
        if (unmounted) { updatesAfterUnmount++; return; }
        slots[i] = typeof next === 'function' ? next(slots[i]) : next;
      }];
    },
    useRef(initial) { return slots[cursor++] ??= { current: initial }; },
    useEffect(setup, deps) {
      const i = cursor++;
      if (!effects.has(i) || deps.some((dep, j) => !Object.is(dep, effects.get(i).deps[j]))) pending.push({ i, setup, deps });
    },
  };
  const jsx = (type, props) => ({ type, props });
  const component = loadComponent('MeasurementImportPanel', {
    react: hooks, 'react/jsx-runtime': { jsx, jsxs: jsx }, '../api/client': env.api, '../api/measurementImport': env.logic,
  }, { AbortController });
  const t = key => { assert.ok(en[key], `Missing translation: ${key}`); return en[key]; };
  function render() {
    cursor = 0; pending = [];
    tree = component({ organizationId: 1, isOperationCurrent: () => operationCurrent, t });
    for (const effect of pending) {
      effects.get(effect.i)?.cleanup?.();
      effects.set(effect.i, { deps: effect.deps, cleanup: effect.setup() });
    }
    return tree;
  }
  function nodes(node) {
    if (arguments.length === 0) node = tree;
    if (node == null || typeof node === 'boolean') return [];
    if (Array.isArray(node)) return node.flatMap(nodes);
    if (typeof node !== 'object') return [node];
    if (typeof node.type === 'function') return nodes(node.type(node.props));
    return [node, ...nodes(node.props?.children)];
  }
  const find = predicate => nodes().find(node => node && typeof node === 'object' && predicate(node));
  const button = key => find(node => node.type === 'button' && node.props.children === t(key));
  function chooseFile(selected = file) { find(node => node.props.type === 'file').props.onChange({ target: { files: selected ? [selected] : [] } }); render(); }
  function map(index, value) {
    nodes().filter(node => node.type === 'select' && node.props.value !== undefined)[index + 1].props.onChange({ target: { value } });
    render();
  }
  async function inspect(data = inspection) {
    chooseFile(); button('csvInspect').props.onClick(); render();
    env.calls.at(-1).resolve(response(data)); await tick(); render();
  }
  function confirm() { find(node => node.props.type === 'checkbox').props.onChange({ target: { checked: true } }); render(); }
  function completeMapping() { map(0, 'field:box_reference'); map(1, 'field:polyp_count'); map(2, 'ignore'); confirm(); }
  render();
  return { ...env, render, nodes, find, button, chooseFile, map, inspect, confirm, completeMapping,
    text: () => nodes().filter(node => typeof node === 'string' || typeof node === 'number').join(' '),
    changeDelimiter(value) { nodes().find(node => node.type === 'select').props.onChange({ target: { value } }); render(); },
    setCurrent(value) { operationCurrent = value; },
    unmount() { unmounted = true; for (const effect of effects.values()) effect.cleanup?.(); },
    get updatesAfterUnmount() { return updatesAfterUnmount; },
  };
}
const preview = (overrides = {}) => ({
  application_available: false, preparation_fingerprint: 'prep', plan_fingerprint: 'plan', source_complete: true, issues: [],
  summary: { total_source_rows: 1, ignored_rows: 0, create_candidates: 1, already_matching: 0, conflicts: 0, unknown_boxes: 0, invalid_or_blocked: 0, blocked_rows: 0, ready: true },
  rows: [{ record_number: 2, line_start: 2, line_end: 3, outcome: 'CREATE', box_reference: '<img onerror=bad>', measured_on: null,
    precision: 'iso_week', iso_year: 2026, iso_week: 1,
    values: [{ field: 'polyp_count', state: 'value', value: 0 }, { field: 'strobila_count', state: 'null', value: null },
      { field: 'salinity_psu', state: 'absent', value: null }, { field: 'culture_status', state: 'blank', value: null }],
    issues: [{ code: 'future_issue', field: null, column: 2 }] }], ...overrides,
});

test('multipart reuses CSRF, organization and no-store without setting a boundary; JSON behavior stays intact', async () => {
  const { api, logic, calls } = setup();
  const signal = new AbortController().signal;
  const promise = logic.inspectMeasurements(file, '\t', 1, signal);
  const call = calls[0];
  assert.equal(call.path, '/api/measurement-import/inspect/');
  assert.equal(call.options.method, 'POST');
  assert.equal(call.options.cache, 'no-store');
  assert.equal(call.options.credentials, 'include');
  assert.equal(call.options.headers.get('X-Organization-Id'), '1');
  assert.equal(call.options.headers.get('X-CSRFToken'), 'csrf-test');
  assert.equal(call.options.headers.has('Content-Type'), false);
  assert.equal(call.options.body.get('delimiter'), '\t');
  assert.equal(call.options.body.get('file').name, 'test.csv');
  call.resolve(response(inspection)); await promise;
  const json = api.apiPost('/api/test/', { count: 0 });
  assert.equal(calls[1].options.headers.get('Content-Type'), 'application/json');
  assert.equal(calls[1].options.body, '{"count":0}');
  calls[1].resolve(response({})); await json;
});

test('mapping distinguishes undecided, ignore and exact unusual headers; preview requires literal confirmation', async () => {
  const { logic, calls, api } = setup();
  assert.equal(logic.mappingComplete(inspection, { box: null, count: 'polyp_count' }), false);
  assert.equal(logic.mappingComplete(inspection, { box: null, count: 'polyp_count', ignored: undefined }), false);
  const mapping = { box: 'box_reference', count: 'polyp_count', ignored: null };
  assert.equal(logic.mappingComplete(inspection, mapping), true);
  await assert.rejects(logic.previewMeasurements(file, ',', 1, inspection, mapping, false, new AbortController().signal));
  assert.equal(calls.length, 0);
  const special = { ...inspection, headers: ['__proto__', ' Box '], fields: ['box_reference'] };
  assert.equal(logic.mappingComplete(special, {}), false);
  const promise = logic.previewMeasurements(file, ';', 1, special, { ['__proto__']: null, ' Box ': 'box_reference' }, true, new AbortController().signal);
  assert.equal(calls[0].path, '/api/measurement-import/preview/');
  assert.equal(calls[0].options.body.get('confirmed'), 'true');
  assert.equal(calls[0].options.body.get('mapping'), '{"__proto__":null," Box ":"box_reference"}');
  calls[0].resolve(response(preview())); await promise;
  api.setActiveOrganizationContext(2);
  await assert.rejects(logic.inspectMeasurements(file, ',', 1, new AbortController().signal));
  assert.equal(calls.length, 1);
});

test('actual panel starts unselected and gates preview on every choice plus mapping confirmation', async () => {
  const h = harness();
  assert.equal(h.button('csvInspect').props.disabled, true);
  await h.inspect();
  assert.equal(h.find(node => node.props.type === 'checkbox').props.disabled, true);
  assert.equal(h.button('csvPreview').props.disabled, true);
  h.map(0, 'field:box_reference'); h.map(1, 'field:polyp_count');
  assert.equal(h.button('csvPreview').props.disabled, true);
  h.map(2, 'ignore');
  assert.equal(h.find(node => node.props.type === 'checkbox').props.disabled, false);
  assert.equal(h.button('csvPreview').props.disabled, true);
  h.confirm(); assert.equal(h.button('csvPreview').props.disabled, false);
  h.button('csvPreview').props.onClick(); h.render();
  assert.equal(h.calls[1].options.body.get('mapping'), '{"box":"box_reference","count":"polyp_count","ignored":null}');
  h.calls[1].resolve(response(preview())); await tick(); h.render();
  assert.match(h.text(), /No blockers detected/);
  assert.match(h.text(), /No measurements imported/);
  assert.match(h.text(), /NULL \(no value\)/);
  assert.match(h.text(), /Not supplied/);
  assert.match(h.text(), /Blank/);
  assert.ok(h.nodes().includes('0'));
  assert.match(h.text(), /Unrecognized issue/);
  assert.match(h.text(), /future_issue/);
  assert.match(h.text(), /<img onerror=bad>/);
  assert.equal(h.find(node => node.props.dangerouslySetInnerHTML), undefined);
  h.map(0, 'ignore');
  assert.equal(h.find(node => node.props.type === 'checkbox').props.checked, false);
  assert.doesNotMatch(h.text(), /No blockers detected/);
});

test('file and delimiter changes abort stale inspection and discard headers', async () => {
  for (const change of [h => h.chooseFile(new File(['new'], 'new.csv')), h => h.changeDelimiter(';')]) {
    const h = harness(); h.chooseFile(); h.button('csvInspect').props.onClick(); h.render();
    change(h);
    assert.equal(h.calls[0].options.signal.aborted, true);
    h.calls[0].resolve(response(inspection)); await tick(); h.render();
    assert.equal(h.find(node => node.props.type === 'checkbox'), undefined);
  }
});

test('mapping edits invalidate a delayed preview even if fetch ignores abort', async () => {
  const h = harness(); await h.inspect(); h.completeMapping();
  h.button('csvPreview').props.onClick(); h.render(); h.map(1, 'ignore');
  assert.equal(h.calls[1].options.signal.aborted, true);
  h.calls[1].resolve(response(preview())); await tick(); h.render();
  assert.doesNotMatch(h.text(), /No blockers detected/);
});

test('organization lifetime, operation generation and unmount reject stale success and errors', async () => {
  for (const change of [h => h.api.setActiveOrganizationContext(2), h => h.setCurrent(false), h => h.unmount()]) {
    for (const status of [200, 403]) {
      const h = harness(); h.chooseFile(); h.button('csvInspect').props.onClick(); h.render();
      change(h); h.calls[0].resolve(response(status === 200 ? inspection : { error_code: 'measurement_import_forbidden' }, status));
      await tick();
      assert.equal(h.updatesAfterUnmount, 0);
      h.render();
      assert.equal(h.find(node => node.props.type === 'checkbox'), undefined);
      assert.equal(h.find(node => node.props.role === 'alert'), undefined);
    }
  }
});

test('actionable error states never expose raw server errors and permit retry', async () => {
  for (const [status, expected] of [[400, en.csvErrorConfiguration], [413, en.csvErrorSize], [403, en.csvErrorPermission], [500, en.csvErrorRequest]]) {
    const h = harness(); h.chooseFile(); h.button('csvInspect').props.onClick(); h.render();
    assert.match(h.text(), /Inspecting columns/);
    h.calls[0].resolve(response({ error_code: '<private server detail>' }, status)); await tick(); h.render();
    assert.match(h.text(), new RegExp(expected.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    assert.doesNotMatch(h.text(), /private server/);
    assert.equal(h.button('csvInspect').props.disabled, false);
  }
});

test('inspection issues prevent mapping; oversize files never upload', async () => {
  const h = harness();
  h.chooseFile(new File([new Uint8Array(5 * 1024 * 1024 + 1)], 'large.csv'));
  assert.equal(h.button('csvInspect').props.disabled, true);
  assert.match(h.text(), /too large/);
  await h.inspect({ ...inspection, issues: [{ code: 'invalid_utf8', field: null, column: null }] });
  assert.match(h.text(), /Save the file as UTF-8/);
  assert.equal(h.find(node => node.props.type === 'checkbox'), undefined);
});

test('blocked, incomplete, empty and paginated preview states execute actual rendering', async () => {
  const h = harness(); await h.inspect(); h.completeMapping();
  async function show(data) {
    h.button('csvPreview').props.onClick(); h.render(); h.calls.at(-1).resolve(response(data)); await tick(); h.render();
  }
  await show(preview({ source_complete: false, rows: [] }));
  assert.match(h.text(), /results are partial/); assert.match(h.text(), /No diagnosed rows/);
  const many = Array.from({ length: 10001 }, (_, i) => ({ ...preview().rows[0], record_number: i + 2 }));
  await show(preview({ summary: { ...preview().summary, ready: false }, rows: many }));
  assert.match(h.text(), /found blockers/);
  assert.equal(h.nodes().filter(node => node.type === 'h4').length, 50);
  assert.equal(h.button('csvPrevious').props.disabled, true);
  h.button('csvNext').props.onClick(); h.render();
  assert.match(h.text(), /Record\s+52/);
  assert.equal(h.nodes().filter(node => node.type === 'h4').length, 50);
});

test('FR/EN parity and issue translations cover current canonical/planner codes; diagnostic-only copy persists', () => {
  const { logic } = setup();
  for (const key of Object.keys(fr).filter(key => key.startsWith('csv'))) {
    assert.ok(fr[key]); assert.ok(en[key]); assert.notEqual(fr[key], key); assert.notEqual(en[key], key);
  }
  for (const key of [...Object.values(logic.fieldLabels), ...Object.values(logic.issueLabels), ...Object.values(logic.outcomeLabels)]) {
    assert.ok(fr[key]); assert.ok(en[key]);
  }
  assert.match(fr.csvDiagnosticOnly, /Aucun relevé importé/);
  assert.match(en.csvDiagnosticOnly, /durable receipt contract/);
  const source = readFileSync(new URL('../src/api/measurementImport.ts', import.meta.url), 'utf8');
  assert.equal((source.match(/\/api\/measurement-import\//g) ?? []).length, 2);
  assert.doesNotMatch(source, /\/apply\//);
});

test('laboratory entry has no superuser bypass and can close/unmount the diagnostic panel', () => {
  let open = false;
  const jsx = (type, props) => ({ type, props });
  const Entry = loadComponent('MeasurementImportEntry', {
    react: { useState: () => [open, next => { open = next; }] },
    'react/jsx-runtime': { jsx, jsxs: jsx },
    '../utils/profilePermissions': moduleAt('../src/utils/profilePermissions.ts'),
    './MeasurementImportPanel': { default: 'Panel' },
  });
  const props = { organizationId: 1, isOperationCurrent: () => true, t: key => en[key], profile: { is_superuser: true, memberships: [] } };
  assert.equal(Entry(props), null);
  props.profile.memberships = [{ role: 'viewer', organization: { id: 1 } }]; assert.equal(Entry(props), null);
  for (const role of ['admin', 'lab_technician']) {
    props.profile.memberships = [{ role, organization: { id: 1 } }]; open = false;
    let entry = Entry(props); assert.equal(entry.props.children[0].props['aria-expanded'], false);
    entry.props.children[0].props.onClick(); entry = Entry(props);
    assert.equal(entry.props.children[0].props['aria-expanded'], true);
    assert.ok(entry.props.children[1]);
    entry.props.children[0].props.onClick(); entry = Entry(props); assert.equal(entry.props.children[1], false);
  }
});

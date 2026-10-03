import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import * as jsxRuntime from 'react/jsx-runtime';
import ts from 'typescript';

class ApiResourceCancelledError extends Error {}
class ApiError extends Error {
  constructor(message, data) { super(message); this.data = data; }
}

// Exercise the actual component handlers without a database or network.
function createView(latestSalinity, canRecordManualSalinity = true, operations = {}) {
  const slots = [];
  let cursor = 0;
  const writes = [];
  const presentedErrors = [];
  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
      return [slots[index], (value) => { slots[index] = value; }];
    },
  };
  const source = readFileSync(new URL('../src/components/ZonesView.tsx', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(`${source}\nexport { ZoneFunctionalSections };`, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
  const imports = {
    react: hooks,
    'react/jsx-runtime': jsxRuntime,
    '../api/client': { ApiError, ApiResourceCancelledError },
    '../utils/dateFormat': { formatDisplayDate: (date) => date },
    '../utils/errors': { getErrorMessage: (error) => { presentedErrors.push(error); return error.message; } },
    '../utils/temperatureScale': {},
    './BoxTrackingPreview': { default: () => null },
    './ModalPortal': { default: () => null },
    './PageLoader': { default: () => null },
    './PolypbaseIcon': { default: () => null },
    './ZoneMovementHistory': { ZoneRecentMovements: () => null },
  };
  const exports = {};
  class FixedDate extends Date {
    constructor(...args) { super(...(args.length ? args : [2026, 9, 3, 12])); }
  }
  vm.runInNewContext(outputText, {
    exports, Date: FixedDate,
    require(name) {
      assert.ok(Object.hasOwn(imports, name), `Unexpected import ${name}`);
      return imports[name];
    },
  });
  let tree;
  function render() {
    cursor = 0;
    tree = exports.ZoneFunctionalSections({
      zone: { id: 7, capacity: null, latest_salinity: latestSalinity, probes: [] },
      language: 'en', boxCount: 0, boxDirectoryLabel: 'boxes', canRecordManualSalinity,
      t: (key) => key, onOpenBox() {}, onOpenHistory() {}, onOpenBoxes() {},
      onRefreshZoneSalinityCapability: async () => {},
      onRecordManualSalinity: async (zoneId, payload) => { writes.push({ kind: 'create', zoneId, ...payload }); },
      onUpdateManualSalinity: async (zoneId, measurementId, payload) => { writes.push({ kind: 'correct', zoneId, measurementId, ...payload }); },
      ...operations,
    });
  }
  function find(predicate) {
    const found = [];
    function visit(node) {
      if (!node || typeof node !== 'object') return;
      if (Array.isArray(node)) return node.forEach(visit);
      if (predicate(node)) found.push(node);
      visit(node.props?.children);
    }
    visit(tree);
    return found;
  }
  render();
  return {
    writes, presentedErrors, find,
    click(label) {
      const button = find((node) => node.type === 'button' && node.props['aria-label'] === label)[0];
      assert.ok(button, `Missing ${label}`);
      button.props.onClick(); render();
    },
    change(type, value) {
      const input = find((node) => node.type === 'input' && node.props.type === type)[0];
      assert.ok(input);
      input.props.onChange({ target: { value } }); render();
    },
    async submit() {
      await find((node) => node.type === 'form')[0].props.onSubmit({ preventDefault() {} }); render();
    },
    cancel() {
      find((node) => node.type === 'button' && node.props.children === 'cancel')[0].props.onClick(); render();
    },
  };
}

const yesterday = { id: 19, measured_on: '2026-10-02', salinity_psu: '0.00', notes: 'Existing note', can_edit: true };

for (const measuredOn of ['2026-10-02', '2026-10-03']) {
  test(`new reading defaults to today independently of correctable latest ${measuredOn}`, async () => {
    const view = createView({ ...yesterday, measured_on: measuredOn });
    view.click('zoneSalinityCreateAction');
    const date = view.find((node) => node.type === 'input' && node.props.type === 'date')[0];
    assert.equal(date.props.value, '2026-10-03');
    assert.equal(date.props.disabled, false);
    assert.equal(view.find((node) => node.type === 'input' && node.props.type === 'number')[0].props.value, '');
    assert.equal(view.find((node) => node.type === 'textarea')[0].props.value, '');
    view.change('number', '0');
    await view.submit();
    assert.deepEqual(view.writes, [{ kind: 'create', zoneId: 7, measured_on: '2026-10-03', salinity_psu: '0', notes: '' }]);
  });

  test(`pencil corrects immutable existing date ${measuredOn} and preserves zero`, async () => {
    const view = createView({ ...yesterday, measured_on: measuredOn });
    view.click('zoneSalinityEditAction');
    const date = view.find((node) => node.type === 'input' && node.props.type === 'date')[0];
    assert.equal(date.props.value, measuredOn);
    assert.equal(date.props.disabled, true);
    assert.equal(view.find((node) => node.type === 'input' && node.props.type === 'number')[0].props.value, '0.00');
    await view.submit();
    assert.deepEqual(view.writes, [{ kind: 'correct', zoneId: 7, measurementId: 19, salinity_psu: '0.00', notes: 'Existing note' }]);
    assert.equal('measured_on' in view.writes[0], false);
  });
}

test('cancel correction then create clears the existing identity, date and note', async () => {
  const view = createView(yesterday);
  view.click('zoneSalinityEditAction'); view.cancel(); view.click('zoneSalinityCreateAction');
  view.change('date', '2026-10-04'); view.change('number', '0'); await view.submit();
  assert.deepEqual(view.writes, [{ kind: 'create', zoneId: 7, measured_on: '2026-10-04', salinity_psu: '0', notes: '' }]);
});

function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

for (const mode of ['create', 'correct']) {
  test(`delayed ${mode} cancellation stays silent and does not close or refresh the editor`, async () => {
    const pending = deferred();
    let refreshes = 0;
    const view = createView(yesterday, true, {
      [mode === 'create' ? 'onRecordManualSalinity' : 'onUpdateManualSalinity']: () => pending.promise,
      onRefreshZoneSalinityCapability: async () => { refreshes += 1; },
    });
    view.click(mode === 'create' ? 'zoneSalinityCreateAction' : 'zoneSalinityEditAction');
    view.change('number', '0');
    const completion = view.submit();
    // A remount gets independent hook state while the old callback is pending.
    const replacement = createView(null);
    pending.reject(new ApiResourceCancelledError('stale organization'));
    await completion;
    assert.equal(view.presentedErrors.length, 0);
    assert.equal(refreshes, 0);
    assert.equal(view.find((node) => node.type === 'form').length, 1);
    assert.equal(view.find((node) => node.type === 'fieldset')[0].props.disabled, false);
    assert.equal(replacement.find((node) => node.type === 'form').length, 0);
    assert.equal(replacement.presentedErrors.length, 0);
  });
}

test('cancelled delayed expiry refresh does not present the expiry message or close the editor', async () => {
  const pending = deferred();
  const started = deferred();
  const view = createView(yesterday, true, {
    onUpdateManualSalinity: async () => { throw new ApiError('expired', { code: 'salinity_edit_window_expired' }); },
    onRefreshZoneSalinityCapability: () => { started.resolve(); return pending.promise; },
  });
  view.click('zoneSalinityEditAction');
  const completion = view.submit();
  await started.promise;
  pending.reject(new ApiResourceCancelledError('stale refresh'));
  await completion;
  assert.equal(view.presentedErrors.length, 0);
  assert.equal(view.find((node) => node.type === 'form').length, 1);
  assert.equal(view.find((node) => node.type === 'p' && node.props.className === 'inline-error').length, 0);
});

for (const outcome of ['success', 'failure']) {
  test(`noncancelled expiry refresh retains ${outcome} handling`, async () => {
    const pending = deferred();
    const started = deferred();
    const failure = new Error('refresh failed');
    const view = createView(yesterday, true, {
      onUpdateManualSalinity: async () => { throw new ApiError('expired', { code: 'salinity_edit_window_expired' }); },
      onRefreshZoneSalinityCapability: () => { started.resolve(); return pending.promise; },
    });
    view.click('zoneSalinityEditAction');
    const completion = view.submit();
    await started.promise;
    if (outcome === 'success') pending.resolve(); else pending.reject(failure);
    await completion;
    const errors = view.find((node) => node.type === 'p' && node.props.className === 'inline-error');
    assert.equal(errors[0].props.children, outcome === 'success' ? 'zoneSalinityEditExpired' : failure.message);
    assert.equal(view.find((node) => node.type === 'form').length, outcome === 'success' ? 0 : 1);
  });
}

test('ordinary delayed save failure is still presented', async () => {
  const pending = deferred();
  const failure = new Error('save failed');
  const view = createView(yesterday, true, { onRecordManualSalinity: () => pending.promise });
  view.click('zoneSalinityCreateAction'); view.change('number', '0');
  const completion = view.submit();
  pending.reject(failure); await completion;
  assert.deepEqual(view.presentedErrors, [failure]);
  assert.equal(view.find((node) => node.type === 'p' && node.props.className === 'inline-error')[0].props.children, failure.message);
});

test('expired capability removes only correction; readonly removes both actions', () => {
  const expired = createView({ ...yesterday, can_edit: false });
  assert.equal(expired.find((node) => node.props?.['aria-label'] === 'zoneSalinityEditAction').length, 0);
  assert.equal(expired.find((node) => node.props?.['aria-label'] === 'zoneSalinityCreateAction').length, 1);
  const readonly = createView(yesterday, false);
  assert.equal(readonly.find((node) => node.type === 'button' && node.props['aria-label']).length, 0);
});

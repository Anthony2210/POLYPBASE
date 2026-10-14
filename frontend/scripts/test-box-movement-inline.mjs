import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { appHarness, ast, boxSource, deferred, functionNode, source as appSource, tick } from './app-operation-test-harness.mjs';

const read = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
const plain = (value) => JSON.parse(JSON.stringify(value));
const text = (node) => Array.isArray(node) ? node.map(text).join('')
  : node == null || typeof node === 'boolean' ? '' : typeof node === 'object'
      ? text(typeof node.type === 'function' ? node.type(node.props) : node.props?.children) : String(node);

function load(path, imports = {}, globals = {}) {
  const exports = {};
  const code = ts.transpileModule(read(path), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  vm.runInNewContext(code, {
    exports, ...globals,
    require(name) { assert.ok(name in imports, `Unexpected import ${name} in ${path}`); return imports[name]; },
  });
  return exports;
}

// Use the actual ApiError class so instanceof checks exercise the real error contract.
const { ApiError } = load('api/client.ts');
const helpers = load('utils/boxMovementCorrection.ts', { '../api/client': { ApiError } });
const catalogues = Object.fromEntries(['fr', 'en'].map((language) => [language, load(`i18n/${language}.ts`)[language]]));
const i18n = load('i18n/index.ts', Object.fromEntries(['fr', 'en'].map((language) => [`./${language}`, { [language]: catalogues[language] }])));
const organization = { id: 1, name: 'Test laboratory' };
const zone = (id, name, extra = {}) => ({ id, name, organization, is_active: true, ...extra });
const origin = zone(1, 'Origin zone'), destination = zone(2, 'Destination zone'), alternative = zone(3, 'Alternative zone');
const movement = (extra = {}) => ({
  id: 12, revision: 0, kind: 'ordinary', moved_at: '2026-07-06T10:12:13.123456Z',
  from_thermal_zone: origin, to_thermal_zone: destination, notes: 'Recorded comment', ...extra,
});
const box = (extra = {}) => ({
  id: 17, organization, status: 'active', thermal_zone: destination, movements: [movement()],
  locations: [{ id: 22, thermal_zone: destination, ends_at: null, end_date_unknown: false }], ...extra,
});
const failure = (code, status = 409) => new ApiError(status, 'Test refusal', { code });

// This deliberately models only hook slots, keyed remounts, JSX and layout effects.
// It tests component state/handlers, not browser layout, native focus or DOM semantics.
function bannerHarness(overrides = {}) {
  let slots = [], cleanups = [], effects = [], cursor = 0, dirty = false, nodes = [], tree, key, alive = true;
  const frames = [], calls = [], refreshes = [];
  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
      const ownedSlots = slots;
      return [slots[index], (next) => {
        ownedSlots[index] = typeof next === 'function' ? next(ownedSlots[index]) : next;
        if (ownedSlots === slots && alive) dirty = true;
      }];
    },
    useRef(initial) { const index = cursor++; return slots[index] ??= { current: initial }; },
    useLayoutEffect(effect, dependencies) {
      const index = cursor++;
      if (!slots[index] || dependencies.some((value, offset) => value !== slots[index][offset])) {
        slots[index] = dependencies;
        effects.push(() => { cleanups[index]?.(); cleanups[index] = effect(); });
      }
    },
  };
  const jsx = (type, props, key) => ({ type, props, key });
  const Metric = load('components/Metric.tsx', { 'react/jsx-runtime': { jsx, jsxs: jsx } });
  const Banner = load('components/LatestMovementBanner.tsx', {
    react: hooks, 'react/jsx-runtime': { jsx, jsxs: jsx }, 'lucide-react': { ArrowRight: 'arrow-icon', Pencil: 'pencil-icon' },
    '../utils/boxMovementCorrection': helpers, './Metric': Metric,
    './MeasurementSaveButton': { default: (props) => jsx('button', {
      className: 'measurement-save-button', type: props.isDesktop ? 'submit' : 'button',
      disabled: props.isDisabled || props.isSaving,
      onClick: () => props.onSave(), 'aria-label': props.isDesktop ? undefined : props.labels.hold,
      children: props.isSaving ? props.labels.saving : props.labels.save,
    }) },
  }, { requestAnimationFrame(callback) { frames.push(callback); } }).default;
  let props = {
    box: box(), zones: [origin, destination, alternative, zone(4, 'Inactive', { is_active: false }),
      zone(5, 'Other organization', { organization: { id: 2 } })],
    language: 'en', canEdit: true, isDesktop: true, isOperationCurrent: () => true,
    onCorrect: async (...args) => { calls.push(args); },
    onRefresh: async (id) => { refreshes.push(id); return props.box; }, ...overrides,
  };
  props.t = i18n.createTranslator(props.language);
  function render() {
    assert.ok(alive);
    const wrapper = Banner(props);
    if (key !== wrapper.key) {
      cleanups.forEach((cleanup) => cleanup?.());
      slots = []; cleanups = []; effects = []; key = wrapper.key;
    }
    cursor = 0; dirty = false;
    tree = wrapper.type(wrapper.props); nodes = [];
    function visit(node) {
      if (Array.isArray(node)) return node.forEach(visit);
      if (!node || typeof node !== 'object') return;
      if (typeof node.type === 'function') return visit(node.type(node.props));
      nodes.push(node);
      node.focus = () => { h.focused = node; };
      if (node.props.ref) node.props.ref.current = node;
      visit(node.props.children);
    }
    visit(tree);
    while (effects.length) effects.shift()();
  }
  const h = {
    calls, refreshes, get nodes() { return nodes; }, get props() { return props; }, get tree() { return tree; },
    t: (name) => props.t(name),
    find: (predicate) => nodes.find(predicate),
    field: (name) => nodes.find((node) => node.props.id?.endsWith(`-${name}`) && ['input', 'select', 'textarea'].includes(node.type)),
    button: (label) => nodes.find((node) => node.type === 'button' && (text(node) === props.t(label) || node.props['aria-label'] === props.t(label))),
    flush() { if (dirty) render(); while (frames.length) frames.shift()(); },
    async settle() { await tick(); this.flush(); },
    click(name) { const button = this.button(name); assert.ok(button, `Missing button ${name}`); button.props.onClick(); this.flush(); },
    change(name, value) { const field = this.field(name); assert.ok(field?.props.onChange); field.props.onChange({ target: { value } }); this.flush(); },
    submit() { this.find((node) => node.type === 'form').props.onSubmit({ preventDefault() {} }); this.flush(); },
    setProps(next) { props = { ...props, ...next }; props.t = i18n.createTranslator(props.language); render(); },
    unmount() { cleanups.forEach((cleanup) => cleanup?.()); alive = false; },
  };
  render();
  return h;
}

test('summary titles match the measurement family in French and English', () => {
  assert.equal(catalogues.fr.latestMovement, 'Dernier déplacement');
  assert.equal(catalogues.en.latestMovement, 'Last movement');
});

for (const language of ['fr', 'en']) {
  test(`banner ${language}: latest persisted summary, ordered information, empty and unavailable states, no modal`, () => {
    const older = movement({ id: 99, moved_at: '2026-07-01T10:00:00Z', notes: 'Old comment' });
    const movements = [older, movement()];
    const h = bannerHarness({ language, box: box({ movements }) });
    assert.equal(text(h.find((node) => node.type === 'h2')), h.t('latestMovement'));
    const summary = text(h.tree);
    assert.ok(summary.indexOf(h.t('movementOrigin')) < summary.indexOf(h.t('movementDestination')));
    assert.ok(summary.indexOf(h.t('movementDestination')) < summary.indexOf(h.t('movementNotes')));
    assert.ok(summary.includes('Recorded comment'));
    assert.ok(!summary.includes('Old comment'));
    assert.ok(!summary.includes(h.t('movementKindOrdinary')));
    assert.equal(h.find((node) => node.type === 'time').props.dateTime, movement().moved_at);
    assert.equal(h.find((node) => node.props.role === 'dialog' || node.type === 'portal'), undefined);
    assert.equal(movements[0], older, 'sorting must not mutate persisted history');
    h.setProps({ box: box({ movements: [] }) });
    assert.ok(text(h.tree).includes(h.t('latestMovementEmpty')));
    assert.equal(h.button('movementCorrect'), undefined);
    const item = box(); delete item.movements;
    h.setProps({ box: item });
    assert.ok(text(h.tree).includes(h.t('latestMovementUnavailable')));
  });

  for (const kind of ['initial_location', 'qualification', 'reactivation', 'unclassified']) {
    test(`banner ${language}: ${kind} is explicitly not ordinary and has a readonly date`, () => {
      const recorded = movement({ kind, from_thermal_zone: null, notes: '' });
      const h = bannerHarness({ language, box: box({ movements: [recorded] }) });
      assert.ok(text(h.tree).includes(h.t(helpers.movementKindKey(recorded))));
      assert.ok(text(h.tree).includes(h.t('movementNoOrigin')));
      assert.ok(text(h.tree).includes(h.t('movementNoNotes')));
      h.click('movementCorrect');
      assert.equal(h.field('date').props.readOnly, true);
      assert.equal(h.field('origin').props.readOnly, true);
      assert.equal(h.field('destination').type, kind === 'unclassified' ? 'input' : 'select');
      if (kind === 'unclassified') assert.equal(h.field('destination').props.readOnly, true);
    });
  }
}

test('pencil opens a prefilled inline editor with immutable origin and scoped active destinations; cancel discards changes', () => {
  const h = bannerHarness();
  h.click('movementCorrect');
  assert.equal(h.field('date').props.value, '2026-07-06T12:12:13');
  assert.equal(h.field('date').props.type, 'datetime-local');
  assert.equal(h.field('date').props.step, '1');
  assert.equal(h.field('origin').props.value, origin.name);
  assert.equal(h.field('origin').props.readOnly, true);
  assert.equal(h.field('origin').props.onChange, undefined);
  assert.equal(h.field('destination').props.value, destination.id);
  assert.equal(h.field('notes').props.value, 'Recorded comment');
  assert.deepEqual(h.nodes.filter((node) => node.type === 'option').map((node) => node.props.value), [2, 3]);
  h.change('notes', 'Unsaved'); h.click('cancel');
  assert.equal(h.find((node) => node.type === 'form'), undefined);
  assert.equal(h.calls.length, 0);
  assert.equal(h.focused, h.button('movementCorrect'));
  h.click('movementCorrect');
  assert.equal(h.field('notes').props.value, 'Recorded comment');
});

for (const language of ['fr', 'en']) {
  test(`editor ${language}: accessible date without visible label/help and normalized dirty actions`, () => {
    const h = bannerHarness({ language }); h.click('movementCorrect');
    assert.equal(h.field('date').props['aria-label'], h.t('movementDate'));
    assert.equal(h.field('date').props['aria-describedby'], undefined);
    assert.ok(!text(h.tree).includes(h.t('movementDate')));
    assert.ok(!text(h.tree).includes(h.t('movementTimeHelp')));
    assert.equal(h.button('movementSave'), undefined);
    assert.ok(h.button('cancel'), 'clean draft can still be closed');
    h.change('notes', '  Recorded comment  ');
    assert.equal(h.button('movementSave'), undefined, 'compare trimmed API content');
    for (const [field, changed, original] of [
      ['notes', 'Correction', 'Recorded comment'],
      ['destination', 3, 2],
      ['date', '2026-07-06T12:13:13', '2026-07-06T12:12:13'],
    ]) {
      h.change(field, changed);
      assert.ok(h.button('movementSave'));
      const row = h.find((node) => node.props.className === 'measurement-actions-row');
      assert.equal(text(row.props.children[0]), h.t('movementSave'));
      assert.equal(text(row.props.children[1]), h.t('cancel'));
      h.change(field, original);
      assert.equal(h.button('movementSave'), undefined, 'reset hides save again');
    }
    h.click('cancel'); h.click('movementCorrect');
    assert.equal(h.button('movementSave'), undefined, 'reopen starts clean');
    assert.equal(h.calls.length, 0);
  });
}

test('tablet uses the shared hold-save control and ignores direct form submission', async () => {
  const h = bannerHarness({ isDesktop: false }); h.click('movementCorrect');
  h.change('notes', 'Tablet correction');
  const save = h.button('movementSave');
  assert.equal(save.props.type, 'button');
  assert.equal(save.props['aria-label'], h.t('holdToUpdate'));
  h.submit(); assert.equal(h.calls.length, 0);
  h.click('movementSave'); await h.settle();
  assert.equal(h.calls.length, 1);
  assert.equal(h.find((node) => node.type === 'form'), undefined);
});

test('notes-only save preserves fractional timestamp/revision zero, locks pending actions, refreshes before closing', async () => {
  const save = deferred(), refresh = deferred(), calls = [], refreshes = [];
  const h = bannerHarness({
    onCorrect(...args) { calls.push(args); return save.promise; },
    onRefresh(id) { refreshes.push(id); return refresh.promise; },
  });
  h.click('movementCorrect'); h.change('notes', '  Corrected comment  '); h.submit();
  assert.deepEqual(plain(calls), [[17, 12, { expected_revision: 0, notes: 'Corrected comment' }]]);
  assert.equal(h.find((node) => node.type === 'fieldset').props.disabled, true);
  assert.equal(h.button('cancel').props.disabled, true);
  assert.equal(h.tree.props['aria-busy'], true);
  h.submit(); h.click('cancel');
  assert.equal(calls.length, 1);
  assert.ok(h.find((node) => node.type === 'form'));
  save.resolve(); await h.settle();
  assert.deepEqual(refreshes, [17]);
  assert.ok(text(h.tree).includes(h.t('movementSaved')));
  assert.ok(h.find((node) => node.type === 'form'), 'wait for authoritative refresh');
  refresh.resolve(box({ movements: [movement({ revision: 1, notes: 'Corrected comment' })] }));
  await h.settle();
  assert.equal(h.find((node) => node.type === 'form'), undefined);
  assert.ok(text(h.tree).includes('Corrected comment'));
  assert.equal(h.focused, h.button('movementCorrect'));
});

test('no-change submit and invalid Paris DST input are rejected locally without a save', () => {
  const h = bannerHarness(); h.click('movementCorrect'); h.submit();
  assert.equal(h.button('movementSave'), undefined);
  assert.ok(!text(h.tree).includes(h.t('movementErrorNoChange')));
  h.change('date', '2026-03-29T02:30:00'); h.submit();
  assert.ok(text(h.tree).includes(h.t('movementTimeGap')));
  h.change('date', '2026-10-25T02:30:00'); h.submit();
  assert.ok(text(h.tree).includes(h.t('movementTimeAmbiguous')));
  assert.equal(h.calls.length, 0);
});

test('stale revision refresh freezes the submitted draft until review/reopen; never silently rebases or retries', async () => {
  const save = deferred(), refresh = deferred(), calls = [];
  const h = bannerHarness({ onCorrect(...args) { calls.push(args); return save.promise; }, onRefresh: () => refresh.promise });
  h.click('movementCorrect'); h.change('notes', 'My draft'); h.submit();
  save.reject(failure('box_movement_changed')); await h.settle();
  assert.ok(text(h.tree).includes(h.t('movementErrorChanged')));
  refresh.resolve(box({ movements: [movement({ revision: 8, notes: 'Other editor' })] })); await h.settle();
  assert.equal(h.field('notes').props.value, 'My draft');
  assert.equal(h.find((node) => node.type === 'fieldset').props.disabled, true);
  assert.ok(text(h.tree).includes(h.t('movementReviewReopen')));
  h.submit(); assert.equal(calls.length, 1);
  assert.equal(calls[0][2].expected_revision, 0);
  h.click('movementReview'); assert.ok(text(h.tree).includes('Other editor'));
  h.setProps({ onCorrect: async (...args) => { calls.push(args); } });
  h.click('movementCorrect'); h.change('notes', 'Reviewed draft'); h.submit(); await h.settle();
  assert.equal(calls[1][2].expected_revision, 8);
});

test('week conflict keeps the editable draft and reports the translated server refusal without refresh', async () => {
  const h = bannerHarness({ onCorrect: async () => { throw failure('box_movement_week_conflict'); } });
  h.click('movementCorrect'); h.change('notes', 'Keep this'); h.submit(); await h.settle();
  assert.ok(text(h.tree).includes(h.t('movementErrorWeekConflict')));
  assert.equal(h.field('notes').props.value, 'Keep this');
  assert.equal(h.find((node) => node.type === 'fieldset').props.disabled, false);
  assert.equal(h.refreshes.length, 0);
  assert.ok(!text(h.tree).includes(h.t('movementSaved')));
});

test('saved correction with failed refresh stays locked; retry refresh closes without submitting twice', async () => {
  let attempts = 0;
  const h = bannerHarness({ onRefresh: async () => {
    if (++attempts === 1) throw new Error('Offline');
    return box({ movements: [movement({ revision: 1, notes: 'Saved notes' })] });
  } });
  h.click('movementCorrect'); h.change('notes', 'Saved notes'); h.submit(); await h.settle();
  assert.ok(text(h.tree).includes(h.t('movementSaved')));
  assert.ok(text(h.tree).includes(h.t('movementErrorRefresh')));
  assert.equal(h.find((node) => node.type === 'fieldset').props.disabled, true);
  h.submit(); assert.equal(h.calls.length, 1);
  h.click('movementRefresh'); await h.settle();
  assert.equal(attempts, 2);
  assert.equal(h.calls.length, 1);
  assert.equal(h.find((node) => node.type === 'form'), undefined);
});

for (const invalidation of ['organization', 'box', 'guard', 'unmount']) {
  test(`pending save ignores continuations after ${invalidation} invalidation`, async () => {
    const save = deferred();
    const h = bannerHarness({ onCorrect: () => save.promise });
    h.click('movementCorrect'); h.change('notes', 'Old scope'); h.submit();
    if (invalidation === 'organization') h.setProps({ box: box({ organization: { id: 2 } }) });
    if (invalidation === 'box') h.setProps({ box: box({ id: 18 }) });
    if (invalidation === 'guard') h.setProps({ isOperationCurrent: () => false });
    if (invalidation === 'unmount') h.unmount();
    save.resolve(); await tick();
    if (invalidation !== 'unmount') h.flush();
    assert.equal(h.refreshes.length, 0);
    assert.ok(!text(h.tree).includes(h.t('movementSaved')));
    if (['box', 'organization'].includes(invalidation)) assert.equal(h.find((node) => node.type === 'form'), undefined);
  });
}

test('new prop revision locks an existing draft and readOnly removes editing capability', () => {
  const h = bannerHarness(); h.click('movementCorrect'); h.change('notes', 'Frozen');
  h.setProps({ box: box({ movements: [movement({ revision: 1 })] }) });
  assert.equal(h.field('notes').props.value, 'Frozen');
  assert.equal(h.find((node) => node.type === 'fieldset').props.disabled, true);
  h.click('movementReview'); h.setProps({ canEdit: false });
  assert.equal(h.button('movementCorrect'), undefined);
  const missingRevision = bannerHarness({ box: box({ movements: [movement({ revision: undefined })] }) });
  assert.equal(missingRevision.button('movementCorrect'), undefined);
});

test('Paris helper handles winter/summer, DST gaps/folds, invalid dates and unchanged precision', () => {
  const original = movement().moved_at;
  assert.equal(helpers.toParisMovementInput(original), '2026-07-06T12:12:13');
  assert.equal(helpers.toParisMovementInput('2026-01-06T10:12:13Z'), '2026-01-06T11:12:13');
  assert.equal(helpers.resolveParisMovementTime(helpers.toParisMovementInput(original), original).timestamp, original);
  const fold = '2026-10-25T02:30:00.654321+02:00';
  assert.equal(helpers.resolveParisMovementTime(helpers.toParisMovementInput(fold), fold).timestamp, fold);
  assert.equal(helpers.resolveParisMovementTime('2026-01-06T11:00', original).timestamp, '2026-01-06T10:00:00.000Z');
  assert.equal(helpers.resolveParisMovementTime('2026-07-06T12:00:00', original).timestamp, '2026-07-06T10:00:00.000Z');
  for (const [input, error] of [['2026-03-29T02:30:00', 'movementTimeGap'], ['2026-10-25T02:30:00', 'movementTimeAmbiguous'],
    ['2026-02-30T12:00:00', 'movementTimeInvalid'], ['', 'movementTimeInvalid'], ['2026-01-01T25:00:00', 'movementTimeInvalid']]) {
    assert.equal(helpers.resolveParisMovementTime(input, original).error, error);
  }
  assert.equal(helpers.toParisMovementInput('2026-01-01'), null);
  assert.equal(helpers.toParisMovementInput('invalid'), null);
  for (const language of ['fr', 'en']) {
    assert.ok(helpers.formatMovementTimestamp(original, language).includes('12:12'));
    assert.ok(!helpers.formatMovementTimestamp('2026-01-01', language).includes('01:00'));
    assert.equal(helpers.formatMovementTimestamp('unknown', language), 'unknown');
  }
});

test('normalized zero seconds retain the exact original fractional timestamp and notes-only payload', async () => {
  for (const original of ['2026-07-06T10:12:00.123456Z', '2026-01-06T11:12:00.654321+01:00',
    '2026-10-25T02:30:00.123456+02:00']) {
    const input = helpers.toParisMovementInput(original);
    assert.ok(input.endsWith(':00'));
    const normalizedByControl = input.slice(0, -3);
    assert.equal(helpers.resolveParisMovementTime(normalizedByControl, original).timestamp, original,
      'a datetime-local control omitting zero seconds must not drop fractional precision or reinterpret a DST fold');
    const h = bannerHarness({ box: box({ movements: [movement({ moved_at: original })] }) });
    h.click('movementCorrect');
    h.change('date', normalizedByControl);
    h.change('notes', 'Notes correction only');
    h.submit(); await h.settle();
    assert.deepEqual(plain(h.calls), [[17, 12, { expected_revision: 0, notes: 'Notes correction only' }]]);
    assert.ok(!text(h.tree).includes(h.t('movementTimeAmbiguous')));
  }
});

test('capabilities separate ordinary, lifecycle and unclassified data and conservatively restrict destination', () => {
  const capabilities = (detail, recorded = detail.movements[0]) => plain(helpers.movementCorrectionCapabilities(detail, recorded));
  assert.deepEqual(capabilities(box()), { notes: true, date: true, destination: true });
  for (const kind of ['initial_location', 'qualification', 'reactivation']) {
    assert.deepEqual(capabilities(box({ movements: [movement({ kind })] })), { notes: true, date: false, destination: true });
  }
  assert.deepEqual(capabilities(box({ movements: [movement({ kind: 'unclassified' })] })), { notes: true, date: false, destination: false });
  for (const revision of [undefined, null, -1, 0.5]) {
    assert.deepEqual(capabilities(box({ movements: [movement({ revision })] })), { notes: false, date: false, destination: false });
  }
  for (const extra of [{ status: 'inactive' }, { thermal_zone: null }, { locations: [] },
    { locations: [box().locations[0], { ...box().locations[0], id: 23 }] },
    { locations: [{ ...box().locations[0], end_date_unknown: true }] },
    { movements: [movement(), movement({ id: 13 })] }]) assert.equal(capabilities(box(extra)).destination, false);
  assert.equal(capabilities(box({ movements: [movement({ moved_at: '2026-07-06' })] })).date, false);
  const tied = box({ movements: [movement(), movement({ id: 13 })] });
  assert.equal(helpers.latestPersistedMovement(tied).id, 13);
  assert.equal(helpers.movementKindKey(movement({ kind: 'future_kind' })), 'movementKindUnclassified');
});

test('error helper maps codes/statuses and does not interpret arbitrary objects as trusted API errors', () => {
  for (const [code, key] of [['box_movement_changed', 'movementErrorChanged'], ['box_movement_week_conflict', 'movementErrorWeekConflict'],
    ['movement_provenance_unresolved', 'movementErrorProvenance'], ['movement_period_link_missing', 'movementErrorMissingLink'],
    ['movement_not_terminal', 'movementErrorNotTerminal']]) assert.equal(helpers.movementCorrectionErrorKey(failure(code)), key);
  for (const [status, key] of [[400, 'movementErrorValidation'], [401, 'movementErrorForbidden'], [403, 'movementErrorForbidden'],
    [404, 'movementErrorNotFound'], [409, 'movementErrorConflict'], [500, 'movementErrorSave']]) {
    assert.equal(helpers.movementCorrectionErrorKey(failure('unknown', status)), key);
  }
  assert.equal(helpers.movementCorrectionErrorKey({ data: { code: 'box_movement_changed' } }), 'movementErrorSave');
});

function movementAppHarness() {
  const h = appHarness();
  for (const name of ['correctMovement', 'refreshMovementState']) h.evaluate(functionNode(name).getText(ast));
  return h;
}

test('App correctMovement PATCH uses the revision payload and propagates server failures', async () => {
  const h = movementAppHarness(), payload = { expected_revision: 0, notes: 'Corrected' };
  const pending = h.context.correctMovement(17, 12, payload);
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].method, 'apiPatch');
  assert.equal(h.requests[0].url, '/api/boxes/17/movements/12/');
  assert.equal(h.requests[0].payload, payload);
  const saved = box();
  saved.movements[0] = { ...saved.movements[0], revision: 1, notes: 'Corrected' };
  h.requests[0].resolve(saved); await pending;
  assert.equal(h.state.data.boxDetails[17], saved, 'retain the committed correction before the refresh');
  assert.equal(h.requests.length, 1, 'component owns refresh, not the PATCH handler');
  const error = failure('box_movement_week_conflict');
  const refused = h.context.correctMovement(17, 12, payload);
  h.requests[1].reject(error);
  await assert.rejects(refused, (received) => received === error);
});

test('App refresh fetches box and all zone pages, merges detail and invalidates dependent summaries', async () => {
  const h = movementAppHarness();
  h.state.data.boxes = [{ id: 99 }];
  const pending = h.context.refreshMovementState(17);
  // Observe rejection immediately so a pagination regression is a test failure,
  // not an unhandled rejection after the test has already finished.
  const outcome = pending.then((detail) => ({ detail }), (error) => ({ error }));
  assert.deepEqual(h.requests.map((request) => [request.method, request.url]), [
    ['apiGet', '/api/boxes/17/'], ['apiGet', '/api/thermal-zones/'],
  ]);
  const detail = box(); h.requests[0].resolve(detail);
  h.requests[1].resolve({ results: [destination], next: '/api/thermal-zones/?page=2' });
  await tick();
  if (!h.requests[2]) {
    const result = await outcome;
    assert.fail(`Zone pagination stopped before page 2: ${result.error?.stack ?? 'no next request'}`);
  }
  assert.equal(h.requests[2].url, '/api/thermal-zones/?page=2');
  h.requests[2].resolve({ results: [alternative], next: null });
  assert.equal(await pending, detail);
  assert.equal(h.state.data.boxDetails[17], detail);
  assert.deepEqual(h.state.data.boxes.map((item) => item.id), [99, 17]);
  assert.deepEqual(plain(h.state.data.zones), [destination, alternative]);
  assert.equal(h.state.data.overview, null);
  assert.equal(h.state.data.exportOptions, null);
});

for (const operation of ['correctMovement', 'refreshMovementState']) {
  test(`App ${operation} rejects stale organization requests without state updates`, async () => {
    const h = movementAppHarness();
    const before = h.state.data;
    const pending = operation === 'correctMovement' ? h.context.correctMovement(17, 12, { expected_revision: 0, notes: 'Old' })
      : h.context.refreshMovementState(17);
    h.switchOrganization();
    h.requests.forEach((request) => request.resolve(request.url.includes('thermal-zones') ? { results: [], next: null } : box()));
    await assert.rejects(pending, (error) => error instanceof h.context.ApiResourceCancelledError);
    assert.equal(h.state.data, before);
  });
}

test('CSS source: movement has a named full-width area before insights in every box grid template', () => {
  const css = read('styles/pages/box-detail.css');
  const area = css.match(/\.box-page-grid\s*>\s*\.measurement-module:is\(\.latest-movement-banner,\s*\.latest-movement-editor\)\s*\{([^}]+)\}/)?.[1];
  assert.ok(area);
  assert.match(area, /grid-area:\s*movement\s*;/);
  assert.match(area, /grid-column:\s*1\s*\/\s*-1\s*;/);
  const templates = [...css.matchAll(/([^{}]+)\{([^{}]*\bgrid-template\s*:[^{}]*)\}/g)]
    .filter((match) => match[1].includes('.box-page-grid'));
  assert.ok(templates.length >= 7, 'cover desktop, readonly, inactive, measurement module, tablet and phone grids');
  for (const [, selector, declarations] of templates) {
    const template = declarations.match(/\bgrid-template\s*:([^;]+);/)[1];
    const rows = [...template.matchAll(/"([^"]+)"/g)].map((match) => match[1].trim().split(/\s+/));
    const movementRow = rows.findIndex((row) => row.includes('movement'));
    const insightsRow = rows.findIndex((row) => row.includes('insights'));
    assert.ok(movementRow >= 0 && insightsRow > movementRow, `${selector.trim()}: movement must precede insights`);
    assert.ok(rows[movementRow].every((cell) => cell === 'movement'), `${selector.trim()}: movement must span the row`);
  }
});

test('CSS source: tablet and phone order is last reading, movement, then measurement form', () => {
  const css = read('styles/pages/box-detail.css');
  for (const [name, media] of [['tablet', /@media\s*\(min-width:\s*760px\)[^{]*\{/],
    ['phone', /@media\s*\(max-width:\s*759px\)[^{]*\{/]]) {
    const start = css.search(media);
    assert.ok(start >= 0, `Missing ${name} media query`);
    const nextMedia = css.indexOf('@media', start + 6);
    const section = css.slice(start, nextMedia < 0 ? undefined : nextMedia);
    const template = section.match(/\.box-page-grid\s*\{[^}]*\bgrid-template\s*:([^;]+);/)?.[1];
    assert.ok(template, `Missing ${name} base box grid template`);
    assert.deepEqual([...template.matchAll(/"([^"]+)"/g)].map((match) => match[1]),
      ['last', 'movement', 'form', 'insights'], `${name} reading/movement/form order`);
  }
});

test('long zone names remain complete without decorative disclosures, including read-only mode', () => {
  const originName = 'Origin laboratory with a very long zone name '.repeat(30);
  const destinationName = 'Destination laboratory with a very long zone name '.repeat(30);
  for (const canEdit of [true, false]) {
    const h = bannerHarness({ canEdit, box: box({ movements: [movement({
      from_thermal_zone: zone(1, originName), to_thermal_zone: zone(2, destinationName),
    })] }) });
    assert.equal(h.nodes.filter((node) => node.type === 'details').length, 0);
    assert.equal(h.nodes.filter((node) => node.type === 'chevron-icon').length, 0);
    const route = h.find((node) => node.props.className === 'movement-route');
    assert.ok(route);
    assert.deepEqual(h.nodes.filter((node) => node.props.className === 'metric movement-zone')
      .map((node) => text(node.props.children[1])), [originName, destinationName]);
    const arrow = h.find((node) => node.type === 'arrow-icon');
    assert.equal(arrow.props['aria-hidden'], 'true');
    assert.equal(route.props.children[1], arrow, 'one explicit arrow between origin and destination');
    const comment = h.find((node) => node.props.className === 'last-reading-comment');
    assert.equal(comment.props.children[0].props.className, 'last-reading-comment-header');
    assert.equal(h.calls.length, 0, 'disclosure rendering does not mutate business data');
  }
});

test('movement route inherits measurement typography and wraps complete names without a height cap', () => {
  const css = read('styles/pages/box-detail.css');
  assert.doesNotMatch(css, /\.latest-movement-banner \.metric strong\s*\{[^}]*font-size/);
  assert.match(css, /\.movement-zone strong\s*\{[^}]*overflow-wrap:\s*anywhere;/);
  assert.match(css, /\.movement-route\s*\{[^}]*grid-column:\s*span 2;[^}]*grid-template-columns:\s*minmax\(0, 1fr\) auto minmax\(0, 1fr\)/);
  assert.match(css, /@media \(min-width: 1181px\)\s*\{\s*\.box-page \.last-reading-card\.measurement-summary\.latest-movement-banner:not\(\.is-empty\)\s*\{[^}]*minmax\(0, 1\.8fr\)/);
  assert.match(css, /@media \(max-width: 1180px\)\s*\{\s*\.box-page \.last-reading-card\.measurement-summary\.latest-movement-banner:not\(\.is-empty\)\s*\{[^}]*repeat\(2, minmax\(0, 1fr\)\)/);
  const disclosureRules = [...css.matchAll(/([^{}]*movement-zone[^{}]*)\{([^{}]*)\}/g)];
  assert.ok(disclosureRules.length > 0);
  for (const [, , declarations] of disclosureRules) assert.doesNotMatch(declarations, /(?:max-)?height\s*:/);
});

test('CSS source: movement notes override summary clipping with block display and unrestricted lines', () => {
  const css = read('styles/pages/box-detail.css');
  const selector = '.latest-movement-banner .last-reading-comment p';
  const start = css.indexOf(selector);
  assert.ok(start > css.indexOf('.last-reading-comment p'), 'movement override follows the shared clipped-note rule');
  const declarations = css.slice(start).match(/^[^{]+\{([^}]+)\}/)?.[1];
  assert.ok(declarations);
  assert.match(declarations, /display:\s*block\s*;/);
  assert.match(declarations, /overflow:\s*visible\s*;/);
  assert.match(declarations, /-webkit-line-clamp:\s*(?:unset|none)\s*;/);
  const notes = 'A long recorded movement comment with details that must remain available. '.repeat(20);
  const h = bannerHarness({ box: box({ movements: [movement({ notes })] }) });
  const comment = h.find((node) => node.props.className === 'last-reading-comment');
  assert.equal(text(comment.props.children[1]), notes, 'render the complete comment, not a truncated string');
});

test('source contracts: banner wiring stays independent of measurement editor and responsive CSS permits narrow fields/actions', () => {
  assert.match(appSource, /onCorrectMovement=\{correctMovement\}/);
  assert.match(appSource, /onRefreshMovementState=\{refreshMovementState\}/);
  assert.doesNotMatch(appSource, /<LatestMovementBanner\b/);
  assert.match(boxSource, /<LatestMovementBanner[\s\S]*?canEdit=\{canWriteLabData\}[\s\S]*?onCorrect=\{onCorrectMovement\}[\s\S]*?onRefresh=\{onRefreshMovementState\}/);
  const css = read('styles/pages/box-detail.css');
  assert.match(css, /\.box-page-grid\s*>\s*\.measurement-module:is\(\.latest-movement-banner,\s*\.latest-movement-editor\)\s*\{[^}]*grid-column:\s*1\s*\/\s*-1/);
  for (const selector of ['movement-correction-fields']) {
    const block = css.match(new RegExp(`\\.${selector}\\s*\\{([^}]+)\\}`))?.[1];
    assert.ok(block, `Missing ${selector}`);
    assert.match(block, /repeat\(auto-fit,\s*minmax\(min\(100%,\s*\d+px\),\s*1fr\)\)/);
  }
  assert.match(read('components/LatestMovementBanner.tsx'), /className="measurement-actions-row"/);
  assert.match(css, /\.measurement-cancel-button\s*\{[^}]*min-height:\s*44px/);
  assert.match(css, /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{\s*\.latest-movement-editor[^}]*animation:\s*none/);
});
